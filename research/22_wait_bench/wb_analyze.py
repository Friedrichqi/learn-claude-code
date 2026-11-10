#!/usr/bin/env python3
"""Topic 22's analysis. Per run (N, condition): the request latency, the entry (task) end-to-end latency and the vLLM
throughput, with what explains them; then `wait` - `none` at each N.

Inputs per run:
  - every trial of data/jobs/<run>/:
      config.json (its queue link q<pos>__<task>), result.json (agent_execution started/finished, the reward, an
      exception), agent/s15/calls.jsonl (one record per model call: `t` from the child's start, `ms`, usage, the tools
      it called), agent/s15/wb_t0.json (the child's absolute start), agent/s15/traces/*.jsonl (tool_end: each `wait`'s
      real duration);
  - data/runs/<run>/scrape_*.jsonl: vLLM /metrics once a second.
Definitions:
  - full-N window: from the first moment N agents run at once until the queue is empty (the last task's agent phase
    starts), from the trials' agent_execution intervals;
  - request latency: a model call's `ms` (send -> full reply; calls are not streamed), every call that got a reply
    (lead, subagent and compaction calls; lead calls alone also shown); in the window: calls that started in it;
  - entry e2e: agent_execution finished - started (model calls, tools and waits; the agent's slot);
  - throughput: counter deltas over the window: requests/h (vllm:request_success_total), tokens/s (prompt +
    generation tokens) and generation tokens/s.
Intervals: 95 % bootstrap, 2,000 draws. Request and entry statistics resample tasks (a task's calls stay together);
wait - none pairs the tasks both runs finished; throughput resamples the window's 10-min blocks.

    python research/22_wait_bench/wb_analyze.py            # data/tables/wb.{md,json}
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import random
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent
DATA = DIR / "data"
JOBS = DATA / "jobs"
RUNS = DATA / "runs"
TABLES = DATA / "tables"
DRAWS = 2000
BLOCK_S = 600.0
STATS = ("avg", "p50", "p95", "p99")
KV_RULE = 0.8


# -- reading ----------------------------------------------------------------------------------------------------------
def ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if path.is_file():
        for line in path.open(errors="replace"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def tool_seconds(s15: Path) -> tuple[float, float]:
    """(seconds in `wait`, seconds in every other tool), from the s15 trace's tool_end events."""
    wait = other = 0.0
    for path in sorted(s15.glob("traces/*.jsonl")):
        for e in read_jsonl(path):
            if e.get("event") == "tool_end":
                d = e.get("data") or {}
                s = float(d.get("duration_ms") or 0.0) / 1000.0
                if d.get("tool") == "wait":
                    wait += s
                else:
                    other += s
    return wait, other


