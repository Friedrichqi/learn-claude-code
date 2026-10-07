#!/usr/bin/env python3
"""Arms, levels and cells of research/14_pd_crossover (PLAN.md §3 and §5): where does prefill/decode disaggregation
stop paying on 2 and 4 H200s?

An *arm* is a serving layout: a list of vLLM instances (role C = colocated, P = prefill, D = decode; TP size; GPU
indices inside the job) plus the router mode in front of them (pd_router.py: dp / pd / pass). A *level* is one arm at
one N, for one workload family:
  L  12's LIGHT pool, closed loop, think time 0 (12's fleet setup)          live: mu_sweep;  replay: pd_replay
  H  12's HEAVY pool (test suites, site-packages searches), think time 0    live
  T  12's LIGHT pool with 11's think time (lognormal, median 30 s)          live
A *cell* is one Slurm job: its levels in N-major order with the arm list rotated left by the N's position (so a
partly finished job still has every arm at every finished N, and time drift does not line up with one arm), then one
duplicate level (`_dup`) that measures run-to-run noise.

Levels are 12's `hw_workloads.level()` dicts (the shape 11's MU_EXTRA_CONDITIONS hook reads) plus a "pd" block:
  arm, wl, kind (live | replay | smoke), n, window_s, warmup_s, think_scale, idle_s, dup
Replay levels also carry 11's "replay" block (src = the per-N plan, policy = the arm) for mu_replay.Replay.

    python research/14_pd_crossover/pd_workloads.py build                 # data/conditions.json
    python research/14_pd_crossover/pd_workloads.py cell l2a --field levels
    python research/14_pd_crossover/pd_workloads.py level pd_1p1d_L_n08 --field window_s
    python research/14_pd_crossover/pd_workloads.py instances pd_1p1d_L_n08   # JSON: one entry per vLLM instance
    python research/14_pd_crossover/pd_workloads.py show                  # every cell, level order and budget
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hw_workloads as hw  # noqa: E402  research/12_hw_utilization
import mu_workloads as mw  # noqa: E402  research/11_multiuser_kv

DIR = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PD_DATA") or DIR / "data")
CONDITIONS_JSON = DATA / "conditions.json"
DECISIONS_JSON = DATA / "decisions.json"        # S1's choices (visibility, UCX_TLS, lease, restart rule)
MODEL = hw.M32
KV_BYTES_PER_TOKEN = 64 * 8 * 128 * 2 * 2      # Qwen3-32B: layers x KV heads x head dim x (K, V) x bf16 = 256 KiB
SCHEDULER = "pd_scheduler.PDScheduler"

W_LIVE, W_REPLAY, W_SMOKE, W_RSMOKE = 1200, 900, 300, 180
IDLE_S = {"L": 60, "H": 120, "T": 300}           # router: when a session without a session_end hint stops counting
# LIGHT levels at N start at their own offset in 12's 512-session LIGHT pool (about 3 sessions per agent apart), so
# the live sweep records ~125 different sessions for the replay pool; every arm at one N runs the same sequence, and
# 2- and 4-GPU levels at the same N run the same sessions. HEAVY and think-time levels start at the pool's head (12).
L_OFFSET = {1: 0, 2: 3, 4: 9, 8: 21, 12: 45, 16: 81, 24: 129, 32: 201, 48: 297}
POOLS = {"L": hw.LPOOL, "H": hw.HPOOL, "T": hw.LPOOL}
THINK = {"L": 0.0, "H": 0.0, "T": 1.0}


# -- arms ---------------------------------------------------------------------------------------------------------
def inst(role: str, tp: int, gpus: list[int], extra: list[str] | None = None) -> dict:
    return {"role": role, "tp": tp, "gpus": list(gpus), "vllm": list(extra or [])}


C2K = ["--max-num-batched-tokens", "2048"]
ARMS: dict[str, dict] = {
    # 2 GPUs
    "dp2": {"g": 2, "router": "dp", "inst": [inst("C", 1, [0]), inst("C", 1, [1])]},
    "dp2c": {"g": 2, "router": "dp", "inst": [inst("C", 1, [0], C2K), inst("C", 1, [1], C2K)]},
    "tp2": {"g": 2, "router": "pass", "inst": [inst("C", 2, [0, 1])]},
    "1p1d": {"g": 2, "router": "pd", "inst": [inst("P", 1, [0]), inst("D", 1, [1])]},
    # 4 GPUs, core
    "dp4": {"g": 4, "router": "dp", "inst": [inst("C", 1, [i]) for i in range(4)]},
    "tp2dp2": {"g": 4, "router": "dp", "inst": [inst("C", 2, [0, 1]), inst("C", 2, [2, 3])]},
    "2p2d": {"g": 4, "router": "pd", "inst": [inst("P", 1, [0]), inst("P", 1, [1]), inst("D", 1, [2]), inst("D", 1, [3])]},
    "3p1d": {"g": 4, "router": "pd", "inst": [inst("P", 1, [0]), inst("P", 1, [1]), inst("P", 1, [2]), inst("D", 1, [3])]},
    "1p1dt2": {"g": 4, "router": "pd", "inst": [inst("P", 2, [0, 1]), inst("D", 2, [2, 3])]},
    # 4 GPUs, secondary
    "1p3d": {"g": 4, "router": "pd", "inst": [inst("P", 1, [0]), inst("D", 1, [1]), inst("D", 1, [2]), inst("D", 1, [3])]},
    "tp4": {"g": 4, "router": "pass", "inst": [inst("C", 4, [0, 1, 2, 3])]},
    "2p1dt2": {"g": 4, "router": "pd", "inst": [inst("P", 1, [0]), inst("P", 1, [1]), inst("D", 2, [2, 3])]},
}
DISPLAY = {"1p1dt2": "1p1d-tp2", "2p1dt2": "2p1d-dtp2"}
COLOCATED = {a for a, s in ARMS.items() if s["router"] != "pd"}
CORE4 = ["dp4", "tp2dp2", "2p2d", "3p1d", "1p1dt2"]
SEC4 = ["1p3d", "tp4", "2p1dt2"]
ARMS2 = ["dp2", "tp2", "1p1d"]
REPLAY2 = ["dp2", "dp2c", "tp2", "1p1d"]


def display(arm: str) -> str:
    return DISPLAY.get(arm, arm)


def cores_needed(arm: str) -> int:
    return sum(4 * i["tp"] for i in ARMS[arm]["inst"])


# -- levels -------------------------------------------------------------------------------------------------------
def warmup(n: int, kind: str) -> int:
    if kind in ("smoke", "rsmoke"):
        return min(120, 60 * n)
    return min(300, 60 * n)


def level_name(arm: str, wl: str, n: int, kind: str = "live", dup: bool = False) -> str:
    tag = {"live": "", "replay": "_rep", "smoke": "_smk", "rsmoke": "_rsmk"}[kind]
    return f"pd_{arm}_{wl}_n{n:02d}{tag}{'_dup' if dup else ''}"


def make_level(arm: str, wl: str, n: int, kind: str = "live", dup: bool = False) -> tuple[str, dict]:
    window = {"live": W_LIVE, "replay": W_REPLAY, "smoke": W_SMOKE, "rsmoke": W_RSMOKE}[kind]
    lo, hi = POOLS[wl]
    if wl == "L" and kind != "replay":
        lo += L_OFFSET.get(n, 0)
    c = hw.level(n, (lo, hi), MODEL, window)
    wu = warmup(n, kind)
    c["hw"]["warmup_s"] = wu
    c["pd"] = {"arm": arm, "wl": wl, "kind": kind, "n": n, "window_s": window, "warmup_s": wu,
               "think_scale": THINK[wl], "idle_s": IDLE_S[wl], "dup": dup, "g": ARMS[arm]["g"],
               "router": ARMS[arm]["router"]}
    if kind in ("replay", "rsmoke"):
        pool = "p2a12" if kind == "rsmoke" else "l2"
        c["replay"] = {"src": f"{pool}_n{n:02d}", "policy": arm, "think_scale": 0.0, "window_s": wu + window}
        c["fillers"] = True                            # mu_replay's plan rows carry their own kind
    return level_name(arm, wl, n, kind, dup), c


def rotated(arms: list[str], k: int) -> list[str]:
    k %= len(arms)
    return arms[k:] + arms[:k]


def cell_levels(plan: list[tuple[int, list[str]]], wl: str, kind: str, dup: tuple[str, int] | None) -> list[tuple]:
    """[(n, arms)] in N order -> [(arm, wl, n, kind, dup)] N-major, arms rotated left by the N's position."""
    out = [(arm, wl, n, kind, False) for k, (n, arms) in enumerate(plan) for arm in rotated(arms, k)]
    if dup:
        out.append((dup[0], wl, dup[1], kind, True))
    return out


