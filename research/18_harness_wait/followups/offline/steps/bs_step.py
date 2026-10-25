#!/usr/bin/env python3
"""Topic 18 brainstorm, offline (CPU only): d1 real idle, d2 batch composition, d3 is K=8 a ceiling,
g1 holds while the server had room, g2 w32_h_share cap releases. Reads the raw sched logs and traces."""
import bisect
import glob
import json
import lzma
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from multiprocessing import Pool

import numpy as np

ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
OUT = os.path.dirname(os.path.abspath(__file__))
COEF = json.load(open("/data/fritz/learn-claude-code/research/18_harness_wait/data/tables/contingency.json"))["step_model"]["coef"]
POOL_TOK = 258176
CELLS = ["w08_none", "w08_m_stock", "w08_m_share", "w16_none", "w16_none_aa", "w16_m_stock", "w16_m_share",
         "w16_h_backoff", "w16_h_share", "x16_h_share_start", "w16_sgate", "w32_none", "w32_m_stock", "w32_m_share",
         "w32_h_backoff", "w32_h_share", "w32_sgate"]
TWINS = ["w16_h_share", "w32_h_share", "w16_h_backoff", "w32_h_backoff", "x16_h_share_start"]
GATES = ["w16_sgate", "w32_sgate"]


def ts(e):
    try:
        return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def sess_of(ext):
    parts = ext.split("~")
    return f"{parts[0]}~{parts[1]}" if len(parts) == 5 else ext, (parts[3] if len(parts) == 5 else "un")


def union_len(iv, lo, hi):
    """total length of the union of intervals clipped to [lo, hi]"""
    iv = sorted((max(a, lo), min(b, hi)) for a, b in iv if b > lo and a < hi)
    tot, cur_a, cur_b = 0.0, None, None
    for a, b in iv:
        if b <= a:
            continue
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                tot += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        tot += cur_b - cur_a
    return tot


def hold_spans(cell, ws, we):
    """(start, end, start_data, end_data) per hold, paired within each trace as wt_blocks.py does."""
    out = []
    for run in sorted(glob.glob(f"{ROOT}/{cell}/runs/*/")):
        for trace in sorted(glob.glob(f"{run}run_*.jsonl.xz")):
            if ".inputs." in trace or ".reads." in trace:
                continue
            starts = {}
            with lzma.open(trace, "rt") as fh:
                for line in fh:
                    if '"hold_' not in line:
                        continue
                    e = json.loads(line)
                    if e.get("event") == "hold_start":
                        starts[e.get("span_id")] = (ts(e), e.get("data") or {})
                    elif e.get("event") == "hold_end":
                        t1 = ts(e)
                        t0, d0 = starts.pop(e.get("span_id"), (None, {}))
                        if t0 is not None and t1 is not None:
                            out.append((t0, t1, d0, e.get("data") or {}, os.path.basename(run.rstrip("/"))))
            break
    return out


