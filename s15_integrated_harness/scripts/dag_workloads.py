#!/usr/bin/env python3
"""Run the dependency-shaped (DAG) workloads used by the KV-reuse study.

    python3 s15_integrated_harness/scripts/dag_workloads.py --rep r1 [--only W1,W2,W3]
        [--level L0|L1|L2] [--probe] [--print-prompts] [--trace-dir DIR] [--model MODEL_ID]

Every earlier profiling workload in this repository was deliberately flat: the lead created N
independent task-board items and `blockedBy` stayed empty in all 31 team runs that carry byte-level
read data.  These workloads are the opposite.  Each one asks for work whose stages genuinely depend
on each other, so the lead has a reason to call update_task(addBlockedBy=...) and the board becomes
a real DAG:

  W1  CHAIN           two subsystems x three stages (inventory -> risk review -> test plan).
                      6 tasks, 4 edges.  Within-branch edges and across-branch non-edges in one run.
  W2  FANIN           three independent audits plus one synthesis blocked by all three.
                      4 tasks, 3 edges.  The join case: does a synthesis task re-read its inputs?
  W3  PIPELINE        control.  Each stage's declared input is the PREDECESSOR'S OUTPUT, not its
                      input, so the edges carry no shared corpus by construction; a fourth,
                      independent task shares a file with stage 1 so the run also contains a
                      non-edge pair that does overlap.  4 tasks, 2 edges, sandbox writes.

The task descriptions never name the files a successor should read (W3's control stages excepted) --
the teammate chooses.  What we measure is whether the graph predicts that choice.

Dependency instruction levels (the lead has never emitted an edge unprompted, so how hard we have to
ask is itself a result):

  L0  narrative order only ("using the inventory from the previous stage")
  L1  + an explicit ordering constraint ("stage b must not begin before stage a is complete")
  L2  + name the mechanism ("create all task nodes first, then update_task(addBlockedBy=[...])")

--probe runs trimmed versions with a short deadline and reports only whether the board came out with
edges; use it before spending the full matrix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "s15_integrated_harness" / "traces" / "dag_redundancy"
SEED = "s15_integrated_harness/scripts/dag_bench/pipeline"
SANDBOX = "profiling_sandbox/dag"

TEAM = ("I confirm this team now: spawn the teammates immediately without asking for confirmation. "
        "Do not create worktrees. Spawn exactly THREE teammates, no more, whatever the number of "
        "items. spawn_teammate REFUSES a task that is still blocked, so pass task_id ONLY for items "
        "blocked by nothing; for the blocked items spawn nothing extra and instead tell every "
        "teammate in its prompt to call claim_task for the next ready item on the board as soon as "
        "it finishes one. Each teammate must call complete_task the moment its findings are "
        "written, because a blocked item only becomes claimable once its blockers are completed. "
        "Do not request shutdown of any teammate while the board still has a pending or "
        "in-progress item. ")
SHUTDOWN = "then request shutdown of all teammates."
NOWRITE = " Do not create or modify any files."

# -- the dependency instruction, at three strengths -------------------------------------------
LEVELS = {
    "L0": "",
    "L1": (" A stage must not begin before the stage it follows has completed."),
    "L2": (" A stage must not begin before the stage it follows has completed. Put that on the task "
           "board: create all task nodes first, and once create_task has returned the runtime IDs, "
           "call update_task with addBlockedBy so each stage is blocked by its predecessor."),
}
# W4's board deliberately disagrees with its narrative, so it cannot also carry L2's "blocked by its
# predecessor" clause; it gets the mechanism without the ordering claim.
PLACEBO_DEP = (" Put the dependencies on the task board: create all task nodes first, and once "
               "create_task has returned the runtime IDs, call update_task with addBlockedBy.")

# -- W1 CHAIN ---------------------------------------------------------------------------------
# Two branches over DIFFERENT files, plus (below) one unconnected audit per branch, so that every
# run contains edge pairs and non-edge pairs drawn from the SAME corpus.  Without that the relation
# "is an edge" is collinear with "is about the same file" and nothing is measurable.
W1_SUBSYSTEMS = [
    ("event recording", "s15_integrated_harness/trace_runtime.py"),
    ("trace rendering", "s15_integrated_harness/trace_view.py"),
]
W1_AUDITS = [
    ("event recording", "independently of the stages above, and without waiting for any of them, "
                        "audit the error handling of s15_integrated_harness/trace_runtime.py: list "
                        "every place where a failure is caught, with line numbers"),
    ("trace rendering", "independently of the stages above, and without waiting for any of them, "
                        "audit the error handling of s15_integrated_harness/trace_view.py: list "
                        "every place where a failure is caught, with line numbers"),
]
W1_STAGES = [
    ("inventory", "list the functions of {what} that implement {name}, each with file:line and a "
                  "one-line description of what it does"),
    ("risk review", "using the inventory produced by the previous stage for {name}, list the failure "
                    "modes of {name} in {what}: what can go wrong, under which conditions, with "
                    "line numbers"),
    ("test plan", "using the risk review produced by the previous stage for {name}, write eight "
                  "numbered test cases for {name} in {what}, each with setup, action and "
                  "expected result"),
]

# -- W2 FANIN ---------------------------------------------------------------------------------
W2_AUDITS = [
    ("output loss", "list every place in s15_integrated_harness/trace_runtime.py where an event's "
                    "output can be truncated, summarised or dropped before it reaches the trace file, "
                    "with line numbers"),
    ("shared mutable state", "list every module-level or instance-level mutable object in "
                             "s15_integrated_harness/trace_runtime.py that is reached from more than "
                             "one thread, and say whether a lock protects it, with line numbers"),
    ("swallowed exceptions", "list every place in s15_integrated_harness/trace_runtime.py where an "
                             "exception is caught and then swallowed without being re-raised or "
                             "reported, with line numbers"),
]
W2_SYNTHESIS = ("reconcile the {n} audits into one ranked list of the five most severe issues in "
                "s15_integrated_harness/trace_runtime.py, each entry citing file:line and stating in "
                "one sentence what concretely fails and when")
W2_UNCONNECTED = ("independently of the audits and of the synthesis, and without waiting for any of "
                  "them, describe in at most 12 lines how s15_integrated_harness/trace_runtime.py "
                  "turns a started span into a finished event, with line numbers")

# -- W5 FANIN-DISJOINT -------------------------------------------------------------------------
# W2's three audits all read the SAME file, so whichever auditor picks up the synthesis already
# holds it and a handoff has nothing to add.  Here each predecessor reads a DIFFERENT file, so the
# successor holds one of three and has to acquire the other two -- which is the only shape in which
# a cross-agent transfer can remove a round at all.
W5_AUDITS = [
    ("runtime", "list the functions of s15_integrated_harness/trace_runtime.py that decide what a "
                "recorded event contains, each with file:line"),
    ("view", "list the functions of s15_integrated_harness/trace_view.py that decide how a recorded "
             "event is rendered, each with file:line"),
    ("stats", "list the functions of s15_integrated_harness/trace_stats.py that aggregate recorded "
              "events into numbers, each with file:line"),
]
W5_SYNTHESIS = ("name, for each of the three modules the previous items covered, the one function "
                "that would have to change first if a new event field were added, citing file:line "
                "in each of the three files")
W5_UNCONNECTED = ("independently of the items above, and without waiting for any of them, describe "
                  "in at most 10 lines what s15_integrated_harness/trace_runtime.py writes to disk "
                  "for a single tool call, with line numbers")

# -- W3 PIPELINE (control) --------------------------------------------------------------------
W3_STAGES = [
    ("notes", f"read s15_integrated_harness/GLOSSARY.md and write {SANDBOX}/stage1_notes.md: a "
              "30-line specification of the runtime behaviours the glossary describes, one numbered "
              "line per behaviour"),
    ("tests", f"read {SANDBOX}/stage1_notes.md and nothing else, and write {SANDBOX}/stage2_tests.md: "
              "ten numbered test cases derived from those notes, each with action and expected result"),
    ("review", f"read {SANDBOX}/stage2_tests.md and nothing else, and write {SANDBOX}/stage3_review.md: "
               "a critique of that test plan naming the three weakest cases and why"),
]
W3_INDEPENDENT = ("read s15_integrated_harness/GLOSSARY.md and write " + SANDBOX + "/summary.md: a "
                  "15-line summary of what the glossary covers")


def _items(texts: list[str], nonce: str) -> str:
    """Number the items and stamp the per-run nonce into each, so that no two runs of the same
    workload can serve each other's teammate prefix out of the provider's prefix cache."""
    return " ".join(f"({i + 1}) [run {nonce}] {t}" for i, t in enumerate(texts))


def w1_texts(branches: int, stages: int, audits: bool) -> list[str]:
    texts = []
    for name, what in W1_SUBSYSTEMS[:branches]:
        for _, template in W1_STAGES[:stages]:
            texts.append(f"[{name}] " + template.format(name=name, what=what))
    if audits:
        for name, text in W1_AUDITS[:branches]:
            texts.append(f"[{name}] " + text)
    return texts


def _chain_edges(branches: int, stages: int) -> str:
    """item n is blocked by item n-1 inside each branch; the audits are blocked by nothing."""
    parts = []
    for b in range(branches):
        base = b * stages
        for s_i in range(1, stages):
            parts.append(f"item {base + s_i + 1} is blocked by item {base + s_i}")
    return "The dependencies are: " + ", ".join(parts) + ". Every other item is blocked by nothing."


def _crossed_edges(branches: int, stages: int) -> str:
    """W4 placebo: the same narrative, but every board edge points at the OTHER branch's stage."""
    if branches < 2:
        return _chain_edges(branches, stages)
    if stages == 2:
        # branch A = items 1,2   branch B = items 3,4 -- each successor's BOARD predecessor is the
        # other branch's inventory, while its TEXT still points at its own branch's inventory
        parts = ["item 2 is blocked by item 3", "item 4 is blocked by item 1"]
    else:
        parts = ["item 2 is blocked by item 4", "item 3 is blocked by item 5",
                 "item 5 is blocked by item 1", "item 6 is blocked by item 2"]
    return ("The dependencies are: " + ", ".join(parts) +
            ". Every other item is blocked by nothing. Wire exactly these edges even though the "
            "item texts describe a different order; the board is authoritative.")


