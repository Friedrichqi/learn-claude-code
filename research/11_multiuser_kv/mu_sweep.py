#!/usr/bin/env python3
"""One closed-loop cell of the 11_multiuser_kv experiment: N simulated users against one vLLM server.

Each user slot owns a lane (an rsync copy of the repository on node-local disk whose `.env` points
at this cell's server, made by 10_rope_shift's run_sweep.make_lane) and runs one session at a time
(mu_session.py). When a session ends the slot waits U(5,30) s and takes the next counted spec of
the condition, so N users stay active throughout.

  * Warm-up: slot i starts at i*300/N s (+ jitter). With fillers on (every N > 1), a slot's first
    session is a filler spec stopped after U(0.1,1)*S_est(N) s, so counted sessions start at spread
    phases instead of all at t=0.
  * Drain: when no counted spec is left but counted sessions are still running, free slots run
    filler sessions, so the last counted sessions still see N users; they are stopped once every
    counted session is done. Fillers are recorded (label ...-F<k>-w<n>/-d<n>) but never counted.
  * Resume: a counted spec whose run has `.done` with status ok/budget/deadline is skipped. A run
    that ended otherwise (killed, error, terminated at the job's end) is moved to `failed_a<k>/` and
    retried as attempt k+1, at most --max-attempts times.
  * Watchdog: if the server is unhealthy for 120 s, or no token is generated for 600 s while requests
    are running or waiting (the stall 10_rope_shift Part B saw on a small pool), every session is
    stopped and the sweep exits 3 (the pipeline restarts the server and resumes).
  * --dispatch-until: no counted session starts after it; --hard-stop: running sessions are stopped
    (they finalize as `terminated` and are retried by the next job); exit 4 = incomplete.

Outputs under data/multiuser_live/cells/<cond>/: runs/<label>/ (per session), cell_log.jsonl,
sweep.log (console), COMPLETE.

    python research/11_multiuser_kv/mu_sweep.py run --cond n16 --cell g0 --base-url http://127.0.0.1:8601 \
        --lane-root /tmp/$USER/mu/<job>/g0
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402
from live_run import count_calls, finalize  # noqa: E402  (10_rope_shift)
from run_sweep import healthy, make_lane  # noqa: E402  (10_rope_shift)

REPO = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent
CELLS = mw.DATA / "cells"
OK_STATUS = {"ok", "budget", "deadline"}
STAGGER_S = 300.0
GAP_S = (5.0, 30.0)


def metric_values(url: str, names: tuple[str, ...]) -> dict[str, float] | None:
    """Sum of each named vLLM Prometheus sample family (all label sets), or None if unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception:
        return None
    out = {name: 0.0 for name in names}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = re.match(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eEna]+)", line)
        if m and m.group(1) in out:
            try:
                out[m.group(1)] += float(m.group(3))
            except ValueError:
                pass
    return out


