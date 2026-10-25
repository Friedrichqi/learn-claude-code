#!/usr/bin/env python3
"""(a) prefix survival vs gap, (b) client-only congestion signal, (c) prefix-break audit, (e) residual waste.
Reads calls.pkl from load.py. Lead calls only; window = window.json's measurement window (arrival wall time)."""
import os
import pickle
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
D = pickle.load(open(os.path.join(HERE, "..", "..", "..", "data", "followups", "calls.pkl"), "rb"))
CELLS = list(D)
BUCKETS = [(0, 1, "<1"), (1, 3, "1-3"), (3, 10, "3-10"), (10, 30, "10-30"), (30, 100, "30-100"),
           (100, 300, "100-300"), (300, 1e18, ">300")]
THRASH = {"w16_none", "w16_none_aa", "w16_m_stock", "w16_m_share", "w32_none", "w32_m_stock", "w32_m_share"}
CALM = {"w08_none", "w08_m_stock", "w08_m_share", "w16_sgate", "w32_sgate"}


def win(cell):
    return [c for c in D[cell]["calls"] if c["in_win"]]


def hit_adm(c):
    return c["hit_adm"]                     # first admission (= usage.cache_read_input_tokens); None: never admitted


def rank(x):
    x = np.asarray(x, float)
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    for v in np.unique(x):                  # average ranks for ties
        m = x == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman(a, b):
    """(rho, two-sided p from 20,000 permutations)"""
    ra, rb = rank(a), rank(b)
    rho = float(np.corrcoef(ra, rb)[0, 1])
    rng = np.random.default_rng(18)
    perm = np.array([np.corrcoef(ra, rng.permutation(rb))[0, 1] for _ in range(20000)])
    return rho, float((np.abs(perm) >= abs(rho)).mean())


def bucket(g):
    for lo, hi, name in BUCKETS:
        if lo <= g < hi:
            return name
    return None


def section_a():
    print("\n## (a) prefix survival vs idle gap: median admission hit / ideal (lead calls admitted, ideal >= 1024), "
          "n in ()\n")
    print("| cell | " + " | ".join(b[2] + " s" for b in BUCKETS) + " | first <0.5 | arrival-based <1 / 1-3 / 3-10 |")
    print("|---|" + "---|" * (len(BUCKETS) + 2))
    for cell in CELLS:
        by, byadm = defaultdict(list), defaultdict(list)
        for c in win(cell):
            if c["gap"] is None or c["ideal"] < 1024 or hit_adm(c) is None:
                continue
            b = bucket(c["gap"])
            by[b].append(hit_adm(c) / c["ideal"])
            byadm[b].append(c["hit_now"] / c["ideal"])
        cells, half = [], "–"
        for _, _, name in BUCKETS:
            v = by.get(name, [])
            if v:
                m = float(np.median(v))
                cells.append(f"{m:.2f} ({len(v)})")
                if half == "–" and m < 0.5:
                    half = name
            else:
                cells.append("–")
        adm = " / ".join(f"{np.median(byadm[n]):.2f}" if byadm.get(n) else "–" for n in ("<1", "1-3", "3-10"))
        print(f"| {cell} | " + " | ".join(cells) + f" | {half} | {adm} |")


