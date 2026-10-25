#!/usr/bin/env python3
"""CPU tests of e4's report, verifier, stage and gates (plan whenever-we-do-shift-epic-misty-muffin.md §5).

    /data/fritz/venv/bin/python tests/test_e4.py

The core is a synthetic round (2 tasks x 3 replicates x 4 arms on one benchmark, with an infrastructure failure and
its rerun, an unfinished trial, an aborted and a fallback record, shadow probes and decision records) whose table is
worked out by hand below; report/e4_report.py must produce it, report/e4_verify.py must agree with it cell by cell,
and the verifier must catch a tampered input.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "report"))
sys.path.insert(0, str(ROOT / "epic"))
sys.path.insert(0, str(ROOT / "run"))
import epic_plan as ep  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REP = _load(ROOT / "report" / "e4_report.py", "e4_report_t")
VER = _load(ROOT / "report" / "e4_verify.py", "e4_verify_t")
import collect  # noqa: E402  (the module e4_report uses)


# -- statistics and formatting ---------------------------------------------------------------------------------

def test_t_quantiles_both_implementations():
    known = [(0.95, 29, 1.699127), (0.95, 99, 1.660391), (0.95, 1, 6.313752), (0.95, 2, 2.919986),
             (0.975, 29, 2.045230), (0.95, 1_000_000, 1.644854)]
    for q, df, want in known:
        assert abs(REP.t_ppf(q, df) - want) < 5e-6, (q, df, REP.t_ppf(q, df))
        if df < 1000:
            assert abs(VER.t_quantile(q, df) - want) < 5e-6, (q, df, VER.t_quantile(q, df))
    for df in (3, 10, 29, 57):
        t = REP.t_ppf(0.95, df)
        assert abs(REP.t_cdf(t, df) - 0.95) < 1e-12


def test_spearman_both_implementations():
    x, y = [1, 2, 2, 3], [1, 3, 2, 4]
    want = 4.5 / math.sqrt(4.5 * 5.0)
    for f in (REP.spearman, VER.spearman):
        assert abs(f(x, y) - want) < 1e-12
        assert abs(f([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-12
        assert abs(f([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-12
        assert f([1, 2], [3, 4]) is None


def test_formatting_agrees_between_report_and_verifier():
    rng = np.random.default_rng(0)
    vals = list(rng.normal(size=200)) + [0.0, -0.0, -0.0004, 0.0005, 0.0015, 2.5, 1234.5, 0.12345, 1e-9, 0.99995]
    for fmt in ("d", ",.0f", ".0f s", ".1%", ".2%", ".3f", ".4f", "+.3f"):
        for v in vals:
            v = abs(v) * 1000 if fmt in ("d", ",.0f") else float(v)
            if fmt == "d":
                v = int(v)
            assert REP.fmt_value(v, fmt) == VER.text_of(v, fmt), (fmt, v, REP.fmt_value(v, fmt), VER.text_of(v, fmt))
    assert REP.fmt_value(-0.0002, "+.3f") == "+0.000" == VER.text_of(-0.0002, "+.3f")
    assert REP.fmt_value((-0.0123, 0.0456), "[+.3f, +.3f]") == "[-0.012, +0.046]" == VER.text_of((-0.0123, 0.0456), "[+.3f, +.3f]")
    assert REP.fmt_value([0.1, None, 0.25, None, None, None], ".3f/") == "0.100 / — / 0.250 / — / — / —"
    assert REP.fmt_value(None, ".4f") == "—" == VER.text_of(None, ".4f")


def test_post_snip_rule_matches_the_planner():
    rng = np.random.default_rng(3)
    base = rng.integers(1000, 5000, size=900).tolist()
    gen = [11, 12, 13]
    entry, recs, edited = None, [], {}
    prompts = [base + [9]]
    for k in range(1, 10):
        prev = prompts[-1] + gen[:2]
        if k == 5:                                     # an append-only call
            prompts.append(prev + [4000 + k] * 7 + [9])
        else:
            cut = 150 + 23 * k
            prompts.append(prev[:cut] + [7000 + k] * 9 + prev[cut + 30:] + [9])
    for k, new in enumerate(prompts):
        if k == 7:
            entry = None                               # an entry reset before call 7
            recs[-1]["entry_reset"] = "decode_overflow"
        plan = ep.plan_call("epic128", entry, new, im_start=-1)
        edited[k] = plan.stats["edited"]
        recs.append({"ev": "call", "call": k, "t": float(k), "P": len(new), "gen": len(gen),
                     "exact": plan.stats["exact"], "edited": plan.stats["edited"]})
        entry = ep.next_entry(plan, new, gen, np.arange(10_000, 10_000 + plan.stats["C"]), np.arange(20_000, 20_003), k)
    flags = REP.post_snip_flags(recs, epic_arm=True)
    assert flags == edited, (flags, edited)
    assert edited[5] is False and edited[7] is False and edited[6] is True


def test_charged_tokens():
    assert REP.charged({"P": 100, "ideal_C": 40, "ideal_E": 60}) == 40
    assert REP.charged({"P": 100, "C": 30}) == 30
    assert REP.charged({"P": 100, "C": 30, "C_charged": 95}) == 95
    assert REP.charged({"P": 100, "fallback": True, "apc_hit": 64}) == 36
    assert REP.charged({"P": 100, "apc_hit": 10}) == 90


def test_verify_imports_only_stdlib_and_numpy():
    tree = ast.parse((ROOT / "report" / "e4_verify.py").read_text())
    allowed = {"__future__", "argparse", "glob", "hashlib", "itertools", "json", "math", "re", "sys", "datetime",
               "pathlib", "numpy"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            continue
        assert names <= allowed, names


def test_verify_constants_match_collect():
    assert VER.INFRA_EXC == collect.INFRA_EXC and tuple(VER.CONN) == tuple(collect.CONN)
    assert tuple(VER.EXCLUDED_PHASES) == tuple(collect.EXCLUDED_PHASES)
    assert tuple(VER.VERIFIER_NET) == tuple(collect.VERIFIER_NET)
    assert tuple(VER.VERIFIER_NET_PHASES) == tuple(collect.VERIFIER_NET_PHASES)


# -- the synthetic round ---------------------------------------------------------------------------------------

TASKS = ["proj__proj-1", "proj__proj-2"]
ARMS = ["prefix", "prefix_aa", "epic128", "epic128rjsd"]
WIN = {"prefix": {TASKS[0]: [1, 1, 0], TASKS[1]: [0, 0, 0]},
       "prefix_aa": {TASKS[0]: [1, 0, 1], TASKS[1]: [1, 0, 0]},
       "epic128": {TASKS[0]: [1, 1, 1], TASKS[1]: [0, 1, 0]},
       "epic128rjsd": {TASKS[0]: [0, 1, 1], TASKS[1]: [0, 0, 1]}}
P = [1000, 1400, 1300]
GEN = [50, 40, 30]
MS = [2000.0, 3000.0, 2500.0]
EXACT = [0, 1049, 600]                   # call 1 appends (1000 + 50 - 1 = 1049), call 2 is cut at 600
EPIC_C = [1000, 360, 200]
C_O, C_E = 72, 200
WALL = 100.0


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


def _trial(root: Path, arm: str, job: str, name: str, task: str, t0: float, reward: float, finished: bool = True,
           exc: str | None = None, calls: list | None = None) -> Path:
    d = root / "jobs" / f"minimax-m2.7-epic+{arm}" / "swe" / job / name
    iso = lambda t: f"2026-10-16T{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}Z"   # noqa: E731
    _w(d / "result.json", {"task_name": f"swe-bench/{task}", "started_at": iso(t0),
                           "finished_at": iso(t0 + 130) if finished else None,
                           "agent_execution": {"started_at": iso(t0 + 10), "finished_at": iso(t0 + 10 + WALL)},
                           "verifier_result": {"rewards": {"reward": reward}},
                           "exception_info": {"exception_type": exc} if exc else None})
    _w(d / "config.json", {"task": {"path": f"datasets/swe/{task}"}})
    _w(d / "agent" / "s15" / "result.json", {"status": "completed", "counter": {"lead": 3}})
    _w(d / "agent" / "s15" / "config.json", {"max_lead_calls": 250})
    lines = calls if calls is not None else [
        {"purpose": "lead", "epic": {"arm": arm, "n": n}, "usage": {"output_tokens": GEN[n]}, "ms": MS[n],
         "response": [{"type": "text"}]} for n in range(3)]
    lines = lines + [{"purpose": "lead", "epic": {"arm": arm, "n": 1}, "error": "APITimeoutError", "ms": 5.0},
                     {"purpose": "one_shot", "usage": {"output_tokens": 9}, "ms": 700.0, "response": []}]
    _w(d / "agent" / "s15" / "calls.jsonl", "\n".join(json.dumps(x) for x in lines) + "\n")
    return d


def _u(block: str, task: str, call: int) -> float:
    return int(hashlib.sha256(f"{block}|{task}|recompute|{call}".encode()).hexdigest()[:8], 16) / 2 ** 32


def _round(root: Path, bad_u: bool = False) -> dict:
    """Writes the synthetic round; returns what each trial's server records were (for the expectations)."""
    log, recompute = [], {}
    t = 1000.0
    # the baseline's calls for the time model: ms = 300 + 0.05 C + 20 out, exactly
    rng = np.random.default_rng(7)
    for i in range(6):
        calls = []
        for _ in range(10):
            c, p, o = int(rng.integers(100, 9000)), int(rng.integers(9000, 20000)), int(rng.integers(20, 900))
            calls.append({"purpose": "lead", "usage": {"input_tokens": c, "cache_read_input_tokens": p - c,
                                                       "output_tokens": o}, "ms": 300 + 0.05 * c + 20 * o})
        _w(root / "jobs" / "minimax-m2.7" / "swe" / "q3__c00" / f"base__{i}" / "agent" / "s15" / "calls.jsonl",
           "\n".join(json.dumps(x) for x in calls) + "\n")
    for k in range(3):
        block = f"e4r{k}"
        for ti, task in enumerate(TASKS):
            for arm in ARMS:
                name = f"{task}__{arm}{k}"
                job = f"{block}b{ti:02d}__c00"
                if arm == "prefix" and k == 1 and ti == 0:
                    # an infrastructure failure, then its rerun under the same block key
                    _trial(root, arm, job, f"{task}__infra", task, t, 1.0, exc="DockerError")
                    job = f"{block}b{ti:02d}x1__c00"
                    t += 1
                if arm == "epic128" and k == 2 and ti == 0:
                    # a verifier that could not install its test runner: an infrastructure failure from e4 on
                    d = _trial(root, arm, job, f"{task}__vnet", task, t, 0.0)
                    _w(d / "verifier" / "test-stdout.txt", "downloading uv 0.7.13\ncurl: (28) SSL connection timeout\n"
                       "failed to download https://github.com/astral-sh/uv/releases/download/0.7.13/uv.tar.gz\n")
                    job = f"{block}b{ti:02d}x1__c00"
                    t += 1
                if arm == "prefix_aa" and k == 2 and ti == 1:
                    _trial(root, arm, f"{block}b{ti:02d}__c00", f"{task}__unfinished", task, t - 500, 0.0,
                           finished=False)
                _trial(root, arm, job, name, task, t, float(WIN[arm][task][k]))
                t += 1
                for n in range(3):
                    rec = {"ev": "call", "spec": name, "arm": arm, "call": n, "P": P[n], "gen": GEN[n],
                           "t": t + n, "apc_hit": 0}
                    if arm.startswith("prefix"):
                        e = (min(EXACT[n], P[n] - 1) // 16) * 16
                        rec.update(exact=EXACT[n], ideal_E=e, ideal_C=P[n] - e)
                        if arm == "prefix" and k == 0 and ti == 0 and n == 1:
                            rec["shadow"] = {"floor": True, "first_kl": 0.01}
                    else:
                        rec.update(exact=EXACT[n], C=EPIC_C[n], edited=n == 2)
                        if arm == "epic128rjsd":
                            rec["C_charged"] = EPIC_C[n]
                            if n == 2:
                                u = _u(block, task, n)
                                m = {(1, 0): 0.6, (1, 1): 0.3}.get((k, ti), 0.05)    # u 0.506 and 0.284: recompute
                                rc = u < m
                                recompute[name] = rc
                                if rc:
                                    rec.update(C=P[n] - 592, recompute=True)
                                rec["C_charged"] = rec["C"] + C_O + (C_E if rc else 0)
                                rec["dec"] = {"metric": "jsd", "jsd": m, "tvd": 0.04, "kl": 0.02,
                                              "exp": 1 - math.exp(-0.02), "kl20": 0.01, "jsd20": 0.03, "tvd20": 0.03,
                                              "exp20": 1 - math.exp(-0.01), "m": m,
                                              "u": (u + 0.5) % 1 if bad_u and k == 0 and ti == 0 else u,
                                              "recompute": rc, "C_E": C_E, "C_O": C_O, "h": 592, "ms": 800.0,
                                              "same": False}
                        if k == 0 and ti == 0 and n == 2 and not recompute.get(name):
                            num = 2.0 if arm == "epic128" else 3.0
                            rec["shadow"] = {"floor": False, "first_kl": 0.03 if arm == "epic128" else 0.04,
                                             "tf_kl": 0.02, "tf_top1": 0.95,
                                             "first_klp": 0.02, "first_jsd": 0.07, "first_tvd": 0.05, "first_exp": 0.02,
                                             "bins": {"classes": ["exact", "reused", "head", "computed"],
                                                      "age": [0, 1, 2, 4, 8, 16], "offset": [0, 32, 64, 128, 512]},
                                             "l1": {"35": [[num, 20.0, 0.0, 0.0, 5.0]]}}
                    if arm == "epic128" and k == 1 and ti == 1 and n == 1:
                        log.append({"ev": "call", "spec": name, "arm": arm, "call": 1, "P": 1400, "C": 999,
                                    "aborted": True, "t": t + n - 0.5})
                    if arm == "epic128" and k == 2 and ti == 1 and n == 2:
                        rec = {"ev": "call", "spec": name, "arm": arm, "call": 2, "P": 1300, "gen": 30,
                               "fallback": True, "apc_hit": 1100, "t": t + n}
                    log.append(rec)
                t += 10
    _w(root / "runs" / "epic" / "minimax-m2.7-epic.calls.jsonl", "\n".join(json.dumps(x) for x in log) + "\n")
    (root / "runs" / "disk_guard_kills.jsonl").write_text("")
    return {"recompute": recompute}


@contextlib.contextmanager
def _rooted(root: Path):
    old = (REP.ROOT, VER.ROOT, collect.ROOT)
    REP.ROOT = VER.ROOT = collect.ROOT = root
    try:
        yield
    finally:
        REP.ROOT, VER.ROOT, collect.ROOT = old


def _expected(state: dict) -> dict:
    """The table, worked out from the fixture's numbers."""
    exp = {}
    beta = (300.0, 0.05, 0.0, 20.0)
    for arm in ARMS:
        charged_calls = []
        post = 0
        for k in range(3):
            for ti, task in enumerate(TASKS):
                name = f"{task}__{arm}{k}"
                for n in range(3):
                    if arm.startswith("prefix"):
                        e = (min(EXACT[n], P[n] - 1) // 16) * 16
                        c = P[n] - e
                    elif arm == "epic128" and k == 2 and ti == 1 and n == 2:
                        c = 1300 - 1100                            # the fallback: what was actually computed
                    elif arm == "epic128rjsd" and n == 2:
                        rc = state["recompute"][name]
                        c = (P[n] - 592 if rc else EPIC_C[n]) + C_O + (C_E if rc else 0)
                    else:
                        c = EPIC_C[n]
                    charged_calls.append((name, n, c))
                    fallback_call = arm == "epic128" and k == 2 and ti == 1 and n == 2
                    post += int(n == 2 and not fallback_call)        # call 2 is cut; a fallback record has no plan
        runs = 6
        sum_c = sum(c for *_x, c in charged_calls)
        llm = [sum((beta[0] + beta[1] * c + beta[3] * GEN[n]) / 1000 for nm, n, c in charged_calls if nm == name)
               for name in sorted({nm for nm, _n, _c in charged_calls})]
        tool = WALL - sum(MS) / 1000
        exp[arm] = {"runs": runs, "calls": 18, "c_call": sum_c / 18, "c_run": sum_c / runs,
                    "llm_s": sum(llm) / runs, "tool_s": tool, "job_s": sum(llm) / runs + tool,
                    "comp_share": sum_c / (6 * sum(P)), "post_snip": post,
                    "pass1": sum(sum(v) for v in WIN[arm].values()) / 6,
                    "pass3": sum(all(v) for v in WIN[arm].values()) / 2}
    # resolve differences: per block arm - mean(prefix, prefix_aa), per task, then over the 2 tasks (df 1)
    t = 6.313751514675043
    for arm in ("prefix_aa", "epic128", "epic128rjsd"):
        means = []
        for task in TASKS:
            ctl = ["prefix"] if arm == "prefix_aa" else ["prefix", "prefix_aa"]
            d = [WIN[arm][task][k] - sum(WIN[c][task][k] for c in ctl) / len(ctl) for k in range(3)]
            means.append(sum(d) / 3)
        mu = sum(means) / 2
        sd = math.sqrt(sum((x - mu) ** 2 for x in means))
        exp[arm]["diff"] = mu
        exp[arm]["ci"] = (mu - t * sd / math.sqrt(2), mu + t * sd / math.sqrt(2))
    exp["epic128"].update(first_kl=0.03, reply_kl=0.02, reply_top1=0.95, dk1=0.1)
    if not state["recompute"].get(f"{TASKS[0]}__epic128rjsd0"):
        exp["epic128rjsd"].update(first_kl=0.04, reply_kl=0.02, reply_top1=0.95, dk1=0.15)
    return exp


def test_known_answer_table_and_independent_verification():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = _round(root)
        with _rooted(root):
            js = REP.build("e4", ["swe"], ARMS, {"swe": TASKS}, root / "runs" / "epic" / "minimax-m2.7-epic.calls.jsonl")
            js["meta"] = {"fit": "sorted"}
            b = js["benches"]["swe"]
            exp = _expected(state)
            assert np.allclose(b["fit"]["beta"], [300, 0.05, 0, 20], atol=1e-6), b["fit"]["beta"]
            for arm in ARMS:
                c = {k: v["value"] for k, v in b["table"][arm]["cells"].items()}
                e = exp[arm]
                for k in ("runs", "calls", "post_snip"):
                    assert c[k] == e[k], (arm, k, c[k], e[k])
                for k in ("c_call", "c_run", "llm_s", "tool_s", "job_s", "comp_share", "pass1", "pass3"):
                    assert abs(c[k] - e[k]) < 1e-6 * max(1, abs(e[k])), (arm, k, c[k], e[k])
                if "diff" in e:
                    assert abs(c["diff"] - e["diff"]) < 1e-12 and np.allclose(c["ci"], e["ci"], atol=1e-6), (arm, c["ci"], e["ci"])
                else:
                    assert c["diff"] is None and c["ci"] == (None, None)
                if "first_kl" in e:
                    assert abs(c["first_kl"] - e["first_kl"]) < 1e-12 and abs(c["reply_top1"] - 0.95) < 1e-12
                    assert abs(c["dk"][1] - e["dk1"]) < 1e-12 and c["dk"][0] is None
            # the trial set: the rerun, not the infrastructure failure; the finished trial, not the unfinished one
            specs = {x["spec"] for x in b["trial_set"]}
            assert f"{TASKS[0]}__infra" not in specs and f"{TASKS[1]}__unfinished" not in specs and len(specs) == 24
            assert f"{TASKS[0]}__vnet" not in specs and f"{TASKS[0]}__epic1282" in specs
            acct = b["accounting"]
            assert acct["epic128"]["aborted_records"] == 1 and acct["epic128"]["fallbacks"] == 1
            assert acct["prefix"]["errored_attempts"] == 6 and acct["prefix"]["non_lead_calls"] == 6
            dec = b["decisions"]["epic128rjsd"]
            n_rc = sum(state["recompute"].values())
            assert n_rc == 2 and dec["decisions"] == 6 and abs(dec["recompute_rate"] - n_rc / 6) < 1e-12
            assert abs(dec["overhead_tokens_per_run"] - (6 * C_O + 2 * C_E) / 6) < 1e-12
            assert b["floor"]["first_kl"] == 0.01 and b["floor"]["n"] == 1
            # the verifier, independently: every cell
            out = VER.verify(js, {"swe": TASKS})
            assert out["ok"], (out["mismatches"][:3], out["invariants"][:3])
            assert out["cells"] == len(ARMS) * len(VER.ORDER) + 6, out["cells"]     # + the recompute arm's decisions
            # tampering: a reward flipped after the report was made
            res = root / "jobs" / "minimax-m2.7-epic+epic128" / "swe" / "e4r0b00__c00" / f"{TASKS[0]}__epic1280" / "result.json"
            r = json.loads(res.read_text())
            r["verifier_result"]["rewards"]["reward"] = 0.0
            res.write_text(json.dumps(r))
            bad = VER.verify(js, {"swe": TASKS})
            cols = {x.get("column") for x in bad["mismatches"]}
            assert not bad["ok"] and {"pass1", "diff"} <= cols, bad["mismatches"][:4]
            r["verifier_result"]["rewards"]["reward"] = 1.0
            res.write_text(json.dumps(r))
            # a lead call removed from an agent's log
            cf = res.parent / "agent" / "s15" / "calls.jsonl"
            lines = cf.read_text().splitlines()
            cf.write_text("\n".join(lines[1:]) + "\n")
            bad = VER.verify(js, {"swe": TASKS})
            assert not bad["ok"] and "calls" in {x.get("column") for x in bad["mismatches"]}
            cf.write_text("\n".join(lines) + "\n")
            assert VER.verify(js, {"swe": TASKS})["ok"]
            # a log edited after the report: the verifier refuses to read it
            lf = root / "runs" / "epic" / "minimax-m2.7-epic.calls.jsonl"
            lf.write_text(lf.read_text().replace('"P": 1400', '"P": 1401', 1))
            try:
                VER.verify(js, {"swe": TASKS})
                raise AssertionError("a changed log must be refused")
            except SystemExit as exc:
                assert "sha256" in str(exc)


def test_verifier_catches_a_broken_decision_record():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _round(root, bad_u=True)
        with _rooted(root):
            js = REP.build("e4", ["swe"], ARMS, {"swe": TASKS}, root / "runs" / "epic" / "minimax-m2.7-epic.calls.jsonl")
            js["meta"] = {"fit": "sorted"}
            out = VER.verify(js, {"swe": TASKS})
            assert not out["ok"] and any("decision records break the contract" in x for x in out["invariants"]), out


# -- stage, gates and admission -----------------------------------------------------------------------------------

def test_stage_e4_blocks_prereg_and_projection():
    stage = _load(ROOT / "run" / "stage_e4.py", "stage_e4_t")
    with tempfile.TemporaryDirectory() as tmp:
        stage.ROOT = Path(tmp)

        def trial(arm, bench, job, finished=True, t0="2026-10-16T00:00:00Z", t1="2026-10-16T00:30:00Z"):
            d = Path(tmp) / "jobs" / f"{stage.SERVER}+{arm}" / bench / job / f"task__{arm}{job}"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({
                "started_at": t0, "finished_at": t1 if finished else None,
                "agent_execution": {"started_at": t0, "finished_at": t1 if finished else None}}))
        for arm in stage.ARMS:
            trial(arm, "swe", "e4r0b03__c00")
        for arm in stage.ARMS[:-1]:
            trial(arm, "swe", "e4r0b04__c00")
        trial(stage.ARMS[-1], "swe", "e4r0b04x1__c00")
        trial(stage.ARMS[0], "tbench", "e4r0b00__c00", finished=False)
        assert stage.complete_blocks("swe", "e4r0") == 2 and stage.complete_blocks("tbench", "e4r0") == 0
        # projection: 14 finished swe trials of 30 min (7 + 6 + 1 rerun); one tbench trial in flight for 60 min
        sh = stage.session_hours("e4", stage._ts("2026-10-16T01:00:00Z"))
        assert abs(sh["done"] - 14 * 0.5) < 1e-9 and abs(sh["inflight"] - 1.0) < 1e-9 and sh["mean_min"]["swe"] == 30.0
        tot = stage.project(10.0, "e4", {"swe": 10}, stage._ts("2026-10-16T01:00:00Z"))
        rate = 10.0 / (7.0 + 1.0)
        assert abs(tot - (10.0 + rate * (max(26.5 - 60, 0.2 * 26.5) + 10 * 30.0) / 60)) < 1e-9
        # pre-registration: the file's own hash and the hashes it lists
        for d in ("run/state", "report", "runs/epic"):
            (Path(tmp) / d).mkdir(parents=True, exist_ok=True)
        for rel in list(stage.TASKS.values()) + stage.ANALYSIS:
            (Path(tmp) / rel).write_text(f"# {rel}\n")
        stage.PREREG = Path(tmp) / "runs" / "epic" / "prereg_e4.md"
        stage.PREREG.write_text("".join(f"- `{rel}`, sha256 `{stage.sha(Path(tmp) / rel)}`\n"
                                        for rel in list(stage.TASKS.values()) + stage.ANALYSIS))
        stage.PREREG.with_suffix(".sha256").write_text(stage.sha(stage.PREREG) + "  runs/epic/prereg_e4.md\n")
        with contextlib.redirect_stdout(io.StringIO()):
            assert stage.check_prereg()
            (Path(tmp) / "report" / "e4_report.py").write_text("# edited\n")
            assert not stage.check_prereg()
    assert all(ep.is_arm(a) for a in stage.ARMS)


def test_pilot_gates_decision_checks_and_out():
    pg = _load(ROOT / "epic" / "pilot_gates.py", "pilot_gates_e4t")
    with tempfile.TemporaryDirectory() as tmp:
        pg.ROOT = Path(tmp)
        recs = []

        def trial(arm, name, calls):
            d = Path(tmp) / "jobs" / f"S+{arm}" / "swe" / "e4r0b00__c00" / name
            (d / "agent" / "s15").mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"started_at": "2026-10-16T00:00:00Z",
                                                       "finished_at": "2026-10-16T00:10:00Z"}))
            (d / "agent" / "s15" / "calls.jsonl").write_text("\n".join(json.dumps(
                {"purpose": "lead", "epic": {"n": n}, "response": {}, "request": {"messages": []}}) for n in calls) + "\n")
        trial("epic128rjsd", "t__r", [0, 1, 2, 3])
        recs += [{"ev": "call", "spec": "t__r", "call": 0, "P": 10},
                 {"ev": "call", "spec": "t__r", "call": 1, "P": 10, "edited": True, "C": 3,
                  "dec": {"m": 0.1, "recompute": True}},
                 {"ev": "call", "spec": "t__r", "call": 2, "P": 10, "edited": True, "C": 3},          # missing
                 {"ev": "call", "spec": "t__r", "call": 3, "P": 10, "edited": True, "C": 3,
                  "dec": {"error": "probe O: no logprobs", "recompute": False}}]
        trial("epic128", "t__e", [0, 1])
        recs += [{"ev": "call", "spec": "t__e", "call": 0, "P": 10},
                 {"ev": "call", "spec": "t__e", "call": 1, "P": 10, "edited": True, "C": 3, "dec": {"m": 0}}]  # leak
        (Path(tmp) / "runs" / "epic").mkdir(parents=True)
        (Path(tmp) / "runs" / "epic" / "S.calls.jsonl").write_text("\n".join(json.dumps(x) for x in recs) + "\n")
        out = Path(tmp) / "g.json"
        argv = sys.argv
        sys.argv = ["pilot_gates.py", "--server", "S", "--phase", "e4r0", "--url", "http://127.0.0.1:1", "--out", str(out)]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                pg.main()
        except SystemExit:
            pass
        finally:
            sys.argv = argv
        g = json.loads(out.read_text())
        assert g["gates"]["decisions_missing"] == 1 and g["gates"]["decisions_leaked"] == 1
        assert g["gates"]["decision_errors"] == 1
        assert not g["pass"]["one_dec_per_edited_call"] and not g["pass"]["no_decision_errors"]
        d = g["arms"]["epic128rjsd"]["decisions"]
        assert d["n"] == 2 and d["recompute"] == 1 and d["errors"] == 1 and d["recompute_rate"] == 0.5


