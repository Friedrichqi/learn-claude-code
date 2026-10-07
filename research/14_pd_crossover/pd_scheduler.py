"""vLLM scheduler for research/14_pd_crossover: 12's HWScheduler (which keeps everything 11's MUScheduler records)
plus the P/D state of the instance.

    PYTHONPATH=research/14_pd_crossover:research/12_hw_utilization:research/11_multiuser_kv:.deps \\
        MU_SCHED_LOG=/node-local/sched.jsonl PD_ROLE=P PD_ARM=1p1d PD_IDX=0 PD_TP=1 PD_GPUS=0 \\
        vllm serve Qwen/Qwen3-32B ... --scheduler-cls pd_scheduler.PDScheduler

Records added (JSON lines in MU_SCHED_LOG, next to 11's hdr / arr / adm / step / tok1 / fin / pool and 12's hw):
  pdhdr  once: role (C / P / D), arm, idx, tp, gpus, connector class, kv_role, kv_load_failure_policy
  pdh    after every pool walk (MU_POOL_WALK_S, 2 s):
           held, held_blk   P: finished requests whose blocks stay pinned until the decode side has read them
                            (vLLM keeps them in self.requests until finished_sending), and their blocks
           rkv, rkv_blk     D: requests waiting for remote KV (WAITING_FOR_REMOTE_KVS) and their blocks
           wait, run        queue lengths (waiting + skipped_waiting; running)
D's KV load per request is already in 11's records (adm.load = time spent waiting for the load, adm.he = tokens
loaded from the remote instance). Any exception switches the P/D records off for good (fail open), as in 12.
"""
from __future__ import annotations

import os
import time
import traceback

import hw_scheduler  # research/12_hw_utilization (on PYTHONPATH)


class PDScheduler(hw_scheduler.HWScheduler):
    _pd_on = True
    _pd_hdr = False

    def _pool_walk(self, now: float) -> None:
        super()._pool_walk(now)
        if not self._pd_on:
            return
        try:
            if not self._pd_hdr:
                self._pd_hdr = True
                self._emit(self._pd_header())
            self._emit(self._pd_record(now))
        except Exception as exc:                      # never disturb serving
            self._pd_on = False
            self._emit({"k": "err", "t": time.monotonic(), "where": "pd", "exc": repr(exc),
                        "tb": traceback.format_exc()[-2000:]})

    def _pd_header(self) -> dict:
        ktc = getattr(self.vllm_config, "kv_transfer_config", None)
        return {"k": "pdhdr", "t": time.monotonic(), "wall": time.time(),
                "role": os.environ.get("PD_ROLE", "C"), "arm": os.environ.get("PD_ARM"),
                "idx": int(os.environ.get("PD_IDX", "0")), "tp": int(os.environ.get("PD_TP", "1")),
                "gpus": os.environ.get("PD_GPUS"),
                "connector": type(self.connector).__name__ if self.connector else None,
                "kv_role": getattr(ktc, "kv_role", None) if ktc else None,
                "fail_policy": getattr(ktc, "kv_load_failure_policy", None) if ktc else None,
                "extra": dict(getattr(ktc, "kv_connector_extra_config", {}) or {}) if ktc else None}

    def _blocks_of(self, request_id: str) -> int:
        try:
            ids = self.kv_cache_manager.get_block_ids(request_id)
            return sum(len(g) for g in ids)
        except Exception:
            return 0

    def _pd_record(self, now: float) -> dict:
        from vllm.v1.request import RequestStatus

        held = held_blk = 0
        for rid, req in self.requests.items():
            if req.is_finished():
                held += 1
                held_blk += self._blocks_of(rid)
        rkv = rkv_blk = 0
        for queue in (self.waiting, getattr(self, "skipped_waiting", ())):
            for req in queue:
                if req.status == RequestStatus.WAITING_FOR_REMOTE_KVS:
                    rkv += 1
                    rkv_blk += self._blocks_of(req.request_id)
        nwait = len(self.waiting) + len(getattr(self, "skipped_waiting", ()))
        return {"k": "pdh", "t": now, "held": held, "held_blk": held_blk, "rkv": rkv, "rkv_blk": rkv_blk,
                "wait": nwait, "run": len(self.running)}
