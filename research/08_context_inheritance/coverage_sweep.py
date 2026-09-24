#!/usr/bin/env python3
"""F1 — is the coverage/rounds relationship a step or a slope?

    python3 research/08_context_inheritance/coverage_sweep.py --reps 10 --concurrency 3

The three-arm experiment showed rounds falling 8.00 -> 6.47 -> 1.50 as a successor was handed 0, 1
and 2 of the documents it needed: the LAST document was worth 3.2x the first.  Three points cannot
separate "the cost of anything being missing" from "the cost per missing item", and the two readings
give opposite deployment advice (concentrate a retention budget, or spread it).

So: a task needing N=4 documents, swept over k = 0,1,2,3,4 documents supplied.

    rounds ≈ 1 + a·1[anything missing] + b·(items missing)

  a >> b  -> a step: partial coverage is worthless, concentrate the budget
  a ≈ 0   -> a slope: every byte pays, spread the budget

Each task asks for four facts, one living in exactly one of four windows with disjoint symbols
(verified by --check), so "supplied" is unambiguous and the score can be split into facts whose home
document was supplied and facts whose home document was missing.  No path is ever named.

WHICH documents are supplied rotates with the repetition, so "how many" is never confounded with
"which one".  A sixth arm supplies all four but truncated to ~half, fact retained, to ask whether a
partially present document behaves like a present one or an absent one.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
from inherit_loop import (HANDLERS, MAX_TOKENS, SYSTEM, TOOLS, _norm, call_with_retry,  # noqa: E402
                          client_and_model, read_window)

REPO = Path(__file__).resolve().parents[2]
H = "s15_integrated_harness"
MAX_ROUNDS = 14

# four windows with disjoint symbols; one graded fact per window per task
DOCS = {
    "runtime": {"path": f"{H}/trace_runtime.py", "offset": 25, "limit": 50},
    "view":    {"path": f"{H}/trace_view.py",    "offset": 13, "limit": 120},
    "stats":   {"path": f"{H}/trace_stats.py",   "offset": 28, "limit": 75},
    "design":  {"path": f"{H}/DESIGN.md",        "offset": 28, "limit": 27},
}
DOC_ORDER = ["runtime", "view", "stats", "design"]

TASKS = {
    "C1": {
        "question": ("In this repository's integrated harness and its tracing stack, give all four of: "
                     "(a) the name of the constant that caps how many characters of a tool call's "
                     "arguments are recorded in a trace event, (b) the name of the set listing the "
                     "tools treated as wrappers when a trace is rendered, (c) the name of the "
                     "constant the trace statistics tool checks a file's schema version against, and "
                     "(d) the name of the constant that raises the output-token cap on escalation."),
        "facts": {"runtime": ["DEFAULT_ARGUMENT_CHARS"], "view": ["WRAPPER_TOOLS"],
                  "stats": ["EXPECTED_SCHEMA_VERSION"], "design": ["ESCALATED_MAX_TOKENS"]},
    },
    "C2": {
        "question": ("In this repository's integrated harness and its tracing stack, give all four of: "
                     "(a) the name of the constant that caps the preview length stored in a trace "
                     "event, (b) the name of the function that merges overlapping time ranges when "
                     "a trace timeline is built, (c) the name of the function that validates a single "
                     "trace file, and (d) the name of the constant fixing how many of the newest tool "
                     "results compaction never touches."),
        "facts": {"runtime": ["DEFAULT_PREVIEW_CHARS"], "view": ["_merge_ranges"],
                  "stats": ["validate_file"], "design": ["KEEP_RECENT_TOOL_RESULTS"]},
    },
    "C3": {
        "question": ("In this repository's integrated harness and its tracing stack, give all four of: "
                     "(a) the name of the fallback serialiser function used when an event value is "
                     "not JSON-serialisable, (b) the name of the function that pairs start and end "
                     "events into spans, (c) the name of the function that checks one trace record "
                     "for its required fields, and (d) the numeric character shrink target the "
                     "compaction pipeline aims down to."),
        "facts": {"runtime": ["_json_default"], "view": ["pair_spans"],
                  "stats": ["check_record"], "design": ["409600"]},
    },
}
ARMS = ["k0", "k1", "k2", "k3", "k4", "k4trunc"]


def supplied_docs(arm: str, rep: int) -> list[str]:
    """Which documents this arm hands over. Rotating by rep keeps 'how many' from being confounded
    with 'which one' -- at k=1 every document takes its turn across repetitions."""
    if arm == "k0":
        return []
    k = 4 if arm == "k4trunc" else int(arm[1:])
    rotated = DOC_ORDER[rep % len(DOC_ORDER):] + DOC_ORDER[:rep % len(DOC_ORDER)]
    return sorted(rotated[:k], key=DOC_ORDER.index)


def window_text(doc: str, task: dict, truncate: bool) -> str:
    spec = DOCS[doc]
    text = read_window(spec["path"], spec["offset"], spec["limit"])
    if not truncate:
        return text
    # keep a CONTIGUOUS half that still contains the graded fact: the question is whether a partly
    # present document counts as present, not whether a shredded one does
    lines = text.splitlines()
    fact = task["facts"][doc][0]
    hit = next((i for i, l in enumerate(lines) if _norm(fact) in _norm(l)), len(lines) // 2)
    half = max(len(lines) // 2, 1)
    start = max(0, min(hit - half // 2, len(lines) - half))
    return "\n".join(lines[start:start + half])


def injection(arm: str, rep: int, task: dict) -> tuple[list[dict], int, list[str]]:
    docs = supplied_docs(arm, rep)
    blocks, total = [], 0
    for index, doc in enumerate(docs):
        text = window_text(doc, task, truncate=(arm == "k4trunc"))
        spec = DOCS[doc]
        payload = {"path": spec["path"], "offset": spec["offset"], "limit": spec["limit"]}
        use_id = f"prewarm_{arm}{index}"
        blocks += [
            {"role": "assistant",
             "content": [{"type": "tool_use", "id": use_id, "name": "read_file", "input": payload}]},
            {"role": "user",
             "content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}]
        total += len(text)
    return blocks, total, docs


def run_one(spec: dict, client, model) -> dict:
    task = TASKS[spec["task"]]
    blocks, injected_chars, docs = injection(spec["arm"], spec["rep"], task)
    messages = [{"role": "user", "content": task["question"]}] + blocks
    calls: list[str] = []
    rounds = in_tok = out_tok = cached = 0
    answer = ""
    stopped = "answered"
    model_s = tool_s = 0.0
    started = time.perf_counter()
    while rounds < MAX_ROUNDS:
        rounds += 1
        t0 = time.perf_counter()
        try:
            response = call_with_retry(client, model=model, system=SYSTEM.format(repo=REPO),
                                       tools=TOOLS, max_tokens=MAX_TOKENS, messages=messages)
        except Exception as exc:                                    # noqa: BLE001
            model_s += time.perf_counter() - t0
            stopped = f"{type(exc).__name__}: {str(exc)[:80]}"
            break
        model_s += time.perf_counter() - t0
        in_tok += response.usage.input_tokens
        out_tok += response.usage.output_tokens
        cached += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            answer = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
            break
        results, finished = [], False
        for block in uses:
            calls.append(block.name)
            if block.name == "answer":
                answer = (block.input or {}).get("answer", "")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Answer recorded."})
                finished = True
                continue
            handler = HANDLERS.get(block.name)
            t1 = time.perf_counter()
            out = (f"Error: unknown tool {block.name}" if handler is None
                   else str(handler(**(block.input or {}))))
            tool_s += time.perf_counter() - t1
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})
        if finished:
            break
    else:
        stopped = "max_rounds"

    flat = _norm(answer)
    per_doc = {d: any(_norm(a) in flat for a in task["facts"][d]) for d in DOC_ORDER}
    given = set(docs)
    counts = defaultdict(int)
    for c in calls:
        counts[c] += 1
    return dict(spec, rounds=rounds, calls=calls, docs_supplied=docs, k=len(docs),
                n_read_file=counts.get("read_file", 0),
                locate_calls=counts.get("glob", 0) + counts.get("grep", 0),
                injected_chars=injected_chars, input_tokens=in_tok, output_tokens=out_tok,
                cached_tokens=cached, model_s=round(model_s, 2), tool_s=round(tool_s, 3),
                wall_s=round(time.perf_counter() - started, 2), stopped=stopped,
                hits=sum(per_doc.values()), total=len(DOC_ORDER),
                hits_supplied=sum(1 for d in DOC_ORDER if d in given and per_doc[d]),
                total_supplied=len(given),
                hits_missing=sum(1 for d in DOC_ORDER if d not in given and per_doc[d]),
                total_missing=len(DOC_ORDER) - len(given),
                per_doc={d: int(v) for d, v in per_doc.items()},
                answer=answer[:400])


def check() -> int:
    """Gate: every graded fact must appear in exactly one window. This is the check that caught a
    void task set in the previous experiment, where all facts were silently present in both."""
    texts = {d: _norm(read_window(**DOCS[d])) for d in DOC_ORDER}
    bad = 0
    for tid, task in TASKS.items():
        for home, facts in task["facts"].items():
            for f in facts:
                hits = [d for d, t in texts.items() if _norm(f) in t]
                if hits != [home]:
                    bad += 1
                    print(f"  {tid} {f!r} home={home} but found in {hits}")
    print("ALL FACTS WINDOW-UNIQUE" if not bad else f"*** {bad} clashes ***")
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reps", type=int, default=10)
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--tasks", default=",".join(TASKS))
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--out", default=str(REPO / "research" / "08_context_inheritance" / "data" / "inherit" / "coverage.jsonl"))
    p.add_argument("--topup", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.check:
        return check()
    if check():
        return 2                                                    # never spend calls on a void set

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    specs = [{"arm": a, "task": t, "rep": r}
             for a in args.arms.split(",") for t in args.tasks.split(",") for r in range(args.reps)]
    if args.topup and out.exists():
        done = {(x["arm"], x["task"], x["rep"])
                for x in (json.loads(l) for l in out.read_text().splitlines() if l.strip())
                if x.get("stopped") in ("answered", "max_rounds")}
        specs = [s for s in specs if (s["arm"], s["task"], s["rep"]) not in done]
    random.Random(20260918).shuffle(specs)
    print(f"{len(specs)} trials -> {out}")
    if args.dry_run:
        for a in args.arms.split(","):
            for r in range(min(args.reps, 4)):
                b, c, d = injection(a, r, TASKS["C1"])
                print(f"  {a:8s} rep{r} k={len(d)} docs={d} chars={c}")
        return 0

    client, model = client_and_model()
    lock = threading.Lock()
    handle = out.open("a", encoding="utf-8")

    def work(spec):
        row = run_one(spec, client, model)
        with lock:
            handle.write(json.dumps(row) + "\n")
            handle.flush()
        print(f"  {row['arm']:8s} {row['task']} rep{row['rep']} k={row['k']} "
              f"rounds={row['rounds']} acc={row['hits']}/4 "
              f"(given {row['hits_supplied']}/{row['total_supplied']}, "
              f"missing {row['hits_missing']}/{row['total_missing']}) {row['wall_s']}s", flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(work, specs))
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
