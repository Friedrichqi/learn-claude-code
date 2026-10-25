#!/usr/bin/env python3
"""Independent check of research/18_harness_wait's primary table: recompute, per cell, straight from the raw session
captures and traces (no mu_analyze, no wt_analyze code), and compare with wt_analyze's JSON output:

  work lead calls / h   lead calls with a reply received inside [window_start, window_end] whose reply is not only
                        a `wait` call, per hour of the window
  wait-only calls       lead calls in the window whose reply is only `wait`
  turns / h             trace turn_end events inside the window, per hour
  hit rate              cache_read_input_tokens / prompt tokens of every lead call received inside the window

Current attempts only (runs/<label>/<label>.*), as wt_analyze reads them; failed attempts are counted and reported.

    python research/18_harness_wait/wt_verify.py --json data/tables/main.json [--cells w16_none,...]
"""
from __future__ import annotations

import argparse
import json
import lzma
import sys
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent
CELLS = DIR / "data" / "cells"


def read_xz_jsonl(path: Path) -> list[dict]:
    out = []
    with lzma.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def recompute(cell: Path) -> dict:
    win = json.loads((cell / "window.json").read_text())
    ws, we = win["window_start"], win["window_end"]
    hours = (we - ws) / 3600
    work = wait_only = turns = 0
    cached = prompt = 0
    failed_attempts = 0
    for run in sorted((cell / "runs").iterdir()):
        if not run.is_dir():
            continue
        failed_attempts += sum(1 for p in run.iterdir() if p.is_dir() and p.name.startswith("failed_a"))
        label = run.name
        cap = run / f"{label}.requests.jsonl.xz"
        if cap.exists():
            for c in read_xz_jsonl(cap):
                resp = c.get("response")
                if c.get("purpose") != "lead" or not resp or c.get("t_recv") is None:
                    continue
                if not (ws <= c["t_recv"] <= we):
                    continue
                u = resp.get("usage") or {}
                hit = int(u.get("cache_read_input_tokens") or 0)
                prompt += int(u.get("input_tokens") or 0) + hit + int(u.get("cache_creation_input_tokens") or 0)
                cached += hit
                uses = [b for b in (resp.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
                if uses and all(b.get("name") == "wait" for b in uses):
                    wait_only += 1
                else:
                    work += 1
        for trace in run.glob("run_*.jsonl.xz"):
            if ".inputs." in trace.name or ".reads." in trace.name:
                continue
            for e in read_xz_jsonl(trace):
                if e.get("event") != "turn_end":
                    continue
                try:
                    t = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    continue
                turns += ws <= t <= we
            break                                   # one trace per session (wt_analyze reads the first)
    return {"work_calls_h": work / hours, "wait_only_calls": wait_only, "turns_h": turns / hours,
            "hit_rate": cached / prompt if prompt else None, "failed_attempts": failed_attempts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="wt_analyze --out ... .json")
    ap.add_argument("--cells", default="")
    ap.add_argument("--root", default=str(CELLS))
    args = ap.parse_args()
    h5 = json.loads(Path(args.json).read_text())["h5"]
    conds = args.cells.split(",") if args.cells else list(h5)
    bad = 0
    for c in conds:
        mine = recompute(Path(args.root) / c)
        theirs = h5.get(c)
        if theirs is None:
            print(f"{c}: missing from the analysis output")
            bad += 1
            continue
        diffs = []
        for key in ("work_calls_h", "wait_only_calls", "turns_h", "hit_rate"):
            a, b = mine[key], theirs.get(key)
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9 * max(1.0, abs(a))):
                diffs.append(f"{key}: verify {a} vs analyze {b}")
        bad += bool(diffs)
        status = "OK " if not diffs else "MISMATCH"
        print(f"{status} {c}: work {mine['work_calls_h']:.1f}/h, wait-only {mine['wait_only_calls']}, turns "
              f"{mine['turns_h']:.1f}/h, hit {mine['hit_rate']:.4f}, failed attempts {mine['failed_attempts']}"
              + ("" if not diffs else " | " + "; ".join(diffs)))
    print(f"{len(conds) - bad}/{len(conds)} cells agree")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
