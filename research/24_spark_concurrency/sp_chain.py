#!/usr/bin/env python3
"""Topic 24's chain: the cells in order, resumable, within a total budget (PLAN2.md, "Cells").

    nohup sg docker -c "~/.venv/bin/python research/24_spark_concurrency/sp_chain.py run" \
        > research/24_spark_concurrency/data/runs/chain.out 2>&1 &
    ~/.venv/bin/python research/24_spark_concurrency/sp_chain.py status

- Part 1 ran BF16 at 8 users (cell `n08`, done). Part 2 runs FP8 and NVFP4 at 8 users, then all three precisions at
  1, 4 and 2 users. Every cell ends by the stable rule (sp_stable.py) or at its hard limit.
- A usable cell (ended by a planned stop) is skipped when the chain is restarted; a cell that is still running
  (started by an earlier chain process) is waited for first. A failed cell is run again, once, when its turn in
  the list comes.
- After each cell the chain analyses it (sp_analyze.py, then tables and figures from the cached results) and waits
  for that to finish before the next cell starts. Nothing else may use memory while a server starts: on 2026-11-11
  an analysis that was still running took about 10 GiB, the next server found less free memory than
  --gpu-memory-utilization wants and exited, and umbench could not create its CUDA context.
- Before each cell the chain looks at the root counter sampler (sp_hw.py): it stops by itself 60 hours after its
  start. A cell that could outlive it is still run, with a warning in chain.log.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402

HARD_H = 4.0
CELLS = [  # name, variant, N, hard limit (h)
    ("n08", "bf16", 8, HARD_H),                                   # part 1, done 2026-11-10
    ("fp8_n08", "fp8", 8, HARD_H), ("nvfp4_n08", "nvfp4", 8, HARD_H),
    ("bf16_n01", "bf16", 1, HARD_H), ("fp8_n01", "fp8", 1, HARD_H), ("nvfp4_n01", "nvfp4", 1, HARD_H),
    ("bf16_n04", "bf16", 4, HARD_H), ("fp8_n04", "fp8", 4, HARD_H), ("nvfp4_n04", "nvfp4", 4, HARD_H),
    ("bf16_n02", "bf16", 2, HARD_H), ("fp8_n02", "fp8", 2, HARD_H), ("nvfp4_n02", "nvfp4", 2, HARD_H),
]
OVERHEAD_H = 0.35          # server start, baseline, coda, teardown
BUDGET_H = 50.0            # for part 2, counted from its first chain start
SAMPLER_CAP_H = 60.0       # sp_hw.py's own limit
STATE = C.RUNS / "chain2.json"
LOG = C.RUNS / "chain.log"


def log(msg: str) -> None:
    C.log(msg, LOG)


def cell_info(name: str) -> dict | None:
    try:
        return json.loads((C.RUNS / name / "cell.json").read_text())
    except (OSError, ValueError):
        return None


def window_h(info: dict) -> float | None:
    m = info.get("marks", {})
    if "users_start" in m and "window_end" in m:
        return (m["window_end"]["t"] - m["users_start"]["t"]) / 3.6e12
    return None


def usable(info: dict | None) -> bool:
    """A cell whose window can be analysed: it ended by a planned stop."""
    return bool(info) and (info.get("status") == "done" or info.get("stop_reason") in ("hard_limit", "stable"))


def analyse(name: str) -> None:
    """The finished cell's tables and figures, before anything else starts."""
    info = cell_info(name)
    if not usable(info):
        return
    env = dict(__import__("os").environ, HF_HOME=C.HF_HOME)
    with open(C.RUNS / "analysis.out", "a") as out:
        for args in (["sp_analyze.py", "--cells", name], ["sp_analyze.py", "--reuse"], ["sp_figs.py"]):
            rc = subprocess.call(["taskset", "-c", C.AUX_CORES, str(C.EXP01_VENV_PY), str(C.DIR / args[0])] + args[1:],
                                 stdout=out, stderr=subprocess.STDOUT, env=env)
            if rc:
                log(f"cell {name}: {' '.join(args)} ended with rc {rc} (see analysis.out)")
    log(f"cell {name}: analysed")


def wait_running(name: str) -> None:
    """A cell started by an earlier chain process is still running: wait for it instead of starting it again."""
    info = cell_info(name)
    if not info or info.get("status") in ("done", "failed") or not info.get("pid"):
        return
    pid = int(info["pid"])
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return
    if b"sp_cell.py" not in cmd:
        return
    log(f"cell {name}: already running as pid {pid}; waiting for it")
    while Path(f"/proc/{pid}").exists():
        time.sleep(30)
    report(name)


