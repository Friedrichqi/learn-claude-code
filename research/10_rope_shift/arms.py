#!/usr/bin/env python3
"""Run the RoPE-shift arms over the sampled compaction events on offline vLLM (GPU).

For one model and every sampled event (events.py) and tail (natural / probe / retention):

  recompute      full prefill of the compacted request (the reference; what the harness does today)
  recompute_alt  the same prompt in a different batch (numerical noise floor; 1 event in 4)
  prefix         standard prefix caching: the unchanged prefix loaded, the rest recomputed (natural)
  shift          THE RoPE CURE: every token run that survives the edit keeps its KV from call k-1,
                 keys re-rotated by the position change delta; only fresh tokens are prefilled
  norope         the same reuse without the rotation (the no-cure control)
  oracle         this step's compaction undone (call k-1's history + the new messages)

The splice (splice_connector.py) is staged: prefill `old` and save its KV; for each fresh segment
before the tail, prefill it on top of the loaded, spliced prefix and save it; the final request loads
everything up to the tail and prefills only the tail. Greedy decoding throughout. The reference
distribution is recompute's own decode-step top-20 (logprobs=20); every loading arm (shift, norope,
prefix, oracle) gets a teacher-forced pass -- its prefix loaded as in its generation, then its tail +
recompute's output computed with prompt_logprobs=20 -- giving per-position top-1 agreement, the NLL of
recompute's tokens and a top-20 KL. The oracle reuses call k-1's KV as its (exact) prefix. Records
are checkpointed per (event, tail, arm) in --out, so reruns resume.

    python research/10_rope_shift/arms.py selftest --model qwen8b
    python research/10_rope_shift/arms.py run --model qwen32b --tp 2 [--shard 0 --shards 4]
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # research/
import _paths  # noqa: F401,E402

os.environ.setdefault("HF_HOME", "/projects/co/jlin4/agenticllm/hf_cache")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# the engine (and its workers under TP) must import splice_connector by name
os.environ["PYTHONPATH"] = f"{HERE}{os.pathsep}{os.environ.get('PYTHONPATH', '')}".rstrip(os.pathsep)
# collective_rpc sends splice_free / splice_stats (module-level functions, pickled by reference) to
# worker processes; vLLM's msgpack serializer refuses callables unless pickle fallback is allowed.
# Everything here is local and trusted.
os.environ.setdefault("VLLM_ALLOW_INSECURE_SERIALIZATION", "1")

from events import history_after, oracle_messages, with_probe  # noqa: E402
from render import MODELS, Renderer, open_jsonl  # noqa: E402

DATA = HERE / "data" / "rope_shift_live"
MIN_RUN = 16                    # reuse runs shorter than this are recomputed (block granularity)
TOPK = 20
MAX_NEW = {"natural": 512, "probe": 512, "retention": 48}
ALT_EVERY = 4


# -- plans ------------------------------------------------------------------------------------------

def splice_plan(old: list[int], new: list[int]) -> list[tuple]:
    """Segments tiling `new`: ("reuse", j1, j2, i1) copied from old[i1:i1+(j2-j1)], or ("fresh", j1, j2).
    The last token is always fresh (vLLM must compute at least one prompt token)."""
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    segs: list[list] = []

    def add_fresh(j1, j2):
        if j1 >= j2:
            return
        if segs and segs[-1][0] == "fresh" and segs[-1][2] == j1:
            segs[-1][2] = j2
        else:
            segs.append(["fresh", j1, j2])

    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" and j2 - j1 >= MIN_RUN:
            segs.append(["reuse", j1, j2, i1])
        else:
            add_fresh(j1, j2)
    if segs and segs[-1][0] == "reuse":
        last = segs[-1]
        last[2] -= 1
        if last[2] - last[1] < MIN_RUN:
            segs.pop()
            add_fresh(last[1], len(new))
        else:
            add_fresh(last[2], len(new))
    return [tuple(s) for s in segs]


def plan_stats(old: list[int], new: list[int], segs: list[tuple]) -> dict:
    reuse = [s for s in segs if s[0] == "reuse"]
    fresh = [s for s in segs if s[0] == "fresh"]
    deltas = [s[1] - s[3] for s in reuse]
    prefix = reuse[0][2] if reuse and reuse[0][1] == 0 and reuse[0][3] == 0 else 0
    last_fresh = fresh[-1][1] if fresh else len(new)
    return {"old_len": len(old), "new_len": len(new), "prefix_len": prefix,
            "reused": sum(s[2] - s[1] for s in reuse), "fresh": sum(s[2] - s[1] for s in fresh),
            "reuse_runs": len(reuse), "stages": max(0, len(fresh) - 1),
            "reused_after_edit": sum(s[2] - s[1] for s in reuse if s[1] >= prefix),
            "delta_min": min(deltas) if deltas else 0, "delta_max": max(deltas) if deltas else 0,
            "delta_absmean": round(sum(abs(d) for d in deltas) / len(deltas), 1) if deltas else 0,
            "shifted_tokens": sum(s[2] - s[1] for s in reuse if s[1] != s[3]),
            "tail_start": last_fresh}


def load_segments(eid: str, arm: str, segs: list[tuple], upto: int) -> list[list]:
    """kv_transfer segments covering new[0:upto] for arm shift / norope / prefix."""
    out = []
    for seg in segs:
        if seg[1] >= upto:
            break
        if seg[0] == "reuse":
            j1, j2, i1 = seg[1], min(seg[2], upto), seg[3]
            delta = j1 - i1
            out.append([f"{eid}/old", i1, i1 + (j2 - j1), j1, delta, arm == "shift"])
        else:
            j1, j2 = seg[1], min(seg[2], upto)
            out.append([f"{eid}/{arm}/f{seg[1]}-{seg[2]}", j1, j2, j1, 0, False])
    return out


# -- engine -----------------------------------------------------------------------------------------

def make_engine(spec: dict, tp: int, gpu_util: float, max_num_seqs: int, max_model_len: int):
    from vllm import LLM
    return LLM(model=spec["hf"], tokenizer=spec["hf"], tensor_parallel_size=tp,
               max_model_len=max_model_len, enable_prefix_caching=False, enforce_eager=True,
               gpu_memory_utilization=gpu_util, max_num_seqs=max_num_seqs, seed=0,
               kv_transfer_config={"kv_connector": "SpliceConnector",
                                   "kv_connector_module_path": "splice_connector",
                                   "kv_role": "kv_both",
                                   "kv_connector_extra_config": {"store_device": "cuda"}})


def sampling(max_tokens: int, *, splice: dict | None = None, logprobs: int | None = None,
             prompt_logprobs: int | None = None):
    from vllm import SamplingParams
    extra = {"kv_transfer_params": {"splice": splice}} if splice else None
    # teacher-forced passes need token ids and logprobs only: skip detokenizing ~P x 21 entries
    return SamplingParams(temperature=0.0, max_tokens=max_tokens, logprobs=logprobs,
                          prompt_logprobs=prompt_logprobs, extra_args=extra, seed=0,
                          detokenize=prompt_logprobs is None)


def generate(llm, batch: list[tuple]) -> list:
    """batch: [(token_ids, SamplingParams)] -> RequestOutputs in order."""
    from vllm import TokensPrompt
    if not batch:
        return []
    prompts = [TokensPrompt(prompt_token_ids=list(ids)) for ids, _ in batch]
    return llm.generate(prompts, [sp for _, sp in batch], use_tqdm=False)


def prompt_lp(out, index: int):
    """Logprob dict for prompt token `index`, whatever prefix was loaded (V2 returns P - N rows)."""
    rows = out.prompt_logprobs or []
    offset = len(out.prompt_token_ids) - len(rows)
    k = index - offset
    return rows[k] if 0 <= k < len(rows) else None


def tf_metrics(ref_out, arm_out, start: int, ref_ids: list[int]) -> dict:
    """Per-position comparison of the arm's teacher-forced distribution with recompute's.

    The reference distribution at position t is recompute's own decode-step top-20 (its greedy
    output IS the reference, so the context matches); the arm's is its prompt_logprobs at the same
    token, from a pass that loads the arm's prefix and computes only its tail + the reference."""
    kls, top1, nll_arm, nll_ref = [], 0, 0.0, 0.0
    ref_lps = ref_out.outputs[0].logprobs or []
    for t, tok in enumerate(ref_ids):
        p = ref_lps[t] if t < len(ref_lps) else None
        q = prompt_lp(arm_out, start + t)
        if not p or not q:
            return {"tf_error": f"missing prompt logprob at {t}"}
        p_best = max(p.items(), key=lambda kv: kv[1].logprob)[0]
        q_best = max(q.items(), key=lambda kv: kv[1].logprob)[0]
        top1 += p_best == q_best
        nll_ref -= p[tok].logprob
        nll_arm -= q[tok].logprob
        floor = min(v.logprob for v in q.values())
        ptop = sorted(p.items(), key=lambda kv: -kv[1].logprob)[:TOPK]
        mass = sum(math.exp(v.logprob) for _, v in ptop)
        kl = 0.0
        for tid, v in ptop:
            pv = math.exp(v.logprob) / mass
            qv = q[tid].logprob if tid in q else floor
            kl += pv * (math.log(pv) - qv)
        kls.append(max(kl, 0.0))
    n = len(ref_ids)
    return {"tf_n": n, "top1_agree": round(top1 / n, 5) if n else None,
            "nll_ref": round(nll_ref / n, 5) if n else None, "nll_arm": round(nll_arm / n, 5) if n else None,
            "kl_mean": round(sum(kls) / n, 6) if n else None, "kl_max": round(max(kls), 5) if n else None,
            "kl_first": round(kls[0], 5) if n else None}


