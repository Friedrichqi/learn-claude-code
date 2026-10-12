#!/usr/bin/env python3
"""GPU selftests of the live EPIC connector on one H100 (plan §6), on an offline vLLM 0.31 engine with
the same flags as the live server (prefix caching on, eager, FlashAttention, no async scheduling):

  T1  load exactness: a prompt's rows saved, then loaded into a request that computes only the tail:
      the same greedy 32 tokens, max |delta first-token logprob| reported
  T2  rotation: layer-0 keys of a prompt saved at positions 0.. and of the same tokens shifted by 97;
      re-rotating the first by +97 matches the second (max relative error; values identical)
  R1  overrides: rows overridden in a forward are the stored rows re-rotated (saved back after the
      forward, max |diff| at the first, middle and last layer); R4: the pre-hooks fired on every layer
  R6  the same across chunked prefill (a prompt longer than max_num_batched_tokens)
  R8  decode saves: rows saved during decode equal a teacher-forced prefill's within bf16 tolerance,
      and loading them reproduces the next-step logprobs
  R9  birth-position rotation: one rotation by (p2 - b) vs two chained rotations (drift reported)
  R11 fail-open: a load naming slots outside the pool is recomputed; the engine keeps serving
  G5  null EPIC: epicinf (every reused run recomputed) gives prefix's greedy output on a spliced prompt
  T5  a plan whose exact prefix covers the whole prompt is capped (one token left to compute)
  T6  the prefix cache serving beyond the planned p0: the served rows are copied to the apc_save reserve
      bit-identically (the session entry takes them, epic_plan.absorb_apc)
  P1  host-pool gather: run-wise pinned copies agree bit for bit with a host-side gather (times both)
  T4  no contamination: a prefix request after all of the above equals the first run

    python research/16_epic_harness/epic_selftest.py [--gpu N]     # runs isolated, frees the GPU
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402

OUT = ec.DATA / "selftest"
POOL_ROWS = 120_000


def engine(max_batched: int = 8192, util: float = 0.92):
    os.environ["PYTHONPATH"] = f"{ec.HERE}:{os.environ.get('PYTHONPATH', '')}"
    os.environ["EPIC_POOL_ROWS"] = str(POOL_ROWS)
    os.environ["VLLM_ALLOW_INSECURE_SERIALIZATION"] = "1"
    from vllm import LLM
    graphs = ({"compilation_config": {"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY"}}
              if os.environ.get("EPIC_CUDAGRAPH") == "1" else {"enforce_eager": True})
    return LLM(model=ec.MODEL, max_model_len=32768, gpu_memory_utilization=util, **graphs,
               enable_prefix_caching=True, async_scheduling=False, seed=0, max_num_seqs=8,
               max_num_batched_tokens=max_batched, attention_backend="FLASH_ATTN",
               kv_transfer_config={"kv_connector": "EpicConnector", "kv_connector_module_path": "epic_connector",
                                   "kv_role": "kv_both", "kv_load_failure_policy": "fail",
                                   "kv_connector_extra_config": {"store_device": "cpu", "pool_rows": POOL_ROWS}})


def gen(llm, ids, splice=None, salt="t", max_tokens=32, logprobs=20, prompt_logprobs=None):
    from vllm import SamplingParams
    sp = SamplingParams(max_tokens=max_tokens, temperature=0.0, logprobs=logprobs,
                        prompt_logprobs=prompt_logprobs, skip_reading_prefix_cache=False,
                        extra_args={"kv_transfer_params": {"splice": splice}} if splice else None)
    out = llm.generate([{"prompt_token_ids": ids, "cache_salt": salt}], sp, use_tqdm=False)[0]
    return out


def first_lp(out) -> dict:
    o = out.outputs[0]
    return {t: lp.logprob for t, lp in o.logprobs[0].items()} if o.logprobs else {}


def save_all(n: int, slot_lo: int) -> list[list]:
    return [[0, n, slot_lo]]


def rows(llm, layer, slots, deltas=None):
    import epic_connector as ecn
    return llm.collective_rpc(ecn.epic_rows, args=(layer, list(map(int, slots)), deltas))[0]


def prompt_ids(llm, n_target: int, seed: int = 0) -> list[int]:
    """A real-text prompt of about n_target tokens (s15's own source), chat-formatted."""
    tok = llm.get_tokenizer()
    text = (ec.REPO / "s15_integrated_harness" / "code.py").read_text()[seed * 997:]
    ids = tok.encode(text, add_special_tokens=False)[:n_target]
    head = tok.encode("<|im_start|>user\nSummarize this code:\n", add_special_tokens=False)
    tail = tok.encode("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n", add_special_tokens=False)
    return head + ids + tail


def run_tests() -> dict:
    import torch

    import epic_connector as ecn
    import epic_plan as ep

    res: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    llm = engine()
    res["stats0"] = llm.collective_rpc(ecn.epic_stats)[0]

    # T1: load exactness ----------------------------------------------------------------------------
    x = prompt_ids(llm, 3000)
    n = len(x)
    a = gen(llm, x, {"p0": 0, "load": [], "override": [], "save": save_all(n, 0)}, salt="t1a")
    p0 = ((n - 1) // 16) * 16
    b = gen(llm, x, {"p0": p0, "load": [[0, p0, 0, 0]], "override": [], "save": []}, salt="t1b")
    # the reference for a load is vLLM's own prefix-cache path over the same split (any split
    # computation differs from a one-shot full prefill at bf16 level, prefix caching included)
    gen(llm, x[:p0 + 1], salt="t1apc", max_tokens=1)
    apc = gen(llm, x, salt="t1apc")
    lp_b = [{t: v.logprob for t, v in d.items()} for d in (b.outputs[0].logprobs or [])]
    lp_apc = [{t: v.logprob for t, v in d.items()} for d in (apc.outputs[0].logprobs or [])]
    worst = max((abs(da[t] - db[t]) for da, db in zip(lp_apc, lp_b) for t in set(da) & set(db)), default=None)
    res["T1"] = {"same_greedy_as_apc": list(apc.outputs[0].token_ids) == list(b.outputs[0].token_ids),
                 "max_dlogprob_vs_apc": worst, "apc_hit": apc.num_cached_tokens,
                 "same_greedy_as_full": list(a.outputs[0].token_ids) == list(b.outputs[0].token_ids),
                 "loaded": (b.kv_transfer_params or {}).get("epic_server", {}).get("loaded")}

    # T2: rotation at layer 0 -------------------------------------------------------------------------
    pad = llm.get_tokenizer().encode(" pad" * 97, add_special_tokens=False)[:97]
    d = len(pad)
    y = pad + x
    gen(llm, y, {"p0": 0, "load": [], "override": [], "save": save_all(len(y), 10_000)}, salt="t2", max_tokens=1)
    sl = list(range(0, n))
    rot = rows(llm, 0, sl, [d] * n)
    shifted = rows(llm, 0, list(range(10_000 + d, 10_000 + d + n)))
    hd = rot.shape[-1] // 2
    k_err = ((rot[..., :hd] - shifted[..., :hd]).norm() / shifted[..., :hd].norm()).item()
    k_raw = ((rows(llm, 0, sl)[..., :hd] - shifted[..., :hd]).norm() / shifted[..., :hd].norm()).item()
    v_same = bool(torch.equal(rot[..., hd:], shifted[..., hd:]))
    res["T2"] = {"k_rel_err_rotated": k_err, "k_rel_err_unrotated": k_raw, "values_identical": v_same}

    # R1/R4: overrides ---------------------------------------------------------------------------------
    # splice: y's tail (x at positions d..) reuses x's stored rows (slots 0..n) with delta +d; override
    # all reused positions except the first 32 (EPIC head) and save the whole prompt to slots 20_000..
    s0 = llm.collective_rpc(ecn.epic_stats)[0]
    lo, hi = d + 32, d + n - 1
    over = [[lo, hi, 32, d]]
    gen(llm, y, {"p0": 0, "load": [], "override": over, "save": save_all(len(y), 20_000)},
        salt="r1", max_tokens=1)
    s1 = llm.collective_rpc(ecn.epic_stats)[0]
    diffs = {}
    for layer in (0, 31, 63):
        want = rows(llm, layer, list(range(32, n - 1)), [d] * (n - 1 - 32))
        got = rows(llm, layer, list(range(20_000 + lo, 20_000 + hi)))
        diffs[layer] = float((want - got).abs().max())
    res["R1"] = {"max_abs_diff_by_layer": diffs, "ok": all(v == 0.0 for v in diffs.values())}
    res["R4"] = {"hook_calls": s1["hook_calls"] - s0["hook_calls"],
                 "overridden_tokens": s1["overridden_tokens"] - s0["overridden_tokens"],
                 "expected_tokens": hi - lo}

    # R6: chunked prefill (prompt > max_num_batched_tokens) ----------------------------------------------
    z = prompt_ids(llm, 12000, seed=3)
    m = len(z)
    gen(llm, z, {"p0": 0, "load": [], "override": [], "save": save_all(m, 40_000)}, salt="r6a", max_tokens=1)
    zz = pad + z
    lo6, hi6 = d + 16, d + m - 1
    gen(llm, zz, {"p0": 0, "load": [], "override": [[lo6, hi6, 40_016, d]],
                  "save": save_all(len(zz), 60_000)}, salt="r6b", max_tokens=1)
    want = rows(llm, 40, list(range(40_016, 40_000 + m - 1)), [d] * (hi6 - lo6))
    got = rows(llm, 40, list(range(60_000 + lo6, 60_000 + hi6)))
    res["R6"] = {"prompt": len(zz), "max_abs_diff_layer40": float((want - got).abs().max())}

    # R8: decode saves ---------------------------------------------------------------------------------
    out = gen(llm, x, {"p0": 0, "load": [], "override": [], "save": [[0, n, 80_000], [n, n + 64, 80_000 + n]]},
              salt="r8a", max_tokens=48)
    g = list(out.outputs[0].token_ids)
    k = len(g) - 1
    tf = gen(llm, x + g[:k], {"p0": 0, "load": [], "override": [], "save": save_all(n + k, 90_000)},
             salt="r8b", max_tokens=1)
    errs = []
    for layer in (0, 63):
        dec = rows(llm, layer, list(range(80_000 + n, 80_000 + n + k)))
        ref = rows(llm, layer, list(range(90_000 + n, 90_000 + n + k)))
        errs.append(((dec - ref).norm() / ref.norm()).item())
    p8 = ((n + k) // 16) * 16
    nxt = gen(llm, x + g[:k], {"p0": p8, "load": [[0, n, 80_000, 0], [n, p8, 80_000 + n, 0]] if p8 > n
                               else [[0, p8, 80_000, 0]], "override": [], "save": []},
              salt="r8c", max_tokens=1)
    l_tf, l_nx = first_lp(tf), first_lp(nxt)
    common = set(l_tf) & set(l_nx)
    res["R8"] = {"decode_vs_prefill_rel_err_layers_0_63": errs,
                 "next_top1_same": bool(l_tf) and bool(l_nx) and max(l_tf, key=l_tf.get) == max(l_nx, key=l_nx.get),
                 "next_max_dlogprob": max(abs(l_tf[t] - l_nx[t]) for t in common) if common else None}

    # R9: birth rotation vs chained rotation ------------------------------------------------------------
    once = rows(llm, 10, list(range(0, 512)), [900] * 512)
    twice_a = rows(llm, 10, list(range(0, 512)), [400] * 512)
    side = twice_a.clone()  # rotate the (bf16-rounded) result again by 500 on the CPU in fp32
    half = side.shape[-1] // 4
    theta = 1e6
    inv = 1.0 / (theta ** (torch.arange(0, half, dtype=torch.float64) * 2 / (2 * half)))
    ang = 500 * inv
    c, s_ = torch.cos(ang).float(), torch.sin(ang).float()
    kk = side[..., :2 * half].bfloat16().float()
    x1, x2 = kk[..., :half], kk[..., half:]
    chained = torch.cat((x1 * c - x2 * s_, x2 * c + x1 * s_), dim=-1).bfloat16().float()
    res["R9"] = {"rel_err_chained_vs_once": ((chained - once[..., :2 * half]).norm()
                                             / once[..., :2 * half].norm()).item()}

    # R11: fail-open load --------------------------------------------------------------------------------
    # a load naming slots outside the pool must fail the request visibly (kv_load_failure_policy
    # "fail"), never serve the unloaded blocks; the engine keeps serving (T4 runs after it)
    try:
        bad = gen(llm, x, {"p0": p0, "load": [[0, p0, POOL_ROWS + 10, 0]], "override": [], "save": []},
                  salt="r11")
        finish = str(bad.outputs[0].finish_reason) if bad.outputs else None
        failed = finish not in ("stop", "length")
    except Exception as exc:  # noqa: BLE001
        finish, failed = f"raised {type(exc).__name__}", True
    s2 = llm.collective_rpc(ecn.epic_stats)[0]
    res["R11"] = {"failed_visibly": failed, "finish": finish, "load_errors": s2["load_errors"]}

    # G5: null EPIC on a spliced prompt -------------------------------------------------------------------
    tok = llm.get_tokenizer()
    edit = tok.encode("<|im_start|>user\n[12 messages archived at .transcripts/transcript_00003.jsonl]<|im_end|>\n",
                      add_special_tokens=False)
    old = x[:-12]
    new = x[:200] + edit + x[260:]
    ref = gen(llm, new, salt="g5-prefix")
    gen(llm, old, {"p0": 0, "load": [], "override": [], "save": save_all(len(old), 100_000)}, salt="g5-old",
        max_tokens=1)
    entry = ep.first_entry(old, list(range(100_000, 100_000 + len(old))), len(old), 0)
    outs = {}
    for arm in ("epicinf", "shift", "epic32", "epic128"):
        plan = ep.plan_call(arm, entry, new, tok.convert_tokens_to_ids("<|im_start|>"))
        o = gen(llm, new, {"p0": plan.p0, "load": plan.load, "override": plan.override, "save": []},
                salt=f"g5-{arm}")
        outs[arm] = {"same_greedy_as_prefix": list(o.outputs[0].token_ids) == list(ref.outputs[0].token_ids),
                     "S": plan.stats["S"], "H": plan.stats["H"]}
    res["G5"] = outs

    # T5: a plan whose exact prefix spans the whole prompt (a prompt-only request after its own rows were
    # saved) must be capped by the connector to leave one token to compute, not crash the scheduler
    whole = gen(llm, x, {"p0": n, "load": [[0, n, 0, 0]], "override": [], "save": []}, salt="t5", max_tokens=8)
    res["T5"] = {"served": bool(whole.outputs and whole.outputs[0].token_ids),
                 "apc_or_loaded": whole.num_cached_tokens}

    # T6: the prefix cache serves beyond the planned p0 (an older sequence of the salt matches further):
    # the rows it served there are copied to the apc_save reserve, bit-identical to the cached rows
    gen(llm, x, {"p0": 0, "load": [], "override": [], "save": save_all(n, 110_000)}, salt="t6", max_tokens=1)
    other = x[:2000] + pad + x[2000:2400]
    p6 = 1904
    b6 = gen(llm, other, {"p0": p6, "load": [], "override": [],
                          "save": [[p6, len(other), 115_000]], "apc_save": [[p6, p6 + 256, 114_000]]},
             salt="t6", max_tokens=1)
    srv = (b6.kv_transfer_params or {}).get("epic_server", {})
    got = rows(llm, 33, list(range(114_000, 114_000 + 96)))
    want = rows(llm, 33, list(range(110_000 + p6, 110_000 + 2000)))
    res["T6"] = {"apc_hit": srv.get("apc_hit"), "overshoot": srv.get("overshoot"), "apc_saved": srv.get("apc_saved"),
                 "max_abs_diff_layer33": float((got - want).abs().max())}

    # P1: host-pool gather speed (not a gate): 12K rows in one run, and the same rows split into 40 runs
    res["P1"] = {"one_run": llm.collective_rpc(ecn.epic_gather_bench, args=(list(range(0, 12_000)),))[0],
                 "forty_runs": llm.collective_rpc(ecn.epic_gather_bench, args=(
                     [r for k in range(40) for r in range(k * 2_000, k * 2_000 + 300)],))[0]}

    # T4: no contamination -------------------------------------------------------------------------------
    again = gen(llm, x, salt="t1a-plain")
    res["T4"] = {"same_greedy_as_T1": list(again.outputs[0].token_ids) == list(a.outputs[0].token_ids)}
    res["stats_end"] = llm.collective_rpc(ecn.epic_stats)[0]
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return res


def gates(res: dict) -> dict:
    return {
        "T1": res["T1"]["same_greedy_as_apc"] and res["T1"]["max_dlogprob_vs_apc"] == 0.0,
        "T2": res["T2"]["k_rel_err_rotated"] < 2e-3 and res["T2"]["values_identical"]
        and res["T2"]["k_rel_err_unrotated"] > 0.05,
        "R1": res["R1"]["ok"],
        "R4": res["R4"]["hook_calls"] >= 64 and res["R4"]["overridden_tokens"] >= res["R4"]["expected_tokens"],
        "R6": res["R6"]["max_abs_diff_layer40"] == 0.0,
        "R8": max(res["R8"]["decode_vs_prefill_rel_err_layers_0_63"]) < 2e-2 and res["R8"]["next_top1_same"],
        "R11": res["R11"]["load_errors"] >= 1 and res["R11"]["failed_visibly"],
        "G5": res["G5"]["epicinf"]["same_greedy_as_prefix"],
        "T4": res["T4"]["same_greedy_as_T1"],
        "T5": res["T5"]["served"],
        "T6": res["T6"]["overshoot"] == 96 and res["T6"]["apc_saved"] == 96
        and res["T6"]["max_abs_diff_layer33"] == 0.0,
        "P1": res["P1"]["one_run"]["identical"] and res["P1"]["forty_runs"]["identical"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gpu", type=int, default=None)
    ap.add_argument("--inner", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.inner:
        gpu = ec.pick_gpu(args.gpu)
        code = ec.run_isolated(gpu, [ec.PYTHON, __file__, "--inner"], OUT / "selftest.log",
                               env_extra={"VLLM_ALLOW_INSECURE_SERIALIZATION": "1"})
        path = OUT / "selftest.json"
        if path.exists():
            print(path.read_text())
        raise SystemExit(code)
    res = run_tests()
    res["gates"] = gates(res)
    res["ok"] = all(res["gates"].values())
    (OUT / "selftest.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print(json.dumps(res["gates"]), "ok" if res["ok"] else "FAILED")


if __name__ == "__main__":
    main()
