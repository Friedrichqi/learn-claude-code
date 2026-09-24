#!/usr/bin/env python3
"""Term-attribution scoreboard: Wall = R x [F + P + D] + H per run and per group.

Step 0 of the efficiency methodology (research/09_efficiency/README.md Part A): every
intervention must name the cost term it attacks and be judged on the measured per-term
delta, not on end-to-end wall alone.

Terms, from the validated regression  duration = F + p*uncached_tokens + d*output_tokens:
  R  rounds whose model call is a lead/teammate/one-shot round (the multiplier)
  F  fixed per-call component (provider queue/delivery + SDK)      [ms, from the fit]
  P  prefill of uncached prompt tokens                             [ms/token, from the fit]
  D  decode                                                        [ms/token, from the fit]
  H  harness-initiated synchronous work: memory recall/extract/consolidate, compaction
     [ms per run; overlap = share of that time concurrent with some other model call,
     so async extraction shows up as high overlap instead of a big tail]

Usage:
  term_scoreboard.py TRACE_DIR_OR_TRACE...                 # per-run + group tables
  term_scoreboard.py DIR_A --json out.json                 # machine-readable
  term_scoreboard.py DIR_BASELINE --compare DIR_OPT        # term deltas baseline vs optimized

Quality gates (score json) are reported alongside so a term win never hides a quality loss.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); import _paths  # noqa: E401,E402,F401 -- research/_paths.py

import latency_breakdown as lb  # noqa: E402  (reuses the validated trace parser)

MAIN_PURPOSES = {"lead", "teammate", "one_shot"}
H_PURPOSES = lb.MEMORY_PURPOSES | lb.COMPACTION_PURPOSES


def trace_paths(targets: list[str]) -> list[Path]:
    out: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            out.extend(sorted(p for p in path.glob("*.jsonl")
                              if not p.name.endswith((".inputs.jsonl", ".reads.jsonl"))))
        else:
            out.append(path)
    return out


def overlap_with_others(call, all_calls) -> float:
    """Fraction of [t_req, t_resp] covered by intervals of calls from other spans."""
    total = call.duration
    if total <= 0:
        return 0.0
    covered = 0.0
    for other in all_calls:
        if other is call or other.span_id == call.span_id:
            continue
        lo = max(call.t_req, other.t_req)
        hi = min(call.t_resp, other.t_resp)
        if hi > lo:
            covered += hi - lo
    return min(covered / total, 1.0)


def solve3(xs, y):
    """Least squares y = a + b*x1 + c*x2 via normal equations. Returns (a, b, c, r2, n)."""
    rows = [(1.0, u, o, t) for u, o, t in xs if u is not None and o is not None and t is not None]
    if len(rows) < 10:
        return None
    s = [0.0] * 6
    m = [[0.0] * 3 for _ in range(3)]
    v = [0.0] * 3
    for r1, u, o, t in rows:
        feats = (r1, u, o)
        for i in range(3):
            v[i] += feats[i] * t
            for j in range(3):
                m[i][j] += feats[i] * feats[j]
    # gaussian elimination
    for i in range(3):
        pivot = max(range(i, 3), key=lambda r: abs(m[r][i]))
        if abs(m[pivot][i]) < 1e-9:
            return None
        m[i], m[pivot] = m[pivot], m[i]
        v[i], v[pivot] = v[pivot], v[i]
        for r in range(i + 1, 3):
            factor = m[r][i] / m[i][i]
            for c in range(i, 3):
                m[r][c] -= factor * m[i][c]
            v[r] -= factor * v[i]
    coef = [0.0] * 3
    for i in (2, 1, 0):
        coef[i] = (v[i] - sum(m[i][j] * coef[j] for j in range(i + 1, 3))) / m[i][i]
    a, b, c = coef
    ss_res = ss_tot = 0.0
    mean_y = statistics.fmean([t for *_, t in rows])
    for _, u, o, t in rows:
        pred = a + b * u + c * o
        ss_res += (t - pred) ** 2
        ss_tot += (t - mean_y) ** 2
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return {"F_ms": a, "p_ms_per_tok": b, "d_ms_per_tok": c, "r2": r2, "n": len(rows)}


def sidecar_tokens(call):
    usage = call.usage or {}
    inp = usage.get("input_tokens")
    cached = usage.get("cache_read_input_tokens") or 0
    uncached = inp - cached if inp is not None else None
    return inp, cached, uncached, usage.get("output_tokens")


def run_record(run) -> dict:
    calls = run.calls
    main = [c for c in calls if c.purpose in MAIN_PURPOSES]
    h_calls = [c for c in calls if c.purpose in H_PURPOSES]
    h_ms = sum(c.duration for c in h_calls)
    h_overlap = (statistics.fmean([overlap_with_others(c, calls) for c in h_calls])
                 if h_calls else None)
    last_resp = max((c.t_resp for c in calls), default=0.0)
    tail_ms = max(run.wall_ms - last_resp, 0.0)
    out_toks = [sidecar_tokens(c)[3] for c in main]
    uncache_toks, cache_read, inp_toks = [], 0.0, 0.0
    for c in main:
        _, cached, uncached, _ = sidecar_tokens(c)
        if uncached is not None:
            uncache_toks.append(uncached)
        if cached is not None:
            cache_read += cached
        i, *_ = sidecar_tokens(c)
        if i is not None:
            inp_toks += i
    think_chars = text_chars = 0
    for c in main:
        side = c.sidecar or {}
        think_chars += side.get("output_thinking_chars") or 0
        text_chars += (side.get("output_text_chars") or 0) + (side.get("output_tool_input_chars") or 0)
    tool_rounds = [r for r in run.rounds if r.tool_calls]
    multi = [r for r in tool_rounds if len(r.tool_calls) > 1]
    reacquire = sum(1 for r in run.records
                    if r.get("event") == "reacquire_warn")
    score = run.score or {}
    quality = (score.get("coverage") if "coverage" in score else
               score.get("correct") if "correct" in score else None)
    return {
        "label": run.label, "category": run.group[0], "mode": run.group[1],
        "status": run.status, "wall_s": round(run.wall_ms / 1000, 1),
        "R": len(main),
        "model_ms": round(sum(c.duration for c in main)),
        "out_tokens_run": round(sum(t for t in out_toks if t)),
        "uncached_tokens_run": round(sum(t for t in uncache_toks if t is not None)),
        "cache_hit": round(cache_read / inp_toks, 3) if inp_toks else None,
        "thinking_share": round(think_chars / (think_chars + text_chars), 3)
                          if (think_chars + text_chars) else None,
        "H_ms": round(h_ms), "H_overlap": round(h_overlap, 2) if h_overlap is not None else None,
        "H_calls": len(h_calls), "tail_s": round(tail_ms / 1000, 1),
        "tools_ms": round(sum(r.tools for r in run.rounds)),
        "tool_rounds": len(tool_rounds),
        "multi_tool_rounds": len(multi),
        "reacquire_warns": reacquire,
        "quality": quality,
    }


def fit_constants(runs) -> dict | None:
    xs, ys = [], []
    for run in runs:
        for c in run.calls:
            if c.purpose not in MAIN_PURPOSES or c.sidecar is None:
                continue
            *_, uncached, out = sidecar_tokens(c)
            if uncached is None or out is None:
                continue
            xs.append((float(uncached), float(out), float(c.duration)))
            ys.append(c.duration)
    return solve3(xs, ys)


def group_table(records: list[dict]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for rec in records:
        groups[(rec["category"], rec["mode"])].append(rec)
    header = (f"| {'group':<14} | {'n':>2} | {'wall_s':>7} | {'R':>5} | {'out_tok':>8} | "
              f"{'think':>5} | {'H_s':>6} | {'H_ovl':>5} | {'tail_s':>6} | {'cache':>5} | "
              f"{'multi%':>6} | {'qual':>5} |")
    lines = [header, "|" + "---|" * 12]
    for key in sorted(groups):
        rs = groups[key]
        def m(field, digits=1):
            vals = [r[field] for r in rs if r.get(field) is not None]
            return f"{statistics.fmean(vals):.{digits}f}" if vals else "-"
        qual = [r["quality"] for r in rs if r.get("quality") is not None]
        multi_share = [r["multi_tool_rounds"] / max(r["tool_rounds"], 1) for r in rs
                       if r.get("tool_rounds")]
        multi_pct = (f"{100 * statistics.fmean(multi_share):.0f}" if multi_share else "-")
        lines.append(
            f"| {key[0]}-{key[1]:<8} | {len(rs):>2} | {m('wall_s', 0):>7} | {m('R'):>5} | "
            f"{m('out_tokens_run', 0):>8} | {m('thinking_share', 2):>5} | {m('H_ms', 0):>6} | "
            f"{m('H_overlap', 2):>5} | {m('tail_s', 0):>6} | {m('cache_hit', 2):>5} | "
            f"{multi_pct:>6} | "
            f"{(f'{statistics.fmean(qual):.2f}' if qual else '-'):>5} |")
    return "\n".join(lines)


def constants_table(name: str, fit: dict | None) -> str:
    if not fit:
        return f"{name}: insufficient data for the F/P/D fit"
    return (f"{name}: F = {fit['F_ms']:.0f} ms/call, P = {fit['p_ms_per_tok']:.3f} ms/uncached tok, "
            f"D = {fit['d_ms_per_tok']:.1f} ms/out tok  (r2={fit['r2']:.3f}, n={fit['n']})")


def summarize(path_name: str, runs, records) -> dict:
    fit = fit_constants(runs)
    return {"name": path_name, "fit": fit, "runs": records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", help="trace dirs or trace files")
    parser.add_argument("--compare", default=None,
                        help="second trace dir; prints term deltas (first = baseline)")
    parser.add_argument("--json", default=None, help="write the full summary as JSON")
    args = parser.parse_args()

    def build(targets):
        runs = []
        for path in trace_paths(targets):
            try:
                runs.append(lb.Run(path, HERE.parents[1], redundancy=False))
            except Exception as exc:                                   # noqa: BLE001
                print(f"skip {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        records = [run_record(run) for run in runs if run.status not in {"error"}]
        return runs, records

    baseline_runs, baseline_records = build(args.targets)
    print(f"# Term scoreboard — {args.targets[0]}  ({len(baseline_records)} runs)\n")
    print(group_table(baseline_records))
    print()
    print(constants_table("fit(all main calls)", fit_constants(baseline_runs)))
    summary = {"baseline": summarize(args.targets[0], baseline_runs, baseline_records)}

    if args.compare:
        opt_runs, opt_records = build([args.compare])
        print(f"\n# Compare: {args.targets[0]} (baseline) -> {args.compare} (optimized)\n")
        print(group_table(opt_records))
        print()
        base_fit, opt_fit = fit_constants(baseline_runs), fit_constants(opt_runs)
        print(constants_table("fit baseline  ", base_fit))
        print(constants_table("fit optimized ", opt_fit))
        summary["optimized"] = summarize(args.compare, opt_runs, opt_records)

        base_groups: dict[tuple, list] = defaultdict(list)
        opt_groups: dict[tuple, list] = defaultdict(list)
        for rec in baseline_records:
            base_groups[(rec["category"], rec["mode"])].append(rec)
        for rec in opt_records:
            opt_groups[(rec["category"], rec["mode"])].append(rec)

        def field(recs, key):
            vals = [r[key] for r in recs if r.get(key) is not None]
            return statistics.fmean(vals) if vals else None

        print("\n| group | wall_s base->opt | R | out_tok | H_ms | H_overlap | think | cache | quality |")
        print("|---|---|---|---|---|---|---|---|---|")
        for key in sorted(set(base_groups) | set(opt_groups)):
            b, o = base_groups.get(key, []), opt_groups.get(key, [])
            if not b or not o:
                continue
            def pair(keyname, digits=0):
                bv, ov = field(b, keyname), field(o, keyname)
                if bv is None or ov is None:
                    return "-"
                if bv == 0:
                    return f"0->{ov:.{digits}f}"
                return f"{bv:.{digits}f}->{ov:.{digits}f} ({100 * (ov - bv) / bv:+.0f}%)"
            print(f"| {key[0]}-{key[1]} | {pair('wall_s')} | {pair('R')} | "
                  f"{pair('out_tokens_run')} | {pair('H_ms')} | {pair('H_overlap', 2)} | "
                  f"{pair('thinking_share', 2)} | {pair('cache_hit', 2)} | "
                  f"{field(b, 'quality') if field(b, 'quality') is not None else '-'}"
                  f"->{field(o, 'quality') if field(o, 'quality') is not None else '-'} |")

    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1), encoding="utf-8")
        print(f"\njson -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
