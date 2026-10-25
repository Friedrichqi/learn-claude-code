#!/usr/bin/env python3
"""CPU tests of the EPIC port (epic/, from research/16_epic_harness): the model-agnostic topic-16 tests (C4-C6,
runs_of, C8, C13) on synthetic token ids, plus the harness_eval changes: partial-rotary rotation checked against
vLLM's RotaryEmbedding, TP-rank merging of the L1 RPC, the age-capped arms, and the decode-reserve reset.

    /data/fritz/venv/bin/python tests/test_epic_port.py
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "epic"))
import epic_plan as ep  # noqa: E402


# -- planner --------------------------------------------------------------------------------------------

def _edit_sequence(rng, steps: int):
    """Prompts of a session whose middle is replaced on every call (a snip), as token lists."""
    head = rng.integers(1000, 2000, size=64).tolist()
    body = rng.integers(2000, 3000, size=400).tolist()
    return [head + rng.integers(3000, 4000, size=20 + s).tolist() + body + [9] for s in range(steps)]


def _run(arm, prompts, gen=(11, 12, 13)):
    pool = ep.SlotPool(100_000)
    entry, plans = None, []
    for step, new in enumerate(prompts):
        plan = ep.plan_call(arm, entry, new, im_start=-1)
        new_slots = pool.alloc(plan.stats["C"])
        decode = pool.alloc(len(gen))
        entry2 = ep.next_entry(plan, new, list(gen), new_slots, decode, step)
        pool.retain(entry2.slot)
        pool.release(new_slots)
        pool.release(decode)
        if entry is not None:
            pool.release(entry.slot)
        entry, plans = entry2, plans + [plan]
    return entry, plans, pool


def test_arm_params():
    assert ep.arm_params("prefix") == (None, None)
    assert ep.arm_params("epic128") == (128, None)
    assert ep.arm_params("epic128a1") == (128, 1)
    assert ep.arm_params("shifta4") == (0, 4)
    assert ep.is_arm("prefix_aa") and not ep.is_arm("epic128b1") and not ep.is_arm(None)


def test_c4_partitions_per_arm():
    rng = np.random.default_rng(0)
    old = rng.integers(10, 500, size=900).tolist()
    for _ in range(20):
        new = list(old)
        i = int(rng.integers(50, 600))
        new[i:i + int(rng.integers(0, 80))] = rng.integers(500, 900, size=int(rng.integers(1, 60))).tolist()
        new = new + [7]
        entry = ep.first_entry(old, np.arange(len(old)), len(old) - 50, 0)
        computed = {}
        for arm in ("prefix", "shift", "epic32", "epic128", "epic128a1"):
            plan = ep.plan_call(arm, entry, new, im_start=-1)
            n = len(new)
            covered = np.zeros(n, dtype=int)
            covered[:plan.p0] += 1
            for lo, hi, _s, _d in plan.override:
                covered[lo:hi] += 1
            for lo, hi in plan.compute:
                covered[lo:hi] += 1
            assert (covered == 1).all(), arm
            for lo, hi, slot_lo, delta in plan.override:
                for p in range(lo, hi):
                    src = slot_lo + (p - lo)
                    assert old[src] == new[p] and p - src == delta
            computed[arm] = plan.stats["C"]
        assert computed["prefix"] >= computed["epic128"] >= computed["epic32"] >= computed["shift"]
        assert computed["epic128a1"] >= computed["epic128"]       # first entry: all ages 0, cap not binding
        assert computed["epic128a1"] == computed["epic128"]


def test_c5_ages_compound_and_the_cap_bounds_them():
    prompts = _edit_sequence(np.random.default_rng(1), 6)
    b0 = 64 + 20 + 5
    mid = b0 + 200
    entry, _plans, pool = _run("shift", prompts)
    assert entry.age[mid] == 5                                   # five edits survived
    assert pool.used == len(np.unique(entry.slot))
    for cap in (1, 2):
        entry, plans, pool = _run(f"shifta{cap}", prompts)
        assert int(entry.age.max()) <= cap, (cap, int(entry.age.max()))
        assert sum(p.stats["aged_out"] for p in plans) > 0
        assert pool.used == len(np.unique(entry.slot))
    # the cap trades reuse for freshness: fewer rows overridden than plain shift
    s_shift = sum(p.stats["S"] for p in _run("shift", prompts)[1])
    s_cap = sum(p.stats["S"] for p in _run("shifta1", prompts)[1])
    assert 0 < s_cap < s_shift


def test_c6_slot_pool_refcounts():
    pool = ep.SlotPool(100)
    a = pool.alloc(40)
    b = pool.alloc(40)
    try:
        pool.alloc(30)
        raise AssertionError("expected exhaustion")
    except MemoryError:
        pass
    pool.retain(a[:10])
    pool.release(a)
    assert pool.used == 50
    c = pool.alloc(30)
    assert not set(c) & set(a[:10]) and not set(c) & set(b)
    for s in (a[:10], b, c):
        pool.release(s)
    assert pool.used == 0 and pool.free_ranges == [[0, 100]]


def test_runs_of_groups_contiguous_rows():
    pos = np.array([5, 6, 7, 10, 11, 12])
    slot = np.zeros(20, dtype=np.int64)
    birth = np.arange(20)
    slot[[5, 6, 7]] = [100, 101, 102]
    slot[[10, 11, 12]] = [103, 104, 200]
    birth[[10, 11, 12]] = [3, 4, 5]
    assert ep.runs_of(pos, slot, birth) == [[5, 8, 100, 0], [10, 12, 103, 7], [12, 13, 200, 7]]


# -- connector: rotation and TP merging ---------------------------------------------------------------

def _side(head_dim=128, rope_dim=64, theta=5e6):
    import torch
    import epic_connector as ecn
    side = object.__new__(ecn._WorkerSide)
    side.head_dim, side.rope_dim, side.theta, side.device = head_dim, rope_dim, theta, torch.device("cpu")
    half = rope_dim // 2
    side.inv_freq = 1.0 / (theta ** (torch.arange(0, half, dtype=torch.float64) * 2 / rope_dim))
    return side


def test_rotary_dim_from_config():
    import epic_connector as ecn
    ns = types.SimpleNamespace
    assert ecn._rotary_dim(ns(rotary_dim=64), 128) == 64                                   # MiniMax-M2
    assert ecn._rotary_dim(ns(rope_parameters={"partial_rotary_factor": 0.25}), 256) == 64  # Qwen3.8
    assert ecn._rotary_dim(ns(partial_rotary_factor=0.5), 128) == 64
    assert ecn._rotary_dim(ns(), 128) == 128                                                # Qwen3-32B


def test_partial_rotation_round_trip_and_untouched_dims():
    import torch
    side = _side()
    g = torch.Generator().manual_seed(0)
    rows = torch.randn(50, 2, 256, generator=g).to(torch.bfloat16)          # [n, H, K|V], head_dim 128
    deltas = torch.randint(-5000, 5000, (50,), generator=g)
    c, s = side.cos_sin(deltas)
    there = side.rotate_rows(rows, c, s)
    c2, s2 = side.cos_sin(-deltas)
    back = side.rotate_rows(there, c2, s2)
    assert torch.equal(there[..., 64:], rows[..., 64:])                     # K dims 64..127 and all of V
    err = (back[..., :64].float() - rows[..., :64].float()).abs().max().item()
    assert err < 0.05, err                                                  # two bf16 roundings


def test_rotation_matches_vllm_rope_at_the_shifted_position():
    """A key rotated by vLLM at position p0 and re-rotated by d equals vLLM's rotation at p0 + d
    (MiniMax-M2: head 128, rotary_dim 64, NeoX, theta 5e6), up to bf16 rounding."""
    import torch
    from vllm.config import VllmConfig, set_current_vllm_config
    from vllm.model_executor.layers.rotary_embedding import get_rope
    with set_current_vllm_config(VllmConfig()):
        rope = get_rope(128, max_position=200_000, dtype=torch.float32,
                        rope_parameters={"rope_theta": 5e6, "partial_rotary_factor": 0.5, "rope_type": "default"})
    side = _side()
    g = torch.Generator().manual_seed(1)
    n, H = 40, 2
    k = torch.randn(n, H * 128, generator=g)
    q = torch.zeros(n, 128)
    p0 = torch.randint(0, 60_000, (n,), generator=g)
    d = torch.randint(-20_000, 20_000, (n,), generator=g)
    d = torch.where(p0 + d < 0, -p0, d)
    _, k0 = rope.forward_native(p0, q.clone(), k.clone())
    _, k1 = rope.forward_native(p0 + d, q.clone(), k.clone())
    rows = torch.cat([k0.view(n, H, 128), torch.zeros(n, H, 128)], dim=-1)  # V half unused here
    c, s = side.cos_sin(d)
    moved = side.rotate_rows(rows, c, s)[..., :128]
    ref = k1.view(n, H, 128)
    # vLLM's cos/sin cache is fp32 (angle error ~ position x 6e-8 at 60K tokens); the connector's angles are fp64,
    # so allow a relative error well below bf16's resolution (2^-8) of the stored rows
    rel = ((moved - ref).abs().max() / ref.abs().max()).item()
    assert rel < 2e-3, rel
    # control: rotating all 128 dims (topic 16's rope_dim = head_dim) is far off on dims 64..127
    full = _side(rope_dim=128)
    cf, sf = full.cos_sin(d)
    wrong = full.rotate_rows(rows, cf, sf)[..., :128]
    assert ((wrong - ref).abs().max() / ref.abs().max()).item() > 0.1


def test_merge_compare_sums_heads_over_ranks():
    import epic_connector as ecn
    r0 = [[[1.0, 2.0, 3.0, 4.0, 10.0]], [[0.5, 0.5, 0.5, 0.5, 10.0]]]
    r1 = [[[1.0, 1.0, 1.0, 1.0, 10.0]], [[0.5, 0.5, 0.5, 0.5, 10.0]]]
    out = ecn.merge_compare([r0, r1])
    assert out == [[[2.0, 3.0, 4.0, 5.0, 10.0]], [[1.0, 1.0, 1.0, 1.0, 10.0]]]


# -- middleware on a fake engine ----------------------------------------------------------------------------

class _FakeOut:
    def __init__(self, ids, gen, finished=True, server=None):
        self.prompt_token_ids, self.finished, self.num_cached_tokens = ids, finished, 0
        self.kv_transfer_params = {"epic_server": server or {"apc_hit": 0}}
        lp = [{t: types.SimpleNamespace(logprob=-0.1)} for t in gen]
        self.outputs = [types.SimpleNamespace(token_ids=list(gen), logprobs=lp, text="x",
                                              finish_reason="stop" if finished else None)]
        self.prompt_logprobs = [None] * len(ids)


def _ext(tmp, **env):
    os.environ.update(EPIC_LOG=str(Path(tmp) / "calls.jsonl"), EPIC_POOL_ROWS="50000", EPIC_IM_START="-7",
                      EPIC_SHADOW_EVERY=env.pop("shadow", "0"), EPIC_CF_EVERY="1",
                      EPIC_DECODE_RESERVE=env.pop("reserve", str(1 << 30)), **env)
    import epic_server_ext as ext
    return importlib.reload(ext)


def _logs(tmp):
    return [json.loads(x) for x in (Path(tmp) / "calls.jsonl").read_text().splitlines()]


def test_c8_middleware_plans_tracks_entries_and_merges_ranks():
    from vllm.sampling_params import SamplingParams
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp, shadow="1")
        seen = []

        class FakeEngine:                                        # two TP ranks answer every RPC
            async def collective_rpc(self, fn, args=(), kwargs=None):
                _a, _d, _f, _b, n_bins = args
                one = [[[0.0, 1.0, 0.0, 1.0, 1.0] for _ in range(n_bins)] for _ in range(2)]
                return [one, one]

        async def fake_generate(self, prompt, sp, rid, *a, **k):
            seen.append((rid, dict(prompt), sp))
            ids = prompt["prompt_token_ids"]
            if sp.prompt_logprobs:
                out = _FakeOut(ids, [5])
                out.prompt_logprobs = [None] + [{t: types.SimpleNamespace(logprob=-0.2)} for t in ids[1:]]
                yield out
                return
            yield _FakeOut(ids, [11, 12, 13][: sp.max_tokens or 3])

        ext._ORIG["generate"] = fake_generate
        engine = FakeEngine()
        rng = np.random.default_rng(3)
        head, body = rng.integers(100, 200, size=80).tolist(), rng.integers(200, 300, size=300).tolist()

        async def call(arm, n, prompt):
            sp = SamplingParams(max_tokens=3, extra_args={"kv_transfer_params": {"epic": {
                "arm": arm, "spec": f"S-{arm}", "call": n, "seed_key": "BLOCK"}}})
            async for _ in ext._generate(engine, {"prompt_token_ids": prompt}, sp, f"r{arm}{n}"):
                pass
            return sp

        async def main():
            sps = {}
            for arm in ("prefix", "epic32", "epic128a1"):
                await call(arm, 0, head + body + [9])
                p2 = head + rng.integers(300, 400, size=7).tolist() + body + [9, 11, 12] + [8]
                sps[arm] = await call(arm, 1, p2)
            return sps

        sps = asyncio.run(main())
        logs = _logs(tmp)
        assert not [x for x in logs if x.get("ev") == "error"]
        epic = [x for x in logs if x.get("ev") == "call" and x["arm"] == "epic32"]
        assert epic[1]["S"] > 0 and epic[1]["H"] == 32 and "shadow" in epic[1]
        assert epic[1]["shadow"]["tp_ranks"] == 2
        l1 = epic[1]["shadow"]["l1"]
        first = next(iter(l1.values()))[0]                       # 2 fake layers in one 4-layer group
        assert first[1] == 4.0 and first[4] == 2.0                 # norms summed over 2 ranks, count once
        assert "splice" in sps["epic128a1"].extra_args["kv_transfer_params"]
        assert "splice" not in sps["prefix"].extra_args["kv_transfer_params"]
        assert sps["prefix"].seed == sps["epic32"].seed == sps["epic128a1"].seed
        for spec in ("S-prefix", "S-epic32", "S-epic128a1"):
            ext.end_session(spec)
        assert ext.POOL.used == 0


def test_decode_overflow_resets_the_session():
    from vllm.sampling_params import SamplingParams
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp, reserve="2")
        lengths = {}

        async def fake_generate(self, prompt, sp, rid, *a, **k):
            yield _FakeOut(prompt["prompt_token_ids"], list(range(50, 50 + lengths[rid])))

        ext._ORIG["generate"] = fake_generate
        rng = np.random.default_rng(4)
        head, body = rng.integers(100, 200, size=80).tolist(), rng.integers(200, 300, size=300).tolist()

        async def call(n, prompt, gen_len):
            lengths[f"r{n}"] = gen_len
            sp = SamplingParams(max_tokens=64, extra_args={"kv_transfer_params": {"epic": {
                "arm": "shift", "spec": "S", "call": n, "seed_key": "B"}}})
            async for _ in ext._generate(None, {"prompt_token_ids": prompt}, sp, f"r{n}"):
                pass

        async def main():
            await call(0, head + body + [9], 3)                  # 2 decode rows kept: fits the reserve
            assert ext.SESSIONS["S"].entry is not None
            await call(1, head + [301] * 5 + body + [9, 50, 51] + [8], 5)   # 4 rows > reserve 2: reset
            assert ext.SESSIONS["S"].entry is None and ext.SESSIONS["S"].salt == "shift:S:r1"
            assert ext.POOL.used == 0
            await call(2, head + [302] * 5 + body + [9] + [7], 2)
            assert ext.SESSIONS["S"].entry is not None

        asyncio.run(main())
        calls = {x["call"]: x for x in _logs(tmp) if x.get("ev") == "call"}
        assert calls[1]["entry_reset"] == "decode_overflow" and "entry_reset" not in calls[0]
        assert calls[2]["E"] == 0
        ext.end_session("S")
        assert ext.POOL.used == 0


class _FakeReq:
    def __init__(self, rid, n, splice):
        self.request_id, self.num_tokens = rid, n
        self.kv_transfer_params = {"splice": splice}


def _sched_output(rid, blocks, computed, scheduled):
    new = types.SimpleNamespace(req_id=rid, block_ids=(blocks,), num_computed_tokens=computed)
    cached = types.SimpleNamespace(req_ids=[], new_block_ids=[], resumed_req_ids=set(), num_computed_tokens=[])
    return types.SimpleNamespace(scheduled_new_reqs=[new], scheduled_cached_reqs=cached,
                                 num_scheduled_tokens={rid: scheduled})


def test_c13_connector_copies_rows_the_prefix_cache_served_beyond_p0():
    import epic_connector as ecn
    side = ecn._SchedulerSide(types.SimpleNamespace(cache_config=types.SimpleNamespace(block_size=16)))
    n = 4000
    splice = {"p0": 2848, "load": [], "from_layer": 0, "override": [[2860, 3900, 500, 7]],
              "save": [[2848, 2860, 10_000], [3900, 4000, 10_012]], "apc_save": [[2848, 3104, 20_000]]}
    req = _FakeReq("r", n, splice)
    assert side.get_num_new_matched_tokens(req, 2880) == (0, False)
    blocks = list(range(100, 100 + n // 16 + 1))
    meta = side.build_connector_meta(_sched_output("r", blocks, 2880, n - 2880))
    apc = [op for op in meta.saves if op.pos and op.pos[0] == 2848]
    assert len(apc) == 1 and apc[0].pos == list(range(2848, 2880)) and apc[0].slots == list(range(20_000, 20_032))
    assert meta.overrides and meta.overrides[0].segs[0][:3] == [2880, 3900, 520]
    fin = side.request_finished(req)["epic_server"]
    assert fin["overshoot"] == 32 and fin["apc_saved"] == 32 and fin["apc_hit"] == 2880


def _hybrid_config(attn_gid=1, block_size=784):
    """A Qwen3.8-like KV cache config: one attention group of 16 layers and three Gated DeltaNet groups."""
    import torch
    from vllm.v1.kv_cache_interface import FullAttentionSpec, KVCacheConfig, KVCacheGroupSpec, MambaSpec
    attn = FullAttentionSpec(block_size=block_size, num_kv_heads=4, head_size=256, dtype=torch.bfloat16)
    mamba = MambaSpec(block_size=block_size, shapes=((3, 8),), dtypes=(torch.bfloat16,))
    groups, k = [], 0
    for gid in range(4):
        if gid == attn_gid:
            groups.append(KVCacheGroupSpec([f"model.layers.{4 * i + 3}.self_attn.attn" for i in range(16)], attn))
        else:
            groups.append(KVCacheGroupSpec([f"model.layers.{4 * i + k}.linear_attn" for i in range(16)], mamba))
            k += 1
    return KVCacheConfig(num_blocks=64, kv_cache_tensors=[], kv_cache_groups=groups)


def test_hybrid_attention_group_and_block_ids():
    import epic_connector as ecn
    cfg = _hybrid_config(attn_gid=1)
    gid, group = ecn._attn_group(cfg)
    assert gid == 1 and len(group.layer_names) == 16 and group.layer_names[0] == "model.layers.3.self_attn.attn"
    assert ecn._attn_group(types.SimpleNamespace(kv_cache_groups=[cfg.kv_cache_groups[1]])) == (0, None)
    vc = types.SimpleNamespace(cache_config=types.SimpleNamespace(block_size=16))
    side = ecn._SchedulerSide(vc, cfg)
    assert side.gid == 1 and side.hybrid and side.block_size == 784
    n = 3000
    splice = {"p0": 1568, "load": [[0, 1568, 0, 0]], "from_layer": 0, "override": [[1600, 2900, 500, 5]],
              "save": [[1568, 1600, 9000], [2900, 3000, 9032]], "apc_save": []}
    req = _FakeReq("h", n, splice)
    # a planned load is computed instead on a hybrid model (no external tokens)
    assert side.get_num_new_matched_tokens(req, 784) == (0, False)
    attn_blocks, other = [7, 8, 9, 10], [90, 91, 92, 93]
    new = types.SimpleNamespace(req_id="h", block_ids=(other, attn_blocks, other, other), num_computed_tokens=784)
    cached = types.SimpleNamespace(req_ids=[], new_block_ids=[], resumed_req_ids=set(), num_computed_tokens=[])
    out = types.SimpleNamespace(scheduled_new_reqs=[new], scheduled_cached_reqs=cached,
                                num_scheduled_tokens={"h": n - 784})
    meta = side.build_connector_meta(out)
    assert meta.overrides and meta.overrides[0].blocks == attn_blocks and not meta.loads
    # the single-group (MiniMax) path is unchanged
    flat = ecn._SchedulerSide(vc, types.SimpleNamespace(kv_cache_groups=[cfg.kv_cache_groups[1]]))
    assert flat.gid == 0 and not flat.hybrid and flat.block_size == 16


def test_hybrid_worker_registers_only_attention_views():
    import torch
    import epic_connector as ecn
    cfg = _hybrid_config(attn_gid=0, block_size=784)
    hf = types.SimpleNamespace(head_dim=256, hidden_size=5120, num_attention_heads=24,
                               rope_parameters={"rope_theta": 1e7, "partial_rotary_factor": 0.25,
                                                "rope_type": "default"})
    vc = types.SimpleNamespace(model_config=types.SimpleNamespace(hf_config=hf),
                               kv_transfer_config=types.SimpleNamespace(kv_connector_extra_config={}),
                               cache_config=types.SimpleNamespace(block_size=16))
    side = ecn._WorkerSide(vc, cfg)
    assert side.sched_block_size == 784 and side.rope_dim == 64 and side.theta == 1e7
    raw = torch.zeros(1 << 16, dtype=torch.int8)
    caches = {}
    for i in range(16):
        caches[f"model.layers.{4 * i + 3}.self_attn.attn"] = torch.zeros(4, 4, 784, 512, dtype=torch.bfloat16)
        for k in range(3):
            caches[f"model.layers.{4 * i + k}.linear_attn"] = raw          # raw state buffer, shared
    side.register_kv_caches(caches)
    assert len(side.views) == 16 and side.names[0] == "model.layers.3.self_attn.attn"
    assert side.names[-1] == "model.layers.63.self_attn.attn"
    assert side.kernel_block_size == 784 and side.ratio == 1 and side.num_heads == 4


def test_dsv4_hook_saves_and_substitutes_attention_inputs():
    import torch
    import vllm.forward_context as fc
    import epic_connector as ecn
    import epic_dsv4 as ed
    from vllm.v1.kv_cache_interface import KVCacheConfig, KVCacheGroupSpec, SlidingWindowSpec
    swa = SlidingWindowSpec(block_size=64, num_kv_heads=1, head_size=8, dtype=torch.bfloat16, sliding_window=128)
    groups = [KVCacheGroupSpec([f"model.layers.{i}.attn.compressor.state_cache" for i in range(2)], swa),
              KVCacheGroupSpec([f"model.layers.{i}.attn.swa_cache" for i in (0, 2)], swa)]
    cfg = KVCacheConfig(num_blocks=16, kv_cache_tensors=[], kv_cache_groups=groups)
    vc = types.SimpleNamespace(cache_config=types.SimpleNamespace(block_size=256),
                               model_config=types.SimpleNamespace(hf_config=types.SimpleNamespace(
                                   hidden_size=8, num_hidden_layers=3)),
                               kv_transfer_config=types.SimpleNamespace(kv_connector_extra_config={}))
    sched = ed._SchedulerSideDSV4(vc, cfg)
    assert sched.gid == 1 and sched.block_size == 64 and sched.hybrid
    state = {"spec": {"apc_save": [[0, 10, 5]]}, "p0": 0, "blocks": [5, 6]}
    meta = ecn.EpicMetadata()
    sched._apc_save(meta, "r", state, 64)
    assert state["apc_saved"] == 0 and not meta.saves
    old_pool = ecn._WorkerSide.POOL
    ecn._WorkerSide.POOL = None
    os.environ["EPIC_POOL_ROWS"] = "400"
    try:
        w = ed._WorkerSideDSV4(vc, cfg)
        w.register_kv_caches({ed.SWA0: torch.zeros(16, 1, 64, 584, dtype=torch.uint8)})
        assert ecn._WorkerSide.POOL.shape == (3, 400, 8) and w.kernel_block_size == 64 and w.ratio == 1
        blocks = [5, 6]
        n = 100
        slot_map = torch.tensor([blocks[p // 64] * 64 + p % 64 for p in range(n)])
        stored = torch.randn(50, 8).to(torch.bfloat16)
        ecn._WorkerSide.POOL[1, 200:250] = stored
        meta = ecn.EpicMetadata(saves=[ecn.SaveOp("r", blocks, list(range(0, 50)), list(range(100, 150)))],
                                overrides=[ecn.OverrideOp("r", [[50, 100, 200, 0]], 1, blocks)])
        conn = types.SimpleNamespace(has_connector_metadata=lambda: True, _get_connector_metadata=lambda: meta)
        w.connector = conn
        orig = fc.get_forward_context
        fc.get_forward_context = lambda: types.SimpleNamespace(slot_mapping={ed.SWA0: slot_map})
        try:
            hs = torch.randn(n, 8).to(torch.bfloat16)
            assert w._pre_hook(0, "model.layers.0.attn", None, (None, hs, None), {}) is None   # from_layer 1
            assert torch.equal(ecn._WorkerSide.POOL[0, 100:150], hs[:50])
            (pos, new, _), _kw = w._pre_hook(1, "model.layers.1.attn", None, (None, hs, None), {})
            assert torch.equal(new[50:], stored) and torch.equal(new[:50], hs[:50]) and not torch.equal(new, hs)
            assert torch.equal(ecn._WorkerSide.POOL[1, 100:150], hs[:50])
        finally:
            fc.get_forward_context = orig
    finally:
        ecn._WorkerSide.POOL = old_pool
        os.environ.pop("EPIC_POOL_ROWS", None)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_e3_sampler_draw_strata_exclusions_and_replacement():
    se = _load(ROOT / "run" / "sample_e3.py", "sample_e3")
    a, b = se.draw(20261014), se.draw(20261014)
    assert a["tasks"] == b["tasks"] and a["strata"] == b["strata"]               # the same seed, the same draw
    assert se.draw(7)["tasks"] != a["tasks"]
    assert len(a["tasks"]) == 100 == len(set(a["tasks"]))
    assert {k: len(v["tasks"]) for k, v in a["strata"].items()} == se.STRATA
    e1 = set(json.loads((ROOT / "run" / "subsets.json").read_text())["swe"]["tasks"][:30])
    assert not (set(a["tasks"]) & e1)
    assert all(se.difficulty(t) != ">4 hours" for t in a["tasks"])
    label = "1-4 hours"
    out = a["strata"][label]["tasks"][0]
    spare = a["strata"][label]["spares"][0]
    pos = a["tasks"].index(out)
    assert se.replace(a, out) == spare and a["tasks"][pos] == spare and out not in a["tasks"]
    assert a["replacements"][-1]["out"] == out and a["strata"][label]["spares"][0] != spare


def test_list_task_spec_and_per_block_commands():
    sys.path.insert(0, str(ROOT / "run"))
    import batch
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "l.json"
        f.write_text(json.dumps({"tasks": ["t2", "t0", "t1"]}))
        assert batch.task_list("swe", f"list:{f}") == ["t2", "t0", "t1"]
    er = _load(ROOT / "run" / "epic_round.py", "epic_round_t")
    for ph, block in (("e3r0", "e3r0"), ("e3r0b07", "e3r0"), ("e3r2b07x1", "e3r2"), ("e1r1", "e1r1"), ("b1ar0", "b1ar0")):
        assert er.block_of(ph) == block
    a = types.SimpleNamespace(server="s", bench="swe", concurrency=3, chunk=1, stagger=2.0, agent_timeout_multiplier=2.0)
    cmd = er.batch_cmd(a, "URL", ["prefix", "epic128"], "e3r0b07", ["t"], seed_key="e3r0", rotate_offset=7)
    assert cmd[cmd.index("--rotate-offset") + 1] == "7" and cmd[cmd.index("--seed-key") + 1] == "e3r0"
    assert cmd[cmd.index("--agent-timeout-multiplier") + 1] == "2.0" and "--rotate" in cmd


def test_e3_analysis_statistic_and_verdicts():
    an = _load(ROOT / "report" / "e3_analysis.py", "e3_analysis_t")
    won = {"prefix": {"t1": (1, 1), "t2": (0, 1), "t3": (0, 0)}, "prefix_aa": {"t1": (1, 1), "t2": (1, 1), "t3": (0, 0)},
           "epic128": {"t1": (1, 0), "t2": (1, 1), "t3": (0, 0)}}
    trials = {(arm, blk, t): {"bench": "swe", "reward": float(v[i])}
              for arm, by in won.items() for t, v in by.items() for i, blk in enumerate(("e3r0", "e3r1"))}
    r = an.diff_stats(trials, "epic128", ["prefix", "prefix_aa"], {"t1", "t2", "t3"})
    # block d: t1 0, -1; t2 +0.5, 0; t3 0, 0 -> task means -0.5, +0.25, 0
    assert r["tasks"] == 3 and r["blocks"] == 6 and abs(r["diff"] - (-0.25 / 3)) < 1e-12 and r["lo"] < r["diff"] < r["hi"]
    r0 = an.diff_stats(trials, "epic128", ["prefix", "prefix_aa"], {"t1", "t2", "t3"},
                       {("e3r0", t) for t in ("t1", "t2", "t3")})
    assert r0["blocks"] == 3 and abs(r0["diff"] - 0.5 / 3) < 1e-12
    assert an.diff_stats(trials, "epic128a4", ["prefix"], {"t1"})["diff"] is None
    assert an.verdict_h1({"diff": 0.0, "lo": -0.04, "hi": 0.04}).startswith("**non-inferior")
    assert an.verdict_h1({"diff": -0.08, "lo": -0.12, "hi": -0.06}).startswith("**inferior")
    assert an.verdict_h1({"diff": -0.03, "lo": -0.07, "hi": 0.01}).startswith("not shown")
    assert an.verdict_h2({"diff": -0.07, "lo": -0.11, "hi": -0.03}).startswith("**worse")
    assert an.verdict_h2({"diff": -0.03, "lo": -0.07, "hi": 0.01}).startswith("not shown")
    assert an.aa_check({"diff": 0.01, "lo": -0.02, "hi": 0.04}) == "covers 0"
    assert "flagged" in an.aa_check({"diff": 0.06, "lo": 0.01, "hi": 0.11})
    assert an.verdict_h1({"diff": None}) == an.aa_check({"diff": None}) == "no data"


def test_gates_read_the_server_log_after_the_agents_logs():
    """A live check must read the server log last (e3's interim check at 02:01 UTC: read first, the calls that
    finished in between counted as missing records)."""
    pg = _load(ROOT / "epic" / "pilot_gates.py", "pilot_gates_t")
    with tempfile.TemporaryDirectory() as tmp:
        pg.ROOT = Path(tmp)
        trial = Path(tmp) / "jobs" / "S+prefix" / "swe" / "e3r0b00__c00" / "t__x"
        (trial / "agent" / "s15").mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps({"started_at": "2026-10-14T00:00:00Z",
                                                       "finished_at": "2026-10-14T00:10:00Z"}))
        (trial / "agent" / "s15" / "calls.jsonl").write_text(json.dumps(
            {"purpose": "lead", "epic": {"n": 0}, "response": {}, "request": {"messages": []}}) + "\n")
        (Path(tmp) / "runs" / "epic").mkdir(parents=True)
        (Path(tmp) / "runs" / "epic" / "S.calls.jsonl").write_text(json.dumps({"ev": "call", "spec": "t__x", "call": 0, "P": 9}) + "\n")
        order, load = [], pg.load_jsonl
        pg.load_jsonl = lambda path: order.append(Path(path).name) or load(Path(path))
        argv = sys.argv
        sys.argv = ["pilot_gates.py", "--server", "S", "--phase", "e3r0", "--url", "http://127.0.0.1:1"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                pg.main()
        except SystemExit:
            pass
        finally:
            sys.argv = argv
        assert order.index("calls.jsonl") < order.index("S.calls.jsonl"), order
        g = json.loads((Path(tmp) / "runs" / "epic" / "gates_S_e3r0.json").read_text())["gates"]
        assert g["tagged_lead_calls"] == 1 and g["missing_server_records"] == 0


def test_e3_stage_counts_complete_blocks_and_checks_the_prereg():
    stage = _load(ROOT / "run" / "stage_e3.py", "stage_e3_t")
    with tempfile.TemporaryDirectory() as tmp:
        stage.ROOT = Path(tmp)

        def trial(arm: str, job: str, finished: bool = True) -> None:
            d = Path(tmp) / "jobs" / f"{stage.SERVER}+{arm}" / "swe" / job / f"task__{arm}"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"finished_at": "2026-10-14T00:00:00Z" if finished else None}))
        for arm in stage.ARMS:
            trial(arm, "e3r0b03__c00")                          # complete
        for arm in stage.ARMS[:4]:
            trial(arm, "e3r0b04__c00")
            trial(arm, "e3r0b05__c00")
        trial(stage.ARMS[4], "e3r0b04x1__c00")                  # completed by its rerun
        trial(stage.ARMS[4], "e3r0b05__c00", finished=False)     # still running
        trial(stage.ARMS[0], "e3r1b03__c00")                    # another replicate
        assert stage.complete_blocks("e3r0") == 2 and stage.complete_blocks("e3r1") == 0
        for d in ("run/state", "report", "runs/epic"):
            (Path(tmp) / d).mkdir(parents=True)
        (Path(tmp) / stage.TASKS).write_text('{"tasks": []}')
        (Path(tmp) / "report" / "e3_analysis.py").write_text("# analysis\n")
        h = {rel: stage.sha(Path(tmp) / rel) for rel in (stage.TASKS, "report/e3_analysis.py")}
        stage.PREREG = Path(tmp) / "runs" / "epic" / "prereg_e3.md"
        stage.PREREG.write_text(f"- The list is `{stage.TASKS}`, sha256 `{h[stage.TASKS]}`, made by ...\n"
                                f"The analysis is fixed in `report/e3_analysis.py`, sha256 `{h['report/e3_analysis.py']}`.\n")
        (Path(tmp) / "runs" / "epic" / "prereg_e3.sha256").write_text(stage.sha(stage.PREREG) + "  runs/epic/prereg_e3.md\n")
        with contextlib.redirect_stdout(io.StringIO()):
            assert stage.check_prereg()
            (Path(tmp) / "report" / "e3_analysis.py").write_text("# analysis, edited after the pre-registration\n")
            assert not stage.check_prereg()
            (Path(tmp) / "report" / "e3_analysis.py").write_text("# analysis\n")
            assert stage.check_prereg()
            stage.PREREG.write_text(stage.PREREG.read_text() + "edited\n")
            assert not stage.check_prereg()



# -- e4: recompute arms (planner) -------------------------------------------------------------------------

def test_recompute_arms_parse():
    assert ep.arm_params("epic128rjsd") == (128, None) == ep.arm_params("epic128rtvd") == ep.arm_params("epic128rexp")
    assert ep.recompute_metric("epic128rjsd") == "jsd" and ep.recompute_metric("epic128rexp") == "exp"
    assert ep.recompute_metric("epic128") is None and ep.recompute_metric("epic128a1") is None
    assert ep.recompute_metric(None) is None and ep.recompute_metric("prefix") is None
    assert not ep.is_arm("epic128rkl") and not ep.is_arm("epic48rjsd") and not ep.is_arm("epic128a1rjsd")
    assert ep.is_arm("epic128rjsd") and ep.is_arm("shiftrtvd")


def _sim(arm, prompts, gen=(11, 12, 13), recompute_at=(), pool=None, absorb_at=None):
    """The server's per-call bookkeeping on a planner-only session: plan (or a forced full recomputation at the
    given call indices), allocation, next_entry, optional absorb_apc, retain/release. Returns the entries per
    call, the plans and the pool; checks the reference counts after every call."""
    pool = pool or ep.SlotPool(200_000)
    entry, entries, plans = None, [], []
    for step, new in enumerate(prompts):
        plan = ep.plan_call(arm, entry, new, im_start=-1)
        if step in recompute_at:
            plan = ep.recompute_plan(arm, entry, new, -1, plan.h)
        new_slots = pool.alloc(plan.stats["C"])
        decode = pool.alloc(len(gen))
        n_apc = 40 if absorb_at == step else 0
        apc = pool.alloc(n_apc)
        e2 = ep.next_entry(plan, new, list(gen), new_slots, decode, step)
        if n_apc:
            ep.absorb_apc(plan, e2, n_apc, apc)
        pool.retain(e2.held())
        for x in (new_slots, decode, apc):
            pool.release(x)
        if entry is not None:
            pool.release(entry.held())
        entry = e2
        entries.append(e2)
        plans.append(plan)
        want = np.zeros(pool.capacity, dtype=np.int64)
        np.add.at(want, entry.held(), 1)
        assert (want == pool.ref).all(), f"refcounts differ at call {step}"
        pool.assert_held(entry.held())
    return entries, plans, pool


def _snips(rng, steps, start=900):
    """Prompts of a session whose middle is cut and marked on every call, built from the previous sequence."""
    base = rng.integers(1000, 5000, size=start).tolist()
    out = [base + [9]]
    gen = [11, 12, 13]
    for k in range(1, steps):
        prev = out[-1] + gen[:2]                                # the entry's sequence: prompt + gen minus the last
        cut = 200 + 7 * k
        out.append(prev[:cut] + [7000 + k] * 12 + prev[cut + 40:] + rng.integers(5000, 6000, size=30).tolist() + [9])
    return out


def test_originals_follow_lineage_and_reset_on_recompute():
    prompts = _snips(np.random.default_rng(11), 6)
    entries, plans, _pool = _sim("epic128rjsd", prompts)
    first = entries[0]
    assert (first.orig_slot == first.slot).all() and (first.orig_birth == first.birth).all()
    for k in range(1, len(prompts)):
        plan, prev, cur = plans[k], entries[k - 1], entries[k]
        p0 = plan.p0
        assert (cur.orig_slot[:p0] == prev.orig_slot[:p0]).all()
        heads = ep.run_heads(plan.segs, 128, p0, plan.n)
        assert heads, "every snip leaves a run head"
        for lo, hi, _s, _d in plan.override:
            for q in range(lo, hi):
                assert cur.orig_slot[q] == prev.orig_slot[plan.src[q]] and cur.orig_birth[q] == prev.orig_birth[plan.src[q]]
        for q in heads:
            assert cur.slot[q] != prev.slot[plan.src[q]]                       # recomputed: a new row
            assert cur.orig_slot[q] == prev.orig_slot[plan.src[q]]             # ... whose original is the source's
        new_pos = [q for q in range(p0, plan.n) if plan.src[q] < 0]
        assert new_pos and all(cur.orig_slot[q] == cur.slot[q] and cur.orig_birth[q] == q for q in new_pos)
        dec = np.arange(plan.n, len(cur))
        assert (cur.orig_slot[dec] == cur.slot[dec]).all()
    # from the second edit on, some kept rows are no longer their originals (earlier heads)
    assert (entries[-1].slot != entries[-1].orig_slot).any()
    # a full recomputation at the last call: the entry is all fresh and is its own original
    entries, plans, pool = _sim("epic128rjsd", prompts, recompute_at={5})
    last, plan = entries[-1], plans[-1]
    assert plan.recompute and plan.stats["recompute"] and plan.stats["h_stale"] == 0
    assert (last.age == 0).all() and (last.birth == np.arange(len(last))).all()
    assert (last.orig_slot == last.slot).all() and (last.orig_birth == last.birth).all() and last.h is None
    pool.release(last.held())
    assert pool.used == 0 and pool.free_ranges == [[0, pool.capacity]]


def test_orig_view_first_edit_equals_shift_plan_and_costs_the_fresh_tokens():
    prompts = _snips(np.random.default_rng(12), 3)
    entries, plans, _pool = _sim("epic128rjsd", prompts[:1])
    entry = entries[0]
    eplan = ep.plan_call("epic128rjsd", entry, prompts[1], im_start=-1)
    oplan = ep.plan_call("shift", ep.orig_view(entry), prompts[1], -1, segs=eplan.segs)
    shift = ep.plan_call("shift", entry, prompts[1], im_start=-1)
    assert oplan.load == shift.load and oplan.override == shift.override and oplan.compute == shift.compute
    assert oplan.stats["C"] == eplan.stats["C"] - eplan.stats["H"] == eplan.stats["F"]
    # at a second edit far before the first one's run heads, those heads are kept rows that are no longer their
    # originals: the original view differs from the arm's rows there
    entries, plans, _pool = _sim("epic128rjsd", prompts[:2])
    e1 = entries[1]
    second = e1.tokens[:50] + [7777] * 5 + e1.tokens[60:] + [9]
    eplan = ep.plan_call("epic128rjsd", e1, second, im_start=-1)
    oplan = ep.plan_call("shift", ep.orig_view(e1), second, -1, segs=eplan.segs)
    assert oplan.p0 == eplan.p0 and oplan.stats["C"] == eplan.stats["F"]

    def rows(plan):                                          # position -> slot of every loaded/overridden row
        return {q: s + (q - lo) for lo, hi, s, _d in plan.load + plan.override for q in range(lo, hi)}
    o_rows, e_rows = rows(oplan), rows(eplan)
    src = {q: (q if q < oplan.p0 else int(eplan.src[q])) for q in o_rows}
    assert all(o_rows[q] == e1.orig_slot[src[q]] for q in o_rows)          # every O row is an original
    assert all(e_rows[q] == e1.slot[q if q < eplan.p0 else int(eplan.src[q])] for q in e_rows)
    assert any(o_rows[q] != e_rows[q] for q in e_rows if q in o_rows)       # ... unlike the arm's (earlier heads)
    assert set(e_rows) < set(o_rows)                                        # O also keeps this call's run heads


def test_same_as_original_cases():
    rng = np.random.default_rng(13)
    prompts = _snips(rng, 3)
    entries, plans, _pool = _sim("epic128rjsd", prompts[:1])
    e0 = entries[0]
    snip = ep.plan_call("epic128rjsd", e0, prompts[1], im_start=-1)
    assert not ep.same_as_original(snip)                                   # run heads are recomputed
    seq = e0.tokens
    tail = seq[:700] + rng.integers(6000, 7000, size=40).tolist() + [9]     # the tail rewritten (E-0 miss)
    tplan = ep.plan_call("epic128rjsd", e0, tail, im_start=-1)
    assert tplan.stats["edited"] and tplan.stats["H"] == 0 and ep.same_as_original(tplan)
    # after a snip with heads, the same kind of tail rewrite keeps rows that are no longer their originals
    entries, plans, _pool = _sim("epic128rjsd", prompts[:2])
    e1 = entries[1]
    tail1 = e1.tokens[:len(e1) - 20] + rng.integers(6000, 7000, size=40).tolist() + [9]
    t1 = ep.plan_call("epic128rjsd", e1, tail1, im_start=-1)
    assert t1.stats["H"] == 0 and not ep.same_as_original(t1)


def test_h_tracks_min_p0_since_last_recompute():
    rng = np.random.default_rng(14)
    base = rng.integers(1000, 5000, size=900).tolist()
    pool = ep.SlotPool(100_000)
    gen = [11, 12, 13]

    def step(entry, new, recompute=False):
        plan = ep.plan_call("epic128rjsd", entry, new, im_start=-1)
        if recompute:
            plan = ep.recompute_plan("epic128rjsd", entry, new, -1, plan.h)
        e2 = ep.next_entry(plan, new, gen, pool.alloc(plan.stats["C"]), pool.alloc(3), 0)
        return plan, e2

    _p, e = step(None, base + [9])
    assert e.h is None
    hs = []
    for x in (520, 260, 390):
        prev = e.tokens
        new = prev[:x] + [8000 + x] * 10 + prev[x + 30:] + [9]
        plan, e = step(e, new)
        hs.append((plan.p0, e.h))
    assert hs == [(512, 512), (256, 256), (384, 256)], hs
    plan, e = step(e, e.tokens + [4000, 4001, 9])                           # append-only: h kept
    assert not plan.stats["edited"] and e.h == 256
    x = 600
    new = e.tokens[:x] + [8100] * 10 + e.tokens[x + 30:] + [9]
    plan, e = step(e, new, recompute=True)
    assert plan.p0 == 256 and plan.recompute and e.h is None and plan.stats["C"] == len(new) - 256


def test_recompute_plan_shape_and_fresh_entry():
    prompts = _snips(np.random.default_rng(15), 4)
    entries, plans, _pool = _sim("epic128rjsd", prompts[:3])
    e = entries[-1]
    eplan = ep.plan_call("epic128rjsd", e, prompts[3], im_start=-1)
    h = eplan.h
    assert h is not None and h % ep.BLOCK == 0 and h <= eplan.p0
    rp = ep.recompute_plan("epic128rjsd", e, prompts[3], -1, h)
    n = len(prompts[3])
    assert rp.p0 == h and rp.override == [] and rp.compute == [[h, n]] and rp.stats["C"] == n - h
    assert rp.stats["S"] == rp.stats["H"] == 0 and rp.stats["E"] == h and rp.stats["recompute"]
    assert all(d == 0 for _lo, _hi, _s, d in rp.load) and sum(hi - lo for lo, hi, _s, _d in rp.load) == h
    r0 = ep.recompute_plan("epic128rjsd", e, prompts[3], -1, 0)
    assert r0.p0 == 0 and r0.load == [] and r0.stats["C"] == n


def test_absorb_apc_redirects_originals_of_new_positions():
    rng = np.random.default_rng(16)
    old = rng.integers(1000, 5000, size=600).tolist()
    pool = ep.SlotPool(100_000)
    plan0 = ep.plan_call("epic128rjsd", None, old + [9], im_start=-1)
    e0 = ep.next_entry(plan0, old + [9], [11, 12], pool.alloc(plan0.stats["C"]), pool.alloc(2), 0)
    seq = e0.tokens
    x = 405                                                                  # p0 = 400: [400, 405) is exact tail
    new = seq[:x] + [7777] * 30 + seq[x:] + [9]
    plan = ep.plan_call("epic128rjsd", e0, new, im_start=-1)
    assert plan.p0 == 400 and (plan.src[400:405] >= 0).all() and (plan.src[405:435] < 0).all()
    e1 = ep.next_entry(plan, new, [11, 12], pool.alloc(plan.stats["C"]), pool.alloc(2), 1)
    planned = e1.slot[405:435].copy()
    apc = pool.alloc(40)
    ep.absorb_apc(plan, e1, 40, apc)
    assert (e1.orig_slot[405:435] == apc[5:35]).all() and (e1.orig_birth[405:435] == np.arange(405, 435)).all()
    assert (e1.orig_slot[400:405] == e0.orig_slot[400:405]).all()             # sourced: the source's original
    assert not set(planned.tolist()) & set(e1.held().tolist())               # the unwritten slots are not held


def _per_position(runs):
    """{position: rotation delta} of a plan's load or override runs (slot numbers depend on the allocator)."""
    return {q: d for lo, hi, _s, d in runs for q in range(lo, hi)}


