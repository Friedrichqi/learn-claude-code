#!/usr/bin/env python3
"""Keep the shared root disk from filling up: Docker's data root (/var/lib/docker) sits on a disk other users
need too, and a task container can write gigabytes (an agent downloading a dataset).

Every 5 s: log free space; below KILL_GB, stop the study task container with the largest writable layer
(Harbor names them <task>__<id>__env-main-1; only containers whose image is one of ours are considered) and
record it in runs/disk_guard_kills.jsonl so the collector can mark that trial as an infrastructure failure.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
KILL_GB = 5.0
WARN_GB = 8.0
OURS = re.compile(r"^(alexgshaw/|ghcr\.io/epoch-research/|ghcr\.io/scaleapi/|swebench/|jefzda/|hb__|.*__env-main)")
UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def size_bytes(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([kKMGT]?B)", text.strip())
    return float(m.group(1)) * UNITS[m.group(2)] if m else 0.0


def main() -> None:
    log = open(ROOT / "runs" / "disk_guard.log", "a")
    kills = ROOT / "runs" / "disk_guard_kills.jsonl"
    while True:
        free = shutil.disk_usage("/var/lib/docker").free / 1e9
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if free < WARN_GB:
            log.write(f"{stamp} free {free:.1f} GB\n")
            log.flush()
        if free < KILL_GB:
            out = subprocess.run(["docker", "ps", "-s", "--format", "{{.Names}}\t{{.Image}}\t{{.Size}}"],
                                 capture_output=True, text=True).stdout
            cands = []
            for line in out.splitlines():
                name, image, size = (line.split("\t") + ["", "", ""])[:3]
                if name.endswith("__env-main-1") and OURS.match(image):
                    cands.append((size_bytes(size.split("(")[0]), name, image))
            if cands:
                cands.sort(reverse=True)
                _, name, image = cands[0]
                subprocess.run(["docker", "kill", name], capture_output=True)
                # a killed container keeps its writable layer until Harbor tears the trial down, so stop the
                # Harbor run (eval container) that owns it: Harbor then removes its trial environments
                trial_key = name.removesuffix("__env-main-1").lower()
                owner = None
                for d in ROOT.glob("jobs/*/*/*/*"):
                    if d.name.lower() == trial_key:
                        model, bench, job = d.parts[-4], d.parts[-3], d.parts[-2]
                        ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
                        for c in ps.split():
                            if not c.startswith("s15eval-"):
                                continue
                            args = subprocess.run(["docker", "inspect", c, "--format", "{{join .Args \" \"}}"],
                                                  capture_output=True, text=True).stdout
                            if f"-o jobs/{model}/{bench} --job-name {job} " in args:
                                owner = c
                        break
                if owner:
                    subprocess.run(["docker", "stop", "-t", "60", owner], capture_output=True)
                rec = {"t": stamp, "free_gb": round(free, 2), "container": name, "image": image,
                       "writable_gb": round(cands[0][0] / 1e9, 2), "stopped_eval": owner}
                with kills.open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                log.write(f"{stamp} KILLED {name} ({rec['writable_gb']} GB writable) at {free:.1f} GB free\n")
                log.flush()
                time.sleep(60)                      # let Harbor tear the stopped run down before judging again
        time.sleep(5)


if __name__ == "__main__":
    main()
