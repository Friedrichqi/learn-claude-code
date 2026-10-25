from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
scripts = load()
EDGES = [0, 1, 3, 10, 30, 100, 1e9]
def one(a):
    n, pol = a
    sim = Sim(scripts, n, pol); sim.run()
    out = []
    for i in range(len(EDGES) - 1):
        v = [h for g, h in sim.obs if EDGES[i] <= g < EDGES[i + 1]]
        out.append(f"{np.median(v):.2f}({len(v)})" if v else "-")
    return f"N={n:2d} {pol:16s} " + " ".join(f"{x:>11s}" for x in out)
if __name__ == "__main__":
    print("sim, mid-turn calls, median admission hit/ideal (n) by gap (s):", " | ".join(f"{EDGES[i]:g}-{EDGES[i+1]:g}" for i in range(len(EDGES)-1)))
    with Pool(6) as p:
        for line in p.map(one, [(8, "none"), (16, "gate:8"), (16, "backoff:mid"), (16, "share:0.9:mid"), (32, "backoff:mid"), (32, "share:0.9:mid")]):
            print(line, flush=True)
