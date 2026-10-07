#!/usr/bin/env python3
"""Tool-duration predictor for B1's hints (research/13_tiered_memory, stage S0). Offline, no GPU.

    python research/13_tiered_memory/tm_predict.py          # after tm_ceilings.py (reads data/s0/pauses.jsonl.xz)
    sbatch --export=NONE research/13_tiered_memory/tm_cpu.sbatch tm_predict.py

When a reply ends in a tool call, B1's server extension looks the call up here to learn how long the
session will probably be away (p50 / p90). Keys come from `tm_ceilings.tool_key`: the tool name, with bash
split by 06's command class and a scope (`bash:pytest/suite` = a whole tests/ directory; `.../wide` = find,
a recursive grep or another walk of a huge tree, such as the 7.6 GB site-packages).

Two sources, in lookup order:
  windows    what the server will actually see: the client-side window between a reply and the session's
             next call (its t_send minus the reply's t_recv: harness + tool + think time), from 11's and
             12's live cells (tm_ceilings.py). A reply that called several tools counts for its dominant
             one: a `/suite` or `/wide` call first, else the one with the longest median tool time.
  tool_time  the tool's own time (06's `net_ms`), for keys without enough windows: 06's census on this
             cluster's hosts plus 12's traces parsed with 06's `tool_census._parse` (failing commands
             count; denied calls are dropped). These keys have no scope. Add `overhead_s` (the median
             window minus tool time over keys present in both) to compare them with windows.
A key needs >= 20 samples. Lookup: windows[key] → windows[key without scope] → tool_time[...] +
overhead_s → global.

Writes research/13_tiered_memory/tool_durations.json (a frozen input of B1, tracked in git) and
data/tables/S0_tool_durations.md.
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import statistics
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: E402,F401

import tool_census  # noqa: E402  (research/06_tool_cost)

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
PAUSES = HERE / "data" / "s0" / "pauses.jsonl.xz"
CENSUS = RESEARCH / "06_tool_cost" / "data" / "tool_census" / "calls.jsonl.xz"
C12 = RESEARCH / "12_hw_utilization" / "data" / "cells"
OUT_JSON = HERE / "tool_durations.json"
OUT_MD = HERE / "data" / "tables" / "S0_tool_durations.md"
HOSTS = {"cluster-local", "cluster-nfs"}
MIN_N = 20


def census_key(call: dict) -> str:
    return f"bash:{call['bash_class']}" if call["tool"] == "bash" and call.get("bash_class") else call["tool"]


def q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs: list[float], src: str) -> dict:
    return {"n": len(xs), "p50_s": round(q(xs, 0.5), 4), "p90_s": round(q(xs, 0.9), 4),
            "p99_s": round(q(xs, 0.99), 4), "mean_s": round(statistics.fmean(xs), 4), "from": src}


def parse12(path: str) -> list[dict]:
    res = tool_census.parse(("12_hw_utilization/cells", Path(path)))
    return [] if res.get("error") or res.get("probe") else res["calls"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    tool_time: dict[str, list[float]] = defaultdict(list)
    with lzma.open(CENSUS, "rt", encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            if c["host"] in HOSTS and c["status"] != "denied" and c.get("net_ms") is not None:
                tool_time[census_key(c)].append(c["net_ms"] / 1000)
    traces = sorted(str(p) for p in C12.glob("*/levels/*/runs/*/run_*.jsonl.xz") if ".inputs." not in p.name)
    with Pool(args.workers) as pool:
        for calls in pool.imap_unordered(parse12, traces, chunksize=8):
            for c in calls:
                if c["status"] != "denied" and c.get("net_ms") is not None:
                    tool_time[census_key(c)].append(c["net_ms"] / 1000)
    base_p50 = {k: q(v, 0.5) for k, v in tool_time.items()}

    def dominant(keys: list[str]) -> str:
        return max(keys, key=lambda k: (("/suite" in k) or ("/wide" in k), base_p50.get(k.split("/", 1)[0], 0.0)))

    windows: dict[str, list[float]] = defaultdict(list)
    window_cells: dict[str, set] = defaultdict(set)
    with lzma.open(PAUSES, "rt", encoding="utf-8") as fh:
        for line in fh:
            p = json.loads(line)
            if p["window_s"] >= 0:
                key = dominant(p["key"].split("+"))
                windows[key].append(p["window_s"])
                window_cells[key].add(p["cell"])
    print(f"[predict] window keys {len(windows)}, tool-time keys {len(tool_time)}, 12 traces {len(traces)}", flush=True)

    win = {k: {**stats(v, "windows"), "cells": sorted(window_cells[k])} for k, v in sorted(windows.items())
           if len(v) >= MIN_N}
    tt = {k: stats(v, "tool_time") for k, v in sorted(tool_time.items()) if len(v) >= MIN_N}
    both = [win[k]["p50_s"] - tt[k]["p50_s"] for k in win if k in tt]
    overhead = round(statistics.median(both), 4) if both else 0.1
    every = [x for v in windows.values() for x in v]
    out = {"_doc": "research/13_tiered_memory/tm_predict.py: seconds a session is away after a reply, by "
                   "tm_ceilings.tool_key. Lookup: windows[key] -> windows[key without '/scope'] -> "
                   "tool_time[...] + overhead_s -> global",
           "_sources": {"window_samples": len(every), "tool_time_samples": sum(len(v) for v in tool_time.values())},
           "windows": win, "tool_time": tt, "overhead_s": overhead, "global": stats(every, "windows")}
    OUT_JSON.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")

    md = ["# S0 · Tool-duration predictor for B1 (seconds a session is away after a reply)", "",
          f"Written by `tm_predict.py` to `tool_durations.json`. Keys with ≥ {MIN_N} samples.", "",
          f"## What the server sees: window after a reply, by its dominant tool ({len(every):,} windows, 11/12 live cells)", "",
          "| what the reply called | windows | p50 | p90 | p99 | mean | cells |", "|---|---|---|---|---|---|---|"]
    for k, r in sorted(win.items(), key=lambda kv: -kv[1]["n"] * kv[1]["mean_s"]):
        md.append(f"| {k} | {r['n']:,} | {r['p50_s']:.2f} | {r['p90_s']:.2f} | {r['p99_s']:.1f} | {r['mean_s']:.2f} | "
                  f"{', '.join(r['cells'])} |")
    md += ["", f"## Fallback: the tool's own time ({out['_sources']['tool_time_samples']:,} calls: 06 census on this "
           "cluster + 12's traces)", "", f"Median window minus tool time over shared keys: **{overhead:.2f} s** "
           "(harness and turn handling around the tool).", "",
           "| tool (bash by command class) | calls | p50 | p90 | p99 | mean |", "|---|---|---|---|---|---|"]
    for k, r in sorted(tt.items(), key=lambda kv: -kv[1]["n"] * kv[1]["mean_s"])[:20]:
        md.append(f"| {k} | {r['n']:,} | {r['p50_s']:.3f} | {r['p90_s']:.3f} | {r['p99_s']:.2f} | {r['mean_s']:.3f} |")
    md += ["", "*Key.* **window**: the client-side time between a reply's arrival and the session's next call "
           "(harness + tool + think time); 11's sessions draw a think time (lognormal, median 30 s) before each "
           "follow-up turn, 12's run closed loop. `/suite`: pytest over a whole tests/ directory; `/wide`: find, a "
           "recursive grep or another walk of a huge tree (site-packages, or a path at most two levels below /). "
           "`bash:grep/wide` is bimodal (p50 0.01 s, p99 ~90 s): whether the tree is in the page cache decides, "
           "which no static feature predicts. Rows sorted by total time."]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:45]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
