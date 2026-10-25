#!/usr/bin/env python3
"""vLLM KV connector of the live EPIC server (research/16_epic_harness): executes explicit per-request
plans; all planning happens in epic_server_ext (API-server process) with epic_plan.

Ported from 10_rope_shift/splice_connector.py (rotation, slot mapping, attention pre-hooks), changed for
live serving on vLLM 0.31:

  Store      one pinned host-RAM slot pool shared by all requests, [L, S, H, row] bf16 (row = K|V of one
             head). The planner allocates slots; a slot holds a row exactly as it was computed (post-RoPE
             at its birth position) and is never rewritten while referenced. Placing a row at position p
             rotates it once, by p - birth (R(m + d) = R(d) R(m), fp32), never row-to-row, so a token
             reused across many snips accumulates no bf16 re-rounding drift.
  Plan       kv_transfer_params["splice"] = {
               p0:       the exact prefix; positions vLLM's prefix cache did not serve below p0 are loaded
               load:     [[dst_lo, dst_hi, slot_lo, delta]]  rows of [0, p0) (cache-miss fallback)
               override: [[pos_lo, pos_hi, slot_lo, delta]]  computed positions whose K/V is replaced by
                         the stored row, re-rotated, at every layer >= from_layer (shift / EPIC)
               save:     [[pos_lo, pos_hi, slot_lo]]         computed prompt positions saved to slots
               decode:   {start, slot_lo, n}                 decode positions start.. saved to slot_lo..
               apc_save: [[pos_lo, pos_hi, slot_lo]]         reserve from p0 on: if the prefix cache
                         serves beyond p0, the rows it served there are copied to these slots
             }
             Everything is clipped per scheduler step to the positions that step computes, so chunked
             prefill works (FlashAttention reads earlier chunks' overridden rows back from the cache).
  Overrides  in an attention forward pre-hook, as in topic 10 (needs --enforce-eager: under torch.compile
             the hook would be traced away; R4 counts that overrides fire).
  Fail-open  a load whose slots are unknown raises nothing: the positions are reported through
             get_block_ids_with_load_errors and vLLM recomputes them (the plan log records it).

RPC helpers (collective_rpc; `self` is the worker): epic_stats, epic_rows (selftests), epic_compare
(L1 deviation of an arm's rows against a fresh recompute's), epic_rotate_check.
"""
from __future__ import annotations

import functools
import math
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import torch

from vllm.distributed.kv_transfer.kv_connector.v1.base import (
    KVConnectorBase_V1,
    KVConnectorMetadata,
    KVConnectorRole,
)
from vllm.logger import init_logger

if TYPE_CHECKING:
    from vllm.config import VllmConfig
    from vllm.forward_context import ForwardContext
    from vllm.v1.core.kv_cache_manager import KVCacheBlocks
    from vllm.v1.core.sched.output import SchedulerOutput
    from vllm.v1.kv_cache_interface import KVCacheConfig
    from vllm.v1.request import Request

logger = init_logger(__name__)
MAX_COPY_RUNS = 64            # more runs of consecutive slots than this: one host-side gather instead


def _spec(request) -> dict | None:
    params = getattr(request, "kv_transfer_params", None) or {}
    spec = params.get("splice") if isinstance(params, dict) else None
    return spec or None


@dataclass
class LoadOp:
    req_id: str
    blocks: list[int]
    lo: int
    hi: int
    segs: list[list]


@dataclass
class SaveOp:
    req_id: str
    blocks: list[int]
    pos: list[int]              # positions computed this step that are saved
    slots: list[int]            # their store slots


@dataclass
class OverrideOp:
    req_id: str
    segs: list[list]            # [[pos_lo, pos_hi, slot_lo, delta], ...] clipped to this step
    from_layer: int
    blocks: list[int]
    cache: dict = field(default_factory=dict)


