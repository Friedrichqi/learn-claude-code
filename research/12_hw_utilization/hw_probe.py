#!/usr/bin/env python3
"""Phase 0 of research/12_hw_utilization: what can be measured on this node, and is it right?

Run inside a GPU job before any server starts (both GPUs idle). Writes one JSON file and prints a summary.

  env     node, CPU model / NUMA, kernel, cgroup version and files, PSI, /proc children and schedstat
          files, perf / resctrl / RAPL access, nsys / dcgmi presence, nvidia-smi topology (NVLink between
          the allocated pair? NVSwitch fabric?), DCGM profiling fields
  nvml    per GPU: identity, PCIe link, NVLink links, GPM support, PCIe byte counters; NVML index <-> CUDA
          device mapping checked by UUID
  cpu     the sampler's tree accounting vs rusage ground truth (CPU burn + 600 short-lived greps, also via a
          grandchild); per-core busy of a pinned burn; I/O of reaped children reaching the parent
  gpu     known loads, each read through the sampler's own Gpus class:
          HBM      allocate 8 GiB, NVML used must grow by 8 GiB (+-5 %)
          GPM      bf16 matmul (SM / tensor high) and device copy (HBM bandwidth high)
          PCIe     10 x 2 GiB pinned H2D then D2H: GB/s (the peak used as denominator) and the PCIe byte
                   counters / GPM rates vs the bytes moved (+-5 %)
          NVLink   10 x 2 GiB peer copy GPU0 -> GPU1: GB/s and NVLink counters vs bytes moved (+-5 %);
                   NCCL all-reduce sweep 1 MiB - 1 GiB on the 2 GPUs: bus bandwidth (TP's traffic pattern)
  sampler hw_sampler.py for 20 s on idle GPUs: its own CPU (must be < 0.1 core)

    python research/12_hw_utilization/hw_probe.py all --out research/12_hw_utilization/data/probe/<job>.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hw_sampler as hs  # noqa: E402

DIR = Path(__file__).resolve().parent
GIB = 2**30


def sh(cmd: str, timeout: float = 60) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "out": p.stdout[-6000:], "err": p.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"rc": None, "out": "", "err": f"timeout {timeout}s"}


def within(measured: float | None, expected: float, tol: float = 0.05) -> bool | None:
    if measured is None or not expected:
        return None
    return abs(measured - expected) <= tol * expected


# ------------------------------------------------------------------------------------------- env
def probe_env() -> dict:
    out: dict = {"host": socket.gethostname(), "kernel": os.uname().release,
                 "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
                 "affinity": sorted(os.sched_getaffinity(0)), "slurm_job": os.environ.get("SLURM_JOB_ID")}
    out["lscpu"] = sh("lscpu")["out"]
    out["numa"] = sh("numactl -H")["out"]
    out["meminfo_total_kib"] = hs.read_kv("/proc/meminfo").get("MemTotal")
    cg = hs.Cgroup()
    out["cgroup"] = cg.describe()
    out["cgroup_sample"] = cg.sample()
    if cg.ver == 2:
        out["cgroup_files"] = sorted(p.name for p in cg.dirs["all"].iterdir())
    out["psi_node"] = hs.node_pressure()
    out["proc_children_files"] = hs.children_of(os.getpid()) is not None
    out["schedstat"] = hs.read_schedstat(os.getpid()) is not None
    out["perf_event_paranoid"] = (hs.read_text("/proc/sys/kernel/perf_event_paranoid") or "").strip()
    tools = {}
    for t in ("perf", "nsys", "ncu", "dcgmi", "nvidia-smi", "pidstat", "numactl"):
        tools[t] = shutil.which(t)
    out["tools"] = tools
    if tools["perf"]:
        out["perf_cycles"] = sh("perf stat -e cycles -- true", 30)
        out["perf_uncore_imc"] = sh("perf stat -a -e uncore_imc/cas_count_read/,uncore_imc/cas_count_write/ -- sleep 1", 30)
        out["perf_amd_df"] = sh("perf list 2>/dev/null | grep -i -E 'umc|data_fabric|amd_df' | head -5", 30)
    out["resctrl"] = {"mounted": "resctrl" in (hs.read_text("/proc/mounts") or ""),
                      "mon_data": os.path.isdir("/sys/fs/resctrl/mon_data")}
    rapl = {}
    for path in sorted(Path("/sys/class/powercap").glob("*rapl*:*/energy_uj"))[:4]:
        text = hs.read_text(path)
        rapl[str(path)] = text.strip() if text else "unreadable"
    out["rapl"] = rapl
    out["nvidia_smi"] = sh("nvidia-smi --query-gpu=index,name,uuid,pci.bus_id,driver_version,memory.total "
                           "--format=csv,noheader")["out"]
    out["topo"] = sh("nvidia-smi topo -m")["out"]
    out["nvlink_status"] = sh("nvidia-smi nvlink -s")["out"]
    out["fabric"] = sh("nvidia-smi -q | grep -A6 -i fabric | head -40")["out"]
    out["nvswitch_lspci"] = sh("lspci 2>/dev/null | grep -i -c nvswitch")["out"].strip()
    if tools["dcgmi"]:
        out["dcgm_discovery"] = sh("dcgmi discovery -l", 30)
        out["dcgm_prof"] = sh("dcgmi dmon -e 1002,1003,1004,1005,1009,1010,1011,1012 -c 3 -d 500", 30)
    if tools["nsys"]:
        tmp = tempfile.mkdtemp(prefix="hwprobe_nsys_")
        out["nsys_gpu_metrics"] = sh(f"cd {tmp} && nsys profile --gpu-metrics-devices=all -o probe -f true "
                                     f"--duration=2 sleep 3 2>&1 | tail -15", 120)
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ------------------------------------------------------------------------------------------ nvml
def probe_nvml(indices: list[int]) -> dict:
    g = hs.Gpus(indices)
    out = {"ok": g.ok, "errors": g.errors, "gpus": g.ident}
    try:
        out["nvml_count"] = g.nv.nvmlDeviceGetCount() if g.ok else None
        out["driver"] = g.nv.nvmlSystemGetDriverVersion() if g.ok else None
    except Exception as exc:
        out["count_err"] = str(exc)
    if g.ok:
        fab = []
        for h in g.handles:
            try:   # pynvml passes this struct by value (segfault, J0 115550): hand it a pointer ourselves
                import ctypes
                info = g.nv.c_nvmlGpuFabricInfo_t()
                g.nv.nvmlDeviceGetGpuFabricInfo(h, ctypes.byref(info))
                fab.append({"state": int(info.state), "status": int(info.status)})
            except Exception as exc:
                fab.append({"err": str(exc)[:100]})
        out["fabric_info"] = fab
        out["first_fast"] = g.fast()
        time.sleep(0.2)
        out["second_fast"] = g.fast()
        out["slow"] = g.slow()
    if os.environ.get("HW_PROBE_NO_TORCH"):        # GPUs busy with servers: do not create CUDA contexts
        return out
    try:
        import torch
        out["torch_uuids"] = [str(torch.cuda.get_device_properties(i).uuid) for i in range(torch.cuda.device_count())]
        nv_uuids = [d.get("uuid", "") for d in g.ident]
        out["uuid_match"] = [nv.replace("GPU-", "") == tu.replace("GPU-", "")
                             for nv, tu in zip(nv_uuids, out["torch_uuids"])]
    except Exception as exc:
        out["torch_err"] = str(exc)[:200]
    return out


# ------------------------------------------------------------------------------------------- cpu
DRIVER = r'''
import json, os, resource, subprocess, sys, time
pidfile = sys.argv[1]
open(pidfile + ".tmp", "w").write(str(os.getpid())); os.replace(pidfile + ".tmp", pidfile)
time.sleep(1.0)
def burn(sec):
    t = time.process_time()
    while time.process_time() - t < sec:
        sum(i * i for i in range(1000))
burn(2.0)
for _ in range(400):
    subprocess.run(["grep", "-c", "root", "/etc/passwd"], stdout=subprocess.DEVNULL)
child = ("import subprocess,time\nt=time.process_time()\nwhile time.process_time()-t<2.0: sum(i*i for i in range(1000))\n"
         "[subprocess.run(['grep','-c','root','/etc/passwd'],stdout=subprocess.DEVNULL) for _ in range(200)]\n")
subprocess.run([sys.executable, "-c", child])
time.sleep(1.5)
s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({"self": s.ru_utime + s.ru_stime, "children": c.ru_utime + c.ru_stime}))
'''


def probe_cpu(work: Path) -> dict:
    out: dict = {}
    work.mkdir(parents=True, exist_ok=True)
    pidfile, samp = work / "driver.pid", work / "samp_cpu"
    shutil.rmtree(samp, ignore_errors=True)
    sampler = subprocess.Popen([sys.executable, str(DIR / "hw_sampler.py"), "run", "--out", str(samp),
                                "--driver-pid-file", str(pidfile), "--duration", "30"])
    time.sleep(1.0)
    truth = json.loads(subprocess.run([sys.executable, "-c", DRIVER, str(pidfile)], capture_output=True,
                                      text=True, check=True).stdout)
    time.sleep(2.0)
    sampler.terminate()
    sampler.wait(timeout=30)
    rows = [json.loads(line) for line in (samp / "host.jsonl").open()]
    last = rows[-1]["cpu"]
    total = truth["self"] + truth["children"]
    out["tree_accounting"] = {"measured_s": last.get("agent_total"), "truth_s": round(total, 3),
                              "driver_own_s": last.get("driver"), "truth_self_s": round(truth["self"], 3),
                              "max_live_tools_seen": max(r["pids"]["n_tools"] for r in rows),
                              "pass": within(last.get("agent_total"), total)}
    # per-core busy of a pinned 2-s burn
    cpus = sorted(os.sched_getaffinity(0))
    core = cpus[-1]
    before = hs.parse_proc_stat(hs.read_text("/proc/stat"), [core])
    t0 = time.monotonic()
    subprocess.run(["taskset", "-c", str(core), sys.executable, "-c",
                    "import time\nt=time.monotonic()\nwhile time.monotonic()-t<2.0: pass"], check=True)
    after = hs.parse_proc_stat(hs.read_text("/proc/stat"), [core])
    a, b = before[core], after[core]
    busy_s = (b[0] - a[0]) / hs.CLK
    out["core_busy"] = {"core": core, "busy_s": round(busy_s, 3), "wall_s": round(time.monotonic() - t0, 3),
                        "pass": within(busy_s, 2.0, 0.1)}
    # I/O of a reaped child reaches the parent's /proc/<pid>/io
    data = work / "io_test.bin"
    with data.open("wb") as handle:
        handle.write(os.urandom(64 * 2**20))
    before_io = hs.read_kv(f"/proc/{os.getpid()}/io")
    subprocess.run([sys.executable, "-c", f"open({str(data)!r},'rb').read()"], check=True)
    after_io = hs.read_kv(f"/proc/{os.getpid()}/io")
    got = after_io.get("rchar", 0) - before_io.get("rchar", 0)
    out["io_reaped_into_parent"] = {"rchar_delta": got, "expected_min": 64 * 2**20, "pass": got >= 64 * 2**20}
    data.unlink(missing_ok=True)
    return out


# ------------------------------------------------------------------------------------------- gpu
def _slow(g: hs.Gpus) -> list[dict]:
    return g.slow()


def probe_gpu(indices: list[int]) -> dict:
    import torch
    out: dict = {}
    g = hs.Gpus(indices)
    if not g.ok or not torch.cuda.is_available():
        return {"skipped": "no GPU"}
    dev0 = torch.device("cuda:0")
    torch.cuda.init()
    torch.zeros(1, device=dev0)                      # create the context before measuring
    torch.cuda.synchronize()
    # HBM: a known allocation
    before = _slow(g)[0].get("used_mib")
    x = torch.empty(8 * GIB, dtype=torch.uint8, device=dev0)
    x.fill_(1)
    torch.cuda.synchronize()
    time.sleep(1.0)
    after = _slow(g)[0].get("used_mib")
    del x
    torch.cuda.empty_cache()
    grown = (after - before) / 1024 if before is not None and after is not None else None
    out["hbm_alloc"] = {"before_mib": before, "after_mib": after, "grown_gib": grown, "expected_gib": 8,
                        "pass": within(grown, 8.0)}

    def gpm_during(enqueue, seconds: float) -> list[dict]:
        """GPM samples every 100 ms (the sampler's cadence; GPM over ~20-ms intervals is meaningless, J1 probe)
        while `enqueue` keeps the GPU queue full (launches block when the queue is full, so it paces itself)."""
        torch.cuda.synchronize()
        g.fast()
        samples, t_end, nxt = [], time.monotonic() + seconds, time.monotonic() + 0.1
        while time.monotonic() < t_end:
            enqueue()
            if time.monotonic() >= nxt:
                samples.append(g.fast()[0])
                nxt += 0.1
        torch.cuda.synchronize()
        return samples[2:-1]                         # drop the ramp-up and the drain

    def mean_of(samples, key):
        vals = [s[key] for s in samples if s.get(key) is not None and s[key] == s[key]]
        return round(sum(vals) / len(vals), 2) if vals else None

    a = torch.randn(8192, 8192, dtype=torch.bfloat16, device=dev0)
    b = torch.randn(8192, 8192, dtype=torch.bfloat16, device=dev0)
    mm = gpm_during(lambda: a @ b, 4.0)
    src = torch.empty(2 * GIB, dtype=torch.uint8, device=dev0)
    dst = torch.empty_like(src)
    cp = gpm_during(lambda: dst.copy_(src), 4.0)
    out["gpm_sanity"] = {k: {m: mean_of(s, m) for m in ("gr", "util", "mutil", "sm", "occ", "tensor", "hmma", "dram")}
                         | {"samples": len(s)} for k, s in (("matmul_bf16", mm), ("device_copy", cp))}
    del a, b, src, dst
    torch.cuda.empty_cache()

    # PCIe: pinned H2D / D2H. Throughput from timed synchronous copies; GPM rates at 100 ms during a continuous
    # stream of copies; the NVML byte counter polled every 10 ms with 32-bit wrap correction (it wraps at 2^32
    # and counts raw link bytes, ~8 % above payload: J1 probe).
    host = torch.empty(1 * GIB, dtype=torch.uint8).pin_memory()
    devbuf = torch.empty(1 * GIB, dtype=torch.uint8, device=dev0)
    for name, fn, key, gkey in (("h2d", lambda: devbuf.copy_(host, non_blocking=True), "pcie_rx_b", "pcie_rx"),
                                ("d2h", lambda: host.copy_(devbuf, non_blocking=True), "pcie_tx_b", "pcie_tx")):
        fn()
        torch.cuda.synchronize()
        t0 = time.monotonic()
        for _ in range(16):
            fn()
        torch.cuda.synchronize()
        gb_s = 16 * GIB / (time.monotonic() - t0) / 1e9
        rates = gpm_during(fn, 3.0)
        gpm_mib = mean_of(rates, gkey)
        # counter over a measured burst, polled every 10 ms (one 10-ms slice < 4 GiB, so at most one wrap)
        h = g.handles[0]
        def raw():
            v = g.nv.nvmlDeviceGetFieldValues(h, [197 if key == "pcie_tx_b" else 198])[0]
            return int(v.value.ullVal)
        total, last = 0, raw()
        t_end = time.monotonic() + 2.0
        n = 0
        while time.monotonic() < t_end:
            fn()
            n += 1
            torch.cuda.synchronize()
            cur = raw()
            total += (cur - last) % 2**32
            last = cur
        out[f"pcie_{name}"] = {"gb_s": round(gb_s, 2), "gpm_mean_gb_s": round(gpm_mib * 2**20 / 1e9, 2) if gpm_mib else None,
                               "gpm_vs_measured": round(gpm_mib * 2**20 / 1e9 / gb_s, 3) if gpm_mib else None,
                               "counter_bytes": total, "payload_bytes": n * GIB,
                               "counter_over_payload": round(total / (n * GIB), 3)}
    del host, devbuf
    torch.cuda.empty_cache()

    # NVLink: peer copy GPU0 -> GPU1, then NCCL all-reduce
    if torch.cuda.device_count() >= 2 and len(g.handles) >= 2:
        dev1 = torch.device("cuda:1")
        out["p2p_access"] = bool(torch.cuda.can_device_access_peer(0, 1))
        s_src = torch.empty(2 * GIB, dtype=torch.uint8, device=dev0)
        s_dst = torch.empty(2 * GIB, dtype=torch.uint8, device=dev1)
        s_dst.copy_(s_src)
        torch.cuda.synchronize(dev0)
        torch.cuda.synchronize(dev1)
        b0 = _slow(g)
        g.fast()
        t0 = time.monotonic()
        rates = []
        for _ in range(10):
            s_dst.copy_(s_src)
            torch.cuda.synchronize(dev1)
            rates.append(g.fast())
        dt = time.monotonic() - t0
        b1 = _slow(g)
        moved = 10 * 2 * GIB

        def delta(k, key, scale=1):
            return ((b1[k][key] - b0[k][key]) * scale) if key in b0[k] and key in b1[k] else None
        tx0, rx1 = delta(0, "nvl_tx_kib", 1024), delta(1, "nvl_rx_kib", 1024)
        out["nvlink_p2p"] = {"gb_s": round(moved / dt / 1e9, 2), "bytes": moved, "gpu0_tx_bytes": tx0,
                             "gpu1_rx_bytes": rx1, "tx_pass": within(tx0, moved) if tx0 is not None else None,
                             "rx_pass": within(rx1, moved) if rx1 is not None else None,
                             "gpm_gpu0_nvl_tx_mib_s": [r[0].get("nvl_tx") for r in rates][:5],
                             "gpm_gpu1_nvl_rx_mib_s": [r[1].get("nvl_rx") for r in rates][:5],
                             "pcie_bytes_during": {"gpu0_tx": delta(0, "pcie_tx_b"), "gpu1_rx": delta(1, "pcie_rx_b")}}
        del s_src, s_dst
        torch.cuda.empty_cache()
        out["nccl_allreduce"] = nccl_sweep(g)
    return out


NCCL_WORKER = r'''
import json, os, sys, time, torch, torch.distributed as dist
rank, world, port, out = int(sys.argv[1]), 2, sys.argv[2], sys.argv[3]
os.environ.update(MASTER_ADDR="127.0.0.1", MASTER_PORT=port)
torch.cuda.set_device(rank)
dist.init_process_group("nccl", rank=rank, world_size=world, device_id=torch.device(f"cuda:{rank}"))
res = []
for mib in (1, 8, 64, 256, 1024):
    x = torch.ones(mib * 2**20 // 2, dtype=torch.bfloat16, device="cuda")
    for _ in range(3):
        dist.all_reduce(x)
    torch.cuda.synchronize()
    iters = max(5, min(50, 2048 // mib))
    dist.barrier(); torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        dist.all_reduce(x)
    torch.cuda.synchronize()
    dt = (time.perf_counter() - t0) / iters
    algbw = mib * 2**20 / dt / 1e9
    res.append({"mib": mib, "iters": iters, "us": round(dt * 1e6, 1), "algbw_gb_s": round(algbw, 2),
                "busbw_gb_s": round(algbw * 2 * (world - 1) / world, 2), "t_wall": time.time()})
if rank == 0:
    json.dump(res, open(out, "w"))
dist.destroy_process_group()
'''


def nccl_sweep(g: hs.Gpus) -> dict:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = str(s.getsockname()[1])
    tmp = Path(tempfile.mkdtemp(prefix="hwprobe_nccl_"))
    out_file = tmp / "res.json"
    b0 = g.slow()
    procs = [subprocess.Popen([sys.executable, "-c", NCCL_WORKER, str(r), port, str(out_file)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for r in range(2)]
    logs = [p.communicate(timeout=600)[0][-1500:] for p in procs]
    b1 = g.slow()
    res = json.loads(out_file.read_text()) if out_file.exists() else None
    shutil.rmtree(tmp, ignore_errors=True)
    nvl = [((b1[k].get("nvl_tx_kib", 0) - b0[k].get("nvl_tx_kib", 0)) * 1024) for k in range(2)]
    pcie = [((b1[k].get("pcie_tx_b", 0) - b0[k].get("pcie_tx_b", 0))) for k in range(2)]
    expected = None
    if res:
        # ring all-reduce on 2 ranks: each rank sends 2(n-1)/n * size = size bytes per call (+ warm-up)
        expected = sum((r["iters"] + 3) * r["mib"] * 2**20 for r in res)
    return {"results": res, "rc": [p.returncode for p in procs], "log_tail": logs if any(p.returncode for p in procs) else None,
            "nvl_tx_bytes": nvl, "pcie_tx_bytes": pcie, "expected_tx_bytes_per_gpu": expected,
            "nvl_pass": [within(v, expected, 0.15) for v in nvl] if expected else None,
            "peak_busbw_gb_s": max((r["busbw_gb_s"] for r in res), default=None) if res else None}


# --------------------------------------------------------------------------------------- sampler
def probe_sampler(indices: list[int], work: Path) -> dict:
    samp = work / "samp_idle"
    shutil.rmtree(samp, ignore_errors=True)
    subprocess.run([sys.executable, str(DIR / "hw_sampler.py"), "run", "--out", str(samp),
                    "--gpus", ",".join(map(str, indices)), "--duration", "20"], check=True)
    meta = json.loads((samp / "meta.json").read_text())
    fast = sum(1 for _ in (samp / "fast.jsonl").open())
    return {"sampler_cores": meta.get("sampler_cores"), "fast_records": fast,
            "pass": (meta.get("sampler_cores") or 1) < 0.1}


# ------------------------------------------------------------------------------------------ main
def summary(res: dict) -> list[str]:
    lines = []
    env, nv = res.get("env", {}), res.get("nvml", {})
    lines.append(f"host {env.get('host')} kernel {env.get('kernel')} cgroup v{(env.get('cgroup') or {}).get('ver')} "
                 f"perf={bool((env.get('tools') or {}).get('perf'))} paranoid={env.get('perf_event_paranoid')}")
    for d in nv.get("gpus", []):
        lines.append(f"GPU{d.get('index')} {d.get('name')} gpm={d.get('gpm')} pcie_counters={d.get('pcie_counters')} "
                     f"nvlinks={len(d.get('nvlinks_active') or [])} PCIe gen{d.get('pcie_gen_max')}x{d.get('pcie_width_max')}")
    lines.append(f"uuid match NVML<->torch: {nv.get('uuid_match')}")
    cpu = res.get("cpu", {})
    for k in ("tree_accounting", "core_busy", "io_reaped_into_parent"):
        lines.append(f"cpu {k}: {cpu.get(k)}")
    gpu = res.get("gpu", {})
    for k in ("hbm_alloc", "gpm_sanity", "pcie_h2d", "pcie_d2h", "nvlink_p2p"):
        lines.append(f"gpu {k}: {gpu.get(k)}")
    nc = gpu.get("nccl_allreduce") or {}
    lines.append(f"nccl peak busbw {nc.get('peak_busbw_gb_s')} GB/s, nvl pass {nc.get('nvl_pass')}")
    lines.append(f"sampler: {res.get('sampler')}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["all", "env", "nvml", "cpu", "gpu", "sampler"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--work", default=None, help="scratch directory (default: node-local tmp)")
    args = ap.parse_args()
    indices = hs.parse_cpulist(args.gpus)
    work = Path(args.work or tempfile.mkdtemp(prefix="hwprobe_"))
    res: dict = {"started": time.strftime("%FT%T"), "t0": time.time()}
    steps = ["env", "nvml", "cpu", "gpu", "sampler"] if args.what == "all" else [args.what]
    for step in steps:
        t0 = time.monotonic()
        try:
            if step == "env":
                res["env"] = probe_env()
            elif step == "nvml":
                res["nvml"] = probe_nvml(indices)
            elif step == "cpu":
                res["cpu"] = probe_cpu(work)
            elif step == "gpu":
                res["gpu"] = probe_gpu(indices)
            elif step == "sampler":
                res["sampler"] = probe_sampler(indices, work)
        except Exception as exc:
            import traceback
            res[step] = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-3000:]}
        res.setdefault("seconds", {})[step] = round(time.monotonic() - t0, 1)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    for line in summary(res):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
