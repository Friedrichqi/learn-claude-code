#!/usr/bin/env python3
"""Add Qwen3-32B capacity as Qwen3.8's GPUs free up during the Qwen phase.

Qwen3-32B in BF16 leaves 40,800 tokens of KV cache on one H100, about 1.25 full 32K contexts. With its four lanes
(SWE-bench, Pro, TB, pilot) on GPU 3, requests queue and median call latency rose from ~1.5 s to ~5 s. Qwen3.8's
trials are faster, so its lanes finish first. Whenever one of GPUs 0-2 is empty (its Qwen3.8 server stopped; the
phase controller never restarts it), this script:
1. starts qwen3-32b@gN there;
2. takes the next benchmark in HELP_ORDER whose Qwen3-32B batch is still running on GPU 3;
3. starts the same batch command against the new server with --reverse, so the two batches work from opposite
   ends of the chunk list and the per-job locks keep any chunk from running twice;
4. stops the server when that batch returns.

    S15_DOCKER_SOCK=... S15_DOCKER_ROOT=... S15_KEEP_IMAGES=1 nohup .venv/bin/python run/helper32.py >> runs/helper32.log &
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
import serve  # noqa: E402

HELP_ORDER = ["swepro", "swe", "tbench"]
FREE_GPUS = [0, 1, 2]
STOP = ROOT / "run" / "state" / "stop"


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def forward_batch(bench: str) -> list[str] | None:
    """The running Qwen3-32B batch for this benchmark on GPU 3 (not a --reverse helper), as an argv list."""
    out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        pid, _, args = line.strip().partition(" ")
        argv = args.split()
        if (len(argv) > 2 and argv[1].endswith("run/batch.py") and "--model" in argv
                and argv[argv.index("--model") + 1] == "qwen3-32b" and "--reverse" not in argv
                and argv[argv.index("--bench") + 1] == bench and "--with" not in argv
                and not argv[argv.index("--phase") + 1].startswith("p")):      # the pilot is not profile work
            return argv
    return None


def help_bench(gpu: int, bench: str, argv: list[str]) -> None:
    key = f"qwen3-32b@g{gpu}"
    try:
        state = serve.start("qwen3-32b", [gpu], instance=f"g{gpu}")
    except SystemExit as exc:
        log(f"{key} did not start: {str(exc)[:200]}")
        return
    log(f"{key} ready on {state['url']}; helping {bench} from the other end")
    try:
        cmd = list(argv)
        cmd[cmd.index("--url") + 1] = state["url"]
        cmd += ["--reverse"]
        rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, cwd=ROOT).returncode
        log(f"{key} {bench} helper batch rc={rc}")
    finally:
        log(f"stopping {key}: {serve.stop(key)}")


def main() -> None:
    pending = list(HELP_ORDER)
    threads: list[threading.Thread] = []
    used: set[int] = set()
    while pending and not (STOP / "all").exists() and not (STOP / "qwen3-32b").exists():
        for gpu in FREE_GPUS:
            if gpu in used or not pending or not serve.gpus_empty([gpu]):
                continue
            # the next benchmark that still has a Qwen3-32B batch running
            while pending:
                bench = pending.pop(0)
                argv = forward_batch(bench)
                if argv:
                    used.add(gpu)
                    th = threading.Thread(target=help_bench, args=(gpu, bench, argv))
                    th.start()
                    threads.append(th)
                    break
                log(f"no running qwen3-32b {bench} batch to help")
        time.sleep(60)
    for th in threads:
        th.join()
    log("helper finished")


if __name__ == "__main__":
    main()
