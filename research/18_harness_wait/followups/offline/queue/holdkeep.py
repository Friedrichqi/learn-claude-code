"""Prefix kept over a harness hold (me.prefix at release / at start, as in H3) by hold length and hold point."""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "/data/fritz/learn-claude-code/research/18_harness_wait")
from wt_verify import CELLS, read_xz_jsonl
from wt_blocks import ts
EDGES = [0, 2, 4, 8, 16, 32, 64, 128, 1e9]
for cond in sys.argv[1:]:
    cell = Path(CELLS) / cond
    win = json.loads((cell / "window.json").read_text())
    rows = {}
    for run in sorted((cell / "runs").iterdir()):
        if not run.is_dir():
            continue
        for trace in run.glob("run_*.jsonl.xz"):
            if ".inputs." in trace.name or ".reads." in trace.name:
                continue
            starts = {}
            for e in read_xz_jsonl(trace):
                if e.get("event") == "hold_start":
                    starts[e.get("span_id")] = e.get("data") or {}
                elif e.get("event") == "hold_end":
                    d0 = starts.pop(e.get("span_id"), None); t = ts(e)
                    if d0 is None or t is None or not (win["window_start"] <= t <= win["window_end"]):
                        continue
                    d1 = e.get("data") or {}
                    me0 = (d0.get("signals") or {}).get("me") or {}; me1 = (d1.get("signals_end") or {}).get("me") or {}
                    if not me0.get("prefix") or me1.get("prefix") is None:
                        continue
                    h = d1.get("held_s") or 0.0
                    k = next(i for i in range(len(EDGES) - 1) if EDGES[i] <= h < EDGES[i + 1])
                    rows.setdefault((d0.get("kind") or "?", k), []).append(min(1.0, me1["prefix"] / me0["prefix"]))
            break
    print(f"\n{cond}: hold point, held (s): n, prefix kept p50 / mean (capped at 1)")
    for (kind, k), v in sorted(rows.items()):
        print(f"  {kind:5s} {EDGES[k]:>4g}-{EDGES[k+1]:<5g} n={len(v):4d}  {np.median(v):.2f} / {np.mean(v):.2f}")
