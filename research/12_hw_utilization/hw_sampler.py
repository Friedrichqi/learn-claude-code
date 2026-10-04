#!/usr/bin/env python3
"""Hardware sampler for one cell of research/12_hw_utilization: GPU, CPU, host memory, links, vLLM.

Three threads append JSON lines to their own files in --out. Every record carries `t` (time.monotonic())
and `wall` (time.time()); the s15 trace's `monotonic_ns` and mu_scheduler's stamps use the same
CLOCK_MONOTONIC, so all streams join without clock fitting.

  fast.jsonl  every --fast-period (0.1 s). Per GPU: NVML utilisation (`util` = % of time a kernel ran,
              `mutil` = memory-controller %), and where GPM works the interval's SM active (`sm`), SM
              occupancy (`occ`), tensor active (`tensor`, `hmma`), HBM bandwidth (`dram`, % of peak),
              graphics-engine active (`gr`), PCIe and NVLink MiB/s. Per core of --cpus: busy fraction.
  host.jsonl  every --host-period (0.5 s). Cumulative CPU seconds per group (see TreeTotals), the hottest
              vLLM threads (cores, run-queue delay, last CPU), RSS per group, run-queue delay and context
              switches of the long-lived groups, cumulative I/O bytes per tree, and the job cgroup's
              cpu/memory/io stat and pressure (PSI).
  slow.jsonl  every --slow-period (1 s). vLLM /metrics (11's mu_scrape.parse_metrics); per GPU: memory
              used / reserved / total, per-process GPU memory, power, energy counter, clocks, throttle
              reasons, temperature, PCIe and NVLink byte counters (NVML field values); node meminfo and
              network bytes; PSS of the vLLM processes every 10th record.
  meta.json   what was available at start (GPM, field counters, NVLink links, cgroup files), GPU
              identities, cpus, args; rewritten at exit with the sampler's own CPU time.

Groups. The vLLM tree is rooted at the server pid: the root is `api`; descendants are `engine`
(EngineCore), `worker` (TP workers) or `vother`. The agent tree is rooted at the driver pid (mu_sweep):
the root is `driver`, its children are `harness` sessions (profile_run in-process), and everything below
them is `tools`. Both pids are re-read from --server-pid-file / --driver-pid-file on every host tick, so
one sampler covers server start-up, every load level and server restarts. Markers (level boundaries)
are written by the pipeline, not by the sampler.

    python research/12_hw_utilization/hw_sampler.py run --out DIR --gpus 0 --cpus 0-11 \\
        --metrics-url http://127.0.0.1:PORT/metrics --server-pid-file F --driver-pid-file G
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

from mu_scrape import fetch, parse_metrics  # noqa: E402  (research/11_multiuser_kv)

CLK = os.sysconf("SC_CLK_TCK")
# GPM metric ids (pynvml NVML_GPM_METRIC_*), numeric so an older pynvml cannot break the import
GPM_METRICS = {"gr": 1, "sm": 2, "occ": 3, "tensor": 5, "hmma": 7, "dram": 10,
               "pcie_tx": 20, "pcie_rx": 21, "nvl_rx": 60, "nvl_tx": 61}
FI_PCIE_TX, FI_PCIE_RX = 197, 198          # NVML_FI_DEV_PCIE_COUNT_TX/RX_BYTES
FI_NVL_TX, FI_NVL_RX = 138, 139            # NVML_FI_DEV_NVLINK_THROUGHPUT_DATA_TX/RX (KiB, scope = link)
MAX_LINKS = 18
STATUS_KEYS = {"RssAnon": "anon", "RssFile": "file", "RssShmem": "shmem",
               "voluntary_ctxt_switches": "vcs", "nonvoluntary_ctxt_switches": "ncs"}
CGROUP_MEM_KEYS = ("anon", "file", "shmem", "file_mapped", "kernel", "sock", "pgmajfault")


# ---------------------------------------------------------------------------------------- helpers
def parse_cpulist(text: str | None) -> list[int]:
    """'0-3,8,10-11' -> [0, 1, 2, 3, 8, 10, 11]."""
    out: list[int] = []
    for part in (text or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def read_text(path: str | Path) -> str | None:
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return None


def read_pid_file(path: str | None) -> int | None:
    if not path:
        return None
    text = read_text(path)
    try:
        return int(text.split()[0]) if text and text.split() else None
    except ValueError:
        return None


def parse_stat(text: str) -> dict:
    """/proc/<pid>[/task/<tid>]/stat -> the fields this sampler uses (comm may contain spaces/parens)."""
    lp, rp = text.index("("), text.rindex(")")
    f = text[rp + 2:].split()                 # f[k - 3] is field k of proc(5)
    return {"comm": text[lp + 1:rp], "state": f[0], "ppid": int(f[1]), "utime": int(f[11]),
            "stime": int(f[12]), "cutime": int(f[13]), "cstime": int(f[14]), "threads": int(f[17]),
            "start": int(f[19]), "cpu": int(f[36]) if len(f) > 36 else -1}


def read_stat(pid: int, tid: int | None = None) -> dict | None:
    text = read_text(f"/proc/{pid}/stat" if tid is None else f"/proc/{pid}/task/{tid}/stat")
    if not text:
        return None
    try:
        return parse_stat(text)
    except (ValueError, IndexError):
        return None


def read_status(pid: int) -> dict:
    out = {}
    text = read_text(f"/proc/{pid}/status") or ""
    for line in text.splitlines():
        key, _, value = line.partition(":")
        name = STATUS_KEYS.get(key)
        if name:
            try:
                out[name] = int(value.split()[0])
            except (ValueError, IndexError):
                pass
    return out


def read_kv(path: str | Path) -> dict:
    """'key value' lines (cpu.stat, memory.stat, /proc/<pid>/io with ':' separators) -> dict of ints."""
    out = {}
    for line in (read_text(path) or "").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def read_schedstat(pid: int, tid: int | None = None) -> tuple[int, int] | None:
    """(ns on CPU, ns runnable but waiting for a CPU) of one task."""
    text = read_text(f"/proc/{pid}/schedstat" if tid is None else f"/proc/{pid}/task/{tid}/schedstat")
    try:
        a, b = text.split()[:2]
        return int(a), int(b)
    except (AttributeError, ValueError):
        return None


def parse_pressure(text: str | None) -> dict:
    """PSI file -> {'some': total_us, 'full': total_us} (cumulative stall microseconds)."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if parts and parts[0] in ("some", "full"):
            for item in parts[1:]:
                if item.startswith("total="):
                    out[parts[0]] = int(item[6:])
    return out


