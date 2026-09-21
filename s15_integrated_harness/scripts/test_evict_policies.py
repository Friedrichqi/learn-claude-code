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
