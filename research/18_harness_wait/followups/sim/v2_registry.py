"""Share rule v2 (turn starts only, tau 0.75, cap 3,600 s) read through a registry: entries read 1 s old on average
and mid-turn entries expiring 10 s after a session's last call, against fresh reads and the gate."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
J = [(n, p) for n in (16, 32) for p in ("gate:8", "share:0.75:start:3600", "share:0.75:start:3600:1.0:10")]
def one(j):
    n, pol = j
    return j, float(np.mean([Sim(load(), n, pol, seed=s).run()["calls_h"] for s in (18, 19, 20)]))
if __name__ == "__main__":
    with Pool(6) as p:
        for (n, pol), c in p.map(one, J):
            print(f"N={n:2d} {pol:30s} work calls/h {c:6,.0f}")
