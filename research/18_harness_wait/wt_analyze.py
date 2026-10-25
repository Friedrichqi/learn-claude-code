#!/usr/bin/env python3
"""Tables of research/18_harness_wait (offline): what waiting does to a crowded server.

Inputs per cell (data/cells/<cond>/): window.json (the clock), every session's capture and trace, the server's
WTScheduler log (topic 11's records plus `wt` records), the /wt/status audit log (wt_ext.jsonl) and the 1 Hz
scrape. Topic 11's analyzer (research/11_multiuser_kv/mu_analyze.py) does the joins, the token classes and the
step model; everything here is clipped to the cell's measurement window [window_start, window_end].

  H1 holds and waits     harness holds per hold point; the model's wait calls and seconds; status lines
  H2 decisions           how often a check found the server crowded; how bunched the releases were; the
                         autocorrelation of the waiting queue (oscillation)
  H3 prefix survival     a held / waiting session's cached prefix before and after, and the hit of its next call
  H4 fairness            Jain's index over the sessions' work calls per hour
  H5 throughput          work lead calls / h (primary; a lead call whose reply is only `wait` is not work), turns
                         / h, call latency, hold + wait per call, Kaplan-Meier turn latency (turns started in the
                         window, censored at its end), hit rate, GPU time by class (step model fit on these cells)
  H6 overhead            /wt/status requests per second, the status age the harness saw
  R                      recovery ratio (X - none) / (sgate - none) per N, with a block bootstrap (10-min blocks)
  exploratory contrasts  each x* cell minus its main-run twin and the server gate (added after the main run)

    python research/18_harness_wait/wt_analyze.py --cells w16_none,w16_sgate,... [--out data/tables/main.md]
    python research/18_harness_wait/wt_analyze.py --smoke s_small,s_gate2,s_mstock,s_hshare
    python research/18_harness_wait/wt_analyze.py --pilot p08_none,p16_none,p16_m_stock,...
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import wt_workloads as ww  # noqa: E402
import mu_analyze as ma  # noqa: E402  research/11_multiuser_kv

ma.mw.CONDITIONS.update(ww.conditions())

BLOCK_S = 600.0


def ts(event: dict) -> float | None:
    try:
        return datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def q(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return float(np.percentile(values, 100 * p))


def km_quantiles(durations: list[float], censored: list[bool], ps=(0.5, 0.95)) -> dict:
    """Kaplan-Meier quantiles of turn latency; None when the curve never falls that far (too censored)."""
    order = sorted(zip(durations, censored))
    n = len(order)
    surv, out, i = 1.0, {p: None for p in ps}, 0
    while i < n:
        t = order[i][0]
        events = sum(1 for d, c in order[i:] if d == t and not c)
        ties = sum(1 for d, _ in order[i:] if d == t)
        at_risk = n - i
        if events:
            surv *= 1 - events / at_risk
            for p in ps:
                if out[p] is None and surv <= 1 - p + 1e-12:
                    out[p] = t
        i += ties
    return out


class WCell:
    """One window cell: topic 11's Cell plus the window, the arm, holds, waits and the status log."""

    def __init__(self, cond: str, root: Path = ww.CELLS):
        self.cond = cond
        self.c = ma.mw.cond(cond) if cond in ma.mw.CONDITIONS else {}
        self.cell = ma.Cell(cond, root)
        self.root = root / cond
        win = json.loads((self.root / "window.json").read_text())
        self.ws, self.we, self.t0 = win["window_start"], win["window_end"], win["t_start"]
        self.arm, self.n = win.get("arm"), win.get("n")
        self.hours = (self.we - self.ws) / 3600
        self.cell.token_classes()
        self.ext = []
        for srv_dir in sorted((self.root / "server").glob("*")):
            self.ext += ma.read_jsonl(srv_dir / "wt_ext.jsonl")
        self.content = {}                              # (label, call index) -> response content blocks
        for sess in self.cell.sessions:
            for c in sess["calls"]:
                resp = c.get("response")
                if resp:
                    self.content[(sess["meta"]["label"], c["call_index"])] = resp.get("content") or []

    def inside(self, t) -> bool:
        return t is not None and self.ws <= t <= self.we

    def wait_only(self, row) -> bool:
        blocks = self.content.get((row["label"], row["call"])) or []
        uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        return bool(uses) and all(b.get("name") == "wait" for b in uses)

    def lead(self) -> list[dict]:
        return [r for r in self.cell.calls if r["purpose"] == "lead" and r.get("lat") is not None]

    def lead_in_window(self, work_only: bool = True) -> list[dict]:
        return [r for r in self.lead() if self.inside(r["t_recv"]) and not (work_only and self.wait_only(r))]

    def events(self, name: str):
        for sess in self.cell.sessions:
            for e in sess["events"]:
                if e["event"] == name:
                    yield sess["meta"]["label"], e


# -- H5: throughput and latency --------------------------------------------------------------------------------
def block_counts(times: list[float], ws: float, we: float) -> list[float]:
    nb = max(1, int(round((we - ws) / BLOCK_S)))
    width = (we - ws) / nb
    counts = [0.0] * nb
    for t in times:
        if ws <= t <= we:
            counts[min(nb - 1, int((t - ws) / width))] += 1
    return [c * 3600 / width for c in counts]


