#!/usr/bin/env python3
"""CPU tests of mu_scheduler.MUScheduler with vLLM's real scheduler in the loop (no GPU, no model).

A tiny pool (48 blocks of 16 tokens) and five simulated users whose prompts grow append-only, with
pauses between their requests, force prefix-cache evictions and preemptions. The engine's async
order is reproduced (schedule step N+1 before update step N) and outputs are faked. Checks:

  * the observer changes nothing: with pins off, every step schedules exactly what the stock
    AsyncScheduler schedules;
  * the log is consistent: every request arrives, is admitted, finishes; ideal >= hit; prefill
    classes and decode tokens add up to the scheduled tokens; commits - evictions = blocks that
    still carry a hash; one `pre` record per preemption;
  * pins: a pinned chain loses no reusable block while pinned, no preemption happens while a pin is held,
    and after the pins expire every block is back in the pool (no reference leak).

Runs on the login node (vLLM config with an explicit cuda DeviceConfig; nothing touches a device):

    python research/11_multiuser_kv/test_mu_scheduler.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

os.environ.setdefault("HF_HOME", "/projects/co/jlin4/agenticllm/hf_cache")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import torch  # noqa: E402
from vllm.config import CacheConfig, DeviceConfig, ModelConfig, SchedulerConfig, VllmConfig  # noqa: E402
from vllm.sampling_params import SamplingParams  # noqa: E402
from vllm.utils.hashing import get_hash_fn_by_name  # noqa: E402
from vllm.v1.core.kv_cache_utils import get_request_block_hasher, init_none_hash  # noqa: E402
from vllm.v1.core.sched.async_scheduler import AsyncScheduler  # noqa: E402
from vllm.v1.kv_cache_interface import FullAttentionSpec, KVCacheConfig, KVCacheGroupSpec  # noqa: E402
from vllm.v1.outputs import ModelRunnerOutput  # noqa: E402
from vllm.v1.request import Request  # noqa: E402
from vllm.v1.structured_output import StructuredOutputManager  # noqa: E402

import mu_scheduler  # noqa: E402

BLOCKS, BS = 48, 16
_CONFIG = {}


def vllm_config() -> VllmConfig:
    if "vc" not in _CONFIG:
        mc = ModelConfig(model="Qwen/Qwen3-32B", max_model_len=512, skip_tokenizer_init=True)
        sc = SchedulerConfig(max_num_seqs=8, max_num_batched_tokens=128, max_model_len=512,
                             enable_chunked_prefill=True, is_encoder_decoder=False, async_scheduling=True)
        cc = CacheConfig(block_size=BS, enable_prefix_caching=True)
        cc.num_gpu_blocks = BLOCKS
        _CONFIG["vc"] = VllmConfig(model_config=mc, scheduler_config=sc, cache_config=cc,
                                   device_config=DeviceConfig(device="cuda"))
        hf = get_hash_fn_by_name(_CONFIG["vc"].cache_config.prefix_caching_hash_algo)
        init_none_hash(hf)
        _CONFIG["hasher"] = get_request_block_hasher(BS, hf)
    return _CONFIG["vc"]


def build(cls, env: dict):
    vc = vllm_config()
    for key in [k for k in os.environ if k.startswith("MU_")]:
        del os.environ[key]
    os.environ.update(env)
    kvc = KVCacheConfig(num_blocks=BLOCKS, kv_cache_tensors=[], kv_cache_groups=[
        KVCacheGroupSpec(["l0"], FullAttentionSpec(block_size=BS, num_kv_heads=8, head_size=128,
                                                   dtype=torch.bfloat16))])
    return cls(vllm_config=vc, kv_cache_config=kvc, structured_output_manager=StructuredOutputManager(vc),
               block_size=BS, log_stats=True)


def workload(seed: int = 7, users: int = 5, turns: int = 4):
    """Per user: a list of (prompt token ids, max_tokens, pause steps before it, tool-call output)."""
    rng = random.Random(seed)
    plan = []
    for u in range(users):
        base = [1000 + 100 * u + (i % 97) for i in range(rng.randint(90, 140))]
        prompt, calls = list(base), []
        for t in range(turns):
            prompt = prompt + [5000 + 13 * u + t + (i % 31) for i in range(rng.randint(20, 50))]
            calls.append((list(prompt), rng.randint(12, 40), rng.randint(0, 12), rng.random() < 0.7))
        plan.append(calls)
    return plan


def drive(s, plan, max_steps: int = 5000, tick_s: float = 0.0):
    """Feed the users' requests (a user's next request arrives `pause` steps after its previous one
    finished), run the async schedule/update order with fake outputs; returns per-step schedules."""
    hasher = _CONFIG["hasher"]
    nxt = [0] * len(plan)
    due = [plan[u][0][2] for u in range(len(plan))]
    live: dict[int, Request] = {}
    pending: deque = deque()
    schedules = []
    step = 0
    while step < max_steps:
        for u, calls in enumerate(plan):
            if u not in live and nxt[u] < len(calls) and due[u] <= step:
                prompt, max_tokens, _, tool = calls[nxt[u]]
                rid = f"chatcmpl-MU-t-S{u:03d}~a1~{nxt[u]:05d}~ld~root-{u:04x}{nxt[u]:04x}"
                req = Request(rid, prompt, SamplingParams(max_tokens=max_tokens, ignore_eos=True), None,
                              block_hasher=hasher, session_id=f"MU-t-S{u:03d}~a1", priority=u)
                req._mu_tool = tool
                live[u] = req
                s.add_request(req)
        if s.has_requests():
            so = s.schedule()
            if so.total_num_scheduled_tokens > 0:
                s.get_grammar_bitmask(so)
            samples = {rid: not s.requests[rid].is_prefill_chunk for rid in so.num_scheduled_tokens}
            pending.append((so, samples))
            schedules.append(dict(so.num_scheduled_tokens))
        if len(pending) == 2 or (pending and not s.has_requests()):
            so, samples = pending.popleft()
            rids = list(so.num_scheduled_tokens)
            tokens = []
            for rid in rids:
                req = s.requests.get(rid)
                first = req is not None and req.num_output_tokens == 0
                tool = getattr(req, "_mu_tool", False)
                tokens.append([mu_scheduler.TOOL_CALL_TOKEN if (first and tool) else 11] if samples[rid] else [])
            s.update_from_output(so, ModelRunnerOutput(req_ids=rids, req_id_to_index={r: i for i, r in enumerate(rids)},
                                                       sampled_token_ids=tokens))
        for u, req in list(live.items()):
            if req.is_finished():
                del live[u]
                nxt[u] += 1
                if nxt[u] < len(plan[u]):
                    due[u] = step + plan[u][nxt[u]][2]
        if not live and not pending and all(nxt[u] >= len(plan[u]) for u in range(len(plan))):
            break
        step += 1
        if tick_s:
            time.sleep(tick_s)
    return schedules, step


def read_log(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def run_mu(env_extra: dict, plan=None, tick_s: float = 0.0):
    path = tempfile.mktemp(suffix=".jsonl", prefix="mu_sched_test_")
    s = build(mu_scheduler.MUScheduler, {"MU_SCHED_LOG": path, "MU_POOL_WALK_S": "0", **env_extra})
    schedules, steps = drive(s, plan or workload(), tick_s=tick_s)
    s.shutdown()
    return s, schedules, read_log(path)


def test_observer_changes_no_decision():
    stock = build(AsyncScheduler, {})
    plan = workload()
    ref, _ = drive(stock, plan)
    _, got, log = run_mu({}, plan)
    assert got == ref, "MUScheduler scheduled differently from AsyncScheduler"
    assert not [r for r in log if r["k"] == "err"], [r for r in log if r["k"] == "err"][:1]


def test_log_is_consistent():
    s, _, log = run_mu({})
    kinds = {}
    for r in log:
        kinds.setdefault(r["k"], []).append(r)
    assert not kinds.get("err"), kinds.get("err")[:1]
    arr, fin = kinds["arr"], kinds["fin"]
    assert len(arr) == len(fin) == 20, (len(arr), len(fin))
    first_adm = {r["r"]: r for r in kinds["adm"] if not r["res"]}
    assert set(first_adm) == {r["r"] for r in arr}
    for r in first_adm.values():
        assert r["ideal"] >= r["hit"] >= 0, r
    evictions = sum(g[1] for r in kinds.get("ev", []) for g in r["g"])
    assert evictions > 0, "workload should force evictions"
    steps = kinds["step"]
    for st in steps:
        assert sum(st["pf"]) + st["dc"] == st["tok"], st
        assert st["u0"] is not None and st["s0"] <= st["s1"] <= st["u0"] <= st["u1"], st
    hashed = sum(1 for b in s.bp.blocks if b._block_hash is not None)
    commits = sum(st["com"] for st in steps)
    assert commits - evictions == hashed, (commits, evictions, hashed)
    npre = sum(r["npre"] for r in fin)
    assert len(kinds.get("pre", [])) == npre, (len(kinds.get("pre", [])), npre)
    assert npre > 0, "workload should force preemptions"
    evict_tokens = sum(st["pf"][1] for st in steps)
    assert evict_tokens > 0 and evict_tokens <= sum(r["ideal"] - r["hit"] for r in first_adm.values()) + 1
    assert any(g[4] == "idle" for r in kinds["ev"] for g in r["g"]), "evictions of paused chains expected"


def test_pins_protect_and_release():
    s, _, log = run_mu({"MU_PIN": "1", "MU_PIN_WHEN": "always", "MU_PIN_TTL": "0.05",
                        "MU_PIN_MAX_FRAC": "0.6"}, tick_s=0.001)
    assert not [r for r in log if r["k"] == "err"], [r for r in log if r["k"] == "err"][:1]
    pinned: dict[int, bool] = {}
    pins_held = 0
    for r in log:                                     # records are in engine order
        if r["k"] == "pin":
            pinned[r["c"]] = True
            pins_held += 1
        elif r["k"] == "unpin":
            if pinned.pop(r["c"], None):
                pins_held -= 1
        elif r["k"] == "ev":
            for g in r["g"]:     # [chain, n, live, dead, ...]: a pinned chain may lose only dead blocks
                assert not (pinned.get(g[0]) and g[2]), f"chain {g[0]} lost reusable blocks while pinned"
        elif r["k"] == "pre":
            assert pins_held == 0, "a preemption happened while a pin was held"
    assert any(r["k"] == "pin" for r in log), "no pin taken"
    assert any(r["k"] == "unpin" and r["why"] == "hit" for r in log), "no pin released by a hit"
    assert s.pinned_blocks == 0 and all(c.pin is None for c in s.chain_list)
    assert all(b.ref_cnt == 0 for b in s.bp.blocks if not b.is_null), "reference leak"
    assert s.bp.get_num_free_blocks() == BLOCKS - 1


def test_toolcall_pins_only_tool_turns():
    _, _, log = run_mu({"MU_PIN": "1", "MU_PIN_WHEN": "toolcall", "MU_PIN_TTL": "0.05"}, tick_s=0.001)
    fins = {r["r"]: r for r in log if r["k"] == "fin"}
    for r in log:
        if r["k"] == "pin":
            assert fins[r["r"]]["tool"], r


def test_session_gate_bounds_active_sessions():
    s, _, log = run_mu({"MU_GATE": "2", "MU_GATE_IDLE_S": "0.01"}, tick_s=0.001)
    assert not [r for r in log if r["k"] == "err"], [r for r in log if r["k"] == "err"][:1]
    arr = {r["r"]: r for r in log if r["k"] == "arr"}
    fins = [r for r in log if r["k"] == "fin"]
    assert len(fins) == len(arr) == 20, (len(fins), len(arr))           # everyone finishes: no deadlock
    live, peak = set(), 0
    for r in log:                                   # engine order: admissions and finishes
        if r["k"] == "adm" and not r["res"]:
            live.add(arr[r["r"]]["c"])
            peak = max(peak, len(live))
        elif r["k"] == "fin":
            live.discard(arr[r["r"]]["c"])
    assert peak <= 2, f"{peak} sessions in service at once with MU_GATE=2"
    gates = [r for r in log if r["k"] == "gate"]
    assert any(g["on"] for g in gates) and any(not g["on"] for g in gates)
    assert all(g["active"] <= 2 for g in gates)


def test_external_id_and_chain():
    assert mu_scheduler.external_id("chatcmpl-MU-n16-S001~a1~00007~ld~root-0a1b2c3d") == "MU-n16-S001~a1~00007~ld~root"
    assert mu_scheduler.external_id("cmpl-MU-n16-S001~r0~00007~mx~root-0a1b2c3d") == "MU-n16-S001~r0~00007~mx~root"

    class R:
        request_id = "chatcmpl-MU-n16-S001~a2~00003~mx~root-0a1b2c3d"
        session_id = None
    assert mu_scheduler.chain_key(R) == ("MU-n16-S001~a2", "mx")
    R.session_id = "sess"
    assert mu_scheduler.chain_key(R) == ("sess", "mx")


if __name__ == "__main__":      # the project venv has no pytest; run the tests directly
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:        # report every failing test, not just the first
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
