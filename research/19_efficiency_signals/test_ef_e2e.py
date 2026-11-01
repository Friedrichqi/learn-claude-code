#!/usr/bin/env python3
"""End-to-end CPU test of research/19_efficiency_signals' client side (no GPU, no model).

Real s15 sessions, run the way the experiment runs them (wt_sweep -> lanes -> mu_session -> profile_run ->
harness), against a fake server that answers like MiniMax-M2.7 (reasoning in the text block, several tool calls
per response) and serves a scripted share-view /wt/status. One short window cell per client arm (2 users, 40 s),
all at once. The gate arm differs only on the server and is covered by the smoke.

Checks:
  * every arm: the model id, the 16,000-token output budget and the 129,000-character context limit reach the
    harness (the request carries the first two; the trace's profile_meta the limit);
  * none: no status request, no line, no pacing tool, no server paragraph;
  * m_wait: `wait` only, the wait paragraph, exactly one [server] line per lead call, the wait's result reaches
    the next call;
  * m_tools: wait, wait_for_capacity and server_status, the tools paragraph, no pushed [server] line, a [cost]
    receipt on every lead call but a session's first (with the server's eviction split), the status read and
    the step-back results reach the next call, and the step-back runs after the round's other tools;
  * every cell ends by its window (COMPLETE); its sessions end `ok` or `terminated`.

    python research/19_efficiency_signals/test_ef_e2e.py          (WT_SKIP_E2E=1 skips it)
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
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_workloads as ew  # noqa: E402

WT = REPO / "research" / "18_harness_wait"
ARMS = ("none", "m_wait", "m_tools")
LAST = {"np": 100, "ideal": 90, "hit": 60, "wait_s": 0.5}
STOCK = {"running": 12, "waiting": 5, "waiting_capacity": 4, "kv_usage": 0.97, "hit_rate_10s": 0.04,
         "preemptions_10s": 0}


def hold_runtime():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ef_e2e_hold", REPO / "s15_integrated_harness" / "hold_runtime.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
            self._send(200, {"data": [{"id": ew.MODEL}]})
        elif url.path == "/metrics":
            self._send(200, "vllm:generation_tokens_total 10\nvllm:num_requests_running 1\n"
                            "vllm:num_requests_waiting 0\n", "text/plain")
        elif url.path == "/wt/status":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            with Fake.lock:
                Fake.status.append(q)
                n = Fake.per_sid[(q.get("sid"), q.get("state"))]
                Fake.per_sid[(q.get("sid"), q.get("state"))] += 1
            crowded = q.get("state") == "wait" and n % 2 == 0          # a step-back waits one poll, then fits
            body = {"view": q.get("view"), "age_ms": 20.0, **STOCK}
            if q.get("view") == "share":
                body.update(pool_tokens=100000, mid_sessions=3, holders=1,
                            me={"sid": q.get("sid"), "known": True, "ws": 20000, "prefix": 15000, "res": 20000,
                                "mid": True, "ahead_ws": 85000 if crowded else 10000, "ahead_n": 2, "rank": 3,
                                "last": LAST})
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
                                  "last": blocks_in, "n": len(body["messages"]), "system": body.get("system"),
                                  "model": body.get("model"), "max_tokens": body.get("max_tokens")})
            k = len(Fake.messages)
        if tool_result:
            out, stop = [{"type": "text", "text": "<think>All listed.\n</think>\n\nDone: listed the files."}], "end_turn"
        else:
            glob = {"type": "tool_use", "id": f"toolu_g{k:05d}", "name": "glob", "input": {"pattern": "profiling_sandbox/*"}}
            if "server_status" in tools:     # read the status, list, then step back (the step-back runs last)
                out = [{"type": "text", "text": "<think>Check the server first."},
                       {"type": "tool_use", "id": f"toolu_y{k:05d}", "name": "wait_for_capacity",
                        "input": {"max_seconds": 5, "reason": "crowded"}},
                       {"type": "tool_use", "id": f"toolu_s{k:05d}", "name": "server_status", "input": {}}, glob]
            elif "wait" in tools:
                out = [{"type": "text", "text": "<think>Crowded."},
                       {"type": "tool_use", "id": f"toolu_w{k:05d}", "name": "wait",
                        "input": {"seconds": 1.5, "reason": "server crowded"}}, glob]
            else:
                out = [{"type": "text", "text": "<think>List the files."}, glob]
            stop = "tool_use"
        self._send(200, {"id": f"msg-{rid}", "type": "message", "role": "assistant", "model": ew.MODEL,
                         "content": out, "stop_reason": stop, "stop_sequence": None,
                         "usage": {"input_tokens": 40, "output_tokens": 5, "cache_read_input_tokens": 60,
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
    return out


def results(blocks) -> dict[str, str]:
    return {b["tool_use_id"]: b["content"] for b in blocks
            if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), str)}


def test_e2e_window_cells_per_arm():
    if os.environ.get("WT_SKIP_E2E") == "1":
        print("  (skipped: WT_SKIP_E2E=1)")
        return
    hold = hold_runtime()
    tmp = Path(tempfile.mkdtemp(prefix="ef_e2e_", dir=os.environ.get("WT_TMP")))
    conds = {f"t_{arm}": ew.cell("test", 2, arm, 2, 40, fillers=False) for arm in ARMS}
    (tmp / "conditions.json").write_text(json.dumps(conds))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "MU_EXTRA_CONDITIONS": str(tmp / "conditions.json")}
    try:
        procs = {}
        for cond in conds:
            cmd = [sys.executable, str(WT / "wt_sweep.py"), "run", "--cond", cond, "--users", "2",
                   "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(tmp / "lanes" / cond),
                   "--sessions", str(ew.SESSIONS), "--cells-root", str(tmp / "cells"), "--think-scale", "0.02",
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
            sids, events = set(), []
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
            names = {e["event"] for e in events}
            assert calls, cond
            assert {c["model"] for c in calls} == {ew.MODEL}, cond
            assert {c["max_tokens"] for c in calls} == {16000}, (cond, {c["max_tokens"] for c in calls})
            limits = {e["data"].get("context_limit") for e in events if e["event"] == "profile_meta"}
            assert limits == {129000}, (cond, limits)
            pushed = [t for c in calls for t in texts(c["last"]) if t.startswith("[server]")]
            receipts = [t for c in calls for t in texts(c["last"]) if t.startswith("[cost]")]
            pacing = {"wait", "wait_for_capacity", "server_status"}
            if arm == "none":
                assert not queries and not pushed and not receipts, cond
                assert all(not pacing & set(c["tools"]) for c in calls), cond
                assert all("Shared server" not in (c["system"] or "") for c in calls), cond
                continue
            section = hold.SERVER_SECTIONS["wait" if arm == "m_wait" else "tools"]
            assert all(section in c["system"] for c in calls), cond
            got = {c["rid"]: results(c["last"]) for c in calls}
            if arm == "m_wait":
                assert all(pacing & set(c["tools"]) == {"wait"} for c in calls), cond
                for c in calls:                           # exactly one [server] share line per lead call
                    mine = [t for t in texts(c["last"]) if t.startswith("[server]")]
                    assert len(mine) == 1 and mine[0].startswith("[server] KV pool"), (cond, mine)
                assert not receipts, cond
                waited = [r for rs in got.values() for r in rs.values() if r.startswith("Waited ")]
                assert waited, f"{cond}: no wait result reached the model"
                assert {q["view"] for q in queries} == {"share"} and {"line", "wait"} <= {q["state"] for q in queries}
                assert "wait_detail" in names
            else:
                assert all(pacing <= set(c["tools"]) for c in calls), cond
                assert not pushed, f"{cond}: the status line must not be pushed"
                first = {}
                for c in calls:
                    first.setdefault(c["sid"], c["rid"])
                for c in calls:
                    mine = [t for t in texts(c["last"]) if t.startswith("[cost]")]
                    want = 0 if c["rid"] == first[c["sid"]] else 1
                    assert len(mine) == want, (cond, c["rid"], mine)
                assert all("100 prompt tokens, 60 reused from the server's cache, 30 recomputed because they had "
                           "been evicted, 10 new or changed; queued 0.5 s; 5 tokens generated;" in t for t in receipts)
                outs = [r for rs in got.values() for r in rs.values()]
                assert any(r.startswith("[server] running 12") and "KV pool 100,000" in r for r in outs), cond
                assert any(r.startswith("Waited ") and "there is room for your context now" in r for r in outs), cond
                states = {q["state"] for q in queries}
                assert {"line", "pull", "wait"} <= states and {q["view"] for q in queries} == {"share"}, states
                assert {"yield_detail", "status_pull"} <= names, names
                for c in calls:                           # the step-back's result is first (tool_use order)...
                    ids = list(results(c["last"]))
                    if any(i.startswith("toolu_y") for i in ids):
                        assert ids[0].startswith("toolu_y"), ids
                spans = [e for e in events if e["event"] == "tool_end"]
                order = [e["data"].get("tool") for e in spans]
                for i, name in enumerate(order):           # ...but it ran after the round's glob
                    if name == "wait_for_capacity":
                        assert "glob" in order[max(0, i - 3):i], order[max(0, i - 3):i + 1]
    finally:
        server.shutdown()


def test_e2e_cycle_specs():
    """Main cells' cycle_specs (prereg amendment 1): with one counted spec and two users, the second user runs it
    again as pass 2; the session gets the manifest's spec under its own label, and the cell still ends by its
    window."""
    if os.environ.get("WT_SKIP_E2E") == "1":
        print("  (skipped: WT_SKIP_E2E=1)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="ef_e2e_cycle_", dir=os.environ.get("WT_TMP")))
    cond = "t_cycle"
    (tmp / "conditions.json").write_text(json.dumps({cond: ew.cell("test", 2, "none", 2, 30, fillers=False,
                                                                   cycle=True)}))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "MU_EXTRA_CONDITIONS": str(tmp / "conditions.json")}
    try:
        cmd = [sys.executable, str(WT / "wt_sweep.py"), "run", "--cond", cond, "--users", "2", "--limit", "1",
               "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(tmp / "lanes" / cond),
               "--sessions", str(ew.SESSIONS), "--cells-root", str(tmp / "cells"), "--think-scale", "0.02",
               "--fillers", "off", "--stagger-s", "1"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
        assert out.returncode == 0, out.stdout[-3000:]
        assert "pass 2 queued" in out.stdout, out.stdout[-3000:]
        root = tmp / "cells" / cond
        assert (root / "COMPLETE").exists()
        labels = sorted(p.name for p in (root / "runs").iterdir())
        assert labels[:2] == [f"MU-{cond}-S000", f"MU-{cond}-S000-p2"], labels
        for label in labels:
            meta = json.loads((root / "runs" / label / f"{label}.meta.json").read_text())
            assert meta["spec"] == "S000" and meta["label"] == label and not meta.get("filler"), meta
        rows = [json.loads(line) for line in (root / "cell_log.jsonl").read_text().splitlines()]
        rows = [r for r in rows if "label" in r]
        assert {r["spec"] for r in rows} == {"S000"} and len({r["label"] for r in rows}) == len(rows), rows
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
