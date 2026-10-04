"""vLLM scheduler for research/12_hw_utilization: 11's MUScheduler plus an HBM record per pool walk.

vLLM builds the scheduler inside its EngineCore process from `--scheduler-cls`, so this module must be
importable there:

    PYTHONPATH=research/12_hw_utilization:research/11_multiuser_kv MU_SCHED_LOG=/node-local/sched.jsonl \\
        vllm serve Qwen/Qwen3-32B ... --scheduler-cls hw_scheduler.HWScheduler

Everything MUScheduler records is kept (per-step schedule / launch / update stamps, the pool walk with
free and cached-free blocks every MU_POOL_WALK_S). After each pool walk this class adds one record

  hw    live (referenced) blocks, running / waiting requests, the KV tokens they logically hold
        (sum of num_computed_tokens, so prefix blocks shared by several requests count once per
        request), the blocks those tokens need without sharing or tail waste, and -- only when the
        model runs in this process (TP=1, UniProcExecutor) -- torch.cuda allocated / reserved bytes and
        the peak allocated since the previous walk (reset each time). CUDA-graph replays allocate from
        a private pool and never show up in the peak; prefill activations do.

The pool record already holds `free` (blocks in the free queue) and `cfree` (free blocks that still
hold a cached prefix). With N = num_gpu_blocks - 1 usable blocks:
  live = N - free (referenced by requests), valid-but-idle = cfree, empty = free - cfree.

With HW_STACK_MS=<ms> (a cell's `server_env`), a daemon thread also samples the engine thread -- the one
that calls schedule() -- every <ms>: its Python stack and the CPU time it used since the previous sample
(its pthread CPU clock). py-spy cannot do this here: the nodes run Yama ptrace_scope 2 (admin-only attach).

  stkdef  id, st: a stack, innermost frame first, "path:line:function" (path from the package root)
  stk     t, dt (since the previous sample; >> <ms> means the engine thread held the GIL), cpu (engine
          thread CPU-seconds in that dt), id

A thread stalled in a C call shows the Python line that made the call. Cost: one sys._current_frames()
per sample, about 0.1 ms of GIL every <ms>.

Every hook fails open: an exception writes an `err` record and turns only this record off.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

try:
    import mu_scheduler
except ImportError:                                   # PYTHONPATH without 11: add it
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "11_multiuser_kv"))
    import mu_scheduler


STACK_DEPTH = 40


class HWScheduler(mu_scheduler.MUScheduler):
    _hw_on = True
    _stk_ms = 0.0
    _stk_thread = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self._on and getattr(self, "sink", None) is not None:
            self._stk_ms = mu_scheduler.env_float("HW_STACK_MS", 0)

    def schedule(self, *args, **kwargs):
        if self._stk_ms and self._stk_thread is None:
            self._stk_thread = threading.Thread(target=self._stack_loop, args=(threading.get_ident(),),
                                                daemon=True, name="hw-stack")
            self._stk_thread.start()
        return super().schedule(*args, **kwargs)

    def _stack_loop(self, ident: int) -> None:
        """Sample the engine thread's stack and CPU clock; batches go straight to the sink's writer queue
        (thread-safe), not through the engine thread's buffer."""
        sink = self.sink
        batch: list[dict] = [{"k": "stkhdr", "t": time.monotonic(), "ms": self._stk_ms, "ident": ident}]
        try:
            clock = time.pthread_getcpuclockid(ident)
            period = self._stk_ms / 1000
            ids: dict[tuple, int] = {}
            t_prev, c_prev = time.monotonic(), time.clock_gettime(clock)
            flushed = t_prev
            while True:
                time.sleep(period)
                frame = sys._current_frames().get(ident)
                try:
                    t, c = time.monotonic(), time.clock_gettime(clock)
                except OSError:                                     # EINVAL: the thread exited after the lookup
                    frame = None
                if frame is None:                                   # engine thread gone: server shutting down
                    break
                key = self._stack_key(frame)
                del frame
                sid = ids.get(key)
                if sid is None:
                    sid = ids[key] = len(ids)
                    batch.append({"k": "stkdef", "id": sid, "st": list(key)})
                batch.append({"k": "stk", "t": t, "dt": round(t - t_prev, 5), "cpu": round(c - c_prev, 5),
                              "id": sid})
                t_prev, c_prev = t, c
                if t - flushed >= 1.0:
                    sink.q.put(batch)
                    batch, flushed = [], t
            sink.q.put(batch)
        except Exception as exc:                                     # never disturb serving
            sink.q.put(batch + [{"k": "err", "t": time.monotonic(), "where": "stack", "exc": repr(exc),
                                 "tb": traceback.format_exc()[-2000:]}])

    @staticmethod
    def _stack_key(frame) -> tuple:
        out = []
        while frame is not None and len(out) < STACK_DEPTH:
            code = frame.f_code
            path = code.co_filename
            for root in ("site-packages/", "research/"):
                i = path.rfind(root)
                if i >= 0:
                    path = path[i + len(root):]
                    break
            out.append(f"{path}:{frame.f_lineno}:{code.co_name}")
            frame = frame.f_back
        return tuple(out)

    def _pool_walk(self, now: float) -> None:
        super()._pool_walk(now)
        if not self._hw_on:
            return
        try:
            self._emit(self._hw_record(now))
        except Exception as exc:                      # never disturb serving
            self._hw_on = False
            self._emit({"k": "err", "t": time.monotonic(), "where": "hw", "exc": repr(exc),
                        "tb": traceback.format_exc()[-2000:]})

    def _hw_record(self, now: float) -> dict:
        free = self.bp.get_num_free_blocks()
        usable = self.bp.num_gpu_blocks - 1
        run_tok = run_need = 0
        for req in self.running:
            n = int(req.num_computed_tokens)
            run_tok += n
            run_need += -(-n // self.block_size)
        rec = {"k": "hw", "t": now, "wall": time.time(), "usable": usable, "free": free,
               "live": usable - free, "run": len(self.running), "wait": len(self.waiting),
               "run_tok": run_tok, "run_need_blk": run_need}
        cuda = self._cuda_stats()
        if cuda:
            rec["cuda"] = cuda
        return rec

    @staticmethod
    def _cuda_stats() -> dict | None:
        """torch.cuda allocator stats, only if this process already owns the CUDA context (TP=1)."""
        try:
            import torch
            if not torch.cuda.is_initialized():
                return None
            out = {"alloc": int(torch.cuda.memory_allocated()), "resv": int(torch.cuda.memory_reserved()),
                   "peak": int(torch.cuda.max_memory_allocated())}
            torch.cuda.reset_peak_memory_stats()
            return out
        except Exception:
            return None
