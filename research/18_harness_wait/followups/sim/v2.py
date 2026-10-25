"""Share rule v2 in the simulator: turn starts (and, with long tools, after long tools) only, tau 0.75-0.8, no
practical cap; three session orders. Upper bounds, like every client-side number of the simulator."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [("scripts.pkl", 16, "gate:8"), ("scripts.pkl", 16, "share:0.75:start:3600"), ("scripts.pkl", 16, "share:0.8:start:3600"),
        ("scripts.pkl", 32, "gate:8"), ("scripts.pkl", 32, "share:0.75:start:3600"), ("scripts.pkl", 32, "share:0.8:start:3600"),
        ("scripts_longtools.pkl", 32, "gate:8"), ("scripts_longtools.pkl", 32, "share:0.75:start+aftlong:3600"),
        ("scripts_batchc8.pkl", 32, "gate:8"), ("scripts_batchc8.pkl", 32, "share:0.75:start:3600")]
def one(j):
    scr, n, pol = j
    rs = [Sim(load(scr), n, pol, seed=s).run() for s in (18, 19, 20)]
    f = lambda k: float(np.mean([r[k] for r in rs]))
    return f"{scr:22s} N={n:2d} {pol:30s} calls/h {f('calls_h'):6.0f} ({' / '.join(f'{r[chr(99)+chr(97)+chr(108)+chr(108)+chr(115)+chr(95)+chr(104)]:.0f}' for r in rs)})  hit {f('hit'):.2f}  turn p50/p95 {f('turn_p50'):.0f}/{f('turn_p95'):.0f}"
if __name__ == "__main__":
    with Pool(10) as p:
        for line in p.map(one, JOBS):
            print(line, flush=True)
