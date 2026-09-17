#!/usr/bin/env python3
"""Stage 2 of the context-handoff experiment: the same arms inside the real s15 team harness.

    python3 s15_integrated_harness/scripts/prewarm_harness.py --reps 3 --only W2 \
        --arms baseline,dag,pollute,summary

Stage 1 (prewarm_loop.py) measures rounds on a scripted single agent.  This stage puts the
injection where it would really live: a teammate that has just claimed a task whose predecessor was
finished by ANOTHER agent gets that predecessor's file content prepended to its history as a
synthetic read_file tool_use/tool_result pair, and the whole run is measured end to end.

Arms map onto profile_run.py's --prewarm:

  baseline   nothing injected (these runs are also the DAG-redundancy experiment's data)
  dag        the direct predecessors' reads, captured LIVE in this same run -- the deployable policy
  oracle     the successor's own eventual read set, frozen from a matched baseline run's dump
  pollute    an equal-size window of an unrelated file (negative control)
  summary    the predecessors' result text instead of the bytes

Arms are interleaved inside a repetition and the schedule is shuffled with a fixed seed, so
provider drift cannot line up with an arm.  --topup re-runs only the cells that have no trace yet.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dag_workloads import dag_summary, trace_for  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "s15_integrated_harness" / "traces" / "prewarm" / "harness"
BASE_DIR = REPO / "s15_integrated_harness" / "traces" / "dag_redundancy"
ARMS = ["baseline", "dag", "oracle", "pollute", "summary"]
ARM_FLAGS = {
    "baseline": ["--prewarm", "none"],
    "dag": ["--prewarm", "dag"],
    "oracle": ["--prewarm", "oracle"],
    "pollute": ["--prewarm", "pollute"],
    "summary": ["--prewarm", "summary"],
}


def spec_for(workload: str, rep: str) -> Path | None:
    """The frozen read sets of the matched baseline run (written by --prewarm-dump)."""
    for candidate in sorted(BASE_DIR.glob(f"{workload}-L2-{rep}.prewarm.json")):
        return candidate
    for candidate in sorted(BASE_DIR.glob(f"{workload}-L2-*.prewarm.json")):
        return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--only", default="W1,W2")
    parser.add_argument("--arms", default="baseline,dag,pollute,summary")
    parser.add_argument("--level", default="L2")
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--pause", type=float, default=15.0)
    parser.add_argument("--trace-dir", default=str(TRACE_DIR))
    parser.add_argument("--topup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    trace_dir = Path(args.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    workloads = args.only.split(",")
    arms = args.arms.split(",")
    cells = [(workload, arm, f"r{rep + 1}")
             for rep in range(args.reps) for workload in workloads for arm in arms]
    # shuffle inside each repetition: arm order must not confound provider drift with the arm
    grouped: dict[str, list] = {}
    for cell in cells:
        grouped.setdefault(cell[2], []).append(cell)
    rng = random.Random(20260916)
    ordered = []
    for rep in sorted(grouped):
        block = grouped[rep]
        rng.shuffle(block)
        ordered.extend(block)

    rows = []
    for workload, arm, rep in ordered:
        label = f"{workload}-{arm}-{rep}"
        if args.topup and trace_for(label, trace_dir) is not None:
            print(f"[prewarm] skip {label} (already has a trace)", flush=True)
            continue
        extra = list(ARM_FLAGS[arm])
        if arm == "oracle":
            spec = spec_for(workload, rep)
            if spec is None:
                print(f"[prewarm] no baseline dump for {workload}; skipping oracle cell", flush=True)
                continue
            extra += ["--prewarm-spec", str(spec)]
        cmd = [sys.executable, str(Path(__file__).resolve().parent / "dag_workloads.py"),
               "--rep", rep, "--level", args.level, "--only", workload,
               "--max-seconds", str(args.max_seconds), "--pause", "0",
               "--trace-dir", str(trace_dir), f"--label-suffix=-{arm}",
               "--nonce", f"{workload}{arm}{rep}"]
        # argparse refuses a value that starts with "-" unless it is given as --opt=value
        for flag in extra:
            cmd.append(f"--extra={flag}")
        if args.dry_run:
            print(" ".join(cmd))
            continue
        print(f"[prewarm] {time.strftime('%H:%M:%S')} start {label}", flush=True)
        subprocess.run(cmd, cwd=REPO)
        trace = trace_for(f"{workload}-{args.level}-{arm}-{rep}", trace_dir)
        if trace is None:
            print(f"[prewarm] {label}: no trace", flush=True)
            continue
        summary = dag_summary(trace)
        summary.update({"label": label, "workload": workload, "arm": arm, "rep": rep})
        rows.append(summary)
        (trace_dir / f"{label}.cell.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[prewarm] {label}: edges={summary['edges']} executed={summary['executed_edges']} "
              f"wall={summary['wall_seconds']}s status={summary['status']}", flush=True)
        time.sleep(args.pause)
    if rows:
        print("\n| cell | edges | executed | cross-owner | wall s | status |")
        print("|---|---|---|---|---|---|")
        for row in rows:
            print(f"| {row['label']} | {row['edges']} | {row['executed_edges']} | "
                  f"{row['cross_owner_edges']} | {row['wall_seconds']} | {row['status']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
