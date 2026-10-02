#!/usr/bin/env python3
"""Every tool call in every harness trace of the repository (research/06_tool_cost Part C, C1).

    python3 research/06_tool_cost/tool_census.py [--out research/06_tool_cost/data/tool_census]
        [--workers 16] [--only SET[,SET]] [--include-new] [--limit N]

Part A priced tools with a probe and with the 24 traces of research/05; this reads all of them: the
~1,800 s15 traces under research/*/data/** (plain .jsonl and the .jsonl.xz of research/10 and 11) and
s15_integrated_harness/traces.  Skipped: sidecars (.inputs/.reads/.records/.requests), *scrape*, probe
traces (a probe_meta event: they dispatch tools without a model), and -- unless --include-new -- the new
Part C data leaves (tool_e2e, tool_sweep, tool_census).  A trace set is research/<topic>/data/<leaf>.

Per tool call (tool_start -> tool_end, paired by span id):
  span_ms     the harness-side span (PreToolUse hooks, handler, PostToolUse, trace writes)
  wait_ms     nested permission_wait spans (a human answering a prompt: 01_tracing, s15 samples)
  net_ms      span_ms - wait_ms: what the tool call cost the run
  handler_ms  the nested tool_execution span (call_tool_handler: the handler alone)
  model_ms    for task / Workflow: union of the model calls of the agents created inside the span
  flags       artifact classes: sleep (a command that waits on purpose), find_root (find /),
              timeout (>= 119 s, the 120 s bash cap); human_wait marks calls with wait_ms > 0
Per run: wall (profile_end.wall_seconds, else the trace span), think time (input_wait spans), tool time
all / lead / without task+Workflow, main-loop model time, and blocked-on-tools time -- the length of time
in which some tool span (task/Workflow excluded) is open and no main-loop model call (purpose lead,
teammate, one_shot, workflow_agent) is in flight, i.e. the tool time on the run's critical path.
Per request (Sutradhara's unit): a user-triggered lead turn plus the team/background/cron turns that
follow it until the next user turn; FTR ends at the first token of the request's final lead call
(the last one without tool_use): streamed first_visible_ms where the .inputs sidecar has it, else
bounded by the final call's request start (FTR lower bound -> share upper bound) and its response.

Outputs in --out: calls.jsonl.xz, runs.json, requests.json, census_tables.md, census_tables.json.
"""

from __future__ import annotations

import argparse
import json
import lzma
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "research" / "06_tool_cost" / "data" / "tool_census"
NEW_LEAVES = {"06_tool_cost/tool_e2e", "06_tool_cost/tool_sweep", "06_tool_cost/tool_census"}
SIDECAR = re.compile(r"\.(inputs|reads|records|requests)\.jsonl")
MAIN_PURPOSES = {"lead", "teammate", "one_shot", "workflow_agent"}
MODEL_TOOLS = {"task", "Workflow"}
SLEEP_CMD = re.compile(r"(^|[;&|(\s])sleep\s+\d|time\.sleep\(")
FIND_ROOT = re.compile(r"\bfind\s+/(\s|$)")


def open_text(path: Path):
    if path.suffix == ".xz":
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def discover(include_new: bool) -> list[tuple[str, Path]]:
    out = []
    for path in sorted((REPO / "research").glob("*/data/**/run_*.jsonl*")):
        name = path.name
        if SIDECAR.search(name) or "scrape" in name or not (name.endswith(".jsonl") or name.endswith(".jsonl.xz")):
            continue
        rel = path.relative_to(REPO / "research").parts
        set_name = f"{rel[0]}/{rel[2]}"
        if set_name in NEW_LEAVES and not include_new:
            continue
        out.append((set_name, path))
    for path in sorted((REPO / "s15_integrated_harness" / "traces").glob("run_*.jsonl")):
        if not SIDECAR.search(path.name):
            out.append(("s15/traces", path))
    return out


def host_class(cwd: str | None) -> str:
    cwd = cwd or ""
    if "yq335" in cwd:          # /home/yq335/... and its /tmp/claude-*-home-yq335-* scratchpad lanes
        return "old-host"
    if cwd.startswith("/home1/"):
        return "sample-host"    # the four s15_integrated_harness/traces samples
    if cwd.startswith("/tmp/"):
        return "cluster-local"
    if cwd.startswith("/mnt/home/"):
        return "cluster-nfs"
    return "other"


