#!/usr/bin/env python3
"""e4's tables (plan ~/.claude/plans/whenever-we-do-shift-epic-misty-muffin.md §4; pre-registration
runs/epic/prereg_e4.md): one table per benchmark, the 7 arms as rows, the user's columns in the user's order.

    .venv/bin/python report/e4_report.py [--phase e4] [--benches swe,swepro,tbench] [--force]

Writes report/e4_minimax-m2.7-epic_<bench>.md (the table, its footnotes and the secondary tables),
report/e4_minimax-m2.7-epic.md (overview: the three tables and the notes) and report/e4_minimax-m2.7-epic.json
(every cell as {value, text, fmt}, the trial set, the inputs' hashes). report/e4_verify.py recomputes every cell.

**One trial set for every column**: per arm and block (replicate phase <p>r<k>, task; a rerun counts for its block)
the first valid trial by start time (epic_report.first_valid: finished, not an infrastructure failure). Its calls
are the completed lead calls: agent `calls.jsonl` records with purpose `lead`, an `epic` tag and a response, each
joined on (spec, epic.n) to the last non-aborted server record of that key.

Columns:
- runs: trials in the set. calls: joined calls (fallbacks in, aborted records out).
- computed tok / call, / run: charged tokens -- prefix arms `ideal_C` (what an unbounded prefix cache recomputes),
  EPIC arms the plan's `C`, recompute arms `C_charged` (= served C + the original-cache forward's C_O + the arm's
  own prefill C_E on a recomputation), fallbacks P - apc_hit.
- model LLM time / run: the per-benchmark time model (fitted on the baseline profile's lead calls, all of them, in
  sorted order; latency_model's legacy fit depends on the order the disk lists files) priced on the charged tokens.
  tool+harness / run: the agent phase's wall time minus the measured times of the joined calls (clipped at 0).
  model job time / run: their sum.
- computed / prompt: sum of charged tokens / sum of prompt tokens over the calls.
- post-snip calls: calls whose prompt is not an append-only extension of the session's previous sequence:
  exact < prev.P + max(0, prev.gen - 1) over the session's non-aborted records (the planner's `edited` for the EPIC
  arms; the previous sequence restarts after an entry reset and is kept across an EPIC arm's fallback).
- resolve difference, 90 % CI: arm minus control (the mean of prefix and prefix_aa) per block, averaged per task,
  then over tasks; a two-sided 90 % t-interval over the task means with an exact t quantile (df = tasks - 1). The
  prefix_aa row is prefix_aa minus prefix (A/A); the baseline row has none.
- pass@1: mean success (reward >= 1). pass^3: over the tasks with a valid trial in each of the 3 replicates, the
  share whose three all succeeded.
- first-token KL, reply KL, reply top-1: means over the calls' non-floor shadow probes of first_kl (KL(fresh || arm)
  over the fresh top-20), tf_kl and tf_top1 (the e1-e3 estimators). D^K reused by age: sum ||dK||^2 / sum ||K*||^2
  over the probes' overridden rows ("reused" class), by the rows' age bin.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import itertools
import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
import collect  # noqa: E402
import epic_report as er  # noqa: E402
import latency_model as lm  # noqa: E402

SERVER = "minimax-m2.7-epic"
BASELINE = "minimax-m2.7"
ARMS = ["prefix", "prefix_aa", "shift", "epic128", "epic128a1", "epic128a4", "epic128rjsd"]
LABEL = {"prefix": "baseline (full recomputation)", "prefix_aa": "prefix_aa", "shift": "shift",
         "epic128": "shift+EPIC", "epic128a1": "shift+EPICa1", "epic128a4": "shift+EPICa4",
         "epic128rjsd": "shift+EPIC+recompute@JSD", "epic128rtvd": "shift+EPIC+recompute@TVD",
         "epic128rexp": "shift+EPIC+recompute@EXP"}
BENCHES = ("swe", "swepro", "tbench")
BENCH_NAME = {"swe": "SWE-bench Verified", "swepro": "SWE-bench Pro", "tbench": "Terminal-Bench"}
COLUMNS = [("arm", "arm", "s"), ("runs", "runs", "d"), ("calls", "calls", "d"),
           ("c_call", "computed tok / call", ",.0f"), ("c_run", "computed tok / run", ",.0f"),
           ("llm_s", "model LLM time / run", ".0f s"), ("tool_s", "tool+harness / run", ".0f s"),
           ("job_s", "model job time / run", ".0f s"), ("comp_share", "computed / prompt", ".1%"),
           ("post_snip", "post-snip calls", "d"), ("diff", "resolve difference", "+.3f"), ("pass1", "pass@1", ".3f"),
           ("pass3", "pass^3", ".3f"), ("ci", "90% CI", "[+.3f, +.3f]"), ("first_kl", "first-token KL", ".4f"),
           ("reply_kl", "reply KL", ".4f"), ("reply_top1", "reply top-1", ".2%"),
           ("dk", "D^K reused by age (0,1,2-3,4-7,8-15,16+)", ".3f/")]
AGE_BINS = (0, 1, 2, 4, 8, 16)
OFF_N = 5
DEC_METRICS = ("jsd", "tvd", "exp", "kl", "jsd20", "tvd20", "exp20", "kl20")
STAGE_LOG = ROOT / "runs" / "epic" / "stage_e4.log"


# -- formatting ------------------------------------------------------------------------------------------------

def fmt_value(v, fmt: str) -> str:
    """The printed text of a cell (the verifier compares these strings)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if fmt == "s":
        return str(v)
    if fmt == "d":
        return f"{int(v):d}"
    if fmt == ",.0f":
        return f"{v:,.0f}"
    if fmt == ".0f s":
        return f"{v:.0f} s"
    if fmt == ".1%":
        return f"{v:.1%}"
    if fmt == ".2%":
        return f"{v:.2%}"
    if fmt in (".3f", ".4f"):
        return format(v, fmt)
    if fmt == "+.3f":
        t = f"{v:+.3f}"
        return "+0.000" if t == "-0.000" else t
    if fmt == "[+.3f, +.3f]":
        lo, hi = v
        if lo is None or hi is None:
            return "—"
        return f"[{fmt_value(lo, '+.3f')}, {fmt_value(hi, '+.3f')}]"
    if fmt == ".3f/":
        return " / ".join("—" if x is None else f"{x:.3f}" for x in v)
    raise ValueError(fmt)


