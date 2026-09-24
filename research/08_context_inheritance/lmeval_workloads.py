#!/usr/bin/env python3
"""G1: does the lead build a real dependency graph on real benchmark tasks, and how deep?

    python3 research/08_context_inheritance/lmeval_workloads.py --census --limit 4
    python3 research/08_context_inheritance/lmeval_workloads.py --arms none,dag,ancestors --tasks qasper_freeform --limit 8

THIS GATE DECIDES WHETHER THE LIVE ARMS ARE WORTH RUNNING AT ALL.  Arms 2 and 3 can only differ at
DEPTH >= 3: in a chain A -> B -> C the direct predecessors of C are {B} and its ancestors are {A, B},
so a board that is only two layers deep makes the `ancestors` arm the `dag` arm with extra steps.
The Sept 17-18 study guaranteed depth by writing workloads that had it.  Here the depth has to come
from the lead reading a real task, or it does not exist.

NOTHING IN THE PROMPT ASKS FOR A GRAPH.  The task text is exactly what lm-eval renders, plus the
answer contract.  The words "depend", "blocked", "independent", "update_task" and "subtask" never
appear: the 2026-09-17 census established that prompts saying "independent" were what suppressed
edges in the earlier study, so any nudge in either direction invalidates the measurement.  A run
where the lead judged the task simple and answered directly is recorded as depth 0 and kept -- that
is a result about real work, not a failed run.

The slate mirrors the DEP/FLAT design of that census:
  DEP-likely   multi-document or multi-stage work, where a decomposition has somewhere to go
  FLAT-likely  single-answer reasoning, where it does not.  If the lead builds graphs here too, the
               graph is noise rather than structure.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
from lmeval_agent_model import contract_for                                     # noqa: E402
from trace_task_dag import parse_tasks                                          # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "research" / "common" / "profile_run.py"
TRACE_ROOT = REPO / "research" / "08_context_inheritance" / "data" / "lmeval"

DEP_LIKELY = ["qasper_freeform", "longbench2_academic_multi", "longproc_travel_planning_2k",
              "longproc_path_traversal_2k"]
FLAT_LIKELY = ["gsm8k", "gpqa_diamond_cot_zeroshot", "mmlu_pro_biology", "ifeval"]


def render(task: str, limit: int, seed: int = 20260918) -> list[tuple[str, str]]:
    """lm-eval's own rendering of the task: its docs, its template, its few-shot policy."""
    from lm_eval.tasks import TaskManager, get_task_dict                        # noqa: PLC0415

    task_dict = get_task_dict([task], TaskManager())
    obj = task_dict[task]
    while isinstance(obj, dict):
        obj = next(iter(obj.values()))
    docs = list(obj.eval_docs)
    random.Random(seed).shuffle(docs)
    out = []
    for i, doc in enumerate(docs[:limit]):
        out.append((f"{task}-{i:03d}", obj.doc_to_text(doc)))
    return out


def depth_of(edges: dict[str, list[str]]) -> int:
    """Longest chain in the board.  Depth 1 = a task with no blockers, 2 = one hop, and only at 3
    do the `dag` and `ancestors` arms see different content."""
    memo: dict[str, int] = {}

    def walk(node: str, seen: frozenset = frozenset()) -> int:
        if node in memo:
            return memo[node]
        if node in seen:
            return 1
        preds = edges.get(node) or []
        value = 1 + max((walk(p, seen | {node}) for p in preds), default=0)
        memo[node] = value
        return value

    return max((walk(n) for n in edges), default=0)


def board_of(trace: Path) -> dict:
    parsed = parse_tasks(trace)
    tasks = parsed.get("tasks") or {}
    # parse_tasks stores the edge list under "blocked" (trace_task_dag.py:61), NOT "blockedBy" --
    # reading the wrong key returns an empty graph for every run, which looks exactly like a lead
    # that never built one.  Assert the shape rather than trusting it again.
    edges = {tid: list((t.get("blocked") or [])) for tid, t in tasks.items()}
    if tasks and not any("blocked" in t for t in tasks.values()):
        raise KeyError("parse_tasks records have no 'blocked' field; the schema moved")
    n_edges = sum(len(v) for v in edges.values())
    return {"tasks": len(tasks), "edges": n_edges, "depth": depth_of(edges),
            "blocked": sum(1 for v in edges.values() if v)}


