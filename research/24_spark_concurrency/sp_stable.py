#!/usr/bin/env python3
"""Topic 24, part 2: the stop rule "stop when the numbers are stable" (PLAN2.md), on a live or a finished cell.

    <exp01 venv python> research/24_spark_concurrency/sp_stable.py --cell fp8_n08 [--json]
    <exp01 venv python> research/24_spark_concurrency/sp_stable.py --cell n08 --replay

The rule. The steady period starts at the first second when half of the active sessions have been trimmed (as in
sp_analyze.py). A cell is stable when

  - the steady period is at least MIN_STEADY_S long, and
  - at least half of the N first sessions have ended, so fresh sessions have joined the trimmed ones (before that the
    users move through the phases together: in the BF16 8-user cell output fell to 18.7 tokens/s around minute 105,
    when nearly all sessions were trimmed, and came back to 22 once refills had started), and
  - the running mean over the steady period of each of these moved by at most TOL between 30 minutes ago and now:
    output tokens/s, prompt tokens computed/s, KV pool pinned by running requests, engine step time.

Only the trial files and the server's step records are read (no samplers, no hardware counters), so the check costs a
few seconds. GPU traffic cannot be part of it: the counter files close every 30 minutes. It is checked after the cell.

--replay runs the rule on a finished cell at every 10 minutes from minute 60, as sp_cell.py does live, and prints where
it would have stopped and how far the steady-period numbers at that point are from those of the whole window.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_analyze as A  # noqa: E402

MIN_STEADY_S = 90 * 60
LOOKBACK_S = 30 * 60
TOL = 0.03          # PLAN2.md started at 5%; the replay on n08 then stopped at minute 150 with prompt tokens/s 19% off
                    # the whole window. At 3% it stops at minute 220 with every number within 3%.
METRICS = ("output_tok_s", "computed_prompt_tok_s", "kv_pinned", "step_ms")


def series(c: A.Cell):
    """Per-second series over the cell's window and the steady mask."""
    g = A.grid(c)
    st = A.step_table(c)
    if not len(g) or not len(st):
        return None
    f = c.sess[c.sess["first"]]
    ended = np.sort(((f.end_mono[f.status.notna()] - c.t0) / 1e9).values) if len(f) else np.array([])
    return {"steady": g.steady.values.astype(bool), "n": c.n, "first_ended_s": ended,
            "output_tok_s": A.per_second(st.t, st.ng, g),
            "computed_prompt_tok_s": A.per_second(st.t, st.ct, g),
            "kv_pinned": A.per_second(st.t, st.kv, g, "mean"),
            "step_ms": A.per_second(st.t, st.dur * 1000, g, "mean")}


def means(s: dict, upto: int) -> dict:
    m = s["steady"].copy()
    m[upto:] = False
    return {k: (float(np.nanmean(s[k][m])) if m.any() and np.isfinite(s[k][m]).any() else float("nan"))
            for k in METRICS}


def check(s: dict | None, now: int | None = None, tol: float = TOL) -> dict:
    if s is None:
        return {"stable": False, "steady_s": 0, "why": "no step records yet"}
    now = len(s["steady"]) if now is None else min(now, len(s["steady"]))
    steady_s = int(s["steady"][:now].sum())
    out = {"t_s": now, "steady_s": steady_s, "stable": False}
    ended = int((s["first_ended_s"] <= now).sum())
    out["first_ended"] = ended
    if steady_s < MIN_STEADY_S:
        out["why"] = f"steady period {steady_s / 60:.0f} min < {MIN_STEADY_S / 60:.0f} min"
        return out
    if ended < (s["n"] + 1) // 2:
        out["why"] = f"{ended} of {s['n']} first sessions ended, need {(s['n'] + 1) // 2}"
        return out
    a, b = means(s, now), means(s, now - LOOKBACK_S)
    drift = {k: (abs(a[k] - b[k]) / abs(a[k]) if a[k] and a[k] == a[k] and b[k] == b[k] else float("nan"))
             for k in METRICS}
    out.update(means=a, drift=drift, stable=bool(all(v == v and v <= tol for v in drift.values())))
    if not out["stable"]:
        out["why"] = "moved by more than {:.0%} in 30 min: {}".format(
            tol, ", ".join(k for k, v in drift.items() if not (v == v and v <= tol)))
    return out


def replay(name: str, tol: float) -> dict:
    c = A.Cell(name, light=True)
    s = series(c)
    full = means(s, len(s["steady"]))
    stop = None
    rows = []
    for t in range(3600, len(s["steady"]) + 1, 600):
        r = check(s, t, tol)
        rows.append(r)
        if r["stable"] and stop is None:
            stop = r
    out = {"cell": name, "window_s": len(s["steady"]), "steady_s": int(s["steady"].sum()), "tol": tol,
           "full_window_means": full, "stop": stop, "checks": rows}
    if stop:
        out["stop_vs_full"] = {k: stop["means"][k] / full[k] - 1 for k in METRICS}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cell", required=True)
    ap.add_argument("--json", action="store_true", help="one JSON line (for sp_cell.py)")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--tol", type=float, default=TOL)
    a = ap.parse_args()
    if a.replay:
        r = replay(a.cell, a.tol)
        for x in r["checks"]:
            d = x.get("drift") or {}
            print(f"  min {x['t_s'] / 60:4.0f}: steady {x['steady_s'] / 60:4.0f} min, drift "
                  + ", ".join(f"{k} {v:.3f}" for k, v in d.items()) + f"  stable {x['stable']}")
        if r["stop"]:
            print(f"{a.cell}: the rule stops at minute {r['stop']['t_s'] / 60:.0f} of {r['window_s'] / 60:.0f} "
                  f"(steady {r['stop']['steady_s'] / 60:.0f} min)")
            for k in METRICS:
                print(f"  {k}: at the stop {r['stop']['means'][k]:.4g}, whole window {r['full_window_means'][k]:.4g} "
                      f"({r['stop_vs_full'][k]:+.1%})")
        else:
            print(f"{a.cell}: the rule never stops within the window (tol {a.tol})")
        (A.C.TABLES / f"stable_replay_{a.cell}.json").write_text(json.dumps(r, indent=1))
        return 0
    r = check(series(A.Cell(a.cell, light=True)), tol=a.tol)
    print(json.dumps(r) if a.json else json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
