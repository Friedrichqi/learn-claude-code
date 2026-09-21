#!/usr/bin/env python3
"""Real published agent trajectories -> the canonical read-item form this repo already analyses.

    python3 scripts/traj_ingest.py --corpus open_swe --split sweagent --limit 500

Every number in the offline half of this study comes through here, so what counts as a "read" is
defined once, in `read_key`, and the three harnesses are held to the same definition.

WHY THESE CORPORA.  The Sept 17-18 study hand-authored every workload, so its headline rests on
prompts we wrote to make the effect visible.  These are runs of real agent harnesses on real
benchmarks, published by their authors:

    nvidia/Open-SWE-Traces v1.1   openhands 80,630 / sweagent 77,261 / minisweagent 95,291
    nebius/SWE-rebench-openhands-trajectories   67,074
    SWE-bench/SWE-smith-trajectories            24-26k

Three harnesses over one task family makes the HARNESS a controllable variable, which no previous
experiment here could do.

WHAT A ROUND IS.  One assistant message carrying tool calls, plus the tool results that answer it.
That is the same unit `inherit_analyze.py` counts as a round (one `model_request`), so counts are
comparable across the live and replayed halves.

THE PREDECESSOR RELATION HERE IS THE ROUND PREFIX, NOT A TASK GRAPH.  A single-agent trajectory has
no task board: for round t the direct predecessor is round t-1 and the ancestors are rounds 1..t-1.
This is the relation a real harness actually evicts over under a context limit, which is why the
budget and eviction experiments belong on this side.  It is NOT the Lead's `blockedBy` graph, and
nothing computed here may be pooled with the live arm numbers.

THE THREE HARNESSES DISAGREE ABOUT EVERYTHING EXCEPT THE WORK:
  sweagent       tools `bash` + `str_replace_editor`; observations prefixed "OBSERVATION:\\n"
  openhands      tools `execute_bash` + `str_replace_editor`; observations raw
  minisweagent   one `bash` tool; observations are JSON {"returncode":..,"output":".."}
`observation_text` normalises all three so byte counts mean the same thing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_redundancy import classify_bash                                      # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "s15_integrated_harness" / "traces" / "realworld"

CORPORA = {
    "open_swe": ("nvidia/Open-SWE-Traces", "v1.1", ["openhands", "sweagent", "minisweagent"]),
    "swe_rebench": ("nebius/SWE-rebench-openhands-trajectories", None, ["train"]),
    "swe_smith": ("SWE-bench/SWE-smith-trajectories", None, ["tool", "xml", "ticks"]),
}

BASH_TOOLS = {"bash", "execute_bash", "run_bash", "shell", "run"}
# smolagents-style agents call their tools from inside a Python snippet rather than through the
# provider's tool interface, so the snippet is the call
CODE_TOOLS = {"python_interpreter", "execute_ipython_cell", "run_python", "python", "run_ipython"}
MUTATING_CODE = re.compile(
    r"\bopen\s*\([^)]*['\"][wa]|\bos\.(?:remove|unlink|rename|replace|rmdir|makedirs|mkdir)\b"
    r"|\bshutil\.|\bwrite_text\b|\bwrite_bytes\b|\bto_csv\b|\bsavefig\b|\bfinal_answer\b"
    r"|\bsubprocess\.|\b(?:pip|apt)\s+install\b")
EDITOR_TOOLS = {"str_replace_editor", "str_replace_edit", "edit_file", "file_editor", "editor"}
# editor subcommands that return file content rather than changing it
EDITOR_READS = {"view", "read"}


def _loads(value):
    """Corpora disagree about whether nested columns are JSON strings or parsed objects."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text") or b.get("content") or "") if isinstance(b, dict) else len(str(b))
                   for b in content)
    return 0


