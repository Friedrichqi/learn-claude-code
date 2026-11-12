#!/usr/bin/env python3
"""Topic 24's 1/s system sampler: what exp02's sampler.py (10/s: per-core busy, meminfo, vLLM tree, NVML power, clock,
utilization) does not record. No root needed.

One JSON line per second in --out (CLOCK_MONOTONIC `t` and wall `w`, ns). Cumulative counters are written as totals;
the analysis takes differences.

  cpu       cumulative CPU-seconds of each group, every process counted once, including short-lived ones (topic 12's
            TreeTotals, research/12_hw_utilization/hw_sampler.py:241):
              vllm_api, vllm_engine, vllm_other   the `vllm serve` tree (root = API server; EngineCore by name)
              harbor, child, docker_cli           the Harbor tree from --harbor-pid-file: the Harbor process, the s15
                                                  children (sp_child.py) and everything else in the tree (docker CLI)
              containers                          cgroup v2 cpu.stat of every docker-<id>.scope (the task containers)
              dockerd, containerd                 cgroup v2 cpu.stat of docker.service and containerd.service
              measurement                         the trees of the pids in --measure-pids-file (samplers, probe, runner)
  mem       RSS (anon, file) of the same process groups, kB; containers' memory.current summed, bytes; and their count
  hot       the three busiest threads of the vLLM engine and API processes: tid, name, cores, last CPU
  psi       /proc/pressure/{cpu,memory,io}: cumulative some/full stall microseconds
  sys       /proc/stat ctxt, procs_running, procs_blocked; /proc/vmstat pswpin, pswpout, pgmajfault
  nv        NVML energy (mJ, cumulative), temperature (C), power (W)
  therm     hottest thermal zone (mC) and all zones
  disk, net cumulative bytes: nvme0n1 read and written; all interfaces but lo, received and sent

    ~/.venv/bin/python research/24_spark_concurrency/sp_extra.py --out <run>/extra.jsonl \
        --harbor-pid-file <run>/harbor.pid --measure-pids-file <run>/measure_pids.json
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # research/
import _paths  # noqa: F401,E402
from hw_sampler import (TreeTotals, Writer, children_of, cpu_member, every, parse_pressure, read_kv,  # noqa: E402
                        read_pid_file, read_stat, read_status, read_text, walk)

CLK = os.sysconf("SC_CLK_TCK")
CG = Path("/sys/fs/cgroup/system.slice")


def kids(pid: int) -> list[int]:
    return children_of(pid) or []


def vllm_root() -> int | None:
    """The `vllm serve` process (its argv has an entry ending in bin/vllm and the word serve)."""
    for e in os.scandir("/proc"):
        if not e.name.isdigit():
            continue
        try:
            argv = open(f"/proc/{e.name}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"bin/vllm") for a in argv) and b"serve" in argv:
            st = read_stat(int(e.name))
            if st and not _is_vllm(st["ppid"]):
                return int(e.name)
    return None


def _is_vllm(pid: int) -> bool:
    try:
        argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError:
        return False
    return any(a.endswith(b"bin/vllm") for a in argv) and b"serve" in argv


def cmdline(pid: int) -> str:
    try:
        return open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="ignore")
    except OSError:
        return ""


class Groups:
    def __init__(self, harbor_pid_file: str | None, measure_file: str | None):
        self.harbor_pid_file = harbor_pid_file
        self.measure_file = measure_file
        self.tt = {k: TreeTotals(("cpu",)) for k in ("vllm", "harbor", "measure")}
        self.vroot = None
        self.vroot_t = 0.0
        self.prev_threads: dict[int, tuple[float, float]] = {}

    def _members(self, root: int | None, classify) -> tuple[list, dict]:
        members, rss = [], {}
        if not root:
            return members, rss
        for pid, depth in walk(root, kids):
            st = read_stat(pid)
            if not st:
                continue
            cls = classify(pid, depth, st)
            members.append(cpu_member(pid, cls, st))
            s = read_status(pid)
            r = rss.setdefault(cls, [0, 0])
            r[0] += s.get("anon", 0)
            r[1] += s.get("file", 0)
        return members, rss

    def sample(self) -> dict:
        now = time.monotonic()
        if self.vroot is None or not os.path.exists(f"/proc/{self.vroot}") or now - self.vroot_t > 30:
            self.vroot, self.vroot_t = vllm_root(), now
        out_cpu, out_mem = {}, {}
        engine_pids = []

        def cls_vllm(pid, depth, st):
            if depth == 0:
                return "vllm_api"
            if "EngineCor" in st["comm"]:
                engine_pids.append(pid)
                return "vllm_engine"
            return "vllm_other"

        def cls_harbor(pid, depth, st):
            if depth == 0:
                return "harbor"
            c = cmdline(pid)
            if "sp_child.py" in c or "s15_child.py" in c:
                return "child"
            return "docker_cli"

        hpid = read_pid_file(self.harbor_pid_file) if self.harbor_pid_file else None
        for key, root, fn in (("vllm", self.vroot, cls_vllm), ("harbor", hpid, cls_harbor)):
            members, rss = self._members(root, fn)
            totals, own = self.tt[key].update(members)
            out_cpu[f"{key}_tree"] = round(totals["cpu"], 3)
            out_cpu.update({k: round(v, 3) for k, v in own.items()})
            out_mem.update(rss)
        mpids = []
        if self.measure_file:
            try:
                mpids = [int(x) for x in json.loads(read_text(self.measure_file) or "[]")]
            except ValueError:
                mpids = []
        members = []
        for p in mpids:
            members += self._members(p, lambda pid, depth, st: "measurement")[0]
        totals, _ = self.tt["measure"].update(members)
        out_cpu["measurement"] = round(totals["cpu"], 3)
        # containers and the Docker daemons, from cgroup v2
        cont_cpu, cont_mem, n = 0, 0, 0
        try:
            for d in CG.glob("docker-*.scope"):
                cont_cpu += read_kv(d / "cpu.stat").get("usage_usec", 0)
                try:
                    cont_mem += int(read_text(d / "memory.current") or 0)
                except ValueError:
                    pass
                n += 1
        except OSError:
            pass
        out_cpu["containers"] = round(cont_cpu / 1e6, 3)
        out_mem["containers_b"] = cont_mem
        out_mem["containers_n"] = n
        for svc in ("docker", "containerd"):
            out_cpu[f"{svc}d" if svc == "docker" else svc] = round(
                read_kv(CG / f"{svc}.service" / "cpu.stat").get("usage_usec", 0) / 1e6, 3)
        return {"cpu": out_cpu, "mem": out_mem, "hot": self.hot(engine_pids + ([self.vroot] if self.vroot else []))}

    def hot(self, pids: list[int]) -> list:
        now = time.monotonic()
        rows = []
        cur = {}
        for pid in pids:
            try:
                tids = os.listdir(f"/proc/{pid}/task")
            except OSError:
                continue
            for t in tids:
                st = read_stat(pid, int(t))
                if not st:
                    continue
                cpu_s = (st["utime"] + st["stime"]) / CLK
                cur[int(t)] = (cpu_s, now)
                prev = self.prev_threads.get(int(t))
                if prev and now > prev[1]:
                    rows.append((round((cpu_s - prev[0]) / (now - prev[1]), 3), int(t), st["comm"], st["cpu"]))
        self.prev_threads = cur
        rows.sort(reverse=True)
        return [list(r) for r in rows[:3]]


class Nvml:
    def __init__(self):
        self.h = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:                                       # noqa: BLE001
            self.h = None

    def sample(self) -> dict:
        if self.h is None:
            return {}
        out = {}
        for k, f in (("energy_mj", lambda: self.nv.nvmlDeviceGetTotalEnergyConsumption(self.h)),
                     ("temp_c", lambda: self.nv.nvmlDeviceGetTemperature(self.h, self.nv.NVML_TEMPERATURE_GPU)),
                     ("power_w", lambda: round(self.nv.nvmlDeviceGetPowerUsage(self.h) / 1000, 2))):
            try:
                out[k] = f()
            except Exception:                                   # noqa: BLE001
                pass
        return out


def thermal() -> dict:
    zones = {}
    for z in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            zones[z.name[12:]] = int(read_text(z / "temp") or 0)
        except ValueError:
            pass
    return {"max": max(zones.values()) if zones else None, "zones": zones}


def disk() -> list:
    for line in (read_text("/proc/diskstats") or "").splitlines():
        p = line.split()
        if len(p) > 9 and p[2] == "nvme0n1":
            return [int(p[5]) * 512, int(p[9]) * 512]
    return []


def net() -> list:
    rx = tx = 0
    for line in (read_text("/proc/net/dev") or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        p = rest.split()
        if name.strip() != "lo" and len(p) >= 9:
            rx += int(p[0])
            tx += int(p[8])
    return [rx, tx]


def sysstat() -> dict:
    out = {}
    for line in (read_text("/proc/stat") or "").splitlines():
        k, _, v = line.partition(" ")
        if k in ("ctxt", "procs_running", "procs_blocked"):
            out[k] = int(v.split()[0])
    vm = read_kv("/proc/vmstat")
    out.update({k: vm.get(k) for k in ("pswpin", "pswpout", "pgmajfault")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--harbor-pid-file")
    ap.add_argument("--measure-pids-file")
    ap.add_argument("--period", type=float, default=1.0)
    a = ap.parse_args()
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    groups = Groups(a.harbor_pid_file, a.measure_pids_file)
    nv = Nvml()
    w = Writer(Path(a.out))

    def tick():
        rec = {"t": time.monotonic_ns(), "w": time.time_ns()}
        rec.update(groups.sample())
        rec["psi"] = {k: parse_pressure(read_text(f"/proc/pressure/{k}")) for k in ("cpu", "memory", "io")}
        rec["sys"] = sysstat()
        rec["nv"] = nv.sample()
        rec["therm"] = thermal()
        rec["disk"] = disk()
        rec["net"] = net()
        w.write(rec)

    every(a.period, stop, tick)
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
