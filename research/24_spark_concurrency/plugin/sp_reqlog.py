"""Topic 24: a vLLM stat-logger plugin that writes exact per-request and per-step records.

vLLM 0.32.0 loads it through the entry point group `vllm.stat_logger_plugins` (`v1/metrics/loggers.py:84`): the
`sp_reqlog-0.1.dist-info/entry_points.txt` next to this file names `SPReqLog`. Put this folder on the server's
PYTHONPATH; nothing is installed. vLLM keeps its own Prometheus logger as well (`loggers.py:1416-1418`).

It runs inside the API-server process. `record()` is called once per batch of engine outputs, in the output handler
coroutine (`v1/engine/async_llm.py:897-903`), so it only formats and buffers lines; files are flushed once a second.
Any error is counted and swallowed: the plugin must never break the server.

Output, in $SP_REQLOG_DIR (the plugin does nothing if it is unset), one file per process and engine:

  requests_<pid>_<engine>.jsonl   one line per finished request (`FinishedRequestStats`, `v1/metrics/stats.py:288`):
      t, w            monotonic and wall-clock ns when the frontend processed the finishing output
      id              request id: "chatcmpl-<X-Request-Id header>" for /v1/messages (`serve/engine/serving.py:133`,
                      `output_processor.py:973`)
      fin             finish reason
      e2e, queue, prefill, decode, infer   seconds (`stats.py:584-603`): arrival->last token, first QUEUED->first
                      SCHEDULED, SCHEDULED->first token, first->last token, SCHEDULED->last token
      tpot            decode / (generated - 1), seconds
      p, c, g         prompt tokens, cached prompt tokens (prefix-cache hit), generated tokens
      pre, maxt       preemptions of this request, its max_tokens
  steps_<pid>_<engine>.jsonl      one line per record() call that carries scheduler stats (one per engine step):
      t, w            as above
      it, cr, ct, gr, gt, ms, dummy   `SchedulerIterationDetails` (needs --enable-logging-iteration-details):
                      iteration index, context (prefill) requests and tokens, generation requests and tokens,
                      the step's elapsed ms in the engine core (`v1/engine/core.py:577-620`)
      run, wait, skip, kv            running, waiting, deferred-waiting requests; KV pool pinned (0-1)
      pq, ph, pr, ppq, pph, ppr      prefix cache deltas: queries, hits, requests; the same for preempted requests
      ng, np                          generated tokens and preemptions in this batch of outputs
      pt                              prompt tokens of requests that prefilled in this batch: [total, computed,
                                      local cache hit, external]
      ttft, itl                       the batch's TTFT samples and inter-token gaps, seconds (rounded to 0.1 ms)
  meta_<pid>_<engine>.json        written once at engine start: model, pool and scheduler settings.
"""
from __future__ import annotations

import json
import os
import time

from vllm.v1.metrics.loggers import StatLoggerBase

FLUSH_S = 1.0


def _r(x, nd=4):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


