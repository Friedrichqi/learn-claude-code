#!/usr/bin/env python3
"""Exploratory (added after the main run; see README "Changes during the run"): what the harness holds did in
each 10-minute block of a twin cell's measurement window, next to the block's work lead calls per hour.

  work/h        lead calls with a reply received in the block whose reply is not only a `wait` call, per hour
                (the blocks of the primary metric's bootstrap)
  releases      hold_end events in the block, by why: cap (the hold reached S15_HOLD_MAX_S), fits (share rule) or
                clear (backoff rule), other (no why: the hold was cut short, as when a warm-up filler is stopped)
  holding       the mean number of sessions holding during the block (hold_start -> hold_end spans)
  start / mid   for a cell that also holds before a turn's first call, the releases by hold point

A second table sums the window's releases by hold point:

  held          the hold's length (held_s of hold_end)
  cached        the share of the session's context still cached when the hold started (share view: `me.prefix`
                / `me.ws` of the hold's first reading)
  kept          the session's cached prefix at release over that at the start (`me.prefix`, as in H3)

Straight from the raw captures and traces, like wt_verify (current attempts only).

    python research/18_harness_wait/wt_blocks.py --cells w16_h_share,w32_h_share,x16_h_share_start
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from wt_verify import CELLS, read_xz_jsonl

BLOCK_S = 600.0


def ts(event: dict) -> float | None:
    try:
        return datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def blocks(cell: Path) -> tuple[list[dict], dict]:
    win = json.loads((cell / "window.json").read_text())
    ws, we = win["window_start"], win["window_end"]
    nb = max(1, int(round((we - ws) / BLOCK_S)))
    width = (we - ws) / nb
    rows = [{"work_h": 0.0, "why": Counter(), "kind": Counter(), "holding": 0.0} for _ in range(nb)]
    points = {}                                         # hold point -> the window's releases there

    def block(t: float) -> int | None:
        return min(nb - 1, int((t - ws) / width)) if ws <= t <= we else None

    spans = []                                          # (start, end) of every hold
    for run in sorted((cell / "runs").iterdir()):
        if not run.is_dir():
            continue
        cap = run / f"{run.name}.requests.jsonl.xz"
        if cap.exists():
            for c in read_xz_jsonl(cap):
                resp = c.get("response")
                if c.get("purpose") != "lead" or not resp or c.get("t_recv") is None:
                    continue
                uses = [b for b in (resp.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
                b = block(c["t_recv"])
                if b is not None and not (uses and all(u.get("name") == "wait" for u in uses)):
                    rows[b]["work_h"] += 3600 / width
        for trace in run.glob("run_*.jsonl.xz"):
            if ".inputs." in trace.name or ".reads." in trace.name:
                continue
            starts = {}                                 # span ids count up per session: pair within the trace
            for e in read_xz_jsonl(trace):
                if e.get("event") == "hold_start":
                    starts[e.get("span_id")] = (ts(e), e.get("data") or {})
                elif e.get("event") == "hold_end":
                    t = ts(e)
                    t0, d0 = starts.pop(e.get("span_id"), (None, {}))
                    if t is None:
                        continue
                    if t0 is not None:
                        spans.append((t0, t))
                    b = block(t)
                    if b is not None:
                        data = e.get("data") or {}
                        why, kind = data.get("why"), d0.get("kind") or "?"
                        rows[b]["why"][why if why in ("cap", "fits", "clear") else "other"] += 1
                        rows[b]["kind"][kind] += 1
                        p = points.setdefault(kind, {"held": [], "cached": [], "kept": []})
                        p["held"].append(data.get("held_s") or 0.0)
                        me0 = (d0.get("signals") or {}).get("me") or {}
                        me1 = (data.get("signals_end") or {}).get("me") or {}
                        if me0.get("prefix") is not None and me0.get("ws"):
                            p["cached"].append(me0["prefix"] / me0["ws"])
                        if me0.get("prefix") and me1.get("prefix") is not None:
                            p["kept"].append(me1["prefix"] / me0["prefix"])
            break                                       # one trace per session (wt_analyze reads the first)
    for i, row in enumerate(rows):
        t0, t1 = ws + i * width, ws + (i + 1) * width
        row["holding"] = sum(max(0.0, min(e, t1) - max(s, t0)) for s, e in spans) / width
    return rows, points


def pq(values: list[float], p: float, scale: float = 1.0, fmt: str = "{:,.1f}") -> str:
    return fmt.format(scale * float(np.percentile(values, p))) if values else "–"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", required=True)
    ap.add_argument("--root", default=str(CELLS))
    args = ap.parse_args()
    for cond in args.cells.split(","):
        rows, points = blocks(Path(args.root) / cond)
        print(f"\n{cond}\n\n| block | work/h | releases: cap / fits or clear / other | start / mid | sessions holding |")
        print("|---|---|---|---|---|")
        for i, r in enumerate(rows):
            w, k = r["why"], r["kind"]
            print(f"| {i} | {r['work_h']:,.0f} | {w['cap']} / {w['fits'] + w['clear']} / {w['other']} | "
                  f"{k['start']} / {k['mid']} | {r['holding']:.1f} |")
        print("\n| hold point | releases | held p50 / p95 (s) | context cached at the start, p50 | "
              "prefix kept p50 / mean |")
        print("|---|---|---|---|---|")
        for kind, p in sorted(points.items(), key=lambda kv: kv[0] != "start"):
            kept_mean = f"{100 * float(np.mean(p['kept'])):.1f}%" if p["kept"] else "–"
            print(f"| {kind} | {len(p['held'])} | {pq(p['held'], 50)} / {pq(p['held'], 95)} | "
                  f"{pq(p['cached'], 50, 100, '{:.1f}%')} | {pq(p['kept'], 50, 100, '{:.1f}%')} / {kept_mean} |")


if __name__ == "__main__":
    main()
