"""Can a policy rescue a thrashing server? No policy until T (thrash builds up), then the policy; measured from
T+10 min for 60 min. Compared with the same policy from the start (same window)."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
scripts = load()
T = 1800.0

class Late(Sim):
    def __init__(self, n, pol, late, seed):
        super().__init__(scripts, n, pol, warm=T + 600, dur=3600, seed=seed)
        self.late = late
        self.real_pol, self.real_K = self.pol, self.K
    def _issue(self, s):
        if self.late and self.t < T:
            self.pol, self.K = "none", None
            try:
                return super()._issue(s)
            finally:
                self.pol, self.K = self.real_pol, self.real_K
        return super()._issue(s)
    def _gate_ok(self, r):
        if self.late and self.t < T:
            return True
        return super()._gate_ok(r)
    def run_metrics(self):
        r = self.run()
        running = r
        return r

def one(a):
    n, pol, late, seed = a
    s = Late(n, pol, late, seed)
    r = s.run()
    return (n, pol, late, seed, r["calls_h"], r["hit"])

if __name__ == "__main__":
    jobs = [(n, pol, late, seed) for n, pol in [(16, "backoff:mid"), (16, "share:0.9:mid"), (16, "share:0.9:start+mid"),
                                                 (16, "gate:8"), (32, "backoff:mid"), (32, "share:0.9:mid"), (32, "gate:8")]
            for late in (False, True) for seed in (18, 19)]
    with Pool(16) as p:
        res = p.map(one, jobs)
    tab = {}
    for n, pol, late, seed, c, h in res:
        tab.setdefault((n, pol, late), []).append((c, h))
    for (n, pol, late), v in tab.items():
        v = np.array(v)
        print(f"N={n} {pol:22s} {'after 30 min of thrash' if late else 'from the start        '}: calls/h {v[:,0].mean():6.0f} hit {v[:,1].mean():.2f}  (seeds: {' '.join(f'{x:.0f}' for x in v[:,0])})")