def build_prompt(wid: str, level: str, probe: bool = False, nonce: str = "x") -> str:
    dep = LEVELS[level]
    if wid in {"W1", "W4"}:
        branches, stages = (1, 2) if probe else (2, 2)
        audits = not probe
        texts = w1_texts(branches, stages, audits)
        if wid == "W4":
            wiring = _crossed_edges(branches, stages)
            dep = PLACEBO_DEP if level == "L2" else dep
        else:
            wiring = _chain_edges(branches, stages)
        stage_names = ", then ".join(name for name, _ in W1_STAGES[:stages])
        return (f"Produce a review pack for {branches} harness subsystem(s). For each subsystem there "
                f"are {stages} stages, in order: {stage_names}"
                + (", and there are also standalone audits that depend on nothing" if audits else "")
                + ". Create one task-board item per item below."
                + dep + " " + wiring +
                " Copy each item's text verbatim into the task description, and do not tell a "
                "teammate how to do its item. Every report must be at most 12 lines. The items are: "
                + _items(texts, nonce) + " " + TEAM +
                "As each result arrives, react to it; when every item has reported, reply with a "
                "summary of at most 20 lines, " + SHUTDOWN + NOWRITE)
    if wid == "W2":
        audit_texts = [text for _, text in (W2_AUDITS[:2] if probe else W2_AUDITS)]
        n = len(audit_texts)
        texts = audit_texts + [W2_SYNTHESIS.format(n=n)] + ([] if probe else [W2_UNCONNECTED])
        return (f"Run {n} independent audits of the harness, one synthesis that depends on all {n} of "
                "them, and one standalone item that depends on nothing. Create one task-board item "
                "per item below." + dep +
                f" The dependencies are: item {n + 1} is blocked by items 1 to {n}. Every other item "
                "is blocked by nothing. Copy each item's text verbatim into the task description, and "
                "do not tell a teammate how to do its item. Every report must be at most 12 lines. "
                "The items are: " + _items(texts, nonce) +
                " " + TEAM + "When every item has reported, reply with the ranked list, " +
                SHUTDOWN + NOWRITE)
    if wid == "W5":
        audits = W5_AUDITS[:2] if probe else W5_AUDITS
        n = len(audits)
        texts = [text for _, text in audits] + [W5_SYNTHESIS] + ([] if probe else [W5_UNCONNECTED])
        return (f"Run {n} independent inventories of three different harness modules, then one "
                f"synthesis that depends on all {n} of them, and one standalone item that depends on "
                "nothing. Create one task-board item per item below." + dep +
                f" The dependencies are: item {n + 1} is blocked by items 1 to {n}. Every other item "
                "is blocked by nothing. Copy each item's text verbatim into the task description, and "
                "do not tell a teammate how to do its item. Every report must be at most 12 lines. "
                "The items are: " + _items(texts, nonce) + " " + TEAM +
                "When every item has reported, reply with the synthesis, " + SHUTDOWN + NOWRITE)
    if wid == "W3":
        stage_texts = [text for _, text in (W3_STAGES[:2] if probe else W3_STAGES)]
        texts = stage_texts + ([] if probe else [W3_INDEPENDENT])
        n = len(stage_texts)
        wiring = ", ".join(f"item {i + 1} is blocked by item {i}" for i in range(1, n))
        return ("Run a documentation pipeline in which each stage consumes the previous stage's "
                "output file, plus one standalone item that depends on nothing. Create one task-board "
                "item per item below." + dep +
                f" The dependencies are: {wiring}. Every other item is blocked by nothing. Copy each "
                "item's text verbatim into the task description. The items are: " +
                _items(texts, nonce) + " " + TEAM +
                "When every item has reported, reply with a summary of at most 15 lines, " + SHUTDOWN)
    raise SystemExit(f"unknown workload {wid}")


