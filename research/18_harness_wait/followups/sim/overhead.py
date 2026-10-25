"""C1 probe: 0.3 s of client and API overhead per call (added to the client delay), against the measured cells
(w08_none 6,713; w16_sgate 6,539; w32_sgate 5,837; w16_h_share 4,837; w16_h_backoff 4,157)."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
J = [(s, n, p) for s in ("scripts.pkl", "scripts_oh03.pkl")
     for (n, p) in ((8, "none"), (16, "gate:8"), (32, "gate:8"), (16, "share:0.9:mid"), (16, "backoff:mid:0.2"))]
def one(j):
    scr, n, pol = j
    return j, float(np.mean([Sim(load(scr), n, pol, seed=s).run()["calls_h"] for s in (18, 19, 20)]))
if __name__ == "__main__":
    with Pool(10) as p:
        res = dict(p.map(one, J))
    for (n, pol) in ((8, "none"), (16, "gate:8"), (32, "gate:8"), (16, "share:0.9:mid"), (16, "backoff:mid:0.2")):
        print(f"N={n:2d} {pol:16s} no overhead {res[('scripts.pkl', n, pol)]:6,.0f}   +0.3 s per call {res[('scripts_oh03.pkl', n, pol)]:6,.0f}")
