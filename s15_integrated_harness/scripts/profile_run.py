#!/usr/bin/env python3
"""Run one s15 session non-interactively for profiling and record an input-composition side log.

    python3 s15_integrated_harness/scripts/profile_run.py --label X1 \
        --prompt "Inspect ... do not delegate." [--prompt "follow-up"] [--followup-if-no-team "Confirmed, proceed."]
        [--max-seconds 900] [--quiet-seconds 30] [--allow-writes] [--trace-dir DIR]
        [--stream] [--write-root DIR --sandbox-from SRC] [--allow-python]

Behaviour
  * Imports s15_integrated_harness/code.py from the repository root (WORKDIR = repo root) and
    initialises the normal JSONL trace, so the run is analysable with trace_view.py and
    scripts/file_read_reuse.py exactly like an interactive session.
  * Auto-approves shell permission prompts, but denies mutating shell commands and (unless
    --allow-writes or --write-root) write_file/edit_file, so profiling runs are read-only on the
    repository.  --write-root DIR allows write_file/edit_file only below DIR; --sandbox-from SRC
    copies SRC into DIR before the run (wiping DIR first) and archives DIR next to the trace as
    <trace-dir>/<label>.sandbox afterwards.  --allow-python additionally lets asynchronous
    teammates run python3 commands (plus cd/timeout/env prefixes) that pass the mutation filter.
    create_worktree is always denied so every agent works in the repository directory.
  * Wraps the traced Messages client once more and writes <trace>.inputs.jsonl: for EVERY model
    call, how many characters of the request were tool_result blocks of each tool (read_file,
    bash, glob, ...), which read_file paths were present, how many results had already been
    replaced by compaction placeholders, plus provider usage.  This measures how many times the
    same file content is re-sent to the model (input reuse), which the trace alone cannot show.
  * --stream sends every request as a streaming request (anthropic `messages.stream`) and records
    per call the client-side time to the first content block (queue + prefill), the time of the
    last stream event and the type of the first block, so the model call splits into a
    prefill-side and a decode-side part.  The final message object is identical in shape to the
    non-streaming response, so the harness is unaffected.
  * Emits `profile_timing` trace events around the lead's context-preparation functions
    (update_context = memory recall, assemble_system_prompt, assemble_tool_pool,
    remember_after_turn = memory extraction, consume_lead_inbox, format_team_events) and the
    teammates' inbox reads, so the gap between a tool result and the next model request can be
    attributed (scripts/latency_breakdown.py).
  * Also writes <trace>.reads.jsonl: the FULL text of every tool_result block the first time it
    appears in an agent's request (tool name and arguments resolved from the matching tool_use
    block), plus a record whenever an already-sent result is rewritten (compaction placeholder).
    Together with the per-call list of tool_result ids in the inputs sidecar this reconstructs the
    exact bytes of file input each agent's model calls contained -- the basis for byte-level
    redundancy measurements (scripts/input_redundancy.py).
  * Drives one or more user turns, then waits until teammates are idle (or --max-seconds), sends
    them a graceful shutdown while holding the lead lock, and closes the trace.
  * Fresh .memory/.tasks/.mailboxes/.transcripts/.task_outputs state at the repo root per run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import os
import random
import re
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HARMLESS_REDIRECT = re.compile(r"\d?>\s*&\s*\d|\d?>\s*/dev/null|<<\s*'?\w+'?")
MUTATE_CMD = re.compile(
    r"(\brm\b|\bmv\b|\bcp\b|\bsed\s+-i|"
    r"\bgit\s+(?:add|commit|checkout|reset|clean|stash|push|pull|rebase|merge|rm|mv|worktree)\b|"
    r"\bpip3?\s+(?:install|uninstall)\b|\bmkdir\b|\btouch\b|\bchmod\b|\bchown\b|\btruncate\b|"
    r"\bpython3?\b[^|;&]*\bopen\([^)]*['\"][wa]|\bdd\b|\bln\b|\bunlink\b|\brmdir\b|"
    r"\bos\.(?:remove|unlink|rename|replace|rmdir|makedirs|mkdir)\b|\bshutil\.|\bwrite_text\b|\bwrite_bytes\b)"
)
RATE_LIMIT_ATTEMPTS = 6
RUN_STATE = [".memory", ".tasks", ".mailboxes", ".transcripts", ".task_outputs", ".scheduled_tasks.json"]
PREP_FUNCTIONS = ("update_context", "assemble_system_prompt", "assemble_tool_pool",
                  "remember_after_turn", "consume_lead_inbox", "format_team_events")
PYTHON_PREFIXES = {"cd", "timeout", "env", "true"}


QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
REDIRECT = re.compile(r">(?!>)\s*\S|>>|\btee\b")


def is_mutating(command: str) -> bool:
    """Mutating shell command?  Redirections are only looked for OUTSIDE quoted strings, so a
    python3 -c "... if a > b ..." comparison or a grep pattern is not mistaken for one (the
    latency_profiling runs of 2026-09-12 still used the stricter form and denied ~20 such calls)."""
    text = HARMLESS_REDIRECT.sub(" ", command)
    if REDIRECT.search(QUOTED.sub(" ", text)):
        return True
    return bool(MUTATE_CMD.search(text))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="experiment label written into the trace run_start data")
    parser.add_argument("--prompt", action="append", required=True, help="user turn(s), in order")
    parser.add_argument("--followup-if-no-team", default=None,
                        help="extra user turn sent once if the first turn spawned no teammate")
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument("--quiet-seconds", type=float, default=30.0,
                        help="how long teammates must all be idle before the run is closed")
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--write-root", default=None,
                        help="repo-relative directory; write_file/edit_file are allowed only below it")
    parser.add_argument("--sandbox-from", default=None,
                        help="repo-relative directory copied into --write-root before the run (write root wiped first) "
                             "and archived as <trace-dir>/<label>.sandbox afterwards")
    parser.add_argument("--allow-python", action="store_true",
                        help="let asynchronous teammates run python3 commands (and cd/timeout/env prefixes) that pass "
                             "the mutation filter; the lead is auto-approved anyway")
    parser.add_argument("--stream", action="store_true",
                        help="use streaming requests and record time-to-first-token per model call")
    parser.add_argument("--no-prep-timing", action="store_true",
                        help="do not emit profile_timing trace events around context-preparation functions")
    parser.add_argument("--trace-dir", default=str(REPO / "s15_integrated_harness" / "traces" / "reuse_profiling"))
    parser.add_argument("--keep-state", action="store_true", help="do not wipe .memory/.tasks/... before the run")
    parser.add_argument("--no-timestamp", action="store_true",
                        help="intervention: drop the per-second 'Current time' line from the lead system prompt")
    parser.add_argument("--context-limit", type=int, default=None,
                        help="intervention: override CONTEXT_LIMIT (chars) of the lead compaction pipeline")
    parser.add_argument("--no-retry-429", action="store_true",
                        help="disable the driver-level retry of provider 429s (the harness itself retries only lead calls, 3x)")
    parser.add_argument("--trace-output", choices=["summary", "full"], default="summary",
                        help="HARNESS_TRACE_OUTPUT mode: 'full' stores every tool result verbatim in the trace")
    parser.add_argument("--no-reads-log", action="store_true",
                        help="do not write the <trace>.reads.jsonl content sidecar")
    args = parser.parse_args()
    if args.sandbox_from and not args.write_root:
        parser.error("--sandbox-from requires --write-root")
    return args


def wipe_run_state():
    for name in RUN_STATE:
        target = REPO / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def load_harness():
    path = REPO / "s15_integrated_harness" / "code.py"
    spec = importlib.util.spec_from_file_location("s15_profiled_harness", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["s15_profiled_harness"] = module
    spec.loader.exec_module(module)
    return module


class StreamingMessages:
    """Drop-in for the raw `client.messages` resource: `create()` streams and records timing.

    The timing of the most recent call is left in `local.timing` (thread-local, so concurrent
    teammate threads never mix) and picked up by the profiler wrapper around the traced client.
    """

    def __init__(self, raw_messages, local: threading.local):
        self._raw = raw_messages
        self._local = local

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def create(self, **kwargs):
        kwargs = dict(kwargs)
        kwargs.pop("stream", None)
        started = time.perf_counter()
        t_message_start = t_first_block = t_first_visible = t_last = None
        first_block_type = None
        events = blocks = 0
        with self._raw.stream(**kwargs) as stream:
            for event in stream:
                now = time.perf_counter()
                events += 1
                kind = getattr(event, "type", None)
                if kind == "message_start":
                    t_message_start = now
                elif kind == "content_block_start":
                    blocks += 1
                    block_type = getattr(getattr(event, "content_block", None), "type", None)
                    if t_first_block is None:
                        t_first_block, first_block_type = now, block_type
                    if t_first_visible is None and block_type in {"text", "tool_use"}:
                        t_first_visible = now
                elif kind == "content_block_delta" and t_first_block is None:
                    t_first_block = now
                    first_block_type = getattr(getattr(event, "delta", None), "type", None)
                t_last = now
            response = stream.get_final_message()
        ended = time.perf_counter()
        ms = lambda t: None if t is None else round((t - started) * 1000, 3)  # noqa: E731
        self._local.timing = {
            "streamed": True,
            "message_start_ms": ms(t_message_start),
            "ttft_ms": ms(t_first_block if t_first_block is not None else ended),
            "first_visible_ms": ms(t_first_visible),
            "first_block_type": first_block_type,
            "last_event_ms": ms(t_last),
            "stream_end_ms": ms(ended),
            "events": events,
            "content_blocks": blocks,
        }
        return response


def relaxed_read_only(mod):
    """Classifier that also accepts python invocations for asynchronous (teammate) turns."""
    original = mod._is_read_only_command

    def classify(command: str) -> bool:
        if original(command):
            return True
        if not isinstance(command, str) or is_mutating(command):
            return False
        plain = HARMLESS_REDIRECT.sub(" ", mod._strip_quoted_text(command))
        plain = re.sub(r'"[^"]*"', "", plain)  # python -c "..." bodies are one argument, not segments
        if ">" in plain or "`" in plain or "$(" in plain:
            return False
        saw_python = False
        for segment in re.split(r"\|\||&&|[;&|\n]", plain):
            words = segment.split()
            while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
                words.pop(0)
            if not words:
                return False
            name = Path(words[0]).name
            while name in {"timeout", "env", "command"} and len(words) > 1:
                words = words[1:]
                if name == "timeout" and words and re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", words[0]):
                    words = words[1:]
                while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
                    words.pop(0)
                name = Path(words[0]).name if words else ""
            if name in {"python", "python3"}:
                saw_python = True
            elif name in PYTHON_PREFIXES:
                continue
            elif not original(" ".join(words)):
                return False
        return saw_python

    return classify


def main() -> int:
    args = parse_args()
    os.chdir(REPO)
    os.environ["HARNESS_TRACE"] = "1"
    os.environ["HARNESS_TRACE_DIR"] = args.trace_dir
    os.environ["HARNESS_TRACE_OUTPUT"] = args.trace_output
    if not args.keep_state:
        wipe_run_state()

    write_root = None
    if args.write_root:
        write_root = (REPO / args.write_root).resolve()
        if not write_root.is_relative_to(REPO):
            print(f"[profile] --write-root must stay inside the repository: {write_root}", flush=True)
            return 2
    if args.sandbox_from:
        source = (REPO / args.sandbox_from).resolve()
        if write_root.exists():
            shutil.rmtree(write_root)
        shutil.copytree(source, write_root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"[profile] sandbox {source} -> {write_root}", flush=True)

    mod = load_harness()
    mod.CLI_ACTIVE = False
    trace = mod.initialize_tracing("s15")
    if args.context_limit:
        mod.CONTEXT_LIMIT = args.context_limit
    if args.no_timestamp:
        original_assemble = mod.assemble_system_prompt

        def assemble_without_time(context):
            prompt = original_assemble(context)
            return "\n\n".join(section for section in prompt.split("\n\n")
                               if not section.startswith("Current time:"))

        mod.assemble_system_prompt = assemble_without_time
    if args.allow_python:
        mod._is_read_only_command = relaxed_read_only(mod)
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  cwd=REPO, check=True).stdout.strip()
    except Exception:
        git_head = os.environ.get("PROFILE_GIT_HEAD")
    trace.emit("profile_meta", {"label": args.label, "prompts": args.prompt,
                                "allow_writes": args.allow_writes, "driver": "scripts/profile_run.py",
                                "no_timestamp": args.no_timestamp, "context_limit": mod.CONTEXT_LIMIT,
                                "git_head": git_head, "trace_output": args.trace_output,
                                "reads_log": not args.no_reads_log, "stream": args.stream,
                                "write_root": args.write_root, "sandbox_from": args.sandbox_from,
                                "allow_python": args.allow_python, "prep_timing": not args.no_prep_timing,
                                "repo": str(REPO)})
    inputs_path = Path(str(trace.path).removesuffix(".jsonl") + ".inputs.jsonl")
    reads_path = Path(str(trace.path).removesuffix(".jsonl") + ".reads.jsonl")
    inputs_lock = threading.Lock()
    print(f"[profile] label={args.label} trace={trace.path} inputs={inputs_path}", flush=True)
    if not args.no_reads_log:
        print(f"[profile] reads={reads_path}", flush=True)

    # -- context-preparation timing ------------------------------------------------------------
    if not args.no_prep_timing:
        def timed(name, fn, lead_side: bool):
            def wrapper(*fargs, **fkwargs):
                started = time.perf_counter()
                try:
                    return fn(*fargs, **fkwargs)
                finally:
                    data = {"fn": name, "duration_ms": round((time.perf_counter() - started) * 1000, 3)}
                    if name == "read_inbox" and fargs:
                        data["inbox"] = str(fargs[0])
                    if lead_side:
                        trace.emit("profile_timing", data, agent_id="agent-root", agent_kind="lead")
                    else:
                        trace.emit("profile_timing", data)
            return wrapper

        for name in PREP_FUNCTIONS:
            setattr(mod, name, timed(name, getattr(mod, name), True))
        mod.BUS.read_inbox = timed("read_inbox", mod.BUS.read_inbox, False)

    # -- permission policy: auto-approve, but keep the repository read-only ----------------
    calls: dict[str, dict] = {}
    denials = Counter()
    rate_limited = Counter()
    original_permission = mod.permission_hook

    def record_call(block):
        calls[getattr(block, "id", None)] = {"tool": block.name, "input": dict(block.input or {})}
        return None

    def guarded_permission(block):
        if block.name == "bash":
            command = block.input.get("command", "")
            if isinstance(command, str) and is_mutating(command):
                denials["bash-mutating"] += 1
                return "Permission denied by user: this profiling session is read-only; do not modify files or git state."
        if block.name in {"write_file", "edit_file"}:
            if write_root is not None:
                path = block.input.get("path", "")
                try:
                    resolved = (REPO / str(path)).resolve()
                except Exception:
                    resolved = None
                if resolved is None or not resolved.is_relative_to(write_root):
                    denials[f"{block.name}-outside-root"] += 1
                    return (f"Permission denied by user: in this session files may only be written below "
                            f"{args.write_root}/; do not modify anything else.")
            elif not args.allow_writes:
                denials[block.name] += 1
                return "Permission denied by user: this profiling session is read-only; report findings in your reply instead."
        if block.name == "create_worktree":
            denials["create_worktree"] += 1
            return "Permission denied by user: do not create worktrees in this session; work in the repository directory."
        return original_permission(block)

    hooks = mod.HOOKS["PreToolUse"]
    hooks[hooks.index(original_permission)] = guarded_permission
    hooks.insert(0, record_call)
    mod.CONSOLE.reader = lambda prompt: "y"

    # -- input-composition profiler around the traced client ------------------------------
    traced_messages = mod.client.messages
    stream_local = threading.local()
    if args.stream:
        traced_messages._raw_messages = StreamingMessages(traced_messages._raw_messages, stream_local)
    raw_create = traced_messages.create
    seq = Counter()

    def block_chars(content) -> int:
        if isinstance(content, str):
            return len(content)
        return len(json.dumps(content, default=str))

    def block_field(block, field):
        if isinstance(block, dict):
            return block.get(field)
        return getattr(block, field, None)

    def tool_use_index(messages) -> dict:
        # tool_use blocks in assistant turns carry the id, tool name and arguments that the
        # matching tool_result refers to (works for every agent kind, hooks or not)
        index = {}
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if block_field(block, "type") == "tool_use":
                    index[block_field(block, "id")] = (block_field(block, "name"), block_field(block, "input"))
        return index

    seen_results: dict[tuple[str, str], str] = {}  # (agent, tool_use_id) -> sha1 of the content sent
    seen_lock = threading.Lock()

    def is_placeholder(text) -> bool:
        return isinstance(text, str) and (text.startswith("[Earlier tool result saved at")
                                          or text.startswith("<persisted-output>"))

    def profiled_create(**kwargs):
        ctx = trace.capture_context()
        messages = kwargs.get("messages", []) or []
        system = kwargs.get("system", "")
        by_tool: Counter = Counter()
        results_by_tool: Counter = Counter()
        placeholders = 0
        placeholder_chars = 0
        read_paths = []
        result_ids = []
        unknown = 0
        agent = ctx.get("agent_id") or "agent-root"
        tool_uses = tool_use_index(messages)
        new_contents = []
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                text = block.get("content", "")
                chars = block_chars(text)
                use_id = block.get("tool_use_id")
                info = calls.get(use_id)
                if use_id in tool_uses:
                    tool, tool_input = tool_uses[use_id]
                elif info:
                    tool, tool_input = info["tool"], info["input"]
                else:
                    tool, tool_input = "unknown", None
                    unknown += 1
                by_tool[tool] += chars
                results_by_tool[tool] += 1
                result_ids.append(use_id)
                if is_placeholder(text):
                    placeholders += 1
                    placeholder_chars += chars
                elif tool == "read_file" and isinstance(tool_input, dict):
                    read_paths.append(tool_input.get("path"))
                if not args.no_reads_log:
                    payload = text if isinstance(text, str) else json.dumps(text, default=str)
                    digest = hashlib.sha1(payload.encode("utf-8", "surrogatepass")).hexdigest()
                    key = (agent, use_id)
                    with seen_lock:
                        previous = seen_results.get(key)
                        if previous != digest:
                            seen_results[key] = digest
                            new_contents.append({
                                "event": "result" if previous is None else "replaced",
                                "tool_use_id": use_id, "tool": tool, "input": tool_input,
                                "chars": chars, "placeholder": is_placeholder(text), "content": payload,
                            })
        seq[agent] += 1
        if new_contents:
            with inputs_lock, reads_path.open("a", encoding="utf-8") as handle:
                for item in new_contents:
                    item.update({"ts": time.time(), "agent_id": agent,
                                 "agent_kind": ctx.get("agent_kind") or "lead",
                                 "call_index": seq[agent], "turn_id": ctx.get("turn_id")})
                    handle.write(json.dumps(item, default=str) + "\n")
        record = {
            "ts": time.time(),
            "agent_id": agent,
            "agent_kind": ctx.get("agent_kind") or "lead",
            "purpose": ctx.get("model_purpose"),
            "turn_id": ctx.get("turn_id"),
            "call_index": seq[agent],
            "message_count": len(messages),
            "system_chars": block_chars(system),
            "messages_chars": block_chars(messages),
            "tools_chars": block_chars(kwargs.get("tools") or []),
            "max_tokens": kwargs.get("max_tokens"),
            "tool_result_chars": dict(by_tool),
            "tool_result_counts": dict(results_by_tool),
            "compacted_placeholders": placeholders,
            "compacted_placeholder_chars": placeholder_chars,
            "read_file_paths_in_context": read_paths,
            "tool_result_ids": result_ids,
            "unknown_tool_results": unknown,
        }
        started = time.perf_counter()
        attempt = 0
        stream_local.timing = None
        while True:
            try:
                response = raw_create(**kwargs)
                break
            except Exception as exc:
                name = type(exc).__name__.lower()
                text = str(exc).lower()
                if (not args.no_retry_429 and ("ratelimit" in name or "429" in text)
                        and attempt < RATE_LIMIT_ATTEMPTS):
                    attempt += 1
                    delay = min(3.0 * 2 ** (attempt - 1), 40.0) + random.uniform(0, 2)
                    rate_limited[agent] += 1
                    print(f"[profile] 429 for {agent} ({ctx.get('model_purpose')}): retry {attempt}/{RATE_LIMIT_ATTEMPTS} "
                          f"in {delay:.1f}s", flush=True)
                    time.sleep(delay)
                    continue
                record.update({"status": "error", "error": type(exc).__name__, "rate_limit_retries": attempt,
                               "duration_ms": (time.perf_counter() - started) * 1000})
                with inputs_lock, inputs_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
                raise
        record["rate_limit_retries"] = attempt
        usage = getattr(response, "usage", None)
        out_text = out_think = out_tool_input = 0
        out_blocks = Counter()
        for block in getattr(response, "content", None) or []:
            btype = block_field(block, "type")
            out_blocks[btype] += 1
            if btype == "text":
                out_text += len(block_field(block, "text") or "")
            elif btype in {"thinking", "redacted_thinking"}:
                out_think += len(block_field(block, "thinking") or "")
            elif btype == "tool_use":
                out_tool_input += block_chars(block_field(block, "input") or {})
        record.update({
            "status": "ok",
            "duration_ms": (time.perf_counter() - started) * 1000,
            "stop_reason": getattr(response, "stop_reason", None),
            "output_text_chars": out_text,
            "output_thinking_chars": out_think,
            "output_tool_input_chars": out_tool_input,
            "output_blocks": dict(out_blocks),
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
            },
        })
        timing = getattr(stream_local, "timing", None)
        if timing:
            record["stream"] = timing
            stream_local.timing = None
        with inputs_lock, inputs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return response

    traced_messages.create = profiled_create

    # -- drive the session ------------------------------------------------------------------
    started = time.monotonic()
    deadline = started + args.max_seconds
    history: list = []
    with trace.agent_scope("agent-root", None, "lead"):
        context = mod.update_context({}, [])
    session_state = {"active_user_request": "(no active user request)"}
    mod.start_runtime_services()
    threading.Thread(target=mod.async_event_loop, args=(history, context, session_state),
                     daemon=True, name="lead-events").start()

    def run_user_turn(prompt: str):
        nonlocal context
        print(f"\n[profile] >>> user turn: {prompt[:120]}", flush=True)
        with mod.agent_lock:
            with mod.traced_lead_turn("user", prompt):
                mod.trigger_hooks("UserPromptSubmit", prompt)
                turn_start = len(history)
                session_state["active_user_request"] = prompt
                history.append({"role": "user", "content": prompt})
                mod.agent_loop(history, context, prompt)
                context = mod.update_context(context, history)
                mod.print_turn_assistants(history, turn_start)

    def team_snapshot():
        with mod.team_lock:
            return dict(mod.active_teammates)

    def wait_for_quiescence() -> str:
        quiet_since = None
        while time.monotonic() < deadline:
            time.sleep(2)
            team = team_snapshot()
            lead_busy = mod.agent_lock.locked()
            inbox = mod.BUS.peek("lead")
            working = [n for n, s in team.items() if s in {"working", "stopping"}]
            if not team and not lead_busy and not inbox:
                return "no-teammates"
            if working or lead_busy or inbox:
                quiet_since = None
                continue
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= args.quiet_seconds:
                return "teammates-idle"
        return "timeout"

    def shutdown_team(reason: str):
        team = team_snapshot()
        if not team:
            return
        print(f"\n[profile] shutting down {len(team)} teammate(s) ({reason}): {sorted(team)}", flush=True)
        with mod.agent_lock:  # keep the event loop from starting lead turns for the responses
            for name in team:
                try:
                    mod.run_request_shutdown(name)
                except Exception as exc:
                    print(f"[profile] shutdown request failed for {name}: {exc}", flush=True)
            end = time.monotonic() + 120
            while time.monotonic() < end and team_snapshot():
                time.sleep(1)
            mod.consume_lead_inbox(route_protocol=True)
        left = team_snapshot()
        if left:
            print(f"[profile] teammates still alive at exit (daemon threads): {sorted(left)}", flush=True)

    def archive_sandbox():
        if write_root is None or not args.sandbox_from or not write_root.exists():
            return
        for cache_dir in write_root.rglob("__pycache__"):
            shutil.rmtree(cache_dir, ignore_errors=True)
        destination = Path(args.trace_dir) / f"{args.label}.sandbox"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(write_root), str(destination))
        print(f"[profile] sandbox archived at {destination}", flush=True)

    status = "completed"
    try:
        turns = list(args.prompt)
        spawned_before = len(mod.teammate_trace_ids)
        first = True
        while turns:
            run_user_turn(turns.pop(0))
            if first and args.followup_if_no_team and not mod.teammate_trace_ids and spawned_before == 0:
                # the lead usually proposes a team and waits for confirmation first
                turns.insert(0, args.followup_if_no_team)
            first = False
            outcome = wait_for_quiescence()
            print(f"\n[profile] quiescence: {outcome} after {time.monotonic() - started:.0f}s", flush=True)
            if outcome == "timeout":
                status = "timeout"
                break
        shutdown_team(status)
    except KeyboardInterrupt:
        status = "interrupted"
    except Exception as exc:
        status = "error"
        print(f"[profile] driver error: {type(exc).__name__}: {exc}", flush=True)
    finally:
        trace.emit("profile_end", {"label": args.label, "status": status, "denials": dict(denials),
                                   "rate_limit_retries": dict(rate_limited),
                                   "wall_seconds": round(time.monotonic() - started, 1)})
        mod.close_tracing(status)
        try:
            archive_sandbox()
        except Exception as exc:
            print(f"[profile] sandbox archive failed: {type(exc).__name__}: {exc}", flush=True)
        print(f"\n[profile] done status={status} denials={dict(denials)} rate_limit_retries={dict(rate_limited)} wall={time.monotonic() - started:.0f}s", flush=True)
        print(f"[profile] trace={trace.path}\n[profile] inputs={inputs_path}\n[profile] reads={reads_path}", flush=True)
    return 0 if status in {"completed", "teammates-idle"} else 1


if __name__ == "__main__":
    sys.exit(main())
