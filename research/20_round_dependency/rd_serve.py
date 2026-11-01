#!/usr/bin/env python3
"""Serve MiniMax-M2.7 on GPUs 0-3 (TP=4) for one GPU stage of research/20_round_dependency, run the stage's client
command against it, stop it and log the GPU-hours.

    python research/20_round_dependency/rd_serve.py run --stage s1_smoke --hours 0.5 -- \\
        /data/fritz/venv/bin/python research/20_round_dependency/rd_cf.py run --url {url} --sample pilot --out smoke
    python research/20_round_dependency/rd_serve.py hours          # the GPU-hour tally against the 90 GPU-h cap
    python research/20_round_dependency/rd_serve.py gpus           # GPUs 0-3 now

A stage
  1. refuses to start when GPUs 0-3 are not empty, when the lab GPU sheet lacks the member's claim on GPUs 0-3 for
     any 30-min slot (US Eastern) from now to now + --hours, or when the tally plus 4 x --hours passes the cap;
  2. starts `vllm serve MiniMaxAI/MiniMax-M2.7` with the stage's configuration. `profile` (S1-S3) is topic 19's
     server (research/19_efficiency_signals/ef_workloads.py SERVER_ARGS) without the scheduler extension and the
     pool pin, with a 131,072-token window for topic 16's longer prompts; `load19` / `load16` (S5) are topic 19's
     server with topic 11's MU scheduler log (and priority scheduling for load16). It waits for /health and one
     1-token request;
  3. runs the client command with {url} filled in (also as RD_URL in its environment), on the other CPU socket, and
     stops it 5 min before now + --hours (the claimed window; rd_cf resumes from its records);
  4. stops the server, waits until GPUs 0-3 are empty, and appends GPUs x the server's lifetime to
     data/gpu_hours.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_rounds as rr  # noqa: E402

MODEL = "MiniMaxAI/MiniMax-M2.7"
VLLM = "/data/fritz/venv/bin/vllm"
GPUS = [0, 1, 2, 3]
SERVER_CPUS, CLIENT_CPUS = "0-51,104-155", "52-103,156-207"     # GPUs 0-3 sit on CPU socket 0
CAP_GPU_H = 90.0
HOURS = rr.DATA / "gpu_hours.jsonl"
SERVERS = rr.DATA / "servers"
SHEET = "https://docs.google.com/spreadsheets/d/1ZUfNV5mGOmWK_-Tzc6zMmV6LWqNUFn41sG-XcCtyIog"
MEMBER = "Yijiahao"
CONFIGS = {
    "profile": ["--max-model-len", "131072", "--trust-remote-code",
                "--tool-call-parser", "minimax_m2", "--reasoning-parser", "minimax_m2_append_think",
                "--default-chat-template-kwargs", "{}", "--tensor-parallel-size", "4",
                "--gpu-memory-utilization", "0.92", "--max-num-seqs", "64",
                "--enable-prefix-caching", "--enable-prompt-tokens-details",
                "--chat-template", str(rr.T19_TEMPLATE)],
}
# S5: topic 19's server for load cells (research/19_efficiency_signals/ef_workloads.py SERVER_ARGS: the pool pinned at
# 16,137 blocks, max_num_seqs 64), with topic 11's MU scheduler logging every request, step and eviction
# (MU_SCHED_LOG in the stage folder) instead of topic 18's status extension, which the replay does not use.
_LOAD = ["--trust-remote-code", "--tool-call-parser", "minimax_m2", "--reasoning-parser", "minimax_m2_append_think",
         "--default-chat-template-kwargs", "{}", "--tensor-parallel-size", "4", "--gpu-memory-utilization", "0.92",
         "--max-num-seqs", "64", "--num-gpu-blocks-override", "16137", "--enable-prefix-caching",
         "--enable-prompt-tokens-details", "--chat-template", str(rr.T19_TEMPLATE),
         "--scheduler-cls", "mu_scheduler.MUScheduler"]
CONFIGS["load19"] = ["--max-model-len", "65536", *_LOAD]
CONFIGS["load16"] = ["--max-model-len", "131072", "--scheduling-policy", "priority", *_LOAD]
SCHED_LOG_CONFIGS = {"load19", "load16"}
SERVER_ENV = {"CUDA_VISIBLE_DEVICES": ",".join(map(str, GPUS)), "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
              "HF_HOME": "/data/fritz/hf", "HF_HUB_OFFLINE": "1", "SAFETENSORS_FAST_GPU": "1",
              "VLLM_NO_USAGE_STATS": "1"}


# -- GPUs, the tally, the sheet (the same rules as research/19_efficiency_signals/ef_pipeline.py) ------------------
def gpu_status() -> dict[int, tuple[int, int]]:
    """gpu -> (MiB used, number of compute processes)."""
    out = {}
    for g in GPUS:
        mem = subprocess.run(["nvidia-smi", f"--id={g}", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout.strip()
        apps = subprocess.run(["nvidia-smi", f"--id={g}", "--query-compute-apps=pid", "--format=csv,noheader"],
                              capture_output=True, text=True).stdout.strip()
        out[g] = (int(mem or 0), len([a for a in apps.splitlines() if a.strip()]))
    return out


def gpus_empty() -> bool:
    return all(mem < 1024 and procs == 0 for mem, procs in gpu_status().values())


def tally() -> float:
    if not HOURS.exists():
        return 0.0
    return sum(json.loads(line).get("gpu_h", 0.0) for line in HOURS.open() if line.strip())


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def sheet_claims() -> dict:
    """(date, slot start 'H:MM AM/PM') -> [the names on GPUs 0..7], from every week tab of the lab GPU sheet."""
    import csv
    import io
    import re
    tabs = re.findall(r'items\.push\(\{name: "([^"]*)", pageUrl: "[^"]*", gid: "(\d+)"', _get(f"{SHEET}/htmlview"))
    out = {}
    for name, gid in tabs:
        if "TEMPLATE" in name.upper():
            continue
        rows = list(csv.reader(io.StringIO(_get(f"{SHEET}/export?format=csv&gid={gid}"))))
        try:
            week = time.strptime(rows[0][1].strip(), "%b %d, %Y")
            hdr = next(i for i, r in enumerate(rows) if r[:2] == ["Date", "Time"])
        except (IndexError, ValueError, StopIteration):
            continue
        day = None
        for r in rows[hdr + 1:]:
            if r and r[0].strip():
                day = r[0].strip()
            if day and len(r) > 1 and r[1].strip():
                mm, dd = (int(x) for x in day.split()[-1].split("/"))
                year = week.tm_year + (1 if week.tm_mon == 12 and mm == 1 else 0)
                out[(f"{year:04d}-{mm:02d}-{dd:02d}", r[1].strip())] = [(r[2 + g].strip() if len(r) > 2 + g else "")
                                                                       for g in range(8)]
    return out


def claims_cover(start: float, end: float) -> tuple[bool, str]:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    try:
        claims = sheet_claims()
    except Exception as exc:
        return False, f"the sheet could not be read: {type(exc).__name__}: {exc}"
    tz = ZoneInfo("America/New_York")
    t = datetime.fromtimestamp(start, tz)
    t = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
    stop = datetime.fromtimestamp(end, tz)
    while t <= stop:
        slot = t.strftime("%I:%M %p").lstrip("0")
        names = claims.get((t.strftime("%Y-%m-%d"), slot))
        if names is None or any(names[g] != MEMBER for g in GPUS):
            return False, f"no {MEMBER} claim on GPUs {GPUS} at {t:%a %m/%d} {slot} US Eastern ({names})"
        t += timedelta(minutes=30)
    return True, f"claimed through {stop:%a %m/%d %I:%M %p} US Eastern"


# -- the server ---------------------------------------------------------------------------------------------------
def free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if "429" not in str(port) and "529" not in str(port):
            return port


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


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


def run_stage(stage: str, hours: float, config: str, client: list[str], claim_check: bool = True) -> int:
    if not gpus_empty():
        raise SystemExit(f"GPUs {GPUS} are not empty: {gpu_status()}")
    est = len(GPUS) * hours
    if tally() + est > CAP_GPU_H:
        raise SystemExit(f"GPU-hour cap: {tally():.2f} spent + {est:.2f} estimated > {CAP_GPU_H}")
    now = time.time()
    if claim_check:
        ok, why = claims_cover(now, now + hours * 3600)
        print(f"[serve] claims: {why}", flush=True)
        if not ok:
            print("[serve] refused: enter the claim in the GPU sheet first", flush=True)
            return 3
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sdir = SERVERS / f"{stage}_{stamp}"
    sdir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = dict(os.environ)
    env.update(SERVER_ENV)
    if config in SCHED_LOG_CONFIGS:
        env.update({"MU_SCHED_LOG": str(sdir / "sched.jsonl"), "MU_STRICT": "1", "MU_TOOLCALL_TOKEN": "200052",
                    "PYTHONPATH": str(rr.REPO / "research" / "11_multiuser_kv")})
    env["LIBRARY_PATH"] = ":".join(filter(None, ["/data/fritz/cache/linklibs", env.get("LIBRARY_PATH", "")]))
    cmd = ["taskset", "-c", SERVER_CPUS, VLLM, "serve", MODEL, "--host", "127.0.0.1", "--port", str(port),
           "--seed", "0", *CONFIGS[config]]
    info = {"stage": stage, "config": config, "gpus": GPUS, "port": port, "cmd": cmd, "env": SERVER_ENV,
            "client": client, "host": socket.gethostname(), "t_start": time.time()}
    print(f"[serve] {stage}: starting vLLM on GPUs {GPUS}, port {port}", flush=True)
    server = subprocess.Popen(cmd, stdout=open(sdir / "vllm.log", "a"), stderr=subprocess.STDOUT, env=env,
                              start_new_session=True, cwd=rr.REPO)
    info["pid"] = server.pid
    (sdir / "server_info.json").write_text(json.dumps(info, indent=1))
    rc, proc = 1, None
    try:
        t0 = time.time()
        while not healthy(port):
            if server.poll() is not None:
                raise RuntimeError(f"vLLM exited during startup (rc {server.returncode}); see {sdir / 'vllm.log'}")
            if time.time() - t0 > 1800:
                raise RuntimeError("vLLM not healthy after 1800 s")
            time.sleep(5)
        info["ready_s"] = round(time.time() - t0, 1)
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/completions", method="POST",
                                     data=json.dumps({"model": MODEL, "prompt": "Hello", "max_tokens": 1}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=300).read()
        (sdir / "server_info.json").write_text(json.dumps(info, indent=1))
        url = f"http://127.0.0.1:{port}"
        print(f"[serve] {stage}: ready in {info['ready_s']} s at {url}; running the client", flush=True)
        cenv = dict(os.environ)
        cenv.update({"RD_URL": url, "CUDA_VISIBLE_DEVICES": ""})
        proc = subprocess.Popen(["taskset", "-c", CLIENT_CPUS, *[a.replace("{url}", url) for a in client]],
                                env=cenv, cwd=rr.REPO, start_new_session=True)
        # the claimed window ends at t_start + hours: stop the client 5 min before it (rd_cf resumes from its records)
        left = info["t_start"] + hours * 3600 - 300 - time.time()
        try:
            rc = proc.wait(timeout=max(1.0, left))
            print(f"[serve] {stage}: client exit {rc}", flush=True)
        except subprocess.TimeoutExpired:
            print(f"[serve] {stage}: the stage's {hours} h are up; stopping the client (resumable)", flush=True)
            stop_group(proc, grace=30.0)
            rc = 124
    except KeyboardInterrupt:
        print(f"[serve] {stage}: interrupted", flush=True)
        rc = 130
    finally:
        stop_group(proc, grace=30.0)
        stop_group(server)
        t_stop = time.time()
        for _ in range(60):
            if gpus_empty():
                break
            time.sleep(5)
        empty = gpus_empty()
        gpu_h = len(GPUS) * (t_stop - info["t_start"]) / 3600
        HOURS.parent.mkdir(parents=True, exist_ok=True)
        with HOURS.open("a") as f:
            f.write(json.dumps({"stage": stage, "config": config, "t_start": info["t_start"], "t_stop": t_stop,
                                "gpus": GPUS, "gpu_h": round(gpu_h, 4), "rc": rc, "gpus_empty_after": empty}) + "\n")
        info.update(t_stop=t_stop, gpu_h=gpu_h, rc=rc, gpus_empty_after=empty)
        (sdir / "server_info.json").write_text(json.dumps(info, indent=1))
        print(f"[serve] {stage}: stopped; {gpu_h:.2f} GPU-h (tally {tally():.2f} / {CAP_GPU_H}); "
              f"GPUs 0-3 {'empty' if empty else 'NOT empty'}", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--stage", required=True)
    r.add_argument("--hours", type=float, required=True, help="expected server lifetime (claims, cap)")
    r.add_argument("--config", default="profile", choices=sorted(CONFIGS))
    r.add_argument("--no-claim-check", action="store_true", help="tests only")
    r.add_argument("client", nargs=argparse.REMAINDER)
    sub.add_parser("hours")
    sub.add_parser("gpus")
    a = ap.parse_args()
    if a.cmd == "run":
        client = a.client[1:] if a.client[:1] == ["--"] else a.client
        if not client:
            raise SystemExit("give the client command after --")
        sys.exit(run_stage(a.stage, a.hours, a.config, client, not a.no_claim_check))
    elif a.cmd == "hours":
        for line in HOURS.open() if HOURS.exists() else []:
            print(line.rstrip())
        print(f"tally {tally():.2f} / {CAP_GPU_H} GPU-h")
    else:
        print(gpu_status())


if __name__ == "__main__":
    main()
