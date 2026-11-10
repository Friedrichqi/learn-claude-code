#!/usr/bin/env python3
"""Topic 22, post hoc (not in prereg.md): is `wait`'s lower request latency the working calls, or the call mix?

Under `wait` some model calls only call `wait` (wait-only calls). They are short (about 60 generated tokens at the
median, against about 150), so adding them lowers the request statistics even if no other call got faster. Per N, this
repeats wb_analyze.py's request contrast (`wait` - `none`, the full-N window, tasks paired, 2,000 draws) on:
  - every call (the pre-registered measure; the same numbers as data/tables/wb.md);
  - every call but the wait-only ones;
  - only the calls without `wait` (dropping calls that did work and also waited).

    python research/22_wait_bench/wb_mix.py      # -> data/tables/wb_mix.{md,json}
"""
from __future__ import annotations

import argparse
import json

import wb_analyze as A

SUBSETS = {"all calls": lambda c: True,
           "no wait-only calls": lambda c: not c["wait_only"],
           "no calls with wait": lambda c: not c["wait"]}


def groups(name: str, bench_of: dict, keep) -> tuple[dict, dict]:
    """{task: [seconds of its calls in the run's full-N window that `keep` takes]}, and the run's counts."""
    n = int((A.read_jsonl(A.RUNS / name / "cell.jsonl") or [{}])[-1].get("n") or 0)
    trials = A.load_trials(A.JOBS / name, bench_of)
    win = A.window(trials, n)
    in_win = lambda c: win["w0"] is not None and win["w0"] <= c["t"] <= win["w1"]
    g = {t["id"]: [c["ms"] / 1000.0 for c in t["calls"] if in_win(c) and keep(c)] for t in trials}
    return {k: v for k, v in g.items() if v}, {"n": n, "calls": sum(len(v) for v in g.values())}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ns", nargs="*", type=int, default=[32, 16, 8, 4])
    ap.add_argument("--out", default="wb_mix")
    a = ap.parse_args()
    bench_of = A.bench_map()
    rows = {}
    for n in a.ns:
        if not all((A.JOBS / f"n{n}_{c}").is_dir() for c in ("none", "wait")):
            continue
        for label, keep in SUBSETS.items():
            gn, cn = groups(f"n{n}_none", bench_of, keep)
            gw, cw = groups(f"n{n}_wait", bench_of, keep)
            rows[f"{n}|{label}"] = {"n": n, "subset": label, "calls_none": cn["calls"], "calls_wait": cw["calls"],
                                    "none": A.summary([v for g in gn.values() for v in g]),
                                    "wait": A.summary([v for g in gw.values() for v in g]),
                                    "diff": A.boot_paired(gn, gw, f"c|req|{n}")}
    A.TABLES.mkdir(parents=True, exist_ok=True)
    (A.TABLES / f"{a.out}.json").write_text(json.dumps(rows, indent=1) + "\n")
    out = ["# Topic 22: request latency with and without wait-only calls (wb_mix.py, post hoc)", "",
           "Seconds; the full-N window. `wait` − `none`, tasks paired, 95 % bootstrap intervals (2,000 draws). "
           "`all calls` repeats data/tables/wb.md's contrast; the seed is the same, so the numbers match.", "",
           "| N | subset | `none` calls | `wait` calls | `none` p50 | `wait` p50 | avg | p50 | p95 | p99 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows.values():
        d = r["diff"]
        cell = lambda s: (f"{d[s]['diff']:+.1f} [{d[s]['ci'][0]:.1f}, {d[s]['ci'][1]:.1f}]"
                          if d.get(s) and d[s]["diff"] is not None and d[s]["ci"][0] is not None else "-")
        out.append(f"| {r['n']} | {r['subset']} | {r['calls_none']} | {r['calls_wait']} | {r['none']['p50']:.1f} | "
                   f"{r['wait']['p50']:.1f} | {cell('avg')} | {cell('p50')} | {cell('p95')} | {cell('p99')} |")
    (A.TABLES / f"{a.out}.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
