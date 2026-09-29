#!/usr/bin/env python3
"""One simulated user of the 11_multiuser_kv experiment: a 4-turn s15 session with think pauses.

Runs research/common/profile_run.py in-process (like research/10_rope_shift/live_run.py) from a LANE
copy of the repository, after patching the Anthropic SDK's `Messages.create` so that every model call:

  * carries vLLM tags: `X-Request-Id: <label>~a<attempt>~<call>~<purpose>~<agent>` (vLLM's engine id
    becomes `chatcmpl-<that>-<8 hex>` and the response id `chatcmpl-<that>`, the join key to the
    server-side scheduler log), `X-Session-ID: <label>~a<attempt>` (Request.session_id) and
    `X-Vllm-Priority: <session arrival index>` (used only under --scheduling-policy priority);
  * gets a per-call timeout (default 3600 s; the SDK's 600 s default fired 30 times in 10_rope_shift);
  * is serialized at call time with its send/receive wall clock (the replay and the analysis need the
    exact request), and leaves an `mu_call` event inside the harness's model_request span;
  * respects a load-independent budget: lead calls per turn and per session are capped (the call is
    refused before any HTTP request, the harness ends the turn), plus a wall-clock backstop;
  * turns vLLM's context-length 400 into an error the harness recognises. vLLM says "This model's
    maximum context length is N tokens ... reduce the length of the input prompt", which
    is_prompt_too_long_error (code.py:2590) misses, so the turn would just end. The shim raises
    PromptTooLong("prompt is too long ...") instead -- with no digits, because the harness retries any
    error whose text contains "429" or "529" (code.py:2557/2569) -- and reactive_compact runs.

Afterwards the capture, trace and inputs sidecar are xz-compressed and `<label>.meta.json` +
`<label>.done` are written (live_run.finalize). SIGTERM (the sweep stopping a filler or a cell)
ends the session cleanly through the harness's own handler.

    python <lane>/research/11_multiuser_kv/mu_session.py run --spec S012 --cond n16 --run-dir DIR \
        --attempt 1 --arrival-index 7
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402
from live_run import SEED_IGNORE, RunDeadline, finalize, to_jsonable  # noqa: E402  (10_rope_shift)

REPO = Path(__file__).resolve().parents[2]
PURPOSE_CODES = {"lead": "ld", "memory_extract": "mx", "memory_recall": "mr", "memory_consolidate": "mc",
                 "compaction_summary": "cs", "one_shot": "os", "teammate": "tm"}
OVERFLOW = re.compile(r"maximum context length|maximum model length|"
                      r"reduce the length of the (?:input )?(?:prompt|messages)", re.I)
SAFE = re.compile(r"[^A-Za-z0-9_.]")


class CallBudget(Exception):
    """The session's lead-call budget is spent (per turn or per session). No digits in the message:
    the harness retries errors whose text contains 429/529."""


class PromptTooLong(Exception):
    """vLLM's context-length 400, re-raised so the harness's is_prompt_too_long_error matches."""


def request_id(label: str, attempt: int, call: int, purpose: str | None, agent: str | None) -> str:
    code = PURPOSE_CODES.get(purpose or "", "un")
    agent = SAFE.sub("_", (agent or "none").removeprefix("agent-"))[:24]
    return f"{label}~a{attempt}~{call:05d}~{code}~{agent}"


def parse_request_id(rid: str) -> dict | None:
    """Engine id (`chatcmpl-<X-Request-Id>-<8 hex>`), response id (`chatcmpl-<X-Request-Id>`) or the
    bare header value -> {label, attempt, call, purpose, agent}."""
    value = rid
    for prefix in ("chatcmpl-", "cmpl-"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    parts = value.split("~")
    if len(parts) != 5:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.]+-[0-9a-f]{8}", parts[4]):       # engine id: random suffix
        parts[4] = parts[4].rsplit("-", 1)[0]
    try:
        return {"label": parts[0], "attempt": int(parts[1].lstrip("a")), "call": int(parts[2]),
                "purpose": parts[3], "agent": parts[4]}
    except ValueError:
        return None


def overflow_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status == 400 and bool(OVERFLOW.search(str(exc)))


class Budget:
    """Lead calls per turn / per session and the wall-clock backstop."""

    def __init__(self, cap_turn: int, cap_session: int, deadline_s: float, clock=time.monotonic):
        self.cap_turn, self.cap_session, self.clock = cap_turn, cap_session, clock
        self.deadline = clock() + deadline_s
        self.per_turn: dict = {}
        self.total = 0
        self.refused = {"turn": 0, "session": 0, "deadline": 0}

    def admit(self, turn_id) -> None:
        """Raise before a lead call that the budget does not allow; count it otherwise."""
        if self.clock() > self.deadline:
            self.refused["deadline"] += 1
            raise RunDeadline("session backstop reached before this lead call")
        if self.total >= self.cap_session:
            self.refused["session"] += 1
            raise CallBudget("session lead-call budget spent")
        if self.per_turn.get(turn_id, 0) >= self.cap_turn:
            self.refused["turn"] += 1
            raise CallBudget("turn lead-call budget spent")
        self.per_turn[turn_id] = self.per_turn.get(turn_id, 0) + 1
        self.total += 1


def install_capture(path: Path, label: str, attempt: int, arrival_index: int, budget: Budget,
                    call_timeout: float) -> dict:
    """Patch anthropic's Messages.create (class level, before the harness loads). Returns live counters."""
    from anthropic.resources.messages import messages as sdk_messages

    original = sdk_messages.Messages.create
    lock = threading.Lock()
    counter = {"calls": 0, "lead": 0, "errors": 0, "overflows": 0, "refused": 0, "id_mismatch": 0,
               "timeouts": 0}
    handle = path.open("a", encoding="utf-8")
    session_id = f"{label}~a{attempt}"

    def context() -> dict:
        harness = sys.modules.get("s15_profiled_harness")
        trace = getattr(harness, "TRACE", None)
        try:
            ctx = trace.capture_context() if trace is not None else {}
        except Exception:
            ctx = {}
        return {"agent_id": ctx.get("agent_id"), "agent_kind": ctx.get("agent_kind"),
                "turn_id": ctx.get("turn_id"), "purpose": ctx.get("model_purpose")}

    def emit(event: str, data: dict) -> None:
        harness = sys.modules.get("s15_profiled_harness")
        trace = getattr(harness, "TRACE", None)
        if trace is not None:
            try:
                trace.emit(event, data)
            except Exception:
                pass

    def write(record: dict) -> None:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()

    def create(self, *args, **kwargs):
        ctx = context()
        with lock:
            call = counter["calls"]
            counter["calls"] += 1
        rid = request_id(label, attempt, call, ctx["purpose"], ctx["agent_id"])
        record = {"call_index": call, "request_id": rid, **ctx,
                  "model": kwargs.get("model"), "max_tokens": kwargs.get("max_tokens"),
                  "system": to_jsonable(kwargs.get("system")), "tools": to_jsonable(kwargs.get("tools")),
                  "messages": to_jsonable(kwargs.get("messages")),
                  "extra_body": to_jsonable(kwargs.get("extra_body"))}
        if ctx["purpose"] == "lead":
            try:
                budget.admit(ctx["turn_id"])
            except (CallBudget, RunDeadline) as exc:
                record.update(t_send=time.time(), refused=type(exc).__name__, error=str(exc))
                with lock:
                    counter["refused"] += 1
                    write(record)
                raise
        headers = dict(kwargs.get("extra_headers") or {})
        headers.update({"X-Request-Id": rid, "X-Session-ID": session_id,
                        "X-Vllm-Priority": str(arrival_index)})
        kwargs["extra_headers"] = headers
        kwargs.setdefault("timeout", call_timeout)
        emit("mu_call", {"request_id": rid, "call_index": call, "purpose": ctx["purpose"]})
        record["t_send"] = time.time()
        started = time.monotonic()
        try:
            response = original(self, *args, **kwargs)
        except Exception as exc:
            record["t_recv"] = time.time()
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            record["error"] = f"{type(exc).__name__}: {exc}"[:4000]
            timed_out = "timeout" in type(exc).__name__.lower()
            if overflow_error(exc):
                record["overflow"] = True
                numbers = [int(n) for n in re.findall(r"\d+", str(exc))]
                record["overflow_numbers"] = numbers[:6]
                with lock:
                    counter["overflows"] += 1
                    write(record)
                raise PromptTooLong("prompt is too long for the model context window") from None
            with lock:
                counter["errors"] += 1
                counter["timeouts"] += timed_out
                write(record)
            raise
        record["t_recv"] = time.time()
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        response_id = getattr(response, "id", None)
        record["response"] = {"id": response_id, "content": to_jsonable(response.content),
                              "stop_reason": response.stop_reason, "usage": to_jsonable(response.usage)}
        record["id_ok"] = response_id == f"chatcmpl-{rid}"
        with lock:
            counter["lead"] += ctx["purpose"] == "lead"
            counter["id_mismatch"] += not record["id_ok"]
            write(record)
        return response

    sdk_messages.Messages.create = create
    counter["_handle"] = handle
    return counter


