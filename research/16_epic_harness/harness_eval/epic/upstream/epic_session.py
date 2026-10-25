#!/usr/bin/env python3
"""One live s15 session for the EPIC study, with full request capture (runs inside a lane).

Built on 11_multiuser_kv/mu_session.py (overflow shim, lead-call Budget) and 10_rope_shift/live_run.py
(capture schema, sandbox reset, finalize). What this driver adds:

  * Arm tagging. Every lead call carries `extra_body.kv_transfer_params.epic = {arm, spec, call}`;
    epic_server_ext plans the call's KV from it (prefix / shift / epicK). `--arm vanilla` sends no
    tag (E-0 runs on stock vLLM). Calls of other purposes (compaction summary, memory) are never
    tagged, so the server serves them as plain requests.
  * Deterministic prompt bytes, so every arm of a block sees byte-identical requests until its
    trajectory diverges: vLLM's random tool_use ids are rewritten to `toolu_<call>_<j>` before the
    harness sees them (they reach tool_result ids and the paths micro_compact writes into
    placeholders), and snip/summary transcripts are named by a per-run counter instead of time_ns.
  * The per-call statistics the server returns in `kv_transfer_params` are kept in the capture.
  * Regimes: R1 (snip + micro at a 50k-char limit), R2 (no snip, window-overflow reactive
    compaction: summary + last 5 messages), OFF (compaction off, for E-0's reuse baseline).

    python <lane>/research/16_epic_harness/epic_session.py --row ROW.json --arm epic64 --regime R1 \
        --run-dir DIR [--label L] [--lead-cap 150] [--deadline-s 7200]
"""
from __future__ import annotations

import argparse
import itertools
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

from live_run import SEED_IGNORE, RunDeadline, finalize, to_jsonable  # noqa: E402  (10_rope_shift)
from mu_session import Budget, CallBudget, PromptTooLong, overflow_error, request_id  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ARMS = ("vanilla", "prefix", "prefix_aa", "shift", "epic32", "epic64", "epic128")
COMMON_FLAGS = ["--no-reads-log", "--client-max-retries", "0", "--server-info", "--quiet-seconds", "2",
                "--memory", "off"]
REGIME_FLAGS = {
    "R1": ["--context-limit", "50000"],
    "R2": ["--no-snip", "--context-limit", "1000000000", "--tool-result-budget", "40000",
           "--reactive-compact-limit", "1000"],
    "OFF": ["--no-snip", "--context-limit", "1000000000", "--tool-result-budget", "40000"],
}


