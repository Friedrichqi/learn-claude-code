#!/usr/bin/env python3
"""Workload, conditions and server / client settings of research/18_harness_wait.

The sessions are topic 11's (research/11_multiuser_kv/mu_workloads.py: one user = a 4-turn s15 session on
one of 10 codebases, think time lognormal with median 30 s, solo), rebuilt from FROZEN seeds: data/seeds/
holds the nine site-packages codebases of /data/fritz/venv and the s15 harness at git 59a24c1 (one of the
ten codebases is the harness itself, which this topic edits). `build` writes data/sessions.jsonl and
compares it with topic 11's manifest (same specs, codebases, templates, think draws and nonces; the M2 / M3
targets follow the seeds' code).

Every cell runs Qwen3-32B (bf16, thinking off) on two H100s (TP=2) with the KV pool pinned to topic 11's
16,137 blocks (258k tokens), the append-only Qwen3 template of topic 16, the status-exporting scheduler
(wt_scheduler) and the status endpoint (wt_server_ext). Arms (client_env reaches the harness):

  none       the harness as shipped
  m_stock    the model gets the `wait` tool and, every round, a status line of stock /metrics fields
  m_share    the same with the share line (the pool, the sessions ahead of it, its own cached prefix)
  h_backoff  the harness holds after tool rounds by the backoff rule on the stock fields (model unaware)
  h_share    the harness holds after tool rounds by the share rule (model unaware)
  sgate      topic 11's server-side session gate: at most 8 sessions active, 5 s idle releases a slot

A cell is a closed loop of N users for WARMUP_S, then a measurement window of WINDOW_S, then everything
stops (wt_sweep.py). Parameters marked `pilot` are set by the pilot and frozen in prereg.md.

    python research/18_harness_wait/wt_workloads.py build          # data/sessions.jsonl (+ meta, diff)
    python research/18_harness_wait/wt_workloads.py conditions     # data/conditions.json (MU_EXTRA_CONDITIONS)
    python research/18_harness_wait/wt_workloads.py show w16_m_stock
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402  research/11_multiuser_kv
import workloads as w10  # noqa: E402  research/10_rope_shift

REPO = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent
DATA = DIR / "data"
SEEDS = DATA / "seeds"
SESSIONS = DATA / "sessions.jsonl"
CONDITIONS_JSON = DATA / "conditions.json"
CELLS = DATA / "cells"
TEMPLATE = REPO / "research" / "16_epic_harness" / "qwen3_append_only.jinja"

POOL_BLOCKS = 16137                         # topic 11's pool on one H200 (258,192 tokens)
WARMUP_S, WINDOW_S = 1200, 5400             # main cells: 20 min warm-up, 90 min measured
PILOT_WARMUP_S, PILOT_WINDOW_S = 900, 1800
SMOKE_WARMUP_S, SMOKE_WINDOW_S = 120, 600

SERVER_ARGS = ["--tensor-parallel-size", "2", "--gpu-memory-utilization", "0.92",
               "--num-gpu-blocks-override", str(POOL_BLOCKS), "--chat-template", str(TEMPLATE),
               "--scheduler-cls", "wt_scheduler.WTScheduler", "--middleware", "wt_server_ext.Middleware"]
SERVER_ENV = {"MU_STRICT": "1", "WT_SNAP_S": "0.2"}
CLIENT_COMMON_ENV = {"S15_HOLD_MAX_S": "600", "S15_WAIT_MAX_S": "300"}
# pilot: the rule parameters; frozen in prereg.md before the main run
RULES = {"S15_BACKOFF_D0": "2", "S15_BACKOFF_DMAX": "30", "S15_BACKOFF_ENTER_KV": "0.92",
         "S15_BACKOFF_EXIT_KV": "0.85", "S15_SHARE_TAU": "0.9", "S15_SHARE_POLL_S": "2"}
ARMS = {
    "none": {"client_env": {}},
    "m_stock": {"client_env": {"S15_WAIT_TOOL": "1", "S15_STATUS_LINE": "1", "S15_STATUS_VIEW": "stock"}},
    "m_share": {"client_env": {"S15_WAIT_TOOL": "1", "S15_STATUS_LINE": "1", "S15_STATUS_VIEW": "share"}},
    "h_backoff": {"client_env": {"S15_HOLD_POLICY": "backoff", "S15_STATUS_VIEW": "stock", "S15_HOLD_PHASES": "mid"}},
    "h_share": {"client_env": {"S15_HOLD_POLICY": "share", "S15_STATUS_VIEW": "share", "S15_HOLD_PHASES": "mid"}},
    "sgate": {"client_env": {}, "env": {"MU_GATE": "8", "MU_GATE_IDLE_S": "5"}},
    # contingency, exploratory: the share rule also before a turn's first call
    "h_share_start": {"client_env": {"S15_HOLD_POLICY": "share", "S15_STATUS_VIEW": "share",
                                     "S15_HOLD_PHASES": "start,mid"}},
}
SMALL_POOL = ["--max-model-len", "16384", "--num-gpu-blocks-override", "1100",
              "--kv-events-config", '{"enable_kv_cache_events": true, "publisher": "zmq", '
                                    '"endpoint": "tcp://*:{kv_events_port}"}']


# the model arms' tool description in the main run: v2, chosen by the rule written before P2b ran (README, S2 pilot):
# fewer wait-only lead calls per 100 work calls, averaged over both model arms (v1 73.9, v2 52.6)
MAIN_WAIT_TEXT = "v2"


def cell(stage: str, n: int, arm: str, warmup_s: int, window_s: int, vllm: list | None = None,
         env: dict | None = None, client_env: dict | None = None, fillers: bool = True,
         natural_pool: bool = False) -> dict:
    spec = ARMS[arm]
    server = list(SERVER_ARGS)
    if natural_pool:                       # no --num-gpu-blocks-override: the pool TP=2 would give
        i = server.index("--num-gpu-blocks-override")
        del server[i:i + 2]
    text = {}
    if arm.startswith("m_"):                 # pilot P2 ran v1 (the default); the main run v2
        text = {"S15_WAIT_TEXT": MAIN_WAIT_TEXT if stage in ("main", "contingency") else "v1"}
    return {"stage": stage, "n": n, "arm": arm, "specs": [0, mw.N_COUNTED], "fillers": fillers,
            "warmup_s": warmup_s, "window_s": window_s,
            "vllm": server + list(vllm or []),
            "env": {**SERVER_ENV, **spec.get("env", {}), **(env or {})},
            "client_env": {**CLIENT_COMMON_ENV, **RULES, **spec["client_env"], **text, **(client_env or {})}}


def conditions() -> dict:
    out = {}
    # S1 smoke: forced contention on a 1,100-block pool (KV events on), the gate at K=2, two arms end to end
    out["s_small"] = cell("smoke", 4, "none", SMOKE_WARMUP_S, SMOKE_WINDOW_S, SMALL_POOL, fillers=False)
    out["s_gate2"] = cell("smoke", 4, "sgate", SMOKE_WARMUP_S, SMOKE_WINDOW_S, SMALL_POOL,
                          env={"MU_GATE": "2"}, fillers=False)
    out["s_mstock"] = cell("smoke", 4, "m_stock", SMOKE_WARMUP_S, SMOKE_WINDOW_S, SMALL_POOL, fillers=False)
    out["s_hshare"] = cell("smoke", 4, "h_share", SMOKE_WARMUP_S, SMOKE_WINDOW_S, SMALL_POOL, fillers=False)
    # natural pool: read the block count TP=2 would give (one user, short)
    out["s_nat"] = cell("smoke", 1, "none", 60, 300, fillers=False, natural_pool=True)
    # S2 pilot
    for n in (8, 16):
        out[f"p{n:02d}_none"] = cell("pilot", n, "none", PILOT_WARMUP_S, PILOT_WINDOW_S)
    for arm in ("m_stock", "m_share", "h_backoff", "h_share"):
        out[f"p16_{arm}"] = cell("pilot", 16, arm, PILOT_WARMUP_S, PILOT_WINDOW_S)
    out["p16_h_share_t75"] = cell("pilot", 16, "h_share", PILOT_WARMUP_S, PILOT_WINDOW_S,
                                  client_env={"S15_SHARE_TAU": "0.75"})
    # P2b (added 2026-10-24 after P2: the model chained 1-second wait-only rounds): the v2 tool description
    for arm in ("m_stock", "m_share"):
        out[f"p16_{arm}_v2"] = cell("pilot", 16, arm, PILOT_WARMUP_S, PILOT_WINDOW_S,
                                    client_env={"S15_WAIT_TEXT": "v2"})
    # S3 main
    for arm in ("none", "m_stock", "m_share", "h_backoff", "h_share", "sgate"):
        for n in (16, 32):
            out[f"w{n:02d}_{arm}"] = cell("main", n, arm, WARMUP_S, WINDOW_S)
    out["w16_none_aa"] = cell("main", 16, "none", WARMUP_S, WINDOW_S)
    for arm in ("none", "m_stock", "m_share"):
        out[f"w08_{arm}"] = cell("main", 8, arm, WARMUP_S, WINDOW_S)
    out["x16_h_share_start"] = cell("contingency", 16, "h_share_start", WARMUP_S, WINDOW_S)
    return out


def write_conditions(path: Path = CONDITIONS_JSON) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(conditions(), indent=1) + "\n", encoding="utf-8")
    return path


def frozen_seed(name: str) -> Path:
    return SEEDS / name


def build() -> list[dict]:
    """topic 11's builder with the frozen seeds in place of the live codebases."""
    meta = json.loads((SEEDS / "SEEDS.json").read_text(encoding="utf-8"))
    saved = (w10.seed_dir, w10.version_of)
    w10.seed_dir = frozen_seed
    w10.version_of = lambda name: meta[name]["version"]
    try:
        return mw.build()
    finally:
        w10.seed_dir, w10.version_of = saved


