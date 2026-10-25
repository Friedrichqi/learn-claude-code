"""F7 screen: client-side holds at turn starts only, on a probe signal (queued requests) or the stock backoff
signal (queued requests or KV in use), against the gate. Upper bounds (client-side rule)."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [(n, p) for n in (16, 32) for p in ("none", "gate:8", "probe:start", "backoff:start", "probe:start+mid")]
def one(j):
    n, pol = j
    rs = [Sim(load(), n, pol, seed=s).run() for s in (18, 19, 20)]
    return j, float(np.mean([r["calls_h"] for r in rs])), float(np.mean([r["hit"] for r in rs]))
if __name__ == "__main__":
    with Pool(10) as p:
        for (n, pol), c, h in p.map(one, JOBS):
            print(f"N={n:2d} {pol:16s} work calls/h {c:6,.0f}  hit {h:.2f}")