def observation_text(content) -> str:
    """One tool result as the bytes the model actually saw, with each harness's wrapper removed."""
    if content is None:
        return ""
    if isinstance(content, list):                       # anthropic-style content blocks
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            else:
                parts.append(str(block))
        content = "\n".join(p for p in parts if isinstance(p, str))
    if not isinstance(content, str):
        content = str(content)
    text = content
    if text.startswith("OBSERVATION:\n"):               # sweagent
        text = text[len("OBSERVATION:\n"):]
    elif text.startswith("OBSERVATION:"):
        text = text[len("OBSERVATION:"):].lstrip("\n")
    stripped = text.lstrip()
    if stripped.startswith("{") and '"output"' in stripped[:200]:   # minisweagent
        parsed = _loads(stripped)
        if isinstance(parsed, dict) and "output" in parsed:
            text = parsed["output"] if isinstance(parsed["output"], str) else str(parsed["output"])
    return text


GUTTER = re.compile(r"^\s*\d+\t")          # the `cat -n` line-number gutter
CAT_N_HEADER = re.compile(r"^Here's the result of running `cat -n` on (\S+?):\s*$", re.M)
DIR_HEADER = re.compile(r"^Here's the files and directories up to \d+ levels deep in (\S+?),", re.M)


def read_key(tool: str, args: dict, observation: str) -> tuple | None:
    """The identity of a retrievable observation, or None if this call is not a read.

    An engine holds or drops whole observations, so the key has to be the thing it would hold.  Two
    calls that fetch the same span of the same file get the same key even when one went through the
    editor tool and the other through `cat`, because to a cache they are the same bytes -- that
    equivalence is the entire reason a handoff can remove a round.

    Writes, edits, test runs and submissions are deliberately NOT reads: their output is a
    consequence of the action, so holding it does not save a later agent from having to act."""
    name = (tool or "").lower()
    if name in EDITOR_TOOLS:
        command = (args.get("command") or "view").lower()
        if command not in EDITOR_READS:
            return None
        path = args.get("path") or args.get("file_path")
        if not path:
            return None
        span = args.get("view_range") or args.get("range")
        span = tuple(span) if isinstance(span, (list, tuple)) else None
        return ("file", str(path), span)
    if name in BASH_TOOLS:
        command = args.get("command") or args.get("cmd") or ""
        if not isinstance(command, str) or not command.strip():
            return None
        is_reader, paths = classify_bash(command)
        if not is_reader:
            return None
        if len(paths) == 1:
            # a plain `cat path` is the same bytes as `str_replace_editor view path`
            return ("file", paths[0], None)
        return ("shell", " ".join(command.split()), None)
    if name in CODE_TOOLS:
        code = args.get("code") or args.get("command") or args.get("input") or ""
        if not isinstance(code, str) or not code.strip():
            return None
        if MUTATING_CODE.search(code):
            return None
        # provenance inside a snippet is unknowable in general, so key on the snippet itself --
        # the same treatment `classify_bash` already gives a compound shell pipeline
        return ("shell", " ".join(code.split()), None)
    return api_key(name, args)


# A retrieval tool returns state; a mutation changes it.  Holding a retrieval's answer can save a
# later round, holding a mutation's receipt cannot -- the act still has to happen.  Verb prefixes
# are how every API-style agent benchmark names these: tau-bench's airline domain splits exactly
# this way (get_reservation_details / get_user_details / search_direct_flight against
# book_reservation / cancel_reservation / update_reservation_*), and so do AppWorld and BFCL.
READ_VERBS = ("get", "list", "search", "find", "read", "view", "fetch", "lookup", "look_up",
              "query", "show", "describe", "check", "retrieve", "load", "download", "browse",
              "inspect", "count", "calculate", "compute")
WRITE_VERBS = ("create", "update", "delete", "remove", "book", "cancel", "send", "modify",
               "insert", "write", "edit", "submit", "transfer", "execute", "run", "install",
               "set", "add", "post", "put", "patch", "apply", "reserve", "pay", "upload",
               "finish", "complete", "terminate")
# tools that carry no retrievable content at all: scratchpads and control signals
NON_CONTENT = {"think", "thinking", "finish", "submit", "done", "stop", "transfer_to_human_agents"}


