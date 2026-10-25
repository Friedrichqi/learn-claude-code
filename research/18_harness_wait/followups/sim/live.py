from multiprocessing import Pool
from kvsim import Sim, load
def one(a):
    n, pol = a
    r = Sim(load(), n, pol).run()
    return f"{n:2d}@{pol:22s} evicted {r['ev_blocks_s']:5.0f} blocks/s, live share {100*r['ev_live_share']:5.1f} %"
if __name__ == "__main__":
    with Pool(8) as p:
        for line in p.map(one, [(8, "none"), (16, "none"), (16, "gate:8"), (16, "backoff:mid"), (16, "share:0.9:mid"), (16, "share:0.9:start+mid"), (32, "share:0.9:mid"), (32, "gate:8")]):
            print(line, flush=True)
