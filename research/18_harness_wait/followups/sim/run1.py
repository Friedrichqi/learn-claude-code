import sys, time, json
from kvsim import Sim, load
scripts = load()
for spec in sys.argv[1:]:
    n, pol = spec.split("@")
    t0 = time.time()
    r = Sim(scripts, int(n), pol, warm=600, dur=1800).run()
    print(f"N={n:>2} {pol:18s} calls/h {r['calls_h']:7.0f} hit {r['hit']:.3f} hit/ideal {r['hit_ideal']:.3f} "
          f"evictGPU {r['evict_gpu']:.2f} freshGPU {r['fresh_gpu']:.2f} decGPU {r['dec_gpu']:.2f} q_p50 {r['q_p50']:.1f} "
          f"q<0.5 {r['q_lt05']:.2f} turn_p50 {r['turn_p50']:.0f} pre {r['preempt']} ({time.time()-t0:.0f}s)", flush=True)