def section_b():
    print("\n## (b) client-only signal: mid-turn calls (gap < 3 s, ideal >= 1024), ratio = admission hit / ideal"
          " (= cache_read_input_tokens / expected prefix)\n")
    rows = []
    print("| cell | mid-turn calls | ratio <0.1 | 0.1-0.9 | >0.9 | mean | block means (9) |")
    print("|---|---|---|---|---|---|---|")
    for cell in CELLS:
        m = D[cell]["meta"]
        ws, we = m["ws"], m["we"]
        nb = max(1, int(round((we - ws) / 600)))
        width = (we - ws) / nb
        blk = [{"ratios": [], "calls": 0, "ev": 0} for _ in range(nb)]
        allr = []
        for c in win(cell):
            b = min(nb - 1, int((c["wall"] - ws) / width))
            blk[b]["calls"] += 1
            if hit_adm(c) is None:
                continue
            blk[b]["ev"] += max(0, c["ideal"] - hit_adm(c))
            if c["gap"] is not None and c["gap"] < 3 and c["ideal"] >= 1024:
                r = hit_adm(c) / c["ideal"]
                blk[b]["ratios"].append(r)
                allr.append(r)
        a = np.array(allr)
        bm = [float(np.mean(x["ratios"])) if x["ratios"] else float("nan") for x in blk]
        print(f"| {cell} | {len(a)} | {np.mean(a < 0.1):.0%} | {np.mean((a >= 0.1) & (a <= 0.9)):.0%} | "
              f"{np.mean(a > 0.9):.0%} | {a.mean():.2f} | " + " ".join(f"{x:.2f}" for x in bm) + " |")
        for x, mean in zip(blk, bm):
            rows.append((cell, mean, x["calls"] * 3600 / width, x["ev"], len(x["ratios"])))
    ok = [r for r in rows if r[4] >= 5]
    mr = np.array([r[1] for r in ok])
    ch = np.array([r[2] for r in ok])
    ev = np.array([r[3] for r in ok])
    s1 = spearman(mr, ch)
    s2 = spearman(mr, ev)
    print(f"\nPooled 10-min blocks with >= 5 mid-turn calls: {len(ok)} of {len(rows)}. Spearman(mean mid-turn ratio, "
          f"lead arrivals/h) = {s1[0]:.2f} (perm p={s1[1]:.4f}); with evicted tokens sum(ideal - adm hit) = "
          f"{s2[0]:.2f} (perm p={s2[1]:.4f})")
    # within-cell correlations for the cells with swings
    for cell in ("w32_h_share", "w16_h_share", "w16_h_backoff", "w32_h_backoff", "x16_h_share_start"):
        sub = [r for r in ok if r[0] == cell]
        if len(sub) >= 5:
            s = spearman([r[1] for r in sub], [r[2] for r in sub])
            print(f"  within {cell}: Spearman(block ratio, arrivals/h) = {s[0]:.2f} (perm p={s[1]:.3f}) over "
                  f"{len(sub)} blocks")
    for thr in (0.5, 0.8):
        th = [r for r in ok if r[0] in THRASH]
        ca = [r for r in ok if r[0] in CALM]
        tw = [r for r in ok if r[0] not in THRASH | CALM]
        print(f"  threshold {thr}: thrashing-cell blocks below it {sum(r[1] < thr for r in th)}/{len(th)}; "
              f"calm-cell blocks at/above it {sum(r[1] >= thr for r in ca)}/{len(ca)}; "
              f"twin/x blocks below it {sum(r[1] < thr for r in tw)}/{len(tw)}")
    # rule sketch: at each turn start, hold if the session's latest mid-turn call (before it) had ratio < 0.5;
    # variant: mean of its last 3 mid-turn calls < 0.5
    print("\nRule sketch (client-only, no status): at a turn start, hold if the session's latest mid-turn call had "
          "admission hit/ideal < 0.5 (A) or the mean of its last 3 < 0.5 (B). Share of in-window turn starts that "
          "would hold:\n")
    print("| cell | turn starts | A: would hold | B: would hold | no mid-turn call yet |")
    print("|---|---|---|---|---|")
    for cell in CELLS:
        hist = defaultdict(list)
        n = ha = hb = none = 0
        for c in sorted(D[cell]["calls"], key=lambda c: c["wall"]):
            if c["turn_start"] and c["in_win"]:
                h = hist[c["sess"]]
                n += 1
                if not h:
                    none += 1
                else:
                    ha += h[-1] < 0.5
                    hb += np.mean(h[-3:]) < 0.5
            if (c["gap"] is not None and c["gap"] < 3 and c["ideal"] >= 1024 and not c["turn_start"]
                    and hit_adm(c) is not None):
                hist[c["sess"]].append(hit_adm(c) / c["ideal"])
        print(f"| {cell} | {n} | {ha / max(1, n):.0%} | {hb / max(1, n):.0%} | {none} |")


