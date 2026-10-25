#!/usr/bin/env python3
"""e3, the pre-registered test of EPIC's accuracy cost (runs/epic/prereg_e3.md): MiniMax-M2.7, 100 new SWE-bench
Verified tasks (run/state/e3_tasks.json) x 3 replicates x 5 arms, on GPUs 0-3.

    S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker S15_KEEP_IMAGES=1 \
      setsid nohup .venv/bin/python run/stage_e3.py >> runs/epic/stage_e3.log 2>&1 < /dev/null &

1. Checks the pre-registration before anything runs: runs/epic/prereg_e3.md must match the sha256 recorded in
   runs/epic/prereg_e3.sha256, and the task list and the analysis script must match the sha256 the pre-registration
   lists for them. Logs the hashes; stops on a mismatch.
2. Waits for GPUs 0-3 to be empty, starts minimax-m2.7-epic, starts the health watcher, runs the live selftests and
   records the code version (runs/epic/code_e3.json). Stops if a selftest fails.
3. Replicates e3r0, e3r1, e3r2 in order, each one run/epic_round.py --per-block over all 100 tasks: 4 blocks at once,
   the agent timeout x2 for every arm, infrastructure failures rerun under the same block key. Its children run with
   S15_STAGE=e3, so the stop file run/state/stop/e3 ends the stage gracefully: the blocks in flight finish, no new one
   starts.
   - After the first 8 complete blocks of e3r0, the interim gate check (epic/pilot_gates.py): a stop if there was a
     preemption, fallbacks + resets reach 5 % of post-snip calls, a lead call lacks its one server record, the ranks
     disagree, or E-0 is below 97 %. ("No sessions left" is not checked here: the blocks in flight hold sessions.)
   - After each replicate, its gates (runs/epic/gates_minimax-m2.7-epic_e3r<k>.json), and a stop on the same
     conditions. A failed gate otherwise only gets reported.
   - Budget rule: replicate k+1 starts only if e3's GPU-hours so far plus replicate k's cost stay within --cap (95).
     At the cap itself the stop file ends the replicate in flight.
4. Stops the server when the last replicate ends, also on SIGTERM.
The stage never reads outcomes (rewards); the analysis is report/e3_analysis.py, after the run.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
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
ARMS = ["prefix", "prefix_aa", "epic128", "epic128a1", "epic128a4"]
TASKS = "run/state/e3_tasks.json"
PREREG = ROOT / "runs" / "epic" / "prereg_e3.md"
STOP = ROOT / "run" / "state" / "stop" / "e3"
INTERIM_BLOCKS = 8
INTERIM_E0 = 0.97
CODE = ["adapter/harbor_agent.py", "adapter/s15_child.py", "run/batch.py", "run/epic_round.py", "run/stage_e3.py",
        "run/sample_e3.py", "report/e3_analysis.py", "report/epic_report.py", "report/collect.py", "serve/models.yaml",
        "docker/CURRENT_TAG", TASKS]


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_prereg() -> bool:
    want = (ROOT / "runs" / "epic" / "prereg_e3.sha256").read_text().split()[0]
    got = sha(PREREG)
    log(f"pre-registration {PREREG.relative_to(ROOT)} sha256 {got} (recorded {want})")
    ok = got == want
    text = PREREG.read_text()
    for rel in (TASKS, "report/e3_analysis.py"):
        m = re.search(r"`" + re.escape(rel) + r"`[^\n]*sha256 `([0-9a-f]{64})`", text)
        cur = sha(ROOT / rel)
        log(f"{rel} sha256 {cur} (pre-registered {m.group(1) if m else 'missing'})")
        ok = ok and bool(m) and m.group(1) == cur
    return ok


def complete_blocks(phase: str) -> int:
    """Blocks of `phase` (per-block jobs "<phase>b<i>__c00") in which every arm's trial has finished."""
    done: dict[str, set[str]] = {}
    for arm in ARMS:
        for res in glob.glob(str(ROOT / "jobs" / f"{SERVER}+{arm}" / "swe" / f"{phase}b*__*" / "*__*" / "result.json")):
            m = re.fullmatch(re.escape(phase) + r"b(\d+)(x\d+)?", Path(res).parts[-3].split("__")[0])
            try:
                fin = json.loads(Path(res).read_text()).get("finished_at")
            except (OSError, json.JSONDecodeError):
                continue
            if m and fin:
                done.setdefault(m.group(1), set()).add(arm)
    return sum(1 for arms in done.values() if len(arms) == len(ARMS))