def test_epic128_plans_unchanged_by_tracking():
    """Without a recomputation, the recompute arm plans exactly like epic128: the same positions loaded,
    overridden (with the same rotation) and computed, and the same ages. Only slot numbers differ, because the
    originals it keeps change what the allocator hands out next."""
    prompts = _snips(np.random.default_rng(17), 6)
    a, pa, _ = _sim("epic128", prompts)
    b, pb, _ = _sim("epic128rjsd", prompts)
    for x, y in zip(pa, pb):
        assert x.p0 == y.p0 and x.compute == y.compute and x.segs == y.segs
        assert _per_position(x.load) == _per_position(y.load)
        assert _per_position(x.override) == _per_position(y.override)
        assert x.stats == y.stats
    for x, y in zip(a, b):
        assert (x.age == y.age).all() and (x.birth == y.birth).all() and x.tokens == y.tokens
        assert x.orig_slot is None and y.orig_slot is not None
        assert x.held() is x.slot


def test_refcounts_exact_with_originals():
    prompts = _snips(np.random.default_rng(18), 12)
    entries, plans, pool = _sim("epic128rjsd", prompts, recompute_at={4, 9}, absorb_at=6)
    assert sum(p.recompute for p in plans) == 2
    pool.release(entries[-1].held())
    assert pool.used == 0 and pool.free_ranges == [[0, pool.capacity]]



