"""The levers stacked at N=32 (simulated, three session orders): gate; + F3a (dedup77 scripts); + FP8-sized pool and
a larger K."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [("scripts.pkl", 16136, "none"), ("scripts.pkl", 16136, "gate:8"), ("scripts_dedup77.pkl", 16136, "gate:8"),
        ("scripts_dedup77.pkl", 32272, "gate:16"), ("scripts_dedup77.pkl", 32272, "gate:20")]
def one(j):
    scr, pool, pol = j
    return j, float(np.mean([Sim(load(scr), 32, pol, pool=pool, seed=s).run()["calls_h"] for s in (18, 19, 20)]))
if __name__ == "__main__":
    with Pool(5) as p:
        res = p.map(one, JOBS)
    base = res[0][1]; gate = res[1][1]
    for (scr, pool, pol), v in res:
        print(f"N=32 {scr:20s} pool {pool:6,d} {pol:8s} work calls/h {v:6,.0f}  = {v/base:4.2f}x no wait, {v/gate:4.2f}x the gate")
