#!/usr/bin/env python3
"""Controlled price list of every supported tool on today's host (research/06_tool_cost Part C, C2).

    python3 research/06_tool_cost/tool_sweep_probe.py --label L [--reps 24] [--heavy-reps 3]
        [--trace-dir DIR] [--no-heavy] [--full-repo PATH] [--seed 20260930] [--only substr]

Extends Part A's probe (tool_latency_probe.py, 54 cases, 2026-09-15 on the old host) to the whole tool
surface of the harness as it is today, with no model calls (the provider client raises):

  * Part A's 54 cases unchanged (lead pool 26 tools + 4 mock-MCP, teammate pool 10) -- the drift check;
  * the paths Part A never took: the lead on the `lead-events` thread (team turns: async permission
    rules), the one-shot subagent pool (SUB_HANDLERS through the subagent dispatch), the teammate pool
    through wrappers that include the real "Claim a Task first" guard, s16 `Workflow` with the mock
    runner (orchestration only) and its journal resume, s17's five tools through AgentSession._run_tool;
  * argument sweeps: read_file 1 KB - 4 MB (first read, re-read under the residency guard, cold page
    cache via posix_fadvise(DONTNEED), offset/limit, missing file), write_file 100 B - 1 MB, edit_file on
    1 KB - 1 MB files, glob by depth and match count, bash by command class (echo, ls, scoped and
    repository greps, site-packages greps and a find, python -c, a pytest file, output > 50 KB, non-zero
    exit, sleep 1 as a calibration), background bash start AND completion, spawn_teammate with a task_id;
  * heavy cases at --heavy-reps (skipped with --no-heavy): the full lesson suite (`pytest tests`, ~45 s),
    a content grep over all of site-packages (~10 s), `create_worktree` for real in this lane's own small
    git repository, and -- with --full-repo -- raw `git worktree add` on a shared clone of the full
    941 MB repository;
  * a task-board phase: board sizes 1 / 10 / 100 / 1000 for the tools that scan the board
    (every lead file tool via assignment_cwd -> _owner_in_progress, list/claim/complete/create).

Environment factors (NFS vs node-local lane, trace directory, trace on/off/full) are separate processes:
research/06_tool_cost/tool_sweep_run.sh.  Records: <trace-dir>/<label>.records.jsonl, one per timed
dispatch {role, tool, case, rep, phase, status, duration_ms, output_chars, warmup, board, note}.
"""

from __future__ import annotations