def turn_latencies(w: WCell) -> tuple[list[float], list[bool]]:
    durations, censored = [], []
    for sess in w.cell.sessions:
        starts = {}
        for e in sess["events"]:
            t = ts(e)
            if e["event"] == "turn_start" and t is not None:
                starts[e["turn_id"]] = t
            elif e["event"] == "turn_end" and e["turn_id"] in starts:
                s0 = starts.pop(e["turn_id"])
                if w.inside(s0):
                    end = t if t is not None else w.we
                    durations.append(min(end, w.we) - s0)
                    censored.append(end > w.we)
        for tid, s0 in starts.items():                    # never ended: censored at the window's end
            if w.inside(s0):
                durations.append(w.we - s0)
                censored.append(True)
    return durations, censored


def gpu_window(w: WCell) -> dict:
    out = Counter()
    for srv in w.cell.servers:
        for s in srv.steps:
            sh = s.get("share")
            if not sh or s.get("g0") is None or s.get("g1") is None or s["gpu"] <= 0:
                continue
            a, b = srv.wall(s["g0"]), srv.wall(s["g1"])
            frac = max(0.0, min(b, w.we) - max(a, w.ws)) / (b - a)
            if frac <= 0:
                continue
            k = len({c[0] for c in sh["pf"]} | {d[0] for d in sh["dec"]}) or 1
            base = sh["fixed"] / k
            for ri, cost, n, fresh, ev, pre in sh["pf"]:
                if n:
                    out["prefill_fresh"] += frac * (cost + base) * fresh / n
                    out["prefill_evicted"] += frac * (cost + base) * ev / n
                    out["prefill_preempted"] += frac * (cost + base) * pre / n
            out["decode"] += frac * sum(cost + base for _, cost in sh["dec"])
            out["busy"] += frac * s["gpu"]
    wall = w.we - w.ws
    out["idle"] = max(0.0, wall - out["busy"])
    return {k: v / wall for k, v in out.items()}


def hold_wait_before(w: WCell) -> dict:
    """Seconds of harness hold and of model wait per session, in the window."""
    held, waited = defaultdict(float), defaultdict(float)
    for label, e in w.events("hold_end"):
        t = ts(e)
        if w.inside(t):
            held[label] += (e["data"].get("held_s") or 0.0)
    for label, e in w.events("wait_detail"):
        t = ts(e)
        if w.inside(t):
            waited[label] += (e["data"].get("slept_s") or 0.0)
    return {"held": held, "waited": waited}


def h5_row(w: WCell) -> dict:
    work = w.lead_in_window(work_only=True)
    allc = w.lead_in_window(work_only=False)
    blocks = block_counts([r["t_recv"] for r in work], w.ws, w.we)
    ends = [ts(e) for _, e in w.events("turn_end")]
    turns_h = sum(1 for t in ends if w.inside(t)) / w.hours
    d, c = turn_latencies(w)
    km = km_quantiles(d, c)
    np_tot = sum(r.get("np") or 0 for r in allc)
    hit_tot = sum(r.get("cached") or 0 for r in allc)
    hw = hold_wait_before(w)
    g = gpu_window(w)
    pre = sum(r.get("npre") or 0 for r in allc)
    lat = [r["lat"] for r in work]
    return {"cond": w.cond, "arm": w.arm, "n": w.n, "work_calls_h": len(work) / w.hours, "blocks": blocks,
            "block_ci": (q(blocks, 0.025), q(blocks, 0.975)), "wait_only_calls": len(allc) - len(work),
            "turns_h": turns_h, "lat_p50": q(lat, 0.5), "lat_mean": float(np.mean(lat)) if lat else None,
            "hold_per_call": sum(hw["held"].values()) / len(work) if work else None,
            "wait_per_call": sum(hw["waited"].values()) / len(work) if work else None,
            "turn_p50": km[0.5], "turn_p95": km[0.95], "turns_started": len(d),
            "censored": (sum(c) / len(c)) if c else None, "hit_rate": hit_tot / np_tot if np_tot else None,
            "prompt_per_call": np_tot / len(allc) if allc else None, "preemptions": pre,
            "gpu": g}


# -- R: recovery ratio with a block bootstrap ----------------------------------------------------------------
def recovery(x: dict, none: dict, gate: dict, iters: int = 4000, seed: int = 18) -> dict:
    point_den = gate["work_calls_h"] - none["work_calls_h"]
    point = (x["work_calls_h"] - none["work_calls_h"]) / point_den if point_den else None
    rng = np.random.default_rng(seed)
    bx, bn, bg = (np.array(r["blocks"]) for r in (x, none, gate))
    draws = []
    for _ in range(iters):
        mx = bx[rng.integers(0, len(bx), len(bx))].mean()
        mn = bn[rng.integers(0, len(bn), len(bn))].mean()
        mg = bg[rng.integers(0, len(bg), len(bg))].mean()
        if mg - mn != 0:
            draws.append((mx - mn) / (mg - mn))
    lo, hi = (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))) if draws else (None, None)
    return {"R": point, "lo": lo, "hi": hi}


# -- the pre-registered decision rules (prereg.md) -------------------------------------------------------------
def boot_means(blocks: list[float], iters: int, rng) -> np.ndarray:
    x = np.array(blocks, dtype=float)
    return x[rng.integers(0, len(x), (iters, len(x)))].mean(axis=1)


