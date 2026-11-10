#!/usr/bin/env python3
"""One topic 22 run: MiniMax-M2.7 on GPUs 0-3 serving N concurrent s15 agents over a frozen task queue, in one
condition.
  none  s15 with pacing off: no `wait` tool, no [server] line, no paragraph;
  wait  the final KV-threshold prompt (topic 19): wait text v5, paragraph wait_kv, the kv_cue line at 80 %.

`run` is one GPU stage: topic 20's rd_serve.run_stage (empty GPUs, the lab-sheet claim for every 30-min slot, the cap,
start, health, client, stop, GPU-hour log), pointed at topic 22's log, server folders and 75 GPU-h cap. The server
(`bench`) is harness_eval's MiniMax (serve/models.yaml) plus --max-num-seqs 64 (16 would queue N=32 at the server), topic
19's append-only template and topic 18's status middleware (stock /wt/status: kv_usage, waiting; every read logged to
<run>/wt_ext.jsonl). It listens on a fixed port, so a stopped run resumes with the same agent settings.
`client` (inside the stage) starts topic 11's 1 Hz /metrics scrape and the Harbor job
    harbor run -p data/queue/<group> -e docker -a wb_agent:WBAgent -n N+max(4,N/2) --n-concurrent-agents N -k 1
        --agent-timeout-multiplier 2 --ak base_url=<url> ... --ak env_file=data/env/<cond>.env
and writes data/runs/<run>/cell.json. Harbor skips the trials it already finished when the job is run again.

    python research/22_wait_bench/wb_cell.py run --n 32 --cond wait --hours 2.5
    python research/22_wait_bench/wb_cell.py run --n 4 --cond wait --group smoke --hours 1.0 --name smoke_wait
    python research/22_wait_bench/wb_cell.py hours
    python research/22_wait_bench/wb_cell.py freeze     # prereg.sha256, after the smoke run

A run of the main queue refuses (exit 4) unless prereg.sha256 exists and every file it lists is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import rd_rounds as rr  # noqa: E402
import rd_serve as rs_  # noqa: E402
import wb_tasks as wt  # noqa: E402

PY = "/data/fritz/venv/bin/python"
DATA = DIR / "data"
RUNS = DATA / "runs"
ENV = DATA / "env"
JOBS = DATA / "jobs"
CAP_GPU_H = float("inf")    # no cap since 2026-11-01 16:40 UTC (the user); 75 before
# since 2026-11-01 16:47 UTC (the user): everything on socket 0's cores 0-51 with NUMA node 0's memory, next to GPUs
# 0-3; the containers (wb_eval.sh, docker-in-docker) also at most 100 GB each. vLLM's memory: wb_vllm.sh
SERVER_CPUS = HOST_CPUS = "0-51"
PORT = 50222
CONDS = {"none": {},
         "wait": {"S15_WAIT_TOOL": "1", "S15_WAIT_TEXT": "v5", "S15_SERVER_SECTION": "wait_kv", "S15_STATUS_LINE": "1",
                  "S15_STATUS_FORMAT": "kv_cue", "S15_WAIT_KV": "0.8"}}
AGENT = {"max_lead_calls": 250, "window_tokens": 131072, "max_tokens_reserve": 32000, "max_tokens": 32000,
         "max_tokens_escalated": 64000}            # harness_eval serve/models.yaml, minimax-m2.7
BENCH_ARGS = ["--max-model-len", "131072", "--trust-remote-code", "--enable-auto-tool-choice",
              "--tool-call-parser", "minimax_m2", "--reasoning-parser", "minimax_m2_append_think",
              "--tensor-parallel-size", "4", "--gpu-memory-utilization", "0.92", "--max-num-seqs", "64",
              "--enable-prefix-caching", "--enable-prompt-tokens-details",
              "--chat-template", str(rr.T19_TEMPLATE), "--middleware", "wt_server_ext.Middleware"]


PREREG = DIR / "prereg.md"
PREREG_SHA = DIR / "prereg.sha256"
HE = REPO / "research" / "16_epic_harness" / "harness_eval"


def frozen_files() -> list[Path]:
    """What the main runs depend on: the pre-registration, topic 22's code, the pinned s15, the task list and queue,
    and the shared files the server, the harness and the analysis use."""
    files = [PREREG, *sorted(DIR.glob("wb_*.py")), DIR / "wb_eval.sh", DIR / "wb_vendor.sh", DIR / "wb_vllm.sh",
             DIR / "test_wb.py",
             DATA / "tasks.json", *sorted(p for p in (DIR / "vendor" / "s15").rglob("*") if p.is_file()),
             REPO / "research" / "20_round_dependency" / "rd_serve.py",
             REPO / "research" / "18_harness_wait" / "wt_server_ext.py",
             REPO / "research" / "11_multiuser_kv" / "mu_scrape.py", rr.T19_TEMPLATE,
             HE / "adapter" / "harbor_agent.py", HE / "adapter" / "s15_child.py", HE / "adapter" / "rpc.py"]
    return [p for p in files if "__pycache__" not in p.parts]


def queue_manifest() -> str:
    """The main queue's link names and targets, in order (hashed with the files)."""
    q = wt.QUEUE / "main"
    return "\n".join(f"{p.name} -> {os.readlink(p)}" for p in sorted(q.iterdir()))


def sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def freeze() -> None:
    lines = [f"# frozen {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, before the first main run; repo-relative"]
    lines += [f"{sha(p.read_bytes())}  {p.relative_to(REPO)}" for p in frozen_files()]
    lines.append(f"{sha(queue_manifest().encode())}  <queue manifest: data/queue/main>")
    PREREG_SHA.write_text("\n".join(lines) + "\n")
    print(f"[freeze] {len(lines) - 1} entries -> {PREREG_SHA.relative_to(REPO)}")


def changed() -> list[str]:
    if not PREREG_SHA.exists():
        return ["prereg.sha256 (missing: freeze first)"]
    bad = []
    for line in PREREG_SHA.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        want, rel = line.split(maxsplit=1)
        if rel.startswith("<queue manifest"):
            got = sha(queue_manifest().encode())
        else:
            path = REPO / rel
            got = sha(path.read_bytes()) if path.is_file() else None
        if got != want:
            bad.append(rel)
    return bad


def cpu_list(spec: str) -> set[int]:
    """'0-3,8' -> {0, 1, 2, 3, 8}."""
    out = set()
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def pin_tree(root: int, cpus: set[int], log: Path, stop: threading.Event, every: float = 2.0) -> None:
    """Keep every thread of root's descendants on `cpus`, and log each reset. taskset alone does not hold: the host's
    numad daemon (root, `numad -i 15`) rebinds any process over 300 MB and half a CPU to a whole NUMA node's CPU list
    (node 0: 0-51,104-155; node 1: 52-103,156-207) and migrates its memory there. On 2026-11-01 it moved vLLM's
    processes to both nodes (/var/log/numad.log). Only root can exclude a process (`numad -x PID`). This resets the
    CPUs only: memory numad moved stays where it put it."""
    import psutil
    while not stop.wait(every):
        try:
            procs = psutil.Process(root).children(recursive=True)
        except psutil.Error:
            continue
        for proc in procs:
            reset, was = 0, None
            try:
                for tid in os.listdir(f"/proc/{proc.pid}/task"):
                    have = os.sched_getaffinity(int(tid))
                    if have != cpus:
                        was = was or have
                        os.sched_setaffinity(int(tid), cpus)
                        reset += 1
                name = proc.name()
            except (OSError, psutil.Error):
                continue
            if reset:
                with log.open("a") as f:
                    f.write(json.dumps({"t": time.time(), "pid": proc.pid, "name": name, "threads_reset": reset,
                                        "was": sorted(was)[:1] + sorted(was)[-1:], "n_was": len(was)}) + "\n")