def terminate(signum, _frame):
    raise SystemExit(128 + signum)


def reset_sandbox(seed: str, sandbox_rel: str) -> Path:
    sandbox = (REPO / sandbox_rel).resolve()
    assert sandbox.is_relative_to(REPO / "profiling_sandbox"), sandbox
    if sandbox.exists():
        shutil.rmtree(sandbox)
    shutil.copytree(seed, sandbox, ignore=SEED_IGNORE)
    return sandbox


def profile_argv(label: str, spec: dict, c: dict, run_dir: Path, deadline_s: float,
                 think_scale: float = 1.0) -> list[str]:
    argv = ["profile_run.py", "--label", label]
    for i, turn in enumerate(spec["turns"]):
        prompt_file = run_dir / f"{label}.turn{i + 1}.txt"
        prompt_file.write_text(turn["prompt"], encoding="utf-8")
        argv += ["--prompt-file", str(prompt_file)]
    for pause in spec["think_s"]:
        argv += ["--think-seconds", str(round(pause * think_scale, 3))]
    argv += ["--trace-dir", str(run_dir), "--max-seconds", str(int(deadline_s + 3600)),
             "--answer-out", str(run_dir / f"{label}.answer.json"), "--write-root", spec["sandbox"]]
    return argv + mw.client_flags(c)