def model_family(model: str | None) -> str:
    m = (model or "").lower()
    for key, fam in (("glm", "glm-hosted"), ("deepseek", "deepseek-hosted"), ("qwen3.8-27b", "qwen3.8-27b-vllm"),
                     ("qwen3-32b", "qwen3-32b-vllm"), ("qwen3-8b", "qwen3-8b-vllm"), ("qwen", "qwen-vllm")):
        if key in m:
            return fam
    return m or "unknown"


def bash_class(command: str) -> str:
    c = (command or "").strip()
    if SLEEP_CMD.search(c):
        return "sleep"
    if "pytest" in c:
        return "pytest"
    head = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", c)
    head = re.sub(r"^(timeout\s+\S+\s+|env\s+(\w+=\S+\s+)*)", "", head)
    word = Path(head.split()[0]).name if head.split() else ""
    if word in {"python", "python3"}:
        return "python-test" if re.search(r"test_\w+\.py|unittest", head) else "python"
    if word in {"grep", "rg", "egrep"}:
        return "grep"
    if word == "find":
        return "find"
    if word in {"ls", "cat", "head", "tail", "wc", "sed", "awk", "tree", "echo", "pwd", "stat", "file", "du"}:
        return "view"
    if word in {"curl", "wget"}:
        return "network"
    if word == "git":
        return "git"
    return "other"


def union_len(intervals, lo=None, hi=None) -> float:
    total, cur_lo, cur_hi = 0.0, None, None
    for a, b in sorted(intervals):
        if lo is not None:
            a, b = max(a, lo), min(b, hi)
        if b <= a:
            continue
        if cur_hi is None or a > cur_hi:
            if cur_hi is not None:
                total += cur_hi - cur_lo
            cur_lo, cur_hi = a, b
        else:
            cur_hi = max(cur_hi, b)
    if cur_hi is not None:
        total += cur_hi - cur_lo
    return total


def blocked_len(tools, models, lo, hi) -> float:
    """Time in [lo, hi] covered by some interval of `tools` and by no interval of `models`."""
    events = []
    for a, b in tools:
        a, b = max(a, lo), min(b, hi)
        if b > a:
            events += [(a, 0, 1), (b, 0, -1)]
    if not events:
        return 0.0
    for a, b in models:
        a, b = max(a, lo), min(b, hi)
        if b > a:
            events += [(a, 1, 1), (b, 1, -1)]
    events.sort()
    n_tool = n_model = 0
    last = None
    total = 0.0
    for t, kind, delta in events:
        if last is not None and n_tool > 0 and n_model == 0:
            total += t - last
        if kind == 0:
            n_tool += delta
        else:
            n_model += delta
        last = t
    return total


def load_sidecar(trace: Path) -> list[dict]:
    stem = trace.name.removesuffix(".xz").removesuffix(".jsonl")
    for name in (f"{stem}.inputs.jsonl", f"{stem}.inputs.jsonl.xz"):
        side = trace.with_name(name)
        if side.exists():
            out = []
            with open_text(side) as handle:
                for line in handle:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("status") == "ok":
                        out.append(rec)
            return out
    return []


def parse(item) -> dict:
    set_name, path = item
    try:
        return _parse(set_name, path)
    except Exception as exc:  # a corrupt trace must not stop the census
        return {"set": set_name, "run": path.name, "error": f"{type(exc).__name__}: {exc}"}


