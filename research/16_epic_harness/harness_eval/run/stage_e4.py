#!/usr/bin/env python3
"""e4, divergence-triggered full recomputation (plan ~/.claude/plans/whenever-we-do-shift-epic-misty-muffin.md,
pre-registration runs/epic/prereg_e4.md): MiniMax-M2.7, 7 arms (prefix, prefix_aa, shift, epic128, epic128a1,
epic128a4, epic128rjsd) x the profile's first 30 tasks of SWE-bench Verified, SWE-bench Pro and Terminal-Bench x
3 replicates, on GPUs 0-3.

    S15_DOCKER_SOCK=/data/fritz/dind-run/docker.sock S15_DOCKER_ROOT=/data/fritz/docker S15_KEEP_IMAGES=1 \
      setsid nohup .venv/bin/python run/stage_e4.py [--pilot] >> runs/epic/stage_e4.log 2>&1 < /dev/null &

1. Main run only: checks the pre-registration (runs/epic/prereg_e4.md against runs/epic/prereg_e4.sha256, and the
   sha256 it lists for the task lists and the analysis scripts). Every run: the 7 arms must parse as EPIC arms (an
   unknown arm would be served untagged, as plain prefix caching).
2. Waits for GPUs 0-3 to be empty, starts minimax-m2.7-epic, checks its configuration (/epic/stats: pool rows,
   decode reserve, shadow rate, decision top-K), starts the health watcher, runs the live selftests S1-S6 and
   records the code version (runs/epic/code_<phase>.json). Stops if a check or a selftest fails.
3. One queue of blocks over the three benchmarks, replicate after replicate; within a replicate the longest
   blocks first (Terminal-Bench, then SWE-bench Pro, then SWE-bench Verified). A block is one task with its 7 arms
   (one batch.py co-run, run/epic_round.py's per-block naming "<phase>r<r>b<i>", the replicate's seed key, the
   launch order rotated by the block index). Admission: a block starts while the agent sessions in flight plus its
   7 stay within --max-sessions and the host pool is below --pool-guard. Agent timeout x2 (swe, swepro), x3
   (tbench). Children run with S15_STAGE=<phase>, so run/state/stop/<phase> ends the stage gracefully: the blocks
   in flight finish, no new one starts.
   - Gates (epic/pilot_gates.py, runs/epic/gates_minimax-m2.7-epic_<bench>_<phase>r<k>[_first8].json): per
     benchmark after its first 8 complete blocks of replicate 0, and per benchmark x replicate once all its blocks
     are complete. A stop if: a preemption; fallbacks + resets at 5 % of post-snip calls; a lead call without its
     one server record; ranks that disagree; E-0 below 97 % (swe, swepro) or 90 % (tbench, whose long reasoning
     hits the template's known limit: 94.15 % in e2); a curated call of the recompute arm without its decision
     record, or a decision record anywhere else; failed decisions at 1 % of decisions.
   - Budget: GPU-hours = server uptime x 4 (+ --base-gpu-h). The next replicate is queued only if the projected
     total stays within --cap (remaining session-hours, from this run's mean trial times per benchmark, times the
     GPU-hours per session-hour so far). At the cap itself the stop file ends the run.
   - Reruns: blocks in which an arm has no valid trial (infrastructure failure) are rerun under the same block key
     ("<phase>r<r>b<i>x<n>"), up to 2 rounds, after the replicates.
4. Stops the server at the end, also on SIGTERM. The stage never reads outcomes (rewards).
--pilot: phase e4p on the pilot task lists (profile indices 30-31 per benchmark), 1 replicate, no pre-registration.
Dry run (no GPU): --server fake --url http://127.0.0.1:PORT (run/fake_model.py, already running) --no-selftest
--no-gates: the scheduler, block names, seed keys, rotation and timeouts on the real task containers.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import types
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "serve"))
sys.path.insert(0, str(ROOT / "run"))
sys.path.insert(0, str(ROOT / "epic"))
import epic_plan as ep  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
VPY = "/data/fritz/venv/bin/python"
SERVER = "minimax-m2.7-epic"
GPUS = [0, 1, 2, 3]
ARMS = ["prefix", "prefix_aa", "shift", "epic128", "epic128a1", "epic128a4", "epic128rjsd"]
BENCHES = ("tbench", "swepro", "swe")                  # queue order within a replicate: the longest blocks first
TASKS = {b: f"run/state/e4_tasks_{b}.json" for b in ("swe", "swepro", "tbench")}
PILOT_TASKS = {b: f"run/state/e4p_tasks_{b}.json" for b in ("swe", "swepro", "tbench")}
TIMEOUT = {"swe": 2.0, "swepro": 2.0, "tbench": 3.0}
E0_STOP = {"swe": 0.97, "swepro": 0.97, "tbench": 0.90}
DEC_ERR_STOP = 0.01
INTERIM_BLOCKS = 8
PREREG = ROOT / "runs" / "epic" / "prereg_e4.md"
ANALYSIS = ["report/e4_report.py", "report/e4_verify.py", "report/latency_model.py", "report/collect.py",
            "report/epic_report.py"]
CONFIG = {"pool_rows": 1_280_000, "decode_reserve": 12288, "shadow_every": 32, "dec_k": 128}
CODE = ["adapter/harbor_agent.py", "adapter/s15_child.py", "run/batch.py", "run/epic_round.py", "run/stage_e4.py",
        "serve/models.yaml", "docker/CURRENT_TAG"] + ANALYSIS
TRIAL_MIN = {"swe": 9.0, "swepro": 11.7, "tbench": 26.5}   # e2/e3 mean trial minutes, until this run has its own


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_prereg() -> bool:
    """prereg_e4.md matches its recorded sha256, and every file it lists as "`path` ... sha256 `hex`" matches."""
    rec = PREREG.with_suffix(".sha256")
    if not PREREG.is_file() or not rec.is_file():
        log(f"pre-registration missing: {PREREG.relative_to(ROOT)} / {rec.name}")
        return False
    want = rec.read_text().split()[0]
    got = sha(PREREG)
    log(f"pre-registration {PREREG.relative_to(ROOT)} sha256 {got} (recorded {want})")
    ok = got == want
    text = PREREG.read_text()
    for rel in list(TASKS.values()) + ANALYSIS:
        m = re.search(r"`" + re.escape(rel) + r"`[^\n]*sha256 `([0-9a-f]{64})`", text)
        cur = sha(ROOT / rel) if (ROOT / rel).is_file() else "missing"
        log(f"{rel} sha256 {cur} (pre-registered {m.group(1) if m else 'missing'})")
        ok = ok and bool(m) and m.group(1) == cur
    return ok


def tasks_of(path: str) -> list[str]:
    return json.loads((ROOT / path).read_text())["tasks"]


def trials_of(bench: str, phase: str) -> list[Path]:
    """Result files of the trials of a replicate phase "<p>r<k>" on one benchmark: its blocks and their reruns."""
    out = []
    for arm in ARMS:
        for res in glob.glob(str(ROOT / "jobs" / f"{SERVER}+{arm}" / bench / f"{phase}b*__*" / "*__*" / "result.json")):
            if re.fullmatch(re.escape(phase) + r"b\d+(x\d+)?", Path(res).parts[-3].split("__")[0]):
                out.append(Path(res))
    return out


def complete_blocks(bench: str, phase: str) -> int:
    """Blocks of `phase` on `bench` in which every arm has a finished trial (a rerun counts for its block)."""
    done: dict[str, set[str]] = {}
    for res in trials_of(bench, phase):
        m = re.fullmatch(re.escape(phase) + r"b(\d+)(x\d+)?", res.parts[-3].split("__")[0])
        try:
            fin = json.loads(res.read_text()).get("finished_at")
        except (OSError, json.JSONDecodeError):
            continue
        if m and fin:
            done.setdefault(m.group(1), set()).add(res.parts[-5].split("+", 1)[1])
    return sum(1 for arms in done.values() if set(ARMS) <= arms)


def _ts(x) -> float | None:
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp() if x else None


def session_hours(phase_prefix: str, now: float) -> dict:
    """Agent session-hours of the run's trials so far: finished ones (agent phase) and the elapsed part of the ones
    in flight, per benchmark; plus the finished trials' mean minutes per benchmark."""
    out = {"done": 0.0, "inflight": 0.0, "mean_min": {}, "inflight_elapsed": {}}
    for bench in TASKS:
        durs, el = [], []
        for res in glob.glob(str(ROOT / "jobs" / f"{SERVER}+*" / bench / f"{phase_prefix}r*__*" / "*__*" / "result.json")):
            try:
                r = json.loads(Path(res).read_text())
            except (OSError, json.JSONDecodeError):
                continue
            ag = r.get("agent_execution") or {}
            t0 = _ts(ag.get("started_at") or r.get("started_at"))
            t1 = _ts(ag.get("finished_at") or r.get("finished_at"))
            if t0 and t1:
                durs.append((t1 - t0) / 60)
            elif t0:
                el.append((now - t0) / 60)
        out["done"] += sum(durs) / 60
        out["inflight"] += sum(el) / 60
        if durs:
            out["mean_min"][bench] = sum(durs) / len(durs)
        out["inflight_elapsed"][bench] = el
    return out