def api_key(name: str, args: dict) -> tuple | None:
    """A read for any other tool-calling agent: tau-bench, AppWorld, BFCL, MCP servers.

    Keyed on (tool, canonical arguments) because for an API the arguments ARE the address -- two
    `get_reservation_details` calls with the same id fetch the same record, exactly as two views of
    one file span fetch the same bytes.  This is not a detail: in the tau-bench airline runs
    `get_reservation_details` is called 1,204 times across 50 tasks, so if API calls were skipped
    the entire non-coding half of the evidence would read as having no redundancy."""
    if not name or name in NON_CONTENT:
        return None
    head = name.lower().split("_")[0]
    if head in WRITE_VERBS or name.lower().startswith(WRITE_VERBS):
        return None
    if head not in READ_VERBS and not name.lower().startswith(READ_VERBS):
        return None
    try:
        canon = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canon = str(args)
    return ("api", name, canon)


def parse_rounds(messages: list) -> list[dict]:
    """Messages -> rounds.  One round = one assistant turn with tool calls plus its results."""
    rounds: list[dict] = []
    pending: dict | None = None
    ctx_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in {"system", "user"} and pending is None:
            ctx_chars += _chars(message.get("content"))
        if role == "assistant":
            calls = _loads(message.get("tool_calls")) or []
            if not isinstance(calls, list) or not calls:
                ctx_chars += _chars(message.get("content"))
                continue
            content = message.get("content")
            pending = {"idx": len(rounds), "calls": [], "by_id": {},
                       # the prompt this round was actually issued against: everything before it.
                       # Composed from the message parts, never from a provider `usage` field --
                       # z.ai reports input_tokens EXCLUDING cache hits, which made metric 5 read
                       # 848% once.
                       "ctx_chars": ctx_chars,
                       "out_chars": len(content) if isinstance(content, str) else 0}
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") or {}
                tool = fn.get("name") or call.get("name") or ""
                raw = fn.get("arguments") if "arguments" in fn else call.get("input")
                args = _loads(raw) or {}
                if not isinstance(args, dict):
                    # smolagents puts a raw Python snippet in `arguments`, not JSON.  Discarding it
                    # left every GAIA and CORE-bench call with empty arguments, so nothing could be
                    # keyed and both benchmarks read as having no retrievals at all.
                    args = {"code": raw} if isinstance(raw, str) and raw.strip() else {}
                entry = {"tool": tool, "args": args, "id": call.get("id")}
                pending["calls"].append(entry)
                if call.get("id"):
                    pending["by_id"][call["id"]] = entry
            rounds.append(pending)
            ctx_chars += pending["out_chars"]
        elif role in {"tool", "user"} and pending is not None:
            text = observation_text(message.get("content"))
            if role == "user" and not message.get("tool_call_id"):
                continue                                # a genuine user turn, not a tool result
            target = pending["by_id"].get(message.get("tool_call_id"))
            if target is None:
                target = next((c for c in pending["calls"] if "observation" not in c), None)
            if target is not None:
                target["observation"] = text
            ctx_chars += len(text)
    for rnd in rounds:
        rnd["read_bytes"] = sum(len(c.get("observation", "")) for c in rnd["calls"])
        rnd["tools"] = [c["tool"] for c in rnd["calls"]]
    return rounds


def line_digests(text: str) -> list[str]:
    """Per-line digests, so overlap can be measured at line granularity as well as whole-item.

    Exact-item matching is the strictest possible test and badly understates reuse: an agent that
    views lines 1-50 and later lines 1-100 of one file has fetched most of those bytes twice, but
    the two observations are not equal and their keys differ.  `input_redundancy` already offers
    whole/range/line/cdc granularities for this reason; digests keep the line view available offline
    without storing the corpus (~9 bytes a line, ~16 KB a trajectory).

    The `cat -n` gutter is stripped first, or the same source line read through two different tools
    -- or at two different offsets -- would hash differently for a reason that has nothing to do
    with its content."""
    out = []
    for line in text.split("\n"):
        body = GUTTER.sub("", line).rstrip()
        if not body:
            continue
        out.append(hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:8])
    return out


