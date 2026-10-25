#!/usr/bin/env python3
"""Run research/18_harness_wait cells on this host's GPU pairs (no Slurm): serve, check, scrape, sweep, stop.

Pairs: A = GPUs 0,1 with vLLM on CPUs 0-25,104-129; B = GPUs 2,3 with vLLM on CPUs 26-51,130-155 (GPUs 0-3
sit on CPU socket 0). The sessions and scrapers run on socket 1 (CPUs 52-103,156-207). Only GPUs 0-3 are used.

    python research/18_harness_wait/wt_pipeline.py cell --cond w16_none --pair A
    python research/18_harness_wait/wt_pipeline.py round w16_none:A w16_none_aa:B    # two cells, B starts 2 min later
    python research/18_harness_wait/wt_pipeline.py hours                              # the GPU-hour tally
    python research/18_harness_wait/wt_pipeline.py gpus                               # GPUs 0-3 now

A cell
  1. refuses to start when its GPUs are not empty, when its folder already holds runs, or when the GPU-hour
     tally plus this cell's estimate would pass the cap (CAP_GPU_H = 100);
  2. starts `vllm serve Qwen/Qwen3-32B` with the condition's arguments and environment (MU_STRICT=1: a
     scheduler setup error stops the server), waits for /health, sends one 1-token request, then checks the
     sched log header (WTScheduler on vLLM 0.31.0, the pool, the gate setting) and that /wt/status answers;
     a mismatch stops the cell before any session starts;
  3. runs the 1 Hz scraper (and the KV-event counter when the condition publishes events) and the window sweep;
  4. stops everything, waits until the GPUs are empty (and says so), compresses the sched log, removes the
     lanes and appends the cell's GPU-hours (GPUs x server lifetime) to data/gpu_hours.jsonl.
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
import time
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import wt_workloads as ww  # noqa: E402

import mu_workloads as mw  # noqa: E402  (already imported by wt_workloads)

# this topic's cells: in this process directly, and for the sweep and session subprocesses through the file
# that MU_EXTRA_CONDITIONS names (mu_workloads reads it at import)
os.environ["MU_EXTRA_CONDITIONS"] = str(ww.write_conditions())
mw.CONDITIONS.update(ww.conditions())

REPO = DIR.parents[1]
PY = "/data/fritz/venv/bin/python"
# WT_VLLM_CMD / WT_DATA_ROOT exist for test_wt_pipeline.py (a fake server, a temporary data root)
VLLM_CMD = os.environ.get("WT_VLLM_CMD", "/data/fritz/venv/bin/vllm").split()
DATA_ROOT = Path(os.environ["WT_DATA_ROOT"]) if os.environ.get("WT_DATA_ROOT") else ww.DATA
CELLS = DATA_ROOT / "cells"
ALLOWED_GPUS = {0, 1, 2, 3}
PAIRS = {"A": {"gpus": [0, 1], "cpus": "0-25,104-129"}, "B": {"gpus": [2, 3], "cpus": "26-51,130-155"}}
CLIENT_CPUS = "52-103,156-207"
CAP_GPU_H = 100.0
HOURS = DATA_ROOT / "gpu_hours.jsonl"
JOBS = DATA_ROOT / "jobs"
LANES = DATA_ROOT / "lanes" if os.environ.get("WT_DATA_ROOT") else Path("/data/fritz/wt_lanes")
MU_SCRAPE = REPO / "research" / "11_multiuser_kv" / "mu_scrape.py"


def gpu_status(gpus: list[int]) -> dict[int, tuple[int, int]]:
    """gpu -> (MiB used, number of compute processes)."""
    out = {}
    for g in gpus:
        mem = subprocess.run(["nvidia-smi", f"--id={g}", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout.strip()
        apps = subprocess.run(["nvidia-smi", f"--id={g}", "--query-compute-apps=pid", "--format=csv,noheader"],
                              capture_output=True, text=True).stdout.strip()
        out[g] = (int(mem or 0), len([a for a in apps.splitlines() if a.strip()]))
    return out


def gpus_empty(gpus: list[int]) -> bool:
    return all(mem < 1024 and procs == 0 for mem, procs in gpu_status(gpus).values())


def free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if "429" not in str(port) and "529" not in str(port):
            return port


def get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def health(port: int) -> bool:
    try:
        return get(f"http://127.0.0.1:{port}/health")[0] == 200
    except Exception:
        return False


def tally() -> float:
    if not HOURS.exists():
        return 0.0
    return sum(json.loads(line).get("gpu_h", 0.0) for line in HOURS.open() if line.strip())


def estimate_gpu_h(c: dict, warmup_s: float | None = None, window_s: float | None = None) -> float:
    warmup = float(c.get("warmup_s", 0)) if warmup_s is None else warmup_s
    window = float(c.get("window_s", 0)) if window_s is None else window_s
    return 2 * (warmup + window + 900) / 3600


def read_records(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def check_server(sched_log: Path, c: dict, args: list[str], port: int, timeout: float = 90.0) -> dict:
    """The scheduler header must say WTScheduler on vLLM 0.31.0 with this condition's pool and gate."""
    t0 = time.time()
    while True:
        records = read_records(sched_log)
        errs = [r for r in records if r.get("k") == "err"]
        if errs:
            raise RuntimeError(f"scheduler error record: {errs[0]}")
        hdr = next((r for r in records if r.get("k") == "hdr"), None)
        wthdr = next((r for r in records if r.get("k") == "wthdr"), None)
        if hdr and wthdr:
            break
        if time.time() - t0 > timeout:
            raise RuntimeError(f"no scheduler header in {sched_log} after {timeout:.0f} s")
        time.sleep(1.0)
    problems = []
    if hdr.get("cls") != "WTScheduler":
        problems.append(f"class {hdr.get('cls')}")
    if hdr.get("vllm") != "0.31.0":
        problems.append(f"vLLM {hdr.get('vllm')}")
    gate = int(c["env"].get("MU_GATE", 0))
    if (hdr.get("gate") or {}).get("k") != gate:
        problems.append(f"gate {hdr.get('gate')} != {gate}")
    if "--num-gpu-blocks-override" in args:
        want = int(args[len(args) - 1 - args[::-1].index("--num-gpu-blocks-override") + 1])
        if hdr.get("nblk") != want:
            problems.append(f"pool {hdr.get('nblk')} blocks != {want}")
    if not hdr.get("async"):
        problems.append("async scheduling off")
    status, body = get(f"http://127.0.0.1:{port}/wt/status?view=share&sid=pipeline-check&state=line", timeout=10)
    out = json.loads(body)
    if status != 200 or out.get("pool_tokens") != (hdr["nblk"] - 1) * hdr["bs"]:
        problems.append(f"/wt/status {status}: pool_tokens {out.get('pool_tokens')}")
    if problems:
        raise RuntimeError("server check failed: " + "; ".join(problems))
    return {"hdr": hdr, "wthdr": wthdr, "status": out}


