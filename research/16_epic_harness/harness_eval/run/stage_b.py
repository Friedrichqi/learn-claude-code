#!/usr/bin/env python3
"""EPIC round, stage B (exploratory): Qwen3.8-27B on GPUs 0-3, one EPIC server instance per GPU.

    S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker S15_KEEP_IMAGES=1 \
      setsid nohup .venv/bin/python run/stage_b.py >> runs/epic/stage_b.log 2>&1 < /dev/null &

1. Waits until GPUs 0-3 are empty (stage A's server stopped), starts qwen3.8-27b-epic@g0 and runs the live
   selftests on it (--format qwen). Stops everything if a check fails.
2. Starts @g1..@g3 and runs, at the same time:
   - the live pilot: 5 tasks x 4 arms (prefix, shift, epic128, epic128a1), one replicate, agent timeout x2. A
     task's arms all run on one instance (paired blocks): tasks are dealt to the four instances, phase
     "b1<letter>r0" per instance;
   - the E-A replay (run/epic_replay_live.py): `REPLAY_N` baseline trajectories that snipped, capped at
     `REPLAY_CALLS` lead calls each, under prefix / prefix_aa / shift / epic128 / epic128a1, spread over the four
     instances at low concurrency (it fills the pilot's idle server time; both are paired within an instance).
3. Stops every instance when both are done (also on SIGTERM), then writes the gates of each pilot phase and the
   replay report.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
sys.path.insert(0, str(ROOT / "run"))
import serve  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
VPY = "/data/fritz/venv/bin/python"                       # vLLM's venv: tokenizers, vLLM's Anthropic converter
SERVER = "qwen3.8-27b-epic"
INSTANCES = {"g0": 0, "g1": 1, "g2": 2, "g3": 3}
ARMS = "prefix,shift,epic128,epic128a1"
REPLAY_ARMS = "prefix,prefix_aa,shift,epic128,epic128a1"
REPLAY_N = 10
REPLAY_CALLS = 60
REPLAY_WORKERS = 12
PILOT_TASKS = 5
TAG = "b1"
started: list[str] = []


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def stop_all(*_args) -> None:
    for key in list(started):
        if (serve.STATE / f"{key}.json").exists():
            log(f"stopping {key}: {serve.stop(key)}")
        started.remove(key)
    if _args:
        raise SystemExit(0)


def start(inst: str) -> str:
    key = f"{SERVER}@{inst}"
    state = serve.start(SERVER, [INSTANCES[inst]], instance=inst)
    started.append(key)
    log(f"{key} ready on {state['url']} after {state.get('ready_s')}s")
    return state["url"]


def snipped_tasks_in_order(n: int) -> list[str]:
    sys.path.insert(0, str(ROOT / "report"))
    import epic_report
    snip = epic_report.snipped_tasks("qwen3.8-27b", "swe")
    order = json.loads((ROOT / "run" / "subsets.json").read_text())["swe"]["tasks"]
    return [t for t in order if t in snip][:n]


def main() -> None:
    signal.signal(signal.SIGTERM, stop_all)
    while not serve.gpus_empty(list(INSTANCES.values())):
        time.sleep(60)
    try:
        urls = {"g0": start("g0")}
        st = subprocess.run([VPY, str(ROOT / "epic" / "selftest_live.py"), "--url", urls["g0"], "--format", "qwen",
                             "--log", str(ROOT / "runs" / "epic" / f"{SERVER}@g0.calls.jsonl"),
                             "--out", str(ROOT / "runs" / "epic" / "selftest_qwen_g0.json")],
                            capture_output=True, text=True)
        print(st.stdout[-3000:], st.stderr[-2000:], flush=True)
        if st.returncode != 0:
            log("selftests failed: stage B stops here")
            return
        for inst in ("g1", "g2", "g3"):
            urls[inst] = start(inst)
        tasks = snipped_tasks_in_order(PILOT_TASKS)
        deal = {inst: [] for inst in INSTANCES}
        for i, t in enumerate(tasks):
            deal[list(INSTANCES)[i % len(INSTANCES)]].append(t)
        log(f"pilot tasks by instance: {deal}")
        env = dict(os.environ)
        procs = []
        for inst, ts in deal.items():
            if not ts:
                continue
            cmd = [PY, str(ROOT / "run" / "epic_round.py"), "--server", SERVER, "--instance", inst,
                   "--arms", ARMS, "--bench", "swe", "--tasks", ",".join(ts), "--phase", f"{TAG}{chr(97 + int(inst[1]))}",
                   "--replicates", "1", "--parallel", "1", "--chunk", str(len(ts)), "--agent-timeout-multiplier", "2",
                   "--keep-server"]
            logf = open(ROOT / "runs" / "epic" / f"round_{TAG}_{inst}.log", "a")
            procs.append(("pilot " + inst, subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env)))
        rcmd = [VPY, str(ROOT / "run" / "epic_replay_live.py"), "run", "--urls", ",".join(urls[i] for i in INSTANCES),
                "--model", SERVER, "--baseline", "qwen3.8-27b", "--bench", "swe", "--n", str(REPLAY_N),
                "--arms", REPLAY_ARMS, "--max-calls", str(REPLAY_CALLS), "--workers", str(REPLAY_WORKERS),
                "--tag", f"{TAG}_replay"]
        rlog = open(ROOT / "runs" / "epic" / f"replay_{TAG}.log", "a")
        procs.append(("replay", subprocess.Popen(rcmd, stdout=rlog, stderr=subprocess.STDOUT)))
        while procs:
            time.sleep(30)
            for name, p in [x for x in procs if x[1].poll() is not None]:
                log(f"{name} finished rc={p.returncode}")
                procs.remove((name, p))
        for inst in INSTANCES:
            phase = f"{TAG}{chr(97 + int(inst[1]))}r0"
            g = subprocess.run([PY, str(ROOT / "epic" / "pilot_gates.py"), "--server", SERVER, "--bench", "swe",
                                "--phase", phase, "--url", urls[inst], "--gpus", "1",
                                "--log", str(ROOT / "runs" / "epic" / f"{SERVER}@{inst}.calls.jsonl"),
                                "--model-dir", "/data/fritz/hf/hub/models--Qwen--Qwen3.8-27B-FP8"],
                               capture_output=True, text=True)
            log(f"gates {phase}: rc={g.returncode}")
    finally:
        stop_all()
    logs = ",".join(str(ROOT / "runs" / "epic" / f"{SERVER}@{i}.calls.jsonl") for i in INSTANCES)
    rep = subprocess.run([VPY, str(ROOT / "run" / "epic_replay_live.py"), "report", "--tag", f"{TAG}_replay",
                          "--logs", logs], capture_output=True, text=True)
    print(rep.stdout[-4000:], rep.stderr[-2000:], flush=True)
    log("stage B done")


if __name__ == "__main__":
    main()
