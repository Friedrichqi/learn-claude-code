#!/usr/bin/env python3
"""Run topic 22's 8 runs back to back, in the pre-registered order, one GPU stage each (wb_cell.py run).

- A run is done when its Harbor job holds a result for every queued task and none was cancelled.
- A run its stage stopped at the time limit (exit 124) is resumed once at once: wb_cell's client reruns the cancelled
  trials.
- Stop rule (prereg.md): after each block (both setups of one N), the GPU-hours left are projected from the runs so
  far. The chain stops if the tally plus the projection passes the cap.
- It also stops when a stage refuses: claims (3), frozen files (4), or anything else not 0/124.

    python research/22_wait_bench/wb_chain.py            # log: data/runs/chain.log
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
PY = "/data/fritz/venv/bin/python"
DATA = DIR / "data"
ORDER = [(32, "wait"), (32, "none"), (16, "none"), (16, "wait"), (8, "wait"), (8, "none"), (4, "none"), (4, "wait")]
# since 2026-11-01 16:40 UTC (the user): no GPU-hour cap and no sheet check (the user handles the claims); a stage's
# --hours is then only its time limit, raised for N=8 and 4 so a run needs no resume (was 3.0 and 4.0)
HOURS = {32: 3.0, 16: 3.0, 8: 5.0, 4: 7.0}
CAP_GPU_H = float("inf")
N_TASKS = 96
LOG = DATA / "runs" / "chain.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def done(name: str) -> bool:
    res = list((DATA / "jobs" / name).glob("*/result.json"))
    if len(res) < N_TASKS:
        return False
    for r in res:
        try:
            if (json.loads(r.read_text()).get("exception_info") or {}).get("exception_type") == "CancelledError":
                return False
        except (OSError, ValueError):
            return False
    return True


def gpu_hours() -> list[dict]:
    path = DATA / "gpu_hours.jsonl"
    return [json.loads(line) for line in path.open() if line.strip()] if path.exists() else []


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log(f"chain start: {[f'n{n}_{c}' for n, c in ORDER]}")
    for i, (n, cond) in enumerate(ORDER):
        name = f"n{n}_{cond}"
        for attempt in (1, 2):
            if done(name):
                log(f"{name}: done")
                break
            log(f"{name}: stage {attempt} (--hours {HOURS[n]})")
            with open(DATA / "runs" / f"{name}.stage.log", "a") as out:
                rc = subprocess.run([PY, str(DIR / "wb_cell.py"), "run", "--n", str(n), "--cond", cond,
                                     "--hours", str(HOURS[n]), "--no-claim-check"], stdout=out,
                                    stderr=subprocess.STDOUT).returncode
            log(f"{name}: stage exit {rc}")
            if rc not in (0, 124):
                log(f"chain stopped: {name} stage exit {rc} (3 claims, 4 frozen files, else see its stage log)")
                return rc
        if not done(name):
            log(f"chain stopped: {name} incomplete after two stages")
            return 1
        if i % 2 == 1 and i + 1 < len(ORDER):           # a block is complete: project the rest
            rows = [r for r in gpu_hours() if r["stage"].startswith("n")]
            tally = sum(r["gpu_h"] for r in gpu_hours())
            per_run = sum(r["gpu_h"] for r in rows) / max(1, len({r["stage"] for r in rows}))
            left = len(ORDER) - (i + 1)
            projected = tally + per_run * left
            log(f"block done: tally {tally:.2f} GPU-h, {per_run:.2f} per run so far, {left} runs left, projected "
                f"{projected:.2f} of {CAP_GPU_H}")
            if projected > CAP_GPU_H:
                log("chain stopped: the projection passes the cap (prereg stop rule); ask the user")
                return 5
    log("chain done: all 8 runs complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
