#!/usr/bin/env python3
"""A scripted stand-in for the model server (Anthropic /v1/messages), for end-to-end adapter checks without a
GPU: the reply depends only on how many tool results the conversation already holds.

    fake_model.py PORT [REQUEST_LOG]
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT = [
    {"type": "tool_use", "name": "bash", "input": {"command": "pwd && ls | head -5 && git status --short | head"}},
    {"type": "tool_use", "name": "write_file", "input": {"path": "s15_probe.txt", "content": "probe\n"}},
    {"type": "tool_use", "name": "read_file", "input": {"path": "s15_probe.txt"}},
    {"type": "tool_use", "name": "glob", "input": {"pattern": "*.txt"}},
    {"type": "tool_use", "name": "bash", "input": {"command": "rm -f s15_probe.txt && echo removed"}},
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = sum(1 for m in body.get("messages", []) if m.get("role") == "user" and isinstance(m.get("content"), list)
                for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result")
        if n < len(SCRIPT):
            block = dict(SCRIPT[n], id=f"t{n}")
            content, stop = [{"type": "text", "text": f"step {n}"}, block], "tool_use"
        else:
            content, stop = [{"type": "text", "text": "FINAL ANSWER: done"}], "end_turn"
        msg = {"id": "m", "type": "message", "role": "assistant", "model": body.get("model"),
               "content": content, "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": 50, "output_tokens": 5}}
        kvp = body.get("kv_transfer_params")
        if kvp is not None:                     # vLLM echoes kv_transfer_params (EPIC: the server's accounting)
            msg["kv_transfer_params"] = {"echo": kvp, "epic_server": {"apc_hit": 0}}
        if LOG:                                 # what the EPIC server would see: tag and request id
            with open(LOG, "a") as f:
                f.write(json.dumps({"epic": (kvp or {}).get("epic"), "rid": self.headers.get("X-Request-Id"),
                                    "n_messages": len(body.get("messages") or [])}) + "\n")
        payload = json.dumps(msg).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):                           # /health, and the EPIC server's /epic/end
        body = json.dumps({"found": self.path.startswith("/epic/end"), "path": self.path}).encode()
        if LOG and self.path.startswith("/epic/"):
            with open(LOG, "a") as f:
                f.write(json.dumps({"get": self.path}) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


LOG = sys.argv[2] if len(sys.argv) > 2 else ""      # optional: one JSON line per request (EPIC tag checks)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
