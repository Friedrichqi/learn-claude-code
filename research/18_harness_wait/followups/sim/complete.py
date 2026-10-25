"""Calibration check: the validation arms on session scripts from completed sessions only (no truncated fillers or
window-cut sessions), against the default scripts. Also the share of evictions that hit live chains (measured:
w08_none 16.8 %, w16_none 92.1 %, w16_sgate 21.0 %, w16_h_backoff 72.7 %, w16_h_share 59.8 %, x16 33.8 %,
w32_h_share 74.3 %, w32_sgate 23.6 %)."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
MEAS = {(8, "none"): 6713, (16, "none"): 1717, (32, "none"): 1807, (16, "gate:8"): 6539, (32, "gate:8"): 5837,
        (16, "backoff:mid"): 4157, (32, "backoff:mid"): 3173, (16, "share:0.9:mid"): 4837, (32, "share:0.9:mid"): 3934,
        (16, "share:0.9:start+mid"): 6096}
def one(a):
    scr, n, pol = a
    rs = [Sim(load(scr), n, pol, seed=sd).run() for sd in (18, 19, 20)]
    f = lambda k: float(np.mean([r[k] for r in rs]))
    return f"{scr:22s} N={n:2d} {pol:22s} calls/h {f('calls_h'):6.0f} (measured {MEAS[(n, pol)]:5d})  hit {f('hit'):.2f}  live evictions {100*f('ev_live_share'):4.0f} %"
if __name__ == "__main__":
    jobs = [(s, n, p) for s in ("scripts_complete.pkl",) for (n, p) in MEAS]
    with Pool(10) as pool:
        for line in pool.map(one, jobs):
            print(line, flush=True)