def compare(rows: list[dict], ref_path: Path = mw.SESSIONS) -> dict:
    ref = {r["spec"]: r for r in (json.loads(line) for line in ref_path.open(encoding="utf-8"))}
    same_keys = ("codebase", "templates", "think_s", "nonce", "filler", "index")
    out = {"specs": len(rows), "in_topic11": sum(r["spec"] in ref for r in rows), "same": {}, "targets_differ": 0,
           "prompts_differ": 0, "versions": {}}
    for key in same_keys:
        out["same"][key] = sum(r["spec"] in ref and r[key] == ref[r["spec"]][key] for r in rows)
    for r in rows:
        o = ref.get(r["spec"])
        if o is None:
            continue
        out["targets_differ"] += any(a.get("target") != b.get("target") for a, b in zip(r["turns"], o["turns"]))
        out["prompts_differ"] += any(a["prompt"] != b["prompt"] for a, b in zip(r["turns"], o["turns"]))
        out["versions"].setdefault(r["codebase"], [o.get("version"), r.get("version")])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="write data/sessions.jsonl, sessions_meta.json and manifest_diff.json")
    sub.add_parser("conditions", help="write data/conditions.json")
    s = sub.add_parser("show", help="print one condition")
    s.add_argument("name")
    args = ap.parse_args()
    if args.cmd == "conditions":
        print(write_conditions())
        return
    if args.cmd == "show":
        print(json.dumps(conditions()[args.name], indent=1))
        return
    rows = build()
    summary = mw.check(rows)
    SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    with SESSIONS.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    diff = compare(rows)
    (DATA / "sessions_meta.json").write_text(json.dumps(
        {**summary, "model": mw.MODEL, "max_model_len": mw.MAX_MODEL_LEN, "client_common": mw.CLIENT_COMMON,
         "budget": mw.BUDGET, "seeds": "data/seeds (SEEDS.json)", "source": "research/11_multiuser_kv/mu_workloads.py build()",
         "diff_vs_topic11": diff}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"specs": summary["specs"], "fillers": summary["fillers"], "think_s": summary["think_s"],
                      "diff_vs_topic11": diff}, indent=1))


if __name__ == "__main__":
    main()
