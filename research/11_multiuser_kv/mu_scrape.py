#!/usr/bin/env python3
"""1 Hz poller for one cell of the 11_multiuser_kv experiment: vLLM /metrics, the GPU, and the CPU.

Every second (drift-corrected) it appends one JSON line with
  * vLLM gauges and counters, summed over label sets, plus the label splits that matter here:
    num_requests_waiting_by_reason{capacity,deferred}, prompt_tokens_by_source{local_compute,
    local_cache_hit,external_kv_transfer}, request_success{finished_reason};
  * histogram _sum/_count pairs (TTFT, inter-token latency, e2e, queue, prefill, decode,
    kv_block_idle_before_evict / lifetime / reuse_gap);
  * GPU utilisation, memory, power, SM clock and temperature (pynvml);
  * CPU seconds of the vLLM API server, its EngineCore child, and the cell's session processes
    (everything under --sweep-pid), and the load average (psutil).
On SIGTERM it saves the final /metrics text (all buckets) next to the output and exits.

`kvevents` subscribes to vLLM's KV-cache event stream (--kv-events-config, ZMQ) and counts
BlockStored / BlockRemoved events and hashes, with sequence gaps, so the smoke test can check
mu_scheduler's commit and eviction counts against vLLM's own events.

    python research/11_multiuser_kv/mu_scrape.py run --metrics-url http://127.0.0.1:8601/metrics --gpu 0 \
        --out DIR/scrape.jsonl --server-pid PID --sweep-pid PID
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
import urllib.request

LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eEinfaINFA]+)")
LABEL = re.compile(r'(\w+)="([^"]*)"')
SPLIT = {"vllm:num_requests_waiting_by_reason": "reason", "vllm:prompt_tokens_by_source_total": "source",
         "vllm:request_success_total": "finished_reason"}
KEEP_PREFIX = "vllm:"
DROP = ("_bucket",)


def parse_metrics(text: str) -> dict:
    """Prometheus text -> {name: value summed over label sets, 'name{label}': split values}."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        m = LINE.match(line)
        if not m:
            continue
        name, labels, value = m.group(1), m.group(2) or "", m.group(3)
        if not name.startswith(KEEP_PREFIX) or name.endswith(DROP):
            continue
        try:
            v = float(value)
        except ValueError:
            continue
        out[name] = out.get(name, 0.0) + v
        key = SPLIT.get(name)
        if key:
            lab = dict(LABEL.findall(labels)).get(key)
            if lab is not None:
                split = f"{name}{{{lab}}}"
                out[split] = out.get(split, 0.0) + v
    return out


def fetch(url: str, timeout: float = 5.0) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except Exception:
        return None


class GPU:
    def __init__(self, index: int):
        self.handle = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        except Exception:
            self.handle = None

    def sample(self) -> dict | None:
        if self.handle is None:
            return None
        nv = self.nv
        try:
            util = nv.nvmlDeviceGetUtilizationRates(self.handle)
            mem = nv.nvmlDeviceGetMemoryInfo(self.handle)
            return {"util": util.gpu, "mem_util": util.memory, "mem_used_gb": round(mem.used / 2**30, 2),
                    "power_w": round(nv.nvmlDeviceGetPowerUsage(self.handle) / 1000, 1),
                    "sm_mhz": nv.nvmlDeviceGetClockInfo(self.handle, nv.NVML_CLOCK_SM),
                    "temp_c": nv.nvmlDeviceGetTemperature(self.handle, nv.NVML_TEMPERATURE_GPU)}
        except Exception:
            return None


class CPU:
    """Cumulative CPU seconds of the server tree (API server + EngineCore) and of the sessions."""

    def __init__(self, server_pid: int | None, sweep_pid: int | None):
        import psutil
        self.psutil = psutil
        self.server = psutil.Process(server_pid) if server_pid else None
        self.sweep = psutil.Process(sweep_pid) if sweep_pid else None
        self.dead: dict[str, float] = {"sessions": 0.0}
        self.seen: dict[int, float] = {}

    @staticmethod
    def _cpu(proc) -> float:
        try:
            t = proc.cpu_times()
            return t.user + t.system
        except Exception:
            return 0.0

    def sample(self) -> dict:
        out = {"load1": round(os.getloadavg()[0], 2)}
        if self.server is not None:
            try:
                kids = self.server.children(recursive=True)
                out["api_s"] = round(self._cpu(self.server), 2)
                engine = [k for k in kids if "EngineCore" in " ".join(k.cmdline() or [k.name()])]
                out["engine_s"] = round(sum(self._cpu(k) for k in engine), 2)
                out["server_threads"] = self.server.num_threads()
            except Exception:
                pass
        if self.sweep is not None:
            try:
                procs = self.sweep.children(recursive=True)
                now = {p.pid: self._cpu(p) for p in procs}
                for pid, secs in self.seen.items():      # finished sessions keep their CPU time
                    if pid not in now:
                        self.dead["sessions"] += secs
                self.seen = now
                out["sessions_s"] = round(self.dead["sessions"] + sum(now.values()), 2)
                out["sessions_n"] = len(procs)
            except Exception:
                pass
        return out


