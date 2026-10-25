"""A small trace-driven, closed-loop simulator of N harness sessions on one vLLM-like server (prototype).

Server: FCFS waiting queue, a token budget per step (chunked prefill), the full prompt's blocks reserved at
admission, prefix caching over per-session chains, LRU eviction of unreferenced blocks (a chain is eaten from its
tail, as vLLM frees blocks in reverse order), recompute preemption (LIFO). Step time from topic 18's fitted step
model. Sessions: scripts taken from logged lead calls (client delay, prompt, reusable prefix, output, turn start).

Policies (one at a time):
  none                  stock FCFS + LRU
  gate:K                topic 11's session gate: at most K sessions active; a slot frees after 5 s idle
  share:TAU:PHASES      the harness share rule: hold while the working sets of the mid-turn sessions ahead
                        (first come, first served by turn start) plus one's own exceed TAU x pool; PHASES = mid,
                        start or start+mid; re-check every 2 s with jitter; CAP s cap (share:0.9:mid:600)
  qpin:FRAC             pin the cached prefix of queued requests (arrival -> admission), up to FRAC of the pool
  qlru                  queue-aware eviction: idle chains by LRU first, then queued requests' chains from the
                        queue's tail (furthest next use first); the queue head is evicted last
  lpm:AGE               admit the waiting request with the largest cached share first; a request waiting more
                        than AGE s goes first (FCFS among those)
  phase                 mid-turn calls before turn-start calls (FCFS within each class)
"""
from __future__ import annotations

import heapq
import math
import pickle
import random
from collections import deque

COEF = {"fixed": 0.012881081491892552, "mixed": -0.00013942860423670476, "pf_tok": 0.053665835364782914,
        "pf_attn": 0.0018250822188149384, "dec_seq": 0.00016206233570081527, "dec_ctx": 0.05090187075619806}
BS = 16


class Req:
    __slots__ = ("s", "np", "ideal", "out", "start", "t_arr", "hit", "done_pf", "dec", "blocks", "t_adm",
                 "pinned", "first", "gap", "offload", "wait")

    def __init__(self, s, c, t, first):
        self.s, self.np, self.ideal, self.out, self.start = s, c["np"], c["ideal"], c["out"], c["start"]
        self.t_arr, self.hit, self.done_pf, self.dec, self.blocks, self.t_adm = t, 0, 0, 0, 0, None
        self.pinned, self.first, self.gap = 0, first, None
        self.offload = bool(c.get("offload"))
        self.wait = bool(c.get("wait"))


class Sess:
    __slots__ = ("id", "slot", "calls", "i", "chain", "rel", "busy", "turn_start", "mid", "last_fin", "ctx",
                 "hold_t0", "hold_n", "bad", "k", "wleft", "wphase", "last_np", "g")

    def __init__(self, sid, slot, calls):
        self.id, self.slot, self.calls, self.i = sid, slot, calls, 0
        self.chain, self.rel, self.busy = 0, 0.0, False      # cached blocks of its chain, release time, referenced
        self.turn_start, self.mid, self.last_fin, self.ctx = None, False, -1e9, 0
        self.hold_t0, self.hold_n = None, 0


