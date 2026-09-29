#!/usr/bin/env python3
"""vLLM KV connector for the RoPE-shift experiment: load a spliced, re-rotated prefix KV and save
computed KV, driven per request by a JSON plan in `kv_transfer_params`.

Loaded by vLLM from its own import path (`kv_connector_module_path="splice_connector"`), so this
module must stay importable on its own (no research imports). The arms driver (`arms.py`) puts
`research/10_rope_shift` on PYTHONPATH before it creates the engine.

Per-request plan, passed as `SamplingParams(extra_args={"kv_transfer_params": {"splice": {...}}})`:

    load : {"n": N, "segs": [[key, src_lo, src_hi, dst_lo, delta, rotate], ...]}
           Prompt positions [0, N) are written into the request's blocks before its first forward,
           assembled from store entries. A segment copies store positions [src_lo, src_hi) to
           prompt positions [dst_lo, dst_lo + len), rotating the positional key part by `delta`
           when `rotate` is true. Segments must tile [0, N). vLLM computes positions N.. itself.
    save : {"key": K, "lo": a, "hi": b[, "layers": [l, ...]]}
           After each forward, the KV of prompt positions in [a, b) computed in that step is copied
           into store entry K (positions a..b), all layers or only the listed ones.
    override : {"key": K, "segs": [[pos_lo, pos_hi, src_lo, delta], ...], "from_layer": f}
           Selective recompute in one forward (CacheBlend / EPIC emulation): prompt positions
           [pos_lo, pos_hi) are computed like any other, but at every layer >= f their K/V is
           replaced, before the layer writes the paged cache and attends, by the rows of store
           entry K at positions src_lo.. re-rotated by delta. Positions not listed keep their
           freshly computed KV, so they are the recomputed ones; layers < f are recomputed for all.

Store entries live in worker memory (GPU by default), one per key, holding the raw cache rows of
every attention layer: GQA rows are [H, 2D] (K then V), MLA rows are [1, kv_lora_rank + rope_dim]
(latent then k_pe). Everything is worker-local, so tensor parallelism works: each rank stores and
rotates its own KV heads (GQA) or its replica of the latent cache (MLA). The driver frees entries
between waves with `LLM.collective_rpc(splice_free, args=(keys,))`.

RoPE re-rotation uses R(m + d) = R(d) R(m) on post-RoPE keys, computed in fp32:
  GQA (Qwen3): NeoX halves over the first rotary_dim dims of K (rotary_dim = head_dim here).
  MLA (GLM-4.7-Flash, DeepSeek-V2 attention): GPT-J interleaved pairs over the trailing k_pe dims
  (vLLM builds that rotary with is_neox_style=False); the latent is position-free and copied as is.
"""
from __future__ import annotations

import functools
import math
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
    key: str
    lo: int          # full save range of the entry
    hi: int
    a: int           # positions computed in this step, a..b
    b: int
    blocks: list[int]
    layers: list[int] | None = None   # store only these layers (e.g. CacheBlend's check layer)
    capacity: int | None = None       # compact pool: rows only for the positions saved into it


@dataclass
class OverrideOp:
    """Positions computed in this step whose freshly written KV is replaced, layer by layer from
    `from_layer` on, by re-rotated rows of store entry `key` before attention reads the cache."""
    req_id: str
    key: str
    segs: list[list]      # [[pos_lo, pos_hi, src_lo, delta], ...] in prompt positions
    from_layer: int
    a: int                # positions computed in this step, a..b
    b: int
    blocks: list[int]
    cache: dict = field(default_factory=dict)


@dataclass
class SpliceMetadata(KVConnectorMetadata):
    loads: list[LoadOp] = field(default_factory=list)
    saves: list[SaveOp] = field(default_factory=list)
    overrides: list[OverrideOp] = field(default_factory=list)
    cache: dict = field(default_factory=dict)       # worker-side, per step


# ------------------------------------------------------------------------------------------------
# scheduler side
# ------------------------------------------------------------------------------------------------

