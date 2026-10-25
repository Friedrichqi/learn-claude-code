#!/usr/bin/env python3
"""Observational time model of the EPIC arms (MiniMax-M2.7), from the baseline's measured lead calls.

    .venv/bin/python report/latency_model.py --server minimax-m2.7-epic --baseline minimax-m2.7 --phase e1

The EPIC server emulates EPIC (it computes every token after the exact prefix, then overrides the reused rows), so
its wall clock says nothing about what EPIC would save. Instead:
- Every baseline lead call's time (CUDA graphs, vLLM's own prefix cache, the profile's concurrency) is fitted as
      ms = b0 + b1*C + b2*C*P/1e3 + b3*out
  (C = computed prompt tokens, i.e. prompt minus prefix-cache hits; P = prompt tokens; out = output tokens) on
  2/3 of the calls with non-negative coefficients, and validated on the rest (median absolute percentage
  error, MdAPE). Contention with the
  other sessions is part of the noise; the fit is a rough price list, not a latency benchmark.
- Every lead call of the EPIC round is priced with the tokens its arm would compute:
  - prefix arms: `ideal_C`, what an unbounded prefix cache would recompute;
  - EPIC arms: the plan's `C`.
  The reused rows' load and re-rotation cost nothing in this model, so EPIC's time is a lower bound.
- Per run:
  - model LLM time is the sum over its lead calls;
  - tool and harness time is the run's agent wall time minus its measured call times (independent of the
    server's speed);
  - model job time is the sum of the two.
Per arm the report gives computed tokens per call and per run, model LLM time and job time per run, and the
ratio of each to `prefix`. Arms follow their own trajectories, so per-run numbers include any change in run length
(e.g. looping); per-call numbers do not.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import statistics as st
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def prompt_tokens(u: dict) -> tuple[int, int]:
    cached = int(u.get("cache_read_input_tokens") or 0)
    p = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
    return p, p - cached


def fit(baseline: str, bench: str) -> tuple[np.ndarray, float, int]:
    rows = []
    for f in glob.glob(str(ROOT / "jobs" / baseline / bench / "q*" / "*" / "agent" / "s15" / "calls.jsonl")):
        for c in load_jsonl(Path(f)):
            if c.get("purpose") != "lead" or not c.get("usage") or c.get("ms") is None:
                continue
            p, comp = prompt_tokens(c["usage"])
            rows.append((comp, p, int(c["usage"].get("output_tokens") or 0), float(c["ms"])))
    random.Random(0).shuffle(rows)
    cut = len(rows) * 2 // 3

    def design(rs):
        return np.array([[1.0, c, c * p / 1e3, o] for c, p, o, _ in rs]), np.array([ms for *_, ms in rs])

    x, y = design(rows[:cut])
    # non-negative coefficients: contention noise otherwise drives the attention term (C*P) below zero. Exact for
    # four features: the best unconstrained fit over every subset of them whose coefficients are all >= 0
    import itertools
    best, beta = None, np.zeros(x.shape[1])
    for k in range(1, x.shape[1] + 1):
        for cols in itertools.combinations(range(x.shape[1]), k):
            b, *_ = np.linalg.lstsq(x[:, cols], y, rcond=None)
            if (b < 0).any():
                continue
            sse = float(((x[:, cols] @ b - y) ** 2).sum())
            if best is None or sse < best:
                best, beta = sse, np.zeros(x.shape[1])
                beta[list(cols)] = b
    xv, yv = design(rows[cut:])
    pred = xv @ beta
    mdape = float(np.median(np.abs(pred - yv) / np.maximum(yv, 1.0)))
    return beta, mdape, len(rows)


def price(beta: np.ndarray, c: float, p: float, out: float) -> float:
    return float(beta[0] + beta[1] * c + beta[2] * c * p / 1e3 + beta[3] * out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="minimax-m2.7-epic")
    ap.add_argument("--baseline", default="minimax-m2.7")
    ap.add_argument("--bench", default="swe")
    ap.add_argument("--phase", default="e1")
    ap.add_argument("--log", default="")
    ap.add_argument("--out", default="", help="default report/latency_<server>_<bench>.md")
    a = ap.parse_args()
    beta, mdape, n_fit = fit(a.baseline, a.bench)
    log = load_jsonl(Path(a.log) if a.log else ROOT / "runs" / "epic" / f"{a.server}.calls.jsonl")
    srv = {(x["spec"], int(x.get("call", -1))): x for x in log if x.get("ev") == "call"}
    per_arm = defaultdict(lambda: {"runs": 0, "calls": 0, "C": [], "C_run": [], "llm_s": [], "tool_s": [],
                                   "job_s": [], "missing": 0})
    for tdir in sorted(glob.glob(str(ROOT / "jobs" / f"{a.server}+*" / a.bench / f"{a.phase}r*__*" / "*__*"))):
        tdir = Path(tdir)
        arm = tdir.parts[-4].split("+", 1)[1]
        res = json.loads((tdir / "result.json").read_text()) if (tdir / "result.json").is_file() else {}
        ae = res.get("agent_execution") or {}
        if not ae.get("started_at") or not ae.get("finished_at"):
            continue
        wall = (datetime.fromisoformat(ae["finished_at"].replace("Z", "+00:00")) -
                datetime.fromisoformat(ae["started_at"].replace("Z", "+00:00"))).total_seconds()
        calls = [c for c in load_jsonl(tdir / "agent" / "s15" / "calls.jsonl")
                 if c.get("purpose") == "lead" and c.get("epic") and c.get("response") is not None]
        m = per_arm[arm]
        llm_ms = measured_ms = c_run = 0.0
        for c in calls:
            r = srv.get((tdir.name, int(c["epic"]["n"])))
            if r is None:
                m["missing"] += 1
                continue
            comp = r.get("ideal_C") if r.get("ideal_C") is not None else r.get("C")
            if comp is None:
                m["missing"] += 1
                continue
            out = int((c.get("usage") or {}).get("output_tokens") or 0)
            llm_ms += price(beta, comp, r["P"], out)
            measured_ms += float(c.get("ms") or 0)
            c_run += comp
            m["C"].append(comp)
            m["calls"] += 1
        tool_s = max(0.0, wall - measured_ms / 1000)
        m["runs"] += 1
        m["C_run"].append(c_run)
        m["llm_s"].append(llm_ms / 1000)
        m["tool_s"].append(tool_s)
        m["job_s"].append(llm_ms / 1000 + tool_s)
    ref = per_arm.get("prefix")
    lines = [f"### Time model ({a.server}, {a.bench}, phase {a.phase})", "",
             f"Fitted on {n_fit} baseline lead calls of `{a.baseline}`: ms = {beta[0]:.0f} + {beta[1] * 1e3:.3f}·C/1e3 "
             f"+ {beta[2] * 1e3:.4f}·C·P/1e6 + {beta[3]:.1f}·out; validation MdAPE {mdape:.0%}. Reused rows cost "
             "nothing in the model (EPIC's time is a lower bound).", "",
             "| arm | runs | calls | computed tok / call | computed tok / run | model LLM time / run | "
             "tool+harness / run | model job time / run | job time vs prefix |", "|---|---|---|---|---|---|---|---|---|"]
    for arm, m in sorted(per_arm.items()):
        if not m["runs"]:
            continue
        job = st.mean(m["job_s"])
        rel = f"{job / st.mean(ref['job_s']):.2f}x" if ref and ref["runs"] else "—"
        lines.append(f"| {arm} | {m['runs']} | {m['calls']} | {st.mean(m['C']) if m['C'] else 0:,.0f} | "
                     f"{st.mean(m['C_run']):,.0f} | {st.mean(m['llm_s']):.0f} s | {st.mean(m['tool_s']):.0f} s | "
                     f"{job:.0f} s | {rel} |")
    lines.append("")
    text = "\n".join(lines) + "\n"
    (Path(a.out) if a.out else ROOT / "report" / f"latency_{a.server}_{a.bench}.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
