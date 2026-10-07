"""B1's scheduler side for research/13_tiered_memory, in vLLM's EngineCore process: 12's HWScheduler (11's
MUScheduler plus HBM records) plus agent hints.

    PYTHONPATH=research/13_tiered_memory:research/12_hw_utilization:research/11_multiuser_kv \
    TM_HINTS=ipc:///node-local/tm.hints MU_SCHED_LOG=/node-local/sched.jsonl \
        vllm serve ... --scheduler-cls tm_scheduler.TMScheduler

Hints arrive from tm_server_ext (API server) on a ZMQ PULL socket bound at TM_HINTS and are drained at every
schedule(). Per session it keeps: running / away / ended, when it went away, the predicted return (p50, p90),
whether it was demoted. Policies, each switched by env (all off: the class only observes and logs):
  TM_DEMOTE=1     a session that goes away for a predicted p50 >= TM_DEMOTE_S (default 2 s), or has been away for
                  TM_AWAY_S (default 10 s) whatever was predicted, or has ended, gets its cached-free GPU blocks
                  moved to the front of the free queue (evicted first), tail blocks first. Blocks another chain's
                  last prompt also uses (a shared prefix such as the system prompt) stay where they are. Needs a CPU
                  tier (a finished request's blocks are freed only after their store completes, so every
                  cached-free block has a CPU copy); without a connector set TM_DEMOTE_NO_TIER=1 to allow it.
  TM_PROTECT=1    with MU_PIN=1: a session away for a predicted p90 < TM_DEMOTE_S keeps its pin until that p90
                  (+ TM_PIN_MARGIN_S, default 1 s) instead of MU_PIN_TTL; a long wait releases the pin
  TM_GATE_AUTO=1  with MU_GATE>0: K is recomputed at every pool walk as usable blocks / the median block count of
                  the sessions with requests, clamped to [TM_GATE_MIN (2), TM_GATE_MAX (64)]
  TM_POLICY=1     fill tm_policy.REG (block hash -> session) for the CPU tier's SessionAwareLRU (session states
                  are always shared with it)
Records added to MU_SCHED_LOG:
  tmh   a hint: session, event, predicted p50 / p90, tool keys
  tmd   a demotion: session, why (predicted / timeout / ended), blocks moved, shared blocks kept
  tmr   a session's next request arrived: away time, predicted p50, demoted or not, request idx (join with adm)
  tmp   a prefetch shadow request arrived (tm_server_ext, request id tmpf-...)
  tmg   gate size change
Every hook fails open: an exception writes an `err` record and turns only these hooks off.
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in ("12_hw_utilization", "11_multiuser_kv"):
    _p = os.path.join(_HERE, "..", _d)
    if _p not in sys.path:
        sys.path.insert(1, _p)

import hw_scheduler  # noqa: E402  (research/12_hw_utilization)
import mu_scheduler  # noqa: E402  (research/11_multiuser_kv)

env_float = mu_scheduler.env_float


class TMScheduler(hw_scheduler.HWScheduler):
    _tm_on = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not self._on:
            return
        try:
            self._tm_setup()
            self._tm_on = True
        except Exception as exc:
            self._emit({"k": "err", "t": time.monotonic(), "where": "tm_init", "exc": repr(exc),
                        "tb": traceback.format_exc()[-2000:]})

    def _tm_setup(self) -> None:
        self.tm_demote = os.environ.get("TM_DEMOTE", "0") == "1"
        self.tm_protect = os.environ.get("TM_PROTECT", "0") == "1"
        self.tm_gate_auto = os.environ.get("TM_GATE_AUTO", "0") == "1"
        self.tm_demote_s = env_float("TM_DEMOTE_S", 2.0)
        self.tm_away_s = env_float("TM_AWAY_S", 10.0)
        self.tm_margin = env_float("TM_PIN_MARGIN_S", 1.0)
        self.tm_gate_min = int(env_float("TM_GATE_MIN", 2))
        self.tm_gate_max = int(env_float("TM_GATE_MAX", 64))
        self.tm_no_tier = os.environ.get("TM_DEMOTE_NO_TIER", "0") == "1"
        self.tm_policy = os.environ.get("TM_POLICY", "0") == "1"      # fill tm_policy.REG (block hash -> session)
        self.tm_sess: dict[str, dict] = {}
        self.tm_last_check = 0.0
        self.tm_sock = None
        try:
            import tm_policy
            self.tm_reg = tm_policy.REG
        except Exception:
            self.tm_reg = {"sess_of_hash": {}, "state": {}, "evictions": {}}
        addr = os.environ.get("TM_HINTS", "")
        if addr:
            import zmq
            if addr.startswith("ipc://"):
                os.makedirs(os.path.dirname(addr[len("ipc://"):]) or ".", exist_ok=True)
            self.tm_sock = zmq.Context.instance().socket(zmq.PULL)
            self.tm_sock.setsockopt(zmq.RCVHWM, 100000)
            self.tm_sock.bind(addr)
            self._tm_zmq = zmq
        self._emit({"k": "tmcfg", "t": time.monotonic(), "demote": self.tm_demote, "protect": self.tm_protect,
                    "gate_auto": self.tm_gate_auto, "demote_s": self.tm_demote_s, "away_s": self.tm_away_s,
                    "hints": addr, "connector": type(self.connector).__name__ if self.connector else None})

    def _tm_fail(self, where: str, exc: BaseException) -> None:
        self._emit({"k": "err", "t": time.monotonic(), "where": f"tm_{where}", "exc": repr(exc),
                    "tb": traceback.format_exc()[-2500:]})
        self._tm_on = False

    # -- hints ------------------------------------------------------------------------------------------------
    def _tm_drain(self, now: float) -> None:
        if self.tm_sock is None:
            return
        zmq = self._tm_zmq
        for _ in range(1000):
            try:
                msg = self.tm_sock.recv_json(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            self._tm_hint(msg, now)

    def _tm_hint(self, msg: dict, now: float) -> None:
        sess, ev = msg.get("s"), msg.get("ev")
        if not sess:
            return
        st = self.tm_sess.setdefault(sess, {"state": "running", "t_away": None, "p50": None, "p90": None,
                                            "demoted": False, "ended": False, "ret": 0.0})
        p50, p90 = msg.get("p50"), msg.get("p90")
        self._emit({"k": "tmh", "t": now, "s": sess, "ev": ev, "p50": p50, "p90": p90, "keys": msg.get("keys"),
                    "n_tok": msg.get("n_tok")})
        if ev in ("tool_start", "turn_end"):
            if self.sess_inflight.get(sess):            # another request of the session is still running
                return
            st.update(state="away", t_away=now, p50=p50, p90=p90, demoted=False, pending=None,
                      ret=now + (p50 or 0.0))
            if self.tm_demote and p50 is not None and p50 >= self.tm_demote_s:
                self._tm_demote(sess, now, "predicted")
            elif self.tm_protect and self._pin_on and p90 is not None:
                self._tm_protect(sess, now, p90)
        elif ev == "session_end":
            st.update(state="away", ended=True, ret=now + 1e9)
            if self.tm_demote:
                self._tm_demote(sess, now, "ended")
        self.tm_reg["state"][sess] = st

    def _tm_protect(self, sess: str, now: float, p90: float) -> None:
        for ch in self.chain_list:
            if ch.key[0] == sess and ch.pin is not None:
                if p90 < self.tm_demote_s:
                    ch.pin.exp = ch.pin.t0 + p90 + self.tm_margin
                else:
                    self._release(ch, "tm_long")

    def _tm_demote(self, sess: str, now: float, why: str) -> None:
        st = self.tm_sess.get(sess)
        if st is None or st.get("demoted"):
            return
        if self.connector is None and not self.tm_no_tier:
            return
        mine = {ch.idx for ch in self.chain_list if ch.key[0] == sess}
        shared = set()
        for ch in self.chain_list:
            if ch.idx not in mine and (ch.inflight or (ch.last_fin is not None and now - ch.last_fin < 600)):
                shared.update(ch.last_hashes)
        moved, kept, busy = [], 0, 0
        blocks = self.bp.blocks
        for bid in range(1, self.nblk):
            ci = self.owner[bid]
            if ci < 0 or ci not in mine:
                continue
            b = blocks[bid]
            if b.block_hash is None:
                continue
            if b.ref_cnt != 0 or b.prev_free_block is None:
                busy += 1                                         # still held (e.g. its CPU store is in flight)
                continue
            if self.chash[bid] in shared:
                kept += 1
                continue
            moved.append((self.cidx[bid], b))
        if moved:
            moved.sort(key=lambda x: -x[0])                      # tail blocks first
            q = self.bp.free_block_queue
            for _, b in moved:
                q.remove(b)
            q.prepend_n([b for _, b in moved])
        if busy and not moved:                                   # nothing freed yet: retry from _tm_check
            st["pending"] = why
            return
        st["demoted"], st["pending"] = True, None
        self._emit({"k": "tmd", "t": now, "s": sess, "why": why, "moved": len(moved), "kept_shared": kept,
                    "busy": busy, "away_s": round(now - st["t_away"], 3) if st.get("t_away") else None})

    def _tm_check(self, now: float) -> None:
        """Demote sessions away longer than TM_AWAY_S whatever was predicted."""
        if not self.tm_demote or now - self.tm_last_check < 0.5:
            return
        self.tm_last_check = now
        for sess, st in self.tm_sess.items():
            if st["state"] != "away" or st["demoted"]:
                continue
            if st.get("pending"):
                self._tm_demote(sess, now, st["pending"])
            elif st["t_away"] is not None and now - st["t_away"] >= self.tm_away_s:
                self._tm_demote(sess, now, "timeout")

    def _tm_register(self, request, sess: str) -> None:
        soh = self.tm_reg["sess_of_hash"]
        for h in request.block_hashes:
            soh.setdefault(h, sess)
        if len(soh) > 2_000_000:                                  # bounded: drop the oldest quarter
            for key in list(soh)[:500_000]:
                del soh[key]

    # -- hooks ------------------------------------------------------------------------------------------------
    def schedule(self, *args, **kwargs):
        if self._tm_on:
            try:
                now = time.monotonic()
                self._tm_drain(now)
                self._tm_check(now)
            except Exception as exc:
                self._tm_fail("schedule", exc)
        return super().schedule(*args, **kwargs)

    def add_request(self, request) -> None:
        super().add_request(request)
        if not self._tm_on:
            return
        try:
            now = time.monotonic()
            sess = mu_scheduler.chain_key(request)[0]
            if self.tm_policy:
                self._tm_register(request, sess)
            st = self.req.get(request.request_id)
            idx = st.idx if st is not None else None
            if request.request_id.startswith("tmpf-"):
                self._emit({"k": "tmp", "t": now, "s": sess, "r": idx, "np": request.num_prompt_tokens})
                return
            ts = self.tm_sess.get(sess)
            if ts is not None and ts["state"] == "away":
                self._emit({"k": "tmr", "t": now, "s": sess, "r": idx,
                            "away_s": round(now - ts["t_away"], 3) if ts["t_away"] else None,
                            "p50": ts["p50"], "p90": ts["p90"], "demoted": ts["demoted"]})
                ts.update(state="running", t_away=None, demoted=False)
                self.tm_reg["state"][sess] = ts
        except Exception as exc:
            self._tm_fail("add_request", exc)

    def _free_request(self, request, delay_free_blocks: bool = False):
        if self._tm_on:
            try:
                if self.tm_policy:                                # the reply's blocks too
                    self._tm_register(request, mu_scheduler.chain_key(request)[0])
            except Exception as exc:
                self._tm_fail("free", exc)
        return super()._free_request(request, delay_free_blocks)

    def _pool_walk(self, now: float) -> None:
        super()._pool_walk(now)
        if not (self._tm_on and self.tm_gate_auto and self.gate_k):
            return
        try:
            sizes = []
            for ch in self.chain_list:
                if ch.inflight and ch.last_np:
                    sizes.append(-(-ch.last_np // self.bs))
            if len(sizes) < 2:
                return
            sizes.sort()
            med = sizes[len(sizes) // 2]
            k = max(self.tm_gate_min, min(self.tm_gate_max, (self.nblk - 1) // max(1, med)))
            if k != self.gate_k:
                self._emit({"k": "tmg", "t": now, "from": self.gate_k, "to": k, "median_blocks": med,
                            "sessions": len(sizes)})
                self.gate_k = k
        except Exception as exc:
            self._tm_fail("gate", exc)