class SPReqLog(StatLoggerBase):

    def __init__(self, vllm_config, engine_index: int = 0):
        self.vllm_config = vllm_config
        self.engine_index = engine_index
        self.dir = os.environ.get("SP_REQLOG_DIR") or ""
        self.on = bool(self.dir)
        self.errors = 0
        self.n_req = 0
        self.n_step = 0
        self._last_flush = time.monotonic()
        if not self.on:
            return
        os.makedirs(self.dir, exist_ok=True)
        tag = f"{os.getpid()}_{engine_index}"
        self.tag = tag
        self.f_req = open(os.path.join(self.dir, f"requests_{tag}.jsonl"), "a", buffering=1 << 16)
        self.f_step = open(os.path.join(self.dir, f"steps_{tag}.jsonl"), "a", buffering=1 << 16)

    # -- vLLM interface -----------------------------------------------------------------------------------------------
    def record(self, scheduler_stats, iteration_stats, mm_cache_stats=None, engine_idx: int = 0):
        if not self.on:
            return
        try:
            t, w = time.monotonic_ns(), time.time_ns()
            if iteration_stats is not None:
                for f in iteration_stats.finished_requests:
                    self.f_req.write(json.dumps(self._request(f, t, w), separators=(",", ":")) + "\n")
                    self.n_req += 1
            if scheduler_stats is not None or iteration_stats is not None:
                self.f_step.write(json.dumps(self._step(scheduler_stats, iteration_stats, t, w),
                                             separators=(",", ":")) + "\n")
                self.n_step += 1
            if (t / 1e9) - self._last_flush >= FLUSH_S:
                self._flush()
                self._last_flush = t / 1e9
        except Exception:                                       # noqa: BLE001  never break the server
            self.errors += 1

    def log_engine_initialized(self):
        if not self.on:
            return
        try:
            c = self.vllm_config
            cc, sc, mc = c.cache_config, c.scheduler_config, c.model_config
            meta = {"t": time.monotonic_ns(), "w": time.time_ns(), "pid": os.getpid(), "engine": self.engine_index,
                    "model": getattr(mc, "model", None), "max_model_len": getattr(mc, "max_model_len", None),
                    "block_size": getattr(cc, "block_size", None),
                    "num_gpu_blocks": getattr(cc, "num_gpu_blocks", None),
                    "enable_prefix_caching": getattr(cc, "enable_prefix_caching", None),
                    "mamba_cache_mode": getattr(cc, "mamba_cache_mode", None),
                    "mamba_block_size": getattr(cc, "mamba_block_size", None),
                    "kv_cache_memory_bytes": getattr(cc, "kv_cache_memory_bytes", None),
                    "max_num_seqs": getattr(sc, "max_num_seqs", None),
                    "max_num_batched_tokens": getattr(sc, "max_num_batched_tokens", None),
                    "async_scheduling": getattr(sc, "async_scheduling", None)}
            with open(os.path.join(self.dir, f"meta_{self.tag}.json"), "w") as f:
                json.dump(meta, f, indent=1, default=str)
        except Exception:                                       # noqa: BLE001
            self.errors += 1

    def log(self):
        if self.on:
            try:
                self._flush()
            except Exception:                                   # noqa: BLE001
                self.errors += 1

    # -- records ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _request(f, t: int, w: int) -> dict:
        return {"t": t, "w": w, "id": f.request_id, "fin": str(getattr(f.finish_reason, "name", f.finish_reason)),
                "e2e": _r(f.e2e_latency), "queue": _r(f.queued_time), "prefill": _r(f.prefill_time),
                "decode": _r(f.decode_time), "infer": _r(f.inference_time),
                "tpot": _r(f.mean_time_per_output_token, 6),
                "p": f.num_prompt_tokens, "c": f.num_cached_tokens, "g": f.num_generation_tokens,
                "pre": f.num_preemptions, "maxt": f.max_tokens_param}

    @staticmethod
    def _step(s, it, t: int, w: int) -> dict:
        out: dict = {"t": t, "w": w}
        if s is not None:
            d = s.iteration_details
            if d is not None:
                out.update(it=d.iteration_index, cr=d.num_ctx_requests, ct=d.num_ctx_tokens,
                           gr=d.num_generation_requests, gt=d.num_generation_tokens, ms=_r(d.elapsed_ms, 2))
                if d.is_dummy:
                    out["dummy"] = 1
            out.update(run=s.num_running_reqs, wait=s.num_waiting_reqs, skip=s.num_skipped_waiting_reqs,
                       kv=_r(s.kv_cache_usage, 5))
            pc = s.prefix_cache_stats
            if pc is not None:
                out.update(pq=pc.queries, ph=pc.hits, pr=pc.requests, ppq=pc.preempted_queries,
                           pph=pc.preempted_hits, ppr=pc.preempted_requests)
        if it is not None:
            out.update(ng=it.num_generation_tokens, np=it.num_preempted_reqs)
            pts = it.prompt_token_stats
            if pts is not None and pts.total:
                out["pt"] = [pts.total, pts.computed, pts.local_cache_hit, pts.external_kv_transfer]
            if it.time_to_first_tokens_iter:
                out["ttft"] = [round(x, 4) for x in it.time_to_first_tokens_iter]
            if it.inter_token_latencies_iter:
                out["itl"] = [round(x, 4) for x in it.inter_token_latencies_iter]
        return out

    def _flush(self):
        self.f_req.flush()
        self.f_step.flush()