def project(used: float, phase_prefix: str, unlaunched: dict[str, int], now: float) -> float:
    """Projected total GPU-hours when the in-flight trials and `unlaunched` trials per benchmark have run."""
    sh = session_hours(phase_prefix, now)
    spent = sh["done"] + sh["inflight"]
    if spent <= 0:
        return float("inf")
    rate = used / spent                                  # GPU-hours per agent session-hour so far
    mean = {b: sh["mean_min"].get(b, TRIAL_MIN[b]) for b in TASKS}
    rest = sum(max(mean[b] - e, 0.2 * mean[b]) for b, els in sh["inflight_elapsed"].items() for e in els)
    rest += sum(n * mean[b] for b, n in unlaunched.items())
    return used + rate * rest / 60


def gates(bench: str, phase: str, url: str, tag: str = "") -> tuple[bool, dict]:
    """Runs epic/pilot_gates.py on (bench, phase); True unless a stop condition holds (see the module docstring)."""
    out = ROOT / "runs" / "epic" / f"gates_{SERVER}_{bench}_{phase}{tag}.json"
    p = subprocess.run([VPY, str(ROOT / "epic" / "pilot_gates.py"), "--server", SERVER, "--bench", bench,
                        "--phase", phase, "--url", url, "--out", str(out)], capture_output=True, text=True)
    if not out.is_file():
        log(f"gates {bench} {phase}{tag}: no report (rc={p.returncode}) {p.stderr[-800:]}")
        return False, {}
    rep = json.loads(out.read_text())
    ps, g = rep["pass"], rep["gates"]
    e0 = rep["e0"]["share_ge_99"]
    stop = [k for k in ("no_preemptions", "fallback_reset_lt_5pct", "one_record_per_call", "ranks_consistent",
                        "one_dec_per_edited_call") if not ps.get(k)]
    if e0 is None or e0 < E0_STOP[bench]:
        stop.append(f"E0 {e0} < {E0_STOP[bench]}")
    n_dec = sum((x.get("decisions") or {}).get("n", 0) for x in rep["arms"].values())
    if n_dec and g.get("decision_errors", 0) / n_dec >= DEC_ERR_STOP:
        stop.append(f"decision errors {g.get('decision_errors')}/{n_dec} >= {DEC_ERR_STOP:.0%}")
    dec = {arm: x.get("decisions") for arm, x in rep["arms"].items() if x.get("decisions")}
    log(f"gates {bench} {phase}{tag}: trials {rep['cost']['trials']}, E-0 {(e0 or 0):.2%} of {rep['e0']['calls']}, "
        f"preemptions {g['preemptions']}, fallbacks+resets {g['fallback_reset_share']:.2%} of {g['post_snip_calls']}, "
        f"records missing/dup {g['missing_server_records']}/{g['duplicate_server_records']}, ranks "
        f"{g['ranks_consistent']}, decisions {json.dumps(dec)}, server errors {g.get('server_errors')}; "
        f"pass {json.dumps(ps)}; stop conditions: {stop or 'none'}")
    return not stop, rep


