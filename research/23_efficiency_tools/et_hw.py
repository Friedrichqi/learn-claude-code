#!/usr/bin/env python3
"""Topic 23's hardware monitor: once a second, everything the analysis and the agate controller read.

Each record (JSONL, one per second; the "wall", "ok", "m" and "gpu" fields as topic 11's mu_scrape.py, so
22_wait_bench/wb_analyze.load_scrape reads these files):
  m     vLLM /metrics: counters and gauges summed over labels, plus the by-reason / by-source / finished-reason splits
        (mu_scrape.parse_metrics);
  lmc   LMCache /metrics (lmcache_mp_*, summed over labels), when --lmc-url is given;
  gpu   [one sample] NVML: utilization (gpu, memory controller), memory used, power, SM clock, PCIe TX/RX; and GPM
        (Hopper, readable without root) over the last second: SM utilization, SM occupancy, any tensor-core
        utilization, HBM (DRAM) bandwidth utilization, PCIe TX/RX bytes per second;
  gate  the gate proxy's /gate/state (K, active / waiting sessions, the active sessions' context tokens);
  cpu   the host's load1.
GET /hw/summary?window=60&minutes=5 serves the last `window` seconds as rates and means (et_prompts.window_lines'
fields), and the previous `minutes` one-minute windows, for the controller. kv_capacity_tokens comes from
vllm:cache_config_info (num_gpu_blocks × block_size).

    python research/23_efficiency_tools/et_hw.py --metrics-url http://127.0.0.1:31431/metrics --gpu 4 \
        --gate-url http://127.0.0.1:31441 [--lmc-url http://127.0.0.1:31452] --port 31461 --out <run>/hw_<t>.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent / "11_multiuser_kv"))
from mu_scrape import parse_metrics  # noqa: E402

LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eEinfaINFA]+)")
KEEP_S = 420


def fetch(url: str | None, timeout: float = 3.0) -> str | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:                                             # noqa: BLE001
        return None


def parse_lmcache(text: str) -> dict:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        m = LINE.match(line)
        if m and m.group(1).startswith("lmcache_mp_") and not m.group(1).endswith("_bucket"):
            try:
                out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(3))
            except ValueError:
                pass
    return out


def kv_capacity(text: str) -> int | None:
    m = re.search(r'^vllm:cache_config_info\{([^}]*)\}', text, re.M)
    if not m:
        return None
    lab = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    try:
        return int(lab["num_gpu_blocks"]) * int(lab["block_size"])
    except (KeyError, ValueError):
        return None


class GPU:
    """NVML basics plus GPM metrics between consecutive samples."""
    GPM = {"sm_util": "NVML_GPM_METRIC_SM_UTIL", "sm_occupancy": "NVML_GPM_METRIC_SM_OCCUPANCY",
           "tensor_util": "NVML_GPM_METRIC_ANY_TENSOR_UTIL", "dram_bw_util": "NVML_GPM_METRIC_DRAM_BW_UTIL",
           "pcie_tx_mb_s": "NVML_GPM_METRIC_PCIE_TX_PER_SEC", "pcie_rx_mb_s": "NVML_GPM_METRIC_PCIE_RX_PER_SEC"}

    def __init__(self, index: int):
        import pynvml as nv
        nv.nvmlInit()
        self.nv, self.h = nv, nv.nvmlDeviceGetHandleByIndex(index)
        self.ids = {k: getattr(nv, v) for k, v in self.GPM.items() if hasattr(nv, v)}
        try:
            self.gpm = bool(nv.nvmlGpmQueryDeviceSupport(self.h).isSupportedDevice)
        except Exception:                                         # noqa: BLE001
            self.gpm = False
        self.prev = self.cur = None
        if self.gpm:
            self.prev, self.cur = nv.nvmlGpmSampleAlloc(), nv.nvmlGpmSampleAlloc()
            nv.nvmlGpmSampleGet(self.h, self.prev)

    def sample(self) -> dict:
        nv, h = self.nv, self.h
        out: dict = {}
        try:
            u = nv.nvmlDeviceGetUtilizationRates(h)
            mem = nv.nvmlDeviceGetMemoryInfo(h)
            out.update(util=u.gpu, mem_util=u.memory, mem_used_gb=round(mem.used / 2**30, 2),
                       power_w=round(nv.nvmlDeviceGetPowerUsage(h) / 1000, 1),
                       sm_mhz=nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM))
        except Exception:                                         # noqa: BLE001
            pass
        if self.gpm:
            try:
                nv.nvmlGpmSampleGet(h, self.cur)
                mg = nv.c_nvmlGpmMetricsGet_t()
                mg.version = nv.NVML_GPM_METRICS_GET_VERSION
                mg.numMetrics = len(self.ids)
                mg.sample1, mg.sample2 = self.prev, self.cur
                for i, v in enumerate(self.ids.values()):
                    mg.metrics[i].metricId = v
                nv.nvmlGpmMetricsGet(mg)
                gpm = {}
                for i, k in enumerate(self.ids):
                    if mg.metrics[i].nvmlReturn == 0:
                        x = float(mg.metrics[i].value)
                        gpm[k] = round(x / 100.0, 4) if k.endswith("util") or k == "sm_occupancy" else round(x, 2)
                out["gpm"] = gpm
                self.prev, self.cur = self.cur, self.prev
            except Exception:                                     # noqa: BLE001
                pass
        return out


def _delta(a: dict, b: dict, key: str) -> float:
    return float(b.get(key) or 0.0) - float(a.get(key) or 0.0)


def summarize(recs: list[dict], cap: int | None) -> dict:
    """Rates and means over a list of consecutive records (et_prompts.window_lines' fields)."""
    recs = [r for r in recs if r.get("ok")]
    if len(recs) < 2:
        return {}
    a, b = recs[0]["m"], recs[-1]["m"]
    dt = recs[-1]["wall"] - recs[0]["wall"]
    if dt <= 0:
        return {}
    mean = lambda f: (sum(f(r) for r in recs) / len(recs)) if recs else None
    hist = lambda name: ((_delta(a, b, name + "_sum") / _delta(a, b, name + "_count"))
                         if _delta(a, b, name + "_count") > 0 else None)
    prompt = _delta(a, b, "vllm:prompt_tokens_total")
    computed = _delta(a, b, "vllm:prompt_tokens_by_source_total{local_compute}")
    hit = _delta(a, b, "vllm:prompt_tokens_by_source_total{local_cache_hit}")
    ext = _delta(a, b, "vllm:prompt_tokens_by_source_total{external_kv_transfer}")
    gpus = [r["gpu"][0] for r in recs if r.get("gpu")]
    gpm = lambda k: (sum(g.get("gpm", {}).get(k, 0.0) for g in gpus) / len(gpus)) if gpus else None
    kv = [float(r["m"].get("vllm:kv_cache_usage_perc") or 0.0) for r in recs]
    gate = recs[-1].get("gate") or {}
    out = {"seconds": round(dt, 1), "kv_capacity_tokens": cap,
           "kv_usage_mean": sum(kv) / len(kv), "kv_usage_max": max(kv),
           "running_mean": mean(lambda r: float(r["m"].get("vllm:num_requests_running") or 0.0)),
           "waiting_mean": mean(lambda r: float(r["m"].get("vllm:num_requests_waiting") or 0.0)),
           "waiting_capacity_mean": mean(lambda r: float(r["m"].get("vllm:num_requests_waiting_by_reason{capacity}")
                                                         or 0.0)),
           "requests_per_min": _delta(a, b, "vllm:request_success_total") / dt * 60,
           "prompt_tok_s": prompt / dt, "computed_tok_s": computed / dt, "gpu_hit_tok_s": hit / dt,
           "offload_hit_tok_s": ext / dt, "gpu_hit_rate": (hit / (hit + computed + ext)) if hit + computed + ext else None,
           "gen_tok_s": _delta(a, b, "vllm:generation_tokens_total") / dt,
           "preemptions_per_min": _delta(a, b, "vllm:num_preemptions_total") / dt * 60,
           "itl_ms": (hist("vllm:inter_token_latency_seconds") or 0.0) * 1000 or None,
           "ttft_s": hist("vllm:time_to_first_token_seconds"), "queue_s": hist("vllm:request_queue_time_seconds"),
           "sm_util": gpm("sm_util"), "sm_occupancy": gpm("sm_occupancy"), "tensor_util": gpm("tensor_util"),
           "dram_bw_util": gpm("dram_bw_util"),
           "power_w": (sum(g.get("power_w", 0.0) for g in gpus) / len(gpus)) if gpus else None,
           "k": gate.get("k")}
    lmc_a, lmc_b = recs[0].get("lmc") or {}, recs[-1].get("lmc") or {}
    if lmc_b:
        out["lmc_l1_usage"] = lmc_b.get("lmcache_mp_l1_usage_ratio")
        out["lmc_hit_tok_s"] = _delta(lmc_a, lmc_b, "lmcache_mp_lookup_hit") / dt
        out["lmc_evicted_per_min"] = _delta(lmc_a, lmc_b, "lmcache_mp_l1_evicted_total") / dt * 60
    return out


class Monitor:
    def __init__(self, a):
        self.a = a
        self.gpu = GPU(a.gpu)
        self.recent: collections.deque = collections.deque(maxlen=KEEP_S)
        self.cap: int | None = None
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def run(self) -> None:
        Path(self.a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(self.a.out, "a", buffering=1) as f:
            nxt = time.monotonic()
            while not self.stop.is_set():
                t, wall = time.monotonic(), time.time()
                text = fetch(self.a.metrics_url)
                if text and self.cap is None:
                    self.cap = kv_capacity(text)
                rec = {"t": t, "wall": wall, "ok": text is not None, "m": parse_metrics(text) if text else None,
                       "gpu": [self.gpu.sample()], "cpu": {"load1": round(os.getloadavg()[0], 2)}}
                lt = fetch(self.a.lmc_url)
                if lt:
                    rec["lmc"] = parse_lmcache(lt)
                gt = fetch(f"{self.a.gate_url.rstrip('/')}/gate/state" if self.a.gate_url else None, 2.0)
                if gt:
                    try:
                        rec["gate"] = json.loads(gt)
                    except ValueError:
                        pass
                rec["scrape_ms"] = round((time.monotonic() - t) * 1000, 1)
                f.write(json.dumps(rec) + "\n")
                with self.lock:
                    self.recent.append(rec)
                nxt += self.a.period
                self.stop.wait(max(0.0, nxt - time.monotonic()))

    def summary(self, window: float, minutes: int) -> dict:
        with self.lock:
            recs = list(self.recent)
        if not recs:
            return {}
        end = recs[-1]["wall"]
        pick = lambda lo, hi: [r for r in recs if lo <= r["wall"] <= hi]
        out = {"window_s": window, "now": summarize(pick(end - window, end), self.cap), "minutes": []}
        out["now"]["gate"] = recs[-1].get("gate")
        for i in range(minutes, 0, -1):
            hi = end - window - 60 * (i - 1)
            s = summarize(pick(hi - 60, hi), self.cap)
            if s:
                out["minutes"].append(s)
        return out


def serve(mon: Monitor, port: int) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/hw/summary":
                self.send_error(404)
                return
            q = urllib.parse.parse_qs(u.query)
            body = json.dumps(mon.summary(float(q.get("window", ["60"])[0]), int(q.get("minutes", ["5"])[0])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics-url", required=True)
    ap.add_argument("--gpu", type=int, required=True)
    ap.add_argument("--gate-url", default=None)
    ap.add_argument("--lmc-url", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--period", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    mon = Monitor(a)
    if a.port:
        serve(mon, a.port)
    signal.signal(signal.SIGTERM, lambda *_: mon.stop.set())
    signal.signal(signal.SIGINT, lambda *_: mon.stop.set())
    mon.run()


if __name__ == "__main__":
    main()
