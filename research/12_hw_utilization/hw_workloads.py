#!/usr/bin/env python3
"""Session specs, conditions and cells of research/12_hw_utilization.

Sessions reuse 11's machinery unchanged (mu_sweep -> mu_session -> profile_run): a spec is a list of
turns run solo by the s15 harness in a fresh sandbox, here with think time 0 (an agent fleet).

  LIGHT  11's 4-turn EXPLAIN / MODIFY sessions on the 10 real codebases (S000-S127). Round 0 is 11's
         own spec (same nonce, same prompts); rounds 1-6 are the same specs with a fresh turn-1 nonce,
         so no two sessions of a cell share more than the harness's system prompt.
  HEAVY  06's tool-heavy tasks (research/06_tool_cost/tool_heavy_workloads.py), one turn each, with a
         nonce: HTEST fixes two bugs in a sandbox copy of the s10 task system, reruns its pytest file
         after each edit and the lessons' full suite (517 tests, ~45 s of one core) at the end;
         HSEARCH answers three exact-count questions with grep / find over the 7.6 GB site-packages.

Layout of the counted list (mu_sweep slices it by position):
  P1     0-13      S000-S007 (round 0), then HTEST / HSEARCH alternating, 3 each
  LPOOL  14-525    LIGHT rounds 1-4
  HPOOL  526-1037  HEAVY mix L, HTEST, L, HSEARCH, ... (HTEST : HSEARCH : LIGHT = 1 : 1 : 2), L from rounds 5-6
Windowed levels take the whole pool from its start, so the levels of a cell are paired on task order
(the prefix cache is reset between levels).

A cell is one server lifetime (model, TP, extra vLLM flags and server environment) running its levels in
order; a level is one mu_sweep condition with its own N, spec range, CPU layout and window. CPU layout
inside a cell's cores C (12 per TP=1 cell = the H200 node's 96 cores / 8 GPUs; 24 for a TP=2 cell): vLLM
gets C[:v], the sampler C[R], the agents C[R+1:R+1+a], with R = 4 (TP=1) or 8 (TP=2); `shared` puts vLLM
and the agents on the same C[:R] + C[R+1:R+1+a].

    python research/12_hw_utilization/hw_workloads.py build      # data/sessions.jsonl, data/conditions.json
    python research/12_hw_utilization/hw_workloads.py check
    python research/12_hw_utilization/hw_workloads.py cell p2a --field levels
    python research/12_hw_utilization/hw_workloads.py layout p2a hw_p2a_n08 0,1,2,3,4,5,6,7,8,9,10,11
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402  research/11_multiuser_kv
import tool_heavy_workloads as th  # noqa: E402  research/06_tool_cost

REPO = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent
DATA = DIR / "data"
SESSIONS = DATA / "sessions.jsonl"
CONDITIONS_JSON = DATA / "conditions.json"
CELLS_ROOT = DATA / "cells"

M32, M8 = "Qwen/Qwen3-32B", "Qwen/Qwen3-8B"
P1_LIGHT = 8
P1_HEAVY = 3
POOL = 512
P1 = (0, P1_LIGHT + 2 * P1_HEAVY)
LPOOL = (P1[1], P1[1] + POOL)
HPOOL = (LPOOL[1], LPOOL[1] + POOL)
WARMUP_S = 300            # mu_sweep's stagger: slot i starts at i * 300 / N s
HEAVY_SEED = str(REPO / th.FIXTURE)


def nonce(*parts) -> str:
    return hashlib.sha256(("12hw|" + "|".join(map(str, parts))).encode()).hexdigest()[:8]


def light_copy(spec: dict, rnd: int, position: int) -> dict:
    """11's spec with a fresh turn-1 nonce (round 0 = 11's own)."""
    out = json.loads(json.dumps(spec))
    out["src"] = spec["spec"]
    if rnd:
        new = nonce("light", spec["spec"], rnd)
        old = spec["nonce"]
        out["turns"][0]["prompt"] = out["turns"][0]["prompt"].replace(f"[run {old}]", f"[run {new}]", 1)
        out["nonce"] = new
    out["spec"] = f"L{rnd}{spec['spec'][1:]}"
    out["index"] = position
    out["filler"] = False
    out["kind"] = "LIGHT"
    return out


def heavy_spec(cat: str, k: int, position: int) -> dict:
    n = nonce("heavy", cat, k)
    return {"spec": f"{cat[1]}{k:04d}", "index": position, "filler": False, "kind": cat,
            "codebase": "task_system" if cat == "HTEST" else "site-packages", "seed": HEAVY_SEED,
            "version": None, "sandbox": th.SANDBOX, "nonce": n, "templates": [cat],
            "turns": [{"template": cat, "family": cat, "target": None, "prompt": f"[run {n}] {th.PROMPTS[cat]}"}],
            "think_s": []}


def build_specs() -> list[dict]:
    base = [s for s in mw.load_sessions(mw.SESSIONS).values() if not s["filler"]]
    base.sort(key=lambda s: s["index"])
    assert len(base) == 128, len(base)
    rows: list[dict] = []
    pos = 0
    for s in base[:P1_LIGHT]:                           # P1: 11's own first 8 specs, then heavy x3 each
        rows.append(light_copy(s, 0, pos))
        pos += 1
    for k in range(P1_HEAVY):
        for cat in ("HTEST", "HSEARCH"):
            rows.append(heavy_spec(cat, k, pos))
            pos += 1
    assert pos == P1[1]
    for rnd in range(1, 5):                             # LPOOL
        for s in base:
            rows.append(light_copy(s, rnd, pos))
            pos += 1
    assert pos == LPOOL[1]
    light_iter = iter([(rnd, s) for rnd in (5, 6) for s in base])
    k = {"HTEST": P1_HEAVY, "HSEARCH": P1_HEAVY}
    for i in range(POOL):                               # HPOOL: L, HT, L, HS, ...
        kind = ("LIGHT", "HTEST", "LIGHT", "HSEARCH")[i % 4]
        if kind == "LIGHT":
            rnd, s = next(light_iter)
            rows.append(light_copy(s, rnd, pos))
        else:
            rows.append(heavy_spec(kind, k[kind], pos))
            k[kind] += 1
        pos += 1
    assert pos == HPOOL[1]
    return rows


# -- conditions (one mu_sweep level each) and cells (one server lifetime each) ---------------------------
def level(n: int, specs: tuple[int, int], model: str, window_s: int | None, vllm: int = 4, agent: int = 7,
          shared: bool = False) -> dict:
    return {"n": n, "specs": list(specs), "fillers": False, "memory": "off", "timestamp": False, "vllm": [],
            "env": {}, "kv_frac": None, "model": model,
            "hw": {"window_s": window_s, "warmup_s": WARMUP_S if window_s else 0, "vllm_cpus": vllm,
                   "agent_cpus": agent, "shared": shared}}


W_P2, W_K = 1800, 1200
CONDITIONS: dict[str, dict] = {
    # Phase 0 / 1: one agent, sessions run to completion
    "hw_smoke32": level(1, (0, 1), M32, None),
    "hw_smoke8": level(1, (0, 1), M8, None),
    "hw_p1a": level(1, P1, M32, None),
    "hw_p1b": level(1, P1, M8, None),
    # Phase 2: N concurrent agents
    **{f"hw_p2a_n{n:02d}": level(n, LPOOL, M32, W_P2) for n in (4, 8, 16)},
    **{f"hw_p2b_n{n:02d}": level(n, HPOOL, M32, W_P2) for n in (4, 8, 16)},
    **{f"hw_p2c_l{n:02d}": level(n, LPOOL, M8, W_P2) for n in (4, 16, 32)},
    **{f"hw_p2c_h{n:02d}": level(n, HPOOL, M8, W_P2) for n in (4, 16, 32)},
    # Phase 3: interventions
    **{f"hw_k1h_k{k}": level(8, HPOOL, M32, W_K, agent=k) for k in (7, 4, 2, 1)},
    **{f"hw_k1l_k{k}": level(8, LPOOL, M32, W_K, agent=k) for k in (7, 4, 2, 1)},
    **{f"hw_k2_v{v}": level(16, LPOOL, M8, W_K, vllm=v) for v in (4, 2, 1)},
    "hw_k3_shared": level(16, HPOOL, M32, W_P2, shared=True),
    **{f"hw_t2_n{n:02d}": level(n, LPOOL, M32, W_P2, vllm=8, agent=15) for n in (1, 8, 24)},
    **{f"hw_k2t_v{v}": level(8, LPOOL, M32, W_K, vllm=v, agent=15) for v in (4, 2)},
    "hw_o1_n16": level(16, LPOOL, M32, W_P2),
    # follow-up: where the EngineCore thread's CPU goes (README §3.3), with its stack sampled every 20 ms
    **{f"hw_s1_n{n:02d}": level(n, LPOOL, M32, W_K) for n in (4, 16)},
    **{f"hw_s2_l{n:02d}": level(n, LPOOL, M8, W_K) for n in (4, 32)},
}

OFFLOAD = ["--kv-offloading-size", "128", "--kv-offloading-backend", "native"]
STACKS = {"HW_STACK_MS": "20"}             # hw_scheduler.py samples the EngineCore thread's stack
CELLS: dict[str, dict] = {
    "p1a": {"model": M32, "tp": 1, "levels": ["hw_smoke32", "hw_p1a"]},
    "p1b": {"model": M8, "tp": 1, "levels": ["hw_smoke8", "hw_p1b"]},
    "p2a": {"model": M32, "tp": 1, "levels": ["hw_p2a_n04", "hw_p2a_n08", "hw_p2a_n16"]},
    "p2b": {"model": M32, "tp": 1, "levels": ["hw_p2b_n04", "hw_p2b_n08", "hw_p2b_n16"]},
    "p2c": {"model": M8, "tp": 1, "levels": ["hw_p2c_l04", "hw_p2c_l16", "hw_p2c_l32",
                                             "hw_p2c_h04", "hw_p2c_h16", "hw_p2c_h32"]},
    "k1h": {"model": M32, "tp": 1, "levels": ["hw_k1h_k7", "hw_k1h_k4", "hw_k1h_k2", "hw_k1h_k1"]},
    "k1l": {"model": M32, "tp": 1, "levels": ["hw_k1l_k7", "hw_k1l_k4", "hw_k1l_k2", "hw_k1l_k1"]},
    "k2": {"model": M8, "tp": 1, "levels": ["hw_k2_v4", "hw_k2_v2", "hw_k2_v1"], "restart": True},
    "k3": {"model": M32, "tp": 1, "levels": ["hw_k3_shared"]},
    "t2": {"model": M32, "tp": 2, "levels": ["hw_t2_n01", "hw_t2_n08", "hw_t2_n24"]},
    "k2t": {"model": M32, "tp": 2, "levels": ["hw_k2t_v4", "hw_k2t_v2"], "restart": True},
    "o1": {"model": M32, "tp": 1, "levels": ["hw_o1_n16"], "vllm_extra": OFFLOAD},
    "s1": {"model": M32, "tp": 1, "levels": ["hw_s1_n04", "hw_s1_n16"], "server_env": STACKS},
    "s2": {"model": M8, "tp": 1, "levels": ["hw_s2_l04", "hw_s2_l32"], "server_env": STACKS},
}


if os.environ.get("HW_TEST_CELLS"):                   # test_hw.py's pipeline test: tiny cells, same code paths
    CONDITIONS.update({"hw_tw_n02": level(2, LPOOL, M32, W_P2), "hw_tw_n03": level(3, HPOOL, M32, W_P2),
                       "hw_tr_v4": level(2, LPOOL, M8, W_K, vllm=4, agent=2),
                       "hw_tr_v2": level(2, LPOOL, M8, W_K, vllm=2, agent=2)})
    CELLS.update({"tw": {"model": M32, "tp": 1, "levels": ["hw_tw_n02", "hw_tw_n03"]},
                  "tr": {"model": M8, "tp": 1, "levels": ["hw_tr_v4", "hw_tr_v2"], "restart": True,
                         "server_env": STACKS}})


def cond(name: str) -> dict:
    c = dict(CONDITIONS[name])
    c["name"] = name
    return c


def server_args(cell: dict, cond_name: str | None = None) -> list[str]:
    """vLLM serve args of a cell: 11's common args with the HW scheduler, plus TP and the cell's extras."""
    args = list(mw.VLLM_COMMON)
    i = args.index("--scheduler-cls")
    args[i + 1] = "hw_scheduler.HWScheduler"
    if cell.get("tp", 1) > 1:
        args += ["--tensor-parallel-size", str(cell["tp"])]
    return args + list(cell.get("vllm_extra", []))


def layout(cell: dict, c: dict, cpus: list[int]) -> dict[str, list[int]]:
    """CPU sets of one level inside the cell's cores (see the module docstring)."""
    hw = c["hw"]
    region = 8 if cell.get("tp", 1) > 1 else 4
    need = region + 1 + hw["agent_cpus"]
    if len(cpus) < region + 1 + 1:
        raise ValueError(f"cell needs at least {region + 2} cores, got {len(cpus)}")
    vllm = cpus[:min(hw["vllm_cpus"], region)]
    sampler = cpus[region:region + 1]
    agent = cpus[region + 1:min(need, len(cpus))]
    if hw["shared"]:
        vllm = agent = cpus[:region] + agent
    return {"vllm": vllm, "sampler": sampler, "agent": agent, "watch": cpus}


