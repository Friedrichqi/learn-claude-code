#!/usr/bin/env python3
"""Run research/19_efficiency_signals cells with research/18_harness_wait/wt_pipeline.py's lifecycle on GPU group Q.

Group Q is GPUs 0-3 (MiniMax-M2.7 at TP=4), so cells run one at a time. Each cell uses this topic's conditions
(ef_workloads), cells and GPU-hour tally (data/), its lanes (/data/fritz/ef_lanes) and its cap of 120 GPU-h.
A cell, as in wt_pipeline:
  1. refuses to start on busy GPUs, an existing cell folder, or a cap the estimate would pass;
  2. serves, checks the scheduler header and /wt/status, scrapes and sweeps;
  3. stops the server and waits until GPUs 0-3 are empty.

    python research/19_efficiency_signals/ef_pipeline.py cell --cond s_none
    python research/19_efficiency_signals/ef_pipeline.py chain s_none s_m_wait s_m_tools s_gate2
    python research/19_efficiency_signals/ef_pipeline.py hours
    python research/19_efficiency_signals/ef_pipeline.py gpus

`chain` runs its cells one after another and stops at the first that fails. A gate cell whose K is still
pending (before pilot P1 sets ef_workloads.GATE_K) is refused, except in the smoke.

Before every cell the lab GPU sheet is read (its CSV export; no credentials). Every 30-min slot from now to the
cell's expected end (warm-up + window + the startup allowance) must carry the member name on GPUs 0-3, or the cell is
refused and a chain stops. The claims are entered by the user. `--no-claim-check` skips this (tests only).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_workloads as ew  # noqa: E402

os.environ.setdefault("WT_DATA_ROOT", str(ew.DATA))
os.environ.setdefault("WT_CAP_GPU_H", "120")
os.environ.setdefault("WT_LANES", "/data/fritz/ef_lanes")

import mu_workloads as mw  # noqa: E402
import wt_pipeline as wp  # noqa: E402  (registers topic 18's conditions; ours go on top)

mw.CONDITIONS.update(ew.conditions())
GROUP = "Q"


def register() -> None:
    """Write data/conditions.json for the sweep and session subprocesses (MU_EXTRA_CONDITIONS). Only the commands
    that run cells call this: a running chain's later cells read the file when their sweep starts."""
    os.environ["MU_EXTRA_CONDITIONS"] = str(ew.write_conditions())

SHEET = "https://docs.google.com/spreadsheets/d/1ZUfNV5mGOmWK_-Tzc6zMmV6LWqNUFn41sG-XcCtyIog"
MEMBER = "Yijiahao"


def _get(url: str) -> str:
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def sheet_claims() -> dict:
    """(date, slot start 'H:MM AM/PM') -> [the names on GPUs 0..7], from every week tab of the lab GPU sheet."""
    import csv
    import io
    import re
    tabs = re.findall(r'items\.push\(\{name: "([^"]*)", pageUrl: "[^"]*", gid: "(\d+)"', _get(f"{SHEET}/htmlview"))
    out = {}
    for name, gid in tabs:
        if "TEMPLATE" in name.upper():
            continue
        rows = list(csv.reader(io.StringIO(_get(f"{SHEET}/export?format=csv&gid={gid}"))))
        try:
            week = time.strptime(rows[0][1].strip(), "%b %d, %Y")
            hdr = next(i for i, r in enumerate(rows) if r[:2] == ["Date", "Time"])
        except (IndexError, ValueError, StopIteration):
            continue
        day = None
        for r in rows[hdr + 1:]:
            if r and r[0].strip():
                day = r[0].strip()                         # "Mon 10/26"
            if day and len(r) > 1 and r[1].strip():
                mm, dd = (int(x) for x in day.split()[-1].split("/"))
                year = week.tm_year + (1 if week.tm_mon == 12 and mm == 1 else 0)
                out[(f"{year:04d}-{mm:02d}-{dd:02d}", r[1].strip())] = [(r[2 + g].strip() if len(r) > 2 + g else "")
                                                                       for g in range(8)]
    return out


