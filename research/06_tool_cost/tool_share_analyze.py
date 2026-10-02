#!/usr/bin/env python3
"""Tables of research/06_tool_cost Part C: the full price list and the end-to-end tool share.

    python3 research/06_tool_cost/tool_share_analyze.py [--data research/06_tool_cost/data]
        [--md .../tool_share/tool_share_tables.md] [--json .../tool_share/tool_share_tables.json]

Inputs (each part is optional; missing parts are reported as missing, not guessed):
  tool_sweep/<run>/<cond>/<run>-<cond>.records.jsonl   C2 controlled sweep (tool_sweep_probe.py)
  tool_latency/*.records.jsonl                           Part A probe (2026-09-15, old host) for the drift table
  tool_census/census_tables.json                         C1 corpus census (tool_census.py)
  tool_e2e/<arm>-model/run_*.{jsonl,records.jsonl}       C3 model-bearing tools (tool_model_probe.py)
  tool_e2e/<arm>-delay/d*/run_*.jsonl + *.score.json     C4a injected-delay sessions (05 latency_workloads.py)
  tool_e2e/<arm>-heavy/run_*.jsonl + *.score.json        C4b tool-heavy sessions (tool_heavy_workloads.py)

Tables: T1 price list v2, T2 drift vs Part A, T3 host factors (lane filesystem, trace mode, page cache,
task-board size), T4 model-bearing tools split into model / nested tools / rest, T5 end-to-end share
(corpus and the new sessions, per run and per request), T6 injected delay (measured vs replay-predicted,
and the per-call tool latency at which the share would reach 30 % and 85 %), T7 heavy workloads,
T8 reconciliation grid (our workloads x serving speed x per-call tool latency).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401
import tool_census as tc                                                     # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "research" / "06_tool_cost" / "data"
DELAYS = {"d0000": 0.0, "d0290": 0.29, "d1090": 1.09, "d6000": 6.0}
ARMS = {"q27": "Qwen3.8-27B (thinking)", "q8": "Qwen3-8B (no thinking)"}
PAPER = {"SWE-bench (0.29 s/call, 30 %)": (0.29, 0.30), "BFCL web search (1.09 s/call, 40 %)": (1.09, 0.40)}


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def f(x, d=1):
    return "-" if x is None else f"{x:,.{d}f}"


def pc(x, d=1):
    return "-" if x is None else f"{100 * x:.{d}f}%"


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def traces_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("run_*.jsonl") if not tc.SIDECAR.search(p.name))


# ----------------------------------------------------------------------------- C2 sweep
def load_sweep(data: Path) -> dict[str, list[dict]]:
    by_cond = defaultdict(list)
    for path in sorted((data / "tool_sweep").glob("*/*/*.records.jsonl")):
        run, cond = path.parent.parent.name, path.parent.name
        for r in read_jsonl(path):
            if r.get("warmup") or r.get("role") == "error":
                continue
            r["run"], r["cond"] = run, cond
            by_cond[cond].append(r)
    return by_cond


def case_stats(records):
    groups = defaultdict(list)
    for r in records:
        groups[(r["role"], r["tool"], r["case"], r.get("phase"), r.get("board"))].append(r)
    out = {}
    for key, rs in groups.items():
        xs = [r["duration_ms"] for r in rs]
        out[key] = {"n": len(xs), "mean": mean(xs), "median": q(xs, 0.5), "p90": q(xs, 0.9), "p99": q(xs, 0.99),
                    "max": max(xs), "status": dict(sorted({r["status"]: sum(1 for x in rs if x["status"] == r["status"])
                                                           for r in rs}.items())),
                    "out_chars": mean([r.get("output_chars") for r in rs])}
    return out


def sweep_tables(data: Path, md: list, js: dict):
    sweep = load_sweep(data)
    js["sweep_conditions"] = {c: len(rs) for c, rs in sweep.items()}
    if not sweep:
        md.append("## T1-T3 Controlled sweep\n\n(no tool_sweep records yet)\n")
        return
    main = {c: case_stats([r for r in rs if r.get("phase") in ("main", "heavy")]) for c, rs in sweep.items()}
    base = "nfs-sum" if "nfs-sum" in main else sorted(main)[0]
    other = "loc-sum" if "loc-sum" in main else None
    md.append("## T1 Price list v2: every supported tool, per argument class\n")
    md.append(f"Controlled dispatches, no model calls, runs pooled ({', '.join(sorted({r['run'] for rs in sweep.values() for r in rs}))}). "
              f"Columns: the `{base}` condition (lane on NFS, trace summary on NFS: the production-like default of the "
              f"05/09 Qwen runs) and, for the median, `{other}` (node-local lane and trace). ms per call, harness-side span.\n")
    md.append(f"| role | tool | case | n | mean | median | p90 | p99 | median {other or ''} | status | out chars |")
    md.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|")
    rows = sorted(main[base].items(), key=lambda kv: (kv[0][0], kv[0][1], -(kv[1]["median"] or 0)))
    js["T1"] = []
    for (role, tool, case, phase, board), st in rows:
        loc = (main.get(other) or {}).get((role, tool, case, phase, board), {}).get("median") if other else None
        status = ", ".join(f"{k} {v}" for k, v in st["status"].items())
        md.append(f"| {role} | {tool} | {case} | {st['n']} | {f(st['mean'], 2)} | {f(st['median'], 2)} | {f(st['p90'], 2)} | "
                  f"{f(st['p99'], 2)} | {f(loc, 2)} | {status} | {f(st['out_chars'], 0)} |")
        js["T1"].append({"role": role, "tool": tool, "case": case, "phase": phase, **st, "median_" + (other or "none"): loc})
    md.append("")
    # pooled per tool (success paths), the headline price list
    md.append("### T1b Per tool, pooled over its argument classes (status ok, condition " + base + ")\n")
    md.append("| role | tool | cases | n | median | p90 | max case median | cheapest case median |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|")
    pooled = defaultdict(list)
    per_case = defaultdict(list)
    for r in sweep[base]:
        if r.get("phase") in ("main", "heavy") and r["status"] in ("ok", "scheduled"):
            pooled[(r["role"], r["tool"])].append(r["duration_ms"])
            per_case[(r["role"], r["tool"], r["case"])].append(r["duration_ms"])
    js["T1b"] = {}
    for (role, tool), xs in sorted(pooled.items(), key=lambda kv: (kv[0][0], -q(kv[1], 0.5))):
        meds = [q(v, 0.5) for (ro, to, _), v in per_case.items() if ro == role and to == tool]
        md.append(f"| {role} | {tool} | {len(meds)} | {len(xs)} | {f(q(xs, 0.5), 2)} | {f(q(xs, 0.9), 2)} | {f(max(meds), 2)} | {f(min(meds), 2)} |")
        js["T1b"][f"{role}|{tool}"] = {"n": len(xs), "median": q(xs, 0.5), "p90": q(xs, 0.9), "cases": len(meds),
                                     "max_case_median": max(meds), "min_case_median": min(meds)}
    md.append("")
    # T2 drift vs Part A
    part_a = defaultdict(list)
    for path in sorted((data / "tool_latency").glob("*.records.jsonl")):
        for r in read_jsonl(path):
            if not r.get("warmup") and r.get("role") != "error":
                part_a[r["case"]].append(r["duration_ms"])
    md.append("## T2 Drift: Part A's 54 cases on the old host (2026-09-15) vs today\n")
    md.append("| case | Part A median | today " + base + " | today " + (other or "-") + " | ratio (" + base + " / A) |")
    md.append("|---|---:|---:|---:|---:|")
    js["T2"] = {}
    today = defaultdict(list)
    today_loc = defaultdict(list)
    for r in sweep[base]:
        if r["case"] in part_a and r.get("phase") == "main":
            today[r["case"]].append(r["duration_ms"])
    for r in sweep.get(other, []):
        if r["case"] in part_a and r.get("phase") == "main":
            today_loc[r["case"]].append(r["duration_ms"])
    for case in sorted(part_a, key=lambda c: -q(part_a[c], 0.5)):
        a, b, c = q(part_a[case], 0.5), q(today.get(case, []), 0.5), q(today_loc.get(case, []), 0.5)
        md.append(f"| {case} | {f(a, 2)} | {f(b, 2)} | {f(c, 2)} | {f(b / a if a and b else None, 2)} |")
        js["T2"][case] = {"part_a": a, base: b, other: c}
    md.append("")
    # T3 host factors: every case's median under the six conditions
    conds = [c for c in ("nfs-sum", "loc-sum", "loc-nfstr", "loc-full", "loc-off", "nfs-full") if c in main]
    md.append("## T3 Host factors: case medians (ms) under each lane / trace condition\n")
    md.append("`nfs-*` lane on NFS; `loc-*` node-local lane; `-sum`/`-full` trace output mode; `nfstr` summary trace written to NFS "
              "from a local lane; `off` HARNESS_TRACE=0.\n")
    md.append("| role | case | " + " | ".join(conds) + " |")
    md.append("|---|---|" + "---:|" * len(conds))
    js["T3"] = {}
    keys = sorted({k for c in conds for k in main[c]}, key=lambda k: (k[0], k[2]))
    for key in keys:
        vals = [main[c].get(key, {}).get("median") for c in conds]
        md.append(f"| {key[0]} | {key[2]} | " + " | ".join(f(v, 2) for v in vals) + " |")
        js["T3"][f"{key[0]}|{key[2]}"] = dict(zip(conds, vals))
    md.append("")
    # board phase, on the NFS lane and on the node-local lane
    js["T3b"] = {}
    for cond in [c for c in (base, other) if c]:
        board = defaultdict(lambda: defaultdict(list))
        for r in sweep.get(cond, []):
            if r.get("phase") == "board":
                board[r["case"]][r.get("board")].append(r["duration_ms"])
        if not board:
            continue
        sizes = sorted({b for v in board.values() for b in v})
        md.append(f"### T3b Task-board size (tasks on the board) vs call cost, median ms ({cond})\n")
        md.append("| case | " + " | ".join(str(s) for s in sizes) + " | ms per task (1 -> 1000) |")
        md.append("|---|" + "---:|" * (len(sizes) + 1))
        js["T3b"][cond] = {}
        for case in sorted(board):
            meds = {s: q(board[case].get(s, []), 0.5) for s in sizes}
            slope = (meds[sizes[-1]] - meds[sizes[0]]) / (sizes[-1] - sizes[0]) if len(sizes) > 1 else None
            md.append(f"| {case} | " + " | ".join(f(meds[s], 2) for s in sizes) + f" | {f(slope, 3)} |")
            js["T3b"][cond][case] = {**{str(k): v for k, v in meds.items()}, "ms_per_task": slope}
        md.append("")


# ----------------------------------------------------------------------------- C3 model tools
def model_tables(data: Path, md: list, js: dict):
    md.append("## T4 Tools whose price is a model's work (real model, one call at a time)\n")
    js["T4"] = {}
    found = False
    md.append("| arm | case | n | ok | span median s | span mean s | nested model % | nested tools s | rest s | note |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for arm in ARMS:
        folder = data / "tool_e2e" / f"{arm}-model"
        for trace in traces_in(folder):
            recs_path = trace.with_name(trace.name.removesuffix(".jsonl") + ".records.jsonl")
            if not recs_path.exists():
                continue
            found = True
            recs = read_jsonl(recs_path)
            parsed = tc._parse(f"{arm}-model", trace, allow_probe=True)
            spans = [c for c in parsed.get("calls", []) if c["tool"] in ("task", "Workflow") and c["kind"] == "lead"]
            compacts = [m for m in parsed.get("models", []) if m["purpose"] == "compaction_summary"]
            tool_recs = [r for r in recs if r["tool"] in ("task", "Workflow") and r["status"] != "case_error"]
            comp_recs = [r for r in recs if r["tool"] == "compact_summary"]
            groups = defaultdict(list)
            for rec, span in zip(tool_recs, spans):
                groups[(rec["case"], rec["cls"])].append((rec, span))
            for rec, m in zip(comp_recs, compacts):
                groups[("compact", rec["cls"])].append((rec, {"net_ms": rec["duration_ms"], "model_ms": m["ms"],
                                                               "nested_tool_ms": 0.0}))
            for (case, cls), items in sorted(groups.items()):
                spans_s = [s["net_ms"] / 1000 for _, s in items]
                model = sum(s["model_ms"] or 0 for _, s in items)
                total = sum(s["net_ms"] for _, s in items)
                nested = mean([(s.get("nested_tool_ms") or 0) / 1000 for _, s in items])
                rest = mean([(s["net_ms"] - (s["model_ms"] or 0) - (s.get("nested_tool_ms") or 0)) / 1000 for _, s in items])
                ok = sum(1 for r, _ in items if r["status"] == "ok")
                notes = sorted({r.get("note") for r, _ in items if r.get("note")})
                note = ", ".join(f"{n} {sum(1 for r, _ in items if r.get('note') == n)}" for n in notes)[:80]
                md.append(f"| {arm} | {case}:{cls} | {len(items)} | {ok} | {f(q(spans_s, 0.5))} | {f(mean(spans_s))} | "
                          f"{pc(model / total if total else None)} | {f(nested, 2)} | {f(rest, 2)} | {note} |")
                js["T4"][f"{arm}|{case}:{cls}"] = {"n": len(items), "ok": ok, "span_median_s": q(spans_s, 0.5),
                                                   "span_mean_s": mean(spans_s), "model_share": model / total if total else None,
                                                   "nested_tool_s": nested, "rest_s": rest}
    if not found:
        md.append("| (no model-probe data yet) | | | | | | | | | |")
    md.append("")


# ----------------------------------------------------------------------------- C4 sessions
def load_sessions(folder: Path, set_name: str) -> list[dict]:
    out = []
    for trace in traces_in(folder):
        parsed = tc._parse(set_name, trace)
        if parsed.get("error") or parsed.get("probe"):
            continue
        run = parsed["summary"]
        score_path = folder / f"{run['label']}.score.json"
        score = json.loads(score_path.read_text()) if score_path.exists() else {}
        m = re.match(r"^([A-Za-z0-9]+)-(team|solo)-(r\d+)$", run["label"])
        run["workload"] = f"{m.group(1)}-{m.group(2)}" if m else run["label"]
        run["score"] = score
        run["requests"] = parsed["requests"]
        run["calls"] = parsed["calls"]
        out.append(run)
    return out


def req_share(runs, key="ftr_stream"):
    xs = []
    for r in runs:
        for rq in r["requests"]:
            num, den = rq.get(f"tools_{key}_ms"), rq.get(f"{key}_ms")
            if num is not None and den:
                xs.append(num / den)
    return xs


def delay_tables(data: Path, md: list, js: dict):
    md.append("## T6 Injected tool latency: measured share vs the replay prediction\n")
    md.append("Every executed tool call sleeps for the injected latency inside its tool_execution span "
              "(`profile_run.py --tool-delay`; `--no-timestamp`, so the model cannot see a clock). "
              "*tool/wall* = sum of tool spans (task/Workflow excluded: their spans are model time) over session wall time; *blocked/wall* = time some tool runs while no "
              "main-loop model call is in flight (the critical-path share); *FTR share* = per request, streamed. "
              "*predicted* replays the d0000 sessions with the latency actually injected per run (D, the sum of the "
              "draws) added to both tool and wall time: (T0 + D) / (W0 + D); the last column is the measured wall minus "
              "that replay (W0 + D), i.e. what the injected latency did beyond adding itself.\n")
    md.append("| arm | workload | delay s | runs | calls/run | wall s | tool/wall | blocked/wall | FTR share p50 | injected D s | predicted tool/wall | wall - (W0 + D) s |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    js["T6"] = {}
    js["crossover"] = {}
    any_data = False
    for arm in ARMS:
        root = data / "tool_e2e" / f"{arm}-delay"
        sessions = {d: load_sessions(root / d, f"{arm}-delay/{d}") for d in DELAYS if (root / d).exists()}
        if not sessions:
            continue
        any_data = True
        base = defaultdict(list)
        for r in sessions.get("d0000", []):
            base[r["workload"]].append(r)
        for d, runs in sessions.items():
            by_w = defaultdict(list)
            for r in runs:
                by_w[r["workload"]].append(r)
            for w, rs in sorted(by_w.items()):
                wall = mean([r["wall_ms"] / 1000 for r in rs])
                tool = mean([r["tool_ms_plain"] / 1000 for r in rs])
                n = mean([r["n_calls"] for r in rs])
                share = mean([r["tool_ms_plain"] / r["wall_ms"] for r in rs if r["wall_ms"]])
                blocked = mean([r["blocked_ms"] / r["wall_ms"] for r in rs if r["wall_ms"]])
                ftr = q(req_share(rs), 0.5)
                b = base.get(w, [])
                W0, T0, n0 = mean([r["wall_ms"] / 1000 for r in b]), mean([r["tool_ms_plain"] / 1000 for r in b]), mean([r["n_calls"] for r in b])
                dd = DELAYS[d]
                injected = mean([r["delay_s"] for r in rs])            # the draws actually slept (lognormal arm)
                pred = (T0 + injected) / (W0 + injected) if W0 else None
                excess = wall - W0 - injected if W0 and wall is not None else None
                md.append(f"| {arm} | {w} | {dd:.2f} | {len(rs)} | {f(n)} | {f(wall)} | {pc(share)} | {pc(blocked)} | {pc(ftr)} | "
                          f"{f(injected)} | {pc(pred)} | {f(excess)} |")
                js["T6"][f"{arm}|{w}|{d}"] = {"runs": len(rs), "calls": n, "wall_s": wall, "tool_s": tool, "share": share,
                                              "blocked": blocked, "ftr_p50": ftr, "predicted": pred, "excess_s": excess,
                                              "delay_s_injected": mean([r["delay_s"] for r in rs])}
        for w, b in sorted(base.items()):
            W0, T0, n0 = mean([r["wall_ms"] / 1000 for r in b]), mean([r["tool_ms_plain"] / 1000 for r in b]), mean([r["n_calls"] for r in b])
            if not n0:
                continue
            cross = {s: (s * W0 - T0) / (n0 * (1 - s)) for s in (0.30, 0.50, 0.85)}
            js["crossover"][f"{arm}|{w}"] = {"W0_s": W0, "T0_s": T0, "calls": n0, **{f"d_{int(s * 100)}": v for s, v in cross.items()}}
    if not any_data:
        md.append("| (no delay sessions yet) | | | | | | | | | | | |")
    md.append("")
    # the historical baselines of research/05: GLM hosted (Part A) and Qwen3.8-27B on one RTX PRO 6000 (Part B)
    census = data / "tool_census" / "runs.json"
    if census.exists():
        hist = defaultdict(list)
        for r in json.loads(census.read_text()):
            m = re.match(r"^([A-Za-z0-9]+)-(team|solo)-(r\d+)$", r["label"])
            if r["smoke"] or not m:
                continue
            if r["set"] == "05_latency_breakdown/latency_profiling":
                hist[("glm (05 A)", f"{m.group(1)}-{m.group(2)}")].append(r)
            elif r["set"] == "05_latency_breakdown/latency_profiling_qwen":
                hist[("q27-rtx (05 B)", f"{m.group(1)}-{m.group(2)}")].append(r)
        for (arm, w), b in sorted(hist.items()):
            W0, T0, n0 = mean([r["wall_ms"] / 1000 for r in b]), mean([r["tool_ms_plain"] / 1000 for r in b]), mean([r["n_calls"] for r in b])
            if n0:
                js["crossover"][f"{arm}|{w}"] = {"W0_s": W0, "T0_s": T0, "calls": n0,
                                                 **{f"d_{int(s * 100)}": (s * W0 - T0) / (n0 * (1 - s)) for s in (0.30, 0.50, 0.85)}}
    if js["crossover"]:
        md.append("### T6b Per-call tool latency at which tools would take 30 % / 50 % / 85 % of wall time\n")
        md.append("Solved from the d0000 sessions: d = (s W0 - T0) / (n (1 - s)).\n")
        md.append("| arm | workload | W0 s | calls | today's mean call ms | d for 30 % (s) | d for 50 % (s) | d for 85 % (s) |")
        md.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for key, v in sorted(js["crossover"].items()):
            arm, w = key.split("|")
            md.append(f"| {arm} | {w} | {f(v['W0_s'])} | {f(v['calls'])} | {f(1000 * v['T0_s'] / v['calls'])} | {f(v['d_30'], 2)} | "
                      f"{f(v['d_50'], 2)} | {f(v['d_85'], 2)} |")
        md.append("")


def heavy_tables(data: Path, md: list, js: dict):
    md.append("## T7 Tool-heavy sessions (the supported tools doing heavy work)\n")
    md.append("| arm | workload | runs | correct | wall s | calls | tool/wall | blocked/wall | FTR share p50 | top tool classes (s per run) |")
    md.append("|---|---|---:|---|---:|---:|---:|---:|---:|---|")
    js["T7"] = {}
    any_data = False
    for arm in ARMS:
        runs = load_sessions(data / "tool_e2e" / f"{arm}-heavy", f"{arm}-heavy")
        by_w = defaultdict(list)
        for r in runs:
            by_w[r["workload"]].append(r)
        for w, rs in sorted(by_w.items()):
            any_data = True
            classes = defaultdict(float)
            for r in rs:
                for c in r["calls"]:
                    classes[c["bash_class"] if c["tool"] == "bash" else c["tool"]] += c["net_ms"] / 1000 / len(rs)
            top = ", ".join(f"{k} {v:.1f}" for k, v in sorted(classes.items(), key=lambda kv: -kv[1])[:3])
            correct = f"{sum(r['score'].get('correct', 0) for r in rs)}/{sum(r['score'].get('total', 0) for r in rs)}"
            share = mean([r["tool_ms_plain"] / r["wall_ms"] for r in rs if r["wall_ms"]])
            blocked = mean([r["blocked_ms"] / r["wall_ms"] for r in rs if r["wall_ms"]])
            ftr = q(req_share(rs), 0.5)
            md.append(f"| {arm} | {w} | {len(rs)} | {correct} | {f(mean([r['wall_ms'] / 1000 for r in rs]))} | "
                      f"{f(mean([r['n_calls'] for r in rs]))} | {pc(share)} | {pc(blocked)} | {pc(ftr)} | {top} |")
            js["T7"][f"{arm}|{w}"] = {"runs": len(rs), "share": share, "blocked": blocked, "ftr_p50": ftr,
                                      "wall_s": mean([r["wall_ms"] / 1000 for r in rs]), "classes": dict(classes)}
    if not any_data:
        md.append("| (no heavy sessions yet) | | | | | | | | | |")
    md.append("")


def grid_table(data: Path, md: list, js: dict):
    """T8: per tool call, share = t / (t + L + o): t tool latency per call, L model time per tool call, o the
    rest of the wall time per call.  L and o measured per workload and arm (d0000 / heavy sessions), and for
    GLM from the 05 Part A census runs; the paper's serving setup is backed out of its own two reported points
    (SWE-bench 0.29 s/call -> 30 %, BFCL 1.09 s/call -> 40 %) with o = 0."""
    census = data / "tool_census" / "runs.json"
    rows = {}
    for arm in ARMS:
        for folder, sub in ((data / "tool_e2e" / f"{arm}-delay" / "d0000", "d0000"), (data / "tool_e2e" / f"{arm}-heavy", "heavy")):
            for r in (load_sessions(folder, f"{arm}-{sub}") if folder.exists() else []):
                rows.setdefault((r["workload"], arm), []).append(r)
    if census.exists():
        for r in json.loads(census.read_text()):
            if r["smoke"]:
                continue
            m = re.match(r"^([A-Za-z0-9]+)-(team|solo)-(r\d+)$", r["label"])
            if r["set"] == "05_latency_breakdown/latency_profiling" and m and m.group(2) == "solo":
                rows.setdefault((f"{m.group(1)}-solo", "glm (05 A)"), []).append(r)
            elif r["set"] == "05_latency_breakdown/latency_profiling_qwen" and m and m.group(2) == "solo":
                rows.setdefault((f"{m.group(1)}-solo", "q27-rtx (05 B)"), []).append(r)
            elif r["set"] == "10_rope_shift/rope_shift_live":
                rows.setdefault(("codebase E/M tasks (10)", "q32-h200 nothink"), []).append(r)
            elif r["set"] == "11_multiuser_kv/multiuser_live" and re.match(r"^MU-n01", r["label"]):
                rows.setdefault(("4-turn sessions, 1 user (11)", "q32-h200 nothink"), []).append(r)
    md.append("## T8 Reconciliation grid: tool share of wall time = t / (t + L + o) per tool call\n")
    md.append("t = per-call tool latency (column), L = model time per tool call and o = everything else per call "
              "(measured per workload and serving setup). The paper's setup is backed out of its own reported points "
              "with o = 0: L = t (1 - s) / s, i.e. " + "; ".join(f"{k}: L = {t * (1 - s) / s:.2f} s" for k, (t, s) in PAPER.items()) + ".\n")
    tcols = [("ours", None), ("0.29 s", 0.29), ("1.09 s", 1.09), ("6 s", 6.0)]
    md.append("| workload | serving | calls | L s/call | o s/call | " + " | ".join(f"t = {n}" for n, _ in tcols) + " |")
    md.append("|---|---|---:|---:|---:|" + "---:|" * len(tcols))
    js["T8"] = {}
    for (w, arm), rs in sorted(rows.items()):
        n = sum(r["n_calls"] for r in rs)
        if not n:
            continue
        L = sum(r["model_ms_main"] for r in rs) / 1000 / n
        t_ours = sum(r["tool_ms_plain"] for r in rs) / 1000 / n        # task/Workflow spans are model time
        o = max(sum(r["wall_ms"] - r.get("think_ms", 0) for r in rs) / 1000 / n - L - t_ours, 0.0)
        cells = []
        for name, t in tcols:
            t = t_ours if t is None else t
            cells.append(t / (t + L + o))
        md.append(f"| {w} | {arm} | {n / len(rs):.1f} | {L:.2f} | {o:.2f} | " + " | ".join(pc(c) for c in cells) + " |")
        js["T8"][f"{w}|{arm}"] = {"calls_per_run": n / len(rs), "L_s": L, "o_s": o, "t_ours_s": t_ours,
                                  "share": dict(zip([c for c, _ in tcols], cells))}
    for k, (t, s) in PAPER.items():
        L = t * (1 - s) / s
        md.append(f"| paper setup ({k}) | Qwen3-14B / A100 | - | {L:.2f} | 0 | " + " | ".join(
            "-" if tt is None else pc(tt / (tt + L)) for _, tt in tcols) + " |")
    md.append("")


def census_summary(data: Path, md: list, js: dict):
    path = data / "tool_census" / "census_tables.json"
    if not path.exists():
        md.append("## T5 Corpus\n\n(no census yet)\n")
        return
    c = json.loads(path.read_text())
    js["T5_census"] = {"all": c.get("all"), "run_share": c.get("run_share"), "average": c.get("average"),
                       "requests_all": c.get("requests", {}).get("all")}
    a = c["all"]
    md.append("## T5 End-to-end share in the corpus (tool_census.py; full tables in tool_census/census_tables.md)\n")
    md.append(f"{a['runs']} runs, {a['wall_ms'] / 3.6e6:,.0f} h of wall time: tools {a['tool_ms'] / 1000:,.0f} s = {pc(a['tool_ms'] / a['wall_ms'], 2)} "
              f"of wall ({pc(a['tool_ms_plain'] / a['wall_ms'], 2)} without task/Workflow, {pc(a['tool_ms_clean'] / a['wall_ms'], 3)} "
              f"without artifacts), blocked-on-tools {pc(a['blocked_ms'] / a['wall_ms'], 3)}.\n")
    rq = c.get("requests", {}).get("all", {})
    if rq:
        fb = rq.get("ftr_bound", {})
        md.append(f"Per request ({rq.get('n')} requests): FTR-bound share p50 {pc(fb.get('0.5'), 2)}, p90 {pc(fb.get('0.9'), 2)}, "
                  f"p99 {pc(fb.get('0.99'), 2)} -- against Sutradhara's 32 / 61 / 85 %.\n")


SUPPORTED = ["bash", "read_file", "write_file", "edit_file", "glob", "todo_write", "task", "load_skill", "compact",
             "create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task", "schedule_cron",
             "list_crons", "cancel_cron", "spawn_teammate", "list_teammates", "send_message", "request_shutdown",
             "request_plan", "review_plan", "create_worktree", "connect_mcp", "Workflow", "mcp__docs__search",
             "mcp__docs__get_version", "mcp__deploy__status", "mcp__deploy__trigger", "submit_plan"]


def overview_table(data: Path, md: list, js: dict):
    """T0: every supported tool in one row -- how often the corpus called it and what it cost there, what it
    costs today under control (median of its success-path dispatches and the range of its argument-class
    medians), and for the tools whose price is a model's work, the real-model medians of C3."""
    import lzma
    corpus = defaultdict(list)
    calls_path = data / "tool_census" / "calls.jsonl.xz"
    if calls_path.exists():
        with lzma.open(calls_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                c = json.loads(line)
                if not c["flags"]:
                    corpus[c["tool"]].append(c["net_ms"])
    sweep = load_sweep(data)
    base = "nfs-sum" if "nfs-sum" in sweep else (sorted(sweep)[0] if sweep else None)
    ctrl, cases = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for r in sweep.get(base, []) if base else []:
        if (r.get("phase") in ("main", "heavy") and r["status"] in ("ok", "scheduled") and r["role"] != "raw-git"
                and not re.search(r"sleep_1s|background_completion", r["case"])):   # calibration cases
            ctrl[r["tool"]].append(r["duration_ms"])
            cases[r["tool"]][r["case"]].append(r["duration_ms"])
    model = js.get("T4", {})
    md.append("## T0 Every supported tool: observed in the corpus vs controlled today\n")
    md.append("Corpus = every call in the repository's 1,801 traced runs (tool_census.py; artifacts excluded, all statuses); "
              f"controlled = the `{base}` sweep condition, success paths, all roles: the median of the argument-class "
              "medians (each class counts once; calibration cases excluded) and their range (cheapest to dearest class). For task / Workflow / compact the last column gives the real-model "
              "medians of T4 (q27 / q8, seconds).\n")
    md.append("| tool | corpus calls | corpus mean ms | corpus p99 ms | controlled: median class ms | argument-class range ms | real model (s) |")
    md.append("|---|---:|---:|---:|---:|---|---|")
    js["T0"] = {}
    for tool in SUPPORTED:
        xs, ys = corpus.get(tool, []), ctrl.get(tool, [])
        meds = [q(v, 0.5) for v in cases.get(tool, {}).values()]
        rng = f"{f(min(meds), 1)} - {f(max(meds), 1)}" if meds else "-"
        real = ""
        key = {"task": "task:", "Workflow": "workflow:", "compact": "compact:"}.get(tool)
        if key:
            vals = [f"{k.split('|')[0]} {k.split('|')[1].split(':', 1)[1]} {f(v['span_median_s'])}"
                    for k, v in model.items() if k.split("|")[1].startswith(key)]
            real = "; ".join(vals)
        md.append(f"| {tool} | {len(xs):,} | {f(mean(xs), 1)} | {f(q(xs, 0.99), 1)} | {f(q(meds, 0.5), 2)} | {rng} | {real} |")
        js["T0"][tool] = {"corpus_n": len(xs), "corpus_mean": mean(xs), "corpus_p99": q(xs, 0.99),
                          "controlled_median_of_case_medians": q(meds, 0.5), "controlled_n": len(ys),
                          "case_median_min": min(meds) if meds else None,
                          "case_median_max": max(meds) if meds else None}
    allx = [x for t in SUPPORTED for x in corpus.get(t, [])]
    md.append(f"\nCall-weighted average over the corpus (supported tools, artifacts excluded, task included): "
              f"{f(mean(allx), 1)} ms mean, {f(q(allx, 0.5), 1)} ms median, {f(q(allx, 0.99), 1)} ms p99 over {len(allx):,} calls.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--md", default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    data = Path(args.data)
    out_md = Path(args.md) if args.md else data / "tool_share" / "tool_share_tables.md"
    out_js = Path(args.json) if args.json else data / "tool_share" / "tool_share_tables.json"
    md, js = ["# research/06_tool_cost Part C tables\n",
              "Generated by `research/06_tool_cost/tool_share_analyze.py` from the data leaves listed in its docstring.\n"], {}
    sweep_tables(data, md, js)
    model_tables(data, md, js)
    t0: list = []
    overview_table(data, t0, js)
    md[2:2] = t0
    census_summary(data, md, js)
    delay_tables(data, md, js)
    heavy_tables(data, md, js)
    grid_table(data, md, js)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    out_js.write_text(json.dumps(js, indent=1, default=str), encoding="utf-8")
    print("\n".join(md)[:6000])
    print(f"[analyze] wrote {out_md} and {out_js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
