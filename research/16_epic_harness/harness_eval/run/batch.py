#!/usr/bin/env python3
"""Run s15 on one benchmark against one already-running model server, in disk-sized chunks.

    batch.py --model qwen3-32b --url http://127.0.0.1:PORT --bench swe --phase p1 --tasks first:50 --k 1
    batch.py --model A --url URL_A --with B=URL_B ...     (both models run each chunk at once, one image pull)

Each chunk is one Harbor job (`jobs/<model>/<bench>/<phase>__cNN`), run inside the s15-eval image; a finished
job is skipped on rerun. Before each chunk the image set is snapshotted and afterwards only our new images are
removed (run/images.py). Task lists come from run/subsets.json: `qual` (first 12), `profile` (all), `first:N`,
`range:A:B`, or explicit ids.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "run"))
import images  # noqa: E402

DATASET = {"swe": "datasets/swe-bench-verified-prebuilt", "swepro": "vendor/SWE-bench_Pro-os/v2/tasks",
           "tbench": "datasets/terminal-bench-2-1", "gaia": "datasets/gaia"}
CHUNK = {"swe": 3, "swepro": 2, "tbench": 4, "gaia": 8}            # tasks per job (disk: images per task)
MIN_FREE_GB = {"swe": 10, "swepro": 12, "tbench": 10, "gaia": 6}
KIND = {"swe": "heavy", "swepro": "heavy", "tbench": "light", "gaia": "tiny"}
SLOTS = ROOT / "run" / "state" / "slots.json"          # {"heavy": n, "light": m}: chunks in flight, all stages
RESERVE_GB = {"swe": 6, "swepro": 10, "tbench": 4, "gaia": 1}    # images a chunk pulls right after it starts
RES_DIR = ROOT / "run" / "state" / "reservations"
RES_TTL = 600                                          # s: by then a chunk's pulls are on disk


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def release(slot: Path) -> None:
    try:
        if int((slot / "pid").read_text() or 0) == os.getpid():
            res = (slot / "reservation").read_text().strip() if (slot / "reservation").is_file() else ""
            if res:
                Path(res).unlink(missing_ok=True)
            shutil.rmtree(slot, ignore_errors=True)
    except (OSError, ValueError):
        pass


def reserved_gb() -> float:
    """Disk that recently started chunks may still be pulling: their images are not on disk yet, so free space
    does not show them. A reservation ends when its chunk does, after RES_TTL, or when its batch dies."""
    total = 0.0
    for f in RES_DIR.glob("*.json"):
        try:
            r = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if time.time() - r["t"] < RES_TTL and _alive(r["pid"]):
            total += r["gb"]
        else:
            f.unlink(missing_ok=True)
    return total


def acquire(kind: str, logf, need_gb: float = 0.0, reserve_gb: float = 0.0) -> Path:
    """A machine-wide slot for one chunk: Docker's root disk is shared and small, so the number of chunks
    pulling large task images at the same time is capped across every running stage. The free-space check
    subtracts other chunks' pending pulls, and check and reservation happen under one lock, so two batches
    starting together cannot both count the same free space."""
    RES_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        limits = json.loads(SLOTS.read_text()) if SLOTS.is_file() else {"heavy": 1, "light": 3}
        got, wait_msg = None, f"waiting for a {kind} slot"
        with open(RES_DIR / ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            free = images.free_gb() - reserved_gb()
            if need_gb and free < need_gb:
                wait_msg = f"waiting: {free:.1f} GB free on Docker's disk after pending pulls, need {need_gb}"
            else:
                for i in range(int(limits.get(kind, 1))):
                    d = ROOT / "run" / "state" / f"slot_{kind}_{i}"
                    try:
                        d.mkdir(parents=True)
                        (d / "pid").write_text(str(os.getpid()))
                        got = d
                        break
                    except FileExistsError:
                        try:
                            pid = int((d / "pid").read_text() or 0)
                        except (OSError, ValueError):
                            pid = 0
                        if pid and not _alive(pid):
                            shutil.rmtree(d, ignore_errors=True)
                if got and reserve_gb:
                    res = RES_DIR / f"{os.getpid()}-{time.time_ns()}.json"
                    res.write_text(json.dumps({"pid": os.getpid(), "t": time.time(), "gb": reserve_gb}))
                    (got / "reservation").write_text(str(res))
        if got:
            return got
        log(wait_msg, logf)
        time.sleep(120 if wait_msg.startswith("waiting:") else 60)


def task_list(bench: str, spec: str) -> list[str]:
    if spec.startswith("list:"):
        # a task list file (e.g. run/state/e3_tasks.json from run/sample_e3.py): its "tasks", in run order
        path = Path(spec[5:])
        return list(json.loads((path if path.is_absolute() else ROOT / path).read_text())["tasks"])
    sub = json.loads((ROOT / "run" / "subsets.json").read_text())[bench]["tasks"]
    if spec == "qual":
        return sub[:12]
    if spec == "profile":
        return sub
    if spec.startswith("first:"):
        return sub[: int(spec.split(":")[1])]
    if spec.startswith("range:"):
        _, a, b = spec.split(":")
        return sub[int(a):int(b)]
    return spec.split(",")


def job_done(path: Path) -> bool:
    res = path / "result.json"
    if not res.is_file():
        return False
    try:
        data = json.loads(res.read_text())
    except json.JSONDecodeError:
        return False
    return bool(data.get("finished_at"))


def log(line: str, f) -> None:
    msg = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}"
    print(msg, flush=True)
    f.write(msg + "\n")
    f.flush()


def epic_end_sessions(name: str, models: list[str], outs: dict, urls: dict, logf) -> None:
    """EPIC round: end the server session of every trial of this chunk (a trial killed before its child exited
    never sent /epic/end itself, and its rows would stay in the pool), then check none of them is left."""
    import urllib.parse
    import urllib.request
    by_url: dict[str, list[str]] = {}
    for m in models:
        if "+" not in m:
            continue
        job = outs[m] / name
        specs = [d.name for d in job.iterdir() if d.is_dir() and "__" in d.name] if job.is_dir() else []
        by_url.setdefault(urls[m].rstrip("/"), []).extend(specs)
    for url, specs in by_url.items():
        try:
            for spec in specs:
                q = urllib.parse.quote(spec, safe="")
                urllib.request.urlopen(f"{url}/epic/end?spec={q}", timeout=30).read()
            st = json.loads(urllib.request.urlopen(f"{url}/epic/stats", timeout=30).read())
            left = sorted(set(specs) & set((st.get("session_calls") or {}).keys()))
            log(f"epic sessions {name}: ended {len(specs)}, left {left}, pool {st.get('pool_used')}/"
                f"{st.get('pool_rows')}, sessions {st.get('sessions')}", logf)
        except Exception as exc:                                   # noqa: BLE001
            log(f"epic sessions {name}: {exc!r}", logf)


def take_lock(lock: Path, name: str, part: list[str], logf) -> None:
    """Per-job lock (pid + tasks): a chunk is never run twice at once; a lock left by a dead pid is taken over."""
    while True:
        try:
            lock.mkdir()
            (lock / "pid").write_text(str(os.getpid()))
            (lock / "tasks").write_text("\n".join(part))
            return
        except FileExistsError:
            try:
                owner = int((lock / "pid").read_text() or 0)
            except (OSError, ValueError):
                owner = 0
            if owner and _alive(owner):
                # another batch on the same server runs this chunk: wait for it (a stage stops its server
                # when its own batches return, so returning early could cut the other batch's trials off)
                log(f"waiting for {name} (run by pid {owner})", logf)
                time.sleep(60)
                continue
            shutil.rmtree(lock, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--with", dest="co", action="append", default=[], metavar="MODEL=URL",
                    help="also run every chunk for this model on its own server, at the same time: the task images "
                         "are pulled once and the chunk holds one disk slot (jobs/<model>/<bench>/<same job name>)")
    ap.add_argument("--bench", required=True, choices=sorted(DATASET))
    ap.add_argument("--phase", required=True)
    ap.add_argument("--tasks", default="qual")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=0, help="Harbor -n (default: tasks per chunk x k, max 6)")
    ap.add_argument("--chunk", type=int, default=0)
    ap.add_argument("--stagger", type=float, default=20.0,
                    help="seconds between starting co-run jobs (the first pulls the shared images)")
    ap.add_argument("--rotate-offset", type=int, default=0,
                    help="added to the chunk index for --rotate: a one-chunk batch per block (epic_round --per-block) "
                         "passes the block's index, so the arms' start order still rotates from block to block")
    ap.add_argument("--rotate", action="store_true",
                    help="rotate the co-run targets' start order from chunk to chunk (EPIC arms: no arm always first)")
    ap.add_argument("--seed-key", default="",
                    help="EPIC block key of the common-random-number seeds (default: the phase); a rerun of a block "
                         "under another phase name passes the original one")
    ap.add_argument("--agent-timeout-multiplier", type=float, default=0)
    ap.add_argument("--extra-ak", action="append", default=[])
    ap.add_argument("--reverse", action="store_true",
                    help="take the chunks last to first: a second batch with the same arguments then works from the "
                         "other end, and the per-job locks keep the two from running a chunk twice")
    ap.add_argument("--max-lead-calls", type=int, default=0,
                    help="default: 150 for the pilot/smoke phases (p1*, p2; comparable with X2), 250 otherwise")
    a = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "serve" / "models.yaml").read_text())
    targets = [(a.model, a.url)] + [tuple(item.split("=", 1)) for item in a.co]
    tasks = task_list(a.bench, a.tasks)
    chunk = a.chunk or CHUNK[a.bench]
    logf = open(ROOT / "runs" / f"batch_{a.model}_{a.bench}_{a.phase}.log", "a")
    harbor_out = ROOT / "runs" / "harbor_out"
    harbor_out.mkdir(exist_ok=True)
    cap = a.max_lead_calls or (150 if a.phase.startswith(("p1", "p2")) else 250)
    outs, aks = {}, {}
    for model, url in targets:
        outs[model] = ROOT / "jobs" / model / a.bench
        outs[model].mkdir(parents=True, exist_ok=True)
        # EPIC round: "<server>+<arm>" is one arm of an EPIC server (serve/models.yaml key <server>): the trial
        # talks to <server> and tags its lead calls with the arm; the block key of its common-random-number
        # seeds is the phase (the agent appends the task). Each alias has its own jobs dir, so the collector
        # reports one row per arm.
        server, _, arm = model.partition("+")
        ak = {"base_url": url, "served_model": server, "bench": a.bench, "max_lead_calls": cap,
              **{k: v for k, v in (cfg[server].get("agent") or {}).items()}}
        if arm:
            ak.update(epic_arm=arm, epic_seed_key=a.seed_key or a.phase)
        if a.bench == "gaia":
            ak["env_file"] = str(ROOT / "secrets.env")
        for item in a.extra_ak:
            k, v = item.split("=", 1)
            ak[k] = v
        aks[model] = ak
    # disk-heavy tasks run as their own chunk: "task [n]" per line, n = trials at once (default 1); the n trials
    # of one task may write n times its scratch data, so n stays 1 for the tasks that fill their 10 GB limit
    heavy_file = ROOT / "run" / "state" / "disk_heavy_tasks.txt"
    disk_heavy: dict[str, int] = {}
    for line in (heavy_file.read_text().splitlines() if heavy_file.is_file() else []):
        if line.split():
            disk_heavy[line.split()[0]] = int(line.split()[1]) if len(line.split()) > 1 else 1
    co = "".join(f" with={m}@{u}" for m, u in targets[1:])
    log(f"start model={a.model} bench={a.bench} phase={a.phase} tasks={len(tasks)} k={a.k} chunk={chunk} "
        f"url={a.url}{co}", logf)
    stop_dir = ROOT / "run" / "state" / "stop"
    normal = [t for t in tasks if t not in disk_heavy]
    plan = [(f"{a.phase}__c{i // chunk:02d}", normal[i: i + chunk], False) for i in range(0, len(normal), chunk)]
    plan += [(f"{a.phase}__h_{t[:40]}", [t], True) for t in tasks if t in disk_heavy]
    if a.reverse:
        plan.reverse()
    for name, part, is_heavy in plan:
        if (stop_dir / "all").exists() or any((stop_dir / m).exists() for m, _ in targets) or \
                (stop_dir / a.bench).exists() or (stop_dir / os.environ.get("S15_STAGE", "-")).exists():
            log(f"stop file present; ending before {name}", logf)
            break
        todo = [m for m, _ in targets if not job_done(outs[m] / name)]
        if not todo:
            log(f"skip {name} (done)", logf)
            continue
        for m in todo:
            take_lock(outs[m] / f"{name}.lock", name, part, logf)
        for m in [m for m in todo if job_done(outs[m] / name)]:
            shutil.rmtree(outs[m] / f"{name}.lock", ignore_errors=True)
            todo.remove(m)
            log(f"skip {name}{f' for {m}' if a.co else ''} (done by another batch)", logf)
        if not todo:
            continue
        slot = acquire("heavy" if is_heavy else KIND[a.bench], logf, 24 if is_heavy else MIN_FREE_GB[a.bench],
                       12 if is_heavy else RESERVE_GB[a.bench])
        snap = f"{a.model}_{a.bench}_{name}"
        images.snapshot(snap)
        n = disk_heavy[part[0]] if is_heavy else (a.concurrency or min(len(part) * a.k, 6))
        t0 = time.time()
        who = f" [{', '.join(todo)}]" if a.co else ""
        log(f"run {name}: {' '.join(part)}{who}", logf)
        procs = {}
        if a.rotate and len(todo) > 1:
            shift = (plan.index((name, part, is_heavy)) + a.rotate_offset) % len(todo)
            todo = todo[shift:] + todo[:shift]
        for i, m in enumerate(todo):
            cmd = [str(ROOT / "run" / "eval.sh"), "harbor", "run", "-p", DATASET[a.bench], "-e", "docker",
                   "-a", "adapter.harbor_agent:S15Agent", "-m", m, "-o", str(outs[m].relative_to(ROOT)),
                   "--job-name", name, "-n", str(n), "-k", str(a.k), "-q", "-y"]
            for t in part:
                cmd += ["-i", t]
            for k, v in aks[m].items():
                cmd += ["--ak", f"{k}={v}"]
            if a.agent_timeout_multiplier:
                cmd += ["--agent-timeout-multiplier", str(a.agent_timeout_multiplier)]
            if a.bench == "swe":
                # Harbor's SWE-bench verifier waits 3000 s; the official harness stops tests at 1800 s
                cmd += ["--verifier-timeout-multiplier", "0.6"]
            if i:
                time.sleep(a.stagger)                   # the first job pulls the shared images
            # output to a file, not a pipe: a job's progress bar must never block on a full pipe
            out_file = harbor_out / f"{m}_{a.bench}_{name}.txt"
            procs[m] = (subprocess.Popen(cmd, stdout=open(out_file, "w"), stderr=subprocess.STDOUT), out_file)
        for m, (proc, out_file) in procs.items():
            rc = proc.wait()
            text = out_file.read_text(errors="replace") if out_file.is_file() else ""
            tail = "\n".join(text.strip().splitlines()[-6:])
            log(f"done {name} rc={rc} in {time.time() - t0:.0f}s{f' [{m}]' if a.co else ''} | {tail}", logf)
        removed = images.cleanup(snap, part)
        try:
            removed += images.sweep()
        except Exception as exc:                        # noqa: BLE001
            log(f"sweep failed: {exc}", logf)
        release(slot)
        for m in todo:
            shutil.rmtree(outs[m] / f"{name}.lock", ignore_errors=True)
        log(f"cleanup {name}: removed {len(removed)} images, {images.free_gb():.1f} GB free", logf)
        epic_end_sessions(name, todo, outs, dict(targets), logf)
    log("finished", logf)


if __name__ == "__main__":
    main()
