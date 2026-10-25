#!/usr/bin/env python3
"""Tests of wt_server_ext (no vLLM needed): the stock view's 10 s windows, the share view's ranking
(first come, first served by turn start; holders stay mid-turn; a harness-reported turn start wins;
an unknown caller queues behind every mid-turn session), staleness, and the ASGI routing.

    python research/18_harness_wait/test_wt_server_ext.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wt_server_ext as ext  # noqa: E402


class Sample:
    def __init__(self, name, value, labels=None):
        self.name, self.value, self.labels = name, value, labels or {}


class Family:
    def __init__(self, samples):
        self.samples = samples


class FakeRegistry:
    def __init__(self, values):
        self.values = values

    def collect(self):
        v = self.values
        return [Family([Sample("vllm:num_requests_running", v["running"], {"engine": "0"})]),
                Family([Sample("vllm:num_requests_waiting", v["waiting"])]),
                Family([Sample("vllm:num_requests_waiting_by_reason", v["cap"], {"reason": "capacity"}),
                        Sample("vllm:num_requests_waiting_by_reason", v["waiting"] - v["cap"], {"reason": "deferred"})]),
                Family([Sample("vllm:kv_cache_usage_perc", v["kv"])]),
                Family([Sample("vllm:prefix_cache_queries_total", v["q"]), Sample("vllm:prefix_cache_queries_created", 1.0)]),
                Family([Sample("vllm:prefix_cache_hits_total", v["h"])]),
                Family([Sample("vllm:num_preemptions_total", v["pre"])])]


def snapshot(sessions: dict, running=3, waiting=2, wall=None) -> dict:
    return {"wall": time.time() if wall is None else wall, "t": 0.0, "seq": 1, "step": 10,
            "pool": {"blocks": 1000, "bs": 16, "tokens": 16000, "free": 100, "usage": 0.9},
            "running": running, "waiting": waiting, "deferred": 0, "cum": {}, "sessions": sessions}


def sess(turn_t0, first, ws, mid=True, prefix=0, res=0):
    return {"first": first, "turn_t0": turn_t0, "inflight": int(mid), "queued": False, "idle_s": 0.0, "mid": mid,
            "np": ws, "out": 0, "ws": ws, "res": res, "prefix": prefix}


def make_status(sessions, **kw):
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps(snapshot(sessions, **kw)))
    values = {"running": 3, "waiting": 2, "cap": 1, "kv": 0.9, "q": 1000.0, "h": 100.0, "pre": 0.0}
    sampler = ext.Sampler(read=lambda: ext.read_registry(FakeRegistry(values)), start=False)
    sampler.sample()                                       # stamped with the real monotonic clock
    return ext.Status(sampler, ext.Snapshot(str(path))), sampler, values, path


def test_read_registry_sums_stock_fields():
    out = ext.read_registry(FakeRegistry({"running": 3, "waiting": 5, "cap": 4, "kv": 0.97, "q": 10.0, "h": 4.0,
                                          "pre": 2.0}))
    assert out == {"running": 3, "waiting": 5, "waiting_capacity": 4, "waiting_deferred": 1, "kv_usage": 0.97,
                   "prefix_q": 10.0, "prefix_h": 4.0, "preempt": 2.0}, out


def test_stock_window_is_ten_seconds():
    values = {"running": 3, "waiting": 2, "cap": 1, "kv": 0.5, "q": 1000.0, "h": 900.0, "pre": 1.0}
    sampler = ext.Sampler(read=lambda: ext.read_registry(FakeRegistry(dict(values))), start=False)
    for t in range(0, 31):                    # one sample per second for 30 s
        values["q"] += 100.0
        values["h"] += 10.0 if t >= 20 else 90.0
        values["pre"] += 1.0 if t >= 25 else 0.0
        sampler.sample(now=float(t))
    out = sampler.stock(now=30.2)
    assert out["window_s"] == 10.0, out
    assert abs(out["hit_rate_10s"] - 0.1) < 1e-9, out          # the last 10 s: 10 of 100 tokens per second
    assert out["preemptions_10s"] == 6 and out["age_ms"] == 200.0, out
    assert out["waiting_capacity"] == 1 and out["kv_usage"] == 0.5


def test_share_ranks_first_come_first_served_by_turn():
    status, *_ = make_status({"A": sess(10.0, 1.0, 5000), "B": sess(20.0, 2.0, 7000),
                              "C": sess(15.0, 3.0, 3000), "D": sess(5.0, 0.5, 9000, mid=False)})
    out = status.build("share", "B", "line", None)
    me = out["me"]
    assert me["rank"] == 3 and me["ahead_n"] == 2 and me["ahead_ws"] == 8000, me    # A (turn 10), C (15)
    assert me["ws"] == 7000 and me["mid"] is True and me["known"] is True
    assert out["mid_sessions"] == 3 and out["pool_tokens"] == 16000
    first = status.build("share", "A", "line", None)["me"]
    assert first["rank"] == 1 and first["ahead_ws"] == 0


def test_holders_stay_mid_and_reported_turn_wins():
    status, *_ = make_status({"A": sess(10.0, 1.0, 5000), "H": sess(12.0, 2.0, 6000, mid=False),
                              "B": sess(20.0, 3.0, 7000)})
    out = status.build("share", "B", "line", None)
    assert out["me"]["ahead_ws"] == 5000, out["me"]                   # H is idle, not in line
    status.build("share", "H", "hold", 11.0)                          # H holds, reports its turn at 11.0
    out = status.build("share", "B", "line", None)
    assert out["me"]["ahead_ws"] == 11000 and out["holders"] == 1, out
    assert status.build("share", "H", "hold", None)["me"]["rank"] == 2      # turn 11 sorts after A's 10
    status.callers["H"]["t"] -= ext.HOLD_TTL_S + 1                    # the holder went silent
    assert status.build("share", "B", "line", None)["me"]["ahead_ws"] == 5000


def test_unknown_caller_queues_behind_every_mid_session():
    status, *_ = make_status({"A": sess(10.0, 1.0, 5000), "B": sess(20.0, 2.0, 7000)})
    me = status.build("share", "NEW", "line", None)["me"]
    assert me["known"] is False and me["ws"] == 0 and me["ahead_ws"] == 12000 and me["rank"] == 3, me
    assert status.build("stock", "NEW", "line", None).get("me") is None


def test_staleness_rules():
    status, sampler, _, path = make_status({"A": sess(10.0, 1.0, 5000)}, wall=time.time() - 30)
    out = status.build("share", "A", "line", None)
    assert out["age_ms"] >= 29000, out                                  # busy engine, old snapshot: stale
    path.write_text(json.dumps(snapshot({"A": sess(10.0, 1.0, 5000, mid=False)}, running=0, waiting=0,
                                        wall=time.time() - 30)))
    out = status.build("share", "A", "line", None)
    assert out["age_ms"] < 1000 and out["snapshot_age_ms"] >= 29000, out   # drained engine: still valid
    empty = ext.Status(ext.Sampler(read=lambda: {}, start=False), ext.Snapshot(None))
    out = empty.build("share", "A", "line", None)
    assert out["age_ms"] >= 1e9 and "error" in out


def test_asgi_routes_only_its_path():
    status, *_ = make_status({"A": sess(10.0, 1.0, 5000)})
    passed = []

    async def app(scope, receive, send):
        passed.append(scope["path"])

    mw = ext.Middleware.__new__(ext.Middleware)
    mw.app, mw.status = app, status
    sent = []

    async def send(message):
        sent.append(message)

    async def run():
        await mw({"type": "http", "path": "/v1/messages", "query_string": b""}, None, send)
        await mw({"type": "http", "path": "/wt/status", "query_string": b"view=share&sid=A&state=line"}, None, send)
        await mw({"type": "http", "path": "/wt/status", "query_string": b"view=bogus"}, None, send)

    asyncio.run(run())
    assert passed == ["/v1/messages"]
    assert sent[0]["status"] == 200 and json.loads(sent[1]["body"])["me"]["rank"] == 1
    assert sent[2]["status"] == 400


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