def decisions(h5: dict, iters: int = 4000, seed: int = 18) -> dict:
    """R1-R4 of prereg.md on the main cells (w08/w16/w32_*). Bootstrap draws of each cell's mean over its 10-min
    blocks, independent across cells; the noise floor is the A/A difference."""
    rng = np.random.default_rng(seed)
    cells = {c: r for c, r in h5.items() if c.startswith("w")}
    draws = {c: boot_means(r["blocks"], iters, rng) for c, r in cells.items()}
    point = {c: r["work_calls_h"] for c, r in cells.items()}
    out = {"rules": {}}
    if "w16_none" not in point or "w16_none_aa" not in point:
        out["note"] = "no A/A pair: decisions not evaluated"
        return out
    d_aa = abs(point["w16_none_aa"] - point["w16_none"])
    out["delta_aa"] = d_aa

    def ci(x):
        return float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5))

    r1 = {}
    for n in (16, 32):
        none, gate = f"w{n:02d}_none", f"w{n:02d}_sgate"
        if none not in point:
            continue
        gate_gain = point[gate] - point[none] if gate in point else None
        for arm in ("m_stock", "m_share", "h_backoff", "h_share"):
            x = f"w{n:02d}_{arm}"
            if x not in point:
                continue
            g = draws[x] - draws[none]
            lo, hi = ci(g)
            row = {"gain": point[x] - point[none], "gain_ci": (lo, hi)}
            if gate_gain is not None and gate_gain > d_aa:
                row["R"] = row["gain"] / gate_gain
                rd = g / (draws[gate] - draws[none])
                row["R_ci"] = ci(rd[np.isfinite(rd)])
            row["recovers"] = bool(row.get("R") is not None and row["R"] >= 0.5 and lo > d_aa)
            row["hurts"] = bool(hi < -d_aa)
            r1[x] = row
        if gate_gain is not None:
            r1[f"{gate} (gate gain)"] = {"gain": gate_gain, "gain_ci": ci(draws[gate] - draws[none]),
                                        "gate_beats_noise": gate_gain > d_aa}
    out["rules"]["R1"] = r1
    r2, r3 = {}, {}
    for n in (16, 32):
        for view in ("stock", "share"):
            m, h = f"w{n:02d}_m_{view}", f"w{n:02d}_h_{'backoff' if view == 'stock' else 'share'}"
            if m in point and h in point:
                dd = draws[m] - draws[h]
                lo, hi = ci(dd)
                r2[f"{m} - {h}"] = {"D": point[m] - point[h], "ci": (lo, hi),
                                    "decider_matters": bool(abs(point[m] - point[h]) > d_aa and (lo > 0 or hi < 0))}
        for a, b_ in (("m_share", "m_stock"), ("h_share", "h_backoff")):
            x, y = f"w{n:02d}_{a}", f"w{n:02d}_{b_}"
            if x in point and y in point:
                dd = draws[x] - draws[y]
                lo, hi = ci(dd)
                r3[f"{x} - {y}"] = {"S": point[x] - point[y], "ci": (lo, hi),
                                    "information_matters": bool(abs(point[x] - point[y]) > d_aa and (lo > 0 or hi < 0))}
    out["rules"]["R2"], out["rules"]["R3"] = r2, r3
    r4 = {}
    if "w08_none" in point:
        for arm in ("m_stock", "m_share"):
            x = f"w08_{arm}"
            if x in point:
                hit_x, hit_n = cells[x]["hit_rate"], cells["w08_none"]["hit_rate"]
                r4[x] = {"diff": point[x] - point["w08_none"], "hit_diff_points": 100 * (hit_x - hit_n),
                         "no_harm": bool(point[x] - point["w08_none"] >= -d_aa and abs(hit_x - hit_n) <= 0.02)}
    out["rules"]["R4"] = r4
    return out


def decisions_md(dec: dict) -> str:
    if "delta_aa" not in dec:
        return ""
    f = lambda x: "–" if x is None else f"{x:,.0f}"           # noqa: E731
    lines = ["", "## Pre-registered decisions (prereg.md)", "",
             f"Noise floor Δ_AA = |w16_none_aa − w16_none| = {f(dec['delta_aa'])} work lead calls/h.", "",
             "**R1 recovery** (gain = X − none; R = gain / (sgate − none))", "",
             "| cell | gain | gain 95% | R | R 95% | recovers | hurts |", "|---|---|---|---|---|---|---|"]
    for c, r in dec["rules"]["R1"].items():
        if "gate_beats_noise" in r:
            lines.append(f"| {c} | {f(r['gain'])} | [{f(r['gain_ci'][0])}, {f(r['gain_ci'][1])}] | – | – | "
                         f"gate beats noise: {'yes' if r['gate_beats_noise'] else 'no'} | – |")
            continue
        rv, rci = r.get("R"), r.get("R_ci")
        r_txt = "–" if rv is None else f"{rv:.2f}"
        rci_txt = "–" if not rci else f"[{rci[0]:.2f}, {rci[1]:.2f}]"
        lines.append(f"| {c} | {f(r['gain'])} | [{f(r['gain_ci'][0])}, {f(r['gain_ci'][1])}] | {r_txt} | {rci_txt} | "
                     f"{'YES' if r['recovers'] else 'no'} | {'YES' if r['hurts'] else 'no'} |")
    for key, name, col in (("R2", "R2 decider (model − harness, same view)", "D"),
                           ("R3", "R3 information (share − stock, same decider)", "S")):
        lines += ["", f"**{name}**", "", f"| contrast | {col} | 95% | differs |", "|---|---|---|---|"]
        for c, r in dec["rules"][key].items():
            flag = r.get("decider_matters", r.get("information_matters"))
            lines.append(f"| {c} | {f(r[col])} | [{f(r['ci'][0])}, {f(r['ci'][1])}] | {'YES' if flag else 'no'} |")
    lines += ["", "**R4 no harm at N=8**", "", "| cell | difference vs none | hit-rate difference (points) | no harm |",
              "|---|---|---|---|"]
    for c, r in dec["rules"]["R4"].items():
        lines.append(f"| {c} | {f(r['diff'])} | {r['hit_diff_points']:+.1f} | {'YES' if r['no_harm'] else 'no'} |")
    return "\n".join(lines) + "\n"


