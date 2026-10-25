"""C1 test: the share rule on a stale status (snapshot every 0.2 s, read with an age of AGE*U(0.5,1.5) s) and the
server's 10-s mid-turn expiry, on the fixed simulator. Measured: w16_h_share 4,837, w32_h_share 3,934."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [(n, p) for n in (16, 32) for p in ("share:0.9:mid", "share:0.9:mid:600:0.5:10", "share:0.9:mid:600:1.0:10")]
def one(j):
    n, pol = j
    rs = [Sim(load(), n, pol, seed=s).run() for s in (18, 19, 20)]
    return j, float(np.mean([r["calls_h"] for r in rs])), float(np.mean([r["hit"] for r in rs]))
if __name__ == "__main__":
    with Pool(6) as p:
        for (n, pol), c, h in p.map(one, JOBS):
            print(f"N={n:2d} {pol:26s} work calls/h {c:6,.0f}  hit {h:.2f}")
