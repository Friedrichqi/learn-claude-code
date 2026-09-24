#!/usr/bin/env python3
"""Side-by-side comparison of two latency-breakdown run sets, e.g. GLM (z.ai) vs Qwen3.8-27B (local vLLM).

    python3 research/05_latency_breakdown/latency_compare.py \
        --a research/05_latency_breakdown/data/latency_profiling --a-name glm-5.3-flash \
        --b research/05_latency_breakdown/data/latency_profiling_qwen --b-name Qwen/Qwen3.8-27B \
        --out compare.md [--json compare.json] [--no-redundancy] [--repo REPO]

Both directories are analysed with latency_breakdown.Run (same round reconstruction, same token semantics),
then the tables of the weekly writeup are rendered with one column per provider (A, B) and, where a ratio makes
sense, B/A.  Sections:

  T0 setup            models, endpoints, harness commit, run counts, server (vLLM) version / GPU / flags
  T1 runs & quality   completed runs, scores, wall per run (mean +- sd), max_tokens share, errors, denials
  T2 one round        gross / prep / model / tools / other split per group and kind (lead, teammate)
  T3 inside the call  3-parameter regression (fixed, prefill, decode) both sides; B's server-measured
                      prefill / decode / queue from vLLM /metrics when present
  T4 tokens per call  prompt, computed, cache-read share, output, thinking share, prompt growth per round
  T5 redundancy       teammate re-sent / cache-read / cross-teammate bytes (ranges over runs)
  T6 rounds & wall    rounds per task, calls per run, wall composition
  T7 stability        mean +- sd across repetitions
  T8 auxiliary calls  memory recall / extraction / compaction calls
  T9 delivery         streaming delivery pattern (first block vs tail) by stop reason
  T10 probes          provider_probe.json summaries when present in either directory
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
import latency_breakdown as lb  # noqa: E402

AGENT = ("lead", "teammate", "one_shot")
CATS = ("FQA", "CODE", "MATH")


# ----------------------------------------------------------------------------- formatting
def fnum(v, d=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if isinstance(v, str):
        return v
    return f"{v:,.{d}f}"


def fpct(v, d=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{100 * v:.{d}f}%"


def ratio(a, b):
    if a is None or b is None or isinstance(a, str) or isinstance(b, str) or not a:
        return "-"
    return f"{b / a:.2f}x"


def mean(xs):
    return lb.mean(xs)


def median(xs):
    return lb.median(xs)


def share(num, den):
    return (num / den) if den else None


def msd(xs, d=1):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return "-"
    m = statistics.fmean(xs)
    if len(xs) < 2:
        return f"{m:,.{d}f}"
    sd = statistics.stdev(xs)
    return f"{m:,.{d}f} +- {sd:,.{d}f}"


def rng(xs, pct=True):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return "-"
    lo, hi = min(xs), max(xs)
    if pct:
        return f"{100 * lo:.0f}%" if abs(hi - lo) < 0.005 else f"{100 * lo:.0f}-{100 * hi:.0f}%"
    return f"{lo:,.1f}" if abs(hi - lo) < 0.05 else f"{lo:,.1f}-{hi:,.1f}"


# ----------------------------------------------------------------------------- data
class Side:
    def __init__(self, name: str, directory: Path, runs: list):
        self.name = name
        self.dir = directory
        self.runs = runs
        self.by_group: dict[tuple, list] = defaultdict(list)
        for run in runs:
            self.by_group[run.group].append(run)
        self.pipeline_meta = self._load_json(directory / "pipeline" / "meta.json")
        self.server_info = self._load_json(directory / "pipeline" / "server_info.json")
        self.probe = self._load_json(directory / "provider_probe.json")

    @staticmethod
    def _load_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except Exception:
            return None

    def groups(self):
        return sorted(self.by_group, key=lambda g: (g[1], g[0]))

    def rounds(self, group, kind):
        return [r for run in self.by_group.get(group, []) for r in run.rounds if r.kind == kind]

    def calls(self, group=None, kind=None, purposes=AGENT):
        runs = self.runs if group is None else self.by_group.get(group, [])
        return [c for run in runs for c in run.calls if c.purpose in purposes and (kind is None or c.kind == kind)]

    def has_server(self):
        return any(c.vllm for run in self.runs for c in run.calls)

    def models(self):
        return sorted({str((run.provider or {}).get("model")) for run in self.runs})

    def base_urls(self):
        return sorted({str((run.provider or {}).get("base_url")) for run in self.runs})


def load_side(name: str, directory: str, repo: str, redundancy: bool) -> Side:
    d = Path(directory)
    paths = lb.collect([str(d)])
    runs = [lb.Run(p, Path(repo), redundancy=redundancy) for p in paths]
    runs = [r for r in runs if r.calls]
    runs.sort(key=lambda r: (r.group[1], r.group[0], r.rep, r.label))
    return Side(name, d, runs)


# ----------------------------------------------------------------------------- metric helpers
def round_metrics(side: Side, group, kind) -> dict:
    rs = side.rounds(group, kind)
    if not rs:
        return {}
    gross = sum(r.gross for r in rs)
    calls = [r.call for r in rs if r.call]
    ttft = [c.ttft for c in calls if c.ttft is not None]
    tail = [c.decode for c in calls if c.decode is not None]
    runs = side.by_group.get(group, [])
    out = {
        "rounds": len(rs), "gross_mean_s": gross / len(rs) / 1000, "gross_median_s": (median([r.gross for r in rs]) or 0) / 1000,
        "gross_p90_s": (lb.quantile([r.gross for r in rs], 0.9) or 0) / 1000,
        "prep_share": share(sum(r.prep for r in rs), gross), "model_share": share(sum(r.model for r in rs), gross),
        "tools_share": share(sum(r.tools for r in rs), gross), "other_share": share(sum(r.other for r in rs), gross),
        "instrument_share": share(sum(r.instrument for r in rs), gross),
        "prep_mean_ms": sum(r.prep for r in rs) / len(rs), "tools_mean_ms": sum(r.tools for r in rs) / len(rs),
        "first_block_mean_s": (mean(ttft) or 0) / 1000 if ttft else None, "tail_mean_s": (mean(tail) or 0) / 1000 if tail else None,
        "tool_calls_per_round": sum(len(r.tool_calls) for r in rs) / len(rs),
    }
    if kind == "lead":
        turns = [t for run in runs for t in run.turns]
        post = sum(t["post_ms"] for t in turns)
        out["turns"] = len(turns)
        out["post_per_turn_s"] = post / len(turns) / 1000 if turns else None
        out["post_amortised_per_round_s"] = post / len(rs) / 1000
        out["prep_post_share_lead_side"] = share(sum(r.prep for r in rs) + post, gross + post)
    return out


def token_metrics(side: Side, group, kind) -> dict:
    cs = side.calls(group, kind)
    if not cs:
        return {}
    prompt = [c.prompt_tokens for c in cs]
    think = sum((c.sidecar or {}).get("output_thinking_chars", 0) or 0 for c in cs)
    allout = sum(((c.sidecar or {}).get("output_thinking_chars", 0) or 0) + ((c.sidecar or {}).get("output_text_chars", 0) or 0)
                 + ((c.sidecar or {}).get("output_tool_input_chars", 0) or 0) for c in cs)
    growth = []
    by_agent: dict[tuple, list] = defaultdict(list)
    for run in side.by_group.get(group, []):
        for c in run.calls:
            if c.purpose in AGENT and c.kind == kind:
                by_agent[(run.label, c.agent)].append(c)
    for seq in by_agent.values():
        growth += [b.prompt_tokens - a.prompt_tokens for a, b in zip(seq, seq[1:])]
    dec = [c for c in cs if c.decode]
    stops = Counter(c.stop_reason for c in cs)
    # character sizes are tokenizer-independent (Qwen3.8's tokenizer yields ~14% more tokens than GLM's on the same text)
    pchars = [((c.sidecar or {}).get("system_chars") or 0) + ((c.sidecar or {}).get("messages_chars") or 0) + ((c.sidecar or {}).get("tools_chars") or 0) for c in cs]
    ochars = [((c.sidecar or {}).get("output_text_chars") or 0) + ((c.sidecar or {}).get("output_thinking_chars") or 0) + ((c.sidecar or {}).get("output_tool_input_chars") or 0) for c in cs]
    return {
        "calls": len(cs), "prompt_mean": mean(prompt), "prompt_median": median(prompt), "prompt_max": max(prompt),
        "prompt_chars_mean": mean(pchars) if any(pchars) else None, "output_chars_mean": mean(ochars) if any(ochars) else None,
        "chars_per_prompt_tok": (sum(pchars) / sum(prompt)) if any(pchars) and sum(prompt) else None,
        "chars_per_output_tok": (sum(ochars) / sum(c.output_tokens for c in cs)) if any(ochars) and sum(c.output_tokens for c in cs) else None,
        "computed_mean": mean([c.uncached_tokens for c in cs]),
        "cache_read_share": share(sum(c.cached_tokens for c in cs), sum(prompt)),
        "cache_creation_share": share(sum(c.created_tokens for c in cs), sum(prompt)),
        "output_mean": mean([c.output_tokens for c in cs]), "output_median": median([c.output_tokens for c in cs]),
        "output_max": max(c.output_tokens for c in cs),
        "thinking_share": share(think, allout),
        "prompt_growth_median": median(growth) if growth else None, "prompt_growth_mean": mean(growth) if growth else None,
        "max_tokens_share": share(stops.get("max_tokens", 0), len(cs)), "tool_use_share": share(stops.get("tool_use", 0), len(cs)),
        "call_mean_s": mean([c.duration for c in cs]) / 1000,
        "tail_tok_s": (sum(c.output_tokens for c in dec) / (sum(c.decode for c in dec) / 1000)) if dec and sum(c.decode for c in dec) else None,
        "first_block_thinking_share": share(sum(1 for c in cs if (c.stream or {}).get("first_block_type") == "thinking"), sum(1 for c in cs if c.stream)),
    }


def task_metrics(side: Side, group) -> dict:
    runs = side.by_group.get(group, [])
    if not runs:
        return {}
    n = len(runs)
    lead_rounds = [r for run in runs for r in run.rounds if r.kind == "lead"]
    segs = [s for run in runs for s in run.teammate_segments()]
    turns = sum(len(run.turns) for run in runs)
    comps = [run.wall_composition() for run in runs]
    avg = lambda key: sum(c[key] for c in comps) / n  # noqa: E731
    wall = avg("wall_ms")
    return {
        "runs": n, "tm_rounds_task_mean": mean([s["rounds"] for s in segs]), "tm_rounds_task_median": median([s["rounds"] for s in segs]),
        "tm_rounds_task_max": max((s["rounds"] for s in segs), default=None),
        "tm_active_task_s": (mean([s["active_ms"] for s in segs]) or 0) / 1000 if segs else None,
        "tm_tasks_run": len(segs) / n, "teammates_run": sum(len([a for a, k in run.kinds.items() if k == "teammate"]) for run in runs) / n,
        "lead_rounds_run": len(lead_rounds) / n, "lead_turns_run": turns / n, "calls_run": sum(len(run.calls) for run in runs) / n,
        "wall_s": wall / 1000, "wall_task_s": wall / 1000 / 3,
        "lead_active_share": share(avg("lead_active_ms"), wall), "lead_model_s": avg("lead_model_ms") / 1000,
        "lead_post_s": avg("lead_post_ms") / 1000, "team_active_share": share(avg("team_active_ms"), wall),
        "overlap_s": avg("overlap_ms") / 1000, "idle_share": share(avg("idle_ms"), wall),
        "denied_run": sum(1 for run in runs for r in run.rounds for t in r.tool_calls if t[2] == "denied") / n,
    }


def quality(side: Side, group) -> dict:
    runs = side.by_group.get(group, [])
    if not runs:
        return {}
    scores = [run.score or {} for run in runs]
    cat = group[0]
    if cat == "FQA":
        cov = [s.get("coverage") for s in scores if s.get("coverage") is not None]
        score_text = f"coverage {msd(cov, 2)}" if cov else "-"
    else:
        correct = sum(s.get("correct") or 0 for s in scores)
        total = sum(s.get("total") or 0 for s in scores)
        score_text = f"{correct}/{total}" if total else "-"
    agent = side.calls(group)
    stops = Counter(c.stop_reason for c in agent)
    return {
        "runs": len(runs), "completed": sum(1 for run in runs if run.status in {"completed", "teammates-idle", "no-teammates"}),
        "timeouts": sum(1 for run in runs if run.status == "timeout"), "score": score_text,
        "wall": [run.wall_ms / 1000 for run in runs], "max_tokens_share": share(stops.get("max_tokens", 0), len(agent)),
        "model_errors": sum(sum(run.model_errors.values()) for run in runs),
        "denied": sum(1 for run in runs for r in run.rounds for t in r.tool_calls if t[2] == "denied"),
        "retries": sum(max(c.attempts - 1, 0) for run in runs for c in run.calls),
    }


def stability(side: Side, group) -> dict:
    rows = []
    for run in side.by_group.get(group, []):
        lead = [r for r in run.rounds if r.kind == "lead"]
        tm = [r for r in run.rounds if r.kind != "lead"]
        agent = [c for c in run.calls if c.purpose in AGENT]
        score = run.score or {}
        rows.append({"wall": run.wall_ms / 1000, "lead_rounds": len(lead), "calls": len(run.calls),
                     "lead_gross": (mean([r.gross for r in lead]) or 0) / 1000 if lead else None,
                     "tm_gross": (mean([r.gross for r in tm]) or 0) / 1000 if tm else None,
                     "output": mean([c.output_tokens for c in agent]), "prompt": mean([c.prompt_tokens for c in agent]),
                     "score": score.get("coverage") if score.get("category") == "FQA" else score.get("correct")})
    return {key: [r[key] for r in rows] for key in (rows[0] if rows else {})}


def regression(side: Side) -> dict:
    calls = side.calls()
    out = {}
    subsets = [("all agent calls", calls), ("lead", [c for c in calls if c.kind == "lead"]), ("teammate", [c for c in calls if c.kind == "teammate"]),
               ("responses ending in tool_use", [c for c in calls if c.stop_reason == "tool_use"]),
               ("responses ending in end_turn", [c for c in calls if c.stop_reason == "end_turn"])]
    for name, cs in subsets:
        fit = lb.fit3([(c.uncached_tokens, c.output_tokens, c.duration) for c in cs])
        out[name] = None if not fit else {"calls": len(cs), "a_s": fit[0][0] / 1000, "b_ms": fit[0][1], "c_ms": fit[0][2], "r2": fit[1]}
    return out


def regression_shares(side: Side, group, kind) -> dict:
    pooled = lb.fit3([(c.uncached_tokens, c.output_tokens, c.duration) for c in side.calls()])
    cs = side.calls(group, kind)
    if not pooled or not cs:
        return {}
    (a, b, cc), _ = pooled
    total = sum(c.duration for c in cs)
    fixed, prefill, decode = a * len(cs), b * sum(c.uncached_tokens for c in cs), cc * sum(c.output_tokens for c in cs)
    return {"calls": len(cs), "fixed": share(fixed, total), "prefill": share(prefill, total), "decode": share(decode, total),
            "residual": share(total - fixed - prefill - decode, total)}


def server_metrics(side: Side, group, kind) -> dict:
    cs = [c for c in side.calls(group, kind) if c.vllm]
    if not cs:
        return {}
    ex = [c for c in cs if c.vllm.get("exact")]
    pre = [c.server("prefill_s") for c in ex if c.server("prefill_s") is not None]
    dec = [c.server("decode_s") for c in ex if c.server("decode_s") is not None]
    que = [c.server("queue_s") for c in ex if c.server("queue_s") is not None]
    e2e = [c.server("e2e_s") for c in ex if c.server("e2e_s") is not None]
    gap = [c.duration / 1000 - c.server("e2e_s") for c in ex if c.server("e2e_s") is not None]
    kv = sum(c.server("kv_computed_tokens") or 0 for c in ex if c.server("prefill_s") is not None)
    gen = sum(max((c.server("request_generation_tokens") or c.output_tokens) - 1, 0) for c in ex if c.server("decode_s") is not None)
    total_call = sum(c.duration for c in ex) / 1000
    return {"calls": len(cs), "exact_share": share(len(ex), len(cs)), "prefill_mean_s": mean(pre), "decode_mean_s": mean(dec),
            "queue_mean_s": mean(que), "e2e_mean_s": mean(e2e), "gap_mean_s": mean(gap), "gap_median_s": median(gap),
            "prefill_tok_s": (kv / sum(pre)) if pre and sum(pre) else None, "decode_ms_tok": (1000 * sum(dec) / gen) if gen and dec else None,
            "server_prefill_share": share(sum(pre), total_call), "server_decode_share": share(sum(dec), total_call),
            "server_queue_share": share(sum(que), total_call), "gap_share": share(sum(gap), total_call),
            "length_share": share(sum(1 for c in ex if (c.vllm.get("finished") or {}).get("length")), len(ex)),
            "running_mean": mean([c.vllm.get("running") for c in cs]), "kv_usage_mean": mean([c.vllm.get("kv_usage") for c in cs]),
            "scrape_ms": mean([c.scrape_ms for c in cs])}


def aux_metrics(side: Side, group) -> dict:
    runs = side.by_group.get(group, [])
    out = {}
    n = len(runs) or 1
    for purpose in sorted({c.purpose for run in runs for c in run.calls if c.purpose not in AGENT}):
        cs = [c for run in runs for c in run.calls if c.purpose == purpose]
        out[purpose] = {"calls_run": len(cs) / n, "mean_s": mean([c.duration for c in cs]) / 1000, "prompt_mean": mean([c.prompt_tokens for c in cs]),
                        "output_mean": mean([c.output_tokens for c in cs]), "max_tokens_share": share(sum(1 for c in cs if c.stop_reason == "max_tokens"), len(cs)),
                        "total_run_s": sum(c.duration for c in cs) / n / 1000}
    return out


def delivery_metrics(side: Side, kind, stop) -> dict:
    cs = [c for c in side.calls(kind=kind) if c.ttft is not None and c.stop_reason == stop]
    if len(cs) < 3:
        return {}
    dec = [c for c in cs if c.decode]
    return {"calls": len(cs), "output_mean": mean([c.output_tokens for c in cs]), "first_block_s": mean([c.ttft for c in cs]) / 1000,
            "tail_s": mean([c.decode for c in cs]) / 1000, "tail_share": share(sum(c.decode for c in cs), sum(c.duration for c in cs)),
            "burst_share": share(sum(1 for c in cs if c.decode < 100), len(cs)),
            "tail_tok_s": (sum(c.output_tokens for c in dec) / (sum(c.decode for c in dec) / 1000)) if dec and sum(c.decode for c in dec) else None}


def probe_summary(probe) -> dict | None:
    if not probe:
        return None
    rows = probe if isinstance(probe, list) else probe.get("rows") or []
    fresh = [r for r in rows if str(r.get("label", "")).startswith("uncached") and not r.get("cached_tok")]
    served = [r for r in rows if r.get("label") == "cached-resend"]
    # probe rows written before 2026-09-22 carry vLLM's partial-tail input_tokens as uncached_tok; a re-derived
    # prompt_tok (tokenizer count of the exact prompt, added by the one-off scripts/probe_tokens.py, removed in the 2026-09-23 cleanup) takes precedence when present
    xs = lambda r: r.get("prompt_tok") or r.get("uncached_tok")  # noqa: E731
    fit = lb.linfit([xs(r) for r in fresh], [1000 * r["ttft_s"] for r in fresh]) if len(fresh) >= 3 else None
    decode = [r["decode_tok_s"] for r in rows if r.get("decode_tok_s")]
    out = {"calls": len(rows), "fresh_calls": len(fresh),
           "fit": None if not fit else {"a_s": fit["intercept"] / 1000, "b_ms": fit["slope"], "r2": fit["r2"], "tok_s": (1000 / fit["slope"]) if fit["slope"] > 0 else None},
           "decode_median_tok_s": median(decode), "decode_min_tok_s": min(decode) if decode else None, "decode_max_tok_s": max(decode) if decode else None,
           "burst_share": share(sum(1 for r in rows if (r.get("tail_s") or 0) < 0.1), len(rows)),
           "resends": [], "cache_cases": []}
    for resend in served:
        match = [r for r in fresh if r["chars"] == resend["chars"] and r["label"] == "uncached-r1"]
        if match:
            out["resends"].append({"cached_tok": resend["cached_tok"], "fresh_ttft_s": match[0]["ttft_s"], "cached_ttft_s": resend["ttft_s"],
                                   "saved_ms_per_tok": 1000 * (match[0]["ttft_s"] - resend["ttft_s"]) / max(resend["cached_tok"], 1)})
    for r in rows:
        label = str(r.get("label", ""))
        if label[:1].isdigit():
            total = (r.get("uncached_tok") or 0) + (r.get("cached_tok") or 0)
            out["cache_cases"].append({"case": label, "cached_share": share(r.get("cached_tok") or 0, total), "ttft_s": r.get("ttft_s")})
    return out


# ----------------------------------------------------------------------------- rendering
class Report:
    def __init__(self, a: Side, b: Side):
        self.a, self.b = a, b
        self.lines: list[str] = []
        self.data: dict = {"a": {"name": a.name, "dir": str(a.dir)}, "b": {"name": b.name, "dir": str(b.dir)}, "tables": {}}

    def h(self, text):
        self.lines += ["", f"## {text}", ""]

    def note(self, text):
        self.lines += [text, ""]

    def table(self, header: list[str], rows: list[list[str]], align_first_left=True):
        self.lines.append("| " + " | ".join(header) + " |")
        self.lines.append("|" + "|".join(("---" if i == 0 and align_first_left else "---:") for i in range(len(header))) + "|")
        for row in rows:
            self.lines.append("| " + " | ".join(str(x) for x in row) + " |")
        self.lines.append("")

    def metric_table(self, key: str, columns: list[tuple], metrics: list[tuple], fn, with_ratio=False):
        """rows = metrics, columns = groups x {A, B}.  columns: (group, kind, label); metrics: (label, field, formatter)."""
        cache = {}
        for group, kind, _ in columns:
            cache[(group, kind, "a")] = fn(self.a, group, kind) if kind is not None else fn(self.a, group)
            cache[(group, kind, "b")] = fn(self.b, group, kind) if kind is not None else fn(self.b, group)
        header = ["metric"]
        for _, _, label in columns:
            header += [f"{label} {self.a.name}", f"{label} {self.b.name}"] + (["B/A"] if with_ratio else [])
        rows = []
        stored = {}
        for label, field, formatter in metrics:
            row = [label]
            for group, kind, col_label in columns:
                va = cache[(group, kind, "a")].get(field)
                vb = cache[(group, kind, "b")].get(field)
                row += [formatter(va), formatter(vb)] + ([ratio(va, vb)] if with_ratio else [])
                stored.setdefault(col_label, {})[field] = {"a": va, "b": vb}
            rows.append(row)
        self.data["tables"][key] = stored
        self.table(header, rows)


def build(a: Side, b: Side, repo: Path) -> Report:
    rep = Report(a, b)
    L = rep.lines
    L += [f"# Latency breakdown: {a.name} vs {b.name}", "",
          f"A = `{a.name}` ({a.dir}), B = `{b.name}` ({b.dir}). Both analysed with `research/05_latency_breakdown/latency_breakdown.py` "
          "(same round reconstruction, `uncached` = tokens the provider computed = input_tokens + cache_creation_input_tokens). "
          "Means are over pooled rounds or calls unless a row says otherwise; B/A is the ratio of the two means."]

    # ---- T0 setup
    rep.h("T0. Setup")
    def meta(side: Side):
        pm = side.pipeline_meta or {}
        si = side.server_info or {}
        version = si.get("version") if isinstance(si.get("version"), dict) else si.get("version")
        heads = sorted({str((run.meta or {}).get("git_head"))[:10] for run in side.runs})
        limits = sorted({str((run.meta or {}).get("context_limit")) for run in side.runs})
        streams = sorted({str((run.meta or {}).get("stream")) for run in side.runs})
        counts = ", ".join(f"{g[0]}-{g[1]} x{len(rs)}" for g, rs in sorted(side.by_group.items(), key=lambda kv: (kv[0][1], kv[0][0])))
        dates = sorted({run.records[0].get("timestamp", "")[:10] for run in side.runs if run.records})
        return {"model": ", ".join(side.models()), "endpoint": ", ".join(side.base_urls()), "runs": counts, "dates": ", ".join(dates),
                "harness commit": ", ".join(heads), "CONTEXT_LIMIT (chars)": ", ".join(limits), "streaming": ", ".join(streams),
                "server": (json.dumps(version) if version else "-"), "GPU": f"{pm.get('gpu_name', '-')} {pm.get('gpu_mem_mib', '')} MiB".strip() if pm else "-",
                "vLLM flags": (f"max-model-len {pm.get('max_model_len')}, gpu-util {pm.get('gpu_util')}, max-num-seqs {pm.get('max_num_seqs')}, "
                               f"max-num-batched-tokens {pm.get('max_num_batched_tokens')}, extra: {pm.get('extra_vllm_args') or '-'}") if pm else "-",
                "software": (f"vllm {pm.get('vllm')}, torch {pm.get('torch')}, anthropic {pm.get('anthropic')}" if pm else "-"),
                "run timeout (s)": str(pm.get("max_seconds")) if pm else "-", "server metrics per call": "yes" if side.has_server() else "no"}
    ma, mb = meta(a), meta(b)
    rep.table(["item", a.name, b.name], [[k, ma[k], mb[k]] for k in ma])
    rep.data["tables"]["T0"] = {"a": ma, "b": mb}

    groups = sorted(set(a.by_group) | set(b.by_group), key=lambda g: (g[1], g[0]))
    team_groups = [g for g in groups if g[1] == "team"]
    solo_groups = [g for g in groups if g[1] == "solo"]

    # ---- T1 runs & quality
    rep.h("T1. Runs and quality")
    rows = []
    stored = {}
    for g in groups:
        qa, qb = quality(a, g), quality(b, g)
        stored[f"{g[0]}-{g[1]}"] = {"a": {k: v for k, v in qa.items() if k != "wall"}, "b": {k: v for k, v in qb.items() if k != "wall"}}
        rows.append([f"{g[0]}-{g[1]}", f"{qa.get('completed', 0)}/{qa.get('runs', 0)}", f"{qb.get('completed', 0)}/{qb.get('runs', 0)}",
                     qa.get("score", "-"), qb.get("score", "-"), msd(qa.get("wall", []), 0), msd(qb.get("wall", []), 0),
                     ratio(mean(qa.get("wall", [])), mean(qb.get("wall", []))), fpct(qa.get("max_tokens_share"), 0), fpct(qb.get("max_tokens_share"), 0),
                     fnum(qa.get("model_errors"), 0), fnum(qb.get("model_errors"), 0), fnum(qa.get("retries"), 0), fnum(qb.get("retries"), 0),
                     fnum(qa.get("denied"), 0), fnum(qb.get("denied"), 0)])
    rep.table(["group", f"completed {a.name}", f"completed {b.name}", f"score {a.name}", f"score {b.name}", f"wall/run s {a.name}", f"wall/run s {b.name}", "wall B/A",
               f"stop=max_tokens {a.name}", f"stop=max_tokens {b.name}", f"model errors {a.name}", f"model errors {b.name}", f"retried calls {a.name}", f"retried calls {b.name}",
               f"denied tools {a.name}", f"denied tools {b.name}"], rows)
    rep.data["tables"]["T1"] = stored

    # ---- T2 one round
    rep.h("T2. One agentic round (prep / model / tools / other add up to the round; instrument = profiler's vLLM metrics scrape, kept out of the buckets)")
    round_rows = [("rounds", "rounds", lambda v: fnum(v, 0)), ("gross per round, mean (s)", "gross_mean_s", fnum), ("gross median (s)", "gross_median_s", fnum),
                  ("gross p90 (s)", "gross_p90_s", fnum), ("context preparation share", "prep_share", fpct), ("agent call share", "model_share", fpct),
                  ("tool execution share", "tools_share", fpct), ("other share", "other_share", fpct), ("instrument share", "instrument_share", fpct),
                  ("prep mean (ms)", "prep_mean_ms", fnum), ("tools mean (ms)", "tools_mean_ms", fnum), ("tool calls per round", "tool_calls_per_round", lambda v: fnum(v, 2)),
                  ("client first block mean (s)", "first_block_mean_s", fnum), ("client streamed tail mean (s)", "tail_mean_s", fnum),
                  ("turn-end work per lead turn (s)", "post_per_turn_s", fnum), ("turn-end work amortised per lead round (s)", "post_amortised_per_round_s", fnum),
                  ("(prep + turn-end) share of lead-side time", "prep_post_share_lead_side", fpct)]
    rep.note("Lead rounds (team and solo):")
    rep.metric_table("T2_lead", [(g, "lead", f"{g[0]}-{g[1]} lead") for g in groups], round_rows, round_metrics)
    rep.note("Teammate rounds (team runs):")
    rep.metric_table("T2_teammate", [(g, "teammate", f"{g[0]} teammate") for g in team_groups], round_rows[:14], round_metrics)

    # ---- T3 inside the call
    rep.h("T3. Inside the agent call")
    rep.note("Three-parameter regression over agent calls: model-call duration = a + b x computed prompt tokens + c x output tokens (least squares).")
    ra, rb = regression(a), regression(b)
    rows = []
    for name in ra:
        fa, fb = ra.get(name) or {}, rb.get(name) or {}
        rows.append([name, fnum(fa.get("calls"), 0), fnum(fb.get("calls"), 0), fnum(fa.get("a_s"), 2), fnum(fb.get("a_s"), 2), fnum(fa.get("b_ms"), 4), fnum(fb.get("b_ms"), 4),
                     ("-" if not fa.get("b_ms") or fa["b_ms"] <= 0 else f"{1000 / fa['b_ms']:,.0f}"), ("-" if not fb.get("b_ms") or fb["b_ms"] <= 0 else f"{1000 / fb['b_ms']:,.0f}"),
                     fnum(fa.get("c_ms"), 2), fnum(fb.get("c_ms"), 2), fnum(fa.get("r2"), 2), fnum(fb.get("r2"), 2)])
    rep.table(["fit over", f"calls {a.name}", f"calls {b.name}", f"a fixed s {a.name}", f"a fixed s {b.name}", f"b ms/computed tok {a.name}", f"b ms/computed tok {b.name}",
               f"implied prefill tok/s {a.name}", f"implied prefill tok/s {b.name}", f"c ms/output tok {a.name}", f"c ms/output tok {b.name}", f"r2 {a.name}", f"r2 {b.name}"], rows)
    rep.data["tables"]["T3_fit"] = {"a": ra, "b": rb}
    rep.note("Share of call time by the pooled coefficients, per group and kind:")
    share_rows = [("calls", "calls", lambda v: fnum(v, 0)), ("fixed (network, queue, start-up)", "fixed", fpct), ("prefill of computed tokens", "prefill", fpct),
                  ("decode", "decode", fpct), ("residual", "residual", fpct)]
    cols = [(g, k, f"{g[0]}-{g[1]} {k}") for g in groups for k in (("lead", "teammate") if g[1] == "team" else ("lead",))]
    rep.metric_table("T3_shares", cols, share_rows, regression_shares)
    if a.has_server() or b.has_server():
        rep.note(f"Server-side measurement from vLLM /metrics (only for the side served by vLLM; exact-attribution calls; gap = client duration - server e2e = HTTP/SDK overhead):")
        srv_rows = [("calls with server metrics", "calls", lambda v: fnum(v, 0)), ("exactly attributable share", "exact_share", fpct),
                    ("server prefill mean (s)", "prefill_mean_s", lambda v: fnum(v, 3)), ("server decode mean (s)", "decode_mean_s", fnum), ("server queue mean (s)", "queue_mean_s", lambda v: fnum(v, 3)),
                    ("server e2e mean (s)", "e2e_mean_s", fnum), ("client gap mean (s)", "gap_mean_s", lambda v: fnum(v, 3)), ("client gap median (s)", "gap_median_s", lambda v: fnum(v, 3)),
                    ("server prefill share of client call", "server_prefill_share", fpct), ("server decode share of client call", "server_decode_share", fpct),
                    ("server queue share of client call", "server_queue_share", fpct), ("gap share of client call", "gap_share", fpct),
                    ("prefill tok/s (computed tokens / prefill s)", "prefill_tok_s", lambda v: fnum(v, 0)), ("decode ms per token", "decode_ms_tok", fnum),
                    ("finished by length (max_tokens) share", "length_share", fpct), ("requests running at finish, mean", "running_mean", fnum),
                    ("KV cache usage at finish, mean", "kv_usage_mean", fpct), ("metrics scrape per call (ms)", "scrape_ms", fnum)]
        rep.metric_table("T3_server", cols, srv_rows, server_metrics)

    # ---- T4 tokens
    rep.h("T4. Tokens per agent call")
    tok_rows = [("calls", "calls", lambda v: fnum(v, 0)), ("prompt tokens, mean", "prompt_mean", lambda v: fnum(v, 0)), ("prompt tokens, median", "prompt_median", lambda v: fnum(v, 0)),
                ("prompt chars, mean (tokenizer-independent)", "prompt_chars_mean", lambda v: fnum(v, 0)), ("chars per prompt token", "chars_per_prompt_tok", lambda v: fnum(v, 2)),
                ("output chars, mean (tokenizer-independent)", "output_chars_mean", lambda v: fnum(v, 0)), ("chars per output token", "chars_per_output_tok", lambda v: fnum(v, 2)),
                ("prompt tokens, max", "prompt_max", lambda v: fnum(v, 0)), ("computed (uncached) tokens, mean", "computed_mean", lambda v: fnum(v, 0)),
                ("provider cache-read share", "cache_read_share", fpct), ("cache-creation share (vLLM only)", "cache_creation_share", fpct),
                ("output tokens, mean", "output_mean", lambda v: fnum(v, 0)), ("output tokens, median", "output_median", lambda v: fnum(v, 0)), ("output tokens, max", "output_max", lambda v: fnum(v, 0)),
                ("thinking share of output chars", "thinking_share", fpct), ("first block is thinking", "first_block_thinking_share", fpct),
                ("prompt growth per round, median (tok)", "prompt_growth_median", lambda v: fnum(v, 0)), ("prompt growth per round, mean (tok)", "prompt_growth_mean", lambda v: fnum(v, 0)),
                ("stop = tool_use share", "tool_use_share", fpct), ("stop = max_tokens share", "max_tokens_share", fpct),
                ("model call mean (s)", "call_mean_s", fnum), ("streamed tail tok/s (pooled)", "tail_tok_s", lambda v: fnum(v, 0))]
    rep.note("Lead calls (team and solo):")
    rep.metric_table("T4_lead", [(g, "lead", f"{g[0]}-{g[1]} lead") for g in groups], tok_rows, token_metrics)
    rep.note("Teammate calls (team runs):")
    rep.metric_table("T4_teammate", [(g, "teammate", f"{g[0]} teammate") for g in team_groups], tok_rows, token_metrics)

    # ---- T5 redundancy
    rep.h("T5. Teammate input redundancy (ranges over the runs of a group)")
    def red(side: Side, group):
        runs = side.by_group.get(group, [])
        vals = defaultdict(list)
        for run in runs:
            resent, total, cached = run.resent_share("teammate")
            lres, ltot, lcached = run.resent_share("lead")
            r = run.redundancy or {}
            if r.get("error"):
                r = {}
            vals["resent"].append(share(resent, total)); vals["cache"].append(share(cached, total))
            vals["cross_range"].append(r.get("cross_rate_range")); vals["cross_prompt"].append(r.get("cross_prompt_share")); vals["file_prompt"].append(r.get("file_prompt_share"))
            vals["lead_resent"].append(share(lres, ltot)); vals["lead_cache"].append(share(lcached, ltot))
            t = run.server_totals or {}
            vals["server_cached"].append(share(t.get("prompt_tokens_cached") or 0, t.get("prompt_tokens") or 0) if t else None)
            vals["server_hit"].append(share(t.get("prefix_cache_hits") or 0, t.get("prefix_cache_queries") or 0) if t else None)
        return vals
    red_rows = [("re-sent: prompt tokens already sent in the same teammate's previous call", "resent"), ("served by the provider cache (usage cache-read share)", "cache"),
                ("server prefix-cache hit rate, whole run (vLLM only)", "server_hit"), ("server cached share of all prompt tokens, whole run (vLLM only)", "server_cached"),
                ("file bytes another teammate fetched first (exact (file,line))", "cross_range"), ("cross-redundant share of teammate prompt tokens", "cross_prompt"),
                ("file content share of teammate prompt tokens", "file_prompt"), ("lead: re-sent share", "lead_resent"), ("lead: cache-read share", "lead_cache")]
    header = ["metric"]
    for g in team_groups:
        header += [f"{g[0]} {a.name}", f"{g[0]} {b.name}"]
    rows = []
    stored = {}
    reds = {(g, "a"): red(a, g) for g in team_groups}
    reds.update({(g, "b"): red(b, g) for g in team_groups})
    for label, key in red_rows:
        row = [label]
        for g in team_groups:
            row += [rng(reds[(g, "a")].get(key, [])), rng(reds[(g, "b")].get(key, []))]
            stored.setdefault(g[0], {})[key] = {"a": reds[(g, "a")].get(key), "b": reds[(g, "b")].get(key)}
        rows.append(row)
    rep.table(header, rows)
    rep.data["tables"]["T5"] = stored

    # ---- T6 rounds per task & wall
    rep.h("T6. Agent runs per task and end-to-end time")
    task_rows = [("runs", "runs", lambda v: fnum(v, 0)), ("teammate rounds per task, mean", "tm_rounds_task_mean", fnum), ("teammate rounds per task, median", "tm_rounds_task_median", lambda v: fnum(v, 0)),
                 ("teammate rounds per task, max", "tm_rounds_task_max", lambda v: fnum(v, 0)), ("teammate active time per task (s)", "tm_active_task_s", lambda v: fnum(v, 0)),
                 ("teammates per run", "teammates_run", fnum), ("teammate tasks per run", "tm_tasks_run", fnum), ("lead rounds per run", "lead_rounds_run", fnum),
                 ("lead turns per run", "lead_turns_run", fnum), ("model calls per run (incl. memory)", "calls_run", fnum), ("wall per run (s)", "wall_s", lambda v: fnum(v, 0)),
                 ("wall per task (s)", "wall_task_s", lambda v: fnum(v, 0)), ("lead busy share of wall", "lead_active_share", fpct), ("of which lead agent calls (s)", "lead_model_s", lambda v: fnum(v, 0)),
                 ("of which turn-end memory work (s)", "lead_post_s", lambda v: fnum(v, 0)), ("teammate active share of wall", "team_active_share", fpct),
                 ("lead/teammate overlap (s)", "overlap_s", lambda v: fnum(v, 0)), ("idle share of wall", "idle_share", fpct), ("denied tool calls per run", "denied_run", fnum)]
    rep.metric_table("T6", [(g, None, f"{g[0]}-{g[1]}") for g in groups], task_rows, task_metrics, with_ratio=True)

    # ---- T7 stability
    rep.h("T7. Stability across repetitions (mean +- sd over the runs of a group)")
    rows = []
    stored = {}
    for g in groups:
        sa, sb = stability(a, g), stability(b, g)
        stored[f"{g[0]}-{g[1]}"] = {"a": sa, "b": sb}
        rows.append([f"{g[0]}-{g[1]}", f"{len(sa.get('wall', []))} / {len(sb.get('wall', []))}", msd(sa.get("wall", []), 0), msd(sb.get("wall", []), 0),
                     msd(sa.get("lead_rounds", []), 1), msd(sb.get("lead_rounds", []), 1), msd(sa.get("calls", []), 1), msd(sb.get("calls", []), 1),
                     msd(sa.get("lead_gross", []), 1), msd(sb.get("lead_gross", []), 1), msd(sa.get("tm_gross", []), 1), msd(sb.get("tm_gross", []), 1),
                     msd(sa.get("prompt", []), 0), msd(sb.get("prompt", []), 0), msd(sa.get("output", []), 0), msd(sb.get("output", []), 0),
                     msd(sa.get("score", []), 2), msd(sb.get("score", []), 2)])
    rep.table(["group", "runs A / B", f"wall s {a.name}", f"wall s {b.name}", f"lead rounds/run {a.name}", f"lead rounds/run {b.name}", f"model calls/run {a.name}", f"model calls/run {b.name}",
               f"lead gross/round s {a.name}", f"lead gross/round s {b.name}", f"teammate gross/round s {a.name}", f"teammate gross/round s {b.name}",
               f"prompt tok/call {a.name}", f"prompt tok/call {b.name}", f"output tok/call {a.name}", f"output tok/call {b.name}", f"score {a.name}", f"score {b.name}"], rows)
    rep.data["tables"]["T7"] = stored

    # ---- T8 auxiliary calls
    rep.h("T8. Auxiliary model calls made by the harness (memory recall / extraction, summary compaction)")
    rows = []
    stored = {}
    for g in groups:
        xa, xb = aux_metrics(a, g), aux_metrics(b, g)
        for purpose in sorted(set(xa) | set(xb)):
            pa, pb = xa.get(purpose, {}), xb.get(purpose, {})
            stored.setdefault(f"{g[0]}-{g[1]}", {})[purpose] = {"a": pa, "b": pb}
            rows.append([f"{g[0]}-{g[1]}", purpose, fnum(pa.get("calls_run")), fnum(pb.get("calls_run")), fnum(pa.get("mean_s")), fnum(pb.get("mean_s")), ratio(pa.get("mean_s"), pb.get("mean_s")),
                         fnum(pa.get("prompt_mean"), 0), fnum(pb.get("prompt_mean"), 0), fnum(pa.get("output_mean"), 0), fnum(pb.get("output_mean"), 0),
                         fpct(pa.get("max_tokens_share"), 0), fpct(pb.get("max_tokens_share"), 0), fnum(pa.get("total_run_s")), fnum(pb.get("total_run_s"))])
    rep.table(["group", "purpose", f"calls/run {a.name}", f"calls/run {b.name}", f"mean s {a.name}", f"mean s {b.name}", "B/A", f"prompt tok {a.name}", f"prompt tok {b.name}",
               f"output tok {a.name}", f"output tok {b.name}", f"stop=max_tokens {a.name}", f"stop=max_tokens {b.name}", f"total per run s {a.name}", f"total per run s {b.name}"], rows)
    rep.data["tables"]["T8"] = stored

    # ---- T9 delivery
    rep.h("T9. Streaming delivery pattern (first block = client time to the first content block; tail = first block to end of stream; tail < 0.1 s = whole response in one burst)")
    rows = []
    stored = {}
    for kind in ("lead", "teammate"):
        for stop in ("tool_use", "end_turn", "max_tokens"):
            da, db = delivery_metrics(a, kind, stop), delivery_metrics(b, kind, stop)
            if not da and not db:
                continue
            stored[f"{kind}/{stop}"] = {"a": da, "b": db}
            rows.append([kind, stop, fnum(da.get("calls"), 0), fnum(db.get("calls"), 0), fnum(da.get("output_mean"), 0), fnum(db.get("output_mean"), 0),
                         fnum(da.get("first_block_s"), 2), fnum(db.get("first_block_s"), 2), fnum(da.get("tail_s"), 2), fnum(db.get("tail_s"), 2),
                         fpct(da.get("tail_share"), 0), fpct(db.get("tail_share"), 0), fpct(da.get("burst_share"), 0), fpct(db.get("burst_share"), 0),
                         fnum(da.get("tail_tok_s"), 0), fnum(db.get("tail_tok_s"), 0)])
    rep.table(["kind", "response ends with", f"calls {a.name}", f"calls {b.name}", f"output tok {a.name}", f"output tok {b.name}", f"first block s {a.name}", f"first block s {b.name}",
               f"tail s {a.name}", f"tail s {b.name}", f"tail share {a.name}", f"tail share {b.name}", f"tail<0.1s {a.name}", f"tail<0.1s {b.name}", f"tail tok/s {a.name}", f"tail tok/s {b.name}"], rows)
    rep.data["tables"]["T9"] = stored

    # ---- T10 probes
    pa, pb = probe_summary(a.probe), probe_summary(b.probe)
    rep.h("T10. Direct provider probes (research/05_latency_breakdown/provider_probe.py: prompt-size sweep, cached resends, decode rate, cache semantics)")
    if not pa and not pb:
        rep.note("No provider_probe.json in either directory.")
    else:
        def fit_text(p):
            f = (p or {}).get("fit")
            return "-" if not f else f"{f['a_s']:.2f} s + {f['b_ms']:.4f} ms/tok (r2 {f['r2']:.2f}) -> {fnum(f.get('tok_s'), 0)} tok/s"
        rows = [["probe calls", fnum((pa or {}).get("calls"), 0), fnum((pb or {}).get("calls"), 0)],
                ["first block = a + b x uncached tokens (fresh calls)", fit_text(pa), fit_text(pb)],
                ["decode tok/s median (min-max)", (f"{fnum(pa['decode_median_tok_s'], 0)} ({fnum(pa['decode_min_tok_s'], 0)}-{fnum(pa['decode_max_tok_s'], 0)})" if pa and pa.get("decode_median_tok_s") else "-"),
                 (f"{fnum(pb['decode_median_tok_s'], 0)} ({fnum(pb['decode_min_tok_s'], 0)}-{fnum(pb['decode_max_tok_s'], 0)})" if pb and pb.get("decode_median_tok_s") else "-")],
                ["responses delivered in one burst (tail < 0.1 s)", fpct((pa or {}).get("burst_share"), 0), fpct((pb or {}).get("burst_share"), 0)]]
        for i in range(max(len((pa or {}).get("resends", [])), len((pb or {}).get("resends", [])))):
            def rs(p):
                r = (p or {}).get("resends", [])
                return "-" if i >= len(r) else f"{r[i]['cached_tok']:,} cached: {r[i]['fresh_ttft_s']:.2f}s -> {r[i]['cached_ttft_s']:.2f}s = {r[i]['saved_ms_per_tok']:.4f} ms saved/tok"
            rows.append([f"identical resend {i + 1}", rs(pa), rs(pb)])
        cases = sorted({c["case"] for p in (pa, pb) if p for c in p.get("cache_cases", [])})
        for case in cases:
            def cs(p):
                m = next((c for c in (p or {}).get("cache_cases", []) if c["case"] == case), None)
                return "-" if not m else f"{fpct(m['cached_share'], 1)} cached, first block {fnum(m['ttft_s'], 2)} s"
            rows.append([f"cache case {case}", cs(pa), cs(pb)])
        rep.table(["probe", a.name, b.name], rows)
    rep.data["tables"]["T10"] = {"a": pa, "b": pb}
    return rep


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="trace directory of side A (e.g. research/05_latency_breakdown/data/latency_profiling)")
    parser.add_argument("--a-name", default="A")
    parser.add_argument("--b", required=True, help="trace directory of side B (e.g. research/05_latency_breakdown/data/latency_profiling_qwen)")
    parser.add_argument("--b-name", default="B")
    parser.add_argument("--out", default=None, help="write the markdown report here (default: stdout)")
    parser.add_argument("--json", default=None, help="write the numbers behind the tables here")
    parser.add_argument("--repo", default=str(lb.REPO))
    parser.add_argument("--no-redundancy", action="store_true", help="skip the (slower) input_redundancy analysis")
    args = parser.parse_args(argv)
    a = load_side(args.a_name, args.a, args.repo, not args.no_redundancy)
    b = load_side(args.b_name, args.b, args.repo, not args.no_redundancy)
    if not a.runs or not b.runs:
        print(f"no analysable runs: A={len(a.runs)} B={len(b.runs)}", file=sys.stderr)
        return 2
    rep = build(a, b, Path(args.repo))
    text = "\n".join(rep.lines).rstrip() + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(rep.lines)} lines; A {len(a.runs)} runs, B {len(b.runs)} runs)")
    else:
        print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(rep.data, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
