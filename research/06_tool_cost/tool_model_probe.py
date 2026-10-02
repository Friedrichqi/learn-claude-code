#!/usr/bin/env python3
"""Time the tools whose price is a model's work, against a REAL model (research/06_tool_cost Part C, C3).

    python3 research/06_tool_cost/tool_model_probe.py --label L [--task-reps 10] [--workflow-reps 5]
        [--compact-reps 10] [--only task,workflow,compact] [--trace-dir DIR] [--workflow-nothink]
        [--seed 20260930]

Part A priced `task` with the client disabled (0.9 ms of dispatch), `compact` as its marker (0.2 ms)
and never ran s16's `Workflow`.  Their real cost is model calls:

  task      one synchronous one-shot subagent loop (code.py spawn_subagent: up to 30 rounds of
            max_tokens 8000 over the five-tool subagent pool) -- three prompt classes: a document
            summary (read_file), a multi-file search (glob + bash grep) and a small coding task that
            iterates on a unittest file in a sandbox copy of a latency_bench problem;
  Workflow  s16's saved `review-changes` workflow over a frozen diff: 4 audit agents, then one
            verifier per finding, at most 8 in flight (s16 CONCURRENCY), each max_tokens 2000;
  compact   the summarisation call that follows the compact marker (code.py summarize_history:
            the history's JSON capped at 80,000 chars, max_tokens 2000), on synthetic histories of
            20k / 50k / 80k chars built from the frozen s15 documents.

Every dispatch goes through the lead loop's own path (tool_latency_probe.Probe.lead: span +
PreToolUse hooks + call_tool_handler + PostToolUse), one at a time, so every model call and nested
tool span inside a tool span's interval belongs to that call; tool_share_analyze.py splits each span
into nested model time (an interval union for the concurrent workflow agents), nested tool time and
the rest.  Prompts start with a per-rep nonce, so repeated reps do not ride on each other's prefix
cache beyond the shared system prompt and tool list (as distinct real tasks would).

Runs from a lane (REPO = parents[2]; chdir + run-state wipe like tool_latency_probe.py) whose .env
points at the server.  --workflow-nothink sends chat_template_kwargs.enable_thinking=false on the
workflow agents' calls only: a thinking model otherwise spends the 2000-token budget before the JSON
(the harness as shipped has no effort knob for purpose workflow_agent).
Output: <trace-dir>/run_*.jsonl (the s15 trace) + run_*.records.jsonl, one record per timed call
{case, cls, rep, tool, status, duration_ms, output_chars, t0_ns, t1_ns, note}.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401
from profile_run import is_mutating                                          # noqa: E402
from tool_latency_probe import Probe, load_harness, wipe_run_state           # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SANDBOX = "profiling_sandbox/toolmodel"
BENCH = REPO / "research" / "common" / "fixtures" / "latency_bench" / "coding"
DIFF = REPO / "research" / "common" / "fixtures" / "tool_heavy" / "review_changes.diff"
DOCS = [REPO / "s15_integrated_harness" / n for n in ("ARCHITECTURE.md", "DESIGN.md", "GLOSSARY.md")]

TASK_PROMPTS = {
    "doc": ("Read s15_integrated_harness/GLOSSARY.md with read_file and summarise what it says about "
            "'Compaction' and 'Task board' in at most 8 lines. Do not modify any files."),
    "search": ("Find every lesson directory s01_* ... s17_* whose code.py defines a tool named \"glob\" in "
               "its tool list. Use the glob tool and read-only bash grep commands. Reply with the "
               "directory names and the line numbers, at most 10 lines. Do not modify any files."),
    "code": (f"Read {SANDBOX}/intervals/README.md, then implement the specification in a new file "
             f"{SANDBOX}/intervals/solution.py (create it with write_file, fix it with edit_file). Run "
             f"`python3 {SANDBOX}/intervals/test_intervals.py` and iterate until every test passes. Do "
             "not modify the README or the test file. Final reply: at most 5 lines with the final "
             "unittest result line."),
}
COMPACT_SIZES = [20_000, 50_000, 80_000]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    parser.add_argument("--task-reps", type=int, default=10, help="reps per task prompt class")
    parser.add_argument("--workflow-reps", type=int, default=5)
    parser.add_argument("--compact-reps", type=int, default=10, help="reps per history size")
    parser.add_argument("--only", default="task,workflow,compact")
    parser.add_argument("--trace-dir", default=str(REPO / "research" / "06_tool_cost" / "data" / "tool_model"))
    parser.add_argument("--workflow-nothink", action="store_true",
                        help="enable_thinking=false on workflow_agent calls (thinking servers)")
    parser.add_argument("--seed", type=int, default=20260930)
    return parser.parse_args()


def load_s16():
    path = REPO / "s16_workflow_runtime" / "code.py"
    spec = importlib.util.spec_from_file_location("s16_toolmodel_runtime", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["s16_toolmodel_runtime"] = module
    spec.loader.exec_module(module)
    return module


class PurposeNoThink:
    """Wraps the raw Messages API under the traced client: calls whose trace purpose is in
    `purposes` get chat_template_kwargs.enable_thinking=false."""

    def __init__(self, raw, trace, purposes):
        self._raw, self._trace, self._purposes = raw, trace, set(purposes)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def create(self, **kwargs):
        if self._trace.model_purpose() in self._purposes:
            extra = dict(kwargs.get("extra_body") or {})
            template = dict(extra.get("chat_template_kwargs") or {})
            template["enable_thinking"] = False
            extra["chat_template_kwargs"] = template
            kwargs = dict(kwargs, extra_body=extra)
        return self._raw.create(**kwargs)


def synthetic_history(target_chars: int, nonce: str) -> list[dict]:
    """A plausible lead history (user request, assistant tool calls, tool results with document text)
    whose json.dumps is about `target_chars` long."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in DOCS)
    messages = [{"role": "user", "content": f"[{nonce}] Explain how the s15 harness compacts context and "
                                            "how teammates share the task board."}]
    offset, n = 0, 0
    while len(json.dumps(messages, default=str)) < target_chars:
        n += 1
        chunk = text[offset:offset + 6000] or text[:6000]
        offset = (offset + 6000) % max(1, len(text) - 6000)
        use_id = f"toolu_synth_{n:03d}"
        messages.append({"role": "assistant", "content": [
            {"type": "text", "text": f"Reading part {n} of the architecture notes."},
            {"type": "tool_use", "id": use_id, "name": "read_file",
             "input": {"path": DOCS[n % len(DOCS)].relative_to(REPO).as_posix(), "offset": n * 100, "limit": 100}}]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": chunk}]})
    return messages


def main() -> int:
    args = parse_args()
    os.chdir(REPO)
    os.environ["HARNESS_TRACE"] = "1"
    os.environ["HARNESS_TRACE_DIR"] = args.trace_dir
    os.environ["HARNESS_TRACE_OUTPUT"] = "summary"
    wipe_run_state()
    runtime = REPO / "s16_workflow_runtime" / ".runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    write_root = REPO / SANDBOX
    if write_root.exists():
        shutil.rmtree(write_root)
    write_root.mkdir(parents=True)

    mod = load_harness()
    mod.CLI_ACTIVE = False
    trace = mod.initialize_tracing("s16")
    s16 = load_s16()
    s16.install_workflow_tool(mod)
    if args.workflow_nothink:
        traced = mod.client.messages
        traced._raw_messages = PurposeNoThink(traced._raw_messages, mod.TRACE, {"workflow_agent"})

    # the profiling driver's policy: mutating bash denied, writes confined to the sandbox,
    # create_worktree denied; the harness hook beneath auto-approves main-thread asks
    original_permission = mod.permission_hook

    def guarded_permission(block):
        if block.name == "bash":
            command = block.input.get("command", "")
            if isinstance(command, str) and is_mutating(command):
                return ("Permission denied by user: this profiling session is read-only; "
                        "do not modify files or git state.")
        if block.name in {"write_file", "edit_file"}:
            try:
                resolved = (REPO / str(block.input.get("path", ""))).resolve()
            except Exception:
                resolved = None
            if resolved is None or not resolved.is_relative_to(write_root):
                return ("Permission denied by user: in this session files may only be written "
                        f"below {SANDBOX}/.")
        if block.name == "create_worktree":
            return "Permission denied by user: do not create worktrees in this session."
        return original_permission(block)

    hooks = mod.HOOKS["PreToolUse"]
    hooks[hooks.index(original_permission)] = guarded_permission
    mod.CONSOLE.reader = lambda prompt: "y"

    records_path = Path(str(trace.path).removesuffix(".jsonl") + ".records.jsonl")
    trace.emit("probe_meta", {"label": args.label, "driver": "research/06_tool_cost/tool_model_probe.py",
                              "task_reps": args.task_reps, "workflow_reps": args.workflow_reps,
                              "compact_reps": args.compact_reps, "workflow_nothink": args.workflow_nothink,
                              "model": mod.MODEL, "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
                              "seed": args.seed, "sandbox": SANDBOX})
    print(f"[model-probe] label={args.label} model={mod.MODEL} trace={trace.path}", flush=True)

    probe = Probe(mod, trace, records_path, write_root)
    _, pool_handlers = mod.assemble_tool_pool()
    probe.handlers = dict(pool_handlers)
    assert "Workflow" in probe.handlers, "install_workflow_tool did not add Workflow"
    diff_text = DIFF.read_text(encoding="utf-8")

    only = set(args.only.split(","))
    schedule = []
    if "task" in only:
        schedule += [("task", cls, rep) for cls in TASK_PROMPTS for rep in range(args.task_reps)]
    if "workflow" in only:
        schedule += [("workflow", "review-changes", rep) for rep in range(args.workflow_reps)]
    if "compact" in only:
        schedule += [("compact", str(size), rep) for size in COMPACT_SIZES for rep in range(args.compact_reps)]
    random.Random(args.seed).shuffle(schedule)

    records = []

    def record(case, cls, rep, tool, status, t0, t1, output, note=None):
        rec = {"case": case, "cls": cls, "rep": rep, "tool": tool, "status": status,
               "duration_ms": round((t1 - t0) / 1e6, 3), "output_chars": len(str(output)),
               "t0_ns": t0, "t1_ns": t1, "note": note}
        records.append(rec)
        with records_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec) + "\n")
        print(f"[model-probe] {case}:{cls} rep {rep} {status} {rec['duration_ms'] / 1000:.1f}s", flush=True)

    for i, (case, cls, rep) in enumerate(schedule, 1):
        nonce = f"probe {args.label} {case}-{cls}-{rep}"
        t0 = time.perf_counter_ns()
        try:
            if case == "task":
                if cls == "code":
                    target = write_root / "intervals"
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(BENCH / "intervals", target,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "solution.py"))
                t0 = time.perf_counter_ns()
                output, _, status = probe.lead("task", {"description": f"[{nonce}] {TASK_PROMPTS[cls]}"})
                t1 = time.perf_counter_ns()
                note = None
                if cls == "code":
                    proc = subprocess.run([sys.executable, str(write_root / "intervals" / "test_intervals.py")],
                                          capture_output=True, text=True, timeout=120,
                                          cwd=str(write_root / "intervals"))
                    note = "tests_pass" if proc.returncode == 0 else "tests_fail"
                record(case, cls, rep, "task", status, t0, t1, output, note)
            elif case == "workflow":
                t0 = time.perf_counter_ns()
                output, _, status = probe.lead("Workflow", {"name": "review-changes",
                                                            "args": {"changes": f"# {nonce}\n{diff_text}"}})
                t1 = time.perf_counter_ns()
                try:
                    wf_status = json.loads(output)["task"]["status"]
                except Exception:
                    wf_status = "unparsed"
                if status == "ok" and wf_status != "completed":
                    status = "error"
                record(case, cls, rep, "Workflow", status, t0, t1, output, f"workflow_status={wf_status}")
            else:
                history = synthetic_history(int(cls), nonce)
                t0 = time.perf_counter_ns()
                with mod.TRACE.agent_scope("agent-root", None, "lead"):
                    with mod.TRACE.span("compact_summary_start", "compact_summary_end",
                                        {"history_chars": len(json.dumps(history, default=str))}) as span:
                        summary = mod.summarize_history(history)
                        span.finish(status="ok", summary_chars=len(summary))
                t1 = time.perf_counter_ns()
                record(case, cls, rep, "compact_summary", "ok", t0, t1, summary,
                       f"history_chars={len(json.dumps(history, default=str))}")
        except Exception as exc:
            t1 = time.perf_counter_ns()
            record(case, cls, rep, case, "case_error", t0, t1, "", f"{type(exc).__name__}: {exc}")
        print(f"[model-probe] {i}/{len(schedule)} done", flush=True)

    trace.emit("probe_end", {"label": args.label, "calls": len(records)})
    mod.close_tracing("completed")
    print(f"[model-probe] records={records_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
