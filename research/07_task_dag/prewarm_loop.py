#!/usr/bin/env python3
"""Stage 1 of the context-handoff experiment: does handing an agent a predecessor's file content
remove agent-loop rounds -- and does it cost correctness?

    python3 research/07_task_dag/prewarm_loop.py --reps 8 \
        --out research/07_task_dag/data/prewarm/stage1.jsonl

A real multi-round loop over real repository files, one scripted agent, so the round count can be
measured with many more repetitions than a full team run affords.  The agent is shaped like a
SUCCESSOR task: it is asked a question whose answer lives in files it is NOT told the path of, so a
baseline run must spend a round locating and a round (or more) reading before it can answer.

Four arms, all token-matched except baseline:

  baseline   nothing injected
  oracle     the exact windows a predecessor would have read, as a synthetic read_file
             tool_use/tool_result pair at the head of the history -- the KV-inheritance emulation
  pollution  an equal-size window of an UNRELATED file.  The negative control: if this removes
             rounds too, what works is the presence of a bulky early tool result, not the content
  summary    a short condensed note instead of the bytes -- what a harness can already do with no
             KV machinery, and which deliberately omits the specifics the grader checks

Primary outcome: rounds.  Secondary: read calls issued, wall, tokens, correctness.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
load_dotenv(REPO / ".env", override=True)

MAX_ROUNDS = 12
MAX_TOKENS = 1500
SYSTEM = ("You are a coding agent working in the repository {repo}. Act, don't explain. "
          "Use the tools to find what you need, then call answer exactly once.")

# ---------------------------------------------------------------- tasks
# Each task's answer needs specifics from files whose paths are NOT given, so the baseline has to
# locate and read.  `windows` is the predecessor's read set: what an oracle handoff would carry.
TASKS = {
    "T1": {
        "question": ("In this repository's integrated harness, what is the numeric character limit "
                     "at which the lead's context is compacted, and how many recent tool results are "
                     "kept when the history is micro-compacted? Give both numbers and the file each "
                     "comes from."),
        "windows": [{"path": "s15_integrated_harness/code.py", "offset": 90, "limit": 40}],
        "must": [["512000", "512,000"], ["3"], ["code.py"]],
        "summary": ("A predecessor task established that the harness keeps a context limit constant "
                    "and a constant for how many recent tool results survive micro-compaction; both "
                    "are module-level constants near the top of the integrated harness module."),
    },
    "T2": {
        "question": ("In this repository's integrated harness, what exact message does a teammate get "
                     "back when it tries to claim a task whose blockers are not finished, and what are "
                     "the three status values a task record moves through? Quote the message."),
        "windows": [{"path": "s15_integrated_harness/code.py", "offset": 379, "limit": 125}],
        "must": [["Blocked by"], ["pending"], ["in_progress"], ["completed", "done"]],
        "summary": ("A predecessor task established that claiming a task validates its blockers and "
                    "returns a refusal string when they are unfinished, and that a task record moves "
                    "through three status values."),
    },
    "T3": {
        "question": ("Using this repository's harness glossary, what does it say a compaction "
                     "placeholder looks like in the message history, and what is the name of the "
                     "component that delivers messages between the lead and its teammates?"),
        "windows": [{"path": "s15_integrated_harness/GLOSSARY.md", "offset": 30, "limit": 22},
                    {"path": "s15_integrated_harness/GLOSSARY.md", "offset": 198, "limit": 18}],
        "must": [["Earlier tool result", "placeholder"], ["bus", "MessageBus", "mailbox"]],
        "summary": ("A predecessor task established that the glossary defines a placeholder left "
                    "behind by compaction and names the component that carries messages between "
                    "agents."),
    },
    "T4": {
        "question": ("In this repository's integrated harness tracing module, which envelope field "
                     "links an event to the event that caused it, which field lists the events it "
                     "depends on, and what is the name of the field carrying the trace format "
                     "version? Give the exact field names."),
        "windows": [{"path": "s15_integrated_harness/trace_runtime.py", "offset": 268, "limit": 45}],
        "must": [["caused_by_event_id"], ["depends_on_event_ids"], ["schema_version"]],
        "summary": ("A predecessor task established that a tool span is delimited by a start and an "
                    "end event and that the envelope carries a causal link field."),
    },
}
ARMS = ["baseline", "oracle", "pollution", "summary"]
POLLUTE = "s15_integrated_harness/ARCHITECTURE.md"


# ---------------------------------------------------------------- tools (real execution)
def _safe(path: str) -> Path:
    resolved = (REPO / path).resolve()
    if not resolved.is_relative_to(REPO.resolve()):
        raise ValueError("path escapes repository")
    return resolved


def read_window(path: str, offset: int = 0, limit: int | None = None) -> str:
    """The harness's own read_file semantics (code.py run_read), so injected bytes are what a real
    read would have produced."""
    lines = _safe(path).read_text(encoding="utf-8").splitlines()
    offset = max(int(offset or 0), 0)
    lines = lines[offset:]
    if limit is not None and limit < len(lines):
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines)


def t_read_file(path: str, offset: int | None = None, limit: int | None = None) -> str:
    try:
        return read_window(path, offset or 0, limit)
    except Exception as exc:                                        # noqa: BLE001
        return f"Error: {exc}"


def t_glob(pattern: str) -> str:
    try:
        hits = sorted(str(p.relative_to(REPO)) for p in REPO.glob(pattern) if p.is_file())
    except Exception as exc:                                        # noqa: BLE001
        return f"Error: {exc}"
    return "\n".join(hits[:200]) or "(no matches)"


def t_grep(pattern: str, path: str | None = None) -> str:
    try:
        targets = [_safe(path)] if path else list((REPO / "s15_integrated_harness").glob("*.py"))
        out = []
        needle = re.compile(pattern)
        for target in targets:
            if not target.is_file():
                continue
            for number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
                if needle.search(line):
                    out.append(f"{target.relative_to(REPO)}:{number}:{line[:200]}")
                    if len(out) >= 80:
                        return "\n".join(out)
        return "\n".join(out) or "(no matches)"
    except Exception as exc:                                        # noqa: BLE001
        return f"Error: {exc}"


HANDLERS = {"read_file": t_read_file, "glob": t_glob, "grep": t_grep}
TOOLS = [
    {"name": "read_file", "description": "Read file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"},
                                                       "offset": {"type": "integer"},
                                                       "limit": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "glob", "description": "Find files by glob pattern; ** matches recursively.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}},
                      "required": ["pattern"]}},
    {"name": "grep", "description": "Search files for a regular expression.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"},
                                                       "path": {"type": "string"}},
                      "required": ["pattern"]}},
    {"name": "answer", "description": "Give the final answer and stop.",
     "input_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                      "required": ["answer"]}},
]


# ---------------------------------------------------------------- injection
def pair(tool: str, payload: dict, text: str, tag: str) -> list[dict]:
    use_id = f"prewarm_{tag}"
    return [{"role": "assistant",
             "content": [{"type": "tool_use", "id": use_id, "name": tool, "input": payload}]},
            {"role": "user",
             "content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}]


def injection(arm: str, task: dict) -> tuple[list[dict], int]:
    if arm == "baseline":
        return [], 0
    if arm == "summary":
        return pair("prior_result", {"from": "predecessor task"}, task["summary"], "sum"), \
               len(task["summary"])
    blocks, total = [], 0
    for index, window in enumerate(task["windows"]):
        text = read_window(window["path"], window.get("offset", 0), window.get("limit"))
        if arm == "pollution":
            # same token count, unrelated content: the control that decides whether the CONTENT is
            # what removed the round, or merely a bulky early tool result
            text = read_window(POLLUTE, 0, None)[:len(text)]
            payload = {"path": POLLUTE}
        else:
            payload = {k: v for k, v in window.items() if v is not None}
        total += len(text)
        blocks.extend(pair("read_file", payload, text, f"{arm}{index}"))
    return blocks, total


def grade(answer: str, must: list[list[str]]) -> bool:
    text = answer.lower().replace(",", "")
    return all(any(alt.lower().replace(",", "") in text for alt in group) for group in must)


def client_and_model():
    return (Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"),
                      auth_token=os.getenv("ANTHROPIC_AUTH_TOKEN"), max_retries=0),
            os.environ["MODEL_ID"])


def call_with_retry(client, tries: int = 4, **kwargs):
    delay = 4.0
    for attempt in range(tries):
        try:
            return client.messages.create(**kwargs)
        except Exception as exc:                                    # noqa: BLE001
            text = str(exc)
            if attempt == tries - 1 or not any(code in text for code in ("429", "529", "overload")):
                raise
            time.sleep(delay + random.random())
            delay *= 1.8
    raise RuntimeError("unreachable")


def run_one(spec: dict, client, model) -> dict:
    task = TASKS[spec["task"]]
    blocks, injected_chars = injection(spec["arm"], task)
    messages = [{"role": "user", "content": task["question"]}] + blocks
    calls: list[str] = []
    rounds = in_tok = out_tok = cached = result_bytes = 0
    answer_text = ""
    started = time.perf_counter()
    stopped = "answered"
    while rounds < MAX_ROUNDS:
        rounds += 1
        try:
            response = call_with_retry(client, model=model, system=SYSTEM.format(repo=REPO),
                                       tools=TOOLS, max_tokens=MAX_TOKENS, messages=messages)
        except Exception as exc:                                    # noqa: BLE001
            stopped = f"{type(exc).__name__}: {str(exc)[:80]}"
            break
        in_tok += response.usage.input_tokens
        out_tok += response.usage.output_tokens
        cached += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            answer_text = "".join(b.text for b in response.content
                                  if getattr(b, "type", None) == "text")
            break
        results, finished = [], False
        for block in uses:
            calls.append(block.name)
            if block.name == "answer":
                answer_text = (block.input or {}).get("answer", "")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Answer recorded."})
                finished = True
                continue
            handler = HANDLERS.get(block.name)
            out = (f"Error: unknown tool {block.name}" if handler is None
                   else str(handler(**(block.input or {}))))
            result_bytes += len(out)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})
        if finished:
            break
    else:
        stopped = "max_rounds"
    counts: dict[str, int] = {}
    for name in calls:
        counts[name] = counts.get(name, 0) + 1
    # did the agent go and fetch what it had already been handed?
    injected_paths = {w["path"] for w in task["windows"]} if spec["arm"] in {"oracle"} else set()
    re_read = sum(1 for name in calls if name == "read_file")
    return dict(spec, rounds=rounds, calls=calls, counts=counts,
                n_read_file=counts.get("read_file", 0), n_glob=counts.get("glob", 0),
                n_grep=counts.get("grep", 0), locate_calls=counts.get("glob", 0) + counts.get("grep", 0),
                re_read_after_injection=(re_read if injected_paths else None),
                injected_chars=injected_chars, result_bytes=result_bytes,
                input_tokens=in_tok, output_tokens=out_tok, cached_tokens=cached,
                wall_s=round(time.perf_counter() - started, 2), stopped=stopped,
                answer=answer_text[:500], correct=grade(answer_text, task["must"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=8)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--tasks", default=",".join(TASKS))
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", default=str(REPO / "research" / "07_task_dag" / "data" / "prewarm"
                                             / "stage1.jsonl"))
    parser.add_argument("--topup", action="store_true",
                        help="only run (arm, task, rep) cells that have no successful record yet")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    specs = [{"arm": arm, "task": task, "rep": rep}
             for arm in args.arms.split(",") for task in args.tasks.split(",")
             for rep in range(args.reps)]
    if args.topup and out.exists():
        done = set()
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("stopped") == "answered":
                    done.add((row["arm"], row["task"], row["rep"]))
        specs = [s for s in specs if (s["arm"], s["task"], s["rep"]) not in done]
    random.Random(20260916).shuffle(specs)      # arm order must not confound wall time
    print(f"{len(specs)} trials -> {out}")
    if args.dry_run:
        for arm in args.arms.split(","):
            for task in args.tasks.split(","):
                blocks, chars = injection(arm, TASKS[task])
                print(f"  {arm:10s} {task}  pairs={len(blocks) // 2}  injected_chars={chars}")
        return 0

    client, model = client_and_model()
    lock = threading.Lock()
    handle = out.open("a", encoding="utf-8")

    def work(spec):
        row = run_one(spec, client, model)
        with lock:
            handle.write(json.dumps(row) + "\n")
            handle.flush()
        print(f"  {row['arm']:10s} {row['task']} rep{row['rep']} rounds={row['rounds']} "
              f"reads={row['n_read_file']} correct={row['correct']} {row['wall_s']}s", flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(work, specs))
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
