#!/usr/bin/env python3
"""E0c slack at N = 1 (input to D3): the idle windows before lead calls, by what the previous reply ran, against the
time an exact refresh of an edited call's reused rows takes on topic 16's H100 model.

Input: E0a's per-call records (`data/e0a/calls_he.jsonl`, `calls_t16.jsonl`; built by hs_census.py, rebuilt here if
missing). harness_eval is the source; topic 16's prefix arms (Qwen3-32B, the TTFT model's own setup) are a cross-check.

Window before lead call k = its start - (previous lead call's start + duration), less the time other chain calls
(one_shot subagents, compaction summaries) ran inside it: tool + harness time, when the GPU serving this session is
idle at N = 1. Measured on the shared servers; tool time does not depend on them.

Classes, from the previous lead reply's tool uses (a reply with several takes the first class in CLASS_ORDER):
  task        the `task` subagent (the window is its tools and harness time; its LLM calls are subtracted)
  bash_test   bash running tests (TEST_RE: pytest, go test, npm/yarn/pnpm test, cargo test, make test/check, tox,
              unittest, nose, Django's runtests.py / manage.py test, jest, mocha, vitest, ctest, mvn/gradle test, ...)
  bash_other  any other bash command
  search      glob / grep tools, and bash whose first program is grep, rg, find, ag, ack, fd, locate or git grep
  read        read_file          edit   edit_file, write_file
  other       todo, task board, skills, compact ...          none   a reply without tool use

Refresh of an edited lead call (a structural edit: E0a's `edited`): recompute exactly the rows it reuses across the
edit, ttft_ms_16(compute, computed = edit tokens, loaded = 0, prompt = P). Alternative 'refresh every call': before every
lead call, recompute every row it takes from the cache (computed = P - new; an upper bound: no staleness tracking).
Fit: share of calls whose window >= refresh; share of the refresh work that fits, counting a window only when it is
longer than the refresh (pre-registered, README section 2 D3) and partially (sum min(window, refresh) / sum refresh).

    /data/fritz/venv/bin/python research/17_harness_aware_serving/hs_slack.py [--workers 24]

Writes data/e0c/windows_<src>.jsonl (one record per lead call after the first) and data/tables/E0c_slack_n1.md.
`windows(records)` and `fit(rows, key)` take any E0a-style records, so N > 1 slack (topic 11's spare prefill budget per
step) can be added beside them.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_census as hcen  # noqa: E402
import hs_common as hc  # noqa: E402

E0C = hc.DATA / "e0c"
CLASS_ORDER = ("task", "bash_test", "bash_other", "search", "read", "edit", "other", "none")
TEST_RE = re.compile(
    r"\bpytest\b|\bpy\.test\b|\bgo\s+test\b|\b(?:npm|yarn|pnpm)\s+(?:run\s+)?test|\bcargo\s+(?:\+\S+\s+)?test\b"
    r"|\bmake\b[^|;&]*\b(?:test|check)\b|\btox\b|\bunittest\b|\bnosetests\b|runtests\.py|manage\.py\s+test"
    r"|\bjest\b|\bmocha\b|\bvitest\b|\bctest\b|\bmvn\b[^|;&]*\btest\b|\bgradlew?\b[^|;&]*\btest\b|\bphpunit\b"
    r"|\brspec\b|\bbats\b")
SEARCH_PROGS = {"grep", "egrep", "fgrep", "rg", "find", "ag", "ack", "fd", "locate"}
SEARCH_TOOLS = {"glob", "grep", "grep_files", "find"}
WRAPPERS = {"sudo", "env", "time", "nice", "nohup", "timeout"}
PREFIXES = {"cd", "export", "source", ".", "set", "pushd", "popd", "true", "unset"}


def first_program(cmd: str) -> tuple[str, str]:
    """(program, next word) of the first command of a shell line that is not a cd/export/env-style prefix."""
    for seg in re.split(r"&&|\|\||;|\n|\|", cmd):
        words = [w for w in seg.strip().split() if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
        while words and words[0] in WRAPPERS:
            words = words[2:] if words[0] == "timeout" else words[1:]
        if not words or words[0] in PREFIXES:
            continue
        return Path(words[0]).name, words[1] if len(words) > 1 else ""
    return "", ""


def use_class(name: str | None, cmd: str | None) -> str:
    if name == "task":
        return "task"
    if name == "bash":
        cmd = cmd or ""
        if TEST_RE.search(cmd):
            return "bash_test"
        prog, nxt = first_program(cmd)
        if prog in SEARCH_PROGS or (prog == "git" and nxt == "grep"):
            return "search"
        return "bash_other"
    if name in SEARCH_TOOLS:
        return "search"
    if name == "read_file":
        return "read"
    if name in ("edit_file", "write_file"):
        return "edit"
    return "other"


def window_class(tool_uses: list) -> str:
    classes = {use_class(n, c) for n, c in tool_uses or []}
    return next((c for c in CLASS_ORDER if c in classes), "none")


def windows(records: list[dict], pricer=None) -> list[dict]:
    """One row per lead call after a session's first: the idle window before it and the refresh costs."""
    by = defaultdict(list)
    for r in records:
        if r["served"]:
            by[r["session"]].append(r)
    out = []
    for _s, rs in by.items():
        rs.sort(key=lambda r: r["idx"])
        prev = None
        others = []                                   # chain calls since the previous lead call
        for r in rs:
            if r["purpose"] != "lead":
                if r["purpose"] in hcen.CHAIN:
                    others.append(r)
                continue
            if prev is not None:
                end = prev["t"] + prev["ms"] / 1e3
                gap = r["t"] - end
                inside = sum(max(0.0, min(o["t"] + o["ms"] / 1e3, r["t"]) - max(o["t"], end)) for o in others)
                row = {"src": r["src"], "model": r["model"], "bench": r["bench"], "session": r["session"],
                       "call": r["call"], "t": r["t"], "gap_s": round(gap, 3), "inside_s": round(inside, 3),
                       "idle_s": round(max(0.0, gap - inside), 3), "cls": window_class(prev["tool_uses"]),
                       "uses": [n for n, _c in prev["tool_uses"]], "edited": r["edited"], "edit": r["edit"],
                       "P": r["P"], "new": r["new"]}
                if pricer is not None:
                    row["ref_edit_ms"] = round(refresh_ms(pricer, r["edit"], r["P"]), 1) if r["edited"] else 0.0
                    row["ref_all_ms"] = round(refresh_ms(pricer, max(0.0, r["P"] - r["new"]), r["P"]), 1)
                out.append(row)
            prev, others = r, []
    return out


