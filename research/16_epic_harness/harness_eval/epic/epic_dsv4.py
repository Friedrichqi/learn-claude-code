#!/usr/bin/env python3
"""EPIC connector for DeepSeek-V4-Flash, design B, emulated (harness_eval port, plan "EPIC round" stage C: design and
selftests only).

What the model caches, per layer (vLLM 0.31, models/deepseek_v4): a 128-token sliding window of the latent (448 FP8
no-RoPE dims + 64 bf16 dims rotated at the token's position, GPT-J pairs); on 41 of 43 layers pooled entries, one per 4
(C4) or 128 (C128) tokens, a softmax-weighted sum of per-token compressor inputs plus a bias by offset in the window,
rotated at the window start; on the C4 layers FP8 indexer keys, rotated before quantization. Rows keyed to absolute
positions cannot be re-rotated in place: window membership and offsets change unless a shift is a multiple of the ratio,
and indexer keys lose precision. But fused kernels compute all of them from position-free inputs and the current
positions: the latent before RoPE, and the compressor and indexer inputs. All of these are functions of the layer's
attention input, `hidden_states` (after hyper-connection mixing and the attention norm).

The design:
- Store: each computed token's attention input at every layer, [L, S, hidden] bf16 host rows. Every TP rank keeps
  its own copy, because the latent and compressor GEMMs are replicated and every rank computes the same rows.
- Reuse: a reused position gets its stored row substituted before the layer runs (a forward pre-hook on
  model.layers.i.attn). The kernels then redo RoPE, FP8 quantization, pooling and indexer keys from the stored
  inputs, at the new positions. That is shift's re-rotation, done exactly.
- Reused tokens' own queries and outputs change too. At every layer their attention input is substituted again, so
  nothing computed from them reaches another position except through the substituted inputs.
- Pooled windows that straddle an edit mix stale and fresh inputs, as a real implementation's would.
- Saves happen in the same hook. The caches cannot give rows back, because the window keeps only 128 tokens.
- The batch rows of planned positions come from the slot mapping of layer 0's sliding-window cache, using that
  cache group's block ids.
- Refused or skipped:
  - loads are refused (EPIC_NO_LOAD on the server; a hybrid model);
  - the prefix-cache overshoot reserve is not kept, so an overshoot resets the session;
  - shadow probes are off (EPIC_SHADOW_EVERY=0), because epic_compare reads per-head K rows this cache does not
    have. The selftests compare first-token distributions instead.
- The server must run eager (--enforce-eager): vLLM captures CUDA graphs for DSv4 even under compilation mode 0, and
  graphs skip Python hooks.
"""
from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Any

import torch

import epic_connector as ec
from epic_connector import EpicConnectorHMA, EpicMetadata
from vllm.distributed.kv_transfer.kv_connector.v1.base import KVConnectorBase_V1, KVConnectorRole
from vllm.logger import init_logger

if TYPE_CHECKING:
    from vllm.config import VllmConfig
    from vllm.v1.kv_cache_interface import KVCacheConfig

logger = init_logger(__name__)
SWA0 = "model.layers.0.attn.swa_cache"          # its group's slot mapping locates planned positions in the batch


def _swa_group(kv_cache_config) -> tuple[int, Any]:
    for gid, group in enumerate(getattr(kv_cache_config, "kv_cache_groups", None) or []):
        if SWA0 in group.layer_names:
            return gid, group
    raise ValueError(f"no KV cache group holds {SWA0}: not a DeepSeek-V4 model?")


class _SchedulerSideDSV4(ec._SchedulerSide):
    def __init__(self, vllm_config: "VllmConfig", kv_cache_config: "KVCacheConfig"):
        super().__init__(vllm_config, None)
        self.gid, group = _swa_group(kv_cache_config)
        self.hybrid = True                         # loads are computed instead (no DeltaNet-like state to restore)
        self.block_size = group.kv_cache_spec.block_size

    def _apc_save(self, meta, req_id: str, state: dict, start: int):
        # the caches cannot give attention inputs back: no reserve, so a prefix-cache overshoot resets the session
        state["apc_saved"] = 0


