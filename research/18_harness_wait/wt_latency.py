#!/usr/bin/env python3
"""Completion time per task in topics 18 and 19 (exploratory, added 2026-10-29; not pre-registered).

Both topics reported throughput (work calls/h, turns/h) and Kaplan-Meier turn p50/p95. This module derives the
end-to-end completion time of one task entry, with its mean, median, p95 and p99 per setup, from the traces on disk.

A task entry is one turn: one task prompt (template E1-E3 or M1-M3 on a codebase). Its completion time runs from
the trace's turn_start to its turn_end (wall clock). That includes queueing, every model call, tools, model waits,
harness holds and gate waits; think pauses fall between turns. Every cell stopped hard at its window end, so turns
still running were cut: the statistics are Kaplan-Meier (KM) estimates over turns that start in the window,
right-censored at its end.

Primary sample: counted sessions (not warm-up fillers), turns started in [window start, window end].
  event      the turn ended before the window end: normal (the model answered), turn cap (100 lead calls), session
             cap (300), too long (the prompt overflowed)
  censored   cut at the window end, or ended with status "error" inside it
  left out   turns refused before any call (the session's budget was spent); counted in their own column
Sensitivity samples (JSON): normal only (capped and too-long turns censored); all sessions (fillers included, their
killed turns censored); old (exactly wt_analyze.turn_latencies, the stored definition, used to check reproduction).

Estimates: the KM mean is the area under the curve up to the longest follow-up ("≥ x" when the curve does not
reach 0, "x*" when under 1 % of turns remain); KM p50 / p95 / p99 ("> t" when not reached, p99 only with at least
100 turns, † when fewer than 5 turns are expected beyond the quantile). Intervals: spec-cluster bootstrap, 2,000
draws (a quantile not reached in a draw counts as +inf). Ratios to `none` at the same N, shown when both values are
finite in >= 97.5 % of draws; mean ratios use both means up to the shorter longest follow-up. Cross-checks: Little's
law after the last warm-up filler ended, the naive mean of finished turns, the median of turns started in the
window's first and second halves. Lead calls: received in the window (the stored definition), and sent in it with a
call whose session was stopped mid-call censored at the stop (the trace's error response). Sessions: first
turn_start to last turn_end, censored at the window end.

One topic per process (wt_analyze and ef_analyze set different module globals).

    python research/18_harness_wait/wt_latency.py --topic 18            # data/tables/latency.{md,json}
    python research/18_harness_wait/wt_latency.py --topic 19
    python research/18_harness_wait/wt_latency.py --topic 18 --verify   # exit 1 unless the stored values reproduce
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zlib
from collections import Counter
from pathlib import Path

import numpy as np

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import wt_analyze as wa  # noqa: E402

TOPICS = {
    "18": {"dir": REPO / "research" / "18_harness_wait", "seed": 18,
           "cells": ["w08_none", "w08_m_stock", "w08_m_share", "w16_none", "w16_none_aa", "w16_m_stock", "w16_m_share",
                     "w16_h_backoff", "w16_h_share", "w16_sgate", "x16_h_share_start", "w32_none", "w32_m_stock",
                     "w32_m_share", "w32_h_backoff", "w32_h_share", "w32_sgate"],
           "stored": [("main.json", "h5"), ("contingency.json", "h5")]},
    "19": {"dir": REPO / "research" / "19_efficiency_signals", "seed": 19,
           "cells": ["w08_none", "w08_m_tools", "w16_none", "w16_sgate", "w16_m_wait", "w16_m_tools", "w32_none",
                     "w32_sgate", "w32_m_wait", "w32_m_tools", "x64_m_wait", "x64_none", "x128_m_wait", "x128_none"],
           "stored": [("main.json", "t")]},
}
PS = (0.5, 0.95, 0.99)
FAMILY = {"E": "explain", "M": "modify"}
EVENT = {"normal", "turn cap", "session cap", "too long", "deadline", "other"}
CAPPED = ("turn cap", "session cap", "too long", "deadline", "other")


# -- reading ----------------------------------------------------------------------------------------------------------
class TraceCell:
    """One cell's window, cell log and session traces (no captures, no server logs). Also enough of a WCell for
    wt_analyze.turn_latencies (it reads w.cell.sessions, w.inside and w.we)."""

    def __init__(self, root: Path):
        win = json.loads((root / "window.json").read_text())
        self.cond, self.root = root.name, root
        self.ws, self.we = win["window_start"], win["window_end"]
        self.n, self.arm = win.get("n"), win.get("arm")
        self.hours = (self.we - self.ws) / 3600
        self.log = wa.ma.read_jsonl(root / "cell_log.jsonl")
        self.sessions = []
        for meta_path in sorted(root.glob("runs/*/*.meta.json")):
            meta = json.loads(meta_path.read_text())
            traces = sorted(p for p in meta_path.parent.glob("run_*.jsonl.xz") if ".inputs." not in p.name)
            self.sessions.append({"meta": meta, "events": wa.ma.read_jsonl(traces[0]) if traces else []})
        self.cell = self

    def inside(self, t) -> bool:
        return t is not None and self.ws <= t <= self.we


def stop_class(d: dict) -> str:
    if d.get("reason") == "no_tool_use":
        return "normal"
    if d.get("reason") == "model_error":
        kind = d.get("error_type")
        if kind == "CallBudget":
            return "session cap" if "session" in (d.get("error") or "") else "turn cap"
        if kind == "PromptTooLong":
            return "too long"
        if kind == "RunDeadline":
            return "deadline"
    return "other"


def end_class(r: dict, we: float) -> str:
    if r["t1"] is None or r["t1"] > we:
        return "cut"
    if r["status"] == "error":
        return "killed" if r["filler"] else "error"
    if r["calls"] == 0 and r["stop"] in ("turn cap", "session cap", "deadline"):
        return "refused"
    return r["stop"] or "other"


def turn_rows(tc: TraceCell) -> list[dict]:
    """One row per turn of every session: its template, start and end, how it stopped and its end class."""
    rows = []
    for sess in tc.sessions:
        meta, cur, idx = sess["meta"], {}, 0
        templates = meta.get("templates") or []
        for e in sess["events"]:
            event, tid = e.get("event"), e.get("turn_id")
            if event == "turn_start":
                idx += 1
                tpl = templates[idx - 1] if idx <= len(templates) else None
                cur[tid] = {"label": meta["label"], "spec": meta.get("spec") or meta["label"],
                            "filler": bool(meta.get("filler")), "idx": idx, "template": tpl,
                            "family": FAMILY.get((tpl or "?")[0]), "t0": wa.ts(e), "t1": None, "status": None,
                            "stop": None, "calls": 0}
                rows.append(cur[tid])
            elif tid in cur:
                r = cur[tid]
                if event == "mu_call":
                    r["calls"] += 1
                elif event == "harness_decision" and (e.get("data") or {}).get("decision") == "stop":
                    r["stop"] = stop_class(e["data"])
                elif event == "turn_end":
                    r["t1"], r["status"] = wa.ts(e), (e.get("data") or {}).get("status")
    for r in rows:
        r["end"] = end_class(r, tc.we)
    return rows


def sample(rows: list[dict], tc: TraceCell, mode: str = "primary", t_from=None, t_to=None):
    """(durations, event flags, clusters, families) of the turns a mode counts: primary, normal or all."""
    lo = tc.ws if t_from is None else t_from
    hi = tc.we if t_to is None else t_to
    d, ev, cl, fam = [], [], [], []
    for r in rows:
        if r["t0"] is None or not lo <= r["t0"] <= hi or r["end"] == "refused":
            continue
        if r["filler"] and mode != "all":
            continue
        if r["end"] == "cut":
            dur, e = tc.we - r["t0"], False
        else:
            dur = r["t1"] - r["t0"]
            e = r["end"] in EVENT and (mode != "normal" or r["end"] == "normal")
        d.append(dur)
        ev.append(e)
        cl.append(r["spec"])
        fam.append(r["family"])
    return np.asarray(d, float), np.asarray(ev, bool), cl, fam


# -- Kaplan-Meier ------------------------------------------------------------------------------------------------------
class KM:
    """Kaplan-Meier over fixed observations, for one weight vector or a matrix of bootstrap weights (one row each).
    At tied times all events count against everyone still at risk, as in wt_analyze.km_quantiles."""

    def __init__(self, d: np.ndarray, e: np.ndarray):
        self.order = np.lexsort((~e, d))
        self.d, self.e = d[self.order], e[self.order]
        self.times, self.first = np.unique(self.d, return_index=True)

    def curves(self, weights: np.ndarray) -> np.ndarray:
        """Survival just after each distinct time; weights in the observations' original order."""
        w = np.atleast_2d(np.asarray(weights, float))[:, self.order]
        at_risk = np.flip(np.cumsum(np.flip(w, 1), 1), 1)[:, self.first]
        deaths = np.add.reduceat(w * self.e, self.first, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            hazard = np.where(at_risk > 0, deaths / at_risk, 0.0)
        return np.cumprod(1.0 - hazard, axis=1)


def quantile(S: np.ndarray, times: np.ndarray, p: float) -> np.ndarray:
    hit = S <= 1 - p + 1e-12
    return np.where(hit.any(axis=1), times[hit.argmax(axis=1)], np.inf)


def rmst(S: np.ndarray, times: np.ndarray, tau: float) -> np.ndarray:
    """The area under each curve from 0 to tau: the KM mean restricted to tau."""
    t = np.minimum(times, tau)
    widths = np.diff(np.concatenate([[0.0], t]))
    before = np.concatenate([np.ones((S.shape[0], 1)), S[:, :-1]], axis=1)
    return (before * widths).sum(axis=1) + S[:, -1] * max(0.0, tau - times[-1])


def step_at(times: np.ndarray, S: np.ndarray, grid: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(times, grid, side="right") - 1
    return np.where(idx >= 0, S[np.maximum(idx, 0)], 1.0)


def boot_weights(clusters: list[str], iters: int, seed: int, cond: str) -> np.ndarray:
    keys = sorted(set(clusters))
    pos = {k: i for i, k in enumerate(keys)}
    rng = np.random.default_rng([seed, zlib.crc32(cond.encode())])
    picks = rng.integers(0, len(keys), size=(iters, len(keys)))
    counts = np.zeros((iters, len(keys)))
    np.add.at(counts, (np.repeat(np.arange(iters), len(keys)), picks.ravel()), 1.0)
    return counts[:, [pos[c] for c in clusters]]


def ci(values: np.ndarray) -> list[float]:
    return [float(np.quantile(values, 0.025, method="inverted_cdf")),
            float(np.quantile(values, 0.975, method="inverted_cdf"))]


def summarize(d, e, clusters, cond: str, iters: int, seed: int) -> dict:
    """KM mean, p50, p95 and p99 with bootstrap intervals; private arrays (_km, _S, _SB) stay for the ratios."""
    n = len(d)
    if n == 0:
        return {"n": 0}
    km = KM(d, e)
    S = km.curves(np.ones(n))
    tau = float(km.times[-1])
    out = {"n": n, "events": int(e.sum()), "censored": int((~e).sum()), "follow_up_max": tau,
           "surv_end": float(S[0, -1]), "mean": float(rmst(S, km.times, tau)[0]),
           "naive_mean": float(d[e].mean()) if e.any() else None, "clusters": len(set(clusters)),
           "_km": km, "_S": S}
    for p in PS:
        out[f"p{round(100 * p)}"] = float(quantile(S, km.times, p)[0])
    if iters:
        SB = km.curves(boot_weights(clusters, iters, seed, cond))
        boots = {"mean": rmst(SB, km.times, tau), **{f"p{round(100 * p)}": quantile(SB, km.times, p) for p in PS}}
        out["ci"] = {k: ci(v) for k, v in boots.items()}
        out["reached_share"] = {k: float(np.isfinite(v).mean()) for k, v in boots.items()}
        out["_SB"] = SB
    return out


def ratio(x: dict, ref: dict, key: str) -> dict | None:
    """x / ref for the mean (both restricted to the shorter longest follow-up) or a quantile, with an interval."""
    if not x.get("n") or not ref.get("n") or "_SB" not in x or "_SB" not in ref:
        return None
    if key == "mean":
        tau = min(x["follow_up_max"], ref["follow_up_max"])
        a = rmst(x["_SB"], x["_km"].times, tau)
        b = rmst(ref["_SB"], ref["_km"].times, tau)
        point = rmst(x["_S"], x["_km"].times, tau)[0] / rmst(ref["_S"], ref["_km"].times, tau)[0]
        return {"ratio": float(point), "ci": ci(a / b), "tau": tau}
    p = int(key[1:]) / 100
    a, b = quantile(x["_SB"], x["_km"].times, p), quantile(ref["_SB"], ref["_km"].times, p)
    if np.isfinite(a).mean() < 0.975 or np.isfinite(b).mean() < 0.975 or not math.isfinite(x[key]) \
            or not math.isfinite(ref[key]):
        return None
    ok = np.isfinite(a) & np.isfinite(b)
    return {"ratio": x[key] / ref[key], "ci": ci(a[ok] / b[ok])}


def mixture(parts: list[dict]) -> dict:
    """The family-standardized curve: the mean of the families' curves on their union of times."""
    parts = [s for s in parts if s.get("n")]
    if len(parts) < 2:
        return {}
    grid = np.unique(np.concatenate([s["_km"].times for s in parts]))
    S = np.mean([step_at(s["_km"].times, s["_S"][0], grid) for s in parts], axis=0)[None, :]
    # the mean is exact when every family's curve reaches 0; otherwise restricted to the shorter follow-up
    exact = all(s["surv_end"] <= 1e-12 for s in parts)
    tau = (max if exact else min)(s["follow_up_max"] for s in parts)
    return {"mean": float(rmst(S, grid, tau)[0]), "tau": tau,
            **{f"p{round(100 * p)}": float(quantile(S, grid, p)[0]) for p in PS}}


# -- cross-checks -------------------------------------------------------------------------------------------------------
def little(rows: list[dict], tc: TraceCell) -> dict:
    """Time counted turns spent in progress inside [a, window end] over the turns that ended there; a is when the
    last warm-up filler had ended, so every slot ran a counted session."""
    fill_end = max([r.get("t1") or 0.0 for r in tc.log if r.get("filler") and "label" in r] or [tc.ws])
    a = min(max(tc.ws, fill_end), tc.we)
    area, departures = 0.0, 0
    for r in rows:
        if r["filler"] or r["end"] == "refused" or r["t0"] is None:
            continue
        end = tc.we if r["end"] == "cut" else r["t1"]
        area += max(0.0, min(end, tc.we) - max(r["t0"], a))
        departures += r["end"] != "cut" and a <= r["t1"] <= tc.we
    return {"from_s": a - tc.ws, "departures": departures, "W": area / departures if departures else None,
            "in_progress": area / (tc.we - a) if tc.we > a else None}


def turns_h_split(rows: list[dict], tc: TraceCell) -> dict:
    """The turn_end events inside the window, which the stored turns/h counts, by session kind and end class."""
    split = Counter(("filler " if r["filler"] else "") + r["end"] for r in rows if tc.inside(r["t1"]))
    total = sum(split.values())
    killed = split.get("filler killed", 0)
    return {"turn_ends": total, "turns_h": total / tc.hours, "turns_h_without_killed": (total - killed) / tc.hours,
            "split": dict(sorted(split.items()))}


def session_sample(tc: TraceCell):
    d, ev, cl = [], [], []
    for sess in tc.sessions:
        meta = sess["meta"]
        if meta.get("filler"):
            continue
        starts = [wa.ts(e) for e in sess["events"] if e.get("event") == "turn_start"]
        ends = [wa.ts(e) for e in sess["events"] if e.get("event") == "turn_end"]
        if not starts or not tc.inside(starts[0]):
            continue
        done = (meta.get("status") in ("ok", "budget") and len(ends) == len(starts) and ends[-1] is not None
                and ends[-1] <= tc.we)
        d.append((ends[-1] if done else tc.we) - starts[0])
        ev.append(done)
        cl.append(meta.get("spec") or meta["label"])
    return np.asarray(d, float), np.asarray(ev, bool), cl


# -- lead calls ---------------------------------------------------------------------------------------------------------
def call_summary(cond: str, topic: str, cells_root: Path, tc: TraceCell) -> dict:
    """Lead calls received in the window (the stored definition: work calls of every session), their server split,
    wait-only calls, and every lead call sent in the window with the calls in flight at the cut censored."""
    if topic == "19":
        import ef_analyze as ea
        w = ea.EfCell(cond, cells_root)
    else:
        w = wa.WCell(cond, cells_root)
    work = w.lead_in_window(work_only=True)
    lat = [r["lat"] for r in work]
    waits = [r["lat"] for r in w.lead_in_window(work_only=False) if w.wait_only(r)]
    part = lambda k: [(r.get("parts") or {}).get(k) for r in work]  # noqa: E731
    out = {"work_calls": len(work), "lat_p50": wa.q(lat, 0.5), "lat_mean": float(np.mean(lat)) if lat else None,
           "lat_p95": wa.q(lat, 0.95), "lat_p99": wa.q(lat, 0.99), "wait_only_calls": len(waits),
           "wait_only_p50": wa.q(waits, 0.5)}
    for k in ("queue", "prefill", "decode"):
        vals = [v for v in part(k) if v is not None]
        out[f"{k}_p50"], out[f"{k}_p95"] = wa.q(vals, 0.5), wa.q(vals, 0.95)
    captured = {(s["meta"]["label"], c["call_index"]): c for s in w.cell.sessions for c in s["calls"]}
    d, e, cl = [], [], []
    stopped = []
    for sess in tc.sessions:
        meta, events = sess["meta"], sess["events"]
        for i, ev in enumerate(events):
            if ev.get("event") != "mu_call" or (ev.get("data") or {}).get("purpose") != "lead":
                continue
            c = captured.get((meta["label"], ev["data"].get("call_index")))
            if c is not None and "response" not in c:
                continue                                   # failed before a reply (refused, overflow): not a call
            t_send = c["t_send"] if c is not None else wa.ts(ev)
            if not tc.inside(t_send):
                continue
            if c is not None and c["t_recv"] <= tc.we:
                d.append(c["t_recv"] - c["t_send"])
                e.append(True)
            elif c is not None:                            # answered after the window end
                d.append(tc.we - t_send)
                e.append(False)
            else:                  # its session was stopped mid-call (the capture never got it): the trace's error
                resp = next((x for x in events[i + 1:] if x.get("event") == "model_response"), None)
                age = min(wa.ts(resp) if resp is not None else tc.we, tc.we) - t_send
                d.append(age)
                e.append(False)
                stopped.append(age)
            cl.append(meta.get("spec") or meta["label"])
    out["sent_in_window"] = summarize(np.asarray(d, float), np.asarray(e, bool), cl, cond, 0, 0)
    out["stopped_mid_call"] = len(stopped)
    out["stopped_age_p50"] = wa.q(stopped, 0.5)
    for k in ("_km", "_S"):
        out["sent_in_window"].pop(k, None)
    del w
    return out


# -- one cell, one topic --------------------------------------------------------------------------------------------------
def stored_values(topic: str) -> dict:
    cfg, out = TOPICS[topic], {}
    for name, key in cfg["stored"]:
        path = cfg["dir"] / "data" / "tables" / name
        if path.exists():
            for cond, row in (json.loads(path.read_text()).get(key) or {}).items():
                out.setdefault(cond, row)
    return out


def analyze_cell(cond: str, topic: str, iters: int, calls: bool) -> dict:
    cfg = TOPICS[topic]
    cells_root = cfg["dir"] / "data" / "cells"
    tc = TraceCell(cells_root / cond)
    rows = turn_rows(tc)
    seed = cfg["seed"]
    d, e, cl, fam = sample(rows, tc)
    primary = summarize(d, e, cl, cond, iters, seed)
    ends = Counter(r["end"] for r in rows if not r["filler"] and tc.inside(r["t0"]))
    fams = {}
    for f in ("explain", "modify"):
        keep = np.asarray([x == f for x in fam], bool)
        fams[f] = summarize(d[keep], e[keep], [c for c, k in zip(cl, keep) if k], cond, 0, seed)
    mid = (tc.ws + tc.we) / 2
    halves = [summarize(*sample(rows, tc, t_from=lo, t_to=hi)[:3], cond, 0, seed) for lo, hi in
              ((tc.ws, mid), (mid, tc.we))]
    sens = {m: summarize(*sample(rows, tc, mode=m)[:3], cond, 0, seed) for m in ("normal", "all")}
    old_d, old_c = wa.turn_latencies(tc)
    old = wa.km_quantiles(old_d, old_c)
    sd, se, sc = session_sample(tc)
    out = {"cond": cond, "n_users": tc.n, "arm": tc.arm, "window_s": tc.we - tc.ws, "primary": primary,
           "ends": dict(sorted(ends.items())), "families": fams, "standardized": mixture(list(fams.values())),
           "halves_p50": [h.get("p50") for h in halves], "sensitivity": sens,
           "old": {"turn_p50": old[0.5], "turn_p95": old[0.95], "turns_started": len(old_d),
                   "censored": (sum(old_c) / len(old_c)) if old_c else None},
           "little": little(rows, tc), "turns_h": turns_h_split(rows, tc),
           "sessions": summarize(sd, se, sc, cond, 0, seed)}
    if calls:
        out["calls"] = call_summary(cond, topic, cells_root, tc)
    return out


def reference(cond: str, n, conds: list[str]) -> str | None:
    for ref in (f"w{n:02d}_none", f"x{n}_none"):
        if ref in conds and ref != cond:
            return ref
    return None


def verify(cells: dict, stored: dict) -> list[str]:
    """Mismatches between the stored H5 values and this module's 'old' sample, calls and turns/h."""
    bad = []
    close = lambda a, b: (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9)  # noqa
    for cond, c in cells.items():
        s = stored.get(cond)
        if not s:
            continue
        checks = {"turn_p50": (c["old"]["turn_p50"], s.get("turn_p50")),
                  "turn_p95": (c["old"]["turn_p95"], s.get("turn_p95")),
                  "turns_started": (c["old"]["turns_started"], s.get("turns_started")),
                  "censored": (c["old"]["censored"], s.get("censored")),
                  "turns_h": (c["turns_h"]["turns_h"], s.get("turns_h"))}
        if "calls" in c:
            checks.update(lat_p50=(c["calls"]["lat_p50"], s.get("lat_p50")),
                          lat_mean=(c["calls"]["lat_mean"], s.get("lat_mean")))
        for k, (mine, theirs) in checks.items():
            if not close(mine, theirs):
                bad.append(f"{cond} {k}: {mine} != stored {theirs}")
    return bad


# -- output -------------------------------------------------------------------------------------------------------------
def fs(x, nd: int = 0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "not reached"
    return f"{x:,.{nd}f}"


def fmt_q(s: dict, key: str) -> str:
    """A KM quantile with its interval: '> t' when not reached, n/a for p99 under 100 turns, † for a thin tail."""
    if not s.get("n"):
        return "—"
    p = int(key[1:]) / 100
    if key == "p99" and s["n"] < 100:
        return "n/a"
    v = s[key]
    if not math.isfinite(v):
        return f"> {fs(s['follow_up_max'])}"
    dag = "†" if s["n"] * (1 - p) < 5 else ""
    c = (s.get("ci") or {}).get(key)
    return f"{fs(v)}{dag}" + (f" [{fs(c[0])}, {'∞' if math.isinf(c[1]) else fs(c[1])}]" if c else "")


def fmt_mean(s: dict, with_ci: bool = True) -> str:
    if not s.get("n"):
        return "—"
    v = fs(s["mean"])
    if s["surv_end"] > 0.01:
        v = f"≥ {v}"
    elif s["surv_end"] > 1e-12:
        v = f"{v}*"
    c = (s.get("ci") or {}).get("mean")
    return v + (f" [{fs(c[0])}, {fs(c[1])}]" if with_ci and c and s["surv_end"] <= 0.01 else "")


def fmt_ratio(r: dict | None) -> str:
    return "—" if not r else f"{r['ratio']:.2f} [{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]"


def tables(topic: str, cells: dict, bad: list[str]) -> str:
    conds = list(cells)
    L = [f"# Completion time per task, topic {topic} (wt_latency.py; exploratory, added 2026-10-29)", "",
         "A task entry is one turn (one task prompt). Completion time = turn_start to turn_end, including queueing, "
         "model calls, tools, model waits, harness holds and gate waits. Kaplan-Meier over turns of counted "
         "sessions started in the window, censored at its end; seconds. Mean: area under the curve up to the "
         "longest follow-up (≥ = the curve does not reach 0, so a lower bound; * = under 1 % left). "
         "\"> t\" = not reached by the longest follow-up t. † = fewer than 5 turns expected beyond the quantile. "
         "[95 %]: spec-cluster bootstrap, 2,000 draws; it shows sampling noise only (see the A/A row in topic 18). "
         "Ratios: against `none` at the same N.", "",
         "## L1 · Completion time per task", "",
         "| cell | turns (specs) | ended normally | capped | cut | refused (left out) | mean [95 %] | p50 [95 %] | "
         "p95 [95 %] | p99 | mean ÷ none | p50 ÷ none | p95 ÷ none |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in conds:
        x, s = cells[c], cells[c]["primary"]
        ends = x["ends"]
        L.append(f"| {c} | {s.get('n', 0)} ({s.get('clusters', 0)}) | {ends.get('normal', 0)} | "
                 f"{sum(ends.get(k, 0) for k in CAPPED)} | {ends.get('cut', 0) + ends.get('error', 0)} | "
                 f"{ends.get('refused', 0)} | {fmt_mean(s)} | {fmt_q(s, 'p50')} | {fmt_q(s, 'p95')} | "
                 f"{fmt_q(s, 'p99')} | {fmt_ratio(x.get('ratio', {}).get('mean'))} | "
                 f"{fmt_ratio(x.get('ratio', {}).get('p50'))} | {fmt_ratio(x.get('ratio', {}).get('p95'))} |")
    L += ["", "Capped: the turn ended at its 100-call cap, the session's 300-call cap, or a prompt overflow. Refused: "
          "the session's budget was already spent, so the turn ended at once without a call.", "",
          "## L2 · Cross-checks", "",
          "| cell | KM mean | Little's law W (departures; from s) | naive mean of finished turns | stored KM p50 / p95 "
          "(reproduced) | normal-only p50 | all-sessions p50 | p50, first / second half of the window |",
          "|---|---|---|---|---|---|---|---|"]
    for c in conds:
        x = cells[c]
        li, o = x["little"], x["old"]
        h = x["halves_p50"]
        L.append(f"| {c} | {fmt_mean(x['primary'], False)} | {fs(li['W'])} ({li['departures']}; {fs(li['from_s'])}) | "
                 f"{fs(x['primary'].get('naive_mean'))} | {fs(o['turn_p50'])} / {fs(o['turn_p95'])} | "
                 f"{fmt_q(x['sensitivity']['normal'], 'p50')} | {fmt_q(x['sensitivity']['all'], 'p50')} | "
                 f"{fs(h[0])} / {fs(h[1])} |")
    L += ["", "## L3 · By task family", "",
          "| cell | EXPLAIN turns | EXPLAIN mean / p50 / p95 | MODIFY turns | MODIFY mean / p50 / p95 | "
          "standardized mean / p50 / p95 |", "|---|---|---|---|---|---|"]
    for c in conds:
        f = cells[c]["families"]
        st = cells[c]["standardized"]
        row = [f"| {c}"]
        for k in ("explain", "modify"):
            s = f[k]
            row += [f"{s.get('n', 0)}", f"{fmt_mean(s, False)} / {fmt_q(s, 'p50')} / {fmt_q(s, 'p95')}"]
        row.append(f"{fs(st.get('mean'))} / {fs(st.get('p50'))} / {fs(st.get('p95'))}" if st else "—")
        L.append(" | ".join(row) + " |")
    if any("calls" in cells[c] for c in conds):
        L += ["", "## L4 · Lead calls (end-to-end latency of one model call, s)", "",
              "| cell | work calls received in the window | mean | p50 | p95 | p99 | queue p50 / p95 | prefill p50 | "
              "decode p50 | wait-only calls (p50) | in flight when their session was stopped (median age) | sent in "
              "the window: KM p50 / p95 / p99 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for c in conds:
            k = cells[c].get("calls")
            if not k:
                continue
            s = k["sent_in_window"]
            L.append(f"| {c} | {k['work_calls']:,} | {fs(k['lat_mean'], 1)} | {fs(k['lat_p50'], 1)} | "
                     f"{fs(k['lat_p95'], 1)} | {fs(k['lat_p99'], 1)} | {fs(k['queue_p50'], 1)} / "
                     f"{fs(k['queue_p95'], 1)} | {fs(k['prefill_p50'], 1)} | {fs(k['decode_p50'], 1)} | "
                     f"{k['wait_only_calls']} ({fs(k['wait_only_p50'], 1)}) | {k['stopped_mid_call']} "
                     f"({fs(k['stopped_age_p50'])}) | {fmt_q(s, 'p50')} / {fmt_q(s, 'p95')} / {fmt_q(s, 'p99')} |")
    L += ["", "## L5 · What the stored turns/h counts", "",
          "| cell | stored-definition turns/h | without killed warm-up turns | turn ends in the window by kind |",
          "|---|---|---|---|"]
    for c in conds:
        t = cells[c]["turns_h"]
        L.append(f"| {c} | {fs(t['turns_h'], 1)} | {fs(t['turns_h_without_killed'], 1)} | "
                 + ", ".join(f"{k} {v}" for k, v in t["split"].items()) + " |")
    L += ["", "## L6 · Sessions (4 turns and 3 think pauses; counted sessions whose first turn started in the window)",
          "", "| cell | sessions | finished | mean | p50 | p95 |", "|---|---|---|---|---|---|"]
    for c in conds:
        s = cells[c]["sessions"]
        L.append(f"| {c} | {s.get('n', 0)} | {s.get('events', 0)} | {fmt_mean(s, False)} | {fmt_q(s, 'p50')} | "
                 f"{fmt_q(s, 'p95')} |")
    L += ["", "## Reproduction check", "",
          "The 'old' sample (wt_analyze.turn_latencies on the traces) against the stored H5 values (KM p50, p95, turns "
          "started, censored share), the stored call p50 and mean, and the stored turns/h: "
          + ("all match." if not bad else f"{len(bad)} mismatches: " + "; ".join(bad))]
    return "\n".join(L) + "\n"


def clean(x):
    """JSON-ready: private arrays dropped, +inf as null (each quantile's reach is in the bootstrap's reached_share)."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items() if not str(k).startswith("_")}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return None if not math.isfinite(float(x)) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic", choices=sorted(TOPICS), required=True)
    ap.add_argument("--cells", default=None, help="comma-separated; default: the topic's main cells present")
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--no-calls", action="store_true", help="skip the lead-call tables (no captures loaded)")
    ap.add_argument("--out", default=None, help="default: <topic>/data/tables/latency.md (+ .json)")
    ap.add_argument("--verify", action="store_true", help="exit 1 unless the stored values reproduce")
    a = ap.parse_args()
    cfg = TOPICS[a.topic]
    if a.topic == "19":
        import ef_analyze  # noqa: F401  (topic 19's conditions and template tail, before any cell is read)
    names = a.cells.split(",") if a.cells else cfg["cells"]
    names = [c for c in names if (cfg["dir"] / "data" / "cells" / c / "COMPLETE").exists()]
    cells = {}
    for cond in names:
        print(f"[latency] {cond}", flush=True)
        cells[cond] = analyze_cell(cond, a.topic, a.iters, not a.no_calls)
    for cond, x in cells.items():
        ref = reference(cond, x["n_users"], names)
        if ref:
            x["ratio"] = {k: ratio(x["primary"], cells[ref]["primary"], k) for k in ("mean", "p50", "p95")}
            x["ratio_ref"] = ref
    bad = verify(cells, stored_values(a.topic))
    md = tables(a.topic, cells, bad)
    out = Path(a.out) if a.out else cfg["dir"] / "data" / "tables" / "latency.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(clean({"topic": a.topic, "cells": cells, "mismatches": bad}),
                                                   indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(md)
    print(f"[latency] wrote {out} and {out.with_suffix('.json')}")
    if a.verify and bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
