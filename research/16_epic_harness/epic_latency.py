#!/usr/bin/env python3
"""E-C: what each arm's lead calls cost in time on one H100, and the job completion time it implies.

The live runs (E-B) compute every token after the exact prefix and override the reused ones (an exact
emulation of EPIC's outputs), so their wall clock says nothing about speed. This script times, for a
stratified sample of the live runs' lead calls, the request a real implementation of each arm would
run, on a CUDA-graph engine (no hooks) with the store on the GPU -- 10_rope_shift/latency.py's
"shape-equivalent" method, extended to epic64 and to prefix caching served by vLLM itself:

  recompute  the whole prompt computed (no cache)
  prefix     vLLM's prefix cache holds the call's exact prefix E (populated by a warm-up request with
             the same salt); the rest computed -- what the live `prefix` arm does
  shift      as prefix, plus the S reused rows loaded and re-rotated by the connector right after the
  epicK      prefix (from the GPU pool); the C = P - E - S computed tokens follow them. A computed token
             therefore attends to the whole context: an upper bound on its attention work (at most 2x)

Each request is timed end to end (llm.generate, max_tokens=1) on an otherwise idle engine, REPS times in
rotating arm order; the per-call value is the median. A TTFT model per arm family,
TTFT = b0 + b1*C + b2*C*P/1e3 + b3*S, is fitted on 2/3 of the calls and validated on the rest (MdAPE).
TPOT is measured at contexts 2k..30k with 1 and 2 sequences. `jct` then prices every live run:
JCT = sum over lead calls (planning + TTFT_model + n_out * TPOT(context)) + tool and harness time.

    python research/16_epic_harness/epic_latency.py run --live data/live/main [--calls 600]
    python research/16_epic_harness/epic_latency.py report --live data/live/main
    python research/16_epic_harness/epic_latency.py jct --live data/live/main [--replay live/main ...]
    python research/16_epic_harness/epic_latency.py validate --live data/live/main

`jct` prices two things with the fitted models: every live run as it ran (its own calls and plans; prefix
arms from vLLM's cache hits), and -- paired, on identical trajectories -- the E-A replays of recorded prefix
runs under every arm. Tool and harness time is the recorded gaps between a run's lead calls (independent
of the server's speed); decode is the reply length times TPOT at the call's context (one sequence).
`validate` prices every lead call of the E-0 runs on vanilla vLLM (prefix cache, CUDA graphs, two sessions)
the same way and compares with the measured call times.
"""
from __future__ import annotations

import argparse
import json
import lzma
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402

ARMS = ["recompute", "prefix", "shift", "epic32", "epic64", "epic128"]
REPS = 3
POOL_ROWS = 8192             # GPU store outside vLLM's share; loads cycle through it (row content does not
                             # affect timing), so S may exceed it
UTIL = 0.90                  # leaves vLLM >= 32K tokens of KV and room for the store and CUDA graphs
OUT_NAME = "latency"


def calls_from_live(live: Path) -> list[dict]:
    """Lead calls of EPIC arms from the server log: P, E (p0), S, H and C per arm come from the plans;
    the same call's geometry under the other arms is recomputed from its plan stats."""
    rows = []
    for path in ec.server_logs(live):
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("ev") != "call" or "S" not in rec or rec.get("fallback"):
                continue
            rows.append(rec)
    return rows


def geometry(rec: dict) -> dict:
    """Tokens per arm for one call: E exact (cache), S loaded+rotated, C computed. The EPIC arms differ
    only by run heads: H_k = sum over runs of min(k, run length) -- approximated from the logged arm."""
    P, E, S, H = rec["P"], rec["E"], rec["S"], rec["H"]
    reused = S + H                                   # reused-after-edit tokens of this call
    runs = max(1, int(rec.get("runs") or 1))
    out = {"recompute": {"E": 0, "S": 0, "C": P}, "prefix": {"E": E, "S": 0, "C": P - E}}
    for arm, k in (("shift", 0), ("epic32", 32), ("epic64", 64), ("epic128", 128)):
        h = min(reused, runs * k)
        out[arm] = {"E": E, "S": reused - h, "C": P - E - (reused - h)}
    return out


