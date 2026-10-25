"""The F3a estimate in the simulator: 77 % of the recorded prefix breaks made append-only (the offline replay's cut
in rewritten tokens), contexts unchanged (conservative: deduplication would also shrink them by part of 15 %)."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [(s, n, p) for s in ("scripts.pkl", "scripts_dedup77.pkl") for (n, p) in ((8, "none"), (16, "gate:8"), (32, "gate:8"))]
def one(j):
    scr, n, pol = j
    v = [Sim(load(scr), n, pol, seed=sd).run()["calls_h"] for sd in (18, 19, 20)]
    return j, float(np.mean(v))
if __name__ == "__main__":
    with Pool(6) as p:
        res = dict(p.map(one, JOBS))
    for (n, pol) in ((8, "none"), (16, "gate:8"), (32, "gate:8")):
        a, b = res[("scripts.pkl", n, pol)], res[("scripts_dedup77.pkl", n, pol)]
        print(f"N={n:2d} {pol:7s} as run {a:6.0f}  with 77 % of breaks removed {b:6.0f}  ({100*(b/a-1):+.0f} %)")
