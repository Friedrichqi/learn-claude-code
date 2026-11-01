#!/usr/bin/env python3
"""The rounds table of research/20_round_dependency: every MiniMax-M2.7 lead call of topic 19's code sessions and
topic 16's SWE-bench / Terminal-Bench trials, one row per round.

Round j of a turn is one lead call that answered, plus the work it started:
  - the response: its text (the <think> reasoning and any visible text) and its tool calls c_j;
  - the results O_j of those calls, read from the next lead request of the same turn, which carries them in full;
  - the tool seconds (the s15 trace's tool_end durations) and the call's usage (prompt, cached, output tokens).
Flags:
  - final: the response has no tool call (the turn's answer);
  - append_ok: the next lead request is this request plus this response plus appended messages, i.e. the harness
    did not rewrite the history in between (micro_compact placeholders, compaction, snip, reactive compaction);
  - tail_ok: the next lead request still holds this request's last TAIL messages and this response in a row, i.e.
    any rewrite touched only older content (micro_compact, the rolling snip), not the recent rounds (compaction);
  - results_src: "next" (full text from the next request), "trace" (the trace's 500-character preview, because the
    history was rewritten first) or "none" (final round, or no later request);
  - reissued: a max_tokens response the harness discarded and re-sent with a larger budget (not a round).
Rows point at their request (file + call index) instead of copying it: a request carries the whole history.

Sources (MiniMax-M2.7 only):
  t19  research/19_efficiency_signals/data/cells/<cell>/runs/<label>/: <label>.requests.jsonl.xz (every call's
       request and response, mu_session.py), run_*.jsonl.xz (the s15 trace), <label>.meta.json. Cells whose prompts
       carry no efficiency text (none, sgate: the gate acts on the server only), pilot and main runs. The smoke
       cells ran a 32k window that compacts far more and are left out.
  t16  research/16_epic_harness/harness_eval/jobs/<job>/<bench>/<phase>__<c>/<trial>/agent/s15/: calls.jsonl
       (adapter/s15_child.py; tool schemas as names only), traces/run_*.jsonl, events.jsonl (snips). Jobs: the
       profile (minimax-m2.7) and the two EPIC setups that kept the attention cache exact (prefix, prefix_aa).

    python research/20_round_dependency/rd_rounds.py build [--src t19,t16] [--workers 48]
    python research/20_round_dependency/rd_rounds.py show
"""
from __future__ import annotations

import argparse
import ast
import json
import lzma
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from multiprocessing import get_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
DATA = DIR / "data"
ROUNDS = DATA / "rounds"
T19_CELLS_ROOT = REPO / "research" / "19_efficiency_signals" / "data" / "cells"
T19_CELLS = ["w08_none", "w16_none", "w32_none", "p08_none", "p16_none", "w16_sgate", "w32_sgate", "p16_sgate"]
T19_TEMPLATE = REPO / "research" / "19_efficiency_signals" / "minimax_m2_append_only.jinja"
HE = REPO / "research" / "16_epic_harness" / "harness_eval"
T16_JOBS = ["minimax-m2.7", "minimax-m2.7-epic+prefix", "minimax-m2.7-epic+prefix_aa"]
T16_BENCHES = ["swe", "swepro", "tbench"]
T16_TEMPLATE = HE / "epic" / "templates" / "minimax_m2_append_only.jinja"
TAIL = 2                         # messages at the end of a request that must survive into the next one (tail_ok)


# -- reading ------------------------------------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass                                     # a killed session's half-written last line
    return out


