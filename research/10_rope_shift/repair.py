#!/usr/bin/env python3
"""Part B: can selective recompute repair the two costs of the RoPE cure? (offline vLLM, GPU)

Part A found that reusing every surviving KV run with re-rotated RoPE (arm `shift`) keeps the next
round within the compaction's own perturbation, with two costs: long verbatim copies out of the
shifted region get worse (SEARCH/REPLACE edits), and Qwen3-8B stops admitting that evicted content is
gone. Both come from KV computed in a context that no longer exists. This script replays the same
683 events with arms that recompute part of the reused KV. Every arm reuses the unchanged prefix
before the first edit exactly (as prefix caching would) and computes the fresh text:

  recompute  full prefill: the reference, rerun so the teacher-forced passes have its decode
             logprobs; the natural tail also keeps its check-layer KV (CacheBlend's signal)
  shift      Part A's RoPE cure through this script's mechanism: the paired baseline, and the check
             that the one-pass override reproduces Part A's staged splice
  epic32     EPIC / LegoLink (Hu et al., ICML 2025): also recompute the first k tokens of every
  epic128      reused run after the first edit, at every layer (k = 32 in the paper; 128 as a
             larger budget)
  blend05    CacheBlend (Yao et al., EuroSys 2025) as LMCache's blender implements it: layers below
  blend15      the check layer (1) are recomputed for every token; from the check layer on, only the
             top r% of reused tokens by squared L2 distance between the freshly computed and the
             reused (re-rotated) check-layer key are recomputed, the rest keep their reused KV
             (`diff_k`; r = 15% is the ratio of LMCache's examples). The fresh check-layer keys come
             from the recompute pass, which is what CacheBlend's full first layers compute.
  rand15     the control: blend15 with a uniformly random token set of the same size

Mechanism (splice_connector.py, "override"): one forward per (event, arm, tail) computes new[P0:ts]
-- P0 the end of the unchanged prefix, which is loaded; ts the start of the tail -- while an
attention pre-hook replaces the K/V of the reused tokens that are not selected, at every layer
>= from_layer, by their old KV re-rotated to the new positions. That forward saves the KV of
[P0, ts); the generation and the teacher-forced pass load it and compute only the tail, like Part
A's final waves. Budgets are exact: nothing is staged or merged. Metrics are Part A's (score.py).

    python research/10_rope_shift/repair.py selftest --model qwen8b
    python research/10_rope_shift/repair.py run --model qwen32b --tp 2 [--shard 0 --shards 4]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # research/
import _paths  # noqa: F401,E402

from arms import MAX_NEW, TOPK, RunCache, generate, make_engine, prepare, sampling, tf_metrics  # noqa: E402
from render import MODELS, Renderer  # noqa: E402

DATA = HERE / "data" / "rope_shift_repair"
LIVE = HERE / "data" / "rope_shift_live"
CHECK_LAYER = 1
HEAD = 32                                   # "run head" for the selection diagnostics (EPIC's k)
ARMS = ["shift", "epic32", "epic128", "blend05", "blend15", "rand15"]
FROM_LAYER = {"shift": 0, "epic32": 0, "epic128": 0,
              "blend05": CHECK_LAYER, "blend15": CHECK_LAYER, "rand15": CHECK_LAYER}
GiB = 1 << 30


# -- selections and override plans (CPU) ------------------------------------------------------------

def region(t: dict) -> tuple[int, int]:
    """[P0, ts) of a tail: after the exactly reused prefix, before the tail."""
    return t["stats"]["prefix_len"], t["stats"]["tail_start"]


def reuse_triplets(segs: list[tuple], lo: int, hi: int) -> tuple[list[int], list[int], list[int]]:
    """New positions, old positions and deltas of the reused tokens in [lo, hi)."""
    new_pos, old_pos, deltas = [], [], []
    for seg in segs:
        if seg[0] != "reuse":
            continue
        j1, j2, i1 = seg[1], seg[2], seg[3]
        for j in range(max(j1, lo), min(j2, hi)):
            new_pos.append(j)
            old_pos.append(i1 + (j - j1))
            deltas.append(j1 - i1)
    return new_pos, old_pos, deltas


def run_heads(segs: list[tuple], k: int, lo: int, hi: int) -> set[int]:
    """First k tokens of every reused run in [lo, hi) (EPIC's recomputed tokens)."""
    out: set[int] = set()
    for seg in segs:
        if seg[0] == "reuse" and seg[1] > 0 and lo <= seg[1] < hi:
            out.update(range(seg[1], min(seg[1] + k, seg[2], hi)))
    return out


def select_blend(new_pos: list[int], dev: list[float], ratio: float) -> set[int]:
    if not new_pos:
        return set()
    k = math.ceil(ratio * len(new_pos))
    order = sorted(range(len(new_pos)), key=lambda i: (-dev[i], new_pos[i]))[:k]
    return {new_pos[i] for i in order}


def select_rand(new_pos: list[int], k: int, seed: str) -> set[int]:
    return set(random.Random(f"10rope-rand-{seed}").sample(new_pos, min(k, len(new_pos))))


def override_segs(segs: list[tuple], lo: int, hi: int, chosen: set[int]) -> list[list]:
    """Connector override segments [pos_lo, pos_hi, src_lo, delta]: the reused tokens of [lo, hi)
    that are NOT chosen for recompute keep their old KV, re-rotated by their run's delta."""
    out = []
    for seg in segs:
        if seg[0] != "reuse":
            continue
        j1, j2, i1 = seg[1], seg[2], seg[3]
        j, b = max(j1, lo), min(j2, hi)
        while j < b:
            if j in chosen:
                j += 1
                continue
            k = j
            while k < b and k not in chosen:
                k += 1
            out.append([j, k, i1 + (j - j1), j1 - i1])
            j = k
    return out


def spans(positions: set[int]) -> int:
    return sum(1 for p in positions if p - 1 not in positions)


def plan_arm(t: dict, arm: str, chosen: dict[str, set]) -> dict:
    lo, hi = region(t)
    reused = set(reuse_triplets(t["segs"], lo, hi)[0])
    sel = run_heads(t["segs"], int(arm[4:]), lo, hi) if arm.startswith("epic") else chosen[arm]
    sel = sel & reused
    return {"lo": lo, "hi": hi, "segs": override_segs(t["segs"], lo, hi, sel),
            "stats": {"from_layer": FROM_LAYER[arm], "candidates": len(reused), "selected": len(sel),
                      "ratio": round(len(sel) / len(reused), 4) if reused else None,
                      "spans": spans(sel)}}


def stage_spec(eid: str, key: str, plan: dict, from_layer: int) -> dict:
    """The override forward over new[P0:ts]: prefix loaded exactly, [P0, ts) saved under `key`."""
    lo, hi = plan["lo"], plan["hi"]
    spec = {"override": {"key": f"{eid}/old", "segs": plan["segs"], "from_layer": from_layer},
            "save": {"key": key, "lo": lo, "hi": hi}}
    if lo > 0:
        spec["load"] = {"n": lo, "segs": [[f"{eid}/old", 0, lo, 0, 0, False]]}
    return spec


def final_spec(eid: str, key: str, plan: dict) -> dict | None:
    """Load new[0:ts] = exact prefix + the arm's [P0, ts); the request computes only the tail."""
    lo, hi = plan["lo"], plan["hi"]
    segs = []
    if lo > 0:
        segs.append([f"{eid}/old", 0, lo, 0, 0, False])
    if hi > lo:
        segs.append([key, lo, hi, lo, 0, False])
    return {"load": {"n": hi, "segs": segs}} if hi > 0 else None


def deviation_summary(new_pos: list[int], dev: list[float], sel15: set[int], segs: list[tuple],
                      lo: int, hi: int) -> dict:
    if not new_pos:
        return {"candidates": 0}
    top = sorted(dev, reverse=True)
    total = sum(top) or 1.0
    heads = run_heads(segs, HEAD, lo, hi)
    return {"candidates": len(new_pos),
            "mass_top5": round(sum(top[:math.ceil(0.05 * len(top))]) / total, 4),
            "mass_top15": round(sum(top[:math.ceil(0.15 * len(top))]) / total, 4),
            "blend15_spans": spans(sel15),
            "blend15_in_run_heads": round(len(sel15 & heads) / len(sel15), 4) if sel15 else None,
            "run_head_share": round(len(heads & set(new_pos)) / len(new_pos), 4)}


# -- one chunk of events ----------------------------------------------------------------------------

def store_room(llm, safety: int) -> int:
    """Bytes the KV store may still take on the tightest rank: GPU memory outside vLLM's
    gpu_memory_utilization share, minus what the store holds and a margin. The allocator's cached
    blocks inside vLLM's share are NOT free: the next forward reuses them for activations (counting
    them as free is what ran an H200 out of memory in the first attempt)."""
    from splice_connector import splice_stats
    return min(int(s["total_bytes"] * (1 - s["gpu_util"])) - s["store_bytes"]
               for s in llm.collective_rpc(splice_stats)) - safety


def unit_groups(chunk: list[dict], token_bytes: int, budget: int) -> list[list[tuple[str, str]]]:
    """(arm, event) units packed into groups whose [P0, ts) entries fit the store together; a unit
    that alone exceeds the budget still gets a group of its own."""
    groups, cur, used = [], [], 0
    for arm in ARMS:
        for p in chunk:
            need = token_bytes * sum(pl["hi"] - pl["lo"] for (a, _), pl in p["plans"].items() if a == arm)
            if cur and used + need > budget:
                groups.append(cur)
                cur, used = [], 0
            cur.append((arm, p["eid"]))
            used += need
    if cur:
        groups.append(cur)
    return groups


def repair_chunk(llm, chunk: list[dict], model: str, sink, done: set, token_bytes: int,
                 safety: int) -> int:
    from splice_connector import splice_deviation, splice_free

    t0 = time.time()
    # wave A: old KV saved; recompute generations (the natural one keeps its check-layer KV)
    batch, keys = [], []
    for p in chunk:
        eid = p["eid"]
        batch.append((p["old"], sampling(1, splice={"save": {"key": f"{eid}/old", "lo": 0, "hi": len(p["old"])}})))
        keys.append(("old", eid, None))
        for tail, t in p["tails"].items():
            lo, hi = region(t)
            splice = {"save": {"key": f"{eid}/rec", "lo": lo, "hi": hi, "layers": [CHECK_LAYER]}} \
                if tail == "natural" and hi > lo else None
            batch.append((t["new"], sampling(MAX_NEW[tail], logprobs=TOPK, splice=splice)))
            keys.append(("recompute", eid, tail))
    outs_a = dict(zip(keys, generate(llm, batch)))

    # selections (CacheBlend's deviation is computed once, on the natural tail) and plans
    for p in chunk:
        eid, nat = p["eid"], p["tails"]["natural"]
        lo, hi = region(nat)
        new_pos, old_pos, deltas = reuse_triplets(nat["segs"], lo, hi)
        dev = [0.0] * len(new_pos)
        if new_pos:
            for part in llm.collective_rpc(splice_deviation, args=(f"{eid}/rec", f"{eid}/old", CHECK_LAYER,
                                                                  new_pos, old_pos, deltas)):
                dev = [a + b for a, b in zip(dev, part)]
        chosen = {"shift": set(), "blend05": select_blend(new_pos, dev, 0.05),
                  "blend15": select_blend(new_pos, dev, 0.15)}
        chosen["rand15"] = select_rand(new_pos, len(chosen["blend15"]), eid)
        p["deviation"] = deviation_summary(new_pos, dev, chosen["blend15"], nat["segs"], lo, hi)
        p["plans"] = {(arm, tail): plan_arm(t, arm, chosen) for arm in ARMS for tail, t in p["tails"].items()}

    # per group of (arm, event) units: override forwards (saving [P0, ts)), then generations and
    # teacher-forced passes that load them
    groups = unit_groups(chunk, token_bytes, max(store_room(llm, safety), 0))
    tails_of = {p["eid"]: p["tails"] for p in chunk}
    outs_f, outs_t = {}, {}
    for group in groups:
        units = set(group)
        batch = []
        for p in chunk:
            for (arm, tail), plan in p["plans"].items():
                if (arm, p["eid"]) in units and plan["hi"] > plan["lo"]:
                    key = f"{p['eid']}/{arm}/{tail}"
                    batch.append((p["tails"][tail]["new"][:plan["hi"]],
                                  sampling(1, splice=stage_spec(p["eid"], key, plan, FROM_LAYER[arm]))))
        generate(llm, batch)
        batch, order = [], []
        for p in chunk:
            eid = p["eid"]
            for (arm, tail), plan in p["plans"].items():
                if (arm, eid) not in units:
                    continue
                spec = final_spec(eid, f"{eid}/{arm}/{tail}", plan)
                new = tails_of[eid][tail]["new"]
                batch.append((new, sampling(MAX_NEW[tail], splice=spec)))
                order.append(("gen", arm, eid, tail))
                ref = list(outs_a[("recompute", eid, tail)].outputs[0].token_ids)
                if ref:
                    batch.append((new + ref, sampling(1, splice=spec, prompt_logprobs=TOPK)))
                    order.append(("tf", arm, eid, tail))
        for (kind, arm, eid, tail), out in zip(order, generate(llm, batch)):
            (outs_f if kind == "gen" else outs_t)[(arm, eid, tail)] = out
        llm.collective_rpc(splice_free, args=([f"{eid}/{arm}/{tail}" for arm, eid in group
                                               for tail in tails_of[eid]],))

    lines = []
    for p in chunk:
        for tail, t in p["tails"].items():
            ref_out = outs_a[("recompute", p["eid"], tail)]
            ref_ids = list(ref_out.outputs[0].token_ids)
            arms = {"recompute": ref_out}
            arms.update({arm: outs_f[(arm, p["eid"], tail)] for arm in ARMS if (arm, p["eid"], tail) in outs_f})
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
                       "repair": p["plans"][(arm, tail)]["stats"] if arm != "recompute" else None,
                       "deviation": p["deviation"]}
                tf = outs_t.get((arm, p["eid"], tail))
                if tf is not None and ref_ids:
                    rec["tf"] = tf_metrics(ref_out, tf, len(t["new"]), ref_ids)
                lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
    sink.write("".join(lines))          # one write per chunk: a killed job leaves whole chunks
    sink.flush()
    written = len(lines)
    llm.collective_rpc(splice_free, args=([f"{p['eid']}/{name}" for p in chunk for name in ("old", "rec")],))
    print(f"[repair] chunk of {len(chunk)} events: {written} records in {time.time() - t0:.0f}s, "
          f"{len(groups)} store groups", flush=True)
    return written


