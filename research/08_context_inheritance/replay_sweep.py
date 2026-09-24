#!/usr/bin/env python3
"""E1-R / E2-R / E3-R: the three arms, the byte budgets and the eviction policies, recomputed over
real published agent trajectories.

    python3 research/08_context_inheritance/replay_sweep.py research/08_context_inheritance/data/realworld/open_swe --md
    python3 research/08_context_inheritance/replay_sweep.py research/08_context_inheritance/data/realworld/open_swe --stage arms --md

No API calls: every number is a deterministic recomputation over runs that real harnesses really
made on real benchmarks.  That is the point -- the Sept 17-18 study's arms were measured on prompts
we wrote ourselves, so its effect sizes cannot be separated from the workloads chosen to show them.

THE UNIT is a SUCCESSOR ROUND: a round that reads something and has at least one earlier reading
round to inherit from.  For such a round the three arms are

    none        hold nothing; every read costs a round
    dag         hold the immediately preceding round's observations
    ancestors   hold every earlier round's observations

WHICH DIRECTION IS THE TREATMENT DIFFERS BETWEEN THE TWO HALVES OF THIS STUDY, and conflating them
would be a real error.  Live, a fresh teammate starts with nothing and inheritance ADDS context, so
the baseline is `none`.  In replay the agent is continuing its own trajectory and already holds
everything, so a budget REMOVES context and the reference cell is `ancestors` at an unlimited
budget.  Both measure the same underlying quantity -- what it is worth to be holding an item when
round t arrives -- but only the `none` column is directly comparable to the live baseline.

COVERAGE IS MEASURED ON CONTENT, NOT ON PATHS.  An agent edits the files it reads, so the same span
fetched twice is usually NOT the same bytes.  A cache keyed on path would serve those stale.  The
gap between the two is reported as `stale` because it is a result in its own right: it is most of
what looks like redundancy in a bash-only harness.

Two granularities, both reported:
    item   whole observations, the unit an engine can actually hold or drop
    line   per-line digests, the ceiling a content-addressed store could reach
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
import evict_policies as ep                                                     # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ARMS = ["none", "dag", "ancestors"]
ARM_LABEL = {"none": "none (hold nothing)", "dag": "dag (direct predecessor)",
             "ancestors": "ancestors (all earlier rounds)"}
COVER_ITEM = 0.95                       # an item counts as held at 95%, as in dag_redundancy.py


def key_of(item: dict) -> tuple:
    return tuple(tuple(x) if isinstance(x, list) else x for x in item["key"])


class Trajectory:
    """One real agent run, indexed for the counterfactual."""

    def __init__(self, record: dict):
        self.rec = record
        self.harness = record.get("harness")
        self.repo = record.get("repo")
        self.resolved = bool(record.get("resolved"))
        self.rounds = {r["idx"]: r for r in record.get("rounds", [])}
        self.by_round: dict[int, list[dict]] = defaultdict(list)
        for item in record["items"]:
            self.by_round[item["round"]].append(item)
        self.read_rounds = sorted(self.by_round)

    # ---------------------------------------------------------------- the pool offered to a round
    def pool(self, t: int, arm: str) -> list[dict]:
        if arm == "none":
            return []
        earlier = [r for r in self.read_rounds if r < t]
        if not earlier:
            return []
        source = [earlier[-1]] if arm == "dag" else earlier
        seen, out = set(), []
        for r in source:                                # oldest first: a handoff that reads like a
            for item in self.by_round[r]:               # coherent history is trusted, a stitched
                k = key_of(item)                        # one is re-read (2026-09-16)
                if k in seen:
                    continue
                seen.add(k)
                out.append(item)
        return out

    def source_reads(self, t: int, arm: str) -> list[dict]:
        """Every read the pool was built from, in order, repeats included."""
        earlier = [r for r in self.read_rounds if r < t]
        source = [earlier[-1]] if arm == "dag" and earlier else earlier
        return [item for r in source for item in self.by_round[r]]

    def items_for(self, pool: list[dict], t: int, reads: list[dict]) -> list[ep.Item]:
        """Pool entries -> priced retention candidates.

        `useful_bytes` and `next_use` are what make the two CEILING policies mean anything: the
        byte-density oracle ranks by how much of an item round t will actually use, and Belady by
        how far in the future it is next needed.  Left unpopulated they collapse to constants and
        the 'ceiling' silently becomes an arbitrary ordering that loses to LRU -- which is what the
        first run of this grid did.

        `freq` and `last_ms` come from `reads`, the rounds before t.  They used to be counted over
        the de-duplicated pool, which made every count 1 and every item's recency its first read,
        so LFU and LRU both ran as FIFO (fixed 2026-09-23)."""
        freq: dict[tuple, int] = defaultdict(int)
        last: dict[tuple, int] = {}
        for item in reads:
            freq[(key_of(item), item["sha"])] += 1
            last[(key_of(item), item["sha"])] = item["round"]
        want_shas = {i["sha"] for i in self.by_round.get(t, [])}
        future: dict[str, int] = {}
        for r in self.read_rounds:
            if r < t:
                continue
            for item in self.by_round[r]:
                future.setdefault(item["sha"], r)
        earlier = [r for r in self.read_rounds if r < t]
        direct = earlier[-1] if earlier else None
        out = []
        for item in pool:
            k = key_of(item)
            out.append(ep.Item(
                key=(k, item["sha"]),                   # content identity: an edited span is a
                nbytes=item["bytes"],                   # different item, not the same one
                round_idx=item["round"],
                first_ms=float(item["round"]),
                last_ms=float(last.get((k, item["sha"]), item["round"])),
                freq=freq.get((k, item["sha"]), 1),
                # there is no task graph in a single-agent trajectory, so the only graph-like
                # distinction available is "came from the immediately preceding round" vs earlier
                tier=0 if item["round"] == direct else 1,
                out_tokens=int(item.get("out_chars", 0) / ep.CHARS_PER_TOKEN),
                next_use=future.get(item["sha"]),
                useful_bytes=item["bytes"] if item["sha"] in want_shas else 0,
                locate_s=ep.FIXED_ROUND_S if item.get("located_by") is not None else 0.0))
        return out

    # ---------------------------------------------------------------- what a round would have kept
    def context(self, t: int, arm: str):
        """Everything that does not depend on the policy or the budget.  Built once per
        (round, arm) and reused across the grid; rebuilding it per cell made the 80-cell sweep
        quadratic in rounds for no reason."""
        want = self.by_round[t]
        if not want:
            return None
        pool = self.pool(t, arm)
        reads = self.source_reads(t, arm)
        cands = self.items_for(pool, t, reads)
        # one Item per read over the pool's keys (the pool keeps a re-read span's first version),
        # so the online caches see every hit
        by_key = {c.key: c for c in cands}
        accesses = []
        for item in reads:
            c = by_key.get((key_of(item), item["sha"]))
            if c is not None:
                accesses.append(ep.Item(key=c.key, nbytes=c.nbytes, round_idx=item["round"],
                                        first_ms=float(item["round"]),
                                        last_ms=float(item["round"]),
                                        out_tokens=c.out_tokens, locate_s=c.locate_s))
        return want, pool, cands, accesses

    def evaluate(self, t: int, arm: str, policy: str, budget: int | None,
                 ctx=None) -> dict | None:
        ctx = ctx or self.context(t, arm)
        if ctx is None:
            return None
        want, pool, cands, accesses = ctx
        kept_keys = ep.retained(cands, policy, budget, accesses=accesses) if cands else set()
        kept = [c for c in cands if c.key in kept_keys]
        sent = sum(c.nbytes for c in kept)

        held_sha = {c.key[1] for c in kept}
        held_key = {c.key[0] for c in kept}
        held_lines: set[str] = set()
        for item in pool:
            if (key_of(item), item["sha"]) in kept_keys:
                held_lines |= set(item.get("lines") or [])

        total = sum(i["bytes"] for i in want)
        covered = sum(i["bytes"] for i in want if i["sha"] in held_sha)
        stale = sum(i["bytes"] for i in want
                    if i["sha"] not in held_sha and key_of(i) in held_key)
        want_lines, cov_lines = 0, 0
        for i in want:
            lines = i.get("lines") or []
            want_lines += len(lines)
            cov_lines += sum(1 for h in lines if h in held_lines)

        # a round disappears only if EVERYTHING it would have fetched is already held -- rounds are
        # quantised, bytes are not, which is the finding this whole line of work keeps re-deriving
        removable = bool(total) and covered / total >= COVER_ITEM
        # the same question at line granularity: would a CONTENT-ADDRESSED store have removed it?
        # Whole-observation identity is all-or-nothing per item, so it leaves no partial coverage to
        # exploit; lines do.  The gap between these two columns is the value of the retention unit.
        removable_line = bool(want_lines) and cov_lines / want_lines >= COVER_ITEM
        used = sum(c.nbytes for c in kept if c.key[1] in {i["sha"] for i in want})
        saved_s = sum(c.recovery_seconds for c in cands
                      if c.key[1] in {i["sha"] for i in want}) if removable else 0.0
        rnd = self.rounds.get(t, {})
        pred = self.read_rounds[self.read_rounds.index(t) - 1] if self.read_rounds.index(t) else None
        return {
            "harness": self.harness, "repo": self.repo, "resolved": self.resolved,
            "instance": self.rec.get("instance_id"), "round": t, "arm": arm,
            "policy": policy, "budget": budget,
            "pool_items": len(cands), "pool_bytes": sum(c.nbytes for c in cands),
            "sent_bytes": sent, "used_bytes": used,
            "want_bytes": total, "covered_bytes": covered, "stale_bytes": stale,
            "want_lines": want_lines, "covered_lines": cov_lines,
            "removable": int(removable), "removable_line": int(removable_line),
            "saved_s": saved_s,
            "ctx_chars": rnd.get("ctx_chars", 0), "out_chars": rnd.get("out_chars", 0),
            "from_direct": sum(c.nbytes for c in kept if c.round_idx == pred) if pred is not None else 0,
        }

    def successor_rounds(self) -> list[int]:
        return [t for i, t in enumerate(self.read_rounds) if i > 0]


def load(targets: list[str]) -> list[Trajectory]:
    out = []
    for target in targets:
        path = Path(target)
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        for f in files:
            with f.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        out.append(Trajectory(json.loads(line)))
    return out


def pct(num: float, den: float) -> str:
    return f"{100 * num / den:.1f}%" if den else "--"


def boot(a: list[float], b: list[float], reps: int = 4000, seed: int = 20260918):
    """Bootstrap of mean(a) - mean(b); same seed and shape as inherit_analyze.boot."""
    if not a or not b:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    point = st.mean(a) - st.mean(b)
    diffs = []
    for _ in range(reps):
        ra = [a[rng.randrange(len(a))] for _ in a]
        rb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(st.mean(ra) - st.mean(rb))
    diffs.sort()
    return point, diffs[int(0.025 * reps)], diffs[int(0.975 * reps)]


# ----------------------------------------------------------------------------------- stage: arms
def stage_arms(trajs: list[Trajectory], policy: str = "lru") -> str:
    rows = defaultdict(list)
    for traj in trajs:
        for t in traj.successor_rounds():
            for arm in ARMS:
                r = traj.evaluate(t, arm, policy, None)
                if r:
                    rows[arm].append(r)
    out = ["## E1-R — the three arms on real trajectories, unlimited budget\n",
           "One row per arm, pooled over successor rounds. `byte recall` is the share of what the "
           "round went on to read that was already held; `round recall` the share of rounds that "
           "could have disappeared entirely.\n",
           "| arm | successor rounds | sent KB (median) | byte recall | line recall | "
           "round recall | precision (used/sent) | stale |",
           "|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        g = rows[arm]
        if not g:
            continue
        sent = st.median([r["sent_bytes"] for r in g]) / 1024
        out.append(
            f"| {ARM_LABEL[arm]} | {len(g)} | {sent:.1f} | "
            f"{pct(sum(r['covered_bytes'] for r in g), sum(r['want_bytes'] for r in g))} | "
            f"{pct(sum(r['covered_lines'] for r in g), sum(r['want_lines'] for r in g))} | "
            f"{sum(r['removable'] for r in g)}/{len(g)} = "
            f"{pct(sum(r['removable'] for r in g), len(g))} | "
            f"{pct(sum(r['removable_line'] for r in g), len(g))} | "
            f"{pct(sum(r['used_bytes'] for r in g), sum(r['sent_bytes'] for r in g))} | "
            f"{pct(sum(r['stale_bytes'] for r in g), sum(r['want_bytes'] for r in g))} |")

    out.append("\n### By harness — the tool table decides how much redundancy there is\n")
    out.append("| harness | trajectories | rounds/traj (median) | prompt chars, median successor "
               "round | byte recall (ancestors) | round recall (ancestors) | stale |")
    out.append("|---|---|---|---|---|---|---|")
    for harness in sorted({t.harness for t in trajs}):
        sub = [t for t in trajs if t.harness == harness]
        g = [r for r in rows["ancestors"] if r["harness"] == harness]
        if not g:
            continue
        ctx = [r["ctx_chars"] for r in g if r["ctx_chars"]]
        out.append(
            f"| {harness} | {len(sub)} "
            f"| {st.median([t.rec['n_rounds'] for t in sub]):.0f} "
            f"| {st.median(ctx):,.0f} " if ctx else
            f"| {harness} | {len(sub)} | {st.median([t.rec['n_rounds'] for t in sub]):.0f} | -- ")
        out[-1] += (
            f"| {pct(sum(r['covered_bytes'] for r in g), sum(r['want_bytes'] for r in g))} "
            f"| {pct(sum(r['removable'] for r in g), len(g))} "
            f"| {pct(sum(r['stale_bytes'] for r in g), sum(r['want_bytes'] for r in g))} |")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ stage: cross-task (by repo)
def cross_grid_tables(rows, budgets, policies, groups) -> str:
    """E2-R / E3-R on the cross-task population: budgets down the columns, policies down the rows."""
    def table(title, cell):
        head = "| policy | " + " | ".join(
            f"{b // 1024} KB" if b else "unlimited" for b in budgets) + " |"
        out = [f"\n### {title}\n", head, "|---" * (len(budgets) + 1) + "|"]
        for policy in policies:
            out.append(f"| {ep.LABEL.get(policy, policy)} | " +
                       " | ".join(cell(rows[(policy, b)]) for b in budgets) + " |")
        return out

    out = [f"## E2-R / E3-R — budget x eviction policy on the cross-task population\n",
           f"{len(groups)} groups, "
           f"{sum(len(v) - 1 for v in groups.values())} successor tasks. Arm: all earlier tasks in "
           "the group, cut down to the budget by the policy.\n"]
    out += table("Removable rounds (share of the successor's reading rounds that disappear)",
                 lambda g: pct(sum(r["removable"] for r in g), sum(r["rounds"] for r in g)) if g else "--")
    out += table("Byte recall",
                 lambda g: pct(sum(r["covered"] for r in g), sum(r["want"] for r in g)) if g else "--")
    out += table("Precision (used / sent)",
                 lambda g: pct(sum(r["used"] for r in g), sum(r["sent"] for r in g)) if g else "--")
    out += table("Median KB handed over",
                 lambda g: f"{st.median([r['sent'] for r in g]) / 1024:.1f}" if g else "--")
    return "\n".join(out) + "\n"


def stage_cross(trajs: list[Trajectory], policy: str = "lru",
                budgets: list[int | None] | None = None,
                policies: list[str] | None = None) -> str:
    """The population that actually maps onto inheritance: DIFFERENT tasks on the SAME repository.

    Within one trajectory the agent already holds everything it read, so a re-read there measures
    the agent wasting a round on content it had -- an upper bound on what inheritance can remove
    from a continuing agent, but not the multi-agent question.  Two SWE-bench instances on the same
    repo are solved by two agents that share nothing, so whatever the second reads that the first
    already read is genuinely removable by handing the bytes over.  That is the real-data counterpart
    of the s15 team setting, and it is directly comparable to the 52%-byte / 63.8%-round base rate
    the ctx512k runs measured for an UNRELATED earlier task.

    There is no dependency graph here and none is invented: instances on a repo have no real order,
    so `dag` means the previous instance in a fixed arbitrary order and `ancestors` means all earlier
    ones.  Any effect is therefore a pure base rate -- exactly what an edge has to beat to be worth
    anything."""
    budgets = budgets or [None]
    policies_used = policies or [policy]
    groups: dict[str, list[Trajectory]] = defaultdict(list)
    for traj in trajs:
        if traj.repo:
            groups[traj.repo].append(traj)
    groups = {k: sorted(v, key=lambda t: str(t.rec.get("instance_id")))
              for k, v in groups.items() if len(v) > 1}

    rows: dict[tuple, list[dict]] = defaultdict(list)
    excluded = 0
    for repo, members in groups.items():
        # everything each trajectory read, content-keyed, plus the same at line granularity
        shas = [{i["sha"]: i["bytes"] for i in t.rec["items"]} for t in members]
        paths = [{tuple(i["key"]) if not isinstance(i["key"], list) else tuple(
            tuple(x) if isinstance(x, list) else x for x in i["key"]) for i in t.rec["items"]}
            for t in members]
        lines = [{h for i in t.rec["items"] for h in (i.get("lines") or [])} for t in members]
        for j in range(1, len(members)):
            succ = members[j]
            # a predecessor that is the SAME benchmark task solved by another model is not a
            # predecessor.  Without this the tau-bench runs pair task 46 with task 46 and the
            # "cross-task" recall is really self-recall.
            # `task_key` where the corpus has one (HAL), otherwise the instance id: the SWE splits
            # are three harnesses over overlapping instance sets, so 11 of 732 pairs are one
            # instance solved twice.  Small, but the same error as tau-bench's and excluded the
            # same way rather than waved off.
            def ident(t):
                return t.rec.get("task_key") or t.rec.get("instance_id")
            same = ident(succ)
            src_ok = [i for i in range(j) if not same or ident(members[i]) != same]
            excluded += (j - len(src_ok))
            if not src_ok:
                continue
            for arm in (("ancestors",) if len(policies_used) > 1 or len(budgets) > 1
                        else ("dag", "ancestors")):
                src = [src_ok[-1]] if arm == "dag" else src_ok
                held_sha = {k: v for i in src for k, v in shas[i].items()}
                held_path = set().union(*(paths[i] for i in src))
                held_line = set().union(*(lines[i] for i in src))
                order = {k: i for i, k in enumerate(held_sha)}
                want_now = {i["sha"] for i in members[j].rec["items"]}
                # The per-item signals a retention policy can rank on, taken ONLY from the reads the
                # engine has seen at the cut point: the source tasks, in order.  The first version
                # counted them over the whole group -- the successor and every later task included
                # -- which told LFU and GDSF what the successor would read, and it offered the online
                # caches each item once, so LRU never refreshed and ran as FIFO (fixed 2026-09-23).
                # Without `located_by` and the round's own decode, `recovery_seconds` falls back to
                # a formula monotone in size and "keep what is costliest to recover" silently
                # becomes "keep the largest".
                stream = [(x, i) for x in src for i in members[x].rec["items"]]
                meta: dict[str, dict] = {}
                rid: dict[tuple, int] = {}
                accesses = []
                for n, (x, i) in enumerate(stream):
                    m = meta.setdefault(i["sha"], {"freq": 0, "located": False, "out": 0,
                                                   "first": n, "last": n})
                    m["freq"] += 1
                    m["last"] = n
                    m["located"] |= i.get("located_by") is not None
                    m["out"] = max(m["out"], int(i.get("out_chars", 0)))
                    accesses.append(ep.Item(
                        key=i["sha"], nbytes=i["bytes"],
                        round_idx=rid.setdefault((x, i["round"]), len(rid)),
                        first_ms=float(n), last_ms=float(n),
                        out_tokens=int(int(i.get("out_chars", 0)) / ep.CHARS_PER_TOKEN),
                        locate_s=ep.FIXED_ROUND_S if i.get("located_by") is not None else 0.0))
                prev = shas[src[-1]]
                cands = [ep.Item(key=k, nbytes=v, round_idx=order[k] // 8,
                                 first_ms=float(meta[k]["first"]), last_ms=float(meta[k]["last"]),
                                 freq=meta[k]["freq"],
                                 # "graph-ordered" on replay means the previous task first: there is
                                 # no real graph, and with no tier set the ordering was plain recency
                                 tier=0 if k in prev else 1,
                                 out_tokens=int(meta[k]["out"] / ep.CHARS_PER_TOKEN),
                                 locate_s=ep.FIXED_ROUND_S if meta[k]["located"] else 0.0,
                                 next_use=0 if k in want_now else None,
                                 useful_bytes=v if k in want_now else 0)
                         for k, v in held_sha.items()]
                for pol in policies_used:
                  for budget in budgets:
                    keep, sent = held_sha, sum(held_sha.values())
                    if budget is not None:
                        kept = ep.retained(cands, pol, budget, accesses=accesses)
                        keep = {k: v for k, v in held_sha.items() if k in kept}
                        sent = sum(keep.values())
                    want = succ.rec["items"]
                    total = sum(i["bytes"] for i in want)
                    covered = sum(i["bytes"] for i in want if i["sha"] in keep)
                    stale = sum(i["bytes"] for i in want if i["sha"] not in keep
                                and (tuple(tuple(x) if isinstance(x, list) else x
                                           for x in i["key"]) in held_path))
                    wl = sum(len(i.get("lines") or []) for i in want)
                    cl = sum(sum(1 for h in (i.get("lines") or []) if h in held_line) for i in want)
                    by_round: dict[int, list[dict]] = defaultdict(list)
                    for i in want:
                        by_round[i["round"]].append(i)
                    removable = sum(
                        1 for _, its in by_round.items()
                        if sum(i["bytes"] for i in its)
                        and sum(i["bytes"] for i in its if i["sha"] in keep)
                        / sum(i["bytes"] for i in its) >= COVER_ITEM)
                    removable_line = sum(
                        1 for _, its in by_round.items()
                        if sum(len(i.get("lines") or []) for i in its)
                        and sum(sum(1 for h in (i.get("lines") or []) if h in held_line)
                                for i in its)
                        / sum(len(i.get("lines") or []) for i in its) >= COVER_ITEM)
                    rows[(pol, budget) if len(policies_used) > 1 or len(budgets) > 1
                         else (arm, budget)].append({
                        "repo": repo, "harness": succ.harness, "rounds": len(by_round),
                        "sent": sent, "want": total, "covered": covered, "stale": stale,
                        "want_lines": wl, "covered_lines": cl,
                        "removable": removable, "removable_line": removable_line,
                        "used": covered})
    if len(budgets) > 1 or len(set(policies_used)) > 1:
        return cross_grid_tables(rows, budgets, policies_used, groups)
    out = [f"## E1-R cross-task — different instances on the SAME repository\n",
           f"{len(groups)} repositories with more than one trajectory, "
           f"{sum(len(v) - 1 for v in groups.values())} successor trajectories. "
           "No dependency graph exists here, so this is the BASE RATE an edge has to beat.\n",
           "| arm | successors | sent KB (median) | byte recall | line recall | round recall | "
           "round recall (line-level) | precision | stale |",
           "|---|---|---|---|---|---|---|---|---|"]
    for arm in ("dag", "ancestors"):
        g = rows[(arm, None)]
        if not g:
            continue
        out.append(
            f"| {arm} ({'previous instance' if arm == 'dag' else 'all earlier instances'}) "
            f"| {len(g)} | {st.median([r['sent'] for r in g]) / 1024:.1f} "
            f"| {pct(sum(r['covered'] for r in g), sum(r['want'] for r in g))} "
            f"| {pct(sum(r['covered_lines'] for r in g), sum(r['want_lines'] for r in g))} "
            f"| {pct(sum(r['removable'] for r in g), sum(r['rounds'] for r in g))} "
            f"| {pct(sum(r['removable_line'] for r in g), sum(r['rounds'] for r in g))} "
            f"| {pct(sum(r['used'] for r in g), sum(r['sent'] for r in g))} "
            f"| {pct(sum(r['stale'] for r in g), sum(r['want'] for r in g))} |")
    out.append(f"\n_Validity: {excluded} predecessor slots were dropped because they were the SAME "
               "benchmark task solved again (a different model, or a different harness split). "
               "Counting them would measure self-recall, not inheritance._")
    return "\n".join(out) + "\n"


# -------------------------------------------------------------------- stage: budgets x policies
def stage_grid(trajs: list[Trajectory], arm: str, policies: list[str],
               budgets: list[int | None]) -> str:
    grid: dict[tuple, list[dict]] = defaultdict(list)
    for n, traj in enumerate(trajs):
        for t in traj.successor_rounds():
            ctx = traj.context(t, arm)
            if ctx is None:
                continue
            for policy in policies:
                for budget in budgets:
                    r = traj.evaluate(t, arm, policy, budget, ctx=ctx)
                    if r:
                        grid[(policy, budget)].append(r)
        if (n + 1) % 25 == 0:
            print(f"[grid] {n + 1}/{len(trajs)} trajectories", file=sys.stderr)

    def table(title: str, cell) -> list[str]:
        head = "| policy | " + " | ".join(
            f"{b // 1024} KB" if b else "unlimited" for b in budgets) + " |"
        out = [f"\n### {title}\n", head, "|---" * (len(budgets) + 1) + "|"]
        for policy in policies:
            cells = [cell(grid[(policy, b)]) for b in budgets]
            out.append(f"| {ep.LABEL.get(policy, policy)} | " + " | ".join(cells) + " |")
        return out

    out = [f"## Budget x policy grid — arm `{arm}`, {len(trajs)} trajectories\n"]
    out += table("Removable rounds, whole-observation identity (the deployable unit)",
                 lambda g: pct(sum(r["removable"] for r in g), len(g)) if g else "--")
    out += table("Removable rounds, line-level content addressing (the ceiling a finer unit buys)",
                 lambda g: pct(sum(r["removable_line"] for r in g), len(g)) if g else "--")
    out += table("Byte recall",
                 lambda g: pct(sum(r["covered_bytes"] for r in g),
                               sum(r["want_bytes"] for r in g)) if g else "--")
    out += table("Precision (used / sent)",
                 lambda g: pct(sum(r["used_bytes"] for r in g),
                               sum(r["sent_bytes"] for r in g)) if g else "--")
    out += table("Latency saved per successor round (s, priced)",
                 lambda g: f"{st.mean([r['saved_s'] for r in g]):.2f}" if g else "--")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--stage", default="all",
                    choices=["all", "arms", "cross", "grid", "crossgrid"])
    ap.add_argument("--arm", default="ancestors", choices=ARMS)
    ap.add_argument("--policies", default=",".join(ep.POLICIES))
    ap.add_argument("--limit", type=int, default=0, help="cap trajectories (0 = all)")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    trajs = load(args.targets)
    if args.limit:
        trajs = trajs[: args.limit]
    print(f"[replay] {len(trajs)} trajectories, "
          f"{sum(len(t.successor_rounds()) for t in trajs)} successor rounds", file=sys.stderr)

    blocks = []
    if args.stage in {"all", "arms"}:
        blocks.append(stage_arms(trajs))
    if args.stage in {"all", "cross"}:
        blocks.append(stage_cross(trajs))
    if args.stage == "crossgrid":
        blocks.append(stage_cross(trajs, budgets=ep.BUDGETS, policies=args.policies.split(",")))
    if args.stage in {"all", "grid"}:
        blocks.append(stage_grid(trajs, args.arm, args.policies.split(","), ep.BUDGETS))
    text = "\n".join(blocks)
    print(text)
    if args.json:
        args.json.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