def sample_calls(recs: list[dict], n: int, seed: str = "ec") -> list[dict]:
    rng = random.Random(seed)
    edited = [r for r in recs if r.get("edited")]
    plain = [r for r in recs if not r.get("edited")]
    k = min(len(edited), int(n * 0.8))
    return rng.sample(edited, k) + rng.sample(plain, min(len(plain), n - k))


def cmd_run(args) -> None:
    out = (ec.DATA / args.live if not Path(args.live).is_absolute() else Path(args.live)) / args.out_name
    out.mkdir(parents=True, exist_ok=True)
    if not args.inner:
        gpu = ec.pick_gpu(args.gpu)
        code = ec.run_isolated(gpu, [ec.PYTHON, __file__, "run", "--live", args.live, "--calls",
                                     str(args.calls), "--out-name", args.out_name, "--inner"], out / "latency.log",
                               env_extra={"VLLM_ALLOW_INSECURE_SERIALIZATION": "1",
                                          "PYTHONPATH": str(ec.HERE)})
        raise SystemExit(code)
    import os
    os.environ["EPIC_POOL_ROWS"] = str(POOL_ROWS)
    from vllm import LLM, SamplingParams

    live = ec.DATA / args.live if not Path(args.live).is_absolute() else Path(args.live)
    recs = sample_calls(calls_from_live(live), args.calls)
    llm = LLM(model=ec.MODEL, max_model_len=32768, gpu_memory_utilization=UTIL, enable_prefix_caching=True,
              async_scheduling=False, seed=0, max_num_seqs=4, max_num_batched_tokens=8192,
              kv_transfer_config={"kv_connector": "EpicConnector", "kv_connector_module_path": "epic_connector",
                                  "kv_role": "kv_both", "kv_load_failure_policy": "fail",
                                  "kv_connector_extra_config": {"store_device": "cuda", "pool_rows": POOL_ROWS,
                                                                "hooks": False}})
    tok = llm.get_tokenizer()
    vocab = [t for t in range(1000, 100000)]
    rng = random.Random("ec-tokens")
    one = SamplingParams(max_tokens=1, temperature=0.0)

    def timed(ids, salt, splice=None):
        sp = SamplingParams(max_tokens=1, temperature=0.0,
                            extra_args={"kv_transfer_params": {"splice": splice}} if splice else None)
        t = time.perf_counter()
        llm.generate([{"prompt_token_ids": ids, "cache_salt": salt}], sp, use_tqdm=False)
        return 1000 * (time.perf_counter() - t)

    # fill the GPU pool once with real rows (content does not affect timing)
    filler = [rng.choice(vocab) for _ in range(POOL_ROWS - 16)]
    for lo in range(0, len(filler), 8000):
        chunk = filler[lo:lo + 8000]
        llm.generate([{"prompt_token_ids": chunk, "cache_salt": f"fill{lo}"}],
                     SamplingParams(max_tokens=1, extra_args={"kv_transfer_params": {"splice": {
                         "p0": 0, "load": [], "override": [], "save": [[0, len(chunk), lo]]}}}), use_tqdm=False)
    results = []
    path = out / "latency_calls.jsonl"
    done = {json.loads(l)["key"] for l in path.read_text().splitlines()} if path.exists() else set()
    for i, rec in enumerate(recs):
        key = f"{rec['spec']}#{rec['call']}"
        if key in done:
            continue
        geo = geometry(rec)
        P = rec["P"]
        ids = [rng.choice(vocab) for _ in range(P)]
        times = {arm: [] for arm in ARMS}
        for rep in range(REPS):
            order = ARMS[rep % len(ARMS):] + ARMS[:rep % len(ARMS)]
            for arm in order:
                g = geo[arm]
                salt = f"{key}:{arm}:{rep}"
                if g["E"]:
                    llm.generate([{"prompt_token_ids": ids[:g["E"] + 1], "cache_salt": salt}], one,
                                 use_tqdm=False)                       # warm the prefix cache
                if arm in ("recompute", "prefix") or not g["S"]:
                    times[arm].append(timed(ids, salt))
                    continue
                e16 = (g["E"] // 16) * 16
                p0 = min(((g["E"] + g["S"]) // 16) * 16, ((P - 1) // 16) * 16)
                span = POOL_ROWS - 16
                load = [[lo, min(lo + span, p0), 0, 97] for lo in range(e16, p0, span)]   # rotate every row
                times[arm].append(timed(ids, salt, {"p0": p0, "load": load, "override": [], "save": []}))
        row = {"key": key, "P": P, "geo": geo, "edited": rec.get("edited"),
               "ms": {arm: statistics.median(v) for arm, v in times.items()}}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        if i % 20 == 0:
            ec.log(f"{i}/{len(recs)} {key} P={P} " + " ".join(f"{a}={row['ms'][a]:.0f}" for a in ARMS))
    # TPOT by context
    tp = []
    for ctx in (2000, 6000, 10000, 16000, 24000, 30000):
        for n_seq in (1, 2):
            if n_seq * (ctx + 129) > 32768:        # beyond the KV pool at UTIL: vLLM would preempt, not decode
                continue
            prompts = [{"prompt_token_ids": [rng.choice(vocab) for _ in range(ctx)], "cache_salt": f"tp{ctx}{s}"}
                       for s in range(n_seq)]
            llm.generate(prompts, SamplingParams(max_tokens=1), use_tqdm=False)
            t = time.perf_counter()
            llm.generate(prompts, SamplingParams(max_tokens=129, min_tokens=129, temperature=0.0), use_tqdm=False)
            total = 1000 * (time.perf_counter() - t)
            tp.append({"ctx": ctx, "n_seq": n_seq, "tpot_ms": total / 128})
    (out / "tpot.json").write_text(json.dumps(tp, indent=1), encoding="utf-8")
    del tok


def fit(rows: list[dict], arms: list[str]) -> dict:
    import numpy as np
    X, y = [], []
    for r in rows:
        for arm in arms:
            g = r["geo"][arm]
            C, S, P = g["C"], g["S"], r["P"]
            X.append([1.0, C, C * P / 1e3, S])
            y.append(r["ms"][arm])
    X, y = np.array(X), np.array(y)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {"beta": beta.tolist(), "n": len(y)}


def predict(beta, C, S, P) -> float:
    return beta[0] + beta[1] * C + beta[2] * C * P / 1e3 + beta[3] * S


def cmd_report(args) -> None:
    import numpy as np
    live = ec.DATA / args.live if not Path(args.live).is_absolute() else Path(args.live)
    out = live / args.out_name
    rows = [json.loads(l) for l in (out / "latency_calls.jsonl").read_text().splitlines()]
    rng = random.Random("ec-split")
    rng.shuffle(rows)
    cut = (2 * len(rows)) // 3
    train, test = rows[:cut], rows[cut:]
    models = {"compute": fit(train, ["recompute", "prefix"]),
              "reuse": fit(train, ["shift", "epic32", "epic64", "epic128"])}
    lines = ["# E-C: time to first token per arm on one H100 (shape-equivalent requests)", "",
             f"{len(rows)} lead calls; median of {REPS} timed runs each; CUDA graphs, GPU store.", "",
             "| arm | tokens computed (mean) | TTFT median (ms) | TTFT mean (ms) | share of recompute (median) |",
             "|---|---|---|---|---|"]
    for arm in ARMS:
        ms = [r["ms"][arm] for r in rows]
        share = [r["ms"][arm] / r["ms"]["recompute"] for r in rows]
        comp = statistics.mean(r["geo"][arm]["C"] for r in rows)
        lines.append(f"| {arm} | {comp:,.0f} | {statistics.median(ms):,.0f} | {statistics.mean(ms):,.0f} | "
                     f"{statistics.median(share):.0%} |")
    lines += ["", "**TTFT model** (fit on 2/3 of the calls, validated on the rest):", "",
              "| family | b0 (ms) | b1 (ms/token) | b2 (ms/token/1k ctx) | b3 (ms/loaded row) | held-out MdAPE |",
              "|---|---|---|---|---|---|"]
    for name, arms in (("compute", ["recompute", "prefix"]), ("reuse", ["shift", "epic32", "epic64", "epic128"])):
        b = models[name]["beta"]
        errs = [abs(predict(b, r["geo"][a]["C"], r["geo"][a]["S"], r["P"]) - r["ms"][a]) / r["ms"][a]
                for r in test for a in arms]
        models[name]["mdape"] = statistics.median(errs) if errs else None
        lines.append(f"| {name} | {b[0]:.1f} | {b[1]:.4f} | {b[2]:.5f} | {b[3]:.5f} | "
                     f"{models[name]['mdape']:.1%} |")
    tp = json.loads((out / "tpot.json").read_text()) if (out / "tpot.json").exists() else []
    if tp:
        lines += ["", "**TPOT** (ms per output token):", "", "| context | 1 seq | 2 seqs |", "|---|---|---|"]
        for ctx in sorted({t["ctx"] for t in tp}):
            v = {t["n_seq"]: t["tpot_ms"] for t in tp if t["ctx"] == ctx}
            lines.append(f"| {ctx:,} | {v.get(1, float('nan')):.1f} | {v.get(2, float('nan')):.1f} |")
    (out / "models.json").write_text(json.dumps({"models": models, "tpot": tp}, indent=1), encoding="utf-8")
    (out / "latency_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    del np


# -- job completion time ------------------------------------------------------------------------------------

def tpot_at(tp: list[dict], ctx: float) -> float:
    """ms per output token at a context (one sequence), linear between the measured contexts."""
    pts = sorted((t["ctx"], t["tpot_ms"]) for t in tp if t["n_seq"] == 1)
    if not pts:
        return float("nan")
    if ctx <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if ctx <= x1:
            return y0 + (y1 - y0) * (ctx - x0) / (x1 - x0)
    return pts[-1][1]


def ttft_ms(models: dict, arm: str, C: float, S: float, P: float) -> float:
    family = "compute" if arm in ("recompute", "prefix", "prefix_aa") else "reuse"
    return predict(models[family]["beta"], C, S, P)


def lead_timing(run_dir: Path) -> list[tuple[int, float, float]]:
    """(call index, send, receive) of a recorded run's lead calls, in order."""
    label = run_dir.name
    path = run_dir / f"{label}.requests.jsonl.xz"
    if not path.exists():
        path = run_dir / f"{label}.requests.jsonl"
    if not path.exists():
        return []
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            r = json.loads(line)
            if r.get("purpose") == "lead" and r.get("t_send") is not None and r.get("t_recv") is not None:
                out.append((int(r["call_index"]), float(r["t_send"]), float(r["t_recv"])))
    return sorted(out)


def tool_s(timing: list[tuple[int, float, float]], n: int | None = None) -> float:
    """Tool and harness time: the gaps between consecutive lead calls (the first n calls)."""
    t = timing[:n] if n else timing
    return sum(max(0.0, b[1] - a[2]) for a, b in zip(t, t[1:]))


def call_geometry(arm: str, c: dict) -> tuple[int, int]:
    """(computed, loaded-and-rotated) tokens of one live call as served (prefix arms and failed plans: vLLM's
    cache hits; EPIC arms: the plan, less rows the prefix cache served beyond it)."""
    if arm in ("prefix", "prefix_aa") or "S" not in c or c.get("fallback"):
        return c["P"] - int(c.get("apc_hit") or 0), 0
    return (c["C"] - int(c.get("apc_over_C") or 0), c["S"] - int(c.get("apc_over_S") or 0))


def cmd_jct(args) -> None:
    live = ec.DATA / args.live if not Path(args.live).is_absolute() else Path(args.live)
    out = live / args.out_name
    out.mkdir(parents=True, exist_ok=True)
    mj = json.loads((Path(args.models) if args.models else out / "models.json").read_text())
    models, tp = mj["models"], mj["tpot"]
    calls = defaultdict(list)
    for path in ec.server_logs(live):
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("ev") == "call":
                calls[rec["spec"].rsplit("~a", 1)[0]].append(rec)
    plan_ms = defaultdict(list)
    runs = []
    for run_dir in sorted((live / "runs").iterdir()):
        label = run_dir.name
        meta_path = run_dir / f"{label}.meta.json"
        if not (run_dir / f"{label}.done").exists() or not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        arm, cs = meta.get("arm"), sorted(calls.get(label, []), key=lambda r: r["call"])
        if not cs:
            continue
        pre = dec = plan = 0.0
        for c in cs:
            C, S = call_geometry(arm, c)
            gen = int(c.get("gen") or 0)
            pre += ttft_ms(models, arm, C, S, c["P"])
            dec += gen * tpot_at(tp, c["P"] + gen / 2)
            if arm not in ("prefix", "prefix_aa"):
                plan += float(c.get("plan_ms") or 0)
                plan_ms[arm].append(float(c.get("plan_ms") or 0))
        tools = tool_s(lead_timing(run_dir))
        runs.append({"label": label, "arm": arm, "block": meta.get("block"), "cell": meta.get("cell"),
                     "calls": len(cs), "prefill_s": pre / 1e3, "decode_s": dec / 1e3,
                     "plan_s": plan / 1e3, "tool_s": tools, "jct_s": (pre + dec + plan) / 1e3 + tools})
    (out / "jct_runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")
    order = ["prefix", "prefix_aa", "shift", "epic32", "epic64", "epic128"]
    lines = ["# Job completion time (modeled on one H100)", "",
             f"Models: `{args.models or out / 'models.json'}`. Prefill = the fitted TTFT model on each call's "
             "geometry; decode = output tokens x TPOT(context); planning = the live planner's time; tool = the "
             "recorded gaps between lead calls.", "",
             "**As run** (each live run on its own trajectory; means per run):", "",
             "| arm | runs | lead calls | prefill (s) | decode (s) | planning (s) | tool + harness (s) | JCT (s) | "
             "prefill per call (ms) |", "|---|---|---|---|---|---|---|---|---|"]
    for arm in order:
        rs = [r for r in runs if r["arm"] == arm]
        if not rs:
            continue
        n_calls = sum(r["calls"] for r in rs)
        m = {k: statistics.mean(r[k] for r in rs) for k in ("calls", "prefill_s", "decode_s", "plan_s", "tool_s",
                                                              "jct_s")}
        lines.append(f"| {arm} | {len(rs)} | {m['calls']:.0f} | {m['prefill_s']:.1f} | {m['decode_s']:.1f} | "
                     f"{m['plan_s']:.2f} | {m['tool_s']:.1f} | {m['jct_s']:.1f} | "
                     f"{1e3 * sum(r['prefill_s'] for r in rs) / max(1, n_calls):.0f} |")
    import epic_analyze as ea                         # paired by block, as the task scores are
    lines += ["", "**As run, paired by block** (arm minus prefix, seconds per run; cluster bootstrap over cells; "
              "trajectories differ between arms, so decode differences are mostly trajectory noise):", "",
              "| arm | blocks | prefill [95% CI] | decode [95% CI] | JCT [95% CI] | JCT ratio (paired sums) |",
              "|---|---|---|---|---|---|"]
    for arm in order[1:]:
        pr = {k: ea.paired_blocks(runs, "prefix", arm, k) for k in ("prefill_s", "decode_s", "jct_s")}
        if not pr["jct_s"]:
            continue
        cis = {k: ea.cluster_bootstrap(v) for k, v in pr.items()}
        ratio = sum(a for _c, _b, a in pr["jct_s"]) / sum(b for _c, b, _a in pr["jct_s"])
        lines.append(f"| {arm} | {len(pr['jct_s'])} | " + " | ".join(
            f"{cis[k][0]:+.1f} [{cis[k][1]:+.1f}, {cis[k][2]:+.1f}]" for k in ("prefill_s", "decode_s", "jct_s"))
            + f" | {ratio:.3f} |")
    # paired: E-A replays of recorded prefix trajectories, every arm on identical calls
    med_plan = {a: statistics.median(v) for a, v in plan_ms.items() if v}
    rows = []
    for src in args.replay:
        src_dir, _, name = src.partition(":")
        src_live = ec.DATA / src_dir if not Path(src_dir).is_absolute() else Path(src_dir)
        path = src_live / (name or "replay") / "replay_calls.jsonl"
        if not path.exists():
            continue
        by_run = defaultdict(list)
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            by_run[rec["run"]].append(rec)
        for run, recs in sorted(by_run.items()):
            recs.sort(key=lambda r: r["call"])
            tools = tool_s(lead_timing(src_live / "runs" / run), len(recs))
            tot = {a: 0.0 for a in ["recompute", "prefix", "shift", "epic32", "epic64", "epic128"]}
            dec = 0.0
            for rec in recs:
                st = next(iter(rec["arms"].values()))["stats"]
                P, E = rec["P"], st["E"]
                tot["recompute"] += ttft_ms(models, "recompute", P, 0, P)
                tot["prefix"] += ttft_ms(models, "prefix", P - E, 0, P)
                for arm in ("shift", "epic32", "epic64", "epic128"):
                    a = rec["arms"].get(arm)
                    if a:
                        tot[arm] += ttft_ms(models, arm, a["stats"]["C"], a["stats"]["S"], P) + med_plan.get(arm, 0.0)
                dec += int(rec.get("reply") or 0) * tpot_at(tp, P + int(rec.get("reply") or 0) / 2)
            rows.append({"src": src, "run": run, "calls": len(recs), "tool_s": tools, "decode_s": dec / 1e3,
                         "prefill_s": {a: v / 1e3 for a, v in tot.items()}})
    if rows:
        n_calls = sum(r["calls"] for r in rows)
        base = sum(r["prefill_s"]["prefix"] for r in rows)
        rec_ = sum(r["prefill_s"]["recompute"] for r in rows)
        dec = sum(r["decode_s"] for r in rows)
        tools = sum(r["tool_s"] for r in rows)
        lines += ["", f"**Paired, on identical trajectories** (E-A replays: {len(rows)} recorded prefix runs, "
                  f"{n_calls} lead calls; the same decode {dec:.0f} s and tool time {tools:.0f} s in every arm):", "",
                  "| arm | prefill per call (ms) | prefill vs prefix | prefill vs recompute | JCT (s, sum) | "
                  "JCT vs prefix |", "|---|---|---|---|---|---|"]
        jct_prefix = base + dec + tools
        for arm in ["recompute", "prefix", "shift", "epic32", "epic64", "epic128"]:
            pre = sum(r["prefill_s"][arm] for r in rows)
            jct = pre + dec + tools
            lines.append(f"| {arm} | {1e3 * pre / n_calls:.0f} | {pre / base:.0%} | {pre / rec_:.0%} | "
                         f"{jct:,.0f} | {jct / jct_prefix:.3f} |")
        (out / "jct_replay.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (out / "jct_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def busy_share(calls: list[dict]) -> None:
    """Per call, the share of its interval during which another call was in flight on the same server."""
    for c in calls:
        spans = sorted((max(c["t0"], o["t0"]), min(c["t1"], o["t1"])) for o in calls
                       if o is not c and o["t0"] < c["t1"] and o["t1"] > c["t0"])
        covered, end = 0.0, c["t0"]
        for a, b in spans:
            if b > end:
                covered += b - max(a, end)
                end = b
        c["share2"] = covered / (c["t1"] - c["t0"]) if c["t1"] > c["t0"] else 0.0


def cmd_validate(args) -> None:
    """The models against real serving: every lead call of the E-0 runs of configuration C (vanilla vLLM 0.31 with
    its prefix cache, CUDA graphs, the append-only template; two sessions at a time; non-streamed, so the
    measured time is the whole call), priced from the call's own usage exactly as `jct` prices a prefix call:
    TTFT_compute(P - cached, P) + n_out x TPOT(P + n_out/2). A second column uses the two-sequence TPOT for the
    share of the call another request was running (the JCT model assumes one sequence)."""
    live = ec.DATA / args.live if not Path(args.live).is_absolute() else Path(args.live)
    out = live / args.out_name
    mj = json.loads((Path(args.models) if args.models else out / "models.json").read_text())
    models, tp = mj["models"], mj["tpot"]
    tp2 = [{**t, "n_seq": 1} for t in tp if t["n_seq"] == 2 and t["tpot_ms"] == t["tpot_ms"]]
    calls = []
    for run_dir in sorted((ec.DATA / "e0_reuse" / "runs").iterdir()):
        path = run_dir / f"{run_dir.name}.requests.jsonl.xz"
        if not path.exists():
            continue
        with lzma.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                r = json.loads(line)
                u = (r.get("response") or {}).get("usage") or {}
                if r.get("purpose") != "lead" or not u or r.get("t_send") is None or r.get("t_recv") is None:
                    continue
                calls.append({"run": run_dir.name, "config": run_dir.name.split("-")[2], "t0": float(r["t_send"]),
                              "t1": float(r["t_recv"]), "cached": int(u.get("cache_read_input_tokens") or 0),
                              "P": sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                                    "cache_creation_input_tokens")),
                              "n_out": int(u.get("output_tokens") or 0)})
    busy_share(calls)                                  # all configurations shared one server
    rows = [c for c in calls if c["config"] == "C"]
    for c in rows:
        ctx = c["P"] + c["n_out"] / 2
        ttft = ttft_ms(models, "prefix", c["P"] - c["cached"], 0, c["P"])
        c["ms"] = 1e3 * (c["t1"] - c["t0"])
        c["pred1"] = ttft + c["n_out"] * tpot_at(tp, ctx)
        c["pred2"] = ttft + c["n_out"] * ((1 - c["share2"]) * tpot_at(tp, ctx) + c["share2"] * tpot_at(tp2, ctx))

    def ape(sel, key):
        return [abs(c[key] - c["ms"]) / c["ms"] for c in sel if c["ms"] > 0]

    def err(sel, key):                                 # signed: model minus measured, relative to measured
        return statistics.median((c[key] - c["ms"]) / c["ms"] for c in sel if c["ms"] > 0)

    bins = [("all calls", rows), ("<= 50 output tokens", [c for c in rows if c["n_out"] <= 50]),
            ("51-300 output tokens", [c for c in rows if 50 < c["n_out"] <= 300]),
            ("> 300 output tokens", [c for c in rows if c["n_out"] > 300]),
            ("computed >= 4,000 tokens", [c for c in rows if c["P"] - c["cached"] >= 4000])]
    lines = ["# E-C validation against real serving", "",
             f"{len(rows)} lead calls of {len({c['run'] for c in rows})} E-0 runs, configuration C (vanilla vLLM "
             "0.31, prefix cache, CUDA graphs, append-only template, two sessions at a time, non-streamed). "
             "Measured: the client's send-to-receive time. Model: TTFT_compute(P - cached, P) + n_out x "
             "TPOT(context), as `jct` prices a prefix call; the second model uses the two-sequence TPOT for the "
             "share of the call another request was in flight.", "",
             "| calls | n | measured median (ms) | MdAPE, JCT model (1 seq) | median signed error | "
             "MdAPE, with concurrency | median signed error |", "|---|---|---|---|---|---|---|"]
    for name, sel in bins:
        if not sel:
            continue
        lines.append(f"| {name} | {len(sel)} | {statistics.median(c['ms'] for c in sel):,.0f} | "
                     f"{statistics.median(ape(sel, 'pred1')):.1%} | {err(sel, 'pred1'):+.1%} | "
                     f"{statistics.median(ape(sel, 'pred2')):.1%} | {err(sel, 'pred2'):+.1%} |")
    per_run = defaultdict(lambda: [0.0, 0.0, 0.0])
    for c in rows:
        acc = per_run[c["run"]]
        acc[0] += c["ms"]
        acc[1] += c["pred1"]
        acc[2] += c["pred2"]
    rel1 = [(p1 - m) / m for m, p1, _ in per_run.values() if m > 0]
    rel2 = [(p2 - m) / m for m, _, p2 in per_run.values() if m > 0]
    md1, md2 = statistics.median(map(abs, rel1)), statistics.median(map(abs, rel2))
    lines += ["", f"Per run (sum of its lead calls' time, {len(per_run)} runs): MdAPE {md1:.1%} (signed median "
              f"{statistics.median(rel1):+.1%}) with the JCT model, {md2:.1%} ({statistics.median(rel2):+.1%}) with "
              f"concurrency. Mean share of a call with another request in flight: "
              f"{statistics.mean(c['share2'] for c in rows):.0%}."]
    (out / "validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "validation_calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in rows), encoding="utf-8")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--live", required=True)
    r.add_argument("--calls", type=int, default=600)
    r.add_argument("--out-name", default=OUT_NAME)
    r.add_argument("--gpu", type=int, default=None)
    r.add_argument("--inner", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("--live", required=True)
    rep.add_argument("--out-name", default=OUT_NAME)
    j = sub.add_parser("jct")
    j.add_argument("--live", required=True)
    j.add_argument("--out-name", default=OUT_NAME)
    j.add_argument("--models", default=None, help="models.json (default: <live>/<out-name>/models.json)")
    j.add_argument("--replay", action="append", default=[],
                   help="live_dir[:replay_name] whose E-A replay to price (default name replay), repeatable")
    v = sub.add_parser("validate")
    v.add_argument("--live", required=True)
    v.add_argument("--out-name", default=OUT_NAME)
    v.add_argument("--models", default=None, help="models.json (default: <live>/<out-name>/models.json)")
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report, "jct": cmd_jct, "validate": cmd_validate}[args.cmd](args)


if __name__ == "__main__":
    main()