# -- per-event preparation --------------------------------------------------------------------------

class RunCache:
    def __init__(self, runs_dir: Path):
        self.runs_dir, self.label, self.by_index = runs_dir, None, {}

    def get(self, label: str) -> dict:
        if label != self.label:
            path = self.runs_dir / label / f"{label}.requests.jsonl.xz"
            if not path.exists():
                path = self.runs_dir / label / f"{label}.requests.jsonl"
            self.by_index = {r["call_index"]: r for r in open_jsonl(path)}
            self.label = label
        return self.by_index


def prepare(event: dict, runs: RunCache, renderer: Renderer, max_model_len: int = 40960) -> dict:
    by_index = runs.get(event["label"])
    prev, cur = by_index[event["prev_call"]], by_index[event["cur_call"]]
    old_req = {"system": prev.get("system"), "tools": prev.get("tools"), "messages": history_after(prev)}
    _, old_ids = renderer.render(old_req, add_generation_prompt=False)
    oracle_base = oracle_messages(prev, cur, event["appended_from"])
    tails = {"natural": (cur["messages"], oracle_base, None)}
    for name in ("probe", "retention"):
        probe = event.get(name)
        if probe:
            tails[name] = (with_probe(cur["messages"], probe["text"]),
                           with_probe(oracle_base, probe["text"]), probe)
    prepared = {"event": event, "eid": event["event_id"], "old": old_ids, "tails": {}}
    for name, (messages, oracle_msgs, probe) in tails.items():
        base = {"system": cur.get("system"), "tools": cur.get("tools")}
        _, new_ids = renderer.render({**base, "messages": messages})
        _, oracle_ids = renderer.render({**base, "messages": oracle_msgs})
        # vLLM rejects a whole batch when one request cannot fit; drop such a tail instead
        budget = max_model_len - MAX_NEW[name] - 8
        if max(len(new_ids), len(oracle_ids)) > budget or len(old_ids) > max_model_len - 8:
            print(f"[arms] {event['event_id']} {name}: prompt too long for {max_model_len}, tail skipped",
                  flush=True)
            continue
        segs = splice_plan(old_ids, new_ids)
        prepared["tails"][name] = {"new": new_ids, "oracle": oracle_ids, "segs": segs,
                                   "stats": plan_stats(old_ids, new_ids, segs), "probe": probe}
    if "natural" not in prepared["tails"]:
        raise ValueError("natural tail does not fit the model length")
    return prepared


