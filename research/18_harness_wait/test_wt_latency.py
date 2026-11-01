#!/usr/bin/env python3
"""Tests of wt_latency (completion time per task) on synthetic cells, plus one data check when the data is present.

    python research/18_harness_wait/test_wt_latency.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import wt_analyze as wa  # noqa: E402
import wt_latency as wl  # noqa: E402

T0 = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)


def ev(event: str, tid: str, sec: float, **data) -> dict:
    stamp = (T0 + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {"event": event, "turn_id": tid, "timestamp": stamp, "data": data}


def turn(tid: str, start: float, end: float | None, calls: int, stop: dict | None, status: str = "completed") -> list:
    out = [ev("turn_start", tid, start, trigger="user")]
    out += [ev("mu_call", tid, start + 0.1 * (i + 1), purpose="lead", call_index=i) for i in range(calls)]
    if stop is not None:
        out.append(ev("harness_decision", tid, (end or start) - 0.01, decision="stop", **stop))
    if end is not None:
        out.append(ev("turn_end", tid, end, trigger="user", status=status))
    return out


class Cell:
    """Just enough of a TraceCell: window [0, 1000] s after T0, sessions of meta + events, a cell log."""

    def __init__(self, sessions: list[tuple[dict, list]], log: list | None = None, we: float = 1000.0):
        self.ws, self.we = T0.timestamp(), T0.timestamp() + we
        self.hours = we / 3600
        self.sessions = [{"meta": m, "events": e} for m, e in sessions]
        self.log = log or []
        self.cell, self.cond, self.n = self, "t", 2

    def inside(self, t) -> bool:
        return t is not None and self.ws <= t <= self.we


NORMAL = {"reason": "no_tool_use"}
TURN_CAP = {"reason": "model_error", "error_type": "CallBudget", "error": "turn lead-call budget spent"}
SESSION_CAP = {"reason": "model_error", "error_type": "CallBudget", "error": "session lead-call budget spent"}
TOO_LONG = {"reason": "model_error", "error_type": "PromptTooLong", "error": "prompt is too long"}


def test_end_classes():
    a = ({"label": "MU-t-S000", "spec": "S000", "templates": ["E1", "E2", "M1", "M2"]},
         turn("turn_000001", 10, 50, 3, NORMAL) + turn("turn_000002", 60, 200, 100, TURN_CAP)
         + turn("turn_000003", 210, 300, 40, SESSION_CAP) + turn("turn_000004", 310, 310.01, 0, SESSION_CAP))
    b = ({"label": "MU-t-S001", "spec": "S001", "templates": ["E3", "E1", "M3", "M1"]},
         turn("turn_000001", 20, 80, 2, TOO_LONG) + turn("turn_000002", 90, 1000.8, 5, None, "error")
         + turn("turn_000003", 1001, None, 0, None))
    c = ({"label": "MU-t-S002", "spec": "S002", "templates": ["E1", "E2", "M1", "M2"]},
         turn("turn_000001", 30, 400, 4, None, "error"))
    f = ({"label": "MU-t-F000-w001", "spec": "F000", "filler": True, "templates": ["E2", "E3", "M2", "M3"]},
         turn("turn_000001", 5, 120, 4, None, "error"))
    rows = wl.turn_rows(Cell([a, b, c, f]))
    got = {(r["label"][-4:], r["idx"]): r["end"] for r in rows}
    assert got == {("S000", 1): "normal", ("S000", 2): "turn cap", ("S000", 3): "session cap",
                   ("S000", 4): "refused", ("S001", 1): "too long", ("S001", 2): "cut", ("S001", 3): "cut",
                   ("S002", 1): "error", ("w001", 1): "killed"}, got
    assert [r["family"] for r in rows if r["label"].endswith("S000")] == ["explain", "explain", "modify", "modify"]
    d, e, cl, fam = wl.sample(rows, Cell([a, b, c, f]))
    # primary: counted, started in the window, refused left out, turn 3 of S001 started after the window end
    assert list(d.round(2)) == [40, 140, 90, 60, 910, 370] and list(e) == [True, True, True, True, False, False]
    d, e, _, _ = wl.sample(rows, Cell([a, b, c, f]), mode="normal")
    assert list(e) == [True, False, False, False, False, False]
    d, e, _, _ = wl.sample(rows, Cell([a, b, c, f]), mode="all")
    assert len(d) == 7 and not e[-1]                      # the killed filler turn is censored


def test_old_sample_is_turn_latencies_and_km_matches():
    a = ({"label": "MU-t-S000", "spec": "S000", "templates": ["E1", "E2"]},
         turn("turn_000001", 10, 50, 3, NORMAL) + turn("turn_000002", 60, 1000.5, 9, None, "error"))
    b = ({"label": "MU-t-S001", "spec": "S001", "templates": ["E1", "E2"]},
         turn("turn_000001", 15, 95, 3, NORMAL) + turn("turn_000002", 100, 130, 3, NORMAL))
    cell = Cell([a, b])
    d, c = wa.turn_latencies(cell)
    km = wl.KM(np.asarray(d), ~np.asarray(c))
    S = km.curves(np.ones(len(d)))
    old = wa.km_quantiles(d, c, ps=wl.PS)
    for p in wl.PS:
        mine = wl.quantile(S, km.times, p)[0]
        assert (old[p] is None and math.isinf(mine)) or abs(old[p] - mine) < 1e-9, (p, old[p], mine)


def test_km_matches_wt_analyze_on_random_samples_with_ties():
    rng = np.random.default_rng(7)
    for _ in range(300):
        n = int(rng.integers(1, 60))
        d = np.round(rng.exponential(50, n))              # integer seconds force ties
        c = rng.random(n) < rng.uniform(0, 0.6)
        km = wl.KM(d, ~c)
        S = km.curves(np.ones(n))
        old = wa.km_quantiles(list(d), list(c), ps=wl.PS)
        for p in wl.PS:
            mine = wl.quantile(S, km.times, p)[0]
            assert (old[p] is None and math.isinf(mine)) or (old[p] is not None and abs(old[p] - mine) < 1e-9), \
                (p, old[p], mine, d, c)


def test_weighted_km_equals_the_duplicated_sample():
    rng = np.random.default_rng(3)
    d = np.round(rng.exponential(30, 40))
    e = rng.random(40) < 0.7
    w = rng.integers(1, 4, 40)
    a = wl.KM(d, e).curves(w)[0]
    km2 = wl.KM(np.repeat(d, w), np.repeat(e, w))
    b = km2.curves(np.ones(int(w.sum())))[0]
    assert np.allclose(a, b)


def test_km_mean_plain_and_by_hand():
    d = np.array([3.0, 1.0, 7.0, 2.0])
    km = wl.KM(d, np.ones(4, bool))
    S = km.curves(np.ones(4))
    assert abs(wl.rmst(S, km.times, km.times[-1])[0] - d.mean()) < 1e-12
    km = wl.KM(np.array([1.0, 2.0, 3.0]), np.array([True, False, True]))
    S = km.curves(np.ones(3))                              # S: 2/3 after 1, stays at 2, 0 after 3
    assert abs(wl.rmst(S, km.times, 3.0)[0] - (1 + 2 / 3 * 2)) < 1e-12
    s = wl.summarize(np.array([1.0, 2.0, 3.0]), np.array([True, True, False]), ["a", "b", "c"], "t", 0, 0)
    assert s["surv_end"] > 0.01 and wl.fmt_mean(s).startswith("≥")


def test_family_mixture():
    e = wl.summarize(np.array([10.0, 20.0]), np.array([True, True]), ["a", "b"], "t", 0, 0)
    m = wl.summarize(np.array([30.0, 40.0]), np.array([True, True]), ["c", "d"], "t", 0, 0)
    mix = wl.mixture([e, m])
    assert mix["p50"] == 20.0 and mix["p95"] == 40.0                # S_mix: .75, .5, .25, 0
    assert abs(mix["mean"] - 25.0) < 1e-9                          # both curves reach 0: the mean of 15 and 35
    m_cut = wl.summarize(np.array([30.0, 40.0]), np.array([True, False]), ["c", "d"], "t", 0, 0)
    assert abs(wl.mixture([e, m_cut])["mean"] - 17.5) < 1e-9       # restricted to the shorter follow-up, 20 s
    assert wl.mixture([e]) == {}


def test_little_and_km_mean_on_a_steady_closed_loop():
    rng = np.random.default_rng(11)
    ws, we, rows = T0.timestamp(), T0.timestamp() + 7200, []
    for slot in range(150):
        t = ws - 3000 + rng.uniform(0, 100)
        while t < we:
            dur = rng.exponential(100.0)
            end = t + dur
            rows.append({"t0": t, "t1": end if end <= we + 0.5 else None, "end": "normal" if end <= we else "cut",
                         "filler": False, "spec": f"S{slot:03d}", "family": "explain"})
            t = end + rng.exponential(30.0)
    cell = Cell([], we=7200)
    d, e, cl, _ = wl.sample(rows, cell)
    s = wl.summarize(d, e, cl, "t", 0, 0)
    li = wl.little(rows, cell)
    assert abs(s["mean"] / 100 - 1) < 0.04 and abs(li["W"] / 100 - 1) < 0.04, (s["mean"], li["W"])
    assert s["naive_mean"] < s["mean"]                               # cutting biases the naive mean low


def test_bootstrap_determinism_and_unreached_quantiles():
    cl = [f"S{i % 10:03d}" for i in range(40)]
    a = wl.boot_weights(cl, 50, 19, "w16_none")
    b = wl.boot_weights(cl, 50, 19, "w16_none")
    c = wl.boot_weights(cl, 50, 19, "w32_none")
    assert np.array_equal(a, b) and not np.array_equal(a, c) and np.allclose(a.sum(1), 40)
    d = np.arange(1.0, 41.0)
    e = d < 36                                                      # the 5 longest turns are cut
    s = wl.summarize(d, e, cl, "t", 400, 1)
    assert math.isinf(s["p95"]) and s["reached_share"]["p95"] < 0.975 and s["p50"] == 20.0
    assert wl.ratio(s, s, "p95") is None and wl.ratio(s, s, "p50")["ratio"] == 1.0
    assert wl.fmt_q(s, "p95") == "> 40"


def test_turns_h_split_counts_every_turn_end_in_the_window():
    rows = [{"t1": T0.timestamp() + 10, "filler": False, "end": "normal"},
            {"t1": T0.timestamp() + 20, "filler": True, "end": "killed"},
            {"t1": T0.timestamp() + 2000, "filler": False, "end": "cut"},
            {"t1": None, "filler": False, "end": "cut"},
            {"t1": T0.timestamp() + 30, "filler": False, "end": "refused"}]
    t = wl.turns_h_split(rows, Cell([]))
    assert t["turn_ends"] == 3 and t["split"] == {"filler killed": 1, "normal": 1, "refused": 1}
    assert abs(t["turns_h"] - 3 / (1000 / 3600)) < 1e-9 and abs(t["turns_h_without_killed"] - 2 / (1000 / 3600)) < 1e-9


def test_formatting():
    s = wl.summarize(np.arange(1.0, 51.0), np.ones(50, bool), [f"S{i}" for i in range(50)], "t", 0, 0)
    assert wl.fmt_q(s, "p99") == "n/a" and wl.fmt_q(s, "p95").endswith("†")   # 50 x 0.05 = 2.5 < 5
    assert wl.fmt_q(s, "p50") == "25" and abs(s["mean"] - 25.5) < 1e-9
    assert wl.fmt_mean(s) == wl.fs(s["mean"])                                # exact: no ≥ or * mark
    assert wl.fs(float("inf")) == "not reached" and wl.fs(None) == "—" and wl.fs(1234.5) == "1,234"
    assert wl.clean({"a": float("inf"), "_x": 1, "b": [np.float64(2.0)]}) == {"a": None, "b": [2.0]}


def test_data_w16_none_reproduces_the_stored_values():
    root = wl.TOPICS["18"]["dir"] / "data" / "cells" / "w16_none"
    if not (root / "COMPLETE").exists():
        print("  (skipped: no data)")
        return
    tc = wl.TraceCell(root)
    d, c = wa.turn_latencies(tc)
    old = wa.km_quantiles(d, c)
    stored = wl.stored_values("18")["w16_none"]
    assert len(d) == stored["turns_started"] and abs(sum(c) / len(c) - stored["censored"]) < 1e-12
    assert abs(old[0.5] - stored["turn_p50"]) < 1e-9 and old[0.95] == stored["turn_p95"] is None
    t = wl.turns_h_split(wl.turn_rows(tc), tc)
    assert abs(t["turns_h"] - stored["turns_h"]) < 1e-9


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