def report(name: str) -> None:
    info = cell_info(name) or {}
    log(f"cell {name}: status {info.get('status')}, reason {info.get('stop_reason')}, "
        f"window {window_h(info) or 0:.2f} h, counters {'yes' if info.get('hw_ok') else 'NO'}")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"t0_wall": time.time(), "decisions": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def elapsed_h(st: dict) -> float:
    return (time.time() - st["t0_wall"]) / 3600


def sampler() -> dict | None:
    try:
        hb = json.loads((C.HW / "heartbeat.json").read_text())
    except (OSError, ValueError):
        return None
    hb["age_s"] = (time.monotonic_ns() - hb.get("t", 0)) / 1e9
    return hb


def run_cell(name: str, variant: str, n: int, hard_h: float, force: bool = False) -> int:
    cmd = [str(C.VLLM_PY), str(C.DIR / "sp_cell.py"), "run", "--n", str(n), "--name", name, "--variant", variant,
           "--stop-rule", "stable", "--hard-limit-h", f"{hard_h:.2f}"] + (["--force"] if force else [])
    log(f"cell {name}: {variant}, N={n}, hard limit {hard_h:.2f} h: {' '.join(cmd)}")
    with open(C.RUNS / f"{name}.out", "a") as out:
        return subprocess.call(cmd, stdout=out, stderr=subprocess.STDOUT)


def before(st: dict, name: str, hard_h: float) -> bool:
    """Budget and sampler checks before a cell. False: do not start it."""
    left = BUDGET_H - elapsed_h(st)
    if hard_h + OVERHEAD_H > left:
        st["decisions"].append({"cell": name, "skipped": f"{hard_h + OVERHEAD_H:.1f} h needed, {left:.1f} h left"})
        log(f"cell {name}: skipped ({left:.1f} h of the budget left)")
        save_state(st)
        return False
    hb = sampler()
    if not hb or hb["age_s"] > 60 or hb.get("state") != "run":
        log(f"cell {name}: WARNING, the root counter sampler is not running (heartbeat {hb})")
    elif hb.get("uptime_s", 0) / 3600 + hard_h + OVERHEAD_H > SAMPLER_CAP_H:
        log(f"cell {name}: WARNING, the counter sampler stops itself in "
            f"{SAMPLER_CAP_H - hb['uptime_s'] / 3600:.1f} h; this cell may lose its counters at the end")
    return True


def cmd_run(a) -> int:
    st = load_state()
    save_state(st)
    log(f"chain (part 2): {elapsed_h(st):.2f} h used of {BUDGET_H} h")
    for name, *_ in CELLS:                                       # a cell left running by an earlier chain process
        wait_running(name)
        if usable(cell_info(name)) and not (C.TABLES / "cells" / f"{name}.json").exists():
            analyse(name)
    for name, variant, n, hard_h in CELLS:
        info = cell_info(name)
        if usable(info):
            continue
        failed = bool(info) and info.get("status") == "failed"
        if failed and st.get(f"repeated_{name}"):
            continue
        if not before(st, name, hard_h):
            continue
        if failed:
            st[f"repeated_{name}"] = True
            log(f"cell {name}: repeating (status {info.get('status')}, reason {info.get('stop_reason')}: "
                f"{str(info.get('error'))[:120]})")
        st["decisions"].append({"cell": name, "start_h": round(elapsed_h(st), 2), "hard_h": hard_h, "repeat": failed})
        save_state(st)
        run_cell(name, variant, n, hard_h, force=failed)
        report(name)
        analyse(name)
    log(f"chain done after {elapsed_h(st):.2f} h (the counter sampler is left running; "
        f"`echo stop > data/hw/control` ends it)")
    return 0


def cmd_status(a) -> int:
    if STATE.exists():
        print(f"chain (part 2): {elapsed_h(load_state()):.2f} h of {BUDGET_H} h")
    for name, variant, n, hard_h in CELLS:
        info = cell_info(name)
        if not info:
            print(f"{name:10s} {variant:6s} N={n:2d} not started")
            continue
        w = window_h(info)
        print(f"{name:10s} {variant:6s} N={n:2d} {info.get('status'):8s} {info.get('stop_reason')!s:12s} "
              f"window {w if w is None else round(w, 2)} h, pool {info.get('pool')}, counters {info.get('hw_ok')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("status")
    a = ap.parse_args()
    return {"run": cmd_run, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