A2C = ARMS2[:2] + ["dp2c"] + ARMS2[2:]               # dp2, tp2, dp2c, 1p1d
CELL_PLANS: dict[str, dict] = {
    "s1": {"g": 2, "calib": True, "levels":
           [(a, "L", 4, "smoke", False) for a in ("dp2", "dp2c", "tp2", "1p1d")]
           + [("1p1d", "H", 4, "smoke", False), ("1p1d", "T", 4, "smoke", False), ("1p1d", "L", 4, "rsmoke", False)]},
    "l2a": {"g": 2, "levels": cell_levels([(1, ARMS2), (2, ARMS2), (4, A2C), (8, A2C)], "L", "live", ("1p1d", 8))},
    "l2b": {"g": 2, "levels": cell_levels([(12, A2C), (16, A2C), (24, ARMS2)], "L", "live", ("dp2", 12))},
    "x2": {"g": 2, "levels": cell_levels([(4, ARMS2), (8, ARMS2), (16, ARMS2)], "H", "live", None)
           + cell_levels([(8, ARMS2), (16, ARMS2), (24, ARMS2)], "T", "live", ("1p1d", 16))},
    "s3": {"g": 4, "calib4": True, "levels": [(a, "L", 8, "smoke", False) for a in CORE4 + SEC4]},
    "l4a": {"g": 4, "levels": cell_levels([(4, CORE4), (8, CORE4 + SEC4), (16, CORE4 + SEC4)], "L", "live",
                                           ("2p2d", 8))},
    "l4b": {"g": 4, "levels": cell_levels([(24, CORE4), (32, CORE4 + SEC4), (48, CORE4)], "L", "live",
                                           ("3p1d", 32))},
    "r2a": {"g": 2, "levels": cell_levels([(n, REPLAY2) for n in (2, 3, 4, 5, 6)], "L", "replay", ("1p1d", 4))},
    "r2b": {"g": 2, "levels": cell_levels([(n, REPLAY2) for n in (8, 10, 12, 16)], "L", "replay", ("dp2", 10))},
}