def export_conditions(path: Path = CONDITIONS_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CONDITIONS, indent=1), encoding="utf-8")


def check(rows: list[dict]) -> list[str]:
    msgs = []
    assert len(rows) == HPOOL[1], len(rows)
    assert len({r["spec"] for r in rows}) == len(rows), "duplicate spec ids"
    assert [r["index"] for r in rows] == list(range(len(rows)))
    nonces = Counter(r["nonce"] for r in rows)
    dup = [n for n, c in nonces.items() if c > 1]
    assert not dup, f"duplicate nonces {dup[:3]}"
    for r in rows:
        assert r["turns"][0]["prompt"].startswith(f"[run {r['nonce']}] "), r["spec"]
    kinds = Counter(r["kind"] for r in rows[HPOOL[0]:HPOOL[1]])
    msgs.append(f"{len(rows)} specs; P1 {Counter(r['kind'] for r in rows[:P1[1]])}; HPOOL {dict(kinds)}")
    for name, cell in CELLS.items():
        for lv in cell["levels"]:
            c = CONDITIONS[lv]
            assert c["model"] == cell["model"], (name, lv)
            lay = layout(cell, c, list(range(24 if cell.get("tp", 1) > 1 else 12)))
            region = 8 if cell.get("tp", 1) > 1 else 4
            want = c["hw"]["agent_cpus"] + (region if c["hw"]["shared"] else 0)
            assert len(lay["agent"]) == want, (lv, lay)
    msgs.append(f"{len(CONDITIONS)} conditions in {len(CELLS)} cells; layouts ok")
    msgs.append("e.g. " + json.dumps(layout(CELLS["k1h"], CONDITIONS["hw_k1h_k2"], list(range(12)))))
    return msgs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write data/sessions.jsonl and data/conditions.json")
    b.add_argument("--data", default=str(DATA), help="output directory (tests use a temporary one)")
    sub.add_parser("check", help="build in memory and validate")
    c = sub.add_parser("cell", help="one field of a cell, for the pipeline")
    c.add_argument("name", choices=sorted(CELLS))
    c.add_argument("--field", choices=["json", "levels", "model", "tp", "restart", "vllm_args", "server_env"],
                   default="json")
    lv = sub.add_parser("level", help="one field of a level (condition)")
    lv.add_argument("name", choices=sorted(CONDITIONS))
    lv.add_argument("--field", choices=["json", "n", "window_s", "warmup_s", "env"], default="json")
    lay = sub.add_parser("layout", help="CPU sets of a level: vllm sampler agent watch (one line each)")
    lay.add_argument("cell", choices=sorted(CELLS))
    lay.add_argument("level", choices=sorted(CONDITIONS))
    lay.add_argument("cpus", help="the cell's cores, comma-separated")
    args = ap.parse_args()
    if args.cmd == "build":
        rows = build_specs()
        for msg in check(rows):
            print(msg)
        out = Path(args.data)
        out.mkdir(parents=True, exist_ok=True)
        with (out / SESSIONS.name).open("w", encoding="utf-8") as handle:
            for r in rows:
                handle.write(json.dumps(r) + "\n")
        export_conditions(out / CONDITIONS_JSON.name)
        print(f"wrote {out / SESSIONS.name} and {out / CONDITIONS_JSON.name}")
    elif args.cmd == "check":
        for msg in check(build_specs()):
            print(msg)
    elif args.cmd == "cell":
        cell = CELLS[args.name]
        if args.field == "json":
            print(json.dumps(cell))
        elif args.field == "levels":
            print(" ".join(cell["levels"]))
        elif args.field == "vllm_args":
            print(" ".join(shlex.quote(a) for a in server_args(cell)))
        elif args.field == "restart":
            print(1 if cell.get("restart") else 0)
        elif args.field == "server_env":
            print(" ".join(f"{k}={v}" for k, v in cell.get("server_env", {}).items()))
        else:
            print(cell.get(args.field, 1 if args.field == "tp" else ""))
    elif args.cmd == "level":
        c = CONDITIONS[args.name]
        if args.field == "json":
            print(json.dumps(c))
        elif args.field == "n":
            print(c["n"])
        elif args.field == "env":
            for k, v in c["env"].items():
                print(f"{k}={v}")
        else:
            print(c["hw"][args.field] or 0)
    elif args.cmd == "layout":
        cpus = [int(x) for x in args.cpus.split(",") if x]
        lay = layout(CELLS[args.cell], CONDITIONS[args.level], cpus)
        for key in ("vllm", "sampler", "agent", "watch"):
            print(",".join(map(str, lay[key])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