def parse_proc_stat(text: str, cpus: list[int]) -> dict[int, tuple[int, int]]:
    """/proc/stat -> {cpu: (busy ticks, total ticks)} for the requested cpus (busy excludes idle+iowait)."""
    want = set(cpus)
    out = {}
    for line in text.splitlines():
        if not line.startswith("cpu") or line.startswith("cpu "):
            continue
        parts = line.split()
        try:
            cpu = int(parts[0][3:])
        except ValueError:
            continue
        if cpu not in want:
            continue
        vals = [int(x) for x in parts[1:9]]           # user nice system idle iowait irq softirq steal
        total = sum(vals)
        out[cpu] = (total - vals[3] - vals[4], total)
    return out


def children_of(pid: int) -> list[int] | None:
    """Direct children via /proc/<pid>/task/*/children (CONFIG_PROC_CHILDREN); None if unsupported."""
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return []
    kids: list[int] = []
    seen_file = False
    for tid in tids:
        text = read_text(f"/proc/{pid}/task/{tid}/children")
        if text is None:
            continue
        seen_file = True
        kids.extend(int(x) for x in text.split())
    return kids if seen_file else None


def ppid_map() -> dict[int, list[int]]:
    """Fallback: scan /proc once and build parent -> children."""
    out: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            st = read_stat(int(name))
            if st:
                out.setdefault(st["ppid"], []).append(int(name))
    return out


def walk(root: int, kids_fn) -> list[tuple[int, int]]:
    """[(pid, depth)] of root and its descendants (depth 0 = root)."""
    out, stack, seen = [], [(root, 0)], set()
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append((pid, depth))
        for kid in kids_fn(pid) or []:
            stack.append((kid, depth + 1))
    return out


def classify_vllm(depth: int, comm: str, cmdline: str) -> str:
    if depth == 0:
        return "api"
    text = f"{comm} {cmdline}"
    if "EngineCore" in text or "EngineCor" in text:
        return "engine"
    if "Worker" in text:
        return "worker"
    return "vother"


def classify_agent(depth: int) -> str:
    return "driver" if depth == 0 else "harness" if depth == 1 else "tools"


