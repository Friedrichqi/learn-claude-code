"""Diagnostic: the age (now - release time) of what the simulator evicts, by kind, and the gap/kept pairs of
mid-turn calls in the share rule at N=16."""
import numpy as np
import kvsim
from kvsim import Sim, load

class D(Sim):
    def __init__(s, *a, **k):
        super().__init__(*a, **k); s.ages = {"orphan": [], "chain": []}
    def _alloc(s, k, exclude=None):
        if k <= s.free or not s._in_window(s.t):
            return super()._alloc(k, exclude)
        snap_o = {id(o): (o[0], o[2]) for o in s.orphans}
        snap_s = {x.id: (x.rel, x.chain) for x in s.sess.values()}
        ok = super()._alloc(k, exclude)
        live_o = {id(o): o[2] for o in s.orphans}
        for oid, (rel, n0) in snap_o.items():
            took = n0 - live_o.get(oid, 0)
            s.ages["orphan"] += [s.t - rel] * took
        for sid, (rel, n0) in snap_s.items():
            x = s.sess.get(sid)
            took = n0 - (x.chain if x is not None else 0)
            if took > 0:
                s.ages["chain"] += [s.t - rel] * took
        return ok

for pol in ("share:0.9:mid", "gate:8", "backoff:mid"):
    d = D(load(), 16, pol)
    r = d.run()
    tot = len(d.ages["orphan"]) + len(d.ages["chain"])
    print(f"N=16 {pol}: evicted blocks {tot}, from orphans {100*len(d.ages['orphan'])/max(1,tot):.0f} %")
    for kind, v in d.ages.items():
        if v:
            v = np.array(v)
            print(f"   {kind:6s} age at eviction p10/p50/p90 {np.percentile(v,10):6.1f} {np.percentile(v,50):6.1f} {np.percentile(v,90):6.1f} s")
    g = np.array([x for x in d.obs]) if d.obs else None
    if g is not None and len(g):
        for lo, hi in ((3, 10), (10, 30), (30, 100)):
            m = (g[:, 0] >= lo) & (g[:, 0] < hi)
            if m.any():
                print(f"   mid-turn calls after a {lo}-{hi} s gap: n {m.sum()}, kept p50 {np.median(g[m,1]):.2f}, mean {g[m,1].mean():.2f}")