# -- the arms ---------------------------------------------------------------------------------------

def common_prefix(a: list[int], b: list[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def run_chunk(llm, chunk: list[dict], model: str, sink, done: set, alt_ids: set) -> int:
    from splice_connector import splice_free

    written = 0
    t0 = time.time()
    # wave A: save old KV; recompute generations with top-20 logprobs (the reference)
    batch, keys = [], []
    for p in chunk:
        batch.append((p["old"], sampling(1, splice={"save": {"key": f"{p['eid']}/old", "lo": 0,
                                                              "hi": len(p["old"])}})))
        keys.append(("save", p["eid"], None))
        for tail, t in p["tails"].items():
            batch.append((t["new"], sampling(MAX_NEW[tail], logprobs=TOPK)))
            keys.append(("recompute", p["eid"], tail))
    outs_a = dict(zip(keys, generate(llm, batch)))

    # stage waves: fresh segments before the tail, for shift and norope (shared across tails)
    max_stages = max((len([s for s in t["segs"] if s[0] == "fresh"]) - 1
                      for p in chunk for t in p["tails"].values()), default=0)
    for k in range(max_stages):
        batch, seen = [], set()
        for p in chunk:
            for t in p["tails"].values():
                fresh = [s for s in t["segs"] if s[0] == "fresh"][:-1]
                if k >= len(fresh):
                    continue
                seg = fresh[k]
                for arm in ("shift", "norope"):
                    key = f"{p['eid']}/{arm}/f{seg[1]}-{seg[2]}"
                    if key in seen:
                        continue
                    seen.add(key)
                    splice = {"save": {"key": key, "lo": seg[1], "hi": seg[2]}}
                    if seg[1] > 0:
                        splice["load"] = {"n": seg[1], "segs": load_segments(p["eid"], arm, t["segs"], seg[1])}
                    batch.append((t["new"][:seg[2]], sampling(1, splice=splice)))
        generate(llm, batch)

    # final wave: the loading arms (shift, norope, prefix, oracle) and the batch-noise floor
    finals = {}          # (arm, eid, tail) -> (prompt ids, splice or None)
    for p in chunk:
        for tail, t in p["tails"].items():
            n = t["stats"]["tail_start"]
            if n > 0:
                for arm in ("shift", "norope"):
                    finals[(arm, p["eid"], tail)] = (t["new"], {"load": {
                        "n": n, "segs": load_segments(p["eid"], arm, t["segs"], n)}})
            if tail == "natural" and t["stats"]["prefix_len"] > 0:
                n0 = t["stats"]["prefix_len"]
                finals[("prefix", p["eid"], tail)] = (t["new"], {"load": {
                    "n": n0, "segs": [[f"{p['eid']}/old", 0, n0, 0, 0, False]]}})
            # oracle = call k-1's history (exactly `old`, reused as is) + the new messages
            lo = min(common_prefix(p["old"], t["oracle"]), len(t["oracle"]) - 1)
            finals[("oracle", p["eid"], tail)] = (t["oracle"], {"load": {
                "n": lo, "segs": [[f"{p['eid']}/old", 0, lo, 0, 0, False]]}} if lo > 0 else None)
            if p["eid"] in alt_ids and tail != "retention":
                finals[("recompute_alt", p["eid"], tail)] = (t["new"], None)
    order = list(finals)
    outs_f = dict(zip(order, generate(llm, [(finals[k][0], sampling(MAX_NEW[k[2]], splice=finals[k][1]))
                                            for k in order])))

    # teacher-forced wave: each loading arm's own prefix + recompute's output, logprobs on computed rows
    order_t, batch = [], []
    for key in order:
        arm, eid, tail = key
        if arm == "recompute_alt":
            continue
        ref = list(outs_a[("recompute", eid, tail)].outputs[0].token_ids)
        if not ref:
            continue
        ids, splice = finals[key]
        batch.append((ids + ref, sampling(1, splice=splice, prompt_logprobs=TOPK)))
        order_t.append(key)
    outs_t = dict(zip(order_t, generate(llm, batch)))

    # records
    for p in chunk:
        for tail, t in p["tails"].items():
            ref_out = outs_a[("recompute", p["eid"], tail)]
            ref_ids = list(ref_out.outputs[0].token_ids)
            arms = {"recompute": ref_out}
            for arm in ("shift", "norope", "prefix", "oracle", "recompute_alt"):
                if (arm, p["eid"], tail) in outs_f:
                    arms[arm] = outs_f[(arm, p["eid"], tail)]
            for arm, out in arms.items():
                if (p["eid"], tail, arm) in done:
                    continue
                comp = out.outputs[0]
                ids = list(comp.token_ids)
                first_div = next((i for i, (a, b) in enumerate(zip(ids, ref_ids)) if a != b),
                                 None if len(ids) == len(ref_ids) else min(len(ids), len(ref_ids)))
                rec = {"event_id": p["eid"], "label": p["event"]["label"], "model": model, "tail": tail,
                       "arm": arm, "text": comp.text, "n_new": len(ids), "finish": comp.finish_reason,
                       "max_new": MAX_NEW[tail], "first_token": ids[0] if ids else None,
                       "same_as_recompute": ids == ref_ids, "first_div": first_div,
                       "kinds": p["event"]["kinds"], "plan": t["stats"],
                       "probe_family": (t["probe"] or {}).get("family"),
                       "cached_tokens": getattr(out, "num_cached_tokens", None)}
                tf = outs_t.get((arm, p["eid"], tail))
                if tf is not None and ref_ids:
                    rec["tf"] = tf_metrics(ref_out, tf, len(finals[(arm, p["eid"], tail)][0]), ref_ids)
                sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
    sink.flush()
    llm.collective_rpc(splice_free, args=([k for p in chunk for k in _keys_of(p)],))
    print(f"[arms] chunk of {len(chunk)} events: {written} records in {time.time() - t0:.0f}s", flush=True)
    return written


def _keys_of(p: dict) -> list[str]:
    keys = [f"{p['eid']}/old"]
    for t in p["tails"].values():
        for s in t["segs"]:
            if s[0] == "fresh":
                keys += [f"{p['eid']}/shift/f{s[1]}-{s[2]}", f"{p['eid']}/norope/f{s[1]}-{s[2]}"]
    return keys


def run(args) -> None:
    spec = MODELS[args.model]
    events = [json.loads(line) for line in open(args.events, encoding="utf-8")]
    events = [e for i, e in enumerate(events) if i % args.shards == args.shard]
    if args.limit:
        events = events[: args.limit]
    out = Path(args.out or DATA / f"arms_{args.model}.jsonl")
    done: set = set()
    if out.exists():
        for line in open(out, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["event_id"], r["tail"], r["arm"]))
    # an event is complete when its natural recompute + shift records exist
    todo = [e for e in events if (e["event_id"], "natural", "shift") not in done]
    alt_ids = {e["event_id"] for e in events
               if int(hashlib.sha1(e["event_id"].encode()).hexdigest(), 16) % ALT_EVERY == 0}
    print(f"[arms] {args.model}: {len(events)} events in shard, {len(todo)} to do", flush=True)
    if not todo:
        return
    renderer = Renderer(spec)
    runs = RunCache(Path(args.runs_dir))
    llm = make_engine(spec, args.tp, args.gpu_util, args.max_num_seqs, args.max_model_len)
    with out.open("a", encoding="utf-8") as sink:
        for start in range(0, len(todo), args.chunk):
            chunk = []
            for e in todo[start:start + args.chunk]:
                try:
                    chunk.append(prepare(e, runs, renderer, args.max_model_len))
                except Exception as exc:          # a malformed event must not stop the shard
                    print(f"[arms] prepare failed {e['event_id']}: {type(exc).__name__}: {exc}", flush=True)
            if not chunk:
                continue
            try:
                run_chunk(llm, chunk, args.model, sink, done, alt_ids)
            except Exception as exc:              # log, free the chunk's KV, keep going
                print(f"[arms] chunk failed ({', '.join(p['eid'] for p in chunk)}): "
                      f"{type(exc).__name__}: {exc}", flush=True)
                from splice_connector import splice_free
                llm.collective_rpc(splice_free, args=([k for p in chunk for k in _keys_of(p)],))


# -- selftests --------------------------------------------------------------------------------------

def selftest(args) -> dict:
    from splice_connector import splice_free, splice_inspect, splice_rotate_check, splice_stats

    spec = MODELS[args.model]
    renderer = Renderer(spec)
    llm = make_engine(spec, args.tp, args.gpu_util, args.max_num_seqs, args.max_model_len)
    tok = renderer.tokenizer
    rng = random.Random(0)
    text = Path(HERE.parent / "common" / "profile_run.py").read_text(encoding="utf-8")
    base = tok.encode(text, add_special_tokens=False)
    A, B, C = base[:1500], base[1500:1900], base[1900:3400]
    P = tok.encode("[Earlier tool result saved at /tmp/x/.task_outputs/tool-results/chatcmpl-tool-1.txt]",
                   add_special_tokens=False)
    tail = tok.encode("\nNow summarise what the code above does in one sentence.", add_special_tokens=False)
    res: dict = {"model": args.model, "stats0": llm.collective_rpc(splice_stats)[0]}

    # T1: loading the old prompt (all but its last token) reproduces recompute
    X = A + C
    generate(llm, [(X, sampling(1, splice={"save": {"key": "t1/old", "lo": 0, "hi": len(X)}}))])
    ref, got = generate(llm, [
        (X, sampling(32, logprobs=TOPK)),
        (X, sampling(32, logprobs=TOPK, splice={"load": {"n": len(X) - 1,
                                                          "segs": [["t1/old", 0, len(X) - 1, 0, 0, False]]}}))])
    d = [abs(ref.outputs[0].logprobs[0][t].logprob - got.outputs[0].logprobs[0][t].logprob)
         for t in ref.outputs[0].logprobs[0] if t in got.outputs[0].logprobs[0]]
    res["T1"] = {"same_greedy_32": list(ref.outputs[0].token_ids) == list(got.outputs[0].token_ids),
                 "first_logprob_maxdiff": round(max(d), 5), "cached_tokens": got.num_cached_tokens}

    # T2: layer-0 rows of X at offset delta == rotate(rows of X at 0, delta)
    delta = 97
    pad = base[3400:3400 + delta]
    generate(llm, [(A, sampling(1, splice={"save": {"key": "t2/a0", "lo": 0, "hi": len(A)}})),
                   (pad + A, sampling(1, splice={"save": {"key": "t2/ad", "lo": 0, "hi": delta + len(A)}}))])
    rot = llm.collective_rpc(splice_rotate_check, args=("t2/a0", delta, 0, 0, len(A)))[0]
    true = llm.collective_rpc(splice_inspect, args=("t2/ad", 0, delta, delta + len(A)))[0]
    raw = llm.collective_rpc(splice_inspect, args=("t2/a0", 0, 0, len(A)))[0]
    st = res["stats0"]
    lo, hi = st["pos_dims"]
    scale = float(true[..., lo:hi].abs().max())
    res["T2"] = {"pos_maxdiff_rel": round(float((rot[..., lo:hi] - true[..., lo:hi]).abs().max()) / scale, 5),
                 "pos_maxdiff_unrotated_rel": round(float((raw[..., lo:hi] - true[..., lo:hi]).abs().max()) / scale, 4),
                 "nonpos_maxdiff": round(float(torch_cat_nonpos(rot, lo, hi, true)), 6)}

    # T3: prompt_logprobs under a connector load equal the full prefill's
    Y = tail
    full, loaded = generate(llm, [
        (X + Y, sampling(1, prompt_logprobs=5)),
        (X + Y, sampling(1, prompt_logprobs=5, splice={"load": {"n": len(X), "segs": [["t1/old", 0, len(X), 0, 0, False]]}}))])
    diffs = [abs(prompt_lp(full, len(X) + i)[Y[i]].logprob - prompt_lp(loaded, len(X) + i)[Y[i]].logprob)
             for i in range(1, len(Y))]
    res["T3"] = {"rows": len(loaded.prompt_logprobs or []), "expected_rows": len(X + Y) - len(X),
                 "nll_maxdiff": round(max(diffs), 5)}

    # T5: staged splice old = A+B+C -> new = A+P+C+tail
    old, new = A + B + C, A + P + C + tail
    segs = splice_plan(old, new)
    stats = plan_stats(old, new, segs)
    generate(llm, [(old, sampling(1, splice={"save": {"key": "t5/old", "lo": 0, "hi": len(old)}}))])
    outs = {}
    for arm in ("shift", "norope"):
        for seg in [s for s in segs if s[0] == "fresh"][:-1]:
            key = f"t5/{arm}/f{seg[1]}-{seg[2]}"
            generate(llm, [(new[:seg[2]], sampling(1, splice={
                "load": {"n": seg[1], "segs": load_segments("t5", arm, segs, seg[1])},
                "save": {"key": key, "lo": seg[1], "hi": seg[2]}}))])
        n = stats["tail_start"]
        outs[arm] = generate(llm, [(new, sampling(24, logprobs=TOPK, splice={
            "load": {"n": n, "segs": load_segments("t5", arm, segs, n)}}))])[0]
    recompute = generate(llm, [(new, sampling(24, logprobs=TOPK))])[0]
    res["T5"] = {"plan": stats, "segs": [list(s) for s in segs],
                 "shift_text": outs["shift"].outputs[0].text, "norope_text": outs["norope"].outputs[0].text,
                 "recompute_text": recompute.outputs[0].text,
                 "shift_first_lp_gap": _first_gap(recompute, outs["shift"]),
                 "norope_first_lp_gap": _first_gap(recompute, outs["norope"])}

    # T4: recompute after all of the above is unchanged (no contamination)
    again = generate(llm, [(new, sampling(24, logprobs=TOPK))])[0]
    res["T4"] = {"recompute_repeat_identical": list(again.outputs[0].token_ids) == list(recompute.outputs[0].token_ids)}
    res["stats1"] = llm.collective_rpc(splice_stats)[0]
    llm.collective_rpc(splice_free, args=(None,))
    # Hard checks catch connector bugs: the rotation reproduces true shifted keys, values / the MLA
    # latent are copied exactly, nothing leaks into later requests, the load really happened, and the
    # cure beats no cure. Logit differences between a loaded prefix and a full prefill are reported,
    # not gated tightly: they are exactly 0 for GQA at TP=1, but a 1-token forward and a 3000-token
    # forward take different GEMM / all-reduce shapes under TP=2, and MLA runs its absorbed-latent
    # kernel on loaded context, so bf16 path noise appears (the `prefix` arm measures it in the
    # sweep). A wrong load produces garbage, far above the 2-nat ceiling.
    res["numerics"] = {"T1_same_greedy_32": res["T1"]["same_greedy_32"],
                       "T1_first_logprob_maxdiff": res["T1"]["first_logprob_maxdiff"],
                       "T3_nll_maxdiff": res["T3"]["nll_maxdiff"]}
    ok = (res["T2"]["pos_maxdiff_rel"] < 0.02 and res["T2"]["nonpos_maxdiff"] < 1e-6
          and res["T4"]["recompute_repeat_identical"] and res["T1"]["cached_tokens"] == len(X) - 1
          and res["T1"]["first_logprob_maxdiff"] < 2.0 and res["T3"]["nll_maxdiff"] < 2.0
          and res["T5"]["shift_first_lp_gap"] < res["T5"]["norope_first_lp_gap"])
    res["ok"] = bool(ok)
    return res


def torch_cat_nonpos(rot, lo, hi, true):
    import torch
    parts = [slice(0, lo), slice(hi, rot.shape[-1])]
    diffs = [float((rot[..., s] - true[..., s]).abs().max()) for s in parts if s.stop > s.start]
    return max(diffs) if diffs else 0.0


def _first_gap(ref, out) -> float:
    a, b = ref.outputs[0].logprobs[0], out.outputs[0].logprobs[0]
    top = max(a.items(), key=lambda kv: kv[1].logprob)[0]
    return round(a[top].logprob - (b[top].logprob if top in b else min(v.logprob for v in b.values())), 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "selftest"])
    ap.add_argument("--model", default="qwen32b", choices=sorted(MODELS))
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--gpu-util", type=float, default=0.70)
    ap.add_argument("--max-num-seqs", type=int, default=64)
    ap.add_argument("--max-model-len", type=int, default=40960)
    ap.add_argument("--events", default=str(DATA / "events.jsonl"))
    ap.add_argument("--runs-dir", default=str(DATA / "runs"))
    ap.add_argument("--out", default="")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--chunk", type=int, default=6, help="events in flight (bounds the KV store)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--selftest-out", default="")
    args = ap.parse_args()
    if args.cmd == "selftest":
        res = selftest(args)
        text = json.dumps(res, indent=2, default=str)
        print(text)
        out = Path(args.selftest_out or DATA / f"selftest_{args.model}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        sys.exit(0 if res["ok"] else 1)
    run(args)


if __name__ == "__main__":
    main()