class _WorkerSideDSV4:
    def __init__(self, vllm_config: "VllmConfig", kv_cache_config: "KVCacheConfig"):
        cfg = vllm_config.model_config.hf_config
        cfg = getattr(cfg, "text_config", None) or cfg
        extra = vllm_config.kv_transfer_config.kv_connector_extra_config or {}
        self.gid, group = _swa_group(kv_cache_config)
        self.sched_block_size = group.kv_cache_spec.block_size
        self.hidden = int(cfg.hidden_size)
        self.num_layers = int(cfg.num_hidden_layers)
        self.pool_rows = int(os.environ.get("EPIC_POOL_ROWS") or extra.get("pool_rows") or 0)
        self.views: list[torch.Tensor] = []       # epic_stats compatibility: the one SWA view used for slots
        self.names: list[str] = []
        self.hooks: list = []
        self.theta, self.rope_dim, self.head_dim = None, None, self.hidden
        # dx_num / dx_den: squared deviation of the attention inputs the layer computed for reused positions from the
        # stored rows it substitutes, and their squared norm (summed over layers and rows): sqrt(num/den) is the
        # relative deviation D^X. Exact reuse (delta 0, no edit) leaves only numerical noise; a misplaced row would
        # give about 1.4 (two unrelated vectors of the same norm)
        self.stats = {"loads": 0, "loaded_tokens": 0, "saves": 0, "saved_tokens": 0, "overrides": 0,
                      "overridden_tokens": 0, "hook_calls": 0, "load_errors": 0, "dx_num": 0.0, "dx_den": 0.0}
        self.load_error_blocks: set[int] = set()
        self.connector = None

    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        view = kv_caches[SWA0]
        view = view.unsqueeze(1) if view.dim() == 3 else view
        self.views = [view]
        self.kernel_block_size = int(view.shape[2])
        if self.sched_block_size % self.kernel_block_size:
            raise ValueError("SWA block size not a multiple of its kernel block size")
        self.ratio = self.sched_block_size // self.kernel_block_size
        self.device = view.device
        self.names = [f"model.layers.{i}.attn" for i in range(self.num_layers)]
        if self.pool_rows and ec._WorkerSide.POOL is None:
            # [L, S, hidden]: epic_stats reads S from shape[1], as for the per-head store
            ec._WorkerSide.POOL = torch.empty((self.num_layers, self.pool_rows, self.hidden), dtype=torch.bfloat16,
                                              device="cpu", pin_memory=True)
        logger.info("EpicConnectorDSV4: %d attention inputs of %d per row, pool %d rows (%.1f GB pinned), "
                    "SWA view %s", self.num_layers, self.hidden, self.pool_rows,
                    self.num_layers * self.pool_rows * self.hidden * 2 / 1e9, tuple(view.shape))

    def install_hooks(self, connector, vllm_config: "VllmConfig"):
        context = vllm_config.compilation_config.static_forward_context
        for handle in self.hooks:
            handle.remove()
        self.connector, self.hooks = connector, []
        for layer, name in enumerate(self.names):
            module = context.get(name)
            if module is None:
                raise KeyError(f"{name} is not in the static forward context")
            self.hooks.append(module.register_forward_pre_hook(
                functools.partial(self._pre_hook, layer, name), with_kwargs=True))

    # -- batch rows of planned positions ---------------------------------------------------------------
    def _slots(self, blocks: list[int], positions: torch.Tensor):
        table = torch.tensor(blocks, dtype=torch.long, device=self.device)
        manager_block = table[positions // self.sched_block_size]
        within = positions % self.sched_block_size
        kernel_block = manager_block * self.ratio + within // self.kernel_block_size
        return kernel_block * self.kernel_block_size + within % self.kernel_block_size

    def _lookup(self, meta: EpicMetadata) -> torch.Tensor:
        if "lookup" not in meta.cache:
            from vllm.forward_context import get_forward_context
            mapping = get_forward_context().slot_mapping
            if isinstance(mapping, list):
                mapping = mapping[0]
            slots = mapping.get(SWA0) if isinstance(mapping, dict) else mapping
            if slots is None:
                raise RuntimeError(f"no slot mapping for {SWA0} in the forward context")
            slots = slots.to(torch.long)
            lookup = torch.full((self.views[0].shape[0] * self.kernel_block_size,), -1, dtype=torch.long,
                                device=self.device)
            valid = slots >= 0
            lookup[slots[valid]] = torch.arange(slots.numel(), device=self.device)[valid]
            meta.cache["lookup"] = lookup
        return meta.cache["lookup"]

    def _rows(self, meta: EpicMetadata, req_id: str, blocks: list[int], pos: list[int]) -> torch.Tensor:
        p = torch.tensor(pos, dtype=torch.long, device=self.device)
        rows = self._lookup(meta)[self._slots(blocks, p)]
        if bool((rows < 0).any()):
            raise RuntimeError(f"epic dsv4: {int((rows < 0).sum())} positions of {req_id} are not in this step's batch")
        return rows

    def _plan(self, meta: EpicMetadata) -> dict:
        """This step's (rows, pool slots, from_layer) of saves and overrides, computed once for all layers."""
        if "plan" not in meta.cache:
            saves, overs = [], []
            for op in meta.saves:
                if op.pos:
                    saves.append((self._rows(meta, op.req_id, op.blocks, op.pos),
                                  torch.tensor(op.slots, dtype=torch.long)))
            for op in meta.overrides:
                pos, slots = [], []
                for lo, hi, slot_lo, *_ in op.segs:
                    pos.extend(range(int(lo), int(hi)))
                    slots.extend(range(int(slot_lo), int(slot_lo) + int(hi) - int(lo)))
                if pos:
                    overs.append((self._rows(meta, op.req_id, op.blocks, pos), torch.tensor(slots, dtype=torch.long),
                                  int(op.from_layer)))
            meta.cache["plan"] = {"saves": saves, "overs": overs}
        return meta.cache["plan"]

    def _pre_hook(self, layer: int, name: str, module, args, kwargs):
        conn = self.connector
        if conn is None or not conn.has_connector_metadata():
            return None
        meta = conn._get_connector_metadata()
        if not isinstance(meta, EpicMetadata) or not (meta.saves or meta.overrides):
            return None
        in_kwargs = len(args) < 2
        hs = kwargs.get("hidden_states") if in_kwargs else args[1]
        if not isinstance(hs, torch.Tensor) or hs.dim() != 2:
            return None
        plan = self._plan(meta)
        pool = ec._WorkerSide.POOL
        self.stats["hook_calls"] += 1
        for rows, slots in plan["saves"]:
            pool[layer].index_copy_(0, slots, hs.index_select(0, rows).to("cpu", dtype=pool.dtype))
            if layer == 0:
                self.stats["saves"] += 1
                self.stats["saved_tokens"] += int(rows.numel())
        overs = [(rows, slots) for rows, slots, from_layer in plan["overs"] if layer >= from_layer]
        if not overs:
            return None
        hs = hs.clone()
        for rows, slots in overs:
            stored = pool[layer].index_select(0, slots).to(hs.device, dtype=hs.dtype)
            cur = hs.index_select(0, rows).float()
            self.stats["dx_num"] += float(((cur - stored.float()) ** 2).sum())
            self.stats["dx_den"] += float((cur ** 2).sum())
            hs.index_copy_(0, rows, stored)
            self.stats["overrides"] += 1
            self.stats["overridden_tokens"] += int(rows.numel())
        if in_kwargs:
            return args, {**kwargs, "hidden_states": hs}
        return (args[0], hs, *args[2:]), kwargs


class EpicConnectorDSV4(EpicConnectorHMA):
    """`--kv-transfer-config {"kv_connector": "EpicConnectorDSV4", "kv_connector_module_path": "epic_dsv4", ...}`"""

    def __init__(self, vllm_config: "VllmConfig", role: KVConnectorRole,
                 kv_cache_config: "KVCacheConfig | None" = None):
        KVConnectorBase_V1.__init__(self, vllm_config, role, kv_cache_config)
        self.sched = _SchedulerSideDSV4(vllm_config, kv_cache_config) if role == KVConnectorRole.SCHEDULER else None
        self.worker = _WorkerSideDSV4(vllm_config, kv_cache_config) if role == KVConnectorRole.WORKER else None

    def register_kv_caches(self, kv_caches: dict[str, torch.Tensor]):
        self.worker.register_kv_caches(kv_caches)
        self.worker.install_hooks(self, self._vllm_config)

    def start_load_kv(self, forward_context, **kwargs: Any) -> None:
        return None                                # loads are refused on this model

    def wait_for_save(self):
        return None                                # saved in the pre-hooks (synchronous host copies)
