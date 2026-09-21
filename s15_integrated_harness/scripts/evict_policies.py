#!/usr/bin/env python3
"""Eviction policies over agent context, and the recovery cost that prices them.

One implementation, two callers: `profile_run.py` uses it live to decide what an inheriting agent
is actually handed, and `replay_sweep.py` uses it offline over real published trajectories.  If the
two ever diverge the live and replayed numbers stop being comparable, which is the whole point of
running both.

THE UNIT OF RETENTION IS ONE WHOLE READ ITEM.  The 2026-09-16 study measured that an agent handed
two non-contiguous slices of one file re-read it 88% of the time while agents handed one contiguous
window re-read 0-12%.  A policy that keeps half a span therefore buys nothing, so no policy here is
allowed to split one.

TWO FAMILIES, because the policies people actually name are not all the same kind of thing:

  ONLINE  (`simulate`)  A real cache.  The access stream is replayed in order; on every insertion
                        that overflows the budget the policy names a victim.  The answer is whatever
                        is still resident at the cut point.  LRU, LFU, FIFO, GDSF, S3-FIFO, SIEVE,
                        sliding-window and random are of this kind -- they are defined by what they
                        do under pressure, not by a global ranking.

  OFFLINE (`select`)    A one-shot ranking with a budget.  The graph-derived policies, the size
                        heuristics, cheapest-whole-rounds and Belady are of this kind: they rank the
                        whole pool at once, and three of them need knowledge (the successor's future
                        reads) that no engine has.  Those are CEILINGS, not deployable policies, and
                        `CEILING` marks them so no table quotes one as a result.

RECOVERY COST is measured, never assumed.  `recovery_seconds` prices what it costs to get an item
back after dropping it, from the baseline trace: the round that fetched it (a model call plus its
decode) plus the tool's own execution.  The subtlety that decides whether "evict what is cheapest to
recover" is a good idea at all: re-reading a file by a path you already know is cheap, but the
glob/grep round that FOUND that path is not, and if the locating round is not charged to the item
the policy will cheerfully evict the single most expensive thing in the pool.  `locate_seconds`
carries that, and `recovery_seconds` includes it.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass, field

# Measured on this harness, 2026-09-12 (weekly_progress/091626/latency_breakdown.md).  Kept here so
# a priced number generalises past the provider it was measured on.
FIXED_ROUND_S = 3.72
MS_PER_OUTPUT_TOKEN = 17.44
MS_PER_UNCACHED_TOKEN = 0.033
CHARS_PER_TOKEN = 4.0

KB = 1024
BUDGETS = [8 * KB, 16 * KB, 32 * KB, 64 * KB, None]          # None = unlimited


@dataclass
class Item:
    """One retrievable tool observation: the unit an engine holds or drops.

    `key` identifies the span (a read_file path+offset+limit, or a bash command, or a tau-bench tool
    call).  Everything else is measured from the trace it came from."""

    key: tuple
    nbytes: int
    round_idx: int                      # the round that first produced it
    first_ms: float                     # first read
    last_ms: float                      # most recent read at the cut point
    freq: int = 1                       # how many rounds read it
    tier: int = 2                       # 0 = direct predecessor, 1 = rest of the closure, 2 = other
    model_s: float = 0.0                # duration of the model call that issued the fetch
    tool_s: float = 0.0                 # the tool's own execution time
    locate_s: float = 0.0               # the glob/grep round that found it, if any
    out_tokens: int = 0                 # decode spent writing the tool call
    next_use: int | None = None         # next round that reads it again (Belady only; CEILING)
    useful_bytes: int = 0               # bytes the successor went on to read (oracle only; CEILING)

    @property
    def tokens(self) -> float:
        return self.nbytes / CHARS_PER_TOKEN

    @property
    def recovery_seconds(self) -> float:
        """Measured cost of getting this back after dropping it.

        Prefers what the trace actually recorded; falls back to the priced model when a corpus does
        not carry per-call timings.  The locating round is charged here on purpose -- see the module
        docstring."""
        measured = self.model_s + self.tool_s
        if measured > 0:
            return measured + self.locate_s
        modelled = (FIXED_ROUND_S
                    + self.out_tokens * MS_PER_OUTPUT_TOKEN / 1000.0
                    + self.tokens * MS_PER_UNCACHED_TOKEN / 1000.0)
        return modelled + self.locate_s


# --------------------------------------------------------------------------- offline rankings
# Each returns a retention priority: FIRST is kept longest, LAST is dropped first.

def _rank_graph(items: list[Item]) -> list[int]:
    return sorted(range(len(items)), key=lambda i: (items[i].tier, -items[i].last_ms))


def _rank_largest(items: list[Item]) -> list[int]:
    return sorted(range(len(items)), key=lambda i: -items[i].nbytes)


def _rank_smallest(items: list[Item]) -> list[int]:
    return sorted(range(len(items)), key=lambda i: items[i].nbytes)


def _rank_costliest(items: list[Item]) -> list[int]:
    """Keep what is most expensive to recover; evict what is cheapest.  The deployable reading of
    'simplest to recover' -- the cheap things are the ones you can afford to lose."""
    return sorted(range(len(items)), key=lambda i: -items[i].recovery_seconds)


def _rank_density(items: list[Item]) -> list[int]:                              # CEILING
    return sorted(range(len(items)),
                  key=lambda i: -(items[i].useful_bytes / max(items[i].nbytes, 1)))


def _rank_belady(items: list[Item]) -> list[int]:                               # CEILING
    """MIN: evict what is used farthest in the future; never-used-again goes first."""
    big = float("inf")
    return sorted(range(len(items)),
                  key=lambda i: (big if items[i].next_use is None else items[i].next_use))


def _rank_cheapest_rounds(items: list[Item]) -> list[int]:                      # CEILING
    """Buy whole rounds, cheapest first.  Bytes spent covering most of several rounds remove none of
    them, so ranking by round price roughly doubles what a budget buys -- but it needs to know which
    rounds the successor will run, so it is a ceiling."""
    by_round: dict[int, list[int]] = {}
    for i, it in enumerate(items):
        by_round.setdefault(it.round_idx, []).append(i)
    cost = {r: sum(items[i].nbytes for i in idxs) for r, idxs in by_round.items()}
    order: list[int] = []
    for r in sorted(by_round, key=lambda r: cost[r]):
        order.extend(sorted(by_round[r], key=lambda i: -items[i].nbytes))
    return order


OFFLINE = {
    "graph": _rank_graph,
    "largest": _rank_largest,
    "smallest": _rank_smallest,
    "costliest_to_recover": _rank_costliest,
    "oracle_density": _rank_density,
    "belady": _rank_belady,
    "cheapest_rounds": _rank_cheapest_rounds,
}
CEILING = {"oracle_density", "belady", "cheapest_rounds"}


def select(items: list[Item], policy: str, budget: int | None,
           seed: int = 20260918) -> list[int]:
    """Offline: rank the pool once, then fill the budget best-effort.

    Best-effort means an item that does not fit is SKIPPED and smaller later ones are still tried --
    it does not stop the fill.  `dag_redundancy.budgeted_keys` and `profile_run.build_blocks` both
    do this; they used to disagree, and a 'budget' that means 'prefix of the ordering' is a
    different quantity that cannot be compared across the two."""
    if policy == "random":
        order = list(range(len(items)))
        _random.Random(seed).shuffle(order)
    else:
        order = OFFLINE[policy](items)
    if budget is None:
        return sorted(order)
    kept, spent = [], 0
    for i in order:
        if spent + items[i].nbytes > budget:
            continue
        kept.append(i)
        spent += items[i].nbytes
    return sorted(kept)


# --------------------------------------------------------------------------- online caches
# Each consumes the access stream in order and returns the resident key set.

def _sim_recency(items: list[Item], budget: int, key_time) -> set:
    """LRU and FIFO differ only in which timestamp they age on: last access vs first insertion."""
    resident: dict[tuple, Item] = {}
    spent = 0
    for it in sorted(items, key=lambda x: x.first_ms):
        if it.nbytes > budget:
            continue
        resident[it.key] = it
        spent += it.nbytes
        while spent > budget:
            victim = min(resident.values(), key=key_time)
            spent -= victim.nbytes
            del resident[victim.key]
    return set(resident)


def _sim_lru(items, budget):
    return _sim_recency(items, budget, lambda it: it.last_ms)


def _sim_fifo(items, budget):
    return _sim_recency(items, budget, lambda it: it.first_ms)


def _sim_lfu(items, budget):
    return _sim_recency(items, budget, lambda it: (it.freq, it.last_ms))


def _sim_gdsf(items: list[Item], budget: int) -> set:
    """Greedy-Dual-Size-Frequency.  Priority H = L + freq * cost / size, where L is the aging clock
    set to the priority of the last victim.  It generalises LRU (cost and size constant), LFU (size
    constant) and size-based eviction, and it is the natural home for a measured recovery cost."""
    resident: dict[tuple, Item] = {}
    pri: dict[tuple, float] = {}
    clock, spent = 0.0, 0
    for it in sorted(items, key=lambda x: x.first_ms):
        if it.nbytes > budget:
            continue
        resident[it.key] = it
        pri[it.key] = clock + it.freq * it.recovery_seconds / max(it.nbytes, 1)
        spent += it.nbytes
        while spent > budget:
            vkey = min(resident, key=lambda k: pri[k])
            clock = pri[vkey]
            spent -= resident[vkey].nbytes
            del resident[vkey], pri[vkey]
    return set(resident)


def _sim_sliding_window(items: list[Item], budget: int) -> set:
    """What production harnesses do: keep the most recent ROUNDS whole, drop older ones whole.
    Round-granular, not item-granular -- this harness's own `KEEP_RECENT_TOOL_RESULTS = 3`."""
    by_round: dict[int, list[Item]] = {}
    for it in items:
        by_round.setdefault(it.round_idx, []).append(it)
    kept, spent = set(), 0
    for r in sorted(by_round, reverse=True):
        cost = sum(it.nbytes for it in by_round[r])
        if spent + cost > budget:
            break                       # whole rounds only: a partial round is a fragmented handoff
        kept |= {it.key for it in by_round[r]}
        spent += cost
    return kept


