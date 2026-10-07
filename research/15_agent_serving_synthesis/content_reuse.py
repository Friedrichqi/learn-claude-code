#!/usr/bin/env python3
"""Tool-result content shared across concurrent agent sessions (offline) -- research/15_agent_serving_synthesis.

11's multi-user cells ran solo 4-turn harness sessions on ten codebases (nine site-packages copies and the
harness itself), several sessions per codebase at a time. Every session's capture holds the exact request
of every model call. This script reads them, read-only, and asks how much of the tool-result content that
enters a session's context was already fetched -- by the same session earlier (a re-read, mostly after
compaction evicted it) or by another session on the same codebase (content a shared, position-independent
KV store could serve without prefill) -- at two granularities:

  whole   SHA-1 of the tool result's text: only byte-identical results match;
  line    exact text of each line with >= 8 non-blank characters, weighted by its characters: catches the
          same file windows read with other offsets (research/02 Part B, research/08 Part B use the same idea).

Tokens are characters / 4.61 (11 §2.2's median characters per token on this harness).

    python research/15_agent_serving_synthesis/content_reuse.py [--cells n04,n08,...] [--root DIR]
        [--out research/15_agent_serving_synthesis/data/tables/content_reuse.md]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: E402,F401

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]
ROOT11 = REPO / "research/11_multiuser_kv/data/multiuser_live/cells"
CELLS = ["n01a", "n04", "n08", "n16", "n32"]
CHARS_PER_TOKEN = 4.61


def result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def session_results(path: Path):
    """Yield (t_send, tool_name, text) for every tool result the first time it enters a request."""
    seen, names = set(), {}
    with lzma.open(path, "rt") as f:
        for line in f:
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            if req.get("purpose") not in (None, "lead"):
                continue
            t = req.get("t_send") or 0.0
            for m in req.get("messages") or []:
                c = m.get("content")
                if not isinstance(c, list):
                    continue
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        names[b.get("id")] = b.get("name", "?")
                    elif b.get("type") == "tool_result":
                        tid = b.get("tool_use_id")
                        if tid in seen:
                            continue
                        seen.add(tid)
                        yield t, names.get(tid, "?"), result_text(b)


def analyze_cell(cell: str, root: Path) -> dict | None:
    runs = sorted((root / cell / "runs").glob("*/"))
    events = []
    for run in runs:
        metas = list(run.glob("*.meta.json"))
        caps = list(run.glob("*.requests.jsonl.xz"))
        if not metas or not caps:
            continue
        meta = json.loads(metas[0].read_text())
        cb = meta.get("codebase", "?")
        for t, name, text in session_results(caps[0]):
            events.append((t, run.name, cb, name, text))
    if not events:
        return None
    events.sort(key=lambda e: e[0])
    whole_first = {}                       # (codebase, hash) -> first session
    sess_whole = defaultdict(set)
    line_first = {}                        # (codebase, line hash) -> first session
    sess_lines = defaultdict(set)
    acc = defaultdict(float)
    by_tool = defaultdict(lambda: [0.0, 0.0, 0.0])   # chars total / intra / cross (line level)
    sessions = set()
    for t, sess, cb, name, text in events:
        sessions.add(sess)
        n = len(text)
        acc["results"] += 1
        acc["chars"] += n
        h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        key = (cb, h)
        if h in sess_whole[sess]:
            acc["whole_intra"] += n
        elif key in whole_first and whole_first[key] != sess:
            acc["whole_cross"] += n
        whole_first.setdefault(key, sess)
        sess_whole[sess].add(h)
        intra = cross = 0
        for ln in text.split("\n"):
            s = ln.strip()
            if len(s.replace(" ", "")) < 8:
                continue
            lh = hashlib.sha1(s.encode("utf-8", "replace")).digest()[:10]
            w = len(ln) + 1
            acc["line_chars"] += w
            k2 = (cb, lh)
            if lh in sess_lines[sess]:
                intra += w
            elif k2 in line_first and line_first[k2] != sess:
                cross += w
            line_first.setdefault(k2, sess)
            sess_lines[sess].add(lh)
        acc["line_intra"] += intra
        acc["line_cross"] += cross
        bt = by_tool[name]
        bt[0] += n
        bt[1] += intra
        bt[2] += cross
    pct = lambda a, b: 100.0 * a / b if b else float("nan")  # noqa: E731
    top = sorted(by_tool.items(), key=lambda kv: -kv[1][0])[:4]
    return {
        "cell": cell, "sessions": len(sessions), "codebases": len({e[2] for e in events}),
        "results": int(acc["results"]), "tok_m": acc["chars"] / CHARS_PER_TOKEN / 1e6,
        "tok_per_result": acc["chars"] / max(1, acc["results"]) / CHARS_PER_TOKEN,
        "whole_intra": pct(acc["whole_intra"], acc["chars"]), "whole_cross": pct(acc["whole_cross"], acc["chars"]),
        "line_intra": pct(acc["line_intra"], acc["line_chars"]), "line_cross": pct(acc["line_cross"], acc["line_chars"]),
        "tools": [(name, round(v[0] / CHARS_PER_TOKEN / 1e3), round(pct(v[1], v[0]), 1), round(pct(v[2], v[0]), 1))
                  for name, v in top],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", default=",".join(CELLS))
    ap.add_argument("--root", default=str(ROOT11))
    ap.add_argument("--out", default=str(HERE / "data/tables/content_reuse.md"))
    args = ap.parse_args()
    rows = []
    for cell in [c for c in args.cells.split(",") if c]:
        r = analyze_cell(cell, Path(args.root))
        if r is None:
            print(f"{cell}: no captures", file=sys.stderr)
            continue
        print(f"{cell}: {r['sessions']} sessions, {r['results']} results, line-level intra {r['line_intra']:.1f} %, "
              f"cross {r['line_cross']:.1f} %", file=sys.stderr)
        rows.append(r)
    f1 = lambda x: "–" if x != x else f"{x:.1f}"  # noqa: E731
    out = ["# Tool-result content shared within and across concurrent agent sessions (11's captures, offline)", "",
           "Generated by `research/15_agent_serving_synthesis/content_reuse.py`; definitions in its docstring. "
           "Lead calls only; each tool result counted once, when it first enters its session's prompt.", "",
           "| cell | sessions | codebases | tool results | tool-result tokens (M) | tokens per result | "
           "whole: re-read by the same session, % | whole: fetched before by another session, % | "
           "line: same session, % | line: another session, % | largest tools (k tokens, line same / other %) |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        tools = "; ".join(f"{n} {k}k ({a}/{b})" for n, k, a, b in r["tools"])
        out.append(f"| {r['cell']} | {r['sessions']} | {r['codebases']} | {r['results']:,} | {r['tok_m']:.2f} | "
                   f"{r['tok_per_result']:.0f} | {f1(r['whole_intra'])} | {f1(r['whole_cross'])} | {f1(r['line_intra'])} | "
                   f"{f1(r['line_cross'])} | {tools} |")
    out += ["", "*Key.* **same session**: content this session had already fetched (a re-read: after compaction evicted it, "
            "or a re-check). **another session**: content not fetched by this session before but fetched earlier by another "
            "session working on the same codebase, i.e. what a content-addressed KV store shared across sessions could "
            "serve. Line level counts lines of at least 8 non-blank characters, weighted by length; it catches the same "
            "file read through different windows, which whole-result hashing misses (research/02 Part B).", ""]
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out) + "\n")
    print(f"wrote {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
