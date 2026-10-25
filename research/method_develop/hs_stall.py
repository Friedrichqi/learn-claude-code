#!/usr/bin/env python3
"""E0d: what a co-scheduled prefill chunk adds to a decode step, at small chunk sizes (topic 11's logged steps).

Topic 11's linear step model (F14: 18.46 ms - 5.88 ms*[prefill] + 96.9 us/token + 3.46 ns*n*(a + n/2) + ...)
fits 2.03M steps with R^2 = 1.000, but its negative "mixed" term says it misfits small chunks, which are what a
stall budget would admit. This script refits steps that carry decodes with a hinge basis in the chunk size:

    t = c0 + c_seq*dec_seq + c_ctx*dec_ctx + c_mix*[prefill] + c_attn*sum n(a + n/2) + sum_j b_j*max(0, n - k_j)

(knots k_j in tokens; Huber IRLS as mu_analyze.fit_step_model), checks it against the binned residuals of
single-chunk steps over the decode-only prediction, and derives the stall budget: the largest chunk n that adds
at most eps of a decode-only step at context a (eps = 5 / 10 / 20 %). With --refresh-tokens-per-call (from E0e)
it reads D3 at N = 4-8: the share of refresh work the budget can host.

Inputs: research/11_multiuser_kv/data/multiuser_live/cells/<cell>/server/*/sched.jsonl.xz (Drive; HS_DATA_ROOT
may point at a copy). Output: data/tables/E0d_stall.md, data/e0d/fit.json.

    python research/17_harness_aware_serving/hs_stall.py [--cells n04,n08] [--refresh-tokens-per-call 900]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import numpy as np  # noqa: E402

import hs_common as hc  # noqa: E402

KNOTS = [0, 32, 64, 128, 256, 512, 1024, 2048, 4096]
N_GRID = [16, 32, 64, 128, 256, 512, 1024, 2048]
A_GRID = [0, 8000, 16000, 24000]
EPS = [0.05, 0.10, 0.20]
N_BINS = [(1, 32), (33, 64), (65, 128), (129, 256), (257, 512), (513, 1024), (1025, 2048), (2049, 8192)]
A_BINS = [(0, 4000), (4000, 12000), (12000, 20000), (20000, 64000)]


def cell_dir(cell: str) -> Path:
    return hc.topic_data("11_multiuser_kv") / "multiuser_live" / "cells" / cell


def load_steps(cell: str) -> tuple[list[dict], float, float]:
    """Steps with decodes (features attached) plus the cell's lead calls per second and its span."""
    import mu_analyze as ma                      # topic 11; imported late so the module docstring runs alone
    import mu_session as ms
    root = cell_dir(cell) / "server"
    servers = [ma.Server(d) for d in sorted(root.glob("*"))
               if d.is_dir() and ((d / "sched.jsonl.xz").exists() or (d / "sched.jsonl").exists())]
    if not servers:
        raise SystemExit(f"{cell}: no scheduler logs under {root} (pull topic 11's data first)")
    ma.step_features(servers)
    steps, lead_arr, t_lo, t_hi = [], 0, float("inf"), float("-inf")
    for srv in servers:
        for rec in srv.req.values():
            info = ms.parse_request_id(rec["arr"].get("id", "")) or {}
            if info.get("purpose") in ("ld", "lead"):          # request ids carry the purpose code
                lead_arr += 1
        for s in srv.steps:
            if s.get("gpu") is None or not (0 < s["gpu"] < 30) or s.get("tok", 0) <= 0:
                continue
            t_lo, t_hi = min(t_lo, s["g0"]), max(t_hi, s["g1"])
            if not s["dcr"]:
                continue                                          # E0d is about steps that carry decodes
            chunks = [(c[2], c[1]) for c in s["pfl"]]                # (n new tokens, a cached before them)
            steps.append({"gpu": s["gpu"], "dec_seq": len(s["dcr"]), "dec_ctx": s["x"][5] * 1e6, "chunks": chunks})
    span = max(t_hi - t_lo, 1e-9)
    return steps, lead_arr / span, span