def load_trials(job: Path, bench_of: dict[str, str]) -> list[dict]:
    """One row per finished trial of a Harbor job."""
    rows = []
    for trial in sorted(p for p in job.iterdir() if (p / "result.json").is_file()):
        cfg = json.loads((trial / "config.json").read_text())
        link = Path(cfg["task"]["path"]).name
        pos, _, tid = link.partition("__")
        res = json.loads((trial / "result.json").read_text())
        ae = res.get("agent_execution") or {}
        a0, a1 = ts(ae.get("started_at")), ts(ae.get("finished_at"))
        reward = ((res.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        s15 = trial / "agent" / "s15"
        t0 = json.loads((s15 / "wb_t0.json").read_text())["t0"] if (s15 / "wb_t0.json").is_file() else a0
        calls, errors = [], 0
        for c in read_jsonl(s15 / "calls.jsonl"):
            if c.get("refused"):
                continue
            if c.get("error") or c.get("ms") is None:
                errors += 1
                continue
            u = c.get("usage") or {}
            tools = c.get("tools") or []
            calls.append({"t": (t0 or 0.0) + float(c.get("t") or 0.0), "ms": float(c["ms"]),
                          "lead": c.get("purpose") == "lead", "wait": "wait" in tools,
                          "wait_only": bool(tools) and set(tools) == {"wait"},
                          "prompt": int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                          + int(u.get("cache_creation_input_tokens") or 0),
                          "cached": int(u.get("cache_read_input_tokens") or 0), "out": int(u.get("output_tokens") or 0)})
        exc = (res.get("exception_info") or {}).get("exception_type")
        wait_s, tool_s = tool_seconds(s15)
        rows.append({"trial": trial.name, "pos": int(pos[1:]) if pos[1:].isdigit() else None, "id": tid,
                     "bench": bench_of.get(tid), "a0": a0, "a1": a1,
                     "e2e": (a1 - a0) if a0 is not None and a1 is not None else None,
                     "trial_s": (ts(res.get("finished_at")) or 0.0) - (ts(res.get("started_at")) or 0.0),
                     "reward": None if reward is None else float(reward), "exception": exc,
                     "limit": exc == "AgentTimeoutError", "calls": calls, "errors": errors,
                     "wait_s": wait_s, "tool_s": tool_s})
    return rows


def load_scrape(run_dir: Path) -> list[list[dict]]:
    """One segment per scrape file (one server: its counters start at 0), each (wall, metrics) once a second in time
    order. A run resumed in a later stage has several."""
    out = []
    for path in sorted(run_dir.glob("scrape_*.jsonl")):
        seg = [{"wall": float(r["wall"]), "m": r["m"]} for r in read_jsonl(path) if r.get("ok") and r.get("m")]
        if seg:
            out.append(sorted(seg, key=lambda r: r["wall"]))
    return out


# -- statistics -------------------------------------------------------------------------------------------------------
def pct(xs: list[float], q: float) -> float | None:
    """The q-th percentile, linear between closest ranks (numpy's default)."""
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "avg": sum(xs) / len(xs) if xs else None, "p50": pct(xs, 50), "p95": pct(xs, 95),
            "p99": pct(xs, 99)}


def ci(draws: list[float]) -> list[float | None]:
    d = sorted(x for x in draws if x is not None)
    if not d:
        return [None, None]
    return [d[int(0.025 * len(d))], d[min(len(d) - 1, int(0.975 * len(d)))]]


def boot_clustered(groups: dict[str, list[float]], seed: str) -> dict:
    """Each statistic's 95 % interval, resampling the groups (tasks) with replacement."""
    keys = sorted(groups)
    rng = random.Random(seed)
    draws = {s: [] for s in STATS}
    for _ in range(DRAWS):
        vals = [v for k in (rng.choice(keys) for _ in keys) for v in groups[k]]
        sm = summary(vals)
        for s in STATS:
            draws[s].append(sm[s])
    return {s: ci(draws[s]) for s in STATS}


def boot_paired(a: dict[str, list[float]], b: dict[str, list[float]], seed: str) -> dict:
    """b - a for each statistic, resampling the tasks both have (the same draw for both)."""
    keys = sorted(set(a) & set(b))
    out = {"n_tasks": len(keys)}
    if not keys:
        return out
    full_a = summary([v for k in keys for v in a[k]])
    full_b = summary([v for k in keys for v in b[k]])
    rng = random.Random(seed)
    draws = {s: [] for s in STATS}
    for _ in range(DRAWS):
        pick = [rng.choice(keys) for _ in keys]
        sa = summary([v for k in pick for v in a[k]])
        sb = summary([v for k in pick for v in b[k]])
        for s in STATS:
            draws[s].append(None if sa[s] is None or sb[s] is None else sb[s] - sa[s])
    for s in STATS:
        diff = None if full_a[s] is None or full_b[s] is None else full_b[s] - full_a[s]
        out[s] = {"diff": diff, "ratio": (full_b[s] / full_a[s]) if full_a[s] else None, "ci": ci(draws[s])}
    return out


# -- one run ------------------------------------------------------------------------------------------------------------
def window(trials: list[dict], n: int) -> dict:
    """The full-N window and the time-average of running agents in it (and over the whole run)."""
    iv = [(t["a0"], t["a1"]) for t in trials if t["a0"] is not None and t["a1"] is not None]
    if not iv:
        return {"w0": None, "w1": None}
    # at equal times an end comes first: Harbor starts an agent only after another gives its permit back
    events = sorted([(a, 1) for a, _ in iv] + [(b, -1) for _, b in iv], key=lambda e: (e[0], e[1]))
    active, w0, peak = 0, None, 0
    for t, d in events:
        active += d
        peak = max(peak, active)
        if w0 is None and active >= n:
            w0 = t
    w1 = max(a for a, _ in iv)
    start, end = min(a for a, _ in iv), max(b for _, b in iv)
    if w0 is None or w1 <= w0:
        w0, w1 = start, w1 if w1 > start else end

    def mean_active(lo: float, hi: float) -> float | None:
        if hi <= lo:
            return None
        return sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in iv) / (hi - lo)
    return {"w0": w0, "w1": w1, "minutes": (w1 - w0) / 60.0, "start": start, "end": end,
            "run_minutes": (end - start) / 60.0, "agents_in_window": mean_active(w0, w1),
            "agents_whole_run": mean_active(start, end), "peak_agents": peak, "reached_n": peak >= n}


