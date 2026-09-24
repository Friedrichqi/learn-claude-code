#!/usr/bin/env python3
"""Three-arm inheritance experiment: how far back up the dependency graph is it worth reaching?

    python3 research/08_context_inheritance/inherit_workloads.py --arms none,dag,ancestors \
        --only RW-FQA,FAB-DEEP --reps 3

The arms differ only in what a successor teammate is handed when it claims a blocked task:

  none        nothing (baseline)
  dag         the reads of its DIRECT predecessors
  ancestors   the reads of its whole transitive closure -- predecessors of predecessors too

**The two treatment arms can only differ at depth 3 or more.** In a chain A -> B -> C the direct
predecessors of C are {B}; its ancestors are {A, B}. Both workloads below are therefore built so the
final task genuinely needs content that its direct predecessor never read.

  RW-FQA    real-world, depth 3, edges emitted by the Lead unprompted.  The FQA-DEP chain from the
            2026-09-17 census (which measured edge recall 1.00 on it), reused verbatim:
                #1 read GLOSSARY.md        -> quote three definitions
                #2 read ARCHITECTURE.md    -> locate the same three terms   (blocked by #1)
                #3 reconcile the two       -> needs BOTH files              (blocked by #2)
            For #3: direct = {#2} = ARCHITECTURE.md only; ancestors = {#1,#2} = both files.

  FAB-DEEP  fabricated, depth 4 with a fan-in, edges stated explicitly in the prompt.  Deliberately
            deeper and wider than anything the Lead produces naturally, because the point here is to
            profile inheritance, not to measure emission -- stating the edges removes emission
            variance from the measurement.  Four documents disagree about the compaction pipeline
            and the last task has to reconcile all four:

                #1 GLOSSARY.md ──► #2 ARCHITECTURE.md ──► #3 DESIGN.md ──┐
                                                                        ├──► #5 reconcile all four
                #4 README.md ───────────────────────────────────────────┘
                #6 standalone (no edges, the within-run control)

            For #5: direct = {#3,#4} = DESIGN.md + README.md (2 of 4 documents);
                    ancestors = {#1,#2,#3,#4} = all four.
            The graded answer needs a fact from each of the four, so the arms are separable by
            construction: baseline must find four documents, `dag` two, `ancestors` none.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
from dag_workloads import dag_summary, trace_for  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "research" / "08_context_inheritance" / "data" / "inherit"

TEAM = ("I confirm this team now: spawn the teammates immediately without asking for confirmation. "
        "Do not create worktrees. Spawn exactly THREE teammates, no more, whatever the number of "
        "items. spawn_teammate REFUSES a task that is still blocked, so pass task_id ONLY for items "
        "blocked by nothing; tell every teammate in its prompt to call claim_task for the next ready "
        "item on the board as soon as it finishes one. Each teammate must call complete_task the "
        "moment its findings are written, because a blocked item only becomes claimable once its "
        "blockers are completed. Do not request shutdown of any teammate while the board still has a "
        "pending or in-progress item. ")
SHUTDOWN = "then request shutdown of all teammates."
NOWRITE = " Do not create or modify any files."
FQA_TAIL = (" Answer in at most 12 lines and cite the file and line number for each claim.")

# ---------------------------------------------------------------- RW-FQA (real world, depth 3)
# Verbatim from research/07_task_dag/census_workloads.py FQA_DEP -- the chain whose edges the Lead emitted
# unprompted in 13/13 DeepSeek and 8/8 glm runs.
RW_ITEMS = [
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
RW_EDGES = [(1, 0), (2, 1)]          # 0-based: item 1 blocked by item 0, item 2 blocked by item 1
RW_SYNTH = "reply with the reconciliation note"
# the successor (#3) must show it consulted BOTH documents
RW_GRADE = [["compaction"], ["hook"], ["skill"],
            ["glossary.md", "glossary"], ["architecture.md", "architecture"]]

# ---------------------------------------------------------------- FAB-DEEP (fabricated, depth 4)
FAB_ITEMS = [
    "from s15_integrated_harness/GLOSSARY.md, read the 'Compaction' entry and list, in order, every "
    "named stage of the compaction pipeline it mentions, with the line number of the entry",
    "using the stage list produced by the previous item, open "
    "s15_integrated_harness/ARCHITECTURE.md section 6 and give, for each stage, the Order number it "
    "carries in that section's layer table and the line number of the table row",
    "using the stage list produced by the previous item, open s15_integrated_harness/DESIGN.md and "
    "give, for each stage, the numeric threshold or target its own section states, with line numbers",
    "independently of the items above, read s15_integrated_harness/README.md and list, in order, "
    "exactly the stages its 'Compaction and Recovery' pipeline diagram names, with the line number",
    "reconcile all four documents: name the single pipeline stage that README.md's diagram OMITS, "
    "and for that stage give (a) the wording GLOSSARY.md uses to describe it, (b) the Order number "
    "and line it has in ARCHITECTURE.md's section 6 table, and (c) the numeric target in its "
    "DESIGN.md section. Answer in at most 12 lines",
    "independently of everything above, state in at most 8 lines what "
    "s15_integrated_harness/DESIGN.md says the estimated context size is computed from, with the "
    "line number",
]
#   #1 -> #2 -> #3 -> #5   and   #4 -> #5      (0-based below)
FAB_EDGES = [(1, 0), (2, 1), (4, 2), (4, 3)]
FAB_SYNTH = "reply with the reconciliation for the omitted stage"
# the omitted stage is fit_tool_results; the answer needs one fact from each of the four documents
FAB_GRADE = [["fit_tool_results"],          # the stage itself (README omits it)
             ["1000", "1 000", "1,000"],    # GLOSSARY / ARCHITECTURE wording: 1000-char previews
             ["3"],                          # ARCHITECTURE section-6 Order number
             ["40000", "40,000", "409600", "409,600"]]   # DESIGN target

WORKLOADS = {
    "RW-FQA": dict(items=RW_ITEMS, edges=RW_EDGES, synth=RW_SYNTH, grade=RW_GRADE,
                   successor=2, state_edges=False),
    "FAB-DEEP": dict(items=FAB_ITEMS, edges=FAB_EDGES, synth=FAB_SYNTH, grade=FAB_GRADE,
                     successor=4, state_edges=True),
}
ARMS = ["none", "dag", "ancestors"]


def build_prompt(wid: str, nonce: str) -> str:
    w = WORKLOADS[wid]
    body = " ".join(f"({i + 1}) [run {nonce}] {t}." for i, t in enumerate(w["items"]))
    n = len(w["items"])
    if w["state_edges"]:
        wiring = ("The dependencies are: "
                  + ", ".join(f"item {a + 1} is blocked by item {b + 1}" for a, b in w["edges"])
                  + ". Every other item is blocked by nothing. Create all task nodes first, and once "
                    "create_task has returned the runtime IDs, call update_task with addBlockedBy to "
                    "record exactly those edges. ")
    else:
        # RW-FQA states no mechanism at all: the chain is in the work, and the Lead is left to
        # notice it, exactly as in the 2026-09-17 census where it did so in 21/21 runs.
        wiring = ""
    return (f"Work through the following {n} items. Create one task-board item per item below. "
            + wiring +
            "Copy each item's text verbatim into the task description, and do not tell a teammate "
            "how to do its item. The items are: " + body + " " + TEAM +
            f"When every item has reported, {w['synth']}, " + SHUTDOWN + NOWRITE)


# ----------------------------------------------------------------------------- grading
def load_trace(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return out


def full_text(value) -> str:
    if isinstance(value, dict):
        full = value.get("full")
        if isinstance(full, str):
            return full
        if isinstance(full, dict):
            return str(full.get("preview") or "")
        return str(value.get("preview") or "")
    return str(value or "")


def score_run(wid: str, trace: Path) -> dict:
    """Grade the SUCCESSOR's own report -- the task whose behaviour the arms change."""
    w = WORKLOADS[wid]
    records = load_trace(trace)
    # the successor is the task with the most blockers; fall back to creation index
    tasks, order = {}, []
    for r in records:
        d = r.get("data") or {}
        if r.get("event") == "task_create":
            tasks[d["id"]] = {"blk": [], "owner": None, "subject": d.get("subject", "")}
            order.append(d["id"])
        elif r.get("event") == "task_update" and d.get("blocked_by_task_ids") is not None:
            if d.get("task_id") in tasks:
                tasks[d["task_id"]]["blk"] = list(d["blocked_by_task_ids"])
        elif r.get("event") == "task_claim" and d.get("task_id") in tasks:
            tasks[d["task_id"]]["owner"] = d.get("owner")
    succ = None
    if tasks:
        succ = max(tasks, key=lambda t: (len(tasks[t]["blk"]), order.index(t)))
    reports = [full_text((r.get("data") or {}).get("content"))
               for r in records
               if r.get("event") == "message_send"
               and (r.get("data") or {}).get("message_type") == "result"
               and (r.get("data") or {}).get("from") == (tasks.get(succ, {}) or {}).get("owner")]
    # the lead's closing text is the fallback when the successor's report is not separable
    text = "\n".join(reports)
    flat = text.lower().replace(",", "").replace(" ", "")
    hits = [any(alt.lower().replace(",", "").replace(" ", "") in flat for alt in group)
            for group in w["grade"]]
    return {"successor": succ, "successor_owner": (tasks.get(succ) or {}).get("owner"),
            "report_chars": len(text), "hits": sum(hits), "total": len(hits),
            "accuracy": sum(hits) / len(hits) if hits else None,
            "missed": [g[0] for g, h in zip(w["grade"], hits) if not h]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", default=",".join(WORKLOADS))
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--max-seconds", type=float, default=900.0)
    p.add_argument("--quiet-seconds", type=float, default=30.0)
    p.add_argument("--pause", type=float, default=10.0)
    p.add_argument("--trace-dir", default=str(TRACE_DIR))
    p.add_argument("--repo", default=str(REPO))
    p.add_argument("--shuffle-seed", type=int, default=20260918)
    p.add_argument("--topup", action="store_true")
    p.add_argument("--print-prompts", action="store_true")
    p.add_argument("--score-only", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--driver-arg", action="append", default=[],
                   help="extra argument passed verbatim to profile_run.py (repeatable), e.g. "
                        "--driver-arg=--prewarm-evict=lfu")
    args = p.parse_args()

    wids = args.only.split(",")
    arms = args.arms.split(",")
    if args.print_prompts:
        for wid in wids:
            pr = build_prompt(wid, "print")
            print(f"\n=== {wid} ({len(pr)} chars)\n{pr}\n")
        return 0

    trace_dir = Path(args.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    lane = Path(args.repo)
    driver = lane / "research" / "common" / "profile_run.py"
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  cwd=REPO, check=True).stdout.strip()
    except Exception:
        git_head = ""

    cells = [(wid, arm, f"r{n + 1}") for n in range(args.reps) for wid in wids for arm in arms]
    rng = random.Random(args.shuffle_seed)
    grouped: dict[str, list] = {}
    for c in cells:
        grouped.setdefault(c[2], []).append(c)
    cells = []
    for rep in sorted(grouped):
        block = grouped[rep]
        rng.shuffle(block)          # arm order must not line up with provider drift
        cells.extend(block)

    rows = []
    for wid, arm, rep in cells:
        label = f"{wid}-{arm}-{rep}"
        if args.topup and trace_for(label, trace_dir) is not None:
            print(f"[inherit] skip {label} (already has a trace)", flush=True)
            continue
        nonce = hashlib.sha1(label.encode()).hexdigest()[:8]
        prompt = build_prompt(wid, nonce)
        log = trace_dir / f"{label}.console.log"
        cmd = [sys.executable, str(driver), "--label", label, "--trace-output", "full",
               "--trace-dir", str(trace_dir), "--max-seconds", str(args.max_seconds),
               "--quiet-seconds", str(args.quiet_seconds), "--prompt", prompt,
               "--followup-if-no-team",
               "Confirmed, proceed: create the task nodes and spawn the teammates now.",
               "--prewarm", arm,
               "--prewarm-dump", str(trace_dir / f"{label}.prewarm.json")]
        cmd += list(args.driver_arg)
        if args.dry_run:
            print(" ".join(cmd[:14]), "...")
            continue
        if not args.score_only:
            print(f"[inherit] {time.strftime('%H:%M:%S')} start {label}", flush=True)
            env = dict(os.environ, PROFILE_GIT_HEAD=git_head)
            with log.open("w", encoding="utf-8") as h:
                code = subprocess.run(cmd, cwd=lane, stdout=h, stderr=subprocess.STDOUT,
                                      env=env).returncode
            print(f"[inherit] {time.strftime('%H:%M:%S')} end {label} exit={code}", flush=True)
        trace = trace_for(label, trace_dir)
        if trace is None:
            print(f"[inherit] {label}: no trace", flush=True)
            continue
        summary = dag_summary(trace)
        summary.update({"label": label, "workload": wid, "arm": arm, "rep": rep})
        summary.update(score_run(wid, trace))
        rows.append(summary)
        (trace_dir / f"{label}.cell.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[inherit] {label}: edges={summary['edges']} executed={summary['executed_edges']} "
              f"accuracy={summary['hits']}/{summary['total']} wall={summary['wall_seconds']}s "
              f"status={summary['status']}", flush=True)
        if not args.score_only:
            time.sleep(args.pause)

    if rows:
        print("\n| cell | edges | executed | cross-owner | accuracy | wall s | status |")
        print("|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['label']} | {r['edges']} | {r['executed_edges']} | {r['cross_owner_edges']} "
                  f"| {r['hits']}/{r['total']} | {r['wall_seconds']} | {r['status']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
