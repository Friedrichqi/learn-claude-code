#!/usr/bin/env python3
"""Infrastructure events of e3 for a Monitor, and never an outcome (runs/epic/prereg_e3.md: no interim look).

    python3 -u run/watch_e3.py

- runs/epic/stage_e3.log: the stage's own events (server, selftests, replicates, gates, budget, stop);
- runs/epic/health_e3.out: the health watcher's alerts and heartbeats;
- runs/epic/round_e3.log: failed batches, rerun rounds, the round's end;
- runs/batch_minimax-m2.7-epic+*_swe_e3r*.log: failed jobs, stop files, tracebacks. A job's line ends in the Harbor
  summary, which holds the job's mean reward, so everything after " | " is cut.
- every 30 min: complete blocks per replicate (all 5 arms finished; never their rewards).
"""
import glob
import json
import re
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
SERVER = "minimax-m2.7-epic"
ARMS = ["prefix", "prefix_aa", "epic128", "epic128a1", "epic128a4"]
STAGE = re.compile(r"ready on|selftest|ALL OK|SOME CHECKS FAILED|FAIL|replicate|gates|budget|stop|terminated|"
                   r"done|Traceback|Error|cap|pre-registration|sha256")
ROUND = re.compile(r"batch rc=[^0]|rerun round|round \S+ done|every block|per-block:|Traceback|Error|adopting")
BATCH = re.compile(r"rc=[1-9-]|stop file|Traceback|sweep failed|left \[[^\]]|Error")


def progress() -> str:
    out = []
    for k in range(3):
        done: dict[str, set[str]] = {}
        for arm in ARMS:
            for res in glob.glob(str(ROOT / "jobs" / f"{SERVER}+{arm}" / "swe" / f"e3r{k}b*__*" / "*__*" / "result.json")):
                m = re.fullmatch(rf"e3r{k}b(\d+)(x\d+)?", Path(res).parts[-3].split("__")[0])
                try:
                    if m and json.loads(Path(res).read_text()).get("finished_at"):
                        done.setdefault(m.group(1), set()).add(arm)
                except (OSError, json.JSONDecodeError):
                    pass
        out.append(f"e3r{k} {sum(1 for v in done.values() if len(v) == len(ARMS))}")
    return "progress (complete blocks): " + ", ".join(out)


def main() -> None:
    offsets: dict[Path, int] = {}
    first, last_progress = True, 0.0
    while True:
        files = [(ROOT / "runs" / "epic" / "stage_e3.log", STAGE), (ROOT / "runs" / "epic" / "health_e3.out", None),
                 (ROOT / "runs" / "epic" / "round_e3.log", ROUND)]
        files += [(Path(f), BATCH) for f in sorted(glob.glob(str(ROOT / "runs" / f"batch_{SERVER}+*_swe_e3r*.log")))]
        for f, pat in files:
            if not f.exists():
                continue
            size = f.stat().st_size
            start = offsets.get(f, size if first else 0)
            if size > start:
                with f.open(errors="replace") as h:
                    h.seek(start)
                    for line in h:
                        line = line.split(" | ")[0].strip()
                        if line and (pat is None or pat.search(line)):
                            print(f"{f.stem}: {line[:240]}", flush=True)
            offsets[f] = size
        first = False
        if time.time() - last_progress > 1800:
            last_progress = time.time()
            print(progress(), flush=True)
        time.sleep(20)


if __name__ == "__main__":
    main()
