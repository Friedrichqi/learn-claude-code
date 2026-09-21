#!/usr/bin/env python3
"""Does the lead's task-dependency DAG predict cross-agent file-read redundancy?

    python3 s15_integrated_harness/scripts/dag_redundancy.py traces/dag_redundancy --tables
        [--gran range] [--json OUT] [--perms 2000] [--git-rev A,B] [--repo DIR]

The independent variable is the task board's `blockedBy` graph (rebuilt by trace_task_dag.parse_tasks);
the dependent variable is byte-level read overlap between tasks (units from input_redundancy.Run).
No trace event carries a task id on a tool call, so reads are attributed to tasks through per-owner
ownership intervals:

    task_claim -> (owner, task_id, t_start);  task_complete / agent_end / run_end closes it
    agent_create.data.name maps the owner name back to the agent_id the tool events carry
    an item belongs to the interval of its own agent containing its timestamp

What is reported:

  1. pair table      every ordered task pair classified edge / ancestor / sibling / unrelated, with
                     binary overlap, path Jaccard and byte coverage of the successor's reads
  2. lift            edge-pair rate against the same run's non-edge rate (never a raw rate: the
                     redundancy of a workload is set by how its tasks were cut), bootstrapped over
                     runs, with a within-run permutation test over the edge labels
  3. policy table    coverage / precision / transferred bytes / residency gap for the prefetch
                     policies a real system could run (direct predecessors, ancestors, all earlier
                     tasks, same-owner history, lead history, random task, oracle)
  4. mechanism       whether the successor's task DESCRIPTION already names the predecessor's files --
                     if text predicts as well as the graph, the engine does not need the board

"Removable rounds" is the quantity experiment 2 actually removes: a successor round whose tool calls
are all reads and all covered by the source set.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from input_redundancy import Run as RedRun, load_records, norm_path  # noqa: E402
from trace_task_dag import parse_tasks  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CHARS_PER_TOKEN = 4.0            # the harness's own conversion (code.py CONTEXT_LIMIT)
FIXED_ROUND_S = 3.72             # measured: model call = 3.72 s + 0.033 ms/uncached tok + 17.4 ms/out tok
MS_PER_UNCACHED_TOKEN = 0.033
COVER_ITEM = 0.95                # an item counts as covered when this share of its bytes is in the source set
PATH_RE = re.compile(r"[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.(?:py|md|json|txt|toml|yaml|yml|cfg|sh)\b")
RELATIONS = ("edge", "ancestor", "sibling", "unrelated")


def pct(part: float, whole: float) -> str:
    return "-" if not whole else f"{100.0 * part / whole:.1f}%"


class DagRun:
    """One trace: the task board, the read items, and reads attributed to tasks."""

    def __init__(self, trace: Path, repo: Path, git_rev: str | None, exclude, include_bash: bool,
                 gran: str = "range"):
        self.path = trace
        self.gran = gran
        self.red = RedRun(trace, repo, git_rev, exclude, include_bash, top_files=10)
        self.label = self.red.label or trace.stem
        self.board = parse_tasks(trace)
        self.tasks = self.board["tasks"]
        self.records = self.red.records
        self._name_to_agent()
        self._intervals()
        self._attribute()
        self._task_sets()
        self._rounds()
        self._reachability()

    # ---- owner name -> agent id ------------------------------------------------------------
    def _name_to_agent(self):
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for agent, name in self.red.agent_name.items():
            self.by_name[name].append(agent)
        # agent_create also records the task the lead assigned at spawn time
        self.spawn_task: dict[str, str] = {}
        for rec in self.records:
            if rec.get("event") == "agent_create":
                task_id = (rec.get("data") or {}).get("task_id")
                if task_id:
                    self.spawn_task[rec.get("agent_id")] = task_id

    # ---- ownership intervals ---------------------------------------------------------------
    def _intervals(self):
        end_of_run = max((r.get("elapsed_ms", 0.0) for r in self.records), default=0.0) + 1.0
        agent_end: dict[str, float] = {}
        for rec in self.records:
            if rec.get("event") == "agent_end":
                agent_end[rec.get("agent_id")] = rec.get("elapsed_ms", end_of_run)
        self.intervals: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
        for task_id, task in self.tasks.items():
            owner = task.get("owner")
            start = task.get("claim_s")
            if owner is None or start is None:
                continue
            agents = self.by_name.get(owner) or []
            if not agents:
                # the lead claims on behalf of the team in some runs; fall back to the spawn record
                agents = [a for a, t in self.spawn_task.items() if t == task_id]
            stop = task.get("done_s")
            for agent in agents:
                if stop is None:
                    stop = agent_end.get(agent, end_of_run)
                self.intervals[agent].append((start, stop if stop is not None else end_of_run, task_id))
        for agent in self.intervals:
            self.intervals[agent].sort()

    def _attribute(self):
        """item['task'] = the task that owned the agent when the read happened."""
        self.unattributed = Counter()
        for item in self.red.items:
            agent = item["agent"]
            if item["kind"] != "teammate":
                item["task"] = "__lead__"
                continue
            task_id = None
            for start, stop, tid in self.intervals.get(agent, []):
                if start <= item["t"] <= stop:
                    task_id = tid
            if task_id is None:
                task_id = self.spawn_task.get(agent)
                if task_id is not None:
                    self.unattributed["by_spawn"] += 1
                else:
                    self.unattributed["dropped"] += 1
            item["task"] = task_id

    # ---- per-task read sets ----------------------------------------------------------------
    def _task_sets(self):
        gran = self.gran
        self.task_units: dict[str, dict[tuple, int]] = defaultdict(dict)   # task -> key -> bytes
        self.task_first: dict[str, dict[tuple, float]] = defaultdict(dict)  # task -> key -> first time
        self.task_paths: dict[str, set] = defaultdict(set)
        self.task_bytes: Counter = Counter()
        self.task_items: dict[str, list[int]] = defaultdict(list)
        for index, (item, units) in enumerate(zip(self.red.items, self.red.item_units)):
            task_id = item.get("task")
            if task_id is None:
                continue
            self.task_items[task_id].append(index)
            paths = [item["path"]] if item.get("path") else []
            paths += item.get("bash_paths") or []
            self.task_paths[task_id].update(p for p in paths if p)
            for key, nbytes in units[gran]:
                if key is None:
                    continue
                self.task_units[task_id][key] = self.task_units[task_id].get(key, 0) + nbytes
                prior = self.task_first[task_id].get(key)
                if prior is None or item["t"] < prior:
                    self.task_first[task_id][key] = item["t"]
                self.task_bytes[task_id] += nbytes

    # ---- rounds ----------------------------------------------------------------------------
    def _rounds(self):
        """Group each agent's tool calls into rounds (one model response = one round)."""
        self.round_of: dict[int, tuple[str, int]] = {}
        starts: dict[str, list[float]] = defaultdict(list)
        for call in self.red.model_calls:
            starts[call["agent"]].append(call["t"])
        for agent in starts:
            starts[agent].sort()
        self.round_calls: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for call in self.red.tool_calls:
            idx = bisect_right(starts.get(call["agent"], []), call["t"]) - 1
            self.round_calls[(call["agent"], idx)].append(call)
        for index, item in enumerate(self.red.items):
            idx = bisect_right(starts.get(item["agent"], []), item["t"]) - 1
            self.round_of[index] = (item["agent"], idx)

    def _reachability(self):
        deps = {tid: list(task.get("deps") or []) for tid, task in self.tasks.items()}
        self.deps = deps
        self.ancestors: dict[str, set] = {}
        for tid in deps:
            seen, stack = set(), list(deps[tid])
            while stack:
                node = stack.pop()
                if node in seen or node not in deps:
                    continue
                seen.add(node)
                stack.extend(deps[node])
            self.ancestors[tid] = seen
        self.n_edges = sum(len(v) for v in deps.values())

    # ---- pair analysis ---------------------------------------------------------------------
    def relation(self, u: str, v: str) -> str:
        if u in self.deps.get(v, []):
            return "edge"
        if u in self.ancestors.get(v, set()):
            return "ancestor"
        if set(self.deps.get(u, [])) & set(self.deps.get(v, [])):
            return "sibling"
        if v in self.ancestors.get(u, set()):
            return "descendant"
        return "unrelated"

    def task_start(self, tid: str) -> float:
        task = self.tasks.get(tid, {})
        for key in ("claim_s", "created_s"):
            if task.get(key) is not None:
                return float(task[key])
        return 0.0

    def coverage(self, source_keys: dict[tuple, float], v: str) -> tuple[int, int, int]:
        """(covered bytes, total bytes, covered items) of task v against a {key: first_seen} source."""
        covered = total = ncov = 0
        for index in self.task_items.get(v, []):
            units = self.red.item_units[index][self.gran]
            item_t = self.red.items[index]["t"]
            item_cov = item_total = 0
            for key, nbytes in units:
                if key is None:
                    continue
                item_total += nbytes
                first = source_keys.get(key)
                if first is not None and first < item_t:
                    item_cov += nbytes
            covered += item_cov
            total += item_total
            if item_total and item_cov / item_total >= COVER_ITEM:
                ncov += 1
        return covered, total, ncov

    def removable_rounds(self, source_keys: dict[tuple, float], v: str) -> tuple[int, int]:
        """(rounds whose reads are all covered and which do nothing else, total read rounds of v)."""
        rounds: dict[tuple[str, int], list[int]] = defaultdict(list)
        for index in self.task_items.get(v, []):
            rounds[self.round_of[index]].append(index)
        removable = 0
        for key, indices in rounds.items():
            calls = self.round_calls.get(key, [])
            read_ids = {self.red.items[i].get("tool") for i in indices}
            non_read = [c for c in calls if c["tool"] not in {"read_file", "bash", "glob"}]
            if non_read:
                continue
            ok = True
            for index in indices:
                units = self.red.item_units[index][self.gran]
                item_t = self.red.items[index]["t"]
                total = sum(n for k, n in units if k is not None)
                cov = sum(n for k, n in units if k is not None
                          and source_keys.get(k) is not None and source_keys[k] < item_t)
                if not total or cov / total < COVER_ITEM:
                    ok = False
                    break
            if ok and read_ids:
                removable += 1
        return removable, len(rounds)

    def source_keys(self, tasks: list[str], before: float | None = None) -> dict[tuple, float]:
        out: dict[tuple, float] = {}
        for tid in tasks:
            for key, first in self.task_first.get(tid, {}).items():
                if before is not None and first >= before:
                    continue
                if key not in out or first < out[key]:
                    out[key] = first
            
        return out

    def source_bytes(self, tasks: list[str]) -> int:
        seen: dict[tuple, int] = {}
        for tid in tasks:
            for key, nbytes in self.task_units.get(tid, {}).items():
                seen[key] = max(seen.get(key, 0), nbytes)
        return sum(seen.values())

    def first_read(self, tid: str) -> float | None:
        times = [self.red.items[i]["t"] for i in self.task_items.get(tid, [])]
        return min(times) if times else None

    def eligible(self, u: str, v: str) -> bool:
        """u could physically have supplied v: it had already read something when v started reading.

        The stricter reading -- u COMPLETED before v was CLAIMED -- is kept as the `strict_handoff`
        flag rather than as the filter, because it excludes every parallel pair, and a task that
        read a file five minutes ago is exactly the case a retention policy is about, whether or not
        its owner has finished writing the report."""
        fu, fv = self.first_read(u), self.first_read(v)
        return fu is not None and fv is not None and fu < fv

    def strict_handoff(self, u: str, v: str) -> bool:
        done = self.tasks.get(u, {}).get("done_s")
        claim = self.tasks.get(v, {}).get("claim_s")
        return done is not None and claim is not None and done <= claim

    def desc_jaccard(self, u: str, v: str) -> float:
        def words(tid):
            text = f"{self.tasks[tid].get('subject','')} {self.tasks[tid].get('desc','')}".lower()
            return {w for w in re.findall(r"[a-z_][a-z0-9_.]{2,}", text)}
        a, b = words(u), words(v)
        return len(a & b) / len(a | b) if (a | b) else 0.0

    def pairs(self) -> list[dict]:
        """Ordered task pairs (u supplied before v started) with relation and overlap measures."""
        rows = []
        readers = [t for t in self.tasks if self.task_bytes.get(t)]
        for v in readers:
            v_total = self.task_bytes.get(v, 0)
            for u in readers:
                if u == v or not self.eligible(u, v):
                    continue
                relation = self.relation(u, v)
                if relation == "descendant":
                    continue
                keys = self.task_first.get(u, {})
                covered, total, ncov = self.coverage(keys, v)
                removable, nrounds = self.removable_rounds(keys, v)
                pu, pv = self.task_paths.get(u, set()), self.task_paths.get(v, set())
                union = pu | pv
                rows.append({
                    "run": self.label, "u": u, "v": v, "relation": relation,
                    "is_edge": 1 if relation == "edge" else 0,
                    "binary": 1 if pu & pv else 0,
                    "jaccard": (len(pu & pv) / len(union)) if union else 0.0,
                    "covered": covered, "total": total,
                    "coverage": covered / total if total else 0.0,
                    "removable": removable, "rounds": nrounds,
                    "round_share": removable / nrounds if nrounds else 0.0,
                    "items_covered": ncov, "items": len(self.task_items.get(v, [])),
                    "u_bytes": self.task_bytes.get(u, 0), "v_bytes": v_total,
                    "gap_s": (self.task_start(v) - (self.tasks[u].get("done_s") or 0.0)) / 1000.0,
                    "same_owner": 1 if self.tasks[u].get("owner") == self.tasks[v].get("owner") else 0,
                    "same_corpus": 1 if pu & pv else 0,
                    "strict_handoff": 1 if self.strict_handoff(u, v) else 0,
                    "desc_jaccard": self.desc_jaccard(u, v),
                    "desc_names_pred_file": self.desc_overlap(u, v),
                })
        return rows

    def matched(self, rows: list[dict], field: str = "coverage", seed: int = 20260916) -> list[dict]:
        """For every edge (p,s), the control (p',s) with the SAME successor and the closest gap.

        Holding the successor fixed cancels its read volume, its corpus and its position in the run --
        the three things that made every earlier cross-teammate comparison unreadable."""
        rng = random.Random(seed)
        by_v: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_v[row["v"]].append(row)
        out = []
        for row in rows:
            if row["relation"] != "edge":
                continue
            controls = [c for c in by_v[row["v"]]
                        if c["relation"] in ("sibling", "unrelated") and c["u"] != row["u"]]
            if not controls:
                out.append(dict(row, control=None, delta=None))
                continue
            best = min(controls, key=lambda c: (abs(c["gap_s"] - row["gap_s"]), rng.random()))
            out.append(dict(row, control=best["u"], delta=row[field] - best[field],
                            control_value=best[field], control_same_owner=best["same_owner"]))
        return out

    def desc_overlap(self, u: str, v: str) -> int:
        """Does v's task text already name a file u read?  (graph vs text decomposition)"""
        text = f"{self.tasks[v].get('subject','')} {self.tasks[v].get('desc','')}"
        named = {norm_path(m, self.red.cwd) for m in PATH_RE.findall(text)}
        named = {p for p in named if p}
        return 1 if named & self.task_paths.get(u, set()) else 0

    # ---- budgeted retention -----------------------------------------------------------------
    # The policies above are defined by GRAPH POSITION.  A real engine is constrained by BYTES, so
    # these ask the deployable question instead: given a budget B, which selection rule keeps the
    # bytes that actually remove a round?  The unit of retention is one READ ITEM (a whole tool
    # result), not a line: an engine holds or drops whole spans, and a fragmented handoff is re-read
    # anyway (measured at 88% in the 2026-09-16 study).

    def candidate_items(self, v: str) -> list[dict]:
        """Every read item that could physically have supplied v: it happened before v read."""
        v_first = self.first_read(v)
        if v_first is None:
            return []
        direct = {t for t in self.deps.get(v, []) if t in self.tasks}
        anc = (self.ancestors.get(v, set()) & set(self.tasks)) - direct
        out = []
        for tid, idxs in self.task_items.items():
            if tid == v:
                continue
            tier = 0 if tid in direct else (1 if tid in anc else 2)
            for i in idxs:
                item = self.red.items[i]
                if item["t"] >= v_first:
                    continue
                out.append({"i": i, "bytes": item["bytes"], "t": item["t"],
                            "task": tid, "tier": tier})
        return out

    def keys_from_items(self, idxs: list[int]) -> dict[tuple, float]:
        keys: dict[tuple, float] = {}
        for i in idxs:
            t = self.red.items[i]["t"]
            for key, _ in self.red.item_units[i][self.gran]:
                if key is None:
                    continue
                if key not in keys or t < keys[key]:
                    keys[key] = t
        return keys

    def _useful_bytes(self, i: int, v: str) -> int:
        """Bytes of v's own read set that item i would have covered -- the oracle's ranking key."""
        want = self.task_units.get(v, {})
        return sum(nb for key, nb in self.red.item_units[i][self.gran]
                   if key is not None and key in want)

    def round_costs(self, v: str) -> list[tuple[float, list[int]]]:
        """For each of v's read-only rounds, the cheapest set of candidate items that would fully
        cover it, and that set's byte cost.

        Round removal is ALL-OR-NOTHING, so maximising covered bytes is not the same problem as
        maximising removed rounds -- a density-greedy selection can spend its whole budget covering
        most of several rounds and remove none of them.

        NOT A DEPLOYABLE POLICY: it groups v's OWN read items by round, which is future knowledge at
        the moment a retention decision would be made.  It is a *reachable* ceiling -- unlike the
        `oracle` policy row, which is v's own read set and may contain content no other task ever
        read, this one is assembled only from what other tasks actually read, so a practical
        predictor could in principle aim at it.  What it measures is the size of the prize from
        allocating a budget by whole rounds rather than by bytes.
        """
        pool = self.candidate_items(v)
        item_keys = {c["i"]: {k for k, _ in self.red.item_units[c["i"]][self.gran] if k}
                     for c in pool}
        by_round: dict[tuple, list[int]] = defaultdict(list)
        for index in self.task_items.get(v, []):
            by_round[self.round_of[index]].append(index)
        out = []
        for rkey, indices in by_round.items():
            calls = self.round_calls.get(rkey, [])
            if [c for c in calls if c["tool"] not in {"read_file", "bash", "glob"}]:
                continue                                   # the round does other work; not removable
            need = set()
            for index in indices:
                need |= {k for k, _ in self.red.item_units[index][self.gran] if k}
            if not need:
                continue
            chosen, cost, remaining = [], 0.0, set(need)
            while remaining:
                best, gain = None, 0
                for c in pool:
                    if c["i"] in chosen:
                        continue
                    g = len(item_keys[c["i"]] & remaining)
                    if g > gain:
                        best, gain = c, g
                if best is None:
                    break                                  # not coverable from this pool
                chosen.append(best["i"])
                cost += best["bytes"]
                remaining -= item_keys[best["i"]]
            if not remaining:
                out.append((cost, chosen))
        out.sort(key=lambda x: x[0])
        return out

    def budgeted_keys(self, v: str, ordering: str, budget: int) -> tuple[dict[tuple, float], int]:
        """Select whole read items under a byte budget, then return their unit keys."""
        pool = self.candidate_items(v)
        if ordering == "recent":
            pool.sort(key=lambda c: -c["t"])
        elif ordering == "graph":
            # direct predecessors first, then the rest of the closure, then everything else;
            # most recent within each tier
            pool.sort(key=lambda c: (c["tier"], -c["t"]))
        elif ordering == "largest":
            pool.sort(key=lambda c: -c["bytes"])
        elif ordering == "smallest":
            pool.sort(key=lambda c: c["bytes"])
        elif ordering == "oracle":
            pool.sort(key=lambda c: -(self._useful_bytes(c["i"], v) / max(c["bytes"], 1)))
        elif ordering == "cheapest_rounds":
            # buy whole rounds, cheapest first, until the budget runs out
            chosen, spent = [], 0
            for cost, items in self.round_costs(v):
                extra = [i for i in items if i not in chosen]
                add = sum(self.red.items[i]["bytes"] for i in extra)
                if spent + add > budget:
                    continue
                chosen.extend(extra)
                spent += add
            return self.keys_from_items(chosen), spent
        else:
            raise ValueError(ordering)
        chosen, spent = [], 0
        for c in pool:
            if spent + c["bytes"] > budget:
                continue                       # keep going: a smaller later item may still fit
            chosen.append(c["i"])
            spent += c["bytes"]
        return self.keys_from_items(chosen), spent

    def pool_bytes(self, v: str) -> int:
        return sum(c["bytes"] for c in self.candidate_items(v))

    # ---- policies --------------------------------------------------------------------------
    def policy_rows(self, seed: int = 20260916) -> list[dict]:
        rng = random.Random(seed)
        ids = list(self.tasks)
        rows = []
        for v in self.tasks:
            if not self.task_bytes.get(v):
                continue
            # a task with no predecessor has nothing for direct_pred to send, and averaging those
            # zeros into the row makes the DAG policy look empty for a reason that has nothing to do
            # with the DAG.  Successors carry the headline; the rest are kept but flagged.
            has_pred = 1 if [t for t in self.deps.get(v, []) if t in self.tasks] else 0
            start = self.task_start(v)
            owner = self.tasks[v].get("owner")
            earlier = [t for t in ids if t != v and self.eligible(t, v)]
            # note: source_keys() still only credits units first seen BEFORE the read
            # they would serve, so a parallel source cannot supply a read it missed
            same_owner = [t for t in earlier if self.tasks[t].get("owner") == owner]
            direct = [t for t in self.deps.get(v, []) if t in self.tasks]
            others = [t for t in earlier if t not in set(direct)]
            # the task whose DESCRIPTION looks most like v's, among those that could have supplied
            # it.  In W1 this is the same task as the board predecessor; in the W4 placebo, where the
            # board edge deliberately points at the other branch, the two disagree -- and whichever
            # recalls more is the answer to "is the signal in the graph or in the words?"
            text_pred = ([max(earlier, key=lambda t: (self.desc_jaccard(t, v), t))]
                         if earlier else [])
            policies = {
                "direct_pred": direct,
                "text_pred": text_pred,
                "ancestors": sorted(self.ancestors.get(v, set()) & set(self.tasks)),
                "all_earlier": earlier,
                "same_owner": same_owner,
                "lead": ["__lead__"],
                "random": ([rng.choice(others)] if others else []),
                "oracle": [v],
            }
            for name, sources in policies.items():
                if name == "oracle":
                    keys = {k: -1.0 for k in self.task_units.get(v, {})}
                else:
                    keys = self.source_keys(sources)
                covered, total, ncov = self.coverage(keys, v)
                removable, nrounds = self.removable_rounds(keys, v)
                sent = self.source_bytes(sources)
                used = sum(n for k, n in self.task_units.get(v, {}).items() if k in keys)
                done = [self.tasks[s].get("done_s") for s in sources if s in self.tasks]
                done = [d for d in done if d is not None]
                cross = [s for s in sources if s in self.tasks
                         and self.tasks[s].get("owner") != self.tasks[v].get("owner")]
                rows.append({
                    "run": self.label, "task": v, "policy": name, "n_sources": len(sources),
                    "has_pred": has_pred,
                    "covered": covered, "total": total, "sent_bytes": sent, "used_bytes": used,
                    "removable": removable, "rounds": nrounds,
                    "cross_owner": 1 if cross else 0,
                    "gap_s": ((start - max(done)) / 1000.0) if done else None,
                })
        return rows

    def successor_rows(self) -> list[dict]:
        """One row per task that HAS a predecessor -- including the ones that read nothing at all.

        This is the population the whole question is about, and it is the one the pair table cannot
        show: a successor that issues no reads has no pairs, yet it is the most informative case
        there is.  When the board hands a successor to the agent that just finished its predecessor,
        that agent still holds the bytes in its own context and re-reads nothing; the transfer
        question only arises when the successor lands somewhere else."""
        rows = []
        for tid, task in self.tasks.items():
            preds = [b for b in self.deps.get(tid, []) if b in self.tasks]
            if not preds:
                continue
            owners = {self.tasks[b].get("owner") for b in preds}
            claimed = task.get("claim_s") is not None
            same = task.get("owner") in owners if claimed else None
            keys = self.source_keys(preds)
            covered, total, _ = self.coverage(keys, tid)
            removable, nrounds = self.removable_rounds(keys, tid)
            rows.append({
                "run": self.label, "task": tid, "subject": task.get("subject", "")[:40],
                "claimed": 1 if claimed else 0,
                "completed": 1 if task.get("done_s") is not None else 0,
                "owner": task.get("owner"), "pred_owners": sorted(o for o in owners if o),
                "same_owner": (1 if same else 0) if claimed else None,
                "reads": len(self.task_items.get(tid, [])),
                "bytes": self.task_bytes.get(tid, 0),
                "pred_bytes": self.source_bytes(preds),
                "covered": covered, "total": total,
                "removable": removable, "rounds": nrounds,
            })
        return rows

    # ---- validity --------------------------------------------------------------------------
    def validity(self, rows: list[dict]) -> dict:
        """The checks that decide whether any p-value from this run means anything."""
        non_adjacent = [r for r in rows if r["relation"] in ("sibling", "unrelated")]
        edges = [r for r in rows if r["relation"] == "edge"]
        values = [r["coverage"] for r in non_adjacent]
        mean = sum(values) / len(values) if values else 0.0
        var = sum((v - mean) ** 2 for v in values) / len(values) if values else 0.0
        resolvable = unresolved = 0
        for index in range(len(self.red.items)):
            for key, nbytes in self.red.item_units[index][self.gran]:
                if key is None:
                    unresolved += nbytes
                else:
                    resolvable += nbytes
        total_reads = len(self.red.items)
        cell = Counter((r["is_edge"], r["same_corpus"]) for r in rows)
        return {
            "run": self.label,
            "V1_nonadjacent_sd": round(var ** 0.5, 4),
            "V2_nonadjacent_base_rate": round(mean, 3),
            "V3_executed_yield": (round(len(edges) / self.n_edges, 2) if self.n_edges else None),
            "V4_same_owner_share_of_edges": (round(sum(r["same_owner"] for r in edges) / len(edges), 2)
                                             if edges else None),
            "V5_resolvable_byte_share": (round(resolvable / (resolvable + unresolved), 3)
                                         if (resolvable + unresolved) else None),
            "V6_unattributed_read_share": (round(self.unattributed.get("dropped", 0) / total_reads, 3)
                                           if total_reads else None),
            "V7_rho_edge_desc": round(spearman([r["is_edge"] for r in rows],
                                               [r["desc_jaccard"] for r in rows]), 2),
            "V7_rho_edge_gap": round(spearman([r["is_edge"] for r in rows],
                                              [r["gap_s"] for r in rows]), 2),
            "V8_cells": {f"edge={a},corpus={b}": n for (a, b), n in sorted(cell.items())},
            "edges_with_control": sum(1 for r in self.matched(rows) if r.get("delta") is not None),
            "n_pairs": len(rows), "n_edges": len(edges),
        }

    def meta(self) -> dict:
        teammate_rounds = sum(1 for key in self.round_calls if self.red.agent_kind.get(key[0]) == "teammate")
        return {"run": self.label, "trace": str(self.path), "model": self.red.model,
                "tasks": len(self.tasks), "edges": self.n_edges,
                "blocked_tasks": sum(1 for t in self.tasks.values() if t.get("deps")),
                "owners": len({t.get("owner") for t in self.tasks.values() if t.get("owner")}),
                "teammates": self.red.teammates_spawned, "status": self.red.status,
                "wall_s": round(self.red.wall_ms / 1000.0, 1),
                "read_items": len(self.red.items),
                "attributed": sum(1 for i in self.red.items if i.get("task") not in (None, "__lead__")),
                "unattributed": dict(self.unattributed),
                "teammate_rounds": teammate_rounds}