def _parse(set_name: str, path: Path, allow_probe: bool = False) -> dict:
    records = []
    with open_text(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in r:
                records.append(r)
    records.sort(key=lambda r: (r.get("monotonic_ns") or 0))
    run_start, meta, end, probe = {}, {}, {}, None
    parents: dict[str, str | None] = {}
    tool_sids: set[str] = set()
    agent_parent: dict[str, str | None] = {}
    agent_created: dict[str, float] = {}
    agent_kind: dict[str, str] = {"agent-root": "lead"}
    open_spans: dict[str, dict] = {}
    tools, models, execs, waits, backgrounds, turns, input_waits, delays = [], [], {}, defaultdict(float), [], [], [], []
    for r in records:
        ev = r["event"]
        sid = r.get("span_id")
        if sid and (ev.endswith("_start") or ev == "model_request"):
            parents[sid] = r.get("parent_span_id")
            if ev == "tool_start":
                tool_sids.add(sid)
        if ev == "run_start":
            run_start = r.get("data") or {}
        elif ev == "profile_meta":
            meta = r.get("data") or {}
        elif ev == "profile_end":
            end = r.get("data") or {}
        elif ev == "probe_meta":
            probe = r.get("data") or {}
        elif ev == "agent_create":
            aid = r.get("agent_id")
            agent_parent[aid] = r.get("parent_agent_id")
            agent_created[aid] = r["elapsed_ms"]
            agent_kind[aid] = r.get("agent_kind") or "teammate"
        elif ev == "tool_delay":
            delays.append((r.get("agent_id"), (r.get("data") or {}).get("delay_s")))
        if ev in ("tool_start", "tool_execution_start", "permission_wait_start", "background_start",
                  "agent_active_start", "input_wait_start", "model_request") and sid:
            open_spans[sid] = r
            continue
        if ev in ("tool_end", "tool_execution_end", "permission_wait_end", "background_end",
                  "agent_active_end", "input_wait_end", "model_response", "model_error") and sid in open_spans:
            s = open_spans.pop(sid)
            t0, t1 = s["elapsed_ms"], r["elapsed_ms"]
            d0, d1 = s.get("data") or {}, r.get("data") or {}
            if ev == "tool_end":
                args = d0.get("arguments") if isinstance(d0.get("arguments"), dict) else {}
                result = d1.get("result") if isinstance(d1.get("result"), dict) else {}
                tools.append({"sid": sid, "agent": s.get("agent_id") or "agent-root",
                              "kind": s.get("agent_kind") or agent_kind.get(s.get("agent_id") or "agent-root", "lead"),
                              "thread": ((s.get("thread") or {}).get("name") or ""), "tool": d1.get("tool") or d0.get("tool"),
                              "status": d1.get("status"), "t0": t0, "t1": t1,
                              "command": args.get("command") if isinstance(args.get("command"), str) else None,
                              "path": args.get("path") if isinstance(args.get("path"), str) else None,
                              "out_chars": result.get("characters"), "special": d1.get("special")})
            elif ev == "tool_execution_end":
                execs[s.get("parent_span_id")] = t1 - t0
            elif ev == "permission_wait_end":
                p = s.get("parent_span_id")
                while p is not None and p not in tool_sids:   # attribute to the enclosing tool span
                    p = parents.get(p)
                if p is not None:
                    waits[p] += t1 - t0
            elif ev == "background_end":
                backgrounds.append({"agent": s.get("agent_id"), "t0": t0, "t1": t1,
                                    "command": (d0.get("arguments") or {}).get("command") if isinstance(d0.get("arguments"), dict) else None})
            elif ev == "agent_active_end":
                if (s.get("agent_id") or "agent-root") == "agent-root":
                    turns.append({"t0": t0, "t1": t1, "reason": str(d0.get("reason") or "")})
            elif ev == "input_wait_end":
                input_waits.append((t0, t1))
            else:  # model call
                purpose = d1.get("purpose") or d0.get("purpose") or "unspecified"
                actions = d1.get("requested_actions") or []
                models.append({"agent": s.get("agent_id") or "agent-root", "purpose": purpose, "t0": t0, "t1": t1,
                               "ok": ev == "model_response",
                               "tool_use": any(a.get("type") == "tool_use" for a in actions if isinstance(a, dict))})
    if probe is not None and not allow_probe:
        return {"set": set_name, "run": path.name, "probe": True}
    if probe is not None:
        meta = {**probe, "driver": probe.get("driver")}
    label = meta.get("label") or path.name
    model = run_start.get("model") or meta.get("model")
    host = host_class(run_start.get("cwd"))
    t_first = records[0]["elapsed_ms"] if records else 0.0
    t_last = records[-1]["elapsed_ms"] if records else 0.0
    wall_ms = (end.get("wall_seconds") or 0) * 1000 or (t_last - t_first)
    main_models = [(m["t0"], m["t1"]) for m in models if m["purpose"] in MAIN_PURPOSES]

    # descendants of an agent created inside a span (task subagent, workflow orchestrator -> agents)
    children = defaultdict(list)
    for aid, parent in agent_parent.items():
        children[parent].append(aid)

    def descendants(agent, lo, hi):
        out, stack = set(), [c for c in children.get(agent, []) if lo <= agent_created.get(c, -1) <= hi]
        while stack:
            a = stack.pop()
            if a in out:
                continue
            out.add(a)
            stack.extend(children.get(a, []))
        return out

    calls = []
    for t in tools:
        span = t["t1"] - t["t0"]
        wait = waits.get(t["sid"], 0.0)
        flags = []
        if t["tool"] == "bash" and t["command"]:
            if SLEEP_CMD.search(t["command"]):
                flags.append("sleep")
            if FIND_ROOT.search(t["command"]):
                flags.append("find_root")
        if span >= 119_000 and t["tool"] == "bash":
            flags.append("timeout")
        nested_model = nested_tools = None
        if t["tool"] in MODEL_TOOLS:
            desc = descendants(t["agent"], t["t0"], t["t1"])
            nested_model = union_len([(m["t0"], m["t1"]) for m in models if m["agent"] in desc], t["t0"], t["t1"])
            nested_tools = sum(u["t1"] - u["t0"] - waits.get(u["sid"], 0.0) for u in tools
                               if u["agent"] in desc and t["t0"] <= u["t0"] and u["t1"] <= t["t1"])
        calls.append({"set": set_name, "run": path.name, "label": label, "model": model_family(model), "host": host,
                      "kind": t["kind"], "thread": t["thread"], "tool": t["tool"], "status": t["status"],
                      "span_ms": round(span, 3), "wait_ms": round(wait, 3), "net_ms": round(span - wait, 3),
                      "handler_ms": None if t["sid"] not in execs else round(execs[t["sid"]], 3),
                      "model_ms": None if nested_model is None else round(nested_model, 3),
                      "nested_tool_ms": None if nested_tools is None else round(nested_tools, 3),
                      "bash_class": bash_class(t["command"]) if t["tool"] == "bash" else None,
                      "human_wait": wait > 0,
                      "out_chars": t["out_chars"], "flags": flags, "t0": t["t0"], "t1": t["t1"]})

    def tool_iv(pred):
        return [(c["t0"], c["t0"] + c["net_ms"]) for c in calls if pred(c)]

    plain = lambda c: c["tool"] not in MODEL_TOOLS                             # noqa: E731
    clean = lambda c: plain(c) and not c["flags"]                              # noqa: E731
    think_ms = sum(b - a for a, b in input_waits)
    run = {"set": set_name, "run": path.name, "label": label, "model": model_family(model), "model_id": model,
           "host": host, "status": end.get("status") or ("interactive" if not meta else "?"),
           "driver": meta.get("driver"), "stream": bool(meta.get("stream")), "trace_output": meta.get("trace_output") or run_start.get("output_mode"),
           "tool_delay": meta.get("tool_delay"), "wall_ms": round(wall_ms, 1), "think_ms": round(think_ms, 1),
           "n_calls": len(calls), "n_model_calls": len(models), "n_main_calls": len(main_models),
           "tool_ms": round(sum(c["net_ms"] for c in calls), 1),
           "tool_ms_plain": round(sum(c["net_ms"] for c in calls if plain(c)), 1),
           "tool_ms_clean": round(sum(c["net_ms"] for c in calls if clean(c)), 1),
           "tool_ms_lead": round(sum(c["net_ms"] for c in calls if c["kind"] == "lead"), 1),
           "wait_ms": round(sum(c["wait_ms"] for c in calls), 1),
           "model_ms_main": round(sum(b - a for a, b in main_models), 1),
           "blocked_ms": round(blocked_len(tool_iv(plain), main_models, t_first, t_last), 1),
           "blocked_ms_clean": round(blocked_len(tool_iv(clean), main_models, t_first, t_last), 1),
           "background_ms": round(sum(b["t1"] - b["t0"] for b in backgrounds), 1),
           "delay_s": round(sum(d or 0 for _, d in delays), 3), "n_delays": len(delays),
           "smoke": bool(re.search(r"smoke|partial", f"{set_name} {label}", re.I))}

    # requests: user turn + the lead's non-user turns until the next user turn
    requests = []
    side = None
    lead_calls = [m for m in models if m["agent"] == "agent-root" and m["purpose"] == "lead" and m["ok"]]
    for t in sorted(turns, key=lambda x: x["t0"]):
        if "user" in t["reason"] or not requests:
            requests.append({"t0": t["t0"], "t1": t["t1"], "turns": 1, "trigger": t["reason"]})
        else:
            requests[-1]["t1"] = max(requests[-1]["t1"], t["t1"])
            requests[-1]["turns"] += 1
    if run["stream"]:
        side = [s for s in load_sidecar(path) if (s.get("agent_id") or "agent-root") == "agent-root"]
        all_lead = [m for m in models if m["agent"] == "agent-root" and m["ok"]]
        side_by_call = {}
        if side and len(side) == len(all_lead):
            for m, s in zip(all_lead, side):
                side_by_call[id(m)] = s
    out_requests = []
    for i, q in enumerate(requests):
        finals = [m for m in lead_calls if q["t0"] <= m["t0"] <= q["t1"] and not m["tool_use"]]
        if not finals:
            continue
        final = finals[-1]
        stream = ((side_by_call.get(id(final)) or {}).get("stream") or {}) if side is not None else {}
        first_visible = stream.get("first_visible_ms")
        ftr_stream = final["t0"] + first_visible if first_visible is not None else None
        rec = {"set": set_name, "run": path.name, "label": label, "model": run["model"], "host": host, "index": i,
               "smoke": run["smoke"],
               "turns": q["turns"], "t0": q["t0"], "turn_ms": q["t1"] - q["t0"],
               "ftr_req_ms": final["t0"] - q["t0"], "ftr_end_ms": final["t1"] - q["t0"],
               "ftr_stream_ms": None if ftr_stream is None else ftr_stream - q["t0"],
               "lead_calls": sum(1 for m in lead_calls if q["t0"] <= m["t0"] <= q["t1"])}
        for key, hi in (("turn", q["t1"]), ("ftr_req", final["t0"]), ("ftr_end", final["t1"]), ("ftr_stream", ftr_stream)):
            if hi is None or hi <= q["t0"]:
                rec[f"tools_{key}_ms"] = rec[f"tools_all_{key}_ms"] = rec[f"lead_tools_{key}_ms"] = None
                continue
            rec[f"tools_{key}_ms"] = round(blocked_len(tool_iv(plain), main_models, q["t0"], hi), 3)
            rec[f"tools_all_{key}_ms"] = round(blocked_len(tool_iv(lambda c: True), [], q["t0"], hi), 3)
            rec[f"lead_tools_{key}_ms"] = round(union_len(tool_iv(lambda c: c["kind"] == "lead" and plain(c)), q["t0"], hi), 3)
        out_requests.append(rec)
    return {"set": set_name, "run": path.name, "calls": calls, "summary": run, "requests": out_requests,
            "models": [{"agent": m["agent"], "purpose": m["purpose"], "ms": m["t1"] - m["t0"], "ok": m["ok"],
                        "t0": m["t0"]} for m in models],
            "backgrounds": [{"set": set_name, "run": path.name, "ms": b["t1"] - b["t0"],
                             "bash_class": bash_class(b["command"] or "")} for b in backgrounds]}


# ----------------------------------------------------------------------------- tables
def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": statistics.fmean(xs), "median": q(xs, 0.5), "p90": q(xs, 0.9),
            "p99": q(xs, 0.99), "max": max(xs), "sum": sum(xs)}


