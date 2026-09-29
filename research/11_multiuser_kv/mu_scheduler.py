"""Observing (and optionally KV-pinning) scheduler for vLLM 0.29.0 -- research/11_multiuser_kv.

vLLM builds the scheduler inside its EngineCore process from `--scheduler-cls`, so this module must be
importable there:

    PYTHONPATH=research/11_multiuser_kv MU_SCHED_LOG=/node-local/sched.jsonl \
        vllm serve Qwen/Qwen3-32B ... --scheduler-cls mu_scheduler.MUScheduler

Without MU_SCHED_LOG and MU_PIN the class is the stock AsyncScheduler (no hook installed). With
MU_SCHED_LOG it records, without changing any scheduling decision:

  arr   per request at arrival: session / chain, prompt tokens, the IDEAL hit (the longest leading
        run of its block hashes ever committed on this server -- a prefix-closed set, so a binary
        search), the hit it would get NOW (read-only lookup) and the common prefix with the chain's
        previous request. ideal - hit_now = prefix evicted while the user was away.
  adm   admission (first or after preemption): the hit it got; ideal (from arrival) - hit = recompute
        caused by eviction (split into lost-during-pause and lost-while-queued with arr.hit_now).
        [2026-09-26: servers started before this fix logged an admission ideal that included the
        request's own first chunk (committed inside schedule()), and classified that chunk as evict
        recompute; mu_analyze reclassifies every chunk from the raw fields, so both logs read alike.]
  pre   preemption: computed tokens thrown away, who asked for the blocks.
  tok1 / fin   first output token, finish (reason, output tokens, whether it ended in a tool call).
  step  per engine step: schedule / launch / update stamps (GPU time ~ u0[n] - max(u0[n-1], l[n])),
        running / waiting, prefill chunks classified fresh / evict / preempt recompute, decode
        requests, free blocks, evictions, commits, the head of the queue and why it waits.
  ev    evicted cached blocks per owner chain: live (still reusable by that chain's next request)
        or dead (e.g. the template's tail), the owner's state and seconds idle.
  pool  every MU_POOL_WALK_S: free / cached-free blocks, per chain cached-free blocks and the LRU
        distance of its first one (how many allocations until its cache starts to go).

Chains: one prefix chain per (session, purpose) -- the lead and the memory calls of a session keep
different prefixes. Sessions come from the X-Session-ID header (Request.session_id); purpose from
the X-Request-Id that research/11_multiuser_kv/mu_session.py sets (`<label>~a<k>~<call>~<purpose>~<agent>`).

MU_PIN=1 adds Continuum-style TTL pinning: when a request finishes (MU_PIN_WHEN=toolcall: only if its
output holds a tool call; always: every finish), an extra reference is taken on its hashed PROMPT
blocks (minus the last MU_PIN_TAIL_SKIP tokens: Qwen3's template re-renders the last assistant turn
differently, so output blocks are never reused). Pinned blocks cannot be evicted. A pin is released
(to the LRU tail) when the chain's next request is admitted, when MU_PIN_TTL expires (not while the
follow-up is queued), or under pressure: a running request's failed allocation releases the
latest-started session's pin first, so pins never cause a preemption; a waiting request may take
pins of later-started sessions only (program FCFS, MU_PIN_ADMIT=fcfs), or any pin when nothing runs.
Pinned blocks are capped at MU_PIN_MAX_FRAC of the pool.

MU_GATE=K adds a session admission gate ("let the ongoing sessions finish first"): at most K sessions
are active; a waiting request of an inactive session is not admitted while K others are active (it stays
queued, work-conserving only within the active set). A session becomes active in FCFS order of its
waiting requests and is released when it has had no request in flight for MU_GATE_IDLE_S seconds (a think
pause; tool pauses are sub-second), so the set of sessions whose prefixes compete for the pool is bounded.

Every hook fails open: an exception writes an `err` record, releases all pins and turns the observer
off; vLLM's own logic always runs exactly as in the parent class.
"""
from __future__ import annotations

import array
import atexit
import os
import queue
import re
import threading
import time
import traceback

import msgspec

import vllm
from vllm.v1.core.sched.async_scheduler import AsyncScheduler
from vllm.v1.request import RequestStatus