@dataclass
class EpicMetadata(KVConnectorMetadata):
    loads: list[LoadOp] = field(default_factory=list)
    saves: list[SaveOp] = field(default_factory=list)
    overrides: list[OverrideOp] = field(default_factory=list)
    cache: dict = field(default_factory=dict)


def _clip(segs: list[list], a: int, b: int, width: int = 4) -> list[list]:
    out = []
    for seg in segs or []:
        lo, hi = int(seg[0]), int(seg[1])
        x, y = max(lo, a), min(hi, b)
        if x < y:
            rest = [int(v) for v in seg[2:width]]
            if width >= 3:
                rest[0] += x - lo                       # slot_lo moves with the clipped start
            out.append([x, y, *rest])
    return out


# ------------------------------------------------------------------------------------------------
# scheduler side
# ------------------------------------------------------------------------------------------------

class _SchedulerSide:
    def __init__(self, vllm_config: "VllmConfig"):
        self.block_size = vllm_config.cache_config.block_size
        self.reqs: dict[str, dict] = {}
        self.pending_loads: dict[str, LoadOp] = {}
        self.events: list[dict] = []

    def get_num_new_matched_tokens(self, request: "Request", num_computed_tokens: int):
        spec = _spec(request)
        if not spec:
            return 0, False
        # a load must leave at least one prompt token to compute (vLLM asserts num_new_tokens > 0)
        p0 = min(int(spec.get("p0") or 0), request.num_tokens - 1)
        state = self.reqs.setdefault(request.request_id, {"spec": spec})
        state["p0"] = p0
        state["apc_hit"] = int(num_computed_tokens)
        if num_computed_tokens > p0:
            # the prefix cache served more than the planned exact prefix (another cached sequence of
            # this salt matched further): the forward uses that sequence's rows there, so they are
            # copied to the plan's apc_save reserve at the first step (_apc_save)
            state["overshoot"] = int(num_computed_tokens) - p0
        else:
            state.pop("overshoot", None)
        if p0 > num_computed_tokens and spec.get("load"):
            return p0 - num_computed_tokens, False
        return 0, False

    def update_state_after_alloc(self, request: "Request", blocks: "KVCacheBlocks", num_external_tokens: int):
        spec = _spec(request)
        if not spec:
            return
        state = self.reqs.setdefault(request.request_id, {"spec": spec})
        state["blocks"] = list(blocks.get_block_ids()[0])
        if num_external_tokens > 0:
            hi = int(state.get("p0", spec["p0"]))
            lo = hi - num_external_tokens
            self.pending_loads[request.request_id] = LoadOp(request.request_id, list(state["blocks"]),
                                                            lo, hi, spec["load"])
            state["n_loaded"] = hi
            state["loaded"] = num_external_tokens

    def _step_ops(self, meta: EpicMetadata, req_id: str, state: dict, start: int, count: int):
        if count <= 0:
            return
        spec = state["spec"]
        a, b = start, start + count
        blocks = list(state["blocks"])
        over = _clip(spec.get("override"), a, b)
        if over:
            meta.overrides.append(OverrideOp(req_id, over, int(spec.get("from_layer", 0)), blocks))
        pos, slots = [], []
        for lo, hi, slot_lo in _clip(spec.get("save"), a, b, width=3):
            pos.extend(range(lo, hi))
            slots.extend(range(slot_lo, slot_lo + (hi - lo)))
        dec = spec.get("decode")
        if dec:
            d0, n = int(dec["start"]), int(dec["n"])
            for p in range(max(a, d0), min(b, d0 + n)):
                pos.append(p)
                slots.append(int(dec["slot_lo"]) + (p - d0))
        if pos:
            meta.saves.append(SaveOp(req_id, blocks, pos, slots))

    def _apc_save(self, meta: EpicMetadata, req_id: str, state: dict, start: int):
        """Rows the prefix cache served beyond the planned p0 ([p0, start) of a new request) are copied
        from their cached blocks into the plan's apc_save reserve, so the session's next entry holds the
        rows this request actually attended to."""
        p0 = int(state.get("p0", 0))
        if start <= p0:
            return
        pos, slots = [], []
        for lo, hi, slot_lo in _clip(state["spec"].get("apc_save"), p0, start, width=3):
            pos.extend(range(lo, hi))
            slots.extend(range(slot_lo, slot_lo + (hi - lo)))
        state["apc_saved"] = len(pos)
        if pos:
            meta.saves.append(SaveOp(req_id, list(state["blocks"]), pos, slots))

    def build_connector_meta(self, scheduler_output: "SchedulerOutput") -> EpicMetadata:
        meta = EpicMetadata()
        scheduled = scheduler_output.num_scheduled_tokens
        for new in scheduler_output.scheduled_new_reqs:
            state = self.reqs.get(new.req_id)
            if state is None:
                continue
            state["blocks"] = list(new.block_ids[0])
            load = self.pending_loads.pop(new.req_id, None)
            if load is not None:
                load.blocks = list(state["blocks"])
                meta.loads.append(load)
            start = new.num_computed_tokens or state.get("n_loaded", 0)
            self._apc_save(meta, new.req_id, state, start)
            self._step_ops(meta, new.req_id, state, start, scheduled.get(new.req_id, 0))
        cached = scheduler_output.scheduled_cached_reqs
        for i, req_id in enumerate(cached.req_ids):
            state = self.reqs.get(req_id)
            if state is None:
                continue
            new_blocks = cached.new_block_ids[i]
            if new_blocks is not None:
                if req_id in cached.resumed_req_ids:
                    state["blocks"] = list(new_blocks[0])
                else:
                    state["blocks"].extend(new_blocks[0])
            load = self.pending_loads.pop(req_id, None)
            if load is not None:
                load.blocks = list(state["blocks"])
                meta.loads.append(load)
            self._step_ops(meta, req_id, state, cached.num_computed_tokens[i], scheduled.get(req_id, 0))
        if self.pending_loads:
            raise RuntimeError(f"epic loads not emitted: {sorted(self.pending_loads)}")
        return meta

    def request_finished(self, request: "Request") -> dict | None:
        state = self.reqs.pop(request.request_id, None)
        self.pending_loads.pop(request.request_id, None)
        if state is None:
            return None
        return {"epic_server": {"apc_hit": state.get("apc_hit"), "loaded": state.get("loaded", 0),
                                "overshoot": state.get("overshoot", 0), "apc_saved": state.get("apc_saved", 0)}}


