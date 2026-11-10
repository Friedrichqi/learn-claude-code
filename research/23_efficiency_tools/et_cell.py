#!/usr/bin/env python3
"""One topic 23 run: Qwen3.8-27B-FP8 on one H100 (GPU 4 = slot a, GPU 5 = slot b) serving 32 concurrent s15 agents
over a task queue, in one setup.

  none, none_r2  vanilla s15: no pacing tools, no status line
  gate8, gate16  the gate proxy's session gate with K fixed at 8 / 16
  agate          the session gate, K set once a minute by a controller model call (et_controller.py); starts at 32
  wait           topic 22's final prompt: wait text v5, paragraph wait_kv, the kv_cue line at 80 %
  lmc            wait plus save_context (lead only); LMCache stores only what save_context sends
  lmc_all        LMCache stores every request; no pacing tools
  pilot_none     the pilot on slot a: none for 60 min, then the gate at K = 16, 8 and 4 for 15 min each
  pilot_wait     the pilot on slot b: wait for 60 min

`run` is one GPU stage: topic 20's rd_serve.run_stage (the GPU empty, start, health, client, stop, GPU-hour log),
pointed at this topic's log and folders, its slot's GPU and CPUs, and no cap; the user enters the sheet claims
(2026-11-02), so the stage does not check the sheet. The server is topic 16's Qwen3.8 configuration
(harness_eval serve/models.yaml) with --max-num-seqs 64, the patched template (qwen3_8_append_only.jinja), topic 18's
status middleware and, for lmc / lmc_all, LMCache's multiprocess connector (its server is started by et_vllm.sh in the
server's process group). `client` (inside the stage) starts the gate proxy (et_proxy.py), the hardware monitor
(et_hw.py) and the Harbor job
    harbor run -p <queue> -e docker -a et_agent:ETAgent -n 48 --n-concurrent-agents 32 -k <1 or 2>
        --agent-timeout-multiplier 8 --ak base_url=<proxy> --ak gate_url=<proxy> --ak limit_mult=2 ...
and writes data/runs/<run>/cell.jsonl. Harbor skips the trials it already finished when the job is run again.

    python research/23_efficiency_tools/et_cell.py run --setup none --slot a --hours 5
    python research/23_efficiency_tools/et_cell.py run --setup pilot_none --slot a --group pilot --hours 2
    python research/23_efficiency_tools/et_cell.py hours
    python research/23_efficiency_tools/et_cell.py freeze     # prereg.sha256, after the smoke runs
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))                              # research/
sys.path.insert(0, str(DIR.parent / "22_wait_bench"))
import _paths  # noqa: F401,E402

import rd_serve as rs_  # noqa: E402
import wb_cell  # noqa: E402

PY = "/data/fritz/venv23/bin/python"
MODEL = "Qwen/Qwen3.8-27B-FP8"
DATA = DIR / "data"
RUNS = DATA / "runs"
ENV = DATA / "env"
JOBS = DATA / "jobs"
QUEUES = {"main": REPO / "research" / "22_wait_bench" / "data" / "queue" / "main", "pilot": DATA / "queue" / "pilot"}
N_AGENTS = 32
L1_GB = 300                                  # LMCache's host-memory tier ("unlimited DRAM", the user, 2026-11-02)
CHUNK = 784                                  # = vLLM's unified block size for this model (S0)
# the user, 2026-11-02: GPUs 4-5 with CPUs on their socket (socket 1: 52-103,156-207; NUMA node 1)
# fixed ports below the kernel's ephemeral range (32768-60999): on 2026-11-02 LMCache's HTTP port 50552 was taken by a
# short-lived socket and the server exited (README Changes)
SLOTS = {
    "a": {"gpu": 4, "server_cpus": "52-57,156-161", "port": 31431, "proxy": 31441, "lmc_zmq": 31451,
          "lmc_http": 31452, "hw": 31461},
    "b": {"gpu": 5, "server_cpus": "58-63,162-167", "port": 31531, "proxy": 31541, "lmc_zmq": 31551,
          "lmc_http": 31552, "hw": 31561},
}
CLIENT_CPUS = "64-71,168-175"                # proxies, monitors, the Harbor eval containers (et_eval.sh)
WAIT_ENV = {"S15_WAIT_TOOL": "1", "S15_WAIT_TEXT": "v5", "S15_SERVER_SECTION": "wait_kv", "S15_STATUS_LINE": "1",
            "S15_STATUS_FORMAT": "kv_cue", "S15_WAIT_KV": "0.8"}
# the S2 screens' winners (data/screen/winners.json, 2026-11-02): save_context = s1's description with the [server]
# line's cue naming it (S5: kv_cue_save, 73 % save-with-wait at 93 % KV in replay, 0 % at 62 %)
SAVE_TEXT, SAVE_LINE = "s1", "kv_cue_save"
# the controller screen (data/screen/winners.json, 2026-11-03): its rule's winner, c4 at the default effort (98 % valid,
# 98.5 % agreement), failed live in the agate smoke run (2 of 8 calls timed out at 300 s, one decision per 3-5 min),
# so it is the next by the same rule: c3 (capacity arithmetic and decision cues) at low effort, 100 % valid, 97.6 %
# agreement, tied with c4 low and ahead by the lower variant number (README Changes, 2026-11-03 01:26 UTC)
CONTROLLER = {"variant": "c3", "effort": "low"}
SETUPS = {
    "none": {}, "none_r2": {},
    "gate8": {"gate_k": 8}, "gate16": {"gate_k": 16},
    "agate": {"gate_k": N_AGENTS, "controller": True},
    "wait": {"env": WAIT_ENV},
    "lmc": {"env": {**WAIT_ENV, "S15_STATUS_FORMAT": SAVE_LINE, "S15_SAVE_TOOL": "1", "S15_SAVE_TEXT": SAVE_TEXT},
            "offload": "flagged"},
    "lmc_all": {"offload": "all"},
    "pilot_none": {"gate_k": N_AGENTS, "k_schedule": "3600:16,4500:8,5400:4"},
    "pilot_wait": {"env": WAIT_ENV},
}
AGENT = {"max_lead_calls": 250, "window_tokens": 131072, "max_tokens_reserve": 32000, "max_tokens": 32000,
         "max_tokens_escalated": 64000,              # harness_eval serve/models.yaml, qwen3.8-27b
         "call_timeout": 14400, "limit_mult": 2}     # the per-call cap at the server is the proxy's (1,800 s)
TEMPLATE = DIR / "qwen3_8_append_only.jinja"
BENCH_ARGS = ["--max-model-len", "131072", "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
              "--reasoning-parser", "qwen3", "--language-model-only", "--tensor-parallel-size", "1",
              "--gpu-memory-utilization", "0.92", "--max-num-seqs", "64", "--enable-prefix-caching",
              "--mamba-cache-mode", "align", "--enable-mamba-shared-prefix-checkpoint",
              "--enable-prompt-tokens-details", "--chat-template", str(TEMPLATE),
              "--middleware", "wt_server_ext.Middleware"]
PREREG = DIR / "prereg.md"
PREREG_SHA = DIR / "prereg.sha256"
STOP_GRACE_S = 180.0


def server_args(setup: str, slot: str) -> list[str]:
    s = SLOTS[slot]
    args = list(BENCH_ARGS)
    if SETUPS[setup].get("offload"):
        kv = {"kv_connector": "LMCacheMPConnector", "kv_role": "kv_both",
              "kv_connector_module_path": "lmcache.integration.vllm.lmcache_mp_connector",
              "kv_connector_extra_config": {"lmcache.mp.host": "tcp://localhost", "lmcache.mp.port": s["lmc_zmq"]}}
        args += ["--kv-transfer-config", json.dumps(kv)]
    return args


def lmcache_args(slot: str) -> str:
    s = SLOTS[slot]
    return (f"--host localhost --port {s['lmc_zmq']} --http-host 127.0.0.1 --http-port {s['lmc_http']} "
            f"--chunk-size {CHUNK} --separate-object-groups --l1-size-gb {L1_GB} --eviction-policy LRU")


def frozen_files() -> list[Path]:
    """What the main runs depend on: the pre-registration, this topic's code, the pinned s15, the template, the task
    lists, and the shared files the server, the harness and the analysis use."""
    files = [PREREG, *sorted(DIR.glob("et_*.py")), *sorted(DIR.glob("et_*.sh")), DIR / "test_et.py", TEMPLATE,
             DATA / "tasks_pilot.json", *sorted(p for p in (DIR / "vendor" / "s15").rglob("*") if p.is_file()),
             REPO / "research" / "22_wait_bench" / "data" / "tasks.json",
             REPO / "research" / "22_wait_bench" / "wb_agent.py", REPO / "research" / "22_wait_bench" / "wb_cap.py",
             REPO / "research" / "22_wait_bench" / "wb_cell.py", REPO / "research" / "22_wait_bench" / "wb_analyze.py",
             REPO / "research" / "20_round_dependency" / "rd_serve.py",
             REPO / "research" / "18_harness_wait" / "wt_server_ext.py",
             REPO / "research" / "11_multiuser_kv" / "mu_scrape.py",
             wb_cell.HE / "adapter" / "harbor_agent.py", wb_cell.HE / "adapter" / "s15_child.py",
             wb_cell.HE / "adapter" / "rpc.py"]
    return [p for p in files if p.is_file() and "__pycache__" not in p.parts]


def queue_manifest() -> str:
    q = QUEUES["main"]
    return "\n".join(f"{p.name} -> {os.readlink(p)}" for p in sorted(q.iterdir()))


def freeze() -> None:
    lines = [f"# frozen {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, before the first main run; repo-relative"]
    lines += [f"{wb_cell.sha(p.read_bytes())}  {p.relative_to(REPO)}" for p in frozen_files()]
    lines.append(f"{wb_cell.sha(queue_manifest().encode())}  <queue manifest: 22_wait_bench/data/queue/main>")
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
        got = (wb_cell.sha(queue_manifest().encode()) if rel.startswith("<queue manifest")
               else (wb_cell.sha((REPO / rel).read_bytes()) if (REPO / rel).is_file() else None))
        if got != want:
            bad.append(rel)
    return bad


def fixed_port(port: int):
    def pick() -> int:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"port {port} is busy")
        return port
    return pick


def configure(setup: str, slot: str, run_dir: Path) -> None:
    """Point topic 20's stage runner at this run: its slot's GPU, CPUs and port, this topic's log and folders, the
    server arguments, no cap. Its functions read these at call time."""
    s = SLOTS[slot]
    rs_.HOURS = DATA / "gpu_hours.jsonl"
    rs_.SERVERS = DATA / "servers"
    rs_.CAP_GPU_H = float("inf")
    rs_.MODEL = MODEL
    rs_.GPUS = [s["gpu"]]
    rs_.CONFIGS["et"] = server_args(setup, slot)
    rs_.SERVER_CPUS, rs_.CLIENT_CPUS, rs_.VLLM = s["server_cpus"], CLIENT_CPUS, str(DIR / "et_vllm.sh")
    rs_.SERVER_ENV = {**rs_.SERVER_ENV, "CUDA_VISIBLE_DEVICES": str(s["gpu"])}
    if SETUPS[setup].get("offload"):
        rs_.SERVER_ENV.update(ET_LMCACHE_ARGS=lmcache_args(slot), ET_LMCACHE_HTTP=str(s["lmc_http"]),
                              ET_LMCACHE_LOG=str(run_dir / f"lmcache_{int(time.time())}.log"))
    rs_.free_port = fixed_port(s["port"])
    stop_group = rs_.stop_group
    rs_.stop_group = lambda proc, grace=60.0: stop_group(proc, max(grace, STOP_GRACE_S))
    os.environ["PYTHONPATH"] = ":".join(p for p in (str(REPO / "research" / "18_harness_wait"),
                                                    os.environ.get("PYTHONPATH", "")) if p)
    os.environ["WT_EXT_LOG"] = str(run_dir / "wt_ext.jsonl")


def pin_roles(root: int, server: set[int], client: set[int], log: Path, stop: threading.Event,
              every: float = 2.0) -> None:
    """wb_cell.pin_tree with two CPU sets: the vLLM and LMCache processes (and their threads) on the slot's server
    CPUs, every other descendant (proxy, monitor, docker CLI) on the client CPUs. The host's numad would otherwise
    move them (topic 22, README Changes)."""
    import psutil
    while not stop.wait(every):
        try:
            procs = psutil.Process(root).children(recursive=True)
        except psutil.Error:
            continue
        for proc in procs:
            try:
                chain = [proc, *proc.parents()]
                role = server if any("vllm" in " ".join(p.cmdline()) or "lmcache" in " ".join(p.cmdline())
                                     for p in chain if p.pid != root) else client
                reset = 0
                for tid in os.listdir(f"/proc/{proc.pid}/task"):
                    if os.sched_getaffinity(int(tid)) != role:
                        os.sched_setaffinity(int(tid), role)
                        reset += 1
                if reset:
                    with log.open("a") as f:
                        f.write(json.dumps({"t": time.time(), "pid": proc.pid, "name": proc.name(),
                                            "threads_reset": reset, "role": "server" if role is server else "client"})
                                + "\n")
            except (OSError, psutil.Error):
                continue


def trial_of(project: str) -> str | None:
    """The trial suffix of a Harbor compose project: <task, shortened>__<trial suffix>__env -> the suffix."""
    if not project.endswith("__env"):
        return None
    return project[:-len("__env")].rsplit("__", 1)[-1]


def cleanup_dind(name: str) -> int:
    """Remove the task containers and Harbor networks this run's trials left in docker-in-docker. Two runs share the
    daemon, so only this job's: Harbor names each trial's compose project <task, shortened>__<trial suffix>__env and
    its network <project>_default, with the suffix of the trial folder (<task>__<suffix>) in lower case."""
    trials = ({p.name.rsplit("__", 1)[-1].lower() for p in (JOBS / name).iterdir() if p.is_dir()}
              if (JOBS / name).is_dir() else set())
    if not trials:
        return 0
    fmt = '{{.ID}} {{.Label "com.docker.compose.project"}}'
    rows = subprocess.run([*wb_cell.DIND, "ps", "-a", "--format", fmt], capture_output=True, text=True).stdout
    ids = [cid for cid, _, proj in (r.partition(" ") for r in rows.splitlines()) if trial_of(proj) in trials]
    if ids:
        subprocess.run([*wb_cell.DIND, "rm", "-f", *ids], capture_output=True)
    nets = [n for n in subprocess.run([*wb_cell.DIND, "network", "ls", "--format", "{{.Name}}"], capture_output=True,
                                      text=True).stdout.split()
            if n.endswith("_default") and trial_of(n[:-len("_default")]) in trials]
    if nets:
        subprocess.run([*wb_cell.DIND, "network", "rm", *nets], capture_output=True)
    return len(ids)


def drop_cancelled(name: str) -> list[str]:
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


def harbor_cmd(name: str, group: str, setup: str, slot: str) -> list[str]:
    proxy = f"http://127.0.0.1:{SLOTS[slot]['proxy']}"
    cmd = [str(DIR / "et_eval.sh"), "harbor", "run", "-p", str(QUEUES[group]), "-e", "docker",
           "-a", "et_agent:ETAgent", "-m", MODEL, "-o", str(JOBS), "--job-name", name,
           "-n", str(N_AGENTS + N_AGENTS // 2), "--n-concurrent-agents", str(N_AGENTS),
           "-k", "2" if group == "pilot" else "1", "-q", "-y", "--agent-timeout-multiplier", "8"]
    for k, v in {"base_url": proxy, "served_model": MODEL, "bench": "swe", "env_file": str(ENV / f"{setup}.env"),
                 "gate_url": proxy, **AGENT}.items():
        cmd += ["--ak", f"{k}={v}"]
    return cmd


def proxy_cmd(setup: str, slot: str, url: str, run_dir: Path) -> list[str]:
    s, cfg = SLOTS[slot], SETUPS[setup]
    cmd = [PY, str(DIR / "et_proxy.py"), "--upstream", url, "--port", str(s["proxy"]), "--out", str(run_dir),
           "--n-max", str(N_AGENTS), "--model", MODEL, "--hw-url", f"http://127.0.0.1:{s['hw']}"]
    if cfg.get("gate_k"):
        cmd += ["--gate-k", str(cfg["gate_k"])]
    if cfg.get("k_schedule"):
        cmd += ["--k-schedule", cfg["k_schedule"]]
    if cfg.get("offload"):
        cmd += ["--store-policy", cfg["offload"], "--no-store-key", "lmcache.max_offload_tokens"]
    if cfg.get("controller"):
        cmd += ["--controller", CONTROLLER["variant"], "--controller-effort", CONTROLLER["effort"]]
    return cmd


def wait_http(url: str, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=3).read()
            return True
        except Exception:                                         # noqa: BLE001
            time.sleep(1)
    return False


def client(name: str, group: str, setup: str, slot: str, url: str) -> int:
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    ENV.mkdir(parents=True, exist_ok=True)
    env_vars = SETUPS[setup].get("env") or {}
    (ENV / f"{setup}.env").write_text("".join(f"{k}={v}\n" for k, v in env_vars.items()) or "# pacing off\n")
    info = {"name": name, "setup": setup, "slot": slot, "gpu": SLOTS[slot]["gpu"], "group": group, "n": N_AGENTS,
            "url": url, "env": env_vars, "agent": AGENT, "server_args": server_args(setup, slot),
            "gate_k": SETUPS[setup].get("gate_k"), "offload": SETUPS[setup].get("offload"),
            "controller": CONTROLLER if SETUPS[setup].get("controller") else None, "t_client": time.time()}
    with (run_dir / "cell.jsonl").open("a") as f:
        f.write(json.dumps(info) + "\n")
    left = cleanup_dind(name)
    if left:
        print(f"[et] {name}: removed {left} containers left in docker-in-docker", flush=True)
    rerun = drop_cancelled(name)
    if rerun:
        print(f"[et] {name}: {len(rerun)} cancelled trials removed, so they run again", flush=True)
    s = SLOTS[slot]
    proxy = subprocess.Popen(proxy_cmd(setup, slot, url, run_dir), stdout=open(run_dir / "proxy.log", "a"),
                             stderr=subprocess.STDOUT)
    hw_cmd = [PY, str(DIR / "et_hw.py"), "--metrics-url", f"{url}/metrics", "--gpu", str(s["gpu"]),
              "--gate-url", f"http://127.0.0.1:{s['proxy']}", "--port", str(s["hw"]),
              "--out", str(run_dir / f"hw_{int(time.time())}.jsonl")]
    if SETUPS[setup].get("offload"):
        hw_cmd += ["--lmc-url", f"http://127.0.0.1:{s['lmc_http']}/metrics"]
    hw = subprocess.Popen(hw_cmd, stdout=open(run_dir / "hw.log", "a"), stderr=subprocess.STDOUT)
    if not wait_http(f"http://127.0.0.1:{s['proxy']}/gate/state"):
        print(f"[et] {name}: the proxy did not come up; see {run_dir / 'proxy.log'}", flush=True)
    env = dict(os.environ, ET_EVAL_NAME=f"eteval-{name}", ET_EVAL_CPUS=CLIENT_CPUS)
    print(f"[et] {name}: {setup} on GPU {s['gpu']}, queue {group}, N={N_AGENTS}", flush=True)
    harbor = subprocess.Popen(harbor_cmd(name, group, setup, slot), stdout=open(run_dir / "harbor.txt", "a"),
                              stderr=subprocess.STDOUT, env=env)

    def stop(signum, frame):
        print(f"[et] {name}: signal {signum}; stopping the Harbor job (resumable)", flush=True)
        subprocess.run(["docker", "stop", "-t", "120", f"eteval-{name}"], capture_output=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        rc = harbor.wait()
    finally:
        if harbor.poll() is None:
            subprocess.run(["docker", "stop", "-t", "120", f"eteval-{name}"], capture_output=True)
            harbor.wait()
        for p in (hw, proxy):
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
        left = cleanup_dind(name)
        if left:
            print(f"[et] {name}: removed {left} containers left in docker-in-docker", flush=True)
    info.update(t_end=time.time(), rc=rc)
    with (run_dir / "cell.jsonl").open("a") as f:
        f.write(json.dumps(info) + "\n")
    print(f"[et] {name}: Harbor exit {rc}", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for p in (sub.add_parser("run"), sub.add_parser("client")):
        p.add_argument("--setup", choices=sorted(SETUPS), required=True)
        p.add_argument("--slot", choices=sorted(SLOTS), required=True)
        p.add_argument("--group", default="main", choices=sorted(QUEUES))
        p.add_argument("--name", default=None, help="default: the setup's name")
    sub.choices["run"].add_argument("--hours", type=float, required=True)
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
        hours = DATA / "gpu_hours.jsonl"
        total = 0.0
        for line in hours.open() if hours.exists() else []:
            print(line.rstrip())
            total += json.loads(line).get("gpu_h", 0.0)
        print(f"total {total:.2f} GPU-h")
        return
    name = a.name or a.setup
    if a.cmd == "client":
        sys.exit(client(name, a.group, a.setup, a.slot, a.url))
    if a.group == "main":
        bad = changed()
        if bad:
            print(f"[et] refused: frozen files changed or missing: {bad}", flush=True)
            sys.exit(4)
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    configure(a.setup, a.slot, run_dir)
    cmd = [PY, str(Path(__file__).resolve()), "client", "--setup", a.setup, "--slot", a.slot, "--group", a.group,
           "--name", name, "--url", "{url}"]
    stop = threading.Event()
    threading.Thread(target=pin_roles, args=(os.getpid(), wb_cell.cpu_list(SLOTS[a.slot]["server_cpus"]),
                                             wb_cell.cpu_list(CLIENT_CPUS), run_dir / "pin.jsonl", stop),
                     daemon=True).start()
    rc = rs_.run_stage(name, a.hours, "et", cmd, claim_check=False)
    stop.set()
    sys.exit(rc)


if __name__ == "__main__":
    main()
