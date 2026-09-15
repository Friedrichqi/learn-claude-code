#!/usr/bin/env python3
"""Micro-benchmark the harness-side latency of every tool the lead and teammate models can call.

    python3 s15_integrated_harness/scripts/tool_latency_probe.py [--reps 24] [--label L]
        [--trace-dir DIR] [--warmup] [--seed 20260915] [--only substr]

The harness prices a tool call as the tool_start -> tool_end span around the dispatch the agent
loop performs: PreToolUse hooks (permission), the handler, the PostToolUse hook and the trace
finish.  This probe executes exactly that path for every tool of both pools -- the lead pool
(assemble_tool_pool: 26 built-ins plus, once connected, the mock MCP tools) and the teammate pool
(the ten tools of spawn_teammate_thread's sub_handlers, reached through _run_teammate_tool with
its plan gate) -- with representative arguments per tool and no model calls at all: the provider
client is patched to raise, so spawned teammates and one-shot subagents exit on their first model
call instead of spending tokens.

Everything runs on a fresh-state lane copy of the repository (like every profiling run), from the
lane root, so writes stay inside the lane; write_file/edit_file are confined below
profiling_sandbox/toolprobe by the same permission policy the profiling driver installs
(mutating bash denied, create_worktree denied -- the denial path itself is measured too), and a
second, unconfined create_worktree case measures the real handler against the lane's .git.

Output: <trace-dir>/<label>.records.jsonl with one record per timed dispatch
{role, tool, case, rep, status, duration_ms, output_chars, warmup}; a full s15 trace is written
next to it, so the spans can be cross-checked with the standard tooling.  Tables:
scripts/tool_latency_analyze.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN_STATE = [".memory", ".tasks", ".mailboxes", ".transcripts", ".task_outputs",
             ".scheduled_tasks.json"]
SANDBOX = "profiling_sandbox/toolprobe"
MUTATE_CMD = re.compile(
    r"(\brm\b|\bmv\b|\bcp\b|\bsed\s+-i|"
    r"\bgit\s+(?:add|commit|checkout|reset|clean|stash|push|pull|rebase|merge|rm|mv|worktree)\b|"
    r"\bpip3?\s+(?:install|uninstall)\b|\bmkdir\b|\btouch\b|\bchmod\b|\bchown\b|\btruncate\b|"
    r"\bpython3?\b[^|;&]*\bopen\([^)]*['\"][wa]|\bdd\b|\bln\b|\bunlink\b|\brmdir\b|"
    r"\bos\.(?:remove|unlink|rename|replace|rmdir|makedirs|mkdir)\b|\bshutil\.|"
    r"\bwrite_text\b|\bwrite_bytes\b)")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=24, help="timed repetitions per case")
    parser.add_argument("--label", default=None, help="run label (default: toolprobe-<ts>)")
    parser.add_argument("--trace-dir", default=str(REPO / "s15_integrated_harness" / "traces" / "tool_latency"))
    parser.add_argument("--seed", type=int, default=20260915, help="shuffle seed for the case order")
    parser.add_argument("--warmup", action="store_true", help="drop the default untimed warm-up pass")
    parser.add_argument("--only", default=None, help="run only cases whose name contains this substring")
    parser.add_argument("--worktree-reps", type=int, default=6,
                         help="repetitions for the real create_worktree case (git is slow)")
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
    spec = importlib.util.spec_from_file_location("s15_toolprobe_harness", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["s15_toolprobe_harness"] = module
    spec.loader.exec_module(module)
    return module


class ProbeClientDisabled(Exception):
    """Raised by the patched client; spawned agents exit on their first model call."""


def make_block(name: str, args: dict, call_id: str):
    return types.SimpleNamespace(name=name, id=call_id, input=args)


class Probe:
    def __init__(self, mod, trace, records_path: Path, write_root: Path):
        self.mod = mod
        self.trace = trace
        self.records = []
        self.records_path = records_path
        self.write_root = write_root
        self.handlers = dict(mod.BUILTIN_HANDLERS)
        self.counter = 0
        self.anchor_task = None
        self.flags = {"deny_worktree": True}
        self.warm = False

    # -- dispatch paths (mirrors of the agent loops) -------------------------------------------

    def next_id(self) -> str:
        self.counter += 1
        return f"call_probe_{self.counter:05d}"

    def lead(self, name: str, args: dict) -> tuple[str, float, str]:
        """One lead-loop dispatch: span + PreToolUse hooks + handler + PostToolUse hook."""
        mod = self.mod
        block = make_block(name, args, self.next_id())
        started = time.perf_counter()
        with mod.TRACE.agent_scope("agent-root", None, "lead"):
            with mod.TRACE.span("tool_start", "tool_end",
                                mod.trace_tool_data(block)) as tool_span:
                if name == "compact":  # special-cased in the loop before hooks
                    output = "[Compaction requested. This completed turn will be summarized.]"
                    tool_span.finish(status="ok", tool_call_id=block.id, tool=name,
                                     result=mod.TRACE.summarize_output(output), special="compact")
                    return output, (time.perf_counter() - started) * 1000, "ok"
                blocked = mod.trigger_hooks("PreToolUse", block)
                if blocked:
                    output = str(blocked)
                    tool_span.finish(status="denied", tool_call_id=block.id, tool=name,
                                     result=mod.TRACE.summarize_output(output))
                    return output, (time.perf_counter() - started) * 1000, "denied"
                if mod.should_run_background(name, args):
                    try:
                        bg_id = mod.start_background_task(block, self.handlers)
                        output = (f"[Background task {bg_id} started] "
                                  "Result will arrive as a task_notification.")
                        status = "scheduled"
                    except Exception as exc:
                        bg_id = None
                        output = (f"Error: Failed to start background task: "
                                  f"{type(exc).__name__}: {exc}")
                        status = "error"
                    tool_span.finish(status=status, tool_call_id=block.id, tool=name,
                                     background_id=bg_id,
                                     result=mod.TRACE.summarize_output(output))
                    return output, (time.perf_counter() - started) * 1000, status
                handler = self.handlers.get(name)
                output = mod.call_tool_handler(handler, args, name)
                mod.trigger_hooks("PostToolUse", block, output)
                status = "error" if str(output).startswith("Error:") else "ok"
                tool_span.finish(status=status, tool_call_id=block.id, tool=name,
                                 result=mod.TRACE.summarize_output(output))
                return output, (time.perf_counter() - started) * 1000, status

    def teammate_handlers(self, owner: str) -> dict:
        """The wrapped handlers of spawn_teammate_thread's run_loop, for a probe teammate."""
        mod = self.mod

        def cwd_or_error():
            try:
                return mod.assignment_cwd(owner), None
            except (FileNotFoundError, ValueError) as exc:
                return None, f"Error: {exc}"

        def run_bash(command):
            cwd, error = cwd_or_error()
            return error or mod.run_bash(command, cwd=cwd)

        def run_read(path, limit=None, offset=0):
            cwd, error = cwd_or_error()
            return error or mod.run_read(path, limit=limit, offset=offset, cwd=cwd)

        def run_write(path, content):
            cwd, error = cwd_or_error()
            return error or mod.run_write(path, content, cwd=cwd)

        def run_edit(path, old_text, new_text):
            cwd, error = cwd_or_error()
            return error or mod.run_edit(path, old_text, new_text, cwd=cwd)

        def run_glob(pattern):
            cwd, error = cwd_or_error()
            return error or mod.run_glob(pattern, cwd=cwd)

        def run_list_tasks():
            tasks = mod.list_tasks()
            if not tasks:
                return "No tasks."
            return "\n".join(f"  {t.id}: {t.subject} [{t.status}]" for t in tasks)

        def claim(task_id):
            try:
                return mod.claim_task(task_id, owner=owner)
            except ValueError as exc:
                return f"Error: {exc}"
            except FileNotFoundError:
                return f"Error: Task {task_id} not found"

        def complete(task_id):
            try:
                return mod.complete_task(task_id, owner=owner)
            except ValueError as exc:
                return f"Error: {exc}"
            except FileNotFoundError:
                return f"Error: Task {task_id} not found"

        return {
            "bash": run_bash, "read_file": run_read, "write_file": run_write,
            "edit_file": run_edit, "glob": run_glob,
            "send_message": lambda to, content: mod._teammate_send_message(owner, to, content),
            "submit_plan": lambda plan: mod._teammate_submit_plan(owner, plan),
            "list_tasks": run_list_tasks, "claim_task": claim, "complete_task": complete,
        }

    def teammate(self, owner: str, name: str, args: dict) -> tuple[str, float, str]:
        """One teammate dispatch through _run_teammate_tool (plan gate + hooks + wrapped handler)."""
        mod = self.mod
        block = make_block(name, args, self.next_id())
        started = time.perf_counter()
        with mod.TRACE.agent_scope(f"agent-probe-{owner}", "agent-root", "teammate"):
            output = mod._run_teammate_tool(owner, block, self.teammate_handlers(owner))
        ms = (time.perf_counter() - started) * 1000
        text = str(output)
        if text.startswith("Blocked:") or "Permission denied" in text:
            status = "denied"
        elif text.startswith("Error:"):
            status = "error"
        else:
            status = "ok"
        return text, ms, status

    def teammate_off_main(self, owner: str, name: str, args: dict, case: str):
        """Teammate dispatch on a worker thread: permission_hook denies interactive approval
        off the main thread, so this is the only faithful way to measure the async denial."""
        result = {}

        def worker():
            result["out"] = self.teammate(owner, name, args)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        output, ms, status = result["out"]
        self.record("teammate", name, case, self.counter, output, ms, status, warmup=self.warm)

    # -- recording ------------------------------------------------------------------------------

    def record(self, role, tool, case, rep, output, ms, status, warmup=False, note=None):
        self.records.append({
            "role": role, "tool": tool, "case": case, "rep": rep,
            "status": status, "duration_ms": round(ms, 4),
            "output_chars": len(str(output)), "warmup": bool(warmup),
            "note": note,
        })

    def lead_case(self, name, args, record_case=None, expect=None):
        output, ms, status = self.lead(name, args)
        self.record("lead", name, record_case or name, self.counter, output, ms, status,
                    warmup=self.warm, note=expect)
        return output

    def tm_case(self, owner, name, args, record_case=None):
        output, ms, status = self.teammate(owner, name, args)
        self.record("teammate", name, record_case or name, self.counter, output, ms, status,
                    warmup=self.warm)
        return output

    # -- state helpers (untimed) ----------------------------------------------------------------

    def new_task(self, subject: str) -> str:
        task = self.mod.create_task(subject, "probe")
        return task.id

    def drop_task(self, task_id: str):
        try:
            (self.mod._task_path(task_id)).unlink()
        except Exception:
            pass

    def pop_pending(self, target: str, kind: str):
        for key in [k for k, v in self.mod.pending_requests.items()
                    if v.target == target and v.type == kind]:
            self.mod.pending_requests.pop(key, None)

    def git(self, *args: str):
        subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def build_cases(p: Probe, args):
    """Each case is (name, reps, fn(rep)) -> timed inside; state prep is untimed."""
    mod = p.mod
    cases = []

    def case(name, fn, reps=None):
        cases.append((name, reps or args.reps, fn))

    # -- lead: shell and files -------------------------------------------------------------------
    def bash_ls(rep):
        p.lead_case("bash", {"command": "ls s15_integrated_harness/scripts"}, "bash_ls")

    def bash_grep(rep):
        p.lead_case("bash", {"command": "grep -n CONTEXT_LIMIT s15_integrated_harness/code.py | head -5"},
                    "bash_grep")

    def bash_python(rep):
        p.lead_case("bash", {"command": f"python3 {SANDBOX}/selftest.py"}, "bash_python_file")

    def bash_bg(rep):
        p.lead_case("bash", {"command": "true", "run_in_background": True}, "bash_background_start")

    def bash_denied(rep):
        p.lead_case("bash", {"command": "rm -rf /tmp/probe_nonexistent"}, "bash_denied_mutating")

    def read_13k(rep):
        p.lead_case("read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "read_file_13KB")

    def read_25k(rep):
        p.lead_case("read_file", {"path": "README.md"}, "read_file_26KB")

    def read_45k(rep):
        p.lead_case("read_file", {"path": "s15_integrated_harness/ARCHITECTURE.md"}, "read_file_45KB")

    def read_146k(rep):
        p.lead_case("read_file", {"path": "s15_integrated_harness/code.py"}, "read_file_146KB")

    def read_limit(rep):
        p.lead_case("read_file", {"path": "s15_integrated_harness/GLOSSARY.md", "limit": 50},
                    "read_file_limit50")

    def write_small(rep):
        p.lead_case("write_file", {"path": f"{SANDBOX}/w_small.txt", "content": "x" * 200},
                    "write_file_200B")

    def write_20k(rep):
        p.lead_case("write_file", {"path": f"{SANDBOX}/w_20k.txt", "content": "x" * 20000},
                    "write_file_20KB")

    def edit_file(rep):
        path = REPO / SANDBOX / "editme.txt"
        path.write_text("line one\nTARGET_LINE\nline three\n", encoding="utf-8")
        p.lead_case("edit_file", {"path": f"{SANDBOX}/editme.txt",
                                  "old_text": "TARGET_LINE", "new_text": "EDITED_LINE"},
                    "edit_file_small")

    def glob_shallow(rep):
        p.lead_case("glob", {"pattern": "*.md"}, "glob_shallow")

    def glob_deep(rep):
        p.lead_case("glob", {"pattern": "**/*.md"}, "glob_deep_recursive")

    def todo(rep):
        p.lead_case("todo_write", {"todos": [
            {"content": "read the spec", "status": "completed"},
            {"content": "write the solution", "status": "in_progress"},
            {"content": "run the tests", "status": "pending"},
            {"content": "fix failures", "status": "pending"},
            {"content": "summarise", "status": "pending"}]}, "todo_write_5")

    def task_dispatch(rep):
        p.lead_case("task", {"description": "probe noop"}, "task_dispatch_only")

    def load_skill_hit(rep):
        name = sorted(mod.SKILL_REGISTRY)[0] if mod.SKILL_REGISTRY else "code-review"
        p.lead_case("load_skill", {"name": name}, "load_skill_hit")

    def load_skill_miss(rep):
        p.lead_case("load_skill", {"name": "no-such-skill"}, "load_skill_miss")

    def compact(rep):
        p.lead_case("compact", {"focus": "probe"}, "compact_marker")

    def create_task(rep):
        task_id = p.lead_case("create_task", {"subject": f"probe task {rep}"}, "create_task")
        if isinstance(task_id, str) and task_id.startswith("Created"):
            p.drop_task(task_id.split(":")[0].replace("Created", "").strip())

    def update_task(rep):
        a = p.new_task(f"probe dep a {rep}")                 # untimed
        b = p.new_task(f"probe dep b {rep}")                 # untimed
        p.lead_case("update_task", {"task_id": b, "addBlockedBy": [a]}, "update_task")
        p.drop_task(a)
        p.drop_task(b)

    def list_tasks(rep):
        p.lead_case("list_tasks", {}, "list_tasks")

    def get_task(rep):
        p.lead_case("get_task", {"task_id": p.anchor_task}, "get_task")

    def taskboard_cycle(rep):
        task_id = p.new_task(f"probe cycle {rep}")          # untimed
        p.lead_case("claim_task", {"task_id": task_id}, "claim_task")
        p.lead_case("complete_task", {"task_id": task_id}, "complete_task")
        mod.teammate_assignments.pop("agent", None)          # untimed cleanup
        p.drop_task(task_id)

    def cron_cycle(rep):
        target = dt.datetime.now() + dt.timedelta(hours=3)
        cron = f"{target.minute} {target.hour} {target.day} {target.month} *"
        out = p.lead_case("schedule_cron",
                          {"cron": cron, "prompt": "probe", "recurring": False, "durable": False},
                          "schedule_cron")
        p.lead_case("list_crons", {}, "list_crons")
        job_id = None
        for job in list(mod.scheduled_jobs.values()):
            if job.prompt == "probe":
                job_id = job.id
        if job_id:
            p.lead_case("cancel_cron", {"job_id": job_id}, "cancel_cron")

    def spawn_teammate(rep):
        name = f"probe-m{rep}"
        p.lead_case("spawn_teammate",
                    {"name": name, "role": "tester", "prompt": "noop"}, "spawn_teammate")
        deadline = time.monotonic() + 5
        while name in mod.active_teammates and time.monotonic() < deadline:
            time.sleep(0.02)

    def list_teammates(rep):
        p.lead_case("list_teammates", {}, "list_teammates")

    def send_message(rep):
        p.lead_case("send_message", {"to": "probe-t", "content": "probe"}, "send_message")

    def request_plan(rep):
        p.lead_case("request_plan", {"teammate": "probe-t", "task": "probe"}, "request_plan")
        mod.plan_gates["probe-t"] = "not_required"           # untimed cleanup

    def request_shutdown(rep):
        p.lead_case("request_shutdown", {"teammate": "probe-t"}, "request_shutdown")
        p.pop_pending("probe-t", "shutdown")                 # untimed cleanup

    def review_plan(rep):
        mod._teammate_submit_plan("probe-c", "probe plan")   # untimed: creates the pending request
        request_id = mod.plan_request_ids.get("probe-c")
        if request_id:
            p.lead_case("review_plan",
                        {"request_id": request_id, "approve": True}, "review_plan")
            mod.pending_requests.pop(request_id, None)
            mod.plan_request_ids.pop("probe-c", None)
        mod.plan_gates["probe-c"] = "not_required"

    def worktree_denied(rep):
        p.flags["deny_worktree"] = True
        p.lead_case("create_worktree", {"name": "probe-wt", "task_id": p.anchor_task},
                    "create_worktree_denied")

    def worktree_real(rep):
        name = f"probe-wt{rep}"
        task_id = p.new_task(f"probe worktree {rep}")        # untimed
        p.flags["deny_worktree"] = False                     # measure the real handler once
        try:
            p.lead_case("create_worktree", {"name": name, "task_id": task_id},
                        "create_worktree_real")
        finally:
            p.flags["deny_worktree"] = True
        path = mod._worktree_path(name)
        p.git("worktree", "remove", "--force", str(path))
        p.git("branch", "-D", f"wt/{name}")
        p.drop_task(task_id)

    def connect_mcp_cold(rep):
        p.lead_case("connect_mcp", {"name": "docs"}, "connect_mcp_cold")
        mod.mcp_clients.pop("docs", None)                    # untimed: cold again next rep

    def mcp_search(rep):
        p.lead_case("mcp__docs__search", {"query": "probe"}, "mcp_docs_search")

    def mcp_version(rep):
        p.lead_case("mcp__docs__get_version", {}, "mcp_docs_get_version")

    def mcp_status(rep):
        p.lead_case("mcp__deploy__status", {"service": "web"}, "mcp_deploy_status")

    def mcp_trigger_ask(rep):
        p.lead_case("mcp__deploy__trigger", {"service": "web"}, "mcp_deploy_trigger_confirm")

    case("bash_ls", bash_ls)
    case("bash_grep", bash_grep)
    case("bash_python_file", bash_python)
    case("bash_background_start", bash_bg)
    case("bash_denied_mutating", bash_denied)
    case("read_file_13KB", read_13k)
    case("read_file_26KB", read_25k)
    case("read_file_45KB", read_45k)
    case("read_file_146KB", read_146k)
    case("read_file_limit50", read_limit)
    case("write_file_200B", write_small)
    case("write_file_20KB", write_20k)
    case("edit_file_small", edit_file)
    case("glob_shallow", glob_shallow)
    case("glob_deep_recursive", glob_deep)
    case("todo_write_5", todo)
    case("task_dispatch_only", task_dispatch)
    case("load_skill_hit", load_skill_hit)
    case("load_skill_miss", load_skill_miss)
    case("compact_marker", compact)
    case("create_task", create_task)
    case("update_task", update_task)
    case("list_tasks", list_tasks)
    case("get_task", get_task)
    case("taskboard_cycle", taskboard_cycle)
    case("cron_cycle", cron_cycle)
    case("spawn_teammate", spawn_teammate)
    case("list_teammates", list_teammates)
    case("send_message", send_message)
    case("request_plan", request_plan)
    case("request_shutdown", request_shutdown)
    case("review_plan", review_plan)
    case("create_worktree_denied", worktree_denied)
    case("create_worktree_real", worktree_real, reps=args.worktree_reps)
    case("connect_mcp_cold", connect_mcp_cold)
    case("mcp_docs_search", mcp_search)
    case("mcp_docs_get_version", mcp_version)
    case("mcp_deploy_status", mcp_status)
    case("mcp_deploy_trigger_confirm", mcp_trigger_ask)

    # -- teammate pool ---------------------------------------------------------------------------
    def tm_bash_display(rep):
        p.tm_case("probe-t", "bash",
                  {"command": "grep -c 'def ' s15_integrated_harness/code.py"}, "tm_bash_display")

    def tm_bash_denied(rep):
        p.teammate_off_main("probe-t", "bash",
                            {"command": f"python3 {SANDBOX}/selftest.py"}, "tm_bash_denied_async")

    def tm_read(rep):
        p.tm_case("probe-t", "read_file",
                  {"path": "s15_integrated_harness/GLOSSARY.md"}, "tm_read_file_13KB")

    def tm_write(rep):
        p.tm_case("probe-t", "write_file",
                  {"path": f"{SANDBOX}/w_tm.txt", "content": "y" * 2000}, "tm_write_file_2KB")

    def tm_edit(rep):
        path = REPO / SANDBOX / "editme.txt"
        path.write_text("line one\nTARGET_LINE\nline three\n", encoding="utf-8")
        p.tm_case("probe-t", "edit_file",
                  {"path": f"{SANDBOX}/editme.txt",
                   "old_text": "TARGET_LINE", "new_text": "EDITED_LINE"}, "tm_edit_file_small")

    def tm_glob(rep):
        p.tm_case("probe-t", "glob", {"pattern": "**/*.md"}, "tm_glob_deep_recursive")

    def tm_send(rep):
        p.tm_case("probe-c", "send_message", {"to": "lead", "content": "probe"}, "tm_send_message")

    def tm_submit_plan(rep):
        p.tm_case("probe-c", "submit_plan", {"plan": "1. probe"}, "tm_submit_plan")
        request_id = mod.plan_request_ids.pop("probe-c", None)
        if request_id:
            mod.pending_requests.pop(request_id, None)
        mod.plan_gates["probe-c"] = "not_required"

    def tm_list(rep):
        p.tm_case("probe-c", "list_tasks", {}, "tm_list_tasks")

    def tm_cycle(rep):
        task_id = p.new_task(f"probe tm cycle {rep}")        # untimed
        p.tm_case("probe-c", "claim_task", {"task_id": task_id}, "tm_claim_task")
        p.tm_case("probe-c", "complete_task", {"task_id": task_id}, "tm_complete_task")
        mod.release_completed_assignment("probe-c")          # untimed: free probe-c for the next rep
        p.drop_task(task_id)

    def tm_gate(rep):
        mod.plan_gates["probe-t"] = "required"               # untimed setup
        p.tm_case("probe-t", "write_file",
                  {"path": f"{SANDBOX}/w_gate.txt", "content": "z"}, "tm_plan_gate_blocked")
        mod.plan_gates["probe-t"] = "not_required"

    case("tm_bash_display", tm_bash_display)
    case("tm_bash_denied_async", tm_bash_denied)
    case("tm_read_file_13KB", tm_read)
    case("tm_write_file_2KB", tm_write)
    case("tm_edit_file_small", tm_edit)
    case("tm_glob_deep_recursive", tm_glob)
    case("tm_send_message", tm_send)
    case("tm_submit_plan", tm_submit_plan)
    case("tm_list_tasks", tm_list)
    case("tm_claim_complete_cycle", tm_cycle)
    case("tm_plan_gate_blocked", tm_gate)
    return cases


def main() -> int:
    args = parse_args()
    os.chdir(REPO)
    os.environ["HARNESS_TRACE"] = "1"
    os.environ["HARNESS_TRACE_DIR"] = args.trace_dir
    os.environ["HARNESS_TRACE_OUTPUT"] = "summary"
    label = args.label or f"toolprobe-{dt.datetime.now():%Y%m%dT%H%M%S}"
    wipe_run_state()
    write_root = REPO / SANDBOX
    if write_root.exists():
        shutil.rmtree(write_root)
    write_root.mkdir(parents=True)
    (write_root / "selftest.py").write_text(
        "import unittest\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertEqual(1 + 1, 2)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8")

    mod = load_harness()
    mod.CLI_ACTIVE = False
    trace = mod.initialize_tracing("s15")

    # no model calls: spawned agents exit on their first create()
    def disabled_create(**kwargs):
        raise ProbeClientDisabled("tool latency probe: model calls are disabled")

    mod.client.messages.create = disabled_create

    # the profiling driver's permission policy: mutating bash denied, writes confined to the
    # sandbox, create_worktree denied; everything else falls through to the harness hook
    # (whose main-thread asks CONSOLE auto-approves).
    original_permission = mod.permission_hook
    probe_flags = {"deny_worktree": True}   # profiling-driver default; the real-worktree
                                            # case flips it off around its own dispatch

    def guarded_permission(block):
        if block.name == "bash":
            command = block.input.get("command", "")
            if isinstance(command, str) and MUTATE_CMD.search(command):
                return ("Permission denied by user: this profiling session is read-only; "
                        "do not modify files or git state.")
        if block.name in {"write_file", "edit_file"}:
            path = block.input.get("path", "")
            try:
                resolved = (REPO / str(path)).resolve()
            except Exception:
                resolved = None
            if resolved is None or not resolved.is_relative_to(write_root):
                return ("Permission denied by user: in this session files may only be written "
                        f"below {SANDBOX}/.")
        if block.name == "create_worktree" and probe_flags["deny_worktree"]:
            return ("Permission denied by user: do not create worktrees in this session; "
                    "work in the repository directory.")
        return original_permission(block)

    hooks = mod.HOOKS["PreToolUse"]
    hooks[hooks.index(original_permission)] = guarded_permission
    mod.CONSOLE.reader = lambda prompt: "y"

    trace.emit("probe_meta", {"label": label, "reps": args.reps,
                              "worktree_reps": args.worktree_reps, "seed": args.seed,
                              "driver": "scripts/tool_latency_probe.py", "sandbox": SANDBOX})
    records_path = Path(str(trace.path).removesuffix(".jsonl") + ".records.jsonl")
    print(f"[probe] label={label} trace={trace.path} records={records_path}", flush=True)

    probe = Probe(mod, trace, records_path, write_root)
    probe.flags = probe_flags
    cases = build_cases(probe, args)
    if args.only:
        cases = [c for c in cases if args.only in c[0]]

    # state: anchor task owned by probe-t (cwd source for teammate workspace tools),
    # probe-t/probe-c registered as active teammates, a few cron jobs on the board
    probe.anchor_task = probe.new_task("probe anchor")
    mod.claim_task(probe.anchor_task, owner="probe-t")
    mod.active_teammates["probe-t"] = "working"
    mod.active_teammates["probe-c"] = "working"
    mod.plan_gates["probe-t"] = "not_required"
    mod.plan_gates["probe-c"] = "not_required"
    target = dt.datetime.now() + dt.timedelta(hours=4)
    for i in range(3):
        mod.schedule_job(f"{target.minute} {target.hour} {target.day} {target.month} *",
                         f"probe-board-{i}", recurring=False, durable=False)
    mod.connect_mcp("docs")
    mod.connect_mcp("deploy")
    # the lead loop dispatches against assemble_tool_pool's merged handler table, which
    # includes the mcp__ tools once their servers are connected
    _, pool_handlers = mod.assemble_tool_pool()
    probe.handlers = dict(pool_handlers)

    rng = random.Random(args.seed)
    schedule = []
    for name, reps, fn in cases:
        for rep in range(reps):
            schedule.append((name, rep, fn))
    rng.shuffle(schedule)

    passes = [("timed", schedule)]
    if not args.warmup:
        warm = [(name, 0, fn) for name, _, fn in cases]
        rng.shuffle(warm)
        passes.insert(0, ("warmup", warm))
    try:
        for pass_name, items in passes:
            probe.warm = pass_name == "warmup"
            for name, rep, fn in items:
                try:
                    fn(rep)
                except Exception as exc:
                    probe.records.append({
                        "role": "error", "tool": name, "case": name, "rep": rep,
                        "status": "case_error", "duration_ms": 0, "output_chars": 0,
                        "warmup": probe.warm,
                        "note": f"{type(exc).__name__}: {exc}",
                    })
                    print(f"[probe] case {name} rep {rep} failed: {type(exc).__name__}: {exc}",
                          flush=True)
    finally:
        with records_path.open("w", encoding="utf-8") as handle:
            for record in probe.records:
                handle.write(json.dumps(record) + "\n")
        mod.mcp_clients.clear()
        mod.release_teammate_assignment("probe-t")
        for name in ("probe-t", "probe-c"):
            mod.active_teammates.pop(name, None)
            mod.plan_gates.pop(name, None)
        timed = [r for r in probe.records if not r["warmup"] and r["role"] != "error"]
        print(f"[probe] done: {len(timed)} timed dispatches over {len(cases)} cases",
              flush=True)
    trace.emit("probe_end", {"label": label, "dispatches": len(timed)})
    mod.close_tracing("completed")
    print(f"[probe] records={records_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
