#!/usr/bin/env python3
"""Latency of Part B's repair arms: time to first token (TTFT) of the next round when only what an
arm recomputes is computed (offline vLLM, one H200 per model, TP=1).

Part B's quality numbers come from an emulation that computes every token after the exact prefix and
overwrites the unselected ones, so its run time says nothing about speed. This script times, for a
sample of Part A's events (the natural tail: the real request after the compaction), the request a
real implementation of each arm would run:

  recompute  full prefill of the prompt (what the harness does today)
  prefix     prefix caching: the unchanged prefix loaded, everything after the first edit computed
  shift      Part A's RoPE cure: the prefix and every surviving run loaded (re-rotated); only the
             fresh text and the tail computed
  epic32     shift, plus the first 32 / 128 tokens of every reused run computed
  epic128
  blend05    shift, plus 5% / 15% of the reused tokens computed, plus CacheBlend's selection pass:
  blend15      every token after the prefix through layer 0 and the check layer's projections

How a request is built ("shape-equivalent"): the connector loads the arm's reused KV (exact prefix
rows as they are, surviving rows re-rotated, from the previous call's KV kept on the GPU) into the
first positions, and the tokens the arm computes follow them. Each request therefore has the arm's
real numbers of loaded and computed tokens and the real context length, and does the real load and
rotation. One thing differs from computing the tokens in place: a computed token's attention covers
the whole context instead of the part before its own position, an upper bound (at most twofold) on
its attention work. Which reused tokens EPIC or CacheBlend pick does not change the work, only how
many, so the counts follow Part B's rules (EPIC's run heads; ceil(r x reused tokens)).

CacheBlend's selection pass is timed with CUDA events inside the prefix-caching request, which
computes exactly the tokens after the prefix: from the end of the connector load to the entry of
layer 1's attention (embedding, layer 0, layer 1's norm and QKV projections) for all of those tokens.

Each request is timed end to end in the driver (llm.generate of one request, max_tokens=1) on an
otherwise idle engine, three times in rotating arm order; the per-event value is the median.

    python research/10_rope_shift/latency.py run --model qwen32b [--events 200]
    python research/10_rope_shift/latency.py report
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # research/
import _paths  # noqa: F401,E402

# arms sets PYTHONPATH (the engine imports splice_connector by name) and the serialization flag
from arms import MAX_NEW, RunCache, plan_stats, sampling, splice_plan  # noqa: E402
from events import history_after  # noqa: E402
from render import MODELS, Renderer  # noqa: E402
from repair import DATA, LIVE, load_events, region, reuse_triplets, run_heads  # noqa: E402

OUT = DATA / "latency"
ARMS = ["recompute", "prefix", "shift", "epic32", "epic128", "blend05", "blend15"]
REPS = 3


# -- requests (CPU) ---------------------------------------------------------------------------------

def natural(event: dict, runs: RunCache, renderer: Renderer, max_model_len: int) -> dict | None:
    """The old prompt (call k-1's history and reply) and the real next request (call k), rendered
    and spliced as in arms.prepare; None when the request does not fit (Parts A and B skip it too)."""
    by_index = runs.get(event["label"])
    prev, cur = by_index[event["prev_call"]], by_index[event["cur_call"]]
    _, old = renderer.render({"system": prev.get("system"), "tools": prev.get("tools"),
                              "messages": history_after(prev)}, add_generation_prompt=False)
    _, new = renderer.render({"system": cur.get("system"), "tools": cur.get("tools"),
                              "messages": cur["messages"]})
    if len(new) > max_model_len - MAX_NEW["natural"] - 8 or len(old) > max_model_len - 8:
        return None
    segs = splice_plan(old, new)
    return {"old": old, "new": new, "segs": segs, "stats": plan_stats(old, new, segs)}


def computed_sets(t: dict, seed: str) -> dict[str, set[int] | None]:
    """Positions of `new` each arm computes (None: recompute computes everything, loads nothing)."""
    lo, hi = region(t)
    n = len(t["new"])
    reused = reuse_triplets(t["segs"], lo, hi)[0]
    rset = set(reused)
    fresh = {p for p in range(lo, n) if p not in rset}
    rng = random.Random(f"10rope-latency-{seed}")
    out: dict[str, set[int] | None] = {"recompute": None, "prefix": set(range(lo, n)), "shift": fresh}
    for k in (32, 128):
        out[f"epic{k}"] = fresh | (run_heads(t["segs"], k, lo, hi) & rset)
    for ratio, name in ((0.05, "blend05"), (0.15, "blend15")):
        out[name] = fresh | set(rng.sample(reused, math.ceil(ratio * len(reused))))
    return out


def layout(t: dict, computed: set[int], key: str) -> tuple[list[int], dict | None]:
    """Token ids and connector plan of the shape-equivalent request: every position not computed is
    loaded from store entry `key` (the old prompt's KV) into the first positions, exact prefix rows
    unrotated and surviving rows re-rotated to their new place; the computed tokens follow."""
    new = t["new"]
    src: list[int | None] = [None] * len(new)
    for seg in t["segs"]:
        if seg[0] == "reuse":
            j1, j2, i1 = seg[1], seg[2], seg[3]
            src[j1:j2] = range(i1, i1 + (j2 - j1))
    loaded = [p for p in range(len(new)) if p not in computed]
    if any(src[p] is None for p in loaded):
        raise ValueError("a position that is not computed has no reusable KV")
    ids = [new[p] for p in loaded] + [new[p] for p in sorted(computed)]
    segs: list[list] = []
    for q, p in enumerate(loaded):
        s = src[p]
        d = q - s
        last = segs[-1] if segs else None
        if last and last[2] == s and last[3] + (last[2] - last[1]) == q and last[4] == d:
            last[2] += 1
        else:
            segs.append([key, s, s + 1, q, d, d != 0])
    return ids, ({"load": {"n": len(loaded), "segs": segs}} if loaded else None)


# -- timing (GPU) -----------------------------------------------------------------------------------

def make_latency_engine(spec: dict, gpu_util: float, max_model_len: int):
    """Part B's engine settings, with a token budget that prefills any prompt in one step."""
    from vllm import LLM
    return LLM(model=spec["hf"], tokenizer=spec["hf"], tensor_parallel_size=1,
               max_model_len=max_model_len, max_num_batched_tokens=max_model_len,
               enable_prefix_caching=False, enforce_eager=True, gpu_memory_utilization=gpu_util,
               max_num_seqs=8, seed=0,
               kv_transfer_config={"kv_connector": "SpliceConnector",
                                   "kv_connector_module_path": "splice_connector",
                                   "kv_role": "kv_both",
                                   "kv_connector_extra_config": {"store_device": "cuda"}})


def timed(llm, ids: list[int], splice: dict | None) -> tuple[float, dict]:
    """Wall-clock milliseconds to the first token of one request, and the connector's GPU timings."""
    from vllm import SamplingParams, TokensPrompt
    from splice_connector import splice_timing
    extra = {"kv_transfer_params": {"splice": splice}} if splice else None
    sp = SamplingParams(temperature=0.0, max_tokens=1, detokenize=False, extra_args=extra, seed=0)
    t0 = time.perf_counter()
    llm.generate([TokensPrompt(prompt_token_ids=list(ids))], [sp], use_tqdm=False)
    ms = (time.perf_counter() - t0) * 1e3
    return ms, llm.collective_rpc(splice_timing)[0]


def run(args) -> None:
    from vllm import TokensPrompt
    from vllm import __version__ as vllm_version
    from splice_connector import splice_free, splice_set_timing, splice_stats, splice_timing

    spec = MODELS[args.model]
    events = load_events(Path(args.events_file))
    sample = sorted(random.Random(args.seed).sample(events, min(args.events, len(events))),
                    key=lambda e: e["event_id"])
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"latency_{args.model}.jsonl"
    done = set()
    if out.exists():
        for line in open(out, encoding="utf-8"):
            try:
                done.add(json.loads(line)["event_id"])
            except json.JSONDecodeError:
                continue
    todo = [e for e in sample if e["event_id"] not in done]
    print(f"[latency] {args.model}: {len(sample)} sampled events, {len(todo)} to do", flush=True)
    if not todo:
        return
    renderer = Renderer(spec)
    runs = RunCache(Path(args.runs_dir))
    llm = make_latency_engine(spec, args.gpu_util, args.max_model_len)
    llm.collective_rpc(splice_set_timing, args=(True,))
    stats = llm.collective_rpc(splice_stats)[0]

    first = natural(todo[0], runs, renderer, args.max_model_len) or {"new": list(range(100, 116))}
    floor = [timed(llm, first["new"][:16], None)[0] for _ in range(9)]
    meta = {"model": args.model, "hf": spec["hf"], "device": stats.get("device"), "vllm": vllm_version,
            "tp": 1, "enforce_eager": True, "max_num_batched_tokens": args.max_model_len,
            "gpu_util": args.gpu_util, "reps": REPS, "seed": args.seed, "events_sampled": len(sample),
            "floor_ms_16_tokens": round(statistics.median(floor[2:]), 2), "kv_view": stats.get("view_shape")}
    (OUT / f"latency_{args.model}_meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"[latency] {meta}", flush=True)

    warmed = False
    with out.open("a", encoding="utf-8") as sink:
        for e in todo:
            t0 = time.time()
            t = natural(e, runs, renderer, args.max_model_len)
            if t is None:
                print(f"[latency] {e['event_id']}: does not fit {args.max_model_len}, skipped", flush=True)
                continue
            key = f"{e['event_id']}/old"
            # the previous call's KV, kept by the server (not timed)
            llm.generate([TokensPrompt(prompt_token_ids=t["old"])],
                         [sampling(1, splice={"save": {"key": key, "lo": 0, "hi": len(t["old"])}})],
                         use_tqdm=False)
            llm.collective_rpc(splice_timing)
            sets = computed_sets(t, e["event_id"])
            reqs = {arm: (layout(t, comp, key) if comp is not None else (t["new"], None))
                    for arm, comp in sets.items()}
            if not warmed:                      # first requests pay one-time costs; not recorded
                for arm in ARMS:
                    timed(llm, *reqs[arm])
                warmed = True
            reps = {arm: [] for arm in ARMS}
            for rep in range(REPS):
                for arm in ARMS[rep:] + ARMS[:rep]:
                    ms, tim = timed(llm, *reqs[arm])
                    fws = tim["forwards"]
                    reps[arm].append({"ttft_ms": round(ms, 2), "load_ms": round(sum(tim["load_ms"]), 3),
                                      "early_ms": fws[0]["early_ms"] if fws else None,
                                      "layers_ms": round(sum(f["layers_ms"] for f in fws), 3),
                                      "forwards": len(fws)})
            llm.collective_rpc(splice_free, args=([key],))
            lo, hi = region(t)
            rec = {"event_id": e["event_id"], "label": e["label"], "model": args.model, "kinds": e["kinds"],
                   "prompt": len(t["new"]), "old": len(t["old"]), "prefix": lo, "tail_start": hi,
                   "reused": len(reuse_triplets(t["segs"], lo, hi)[0]),
                   "computed": {arm: (len(c) if c is not None else len(t["new"])) for arm, c in sets.items()},
                   "reps": reps}
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            med = {arm: statistics.median(x["ttft_ms"] for x in reps[arm]) for arm in ARMS}
            print(f"[latency] {e['event_id']} prompt {len(t['new'])}: "
                  + " ".join(f"{a}={med[a]:.0f}" for a in ARMS) + f" ms ({time.time() - t0:.0f}s)", flush=True)


# -- report (CPU) -----------------------------------------------------------------------------------

def per_event(rec: dict) -> dict:
    """Median over repetitions; CacheBlend = its selective request + the selection pass measured in
    the same event's prefix-caching request."""
    med = {arm: statistics.median(x["ttft_ms"] for x in rec["reps"][arm]) for arm in ARMS}
    early = [x["early_ms"] for x in rec["reps"]["prefix"] if x["early_ms"] is not None]
    sel = statistics.median(early) if early else 0.0
    out = {arm: med[arm] for arm in ARMS}
    for arm in ("blend05", "blend15"):
        out[arm] = med[arm] + sel
    out["selection_ms"] = sel
    out["load_ms"] = {arm: statistics.median(x["load_ms"] for x in rec["reps"][arm]) for arm in ARMS}
    return out


def report(args) -> None:
    from analyze import boot_mean, table
    names = {"qwen32b": "Qwen3-32B (GQA)", "qwen8b": "Qwen3-8B (GQA)", "glm47flash": "GLM-4.7-Flash (MLA)"}
    md = ["# 10_rope_shift Part B latency: time to first token of the next round", "",
          "Generated by `latency.py report` from `data/rope_shift_repair/latency/latency_*.jsonl`. "
          "One request at a time on an idle engine; per event the median of three runs; CacheBlend "
          "includes its selection pass. Means carry a bootstrap interval over runs.", ""]
    summary = {}
    for m in args.models.split(","):
        path = OUT / f"latency_{m}.jsonl"
        if not path.exists():
            continue
        recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        meta = json.loads((OUT / f"latency_{m}_meta.json").read_text())
        evs = [per_event(r) for r in recs]
        labels = [r["label"] for r in recs]
        rows, summary[m] = [], {}
        for arm in ARMS:
            ttft = [e[arm] for e in evs]
            share = [e[arm] / e["recompute"] for e in evs]
            extra = [e[arm] - e["shift"] for e in evs]
            comp = [r["computed"][arm] for r in recs]
            mean, lo, hi = boot_mean(ttft, labels)
            # the cost of a few hundred extra tokens is step-shaped on some kernels (GLM's MoE), so the
            # mean per-event difference, not its median, is the average cost
            x_mean, x_lo, x_hi = boot_mean(extra, labels)
            load = statistics.median(e["load_ms"][arm] for e in evs)
            summary[m][arm] = {"ttft_median": statistics.median(ttft), "ttft_mean": mean,
                               "share_median": statistics.median(share), "extra_vs_shift_mean": x_mean,
                               "computed_mean": sum(comp) / len(comp), "load_median": load}
            rows.append([arm, f"{sum(comp) / len(comp):,.0f}", f"{statistics.median(ttft):,.0f}",
                         f"{mean:,.0f} [{lo:,.0f}, {hi:,.0f}]", f"{100 * statistics.median(share):.0f}%",
                         "—" if arm in ("recompute", "prefix", "shift")
                         else f"+{x_mean:,.1f} [{x_lo:,.1f}, {x_hi:,.1f}]",
                         "—" if arm == "recompute" else f"{load:.1f}"])
        sel = statistics.median(e["selection_ms"] for e in evs)
        summary[m]["selection_median"] = sel
        summary[m]["events"] = len(recs)
        summary[m]["prompt_mean"] = sum(r["prompt"] for r in recs) / len(recs)
        md += [f"## {names.get(m, m)}", "",
               f"{len(recs)} events (mean prompt {summary[m]['prompt_mean']:,.0f} tokens) on {meta.get('device')}, "
               f"vLLM {meta.get('vllm')}, TP=1, eager; a 16-token request takes {meta.get('floor_ms_16_tokens')} ms "
               f"end to end. CacheBlend's selection pass (layer 0 and layer 1's projections for every token "
               f"after the prefix): median {sel:,.1f} ms, included in the blend rows.", "",
               table(["arm", "tokens computed (mean)", "TTFT median (ms)", "TTFT mean (ms)",
                      "median share of recompute's TTFT", "mean extra over shift (ms)",
                      "connector load, median (ms)"], rows), ""]
    (OUT / "latency_tables.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "latency_tables.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print("\n".join(md))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--model", default="qwen32b", choices=sorted(MODELS))
    ap.add_argument("--models", default="qwen32b,qwen8b,glm47flash", help="report: models to include")
    ap.add_argument("--events", type=int, default=200, help="events sampled (the same for every model)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--gpu-util", type=float, default=0.62)
    ap.add_argument("--max-model-len", type=int, default=40960)
    ap.add_argument("--events-file", default=str(LIVE / "events.jsonl"))
    ap.add_argument("--runs-dir", default=str(LIVE / "runs"))
    args = ap.parse_args()
    run(args) if args.cmd == "run" else report(args)


if __name__ == "__main__":
    main()
