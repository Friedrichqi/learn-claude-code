#!/usr/bin/env python3
"""One GPU stage: wait for the sheet claim, start the model server, run its batches, always stop the server.

    stage.py --model qwen3-32b --gpus 0 --claim "2026-10-11 22:00" "2026-10-12 08:00" \
        --batch swe:p1:first:50:1:2 --batch swepro:p2:first:5:1:2

Batch spec: bench:phase:tasks:k:concurrency (tasks as in run/batch.py: qual, profile, first:N, range:A:B).
The server is stopped as soon as the last batch ends (or on any error / signal), and the stage waits until the
GPUs show no compute process before exiting. Nothing starts before the claim window opens.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
sys.path.insert(0, str(ROOT / "run"))
import serve  # noqa: E402
import sheet  # noqa: E402

ET = ZoneInfo("America/New_York")


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--gpus", required=True)
    ap.add_argument("--claim", nargs=2, required=True, metavar=("START", "END"))
    ap.add_argument("--batch", action="append", required=True)
    ap.add_argument("--wait-file", action="append", default=[], help="wait until this path exists")
    ap.add_argument("--instance", default="", help="server instance name (several servers of one model)")
    a = ap.parse_args()
    gpus = [int(g) for g in a.gpus.split(",")]
    start, end = sheet.parse(a.claim[0]), sheet.parse(a.claim[1])

    for path in a.wait_file:
        while not Path(path).exists():
            log(f"waiting for {path}")
            time.sleep(120)
    if (ROOT / "run" / "state" / "no_sheet").exists():
        # 2026-10-12: the user handles the sheet claims for this study; no wait, no window end
        end = dt.datetime(2100, 1, 1, tzinfo=ET)
        log(f"sheet check off (the user claims GPUs {gpus} in the sheet)")
    else:
        while True:
            ok, problems = sheet.check(gpus, start, end)
            now = dt.datetime.now(ET)
            if ok and now >= start:
                break
            log(f"waiting: claim ok={ok} ({problems[:2]}), now {now:%H:%M} ET, window opens {start:%m-%d %H:%M}")
            time.sleep(300)
        log(f"claim confirmed for GPUs {gpus} {a.claim[0]} -> {a.claim[1]} ET")
    while not serve.gpus_empty(gpus):
        if dt.datetime.now(ET) >= end:
            log(f"claim window ended while GPUs {gpus} were busy: {serve.gpu_status(gpus)}")
            return
        log(f"waiting for GPUs {gpus} to be empty: {serve.gpu_status(gpus)}")
        time.sleep(120)

    def on_signal(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)

    state = None
    try:
        state = serve.start(a.model, gpus, instance=a.instance)
        log(f"server {a.model} ready on {state['url']} after {state.get('ready_s')}s")
        stop_dir = ROOT / "run" / "state" / "stop"
        stage_id = f"{a.model}@{a.instance}" if a.instance else a.model
        os.environ["S15_STAGE"] = stage_id
        for spec in a.batch:
            if (stop_dir / "all").exists() or (stop_dir / a.model).exists() or (stop_dir / stage_id).exists():
                log(f"stop file present; not starting {spec}")
                break
            bench, phase, *rest = spec.split(":")
            k, conc = rest[-2], rest[-1]
            tasks = ":".join(rest[:-2])
            if bench == "gaia" and not (ROOT / "secrets.env").is_file():
                log(f"no secrets.env (search-API key) yet; skipping {spec}")
                continue
            if dt.datetime.now(ET) >= end:
                log(f"claim window ended; skipping {spec}")
                break
            cmd = [sys.executable, str(ROOT / "run" / "batch.py"), "--model", a.model, "--url", state["url"],
                   "--bench", bench, "--phase", phase, "--tasks", tasks, "--k", k, "--concurrency", conc]
            log(f"batch {spec}")
            rc = subprocess.run(cmd).returncode
            log(f"batch {spec} rc={rc}")
    finally:
        if state is not None:
            log(serve.stop(state.get("key", a.model)))
        log(f"GPUs {gpus}: {serve.gpu_status(gpus)}")


if __name__ == "__main__":
    main()
