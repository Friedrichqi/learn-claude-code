"""Generalization: think pauses 4x longer (about 2 min at the median, closer to human users). Where is the knee?"""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [(scr, n, p) for scr in ("scripts.pkl", "scripts_think4.pkl") for n in (12, 16, 24, 32) for p in ("none", "gate:8")]
def one(j):
    scr, n, pol = j
    rs = [Sim(load(scr), n, pol, seed=s).run() for s in (18, 19, 20)]
    return j, float(np.mean([r["calls_h"] for r in rs])), float(np.mean([r["hit"] for r in rs]))
if __name__ == "__main__":
    with Pool(16) as p:
        res = {j: (c, h) for j, c, h in p.map(one, JOBS)}
    for scr in ("scripts.pkl", "scripts_think4.pkl"):
        for n in (12, 16, 24, 32):
            a, b = res[(scr, n, "none")], res[(scr, n, "gate:8")]
            print(f"{scr:20s} N={n:2d}  none {a[0]:6,.0f} (hit {a[1]:.2f})   gate:8 {b[0]:6,.0f}   none/gate {a[0]/b[0]:.2f}")
