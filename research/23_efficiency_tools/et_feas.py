#!/usr/bin/env python3
"""Topic 23, S0: does KV offload (LMCache's multiprocess server, or vLLM's native CPU offload) work for Qwen3.8-27B, and
can a client choose per request whether its KV is stored?

Against a running server it replays recorded s15 requests (topic 16's Qwen3.8 calls) through the Anthropic endpoint,
as s15 sends them, and reads vLLM's and the offload backend's counters around each step:
  T1 store all:   send A; clear the GPU prefix cache (/reset_prefix_cache, offload kept); send A again: the prompt
                  must come back as external hit tokens, and the greedy reply must match.
  T2 on request:  send B with the "don't store" switch: nothing stored. Re-send B without it (max_tokens 1, its prefix
                  still on the GPU): B's chunks are stored. Clear the GPU cache; send B with the switch: external hits.
  T3 footprint:   host bytes per stored token, reload time against recompute time.

    python research/23_efficiency_tools/et_feas.py --url http://127.0.0.1:50331 --backend lmcache \
        --lmc-url http://127.0.0.1:50352 --out data/s0/feas_lmcache.json
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import time
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
RECORDED = DIR.parents[0] / "16_epic_harness/harness_eval/jobs/qwen3.8-27b"
MODEL = "qwen3.8-27b"
NO_STORE = {"lmcache": {"lmcache.max_offload_tokens": 0}, "native": {"max_offload_tokens": 0}}


def http(url: str, body: dict | None = None, timeout: float = 600.0) -> tuple[float, dict | str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if data is not None or url.endswith("cache") else "GET",
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    dt = time.monotonic() - t0
    try:
        return dt, json.loads(raw)
    except ValueError:
        return dt, raw


def prom(url: str) -> dict[str, float]:
    """Every sample of a Prometheus text page, keyed by name{labels}."""
    _, text = http(url)
    out = {}
    for line in str(text).splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r"^(\S+?)(\{.*\})?\s+(\S+)$", line)
        if m:
            try:
                out[m.group(1) + (m.group(2) or "")] = float(m.group(3))
            except ValueError:
                pass
    return out


def pick(series: dict[str, float], name: str, label: str = "") -> float:
    return sum(v for k, v in series.items() if k.split("{")[0] == name and label in k)


class Probe:
    def __init__(self, url: str, backend: str, lmc_url: str | None):
        self.url, self.backend, self.lmc_url = url.rstrip("/"), backend, lmc_url

    def counters(self) -> dict[str, float]:
        v = prom(f"{self.url}/metrics")
        c = {"external": pick(v, "vllm:prompt_tokens_by_source_total", 'source="external_kv_transfer"'),
             "local_hit": pick(v, "vllm:prompt_tokens_by_source_total", 'source="local_cache_hit"'),
             "computed": pick(v, "vllm:prompt_tokens_by_source_total", 'source="local_compute"'),
             "kv_usage": pick(v, "vllm:kv_cache_usage_perc"),
             "offload_store_bytes": pick(v, "vllm:kv_offload_store_bytes_total"),
             "offload_load_bytes": pick(v, "vllm:kv_offload_load_bytes_total"),
             "offload_cpu_usage": pick(v, "vllm:kv_offload_cpu_cache_usage_perc")}
        if self.lmc_url:
            lv = prom(f"{self.lmc_url}/metrics")
            c.update({"lmc_" + k: pick(lv, k) for k in (
                "lmcache_mp_num_submitted_stores_total", "lmcache_mp_num_finished_stores_total",
                "lmcache_mp_l1_write_total", "lmcache_mp_l1_memory_usage_bytes", "lmcache_mp_lookup_hit",
                "lmcache_mp_lookup_requested", "lmcache_mp_num_finished_retrieves_total")})
        return c

    def send(self, req: dict, max_tokens: int, no_store: bool = False) -> dict:
        # the recorder keeps only the tool names, so the replay sends no tool schemas (any long prompt will do here)
        body = {"model": MODEL, "system": req["system"], "messages": req["messages"], "max_tokens": max_tokens,
                "temperature": 0.0}
        if no_store:
            body["kv_transfer_params"] = NO_STORE[self.backend]
        dt, r = http(f"{self.url}/v1/messages", body)
        text = "".join(b.get("text", "") + b.get("thinking", "") + json.dumps(b.get("input", ""))
                       for b in r.get("content", []))
        return {"s": round(dt, 3), "usage": r.get("usage"), "text": text}

    def reset_gpu_cache(self) -> None:
        http(f"{self.url}/reset_prefix_cache", {})

    def step(self, label: str, fn) -> dict:
        a = self.counters()
        out = fn()
        time.sleep(3.0)                     # stores finish asynchronously
        b = self.counters()
        d = {k: round(b[k] - a[k], 3) for k in a if not k.endswith(("usage", "usage_perc", "usage_bytes"))}
        d.update({k: b[k] for k in b if k.endswith(("usage", "usage_perc", "usage_bytes"))})
        rec = {"step": label, **(out or {}), "delta": d}
        print(json.dumps({k: v for k, v in rec.items() if k != "text"}), flush=True)
        return rec


def recorded(n: int, min_chars: int) -> list[dict]:
    """Late lead calls of recorded Qwen3.8 sessions (long prompts), one per session."""
    out = []
    for path in sorted(glob.glob(str(RECORDED / "*/*/*/agent/s15/calls.jsonl"))):
        rows = [json.loads(l) for l in open(path) if l.strip()]
        leads = [r for r in rows if r.get("purpose") == "lead" and r.get("request")]
        for r in reversed(leads):
            if len(json.dumps(r["request"]["messages"])) >= min_chars:
                out.append(r["request"])
                break
        if len(out) >= n:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--backend", choices=sorted(NO_STORE), required=True)
    ap.add_argument("--lmc-url", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    p = Probe(a.url, a.backend, a.lmc_url)
    reqs = recorded(3, 60_000)
    A, B, C = reqs[0], reqs[1], reqs[2]
    res = {"backend": a.backend, "steps": []}
    S = res["steps"]
    # T1: store all
    S.append(p.step("T1 A cold", lambda: p.send(A, 64)))
    S.append(p.step("T1 reset GPU", lambda: p.reset_gpu_cache()))
    S.append(p.step("T1 A again (offload hit expected)", lambda: p.send(A, 64)))
    S.append(p.step("T1 A again on GPU (local hit)", lambda: p.send(A, 64)))
    # T2: store on request
    S.append(p.step("T2 B no-store", lambda: p.send(B, 1, no_store=True)))
    S.append(p.step("T2 reset GPU", lambda: p.reset_gpu_cache()))
    S.append(p.step("T2 B no-store again (no offload hit expected)", lambda: p.send(B, 1, no_store=True)))
    S.append(p.step("T2 B store re-send (GPU prefix cached)", lambda: p.send(B, 1)))
    S.append(p.step("T2 reset GPU", lambda: p.reset_gpu_cache()))
    S.append(p.step("T2 B no-store after store (offload hit expected)", lambda: p.send(B, 1, no_store=True)))
    # T3: reload against recompute on a third prompt
    S.append(p.step("T3 C no-store cold (recompute)", lambda: p.send(C, 1, no_store=True)))
    S.append(p.step("T3 C store re-send", lambda: p.send(C, 1)))
    S.append(p.step("T3 reset GPU", lambda: p.reset_gpu_cache()))
    S.append(p.step("T3 C reload", lambda: p.send(C, 1, no_store=True)))
    g = {s["step"]: s for s in S}
    t1 = g["T1 A again (offload hit expected)"]
    res["checks"] = {
        "T1 offload hit tokens": t1["delta"]["external"],
        "T1 reply equal after reload": g["T1 A cold"]["text"] == t1["text"],
        "T2 no-store stored nothing": g["T2 B no-store again (no offload hit expected)"]["delta"]["external"] == 0,
        "T2 store re-send then hit tokens": g["T2 B no-store after store (offload hit expected)"]["delta"]["external"],
        "T3 recompute s": g["T3 C no-store cold (recompute)"]["s"], "T3 reload s": g["T3 C reload"]["s"],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res["checks"], indent=1))


if __name__ == "__main__":
    main()
