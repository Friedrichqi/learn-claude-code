#!/usr/bin/env python3
"""Dispatch the live-run manifest over lanes and local vLLM replicas; resumable.

A lane is an rsync copy of this repository outside it (node-local disk), with its own `.env`
pointing the harness at one vLLM replica -- the same device 05's pipeline uses (`step_lane`): the
harness's load_dotenv(override=True) makes the lane's `.env` win, a stray write cannot touch the
repository, and profile_run's per-run state wipe stays inside the lane. Each lane runs one session
at a time (profile_run wipes `.memory/.tasks/...` at its repo root), so lanes = concurrency.

A run is done when `runs/<label>/<label>.done` exists; rerunning skips done labels, so a shard job
that hits its time limit resumes where it stopped.

    python research/10_rope_shift/run_sweep.py --shard 0 --shards 4 --lanes 12 \
        --base-url http://127.0.0.1:8001 [--base-url http://127.0.0.1:8002] --lane-root /tmp/$USER/lanes
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

from live_run import count_calls, finalize  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data" / "rope_shift_live"
RSYNC_EXCLUDES = [".git", "s15_integrated_harness/traces", "research/*/data", "profiling_sandbox",
                  "__pycache__", ".memory", ".tasks*", ".mailboxes", ".transcripts", ".task_outputs",
                  ".worktrees", ".scheduled_tasks.json", ".teams", "*.log", ".env", "weekly_progress",
                  ".zcode", ".claude"]
PROFILE_FLAGS = ["--context-limit", "50000", "--no-timestamp", "--no-reads-log",
                 "--client-max-retries", "0", "--server-info", "--quiet-seconds", "20"]


def make_lane(lane: Path, base_url: str, model: str) -> None:
    lane.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete"]
    for pattern in RSYNC_EXCLUDES:
        cmd += ["--exclude", pattern]
    subprocess.run(cmd + [f"{REPO}/", f"{lane}/"], check=True)
    (lane / ".env").write_text(f"ANTHROPIC_API_KEY=EMPTY\nANTHROPIC_BASE_URL={base_url}\nMODEL_ID={model}\n",
                               encoding="utf-8")


def healthy(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def run_one(row: dict, lane: Path, base_url: str, max_seconds: int, python: str) -> dict:
    label = row["label"]
    run_dir = DATA / "runs" / label
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = run_dir / f"{label}.prompt.txt"
    prompt_file.write_text(row["prompt"], encoding="utf-8")
    writable = row["family"] == "MODIFY"
    cmd = [python, str(lane / "research" / "10_rope_shift" / "live_run.py"),
           "--label", label, "--run-dir", str(run_dir), "--seed", row["seed"],
           "--sandbox", row["sandbox"]] + (["--writable"] if writable else []) + [
        "--", "--label", label, "--prompt-file", str(prompt_file), "--trace-dir", str(run_dir),
        "--max-seconds", str(max_seconds), "--answer-out", str(run_dir / f"{label}.answer.json"),
        *PROFILE_FLAGS] + (["--write-root", row["sandbox"]] if writable else [])
    while not healthy(base_url):
        time.sleep(15)
    started = time.time()
    with (run_dir / "console.log").open("w", encoding="utf-8") as log:
        try:
            # live_run stops the lead at --max-seconds; this is only the backstop for a hung session
            proc = subprocess.run(cmd, cwd=lane, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=max_seconds + 600, env={**os.environ, "PYTHONUNBUFFERED": "1"})
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    if not (run_dir / f"{label}.done").exists():
        salvage(row, run_dir, lane, f"killed (exit {code})", time.time() - started)
    return {"label": label, "exit": code, "done": True, "wall_s": round(time.time() - started, 1),
            "lane": lane.name, "base_url": base_url}


def salvage(row: dict, run_dir: Path, lane: Path | None, status: str, wall: float | None) -> dict:
    """Finalize a run whose session died before live_run could: its capture is flushed per call, so
    everything up to the kill is kept and marked done (status says it was killed)."""
    label = row["label"]
    sandbox = (lane / row["sandbox"]) if lane is not None else None
    meta = {"label": label, "status": status, "exit_code": None,
            "wall_s": round(wall, 1) if wall is not None else None, **count_calls(run_dir, label),
            "lane": str(lane) if lane else None, "seed": row["seed"], "sandbox": row["sandbox"],
            "salvaged": True}
    return finalize(run_dir, label, meta, row["seed"], sandbox, row["family"] == "MODIFY")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(DATA / "manifest.jsonl"))
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--lanes", type=int, default=12)
    ap.add_argument("--base-url", action="append", default=[], help="one per vLLM replica")
    ap.add_argument("--model", default="Qwen/Qwen3-32B")
    ap.add_argument("--lane-root", default=f"/tmp/{os.environ.get('USER', 'user')}/rope_shift_lanes")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--limit", type=int, default=0, help="at most this many runs (smoke)")
    ap.add_argument("--labels", default="", help="comma-separated labels to run (smoke)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--salvage", action="store_true",
                    help="only finalize this shard's runs that have a capture but no .done marker")
    args = ap.parse_args()

    rows = [json.loads(line) for line in open(args.manifest, encoding="utf-8")]
    rows = [r for r in rows if r["index"] % args.shards == args.shard]
    if args.labels:
        wanted = set(args.labels.split(","))
        rows = [r for r in rows if r["label"] in wanted]
    if args.salvage:
        for r in rows:
            run_dir = DATA / "runs" / r["label"]
            # only runs a sweep already gave up on (.failed); a live run also lacks .done
            if (run_dir / f"{r['label']}.failed").exists() and not (run_dir / f"{r['label']}.done").exists():
                meta = salvage(r, run_dir, None, "killed (salvaged after the sweep)", None)
                print(f"[sweep] salvaged {r['label']}: {meta['lead_calls']} lead calls", flush=True)
        return
    todo = [r for r in rows if not (DATA / "runs" / r["label"] / f"{r['label']}.done").exists()]
    if not args.base_url:
        ap.error("--base-url is required to run sessions")
    if args.limit:
        todo = todo[: args.limit]
    print(f"[sweep] shard {args.shard}/{args.shards}: {len(rows)} runs, {len(todo)} to do, "
          f"{args.lanes} lanes over {len(args.base_url)} replica(s)", flush=True)
    if not todo:
        return

    lane_root = Path(args.lane_root)
    lanes = []
    for i in range(min(args.lanes, len(todo))):
        base_url = args.base_url[i % len(args.base_url)]
        lane = lane_root / f"lane{args.shard:02d}_{i:02d}"
        make_lane(lane, base_url, args.model)
        lanes.append((lane, base_url))
    print(f"[sweep] {len(lanes)} lanes ready under {lane_root}", flush=True)

    work: queue.Queue = queue.Queue()
    for row in todo:
        work.put(row)
    log_path = DATA / "sweep_log.jsonl"
    log_lock = threading.Lock()
    finished = [0]

    def worker(lane: Path, base_url: str):
        while True:
            try:
                row = work.get_nowait()
            except queue.Empty:
                return
            result = run_one(row, lane, base_url, args.max_seconds, args.python)
            with log_lock:
                finished[0] += 1
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({**result, "shard": args.shard, "ts": time.time()}) + "\n")
                print(f"[sweep] {finished[0]}/{len(todo)} {result['label']} exit={result['exit']} "
                      f"done={result['done']} {result['wall_s']}s", flush=True)

    threads = [threading.Thread(target=worker, args=lane_base, daemon=True) for lane_base in lanes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print("[sweep] shard finished", flush=True)


if __name__ == "__main__":
    main()