# ------------------------------------------------------------------------------------------------
# worker side
# ------------------------------------------------------------------------------------------------

def _rope_theta(cfg) -> float:
    params = getattr(cfg, "rope_parameters", None) or {}
    theta = params.get("rope_theta") if isinstance(params, dict) else None
    theta = theta or getattr(cfg, "rope_theta", None)
    if not theta:
        raise ValueError("model config carries no rope_theta")
    scaling = getattr(cfg, "rope_scaling", None) or {}
    kind = (params.get("rope_type") if isinstance(params, dict) else None) or \
        (scaling.get("rope_type") or scaling.get("type") if isinstance(scaling, dict) else None)
    if kind not in (None, "default"):
        raise NotImplementedError(f"RoPE scaling {kind!r}: rotation by delta not implemented")
    return float(theta)


class _WorkerSide:
    POOL: torch.Tensor | None = None             # [L, S, H, row] pinned host rows

    def __init__(self, vllm_config: "VllmConfig"):
        cfg = vllm_config.model_config.hf_config
        cfg = getattr(cfg, "text_config", None) or cfg
        extra = vllm_config.kv_transfer_config.kv_connector_extra_config or {}
        self.sched_block_size = vllm_config.cache_config.block_size
        if getattr(cfg, "kv_lora_rank", None):
            raise NotImplementedError("MLA models are out of scope for the live EPIC connector")
        self.theta = float(extra.get("rope_theta") or _rope_theta(cfg))
        head_dim = getattr(cfg, "head_dim", None) or cfg.hidden_size // cfg.num_attention_heads
        self.head_dim = int(head_dim)
        self.rope_dim = self.head_dim
        self.style = extra.get("rope_style", "neox")
        self.pool_rows = int(os.environ.get("EPIC_POOL_ROWS") or extra.get("pool_rows") or 0)
        self.pool_device = extra.get("store_device", "cpu")
        self.views: list[torch.Tensor] = []
        self.names: list[str] = []
        self.stats = {"loads": 0, "loaded_tokens": 0, "saves": 0, "saved_tokens": 0, "overrides": 0,
                      "overridden_tokens": 0, "hook_calls": 0, "load_errors": 0}
        self.load_error_blocks: set[int] = set()
        self.connector = None

    # -- registration ---------------------------------------------------------------------------
    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        self.names, self.views = [], []
        seen = set()

        def layer_number(item):
            match = re.search(r"layers\.(\d+)\.", item[0])
            return int(match.group(1)) if match else 1 << 30

        for name, tensor in sorted(kv_caches.items(), key=layer_number):
            if not isinstance(tensor, torch.Tensor):
                raise TypeError(f"layer {name}: non-tensor KV cache ({type(tensor).__name__})")
            if tensor.data_ptr() in seen:
                continue
            seen.add(tensor.data_ptr())
            view = tensor.unsqueeze(1) if tensor.dim() == 3 else tensor
            if view.dim() != 4:
                raise ValueError(f"layer {name}: unexpected KV view shape {tuple(tensor.shape)}")
            self.names.append(name)
            self.views.append(view)
        first = self.views[0]
        self.num_heads, self.kernel_block_size, self.row = first.shape[1], first.shape[2], first.shape[3]
        self.dtype, self.device = first.dtype, first.device
        if self.row != 2 * self.head_dim:
            raise ValueError(f"GQA row {self.row} != 2 x head_dim {self.head_dim}")
        if self.sched_block_size % self.kernel_block_size:
            raise ValueError("scheduler block size not a multiple of the kernel block size")
        self.ratio = self.sched_block_size // self.kernel_block_size
        half = self.rope_dim // 2
        self.inv_freq = 1.0 / (self.theta ** (torch.arange(0, half, dtype=torch.float64,
                                                            device=self.device) * 2 / self.rope_dim))
        if self.pool_rows and _WorkerSide.POOL is None:
            pin = self.pool_device == "cpu"
            shape = (len(self.views), self.pool_rows, self.num_heads, self.row)
            _WorkerSide.POOL = torch.empty(shape, dtype=self.dtype,
                                           device="cpu" if pin else self.device, pin_memory=pin)
        logger.info("EpicConnector: %d layers, view %s, pool %s rows on %s, theta=%g",
                    len(self.views), tuple(first.shape), self.pool_rows, self.pool_device, self.theta)

    # -- helpers --------------------------------------------------------------------------------
    def _slots(self, blocks: list[int], positions: torch.Tensor):
        table = torch.tensor(blocks, dtype=torch.long, device=self.device)
        manager_block = table[positions // self.sched_block_size]
        within = positions % self.sched_block_size
        kernel_block = manager_block * self.ratio + within // self.kernel_block_size
        return kernel_block, within % self.kernel_block_size

    def cos_sin(self, deltas: torch.Tensor):
        angle = deltas.to(torch.float64).unsqueeze(-1) * self.inv_freq.unsqueeze(0)
        return torch.cos(angle).to(torch.float32), torch.sin(angle).to(torch.float32)

    def rotate_rows(self, rows: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        """rows [n, H, row]; the K half of every row rotated by its own delta (NeoX halves, fp32)."""
        out = rows.clone()
        k = out[..., :self.rope_dim].float()
        c, s = cos.unsqueeze(1), sin.unsqueeze(1)
        half = self.rope_dim // 2
        x1, x2 = k[..., :half], k[..., half:]
        out[..., :self.rope_dim] = torch.cat((x1 * c - x2 * s, x2 * c + x1 * s), dim=-1).to(out.dtype)
        return out

    @staticmethod
    def runs(slots: torch.Tensor) -> list[tuple[int, int, int]]:
        """(first index, end index, first slot) of every run of consecutive slots (CPU long tensor)."""
        n = slots.numel()
        if n == 0:
            return []
        brk = (torch.nonzero(slots[1:] - slots[:-1] != 1).flatten() + 1).tolist()
        return [(a, b, int(slots[a])) for a, b in zip([0, *brk], [*brk, n])]

    def gather(self, layer: int, slots: torch.Tensor, runs: list | None = None) -> torch.Tensor:
        """Pool rows of `slots` at `layer`, on the GPU. From the pinned host pool, each run of consecutive
        slots is one asynchronous copy (stream-ordered before any use of the result); a fragmented slot list
        falls back to one host-side gather."""
        pool = _WorkerSide.POOL
        if pool.device.type != "cpu":
            return pool[layer].index_select(0, slots.to(pool.device))
        s = slots.cpu()
        runs = self.runs(s) if runs is None else runs
        if len(runs) > MAX_COPY_RUNS:
            return pool[layer].index_select(0, s).to(self.device)
        out = torch.empty((s.numel(),) + tuple(pool.shape[2:]), dtype=pool.dtype, device=self.device)
        for a, b, lo in runs:
            out[a:b].copy_(pool[layer, lo:lo + (b - a)], non_blocking=True)
        return out

    @staticmethod
    def expand(segs: list[list]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """[[lo, hi, slot_lo, delta]] -> positions, slots, deltas (CPU long tensors)."""
        pos, slots, deltas = [], [], []
        for lo, hi, slot_lo, delta in segs:
            n = hi - lo
            pos.append(torch.arange(lo, hi, dtype=torch.long))
            slots.append(torch.arange(slot_lo, slot_lo + n, dtype=torch.long))
            deltas.append(torch.full((n,), delta, dtype=torch.long))
        if not pos:
            empty = torch.zeros(0, dtype=torch.long)
            return empty, empty, empty
        return torch.cat(pos), torch.cat(slots), torch.cat(deltas)

    # -- load -----------------------------------------------------------------------------------
    def start_load(self, meta: EpicMetadata):
        for op in meta.loads:
            segs = _clip(op.segs, op.lo, op.hi)
            pos, slots, deltas = self.expand(segs)
            if len(pos) != op.hi - op.lo or _WorkerSide.POOL is None or \
                    (len(slots) and int(slots.max()) >= _WorkerSide.POOL.shape[1]):
                blocks = {op.blocks[p // self.sched_block_size] for p in range(op.lo, op.hi)}
                self.load_error_blocks |= blocks
                self.stats["load_errors"] += 1
                continue
            p = pos.to(self.device)
            blk, off = self._slots(op.blocks, p)
            cos, sin = self.cos_sin(deltas.to(self.device))
            runs = self.runs(slots)
            for layer, view in enumerate(self.views):
                rows = self.gather(layer, slots, runs)
                view[blk, :, off, :] = self.rotate_rows(rows, cos, sin)
            self.stats["loads"] += 1
            self.stats["loaded_tokens"] += op.hi - op.lo

    # -- save -----------------------------------------------------------------------------------
    def save(self, meta: EpicMetadata):
        pool = _WorkerSide.POOL
        if pool is None:
            return
        for op in meta.saves:
            p = torch.tensor(op.pos, dtype=torch.long, device=self.device)
            dst = torch.tensor(op.slots, dtype=torch.long)
            if int(dst.max()) >= pool.shape[1]:
                raise IndexError(f"save slot {int(dst.max())} outside the pool ({pool.shape[1]} rows)")
            blk, off = self._slots(op.blocks, p)
            runs = self.runs(dst) if pool.device.type == "cpu" else []
            direct = pool.device.type == "cpu" and len(runs) <= MAX_COPY_RUNS
            for layer, view in enumerate(self.views):        # one layer at a time: low peak memory
                rows = view[blk, :, off, :]
                if direct:                                   # runs of slots: async copies into pinned rows
                    for a, b, lo in runs:
                        pool[layer, lo:lo + (b - a)].copy_(rows[a:b], non_blocking=True)
                elif pool.device.type == "cpu":
                    pool[layer].index_copy_(0, dst, rows.cpu())
                else:
                    pool[layer].index_copy_(0, dst.to(pool.device), rows)
            if direct:
                torch.cuda.current_stream(self.device).synchronize()   # rows are readable once save returns
            self.stats["saves"] += 1
            self.stats["saved_tokens"] += len(op.pos)

    # -- override -------------------------------------------------------------------------------
    def install_hooks(self, connector, vllm_config: "VllmConfig"):
        context = vllm_config.compilation_config.static_forward_context
        for handle in getattr(self, "hooks", []):
            handle.remove()
        self.connector = connector
        self.hooks = []
        for layer, name in enumerate(self.names):
            module = context.get(name)
            if module is None:
                raise KeyError(f"attention layer {name} is not in the static forward context")
            self.hooks.append(module.register_forward_pre_hook(
                functools.partial(self._pre_hook, layer, name), with_kwargs=True))

    def _slot_lookup(self, meta: EpicMetadata, layer_name: str) -> torch.Tensor:
        """Cache slot -> row of this step's flattened token batch (-1 when not computed now)."""
        from vllm.forward_context import get_forward_context
        mapping = get_forward_context().slot_mapping
        if isinstance(mapping, list):
            mapping = mapping[0]
        slots = mapping.get(layer_name) if isinstance(mapping, dict) else mapping
        if slots is None:
            raise RuntimeError(f"override: no slot mapping for {layer_name} in the forward context")
        key = ("lookup", slots.data_ptr())
        if key not in meta.cache:
            slots = slots.to(torch.long)
            lookup = torch.full((self.views[0].shape[0] * self.kernel_block_size,), -1,
                                dtype=torch.long, device=self.device)
            valid = slots >= 0
            lookup[slots[valid]] = torch.arange(slots.numel(), device=self.device)[valid]
            meta.cache[key] = lookup
        return meta.cache[key]

    def _override_tensors(self, meta: EpicMetadata, op: OverrideOp, layer_name: str) -> dict:
        c = op.cache
        if "n" in c:
            return c
        pos, slots, deltas = self.expand(op.segs)
        if not len(pos):
            c["n"] = 0
            return c
        p = pos.to(self.device)
        blk, off = self._slots(op.blocks, p)
        rows = self._slot_lookup(meta, layer_name)[blk * self.kernel_block_size + off]
        if bool((rows < 0).any()):
            raise RuntimeError(f"override: {int((rows < 0).sum())} positions of {op.req_id} are not "
                               "in this step's batch")
        cos, sin = self.cos_sin(deltas.to(self.device))
        c.update(slots=slots, runs=self.runs(slots), rows=rows, cos=cos, sin=sin, n=int(p.numel()))
        self.stats["overrides"] += 1
        self.stats["overridden_tokens"] += int(p.numel())
        return c

    def _pre_hook(self, layer: int, layer_name: str, module, args, kwargs):
        conn = self.connector
        if conn is None or not conn.has_connector_metadata():
            return None
        meta = conn._get_connector_metadata()
        if not isinstance(meta, EpicMetadata) or not meta.overrides:
            return None
        ops = [op for op in meta.overrides if layer >= op.from_layer]
        if not ops:
            return None
        self.stats["hook_calls"] += 1
        key = args[1] if len(args) > 1 else kwargs["key"]
        value = args[2] if len(args) > 2 else kwargs["value"]
        d = self.head_dim
        for op in ops:
            t = self._override_tensors(meta, op, layer_name)
            if not t["n"]:
                continue
            new = self.rotate_rows(self.gather(layer, t["slots"], t["runs"]), t["cos"], t["sin"])  # [n, H, row]
            n, idx = t["n"], t["rows"]
            key[idx] = new[..., :d].reshape((n,) + tuple(key.shape[1:])).to(key.dtype)
            value[idx] = new[..., d:].reshape((n,) + tuple(value.shape[1:])).to(value.dtype)
        return None


# ------------------------------------------------------------------------------------------------
# the connector
# ------------------------------------------------------------------------------------------------

class EpicConnector(KVConnectorBase_V1):
    def __init__(self, vllm_config: "VllmConfig", role: KVConnectorRole,
                 kv_cache_config: "KVCacheConfig | None" = None):
        super().__init__(vllm_config, role, kv_cache_config)
        self.sched = _SchedulerSide(vllm_config) if role == KVConnectorRole.SCHEDULER else None
        self.worker = _WorkerSide(vllm_config) if role == KVConnectorRole.WORKER else None

    # worker side
    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        self.worker.register_kv_caches(kv_caches)
        extra = self._vllm_config.kv_transfer_config.kv_connector_extra_config or {}
        if extra.get("hooks", True):            # E-C times loads only, on a compiled engine: no hooks
            self.worker.install_hooks(self, self._vllm_config)

    def start_load_kv(self, forward_context: "ForwardContext", **kwargs: Any) -> None:
        meta = self._get_connector_metadata()
        if isinstance(meta, EpicMetadata) and meta.loads:
            self.worker.start_load(meta)

    def wait_for_layer_load(self, layer_name: str) -> None:
        return None

    def save_kv_layer(self, layer_name: str, kv_layer: torch.Tensor, attn_metadata, **kwargs) -> None:
        return None

    def wait_for_save(self):
        meta = self._get_connector_metadata()
        if isinstance(meta, EpicMetadata) and meta.saves:
            self.worker.save(meta)

    def get_block_ids_with_load_errors(self) -> set[int]:
        if self.worker is None:
            return set()
        out, self.worker.load_error_blocks = self.worker.load_error_blocks, set()
        return out

    # scheduler side
    def get_num_new_matched_tokens(self, request: "Request", num_computed_tokens: int):
        return self.sched.get_num_new_matched_tokens(request, num_computed_tokens)

    def update_state_after_alloc(self, request: "Request", blocks: "KVCacheBlocks", num_external_tokens: int):
        self.sched.update_state_after_alloc(request, blocks, num_external_tokens)

    def build_connector_meta(self, scheduler_output: "SchedulerOutput") -> KVConnectorMetadata:
        return self.sched.build_connector_meta(scheduler_output)

    def request_finished(self, request: "Request", block_ids: list[int]):
        return False, self.sched.request_finished(request)


# ------------------------------------------------------------------------------------------------
# collective_rpc helpers (run inside the worker; `self` is the vLLM worker)
# ------------------------------------------------------------------------------------------------

def _worker_side() -> _WorkerSide:
    from vllm.distributed.kv_transfer import get_kv_transfer_group
    return get_kv_transfer_group().worker


def epic_stats(self) -> dict:
    side = _worker_side()
    pool = _WorkerSide.POOL
    return {**side.stats, "pool_rows": int(pool.shape[1]) if pool is not None else 0,
            "pool_device": str(pool.device) if pool is not None else None,
            "views": len(side.views), "view_shape": tuple(side.views[0].shape) if side.views else None,
            "hooks": len(getattr(side, "hooks", [])), "theta": side.theta}


def epic_rows(self, layer: int, slots: list[int], deltas: list[int] | None = None) -> torch.Tensor:
    """Pool rows (optionally re-rotated), [n, H, row] float32 on CPU (selftests)."""
    side = _worker_side()
    rows = side.gather(layer, torch.tensor(slots, dtype=torch.long))
    if deltas is not None:
        cos, sin = side.cos_sin(torch.tensor(deltas, dtype=torch.long, device=side.device))
        rows = side.rotate_rows(rows, cos, sin)
    return rows.float().cpu()


def epic_gather_bench(self, slots: list[int], reps: int = 3) -> dict:
    """Time a gather of `slots` at every layer from the host pool: the run-wise pinned copies vs one
    host-side index_select per layer (the previous path); checks they agree bit for bit."""
    import time as _time
    side = _worker_side()
    pool = _WorkerSide.POOL
    s = torch.tensor(slots, dtype=torch.long)
    runs = side.runs(s)
    out = {"rows": len(slots), "runs": len(runs)}
    for name in ("runs", "index_select"):
        best = None
        for _ in range(reps):
            torch.cuda.synchronize()
            t = _time.perf_counter()
            for layer in range(len(side.views)):
                rows = side.gather(layer, s, runs) if name == "runs" else \
                    pool[layer].index_select(0, s).to(side.device)
            torch.cuda.synchronize()
            ms = 1000 * (_time.perf_counter() - t)
            best = ms if best is None else min(best, ms)
        out[f"{name}_ms"] = round(best, 1)
    a = side.gather(7, s, runs)
    b = pool[7].index_select(0, s).to(side.device)
    out["identical"] = bool(torch.equal(a, b))
    return out


def epic_rotate_check(self, slots: list[int], delta: int, layer: int) -> torch.Tensor:
    side = _worker_side()
    rows = side.gather(layer, torch.tensor(slots, dtype=torch.long))
    cos, sin = side.cos_sin(torch.full((len(slots),), int(delta), dtype=torch.long, device=side.device))
    return side.rotate_rows(rows, cos, sin).float().cpu()


def epic_compare(self, arm_slots: list[int], arm_deltas: list[int], fresh_slots: list[int],
                 bins: list[int], n_bins: int, chunk: int = 4096) -> list:
    """L1 deviation of an arm's rows from a fresh recompute's, per layer and bin:
    [L][n_bins] -> [sum |dK|^2, sum |K*|^2, sum |dV|^2, sum |V*|^2, count]. Arm rows are re-rotated
    by their delta (position - birth); fresh rows were computed in place (delta 0)."""
    side = _worker_side()
    d = side.head_dim
    L = len(side.views)
    out = torch.zeros((L, n_bins, 5), dtype=torch.float64)
    a_slots = torch.tensor(arm_slots, dtype=torch.long)
    a_dl = torch.tensor(arm_deltas, dtype=torch.long)
    f_slots = torch.tensor(fresh_slots, dtype=torch.long)
    b = torch.tensor(bins, dtype=torch.long)
    for start in range(0, len(arm_slots), chunk):
        sl = slice(start, start + chunk)
        bb = b[sl].to(side.device)
        cos, sin = side.cos_sin(a_dl[sl].to(side.device))
        a_runs, f_runs = side.runs(a_slots[sl]), side.runs(f_slots[sl])
        for layer in range(L):
            arm = side.rotate_rows(side.gather(layer, a_slots[sl], a_runs), cos, sin).float()
            fresh = side.gather(layer, f_slots[sl], f_runs).float()
            dk = ((arm[..., :d] - fresh[..., :d]) ** 2).sum(dim=(1, 2))
            nk = (fresh[..., :d] ** 2).sum(dim=(1, 2))
            dv = ((arm[..., d:] - fresh[..., d:]) ** 2).sum(dim=(1, 2))
            nv = (fresh[..., d:] ** 2).sum(dim=(1, 2))
            stack = torch.stack([dk, nk, dv, nv, torch.ones_like(dk)], dim=1).double()
            acc = torch.zeros((n_bins, 5), dtype=torch.float64, device=side.device)
            acc.index_add_(0, bb, stack)
            out[layer] += acc.cpu()
    return out.tolist()
