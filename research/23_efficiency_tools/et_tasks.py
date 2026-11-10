#!/usr/bin/env python3
"""Topic 23's task lists.
  main   topic 22's 96 tasks (22_wait_bench/data/tasks.json, seed 2211) in its queue order; the runs read topic 22's
         data/queue/main links (read only).
  pilot  48 fresh tasks for the pilot, the prompt screens and the smoke runs: 16 per benchmark, drawn with seed 2212
         from topic 22's ordered reserves (so none of its 96 main or 12 smoke tasks), interleaved one task of each
         benchmark per round.
`oracle` runs Harbor's oracle agent (each task's reference solution) over the main and pilot tasks in a fresh job.
That pulls every image into this topic's docker-in-docker daemon (topic 22's was removed) and checks that every task
still passes. A pilot task that fails is replaced by its benchmark's next reserve (`replace`).

    python research/23_efficiency_tools/et_tasks.py pilot     # data/tasks_pilot.json, data/queue/pilot/
    python research/23_efficiency_tools/et_tasks.py oracle    # data/jobs/oracle/<job>, data/oracle_<job>.txt
    python research/23_efficiency_tools/et_tasks.py replace   # swap failed pilot tasks for reserves, then oracle again
    python research/23_efficiency_tools/et_tasks.py status
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(REPO / "research" / "22_wait_bench"))
import wb_tasks as wt  # noqa: E402

DATA = DIR / "data"
PILOT = DATA / "tasks_pilot.json"
DATASET = DATA / "dataset"          # links to every main and pilot task, for the oracle
QUEUE = DATA / "queue"
ORACLE = DATA / "jobs" / "oracle"
MAIN_QUEUE = wt.QUEUE / "main"      # topic 22's, read only
SEED = 2212
N_PILOT = 16


def main_tasks() -> list[dict]:
    return wt.load()["main"]


def draw_pilot() -> dict:
    rng = random.Random(SEED)
    reserves = wt.load()["reserves"]
    picked, rest = {}, {}
    for bench in wt.BENCHES:
        pool = list(reserves[bench])
        rng.shuffle(pool)
        picked[bench], rest[bench] = pool[:N_PILOT], pool[N_PILOT:]
    queue = []
    for k in range(N_PILOT):
        order = list(wt.BENCHES)
        rng.shuffle(order)
        queue += [picked[b][k] for b in order]
    return {"seed": SEED, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "22_wait_bench/data/tasks.json reserves", "pilot": queue, "reserves": rest, "replaced": [],
            "oracle": {}}


def load_pilot() -> dict:
    return json.loads(PILOT.read_text())


def link_dataset(pilot: dict) -> None:
    DATASET.mkdir(parents=True, exist_ok=True)
    want = {t["id"]: t["path"] for t in main_tasks() + pilot["pilot"]}
    for p in DATASET.iterdir():
        if p.name not in want:
            p.unlink()
    for name, path in want.items():
        p = DATASET / name
        if not p.is_symlink():
            p.symlink_to(path)


def link_queue(pilot: dict) -> None:
    out = QUEUE / "pilot"
    if out.exists():
        for p in out.iterdir():
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(pilot["pilot"], 1):
        (out / f"p{i:02d}__{t['id']}").symlink_to(t["path"])


def oracle_results() -> dict[str, float | None]:
    old, wt.ORACLE = wt.ORACLE, ORACLE
    try:
        return wt.oracle_results()
    finally:
        wt.ORACLE = old


def oracle(n: int) -> int:
    pilot = load_pilot()
    link_dataset(pilot)
    done = oracle_results()
    todo = [t["id"] for t in main_tasks() + pilot["pilot"] if done.get(t["id"]) != 1.0]
    if not todo:
        print("[oracle] every task passed")
        return 0
    name = f"check_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    cmd = [str(DIR / "et_eval.sh"), "harbor", "run", "-p", str(DATASET), "-e", "docker", "-a", "oracle",
           "-o", str(ORACLE), "--job-name", name, "-n", str(n), "-k", "1", "-q", "-y"]
    for t in todo:
        cmd += ["-i", t]
    print(f"[oracle] {len(todo)} tasks, job {name}, -n {n}", flush=True)
    t0 = time.time()
    with open(DATA / f"oracle_{name}.txt", "w") as out:
        rc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT).returncode
    res = oracle_results()
    passed = sum(res.get(t) == 1.0 for t in todo)
    print(f"[oracle] rc {rc} in {time.time() - t0:.0f} s: {passed}/{len(todo)} passed", flush=True)
    pilot = load_pilot()          # read again: a `replace` may have run while the job did (2026-11-02, see README)
    pilot["oracle"] = {t["id"]: res.get(t["id"]) for t in pilot["pilot"]}
    PILOT.write_text(json.dumps(pilot, indent=1) + "\n")
    return 0 if passed == len(todo) else 1


def replace() -> int:
    pilot = load_pilot()
    res = oracle_results()
    n = 0
    for i, t in enumerate(pilot["pilot"]):
        if t["id"] in res and res[t["id"]] != 1.0:
            new = pilot["reserves"][t["bench"]].pop(0)
            pilot["replaced"].append({"pos": i, "old": t["id"], "new": new["id"], "reward": res[t["id"]],
                                      "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            pilot["pilot"][i] = new
            n += 1
    PILOT.write_text(json.dumps(pilot, indent=1) + "\n")
    link_dataset(pilot)
    link_queue(pilot)
    print(f"[replace] {n} pilot tasks replaced")
    return 0


def status() -> None:
    res = oracle_results()
    groups = {"main": main_tasks()}
    if PILOT.exists():
        groups["pilot"] = load_pilot()["pilot"]
    for g, tasks in groups.items():
        ids = [t["id"] for t in tasks]
        print(f"{g}: {len(ids)} tasks; oracle passed {sum(res.get(i) == 1.0 for i in ids)}, failed "
              f"{sum(i in res and res[i] != 1.0 for i in ids)}, not run {sum(i not in res for i in ids)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pilot")
    o = sub.add_parser("oracle")
    o.add_argument("-n", type=int, default=16)
    sub.add_parser("replace")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "pilot":
        if PILOT.exists():
            raise SystemExit(f"{PILOT} exists; the draw is frozen")
        pilot = draw_pilot()
        DATA.mkdir(parents=True, exist_ok=True)
        PILOT.write_text(json.dumps(pilot, indent=1) + "\n")
        link_dataset(pilot)
        link_queue(pilot)
        print(f"[pilot] {len(pilot['pilot'])} tasks -> {PILOT}")
    elif a.cmd == "oracle":
        sys.exit(oracle(a.n))
    elif a.cmd == "replace":
        sys.exit(replace())
    else:
        status()


if __name__ == "__main__":
    main()
