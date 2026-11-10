#!/usr/bin/env python3
"""Topic 22: the mixed benchmark task list. 96 tasks = 32 SWE-bench Verified + 32 SWE-bench Pro + 32 Terminal-Bench
2.1, a seeded draw from harness_eval's full local sets, linked as one Harbor dataset (data/dataset/<task> -> the task
folder), and checked with each task's reference solution (Harbor's oracle agent), which also pulls its image into the
docker-in-docker daemon.

Excluded before the draw:
  - SWE-bench Pro protonmail tasks (19.6 GB images; harness_eval's subsets.json excluded them too);
  - Terminal-Bench tasks listed as disk-heavy (harness_eval run/state/disk_heavy_tasks.txt), the two whose oracle fails
    (qemu-startup, mcmc-sampling-stan), and those with an agent timeout over 1,800 s.
Each benchmark's eligible tasks are shuffled with seed 2211: the first 32 are the main list, the next 4 the smoke
tasks, the rest ordered reserves. The queue interleaves the three main lists, one task of each per round in a seeded
order. An oracle failure is replaced by its benchmark's next reserve (`replace`), at the same queue position.

    python research/22_wait_bench/wb_tasks.py draw       # data/tasks.json, data/dataset/
    python research/22_wait_bench/wb_tasks.py oracle     # Harbor oracle on every listed task not yet passed
    python research/22_wait_bench/wb_tasks.py replace    # swap failures for reserves (then run oracle again)
    python research/22_wait_bench/wb_tasks.py queue      # data/queue/{main,smoke}/<pos>__<task> for the runs
    python research/22_wait_bench/wb_tasks.py status

Harbor loads a dataset folder's tasks in sorted name order, so the runs use data/queue/main, whose links carry the queue
position (q001__<task>, ...): that makes Harbor's trial order the seeded queue.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import tomllib
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
HE = REPO / "research" / "16_epic_harness" / "harness_eval"
DATASETS = {"swe": HE / "datasets" / "swe-bench-verified-prebuilt", "swepro": HE / "vendor" / "SWE-bench_Pro-os" / "v2" / "tasks",
            "tbench": HE / "datasets" / "terminal-bench-2-1"}
BENCHES = tuple(DATASETS)
DATA = DIR / "data"
TASKS = DATA / "tasks.json"
DATASET = DATA / "dataset"
QUEUE = DATA / "queue"
ORACLE = DATA / "jobs" / "oracle"
SEED = 2211
N_MAIN, N_SMOKE = 32, 4
TB_MAX_AGENT_S = 1800.0
TB_ORACLE_FAILS = ("qemu-startup", "mcmc-sampling-stan")


def agent_timeout(task: Path) -> float:
    return float(tomllib.loads((task / "task.toml").read_text()).get("agent", {}).get("timeout_sec") or 0.0)


def eligible(bench: str) -> tuple[list[str], dict[str, list[str]]]:
    root = DATASETS[bench]
    names = sorted(p.name for p in root.iterdir() if (p / "task.toml").is_file())
    out, excluded = [], {}
    heavy = {line.split()[0] for line in (HE / "run" / "state" / "disk_heavy_tasks.txt").read_text().splitlines()
             if line.strip()}
    for name in names:
        why = None
        if bench == "swepro" and "protonmail" in name:
            why = "protonmail image (19.6 GB)"
        elif bench == "tbench" and name in heavy:
            why = "disk-heavy"
        elif bench == "tbench" and name in TB_ORACLE_FAILS:
            why = "oracle fails"
        elif bench == "tbench" and agent_timeout(root / name) > TB_MAX_AGENT_S:
            why = f"agent timeout over {TB_MAX_AGENT_S:.0f} s"
        if why:
            excluded.setdefault(why, []).append(name)
        else:
            out.append(name)
    return out, excluded


def entry(bench: str, name: str) -> dict:
    task = DATASETS[bench] / name
    return {"id": name, "bench": bench, "path": str(task), "agent_timeout_s": agent_timeout(task)}


def draw() -> dict:
    rng = random.Random(SEED)
    main, smoke, reserves, excluded, pools = {}, {}, {}, {}, {}
    for bench in BENCHES:
        names, excl = eligible(bench)
        rng.shuffle(names)
        pools[bench] = len(names)
        excluded[bench] = excl
        main[bench] = names[:N_MAIN]
        smoke[bench] = names[N_MAIN:N_MAIN + N_SMOKE]
        reserves[bench] = names[N_MAIN + N_SMOKE:]
    queue = []
    for k in range(N_MAIN):
        order = list(BENCHES)
        rng.shuffle(order)
        queue += [entry(b, main[b][k]) for b in order]
    smoke_list = [entry(b, smoke[b][k]) for k in range(N_SMOKE) for b in BENCHES]
    return {"seed": SEED, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "eligible": pools,
            "excluded": excluded, "main": queue, "smoke": smoke_list,
            "reserves": {b: [entry(b, n) for n in reserves[b]] for b in BENCHES}, "replaced": [], "oracle": {}}


def load() -> dict:
    return json.loads(TASKS.read_text())


def save(data: dict) -> None:
    TASKS.write_text(json.dumps(data, indent=1) + "\n")


def link(data: dict) -> None:
    """data/dataset/<id> -> the task folder, for every main and smoke task."""
    DATASET.mkdir(parents=True, exist_ok=True)
    want = {t["id"]: t["path"] for t in data["main"] + data["smoke"]}
    for p in DATASET.iterdir():
        if p.name not in want:
            p.unlink()
    for name, path in want.items():
        p = DATASET / name
        if not p.is_symlink():
            p.symlink_to(path)


def oracle_results() -> dict[str, float | None]:
    """task id -> reward of its latest oracle trial (None: no reward, e.g. an exception)."""
    out: dict[str, tuple[str, float | None]] = {}
    for res in sorted(ORACLE.glob("*/*/result.json")):
        try:
            r = json.loads(res.read_text())
        except (OSError, ValueError):
            continue
        name = r.get("task_name") or res.parent.name.rsplit("__", 1)[0]
        name = name.split("/")[-1]
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        stamp = r.get("finished_at") or r.get("started_at") or ""
        if name not in out or stamp >= out[name][0]:
            out[name] = (stamp, None if reward is None else float(reward))
    return {k: v for k, (_, v) in out.items()}


def oracle(n: int) -> int:
    data = load()
    link(data)
    done = oracle_results()
    todo = [t["id"] for t in data["main"] + data["smoke"] if done.get(t["id"]) != 1.0]
    if not todo:
        print("[oracle] every listed task passed")
        return 0
    name = f"check_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    cmd = [str(DIR / "wb_eval.sh"), "harbor", "run", "-p", str(DATASET.relative_to(DIR)), "-e", "docker",
           "-a", "oracle", "-o", str(ORACLE.relative_to(DIR)), "--job-name", name, "-n", str(n), "-k", "1", "-q", "-y"]
    for t in todo:
        cmd += ["-i", t]
    print(f"[oracle] {len(todo)} tasks, job {name}, -n {n}", flush=True)
    t0 = time.time()
    with open(DATA / f"oracle_{name}.txt", "w") as out:
        rc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT).returncode
    res = oracle_results()
    passed = sum(res.get(t) == 1.0 for t in todo)
    print(f"[oracle] rc {rc} in {time.time() - t0:.0f} s: {passed}/{len(todo)} passed", flush=True)
    data["oracle"] = {t["id"]: res.get(t["id"]) for t in data["main"] + data["smoke"]}
    save(data)
    return 0 if passed == len(todo) else 1


def replace() -> int:
    data = load()
    res = oracle_results()
    n = 0
    for group in ("main", "smoke"):
        for i, t in enumerate(data[group]):
            if t["id"] in res and res[t["id"]] != 1.0:
                if not data["reserves"][t["bench"]]:
                    raise SystemExit(f"no reserves left for {t['bench']}")
                new = data["reserves"][t["bench"]].pop(0)
                data["replaced"].append({"group": group, "pos": i, "old": t["id"], "new": new["id"],
                                         "reward": res[t["id"]], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                data[group][i] = new
                n += 1
    save(data)
    link(data)
    print(f"[replace] {n} replaced")
    return 0


def queue() -> int:
    """data/queue/main/q<pos>__<task> and data/queue/smoke/s<pos>__<task>, once every listed task passed its oracle."""
    data = load()
    res = oracle_results()
    bad = [t["id"] for t in data["main"] + data["smoke"] if res.get(t["id"]) != 1.0]
    if bad:
        print(f"[queue] refused: {len(bad)} tasks have not passed the oracle: {bad[:5]}")
        return 1
    for group, prefix in (("main", "q"), ("smoke", "s")):
        out = QUEUE / group
        if out.exists():
            for p in out.iterdir():
                p.unlink()
        out.mkdir(parents=True, exist_ok=True)
        width = 3 if group == "main" else 2
        for i, t in enumerate(data[group], 1):
            (out / f"{prefix}{i:0{width}d}__{t['id']}").symlink_to(t["path"])
    print(f"[queue] main {len(data['main'])}, smoke {len(data['smoke'])} links in {QUEUE}")
    return 0


def queue_names(group: str) -> list[str]:
    """The link names of a queue group, in queue order."""
    return sorted(p.name for p in (QUEUE / group).iterdir())


def status() -> None:
    data = load()
    res = oracle_results()
    for group in ("main", "smoke"):
        ids = [t["id"] for t in data[group]]
        print(f"{group}: {len(ids)} tasks; oracle passed {sum(res.get(i) == 1.0 for i in ids)}, failed "
              f"{sum(i in res and res[i] != 1.0 for i in ids)}, not run {sum(i not in res for i in ids)}")
    print("replaced:", len(data["replaced"]), "eligible:", data["eligible"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("draw")
    o = sub.add_parser("oracle")
    o.add_argument("-n", type=int, default=16)
    sub.add_parser("replace")
    sub.add_parser("queue")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "draw":
        if TASKS.exists():
            raise SystemExit(f"{TASKS} exists; the tasks are drawn once")
        DATA.mkdir(parents=True, exist_ok=True)
        data = draw()
        save(data)
        link(data)
        print(f"[draw] eligible {data['eligible']}; main {len(data['main'])}, smoke {len(data['smoke'])}; excluded "
              f"{ {b: {k: len(v) for k, v in e.items()} for b, e in data['excluded'].items()} }")
    elif a.cmd == "oracle":
        sys.exit(oracle(a.n))
    elif a.cmd == "replace":
        sys.exit(replace())
    elif a.cmd == "queue":
        sys.exit(queue())
    else:
        status()


if __name__ == "__main__":
    main()