# -- e4: recompute arms (middleware on a fake engine) -------------------------------------------------------

def test_partition_divergences_exact_bounded_and_lower_bounds():
    import math
    import epic_server_ext as ext
    lp = lambda xs: {i: math.log(x) for i, x in enumerate(xs)}          # noqa: E731
    same = ext.partition_div(lp([.5, .3, .2]), lp([.5, .3, .2]))
    assert same["kl"] == 0.0 and same["jsd"] == 0.0 and same["tvd"] == 0.0 and same["exp"] == 0.0
    d = ext.partition_div(lp([.5, .3, .2]), lp([.4, .4, .2]))
    kl = .5 * math.log(.5 / .4) + .3 * math.log(.3 / .4)
    m = [.45, .35, .2]
    js = 0.5 * sum(a * math.log2(a / c) for a, c in zip([.5, .3, .2], m)) + \
        0.5 * sum(b * math.log2(b / c) for b, c in zip([.4, .4, .2], m))
    assert abs(d["kl"] - kl) < 1e-12 and abs(kl - 0.02527) < 1e-5
    assert abs(d["tvd"] - 0.1) < 1e-12 and abs(d["jsd"] - math.sqrt(js)) < 1e-12
    assert abs(d["exp"] - (1 - math.exp(-kl))) < 1e-15
    disjoint = ext.partition_div({1: 0.0}, {2: 0.0})                    # all mass in the rest bucket on both
    assert disjoint["n_common"] == 0 and disjoint["jsd"] == 0.0         # (a coarsening: a lower bound, here 0)
    rng = np.random.default_rng(5)
    for _ in range(200):
        V = 2000
        alpha = float(rng.choice([0.05, 0.3, 1.0]))
        P = rng.dirichlet(np.full(V, alpha))
        Q = 0.7 * P + 0.3 * rng.dirichlet(np.full(V, alpha))
        Q = Q / Q.sum()
        full_kl = float(np.sum(np.where(P > 0, P * np.log(np.maximum(P, 1e-300) / np.maximum(Q, 1e-300)), 0)))
        M = 0.5 * (P + Q)
        full_js = 0.5 * float(np.sum(np.where(P > 0, P * np.log2(np.maximum(P, 1e-300) / M), 0))) + \
            0.5 * float(np.sum(np.where(Q > 0, Q * np.log2(np.maximum(Q, 1e-300) / M), 0)))
        full_tvd = 0.5 * float(np.abs(P - Q).sum())

        def top(D, k):
            idx = np.argsort(-D)[:k]
            return {int(i): float(np.log(D[i])) for i in idx if D[i] > 0}
        d128 = ext.partition_div(top(P, 128), top(Q, 128))
        d20 = ext.partition_div(top(P, 20), top(Q, 20))
        assert d128["kl"] <= full_kl + 1e-9 and d128["jsd"] <= math.sqrt(full_js) + 1e-9
        assert d128["tvd"] <= full_tvd + 1e-9
        assert d20["kl"] <= d128["kl"] + 1e-9 and d20["jsd"] <= d128["jsd"] + 1e-9 and d20["tvd"] <= d128["tvd"] + 1e-9
        assert 0 <= d128["jsd"] <= 1 and 0 <= d128["tvd"] <= 1 and 0 <= d128["exp"] < 1