class _SchedulerSide:
    def __init__(self, vllm_config: "VllmConfig"):
        self.block_size = vllm_config.cache_config.block_size
        self.reqs: dict[str, dict] = {}
        self.pending_loads: dict[str, LoadOp] = {}

    def get_num_new_matched_tokens(self, request: "Request", num_computed_tokens: int):
        spec = _spec(request)
        if not spec or "load" not in spec:
            return 0, False
        n = int(spec["load"]["n"])
        if n >= request.num_tokens:
            raise ValueError(f"splice load n={n} must leave at least one prompt token to compute "
                             f"(request has {request.num_tokens})")
        return max(0, n - num_computed_tokens), False

    def update_state_after_alloc(self, request: "Request", blocks: "KVCacheBlocks",
                                 num_external_tokens: int):
        spec = _spec(request)
        if not spec:
            return
        state = self.reqs.setdefault(request.request_id, {"spec": spec})
        state["blocks"] = list(blocks.get_block_ids()[0])
        if num_external_tokens > 0:
            # the scheduler updates request.num_computed_tokens only after this call; the external
            # tokens are always the tail of the plan's [0, n) above any local prefix-cache hit
            hi = int(spec["load"]["n"])
            lo = hi - num_external_tokens
            self.pending_loads[request.request_id] = LoadOp(
                request.request_id, list(state["blocks"]), lo, hi, spec["load"]["segs"])
            state["n_loaded"] = hi

    def _maybe_save(self, meta: SpliceMetadata, req_id: str, state: dict, start: int, count: int):
        save = state["spec"].get("save")
        if not save or count <= 0:
            return
        lo, hi = int(save["lo"]), int(save["hi"])
        a, b = max(lo, start), min(hi, start + count)
        if a < b:
            meta.saves.append(SaveOp(req_id, save["key"], lo, hi, a, b, list(state["blocks"]),
                                     save.get("layers"), save.get("capacity")))

    def _maybe_override(self, meta: SpliceMetadata, req_id: str, state: dict, start: int, count: int):
        spec = state["spec"].get("override")
        if not spec or count <= 0:
            return
        lo = min(seg[0] for seg in spec["segs"]) if spec["segs"] else 0
        hi = max(seg[1] for seg in spec["segs"]) if spec["segs"] else 0
        a, b = max(lo, start), min(hi, start + count)
        if a < b:
            meta.overrides.append(OverrideOp(req_id, spec["key"], spec["segs"], int(spec.get("from_layer", 0)),
                                             start, start + count, list(state["blocks"])))

    def build_connector_meta(self, scheduler_output: "SchedulerOutput") -> SpliceMetadata:
        meta = SpliceMetadata()
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
            self._maybe_save(meta, new.req_id, state, start, scheduled.get(new.req_id, 0))
            self._maybe_override(meta, new.req_id, state, start, scheduled.get(new.req_id, 0))
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
            load = self.pending_loads.pop(req_id, None)     # resumed after preemption: reload
            if load is not None:
                load.blocks = list(state["blocks"])
                meta.loads.append(load)
            self._maybe_save(meta, req_id, state, cached.num_computed_tokens[i],
                             scheduled.get(req_id, 0))
            self._maybe_override(meta, req_id, state, cached.num_computed_tokens[i],
                                 scheduled.get(req_id, 0))
        if self.pending_loads:
            # a load that was allocated but not scheduled cannot be dropped silently
            raise RuntimeError(f"splice loads not emitted: {sorted(self.pending_loads)}")
        return meta

    def request_finished(self, request: "Request"):
        self.reqs.pop(request.request_id, None)
        self.pending_loads.pop(request.request_id, None)


# ------------------------------------------------------------------------------------------------
# worker side
# ------------------------------------------------------------------------------------------------