def write_jsonl_xz(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with lzma.open(tmp, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    tmp.replace(path)
    return n


def block_text(content) -> str:
    """The text of a message's or tool result's content (a string or a list of blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                parts.append(block_text(b.get("content")))
        elif isinstance(b, str):
            parts.append(b)
    return "\n".join(parts)


def norm_block(b: dict) -> tuple:
    """A content block reduced to what the model sees (the SDK adds fields such as citations=None)."""
    t = b.get("type")
    if t == "text":
        return ("text", b.get("text") or "")
    if t == "tool_use":
        return ("tool_use", b.get("id"), b.get("name"), json.dumps(b.get("input"), sort_keys=True))
    if t == "tool_result":
        return ("tool_result", b.get("tool_use_id"), block_text(b.get("content")))
    return (t, json.dumps(b, sort_keys=True))


def norm_message(m: dict) -> tuple:
    c = m.get("content")
    if isinstance(c, str):
        return (m.get("role"), (("text", c),))
    return (m.get("role"), tuple(norm_block(b) for b in c or [] if isinstance(b, dict)))


def tool_uses(blocks) -> list[dict]:
    return [{"id": b.get("id"), "name": b.get("name"), "input": b.get("input") or {}}
            for b in blocks or [] if isinstance(b, dict) and b.get("type") == "tool_use"]


def text_of(blocks) -> str:
    return "\n".join(b.get("text") or "" for b in blocks or [] if isinstance(b, dict) and b.get("type") == "text")


def results_in(messages: list[dict], ids: set[str]) -> dict[str, str]:
    out = {}
    for m in messages:
        if m.get("role") != "user" or isinstance(m.get("content"), str):
            continue
        for b in m.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in ids:
                out[b["tool_use_id"]] = block_text(b.get("content"))
    return out


def trace_tools(events: list[dict]) -> dict[str, dict]:
    """tool_call_id -> {ms, status, preview} from the s15 trace's tool_end events."""
    out = {}
    for e in events:
        if e.get("event") != "tool_end":
            continue
        d = e.get("data") or {}
        cid = d.get("tool_call_id")
        if not cid:
            continue
        res = d.get("result") or {}
        try:
            ms = float(d.get("duration_ms"))
        except (TypeError, ValueError):
            ms = None
        out[cid] = {"ms": ms, "status": d.get("status"),
                    "preview": res.get("preview") if isinstance(res, dict) else None,
                    "chars": res.get("characters") if isinstance(res, dict) else None}
    return out


def usage_of(u: dict | None) -> dict:
    u = u or {}
    cached = int(u.get("cache_read_input_tokens") or 0)
    np_ = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
    return {"np": np_, "cached": cached, "out": int(u.get("output_tokens") or 0)}


# -- one chain of lead calls -> rounds ------------------------------------------------------------------------------
def chain_rounds(calls: list[dict], tools_ms: dict[str, dict], base: dict) -> list[dict]:
    """calls: the session's lead calls in order, each {call, turn, messages, blocks, usage, stop, t0, t1, ptr}."""
    rows = []
    # a max_tokens response that the harness re-sent with the same messages is discarded, not a round
    keep = []
    for i, c in enumerate(calls):
        nxt = calls[i + 1] if i + 1 < len(calls) else None
        if (c["stop"] == "max_tokens" and nxt is not None and nxt["turn"] == c["turn"]
                and len(nxt["messages"]) == len(c["messages"])
                and [norm_message(m) for m in nxt["messages"]] == [norm_message(m) for m in c["messages"]]):
            continue
        keep.append(c)
    j_in_turn = Counter()
    for i, c in enumerate(keep):
        nxt = keep[i + 1] if i + 1 < len(keep) and keep[i + 1]["turn"] == c["turn"] else None
        uses = tool_uses(c["blocks"])
        ids = {u["id"] for u in uses}
        results, src = {}, "none"
        append_ok = tail_ok = None
        if nxt is not None:
            a, b = c["messages"], nxt["messages"]
            want = [norm_message(m) for m in a] + [norm_message({"role": "assistant", "content": c["blocks"]})]
            nb = [norm_message(m) for m in b]
            append_ok = nb[:len(want)] == want
            # rewrites of old content (micro_compact placeholders, the rolling snip) keep the recent tail; a
            # compaction or a rewrite of the newest results does not
            tail = want[-(TAIL + 1):]
            tail_ok = append_ok or any(nb[p:p + len(tail)] == tail for p in range(len(nb) - len(tail) + 1))
            if uses:
                results = results_in(b, ids)
                src = "next" if len(results) == len(ids) else src
        if uses and src != "next":
            pv = {i_: (tools_ms.get(i_) or {}).get("preview") for i_ in ids}
            if all(v is not None for v in pv.values()):
                results, src = {k: v for k, v in pv.items()}, "trace"
        j = j_in_turn[c["turn"]]
        j_in_turn[c["turn"]] += 1
        rows.append({**base, "turn": c["turn"], "j": j, "call": c["call"], "ptr": c["ptr"],
                     "text": text_of(c["blocks"]), "calls": uses,
                     "results": [results.get(u["id"]) for u in uses], "results_src": src if uses else "none",
                     "tool_ms": [(tools_ms.get(u["id"]) or {}).get("ms") for u in uses],
                     "tool_status": [(tools_ms.get(u["id"]) or {}).get("status") for u in uses],
                     "final": not uses, "stop": c["stop"], "append_ok": append_ok, "tail_ok": tail_ok,
                     "t0": c["t0"], "t1": c["t1"], **c["usage"]})
    # the round count of each turn, so readers can tell the last round
    n_turn = Counter(r["turn"] for r in rows)
    for r in rows:
        r["n_turn"] = n_turn[r["turn"]]
    return rows


# -- t19: topic 19's sessions -------------------------------------------------------------------------------------
def t19_sessions() -> list[Path]:
    out = []
    for cell in T19_CELLS:
        out += sorted((T19_CELLS_ROOT / cell / "runs").glob("*/*.meta.json"))
    return out


def t19_one(meta_path: str) -> tuple[dict, list[dict]]:
    meta_path = Path(meta_path).resolve()
    meta = json.loads(meta_path.read_text())
    run = meta_path.parent
    label = meta["label"]
    req_file = run / f"{label}.requests.jsonl.xz"
    recs = read_jsonl(req_file)
    traces = [p for p in run.glob("run_*.jsonl.xz") if ".inputs." not in p.name]
    events = read_jsonl(traces[0]) if traces else []
    turn_ends = {e.get("turn_id"): (e.get("data") or {}).get("status") for e in events if e.get("event") == "turn_end"}
    stops = {e.get("turn_id"): (e.get("data") or {}).get("reason") for e in events
             if e.get("event") == "harness_decision" and (e.get("data") or {}).get("decision") == "stop"}
    templates = meta.get("templates") or []
    rel = str(req_file.relative_to(REPO))
    calls, compactions = [], Counter()
    for r in recs:
        if r.get("purpose") != "lead":
            if r.get("purpose") == "compaction_summary":
                compactions[r.get("turn_id")] += 1
            continue
        resp = r.get("response")
        if not resp:
            continue
        calls.append({"call": r["call_index"], "turn": r.get("turn_id"), "messages": r.get("messages") or [],
                      "blocks": resp.get("content") or [], "usage": usage_of(resp.get("usage")),
                      "stop": resp.get("stop_reason"), "t0": r.get("t_send"), "t1": r.get("t_recv"),
                      "ptr": {"file": rel, "call": r["call_index"]}})
    cell = meta.get("cond")
    sid = f"t19/{cell}/{label}"
    turns = sorted({c["turn"] for c in calls})
    tmpl = {t: (templates[int(t.split("_")[-1]) - 1] if t and t.split("_")[-1].isdigit()
                and int(t.split("_")[-1]) <= len(templates) else None) for t in turns}
    base = {"src": "t19", "sid": sid, "group": cell}
    rows = chain_rounds(calls, trace_tools(events), base)
    for row in rows:
        row["kind"] = tmpl.get(row["turn"])
    prompts = {}
    for c in calls:                                       # the user message that opened each turn
        if c["turn"] not in prompts:
            users = [m for m in c["messages"] if m.get("role") == "user" and isinstance(m.get("content"), str)
                     and not m["content"].startswith("<reminder>")]
            prompts[c["turn"]] = users[-1]["content"] if users else None
    sess = {"src": "t19", "sid": sid, "group": cell, "label": label, "spec": meta.get("spec"),
            "filler": bool(meta.get("filler")), "status": meta.get("status"), "codebase": meta.get("codebase"),
            "templates": templates, "seed": meta.get("seed"), "sandbox": meta.get("sandbox"),
            "turns": {t: {"kind": tmpl.get(t), "normal": stops.get(t) == "no_tool_use", "end": turn_ends.get(t),
                          "compactions": compactions.get(t, 0), "prompt": prompts.get(t),
                          "rounds": sum(1 for r in rows if r["turn"] == t)} for t in turns},
            "requests": rel, "template": str(T19_TEMPLATE.relative_to(REPO)), "rounds": len(rows)}
    return sess, rows


# -- t16: topic 16's trials ---------------------------------------------------------------------------------------
def t16_trials() -> list[Path]:
    out = []
    for job in T16_JOBS:
        for bench in T16_BENCHES:
            out += sorted((HE / "jobs" / job / bench).glob("*/*/agent/s15/calls.jsonl"))
    return out


def t16_one(calls_path: str) -> tuple[dict, list[dict]]:
    calls_path = Path(calls_path).resolve()
    s15 = calls_path.parent
    trial_dir = s15.parent.parent
    job, bench, phase = trial_dir.parts[-4], trial_dir.parts[-3], trial_dir.parts[-2]
    recs = read_jsonl(calls_path)
    traces = sorted((s15 / "traces").glob("run_*.jsonl"))
    events = read_jsonl(traces[0]) if traces else []
    snips = read_jsonl(s15 / "events.jsonl")
    rel = str(calls_path.relative_to(REPO))
    calls = []
    for r in recs:
        if r.get("purpose") != "lead" or r.get("response") is None or not r.get("request"):
            continue
        t0 = r.get("t")
        calls.append({"call": r["call"], "turn": "turn_000001", "messages": r["request"].get("messages") or [],
                      "blocks": r.get("response") or [], "usage": usage_of(r.get("usage")),
                      "stop": r.get("stop_reason"), "t0": t0,
                      "t1": (t0 + r["ms"] / 1000.0) if t0 is not None and r.get("ms") is not None else None,
                      "ptr": {"file": rel, "call": r["call"]}})
    reward, task = None, None
    try:
        res = json.loads((trial_dir / "result.json").read_text())
        reward = ((res.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        task = res.get("task_name")                       # the trial folder name truncates it
    except (OSError, ValueError):
        pass
    sid = f"t16/{job}/{bench}/{phase}/{trial_dir.name}"
    base = {"src": "t16", "sid": sid, "group": f"{job}/{bench}", "kind": bench}
    rows = chain_rounds(calls, trace_tools(events), base)
    task = task or trial_dir.name.rsplit("__", 1)[0]
    first = calls[0]["messages"] if calls else []
    prompt = next((m["content"] for m in first if m.get("role") == "user" and isinstance(m.get("content"), str)), None)
    template = T16_TEMPLATE if job != "minimax-m2.7" else None   # the profile ran the vendor template
    sess = {"src": "t16", "sid": sid, "group": f"{job}/{bench}", "job": job, "bench": bench, "phase": phase,
            "trial": trial_dir.name, "task": task, "reward": reward, "snips": len(snips),
            "turns": {"turn_000001": {"kind": bench, "normal": bool(rows) and rows[-1]["final"], "prompt": prompt,
                                      "rounds": len(rows)}},
            "requests": rel, "template": str(template.relative_to(REPO)) if template else None,
            "rounds": len(rows)}
    return sess, rows


# -- requests and tool schemas for later stages ---------------------------------------------------------------------
@lru_cache(maxsize=4)
def _request_file(rel: str) -> dict[int, dict]:
    out = {}
    for r in read_jsonl(REPO / rel):
        if "call_index" in r:                            # t19
            out[r["call_index"]] = {"system": r.get("system"), "tools": r.get("tools"),
                                    "messages": r.get("messages"), "max_tokens": r.get("max_tokens")}
        elif "call" in r and r.get("request"):           # t16
            q = r["request"]
            out[r["call"]] = {"system": q.get("system"), "tools": q.get("tools"), "messages": q.get("messages"),
                              "max_tokens": r.get("max_tokens")}
    return out


def request(ptr: dict) -> dict:
    """The request of a round as s15 sent it ({system, tools, messages, max_tokens}), tool schemas resolved."""
    req = dict(_request_file(ptr["file"])[ptr["call"]])
    req["tools"] = resolve_tools(req.get("tools"))
    return req


_BUILTIN: dict[str, dict] = {}


def resolve_tools(tools) -> list[dict] | None:
    """t16 records only tool names; the lead sent harness_eval's vendored BUILTIN_TOOLS filtered to them, in that
    order (the same rule as harness_eval/run/epic_replay_live.py builtin_tools)."""
    if not tools or all(isinstance(t, dict) for t in tools):
        return tools
    if not _BUILTIN:
        src = (HE / "vendor" / "s15" / "s15_integrated_harness" / "code.py").read_text()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BUILTIN_TOOLS"
                                                    for t in node.targets):
                _BUILTIN.update({t["name"]: t for t in ast.literal_eval(node.value)})
    return [_BUILTIN[t] if isinstance(t, str) else t for t in tools]


def load(src: str) -> tuple[list[dict], list[dict]]:
    """(sessions, rounds) of one source, as built."""
    return read_jsonl(ROUNDS / f"sessions_{src}.jsonl.xz"), read_jsonl(ROUNDS / f"rounds_{src}.jsonl.xz")


def by_session(rows: list[dict]) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for r in rows:
        out[r["sid"]].append(r)
    for v in out.values():
        v.sort(key=lambda r: r["call"])
    return out


# -- CLI ------------------------------------------------------------------------------------------------------------
def build(src: str, workers: int) -> None:
    fn, items = (t19_one, t19_sessions()) if src == "t19" else (t16_one, t16_trials())
    sessions, rows = [], []
    with get_context("fork").Pool(workers) as pool:
        for sess, rs in pool.imap(fn, [str(p) for p in items], chunksize=4):
            sessions.append(sess)
            rows += rs
    sessions.sort(key=lambda s: s["sid"])
    rows.sort(key=lambda r: (r["sid"], r["call"]))
    n_s = write_jsonl_xz(ROUNDS / f"sessions_{src}.jsonl.xz", sessions)
    n_r = write_jsonl_xz(ROUNDS / f"rounds_{src}.jsonl.xz", rows)
    print(f"[{src}] {n_s} sessions, {n_r} rounds -> {ROUNDS}", flush=True)


def show() -> None:
    for src in ("t19", "t16"):
        sessions, rows = load(src)
        if not rows:
            continue
        print(f"== {src}: {len(sessions)} sessions, {len(rows)} rounds")
        by_group = defaultdict(list)
        for r in rows:
            by_group[r["group"]].append(r)
        print(f"{'group':40s} {'rounds':>7s} {'final':>6s} {'multi':>6s} {'append_ok':>9s} {'res next/trace/none':>20s}")
        for g, rs in sorted(by_group.items()):
            tool_rounds = [r for r in rs if not r["final"]]
            src_c = Counter(r["results_src"] for r in tool_rounds)
            ok = [r["append_ok"] for r in rs if r["append_ok"] is not None]
            print(f"{g:40s} {len(rs):7d} {sum(r['final'] for r in rs):6d} "
                  f"{sum(len(r['calls']) > 1 for r in rs) / max(1, len(tool_rounds)):6.1%} "
                  f"{sum(ok) / max(1, len(ok)):9.1%} "
                  f"{src_c['next']:>8d}/{src_c['trace']:>5d}/{src_c['none']:>5d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--src", default="t19,t16")
    b.add_argument("--workers", type=int, default=48)
    sub.add_parser("show")
    a = ap.parse_args()
    if a.cmd == "build":
        for src in a.src.split(","):
            build(src, a.workers)
    else:
        show()


if __name__ == "__main__":
    main()