def install_capture(path: Path, label: str, arm: str, attempt: int, budget: Budget,
                    call_timeout: float, seed_key: str, out_cap: int = 0) -> dict:
    """Patch anthropic's Messages.create (class level, before the harness loads). `out_cap` > 0 refuses
    lead calls once the run's lead calls have generated that many output tokens (status "budget")."""
    from anthropic.resources.messages import messages as sdk_messages

    original = sdk_messages.Messages.create
    lock = threading.Lock()
    counter = {"calls": 0, "lead": 0, "errors": 0, "overflows": 0, "refused": 0, "tool_ids": 0, "out_tokens": 0}
    handle = path.open("a", encoding="utf-8")

    def context() -> dict:
        harness = sys.modules.get("s15_profiled_harness")
        trace = getattr(harness, "TRACE", None)
        try:
            ctx = trace.capture_context() if trace is not None else {}
        except Exception:
            ctx = {}
        return {"agent_id": ctx.get("agent_id"), "agent_kind": ctx.get("agent_kind"),
                "turn_id": ctx.get("turn_id"), "purpose": ctx.get("model_purpose")}

    def write(record: dict) -> None:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()

    def create(self, *args, **kwargs):
        ctx = context()
        with lock:
            call = counter["calls"]
            counter["calls"] += 1
        rid = request_id(label, attempt, call, ctx["purpose"], ctx["agent_id"])
        lead = ctx["purpose"] == "lead"
        if lead and arm != "vanilla":
            extra = dict(kwargs.get("extra_body") or {})
            extra["kv_transfer_params"] = {"epic": {"arm": arm, "spec": f"{label}~a{attempt}",
                                                    "call": call, "seed_key": seed_key}}
            kwargs["extra_body"] = extra
        record = {"call_index": call, "request_id": rid, "arm": arm, **ctx,
                  "model": kwargs.get("model"), "max_tokens": kwargs.get("max_tokens"),
                  "system": to_jsonable(kwargs.get("system")), "tools": to_jsonable(kwargs.get("tools")),
                  "messages": to_jsonable(kwargs.get("messages")),
                  "extra_body": to_jsonable(kwargs.get("extra_body"))}
        if lead:
            try:
                if out_cap and counter["out_tokens"] >= out_cap:
                    budget.refused["out"] = budget.refused.get("out", 0) + 1
                    raise CallBudget("session output-token budget spent")
                budget.admit(ctx["turn_id"])
            except (CallBudget, RunDeadline) as exc:
                record.update(t_send=time.time(), refused=type(exc).__name__, error=str(exc))
                with lock:
                    counter["refused"] += 1
                    write(record)
                raise
        headers = dict(kwargs.get("extra_headers") or {})
        headers["X-Request-Id"] = rid
        kwargs["extra_headers"] = headers
        kwargs.setdefault("timeout", call_timeout)
        record["t_send"] = time.time()
        started = time.monotonic()
        try:
            response = original(self, *args, **kwargs)
        except Exception as exc:
            record["t_recv"] = time.time()
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            record["error"] = f"{type(exc).__name__}: {exc}"[:4000]
            if overflow_error(exc):
                record["overflow"] = True
                with lock:
                    counter["overflows"] += 1
                    write(record)
                raise PromptTooLong("prompt is too long for the model context window") from None
            with lock:
                counter["errors"] += 1
                write(record)
            raise
        record["t_recv"] = time.time()
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        tool_ids = {}
        for j, block in enumerate(b for b in response.content if getattr(b, "type", None) == "tool_use"):
            tool_ids[block.id] = f"toolu_{call:05d}_{j}"
            block.id = tool_ids[block.id]
        extra = getattr(response, "model_extra", None) or {}
        record["response"] = {"id": getattr(response, "id", None),
                              "content": to_jsonable(response.content),
                              "stop_reason": response.stop_reason, "usage": to_jsonable(response.usage),
                              "kv_transfer_params": to_jsonable(extra.get("kv_transfer_params")),
                              "tool_id_map": tool_ids}
        with lock:
            counter["lead"] += lead
            counter["tool_ids"] += len(tool_ids)
            if lead:
                counter["out_tokens"] += int(getattr(response.usage, "output_tokens", 0) or 0)
            write(record)
        return response

    def stream(self, *args, **kwargs):
        raise RuntimeError("epic_session captures Messages.create only; do not pass --stream")

    sdk_messages.Messages.create = create
    sdk_messages.Messages.stream = stream
    counter["_handle"] = handle
    return counter


def install_determinism(profile_run) -> None:
    """Name snip/summary transcripts by a per-run counter (they appear in the snip marker the model
    reads; time_ns would make every arm's prompt differ). profile_run wipes .transcripts per run."""
    original = profile_run.install_interventions

    def install(mod, args):
        applied = original(mod, args)
        counter = itertools.count()

        def write_transcript(messages: list) -> Path:
            mod.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
            path = mod.TRANSCRIPT_DIR / f"transcript_{next(counter):05d}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for message in messages:
                    handle.write(json.dumps(message, default=str) + "\n")
            return path

        mod.write_transcript = write_transcript
        applied["deterministic_transcripts"] = True
        return applied

    profile_run.install_interventions = install


def end_session(spec: str) -> None:
    """Free the server-side session (its KV store slots); best effort."""
    import urllib.parse
    import urllib.request
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if not base:
        return
    try:
        urllib.request.urlopen(f"{base}/epic/end?spec={urllib.parse.quote(spec)}", timeout=30).read()
    except Exception:
        pass


def terminate(signum, _frame):
    raise SystemExit(128 + signum)


def reset_sandbox(seed: str, sandbox_rel: str) -> Path:
    sandbox = (REPO / sandbox_rel).resolve()
    assert sandbox.is_relative_to(REPO / "profiling_sandbox"), sandbox
    if sandbox.exists():
        shutil.rmtree(sandbox)
    shutil.copytree(seed, sandbox, ignore=SEED_IGNORE)
    return sandbox