# Exploratory (added after the main run, see README "Changes during the run"): the x cells against the main run.
def exploratory_contrasts(h5: dict, iters: int = 4000, seed: int = 18) -> dict:
    """Each exploratory cell (x*) minus the main cell of the same rule at its N (x16_h_share_start - w16_h_share)
    and minus the server gate there, with the decisions' bootstrap (each cell's block means drawn independently)."""
    rng = np.random.default_rng(seed)
    out = {}
    for c, r in h5.items():
        if not c.startswith("x"):
            continue
        for other in (f"w{r['n']:02d}_{r['arm'].removesuffix('_start')}", f"w{r['n']:02d}_sgate"):
            if other in h5:
                d = boot_means(r["blocks"], iters, rng) - boot_means(h5[other]["blocks"], iters, rng)
                out[f"{c} - {other}"] = {"D": r["work_calls_h"] - h5[other]["work_calls_h"],
                                         "ci": (float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))}
    return out


# -- H1 / H2 / H3 / H4 / H6 ------------------------------------------------------------------------------------
def h1_row(w: WCell) -> dict:
    checks = [e for _, e in w.events("hold_check") if w.inside(ts(e))]
    ends = [e for _, e in w.events("hold_end") if w.inside(ts(e))]
    waits = [e for _, e in w.events("wait_detail") if w.inside(ts(e))]
    held = [e["data"].get("held_s") or 0.0 for e in ends]
    why = Counter(e["data"].get("why") for e in ends)
    req = [e["data"].get("requested_s") for e in waits]
    slept = [e["data"].get("slept_s") for e in waits]
    work = w.lead_in_window(True)
    lines = 0
    for sess in w.cell.sessions:
        for c in sess["calls"]:
            if c.get("purpose") != "lead" or not w.inside(c.get("t_send")):
                continue
            last = (c.get("messages") or [{}])[-1]
            content = last.get("content")
            if isinstance(content, list) and any(isinstance(b, dict) and str(b.get("text", "")).startswith("[server]")
                                                 for b in content):
                lines += 1
    return {"cond": w.cond, "arm": w.arm, "checks": len(checks), "held_frac": len(ends) / len(checks) if checks else None,
            "hold_p50": q(held, 0.5), "hold_p95": q(held, 0.95), "hold_max": max(held) if held else None,
            "hold_why": dict(why), "waits": len(waits), "wait_per_100_calls": 100 * len(waits) / len(work) if work else None,
            "wait_req_p50": q(req, 0.5), "wait_req_p95": q(req, 0.95), "wait_slept_sum": sum(s or 0 for s in slept),
            "status_lines": lines}


# Exploratory (added after the freeze, see README "Changes during the run"): runs of wait-only rounds.
STOCK_RE = re.compile(r"\[server\] running (\d+), waiting (\d+) \((\d+) for KV memory\); KV memory in use (\d+)%")
SHARE_RE = re.compile(r"\[server\] KV pool ([\d,]+) tokens; sessions ahead of you hold ([\d,]+); your context "
                      r"([\d,]+), ([\d,]+) still cached; (\d+) requests waiting")


def twin_would_hold(line: str | None, env: dict) -> bool | None:
    """Did the status line the model read meet its harness twin's rule? Share: h_share's (ahead + own > tau * pool).
    Stock: h_backoff's entry (a request waiting for KV memory, or KV in use >= the entry level; the line rounds KV
    to whole percent)."""
    if not line:
        return None
    if m := SHARE_RE.search(line):
        pool, ahead, own = (int(m.group(i).replace(",", "")) for i in (1, 2, 3))
        return ahead + own > float(env.get("S15_SHARE_TAU", 0.9)) * pool
    if m := STOCK_RE.search(line):
        return int(m.group(3)) >= 1 or int(m.group(4)) >= round(100 * float(env.get("S15_BACKOFF_ENTER_KV", 0.92)))
    return None


def wait_runs(seq: list[tuple]) -> list[dict]:
    """One session's lead calls in order as (turn_id, wait_only, in_window, crowded). A run is a maximal stretch of
    wait-only calls inside one turn; runs with a call in the window are kept. A run ends 'resumed' (the model did
    work next in the same turn), 'budget' (the turn had no further call: after a wait-only reply only the turn's
    call budget or an error ends it) or 'last' (the session's last call: session budget, or the cell stopped)."""
    runs, i = [], 0
    while i < len(seq):
        if not seq[i][1]:
            i += 1
            continue
        j = i
        while j < len(seq) and seq[j][1] and seq[j][0] == seq[i][0]:
            j += 1
        if any(s[2] for s in seq[i:j]):
            end = "last" if j == len(seq) else ("budget" if seq[j][0] != seq[i][0] else "resumed")
            runs.append({"len": j - i, "in_window": sum(1 for s in seq[i:j] if s[2]), "end": end,
                         "start_crowded": seq[i][3]})
        i = j
    return runs