# ------------------------------------------------------------------------------------ CPU accounting
class TreeTotals:
    """Cumulative counters of one process tree, exact for children that start and end between samples.

    Each live member contributes a value that already includes its reaped children: for CPU
    utime+stime+cutime+cstime, for I/O /proc/<pid>/io (Linux adds a reaped child's io to the parent's
    signal->ioac, which /proc/<pid>/io reports). A child's counts move into its parent at the moment the
    parent reaps it, which is also when it leaves the live set (a zombie still reports its own), so
    sum(live members) is continuous and counts every process once -- including tool processes that
    lived and died between two samples. When a member disappears and its parent is not live in the tree
    (the root, a server restart, an orphan), nothing will carry its counts, so its last values move into
    an offset; every total stays monotonic. Lost: the last partial interval of such a member, and
    processes re-parented to init while still running.
    own[class] = sum over live members of the class of their own time (CPU only) plus the last own time
    of members that left.
    """

    def __init__(self, fields: tuple[str, ...]):
        self.fields = fields
        self.offset = {f: 0.0 for f in fields}
        self.dead_own: dict[str, float] = {}
        self.last: dict[tuple[int, int], tuple[int, str, float, dict]] = {}

    def update(self, members: list[tuple[int, int, int, str, float, dict]]) -> tuple[dict, dict]:
        """members: [(pid, start, ppid, class, own, totals)] of the live tree. Returns (totals, own)."""
        now = {(pid, start): (ppid, cls, own, tot) for pid, start, ppid, cls, own, tot in members}
        live = {pid for pid, _start in now}
        last_ppid = {pid: ppid for (pid, _start), (ppid, *_rest) in self.last.items()}
        for key, (ppid, cls, own, tot) in self.last.items():
            if key in now:
                continue
            self.dead_own[cls] = self.dead_own.get(cls, 0.0) + own
            # Its counts are carried up the reaping chain to the nearest ancestor that is still alive (a parent
            # that died in the same interval was itself reaped, with this child inside its cutime). Only when no
            # ancestor of the last snapshot is alive (the root, a restart, an orphan) do they need the offset.
            # [fixed 2026-10-03: J1's first rule checked only the parent, and double-counted a pytest that
            # exited together with its bash: up to 165 phantom cores in one 0.5-s interval]
            anc, seen = ppid, set()
            while anc not in live and anc in last_ppid and anc not in seen:
                seen.add(anc)
                anc = last_ppid[anc]
            if anc not in live:
                for f in self.fields:
                    self.offset[f] += tot.get(f, 0)
        self.last = now
        totals = dict(self.offset)
        own_by: dict[str, float] = dict(self.dead_own)
        for _ppid, cls, own, tot in now.values():
            own_by[cls] = own_by.get(cls, 0.0) + own
            for f in self.fields:
                totals[f] += tot.get(f, 0)
        return totals, own_by


def cpu_member(pid: int, cls: str, st: dict) -> tuple[int, int, int, str, float, dict]:
    own = (st["utime"] + st["stime"]) / CLK
    return pid, st["start"], st["ppid"], cls, own, {"cpu": own + (st["cutime"] + st["cstime"]) / CLK}


IO_FIELDS = ("rchar", "wchar", "read_bytes", "write_bytes")


