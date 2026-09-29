#!/usr/bin/env python3
"""One live s15 session through research/common/profile_run.py, with full request capture.

profile_run.py records sizes and tool-result text but not the requests themselves (04's
`--dump-requests` was never committed). This wrapper patches the Anthropic SDK's
`Messages.create` before profile_run loads the harness, so every model call of every agent (lead,
compaction summary, memory) is serialized AT CALL TIME -- the harness later rewrites the same block
dicts in place -- into `<run-dir>/<label>.requests.jsonl` in 04_kv_splice's schema (model,
max_tokens, system, tools, messages, extra_body, response{content, stop_reason, usage}, plus
agent_id / agent_kind / purpose / turn_id from the trace's context and call_index / ts /
duration_ms). It also resets the codebase sandbox inside the lane before the run, enforces the run
deadline on the lead (see RunDeadline), and afterwards xz-compresses the trace, inputs sidecar and
requests, writes a unified diff of the sandbox for MODIFY runs, and drops `<label>.meta.json` +
`<label>.done`. `finalize()` is also what run_sweep.py uses to salvage a run it had to kill.

Run it from a LANE copy of the repository (profile_run chdirs to its own repo root and wipes the
harness state there); `--run-dir` is an absolute path into the real repository's data leaf.

    python <lane>/research/10_rope_shift/live_run.py --label L --run-dir DIR --seed SRC \
        --sandbox profiling_sandbox/requests [--writable] -- <profile_run.py arguments>
"""
from __future__ import annotations

import argparse
import json
import lzma
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

REPO = Path(__file__).resolve().parents[2]
SEED_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "traces", "*.jsonl", "*.so", "*.pyi",
                                     "py.typed")
DIFF_EXCLUDES = ["-x", "__pycache__", "-x", "*.pyc", "-x", "*.so", "-x", "traces", "-x", "*.jsonl",
                 "-x", "*.pyi", "-x", "py.typed"]


def to_jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class RunDeadline(Exception):
    """Raised on the first lead call after the run's deadline. profile_run's --max-seconds only bounds
    the wait for teammates, not the lead's own loop, so a lead that keeps re-reading (as the 50k-limit
    runs do after snip_compact evicts the evidence) would never stop. The harness treats the exception
    as a model error: it records a stop decision, appends an "[Error] ..." turn and ends the turn, and
    profile_run then shuts down normally. The name avoids every retry trigger in with_retry."""


def install_capture(path: Path, deadline: float | None = None) -> dict:
    """Patch anthropic's Messages.create; returns a live counter dict."""
    from anthropic.resources.messages import messages as sdk_messages

    original = sdk_messages.Messages.create
    lock = threading.Lock()
    counter = {"calls": 0, "errors": 0, "lead": 0, "deadline_hit": False}
    handle = path.open("a", encoding="utf-8")

    def context() -> dict:
        harness = sys.modules.get("s15_profiled_harness")
        trace = getattr(harness, "TRACE", None)
        try:
            ctx = trace.capture_context() if trace is not None else {}
        except Exception:
            ctx = {}
        return {"agent_id": ctx.get("agent_id"), "agent_kind": ctx.get("agent_kind"),
                "parent_agent_id": ctx.get("parent_agent_id"), "turn_id": ctx.get("turn_id"),
                "purpose": ctx.get("model_purpose")}

    def write(record: dict):
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()

    def create(self, *args, **kwargs):
        record = {
            "model": kwargs.get("model"), "max_tokens": kwargs.get("max_tokens"),
            "system": to_jsonable(kwargs.get("system")), "tools": to_jsonable(kwargs.get("tools")),
            "messages": to_jsonable(kwargs.get("messages")),
            "extra_body": to_jsonable(kwargs.get("extra_body")),
            "ts": time.time(), **context(),
        }
        if deadline is not None and record["purpose"] == "lead" and time.monotonic() > deadline:
            record["error"] = "RunDeadline: run deadline reached before this lead call"
            with lock:
                record["call_index"] = counter["calls"]
                counter["calls"] += 1
                counter["deadline_hit"] = True
                write(record)
            raise RunDeadline("run deadline reached")
        with lock:
            record["call_index"] = counter["calls"]
            counter["calls"] += 1
            counter["lead"] += record["purpose"] == "lead"
        started = time.monotonic()
        try:
            response = original(self, *args, **kwargs)
        except Exception as exc:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
            record["error"] = f"{type(exc).__name__}: {exc}"[:4000]
            with lock:
                counter["errors"] += 1
                write(record)
            raise
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        record["response"] = {"content": to_jsonable(response.content),
                              "stop_reason": response.stop_reason,
                              "usage": to_jsonable(response.usage)}
        with lock:
            write(record)
        return response

    sdk_messages.Messages.create = create
    counter["_handle"] = handle
    return counter