def stop_group(proc: subprocess.Popen | None, grace: float = 60.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 15.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def kill_sessions(cond: str) -> int:
    """Stop this cell's session processes left behind by a failed sweep (each runs in its own process group:
    mu_sweep starts them with start_new_session). Matched on the exact mu_session command line."""
    import psutil
    killed = 0
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = proc.info.get("cmdline") or []
        if proc.info["pid"] == me or len(cmd) < 3:
            continue
        if any(part.endswith("11_multiuser_kv/mu_session.py") for part in cmd) and "--cond" in cmd \
                and cmd[cmd.index("--cond") + 1] == cond:
            try:
                os.killpg(proc.info["pid"], signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    return killed


def run_cell(cond: str, pair: str, warmup_s: float | None = None, window_s: float | None = None,
             users: int = 0, think_scale: float = 1.0) -> int:
    c = mw.cond(cond)
    if pair not in PAIRS:
        raise SystemExit(f"pair must be one of {sorted(PAIRS)}")
    gpus, cpus = PAIRS[pair]["gpus"], PAIRS[pair]["cpus"]
    assert set(gpus) <= ALLOWED_GPUS
    cell_dir = CELLS / cond
    runs = cell_dir / "runs"
    if runs.exists() and any(runs.iterdir()):
        raise SystemExit(f"{runs} already holds runs; move the cell away first")
    if not gpus_empty(gpus):
        raise SystemExit(f"GPUs {gpus} are not empty: {gpu_status(gpus)}")
    est = estimate_gpu_h(c, warmup_s, window_s)
    if tally() + est > CAP_GPU_H:
        raise SystemExit(f"GPU-hour cap: {tally():.1f} spent + {est:.1f} estimated > {CAP_GPU_H}")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sdir = cell_dir / "server" / f"{pair}_{stamp}"
    sdir.mkdir(parents=True, exist_ok=True)
    port, kv_port = free_port(), free_port()
    args = [str(a).replace("{kv_events_port}", str(kv_port)) for a in mw.vllm_args(c)]
    status_path = f"/dev/shm/wt_status_{cond}_{port}.json"
    env = dict(os.environ)
    env.update({"CUDA_VISIBLE_DEVICES": ",".join(map(str, gpus)), "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                "HF_HOME": "/data/shared/huggingface", "HF_HUB_OFFLINE": "1", "VLLM_NO_USAGE_STATS": "1",
                "LIBRARY_PATH": ":".join(filter(None, ["/data/fritz/cache/linklibs", env.get("LIBRARY_PATH", "")])),
                "PYTHONPATH": f"{DIR}:{REPO / 'research' / '11_multiuser_kv'}",
                "MU_SCHED_LOG": str(sdir / "sched.jsonl"), "WT_STATUS_PATH": status_path,
                "WT_EXT_LOG": str(sdir / "wt_ext.jsonl")})
    env.update({k: str(v) for k, v in c["env"].items()})
    cmd = ["taskset", "-c", cpus, *VLLM_CMD, "serve", mw.MODEL, "--host", "127.0.0.1", "--port", str(port),
           "--seed", "0", *args]
    info = {"cond": cond, "arm": c.get("arm"), "n": c["n"], "pair": pair, "gpus": gpus, "port": port,
            "kv_events_port": kv_port, "cmd": cmd, "server_env": c["env"], "client_env": c.get("client_env"),
            "status_path": status_path, "host": socket.gethostname(), "t_start": time.time()}
    print(f"[pipeline] {cond} on pair {pair} (GPUs {gpus}), port {port}", flush=True)
    server = subprocess.Popen(cmd, stdout=open(sdir / "vllm.log", "a"), stderr=subprocess.STDOUT, env=env,
                              start_new_session=True, cwd=REPO)
    info.update(pid=server.pid)
    (sdir / "server_info.json").write_text(json.dumps(info, indent=1))
    scraper = kvev = sweep = None
    rc = 1
    try:
        t0 = time.time()
        while not health(port):
            if server.poll() is not None:
                raise RuntimeError(f"vLLM exited during startup (rc {server.returncode}); see {sdir / 'vllm.log'}")
            if time.time() - t0 > 1800:
                raise RuntimeError("vLLM not healthy after 1800 s")
            time.sleep(5)
        info["ready_s"] = round(time.time() - t0, 1)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/completions", method="POST",
                                     data=json.dumps({"model": mw.MODEL, "prompt": "Hello", "max_tokens": 1}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=300).read()
        time.sleep(2.5)                                     # the observer's log flushes every second
        check = check_server(sdir / "sched.jsonl", c, args, port)
        info.update(check_ok=True, nblk=check["hdr"]["nblk"], pool_tokens=check["status"]["pool_tokens"])
        (sdir / "server_info.json").write_text(json.dumps(info, indent=1))
        print(f"[pipeline] {cond}: server ready in {info['ready_s']} s, pool {info['nblk']} blocks, checks ok", flush=True)
        lane_root = LANES / cond
        if lane_root.exists():
            shutil.rmtree(lane_root)
        extra = []
        if warmup_s is not None:
            extra += ["--warmup-s", str(warmup_s)]
        if window_s is not None:
            extra += ["--window-s", str(window_s)]
        if users:
            extra += ["--users", str(users)]
        if think_scale != 1.0:
            extra += ["--think-scale", str(think_scale)]
        sweep = subprocess.Popen(["taskset", "-c", CLIENT_CPUS, PY, str(DIR / "wt_sweep.py"), "run", "--cond", cond,
                                  "--cell", pair, "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(lane_root),
                                  "--sessions", str(ww.SESSIONS), "--cells-root", str(CELLS), "--python", PY, *extra],
                                 stdout=open(cell_dir / "sweep.log", "a"), stderr=subprocess.STDOUT, cwd=REPO,
                                 env=dict(os.environ), start_new_session=True)
        scraper = subprocess.Popen(["taskset", "-c", CLIENT_CPUS, PY, str(MU_SCRAPE), "run",
                                    "--metrics-url", f"http://127.0.0.1:{port}/metrics",
                                    "--gpu", ",".join(map(str, gpus)), "--out", str(sdir / "scrape.jsonl"),
                                    "--server-pid", str(server.pid), "--sweep-pid", str(sweep.pid)],
                                   stdout=open(sdir / "scrape.log", "a"), stderr=subprocess.STDOUT,
                                   start_new_session=True)
        if any("--kv-events-config" == a for a in args):
            kvev = subprocess.Popen([PY, str(MU_SCRAPE), "kvevents", "--endpoint", f"tcp://127.0.0.1:{kv_port}",
                                     "--out", str(sdir / "kvevents.json")],
                                    stdout=open(sdir / "kvevents.log", "a"), stderr=subprocess.STDOUT,
                                    start_new_session=True)
        rc = sweep.wait()
        print(f"[pipeline] {cond}: sweep exit {rc}", flush=True)
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[pipeline] {cond}: FAILED: {info['error']}", flush=True)
    finally:
        stop_group(sweep, grace=300)
        if "error" in info or rc != 0:
            time.sleep(5)
            info["sessions_killed"] = kill_sessions(cond)
        for proc in (scraper, kvev):
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    stop_group(proc, grace=5)
        stop_group(server, grace=90)
        t_wait = time.time()
        while time.time() - t_wait < 300 and not gpus_empty(gpus):
            time.sleep(3)
        info["t_stop"] = time.time()
        info["gpus_after"] = {str(k): v for k, v in gpu_status(gpus).items()}
        info["gpus_empty_after"] = gpus_empty(gpus)
        info["sweep_rc"] = rc
        (sdir / "server_info.json").write_text(json.dumps(info, indent=1))
        log = sdir / "sched.jsonl"
        if log.exists():
            subprocess.run(["xz", "-T2", "-f", str(log)], check=False)
        shutil.rmtree(LANES / cond, ignore_errors=True)
        try:
            os.unlink(status_path)
        except OSError:
            pass
        gpu_h = len(gpus) * (info["t_stop"] - info["t_start"]) / 3600
        HOURS.parent.mkdir(parents=True, exist_ok=True)
        with HOURS.open("a") as handle:
            handle.write(json.dumps({"cond": cond, "pair": pair, "gpus": gpus, "t_start": info["t_start"],
                                     "t_stop": info["t_stop"], "gpu_h": round(gpu_h, 3), "rc": rc,
                                     "error": info.get("error")}) + "\n")
        print(f"[pipeline] {cond}: stopped; GPUs {gpus} {'empty' if info['gpus_empty_after'] else 'NOT EMPTY'}; "
              f"{gpu_h:.2f} GPU-h (tally {tally():.1f} of {CAP_GPU_H:.0f})", flush=True)
    return 0 if rc == 0 and "error" not in info else 1


def run_round(items: list[str], stagger_s: float = 120.0) -> int:
    JOBS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    pairs = [item.split(":") for item in items]
    if len({p for _, p in pairs}) != len(pairs):
        raise SystemExit("one cell per pair")
    procs = []
    for i, (cond, pair) in enumerate(pairs):
        if i:
            time.sleep(stagger_s)
        log = JOBS / f"{stamp}_{cond}_{pair}.log"
        procs.append((cond, pair, subprocess.Popen([PY, str(Path(__file__).resolve()), "cell", "--cond", cond,
                                                    "--pair", pair], stdout=open(log, "a"),
                                                   stderr=subprocess.STDOUT, cwd=REPO)))
        print(f"[round] {cond} on {pair}: log {log}", flush=True)
    rcs = {}
    for cond, pair, proc in procs:
        rcs[cond] = proc.wait()
    print(f"[round] done: {rcs}; GPU-hours so far {tally():.1f} of {CAP_GPU_H:.0f}", flush=True)
    return 0 if all(rc == 0 for rc in rcs.values()) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cell", help="run one cell on one GPU pair")
    c.add_argument("--cond", required=True, choices=sorted(mw.CONDITIONS))
    c.add_argument("--pair", required=True, choices=sorted(PAIRS))
    c.add_argument("--warmup-s", type=float, default=None, help="override (tests)")
    c.add_argument("--window-s", type=float, default=None, help="override (tests)")
    c.add_argument("--users", type=int, default=0, help="override N (tests)")
    c.add_argument("--think-scale", type=float, default=1.0, help="scale think times (tests)")
    r = sub.add_parser("round", help="run cells on both pairs at once: COND:PAIR ...")
    r.add_argument("items", nargs="+")
    r.add_argument("--stagger-s", type=float, default=120.0)
    sub.add_parser("hours", help="print the GPU-hour tally")
    sub.add_parser("gpus", help="print GPUs 0-3")
    args = ap.parse_args()
    if args.cmd == "cell":
        sys.exit(run_cell(args.cond, args.pair, args.warmup_s, args.window_s, args.users, args.think_scale))
    if args.cmd == "round":
        sys.exit(run_round(args.items, args.stagger_s))
    if args.cmd == "hours":
        rows = [json.loads(line) for line in HOURS.open()] if HOURS.exists() else []
        for row in rows:
            print(f"{row['cond']:<20} {row['pair']} {row['gpu_h']:7.2f} GPU-h rc={row['rc']} {row.get('error') or ''}")
        print(f"total {tally():.2f} of {CAP_GPU_H:.0f} GPU-h")
        return
    print(json.dumps({g: gpu_status([g])[g] for g in sorted(ALLOWED_GPUS)}))


if __name__ == "__main__":
    main()