def _dec_fake(seen, scale=0.05, fail=None, abort=None):
    """A fake AsyncLLM.generate for the decision tests: probe rids (epicdec-*) get a top-128 distribution whose
    noise depends on the probe's splice (so O and E differ, E and E2 agree); shadow probes and calls as in C8.
    `fail(rid, sp)` may raise or return 'nolp'; `abort(rid)` makes a main call raise CancelledError."""
    import hashlib as hl
    V = 3000
    base = np.random.default_rng(99).normal(size=V) * 3.0

    async def fake_generate(self, prompt, sp, rid, *a, **k):
        seen.append((rid, dict(prompt), sp))
        ids = prompt["prompt_token_ids"]
        if fail is not None and fail(rid, sp) == "nolp":
            out = _FakeOut(ids, [5])
            out.outputs[0].logprobs = None
            yield out
            return
        if rid.startswith("epicdec-"):
            splice = (sp.extra_args or {}).get("kv_transfer_params", {}).get("splice") or {}
            key = json.dumps([splice.get("load"), splice.get("override")], default=int)
            seed = int(hl.sha256(key.encode()).hexdigest()[:8], 16)
            logits = base + scale * np.random.default_rng(seed).normal(size=V)
            lp = logits - np.log(np.exp(logits - logits.max()).sum()) - logits.max()
            top = np.argsort(-lp)[:sp.logprobs]
            out = _FakeOut(ids, [int(top[0])])
            out.outputs[0].logprobs = [{int(t): types.SimpleNamespace(logprob=float(lp[t])) for t in top}]
            yield out
            return
        if sp.prompt_logprobs:
            out = _FakeOut(ids, [5])
            out.prompt_logprobs = [None] + [{t: types.SimpleNamespace(logprob=-0.2)} for t in ids[1:]]
            yield out
            return
        if abort is not None and abort(rid):
            raise asyncio.CancelledError()
        yield _FakeOut(ids, [11, 12, 13][: sp.max_tokens or 3])
    return fake_generate


