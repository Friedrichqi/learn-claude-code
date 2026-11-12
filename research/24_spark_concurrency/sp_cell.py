#!/usr/bin/env python3
"""One topic-24 cell: N concurrent s15 agent users against a fresh vLLM server on the DGX Spark (PLAN.md, "One cell").

    ~/.venv/bin/python research/24_spark_concurrency/sp_cell.py run --n 8 --name n08 --hard-limit-h 4
    ~/.venv/bin/python research/24_spark_concurrency/sp_cell.py run --n 2 --name smoke --max-lead-calls 6 --hard-limit-h 0.75

Steps (every boundary is written to <run>/cell.json on CLOCK_MONOTONIC `t` and wall `w`, ns):
  1. preflight: no GPU compute job, memory and disk, no task containers left, the root counter sampler's heartbeat
  2. samplers (exp02's sampler.py at 10/s, sp_extra.py at 1/s) and the latency probe (exp02's umbench on core 8,
     back-to-back 20 s windows with a 10 ms series); evict the checkpoint's page cache; start vLLM (exp02's Server,
     topic-24 flags and the sp_reqlog plugin); evict again; one throwaway request
  3. idle baseline: 60 s of probe, then exp02's 4- and 12-core stream readers (15 s each, a separate umbench process so
     their buffers do not stay allocated)
  4. Harbor: N concurrent agents (sp_agent.SPAgent) over data/queue/main, eight passes (-k 8), verifier off
  5. the window ends when every one of the first N sessions (by agent start) has ended, or at --hard-limit-h
  6. coda: the two stream readers, 20 s each, users still running (skipped below 8 GiB MemAvailable)
  7. teardown: SIGTERM Harbor, then leftover children and containers, the server, the probe, the samplers

A cell ends early, marked failed, if the server dies or stops producing steps for 20 min while sessions are active, or
Harbor exits, or MemAvailable falls below 4 GiB. The chain (sp_chain.py) moves on.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402

HARBOR = C.HARBOR_VENV / "bin" / "harbor"
ADAPTER = C.REPO / "research" / "16_epic_harness" / "harness_eval" / "adapter"
STALL_S = 20 * 60
MEM_STOP_GIB = 4.0
MEM_CODA_GIB = 8.0
HB_FRESH_S = 60
STABLE_FROM_S = 3600                  # the stable stop is first tried one hour into the window ...
STABLE_EVERY_S = 600                  # ... and then every 10 minutes (sp_stable.py)


def kernel_lines(server_log: Path, limit: int = 40) -> list[str]:
    """What the server log says about quantization kernels and the KV cache dtype (which GEMM runs the FP8 and FP4
    layers: native CUTLASS / FlashInfer, or a fallback)."""
    import re
    pat = re.compile(r"(?i)(nvfp4|fp4|fp8|kernel|quantiz|kv_cache_dtype|marlin|emulation|deepgemm|cutlass|flashinfer)")
    out, seen = [], set()
    try:
        with open(server_log, errors="ignore") as f:
            for line in f:
                if not pat.search(line) or "non-default args" in line:
                    continue
                key = re.sub(r"^.*?(INFO|WARNING|ERROR)\s+[\d\-: ]*", "", line.strip())[:300]
                if key not in seen:
                    seen.add(key)
                    out.append(key)
                    if len(out) >= limit:
                        break
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------------------------------------------------
# trials: what the harness wrote, read without touching Harbor
def trial_dirs(job: Path) -> list[Path]:
    return sorted(p for p in job.iterdir() if p.is_dir() and "__" in p.name) if job.exists() else []


def trial_state(d: Path) -> dict:
    s15 = d / "agent" / "s15"
    st = {"trial": d.name}
    try:
        t0 = json.loads((s15 / "wb_t0.json").read_text())
        st.update(t0=t0.get("t0"), t0_mono=t0.get("t0_mono"), pid=t0.get("pid"))
    except (OSError, ValueError):
        return st
    res = s15 / "result.json"
    if res.exists():
        try:
            r = json.loads(res.read_text())
            st.update(ended=True, status=r.get("status"), stop_reason=r.get("stop_reason"),
                      lead=(r.get("counter") or {}).get("lead"))
        except (OSError, ValueError):
            st["ended"] = True
    elif st.get("pid") and not Path(f"/proc/{st['pid']}").exists():
        st.update(ended=True, status="gone")
    elif (d / "result.json").exists():
        st.update(ended=True, status="trial_done")
    try:
        with open(s15 / "events.jsonl") as f:
            st["snipped"] = any('"snip_compact"' in line for line in f)
    except OSError:
        st["snipped"] = False
    try:
        with open(s15 / "calls.jsonl", "rb") as f:
            st["calls"] = sum(1 for _ in f)
    except OSError:
        st["calls"] = 0
    return st


def first_sessions(states: list[dict], n: int) -> list[dict]:
    started = sorted((s for s in states if s.get("t0_mono")), key=lambda s: s["t0_mono"])
    return started[:n]


def window_done(states: list[dict], n: int) -> bool:
    first = first_sessions(states, n)
    return len(first) == n and all(s.get("ended") for s in first)


# ---------------------------------------------------------------------------------------------------------------------
def gpu_apps() -> str:
    return subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
                          capture_output=True, text=True).stdout.strip()


def task_ids() -> list[str]:
    return [t["id"] for t in json.loads(C.TASKS_JSON.read_text())["main"]]


def our_containers() -> list[str]:
    """Task containers of topic-24 jobs: compose projects <trial>__env whose trial is one of our tasks."""
    ids = [i.lower() for i in task_ids()]
    p = subprocess.run(["docker", "ps", "-a", "--format", '{{.ID}} {{.Label "com.docker.compose.project"}}'],
                       capture_output=True, text=True)
    out = []
    for line in p.stdout.splitlines():
        cid, _, proj = line.partition(" ")
        if proj.endswith("__env") and any(proj.startswith(i + "__") for i in ids):
            out.append(cid)
    return out


def remove_containers() -> int:
    ids = our_containers()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True)
    nets = subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"], capture_output=True,
                          text=True).stdout.split()
    ours = [n for n in nets if n.endswith("__env_default") and any(n.startswith(i.lower() + "__") for i in task_ids())]
    if ours:
        subprocess.run(["docker", "network", "rm", *ours], capture_output=True)
    return len(ids)


def hw_heartbeat() -> dict | None:
    try:
        hb = json.loads((C.HW / "heartbeat.json").read_text())
    except (OSError, ValueError):
        return None
    hb["age_s"] = (time.monotonic_ns() - hb.get("t", 0)) / 1e9
    return hb


# ---------------------------------------------------------------------------------------------------------------------
class Probe:
    """exp02's latency probe (umbench pointer chase on core 8), back-to-back 20 s windows with a 10 ms series."""

    def __init__(self, rs, tag: str, raw: Path):
        self.rs, self.tag = rs, tag
        self.umb = rs.Umbench(name=f"umbench_probe_{tag}")
        self.ts = raw / "probe_ts.csv"
        self.stop_evt = threading.Event()
        self.paused = threading.Event()
        self.lock = threading.Lock()
        self.n = 0
        self.errors = 0
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _loop(self):
        while not self.stop_evt.is_set():
            if self.paused.is_set():
                time.sleep(0.2)
                continue
            with self.lock:
                try:
                    r = self.umb.run(tag=f"probe_{self.tag}_{self.n:05d}", cpu_op="none", probe=1, probe_cpu=C.PROBE_CPU,
                                     probe_mode="hash", probe_hist=1, probe_mib=1024, window=20, warmup=0.0, gap=0,
                                     sample_ms=10, ts=str(self.ts))
                    self.rs.record(r, kind="probe", cell=self.tag, i=self.n)
                    self.n += 1
                except Exception as e:                          # noqa: BLE001
                    self.errors += 1
                    C.log(f"probe error: {e!r}")
                    if self.errors > 20:
                        return
                    time.sleep(5)

    def alive(self) -> bool:
        return self.th.is_alive()

    def pause(self):
        """Stop probing: waits for the current window and holds the lock until resume(), so no window overlaps."""
        self.paused.set()
        self.lock.acquire()

    def resume(self):
        self.paused.clear()
        self.lock.release()

    def close(self):
        self.stop_evt.set()
        self.paused.clear()
        self.th.join(timeout=60)
        self.umb.close()


