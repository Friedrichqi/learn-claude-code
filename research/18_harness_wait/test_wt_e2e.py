#!/usr/bin/env python3
"""End-to-end CPU test of research/18_harness_wait's client side (no GPU, no model).

Real s15 sessions, run the way the experiment runs them (wt_sweep -> lanes -> mu_session -> profile_run ->
harness), against a fake Anthropic-compatible server that also serves /metrics and a scripted /wt/status.
One short window cell per arm (2 users, 40 s), all at once. Checks what only a real run shows:

  * none: no status request, no status line, no `wait` tool, no hold;
  * m_stock / m_share: the `wait` tool is advertised, every lead call ends with exactly one status line of
    the arm's view, the model's wait runs after its other tool and its result reaches the next call;
  * h_backoff / h_share: no tool and no line (the prompts look like none's), holds happen after tool rounds
    only (the trace has hold_check / hold_start / hold_end), with the arm's view and the session's own id;
  * every cell ends by its window (window.json, COMPLETE), its sessions end `ok` or `terminated`.

    python research/18_harness_wait/test_wt_e2e.py          (WT_SKIP_E2E=1 skips it)
"""
from __future__ import annotations

import json
import lzma
import os
import socket
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import wt_workloads as ww  # noqa: E402

MODEL = "Qwen/Qwen3-32B"
ARMS = ("none", "m_stock", "m_share", "h_backoff", "h_share")
CROWDED = {"running": 12, "waiting": 5, "waiting_capacity": 4, "kv_usage": 0.97, "hit_rate_10s": 0.04,
           "preemptions_10s": 0}
CALM = {"running": 3, "waiting": 0, "waiting_capacity": 0, "kv_usage": 0.40, "hit_rate_10s": 0.9,
        "preemptions_10s": 0}


class Fake(BaseHTTPRequestHandler):
    lock = threading.Lock()
    messages: list = []                       # one record per /v1/messages request
    status: list = []                         # one record per /wt/status request
    per_sid = defaultdict(int)

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            self._send(200, "")
        elif url.path == "/version":
            self._send(200, {"version": "fake"})
        elif url.path == "/v1/models":
            self._send(200, {"data": [{"id": MODEL}]})
        elif url.path == "/metrics":
            self._send(200, "vllm:generation_tokens_total 10\nvllm:num_requests_running 1\n"
                            "vllm:num_requests_waiting 0\n", "text/plain")
        elif url.path == "/wt/status":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            with Fake.lock:
                Fake.status.append(q)
                n = Fake.per_sid[(q.get("sid"), q.get("state"))]
                Fake.per_sid[(q.get("sid"), q.get("state"))] += 1
            crowded = q.get("state") == "check" or (q.get("state") == "hold" and n % 2 == 0)
            body = {"view": q.get("view"), "age_ms": 20.0, **(CROWDED if crowded else CALM)}
            if q.get("view") == "share":
                body.update(pool_tokens=100000, mid_sessions=3, holders=1,
                            me={"sid": q.get("sid"), "known": True, "ws": 20000, "prefix": 15000, "res": 20000,
                                "mid": True, "ahead_ws": 85000 if crowded else 10000, "ahead_n": 2, "rank": 3})
            self._send(200, body)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        rid = self.headers.get("X-Request-Id")
        tools = [t["name"] for t in body.get("tools") or []]
        last = body["messages"][-1]
        content = last.get("content")
        blocks_in = content if isinstance(content, list) else [{"type": "text", "text": content}]
        tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks_in)
        with Fake.lock:
            Fake.messages.append({"rid": rid, "sid": self.headers.get("X-Session-ID"), "tools": tools,
                                  "last": blocks_in, "n": len(body["messages"])})
            k = len(Fake.messages)
        if tool_result:
            out, stop = [{"type": "text", "text": "Done: listed the files."}], "end_turn"
        else:
            out = [{"type": "tool_use", "id": f"toolu_g{k:05d}", "name": "glob",
                    "input": {"pattern": "profiling_sandbox/*"}}]
            if "wait" in tools:                                  # the model asks to wait, before its glob
                out.insert(0, {"type": "tool_use", "id": f"toolu_w{k:05d}", "name": "wait",
                               "input": {"seconds": 1.5, "reason": "server crowded"}})
            stop = "tool_use"
        self._send(200, {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": MODEL,
                         "content": out, "stop_reason": stop, "stop_sequence": None,
                         "usage": {"input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0}})


def free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if "429" not in str(port) and "529" not in str(port):
            return port


def texts(blocks) -> list[str]:
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b["text"])
        elif isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), str):
            out.append(b["content"])
    return out