class _RpcEngine:                                    # two TP ranks answer the shadow probes' compare RPC
    def __init__(self, max_logprobs=None):
        if max_logprobs is not None:
            self.model_config = types.SimpleNamespace(max_logprobs=max_logprobs)

    async def collective_rpc(self, fn, args=(), kwargs=None):
        _a, _d, _f, _b, n_bins = args
        one = [[[0.0, 1.0, 0.0, 1.0, 1.0] for _ in range(n_bins)] for _ in range(2)]
        return [one, one]


def _dcall(ext, engine, arm, spec, n, prompt, seed_key="BLK", **tag):
    from vllm.sampling_params import SamplingParams

    async def go():
        sp = SamplingParams(max_tokens=3, extra_args={"kv_transfer_params": {"epic": {
            "arm": arm, "spec": spec, "call": n, "seed_key": seed_key, **tag}}})
        async for _ in ext._generate(engine, {"prompt_token_ids": list(prompt)}, sp, f"r-{spec}-{n}"):
            pass
        return sp
    return go()


def _records(tmp, spec=None):
    return [x for x in _logs(tmp) if x.get("ev") == "call" and (spec is None or x["spec"] == spec)]


def test_decision_recompute_path_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp)
        seen = []
        ext._ORIG["generate"] = _dec_fake(seen)
        eng = _RpcEngine()
        prompts = _snips(np.random.default_rng(21), 4)
        spec = "st5-rc"

        async def main():
            await _dcall(ext, eng, "epic128rjsd", spec, 0, prompts[0])
            e0 = ext.SESSIONS[spec].entry
            eplan = ep.plan_call("epic128rjsd", e0, prompts[1], ext.IM_START)
            oplan = ep.plan_call("shift", ep.orig_view(e0), prompts[1], ext.IM_START, segs=eplan.segs)
            seen.clear()
            await _dcall(ext, eng, "epic128rjsd", spec, 1, prompts[1], dec_force="recompute", dec_floor=False)
            probes = [x for x in seen if x[0].startswith("epicdec-")]
            assert sorted(x[0].split("-")[1] for x in probes) == ["E", "O"]
            salts = {x[1]["cache_salt"] for x in probes}
            assert len(salts) == 2 and all(s.startswith("dec:") for s in salts)
            for rid, prompt, sp in probes:
                sl = sp.extra_args["kv_transfer_params"]["splice"]
                assert sp.max_tokens == 1 and sp.temperature == 0.0 and sp.logprobs == ext.DEC_K
                assert sl["save"] == [] and not sp.prompt_logprobs
                want = oplan if rid.startswith("epicdec-O") else eplan
                assert sl["p0"] == want.p0 and sl["load"] == want.load and sl["override"] == want.override
            main_call = [x for x in seen if x[0] == f"r-{spec}-1"][0]
            assert main_call[1]["cache_salt"] == f"epic128rjsd:{spec}:r1"
            sl = main_call[2].extra_args["kv_transfer_params"]["splice"]
            assert sl["override"] == [] and sl["p0"] == eplan.h
            e1 = ext.SESSIONS[spec].entry
            assert (e1.age == 0).all() and (e1.orig_slot == e1.slot).all() and e1.h is None
            assert ext.ref_check()["ok"]
            await _dcall(ext, eng, "epic128rjsd", spec, 2, prompts[2])       # unforced, under the new salt
            assert [x for x in seen if x[0] == f"r-{spec}-2"][0][1]["cache_salt"] == f"epic128rjsd:{spec}:r1"
            return eplan, oplan

        eplan, oplan = asyncio.run(main())
        recs = {r["call"]: r for r in _records(tmp, spec)}
        r1 = recs[1]
        P = len(prompts[1])
        assert r1["recompute"] is True and r1["dec"]["recompute"] and r1["dec"]["forced"] == "recompute"
        assert r1["C"] == P - eplan.h and r1["E"] == eplan.h and r1["S"] == 0 and r1["H"] == 0
        assert r1["dec"]["C_E"] == eplan.stats["C"] and r1["dec"]["C_O"] == oplan.stats["C"] == eplan.stats["F"]
        assert r1["C_charged"] == r1["C"] + r1["dec"]["C_O"] + r1["dec"]["C_E"]
        assert r1["dec"]["E"]["C"] == eplan.stats["C"] and r1["salt_gen"] == 1
        assert "fallback" not in r1 and "entry_reset" not in r1 and r1["stale_rows"] == 0
        assert 0 < r1["dec"]["jsd"] <= 1 and r1["dec"]["m"] == r1["dec"]["jsd"]
        assert "o20" in r1["dec"] and len(r1["dec"]["o20"]) == ext.TOPK
        r2 = recs[2]
        assert "dec" in r2 and "forced" not in r2["dec"] and r2["dec"]["recompute"] == (r2["dec"]["u"] < r2["dec"]["m"])
        assert "C_charged" in recs[0] and recs[0]["C_charged"] == recs[0]["C"] and "dec" not in recs[0]
        assert not [x for x in _logs(tmp) if x.get("ev") == "error"]
        ext.end_session(spec)
        assert ext.POOL.used == 0


