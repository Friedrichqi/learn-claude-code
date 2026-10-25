"""C1 diagnostic: lead-call latency (arrival -> last token) in the simulator, against the measured H5 call p50
(w16_sgate 1.9 s, w16_h_share 2.6 s, w16_h_backoff 3.7 s, x16 not in H5's README table, w08_none 1.5 s)."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load

class L(Sim):
    def __init__(s, *a, **k):
        super().__init__(*a, **k); s.lat = []
    def _finish(s, r):
        if s._in_window(s.t) and not getattr(r, "wait", False):
            s.lat.append(s.t - r.t_arr)
        return super()._finish(r)

def one(j):
    n, pol = j
    s = L(load(), n, pol); s.run()
    v = np.array(s.lat)
    return j, float(np.median(v)), float(np.mean(v))

if __name__ == "__main__":
    J = [(8, "none"), (16, "gate:8"), (16, "share:0.9:mid"), (16, "backoff:mid"), (16, "backoff:mid:0.2")]
    with Pool(5) as p:
        for (n, pol), p50, mean in p.map(one, J):
            print(f"N={n:2d} {pol:18s} call latency p50 {p50:4.1f} s, mean {mean:5.1f} s")
