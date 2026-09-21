#!/usr/bin/env python3
"""F2 — given a byte budget, which retention rule actually removes rounds?

    python3 s15_integrated_harness/scripts/budget_sweep.py traces/inherit traces/dag_redundancy --md

Every policy in the two previous studies was defined by GRAPH POSITION (direct predecessors, the
transitive closure, every earlier task). A real engine is constrained by BYTES. This asks the
deployable question instead: at equal budget, does a graph-aware ordering beat plain recency?

It is a pure recomputation over BASELINE traces -- no API calls. It has to be the untreated arm,
because a successor that was handed its content does not read, which is the whole point of treating
it. The unit of retention is one read item (a whole tool result): an engine holds or drops whole
spans, and the 2026-09-16 study measured that a fragmented handoff is re-read 88% of the time.

Orderings
  recent     most-recent-first -- LRU, the engine's default, graph-blind
  graph      direct predecessors, then the rest of the closure, then everything else
  largest    biggest spans first  } naive capacity heuristics
  smallest   smallest spans first }
  oracle     ranked by useful-bytes-per-byte against what the successor went on to read (ceiling)

The headline is REMOVABLE ROUNDS versus budget, not bytes versus budget: bytes were already shown to
accumulate smoothly without paying out in latency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dag_redundancy import DagRun, collect  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ORDERINGS = ["recent", "graph", "largest", "smallest", "oracle", "cheapest_rounds"]
LABEL = {"recent": "most-recent-first (LRU)", "graph": "graph-ordered (preds → closure → rest)",
         "largest": "largest spans first", "smallest": "smallest spans first",
         "oracle": "oracle by byte density",
         "cheapest_rounds": "**cheapest whole rounds first**"}
KB = 1024
BUDGETS_KB = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]


def baseline_runs(targets: list[str], repo: Path, only_baseline: bool) -> list[DagRun]:
    exclude = re.compile(r"traces/|\.task_outputs")
    runs = []
    for path in collect(targets):
        try:
            run = DagRun(path, repo, None, exclude, True, "range")
        except Exception as exc:                                    # noqa: BLE001
            print(f"[budget] skip {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        label = run.red.label or ""
        # the inherit corpus is arm-tagged; the dag_redundancy corpus is all untreated
        if only_baseline and ("-dag-" in label or "-ancestors-" in label
                              or "-pollute-" in label or "-summary-" in label
                              or "-oracle-" in label):
            continue
        runs.append(run)
    return runs


def successors(runs: list[DagRun]) -> list[tuple[DagRun, str]]:
    out = []
    for run in runs:
        for tid in run.tasks:
            preds = [b for b in run.deps.get(tid, []) if b in run.tasks]
            if preds and run.task_bytes.get(tid) and run.candidate_items(tid):
                out.append((run, tid))
    return out


def sweep(pairs: list[tuple[DagRun, str]]) -> dict:
    budgets = [b * KB for b in BUDGETS_KB]
    full = max((run.pool_bytes(v) for run, v in pairs), default=0)
    budgets.append(full + 1)                                        # unlimited
    grid: dict[tuple[str, int], dict] = {}
    for ordering in ORDERINGS:
        for budget in budgets:
            rem = nr = cov = tot = sent = 0
            for run, v in pairs:
                keys, spent = run.budgeted_keys(v, ordering, budget)
                r, n = run.removable_rounds(keys, v)
                c, t, _ = run.coverage(keys, v)
                rem += r
                nr += n
                cov += c
                tot += t
                sent += spent
            grid[(ordering, budget)] = {"removable": rem, "rounds": nr, "covered": cov,
                                        "total": tot, "sent": sent}
    return {"budgets": budgets, "grid": grid, "n": len(pairs),
            "pool_median": sorted(run.pool_bytes(v) for run, v in pairs)[len(pairs) // 2] if pairs else 0}


def tables(data: dict) -> str:
    budgets = data["budgets"]
    grid = data["grid"]
    out = [f"## F2 — removable rounds versus retention budget ({data['n']} baseline successors)\n",
           "Unit of retention is one whole read item. Each cell is pooled removable read-rounds as a "
           "share of all read-issuing rounds; the last column is an unlimited budget.\n"]
    head = "| ordering | " + " | ".join(f"{b // KB} KB" for b in budgets[:-1]) + " | unlimited |"
    out.append(head)
    out.append("|---" * (len(budgets) + 1) + "|")
    for ordering in ORDERINGS:
        cells = []
        for b in budgets:
            g = grid[(ordering, b)]
            cells.append(f"{100 * g['removable'] / g['rounds']:.0f}%" if g["rounds"] else "-")
        out.append(f"| {LABEL[ordering]} | " + " | ".join(cells) + " |")

    out.append("\n### The same grid as byte recall (what accumulates smoothly)\n")
    out.append(head)
    out.append("|---" * (len(budgets) + 1) + "|")
    for ordering in ORDERINGS:
        cells = []
        for b in budgets:
            g = grid[(ordering, b)]
            cells.append(f"{100 * g['covered'] / g['total']:.0f}%" if g["total"] else "-")
        out.append(f"| {LABEL[ordering]} | " + " | ".join(cells) + " |")

    # the decisive comparison
    out.append("\n### Graph-ordered minus most-recent-first, at each budget (removable rounds)\n")
    out.append("| budget | graph | recent | difference |")
    out.append("|---|---|---|---|")
    wins = 0
    for b in budgets:
        gg, gr = grid[("graph", b)], grid[("recent", b)]
        if not gg["rounds"]:
            continue
        a = 100 * gg["removable"] / gg["rounds"]
        c = 100 * gr["removable"] / gr["rounds"]
        wins += 1 if a > c else 0
        name = "unlimited" if b == budgets[-1] else f"{b // KB} KB"
        out.append(f"| {name} | {a:.0f}% | {c:.0f}% | {a - c:+.0f} pts |")
    out.append(f"\nGraph ordering is ahead at **{wins} of {len(budgets)}** budget points. "
               "The pre-registered rule: if it is never ahead, the task board has nothing to offer a "
               "retention policy and the DAG-directed retention proposal should be retired rather "
               "than prototyped.")
    out.append(f"\nMedian candidate pool per successor: {data['pool_median'] / KB:.0f} KB.")

    # ---- invariants.  NOTE: the "oracle" here is not the oracle of the earlier policy table.
    # That one was task v's OWN future reads (a 97% ceiling, including content no other task ever
    # read).  This pool is restricted to what other tasks actually read, so its ceiling is whatever
    # the pool contains -- which is exactly the unlimited-budget number every ordering converges to.
    fullb = budgets[-1]
    conv = {o: grid[(o, fullb)]["removable"] for o in ORDERINGS}
    converged = len(set(conv.values())) == 1
    dominates = all(
        grid[("cheapest_rounds", b)]["removable"] >= grid[(o, b)]["removable"]
        for b in budgets for o in ORDERINGS)
    monotone = all(
        grid[(o, budgets[i])]["removable"] <= grid[(o, budgets[i + 1])]["removable"]
        for o in ORDERINGS for i in range(len(budgets) - 1))
    out.append("\n### Invariants (these must hold or the budgeted key-set builder is wrong)\n")
    out.append(f"- every ordering converges at an unlimited budget: **{converged}** "
               f"(ordering can only matter under a constraint) — "
               f"{100 * grid[('recent', fullb)]['removable'] / grid[('recent', fullb)]['rounds']:.0f}% "
               "of rounds, which is the ceiling of this candidate pool.")
    out.append(f"- buying cheapest whole rounds first dominates every other ordering at every "
               f"budget: **{dominates}** (it optimises the metric directly; the byte-density oracle "
               "does not, which is the point).")
    out.append(f"- removable rounds are non-decreasing in budget for every ordering: **{monotone}**.")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("targets", nargs="+")
    p.add_argument("--repo", default=str(REPO))
    p.add_argument("--all-arms", action="store_true",
                   help="do not filter to the untreated arm (for debugging only)")
    p.add_argument("--json", default=None)
    p.add_argument("--md", action="store_true")
    args = p.parse_args()

    runs = baseline_runs(args.targets, Path(args.repo), not args.all_arms)
    pairs = successors(runs)
    print(f"[budget] {len(runs)} baseline runs, {len(pairs)} successors with reads and a candidate pool",
          file=sys.stderr)
    if not pairs:
        print("no successors to sweep", file=sys.stderr)
        return 1
    data = sweep(pairs)
    print(tables(data))
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"n": data["n"], "budgets": data["budgets"],
             "grid": {f"{o}|{b}": v for (o, b), v in data["grid"].items()}}, indent=2),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