def test_epic_round_admission_helpers():
    er = _load(ROOT / "run" / "epic_round.py", "epic_round_e4t")
    a = types.SimpleNamespace(server="S", bench="swe", concurrency=3, chunk=1, stagger=2.0, agent_timeout_multiplier=2.0)
    cmd = er.batch_cmd(a, "URL", ["prefix", "epic128rjsd"], "e4r0b07", ["t"], seed_key="e4r0", rotate_offset=7)
    meta = er.cmd_meta(cmd)
    assert meta == {"server": "S", "bench": "swe", "phase": "e4r0b07", "arms": ["prefix", "epic128rjsd"]}
    with tempfile.TemporaryDirectory() as tmp:
        er.ROOT = Path(tmp)
        for arm, done in (("prefix", True), ("epic128rjsd", False)):
            d = Path(tmp) / "jobs" / f"S+{arm}" / "swe" / "e4r0b07__c00" / f"t__{arm}"
            d.mkdir(parents=True)
            ae = {"started_at": "x", "finished_at": "y" if done else None}
            (d / "result.json").write_text(json.dumps({"agent_execution": ae}))
        assert er.sessions_in_flight([dict(meta, tasks=1)]) == 1
        admit = er.admission(3, 0.0, "URL")
        assert admit([dict(meta, tasks=1)], {"arms": ["a", "b"], "tasks": 1})          # 1 + 2 <= 3
        assert not admit([dict(meta, tasks=1)], {"arms": ["a", "b", "c"], "tasks": 1})  # 1 + 3 > 3
        assert er.admission(0, 0.0, "URL") is None


def main() -> None:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as exc:                            # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=4)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
