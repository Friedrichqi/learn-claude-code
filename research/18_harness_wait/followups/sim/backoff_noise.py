"""C1 test: backoff on a stock-/metrics-like signal (sampled every 0.5 s, KV in use with Gaussian noise of sd SD),
against the exact signal. Measured: w16_h_backoff 4,157 work calls/h, w32_h_backoff 3,173."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [(n, p) for n in (16, 32) for p in ("backoff:mid", "backoff:mid:0", "backoff:mid:0.1", "backoff:mid:0.2")]
def one(j):
    n, pol = j
    rs = [Sim(load(), n, pol, seed=s).run() for s in (18, 19, 20)]
    return j, float(np.mean([r["calls_h"] for r in rs])), float(np.mean([r["hit"] for r in rs]))
if __name__ == "__main__":
    with Pool(8) as p:
        for (n, pol), c, h in p.map(one, JOBS):
            print(f"N={n:2d} {pol:18s} work calls/h {c:6,.0f}  hit {h:.2f}")
