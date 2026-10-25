#!/usr/bin/env python3
"""EPIC round on SWE-bench Pro and Terminal-Bench (phase e2; the user's request after e1): MiniMax-M2.7, the same 5
arms as e1, on GPUs 0-3.

    S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker S15_KEEP_IMAGES=1 \
      setsid nohup .venv/bin/python run/stage_e2.py --base-gpu-h 120.2 >> runs/epic/stage_e2.log 2>&1 < /dev/null &

1. Waits for GPUs 0-3 to be empty, starts minimax-m2.7-epic (the e1 configuration), runs the live selftests and
   records the code version (runs/epic/code_e2.json). Stops if a selftest fails.
2. Replicates in lockstep: replicate k of both benchmarks runs at once (one run/epic_round.py per benchmark: 30 tasks
   x 5 arms, one-task chunks, forward and reverse batches, agent timeout x2 for every arm). Replicate k+1 starts only if
   the GPU-hours so far plus the last replicate's cost stay within --cap (the study's budget): the rule is fixed here,
   before any result is seen.
3. Stops the server when the last replicate ends, also on SIGTERM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
import serve  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
VPY = "/data/fritz/venv/bin/python"
SERVER = "minimax-m2.7-epic"
GPUS = [0, 1, 2, 3]
ARMS = "prefix,prefix_aa,shift,epic128,epic128a1"
BENCHES = ("swepro", "tbench")
CODE = ["adapter/harbor_agent.py", "adapter/s15_child.py", "run/batch.py", "run/epic_round.py", "serve/models.yaml",
        "docker/CURRENT_TAG"]


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-gpu-h", type=float, required=True, help="GPU-hours used before this stage")
    ap.add_argument("--cap", type=float, default=150.0)
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--timeout-multiplier", type=float, default=2.0)
    a = ap.parse_args()
    started = {"t": None}

    def stop(*_args):
        if (serve.STATE / f"{SERVER}.json").exists():
            log(f"stopping {SERVER}: {serve.stop(SERVER)}")
        if _args:
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    while not serve.gpus_empty(GPUS):
        time.sleep(60)
    try:
        state = serve.start(SERVER, GPUS)
        started["t"] = time.time()
        url = state["url"]
        log(f"{SERVER} ready on {url} after {state.get('ready_s')}s")
        # live counters every minute (per-rank every 5 min) for the gates after the server is stopped
        subprocess.Popen(["python3", "-u", str(ROOT / "run" / "epic_health.py"), "--server", SERVER, "--since",
                          str(int(time.time()))], stdout=open(ROOT / "runs" / "epic" / "health_e2.out", "a"),
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        st = subprocess.run([VPY, str(ROOT / "epic" / "selftest_live.py"), "--url", url, "--format", "minimax",
                             "--out", str(ROOT / "runs" / "epic" / "selftest_e2.json")], capture_output=True, text=True)
        print(st.stdout[-3000:], st.stderr[-1500:], flush=True)
        if st.returncode != 0:
            log("selftests failed: e2 stops here")
            return
        files = CODE + sorted(str(p.relative_to(ROOT)) for p in (ROOT / "epic").glob("*.py")) + \
            sorted(str(p.relative_to(ROOT)) for p in (ROOT / "epic" / "templates").glob("*"))
        (ROOT / "runs" / "epic" / "code_e2.json").write_text(json.dumps(
            {"recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "server_started": started["t"],
             "files": {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files}}, indent=1))
        last_cost = None
        for k in range(a.replicates):
            used = a.base_gpu_h + len(GPUS) * (time.time() - started["t"]) / 3600
            if last_cost is not None and used + last_cost > a.cap:
                log(f"budget rule: {used:.1f} GPU-h used + {last_cost:.1f} for another replicate > {a.cap}: "
                    f"stopping after {k} replicate(s)")
                break
            log(f"replicate {k}: {used:.1f} GPU-h used so far")
            t0 = time.time()
            procs = []
            for bench in BENCHES:
                cmd = [PY, str(ROOT / "run" / "epic_round.py"), "--server", SERVER, "--arms", ARMS, "--bench", bench,
                       "--tasks", "first:30", "--phase", "e2", "--replicates", "1", "--first-replicate", str(k),
                       "--parallel", "2", "--chunk", "1", "--both-ends",
                       "--agent-timeout-multiplier", str(a.timeout_multiplier), "--keep-server"]
                logf = open(ROOT / "runs" / "epic" / f"round_e2_{bench}.log", "a")
                procs.append((bench, subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=dict(os.environ))))
            for bench, p in procs:
                p.wait()
                log(f"replicate {k} {bench}: epic_round rc={p.returncode}")
            last_cost = len(GPUS) * (time.time() - t0) / 3600
            log(f"replicate {k} took {last_cost:.1f} GPU-h")
    finally:
        stop()
    used = a.base_gpu_h + (len(GPUS) * (time.time() - started["t"]) / 3600 if started["t"] else 0)
    log(f"stage e2 done; about {used:.1f} GPU-h used in the study")


if __name__ == "__main__":
    main()
