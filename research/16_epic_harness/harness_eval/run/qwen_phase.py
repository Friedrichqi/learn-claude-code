#!/usr/bin/env python3
"""The 1-GPU phase, after the 4-GPU models: Qwen3.8-27B (hybrid attention, thinking) and Qwen3-32B (legacy) on
GPUs 0-3, in Docker-in-Docker mode (run/dind.md: task containers in our own daemon with its data root on /data, so
disk no longer limits how many chunks run at once).

    S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker S15_KEEP_IMAGES=1 \
      nohup .venv/bin/python run/qwen_phase.py >> runs/qwen_phase.log 2>&1 &

Qwen3.8 thinks and is several times slower per trial, so it gets three GPUs (one benchmark each) and Qwen3-32B
one (all three benchmarks plus the pilot's remaining SWE-bench chunks at 150 lead calls). Each lane plans the
missing trials of one model and benchmark (run/plan_p4.py: every task of the first 30 reaches 3 valid trials),
runs them, then reruns trials lost to infrastructure, for up to 1 + RERUN_ROUNDS rounds. A server is stopped as
soon as no lane needs it, and every server this script started is stopped when it exits or is terminated.
(The earlier co-run variant, both models on the same chunk to share images on the small disk, is kept in
run/state/qwen_phase.py.corun.)
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
sys.path.insert(0, str(ROOT / "run"))
sys.path.insert(0, str(ROOT / "report"))
import serve  # noqa: E402
import plan_p4  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
GPUS = [0, 1, 2, 3]
SERVERS = {"qwen3.8-27b@g0": 0, "qwen3.8-27b@g1": 1, "qwen3.8-27b@g2": 2, "qwen3-32b@g3": 3}
CHUNK = {"swe": 6, "swepro": 4, "tbench": 4}     # larger than batch.py's disk-sized defaults: no disk limit here
N = 30
K = 3
RERUN_ROUNDS = 2
STOP = ROOT / "run" / "state" / "stop"
# left by earlier stages to drain the 1-GPU servers before the 4-GPU window; batch.py honours them
STALE_STOPS = ["qwen3-32b", "qwen3.8-27b", "qwen3-32b@g2", "qwen3-32b@g3", "qwen3.8-27b@g0", "qwen3.8-27b@g1"]

urls: dict[str, str] = {}
users: dict[str, int] = {}
lock = threading.Lock()


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def model(key: str) -> str:
    return key.split("@")[0]


def stopped(*models: str) -> bool:
    return (STOP / "all").exists() or any((STOP / m).exists() for m in models)


def release(key: str) -> None:
    """A lane no longer needs this server: stop it when no lane does."""
    with lock:
        users[key] -= 1
        last = users[key] == 0
    if last:
        log(f"stopping {key}: {serve.stop(key)}")


def next_phase(models: list[str], bench: str) -> str:
    n = 4
    while any(list((ROOT / "jobs" / m / bench).glob(f"q{n}k*")) for m in models):
        n += 1
    return f"q{n}"


def missing(m: str, bench: str) -> dict[str, int]:
    subsets = json.loads((ROOT / "run" / "subsets.json").read_text())
    have = plan_p4.valid_trials(m, bench)
    return {t: K - have.get(t, 0) for t in subsets[bench]["tasks"][:N] if K - have.get(t, 0) > 0}


def batch(key: str, bench: str, phase: str, tasks: str, k: int, conc: int, co: str = "", chunk: int = 0) -> None:
    cmd = [PY, str(ROOT / "run" / "batch.py"), "--model", model(key), "--url", urls[key]]
    if co:
        cmd += ["--with", f"{model(co)}={urls[co]}"]
    cmd += ["--bench", bench, "--phase", phase, "--tasks", tasks, "--k", str(k), "--concurrency", str(conc)]
    if chunk:
        cmd += ["--chunk", str(chunk)]
    who = f"{key} + {co}" if co else key
    log(f"batch {bench} {phase} k={k} on {who}: {len(tasks.split(',')) if ',' in tasks else tasks}")
    rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT).returncode
    log(f"batch {bench} {phase} on {who} rc={rc}")


def reruns(key: str, bench: str, conc: int, rounds: int) -> None:
    """Plan and run whatever is still missing for this model and benchmark, round by round."""
    m = model(key)
    for r in range(rounds):
        if stopped(m):
            log(f"{key} {bench}: stop file present")
            return
        need = missing(m, bench)
        if not need:
            log(f"{key} {bench}: every task has its {K} trials")
            return
        phase = next_phase([m], bench)
        groups: dict[int, list[str]] = {}
        for t, k in need.items():
            groups.setdefault(k, []).append(t)
        for k, ids in sorted(groups.items(), reverse=True):
            batch(key, bench, f"{phase}k{k}", ",".join(ids), k, conc, chunk=CHUNK.get(bench, 0))


def corun(bench: str, primary: str, co: str, conc: int) -> None:
    mp, mc = model(primary), model(co)
    if stopped(mp, mc):
        return
    need_p, need_c = missing(mp, bench), missing(mc, bench)
    both = [t for t, k in need_p.items() if k == K and need_c.get(t) == K]
    if both:
        batch(primary, bench, f"{next_phase([mp, mc], bench)}k{K}", ",".join(both), K, conc, co=co)


def pilot(key: str) -> None:
    """The pilot (Qwen3-32B on SWE-bench Verified Mini, 1 trial at 150 lead calls, as in X2): chunks c10-c15 of
    p1b, the first three tasks (p1c), then one more trial for every pilot task without a valid one (p1d)."""
    m = model(key)
    for phase, tasks in (("p1b", "range:3:50"), ("p1c", "first:3")):
        if stopped(m):
            return
        batch(key, "swe", phase, tasks, 1, 3)
    import collect  # noqa: E402
    subsets = json.loads((ROOT / "run" / "subsets.json").read_text())
    valid = {r["task"] for r in collect.load_trials() if r["model"] == m and r["bench"] == "swe"
             and r["phase"].startswith("p1") and not r.get("infra") and r.get("finished")}
    redo = [t for t in subsets["swe"]["tasks"][:50] if t not in valid]
    if redo and not stopped(m):
        batch(key, "swe", "p1d", ",".join(redo), 1, 3)


class Lane(threading.Thread):
    """A sequence of steps on some servers. drop(key) gives a server back as soon as the lane is done with it;
    whatever the lane still holds is given back when it ends, normally or not."""

    def __init__(self, name: str, servers: list[str], steps) -> None:
        super().__init__(name=name, daemon=True)
        self.held = list(servers)
        self.steps = steps

    def drop(self, key: str) -> None:
        if key in self.held:
            self.held.remove(key)
            release(key)

    def run(self) -> None:
        try:
            for step in self.steps(self):
                if stopped("qwen3.8-27b", "qwen3-32b"):
                    log(f"lane {self.name}: stop file present")
                    break
                step()
        except Exception as exc:                        # noqa: BLE001
            log(f"lane {self.name} failed: {exc!r}")
        finally:
            for key in list(self.held):
                self.drop(key)
            log(f"lane {self.name} done")


def start(key: str) -> None:
    try:
        state = serve.start(model(key), [SERVERS[key]], instance=key.split("@")[1])
        urls[key] = state["url"]
        log(f"server {key} ready on {state['url']} after {state.get('ready_s')}s")
    except SystemExit as exc:
        log(f"server {key} did not start: {str(exc)[:300]}")


def stop_all(*_args) -> None:
    for key in SERVERS:
        if (serve.STATE / f"{key}.json").exists():
            log(f"stopping {key}: {serve.stop(key)}")
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, stop_all)
    log("qwen phase: waiting for GPUs 0-3 to be empty")
    while not serve.gpus_empty(GPUS):
        time.sleep(120)
    for name in STALE_STOPS:
        (STOP / name).unlink(missing_ok=True)
    if stopped("qwen3.8-27b", "qwen3-32b"):
        log("stop file present; not starting")
        return
    try:
        threads = [threading.Thread(target=start, args=(key,)) for key in SERVERS]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        if len(urls) < len(SERVERS):
            log(f"only {sorted(urls)} started; stopping")
            return
        q38 = ["qwen3.8-27b@g0", "qwen3.8-27b@g1", "qwen3.8-27b@g2"]
        lanes = [
            Lane("swe38", [q38[0]], lambda ln: [lambda: reruns(q38[0], "swe", 10, 1 + RERUN_ROUNDS)]),
            Lane("pro38", [q38[1]], lambda ln: [lambda: reruns(q38[1], "swepro", 8, 1 + RERUN_ROUNDS)]),
            Lane("tb38", [q38[2]], lambda ln: [lambda: reruns(q38[2], "tbench", 10, 1 + RERUN_ROUNDS)]),
            Lane("swe32", ["qwen3-32b@g3"], lambda ln: [lambda: reruns("qwen3-32b@g3", "swe", 3, 1 + RERUN_ROUNDS)]),
            Lane("pro32", ["qwen3-32b@g3"], lambda ln: [lambda: reruns("qwen3-32b@g3", "swepro", 3, 1 + RERUN_ROUNDS)]),
            Lane("tb32", ["qwen3-32b@g3"], lambda ln: [lambda: reruns("qwen3-32b@g3", "tbench", 3, 1 + RERUN_ROUNDS)]),
            Lane("pilot", ["qwen3-32b@g3"], lambda ln: [lambda: pilot("qwen3-32b@g3")]),
        ]
        for ln in lanes:
            for key in ln.held:
                users[key] = users.get(key, 0) + 1
        for ln in lanes:
            ln.start()
        while any(ln.is_alive() for ln in lanes):
            time.sleep(10)
    finally:
        for key in SERVERS:
            if (serve.STATE / f"{key}.json").exists():
                log(f"stopping {key}: {serve.stop(key)}")
        log(f"qwen phase finished; GPUs {serve.gpu_status(GPUS)}")


if __name__ == "__main__":
    main()
