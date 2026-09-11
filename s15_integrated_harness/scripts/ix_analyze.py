#!/usr/bin/env python3
"""Analyse context-intervention runs of the X3 workload.

Handles BOTH baseline and treated runs:
  * reads arrive via read_file or via the batch read_files tool (expanded per entry)
  * an evicted result may be a bare "[Earlier tool result saved at ...]" line or a multi-line outline
  * residency is reconstructed exactly from <trace>.reads.jsonl (which records the first content of
    every tool_result and every later rewrite) crossed with the per-call tool_result_ids in
    <trace>.inputs.jsonl, so "was this file live in the request" needs no guessing.

    python3 ix_analyze.py <trace-dir> [--answers OUTDIR] [--json OUT.json] [--rounds LABEL]
"""
from __future__ import annotations
import argparse, json, re, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path

CHAPTER = re.compile(r"(s\d\d)_[a-z_]+/README\.md")
PLACEHOLDER_PREFIXES = ("[Earlier tool result saved at", "<persisted-output>")
# read_files echoes one separator per file it ACTUALLY returned; the per-call char cap can deliver
# fewer files than were requested, so requested entries must never be counted as reads.
SEPARATOR_RE = re.compile(r"^===== (.+?)(?: \(offset=(\d+), limit=(\S+)\))? =====$", re.M)


def chapter(path: str):
    m = CHAPTER.search(str(path or ""))
    return m.group(1) if m else None