def test_decision_keep_and_seeded_bernoulli():
    import math
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp)
        seen = []
        ext._ORIG["generate"] = _dec_fake(seen, scale=0.3)
        eng = _RpcEngine()
        rng = np.random.default_rng(22)
        prompts = _snips(rng, 8)

        async def main():
            for n, pr in enumerate(prompts):
                await _dcall(ext, eng, "epic128rjsd", "S-a", n, pr, dec_force="recompute")   # ignored: not st
                await _dcall(ext, eng, "epic128rjsd", "S-b", n, pr)
        asyncio.run(main())
        recs = [r for r in _records(tmp) if "dec" in r]
        assert len(recs) == 2 * (len(prompts) - 1)
        for r in recs:
            d = r["dec"]
            u = ext.seed_of("BLK|recompute", r["call"]) / 2 ** 32
            assert d["u"] == u and "forced" not in d and d["recompute"] == (u < d["m"]) and d["m"] == d["jsd"]
            assert abs(d["exp"] - (1 - math.exp(-d["kl"]))) < 1e-12
            assert d["kl20"] <= d["kl"] + 1e-12 and d["jsd20"] <= d["jsd"] + 1e-12 and d["tvd20"] <= d["tvd"] + 1e-12
            assert r.get("recompute", False) == d["recompute"] and "o20" not in d
            charged = r["C"] + d["C_O"] + (d["C_E"] if d["recompute"] else 0)
            assert r["C_charged"] == charged
        by = {}
        for r in recs:
            by.setdefault(r["call"], []).append(r["dec"]["u"])
        assert all(len(set(v)) == 1 for v in by.values())                   # u is common to the block's arms
        ext.end_session("S-a")
        ext.end_session("S-b")
        assert ext.POOL.used == 0


