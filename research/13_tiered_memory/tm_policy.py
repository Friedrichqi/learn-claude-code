"""Session-aware eviction for the CPU (DRAM) tier of vLLM's native KV offload -- research/13_tiered_memory, B1.

Selected out-of-tree through the offload connector's extra config (vLLM's CachePolicyFactory imports it by path):

    --kv-offloading-size 64 --kv-offloading-backend native \
    --kv-transfer-config '{"kv_connector_extra_config": {"eviction_policy": "SessionAwareLRU",
                                                         "cache_policy_module_path": "tm_policy"}}'

It runs inside the EngineCore process, next to tm_scheduler.TMScheduler, which fills REG: the session of every block
hash it has seen and each session's state. Eviction order: blocks of ended sessions, then plain LRU over everything
else, and blocks of sessions that are away (in a tool or thinking: about to come back) only last, the latest
predicted return first. With REG empty (no TMScheduler, no hints) it is exactly vLLM's LRU.
[2026-10-04, S2: the first order (ended -> away -> LRU) evicted the sessions about to return. Together with demotions
at closed-loop turn ends, it cost 3.5 % of prompt tokens in recompute where LRU cost 0 %; fixed to protect away
sessions.]
"""
from __future__ import annotations

from vllm.v1.kv_offload.base import get_offload_block_hash
from vllm.v1.kv_offload.cpu.policies.lru import LRUCachePolicy

# filled by tm_scheduler (same process): {"sess_of_hash": {bytes: session}, "state": {session: {...}}, "evictions": n}
REG: dict = {"sess_of_hash": {}, "state": {}, "evictions": {"ended": 0, "away": 0, "lru": 0}}


class SessionAwareLRU(LRUCachePolicy):
    def evict(self, n, protected):
        state, soh = REG["state"], REG["sess_of_hash"]
        if n == 0 or not state:
            out = super().evict(n, protected)
            if out:
                REG["evictions"]["lru"] += len(out)
            return out
        any_ended = any(st.get("ended") for st in state.values())
        ended, away, rest = [], [], []
        for key in self.evictable_blocks:                     # oldest first
            if key in protected:
                continue
            st = state.get(soh.get(get_offload_block_hash(key)))
            if st is not None and st.get("ended"):
                ended.append(key)
                if len(ended) >= n:
                    break
            elif st is not None and st.get("state") == "away":
                away.append((st.get("ret", 0.0), key))
            else:
                rest.append(key)
                if not any_ended and len(rest) >= n:          # nothing ranks above LRU: stop at n
                    break
        away.sort(key=lambda x: -x[0])                         # among the protected, the latest return goes first
        chosen = (ended + rest + [k for _, k in away])[:n]
        if len(chosen) < n:
            return None
        out = []
        for key in chosen:
            block = self.blocks.pop(key)
            del self.evictable_blocks[key]
            out.append((key, block))
        ne = min(len(ended), n)
        nl = min(len(rest), n - ne)
        REG["evictions"]["ended"] += ne
        REG["evictions"]["lru"] += nl
        REG["evictions"]["away"] += n - ne - nl
        return out
