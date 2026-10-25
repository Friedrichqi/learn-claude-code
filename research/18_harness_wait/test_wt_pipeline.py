#!/usr/bin/env python3
"""CPU test of wt_pipeline.py's whole cell path against a fake vLLM (wt_fake_vllm.py), with a temporary data root:
the GPU-empty and cap checks, the server start, the 1-token probe, the scheduler-header and /wt/status checks, the
scraper and KV-event counter, the window sweep with real s15 sessions of the h_share arm, the stop, the GPU check after
it, the log compression, the lane cleanup and the GPU-hour line. Nothing touches a GPU (nvidia-smi is only queried).

    python research/18_harness_wait/test_wt_pipeline.py        (WT_SKIP_E2E=1 skips it)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DIR = Path(__file__).resolve().parent
PY = "/data/fritz/venv/bin/python"


def test_pipeline_cell_with_fake_server():
    if os.environ.get("WT_SKIP_E2E") == "1":
        print("  (skipped: WT_SKIP_E2E=1)")
        return
    root = Path(tempfile.mkdtemp(prefix="wt_pipe_", dir=os.environ.get("WT_TMP")))
    env = {**os.environ, "WT_DATA_ROOT": str(root), "WT_VLLM_CMD": f"{PY} {DIR / 'wt_fake_vllm.py'}"}
    cmd = [PY, str(DIR / "wt_pipeline.py"), "cell", "--cond", "s_hshare", "--pair", "A", "--warmup-s", "2",
           "--window-s", "30", "--users", "2", "--think-scale", "0.02"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    cell = root / "cells" / "s_hshare"
    assert (cell / "COMPLETE").exists() and (cell / "window.json").exists()
    runs = sorted((cell / "runs").glob("MU-s_hshare-S*"))
    assert runs, "no sessions ran"
    servers = sorted((cell / "server").glob("A_*"))
    assert len(servers) == 1
    sdir = servers[0]
    info = json.loads((sdir / "server_info.json").read_text())
    assert info["check_ok"] and info["nblk"] == 1100 and info["pool_tokens"] == 1099 * 16, info
    assert info["gpus_empty_after"] and info["sweep_rc"] == 0, info
    assert (sdir / "sched.jsonl.xz").exists() and not (sdir / "sched.jsonl").exists()
    ext = [json.loads(line) for line in (sdir / "wt_ext.jsonl").read_text().splitlines() if line.strip()]
    states = {r.get("state") for r in ext}
    assert "check" in states, states
    assert (sdir / "scrape.jsonl").exists() and (sdir / "kvevents.json").exists()
    assert not (root / "lanes" / "s_hshare").exists(), "lanes were not removed"
    assert not Path(info["status_path"]).exists(), "status file left in /dev/shm"
    hours = [json.loads(line) for line in (root / "gpu_hours.jsonl").read_text().splitlines()]
    assert len(hours) == 1 and hours[0]["rc"] == 0 and 0 < hours[0]["gpu_h"] < 0.2, hours
    # the guard: a second run of the same cell refuses to overwrite it
    again = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    assert again.returncode != 0 and "already holds runs" in (again.stdout + again.stderr)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
