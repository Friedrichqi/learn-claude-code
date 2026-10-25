"""Synthetic checks for hs_stall: the hinge fit recovers a known step-time curve, and the budget follows it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import numpy as np  # noqa: E402

import hs_stall as hs  # noqa: E402


def truth(n: float, a: float, seq: int, ctx: float) -> float:
    """A step that is flat for the first 64 prefill tokens, then linear, plus prefill attention."""
    base = 0.018 + 2.8e-5 * seq + 0.077 * ctx / 1e6
    if n <= 0:
        return base
    return base + 0.001 + 0.0969 * max(0.0, n - 64) / 1e3 + 0.003464 * n * (a + n / 2) / 1e6


def synthetic(count: int = 4000, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    steps = []
    for _ in range(count):
        seq = int(rng.integers(1, 9))
        ctx = float(seq * rng.integers(8000, 30000))
        n = int(rng.choice([0, 0, 16, 32, 64, 96, 128, 256, 512, 1024, 2048, 4096, 8192]))
        a = float(rng.integers(0, 24000))
        steps.append({"gpu": truth(n, a, seq, ctx) * (1 + rng.normal(0, 0.01)), "dec_seq": seq, "dec_ctx": ctx,
                      "chunks": [(n, a)] if n else []})
    return steps


def test_fit_recovers_added_cost():
    steps = synthetic()
    coef = hs.huber_fit(hs.design(steps), np.array([s["gpu"] for s in steps]))
    for n in (32, 128, 512, 2048):
        for a in (0, 16000):
            want = truth(n, a, 4, 80000) - truth(0, a, 4, 80000)
            assert abs(hs.added(coef, n, a) - want) < 0.0015 + 0.05 * want


def test_budget_is_monotone_in_eps_and_context():
    steps = synthetic()
    coef = hs.huber_fit(hs.design(steps), np.array([s["gpu"] for s in steps]))
    base = hs.decode_only(coef, 4, 80000)
    b5, b10, b20 = (hs.budget(coef, 8000, base, e) for e in (0.05, 0.10, 0.20))
    assert b5 <= b10 <= b20
    assert hs.budget(coef, 24000, base, 0.10) <= hs.budget(coef, 0, base, 0.10)


def test_no_prefill_adds_nothing():
    steps = synthetic(800)
    coef = hs.huber_fit(hs.design(steps), np.array([s["gpu"] for s in steps]))
    assert hs.added(coef, 0, 16000) == 0.0
