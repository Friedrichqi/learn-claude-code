#!/usr/bin/env python3
"""S0 of research/13_tiered_memory: the most each proposed idea (D1, B1, B2, B3) can win, computed offline
from 11's and 12's existing scheduler logs and request captures. No GPU and no new runs.

    python research/13_tiered_memory/tm_ceilings.py                     # every cell -> data/s0/, data/tables/
    python research/13_tiered_memory/tm_ceilings.py --cells p1a,n16 --jobs 2
    sbatch --export=NONE research/13_tiered_memory/tm_cpu.sbatch tm_ceilings.py

One row per level (a cell at one N). Definitions:
  calls      every model call of the level that has a request capture; "call time" is the client's
             send -> receive (capture t_send / t_recv), as in 12's call_breakdown.
  D1  fe_in      client send -> scheduler arrival (SDK + HTTP + parse + chat template + tokenise + IPC);
                 the slope (ms per 1k prompt tokens) is a least-squares fit over the server's calls.
      fe_d1      the part D1 removes: the per-token cost fitted on the same setup's N = 1 cell (where
                 scheduler arrival never waits for a running engine step) x the prompt tokens shared with
                 the chain's previous call (arrival `lcp`), which D1 neither re-sends nor re-renders.
                 At higher load fe_in also holds the wait for the engine step in flight (up to ~1.2 s on
                 the 32B), which D1 does not remove.
      reply      append-only calls (the prompt extends the chain's previous prompt): with D1 the reusable
                 prefix would run to the last full block of the previous prompt + its reply as generated;
                 today it stops 4 tokens short of the previous prompt's end (the template drops the empty
                 think block). Counted only when the previous prefix was still cached (hit >= lcp), in
                 tokens and in GPU seconds from 11's step model (prefill cost at that position).
  B1  evicted    arrival ideal - first-admission hit (11's definition), split by the pause before the call
                 (after a tool call / after a turn end / the chain's first call) and its length, and by
                 when it was lost (during the pause = ideal - hit_now; while queued = hit_now - hit);
                 GPU seconds from 11's step attribution (attribute_steps).
      pauses     the client-side window before each call (its t_send - the previous call's t_recv: harness
                 + tool + think time) by what the previous reply called (`tool_key`: bash split by 06's
                 command class and a scope), with the server-side gap and the evicted tokens after it.
                 Raw rows go to data/s0/pauses.jsonl.xz for tm_predict.py.
  B2  restores   offload cells only: calls that loaded KV from the CPU tier (num_external_cached_tokens > 0),
                 tokens loaded, load wait (`adm.load`), load wait as a share of call time, tokens per second
                 of load wait.
  B3  decode lost behind prefill: for each engine step that ran prefill chunks and decodes, its GPU time
                 minus the step model's time for the same decodes alone, charged to every request decoding
                 in it (each waits the whole step); per call, vs its decode time (first token -> finish)
                 and its call time.
GPU busy is the sum of step GPU intervals [max(previous u0, launch), u0] (11/12's definition).
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: E402,F401

import mu_analyze  # noqa: E402  (research/11_multiuser_kv)
from tm_common import tool_key  # noqa: E402  (bash split by 06's command class + scope)

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
OUT = HERE / "data" / "s0"
TABLES = HERE / "data" / "tables"
C12 = RESEARCH / "12_hw_utilization" / "data" / "cells"
C11 = RESEARCH / "11_multiuser_kv" / "data" / "multiuser_live" / "cells"
BS = 16
THINK_TOKENS = 4                     # "<think>\n\n</think>\n\n" of the generation prompt, dropped on re-render
GAP_BUCKETS = ((0, 1), (1, 15), (15, 60), (60, 120), (120, math.inf))

# cell -> (cell dir, model, setup); 11's cells are one level each, 12's levels come from the request ids
CELLS = {
    "p1a": (C12 / "p1a", "Qwen3-32B", "12 · H200 TP=1 · LIGHT x8, HTEST x3, HSEARCH x3 in sequence"),
    "p1b": (C12 / "p1b", "Qwen3-8B", "12 · H200 TP=1 · LIGHT x8, HTEST x3, HSEARCH x3 in sequence"),
    "p2a": (C12 / "p2a", "Qwen3-32B", "12 · H200 TP=1 · LIGHT closed loop"),
    "p2b": (C12 / "p2b", "Qwen3-32B", "12 · H200 TP=1 · HEAVY mix closed loop"),
    "p2c": (C12 / "p2c", "Qwen3-8B", "12 · H200 TP=1 · LIGHT (l) / HEAVY (h) closed loop"),
    "o1": (C12 / "o1", "Qwen3-32B", "12 · H200 TP=1 · LIGHT closed loop · CPU offload 128 GiB"),
    "t2": (C12 / "t2", "Qwen3-32B", "12 · H200 TP=2 · LIGHT closed loop"),
    "n01a": (C11 / "n01a", "Qwen3-32B", "11 · H200 TP=1 · 4-turn sessions, think time"),
    "n04": (C11 / "n04", "Qwen3-32B", "11 · H200 TP=1 · 4-turn sessions, think time"),
    "n08": (C11 / "n08", "Qwen3-32B", "11 · H200 TP=1 · 4-turn sessions, think time"),
    "n12": (C11 / "n12", "Qwen3-32B", "11 · H200 TP=1 · 4-turn sessions, think time"),
    "n16": (C11 / "n16", "Qwen3-32B", "11 · H200 TP=1 · 4-turn sessions, think time"),
    "n32": (C11 / "n32", "Qwen3-32B", "11 · H200 TP=1 · 4-turn sessions, think time"),
    "r_n16_off128": (C11 / "r_n16_off128", "Qwen3-32B", "11 replay · H200 TP=1 · CPU offload 128 GiB"),
    "r_n32_off128": (C11 / "r_n32_off128", "Qwen3-32B", "11 replay · H200 TP=1 · CPU offload 128 GiB"),
}
SKIP_LEVELS = {"hw_smoke32", "hw_smoke8"}
# the N = 1 level whose fitted per-token front-end cost stands for each cell's setup (no engine-step waits)
FE_REF = {"p1a": "hw_p1a", "p2a": "hw_p1a", "p2b": "hw_p1a", "o1": "hw_p1a", "p1b": "hw_p1b", "p2c": "hw_p1b",
          "t2": "hw_t2_n01", **{c: "n01a" for c in ("n01a", "n04", "n08", "n12", "n16", "n32")}}


# -- helpers --------------------------------------------------------------------------------------------------
def level_of(ext_id: str) -> str:
    """'MU-hw_p2a_n08-L1000~a1~00001~ld~root' -> 'hw_p2a_n08'; 'MU-n16-F000-w001~...' -> 'n16'."""
    label = ext_id.split("~", 1)[0]
    parts = label.split("-")
    return parts[1] if len(parts) > 1 and parts[0] == "MU" else label


def purpose_of(ext_id: str) -> str:
    parts = ext_id.split("~")
    return parts[3] if len(parts) == 5 else "un"


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fit_line(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least squares y = a + b x; (0, 0) when degenerate."""
    if len(xs) < 3:
        return 0.0, 0.0
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return my, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def read_captures(cell_dir: Path) -> dict[str, dict]:
    """request id -> capture (t_send, t_recv, the tools the reply called)."""
    out = {}
    for path in sorted(cell_dir.glob("**/runs/*/*.requests.jsonl*")):
        opener = lzma.open if path.suffix == ".xz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    c = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = c.get("request_id")
                if not rid or c.get("t_send") is None or c.get("t_recv") is None:
                    continue
                tools = []
                for block in ((c.get("response") or {}).get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools.append(tool_key(block.get("name") or "?", block.get("input")))
                out[rid] = {"t_send": c["t_send"], "t_recv": c["t_recv"], "tools": tools}
    return out


def pure_decode_cost(coef: dict, srv, step) -> float:
    """The step model's GPU time for this step's decodes alone (no prefill chunks, no mixed-step term)."""
    total = max(coef["fixed"], 0.0)
    for ri in step["dcr"]:
        rec = srv.req.get(ri)
        ctx = rec["arr"]["np"] if rec else 0
        total += max(coef["dec_seq"] + coef["dec_ctx"] * ctx / 1e6, 0.0)
    return total


def prefill_cost(coef: dict, a: int, n: int) -> float:
    return max(coef["pf_tok"] * n / 1e3 + coef["pf_attn"] * n * (a + n / 2) / 1e6, 0.0)


# -- one server log -------------------------------------------------------------------------------------------
def analyze_server(args: tuple[str, str]) -> list[dict]:
    cell, server_dir = args
    cell_dir, model, setup = CELLS[cell]
    srv = mu_analyze.Server(Path(server_dir))
    if not srv.steps:
        return []
    mu_analyze.step_features([srv])
    fit = mu_analyze.fit_step_model([srv])
    coef = fit.get("coef")
    if coef is None:
        return []
    mu_analyze.attribute_steps([srv], fit)
    caps = read_captures(cell_dir)
    connector = srv.hdr.get("connector")

    # chain order, previous call of each request
    by_chain = defaultdict(list)
    for ri, rec in srv.req.items():
        by_chain[rec["arr"]["c"]].append(ri)
    prev_of = {}
    for rs in by_chain.values():
        rs.sort(key=lambda r: srv.req[r]["arr"]["t"])
        for a, b in zip(rs, rs[1:]):
            prev_of[b] = a

    # front-end slope over the server's calls
    xs, ys = [], []
    for ri, rec in srv.req.items():
        cap = caps.get(rec["arr"]["id"])
        if cap and rec["arr"].get("wall") is not None:
            fe_in = rec["arr"]["wall"] - cap["t_send"]
            if 0 <= fe_in < 5:
                xs.append(rec["arr"]["np"])
                ys.append(fe_in)
    fe_a, fe_b = fit_line(xs, ys)

    # GPU seconds per request: evicted prefill (attributed) and decode lost behind prefill
    ev_gpu = defaultdict(float)
    infl = defaultdict(float)
    level_gpu = Counter()
    level_pf = Counter()
    for s in srv.steps:
        gpu = s.get("gpu")
        sh = s.get("share")
        ids = [c[0] for c in s["pfl"]] + list(s["dcr"])
        levels = Counter(level_of(srv.req[ri]["arr"]["id"]) for ri in ids if ri in srv.req)
        lev = levels.most_common(1)[0][0] if levels else None
        if gpu is None or gpu <= 0 or lev is None:
            continue
        level_gpu[lev] += gpu
        level_pf[lev] += sum(c[2] for c in s["pfl"])
        if sh:
            k = len({c[0] for c in sh["pf"]} | {d[0] for d in sh["dec"]}) or 1
            base = sh["fixed"] / k
            for ri, cost, n, fresh, ev, pre in sh["pf"]:
                if n and ev:
                    ev_gpu[ri] += (cost + base) * ev / n
        if s["pfl"] and s["dcr"]:
            lost = max(0.0, gpu - pure_decode_cost(coef, srv, s))
            for ri in s["dcr"]:
                infl[ri] += lost

    rows = {}
    pause_rows: list[dict] = []
    for ri, rec in srv.req.items():
        arr = rec["arr"]
        lev = level_of(arr["id"])
        if lev in SKIP_LEVELS:
            continue
        r = rows.setdefault(lev, defaultdict(float, {"pauses": defaultdict(list), "ev_by": Counter(),
                                                     "ev_gpu_by": Counter(), "ev_gap": Counter()}))
        cap = caps.get(arr["id"])
        fin = rec["fin"]
        firsts = [x for x in rec["adm"] if not x["res"]]
        hit = firsts[0]["hit"] if firsts else None
        r["requests"] += 1
        if fin is None or cap is None or hit is None:
            continue
        dur = cap["t_recv"] - cap["t_send"]
        lead = purpose_of(arr["id"]) == "ld"
        r["calls"] += 1
        r["lead_calls"] += lead
        r["call_s"] += dur
        r["prompt_tok"] += arr["np"]
        # D1: front end
        fe_in = max(0.0, arr["wall"] - cap["t_send"]) if arr.get("wall") is not None else 0.0
        r["fe_in_s"] += fe_in
        if arr.get("fe") is not None:
            r["fe_http_s"] += min(fe_in, max(0.0, arr["fe"] - cap["t_send"]))
        prev = prev_of.get(ri)
        lcp = arr.get("lcp") or 0
        if prev is not None:
            r["lcp_tok"] += lcp
        # D1: the previous reply's re-prefill (append-only calls whose prefix survived)
        if prev is not None and srv.req[prev]["fin"] is not None:
            p = srv.req[prev]
            np_k, no_k = p["arr"]["np"], p["fin"]["no"]
            if lcp >= (np_k - THINK_TOKENS) // BS * BS - BS:
                r["append_calls"] += 1
                if hit >= lcp:
                    reuse = min(np_k + no_k, arr["np"] - 1) // BS * BS
                    gain = max(0, reuse - max(lcp, hit))
                    r["reply_tok"] += gain
                    r["reply_gpu_s"] += prefill_cost(coef, max(lcp, hit), gain)
                    r["reply_calls"] += 1
        # B1: eviction by pause type and length
        ideal = max(hit, arr["ideal"])
        evicted = ideal - hit
        lost_pause = max(0, ideal - max(arr["hit_now"], hit))
        if prev is None:
            kind = "first"
        else:
            pf = srv.req[prev]["fin"]
            kind = "tool" if pf and pf.get("tool") else "turn"
        gap = arr.get("gap")
        bucket = next((f"{lo}-{hi}s" for lo, hi in GAP_BUCKETS if gap is not None and lo <= gap < hi), "n/a")
        r["evicted_tok"] += evicted
        r["lost_pause_tok"] += lost_pause
        r["lost_queue_tok"] += evicted - lost_pause
        r["evicted_gpu_s"] += ev_gpu.get(ri, 0.0)
        r["ev_by"][kind] += evicted
        r["ev_gpu_by"][kind] += ev_gpu.get(ri, 0.0)
        if kind != "first":
            r["ev_gap"][bucket] += evicted
        pcap = caps.get(srv.req[prev]["arr"]["id"]) if prev is not None else None
        if pcap is not None and gap is not None:
            key = "+".join(sorted(set(pcap["tools"]))) or ("(turn end)" if kind == "turn" else "(no tool)")
            window = cap["t_send"] - pcap["t_recv"]
            r["pauses"][key].append((window, evicted))
            pause_rows.append({"cell": cell, "level": lev, "model": model, "key": key, "n_tools": len(pcap["tools"]),
                               "window_s": round(window, 4), "gap_s": gap, "evicted": evicted})
        # B2: restores from the CPU tier
        he = firsts[0].get("he") or 0
        if he > 0:
            r["load_calls"] += 1
            r["load_tok"] += he
            r["load_s"] += firsts[0].get("load") or 0.0
        # B3: decode lost behind prefill
        if rec["tok1"] is not None:
            r["decode_s"] += max(0.0, fin["t"] - rec["tok1"])
        r["decode_lost_s"] += infl.get(ri, 0.0)

    out = []
    for lev, r in rows.items():
        if not r["calls"]:
            continue
        pauses = {}
        for tool, items in r["pauses"].items():
            windows = [w for w, _ in items]
            pauses[tool] = {"n": len(items), "p50_s": q(windows, 0.5), "p90_s": q(windows, 0.9),
                            "evicted_tok": sum(e for _, e in items)}
        rec = {k: v for k, v in r.items() if not isinstance(v, (dict, Counter, defaultdict))}
        rec.update({"cell": cell, "level": lev, "model": model, "setup": setup, "server": Path(server_dir).name,
                    "connector": connector, "gpu_busy_s": level_gpu.get(lev, 0.0),
                    "prefill_tok": level_pf.get(lev, 0), "fe_fit_ms": [1000 * fe_a, 1000 * 1000 * fe_b],
                    "step_model": {"r2": fit.get("r2"), "median_ape": fit.get("median_ape"), "n": fit.get("n"),
                                   "coef": coef},
                    "ev_by": dict(r["ev_by"]), "ev_gpu_by": dict(r["ev_gpu_by"]), "ev_gap": dict(r["ev_gap"]),
                    "pauses": pauses})
        out.append(rec)
    return [{"_pauses": pause_rows}] + out


# -- tables ---------------------------------------------------------------------------------------------------
def pct(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f} %"


def num(x, d=0):
    return "—" if x is None else f"{x:,.{d}f}"


def div(a, b):
    return a / b if b else None


def n_of(level: str) -> str:
    m = re.search(r"[nlh](\d+)[ab]?$", level)
    return str(int(m.group(1))) if m else ("1" if level in ("hw_p1a", "hw_p1b", "n01a") else "?")


def tables(rows: list[dict]) -> str:
    rows = sorted(rows, key=lambda r: (list(CELLS).index(r["cell"]), r["level"]))
    md = ["# S0 · What each idea can win at most (existing 11/12 data, no GPU)", "",
          "Generated by `tm_ceilings.py`; definitions in its docstring. Shares of **call time** are summed over",
          "calls (Σ part / Σ client send → receive); shares of **GPU** are of the level's GPU busy time.", ""]
    md += ["## D1 · front end and the previous reply's re-prefill", "",
           "| cell | level | N | calls | mean call s | mean prompt tokens | SDK + HTTP + parse, ms/call | template + tokenise (+ engine-step wait), ms/call | "
           "front end, share of call time | D1 removes, share of call time | append-only calls | reply tokens re-prefilled per call | reply re-prefill, share of prefill tokens | share of GPU |",
           "|" + "---|" * 14]
    by_level = {r["level"]: r for r in rows}
    for r in rows:
        ref = by_level.get(FE_REF.get(r["cell"], ""))
        slope = ref["fe_fit_ms"][1] / 1e6 if ref else None            # s per token
        d1 = slope * r["lcp_tok"] if slope is not None else None
        md.append(f"| {r['cell']} | {r['level']} | {n_of(r['level'])} | {num(r['calls'])} | {num(div(r['call_s'], r['calls']), 1)} | "
                  f"{num(div(r['prompt_tok'], r['calls']))} | {num(1000 * div(r['fe_http_s'], r['calls']), 0)} | "
                  f"{num(1000 * div(r['fe_in_s'] - r['fe_http_s'], r['calls']), 0)} | {pct(div(r['fe_in_s'], r['call_s']))} | "
                  f"{pct(div(d1, r['call_s']))} | "
                  f"{num(r['append_calls'])} | {num(div(r['reply_tok'], r['reply_calls']), 0)} | "
                  f"{pct(div(r['reply_tok'], r['prefill_tok']))} | {pct(div(r['reply_gpu_s'], r['gpu_busy_s']))} |")
    refs = sorted({FE_REF[c] for c in FE_REF if FE_REF[c] in by_level})
    md += ["", "*Key.* **front end**: client send → scheduler arrival. Its first part (send → vLLM starts rendering)",
           "is the SDK, HTTP and request parsing; the second is the chat template, tokenisation and the hand-off to",
           "the engine, which at higher load also waits for the engine step in flight (up to ~1.2 s on the 32B;",
           "D1 does not remove that wait). **D1 removes**: the per-token front-end cost fitted on the same setup's",
           "N = 1 cell (" + "; ".join(f"{x}: {by_level[x]['fe_fit_ms'][0]:.0f} ms + {by_level[x]['fe_fit_ms'][1]:.2f} ms per 1k tokens" for x in refs) + ")",
           "× the prompt tokens shared with the chain's previous call.",
           "**reply re-prefill**: tokens of the previous reply (as generated) that today are prefilled again.", ""]
    md += ["## B1 · recompute caused by eviction", "",
           "| cell | level | N | prefill tokens | evicted, share of prefill | evicted, share of GPU | after a tool call | after a turn end | chain's first call | lost during the pause | lost while queued |",
           "|" + "---|" * 11]
    for r in rows:
        ev = r["evicted_tok"]
        md.append(f"| {r['cell']} | {r['level']} | {n_of(r['level'])} | {num(r['prefill_tok'])} | {pct(div(ev, r['prefill_tok']))} | "
                  f"{pct(div(r['evicted_gpu_s'], r['gpu_busy_s']))} | {pct(div(r['ev_by'].get('tool', 0), ev))} | "
                  f"{pct(div(r['ev_by'].get('turn', 0), ev))} | {pct(div(r['ev_by'].get('first', 0), ev))} | "
                  f"{pct(div(r['lost_pause_tok'], ev))} | {pct(div(r['lost_queue_tok'], ev))} |")
    md += ["", "*Key.* **evicted**: tokens inside the arrival ideal hit that the call did not hit (11's definition).",
           "The three \"after\" columns split the evicted tokens by the pause before the call; the last two by",
           "when the prefix was lost.", "", "Evicted tokens by pause length (calls after a pause):", "",
           "| cell | level | " + " | ".join(f"{lo}-{hi if hi != math.inf else '∞'} s" for lo, hi in GAP_BUCKETS) + " |",
           "|" + "---|" * (2 + len(GAP_BUCKETS))]
    for r in rows:
        tot = sum(r["ev_gap"].values())
        if tot:
            md.append(f"| {r['cell']} | {r['level']} | " + " | ".join(
                pct(div(r["ev_gap"].get(f"{lo}-{hi}s", 0), tot), 0) for lo, hi in GAP_BUCKETS) + " |")
    md += ["", "Pauses by what the previous reply called (levels with heavy tools or think time; top 6 per level):", "",
           "| cell | level | tool | pauses | p50 s | p90 s | evicted tokens after it |", "|" + "---|" * 7]
    for r in rows:
        if r["cell"] not in ("p1a", "p1b", "p2b", "p2c", "n08", "n16"):
            continue
        top = sorted(r["pauses"].items(), key=lambda kv: -kv[1]["n"] * (kv[1]["p50_s"] or 0))[:6]
        for tool, p in top:
            md.append(f"| {r['cell']} | {r['level']} | {tool} | {num(p['n'])} | {num(p['p50_s'], 2)} | {num(p['p90_s'], 1)} | {num(p['evicted_tok'])} |")
    md += ["", "## B2 · restores from the CPU tier (offload cells)", "",
           "| cell | level | calls | calls that loaded | tokens loaded per loading call | load wait per loading call, s | load wait, share of call time | tokens per s of load wait |",
           "|" + "---|" * 8]
    for r in rows:
        if not r.get("connector"):
            continue
        md.append(f"| {r['cell']} | {r['level']} | {num(r['calls'])} | {num(r['load_calls'])} | {num(div(r['load_tok'], r['load_calls']))} | "
                  f"{num(div(r['load_s'], r['load_calls']), 3)} | {pct(div(r['load_s'], r['call_s']), 2)} | {num(div(r['load_tok'], r['load_s']))} |")
    md += ["", "## B3 · decode time lost behind co-scheduled prefill", "",
           "| cell | level | N | decode s per call | lost behind prefill, s per call | lost, share of decode | lost, share of call time |",
           "|" + "---|" * 7]
    for r in rows:
        md.append(f"| {r['cell']} | {r['level']} | {n_of(r['level'])} | {num(div(r['decode_s'], r['calls']), 2)} | "
                  f"{num(div(r['decode_lost_s'], r['calls']), 2)} | {pct(div(r['decode_lost_s'], r['decode_s']))} | "
                  f"{pct(div(r['decode_lost_s'], r['call_s']))} |")
    md += ["", "*Key.* **lost behind prefill**: per decode step that also ran prefill chunks, its GPU time minus the",
           "step model's time for its decodes alone, charged to each request decoding in it.", "",
           "Step model fit per server (11's Huber fit): " + "; ".join(
               f"{r['server']} R² {r['step_model']['r2']:.3f}" for r in {x['server']: x for x in rows}.values()) + "."]
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", default=",".join(CELLS))
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()
    items = []
    for cell in args.cells.split(","):
        cell_dir = CELLS[cell][0]
        for sdir in sorted((cell_dir / "server").glob("*/")):
            if (sdir / "sched.jsonl.xz").exists() or (sdir / "sched.jsonl").exists():
                items.append((cell, str(sdir)))
    print(f"[s0] {len(items)} server logs", flush=True)
    rows, pauses = [], []
    with Pool(min(args.jobs, len(items))) as pool:
        for res in pool.imap_unordered(analyze_server, items):
            if res and "_pauses" in res[0]:
                pauses += res[0]["_pauses"]
                res = res[1:]
            rows += res
            print(f"[s0] {len(rows)} levels", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    with lzma.open(OUT / "pauses.jsonl.xz", "wt", encoding="utf-8") as fh:
        for p in pauses:
            fh.write(json.dumps(p) + "\n")
    (OUT / "ceilings.json").write_text(json.dumps(rows, indent=1, default=float), encoding="utf-8")
    md = tables(rows)
    (TABLES / "S0_ceilings.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
