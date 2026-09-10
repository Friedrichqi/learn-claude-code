#!/usr/bin/env python3
"""Run the byte-level input-redundancy workloads (lead + 3 teammates each) one after another.

    python3 s15_integrated_harness/scripts/redundancy_workloads.py --rep r1 [--only PF-S,DC-S] [--max-seconds 1200]

Each workload is one non-interactive s15 session driven by scripts/profile_run.py with the
content sidecar enabled (<trace>.reads.jsonl) and full tool outputs in the trace.  Two axes:

  prefill-heavy  (PF): teammates must read large files completely and answer in <= 15 lines
  decode-heavy   (DC): teammates read one small file and write >= 1,200-word documents

  shared   (-S): the three tasks touch the same files
  partial  (-P): each task has a private file plus (PF) one shared file / (DC) none shared

Traces land in s15_integrated_harness/traces/redundancy_profiling/ together with a console log.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "s15_integrated_harness" / "traces" / "redundancy_profiling"

TEAM = ("Delegate each item to its own teammate. I confirm this team now: spawn the three teammates "
        "immediately without asking for confirmation. ")
SHUTDOWN = "then request shutdown of all three teammates. Do not create or modify any files."

WORKLOADS = {
    # prefill-heavy, shared corpus: three audits of the same two source files
    "PF-S": (
        "Create three independent task-board items, all about the two harness source files "
        "s15_integrated_harness/code.py and s15_integrated_harness/trace_runtime.py: "
        "(1) list every place where an exception is caught and then swallowed without being re-raised "
        "or reported, with line numbers; (2) list every module-level mutable object that is accessed "
        "from more than one thread and say whether a lock protects it, with line numbers; (3) list every "
        "code path that can drop, truncate or replace a tool result before it reaches the model, with "
        "line numbers. Each task description must tell the teammate to read both files completely before "
        "answering (paging with read_file is fine; grep alone is not enough) and to keep its final report "
        "under 15 lines with no code excerpts. " + TEAM +
        "When all three results have arrived, combine them into one summary of at most 20 lines, " + SHUTDOWN
    ),
    # prefill-heavy, partially shared corpus: private chapter file + shared s15 code.py
    "PF-P": (
        "Create three independent task-board items: (1) compare s08_context_compact/code.py with "
        "s15_integrated_harness/code.py and list which functions of the chapter were carried into s15 "
        "unchanged, which were changed, and which were dropped; (2) the same comparison for "
        "s10_task_system/code.py against s15_integrated_harness/code.py; (3) the same comparison for "
        "s13_agent_teams/code.py against s15_integrated_harness/code.py. Each task description must tell "
        "the teammate to read its chapter file and s15_integrated_harness/code.py completely before "
        "answering (paging with read_file is fine; grep alone is not enough) and to keep its final report "
        "under 15 lines with no code excerpts. " + TEAM +
        "When all three results have arrived, combine them into one summary of at most 20 lines, " + SHUTDOWN
    ),
    # decode-heavy, shared small input: three long documents from one 13 KB glossary
    "DC-S": (
        "Create three independent task-board items that all start from the specification file "
        "s15_integrated_harness/GLOSSARY.md: (1) write a complete beginner tutorial of at least 1,200 "
        "words that teaches every term in the glossary with a worked example for each; (2) write a "
        "detailed test plan with at least 40 numbered test cases (steps and expected result each) "
        "covering the runtime behaviours the glossary describes; (3) write a design critique of at least "
        "1,200 words proposing improvements to the mechanisms the glossary describes. Each task "
        "description must tell the teammate to base its work on the glossary (read it once; other files "
        "only if a definition is unclear) and to deliver the whole document as its final text reply. "
        + TEAM +
        "When all three results have arrived, reply with a 5-line summary of what each delivered without "
        "repeating their documents, " + SHUTDOWN
    ),
    # decode-heavy, disjoint inputs: three long tutorials from three chapter READMEs
    "DC-P": (
        "Create three independent task-board items: write a complete beginner tutorial of at least 1,200 "
        "words for (1) chapter s06_subagent, (2) chapter s10_task_system, (3) chapter s13_agent_teams, "
        "each based on that chapter's README.md (read the chapter's code.py only where the README leaves "
        "something unclear). Each task description must tell the teammate to deliver the whole tutorial "
        "as its final text reply. " + TEAM +
        "When all three results have arrived, reply with a 5-line summary of what each delivered without "
        "repeating their tutorials, " + SHUTDOWN
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rep", required=True, help="repetition tag appended to the label, e.g. r1")
    parser.add_argument("--only", default=None, help="comma-separated workload ids (default: all four)")
    parser.add_argument("--max-seconds", type=float, default=1200.0)
    parser.add_argument("--quiet-seconds", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=20.0, help="seconds between runs (lets provider limits settle)")
    args = parser.parse_args()
    ids = args.only.split(",") if args.only else list(WORKLOADS)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    for wid in ids:
        label = f"{wid}-{args.rep}"
        log = TRACE_DIR / f"{label}.console.log"
        cmd = [sys.executable, str(REPO / "s15_integrated_harness" / "scripts" / "profile_run.py"),
               "--label", label, "--trace-output", "full", "--trace-dir", str(TRACE_DIR),
               "--max-seconds", str(args.max_seconds), "--quiet-seconds", str(args.quiet_seconds),
               "--prompt", WORKLOADS[wid]]
        print(f"[workloads] {time.strftime('%H:%M:%S')} start {label} (log {log})", flush=True)
        with log.open("w", encoding="utf-8") as handle:
            code = subprocess.run(cmd, cwd=REPO, stdout=handle, stderr=subprocess.STDOUT).returncode
        print(f"[workloads] {time.strftime('%H:%M:%S')} end {label} exit={code}", flush=True)
        time.sleep(args.pause)
    return 0


if __name__ == "__main__":
    sys.exit(main())