def refresh_ms(pricer, computed: float, P: float) -> float:
    """An exact refresh of `computed` rows at prompt length P, as one prefill of the compute family."""
    return hc.ttft_ms_16(pricer.models, "compute", computed, 0, P) if computed > 0 else 0.0


def fit(rows: list[dict], key: str) -> dict:
    """Refresh work against the window before each call (rows with a refresh > 0)."""
    rs = [r for r in rows if r.get(key, 0) > 0]
    if not rs:
        return {"n": 0}
    w = np.array([r["idle_s"] * 1e3 for r in rs])
    f = np.array([r[key] for r in rs])
    whole = w >= f
    return {"n": len(rs), "ref_med_ms": float(np.median(f)), "ref_p90_ms": float(np.percentile(f, 90)),
            "ref_h": f.sum() / 3.6e6, "calls_fit": whole.mean(), "work_fit": f[whole].sum() / f.sum(),
            "work_partial": np.minimum(w, f).sum() / f.sum()}


def pct(a: np.ndarray, q: float) -> str:
    return f"{np.percentile(a, q):.2f}" if len(a) else "-"


def model_order(m: str) -> int:
    return hc.HE_MODELS.index(m) if m in hc.HE_MODELS else 9


def window_lines(rows: list[dict]) -> list[str]:
    lines = ["| source | model | window class | windows | share of windows | p50 (s) | p90 (s) | p99 (s) | "
             "share of total window time |", "|---|---|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["src"], r["model"])].append(r)
    for (src, model), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], model_order(kv[0][1]))):
        tot = sum(r["idle_s"] for r in rs)
        for cls in CLASS_ORDER + ("all",):
            sel = rs if cls == "all" else [r for r in rs if r["cls"] == cls]
            if not sel:
                continue
            a = np.array([r["idle_s"] for r in sel])
            name = f"**{cls}**" if cls == "all" else cls
            lines.append(f"| {src} | {model} | {name} | {len(sel):,} | {len(sel) / len(rs):.1%} | {pct(a, 50)} | "
                         f"{pct(a, 90)} | {pct(a, 99)} | {a.sum() / tot:.1%} |")
    return lines