COUNTERS = {"requests": "vllm:request_success_total", "prompt": "vllm:prompt_tokens_total",
            "generation": "vllm:generation_tokens_total", "preempt": "vllm:num_preemptions_total",
            "prefix_q": "vllm:prefix_cache_queries_total", "prefix_h": "vllm:prefix_cache_hits_total"}


def counter_at(scrape: list[dict], t: float) -> dict | None:
    """Counters of the last sample at or before t (the first one if t precedes the scrape)."""
    best = None
    for r in scrape:
        if r["wall"] <= t:
            best = r
        else:
            break
    best = best or (scrape[0] if scrape else None)
    if best is None:
        return None
    return {"wall": best["wall"], **{k: float(best["m"].get(name) or 0.0) for k, name in COUNTERS.items()}}


def rates(segments: list[list[dict]], lo: float, hi: float) -> dict | None:
    """Counter deltas over [lo, hi], summed over the server segments it overlaps (each segment's own counters)."""
    if segments and isinstance(segments[0], dict):     # a single segment given as a list of samples
        segments = [segments]
    dt, d = 0.0, {k: 0.0 for k in COUNTERS}
    for seg in segments:
        s_lo, s_hi = max(lo, seg[0]["wall"]), min(hi, seg[-1]["wall"])
        if s_hi <= s_lo:
            continue
        a, b = counter_at(seg, s_lo), counter_at(seg, s_hi)
        if not a or not b or b["wall"] <= a["wall"]:
            continue
        dt += b["wall"] - a["wall"]
        for k in COUNTERS:
            d[k] += b[k] - a[k]
    if dt <= 0:
        return None
    return {"seconds": dt, "requests_per_h": d["requests"] / dt * 3600.0,
            "tokens_per_s": (d["prompt"] + d["generation"]) / dt, "gen_tokens_per_s": d["generation"] / dt,
            "prompt_tokens_per_s": d["prompt"] / dt, "preemptions_per_min": d["preempt"] / dt * 60.0,
            "prefix_hit_rate": (d["prefix_h"] / d["prefix_q"]) if d["prefix_q"] > 0 else None}


def block_rates(scrape: list[dict], lo: float, hi: float) -> list[dict]:
    out, t = [], lo
    while t + BLOCK_S <= hi + 1e-6:
        r = rates(scrape, t, t + BLOCK_S)
        if r:
            out.append(r)
        t += BLOCK_S
    return out


def kv_profile(segments: list[list[dict]], lo: float, hi: float) -> dict:
    if segments and isinstance(segments[0], dict):
        segments = [segments]
    samples = [r for seg in segments for r in seg if lo <= r["wall"] <= hi]
    kv = [float(r["m"].get("vllm:kv_cache_usage_perc") or 0.0) for r in samples]
    run = [float(r["m"].get("vllm:num_requests_running") or 0.0) for r in samples]
    wait = [float(r["m"].get("vllm:num_requests_waiting") or 0.0) for r in samples]
    return {"samples": len(kv), "kv_mean": sum(kv) / len(kv) if kv else None, "kv_p50": pct(kv, 50),
            "kv_share_at_rule": (sum(x >= KV_RULE for x in kv) / len(kv)) if kv else None,
            "running_mean": sum(run) / len(run) if run else None, "waiting_mean": sum(wait) / len(wait) if wait else None}