# ----------------------------------------------------------------------------- statistics
def spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return 0.0

    def rank(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = average
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def cluster_bootstrap(per_run: list[list[float]], reps: int = 10000,
                      seed: int = 20260916) -> tuple[float, float, float]:
    """Resample RUNS with replacement: pairs inside a run are not independent."""
    flat = [v for run in per_run for v in run]
    if not flat:
        return (0.0, 0.0, 0.0)
    point = sum(flat) / len(flat)
    rng = random.Random(seed)
    means = []
    n = len(per_run)
    for _ in range(reps):
        sample = [v for _ in range(n) for v in per_run[rng.randrange(n)]]
        if sample:
            means.append(sum(sample) / len(sample))
    means.sort()
    if not means:
        return (point, point, point)
    return (point, means[int(0.025 * len(means))], means[int(0.975 * len(means)) - 1])


def sign_flip_p(per_run: list[list[float]], reps: int = 20000, seed: int = 0x5EED) -> tuple[float, float]:
    """Run-clustered sign-flip randomisation on matched deltas; returns (mean delta, two-sided p)."""
    runs = [r for r in per_run if r]
    if not runs:
        return (0.0, 1.0)
    flat = [v for run in runs for v in run]
    observed = sum(flat) / len(flat)
    rng = random.Random(seed)
    hits = 0
    for _ in range(reps):
        total, n = 0.0, 0
        for run in runs:
            sign = 1 if rng.random() < 0.5 else -1
            for v in run:
                total += sign * v
                n += 1
        if n and abs(total / n) >= abs(observed) - 1e-12:
            hits += 1
    return (observed, (hits + 1) / (reps + 1))


def binomial_p(successes: int, n: int) -> float:
    """Two-sided exact sign test, hand-rolled (no scipy on this host)."""
    if n == 0:
        return 1.0
    from math import comb
    tail = sum(comb(n, k) for k in range(0, min(successes, n - successes) + 1))
    return min(1.0, 2.0 * tail / (2 ** n))


# ----------------------------------------------------------------------------- tables
def tables(runs: list[DagRun], perms: int) -> str:
    out: list[str] = []
    all_pairs = {run.label: run.pairs() for run in runs}

    out.append("## Table A. Runs\n")
    out.append("| run | model | tasks | edges | executed | owners | reads | attributed | t-rounds | wall s | status |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        m = run.meta()
        ex = sum(1 for r in all_pairs[run.label] if r["relation"] == "edge")
        out.append(f"| {m['run']} | {m['model']} | {m['tasks']} | {m['edges']} | {ex} | "
                   f"{m['owners']} | {m['read_items']} | {m['attributed']} | {m['teammate_rounds']} | "
                   f"{m['wall_s']} | {m['status']} |")

    out.append("\n## Table V. Validity (read this before any p-value)\n")
    out.append("| run | non-adj sd | non-adj base | exec yield | same-owner edges | resolvable bytes | "
               "unattributed | rho(edge,desc) | rho(edge,gap) | edges w/ control |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for run in runs:
        v = run.validity(all_pairs[run.label])
        out.append(f"| {v['run']} | {v['V1_nonadjacent_sd']} | {v['V2_nonadjacent_base_rate']} | "
                   f"{v['V3_executed_yield']} | {v['V4_same_owner_share_of_edges']} | "
                   f"{v['V5_resolvable_byte_share']} | {v['V6_unattributed_read_share']} | "
                   f"{v['V7_rho_edge_desc']} | {v['V7_rho_edge_gap']} | {v['edges_with_control']} |")
    out.append("\nV1 sd = 0 means every non-adjacent pair in the run has the identical value, so the "
               "workload -- not the graph -- fixed the answer and the run carries no information. "
               "V2 outside [0.05, 0.95] is a ceiling/floor. V4 near 1.0 means the successors were the "
               "same agent, which already holds the bytes for free. V5 below 0.70 means bash "
               "provenance is too thin for `range` and `cdc256` should carry the headline.")

    flat = [r for rows in all_pairs.values() for r in rows]
    if not flat:
        out.append("\n_No eligible task pairs with read content; nothing to compare._")
        return "\n".join(out)

    succ = [row for run in runs for row in run.successor_rows()]
    if succ:
        out.append("\n## Table S. Successor tasks: what an agent that inherits a dependency does\n")
        out.append("| successors | claimed | same agent as a predecessor | reads issued | "
                   "read nothing at all |")
        out.append("|---|---|---|---|---|")
        claimed = [r for r in succ if r["claimed"]]
        same = [r for r in claimed if r["same_owner"]]
        cross = [r for r in claimed if not r["same_owner"]]
        out.append(f"| {len(succ)} | {len(claimed)} | {len(same)} | "
                   f"{sum(r['reads'] for r in claimed)} | "
                   f"{sum(1 for r in claimed if r['reads'] == 0)} |")
        out.append("\n| group | n | mean reads | mean bytes read | mean predecessor bytes | "
                   "byte coverage | removable rounds |")
        out.append("|---|---|---|---|---|---|---|")
        for name, group in (("same agent as predecessor", same), ("different agent", cross)):
            if not group:
                out.append(f"| {name} | 0 | - | - | - | - | - |")
                continue
            covered = sum(r["covered"] for r in group)
            total = sum(r["total"] for r in group)
            rem = sum(r["removable"] for r in group)
            rnd = sum(r["rounds"] for r in group)
            out.append(f"| {name} | {len(group)} | "
                       f"{sum(r['reads'] for r in group) / len(group):.1f} | "
                       f"{sum(r['bytes'] for r in group) / len(group):,.0f} | "
                       f"{sum(r['pred_bytes'] for r in group) / len(group):,.0f} | "
                       f"{pct(covered, total)} | {rem}/{rnd} ({pct(rem, rnd)}) |")
        out.append("\nA successor that reads nothing has already got what it needed -- it is the "
                   "same agent, and its own context still holds the predecessor's bytes. Those rows "
                   "are the free case; only the 'different agent' rows are what a KV transfer would "
                   "have to buy.")

    out.append("\n## Table B. Task pairs by relation (pooled)\n")
    out.append("| relation | pairs | same owner | any shared path | path Jaccard | byte coverage | "
               "removable rounds | desc names pred file |")
    out.append("|---|---|---|---|---|---|---|---|")
    for relation in RELATIONS:
        rows = [r for r in flat if r["relation"] == relation]
        if not rows:
            out.append(f"| {relation} | 0 | - | - | - | - | - | - |")
            continue
        covered = sum(r["covered"] for r in rows)
        total = sum(r["total"] for r in rows)
        rem = sum(r["removable"] for r in rows)
        rnd = sum(r["rounds"] for r in rows)
        out.append(f"| {relation} | {len(rows)} | {pct(sum(r['same_owner'] for r in rows), len(rows))} | "
                   f"{pct(sum(r['binary'] for r in rows), len(rows))} | "
                   f"{sum(r['jaccard'] for r in rows) / len(rows):.2f} | {pct(covered, total)} | "
                   f"{rem}/{rnd} ({pct(rem, rnd)}) | "
                   f"{pct(sum(r['desc_names_pred_file'] for r in rows), len(rows))} |")

    # The workloads are NOT interchangeable: W3 and W4 are controls built to show zero (W3's edges
    # span disjoint corpora by construction, W4's board edges deliberately contradict its own text).
    # Pooling them with the aligned workloads cancels the effect the aligned ones carry, so the
    # contrast is reported per group and the pooled row is kept only for completeness.
    groups = [("aligned boards (W1, W2, W5)", ("W1", "W2", "W5")),
              ("board contradicts the text (W4 placebo)", ("W4",)),
              ("edges span disjoint corpora (W3 control)", ("W3",)),
              ("all workloads pooled", None)]
    out.append("\n## Table C. Matched contrast: each edge against a control with the SAME successor\n")
    out.append("| group | measure | edges matched | mean delta | 95% CI | sign-flip p | "
               "runs positive |")
    out.append("|---|---|---|---|---|---|---|")
    for gname, prefixes in groups:
        chosen = [r for r in runs
                  if prefixes is None or (r.label or "").split("-")[0] in prefixes]
        for field, name in (("coverage", "byte coverage"), ("round_share", "removable-round share")):
            per_run, n_matched = [], 0
            for run in chosen:
                deltas = [r["delta"] for r in run.matched(all_pairs[run.label], field)
                          if r.get("delta") is not None]
                n_matched += len(deltas)
                if deltas:
                    per_run.append(deltas)
            if not per_run:
                out.append(f"| {gname} | {name} | 0 | - | - | - | - |")
                continue
            observed, p = sign_flip_p(per_run, reps=perms)
            point, lo, hi = cluster_bootstrap(per_run)
            positive = sum(1 for run in per_run if sum(run) / len(run) > 0)
            out.append(f"| {gname} | {name} | {n_matched} | {point:+.3f} | "
                       f"[{lo:+.3f}, {hi:+.3f}] | {p:.4f} | {positive}/{len(per_run)} |")

    out.append("\n### Table C2. Per-workload edge vs unrelated (raw, not matched)\n")
    out.append("| workload | edges | edge byte coverage | unrelated byte coverage | "
               "edge removable rounds | unrelated removable rounds |")
    out.append("|---|---|---|---|---|---|")
    by_workload: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for run in runs:
        prefix = (run.label or "").split("-")[0]
        for row in all_pairs[run.label]:
            by_workload[prefix][row["relation"]].append(row)
    for prefix in sorted(by_workload):
        edges = by_workload[prefix]["edge"]
        unrel = by_workload[prefix]["unrelated"]

        def cov(rows):
            c = sum(r["covered"] for r in rows)
            t = sum(r["total"] for r in rows)
            return pct(c, t)

        def rem(rows):
            a = sum(r["removable"] for r in rows)
            b = sum(r["rounds"] for r in rows)
            return pct(a, b)

        out.append(f"| {prefix} | {len(edges)} | {cov(edges)} | {cov(unrel)} | "
                   f"{rem(edges)} | {rem(unrel)} |")
    out.append("\nA control is a non-adjacent, non-ancestor task that finished before the same "
               "successor started. Holding the successor fixed removes its read volume, its corpus "
               "and its position in the run from the comparison.")

    out.append("\n## Table D. Prefetch policies, over tasks that HAVE a predecessor\n")
    out.append("| policy | tasks | byte recall | round recall | precision (used/sent) | "
               "sent KB median | tokens per round removed | gap s median |")
    out.append("|---|---|---|---|---|---|---|---|")
    all_policy_rows = [row for run in runs for row in run.policy_rows()]
    policy_rows = [r for r in all_policy_rows if r.get("has_pred")]
    order = ["direct_pred", "text_pred", "ancestors", "all_earlier", "same_owner", "lead",
             "random", "oracle"]
    for policy in order:
        rows = [r for r in policy_rows if r["policy"] == policy]
        if not rows:
            continue
        out.append(policy_line(policy, rows))
    out.append("\nThe same policies over EVERY reading task, predecessor or not -- the view a "
               "retention policy that does not look at the board would see:\n")
    out.append("| policy | tasks | byte recall | round recall | precision | sent KB median | "
               "tokens per round removed | gap s median |")
    out.append("|---|---|---|---|---|---|---|---|")
    for policy in order:
        rows = [r for r in all_policy_rows if r["policy"] == policy]
        if rows:
            out.append(policy_line(policy, rows))

    out.append("\nCross-owner successors only (the transferable case):\n")
    out.append("| policy | tasks | byte recall | round recall | precision | sent KB median | "
               "tokens per round removed | gap s median |")
    out.append("|---|---|---|---|---|---|---|---|")
    for policy in order:
        rows = [r for r in policy_rows if r["policy"] == policy and r["cross_owner"]]
        if rows:
            out.append(policy_line(policy, rows))
    out.append("\n`direct_pred` sending nothing means the predecessor itself read nothing -- which "
               "happens exactly when the predecessor was the same agent and had the bytes already.")

    out.append("\n## Table E. What a removable round is worth\n")
    out.append(f"One round = {FIXED_ROUND_S} s fixed + prefill + decode; sending N bytes costs "
               f"N/{CHARS_PER_TOKEN:.0f} tokens x {MS_PER_UNCACHED_TOKEN} ms of prefill.\n")
    out.append("| policy | removable rounds | saved s (rounds x fixed) | sent tokens | "
               "prefill s | net s |")
    out.append("|---|---|---|---|---|---|")
    for policy in order:
        rows = [r for r in policy_rows if r["policy"] == policy]
        if not rows:
            continue
        removable = sum(r["removable"] for r in rows)
        sent_tokens = sum(r["sent_bytes"] for r in rows) / CHARS_PER_TOKEN
        saved = removable * FIXED_ROUND_S
        paid = sent_tokens * MS_PER_UNCACHED_TOKEN / 1000.0
        out.append(f"| {policy} | {removable} | {saved:.1f} | {sent_tokens:,.0f} | {paid:.2f} | "
                   f"{saved - paid:+.1f} |")
    return "\n".join(out)


def policy_line(policy: str, rows: list[dict]) -> str:
    covered = sum(r["covered"] for r in rows)
    total = sum(r["total"] for r in rows)
    removable = sum(r["removable"] for r in rows)
    nrounds = sum(r["rounds"] for r in rows)
    sent = sum(r["sent_bytes"] for r in rows)
    used = sum(r["used_bytes"] for r in rows)
    sizes = sorted(r["sent_bytes"] for r in rows)
    gaps = sorted(r["gap_s"] for r in rows if r["gap_s"] is not None)
    gap_txt = f"{gaps[len(gaps) // 2]:.0f}" if gaps else "-"
    per_round = f"{sent / CHARS_PER_TOKEN / removable:,.0f}" if removable else "-"
    return (f"| {policy} | {len(rows)} | {pct(covered, total)} | "
            f"{removable}/{nrounds} ({pct(removable, nrounds)}) | {pct(used, sent)} | "
            f"{sizes[len(sizes) // 2] / 1024:.1f} | {per_round} | {gap_txt} |")


def collect(targets: list[str]) -> list[Path]:
    """Trace files only: a run trace has exactly one dot in its name (latency_breakdown's rule)."""
    paths: list[Path] = []
    for target in targets:
        q = Path(target)
        if q.is_dir():
            paths.extend(sorted(p for p in q.rglob("run_*.jsonl") if p.name.count(".") == 1))
        elif q.is_file():
            paths.append(q)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+")
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--git-rev", default=None)
    parser.add_argument("--exclude", default=r"traces/|\.task_outputs")
    parser.add_argument("--no-bash", action="store_true")
    parser.add_argument("--gran", default="range", choices=["whole", "range", "line", "cdc256", "cdc1k"])
    parser.add_argument("--perms", type=int, default=2000)
    parser.add_argument("--tables", action="store_true")
    parser.add_argument("--pairs", action="store_true", help="dump the per-pair rows")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    exclude = re.compile(args.exclude) if args.exclude else None
    runs: list[DagRun] = []
    for path in collect(args.targets):
        try:
            run = DagRun(path, Path(args.repo), args.git_rev, exclude, not args.no_bash, args.gran)
        except Exception as exc:  # a partial trace should not kill the batch
            print(f"[dag_redundancy] skip {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        runs.append(run)
    if not runs:
        print("no runs", file=sys.stderr)
        return 1
    zero = [r for r in runs if r.n_edges == 0]
    if len(zero) == len(runs):
        print(f"[dag_redundancy] every run has ZERO dependency edges ({len(runs)} runs): "
              "no lift can be computed. Use structural proxies or rerun with a stronger level.",
              file=sys.stderr)
    if args.tables or not args.pairs:
        print(tables(runs, args.perms))
    if args.pairs:
        for run in runs:
            for row in run.pairs():
                print(json.dumps(row))
    if args.json:
        payload = {"runs": [run.meta() for run in runs],
                   "validity": [run.validity(run.pairs()) for run in runs],
                   "successors": [row for run in runs for row in run.successor_rows()],
                   "pairs": [p for run in runs for p in run.pairs()],
                   "policies": [p for run in runs for p in run.policy_rows()]}
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
