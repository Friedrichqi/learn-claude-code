#!/usr/bin/env python3
"""Pre-registered bottleneck bounds for every arm of research/14_pd_crossover (PLAN.md §2, §8).

    python research/14_pd_crossover/pd_predict.py [--calib data/calib/JOB] [--out data/tables/S0_predictions.md]
    sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_predict.py
Workload: the lead and memory calls of 12's p2a levels N = 4 and 8 (Qwen3-32B, LIGHT closed loop, H200 TP=1, no
eviction), read from 12's scheduler log and request captures:
  per call  prefill GPU-seconds = sum over its prefill chunks (context a, n tokens) of 11's step model
            pf_tok*n + pf_attn*n*(a + n/2), plus the fixed step cost times the share of an 8,192-token step the call
            fills, with the TP=1 (p2a) or TP=2 (t2) coefficients from 13's S0 fit;
            output tokens L; prompt tokens; new (computed) prompt tokens
  Z         client time between a call's reply and the chain's next call (harness, tools, front end)
Bounds, in calls per second (lead calls are ~99 % of calls):
  X_P   prefill instance        1 / E[prefill GPU-s]
  X_D   decode instance         b_max / (E[L] * t_dec(b_max)), b_max = pool tokens / (mean prompt + E[L]) concurrent
                                decodes; t_dec(b) = fixed + b*dec_seq + dec_ctx * b * mean prompt
  X_C   colocated instance      1 / (E[prefill GPU-s] + E[L] * t_dec(b_max) / b_max)
  arm   pd: min(sum X_P, sum X_D); colocated: sum X_C
  N_sat closed-loop saturation  X_max * (Z + R0), R0 = the uncontended call time (prefill + E[L] * t_dec(1)
                                + KV transfer of the new tokens + router hop for pd)
  N_cap sessions whose KV fits: colocated sum(pool / session tokens); pd: the prefill side's caches, and the decode
                                side's b_max concurrent decodes
The first resource to bind (lowest of N_sat and N_cap) is the predicted mechanism of N*_high. TP=4 coefficients are
not measured: the TP=2 / TP=1 ratio is applied once more (flagged). With --calib, S1's measured KV transfer rate and
router hop replace the defaults (200 GB/s, 2 ms).
"""
from __future__ import annotations

import argparse
import json
import lzma
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_analyze  # noqa: E402  research/11_multiuser_kv
import pd_workloads as pw  # noqa: E402

DIR = Path(__file__).resolve().parent
RESEARCH = DIR.parent
P2A = RESEARCH / "12_hw_utilization" / "data" / "cells" / "p2a"
CEIL = RESEARCH / "13_tiered_memory" / "data" / "s0" / "ceilings.json"
LEVELS = ("hw_p2a_n04", "hw_p2a_n08")
POOL = {1: 258_192, 2: 776_720}                    # tokens per instance, measured (12 T2)
WEIGHTS_GIB, KV1_GIB = 61.03, 63.04


def pool_tokens(tp: int) -> int:
    if tp in POOL:
        return POOL[tp]
    per_gpu = KV1_GIB + WEIGHTS_GIB * (1 - 1 / tp)
    return int(tp * per_gpu * (1 << 30) / pw.KV_BYTES_PER_TOKEN)


def coefs() -> dict[int, dict]:
    rows = json.loads(CEIL.read_text())
    c1 = next(r["step_model"]["coef"] for r in rows if isinstance(r, dict) and r.get("level") == "hw_p2a_n08")
    c2 = next(r["step_model"]["coef"] for r in rows if isinstance(r, dict) and r.get("level") == "hw_t2_n08")
    c4 = {k: c2[k] * (c2[k] / c1[k]) if c1[k] and c1[k] * c2[k] > 0 else c2[k] for k in c2}
    return {1: c1, 2: c2, 4: c4}