class Sim:
    def __init__(self, scripts, n, policy="none", pool=16136, mnbt=8192, warm=1200.0, dur=5400.0, seed=18,
                 coef=COEF, shared_blocks=167):
        self.rng = random.Random(seed)
        order = list(range(len(scripts)))
        self.rng.shuffle(order)
        self.queue_scripts = deque(scripts[i]["calls"] for i in order)
        self.n, self.pool, self.mnbt, self.warm, self.dur, self.c = n, pool, mnbt, warm, dur, coef
        p = policy.split(":")
        self.pol = p[0]
        self.K = int(p[1]) if self.pol == "gate" else None
        self.gate_order = p[2] if self.pol == "gate" and len(p) > 2 else "fcfs"
        if self.pol == "mix":
            self.pol, self.mix_frac = "share", float(p[3]); p = p[:3]
        else:
            self.mix_frac = 1.0
        self.tau = float(p[1]) if self.pol in ("share", "wsgate") else None
        self.phases = set(p[2].split("+")) if self.pol == "share" else set()
        self.cap = float(p[3]) if self.pol == "share" and len(p) > 3 else 600.0
        self.pin_frac = float(p[1]) if self.pol == "qpin" else 0.0
        self.age = float(p[1]) if self.pol == "lpm" else None
        self.st_age = float(p[4]) if self.pol == "share" and len(p) > 4 else 0.0     # status age (s)
        self.mid_exp = float(p[5]) if self.pol == "share" and len(p) > 5 else 0.0    # server mid flag expiry (s)
        self.snaps = deque()                                                         # (t, {sid: (ctx, mid, ts)})
        self.next_snap = 0.0
        self.d0 = float(p[1]) if self.pol == "pff" else None
        self.probe_only = self.pol == "probe"
        self.sig_sd = float(p[2]) if self.pol in ("backoff", "probe") and len(p) > 2 else None   # backoff:PHASES:SD
        self.sig_next, self.sig_wait, self.sig_kv = 0.0, 0, 0.0
        if self.pol == "probe":
            self.pol = "backoff"
        if self.pol == "backoff":
            self.phases = set(p[1].split("+"))
        if self.pol == "mwait":
            self.wp, self.wd, self.wq, self.wmode = float(p[1]), float(p[2]), float(p[3]), p[4]
            self.tau, self.phases, self.cap = 0.9, {"mid"}, 600.0
        self.t = 0.0
        self.G = shared_blocks                               # the system prompt + tools every session shares
        self.free = pool - shared_blocks                     # never used / evicted blocks (the shared chain is pinned)
        self.orphans = []                                    # heap of [rel, uid, nblocks]: garbage chains
        self.uid = 0
        self.sess = {}
        self.waiting: list[Req] = []
        self.running: list[Req] = []
        self.events = []                                     # heap of (t, seq, kind, sid)
        self.seq = 0
        self.active = set()                                  # gate
        self.obs = []                                        # (gap, admission hit / ideal) of mid-turn calls
        self.m = {"calls": 0, "np": 0, "hit": 0, "ideal": 0, "busy": 0.0, "evict_gpu": 0.0, "fresh_gpu": 0.0,
                  "dec_gpu": 0.0, "fixed_gpu": 0.0, "qwait": [], "turns": [], "preempt": 0, "holds": 0,
                  "cap_rel": 0, "hold_s": 0.0}
        for slot in range(n):                                # staggered starts as in the sweeps (~15 s apart)
            self._new_session(slot, slot * 15.0)

    # -- sessions -----------------------------------------------------------------------------------------
    def _new_session(self, slot, t):
        if not self.queue_scripts:
            return
        calls = self.queue_scripts.popleft()
        self.queue_scripts.append(calls)
        self.uid += 1
        s = Sess(self.uid, slot, calls)
        self.sess[s.id] = s
        self._push(t, "issue", s.id)

    def _push(self, t, kind, sid):
        self.seq += 1
        heapq.heappush(self.events, (t, self.seq, kind, sid))

    def _in_window(self, t):
        return self.warm <= t <= self.warm + self.dur

    def _issue(self, s: Sess):
        c = s.calls[s.i]
        point = "start" if (c["start"] or s.i == 0) else ("aftlong" if c["delay"] > 5.0 and "aftlong" in self.phases else "mid")
        if point == "start" and s.hold_t0 is None:
            s.turn_start, s.mid = self.t, True
        if self.pol == "mwait" and point == "mid":
            if getattr(s, "wleft", 0) in (0, None) and getattr(s, "wphase", None) is None and self.rng.random() < self.wp:
                k = 1
                if self.wmode == "loop":
                    while self.rng.random() < self.wq and k < 100:
                        k += 1
                s.wleft, s.wphase = k, "sleep" if self.wmode == "loop" else "call"
            if getattr(s, "wphase", None) == "sleep":          # loop: sleep, then a wait-only call
                s.wphase = "call"
                self._push(self.t + self.wd * self.rng.uniform(0.5, 1.5), "issue", s.id)
                return
            if getattr(s, "wphase", None) == "call":           # the wait-only call on the server
                r = Req(s.id, {"np": s.ctx + 60, "ideal": s.ctx, "out": 20, "start": False, "wait": True}, self.t, False)
                r.gap = self.t - s.last_fin
                s.busy = True
                self.waiting.append(r)
                return
            if getattr(s, "wphase", None) == "hold":           # block: the harness holds until the share rule fits
                if s.hold_t0 is None:
                    s.hold_t0 = self.t
                if self._share_holds(s, c) and self.t - s.hold_t0 < self.cap:
                    self._push(self.t + 2.0 * self.rng.uniform(0.5, 1.5), "issue", s.id)
                    return
                if self._in_window(self.t):
                    self.m["hold_s"] += self.t - s.hold_t0
                s.hold_t0, s.wphase = None, None
        if self.pol == "pff" and point == "start" and s.i > 0 and s.hold_t0 is None:
            if getattr(s, "bad", False):
                s.k = min(getattr(s, "k", 0) + 1, 6)
                d = self.d0 * (2 ** (s.k - 1)) * self.rng.uniform(0.5, 1.5)
                s.hold_t0 = self.t
                if self._in_window(self.t):
                    self.m["holds"] += 1; self.m["hold_s"] += d
                self._push(self.t + d, "issue", s.id)
                return
            s.k = 0
        if self.pol == "pff" and s.hold_t0 is not None:
            s.hold_t0 = None
        if self.pol == "backoff" and point in self.phases:
            used = (sum(r.blocks for r in self.running) + self.G) / self.pool
            if self.sig_sd is not None:                      # the stock /metrics view: sampled every 0.5 s, KV noisy
                if self.t >= self.sig_next:
                    self.sig_wait = len(self.waiting)
                    self.sig_kv = min(1.0, max(0.0, used + self.rng.gauss(0.0, self.sig_sd)))
                    self.sig_next = self.t + 0.5
                waiting_seen, used = self.sig_wait, self.sig_kv
            else:
                waiting_seen = len(self.waiting)
            if self.probe_only:                              # a timed 1-token probe: queued requests only, no KV view
                crowded = len(self.waiting) >= 1
            else:
                crowded = (waiting_seen >= 1 or used >= 0.92) if s.hold_t0 is None else not (waiting_seen == 0 and used < 0.85)
            if crowded and (s.hold_t0 is None or self.t - s.hold_t0 < 600.0):
                if s.hold_t0 is None:
                    s.hold_t0, s.hold_n = self.t, 0
                    if self._in_window(self.t):
                        self.m["holds"] += 1
                d = min(30.0, 2.0 * 2 ** s.hold_n) * self.rng.uniform(0.5, 1.5)
                s.hold_n += 1
                self._push(self.t + d, "issue", s.id)
                return
            if s.hold_t0 is not None and self._in_window(self.t):
                self.m["hold_s"] += self.t - s.hold_t0
            s.hold_t0, s.hold_n = None, 0
        if self.pol == "share" and point in self.phases and s.slot < self.mix_frac * self.n:
            if s.hold_t0 is None:
                s.hold_t0 = self.t
            if self._share_holds(s, c) and self.t - s.hold_t0 < self.cap:
                if s.hold_n == 0 and self._in_window(self.t):
                    self.m["holds"] += 1
                s.hold_n += 1
                self._push(self.t + 2.0 * self.rng.uniform(0.5, 1.5), "issue", s.id)
                return
            if s.hold_n and self._in_window(self.t):
                self.m["hold_s"] += self.t - s.hold_t0
                if self.t - s.hold_t0 >= self.cap:
                    self.m["cap_rel"] += 1
            s.hold_t0, s.hold_n = None, 0
        r = Req(s.id, c, self.t, s.i == 0)
        r.gap = self.t - s.last_fin if s.last_fin > 0 else None
        s.busy = True                                        # a queued request's chain is not yet referenced
        self.waiting.append(r)

    def _snapshot(self):
        snap = {}
        for o in self.sess.values():
            mid = o.mid or (self.mid_exp > 0 and self.t - o.last_fin < self.mid_exp and o.turn_start is not None)
            snap[o.id] = (o.ctx, mid, o.turn_start)
        self.snaps.append((self.t, snap))
        while self.snaps and self.snaps[0][0] < self.t - 5.0:
            self.snaps.popleft()

    def _share_holds(self, s: Sess, c) -> bool:
        need = c["np"] + c["out"]
        if self.st_age > 0 and self.snaps:
            a = self.st_age * self.rng.uniform(0.5, 1.5)
            snap = self.snaps[0][1]
            for t, sn in self.snaps:
                if t <= self.t - a:
                    snap = sn
            ahead = sum(ctx for sid, (ctx, mid, ts) in snap.items()
                        if sid != s.id and mid and ts is not None and ts < s.turn_start)
        else:
            ahead = sum(o.ctx for o in self.sess.values()
                        if o is not s and o.mid and o.turn_start is not None and o.turn_start < s.turn_start)
        return ahead + need > self.tau * self.pool * BS

    def _finish(self, r: Req):
        s = self.sess[r.s]
        s.chain, s.rel, s.busy = r.blocks, self.t, False
        s.last_fin, s.ctx = self.t, r.np + r.out
        s.last_np = r.np
        if r.wait:
            if self._in_window(self.t):
                self.m["wait_calls"] = self.m.get("wait_calls", 0) + 1
            s.wleft -= 1
            if self.wmode == "loop":
                s.wphase = "sleep" if s.wleft > 0 else None
            else:
                s.wphase, s.wleft = "hold", 0
            self._push(self.t + 0.05, "issue", s.id)
            return
        if self._in_window(self.t):
            self.m["calls"] += 1
            g = "coop" if s.slot < self.mix_frac * self.n else "free"
            self.m[g] = self.m.get(g, 0) + 1
        s.i += 1
        if s.i >= len(s.calls):                              # session over: its chain is garbage; next session
            self._orphan(s.chain, s.rel)
            s.chain, s.mid = 0, False
            del self.sess[s.id]
            if self._in_window(self.t) and s.turn_start is not None:
                self.m["turns"].append(self.t - s.turn_start)
            self._new_session(s.slot, self.t)
            return
        nxt = s.calls[s.i]
        if nxt["start"]:                                     # the turn ended
            if self._in_window(self.t) and s.turn_start is not None:
                self.m["turns"].append(self.t - s.turn_start)
            s.mid = False
        self._push(self.t + nxt["delay"], "issue", s.id)

    # -- blocks -------------------------------------------------------------------------------------------
    def _orphan(self, n, rel):
        if n > 0:
            self.uid += 1
            heapq.heappush(self.orphans, [rel, self.uid, n])

    def _victims(self, exclude):
        """Evictable chains in eviction order: [(key, sess)]; orphans are handled first by the caller."""
        waiting_pos = {}
        if self.pol in ("qlru", "qpin"):
            for i, w in enumerate(self.waiting):
                waiting_pos.setdefault(w.s, i)
        out = []
        for s in self.sess.values():
            if s.chain <= 0 or s.id == exclude or self._referenced(s, waiting_pos):
                continue
            if self.pol == "qlru" and s.id in waiting_pos:
                out.append(((1, -waiting_pos[s.id]), s))     # queued: deepest first, the head last
            else:
                out.append(((0, s.rel), s))
        out.sort(key=lambda x: x[0])
        return out

    def _referenced(self, s, waiting_pos):
        if s.busy and any(r.s == s.id for r in self.running):
            return True
        if self.pol == "qpin" and s.id in self.pinned_sids:
            return True
        return False

    def _evictable(self, exclude=None):
        n = sum(o[2] for o in self.orphans)
        wp = {}
        for s in self.sess.values():
            if s.chain > 0 and s.id != exclude and not self._referenced(s, wp):
                n += s.chain
        return n

    def _alloc(self, k, exclude=None) -> bool:
        if k <= self.free:
            self.free -= k
            return True
        if self.free + self._evictable(exclude) < k:
            return False
        need = k - self.free
        if self._in_window(self.t):
            self.m["ev_blocks"] = self.m.get("ev_blocks", 0) + need
        # orphans (garbage from rewrites and ended sessions) compete by release time like any idle chain
        cands = [((0, o[0]), "o", o) for o in self.orphans]
        cands += [(key, "s", s) for key, s in self._victims(exclude)]
        cands.sort(key=lambda x: x[0])
        for _, kind, x in cands:
            if kind == "o":
                take = min(need, x[2]); x[2] -= take
            else:
                take = min(need, x.chain)
                if self._in_window(self.t):                       # live = within the chain's latest prompt (as MU counts)
                    live_blocks = max(0, (getattr(x, "last_np", None) or 0) // BS - (getattr(x, "g", None) or 0))
                    nonlive = max(0, x.chain - live_blocks)
                    self.m["ev_live"] = self.m.get("ev_live", 0) + max(0, take - min(take, nonlive))
                x.chain -= take                                   # eaten from the tail
            need -= take
            if need == 0:
                break
        self.orphans = [o for o in self.orphans if o[2] > 0]
        heapq.heapify(self.orphans)
        self.free = 0
        return True

    # -- scheduling ---------------------------------------------------------------------------------------
    def _gate_ok(self, r):
        if self.pol == "wsgate":
            if r.s in self.active:
                return True
            for sid in list(self.active):
                s = self.sess.get(sid)
                if s is None or (not s.busy and self.t - s.last_fin >= 5.0):
                    self.active.discard(sid)
            used = sum(self.sess[sid].ctx for sid in self.active if sid in self.sess)
            return used + r.np + r.out <= self.tau * self.pool * BS
        if self.K is None:
            return True
        if r.s in self.active:
            return True
        # free slots of sessions idle for 5 s
        for sid in list(self.active):
            s = self.sess.get(sid)
            if s is None or (not s.busy and self.t - s.last_fin >= 5.0):
                self.active.discard(sid)
        return len(self.active) < self.K

    def _order(self):
        w = self.waiting
        if self.pol == "lpm":
            def key(r):
                s = self.sess[r.s]
                hb = min(s.chain, r.ideal // BS)
                old = (self.t - r.t_arr) > self.age
                return (0 if old else 1, r.t_arr if old else -hb / max(1, math.ceil(r.np / BS)))
            return sorted(w, key=key)
        if self.pol == "phase":
            return sorted(w, key=lambda r: (1 if (r.start or r.first) else 0, r.t_arr))
        if self.pol == "gate" and self.gate_order != "fcfs":
            def left(s):
                k, i = 0, s.i
                while i < len(s.calls):
                    k += 1; i += 1
                    if i < len(s.calls) and s.calls[i]["start"]:
                        break
                return k
            def key(r):
                s = self.sess[r.s]
                if r.s in self.active:
                    return (0, r.t_arr)
                return (1, left(s) if self.gate_order == "srpt" else s.i, r.t_arr)
            return sorted(w, key=key)
        return list(w)

    def _update_pins(self):
        self.pinned_sids = set()
        if self.pol != "qpin":
            return
        budget = self.pin_frac * self.pool
        for r in self.waiting:                               # queue order: the head first
            s = self.sess[r.s]
            if s.chain and budget >= s.chain:
                self.pinned_sids.add(s.id)
                budget -= s.chain

    def step(self):
        c = self.c
        self._update_pins()
        budget = self.mnbt
        cost = c["fixed"]
        any_pf = False
        sched = []
        # running requests first
        for r in list(self.running):
            if r.done_pf < r.np:
                n = min(r.np - r.done_pf, budget)
                if n <= 0:
                    continue
                sched.append(("pf", r, n))
                budget -= n
            else:
                if (r.np + r.dec) > (r.blocks + (getattr(self.sess[r.s], "g", None) or 0)) * BS:
                    if not self._alloc(1, exclude=r.s):
                        self._preempt()
                        if r not in self.running:
                            continue
                        if not self._alloc(1, exclude=r.s):
                            continue
                    r.blocks += 1
                sched.append(("dec", r, 1))
                budget -= 1
        # admissions
        for r in self._order():
            if budget <= 0:
                break
            if not self._gate_ok(r):
                continue
            s = self.sess[r.s]
            reuse = r.ideal // BS
            if r.first:
                g = min(self.G, reuse)                       # the shared prefix: cached once for everyone
                hit_own, own_reuse = 0, 0
            else:
                g = getattr(s, "g", None) or 0
                own_reuse = max(0, reuse - g)
                hit_own = min(s.chain, own_reuse)            # the session's own chain, eaten from the tail
            if s.chain > hit_own and not r.first:            # the part beyond an edit is garbage now
                self._orphan(s.chain - hit_own, s.rel)
                s.chain = hit_own
            need = math.ceil(r.np / BS) - g - hit_own        # own blocks to allocate (an offload reload needs them too)
            s_chain_before = s.chain
            if not self._alloc(need, exclude=r.s):
                break                                        # FCFS: the head blocks the rest
            s.chain = s_chain_before
            if r.first:
                s.g = g
            hit_b = g + (own_reuse if r.offload else hit_own)  # what needs no prefill compute
            r.hit = hit_b * BS
            if not (r.start or r.first) and r.ideal >= 1024:
                s.bad = (r.hit / r.ideal) < 0.5
                if self._in_window(self.t) and r.gap is not None:
                    self.obs.append((r.gap, r.hit / r.ideal))
            r.done_pf, r.blocks, r.t_adm = r.hit, hit_own + need, self.t
            self.waiting.remove(r)
            self.running.append(r)
            if self.K is not None or self.pol == "wsgate":
                self.active.add(r.s)
            if self._in_window(self.t):
                self.m["np"] += r.np
                self.m["hit"] += r.hit
                self.m["ideal"] += r.ideal
                self.m["qwait"].append(self.t - r.t_arr)
            n = min(r.np - r.done_pf, budget)
            if n > 0:
                sched.append(("pf", r, n))
                budget -= n
        if not sched:
            return False
        ev = fr = dc = 0.0
        for kind, r, n in sched:
            if kind == "pf":
                any_pf = True
                k = c["pf_tok"] * n / 1e3 + c["pf_attn"] * n * (r.done_pf + n / 2) / 1e6
                a, b = r.done_pf, r.done_pf + n
                evicted = max(0, min(b, r.ideal) - max(a, r.hit))
                ev += k * evicted / n
                fr += k * (n - evicted) / n
                cost += k
                r.done_pf += n
            else:
                k = c["dec_seq"] + c["dec_ctx"] * (r.np + r.dec) / 1e6
                dc += k
                cost += k
        if any_pf:
            cost += c["mixed"]
        t0 = self.t
        self.t += cost
        if self._in_window(t0):
            self.m["run_tw"] = self.m.get("run_tw", 0.0) + cost * len(self.running)
            self.m["ref_tw"] = self.m.get("ref_tw", 0.0) + cost * (sum(r.blocks for r in self.running) + self.G) / self.pool
            self.m["busy"] += cost
            self.m["evict_gpu"] += ev
            self.m["fresh_gpu"] += fr
            self.m["dec_gpu"] += dc
            self.m["fixed_gpu"] += cost - ev - fr - dc
        for kind, r, n in sched:
            if kind == "dec":
                r.dec += 1
            if r.done_pf >= r.np and r.dec >= r.out and r in self.running:
                self.running.remove(r)
                self._finish(r)
        return True

    def _preempt(self):
        if not self.running:
            return
        r = self.running.pop()                               # the last admitted
        s = self.sess[r.s]
        g = getattr(s, "g", None) or 0
        s.chain, s.rel = max(0, min(r.blocks, math.ceil(r.done_pf / BS) - g)), self.t
        self.free += r.blocks - s.chain
        r.done_pf, r.dec, r.blocks, r.hit = 0, 0, 0, 0
        self.waiting.insert(0, r)
        self.m["preempt"] += 1

    def run(self):
        end = self.warm + self.dur
        self.pinned_sids = set()
        while self.t < end:
            if self.st_age > 0 and self.t >= self.next_snap:
                self._snapshot()
                self.next_snap = self.t + 0.2
            while self.events and self.events[0][0] <= self.t:
                _, _, kind, sid = heapq.heappop(self.events)
                s = self.sess.get(sid)
                if s is not None and kind == "issue":
                    self._issue(s)
            if not self.step():
                if not self.events:
                    break
                self.t = max(self.t, self.events[0][0])
        return self.report()

    def report(self):
        m, h = self.m, self.dur / 3600
        q = sorted(m["qwait"]) or [0.0]
        tr = sorted(m["turns"]) or [0.0]
        self.turn_mean = sum(tr) / len(tr); self.turn_p95 = tr[int(0.95 * (len(tr) - 1))]
        b = m["busy"] or 1.0
        return {"calls_h": m["calls"] / h, "hit": m["hit"] / max(1, m["np"]), "hit_ideal": m["hit"] / max(1, m["ideal"]),
                "evict_gpu": m["evict_gpu"] / b, "fresh_gpu": m["fresh_gpu"] / b, "dec_gpu": m["dec_gpu"] / b,
                "q_p50": q[len(q) // 2], "q_lt05": sum(1 for x in q if x < 0.5) / len(q),
                "turn_p50": tr[len(tr) // 2], "turn_mean": sum(tr) / len(tr), "turn_p95": tr[int(0.95 * (len(tr) - 1))], "preempt": m["preempt"], "holds": m["holds"], "cap_rel": m["cap_rel"],
                "hold_s_per_call": m["hold_s"] / max(1, m["calls"]),
                "ev_blocks_s": m.get("ev_blocks", 0) / self.dur,
                "ev_live_share": m.get("ev_live", 0) / max(1, m.get("ev_blocks", 0)),
                "running_mean": m.get("run_tw", 0.0) / b, "referenced_mean": m.get("ref_tw", 0.0) / b,
                "wait_calls_per_100": 100 * m.get("wait_calls", 0) / max(1, m["calls"]),
                "coop_per_sess_h": m.get("coop", 0) / h / max(1e-9, self.mix_frac * self.n),
                "free_per_sess_h": m.get("free", 0) / h / max(1e-9, (1 - self.mix_frac) * self.n) if self.mix_frac < 1 else 0.0}


def load(path="scripts.pkl"):
    return pickle.load(open(path, "rb"))