def run(args) -> int:
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    gpu = GPU(args.gpu)
    cpu = CPU(args.server_pid, args.sweep_pid)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    period = args.period
    next_t = time.monotonic()
    with open(args.out, "a", encoding="utf-8") as handle:
        while not stop["flag"]:
            t, wall = time.monotonic(), time.time()
            text = fetch(args.metrics_url)
            record = {"t": t, "wall": wall, "ok": text is not None,
                      "m": parse_metrics(text) if text else None, "gpu": gpu.sample(), "cpu": cpu.sample(),
                      "scrape_ms": round((time.monotonic() - t) * 1000, 1)}
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            next_t += period
            delay = next_t - time.monotonic()
            if delay < 0:
                next_t = time.monotonic()
                delay = 0
            end = time.monotonic() + delay
            while not stop["flag"] and time.monotonic() < end:
                time.sleep(min(0.1, max(0.0, end - time.monotonic())))
    final = fetch(args.metrics_url, timeout=15)
    if final:
        with open(os.path.join(os.path.dirname(os.path.abspath(args.out)), "metrics_final.prom"), "w") as f:
            f.write(final)
    return 0


def kvevents(args) -> int:
    import msgspec
    import zmq
    from vllm.distributed.kv_events import KVEventBatch

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    decoder = msgspec.msgpack.Decoder(type=KVEventBatch)
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(args.endpoint)
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    counts = {"batches": 0, "gaps": 0, "BlockStored": 0, "BlockRemoved": 0, "AllBlocksCleared": 0,
              "stored_hashes": 0, "removed_hashes": 0, "by_medium": {}}
    last_seq, last_write = None, 0.0

    def dump():
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(counts, handle, indent=1)

    while not stop["flag"]:
        if not sock.poll(500):
            continue
        frames = sock.recv_multipart()
        if len(frames) != 3:
            continue
        seq = int.from_bytes(frames[1], "big")
        if last_seq is not None and seq != last_seq + 1:
            counts["gaps"] += 1
        last_seq = seq
        batch = decoder.decode(frames[2])
        counts["batches"] += 1
        for event in batch.events:
            name = type(event).__name__
            counts[name] = counts.get(name, 0) + 1
            medium = str(getattr(event, "medium", None))
            per = counts["by_medium"].setdefault(medium, {"stored": 0, "removed": 0})
            if name == "BlockStored":
                counts["stored_hashes"] += len(event.block_hashes)
                per["stored"] += len(event.block_hashes)
            elif name == "BlockRemoved":
                counts["removed_hashes"] += len(event.block_hashes)
                per["removed"] += len(event.block_hashes)
        if time.monotonic() - last_write > 5:
            dump()
            last_write = time.monotonic()
    dump()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="poll until SIGTERM")
    r.add_argument("--metrics-url", required=True)
    r.add_argument("--gpu", type=int, default=0, help="NVML index of this cell's GPU")
    r.add_argument("--out", required=True)
    r.add_argument("--server-pid", type=int, default=0)
    r.add_argument("--sweep-pid", type=int, default=0)
    r.add_argument("--period", type=float, default=1.0)
    k = sub.add_parser("kvevents", help="count vLLM KV-cache events until SIGTERM")
    k.add_argument("--endpoint", required=True, help="e.g. tcp://127.0.0.1:5557")
    k.add_argument("--out", required=True)
    p = sub.add_parser("parse", help="parse a saved /metrics text file and print the summed families")
    p.add_argument("file")
    args = ap.parse_args()
    if args.cmd == "kvevents":
        sys.exit(kvevents(args))
    if args.cmd == "parse":
        print(json.dumps(parse_metrics(open(args.file).read()), indent=1, sort_keys=True))
        return
    sys.exit(run(args))


if __name__ == "__main__":
    main()
