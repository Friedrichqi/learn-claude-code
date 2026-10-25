"""Status-exporting scheduler of research/18_harness_wait (vLLM 0.31.0).

Topic 11's observer (research/11_multiuser_kv/mu_scheduler.py: the per-request, per-step and
per-eviction log, and the MU_GATE session gate, both unchanged) plus a live status snapshot that the
API-process middleware wt_server_ext.py serves to the harnesses at GET /wt/status:

    PYTHONPATH=research/18_harness_wait:research/11_multiuser_kv MU_SCHED_LOG=.../sched.jsonl \\
    WT_STATUS_PATH=/dev/shm/wt_<port>.json vllm serve ... \\
        --scheduler-cls wt_scheduler.WTScheduler --middleware wt_server_ext.Middleware

The snapshot is JSON, replaced atomically (write + os.replace by a writer thread) at most every
WT_SNAP_S = 0.2 s while the engine steps, and once more when the engine drains:

  wall / t / seq / step    when it was taken (wall clock, monotonic), snapshot and engine step numbers
  pool                     usable blocks, block size, tokens, free blocks, usage (= vLLM's kv_cache_usage)
  running / waiting / deferred
  cum                      since start: preemptions, evictions (all / still reusable by their chain),
                           prefill tokens fresh / evicted recompute / preemption recompute, first
                           admissions with their hit and prompt tokens
  sessions                 sid -> the session's lead chain: first (wall), turn_t0 (wall: the start of its
                           current turn as the server sees it, i.e. the first lead request after a lead
                           reply without a tool call or after WT_TURN_GAP_S = 60 s without a request; a
                           harness that reports its own turn start to wt_server_ext overrides it),
                           inflight, queued, idle_s, mid (a lead request in flight, or the last
                           lead reply called a tool less than WT_MID_EXPIRE_S = 10 s ago), np / out (last
                           lead prompt / output tokens), ws (working set: max(np + out, live tokens)),
                           res (tokens of the session's blocks still in the prefix cache), prefix (leading
                           tokens of the last lead prompt + reply still cached: what its next call would
                           hit, with the append-only template)

Sessions idle for WT_DROP_IDLE_S = 900 s leave the snapshot. Every 1 s a `wt` record with the global
part goes to the sched log (MU_SCHED_LOG), so the analysis sees what the harnesses saw. Like the
observer, every hook fails open (an `err` record, the status stops updating, vLLM runs unchanged).
"""
from __future__ import annotations

import os
import threading
import time
import traceback

import msgspec

from mu_scheduler import TOOL_CALL_TOKEN, MUMixin, chain_key, env_float
from vllm.v1.core.sched.async_scheduler import AsyncScheduler
from vllm.v1.core.sched.scheduler import Scheduler


