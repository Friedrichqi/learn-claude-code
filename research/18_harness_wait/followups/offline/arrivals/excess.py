import json, os, pickle
import numpy as np
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
D = pickle.load(open(os.path.join(HERE, "..", "..", "..", "data", "followups", "calls.pkl"), "rb"))
C = pickle.load(open(os.path.join(HERE, "..", "..", "..", "data", "followups", "causes.pkl"), "rb"))
T = json.load(open("/data/fritz/learn-claude-code/research/18_harness_wait/data/tables/contingency.json"))["h5"]
print("| cell | break calls | fresh tok/call: break / non-break (median) | excess fresh from breaks, % of all fresh | % of all prefilled | GPU fresh share (H5) | est. GPU share on break re-prefill | lead calls between breaks, p50 (per session) | break offset ideal/np p50 | np at break p50 |")
print("|---|---|---|---|---|---|---|---|---|---|")
for cell in D:
    cs = [c for c in D[cell]["calls"] if c["in_win"] and c["prev_np"] is not None and c["hit_adm"] is not None]
    brk = [c for c in cs if c["lcp"] < 16 * (c["prev_np"] // 16) - 1024]
    non = [c for c in cs if c["lcp"] >= 16 * (c["prev_np"] // 16) - 1024]
    fb = np.array([c["np"] - c["ideal"] for c in brk]); fn = np.array([c["np"] - c["ideal"] for c in non])
    fresh = fb.sum() + fn.sum()
    excess = fb.sum() - len(fb) * fn.mean()
    pre = sum(max(0, c["np"] - c["hit_adm"]) for c in cs)
    g = T[cell]["gpu"]["prefill_fresh"]
    # spacing between breaks within a session (all calls, not only window)
    gaps = []
    bys = defaultdict(list)
    for c in sorted(D[cell]["calls"], key=lambda c: c["r"]):
        if c["prev_np"] is not None:
            bys[c["sess"]].append(c["lcp"] < 16 * (c["prev_np"] // 16) - 1024)
    for s, flags in bys.items():
        idx = [i for i, f in enumerate(flags) if f]
        gaps += list(np.diff(idx))
    off = np.median([c["ideal"] / c["np"] for c in brk]); npb = np.median([c["np"] for c in brk])
    print(f"| {cell} | {len(brk)} ({len(brk)/len(cs):.0%}) | {np.median(fb):,.0f} / {np.median(fn):,.0f} | {excess/fresh:.0%} | {excess/pre:.0%} | {g:.1%} | {g*excess/fresh:.1%} | {np.median(gaps) if gaps else float('nan'):.0f} | {off:.2f} | {npb:,.0f} |")