# ------------------------------------------------------------------------------------------- GPUs
class Gpus:
    def __init__(self, indices: list[int]):
        self.ok = False
        self.handles: list = []
        self.gpm: list[bool] = []
        self.samples: list = []
        self.fields_ok: list[bool] = []
        self.links: list[list[int]] = []
        self.ident: list[dict] = []
        self.errors: list[str] = []
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
        except Exception as exc:                       # no driver (login node, tests)
            self.errors.append(f"nvmlInit: {exc}")
            return
        nv = self.nv
        for i in indices:
            try:
                h = nv.nvmlDeviceGetHandleByIndex(i)
            except Exception as exc:
                self.errors.append(f"handle {i}: {exc}")
                continue
            self.handles.append(h)
            ident = {"index": i}
            for key, fn in (("name", nv.nvmlDeviceGetName), ("uuid", nv.nvmlDeviceGetUUID)):
                try:
                    v = fn(h)
                    ident[key] = v.decode() if isinstance(v, bytes) else v
                except Exception:
                    pass
            try:
                ident["pci"] = nv.nvmlDeviceGetPciInfo(h).busId
                ident["pci"] = ident["pci"].decode() if isinstance(ident["pci"], bytes) else ident["pci"]
            except Exception:
                pass
            try:
                ident["mem_total_gib"] = round(nv.nvmlDeviceGetMemoryInfo(h).total / 2**30, 2)
            except Exception:
                pass
            for key, fn in (("pcie_gen_max", nv.nvmlDeviceGetMaxPcieLinkGeneration),
                            ("pcie_width_max", nv.nvmlDeviceGetMaxPcieLinkWidth),
                            ("pcie_gen", nv.nvmlDeviceGetCurrPcieLinkGeneration),
                            ("pcie_width", nv.nvmlDeviceGetCurrPcieLinkWidth)):
                try:
                    ident[key] = fn(h)
                except Exception:
                    pass
            links = []
            for link in range(MAX_LINKS):
                try:
                    if nv.nvmlDeviceGetNvLinkState(h, link) == 1:
                        links.append(link)
                except Exception:
                    break
            self.links.append(links)
            ident["nvlinks_active"] = links
            gpm = False
            try:
                gpm = bool(nv.nvmlGpmQueryDeviceSupport(h).isSupportedDevice)
                if gpm:
                    a, b = nv.nvmlGpmSampleAlloc(), nv.nvmlGpmSampleAlloc()
                    nv.nvmlGpmSampleGet(h, a)
                    self.samples.append([a, b])
                else:
                    self.samples.append(None)
            except Exception as exc:
                self.errors.append(f"gpm {i}: {exc}")
                gpm = False
                self.samples.append(None)
            self.gpm.append(gpm)
            fields_ok = False
            try:
                vals = nv.nvmlDeviceGetFieldValues(h, [FI_PCIE_TX, FI_PCIE_RX])
                fields_ok = all(v.nvmlReturn == 0 for v in vals)
            except Exception as exc:
                self.errors.append(f"fields {i}: {exc}")
            self.fields_ok.append(fields_ok)
            ident["gpm"] = gpm
            ident["pcie_counters"] = fields_ok
            self.ident.append(ident)
        self.ok = bool(self.handles)
        if any(self.gpm):        # GPM reports an interval: make sure the first interval is not empty
            time.sleep(0.05)

    def _gpm(self, k: int) -> dict:
        nv, h = self.nv, self.handles[k]
        a, b = self.samples[k]
        nv.nvmlGpmSampleGet(h, b)
        mg = nv.c_nvmlGpmMetricsGet_t()
        mg.version = nv.NVML_GPM_METRICS_GET_VERSION
        mg.numMetrics = len(GPM_METRICS)
        mg.sample1, mg.sample2 = a, b
        names = list(GPM_METRICS)
        for j, name in enumerate(names):
            mg.metrics[j].metricId = GPM_METRICS[name]
        nv.nvmlGpmMetricsGet(mg)
        self.samples[k] = [b, a]                       # the new sample starts the next interval
        out = {}
        for j, name in enumerate(names):
            if mg.metrics[j].nvmlReturn == 0:
                out[name] = round(mg.metrics[j].value, 2)
        return out

    def fast(self) -> list[dict]:
        out = []
        for k, h in enumerate(self.handles):
            rec = {}
            try:
                u = self.nv.nvmlDeviceGetUtilizationRates(h)
                rec["util"], rec["mutil"] = u.gpu, u.memory
            except Exception:
                pass
            if self.gpm[k]:
                try:
                    rec.update(self._gpm(k))
                except Exception as exc:
                    rec["gpm_err"] = str(exc)[:80]
            out.append(rec)
        return out

    def slow(self) -> list[dict]:
        nv = self.nv
        out = []
        for k, h in enumerate(self.handles):
            rec: dict = {}
            try:
                m = nv.nvmlDeviceGetMemoryInfo(h, version=nv.nvmlMemory_v2)
                rec.update({"used_mib": round(m.used / 2**20), "resv_mib": round(m.reserved / 2**20),
                            "total_mib": round(m.total / 2**20)})
            except Exception:
                try:
                    m = nv.nvmlDeviceGetMemoryInfo(h)
                    rec.update({"used_mib": round(m.used / 2**20), "total_mib": round(m.total / 2**20)})
                except Exception:
                    pass
            try:
                rec["procs"] = {str(p.pid): round((p.usedGpuMemory or 0) / 2**20)
                                for p in nv.nvmlDeviceGetComputeRunningProcesses(h)}
            except Exception:
                pass
            for key, fn in (("power_w", lambda: nv.nvmlDeviceGetPowerUsage(h) / 1000),
                            ("energy_mj", lambda: nv.nvmlDeviceGetTotalEnergyConsumption(h)),
                            ("sm_mhz", lambda: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM)),
                            ("mem_mhz", lambda: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_MEM)),
                            ("throttle", lambda: nv.nvmlDeviceGetCurrentClocksEventReasons(h)),
                            ("temp_c", lambda: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU))):
                try:
                    v = fn()
                    rec[key] = round(v, 1) if isinstance(v, float) else v
                except Exception:
                    pass
            if self.fields_ok[k]:
                try:
                    vals = nv.nvmlDeviceGetFieldValues(h, [FI_PCIE_TX, FI_PCIE_RX])
                    rec["pcie_tx_b"], rec["pcie_rx_b"] = (int(v.value.ullVal) for v in vals)
                except Exception:
                    pass
            if self.links[k]:
                try:
                    ids = [(FI_NVL_TX, link) for link in self.links[k]] + [(FI_NVL_RX, link) for link in self.links[k]]
                    vals = nv.nvmlDeviceGetFieldValues(h, ids)
                    n = len(self.links[k])
                    if all(v.nvmlReturn == 0 for v in vals):
                        rec["nvl_tx_kib"] = sum(int(v.value.ullVal) for v in vals[:n])
                        rec["nvl_rx_kib"] = sum(int(v.value.ullVal) for v in vals[n:])
                except Exception:
                    pass
            out.append(rec)
        return out


