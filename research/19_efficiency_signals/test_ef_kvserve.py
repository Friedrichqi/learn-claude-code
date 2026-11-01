#!/usr/bin/env python3
"""CPU tests of ef_kvserve.py (the KV-threshold probe's stage of its own): the runner points at topic 19's log and cap,
the stage's server check, and the client path into the frozen ef_kvwait.run. Runs as a script with the vLLM venv:

    /data/fritz/venv/bin/python research/19_efficiency_signals/test_ef_kvserve.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_kvserve as ks  # noqa: E402
import ef_kvwait as ek  # noqa: E402
import rd_serve as rs_  # noqa: E402
from test_ef_kvwait import Fake  # noqa: E402


def test_configure_points_the_runner_at_topic_19():
    ks.configure()
    assert rs_.HOURS == ks.KV / "gpu_hours.jsonl" and rs_.SERVERS == ks.KV / "servers"
    assert rs_.CAP_GPU_H == 11.48


def test_check_accepts_only_this_stage_serving_minimax():
    from aiohttp import web

    async def models(request):
        return web.json_response({"data": [{"id": request.app["model"]}]})
    fake = Fake([("GET", "/v1/models", models)])
    fake.app["model"] = ek.cf.MODEL
    saved = ek.T21_SERVERS
    try:
        ek.T21_SERVERS = Path(tempfile.mkdtemp(prefix="kvserve_check_"))
        assert "no live kvwait stage" in ks.check_server(fake.url, ek)
        info = ek.T21_SERVERS / "kvwait_20261030T210000Z" / "server_info.json"
        info.parent.mkdir()
        for stage, config, ok in (("s2a_probe", "profile", False), ("kvwait", "load16", False),
                                  ("kvwait", "profile", True)):
            info.write_text(json.dumps({"stage": stage, "config": config, "port": fake.port}))
            assert (ks.check_server(fake.url, ek) is None) == ok, (stage, config, ks.check_server(fake.url, ek))
        fake.app["model"] = "Qwen/Qwen3-32B"
        assert "not MiniMaxAI" in ks.check_server(fake.url, ek)
        fake.app["model"] = ek.cf.MODEL
        info.write_text(json.dumps({"stage": "kvwait", "config": "profile", "port": fake.port, "t_stop": 1}))
        assert "no live kvwait stage" in ks.check_server(fake.url, ek)
    finally:
        ek.T21_SERVERS = saved


def test_client_runs_the_frozen_probe_with_8_in_flight():
    seen = {}
    saved = (ek.run, ek.INFLIGHT, ek.T21_SERVERS, ks.check_server)
    try:
        ek.run = lambda url, any_server=False: seen.update(url=url, any_server=any_server, inflight=ek.INFLIGHT) or 0
        ks.check_server = lambda url, mod, stage="kvwait": None
        assert ks.client("http://127.0.0.1:1") == 0
        assert seen == {"url": "http://127.0.0.1:1", "any_server": True, "inflight": 8}, seen
        assert ek.T21_SERVERS == ks.KV / "servers"
        seen.clear()
        ks.check_server = lambda url, mod, stage="kvwait": "no live kvwait stage listens on this port"
        assert ks.client("http://127.0.0.1:1") == 6 and not seen
    finally:
        ek.run, ek.INFLIGHT, ek.T21_SERVERS, ks.check_server = saved


def test_client_runs_the_revised_screen_on_its_own_stage():
    import ef_kvwait2 as e2
    seen = {}
    saved = (e2.run, ks.check_server)
    try:
        e2.run = lambda url: seen.update(url=url) or 0
        ks.check_server = lambda url, mod, stage="kvwait": seen.update(stage=stage) or None
        assert ks.client("http://127.0.0.1:2", "kvwait2") == 0
        assert seen == {"url": "http://127.0.0.1:2", "stage": "kvwait2"}, seen
    finally:
        e2.run, ks.check_server = saved


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}", flush=True)
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
    sys.exit(1 if failures else 0)