def gpu_by_reply(w: WCell) -> dict:
    """The window's GPU time (as a share of its wall time, accounted as in gpu_window) spent on wait-only lead
    calls, on work lead calls and on other calls (compaction summaries, one-shot helpers), plus the evicted-recompute
    part of the wait-only calls' prefill."""
    flag = {id(r["rec"]): w.wait_only(r) for r in w.lead() if r.get("rec") is not None}
    out = Counter()
    for srv in w.cell.servers:
        kind = {ri: ("wait_only" if flag[id(rec)] else "work") if id(rec) in flag else "other"
                for ri, rec in srv.req.items()}
        for s in srv.steps:
            sh = s.get("share")
            if not sh or s.get("g0") is None or s.get("g1") is None or s["gpu"] <= 0:
                continue
            a, b = srv.wall(s["g0"]), srv.wall(s["g1"])
            frac = max(0.0, min(b, w.we) - max(a, w.ws)) / (b - a)
            if frac <= 0:
                continue
            base = sh["fixed"] / (len({c[0] for c in sh["pf"]} | {d[0] for d in sh["dec"]}) or 1)
            for ri, cost, n, fresh, ev, pre in sh["pf"]:
                k = kind.get(ri, "other")
                out[k] += frac * (cost + base)
                if k == "wait_only" and n:
                    out["wait_only_evicted"] += frac * (cost + base) * ev / n
            for ri, cost in sh["dec"]:
                out[kind.get(ri, "other")] += frac * (cost + base)
    wall = w.we - w.ws
    return {k: out[k] / wall for k in ("wait_only", "wait_only_evicted", "work", "other")}


def h1b_row(w: WCell) -> dict:
    env = json.loads((w.root / "window.json").read_text()).get("client_env") or {}
    runs, wo_crowded, work_crowded, looping = [], [], [], 0
    for sess in w.cell.sessions:
        label, seq = sess["meta"]["label"], []
        for c in sorted((c for c in sess["calls"] if c.get("purpose") == "lead" and c.get("response")),
                        key=lambda c: c["call_index"]):
            last = (c.get("messages") or [{}])[-1].get("content")
            line = next((b["text"] for b in last if isinstance(b, dict) and str(b.get("text", "")).startswith("[server]")),
                        None) if isinstance(last, list) else None
            crowded = twin_would_hold(line, env)
            wo = w.wait_only({"label": label, "call": c["call_index"]})
            inside = w.inside(c.get("t_recv"))
            seq.append((c.get("turn_id"), wo, inside, crowded))
            if inside and crowded is not None:
                (wo_crowded if wo else work_crowded).append(crowded)
        mine = wait_runs(seq)
        looping += any(r["len"] >= 10 for r in mine)
        runs += mine
    lens = [r["len"] for r in runs]
    wo_total = sum(r["in_window"] for r in runs)
    starts = [r["start_crowded"] for r in runs if r["start_crowded"] is not None]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    return {"cond": w.cond, "arm": w.arm, "wait_only": wo_total, "runs": len(runs), "run_p50": q(lens, 0.5),
            "run_p90": q(lens, 0.9), "run_max": max(lens) if lens else None,
            "in_runs_ge10": sum(r["in_window"] for r in runs if r["len"] >= 10) / wo_total if wo_total else None,
            "ended": dict(Counter(r["end"] for r in runs)), "sessions_ge10": looping,
            "crowded_run_start": mean(starts), "crowded_wait_only": mean(wo_crowded), "crowded_work": mean(work_crowded),
            "gpu": gpu_by_reply(w)}


def h2_row(w: WCell) -> dict:
    checks = [e["data"] for _, e in w.events("hold_check") if w.inside(ts(e))]
    crowded = sum(1 for d in checks if d.get("hold")) / len(checks) if checks else None
    rel = sorted(t for _, e in w.events("hold_end") if (t := ts(e)) is not None and w.inside(t))
    bunched = sum(1 for i, t in enumerate(rel) if (i and t - rel[i - 1] < 2.0) or
                  (i + 1 < len(rel) and rel[i + 1] - t < 2.0)) / len(rel) if rel else None
    series = []                                            # waiting queue, resampled at 1 s from the step log
    for srv in w.cell.servers:
        pts = sorted((srv.wall(s["s0"]), s["wq"]) for s in srv.steps if s.get("s0") is not None)
        j, last = 0, 0
        t = w.ws
        while t <= w.we and pts:
            while j < len(pts) and pts[j][0] <= t:
                last = pts[j][1]
                j += 1
            series.append(last)
            t += 1.0
    ac = {}
    x = np.array(series, dtype=float)
    if len(x) > 120 and x.std() > 0:
        x = x - x.mean()
        for lag in (10, 30, 60):
            ac[lag] = float(np.dot(x[:-lag], x[lag:]) / np.dot(x, x))
    return {"cond": w.cond, "arm": w.arm, "crowded_at_check": crowded, "bunched_releases": bunched,
            "queue_mean": float(np.mean(series)) if series else None, "queue_ac": ac}