def workload() -> dict:
    srv_dir = next((P2A / "server").iterdir())
    srv = mu_analyze.Server(srv_dir)
    chunks = defaultdict(list)
    for s in srv.steps:
        for c in s["pfl"]:
            chunks[c[0]].append((c[1], c[2]))
    calls = []
    for ri, rec in srv.req.items():
        lev = rec["arr"]["id"].split("~", 1)[0].split("-")
        if len(lev) < 2 or lev[1] not in LEVELS or rec["fin"] is None:
            continue
        calls.append({"np": rec["arr"]["np"], "no": rec["fin"]["no"], "chunks": chunks.get(ri, []),
                      "new": sum(n for _, n in chunks.get(ri, []))})
    gaps = []
    for lvl in LEVELS:
        caps = defaultdict(list)
        for f in sorted((P2A / "levels" / lvl / "runs").glob("*/*.requests.jsonl.xz")):
            with lzma.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        c = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if c.get("t_send") is not None and c.get("t_recv") is not None and c.get("purpose") == "lead":
                        caps[f.parent.name].append((c["t_send"], c["t_recv"]))
        for xs in caps.values():
            xs.sort()
            gaps += [b[0] - a[1] for a, b in zip(xs, xs[1:]) if 0 <= b[0] - a[1] < 120]
    return {"calls": calls, "z": statistics.fmean(gaps) if gaps else 0.5, "n_gaps": len(gaps)}


MNBT = 8192


def prefill_s(c: dict, call: dict) -> float:
    """Minimum GPU-seconds of a call's prefill: the chunks' marginal costs plus the fixed step cost for the share of
    a full 8,192-token step they fill (steps are packed by the token budget)."""
    marg = sum(c["pf_tok"] * n / 1e3 + c["pf_attn"] * n * (a + n / 2) / 1e6 for a, n in call["chunks"])
    return marg + c["fixed"] * call["new"] / MNBT


def t_dec(c: dict, b: int, ctx: float) -> float:
    return c["fixed"] + c["dec_seq"] * b + c["dec_ctx"] * b * ctx / 1e6