def run(args) -> int:
    specs = mw.load_sessions(Path(args.sessions))
    spec = specs[args.spec]
    c = mw.cond(args.cond)
    label = args.label or f"MU-{args.cond}-{spec['spec']}"
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(run_dir.glob("*.jsonl")) + list(run_dir.glob("*.jsonl.xz")):
        stale.unlink()                                  # a retried attempt starts from a clean capture
    signal.signal(signal.SIGTERM, terminate)  # until the harness installs its own (same behaviour)
    sandbox = reset_sandbox(spec["seed"], spec["sandbox"])
    budget = Budget(args.lead_cap_turn, args.lead_cap_session, args.deadline_s)
    counter = install_capture(run_dir / f"{label}.requests.jsonl", label, args.attempt, args.arrival_index,
                              budget, args.call_timeout_s)
    started_wall, started = time.time(), time.monotonic()
    status, exit_code = "ok", None

    import profile_run  # research/common, the lane's own copy

    sys.argv = profile_argv(label, spec, c, run_dir, args.deadline_s, args.think_scale)
    try:
        exit_code = profile_run.main()
    except SystemExit as exc:
        exit_code = exc.code
        status = "terminated" if exc.code == 128 + signal.SIGTERM else "exit"
    except Exception as exc:  # record and keep the capture
        status = f"error: {type(exc).__name__}: {exc}"[:500]
    finally:
        counter["_handle"].close()
    if status == "ok":
        if budget.refused["deadline"]:
            status = "deadline"
        elif budget.refused["session"] or budget.refused["turn"]:
            status = "budget"
    if status == "terminated" and spec["filler"]:
        status = "filler-stopped"
    meta = {"label": label, "spec": spec["spec"], "cond": args.cond, "cell": args.cell,
            "attempt": args.attempt, "arrival_index": args.arrival_index, "filler": spec["filler"],
            "codebase": spec["codebase"], "templates": spec["templates"], "think_s": spec["think_s"],
            "think_scale": args.think_scale,
            "status": status, "exit_code": exit_code, "t_start": started_wall, "t_end": time.time(),
            "wall_s": round(time.monotonic() - started, 1),
            **{k: v for k, v in counter.items() if not k.startswith("_")},
            "lead_refused": budget.refused, "lead_per_turn": list(budget.per_turn.values()),
            "base_url": os.environ.get("ANTHROPIC_BASE_URL"), "lane": str(REPO),
            "host": os.uname().nodename, "seed": spec["seed"], "sandbox": spec["sandbox"]}
    meta = finalize(run_dir, label, meta, spec["seed"], sandbox, writable=True)
    shutil.rmtree(sandbox, ignore_errors=True)
    print(json.dumps({k: meta[k] for k in ("label", "status", "wall_s", "calls", "lead", "overflows")}))
    return 0 if status in ("ok", "budget", "deadline", "filler-stopped") else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one session (from a lane)")
    r.add_argument("--spec", required=True, help="spec id from sessions.jsonl, e.g. S012 or F003")
    r.add_argument("--cond", required=True, choices=sorted(mw.CONDITIONS))
    r.add_argument("--run-dir", required=True, help="absolute path into the real repository's data leaf")
    r.add_argument("--sessions", default=str(mw.SESSIONS))
    r.add_argument("--label", default=None)
    r.add_argument("--cell", default="")
    r.add_argument("--attempt", type=int, default=1)
    r.add_argument("--arrival-index", type=int, default=0)
    r.add_argument("--lead-cap-turn", type=int, default=mw.BUDGET["lead_cap_turn"])
    r.add_argument("--lead-cap-session", type=int, default=mw.BUDGET["lead_cap_session"])
    r.add_argument("--deadline-s", type=float, default=mw.BUDGET["deadline_s"])
    r.add_argument("--call-timeout-s", type=float, default=mw.BUDGET["call_timeout_s"])
    r.add_argument("--think-scale", type=float, default=1.0, help="scale the spec's think times (tests)")
    args = ap.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
