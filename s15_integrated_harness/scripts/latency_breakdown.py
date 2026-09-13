#!/usr/bin/env python3
"""End-to-end latency breakdown of s15 harness runs.

    python3 s15_integrated_harness/scripts/latency_breakdown.py <trace-dir | trace.jsonl> [...]
        [--json OUT.json] [--per-run] [--no-redundancy] [--repo REPO] [--tools]

Per *agentic round* (one model call of one agent plus the harness work around it) the trace is split
into four buckets that add up exactly to the round's gross time:

  prep    from the end of the previous round (last tool_end, or the turn/agent start) to the
          model_request: compaction pipeline (context_prepare span), memory recall (update_context,
          which calls the model when memory records exist), tool pool + system prompt assembly,
          inbox reads, harness bookkeeping
  model   model_request -> model_response of the round's agent call (includes provider 429 retries);
          with --stream runs it also records the client-side time to the first delivered content
          block and the streamed tail after it (NB: this provider delivers thinking/tool_use blocks in
          bursts, so the split into prefill and decode is estimated by regression on token counts)
  tools   sum of tool_start -> tool_end spans dispatched for that response
  other   everything else between the response and the last tool_end (decision events, appends)

A round without tool use is *final*: for the lead it is followed by turn-end work (Stop hook, memory
extraction model calls, post-turn memory recall) reported as `post`; for a teammate it is followed by
idle waiting, reported separately and excluded from the round.

Per-call token shape (prompt = input + cache_read (+ cache_creation), uncached = input_tokens as the
provider reports it, output), TTFT/decode statistics and a linear TTFT fit come from the
<trace>.inputs.jsonl sidecar written by scripts/profile_run.py.  Teammate input redundancy combines
(a) the share of prompt tokens re-sent from the previous call of the same agent (append-only history),
(b) the provider cache-read share, and (c) the cross-teammate byte redundancy and prompt-token share
from scripts/input_redundancy.py (exact (file, line) provenance).

Labels of the form <CATEGORY>-<mode>-<rep> (e.g. FQA-team-r1) are grouped by (CATEGORY, mode);
other labels are grouped after stripping a trailing -rN.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LABEL_RE = re.compile(r"^(?P<cat>[A-Za-z0-9]+)-(?P<mode>team|solo)-(?P<rep>r\d+)$")
MEMORY_PURPOSES = {"memory_recall", "memory_extract", "memory_consolidate"}
COMPACTION_PURPOSES = {"compaction_summary"}
PREP_FNS = ("update_context", "assemble_system_prompt", "assemble_tool_pool", "read_inbox",
            "consume_lead_inbox", "format_team_events", "remember_after_turn")


# ----------------------------------------------------------------------------- helpers
def load_records(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def iso_to_epoch(text: str) -> float:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return float("nan")


def mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.fmean(xs) if xs else None


def median(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.median(xs) if xs else None


def quantile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fmt_s(ms, digits=1):
    return "-" if ms is None else f"{ms / 1000:.{digits}f}"


def fmt_n(x, digits=0):
    if x is None:
        return "-"
    return f"{x:,.{digits}f}"


def pct(part, whole):
    if not whole:
        return "-"
    return f"{100.0 * part / whole:.1f}%"


def linfit(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    if sxx == 0:
        return None
    b = sum((p[0] - mx) * (p[1] - my) for p in pairs) / sxx
    a = my - b * mx
    ss_res = sum((p[1] - (a + b * p[0])) ** 2 for p in pairs)
    ss_tot = sum((p[1] - my) ** 2 for p in pairs) or 1.0
    return {"intercept": a, "slope": b, "r2": 1 - ss_res / ss_tot, "n": n}


# ----------------------------------------------------------------------------- data model
@dataclass
class Call:
    agent: str
    kind: str
    purpose: str
    t_req: float          # elapsed ms of the first request attempt
    t_resp: float         # elapsed ms of the successful response
    span_id: str
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None
    attempts: int = 1
    sidecar: dict | None = None
    wall_epoch: float = float("nan")

    @property
    def duration(self) -> float:
        return self.t_resp - self.t_req

    @property
    def prompt_tokens(self):
        u = self.usage or {}
        inp = u.get("input_tokens") or 0
        return inp + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)

    @property
    def uncached_tokens(self):
        return (self.usage or {}).get("input_tokens") or 0

    @property
    def cached_tokens(self):
        return (self.usage or {}).get("cache_read_input_tokens") or 0

    @property
    def output_tokens(self):
        return (self.usage or {}).get("output_tokens") or 0

    @property
    def stream(self) -> dict | None:
        return (self.sidecar or {}).get("stream")

    @property
    def ttft(self):
        s = self.stream
        return None if not s else s.get("ttft_ms")

    @property
    def decode(self):
        s = self.stream
        if not s or s.get("ttft_ms") is None or s.get("stream_end_ms") is None:
            return None
        return max(s["stream_end_ms"] - s["ttft_ms"], 0.0)


@dataclass
class Round:
    run: str
    group: tuple
    agent: str
    kind: str
    turn: str | None
    index: int
    start: float
    t_req: float | None = None
    t_resp: float | None = None
    end: float | None = None
    prep: float = 0.0
    model: float = 0.0
    tools: float = 0.0
    other: float = 0.0
    post: float = 0.0            # lead: turn-end work after a final response
    idle_before: float = 0.0     # teammate: idle wait excluded from the round
    prep_parts: Counter = field(default_factory=Counter)
    post_parts: Counter = field(default_factory=Counter)
    tool_calls: list = field(default_factory=list)   # (tool, duration_ms, status)
    call: Call | None = None
    final: bool = False
    decision: str | None = None
    error: str | None = None

    @property
    def gross(self) -> float:
        return (self.end or self.t_resp or self.start) - self.start


class Run:
    def __init__(self, trace: Path, repo: Path, redundancy: bool = True):
        self.path = trace
        self.repo = repo
        self.records = sorted(load_records(trace), key=lambda r: r.get("monotonic_ns", 0))
        self.meta = next((r["data"] for r in self.records if r.get("event") == "profile_meta"), {})
        self.end_meta = next((r["data"] for r in self.records if r.get("event") == "profile_end"), {})
        self.label = self.meta.get("label") or trace.stem
        m = LABEL_RE.match(self.label)
        if m:
            self.group = (m.group("cat"), m.group("mode"))
            self.rep = m.group("rep")
        else:
            self.group = (re.sub(r"-r\d+$", "", self.label), "?")
            self.rep = (re.search(r"-(r\d+)$", self.label) or [None, "?"])[1]
        self.status = self.end_meta.get("status") or next((r["data"].get("status") for r in self.records if r.get("event") == "run_end"), "?")
        self.wall_ms = (self.end_meta.get("wall_seconds") or 0) * 1000 or (self.records[-1]["elapsed_ms"] if self.records else 0)
        self.denials = self.end_meta.get("denials") or {}
        self.kinds: dict[str, str] = {"agent-root": "lead"}
        self.names: dict[str, str] = {"agent-root": "lead"}
        for r in self.records:
            if r.get("event") == "agent_create":
                self.kinds[r["agent_id"]] = r.get("agent_kind") or "teammate"
                self.names[r["agent_id"]] = r["data"].get("name") or r["agent_id"]
        self.calls: list[Call] = []
        self.rounds: list[Round] = []
        self.turns: list[dict] = []
        self.model_errors = Counter()
        self.sidecar_mismatch = None
        self._build_calls()
        self._attach_sidecar()
        self._build_lead_rounds()
        self._build_worker_rounds()
        self.score = self._load_score()
        self.redundancy = self._redundancy() if redundancy else None

    # -- model calls -------------------------------------------------------------------------
    def _build_calls(self):
        open_requests: dict[str, dict] = {}
        pending_attempts: Counter = Counter()   # agent -> failed attempts since last success
        first_attempt_time: dict[str, float] = {}
        for r in self.records:
            ev = r.get("event")
            if ev == "model_request":
                open_requests[r["span_id"]] = r
                agent = r.get("agent_id") or "agent-root"
                first_attempt_time.setdefault(agent, r["elapsed_ms"])
            elif ev in ("model_response", "model_error"):
                req = open_requests.pop(r["span_id"], None)
                if req is None:
                    continue
                agent = r.get("agent_id") or "agent-root"
                if ev == "model_error":
                    pending_attempts[agent] += 1
                    self.model_errors[str(r["data"].get("error_type"))] += 1
                    continue
                call = Call(agent=agent, kind=self.kinds.get(agent, r.get("agent_kind") or "lead"),
                            purpose=r["data"].get("purpose") or req["data"].get("purpose") or "unspecified",
                            t_req=first_attempt_time.pop(agent, req["elapsed_ms"]), t_resp=r["elapsed_ms"],
                            span_id=r["span_id"], usage=r["data"].get("usage") or {},
                            stop_reason=r["data"].get("stop_reason"), attempts=1 + pending_attempts.pop(agent, 0),
                            wall_epoch=iso_to_epoch(req.get("timestamp", "")))
                # the request event of the successful attempt is the round's request when no retry preceded it
                call.t_req_success = req["elapsed_ms"]
                self.calls.append(call)
        # abandoned requests (process died) are ignored

    def _attach_sidecar(self):
        side = self.path.with_name(self.path.name.removesuffix(".jsonl") + ".inputs.jsonl")
        if not side.exists():
            return
        records = [r for r in load_records(side) if r.get("status") == "ok"]
        by_agent: dict[str, list[dict]] = defaultdict(list)
        for rec in records:
            by_agent[rec.get("agent_id") or "agent-root"].append(rec)
        calls_by_agent: dict[str, list[Call]] = defaultdict(list)
        for call in self.calls:
            calls_by_agent[call.agent].append(call)
        mismatch = {}
        for agent, recs in by_agent.items():
            calls = calls_by_agent.get(agent, [])
            if len(calls) != len(recs):
                mismatch[agent] = (len(calls), len(recs))
                # fall back to nearest wall-clock match
                for rec in recs:
                    best = min(calls, key=lambda c: abs(c.wall_epoch - rec["ts"]) if not math.isnan(c.wall_epoch) else 1e9, default=None)
                    if best is not None and abs(best.wall_epoch - rec["ts"]) < 5.0 and best.sidecar is None:
                        best.sidecar = rec
                continue
            for call, rec in zip(calls, recs):
                call.sidecar = rec
        self.sidecar_mismatch = mismatch or None

    # -- rounds ------------------------------------------------------------------------------
    def _events_of(self, agent: str) -> list[dict]:
        out = []
        for r in self.records:
            aid = r.get("agent_id")
            if aid == agent or (agent == "agent-root" and aid is None and r.get("event") == "profile_timing"):
                out.append(r)
        return out

    def _tool_spans(self, events: list[dict]) -> list[tuple[float, float, str, str]]:
        starts = {}
        spans = []
        for r in events:
            if r.get("event") == "tool_start":
                starts[r["span_id"]] = r
            elif r.get("event") == "tool_end":
                s = starts.pop(r["span_id"], None)
                if s is not None:
                    spans.append((s["elapsed_ms"], r["elapsed_ms"], r["data"].get("tool") or s["data"].get("tool"), r["data"].get("status")))
        return sorted(spans)

    def _prep_parts(self, events: list[dict], lo: float, hi: float, calls: list[Call]) -> Counter:
        parts = Counter()
        for r in events:
            t = r["elapsed_ms"]
            if t < lo or t > hi:
                continue
            ev = r.get("event")
            if ev == "context_prepared":
                parts["compaction_pipeline"] += r["data"].get("duration_ms") or 0
            elif ev == "profile_timing":
                fn = r["data"].get("fn")
                if fn in PREP_FNS:
                    parts[fn] += r["data"].get("duration_ms") or 0
            elif ev == "context_compact":
                parts["summary_compactions"] += 1
        for c in calls:
            if lo <= c.t_req and c.t_resp <= hi + 1e-6:
                if c.purpose in MEMORY_PURPOSES:
                    parts["memory_model_ms"] += c.duration
                    parts["memory_model_calls"] += 1
                elif c.purpose in COMPACTION_PURPOSES:
                    parts["compaction_model_ms"] += c.duration
                    parts["compaction_model_calls"] += 1
        return parts

    def _build_lead_rounds(self):
        events = self._events_of("agent-root")
        lead_calls = [c for c in self.calls if c.agent == "agent-root" and c.purpose == "lead"]
        aux_calls = [c for c in self.calls if c.agent == "agent-root" and c.purpose != "lead"]
        tool_spans = self._tool_spans(events)
        turns = []
        open_turns = {}
        for r in events:
            if r.get("event") == "agent_active_start" and (r.get("agent_kind") == "lead" or r["data"].get("reason") != "model_cycle"):
                open_turns[r["span_id"]] = r
            elif r.get("event") == "agent_active_end" and r["span_id"] in open_turns:
                turns.append((open_turns.pop(r["span_id"]), r))
        decisions = [r for r in events if r.get("event") == "harness_decision"]
        for t_start, t_end in turns:
            lo, hi = t_start["elapsed_ms"], t_end["elapsed_ms"]
            calls = [c for c in lead_calls if lo <= c.t_req <= hi]
            turn_id = t_start.get("turn_id")
            info = {"turn_id": turn_id, "trigger": t_start["data"].get("reason"), "start": lo, "end": hi,
                    "rounds": len(calls), "pre_ms": 0.0, "post_ms": 0.0, "post_parts": Counter()}
            cursor = lo
            for i, call in enumerate(calls):
                nxt = calls[i + 1].t_req if i + 1 < len(calls) else hi
                rnd = Round(run=self.label, group=self.group, agent="agent-root", kind="lead", turn=turn_id, index=i + 1, start=cursor)
                rnd.t_req, rnd.t_resp, rnd.call = call.t_req, call.t_resp, call
                rnd.prep = call.t_req - cursor
                rnd.model = call.duration
                rnd.prep_parts = self._prep_parts(events, cursor, call.t_req, aux_calls)
                spans = [s for s in tool_spans if call.t_resp <= s[0] and s[1] <= nxt]
                rnd.tool_calls = [(s[2], s[1] - s[0], s[3]) for s in spans]
                rnd.tools = sum(s[1] - s[0] for s in spans)
                dec = next((d for d in decisions if call.t_resp <= d["elapsed_ms"] <= (spans[0][0] if spans else nxt)), None)
                if dec is not None:
                    rnd.decision = f"{dec['data'].get('decision')}:{dec['data'].get('reason')}"
                if spans:
                    rnd.end = spans[-1][1]
                    rnd.other = (rnd.end - call.t_resp) - rnd.tools
                else:
                    rnd.end = call.t_resp
                    rnd.final = True
                    if i + 1 == len(calls):
                        rnd.post = hi - call.t_resp
                        rnd.post_parts = self._prep_parts(events, call.t_resp, hi, aux_calls)
                        info["post_ms"] = rnd.post
                        info["post_parts"] = rnd.post_parts
                cursor = rnd.end
                self.rounds.append(rnd)
            if not calls:
                info["post_ms"] = hi - lo
            self.turns.append(info)
        # wake-up work before team-triggered turns: consume_lead_inbox timings outside any turn
        turn_windows = [(t["start"], t["end"]) for t in self.turns]
        for r in events:
            if r.get("event") == "profile_timing" and r["data"].get("fn") in ("consume_lead_inbox", "format_team_events"):
                t = r["elapsed_ms"]
                if not any(lo <= t <= hi for lo, hi in turn_windows):
                    nxt = next((tu for tu in self.turns if tu["start"] >= t), None)
                    if nxt is not None:
                        nxt["pre_ms"] += r["data"].get("duration_ms") or 0

    def _build_worker_rounds(self):
        for agent, kind in self.kinds.items():
            if agent == "agent-root":
                continue
            events = self._events_of(agent)
            if not events:
                continue
            calls = [c for c in self.calls if c.agent == agent]
            tool_spans = self._tool_spans(events)
            agent_start = next((r["elapsed_ms"] for r in events if r.get("event") == "agent_start"), events[0]["elapsed_ms"])
            agent_end = next((r["elapsed_ms"] for r in events if r.get("event") == "agent_end"), events[-1]["elapsed_ms"])
            active_starts = [r["elapsed_ms"] for r in events if r.get("event") == "agent_active_start"]
            inbox_reads = [(r["elapsed_ms"], r["data"].get("duration_ms") or 0) for r in events
                           if r.get("event") == "profile_timing" and r["data"].get("fn") == "read_inbox"]
            cursor = agent_start
            prev_final = True
            for i, call in enumerate(calls):
                nxt = calls[i + 1].t_req if i + 1 < len(calls) else agent_end
                rnd = Round(run=self.label, group=self.group, agent=agent, kind=kind, turn=None, index=i + 1, start=cursor)
                if prev_final:
                    # a new task / message: the round starts at the model cycle, the wait before it is idle
                    start = max([a for a in active_starts if cursor <= a <= call.t_req], default=cursor)
                    rnd.idle_before = start - cursor
                    rnd.start = start
                    reads = [d for (t, d) in inbox_reads if cursor <= t <= call.t_req]
                    if reads:
                        rnd.prep_parts["read_inbox"] += reads[-1]
                        rnd.start -= reads[-1]
                        rnd.idle_before -= reads[-1]
                else:
                    rnd.prep_parts = self._prep_parts(events, cursor, call.t_req, [])
                rnd.t_req, rnd.t_resp, rnd.call = call.t_req, call.t_resp, call
                rnd.prep = call.t_req - rnd.start
                rnd.model = call.duration
                spans = [s for s in tool_spans if call.t_resp <= s[0] and s[1] <= nxt]
                rnd.tool_calls = [(s[2], s[1] - s[0], s[3]) for s in spans]
                rnd.tools = sum(s[1] - s[0] for s in spans)
                if spans:
                    rnd.end = spans[-1][1]
                    rnd.other = (rnd.end - call.t_resp) - rnd.tools
                    prev_final = False
                else:
                    rnd.end = call.t_resp
                    rnd.final = True
                    prev_final = True
                cursor = rnd.end
                self.rounds.append(rnd)

    # -- scoring / redundancy ----------------------------------------------------------------
    def _load_score(self):
        path = self.path.parent / f"{self.label}.score.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _redundancy(self):
        try:
            sys.path.insert(0, str(HERE))
            import input_redundancy as ir  # type: ignore
        except Exception as exc:  # pragma: no cover
            return {"error": f"input_redundancy import failed: {exc}"}
        try:
            run = ir.Run(self.path, self.repo, None, None, True, 10)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        s = run.stats["teammates"]["range"].get("teammate", Counter())
        whole = run.stats["teammates"]["whole"].get("teammate", Counter())
        total, union, per_agent, _ = run.union_stats("range", {"teammate"})
        work = run.work.get("teammate", Counter())
        resend = run.resend.get("teammate", {})
        lead_range = run.stats["all"]["range"].get("lead", Counter())
        return {
            "teammates": len(run.teammates()),
            "bytes_fetched": s.get("total", 0), "cross_bytes": s.get("cross", 0), "intra_bytes": s.get("intra", 0),
            "union_unique": union, "resident_copies": (per_agent / union) if union else None,
            "cross_rate_range": (s["cross"] / s["total"]) if s.get("total") else None,
            "cross_rate_whole": (whole["cross"] / whole["total"]) if whole.get("total") else None,
            "prompt_tokens": work.get("prompt", 0),
            "cross_prompt_share": (resend.get("cross_prompt_tokens", 0) / work["prompt"]) if work.get("prompt") else None,
            "file_prompt_share": (resend.get("file_prompt_tokens", 0) / work["prompt"]) if work.get("prompt") else None,
            "cross_once_tokens": resend.get("cross_once_tokens", 0),
            "lead_bytes_fetched": lead_range.get("total", 0),
            "lead_cross_rate": (lead_range["cross"] / lead_range["total"]) if lead_range.get("total") else None,
        }

    # -- derived -----------------------------------------------------------------------------
    def wall_composition(self) -> dict:
        """Where the run's wall time goes: lead active turns, teammate active rounds, overlap, idle tail."""
        # simpler and safe: merge sorted intervals explicitly
        def merged_len(intervals):
            merged = []
            for lo, hi in sorted(i for i in intervals if i[1] > i[0]):
                if merged and lo <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            return sum(hi - lo for lo, hi in merged), merged
        lead_iv = [(t["start"], t["end"]) for t in self.turns]
        team_iv = [(r.start, r.end) for r in self.rounds if r.kind != "lead" and r.end is not None]
        lead_len, _ = merged_len(lead_iv)
        team_len, _ = merged_len(team_iv)
        both_len, _ = merged_len(lead_iv + team_iv)
        post = sum(t["post_ms"] for t in self.turns)
        lead_model = sum(r.model for r in self.rounds if r.kind == "lead")
        wall = self.wall_ms or both_len
        return {"wall_ms": wall, "lead_active_ms": lead_len, "lead_model_ms": lead_model, "lead_post_ms": post,
                "team_active_ms": team_len, "overlap_ms": lead_len + team_len - both_len,
                "any_active_ms": both_len, "idle_ms": max(wall - both_len, 0.0)}

    def calls_of(self, kind: str, purpose: str | None = None) -> list[Call]:
        return [c for c in self.calls if c.kind == kind and (purpose is None or c.purpose == purpose)]

    def teammate_segments(self) -> list[dict]:
        """Rounds of a worker agent grouped into tasks (a final round closes a segment)."""
        segments = []
        for agent in self.kinds:
            if agent == "agent-root":
                continue
            rounds = [r for r in self.rounds if r.agent == agent]
            current = []
            for rnd in rounds:
                current.append(rnd)
                if rnd.final:
                    segments.append({"agent": agent, "name": self.names.get(agent, agent), "rounds": len(current),
                                     "active_ms": sum(r.gross for r in current), "closed": True,
                                     "tool_calls": sum(len(r.tool_calls) for r in current)})
                    current = []
            if current:
                segments.append({"agent": agent, "name": self.names.get(agent, agent), "rounds": len(current),
                                 "active_ms": sum(r.gross for r in current), "closed": False,
                                 "tool_calls": sum(len(r.tool_calls) for r in current)})
        return segments

    def resent_share(self, kind: str) -> tuple[int, int, int]:
        """(resent prompt tokens, total prompt tokens, cache-read tokens) over agents of this kind."""
        resent = total = cached = 0
        by_agent: dict[str, list[Call]] = defaultdict(list)
        for c in self.calls:
            if c.kind == kind and c.purpose in ("lead", "teammate", "one_shot"):
                by_agent[c.agent].append(c)
        for calls in by_agent.values():
            prev = 0
            for c in calls:
                p = c.prompt_tokens
                new = p if prev == 0 else max(p - prev, 0)
                resent += p - new
                total += p
                cached += c.cached_tokens
                prev = p
        return resent, total, cached