def to_items(rounds: list[dict]) -> list[dict]:
    """Every read in the trajectory, in order, with what it cost to obtain.

    `located_by` is the round of the earliest shell read whose output names this file.  Recovery
    cost has to carry it: re-reading a path you already know is one cheap round, but the
    find/grep round that DISCOVERED the path is a second one, and an item whose locating round is
    not charged to it looks cheap to evict when it is the most expensive thing in the pool."""
    items: list[dict] = []
    shell_seen: list[tuple[int, str]] = []          # (round, observation) for shell reads, in order
    for rnd in rounds:
        for call in rnd["calls"]:
            observation = call.get("observation", "")
            key = read_key(call["tool"], call["args"], observation)
            if key is None or not observation:
                continue
            located_by = None
            if key[0] == "file":
                path = key[1]
                base = path.rsplit("/", 1)[-1]
                for idx, text in shell_seen:
                    if idx >= rnd["idx"]:
                        break
                    if path in text or (len(base) > 3 and base in text):
                        located_by = idx
                        break
            items.append({
                "key": list(key), "round": rnd["idx"], "tool": call["tool"],
                "bytes": len(observation),
                "sha": hashlib.sha256(observation.encode("utf-8", "replace")).hexdigest()[:16],
                "lines": line_digests(observation),
                "out_chars": rnd["out_chars"],
                "located_by": located_by,
            })
            if key[0] == "shell":
                shell_seen.append((rnd["idx"], observation))
    return items


def ingest_row(row: dict, harness: str, corpus: str) -> dict | None:
    messages = _loads(row.get("messages") or row.get("trajectory"))
    if not isinstance(messages, list) or len(messages) < 4:
        return None
    rounds = parse_rounds(messages)
    items = to_items(rounds)
    if not rounds:
        return None
    return {
        "corpus": corpus, "harness": harness,
        "instance_id": row.get("instance_id") or row.get("trajectory_id"),
        "repo": row.get("repo"),
        "resolved": row.get("resolved") if row.get("resolved") is not None else row.get("exit_status"),
        "n_messages": len(messages), "n_rounds": len(rounds), "n_items": len(items),
        "read_bytes": sum(i["bytes"] for i in items),
        "rounds": [{"idx": r["idx"], "ctx_chars": r["ctx_chars"], "out_chars": r["out_chars"],
                    "n_calls": len(r["calls"]), "read_bytes": r["read_bytes"],
                    "tools": r["tools"]} for r in rounds],
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="open_swe", choices=sorted(CORPORA))
    ap.add_argument("--split", default="sweagent")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--resolved-only", action="store_true",
                    help="keep only trajectories whose original graded run succeeded")
    args = ap.parse_args()

    dataset, config, splits = CORPORA[args.corpus]
    if args.split not in splits:
        print(f"[ingest] split must be one of {splits}", file=sys.stderr)
        return 2
    from datasets import load_dataset                                           # noqa: PLC0415

    stream = load_dataset(dataset, config, split=args.split, streaming=True)
    out = args.out or (OUT_ROOT / args.corpus / f"{args.split}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)

    kept = skipped = 0
    with out.open("w") as fh:
        for row in stream:
            if kept >= args.limit:
                break
            try:
                record = ingest_row(row, args.split, args.corpus)
            except Exception as exc:                                            # noqa: BLE001
                print(f"[ingest] skip: {type(exc).__name__}: {exc}", file=sys.stderr)
                skipped += 1
                continue
            if record is None:
                skipped += 1
                continue
            if args.resolved_only and not record["resolved"]:
                skipped += 1
                continue
            fh.write(json.dumps(record) + "\n")
            kept += 1
            if kept % 100 == 0:
                print(f"[ingest] {kept}...", file=sys.stderr)
    print(f"[ingest] wrote {kept} trajectories ({skipped} skipped) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