EXPECTED_VLLM = "0.29.0"
TOOL_CALL_TOKEN = int(os.environ.get("MU_TOOLCALL_TOKEN", "151657"))    # Qwen3 <tool_call>
PURPOSES = {"ld", "mx", "mr", "mc", "cs", "os", "tm", "un"}
SUFFIX = re.compile(r"-[0-9a-f]{8}$")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def external_id(request_id: str) -> str:
    """Engine id `chatcmpl-<X-Request-Id>-<8 hex>` -> `<X-Request-Id>`."""
    value = request_id
    for prefix in ("chatcmpl-", "cmpl-"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return SUFFIX.sub("", value)


def chain_key(request) -> tuple[str, str]:
    ext = external_id(request.request_id)
    parts = ext.split("~")
    purpose = parts[3] if len(parts) == 5 and parts[3] in PURPOSES else "un"
    session = request.session_id or (f"{parts[0]}~{parts[1]}" if len(parts) == 5 else ext)
    return session, purpose


class Sink:
    """Engine thread appends records; a daemon thread encodes and writes them."""

    def __init__(self, path: str, flush_s: float):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.handle = open(path, "ab")
        self.flush_s = flush_s
        self.buf: list = []
        self.last = time.monotonic()
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.encoder = msgspec.json.Encoder()
        self.thread = threading.Thread(target=self._writer, daemon=True, name="mu-sched-writer")
        self.thread.start()
        self.closed = False

    def put(self, record) -> None:
        self.buf.append(record)

    def maybe_flush(self, now: float) -> None:
        if now - self.last >= self.flush_s and self.buf:
            self.q.put(self.buf)
            self.buf = []
            self.last = now

    def _writer(self) -> None:
        while True:
            batch = self.q.get()
            if batch is None:
                break
            try:
                self.handle.write(b"".join(self.encoder.encode(r) + b"\n" for r in batch))
                self.handle.flush()
            except Exception:
                pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.buf:
            self.q.put(self.buf)
            self.buf = []
        self.q.put(None)
        self.thread.join(timeout=30)
        self.handle.close()


class ReqSt:
    __slots__ = ("idx", "chain", "t_arr", "np", "phase", "hit", "ideal", "pre_q", "rehit", "pf_end",
                 "t_q", "t_tok1", "npre", "t_load")

    def __init__(self, idx, chain, t_arr, np_, ideal):
        self.idx, self.chain, self.t_arr, self.np = idx, chain, t_arr, np_
        self.phase = "Q"            # Q queued, L loading (offload), R running, P preempted
        self.hit = self.pre_q = self.rehit = 0
        self.ideal = ideal          # tokens
        self.pf_end = np_
        self.t_q = t_arr            # queued since (arrival or preemption)
        self.t_tok1 = None
        self.npre = 0
        self.t_load = None


class ChainSt:
    __slots__ = ("idx", "key", "inflight", "last_fin", "last_np", "last_hashes", "pri", "pin")

    def __init__(self, idx, key, pri):
        self.idx, self.key, self.pri = idx, key, pri
        self.inflight = 0
        self.last_fin = None
        self.last_np = 0
        self.last_hashes: list[int] = []
        self.pin = None


class Pin:
    __slots__ = ("blocks", "t0", "exp", "pri")

    def __init__(self, blocks, t0, exp, pri):
        self.blocks, self.t0, self.exp, self.pri = blocks, t0, exp, pri


class MUScheduler(AsyncScheduler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._on = False
        self._pin_on = os.environ.get("MU_PIN", "0") == "1"
        self.gate_k = int(env_float("MU_GATE", 0))
        path = os.environ.get("MU_SCHED_LOG")
        if not path and not self._pin_on and not self.gate_k:
            return
        self.sink = Sink(path, env_float("MU_FLUSH_S", 1.0)) if path else None
        try:
            self._setup()
            self._on = True
        except Exception as exc:                                   # never block serving
            self._emit({"k": "err", "t": time.monotonic(), "where": "init", "exc": repr(exc),
                        "tb": traceback.format_exc()[-2000:]})
            self._pin_on = False

    # -- setup ---------------------------------------------------------------------------------------
    def _setup(self) -> None:
        assert vllm.__version__ == EXPECTED_VLLM, f"mu_scheduler is written against vLLM {EXPECTED_VLLM}"
        groups = self.kv_cache_config.kv_cache_groups
        assert len(groups) == 1, "one full-attention KV group expected (dense model)"
        assert self.hash_block_size == self.block_size, "hash block size must equal block size"
        assert self.cache_config.enable_prefix_caching, "prefix caching must be on"
        if self._pin_on:
            # an extra ref on a block would make cascade attention's shared-block test lie
            assert self.vllm_config.model_config.disable_cascade_attn, "pins need cascade attention off"
        self.kvm = self.kv_cache_manager
        self.bp = self.kvm.block_pool
        n = self.bp.num_gpu_blocks
        self.nblk = n
        self.bs = self.block_size
        self.owner = array.array("i", [-1]) * n          # block id -> chain idx that committed it
        self.cidx = array.array("i", [-1]) * n           # block id -> block index in that request
        self.chash = array.array("q", [0]) * n           # block id -> hash of its block hash
        self.ever: set[int] = set()
        self.req: dict[str, ReqSt] = {}
        self.chains: dict[tuple, ChainSt] = {}
        self.chain_list: list[ChainSt] = []
        self.next_req = 0
        self.pin_ttl = env_float("MU_PIN_TTL", 10.0)
        self.pin_when = os.environ.get("MU_PIN_WHEN", "toolcall")
        self.pin_admit = os.environ.get("MU_PIN_ADMIT", "fcfs")
        self.pin_tail = int(env_float("MU_PIN_TAIL_SKIP", 4))
        self.pin_cap = int(env_float("MU_PIN_MAX_FRAC", 0.5) * n)
        self.pinned_blocks = 0
        self.walk_s = env_float("MU_POOL_WALK_S", 2.0)
        self.last_walk = 0.0
        self.last_clk = 0.0
        self.gate_idle = env_float("MU_GATE_IDLE_S", 5.0)
        self.gate_active: dict[str, float] = {}         # session -> activation time
        self.sess_inflight: dict[str, int] = {}
        self.sess_last_fin: dict[str, float] = {}
        self._gated_ids: set[str] = set()
        self.fifo: list = []                            # [(id(scheduler_output), step record)]
        self.step_n = 0
        self.idle_run = None                            # [t0, t1, count] of consecutive empty steps
        self.cur_alloc = None                           # request whose allocation is in progress
        self.alloc_fail: set[str] = set()               # request ids whose allocation failed this step
        self.ev_buf: dict[int, list] = {}               # chain idx -> [n, live, dead]
        self.com_n = 0
        self.last_sched_t = None
        # instance-level wrappers (vLLM calls these through the instance)
        self._orig_evict = self.bp._maybe_evict_cached_block
        self.bp._maybe_evict_cached_block = self._evict_hook
        self._orig_cache = self.bp.cache_full_blocks
        self.bp.cache_full_blocks = self._cache_hook
        self._orig_alloc = self.kvm.allocate_slots
        self.kvm.allocate_slots = self._alloc_hook
        sc = self.scheduler_config
        self._emit({"k": "hdr", "t": time.monotonic(), "wall": time.time(), "pid": os.getpid(),
                    "vllm": vllm.__version__, "nblk": n, "bs": self.bs,
                    "mnbt": sc.max_num_batched_tokens, "mns": sc.max_num_seqs,
                    "mml": self.max_model_len, "policy": str(self.policy.value),
                    "watermark": getattr(sc, "watermark", None),
                    "reserve_full_isl": self.scheduler_reserve_full_isl,
                    "async": bool(getattr(sc, "async_scheduling", False)),
                    "connector": type(self.connector).__name__ if self.connector else None,
                    "defer_free": self.defer_block_free,
                    "pin": {"on": self._pin_on, "ttl": self.pin_ttl, "when": self.pin_when,
                            "admit": self.pin_admit, "tail": self.pin_tail, "cap": self.pin_cap},
                    "gate": {"k": self.gate_k, "idle_s": self.gate_idle}})
        atexit.register(self._close)

    # -- helpers -------------------------------------------------------------------------------------
    def _emit(self, record) -> None:
        if getattr(self, "sink", None) is not None:
            self.sink.put(record)

    def _fail(self, where: str, exc: BaseException) -> None:
        self._emit({"k": "err", "t": time.monotonic(), "where": where, "exc": repr(exc),
                    "tb": traceback.format_exc()[-3000:]})
        try:
            self._release_all("error")
        except Exception:
            pass
        self._on = False
        self._pin_on = False

    def _chain(self, request) -> ChainSt:
        key = chain_key(request)
        ch = self.chains.get(key)
        if ch is None:
            ch = ChainSt(len(self.chain_list), key, int(request.priority or 0))
            self.chains[key] = ch
            self.chain_list.append(ch)
            self._emit({"k": "chain", "t": time.monotonic(), "c": ch.idx, "sid": key[0], "p": key[1],
                        "pri": ch.pri})
        return ch

    def _leading_ever(self, hashes, cap: int) -> int:
        """Longest leading run of `hashes` ever committed (the set is prefix-closed: binary search)."""
        lo, hi = 0, min(cap, len(hashes))
        ever = self.ever
        while lo < hi:
            mid = (lo + hi + 1) >> 1
            if hash(hashes[mid - 1]) in ever:
                lo = mid
            else:
                hi = mid - 1
        return lo

    @staticmethod
    def _lcp(a: list[int], b: list[int]) -> int:
        """Common leading run of two chained-hash lists (equal prefixes stay equal: binary search)."""
        lo, hi = 0, min(len(a), len(b))
        while lo < hi:
            mid = (lo + hi + 1) >> 1
            if a[mid - 1] == b[mid - 1]:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # -- arrival ---------------------------------------------------------------------------------------
    def add_request(self, request) -> None:
        existing = request.request_id in self.requests
        super().add_request(request)
        if existing or not self._on:
            return
        try:
            t = time.monotonic()
            ch = self._chain(request)
            hashes = request.block_hashes
            cap = (request.num_tokens - 1) // self.bs
            ideal = self._leading_ever(hashes, cap) * self.bs
            _, hit_now, _ = self.kvm.coordinator.find_longest_cache_hit(hashes, request.num_tokens - 1)
            mine = [hash(h) for h in hashes]
            lcp = self._lcp(mine, ch.last_hashes) * self.bs
            gap = None if ch.last_fin is None else round(t - ch.last_fin, 4)
            pin = None if ch.pin is None else [len(ch.pin.blocks), round(t - ch.pin.t0, 3)]
            st = ReqSt(self.next_req, ch.idx, t, request.num_prompt_tokens, ideal)
            self.next_req += 1
            self.req[request.request_id] = st
            ch.inflight += 1
            self.sess_inflight[ch.key[0]] = self.sess_inflight.get(ch.key[0], 0) + 1
            ch.last_hashes = mine
            ch.last_np = request.num_prompt_tokens
            self._emit({"k": "arr", "t": t, "wall": time.time(), "r": st.idx,
                        "id": external_id(request.request_id), "c": ch.idx, "pri": int(request.priority or 0),
                        "fe": request.arrival_time, "np": request.num_prompt_tokens,
                        "mt": request.max_tokens, "nb": len(hashes), "ideal": ideal, "hit_now": hit_now,
                        "lcp": lcp, "gap": gap, "pin": pin, "q": [len(self.waiting), len(self.running)]})
        except Exception as exc:
            self._fail("add_request", exc)

    # -- scheduling ------------------------------------------------------------------------------------
    def schedule(self, *args, **kwargs):
        if not self._on:
            return super().schedule(*args, **kwargs)
        s0 = time.monotonic()
        gated = []
        try:
            if self._pin_on:
                self._expire_pins(s0)
            self.alloc_fail.clear()
            if self.gate_k:
                gated = self._gate(s0)
        except Exception as exc:
            self._fail("schedule-pre", exc)
        try:
            out = super().schedule(*args, **kwargs)
        finally:
            for request in reversed(gated):               # back at the front, in their order
                self.waiting.prepend_request(request)
        if self._on:
            try:
                self._post_schedule(out, s0)
            except Exception as exc:
                self._fail("schedule-post", exc)
        return out

    def _gate(self, now: float) -> list:
        """Release idle sessions, activate waiting sessions FCFS up to K, and take the waiting requests of
        inactive sessions out of the queue for this step."""
        for sess in list(self.gate_active):
            if not self.sess_inflight.get(sess) and now - self.sess_last_fin.get(sess, now) >= self.gate_idle:
                del self.gate_active[sess]
                self._emit({"k": "gate", "t": now, "s": sess, "on": 0, "active": len(self.gate_active)})
        gated = []
        for request in list(self.waiting):
            st = self.req.get(request.request_id)
            if st is None:
                continue
            sess = self.chain_list[st.chain].key[0]
            if sess in self.gate_active:
                continue
            if len(self.gate_active) < self.gate_k:
                self.gate_active[sess] = now
                self._emit({"k": "gate", "t": now, "s": sess, "on": 1, "active": len(self.gate_active),
                            "waited": round(now - st.t_q, 4)})
                continue
            gated.append(request)
        if gated:
            self.waiting.remove_requests(gated)
        self._gated_ids = {r.request_id for r in gated}
        return gated

    def _post_schedule(self, out, s0: float) -> None:
        now = time.monotonic()
        self.step_n += 1
        pf_fresh = pf_evict = pf_pre = dc = 0
        pfl, dcr = [], []
        n_adm = n_res = 0
        for rid, n in out.num_scheduled_tokens.items():
            r = self.requests.get(rid)
            st = self.req.get(rid)
            if r is None or st is None:
                continue
            a = r.num_computed_tokens - n                 # start of this chunk (advanced after schedule)
            if st.phase != "R":                            # admission: first, resume, or after a load
                resumed = st.phase == "P"
                if resumed:
                    st.rehit = a
                    n_res += 1
                else:
                    # st.ideal stays the ARRIVAL ideal: vLLM commits this chunk's blocks inside
                    # schedule(), so a lookup now would count the request's own fresh blocks as reusable
                    st.hit = a
                    n_adm += 1
                st.pf_end = r.num_tokens
                wait = round(now - st.t_q, 4)
                load = round(now - st.t_load, 4) if st.t_load is not None else None
                st.phase, st.t_load = "R", None
                ch = self.chain_list[st.chain]
                useful = None
                if ch.pin is not None and not resumed:
                    useful = min(a // self.bs, len(ch.pin.blocks))
                    self._release(ch, "hit", useful)
                ps = getattr(r, "prefill_stats", None)
                self._emit({"k": "adm", "t": now, "n": self.step_n, "r": st.idx, "res": int(resumed),
                            "hit": a, "ideal": st.ideal, "wait": wait, "load": load, "chunk": n,
                            "hl": getattr(ps, "num_local_cached_tokens", None) if ps else None,
                            "he": getattr(ps, "num_external_cached_tokens", None) if ps else None,
                            "lost": st.pre_q if resumed else None, "free": self.bp.get_num_free_blocks()})
            pf = max(0, min(a + n, st.pf_end) - a)
            if pf:
                lo, hi = a, a + pf
                pre = max(0, min(hi, st.pre_q) - max(lo, st.rehit)) if st.npre else 0
                ev = max(0, min(hi, st.ideal) - max(lo, st.hit))
                if pre and ev:                              # preemption recompute takes precedence
                    ev = max(0, ev - max(0, min(hi, st.pre_q, st.ideal) - max(lo, st.rehit, st.hit)))
                fresh = pf - pre - ev
                pf_fresh += fresh
                pf_evict += ev
                pf_pre += pre
                pfl.append([st.idx, a, pf, fresh, ev, pre])
            if n - pf > 0:
                dc += n - pf
                dcr.append(st.idx)
        head = hwhy = None
        wq = len(self.waiting)
        sk = len(self.skipped_waiting)
        if wq or sk:
            queue_ = self.waiting if wq else self.skipped_waiting
            try:
                hr = queue_.peek_request()
            except Exception:
                hr = None
            if hr is not None:
                hs = self.req.get(hr.request_id)
                head = hs.idx if hs else None
                if hr.request_id in self._gated_ids:
                    hwhy = "gate"
                elif out.preempted_req_ids:
                    hwhy = "pre"
                elif len(self.running) >= self.max_num_running_reqs:
                    hwhy = "seq"
                elif hr.request_id in self.alloc_fail:
                    hwhy = "kv"
                elif out.total_num_scheduled_tokens >= self.max_num_scheduled_tokens:
                    hwhy = "tok"
                elif hr.status == RequestStatus.WAITING_FOR_REMOTE_KVS:
                    hwhy = "load"
                else:
                    hwhy = "other"
        if self.connector is not None:                      # offload arm: async loads in flight
            for req in list(self.skipped_waiting):
                s = self.req.get(req.request_id)
                if s is not None and req.status == RequestStatus.WAITING_FOR_REMOTE_KVS and s.phase == "Q":
                    s.phase, s.t_load = "L", now
        ev = self.ev_buf
        self.ev_buf = {}
        n_ev = 0
        if ev:
            groups = []
            for ci, (cnt, live, dead) in ev.items():
                ch = self.chain_list[ci]
                idle = None if ch.inflight or ch.last_fin is None else round(now - ch.last_fin, 3)
                groups.append([ci, cnt, live, dead, "busy" if ch.inflight else "idle", idle])
                n_ev += cnt
            self._emit({"k": "ev", "t": now, "n": self.step_n, "g": groups})
        rec = {"k": "step", "n": self.step_n, "s0": s0, "s1": now, "l": None, "u0": None, "u1": None,
               "run": len(self.running), "wq": wq, "sk": sk, "tok": out.total_num_scheduled_tokens,
               "pf": [pf_fresh, pf_evict, pf_pre], "dc": dc, "pfl": pfl, "dcr": dcr,
               "adm": n_adm, "res": n_res, "pre": len(out.preempted_req_ids), "fin": len(out.finished_req_ids),
               "free": self.bp.get_num_free_blocks(), "ev": n_ev, "com": self.com_n,
               "pin": [sum(1 for c in self.chain_list if c.pin), self.pinned_blocks] if self._pin_on else None,
               "head": head, "hwhy": hwhy, "obs_us": round((time.monotonic() - now) * 1e6)}
        self.com_n = 0
        self.fifo.append((id(out), rec))

    def get_grammar_bitmask(self, scheduler_output):
        if self._on:
            try:
                t = time.monotonic()
                for key, rec in reversed(self.fifo):
                    if key == id(scheduler_output):
                        rec["l"] = t
                        break
                if t - self.last_walk >= self.walk_s:
                    self.last_walk = t
                    self._pool_walk(t)
            except Exception as exc:
                self._fail("grammar", exc)
        return super().get_grammar_bitmask(scheduler_output)

    def update_from_output(self, scheduler_output, model_runner_output):
        if not self._on:
            return super().update_from_output(scheduler_output, model_runner_output)
        u0 = time.monotonic()
        outs = super().update_from_output(scheduler_output, model_runner_output)
        if not self._on:
            return outs
        try:
            u1 = time.monotonic()
            rec = None
            if self.fifo and self.fifo[0][0] == id(scheduler_output):
                rec = self.fifo.pop(0)[1]
            else:
                for i, (key, candidate) in enumerate(self.fifo):
                    if key == id(scheduler_output):
                        rec = self.fifo.pop(i)[1]
                        break
            for rid in scheduler_output.num_scheduled_tokens:
                st = self.req.get(rid)
                if st is not None and st.t_tok1 is None:
                    r = self.requests.get(rid)
                    if r is not None and r.num_output_tokens > 0:
                        st.t_tok1 = u0
                        self._emit({"k": "tok1", "t": u0, "r": st.idx})
            if rec is not None:
                rec["u0"], rec["u1"] = u0, u1
                if rec["tok"] == 0:                         # fold empty steps into idle runs
                    if self.idle_run is None:
                        self.idle_run = [rec["s0"], u1, 0, rec["wq"], rec["head"], rec["hwhy"]]
                    self.idle_run[1] = u1
                    self.idle_run[2] += 1
                else:
                    if self.idle_run is not None:
                        t0, t1, count, wq, head, hwhy = self.idle_run
                        self._emit({"k": "idle", "t0": t0, "t1": t1, "count": count, "wq": wq,
                                    "head": head, "hwhy": hwhy})
                        self.idle_run = None
                    self._emit(rec)
            if u1 - self.last_clk >= 10.0:
                self.last_clk = u1
                self._emit({"k": "clk", "t": u1, "wall": time.time()})
            if self.sink is not None:
                self.sink.maybe_flush(u1)
        except Exception as exc:
            self._fail("update", exc)
        return outs

    # -- preemption and finish ------------------------------------------------------------------------
    def _preempt_request(self, request, timestamp, drop_stale_output: bool = False) -> None:
        if self._on:
            try:
                st = self.req.get(request.request_id)
                if st is not None:
                    st.pre_q = request.num_computed_tokens
                    st.npre += 1
                    st.phase = "P"
                    st.t_q = time.monotonic()
                    by = self.req.get(self.cur_alloc.request_id) if self.cur_alloc is not None else None
                    self._emit({"k": "pre", "t": st.t_q, "n": self.step_n + 1, "r": st.idx,
                                "nc": request.num_computed_tokens, "no": request.num_output_tokens,
                                "by": by.idx if by else None, "free": self.bp.get_num_free_blocks()})
            except Exception as exc:
                self._fail("preempt", exc)
        super()._preempt_request(request, timestamp, drop_stale_output)

    def _free_request(self, request, delay_free_blocks: bool = False):
        st = None
        if self._on:
            try:
                st = self.req.pop(request.request_id, None)
                if st is not None and self._pin_on:
                    self._maybe_pin(request, st)
            except Exception as exc:
                self._fail("free-pre", exc)
        result = super()._free_request(request, delay_free_blocks)
        if self._on and st is not None:
            try:
                now = time.monotonic()
                if st.t_tok1 is None and request.num_output_tokens > 0:
                    st.t_tok1 = now
                    self._emit({"k": "tok1", "t": now, "r": st.idx})
                ch = self.chain_list[st.chain]
                ch.inflight = max(0, ch.inflight - 1)
                ch.last_fin = now
                sess = ch.key[0]
                self.sess_inflight[sess] = max(0, self.sess_inflight.get(sess, 0) - 1)
                self.sess_last_fin[sess] = now
                reason = request.get_finished_reason()
                tool = TOOL_CALL_TOKEN in request._output_token_ids
                self._emit({"k": "fin", "t": now, "r": st.idx, "st": getattr(reason, "name", str(reason)).lower() if reason is not None
                            else request.status.name.lower(), "no": request.num_output_tokens, "npre": st.npre,
                            "tool": tool, "pin": len(ch.pin.blocks) if ch.pin else 0})
            except Exception as exc:
                self._fail("free-post", exc)
        return result

    # -- block pool hooks ------------------------------------------------------------------------------
    def _evict_hook(self, block) -> bool:
        evicted = self._orig_evict(block)
        if evicted and self._on:
            try:
                bid = block.block_id
                ci = self.owner[bid]
                if ci >= 0:
                    ch = self.chain_list[ci]
                    i = self.cidx[bid]
                    live = (i < len(ch.last_hashes) and ch.last_hashes[i] == self.chash[bid]
                            and (i + 1) * self.bs <= ch.last_np - self.pin_tail)
                    slot = self.ev_buf.get(ci)
                    if slot is None:
                        slot = self.ev_buf[ci] = [0, 0, 0]
                    slot[0] += 1
                    slot[1 if live else 2] += 1
                    self.owner[bid] = -1
            except Exception as exc:
                self._fail("evict", exc)
        return evicted

    def _cache_hook(self, request, blocks, num_cached_blocks, num_full_blocks, block_size,
                    kv_cache_group_id, block_mask=None):
        self._orig_cache(request, blocks, num_cached_blocks, num_full_blocks, block_size,
                         kv_cache_group_id, block_mask)
        if self._on and num_full_blocks > num_cached_blocks:
            try:
                st = self.req.get(request.request_id)
                ci = st.chain if st is not None else -1
                hashes = request.block_hashes
                for i in range(num_cached_blocks, min(num_full_blocks, len(hashes), len(blocks))):
                    b = blocks[i]
                    if b.is_null or b.block_hash is None:
                        continue
                    h = hash(hashes[i])
                    self.ever.add(h)
                    bid = b.block_id
                    self.owner[bid] = ci
                    self.cidx[bid] = i
                    self.chash[bid] = h
                    self.com_n += 1
            except Exception as exc:
                self._fail("cache", exc)

    def _alloc_hook(self, request, num_new_tokens, *args, **kwargs):
        self.cur_alloc = request
        try:
            out = self._orig_alloc(request, num_new_tokens, *args, **kwargs)
            if out is None and self._on:
                self.alloc_fail.add(request.request_id)
                if self._pin_on:
                    while out is None:
                        victim = self._pick_victim(request)
                        if victim is None:
                            break
                        running = request.status == RequestStatus.RUNNING
                        self._release(victim, "press_run" if running else "press_adm")
                        out = self._orig_alloc(request, num_new_tokens, *args, **kwargs)
            return out
        finally:
            self.cur_alloc = None

    # -- pins ------------------------------------------------------------------------------------------
    def _maybe_pin(self, request, st: ReqSt) -> None:
        # a stop (answer / tool call) or a length cap (the harness re-sends it with a larger budget)
        if request.status not in (RequestStatus.FINISHED_STOPPED, RequestStatus.FINISHED_LENGTH_CAPPED):
            return
        if self.pin_when == "toolcall" and TOOL_CALL_TOKEN not in request._output_token_ids:
            return
        ch = self.chain_list[st.chain]
        lim = max(0, request.num_prompt_tokens - self.pin_tail) // self.bs
        group = self.kvm.get_blocks(request.request_id).blocks[0]
        chosen = [b for b in list(group)[:lim] if not b.is_null and b.block_hash is not None]
        if not chosen:
            return
        if ch.pin is not None:
            self._release(ch, "replaced")
        while self.pinned_blocks + len(chosen) > self.pin_cap:
            others = [c for c in self.chain_list if c.pin is not None]
            if not others:
                return
            latest = max(others, key=lambda c: (c.pin.pri, c.pin.t0))
            if latest.pin.pri < ch.pri:           # the new pin is the latest session: skip it
                return
            self._release(latest, "cap")
        for b in chosen:
            b.ref_cnt += 1
        now = time.monotonic()
        ch.pin = Pin(chosen, now, now + self.pin_ttl, ch.pri)
        self.pinned_blocks += len(chosen)
        self._emit({"k": "pin", "t": now, "c": ch.idx, "r": st.idx, "blocks": len(chosen),
                    "excl": sum(1 for b in chosen if b.ref_cnt == 2), "exp": ch.pin.exp})

    def _release(self, ch: ChainSt, why: str, useful=None) -> None:
        pin = ch.pin
        if pin is None:
            return
        ch.pin = None
        self.pinned_blocks -= len(pin.blocks)
        self.bp.free_blocks(reversed(pin.blocks))
        self._emit({"k": "unpin", "t": time.monotonic(), "c": ch.idx, "why": why,
                    "held": round(time.monotonic() - pin.t0, 4), "blocks": len(pin.blocks), "useful": useful})

    def _release_all(self, why: str) -> None:
        for ch in getattr(self, "chain_list", []):
            if ch.pin is not None:
                self._release(ch, why)

    def _expire_pins(self, now: float) -> None:
        for ch in self.chain_list:
            pin = ch.pin
            if pin is not None and pin.exp <= now and ch.inflight == 0:
                self._release(ch, "ttl")

    def _pick_victim(self, request):
        me = self.req.get(request.request_id)
        mine = me.chain if me is not None else -1
        cands = [c for c in self.chain_list if c.pin is not None and c.idx != mine]
        if not cands:
            return None
        latest = lambda cs: max(cs, key=lambda c: (c.pin.pri, c.pin.t0))  # noqa: E731
        if request.status == RequestStatus.RUNNING or self.pin_admit == "always" or not self.running:
            return latest(cands)
        if self.pin_admit == "fcfs":
            later = [c for c in cands if c.pin.pri > int(request.priority or 0)]
            return latest(later) if later else None
        return None

    # -- periodic pool walk ----------------------------------------------------------------------------
    def _pool_walk(self, now: float) -> None:
        q = self.bp.free_block_queue
        block = q.fake_free_list_head.next_free_block
        tail = q.fake_free_list_tail
        pos = cfree = 0
        per: dict[int, list] = {}
        owner = self.owner
        while block is not None and block is not tail:
            if block._block_hash is not None:
                cfree += 1
                ci = owner[block.block_id]
                if ci >= 0:
                    slot = per.get(ci)
                    if slot is None:
                        per[ci] = [1, pos]
                    else:
                        slot[0] += 1
            pos += 1
            block = block.next_free_block
        self._emit({"k": "pool", "t": now, "free": pos, "cfree": cfree, "ever": len(self.ever),
                    "pinned": self.pinned_blocks,
                    "res": [[ci, v[0], v[1]] for ci, v in sorted(per.items())]})

    # -- lifecycle -------------------------------------------------------------------------------------
    def reset_prefix_cache(self, *args, **kwargs):
        if self._pin_on:
            try:
                self._release_all("reset")
            except Exception as exc:
                self._fail("reset", exc)
        ok = super().reset_prefix_cache(*args, **kwargs)
        if self._on and ok:
            self.ever.clear()
            self._emit({"k": "reset", "t": time.monotonic(), "ok": bool(ok)})
        return ok

    def shutdown(self) -> None:
        try:
            self._release_all("shutdown")
        except Exception:
            pass
        self._close()
        super().shutdown()

    def _close(self) -> None:
        sink = getattr(self, "sink", None)
        if sink is not None and not sink.closed:
            if getattr(self, "idle_run", None) is not None:
                t0, t1, count, wq, head, hwhy = self.idle_run
                sink.put({"k": "idle", "t0": t0, "t1": t1, "count": count, "wq": wq, "head": head,
                          "hwhy": hwhy})
                self.idle_run = None
            sink.close()