def h3_row(w: WCell) -> dict:
    """Prefix survival across holds and waits (share view only reports the session's own prefix)."""
    pairs = []
    # the reading at the start; span ids count up per session, so they repeat across sessions
    starts = {(label, e.get("span_id")): e["data"] for label, e in w.events("hold_start")}
    for label, e in w.events("hold_end"):
        if not w.inside(ts(e)):
            continue
        a = (((starts.get((label, e.get("span_id"))) or {}).get("signals") or {}).get("me") or {}).get("prefix")
        b = ((e["data"].get("signals_end") or {}).get("me") or {}).get("prefix")
        if a and b is not None:
            pairs.append(b / a)
    for _, e in w.events("wait_detail"):
        if not w.inside(ts(e)):
            continue
        a = ((e["data"].get("before") or {}).get("me") or {}).get("prefix")
        b = ((e["data"].get("after") or {}).get("me") or {}).get("prefix")
        if a and b is not None:
            pairs.append(b / a)
    work = w.lead_in_window(True)
    hit_ideal = [r["hit"] / r["ideal"] for r in work if r.get("hit") is not None and r.get("ideal")]
    return {"cond": w.cond, "arm": w.arm, "n_pairs": len(pairs), "kept_p50": q(pairs, 0.5), "kept_mean":
            float(np.mean(pairs)) if pairs else None, "all_calls_hit_over_ideal": float(np.mean(hit_ideal)) if hit_ideal else None}


def h4_row(w: WCell) -> dict:
    per = Counter(r["label"] for r in w.lead_in_window(True))
    x = np.array(list(per.values()), dtype=float)
    jain = float(x.sum() ** 2 / (len(x) * (x ** 2).sum())) if len(x) and (x ** 2).sum() else None
    hw = hold_wait_before(w)
    tot = {k: hw["held"].get(k, 0.0) + hw["waited"].get(k, 0.0) for k in set(hw["held"]) | set(hw["waited"])}
    return {"cond": w.cond, "arm": w.arm, "sessions": len(per), "jain": jain,
            "max_hold_wait_s": max(tot.values()) if tot else 0.0}


def h6_row(w: WCell) -> dict:
    ext = [r for r in w.ext if w.inside(r.get("wall"))]
    ages = [((r.get("out") or {}).get("age_ms")) for r in ext]
    return {"cond": w.cond, "arm": w.arm, "status_req_s": len(ext) / (w.we - w.ws),
            "age_p50_ms": q(ages, 0.5), "age_p99_ms": q(ages, 0.99)}


# -- report ------------------------------------------------------------------------------------------------------
def pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def num(x, nd=1):
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:,.{nd}f}"