def analyze_run(name: str, bench_of: dict[str, str]) -> dict:
    cells = read_jsonl(RUNS / name / "cell.jsonl")
    info = cells[-1] if cells else {}
    n = int(info.get("n") or 0)
    trials = load_trials(JOBS / name, bench_of)
    win = window(trials, n)
    scrape = load_scrape(RUNS / name)
    in_win = (lambda c: win["w0"] is not None and win["w0"] <= c["t"] <= win["w1"])
    req = {"window": {}, "whole": {}, "window_lead": {}}
    for key, keep in (("window", in_win), ("whole", lambda c: True), ("window_lead", lambda c: in_win(c) and c["lead"])):
        groups = {t["id"]: [c["ms"] / 1000.0 for c in t["calls"] if keep(c)] for t in trials}
        groups = {k: v for k, v in groups.items() if v}
        vals = [v for g in groups.values() for v in g]
        req[key] = {**summary(vals), "ci": boot_clustered(groups, f"req|{name}|{key}") if groups else {}}
    e2e = {t["id"]: [t["e2e"]] for t in trials if t["e2e"] is not None}
    entry = {**summary([v[0] for v in e2e.values()]), "ci": boot_clustered(e2e, f"e2e|{name}") if e2e else {}}
    by_bench = {}
    for b in ("swe", "swepro", "tbench"):
        tb = [t for t in trials if t["bench"] == b]
        by_bench[b] = {"tasks": len(tb), "entry": summary([t["e2e"] for t in tb if t["e2e"] is not None]),
                       "request_window": summary([c["ms"] / 1000.0 for t in tb for c in t["calls"] if in_win(c)]),
                       "pass": (sum((t["reward"] or 0.0) for t in tb) / len(tb)) if tb else None}
    thr = {"window": rates(scrape, win["w0"], win["w1"]) if win["w0"] else None,
           "whole": rates(scrape, win.get("start"), win.get("end")) if win.get("start") else None}
    blocks = block_rates(scrape, win["w0"], win["w1"]) if win["w0"] else []
    if blocks:
        rng = random.Random(f"thr|{name}")
        for k in ("requests_per_h", "tokens_per_s", "gen_tokens_per_s"):
            draws = []
            for _ in range(DRAWS):
                pick = [rng.choice(blocks) for _ in blocks]
                draws.append(sum(p[k] for p in pick) / len(pick))
            thr[f"{k}_ci"] = ci(draws)
    thr["blocks"] = len(blocks)
    thr["_blocks"] = blocks
    calls = [c for t in trials for c in t["calls"]]
    calls_w = [c for c in calls if in_win(c)]
    waits = {"calls_with_wait": sum(c["wait"] for c in calls) / len(calls) if calls else None,
             "calls_wait_only": sum(c["wait_only"] for c in calls) / len(calls) if calls else None,
             "wait_calls_per_task": sum(c["wait"] for c in calls) / len(trials) if trials else None,
             "wait_s_per_task": sum(t["wait_s"] for t in trials) / len(trials) if trials else None,
             "tasks_that_waited": sum(t["wait_s"] > 0 for t in trials),
             "window_calls_with_wait": sum(c["wait"] for c in calls_w) / len(calls_w) if calls_w else None}
    tasks_done = [t for t in trials if t["a1"] is not None]
    return {"name": name, "n": n, "cond": info.get("cond"), "group": info.get("group"), "trials": len(trials),
            "tasks_listed": info.get("tasks"), "limit_hits": sum(t["limit"] for t in trials),
            "exceptions": sorted({t["exception"] for t in trials if t["exception"]}),
            "call_errors": sum(t["errors"] for t in trials), "calls": len(calls), "calls_in_window": len(calls_w),
            "pass_rate": (sum((t["reward"] or 0.0) for t in trials) / len(trials)) if trials else None,
            "tasks_per_h_whole": (len(tasks_done) / (win["run_minutes"] / 60.0)) if win.get("run_minutes") else None,
            "window": win, "request": req, "entry": entry, "by_bench": by_bench, "throughput": thr,
            "kv": kv_profile(scrape, win["w0"], win["w1"]) if win["w0"] else {}, "waits": waits,
            "tool_s_per_task": (sum(t["tool_s"] for t in trials) / len(trials)) if trials else None,
            "model_s_per_task": (sum(c["ms"] for c in calls) / 1000.0 / len(trials)) if trials else None,
            "_groups": {"req_window": {t["id"]: [c["ms"] / 1000.0 for c in t["calls"] if in_win(c)] for t in trials},
                        "e2e": {k: v for k, v in e2e.items()},
                        "reward": {t["id"]: [t["reward"] or 0.0] for t in trials}}}