def xz(path: Path) -> Path:
    target = path.with_name(path.name + ".xz")
    with path.open("rb") as src, lzma.open(target, "wb", preset=9 | lzma.PRESET_EXTREME) as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return target


def finalize(run_dir: Path, label: str, meta: dict, seed: str | None = None,
             sandbox: Path | None = None, writable: bool = False) -> dict:
    """Compress the run's JSONL files, write the MODIFY diff, the meta and the done marker."""
    for path in sorted(run_dir.glob("*.jsonl")):
        xz(path)
    diff_name = None
    if writable and seed and sandbox is not None and sandbox.exists():
        diff = subprocess.run(["diff", "-ruN", *DIFF_EXCLUDES, str(Path(seed)), str(sandbox)],
                              capture_output=True, text=True)
        diff_name = f"{label}.diff"
        (run_dir / diff_name).write_text(diff.stdout, encoding="utf-8")
    meta = {**meta, "files": sorted(p.name for p in run_dir.glob("*.jsonl.xz")), "diff": diff_name,
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (run_dir / f"{label}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_dir / f"{label}.done").write_text(meta["status"] + "\n", encoding="utf-8")
    (run_dir / f"{label}.failed").unlink(missing_ok=True)
    return meta


def count_calls(run_dir: Path, label: str) -> dict:
    """Call counts from a run's (plain or compressed) capture, for salvaged runs."""
    path = run_dir / f"{label}.requests.jsonl"
    if not path.exists():
        path = run_dir / f"{label}.requests.jsonl.xz"
    if not path.exists():
        return {"calls": 0, "lead_calls": 0, "errors": 0}
    opener = lzma.open if path.suffix == ".xz" else open
    calls = lead = errors = 0
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:        # a line cut by the kill
                continue
            calls += 1
            lead += record.get("purpose") == "lead" and "response" in record
            errors += "error" in record
    return {"calls": calls, "lead_calls": lead, "errors": errors}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--seed", required=True, help="codebase to copy into the sandbox")
    ap.add_argument("--sandbox", required=True, help="sandbox path relative to the lane root")
    ap.add_argument("--writable", action="store_true", help="MODIFY run: diff the sandbox afterwards")
    ap.add_argument("profile_args", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    passthrough = [a for a in args.profile_args if a != "--"] if args.profile_args else []

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    sandbox = (REPO / args.sandbox).resolve()
    assert sandbox.is_relative_to(REPO / "profiling_sandbox"), sandbox
    if sandbox.exists():
        shutil.rmtree(sandbox)
    shutil.copytree(args.seed, sandbox, ignore=SEED_IGNORE)

    for stale in list(run_dir.glob("*.jsonl")) + list(run_dir.glob("*.jsonl.xz")):
        stale.unlink()                      # a retried run starts from a clean capture
    requests_path = run_dir / f"{args.label}.requests.jsonl"
    max_seconds = float(passthrough[passthrough.index("--max-seconds") + 1]) \
        if "--max-seconds" in passthrough else None
    counter = install_capture(requests_path, time.monotonic() + max_seconds if max_seconds else None)
    started = time.time()
    status, exit_code = "ok", None

    import profile_run  # research/common, via _paths (the lane's own copy)

    sys.argv = ["profile_run.py", *passthrough]
    try:
        exit_code = profile_run.main()
    except SystemExit as exc:
        exit_code = exc.code
        status = "exit"
    except Exception as exc:  # record and keep the capture
        status = f"error: {type(exc).__name__}: {exc}"[:500]
    finally:
        counter["_handle"].close()
    if status == "ok" and counter["deadline_hit"]:
        status = "deadline"
    meta = {"label": args.label, "status": status, "exit_code": exit_code,
            "wall_s": round(time.time() - started, 1), "calls": counter["calls"],
            "lead_calls": counter["lead"], "errors": counter["errors"], "lane": str(REPO),
            "seed": args.seed, "sandbox": args.sandbox, "profile_args": passthrough,
            "host": os.uname().nodename}
    meta = finalize(run_dir, args.label, meta, args.seed, sandbox, args.writable)
    shutil.rmtree(sandbox, ignore_errors=True)
    print(json.dumps({k: meta[k] for k in ("label", "status", "wall_s", "calls", "lead_calls")}))
    sys.exit(0 if status in ("ok", "deadline") else 1)


if __name__ == "__main__":
    main()
