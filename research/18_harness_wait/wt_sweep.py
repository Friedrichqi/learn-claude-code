#!/usr/bin/env python3
"""One measurement-window cell of research/18_harness_wait: N simulated users against one vLLM server.

Topic 11's closed loop (research/11_multiuser_kv/mu_sweep.py: N user slots, each running one 4-turn s15
session at a time from a lane copy of the repository, U(5,30) s between sessions, staggered first starts,
truncated warm-up fillers) with a fixed clock instead of a fixed set of sessions:

  * warm-up WARMUP_S from the first slot start, then the measurement window WINDOW_S; at the window's end
    every session is stopped (SIGTERM: the harness unwinds and its capture, trace and meta are written)
    and the cell ends; nothing is retried or resumed after that;
  * counted specs come from one queue in manifest order, as in topic 11 (any cell runs specs 0, 1, 2, ...
    in that order, so arms see the same task mix);
  * each session gets the condition's client_env (the S15_* settings that select the arm);
  * lanes exclude the 30 GB of research/16_epic_harness/harness_eval and other large folders;
  * window.json in the cell folder records the clock (wall times of start, window start and end);
  * a watchdog notes in cell_log.jsonl every 30 s in which the server had nothing running or waiting
    while sessions were alive (all of them holding, or a stall), and topic 11's stall watchdog still aborts
    a cell whose server stops generating while requests are queued.

    MU_EXTRA_CONDITIONS=research/18_harness_wait/data/conditions.json \\
    python research/18_harness_wait/wt_sweep.py run --cond w16_m_stock --cell A --base-url http://127.0.0.1:8611 \\
        --lane-root /data/fritz/wt_lanes/w16_m_stock
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
os.environ.setdefault("MU_EXTRA_CONDITIONS", str(DIR / "data" / "conditions.json"))
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import mu_sweep  # noqa: E402  research/11_multiuser_kv
import mu_workloads as mw  # noqa: E402
from run_sweep import RSYNC_EXCLUDES, healthy  # noqa: E402  research/10_rope_shift

import wt_workloads as ww  # noqa: E402

EXTRA_EXCLUDES = ["research/16_epic_harness/harness_eval", "research/*/.venv", "research/*/.deps", "node_modules",
                  ".pytest_cache"]


def make_lane(lane: Path, base_url: str, model: str) -> None:
    lane.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete"]
    for pattern in list(RSYNC_EXCLUDES) + EXTRA_EXCLUDES:
        cmd += ["--exclude", pattern]
    subprocess.run(cmd + [f"{mw.REPO}/", f"{lane}/"], check=True)
    (lane / ".env").write_text(f"ANTHROPIC_API_KEY=EMPTY\nANTHROPIC_BASE_URL={base_url}\nMODEL_ID={model}\n",
                               encoding="utf-8")


class WindowCell(mu_sweep.Cell):
    def __init__(self, args):
        super().__init__(args)
        self.warmup_s = args.warmup_s if args.warmup_s >= 0 else float(self.c.get("warmup_s", ww.WARMUP_S))
        self.window_s = args.window_s if args.window_s > 0 else float(self.c.get("window_s", ww.WINDOW_S))
        self.client_env = {k: str(v) for k, v in (self.c.get("client_env") or {}).items()}
        self.t_start = self.window_start = self.window_end = None
        self.window_done = threading.Event()

    def _todo(self) -> list[tuple[str, int]]:
        return [(spec, 1) for spec in self.counted]          # a window cell never resumes

    def next_counted(self):
        if self.window_end is not None and time.time() >= self.window_end:
            return None
        return super().next_counted()

    def clock(self) -> None:
        """End the cell at the window's end."""
        while not self.stop.is_set():
            if time.time() >= self.window_end:
                print(f"[sweep] window over at {time.strftime('%FT%T')}: stopping every session", flush=True)
                self.log({"event": "window-end", "wall": time.time()})
                self.window_done.set()
                self.stop.set()
                break
            time.sleep(1.0)

    def idle_watch(self) -> None:
        """Note every 30 s span with sessions alive but nothing running or waiting on the server."""
        url = f"{self.args.base_url}/metrics"
        while not self.stop.is_set():
            time.sleep(30)
            with self.lock:
                alive = len(self.procs)
            if not alive:
                continue
            values = mu_sweep.metric_values(url, ("vllm:num_requests_running", "vllm:num_requests_waiting"))
            if values is not None and values["vllm:num_requests_running"] + values["vllm:num_requests_waiting"] == 0:
                self.log({"event": "server-idle", "wall": time.time(), "sessions_alive": alive})

    def run(self) -> int:
        if any(self.runs.iterdir()):
            print(f"[sweep] {self.runs} is not empty: a window cell runs once (move the old cell away)", flush=True)
            return 2
        print(f"[sweep] cond {self.c['name']} ({self.c.get('arm')}): N={self.n}, warm-up {self.warmup_s:.0f} s, "
              f"window {self.window_s:.0f} s, client env {self.client_env}", flush=True)
        while not healthy(self.args.base_url):
            print("[sweep] waiting for the server", flush=True)
            time.sleep(15)
        lane_root = Path(self.args.lane_root)
        lanes = []
        for i in range(self.n):
            lane = lane_root / f"lane{i:02d}"
            make_lane(lane, self.args.base_url, self.c.get("model", mw.MODEL))
            lanes.append(lane)
        print(f"[sweep] {len(lanes)} lanes ready under {lane_root}", flush=True)
        # one arm per cell: every session launched from now on inherits the arm's S15_* settings
        # (Cell.launch copies os.environ); set once, before any slot thread starts
        os.environ.update(self.client_env)
        self.t_start = time.time()
        self.window_start = self.t_start + self.warmup_s
        self.window_end = self.window_start + self.window_s
        (self.root / "window.json").write_text(json.dumps(
            {"cond": self.c["name"], "arm": self.c.get("arm"), "n": self.n, "cell": self.args.cell,
             "t_start": self.t_start, "window_start": self.window_start, "window_end": self.window_end,
             "warmup_s": self.warmup_s, "window_s": self.window_s, "client_env": self.client_env,
             "base_url": self.args.base_url}, indent=1), encoding="utf-8")
        threads = [threading.Thread(target=self.watchdog, daemon=True, name="watchdog"),
                   threading.Thread(target=self.clock, daemon=True, name="clock"),
                   threading.Thread(target=self.idle_watch, daemon=True, name="idle-watch")]
        threads += [threading.Thread(target=self.slot, args=(i, lane), daemon=True, name=f"slot{i:02d}")
                    for i, lane in enumerate(lanes)]
        for thread in threads:
            thread.start()
        for thread in threads[3:]:
            thread.join()
        self.stop.set()
        complete = self.window_done.is_set() and self.exit_code == 0
        (self.root / ("COMPLETE" if complete else "INCOMPLETE")).write_text(time.strftime("%FT%T") + "\n",
                                                                           encoding="utf-8")
        print(f"[sweep] cell {'COMPLETE' if complete else 'INCOMPLETE'} (exit {self.exit_code})", flush=True)
        return 0 if complete else (self.exit_code or 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one window cell")
    r.add_argument("--cond", required=True, choices=sorted(mw.CONDITIONS))
    r.add_argument("--cell", default="A", help="GPU pair tag recorded in every session's meta")
    r.add_argument("--base-url", required=True)
    r.add_argument("--lane-root", required=True)
    r.add_argument("--sessions", default=str(ww.SESSIONS))
    r.add_argument("--cells-root", default=str(ww.CELLS))
    r.add_argument("--python", default=sys.executable)
    r.add_argument("--warmup-s", type=float, default=-1.0, help="override the condition's warm-up")
    r.add_argument("--window-s", type=float, default=0.0, help="override the condition's window")
    r.add_argument("--users", type=int, default=0)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--max-attempts", type=int, default=3)
    r.add_argument("--stall-s", type=float, default=600.0)
    r.add_argument("--think-scale", type=float, default=1.0)
    r.add_argument("--fillers", choices=["auto", "on", "off"], default="auto")
    r.add_argument("--stagger-s", type=float, default=mu_sweep.STAGGER_S)
    args = ap.parse_args()
    args.dispatch_until = args.hard_stop = 0.0
    sys.exit(WindowCell(args).run())


if __name__ == "__main__":
    main()
