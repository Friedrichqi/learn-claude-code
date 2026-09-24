#!/usr/bin/env python3
"""Invariants every eviction policy must satisfy.  Run: python3 -m pytest scripts/test_evict_policies.py"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evict_policies as ep                                                     # noqa: E402

BUDGETS = [8192, 16384, 32768, 65536]


def pool(seed: int, n: int = 45) -> list[ep.Item]:
    rng = random.Random(seed)
    items = []
    for i in range(n):
        nb = rng.choice([200, 900, 3000, 9000, 25000])
        items.append(ep.Item(key=("read_file", f"f{i % 19}.py", 0, None), nbytes=nb,
                             round_idx=i // 3, first_ms=i * 1000.0,
                             last_ms=i * 1000.0 + rng.random() * 500,
                             freq=rng.choice([1, 1, 1, 2, 3]), tier=rng.choice([0, 1, 2]),
                             model_s=rng.uniform(1, 6), tool_s=rng.uniform(0.005, 0.08),
                             locate_s=rng.choice([0.0, 0.0, 4.1]),
                             out_tokens=rng.randint(20, 300),
                             next_use=rng.choice([None, i + 2, i + 9]),
                             useful_bytes=rng.randint(0, nb)))
    seen: dict = {}
    for it in items:
        seen.setdefault(it.key, it)
    return list(seen.values())


def test_budget_is_never_exceeded():
    for seed in range(30):
        p = pool(seed)
        for policy in ep.POLICIES:
            for b in BUDGETS:
                keep = ep.retained(p, policy, b)
                spent = sum(i.nbytes for i in p if i.key in keep)
                assert spent <= b, f"{policy} spent {spent} > {b}"


def test_every_policy_converges_at_unlimited_budget():
    """An ordering can only matter under a constraint.  If two policies differ at an unlimited
    budget one of them is dropping content for a reason that is not capacity."""
    for seed in range(30):
        p = pool(seed)
        for policy in ep.POLICIES:
            assert ep.retained(p, policy, None) == {i.key for i in p}, policy


def test_offline_rankings_are_monotone_in_budget():
    """Only the offline family.  Online caches over variable-size objects lose LRU's stack property
    and are genuinely non-monotone -- see MONOTONE_IN_BUDGET."""
    for seed in range(30):
        p = pool(seed)
        for policy in sorted(ep.MONOTONE_IN_BUDGET):
            prev = -1
            for b in BUDGETS:
                spent = sum(i.nbytes for i in p if i.key in ep.retained(p, policy, b))
                assert spent >= prev - 1, f"{policy} dropped {prev}->{spent} at {b}"
                prev = spent


def test_whole_items_only():
    """No policy may hand over half a span: a fragmented handoff was measured to be re-read 88% of
    the time, so a split span is worth nothing and costs prefill."""
    p = pool(1)
    keys = {i.key for i in p}
    for policy in ep.POLICIES:
        for b in BUDGETS:
            assert ep.retained(p, policy, b) <= keys, policy


def stream(p: list[ep.Item], seed: int, n: int = 120) -> list[ep.Item]:
    """A read stream over pool `p`: every item read once in order, then random re-reads."""
    rng = random.Random(seed)
    order = list(p) + [rng.choice(p) for _ in range(n - len(p))]
    return [ep.Item(key=it.key, nbytes=it.nbytes, round_idx=t // 3, first_ms=float(t),
                    last_ms=float(t), model_s=it.model_s, tool_s=it.tool_s, locate_s=it.locate_s)
            for t, it in enumerate(order)]


def test_stream_invariants():
    """The stream path keeps the same contract: budget, whole items, convergence at unlimited."""
    for seed in range(30):
        p = pool(seed)
        s = stream(p, seed)
        keys = {i.key for i in p}
        for policy in ep.ONLINE:
            assert ep.retained(p, policy, None, accesses=s) == keys, policy
            for b in BUDGETS:
                keep = ep.retained(p, policy, b, accesses=s)
                assert keep <= keys, policy
                assert sum(i.nbytes for i in p if i.key in keep) <= b, policy


def _item(key: str, nbytes: int = 4000, freq: int = 1) -> ep.Item:
    return ep.Item(key=(key,), nbytes=nbytes, round_idx=0, first_ms=0.0, last_ms=0.0, freq=freq)


def test_stream_lru_refreshes_on_a_hit_and_fifo_does_not():
    a, b, c = _item("a"), _item("b"), _item("c")
    reads = [a, b, a, c]                            # 8 KB holds two: a was re-read after b
    assert ep.retained([a, b, c], "lru", 8000, accesses=reads) == {("a",), ("c",)}
    assert ep.retained([a, b, c], "fifo", 8000, accesses=reads) == {("b",), ("c",)}


def test_stream_lfu_counts_only_reads_it_has_seen():
    """Frequency must come from the stream, never from `Item.freq`: a caller that counts reads the
    cache has not seen yet (the successor's, later tasks') hands LFU the answer."""
    a, b, c = _item("a"), _item("b", freq=99), _item("c")
    reads = [a, a, a, b, c]
    assert ep.retained([a, b, c], "lfu", 8000, accesses=reads) == {("a",), ("c",)}


def test_recovery_cost_charges_the_locating_round():
    """A read of a known path is cheap; the grep that found the path is not.  If the locating round
    is not charged to the item, 'evict what is cheapest to recover' evicts the expensive thing."""
    common = dict(key=("read_file", "a.py", 0, None), nbytes=4000, round_idx=1,
                  first_ms=0.0, last_ms=0.0, model_s=3.0, tool_s=0.02)
    cheap = ep.Item(**common, locate_s=0.0)
    costly = ep.Item(**common, locate_s=4.1)
    assert costly.recovery_seconds > cheap.recovery_seconds + 4.0


def test_measured_timings_beat_the_priced_model():
    measured = ep.Item(key=("k",), nbytes=4000, round_idx=0, first_ms=0, last_ms=0,
                       model_s=9.0, tool_s=0.5)
    modelled = ep.Item(key=("k",), nbytes=4000, round_idx=0, first_ms=0, last_ms=0)
    assert abs(measured.recovery_seconds - 9.5) < 1e-6
    assert abs(modelled.recovery_seconds - ep.FIXED_ROUND_S) < 0.5
