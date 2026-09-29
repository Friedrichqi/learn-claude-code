#!/usr/bin/env python3
"""CPU tests of the 11_multiuser_kv client side (no GPU, no model).

Unit tests: profile_run's new interventions on a fake harness module, mu_session's request ids,
budget and overflow shim, mu_workloads' specs and think draws, mu_scrape's metrics parser.

End-to-end test (test_e2e_fake_server): two real s15 sessions (mu_sweep -> lanes -> mu_session ->
profile_run -> harness) against a fake Anthropic-compatible server that answers every new user turn
with one glob tool call and then a final text. It checks what only a real run shows: the headers
reach the server, response ids join, think spans land in the trace, memory-off sessions make no
memory calls and keep one system prompt, and every session finalizes. Set MU_SKIP_E2E=1 to skip it.

    python research/11_multiuser_kv/test_multiuser.py
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
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_scrape  # noqa: E402
import mu_session  # noqa: E402
import mu_workloads as mw  # noqa: E402
import profile_run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent


# -- profile_run interventions ---------------------------------------------------------------------
class FakeRecovery:
    def __init__(self):
        self.has_escalated = False
        self.has_attempted_reactive_compact = False


def fake_module():
    runtime = types.SimpleNamespace(load_memories=lambda m: "records", read_memory_index=lambda: "catalog")
    calls = []
    mod = types.SimpleNamespace(
        MEMORY_RUNTIME=runtime, RecoveryState=FakeRecovery,
        snip_compact=lambda messages, max_messages=50: messages[-max_messages:],
        tool_result_budget=lambda messages, max_bytes=200_000: calls.append(max_bytes) or messages,
        remember_after_turn_async=lambda messages: calls.append("extract"))
    return mod, calls


def test_interventions_default_is_a_no_op():
    mod, _ = fake_module()
    before = dict(vars(mod))
    args = types.SimpleNamespace(no_snip=False, memory="on", tool_result_budget=None, reactive_compact_limit=None)
    assert profile_run.install_interventions(mod, args) == {}
    assert vars(mod) == before


def test_interventions_patch_the_harness_globals():
    mod, calls = fake_module()
    ns = types.SimpleNamespace(no_snip=True, memory="off", tool_result_budget=64000, reactive_compact_limit=3)
    applied = profile_run.install_interventions(mod, ns)
    assert applied == {"no_snip": True, "memory": "off", "tool_result_budget": 64000, "reactive_compact_limit": 3}
    history = list(range(80))
    assert mod.snip_compact(history) == history                      # snip off
    assert mod.MEMORY_RUNTIME.load_memories([]) == "" and mod.MEMORY_RUNTIME.read_memory_index() == ""
    mod.remember_after_turn_async([])
    assert "extract" not in calls                                   # no extraction queued
    mod.tool_result_budget([])
    assert calls[-1] == 64000
    state = mod.RecoveryState()
    for _ in range(3):
        assert not state.has_attempted_reactive_compact
        state.has_attempted_reactive_compact = True                 # what agent_loop does per compaction
    assert state.has_attempted_reactive_compact                     # the 4th overflow ends the turn
    assert not mod.RecoveryState().has_attempted_reactive_compact   # fresh per agent_loop


def test_think_pause_counts_from_turn_end():
    clock = {"t": 100.0}
    slept = []
    now = lambda: clock["t"]                                        # noqa: E731
    assert profile_run.think_pause(30.0, 98.0, now=now, sleep=slept.append) == 28.0
    assert slept == [28.0]
    assert profile_run.think_pause(1.0, 50.0, now=now, sleep=slept.append) == 0.0


def test_parse_args_accepts_the_new_flags():
    argv = sys.argv
    try:
        sys.argv = ["profile_run.py", "--label", "x", "--prompt", "a", "--prompt", "b", "--think-seconds", "12.5",
                    "--no-snip", "--memory", "off", "--tool-result-budget", "64000", "--reactive-compact-limit", "3"]
        args = profile_run.parse_args()
    finally:
        sys.argv = argv
    assert args.think_seconds == [12.5] and args.no_snip and args.memory == "off"
    assert args.tool_result_budget == 64000 and args.reactive_compact_limit == 3


# -- mu_session helpers ----------------------------------------------------------------------------
def test_request_id_round_trip():
    rid = mu_session.request_id("MU-n16-S012", 2, 7, "lead", "agent-root")
    assert rid == "MU-n16-S012~a2~00007~ld~root"
    want = {"label": "MU-n16-S012", "attempt": 2, "call": 7, "purpose": "ld", "agent": "root"}
    assert mu_session.parse_request_id(rid) == want
    assert mu_session.parse_request_id("chatcmpl-" + rid) == want
    assert mu_session.parse_request_id("chatcmpl-" + rid + "-0a1b2c3d") == want
    assert mu_session.request_id("L", 1, 3, "memory_extract", None).endswith("~mx~none")


def test_overflow_shim_is_recognised_and_digit_free():
    class Fake400(Exception):
        status_code = 400
    exc = Fake400("Error code: 400 - This model's maximum context length is 40960 tokens. However, you requested "
                  "8000 output tokens and your prompt contains 34290 input tokens, for a total of 42290 tokens. "
                  "Please reduce the length of the input prompt or the number of requested output tokens.")
    assert mu_session.overflow_error(exc)
    shim = mu_session.PromptTooLong("prompt is too long for the model context window")
    msg = str(shim).lower()
    assert ("prompt" in msg and "long" in msg)                      # code.py:2590 is_prompt_too_long_error
    assert not any(ch.isdigit() for ch in msg)                      # no "429"/"529" retry trigger
    other = Fake400("Error code: 400 - tool schema invalid")
    assert not mu_session.overflow_error(other)


def test_budget_caps_lead_calls():
    clock = {"t": 0.0}
    b = mu_session.Budget(cap_turn=2, cap_session=3, deadline_s=100, clock=lambda: clock["t"])
    b.admit("t1"); b.admit("t1")
    try:
        b.admit("t1"); raise AssertionError("turn cap not enforced")
    except mu_session.CallBudget:
        pass
    b.admit("t2")
    try:
        b.admit("t3"); raise AssertionError("session cap not enforced")
    except mu_session.CallBudget:
        pass
    clock["t"] = 101
    try:
        b.admit("t4"); raise AssertionError("deadline not enforced")
    except mu_session.RunDeadline:
        pass
    assert b.refused == {"turn": 1, "session": 1, "deadline": 1}
    for exc in (mu_session.CallBudget("session lead-call budget spent"),
                mu_session.RunDeadline("session backstop reached before this lead call")):
        assert not any(ch.isdigit() for ch in str(exc))


# -- workloads and scrape ----------------------------------------------------------------------------
def test_workloads_are_balanced_and_nested():
    rows = mw.build()
    summary = mw.check(rows)
    assert summary["specs"] == mw.N_COUNTED and summary["fillers"] == mw.N_FILLER
    assert 10 <= summary["think_s"]["p10"] <= 13 and 70 <= summary["think_s"]["p90"] <= 95
    counted = [r for r in rows if not r["filler"]]
    assert counted[0]["think_s"] == mw.think_draws(counted[0]["spec"])       # deterministic
    for name in mw.CONDITIONS:
        c = mw.cond(name)
        flags = mw.client_flags(c)
        assert ("--no-timestamp" in flags) == (not c["timestamp"])
        assert flags[flags.index("--memory") + 1] == c["memory"]
    assert mw.cond("n16mem")["memory"] == "on" and mw.cond("n16")["memory"] == "off"


def test_metrics_parser_sums_and_splits():
    text = "\n".join([
        '# HELP vllm:num_requests_waiting_by_reason x',
        'vllm:num_requests_waiting_by_reason{engine="0",model_name="m",reason="capacity"} 3.0',
        'vllm:num_requests_waiting_by_reason{engine="0",model_name="m",reason="deferred"} 1.0',
        'vllm:prompt_tokens_by_source_total{engine="0",model_name="m",source="local_compute"} 100.0',
        'vllm:prompt_tokens_by_source_total{engine="0",model_name="m",source="local_cache_hit"} 900.0',
        'vllm:time_to_first_token_seconds_bucket{le="0.1"} 5.0',
        'vllm:time_to_first_token_seconds_sum{engine="0"} 2.5',
        'vllm:num_preemptions_total{engine="0"} 7.0',
        'python_gc_objects_collected_total 12.0'])
    m = mu_scrape.parse_metrics(text)
    assert m["vllm:num_requests_waiting_by_reason"] == 4.0
    assert m["vllm:num_requests_waiting_by_reason{capacity}"] == 3.0
    assert m["vllm:prompt_tokens_by_source_total{local_cache_hit}"] == 900.0
    assert m["vllm:time_to_first_token_seconds_sum"] == 2.5 and m["vllm:num_preemptions_total"] == 7.0
    assert not any(k.endswith("_bucket") for k in m) and "python_gc_objects_collected_total" not in m


# -- end to end against a fake server --------------------------------------------------------------------
class FakeAnthropic(BaseHTTPRequestHandler):
    """/v1/messages: a new user turn gets one glob tool call, a tool result gets the final answer."""
    seen: list = []
    replayed: list = []
    lock = threading.Lock()
    generated = 0

    def log_message(self, *args):
        pass

    def _send(self, code: int, body, ctype: str = "application/json"):
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
            self._send(200, {"data": [{"id": mw.MODEL}]})
        elif self.path == "/metrics":
            self._send(200, f"vllm:generation_tokens_total {FakeAnthropic.generated}\n"
                            "vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n", "text/plain")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        rid = self.headers.get("X-Request-Id")
        if self.path == "/v1/completions":                      # the replay endpoint
            with FakeAnthropic.lock:
                FakeAnthropic.replayed.append({"rid": rid, "sid": self.headers.get("X-Session-ID"),
                                               "n": len(body["prompt"]), "max_tokens": body["max_tokens"],
                                               "ignore_eos": body.get("ignore_eos")})
            self._send(200, {"id": f"cmpl-{rid}", "object": "text_completion", "model": mw.MODEL,
                             "choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                             "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": body["max_tokens"],
                                       "total_tokens": len(body["prompt"]) + body["max_tokens"],
                                       "prompt_tokens_details": {"cached_tokens": 0}}})
            return
        with FakeAnthropic.lock:
            FakeAnthropic.seen.append({"rid": rid, "sid": self.headers.get("X-Session-ID"),
                                       "pri": self.headers.get("X-Vllm-Priority"),
                                       "system": body.get("system"), "n": len(body.get("messages", []))})
            FakeAnthropic.generated += 5
        last = body["messages"][-1]
        content = last.get("content")
        tool_result = isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        if tool_result:
            blocks, stop = [{"type": "text", "text": "Done: listed the files."}], "end_turn"
        else:
            blocks = [{"type": "tool_use", "id": f"toolu_{len(FakeAnthropic.seen):05d}", "name": "glob",
                       "input": {"pattern": "profiling_sandbox/*"}}]
            stop = "tool_use"
        self._send(200, {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": mw.MODEL,
                         "content": blocks, "stop_reason": stop, "stop_sequence": None,
                         "usage": {"input_tokens": 100, "output_tokens": 5,
                                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})


def free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if "429" not in str(port) and "529" not in str(port):
            return port


def test_e2e_fake_server():
    if os.environ.get("MU_SKIP_E2E") == "1":
        print("  (skipped: MU_SKIP_E2E=1)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="mu_e2e_", dir=os.environ.get("MU_TMP")))
    sessions = tmp / "sessions.jsonl"
    with sessions.open("w", encoding="utf-8") as handle:
        for row in mw.build():
            handle.write(json.dumps(row) + "\n")
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeAnthropic)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        cmd = [sys.executable, str(DIR / "mu_sweep.py"), "run", "--cond", "pilot8", "--users", "2",
               "--limit", "2", "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(tmp / "lanes"),
               "--sessions", str(sessions), "--cells-root", str(tmp / "cells"), "--think-scale", "0.02",
               "--fillers", "off", "--stagger-s", "2"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
        runs = sorted((tmp / "cells" / "pilot8" / "runs").glob("MU-pilot8-S*"))
        assert len(runs) == 2, runs
        for run in runs:
            label = run.name
            meta = json.loads((run / f"{label}.meta.json").read_text())
            assert meta["status"] == "ok", meta
            assert meta["id_mismatch"] == 0 and meta["overflows"] == 0 and meta["errors"] == 0, meta
            with lzma.open(run / f"{label}.requests.jsonl.xz", "rt") as handle:
                calls = [json.loads(line) for line in handle]
            assert calls and all(c.get("id_ok") for c in calls), calls[:1]
            assert {c["purpose"] for c in calls} == {"lead"}, {c["purpose"] for c in calls}   # memory off
            systems = {json.dumps(c["system"]) for c in calls}
            assert len(systems) == 1, "system prompt changed across calls"
            assert "Current time:" not in next(iter(systems)) and "Memory catalog" not in next(iter(systems))
            trace = next(p for p in run.glob("run_*.jsonl.xz") if ".inputs." not in p.name)
            with lzma.open(trace, "rt") as handle:
                events = [json.loads(line) for line in handle]
            waits = [e for e in events if e["event"] == "input_wait_end"]
            assert len(waits) == 3, len(waits)
            meta_ev = next(e for e in events if e["event"] == "profile_meta")["data"]
            assert meta_ev["memory"] == "off" and meta_ev["no_snip"] and meta_ev["tool_result_budget"] == 64000
            assert sum(e["event"] == "mu_call" for e in events) == len(calls)
            turns = sum(e["event"] == "turn_end" for e in events)
            assert turns == 4, turns
        seen = FakeAnthropic.seen
        assert all(s["rid"] and s["sid"] and s["pri"] is not None for s in seen)
        assert len({s["sid"] for s in seen}) == 2
        log = [json.loads(line) for line in (tmp / "cells" / "pilot8" / "cell_log.jsonl").read_text().splitlines()]
        assert sorted(r["status"] for r in log if not r["filler"]) == ["ok", "ok"]
        assert (tmp / "cells" / "pilot8" / "COMPLETE").exists()
        # -- replay the recorded cell against the same fake server ------------------------------
        replay = [sys.executable, str(DIR / "mu_replay.py")]
        roots = ["--cells-root", str(tmp / "cells"), "--plans-root", str(tmp / "plans")]
        proc = subprocess.run(replay + ["prepare", "--src", "pilot8", "--workers", "2"] + roots,
                              capture_output=True, text=True, timeout=1200)
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
        plan_meta = json.loads((tmp / "plans" / "pilot8" / "plan_meta.json").read_text())
        assert plan_meta["sessions"] == 2 and plan_meta["calls"] == 18, plan_meta
        proc = subprocess.run(replay + ["run", "--cond", "r_pilot8_default", "--base-url", f"http://127.0.0.1:{port}",
                                        "--out-dir", str(tmp / "replay")] + roots,
                              capture_output=True, text=True, timeout=1200)
        assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
        rows = [json.loads(line) for line in (tmp / "replay" / "replay_calls.jsonl").read_text().splitlines()]
        assert len(rows) == 18 and all(r["status"] == "ok" for r in rows), rows[:2]
        assert all(r["ignore_eos"] and r["n"] > 1000 for r in FakeAnthropic.replayed)   # real rendered prompts
        assert (tmp / "cells" / "r_pilot8_default" / "COMPLETE").exists()
    finally:
        server.shutdown()


if __name__ == "__main__":      # the project venv has no pytest; run the tests directly
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:        # report every failing test, not just the first
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