# ------------------------------------------------------------------------------------- host / proc
def _job_part(path: str) -> str:
    """Cut a cgroup path after its job_<id> component (the whole job, both cells)."""
    parts = path.strip("/").split("/")
    for i, part in enumerate(parts):
        if part.startswith("job_"):
            return "/".join(parts[:i + 1])
    return "/".join(parts)


class Cgroup:
    """The Slurm job's cgroup, v2 (one hierarchy, PSI per cgroup) or v1 (cpuacct + memory hierarchies)."""

    def __init__(self, root: str = "/sys/fs/cgroup", self_cgroup: str = "/proc/self/cgroup"):
        self.ver, self.dirs = None, {}
        lines = (read_text(self_cgroup) or "").splitlines()
        v1 = {}
        for line in lines:
            hier, ctrl, path = line.split(":", 2)
            if hier == "0" and ctrl == "":
                d = Path(root) / _job_part(path)
                if d.exists() and (d / "cgroup.controllers").exists():
                    self.ver, self.dirs = 2, {"all": d}
            for c in ctrl.split(","):
                v1[c] = path
        if self.ver is None and ("cpuacct" in v1 or "memory" in v1):
            self.ver = 1
            for ctrl, sub in (("cpuacct", "cpu,cpuacct"), ("memory", "memory")):
                if ctrl in v1:
                    for base in (Path(root) / sub, Path(root) / ctrl):
                        d = base / _job_part(v1[ctrl])
                        if d.exists():
                            self.dirs[ctrl] = d
                            break

    def describe(self) -> dict:
        return {"ver": self.ver, "dirs": {k: str(v) for k, v in self.dirs.items()}}

    def sample(self) -> dict | None:
        if self.ver == 2:
            cg = self.dirs["all"]
            cpu = read_kv(cg / "cpu.stat")
            ms = read_kv(cg / "memory.stat")
            iostat = {"rbytes": 0, "wbytes": 0}
            for line in (read_text(cg / "io.stat") or "").splitlines():
                for item in line.split()[1:]:
                    k, _, v = item.partition("=")
                    if k in iostat:
                        iostat[k] += int(v)
            mc, mp = read_text(cg / "memory.current"), read_text(cg / "memory.peak")
            return {"ver": 2, "cpu_us": cpu.get("usage_usec"), "user_us": cpu.get("user_usec"),
                    "sys_us": cpu.get("system_usec"), "thr_us": cpu.get("throttled_usec"),
                    "psi_cpu": parse_pressure(read_text(cg / "cpu.pressure")),
                    "psi_mem": parse_pressure(read_text(cg / "memory.pressure")),
                    "psi_io": parse_pressure(read_text(cg / "io.pressure")),
                    "mem_cur": int(mc) if mc and mc.strip().isdigit() else None,
                    "mem_peak": int(mp) if mp and mp.strip().isdigit() else None,
                    "mem": {k: ms.get(k) for k in CGROUP_MEM_KEYS if k in ms}, "io": iostat}
        if self.ver == 1:
            out: dict = {"ver": 1}
            d = self.dirs.get("cpuacct")
            if d is not None:
                usage = read_text(d / "cpuacct.usage")
                st = read_kv(d / "cpuacct.stat")
                out["cpu_us"] = int(usage) // 1000 if usage and usage.strip().isdigit() else None
                out["user_us"] = st.get("user", 0) * 1_000_000 // CLK if st else None
                out["sys_us"] = st.get("system", 0) * 1_000_000 // CLK if st else None
            d = self.dirs.get("memory")
            if d is not None:
                mc, mp = read_text(d / "memory.usage_in_bytes"), read_text(d / "memory.max_usage_in_bytes")
                ms = read_kv(d / "memory.stat")
                out["mem_cur"] = int(mc) if mc and mc.strip().isdigit() else None
                out["mem_peak"] = int(mp) if mp and mp.strip().isdigit() else None
                out["mem"] = {"anon": ms.get("total_rss"), "file": ms.get("total_cache"),
                              "shmem": ms.get("total_shmem"), "file_mapped": ms.get("total_mapped_file")}
            return out
        return None