def contrast(none: dict, wait: dict) -> dict:
    """wait - none at one N."""
    a_req = {k: v for k, v in none["_groups"]["req_window"].items() if v}
    b_req = {k: v for k, v in wait["_groups"]["req_window"].items() if v}
    out = {"n": none["n"], "request_window": boot_paired(a_req, b_req, f"c|req|{none['n']}"),
           "entry": boot_paired(none["_groups"]["e2e"], wait["_groups"]["e2e"], f"c|e2e|{none['n']}"),
           "throughput": {}}
    pas = boot_paired(none["_groups"]["reward"], wait["_groups"]["reward"], f"c|pass|{none['n']}")
    out["pass"] = {"n_tasks": pas["n_tasks"], **(pas.get("avg") or {})}     # the pass-rate difference, by task
    ba, bb = none["throughput"].get("_blocks") or [], wait["throughput"].get("_blocks") or []
    rng = random.Random(f"c|thr|{none['n']}")
    for k in ("requests_per_h", "tokens_per_s", "gen_tokens_per_s"):
        a, b = (none["throughput"].get("window") or {}).get(k), (wait["throughput"].get("window") or {}).get(k)
        ratios = []
        if ba and bb:                              # the two runs' 10-min blocks, resampled independently
            for _ in range(DRAWS):
                ma = sum(rng.choice(ba)[k] for _ in ba) / len(ba)
                mb = sum(rng.choice(bb)[k] for _ in bb) / len(bb)
                ratios.append(mb / ma if ma else None)
        out["throughput"][k] = {"none": a, "wait": b, "ratio": (b / a) if a and b else None, "ratio_ci": ci(ratios),
                                "none_ci": none["throughput"].get(f"{k}_ci"), "wait_ci": wait["throughput"].get(f"{k}_ci")}
    return out


# -- tables -------------------------------------------------------------------------------------------------------------
def _s(x, digits=1):
    return "—" if x is None else f"{x:.{digits}f}"


def _ci(c, digits=1):
    return "" if not c or c[0] is None else f" [{c[0]:.{digits}f}, {c[1]:.{digits}f}]"


