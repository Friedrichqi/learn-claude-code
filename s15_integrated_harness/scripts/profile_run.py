#!/usr/bin/env python3
"""Run one s15 session non-interactively for profiling and record an input-composition side log.

    python3 s15_integrated_harness/scripts/profile_run.py --label X1 \
        --prompt "Inspect ... do not delegate." [--prompt "follow-up"] [--followup-if-no-team "Confirmed, proceed."]
        [--max-seconds 900] [--quiet-seconds 30] [--allow-writes] [--trace-dir DIR]

Behaviour
  * Imports s15_integrated_harness/code.py from the repository root (WORKDIR = repo root) and
    initialises the normal JSONL trace, so the run is analysable with trace_view.py and
    scripts/file_read_reuse.py exactly like an interactive session.
  * Auto-approves shell permission prompts, but denies mutating shell commands and (unless
    --allow-writes) write_file/edit_file, so profiling runs are read-only on the repository.
  * Wraps the traced Messages client once more and writes <trace>.inputs.jsonl: for EVERY model
    call, how many characters of the request were tool_result blocks of each tool (read_file,
    bash, glob, ...), which read_file paths were present, how many results had already been
    replaced by compaction placeholders, plus provider usage.  This measures how many times the
    same file content is re-sent to the model (input reuse), which the trace alone cannot show.
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
    r"(\brm\b|\bmv\b|\bcp\b|>(?!>)\s*\S|>>|\btee\b|\bsed\s+-i|"
    r"\bgit\s+(?:add|commit|checkout|reset|clean|stash|push|pull|rebase|merge|rm|mv|worktree)\b|"
    r"\bpip3?\s+(?:install|uninstall)\b|\bmkdir\b|\btouch\b|\bchmod\b|\bchown\b|\btruncate\b|"
    r"\bpython3?\b[^|;&]*\bopen\([^)]*['\"][wa]|\bdd\b|\bln\b|\bunlink\b|\brmdir\b)"
)
RATE_LIMIT_ATTEMPTS = 6
RUN_STATE = [".memory", ".tasks", ".mailboxes", ".transcripts", ".task_outputs", ".scheduled_tasks.json"]


def is_mutating(command: str) -> bool:
    return bool(MUTATE_CMD.search(HARMLESS_REDIRECT.sub(" ", command)))


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
    return parser.parse_args()


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


def main() -> int:
    args = parse_args()
    os.chdir(REPO)
    os.environ["HARNESS_TRACE"] = "1"
    os.environ["HARNESS_TRACE_DIR"] = args.trace_dir
    os.environ["HARNESS_TRACE_OUTPUT"] = args.trace_output
    if not args.keep_state:
        wipe_run_state()

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
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  cwd=REPO, check=True).stdout.strip()
    except Exception:
        git_head = None
    trace.emit("profile_meta", {"label": args.label, "prompts": args.prompt,
                                "allow_writes": args.allow_writes, "driver": "scripts/profile_run.py",
                                "no_timestamp": args.no_timestamp, "context_limit": mod.CONTEXT_LIMIT,
                                "git_head": git_head, "trace_output": args.trace_output,
                                "reads_log": not args.no_reads_log})
    inputs_path = Path(str(trace.path).removesuffix(".jsonl") + ".inputs.jsonl")
    reads_path = Path(str(trace.path).removesuffix(".jsonl") + ".reads.jsonl")
    inputs_lock = threading.Lock()
    print(f"[profile] label={args.label} trace={trace.path} inputs={inputs_path}", flush=True)
    if not args.no_reads_log:
        print(f"[profile] reads={reads_path}", flush=True)

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
        if block.name in {"write_file", "edit_file"} and not args.allow_writes:
            denials[block.name] += 1
            return "Permission denied by user: this profiling session is read-only; report findings in your reply instead."
        return original_permission(block)

    hooks = mod.HOOKS["PreToolUse"]
    hooks[hooks.index(original_permission)] = guarded_permission
    hooks.insert(0, record_call)
    mod.CONSOLE.reader = lambda prompt: "y"

    # -- input-composition profiler around the traced client ------------------------------
    traced_messages = mod.client.messages
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
        for block in getattr(response, "content", None) or []:
            btype = block_field(block, "type")
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
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
            },
        })
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
        print(f"\n[profile] done status={status} denials={dict(denials)} rate_limit_retries={dict(rate_limited)} wall={time.monotonic() - started:.0f}s", flush=True)
        print(f"[profile] trace={trace.path}\n[profile] inputs={inputs_path}\n[profile] reads={reads_path}", flush=True)
    return 0 if status in {"completed", "teammates-idle"} else 1


if __name__ == "__main__":
    sys.exit(main())
