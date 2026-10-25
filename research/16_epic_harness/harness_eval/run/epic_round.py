#!/usr/bin/env python3
"""EPIC round runner (plan "EPIC round"): all arms of one model on one EPIC server, as paired blocks.

    S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker S15_KEEP_IMAGES=1 \
      .venv/bin/python run/epic_round.py --server minimax-m2.7-epic --gpus 0,1,2,3 \
        --arms prefix,prefix_aa,shift,epic128,epic128a1 --bench swe --phase e1 --replicates 3 --parallel 2

- A block is one task in one replicate. Replicate r is the phase "<phase>r<r>" (k = 1), and its block key (the
  seed key of the arms' common random numbers) is that phase name plus the task.
- Each replicate is one co-run batch over all arms (`batch.py --model S+arm0 --with S+arm1=URL ...`): every chunk
  starts all arms of its blocks together, in an order rotated from chunk to chunk. `--parallel` replicate batches
  run at once.
- `--both-ends` adds a `--reverse` twin to every replicate batch: the twin takes the replicate's chunks last to
  first, and batch.py's per-job locks keep the two from running a chunk twice. The forward batches are queued
  first, then the twins, so with `--parallel 4` four batches stay busy until the end (with one-task chunks the
  server is not left idle while a chunk's slowest arm finishes, as in pilot ep1).
- Reruns: blocks in which an arm has no valid trial (infrastructure failure) are run again for that arm under the
  same block key (phase "<phase>r<r>b<i>x<n>" for task i, `--seed-key <phase>r<r>`), for up to `--rerun-rounds`
  rounds. (Before 2026-10-13 the rerun phase was "<phase>r<r>x<n>" for every task, which made the reruns of one round
  share job names, so only one task per round would have run; e1 needed no reruns.)
- `--per-block` runs every block still missing an arm as its own batch (phase "<phase>r<r>b<i>", one task, the
  replicate's seed key), `--parallel` blocks at once. Each block's arms still start together, but no block waits for
  another block's slowest arm, which one-task chunks in a replicate batch do (Terminal-Bench in e2).
- Admission (e4): `--max-sessions N` starts a queued batch only while the agent sessions still running in the
  started batches (arms whose trial has not finished its agent phase) plus the batch's own arms stay within N, so
  a block's slow tail overlaps the next block; `--pool-guard F` also holds new batches while the server's host pool
  is at least F full. Reruns run `--rerun-parallel` at once (default max(--parallel, 4)).
- The server is adopted if it is running, otherwise started on `--gpus`. It is stopped at the end unless
  `--keep-server` is given, also when this script is terminated. `--instance X` uses the server instance
  "<server>@X" (one-GPU models: one instance per GPU, each running its own blocks).
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
sys.path.insert(0, str(ROOT / "run"))
sys.path.insert(0, str(ROOT / "report"))
import serve  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def tasks_of(bench: str, spec: str) -> list[str]:
    import batch
    return batch.task_list(bench, spec)


def batch_cmd(a, url: str, arms: list[str], phase: str, tasks: list[str], seed_key: str = "",
              reverse: bool = False, rotate_offset: int = 0) -> list[str]:
    cmd = [PY, str(ROOT / "run" / "batch.py"), "--model", f"{a.server}+{arms[0]}", "--url", url]
    for arm in arms[1:]:
        cmd += ["--with", f"{a.server}+{arm}={url}"]
    cmd += ["--bench", a.bench, "--phase", phase, "--tasks", ",".join(tasks), "--k", "1",
            "--concurrency", str(a.concurrency), "--chunk", str(a.chunk), "--stagger", str(a.stagger), "--rotate"]
    if seed_key:
        cmd += ["--seed-key", seed_key]
    if a.agent_timeout_multiplier:
        cmd += ["--agent-timeout-multiplier", str(a.agent_timeout_multiplier)]
    if reverse:
        cmd.append("--reverse")
    if rotate_offset:
        cmd += ["--rotate-offset", str(rotate_offset)]
    return cmd


def cmd_meta(cmd: list[str]) -> dict:
    """server, bench, phase and arms of a batch.py command (as built by batch_cmd)."""
    def opt(name: str) -> str:
        return cmd[cmd.index(name) + 1] if name in cmd else ""
    models = [opt("--model")] + [cmd[i + 1].split("=", 1)[0] for i, c in enumerate(cmd) if c == "--with"]
    server = models[0].split("+", 1)[0]
    return {"server": server, "bench": opt("--bench"), "phase": opt("--phase"),
            "arms": [m.split("+", 1)[1] for m in models if "+" in m]}


def agent_done(trial_result: Path) -> bool:
    """A trial no longer loads the server: its agent phase (or the whole trial) has finished."""
    try:
        res = json.loads(trial_result.read_text())
    except (OSError, ValueError):
        return False
    return bool((res.get("agent_execution") or {}).get("finished_at") or res.get("finished_at"))


def sessions_in_flight(metas: list[dict]) -> int:
    """Agent sessions of started batches whose trial has not finished its agent phase (one trial per arm and task;
    a trial not started yet counts as in flight, since its batch starts all arms within seconds)."""
    n = 0
    for m in metas:
        for arm in m["arms"]:
            done = [f for f in glob.glob(str(ROOT / "jobs" / f"{m['server']}+{arm}" / m["bench"] / f"{m['phase']}__*"
                                             / "*__*" / "result.json")) if agent_done(Path(f))]
            n += max(0, int(m.get("tasks", 1)) - len(done))
    return n


def pool_share(url: str) -> float | None:
    try:
        st = json.loads(urllib.request.urlopen(f"{url}/epic/stats", timeout=30).read())
        return float(st["pool_used"]) / float(st["pool_rows"])
    except Exception:                                              # noqa: BLE001
        return None


def admission(max_sessions: int, pool_guard: float, url: str):
    """The admission rule of run_all: None (no limit beyond --parallel) or f(running metas, next meta) -> bool."""
    if not max_sessions and not pool_guard:
        return None

    def admit(running: list[dict], nxt: dict) -> bool:
        if max_sessions and sessions_in_flight(running) + len(nxt["arms"]) * int(nxt.get("tasks", 1)) > max_sessions:
            return False
        if pool_guard:
            share = pool_share(url)
            if share is not None and share >= pool_guard:
                return False
        return True
    return admit


def run_all(cmds: list[list[str]], parallel: int, admit=None, poll: float = 15.0) -> None:
    """Runs batch commands, at most `parallel` at once; with `admit`, a queued batch also waits until
    admit(<metas of the running batches>, <its meta>) holds (or nothing runs)."""
    running: list[tuple[subprocess.Popen, dict]] = []
    queue = list(cmds)
    while queue or running:
        while queue and len(running) < parallel:
            meta = cmd_meta(queue[0])
            meta["tasks"] = len(queue[0][queue[0].index("--tasks") + 1].split(",")) if "--tasks" in queue[0] else 1
            if running and admit is not None and not admit([m for _p, m in running], meta):
                break
            cmd = queue.pop(0)
            log("batch " + " ".join(c if len(c) < 80 else c[:77] + "..." for c in cmd[2:]))
            running.append((subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT), meta))
        time.sleep(poll)
        for p, m in [x for x in running if x[0].poll() is not None]:
            log(f"batch rc={p.returncode}")
            running.remove((p, m))


def block_of(phase: str) -> str:
    """The block's replicate phase: "<p>r<r>", "<p>r<r>b<i>" (per-block run) and "<p>r<r>[b<i>]x<n>" (rerun) map to
    "<p>r<r>"."""
    return re.sub(r"(b\d+)?(x\d+)?$", "", phase)


def missing_blocks(a, arms: list[str], replicates: list[str], tasks: list[str]) -> dict[tuple[str, str], list[str]]:
    """(block phase, task) -> arms without a valid trial; reruns count toward their block phase."""
    import collect
    have: set[tuple[str, str, str]] = set()
    for r in collect.load_trials():
        model = r["model"]
        if not model.startswith(a.server + "+") or r["bench"] != a.bench:
            continue
        if r.get("infra") or not r.get("finished"):
            continue
        block = block_of(r["phase"])
        have.add((model.split("+", 1)[1], block, r["task"]))
    out: dict[tuple[str, str], list[str]] = {}
    for ph in replicates:
        for t in tasks:
            miss = [arm for arm in arms if (arm, ph, t) not in have]
            if miss:
                out[(ph, t)] = miss
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--gpus", default="0,1,2,3")
    ap.add_argument("--arms", required=True)
    ap.add_argument("--bench", default="swe")
    ap.add_argument("--tasks", default="first:30")
    ap.add_argument("--phase", required=True)
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--first-replicate", type=int, default=0)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--chunk", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--stagger", type=float, default=2.0)
    ap.add_argument("--rerun-rounds", type=int, default=2)
    ap.add_argument("--both-ends", action="store_true")
    ap.add_argument("--per-block", action="store_true")
    ap.add_argument("--instance", default="")
    ap.add_argument("--agent-timeout-multiplier", type=float, default=0)
    ap.add_argument("--keep-server", action="store_true")
    ap.add_argument("--max-sessions", type=int, default=0, help="agent sessions in flight (0: only --parallel)")
    ap.add_argument("--pool-guard", type=float, default=0.0, help="hold new batches at this host-pool share")
    ap.add_argument("--rerun-parallel", type=int, default=0, help="reruns at once (default max(--parallel, 4))")
    a = ap.parse_args()
    arms = a.arms.split(",")
    tasks = tasks_of(a.bench, a.tasks)
    # block names use the task's index in the whole subset (or in the --tasks list file), so every run names a block
    # the same way
    order = tasks if a.tasks.startswith("list:") else tasks_of(a.bench, "profile")

    def bidx(t: str) -> int:
        return order.index(t) if t in order else 90 + tasks.index(t)
    reps = [f"{a.phase}r{r}" for r in range(a.first_replicate, a.first_replicate + a.replicates)]
    key = f"{a.server}@{a.instance}" if a.instance else a.server
    state_file = serve.STATE / f"{key}.json"
    started_here = False

    def stop(*_args):
        if not a.keep_server and state_file.exists():
            log(f"stopping {key}: {serve.stop(key)}")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    if state_file.exists():
        url = json.loads(state_file.read_text())["url"]
        log(f"adopting {key} on {url}")
    else:
        gpus = [int(g) for g in a.gpus.split(",")]
        while not serve.gpus_empty(gpus):
            time.sleep(60)
        state = serve.start(a.server, gpus, instance=a.instance)
        url, started_here = state["url"], True
        log(f"{key} ready on {url} after {state.get('ready_s')}s")
    try:
        log(f"round {a.phase}: {len(arms)} arms x {len(tasks)} tasks x {len(reps)} replicates on {a.bench}")
        if a.per_block:
            miss = missing_blocks(a, arms, reps, tasks)
            log(f"per-block: {len(miss)} blocks missing an arm, {a.parallel} at once")
            # (e2 ran without the offset: every per-block batch started its arms in the same order)
            cmds = [batch_cmd(a, url, arms_missing, f"{ph}b{bidx(t):02d}", [t], seed_key=ph, rotate_offset=bidx(t))
                    for (ph, t), arms_missing in sorted(miss.items(), key=lambda kv: (kv[0][0], bidx(kv[0][1])))]
        else:
            cmds = [batch_cmd(a, url, arms, ph, tasks) for ph in reps]
            if a.both_ends:
                cmds += [batch_cmd(a, url, arms, ph, tasks, reverse=True) for ph in reps]
        run_all(cmds, a.parallel, admission(a.max_sessions, a.pool_guard, url))
        for n in range(1, a.rerun_rounds + 1):
            miss = missing_blocks(a, arms, reps, tasks)
            if not miss:
                log("every block has a valid trial in every arm")
                break
            log(f"rerun round {n}: {sum(len(v) for v in miss.values())} trials in {len(miss)} blocks")
            cmds = []
            for (ph, t), arms_missing in sorted(miss.items()):
                cmds.append(batch_cmd(a, url, arms_missing, f"{ph}b{bidx(t):02d}x{n}", [t], seed_key=ph))
            run_all(cmds, a.rerun_parallel or max(a.parallel, 4), admission(a.max_sessions, a.pool_guard, url))
        left = missing_blocks(a, arms, reps, tasks)
        log(f"round {a.phase} done; blocks still missing an arm: {len(left)}")
    finally:
        if not a.keep_server and (started_here or state_file.exists()):
            log(f"stopping {key}: {serve.stop(key)}")


if __name__ == "__main__":
    main()