def cell(v, fmt: str) -> dict:
    return {"value": v, "text": fmt_value(v, fmt), "fmt": fmt}


# -- statistics ------------------------------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def ibeta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * ibeta(df / 2.0, 0.5, x)
    return 1.0 - tail if t >= 0 else tail


def t_ppf(q: float, df: float) -> float:
    """The q-quantile of Student's t (q > 0.5), by bisection on t_cdf."""
    hi = 1.0
    while t_cdf(hi, df) < q:
        hi *= 2.0
    lo = 0.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def diff_ci(trials: dict, arm: str, control: list[str]) -> dict:
    """epic_report.paired's per-task statistic with an exact two-sided 90 % t-interval."""
    r = er.paired(trials, arm, control)
    out = {"tasks": r["tasks"], "blocks": r["blocks"], "diff": r["diff"], "lo": None, "hi": None, "df": None,
           "t": None, "se": r.get("se")}
    if r["diff"] is not None:
        df = r["tasks"] - 1
        t = t_ppf(0.95, df)
        out.update(df=df, t=t, lo=r["diff"] - t * r["se"], hi=r["diff"] + t * r["se"])
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def fisher_ci(rho: float | None, n: int, z: float = 1.6448536269514722) -> tuple:
    if rho is None or n < 4 or abs(rho) >= 1:
        return (None, None)
    f = math.atanh(rho)
    se = 1.0 / math.sqrt(n - 3)
    return (math.tanh(f - z * se), math.tanh(f + z * se))


# -- time model --------------------------------------------------------------------------------------------------

def _subset_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """latency_model.fit's estimator: the least-squares fit, over every subset of the features, with the lowest
    error among those whose coefficients are all >= 0."""
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
    return beta


def baseline_calls(baseline: str, bench: str) -> list[tuple]:
    """(computed, prompt, out, ms, trial) of every baseline lead call with usage and a measured time, sorted."""
    rows = []
    for f in sorted(glob.glob(str(ROOT / "jobs" / baseline / bench / "q*" / "*" / "agent" / "s15" / "calls.jsonl"))):
        trial = Path(f).parts[-4]
        for c in lm.load_jsonl(Path(f)):
            if c.get("purpose") != "lead" or not c.get("usage") or c.get("ms") is None:
                continue
            p, comp = lm.prompt_tokens(c["usage"])
            rows.append((comp, p, int(c["usage"].get("output_tokens") or 0), float(c["ms"]), trial))
    return rows


def _design(rows):
    return (np.array([[1.0, c, c * p / 1e3, o] for c, p, o, _ms, _t in rows]),
            np.array([ms for _c, _p, _o, ms, _t in rows]))


def fit_sorted(baseline: str, bench: str) -> dict:
    """The time model on all baseline calls (order-free); MdAPE from a 3-fold split by sha256(trial) mod 3."""
    rows = baseline_calls(baseline, bench)
    x, y = _design(rows)
    beta = _subset_fit(x, y)
    fold = np.array([int(hashlib.sha256(t.encode()).hexdigest(), 16) % 3 for *_r, t in rows])
    errs = []
    for k in range(3):
        tr, te = fold != k, fold == k
        b = _subset_fit(x[tr], y[tr])
        errs.extend((np.abs(x[te] @ b - y[te]) / np.maximum(y[te], 1.0)).tolist())
    return {"mode": "sorted", "beta": [float(v) for v in beta], "mdape": float(np.median(errs)), "n": len(rows)}