def profile_argv(label: str, row: dict, regime: str, run_dir: Path, deadline_s: float) -> list[str]:
    prompt_file = run_dir / f"{label}.prompt.txt"
    prompt_file.write_text(row["prompt"], encoding="utf-8")
    argv = ["profile_run.py", "--label", label, "--prompt-file", str(prompt_file),
            "--trace-dir", str(run_dir), "--max-seconds", str(int(deadline_s + 3600)),
            "--answer-out", str(run_dir / f"{label}.answer.json")]
    if row["family"] == "MODIFY":
        argv += ["--write-root", row["sandbox"]]
    return argv + COMMON_FLAGS + REGIME_FLAGS[regime]


def run(args) -> int:
    row = json.loads(Path(args.row).read_text(encoding="utf-8")) if args.row else json.loads(args.row_json)
    label = args.label or f"{row['label']}-{args.arm}"
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(run_dir.glob("*.jsonl")) + list(run_dir.glob("*.jsonl.xz")):
        stale.unlink()                                  # a retried attempt starts from a clean capture
    signal.signal(signal.SIGTERM, terminate)
    sandbox = reset_sandbox(row["seed"], row["sandbox"])
    budget = Budget(args.lead_cap, args.lead_cap, args.deadline_s)
    counter = install_capture(run_dir / f"{label}.requests.jsonl", label, args.arm, args.attempt,
                              budget, args.call_timeout_s, seed_key=f"{row['label']}~a{args.attempt}",
                              out_cap=args.out_cap)
    started_wall, started = time.time(), time.monotonic()
    status, exit_code = "ok", None

    import profile_run  # research/common, the lane's own copy

    install_determinism(profile_run)
    sys.argv = profile_argv(label, row, args.regime, run_dir, args.deadline_s)
    try:
        exit_code = profile_run.main()
    except SystemExit as exc:
        exit_code = exc.code
        status = "terminated" if exc.code == 128 + signal.SIGTERM else "exit"
    except Exception as exc:  # record and keep the capture
        status = f"error: {type(exc).__name__}: {exc}"[:500]
    finally:
        counter["_handle"].close()
        if args.arm != "vanilla":
            end_session(f"{label}~a{args.attempt}")
    if status == "ok":
        if budget.refused["deadline"]:
            status = "deadline"
        elif budget.refused["session"] or budget.refused["turn"] or budget.refused.get("out"):
            status = "budget"
    meta = {"label": label, "row": row["label"], "cell": row.get("cell"), "block": row.get("block"),
            "arm": args.arm, "regime": args.regime, "attempt": args.attempt,
            "codebase": row["codebase"], "template": row["template"], "family": row["family"],
            "status": status, "exit_code": exit_code, "t_start": started_wall, "t_end": time.time(),
            "wall_s": round(time.monotonic() - started, 1),
            **{k: v for k, v in counter.items() if not k.startswith("_")},
            "lead_refused": budget.refused, "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
            "lane": str(REPO), "host": os.uname().nodename, "seed": row["seed"],
            "sandbox": row["sandbox"], "profile_args": sys.argv[1:]}
    meta = finalize(run_dir, label, meta, row["seed"], sandbox, writable=row["family"] == "MODIFY")
    if row["family"] == "MODIFY":                       # keep the final tree for the graders
        archive = run_dir / f"{label}.sandbox"
        if archive.exists():
            shutil.rmtree(archive)
        shutil.copytree(sandbox, archive, ignore=SEED_IGNORE)
    shutil.rmtree(sandbox, ignore_errors=True)
    print(json.dumps({k: meta[k] for k in ("label", "status", "wall_s", "calls", "lead", "overflows")}))
    return 0 if status in ("ok", "budget", "deadline") else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--row", help="manifest row (JSON file)")
    ap.add_argument("--row-json", help="manifest row (JSON string)")
    ap.add_argument("--arm", required=True, choices=ARMS)
    ap.add_argument("--regime", required=True, choices=sorted(REGIME_FLAGS))
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--lead-cap", type=int, default=150)
    ap.add_argument("--out-cap", type=int, default=0, help="output tokens of lead calls per run (0: no cap)")
    ap.add_argument("--deadline-s", type=float, default=7200.0, help="wall-clock backstop only")
    ap.add_argument("--call-timeout-s", type=float, default=3600.0)
    args = ap.parse_args()
    if not (args.row or args.row_json):
        ap.error("--row or --row-json is required")
    sys.exit(run(args))


if __name__ == "__main__":
    main()