import argparse
import builtins
import datetime as dt
import importlib.util
import json
import os
import random
import re
import shutil
import site
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401
import tool_latency_probe as tlp                                              # noqa: E402
from profile_run import is_mutating                                           # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SWEEP = "profiling_sandbox/toolsweep"
DIFF = REPO / "research" / "common" / "fixtures" / "tool_heavy" / "review_changes.diff"
SITE = Path(site.getsitepackages()[0])
SIZES_READ = {"1KB": 1 << 10, "64KB": 64 << 10, "512KB": 512 << 10, "2MB": 2 << 20, "4MB": 4 << 20}
SIZES_WRITE = {"100B": 100, "2KB": 2 << 10, "20KB": 20 << 10, "200KB": 200 << 10, "1MB": 1 << 20}
SIZES_EDIT = {"1KB": 1 << 10, "20KB": 20 << 10, "146KB": 146 << 10, "1MB": 1 << 20}
BOARD_SIZES = [1, 10, 100, 1000]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True)
    parser.add_argument("--reps", type=int, default=24)
    parser.add_argument("--heavy-reps", type=int, default=3)
    parser.add_argument("--board-reps", type=int, default=12)
    parser.add_argument("--trace-dir", default=str(REPO / "research" / "06_tool_cost" / "data" / "tool_sweep"))
    parser.add_argument("--no-heavy", action="store_true")
    parser.add_argument("--no-board", action="store_true")
    parser.add_argument("--full-repo", default=None, help="repository to clone --shared for raw git worktree add")
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--only", default=None, help="run only cases whose name contains this substring")
    # Part A's build_cases reads these two
    parser.add_argument("--worktree-reps", type=int, default=6)
    parser.add_argument("--warmup", action="store_true", help="drop the untimed warm-up pass (Part A semantics)")
    return parser.parse_args()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def text_of(size: int) -> str:
    line = "tool sweep probe line with some ordinary words and digits 0123456789\n"
    return (line * (size // len(line) + 1))[:size]


class SweepProbe(tlp.Probe):
    phase = "main"
    board = None

    def record(self, role, tool, case, rep, output, ms, status, warmup=False, note=None):
        super().record(role, tool, case, rep, output, ms, status, warmup, note)
        self.records[-1].update(phase=self.phase, board=self.board, rep_index=self.rep_index)

    rep_index = None

    def teammate_handlers(self, owner: str) -> dict:
        """Part A's wrappers plus the real run_loop's claim guard (code.py current_cwd)."""
        handlers = super().teammate_handlers(owner)
        mod = self.mod

        def guarded(fn):
            def wrapper(*a, **k):
                if owner not in mod.teammate_assignments:
                    return "Error: Claim a Task before using workspace tools."
                return fn(*a, **k)
            return wrapper

        for name in ("bash", "read_file", "write_file", "edit_file", "glob"):
            handlers[name] = guarded(handlers[name])
        return handlers

    def subagent(self, name: str, args: dict):
        """One tool call of the one-shot subagent loop (code.py spawn_subagent): span, hooks, SUB_HANDLERS."""
        mod = self.mod
        block = tlp.make_block(name, args, self.next_id())
        started = time.perf_counter()
        with mod.TRACE.agent_scope("agent-task-probe", "agent-root", "one_shot"):
            with mod.TRACE.span("tool_start", "tool_end", mod.trace_tool_data(block)) as span:
                blocked = mod.trigger_hooks("PreToolUse", block)
                if blocked:
                    output, status = str(blocked), "denied"
                else:
                    output = mod.call_tool_handler(mod.SUB_HANDLERS.get(name), args, name)
                    mod.trigger_hooks("PostToolUse", block, output)
                    status = "error" if str(output).startswith("Error:") else "ok"
                span.finish(status=status, tool_call_id=block.id, tool=name, result=mod.TRACE.summarize_output(output))
        return output, (time.perf_counter() - started) * 1000, status

    def sub_case(self, name, args, case):
        output, ms, status = self.subagent(name, args)
        self.record("subagent", name, case, self.counter, output, ms, status, warmup=self.warm)
        return output

    def lead_events_case(self, name, args, case):
        """The lead dispatch on the `lead-events` thread inside a team-triggered turn (async rules)."""
        mod = self.mod
        result = {}

        def worker():
            with mod.agent_lock:
                with mod.traced_lead_turn("team", "probe team turn"):
                    result["out"] = self.lead(name, args)

        thread = threading.Thread(target=worker, name="lead-events")
        thread.start()
        thread.join()
        output, ms, status = result["out"]
        self.record("lead-events", name, case, self.counter, output, ms, status, warmup=self.warm)
        return output


def main() -> int:
    args = parse_args()
    os.chdir(REPO)
    trace_on = os.environ.get("HARNESS_TRACE", "1") != "0"
    os.environ.setdefault("HARNESS_TRACE", "1")
    os.environ["HARNESS_TRACE_DIR"] = args.trace_dir
    os.environ.setdefault("HARNESS_TRACE_OUTPUT", "summary")
    Path(args.trace_dir).mkdir(parents=True, exist_ok=True)
    tlp.wipe_run_state()
    for extra in (REPO / "s16_workflow_runtime" / ".runtime", REPO / ".worktrees"):
        if extra.exists():
            shutil.rmtree(extra, ignore_errors=True)
    for sandbox in (REPO / tlp.SANDBOX, REPO / SWEEP):
        if sandbox.exists():
            shutil.rmtree(sandbox)
        sandbox.mkdir(parents=True)
    (REPO / tlp.SANDBOX / "selftest.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(1 + 1, 2)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
    sweep = REPO / SWEEP
    for tag, size in SIZES_READ.items():
        (sweep / f"read_{tag}.txt").write_text(text_of(size), encoding="utf-8")

    mod = tlp.load_harness()
    mod.CLI_ACTIVE = False
    trace = mod.initialize_tracing("s15")

    def disabled_create(**kwargs):
        raise tlp.ProbeClientDisabled("tool sweep probe: model calls are disabled")

    mod.client.messages.create = disabled_create
    s16 = load_module("s16_toolsweep_runtime", REPO / "s16_workflow_runtime" / "code.py")
    s16.install_workflow_tool(mod)
    s16.RUNNER_FACTORY = s16.MockAgentRunner          # read at call time: orchestration without a model
    s17 = load_module("s17_toolsweep_goal", REPO / "s17_goal_loop" / "code.py")
    builtins.input = lambda prompt="": "n"             # s17's destructive-command prompt: deny, never block

    original_permission = mod.permission_hook
    flags = {"deny_worktree": True}
    write_root = (REPO / "profiling_sandbox").resolve()

    def guarded_permission(block):
        if block.name == "bash":
            command = block.input.get("command", "")
            if isinstance(command, str) and is_mutating(command):
                return ("Permission denied by user: this profiling session is read-only; "
                        "do not modify files or git state.")
        if block.name in {"write_file", "edit_file"}:
            try:
                resolved = (REPO / str(block.input.get("path", ""))).resolve()
            except Exception:
                resolved = None
            if resolved is None or not resolved.is_relative_to(write_root):
                return "Permission denied by user: in this session files may only be written below profiling_sandbox/."
        if block.name == "create_worktree" and flags["deny_worktree"]:
            return "Permission denied by user: do not create worktrees in this session; work in the repository directory."
        return original_permission(block)

    hooks = mod.HOOKS["PreToolUse"]
    hooks[hooks.index(original_permission)] = guarded_permission
    mod.CONSOLE.reader = lambda prompt: "y"

    trace_path = Path(str(getattr(trace, "path", "") or ""))
    records_path = Path(args.trace_dir) / f"{args.label}.records.jsonl"
    meta = {"label": args.label, "driver": "research/06_tool_cost/tool_sweep_probe.py", "reps": args.reps,
            "heavy_reps": args.heavy_reps, "board_reps": args.board_reps, "seed": args.seed, "repo": str(REPO),
            "trace_on": trace_on, "trace_output": os.environ.get("HARNESS_TRACE_OUTPUT"), "trace_dir": args.trace_dir,
            "host": os.uname().nodename, "cpus": len(os.sched_getaffinity(0)), "loadavg": os.getloadavg(),
            "site_packages": str(SITE), "full_repo": args.full_repo, "no_heavy": args.no_heavy,
            "python": sys.version.split()[0]}
    trace.emit("probe_meta", meta)
    print(f"[sweep] label={args.label} trace={trace_path or '(off)'} records={records_path}", flush=True)

    p = SweepProbe(mod, trace, records_path, REPO / tlp.SANDBOX)
    p.flags = flags
    cases = tlp.build_cases(p, args)                   # Part A's 54 cases, unchanged
    cases = [(f"A:{name}", reps, fn) for name, reps, fn in cases]
    heavy, board_cases = [], []

    def case(name, fn, reps=None):
        cases.append((name, reps or args.reps, fn))

    # -- read_file sweep -----------------------------------------------------------------------
    def read_first(tag):
        def fn(rep):
            mod._resident_reads.clear()
            p.lead_case("read_file", {"path": f"{SWEEP}/read_{tag}.txt"}, f"read_first_{tag}")
        return fn

    def read_again(tag):
        def fn(rep):
            mod.run_read(f"{SWEEP}/read_{tag}.txt")    # untimed: make it resident
            p.lead_case("read_file", {"path": f"{SWEEP}/read_{tag}.txt"}, f"read_reread_{tag}")
        return fn

    def read_cold(tag):
        def fn(rep):
            mod._resident_reads.clear()
            path = sweep / f"read_{tag}.txt"
            fd = os.open(path, os.O_RDONLY)
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)   # drops the NFS client's cached pages
            finally:
                os.close(fd)
            p.lead_case("read_file", {"path": f"{SWEEP}/read_{tag}.txt"}, f"read_cold_{tag}")
        return fn

    for tag in SIZES_READ:
        case(f"read_first_{tag}", read_first(tag))
        case(f"read_reread_{tag}", read_again(tag))
        case(f"read_cold_{tag}", read_cold(tag))
    case("read_offset_limit_2MB", lambda rep: p.lead_case(
        "read_file", {"path": f"{SWEEP}/read_2MB.txt", "offset": 1000, "limit": 100}, "read_offset_limit_2MB"))
    case("read_missing", lambda rep: p.lead_case("read_file", {"path": f"{SWEEP}/no_such_file.txt"}, "read_missing"))

    # -- write / edit sweeps -------------------------------------------------------------------
    def write_case(tag, size):
        content = text_of(size)
        return lambda rep: p.lead_case("write_file", {"path": f"{SWEEP}/w_{tag}.txt", "content": content}, f"write_{tag}")

    for tag, size in SIZES_WRITE.items():
        case(f"write_{tag}", write_case(tag, size))

    def edit_case(tag, size):
        body = text_of(size)
        cut = len(body) // 2

        def fn(rep):
            (sweep / f"e_{tag}.txt").write_text(body[:cut] + "EDIT_TARGET_LINE\n" + body[cut:], encoding="utf-8")
            p.lead_case("edit_file", {"path": f"{SWEEP}/e_{tag}.txt", "old_text": "EDIT_TARGET_LINE",
                                      "new_text": "EDITED_LINE"}, f"edit_{tag}")
        return fn

    for tag, size in SIZES_EDIT.items():
        case(f"edit_{tag}", edit_case(tag, size))

    # -- glob ----------------------------------------------------------------------------------
    for tag, pattern in (("shallow_md", "*.md"), ("deep_md", "**/*.md"), ("deep_py", "**/*.py"),
                         ("research_py", "research/**/*.py"), ("s15_star", "s15_integrated_harness/*")):
        case(f"glob_{tag}", (lambda pat, t: lambda rep: p.lead_case("glob", {"pattern": pat}, f"glob_{t}"))(pattern, tag))

    # -- bash by class -------------------------------------------------------------------------
    bash_cases = {
        "echo": "echo probe",
        "ls": "ls -la s15_integrated_harness",
        "grep_scoped": "grep -n CONTEXT_LIMIT s15_integrated_harness/code.py | head -5",
        "grep_repo": "grep -rn --include='*.py' 'def run_read' . | head -20",
        "grep_vllm": f"grep -rlE --include='*.py' '^class [A-Za-z0-9_]+ForCausalLM\\b' {SITE}/vllm | wc -l",
        "grep_transformers": f"grep -rlE --include='modeling_*.py' '^def apply_rotary_pos_emb\\b' {SITE}/transformers/models | wc -l",
        "find_site": f"find {SITE} -name py.typed | wc -l",
        "python_c": "python3 -c \"print(sum(range(1000)))\"",
        "python_unittest": f"python3 {tlp.SANDBOX}/selftest.py",
        "pytest_file": "python3 -m pytest -q -p no:cacheprovider tests/test_task_system.py",
        "output_60KB": "seq 1 12000",
        "nonzero_exit": "ls /no/such/dir",
        "sleep_1s": "sleep 1",
    }
    for tag, command in bash_cases.items():
        reps = 8 if tag in {"pytest_file", "find_site", "grep_repo", "grep_vllm", "grep_transformers", "sleep_1s"} else None
        case(f"bash_{tag}", (lambda cmd, t: lambda rep: p.lead_case("bash", {"command": cmd}, f"bash_{t}"))(command, tag), reps)

    def bash_bg_complete(rep):
        """dispatch of a background bash job, then the time until its result is collected (untimed wait
        loop at 2 ms granularity, recorded as a second record)"""
        started = time.perf_counter()
        out = p.lead_case("bash", {"command": "sleep 0.2", "run_in_background": True}, "bash_background_dispatch")
        match = re.search(r"(bg_\d+)", str(out))
        deadline = time.monotonic() + 30
        while match and time.monotonic() < deadline:
            with mod.background_lock:
                status = (mod.background_tasks.get(match.group(1)) or {}).get("status")
            if status in {"completed", "failed"}:
                break
            time.sleep(0.002)
        mod.collect_background_results()
        p.record("lead", "bash", "bash_background_completion_0.2s", p.counter, "",
                 (time.perf_counter() - started) * 1000, "ok", warmup=p.warm)

    case("bash_background_complete", bash_bg_complete, reps=8)

    # -- spawn_teammate with a task_id ---------------------------------------------------------
    def spawn_with_task(rep):
        name = f"probe-s{rep}-{p.counter}"
        task_id = p.new_task(f"probe spawn task {rep}")
        p.lead_case("spawn_teammate", {"name": name, "role": "tester", "prompt": "noop", "task_id": task_id},
                    "spawn_teammate_with_task")
        deadline = time.monotonic() + 5
        while name in mod.active_teammates and time.monotonic() < deadline:
            time.sleep(0.02)
        mod.teammate_assignments.pop(name, None)
        p.drop_task(task_id)

    case("spawn_teammate_with_task", spawn_with_task)

    # -- lead on the lead-events thread (team turns) --------------------------------------------
    case("le_bash_display", lambda rep: p.lead_events_case("bash", {"command": "grep -c 'def ' s15_integrated_harness/code.py"}, "le_bash_display"))
    case("le_bash_python_denied", lambda rep: p.lead_events_case("bash", {"command": f"python3 {tlp.SANDBOX}/selftest.py"}, "le_bash_python"))
    case("le_read_13KB", lambda rep: p.lead_events_case("read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "le_read_file_13KB"))
    case("le_list_tasks", lambda rep: p.lead_events_case("list_tasks", {}, "le_list_tasks"))
    case("le_mcp_trigger", lambda rep: p.lead_events_case("mcp__deploy__trigger", {"service": "web"}, "le_mcp_deploy_trigger"))

    # -- one-shot subagent pool ------------------------------------------------------------------
    case("sub_bash_grep", lambda rep: p.sub_case("bash", {"command": "grep -n CONTEXT_LIMIT s15_integrated_harness/code.py | head -5"}, "sub_bash_grep"))
    case("sub_read_13KB", lambda rep: p.sub_case("read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "sub_read_file_13KB"))
    case("sub_write_2KB", lambda rep: p.sub_case("write_file", {"path": f"{SWEEP}/sub_w.txt", "content": "s" * 2048}, "sub_write_file_2KB"))

    def sub_edit(rep):
        (sweep / "sub_e.txt").write_text("one\nSUB_TARGET\nthree\n", encoding="utf-8")
        p.sub_case("edit_file", {"path": f"{SWEEP}/sub_e.txt", "old_text": "SUB_TARGET", "new_text": "SUB_EDITED"}, "sub_edit_file_small")

    case("sub_edit_small", sub_edit)
    case("sub_glob_deep", lambda rep: p.sub_case("glob", {"pattern": "**/*.md"}, "sub_glob_deep_md"))

    # -- teammate pool with the claim guard ----------------------------------------------------
    case("tmg_read_13KB", lambda rep: p.tm_case("probe-t", "read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "tmg_read_file_13KB"))
    case("tmg_unclaimed_read", lambda rep: p.tm_case("probe-c", "read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "tmg_read_unclaimed_guard"))
    case("tmg_bash_display", lambda rep: p.tm_case("probe-t", "bash", {"command": "grep -c 'def ' s15_integrated_harness/code.py"}, "tmg_bash_display"))

    # -- s16 Workflow (mock runner) -------------------------------------------------------------
    diff_text = DIFF.read_text(encoding="utf-8")

    def workflow_run(rep):
        p.lead_case("Workflow", {"name": "review-changes", "args": {"changes": f"# sweep {rep} {p.counter}\n{diff_text}"}},
                    "workflow_mock_run")

    def workflow_resume(rep):
        changes = f"# sweep resume {rep} {p.counter}\n{diff_text}"
        first = mod.call_tool_handler(p.handlers["Workflow"], {"name": "review-changes", "args": {"changes": changes}}, "Workflow")
        try:
            run_id = json.loads(first)["task"]["runId"]
        except Exception:
            run_id = None
        if run_id:
            p.lead_case("Workflow", {"name": "review-changes", "args": {"changes": changes}, "resume_from_run_id": run_id},
                        "workflow_mock_resume")

    case("workflow_mock_run", workflow_run)
    case("workflow_mock_resume", workflow_resume, reps=12)

    # -- s17 tools --------------------------------------------------------------------------------
    session = s17.AgentSession(client=None, model="probe", goal=s17.GoalController(None), workdir=REPO)

    def s17_case(name, tool_args, label):
        def fn(rep):
            if name == "edit_file":
                (sweep / "s17_e.txt").write_text("one\nS17_TARGET\nthree\n", encoding="utf-8")
            block = types.SimpleNamespace(type="tool_use", name=name, id=p.next_id(), input=tool_args)
            started = time.perf_counter()
            blocked = session.trigger_hooks("PreToolUse", block)
            if blocked is not None:
                output, status = str(blocked), "denied"
            else:
                try:
                    output = session._run_tool(name, tool_args)
                except Exception as exc:
                    output = f"{type(exc).__name__}: {exc}"
                session.trigger_hooks("PostToolUse", block, output)
                status = "error" if str(output).startswith(("Error", "error")) else "ok"
            p.record("s17", name, label, p.counter, output, (time.perf_counter() - started) * 1000, status, warmup=p.warm)
        return fn

    case("s17_bash_grep", s17_case("bash", {"command": "grep -n CONTEXT_LIMIT s15_integrated_harness/code.py | head -5"}, "s17_bash_grep"))
    case("s17_read_13KB", s17_case("read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "s17_read_file_13KB"))
    case("s17_write_2KB", s17_case("write_file", {"path": f"{SWEEP}/s17_w.txt", "content": "q" * 2048}, "s17_write_file_2KB"))
    case("s17_edit_small", s17_case("edit_file", {"path": f"{SWEEP}/s17_e.txt", "old_text": "S17_TARGET", "new_text": "S17_EDITED"}, "s17_edit_file_small"))
    case("s17_glob_deep", s17_case("glob", {"pattern": "**/*.md"}, "s17_glob_deep_md"))

    # -- heavy cases ------------------------------------------------------------------------------
    def heavy_case(name, fn):
        heavy.append((name, args.heavy_reps, fn))

    heavy_case("bash_pytest_suite", lambda rep: p.lead_case("bash", {"command": "python3 -m pytest -q -p no:cacheprovider tests"}, "bash_pytest_suite"))
    heavy_case("bash_grep_site_all", lambda rep: p.lead_case("bash", {"command": f"grep -rl --include='*.py' 'flash_attn' {SITE} | wc -l"}, "bash_grep_site_all"))
    if args.full_repo:
        clone = Path(os.environ.get("TMPDIR", "/tmp")) / f"toolsweep_fullrepo_{os.getpid()}"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", args.full_repo, str(clone)], check=True)

        def git_full(rep):
            target = clone.parent / f"{clone.name}_wt{rep}_{p.counter}"
            started = time.perf_counter()
            proc = subprocess.run(["git", "-C", str(clone), "worktree", "add", "--quiet", "-b", f"probe/{target.name}",
                                   str(target), "HEAD"], capture_output=True, text=True)
            ms = (time.perf_counter() - started) * 1000
            p.record("raw-git", "git_worktree_add", "git_worktree_add_full_repo", p.counter, proc.stdout + proc.stderr, ms,
                     "ok" if proc.returncode == 0 else "error", warmup=p.warm)
            subprocess.run(["git", "-C", str(clone), "worktree", "remove", "--force", str(target)], capture_output=True)
            subprocess.run(["git", "-C", str(clone), "branch", "-D", f"probe/{target.name}"], capture_output=True)
            subprocess.run(["git", "-C", str(clone), "worktree", "prune"], capture_output=True)

        heavy_case("git_worktree_add_full_repo", git_full)

    # -- board phase ------------------------------------------------------------------------------
    def board_case(name, fn):
        board_cases.append((name, fn))

    board_case("board_read_13KB", lambda rep: p.lead_case("read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "board_read_file_13KB"))
    board_case("board_bash_echo", lambda rep: p.lead_case("bash", {"command": "echo probe"}, "board_bash_echo"))
    board_case("board_list_tasks", lambda rep: p.lead_case("list_tasks", {}, "board_list_tasks"))

    def board_cycle(rep):
        task_id = p.new_task(f"probe board cycle {rep}")
        p.lead_case("claim_task", {"task_id": task_id}, "board_claim_task")
        p.lead_case("complete_task", {"task_id": task_id}, "board_complete_task")
        mod.teammate_assignments.pop("agent", None)
        p.drop_task(task_id)

    board_case("board_claim_complete", board_cycle)

    def board_create(rep):
        out = p.lead_case("create_task", {"subject": f"probe board create {rep}"}, "board_create_task")
        if isinstance(out, str) and out.startswith("Created"):
            p.drop_task(out.split(":")[0].replace("Created", "").strip())

    board_case("board_create_task", board_create)
    board_case("board_tm_read_13KB", lambda rep: p.tm_case("probe-t", "read_file", {"path": "s15_integrated_harness/GLOSSARY.md"}, "board_tm_read_file_13KB"))

    if args.only:
        cases = [c for c in cases if args.only in c[0]]
        heavy = [c for c in heavy if args.only in c[0]]
        board_cases = [c for c in board_cases if args.only in c[0]]

    # -- shared state, as in Part A --------------------------------------------------------------
    p.anchor_task = p.new_task("probe anchor")
    mod.claim_task(p.anchor_task, owner="probe-t")
    mod.active_teammates["probe-t"] = "working"
    mod.active_teammates["probe-c"] = "working"
    mod.plan_gates["probe-t"] = "not_required"
    mod.plan_gates["probe-c"] = "not_required"
    target = dt.datetime.now() + dt.timedelta(hours=4)
    for i in range(3):
        mod.schedule_job(f"{target.minute} {target.hour} {target.day} {target.month} *", f"probe-board-{i}",
                         recurring=False, durable=False)
    mod.connect_mcp("docs")
    mod.connect_mcp("deploy")
    _, pool_handlers = mod.assemble_tool_pool()
    p.handlers = dict(pool_handlers)

    rng = random.Random(args.seed)

    def run_pass(items, warm):
        p.warm = warm
        for name, rep, fn in items:
            p.rep_index = rep
            try:
                fn(rep)
            except Exception as exc:
                p.records.append({"role": "error", "tool": name, "case": name, "rep": rep, "status": "case_error",
                                  "duration_ms": 0, "output_chars": 0, "warmup": warm, "phase": p.phase,
                                  "board": p.board, "rep_index": rep, "note": f"{type(exc).__name__}: {exc}"})
                print(f"[sweep] case {name} rep {rep} failed: {type(exc).__name__}: {exc}", flush=True)

    def schedule(items):
        out = [(name, rep, fn) for name, reps, fn in items for rep in range(reps)]
        rng.shuffle(out)
        return out

    try:
        p.phase = "main"
        if not args.warmup:
            warm = [(name, 0, fn) for name, _, fn in cases]
            rng.shuffle(warm)
            run_pass(warm, True)
        run_pass(schedule(cases), False)
        if heavy and not args.no_heavy:
            # (create_worktree for real is Part A's case A:create_worktree_real, in the main pass: it runs
            # against this lane's own small git repository, which tool_sweep_run.sh git-inits)
            p.phase = "heavy"
            run_pass(schedule(heavy), False)
        if board_cases and not args.no_board:
            p.phase = "board"
            fillers = []
            for size in BOARD_SIZES:
                while len(fillers) + 1 < size:     # +1: the anchor task
                    fillers.append(p.new_task(f"board filler {len(fillers)}"))
                p.board = size
                items = [(name, rep, fn) for name, fn in board_cases for rep in range(args.board_reps)]
                rng.shuffle(items)
                run_pass(items, False)
            for task_id in fillers:
                p.drop_task(task_id)
            p.board = None
    finally:
        with records_path.open("w", encoding="utf-8") as handle:
            for rec in p.records:
                handle.write(json.dumps(rec) + "\n")
        mod.mcp_clients.clear()
        mod.release_teammate_assignment("probe-t")
        for name in ("probe-t", "probe-c"):
            mod.active_teammates.pop(name, None)
            mod.plan_gates.pop(name, None)
        timed = [r for r in p.records if not r["warmup"] and r["role"] != "error"]
        errors = [r for r in p.records if r["role"] == "error"]
        print(f"[sweep] done: {len(timed)} timed dispatches, {len(errors)} case errors", flush=True)
    trace.emit("probe_end", {"label": args.label, "dispatches": len(timed), "case_errors": len(errors)})
    mod.close_tracing("completed")
    print(f"[sweep] records={records_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
