import os
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
def one(a):
    scr, n, pol = a
    scripts = load(scr)
    v = [Sim(scripts, n, pol, seed=sd).run() for sd in (18, 19, 20)]
    f = lambda k: np.mean([x[k] for x in v])
    return f"{scr:24s} N={n:2d} {pol:26s} calls/h {f('calls_h'):6.0f} hit {f('hit'):.2f} turn mean {f('turn_mean'):5.0f} p50 {f('turn_p50'):5.0f} p95 {f('turn_p95'):5.0f}"
JOBS = [("scripts.pkl", 16, "gate:8"), ("scripts.pkl", 16, "gate:8:srpt"), ("scripts.pkl", 16, "gate:8:las"),
        ("scripts.pkl", 32, "gate:8"), ("scripts.pkl", 32, "gate:8:srpt"), ("scripts.pkl", 32, "gate:8:las"),
        ("scripts_longtools.pkl", 8, "none"), ("scripts_longtools.pkl", 16, "none"), ("scripts_longtools.pkl", 16, "gate:8"),
        ("scripts_longtools.pkl", 16, "share:0.9:mid"), ("scripts_longtools.pkl", 16, "share:0.9:start"),
        ("scripts_longtools.pkl", 16, "share:0.9:start+aftlong"), ("scripts_longtools.pkl", 32, "gate:8"),
        ("scripts_longtools.pkl", 32, "share:0.9:start"), ("scripts_longtools.pkl", 32, "share:0.9:start+aftlong")]
if __name__ == "__main__":
    with Pool(15) as p:
        for line in p.map(one, JOBS):
            print(line, flush=True)
