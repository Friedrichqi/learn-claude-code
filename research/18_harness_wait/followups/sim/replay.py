"""Open-loop replay: the recorded arrivals of a cell (time, session, np, ideal, output) through the simulator's
server (no client model, no policy). Compare admission hit and eviction rate with the measured ones."""
import glob, json, lzma, math, sys
from multiprocessing import Pool
from kvsim import Sim, Sess, Req, BS
ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"

def load_cell(cell):
    win = json.load(open(f"{ROOT}/{cell}/window.json"))
    arr, fin, adm = [], {}, {}
    for p in glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz"):
        with lzma.open(p, "rt") as f:
            for line in f:
                if line.startswith('{"k":"arr"'):
                    arr.append(json.loads(line))
                elif line.startswith('{"k":"fin"'):
                    r = json.loads(line); fin[r["r"]] = r.get("no") or 1
                elif line.startswith('{"k":"adm"'):
                    r = json.loads(line); adm[r["r"]] = r
    return win, arr, fin, adm

class Replay(Sim):
    def __init__(self, cell, gate=None):
        win, arr, fin, adm = load_cell(cell)
        t0 = win["t_start"]
        super().__init__([{"calls": [{"delay": 0, "np": 1, "ideal": 0, "out": 1, "start": True}]}], 0,
                         f"gate:{gate}" if gate else "none", warm=win["window_start"] - t0, dur=win["window_s"])
        self.real_hit = sum(adm[a["r"]].get("hit") or 0 for a in arr
                            if a["r"] in adm and "~ld~" in a["id"] and win["window_start"] <= a["wall"] <= win["window_end"])
        self.real_np = sum(a["np"] for a in arr if a["r"] in adm and "~ld~" in a["id"] and win["window_start"] <= a["wall"] <= win["window_end"])
        self.byid = {}
        self.pending = {}
        for a in sorted(arr, key=lambda a: a["wall"]):
            sid = a["id"].split("~")[0] + ("" if "~ld~" in a["id"] else "~" + a["id"].split("~")[3] if len(a["id"].split("~")) > 3 else "")
            self._push(a["wall"] - t0, "arr", (sid, a["np"], a.get("ideal") or 0, fin.get(a["r"], 1), a.get("gap") is None))
    def run(self):
        end = self.warm + self.dur
        self.pinned_sids = set()
        while self.t < end:
            while self.events and self.events[0][0] <= self.t:
                _, _, kind, payload = __import__("heapq").heappop(self.events)
                sid, np_, ideal, out, first = payload
                s = self.byid.get(sid)
                if s is None:
                    self.uid += 1
                    s = Sess(self.uid, 0, None); self.sess[s.id] = s; self.byid[sid] = s
                r = Req(s.id, {"np": np_, "ideal": ideal, "out": out, "start": False}, self.t, first)
                if s.busy:                                   # one call per session at a time: wait for the last
                    self.pending.setdefault(s.id, []).append(r)
                    continue
                s.busy = True
                self.waiting.append(r)
            if not self.step():
                if not self.events:
                    break
                self.t = max(self.t, self.events[0][0])
        return self.report()
    def _finish(self, r):
        s = self.sess[r.s]
        s.chain, s.rel, s.busy = r.blocks, self.t, False
        q = self.pending.get(s.id)
        if q:
            nxt = q.pop(0); nxt.t_arr = self.t; s.busy = True; self.waiting.append(nxt)
        s.last_fin, s.ctx = self.t, r.np + r.out
        if self._in_window(self.t):
            self.m["calls"] += 1

def one(a):
    cell, gate = a
    rp = Replay(cell, gate)
    r = rp.run()
    return (f"{cell:18s} replay{'(gate '+str(gate)+')' if gate else '':9s}: hit {r['hit']:.3f} (measured {rp.real_hit/max(1,rp.real_np):.3f}), "
            f"evicted {r['ev_blocks_s']:.0f} blocks/s, evictGPU {r['evict_gpu']:.2f}, q_p50 {r['q_p50']:.1f} s, calls/h {r['calls_h']:.0f}")
if __name__ == "__main__":
    with Pool(8) as p:
        for line in p.map(one, [("w08_none", None), ("w16_none", None), ("w16_sgate", 8), ("w16_h_backoff", None), ("w16_h_share", None), ("x16_h_share_start", None), ("w32_h_share", None), ("w32_h_backoff", None)]):
            print(line, flush=True)
