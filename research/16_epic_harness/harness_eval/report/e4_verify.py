#!/usr/bin/env python3
"""Independent check of e4's tables (plan ~/.claude/plans/whenever-we-do-shift-epic-misty-muffin.md §5).

    .venv/bin/python report/e4_verify.py [--json report/e4_minimax-m2.7-epic.json]     # every cell, invariants
    .venv/bin/python report/e4_verify.py --regress e3 | e2                          # e4_report on published data

Default mode recomputes every cell of report/e4_report.py's JSON from the raw files -- trial results, the agents'
call logs, the server log (the byte prefix the report read, checked by its sha256), the baseline's calls -- with
its own implementation of every step: infrastructure classes, first-valid selection, the call join, post-snip,
charged tokens, the time model's least squares, the t quantile (Simpson integration of the density, not the
incomplete beta), D^K decoded from each probe's own bin metadata, and the text formatting. A cell passes when its
text is identical. It also checks invariants and the decision records' contract. Writes report/verify_e4.json and
report/verify_e4.md; exits 1 on any mismatch.

It imports only the standard library and numpy (tests/test_e4.py checks that); --regress imports report/e4_report.py
on purpose, to run it on rounds whose tables are already published.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import itertools
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
SERVER = "minimax-m2.7-epic"
BASELINE = "minimax-m2.7"
# collect.py's infrastructure classes, restated (tests/test_e4.py checks they agree)
INFRA_EXC = {"CancelledError", "KeyboardInterrupt", "RuntimeError", "EnvironmentStartError", "DockerError",
             "BuildError", "SandboxBuildFailedError", "HealthcheckError", "ConnectionError", "OSError"}
CONN = ("APIConnectionError", "Connection error", "Connection refused", "InternalServerError", "ServiceUnavailable")
EXCLUDED_PHASES = ("p1x", "p3s", "p4x")
VERIFIER_NET = ("failed to download https://github.com/astral-sh/uv",)
VERIFIER_NET_PHASES = ("e4",)
ORDER = ["arm", "runs", "calls", "c_call", "c_run", "llm_s", "tool_s", "job_s", "comp_share", "post_snip", "diff",
         "pass1", "pass3", "ci", "first_kl", "reply_kl", "reply_top1", "dk"]


# -- independent pieces -------------------------------------------------------------------------------------------

def jl(path: Path) -> list[dict]:
    out = []
    if not path.is_file():
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def text_of(v, fmt: str) -> str:
    """The formatting rules of the plan (§4), written out again."""
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if fmt == "s":
        return str(v)
    if fmt == "d":
        return str(int(v))
    if fmt == ",.0f":
        return f"{v:,.0f}"
    if fmt == ".0f s":
        return f"{v:.0f} s"
    if fmt == ".1%":
        return f"{100 * v:.1f}%"
    if fmt == ".2%":
        return f"{100 * v:.2f}%"
    if fmt == ".3f":
        return f"{v:.3f}"
    if fmt == ".4f":
        return f"{v:.4f}"
    if fmt == "+.3f":
        s = ("+" if v >= 0 else "-") + f"{abs(v):.3f}"
        return "+0.000" if s == "-0.000" else s
    if fmt == "[+.3f, +.3f]":
        lo, hi = v
        if lo is None or hi is None:
            return "—"
        return f"[{text_of(lo, '+.3f')}, {text_of(hi, '+.3f')}]"
    if fmt == ".3f/":
        return " / ".join("—" if x is None else f"{x:.3f}" for x in v)
    raise ValueError(fmt)


def t_quantile(q: float, df: int) -> float:
    """Student t quantile: Simpson integration of the density from 0, then bisection."""
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)

    def cdf(t: float) -> float:
        n = 4000
        h = t / n
        s = 0.0
        for i in range(n + 1):
            x = i * h
            w = 1 if i in (0, n) else (4 if i % 2 else 2)
            s += w * (1 + x * x / df) ** (-(df + 1) / 2)
        return 0.5 + c * s * h / 3

    lo, hi = 0.0, 1.0
    while cdf(hi) < q:
        hi *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if cdf(mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def iso(x) -> float | None:
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp() if x else None


def infra_kills() -> set[str]:
    out = set()
    for x in jl(ROOT / "runs" / "disk_guard_kills.jsonl"):
        c = str(x.get("container", ""))
        out.add(c[:-len("__env-main-1")] if c.endswith("__env-main-1") else c)
    return out


def trial_rows(bench: str, arms: list[str], phase: str, tasks: list[str]) -> list[dict]:
    kills = infra_kills()
    rows = []
    for arm in arms:
        for res in sorted(glob.glob(str(ROOT / "jobs" / f"{SERVER}+{arm}" / bench / "*" / "*" / "result.json"))):
            tdir = Path(res).parent
            job = tdir.parent.name
            ph = job.split("__")[0]
            if ph in EXCLUDED_PHASES:
                continue
            block = re.sub(r"(b\d+)?(x\d+)?$", "", ph)
            if not re.fullmatch(re.escape(phase) + r"r\d+", block):
                continue
            try:
                r = json.loads(Path(res).read_text())
            except json.JSONDecodeError:
                continue
            if "task_name" not in r:
                continue
            task = r["task_name"].split("/")[-1]
            if task not in tasks:
                continue
            s15 = tdir / "agent" / "s15"
            sres = json.loads((s15 / "result.json").read_text()) if (s15 / "result.json").is_file() else {}
            exc = r.get("exception_info") or {}
            err = str(sres.get("error") or "")
            hist = s15 / "history.json"
            broken = hist.is_file() and "agent side closed the connection" in hist.read_text(errors="replace")
            vout = tdir / "verifier" / "test-stdout.txt"
            vnet = ph.startswith(VERIFIER_NET_PHASES) and vout.is_file() and \
                any(sig in vout.read_text(errors="replace") for sig in VERIFIER_NET)
            infra = bool(broken or tdir.name.lower() in kills or exc.get("exception_type") in INFRA_EXC or
                         (sres.get("status") in ("error", "crash") and any(cn in err for cn in CONN)) or vnet)
            reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
            status = "timeout" if exc.get("exception_type") == "AgentTimeoutError" else sres.get("status")
            rows.append({"arm": arm, "block": block, "task": task, "spec": tdir.name, "tdir": tdir, "job": job,
                         "started": r.get("started_at"), "finished": r.get("finished_at"), "infra": infra,
                         "ok": 1.0 if (reward or 0) >= 1.0 else 0.0, "status": status, "res": r})
    return rows


def first_valid(rows: list[dict]) -> dict:
    out = {}
    for r in sorted(rows, key=lambda r: r["started"] or ""):
        if r["infra"] or not r["finished"]:
            continue
        out.setdefault((r["arm"], r["block"], r["task"]), r)
    return out


def least_squares_model(rows: list[tuple]) -> np.ndarray:
    """Non-negative-coefficient best subset fit of ms ~ 1 + C + C*P/1e3 + out (latency_model's estimator)."""
    X = np.array([[1.0, c, c * p / 1e3, o] for c, p, o, _m, _t in rows])
    y = np.array([m for _c, _p, _o, m, _t in rows])
    best, beta = None, np.zeros(4)
    for k in range(1, 5):
        for cols in itertools.combinations(range(4), k):
            sub = X[:, list(cols)]
            b = np.linalg.solve(sub.T @ sub, sub.T @ y) if np.linalg.matrix_rank(sub) == len(cols) else None
            if b is None or (b < 0).any():
                continue
            sse = float(((sub @ b - y) ** 2).sum())
            if best is None or sse < best:
                best, beta = sse, np.zeros(4)
                beta[list(cols)] = b
    return beta


def baseline_rows(bench: str) -> list[tuple]:
    rows = []
    for f in sorted(glob.glob(str(ROOT / "jobs" / BASELINE / bench / "q*" / "*" / "agent" / "s15" / "calls.jsonl"))):
        trial = Path(f).parts[-4]
        for c in jl(Path(f)):
            u = c.get("usage")
            if c.get("purpose") != "lead" or not u or c.get("ms") is None:
                continue
            cached = int(u.get("cache_read_input_tokens") or 0)
            p = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
            rows.append((p - cached, p, int(u.get("output_tokens") or 0), float(c["ms"]), trial))
    return rows


def model_ms(beta, c, p, o) -> float:
    return float(beta[0] + beta[1] * c + beta[2] * c * p / 1e3 + beta[3] * o)


def mdape_3fold(rows: list[tuple]) -> float:
    fold = [int(hashlib.sha256(t.encode()).hexdigest(), 16) % 3 for *_r, t in rows]
    errs = []
    for k in range(3):
        tr = [r for r, f in zip(rows, fold) if f != k]
        b = least_squares_model(tr)
        for r, f in zip(rows, fold):
            if f == k:
                errs.append(abs(model_ms(b, r[0], r[1], r[2]) - r[3]) / max(r[3], 1.0))
    errs.sort()
    n = len(errs)
    return errs[n // 2] if n % 2 else (errs[n // 2 - 1] + errs[n // 2]) / 2


def ranks(v: list[float]) -> list[float]:
    idx = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        avg = (i + j + 2) / 2
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    a, b = np.array(ranks(x)), np.array(ranks(y))
    a, b = a - a.mean(), b - b.mean()
    den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum()) / den if den > 0 else None


# -- recomputation of one benchmark ---------------------------------------------------------------------------

def records_by_spec(log: Path, nbytes: int, sha: str, specs: set[str]) -> dict[str, list[dict]]:
    h = hashlib.sha256()
    out: dict[str, list[dict]] = {}
    with open(log, "rb") as f:
        data = f.read(nbytes)
    h.update(data)
    if h.hexdigest() != sha:
        raise SystemExit(f"the log's first {nbytes} bytes do not match the report's sha256")
    for raw in data.splitlines():
        try:
            x = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if x.get("spec") in specs:
            out.setdefault(x["spec"], []).append(x)
    return out


def recompute(bench: str, arms: list[str], phase: str, tasks: list[str], js: dict, fit_mode: str) -> dict:
    trials = first_valid(trial_rows(bench, arms, phase, tasks))
    specs = {r["spec"] for r in trials.values()}
    recs = records_by_spec(ROOT / js["log"]["path"], js["log"]["bytes"], js["log"]["sha256"], specs)
    rows = baseline_rows(bench)
    if fit_mode == "sorted":
        beta = least_squares_model(rows)
        mdape = mdape_3fold(rows)
    else:
        beta, mdape = np.array(js["benches"][bench]["fit"]["beta"]), js["benches"][bench]["fit"]["mdape"]
    cols: dict[str, dict] = {}
    inv: list[str] = []
    dec_bad: list = []
    per_block_success: dict = {}
    for arm in arms:
        mine = sorted(((k, r) for k, r in trials.items() if k[0] == arm), key=lambda kv: kv[0])
        n_calls = sum_c = sum_p = post = 0
        c_runs, llm, tool, job = [], [], [], []
        fk, tk, tt = [], [], []
        dk_num = [0.0] * 6
        dk_den = [0.0] * 6
        decs: list[dict] = []
        for (a_, block, task), r in mine:
            per_block_success[(arm, block, task)] = r["ok"]
            ses = sorted((x for x in recs.get(r["spec"], []) if x.get("ev") == "call" and not x.get("aborted")),
                         key=lambda x: (int(x["call"]), float(x.get("t") or 0)))
            # the session's previous sequence, walked independently
            prev = None
            edited_by: dict[int, bool] = {}
            last: dict[int, dict] = {}
            for x in ses:
                e = prev is not None and x.get("exact") is not None and \
                    x["exact"] < prev["P"] + max(0, (prev.get("gen") or 0) - 1)
                if arm not in ("prefix", "prefix_aa") and "edited" in x and x.get("exact") is not None and \
                        bool(x["edited"]) != e and not x.get("recompute"):
                    inv.append(f"{arm} {r['spec']} call {x['call']}: derived post-snip {e} != planner {x['edited']}")
                edited_by[int(x["call"])] = e
                last[int(x["call"])] = x
                if x.get("entry_reset"):
                    prev = None
                elif not (x.get("fallback") and arm not in ("prefix", "prefix_aa")):
                    prev = x
            try:                            # the seed key's task: the trial config's task directory name
                task_key = Path(json.loads((r["tdir"] / "config.json").read_text())["task"]["path"]).name
            except (OSError, KeyError, TypeError, ValueError):
                task_key = task
            calls = [c for c in jl(r["tdir"] / "agent" / "s15" / "calls.jsonl")
                     if c.get("purpose") == "lead" and c.get("epic") and c.get("response") is not None]
            run_c = 0
            run_ms = meas = 0.0
            for c in calls:
                x = last.get(int(c["epic"]["n"]))
                if x is None:
                    continue
                if x.get("fallback") or ("C" not in x and "ideal_C" not in x):
                    ch = max(0, int(x.get("P") or 0) - int(x.get("apc_hit") or 0))
                elif "ideal_C" in x:
                    ch = int(x["ideal_C"])
                else:
                    ch = int(x["C_charged"]) if "C_charged" in x else int(x["C"])
                    d = x.get("dec")
                    if isinstance(d, dict) and not d.get("error"):
                        want = int(x["C"]) + int(d["C_O"]) + (int(d["C_E"]) if d.get("recompute") else 0)
                        if ch != want:
                            dec_bad.append((r["spec"], x["call"], "C_charged", ch, want))
                n_calls += 1
                sum_c += ch
                sum_p += int(x.get("P") or 0)
                run_c += ch
                run_ms += model_ms(beta, ch, int(x.get("P") or 0), int((c.get("usage") or {}).get("output_tokens") or 0))
                meas += float(c.get("ms") or 0)
                post += int(edited_by.get(int(c["epic"]["n"]), False))
                sh = x.get("shadow")
                if sh and not sh.get("floor"):
                    for key, lst in (("first_kl", fk), ("tf_kl", tk), ("tf_top1", tt)):
                        v = sh.get(key)
                        if isinstance(v, (int, float)) and v == v:
                            lst.append(float(v))
                    bins = sh.get("bins") or {}
                    n_age = len(bins.get("age") or (0, 1, 2, 4, 8, 16))
                    n_off = len(bins.get("offset") or (0, 32, 64, 128, 512))
                    for b, groups in (sh.get("l1") or {}).items():
                        cls, rest = divmod(int(b), n_age * n_off)
                        if cls == (bins.get("classes") or ["exact", "reused"]).index("reused"):
                            age = rest // n_off
                            dk_num[age] += sum(g[0] for g in groups)
                            dk_den[age] += sum(g[1] for g in groups)
                # the decision contract (recompute arms)
                d = x.get("dec")
                if isinstance(d, dict):
                    decs.append(d)
                if re.search(r"r(jsd|tvd|exp)$", arm):
                    if edited_by.get(int(c["epic"]["n"])) and not x.get("fallback") and not isinstance(d, dict):
                        dec_bad.append((r["spec"], x["call"], "missing decision"))
                    if isinstance(d, dict) and not d.get("error"):
                        metric = re.search(r"r(jsd|tvd|exp)$", arm).group(1)
                        u = int(hashlib.sha256(f"{block}|{task_key}|recompute|{x['call']}".encode()).hexdigest()[:8],
                                16) / 2 ** 32
                        rule = bool(d["recompute"]) == (d["u"] < d["m"]) or bool(d.get("forced"))
                        if d["m"] != d[metric] or abs(d["exp"] - (1 - math.exp(-d["kl"]))) > 1e-9 or not rule:
                            dec_bad.append((r["spec"], x["call"], "rule", d.get("m"), d.get("u"), d.get("recompute")))
                        if d["u"] != u:
                            dec_bad.append((r["spec"], x["call"], "u is not sha256(block|task|recompute|call)", d["u"], u))
                        if not (0 <= d["jsd"] <= 1 and 0 <= d["tvd"] <= 1 and 0 <= d["exp"] < 1 and 0 <= d["u"] < 1):
                            dec_bad.append((r["spec"], x["call"], "range"))
                        if bool(x.get("recompute")) != bool(d["recompute"]):
                            dec_bad.append((r["spec"], x["call"], "record vs decision"))
                elif isinstance(d, dict) or x.get("recompute"):
                    dec_bad.append((r["spec"], x["call"], "decision on a non-recompute arm"))
            ae = r["res"].get("agent_execution") or {}
            wall = (iso(ae.get("finished_at")) - iso(ae.get("started_at"))) if ae.get("finished_at") and ae.get("started_at") else 0.0
            t_s = max(0.0, wall - meas / 1000)
            c_runs.append(run_c)
            llm.append(run_ms / 1000)
            tool.append(t_s)
            job.append(run_ms / 1000 + t_s)
        dk = [dk_num[i] / dk_den[i] if dk_den[i] > 0 else None for i in range(6)]
        mean = lambda v: sum(v) / len(v) if v else None              # noqa: E731
        cols[arm] = {"runs": len(mine), "calls": n_calls, "c_call": sum_c / n_calls if n_calls else None,
                     "c_run": mean(c_runs), "llm_s": mean(llm), "tool_s": mean(tool), "job_s": mean(job),
                     "comp_share": sum_c / sum_p if sum_p else None, "post_snip": post,
                     "first_kl": mean(fk), "reply_kl": mean(tk), "reply_top1": mean(tt),
                     "dk": dk if any(v is not None for v in dk) else [None] * 6,
                     "_sum_c": sum_c, "_c_runs": c_runs, "_llm": llm, "_tool": tool, "_job": job, "_decs": decs}
    # accuracy, recomputed
    reps = sorted({b for (_a, b, _t) in trials})
    for arm in arms:
        succ = [v for (a_, _b, _t), v in per_block_success.items() if a_ == arm]
        cols[arm]["pass1"] = sum(succ) / len(succ) if succ else None
        tasks_full = []
        for t in sorted({t for (a_, _b, t) in per_block_success if a_ == arm}):
            if len(reps) >= 3 and all((arm, b, t) in per_block_success for b in reps[:3]):
                tasks_full.append(all(per_block_success[(arm, b, t)] for b in reps[:3]))
        cols[arm]["pass3"] = sum(tasks_full) / len(tasks_full) if tasks_full else None
        if arm == "prefix":
            cols[arm]["diff"], cols[arm]["ci"] = None, (None, None)
            continue
        ctl = ["prefix"] if arm == "prefix_aa" else [c for c in ("prefix", "prefix_aa") if c in arms]
        per_task: dict[str, list[float]] = {}
        for (a_, b, t), v in per_block_success.items():
            if a_ != arm or any((c, b, t) not in per_block_success for c in ctl):
                continue
            per_task.setdefault(t, []).append(v - sum(per_block_success[(c, b, t)] for c in ctl) / len(ctl))
        means = [sum(v) / len(v) for v in per_task.values()]
        n = len(means)
        if n < 2:
            cols[arm]["diff"], cols[arm]["ci"] = None, (None, None)
            continue
        mu = sum(means) / n
        sd = math.sqrt(sum((m - mu) ** 2 for m in means) / (n - 1))
        half = t_quantile(0.95, n - 1) * sd / math.sqrt(n)
        cols[arm]["diff"], cols[arm]["ci"] = mu, (mu - half, mu + half)
        cols[arm]["_df"] = n - 1
    return {"cols": cols, "beta": beta, "mdape": mdape, "inv": inv, "dec_bad": dec_bad,
            "trial_set": sorted((a, b, t, r["spec"]) for (a, b, t), r in trials.items())}


FMT = {"arm": "s", "runs": "d", "calls": "d", "c_call": ",.0f", "c_run": ",.0f", "llm_s": ".0f s", "tool_s": ".0f s",
       "job_s": ".0f s", "comp_share": ".1%", "post_snip": "d", "diff": "+.3f", "pass1": ".3f", "pass3": ".3f",
       "ci": "[+.3f, +.3f]", "first_kl": ".4f", "reply_kl": ".4f", "reply_top1": ".2%", "dk": ".3f/"}


def verify(js: dict, task_lists: dict) -> dict:
    out = {"cells": 0, "mismatches": [], "invariants": [], "rounding_boundary": [], "benches": {}}
    fit_mode = js.get("meta", {}).get("fit", "sorted")
    for bench, b in js["benches"].items():
        arms = list(b["table"].keys())
        rc = recompute(bench, arms, js["phase"], task_lists[bench], js, fit_mode)
        # the trial set
        rep_set = sorted((x["arm"], x["block"], x["task"], x["spec"]) for x in b["trial_set"])
        if rep_set != rc["trial_set"]:
            out["mismatches"].append({"bench": bench, "what": "trial set", "report": len(rep_set),
                                      "verify": len(rc["trial_set"])})
        if fit_mode == "sorted":
            if not np.allclose(np.array(b["fit"]["beta"]), rc["beta"], rtol=1e-6, atol=1e-9):
                out["mismatches"].append({"bench": bench, "what": "time model", "report": b["fit"]["beta"],
                                          "verify": rc["beta"].tolist()})
            if abs(b["fit"]["mdape"] - rc["mdape"]) > 1e-9:
                out["mismatches"].append({"bench": bench, "what": "MdAPE", "report": b["fit"]["mdape"],
                                          "verify": rc["mdape"]})
        for arm in arms:
            cells = b["table"][arm]["cells"]
            mine = dict(rc["cols"][arm], arm=cells["arm"]["value"])
            for k in ORDER:
                want = text_of(mine.get(k), FMT[k])
                got = cells[k]["text"]
                out["cells"] += 1
                if want != got:
                    out["mismatches"].append({"bench": bench, "arm": arm, "column": k, "report": got, "verify": want})
            m = rc["cols"][arm]
            # invariants
            if m["calls"] and abs(m["_sum_c"] - (m["c_call"] or 0) * m["calls"]) > 1e-6 * max(1, m["_sum_c"]):
                out["invariants"].append(f"{bench} {arm}: computed/call x calls != sum")
            if m["runs"] and abs(sum(m["_c_runs"]) - m["_sum_c"]) > 1e-6 * max(1, m["_sum_c"]):
                out["invariants"].append(f"{bench} {arm}: per-run sums != total")
            if m["runs"] and max(abs(j - (l_ + t)) for j, l_, t in zip(m["_job"], m["_llm"], m["_tool"])) > 1e-9:
                out["invariants"].append(f"{bench} {arm}: job != LLM + tool")
            if m["pass1"] is not None and abs(m["pass1"] * m["runs"] - round(m["pass1"] * m["runs"])) > 1e-9:
                out["invariants"].append(f"{bench} {arm}: pass@1 x runs not an integer")
            if m.get("diff") is not None and not (m["ci"][0] < m["diff"] < m["ci"][1]):
                out["invariants"].append(f"{bench} {arm}: the difference is outside its interval")
            if m["runs"] > len(task_lists[bench]) * 3:
                out["invariants"].append(f"{bench} {arm}: more runs than blocks")
            if b["table"][arm]["n"].get("df") is not None and m.get("_df") is not None and \
                    b["table"][arm]["n"]["df"] != m["_df"]:
                out["invariants"].append(f"{bench} {arm}: df {b['table'][arm]['n']['df']} != {m['_df']}")
        for arm, rs in (b.get("decisions") or {}).items():
            ds = rc["cols"][arm]["_decs"]
            ok = [d for d in ds if not d.get("error")]
            runs = rc["cols"][arm]["runs"]
            mine_ = {"decisions": len(ds), "errors": len(ds) - len(ok),
                     "recompute_rate": sum(1 for d in ok if d.get("recompute")) / len(ok) if ok else None,
                     "recomputes_per_run": sum(1 for d in ok if d.get("recompute")) / runs if runs else None,
                     "mean_m": sum(d["m"] for d in ok) / len(ok) if ok else None,
                     "overhead_tokens_per_run": sum(int(d.get("C_O") or 0) + (int(d.get("C_E") or 0) if d.get("recompute")
                                                    else 0) for d in ok) / runs if runs else None}
            for k, v in mine_.items():
                out["cells"] += 1
                w = rs.get(k)
                same = (v is None and w is None) or (v is not None and w is not None and abs(v - w) <= 1e-9 * max(1, abs(v)))
                if not same:
                    out["mismatches"].append({"bench": bench, "arm": arm, "decisions": k, "report": w, "verify": v})
        out["invariants"] += rc["inv"]
        if rc["dec_bad"]:
            out["invariants"].append(f"{bench}: {len(rc['dec_bad'])} decision records break the contract, e.g. "
                                     f"{rc['dec_bad'][:3]}")
        # common u across the recompute arms of a block (only when there is more than one)
        out["benches"][bench] = {"arms": arms, "trials": len(rc["trial_set"])}
    out["ok"] = not out["mismatches"] and not out["invariants"]
    return out


# -- regression on published rounds --------------------------------------------------------------------------

def md_table(path: Path, header_start: str) -> dict[str, dict]:
    """{first cell: {header: cell}} of the first markdown table whose header row starts with `header_start`."""
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(header_start):
            heads = [h.strip() for h in line.strip("|").split("|")]
            out = {}
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                cells = [c.strip() for c in row.strip("|").split("|")]
                out[cells[0]] = dict(zip(heads, cells))
            return out
    raise KeyError(f"{path.name}: no table starting {header_start!r}")


def regress(round_: str) -> dict:
    sys.path.insert(0, str(ROOT / "report"))
    import e4_report as rep                                    # the code under test
    res = {"round": round_, "checks": 0, "mismatches": [], "definitional": []}
    if round_ == "e3":
        arms = ["prefix", "prefix_aa", "epic128", "epic128a1", "epic128a4"]
        tl = {"swe": json.loads((ROOT / "run" / "state" / "e3_tasks.json").read_text())["tasks"]}
        js = rep.build("e3", ["swe"], arms, tl, ROOT / "runs" / "epic" / f"{SERVER}.calls.jsonl", fit_mode="legacy")
        b = js["benches"]["swe"]
        lat = md_table(ROOT / "report" / "latency_minimax-m2.7-epic_swe_e3.md", "| arm | runs |")
        per = md_table(ROOT / "report" / "epic_minimax-m2.7-epic_swe_e3.md", "| arm | trials | pass@1 |")
        srv = md_table(ROOT / "report" / "epic_minimax-m2.7-epic_swe_e3.md", "| arm | lead calls logged |")
        dif = md_table(ROOT / "report" / "epic_minimax-m2.7-epic_swe_e3.md", "| arm | tasks | blocks | difference |")
        for arm in arms:
            c = {k: v["text"] for k, v in b["table"][arm]["cells"].items()}
            want = {"runs": lat[arm]["runs"], "calls": lat[arm]["calls"], "c_call": lat[arm]["computed tok / call"],
                    "c_run": lat[arm]["computed tok / run"], "llm_s": lat[arm]["model LLM time / run"],
                    "tool_s": lat[arm]["tool+harness / run"], "job_s": lat[arm]["model job time / run"],
                    "comp_share": srv[arm]["computed / prompt"], "pass1": per[arm]["pass@1"],
                    "pass3": per[arm]["pass^3"], "first_kl": srv[arm]["first-token KL"],
                    "reply_kl": srv[arm]["reply KL"], "reply_top1": srv[arm]["reply top-1"],
                    "dk": srv[arm]["D^K reused by age (0,1,2-3,4-7,8-15,16+)"]}
            if arm in ("prefix", "prefix_aa"):
                res["definitional"].append({"arm": arm, "column": "post_snip", "published": srv[arm]["post-snip calls"],
                                            "e4": c["post_snip"],
                                            "why": "e3 counted the planner's flag, which only EPIC arms log"})
            else:
                want["post_snip"] = srv[arm]["post-snip calls"]
            key = "prefix_aa vs prefix (A/A)" if arm == "prefix_aa" else arm
            if key in dif:
                want["diff"] = dif[key]["difference"]
                want["ci"] = dif[key]["90% two-sided [lo, hi]"]
            for k, v in want.items():
                res["checks"] += 1
                got = c[k]
                pub = v.replace(" ", "") if k == "dk" else v
                mine = got.replace(" ", "") if k == "dk" else got
                if k == "diff":
                    pub, mine = pub.replace("-0.000", "+0.000"), mine
                if pub != mine:
                    res["mismatches"].append({"arm": arm, "column": k, "published": v, "e4_report": got})
    elif round_ == "e2":
        arms = ["prefix", "prefix_aa", "shift", "epic128", "epic128a1"]
        subsets = json.loads((ROOT / "run" / "subsets.json").read_text())
        for bench in ("swepro", "tbench"):
            tl = {bench: subsets[bench]["tasks"][:30]}
            js = rep.build("e2", [bench], arms, tl, ROOT / "runs" / "epic" / f"{SERVER}.calls.jsonl", fit_mode="legacy")
            b = js["benches"][bench]
            lat = md_table(ROOT / "report" / f"latency_minimax-m2.7-epic_{bench}.md", "| arm | runs |")
            srv = md_table(ROOT / "report" / f"epic_minimax-m2.7-epic_{bench}.md", "| arm | lead calls logged |")
            for arm in arms:
                n = b["table"][arm]["cells"]["calls"]["value"]
                acct = b["accounting"][arm]
                lat_calls, srv_calls = int(lat[arm]["calls"]), int(srv[arm]["lead calls logged"])
                res["checks"] += 2
                if n != lat_calls + acct["fallbacks"]:
                    res["mismatches"].append({"bench": bench, "arm": arm, "identity": "calls = latency + fallbacks",
                                              "e4": n, "latency": lat_calls, "fallbacks": acct["fallbacks"]})
                if n != srv_calls - acct["aborted_records"]:
                    res["mismatches"].append({"bench": bench, "arm": arm, "identity": "calls = logged - aborted",
                                              "e4": n, "logged": srv_calls, "aborted": acct["aborted_records"]})
    else:
        raise SystemExit(f"unknown round {round_}")
    res["ok"] = not res["mismatches"]
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "report" / f"e4_{SERVER}.json"))
    ap.add_argument("--regress", default="", choices=("", "e3", "e2"))
    ap.add_argument("--out", default=str(ROOT / "report" / "verify_e4"))
    ap.add_argument("--tasks", default="", help="task list per benchmark ({bench}), default run/state/<phase>_tasks_<bench>.json")
    a = ap.parse_args()
    if a.regress:
        res = regress(a.regress)
        Path(f"{a.out}_regress_{a.regress}.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
        print(json.dumps(res, indent=1, default=str))
        raise SystemExit(0 if res["ok"] else 1)
    js = json.loads(Path(a.json).read_text())
    phase = js["phase"]
    tl = {b: json.loads((ROOT / (a.tasks.format(bench=b) if a.tasks else f"run/state/{phase}_tasks_{b}.json"))
                        .read_text())["tasks"] for b in js["benches"]}
    res = verify(js, tl)
    Path(f"{a.out}.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    lines = [f"e4_verify: {res['cells']} cells recomputed independently, {len(res['mismatches'])} mismatches, "
             f"{len(res['invariants'])} broken invariants ({'OK' if res['ok'] else 'FAILED'})."]
    for x in res["mismatches"][:20]:
        lines.append(f"- mismatch: {json.dumps(x, default=str)}")
    for x in res["invariants"][:20]:
        lines.append(f"- invariant: {x}")
    Path(f"{a.out}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    raise SystemExit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
