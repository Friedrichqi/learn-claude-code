"""C1 lead: the backoff rule's hold rate and hold lengths in the simulator (measured w16_h_backoff: held at 31 % of
its checks, p50 2.6 s, p95 71 s; w32_h_backoff: 49 %, 6.8 s, 226 s)."""
import numpy as np
from kvsim import Sim, load

class B(Sim):
    def __init__(s, *a, **k):
        super().__init__(*a, **k); s.checks = 0; s.lens = []
    def _issue(s, sess):
        c = sess.calls[sess.i]
        point = "start" if (c["start"] or sess.i == 0) else "mid"
        fresh = sess.hold_t0 is None
        t0 = sess.hold_t0
        super()._issue(sess)
        if point in s.phases and s._in_window(s.t):
            if fresh:
                s.checks += 1
            if t0 is not None and sess.hold_t0 is None:
                s.lens.append(s.t - t0)

for n in (16, 32):
    b = B(load(), n, "backoff:mid")
    b.run()
    L = np.array(b.lens) if b.lens else np.array([0.0])
    print(f"N={n}: checks {b.checks}, holds {b.m['holds']} ({100*b.m['holds']/max(1,b.checks):.0f} % of checks), "
          f"held p50 {np.median(L):.1f} s, p95 {np.percentile(L,95):.0f} s")