def section_c():
    print("\n## (c) prefix breaks: consecutive lead calls of a chain; base = previous prompt's full blocks "
          "(16 * floor(prev_np/16)); break = lcp < base - 1024; forced = max(0, base - ideal) tokens of the previous "
          "prompt no cache could serve\n")
    print("| cell | calls with prev | breaks | forced tok / prefilled tok | forced / fresh (np-ideal) | "
          "prefill tok/call | top sessions (share of forced) |")
    print("|---|---|---|---|---|---|---|")
    out = {}
    for cell in CELLS:
        n = brk = 0
        forced = pre = fresh = 0
        by_s = defaultdict(int)
        for c in win(cell):
            if c["prev_np"] is None or hit_adm(c) is None:
                continue
            n += 1
            base = 16 * (c["prev_np"] // 16)
            f = max(0, base - c["ideal"])
            if c["lcp"] < base - 1024:
                brk += 1
            forced += f
            by_s[c["sess"].split("-")[-1].split("~")[0]] += f
            pre += max(0, c["np"] - hit_adm(c))
            fresh += max(0, c["np"] - c["ideal"])
        top = sorted(by_s.items(), key=lambda kv: -kv[1])[:3]
        tops = ", ".join(f"{s} {v / max(1, forced):.0%}" for s, v in top if v)
        out[cell] = (forced, pre, fresh)
        print(f"| {cell} | {n} | {brk / max(1, n):.1%} | {forced / max(1, pre):.1%} | {forced / max(1, fresh):.1%} | "
              f"{pre / max(1, n):,.0f} | {tops} |")
    return out


def section_e():
    print("\n## (e) residual evicted tokens (sum(ideal - admission hit)) as % of all prefilled lead tokens "
          "(sum(np - admission hit))\n")
    print("| cell | evicted % of prefilled | turn starts | mid-turn gap < 10 s | mid-turn gap >= 10 s | "
          "first call of chain / unjoined | by gap only: < 10 s / >= 10 s |")
    print("|---|---|---|---|---|---|---|")
    for cell in ("w08_none", "w16_sgate", "w32_sgate", "x16_h_share_start", "w16_h_share", "w16_none",
                 "w32_h_share"):
        pre = 0
        ev = defaultdict(int)
        for c in win(cell):
            if hit_adm(c) is None:
                continue
            pre += max(0, c["np"] - hit_adm(c))
            e = max(0, c["ideal"] - hit_adm(c))
            if c["gap"] is None or c["turn_start"] is None:
                ev["first"] += e
            elif c["turn_start"]:
                ev["ts"] += e
            elif c["gap"] < 10:
                ev["mid_short"] += e
            else:
                ev["mid_long"] += e
            if c["gap"] is not None:
                ev["g<10" if c["gap"] < 10 else "g>=10"] += e
        tot = ev["ts"] + ev["mid_short"] + ev["mid_long"] + ev["first"]
        p = lambda k: f"{ev[k] / pre:.1%}"
        print(f"| {cell} | {tot / pre:.1%} | {p('ts')} | {p('mid_short')} | {p('mid_long')} | {p('first')} | "
              f"{p('g<10')} / {p('g>=10')} |")


def section_a2():
    print("\n## (a2) where the prefix is lost: sum over admitted lead calls of ideal -> hit at arrival -> hit at "
          "admission (as % of sum ideal), and the median queue wait (arrival -> admission)\n")
    print("| cell | mid-turn (gap < 3 s): n | kept at arrival | kept at admission | queue wait p50 (s) | "
          "gap >= 10 s: n | kept at arrival | kept at admission | queue wait p50 (s) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for cell in CELLS:
        parts = []
        for sel in (lambda g: g < 3, lambda g: g >= 10):
            cs = [c for c in win(cell) if c["gap"] is not None and sel(c["gap"]) and hit_adm(c) is not None
                  and c["ideal"] > 0]
            si = sum(c["ideal"] for c in cs)
            arr = sum(min(c["hit_now"], c["ideal"]) for c in cs) / max(1, si)
            adm = sum(min(hit_adm(c), c["ideal"]) for c in cs) / max(1, si)
            qw = float(np.median([c["qwait"] for c in cs])) if cs else float("nan")
            parts += [f"{len(cs)}", f"{arr:.0%}", f"{adm:.0%}", f"{qw:.2f}"]
        print(f"| {cell} | " + " | ".join(parts) + " |")


if __name__ == "__main__":
    section_a()
    section_a2()
    section_b()
    section_c()
    section_e()