def test_decision_same_skips_probes():
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp)
        seen = []
        ext._ORIG["generate"] = _dec_fake(seen)
        rng = np.random.default_rng(23)
        prompts = _snips(rng, 1)

        async def main():
            await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-same", 0, prompts[0])
            e0 = ext.SESSIONS["st5-same"].entry
            tail = e0.tokens[:700] + rng.integers(6000, 7000, size=40).tolist() + [9]
            seen.clear()
            await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-same", 1, tail)
        asyncio.run(main())
        assert not [x for x in seen if x[0].startswith("epicdec-")]
        d = [r for r in _records(tmp) if r["call"] == 1][0]["dec"]
        assert d["same"] and d["m"] == 0.0 and d["C_O"] == 0 and not d["recompute"]
        ext.end_session("st5-same")
        assert ext.POOL.used == 0


def test_decision_errors_never_fallback():
    prompts = _snips(np.random.default_rng(24), 2)
    cases = {
        "raise": dict(fail=lambda rid, sp: (_ for _ in ()).throw(RuntimeError("boom")) if rid.startswith("epicdec-O") else None),
        "nolp": dict(fail=lambda rid, sp: "nolp" if rid.startswith("epicdec-E") else None),
    }
    for name, kw in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            ext = _ext(tmp)
            ext._ORIG["generate"] = _dec_fake([], **kw)

            async def main():
                await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-err", 0, prompts[0])
                await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-err", 1, prompts[1], dec_force="recompute")
            asyncio.run(main())
            r = [x for x in _records(tmp) if x["call"] == 1][0]
            assert r["dec"].get("error") and not r["dec"]["recompute"], (name, r["dec"])
            assert "fallback" not in r and "recompute" not in r and r["H"] > 0, name    # the arm's own plan
            assert [x for x in _logs(tmp) if x.get("ev") == "error" and x["where"] == "decide"], name
            ext.end_session("st5-err")
            assert ext.POOL.used == 0
    with tempfile.TemporaryDirectory() as tmp:                       # the server allows too few logprobs
        ext = _ext(tmp)
        ext._ORIG["generate"] = _dec_fake([])

        async def main():
            await _dcall(ext, _RpcEngine(max_logprobs=20), "epic128rjsd", "st5-lp", 0, prompts[0])
            await _dcall(ext, _RpcEngine(max_logprobs=20), "epic128rjsd", "st5-lp", 1, prompts[1])
        asyncio.run(main())
        d = [x for x in _records(tmp) if x["call"] == 1][0]["dec"]
        assert "max-logprobs" in d["error"] and not d["recompute"]
        ext.end_session("st5-lp")
    # no pool rows for the recomputation: the arm's plan is served (a decision error, not a fallback)
    e0 = ep.plan_call("epic128rjsd", None, prompts[0], -7)
    P0 = len(prompts[0])
    entry = ep.next_entry(e0, prompts[0], [11, 12, 13], np.arange(P0), np.arange(P0, P0 + 3), 0)
    eplan = ep.plan_call("epic128rjsd", entry, prompts[1], -7)
    n_apc = max(0, min(256, len(prompts[1]) - 1 - eplan.p0))
    cap = (P0 + 2) + eplan.stats["C"] + 3 + n_apc + 4
    assert cap < (P0 + 2) + (len(prompts[1]) - eplan.h) + 3
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp)
        os.environ["EPIC_POOL_ROWS"] = str(cap)
        ext = importlib.reload(ext)
        ext._ORIG["generate"] = _dec_fake([])

        async def main():
            await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-pool", 0, prompts[0])
            await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-pool", 1, prompts[1], dec_force="recompute")
        asyncio.run(main())
        r = [x for x in _records(tmp) if x["call"] == 1][0]
        assert r["dec"]["error"].startswith("pool") and not r["dec"]["recompute"] and "fallback" not in r
        assert r["C"] == eplan.stats["C"] and r["C_charged"] == r["C"] + r["dec"]["C_O"]
        assert ext.STATS["recomputes_epic128rjsd"] == 0 and ext.STATS["dec_errors_epic128rjsd"] == 1
        ext.end_session("st5-pool")
        assert ext.POOL.used == 0
    os.environ["EPIC_POOL_ROWS"] = "50000"