def _sim_sieve(items: list[Item], budget: int) -> set:
    """SIEVE (NSDI'24): one FIFO queue, a visited bit per object, and a hand that sweeps from the
    tail.  The hand clears visited bits as it passes and evicts the first object it finds unvisited,
    which makes it scan-resistant at LRU's cost."""
    queue: list[Item] = []
    visited: dict[tuple, bool] = {}
    present: set = set()
    spent, hand = 0, 0
    for it in sorted(items, key=lambda x: x.first_ms):
        if it.nbytes > budget:
            continue
        if it.key in present:
            visited[it.key] = True
            continue
        queue.append(it)
        present.add(it.key)
        visited[it.key] = it.freq > 1
        spent += it.nbytes
        while spent > budget and queue:
            if hand >= len(queue):
                hand = 0
            cand = queue[hand]
            if visited.get(cand.key):
                visited[cand.key] = False
                hand += 1
                continue
            spent -= cand.nbytes
            present.discard(cand.key)
            queue.pop(hand)
    return present


def _sim_s3fifo(items: list[Item], budget: int) -> set:
    """S3-FIFO (SOSP'23): a small 10% probationary FIFO plus a 90% main FIFO.

    Everything enters S.  An object evicted from S is PROMOTED to M only if it was accessed again
    while it sat there, otherwise it is dropped -- most objects in any cache are one-hit wonders and
    this is the cheap way to notice.  M gives a second chance by decrementing a small counter and
    reinserting.  Objects must earn promotion from the stream; pre-sorting them by their final
    frequency would be using knowledge the cache does not have, and it also broke monotonicity."""
    small_cap = max(budget // 10, 1)
    main_cap = max(budget - small_cap, 1)
    small: list[Item] = []
    main: list[Item] = []
    present: set = set()
    hits: dict[tuple, int] = {}
    s_spent = m_spent = 0

    for it in sorted(items, key=lambda x: x.first_ms):
        if it.nbytes > budget:
            continue
        if it.key in present:
            hits[it.key] = hits.get(it.key, 0) + 1
            continue
        small.append(it)
        present.add(it.key)
        hits[it.key] = it.freq - 1       # re-reads recorded in the trace = accesses while resident
        s_spent += it.nbytes
        while s_spent > small_cap and small:
            ev = small.pop(0)
            s_spent -= ev.nbytes
            if hits.get(ev.key, 0) > 0:
                main.append(ev)
                m_spent += ev.nbytes
            else:
                present.discard(ev.key)
        while m_spent > main_cap and main:
            ev = main.pop(0)
            if hits.get(ev.key, 0) > 0:
                hits[ev.key] -= 1        # second chance
                main.append(ev)
                continue
            m_spent -= ev.nbytes
            present.discard(ev.key)
    return present


def _sim_random(items: list[Item], budget: int, seed: int = 20260918) -> set:
    rng = _random.Random(seed)
    resident: dict[tuple, Item] = {}
    spent = 0
    for it in sorted(items, key=lambda x: x.first_ms):
        if it.nbytes > budget:
            continue
        resident[it.key] = it
        spent += it.nbytes
        while spent > budget:
            vkey = rng.choice(list(resident))
            spent -= resident[vkey].nbytes
            del resident[vkey]
    return set(resident)


ONLINE = {
    "lru": _sim_lru,
    "fifo": _sim_fifo,
    "lfu": _sim_lfu,
    "gdsf": _sim_gdsf,
    "sliding_window": _sim_sliding_window,
    "sieve": _sim_sieve,
    "s3fifo": _sim_s3fifo,
    "random_online": _sim_random,
}

# Measured, not assumed (60 random pools, scripts/test_evict_policies.py): the OFFLINE rankings are
# monotone in budget, and EVERY online cache is not -- LRU and FIFO violate it in 17 of 60 pools,
# SIEVE 12, S3-FIFO 13.  This is not a bug in any of them.  LRU's stack property (the resident set
# at capacity C is contained in the one at C' > C) only holds for equal-sized objects; with
# variable-size tool observations the inclusion property is lost, so a larger budget can leave fewer
# bytes resident.  Any invariant check must therefore be applied per family, and the earlier study's
# "removable rounds are non-decreasing in budget for every ordering" (budget_sweep.py:167) was only
# ever tested on offline orderings.
MONOTONE_IN_BUDGET = set(OFFLINE) | {"random"}

POLICIES = list(ONLINE) + list(OFFLINE) + ["random"]
LABEL = {
    "lru": "LRU (least-recently-used)",
    "fifo": "FIFO / recency truncation",
    "lfu": "LFU (least-frequently-used)",
    "gdsf": "GDSF (greedy-dual size-frequency)",
    "sliding_window": "sliding window (last N rounds, whole)",
    "sieve": "SIEVE",
    "s3fifo": "S3-FIFO",
    "random_online": "random eviction (control)",
    "graph": "graph-ordered (preds → closure → rest)",
    "largest": "largest spans first",
    "smallest": "smallest spans first",
    "costliest_to_recover": "keep costliest-to-recover",
    "oracle_density": "oracle by byte density [CEILING]",
    "belady": "Belady / MIN [CEILING]",
    "cheapest_rounds": "cheapest whole rounds first [CEILING]",
    "random": "random selection (control)",
}


def retained(items: list[Item], policy: str, budget: int | None,
             seed: int = 20260918) -> set:
    """The resident key set under `policy` at `budget`.  Unlimited budget keeps everything, for
    every policy -- an ordering can only matter under a constraint, and that invariant is the first
    thing every sweep checks."""
    if not items:
        return set()
    if budget is None:
        return {it.key for it in items}
    if policy in ONLINE:
        return ONLINE[policy](items, budget)
    return {items[i].key for i in select(items, policy, budget, seed)}
