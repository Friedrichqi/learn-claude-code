import json, lzma, glob, sys
from multiprocessing import Pool
ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
def real(cell):
    win = json.load(open(f"{ROOT}/{cell}/window.json"))
    ev = com = 0; off = None
    for p in glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz"):
        with lzma.open(p, "rt") as f:
            for line in f:
                if line.startswith('{"k":"clk"'):
                    r = json.loads(line); off = r["wall"] - r["t"]
                elif line.startswith('{"k":"step"') and off is not None:
                    r = json.loads(line)
                    if win["window_start"] <= r["s0"] + off <= win["window_end"]:
                        ev += r.get("ev") or 0; com += r.get("com") or 0
    return cell, ev / win["window_s"], com / win["window_s"]
def sim(a):
    from kvsim import Sim, load
    n, pol = a
    r = Sim(load(), n, pol).run()
    return f"{n}@{pol}", r["ev_blocks_s"], None
if __name__ == "__main__":
    with Pool(12) as p:
        R = p.map(real, ["w08_none", "w16_none", "w16_sgate", "w16_h_backoff", "w16_h_share", "x16_h_share_start", "w32_h_backoff", "w32_h_share"])
        S = p.map(sim, [(8, "none"), (16, "none"), (16, "gate:8"), (16, "backoff:mid"), (16, "share:0.9:mid"), (16, "share:0.9:start+mid"), (32, "backoff:mid"), (32, "share:0.9:mid")])
    for (c, ev, com), (s, sev, _) in zip(R, S):
        print(f"{c:20s} real: evicted {ev:6.1f} blocks/s (committed {com:6.1f})   sim {s:22s}: evicted {sev:6.1f} blocks/s")
