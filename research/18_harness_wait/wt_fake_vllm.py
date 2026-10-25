#!/usr/bin/env python3
"""Stand-in for `vllm serve` in test_wt_pipeline.py (no GPU): the endpoints wt_pipeline, wt_sweep and the harness use,
the scheduler log header the pipeline checks (MU_SCHED_LOG), an engine snapshot (WT_STATUS_PATH) and /wt/status served
by the real wt_server_ext.Status. The model answers each user turn with one glob call and then a one-line reply.

    python research/18_harness_wait/wt_fake_vllm.py serve MODEL --host 127.0.0.1 --port P [vLLM args, mostly ignored]
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wt_server_ext as ext  # noqa: E402

ARGS = sys.argv[1:]


def arg(name: str, default=None):
    return ARGS[len(ARGS) - 1 - ARGS[::-1].index(name) + 1] if name in ARGS else default


MODEL = ARGS[1] if len(ARGS) > 1 else "fake"
NBLK = int(arg("--num-gpu-blocks-override", 19500))
STATE = {"running": 0, "calls": 0, "q": 0.0, "h": 0.0, "sessions": {}}
LOCK = threading.Lock()


def registry_values() -> dict:
    with LOCK:
        return {"running": float(STATE["running"]), "waiting": 0.0, "waiting_capacity": 0.0, "waiting_deferred": 0.0,
                "kv_usage": 0.5, "prefix_q": STATE["q"], "prefix_h": STATE["h"], "preempt": 0.0}


SAMPLER = ext.Sampler(read=registry_values)
STATUS = ext.Status(SAMPLER, ext.Snapshot(os.environ.get("WT_STATUS_PATH")), os.environ.get("WT_EXT_LOG"))


def write_header() -> None:
    path = os.environ.get("MU_SCHED_LOG")
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    t = time.monotonic()
    with open(path, "a") as handle:
        handle.write(json.dumps({"k": "hdr", "t": t, "wall": time.time(), "pid": os.getpid(), "vllm": "0.31.0",
                                 "cls": "WTScheduler", "nblk": NBLK, "bs": 16, "async": True,
                                 "gate": {"k": int(os.environ.get("MU_GATE", "0") or 0), "idle_s": 5.0}}) + "\n")
        handle.write(json.dumps({"k": "wthdr", "t": t, "path": os.environ.get("WT_STATUS_PATH")}) + "\n")


def snapshots() -> None:
    path = os.environ.get("WT_STATUS_PATH")
    while path:
        with LOCK:
            sessions = {sid: dict(v) for sid, v in STATE["sessions"].items()}
            running = STATE["running"]
        snap = {"wall": time.time(), "t": time.monotonic(), "seq": 1, "step": STATE["calls"],
                "pool": {"blocks": NBLK - 1, "bs": 16, "tokens": (NBLK - 1) * 16, "free": NBLK // 2, "usage": 0.5},
                "running": running, "waiting": 0, "deferred": 0, "cum": {}, "sessions": sessions}
        tmp = f"{path}.tmp"
        with open(tmp, "w") as handle:
            json.dump(snap, handle)
        os.replace(tmp, path)
        time.sleep(0.2)


class Handler(BaseHTTPRequestHandler):
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
            self._send(200, {"version": "0.31.0-fake"})
        elif url.path == "/v1/models":
            self._send(200, {"data": [{"id": MODEL}]})
        elif url.path == "/metrics":
            with LOCK:
                text = (f"vllm:generation_tokens_total {5 * STATE['calls']}\nvllm:num_requests_running {STATE['running']}\n"
                        "vllm:num_requests_waiting 0\nvllm:kv_cache_usage_perc 0.5\n")
            self._send(200, text, "text/plain")
        elif url.path == "/wt/status":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            turn = ext._float(q.get("turn"))
            out = STATUS.build(q.get("view", "stock"), q.get("sid"), q.get("state", "line"), turn)
            STATUS.write_log({"wall": time.time(), "sid": q.get("sid"), "state": q.get("state"), "view": q.get("view"),
                              "out": {k: out.get(k) for k in ("age_ms", "running", "waiting", "me")}})
            self._send(200, out)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        rid = self.headers.get("X-Request-Id") or "probe"
        sid = self.headers.get("X-Session-ID")
        if self.path == "/v1/completions":
            self._send(200, {"id": f"cmpl-{rid}", "object": "text_completion", "model": MODEL,
                             "choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        with LOCK:
            STATE["running"] += 1
            STATE["calls"] += 1
            STATE["q"] += 1000
            STATE["h"] += 800
            k = STATE["calls"]
            if sid:
                s = STATE["sessions"].setdefault(sid, {"first": time.time(), "turn_t0": time.time(), "inflight": 0,
                                                       "queued": False, "idle_s": 0.0, "mid": True, "np": 0, "out": 0,
                                                       "ws": 0, "res": 0, "prefix": 0})
                s.update(inflight=1, mid=True, np=1000 + 50 * k, ws=1100 + 50 * k, res=1000, prefix=900)
        time.sleep(0.05)
        last = body["messages"][-1].get("content")
        blocks = last if isinstance(last, list) else [{"type": "text", "text": last}]
        tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
        if tool_result:
            out, stop = [{"type": "text", "text": "Done: listed the files."}], "end_turn"
        else:
            out, stop = [{"type": "tool_use", "id": f"toolu_{k:05d}", "name": "glob",
                          "input": {"pattern": "profiling_sandbox/*"}}], "tool_use"
        with LOCK:
            STATE["running"] -= 1
            if sid in STATE["sessions"]:
                STATE["sessions"][sid].update(inflight=0, mid=not tool_result)
        self._send(200, {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": MODEL,
                         "content": out, "stop_reason": stop, "stop_sequence": None,
                         "usage": {"input_tokens": 200, "output_tokens": 5, "cache_read_input_tokens": 800,
                                   "cache_creation_input_tokens": 0}})


def main():
    port = int(arg("--port", 8000))
    host = arg("--host", "127.0.0.1")
    write_header()
    threading.Thread(target=snapshots, daemon=True).start()
    server = ThreadingHTTPServer((host, port), Handler)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    server.serve_forever()


if __name__ == "__main__":
    main()
