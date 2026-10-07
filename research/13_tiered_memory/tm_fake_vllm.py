#!/usr/bin/env python3
"""Stand-in for `vllm serve` in test_tm.py's pipeline test (no GPU), extended from 12's fake_vllm.py with the D1
protocol of tm_server_ext: per-chain message lists, X-TM-Base (409 tm_resync when it cannot serve k), X-TM-Init,
X-TM-Verify (the body has all messages: the fake checks that held[:k] + body[k:] equals them), /tm/stats and
/tm/hint. The model answers a user turn with one read-only bash call and a tool result with a one-line reply.

    python research/13_tiered_memory/tm_fake_vllm.py serve MODEL --host 127.0.0.1 --port P [vLLM args, ignored]
Env: TM_FAKE_LOG=PATH  one JSON line per call (chain, mode, k, held, verify result)
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tm_common as tc  # noqa: E402
from tm_client import _blocks  # noqa: E402

STATE = {"calls": 0, "model": "fake", "resync": 0, "verify_ok": 0, "verify_bad": 0, "delta": 0, "full": 0,
         "hints": 0}
HELD: dict[str, list] = {}
LOCK = threading.Lock()
LOG = os.environ.get("TM_FAKE_LOG", "")


def norm(msg: dict) -> tuple:
    return (msg.get("role"), tuple(_blocks(msg.get("content"))))


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
        elif self.path == "/version":
            self._send(200, {"version": "fake"})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": STATE["model"]}]})
        elif self.path == "/tm/stats":
            self._send(200, {"stats": {k: v for k, v in STATE.items() if k != "model"}, "chains": len(HELD)})
        elif self.path == "/metrics":
            n = STATE["calls"]
            self._send(200, "vllm:cache_config_info{block_size=\"16\",num_gpu_blocks=\"1000\"} 1.0\n"
                            f"vllm:generation_tokens_total {n * 5}\nvllm:prompt_tokens_total {n * 100}\n"
                            "vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n"
                            "vllm:kv_cache_usage_perc 0.1\n", "text/plain")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        if self.path.startswith("/reset_prefix_cache"):
            self._send(200, {"success": True})
            return
        if self.path == "/tm/hint":
            with LOCK:
                STATE["hints"] += 1
            self._send(200, {"ok": True})
            return
        rid, sess = self.headers.get("X-Request-Id"), self.headers.get("X-Session-ID")
        chain = tc.chain_of(rid, sess)
        base = self.headers.get("X-TM-Base")
        verify = self.headers.get("X-TM-Verify") == "1"
        msgs = body.get("messages") or []
        rec = {"chain": chain, "rid": rid, "base": base, "verify": verify, "n_body": len(msgs)}
        with LOCK:
            STATE["calls"] += 1
            n = STATE["calls"]
            held = HELD.get(chain)
            if base is not None:
                k = int(base)
                if held is None or k > len(held) or k < 1:
                    STATE["resync"] += 1
                    rec["mode"] = "409"
                    self._log(rec)
                    self._send(409, {"type": "error", "error": {"type": "tm_resync", "message": "session state"}})
                    return
                if verify:
                    ok = [norm(m) for m in held[:k]] == [norm(m) for m in msgs[:k]]
                    STATE["verify_ok" if ok else "verify_bad"] += 1
                    rec["verify_ok"] = ok
                    full = msgs
                else:
                    full = held[:k] + msgs
                    STATE["delta"] += 1
                    if "tools" in body or "system" in body:
                        rec["delta_had_tools"] = True
                rec["mode"], rec["k"] = ("verify" if verify else "delta"), k
            else:
                full = msgs
                STATE["full"] += 1
                rec["mode"] = "full"
        time.sleep(0.2)                                   # "decode"
        last = (full or [{}])[-1].get("content")
        tool_result = isinstance(last, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in last)
        if tool_result:
            blocks, stop = [{"type": "text", "text": "Done."}], "end_turn"
        else:
            blocks = [{"type": "tool_use", "id": f"toolu_{n:05d}", "name": "bash",
                       "input": {"command": "grep -rc def research/common | tail -2"}}]
            stop = "tool_use"
        with LOCK:
            HELD[chain] = list(full) + [{"role": "assistant", "content": blocks}]
            rec["held"] = len(HELD[chain])
        kv_in = body.get("kv_transfer_params") or {}
        rec["kv"] = "producer" if kv_in.get("do_remote_decode") else ("consumer" if kv_in else None)
        self._log(rec)
        out = {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": STATE["model"],
               "content": blocks, "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": 100, "output_tokens": 5,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
        if kv_in.get("do_remote_decode"):                 # P/D: the prefill side returns the decode side's params
            out["kv_transfer_params"] = {"do_remote_prefill": True, "remote_engine_id": f"fake-{os.getpid()}",
                                         "remote_block_ids": [1, 2, 3]}
        self._send(200, out)

    @staticmethod
    def _log(rec: dict) -> None:
        if LOG:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")


def main() -> int:
    argv = sys.argv[1:]
    assert argv and argv[0] == "serve", argv
    STATE["model"] = argv[1]
    port = int(argv[argv.index("--port") + 1])
    host = argv[argv.index("--host") + 1] if "--host" in argv else "127.0.0.1"
    engine = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(1)", "EngineCore"])
    server = ThreadingHTTPServer((host, port), Handler)

    def stop(*_):
        engine.terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    time.sleep(1.0)
    server.serve_forever()
    engine.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