def analyze(conds: list[str], root: Path = ww.CELLS) -> tuple[str, dict]:
    cells = {c: WCell(c, root) for c in conds if (root / c / "window.json").exists()}
    servers = [s for w in cells.values() for s in w.cell.servers]
    ma.step_features(servers)
    model = ma.fit_step_model(servers)
    if model.get("coef"):
        ma.attribute_steps(servers, model)
    h5 = {c: h5_row(w) for c, w in cells.items()}
    out = {"step_model": model, "h5": h5, "h1": {c: h1_row(w) for c, w in cells.items()},
           "h1b": {c: h1b_row(w) for c, w in cells.items() if (w.arm or "").startswith("m_")},
           "h2": {c: h2_row(w) for c, w in cells.items()}, "h3": {c: h3_row(w) for c, w in cells.items()},
           "h4": {c: h4_row(w) for c, w in cells.items()}, "h6": {c: h6_row(w) for c, w in cells.items()}}
    rec = {}
    for n in sorted({w.n for w in cells.values()}):
        by_arm = {w.arm: h5[c] for c, w in cells.items() if w.n == n and not c.endswith("_aa")}
        if "none" in by_arm and "sgate" in by_arm:
            for arm, row in by_arm.items():
                if arm not in ("none", "sgate"):
                    rec[f"{arm}@{n}"] = recovery(row, by_arm["none"], by_arm["sgate"])
    out["recovery"] = rec
    out["decisions"] = decisions(h5)
    xc = exploratory_contrasts(h5)
    if xc:
        out["exploratory"] = xc
    aa = [c for c in cells if c.endswith("_aa")]
    if aa:
        base = aa[0][:-3]
        if base in h5:
            a, b = h5[base]["work_calls_h"], h5[aa[0]]["work_calls_h"]
            out["aa"] = {"pair": [base, aa[0]], "diff": b - a, "rel": (b - a) / a if a else None}
    lines = [f"# research/18_harness_wait tables ({', '.join(cells)})", ""]
    m = model.get("coef")
    lines.append(f"Step model (H100 TP=2, {model.get('n')} steps): " +
                 (", ".join(f"{k} {v:.5f}" for k, v in m.items()) + f"; R² {model['r2']:.3f}, median error "
                  f"{100 * model['median_ape']:.1f}%" if m else "not fitted (too few steps)"))
    lines += ["", "## H5 throughput and latency (measurement window)", "",
              "| cell | arm | N | work lead calls/h [10-min blocks 95%] | wait-only calls | turns/h | call p50 / mean (s) | hold + wait per call (s) | turn p50 / p95 KM (s) | censored | hit rate | prompt tok/call | GPU: evicted / preempted / fresh / decode / idle | preemptions |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, r in h5.items():
        g = r["gpu"]
        lines.append(f"| {c} | {r['arm']} | {r['n']} | {num(r['work_calls_h'], 0)} [{num(r['block_ci'][0], 0)}, "
                     f"{num(r['block_ci'][1], 0)}] | {r['wait_only_calls']} | {num(r['turns_h'])} | {num(r['lat_p50'])} / "
                     f"{num(r['lat_mean'])} | {num((r['hold_per_call'] or 0) + (r['wait_per_call'] or 0), 2)} | "
                     f"{num(r['turn_p50'], 0)} / {num(r['turn_p95'], 0)} | {pct(r['censored'])} | {pct(r['hit_rate'])} | "
                     f"{num(r['prompt_per_call'], 0)} | {pct(g.get('prefill_evicted'))} / {pct(g.get('prefill_preempted'))} / "
                     f"{pct(g.get('prefill_fresh'))} / {pct(g.get('decode'))} / {pct(g.get('idle'))} | {r['preemptions']} |")
    if rec:
        lines += ["", "## Recovery ratio R = (X − none) / (sgate − none), work lead calls/h", "",
                  "| arm @ N | R | 95% (block bootstrap) |", "|---|---|---|"]
        for k, v in rec.items():
            lines.append(f"| {k} | {num(v['R'], 2)} | [{num(v['lo'], 2)}, {num(v['hi'], 2)}] |")
    if out.get("aa"):
        lines.append(f"\nA/A: {out['aa']['pair'][1]} − {out['aa']['pair'][0]} = {num(out['aa']['diff'], 0)} calls/h "
                     f"({pct(out['aa']['rel'])}).")
    lines += ["", "## H1 holds and waits", "", "| cell | arm | hold checks | held | hold p50 / p95 / max (s) | why | model waits | per 100 work calls | requested p50 / p95 (s) | slept (s) | status lines |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, r in out["h1"].items():
        lines.append(f"| {c} | {r['arm']} | {r['checks']} | {pct(r['held_frac'])} | {num(r['hold_p50'])} / {num(r['hold_p95'])} / "
                     f"{num(r['hold_max'])} | {r['hold_why'] or '–'} | {r['waits']} | {num(r['wait_per_100_calls'])} | "
                     f"{num(r['wait_req_p50'])} / {num(r['wait_req_p95'])} | {num(r['wait_slept_sum'], 0)} | {r['status_lines']} |")
    if out["h1b"]:
        lines += ["", "## H1b wait loops (model arms; exploratory, added after the freeze)", "",
                  "A run is a stretch of consecutive wait-only rounds within one turn. \"Crowded\" means the status line the "
                  "model read met its harness twin's rule (share: ahead + own > 0.9 × pool; stock: a request waiting for "
                  "KV memory, or KV in use ≥ 92%). GPU time is a share of the window's wall time, split like the H5 columns; other "
                  "calls are compaction summaries and one-shot helper calls.", "",
                  "| cell | arm | wait-only calls | runs | sessions with a run ≥ 10 | run length p50 / p90 / max | wait-only calls in runs ≥ 10 | runs ended: resumed / budget / last | crowded: at run start / wait-only calls / work calls | GPU: wait-only calls (evicted recompute) / work calls / other calls |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for c, r in out["h1b"].items():
            e, g = r["ended"], r["gpu"]
            lines.append(f"| {c} | {r['arm']} | {r['wait_only']} | {r['runs']} | {r['sessions_ge10']} | {num(r['run_p50'], 0)} / "
                         f"{num(r['run_p90'], 0)} / {num(r['run_max'], 0)} | {pct(r['in_runs_ge10'])} | {e.get('resumed', 0)} / "
                         f"{e.get('budget', 0)} / {e.get('last', 0)} | {pct(r['crowded_run_start'])} / {pct(r['crowded_wait_only'])} / "
                         f"{pct(r['crowded_work'])} | {pct(g['wait_only'])} ({pct(g['wait_only_evicted'])}) / {pct(g['work'])} / "
                         f"{pct(g['other'])} |")
    lines += ["", "## H2 decisions and oscillation", "", "| cell | arm | crowded at check | bunched releases (< 2 s) | waiting queue mean | autocorrelation 10 / 30 / 60 s |",
              "|---|---|---|---|---|---|"]
    for c, r in out["h2"].items():
        ac = r["queue_ac"]
        lines.append(f"| {c} | {r['arm']} | {pct(r['crowded_at_check'])} | {pct(r['bunched_releases'])} | {num(r['queue_mean'])} | "
                     f"{' / '.join(num(ac.get(k), 2) for k in (10, 30, 60))} |")
    lines += ["", "## H3 prefix survival", "", "| cell | arm | holds / waits with a share reading | prefix kept p50 / mean | work calls: hit / ideal |",
              "|---|---|---|---|---|"]
    for c, r in out["h3"].items():
        lines.append(f"| {c} | {r['arm']} | {r['n_pairs']} | {pct(r['kept_p50'])} / {pct(r['kept_mean'])} | {pct(r['all_calls_hit_over_ideal'])} |")
    lines += ["", "## H4 fairness and H6 overhead", "", "| cell | arm | sessions | Jain index (work calls) | max hold + wait per session (s) | status requests/s | status age p50 / p99 (ms) |",
              "|---|---|---|---|---|---|---|"]
    for c in cells:
        a, b = out["h4"][c], out["h6"][c]
        lines.append(f"| {c} | {a['arm']} | {a['sessions']} | {num(a['jain'], 3)} | {num(a['max_hold_wait_s'], 0)} | "
                     f"{num(b['status_req_s'], 2)} | {num(b['age_p50_ms'], 0)} / {num(b['age_p99_ms'], 0)} |")
    text = "\n".join(lines) + "\n" + decisions_md(out["decisions"])
    if xc:
        text += ("\n**Exploratory contrasts (not pre-registered; work lead calls/h, the decisions' bootstrap)**\n\n"
                 "| contrast | D | 95% |\n|---|---|---|\n" +
                 "".join(f"| {k} | {num(v['D'], 0)} | [{num(v['ci'][0], 0)}, {num(v['ci'][1], 0)}] |\n" for k, v in xc.items()))
    return text, out


# -- smoke and pilot gates -----------------------------------------------------------------------------------------
def smoke(conds: list[str], root: Path = ww.CELLS) -> dict:
    out = {}
    for cond in conds:
        w = WCell(cond, root)
        res = {"sessions": len(w.cell.sessions), "errors": [e["exc"] for s in w.cell.servers for e in s.errors][:3]}
        joined = [r for r in w.lead() if r.get("hit") is not None]
        res["joined"] = f"{len(joined)}/{len(w.lead())}"
        res["hit_eq_cached"] = sum(1 for r in joined if r["hit"] == r.get("cached")) / len(joined) if joined else None
        evicted = sum(g[1] for s in w.cell.servers for r in s.ev for g in r["g"])
        res["evictions_observer"] = evicted
        for srv_dir in sorted((w.root / "server").glob("*")):
            kv = srv_dir / "kvevents.json"
            if kv.exists():
                k = json.loads(kv.read_text())
                res["kvevents_removed_hashes"] = k.get("removed_hashes")
                res["kvevents_gaps"] = k.get("gaps")
            final = srv_dir / "metrics_final.prom"
            if final.exists():
                import mu_scrape
                m = mu_scrape.parse_metrics(final.read_text())
                res["preemptions_vllm"] = m.get("vllm:num_preemptions_total")
        res["preemptions_observer"] = sum(len(r["pre"]) for s in w.cell.servers for r in s.req.values())
        ages = [((r.get("out") or {}).get("age_ms")) for r in w.ext]
        res["status_age_p50_p99_ms"] = (q(ages, 0.5), q(ages, 0.99))
        # the stock view against the 1 Hz /metrics scrape at the nearest second
        scrape = []
        for srv_dir in sorted((w.root / "server").glob("*")):
            scrape += [(r["wall"], r["m"]) for r in ma.read_jsonl(srv_dir / "scrape.jsonl") if r.get("m")]
        scrape.sort(key=lambda x: x[0])
        if scrape and w.ext:
            import bisect
            walls = [x[0] for x in scrape]
            d_kv, same_run = [], []
            for r in w.ext:
                o = r.get("out") or {}
                if o.get("kv_usage") is None or r.get("wall") is None:
                    continue
                i = bisect.bisect_left(walls, r["wall"])
                j = min(range(max(0, i - 1), min(len(walls), i + 1)), key=lambda k: abs(walls[k] - r["wall"]))
                m = scrape[j][1]
                d_kv.append(abs(o["kv_usage"] - m.get("vllm:kv_cache_usage_perc", 0.0)))
                same_run.append(abs(o["running"] - m.get("vllm:num_requests_running", 0.0)) <= 1)
            res["stock_vs_metrics"] = {"n": len(d_kv), "kv_abs_diff_p50_p95": (q(d_kv, 0.5), q(d_kv, 0.95)),
                                       "running_within_1": sum(same_run) / len(same_run) if same_run else None}
        res["spans"] = {k: sum(1 for _ in w.events(k)) for k in ("hold_check", "hold_start", "hold_end", "wait_detail")}
        if w.c.get("env", {}).get("MU_GATE"):
            live, peak = set(), 0
            for s in w.cell.servers:
                recs = sorted([(r["adm"][0]["t"], 1, r["arr"]["c"]) for r in s.req.values() if r["adm"]] +
                              [(r["fin"]["t"], -1, r["arr"]["c"]) for r in s.req.values() if r["fin"]])
                active = Counter()
                for _, d, chain in recs:
                    active[chain] += d
                    peak = max(peak, sum(1 for v in active.values() if v > 0))
            res["gate_peak_active_chains"] = peak
        out[cond] = res
    return out


def pilot_gates(out: dict) -> dict:
    h5 = out["h5"]
    gates = {}
    if "p16_none" in h5:
        r = h5["p16_none"]
        gates["none16_thrashes"] = (r["hit_rate"] is not None and r["hit_rate"] < 0.5
                                    and r["gpu"].get("prefill_evicted", 0) > 0.30)
    if "p08_none" in h5:
        r = h5["p08_none"]
        gates["none8_does_not"] = (r["hit_rate"] is not None and r["hit_rate"] >= 0.7
                                   and r["gpu"].get("prefill_evicted", 0) < 0.10)
    for c in ("p16_m_stock", "p16_m_share"):
        if c in out["h1"]:
            gates[f"{c}_waits"] = out["h1"][c]["waits"] > 0
    for c in ("p16_h_backoff", "p16_h_share", "p16_h_share_t75"):
        if c in out["h1"]:
            gates[f"{c}_holds"] = out["h1"][c]["checks"] > 0 and (out["h1"][c]["held_frac"] or 0) > 0
            gates[f"{c}_no_deadlock"] = (out["h4"][c]["max_hold_wait_s"] or 0) < 1800
    return gates


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", default="")
    ap.add_argument("--smoke", default="")
    ap.add_argument("--pilot", default="")
    ap.add_argument("--root", default=str(ww.CELLS))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.root)
    if args.smoke:
        print(json.dumps(smoke(args.smoke.split(","), root), indent=1, default=str))
        return
    conds = (args.pilot or args.cells).split(",")
    text, out = analyze(conds, root)
    if args.pilot:
        out["gates"] = pilot_gates(out)
        text += "\n## Pilot gates\n\n" + "\n".join(f"- {k}: {'PASS' if v else 'FAIL'}" for k, v in out["gates"].items()) + "\n"
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.with_suffix(".json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