def start_watchdog(state: dict, limit: float, stuck_file: Path) -> None:
    """One engine stalled for 45 minutes on an ordinary chunk (all threads waiting, no progress; the
    cause was not visible from outside its GPU). If a chunk runs past `limit` seconds, record its
    events in `stuck_file` (later runs skip them), kill the engine processes and exit, so the
    pipeline moves on instead of holding the GPUs."""
    def watch():
        while True:
            time.sleep(30)
            t0 = state.get("start")
            if t0 is None or time.time() - t0 < limit:
                continue
            eids = state.get("events", [])
            print(f"[repair] watchdog: chunk ({', '.join(eids)}) ran {time.time() - t0:.0f}s > {limit:.0f}s; "
                  "recording it as stuck and killing the engine", flush=True)
            with stuck_file.open("a", encoding="utf-8") as f:
                f.writelines(e + "\n" for e in eids)
            import psutil
            for child in psutil.Process().children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            os._exit(5)
    threading.Thread(target=watch, daemon=True, name="repair-watchdog").start()


def load_events(path: Path) -> list[dict]:
    from score import merged
    events = [json.loads(line) for line in merged(path, "events_s*.jsonl")]
    events.sort(key=lambda e: e["event_id"])
    return events


def run(args) -> None:
    from splice_connector import splice_free, splice_stats
    spec = MODELS[args.model]
    events = load_events(Path(args.events))
    events = [e for i, e in enumerate(events) if i % args.shards == args.shard]
    events = [e for i, e in enumerate(events) if i % args.subshards == args.subshard]
    if args.only:
        events = [e for e in events if e["event_id"] in set(args.only.split(","))]
    if args.limit:
        events = events[: args.limit]
    DATA.mkdir(parents=True, exist_ok=True)
    out = Path(args.out or DATA / f"arms_{args.model}_s{args.shard}_{args.subshard}.jsonl")
    done: set = set()
    # a resubmitted shard may land on a node with another engine count: resume from every sub-file
    for f in {out, *out.parent.glob(f"arms_{args.model}_s{args.shard}_*.jsonl")}:
        if f.exists():
            for line in open(f, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:       # a line cut by a kill; its chunk reruns
                    continue
                done.add((r["event_id"], r["tail"], r["arm"]))
    stuck_file = DATA / f"stuck_{args.model}.txt"
    stuck = set(stuck_file.read_text().split()) if stuck_file.exists() and not args.ignore_stuck else set()
    todo = [e for e in events if (e["event_id"], "natural", ARMS[-1]) not in done and e["event_id"] not in stuck]
    print(f"[repair] {args.model}: {len(events)} events in shard, {len(todo)} to do"
          f"{f', {len(stuck)} recorded as stuck' if stuck else ''}", flush=True)
    if not todo:
        return
    renderer = Renderer(spec)
    runs = RunCache(Path(args.runs_dir))
    llm = make_engine(spec, args.tp, args.gpu_util, args.max_num_seqs, args.max_model_len)
    stats = llm.collective_rpc(splice_stats)[0]
    token_bytes, safety = stats["token_bytes"], int(args.safety_gb * GiB)
    print(f"[repair] {stats['views']} layers x {stats['view_shape']}: {token_bytes / 1024:.0f} KiB per "
          f"token per rank, {store_room(llm, safety) / GiB:.1f} GiB of store room, {stats['hooks']} hooks",
          flush=True)
    cap = store_room(llm, safety) // 2        # a chunk's old KV may fill at most half the room
    watch: dict = {}
    start_watchdog(watch, args.chunk_timeout, stuck_file)

    def run_chunk(chunk: list[dict]) -> None:
        watch.update(start=time.time(), events=[p["eid"] for p in chunk])
        try:
            repair_chunk(llm, chunk, args.model, sink, done, token_bytes, safety)
            watch["start"] = None
        except Exception as exc:
            watch["start"] = None
            print(f"[repair] chunk failed ({', '.join(p['eid'] for p in chunk)}): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            if type(exc).__name__ == "EngineDeadError":         # every later chunk would fail too
                raise SystemExit(4) from exc
            llm.collective_rpc(splice_free, args=(None,))

    with out.open("a", encoding="utf-8") as sink:
        chunk, old_bytes = [], 0
        for e in todo:
            try:
                p = prepare(e, runs, renderer, args.max_model_len)
            except Exception as exc:
                print(f"[repair] prepare failed {e['event_id']}: {type(exc).__name__}: {exc}", flush=True)
                continue
            need = len(p["old"]) * token_bytes
            if chunk and (len(chunk) >= args.chunk or old_bytes + need > cap):
                run_chunk(chunk)
                chunk, old_bytes = [], 0
            chunk.append(p)
            old_bytes += need
        if chunk:
            run_chunk(chunk)


# -- selftest ---------------------------------------------------------------------------------------

def selftest(args) -> dict:
    """Gates for the override mechanism on one real event (GPU):
      R1  reused rows written under an override (from_layer 0) equal the re-rotated old rows bit for
          bit at the first, middle and last layer, K and V (or MLA latent and k_pe)
      R2  with from_layer 2, layer-1 rows of reused tokens are the fresh ones (close to recompute's,
          far from the reused ones) and layer-2 rows are the reused ones exactly
      R3  selected tokens (from_layer 1) keep fresh KV: layer 1 equals recompute's up to numerics,
          layer 2 differs from the reused rows; unselected neighbours are reused exactly
      R4  a hook on every layer, and the override really fired
      R5  a recompute after all of this is unchanged (no contamination)"""
    from splice_connector import splice_free, splice_inspect, splice_rotate_check, splice_stats

    spec = MODELS[args.model]
    renderer = Renderer(spec)
    runs = RunCache(Path(args.runs_dir))
    p = run_seg = None
    for e in load_events(Path(args.events)):
        try:
            cand = prepare(e, runs, renderer, args.max_model_len)
        except Exception:
            continue
        nat = cand["tails"]["natural"]
        lo, hi = region(nat)
        shifted = [s for s in nat["segs"] if s[0] == "reuse" and s[1] >= lo and s[2] <= hi
                   and s[2] - s[1] >= 96 and s[1] != s[3]]
        if lo > 0 and shifted:
            p, run_seg = cand, shifted[0]
            break
    if p is None:
        raise SystemExit("no event with a shifted reused run of >= 96 tokens")
    nat = p["tails"]["natural"]
    lo, hi = region(nat)
    new = nat["new"][:hi]
    j1, i1 = run_seg[1], run_seg[3]
    delta = j1 - i1
    llm = make_engine(spec, args.tp, args.gpu_util, args.max_num_seqs, args.max_model_len)
    st0 = llm.collective_rpc(splice_stats)[0]
    n_layers = st0["views"]
    res: dict = {"model": args.model, "tp": args.tp, "event": p["eid"], "region": [lo, hi],
                 "run": list(run_seg), "stats0": st0}

    generate(llm, [(p["old"], sampling(1, splice={"save": {"key": "st/old", "lo": 0, "hi": len(p["old"])}})),
                   (new, sampling(1, splice={"save": {"key": "st/rec", "lo": lo, "hi": hi, "layers": [0, 1, 2]}}))])
    ref_a = generate(llm, [(nat["new"], sampling(16, logprobs=TOPK))])[0]
    load = {"n": lo, "segs": [["st/old", 0, lo, 0, 0, False]]}
    every = override_segs(nat["segs"], lo, hi, set())
    sel = set(range(j1 + 16, j1 + 48))                       # 32 tokens inside the shifted run
    generate(llm, [
        (new, sampling(1, splice={"override": {"key": "st/old", "segs": every, "from_layer": 0},
                                  "save": {"key": "st/shift", "lo": lo, "hi": hi}, "load": load})),
        (new, sampling(1, splice={"override": {"key": "st/old", "segs": every, "from_layer": 2},
                                  "save": {"key": "st/fl2", "lo": lo, "hi": hi}, "load": load})),
        (new, sampling(1, splice={"override": {"key": "st/old", "segs": override_segs(nat["segs"], lo, hi, sel),
                                               "from_layer": 1},
                                  "save": {"key": "st/sel", "lo": lo, "hi": hi}, "load": load}))])

    def rows(key, layer, a, b):
        return llm.collective_rpc(splice_inspect, args=(key, layer, a, b))[0]

    def reused(layer, a, b):                                  # old rows of new positions [a, b), re-rotated
        return llm.collective_rpc(splice_rotate_check, args=("st/old", delta, layer, i1 + a - j1, i1 + b - j1))[0]

    def sq(x, y):
        return float(((x - y) ** 2).sum())

    def maxdiff(x, y):
        return float((x - y).abs().max())

    a, b = j1, j1 + 96
    res["R1"] = {f"layer{l}_maxdiff": maxdiff(rows("st/shift", l, a, b), reused(l, a, b))
                 for l in (0, n_layers // 2, n_layers - 1)}
    fresh1, old1, rec1 = rows("st/fl2", 1, a, b), reused(1, a, b), rows("st/rec", 1, a, b)
    res["R2"] = {"layer1_fresh_err": sq(fresh1, rec1), "layer1_reused_err": sq(old1, rec1),
                 "layer1_ratio": sq(fresh1, rec1) / max(sq(old1, rec1), 1e-12),
                 "layer2_maxdiff": maxdiff(rows("st/fl2", 2, a, b), reused(2, a, b))}
    s_a, s_b = j1 + 16, j1 + 48
    sel1, rec_sel1 = rows("st/sel", 1, s_a, s_b), rows("st/rec", 1, s_a, s_b)
    res["R3"] = {"selected_layer1_ratio": sq(sel1, rec_sel1) / max(sq(reused(1, s_a, s_b), rec_sel1), 1e-12),
                 "selected_layer2_vs_reused": sq(rows("st/sel", 2, s_a, s_b), reused(2, s_a, s_b)),
                 "unselected_layer2_maxdiff": maxdiff(rows("st/sel", 2, j1, j1 + 16), reused(2, j1, j1 + 16)),
                 "unselected_layer0_vs_rec": sq(rows("st/sel", 0, j1, j1 + 16), rows("st/rec", 0, j1, j1 + 16))}
    st1 = llm.collective_rpc(splice_stats)[0]
    res["R4"] = {"hooks": st1["hooks"], "layers": n_layers, "overrides": st1["overrides"],
                 "overridden_tokens": st1["overridden_tokens"]}
    shift_gen = generate(llm, [(nat["new"], sampling(16, logprobs=TOPK,
                                                     splice=final_spec("st", "st/shift", {"lo": lo, "hi": hi})))])[0]
    ref_b = generate(llm, [(nat["new"], sampling(16, logprobs=TOPK))])[0]
    res["R5"] = {"recompute_repeat_identical": list(ref_a.outputs[0].token_ids) == list(ref_b.outputs[0].token_ids)}
    res["texts"] = {"recompute": ref_a.outputs[0].text, "shift_override": shift_gen.outputs[0].text}
    llm.collective_rpc(splice_free, args=(None,))
    res["ok"] = bool(all(v == 0.0 for v in res["R1"].values())
                     and res["R2"]["layer1_ratio"] < 0.1 and res["R2"]["layer2_maxdiff"] == 0.0
                     and res["R3"]["selected_layer1_ratio"] < 0.1 and res["R3"]["selected_layer2_vs_reused"] > 0
                     and res["R3"]["unselected_layer2_maxdiff"] == 0.0
                     and res["R4"]["hooks"] == n_layers and res["R4"]["overridden_tokens"] > 0
                     and res["R5"]["recompute_repeat_identical"])
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "selftest"])
    ap.add_argument("--model", default="qwen32b", choices=sorted(MODELS))
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--gpu-util", type=float, default=0.62)
    ap.add_argument("--max-num-seqs", type=int, default=64)
    ap.add_argument("--max-model-len", type=int, default=40960)
    ap.add_argument("--events", default=str(LIVE / "events.jsonl"))
    ap.add_argument("--runs-dir", default=str(LIVE / "runs"))
    ap.add_argument("--out", default="")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--subshard", type=int, default=0, help="engine index within the shard's node")
    ap.add_argument("--subshards", type=int, default=1)
    ap.add_argument("--chunk", type=int, default=4, help="events in flight (their old KV stays stored)")
    ap.add_argument("--safety-gb", type=float, default=16.0,
                    help="margin kept free outside vLLM's gpu_memory_utilization share")
    ap.add_argument("--chunk-timeout", type=float, default=900.0,
                    help="seconds before a stalled chunk is recorded as stuck and the process exits")
    ap.add_argument("--only", default="", help="comma-separated event ids to run (within the shard)")
    ap.add_argument("--ignore-stuck", action="store_true",
                    help="retry events recorded as stuck (e.g. with a larger KV pool or fewer sequences)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.cmd == "selftest":
        res = selftest(args)
        out = Path(args.out or DATA / f"selftest_{args.model}{'_tp%d' % args.tp if args.tp > 1 else ''}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in res.items() if k != "stats0"}, indent=1, default=str))
        sys.exit(0 if res["ok"] else 1)
    run(args)


if __name__ == "__main__":
    main()