def bounds(w: dict, co: dict, xfer_gbs: float, hop_s: float) -> list[dict]:
    calls = w["calls"]
    L = statistics.fmean(x["no"] for x in calls)
    ctx = statistics.fmean(x["np"] for x in calls)
    new = statistics.fmean(x["new"] for x in calls)
    sess = ctx + L
    pre = {tp: statistics.fmean(prefill_s(co[tp], x) for x in calls) for tp in co}
    out = []
    for arm, spec in pw.ARMS.items():
        insts = spec["inst"]
        row = {"arm": arm, "g": spec["g"], "router": spec["router"], "flag": "TP=4 extrapolated"
               if any(i["tp"] == 4 for i in insts) else ""}
        caps, xs = [], []
        if spec["router"] == "pd":
            xp = sum(1 / pre[i["tp"]] for i in insts if i["role"] == "P")
            xd, bd = 0.0, 0
            for i in insts:
                if i["role"] == "D":
                    b = max(1, int(pool_tokens(i["tp"]) // sess))
                    bd += b
                    xd += b / (L * t_dec(co[i["tp"]], b, ctx))
            pcap = sum(int(pool_tokens(i["tp"]) // sess) for i in insts if i["role"] == "P")
            tp_p = next(i["tp"] for i in insts if i["role"] == "P")
            tp_d = next(i["tp"] for i in insts if i["role"] == "D")
            r0 = pre[tp_p] + new * pw.KV_BYTES_PER_TOKEN / (xfer_gbs * 1e9) + hop_s + L * t_dec(co[tp_d], 1, ctx)
            row.update(x_p=xp, x_d=xd, x_max=min(xp, xd), bind="P compute" if xp <= xd else "D capacity x speed",
                       n_cap_p=pcap, b_max_d=bd)
            caps = [("P cache", pcap)]
        else:
            xc = 0.0
            ncap = 0
            for i in insts:
                b = max(1, int(pool_tokens(i["tp"]) // sess))
                xc += 1 / (pre[i["tp"]] + L * t_dec(co[i["tp"]], b, ctx) / b)   # decode steps at full batch
                ncap += int(pool_tokens(i["tp"]) // sess)
            tp = insts[0]["tp"]
            r0 = pre[tp] + L * t_dec(co[tp], 1, ctx)
            row.update(x_c=xc, x_max=xc, bind="GPU compute", n_cap=ncap)
            caps = [("KV capacity", ncap)]
        row["r0"] = r0
        row["n_sat"] = row["x_max"] * (w["z"] + r0)
        first = min([("throughput", row["n_sat"])] + caps, key=lambda t: t[1])
        row["first"] = f"{first[0]} at N ~ {first[1]:.0f}"
        out.append(row)
        del xs
    meta = {"calls": len(calls), "mean_out": L, "mean_prompt": ctx, "mean_new": new, "z_s": w["z"],
            "prefill_s": pre, "pool": {tp: pool_tokens(tp) for tp in co}, "xfer_gbs": xfer_gbs, "hop_s": hop_s}
    return out, meta


def table(rows: list[dict], meta: dict, title: str) -> str:
    h = 3600
    md = [f"# {title}", "",
          f"Workload: {meta['calls']} calls of 12's p2a N = 4 and 8; mean prompt {meta['mean_prompt']:,.0f} tokens, "
          f"new (computed) {meta['mean_new']:,.0f}, output {meta['mean_out']:.0f}; client time Z = {meta['z_s']:.2f} s. "
          f"Prefill GPU-s per call: TP1 {meta['prefill_s'][1]:.3f}, TP2 {meta['prefill_s'][2]:.3f}, TP4 "
          f"{meta['prefill_s'][4]:.3f} (extrapolated). KV pool tokens: "
          + ", ".join(f"TP{k} {v:,}" for k, v in meta["pool"].items())
          + f". KV transfer {meta['xfer_gbs']:.0f} GB/s, router hop {1000 * meta['hop_s']:.1f} ms.", "",
          "| arm | GPUs | prefill bound (calls/h) | decode bound | colocated bound | arm bound | binds | uncontended call s "
          "| N_sat | KV capacity (sessions) | first to bind |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = lambda k: f"{r[k] * h:,.0f}" if k in r else "—"  # noqa: E731
        cap = f"P caches {r['n_cap_p']}, D concurrent {r['b_max_d']}" if "n_cap_p" in r else str(r.get("n_cap"))
        md.append(f"| {pw.display(r['arm'])} | {r['g']} | {f('x_p')} | {f('x_d')} | {f('x_c')} | {r['x_max'] * h:,.0f} "
                  f"| {r['bind']} | {r['r0']:.2f} | {r['n_sat']:.1f} | {cap} | {r['first']} {r['flag']} |")
    md += ["", "*Key.* Bounds are approximate upper limits (minimum GPU-seconds per call from 11's step model, decode "
           "steps at the largest batch the KV pool allows) on calls per hour from one resource each (PLAN.md §2): the prefill "
           "side's compute, the decode side's concurrent-decode capacity times its step speed, or a colocated "
           "instance's compute. **N_sat**: agents at which the closed loop reaches the arm bound (Little's law). "
           "**KV capacity**: sessions whose prefix fits (pd: in the prefill caches; the decode side holds at most "
           "the listed concurrent decodes). The lowest N among them is the predicted mechanism of N*_high."]
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calib", default=None)
    ap.add_argument("--out", default=str(pw.DATA / "tables" / "S0_predictions.md"))
    a = ap.parse_args()
    xfer, hop, title = 200.0, 0.002, "S0 predictions (12's coefficients, assumed transfer rate)"
    if a.calib:
        cd = Path(a.calib)
        g = json.loads((cd / "gates.json").read_text()) if (cd / "gates.json").exists() else {}
        run = (g.get("runs") or {}).get(g.get("G1", {}).get("choice") or "A") or {}
        cal = json.loads((cd / "calib_A.json").read_text()) if (cd / "calib_A.json").exists() else {}
        tr = [x for x in cal.get("transfer", []) if x["nixl_s"] and x["nixl_bytes"]]
        if tr:
            xfer = sum(x["nixl_bytes"] for x in tr) / sum(x["nixl_s"] for x in tr) / 1e9
        if (cd / "hop.json").exists():
            hop = max(0.0, json.loads((cd / "hop.json").read_text())["overhead_ms_p50"] / 1000)
        title = "S1 predictions (frozen before the sweeps; S1-measured transfer rate and hop)"
        del run
    rows, meta = bounds(workload(), coefs(), xfer, hop)
    md = table(rows, meta, title)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(md)
    Path(a.out).with_suffix(".json").write_text(json.dumps({"rows": rows, "meta": meta}, indent=1, default=str))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
