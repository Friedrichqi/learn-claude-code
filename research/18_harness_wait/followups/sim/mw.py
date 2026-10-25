from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
scripts = load()
JOBS = [(16, "none"), (16, "mwait:0.042:5:0.9:loop"), (16, "mwait:0.042:5:0.9:block"), (16, "mwait:0.30:5:0:block"),
        (16, "share:0.9:mid"), (32, "none"), (32, "mwait:0.05:10:0.9:loop"), (32, "mwait:0.05:10:0.9:block"),
        (8, "none"), (8, "mwait:0.03:1:0.97:loop"), (8, "mwait:0.03:1:0.97:block")]
def one(a):
    n, pol = a
    v = [Sim(scripts, n, pol, seed=sd).run() for sd in (18, 19)]
    c = np.mean([x["calls_h"] for x in v]); w = np.mean([x.get("wait_calls_per_100", 0) for x in v]); h = np.mean([x["hit"] for x in v])
    return f"N={n:2d} {pol:26s} work calls/h {c:6.0f}  wait-only per 100 work {w:5.1f}  hit {h:.2f}"
if __name__ == "__main__":
    with Pool(11) as p:
        for line in p.map(one, JOBS):
            print(line, flush=True)
