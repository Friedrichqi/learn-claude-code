#!/usr/bin/env python3
"""Tier calibration for research/13_tiered_memory (S2 on one RTX PRO 6000, S4 on an H200 pair): what moving
KV costs between each pair of tiers on the allocated node. Microbenchmarks only calibrate; every result
the README draws a conclusion from comes from live harness runs.

    python research/13_tiered_memory/tm_calib.py all    --out DIR [--flash-dirs /tmp,...] [--gib 8]
    python research/13_tiered_memory/tm_calib.py sys    --out DIR   # node facts: CPU, GPU, PCIe link, disks, memory
    python research/13_tiered_memory/tm_calib.py pcie   --out DIR   # one large pinned / pageable copy, H2D and D2H
    python research/13_tiered_memory/tm_calib.py blocks --out DIR   # vLLM's own block-copy kernel (swap_blocks_batch)
    python research/13_tiered_memory/tm_calib.py flash  --out DIR   # O_DIRECT, one big file and fs-tier-like 4 MiB files
    python research/13_tiered_memory/tm_calib.py peer   --out DIR   # >= 2 GPUs: peer copies over NVLink (S4)

`blocks` is the number that matters for B1/B2: vLLM's CPU offload moves KV as one pointer copy per layer per
K/V page (32 KiB for both Qwen3-32B and -8B: 16 tokens x 8 KV heads x 128 x 2 bytes), submitted in one
cuMemcpyBatchAsync call (vllm/v1/kv_offload/cpu/gpu_worker.py). 12's o1 restored at 8.9 GB/s, far below
the link; this measures the kernel alone at each chunk size. Each step writes DIR/calib_<what>.json.
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

GiB = 1 << 30
MiB = 1 << 20
KiB = 1 << 10


def sh(cmd: str, timeout: float = 30.0) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception as exc:  # a missing tool must not stop the probe
        return f"ERROR {type(exc).__name__}: {exc}"


def save(out: Path, what: str, obj: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"calib_{what}.json").write_text(json.dumps(obj, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"[calib] {what}: {json.dumps(obj, default=str)[:1500]}", flush=True)


# -- sys --------------------------------------------------------------------------------------------------------
def do_sys(args) -> dict:
    mounts = []
    for line in sh("df -PT -B1 2>/dev/null").splitlines()[1:]:
        f = line.split()
        if len(f) >= 7:
            mounts.append({"fs": f[0], "type": f[1], "size_gib": round(int(f[2]) / GiB, 1),
                           "avail_gib": round(int(f[4]) / GiB, 1), "mount": f[6]})
    gpus = sh("nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.gen.max,"
              "pcie.link.width.current,pcie.link.width.max,power.limit --format=csv,noheader")
    return {"host": socket.gethostname(), "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "cpus_allowed": len(os.sched_getaffinity(0)), "lscpu": sh("lscpu"), "gpus": gpus,
            "topo": sh("nvidia-smi topo -m"), "lsblk": sh("lsblk -d -o NAME,ROTA,TRAN,MODEL,SIZE,TYPE"),
            "lsblk_mounts": sh("lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINT"), "mounts": mounts,
            "free_gib": sh("free -g"), "numa": sh("numactl -H"), "kernel": sh("uname -r"),
            "cgroup_mem_max": sh("cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.max"),
            "shm": sh("df -h /dev/shm")}


# -- pcie -------------------------------------------------------------------------------------------------------
def _time_copy(torch, dst, src, reps: int = 5) -> float:
    """Median seconds of dst.copy_(src) on the current stream."""
    times = []
    for _ in range(reps + 1):
        a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        a.record()
        dst.copy_(src, non_blocking=True)
        b.record()
        b.synchronize()
        times.append(a.elapsed_time(b) / 1000)
    return statistics.median(times[1:])


def do_pcie(args) -> dict:
    import torch
    dev = torch.device("cuda:0")
    out = {"device": torch.cuda.get_device_name(0), "rows": []}
    for size in (64 * MiB, 256 * MiB, 1 * GiB, 4 * GiB):
        n = size // 2
        g = torch.empty(n, dtype=torch.bfloat16, device=dev)
        for pinned in (True, False):
            h = torch.empty(n, dtype=torch.bfloat16, pin_memory=pinned)
            h.fill_(1)
            h2d = _time_copy(torch, g, h)
            d2h = _time_copy(torch, h, g)
            out["rows"].append({"bytes": size, "pinned": pinned, "h2d_gbs": round(size / h2d / 1e9, 2),
                                "d2h_gbs": round(size / d2h / 1e9, 2)})
            del h
        del g
        torch.cuda.empty_cache()
    return out


# -- blocks: vLLM's swap_blocks_batch ---------------------------------------------------------------------------
def _batch_rate(torch, ops, src_base: int, dst_base: int, chunk: int, n: int, span: int, h2d: bool,
                seed: int = 0) -> float:
    """GB/s of one swap_blocks_batch call moving n chunks between random slots of two `span`-byte buffers."""
    g = torch.Generator().manual_seed(seed)
    slots = span // chunk
    src_idx = torch.randperm(slots, generator=g)[:n].to(torch.int64)
    dst_idx = torch.randperm(slots, generator=g)[:n].to(torch.int64)
    src = (src_idx * chunk + src_base).contiguous()
    dst = (dst_idx * chunk + dst_base).contiguous()
    sizes = torch.full((n,), chunk, dtype=torch.int64)
    stream = torch.cuda.Stream()
    times = []
    for _ in range(4):
        a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        with torch.cuda.stream(stream):
            a.record(stream)
            ops.swap_blocks_batch(src, dst, sizes, is_src_access_order_any=h2d)
            b.record(stream)
        b.synchronize()
        times.append(a.elapsed_time(b) / 1000)
    return n * chunk / statistics.median(times[1:]) / 1e9


def do_blocks(args) -> dict:
    import torch
    from vllm import _custom_ops as ops
    span = args.block_gib * GiB
    gpu = torch.empty(span, dtype=torch.uint8, device="cuda:0")
    host = torch.empty(span, dtype=torch.uint8, pin_memory=True)
    host.fill_(7)
    out = {"device": torch.cuda.get_device_name(0), "span_bytes": span, "rows": []}
    for chunk in (32 * KiB, 64 * KiB, 256 * KiB, 1 * MiB, 4 * MiB):
        n = min(span // chunk, (2 * GiB) // chunk)
        h2d = _batch_rate(torch, ops, host.data_ptr(), gpu.data_ptr(), chunk, n, span, True)
        d2h = _batch_rate(torch, ops, gpu.data_ptr(), host.data_ptr(), chunk, n, span, False)
        out["rows"].append({"chunk_bytes": chunk, "copies": n, "h2d_gbs": round(h2d, 2), "d2h_gbs": round(d2h, 2)})
    # one 23k-token Qwen3-32B restore: 1,438 blocks x 64 layers x (K, V) pages of 32 KiB = 5.6 GiB
    n = 1438 * 64 * 2
    if n * 32 * KiB <= span:
        t = n * 32 * KiB / _batch_rate(torch, ops, host.data_ptr(), gpu.data_ptr(), 32 * KiB, n, span, True) / 1e9
        out["restore_23k_32b_s"] = round(t, 4)
    return out


# -- flash ------------------------------------------------------------------------------------------------------
def _aligned(size: int) -> mmap.mmap:
    buf = mmap.mmap(-1, size)        # page-aligned anonymous memory, as O_DIRECT needs
    buf.write(os.urandom(min(size, 4 * MiB)) * (size // min(size, 4 * MiB)))
    return buf


def _direct_ok(path: Path) -> bool:
    try:
        fd = os.open(path / ".odirect_probe", os.O_CREAT | os.O_WRONLY | os.O_DIRECT, 0o600)
        os.close(fd)
        os.unlink(path / ".odirect_probe")
        return True
    except OSError:
        return False


def _drop(fd: int) -> None:
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass


def flash_dir(path: Path, gib: int) -> dict:
    res = {"dir": str(path)}
    try:
        st = os.statvfs(path)
    except OSError as exc:
        return {**res, "error": repr(exc)}
    avail = st.f_bavail * st.f_frsize
    res["avail_gib"] = round(avail / GiB, 1)
    res["fs"] = sh(f"df -PT {path} | tail -1 | awk '{{print $1, $2}}'")
    if avail < 2 * gib * GiB:
        gib = max(1, int(avail / GiB / 3))
    if gib < 1:
        return {**res, "error": "no space"}
    work = path / f"tm_calib_{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    direct = _direct_ok(work)
    res["o_direct"] = direct
    flags = os.O_DIRECT if direct else 0
    blk = 4 * MiB
    buf = _aligned(blk)
    try:
        # one big file, sequential
        total = gib * GiB
        f = work / "big"
        fd = os.open(f, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | flags, 0o600)
        t0 = time.perf_counter()
        for _ in range(total // blk):
            os.write(fd, buf)
        os.fsync(fd)
        res["seq_write_gbs"] = round(total / (time.perf_counter() - t0) / 1e9, 2)
        _drop(fd)
        os.close(fd)
        fd = os.open(f, os.O_RDONLY | flags)
        t0 = time.perf_counter()
        while os.readv(fd, [buf]) > 0:
            pass
        res["seq_read_gbs"] = round(total / (time.perf_counter() - t0) / 1e9, 2)
        os.close(fd)
        f.unlink()
        # fs-tier-like: one 4 MiB file per KV block (a Qwen3-32B block of 16 tokens is 4 MiB over all layers)
        nfiles = min(1024, total // blk)
        t0 = time.perf_counter()
        for i in range(nfiles):
            d = work / f"{i % 64:02x}"
            d.mkdir(exist_ok=True)
            tmp = d / f"{i}.tmp"
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | flags, 0o600)
            os.write(fd, buf)
            os.close(fd)
            os.replace(tmp, d / f"{i}.kv")
        os.sync()
        res["files_write_gbs"] = round(nfiles * blk / (time.perf_counter() - t0) / 1e9, 2)
        order = list(range(nfiles))
        import random
        random.Random(0).shuffle(order)
        t0 = time.perf_counter()
        for i in order:
            fd = os.open(work / f"{i % 64:02x}" / f"{i}.kv", os.O_RDONLY | flags)
            _drop(fd)
            os.readv(fd, [buf])
            os.close(fd)
        res["files_read_gbs"] = round(nfiles * blk / (time.perf_counter() - t0) / 1e9, 2)
        res["files"] = nfiles
        res["gib"] = gib
    except OSError as exc:
        res["error"] = repr(exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return res


def do_flash(args) -> dict:
    dirs = [d for d in (args.flash_dirs or "").split(",") if d]
    if not dirs:
        dirs = ["/tmp", "/dev/shm"] + [m for m in ("/scratch", "/local", "/localscratch", "/tmp/local") if os.path.isdir(m)]
    return {"rows": [flash_dir(Path(d), args.gib) for d in dirs]}


# -- peer (S4) --------------------------------------------------------------------------------------------------
def enable_peer(a: int, b: int) -> str:
    """cudaDeviceEnablePeerAccess both ways through the CUDA runtime that torch loaded."""
    import ctypes
    import torch
    lib = None
    for name in ("libcudart.so", "libcudart.so.13", "libcudart.so.12"):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if lib is None:
        return "no libcudart"
    out = []
    for src, dst in ((a, b), (b, a)):
        torch.cuda.set_device(src)
        rc = lib.cudaDeviceEnablePeerAccess(ctypes.c_int(dst), ctypes.c_uint(0))
        out.append(f"{src}->{dst}:{rc}")          # 0 ok, 704 already enabled
    torch.cuda.set_device(a)
    return " ".join(out)


def do_peer(args) -> dict:
    import torch
    n = torch.cuda.device_count()
    if n < 2:
        return {"skipped": f"{n} GPU visible"}
    from vllm import _custom_ops as ops
    out = {"can_access": torch.cuda.can_device_access_peer(0, 1), "enable": enable_peer(0, 1), "rows": []}
    size = 2 * GiB
    a = torch.empty(size, dtype=torch.uint8, device="cuda:0")
    b = torch.empty(size, dtype=torch.uint8, device="cuda:1")
    torch.cuda.set_device(0)
    out["copy_0to1_gbs"] = round(size / _time_copy(torch, b, a) / 1e9, 2)
    out["copy_1to0_gbs"] = round(size / _time_copy(torch, a, b) / 1e9, 2)
    for chunk in (32 * KiB, 64 * KiB, 256 * KiB, 4 * MiB):
        cnt = size // chunk // 2
        r = _batch_rate(torch, ops, b.data_ptr(), a.data_ptr(), chunk, cnt, size, True)   # peer -> local (restore)
        w = _batch_rate(torch, ops, a.data_ptr(), b.data_ptr(), chunk, cnt, size, False)  # local -> peer (spill)
        out["rows"].append({"chunk_bytes": chunk, "copies": cnt, "peer_to_local_gbs": round(r, 2),
                            "local_to_peer_gbs": round(w, 2)})
    return out


STEPS = {"sys": do_sys, "pcie": do_pcie, "blocks": do_blocks, "flash": do_flash, "peer": do_peer}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=[*STEPS, "all"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--flash-dirs", default="")
    ap.add_argument("--gib", type=int, default=8, help="flash test size per directory")
    ap.add_argument("--block-gib", type=int, default=8, help="buffer size for the block-copy test")
    args = ap.parse_args()
    out = Path(args.out)
    for what in (STEPS if args.what == "all" else [args.what]):
        t0 = time.time()
        try:
            res = STEPS[what](args)
        except Exception as exc:          # one failing step must not lose the others
            import traceback
            res = {"error": repr(exc), "tb": traceback.format_exc()[-3000:]}
        res["seconds"] = round(time.time() - t0, 1)
        save(out, what, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
