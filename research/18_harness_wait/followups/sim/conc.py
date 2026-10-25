"""Concurrency check: time-weighted running requests and the share of the pool they reference, simulated vs
measured (measured running: w08_none 6.3, w16_none 10.3-10.6, w16_sgate 7.5, w16_h_backoff 9.7, w16_h_share 9.7,
x16 9.0, w32_h_share 9.8, w32_sgate 7.5; KV in use while held 0.83-0.86)."""
from multiprocessing import Pool
from kvsim import Sim, load
def one(a):
    n, pol = a
    r = Sim(load(), n, pol).run()
    return f"N={n:2d} {pol:22s} running {r['running_mean']:4.1f}  referenced {r['referenced_mean']:.2f} of the pool  calls/h {r['calls_h']:6.0f}"
if __name__ == "__main__":
    with Pool(8) as p:
        for line in p.map(one, [(8, "none"), (16, "none"), (16, "gate:8"), (16, "backoff:mid"), (16, "share:0.9:mid"), (16, "share:0.9:start+mid"), (32, "share:0.9:mid"), (32, "gate:8")]):
            print(line, flush=True)