def test_abort_after_recompute_decision_keeps_entry():
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp)
        tries = {"n": 0}

        def abort(rid):
            if rid == "r-st5-ab-1":
                tries["n"] += 1
                return tries["n"] == 1
            return False
        ext._ORIG["generate"] = _dec_fake([], abort=abort)
        prompts = _snips(np.random.default_rng(25), 2)

        async def main():
            await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-ab", 0, prompts[0])
            e0 = ext.SESSIONS["st5-ab"].entry
            try:
                await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-ab", 1, prompts[1], dec_force="recompute")
                raise AssertionError("the call should have been cancelled")
            except asyncio.CancelledError:
                pass
            sess = ext.SESSIONS["st5-ab"]
            assert sess.entry is e0 and sess.salt_gen == 2 and ext.ref_check()["ok"]
            await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-ab", 1, prompts[1], dec_force="keep")
            assert ext.SESSIONS["st5-ab"].salt == "epic128rjsd:st5-ab:r2"
        asyncio.run(main())
        recs = [x for x in _records(tmp) if x["call"] == 1]
        assert recs[0]["aborted"] and recs[0]["dec"]["recompute"] and recs[0]["recompute"]
        assert "aborted" not in recs[1] and not recs[1]["dec"]["recompute"] and recs[1]["H"] > 0
        ext.end_session("st5-ab")
        assert ext.POOL.used == 0


def test_floor_probe_and_shadow_partition_metrics():
    with tempfile.TemporaryDirectory() as tmp:
        ext = _ext(tmp, shadow="1")
        seen = []
        ext._ORIG["generate"] = _dec_fake(seen)
        prompts = _snips(np.random.default_rng(26), 3)

        async def main():
            for n, pr in enumerate(prompts):
                await _dcall(ext, _RpcEngine(), "epic128rjsd", "st5-fl", n, pr, dec_force="keep", dec_floor=True)
        asyncio.run(main())
        recs = [x for x in _records(tmp) if x["call"] > 0]
        for r in recs:
            f = r["dec"]["floor"]
            assert f["kl"] == 0.0 and f["jsd"] == 0.0                     # E and E2: the same forward here
            sh = r["shadow"]
            for k in ("first_klp", "first_jsd", "first_tvd", "first_exp"):
                assert k in sh and 0.0 <= sh[k] <= (1.0 if k != "first_klp" else 50.0), (k, sh)
        salts = [x[1]["cache_salt"] for x in seen if x[0].startswith(("epicdec-", "epicshadow-"))]
        assert len(salts) == len(set(salts))                              # every probe its own salt
        assert len([x for x in seen if x[0].startswith("epicdec-E2")]) == len(recs)
        ext.end_session("st5-fl")
        assert ext.POOL.used == 0


def main() -> None:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as exc:                            # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
