#!/usr/bin/env python3
"""Does an advertised tool cost change a multi-round agent's route -- and does the change pay off?

    python3 research/06_tool_cost/tool_cost_loop.py --reps 4 \
        --out research/06_tool_cost/data/tool_cost/stage2.jsonl

Stage 1 (research/06_tool_cost/tool_cost_probe.py) asks which tool the model names in ONE response.  This stage
runs a real loop -- the tools execute against real repository files -- so the same manipulation can
be scored on what a harness actually cares about: rounds, bytes pulled into context, wall time, and
whether the answer is still right.

2x2 factorial, because "advertised" and "actually slow" are different things and only the harness
knows which is which:

                       read_file description says 5 s     read_file really sleeps 5 s
    plain                        no                                no
    advertise                    yes                               no     <- pure prompt sensitivity
    enforce                      no                                yes    <- agent pays, blind
    both                         yes                               yes    <- agent pays, informed

`advertise` isolates the effect of the text (nothing is actually slower, so any change in the route
is caused by the description alone).  `both` minus `enforce` is the payoff: the wall-clock seconds
that telling the agent about the cost actually saves.  `advertise` minus `plain` on correctness is
the risk: the cheap routes here are LOSSY (grep returns matching lines only, recall_summary returns
a deliberately vague cached blurb), so a cost-avoiding agent can talk itself out of the evidence it
needs.

Three questions over real files, each with an exact gradeable answer, chosen so that the expensive
route (read the whole file) and the cheap route (search it) both work but differ by two orders of
magnitude in returned bytes: GLOSSARY.md is 12.8 KB and code.py is 146 KB.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import random
import re
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SLOW_S = 5.00
CHEAP_S = {"grep": 0.05, "glob": 0.03, "recall_summary": 0.02, "answer": 0.0}
MAX_ROUNDS = 8
MAX_TOKENS = 1500

# A cached blurb is honestly vague: it names the topic and omits every specific value, which is what
# makes it a trap for an agent that is trying to avoid an expensive read.
SUMMARIES = {
    "s15_integrated_harness/GLOSSARY.md":
        "Cached summary (may be stale): a glossary of s15 harness terms -- agent loop, compaction, "
        "cron jobs, error recovery, hooks, the lead agent, MCP integration, memory, permissions, "
        "plan gates, prompt assembly, skills, the task board, teammates, tool dispatch, trace "
        "events and worktrees. Each entry explains the concept and points at the functions in "
        "code.py that implement it. Specific constants and thresholds are not reproduced here.",
    "s15_integrated_harness/code.py":
        "Cached summary (may be stale): the single-file s15 integrated harness. Defines the module "
        "constants, the task board, the memory runtime, the trace recorder, prompt assembly, the "
        "built-in tools (bash, read_file, write_file, edit_file, glob), the teammate/team-event "
        "machinery, MCP client plumbing, the compaction pipeline and the main agent loop. Numeric "
        "configuration values are defined near the top of the file and are not listed here.",
}

TASKS = {
    "T1": {"path": "s15_integrated_harness/GLOSSARY.md",
           "question": ("In s15_integrated_harness/GLOSSARY.md, the compaction pipeline's "
                        "`snip_compact` stage keeps a fixed number of messages at the tail. "
                        "What is that number? Answer with the number."),
           "must": [["46"]]},
    "T2": {"path": "s15_integrated_harness/code.py",
           "question": ("In s15_integrated_harness/code.py, what is the value of the module "
                        "constant PERSIST_THRESHOLD? Answer with the number."),
           "must": [["30000", "30,000"]]},
    "T3": {"path": "s15_integrated_harness/GLOSSARY.md",
           "question": ("In s15_integrated_harness/GLOSSARY.md, the Error recovery entry states the "
                        "exponential backoff base delay and the value the delay is capped at. "
                        "Give both values."),
           "must": [["500"], ["32"]]},
    # T4 is the accuracy-risk task: the answer needs the whole Compaction entry, so the cheap
    # routes (a cached blurb, one narrow grep) are genuinely insufficient.
    "T4": {"path": "s15_integrated_harness/GLOSSARY.md",
           "question": ("In s15_integrated_harness/GLOSSARY.md, the Compaction entry names the "
                        "stages of the context-shrinking pipeline in order. List every stage "
                        "name."),
           "must": [["tool_result_budget"], ["snip_compact"], ["micro_compact"],
                    ["fit_tool_results"], ["compact_history"]]},
}

ARMS = {"plain": (False, False), "advertise": (True, False),
        "enforce": (False, True), "both": (True, True)}


# ---------------------------------------------------------------- tools

def _safe(path: str) -> Path:
    resolved = (REPO / path).resolve()
    if not resolved.is_relative_to(REPO):
        raise ValueError(f"path escapes repository: {path}")
    return resolved


def t_read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    target = _safe(path)
    if not target.is_file():
        return f"Error: no such file: {path}"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start = max((offset or 1) - 1, 0)
    end = len(lines) if limit is None else start + limit
    return "".join(lines[start:end])


def t_grep(pattern: str, path: str | None = None) -> str:
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Error: bad pattern: {exc}"
    targets: list[Path] = []
    if path:
        candidate = _safe(path)
        targets = [candidate] if candidate.is_file() else sorted(
            p for p in candidate.rglob("*") if p.is_file())
    else:
        targets = [_safe(p) for p in
                   ("s15_integrated_harness/GLOSSARY.md", "s15_integrated_harness/code.py",
                    "s15_integrated_harness/ARCHITECTURE.md", "s15_integrated_harness/DESIGN.md")]
    hits = []
    for target in targets[:40]:
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{target.relative_to(REPO)}:{number}: {line.rstrip()[:300]}")
                if len(hits) >= 80:
                    return "\n".join(hits) + "\n(truncated at 80 matches)"
    return "\n".join(hits) if hits else "(no matches)"


def t_glob(pattern: str) -> str:
    names = [str(p.relative_to(REPO)) for p in REPO.rglob("*")
             if p.is_file() and fnmatch.fnmatch(str(p.relative_to(REPO)), pattern)]
    names = sorted(names)[:100]
    return "\n".join(names) if names else "(no matches)"


def t_recall_summary(path: str) -> str:
    key = path.lstrip("./")
    for known, blurb in SUMMARIES.items():
        if key.endswith(known) or known.endswith(key):
            return blurb
    return f"(no cached summary for {path})"


HANDLERS = {"read_file": t_read_file, "grep": t_grep, "glob": t_glob,
            "recall_summary": t_recall_summary}

POOL = [
    ("read_file", "Read file contents. Returns the whole file unless offset/limit are given.",
     {"type": "object", "properties": {"path": {"type": "string"},
                                       "offset": {"type": "integer"},
                                       "limit": {"type": "integer"}},
      "required": ["path"]}),
    ("grep", "Search files for a regular expression. Returns only the matching lines, with line numbers.",
     {"type": "object", "properties": {"pattern": {"type": "string"},
                                       "path": {"type": "string"}},
      "required": ["pattern"]}),
    ("glob", "Find files matching a glob pattern; ** matches recursively.",
     {"type": "object", "properties": {"pattern": {"type": "string"}},
      "required": ["pattern"]}),
    ("recall_summary", "Return a cached one-paragraph summary of a file. May be stale and omits detail.",
     {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
    ("answer", "Submit your final answer and end the task.",
     {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}),
]


def advertised_cost(name: str) -> float:
    return SLOW_S if name == "read_file" else CHEAP_S.get(name, 0.05)


def build_tools(advertise: bool) -> list[dict]:
    tools = []
    for name, desc, schema in POOL:
        if advertise and name != "answer":
            desc = f"{desc} Measured latency: {advertised_cost(name):.2f} s per call."
        tools.append({"name": name, "description": desc, "input_schema": schema})
    return tools


SYSTEM = ("You are a coding agent. Act, don't explain. Answer the user's question about this "
          "repository using the tools, then call `answer` with the final answer.\n\n"
          "Working directory: {repo}")


# ---------------------------------------------------------------- loop

def client_and_model():
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env", override=True)
    import anthropic
    return (anthropic.Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"), max_retries=0),
            os.environ["MODEL_ID"])


def call_with_retry(client, **kwargs):
    import random
    last = None
    for attempt in range(6):
        try:
            return client.messages.create(**kwargs)
        except Exception as exc:                                   # noqa: BLE001
            last = exc
            status = getattr(exc, "status_code", None)
            if status is not None and status not in (429, 500, 502, 503, 504, 529):
                raise
            time.sleep(min(2 ** attempt, 20) * (0.6 + random.random() * 0.8))
    raise last


def grade(answer: str, must: list[list[str]]) -> bool:
    text = (answer or "").lower()
    return all(any(alt.lower() in text for alt in group) for group in must)


def run_one(spec: dict, client, model) -> dict:
    advertise, enforce = ARMS[spec["arm"]]
    task = TASKS[spec["task"]]
    tools = build_tools(advertise)
    messages = [{"role": "user", "content": task["question"]}]

    calls: list[str] = []
    bill = real_tool_s = model_s = 0.0
    result_bytes = in_tok = out_tok = 0
    answer_text = ""
    rounds = 0
    started = time.perf_counter()

    while rounds < MAX_ROUNDS:
        rounds += 1
        t0 = time.perf_counter()
        try:
            response = call_with_retry(client, model=model, system=SYSTEM.format(repo=REPO),
                                       tools=tools, max_tokens=MAX_TOKENS, messages=messages)
        except Exception as exc:                                   # noqa: BLE001
            model_s += time.perf_counter() - t0
            overflow = "too long" in str(exc).lower() or "context" in str(exc).lower()
            spec = dict(spec, stopped=("context_overflow" if overflow else
                                       f"{type(exc).__name__}"))
            break
        model_s += time.perf_counter() - t0
        in_tok += response.usage.input_tokens
        out_tok += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})

        uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            answer_text = "".join(b.text for b in response.content
                                  if getattr(b, "type", None) == "text")
            break

        results, finished = [], False
        for block in uses:
            calls.append(block.name)
            bill += advertised_cost(block.name)
            if block.name == "answer":
                answer_text = (block.input or {}).get("answer", "")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Answer recorded."})
                finished = True
                continue
            handler = HANDLERS.get(block.name)
            if handler is None:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Error: unknown tool {block.name}", "is_error": True})
                continue
            if enforce:
                delay = advertised_cost(block.name)
                time.sleep(delay)
                real_tool_s += delay
            try:
                out = handler(**(block.input or {}))
            except Exception as exc:                               # noqa: BLE001
                out = f"Error: {type(exc).__name__}: {exc}"
            out = str(out)
            result_bytes += len(out)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})
        if finished:
            break

    wall = time.perf_counter() - started
    counts: dict[str, int] = {}
    for name in calls:
        counts[name] = counts.get(name, 0) + 1
    return dict(spec, rounds=rounds, calls=calls, counts=counts,
                n_read_file=counts.get("read_file", 0), n_grep=counts.get("grep", 0),
                n_recall=counts.get("recall_summary", 0), n_glob=counts.get("glob", 0),
                used_read_file=counts.get("read_file", 0) > 0,
                advertised_bill_s=round(bill, 3), real_tool_s=round(real_tool_s, 3),
                model_s=round(model_s, 3), wall_s=round(wall, 3),
                result_bytes=result_bytes, input_tokens=in_tok, output_tokens=out_tok,
                answer=answer_text[:600], correct=grade(answer_text, task["must"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=4)
    parser.add_argument("--arms", default="plain,advertise,enforce,both")
    parser.add_argument("--tasks", default="T1,T2,T3,T4")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default=str(REPO / "research" / "06_tool_cost" / "data"
                                             / "tool_cost" / "stage2.jsonl"))
    args = parser.parse_args()

    specs = [{"arm": arm, "task": task, "rep": rep}
             for arm in args.arms.split(",")
             for task in args.tasks.split(",")
             for rep in range(args.reps)]
    # Submitted in arm order the last arm would run entirely after the first, and any drift in
    # provider latency would read as an arm effect; wall time is one of the outcomes here, so the
    # order is shuffled with a fixed seed instead.
    random.Random(20260914).shuffle(specs)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    client, model = client_and_model()
    lock = threading.Lock()
    done = [0]
    started = time.time()

    with out_path.open("w", encoding="utf-8") as handle:
        def work(spec):
            try:
                record = run_one(spec, client, model)
            except Exception as exc:                               # noqa: BLE001
                record = dict(spec, error=f"{type(exc).__name__}: {exc}")
            with lock:
                handle.write(json.dumps(record, default=str) + "\n")
                handle.flush()
                done[0] += 1
                print(f"  {done[0]}/{len(specs)} {spec['arm']}/{spec['task']}/r{spec['rep']} "
                      f"read={record.get('n_read_file')} grep={record.get('n_grep')} "
                      f"ok={record.get('correct')} wall={record.get('wall_s')}", flush=True)
            return record
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(work, specs))
    print(f"wrote {out_path} ({len(specs)} runs, {time.time() - started:.0f} s)")


if __name__ == "__main__":
    main()
