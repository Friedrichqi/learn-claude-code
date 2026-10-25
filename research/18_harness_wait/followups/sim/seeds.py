import sys
from multiprocessing import Pool
import numpy as np
from kvsim import Sim, load
scripts = load()
CFG = [(8, "none"), (16, "none"), (16, "gate:8"), (16, "share:0.9:start+mid"), (32, "gate:8"), (16, "gate:10")]
SEEDS = range(18, 24)
def one(a):
    (n, pol), seed = a
    return (n, pol, seed, Sim(scripts, n, pol, seed=seed).run()["calls_h"])
if __name__ == "__main__":
    with Pool(16) as p:
        res = p.map(one, [(c, s) for c in CFG for s in SEEDS])
    tab = {}
    for n, pol, seed, v in res:
        tab.setdefault((n, pol), {})[seed] = v
    for k, d in tab.items():
        v = np.array([d[s] for s in SEEDS])
        print(f"N={k[0]:2d} {k[1]:20s} mean {v.mean():6.0f} sd {v.std(ddof=1):5.0f} cv {v.std(ddof=1)/v.mean():.2f}  " + " ".join(f"{x:.0f}" for x in v))
    for a, b in [((16, "gate:8"), (16, "none")), ((16, "share:0.9:start+mid"), (16, "gate:8")), ((16, "gate:10"), (16, "gate:8"))]:
        d = np.array([tab[a][s] - tab[b][s] for s in SEEDS]); r = np.array([tab[a][s] / tab[b][s] for s in SEEDS])
        print(f"paired {a[1]} - {b[1]} at N={a[0]}: mean {d.mean():6.0f} sd {d.std(ddof=1):5.0f}; ratio {r.mean():.2f} (sd {r.std(ddof=1):.2f})")
