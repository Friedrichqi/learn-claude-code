#!/usr/bin/env python3
"""Print new chunk completions, stage events and disk-guard kills across all logs (for a Monitor)."""
import re
import time
from pathlib import Path

RUNS = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval/runs")
PAT = re.compile(r" done [a-z0-9]+__(c\d+|h_\S+) rc=| finished$|KILLED|ready on|exited during|not healthy|stopped;|"
                 r"Traceback|claim window ended|adopting|starting |did not start|every task has|: done$|"
                 r"batches returned|stopping |stop/tbench| round \d+: |lane \S+ (done|failed)|qwen phase|only \[|helping |helper batch rc|did not start|helper finished|batch rc=|rerun round|round \S+ done|every block|epic sessions .*left \[[^\]]|"
                 r"selftests|ALL OK|SOME CHECKS FAILED|FAIL  |pilot tasks by|finished rc=|stage [BC] done|"
                 r"gates \S+: rc=|server failed|Error")
offsets: dict[Path, int] = {}
first = True
while True:
    for f in sorted(list(RUNS.glob("batch_*.log")) + list(RUNS.glob("stage_*.log")) +
                    list(RUNS.glob("profile_*.log")) + list(RUNS.glob("p4_*.log")) + list(RUNS.glob("tb_stop_*.log")) +
                    list(RUNS.glob("qwen_phase*.log")) + list(RUNS.glob("helper32*.log")) +
                    list((RUNS / "epic").glob("round_*.log")) + list((RUNS / "epic").glob("stage_*.log")) +
                    list((RUNS / "epic").glob("replay_*.log")) +
                    [RUNS / "disk_guard.log"]):
        if not f.exists():
            continue
        size = f.stat().st_size
        start = offsets.get(f, size if first else 0)
        if size > start:
            with f.open() as h:
                h.seek(start)
                for line in h:
                    # controller logs also carry their batches' stdout: chunk lines come from the batch logs
                    if f.stem.startswith(("p4_", "profile_", "qwen_phase")) and re.search(r" (run|done) q\S+__", line):
                        continue
                    if PAT.search(line):
                        print(f"{f.stem}: {line.strip()[:220]}", flush=True)
        offsets[f] = size
    first = False
    time.sleep(20)