def design(steps: list[dict]) -> np.ndarray:
    rows = []
    for s in steps:
        n = sum(c[0] for c in s["chunks"])
        attn = sum(c[0] * (c[1] + c[0] / 2) for c in s["chunks"]) / 1e6
        rows.append([1.0, s["dec_seq"], s["dec_ctx"] / 1e6, 1.0 if n else 0.0, attn]
                    + [max(0.0, n - k) / 1e3 for k in KNOTS])
    return np.array(rows)


def huber_fit(X: np.ndarray, y: np.ndarray, iters: int = 30) -> np.ndarray:
    w = np.ones(len(y))
    coef = np.zeros(X.shape[1])
    for _ in range(iters):
        W = np.sqrt(w)[:, None]
        coef, *_ = np.linalg.lstsq(X * W, y * W[:, 0], rcond=None)
        resid = y - X @ coef
        scale = 1.4826 * np.median(np.abs(resid - np.median(resid))) + 1e-9
        k = 1.345 * scale
        w = np.where(np.abs(resid) <= k, 1.0, k / np.maximum(np.abs(resid), 1e-12))
    return coef


def added(coef: np.ndarray, n: float, a: float) -> float:
    """Seconds one chunk of n tokens after a cached tokens adds to a step that already carries decodes."""
    if n <= 0:
        return 0.0
    return float(coef[3] + coef[4] * n * (a + n / 2) / 1e6 + sum(b * max(0.0, n - k) / 1e3
                                                                for b, k in zip(coef[5:], KNOTS)))


def decode_only(coef: np.ndarray, dec_seq: float, dec_ctx: float) -> float:
    return float(coef[0] + coef[1] * dec_seq + coef[2] * dec_ctx / 1e6)


