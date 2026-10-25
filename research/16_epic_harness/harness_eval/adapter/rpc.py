"""JSON-lines RPC between the s15 child process and the Harbor agent that owns the task container.

The child (s15, synchronous, one process per trial) asks; the agent (async, inside Harbor) executes against
the task environment and answers. Two operations are enough for every tool:

  exec    {"op": "exec", "cmd": str, "cwd": str|None, "timeout": int}
              -> {"stdout": str, "rc": int} or {"timeout": true} or {"error": str}
  upload  {"op": "upload", "path": str, "data_b64": str} -> {"ok": true} or {"error": str}

Reads, globs and edits are built from these in the child (`base64`, `find`, read-modify-upload), so the agent
side stays a thin bridge. Several requests may be in flight (s15 runs background bash in a thread): replies are
matched by id.
"""
from __future__ import annotations

import base64
import json
import socket
import threading


class RpcError(RuntimeError):
    pass


class RpcClient:
    def __init__(self, path: str):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(path)
        self._wfile = self._sock.makefile("wb")
        self._rfile = self._sock.makefile("rb")
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next = 0
        self._waiting: dict[int, list] = {}     # id -> [Event, reply]
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name="rpc-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            for line in self._rfile:
                reply = json.loads(line)
                with self._state_lock:
                    slot = self._waiting.get(reply.get("id"))
                if slot is not None:
                    slot[1] = reply
                    slot[0].set()
        except (OSError, ValueError):
            pass
        finally:
            with self._state_lock:
                self._closed = True
                pending = list(self._waiting.values())
            for slot in pending:
                slot[0].set()

    def call(self, op: str, **payload) -> dict:
        event = threading.Event()
        with self._state_lock:
            if self._closed:
                raise RpcError("agent side closed the connection")
            self._next += 1
            rid = self._next
            self._waiting[rid] = [event, None]
        data = (json.dumps({"id": rid, "op": op, **payload}) + "\n").encode()
        with self._send_lock:
            self._wfile.write(data)
            self._wfile.flush()
        event.wait()
        with self._state_lock:
            reply = self._waiting.pop(rid)[1]
        if reply is None:
            raise RpcError("agent side closed the connection")
        if reply.get("error"):
            raise RpcError(reply["error"])
        return reply

    def exec(self, cmd: str, cwd: str | None, timeout: int) -> tuple[str, int | None]:
        """(output, exit code); exit code None means the command timed out."""
        r = self.call("exec", cmd=cmd, cwd=cwd, timeout=int(timeout))
        if r.get("timeout"):
            return "", None
        return r.get("stdout") or "", int(r.get("rc", -1))

    def upload(self, path: str, data: bytes) -> None:
        self.call("upload", path=path, data_b64=base64.b64encode(data).decode())

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for f in (self._wfile, self._rfile, self._sock):
            try:
                f.close()
            except OSError:
                pass
