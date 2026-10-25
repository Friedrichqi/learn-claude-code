"""C1 diagnostic: the share of hold releases within 2 s of another release (as wt_analyze's H2 'bunched_releases'),
simulated. Measured: w16_h_share 0.46, w32_h_share 0.445, x16 0.442, w16_h_backoff 0.854, w32_h_backoff 0.939."""
from multiprocessing import Pool
from kvsim import Sim, load

class R(Sim):
    def __init__(s, *a, **k):
        super().__init__(*a, **k); s.rel = []
    def _issue(s, sess):
        held = sess.hold_t0 is not None
        n0 = len(s.waiting)
        super()._issue(sess)
        if held and sess.hold_t0 is None and len(s.waiting) > n0 and s._in_window(s.t):
            s.rel.append(s.t)

def bunched(rel):
    rel = sorted(rel)
    return sum(1 for i, t in enumerate(rel) if (i and t - rel[i - 1] < 2.0) or
               (i + 1 < len(rel) and rel[i + 1] - t < 2.0)) / len(rel) if rel else float("nan")

def one(j):
    n, pol = j
    r = R(load(), n, pol)
    r.run()
    return j, len(r.rel), bunched(r.rel)

if __name__ == "__main__":
    J = [(16, "share:0.9:mid"), (32, "share:0.9:mid"), (16, "share:0.9:start+mid"), (16, "backoff:mid"),
         (32, "backoff:mid"), (16, "backoff:mid:0.2"), (32, "backoff:mid:0.2")]
    with Pool(7) as p:
        for (n, pol), k, b in p.map(one, J):
            print(f"N={n:2d} {pol:22s} releases {k:5d}  bunched within 2 s {b:.2f}")