CONDITIONS: dict[str, dict] = {}
# Gate markers (PLAN.md §7): a sweep cell runs only once its smoke cell wrote data/PASS_<cell> (gates G1-G5 and the
# analysis sanity check on the smoke logs); arms that failed G6 are listed in data/dropped_arms.json and skipped.
NEEDS = {"s3": "s1", "l2a": "s1", "l2b": "s1", "x2": "s1", "r2a": "s1", "r2b": "s1", "l4a": "s3", "l4b": "s3"}
DROPPED_JSON = DATA / "dropped_arms.json"
DROPPED = set(json.loads(DROPPED_JSON.read_text())) if DROPPED_JSON.exists() else set()

CELLS: dict[str, dict] = {}
for _cell, _spec in CELL_PLANS.items():
    names = []
    for _arm, _wl, _n, _kind, _dup in _spec["levels"]:
        _name, _c = make_level(_arm, _wl, _n, _kind, _dup)
        CONDITIONS[_name] = _c
        if _arm not in DROPPED or _spec.get("calib4"):
            names.append(_name)
    CELLS[_cell] = {"g": _spec["g"], "levels": names, "calib": bool(_spec.get("calib")),
                    "calib4": bool(_spec.get("calib4")), "needs": NEEDS.get(_cell, "")}

if os.environ.get("PD_TEST_CELLS"):                    # test_pd.py: tiny cells through the same code paths
    for _cell, _arms, _g in (("tt2", ["dp2", "tp2", "1p1d"], 2), ("tt4", ["dp4", "2p2d", "3p1d", "2p1dt2"], 4)):
        names = []
        for _arm in _arms:
            _name, _c = make_level(_arm, "L", 2, "smoke")
            CONDITIONS[_name] = _c
            names.append(_name)
        CELLS[_cell] = {"g": _g, "levels": names, "calib": False, "calib4": False}
    names = []
    for _arm in ("dp2", "1p1d"):
        _name, _c = make_level(_arm, "L", 2, "rsmoke")
        _c["replay"]["src"] = "tt_n02"
        CONDITIONS[_name] = _c
        names.append(_name)
    CELLS["ttr"] = {"g": 2, "levels": names, "calib": False, "calib4": False}


# -- servers ------------------------------------------------------------------------------------------------------
def decisions() -> dict:
    d = {"visibility": "A", "ucx_tls": None, "kv_lease_duration": None, "restart_each_level": False}
    if DECISIONS_JSON.exists():
        d.update(json.loads(DECISIONS_JSON.read_text(encoding="utf-8")))
    return d


def kv_config(role: str, dec: dict | None = None) -> dict | None:
    dec = dec or decisions()
    if role == "C":
        return None
    cfg = {"kv_connector": "NixlConnector", "kv_role": "kv_producer" if role == "P" else "kv_consumer"}
    if role == "D":
        cfg["kv_load_failure_policy"] = "recompute"
    if dec.get("kv_lease_duration"):
        cfg["kv_connector_extra_config"] = {"kv_lease_duration": int(dec["kv_lease_duration"])}
    return cfg


