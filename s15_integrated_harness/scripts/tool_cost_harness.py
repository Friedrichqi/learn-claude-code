#!/usr/bin/env python3
"""Stage 3: the advertised-cost intervention inside the real s15 harness.

    python3 s15_integrated_harness/scripts/tool_cost_harness.py --reps 4
    python3 s15_integrated_harness/scripts/tool_cost_harness.py --analyze-only

Stages 1 and 2 use purpose-built tool pools.  This one changes nothing about s15 except the
sentence appended to each tool description by `profile_run.py --tool-cost`, so the lead keeps its
full pool (bash, read_file, write_file, edit_file, glob, todo_write, task, load_skill, compact, the
task board, cron, teammates, MCP) and every other route stays open.  The question is whether an
agent that has `bash` -- and can therefore run `grep` itself -- reroutes around a read_file that is
advertised as expensive.

Arms: `plain` (no annotation) and `advertise` (read_file at 5.00 s, every other tool at 0.05 s).
Runs are sequential: profile_run.py wipes .memory/.tasks/.transcripts/.task_outputs at the
repository root before each session, so two sessions cannot share a checkout.

Scored from the trace (`tool_start` events give the tool and its arguments) and from the console
log (the lead's visible answer), against the three questions in PROMPT.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "s15_integrated_harness" / "scripts" / "profile_run.py"
TRACE_DIR = REPO / "s15_integrated_harness" / "traces" / "tool_cost" / "harness"

# Two workloads, because the first one turned out to have a floor.  `constants` asks for specific
# values, which the lead answers with `bash` + grep in every arm, so there is no read_file for the
# intervention to remove.  `summary` asks for the content of three glossary entries, which is the
# shape that made the lead read whole files in the 2026-09-12 latency runs.
WORKLOADS = {
    "constants": {
        "prompt": (
            "Answer these three questions about this repository. Do the work yourself; do not "
            "delegate and do not spawn teammates.\n"
            "1. In s15_integrated_harness/GLOSSARY.md, the Compaction entry names the stages of "
            "the context-shrinking pipeline. List every stage name, in order.\n"
            "2. In s15_integrated_harness/code.py, give the values of the module constants "
            "PERSIST_THRESHOLD, KEEP_RECENT_TOOL_RESULTS and CONTEXT_TOKEN_LIMIT.\n"
            "3. In s15_integrated_harness/GLOSSARY.md, the Error recovery entry gives the "
            "exponential backoff base delay and the value it is capped at. Give both.\n"
            "Answer all three in at most 15 lines."),
        # Graded with word-boundary regexes on the ANSI-stripped console log, after thousands
        # separators are removed, because the log also carries the harness's own numbers (char
        # counts, retry sizes) and a bare substring test would match "500" inside "1500".
        "expected": {
            "q1_tool_result_budget": r"tool_result_budget",
            "q1_snip_compact": r"snip_compact",
            "q1_micro_compact": r"micro_compact",
            "q1_fit_tool_results": r"fit_tool_results",
            "q1_compact_history": r"compact_history",
            "q2_persist_threshold": r"persist_threshold\W{0,40}\b30000\b",
            "q2_keep_recent": r"keep_recent_tool_results\W{0,40}\b3\b",
            "q2_context_limit": r"context_token_limit\W{0,40}\b128000\b",
            "q3_base_delay": r"\b500\s*ms\b",
            "q3_cap": r"\b32\s*s(?:ec|econds)?\b",
        },
    },
    "summary": {
        "prompt": (
            "Using s15_integrated_harness/GLOSSARY.md, explain in your own words what the glossary "
            "says about (a) the compaction pipeline, (b) the plan gate and (c) error recovery. "
            "Cover the stages, the triggers, what is blocked and what is retried. Do the work "
            "yourself; do not delegate and do not spawn teammates. At most 18 lines."),
        "expected": {
            "c_tool_result_budget": r"tool_result_budget",
            "c_snip_compact": r"snip_compact",
            "c_micro_compact": r"micro_compact",
            "c_fit_tool_results": r"fit_tool_results",
            "c_compact_history": r"compact_history",
            "p_bash": r"\bbash\b",
            "p_write_file": r"write_file",
            "p_edit_file": r"edit_file",
            "p_rearm": r"advance_assignment_version|re-?arm",
            "e_attempts": r"\b3\s*attempts\b|up to 3",
            "e_base": r"\b500\s*ms\b",
            "e_cap": r"\b32\s*s(?:ec|econds)?\b",
            "e_fallback": r"fallback_model_id|fallback model",
        },
    },
}

ARMS = {
    "plain": [],
    "advertise": ["--tool-cost", "read_file=5.0", "--tool-cost-default", "0.05"],
    # stage-1 probe D says exposing a cost and asking the agent to act on it are different
    # interventions, so the real harness gets both arms
    "policy": ["--tool-cost", "read_file=5.0", "--tool-cost-default", "0.05",
               "--tool-cost-policy"],
}

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run_arm(arm: str, rep: int, max_seconds: float, workload: str) -> Path:
    label = f"{workload}-{arm}-r{rep}"
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    console = TRACE_DIR / f"{label}.console.log"
    cmd = [sys.executable, str(DRIVER), "--label", label, "--prompt",
           WORKLOADS[workload]["prompt"],
           "--trace-dir", str(TRACE_DIR), "--max-seconds", str(max_seconds),
           "--quiet-seconds", "10", "--stream"] + ARMS[arm]
    started = time.time()
    with console.open("w", encoding="utf-8") as handle:
        subprocess.run(cmd, cwd=REPO, stdout=handle, stderr=subprocess.STDOUT, check=False)
    print(f"  {label}: {time.time() - started:.0f} s -> {console}", flush=True)
    return console


def trace_for(label: str) -> Path | None:
    for path in sorted(TRACE_DIR.glob("run_*.jsonl")):
        if ".inputs." in path.name or ".reads." in path.name:
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if event.get("event") == "profile_meta":
                    if event["data"].get("label") == label:
                        return path
                    break
    return None


def score_console(path: Path, workload: str) -> dict:
    text = ANSI.sub("", path.read_text(encoding="utf-8", errors="replace")).lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)      # 30,000 and 30000 are the same number
    expected = WORKLOADS[workload]["expected"]
    hits = {key: bool(re.search(pattern, text)) for key, pattern in expected.items()}
    return {"hits": hits, "coverage": round(sum(hits.values()) / len(hits), 3)}


def analyse(label: str, arm: str, rep: int, workload: str) -> dict | None:
    trace = trace_for(label)
    if trace is None:
        return None
    tools: Counter = Counter()
    read_paths: list[str] = []
    bash_commands: list[str] = []
    grep_like = 0
    result_chars: Counter = Counter()
    model_calls = 0
    meta = {}
    elapsed = 0.0
    pending: dict[str, str] = {}
    for line in trace.open(encoding="utf-8"):
        event = json.loads(line)
        kind, data = event.get("event"), event.get("data") or {}
        elapsed = max(elapsed, event.get("elapsed_ms") or 0)
        if kind == "profile_meta":
            meta = data
        elif kind == "model_request":
            model_calls += 1
        elif kind == "tool_start":
            name = data.get("tool")
            tools[name] += 1
            pending[data.get("tool_call_id")] = name
            args = data.get("arguments") or {}
            if name == "read_file":
                read_paths.append(str(args.get("path")))
            elif name == "bash":
                command = str(args.get("command", ""))
                bash_commands.append(command[:200])
                if re.search(r"\b(grep|rg|sed|awk|head|tail)\b", command):
                    grep_like += 1
        elif kind == "tool_end":
            name = pending.pop(data.get("tool_call_id"), None)
            chars = ((data.get("result") or {}).get("characters")
                     if isinstance(data.get("result"), dict) else None)
            if name and isinstance(chars, int):
                result_chars[name] += chars
    console = TRACE_DIR / f"{label}.console.log"
    score = (score_console(console, workload) if console.exists()
             else {"coverage": None, "hits": {}})
    return {
        "label": label, "arm": arm, "rep": rep, "workload": workload, "trace": trace.name,
        "tool_cost": meta.get("tool_cost"), "tool_cost_default": meta.get("tool_cost_default"),
        "model_calls": model_calls, "wall_s": round(elapsed / 1000, 1),
        "tools": dict(tools), "n_read_file": tools.get("read_file", 0),
        "n_bash": tools.get("bash", 0), "n_glob": tools.get("glob", 0),
        "grep_like_bash": grep_like,
        "read_chars": result_chars.get("read_file", 0), "bash_chars": result_chars.get("bash", 0),
        "total_tool_chars": sum(result_chars.values()),
        "read_paths": read_paths, "bash_commands": bash_commands,
        "coverage": score["coverage"], "hits": score["hits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=4)
    parser.add_argument("--arms", default="plain,advertise,policy")
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--workload", default="constants", choices=sorted(WORKLOADS))
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    arms = args.arms.split(",")
    out_path = Path(args.out or TRACE_DIR / f"harness_runs_{args.workload}.json")
    if not args.analyze_only:
        for rep in range(args.reps):
            for arm in arms:                      # interleave so provider drift hits both arms
                run_arm(arm, rep, args.max_seconds, args.workload)

    rows = []
    for rep in range(args.reps):
        for arm in arms:
            # runs made before the workloads were split carry an unprefixed label
            row = (analyse(f"{args.workload}-{arm}-r{rep}", arm, rep, args.workload)
                   or analyse(f"{arm}-r{rep}", arm, rep, args.workload))
            if row:
                rows.append(row)
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} runs)")

    print(f"\n{'arm':<10} {'n':>3} {'rounds':>7} {'read_file':>10} {'bash':>6} "
          f"{'grep-ish':>9} {'read KB':>8} {'tool KB':>8} {'wall s':>7} {'coverage':>9}")
    for arm in arms:
        cell = [r for r in rows if r["arm"] == arm]
        if not cell:
            continue
        avg = lambda key: sum(r[key] for r in cell) / len(cell)            # noqa: E731
        print(f"{arm:<10} {len(cell):>3} {avg('model_calls'):>7.1f} {avg('n_read_file'):>10.2f} "
              f"{avg('n_bash'):>6.2f} {avg('grep_like_bash'):>9.2f} "
              f"{avg('read_chars') / 1024:>8.1f} {avg('total_tool_chars') / 1024:>8.1f} "
              f"{avg('wall_s'):>7.1f} "
              f"{sum(r['coverage'] or 0 for r in cell) / len(cell):>9.2f}")


if __name__ == "__main__":
    main()