class Cell:
    def __init__(self, args):
        self.args = args
        self.c = mw.cond(args.cond)
        if args.fillers != "auto":
            self.c["fillers"] = args.fillers == "on"
        self.n = args.users or self.c["n"]
        self.sessions = mw.load_sessions(Path(args.sessions))
        lo, hi = self.c["specs"]
        counted = [s for s in self.sessions.values() if not s["filler"]]
        counted.sort(key=lambda s: s["index"])
        self.counted = [s["spec"] for s in counted[lo:hi]]
        if args.limit:
            self.counted = self.counted[: args.limit]
        self.fillers = sorted(s for s, v in self.sessions.items() if v["filler"])
        self.root = Path(args.cells_root) / args.cond
        self.runs = self.root / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stop = threading.Event()          # hard stop / stall: end everything now
        self.counted_done = threading.Event()  # every counted spec finished: stop the fillers
        self.procs: dict[str, subprocess.Popen] = {}
        self.exit_code = 0
        self.log_path = self.root / "cell_log.jsonl"
        self.arrival = self._previous_arrivals()
        self.filler_seq = 0
        self.queue = self._todo()
        self.running_counted = 0

    # -- bookkeeping ----------------------------------------------------------------------------
    def _previous_arrivals(self) -> int:
        if not self.log_path.exists():
            return 0
        return sum(1 for _ in self.log_path.open(encoding="utf-8"))

    def label(self, spec: str) -> str:
        return f"MU-{self.c['name']}-{spec}"

    def status_of(self, label: str) -> str | None:
        run_dir = self.runs / label
        meta_path = run_dir / f"{label}.meta.json"
        if not (run_dir / f"{label}.done").exists() or not meta_path.exists():
            return None
        return json.loads(meta_path.read_text(encoding="utf-8")).get("status")

    def _failed_attempts(self, label: str) -> int:
        run_dir = self.runs / label
        return len(list(run_dir.glob("failed_a*"))) if run_dir.exists() else 0

    def _todo(self) -> list[tuple[str, int]]:
        todo = []
        for spec in self.counted:
            label = self.label(spec)
            if self.status_of(label) in OK_STATUS:
                continue
            run_dir = self.runs / label
            current = [p for p in run_dir.iterdir() if not p.name.startswith("failed_a")] if run_dir.exists() else []
            attempt = self._retire(label) if current else self._failed_attempts(label) + 1
            if attempt > self.args.max_attempts:
                print(f"[sweep] {spec}: {attempt - 1} attempts used, giving up", flush=True)
                continue
            todo.append((spec, attempt))
        return todo

    def _retire(self, label: str) -> int:
        """Move the current (failed or unfinished) attempt into failed_a<k>/; returns k + 1."""
        run_dir = self.runs / label
        meta_path = run_dir / f"{label}.meta.json"
        attempt = self._failed_attempts(label) + 1
        if meta_path.exists():
            attempt = int(json.loads(meta_path.read_text(encoding="utf-8")).get("attempt") or attempt)
        target = run_dir / f"failed_a{attempt}"
        target.mkdir(exist_ok=True)
        for item in run_dir.iterdir():
            if not item.name.startswith("failed_a"):
                shutil.move(str(item), str(target / item.name))
        return attempt + 1

    def log(self, record: dict) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    def next_counted(self) -> tuple[str, int, int] | None:
        with self.lock:
            if self.stop.is_set() or not self.queue:
                return None
            if self.args.dispatch_until and time.time() > self.args.dispatch_until:
                return None
            spec, attempt = self.queue.pop(0)
            self.arrival += 1
            self.running_counted += 1
            return spec, attempt, self.arrival

    def next_filler(self, kind: str) -> tuple[str, str, int]:
        with self.lock:
            spec = self.fillers[self.filler_seq % len(self.fillers)]
            self.filler_seq += 1
            self.arrival += 1
            return spec, f"MU-{self.c['name']}-{spec}-{kind}{self.filler_seq:03d}", self.arrival

    def counted_finished(self, spec: str, label: str, status: str | None) -> None:
        """Requeue a failed counted session (unless the cell is stopping); detect the end of the cell."""
        requeue = None
        if status not in OK_STATUS and not self.stop.is_set():
            attempt = self._retire(label)
            if attempt <= self.args.max_attempts:
                requeue = (spec, attempt)
            else:
                print(f"[sweep] {spec}: {attempt - 1} attempts used, giving up", flush=True)
        with self.lock:
            if requeue:
                self.queue.append(requeue)
            self.running_counted -= 1
            if not self.queue and self.running_counted == 0:
                self.counted_done.set()

    def dispatch_closed(self) -> bool:
        """Past --dispatch-until with no counted session running: nothing more can happen this job."""
        with self.lock:
            closed = bool(self.args.dispatch_until) and time.time() > self.args.dispatch_until
            return closed and self.running_counted == 0

    def drain_over(self) -> bool:
        """A drain filler stops when every counted session is done or a retried one is waiting."""
        with self.lock:
            return self.counted_done.is_set() or bool(self.queue)

    # -- running one session ----------------------------------------------------------------------
    def launch(self, lane: Path, spec: str, label: str, attempt: int, arrival: int) -> tuple[subprocess.Popen, Path]:
        run_dir = self.runs / label
        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = [self.args.python, str(lane / "research" / "11_multiuser_kv" / "mu_session.py"), "run",
               "--spec", spec, "--cond", self.c["name"], "--run-dir", str(run_dir),
               "--sessions", str(Path(self.args.sessions).resolve()), "--label", label,
               "--cell", self.args.cell, "--attempt", str(attempt), "--arrival-index", str(arrival),
               "--think-scale", str(self.args.think_scale)]
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1",
               "TOKENIZERS_PARALLELISM": "false", "CUDA_VISIBLE_DEVICES": ""}
        log = (run_dir / "console.log").open("a", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=lane, stdout=log, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)
        log.close()
        with self.lock:
            self.procs[label] = proc
        return proc, run_dir

    def terminate(self, proc: subprocess.Popen, grace: float = 240.0) -> None:
        """SIGTERM the session (the harness unwinds and finalizes), then kill its process group."""
        if proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

    def supervise(self, proc: subprocess.Popen, stop_after: float | None, stop_when) -> None:
        deadline = time.monotonic() + stop_after if stop_after else None
        backstop = time.monotonic() + mw.BUDGET["deadline_s"] + 1800
        while proc.poll() is None:
            if self.stop.is_set() or (stop_when is not None and stop_when()) \
                    or (deadline is not None and time.monotonic() > deadline) or time.monotonic() > backstop:
                self.terminate(proc)
                break
            time.sleep(1.0)

    def finish(self, spec: str, label: str, run_dir: Path, proc: subprocess.Popen, t0: float,
               slot: int, attempt: int, arrival: int, filler: bool, kind: str) -> str | None:
        with self.lock:
            self.procs.pop(label, None)
        if not (run_dir / f"{label}.done").exists():         # died before mu_session could finalize
            row = self.sessions[spec]
            meta = {"label": label, "spec": spec, "cond": self.c["name"], "cell": self.args.cell,
                    "attempt": attempt, "arrival_index": arrival, "filler": filler,
                    "status": f"killed (exit {proc.returncode})", "t_start": t0, "t_end": time.time(),
                    **count_calls(run_dir, label), "salvaged": True}
            finalize(run_dir, label, meta, row["seed"], None, False)
        status = json.loads((run_dir / f"{label}.meta.json").read_text(encoding="utf-8")).get("status")
        self.log({"label": label, "spec": spec, "slot": slot, "kind": kind, "filler": filler,
                  "attempt": attempt, "arrival": arrival, "t0": t0, "t1": time.time(),
                  "exit": proc.returncode, "status": status})
        print(f"[sweep] slot {slot:02d} {label} a{attempt} {kind} status={status} "
              f"{time.time() - t0:.0f}s", flush=True)
        return status

    # -- one user slot ------------------------------------------------------------------------------
    def slot(self, slot: int, lane: Path) -> None:
        rng = random.Random(f"11mu-sweep-{self.c['name']}-{slot}")
        time.sleep(slot * self.args.stagger_s / self.n + rng.uniform(0, 5))
        first = True
        while not self.stop.is_set():
            job = None
            if first and self.c["fillers"]:
                spec, label, arrival = self.next_filler("w")
                trunc = rng.uniform(0.1, 1.0) * mw.SESSION_EST_S.get(self.n, 1500)
                job = (spec, label, 1, arrival, True, "warmup", trunc, None)
            else:
                nxt = self.next_counted()
                if nxt is not None:
                    spec, attempt, arrival = nxt
                    job = (spec, self.label(spec), attempt, arrival, False, "counted", None, None)
                elif self.c["fillers"] and not self.drain_over() and not self.stop.is_set():
                    spec, label, arrival = self.next_filler("d")
                    job = (spec, label, 1, arrival, True, "drain", None, self.drain_over)
                elif not self.counted_done.is_set() and not self.stop.is_set() and not self.dispatch_closed():
                    time.sleep(5)          # a counted session may still fail and be requeued
                    continue
            first = False
            if job is None:
                return
            spec, label, attempt, arrival, filler, kind, stop_after, stop_when = job
            t0 = time.time()
            proc, run_dir = self.launch(lane, spec, label, attempt, arrival)
            self.supervise(proc, stop_after, stop_when)
            status = self.finish(spec, label, run_dir, proc, t0, slot, attempt, arrival, filler, kind)
            if kind == "counted":
                self.counted_finished(spec, label, status)
            if self.stop.is_set():
                return
            time.sleep(rng.uniform(*GAP_S))

    # -- watchdog -----------------------------------------------------------------------------------
    def watchdog(self) -> None:
        metrics_url = f"{self.args.base_url}/metrics"
        unhealthy_since = None
        last_tokens, last_progress = None, time.monotonic()
        while not self.stop.is_set() and not self.all_done():
            time.sleep(30)
            if self.args.hard_stop and time.time() > self.args.hard_stop:
                print("[sweep] hard stop reached: stopping every session", flush=True)
                self.exit_code = 4
                self.stop.set()
                break
            if not healthy(self.args.base_url):
                unhealthy_since = unhealthy_since or time.monotonic()
                if time.monotonic() - unhealthy_since > 120:
                    self.abort("server unhealthy for 120 s")
                    break
                continue
            unhealthy_since = None
            values = metric_values(metrics_url, ("vllm:generation_tokens_total", "vllm:num_requests_running",
                                                 "vllm:num_requests_waiting"))
            if values is None:
                continue
            tokens = values["vllm:generation_tokens_total"]
            busy = values["vllm:num_requests_running"] + values["vllm:num_requests_waiting"] > 0
            if last_tokens is None or tokens > last_tokens or not busy:
                last_progress = time.monotonic()
            last_tokens = tokens
            if time.monotonic() - last_progress > self.args.stall_s:
                self.abort(f"no generated token for {self.args.stall_s:.0f} s with requests queued")
                break

    def abort(self, why: str) -> None:
        print(f"[sweep] WATCHDOG: {why}; stopping the cell", flush=True)
        (self.root / "STALL").write_text(f"{time.strftime('%FT%T')} {why}\n", encoding="utf-8")
        self.exit_code = 3
        self.stop.set()

    def all_done(self) -> bool:
        with self.lock:
            return self.counted_done.is_set() and not self.procs

    def run(self) -> int:
        todo = len(self.queue)
        print(f"[sweep] cond {self.c['name']}: N={self.n}, {len(self.counted)} counted specs, {todo} to do, "
              f"fillers={'on' if self.c['fillers'] else 'off'}", flush=True)
        if not todo:
            (self.root / "COMPLETE").write_text(time.strftime("%FT%T") + "\n", encoding="utf-8")
            return 0
        while not healthy(self.args.base_url):
            print("[sweep] waiting for the server", flush=True)
            time.sleep(15)
        lane_root = Path(self.args.lane_root)
        lanes = []
        for i in range(self.n):
            lane = lane_root / f"lane{i:02d}"
            make_lane(lane, self.args.base_url, mw.MODEL)
            lanes.append(lane)
        print(f"[sweep] {len(lanes)} lanes ready under {lane_root}", flush=True)
        dog = threading.Thread(target=self.watchdog, daemon=True, name="watchdog")
        dog.start()
        threads = [threading.Thread(target=self.slot, args=(i, lane), daemon=True, name=f"slot{i:02d}")
                   for i, lane in enumerate(lanes)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.stop.set()
        remaining = [spec for spec in self.counted if self.status_of(self.label(spec)) not in OK_STATUS]
        if not remaining and self.exit_code == 0:
            (self.root / "COMPLETE").write_text(time.strftime("%FT%T") + "\n", encoding="utf-8")
            print("[sweep] cell COMPLETE", flush=True)
            return 0
        print(f"[sweep] cell incomplete: {len(remaining)} counted specs left (exit {self.exit_code or 4})",
              flush=True)
        return self.exit_code or 4


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one cell until its counted sessions are done (resumable)")
    r.add_argument("--cond", required=True, choices=sorted(mw.CONDITIONS))
    r.add_argument("--cell", default="g0", help="GPU/cell tag recorded in every session's meta")
    r.add_argument("--base-url", required=True)
    r.add_argument("--lane-root", required=True, help="node-local directory for the N lanes")
    r.add_argument("--sessions", default=str(mw.SESSIONS))
    r.add_argument("--users", type=int, default=0, help="override N (smoke tests)")
    r.add_argument("--limit", type=int, default=0, help="only the first K counted specs (smoke tests)")
    r.add_argument("--max-attempts", type=int, default=3)
    r.add_argument("--stall-s", type=float, default=600.0)
    r.add_argument("--dispatch-until", type=float, default=0.0, help="epoch seconds; 0 = no limit")
    r.add_argument("--hard-stop", type=float, default=0.0, help="epoch seconds; 0 = no limit")
    r.add_argument("--python", default=sys.executable)
    r.add_argument("--cells-root", default=str(CELLS), help="where cells/<cond>/ live (tests use a temp dir)")
    r.add_argument("--think-scale", type=float, default=1.0, help="scale think times (tests only)")
    r.add_argument("--fillers", choices=["auto", "on", "off"], default="auto",
                   help="auto: the condition's setting (on for every N > 1)")
    r.add_argument("--stagger-s", type=float, default=STAGGER_S, help="spread of the slots' first starts")
    s = sub.add_parser("status", help="print each condition's progress")
    s.add_argument("--cond", action="append", default=[])
    args = ap.parse_args()
    if args.cmd == "status":
        for name in args.cond or sorted(mw.CONDITIONS):
            root = CELLS / name
            if not root.exists():
                continue
            metas = [json.loads(p.read_text()) for p in root.glob("runs/*/*.meta.json")]
            counted = [m for m in metas if not m.get("filler")]
            ok = sum(m.get("status") in OK_STATUS for m in counted)
            print(f"{name}: {ok}/{mw.cond(name)['specs'][1] - mw.cond(name)['specs'][0]} counted ok, "
                  f"{len(metas) - len(counted)} fillers, complete={'yes' if (root / 'COMPLETE').exists() else 'no'}")
        return
    sys.exit(Cell(args).run())


if __name__ == "__main__":
    main()
