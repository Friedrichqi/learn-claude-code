#!/usr/bin/python3 -I
"""Topic 24's hardware-counter sampler. The one part of the study that runs as root (PLAN.md, "Hardware counters").

Start it once per chain, from the repository root:

    sudo -b /usr/bin/python3 -I research/24_spark_concurrency/sp_hw.py run \
        --out research/24_spark_concurrency/data/hw --owner friedrichqi

It runs two samplers, pinned with itself to cores 0, 11 and 13, until <out>/control says `stop`, it gets SIGTERM, or
60 hours pass:

  perf     `perf stat -a -A -x, -I 1000` with up to eight events per core on both core types, one CSV per hour
           (perf_<n>.csv). The event names come from sp_hw_events.txt next to this file (one per line, read each time
           perf starts, so after a pause -> run too); every name must match [a-z0-9_]+ and exist in the PMU's sysfs
           event folder, or it is dropped. Without the file: cycles, instructions, memory stall cycles,
           last-level-cache read misses, L3 refills, L2 write-backs.
  gpu      `nsys profile` with GPU-metrics sampling only (no CUDA trace, nothing attached to any process): the metric set
           sp24.config at 100 samples/s, one report per 30 minutes (gpu_<n>.nsys-rep). Nsight wants the file named
           after the set's alias (sp24). If Nsight rejects that set (the report never appears), it falls back to
           Nsight's own `gb20b` set and says so in hw.log; the custom set is tried again after every pause -> run,
           so the file can be corrected without restarting this program. A sampler that keeps failing is restarted
           with a growing delay (5 s to 5 min), not every second.

Only one copy runs per <out> folder (a lock file); a second start exits at once. <out> itself is handed to --owner so
the unprivileged chain can write the control file.

Control file <out>/control (written by the unprivileged chain, read every second):
  run     sample (also when the file is missing)
  pause   stop both samplers until the file says run again (overhead checks)
  stop    stop both samplers and exit

Output in <out>/<session>/, a new folder per start (created by root, so nothing in it can be a planted link):
  perf_<n>.csv, gpu_<n>.nsys-rep           the samples
  segments.jsonl                           one line per finished file: kind, file, rc, start and end on CLOCK_MONOTONIC (t)
                                           and wall clock (w), ns; perf's interval times count from its start line
  heartbeat.json                           rewritten every 5 s: state, pid, which samplers run, the current files
  hw.log                                   what happened
Every finished file, and the folder, is handed to --owner. Standard library only; run it with the system Python in
isolated mode (-I) so nothing from a user's environment is imported as root.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERF = "/usr/bin/perf"
NSYS = "/usr/local/cuda/bin/nsys"
CORES = {0, 11, 13}
EVENTS = ("cpu_cycles", "inst_retired", "stall_backend_mem", "ll_cache_miss_rd", "l3d_cache_refill", "l2d_cache_wb")
PMUS = ("armv8_pmuv3_0", "armv8_pmuv3_1")        # A725 (cpus 0-4, 10-14), X925 (cpus 5-9, 15-19)
PERF_ROTATE_S = 3600
GPU_SEGMENT_S = 1800
GPU_HZ = 100
MAX_HOURS = 60.0
HEARTBEAT_S = 5.0


def clocks() -> dict:
    return {"t": time.monotonic_ns(), "w": time.time_ns()}


class Sampler:
    def __init__(self, a):
        self.a = a
        self.fake = a.fake
        out = Path(a.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        self.out = out
        pw = pwd.getpwnam(a.owner)
        self.uid, self.gid = pw.pw_uid, pw.pw_gid
        if not a.fake:
            self._chown(out)                        # the chain writes <out>/control
        self._lock = os.open(out / "lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"sp_hw.py: another copy already samples into {out}; not starting a second one", file=sys.stderr)
            raise SystemExit(3)
        self.control = out / "control"
        self.session = out / time.strftime("session_%Y%m%d_%H%M%S")
        self.session.mkdir()                       # fails if it exists: a fresh folder per start
        self.t_start = time.monotonic()
        self.perf = None                            # (Popen, file, start clocks, index)
        self.gpu = None
        self.n_perf = 0
        self.n_gpu = 0
        self.gpu_set = str(HERE / "sp24.config")
        self.gpu_fallback = False
        self.gpu_fails = 0                          # consecutive segments without a report
        self.gpu_retry_at = 0.0
        self.stopping = False
        self.state = "run"
        self._last_hb = 0.0
        self._chown(self.session)

    # -- helpers ------------------------------------------------------------------------------------------------------
    def log(self, msg: str) -> None:
        with open(self.session / "hw.log", "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + msg + "\n")
        self._chown(self.session / "hw.log")

    def _chown(self, p: Path) -> None:
        try:
            os.chown(p, self.uid, self.gid, follow_symlinks=False)
        except OSError:
            pass

    def read_control(self) -> str:
        try:
            fd = os.open(self.control, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return "run"
        try:
            word = os.read(fd, 16).decode(errors="ignore").strip().lower()
        finally:
            os.close(fd)
        return word if word in ("run", "pause", "stop") else "run"

    def segment(self, kind: str, path: Path, rc, start: dict, extra: dict | None = None) -> None:
        rec = {"kind": kind, "file": path.name, "rc": rc, "start": start, "end": clocks(), **(extra or {})}
        with open(self.session / "segments.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        self._chown(self.session / "segments.jsonl")
        for p in self.session.glob(path.stem + "*"):
            self._chown(p)

    def heartbeat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_hb < HEARTBEAT_S:
            return
        self._last_hb = now
        hb = {**clocks(), "pid": os.getpid(), "state": self.state, "session": self.session.name,
              "perf": self.perf[1].name if self.perf else None, "gpu": self.gpu[1].name if self.gpu else None,
              "gpu_set": "gb20b (fallback)" if self.gpu_fallback else "sp24.config", "gpu_fails": self.gpu_fails,
              "uptime_s": round(now - self.t_start, 1), "fake": self.fake}
        tmp = self.session / "heartbeat.json.tmp"
        tmp.write_text(json.dumps(hb))
        self._chown(tmp)
        os.replace(tmp, self.session / "heartbeat.json")
        link = self.out / "heartbeat.json"           # a copy at the top for the chain (not a link: root writes it)
        try:
            fd = os.open(link, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(hb))
            self._chown(link)
        except OSError:
            pass

    # -- perf ---------------------------------------------------------------------------------------------------------
    def events(self) -> dict:
        """PMU -> event names: sp_hw_events.txt if it has valid names, else EVENTS. Names are checked against sysfs,
        so nothing but a PMU event name can reach perf's command line."""
        names = list(EVENTS)
        try:
            fd = os.open(HERE / "sp_hw_events.txt", os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as f:
                want = [ln.split("#")[0].strip() for ln in f.read(4096).splitlines()]
            want = [w for w in want if re.fullmatch(r"[a-z0-9_]{1,40}", w)][:8]
            if want:
                names = want
        except OSError:
            pass
        out = {}
        for pmu in PMUS:
            have = Path(f"/sys/bus/event_source/devices/{pmu}/events")
            out[pmu] = [e for e in names if (have / e).exists()]
        return out

    def perf_cmd(self, path: Path) -> list[str]:
        if self.fake:
            return [sys.executable, "-c", "import time,sys\nf=open(sys.argv[1],'w')\nwhile True:\n f.write('1.0,CPU0,1,,x,1,100.00,,\\n');f.flush();time.sleep(1)", str(path)]
        evs = self.events()
        self.log("perf events: " + "; ".join(f"{p}: {' '.join(e)}" for p, e in evs.items()))
        ev = ",".join(f"{p}/{e}/" for p, es in evs.items() for e in es)
        return [PERF, "stat", "-a", "-A", "-x", ",", "-I", "1000", "-o", str(path), "-e", ev]

    def start_perf(self) -> None:
        path = self.session / f"perf_{self.n_perf:03d}.csv"
        start = clocks()
        p = subprocess.Popen(self.perf_cmd(path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=open(self.session / f"perf_{self.n_perf:03d}.err", "w"))
        self.perf = (p, path, start, self.n_perf)
        self.n_perf += 1
        self.log(f"perf started: {path.name} pid {p.pid}")

    def stop_perf(self) -> None:
        if not self.perf:
            return
        p, path, start, _ = self.perf
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self.segment("perf", path, p.returncode, start)
        self.log(f"perf stopped: {path.name} rc {p.returncode}")
        self.perf = None

    # -- gpu ----------------------------------------------------------------------------------------------------------
    def gpu_cmd(self, base: Path) -> list[str]:
        if self.fake:
            return [sys.executable, "-c", "import time,sys\ntime.sleep(float(sys.argv[2]))\nopen(sys.argv[1]+'.nsys-rep','w').write('x')",
                    str(base), str(GPU_SEGMENT_S)]
        gset = "gb20b" if self.gpu_fallback else f"file:{self.gpu_set}"
        return [NSYS, "profile", "--trace=none", "--sample=none", "--cpuctxsw=none", "--gpu-metrics-devices=0",
                f"--gpu-metrics-set={gset}", f"--gpu-metrics-frequency={GPU_HZ}", f"--output={base}",
                "--force-overwrite=true", "--stats=false", "sleep", str(GPU_SEGMENT_S)]

    def start_gpu(self) -> None:
        base = self.session / f"gpu_{self.n_gpu:03d}"
        start = clocks()
        p = subprocess.Popen(self.gpu_cmd(base), stdin=subprocess.DEVNULL,
                             stdout=open(self.session / f"gpu_{self.n_gpu:03d}.out", "w"), stderr=subprocess.STDOUT,
                             start_new_session=True)
        self.gpu = (p, base, start, self.n_gpu)
        self.n_gpu += 1
        self.log(f"gpu started: {base.name} pid {p.pid} set {'gb20b' if self.gpu_fallback else 'sp24'}")

    def stop_gpu(self, wait_s: float = 120) -> None:
        if not self.gpu:
            return
        p, base, start, _ = self.gpu
        if p.poll() is None:
            # nsys stops the profiled `sleep` and writes its report on SIGINT
            try:
                os.killpg(p.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                p.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
        self.finish_gpu()

    def finish_gpu(self) -> None:
        p, base, start, n = self.gpu
        rep = Path(str(base) + ".nsys-rep")
        ok = rep.exists()
        self.segment("gpu", rep, p.returncode, start, {"report": ok, "set": "gb20b" if self.gpu_fallback else "sp24"})
        self.log(f"gpu finished: {rep.name} rc {p.returncode} report {ok}")
        if ok:
            self.gpu_fails = 0
        elif not self.fake:
            age = (time.monotonic_ns() - start["t"]) / 1e9
            try:
                why = " | ".join(Path(str(base) + ".out").read_text(errors="ignore").split("\n")[:2])[:300]
            except OSError:
                why = ""
            if not self.gpu_fallback and age < 120:
                # the custom set may have been rejected: fall back to Nsight's own set for this GPU
                self.gpu_fallback = True
                self.log(f"custom GPU metric set gave no report: falling back to gb20b ({why})")
            else:
                self.gpu_fails += 1
                delay = min(300.0, 5.0 * 2 ** (self.gpu_fails - 1))
                self.gpu_retry_at = time.monotonic() + delay
                self.log(f"gpu sampler failed {self.gpu_fails} time(s) in a row; next try in {delay:.0f} s ({why})")
        self.gpu = None

    # -- loop ---------------------------------------------------------------------------------------------------------
    def run(self) -> int:
        try:
            os.sched_setaffinity(0, CORES)
        except OSError as e:
            self.log(f"could not pin to {sorted(CORES)}: {e!r}")
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))
        self.log(f"session {self.session.name}: pid {os.getpid()}, owner {self.a.owner}, fake {self.fake}")
        t_perf = 0.0
        while not self.stopping:
            if time.monotonic() - self.t_start > MAX_HOURS * 3600:
                self.log(f"{MAX_HOURS} h reached: stopping")
                break
            word = self.read_control()
            if word == "stop":
                self.log("control: stop")
                break
            if word == "pause" and self.state != "pause":
                self.log("control: pause")
                self.stop_perf()
                self.stop_gpu()
                self.state = "pause"
            elif word == "run" and self.state != "run":
                self.log("control: run")
                self.state = "run"
                self.gpu_fallback, self.gpu_fails, self.gpu_retry_at = False, 0, 0.0   # try the custom set again
            if self.state == "run":
                if self.perf and (self.perf[0].poll() is not None or time.monotonic() - t_perf >= PERF_ROTATE_S):
                    self.stop_perf()
                if not self.perf:
                    self.start_perf()
                    t_perf = time.monotonic()
                if self.gpu and self.gpu[0].poll() is not None:
                    self.finish_gpu()
                if not self.gpu and time.monotonic() >= self.gpu_retry_at:
                    self.start_gpu()
            self.heartbeat()
            time.sleep(1.0)
        self.state = "stopping"
        self.heartbeat(force=True)
        self.stop_perf()
        self.stop_gpu()
        self.state = "stopped"
        self.heartbeat(force=True)
        self.log("stopped")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("run",))
    ap.add_argument("--out", required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--fake", action="store_true", help="tests: stand-in processes instead of perf and nsys, no root")
    a = ap.parse_args()
    if not a.fake and os.geteuid() != 0:
        print("sp_hw.py run needs root (sudo); use --fake for tests", file=sys.stderr)
        return 2
    return Sampler(a).run()


if __name__ == "__main__":
    sys.exit(main())
