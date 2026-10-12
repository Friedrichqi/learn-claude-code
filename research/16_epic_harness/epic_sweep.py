#!/usr/bin/env python3
"""Live closed-loop runs of the EPIC study (E-B, and the pilot) on one GPU; resumable.

One `vllm serve` with the EpicConnector and the epic_server_ext middleware serves every arm (the arm
is chosen per request by epic_session's tag; each arm-session has its own cache salt). Lanes are rsync
copies of the repository (epic_common.make_lane) whose `.env` points at the server; all arms of a block
run in the same lane, one after another, so absolute paths the harness writes into placeholders are
identical across the block's arms. `--lanes` sessions run at once (2 fit the BF16 KV pool).

A schedule is a JSONL file of runs: {label, row (manifest row), arm, regime, block, lane}. A run is done
when runs/<label>/<label>.done exists; rerunning skips it, so a crashed sweep resumes where it stopped.

A sweep split over several GPUs runs one process per GPU (EPIC_GPU) on disjoint schedule files with whole
blocks each, into the same --out: --tag names that server's logs (serve.<tag>.log, server_calls.<tag>.jsonl,
read back by epic_common.server_logs) and --lane-offset gives it its own lane directories.

    python research/16_epic_harness/epic_sweep.py --schedule SCHED.jsonl --out data/live/pilot \
        [--lanes 2] [--lead-cap 150] [--out-cap 0] [--limit N] [--gpu G] [--tag T --lane-offset 2]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402

POOL_ROWS = 200_000
DEADLINE = {"R1": 7200.0, "R2": 9000.0, "OFF": 3600.0}


def server_flags() -> list[str]:
    kv = {"kv_connector": "EpicConnector", "kv_connector_module_path": "epic_connector",
          "kv_role": "kv_both", "kv_load_failure_policy": "fail",
          "kv_connector_extra_config": {"store_device": "cpu", "pool_rows": POOL_ROWS}}
    # the override hooks need eager PyTorch modules; EPIC_CUDAGRAPH=1 keeps them eager (no torch.compile)
    # but replays decode-only steps from full CUDA graphs (decode steps never carry overrides)
    graphs = (["--compilation-config", json.dumps({"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY"})]
              if os.environ.get("EPIC_CUDAGRAPH") == "1" else ["--enforce-eager"])
    return ["--enable-prefix-caching", "--enable-prompt-tokens-details", *graphs,
            "--no-async-scheduling", "--attention-backend", "FLASH_ATTN", "--disable-cascade-attn",
            "--max-num-batched-tokens", "8192", "--chat-template", str(ec.TEMPLATE),
            "--middleware", "epic_server_ext.Middleware", "--kv-transfer-config", json.dumps(kv)]


def log_names(tag: str = "") -> tuple[str, str]:
    """(serve log, middleware call log) file names of a sweep's server; tagged for a further GPU."""
    return (f"serve.{tag}.log", f"server_calls.{tag}.jsonl") if tag else ("serve.log", "server_calls.jsonl")


def server_env(out: Path, tag: str = "") -> dict:
    return {"PYTHONPATH": f"{ec.HERE}:{os.environ.get('PYTHONPATH', '')}",
            "EPIC_LOG": str(out / log_names(tag)[1]), "EPIC_POOL_ROWS": str(POOL_ROWS),
            "VLLM_ALLOW_INSECURE_SERIALIZATION": "1",
            "EPIC_SHADOW_EVERY": os.environ.get("EPIC_SHADOW_EVERY", "4"),
            "EPIC_CF_EVERY": os.environ.get("EPIC_CF_EVERY", "3"),
            # batch-invariant kernels: a request's logits do not depend on what else is batched, so
            # identical prompts with identical seeds sample identically (common random numbers)
            "VLLM_BATCH_INVARIANT": os.environ.get("EPIC_BATCH_INVARIANT", "0")}


def load_schedule(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--out", required=True, help="output dir (relative paths are under this topic's data/)")
    ap.add_argument("--lanes", type=int, default=2)
    ap.add_argument("--lead-cap", type=int, default=150)
    ap.add_argument("--out-cap", type=int, default=0, help="output tokens of lead calls per run (0: no cap)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gpu", type=int, default=None)
    ap.add_argument("--tag", default="", help="this server's log tag when the sweep is split over GPUs")
    ap.add_argument("--lane-offset", type=int, default=0, help="first lane directory index of this process")
    args = ap.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ec.DATA / out
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    schedule = load_schedule(Path(args.schedule))
    for r in schedule:                      # epic_workloads manifest rows: one per (block, arm)
        if "run_label" in r:
            r["row"] = {k: v for k, v in r.items() if k != "row"}   # a copy: never self-referencing
            r["label"] = r["run_label"]
    schedule.sort(key=lambda r: (int(r.get("lane", 0)), int(r.get("seq", 0))))

    def finished(label: str) -> bool:
        """Done, and not cut short by a stopped sweep (a terminated session reruns from scratch)."""
        d = runs_dir / label
        if not (d / f"{label}.done").exists():
            return False
        meta = d / f"{label}.meta.json"
        status = json.loads(meta.read_text()).get("status", "") if meta.exists() else ""
        answer = d / f"{label}.answer.json"
        text = json.loads(answer.read_text()).get("answer") or "" if answer.exists() else ""
        broken = any(k in str(text) for k in ("InternalServerError", "EngineDeadError", "APIConnectionError",
                                                 "Connection error", "EngineCore encountered"))
        if status == "terminated" or broken or (str(status).startswith("error: ") and "Connection" in str(status)):
            shutil.rmtree(d)
            return False
        return True

    todo = [r for r in schedule if not finished(r["label"])]
    if args.limit:
        todo = todo[: args.limit]
    ec.log(f"{len(todo)} of {len(schedule)} runs to do")
    if not todo:
        return
    by_lane = defaultdict(list)
    for r in todo:
        by_lane[int(r.get("lane", 0)) % args.lanes].append(r)
    gpu = ec.pick_gpu(args.gpu)
    serve_log = out / log_names(args.tag)[0]
    with ec.served(gpu, serve_log, server_flags(), env_extra=server_env(out, args.tag)) as base_url:
        lanes = {i: ec.make_lane(ec.LANES / f"{out.name}_lane{i + args.lane_offset}", base_url)
                 for i in by_lane}
        failures = []

        def worker(i: int):
            for r in by_lane[i]:
                if ec.STOPPING.is_set():
                    return
                if not ec.healthy(base_url):
                    raise RuntimeError("server unhealthy")
                label = r["label"]
                started = time.time()
                meta = ec.run_session(lanes[i], r["row"], r["arm"], r["regime"], runs_dir / label, label,
                                      lead_cap=args.lead_cap, deadline_s=DEADLINE[r["regime"]],
                                      out_cap=args.out_cap)
                ec.log(f"lane{i + args.lane_offset} {label}: {meta.get('status')} {meta.get('wall_s')} s "
                       f"lead={meta.get('lead')} ({time.time() - started:.0f} s)")
                if str(meta.get("status", "")).startswith("failed"):
                    failures.append(label)

        with cf.ThreadPoolExecutor(len(by_lane)) as pool:
            for fut in [pool.submit(worker, i) for i in by_lane]:
                fut.result()
        ec.log(f"sweep done; {len(failures)} failed: {failures[:10]}")


if __name__ == "__main__":
    main()