def analyze(cell):
    win = json.load(open(f"{ROOT}/{cell}/window.json"))
    srv = sorted(glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz"))
    assert len(srv) == 1, (cell, srv)
    steps, clk, wt, idle, gate = [], [], [], [], []
    arr, adm, fin = {}, {}, {}
    with lzma.open(srv[0], "rt") as f:
        for line in f:
            r = json.loads(line)
            k = r.get("k")
            if k == "step":
                steps.append(r)
            elif k == "clk":
                clk.append((r["t"], r["wall"]))
            elif k == "wt":
                wt.append((r["t"], r["waiting"], r["pool"]["usage"], r["running"], r.get("deferred", 0)))
            elif k == "idle":
                idle.append((r["t0"], r["t1"], r["count"], r["wq"], r.get("hwhy")))
            elif k == "gate":
                gate.append((r["t"], r["s"], r["on"]))
            elif k == "arr":
                arr[r["r"]] = (r["t"], r["id"], r["np"])
            elif k == "adm":
                if r["r"] not in adm:
                    adm[r["r"]] = r["t"]
            elif k == "fin":
                fin[r["r"]] = (r["t"], r.get("no") or 0)
    off = float(np.median([w - t for t, w in clk]))
    lo, hi = win["window_start"] - off, win["window_end"] - off      # window in monotonic seconds
    W = hi - lo
    steps.sort(key=lambda s: s["n"])

    # ---- d1/d2/d3: step intervals as mu_analyze builds them (g0 = max(prev u0, launch), g1 = u0)
    prev_u0 = None
    covered = 0.0
    gaps = []
    dec_count = defaultdict(int)
    tw = Counter()             # time-weighted sums
    comp = Counter()           # step-model components (seconds), steps inside the window
    nsteps = Counter()
    meas_tot = pred_tot = 0.0
    for s in steps:
        u0 = s.get("u0")
        launch = s.get("l") or s.get("s1")
        g0 = max(prev_u0, launch) if prev_u0 is not None else launch
        if prev_u0 is not None and launch > prev_u0:
            gaps.append((prev_u0, launch))
        g1 = u0
        if u0 is not None:
            prev_u0 = u0
        pf_tok = sum(c[2] for c in s["pfl"])
        pf_attn = sum(c[2] * (c[1] + c[2] / 2) for c in s["pfl"]) / 1e6
        ctx = 0
        for ri in s["dcr"]:
            a = arr.get(ri)
            dec_count[ri] += 1
            ctx += (a[2] if a else 0) + dec_count[ri]
        if g1 is None or g1 <= g0:
            continue
        a_, b_ = max(g0, lo), min(g1, hi)
        if b_ <= a_:
            continue
        frac = (b_ - a_) / (g1 - g0)
        dur = b_ - a_
        covered += dur
        ndec = len(s["dcr"])
        kind = "dec" if pf_tok == 0 else ("mix" if ndec else "pf")
        nsteps[kind] += frac
        tw["run"] += dur * s["run"]
        tw["dc"] += dur * ndec
        tw["t_" + kind] += dur
        tw["dc_dec"] += dur * ndec if kind == "dec" else 0.0
        fixed = COEF["fixed"] + (COEF["mixed"] if pf_tok else 0.0)
        p = COEF["pf_tok"] * pf_tok / 1e3 + COEF["pf_attn"] * pf_attn
        d = COEF["dec_seq"] * ndec + COEF["dec_ctx"] * ctx / 1e6
        comp["fixed_" + kind] += frac * fixed
        comp["prefill"] += frac * p
        comp["decode"] += frac * d
        comp["dec_tokens"] += frac * ndec
        comp["pf_tokens"] += frac * pf_tok
        meas_tot += dur
        pred_tot += frac * (fixed + p + d)
    gap_in = union_len(gaps, lo, hi)
    idle_iv = [(a, b) for a, b, *_ in idle]
    idle_in = union_len(idle_iv, lo, hi)
    gaps_long = [(a, b) for a, b in gaps if b - a > 0.05 and b > lo and a < hi]
    # gap time not overlapped by empty-step ("idle") records = engine blocked, nothing queued or running
    gap_and_idle = 0.0
    if gaps:
        merged = sorted((max(a, lo), min(b, hi)) for a, b in gaps if b > lo and a < hi)
        ii = sorted(idle_iv)
        starts_ = [a for a, b in ii]
        for a, b in merged:
            j = bisect.bisect_left(starts_, a) - 1
            j = max(j, 0)
            while j < len(ii) and ii[j][0] < b:
                x, y = max(a, ii[j][0]), min(b, ii[j][1])
                if y > x:
                    gap_and_idle += y - x
                j += 1
    res = {"cell": cell, "W": W, "covered": covered / W, "gap": gap_in / W, "idle_rec": idle_in / W,
           "gap_in_idle_rec": gap_and_idle / W, "n_gaps_gt50ms": len(gaps_long),
           "max_gap": max((min(b, hi) - max(a, lo) for a, b in gaps_long), default=0.0),
           "pred_over_meas": pred_tot / meas_tot if meas_tot else None,
           "run_tw": tw["run"] / covered if covered else None, "dc_tw": tw["dc"] / covered if covered else None,
           "dc_in_dec_steps": tw["dc_dec"] / tw["t_dec"] if tw["t_dec"] else None,
           "t_dec": tw["t_dec"] / W, "t_mix": tw["t_mix"] / W, "t_pf": tw["t_pf"] / W,
           "n_dec": nsteps["dec"], "n_mix": nsteps["mix"], "n_pf": nsteps["pf"], "comp": dict(comp)}
    # wt state (1-s records) for g1
    wt.sort()
    res["wt_waiting0"] = float(np.mean([w == 0 for t, w, u, r, dfr in wt if lo <= t <= hi])) if wt else None
    res["wt_room"] = float(np.mean([(w == 0 and u < 0.85) for t, w, u, r, dfr in wt if lo <= t <= hi])) if wt else None
    res["wt_run0"] = float(np.mean([r == 0 for t, w, u, r, dfr in wt if lo <= t <= hi])) if wt else None
    res["wt_usage_mean"] = float(np.mean([u for t, w, u, r, dfr in wt if lo <= t <= hi])) if wt else None

    # ---- d3 pool constraint (gate cells): active sessions' summed lead context, and the gate queue
    if cell in GATES:
        # lead ws over time per session: np at arrival, np + no at finish
        ev = defaultdict(list)
        lead_reqs = []
        for ri, (t, ext, np_) in arr.items():
            sid, purpose = sess_of(ext)
            if purpose != "ld":
                continue
            ev[sid].append((t, np_))
            if ri in fin:
                ev[sid].append((fin[ri][0], np_ + fin[ri][1]))
            lead_reqs.append((t, adm.get(ri), sid, np_))
        for sid in ev:
            ev[sid].sort()
        evt = {sid: [x[0] for x in v] for sid, v in ev.items()}

        def ws_at(sid, t):
            v = ev.get(sid)
            if not v:
                return 0
            j = bisect.bisect_right(evt[sid], t) - 1
            return v[j][1] if j >= 0 else 0

        # active intervals from gate records
        gate.sort()
        on_since, active_iv = {}, defaultdict(list)
        for t, s, on in gate:
            if on:
                on_since[s] = t
            elif s in on_since:
                active_iv[s].append((on_since.pop(s), t))
        for s, t0 in on_since.items():
            active_iv[s].append((t0, hi + 1))
        grid = np.arange(lo, hi, 1.0)
        S = {k: [] for k in range(8, 13)}
        nact, marg, qlen = [], [], []
        lead_reqs.sort()
        for t in grid:
            act = [s for s, iv in active_iv.items() if any(a <= t < b for a, b in iv)]
            vals = [ws_at(s, t) for s in act]
            nact.append(len(act))
            marg.extend(vals)
            base = sum(vals)
            aset = set(act)
            queued, seen = [], set()
            for ta, tad, sid, np_ in lead_reqs:            # FCFS by arrival
                if ta > t:
                    break
                if sid in aset or sid in seen:
                    continue
                if tad is not None and tad > t:              # never admitted = aborted (stopped filler)
                    queued.append(np_)
                    seen.add(sid)
            qlen.append(len(queued))
            for k in range(8, 13):
                extra = max(0, k - len(act))
                S[k].append((base + sum(queued[:extra])) / POOL_TOK)
        rng = np.random.default_rng(18)
        marg = np.array([m for m in marg if m > 0])
        res["d3"] = {"n_active_mean": float(np.mean(nact)), "n_active_p50": float(np.median(nact)),
                     "gate_queue_mean": float(np.mean(qlen)), "gate_queue_p10": float(np.percentile(qlen, 10)),
                     "ws_active_p50": float(np.median(marg)), "ws_active_mean": float(np.mean(marg)),
                     "ws_active_p90": float(np.percentile(marg, 90)),
                     "S": {k: {"p50": float(np.median(v)), "p90": float(np.percentile(v, 90)),
                               "p99": float(np.percentile(v, 99)), "P_gt_0.9": float(np.mean(np.array(v) > 0.9)),
                               "P_gt_1.0": float(np.mean(np.array(v) > 1.0))} for k, v in S.items()},
                     "iid": {k: float(np.mean(rng.choice(marg, size=(100000, k)).sum(1) > 0.9 * POOL_TOK))
                             for k in range(8, 13)}}

    # ---- g1 / g2 (twin cells)
    if cell in TWINS:
        spans = hold_spans(cell, win["window_start"], win["window_end"])
        wt_t = np.array([t + off for t, *_ in wt])          # wall
        wt_room = np.array([(w == 0 and u < 0.85) for t, w, u, r, dfr in wt])
        wt_w0 = np.array([w == 0 for t, w, u, r, dfr in wt])
        wt_r0 = np.array([r == 0 for t, w, u, r, dfr in wt])
        wt_u = np.array([u for t, w, u, r, dfr in wt])
        gaps_wall = sorted((a + off, b + off) for a, b in gaps if b - a > 0.0)
        acc = Counter()
        by_kind = defaultdict(Counter)
        ws_, we_ = win["window_start"], win["window_end"]
        for t0, t1, d0, d1_, run in spans:
            a, b = max(t0, ws_), min(t1, we_)
            if b <= a:
                continue
            kind = d0.get("kind") or "?"
            # integrate the 1-s wt state over [a, b] by 0.25-s sampling
            ts_ = np.arange(a, b, 0.25)
            idx = np.clip(np.searchsorted(wt_t, ts_, side="right") - 1, 0, len(wt_t) - 1)
            dt = (b - a) / len(ts_) if len(ts_) else 0.0
            for key, arrv in (("room", wt_room), ("waiting0", wt_w0), ("run0", wt_r0)):
                acc[key] += float(arrv[idx].sum()) * dt
                by_kind[kind][key] += float(arrv[idx].sum()) * dt
            acc["usage_x_s"] += float(wt_u[idx].sum()) * dt
            acc["held"] += b - a
            by_kind[kind]["held"] += b - a
            # GPU idle (step gaps) overlap
            j = bisect.bisect_left(gaps_wall, (a - 3600, 0))
            g_ov = 0.0
            for x, y in gaps_wall[max(0, bisect.bisect_left(gaps_wall, (a - 30.0, 0)) - 1):]:
                if x >= b:
                    break
                ov = min(y, b) - max(x, a)
                if ov > 0:
                    g_ov += ov
            acc["gpu_gap"] += g_ov
        res["g1"] = {"held_s": acc["held"], "room": acc["room"] / acc["held"] if acc["held"] else None,
                     "waiting0": acc["waiting0"] / acc["held"] if acc["held"] else None,
                     "run0": acc["run0"] / acc["held"] if acc["held"] else None,
                     "gpu_gap": acc["gpu_gap"] / acc["held"] if acc["held"] else None,
                     "usage_mean_while_held": acc["usage_x_s"] / acc["held"] if acc["held"] else None,
                     "mean_holding": acc["held"] / (we_ - ws_),
                     "by_kind": {k: {"held_s": v["held"], "room": v["room"] / v["held"] if v["held"] else None}
                                 for k, v in by_kind.items()}}
        if cell == "w32_h_share":
            rows = []
            for t0, t1, d0, d1_, run in spans:
                if not (ws_ <= t1 <= we_):
                    continue
                me0 = ((d0.get("signals") or {}).get("me") or {})
                sg0 = d0.get("signals") or {}
                me1 = ((d1_.get("signals_end") or {}).get("me") or {})
                holding_now = sum(1 for a, b, *_ in spans if a <= t1 < b) + 1
                rows.append({"why": d1_.get("why") or "other", "held": d1_.get("held_s"), "ws": me0.get("ws"),
                             "ahead_ws": me0.get("ahead_ws"), "ahead_n": me0.get("ahead_n"), "rank": me0.get("rank"),
                             "holders0": sg0.get("holders"), "mid0": sg0.get("mid_sessions"),
                             "rank1": me1.get("rank"), "ahead1": me1.get("ahead_ws"), "holding_at_end": holding_now,
                             "kind": d0.get("kind")})
            res["g2_rows"] = rows
    return res


def main():
    with Pool(17) as p:
        results = p.map(analyze, CELLS)
    json.dump(results, open(f"{OUT}/bs_step.json", "w"), indent=1, default=float)
    print("saved", f"{OUT}/bs_step.json")


if __name__ == "__main__":
    main()
