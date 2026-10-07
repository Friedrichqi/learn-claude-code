#!/usr/bin/env python3
"""Start every vLLM instance of one arm of research/14_pd_crossover as children of this process, so 12's sampler
sees one server tree (its --server-pid-file is this process). Instances start 20 s apart (each one's memory profiling
runs alone), each with its GPUs (own first; visibility variant A also lists the job's other GPUs, B does not), its
cores, a fresh HTTP port, a fresh NIXL side-channel port (P / D) and its own MU_SCHED_LOG.

    python research/14_pd_crossover/pd_launch.py --level pd_1p1d_L_n08 --gpus 4,5 --cpus 0,1,...,15 \
        --out SRV --local /tmp/$USER/pd/JOB --tag TAG [--model Qwen/Qwen3-32B] [--only 0] [--arm 1p1d]
Writes SRV/launch.pid, SRV/instances.json (idx, role, tp, gpus, visible, port, base, side_port, pid, log, sched)
and then SRV/READY when every /health answers, or SRV/FAILED (with the reason) when one exits or 20 min pass.
SIGTERM / SIGINT: every instance's process group gets SIGTERM, then SIGKILL after 60 s; then this process exits.
Env: VLLM_BIN (default ~/.venv/bin/vllm; tests point it at pd_fake_vllm.py), PD_VISIBILITY (A / B, overrides
data/decisions.json), PD_UCX_TLS (overrides decisions' ucx_tls).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import pd_workloads as pw  # noqa: E402

DIR = Path(__file__).resolve().parent
RESEARCH = DIR.parent
PYPATH = ":".join(str(p) for p in (DIR, DIR / ".deps", RESEARCH / "12_hw_utilization", RESEARCH / "11_multiuser_kv"))
STAGGER_S = 20
READY_TIMEOUT_S = 1200


def free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if "429" not in str(p) and "529" not in str(p):
            return p


def healthy(base: str) -> bool:
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


class Launcher:
    def __init__(self, a) -> None:
        self.a = a
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.procs: list[subprocess.Popen] = []
        self.stopping = False

    def decisions(self) -> dict:
        d = pw.decisions()
        if os.environ.get("PD_VISIBILITY"):
            d["visibility"] = os.environ["PD_VISIBILITY"]
        if os.environ.get("PD_UCX_TLS"):
            d["ucx_tls"] = os.environ["PD_UCX_TLS"]
        if os.environ.get("PD_LEASE_S"):
            d["kv_lease_duration"] = int(os.environ["PD_LEASE_S"])
        return d

    def plan(self) -> list[dict]:
        dec = self.decisions()
        level = self.a.level
        if self.a.arm:                                 # calibration: an arm without a level
            level, cond = pw.make_level(self.a.arm, "L", 4, "smoke")
            pw.CONDITIONS.setdefault(level, cond)
        out = pw.instances(level, dec)
        if self.a.only is not None:
            out = [x for x in out if x["idx"] in self.a.only]
        return out

    def start(self) -> int:
        gpus = [g for g in self.a.gpus.split(",") if g]
        cpus = [c for c in self.a.cpus.split(",") if c]
        vbin = shlex.split(os.environ.get("VLLM_BIN") or str(Path.home() / ".venv/bin/vllm"))
        (self.out / "launch.pid").write_text(str(os.getpid()))
        infos = []
        for k, it in enumerate(self.plan()):
            if k:
                for _ in range(STAGGER_S):
                    if self.stopping:
                        return 1
                    time.sleep(1)
            port = free_port()
            base = f"http://127.0.0.1:{port}"
            idx = it["idx"]
            env = dict(os.environ)
            env.update(it["env"])
            env.update({"CUDA_VISIBLE_DEVICES": ",".join(gpus[j] for j in it["visible"]),
                        "PYTHONPATH": PYPATH + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
                        "MU_SCHED_LOG": f"{self.a.local}/{self.a.tag}.{idx}.sched.jsonl", "MU_POOL_WALK_S": "2",
                        "VLLM_SERVER_DEV_MODE": "1"})
            side = None
            if it["role"] != "C":
                side = free_port()
                env["VLLM_NIXL_SIDE_CHANNEL_PORT"] = str(side)
            c0, c1 = it["cores"]
            cores = ",".join(cpus[c0:c1]) or ",".join(cpus)
            cmd = ["taskset", "-c", cores] + vbin + ["serve", self.a.model, "--host", "127.0.0.1", "--port", str(port),
                                                     "--served-model-name", self.a.model] + it["args"]
            log = self.out / f"vllm{idx}.log"
            fh = open(log, "w", encoding="utf-8")
            fh.write("# " + shlex.join(cmd) + "\n# env: " + json.dumps({k: env[k] for k in sorted(it["env"])}
                     | {"CUDA_VISIBLE_DEVICES": env["CUDA_VISIBLE_DEVICES"],
                        "VLLM_NIXL_SIDE_CHANNEL_PORT": env.get("VLLM_NIXL_SIDE_CHANNEL_PORT")}) + "\n")
            fh.flush()
            proc = subprocess.Popen(cmd, env=env, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            self.procs.append(proc)
            infos.append({"idx": idx, "role": it["role"], "tp": it["tp"],
                          "gpus": [gpus[j] for j in it["gpus"]], "visible": env["CUDA_VISIBLE_DEVICES"],
                          "cores": cores, "port": port, "base": base, "side_port": side, "pid": proc.pid,
                          "log": str(log), "sched": env["MU_SCHED_LOG"], "args": it["args"]})
            (self.out / "instances.json").write_text(json.dumps(infos, indent=1))
        t0 = time.time()
        while not self.stopping:
            dead = [(i, p.poll()) for i, p in zip(infos, self.procs) if p.poll() is not None]
            if dead:
                (self.out / "FAILED").write_text(f"instance {dead[0][0]['idx']} exited rc={dead[0][1]}\n")
                return 1
            if all(healthy(i["base"]) for i in infos):
                (self.out / "READY").write_text(f"{time.time() - t0:.1f}\n")
                return 0
            if time.time() - t0 > READY_TIMEOUT_S:
                (self.out / "FAILED").write_text("timeout\n")
                return 1
            time.sleep(5)
        return 1

    def stop(self, *_) -> None:
        self.stopping = True

    def kill_all(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        t0 = time.time()
        while time.time() - t0 < 60 and any(p.poll() is None for p in self.procs):
            time.sleep(1)
        for p in self.procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--level", default=None)
    ap.add_argument("--arm", default=None, help="calibration: start this arm's instances without a level")
    ap.add_argument("--only", default=None, help="comma-separated instance indices to start")
    ap.add_argument("--gpus", required=True)
    ap.add_argument("--cpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--local", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--model", default=pw.MODEL)
    a = ap.parse_args()
    only = a.only or os.environ.get("PD_ONLY") or ""
    a.only = [int(x) for x in only.split(",") if x] or None
    if not a.level and not a.arm:
        ap.error("--level or --arm")
    Path(a.local).mkdir(parents=True, exist_ok=True)
    la = Launcher(a)
    signal.signal(signal.SIGTERM, la.stop)
    signal.signal(signal.SIGINT, la.stop)
    rc = la.start()
    if rc == 0:
        while not la.stopping:                         # serve until the pipeline stops us
            if any(p.poll() is not None for p in la.procs):
                (la.out / "DIED").write_text(time.strftime("%FT%T") + "\n")
                break
            time.sleep(2)
    la.kill_all()
    return rc


if __name__ == "__main__":
    sys.exit(main())