def meters(rs, tag: str, kind: str, secs: float, probe: Probe | None) -> list[dict]:
    """exp02's 4- and 12-core stream readers (run_study.py:420-424), in their own umbench process."""
    if probe:
        probe.pause()
    out = []
    umb = rs.Umbench(name=f"umbench_meter_{tag}_{kind}")
    try:
        for name, cores in (("m4", rs.M4), ("m12", rs.M12)):
            r = umb.run(tag=f"{kind}_{name}_{tag}", cpu_op="read", cpu_n=len(cores.split(",")), cpu_order=cores,
                        cpu_buf_gib=4, cpu_rate=0, window=secs, warmup=0.3, gap=0)
            rs.record(r, kind=f"meter_{kind}", meter=name, cell=tag)
            out.append({"meter": name, "cpu_gbps": r.get("cpu_gbps"), "t0": r.get("t0_ns"), "t1": r.get("t1_ns")})
            C.log(f"  {kind} {name}: {r.get('cpu_gbps')} GB/s")
    finally:
        umb.close()
        if probe:
            probe.resume()
    return out


# ---------------------------------------------------------------------------------------------------------------------
class Cell:
    def __init__(self, a):
        self.a = a
        self.name = a.name
        self.run_dir = C.RUNS / a.name
        self.raw, self.logs = self.run_dir / "raw", self.run_dir / "logs"
        self.reqlog = self.run_dir / "reqlog"
        self.job = C.JOBS / a.name
        self.cell_json = self.run_dir / "cell.json"
        self.log_path = self.run_dir / "cell.log"
        self.info: dict = {}
        self.procs: dict[str, subprocess.Popen] = {}

    def log(self, msg: str):
        C.log(msg, self.log_path)

    def mark(self, key: str, **extra):
        self.info.setdefault("marks", {})[key] = {**C.clocks(), **extra}
        self.save()

    def save(self):
        tmp = self.cell_json.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.info, indent=1, default=str))
        os.replace(tmp, self.cell_json)

    # -- harbor -------------------------------------------------------------------------------------------------------
    def harbor_cmd(self) -> list[str]:
        a = self.a
        env_file = self.run_dir / "s15.env"
        env_file.write_text("# topic 24: pacing off, no extra S15_* settings\n")
        slack = max(2, a.n // 4)
        cmd = [str(HARBOR), "run", "-p", str(C.QUEUE / "main"), "-e", "docker", "-a", "sp_agent:SPAgent",
               "-m", C.MODEL, "-o", str(C.JOBS), "--job-name", self.name, "-n", str(a.n + slack),
               "--n-concurrent-agents", str(a.n), "-k", str(a.passes), "-q", "-y", "--disable-verification",
               "--no-force-build", "--extra-docker-compose", str(C.DIR / "sp_compose.yaml")]
        ak = {"base_url": C.URL, "served_model": C.MODEL, "bench": "swe", "max_lead_calls": a.max_lead_calls,
              "max_tokens": 8000, "max_tokens_escalated": 8000, "max_tokens_reserve": 8000, "window_tokens": 131072,
              "call_timeout": 14400, "env_file": str(env_file)}
        for k, v in ak.items():
            cmd += ["--ak", f"{k}={v}"]
        return ["taskset", "-c", C.AGENT_CORES] + cmd

    def harbor_env(self) -> dict:
        home = C.DIR / "home"
        home.mkdir(exist_ok=True)
        pp = os.pathsep.join([str(C.DIR), str(C.REPO / "research" / "22_wait_bench"),
                              str(C.REPO / "research" / "16_epic_harness" / "harness_eval")])
        env = dict(os.environ, HARBOR_TELEMETRY="0", HOME=str(home), S15_VENDOR=str(C.DIR / "vendor" / "s15"),
                   WB_ADAPTER_DIR=str(ADAPTER), PYTHONPATH=pp, PYTHONUNBUFFERED="1")
        for k in ("VIRTUAL_ENV", "PYTHONHOME"):
            env.pop(k, None)
        return env

    # -- run ----------------------------------------------------------------------------------------------------------
    def run(self) -> int:
        a = self.a
        if self.cell_json.exists() and not a.force:
            prev = json.loads(self.cell_json.read_text())
            if prev.get("status") in ("done", "failed"):
                self.log(f"{self.name}: already {prev.get('status')}; use --force to rerun")
                return 0
        if self.run_dir.exists() and a.force:
            shutil.move(str(self.run_dir), str(self.run_dir) + time.strftime("_old%m%d_%H%M%S"))
            if self.job.exists():
                shutil.move(str(self.job), str(self.job) + time.strftime("_old%m%d_%H%M%S"))
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.info = {"name": self.name, "n": a.n, "status": "starting", "max_lead_calls": a.max_lead_calls,
                     "passes": a.passes, "hard_limit_h": a.hard_limit_h, "pool": C.POOL,
                     "variant": C.VARIANT, "repo": C.MODEL, "stop_rule": a.stop_rule,
                     "snapshot": str(C.snapshot_dir() or ""),
                     "server_flags": C.BASE + ["--kv-cache-memory-bytes", C.POOL, "--enable-prefix-caching"] + C.EXTRA,
                     "cores": {"vllm": C.VLLM_CORES, "probe": C.PROBE_CPU, "aux": C.AUX_CORES,
                               "agents": C.AGENT_CORES},
                     "repo_head": subprocess.run(["git", "-C", str(C.REPO), "rev-parse", "HEAD"], capture_output=True,
                                                 text=True).stdout.strip(),
                     "exp02_commit": C.exp02_commit(), "tasks": task_ids(), "pid": os.getpid()}
        try:
            snap = json.loads((C.DIR / "vendor" / "s15" / "SNAPSHOT.json").read_text())
            self.info["vendor"] = {"head": snap.get("head"), "dirty": snap.get("dirty")}
        except (OSError, ValueError):
            self.info["vendor"] = None
        self.mark("start")
        rs = C.exp02(self.raw, self.logs, self.log_path)
        srv = probe = None
        stop_reason = "error"
        try:
            # 1. preflight
            apps = gpu_apps()
            mem = C.meminfo()
            free = shutil.disk_usage(C.DATA).free
            left = remove_containers()
            # the shared network of sp_compose.yaml (one network per trial would exhaust Docker's address pools)
            if subprocess.run(["docker", "network", "inspect", "sp24net"], capture_output=True).returncode:
                subprocess.run(["docker", "network", "create", "--subnet", "10.224.0.0/16", "--label", "sp24=net",
                                "sp24net"], capture_output=True, check=True)
            hb = hw_heartbeat()
            self.info["preflight"] = {"gpu_apps": apps, "mem_available_gib": round(mem["MemAvailable"] / 2**30, 1),
                                      "disk_free_gb": round(free / 1e9), "containers_removed": left,
                                      "hw": hb, "loadavg": os.getloadavg()[0]}
            self.info["hw_ok"] = bool(hb and hb.get("age_s", 1e9) < HB_FRESH_S and hb.get("state") == "run")
            self.save()
            if apps:
                raise RuntimeError(f"GPU compute apps running: {apps}")
            if free < 50e9:
                raise RuntimeError(f"less than 50 GB free on disk ({free / 1e9:.0f} GB)")
            if not self.info["hw_ok"]:
                self.log("WARNING: the root counter sampler (sp_hw.py) is not running: no G/C counters for this cell")
            # 2. samplers, probe, server
            measure = self.run_dir / "measure_pids.json"
            harbor_pid = self.run_dir / "harbor.pid"
            smp = rs.Sampler(self.name)
            self.procs["sampler"] = smp.p
            self.procs["extra"] = subprocess.Popen(
                ["taskset", "-c", C.AUX_CORES, str(C.VLLM_PY), str(C.DIR / "sp_extra.py"), "--out",
                 str(self.run_dir / "extra.jsonl"), "--harbor-pid-file", str(harbor_pid), "--measure-pids-file",
                 str(measure)], stdout=subprocess.DEVNULL, stderr=open(self.logs / "extra_stderr.log", "a"))
            probe = Probe(rs, self.name, self.raw)
            measure.write_text(json.dumps([os.getpid(), smp.p.pid, self.procs["extra"].pid, probe.umb.p.pid]))
            self.mark("samplers")
            rs.evict_model_pages()
            srv = rs.Server(self.name, extra=C.EXTRA, env_extra=C.server_env(self.reqlog), pool=C.POOL,
                            prefix_caching=True)
            self.info["server_launch"] = {"t": srv.t_launch}
            srv.wait_ready(2400)
            self.mark("server_ready", ready_s=round((srv.t_ready - srv.t_launch) / 1e9, 1))
            rs.dump_metric_names(self.name)
            self.info["server_log"] = rs.parse_server_log(self.name)
            self.info["kernel_lines"] = kernel_lines(self.logs / f"server_{self.name}.log")
            rs.evict_model_pages()
            rs.smoke(self.name)
            mem = C.meminfo()
            self.info["mem_after_ready_gib"] = round(mem["MemAvailable"] / 2**30, 2)
            self.mark("throwaway_done")
            # 3. idle baseline
            self.mark("baseline_start")
            time.sleep(60)
            self.info["baseline_meters"] = meters(rs, self.name, "baseline", 15, probe)
            self.mark("baseline_end")
            # 4. users
            cmd = self.harbor_cmd()
            self.info["harbor_cmd"] = cmd
            (self.logs / "harbor.txt").touch()
            hb_proc = subprocess.Popen(cmd, cwd=str(C.DIR), env=self.harbor_env(), stdout=open(self.logs / "harbor.txt", "a"),
                                       stderr=subprocess.STDOUT, start_new_session=True)
            self.procs["harbor"] = hb_proc
            harbor_pid.write_text(str(hb_proc.pid))
            self.mark("users_start", harbor_pid=hb_proc.pid)
            # 5. window
            stop_reason = self.watch(srv, hb_proc, probe)
            self.mark("window_end", reason=stop_reason)
            # 6. coda
            mem = C.meminfo()
            if stop_reason in ("done", "stable") and mem["MemAvailable"] / 2**30 >= MEM_CODA_GIB:
                self.info["coda_meters"] = meters(rs, self.name, "coda", 20, probe)
            self.mark("coda_end")
        except Exception as e:                                  # noqa: BLE001
            self.info["error"] = f"{type(e).__name__}: {e}"
            self.log(f"{self.name}: ERROR {e!r}")
            stop_reason = "error"
        finally:
            self.teardown(srv, probe)
            # the hard limit is a planned way to end a cell (PLAN.md, stop rule): its window is as usable as any
            self.info["status"] = "done" if stop_reason in ("done", "hard_limit", "stable") else "failed"
            self.info["stop_reason"] = stop_reason
            self.mark("end")
            self.log(f"{self.name}: {self.info['status']} ({stop_reason})")
        return 0 if self.info["status"] == "done" else 1

    def stable_now(self) -> dict | None:
        """The stop rule of part 2 (PLAN2.md) on the live cell: sp_stable.py in the analysis venv, on the aux cores."""
        try:
            p = subprocess.run(["taskset", "-c", C.AUX_CORES, str(C.EXP01_VENV_PY), str(C.DIR / "sp_stable.py"),
                                "--cell", self.name, "--json"], capture_output=True, text=True, timeout=600,
                               env=dict(os.environ, HF_HOME=C.HF_HOME))
            return json.loads(p.stdout.strip().splitlines()[-1])
        except Exception as e:                                  # noqa: BLE001
            self.log(f"{self.name}: stable check failed: {e!r}")
            return None

    def watch(self, srv, harbor: subprocess.Popen, probe: Probe) -> str:
        a = self.a
        t_start = time.monotonic()
        last_steps, last_step_change = -1, time.monotonic()
        last_log = 0.0
        next_stable = t_start + STABLE_FROM_S
        while True:
            time.sleep(10)
            states = [trial_state(d) for d in trial_dirs(self.job)]
            first = first_sessions(states, a.n)
            # part 1's rule: the window ends when all N first sessions have ended. Under the stable rule the window
            # goes on with refill sessions until the steady-period numbers stop moving (or the hard limit).
            if a.stop_rule == "first_sessions" and window_done(states, a.n):
                self.info["first_sessions"] = first
                return "done"
            if srv.p.poll() is not None:
                return "server_died"
            if (self.run_dir / "stop").exists():                 # a manual stop, treated like a stable stop
                self.info["first_sessions"] = first
                self.info["stop_note"] = (self.run_dir / "stop").read_text()[:500]
                return "stable"
            if a.stop_rule == "stable" and time.monotonic() >= next_stable:
                next_stable = time.monotonic() + STABLE_EVERY_S
                r = self.stable_now()
                if r:
                    self.info.setdefault("stable_checks", []).append(r)
                    self.log(f"{self.name}: stable check: steady {r.get('steady_s', 0) / 60:.0f} min, drift "
                             f"{ {k: round(v, 3) for k, v in (r.get('drift') or {}).items()} }, "
                             f"stable {r.get('stable')}")
                    self.save()
                    if r.get("stable"):
                        self.info["first_sessions"] = first
                        return "stable"
            # trials that ended before their agent started (container or network could not be created): each one
            # is a session lost from the refill queue, and many mean the cell is not running N users
            broken = [d.name for d in trial_dirs(self.job)
                      if (d / "result.json").exists() and not (d / "agent" / "s15" / "wb_t0.json").exists()]
            if len(broken) > max(2, a.n // 8) or (len(first) < a.n and time.monotonic() - t_start > 900):
                self.info["broken_trials"] = broken[:40]
                self.info["first_sessions"] = first
                self.log(f"{self.name}: {len(broken)} trials ended before their agent started, "
                         f"{len(first)}/{a.n} first sessions started")
                return "trials_failed"
            if harbor.poll() is not None:
                self.info["first_sessions"] = first
                return "harbor_exited"
            mem = C.meminfo()["MemAvailable"] / 2**30
            if mem < MEM_STOP_GIB:
                return "memory"
            steps = sum(p.stat().st_size for p in self.reqlog.glob("steps_*.jsonl"))
            if steps != last_steps:
                last_steps, last_step_change = steps, time.monotonic()
            elif any(not s.get("ended") for s in states if s.get("t0_mono")) and \
                    time.monotonic() - last_step_change > STALL_S:
                return "stalled"
            elapsed_h = (time.monotonic() - t_start) / 3600
            if elapsed_h > a.hard_limit_h:
                self.info["first_sessions"] = first
                return "hard_limit"
            if time.monotonic() - last_log > 300:
                last_log = time.monotonic()
                active = [s for s in states if s.get("t0_mono") and not s.get("ended")]
                self.log(f"{self.name}: {elapsed_h:.2f} h, trials {len(states)}, active {len(active)}, first "
                         f"{len(first)}/{a.n} started, {sum(1 for s in first if s.get('ended'))} ended, "
                         f"{sum(1 for s in first if s.get('snipped'))} snipped, calls of first "
                         f"{sum(s.get('calls', 0) for s in first)}, MemAvailable {mem:.1f} GiB, "
                         f"probe {'ok' if probe.alive() else 'DEAD'}, hw {'ok' if (hw_heartbeat() or {}).get('age_s', 1e9) < HB_FRESH_S else 'stale'}")
                self.info["progress"] = {"elapsed_h": round(elapsed_h, 3), "first": first}
                self.save()

    def teardown(self, srv, probe):
        h = self.procs.get("harbor")
        if h and h.poll() is None:
            self.log(f"{self.name}: stopping Harbor")
            try:
                h.send_signal(signal.SIGTERM)          # Harbor turns SIGTERM into KeyboardInterrupt and cancels trials
                h.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(h.pid, signal.SIGKILL)
                h.wait()
        # leftover s15 children (they run in their own sessions)
        killed = 0
        for d in trial_dirs(self.job):
            st = trial_state(d)
            pid = st.get("pid")
            if pid and not st.get("ended") and Path(f"/proc/{pid}").exists():
                try:
                    os.killpg(pid, signal.SIGKILL)
                    killed += 1
                except OSError:
                    pass
        self.info["children_killed"] = killed
        try:
            self.info["containers_removed_after"] = remove_containers()
        except Exception as e:                                  # noqa: BLE001
            self.info["containers_removed_after"] = f"error {e!r}"
        if srv is not None:
            srv.stop()
        if probe is not None:
            probe.close()
        for k in ("extra", "sampler"):
            p = self.procs.get(k)
            if p and p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.mark("teardown_done")
        self.flush_hw()

    def flush_hw(self) -> None:
        """Close the counter sampler's current files (pause, then run) so this cell can be analysed at once: sp_hw.py
        records a file's start and end only when it closes it."""
        if not self.info.get("hw_ok"):
            return
        try:
            for word, ok in (("pause", lambda h: h.get("state") == "pause"),
                             ("run", lambda h: h.get("state") == "run" and h.get("gpu"))):
                (C.HW / "control").write_text(word + "\n")
                t_end = time.monotonic() + 240
                while time.monotonic() < t_end:
                    hb = hw_heartbeat()
                    if hb and hb.get("age_s", 1e9) < 15 and ok(hb):
                        break
                    time.sleep(2)
            self.mark("hw_flushed")
        except OSError as e:
            self.log(f"could not flush the counter sampler: {e!r}")
        finally:
            try:
                (C.HW / "control").write_text("run\n")
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--max-lead-calls", type=int, default=40)
    p.add_argument("--passes", type=int, default=8, help="Harbor -k: passes over the 16 tasks")
    p.add_argument("--hard-limit-h", type=float, required=True)
    p.add_argument("--variant", choices=sorted(C.VARIANTS), default="bf16", help="which checkpoint to serve")
    p.add_argument("--pool", default=None, help="KV pool, e.g. 58G (default: the variant's, data/variants.json)")
    p.add_argument("--stop-rule", choices=("stable", "first_sessions"), default="stable",
                   help="stable: PLAN2.md's rule (sp_stable.py); first_sessions: part 1's rule")
    p.add_argument("--force", action="store_true", help="rerun a finished cell (the old folders are kept, renamed)")
    p = sub.add_parser("status")
    p.add_argument("--name", required=True)
    p.add_argument("--n", type=int, required=True)
    a = ap.parse_args()
    if a.cmd == "status":
        states = [trial_state(d) for d in trial_dirs(C.JOBS / a.name)]
        for s in first_sessions(states, a.n):
            print(s)
        print("window done:", window_done(states, a.n))
        return 0
    C.use_variant(a.variant, a.pool)
    return Cell(a).run()


if __name__ == "__main__":
    sys.exit(main())
