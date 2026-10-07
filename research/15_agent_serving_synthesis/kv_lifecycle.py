#!/usr/bin/env python3
"""KV lifecycle of agent sessions on one vLLM server (offline) -- research/15_agent_serving_synthesis.

Reads, read-only, the MUScheduler logs that research/11_multiuser_kv wrote (12, 13 and 14 subclass the
same scheduler, so their logs read alike) and measures per level what the 15 brainstorm needs:

  edits      per prefix chain (session, purpose): does a call append to the chain's previous prompt or
             diverge before its end (a harness edit: compaction, memory or timestamp sections)? Where does
             it diverge, how many cached tokens of the old version die, and how many of the tokens
             recomputed after the edit point could a position-shifted reuse keep (an estimate: the tokens
             after the edit point minus the chain's typical append and a placeholder)?
  garbage    evicted cached blocks split into live (still reusable by the owner chain's next request) and
             dead (superseded suffixes, re-rendered replies, the template tail), and the dead blocks the
             trace produces per minute, against the pool.
  reuse      the gap between a chain's finish and its next arrival, and the share of the arrival's ideal
             prefix still cached at arrival (lost during the pause) and at admission (lost in the queue).
  bandwidth  with 11's pooled step model (t = 18.46 ms + 77.3 ns per decode-context token + 0.03 ms per
             decoding sequence, R^2 1.000 over 2.03M steps), the share of decode-only step time that goes
             to reading KV rather than weights.

    python research/15_agent_serving_synthesis/kv_lifecycle.py [--cells n01a,n04,...] [--root DIR]
        [--out research/15_agent_serving_synthesis/data/tables/kv_lifecycle.md]
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: E402,F401

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]
ROOT11 = REPO / "research/11_multiuser_kv/data/multiuser_live/cells"
CELLS = ["n01a", "n01b", "n04", "n08", "n12", "n16", "n32", "n08kvh", "n08mem", "n08ts",
         "s3gate16", "s3gate32"]
# 11's pooled step model (research/11_multiuser_kv/data/multiuser_live/multiuser_tables.md, line 5)
STEP = {"fixed": 0.01846, "dec_seq": 2.775e-05, "dec_ctx": 0.07729}
APPEND_MAX = 32      # tokens: an append-only call loses only the previous prompt's re-rendered tail block
PLACEHOLDER = 64     # tokens a compaction placeholder adds back (estimate)
GAP_BINS = [(0, 1), (1, 15), (15, 60), (60, 120), (120, math.inf)]


def read_log(path: Path):
    with lzma.open(path, "rt") as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def analyze_server(path: Path) -> dict:
    hdr, chains, arr, adm, fin = {}, {}, {}, {}, {}
    ev = {"n": 0, "live": 0, "dead": 0, "live_idle": 0, "live_busy": 0}
    bw = {"steps": 0, "t": 0.0, "kv": 0.0, "batch": 0, "ctx": 0}
    # pool composition: per chain, requests queued / running and the latest prompt's blocks
    queued, running, last_nb = defaultdict(int), defaultdict(int), {}
    req_chain = {}
    pool = {"walks": 0, "cfree": 0, "dead_lb": 0, "live_ub": 0, "free": 0}
    t_first = t_last = None
    for r in read_log(path):
        k = r.get("k")
        t = r.get("t")
        if isinstance(t, (int, float)):
            t_first = t if t_first is None else t_first
            t_last = t
        if k == "hdr":
            hdr = r
        elif k == "chain":
            chains[r["c"]] = r.get("p", "un")
        elif k == "arr":
            arr[r["r"]] = r
            req_chain[r["r"]] = r["c"]
            queued[r["c"]] += 1
            last_nb[r["c"]] = r["np"] // (hdr.get("bs") or 16)
        elif k == "adm":
            if not r.get("res"):
                adm[r["r"]] = r
                c = req_chain.get(r["r"])
                if c is not None and queued[c] > 0:
                    queued[c] -= 1
                    running[c] += 1
        elif k == "fin":
            fin[r["r"]] = r
            c = req_chain.get(r["r"])
            if c is not None:
                if running[c] > 0:
                    running[c] -= 1
                elif queued[c] > 0:
                    queued[c] -= 1
        elif k == "pool":
            # a running chain's current prompt blocks are referenced, so every cached-free block it owns is
            # dead (an older version or a reply); a queued or idle chain can own at most its latest prompt's
            # blocks live. The excess is dead: a lower bound on garbage, an upper bound on live.
            dead_lb = 0
            for ci, nfree, _pos in r.get("res", []):
                if running[ci] > 0 and queued[ci] == 0:
                    dead_lb += nfree
                else:
                    dead_lb += max(0, nfree - last_nb.get(ci, 0))
            pool["walks"] += 1
            pool["cfree"] += r.get("cfree", 0)
            pool["free"] += r.get("free", 0)
            pool["dead_lb"] += dead_lb
            pool["live_ub"] += max(0, r.get("cfree", 0) - dead_lb)
        elif k == "ev":
            for ci, cnt, live, dead, state, _idle in r["g"]:
                ev["n"] += cnt
                ev["live"] += live
                ev["dead"] += dead
                ev["live_idle" if state == "idle" else "live_busy"] += live
        elif k == "step":
            pf = r.get("pf") or [0, 0, 0]
            dcr = r.get("dcr") or []
            if dcr and not any(pf):
                ctx = sum(arr[ri]["np"] for ri in dcr if ri in arr)
                kv = STEP["dec_ctx"] * ctx / 1e6
                tm = STEP["fixed"] + STEP["dec_seq"] * len(dcr) + kv
                bw["steps"] += 1
                bw["t"] += tm
                bw["kv"] += kv
                bw["batch"] += len(dcr)
                bw["ctx"] += ctx
    return {"hdr": hdr, "chains": chains, "arr": arr, "adm": adm, "fin": fin, "ev": ev, "bw": bw,
            "pool": pool, "span_s": (t_last - t_first) if t_first is not None else 0.0}


def chain_calls(srv: dict) -> dict[int, list[dict]]:
    by = defaultdict(list)
    for ri, a in srv["arr"].items():
        by[a["c"]].append(a)
    for calls in by.values():
        calls.sort(key=lambda a: a["t"])
    return by


def analyze_cell(cell: str, root: Path) -> dict | None:
    paths = sorted((root / cell / "server").glob("*/sched.jsonl.xz"))
    if not paths:
        return None
    acc = defaultdict(float)
    growth_all, gaps, edit_pos = [], [], []
    gap_rows = {b: [0, 0.0, 0.0, 0] for b in GAP_BINS}       # n, kept at arrival, kept at admission, n_ideal
    ev_tot = defaultdict(int)
    bw_tot = defaultdict(float)
    pool_tot = defaultdict(float)
    nblk = bs = None
    span_min = 0.0
    for p in paths:
        srv = analyze_server(p)
        nblk = srv["hdr"].get("nblk", nblk)
        bs = srv["hdr"].get("bs", bs) or 16
        span_min += srv["span_s"] / 60.0
        for kk, v in srv["ev"].items():
            ev_tot[kk] += v
        for kk, v in srv["bw"].items():
            bw_tot[kk] += v
        for kk, v in srv["pool"].items():
            pool_tot[kk] += v
        out_tok = {ri: f.get("no", 0) for ri, f in srv["fin"].items()}
        for ci, calls in chain_calls(srv).items():
            lead = srv["chains"].get(ci) == "ld"
            # the chain's typical append (reply + new tool results), from its append-only calls
            apps = []
            for prev, cur in zip(calls, calls[1:]):
                if prev["np"] - cur["lcp"] <= APPEND_MAX and cur["np"] >= prev["np"]:
                    apps.append(cur["np"] - prev["np"])
            app_med = statistics.median(apps) if apps else None
            if lead:
                growth_all.extend(apps)
            for i, cur in enumerate(calls):
                a = srv["adm"].get(cur["r"])
                hit = a["hit"] if a else cur.get("hit_now", 0)
                np_ = cur["np"]
                comp = max(0, np_ - hit)
                acc["calls"] += 1
                acc["prompt"] += np_
                acc["computed"] += comp
                acc["ideal"] += cur.get("ideal", 0)
                acc["evicted"] += max(0, cur.get("ideal", 0) - hit)
                if lead:
                    acc["lead_calls"] += 1
                    acc["out"] += out_tok.get(cur["r"], 0)
                    g = cur.get("gap")
                    if g is not None and cur.get("ideal", 0) > 0:
                        gaps.append(g)
                        for b in GAP_BINS:
                            if b[0] <= g < b[1]:
                                row = gap_rows[b]
                                row[0] += 1
                                row[1] += min(1.0, cur.get("hit_now", 0) / cur["ideal"])
                                row[2] += min(1.0, hit / cur["ideal"])
                                break
                if i == 0:
                    acc["first"] += 1
                    continue
                prev = calls[i - 1]
                d = prev["np"] - cur["lcp"]
                prev_out = out_tok.get(prev["r"], 0)
                dead_blocks = max(0, (prev["np"] + prev_out) // bs - cur["lcp"] // bs)
                acc["dead_blocks"] += dead_blocks
                if d <= APPEND_MAX:
                    acc["append"] += 1
                    continue
                acc["edit"] += 1
                after = max(0, np_ - cur["lcp"])
                acc["edit_after"] += after
                acc["edit_dead"] += d
                acc["edit_computed"] += comp
                est_app = app_med if app_med is not None else 0
                acc["edit_reusable"] += max(0, after - est_app - PLACEHOLDER)
                if lead:
                    edit_pos.append(cur["lcp"] / max(1, prev["np"]))
    if not acc["calls"]:
        return None
    pct = lambda a, b: 100.0 * a / b if b else float("nan")  # noqa: E731
    q = lambda xs, f: (statistics.quantiles(xs, n=100)[int(f * 100) - 1] if len(xs) >= 100  # noqa: E731
                       else (sorted(xs)[min(len(xs) - 1, int(f * len(xs)))] if xs else float("nan")))
    res = {
        "cell": cell, "servers": len(paths), "nblk": nblk, "minutes": round(span_min, 1),
        "calls": int(acc["calls"]), "lead_calls": int(acc["lead_calls"]),
        "prompt_mean": acc["prompt"] / acc["calls"], "computed_mean": acc["computed"] / acc["calls"],
        "out_mean": acc["out"] / max(1, acc["lead_calls"]),
        "edit_pct_calls": pct(acc["edit"], acc["calls"] - acc["first"]),
        "edit_after_mean": acc["edit_after"] / max(1, acc["edit"]),
        "edit_dead_mean": acc["edit_dead"] / max(1, acc["edit"]),
        "edit_share_computed": pct(acc["edit_computed"], acc["computed"]),
        "reusable_share_computed": pct(acc["edit_reusable"], acc["computed"]),
        "evicted_share_computed": pct(acc["evicted"], acc["computed"]),
        "edit_pos_median": statistics.median(edit_pos) if edit_pos else float("nan"),
        "growth_median": statistics.median(growth_all) if growth_all else float("nan"),
        "dead_blocks_per_min": acc["dead_blocks"] / max(1e-9, span_min),
        "dead_pool_per_min": pct(acc["dead_blocks"] / max(1e-9, span_min), nblk or 1),
        "ev_blocks": ev_tot["n"], "ev_dead_pct": pct(ev_tot["dead"], ev_tot["n"]),
        "ev_live_idle_pct": pct(ev_tot["live_idle"], ev_tot["live"]),
        "gap_p50": q(gaps, 0.50), "gap_p90": q(gaps, 0.90),
        "gap_rows": {f"{lo}-{hi}": (row[0], row[1] / row[0] if row[0] else float("nan"),
                                    row[2] / row[0] if row[0] else float("nan"))
                     for (lo, hi), row in gap_rows.items()},
        "bw_steps": int(bw_tot["steps"]),
        "bw_batch": bw_tot["batch"] / max(1, bw_tot["steps"]),
        "bw_ctx_k": bw_tot["ctx"] / max(1, bw_tot["steps"]) / 1e3,
        "bw_kv_share": pct(bw_tot["kv"], bw_tot["t"]),
        "pool_walks": int(pool_tot["walks"]),
        "pool_cfree_pct": pct(pool_tot["cfree"] / max(1, pool_tot["walks"]), nblk or 1),
        "pool_empty_pct": pct((pool_tot["free"] - pool_tot["cfree"]) / max(1, pool_tot["walks"]), nblk or 1),
        "pool_dead_lb_pct": pct(pool_tot["dead_lb"] / max(1, pool_tot["walks"]), nblk or 1),
        "pool_live_ub_pct": pct(pool_tot["live_ub"] / max(1, pool_tot["walks"]), nblk or 1),
        "pool_dead_of_cfree": pct(pool_tot["dead_lb"], pool_tot["cfree"]),
    }
    return res


def fmt(x, d=1):
    if isinstance(x, float):
        if math.isnan(x):
            return "–"
        return f"{x:,.{d}f}"
    return f"{x:,}"


def tables(rows: list[dict]) -> str:
    out = ["# KV lifecycle of agent sessions on one vLLM server (11's logs, offline)", "",
           "Generated by `research/15_agent_serving_synthesis/kv_lifecycle.py` from the MUScheduler logs of "
           "`research/11_multiuser_kv` (Qwen3-32B, one H200, 16,137 blocks of 16 tokens = 258,192 tokens). "
           "Read-only; definitions in the script's docstring. Whole logs, warm-up and drain included.", ""]
    out += ["## 1. Edits: how often a call rewrites its session's earlier prompt", "",
            "| cell | calls | prompt tok, mean | computed tok per call, mean | output tok per lead call | calls that edit (% of non-first) | "
            "tokens after the edit point, mean | old tokens killed per edit, mean | edit calls' computed tokens, % of all computed | "
            "shift-reusable (estimate), % of all computed | evicted, % of all computed | edit point, median share of previous prompt | "
            "append per call, median tok |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        out.append("| " + " | ".join([r["cell"], fmt(r["calls"]), fmt(r["prompt_mean"], 0), fmt(r["computed_mean"], 0),
                                      fmt(r["out_mean"], 0), fmt(r["edit_pct_calls"]), fmt(r["edit_after_mean"], 0),
                                      fmt(r["edit_dead_mean"], 0), fmt(r["edit_share_computed"]),
                                      fmt(r["reusable_share_computed"]), fmt(r["evicted_share_computed"]),
                                      fmt(r["edit_pos_median"], 2), fmt(r["growth_median"], 0)]) + " |")
    out += ["", "*Key.* **edit**: the call's prompt diverges from the same chain's previous prompt more than "
            f"{APPEND_MAX} tokens before that prompt's end (an append-only call loses only the re-rendered tail block). "
            "**old tokens killed**: tokens of the previous prompt after the divergence point; their cached blocks can never "
            "be hit again. **shift-reusable (estimate)**: tokens after the edit point minus the chain's median append and a "
            f"{PLACEHOLDER}-token placeholder, i.e. surviving old content that prefix caching recomputes and a position-shifted "
            "reuse (research/10) would keep; an estimate, not a replay.", ""]
    out += ["## 2. Garbage: dead blocks in the pool", "",
            "| cell | minutes | evicted blocks | dead, % of evicted | live evictions with the owner idle, % | dead blocks produced per minute | same, % of the pool per minute |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        out.append("| " + " | ".join([r["cell"], fmt(r["minutes"]), fmt(r["ev_blocks"]), fmt(r["ev_dead_pct"]),
                                      fmt(r["ev_live_idle_pct"]), fmt(r["dead_blocks_per_min"], 0),
                                      fmt(r["dead_pool_per_min"])]) + " |")
    out += ["", "*Key.* **dead**: an evicted cached block that its owner chain's next request cannot use (a superseded "
            "suffix after an edit, a re-rendered reply, the template tail), as the scheduler classified it at eviction. "
            "**dead blocks produced**: per call, the previous request's full blocks (prompt + output) minus the blocks "
            "still on the new prompt's common prefix, summed over the log.", ""]
    out += ["## 3. Reuse distance and what survives it (lead chains)", "",
            "| cell | gap p50 (s) | gap p90 (s) | " + " | ".join(
                f"{b} s: calls / kept at arrival / kept at admission" for b in rows[0]["gap_rows"]) + " |",
            "|---|---:|---:|" + "---|" * len(rows[0]["gap_rows"])]
    for r in rows:
        cells = []
        for b, (n, ka, kd) in r["gap_rows"].items():
            cells.append(f"{fmt(n)} / {fmt(ka, 2)} / {fmt(kd, 2)}" if n else "–")
        out.append("| " + " | ".join([r["cell"], fmt(r["gap_p50"], 2), fmt(r["gap_p90"], 1)] + cells) + " |")
    out += ["", "*Key.* **gap**: seconds from the chain's previous finish to this arrival (tool time, harness time, "
            "think time). **kept**: mean share of the arrival's ideal prefix still cached at arrival, and at admission "
            "(the difference was lost while the request queued).", ""]
    out += ["## 4. Bandwidth: decode-only steps", "",
            "| cell | decode-only steps | decoding sequences per step | context tokens read per step (k) | share of step time reading KV |",
            "|---|---:|---:|---:|---:|"]
    for r in rows:
        out.append("| " + " | ".join([r["cell"], fmt(r["bw_steps"]), fmt(r["bw_batch"]), fmt(r["bw_ctx_k"]),
                                      fmt(r["bw_kv_share"])]) + " |")
    out += ["", "*Key.* modeled with 11's pooled step model: a decode-only step costs 18.46 ms (weights and launch) + "
            "0.028 ms per sequence + 77.3 ns per context token of every decoding sequence (≈ 256 KiB per token at ~3.4 TB/s, "
            "70 % of the H200's 4.8 TB/s). Context = the request's prompt tokens (its own output excluded).", ""]
    out += ["## 5. What the free part of the pool holds (pool walks every ~2 s)", "",
            "| cell | pool walks | cached-free, % of pool | empty, % of pool | dead (lower bound), % of pool | live (upper bound), % of pool | dead, % of cached-free |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        out.append("| " + " | ".join([r["cell"], fmt(r["pool_walks"]), fmt(r["pool_cfree_pct"]), fmt(r["pool_empty_pct"]),
                                      fmt(r["pool_dead_lb_pct"]), fmt(r["pool_live_ub_pct"]),
                                      fmt(r["pool_dead_of_cfree"])]) + " |")
    out += ["", "*Key.* the rest of the pool is referenced by running requests. **dead (lower bound)**: cached-free blocks "
            "owned by a chain whose current request is running (its current prompt is referenced, so what it still owns "
            "in the free queue is an older version or a reply), plus, for a queued or idle chain, whatever it owns beyond "
            "its latest prompt's length. This is the capacity an edit-aware collector could hand back without losing a "
            "single reusable block.", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", default=",".join(CELLS))
    ap.add_argument("--root", default=str(ROOT11))
    ap.add_argument("--out", default=str(HERE / "data/tables/kv_lifecycle.md"))
    args = ap.parse_args()
    rows = []
    for cell in [c for c in args.cells.split(",") if c]:
        r = analyze_cell(cell, Path(args.root))
        if r is None:
            print(f"{cell}: no scheduler log", file=sys.stderr)
            continue
        print(f"{cell}: {r['calls']} calls, edits {r['edit_pct_calls']:.1f} %, dead evictions {r['ev_dead_pct']:.1f} %, "
              f"KV share of decode {r['bw_kv_share']:.1f} %", file=sys.stderr)
        rows.append(r)
    if not rows:
        sys.exit("nothing to report")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tables(rows) + "\n")
    (out.parent.parent / "kv_lifecycle.json").write_text(json.dumps(rows, indent=1, default=str))
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