WORKLOADS = ["W1", "W2", "W3", "W4", "W5"]


# ----------------------------------------------------------------------------- DAG check
def load_trace(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return records


def dag_summary(trace: Path) -> dict:
    """The board of one run, and -- the metric that actually gates the study -- how many of its
    edges were EXECUTED: both endpoints claimed, predecessor completed before the successor started.
    An edge whose successor never gets claimed contributes nothing to a redundancy measurement, and
    the harness makes that easy to hit (spawn_teammate refuses a blocked task, so successors can only
    be picked up by an idle teammate's auto-claim)."""
    records = load_trace(trace)
    tasks: dict[str, dict] = {}
    claims: list[tuple[str, str]] = []
    for record in records:
        data = record.get("data") or {}
        event = record.get("event")
        if event == "task_create":
            tasks[data.get("id")] = {"subject": data.get("subject") or "",
                                     "blocked": list(data.get("blockedBy") or []),
                                     "owner": None, "claim_s": None, "done_s": None}
        elif event == "task_update" and data.get("blocked_by_task_ids") is not None:
            task = tasks.get(data.get("task_id"))
            if task is not None:
                task["blocked"] = list(data["blocked_by_task_ids"])
        elif event == "task_claim":
            claims.append((data.get("owner"), data.get("task_id")))
            task = tasks.get(data.get("task_id"))
            if task is not None:
                task["owner"] = data.get("owner")
                task["claim_s"] = record.get("elapsed_ms")
        elif event == "task_complete":
            task = tasks.get(data.get("task_id"))
            if task is not None:
                task["done_s"] = record.get("elapsed_ms")
    edges = [(b, t) for t, task in tasks.items() for b in task["blocked"] if b in tasks]
    executed = [(b, t) for b, t in edges
                if tasks[b]["done_s"] is not None and tasks[t]["claim_s"] is not None
                and tasks[b]["done_s"] <= tasks[t]["claim_s"]]
    cross = [(b, t) for b, t in executed if tasks[b]["owner"] != tasks[t]["owner"]]
    end = next((r["data"] for r in records if r.get("event") == "profile_end"), {})
    return {"trace": str(trace), "tasks": len(tasks), "edges": len(edges),
            "executed_edges": len(executed), "cross_owner_edges": len(cross),
            "executed_yield": round(len(executed) / len(edges), 2) if edges else None,
            "blocked_tasks": sum(1 for t in tasks.values() if t["blocked"]),
            "claims": len(claims), "owners": len({o for o, _ in claims}),
            "unclaimed_tasks": sum(1 for t in tasks.values() if t["claim_s"] is None),
            "incomplete_tasks": sum(1 for t in tasks.values() if t["done_s"] is None),
            "status": end.get("status"), "wall_seconds": end.get("wall_seconds"),
            "subjects": [t["subject"][:60] for t in tasks.values()]}


def trace_for(label: str, trace_dir: Path) -> Path | None:
    """Locate a run's trace by its profile_meta label (same approach as tool_cost_harness.py)."""
    best = None
    for path in sorted(trace_dir.glob("run_*.jsonl")):
        if path.name.count(".") != 1:
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                for _ in range(80):
                    line = handle.readline()
                    if not line:
                        break
                    record = json.loads(line)
                    if record.get("event") == "profile_meta" and record["data"].get("label") == label:
                        best = path
                        break
        except (json.JSONDecodeError, OSError):
            continue
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rep", default="r1", help="repetition tag appended to the label, e.g. r1")
    parser.add_argument("--only", default=None, help="comma-separated workload ids (default W1,W2,W3)")
    parser.add_argument("--level", default="L2", choices=sorted(LEVELS), help="strength of the dependency instruction")
    parser.add_argument("--probe", action="store_true", help="trimmed workloads, short deadline, DAG check only")
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--quiet-seconds", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=20.0)
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--model", default=None, help="override MODEL_ID for this run (second-model check)")
    parser.add_argument("--repo", default=str(REPO), help="repository (or lane copy) whose profile_run.py drives")
    parser.add_argument("--extra", action="append", default=[], help="extra argv passed through to profile_run.py")
    parser.add_argument("--label-suffix", default="", help="appended to the label (e.g. an experiment arm)")
    parser.add_argument("--nonce", default=None, help="per-run tag stamped into every item "
                        "(default: derived from the label) so runs cannot share a provider prefix")
    parser.add_argument("--print-prompts", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="only re-check the DAG of existing labels")
    args = parser.parse_args()

    ids = args.only.split(",") if args.only else list(WORKLOADS)
    trace_dir = Path(args.trace_dir) if args.trace_dir else (TRACE_DIR / "probe" if args.probe else TRACE_DIR)
    trace_dir.mkdir(parents=True, exist_ok=True)
    max_seconds = args.max_seconds if args.max_seconds is not None else (600.0 if args.probe else 1500.0)
    lane = Path(args.repo)
    driver = lane / "s15_integrated_harness" / "scripts" / "profile_run.py"
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  cwd=REPO, check=True).stdout.strip()
    except Exception:
        git_head = ""

    rows = []
    for wid in ids:
        model_tag = f"-{args.model.split('/')[-1]}" if args.model else ""
        label = f"{wid}-{args.level}{model_tag}{args.label_suffix}-{args.rep}"
        nonce = args.nonce or hashlib.sha1(label.encode()).hexdigest()[:8]
        prompt = build_prompt(wid, args.level, probe=args.probe, nonce=nonce)
        if args.print_prompts:
            print(f"\n=== {label} ({len(prompt)} chars)\n{prompt}\n")
            continue
        log = trace_dir / f"{label}.console.log"
        if not args.check_only:
            cmd = [sys.executable, str(driver), "--label", label, "--trace-output", "full",
                   "--trace-dir", str(trace_dir), "--max-seconds", str(max_seconds),
                   "--quiet-seconds", str(args.quiet_seconds), "--prompt", prompt,
                   "--followup-if-no-team",
                   "Confirmed, proceed: create the task nodes, add the dependencies, and spawn the teammates now."]
            # every run records its per-task read sets: a baseline run's dump is the frozen spec
            # the context-handoff arms inject from
            cmd += ["--prewarm-dump", str(trace_dir / f"{label}.prewarm.json")]
            if wid == "W3":
                cmd += ["--write-root", SANDBOX, "--sandbox-from", SEED]
            cmd += args.extra
            env = dict(os.environ, PROFILE_GIT_HEAD=git_head)
            if args.model:
                env["MODEL_ID"] = args.model
            print(f"[dag] {time.strftime('%H:%M:%S')} start {label} (log {log})", flush=True)
            with log.open("w", encoding="utf-8") as handle:
                code = subprocess.run(cmd, cwd=lane, stdout=handle, stderr=subprocess.STDOUT, env=env).returncode
            print(f"[dag] {time.strftime('%H:%M:%S')} end {label} exit={code}", flush=True)
        trace = trace_for(label, trace_dir)
        if trace is None:
            print(f"[dag] {label}: no trace found", flush=True)
            continue
        summary = dag_summary(trace)
        summary["label"] = label
        summary["level"] = args.level
        summary["workload"] = wid
        summary["model"] = args.model or os.environ.get("MODEL_ID", "")
        summary["nonce"] = nonce
        rows.append(summary)
        (trace_dir / f"{label}.dag.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"[dag] {label}: tasks={summary['tasks']} edges={summary['edges']} "
              f"executed={summary['executed_edges']} cross_owner={summary['cross_owner_edges']} "
              f"unclaimed={summary['unclaimed_tasks']} owners={summary['owners']} "
              f"status={summary['status']} wall={summary['wall_seconds']}s", flush=True)
        if not args.check_only:
            time.sleep(args.pause)

    if rows:
        print("\n| label | level | tasks | edges | executed | cross-owner | unclaimed | owners | wall s | status |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for row in rows:
            print(f"| {row['label']} | {row['level']} | {row['tasks']} | {row['edges']} | "
                  f"{row['executed_edges']} | {row['cross_owner_edges']} | {row['unclaimed_tasks']} | "
                  f"{row['owners']} | {row['wall_seconds']} | {row['status']} |")
        ex = sum(r["executed_edges"] for r in rows)
        total = sum(r["edges"] for r in rows)
        print(f"\n[dag] executed-edge yield {ex}/{total}"
              f"{'' if not total else f' = {ex / total:.0%}'}; "
              f"cross-owner {sum(r['cross_owner_edges'] for r in rows)}")
        if all(row["edges"] == 0 for row in rows):
            print("\n[dag] NO EDGES in any run: escalate --level, or fall back to structural proxies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
