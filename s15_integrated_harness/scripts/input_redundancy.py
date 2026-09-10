#!/usr/bin/env python3
"""Byte-level redundancy of file input across agents in s15 harness runs.

    python3 s15_integrated_harness/scripts/input_redundancy.py <trace.jsonl | directory> [...]
        [--git-rev REV] [--json out.json] [--top-files 10] [--exclude REGEX] [--no-bash]

Question answered: of all the bytes of file content that teammates (and the lead) pulled into
their model inputs, how many had already been pulled in by another agent -- measured on the bytes
themselves rather than on whole-result hashes, at several granularities:

  whole    one unit per tool result (SHA-256 of the returned text) -- the strict baseline;
           a read of lines 1-200 and a read of lines 1-201 share nothing under this measure
  range    exact provenance: one unit per (file, line number); a byte is redundant when the same
           line of the same file was fetched before (pagination windows overlap correctly)
  line     path-agnostic: one unit per distinct line text (lines with fewer than 8 non-blank
           characters are never deduplicated); catches the same text in other files (copied code)
  cdc256   content-defined chunks (gear rolling hash, mean 256 B, 64..2048 B), path-agnostic and
           alignment-insensitive -- roughly a 64-token block in a content-addressed KV cache
  cdc1k    the same with mean 1 KiB chunks (coarser; needs longer identical stretches)

Where the bytes come from, in priority order:
  1. <trace>.reads.jsonl written by scripts/profile_run.py: the exact tool_result text the first
     time it appeared in an agent's request (what the model saw, per agent);
  2. the trace itself when HARNESS_TRACE_OUTPUT=full stored tool results verbatim;
  3. reconstruction of read_file results from the git blob of the run's commit (--git-rev,
     default: profile_meta.git_head, else HEAD); every reconstruction is verified against the
     SHA-256 the trace recorded, and unverifiable reads are reported and dropped.
Lead results over PERSIST_THRESHOLD (30,000 chars) are replaced by the harness with a 2,000-char
preview before they reach the model; that is emulated for reconstructed lead reads.

Shell reads (cat/head/tail/sed -n/grep ... on one file) count as file input too, with exact
line provenance where the command form is recognised; --no-bash restricts the analysis to
read_file.

Classification is chronological over the whole run.  Every unit of every item is
  new    : never fetched before by anyone
  intra  : fetched before by the same agent (self re-read, re-paging)
  cross  : not fetched before by this agent, but by another agent (cross-agent redundancy)
Rates are byte-weighted.  The teammate-only view repeats the classification with the lead's
reads removed from the state, so "cross" there means "fetched earlier by another teammate".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

PERSIST_THRESHOLD = 30000
PERSIST_PREVIEW = 2000
MIN_LINE_CHARS = 8
GRANULARITIES = ["whole", "range", "line", "cdc256", "cdc1k"]

_rng = random.Random(0x5EED)
GEAR = [_rng.getrandbits(32) for _ in range(256)]


# --------------------------------------------------------------------------- helpers
def pct(part: float, whole: float) -> str:
    return "-" if not whole else f"{100.0 * part / whole:.1f}%"


def fmt(n: float) -> str:
    return f"{int(round(n)):,}"


def load_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def norm_path(value: str | None, cwd: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if cwd and text.startswith(cwd.rstrip("/") + "/"):
        text = text[len(cwd.rstrip("/")) + 1:]
    if text.startswith("./"):
        text = text[2:]
    return text or None


def cdc_chunks(data: bytes, mean: int, min_size: int, max_size: int):
    """Gear-hash content-defined chunking; boundaries depend on content only."""
    mask = mean - 1  # mean must be a power of two
    n = len(data)
    start = 0
    h = 0
    gear = GEAR
    i = 0
    while i < n:
        h = ((h << 1) + gear[data[i]]) & 0xFFFFFFFF
        i += 1
        length = i - start
        if (length >= min_size and (h & mask) == 0) or length >= max_size:
            yield data[start:i]
            start = i
            h = 0
    if start < n:
        yield data[start:n]


def line_units(content: str, path: str | None, first_line: int | None):
    """(range_key, line_key, nbytes) per line; range_key None when provenance is unknown."""
    units = []
    for index, line in enumerate(content.split("\n")):
        nbytes = len(line.encode("utf-8", "surrogatepass")) + 1
        stripped = "".join(line.split())
        range_key = (path, first_line + index) if path is not None and first_line is not None else None
        if len(stripped) >= MIN_LINE_CHARS:
            line_key = hashlib.blake2b(line.encode("utf-8", "surrogatepass"), digest_size=8).digest()
        else:
            line_key = None  # too short to count as a duplicate
        units.append((range_key, line_key, nbytes))
    return units


READER_CMDS = {"cat", "head", "tail", "sed", "grep", "rg", "awk", "more", "less", "nl", "tac", "cut", "strings", "od", "xxd"}
PAGINATION_TRAILER = re.compile(r"^\.\.\. \(\d+ more lines\)$")


def classify_bash(command: str) -> tuple[bool, list[str]]:
    """Return (is_file_reader, paths) for the simple single-command forms we understand."""
    if not isinstance(command, str):
        return False, []
    plain = re.sub(r"'[^']*'", "''", command)
    if re.search(r"[|;&]|>|\$\(|`", plain):
        # pipelines and compounds: reader if any segment is a reader; provenance unknown
        segments = re.split(r"\|\||&&|[;&|\n]", command)
        readers = [seg for seg in segments if seg.split() and Path(seg.split()[0]).name in READER_CMDS]
        return bool(readers), []
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    if not words:
        return False, []
    name = Path(words[0]).name
    if name not in READER_CMDS:
        return False, []
    paths = [w for w in words[1:] if not w.startswith("-") and "/" in w or (not w.startswith("-") and "." in w)]
    return True, paths


def bash_line_provenance(command: str, output: str, file_lines: list[str] | None) -> list[int | None]:
    """Best-effort line numbers (0-based) for each output line of a shell reader; None = unknown."""
    lines = output.split("\n")
    if file_lines is None:
        return [None] * len(lines)
    try:
        words = shlex.split(command)
    except ValueError:
        return [None] * len(lines)
    name = Path(words[0]).name if words else ""
    if name == "cat" and not any(w.startswith("-") for w in words[1:]):
        return list(range(len(lines))) if len(lines) <= len(file_lines) else [None] * len(lines)
    if name == "head":
        return list(range(len(lines)))
    if name == "tail":
        start = len(file_lines) - len(lines)
        return list(range(start, start + len(lines))) if start >= 0 else [None] * len(lines)
    if name == "sed":
        match = re.search(r"(\d+)(?:,(\d+))?p", command)
        if match and "-n" in words:
            start = int(match.group(1)) - 1
            return list(range(start, start + len(lines)))
    if name in {"grep", "rg"} and ("-n" in words or "--line-number" in words):
        numbers = []
        for line in lines:
            m = re.match(r"^(\d+)[:-]", line)
            numbers.append(int(m.group(1)) - 1 if m else None)
        return numbers
    if name == "cat" and "-n" in words:
        numbers = []
        for line in lines:
            m = re.match(r"^\s*(\d+)\t", line)
            numbers.append(int(m.group(1)) - 1 if m else None)
        return numbers
    # fall back: exact unique text match
    index: dict[str, list[int]] = defaultdict(list)
    for number, text in enumerate(file_lines):
        index[text].append(number)
    result = []
    for line in lines:
        hits = index.get(line)
        result.append(hits[0] if hits and len(hits) == 1 else None)
    return result


# --------------------------------------------------------------------------- git access
class BlobStore:
    """Git blobs at one or more candidate revisions (first verified match wins per run)."""

    def __init__(self, repo: Path, revs: list[str]):
        self.repo = repo
        self.revs = [r for r in revs if r]
        self.rev = self.revs[0] if self.revs else None
        self.cache: dict[tuple[str, str], str | None] = {}

    def text_at(self, rev: str, path: str) -> str | None:
        key = (rev, path)
        if key not in self.cache:
            result = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, cwd=self.repo)
            self.cache[key] = result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else None
        return self.cache[key]

    def text(self, path: str) -> str | None:
        return self.text_at(self.rev, path) if self.rev else None


def emulate_read(text: str, limit, offset) -> str:
    lines = text.splitlines()
    offset = max(int(offset or 0), 0)
    lines = lines[offset:]
    if limit is not None:
        limit = int(limit)
        if limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines)


def emulate_persisted(output: str, path_hint: str) -> str:
    return f"<persisted-output>\nFull output: {path_hint}\nPreview:\n{output[:PERSIST_PREVIEW]}\n</persisted-output>"


# --------------------------------------------------------------------------- one run
class Run:
    def __init__(self, trace_path: Path, repo: Path, git_rev: str | None, exclude: re.Pattern | None,
                 include_bash: bool, top_files: int, max_pairs: int = 15):
        self.path = trace_path
        self.max_pairs = max_pairs
        self.repo = repo
        self.exclude = exclude
        self.include_bash = include_bash
        self.top_files = top_files
        self.records = load_records(trace_path)
        self.label = None
        self.cwd = None
        self.model = None
        self.status = None
        self.wall_ms = 0.0
        self.git_head = None
        self.output_mode = None
        self.agent_kind: dict[str, str] = {"agent-root": "lead"}
        self.agent_name: dict[str, str] = {"agent-root": "lead"}
        self.tool_calls: list[dict] = []
        self.model_calls: list[dict] = []
        self.items: list[dict] = []
        self.dropped = Counter()
        self.sources = Counter()
        self.notes: list[str] = []
        self._parse_trace()
        candidates = [r.strip() for r in (git_rev or "").split(",") if r.strip()]
        self.blobs = BlobStore(repo, candidates or [self.git_head or "HEAD"])
        self._pin_revision()
        self.sidecar = self._load_sidecar()
        self.inputs = self._load_inputs()
        self._build_items()
        self._classify()
        self._workload()
        self._resend()
        self.display = self._display_names()

    def _display_names(self) -> dict[str, str]:
        counts = Counter(self.agent_name.get(a, a) for a in self.agent_kind)
        seen = Counter()
        names = {}
        for agent in sorted(self.agent_kind, key=lambda a: (a != "agent-root", a)):
            base = self.agent_name.get(agent, agent)
            if counts[base] > 1:
                seen[base] += 1
                names[agent] = f"{base}#{seen[base]}"
            else:
                names[agent] = base
        return names

    def dn(self, agent: str) -> str:
        return self.display.get(agent, self.agent_name.get(agent, agent))

    def _resend(self):
        """How often each item's bytes were re-sent (calls of the same agent at or after the item),
        and hence what share of the kind's prompt tokens the cross-redundant file bytes occupy."""
        calls_by_agent: dict[str, list[float]] = defaultdict(list)
        for call in self.model_calls:
            if call["status"] == "ok":
                calls_by_agent[call["agent"]].append(call["t"])
        self.resend = {}
        for kind in self.kinds():
            w = self.work.get(kind, Counter())
            chars_per_token = (w["request_chars"] / w["prompt"]) if w.get("request_chars") and w.get("prompt") else 3.94
            cross_resident = 0.0
            total_resident = 0.0
            cross_once = 0.0
            fetched = 0.0
            for item, units in zip(self.items, self.item_units):
                if item["kind"] != kind:
                    continue
                later = sum(1 for t in calls_by_agent.get(item["agent"], []) if t >= item["t"])
                cls = self._item_class(item, units, "range")
                cross_resident += cls["cross"] * later
                total_resident += item["bytes"] * later
                cross_once += cls["cross"]
                fetched += item["bytes"]
            self.resend[kind] = {
                "chars_per_token": chars_per_token,
                "file_prompt_tokens": total_resident / chars_per_token,
                "cross_prompt_tokens": cross_resident / chars_per_token,
                "cross_once_tokens": cross_once / chars_per_token,
                "resend_x": (total_resident / fetched) if fetched else 0.0,
            }

    def _item_class(self, item: dict, units: dict, gran: str) -> Counter:
        return self.item_classes[gran][id(item)]

    # ---- parsing
    def _parse_trace(self):
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
                self.output_mode = data.get("output_mode")
            elif event == "profile_meta":
                self.label = data.get("label")
                self.git_head = data.get("git_head")
            elif event == "run_end":
                self.status = data.get("status")
                self.wall_ms = rec.get("elapsed_ms", 0.0)
            elif event == "agent_create":
                self.agent_kind[agent] = rec.get("agent_kind") or "child"
                self.agent_name[agent] = data.get("name") or agent
            elif event == "agent_start" and agent not in self.agent_name and data.get("name"):
                self.agent_name[agent] = data["name"]
            elif event in {"tool_start", "model_request"}:
                starts[rec.get("span_id")] = rec
            elif event == "tool_end":
                start = starts.pop(rec.get("span_id"), None)
                if start is None:
                    continue
                result = data.get("result") or {}
                owner = start.get("agent_id") or agent or "agent-root"
                full = result.get("full")
                self.tool_calls.append({
                    "agent": owner,
                    "kind": self.agent_kind.get(owner, start.get("agent_kind") or "lead"),
                    "t": start.get("elapsed_ms", 0.0),
                    "id": data.get("tool_call_id") or (start.get("data") or {}).get("tool_call_id"),
                    "tool": (start.get("data") or {}).get("tool") or data.get("tool"),
                    "args": (start.get("data") or {}).get("arguments") or {},
                    "status": data.get("status"),
                    "chars": result.get("characters", 0) or 0,
                    "sha": result.get("sha256"),
                    "full": full if isinstance(full, str) else None,
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
                    "input": usage.get("input_tokens"),
                    "cache_read": usage.get("cache_read_input_tokens") or 0,
                    "output": usage.get("output_tokens"),
                    "duration_ms": data.get("duration_ms", 0.0) or 0.0,
                })
        if not self.wall_ms and self.records:
            self.wall_ms = self.records[-1].get("elapsed_ms", 0.0)
        self.tool_calls.sort(key=lambda c: c["t"])
        self.model_calls.sort(key=lambda c: c["t"])

    def _pin_revision(self):
        """Pick the candidate revision that reproduces the most recorded read_file SHA-256s."""
        if len(self.blobs.revs) <= 1:
            return
        best = None
        for rev in self.blobs.revs:
            ok = 0
            for call in self.tool_calls:
                if call["tool"] != "read_file" or call["status"] != "ok" or not call["sha"]:
                    continue
                path = norm_path((call["args"] or {}).get("path"), self.cwd)
                if not path:
                    continue
                for candidate in self._git_candidates(path):
                    blob = self.blobs.text_at(rev, candidate)
                    if blob is None:
                        continue
                    text = emulate_read(blob, call["args"].get("limit"), call["args"].get("offset"))
                    if hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest() == call["sha"]:
                        ok += 1
                        break
            if best is None or ok > best[0]:
                best = (ok, rev)
        if best:
            self.blobs.rev = best[1]

    def _load_sidecar(self) -> dict[tuple[str, str], dict]:
        path = Path(str(self.path).removesuffix(".jsonl") + ".reads.jsonl")
        if not path.exists():
            return {}
        out = {}
        for rec in load_records(path):
            if rec.get("event") != "result":
                continue
            out[(rec.get("agent_id"), rec.get("tool_use_id"))] = rec
        return out

    def _load_inputs(self) -> list[dict]:
        path = Path(str(self.path).removesuffix(".jsonl") + ".inputs.jsonl")
        return load_records(path) if path.exists() else []

    # ---- items: every piece of file content that entered a model input
    def _build_items(self):
        for call in self.tool_calls:
            tool = call["tool"]
            if tool not in {"read_file", "bash"}:
                continue
            if tool == "bash" and not self.include_bash:
                continue
            if call["status"] != "ok":
                continue
            args = call["args"] or {}
            command = args.get("command") if tool == "bash" else None
            if tool == "bash":
                is_reader, paths = classify_bash(command or "")
                if not is_reader:
                    continue
                paths = [p for p in paths if self._file_lines(norm_path(p, self.cwd)) is not None]
            path = norm_path(args.get("path"), self.cwd) if tool == "read_file" else None
            if self.exclude and path and self.exclude.search(path):
                self.dropped["excluded"] += 1
                continue
            content, source = self._content_for(call, path)
            if content is None:
                self.dropped[f"{tool}:no-content"] += 1
                continue
            if content.startswith("Error:") or content.startswith("Permission denied"):
                continue
            self.sources[source] += 1
            item = {"agent": call["agent"], "kind": call["kind"], "name": self.agent_name.get(call["agent"], call["agent"]),
                    "t": call["t"], "tool": tool, "path": path, "command": command, "content": content,
                    "bytes": len(content.encode("utf-8", "surrogatepass")), "source": source}
            if tool == "read_file":
                item["offset"] = int(args.get("offset") or 0)
                item["limit"] = args.get("limit")
            else:
                if len(paths) == 1:
                    item["path"] = norm_path(paths[0], self.cwd)
                item["bash_paths"] = [norm_path(p, self.cwd) for p in paths]
            self.items.append(item)
        self.items.sort(key=lambda i: i["t"])

    def _content_for(self, call: dict, path: str | None) -> tuple[str | None, str]:
        side = self.sidecar.get((call["agent"], call["id"]))
        if side is not None:
            return side.get("content"), "sidecar"
        if self.sidecar and call["kind"] != "lead":
            # a sidecar exists but this result never reached a model call (e.g. shutdown)
            return None, "never-sent"
        if call["full"] is not None:
            text = call["full"]
            if call["kind"] == "lead" and len(text) > PERSIST_THRESHOLD:
                text = emulate_persisted(text, path or "?")
            return text, "trace-full"
        if call["tool"] == "read_file" and path:
            for candidate in self._git_candidates(path):
                blob = self.blobs.text(candidate)
                if blob is None:
                    continue
                text = emulate_read(blob, call["args"].get("limit"), call["args"].get("offset"))
                if call["sha"] and hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest() != call["sha"]:
                    continue
                if call["kind"] == "lead" and len(text) > PERSIST_THRESHOLD:
                    text = emulate_persisted(text, path)
                return text, "reconstructed"
            return None, "unverifiable"
        return None, "no-source"

    def _git_candidates(self, path: str) -> list[str]:
        candidates = [path]
        if self.cwd:
            base = Path(self.cwd).name
            if base and not path.startswith(base + "/"):
                candidates.append(f"{base}/{path}")
        return candidates

    def _file_lines(self, path: str | None) -> list[str] | None:
        if not path:
            return None
        for candidate in self._git_candidates(path):
            blob = self.blobs.text(candidate)
            if blob is not None:
                return blob.splitlines()
        return None

    # ---- units per granularity
    def _units(self, item: dict) -> dict[str, list[tuple]]:
        content = item["content"]
        data = content.encode("utf-8", "surrogatepass")
        units: dict[str, list[tuple]] = {}
        units["whole"] = [(hashlib.sha256(data).digest(), len(data))]
        body = content
        first_line = None
        if item["tool"] == "read_file":
            lines = content.split("\n")
            if lines and PAGINATION_TRAILER.match(lines[-1]):
                lines = lines[:-1]
            body = "\n".join(lines)
            first_line = item["offset"]
            if content.startswith("<persisted-output>"):
                first_line = None
            line_items = line_units(body, item["path"], first_line)
        else:
            numbers = bash_line_provenance(item["command"] or "", content, self._file_lines(item.get("path")))
            line_items = []
            for (_, line_key, nbytes), number in zip(line_units(content, None, None), numbers):
                range_key = (item.get("path"), number) if number is not None and item.get("path") else None
                line_items.append((range_key, line_key, nbytes))
        range_units, text_units = [], []
        for range_key, line_key, nbytes in line_items:
            range_units.append((("R",) + range_key if range_key else None, nbytes))
            text_units.append((("L", line_key) if line_key else None, nbytes))
        units["range"] = range_units
        units["line"] = text_units
        units["cdc256"] = [(("C256", hashlib.blake2b(chunk, digest_size=8).digest()), len(chunk))
                           for chunk in cdc_chunks(data, 256, 64, 2048)]
        units["cdc1k"] = [(("C1K", hashlib.blake2b(chunk, digest_size=8).digest()), len(chunk))
                          for chunk in cdc_chunks(data, 1024, 256, 8192)]
        return units

    # ---- chronological classification
    def _classify(self):
        self.stats = {}          # view -> granularity -> kind -> Counter(total,new,intra,cross,cross_lead,cross_teammate,unmatched)
        self.agent_stats = {}    # view -> granularity -> agent -> Counter
        self.agent_sets = {}     # granularity -> agent -> {key: bytes}
        self.item_units = [self._units(item) for item in self.items]
        self.item_classes = {g: {} for g in GRANULARITIES}
        self.gaps: dict[str, dict[str, list[tuple[float, int]]]] = {}
        for gran in GRANULARITIES:
            self.agent_sets[gran] = defaultdict(dict)
        for view in ("all", "teammates"):
            self.stats[view] = {g: defaultdict(Counter) for g in GRANULARITIES}
            self.agent_stats[view] = {g: defaultdict(Counter) for g in GRANULARITIES}
            for gran in GRANULARITIES:
                seen_by: dict[tuple, set] = defaultdict(set)
                first_kind: dict[tuple, str] = {}
                first_t: dict[tuple, float] = {}
                gaps = self.gaps.setdefault(view, {}).setdefault(gran, [])
                for item, units in zip(self.items, self.item_units):
                    if view == "teammates" and item["kind"] != "teammate":
                        continue
                    agent = item["agent"]
                    kind_counter = self.stats[view][gran][item["kind"]]
                    agent_counter = self.agent_stats[view][gran][agent]
                    item_counter = Counter()
                    if view == "all":
                        self.item_classes[gran][id(item)] = item_counter
                    for key, nbytes in units[gran]:
                        kind_counter["total"] += nbytes
                        agent_counter["total"] += nbytes
                        item_counter["total"] += nbytes
                        if key is None:
                            kind_counter["new"] += nbytes
                            kind_counter["unmatched"] += nbytes
                            agent_counter["new"] += nbytes
                            item_counter["new"] += nbytes
                            continue
                        holders = seen_by.get(key)
                        if not holders:
                            cls = "new"
                            first_kind[key] = item["kind"]
                            first_t[key] = item["t"]
                        elif agent in holders:
                            cls = "intra"
                        else:
                            cls = "cross"
                            gaps.append((item["t"] - first_t[key], nbytes))
                            src = "cross_lead" if first_kind.get(key) == "lead" and all(
                                self.agent_kind.get(h) == "lead" for h in holders) else "cross_teammate"
                            kind_counter[src] += nbytes
                            agent_counter[src] += nbytes
                        kind_counter[cls] += nbytes
                        agent_counter[cls] += nbytes
                        item_counter[cls] += nbytes
                        seen_by[key].add(agent)
                        if view == "all":
                            self.agent_sets[gran][agent][key] = nbytes

    # ---- workload characterisation
    def _workload(self):
        self.work = defaultdict(Counter)
        for call in self.model_calls:
            if call["status"] != "ok":
                self.work[call["kind"]]["errors"] += 1
                continue
            w = self.work[call["kind"]]
            w["calls"] += 1
            w["prompt"] += (call["input"] or 0) + (call["cache_read"] or 0)
            w["uncached"] += call["input"] or 0
            w["cached"] += call["cache_read"] or 0
            w["output"] += call["output"] or 0
            w["ms"] += call["duration_ms"]
        for rec in self.inputs:
            if rec.get("status") != "ok":
                continue
            w = self.work[rec.get("agent_kind") or "lead"]
            w["text_chars"] += rec.get("output_text_chars", 0) or 0
            w["think_chars"] += rec.get("output_thinking_chars", 0) or 0
            w["tool_input_chars"] += rec.get("output_tool_input_chars", 0) or 0
            w["request_chars"] += rec.get("messages_chars", 0) or 0
            w["read_chars"] += (rec.get("tool_result_chars") or {}).get("read_file", 0)
            w["bash_chars"] += (rec.get("tool_result_chars") or {}).get("bash", 0)

    # ---- reporting
    def kinds(self) -> list[str]:
        order = ["lead", "teammate", "one_shot"]
        present = {i["kind"] for i in self.items} | set(self.work)
        return [k for k in order if k in present] + sorted(present - set(order))

    def summary_row(self, view: str, gran: str, kind: str) -> Counter:
        return self.stats[view][gran][kind]

    def teammates(self) -> list[str]:
        seen = []
        for item in self.items:
            if item["kind"] == "teammate" and item["agent"] not in seen:
                seen.append(item["agent"])
        return seen

    def pairwise(self, gran: str) -> list[tuple[str, str, int, int, int]]:
        """(a, b, |A|, |B|, |A∩B|) in bytes over unique units per agent."""
        agents = self.teammates()
        rows = []
        sets = self.agent_sets[gran]
        for i, a in enumerate(agents):
            for b in agents[i + 1:]:
                sa, sb = sets.get(a, {}), sets.get(b, {})
                inter = sum(min(nb, sb[key]) for key, nb in sa.items() if key in sb)
                rows.append((a, b, sum(sa.values()), sum(sb.values()), inter))
        return rows

    def union_stats(self, gran: str, kinds: set[str]) -> tuple[int, int, int, int]:
        """(total fetched bytes, union unique bytes, sum of per-agent unique bytes, bytes held by >=2 agents)."""
        total = 0
        holders: dict[tuple, int] = Counter()
        size: dict[tuple, int] = {}
        per_agent_unique = 0
        for agent, keyed in self.agent_sets[gran].items():
            if self.agent_kind.get(agent, "lead") not in kinds:
                continue
            per_agent_unique += sum(keyed.values())
            for key, nb in keyed.items():
                holders[key] += 1
                size[key] = nb
        for item, units in zip(self.items, self.item_units):
            if item["kind"] in kinds:
                total += item["bytes"]
        union = sum(size.values())
        shared = sum(nb for key, nb in size.items() if holders[key] >= 2)
        return total, union, per_agent_unique, shared

    def file_table(self) -> list[dict]:
        per_file: dict[str, dict] = {}
        for item, units in zip(self.items, self.item_units):
            path = item.get("path") or "(unknown)"
            entry = per_file.setdefault(path, {"bytes": Counter(), "lines": defaultdict(set), "readers": Counter()})
            entry["bytes"][item["agent"]] += item["bytes"]
            entry["readers"][item["agent"]] += 1
            for key, nb in units["range"]:
                if key:
                    entry["lines"][item["agent"]].add(key)
        rows = []
        for path, entry in per_file.items():
            lines = self._file_lines(path)
            file_bytes = sum(len(l.encode("utf-8", "surrogatepass")) + 1 for l in lines) if lines else None
            coverage = {}
            for agent, keys in entry["lines"].items():
                covered = sum(len(lines[k[2]].encode("utf-8", "surrogatepass")) + 1 for k in keys
                              if lines and isinstance(k[2], int) and 0 <= k[2] < len(lines))
                coverage[agent] = covered / file_bytes if file_bytes else None
            rows.append({"path": path, "file_bytes": file_bytes, "bytes": entry["bytes"], "reads": entry["readers"],
                         "coverage": coverage, "agents": len(entry["bytes"])})
        rows.sort(key=lambda r: -sum(r["bytes"].values()))
        return rows

    def report(self) -> str:
        out = []
        label = self.label or self.path.stem
        out.append(f"## {label}")
        chunk_means = []
        for gran in ("cdc256", "cdc1k"):
            n_units = sum(len(u[gran]) for u in self.item_units)
            n_bytes = sum(nb for u in self.item_units for _, nb in u[gran])
            chunk_means.append(f"{gran} mean chunk {n_bytes / n_units:.0f} B" if n_units else f"{gran} n/a")
        out.append(f"trace: `{self.path.name}` | model: {self.model} | status: {self.status} | wall: {self.wall_ms / 1000:.0f}s | "
                   f"content source: {dict(self.sources)} | dropped: {dict(self.dropped) or 'none'} | git rev: {self.blobs.rev} | "
                   + " | ".join(chunk_means))
        kinds = self.kinds()
        # workload
        out.append("")
        out.append("**Workload shape** (model calls with usage; prefill/decode split estimated with the glm-5.3-flash latency fit "
                   "2.6 s + 0.42 ms/uncached tok + 0.10 ms/cached tok + 18.4 ms/output tok):")
        out.append("| kind | calls | prompt tok (uncached+cached) | cache hit | output tok | thinking share of output | out/prompt | model time | est. prefill / decode share | file bytes fetched | file bytes per output tok |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
        for kind in kinds:
            w = self.work.get(kind, Counter())
            fetched = sum(i["bytes"] for i in self.items if i["kind"] == kind)
            prefill = 0.42 * w["uncached"] + 0.10 * w["cached"]
            decode = 18.4 * w["output"]
            fixed = 2600 * w["calls"]
            model_ms = prefill + decode + fixed
            out_chars = w["text_chars"] + w["think_chars"] + w.get("tool_input_chars", 0)
            think = pct(w["think_chars"], out_chars) if out_chars else "n/a"
            out.append(f"| {kind} | {w['calls']} | {fmt(w['prompt'])} ({fmt(w['uncached'])}+{fmt(w['cached'])}) | "
                       f"{pct(w['cached'], w['prompt'])} | {fmt(w['output'])} | {think} | "
                       f"{(w['output'] / w['prompt']) if w['prompt'] else 0:.3f} | {w['ms'] / 1000:.0f}s | "
                       f"{pct(prefill, model_ms)} / {pct(decode, model_ms)} | {fmt(fetched)} | "
                       f"{(fetched / w['output']) if w['output'] else 0:.1f} |")
        out.append("")
        out.append("**What the redundant bytes cost in prompt volume** (`range` granularity; teammates never compact, so a fetched byte is re-sent on every later call of that agent)")
        out.append("| kind | chars/token | re-send x | file content in prompts (tok) | share of prompt tok | cross-redundant file content in prompts (tok) | share of prompt tok | cross-redundant, first send only (tok) | share of uncached tok |")
        out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for kind in kinds:
            r = self.resend.get(kind)
            w = self.work.get(kind, Counter())
            if not r or not w["prompt"]:
                continue
            out.append(f"| {kind} | {r['chars_per_token']:.2f} | {r['resend_x']:.1f}x | {fmt(r['file_prompt_tokens'])} | {pct(r['file_prompt_tokens'], w['prompt'])} | "
                       f"{fmt(r['cross_prompt_tokens'])} | {pct(r['cross_prompt_tokens'], w['prompt'])} | {fmt(r['cross_once_tokens'])} | {pct(r['cross_once_tokens'], w['uncached'])} |")
        # redundancy by granularity
        for view in ("teammates", "all"):
            title = ("**Byte-level redundancy among teammates** (lead excluded from the state; "
                     "cross = fetched earlier by another teammate)" if view == "teammates"
                     else "**Byte-level redundancy, all agents** (cross = fetched earlier by any other agent; "
                          "split by whether only the lead had it)")
            out.append("")
            out.append(title)
            out.append("| granularity | kind | bytes fetched | new | intra-agent repeat | cross-agent repeat | redundancy (intra+cross) | cross only | cross of which lead-only | unmatched (short lines) |")
            out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for gran in GRANULARITIES:
                for kind in kinds:
                    if view == "teammates" and kind != "teammate":
                        continue
                    c = self.stats[view][gran].get(kind)
                    if not c or not c["total"]:
                        continue
                    out.append(f"| {gran} | {kind} | {fmt(c['total'])} | {pct(c['new'], c['total'])} | {pct(c['intra'], c['total'])} | "
                               f"{pct(c['cross'], c['total'])} | **{pct(c['intra'] + c['cross'], c['total'])}** | {pct(c['cross'], c['total'])} | "
                               f"{pct(c['cross_lead'], c['cross']) if c['cross'] else '-'} | {pct(c['unmatched'], c['total'])} |")
        # union / copies
        out.append("")
        out.append("**Copies of the same bytes across teammate contexts** (unique units per agent; teammates never compact, so every fetched byte stays resident)")
        out.append("| granularity | total fetched | union unique | sum of per-teammate unique | resident copies (sum/union) | union bytes held by >=2 teammates | redundancy 1-union/total |")
        out.append("|---|---:|---:|---:|---:|---:|---:|")
        for gran in GRANULARITIES:
            total, union, per_agent, shared = self.union_stats(gran, {"teammate"})
            if not total:
                continue
            out.append(f"| {gran} | {fmt(total)} | {fmt(union)} | {fmt(per_agent)} | {per_agent / union if union else 0:.2f}x | "
                       f"{pct(shared, union)} | {pct(max(total - union, 0), total)} |")
        # temporal gaps
        gap_rows = [(g, self.gap_stats("teammates", g)) for g in ("range", "cdc256")]
        if any(s for _, s in gap_rows):
            out.append("")
            out.append("**How stale is a cross-teammate duplicate?** (time since another teammate first fetched the same bytes; byte-weighted; "
                       "the provider prefix cache kept idle entries ~5 min in the earlier probe)")
            out.append("| granularity | cross bytes | p50 gap | p90 gap | max gap | <60s | 60-300s | 300-600s | >600s |")
            out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for gran, s in gap_rows:
                if not s:
                    continue
                b = s["buckets"]
                out.append(f"| {gran} | {fmt(s['total'])} | {s['p50']:.0f}s | {s['p90']:.0f}s | {s['max']:.0f}s | "
                           f"{b['<60s'] * 100:.0f}% | {b['60-300s'] * 100:.0f}% | {b['300-600s'] * 100:.0f}% | {b['>600s'] * 100:.0f}% |")
        # pairwise
        pairs = self.pairwise("range")
        if pairs:
            out.append("")
            shown = sorted(pairs, key=lambda r: -r[4])[:self.max_pairs]
            more = f" (top {self.max_pairs} of {len(pairs)} pairs by overlap)" if len(pairs) > self.max_pairs else ""
            out.append(f"**Pairwise overlap between teammates**{more} (exact provenance `range`; content-defined `cdc256` in brackets): |A∩B| as share of A and of B")
            out.append("| A | B | A bytes | B bytes | overlap bytes | of A | of B |")
            out.append("|---|---|---:|---:|---:|---:|---:|")
            cdc = {(a, b): inter for a, b, _, _, inter in self.pairwise("cdc256")}
            for a, b, sa, sb, inter in shown:
                out.append(f"| {self.dn(a)} | {self.dn(b)} | {fmt(sa)} | {fmt(sb)} | "
                           f"{fmt(inter)} [{fmt(cdc.get((a, b), 0))}] | {pct(inter, sa)} | {pct(inter, sb)} |")
        # per agent
        out.append("")
        out.append("**Per agent** (`range` granularity, all-agents view)")
        out.append("| agent | kind | model calls | peak prompt tok | output tok | items (read_file / shell) | bytes fetched | new | intra | cross | unique bytes held | first-source of bytes later fetched by others |")
        out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        later = self._first_source_reuse("range")
        calls_by_agent: dict[str, list[dict]] = defaultdict(list)
        for call in self.model_calls:
            if call["status"] == "ok":
                calls_by_agent[call["agent"]].append(call)
        for agent in sorted(self.agent_stats["all"]["range"], key=lambda a: (self.agent_kind.get(a) != "lead", a)):
            c = self.agent_stats["all"]["range"][agent]
            n_read = sum(1 for i in self.items if i["agent"] == agent and i["tool"] == "read_file")
            n_bash = sum(1 for i in self.items if i["agent"] == agent and i["tool"] == "bash")
            held = sum(self.agent_sets["range"].get(agent, {}).values())
            calls = calls_by_agent.get(agent, [])
            peak = max(((cl["input"] or 0) + (cl["cache_read"] or 0)) for cl in calls) if calls else 0
            out_tok = sum(cl["output"] or 0 for cl in calls)
            out.append(f"| {self.dn(agent)} | {self.agent_kind.get(agent, 'lead')} | {len(calls)} | {fmt(peak)} | {fmt(out_tok)} | {n_read} / {n_bash} | {fmt(c['total'])} | "
                       f"{pct(c['new'], c['total'])} | {pct(c['intra'], c['total'])} | {pct(c['cross'], c['total'])} | {fmt(held)} | {fmt(later.get(agent, 0))} |")
        # files
        rows = self.file_table()
        if rows:
            out.append("")
            out.append(f"**Files** (top {self.top_files} by bytes fetched; coverage = share of the file's bytes each agent fetched at least once)")
            out.append("| file | size | agents | bytes fetched (all agents) | reads | per-agent coverage |")
            out.append("|---|---:|---:|---:|---:|---|")
            for row in rows[:self.top_files]:
                cov = ", ".join(f"{self.dn(a)} {c * 100:.0f}%" if c is not None else f"{self.dn(a)} ?"
                                for a, c in sorted(row["coverage"].items(), key=lambda x: -(x[1] or 0)))
                out.append(f"| {row['path']} | {fmt(row['file_bytes']) if row['file_bytes'] else '?'} | {row['agents']} | "
                           f"{fmt(sum(row['bytes'].values()))} | {sum(row['reads'].values())} | {cov} |")
        if self.notes:
            out.append("")
            out.extend(f"- {n}" for n in self.notes)
        return "\n".join(out)

    def gap_stats(self, view: str, gran: str) -> dict | None:
        gaps = sorted(self.gaps.get(view, {}).get(gran, []))
        total = sum(nb for _, nb in gaps)
        if not total:
            return None
        def weighted_pct(q: float) -> float:
            target = q * total
            acc = 0
            for gap, nb in gaps:
                acc += nb
                if acc >= target:
                    return gap / 1000.0
            return gaps[-1][0] / 1000.0
        buckets = Counter()
        for gap, nb in gaps:
            s = gap / 1000.0
            if s < 60:
                buckets["<60s"] += nb
            elif s < 300:
                buckets["60-300s"] += nb
            elif s < 600:
                buckets["300-600s"] += nb
            else:
                buckets[">600s"] += nb
        return {"total": total, "p50": weighted_pct(0.5), "p90": weighted_pct(0.9), "max": gaps[-1][0] / 1000.0,
                "buckets": {k: buckets[k] / total for k in ["<60s", "60-300s", "300-600s", ">600s"]}}

    def _first_source_reuse(self, gran: str) -> Counter:
        first: dict[tuple, str] = {}
        reused = Counter()
        for item, units in zip(self.items, self.item_units):
            for key, nb in units[gran]:
                if key is None:
                    continue
                if key not in first:
                    first[key] = item["agent"]
                elif first[key] != item["agent"]:
                    reused[first[key]] += nb
        return reused

    def to_json(self) -> dict:
        def counters(d):
            return {k: dict(v) for k, v in d.items()}
        return {
            "label": self.label, "trace": self.path.name, "model": self.model, "status": self.status,
            "wall_s": self.wall_ms / 1000, "sources": dict(self.sources), "dropped": dict(self.dropped),
            "work": counters(self.work),
            "stats": {view: {g: counters(self.stats[view][g]) for g in GRANULARITIES} for view in self.stats},
            "union_teammates": {g: self.union_stats(g, {"teammate"}) for g in GRANULARITIES},
            "resend": self.resend,
            "gaps_teammates_range": self.gap_stats("teammates", "range"),
            "pairwise_range": [(self.dn(a), self.dn(b), sa, sb, inter) for a, b, sa, sb, inter in self.pairwise("range")],
            "pairwise_cdc256": [(self.dn(a), self.dn(b), sa, sb, inter) for a, b, sa, sb, inter in self.pairwise("cdc256")],
            "agents": {self.dn(a): {"kind": self.agent_kind.get(a, "lead"),
                                    "range": dict(self.agent_stats["all"]["range"][a])}
                       for a in self.agent_stats["all"]["range"]},
        }


# --------------------------------------------------------------------------- aggregate
def aggregate(runs: list[Run]) -> str:
    out = ["## Cross-run summary (teammates; byte-weighted)", "",
           "| run | status / wall | teammates | teammate calls | prompt tok | cache hit | output tok | out/prompt | est. decode share | bytes fetched | whole: cross | range: intra / cross | line: cross | cdc256: cross | cdc1k: cross | resident copies (range) | file content share of prompt tok | cross-redundant share of prompt tok |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|"]
    for run in runs:
        w = run.work.get("teammate", Counter())
        st = {g: run.stats["teammates"][g].get("teammate", Counter()) for g in GRANULARITIES}
        total = st["range"]["total"]
        if not total:
            continue
        prefill = 0.42 * w["uncached"] + 0.10 * w["cached"]
        decode = 18.4 * w["output"]
        model_ms = prefill + decode + 2600 * w["calls"]
        _, union, per_agent, _ = run.union_stats("range", {"teammate"})
        out.append(f"| {run.label or run.path.stem} | {run.status} / {run.wall_ms / 1000:.0f}s | {len(run.teammates())} | {w['calls']} | "
                   f"{fmt(w['prompt'])} | {pct(w['cached'], w['prompt'])} | {fmt(w['output'])} | "
                   f"{(w['output'] / w['prompt']) if w['prompt'] else 0:.3f} | {pct(decode, model_ms)} | {fmt(total)} | "
                   f"{pct(st['whole']['cross'], st['whole']['total'])} | "
                   f"{pct(st['range']['intra'], total)} / {pct(st['range']['cross'], total)} | "
                   f"{pct(st['line']['cross'], st['line']['total'])} | {pct(st['cdc256']['cross'], st['cdc256']['total'])} | "
                   f"{pct(st['cdc1k']['cross'], st['cdc1k']['total'])} | {per_agent / union if union else 0:.2f}x | "
                   f"{pct(run.resend.get('teammate', {}).get('file_prompt_tokens', 0), w['prompt'])} | "
                   f"{pct(run.resend.get('teammate', {}).get('cross_prompt_tokens', 0), w['prompt'])} |")
    return "\n".join(out)


def summary_tables(runs: list[Run]) -> str:
    """Four compact cross-run tables (teammates only) for the report."""
    def tm(run):
        return run.work.get("teammate", Counter())
    def st(run, g):
        return run.stats["teammates"][g].get("teammate", Counter())
    out = []
    out.append("**A. Cross-teammate redundancy (byte-weighted, exact `range` provenance)**")
    out.append("| run | status / wall | teammates | teammate bytes fetched | union unique | cross-teammate redundancy | intra-teammate repeat | total redundancy (1-union/total) | resident copies | cross-redundant share of teammate prompt tok |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for run in runs:
        s = st(run, "range")
        if not s["total"]:
            continue
        total, union, per_agent, _ = run.union_stats("range", {"teammate"})
        w = tm(run)
        out.append(f"| {run.label or run.path.stem} | {run.status} / {run.wall_ms / 1000:.0f}s | {len(run.teammates())} | {fmt(s['total'])} | {fmt(union)} | "
                   f"**{pct(s['cross'], s['total'])}** | {pct(s['intra'], s['total'])} | {pct(max(total - union, 0), total)} | "
                   f"{per_agent / union if union else 0:.2f}x | {pct(run.resend.get('teammate', {}).get('cross_prompt_tokens', 0), w['prompt'])} |")
    out.append("")
    out.append("**B. The same cross-teammate redundancy under each granularity** (share of teammate bytes fetched earlier by another teammate)")
    out.append("| run | `whole` (SHA-256 of result) | `range` (file,line) | `line` (text, >=8 chars) | `cdc256` (~350 B chunks) | `cdc1k` (~1.6 KB chunks) |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for run in runs:
        if not st(run, "range")["total"]:
            continue
        cells = " | ".join(pct(st(run, g)["cross"], st(run, g)["total"]) for g in GRANULARITIES)
        out.append(f"| {run.label or run.path.stem} | {cells} |")
    out.append("")
    out.append("**C. Workload shape of the teammates** (prefill/decode split from the glm-5.3-flash latency fit; file content share counts every re-send)")
    out.append("| run | teammate calls | prompt tok | cache hit | output tok | thinking share of output | output / prompt tok | file bytes per output tok | est. decode share of model time | file content share of prompt tok | cross-redundant first sends (tok) | share of uncached tok |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for run in runs:
        w = tm(run)
        if not w["calls"]:
            continue
        fetched = sum(i["bytes"] for i in run.items if i["kind"] == "teammate")
        prefill = 0.42 * w["uncached"] + 0.10 * w["cached"]
        decode = 18.4 * w["output"]
        model_ms = prefill + decode + 2600 * w["calls"]
        out_chars = w["text_chars"] + w["think_chars"] + w.get("tool_input_chars", 0)
        think = pct(w["think_chars"], out_chars) if out_chars else "n/a"
        r = run.resend.get("teammate", {})
        out.append(f"| {run.label or run.path.stem} | {w['calls']} | {fmt(w['prompt'])} | {pct(w['cached'], w['prompt'])} | {fmt(w['output'])} | {think} | "
                   f"{(w['output'] / w['prompt']) if w['prompt'] else 0:.3f} | {(fetched / w['output']) if w['output'] else 0:.1f} | {pct(decode, model_ms)} | "
                   f"{pct(r.get('file_prompt_tokens', 0), w['prompt'])} | {fmt(r.get('cross_once_tokens', 0))} | {pct(r.get('cross_once_tokens', 0), w['uncached'])} |")
    out.append("")
    out.append("**D. Age of cross-teammate duplicates** (time since another teammate first fetched the same bytes; `range`, byte-weighted)")
    out.append("| run | cross bytes | p50 | p90 | max | within 60 s | 60-300 s | 300-600 s | over 600 s |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for run in runs:
        g = run.gap_stats("teammates", "range")
        if not g:
            continue
        b = g["buckets"]
        out.append(f"| {run.label or run.path.stem} | {fmt(g['total'])} | {g['p50']:.0f}s | {g['p90']:.0f}s | {g['max']:.0f}s | "
                   f"{b['<60s'] * 100:.0f}% | {b['60-300s'] * 100:.0f}% | {b['300-600s'] * 100:.0f}% | {b['>600s'] * 100:.0f}% |")
    return "\n".join(out)


def collect(targets: list[str]) -> list[Path]:
    files = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            files.extend(sorted(p for p in path.glob("*.jsonl")
                                if not p.name.endswith((".inputs.jsonl", ".reads.jsonl"))))
        elif path.exists():
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+")
    parser.add_argument("--git-rev", default=None,
                        help="commit (or comma-separated candidates; the one reproducing most recorded SHA-256s is used per run) "
                             "whose blobs reconstruct read_file results (default: the trace's git_head, else HEAD)")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--json", default=None)
    parser.add_argument("--top-files", type=int, default=10)
    parser.add_argument("--exclude", default=None, help="regex of file paths to ignore")
    parser.add_argument("--no-bash", action="store_true", help="only read_file results, ignore shell readers")
    parser.add_argument("--no-aggregate", action="store_true")
    parser.add_argument("--max-pairs", type=int, default=15, help="pairwise rows to print (largest overlaps first)")
    parser.add_argument("--tables", action="store_true", help="print only the four compact cross-run summary tables")
    parser.add_argument("--sort", choices=["time", "label"], default="time", help="run order in the output")
    args = parser.parse_args(argv)
    exclude = re.compile(args.exclude) if args.exclude else None
    runs = []
    for path in collect(args.targets):
        run = Run(path, Path(args.repo), args.git_rev, exclude, not args.no_bash, args.top_files, args.max_pairs)
        if not run.items and not run.model_calls:
            continue
        runs.append(run)
    if args.sort == "label":
        runs.sort(key=lambda r: (r.label or r.path.stem))
    if args.tables:
        print(summary_tables(runs))
    else:
        for run in runs:
            print(run.report())
            print()
        if len(runs) > 1 and not args.no_aggregate:
            print(aggregate(runs))
    if args.json:
        Path(args.json).write_text(json.dumps([r.to_json() for r in runs], indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
