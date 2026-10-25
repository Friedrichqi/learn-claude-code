from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
scripts = load()
JOBS = [(16, "mix:0.9:start+mid:0.5"), (32, "mix:0.9:start+mid:0.5"), (16, "mix:0.9:start+mid:0.75"),
        (32, "mix:0.9:start+mid:0.75"), (16, "share:0.9:start+mid"), (16, "none"),
        (16, "wsgate:0.8"), (16, "wsgate:0.9"), (32, "wsgate:0.8"), (32, "wsgate:0.9"), (16, "gate:8"), (16, "gate:10")]
def one(a):
    n, pol = a
    out = []
    for seed in (18, 19, 20):
        r = Sim(scripts, n, pol, seed=seed).run()
        out.append((r["calls_h"], r["coop_per_sess_h"], r["free_per_sess_h"], r["hit"], r["evict_gpu"]))
    v = np.array(out)
    return (f"N={n:2d} {pol:24s} calls/h {v[:,0].mean():6.0f}  per session/h: cooperators {v[:,1].mean():5.0f}"
            f"  others {v[:,2].mean():5.0f}  hit {v[:,3].mean():.2f} evictGPU {v[:,4].mean():.2f}  (3 seeds)")
if __name__ == "__main__":
    with Pool(12) as p:
        for line in p.map(one, JOBS):
            print(line, flush=True)