def f(x, d=1):
    return "-" if x is None else f"{x:,.{d}f}"


def pc(x, d=2):
    return "-" if x is None else f"{100 * x:.{d}f}%"


def tables(calls, runs, requests, backgrounds) -> tuple[str, dict]:
    md, js = [], {}
    md.append("# Tool-call census of every harness trace in the repository\n")
    md.append("Generated by `research/06_tool_cost/tool_census.py`. Times in ms unless marked; `net` = tool span minus "
              "human permission waits; *plain* = without `task`/`Workflow` (whose spans contain model calls); *clean* = "
              "plain and without flagged artifacts (sleep commands, `find /`, 120 s timeouts). Human permission waits are "
              "subtracted from every call (net), not flagged.\n")
    # T1 sets
    by_set = defaultdict(list)
    for r in runs:
        by_set[r["set"]].append(r)
    md.append("## C1.1 Run sets: tool share of wall time\n")
    md.append("| set | runs | model | host | wall h | think h | tool s | tool/wall | plain/wall | clean/wall | blocked/wall | blocked/(wall-think) | task+Workflow s |")
    md.append("|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    js["sets"] = {}
    tot = Counter()
    for s in sorted(by_set):
        rs = by_set[s]
        wall = sum(r["wall_ms"] for r in rs)
        think = sum(r["think_ms"] for r in rs)
        row = {k: sum(r[k] for r in rs) for k in ("tool_ms", "tool_ms_plain", "tool_ms_clean", "blocked_ms", "blocked_ms_clean")}
        models = Counter(r["model"] for r in rs).most_common(2)
        hosts = Counter(r["host"] for r in rs).most_common(1)
        md.append(f"| {s} | {len(rs)} | {', '.join(m for m, _ in models)} | {hosts[0][0]} | {wall / 3.6e6:.2f} | {think / 3.6e6:.2f} | "
                  f"{row['tool_ms'] / 1000:,.1f} | {pc(row['tool_ms'] / wall if wall else None)} | "
                  f"{pc(row['tool_ms_plain'] / wall if wall else None)} | {pc(row['tool_ms_clean'] / wall if wall else None)} | "
                  f"{pc(row['blocked_ms'] / wall if wall else None)} | {pc(row['blocked_ms'] / (wall - think) if wall - think > 0 else None)} | "
                  f"{(row['tool_ms'] - row['tool_ms_plain']) / 1000:,.1f} |")
        js["sets"][s] = {"runs": len(rs), "wall_ms": wall, "think_ms": think, **row,
                         "models": dict(Counter(r["model"] for r in rs)), "hosts": dict(Counter(r["host"] for r in rs))}
        real = [r for r in rs if not r["smoke"]]
        for k in ("tool_ms", "tool_ms_plain", "tool_ms_clean", "blocked_ms"):
            tot[k] += sum(r[k] for r in real)
        tot["wall_ms"] += sum(r["wall_ms"] for r in real)
        tot["think_ms"] += sum(r["think_ms"] for r in real)
        tot["runs"] += len(real)
    md.append(f"| **all (smoke runs excluded)** | {tot['runs']} | | | {tot['wall_ms'] / 3.6e6:.2f} | {tot['think_ms'] / 3.6e6:.2f} | {tot['tool_ms'] / 1000:,.1f} | "
              f"{pc(tot['tool_ms'] / tot['wall_ms'])} | {pc(tot['tool_ms_plain'] / tot['wall_ms'])} | {pc(tot['tool_ms_clean'] / tot['wall_ms'])} | "
              f"{pc(tot['blocked_ms'] / tot['wall_ms'])} | {pc(tot['blocked_ms'] / (tot['wall_ms'] - tot['think_ms']))} | "
              f"{(tot['tool_ms'] - tot['tool_ms_plain']) / 1000:,.1f} |\n")
    js["all"] = dict(tot)
    # per-run share distribution
    shares = [r["blocked_ms"] / r["wall_ms"] for r in runs if r["wall_ms"] > 0 and not r["smoke"]]
    md.append(f"Per-run blocked-on-tools share over {len(shares)} runs: median {pc(q(shares, 0.5))}, p90 {pc(q(shares, 0.9))}, "
              f"p99 {pc(q(shares, 0.99))}, max {pc(max(shares) if shares else None)}.\n")
    js["run_share"] = {"median": q(shares, 0.5), "p90": q(shares, 0.9), "p99": q(shares, 0.99), "max": max(shares) if shares else None}

    # T2 per tool x role
    md.append("## C1.2 Per tool and agent kind (net ms, all statuses)\n")
    md.append("| tool | kind | n | mean | median | p90 | p99 | max | handler mean | total s | flagged |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    groups = defaultdict(list)
    for c in calls:
        groups[(c["tool"], c["kind"])].append(c)
    js["tools"] = {}
    for (tool, kind), cs in sorted(groups.items(), key=lambda kv: -sum(c["net_ms"] for c in kv[1])):
        st = stats([c["net_ms"] for c in cs])
        hd = stats([c["handler_ms"] for c in cs])
        flagged = sum(1 for c in cs if c["flags"])
        md.append(f"| {tool} | {kind} | {st['n']:,} | {f(st['mean'])} | {f(st['median'])} | {f(st['p90'])} | {f(st['p99'])} | "
                  f"{f(st['max'])} | {f(hd.get('mean'))} | {st['sum'] / 1000:,.1f} | {flagged} |")
        js["tools"][f"{tool}|{kind}"] = {"net": st, "handler": hd, "flagged": flagged}
    md.append("")
    # T3 bash classes
    md.append("## C1.3 bash by command class (net ms)\n")
    md.append("| class | n | mean | median | p90 | p99 | max | total s |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    js["bash"] = {}
    bc = defaultdict(list)
    for c in calls:
        if c["tool"] == "bash":
            bc[c["bash_class"]].append(c["net_ms"])
    for k, xs in sorted(bc.items(), key=lambda kv: -sum(kv[1])):
        st = stats(xs)
        md.append(f"| {k} | {st['n']:,} | {f(st['mean'])} | {f(st['median'])} | {f(st['p90'])} | {f(st['p99'])} | {f(st['max'])} | {st['sum'] / 1000:,.1f} |")
        js["bash"][k] = st
    md.append("")
    # T4 average tool call
    md.append("## C1.4 The average tool call (call-weighted)\n")
    md.append("| scope | n | mean ms | median ms | p90 ms | p99 ms |")
    md.append("|---|---:|---:|---:|---:|---:|")
    js["average"] = {}
    scopes = [("all calls", lambda c: True), ("plain (no task/Workflow)", lambda c: c["tool"] not in MODEL_TOOLS),
              ("clean (plain, no artifacts)", lambda c: c["tool"] not in MODEL_TOOLS and not c["flags"]),
              ("clean, status ok", lambda c: c["tool"] not in MODEL_TOOLS and not c["flags"] and c["status"] == "ok")]
    for name, pred in scopes:
        st = stats([c["net_ms"] for c in calls if pred(c)])
        md.append(f"| {name} | {st['n']:,} | {f(st.get('mean'))} | {f(st.get('median'))} | {f(st.get('p90'))} | {f(st.get('p99'))} |")
        js["average"][name] = st
    by_model = defaultdict(list)
    for c in calls:
        if c["tool"] not in MODEL_TOOLS and not c["flags"]:
            by_model[(c["model"], c["host"])].append(c["net_ms"])
    for (m, h), xs in sorted(by_model.items()):
        st = stats(xs)
        md.append(f"| clean, {m} @ {h} | {st['n']:,} | {f(st['mean'])} | {f(st['median'])} | {f(st['p90'])} | {f(st['p99'])} |")
        js["average"][f"clean|{m}|{h}"] = st
    md.append("")
    # T5 model-bearing tools
    md.append("## C1.5 task / Workflow spans: model time inside\n")
    md.append("| tool | n | mean span s | mean nested model s | model share of span |")
    md.append("|---|---:|---:|---:|---:|")
    js["model_tools"] = {}
    for tool in sorted(MODEL_TOOLS):
        cs = [c for c in calls if c["tool"] == tool]
        if not cs:
            continue
        span = sum(c["net_ms"] for c in cs)
        model = sum(c["model_ms"] or 0 for c in cs)
        md.append(f"| {tool} | {len(cs)} | {span / len(cs) / 1000:,.1f} | {model / len(cs) / 1000:,.1f} | {pc(model / span if span else None)} |")
        js["model_tools"][tool] = {"n": len(cs), "span_ms": span, "model_ms": model}
    md.append("")
    # T6 requests
    md.append("## C1.6 Per request (Sutradhara's unit): tool share of turn latency and of FTR\n")
    md.append("Blocked-on-tools time inside the request window (plain tools, main-loop model calls mask), over the window up to: "
              "the end of the request (turn), the final call's request start (FTR lower bound, so an upper bound on the share), "
              "its first visible token (streamed runs only), and its response.\n")
    md.append("| set | requests | median turn s | turn share p50 / p90 / p99 | FTR-bound share p50 / p90 / p99 | streamed FTR share p50 / p90 / p99 (n) |")
    md.append("|---|---:|---:|---|---|---|")
    js["requests"] = {}
    by_req = defaultdict(list)
    for r in requests:
        by_req[r["set"]].append(r)

    def share(rs, key, span):
        return [r[f"tools_{key}_ms"] / r[span] for r in rs if r.get(f"tools_{key}_ms") is not None and r.get(span)]

    def three(xs):
        return " / ".join(pc(q(xs, p), 1) for p in (0.5, 0.9, 0.99)) if xs else "-"

    for s in sorted(by_req) + ["all"]:
        rs = [r for r in requests if not r["smoke"]] if s == "all" else by_req[s]
        turn = share(rs, "turn", "turn_ms")
        ftr = share(rs, "ftr_req", "ftr_req_ms")
        strm = share(rs, "ftr_stream", "ftr_stream_ms")
        md.append(f"| {'**all (no smoke)**' if s == 'all' else s} | {len(rs):,} | {f(q([r['turn_ms'] for r in rs], 0.5) / 1000 if rs else None)} | "
                  f"{three(turn)} | {three(ftr)} | {three(strm)} ({len(strm)}) |")
        js["requests"][s] = {"n": len(rs), "turn": {p: q(turn, p) for p in (0.5, 0.9, 0.99)},
                             "ftr_bound": {p: q(ftr, p) for p in (0.5, 0.9, 0.99)},
                             "ftr_stream": {p: q(strm, p) for p in (0.5, 0.9, 0.99)}, "n_stream": len(strm)}
    md.append("")
    # CDF points (pooled)
    ftr_all = sorted(share([r for r in requests if not r["smoke"]], "ftr_req", "ftr_req_ms"))
    js["request_cdf_ftr_bound"] = [q(ftr_all, p / 100) for p in range(0, 101)]
    # background jobs
    if backgrounds:
        st = stats([b["ms"] for b in backgrounds])
        md.append(f"Background bash jobs (off the tool span, overlap with model calls): n {st['n']}, mean {f(st['mean'])} ms, "
                  f"p90 {f(st['p90'])} ms, max {f(st['max'])} ms.\n")
        js["background"] = st
    return "\n".join(md) + "\n", js


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--only", default=None, help="comma-separated set names (research/<topic>/data/<leaf> as topic/leaf)")
    parser.add_argument("--include-new", action="store_true", help="also read the new Part C leaves (tool_e2e, ...)")
    parser.add_argument("--limit", type=int, default=None, help="at most N traces (smoke tests)")
    args = parser.parse_args()
    items = discover(args.include_new)
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i[0] in wanted]
    if args.limit:
        items = items[:args.limit]
    print(f"[census] {len(items)} traces in {len({s for s, _ in items})} sets", flush=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    calls, runs, requests, backgrounds, errors, probes = [], [], [], [], [], 0
    with Pool(args.workers) as pool:
        for i, res in enumerate(pool.imap_unordered(parse, items, chunksize=4), 1):
            if res.get("error"):
                errors.append(res)
            elif res.get("probe"):
                probes += 1
            else:
                calls += res["calls"]
                runs.append(res["summary"])
                requests += res["requests"]
                backgrounds += res["backgrounds"]
            if i % 200 == 0:
                print(f"[census] {i}/{len(items)}", flush=True)
    calls.sort(key=lambda c: (c["set"], c["run"], c["t0"]))
    runs.sort(key=lambda r: (r["set"], r["run"]))
    requests.sort(key=lambda r: (r["set"], r["run"], r["index"]))
    with lzma.open(out / "calls.jsonl.xz", "wt", encoding="utf-8") as handle:
        for c in calls:
            handle.write(json.dumps(c) + "\n")
    (out / "runs.json").write_text(json.dumps(runs, indent=0), encoding="utf-8")
    (out / "requests.json").write_text(json.dumps(requests, indent=0), encoding="utf-8")
    md, js = tables(calls, runs, requests, backgrounds)
    js["inputs"] = {"traces": len(items), "runs": len(runs), "probe_traces_skipped": probes, "errors": errors,
                    "calls": len(calls), "requests": len(requests)}
    md += (f"\nInputs: {len(items)} traces, {len(runs)} runs, {probes} probe traces skipped, {len(errors)} unreadable, "
           f"{len(calls):,} tool calls, {len(requests):,} requests.\n")
    (out / "census_tables.md").write_text(md, encoding="utf-8")
    (out / "census_tables.json").write_text(json.dumps(js, indent=1, default=str), encoding="utf-8")
    print(md[:3000])
    print(f"[census] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