def test_e2e_window_cells_per_arm():
    if os.environ.get("WT_SKIP_E2E") == "1":
        print("  (skipped: WT_SKIP_E2E=1)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="wt_e2e_", dir=os.environ.get("WT_TMP")))
    conds = {f"t_{arm}": ww.cell("test", 2, arm, 2, 40, fillers=False) for arm in ARMS}
    (tmp / "conditions.json").write_text(json.dumps(conds))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "MU_EXTRA_CONDITIONS": str(tmp / "conditions.json")}
    try:
        procs = {}
        for cond in conds:
            cmd = [sys.executable, str(DIR / "wt_sweep.py"), "run", "--cond", cond, "--users", "2",
                   "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(tmp / "lanes" / cond),
                   "--sessions", str(ww.SESSIONS), "--cells-root", str(tmp / "cells"), "--think-scale", "0.02",
                   "--fillers", "off", "--stagger-s", "1"]
            procs[cond] = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        for cond, proc in procs.items():
            out, _ = proc.communicate(timeout=900)
            assert proc.returncode == 0, f"{cond}: {out[-3000:]}"
        for arm in ARMS:
            cond = f"t_{arm}"
            root = tmp / "cells" / cond
            assert (root / "COMPLETE").exists() and json.loads((root / "window.json").read_text())["arm"] == arm
            runs = sorted((root / "runs").glob(f"MU-{cond}-S*"))
            assert runs, cond
            sids = set()
            events = []
            for run in runs:
                meta = json.loads((run / f"{run.name}.meta.json").read_text())
                assert meta["status"] in ("ok", "terminated"), meta["status"]
                sids.add(f"{run.name}~a{meta['attempt']}")
                for trace in run.glob("run_*.jsonl.xz"):
                    if ".inputs." in trace.name or ".reads." in trace.name:
                        continue
                    with lzma.open(trace, "rt") as handle:
                        events += [json.loads(line) for line in handle]
            calls = [m for m in Fake.messages if m["sid"] in sids]
            queries = [q for q in Fake.status if q.get("sid") in sids]
            lines = [t for c in calls for t in texts(c["last"]) if t.startswith("[server]")]
            names = {e["event"] for e in events}
            assert calls, cond
            if arm == "none":
                assert not queries and not lines and all("wait" not in c["tools"] for c in calls), cond
                assert not names & {"hold_check", "hold_start", "wait_detail"}
            elif arm.startswith("m_"):
                view = arm[2:]
                prefix = "[server] running" if view == "stock" else "[server] KV pool"
                assert all("wait" in c["tools"] for c in calls), cond
                for c in calls:                         # exactly one line per lead call, of the arm's view
                    mine = [t for t in texts(c["last"]) if t.startswith("[server]")]
                    assert len(mine) == 1 and mine[0].startswith(prefix), (cond, mine)
                assert {q["view"] for q in queries} == {view} and {"line", "wait"} <= {q["state"] for q in queries}
                waited = [t for c in calls for t in texts(c["last"]) if t.startswith("Waited ")]
                assert waited, f"{cond}: no wait result reached the model"
                assert "wait_detail" in names and not names & {"hold_check", "hold_start"}
            else:
                view = "stock" if arm == "h_backoff" else "share"
                assert not lines and all("wait" not in c["tools"] for c in calls), cond
                states = {q["state"] for q in queries}
                assert {q["view"] for q in queries} == {view} and states <= {"check", "hold"} and "check" in states
                assert {"hold_check", "hold_start", "hold_end"} <= names, (cond, names)
                checks = [e for e in events if e["event"] == "hold_check"]
                assert {e["data"]["kind"] for e in checks} == {"mid"}, cond          # never before a turn
                assert all(q.get("sid") in sids for q in queries)
    finally:
        server.shutdown()


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