def claims_cover(start: float, end: float, gpus=(0, 1, 2, 3)) -> tuple[bool, str]:
    """Do the sheet's claims put MEMBER on `gpus` for every 30-min slot (US Eastern) that [start, end] touches?"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    try:
        claims = sheet_claims()
    except Exception as exc:
        return False, f"the sheet could not be read: {type(exc).__name__}: {exc}"
    tz = ZoneInfo("America/New_York")
    t = datetime.fromtimestamp(start, tz)
    t = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
    stop = datetime.fromtimestamp(end, tz)
    while t <= stop:
        slot = t.strftime("%I:%M %p").lstrip("0")
        names = claims.get((t.strftime("%Y-%m-%d"), slot))
        if names is None or any(names[g] != MEMBER for g in gpus):
            return False, f"no {MEMBER} claim on GPUs {list(gpus)} at {t:%a %m/%d} {slot} US Eastern ({names})"
        t += timedelta(minutes=30)
    return True, f"claimed through {stop:%a %m/%d %I:%M %p} US Eastern"


def run(cond: str, warmup_s: float | None = None, window_s: float | None = None, users: int = 0,
        think_scale: float = 1.0, claim_check: bool = True) -> int:
    c = mw.cond(cond)
    if c.get("topic") != "19_efficiency_signals":
        raise SystemExit(f"{cond} is not a topic-19 condition")
    if c.get("gate_k_pending"):
        raise SystemExit(f"{cond}: the gate size is pending (set ef_workloads.GATE_K from pilot P1 first)")
    if claim_check:
        warm = c.get("warmup_s", 0) if warmup_s is None else warmup_s
        win = c.get("window_s", 0) if window_s is None else window_s
        now = time.time()
        ok, why = claims_cover(now, now + float(warm) + float(win) + float(c.get("overhead_s", 900)))
        print(f"[claims] {cond}: {why}", flush=True)
        if not ok:
            print(f"[claims] {cond} refused: enter the claim in the GPU sheet first", flush=True)
            return 3
    return wp.run_cell(cond, GROUP, warmup_s, window_s, users, think_scale)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cell", help="run one cell on GPUs 0-3")
    c.add_argument("--cond", required=True, choices=sorted(ew.conditions()))
    c.add_argument("--warmup-s", type=float, default=None, help="override (tests)")
    c.add_argument("--window-s", type=float, default=None, help="override (tests)")
    c.add_argument("--users", type=int, default=0, help="override N (tests)")
    c.add_argument("--think-scale", type=float, default=1.0, help="scale think times (tests)")
    c.add_argument("--no-claim-check", action="store_true", help="skip the GPU-sheet check (tests only)")
    ch = sub.add_parser("chain", help="run cells one after another; stop at the first failure")
    ch.add_argument("conds", nargs="+", choices=sorted(ew.conditions()))
    sub.add_parser("claims", help="print whether the sheet covers the next 2 h on GPUs 0-3")
    sub.add_parser("hours", help="print the GPU-hour tally")
    sub.add_parser("gpus", help="print GPUs 0-3")
    args = ap.parse_args()
    if args.cmd in ("cell", "chain"):
        register()
    if args.cmd == "cell":
        sys.exit(run(args.cond, args.warmup_s, args.window_s, args.users, args.think_scale,
                     claim_check=not args.no_claim_check))
    if args.cmd == "claims":
        now = time.time()
        print(claims_cover(now, now + 7200))
        return
    if args.cmd == "chain":
        for i, cond in enumerate(args.conds):
            print(f"[chain] {i + 1}/{len(args.conds)} {cond} at {time.strftime('%FT%TZ', time.gmtime())}; "
                  f"GPU-hours so far {wp.tally():.1f} of {wp.CAP_GPU_H:.0f}", flush=True)
            rc = run(cond)
            if rc != 0:
                print(f"[chain] {cond} failed (rc {rc}): the chain stops here", flush=True)
                sys.exit(rc)
        print(f"[chain] done; GPU-hours {wp.tally():.1f} of {wp.CAP_GPU_H:.0f}", flush=True)
        return
    if args.cmd == "hours":
        rows = [json.loads(line) for line in wp.HOURS.open()] if wp.HOURS.exists() else []
        for row in rows:
            print(f"{row['cond']:<20} {row['pair']} {row['gpu_h']:7.2f} GPU-h rc={row['rc']} {row.get('error') or ''}")
        print(f"total {wp.tally():.2f} of {wp.CAP_GPU_H:.0f} GPU-h")
        return
    print(json.dumps({g: wp.gpu_status([g])[g] for g in sorted(wp.ALLOWED_GPUS)}))


if __name__ == "__main__":
    main()