class SnapWriter:
    """Writes the newest snapshot only (an older one still pending is dropped), off the engine thread."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.tmp = f"{path}.tmp{os.getpid()}"
        self.cv = threading.Condition()
        self.pending = None
        self.closed = False
        self.written = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="wt-snapshot-writer")
        self.thread.start()

    def put(self, snapshot: dict) -> None:
        with self.cv:
            self.pending = snapshot
            self.cv.notify()

    def _run(self) -> None:
        encoder = msgspec.json.Encoder()
        while True:
            with self.cv:
                while self.pending is None and not self.closed:
                    self.cv.wait()
                if self.pending is None:
                    return
                snapshot, self.pending = self.pending, None
            try:
                data = encoder.encode(snapshot)
                with open(self.tmp, "wb") as handle:
                    handle.write(data)
                os.replace(self.tmp, self.path)
                self.written += 1
            except Exception:
                pass

    def close(self) -> None:
        with self.cv:
            self.closed = True
            self.cv.notify()
        self.thread.join(timeout=10)


class WTSess:
    __slots__ = ("sid", "first", "turn_t0", "inflight", "last_fin", "last_tool", "np", "out", "live",
                 "hashes", "chains")

    def __init__(self, sid: str, wall: float):
        self.sid = sid
        self.first = wall
        self.turn_t0 = wall
        self.inflight = 0
        self.last_fin = None          # monotonic
        self.last_tool = None         # did the last lead reply call a tool?
        self.np = self.out = 0
        self.live = None              # request id of the lead request in flight
        self.hashes: list = []        # block hashes of the last lead prompt
        self.chains: set[int] = set()


class WTMixin(MUMixin):
    # -- setup -------------------------------------------------------------------------------------
    def _setup(self) -> None:
        super()._setup()
        self.wt_path = os.environ.get("WT_STATUS_PATH")
        self.wt_snap_s = env_float("WT_SNAP_S", 0.2)
        self.wt_mid_expire = env_float("WT_MID_EXPIRE_S", 10.0)
        self.wt_turn_gap = env_float("WT_TURN_GAP_S", 60.0)
        self.wt_drop_s = env_float("WT_DROP_IDLE_S", 900.0)
        self.wt_res: dict[int, int] = {}           # chain idx -> its blocks still in the prefix cache
        self.wt_sess: dict[str, WTSess] = {}
        self.wt_np: dict[int, int] = {}            # request idx -> prompt tokens (arrival -> admission)
        self.wt_cum = {"preempt": 0, "evict": 0, "evict_live": 0, "pf_fresh": 0, "pf_evict": 0, "pf_pre": 0,
                       "adm": 0, "adm_hit": 0, "adm_np": 0}
        self.wt_seq = 0
        self.wt_last_snap = 0.0
        self.wt_last_rec = 0.0
        self.wt_on = True
        self.wt_writer = SnapWriter(self.wt_path) if self.wt_path else None
        self._emit({"k": "wthdr", "t": time.monotonic(), "path": self.wt_path, "snap_s": self.wt_snap_s,
                    "mid_expire_s": self.wt_mid_expire, "drop_idle_s": self.wt_drop_s})

    def _wt_fail(self, where: str, exc: BaseException) -> None:
        self._emit({"k": "err", "t": time.monotonic(), "where": f"wt-{where}", "exc": repr(exc),
                    "tb": traceback.format_exc()[-3000:]})
        self.wt_on = False

    # -- counters from the observer's own records ------------------------------------------------------
    def _emit(self, record) -> None:
        if getattr(self, "wt_on", False):
            try:
                kind = record.get("k")
                cum = self.wt_cum
                if kind == "step":
                    fresh, evict, pre = record["pf"]
                    cum["pf_fresh"] += fresh
                    cum["pf_evict"] += evict
                    cum["pf_pre"] += pre
                elif kind == "arr":
                    self.wt_np[record["r"]] = record["np"]
                elif kind == "adm":
                    np_ = self.wt_np.pop(record["r"], None)
                    if not record["res"] and np_ is not None:
                        cum["adm"] += 1
                        cum["adm_hit"] += record["hit"]
                        cum["adm_np"] += np_
                elif kind == "pre":
                    cum["preempt"] += 1
            except Exception as exc:
                self.wt_on = False
                super()._emit({"k": "err", "t": time.monotonic(), "where": "wt-count", "exc": repr(exc)})
        super()._emit(record)

    # -- block residency (MU calls these from its block-pool hooks) --------------------------------------
    def _on_commit(self, ci: int, bid: int, i: int) -> None:
        self.wt_res[ci] = self.wt_res.get(ci, 0) + 1

    def _on_evict(self, ci: int, bid: int, live: bool) -> None:
        self.wt_res[ci] = self.wt_res.get(ci, 0) - 1
        self.wt_cum["evict"] += 1
        self.wt_cum["evict_live"] += bool(live)

    def _on_reset(self) -> None:
        self.wt_res.clear()

    # -- sessions --------------------------------------------------------------------------------------
    def add_request(self, request) -> None:
        new = request.request_id not in self.requests
        super().add_request(request)
        if not (new and self._on and getattr(self, "wt_on", False)):
            return
        try:
            sid, purpose = chain_key(request)
            ch = self.chains.get((sid, purpose))
            wall = time.time()
            sess = self.wt_sess.get(sid)
            if sess is None:
                sess = self.wt_sess[sid] = WTSess(sid, wall)
            if ch is not None:
                sess.chains.add(ch.idx)
            if purpose != "ld":
                return
            idle = None if sess.last_fin is None else time.monotonic() - sess.last_fin
            if sess.inflight == 0 and (sess.last_tool is False or (idle is not None and idle > self.wt_turn_gap)):
                sess.turn_t0 = wall                    # the previous lead reply ended a turn
            sess.inflight += 1
            sess.np = request.num_prompt_tokens
            sess.out = 0
            sess.live = request.request_id
            sess.hashes = list(request.block_hashes)
        except Exception as exc:
            self._wt_fail("add", exc)

    def _free_request(self, request, delay_free_blocks: bool = False):
        info = None
        if self._on and getattr(self, "wt_on", False):
            try:
                sid, purpose = chain_key(request)
                if purpose == "ld":
                    info = (sid, request.num_output_tokens, TOOL_CALL_TOKEN in request._output_token_ids,
                            list(request.block_hashes))
            except Exception as exc:
                self._wt_fail("free-pre", exc)
        result = super()._free_request(request, delay_free_blocks)
        if info is not None:
            try:
                sess = self.wt_sess.get(info[0])
                if sess is not None:
                    sess.inflight = max(0, sess.inflight - 1)
                    sess.last_fin = time.monotonic()
                    sess.out = info[1]
                    sess.last_tool = info[2]
                    sess.hashes = info[3]              # prompt + reply: an append-only next prompt reuses both
                    if sess.live == request.request_id:
                        sess.live = None
            except Exception as exc:
                self._wt_fail("free-post", exc)
        return result

    # -- the snapshot ----------------------------------------------------------------------------------
    def update_from_output(self, scheduler_output, model_runner_output):
        outs = super().update_from_output(scheduler_output, model_runner_output)
        if self._on and getattr(self, "wt_on", False):
            try:
                now = time.monotonic()
                drained = not self.requests
                if self.wt_writer is not None and (drained or now - self.wt_last_snap >= self.wt_snap_s):
                    self.wt_last_snap = now
                    self.wt_writer.put(self.wt_snapshot(now))
                if now - self.wt_last_rec >= 1.0:
                    self.wt_last_rec = now
                    self._emit(self._wt_global(now, time.time()) | {"k": "wt"})
            except Exception as exc:
                self._wt_fail("snapshot", exc)
        return outs

    def _wt_global(self, now: float, wall: float) -> dict:
        wq, sk, _ = self._queue_view()
        usable = self.nblk - 1                        # vLLM keeps one null block
        free = self.bp.get_num_free_blocks()
        return {"t": now, "wall": wall, "step": self.step_n,
                "pool": {"blocks": usable, "bs": self.bs, "tokens": usable * self.bs, "free": free,
                         "usage": round(1.0 - free / usable, 5) if usable else 0.0},
                "running": len(self.running), "waiting": wq, "deferred": sk, "cum": dict(self.wt_cum)}

    def _cached_prefix(self, hashes: list) -> int:
        """Leading blocks of `hashes` still in the prefix cache. LRU eviction eats a chain from its tail
        (vLLM frees a request's blocks tail-first), so the cached part is a prefix: binary search."""
        lo, hi = 0, len(hashes)
        bp = self.bp
        while lo < hi:
            mid = (lo + hi + 1) >> 1
            if bp.get_cached_block(hashes[mid - 1], [0]) is not None:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def wt_snapshot(self, now: float) -> dict:
        wall = time.time()
        self.wt_seq += 1
        snap = self._wt_global(now, wall)
        snap["seq"] = self.wt_seq
        sessions = {}
        bs = self.bs
        for sid, s in self.wt_sess.items():
            idle = None if s.last_fin is None or s.inflight else now - s.last_fin
            if s.inflight == 0 and idle is not None and idle > self.wt_drop_s:
                continue
            live = self.requests.get(s.live) if s.live else None
            st = self.req.get(s.live) if s.live else None
            ws = s.np + s.out
            if live is not None:
                ws = max(ws, live.num_tokens)
            mid = bool(s.inflight) or (bool(s.last_tool) and idle is not None and idle < self.wt_mid_expire)
            sessions[sid] = {
                "first": round(s.first, 3), "turn_t0": round(s.turn_t0, 3), "inflight": s.inflight,
                "queued": bool(st is not None and st.phase in ("Q", "P")),
                "idle_s": None if idle is None else round(idle, 3), "mid": mid,
                "np": s.np, "out": s.out, "ws": ws,
                "res": sum(self.wt_res.get(ci, 0) for ci in s.chains) * bs,
                "prefix": min(self._cached_prefix(s.hashes) * bs, s.np + s.out) if s.hashes else 0,
            }
        snap["sessions"] = sessions
        return snap

    def _close(self) -> None:
        writer = getattr(self, "wt_writer", None)
        if writer is not None and getattr(self, "wt_on", False):
            try:
                writer.put(self.wt_snapshot(time.monotonic()))
            except Exception:
                pass
            writer.close()
            self.wt_writer = None
        super()._close()


class WTScheduler(WTMixin, AsyncScheduler):
    """The status-exporting observer on vLLM's AsyncScheduler (async scheduling, the default)."""


class WTSyncScheduler(WTMixin, Scheduler):
    """The same on the plain Scheduler, for servers run with --no-async-scheduling."""
