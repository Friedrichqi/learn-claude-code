#!/usr/bin/env python3
"""Scrape every vLLM instance's /metrics of a research/14_pd_crossover layout every --period seconds.

    python research/14_pd_crossover/pd_scrape.py --instances SRV/instances.json --out DIR [--period 5]
One JSON line per scrape and instance in DIR/scrape.<idx>.jsonl: {"t" (monotonic), "wall", "idx", "ok", "m": {...}}.
Kept series (Prometheus text, parsed with 11's mu_scrape line regex): NIXL transfer histograms (_sum / _count only)
and failure / expiry counters; prompt tokens by source (local_compute / local_cache_hit / external_kv_transfer);
external prefix cache queries / hits; prompt and generation token totals; running / waiting requests; KV usage;
prefix-cache queries / hits; preemptions. Labelled series keep the label that matters (source) as name{value}.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

from mu_scrape import LABEL, LINE  # noqa: E402  research/11_multiuser_kv

KEEP = ("vllm:nixl_", "vllm:prompt_tokens_by_source", "vllm:external_prefix_cache", "vllm:prompt_tokens_total",
        "vllm:generation_tokens_total", "vllm:num_requests_running", "vllm:num_requests_waiting",
        "vllm:kv_cache_usage_perc", "vllm:prefix_cache_queries", "vllm:prefix_cache_hits",
        "vllm:num_preemptions", "vllm:prompt_tokens_cached", "vllm:request_prefill_kv_computed_tokens")
SPLIT_LABEL = {"vllm:prompt_tokens_by_source": "source"}


def parse(text: str) -> dict:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        m = LINE.match(line)
        if not m:
            continue
        name, labels, value = m.group(1), m.group(2) or "", m.group(3)
        if not name.startswith(KEEP) or name.endswith("_bucket") or name.endswith("_created"):
            continue
        try:
            v = float(value)
        except ValueError:
            continue
        base = name
        for suffix in ("_sum", "_count", "_total"):
            if name.endswith(suffix):
                base = name[: -len(suffix)]
                break
        key = SPLIT_LABEL.get(base) or SPLIT_LABEL.get(name)
        if key:
            lab = dict(LABEL.findall(labels)).get(key)
            if lab is not None:
                name = f"{name}{{{lab}}}"
        out[name] = out.get(name, 0.0) + v
    return out


def scrape(base: str) -> dict | None:
    try:
        with urllib.request.urlopen(base + "/metrics", timeout=5) as r:
            return parse(r.read().decode())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instances", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", type=float, default=5.0)
    ap.add_argument("--once", action="store_true", help="one scrape, printed as JSON (snapshots)")
    a = ap.parse_args()
    insts = json.loads(Path(a.instances).read_text())
    if a.once:
        print(json.dumps({str(i["idx"]): scrape(i["base"]) for i in insts}))
        return 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = {i["idx"]: open(out / f"scrape.{i['idx']}.jsonl", "a", encoding="utf-8") for i in insts}
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(v=True))
    while not stop["v"]:
        t0 = time.monotonic()
        for i in insts:
            m = scrape(i["base"])
            files[i["idx"]].write(json.dumps({"t": time.monotonic(), "wall": time.time(), "idx": i["idx"],
                                              "ok": m is not None, "m": m}) + "\n")
            files[i["idx"]].flush()
        while not stop["v"] and time.monotonic() - t0 < a.period:
            time.sleep(0.2)
    for f in files.values():
        f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