def budget(coef: np.ndarray, a: float, base: float, eps: float, n_max: int = 8192) -> int:
    """Largest n (tokens) whose chunk adds at most eps x base seconds to the step; 0 if even one token does."""
    best = 0
    for n in range(1, n_max + 1):
        if added(coef, n, a) <= eps * base:
            best = n
        elif n > 64 and added(coef, n, a) > 2 * eps * base:
            break
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", default="n04,n08")
    ap.add_argument("--refresh-tokens-per-call", type=float, default=None,
                    help="E0e's refresh work per lead call (tokens) under the D2 policy")
    args = ap.parse_args()
    out_dir = hc.DATA / "e0d"
    out_dir.mkdir(parents=True, exist_ok=True)
    fits, lines = {}, []
    for cell in args.cells.split(","):
        steps, lead_rate, span = load_steps(cell)
        mixed = sum(1 for s in steps if s["chunks"])
        if mixed < 200:                                   # one session never prefills while it decodes
            lines += [f"## {cell}: {len(steps):,} steps with decodes, {mixed} of them with a prefill chunk: "
                      "too few co-scheduled steps to fit (a single session never prefills while it decodes)", ""]
            fits[cell] = {"steps": len(steps), "mixed_steps": mixed, "skipped": True}
            continue
        X, y = design(steps), np.array([s["gpu"] for s in steps])
        coef = huber_fit(X, y)
        pred = X @ coef
        mdape = float(np.median(np.abs(y - pred) / np.maximum(y, 1e-4)))
        seq = float(np.median([s["dec_seq"] for s in steps]))
        ctx = float(np.median([s["dec_ctx"] for s in steps]))
        base = decode_only(coef, seq, ctx)
        # binned residuals of single-chunk steps over the decode-only prediction (model-free check)
        emp = {}
        for s in steps:
            if len(s["chunks"]) != 1:
                continue
            n, a = s["chunks"][0]
            r = s["gpu"] - decode_only(coef, s["dec_seq"], s["dec_ctx"])
            for i, (lo, hi) in enumerate(N_BINS):
                if lo <= n <= hi:
                    for j, (alo, ahi) in enumerate(A_BINS):
                        if alo <= a < ahi:
                            emp.setdefault((i, j), []).append(r)
        fits[cell] = {"coef": coef.tolist(), "knots": KNOTS, "steps": len(steps), "mixed_steps": mixed, "mdape": mdape,
                      "median_dec_seq": seq, "median_dec_ctx": ctx, "decode_only_s": base,
                      "lead_calls_per_s": lead_rate, "span_s": span}
        lines += [f"## {cell}: {len(steps):,} steps with decodes; median {seq:.0f} decoding sequences, {ctx / 1e3:.0f}k "
                  f"context tokens; decode-only step {base * 1e3:.1f} ms (model); hinge fit MdAPE {mdape:.1%}", "",
                  "Model: what one chunk of n tokens after a cached tokens adds to such a step (ms)", "",
                  "| n \\ a | " + " | ".join(f"{a // 1000}k" for a in A_GRID) + " |",
                  "|---|" + "---|" * len(A_GRID)]
        for n in N_GRID:
            lines.append(f"| {n} | " + " | ".join(f"{added(coef, n, a) * 1e3:.1f}" for a in A_GRID) + " |")
        lines += ["", "Measured: median excess of single-chunk steps over the decode-only prediction (ms; steps)", "",
                  "| n | " + " | ".join(f"a {lo // 1000}-{hi // 1000}k" for lo, hi in A_BINS) + " |",
                  "|---|" + "---|" * len(A_BINS)]
        for i, (lo, hi) in enumerate(N_BINS):
            cells = []
            for j in range(len(A_BINS)):
                v = emp.get((i, j), [])
                cells.append(f"{np.median(v) * 1e3:.1f} ({len(v):,})" if len(v) >= 20 else "—")
            lines.append(f"| {lo}-{hi} | " + " | ".join(cells) + " |")
        lines += ["", "Stall budget: largest chunk (tokens) that adds at most eps of the decode-only step", "",
                  "| eps | " + " | ".join(f"a = {a // 1000}k" for a in A_GRID) + " | spare tokens/s at a = 16k |",
                  "|---|" + "---|" * (len(A_GRID) + 1)]
        for eps in EPS:
            b = [budget(coef, a, base, eps) for a in A_GRID]
            per_s = b[A_GRID.index(16000)] / (base * (1 + eps))
            lines.append(f"| {eps:.0%} | " + " | ".join(f"{v:,}" for v in b) + f" | {per_s:,.0f} |")
            fits[cell][f"budget_{int(eps * 100)}"] = dict(zip([str(a) for a in A_GRID], b))
        if args.refresh_tokens_per_call:
            b10 = budget(coef, 16000, base, 0.10)
            supply = b10 / (base * 1.10)                         # refresh tokens/s the 10 % budget can host
            demand = args.refresh_tokens_per_call * lead_rate     # refresh tokens/s the D2 policy needs
            share = min(1.0, supply / demand) if demand else float("nan")
            fits[cell]["d3"] = {"supply_tok_s": supply, "demand_tok_s": demand, "fit_share": share}
            lines += ["", f"D3 at {cell} (illustrative: the refresh work per call comes from topic 16's R1 runs, the "
                      f"lead-call rate from this snip-off cell): the 10 % budget hosts {supply:,.0f} refresh tokens/s "
                      f"against a demand of {demand:,.0f} ({args.refresh_tokens_per_call:,.0f} tokens per lead call x "
                      f"{lead_rate:.2f} lead calls/s): **{share:.0%} fits** (rule: >= 50 %)."]
        lines.append("")
    (out_dir / "fit.json").write_text(json.dumps(fits, indent=1), encoding="utf-8")
    note = ("Hinge-basis refit of topic 11's step model on steps that carry decodes (H200, Qwen3-32B, vLLM 0.29, "
            "8,192-token chunk limit). 'a' is the cached context before the chunk; the attention term grows with it.")
    print(hc.write_table("E0d_stall", "E0d · the prefill a decode step can absorb", lines, note))


if __name__ == "__main__":
    main()