def server_config_ok(url: str) -> bool:
    st = json.loads(urllib.request.urlopen(f"{url}/epic/stats", timeout=60).read())
    have = {"pool_rows": st.get("pool_rows"), **{k: (st.get("config") or {}).get(k) for k in
                                                 ("decode_reserve", "shadow_every", "dec_k")}}
    ok = all(have.get(k) == v for k, v in CONFIG.items())
    log(f"server config {json.dumps(have)} {'ok' if ok else f'!= {json.dumps(CONFIG)}'}")
    return ok


def main() -> None:
    global SERVER
    import epic_round as er
    import serve
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--server", default=SERVER, help="dry runs: fake (run/fake_model.py) with --url")
    ap.add_argument("--url", default="", help="adopt a server already running there (dry runs): no start, no stop")
    ap.add_argument("--no-gates", action="store_true", help="dry runs: the fake server has no EPIC log")
    ap.add_argument("--cap", type=float, default=300.0, help="GPU-hours of e4, the pilot included (--base-gpu-h)")
    ap.add_argument("--base-gpu-h", type=float, default=0.0, help="GPU-hours spent before this start (pilot, resume)")
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--first-replicate", type=int, default=0)
    ap.add_argument("--max-sessions", type=int, default=22)
    ap.add_argument("--pool-guard", type=float, default=0.6)
    ap.add_argument("--max-blocks", type=int, default=6, help="blocks at once, whatever the session count")
    ap.add_argument("--rerun-rounds", type=int, default=2)
    ap.add_argument("--no-selftest", action="store_true", help="dry runs against the fake model only")
    a = ap.parse_args()
    SERVER = a.server
    phase = "e4p" if a.pilot else "e4"
    reps = 1 if a.pilot else a.replicates
    task_files = PILOT_TASKS if a.pilot else TASKS
    stopf = ROOT / "run" / "state" / "stop" / phase
    bad = [x for x in ARMS if not ep.is_arm(x)]
    if bad:
        log(f"arms the server would not tag: {bad}; {phase} does not start")
        return
    if not a.pilot and not a.url and not check_prereg():
        log("pre-registration check failed: e4 does not start")
        return
    if stopf.exists() or (stopf.parent / "all").exists():
        log(f"stop file present ({stopf}): {phase} does not start")
        return
    tasks = {b: tasks_of(task_files[b]) for b in BENCHES}
    started = {"t": None}
    kids: list = []

    def gpu_h() -> float:
        return a.base_gpu_h + (len(GPUS) * (time.time() - started["t"]) / 3600 if started["t"] else 0.0)

    def stop(*_args):
        if _args:
            for p, _m in kids:
                if p.poll() is None:
                    p.terminate()
        if not a.url and (serve.STATE / f"{SERVER}.json").exists():
            log(f"stopping {SERVER}: {serve.stop(SERVER)}")
        if _args:
            log(f"terminated; about {gpu_h():.1f} GPU-h used by {phase}")
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    env = dict(os.environ, S15_STAGE=phase)
    halt = {"why": ""}
    while not a.url and not serve.gpus_empty(GPUS):
        time.sleep(60)
    try:
        if a.url:
            url = a.url
            started["t"] = time.time()
            log(f"adopting {SERVER} on {url} (dry run: no configuration check, no health watcher)")
        else:
            state = serve.start(SERVER, GPUS)
            started["t"] = time.time()
            url = state["url"]
            log(f"{SERVER} ready on {url} after {state.get('ready_s')}s")
            if not server_config_ok(url):
                log(f"server configuration differs: {phase} stops here")
                return
            subprocess.Popen(["python3", "-u", str(ROOT / "run" / "epic_health.py"), "--server", SERVER, "--since",
                              str(int(time.time()))], stdout=open(ROOT / "runs" / "epic" / f"health_{phase}.out", "a"),
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        tag = "" if not (ROOT / "runs" / "epic" / f"code_{phase}.json").exists() else f"_{int(started['t'])}"
        if not a.no_selftest:
            st = subprocess.run([VPY, str(ROOT / "epic" / "selftest_live.py"), "--url", url, "--format", "minimax",
                                 "--out", str(ROOT / "runs" / "epic" / f"selftest_{phase}{tag}.json")],
                                capture_output=True, text=True)
            print(st.stdout[-5000:], st.stderr[-1500:], flush=True)
            if st.returncode != 0:
                log(f"selftests failed: {phase} stops here")
                return
        files = CODE + list(task_files.values()) + \
            sorted(str(p.relative_to(ROOT)) for p in (ROOT / "epic").glob("*.py")) + \
            sorted(str(p.relative_to(ROOT)) for p in (ROOT / "epic" / "templates").glob("*"))
        (ROOT / "runs" / "epic" / f"code_{phase}{tag}{'_dry' if a.url else ''}.json").write_text(json.dumps(
            {"recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "server_started": started["t"],
             "prereg_sha256": sha(PREREG) if PREREG.is_file() else None,
             "files": {f: sha(ROOT / f) for f in files if (ROOT / f).is_file()}}, indent=1))

        def args_for(bench: str):
            return types.SimpleNamespace(server=SERVER, bench=bench, concurrency=3, chunk=1, stagger=2.0,
                                         agent_timeout_multiplier=TIMEOUT[bench])

        def block_cmds(k: int, rerun: int = 0) -> list[tuple[str, list[str]]]:
            """(bench, batch.py command) of every block of replicate k still missing an arm, longest first."""
            ph = f"{phase}r{k}"
            out = []
            for bench in BENCHES:
                order = tasks[bench]
                miss = er.missing_blocks(args_for(bench), ARMS, [ph], order)
                for (p_, t), arms_missing in sorted(miss.items(), key=lambda kv: order.index(kv[0][1])):
                    i = order.index(t)
                    name = f"{p_}b{i:02d}" + (f"x{rerun}" if rerun else "")
                    out.append((bench, er.batch_cmd(args_for(bench), url, arms_missing, name, [t], seed_key=p_,
                                                    rotate_offset=0 if rerun else i)))
            return out

        running: list = []
        gated: set = set()

        def halt_now(why: str) -> None:
            if not halt["why"]:
                halt["why"] = why
                stopf.parent.mkdir(parents=True, exist_ok=True)
                stopf.write_text(why + "\n")
                log(f"{why}: wrote {stopf}; the blocks in flight finish, then {phase} stops")

        def check_gates(final: bool = False) -> None:
            if a.no_gates:
                return
            for k in range(a.first_replicate, a.first_replicate + reps):
                for bench in BENCHES:
                    ph = f"{phase}r{k}"
                    n = complete_blocks(bench, ph)
                    if k == a.first_replicate and (bench, ph, "i") not in gated and n >= min(INTERIM_BLOCKS,
                                                                                              len(tasks[bench])):
                        gated.add((bench, ph, "i"))
                        ok, _ = gates(bench, ph, url, f"_first{INTERIM_BLOCKS}")
                        if not ok:
                            halt_now(f"interim gates failed ({bench} {ph})")
                    busy = any(m["bench"] == bench and er.block_of(m["phase"]) == ph for _p, m in running)
                    if (bench, ph, "f") not in gated and not busy and (n == len(tasks[bench]) or final) and n:
                        gated.add((bench, ph, "f"))
                        ok, _ = gates(bench, ph, url)
                        if not ok:
                            halt_now(f"gates failed ({bench} {ph})")

        def drive(queue: list, rep_next=None) -> None:
            """Runs `queue` under admission; `rep_next()` may append the next replicate when the queue empties."""
            while queue or running:
                while queue and len(running) < a.max_blocks and not halt["why"] and not stopf.exists():
                    bench, cmd = queue[0]
                    meta = er.cmd_meta(cmd)
                    meta["tasks"] = 1
                    if running and er.sessions_in_flight([m for _p, m in running]) + len(meta["arms"]) > a.max_sessions:
                        break
                    share = er.pool_share(url)
                    if running and share is not None and share >= a.pool_guard:
                        break
                    queue.pop(0)
                    log(f"block {bench} {meta['phase']} ({len(meta['arms'])} arms; "
                        f"{er.sessions_in_flight([m for _p, m in running])} sessions in flight; {gpu_h():.1f} GPU-h)")
                    p = subprocess.Popen(cmd, stdout=open(ROOT / "runs" / "epic" / f"round_{phase}_{bench}.log", "a"),
                                         stderr=subprocess.STDOUT, env=env)
                    running.append((p, meta))
                    kids[:] = running
                if (halt["why"] or stopf.exists()) and queue:
                    log(f"stop: {len(queue)} queued blocks dropped")
                    queue.clear()
                time.sleep(30)
                for p, m in [x for x in running if x[0].poll() is not None]:
                    log(f"block {m['bench']} {m['phase']} done rc={p.returncode}")
                    running.remove((p, m))
                kids[:] = running
                if gpu_h() >= a.cap:
                    halt_now(f"{gpu_h():.1f} GPU-h reached the cap {a.cap}")
                check_gates()
                if not queue and rep_next is not None and not halt["why"] and not stopf.exists():
                    queue.extend(rep_next())

        nxt = {"k": a.first_replicate}

        def next_replicate() -> list:
            k = nxt["k"]
            if k >= a.first_replicate + reps:
                return []
            cmds = block_cmds(k)
            if k > a.first_replicate:
                unl: dict[str, int] = {}
                for bench, cmd in cmds:
                    unl[bench] = unl.get(bench, 0) + len(er.cmd_meta(cmd)["arms"])
                total = project(gpu_h(), phase, unl, time.time())
                log(f"replicate {phase}r{k}: {gpu_h():.1f} GPU-h used; projected total with it {total:.1f} "
                    f"(cap {a.cap})")
                if total > a.cap:
                    log(f"budget rule: replicate {phase}r{k} would exceed the cap: not started")
                    nxt["k"] = a.first_replicate + reps
                    return []
            nxt["k"] = k + 1
            log(f"replicate {phase}r{k}: {len(cmds)} blocks queued ({gpu_h():.1f} GPU-h used)")
            return cmds

        drive(next_replicate(), next_replicate)
        for n in range(1, a.rerun_rounds + 1):
            if halt["why"] or stopf.exists():
                break
            q = [c for k in range(a.first_replicate, nxt["k"]) for c in block_cmds(k, rerun=n)]
            if not q:
                log("every block has a valid trial in every arm")
                break
            log(f"rerun round {n}: {len(q)} blocks")
            drive(q)
        check_gates(final=True)
        log(f"{phase}: blocks complete per benchmark and replicate: " + json.dumps(
            {f"{b} {phase}r{k}": complete_blocks(b, f"{phase}r{k}") for k in range(a.first_replicate, nxt["k"])
             for b in BENCHES}))
    finally:
        stop()
    log(f"stage {phase} done; about {gpu_h():.1f} GPU-h used by {phase}{' (halted: ' + halt['why'] + ')' if halt['why'] else ''}")


if __name__ == "__main__":
    main()