def node_pressure() -> dict:
    """Node-wide PSI (/proc/pressure): context only, other jobs share the node."""
    return {k: parse_pressure(read_text(f"/proc/pressure/{k}")) for k in ("cpu", "memory", "io")}


class Host:
    """Process trees, per-group CPU / memory / delay / I/O, cgroup stats."""

    def __init__(self, server_pid_file: str | None, driver_pid_file: str | None):
        self.server_pid_file, self.driver_pid_file = server_pid_file, driver_pid_file
        self.vcpu, self.acpu = TreeTotals(("cpu",)), TreeTotals(("cpu",))
        self.vio, self.aio = TreeTotals(IO_FIELDS), TreeTotals(IO_FIELDS)
        self.server = self.driver = None            # last good pids (a pid file can be mid-rewrite)
        self.cmd_cache: dict[tuple[int, int], str] = {}
        self.thread_last: dict[tuple[int, int], tuple[int, int, float]] = {}    # (pid, tid) -> (ticks, delay_ns, t)
        self.proc_last: dict[tuple[int, int], dict] = {}                         # (pid, start) -> counters
        self.children_ok = children_of(os.getpid()) is not None
        self.cgroup = Cgroup()
        self.self_pid = os.getpid()

    def _kids_fn(self):
        if self.children_ok:
            return children_of
        pm = ppid_map()
        return lambda pid: pm.get(pid, [])

    def _cmdline(self, pid: int, start: int) -> str:
        key = (pid, start)
        if key not in self.cmd_cache:
            text = read_text(f"/proc/{pid}/cmdline") or ""
            self.cmd_cache[key] = text.replace("\0", " ")[:200]
        return self.cmd_cache[key]

    def _tree(self, root: int | None, kind: str, kids_fn) -> list[tuple[int, str, dict]]:
        if not root:
            return []
        members = []
        for pid, depth in walk(root, kids_fn):
            st = read_stat(pid)
            if st is None:
                continue
            if kind == "vllm":
                cls = classify_vllm(depth, st["comm"], self._cmdline(pid, st["start"]))
            else:
                cls = classify_agent(depth)
            members.append((pid, cls, st))
        members.sort(key=lambda m: 0 if m[0] == root else 1)
        return members

    def _threads(self, pid: int, now: float) -> list[dict]:
        """Per-thread cores and run-queue delay since the previous tick, for one process."""
        out = []
        try:
            tids = os.listdir(f"/proc/{pid}/task")
        except OSError:
            return out
        for name in tids:
            tid = int(name)
            st = read_stat(pid, tid)
            ss = read_schedstat(pid, tid)
            if st is None:
                continue
            ticks = st["utime"] + st["stime"]
            delay = ss[1] if ss else 0
            key = (pid, tid)
            prev = self.thread_last.get(key)
            self.thread_last[key] = (ticks, delay, now)
            if prev is None or now <= prev[2]:
                continue
            dt = now - prev[2]
            out.append({"tid": tid, "name": st["comm"], "cpu": round((ticks - prev[0]) / CLK / dt, 3),
                        "delay": round((delay - prev[1]) / 1e9 / dt, 3), "on": st["cpu"]})
        return out

    def sample(self) -> dict:
        now = time.monotonic()
        rec: dict = {"t": now, "wall": time.time()}
        kids_fn = self._kids_fn()
        server = read_pid_file(self.server_pid_file) or self.server
        driver = read_pid_file(self.driver_pid_file) or self.driver
        self.server, self.driver = server, driver
        vm = self._tree(server, "vllm", kids_fn)
        am = self._tree(driver, "agent", kids_fn)
        vtot, vown = self.vcpu.update([cpu_member(p, c, s) for p, c, s in vm])
        atot, aown = self.acpu.update([cpu_member(p, c, s) for p, c, s in am])
        me = read_stat(self.self_pid)
        rec["cpu"] = {"vllm_total": round(vtot["cpu"], 3), **{k: round(v, 3) for k, v in vown.items()},
                      "agent_total": round(atot["cpu"], 3), **{k: round(v, 3) for k, v in aown.items()},
                      "sampler": round((me["utime"] + me["stime"]) / CLK, 3) if me else None}
        rec["pids"] = {"server": server, "driver": driver, "n_vllm": len(vm), "n_agent": len(am),
                       "n_harness": sum(1 for m in am if m[1] == "harness"),
                       "n_tools": sum(1 for m in am if m[1] == "tools")}
        # memory, context switches, run-queue delay per group
        mem: dict[str, dict] = {}
        ctx: dict[str, list[int]] = {}
        seen = set()
        for pid, cls, st in vm + am:
            status = read_status(pid)
            g = mem.setdefault(cls, {"anon": 0, "file": 0, "shmem": 0, "n": 0})
            for k in ("anon", "file", "shmem"):
                g[k] += status.get(k, 0)
            g["n"] += 1
            key = (pid, st["start"])
            seen.add(key)
            prev = self.proc_last.get(key)
            cur = {"vcs": status.get("vcs", 0), "ncs": status.get("ncs", 0)}
            if cls in ("api", "engine", "worker", "harness", "driver"):
                ss = read_schedstat(pid)            # main thread only; threads of vLLM below
                cur["delay"] = ss[1] if ss else 0
            self.proc_last[key] = cur
            if prev is not None:
                c = ctx.setdefault(cls, [0, 0, 0])
                c[0] += max(0, cur["vcs"] - prev["vcs"])
                c[1] += max(0, cur["ncs"] - prev["ncs"])
                c[2] += max(0, cur.get("delay", 0) - prev.get("delay", 0))
        for key in [k for k in self.proc_last if k not in seen]:
            del self.proc_last[key]
        rec["mem_kib"] = mem
        rec["ctx"] = {k: {"vcs": v[0], "ncs": v[1], "delay_ms": round(v[2] / 1e6, 2)} for k, v in ctx.items()}
        # hottest threads of the vLLM processes
        hot = {}
        for pid, cls, st in vm:
            if cls in ("api", "engine", "worker"):
                threads = sorted(self._threads(pid, now), key=lambda r: -r["cpu"])
                if threads:
                    hot.setdefault(cls, []).extend(threads[:4])
        alive = {(pid, tid) for pid, _, _ in vm for tid in self._tids(pid)}
        for key in [k for k in self.thread_last if k not in alive]:
            del self.thread_last[key]
        rec["hot"] = {k: sorted(v, key=lambda r: -r["cpu"])[:6] for k, v in hot.items()}
        # I/O per tree
        rec["io"] = {}
        for name, members, acc in (("vllm", vm, self.vio), ("agent", am, self.aio)):
            rows = [(p, s["start"], s["ppid"], c, 0.0, read_kv(f"/proc/{p}/io")) for p, c, s in members]
            rec["io"][name], _ = acc.update(rows)
        # job cgroup
        cg = self.cgroup.sample()
        if cg is not None:
            rec["cg"] = cg
        return rec

    @staticmethod
    def _tids(pid: int) -> list[int]:
        try:
            return [int(t) for t in os.listdir(f"/proc/{pid}/task")]
        except OSError:
            return []

    def pss(self) -> dict:
        """PSS (KiB) of the live vLLM processes, by class (smaps_rollup is too slow for every tick)."""
        out: dict[str, dict] = {}
        server = read_pid_file(self.server_pid_file)
        for pid, cls, _ in self._tree(server, "vllm", self._kids_fn()):
            vals = read_kv(f"/proc/{pid}/smaps_rollup")
            g = out.setdefault(cls, {"pss": 0, "pss_anon": 0, "pss_file": 0, "pss_shmem": 0})
            for src, dst in (("Pss", "pss"), ("Pss_Anon", "pss_anon"), ("Pss_File", "pss_file"),
                             ("Pss_Shmem", "pss_shmem")):
                g[dst] += vals.get(src, 0)
        return out