def n_trials(n: int) -> int:
    """Harbor's -n: the N agents plus trials setting up their environment or running their verifier."""
    return n + max(4, n // 2)


def fixed_port() -> int:
    """Refuse only a live listener: SO_REUSEADDR, as vLLM binds, so the last server's TIME-WAIT sockets pass."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", PORT))
        except OSError:
            raise SystemExit(f"port {PORT} is busy")
    return PORT


STOP_GRACE_S = 180.0        # the client's stop: docker stop -t 120 (Harbor cancels its trials), then the cleanup
DIND = ["docker", "-H", "unix:///data/fritz/dind-run/docker.sock"]


def configure(run_dir: Path) -> None:
    """Point topic 20's stage runner at topic 22's log, folders, cap and server (its functions read these at call
    time); the middleware's module and audit log go to the server through the environment. rd_serve gives a stopped
    client 30 s before SIGKILL; the client here needs up to STOP_GRACE_S to stop Harbor and clean up."""
    rs_.HOURS = DATA / "gpu_hours.jsonl"
    rs_.SERVERS = DATA / "servers"
    rs_.CAP_GPU_H = CAP_GPU_H
    rs_.CONFIGS["bench"] = BENCH_ARGS
    rs_.SERVER_CPUS, rs_.CLIENT_CPUS, rs_.VLLM = SERVER_CPUS, HOST_CPUS, str(DIR / "wb_vllm.sh")
    rs_.free_port = fixed_port
    stop_group = rs_.stop_group
    rs_.stop_group = lambda proc, grace=60.0: stop_group(proc, max(grace, STOP_GRACE_S))
    os.environ["PYTHONPATH"] = ":".join(p for p in (str(REPO / "research" / "18_harness_wait"),
                                                    os.environ.get("PYTHONPATH", "")) if p)
    os.environ["WT_EXT_LOG"] = str(run_dir / "wt_ext.jsonl")


def harbor_cmd(name: str, group: str, n: int, cond: str, url: str) -> list[str]:
    env_file = ENV / f"{cond}.env"
    cmd = [str(DIR / "wb_eval.sh"), "harbor", "run", "-p", str((wt.QUEUE / group).relative_to(DIR)), "-e", "docker",
           "-a", "wb_agent:WBAgent", "-m", rs_.MODEL, "-o", str(JOBS.relative_to(DIR)), "--job-name", name,
           "-n", str(n_trials(n)), "--n-concurrent-agents", str(n), "-k", "1", "-q", "-y",
           "--agent-timeout-multiplier", "2"]
    for k, v in {"base_url": url, "served_model": rs_.MODEL, "bench": "swe", "env_file": str(env_file), **AGENT}.items():
        cmd += ["--ak", f"{k}={v}"]
    return cmd


def cleanup_dind() -> int:
    """Remove every task container and Harbor network left in the docker-in-docker daemon (only one run uses it at a
    time, and Harbor cannot clean up trials it was stopped in the middle of)."""
    ids = subprocess.run([*DIND, "ps", "-aq"], capture_output=True, text=True).stdout.split()
    if ids:
        subprocess.run([*DIND, "rm", "-f", *ids], capture_output=True)
    nets = [n for n in subprocess.run([*DIND, "network", "ls", "--format", "{{.Name}}"], capture_output=True,
                                      text=True).stdout.split() if "__env" in n]
    if nets:
        subprocess.run([*DIND, "network", "rm", *nets], capture_output=True)
    return len(ids)


def drop_cancelled(name: str) -> list[str]:
    """Trials a stop cancelled (CancelledError) count as done when Harbor resumes a job; removing their folders makes
    it run those tasks again. The removed trials are logged in <run>/cancelled.jsonl."""
    import shutil
    out = []
    for res in sorted((JOBS / name).glob("*/result.json")):
        try:
            r = json.loads(res.read_text())
        except (OSError, ValueError):
            continue
        if (r.get("exception_info") or {}).get("exception_type") == "CancelledError":
            shutil.rmtree(res.parent)
            out.append(res.parent.name)
    if out:
        with (RUNS / name / "cancelled.jsonl").open("a") as f:
            f.write(json.dumps({"t": time.time(), "removed": out}) + "\n")
    return out


def client(name: str, group: str, n: int, cond: str, url: str) -> int:
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    ENV.mkdir(parents=True, exist_ok=True)
    (ENV / f"{cond}.env").write_text("".join(f"{k}={v}\n" for k, v in CONDS[cond].items()) or "# pacing off\n")
    tasks = wt.queue_names(group)
    info = {"name": name, "n": n, "cond": cond, "group": group, "tasks": len(tasks), "n_trials": n_trials(n), "url": url,
            "env": CONDS[cond], "agent": AGENT, "server_args": BENCH_ARGS, "t_client": time.time()}
    with (run_dir / "cell.jsonl").open("a") as f:      # the start line; the end line follows when Harbor exits
        f.write(json.dumps(info) + "\n")
    left = cleanup_dind()
    if left:
        print(f"[wb] {name}: removed {left} containers left in docker-in-docker", flush=True)
    rerun = drop_cancelled(name)
    if rerun:
        print(f"[wb] {name}: {len(rerun)} cancelled trials removed, so they run again: {rerun}", flush=True)
    scrape = subprocess.Popen([PY, str(REPO / "research" / "11_multiuser_kv" / "mu_scrape.py"), "run",
                               "--metrics-url", f"{url}/metrics", "--gpu", "0,1,2,3",
                               "--out", str(run_dir / f"scrape_{int(time.time())}.jsonl"), "--period", "1"],
                              stdout=open(run_dir / "scrape.log", "a"), stderr=subprocess.STDOUT)
    env = dict(os.environ, WB_EVAL_NAME=f"wbeval-{name}")
    print(f"[wb] {name}: {len(tasks)} tasks, N={n}, -n {n_trials(n)}, {cond}", flush=True)
    harbor = subprocess.Popen(harbor_cmd(name, group, n, cond, url), stdout=open(run_dir / "harbor.txt", "a"),
                              stderr=subprocess.STDOUT, env=env)

    def stop(signum, frame):
        # killpg reaches the docker CLI too, but killing it would leave the container running: stop the container,
        # which lets Harbor cancel its trials for up to 120 s
        print(f"[wb] {name}: signal {signum}; stopping the Harbor job (resumable)", flush=True)
        subprocess.run(["docker", "stop", "-t", "120", f"wbeval-{name}"], capture_output=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        rc = harbor.wait()
    finally:
        if harbor.poll() is None:
            subprocess.run(["docker", "stop", "-t", "120", f"wbeval-{name}"], capture_output=True)
            harbor.wait()
        scrape.send_signal(signal.SIGTERM)
        try:
            scrape.wait(timeout=30)
        except subprocess.TimeoutExpired:
            scrape.kill()
        left = cleanup_dind()
        if left:
            print(f"[wb] {name}: removed {left} containers left in docker-in-docker", flush=True)
    info.update(t_end=time.time(), rc=rc)
    with (run_dir / "cell.jsonl").open("a") as f:
        f.write(json.dumps(info) + "\n")
    print(f"[wb] {name}: Harbor exit {rc}", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for p in (sub.add_parser("run"), sub.add_parser("client")):
        p.add_argument("--n", type=int, required=True)
        p.add_argument("--cond", choices=sorted(CONDS), required=True)
        p.add_argument("--group", default="main", choices=("main", "smoke"))
        p.add_argument("--name", default=None, help="default: n<N>_<cond>")
    sub.choices["run"].add_argument("--hours", type=float, required=True)
    sub.choices["run"].add_argument("--no-claim-check", action="store_true",
                                    help="skip the sheet check (tests; the main runs since 2026-11-01 16:40 UTC, when "
                                         "the user took over the claims)")
    sub.choices["client"].add_argument("--url", required=True)
    sub.add_parser("hours")
    sub.add_parser("freeze")
    sub.add_parser("check")
    a = ap.parse_args()
    if a.cmd == "freeze":
        freeze()
        return
    if a.cmd == "check":
        bad = changed()
        print("all unchanged" if not bad else f"changed: {bad}")
        sys.exit(1 if bad else 0)
    if a.cmd == "hours":
        configure(RUNS / "_")
        for line in rs_.HOURS.open() if rs_.HOURS.exists() else []:
            print(line.rstrip())
        print(f"tally {rs_.tally():.2f} / {CAP_GPU_H} GPU-h")
        return
    name = a.name or f"n{a.n}_{a.cond}"
    if a.cmd == "client":
        sys.exit(client(name, a.group, a.n, a.cond, a.url))
    if a.group == "main":
        bad = changed()
        if bad:
            print(f"[wb] refused: frozen files changed or missing: {bad}", flush=True)
            sys.exit(4)
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    configure(run_dir)
    cmd = [PY, str(Path(__file__).resolve()), "client", "--n", str(a.n), "--cond", a.cond, "--group", a.group,
           "--name", name, "--url", "{url}"]
    stop = threading.Event()                     # the server, the client and the scrape stay on SERVER_CPUS (0-51)
    threading.Thread(target=pin_tree, args=(os.getpid(), cpu_list(SERVER_CPUS), run_dir / "pin.jsonl", stop),
                     daemon=True).start()
    rc = rs_.run_stage(name, a.hours, "bench", cmd, not a.no_claim_check)
    stop.set()
    sys.exit(rc)


if __name__ == "__main__":
    main()
