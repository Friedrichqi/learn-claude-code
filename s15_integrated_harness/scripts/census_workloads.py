#!/usr/bin/env python3
"""Run the cross-category task-DAG census workloads.

    python3 s15_integrated_harness/scripts/census_workloads.py --reps 3
        [--only FQA,CODE] [--structures DEP,FLAT] [--provider-tag glm]
        [--repo LANE] [--trace-dir DIR] [--topup] [--dry-run] [--print-prompts] [--check-prompts]

THE QUESTION.  Does the Lead maintain a dependency graph on the task board, or does it leave
`blockedBy` empty and use the board as a flat list?  Every earlier experiment in this repository
either used workloads that were deliberately flat (so an empty graph was correct) or told the Lead
in so many words to call `update_task(addBlockedBy=...)` (so a non-empty graph was assured).
Neither can answer the question.

THE DESIGN.  Six task categories x two latent structures x N repetitions.

  DEP  -- the work genuinely has stages: item k cannot be done before item k-1 (or, for RESEARCH,
          a synthesis consumes three surveys).  A correct board HAS edges.
  FLAT -- the items are genuinely independent.  A correct board has NO edges.

FLAT is the specificity control.  Without it, zero edges on DEP cannot be told apart from a Lead
that simply never emits edges; with it, we can say whether the Lead is silent or merely discerning.

THE RULE THAT MAKES THIS A MEASUREMENT.  Edge emission is the dependent variable, so no prompt may
name the mechanism.  `check_prompts()` fails the run if any assembled prompt contains update_task,
addBlockedBy, "dependency", "blocked", "prerequisite" and friends.  Run --check-prompts before
spending a single API call.  The staged workloads state their order the way a person would ("using
the inventory produced by the previous item"); translating that narrative into board edges is
exactly what we are measuring, so it must never be requested.

The harness is NOT modified for this experiment, and the Lead's own system prompt already asks for
dependencies (code.py:892-896).  What is measured is whether it acts on that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "s15_integrated_harness" / "traces" / "dag_census"

CODE_SANDBOX = "profiling_sandbox/coding"
CODE_SEED = "s15_integrated_harness/scripts/latency_bench/coding"
RF_SANDBOX = "profiling_sandbox/refactor"
RF_SEED = "s15_integrated_harness/scripts/census_bench/refactor"

# -- the neutral frame -------------------------------------------------------------------------
# Names the board, never its shape: not how many items, not how they relate.  "claim the next
# available item" is plain work acquisition -- it lets a graph EXECUTE if the Lead builds one,
# without hinting that it should.
PREAMBLE = ("Put this work on the task board and delegate it to a team. I confirm the team now: "
            "spawn the teammates immediately without asking for confirmation. Do not create "
            "worktrees. Spawn exactly THREE teammates, no more, whatever the number of items. "
            "Each teammate must call complete_task as soon as its findings are written, and then "
            "claim the next available item on the board. ")
EPILOGUE = "When all the results have arrived, {synthesis}, then request shutdown of all teammates."
NOWRITE = " Do not create or modify any files."

FOLLOWUP = "Confirmed, proceed: spawn the teammates now."

# Anything here in an assembled prompt means the experiment is contaminated.  \b matters:
# "independently" must not trip the "depend" rule.
FORBIDDEN = re.compile(
    r"\b(update_task|addBlockedBy|blockedBy|blocked|blocker|blockers|depend|depends|depended|"
    r"dependent|dependency|dependencies|prerequisite|prerequisites|predecessor|predecessors|"
    r"successor|successors)\b", re.IGNORECASE)

# -- FQA ---------------------------------------------------------------------------------------
FQA_TAIL = " Answer in at most 15 lines and cite the file and line number for each claim."
FQA_DEP = [
    "from s15_integrated_harness/GLOSSARY.md, quote the definition of each of the three terms "
    "'Compaction', 'Hook' and 'Skill catalog', each with the line number where the definition "
    "begins" + FQA_TAIL,
    "using the three quoted definitions produced by the previous item, find where each of those "
    "three terms is described in s15_integrated_harness/ARCHITECTURE.md and cite the section "
    "heading and line number for each" + FQA_TAIL,
    "using the three locations produced by the previous item, write a note of at most 12 lines "
    "naming every place where ARCHITECTURE.md and the glossary wording disagree, or stating that "
    "they agree" + FQA_TAIL,
]
FQA_FLAT = [
    "using s15_integrated_harness/trace_runtime.py, describe what is written to the trace file for "
    "a single tool call" + FQA_TAIL,
    "using s15_integrated_harness/trace_view.py, describe how a recorded event becomes rendered "
    "output" + FQA_TAIL,
    "using s15_integrated_harness/trace_stats.py, describe how recorded events are aggregated into "
    "numbers" + FQA_TAIL,
]

# -- CODE --------------------------------------------------------------------------------------
CODE_DEP = [
    f"read {CODE_SANDBOX}/intervals/README.md and write {CODE_SANDBOX}/intervals/checklist.md: a "
    "numbered checklist of every behaviour the specification requires, one line per behaviour",
    f"using the checklist written by the previous item ({CODE_SANDBOX}/intervals/checklist.md), "
    f"implement {CODE_SANDBOX}/intervals/solution.py so that every line of that checklist is "
    "satisfied. Create it with write_file and correct it with edit_file",
    "using the implementation produced by the previous item, run the shell command "
    f"`python3 {CODE_SANDBOX}/intervals/test_intervals.py`, correct any failure in "
    f"{CODE_SANDBOX}/intervals/solution.py, and report the final unittest result line",
]
_CODE_ITEM = ("read {sb}/{p}/README.md, then implement the specification in a new file "
              "{sb}/{p}/solution.py (create it with write_file, correct it with edit_file). Run the "
              "tests with the shell command `python3 {sb}/{p}/test_{p}.py` and iterate until every "
              "test passes. Do not modify the README or the test file and do not write anywhere "
              "else. Report the final unittest result line")
CODE_FLAT = [_CODE_ITEM.format(sb=CODE_SANDBOX, p=p) for p in ("intervals", "ttl_cache", "expr")]

# -- MATH --------------------------------------------------------------------------------------
MATH_TAIL = (" Show the reasoning; you may check arithmetic with a `python3 -c` command. End your "
             "answer with a line 'Final answer: <integer>'.")
MATH_DEP = [
    "let S be the set of positive integers n with n <= 1000 that are divisible by neither 2 nor 5. "
    "Determine how many elements S has, and state the counting argument you used" + MATH_TAIL,
    "using the count and the counting argument produced by the previous item, determine the sum of "
    "all the elements of S" + MATH_TAIL,
    "using the sum produced by the previous item, find the remainder when that sum is divided by 7"
    + MATH_TAIL,
]
MATH_DEP_ANSWERS = [400, 200000, 3]
MATH_FLAT = [
    "let x, y and z all exceed 1 and let w be a positive number such that log_x(w) = 24, "
    "log_y(w) = 40 and log_(xyz)(w) = 12. Find log_z(w)" + MATH_TAIL,
    "what is the largest positive integer n for which n^3 + 100 is divisible by n + 10?" + MATH_TAIL,
    "find the largest possible value of k for which 3^11 is expressible as the sum of k "
    "consecutive positive integers" + MATH_TAIL,
]
MATH_FLAT_ANSWERS = [60, 890, 486]

# -- REFACTOR ----------------------------------------------------------------------------------
RF_DEP = [
    f"inventory every place in {RF_SANDBOX}/ where the function `normalize_key` appears -- its "
    "definition, every import of it, and every call of it -- listing file:line for each",
    "using the inventory produced by the previous item, rename `normalize_key` to `canonical_key` "
    "at its definition and at every place that inventory lists, editing the files in "
    f"{RF_SANDBOX}/ with edit_file",
    "using the renamed code produced by the previous item, run the shell command "
    f"`python3 {RF_SANDBOX}/test_pkg.py`, correct anything still failing in {RF_SANDBOX}/, and "
    "report the final unittest result line",
]
_RF_AUDIT = ("audit the error handling of s15_integrated_harness/{f}: list every place where a "
             "failure is caught, with line numbers")
RF_FLAT = [_RF_AUDIT.format(f=f) for f in ("trace_runtime.py", "trace_view.py", "trace_stats.py")]

# -- DATA --------------------------------------------------------------------------------------
_DATA_FILES = ("s15_integrated_harness/trace_runtime.py", "s15_integrated_harness/trace_view.py",
               "s15_integrated_harness/trace_stats.py")
DATA_DEP = [
    "for each of " + ", ".join(_DATA_FILES) + ", count the top-level function definitions and the "
    "class definitions, and report a three-row table carrying both counts per file",
    "using the three-row table produced by the previous item, compute the totals across the three "
    "files and the percentage each file contributes to the total function count",
    "using the totals produced by the previous item, rank the three files by function count and "
    "write a summary of at most 6 lines naming which file leads and by how much",
]
DATA_FLAT = ["count the top-level function definitions and the class definitions in " + f +
             ", reporting both numbers and the command you used" for f in _DATA_FILES]

# -- RESEARCH (fan-in) -------------------------------------------------------------------------
RESEARCH_DEP = [
    "survey s15_integrated_harness/trace_runtime.py and list the functions that decide what a "
    "recorded event contains, each with file:line",
    "survey s15_integrated_harness/trace_view.py and list the functions that decide how a recorded "
    "event is rendered, each with file:line",
    "survey s15_integrated_harness/trace_stats.py and list the functions that aggregate recorded "
    "events into numbers, each with file:line",
    "using the three surveys produced by the previous three items, name for each of those three "
    "modules the one function that would have to change first if a new field were added to every "
    "recorded event, citing file:line in each of the three files",
]
RESEARCH_FLAT = ["summarise s15_integrated_harness/" + f + " in at most 12 lines"
                 for f in ("GLOSSARY.md", "DESIGN.md", "CHANGELOG.md")]

CHAIN3 = [(1, 0), (2, 1)]           # item 1 after item 0, item 2 after item 1
FANIN3 = [(3, 0), (3, 1), (3, 2)]   # item 3 after each of 0,1,2

# reference_edges are 0-based (successor, predecessor) pairs: the board a correct Lead would build.
WORKLOADS = {
    ("FQA", "DEP"):       dict(items=FQA_DEP, shape="chain", edges=CHAIN3, ro=True,
                               synthesis="reply with the reconciliation note"),
    ("FQA", "FLAT"):      dict(items=FQA_FLAT, shape="flat", edges=[], ro=True,
                               synthesis="combine the three answers into one summary of at most 20 lines"),
    ("CODE", "DEP"):      dict(items=CODE_DEP, shape="chain", edges=CHAIN3, ro=False, py=True,
                               sandbox=(CODE_SANDBOX, CODE_SEED),
                               synthesis="report the final unittest result line"),
    ("CODE", "FLAT"):     dict(items=CODE_FLAT, shape="flat", edges=[], ro=False, py=True,
                               sandbox=(CODE_SANDBOX, CODE_SEED),
                               synthesis="report for each problem whether its tests passed"),
    ("MATH", "DEP"):      dict(items=MATH_DEP, shape="chain", edges=CHAIN3, ro=True, py=True,
                               answers=MATH_DEP_ANSWERS,
                               synthesis="reply with the final remainder"),
    ("MATH", "FLAT"):     dict(items=MATH_FLAT, shape="flat", edges=[], ro=True, py=True,
                               answers=MATH_FLAT_ANSWERS,
                               synthesis="reply with the three final answers, one per line"),
    ("REFACTOR", "DEP"):  dict(items=RF_DEP, shape="chain", edges=CHAIN3, ro=False, py=True,
                               sandbox=(RF_SANDBOX, RF_SEED),
                               synthesis="report the final unittest result line"),
    ("REFACTOR", "FLAT"): dict(items=RF_FLAT, shape="flat", edges=[], ro=True,
                               synthesis="combine the three audits into one list of at most 15 lines"),
    ("DATA", "DEP"):      dict(items=DATA_DEP, shape="chain", edges=CHAIN3, ro=True, py=True,
                               synthesis="reply with the ranking"),
    ("DATA", "FLAT"):     dict(items=DATA_FLAT, shape="flat", edges=[], ro=True, py=True,
                               synthesis="reply with the three pairs of counts, one file per line"),
    ("RESEARCH", "DEP"):  dict(items=RESEARCH_DEP, shape="fanin", edges=FANIN3, ro=True,
                               synthesis="reply with the three named functions"),
    ("RESEARCH", "FLAT"): dict(items=RESEARCH_FLAT, shape="flat", edges=[], ro=True,
                               synthesis="combine the three summaries into one of at most 20 lines"),
}

CATEGORIES = ["FQA", "CODE", "MATH", "REFACTOR", "DATA", "RESEARCH"]
STRUCTURES = ["DEP", "FLAT"]


def build_prompt(cat: str, struct: str, nonce: str = "x") -> str:
    """Assemble one run's user turn.  Items are numbered and nonce-stamped so that two runs of the
    same workload cannot serve each other's teammate prefix out of the provider's prefix cache."""
    w = WORKLOADS[(cat, struct)]
    body = " ".join(f"({i + 1}) [run {nonce}] {t}." for i, t in enumerate(w["items"]))
    prompt = body + " " + PREAMBLE + EPILOGUE.format(synthesis=w["synthesis"])
    if w.get("ro"):
        prompt += NOWRITE
    return prompt


def check_prompts(verbose: bool = True) -> int:
    """Fail loudly if any assembled prompt names the mechanism we are measuring."""
    bad = 0
    for cat in CATEGORIES:
        for struct in STRUCTURES:
            prompt = build_prompt(cat, struct, nonce="check")
            hits = sorted({m.group(0).lower() for m in FORBIDDEN.finditer(prompt)})
            if hits:
                bad += 1
                print(f"[census] CONTAMINATED {cat}-{struct}: {hits}", file=sys.stderr)
            elif verbose:
                print(f"[census] clean {cat}-{struct} ({len(prompt)} chars, "
                      f"{len(WORKLOADS[(cat, struct)]['items'])} items)")
    # the followup travels with every run, so it is held to the same rule
    hits = sorted({m.group(0).lower() for m in FORBIDDEN.finditer(FOLLOWUP)})
    if hits:
        bad += 1
        print(f"[census] CONTAMINATED followup: {hits}", file=sys.stderr)
    if bad:
        print(f"[census] {bad} contaminated prompt(s): DO NOT RUN", file=sys.stderr)
    elif verbose:
        print("[census] all prompts clean")
    return bad


def census_summary(trace: Path) -> dict:
    """Light per-run board summary (the full analysis lives in dag_census.py)."""
    records = []
    with trace.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    break
    tasks: dict[str, dict] = {}
    updates = 0
    for r in records:
        d = r.get("data") or {}
        ev = r.get("event")
        if ev == "task_create":
            tid = d.get("id") or d.get("task_id") or "?"
            tasks[tid] = {"blocked": list(d.get("blockedBy") or []), "claim_s": None, "done_s": None}
        elif ev == "task_update":
            updates += 1
            if d.get("blocked_by_task_ids") is not None and d.get("task_id") in tasks:
                tasks[d["task_id"]]["blocked"] = list(d["blocked_by_task_ids"])
        elif ev == "task_claim" and d.get("task_id") in tasks:
            tasks[d["task_id"]]["claim_s"] = r.get("elapsed_ms")
        elif ev == "task_complete" and d.get("task_id") in tasks:
            tasks[d["task_id"]]["done_s"] = r.get("elapsed_ms")
    ids = set(tasks)
    edges = [(b, t) for t, v in tasks.items() for b in v["blocked"] if b in ids]
    end = next((r["data"] for r in records if r.get("event") == "profile_end"), {})
    return {"trace": str(trace), "tasks": len(tasks), "edges": len(edges),
            "update_task_calls": updates,
            "blocked_tasks": sum(1 for v in tasks.values() if v["blocked"]),
            "unclaimed": sum(1 for v in tasks.values() if v["claim_s"] is None),
            "incomplete": sum(1 for v in tasks.values() if v["done_s"] is None),
            "status": end.get("status"), "wall_seconds": end.get("wall_seconds")}


def trace_for(label: str, trace_dir: Path) -> Path | None:
    """Locate a run's trace by its profile_meta label (same approach as dag_workloads.py)."""
    for path in sorted(trace_dir.glob("run_*.jsonl")):
        if path.name.count(".") != 1:
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                for _ in range(80):
                    line = fh.readline()
                    if not line:
                        break
                    rec = json.loads(line)
                    if rec.get("event") == "profile_meta" and rec["data"].get("label") == label:
                        return path
        except (json.JSONDecodeError, OSError):
            continue
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--only", default=None, help="comma-separated categories")
    p.add_argument("--structures", default="DEP,FLAT")
    p.add_argument("--provider-tag", default="glm", help="label tag only; the model comes from the lane's .env")
    p.add_argument("--repo", default=str(REPO), help="repository (or lane copy) whose profile_run.py drives")
    p.add_argument("--trace-dir", default=None)
    p.add_argument("--max-seconds", type=float, default=1200.0)
    p.add_argument("--quiet-seconds", type=float, default=30.0)
    p.add_argument("--pause", type=float, default=20.0)
    p.add_argument("--shuffle-seed", type=int, default=20260917)
    p.add_argument("--topup", action="store_true", help="skip cells that already have a trace")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--print-prompts", action="store_true")
    p.add_argument("--check-prompts", action="store_true")
    p.add_argument("--extra", action="append", default=[])
    args = p.parse_args()

    if args.check_prompts:
        return 1 if check_prompts() else 0

    cats = args.only.split(",") if args.only else CATEGORIES
    structs = args.structures.split(",")
    if args.print_prompts:
        for cat in cats:
            for struct in structs:
                pr = build_prompt(cat, struct, nonce="print")
                print(f"\n=== {cat}-{struct} ({len(pr)} chars)\n{pr}")
        return 0

    # never spend an API call on a contaminated prompt
    if check_prompts(verbose=False):
        return 2

    trace_dir = Path(args.trace_dir) if args.trace_dir else TRACE_DIR
    trace_dir.mkdir(parents=True, exist_ok=True)
    lane = Path(args.repo)
    driver = lane / "s15_integrated_harness" / "scripts" / "profile_run.py"
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  cwd=REPO, check=True).stdout.strip()
    except Exception:
        git_head = ""

    cells = [(c, s, f"r{n + 1}") for n in range(args.reps) for c in cats for s in structs]
    # shuffle inside each repetition so cell order cannot confound provider drift
    rng = random.Random(args.shuffle_seed)
    grouped: dict[str, list] = {}
    for cell in cells:
        grouped.setdefault(cell[2], []).append(cell)
    cells = []
    for rep in sorted(grouped):
        block = grouped[rep]
        rng.shuffle(block)
        cells.extend(block)

    rows = []
    for cat, struct, rep in cells:
        label = f"{cat}-{struct}-{args.provider_tag}-{rep}"
        w = WORKLOADS[(cat, struct)]
        nonce = hashlib.sha1(label.encode()).hexdigest()[:8]
        prompt = build_prompt(cat, struct, nonce=nonce)
        if args.topup and trace_for(label, trace_dir) is not None:
            print(f"[census] skip {label} (already has a trace)", flush=True)
            continue
        cmd = [sys.executable, str(driver), "--label", label, "--trace-output", "full",
               "--trace-dir", str(trace_dir), "--max-seconds", str(args.max_seconds),
               "--quiet-seconds", str(args.quiet_seconds), "--prompt", prompt,
               "--followup-if-no-team", FOLLOWUP]
        if w.get("sandbox"):
            cmd += ["--write-root", w["sandbox"][0], "--sandbox-from", w["sandbox"][1]]
        if w.get("py"):
            cmd += ["--allow-python"]
        cmd += args.extra
        if args.dry_run:
            print(f"[census] DRY {label}: {' '.join(cmd[:8])} ... ({len(prompt)} chars)")
            continue
        log = trace_dir / f"{label}.console.log"
        print(f"[census] {time.strftime('%H:%M:%S')} start {label} (log {log})", flush=True)
        env = dict(os.environ, PROFILE_GIT_HEAD=git_head)
        with log.open("w", encoding="utf-8") as handle:
            code = subprocess.run(cmd, cwd=lane, stdout=handle, stderr=subprocess.STDOUT,
                                  env=env).returncode
        print(f"[census] {time.strftime('%H:%M:%S')} end {label} exit={code}", flush=True)

        trace = trace_for(label, trace_dir)
        if trace is None:
            print(f"[census] {label}: no trace found", flush=True)
            continue
        s = census_summary(trace)
        s.update(label=label, category=cat, structure=struct, rep=rep,
                 provider_tag=args.provider_tag, shape=w["shape"],
                 reference_edges=len(w["edges"]), items=len(w["items"]))
        rows.append(s)
        (trace_dir / f"{label}.census.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
        print(f"[census] {label}: tasks={s['tasks']} edges={s['edges']} "
              f"update_task={s['update_task_calls']} ref_edges={len(w['edges'])} "
              f"status={s['status']} wall={s['wall_seconds']}", flush=True)
        time.sleep(args.pause)

    if rows:
        print("\n| label | shape | items | tasks | edges | ref edges | update_task | wall s | status |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---|")
        for r in rows:
            print(f"| {r['label']} | {r['shape']} | {r['items']} | {r['tasks']} | {r['edges']} | "
                  f"{r['reference_edges']} | {r['update_task_calls']} | {r['wall_seconds']} | "
                  f"{r['status']} |")
        if not any(r["edges"] for r in rows):
            print("\n[census] NO EDGES in any run -- the flat-board result. The secondary measures "
                  "in dag_census.py (prose ordering, staged creation, spawn gating) are the payload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
