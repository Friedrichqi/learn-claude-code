#!/usr/bin/env python3
"""Tests of wt_analyze's pre-registered decision rules and Kaplan-Meier helper on synthetic cells (no data needed).

    python research/18_harness_wait/test_wt_analyze.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import wt_analyze as wa  # noqa: E402


def cell(rate: float, spread: float = 50.0, hit: float = 0.5) -> dict:
    blocks = [rate + spread * ((i % 3) - 1) for i in range(9)]
    return {"work_calls_h": sum(blocks) / 9, "blocks": blocks, "hit_rate": hit}


def test_decisions_follow_the_prereg():
    h5 = {"w16_none": cell(2000), "w16_none_aa": cell(2100), "w16_sgate": cell(5000),
          "w16_h_share": cell(4800), "w16_h_backoff": cell(3000), "w16_m_stock": cell(1000), "w16_m_share": cell(2050),
          "w08_none": cell(6000, hit=0.89), "w08_m_stock": cell(5950, hit=0.88), "w08_m_share": cell(5000, hit=0.80)}
    dec = wa.decisions(h5, iters=500)
    assert abs(dec["delta_aa"] - 100) < 1e-6
    r1 = dec["rules"]["R1"]
    assert r1["w16_h_share"]["recovers"] and abs(r1["w16_h_share"]["R"] - 2800 / 3000) < 1e-6
    assert not r1["w16_h_backoff"]["recovers"]                  # R = 1/3 < 0.5
    assert r1["w16_m_stock"]["hurts"] and not r1["w16_m_share"]["hurts"] and not r1["w16_m_share"]["recovers"]
    assert r1["w16_sgate (gate gain)"]["gate_beats_noise"]
    r2 = dec["rules"]["R2"]
    assert r2["w16_m_share - w16_h_share"]["decider_matters"] and r2["w16_m_stock - w16_h_backoff"]["decider_matters"]
    r3 = dec["rules"]["R3"]
    assert r3["w16_m_share - w16_m_stock"]["information_matters"] and r3["w16_h_share - w16_h_backoff"]["information_matters"]
    r4 = dec["rules"]["R4"]
    assert r4["w08_m_stock"]["no_harm"] and not r4["w08_m_share"]["no_harm"]
    assert "R1 recovery" in wa.decisions_md(dec)


def test_exploratory_contrasts_pair_an_x_cell_with_its_twin_and_the_gate():
    h5 = {"w16_h_share": cell(4800), "w16_sgate": cell(6500), "w32_h_share": cell(1000),
          "x16_h_share_start": {**cell(6100), "arm": "h_share_start", "n": 16}}
    xc = wa.exploratory_contrasts(h5, iters=500)
    assert list(xc) == ["x16_h_share_start - w16_h_share", "x16_h_share_start - w16_sgate"]
    d = xc["x16_h_share_start - w16_h_share"]
    assert abs(d["D"] - 1300) < 1e-6 and d["ci"][0] < 1300 < d["ci"][1]
    assert wa.exploratory_contrasts({"w16_h_share": cell(4800)}) == {}


def test_no_aa_no_decisions():
    assert "note" in wa.decisions({"w16_none": cell(2000)})


def test_km_with_censoring():
    q = wa.km_quantiles([10, 20, 30, 40, 100], [False, False, True, False, True])
    assert q[0.5] == 40 and q[0.95] is None


def test_wait_runs_split_at_turns_and_classify_the_end():
    # (turn, wait_only, in_window, crowded)
    seq = [("t1", False, True, False), ("t1", True, True, False), ("t1", True, True, True), ("t1", False, True, None),
           ("t1", True, False, True), ("t1", True, True, False), ("t2", True, True, False), ("t2", False, True, False),
           ("t3", True, False, False), ("t4", True, True, True)]
    runs = wa.wait_runs(seq)
    assert [(r["len"], r["in_window"], r["end"], r["start_crowded"]) for r in runs] == [
        (2, 2, "resumed", False), (2, 1, "budget", True), (1, 1, "resumed", False), (1, 1, "last", True)]
    # the run in t3 lies wholly outside the window and is dropped


def test_twin_would_hold_on_both_lines():
    env = {"S15_SHARE_TAU": "0.9", "S15_BACKOFF_ENTER_KV": "0.92"}
    share = ("[server] KV pool 258,176 tokens; sessions ahead of you hold {}; your context 23,115, 23,072 still cached; "
             "0 requests waiting")
    assert wa.twin_would_hold(share.format("75,045"), env) is False
    assert wa.twin_would_hold(share.format("210,000"), env) is True          # 233,115 > 232,358
    stock = ("[server] running 12, waiting 5 ({} for KV memory); KV memory in use {}%; prompt-cache hits 4% and "
             "preemptions 0 in the last 10 s")
    assert wa.twin_would_hold(stock.format(0, 91), env) is False
    assert wa.twin_would_hold(stock.format(0, 92), env) is True
    assert wa.twin_would_hold(stock.format(1, 50), env) is True
    assert wa.twin_would_hold(None, env) is None and wa.twin_would_hold("[server] n/a", env) is None


class Holds:
    """Just enough of a WCell for h3_row: sessions' events, every one inside the window, no calls."""
    cond, arm = "x16_h_share_start", "h_share_start"

    def __init__(self, sessions: dict):
        self.sessions = sessions

    def events(self, name):
        for label, events in self.sessions.items():
            for e in events:
                if e["event"] == name:
                    yield label, e

    def inside(self, t) -> bool:
        return t is not None

    def lead_in_window(self, work_only: bool = True) -> list:
        return []


def test_prefix_survival_pairs_holds_within_a_session():
    def hold(event, prefix, sec):
        key = "signals" if event == "hold_start" else "signals_end"
        return {"event": event, "span_id": "span_000002", "timestamp": f"2026-10-24T22:00:{sec:02d}Z",
                "data": {key: {"me": {"prefix": prefix}}}}
    # both sessions' first hold has the same span id; S002's start comes last
    w = Holds({"S001": [hold("hold_start", 20000, 0), hold("hold_end", 10000, 5)],
               "S002": [hold("hold_start", 320, 1), hold("hold_end", 320, 9)]})
    row = wa.h3_row(w)
    assert row["n_pairs"] == 2 and abs(row["kept_mean"] - 0.75) < 1e-9    # 0.5 and 1.0, not 31.25 and 1.0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
