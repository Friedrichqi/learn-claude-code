#!/usr/bin/env python3
"""Throttle new chunks by free space on Docker's shared root disk. run/state/slots.json caps how many chunks may
be in flight per kind, and every batch re-reads it before taking a slot, so lowering a cap stops new chunks from
starting while running chunks carry on. This acts before the disk guard has to kill anything.

    free >= 16 GB: heavy 2, light 1     10-16 GB: heavy 1, light 1     < 10 GB: heavy 1, light 0

    nohup python3 run/disk_governor.py &      (log: runs/disk_governor.log)
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
SLOTS = ROOT / "run" / "state" / "slots.json"
LEVELS = [(16.0, {"heavy": 2, "light": 1, "tiny": 4}),
          (10.0, {"heavy": 1, "light": 1, "tiny": 4}),
          (0.0, {"heavy": 1, "light": 0, "tiny": 4})]
HOLD_S = 120          # keep a level at least this long, so a pull in progress does not flip it back and forth


def main() -> None:
    log = open(ROOT / "runs" / "disk_governor.log", "a")
    current, since = None, 0.0
    while True:
        free = shutil.disk_usage("/var/lib/docker").free / 1e9
        want = next(limits for floor, limits in LEVELS if free >= floor)
        if want != current and (current is None or time.time() - since >= HOLD_S
                                or want["heavy"] + want["light"] < current["heavy"] + current["light"]):
            SLOTS.write_text(json.dumps(want))
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            log.write(f"{stamp} free {free:.1f} GB: slots {want}\n")
            log.flush()
            current, since = want, time.time()
        time.sleep(20)


if __name__ == "__main__":
    main()