def render(res: dict) -> str:
    out = ["# Topic 22: the KV-threshold `wait` prompt with 4-32 concurrent MiniMax-M2.7 agents (wb_analyze.py)", "",
           "Seconds unless noted. request = one model call (send -> full reply); entry = one task's agent phase "
           "(model calls, tools, waits); window = the full-N window (N agents at once until the queue is empty); 95 % "
           "bootstrap intervals (tasks resampled; throughput: 10-min blocks).", ""]
    runs = sorted(res["runs"].values(), key=lambda r: (r["n"], r["cond"] or ""))
    out += ["## Runs", "", "| run | N | condition | tasks | window (min) | agents in window | run (min) | limit hits | "
            "call errors | pass rate |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        w = r["window"]
        out.append(f"| `{r['name']}` | {r['n']} | `{r['cond']}` | {r['trials']} | {_s(w.get('minutes'))} | "
                   f"{_s(w.get('agents_in_window'))} | {_s(w.get('run_minutes'))} | {r['limit_hits']} | "
                   f"{r['call_errors']} | {_s(100 * r['pass_rate'] if r['pass_rate'] is not None else None, 0)} % |")
    for title, key in (("Request latency in the window (s)", ("request", "window")),
                       ("Entry end-to-end latency (s)", ("entry",))):
        out += ["", f"## {title}", "", "| run | n | avg | p50 | p95 | p99 |", "|---|---|---|---|---|---|"]
        for r in runs:
            d = r[key[0]] if len(key) == 1 else r[key[0]][key[1]]
            c = d.get("ci") or {}
            out.append(f"| `{r['name']}` | {d['n']} | " + " | ".join(f"{_s(d[s])}{_ci(c.get(s))}" for s in STATS) + " |")
    out += ["", "## Throughput in the window", "", "| run | requests/h | tokens/s | generation tokens/s | prefix hit rate | "
            "preemptions/min | KV mean | time at KV >= 80 % |", "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        t, k = r["throughput"].get("window") or {}, r["kv"]
        out.append(f"| `{r['name']}` | {_s(t.get('requests_per_h'), 0)}{_ci(r['throughput'].get('requests_per_h_ci'), 0)} | "
                   f"{_s(t.get('tokens_per_s'), 0)}{_ci(r['throughput'].get('tokens_per_s_ci'), 0)} | "
                   f"{_s(t.get('gen_tokens_per_s'), 0)}{_ci(r['throughput'].get('gen_tokens_per_s_ci'), 0)} | "
                   f"{_s(t.get('prefix_hit_rate'), 3)} | {_s(t.get('preemptions_per_min'))} | {_s(k.get('kv_mean'), 3)} | "
                   f"{_s(100 * k['kv_share_at_rule'] if k.get('kv_share_at_rule') is not None else None, 0)} % |")
    out += ["", "## Waits", "", "| run | calls with wait | wait-only calls | wait calls per task | seconds waited per task | "
            "tasks that waited |", "|---|---|---|---|---|---|"]
    for r in runs:
        w = r["waits"]
        pc = (lambda x: "—" if x is None else f"{100 * x:.1f} %")
        out.append(f"| `{r['name']}` | {pc(w['calls_with_wait'])} | {pc(w['calls_wait_only'])} | "
                   f"{_s(w['wait_calls_per_task'], 2)} | {_s(w['wait_s_per_task'])} | {w['tasks_that_waited']} |")
    if res["contrasts"]:
        out += ["", "## wait − none at each N (paired by task)", "",
                "| N | request avg | request p50 | request p95 | request p99 | entry avg | entry p50 | entry p95 | entry p99 | "
                "requests/h ratio | tokens/s ratio | pass rate (points) |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n, c in sorted(res["contrasts"].items(), key=lambda kv: int(kv[0])):
            cells = []
            for part in ("request_window", "entry"):
                for s in STATS:
                    d = c[part].get(s) or {}
                    cells.append(f"{_s(d.get('diff'))}{_ci(d.get('ci'))}")
            th = c["throughput"]
            cells += [f"{_s(th[k]['ratio'], 3)}{_ci(th[k].get('ratio_ci'), 3)}" for k in ("requests_per_h", "tokens_per_s")]
            pas = c.get("pass") or {}
            pts = [None if x is None else 100 * x for x in (pas.get("ci") or [None, None])]
            cells.append(f"{_s(None if pas.get('diff') is None else 100 * pas['diff'])}{_ci(pts)}")
            out.append(f"| {n} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def bench_map() -> dict[str, str]:
    tasks = json.loads((DATA / "tasks.json").read_text()) if (DATA / "tasks.json").is_file() else {}
    out = {}
    for t in tasks.get("main", []) + tasks.get("smoke", []):
        out[t["id"]] = t["bench"]
    for group in (tasks.get("reserves") or {}).values():
        for t in group:
            out[t["id"]] = t["bench"]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="*", help="default: every run with a cell.jsonl and a Harbor job, except dry_*")
    ap.add_argument("--out", default="wb")
    a = ap.parse_args()
    names = a.runs or sorted(p.name for p in RUNS.iterdir() if (p / "cell.jsonl").is_file() and (JOBS / p.name).is_dir()
                             and not p.name.startswith("dry_"))
    bench_of = bench_map()
    runs = {name: analyze_run(name, bench_of) for name in names}
    contrasts = {}
    for n in sorted({r["n"] for r in runs.values()}):
        by = {r["cond"]: r for r in runs.values()                  # n<N>_<cond> only: not a superseded run's copy
              if r["n"] == n and r["group"] == "main" and r["name"] == f"n{n}_{r['cond']}"}
        if "none" in by and "wait" in by:
            contrasts[str(n)] = contrast(by["none"], by["wait"])
    res = {"runs": runs, "contrasts": contrasts}
    TABLES.mkdir(parents=True, exist_ok=True)
    clean = {"runs": {k: {**{kk: vv for kk, vv in v.items() if kk != "_groups"},
                          "throughput": {kk: vv for kk, vv in v["throughput"].items() if kk != "_blocks"}}
                      for k, v in runs.items()},
             "contrasts": contrasts}
    (TABLES / f"{a.out}.json").write_text(json.dumps(clean, indent=1, default=str) + "\n")
    (TABLES / f"{a.out}.md").write_text(render(res))
    print((TABLES / f"{a.out}.md").read_text())


if __name__ == "__main__":
    main()
