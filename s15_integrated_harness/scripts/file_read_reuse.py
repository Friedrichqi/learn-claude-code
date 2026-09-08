#!/usr/bin/env python3
"""Measure file-read repetition (reuse) in s15 harness JSONL traces.

Usage:
    python3 scripts/file_read_reuse.py <trace.jsonl | directory> [...]
        [--json out.json] [--top 12] [--exclude REGEX] [--no-aggregate]

For each run the report answers, from the recorded tool spans and model spans:

  A. read_file calls per agent kind (lead / teammate / one_shot): distinct files,
     same-agent re-reads split into *identical content* (true redundancy) and
     *other range/content* (pagination with offset/limit, or the file changed),
     and cross-agent duplicates: reads of a path another agent had already read,
     and reads whose bytes had already been fetched earlier by another agent
     (whatever path/range this agent used).
  B. cross-agent duplication detail: teammate reads of files the lead / another
     teammate had already read.  Agents never share message history, so every
     such read is structural.
  C. lead re-reads that follow a history shrink (micro / fit / summary
     compaction) -- compaction-induced re-reads.
  D. bash commands used as file readers (cat/head/tail/sed/grep ...).
  E. model input tokens per agent kind, the share that repeats the previous
     request's prefix (append-only growth => prompt-cache reuse potential), and
     provider-reported cache_read_input_tokens when returned.
  G. when <trace>.inputs.jsonl (written by scripts/profile_run.py) sits next to
     the trace: how many characters of every request were read_file results,
     how many times each read byte was re-sent to the model, and how much of the
     history had already been replaced by compaction placeholders.

Only the standard library is used.  Paths are normalised relative to the run's
recorded cwd so the same file has one key regardless of how the model spelled it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HARMLESS_REDIRECT = re.compile(r"\d?>\s*&\s*\d|\d?>\s*/dev/null|<<\s*'?\w+'?")
MUTATE_CMD = re.compile(
    r"(\brm\b|\bmv\b|\bcp\b|>(?!>)\s*\S|>>|\btee\b|\bsed\s+-i|"
    r"\bgit\s+(?:add|commit|checkout|reset|clean|stash|push|pull|rebase|merge|rm|mv)\b|"
    r"\bpip3?\s+(?:install|uninstall)\b|\bmkdir\b|\btouch\b|\bchmod\b|\bchown\b|\btruncate\b)"
)
READ_CMD = re.compile(
    r"\b(cat|head|tail|less|more|nl|wc|grep|rg|egrep|fgrep|sed|awk|cut|sort|uniq|"
    r"diff|find|ls|tree|stat|file|strings|jq|python3?)\b"
)
PATH_TOKEN = re.compile(
    r"(?<![\w-])((?:[\w.@-]+/)*[\w.@-]+\.(?:py|md|json|jsonl|txt|sh|ya?ml|toml|cfg|ini|html|svg|csv|rst|env))\b"
)
KINDS = ("lead", "teammate", "one_shot")


def is_mutating(command: str) -> bool:
    return bool(MUTATE_CMD.search(HARMLESS_REDIRECT.sub(" ", command)))


def bash_read_paths(command: str) -> list[str] | None:
    """None when the command is not read-like; else the file paths it names."""
    if not isinstance(command, str) or is_mutating(command) or not READ_CMD.search(command):
        return None
    return sorted(set(PATH_TOKEN.findall(command)))


def load_records(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def norm_path(value, cwd: str | None) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    if cwd:
        root = cwd.rstrip("/") + "/"
        if text.startswith(root):
            text = text[len(root):]
    while text.startswith("./"):
        text = text[2:]
    return text or "."


def pct(part: float, whole: float) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


class RunAnalysis:
    def __init__(self, path: Path, exclude: re.Pattern | None = None):
        self.path = path
        self.exclude = exclude
        self.records = load_records(path)
        self.cwd = None
        self.model = None
        self.label = None
        self.request_preview = ""
        self.run_status = None
        self.duration_ms = 0.0
        self.agent_kind: dict[str, str] = {"agent-root": "lead"}
        self.agent_name: dict[str, str] = {"agent-root": "lead"}
        self.tool_calls: list[dict] = []
        self.model_calls: list[dict] = []
        self.shrinks: list[float] = []
        self.summary_compactions: list[float] = []
        self.excluded_reads = 0
        self._parse()
        self._analyse()
        self._analyse_inputs()

    # ------------------------------------------------------------------ parse
    def _parse(self) -> None:
        starts: dict[str, dict] = {}
        for rec in self.records:
            event = rec.get("event")
            data = rec.get("data") or {}
            agent = rec.get("agent_id")
            if agent and rec.get("agent_kind") and agent not in self.agent_kind:
                self.agent_kind[agent] = rec["agent_kind"]
            if event == "run_start":
                self.cwd = data.get("cwd")
                self.model = data.get("model")
            elif event == "profile_meta":
                self.label = data.get("label")
            elif event == "run_end":
                self.run_status = data.get("status")
                self.duration_ms = rec.get("elapsed_ms", 0.0)
            elif event == "turn_start" and not self.request_preview:
                self.request_preview = (data.get("request") or {}).get("preview", "")
            elif event == "agent_create":
                self.agent_kind[agent] = rec.get("agent_kind") or data.get("role") or "child"
                self.agent_name[agent] = data.get("name") or data.get("role") or agent
            elif event in {"tool_start", "model_request", "context_prepare"}:
                starts[rec.get("span_id")] = rec
            elif event == "tool_end":
                start = starts.pop(rec.get("span_id"), None)
                if start is None:
                    continue
                result = data.get("result") or {}
                owner = start.get("agent_id") or agent or "agent-root"
                self.tool_calls.append({
                    "agent": owner,
                    "kind": self.agent_kind.get(owner, start.get("agent_kind") or "lead"),
                    "turn": start.get("turn_id"),
                    "t": start.get("elapsed_ms", 0.0),
                    "tool": (start.get("data") or {}).get("tool") or data.get("tool"),
                    "args": (start.get("data") or {}).get("arguments") or {},
                    "status": data.get("status"),
                    "chars": result.get("characters", 0) or 0,
                    "sha": result.get("sha256"),
                    "duration_ms": data.get("duration_ms", 0.0),
                })
            elif event in {"model_response", "model_error"}:
                start = starts.pop(rec.get("span_id"), None)
                if start is None:
                    continue
                usage = data.get("usage") or {}
                owner = start.get("agent_id") or agent or "agent-root"
                self.model_calls.append({
                    "agent": owner,
                    "kind": self.agent_kind.get(owner, start.get("agent_kind") or "lead"),
                    "purpose": (start.get("data") or {}).get("purpose"),
                    "t": start.get("elapsed_ms", 0.0),
                    "status": data.get("status"),
                    "context_chars": (start.get("data") or {}).get("context_characters"),
                    "input": usage.get("input_tokens"),
                    "prompt": (None if usage.get("input_tokens") is None
                               else usage.get("input_tokens") + (usage.get("cache_read_input_tokens") or 0)),
                    "output": usage.get("output_tokens"),
                    "cache_read": usage.get("cache_read_input_tokens"),
                    "cache_create": usage.get("cache_creation_input_tokens"),
                    "duration_ms": data.get("duration_ms", 0.0),
                    "error": data.get("error_type"),
                })
            elif event == "context_prepared":
                start = starts.pop(rec.get("span_id"), None)
                if start is None:
                    continue
                before = (start.get("data") or {}).get("characters_before", 0) or 0
                after = data.get("characters_after", before) or 0
                if after < before:
                    self.shrinks.append(rec.get("elapsed_ms", 0.0))
            elif event == "context_compact":
                self.summary_compactions.append(rec.get("elapsed_ms", 0.0))
        if not self.duration_ms and self.records:
            self.duration_ms = self.records[-1].get("elapsed_ms", 0.0)
        self.tool_calls.sort(key=lambda call: call["t"])
        self.model_calls.sort(key=lambda call: call["t"])

    # ---------------------------------------------------------------- analyse
    def _analyse(self) -> None:
        reads = [c for c in self.tool_calls if c["tool"] == "read_file"]
        self.read_attempts = len(reads)
        self.failed_reads = Counter(c["kind"] for c in reads if c["status"] != "ok")
        ok_reads = [c for c in reads if c["status"] == "ok"]

        seen_by_agent: dict[str, set] = defaultdict(set)
        seen_sha_by_agent: dict[str, set] = defaultdict(set)
        seen_by_kind: dict[str, set] = defaultdict(set)
        readers: dict[str, set] = defaultdict(set)
        last_read_t: dict[tuple, float] = {}
        global_sha: set = set()
        self.path_stats: dict[str, dict] = defaultdict(
            lambda: {"reads": 0, "agents": set(), "kinds": set(), "chars": 0, "identical": 0})
        self.reads = []
        for call in ok_reads:
            args = call["args"] if isinstance(call["args"], dict) else {}
            path = norm_path(args.get("path", "?"), self.cwd)
            if self.exclude and self.exclude.search(path):
                self.excluded_reads += 1
                continue
            agent, kind, sha = call["agent"], call["kind"], call["sha"]
            intra = path in seen_by_agent[agent]
            info = {
                "agent": agent, "kind": kind, "path": path, "chars": call["chars"], "t": call["t"],
                "offset": args.get("offset"), "limit": args.get("limit"), "sha": sha,
                "intra_repeat": intra,
                "intra_exact": (path, sha) in seen_sha_by_agent[agent],
                "cross_path": (not intra) and path in readers,
                # bytes already fetched earlier by ANY agent, excluding this agent's own exact repeat
                "cross_exact": sha in global_sha and (path, sha) not in seen_sha_by_agent[agent],
                "lead_before": path in seen_by_kind["lead"],
                "teammate_before": path in seen_by_kind["teammate"],
                "other_teammate_before": any(a != agent and self.agent_kind.get(a) == "teammate" for a in readers[path]),
                "global_first": path not in readers,
                "content_new": sha not in global_sha,
                "after_shrink": False,
            }
            if intra and kind == "lead":
                previous = last_read_t.get((agent, path), 0.0)
                info["after_shrink"] = any(previous < t < call["t"] for t in self.shrinks + self.summary_compactions)
            seen_by_agent[agent].add(path)
            seen_sha_by_agent[agent].add((path, sha))
            seen_by_kind[kind].add(path)
            readers[path].add(agent)
            last_read_t[(agent, path)] = call["t"]
            stats = self.path_stats[path]
            stats["reads"] += 1
            stats["agents"].add(agent)
            stats["kinds"].add(kind)
            stats["chars"] += call["chars"]
            if not info["content_new"]:
                stats["identical"] += 1
            global_sha.add(sha)
            self.reads.append(info)

        self.kind_summary: dict[str, dict] = {}
        for kind in KINDS:
            rows = [r for r in self.reads if r["kind"] == kind]
            agents = {r["agent"] for r in rows} | {a for a, k in self.agent_kind.items() if k == kind}
            self.kind_summary[kind] = {
                "agents": len(agents),
                "reads": len(rows),
                "failed": self.failed_reads.get(kind, 0),
                "unique_paths": len({r["path"] for r in rows}),
                "intra_repeats": sum(1 for r in rows if r["intra_repeat"]),
                "intra_exact": sum(1 for r in rows if r["intra_exact"]),
                "intra_other": sum(1 for r in rows if r["intra_repeat"] and not r["intra_exact"]),
                "cross_path": sum(1 for r in rows if r["cross_path"]),
                "cross_exact": sum(1 for r in rows if r["cross_exact"]),
                "global_first": sum(1 for r in rows if r["global_first"]),
                "chars": sum(r["chars"] for r in rows),
                "identical_chars": sum(r["chars"] for r in rows if not r["content_new"]),
                "intra_exact_chars": sum(r["chars"] for r in rows if r["intra_exact"]),
                "cross_exact_chars": sum(r["chars"] for r in rows if r["cross_exact"]),
                "spill_reads": sum(1 for r in rows if r["path"].startswith(".task_outputs/")),
                "spill_chars": sum(r["chars"] for r in rows if r["path"].startswith(".task_outputs/")),
            }

        teammate_rows = [r for r in self.reads if r["kind"] == "teammate"]
        lead_rows = [r for r in self.reads if r["kind"] == "lead"]
        self.cross = {
            "teammate_reads": len(teammate_rows),
            "teammate_lead_before": sum(1 for r in teammate_rows if r["lead_before"]),
            "teammate_other_teammate_before": sum(1 for r in teammate_rows if r["other_teammate_before"]),
            "teammate_global_first": sum(1 for r in teammate_rows if r["global_first"]),
            "teammate_identical": sum(1 for r in teammate_rows if not r["content_new"]),
            "lead_reads": len(lead_rows),
            "lead_teammate_before": sum(1 for r in lead_rows if r["teammate_before"] and not r["intra_repeat"]),
            "paths_total": len(self.path_stats),
            "paths_multi_agent": sum(1 for s in self.path_stats.values() if len(s["agents"]) > 1),
            "paths_multi_kind": sum(1 for s in self.path_stats.values() if len(s["kinds"]) > 1),
        }
        self.lead_compaction = {
            "lead_repeats": sum(1 for r in lead_rows if r["intra_repeat"]),
            "after_shrink": sum(1 for r in lead_rows if r["after_shrink"]),
            "shrink_events": len(self.shrinks),
            "summary_compactions": len(self.summary_compactions),
        }
        total = len(self.reads)
        self.global_reuse = {
            "reads": total,
            "unique_paths": len(self.path_stats),
            "unique_contents": len({r["sha"] for r in self.reads}),
            "identical": sum(1 for r in self.reads if not r["content_new"]),
            "chars": sum(r["chars"] for r in self.reads),
            "identical_chars": sum(r["chars"] for r in self.reads if not r["content_new"]),
        }

        bash = [c for c in self.tool_calls if c["tool"] == "bash"]
        self.bash = {"calls": len(bash), "ok": 0, "denied": 0, "read_like": 0, "with_paths": 0,
                     "paths": Counter(), "overlap": 0, "by_kind": Counter(c["kind"] for c in bash),
                     "exact_repeat": 0, "path_repeat": 0, "chars": 0, "read_like_by_kind": Counter()}
        read_paths = set(self.path_stats)
        seen_any: set = set()
        seen_cmd: set = set()
        for call in self.tool_calls:
            if call["tool"] == "bash" and call["status"] == "denied":
                self.bash["denied"] += 1
                continue
            if call["status"] != "ok" or call["tool"] not in {"bash", "read_file"}:
                continue
            args = call["args"] if isinstance(call["args"], dict) else {}
            if call["tool"] == "read_file":
                path = norm_path(args.get("path", "?"), self.cwd)
                if not (self.exclude and self.exclude.search(path)):
                    seen_any.add(path)
                continue
            self.bash["ok"] += 1
            command = args.get("command")
            if isinstance(command, dict):
                command = command.get("preview", "")
            paths = bash_read_paths(command or "")
            if paths is None:
                continue
            self.bash["read_like"] += 1
            self.bash["read_like_by_kind"][call["kind"]] += 1
            self.bash["chars"] += call["chars"]
            key = (call["agent"], command)
            if key in seen_cmd:
                self.bash["exact_repeat"] += 1
            seen_cmd.add(key)
            normalised = [norm_path(p, self.cwd) for p in paths]
            if normalised:
                self.bash["with_paths"] += 1
                if any(p in seen_any for p in normalised):
                    self.bash["path_repeat"] += 1
                for p in normalised:
                    self.bash["paths"][p] += 1
                    if p in read_paths:
                        self.bash["overlap"] += 1
                seen_any.update(normalised)

        globs = [c for c in self.tool_calls if c["tool"] == "glob" and c["status"] == "ok"]
        self.globs = {"calls": len(globs),
                      "unique_per_agent": len({(c["agent"], json.dumps(c["args"], sort_keys=True)) for c in globs}),
                      "unique_patterns": len({json.dumps(c["args"], sort_keys=True) for c in globs})}

        self.model_summary: dict[str, dict] = {}
        per_agent: dict[str, list] = defaultdict(list)
        for call in self.model_calls:
            per_agent[call["agent"]].append(call)
        for kind in KINDS:
            calls = [c for c in self.model_calls if c["kind"] == kind and c["status"] == "ok"]
            cache_read = [c["cache_read"] for c in calls if c["cache_read"] is not None]
            reusable = 0
            for agent, seq in per_agent.items():
                if self.agent_kind.get(agent, "lead") != kind:
                    continue
                prev = None
                for c in seq:
                    if c["status"] != "ok" or c["prompt"] is None:
                        continue
                    if prev is not None and c["prompt"] >= prev:
                        reusable += prev
                    prev = c["prompt"]
            # Agents without a compaction pipeline (teammates, one-shots) re-send every
            # earlier tool result on every later call, so the trace alone gives an exact
            # re-send count: chars x number of later model calls by the same agent.
            resend_chars = 0
            fetched_chars = 0
            if kind in {"teammate", "one_shot"}:
                for agent, seq in per_agent.items():
                    if self.agent_kind.get(agent, "lead") != kind:
                        continue
                    call_times = sorted(c["t"] for c in seq if c["status"] == "ok")
                    for r in self.reads:
                        if r["agent"] != agent:
                            continue
                        later = sum(1 for t in call_times if t > r["t"])
                        resend_chars += r["chars"] * later
                        fetched_chars += r["chars"]
            self.model_summary[kind] = {
                "resend_chars": resend_chars,
                "resend_multiplier": (resend_chars / fetched_chars) if fetched_chars else None,
                "calls": len(calls),
                "errors": sum(1 for c in self.model_calls if c["kind"] == kind and c["status"] != "ok"),
                "input": sum(c["prompt"] or 0 for c in calls),
                "uncached_input": sum(c["input"] or 0 for c in calls),
                "output": sum(c["output"] or 0 for c in calls),
                "prefix_reusable": reusable,
                "cache_read": sum(cache_read) if cache_read else None,
                "cache_reported": len(cache_read),
                "max_input": max((c["prompt"] or 0 for c in calls), default=0),
                "purposes": Counter(c["purpose"] for c in calls),
                "model_ms": sum(c["duration_ms"] or 0 for c in calls),
            }

    def _analyse_inputs(self) -> None:
        self.inputs = None
        sidecar = self.path.with_name(self.path.name.removesuffix(".jsonl") + ".inputs.jsonl")
        if not sidecar.is_file():
            return
        rows = load_records(sidecar)
        summary: dict[str, dict] = {}
        for kind in KINDS:
            calls = [r for r in rows if (r.get("agent_kind") or "lead") == kind]
            if not calls:
                continue
            req_chars = sum((r.get("system_chars") or 0) + (r.get("messages_chars") or 0) for r in calls)
            read_chars = sum((r.get("tool_result_chars") or {}).get("read_file", 0) for r in calls)
            bash_chars = sum((r.get("tool_result_chars") or {}).get("bash", 0) for r in calls)
            other_chars = sum(sum(v for k, v in (r.get("tool_result_chars") or {}).items()
                                  if k not in {"read_file", "bash"}) for r in calls)
            placeholders = sum(r.get("compacted_placeholders") or 0 for r in calls)
            reads_in_ctx = sum((r.get("tool_result_counts") or {}).get("read_file", 0) for r in calls)
            fetched = self.kind_summary[kind]["chars"]
            summary[kind] = {
                "calls": len(calls),
                "request_chars": req_chars,
                "system_chars": sum(r.get("system_chars") or 0 for r in calls),
                "read_file_chars": read_chars,
                "bash_chars": bash_chars,
                "other_tool_chars": other_chars,
                "read_results_in_context": reads_in_ctx,
                "placeholders": placeholders,
                "fetched_chars": fetched,
                "resend_multiplier": (read_chars / fetched) if fetched else None,
                "peak_request_chars": max(((r.get("system_chars") or 0) + (r.get("messages_chars") or 0)) for r in calls),
            }
        self.inputs = summary

    # ----------------------------------------------------------------- report
    def report(self, top: int = 12) -> str:
        out = []
        kinds = Counter(self.agent_kind[a] for a in self.agent_kind if a != "agent-root")
        title = f"## {self.path.name}" + (f"  ({self.label})" if self.label else "")
        out += [title, "",
                f"- model: `{self.model}`  status: {self.run_status}  wall: {self.duration_ms/1000:.0f}s  cwd: `{self.cwd}`",
                f"- request: {json.dumps(self.request_preview[:200])}",
                f"- child agents: {dict(kinds) or 'none'}; model calls: {len(self.model_calls)}; tool calls: {len(self.tool_calls)}; "
                f"read_file attempts: {self.read_attempts} (failed/denied: {sum(self.failed_reads.values())}"
                + (f", excluded by filter: {self.excluded_reads}" if self.excluded_reads else "") + ")", ""]
        out += ["### A. read_file repetition by agent kind", "",
                "| kind | agents | reads | distinct files | same-agent re-read: identical | same-agent re-read: other range/content | "
                "cross-agent: same path | cross-agent: identical content | chars fetched | identical-content chars |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for kind, s in self.kind_summary.items():
            if not s["reads"] and not s["agents"]:
                continue
            out.append(
                f"| {kind} | {s['agents']} | {s['reads']} | {s['unique_paths']} | "
                f"{s['intra_exact']} ({pct(s['intra_exact'], s['reads'])}) | {s['intra_other']} ({pct(s['intra_other'], s['reads'])}) | "
                f"{s['cross_path']} ({pct(s['cross_path'], s['reads'])}) | {s['cross_exact']} ({pct(s['cross_exact'], s['reads'])}) | "
                f"{s['chars']:,} | {s['identical_chars']:,} ({pct(s['identical_chars'], s['chars'])}) |")
        g = self.global_reuse
        out += ["", f"Run-wide: {g['reads']} successful reads over {g['unique_paths']} distinct files and {g['unique_contents']} distinct "
                f"contents; {g['identical']} reads ({pct(g['identical'], g['reads'])}) returned bytes already returned earlier in the run "
                f"= {g['identical_chars']:,} of {g['chars']:,} chars ({pct(g['identical_chars'], g['chars'])}).", ""]
        c = self.cross
        out += ["### B. cross-agent duplication (agents never share history)", "",
                f"- teammate reads: {c['teammate_reads']}; file already read by the lead earlier: {c['teammate_lead_before']} "
                f"({pct(c['teammate_lead_before'], c['teammate_reads'])}); already read by another teammate: "
                f"{c['teammate_other_teammate_before']} ({pct(c['teammate_other_teammate_before'], c['teammate_reads'])}); "
                f"first read of that file anywhere in the run: {c['teammate_global_first']} ({pct(c['teammate_global_first'], c['teammate_reads'])}); "
                f"byte-identical to an earlier read: {c['teammate_identical']} ({pct(c['teammate_identical'], c['teammate_reads'])})",
                f"- lead reads: {c['lead_reads']}; lead first-reads of a file a teammate had already read: {c['lead_teammate_before']}",
                f"- distinct files: {c['paths_total']}; read by >1 agent: {c['paths_multi_agent']} ({pct(c['paths_multi_agent'], c['paths_total'])}); "
                f"read by >1 agent kind: {c['paths_multi_kind']}", ""]
        lc = self.lead_compaction
        spill = {k: (v["spill_reads"], v["spill_chars"]) for k, v in self.kind_summary.items() if v["spill_reads"]}
        out += ["### C. lead re-reads vs context compaction", "",
                f"- lead same-agent re-reads: {lc['lead_repeats']}; after a history shrink between the two reads: {lc['after_shrink']} "
                f"({pct(lc['after_shrink'], lc['lead_repeats'])}); shrink events: {lc['shrink_events']}; summary compactions: {lc['summary_compactions']}",
                f"- reads of the harness's own spilled tool outputs (`.task_outputs/tool-results/*.txt`, i.e. content already fetched once): "
                f"{ {k: v[0] for k, v in spill.items()} or 0} reads, { {k: v[1] for k, v in spill.items()} or 0} chars", ""]
        b = self.bash
        out += ["### D. bash used as a file reader", "",
                f"- bash calls: {b['calls']} by kind {dict(b['by_kind'])} (ok {b['ok']}, denied {b['denied']}); read-like: {b['read_like']} "
                f"({pct(b['read_like'], b['ok'])} of ok) by kind {dict(b['read_like_by_kind'])}; chars returned by read-like bash: {b['chars']:,}",
                f"- read-like bash: exact-duplicate command by the same agent: {b['exact_repeat']} ({pct(b['exact_repeat'], b['read_like'])}); "
                f"re-targeting a file already read by any tool earlier: {b['path_repeat']} ({pct(b['path_repeat'], b['read_like'])}); "
                f"commands naming files: {b['with_paths']}; distinct files named: {len(b['paths'])}; "
                f"bash-read path occurrences also read via read_file: {b['overlap']}",
                f"- glob calls: {self.globs['calls']}; distinct patterns: {self.globs['unique_patterns']}; distinct (agent, pattern): {self.globs['unique_per_agent']}", ""]
        out += ["### E. model input repetition (prompt-prefix reuse); prompt tokens = provider input_tokens + cache_read_input_tokens", "",
                "| kind | calls (errors) | prompt tokens (uncached + cached) | output tokens | max prompt/call | prefix-repeat est. | provider cache_read (hit rate) | model time |",
                "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for kind, m in self.model_summary.items():
            if not m["calls"] and not m["errors"]:
                continue
            cache = "not reported" if m["cache_read"] is None else f"{m['cache_read']:,} ({pct(m['cache_read'], m['input'])})"
            out.append(f"| {kind} | {m['calls']} ({m['errors']}) | {m['input']:,} | {m['output']:,} | {m['max_input']:,} | "
                       f"{m['prefix_reusable']:,} ({pct(m['prefix_reusable'], m['input'])}) | {cache} | {m['model_ms']/1000:.0f}s |")
        purposes = Counter()
        for m in self.model_summary.values():
            purposes.update(m["purposes"])
        out += ["", f"model-call purposes: {dict(purposes)}"]
        for kind, m in self.model_summary.items():
            if m.get("resend_multiplier") is not None:
                out.append(f"- {kind} agents have no compaction: every read_file result is re-sent on each later call; "
                           f"{m['resend_chars']:,} chars of read_file content re-sent = {m['resend_multiplier']:.1f}x the bytes fetched "
                           f"(read_file content only; bash/glob results and messages add to this)")
        out.append("")
        out += [f"### F. most re-read files (top {top})", "", "| file | reads | agents | kinds | identical-content reads | chars returned |", "|---|---:|---:|---|---:|---:|"]
        for path, s in sorted(self.path_stats.items(), key=lambda kv: (-kv[1]["reads"], -kv[1]["chars"]))[:top]:
            out.append(f"| `{path}` | {s['reads']} | {len(s['agents'])} | {','.join(sorted(s['kinds']))} | {s['identical']} | {s['chars']:,} |")
        out.append("")
        if self.inputs:
            out += ["### G. what the model actually received (from the .inputs.jsonl sidecar)", "",
                    "| kind | calls | request chars (sum) | peak request | read_file chars in requests | share of requests | "
                    "bytes fetched by read_file | re-send multiplier | read results in context (sum) | compaction placeholders seen |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
            for kind, s in self.inputs.items():
                mult = "n/a" if s["resend_multiplier"] is None else f"{s['resend_multiplier']:.1f}x"
                out.append(f"| {kind} | {s['calls']} | {s['request_chars']:,} | {s['peak_request_chars']:,} | {s['read_file_chars']:,} | "
                           f"{pct(s['read_file_chars'], s['request_chars'])} | {s['fetched_chars']:,} | {mult} | {s['read_results_in_context']} | {s['placeholders']} |")
            out.append("")
        return "\n".join(out)

    def calls_table(self) -> str:
        sidecar = self.path.with_name(self.path.name.removesuffix(".jsonl") + ".inputs.jsonl")
        if not sidecar.is_file():
            return ""
        out = [f"### per-call request composition ({sidecar.name})", "",
               "| # | kind | purpose | msgs | request chars | read_file chars | bash chars | other tool chars | placeholders | input tok | cache_read | output tok | s |",
               "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for i, r in enumerate(load_records(sidecar), 1):
            u = r.get("usage") or {}
            trc = r.get("tool_result_chars") or {}
            other = sum(v for k, v in trc.items() if k not in {"read_file", "bash"})
            out.append(f"| {i} | {r.get('agent_kind')} | {r.get('purpose')} | {r.get('message_count')} | "
                       f"{(r.get('system_chars') or 0) + (r.get('messages_chars') or 0):,} | {trc.get('read_file', 0):,} | {trc.get('bash', 0):,} | {other:,} | "
                       f"{r.get('compacted_placeholders', 0)} | {u.get('input_tokens') if u else r.get('status')} | {u.get('cache_read_input_tokens') if u else ''} | "
                       f"{u.get('output_tokens') if u else ''} | {(r.get('duration_ms') or 0)/1000:.1f} |")
        out.append("")
        return "\n".join(out)

    def to_json(self) -> dict:
        def plain(value):
            if isinstance(value, Counter):
                return dict(value)
            if isinstance(value, dict):
                return {k: plain(v) for k, v in value.items()}
            if isinstance(value, set):
                return sorted(value)
            return value
        return {
            "file": str(self.path), "label": self.label, "model": self.model, "status": self.run_status,
            "duration_ms": self.duration_ms, "request_preview": self.request_preview,
            "kind_summary": plain(self.kind_summary), "cross": self.cross, "lead_compaction": self.lead_compaction,
            "global_reuse": self.global_reuse, "bash": plain(self.bash), "globs": self.globs,
            "model_summary": plain(self.model_summary), "inputs": self.inputs,
            "paths": {p: plain(s) for p, s in self.path_stats.items()}, "reads": self.reads,
        }


def aggregate(runs: list[RunAnalysis]) -> str:
    out = ["## Aggregate over all runs listed above", ""]
    out += ["| kind | reads | same-agent identical re-reads | same-agent other-range re-reads | cross-agent same path | cross-agent identical | chars fetched | identical-content chars |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for kind in KINDS:
        ks = [r.kind_summary[kind] for r in runs]
        reads = sum(s["reads"] for s in ks)
        if not reads:
            continue
        ie, io_ = sum(s["intra_exact"] for s in ks), sum(s["intra_other"] for s in ks)
        cp, ce = sum(s["cross_path"] for s in ks), sum(s["cross_exact"] for s in ks)
        chars, ident = sum(s["chars"] for s in ks), sum(s["identical_chars"] for s in ks)
        out.append(f"| {kind} | {reads} | {ie} ({pct(ie, reads)}) | {io_} ({pct(io_, reads)}) | {cp} ({pct(cp, reads)}) | {ce} ({pct(ce, reads)}) | "
                   f"{chars:,} | {ident:,} ({pct(ident, chars)}) |")
    t_reads = sum(r.cross["teammate_reads"] for r in runs)
    t_lead = sum(r.cross["teammate_lead_before"] for r in runs)
    t_other = sum(r.cross["teammate_other_teammate_before"] for r in runs)
    rep = sum(r.lead_compaction["lead_repeats"] for r in runs)
    shr = sum(r.lead_compaction["after_shrink"] for r in runs)
    out += ["", f"teammate reads of a file the lead had already read: {t_lead}/{t_reads} ({pct(t_lead, t_reads)}); "
            f"of a file another teammate had already read: {t_other}/{t_reads} ({pct(t_other, t_reads)})",
            f"lead re-reads that followed a compaction shrink: {shr}/{rep} ({pct(shr, rep)})", "",
            "| kind | model calls | prompt tokens (uncached + cached) | prefix-repeat est. | provider cache_read (hit rate) |", "|---|---:|---:|---:|---:|"]
    for kind in KINDS:
        calls = sum(r.model_summary[kind]["calls"] for r in runs)
        if not calls:
            continue
        inp = sum(r.model_summary[kind]["input"] for r in runs)
        reuse = sum(r.model_summary[kind]["prefix_reusable"] for r in runs)
        cache_vals = [r.model_summary[kind]["cache_read"] for r in runs if r.model_summary[kind]["cache_read"] is not None]
        cache = "not reported" if not cache_vals else f"{sum(cache_vals):,} ({pct(sum(cache_vals), inp)})"
        out.append(f"| {kind} | {calls} | {inp:,} | {reuse:,} ({pct(reuse, inp)}) | {cache} |")
    with_inputs = [r for r in runs if r.inputs]
    if with_inputs:
        out += ["", "| kind | read_file chars in requests | request chars | share | bytes fetched | re-send multiplier |", "|---|---:|---:|---:|---:|---:|"]
        for kind in KINDS:
            rows = [r.inputs[kind] for r in with_inputs if kind in r.inputs]
            if not rows:
                continue
            rc = sum(s["read_file_chars"] for s in rows)
            req = sum(s["request_chars"] for s in rows)
            fetched = sum(s["fetched_chars"] for s in rows)
            out.append(f"| {kind} | {rc:,} | {req:,} | {pct(rc, req)} | {fetched:,} | {(rc / fetched) if fetched else 0:.1f}x |")
    out.append("")
    return "\n".join(out)


def collect_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.jsonl") if not p.name.endswith(".inputs.jsonl")))
        elif path.is_file():
            files.append(path)
        else:
            print(f"warning: {target} not found", file=sys.stderr)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", help="trace files or directories")
    parser.add_argument("--json", help="write per-run JSON here")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--exclude", help="regex of normalised paths to ignore (e.g. the run's own trace files)")
    parser.add_argument("--no-aggregate", action="store_true")
    parser.add_argument("--calls", action="store_true", help="also print the per-call request composition from the .inputs.jsonl sidecar")
    args = parser.parse_args(argv)
    files = collect_files(args.targets)
    if not files:
        print("no trace files", file=sys.stderr)
        return 2
    exclude = re.compile(args.exclude) if args.exclude else None
    runs = []
    for file in files:
        run = RunAnalysis(file, exclude)
        if not run.records:
            continue
        runs.append(run)
        print(run.report(args.top))
        if args.calls:
            print(run.calls_table())
    if len(runs) > 1 and not args.no_aggregate:
        print(aggregate(runs))
    if args.json:
        Path(args.json).write_text(json.dumps([r.to_json() for r in runs], indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