def fit_legacy(baseline: str, bench: str) -> dict:
    beta, mdape, n = lm.fit(baseline, bench)
    return {"mode": "legacy", "beta": [float(v) for v in beta], "mdape": mdape, "n": n}


# -- data ----------------------------------------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict]:
    return lm.load_jsonl(path)


def read_log(path: Path, specs: set[str]) -> tuple[dict, dict]:
    """The server records of `specs`, by spec, in file order; and the log's identity (bytes read, sha256)."""
    by_spec: dict[str, list[dict]] = defaultdict(list)
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for raw in f:
            h.update(raw)
            n += len(raw)
            if b'"spec"' not in raw:
                continue
            try:
                x = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if x.get("spec") in specs:
                by_spec[x["spec"]].append(x)
    return by_spec, {"path": str(path.relative_to(ROOT)), "bytes": n, "sha256": h.hexdigest()}


def post_snip_flags(recs: list[dict], epic_arm: bool) -> dict[int, bool]:
    """call -> whether its (last non-aborted) record follows an edit of the session's previous sequence."""
    calls = sorted((r for r in recs if r.get("ev") == "call" and not r.get("aborted")),
                   key=lambda r: (int(r.get("call", -1)), float(r.get("t") or 0)))
    out: dict[int, bool] = {}
    prev = None
    for r in calls:
        edited = False
        if prev is not None and r.get("exact") is not None:
            edited = int(r["exact"]) < int(prev["P"]) + max(0, int(prev.get("gen") or 0) - 1)
        out[int(r["call"])] = edited
        if r.get("entry_reset"):
            prev = None
        elif r.get("fallback") and epic_arm:
            pass                                  # the entry is kept: the next call is planned against the same one
        else:
            prev = r
    return out


def charged(r: dict) -> int:
    if r.get("fallback") or ("C" not in r and "ideal_C" not in r):
        return max(0, int(r.get("P") or 0) - int(r.get("apc_hit") or 0))
    if "ideal_C" in r:
        return int(r["ideal_C"])
    if "C_charged" in r:
        return int(r["C_charged"])
    return int(r["C"])


def trial_dir(row: dict) -> Path:
    return ROOT / "jobs" / row["model"] / row["bench"] / row["job"] / row["trial"]


