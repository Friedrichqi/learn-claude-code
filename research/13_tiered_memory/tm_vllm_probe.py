#!/usr/bin/env python3
"""Measure one running vLLM server for research/13_tiered_memory (stage S2): what fits and what each way of
getting KV back costs on this GPU.

    python research/13_tiered_memory/tm_vllm_probe.py --base http://127.0.0.1:PORT --model Qwen/Qwen3-32B \
        --log SRV/vllm.log --out DIR/probe_qwen3-32b.json [--offload]

  memory     from the server log: weights, KV memory and tokens, max concurrency at the window, CUDA graphs
  prefill    recompute rate: latency of max_tokens=1 requests on random-token prompts (distinct each time,
             so nothing hits the prefix cache) at 1k-32k tokens; tokens / latency
  decode     batch-1 decode: 256 output tokens with ignore_eos, (latency - prefill) / tokens
  restore    (--offload) the same prompt again after the GPU prefix cache is reset while the CPU tier is
             kept (POST /reset_prefix_cache?reset_external=false): the request now loads its KV from DRAM.
             Also a GPU hit (no reset) for reference. Needs VLLM_SERVER_DEV_MODE=1.
Prompts are token ids sent to /v1/completions, so no chat template or tokenizer cost is included.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

LENGTHS = (1024, 4096, 8192, 16384, 23552, 32768)


def post(base: str, path: str, body: dict | None = None, timeout: float = 600.0) -> dict:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:200]}


def complete(base: str, model: str, ids: list[int], max_tokens: int, ignore_eos: bool = False) -> tuple[float, dict]:
    t0 = time.perf_counter()
    r = post(base, "/v1/completions", {"model": model, "prompt": ids, "max_tokens": max_tokens, "temperature": 0,
                                       "ignore_eos": ignore_eos})
    return time.perf_counter() - t0, r


def rand_ids(n: int, rng: random.Random) -> list[int]:
    return [rng.randrange(1000, 100000) for _ in range(n)]


def metric(base: str, names: tuple[str, ...]) -> dict:
    with urllib.request.urlopen(base + "/metrics", timeout=10) as resp:
        text = resp.read().decode()
    out = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        for n in names:
            if line.startswith(n):
                key, _, val = line.rpartition(" ")
                try:
                    out[key] = float(val)
                except ValueError:
                    pass
    return out


def parse_log(path: Path) -> dict:
    text = path.read_text(errors="replace") if path.exists() else ""
    pats = {"weights_gib": r"Model loading took ([\d.]+) GiB", "kv_mem_gib": r"Available KV cache memory: ([\d.]+) GiB",
            "kv_tokens": r"GPU KV cache size: ([\d,]+) tokens", "max_concurrency": r"Maximum concurrency for [\d,]+ tokens per request: ([\d.]+)x",
            "graph_gib": r"CUDA graph pool memory: ([\d.]+) GiB \(actual\)", "offload_gb": r"Created mmap file \S+ \(([\d.]+) GB\)",
            "util_line": r"(Free memory on device .*)"}
    out = {}
    for k, p in pats.items():
        m = re.search(p, text)
        if m:
            v = m.group(1).replace(",", "")
            try:
                out[k] = float(v) if k != "util_line" else v[:400]
            except ValueError:
                out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--log", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--offload", action="store_true")
    ap.add_argument("--max-len", type=int, default=32768)
    args = ap.parse_args()
    rng = random.Random(13)
    res = {"model": args.model, "memory": parse_log(Path(args.log)) if args.log else {}, "prefill": [], "decode": {},
           "restore": []}
    complete(args.base, args.model, rand_ids(512, rng), 1)                      # warm-up
    for n in [x for x in LENGTHS if x <= args.max_len]:
        lat = [complete(args.base, args.model, rand_ids(n, rng), 1)[0] for _ in range(2)]
        res["prefill"].append({"tokens": n, "latency_s": [round(x, 4) for x in lat],
                               "tok_per_s": round(n / statistics.median(lat))})
        print(f"[probe] prefill {n}: {lat}", flush=True)
    base_lat = complete(args.base, args.model, rand_ids(256, rng), 1)[0]
    lat, r = complete(args.base, args.model, rand_ids(256, rng), 257, ignore_eos=True)
    res["decode"] = {"out_tokens": 256, "latency_s": round(lat, 3),
                     "ms_per_token": round(1000 * (lat - base_lat) / 256, 2)}
    if args.offload:
        names = ("vllm:kv_offload", "vllm:prefix_cache", "vllm:external_prefix_cache")
        for n in (4096, 16384, 23552):
            if n > args.max_len:
                continue
            ids = rand_ids(n, rng)
            cold, _ = complete(args.base, args.model, ids, 1)
            time.sleep(3)                                                          # let the store to DRAM finish
            gpu_hit, _ = complete(args.base, args.model, ids, 1)
            m0 = metric(args.base, names)
            ok = post(args.base, "/reset_prefix_cache?reset_external=false")
            time.sleep(1)
            dram, _ = complete(args.base, args.model, ids, 1)
            m1 = metric(args.base, names)
            delta = {k: round(m1[k] - m0.get(k, 0.0), 1) for k in m1 if m1[k] != m0.get(k, 0.0)}
            res["restore"].append({"tokens": n, "cold_s": round(cold, 4), "gpu_hit_s": round(gpu_hit, 4),
                                   "dram_s": round(dram, 4), "reset": ok, "metrics_delta": delta,
                                   "dram_tok_per_s": round(n / dram)})
            print(f"[probe] restore {n}: cold {cold:.3f} gpu {gpu_hit:.3f} dram {dram:.3f} {delta}", flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(res)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
