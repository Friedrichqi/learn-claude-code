#!/usr/bin/env python3
"""Workload, conditions and server / client settings of research/19_efficiency_signals.

The sessions are topic 18's frozen manifest (research/18_harness_wait/data/sessions.jsonl: topic 11's 160 specs,
4-turn s15 sessions E, E, M, M on 10 codebases, think pauses with a median of 30 s), so the task mix is topic 18's.

Every cell serves MiniMax-M2.7 with vLLM 0.31.0 on GPUs 0-3:
  * FP8 weights, interleaved thinking kept in history; TP=4 as wt_pipeline's group Q;
  * the KV pool pinned to topic 11's 16,137 blocks (258,192 tokens; the natural pool is 280,112);
  * the append-only template of this folder;
  * the status-exporting scheduler (wt_scheduler) and the status endpoint (wt_server_ext).

Arms (client_env reaches the harness; s15_integrated_harness/hold_runtime.py):

  none     the harness as shipped
  sgate    topic 11's server-side session gate: at most K sessions active, 5 s idle releases a slot (K from P1)
  m_wait   the model gets `wait` (text v3), the system paragraph about the shared server (wait version) and,
           every round, topic 18's [server] share line
  m_tools  the model gets wait, wait_for_capacity and server_status, the paragraph's tools version and, every
           round, a [cost] receipt of its own last step; the server status is never pushed, only pulled

Every arm runs the same harness budgets:
  * the lead's output budget 16,000 tokens, escalated to 32,000;
  * the compaction summary's budget 8,000;
  * a context limit of 129,000 characters (ef_calibrate.py: topic 18's history budget of about 31k tokens in a
    65,536-token window that reserves the escalated budget).

A cell is wt_sweep's closed loop: N users for the warm-up, then the measurement window, then everything stops.
Main cells set `cycle_specs` (prereg amendment 1): once the manifest's 128 counted specs are handed out, the queue
starts them again in the same order (labels ...-S000-p2), because MiniMax outran the manifest in w16_sgate's first
attempt.

    python research/19_efficiency_signals/ef_workloads.py conditions     # data/conditions.json
    python research/19_efficiency_signals/ef_workloads.py show w16_m_tools
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402  research/11_multiuser_kv
import wt_workloads as ww  # noqa: E402  research/18_harness_wait

DIR = Path(__file__).resolve().parent
DATA = DIR / "data"
CONDITIONS_JSON = DATA / "conditions.json"
CELLS = DATA / "cells"
SESSIONS = ww.SESSIONS                           # topic 18's frozen manifest
TEMPLATE = DIR / "minimax_m2_append_only.jinja"

MODEL = "MiniMaxAI/MiniMax-M2.7"
MAX_MODEL_LEN = 65536
CONTEXT_LIMIT = 129000                           # ef_calibrate.py (data/calibration.json)
POOL_BLOCKS = ww.POOL_BLOCKS                     # 16,137 (258,192 tokens)
TOOL_CALL_TOKEN = 200052                         # <minimax:tool_call>
WARMUP_S, WINDOW_S = ww.WARMUP_S, ww.WINDOW_S    # 20 min + 90 min
PILOT_WARMUP_S, PILOT_WINDOW_S = ww.PILOT_WARMUP_S, ww.PILOT_WINDOW_S
SMOKE_WARMUP_S, SMOKE_WINDOW_S = ww.SMOKE_WARMUP_S, ww.SMOKE_WINDOW_S
OVERHEAD_S = 1200                                # the GPU-hour estimate's allowance for startup (~240 s) and teardown

# Set from pilot P1 by the rules in the plan, before the main run (recorded in README "Changes"):
GATE_K = 8             # clip(round(8 * 23,322.4 / mean lead prompt tokens of p08_none), 4, 12); 23,322.4 = w08_none
                       # P1 (2026-10-26): p08_none's lead calls averaged 22,748 prompt tokens -> round(8.20) = 8
N_LIGHT = 8            # 8 if p08_none's hit rate >= 80 % and evicted recompute <= 10 % of GPU time, else 4
                       # P1: hit rate 85.1 %, evicted recompute 1.6 % of GPU time -> 8
# n -> seconds, the warm-up fillers' truncation. P1 measured complete sessions' mean wall time: 316 s at N=8, 713 s
# at N=16; N=4 and N=32 scale those by topic 11's ratios (1,000 / 1,100 and 2,200 / 1,400).
SESSION_EST_S = {4: 287, 8: 316, 16: 713, 32: 1121}

SERVER_ARGS = ["--max-model-len", str(MAX_MODEL_LEN), "--trust-remote-code",
               "--tool-call-parser", "minimax_m2", "--reasoning-parser", "minimax_m2_append_think",
               "--default-chat-template-kwargs", "{}",                    # topic 11's Qwen kwargs do not apply
               "--tensor-parallel-size", "4", "--gpu-memory-utilization", "0.92",
               # vLLM's default (1,024) makes its memory profile reserve logits for 1,024 sequences over the 200k
               # vocabulary (~2.4 GB per GPU): smoke s_m_tools found a natural pool of 15,174 blocks, below the pin.
               # At most N <= 32 sessions run at once, so 64 never binds.
               "--max-num-seqs", "64",
               "--num-gpu-blocks-override", str(POOL_BLOCKS), "--chat-template", str(TEMPLATE),
               "--scheduler-cls", "wt_scheduler.WTScheduler", "--middleware", "wt_server_ext.Middleware"]
SERVER_ENV = {"MU_STRICT": "1", "WT_SNAP_S": "0.2", "MU_TOOLCALL_TOKEN": str(TOOL_CALL_TOKEN),
              "SAFETENSORS_FAST_GPU": "1", "HF_HOME": "/data/fritz/hf"}
CLIENT_COMMON_ENV = {"S15_HOLD_MAX_S": "600", "S15_WAIT_MAX_S": "300", "S15_YIELD_MAX_S": "1800",
                     "S15_YIELD_DEFAULT_S": "600", "S15_SHARE_TAU": "0.9", "S15_SHARE_POLL_S": "2",
                     "S15_MAX_TOKENS": "16000", "S15_MAX_TOKENS_ESCALATED": "32000", "S15_SUMMARY_MAX_TOKENS": "8000"}
ARMS = {
    "none": {"client_env": {}},
    "sgate": {"client_env": {}, "env": {"MU_GATE_IDLE_S": "5"}},
    "m_wait": {"client_env": {"S15_TOOLS": "wait", "S15_WAIT_TEXT": "v3", "S15_SERVER_SECTION": "wait",
                              "S15_STATUS_LINE": "1", "S15_STATUS_VIEW": "share"}},
    "m_tools": {"client_env": {"S15_TOOLS": "wait,wait_for_capacity,server_status", "S15_WAIT_TEXT": "v3",
                               "S15_SERVER_SECTION": "tools", "S15_RECEIPT": "1", "S15_STATUS_VIEW": "share"}},
}
# S1 smoke: forced contention on a 2,200-block pool (35,200 tokens) in a 32,768-token window. The budgets
# shrink with the window: output 8,000 escalated to 16,000, and the context limit by ef_calibrate's rule,
# (32,768 - 16,000 - 3,198) x 4.276 = 58,000 characters.
SMALL_POOL = ["--max-model-len", "32768", "--num-gpu-blocks-override", "2200",
              "--kv-events-config", '{"enable_kv_cache_events": true, "publisher": "zmq", '
                                    '"endpoint": "tcp://*:{kv_events_port}"}']
SMALL_CLIENT = {"S15_MAX_TOKENS": "8000", "S15_MAX_TOKENS_ESCALATED": "16000", "S15_SUMMARY_MAX_TOKENS": "4000"}
SMALL_CONTEXT_LIMIT = 58000


def cell(stage: str, n: int, arm: str, warmup_s: int, window_s: int, vllm: list | None = None,
         env: dict | None = None, client_env: dict | None = None, fillers: bool = True,
         context_limit: int = CONTEXT_LIMIT, cycle: bool = False) -> dict:
    spec = ARMS[arm]
    server_env = {**SERVER_ENV, **spec.get("env", {}), **(env or {})}
    out = {"stage": stage, "n": n, "arm": arm, "model": MODEL, "specs": [0, mw.N_COUNTED], "fillers": fillers,
           "warmup_s": warmup_s, "window_s": window_s, "overhead_s": OVERHEAD_S, "topic": "19_efficiency_signals",
           "vllm": list(SERVER_ARGS) + list(vllm or []),
           "env": server_env,
           "client_env": {**CLIENT_COMMON_ENV, **spec["client_env"], **(client_env or {})},
           "client_extra": ["--context-limit", str(context_limit)]}
    if cycle:
        out["cycle_specs"] = True                         # wt_sweep: pass 2, 3, ... of the counted specs
    if arm == "sgate":
        if "MU_GATE" not in server_env:
            server_env["MU_GATE"] = str(GATE_K or 8)
            out["gate_k_pending"] = GATE_K is None        # ef_pipeline refuses to run it until K is set
    if SESSION_EST_S.get(n):
        out["session_est_s"] = SESSION_EST_S[n]
    return out


def conditions() -> dict:
    out = {}
    small = {"vllm": SMALL_POOL, "client_env": SMALL_CLIENT, "context_limit": SMALL_CONTEXT_LIMIT, "fillers": False}
    # S1 smoke: N=4 on the small pool; the gate at K=2
    for arm in ("none", "m_wait", "m_tools"):
        out[f"s_{arm}"] = cell("smoke", 4, arm, SMOKE_WARMUP_S, SMOKE_WINDOW_S, **small)
    out["s_gate2"] = cell("smoke", 4, "sgate", SMOKE_WARMUP_S, SMOKE_WINDOW_S, env={"MU_GATE": "2"}, **small)
    # S2 pilot (P1 regime and calibration, P2 m_wait, P3 m_tools, P4 the gate at the K of the rule)
    for n in (8, 16):
        out[f"p{n:02d}_none"] = cell("pilot", n, "none", PILOT_WARMUP_S, PILOT_WINDOW_S)
    for arm in ("m_wait", "m_tools", "sgate"):
        out[f"p16_{arm}"] = cell("pilot", 16, arm, PILOT_WARMUP_S, PILOT_WINDOW_S)
    # P2b, only if P2 trips the plan's revision rule: the v4 text and its paragraph (written before P2 ran)
    out["p16_m_wait_v4"] = cell("pilot", 16, "m_wait", PILOT_WARMUP_S, PILOT_WINDOW_S,
                                client_env={"S15_WAIT_TEXT": "v4", "S15_SERVER_SECTION": "wait_v2"})
    # S3 main: N=16 and N=32 x four arms; the light load (N_LIGHT, 8 or 4) x {none, m_tools}
    for n in (16, 32):
        for arm in ("none", "sgate", "m_wait", "m_tools"):
            out[f"w{n:02d}_{arm}"] = cell("main", n, arm, WARMUP_S, WINDOW_S, cycle=True)
    for n in (4, 8):
        for arm in ("none", "m_tools"):
            out[f"w{n:02d}_{arm}"] = cell("main", n, arm, WARMUP_S, WINDOW_S, cycle=True)
    return out


def write_conditions(path: Path = CONDITIONS_JSON) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(conditions(), indent=1) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("conditions", help="write data/conditions.json")
    s = sub.add_parser("show", help="print one condition")
    s.add_argument("name")
    args = ap.parse_args()
    if args.cmd == "conditions":
        print(write_conditions())
        return
    print(json.dumps(conditions()[args.name], indent=1))


if __name__ == "__main__":
    main()