def run_cell(task: str, doc_id: str, prompt: str, arm: str, evict: str, budget: str,
             model: str, max_seconds: int, rep: str, dry: bool) -> dict | None:
    label = f"{task}-{arm}-{evict}-{budget}-{rep}-{doc_id.rsplit('-', 1)[-1]}"
    out_dir = TRACE_ROOT / task
    out_dir.mkdir(parents=True, exist_ok=True)
    answer = out_dir / f"{label}.answer.json"
    # a long-context task document blows past Linux's 128 KB MAX_ARG_STRLEN and the exec fails
    # with Errno 7 before the harness even starts, so the prompt always goes through a file
    prompt_path = out_dir / f"{label}.prompt.txt"
    prompt_path.write_text(f"{prompt.rstrip()}\n\n{contract_for(task)}", encoding="utf-8")
    cmd = [sys.executable, str(DRIVER), "--label", label, "--trace-output", "full",
           "--trace-dir", str(out_dir), "--max-seconds", str(max_seconds),
           "--quiet-seconds", "20", "--model", model,
           "--prompt-file", str(prompt_path),
           "--prewarm", arm, "--prewarm-evict", evict, "--prewarm-budget", budget,
           "--answer-out", str(answer)]
    if dry:
        print(f"[dry] {label}")
        return None
    started = time.time()
    subprocess.run(cmd, cwd=REPO, check=False, capture_output=True, text=True,
                   timeout=max_seconds + 240)
    traces = sorted(out_dir.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
    traces = [t for t in traces if t.name.count(".") == 1 and t.stat().st_mtime >= started - 5]
    row = {"task": task, "doc": doc_id, "arm": arm, "evict": evict, "budget": budget, "rep": rep,
           "label": label, "wall_s": round(time.time() - started, 1)}
    if answer.exists():
        row.update(json.loads(answer.read_text()))
    if traces:
        row["trace"] = traces[-1].name
        try:
            row.update(board_of(traces[-1]))
        except Exception as exc:                                                # noqa: BLE001
            row["board_error"] = f"{type(exc).__name__}: {exc}"
    return row


def census_table(path: Path) -> str:
    """G1. Depth is what matters: arms 2 and 3 see different content only at depth >= 3."""
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    by_task: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_task[row["task"]].append(row)
    out = ["## G1 — does the lead build a dependency graph on real benchmark tasks?\n",
           "Nothing in the prompt asks for one: the task text is exactly what lm-eval renders plus "
           "the answer contract. A run where the lead judged the task simple and answered directly "
           "is depth 0 and is kept — that is a result about real work.\n",
           "| task | expected | runs | any board | any edge | max depth | depth>=3 | median wall s |",
           "|---|---|---|---|---|---|---|---|"]
    for task in sorted(by_task):
        g = by_task[task]
        shape = "DEP-likely" if task in DEP_LIKELY else "FLAT-likely"
        boards = sum(1 for r in g if (r.get("tasks") or 0) > 0)
        edged = sum(1 for r in g if (r.get("edges") or 0) > 0)
        deep = sum(1 for r in g if (r.get("depth") or 0) >= 3)
        depths = [r.get("depth") or 0 for r in g]
        walls = sorted(r.get("wall_s", 0) for r in g)
        out.append(f"| `{task}` | {shape} | {len(g)} | {boards} | {edged} | {max(depths)} | "
                   f"{deep} | {walls[len(walls) // 2]:.0f} |")
    deep_total = sum(1 for r in rows if (r.get("depth") or 0) >= 3)
    out.append(f"\n**{deep_total} of {len(rows)} runs reached depth >= 3.** Arms 2 and 3 are "
               "indistinguishable below that, so this number is the gate on the live arm comparison: "
               "at zero, the honest finding is that real lm-eval tasks do not make the lead build "
               "graphs deep enough for the ancestors arm to mean anything, and no edges get "
               "fabricated to rescue it.")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--census", action="store_true", help="baseline only, over the whole slate")
    ap.add_argument("--tasks", default="")
    ap.add_argument("--arms", default="none")
    ap.add_argument("--evict", default="lru")
    ap.add_argument("--budgets", default="unlimited")
    ap.add_argument("--limit", type=int, default=4, help="documents per task")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--max-seconds", type=int, default=420)
    ap.add_argument("--out", type=Path, default=TRACE_ROOT / "census.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true", help="table an existing census, no API calls")
    args = ap.parse_args()

    if args.report:
        print(census_table(args.out))
        return 0

    tasks = [t for t in args.tasks.split(",") if t] or (DEP_LIKELY + FLAT_LIKELY)
    cells = []
    for task in tasks:
        try:
            docs = render(task, args.limit)
        except Exception as exc:                                                # noqa: BLE001
            print(f"[census] skip {task}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        for doc_id, prompt in docs:
            for arm in args.arms.split(","):
                for budget in args.budgets.split(","):
                    for rep in range(args.reps):
                        cells.append((task, doc_id, prompt, arm, args.evict, budget, f"r{rep + 1}"))
    # interleave arms within a repetition and shuffle on a fixed seed, so provider drift over the
    # run cannot line up with an arm
    random.Random(20260918).shuffle(cells)
    print(f"[census] {len(cells)} cells over {len(tasks)} tasks", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["label"])
    with args.out.open("a") as fh:
        for i, (task, doc_id, prompt, arm, evict, budget, rep) in enumerate(cells, 1):
            # skip BEFORE spending the call, not after -- a resumed run was re-running every
            # completed cell and then throwing the result away
            label = f"{task}-{arm}-{evict}-{budget}-{rep}-{doc_id.rsplit('-', 1)[-1]}"
            if label in done:
                continue
            row = run_cell(task, doc_id, prompt, arm, evict, budget, args.model,
                           args.max_seconds, rep, args.dry_run)
            if row is None:
                continue
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"[census] {i}/{len(cells)} {row['label']} tasks={row.get('tasks')} "
                  f"edges={row.get('edges')} depth={row.get('depth')} "
                  f"{row.get('wall_s')}s", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