def gates(phase: str, url: str, interim: bool) -> tuple[bool, dict]:
    """Runs epic/pilot_gates.py on `phase`; True unless a stop condition holds (see the module docstring)."""
    p = subprocess.run([VPY, str(ROOT / "epic" / "pilot_gates.py"), "--server", SERVER, "--bench", "swe",
                        "--phase", phase, "--url", url], capture_output=True, text=True)
    out = ROOT / "runs" / "epic" / f"gates_{SERVER}_{phase}.json"
    if not out.is_file():
        log(f"gates {phase}: no report (rc={p.returncode}) {p.stderr[-800:]}")
        return False, {}
    rep = json.loads(out.read_text())
    if interim:
        # (the first start's interim report, which the read-order race failed, is ..._first8_0201utc.json)
        shutil.copy(out, out.with_name(f"gates_{SERVER}_{phase}_first{INTERIM_BLOCKS}.json"))
    ps, g = rep["pass"], rep["gates"]
    e0 = rep["e0"]["share_ge_99"]
    stop = [k for k in ("no_preemptions", "fallback_reset_lt_5pct", "one_record_per_call", "ranks_consistent")
            if not ps[k]]
    if e0 is None or e0 < INTERIM_E0:
        stop.append(f"E0 {e0} < {INTERIM_E0}")
    log(f"gates {phase}{' (interim)' if interim else ''}: trials {rep['cost']['trials']}, E-0 "
        f"{(e0 or 0):.2%} of {rep['e0']['calls']}, preemptions {g['preemptions']}, fallbacks+resets "
        f"{g['fallback_reset_share']:.2%} of {g['post_snip_calls']}, records missing/dup {g['missing_server_records']}/"
        f"{g['duplicate_server_records']}, ranks {g['ranks_consistent']}, sessions left {len(g['sessions_left'])}; "
        f"pass {json.dumps(ps)}; stop conditions: {stop or 'none'}")
    return not stop, rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=95.0, help="e3's GPU-hours (server uptime x 4 GPUs)")
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--first-replicate", type=int, default=0, help="resume: the replicates before it are done")
    ap.add_argument("--base-gpu-h", type=float, default=0.0,
                    help="resume: e3's GPU-hours before this start, all spent on replicate --first-replicate")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--timeout-multiplier", type=float, default=2.0)
    a = ap.parse_args()
    if not check_prereg():
        log("pre-registration check failed: e3 does not start")
        return
    if STOP.exists() or (STOP.parent / "all").exists():
        log(f"stop file present ({STOP}): e3 does not start")
        return
    started = {"t": None}
    child: dict[str, subprocess.Popen | None] = {"p": None}

    def gpu_h() -> float:
        return a.base_gpu_h + (len(GPUS) * (time.time() - started["t"]) / 3600 if started["t"] else 0.0)

    def stop(*_args):
        if child["p"] is not None and child["p"].poll() is None:
            child["p"].terminate()
        if (serve.STATE / f"{SERVER}.json").exists():
            log(f"stopping {SERVER}: {serve.stop(SERVER)}")
        if _args:
            log(f"terminated; about {gpu_h():.1f} GPU-h used by e3")
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    env = dict(os.environ, S15_STAGE="e3")
    while not serve.gpus_empty(GPUS):
        time.sleep(60)
    try:
        state = serve.start(SERVER, GPUS)
        started["t"] = time.time()
        url = state["url"]
        log(f"{SERVER} ready on {url} after {state.get('ready_s')}s")
        # live counters every minute (per-rank every 5 min) for the gates after the server is stopped
        subprocess.Popen(["python3", "-u", str(ROOT / "run" / "epic_health.py"), "--server", SERVER, "--since",
                          str(int(time.time()))], stdout=open(ROOT / "runs" / "epic" / "health_e3.out", "a"),
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        # a resumed stage keeps the first start's records
        tag = "" if not (ROOT / "runs" / "epic" / "code_e3.json").exists() else f"_{int(started['t'])}"
        st = subprocess.run([VPY, str(ROOT / "epic" / "selftest_live.py"), "--url", url, "--format", "minimax",
                             "--out", str(ROOT / "runs" / "epic" / f"selftest_e3{tag}.json")], capture_output=True, text=True)
        print(st.stdout[-3000:], st.stderr[-1500:], flush=True)
        if st.returncode != 0:
            log("selftests failed: e3 stops here")
            return
        files = CODE + sorted(str(p.relative_to(ROOT)) for p in (ROOT / "epic").glob("*.py")) + \
            sorted(str(p.relative_to(ROOT)) for p in (ROOT / "epic" / "templates").glob("*"))
        (ROOT / "runs" / "epic" / f"code_e3{tag}.json").write_text(json.dumps(
            {"recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "server_started": started["t"],
             "prereg_sha256": sha(PREREG), "files": {f: sha(ROOT / f) for f in files}}, indent=1))
        last_cost = None
        for k in range(a.first_replicate, a.replicates):
            if STOP.exists() or (STOP.parent / "all").exists():
                log(f"stop file present: no replicate e3r{k}")
                break
            if last_cost is not None and gpu_h() + last_cost > a.cap:
                log(f"budget rule: {gpu_h():.1f} GPU-h used + {last_cost:.1f} for another replicate > {a.cap}: "
                    f"stopping after replicate e3r{k - 1}")
                break
            log(f"replicate e3r{k}: {gpu_h():.1f} GPU-h used so far")
            t0 = time.time()
            cmd = [PY, str(ROOT / "run" / "epic_round.py"), "--server", SERVER, "--arms", ",".join(ARMS),
                   "--bench", "swe", "--tasks", f"list:{TASKS}", "--phase", "e3", "--replicates", "1",
                   "--first-replicate", str(k), "--parallel", str(a.parallel), "--per-block",
                   "--agent-timeout-multiplier", str(a.timeout_multiplier), "--keep-server"]
            child["p"] = subprocess.Popen(cmd, stdout=open(ROOT / "runs" / "epic" / "round_e3.log", "a"),
                                          stderr=subprocess.STDOUT, env=env)
            interim_done = k > 0
            halt = False
            while child["p"].poll() is None:
                time.sleep(60)
                if not interim_done and complete_blocks(f"e3r{k}") >= INTERIM_BLOCKS:
                    interim_done = True
                    ok, _ = gates(f"e3r{k}", url, interim=True)
                    if not ok:
                        halt = True
                        STOP.parent.mkdir(parents=True, exist_ok=True)
                        STOP.write_text("interim gate check failed\n")
                        log(f"interim gates failed: wrote {STOP}; the blocks in flight finish, then e3 stops")
                if not halt and gpu_h() >= a.cap:
                    halt = True
                    STOP.parent.mkdir(parents=True, exist_ok=True)
                    STOP.write_text(f"cap {a.cap} GPU-h reached\n")
                    log(f"{gpu_h():.1f} GPU-h reached the cap: wrote {STOP}")
            log(f"replicate e3r{k}: epic_round rc={child['p'].returncode}")
            child["p"] = None
            last_cost = len(GPUS) * (time.time() - t0) / 3600 + (a.base_gpu_h if k == a.first_replicate else 0.0)
            log(f"replicate e3r{k} took {last_cost:.1f} GPU-h; complete blocks {complete_blocks(f'e3r{k}')}")
            ok, _ = gates(f"e3r{k}", url, interim=False)
            if halt or not ok:
                log(f"e3 stops after replicate e3r{k} ({'halt' if halt else 'gates'})")
                break
    finally:
        stop()
    log(f"stage e3 done; about {gpu_h():.1f} GPU-h used by e3")


if __name__ == "__main__":
    main()