def server_args(arm: str, idx: int, dec: dict | None = None) -> list[str]:
    """Full `vllm serve MODEL` flag list of instance `idx` of `arm` (host/port added by pd_launch.py)."""
    spec = ARMS[arm]["inst"][idx]
    args = list(mw.VLLM_COMMON)
    i = args.index("--scheduler-cls")
    args[i + 1] = SCHEDULER
    if spec["tp"] > 1:
        args += ["--tensor-parallel-size", str(spec["tp"])]
    cfg = kv_config(spec["role"], dec)
    if cfg:
        args += ["--kv-transfer-config", json.dumps(cfg, separators=(",", ":"))]
    return args + list(spec["vllm"])


def instances(level: str, dec: dict | None = None) -> list[dict]:
    """One entry per vLLM instance of the level's arm: role, tp, gpus (job-relative), cores, args, env."""
    dec = dec or decisions()
    arm = CONDITIONS[level]["pd"]["arm"]
    spec = ARMS[arm]
    g = spec["g"]
    out, core = [], 0
    for idx, it in enumerate(spec["inst"]):
        own = list(it["gpus"])
        visible = own + ([x for x in range(g) if x not in own] if dec["visibility"] == "A" else [])
        env = {"PD_ROLE": it["role"], "PD_ARM": arm, "PD_IDX": str(idx), "PD_TP": str(it["tp"]),
               "PD_GPUS": ",".join(map(str, own))}
        if it["role"] != "C":
            env["VLLM_NIXL_SIDE_CHANNEL_HOST"] = "127.0.0.1"
            if dec.get("ucx_tls"):
                env["UCX_TLS"] = dec["ucx_tls"]
        ncores = 4 * it["tp"]
        out.append({"idx": idx, "role": it["role"], "tp": it["tp"], "gpus": own, "visible": visible,
                    "cores": [core, core + ncores], "args": server_args(arm, idx, dec), "env": env})
        core += ncores
    return out


def router_spec(level: str) -> dict:
    p = CONDITIONS[level]["pd"]
    return {"mode": p["router"], "idle_s": p["idle_s"]}


def server_key(level: str) -> str:
    """Levels with the same key can share running servers (same arm, same flags)."""
    return json.dumps([CONDITIONS[level]["pd"]["arm"], instances(level)], sort_keys=True)


def level_seconds(level: str, restart_s: float) -> float:
    p = CONDITIONS[level]["pd"]
    return p["warmup_s"] + p["window_s"] + 60 + restart_s


def export_conditions(path: Path = CONDITIONS_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CONDITIONS, indent=1), encoding="utf-8")


def show() -> str:
    lines, total = [], 0.0
    for cell, spec in CELLS.items():
        g = spec["g"]
        secs = sum(level_seconds(name, 150 if g == 2 else 240) for name in spec["levels"])
        if spec["calib"] or spec["calib4"]:
            secs += 3600 if spec["calib"] else 1800
        total += secs / 3600 * g
        lines.append(f"{cell}: {g} GPUs, {len(spec['levels'])} levels, {secs / 3600:.1f} h, "
                     f"{secs / 3600 * g:.1f} GPU-h")
        lines += [f"    {name}" for name in spec["levels"]]
    lines.append(f"total {total:.1f} GPU-h")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--out", default=str(CONDITIONS_JSON))
    p = sub.add_parser("cell")
    p.add_argument("name")
    p.add_argument("--field", required=True)
    p = sub.add_parser("level")
    p.add_argument("name")
    p.add_argument("--field", required=True)
    p = sub.add_parser("instances")
    p.add_argument("name")
    p = sub.add_parser("key")
    p.add_argument("name")
    p = sub.add_parser("decision")
    p.add_argument("key")
    sub.add_parser("show")
    a = ap.parse_args()
    if a.cmd == "build":
        export_conditions(Path(a.out))
        print(f"[pd_workloads] {len(CONDITIONS)} conditions -> {a.out}")
    elif a.cmd == "cell":
        v = CELLS[a.name].get(a.field, "")
        print(" ".join(v) if isinstance(v, list) else (int(v) if isinstance(v, bool) else v))
    elif a.cmd == "level":
        c = CONDITIONS[a.name]
        if a.field == "client_env":
            env = {"TM_CLIENT_HINTS": "1"} if c["pd"]["kind"] in ("live", "smoke") else {}
            print(" ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())))
        elif a.field == "specs":
            print(" ".join(map(str, c["specs"])))
        else:
            v = c["pd"].get(a.field, c.get(a.field))
            print(int(v) if isinstance(v, bool) else ("" if v is None else v))
    elif a.cmd == "instances":
        print(json.dumps(instances(a.name)))
    elif a.cmd == "key":
        print(server_key(a.name))
    elif a.cmd == "decision":
        v = decisions().get(a.key)
        print(int(v) if isinstance(v, bool) else ("" if v is None else v))
    elif a.cmd == "show":
        print(show())
    return 0


if __name__ == "__main__":
    sys.exit(main())
