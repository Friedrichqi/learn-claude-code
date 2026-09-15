#!/usr/bin/env python3
"""Average latency per tool-use function, lead pool vs teammate pool, two ways.

    python3 s15_integrated_harness/scripts/tool_latency_analyze.py \
        --records 's15_integrated_harness/traces/tool_latency/*.records.jsonl' \
        --traces  s15_integrated_harness/traces/latency_profiling \
        --md s15_integrated_harness/traces/tool_latency/tool_latency_tables.md \
        --json s15_integrated_harness/traces/tool_latency/tool_latency_tables.json

Basis A (controlled): the records sidecars written by scripts/tool_latency_probe.py -- every
tool of both pools executed through the real dispatch path (hooks + handler + trace span) with
fixed representative arguments, no model calls.  Warmup rows are dropped; status is reported
per case.

Basis B (observational): the tool_start -> tool_end spans of the 2026-09-12 latency-profiling
runs (traces/latency_profiling), split by agent_kind (lead / teammate / one_shot) and by status
(ok / denied / error).  These carry real arguments and real in-run conditions, but only the
tools those workloads happened to call.

The comparison table puts both bases next to each other for every tool that appears in both.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records",
                        default="s15_integrated_harness/traces/tool_latency/*.records.jsonl")
    parser.add_argument("--traces", default="s15_integrated_harness/traces/latency_profiling")
    parser.add_argument("--md", default=None)
    parser.add_argument("--json", default=None)
    return parser.parse_args()


def fmt(ms):
    if ms != ms:  # NaN
        return "--"
    if ms >= 100:
        return f"{ms:.0f}"
    if ms >= 10:
        return f"{ms:.1f}"
    return f"{ms:.2f}"


class Stats:
    """Parallel lists of durations and statuses, with ok-only views."""

    def __init__(self):
        self.values = []
        self.statuses = []

    def add(self, ms, status):
        self.values.append(ms)
        self.statuses.append(status)

    def extend(self, other):
        self.values.extend(other.values)
        self.statuses.extend(other.statuses)

    @property
    def n(self):
        return len(self.values)

    def count(self, status):
        return sum(1 for s in self.statuses if s == status)

    def of_status(self, *statuses):
        view = Stats()
        for ms, status in zip(self.values, self.statuses):
            if status in statuses:
                view.add(ms, status)
        return view

    def mean(self):
        return statistics.fmean(self.values) if self.values else float("nan")

    def median(self):
        return statistics.median(self.values) if self.values else float("nan")

    def pct(self, p):
        if not self.values:
            return float("nan")
        xs = sorted(self.values)
        k = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
        return xs[k]

    def max(self):
        return max(self.values) if self.values else float("nan")


def load_probe(pattern: str):
    """(role, case) -> Stats, one row per timed dispatch, warmup and case errors dropped."""
    by_case = defaultdict(Stats)
    errors = []
    files = sorted(glob.glob(pattern))
    for path in files:
        for line in open(path, encoding="utf-8"):
            record = json.loads(line)
            if record.get("role") == "error":
                errors.append(record)
                continue
            if record.get("warmup"):
                continue
            by_case[(record["role"], record["case"])].add(record["duration_ms"],
                                                          record["status"])
    return by_case, files, errors


def load_observational(trace_dir: str):
    """(kind, tool) -> Stats from tool_start/tool_end span pairs of every run trace."""
    by_tool = defaultdict(Stats)
    runs = sorted(Path(trace_dir).glob("run_*.jsonl"))
    runs = [r for r in runs if not r.name.endswith((".inputs.jsonl", ".reads.jsonl"))]
    for path in runs:
        open_spans = {}
        for line in open(path, encoding="utf-8"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = event.get("event")
            span_id = event.get("span_id")
            if name == "tool_start" and span_id:
                open_spans[span_id] = (event["monotonic_ns"],
                                       event.get("agent_kind") or "lead",
                                       (event.get("data") or {}).get("tool", "unknown"))
            elif name == "tool_end" and span_id in open_spans:
                started_ns, kind, tool = open_spans.pop(span_id)
                ms = (event["monotonic_ns"] - started_ns) / 1e6
                status = (event.get("data") or {}).get("status", "unknown")
                by_tool[(kind, tool)].add(ms, status)
    return by_tool, runs


CASE_ORDER = [
    # lead: shell and files
    "bash_ls", "bash_grep", "bash_python_file", "bash_background_start", "bash_denied_mutating",
    "read_file_13KB", "read_file_26KB", "read_file_45KB", "read_file_146KB", "read_file_limit50",
    "write_file_200B", "write_file_20KB", "edit_file_small",
    "glob_shallow", "glob_deep_recursive", "todo_write_5",
    "task_dispatch_only", "load_skill_hit", "load_skill_miss", "compact_marker",
    # lead: task board and cron
    "create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task",
    "schedule_cron", "list_crons", "cancel_cron",
    # lead: team
    "spawn_teammate", "list_teammates", "send_message", "request_plan", "request_shutdown",
    "review_plan", "create_worktree_denied", "create_worktree_real",
    # lead: MCP
    "connect_mcp_cold", "mcp_docs_search", "mcp_docs_get_version", "mcp_deploy_status",
    "mcp_deploy_trigger_confirm",
]

TM_ORDER = [
    "tm_bash_display", "tm_bash_denied_async", "tm_read_file_13KB", "tm_write_file_2KB",
    "tm_edit_file_small", "tm_glob_deep_recursive", "tm_send_message", "tm_submit_plan",
    "tm_list_tasks", "tm_claim_task", "tm_complete_task", "tm_plan_gate_blocked",
]

CASE_TO_TOOL = {
    "bash": "bash", "read_file": "read_file", "write_file": "write_file",
    "edit_file": "edit_file", "glob": "glob", "todo_write": "todo_write", "task": "task",
    "load_skill": "load_skill", "compact": "compact", "create_task": "create_task",
    "update_task": "update_task", "list_tasks": "list_tasks", "get_task": "get_task",
    "claim_task": "claim_task", "complete_task": "complete_task",
    "schedule_cron": "schedule_cron", "list_crons": "list_crons", "cancel_cron": "cancel_cron",
    "spawn_teammate": "spawn_teammate", "list_teammates": "list_teammates",
    "send_message": "send_message", "request_plan": "request_plan",
    "request_shutdown": "request_shutdown", "review_plan": "review_plan",
    "create_worktree": "create_worktree", "connect_mcp": "connect_mcp",
    "submit_plan": "submit_plan",
}


def tool_of(case: str) -> str:
    """Case name -> the tool whose pool entry produced it (mcp tools pooled as mcp__*)."""
    base = case[3:] if case.startswith("tm_") else case
    if base.startswith("mcp"):
        return "mcp__*"
    for prefix in sorted(CASE_TO_TOOL, key=len, reverse=True):
        if base == prefix or base.startswith(prefix + "_"):
            return CASE_TO_TOOL[prefix]
    return base.split("_")[0]


def render(by_case, files, errors, by_tool, runs):
    out = []
    n_min = min(s.n for s in by_case.values())
    n_max = max(s.n for s in by_case.values())

    out.append("# Per-tool execution latency: lead pool vs teammate pool")
    out.append("")
    out.append(f"Probe runs: {', '.join(Path(f).name for f in files)}; "
               f"each case holds {n_min}-{n_max} timed dispatches (warm-up pass dropped, "
               "shuffled order, no model calls).")
    if errors:
        out.append(f"Case errors (dropped): {len(errors)} "
                   f"({', '.join(sorted({e['tool'] for e in errors}))}).")
    out.append("")

    for role, title, order in (
            ("lead", "Basis A. Lead pool (assemble_tool_pool: 26 built-ins + mock MCP)",
             CASE_ORDER),
            ("teammate", "Basis A. Teammate pool (the ten sub_handlers of the teammate loop)",
             TM_ORDER)):
        out.append(f"## {title}")
        out.append("")
        out.append("| case | n | mean ms | median ms | p90 ms | max ms | status |")
        out.append("|---|---:|---:|---:|---:|---:|---|")
        seen = set()
        for case in order:
            stats = by_case.get((role, case))
            if not stats:
                continue
            seen.add(case)
            status = ", ".join(f"{k} {v}" for k, v in sorted(
                {s: stats.count(s) for s in stats.statuses}.items()))
            out.append(f"| {case} | {stats.n} | {fmt(stats.mean())} | {fmt(stats.median())} | "
                       f"{fmt(stats.pct(90))} | {fmt(stats.max())} | {status} |")
        for (r, case), stats in by_case.items():
            if r == role and case not in seen:
                status = ", ".join(f"{k} {v}" for k, v in sorted(
                    {s: stats.count(s) for s in stats.statuses}.items()))
                out.append(f"| {case} | {stats.n} | {fmt(stats.mean())} | {fmt(stats.median())} | "
                           f"{fmt(stats.pct(90))} | {fmt(stats.max())} | {status} |")
        out.append("")

        tool_stats = defaultdict(Stats)
        for (r, case), stats in by_case.items():
            if r != role:
                continue
            tool_stats[tool_of(case)].extend(stats)
        out.append("Pooled by tool over all argument classes:")
        out.append("")
        out.append("| tool | n | ok | denied | error/sched | mean ms | median ms | p90 ms |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        rows = sorted(tool_stats.items(), key=lambda kv: -kv[1].of_status("ok").mean())
        for tool, stats in rows:
            ok = stats.of_status("ok")
            if not ok.n:
                continue
            other = stats.n - ok.n
            out.append(f"| {tool} | {stats.n} | {ok.n} | {stats.count('denied')} | "
                       f"{stats.count('scheduled') + stats.count('error')} | "
                       f"{fmt(ok.mean())} | {fmt(ok.median())} | {fmt(ok.pct(90))} |")
        out.append("")

    out.append("## Basis B. Observational tool spans of the 2026-09-12 latency-profiling runs")
    out.append("")
    out.append(f"{len(runs)} run traces, tool_start -> tool_end pairs, split by agent_kind. "
               "Real arguments, real in-run conditions; only the tools those workloads called.")
    out.append("")
    for kind in ("lead", "teammate", "one_shot"):
        rows = [(tool, s) for (k, tool), s in by_tool.items() if k == kind]
        if not rows:
            continue
        rows.sort(key=lambda kv: -kv[1].of_status("ok").mean())
        out.append(f"### {kind}")
        out.append("")
        out.append("| tool | calls | ok | denied | error | mean ms | median ms | p90 ms | max ms |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for tool, s in rows:
            out.append(f"| {tool} | {s.n} | {s.count('ok')} | {s.count('denied')} | "
                       f"{s.count('error')} | {fmt(s.mean())} | {fmt(s.median())} | "
                       f"{fmt(s.pct(90))} | {fmt(s.max())} |")
        out.append("")

    out.append("## Basis A vs B, same tool (success paths only)")
    out.append("")
    out.append("| tool | probe lead ms | observed lead ms | probe teammate ms | "
               "observed teammate ms |")
    out.append("|---|---:|---:|---:|---:|")

    def probe_ok_stats(tool, role):
        pooled = Stats()
        for (r, case), stats in by_case.items():
            if r != role or tool_of(case) != tool:
                continue
            if "denied" in case or "gate" in case:
                continue
            pooled.extend(stats)
        return pooled.of_status("ok")

    tools = sorted({t for (_, t) in by_tool} | {tool_of(c) for (r, c) in by_case})
    for tool in tools:
        cells = [tool]
        for role in ("lead", "teammate"):
            probe_stats = probe_ok_stats(tool, role)
            cells.append(fmt(probe_stats.mean()) if probe_stats.n else "--")
            observed_ok = by_tool.get((role, tool), Stats()).of_status("ok")
            cells.append(fmt(observed_ok.mean()) if observed_ok.n else "--")
        if {c for c in cells[1:]} != {"--"}:
            out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out)


def main():
    args = parse_args()
    by_case, files, errors = load_probe(args.records)
    if not by_case:
        raise SystemExit(f"no probe records matched {args.records}")
    by_tool, runs = load_observational(args.traces)
    report = render(by_case, files, errors, by_tool, runs)
    print(report)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(report + "\n", encoding="utf-8")
    if args.json:
        payload = {
            "probe_files": files,
            "probe": {
                f"{role}/{case}": {
                    "n": s.n, "mean_ms": s.mean(), "median_ms": s.median(),
                    "p90_ms": s.pct(90), "max_ms": s.max(),
                    "status": {k: s.count(k) for k in set(s.statuses)},
                } for (role, case), s in by_case.items()},
            "observational": {
                f"{kind}/{tool}": {
                    "n": s.n, "mean_ms": s.mean(), "median_ms": s.median(),
                    "p90_ms": s.pct(90), "max_ms": s.max(),
                    "status": {k: s.count(k) for k in set(s.statuses)},
                } for (kind, tool), s in by_tool.items()},
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