def agent_wall(tdir: Path) -> float | None:
    res = json.loads((tdir / "result.json").read_text()) if (tdir / "result.json").is_file() else {}
    ae = res.get("agent_execution") or {}
    if not ae.get("started_at") or not ae.get("finished_at"):
        return None
    t0 = datetime.fromisoformat(ae["started_at"].replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(ae["finished_at"].replace("Z", "+00:00"))
    return (t1 - t0).total_seconds()


def build(phase: str, benches, arms: list[str], task_lists: dict, log_path: Path, fit_mode: str = "sorted",
          rows: list[dict] | None = None, baseline: str = BASELINE) -> dict:
    """Everything the tables show, per benchmark (pure apart from reading the run's files)."""
    rows = collect.load_trials() if rows is None else rows
    sel = {}
    for bench in benches:
        tasks = set(task_lists[bench])
        bench_rows = [r for r in rows if r["model"].startswith(SERVER + "+") and r["bench"] == bench
                      and r["model"].split("+", 1)[1] in arms and r["task"] in tasks
                      and er.block_of(r["phase"]).startswith(phase + "r")
                      and er.block_of(r["phase"])[len(phase) + 1:].isdigit()]
        sel[bench] = er.first_valid(bench_rows)
    specs = {r["trial"] for t in sel.values() for r in t.values()}
    by_spec, log_id = read_log(log_path, specs)
    out = {"phase": phase, "arms": arms, "log": log_id, "benches": {}}
    for bench in benches:
        trials = sel[bench]
        fit = fit_sorted(baseline, bench) if fit_mode == "sorted" else fit_legacy(baseline, bench)
        beta = np.array(fit["beta"])
        control = [c for c in ("prefix", "prefix_aa") if c in arms]
        per_arm: dict[str, dict] = {}
        acct: dict[str, dict] = {}
        decisions: dict[str, list] = defaultdict(list)
        rel_pairs: list[dict] = []
        floor_kl: list[float] = []
        floor_part: dict[str, list] = defaultdict(list)
        trial_list = []
        for arm in arms:
            m = {"runs": 0, "calls": 0, "C": 0, "P": 0, "C_run": [], "llm_s": [], "tool_s": [], "job_s": [],
                 "post_snip": 0, "first_kl": [], "tf_kl": [], "tf_top1": [], "dk": defaultdict(lambda: [0.0, 0.0]),
                 "dec_runs": []}
            a = {"aborted_records": 0, "aborted_P": 0, "aborted_C": 0, "fallbacks": 0, "missing": 0, "orphans": 0,
                 "errored_attempts": 0, "non_lead_calls": 0, "tool_clipped": 0, "entry_resets": 0}
            epic_arm = arm not in ("prefix", "prefix_aa")
            for (a_, block, task), row in sorted(trials.items()):
                if a_ != arm:
                    continue
                tdir = trial_dir(row)
                recs = by_spec.get(row["trial"], [])
                flags = post_snip_flags(recs, epic_arm)
                last: dict[int, dict] = {}
                n_rec: dict[int, int] = defaultdict(int)
                for r in recs:
                    if r.get("ev") != "call":
                        continue
                    if r.get("aborted"):
                        a["aborted_records"] += 1
                        a["aborted_P"] += int(r.get("P") or 0)
                        a["aborted_C"] += int(r.get("C") or r.get("ideal_C") or 0)
                        continue
                    k = int(r["call"])
                    n_rec[k] += 1
                    if k not in last or float(r.get("t") or 0) >= float(last[k].get("t") or 0):
                        last[k] = r
                calls = load_jsonl(tdir / "agent" / "s15" / "calls.jsonl")
                a["errored_attempts"] += sum(1 for c in calls if c.get("purpose") == "lead" and c.get("error"))
                a["non_lead_calls"] += sum(1 for c in calls if c.get("purpose") != "lead")
                lead = [c for c in calls if c.get("purpose") == "lead" and c.get("epic") and c.get("response") is not None]
                c_run = 0
                llm_ms = meas_ms = 0.0
                n_dec = 0
                joined = set()
                for c in lead:
                    k = int(c["epic"]["n"])
                    r = last.get(k)
                    if r is None:
                        a["missing"] += 1
                        continue
                    joined.add(k)
                    ch = charged(r)
                    out_tok = int((c.get("usage") or {}).get("output_tokens") or 0)
                    m["calls"] += 1
                    m["C"] += ch
                    m["P"] += int(r.get("P") or 0)
                    c_run += ch
                    llm_ms += lm.price(beta, ch, int(r.get("P") or 0), out_tok)
                    meas_ms += float(c.get("ms") or 0)
                    m["post_snip"] += int(flags.get(k, False))
                    a["fallbacks"] += int(bool(r.get("fallback")))
                    a["entry_resets"] += int(bool(r.get("entry_reset")))
                    sh = r.get("shadow")
                    if sh and not sh.get("floor"):
                        for key, dst in (("first_kl", "first_kl"), ("tf_kl", "tf_kl"), ("tf_top1", "tf_top1")):
                            v = sh.get(key)
                            if isinstance(v, (int, float)) and not math.isnan(v):
                                m[dst].append(float(v))
                        for b, lst in (sh.get("l1") or {}).items():
                            cls, rest = divmod(int(b), len(AGE_BINS) * OFF_N)
                            if cls == 1:
                                acc = m["dk"][rest // OFF_N]
                                acc[0] += sum(x[0] for x in lst)
                                acc[1] += sum(x[1] for x in lst)
                    elif sh and sh.get("floor"):
                        if isinstance(sh.get("first_kl"), (int, float)):
                            floor_kl.append(float(sh["first_kl"]))
                        for key in ("first_klp", "first_jsd", "first_tvd", "first_exp"):
                            if isinstance(sh.get(key), (int, float)):
                                floor_part[key].append(float(sh[key]))
                    d = r.get("dec")
                    if isinstance(d, dict):
                        n_dec += 1
                        decisions[arm].append({"spec": row["trial"], "call": k, "block": block, "task": task, **{
                            x: d.get(x) for x in ("m", "u", "recompute", "same", "C_E", "C_O", "ms", "error",
                                                  "floor", *DEC_METRICS)}, "C_served": int(r.get("C") or 0),
                            "charged": ch})
                        if sh and not sh.get("floor") and not d.get("recompute") and not d.get("error"):
                            rel_pairs.append({"arm": arm, "spec": row["trial"], "call": k,
                                              "oracle_kl": sh.get("first_kl"), "oracle_jsd": sh.get("first_jsd"),
                                              **{x: d.get(x) for x in DEC_METRICS}})
                a["orphans"] += sum(n - 1 for n in n_rec.values() if n > 1) + \
                    sum(1 for k in last if k not in joined)
                wall = agent_wall(tdir)
                tool_s = max(0.0, (wall or 0.0) - meas_ms / 1000)
                if wall is not None and wall - meas_ms / 1000 < 0:
                    a["tool_clipped"] += 1
                m["runs"] += 1
                m["C_run"].append(c_run)
                m["llm_s"].append(llm_ms / 1000)
                m["tool_s"].append(tool_s)
                m["job_s"].append(llm_ms / 1000 + tool_s)
                m["dec_runs"].append(n_dec)
                trial_list.append({"arm": arm, "block": block, "task": task, "spec": row["trial"], "job": row["job"],
                                   "started": row.get("started"), "success": collect.success(row),
                                   "status": row.get("s15_status")})
            per_arm[arm] = m
            acct[arm] = a
        # accuracy
        acc = {}
        for arm in arms:
            rs = [r for (a_, _b, _t), r in trials.items() if a_ == arm]
            by_task = defaultdict(dict)
            for (a_, b, t), r in trials.items():
                if a_ == arm:
                    by_task[t][b] = collect.success(r)
            reps = sorted({b for (_a, b, _t) in trials})
            full = [v for v in by_task.values() if all(b in v for b in reps)] if len(reps) >= 3 else []
            pass3 = st.mean(float(all(v[b] for b in reps[:3])) for v in full) if full else None
            if arm == "prefix":
                d = {"tasks": None, "blocks": None, "diff": None, "lo": None, "hi": None}
            elif arm == "prefix_aa":
                d = diff_ci(trials, "prefix_aa", ["prefix"])
            else:
                d = diff_ci(trials, arm, control)
            acc[arm] = {"pass1": st.mean(collect.success(r) for r in rs) if rs else None, "pass3": pass3,
                        "pass3_tasks": len(full), "diff": d,
                        "timeouts": sum(1 for r in rs if r.get("s15_status") == "timeout"),
                        "at_budget": sum(1 for r in rs if r.get("s15_status") == "budget"),
                        "lead_calls": st.mean(r.get("lead_calls") or 0 for r in rs) if rs else None}
        table = {}
        for arm in arms:
            m, ac = per_arm[arm], acc[arm]
            dk = [m["dk"][i][0] / m["dk"][i][1] if i in m["dk"] and m["dk"][i][1] > 0 else None
                  for i in range(len(AGE_BINS))]
            vals = {"arm": LABEL.get(arm, arm), "runs": m["runs"], "calls": m["calls"],
                    "c_call": m["C"] / m["calls"] if m["calls"] else None,
                    "c_run": st.mean(m["C_run"]) if m["C_run"] else None,
                    "llm_s": st.mean(m["llm_s"]) if m["llm_s"] else None,
                    "tool_s": st.mean(m["tool_s"]) if m["tool_s"] else None,
                    "job_s": st.mean(m["job_s"]) if m["job_s"] else None,
                    "comp_share": m["C"] / m["P"] if m["P"] else None, "post_snip": m["post_snip"],
                    "diff": ac["diff"]["diff"], "pass1": ac["pass1"], "pass3": ac["pass3"],
                    "ci": (ac["diff"]["lo"], ac["diff"]["hi"]),
                    "first_kl": st.mean(m["first_kl"]) if m["first_kl"] else None,
                    "reply_kl": st.mean(m["tf_kl"]) if m["tf_kl"] else None,
                    "reply_top1": st.mean(m["tf_top1"]) if m["tf_top1"] else None,
                    "dk": dk if any(x is not None for x in dk) else [None] * len(AGE_BINS)}
            table[arm] = {"label": LABEL.get(arm, arm), "cells": {k: cell(vals[k], f) for k, _h, f in COLUMNS},
                          "n": {"probes_first_kl": len(m["first_kl"]), "probes_tf": len(m["tf_kl"]),
                                "pass3_tasks": ac["pass3_tasks"], "diff_tasks": ac["diff"]["tasks"],
                                "diff_blocks": ac["diff"]["blocks"], "df": ac["diff"].get("df"),
                                "t": ac["diff"].get("t"), "se": ac["diff"].get("se"),
                                "sum_C": m["C"], "sum_P": m["P"], "timeouts": ac["timeouts"],
                                "at_budget": ac["at_budget"], "lead_calls": ac["lead_calls"],
                                "decisions_per_run": st.mean(m["dec_runs"]) if m["dec_runs"] else None}}
        timed_out = {(b_, t_) for (_a, b_, t_), r in trials.items() if r.get("s15_status") == "timeout"}
        fell_back = set()
        for (a_, b_, t_), row in trials.items():
            if any(x.get("fallback") for x in by_spec.get(row["trial"], []) if x.get("ev") == "call"):
                fell_back.add((b_, t_))
        sens = {}
        for name, skip in (("without blocks where any arm hit the agent timeout", timed_out),
                           ("without blocks where any call fell back to prefix caching", fell_back)):
            if not skip:
                continue
            rows_ = {}
            for arm in arms:
                if arm == "prefix":
                    continue
                ctl = ["prefix"] if arm == "prefix_aa" else control
                r = er.paired(trials, arm, ctl, skip_blocks=skip)
                d = {"tasks": r["tasks"], "blocks": r["blocks"], "diff": r["diff"], "lo": None, "hi": None}
                if r["diff"] is not None:
                    t = t_ppf(0.95, r["tasks"] - 1)
                    d.update(lo=r["diff"] - t * r["se"], hi=r["diff"] + t * r["se"])
                rows_[arm] = d
            sens[name] = {"skipped_blocks": len(skip), "rows": rows_}
        out["benches"][bench] = {"fit": fit, "table": table, "accounting": acct, "sensitivity": sens,
                                 "decisions": decision_summary(decisions, per_arm),
                                 "relationship": relationship(rel_pairs),
                                 "floor": {"first_kl": st.mean(floor_kl) if floor_kl else None, "n": len(floor_kl),
                                           **{k: st.mean(v) for k, v in floor_part.items() if v}},
                                 "trial_set": sorted(trial_list, key=lambda x: (x["arm"], x["block"], x["task"])),
                                 "decision_records": {arm: v for arm, v in decisions.items()},
                                 "relationship_pairs": rel_pairs}
    return out


def decision_summary(decisions: dict, per_arm: dict) -> dict:
    out = {}
    for arm, ds in decisions.items():
        ok = [d for d in ds if not d.get("error")]
        rec = [d for d in ok if d.get("recompute")]
        runs = per_arm[arm]["runs"]
        overhead = sum(int(d.get("C_O") or 0) + (int(d.get("C_E") or 0) if d.get("recompute") else 0) for d in ok)
        floors = [d["floor"]["jsd"] for d in ok if isinstance(d.get("floor"), dict) and d["floor"].get("jsd") is not None]
        out[arm] = {"decisions": len(ds), "errors": len(ds) - len(ok), "same": sum(1 for d in ok if d.get("same")),
                    "decisions_per_run": len(ds) / runs if runs else None,
                    "mean_m": st.mean(d["m"] for d in ok) if ok else None,
                    "recompute_rate": len(rec) / len(ok) if ok else None,
                    "recomputes_per_run": len(rec) / runs if runs else None,
                    "overhead_tokens_per_run": overhead / runs if runs else None,
                    "overhead_share": overhead / per_arm[arm]["C"] if per_arm[arm]["C"] else None,
                    "mean_ms": st.mean(float(d["ms"]) for d in ok if d.get("ms") is not None) if ok else None,
                    "floor_jsd": st.mean(floors) if floors else None, "floor_n": len(floors),
                    "means": {x: st.mean(float(d[x]) for d in ok if d.get(x) is not None) if ok else None
                              for x in DEC_METRICS}}
    return out


def relationship(pairs: list[dict]) -> dict:
    out = {"n": len(pairs), "spearman": {}, "quintiles": []}
    for x in DEC_METRICS:
        xs = [(float(p[x]), float(p["oracle_kl"])) for p in pairs
              if isinstance(p.get(x), (int, float)) and isinstance(p.get("oracle_kl"), (int, float))]
        rho = spearman([a for a, _ in xs], [b for _, b in xs])
        out["spearman"][x] = {"n": len(xs), "rho": rho, "ci90": fisher_ci(rho, len(xs))}
    js = sorted(((float(p["jsd"]), float(p["oracle_kl"]), p["spec"], p["call"]) for p in pairs
                 if isinstance(p.get("jsd"), (int, float)) and isinstance(p.get("oracle_kl"), (int, float))),
                key=lambda v: (v[0], v[2], v[3]))
    n = len(js)
    if n >= 5:
        for q in range(5):
            part = js[q * n // 5:(q + 1) * n // 5]
            out["quintiles"].append({"n": len(part), "mean_jsd": st.mean(v[0] for v in part),
                                     "mean_oracle_kl": st.mean(v[1] for v in part)})
    return out


# -- rendering -------------------------------------------------------------------------------------------------

def render_table(b: dict, arms: list[str]) -> list[str]:
    lines = ["| " + " | ".join(h for _k, h, _f in COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for arm in arms:
        cells = b["table"][arm]["cells"]
        lines.append("| " + " | ".join(cells[k]["text"] for k, _h, _f in COLUMNS) + " |")
    return lines


def footnotes(b: dict, bench: str, arms: list[str]) -> list[str]:
    fit = b["fit"]
    beta = fit["beta"]
    t = b["table"]
    p3 = {a: t[a]["n"]["pass3_tasks"] for a in arms}
    fl = b["floor"]
    notes = [
        f"- Time model ({fit['mode']} fit, {fit['n']} baseline lead calls of `{BASELINE}`): ms = {beta[0]:.0f} + "
        f"{beta[1] * 1e3:.3f}·C/1e3 + {beta[2] * 1e3:.4f}·C·P/1e6 + {beta[3]:.1f}·out; MdAPE {fit['mdape']:.0%}. "
        "Reused rows cost nothing in the model, so EPIC's time is a lower bound."
        + (" On this benchmark the fit gives prefill no weight: computed tokens do not change the model's time."
           if beta[1] == 0 and beta[2] == 0 else ""),
        "- tool+harness also holds the runs' other model calls (sub-agents, summaries) and failed attempts.",
        f"- pass^3 over the tasks with a valid trial in all 3 replicates: {', '.join(f'{a} {n}' for a, n in p3.items())}.",
        "- Probe columns: shadow probes of curated calls with stale rows (1 in 32, plus every shift of at least 8,000 "
        "tokens); a recomputed call has no stale rows and is never probed. Probes per arm: "
        + ", ".join(f"{a} {t[a]['n']['probes_first_kl']}" for a in arms) + ".",
        f"- Noise floor (shadow probes of the prefix arms, an exact cache): first-token KL "
        f"{fmt_value(fl.get('first_kl'), '.4f')} over {fl.get('n', 0)} probes.",
        "- The difference is against the control (mean of baseline and prefix_aa per block); the prefix_aa row is "
        "prefix_aa minus baseline (A/A).",
    ]
    return notes


def render_secondary(b: dict, bench: str, arms: list[str]) -> list[str]:
    lines = []
    dec = b["decisions"]
    if dec:
        lines += [f"#### Decisions of the recompute arm ({BENCH_NAME[bench]})", "",
                  "| arm | decisions / run | mean JSD (= recompute probability) | recompute rate | recomputes / run | "
                  "decision overhead tokens / run (share of computed) | decision time | noise floor (JSD, n) | "
                  "failed decisions |", "|---|---|---|---|---|---|---|---|---|"]
        for arm, d in dec.items():
            lines.append(f"| {LABEL.get(arm, arm)} | {fmt_value(d['decisions_per_run'], '.3f')} | "
                         f"{fmt_value(d['mean_m'], '.4f')} | {fmt_value(d['recompute_rate'], '.2%')} | "
                         f"{fmt_value(d['recomputes_per_run'], '.3f')} | {fmt_value(d['overhead_tokens_per_run'], ',.0f')} "
                         f"({fmt_value(d['overhead_share'], '.1%')}) | "
                         f"{'—' if d['mean_ms'] is None else f'{d['mean_ms'] / 1000:.2f} s'} | "
                         f"{fmt_value(d['floor_jsd'], '.4f')} ({d['floor_n']}) | {d['errors']} |")
        lines += ["", "Descriptive, for the future TVD and EXP arms: the same decisions' other metrics (their mean is the "
                      "recompute rate each would have produced on these calls).", "",
                  "| arm | " + " | ".join(DEC_METRICS) + " |", "|---|" + "---|" * len(DEC_METRICS)]
        for arm, d in dec.items():
            lines.append(f"| {LABEL.get(arm, arm)} | " + " | ".join(fmt_value(d["means"][x], ".4f") for x in DEC_METRICS) + " |")
        lines.append("")
    rel = b["relationship"]
    if rel["n"]:
        lines += [f"#### Decision metrics against the real error ({rel['n']} probed decision calls)", "",
                  "Spearman ρ between each decision metric (original vs after-EPIC) and the shadow probe's first-token "
                  "KL of the same call (fresh recompute vs after-EPIC), with an approximate 90 % interval (Fisher z).", "",
                  "| metric | n | ρ | 90% [lo, hi] |", "|---|---|---|---|"]
        for x in DEC_METRICS:
            s = rel["spearman"][x]
            lo, hi = s["ci90"]
            lines.append(f"| {x} | {s['n']} | {fmt_value(s['rho'], '+.3f')} | "
                         f"{fmt_value((lo, hi), '[+.3f, +.3f]') if lo is not None else '—'} |")
        if rel["quintiles"]:
            lines += ["", "| JSD quintile | n | mean JSD | mean first-token KL (fresh vs arm) |", "|---|---|---|---|"]
            for i, q in enumerate(rel["quintiles"]):
                lines.append(f"| {i + 1} | {q['n']} | {q['mean_jsd']:.4f} | {q['mean_oracle_kl']:.4f} |")
        lines.append("")
    for name, sv in (b.get("sensitivity") or {}).items():
        lines += [f"#### Sensitivity ({BENCH_NAME[bench]}): resolve difference {name} ({sv['skipped_blocks']} blocks "
                  "left out)", "", "| arm | tasks | blocks | difference | 90% CI |", "|---|---|---|---|---|"]
        for arm, d in sv["rows"].items():
            lines.append(f"| {LABEL.get(arm, arm)}{' (vs baseline, A/A)' if arm == 'prefix_aa' else ''} | {d['tasks']} | "
                         f"{d['blocks']} | {fmt_value(d['diff'], '+.3f')} | {fmt_value((d['lo'], d['hi']), '[+.3f, +.3f]')} |")
        lines.append("")
    acct = b["accounting"]
    lines += [f"#### Call accounting ({BENCH_NAME[bench]})", "",
              "| arm | aborted records (P, C) | fallbacks | entry resets | lead calls without a record | records not "
              "joined | failed attempts | non-lead calls | runs with tool time clipped at 0 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for arm in arms:
        a = acct[arm]
        lines.append(f"| {LABEL.get(arm, arm)} | {a['aborted_records']} ({a['aborted_P']:,}, {a['aborted_C']:,}) | "
                     f"{a['fallbacks']} | {a['entry_resets']} | {a['missing']} | {a['orphans']} | "
                     f"{a['errored_attempts']} | {a['non_lead_calls']} | {a['tool_clipped']} |")
    lines.append("")
    gl = gates_lines(bench)
    if gl:
        lines += gl
    return lines


def gates_lines(bench: str, phase: str = "e4") -> list[str]:
    files = sorted(glob.glob(str(ROOT / "runs" / "epic" / f"gates_{SERVER}_{bench}_{phase}r[0-9].json")))
    if not files:
        return []
    lines = [f"#### Validity gates ({BENCH_NAME[bench]}, epic/pilot_gates.py)", "",
             "| replicate | trials | E-0 | preemptions | fallbacks+resets / post-snip | records | ranks | decisions "
             "(recompute rate) | failed decisions | pass |", "|---|---|---|---|---|---|---|---|---|---|"]
    for f in files:
        rep = json.loads(Path(f).read_text())
        g, ps = rep["gates"], rep["pass"]
        dec = [(arm, x["decisions"]) for arm, x in rep["arms"].items() if x.get("decisions")]
        dtext = ", ".join(f"{d['n']} ({fmt_value(d.get('recompute_rate'), '.1%')})" for _a, d in dec) or "—"
        lines.append(f"| {rep['phase']} | {rep['cost']['trials']} | {fmt_value(rep['e0']['share_ge_99'], '.1%')} | "
                     f"{g['preemptions']} | {fmt_value(g['fallback_reset_share'], '.2%')} | "
                     f"{'ok' if ps.get('one_record_per_call') else 'FAIL'} | "
                     f"{'ok' if ps.get('ranks_consistent') else 'FAIL'} | {dtext} | {g.get('decision_errors', 0)} | "
                     f"{', '.join(k for k, v in ps.items() if not v) or 'all'}{' fail' if not all(ps.values()) else ''} |")
    return lines + [""]


def stage_done(phase: str) -> bool:
    return STAGE_LOG.is_file() and f"stage {phase} done" in STAGE_LOG.read_text(errors="replace")


def file_hashes(paths: list[str]) -> dict:
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).is_file()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="e4")
    ap.add_argument("--benches", default=",".join(BENCHES))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--log", default=str(ROOT / "runs" / "epic" / f"{SERVER}.calls.jsonl"))
    ap.add_argument("--fit", default="sorted", choices=("sorted", "legacy"))
    ap.add_argument("--out-dir", default=str(ROOT / "report"))
    ap.add_argument("--tasks", default="", help="task list per benchmark, default run/state/<phase>_tasks_<bench>.json")
    ap.add_argument("--force", action="store_true", help="show outcome columns before the stage has ended")
    a = ap.parse_args()
    benches = a.benches.split(",")
    arms = a.arms.split(",")
    tl = {b: json.loads((ROOT / (a.tasks.format(bench=b) if a.tasks else f"run/state/{a.phase}_tasks_{b}.json"))
                        .read_text())["tasks"] for b in benches}
    if not a.force and not stage_done(a.phase):
        raise SystemExit(f"stage {a.phase} has not ended ({STAGE_LOG.name}): outcomes stay unread (--force overrides)")
    js = build(a.phase, benches, arms, tl, Path(a.log), a.fit)
    js["meta"] = {"generated_by": "report/e4_report.py", "fit": a.fit,
                  "code": file_hashes(["report/e4_report.py", "report/epic_report.py", "report/latency_model.py",
                                       "report/collect.py"]),
                  "tasks": {b: hashlib.sha256(json.dumps(tl[b]).encode()).hexdigest() for b in benches}}
    out_dir = Path(a.out_dir)
    tag = f"{a.phase}_{SERVER}"
    over = [f"## e4: divergence-triggered full recomputation ({SERVER}, phase {a.phase})", "",
            "Arms: " + ", ".join(f"{LABEL.get(x, x)} (`{x}`)" for x in arms) + ". Blocks: the profile's first 30 tasks "
            "per benchmark × 3 replicates, all arms of a block started together. Control: the mean of baseline and "
            "prefix_aa per block. Every value below is generated by `report/e4_report.py` and recomputed cell by cell "
            "by `report/e4_verify.py`.", ""]
    for bench in benches:
        b = js["benches"][bench]
        part = [f"### {BENCH_NAME[bench]} ({bench})", ""] + render_table(b, arms) + [""] + footnotes(b, bench, arms) + [""]
        over += part
        (out_dir / f"{tag}_{bench}.md").write_text("\n".join(
            [f"### e4 on {BENCH_NAME[bench]}: secondary tables", ""] + render_secondary(b, bench, arms)) + "\n")
    notes = ROOT / "runs" / "epic" / f"notes_{SERVER}_{a.phase}.md"
    if notes.is_file():
        over += [notes.read_text().rstrip(), ""]
    (out_dir / f"{tag}.md").write_text("\n".join(over) + "\n")
    (out_dir / f"{tag}.json").write_text(json.dumps(js, indent=1, default=str) + "\n")
    print("\n".join(over))


if __name__ == "__main__":
    main()
