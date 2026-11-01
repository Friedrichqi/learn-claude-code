#!/usr/bin/env python3
"""Independent check of research/19_efficiency_signals' primary table. Per cell, it recomputes these straight from
the raw session captures and traces, with no mu_analyze, wt_analyze or ef_analyze code, and compares them with
ef_analyze's JSON output:

  turns / h             trace turn_end events inside [window_start, window_end], per hour (the primary metric)
  work lead calls / h   lead calls with a reply received inside the window whose tool calls are not all pacing tools
                        (wait, wait_for_capacity, server_status), per hour
  overhead-only calls   lead calls in the window whose tool calls are all pacing tools
  hit rate              cache_read_input_tokens / prompt tokens of every lead call received inside the window

Current attempts only (runs/<label>/<label>.*), as the analysis reads them; failed attempts are counted.

    python research/19_efficiency_signals/ef_verify.py --json research/19_efficiency_signals/data/tables/main.json
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
PACING = {"wait", "wait_for_capacity", "server_status"}


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
    work = overhead = turns = 0
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
                if uses and all(b.get("name") in PACING for b in uses):
                    overhead += 1
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
            break                                   # one trace per session (the analysis reads the first)
    return {"turns_h": turns / hours, "work_calls_h": work / hours, "overhead_calls": overhead,
            "hit_rate": cached / prompt if prompt else None, "failed_attempts": failed_attempts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="ef_analyze --out ... .json")
    ap.add_argument("--cells", default="")
    ap.add_argument("--root", default=str(CELLS))
    args = ap.parse_args()
    t = json.loads(Path(args.json).read_text())["t"]
    conds = args.cells.split(",") if args.cells else list(t)
    bad = 0
    for c in conds:
        mine = recompute(Path(args.root) / c)
        theirs = t.get(c)
        if theirs is None:
            print(f"{c}: missing from the analysis output")
            bad += 1
            continue
        diffs = []
        for key in ("turns_h", "work_calls_h", "overhead_calls", "hit_rate"):
            a, b = mine[key], theirs.get(key)
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9 * max(1.0, abs(a))):
                diffs.append(f"{key}: verify {a} vs analyze {b}")
        bad += bool(diffs)
        status = "OK " if not diffs else "MISMATCH"
        print(f"{status} {c}: turns {mine['turns_h']:.1f}/h, work {mine['work_calls_h']:.1f}/h, overhead-only "
              f"{mine['overhead_calls']}, hit {mine['hit_rate']:.4f}, failed attempts {mine['failed_attempts']}"
              + ("" if not diffs else " | " + "; ".join(diffs)))
    print(f"{len(conds) - bad}/{len(conds)} cells agree")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
