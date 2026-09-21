#!/usr/bin/env python3
"""Scripted half of the three-arm inheritance experiment: how far back up the chain is worth reaching?

    python3 s15_integrated_harness/scripts/inherit_loop.py --reps 8 --concurrency 3

One agent, real tools executing against this repository, shaped like the LAST task of a three-stage
chain  A -> B -> C.  The question always needs a fact from the document A read AND a fact from the
document B read, and the agent is never told either path, so a baseline has to locate and open both.

  none        C is handed nothing                         (baseline)
  dag         C is handed what B read                      -- direct predecessors only
  ancestors   C is handed what A read AND what B read      -- the transitive closure

That is the whole contrast: `dag` closes half the gap by construction, `ancestors` closes all of it.
If reaching past the direct predecessor were worthless, `dag` and `ancestors` would land together.

Injected blocks are the exact assistant `tool_use` / user `tool_result` pair a real read_file would
have produced (via the harness's own `run_read` semantics), ordered oldest-ancestor-first.
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

MAX_ROUNDS = 14
MAX_TOKENS = 1500
SYSTEM = ("You are a coding agent working in the repository {repo}. Act, don't explain. "
          "Use the tools to find what you need, then call answer exactly once.")

H = "s15_integrated_harness"
# Each task: `grand` is what the chain's FIRST stage read, `direct` what the SECOND stage read.
# The question needs a fact from each, and names neither path.
# Each task: `grand` is what the chain's FIRST stage read, `direct` what the SECOND stage read.
# Every task is built so that SOME graded facts live only in `grand` and SOME only in `direct`
# (verified by scripts/inherit_check.py).  That split is what separates the arms: `dag` can answer
# the direct half from what it was handed and must go looking for the rest; `ancestors` has both.
# No path is ever named in the question, so a baseline has to locate both documents as well.
TASKS = {
    "T1": {
        "question": ("In this repository's integrated harness, about the context-compaction pipeline, "
                     "give all four of: (a) the numeric character shrink target the pipeline aims "
                     "down to, (b) the name of the constant fixing how many of the newest tool "
                     "results are never touched, (c) how many messages the history must exceed "
                     "before the archiving layer runs, and (d) the character size of the previews "
                     "that replace the largest remaining results."),
        "grand":  [{"path": f"{H}/DESIGN.md", "offset": 28, "limit": 27}],
        "direct": [{"path": f"{H}/ARCHITECTURE.md", "offset": 230, "limit": 22}],
        "must_grand":  [["409600"], ["KEEP_RECENT_TOOL_RESULTS"]],
        "must_direct": [["50messages", "50message"], ["1000char"]],
    },
    "T2": {
        "question": ("In this repository's integrated harness, about the hook and permission layer, "
                     "give all four of: (a) the name of the built-in PostToolUse hook, (b) the "
                     "character count above which it warns, (c) one command string on the bash deny "
                     "list, and (d) the name of the table that authorizes MCP tools."),
        "grand":  [{"path": f"{H}/GLOSSARY.md", "offset": 87, "limit": 12}],
        "direct": [{"path": f"{H}/ARCHITECTURE.md", "offset": 168, "limit": 24}],
        "must_grand":  [["large_output_hook"], ["100000"]],
        "must_direct": [["mkfs", "ddif=", "sudo"], ["MCP_HOST_POLICY"]],
    },
    "T3": {
        "question": ("In this repository's integrated harness, give all four of: (a) the numeric "
                     "target_chars the tool-result fitting layer is called with, (b) the name of "
                     "that keyword parameter, and the two HTTP status codes the model call retries "
                     "with exponential backoff, namely (c) the rate-limit one and (d) the overload "
                     "one."),
        "grand":  [{"path": f"{H}/DESIGN.md", "offset": 55, "limit": 60}],
        "direct": [{"path": f"{H}/README.md", "offset": 150, "limit": 18}],
        "must_grand":  [["40000"], ["target_chars"]],
        "must_direct": [["429"], ["529"]],
    },
    "T4": {
        "question": ("In this repository's integrated harness, give all three of: (a) the character "
                     "size of the persisted previews the tool-result fitting layer produces, (b) the "
                     "name of the constant that raises the output-token cap on escalation, and (c) "
                     "the numeric shrink target the pipeline aims down to."),
        "grand":  [{"path": f"{H}/GLOSSARY.md", "offset": 42, "limit": 16}],
        "direct": [{"path": f"{H}/DESIGN.md", "offset": 28, "limit": 27}],
        "must_grand":  [["1000char"]],
        "must_direct": [["ESCALATED_MAX_TOKENS"], ["409600"]],
    },
}
for _t in TASKS.values():
    _t["must"] = _t["must_grand"] + _t["must_direct"]

ARMS = ["none", "dag", "ancestors"]


# ---------------------------------------------------------------- tools (real execution)
def _safe(path: str) -> Path:
    resolved = (REPO / path).resolve()
    if not resolved.is_relative_to(REPO.resolve()):
        raise ValueError("path escapes repository")
    return resolved


def read_window(path: str, offset: int = 0, limit: int | None = None) -> str:
    """The harness's own read_file semantics (code.py run_read)."""
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
        if path:
            targets = [_safe(path)]
        else:
            targets = [p for p in (REPO / H).glob("*") if p.is_file() and p.suffix in {".md", ".py"}]
        needle = re.compile(pattern)
        out = []
        for target in targets:
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for number, line in enumerate(lines, 1):
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
def pair(payload: dict, text: str, tag: str) -> list[dict]:
    use_id = f"prewarm_{tag}"
    return [{"role": "assistant",
             "content": [{"type": "tool_use", "id": use_id, "name": "read_file", "input": payload}]},
            {"role": "user",
             "content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}]


def windows_for(arm: str, task: dict) -> list[dict]:
    if arm == "none":
        return []
    if arm == "dag":
        return list(task["direct"])
    # oldest ancestor first, exactly as profile_run.py's ancestors arm orders them
    return list(task["grand"]) + list(task["direct"])


def injection(arm: str, task: dict) -> tuple[list[dict], int, set]:
    blocks, total, paths = [], 0, set()
    for index, w in enumerate(windows_for(arm, task)):
        text = read_window(w["path"], w.get("offset", 0), w.get("limit"))
        payload = {k: v for k, v in w.items() if v is not None}
        total += len(text)
        paths.add(w["path"])
        blocks.extend(pair(payload, text, f"{arm}{index}"))
    return blocks, total, paths


def _norm(s: str) -> str:
    return s.lower().replace(",", "").replace(" ", "").replace("-", "")


def grade(answer: str, must: list[list[str]]) -> tuple[bool, int]:
    text = _norm(answer)
    hits = sum(1 for g in must if any(_norm(a) in text for a in g))
    return hits == len(must), hits


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
            if attempt == tries - 1 or not any(c in str(exc) for c in ("429", "529", "overload")):
                raise
            time.sleep(delay + random.random())
            delay *= 1.8
    raise RuntimeError("unreachable")


def run_one(spec: dict, client, model) -> dict:
    task = TASKS[spec["task"]]
    blocks, injected_chars, injected_paths = injection(spec["arm"], task)
    messages = [{"role": "user", "content": task["question"]}] + blocks
    calls: list[str] = []
    read_paths: list[str] = []
    rounds = in_tok = out_tok = cached = result_bytes = 0
    first_prompt_tok = 0
    answer_text = ""
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
        if rounds == 1:
            first_prompt_tok = response.usage.input_tokens
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
            if block.name == "read_file":
                read_paths.append((block.input or {}).get("path", ""))
            if block.name == "answer":
                answer_text = (block.input or {}).get("answer", "")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Answer recorded."})
                finished = True
                continue
            handler = HANDLERS.get(block.name)
            t1 = time.perf_counter()
            out = (f"Error: unknown tool {block.name}" if handler is None
                   else str(handler(**(block.input or {}))))
            tool_s += time.perf_counter() - t1
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
    # did it go and fetch a document it had already been handed?  Count re-reads of the paths that
    # were ACTUALLY injected -- not every read_file.  The old form counted the dag arm legitimately
    # fetching the GRAND document it was never handed, so the published "dag 75% re-fetch" line was
    # mostly ordinary work, not distrust of the handoff.
    injected_set = set(injected_paths)
    refetch = sum(1 for path in read_paths if path in injected_set)
    ok, hits = grade(answer_text, task["must"])
    # split the score: the DIRECT half is what the `dag` arm was handed, the GRAND half is what only
    # the `ancestors` arm was handed.  This is where the arms should separate, not in the total.
    _, hits_grand = grade(answer_text, task["must_grand"])
    _, hits_direct = grade(answer_text, task["must_direct"])
    wall = time.perf_counter() - started
    return dict(spec, rounds=rounds, calls=calls,
                n_read_file=counts.get("read_file", 0), n_glob=counts.get("glob", 0),
                n_grep=counts.get("grep", 0),
                locate_calls=counts.get("glob", 0) + counts.get("grep", 0),
                refetch_after_injection=(refetch if injected_paths else None),
                injected_chars=injected_chars, injected_paths=sorted(injected_paths),
                result_bytes=result_bytes, input_tokens=in_tok, output_tokens=out_tok,
                cached_tokens=cached, first_prompt_tokens=first_prompt_tok,
                model_s=round(model_s, 2), tool_s=round(tool_s, 3),
                other_s=round(wall - model_s - tool_s, 3),
                wall_s=round(wall, 2), stopped=stopped,
                answer=answer_text[:500], hits=hits, total=len(task["must"]), correct=ok,
                hits_grand=hits_grand, total_grand=len(task["must_grand"]),
                hits_direct=hits_direct, total_direct=len(task["must_direct"]))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reps", type=int, default=8)
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--tasks", default=",".join(TASKS))
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--out", default=str(REPO / H / "traces" / "inherit" / "stage1.jsonl"))
    p.add_argument("--topup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    specs = [{"arm": a, "task": t, "rep": r}
             for a in args.arms.split(",") for t in args.tasks.split(",")
             for r in range(args.reps)]
    if args.topup and out.exists():
        done = {(x["arm"], x["task"], x["rep"])
                for x in (json.loads(l) for l in out.read_text().splitlines() if l.strip())
                if x.get("stopped") in ("answered", "max_rounds")}
        specs = [s for s in specs if (s["arm"], s["task"], s["rep"]) not in done]
    random.Random(20260918).shuffle(specs)
    print(f"{len(specs)} trials -> {out}")
    if args.dry_run:
        for a in args.arms.split(","):
            for t in args.tasks.split(","):
                b, c, paths = injection(a, TASKS[t])
                print(f"  {a:10s} {t}  pairs={len(b)//2}  injected_chars={c:6d}  {sorted(paths)}")
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
              f"reads={row['n_read_file']} acc={row['hits']}/{row['total']} {row['wall_s']}s",
              flush=True)
        return row

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(work, specs))
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