# ----------------------------------------------------------------------------- aggregation
def round_bucket_table(rounds: list[Round], title: str) -> list[str]:
    out = [f"**{title}** (time-weighted shares of the pooled round time; means per round in seconds)",
           "| group | kind | rounds | final rounds | gross mean | gross median | gross p90 | prep | model | tools | other | prep share | model share (to first delivered block / streamed tail) | tools share | other share |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups: dict[tuple, list[Round]] = defaultdict(list)
    for r in rounds:
        groups[(r.group, r.kind)].append(r)
    for (group, kind), rs in sorted(groups.items(), key=lambda kv: (kv[0][0][1], kv[0][0][0], kv[0][1])):
        gross = sum(r.gross for r in rs)
        prep = sum(r.prep for r in rs)
        model = sum(r.model for r in rs)
        tools = sum(r.tools for r in rs)
        other = sum(r.other for r in rs)
        ttft = [r.call.ttft for r in rs if r.call and r.call.ttft is not None]
        dec = [r.call.decode for r in rs if r.call and r.call.decode is not None]
        model_with_stream = sum(r.model for r in rs if r.call and r.call.ttft is not None)
        if ttft and model_with_stream:
            ttft_share = sum(ttft) / (sum(ttft) + sum(dec)) if (sum(ttft) + sum(dec)) else 0
            split = f"{pct(model, gross)} ({100 * (model / gross) * ttft_share:.1f}% / {100 * (model / gross) * (1 - ttft_share):.1f}%)"
        else:
            split = pct(model, gross)
        n = len(rs)
        out.append(f"| {group[0]}-{group[1]} | {kind} | {n} | {sum(1 for r in rs if r.final)} | {fmt_s(gross / n)} | {fmt_s(median([r.gross for r in rs]))} | "
                   f"{fmt_s(quantile([r.gross for r in rs], 0.9))} | {fmt_s(prep / n, 2)} | {fmt_s(model / n)} | {fmt_s(tools / n, 2)} | {fmt_s(other / n, 3)} | "
                   f"{pct(prep, gross)} | {split} | {pct(tools, gross)} | {pct(other, gross)} |")
    return out


def prep_detail_table(rounds: list[Round]) -> list[str]:
    out = ["**Context preparation, attributed** (lead rounds; seconds per round, pooled by group; `memory recall model` = model calls made by update_context; `post` = turn-end work after a final round: Stop hook + memory extraction + post-turn recall)",
           "| group | rounds | prep mean | compaction pipeline (incl. any summary call) | of which summary-compaction model calls | summary calls / run | memory recall (model) | memory recall calls / round | prompt+tool assembly | inbox | unattributed | turns | post per turn | memory extract (model) per turn | memory calls per turn | post amortised per round | (prep + post) share of lead-side time |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups: dict[tuple, list[Round]] = defaultdict(list)
    for r in rounds:
        if r.kind == "lead":
            groups[r.group].append(r)
    for group, rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        n = len(rs)
        prep = sum(r.prep for r in rs)
        comp = sum(r.prep_parts.get("compaction_pipeline", 0) for r in rs)
        comp_model = sum(r.prep_parts.get("compaction_model_ms", 0) for r in rs)
        comp_calls = sum(r.prep_parts.get("compaction_model_calls", 0) for r in rs)
        runs_in_group = len({r.run for r in rs}) or 1
        mem = sum(r.prep_parts.get("memory_model_ms", 0) for r in rs)
        memc = sum(r.prep_parts.get("memory_model_calls", 0) for r in rs)
        upd = sum(r.prep_parts.get("update_context", 0) for r in rs)
        asm = sum(r.prep_parts.get("assemble_system_prompt", 0) + r.prep_parts.get("assemble_tool_pool", 0) for r in rs)
        inbox = sum(r.prep_parts.get("read_inbox", 0) + r.prep_parts.get("consume_lead_inbox", 0) + r.prep_parts.get("format_team_events", 0) for r in rs)
        attributed = comp + max(upd, mem) + asm + inbox  # comp already contains any summary model call
        finals = [r for r in rs if r.final and r.post]
        turns = len(finals)
        post = sum(r.post for r in finals)
        post_mem = sum(r.post_parts.get("memory_model_ms", 0) for r in finals)
        post_memc = sum(r.post_parts.get("memory_model_calls", 0) for r in finals)
        lead_side = sum(r.gross for r in rs) + post
        out.append(f"| {group[0]}-{group[1]} | {n} | {fmt_s(prep / n, 2)} | {fmt_s(comp / n, 3)} | {fmt_s(comp_model / n, 3)} | {comp_calls / runs_in_group:.2f} | {fmt_s(mem / n, 2)} | {memc / n:.2f} | {fmt_s(asm / n, 3)} | {fmt_s(inbox / n, 3)} | "
                   f"{fmt_s(max(prep - attributed, 0) / n, 3)} | {turns} | {fmt_s(post / turns, 1) if turns else '-'} | {fmt_s(post_mem / turns, 1) if turns else '-'} | {(post_memc / turns) if turns else 0:.2f} | "
                   f"{fmt_s(post / n, 1)} | {pct(prep + post, lead_side)} |")
    return out


def token_table(runs: list[Run]) -> list[str]:
    out = ["**Tokens and model-call timing per call** (agent calls only: purpose lead / teammate; prompt = input + cache_read (+cache_creation); uncached = input_tokens as reported; first block = client time to the first delivered content block (NOT prefill alone: thinking/tool_use blocks arrive in bursts, see the delivery-pattern table); tail = first block to stream end; fit: first-block time = a + b x uncached tokens)",
           "| group | kind | calls | prompt tok mean | prompt tok median | uncached mean | cache-read share | output tok mean | output tok median | thinking share of output chars | model call mean | first block mean | first block median | tail mean | tail tok/s (pooled) | first-block fit a (s) + b (ms/tok), r2 | first block is thinking |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    groups: dict[tuple, list[Call]] = defaultdict(list)
    for run in runs:
        for c in run.calls:
            if c.purpose in ("lead", "teammate", "one_shot"):
                groups[(run.group, c.kind)].append(c)
    for (group, kind), cs in sorted(groups.items(), key=lambda kv: (kv[0][0][1], kv[0][0][0], kv[0][1])):
        prompt = [c.prompt_tokens for c in cs]
        unc = [c.uncached_tokens for c in cs]
        cached = sum(c.cached_tokens for c in cs)
        outp = [c.output_tokens for c in cs]
        think = sum((c.sidecar or {}).get("output_thinking_chars", 0) for c in cs)
        allout = sum((c.sidecar or {}).get("output_thinking_chars", 0) + (c.sidecar or {}).get("output_text_chars", 0) + (c.sidecar or {}).get("output_tool_input_chars", 0) for c in cs)
        ttft = [c.ttft for c in cs if c.ttft is not None]
        dec = [c.decode for c in cs if c.decode is not None]
        dec_tok = sum(c.output_tokens for c in cs if c.decode is not None)
        fit = linfit([c.uncached_tokens for c in cs if c.ttft is not None], ttft)
        fit_text = "-" if not fit else f"{fit['intercept'] / 1000:.2f} s + {fit['slope']:.3f} ms/tok, r2 {fit['r2']:.2f}"
        first_think = sum(1 for c in cs if (c.stream or {}).get("first_block_type") == "thinking")
        streamed = sum(1 for c in cs if c.stream)
        out.append(f"| {group[0]}-{group[1]} | {kind} | {len(cs)} | {fmt_n(mean(prompt))} | {fmt_n(median(prompt))} | {fmt_n(mean(unc))} | {pct(cached, sum(prompt))} | "
                   f"{fmt_n(mean(outp))} | {fmt_n(median(outp))} | {pct(think, allout)} | {fmt_s(mean([c.duration for c in cs]))} | {fmt_s(mean(ttft))} | {fmt_s(median(ttft))} | {fmt_s(mean(dec))} | "
                   f"{fmt_n(dec_tok / (sum(dec) / 1000), 1) if dec and sum(dec) else '-'} | {fit_text} | {pct(first_think, streamed) if streamed else '-'} |")
    # pooled fits over every agent call (wider prompt-size range than any single group)
    pooled = [c for cs in groups.values() for c in cs]
    for kind in ("lead", "teammate", "all"):
        cs = [c for c in pooled if kind == "all" or c.kind == kind]
        ttft = [(c.uncached_tokens, c.prompt_tokens, c.ttft) for c in cs if c.ttft is not None]
        if len(ttft) < 3:
            continue
        fit_u = linfit([t[0] for t in ttft], [t[2] for t in ttft])
        fit_p = linfit([t[1] for t in ttft], [t[2] for t in ttft])
        dec = [c for c in cs if c.decode]
        rate = (sum(c.output_tokens for c in dec) / (sum(c.decode for c in dec) / 1000)) if dec else None
        fit_d = linfit([c.output_tokens for c in dec], [c.decode for c in dec]) if dec else None
        out.append(f"| pooled fit | {kind} | {len(cs)} | | | uncached {fmt_n(min(t[0] for t in ttft))}-{fmt_n(max(t[0] for t in ttft))} | | | | | | | | | "
                   f"{fmt_n(rate, 1) if rate else '-'} | first block vs uncached: {fit_u['intercept'] / 1000:.2f} s + {fit_u['slope']:.3f} ms/tok (r2 {fit_u['r2']:.2f}); vs prompt: {fit_p['intercept'] / 1000:.2f} s + {fit_p['slope']:.3f} ms/tok (r2 {fit_p['r2']:.2f})"
                   f"{'' if not fit_d else f'; tail vs output tok: {fit_d['intercept'] / 1000:.2f} s + {fit_d['slope']:.1f} ms/tok (r2 {fit_d['r2']:.2f})'} | |")
    return out


def fit3(rows):
    """Least squares y = a + b*x1 + c*x2 over rows of (x1, x2, y); returns ((a, b, c), r2) or None."""
    rows = [r for r in rows if None not in r]
    if len(rows) < 6:
        return None
    n = len(rows)
    X = [[1.0, r[0], r[1]] for r in rows]
    y = [r[2] for r in rows]
    M = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(3)] + [sum(X[i][a] * y[i] for i in range(n))] for a in range(3)]
    for i in range(3):
        pivot = max(range(i, 3), key=lambda k: abs(M[k][i]))
        M[i], M[pivot] = M[pivot], M[i]
        if abs(M[i][i]) < 1e-12:
            return None
        for k in range(3):
            if k != i:
                f = M[k][i] / M[i][i]
                M[k] = [M[k][j] - f * M[i][j] for j in range(4)]
    beta = tuple(M[i][3] / M[i][i] for i in range(3))
    pred = [beta[0] + beta[1] * r[0] + beta[2] * r[1] for r in rows]
    my = sum(y) / n
    ss_res = sum((a - b) ** 2 for a, b in zip(y, pred))
    ss_tot = sum((a - my) ** 2 for a in y) or 1.0
    return beta, 1 - ss_res / ss_tot


def decomposition_table(runs: list[Run]) -> list[str]:
    out = ["**Prefill vs decode by regression** (agent calls only; model-call duration = a + b x uncached prompt tokens + c x output tokens, least squares; the per-group shares apply the pooled coefficients to each group's own token counts, so they add to ~100% of that group's call time)",
           "| group | kind | calls | mean call (s) | fixed a | prefill b x uncached | decode c x output | residual |",
           "|---|---|---:|---:|---:|---:|---:|---:|"]
    calls = [(run.group, c) for run in runs for c in run.calls if c.purpose in ("lead", "teammate", "one_shot")]
    pooled = fit3([(c.uncached_tokens, c.output_tokens, c.duration) for _, c in calls])
    if not pooled:
        return out + ["| (not enough calls) | | | | | | | |"]
    (a, b, cc), r2 = pooled
    groups: dict[tuple, list[Call]] = defaultdict(list)
    for group, c in calls:
        groups[(group, c.kind)].append(c)
    for (group, kind), cs in sorted(groups.items(), key=lambda kv: (kv[0][0][1], kv[0][0][0], kv[0][1])):
        total = sum(c.duration for c in cs)
        fixed = a * len(cs)
        prefill = b * sum(c.uncached_tokens for c in cs)
        decode = cc * sum(c.output_tokens for c in cs)
        out.append(f"| {group[0]}-{group[1]} | {kind} | {len(cs)} | {total / len(cs) / 1000:.1f} | {pct(fixed, total)} | {pct(prefill, total)} | {pct(decode, total)} | {pct(total - fixed - prefill - decode, total)} |")
    out.append("")
    out.append("| fit over | calls | a fixed (s) | b prefill (ms per uncached tok) | c decode (ms per output tok) | r2 |")
    out.append("|---|---:|---:|---:|---:|---:|")
    subsets = [("all agent calls", [c for _, c in calls]), ("lead", [c for _, c in calls if c.kind == "lead"]),
               ("teammate", [c for _, c in calls if c.kind == "teammate"]),
               ("responses ending in tool_use", [c for _, c in calls if c.stop_reason == "tool_use"]),
               ("responses ending in end_turn", [c for _, c in calls if c.stop_reason == "end_turn"])]
    for name, cs in subsets:
        fit = fit3([(c.uncached_tokens, c.output_tokens, c.duration) for c in cs])
        if fit:
            (fa, fb, fc), fr2 = fit
            out.append(f"| {name} | {len(cs)} | {fa / 1000:.2f} | {fb:.3f} | {fc:.2f} | {fr2:.2f} |")
    return out


def burst_table(runs: list[Run]) -> list[str]:
    out = ["**Streaming delivery pattern** (why the first-block time is not prefill: the provider delivers thinking and tool_use blocks in bursts; 'first block' = client time to the first delivered content block, 'tail' = first block to end of stream; a tail under 0.1 s means the whole response arrived at once)",
           "| kind | response ends with | calls | output tok mean | first block mean (s) | tail mean (s) | tail share of call | calls with tail < 0.1 s | first-block fit: a + c x output tok (r2) |",
           "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    calls = [c for run in runs for c in run.calls if c.purpose in ("lead", "teammate", "one_shot") and c.ttft is not None]
    for kind in ("lead", "teammate", "one_shot"):
        for stop in ("tool_use", "end_turn", "max_tokens"):
            cs = [c for c in calls if c.kind == kind and c.stop_reason == stop]
            if len(cs) < 3:
                continue
            fit = fit3([(c.uncached_tokens, c.output_tokens, c.ttft) for c in cs])
            fit_text = "-" if not fit else f"{fit[0][0] / 1000:.2f} s + {fit[0][2]:.2f} ms/tok ({fit[1]:.2f})"
            out.append(f"| {kind} | {stop} | {len(cs)} | {fmt_n(mean([c.output_tokens for c in cs]))} | {fmt_s(mean([c.ttft for c in cs]), 2)} | {fmt_s(mean([c.decode for c in cs]), 2)} | "
                       f"{pct(sum(c.decode for c in cs), sum(c.duration for c in cs))} | {pct(sum(1 for c in cs if c.decode < 100), len(cs))} | {fit_text} |")
    return out


def aux_table(runs: list[Run]) -> list[str]:
    out = ["**Auxiliary model calls made by the harness itself** (context maintenance: memory recall inside update_context before a lead call, memory extraction after a lead turn, summary compaction; pooled by group)",
           "| group | purpose | calls | calls / run | calls / lead round | mean duration | median | prompt tok mean | output tok mean | stop=max_tokens share | first block mean | tail mean | total per run (s) |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups: dict[tuple, list[Run]] = defaultdict(list)
    for run in runs:
        groups[run.group].append(run)
    for group, rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        n = len(rs)
        lead_rounds = sum(1 for run in rs for r in run.rounds if r.kind == "lead") or 1
        purposes = sorted({c.purpose for run in rs for c in run.calls if c.purpose not in ("lead", "teammate", "one_shot")})
        for purpose in purposes:
            cs = [c for run in rs for c in run.calls if c.purpose == purpose]
            if not cs:
                continue
            ttft = [c.ttft for c in cs if c.ttft is not None]
            dec = [c.decode for c in cs if c.decode is not None]
            maxed = sum(1 for c in cs if c.stop_reason == "max_tokens")
            out.append(f"| {group[0]}-{group[1]} | {purpose} | {len(cs)} | {len(cs) / n:.1f} | {len(cs) / lead_rounds:.2f} | {fmt_s(mean([c.duration for c in cs]))} | "
                       f"{fmt_s(median([c.duration for c in cs]))} | {fmt_n(mean([c.prompt_tokens for c in cs]))} | {fmt_n(mean([c.output_tokens for c in cs]))} | {pct(maxed, len(cs))} | "
                       f"{fmt_s(mean(ttft))} | {fmt_s(mean(dec))} | {sum(c.duration for c in cs) / n / 1000:.1f} |")
    return out


def redundancy_table(runs: list[Run]) -> list[str]:
    out = ["**Teammate input redundancy** (a) re-sent = prompt tokens already sent in the same agent's previous call (append-only history), (b) provider cache-read share, (c) cross-teammate file bytes another teammate had fetched first (exact (file,line) provenance) and their share of teammate prompt tokens",
           "| run | teammates | teammate calls | teammate prompt tok | re-sent share (a) | cache-read share (b) | file bytes fetched | cross-teammate redundancy, range (c) | same, whole-result SHA | resident copies | cross-redundant share of prompt tok | file content share of prompt tok | lead: prompt tok | lead re-sent share | lead cache-read |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for run in runs:
        tm_calls = run.calls_of("teammate", "teammate")
        resent, total, cached = run.resent_share("teammate")
        lresent, ltotal, lcached = run.resent_share("lead")
        red = run.redundancy or {}
        if red.get("error"):
            red = {}
        rr = red.get("cross_rate_range")
        rw = red.get("cross_rate_whole")
        out.append(f"| {run.label} | {len([a for a, k in run.kinds.items() if k == 'teammate'])} | {len(tm_calls)} | {fmt_n(total)} | {pct(resent, total)} | {pct(cached, total)} | "
                   f"{fmt_n(red.get('bytes_fetched'))} | {'-' if rr is None else f'{100 * rr:.1f}%'} | {'-' if rw is None else f'{100 * rw:.1f}%'} | "
                   f"{'-' if red.get('resident_copies') is None else f'{red['resident_copies']:.2f}x'} | {'-' if red.get('cross_prompt_share') is None else f'{100 * red['cross_prompt_share']:.1f}%'} | "
                   f"{'-' if red.get('file_prompt_share') is None else f'{100 * red['file_prompt_share']:.1f}%'} | {fmt_n(ltotal)} | {pct(lresent, ltotal)} | {pct(lcached, ltotal)} |")
    return out


def runs_table(runs: list[Run]) -> list[str]:
    out = ["**Runs** (lead turns = lead activations; rounds = model calls of the agent loop; memory/compaction = auxiliary model calls; score: CODE tests passed, MATH answers correct, FQA keyword coverage)",
           "| run | status | wall (s) | lead turns | lead rounds | teammates | teammate rounds | teammate tasks (closed) | memory calls | compaction calls | model errors (429 etc.) | tool calls (denied) | score |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for run in runs:
        lead_rounds = [r for r in run.rounds if r.kind == "lead"]
        tm_rounds = [r for r in run.rounds if r.kind != "lead"]
        segs = run.teammate_segments()
        mem = len([c for c in run.calls if c.purpose in MEMORY_PURPOSES])
        comp = len([c for c in run.calls if c.purpose in COMPACTION_PURPOSES])
        tools = sum(len(r.tool_calls) for r in run.rounds)
        denied = sum(1 for r in run.rounds for t in r.tool_calls if t[2] == "denied")
        score = run.score or {}
        if score.get("category") == "FQA":
            sc = f"coverage {score.get('coverage')}"
        elif score:
            sc = f"{score.get('correct')}/{score.get('total')}"
        else:
            sc = "-"
        out.append(f"| {run.label} | {run.status} | {run.wall_ms / 1000:.0f} | {len(run.turns)} | {len(lead_rounds)} | {len([a for a, k in run.kinds.items() if k == 'teammate'])} | "
                   f"{len(tm_rounds)} | {len(segs)} ({sum(1 for s in segs if s['closed'])}) | {mem} | {comp} | {sum(run.model_errors.values())} | {tools} ({denied}) | {sc} |")
    return out


def task_table(runs: list[Run]) -> list[str]:
    out = ["**Agent runs per task** (mean over runs of a group unless noted; a teammate task = rounds from assignment to its final reply; wall = driver wall time)",
           "| group | runs | wall mean (s) | lead turns / run | lead rounds / run | lead rounds / turn | teammates / run | teammate rounds / task: mean | median | max | teammate task active time (s): mean | median | teammate tasks / run | tool calls / round (lead) | tool calls / round (teammate) | model calls / run (all) | denied tool calls / run |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups: dict[tuple, list[Run]] = defaultdict(list)
    for run in runs:
        groups[run.group].append(run)
    for group, rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        n = len(rs)
        lead_rounds = [r for run in rs for r in run.rounds if r.kind == "lead"]
        tm_rounds = [r for run in rs for r in run.rounds if r.kind != "lead"]
        segs = [s for run in rs for s in run.teammate_segments()]
        turns = sum(len(run.turns) for run in rs)
        denied = sum(1 for run in rs for r in run.rounds for t in r.tool_calls if t[2] == "denied")
        out.append(f"| {group[0]}-{group[1]} | {n} | {mean([run.wall_ms for run in rs]) / 1000:.0f} | {turns / n:.1f} | {len(lead_rounds) / n:.1f} | "
                   f"{(len(lead_rounds) / turns) if turns else 0:.1f} | {sum(len([a for a, k in run.kinds.items() if k == 'teammate']) for run in rs) / n:.1f} | "
                   f"{fmt_n(mean([s['rounds'] for s in segs]), 1)} | {fmt_n(median([s['rounds'] for s in segs]), 0)} | {max((s['rounds'] for s in segs), default=0)} | "
                   f"{fmt_s(mean([s['active_ms'] for s in segs]), 0)} | {fmt_s(median([s['active_ms'] for s in segs]), 0)} | {len(segs) / n:.1f} | "
                   f"{(sum(len(r.tool_calls) for r in lead_rounds) / len(lead_rounds)) if lead_rounds else 0:.2f} | "
                   f"{(sum(len(r.tool_calls) for r in tm_rounds) / len(tm_rounds)) if tm_rounds else 0:.2f} | {sum(len(run.calls) for run in rs) / n:.1f} | {denied / n:.1f} |")
    return out


def wall_table(runs: list[Run]) -> list[str]:
    out = ["**Where the wall time of a run goes** (means over the runs of a group; lead active = lead turns incl. turn-end memory work; teammate active = union of teammate round windows; overlap = lead and teammates busy at the same time; idle tail = nobody busy: waits for team events, quiescence timer, shutdown handshake)",
           "| group | runs | wall (s) | lead active (s) | share | of which lead agent calls (s) | of which turn-end memory (s) | teammate active (s) | share | overlap (s) | idle / tail (s) | share |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    groups: dict[tuple, list[Run]] = defaultdict(list)
    for run in runs:
        groups[run.group].append(run)
    for group, rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        comps = [run.wall_composition() for run in rs]
        n = len(comps)
        avg = lambda key: sum(c[key] for c in comps) / n  # noqa: E731
        wall = avg("wall_ms")
        out.append(f"| {group[0]}-{group[1]} | {n} | {wall / 1000:.0f} | {avg('lead_active_ms') / 1000:.0f} | {pct(avg('lead_active_ms'), wall)} | {avg('lead_model_ms') / 1000:.0f} | "
                   f"{avg('lead_post_ms') / 1000:.0f} | {avg('team_active_ms') / 1000:.0f} | {pct(avg('team_active_ms'), wall)} | {avg('overlap_ms') / 1000:.0f} | "
                   f"{avg('idle_ms') / 1000:.0f} | {pct(avg('idle_ms'), wall)} |")
    return out


def tool_table(rounds: list[Round]) -> list[str]:
    out = ["**Tool execution time by tool** (tool_start -> tool_end spans, pooled over all runs)",
           "| tool | calls | denied | error | mean (ms) | median (ms) | p90 (ms) | max (ms) | total (s) |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    by_tool: dict[str, list[tuple]] = defaultdict(list)
    for r in rounds:
        for t in r.tool_calls:
            by_tool[t[0]].append(t)
    for tool, ts in sorted(by_tool.items(), key=lambda kv: -sum(t[1] for t in kv[1])):
        d = [t[1] for t in ts]
        denied = sum(1 for t in ts if t[2] == "denied")
        errors = sum(1 for t in ts if t[2] == "error")
        out.append(f"| {tool} | {len(ts)} | {denied} | {errors} | {mean(d):,.0f} | {median(d):,.0f} | {quantile(d, 0.9):,.0f} | {max(d):,.0f} | {sum(d) / 1000:.1f} |")
    return out


def per_run_rounds(runs: list[Run]) -> list[str]:
    out = ["**Per-run round breakdown** (lead and teammate rounds separately; seconds)",
           "| run | kind | rounds | gross mean | prep mean | model mean | first block mean | tail mean | tools mean | other mean | prep share | model share | tools share | prompt tok mean | output tok mean |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for run in runs:
        for kind in ("lead", "teammate", "one_shot"):
            rs = [r for r in run.rounds if r.kind == kind]
            if not rs:
                continue
            n = len(rs)
            gross = sum(r.gross for r in rs)
            calls = [r.call for r in rs if r.call]
            out.append(f"| {run.label} | {kind} | {n} | {fmt_s(gross / n)} | {fmt_s(sum(r.prep for r in rs) / n, 2)} | {fmt_s(sum(r.model for r in rs) / n)} | "
                       f"{fmt_s(mean([c.ttft for c in calls]))} | {fmt_s(mean([c.decode for c in calls]))} | {fmt_s(sum(r.tools for r in rs) / n, 2)} | {fmt_s(sum(r.other for r in rs) / n, 3)} | "
                       f"{pct(sum(r.prep for r in rs), gross)} | {pct(sum(r.model for r in rs), gross)} | {pct(sum(r.tools for r in rs), gross)} | "
                       f"{fmt_n(mean([c.prompt_tokens for c in calls]))} | {fmt_n(mean([c.output_tokens for c in calls]))} |")
    return out


def to_json(runs: list[Run]) -> dict:
    def rnd(r: Round):
        return {"run": r.run, "group": list(r.group), "agent": r.agent, "kind": r.kind, "turn": r.turn, "index": r.index,
                "start_ms": r.start, "gross_ms": r.gross, "prep_ms": r.prep, "model_ms": r.model, "tools_ms": r.tools,
                "other_ms": r.other, "post_ms": r.post, "idle_before_ms": r.idle_before, "final": r.final,
                "decision": r.decision, "prep_parts": dict(r.prep_parts), "post_parts": dict(r.post_parts),
                "tools": [{"tool": t[0], "ms": t[1], "status": t[2]} for t in r.tool_calls],
                "call": None if not r.call else {"purpose": r.call.purpose, "duration_ms": r.call.duration, "attempts": r.call.attempts,
                                                  "prompt_tokens": r.call.prompt_tokens, "uncached_tokens": r.call.uncached_tokens,
                                                  "cached_tokens": r.call.cached_tokens, "output_tokens": r.call.output_tokens,
                                                  "ttft_ms": r.call.ttft, "decode_ms": r.call.decode, "stop_reason": r.call.stop_reason,
                                                  "first_block": (r.call.stream or {}).get("first_block_type")}}
    return {"runs": [{"label": run.label, "group": list(run.group), "status": run.status, "wall_ms": run.wall_ms,
                      "trace": str(run.path), "turns": [{k: (dict(v) if isinstance(v, Counter) else v) for k, v in t.items()} for t in run.turns],
                      "rounds": [rnd(r) for r in run.rounds], "segments": run.teammate_segments(), "score": run.score,
                      "redundancy": run.redundancy, "model_errors": dict(run.model_errors), "sidecar_mismatch": run.sidecar_mismatch,
                      "denials": run.denials} for run in runs]}


def collect(targets: list[str]) -> list[Path]:
    out = []
    for target in targets:
        p = Path(target)
        if p.is_dir():
            # only the trace itself: run_<stamp>_<id>.jsonl, never a sidecar (.inputs./.reads./.requests./.replay.)
            out.extend(sorted(q for q in p.glob("run_*.jsonl") if q.name.count(".") == 1))
        elif p.exists():
            out.append(p)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+")
    parser.add_argument("--json", default=None)
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--no-redundancy", action="store_true", help="skip the (slower) input_redundancy cross-teammate analysis")
    parser.add_argument("--per-run", action="store_true", help="also print the per-run round breakdown")
    parser.add_argument("--tools", action="store_true", help="also print the per-tool execution table")
    parser.add_argument("--sort", choices=["time", "label"], default="label")
    args = parser.parse_args(argv)
    paths = collect(args.targets)
    if not paths:
        print("no traces found", file=sys.stderr)
        return 2
    runs = [Run(p, Path(args.repo), redundancy=not args.no_redundancy) for p in paths]
    runs = [r for r in runs if r.calls]
    if args.sort == "label":
        runs.sort(key=lambda r: (r.group[1], r.group[0], r.rep, r.label))
    all_rounds = [r for run in runs for r in run.rounds]
    sections = []
    sections += runs_table(runs) + [""]
    sections += round_bucket_table(all_rounds, "Latency breakdown of one agentic round") + [""]
    sections += prep_detail_table(all_rounds) + [""]
    sections += token_table(runs) + [""]
    sections += decomposition_table(runs) + [""]
    sections += burst_table(runs) + [""]
    sections += aux_table(runs) + [""]
    sections += redundancy_table(runs) + [""]
    sections += task_table(runs) + [""]
    sections += wall_table(runs) + [""]
    if args.tools:
        sections += tool_table(all_rounds) + [""]
    if args.per_run:
        sections += per_run_rounds(runs) + [""]
    warnings = [f"{run.label}: sidecar/trace call-count mismatch {run.sidecar_mismatch}" for run in runs if run.sidecar_mismatch]
    if warnings:
        sections += ["Warnings:"] + [f"- {w}" for w in warnings]
    print("\n".join(sections))
    if args.json:
        Path(args.json).write_text(json.dumps(to_json(runs), indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
