#!/usr/bin/env python3
"""Topic 23's schedule: runs two at a time, one per GPU (slot a = GPU 4, slot b = GPU 5).

  pilot  pilot_none (slot a, 2.0 h: none, then the K sweep) | pilot_wait (slot b, 1.2 h), on the pilot tasks
  smoke  every setup for about 15 min (0.45 h stages) on the pilot tasks, in the main pairs, named smoke_<setup>
  main   the pre-registered pairs, in order:
           P1 none | lmc_all      does offload by itself help?
           P2 wait | lmc          does offload fix wait's eviction problem?
           P3 gate8 | agate       does the LLM-set K beat a fixed one?
           P4 gate16 | none_r2    none_r2 - none: run, time and GPU noise together
Each run is `et_cell.py run` with --hours HOURS; a run its stage stopped at the time limit (exit 124) is run once more
(Harbor skips its finished trials). Both runs of a pair start together; the next pair starts when both have ended.
Progress goes to data/runs/chain.log.

    nohup python research/23_efficiency_tools/et_chain.py pilot > data/runs/chain_pilot.out 2>&1 &
    nohup python research/23_efficiency_tools/et_chain.py main [--from P2] > data/runs/chain.out 2>&1 &
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
PY = "/data/fritz/venv23/bin/python"
LOG = DIR / "data" / "runs" / "chain.log"
HOURS = 5.0
PAIRS = {"P1": ("none", "lmc_all"), "P2": ("wait", "lmc"), "P3": ("gate8", "agate"), "P4": ("gate16", "none_r2")}
PILOT = (("pilot_none", 2.0), ("pilot_wait", 1.2))
SMOKE_H = 0.45


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def start(setup: str, slot: str, group: str, hours: float, name: str | None = None) -> subprocess.Popen:
    name = name or setup
    cmd = [PY, str(DIR / "et_cell.py"), "run", "--setup", setup, "--slot", slot, "--group", group,
           "--hours", str(hours), "--name", name]
    out = open(DIR / "data" / "runs" / f"{name}.stage.log", "a")
    log(f"start {name} ({setup}) on slot {slot} ({group}, {hours} h)")
    return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)


def pair(runs: list[tuple], resume: bool = True) -> list[int]:
    """Start the runs (setup, slot, group, hours[, name]) together; a run its stage stopped at the time limit (124) is
    resumed once (resume=True), alone if its partner is done."""
    procs = {}
    for r in runs:
        setup, slot, group, h, *rest = r
        name = rest[0] if rest else setup
        procs[name] = (start(setup, slot, group, h, name), setup, slot, group, h, False)
    codes = {}
    while procs:
        time.sleep(30)
        for name, (p, setup, slot, group, h, resumed) in list(procs.items()):
            rc = p.poll()
            if rc is None:
                continue
            log(f"end {name}: exit {rc}")
            del procs[name]
            if rc == 124 and resume and not resumed:
                procs[name] = (start(setup, slot, group, h, name), setup, slot, group, h, True)
            else:
                codes[name] = rc
    return [codes[r[4] if len(r) > 4 else r[0]] for r in runs]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("pilot", "smoke", "main"))
    ap.add_argument("--from", dest="start", default="P1", choices=sorted(PAIRS))
    a = ap.parse_args()
    if a.what == "pilot":
        (sa, ha), (sb, hb) = PILOT
        # the pilots end at their time limit by design: never resumed (2026-11-02 21:58 UTC, see README Changes)
        log(f"pilot: {pair([(sa, 'a', 'pilot', ha), (sb, 'b', 'pilot', hb)], resume=False)}")
        return
    if a.what == "smoke":
        # every setup for ~15 min of client time on the pilot tasks, in the main pairs (stage: 0.45 h)
        for name in sorted(PAIRS):
            if name < a.start:
                continue
            sa, sb = PAIRS[name]
            rcs = pair([(sa, "a", "pilot", SMOKE_H, f"smoke_{sa}"), (sb, "b", "pilot", SMOKE_H, f"smoke_{sb}")],
                       resume=False)
            log(f"smoke {name}: {sa} {rcs[0]}, {sb} {rcs[1]}")
        return
    for name in sorted(PAIRS):
        if name < a.start:
            continue
        sa, sb = PAIRS[name]
        rcs = pair([(sa, "a", "main", HOURS), (sb, "b", "main", HOURS)])
        log(f"{name} done: {sa} {rcs[0]}, {sb} {rcs[1]}")
        if any(rc == 4 for rc in rcs):
            log("stopped: the freeze check refused a run")
            sys.exit(4)


if __name__ == "__main__":
    main()
