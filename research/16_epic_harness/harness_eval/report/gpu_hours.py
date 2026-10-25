#!/usr/bin/env python3
"""GPU-hours held by this study's vLLM servers, from runs/servers/*.json (start) and the stop time encoded in
*.stopped-<epoch>.json; servers still running are counted up to now."""
import json
import re
import time
from collections import defaultdict
from pathlib import Path

STATE = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval/runs/servers")


def servers() -> list[dict]:
    """One record per server run: model, instance key, GPUs, start, end (None while running), GPU-hours."""
    rows = []
    for f in sorted(STATE.glob("*.json")):
        s = json.loads(f.read_text())
        start = float(s.get("started") or 0)
        m = re.search(r"stopped-(\d+)\.json$", f.name)
        end = float(m.group(1)) if m else None
        hours = max(0.0, (end or time.time()) - start) / 3600 * len(s.get("gpus") or [])
        rows.append({"name": s.get("name") or f.name, "key": s.get("key"), "gpus": s.get("gpus"), "start": start,
                     "end": end, "gpu_hours": hours})
    return rows


def per_model() -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in servers():
        out[r["name"]] += r["gpu_hours"]
    return dict(out)


def main() -> None:
    for r in servers():
        print(r["name"], r["gpus"], time.strftime("%H:%M", time.gmtime(r["start"])),
              "running" if r["end"] is None else time.strftime("%H:%M", time.gmtime(r["end"])), round(r["gpu_hours"], 2))
    pm = per_model()
    print({k: round(v, 1) for k, v in pm.items()}, "total", round(sum(pm.values()), 1))


if __name__ == "__main__":
    main()