def node_meminfo() -> dict:
    vals = read_kv("/proc/meminfo")
    return {k: vals.get(k) for k in ("MemTotal", "MemAvailable", "Cached", "Shmem", "Mlocked") if k in vals}


def net_bytes() -> dict:
    out = {}
    for line in (read_text("/proc/net/dev") or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) >= 9:
            out[name.strip()] = [int(parts[0]), int(parts[8])]      # rx bytes, tx bytes
    return out


# ------------------------------------------------------------------------------------------ loops
class Writer:
    def __init__(self, path: Path):
        self.handle = path.open("a", encoding="utf-8")
        self.lock = threading.Lock()
        self.last_flush = time.monotonic()

    def write(self, record: dict) -> None:
        with self.lock:
            self.handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            if time.monotonic() - self.last_flush > 1.0:
                self.handle.flush()
                self.last_flush = time.monotonic()

    def close(self) -> None:
        with self.lock:
            self.handle.flush()
            self.handle.close()


def every(period: float, stop: threading.Event, body) -> None:
    """Call body() every `period` seconds (drift-corrected) until stop is set."""
    next_t = time.monotonic()
    while not stop.is_set():
        try:
            body()
        except Exception as exc:                      # a sampler must never die on one bad read
            print(f"[hw_sampler] {body.__name__}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        next_t += period
        delay = next_t - time.monotonic()
        if delay < 0:
            next_t = time.monotonic()
            delay = 0
        stop.wait(delay)


def run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    gpus = Gpus(parse_cpulist(args.gpus) if args.gpus else [])
    cpus = parse_cpulist(args.cpus) if args.cpus else sorted(os.sched_getaffinity(0))
    host = Host(args.server_pid_file, args.driver_pid_file)
    t_start, wall_start = time.monotonic(), time.time()
    meta = {"host": socket.gethostname(), "pid": os.getpid(), "t_start": t_start, "wall_start": wall_start,
            "args": vars(args), "cpus": cpus, "affinity": sorted(os.sched_getaffinity(0)),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "clk_tck": CLK,
            "gpus": gpus.ident, "gpu_errors": gpus.errors, "children_files": host.children_ok,
            "cgroup": host.cgroup.describe(),
            "periods": {"fast": args.fast_period, "host": args.host_period, "slow": args.slow_period}}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    wf, wh, ws = Writer(out / "fast.jsonl"), Writer(out / "host.jsonl"), Writer(out / "slow.jsonl")
    prev_cores = {"v": parse_proc_stat(read_text("/proc/stat") or "", cpus)}
    counter = {"slow": 0}

    def fast():
        now, wall = time.monotonic(), time.time()
        cores = parse_proc_stat(read_text("/proc/stat") or "", cpus)
        busy = []
        for c in cpus:
            a, b = prev_cores["v"].get(c), cores.get(c)
            busy.append(round((b[0] - a[0]) / (b[1] - a[1]), 2) if a and b and b[1] > a[1] else None)
        prev_cores["v"] = cores
        rec = {"t": now, "wall": wall, "core": busy}
        if gpus.ok:
            rec["gpu"] = gpus.fast()
        wf.write(rec)

    def hostloop():
        wh.write(host.sample())

    def slow():
        now, wall = time.monotonic(), time.time()
        rec: dict = {"t": now, "wall": wall}
        if args.metrics_url:
            t0 = time.monotonic()
            text = fetch(args.metrics_url, timeout=2.0)
            rec["ok"] = text is not None
            rec["m"] = parse_metrics(text) if text else None
            rec["scrape_ms"] = round((time.monotonic() - t0) * 1000, 1)
        if gpus.ok:
            rec["gpu"] = gpus.slow()
        rec["node"] = node_meminfo()
        rec["psi_node"] = node_pressure()
        rec["net"] = net_bytes()
        counter["slow"] += 1
        if counter["slow"] % 10 == 1:
            rec["pss_kib"] = host.pss()
        ws.write(rec)

    threads = [threading.Thread(target=every, args=(args.fast_period, stop, fast), daemon=True, name="fast"),
               threading.Thread(target=every, args=(args.host_period, stop, hostloop), daemon=True, name="host"),
               threading.Thread(target=every, args=(args.slow_period, stop, slow), daemon=True, name="slow")]
    for th in threads:
        th.start()
    end_at = time.monotonic() + args.duration if args.duration else None
    while not stop.is_set():
        stop.wait(0.5)
        if end_at and time.monotonic() > end_at:
            stop.set()
    for th in threads:
        th.join(timeout=10)
    for w in (wf, wh, ws):
        w.close()
    me = read_stat(os.getpid())
    meta.update({"t_end": time.monotonic(), "wall_end": time.time(),
                 "sampler_cpu_s": round((me["utime"] + me["stime"]) / CLK, 3) if me else None})
    meta["sampler_cores"] = (round(meta["sampler_cpu_s"] / (meta["t_end"] - t_start), 4)
                             if meta["sampler_cpu_s"] is not None and meta["t_end"] > t_start else None)
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="sample until SIGTERM (or --duration)")
    r.add_argument("--out", required=True, help="output directory (node-local; the pipeline compresses it)")
    r.add_argument("--gpus", default="", help="NVML indices, e.g. 0 or 0,1")
    r.add_argument("--cpus", default="", help="cores whose busy fraction is recorded (default: own affinity)")
    r.add_argument("--metrics-url", default="", help="vLLM /metrics URL")
    r.add_argument("--server-pid-file", default="", help="file holding the vLLM API server pid (re-read)")
    r.add_argument("--driver-pid-file", default="", help="file holding the driver (mu_sweep) pid (re-read)")
    r.add_argument("--fast-period", type=float, default=0.1)
    r.add_argument("--host-period", type=float, default=0.5)
    r.add_argument("--slow-period", type=float, default=1.0)
    r.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds (0 = never)")
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
