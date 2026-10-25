#!/usr/bin/env python3
"""The e3 task sample (plan "e3"): 100 new SWE-bench Verified tasks, a seeded draw within difficulty labels, with
spares for tasks whose environment fails the oracle check.

    .venv/bin/python run/sample_e3.py [--seed 20261014] [--out run/state/e3_tasks.json]
    .venv/bin/python run/sample_e3.py --replace TASK [--replace TASK ...]   # swap in the next spare of its stratum

- Pool: datasets/swe-bench-verified-prebuilt (500 tasks), minus e1's 30 tasks (run/subsets.json "swe", first 30) and
  the ">4 hours" tasks (always failed in the baseline, and the most expensive).
- Strata by the `difficulty` label in each task.toml: "<15 min fix" 35, "15 min - 1 hour" 50, "1-4 hours" 15. Each
  stratum is shuffled with the seed; its first n tasks are the sample, the next 10 its spares, in replacement order.
- The 100 sampled tasks are then shuffled with the seed into the run order, so every replicate mixes difficulties.
- A replacement takes the replaced task's place in the run order, and is logged in the file.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
DATASET = ROOT / "datasets" / "swe-bench-verified-prebuilt"
STRATA = {"<15 min fix": 35, "15 min - 1 hour": 50, "1-4 hours": 15}
SPARES = 10
DEFAULT_OUT = ROOT / "run" / "state" / "e3_tasks.json"


def difficulty(task: str) -> str:
    text = (DATASET / task / "task.toml").read_text()
    m = re.search(r'difficulty = "([^"]+)"', text)
    return m.group(1) if m else "?"


def draw(seed: int) -> dict:
    used = set(json.loads((ROOT / "run" / "subsets.json").read_text())["swe"]["tasks"][:30])
    pool = sorted(d.name for d in DATASET.iterdir() if (d / "task.toml").is_file())
    by: dict[str, list[str]] = {label: [] for label in STRATA}
    for t in pool:
        label = difficulty(t)
        if t not in used and label in by:
            by[label].append(t)
    strata = {}
    for label, n in STRATA.items():
        tasks = sorted(by[label])
        random.Random(f"{seed}|{label}").shuffle(tasks)
        if len(tasks) < n + SPARES:
            raise SystemExit(f"stratum {label!r} has {len(tasks)} tasks, needs {n + SPARES}")
        strata[label] = {"pool": len(tasks), "tasks": tasks[:n], "spares": tasks[n:n + SPARES]}
    order = [t for s in strata.values() for t in s["tasks"]]
    random.Random(f"{seed}|order").shuffle(order)
    return {"seed": seed, "drawn": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "excluded": {"e1_tasks": sorted(used), "difficulty": [">4 hours"]},
            "strata": strata, "tasks": order, "replacements": []}


def replace(data: dict, task: str) -> str:
    for label, s in data["strata"].items():
        if task in s["tasks"]:
            if not s["spares"]:
                raise SystemExit(f"no spare left in {label!r}")
            new = s["spares"].pop(0)
            s["tasks"][s["tasks"].index(task)] = new
            data["tasks"][data["tasks"].index(task)] = new
            data["replacements"].append({"out": task, "in": new, "stratum": label,
                                         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            return new
    raise SystemExit(f"{task} is not in the sample")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--replace", action="append", default=[])
    a = ap.parse_args()
    out = Path(a.out)
    if a.replace:
        data = json.loads(out.read_text())
        for t in a.replace:
            print(f"{t} -> {replace(data, t)}")
    else:
        if out.exists():
            raise SystemExit(f"{out} exists: the draw is fixed once made (use --replace)")
        data = draw(a.seed)
    out.write_text(json.dumps(data, indent=1))
    print(f"{len(data['tasks'])} tasks in {out}; strata " +
          ", ".join(f"{k}: {len(v['tasks'])} (+{len(v['spares'])} spares of {v['pool']})" for k, v in data["strata"].items()))


if __name__ == "__main__":
    main()
