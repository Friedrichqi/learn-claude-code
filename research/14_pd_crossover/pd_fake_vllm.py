#!/usr/bin/env python3
"""Stand-in for `vllm serve` in test_pd.py (no GPU): enough of vLLM for pd_launch / pd_router / pd_pipeline /
pd_replay / pd_analyze to run end to end.

    python research/14_pd_crossover/pd_fake_vllm.py serve MODEL --host 127.0.0.1 --port P [vLLM args, read for
        --kv-transfer-config only]
  POST /v1/messages      the harness's Anthropic call: a user turn gets one read-only bash tool call, a tool result
                         gets a one-line reply (13's fake behaviour), so real s15 sessions finish
  POST /v1/completions   replay: usage with prompt_tokens = len(prompt), completion_tokens = max_tokens
  kv_transfer_params     a request with do_remote_decode (prefill role) returns do_remote_prefill params naming this
                         instance; a request carrying remote_block_ids (decode role) counts them as pulled
  GET /metrics           vLLM-shaped counters, incl. vllm:nixl_* and vllm:prompt_tokens_by_source_total{source=...}
  POST /reset_prefix_cache, /tm/hint; GET /health, /v1/models
Env: PD_FAKE_LOG=PATH (one JSON line per call), MU_SCHED_LOG=PATH (a minimal 11-format scheduler log: hdr, pdhdr,
clk, arr, adm, step, tok1, fin, pdh), PD_ROLE.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"calls": 0, "model": "fake", "gen": 0, "prompt": 0, "pulled": 0, "xfers": 0, "local": 0, "running": 0}
LOCK = threading.Lock()
LOG = os.environ.get("PD_FAKE_LOG", "")
SCHED = os.environ.get("MU_SCHED_LOG", "")
ROLE = os.environ.get("PD_ROLE", "C")
SEQ = {"r": 0, "n": 0}


def sched(rec: dict) -> None:
    if SCHED:
        with LOCK, open(SCHED, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


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
        if self.path == "/health":
            self._send(200, "")
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": STATE["model"]}]})
        elif self.path == "/metrics":
            s = STATE
            self._send(200, "vllm:cache_config_info{block_size=\"16\",num_gpu_blocks=\"1000\"} 1.0\n"
                            f"vllm:generation_tokens_total {s['gen']}\nvllm:prompt_tokens_total {s['prompt']}\n"
                            f"vllm:num_requests_running {s['running']}\nvllm:num_requests_waiting 0\n"
                            "vllm:kv_cache_usage_perc 0.1\n"
                            f"vllm:prompt_tokens_by_source_total{{source=\"local_compute\"}} {s['local']}\n"
                            f"vllm:prompt_tokens_by_source_total{{source=\"external_kv_transfer\"}} {s['pulled']}\n"
                            f"vllm:nixl_bytes_transferred_sum {s['pulled'] * 262144}\n"
                            f"vllm:nixl_bytes_transferred_count {s['xfers']}\n"
                            f"vllm:nixl_xfer_time_seconds_sum {s['xfers'] * 0.005}\n"
                            f"vllm:nixl_xfer_time_seconds_count {s['xfers']}\n"
                            "vllm:nixl_num_failed_transfers_total 0\nvllm:nixl_num_kv_expired_reqs_total 0\n",
                       "text/plain")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        if self.path.startswith("/reset_prefix_cache"):
            sched({"k": "reset", "t": time.monotonic(), "ok": True})
            self._send(200, {"success": True})
            return
        if self.path == "/tm/hint":
            self._send(200, {"ok": True})
            return
        rid = self.headers.get("X-Request-Id") or f"anon-{time.time()}"
        sess = self.headers.get("X-Session-ID") or rid
        kv_in = body.get("kv_transfer_params") or {}
        completions = self.path == "/v1/completions"
        np_ = len(body.get("prompt") or []) if completions else 100 + 50 * len(body.get("messages") or [])
        no = int(body.get("max_tokens") or 1)
        no = min(no, 64) if completions else min(no, 5)
        with LOCK:
            STATE["calls"] += 1
            STATE["running"] += 1
            n = STATE["calls"]
            r = SEQ["r"]
            SEQ["r"] += 1
        t_arr = time.monotonic()
        pulled = np_ - 1 if kv_in.get("remote_block_ids") else 0
        sched({"k": "arr", "t": t_arr, "wall": time.time(), "r": r, "id": rid, "c": sess, "pri": 0,
               "fe": time.time() - 0.01, "np": np_, "mt": no, "nb": np_ // 16, "ideal": 0, "hit_now": 0, "lcp": 0,
               "gap": None, "pin": 0, "q": 0})
        load = 0.005 if pulled else 0.0
        time.sleep(load)
        sched({"k": "adm", "t": time.monotonic(), "n": SEQ["n"] + 1, "r": r, "res": False, "hit": 0, "ideal": 0,
               "wait": time.monotonic() - t_arr, "load": load, "chunk": np_ - pulled, "hl": 0, "he": pulled,
               "lost": 0, "free": 900})
        self._step([[r, pulled, max(1, np_ - pulled)]], [], 0.03)
        sched({"k": "tok1", "t": time.monotonic(), "r": r})
        for _ in range(max(0, no - 1)):
            self._step([], [r], 0.004)
        with LOCK:
            STATE["gen"] += no
            STATE["prompt"] += np_
            STATE["pulled"] += pulled
            STATE["xfers"] += 1 if pulled else 0
            STATE["local"] += np_ - pulled
            STATE["running"] -= 1
        sched({"k": "fin", "t": time.monotonic(), "r": r, "st": "length", "no": no, "npre": 0, "tool": False,
               "pin": 0})
        rec = {"rid": rid, "session": sess, "path": self.path, "role": ROLE, "np": np_, "no": no,
               "kv": "producer" if kv_in.get("do_remote_decode") else ("consumer" if kv_in else None)}
        if LOG:
            with LOCK, open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        out_kv = None
        if kv_in.get("do_remote_decode"):
            out_kv = {"do_remote_prefill": True, "do_remote_decode": False, "remote_engine_id": f"fake-{os.getpid()}",
                      "remote_request_id": rid, "remote_block_ids": [list(range(max(1, np_ // 16)))],
                      "remote_host": "127.0.0.1", "remote_port": 1, "remote_num_tokens": np_}
        if completions:
            out = {"id": f"cmpl-{rid}", "object": "text_completion", "model": STATE["model"],
                   "choices": [{"index": 0, "text": " x" * no, "finish_reason": "length"}],
                   "usage": {"prompt_tokens": np_, "completion_tokens": no, "total_tokens": np_ + no,
                             "prompt_tokens_details": {"cached_tokens": pulled}}}
        else:
            msgs = body.get("messages") or [{}]
            last = msgs[-1].get("content")
            tool_result = isinstance(last, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                         for b in last)
            if tool_result:
                blocks, stop = [{"type": "text", "text": "Done."}], "end_turn"
            else:
                blocks = [{"type": "tool_use", "id": f"toolu_{n:05d}", "name": "bash",
                           "input": {"command": "grep -rc def research/common | tail -2"}}]
                stop = "tool_use"
            out = {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": STATE["model"],
                   "content": blocks, "stop_reason": stop, "stop_sequence": None,
                   "usage": {"input_tokens": np_ - pulled, "output_tokens": no, "cache_read_input_tokens": pulled,
                             "cache_creation_input_tokens": 0}}
        if out_kv:
            out["kv_transfer_params"] = out_kv
        self._send(200, out)

    @staticmethod
    def _step(pfl, dcr, dur):
        with LOCK:
            SEQ["n"] += 1
            sn = SEQ["n"]
        s0 = time.monotonic()
        time.sleep(dur)
        u0 = time.monotonic()
        sched({"k": "step", "n": sn, "s0": s0, "s1": s0, "l": s0, "u0": u0, "u1": u0, "run": 1, "wq": 0, "sk": 0,
               "tok": sum(c[2] for c in pfl) + len(dcr), "pf": [0, 0, 0], "dc": len(dcr), "pfl": pfl, "dcr": dcr,
               "adm": 0, "res": 0, "pre": 0, "fin": 0, "free": 900, "ev": 0, "com": 0, "pin": 0})


def main() -> int:
    argv = sys.argv[1:]
    assert argv and argv[0] == "serve", argv
    STATE["model"] = argv[1]
    port = int(argv[argv.index("--port") + 1])
    host = argv[argv.index("--host") + 1] if "--host" in argv else "127.0.0.1"
    t = time.monotonic()
    sched({"k": "hdr", "t": t, "wall": time.time(), "pid": os.getpid(), "vllm": "fake", "nblk": 1000, "bs": 16,
           "mnbt": 8192, "mns": 1024, "mml": 40960, "connector": "NixlConnector" if ROLE != "C" else None})
    sched({"k": "pdhdr", "t": t, "wall": time.time(), "role": ROLE, "arm": os.environ.get("PD_ARM"),
           "idx": int(os.environ.get("PD_IDX", "0")), "tp": int(os.environ.get("PD_TP", "1")),
           "gpus": os.environ.get("PD_GPUS")})
    engine = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(1)", "EngineCore"])
    server = ThreadingHTTPServer((host, port), Handler)

    def clock():
        while True:
            time.sleep(2)
            sched({"k": "clk", "t": time.monotonic(), "wall": time.time()})
            sched({"k": "pdh", "t": time.monotonic(), "held": 0, "held_blk": 0, "rkv": 0, "rkv_blk": 0, "wait": 0,
                   "run": STATE["running"]})
    threading.Thread(target=clock, daemon=True).start()

    def stop(*_):
        engine.terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    time.sleep(0.5)
    server.serve_forever()
    engine.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