@dataclass
class _Entry:
    lo: int
    hi: int
    data: torch.Tensor          # [L or len(layers), hi - lo (or capacity), H, C]
    layers: list[int] | None = None
    pos_map: torch.Tensor | None = None   # compact pool: position - lo -> row, -1 if never saved
    cursor: int = 0


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
    STORE: dict[str, _Entry] = {}

    def __init__(self, vllm_config: "VllmConfig"):
        cfg = vllm_config.model_config.hf_config
        cfg = getattr(cfg, "text_config", None) or cfg
        extra = vllm_config.kv_transfer_config.kv_connector_extra_config or {}
        self.sched_block_size = vllm_config.cache_config.block_size
        self.is_mla = bool(getattr(cfg, "kv_lora_rank", None))
        self.theta = float(extra.get("rope_theta") or _rope_theta(cfg))
        if self.is_mla:
            self.rope_dim = int(cfg.qk_rope_head_dim)
            self.kv_lora_rank = int(cfg.kv_lora_rank)
            self.style = extra.get("rope_style", "gptj")
        else:
            head_dim = getattr(cfg, "head_dim", None) or cfg.hidden_size // cfg.num_attention_heads
            params = getattr(cfg, "rope_parameters", None) or {}
            factor = (params.get("partial_rotary_factor") if isinstance(params, dict) else None) \
                or getattr(cfg, "partial_rotary_factor", None) or 1.0
            self.head_dim = int(head_dim)
            self.rope_dim = int(self.head_dim * float(factor))
            self.style = extra.get("rope_style", "neox")
        self.store_device = extra.get("store_device", "cuda")
        self.gpu_util = float(vllm_config.cache_config.gpu_memory_utilization)
        self.views: list[torch.Tensor] = []
        self.names: list[str] = []
        self.stats = {"loads": 0, "loaded_tokens": 0, "rotated_tokens": 0, "saves": 0,
                      "saved_tokens": 0, "overrides": 0, "overridden_tokens": 0}
        self.connector = None
        # latency runs only (latency.py): CUDA events around connector loads and at every attention
        # layer's entry, read back with splice_timing; off by default
        self.timing_on = False
        self.timing: dict = {"loads": [], "forwards": []}

    # -- registration ---------------------------------------------------------------------------
    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        self.names, self.views = [], []
        seen = set()

        def layer_number(item):
            match = re.search(r"layers\.(\d+)\.", item[0])
            return int(match.group(1)) if match else 1 << 30

        for name, tensor in sorted(kv_caches.items(), key=layer_number):
            if not isinstance(tensor, torch.Tensor):
                raise TypeError(f"layer {name}: non-tensor KV cache ({type(tensor).__name__}); "
                                "hybrid models are out of scope for this connector")
            if tensor.data_ptr() in seen:         # shared-KV layers alias another layer's cache
                continue
            seen.add(tensor.data_ptr())
            view = tensor.unsqueeze(1) if tensor.dim() == 3 else tensor
            if view.dim() != 4:
                raise ValueError(f"layer {name}: unexpected KV view shape {tuple(tensor.shape)}")
            self.names.append(name)
            self.views.append(view)
        self.index_of = {name: i for i, name in enumerate(self.names)}
        first = self.views[0]
        self.num_heads, self.kernel_block_size, self.row = first.shape[1], first.shape[2], first.shape[3]
        self.dtype, self.device = first.dtype, first.device
        if self.sched_block_size % self.kernel_block_size:
            raise ValueError(f"block size {self.sched_block_size} not a multiple of kernel block "
                             f"size {self.kernel_block_size}")
        self.ratio = self.sched_block_size // self.kernel_block_size
        if self.is_mla:
            if self.row != self.kv_lora_rank + self.rope_dim:
                raise ValueError(f"MLA row {self.row} != kv_lora_rank {self.kv_lora_rank} + "
                                 f"rope {self.rope_dim}")
            self.pos_lo, self.pos_hi = self.kv_lora_rank, self.kv_lora_rank + self.rope_dim
        else:
            if self.row != 2 * self.head_dim:
                raise ValueError(f"GQA row {self.row} != 2 x head_dim {self.head_dim}")
            self.pos_lo, self.pos_hi = 0, self.rope_dim
        half = self.rope_dim // 2
        self.inv_freq = 1.0 / (self.theta ** (torch.arange(0, half, dtype=torch.float64,
                                                            device=self.device) * 2 / self.rope_dim))
        logger.info("SpliceConnector: %d layers, view %s, rotate dims [%d:%d) %s theta=%g",
                    len(self.views), tuple(first.shape), self.pos_lo, self.pos_hi, self.style, self.theta)

    # -- helpers --------------------------------------------------------------------------------
    def _slots(self, blocks: list[int], positions: torch.Tensor):
        table = torch.tensor(blocks, dtype=torch.long, device=self.device)
        manager_block = table[positions // self.sched_block_size]
        within = positions % self.sched_block_size
        kernel_block = manager_block * self.ratio + within // self.kernel_block_size
        return kernel_block, within % self.kernel_block_size

    def cos_sin(self, deltas: torch.Tensor):
        """Per-position rotation angles delta * inv_freq -> (cos, sin), each [n, rope_dim/2], fp32."""
        angle = deltas.to(torch.float64).unsqueeze(-1) * self.inv_freq.unsqueeze(0)
        return torch.cos(angle).to(torch.float32), torch.sin(angle).to(torch.float32)

    def rotate_rows(self, rows: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
        """rows [n, H, row]; cos/sin [n, half]. Returns a copy with the positional key part of every
        row rotated by its own delta (R(m + d) = R(d) R(m) on post-RoPE keys, computed in fp32)."""
        out = rows.clone()
        k = out[..., self.pos_lo:self.pos_hi].float()
        c, s = cos.unsqueeze(1), sin.unsqueeze(1)             # broadcast over heads
        if self.style == "neox":
            half = self.rope_dim // 2
            x1, x2 = k[..., :half], k[..., half:]
            k = torch.cat((x1 * c - x2 * s, x2 * c + x1 * s), dim=-1)
        elif self.style == "gptj":
            x1, x2 = k[..., 0::2], k[..., 1::2]
            k = torch.stack((x1 * c - x2 * s, x2 * c + x1 * s), dim=-1).flatten(-2)
        else:
            raise ValueError(self.style)
        out[..., self.pos_lo:self.pos_hi] = k.to(out.dtype)
        return out

    def rotate(self, rows: torch.Tensor, delta: int) -> torch.Tensor:
        """rows [n, H, row] rotated by one delta (selftests)."""
        if delta == 0:
            return rows.clone()
        cos, sin = self.cos_sin(torch.full((rows.shape[0],), int(delta), device=rows.device))
        return self.rotate_rows(rows, cos, sin)

    # -- load -----------------------------------------------------------------------------------
    def _groups(self, op: LoadOp) -> list[tuple]:
        """Resolve segments to one gather per store entry: (entry, src index, dst index, cos, sin),
        cos/sin None when nothing in the group rotates. Segments must tile [lo, hi) exactly."""
        per_entry: dict[str, list] = {}
        covered = 0
        for key, s_lo, s_hi, d_lo, delta, rotate in op.segs:
            s_lo, s_hi, d_lo, delta = int(s_lo), int(s_hi), int(d_lo), int(delta)
            d_hi = d_lo + (s_hi - s_lo)
            a, b = max(d_lo, op.lo), min(d_hi, op.hi)     # clip to the positions loaded now
            if a >= b:
                continue
            entry = self.STORE.get(key)
            if entry is None:
                raise KeyError(f"splice store has no entry {key!r}")
            if entry.layers is not None:
                raise ValueError(f"{key} holds only layers {entry.layers}; it cannot be loaded")
            src_a = s_lo + (a - d_lo)
            if src_a < entry.lo or src_a + (b - a) > entry.hi:
                raise IndexError(f"{key}: positions [{src_a},{src_a + b - a}) outside entry "
                                 f"[{entry.lo},{entry.hi})")
            per_entry.setdefault(key, [entry, [], [], []])
            group = per_entry[key]
            group[1].append((src_a - entry.lo, b - a))
            group[2].append((a - op.lo, b - a))
            group[3].append((delta if rotate else 0, b - a))
            covered += b - a
        if covered != op.hi - op.lo:
            raise ValueError(f"splice segments cover {covered} of {op.hi - op.lo} positions for {op.req_id}")
        groups = []
        for entry, srcs, dsts, deltas in per_entry.values():
            src = torch.cat([torch.arange(o, o + n, dtype=torch.long) for o, n in srcs]).to(self.device)
            if entry.pos_map is not None:
                src = entry.pos_map[src]
                if bool((src < 0).any()):
                    raise KeyError(f"compact pool is missing {int((src < 0).sum())} requested positions")
            dst = torch.cat([torch.arange(o, o + n, dtype=torch.long) for o, n in dsts]).to(self.device)
            if any(d for d, _ in deltas):
                dvec = torch.cat([torch.full((n,), d, dtype=torch.long) for d, n in deltas]).to(self.device)
                cos, sin = self.cos_sin(dvec)
                self.stats["rotated_tokens"] += int((dvec != 0).sum())
            else:
                cos = sin = None
            groups.append((entry, src, dst, cos, sin))
        return groups

    def start_load(self, meta: SpliceMetadata):
        if self.timing_on:
            t0 = torch.cuda.Event(enable_timing=True)
            t0.record()
        for op in meta.loads:
            groups = self._groups(op)
            positions = torch.arange(op.lo, op.hi, dtype=torch.long, device=self.device)
            blk, off = self._slots(op.blocks, positions)
            # layer by layer: peak memory is one layer's rows, not the whole prefix; one gather per
            # store entry, however many segments the plan has
            for layer, view in enumerate(self.views):
                out = torch.empty((op.hi - op.lo, self.num_heads, self.row), dtype=self.dtype,
                                  device=self.device)
                for entry, src, dst, cos, sin in groups:
                    rows = entry.data[layer].index_select(0, src.to(entry.data.device)).to(self.device)
                    out[dst] = self.rotate_rows(rows, cos, sin) if cos is not None else rows
                view[blk, :, off, :] = out
            self.stats["loads"] += 1
            self.stats["loaded_tokens"] += op.hi - op.lo
        if self.timing_on:
            t1 = torch.cuda.Event(enable_timing=True)
            t1.record()
            self.timing["loads"].append((t0, t1))
            self.timing["pending_start"] = t1     # the next forward starts where the load ends

    # -- save -----------------------------------------------------------------------------------
    def save(self, meta: SpliceMetadata):
        for op in meta.saves:
            layers = list(op.layers) if op.layers else None
            entry = self.STORE.get(op.key)
            if entry is None or entry.lo != op.lo or entry.hi != op.hi or entry.layers != layers:
                rows = op.capacity if op.capacity else op.hi - op.lo
                entry = _Entry(op.lo, op.hi, torch.empty(
                    (len(layers) if layers else len(self.views), rows, self.num_heads, self.row),
                    dtype=self.dtype, device=self.device if self.store_device == "cuda" else "cpu"),
                    layers)
                if op.capacity:
                    entry.pos_map = torch.full((op.hi - op.lo,), -1, dtype=torch.long, device=self.device)
                self.STORE[op.key] = entry
            positions = torch.arange(op.a, op.b, dtype=torch.long, device=self.device)
            blk, off = self._slots(op.blocks, positions)
            if entry.pos_map is not None:            # append rows, remember where each position went
                existing = entry.pos_map[op.a - op.lo: op.b - op.lo]
                if bool((existing >= 0).all()):
                    rows = existing
                else:
                    n = op.b - op.a
                    if entry.cursor + n > entry.data.shape[1]:
                        raise ValueError(f"compact pool {op.key} over capacity ({entry.cursor}+{n} > "
                                         f"{entry.data.shape[1]})")
                    rows = torch.arange(entry.cursor, entry.cursor + n, dtype=torch.long, device=self.device)
                    entry.pos_map[op.a - op.lo: op.b - op.lo] = rows
                    entry.cursor += n
            else:
                rows = torch.arange(op.a - op.lo, op.b - op.lo, dtype=torch.long, device=self.device)
            for slot, layer in enumerate(layers if layers else range(len(self.views))):
                entry.data[slot].index_copy_(
                    0, rows.to(entry.data.device), self.views[layer][blk, :, off, :].to(entry.data.device))
            self.stats["saves"] += 1
            self.stats["saved_tokens"] += op.b - op.a

    # -- override: selective recompute in one forward ----------------------------------------------
    def install_hooks(self, connector, vllm_config: "VllmConfig"):
        """A forward pre-hook on every attention module replaces, in place, the rows of the K/V it
        is about to receive (GQA key/value, MLA kv_c_normed/k_pe; all post-RoPE) for positions under
        an override. The module then writes the replaced rows into the paged cache and attends with
        them, whichever way its kernel reads the current chunk: FlashAttention reads it back from the
        cache, vLLM's MLA prefill uses the in-flight latents, so overwriting the cache alone would
        miss the MLA case."""
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

    def _slot_lookup(self, meta: SpliceMetadata, layer_name: str) -> torch.Tensor:
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

    def _override_tensors(self, meta: SpliceMetadata, op: OverrideOp, layer_name: str) -> dict:
        c = op.cache
        if "n" in c:
            return c
        entry = self.STORE.get(op.key)
        if entry is None:
            raise KeyError(f"splice store has no entry {op.key!r}")
        if entry.layers is not None or entry.pos_map is not None:
            raise ValueError(f"override source {op.key} must be a dense all-layer entry")
        pos, src, dlt = [], [], []
        for pos_lo, pos_hi, src_lo, delta in op.segs:
            pos_lo, pos_hi, src_lo, delta = int(pos_lo), int(pos_hi), int(src_lo), int(delta)
            x, y = max(pos_lo, op.a), min(pos_hi, op.b)
            if x >= y:
                continue
            s_x = src_lo + (x - pos_lo)
            if s_x < entry.lo or s_x + (y - x) > entry.hi:
                raise IndexError(f"{op.key}: positions [{s_x},{s_x + y - x}) outside entry "
                                 f"[{entry.lo},{entry.hi})")
            pos.append(torch.arange(x, y, dtype=torch.long))
            src.append(torch.arange(s_x - entry.lo, s_x - entry.lo + (y - x), dtype=torch.long))
            dlt.append(torch.full((y - x,), delta, dtype=torch.long))
        if not pos:
            c["n"] = 0
            return c
        p = torch.cat(pos).to(self.device)
        blk, off = self._slots(op.blocks, p)
        rows = self._slot_lookup(meta, layer_name)[blk * self.kernel_block_size + off]
        if bool((rows < 0).any()):
            raise RuntimeError(f"override: {int((rows < 0).sum())} positions of {op.req_id} are not "
                               "in this step's batch")
        cos, sin = self.cos_sin(torch.cat(dlt).to(self.device))
        c.update(entry=entry, src=torch.cat(src).to(entry.data.device), rows=rows, cos=cos, sin=sin,
                 n=int(p.numel()))
        self.stats["overrides"] += 1
        self.stats["overridden_tokens"] += int(p.numel())
        return c

    def _pre_hook(self, layer: int, layer_name: str, module, args, kwargs):
        if self.timing_on:
            ev = torch.cuda.Event(enable_timing=True)
            ev.record()
            if layer == 0:
                self.timing["forwards"].append({"start": self.timing.pop("pending_start", None),
                                                "layers": [ev]})
            elif self.timing["forwards"]:
                self.timing["forwards"][-1]["layers"].append(ev)
        conn = self.connector
        if conn is None or not conn.has_connector_metadata():
            return None
        meta = conn._get_connector_metadata()
        if not isinstance(meta, SpliceMetadata) or not meta.overrides:
            return None
        ops = [op for op in meta.overrides if layer >= op.from_layer]
        if not ops:
            return None
        names = ("q", "kv_c_normed", "k_pe") if self.is_mla else ("query", "key", "value")
        first = args[1] if len(args) > 1 else kwargs[names[1]]
        second = args[2] if len(args) > 2 else kwargs[names[2]]
        for op in ops:
            t = self._override_tensors(meta, op, layer_name)
            if not t["n"]:
                continue
            new = t["entry"].data[layer].index_select(0, t["src"]).to(self.device)
            new = self.rotate_rows(new, t["cos"], t["sin"])            # [n, H, row]
            n, idx = t["n"], t["rows"]
            if self.is_mla:                                               # latent | k_pe
                first[idx] = new[:, 0, :self.kv_lora_rank].to(first.dtype)
                second[idx] = new[:, 0, self.kv_lora_rank:].reshape((n,) + tuple(second.shape[1:])).to(second.dtype)
            else:                                                         # K | V per head
                d = self.head_dim
                first[idx] = new[..., :d].reshape((n,) + tuple(first.shape[1:])).to(first.dtype)
                second[idx] = new[..., d:].reshape((n,) + tuple(second.shape[1:])).to(second.dtype)
        return None

    def free(self, keys: list[str] | None) -> int:
        if keys is None:
            n = len(self.STORE)
            self.STORE.clear()
        else:
            n = sum(1 for key in keys if self.STORE.pop(key, None) is not None)
        # store entries come and go in every size; hand their blocks back so the next entries and
        # vLLM's activations do not fight over a fragmented cache
        if n and torch.cuda.is_available():
            torch.cuda.empty_cache()
        return n


# ------------------------------------------------------------------------------------------------
# the connector
# ------------------------------------------------------------------------------------------------

class SpliceConnector(KVConnectorBase_V1):
    def __init__(self, vllm_config: "VllmConfig", role: KVConnectorRole,
                 kv_cache_config: "KVCacheConfig | None" = None):
        super().__init__(vllm_config, role, kv_cache_config)
        self.sched = _SchedulerSide(vllm_config) if role == KVConnectorRole.SCHEDULER else None
        self.worker = _WorkerSide(vllm_config) if role == KVConnectorRole.WORKER else None

    # worker side
    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        self.worker.register_kv_caches(kv_caches)
        self.worker.install_hooks(self, self._vllm_config)

    def start_load_kv(self, forward_context: "ForwardContext", **kwargs: Any) -> None:
        meta = self._get_connector_metadata()
        if isinstance(meta, SpliceMetadata) and meta.loads:
            self.worker.start_load(meta)

    def wait_for_layer_load(self, layer_name: str) -> None:
        return None

    def save_kv_layer(self, layer_name: str, kv_layer: torch.Tensor, attn_metadata, **kwargs) -> None:
        return None

    def wait_for_save(self):
        meta = self._get_connector_metadata()
        if isinstance(meta, SpliceMetadata) and meta.saves:
            self.worker.save(meta)

    # scheduler side
    def get_num_new_matched_tokens(self, request: "Request", num_computed_tokens: int):
        return self.sched.get_num_new_matched_tokens(request, num_computed_tokens)

    def update_state_after_alloc(self, request: "Request", blocks: "KVCacheBlocks",
                                 num_external_tokens: int):
        self.sched.update_state_after_alloc(request, blocks, num_external_tokens)

    def build_connector_meta(self, scheduler_output: "SchedulerOutput") -> KVConnectorMetadata:
        return self.sched.build_connector_meta(scheduler_output)

    def request_finished(self, request: "Request", block_ids: list[int]):
        self.sched.request_finished(request)
        return False, None


# ------------------------------------------------------------------------------------------------
# collective_rpc helpers (run inside every worker; `self` is the vLLM worker)
# ------------------------------------------------------------------------------------------------

def _worker_side() -> _WorkerSide:
    from vllm.distributed.kv_transfer import get_kv_transfer_group
    return get_kv_transfer_group().worker


def splice_free(self, keys=None) -> int:
    return _worker_side().free(keys)


def splice_set_timing(self, on: bool) -> bool:
    """Switch the latency instrumentation on or off (and drop anything recorded)."""
    side = _worker_side()
    side.timing_on = bool(on)
    side.timing = {"loads": [], "forwards": []}
    return side.timing_on


def splice_timing(self) -> dict:
    """GPU milliseconds recorded since the last call, then cleared:
      load_ms     every connector load (gather, re-rotation, write into the request's blocks)
      forwards    per forward: early_ms = from the forward's start (the end of its load, else layer
                  0's attention entry) to layer 1's attention entry, i.e. embedding + layer 0 + layer
                  1's norm and QKV projections; layers_ms = layer 0's to the last layer's attention
                  entry"""
    side = _worker_side()
    torch.cuda.synchronize(side.device)
    loads = [round(a.elapsed_time(b), 3) for a, b in side.timing["loads"]]
    forwards = []
    for fw in side.timing["forwards"]:
        evs, start = fw["layers"], fw["start"]
        if len(evs) < 2:
            continue
        forwards.append({"early_ms": round((start or evs[0]).elapsed_time(evs[1]), 3),
                         "layers_ms": round(evs[0].elapsed_time(evs[-1]), 3),
                         "after_load": start is not None})
    side.timing = {"loads": [], "forwards": []}
    return {"load_ms": loads, "forwards": forwards}


def splice_stats(self) -> dict:
    side = _worker_side()
    mem = sum(e.data.numel() * e.data.element_size() for e in side.STORE.values())
    total = torch.cuda.get_device_properties(side.device).total_memory \
        if side.views and torch.cuda.is_available() else 0
    token_bytes = len(side.views) * side.num_heads * side.row * side.views[0].element_size() \
        if side.views else 0
    return {**side.stats, "entries": len(side.STORE), "store_bytes": mem,
            "views": len(side.views), "view_shape": tuple(side.views[0].shape) if side.views else None,
            "pos_dims": [side.pos_lo, side.pos_hi], "style": side.style, "theta": side.theta,
            "is_mla": side.is_mla, "token_bytes": token_bytes, "total_bytes": int(total),
            "device": torch.cuda.get_device_name(side.device) if side.views and torch.cuda.is_available() else None,
            "gpu_util": side.gpu_util,
            "hooks": len(getattr(side, "hooks", []))}


def splice_inspect(self, key: str, layer: int, lo: int, hi: int) -> torch.Tensor:
    """Rows [hi - lo, H, row] of store entry `key` at `layer`, on CPU (selftests)."""
    entry = _worker_side().STORE[key]
    slot = entry.layers.index(layer) if entry.layers is not None else layer
    return entry.data[slot, lo - entry.lo: hi - entry.lo].float().cpu()


def splice_deviation(self, fresh_key: str, old_key: str, layer: int, new_pos: list[int],
                     old_pos: list[int], deltas: list[int]) -> list[float]:
    """CacheBlend's selection signal: for each reused token, the squared L2 distance between its
    freshly computed key at `layer` (from the recompute pass, entry `fresh_key`) and the reused key
    (entry `old_key` at the token's old position, re-rotated by its delta). GQA compares the K half
    of the cache row, as LMCache's blender does (diff_k); MLA compares the whole cache row (latent +
    k_pe), since the latent carries both K and V. Summed over this rank's heads; the caller adds
    ranks up under tensor parallelism."""
    side = _worker_side()
    fresh, old = side.STORE[fresh_key], side.STORE[old_key]
    f_slot = fresh.layers.index(layer) if fresh.layers is not None else layer
    o_slot = old.layers.index(layer) if old.layers is not None else layer
    dev = side.device
    new_idx = torch.tensor(new_pos, dtype=torch.long, device=dev) - fresh.lo
    old_idx = torch.tensor(old_pos, dtype=torch.long, device=dev) - old.lo
    cos, sin = side.cos_sin(torch.tensor(deltas, dtype=torch.long, device=dev))
    reused = side.rotate_rows(old.data[o_slot].index_select(0, old_idx.to(old.data.device)).to(dev), cos, sin)
    recomputed = fresh.data[f_slot].index_select(0, new_idx.to(fresh.data.device)).to(dev)
    width = side.row if side.is_mla else side.head_dim
    diff = (recomputed[..., :width].float() - reused[..., :width].float()) ** 2
    return diff.sum(dim=(1, 2)).cpu().tolist()


def splice_rotate_check(self, key: str, delta: int, layer: int, lo: int, hi: int) -> torch.Tensor:
    """Rows of `key` rotated by delta (selftest T2), on CPU."""
    side = _worker_side()
    entry = side.STORE[key]
    rows = entry.data[layer, lo - entry.lo: hi - entry.lo].to(side.device)
    return side.rotate(rows, delta).float().cpu()
