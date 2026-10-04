#!/usr/bin/env python3
"""Stand-in for `vllm serve` in test_hw.py's pipeline test (no GPU): the endpoints hw_pipeline.sh, mu_sweep and
the harness use, an "EngineCore" child process, and a model that answers each user turn with one read-only
bash call (grep -rc) and then a one-line reply, after a short think.

    python research/12_hw_utilization/fake_vllm.py serve MODEL --host 127.0.0.1 --port P [vLLM args, ignored]
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"calls": 0, "model": "fake"}
LOCK = threading.Lock()


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
        rid = self.headers.get("X-Request-Id")
        with LOCK:
            STATE["calls"] += 1
            n = STATE["calls"]
        time.sleep(0.3)                                   # "decode"
        if self.path == "/v1/completions":
            self._send(200, {"id": f"cmpl-{rid}", "object": "text_completion", "model": STATE["model"],
                             "choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                             "usage": {"prompt_tokens": len(body.get("prompt", [])),
                                       "completion_tokens": body.get("max_tokens", 1),
                                       "prompt_tokens_details": {"cached_tokens": 0}}})
            return
        last = (body.get("messages") or [{}])[-1].get("content")
        tool_result = isinstance(last, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in last)
        if tool_result:
            blocks, stop = [{"type": "text", "text": "Done."}], "end_turn"
        else:
            blocks = [{"type": "tool_use", "id": f"toolu_{n:05d}", "name": "bash",
                       "input": {"command": "grep -rc def research/common | tail -2"}}]
            stop = "tool_use"
        self._send(200, {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": STATE["model"],
                         "content": blocks, "stop_reason": stop, "stop_sequence": None,
                         "usage": {"input_tokens": 100, "output_tokens": 5,
                                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})


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
    time.sleep(1.0)                                       # "loading"
    server.serve_forever()
    engine.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
