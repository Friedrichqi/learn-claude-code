"""Scaling: the gate and share rule v2 (turn starts only, tau 0.75, cap 3,600 s) at N=48 and N=64."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
J = [(n, p) for n in (48, 64) for p in ("none", "gate:8", "share:0.75:start:3600")]
def one(j):
    n, pol = j
    rs = [Sim(load(), n, pol, seed=s).run() for s in (18, 19, 20)]
    return j, float(np.mean([r["calls_h"] for r in rs])), float(np.mean([r["turn_p50"] for r in rs])), float(np.mean([r["turn_p95"] for r in rs]))
if __name__ == "__main__":
    with Pool(6) as p:
        for (n, pol), c, t50, t95 in p.map(one, J):
            print(f"N={n:2d} {pol:22s} work calls/h {c:6,.0f}  turn p50/p95 {t50:5,.0f} / {t95:5,.0f} s")
