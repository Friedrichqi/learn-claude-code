#!/usr/bin/env python3
"""Plan the remaining trials so every task of the profile set reaches K valid trials.

    plan_p4.py --model qwen3.8-27b --n swe=50,swepro=40,tbench=40,gaia=40 [--k 3] [--phase p4]

A trial counts when Harbor finished it without an infrastructure failure (agent timeouts and budget stops
count: they are the model's outcome), in a profile phase (q3 qualification, q4* profiling; 250 lead calls). Prints one stage --batch spec per
(benchmark, missing-trial count): bench:phase:id1,id2,...:k:concurrency.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
INFRA = {"CancelledError", "KeyboardInterrupt", "RuntimeError", "EnvironmentStartError", "DockerError", "BuildError", "SandboxBuildFailedError",
         "HealthcheckError", "ConnectionError", "OSError"}
CONC = {"swe": 3, "swepro": 2, "tbench": 4, "gaia": 6}


def valid_trials(model: str, bench: str) -> dict[str, int]:
    """Valid trials per task, with the collector's rules (infra failures and excluded phases do not count)."""
    import sys
    sys.path.insert(0, str(ROOT / "report"))
    import collect
    counts: dict[str, int] = defaultdict(int)
    for row in collect.load_trials():
        if (row["model"] == model and row["bench"] == bench and row["phase"].startswith("q")
                and not row.get("infra") and row.get("finished")):
            counts[row["task"]] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--phase", default="q4")
    ap.add_argument("--conc", default="")
    a = ap.parse_args()
    subsets = json.loads((ROOT / "run" / "subsets.json").read_text())
    conc = dict(CONC)
    for item in filter(None, a.conc.split(",")):
        b, c = item.split("=")
        conc[b] = int(c)
    for item in a.n.split(","):
        bench, n = item.split("=")
        tasks = subsets[bench]["tasks"][: int(n)]
        have = valid_trials(a.model, bench)
        groups: dict[int, list[str]] = defaultdict(list)
        for t in tasks:
            missing = a.k - have.get(t, 0)
            if missing > 0:
                groups[missing].append(t)
        for missing, ids in sorted(groups.items(), reverse=True):
            print(f"{bench}:{a.phase}k{missing}:{','.join(ids)}:{missing}:{conc[bench]}")


if __name__ == "__main__":
    main()
