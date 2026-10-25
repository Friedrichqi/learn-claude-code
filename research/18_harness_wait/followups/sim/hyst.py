"""Metastability probe: N sessions after a switch time T, starting from a healthy state (gate K=8 until T) or a
thrashing one (N0 > N sessions without a gate until T, then the extra slots retire). Measured from T+10 min."""
import sys, time
from multiprocessing import Pool
from kvsim import Sim, load
scripts = load()
T = 1800.0

class Switch(Sim):
    def __init__(self, n_after, mode, n_before, **kw):
        super().__init__(scripts, max(n_after, n_before), "gate:8" if mode == "healthy" else "none",
                         warm=T + 600, dur=3600, **kw)
        self.n_after, self.mode = n_after, mode
    def step(self):
        if self.t >= T and self.K is not None:
            self.K, self.active = None, set()            # gate off at T
        return super().step()
    def _issue(self, s):
        if self.t >= T and s.slot >= self.n_after:
            s.busy = False
            self._orphan(s.chain, s.rel); s.chain = 0
            self.sess.pop(s.id, None)                     # the extra slots retire
            return
        super()._issue(s)

def one(args):
    n_after, mode, n_before, seed = args
    r = Switch(n_after, mode, n_before, seed=seed).run()
    return f"N={n_after} from {mode:8s} (before: {n_before:2d} sessions{', gate 8' if mode=='healthy' else ''}) seed {seed}: calls/h {r['calls_h']:6.0f} hit {r['hit']:.2f} q_p50 {r['q_p50']:5.1f}"

if __name__ == "__main__":
    jobs = []
    for n in (12, 13, 14):
        for seed in (18, 19):
            jobs += [(n, "healthy", n, seed), (n, "thrash", 24, seed)]
    with Pool(12) as p:
        for line in p.map(one, jobs):
            print(line, flush=True)