def fit_lines(rows: list[dict], chain_s: dict) -> list[str]:
    lines = ["| source | model | bench | idle share of the N = 1 session | edited calls (share of lead calls) | "
             "refresh per edited call, median / p90 (ms) | edited calls whose window >= refresh | **refresh work "
             "that fits (whole windows)** | ... partially (sum min / sum) | refresh every call: calls fitting / "
             "work fitting (whole) / partial |", "|---|---|---|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["src"], r["model"], r["bench"])].append(r)
        groups[(r["src"], r["model"], "all")].append(r)
        if r["src"] == "he":
            groups[("he", "all models", "all")].append(r)
    benches = defaultdict(set)
    for r in rows:
        benches[(r["src"], r["model"])].add(r["bench"])
    for k in [k for k in groups if k[2] != "all" and len(benches[(k[0], k[1])]) == 1]:
        del groups[k]                                 # a single bench: its row is the model's row
    order = lambda kv: (kv[0][0], kv[0][1] == "all models", model_order(kv[0][1]), kv[0][2] == "all",  # noqa: E731
                        kv[0][2])
    for k, rs in sorted(groups.items(), key=order):
        a, b = fit(rs, "ref_edit_ms"), fit(rs, "ref_all_ms")
        idle = sum(r["idle_s"] for r in rs)
        busy = sum(chain_s.get(s, 0.0) for s in {r["session"] for r in rs})
        idle_share = idle / (idle + busy) if idle + busy else 0.0
        name = (lambda x: f"**{x}**" if k[2] == "all" else x)
        if a["n"]:
            ea = (f"{a['n']:,} ({a['n'] / len(rs):.0%}) | {a['ref_med_ms']:,.0f} / {a['ref_p90_ms']:,.0f} | "
                  f"{a['calls_fit']:.1%} | **{a['work_fit']:.1%}** | {a['work_partial']:.1%}")
        else:
            ea = "0 | - | - | - | -"
        eb = f"{b['calls_fit']:.1%} / {b['work_fit']:.1%} / {b['work_partial']:.1%}" if b["n"] else "-"
        lines.append(f"| {k[0]} | {name(k[1])} | {k[2]} | {idle_share:.1%} | {ea} | {eb} |")
    return lines


def d3_lines(rows: list[dict]) -> list[str]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["src"], r["model"])].append(r)
        if r["src"] == "he":
            groups[("he", "all models")].append(r)
    lines = []
    for k, rs in sorted(groups.items(), key=lambda kv: (kv[0][0] != "he", kv[0][1] != "all models",
                                                        model_order(kv[0][1]))):
        a, b = fit(rs, "ref_edit_ms"), fit(rs, "ref_all_ms")
        if not a["n"]:
            continue
        ok = "passes" if a["work_fit"] >= 0.5 else "fails"
        okb = "passes" if b["work_fit"] >= 0.5 else "fails"
        lines.append(f"- **{k[0]} / {k[1]}**: {a['work_fit']:.1%} of the edited calls' refresh work fits in the window "
                     f"before them ({ok}; partially {a['work_partial']:.1%}); refreshing every call: "
                     f"{b['work_fit']:.1%} ({okb}).")
    return lines


def load_records(workers: int) -> dict[str, list[dict]]:
    out = {}
    for src, name in (("he", "calls_he.jsonl"), ("t16", "calls_t16.jsonl")):
        path = hcen.E0A / name
        if path.exists():
            out[src] = list(hc.read_jsonl(path))
    missing = tuple(s for s in ("he", "t16") if s not in out)
    if missing:
        built = hcen.build(missing, workers)
        if "t16" in built:
            built["t16"] = [r for r in built["t16"] if r["src"] == "t16-main"
                            and r["bench"] in ("R1-prefix", "R1-prefix_aa")]
        for s, rs in built.items():
            hcen.write_jsonl(hcen.E0A / f"calls_{s}.jsonl", rs)
            out[s] = rs
    out["t16"] = [dict(r, src="t16", bench="R1") for r in out.get("t16", [])]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=24, help="worker processes if the E0a records must be rebuilt")
    a = ap.parse_args()
    recs = load_records(min(32, max(1, a.workers)))
    pricer = hcen.N1Pricer(*hc.latency_models_16())
    rows, chain_s = [], defaultdict(float)
    for src, rs in recs.items():
        w = windows(rs, pricer)
        hcen.write_jsonl(E0C / f"windows_{src}.jsonl", w)
        rows += w
        for r in rs:
            if r["served"] and r["purpose"] in hcen.CHAIN:
                pre, _e, dec = pricer(r)
                chain_s[r["session"]] += (pre + dec) / 1e3
    lines = ["## 1. Idle windows before lead calls, by what the previous reply ran", "",
             "Window = tool + harness time between a lead call's end and the next lead call's start, less any "
             "subagent or compaction call inside it (measured on harness_eval's shared servers; tool time does not "
             "depend on them). Classes as in the script's docstring.", ""]
    lines += window_lines(rows)
    lines += ["", "## 2. Does an exact refresh fit in the window before the edited call?", "",
              "Edited call = a lead call with a structural edit (E0a); its refresh recomputes the rows it reuses "
              "across the edit (E0a's edit tokens) as one prefill at its prompt length on topic 16's H100 compute "
              "model. 'Idle share' = windows / (windows + the session's modeled N = 1 chain time). 'Refresh every "
              "call' recomputes every cached row (P - new) before every lead call.", ""]
    lines += fit_lines(rows, chain_s)
    lines += ["", "## 3. D3 reading at N = 1", "",
              "D3 passes if >= 50 % of the refresh work fits in measured slack; at N = 1 a window counts only when it "
              "is longer than the refresh (README section 2). The D2 policy is not chosen yet (E0e), so the reading "
              "uses the per-edited-call refresh (every refresh policy refreshes a subset of these calls):", ""]
    lines += d3_lines(rows)
    note = ("E0c at N = 1 (D3 input). Windows from harness_eval's measured call times (`t`, `ms` in calls.jsonl) "
            "and topic 16's request logs; refresh times from topic 16's H100 Qwen3-32B TTFT model for every model. "
            "Records: `data/e0c/windows_he.jsonl`, `data/e0c/windows_t16.jsonl`.")
    print(hc.write_table("E0c_slack_n1", "E0c slack at N = 1: does an exact refresh fit in the idle windows?",
                         lines, note))


if __name__ == "__main__":
    main()
