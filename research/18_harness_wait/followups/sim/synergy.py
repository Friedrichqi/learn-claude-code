"""Does F3a (deduplicated re-reads, 77 % of rewrites gone) also give the harness rules more margin? The share rule
(tau 0.9, after tool rounds) and backoff on the realistic signal, on the run's scripts vs the F3a scripts; ratios
against the gate on the same scripts."""
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
JOBS = [(scr, n, p) for scr in ("scripts.pkl", "scripts_dedup77.pkl") for n in (16, 32)
        for p in ("gate:8", "share:0.9:mid", "backoff:mid:0.2")]
def one(j):
    scr, n, pol = j
    return j, float(np.mean([Sim(load(scr), n, pol, seed=s).run()["calls_h"] for s in (18, 19, 20)]))
if __name__ == "__main__":
    with Pool(12) as p:
        res = dict(p.map(one, JOBS))
    for scr in ("scripts.pkl", "scripts_dedup77.pkl"):
        for n in (16, 32):
            g = res[(scr, n, "gate:8")]
            print(f"{scr:20s} N={n:2d} gate {g:6,.0f} | share {res[(scr, n, 'share:0.9:mid')]:6,.0f} "
                  f"({res[(scr, n, 'share:0.9:mid')]/g:.2f}x gate) | backoff(real signal) {res[(scr, n, 'backoff:mid:0.2')]:6,.0f} "
                  f"({res[(scr, n, 'backoff:mid:0.2')]/g:.2f}x gate)")
