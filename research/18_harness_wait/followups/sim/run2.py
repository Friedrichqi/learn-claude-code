import sys, time
from multiprocessing import Pool
from kvsim import Sim, load
import os
scripts = load(os.environ.get("SCRIPTS", "scripts.pkl"))
def one(spec):
    n, pol = spec.split("@")
    t0 = time.time()
    pool = int(os.environ.get("POOL", "16136"))
    r = Sim(scripts, int(n), pol, pool=pool).run()
    return (f"N={n:>2} {pol:20s} calls/h {r['calls_h']:6.0f} hit {r['hit']:.3f} evictGPU {r['evict_gpu']:.2f} "
            f"freshGPU {r['fresh_gpu']:.2f} decGPU {r['dec_gpu']:.2f} q_p50 {r['q_p50']:5.1f} q<0.5 {r['q_lt05']:.2f} "
            f"turn_p50 {r['turn_p50']:5.0f} holds {r['holds']} cap {r['cap_rel']} hold/call {r['hold_s_per_call']:.1f} pre {r['preempt']} ({time.time()-t0:.0f}s)")
if __name__ == "__main__":
    with Pool(min(16, len(sys.argv) - 1)) as p:
        for line in p.map(one, sys.argv[1:]):
            print(line, flush=True)