def load(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_entries(tool: str, args: dict):
    """Every (path, offset, limit) a single tool call fetched."""
    args = args if isinstance(args, dict) else {}
    if tool == "read_file":
        return [(str(args.get("path", "")), args.get("offset"), args.get("limit"))]
    if tool == "read_files":
        entries = args.get("files") or args.get("paths") or []
        if isinstance(entries, str):
            try:
                entries = json.loads(entries)
            except json.JSONDecodeError:
                entries = [p.strip() for p in entries.split(",") if p.strip()]
        out = []
        for spec in entries if isinstance(entries, list) else []:
            if isinstance(spec, str):
                out.append((spec, None, None))
            elif isinstance(spec, dict) and spec.get("path"):
                out.append((str(spec["path"]), spec.get("offset"), spec.get("limit")))
        return out
    return []


class Run:
    def __init__(self, trace: Path):
        self.trace = trace
        recs = load(trace)
        self.meta = next((r["data"] for r in recs if r["event"] == "profile_meta"), {})
        self.end = next((r["data"] for r in recs if r["event"] == "profile_end"), {})
        self.label = self.meta.get("label", trace.stem)
        self.cwd = next((r["data"]["cwd"] for r in recs if r["event"] == "run_start"), "")
        self.wall = recs[-1].get("elapsed_ms", 0) / 1000
        side = load(trace.with_name(trace.name.replace(".jsonl", ".inputs.jsonl")))
        self.lead_calls = [r for r in side if r.get("purpose") == "lead"]
        self.ok_calls = [r for r in self.lead_calls if r.get("status") == "ok"]
        reads_file = trace.with_name(trace.name.replace(".jsonl", ".reads.jsonl"))
        self.reads_log = load(reads_file) if reads_file.exists() else []
        self._rounds(recs)
        self._residency()
        self._classify()

    # ---- round reconstruction ------------------------------------------------------------
    def _rounds(self, recs):
        spans, self.rounds, cur = {}, [], None
        self.retries = sum(1 for r in recs if r["event"] == "model_retry")
        self.compactions = sum(1 for r in recs if r["event"] == "context_compact")
        self.shrinks = 0
        for r in recs:
            ev, d = r["event"], r["data"]
            if ev == "model_request" and d.get("purpose") == "lead":
                cur = {"t": r["elapsed_ms"], "tools": [], "status": None}
                spans[r["span_id"]] = cur
            elif ev in ("model_response", "model_error") and r["span_id"] in spans:
                c = spans.pop(r["span_id"])
                c["status"] = d.get("status")
                c["dur"] = (d.get("duration_ms") or 0) / 1000
                c["out"] = (d.get("usage") or {}).get("output_tokens") or 0
                c["t_end"] = r["elapsed_ms"]
                self.rounds.append(c)
                cur = c
            elif ev in ("tool_start", "context_prepare"):
                spans[r["span_id"]] = r
            elif ev == "context_prepared":
                s = spans.pop(r["span_id"], None)
                if s and (d.get("characters_after", 0) or 0) < (s["data"].get("characters_before", 0) or 0):
                    self.shrinks += 1
            elif ev == "tool_end":
                s = spans.pop(r["span_id"], None)
                if s is None or (s.get("agent_kind") or "lead") != "lead" or cur is None:
                    continue
                a = s["data"].get("arguments") or {}
                cmd = a.get("command")
                cmd = cmd.get("preview", "") if isinstance(cmd, dict) else (cmd or "")
                cur["tools"].append({
                    "tool": d.get("tool"), "status": d.get("status"), "args": a, "cmd": cmd,
                    "id": d.get("tool_call_id") or s["data"].get("tool_call_id"),
                    "chars": (d.get("result") or {}).get("characters", 0) or 0,
                    "exec_ms": d.get("duration_ms") or 0,
                })
        self.ok_rounds = [r for r in self.rounds if r["status"] == "ok"]
        self.model_time = sum(r.get("dur", 0) for r in self.ok_rounds)

    # ---- exact residency from the content sidecar -----------------------------------------
    def _residency(self):
        # tool_use_id -> call_index at which its content became a placeholder (inf if never)
        self.demoted_at = {}
        self.id_files = defaultdict(list)
        self.delivered = {}      # tool_use_id -> [(path, base offset)] actually returned
        self.requested = {}      # tool_use_id -> entries the model asked for
        self.truncated_batches = 0
        for entry in self.reads_log:
            if (entry.get("agent_kind") or "lead") != "lead":
                continue
            use_id = entry.get("tool_use_id")
            text = entry.get("content") or ""
            if entry.get("tool") == "read_files" and use_id not in self.delivered:
                asked = read_entries("read_files", entry.get("input") or {})
                self.requested[use_id] = asked
                # count only separators for files this call actually asked for: a spilled batch
                # blob read back later contains its own separators and would inflate the count
                asked_keys = {chapter(path) or path for path, _o, _l in asked}
                got = [(m.group(1), int(m.group(2) or 0)) for m in SEPARATOR_RE.finditer(text)
                       if m.group(1) != "note"
                       and (chapter(m.group(1)) or m.group(1)) in asked_keys]
                got = got[:len(asked)] if asked else got
                # a placeholder body cannot be parsed; fall back to what was asked for
                self.delivered[use_id] = got if (got or text.startswith(PLACEHOLDER_PREFIXES)) else [
                    (path, offset or 0) for path, offset, _l in asked]
                if len(self.delivered[use_id]) < len(asked):
                    self.truncated_batches += 1
            for path, _o, _l in (read_entries(entry.get("tool"), entry.get("input") or {})
                                 if entry.get("tool") != "read_files"
                                 else [(p, o, None) for p, o in self.delivered.get(use_id, [])]):
                if chapter(path) and chapter(path) not in self.id_files[use_id]:
                    self.id_files[use_id].append(chapter(path))
            is_ph = entry.get("placeholder") or text.startswith(PLACEHOLDER_PREFIXES)
            if is_ph and use_id not in self.demoted_at:
                self.demoted_at[use_id] = entry.get("call_index", 0)
        self.live_per_call = []
        for call in self.ok_calls:
            live = set()
            for use_id in call.get("tool_result_ids") or []:
                demoted = self.demoted_at.get(use_id)
                if demoted is not None and call["call_index"] >= demoted:
                    continue
                live.update(self.id_files.get(use_id, []))
            self.live_per_call.append(live)

    # ---- classification --------------------------------------------------------------------
    def _classify(self):
        seen, self.reads, self.classes, self.ctime, self.cout = set(), [], Counter(), Counter(), Counter()
        self.reacq = Counter()
        self.reacq_rounds = 0
        self.batch_calls = 0
        self.batch_entries = 0      # files a batch actually DELIVERED
        self.batch_requested = 0    # files the model asked a batch for
        live_seq = self.live_per_call + [set()] * max(0, len(self.ok_rounds) - len(self.live_per_call))
        for index, (rnd, live) in enumerate(zip(self.ok_rounds, live_seq), 1):
            kinds, reacq = set(), False
            for tool in rnd["tools"]:
                if tool["status"] != "ok":
                    continue
                name = tool["tool"]
                if name in ("read_file", "read_files"):
                    if name == "read_files":
                        self.batch_calls += 1
                        asked = read_entries(name, tool["args"])
                        got = self.delivered.get(tool.get("id"))
                        entries = ([(p, o, None) for p, o in got] if got is not None
                                   else asked)
                        self.batch_entries += len(entries)
                        self.batch_requested += len(asked)
                    else:
                        entries = read_entries(name, tool["args"])
                    for path, offset, limit in entries:
                        ch = chapter(path)
                        if ".task_outputs/" in path:
                            self.reacq["spill-file read"] += 1
                            kinds.add("reacq"); reacq = True
                            continue
                        if not ch:
                            kinds.add("other-read")
                            continue
                        record = {"round": index, "chapter": ch, "offset": offset, "limit": limit,
                                  "tool": name, "live": ch in live, "first": ch not in seen}
                        self.reads.append(record)
                        if ch in seen:
                            self.reacq["README re-read (evicted)" if ch not in live
                                       else "README re-read (still live)"] += 1
                            kinds.add("reacq"); reacq = True
                        else:
                            kinds.add("first")
                        seen.add(ch)
                elif name == "bash":
                    targets = {chapter(p) for p in re.findall(r"s\d\d_[a-z_]+/README\.md", tool["cmd"])}
                    targets.discard(None)
                    if targets & seen:
                        self.reacq["grep on an already-read README"] += len(targets & seen)
                        kinds.add("reacq"); reacq = True
                    else:
                        kinds.add("bash")
                else:
                    kinds.add("other")
            if reacq:
                self.reacq_rounds += 1
            if not rnd["tools"]:
                cls = "answer / text only"
            elif "reacq" in kinds:
                cls = "re-acquire evicted content"
            elif "first" in kinds:
                cls = "corpus acquisition"
            elif "bash" in kinds:
                cls = "grep / shell"
            else:
                cls = "todo / other tool"
            self.classes[cls] += 1
            self.ctime[cls] += rnd.get("dur", 0)
            self.cout[cls] += rnd.get("out", 0)

    # ---- aggregates -------------------------------------------------------------------------
    @property
    def outlines(self):
        return sum(1 for e in self.reads_log if "[demoted " in (e.get("content") or ""))

    @property
    def targeting(self):
        """Are re-reads precise windows (an outline is meant to make them so) or whole-file grabs?"""
        first = [r for r in self.reads if r["first"]]
        again = [r for r in self.reads if not r["first"]]
        def stat(rows):
            if not rows:
                return {"n": 0, "with_offset": 0, "pct_offset": 0, "med_limit": None}
            with_offset = sum(1 for r in rows if r.get("offset"))
            limits = [r["limit"] for r in rows if r.get("limit")]
            return {"n": len(rows), "with_offset": with_offset,
                    "pct_offset": round(100 * with_offset / len(rows)),
                    "med_limit": statistics.median(limits) if limits else None}
        return {"first": stat(first), "reread": stat(again)}

    @property
    def tokens(self):
        unc = sum(c["usage"]["input_tokens"] or 0 for c in self.ok_calls)
        cached = sum(c["usage"]["cache_read_input_tokens"] or 0 for c in self.ok_calls)
        out = sum(c["usage"]["output_tokens"] or 0 for c in self.ok_calls)
        return unc, cached, out

    def summary(self):
        unc, cached, out = self.tokens
        chapters = {r["chapter"] for r in self.reads}
        live_after_first = [len(s) for s in self.live_per_call[3:]] or [0]
        return {
            "label": self.label, "limit": self.meta.get("context_limit"),
            "flags": "+".join([k for k, v in (("batch", self.meta.get("batch_read")),
                                              ("skeleton", self.meta.get("skeleton_demote")),
                                              ("stable", self.meta.get("stable_prefix"))) if v]) or "baseline",
            "status": self.end.get("status"), "rounds": len(self.ok_rounds),
            "errors": len(self.rounds) - len(self.ok_rounds), "retries": self.retries,
            "model_time": round(self.model_time), "wall": round(self.wall),
            "reacq_rounds": self.reacq_rounds,
            "reacq": dict(self.reacq), "reads": len(self.reads), "chapters_seen": len(chapters),
            "batch_calls": self.batch_calls, "batch_entries": self.batch_entries,
            "batch_requested": self.batch_requested, "truncated_batches": self.truncated_batches,
            "uncached": unc, "cached": cached, "output": out,
            "cache_hit": round(100 * cached / max(1, unc + cached)),
            "shrinks": self.shrinks, "compactions": self.compactions,
            "outlines": self.outlines, "targeting": self.targeting,
            "live_med": statistics.median(live_after_first), "live_max": max(live_after_first),
        }

    def answer(self) -> str:
        log = self.trace.parent / f"{self.label}.console.log"
        if not log.exists():
            return ""
        skip = re.compile(r"^(\[profile\]|\[HOOK\]|\x1b|\s*$|> |  \[|\[9\d\dm)")
        lines = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
                 if not skip.match(re.sub(r"\x1b\[[0-9;]*m", "", l))]
        return "\n".join(lines[-140:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_dir")
    ap.add_argument("--answers")
    ap.add_argument("--json")
    ap.add_argument("--rounds", help="print the per-round timeline for this label")
    args = ap.parse_args()
    traces = sorted(p for p in Path(args.trace_dir).glob("run_*.jsonl") if ".inputs." not in p.name and ".reads." not in p.name)
    runs = []
    for t in traces:
        try:
            runs.append(Run(t))
        except Exception as exc:  # a run still in flight
            print(f"skip {t.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    runs.sort(key=lambda r: (r.summary()["flags"], r.label))
    head = ("arm", "run", "rounds", "model s", "wall s", "re-acq rounds", "reads", "batch calls x delivered/asked",
            "uncached tok", "cached tok", "hit%", "out tok", "shrinks", "live med/max", "status")
    print("| " + " | ".join(head) + " |")
    print("|" + "|".join("---" for _ in head) + "|")
    for r in runs:
        s = r.summary()
        print(f"| {s['flags']} | {s['label']} | {s['rounds']} | {s['model_time']} | {s['wall']} | "
              f"{s['reacq_rounds']} ({100*s['reacq_rounds']//max(1,s['rounds'])}%) | {s['reads']} | "
              f"{s['batch_calls']}x{s['batch_entries']}/{s['batch_requested']} | {s['uncached']:,} | {s['cached']:,} | "
              f"{s['cache_hit']} | {s['output']:,} | {s['shrinks']} | {s['live_med']}/{s['live_max']} | {s['status']} |")
    print()
    for r in runs:
        s = r.summary()
        print(f"{s['label']:22} re-acquisition: {s['reacq'] or 'none'}; chapters seen {s['chapters_seen']}/17; "
              f"compactions {s['compactions']}; 429 retries {s['retries']}"
              + (f"; batches truncated by the char cap: {s['truncated_batches']}" if s['truncated_batches'] else "")
              + (f"; outlines produced {s['outlines']}" if s['outlines'] else "")
              + f"; re-reads with an explicit offset {s['targeting']['reread']['pct_offset']}%"
                f" (median limit {s['targeting']['reread']['med_limit']})")
    if args.rounds:
        for r in runs:
            if r.label != args.rounds:
                continue
            print(f"\nper-round timeline for {r.label}")
            print(f"{'#':>3} {'class':28} {'tools':38} {'live':>4} {'unc tok':>8} {'cache':>7} {'out':>5} {'s':>6}")
            for index, (rnd, call) in enumerate(zip(r.ok_rounds, r.ok_calls), 1):
                live = r.live_per_call[index - 1] if index <= len(r.live_per_call) else set()
                tools = ",".join(f"{t['tool']}" + (f"[{len(read_entries(t['tool'], t['args']))}]"
                                                   if t['tool'] == 'read_files' else "")
                                 for t in rnd["tools"]) or "-"
                u = call["usage"]
                cls = next((c for c in ("re-acquire evicted content", "corpus acquisition", "grep / shell",
                                        "answer / text only", "todo / other tool")), "")
                print(f"{index:3} {'':28} {tools[:38]:38} {len(live):4} {u['input_tokens']:8,} "
                      f"{u['cache_read_input_tokens'] or 0:7,} {rnd['out']:5} {rnd.get('dur',0):6.1f}")
    if args.answers:
        out = Path(args.answers); out.mkdir(parents=True, exist_ok=True)
        for r in runs:
            (out / f"{r.label}.txt").write_text(r.answer(), encoding="utf-8")
        print(f"\nanswers written to {out}")
    if args.json:
        Path(args.json).write_text(json.dumps([r.summary() for r in runs], indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
