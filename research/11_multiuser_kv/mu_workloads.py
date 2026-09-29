#!/usr/bin/env python3
"""Session specs, think-time draws and the condition table of the 11_multiuser_kv experiment.

A session is one simulated user: four turns on one codebase, run solo by the s15 harness, with a
think pause before each follow-up turn. The turn texts are 10_rope_shift's templates (imported from
research/10_rope_shift/workloads.py, so codebases, entry points and M2/M3 targets are identical):
turns 1-2 are two different EXPLAIN templates (E1 inventory, E2 trace, E3 orientation), turns 3-4
two different MODIFY templates (M1 BUILD_TAG + changelog, M2 rename, M3 docstrings). Turn 1 carries
the session nonce, turns 2-4 read as follow-ups. The sandbox persists across the four turns.

Balance: specs come in blocks of 8. Block b uses 8 of the 10 codebases (it drops c[2b mod 10] and
c[2b+1 mod 10]), and spec k gets E-pair EP[k mod 6] and M-pair MP[(5k+3) mod 6], so any 24
consecutive specs hold each ordered E-pair and M-pair 4 times and any 40 hold each codebase 4 times.
A condition with M sessions runs specs 0..M-1: conditions are nested and paired on task mix and
think draws. Think time per gap: min(240, lognormal(ln 30, 0.8)) s, seeded by the spec id.

Fillers (F000..) are extra sessions of the same kind that keep N users active before the first and
after the last counted session of a cell; they are recorded but not counted.

    python research/11_multiuser_kv/mu_workloads.py build      # writes data/multiuser_live/sessions.jsonl
    python research/11_multiuser_kv/mu_workloads.py check      # validate prompts, balance, think quantiles
    python research/11_multiuser_kv/mu_workloads.py cond n16 --field vllm   # one condition, for the pipeline
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shlex
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import workloads as w10  # noqa: E402  research/10_rope_shift/workloads.py

REPO = Path(__file__).resolve().parents[2]
DIR = Path(__file__).resolve().parent
DATA = DIR / "data" / "multiuser_live"
SESSIONS = DATA / "sessions.jsonl"

N_COUNTED = 128
N_FILLER = 32
THINK_MEDIAN_S, THINK_SIGMA, THINK_CAP_S = 30.0, 0.8, 240.0
E_PAIRS = [("E1", "E2"), ("E1", "E3"), ("E2", "E1"), ("E2", "E3"), ("E3", "E1"), ("E3", "E2")]
M_PAIRS = [("M1", "M2"), ("M1", "M3"), ("M2", "M1"), ("M2", "M3"), ("M3", "M1"), ("M3", "M2")]

# -- what every session runs with (profile_run flags beyond the per-session ones) ----------------------
MODEL = "Qwen/Qwen3-32B"
MAX_MODEL_LEN = 40960
CLIENT_COMMON = ["--no-reads-log", "--client-max-retries", "0", "--no-retry-429", "--server-info",
                 "--no-snip", "--tool-result-budget", "64000", "--reactive-compact-limit", "3",
                 "--quiet-seconds", "5"]
BUDGET = {"lead_cap_turn": 100, "lead_cap_session": 300, "deadline_s": 14400, "call_timeout_s": 3600}
VLLM_COMMON = ["--max-model-len", str(MAX_MODEL_LEN), "--enable-prefix-caching", "--enable-prompt-tokens-details",
               "--enable-auto-tool-choice", "--tool-call-parser", "hermes", "--reasoning-parser", "qwen3",
               "--default-chat-template-kwargs", '{"enable_thinking": false}',
               "--scheduler-cls", "mu_scheduler.MUScheduler", "--kv-cache-metrics",
               "--kv-cache-metrics-sample", "0.05", "--uvicorn-log-level", "warning"]
# The harness's context limit equals the model's INPUT window, in the harness's own units (characters
# of JSON-serialized messages): vLLM reserves the lead's max_tokens (8000) of the window for output, and
# the system prompt + tool schemas (~1,795 tokens) are not in the harness's count. Calibrated on 15,403
# lead calls of 10_rope_shift (tokens = 1795 + chars / r, r = 4.61 chars/token at the median and 4.33 at
# the 5th percentile for contexts > 30k chars); the 5th percentile keeps 95% of prompts inside the window
# while using 94-100% of it. The pilot of 2026-09-26 showed why the limit cannot stay at 512k: the
# harness's reactive compaction keeps the last 5 messages verbatim, so after one large file read it
# overflows again and the turn ends in an error (2 of 3 overflowing turns in pilot8).
LEAD_MAX_TOKENS = 8000
PROMPT_OVERHEAD_TOKENS = 1795
CHARS_PER_TOKEN_P5 = 4.33


def context_limit(max_model_len: int) -> int:
    return int((max_model_len - LEAD_MAX_TOKENS - PROMPT_OVERHEAD_TOKENS) * CHARS_PER_TOKEN_P5 // 1000) * 1000


# estimated session length (s) per N, for truncating the warm-up fillers; replaced after the pilot
SESSION_EST_S = {1: 900, 4: 1000, 8: 1100, 12: 2400, 16: 1400, 32: 2200}

# cond -> users, counted spec range, client and server options.
#   memory: harness memory on/off; timestamp: keep the per-second 'Current time' line;
#   kv_frac: shrink the KV pool to this fraction of the stock pool (--num-gpu-blocks-override);
#   vllm: extra server args; env: extra server environment (mu_scheduler policy knobs, MU_PIN...);
#   fillers: keep N users active around the counted sessions
CONDITIONS = {
    "n01a": {"stage": 1, "n": 1, "specs": [0, 12], "fillers": False},
    "n01b": {"stage": 1, "n": 1, "specs": [12, 24], "fillers": False},
    "n04": {"stage": 1, "n": 4, "specs": [0, 40]},
    "n08": {"stage": 1, "n": 8, "specs": [0, 56]},
    "n16": {"stage": 1, "n": 16, "specs": [0, 80]},
    "n32": {"stage": 1, "n": 32, "specs": [0, 96]},
    # Stage 2 was planned at N=16, but N=16 turned out to be deep in the thrashing regime (73% of prompt
    # tokens re-prefilled after eviction, sessions ~6x slower than at N=8), where each contrast would be
    # drowned and would take ~7 GPU-h. Moved on 2026-09-27 to N=8, just below the knee, where the halved
    # pool is the causal test of the knee and memory / timestamp show what they cost while caching works.
    # The N=16 versions are kept for the record and were never run.
    "n16kvh": {"stage": 2, "n": 16, "specs": [0, 64], "kv_frac": 0.5, "dropped": True},
    "n16mem": {"stage": 2, "n": 16, "specs": [0, 64], "memory": "on", "dropped": True},
    "n16ts": {"stage": 2, "n": 16, "specs": [0, 40], "memory": "on", "timestamp": True, "dropped": True},
    "n12": {"stage": 1, "n": 12, "specs": [0, 36]},
    "n08kvh": {"stage": 2, "n": 8, "specs": [0, 24], "kv_frac": 0.5},
    "n08mem": {"stage": 2, "n": 8, "specs": [0, 40], "memory": "on"},
    "n08ts": {"stage": 2, "n": 8, "specs": [0, 40], "memory": "on", "timestamp": True},
    # live policy check (stage 3): the server-side session gate (at most 8 sessions active, the pool's
    # worth of working sets; mu_scheduler MU_GATE), on the first 48 specs of n16 / n32 (paired)
    "s3gate16": {"stage": 3, "n": 16, "specs": [0, 48], "env": {"MU_GATE": "8", "MU_GATE_IDLE_S": "5"}},
    "s3gate32": {"stage": 3, "n": 32, "specs": [0, 48], "env": {"MU_GATE": "8", "MU_GATE_IDLE_S": "5"}},
    # pilot and forced-contention smoke (P0)
    "pilot1": {"stage": 0, "n": 1, "specs": [0, 3], "fillers": False},
    "pilot8": {"stage": 0, "n": 8, "specs": [0, 16]},
    # "tcp://*:<port>" makes vLLM's publisher BIND (a host address would make it connect); the first smoke
    # used 127.0.0.1 and received no events. smoke2 also shrinks the pool to force preemptions.
    "smoke": {"stage": 0, "n": 4, "specs": [0, 4], "fillers": False, "short": True,
              "vllm": ["--max-model-len", "16384", "--num-gpu-blocks-override", "2600",
                       "--kv-events-config", '{"enable_kv_cache_events": true, "publisher": "zmq", '
                                             '"endpoint": "tcp://*:{kv_events_port}"}']},
    "smoke2": {"stage": 0, "n": 4, "specs": [4, 8], "fillers": False, "short": True,
               "vllm": ["--max-model-len", "16384", "--num-gpu-blocks-override", "1100",
                        "--kv-events-config", '{"enable_kv_cache_events": true, "publisher": "zmq", '
                                              '"endpoint": "tcp://*:{kv_events_port}"}']},
}

# Trace-replay policy study (mu_replay.py): r_<source cell>_<policy>. Each replays the recorded
# sessions of the source cell (exact prompt ids, recorded output lengths and gaps) under one policy.
REPLAY_POLICIES = {
    "default": {}, "default2": {},                                       # stock, twice: the noise floor
    "wm05": {"vllm": ["--watermark", "0.05"]},
    "wm10": {"vllm": ["--watermark", "0.10"]},
    "prio": {"vllm": ["--scheduling-policy", "priority"], "priority": True},   # program-level FCFS
    "pin10t": {"env": {"MU_PIN": "1", "MU_PIN_WHEN": "toolcall", "MU_PIN_TTL": "10"}},
    "pin60a": {"env": {"MU_PIN": "1", "MU_PIN_WHEN": "always", "MU_PIN_TTL": "60"}},
    "pin240a": {"env": {"MU_PIN": "1", "MU_PIN_WHEN": "always", "MU_PIN_TTL": "240"}},
    "priopin60a": {"vllm": ["--scheduling-policy", "priority"], "priority": True,
                   "env": {"MU_PIN": "1", "MU_PIN_WHEN": "always", "MU_PIN_TTL": "60"}},
    "gate": {"turn_gate_frac": 0.5},                                     # at most N/2 sessions mid-turn
    "mnbt2k": {"vllm": ["--max-num-batched-tokens", "2048"]},            # smaller prefill chunks: less interference
    # server-side session gate (mu_scheduler MU_GATE): at most K sessions active, the rest wait queued
    "sgate8": {"env": {"MU_GATE": "8", "MU_GATE_IDLE_S": "5"}},
    "sgate6": {"env": {"MU_GATE": "6", "MU_GATE_IDLE_S": "5"}},
    "off128": {"vllm": ["--kv-offloading-size", "128", "--kv-offloading-backend", "native"]},
    "think05": {"think_scale": 0.5},
    "think2": {"think_scale": 2.0},
}
# replay length: the thrashing cells would take ~4 h each in full; 100 min of closed loop, measured after
# 10 min, compares throughput and latency (pilot8 replays run whole: the observer-overhead A/B)
REPLAY_WINDOW_S = {"n16": 6000, "n32": 6000}
REPLAY_WARMUP_S = 600
# "stock" drops --scheduler-cls (the stock AsyncScheduler): with default, the observer-overhead A/B
REPLAY_POLICIES["stock"] = {"stock_scheduler": True}
REPLAY_SOURCES = {"n16": list(REPLAY_POLICIES), "n32": list(REPLAY_POLICIES),
                  "pilot8": ["default", "default2", "stock"]}
for _src, _pols in REPLAY_SOURCES.items():
    for _pol in _pols:
        _spec = REPLAY_POLICIES[_pol]
        CONDITIONS[f"r_{_src}_{_pol}"] = {"stage": 3, "n": CONDITIONS[_src]["n"], "specs": CONDITIONS[_src]["specs"],
                                          "fillers": True, "replay": {"src": _src, "policy": _pol,
                                                                      "window_s": REPLAY_WINDOW_S.get(_src, 0), **_spec},
                                          "vllm": _spec.get("vllm", []), "env": _spec.get("env", {}),
                                          "stock_scheduler": bool(_spec.get("stock_scheduler"))}


def cond(name: str) -> dict:
    spec = dict(CONDITIONS[name])
    spec.setdefault("memory", "off")
    spec.setdefault("timestamp", False)
    spec.setdefault("fillers", spec["n"] > 1)
    spec.setdefault("vllm", [])
    spec.setdefault("env", {})
    spec.setdefault("kv_frac", None)
    spec["name"] = name
    return spec


def model_len(c: dict) -> int:
    args = list(VLLM_COMMON) + list(c["vllm"])
    return int(args[len(args) - 1 - args[::-1].index("--max-model-len") + 1])


def client_flags(c: dict) -> list[str]:
    """profile_run flags a session of condition `c` runs with (on top of label/prompts/think/trace)."""
    flags = list(CLIENT_COMMON) + ["--memory", c["memory"], "--context-limit", str(context_limit(model_len(c)))]
    if not c["timestamp"]:
        flags.append("--no-timestamp")
    return flags


def vllm_args(c: dict) -> list[str]:
    """Server args of condition `c`. A later --max-model-len overrides the common one (argparse)."""
    args = list(VLLM_COMMON)
    if c.get("stock_scheduler"):
        i = args.index("--scheduler-cls")
        del args[i:i + 2]
    return args + list(c["vllm"])


def think_draws(spec_id: str) -> list[float]:
    rng = random.Random(f"11mu-think-{spec_id}")
    return [round(min(THINK_CAP_S, rng.lognormvariate(math.log(THINK_MEDIAN_S), THINK_SIGMA)), 1)
            for _ in range(3)]


def follow_up(prompt: str) -> str:
    """Turns 2-4: the template prompt without the run nonce, so it reads as a follow-up request."""
    assert prompt.startswith("[run "), prompt[:40]
    return prompt.split("] ", 1)[1]


def codebase_order(block: int, seed: str) -> list[str]:
    names = list(w10.CODEBASES)
    drop = {names[(2 * block) % len(names)], names[(2 * block + 1) % len(names)]}
    keep = [n for n in names if n not in drop]
    random.Random(f"{seed}-block-{block}").shuffle(keep)
    return keep


def build() -> list[dict]:
    src08 = w10.items_08()
    targets = {}
    for cb in w10.CODEBASES:
        root = w10.seed_dir(cb)
        assert root.is_dir(), f"missing seed {root}"
        targets[cb] = {"M2": w10.rename_candidates(root), "M3": w10.docstring_candidates(root)}
        assert targets[cb]["M2"] and targets[cb]["M3"], cb
    rows = []
    for kind, count, prefix in (("counted", N_COUNTED, "S"), ("filler", N_FILLER, "F")):
        seed = f"11mu-{kind}"
        for k in range(count):
            block, pos = divmod(k, 8)
            cb = codebase_order(block + (0 if kind == "counted" else 7), seed)[pos]
            spec_id = f"{prefix}{k:03d}"
            nonce = hashlib.sha1(f"11mu-{spec_id}".encode()).hexdigest()[:8]
            templates = list(E_PAIRS[k % 6]) + list(M_PAIRS[(5 * k + 3) % 6])
            turns = []
            for i, t in enumerate(templates):
                target = None
                if t in ("M2", "M3"):
                    pool = targets[cb][t]
                    target = pool[(k // 10) % len(pool)]
                prompt = w10.build_prompt(t, cb, nonce, target, src08)
                turns.append({"template": t, "family": w10.FAMILY[t], "target": target,
                              "prompt": prompt if i == 0 else follow_up(prompt)})
            rows.append({"spec": spec_id, "index": k, "filler": kind == "filler", "codebase": cb,
                         "seed": str(w10.seed_dir(cb)), "version": w10.version_of(cb),
                         "sandbox": f"{w10.SANDBOX_ROOT}/{cb}", "nonce": nonce,
                         "templates": templates, "turns": turns, "think_s": think_draws(spec_id)})
    return rows


def check(rows: list[dict]) -> dict:
    counted = [r for r in rows if not r["filler"]]
    assert len({r["spec"] for r in rows}) == len(rows), "duplicate spec ids"
    assert len({r["nonce"] for r in rows}) == len(rows), "duplicate nonces"
    for r in rows:
        assert len(r["turns"]) == 4 and len(r["think_s"]) == 3, r["spec"]
        assert r["turns"][0]["prompt"].count("[run ") == 1, r["spec"]
        assert all("[run " not in t["prompt"] for t in r["turns"][1:]), r["spec"]
        for t in r["turns"]:
            assert not w10.FORBIDDEN.search(t["prompt"].replace(w10.SOLO, "")), (r["spec"], t["template"])
            assert len(t["prompt"]) < 1200, r["spec"]
        assert [t["family"] for t in r["turns"]] == ["EXPLAIN", "EXPLAIN", "MODIFY", "MODIFY"], r["spec"]
    for lo in range(0, len(counted) - 23, 8):
        window = counted[lo:lo + 24]
        assert set(Counter(tuple(r["templates"][:2]) for r in window).values()) == {4}, lo
        assert set(Counter(tuple(r["templates"][2:]) for r in window).values()) == {4}, lo
    for lo in range(0, len(counted) - 39, 40):
        assert set(Counter(r["codebase"] for r in counted[lo:lo + 40]).values()) == {4}, lo
    thinks = sorted(t for r in counted for t in r["think_s"])
    q = lambda p: thinks[int(p * (len(thinks) - 1))]
    summary = {"specs": len(counted), "fillers": len(rows) - len(counted),
               "think_s": {"p10": q(0.1), "p50": q(0.5), "p90": q(0.9), "max": thinks[-1]},
               "per_codebase": dict(Counter(r["codebase"] for r in counted)),
               "conditions": {name: {"n": c["n"], "specs": c["specs"]} for name, c in CONDITIONS.items()}}
    assert 20 <= summary["think_s"]["p50"] <= 40 and summary["think_s"]["max"] <= THINK_CAP_S, summary["think_s"]
    for name, c in CONDITIONS.items():
        assert 0 <= c["specs"][0] < c["specs"][1] <= len(counted), name
    return summary


def load_sessions(path: Path = SESSIONS) -> dict[str, dict]:
    return {r["spec"]: r for r in (json.loads(line) for line in path.open(encoding="utf-8"))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write data/multiuser_live/sessions.jsonl and sessions_meta.json")
    b.add_argument("--out", default=str(SESSIONS))
    sub.add_parser("check", help="build in memory, validate, print a summary and one example")
    c = sub.add_parser("cond", help="print one condition (json), or one field of it for the pipeline")
    c.add_argument("name", choices=sorted(CONDITIONS))
    c.add_argument("--field", choices=["json", "n", "vllm", "client", "env", "kv_frac"], default="json")
    args = ap.parse_args()
    if args.cmd == "cond":
        spec = cond(args.name)
        if args.field == "json":
            print(json.dumps(spec))
        elif args.field == "n":
            print(spec["n"])
        elif args.field == "vllm":
            print(" ".join(shlex.quote(a) for a in vllm_args(spec)))
        elif args.field == "env":
            for key, value in spec["env"].items():
                print(f"{key}={value}")
        elif args.field == "kv_frac":
            print(spec["kv_frac"] or "")
        else:
            print(" ".join(shlex.quote(a) for a in client_flags(spec)))
        return
    rows = build()
    summary = check(rows)
    if args.cmd == "check":
        print(json.dumps(summary, indent=2))
        example = rows[0]
        print(f"\n[{example['spec']}] {example['codebase']} think={example['think_s']}")
        for t in example["turns"]:
            print(f"  {t['template']}: {t['prompt']}")
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out.parent / "sessions_meta.json").write_text(json.dumps(
        {**summary, "model": MODEL, "max_model_len": MAX_MODEL_LEN, "client_common": CLIENT_COMMON,
         "budget": BUDGET, "vllm_common": VLLM_COMMON, "session_est_s": SESSION_EST_S,
         "source": "research/10_rope_shift/workloads.py templates"}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
