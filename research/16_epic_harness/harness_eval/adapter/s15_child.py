#!/usr/bin/env python3
"""One trial of the s15 harness, with its tools redirected into a Harbor task container.

Started by adapter.harbor_agent.S15Agent as a separate process (s15 keeps process-global state and installs
signal handlers, so it needs its own process and main thread):

    python s15_child.py CONFIG.json

The vendored s15 (`vendor/s15`, a read-only copy of the learn-claude-code working tree) is imported unmodified;
everything below replaces module globals that s15 looks up at call time, the way research/common/profile_run.py
does. What changes against interactive s15 (the "benchmark profile"):

  * bash / read_file / write_file / edit_file / glob act on the task container over RPC (lead, subagent and
    background bash alike); s15's own files (persisted tool outputs, snip transcripts) stay on the host;
  * permissions: everything inside the container is allowed (it is the sandbox); teams, cron, worktrees and the
    mock MCP servers are removed from the tool pool and from the prompt (the teams section would ask the user);
  * memory off (single-task runs); CONTEXT_LIMIT from the served window; vLLM's overflow 400 is re-raised as
    "prompt is too long" so reactive compaction runs, up to `reactive_limit` times;
  * a lead-call cap, an output-token cap and a wall-clock deadline end the run cleanly (digit-free errors, so
    s15's retry logic does not retry them); tool-use ids and transcript names are deterministic;
  * GAIA adds web_search and fetch_url (adapter/web_tools.py).

Outputs in the staging dir: calls.jsonl (one record per model call, request included), events.jsonl
(compaction events), result.json (status, counters, final text), history.json.
"""
from __future__ import annotations

import base64
import importlib.util
import itertools
import json
import os
import posixpath
import re
import shlex
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rpc import RpcClient  # noqa: E402

VENDOR = Path(os.environ.get("S15_VENDOR") or HERE.parent / "vendor" / "s15")
CODE = VENDOR / "s15_integrated_harness" / "code.py"
MODULE_NAME = "s15_bench_harness"

KEEP_TOOLS = ["bash", "read_file", "write_file", "edit_file", "glob", "todo_write", "task", "load_skill",
              "compact", "create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task"]
OVERFLOW = re.compile(r"maximum context length|maximum model length|context length is only|"
                      r"reduce the length of the (input )?(prompt|messages)|longer than the maximum|"
                      r"exceeds? the (model'?s? )?(maximum|max) (context|model) length", re.I)
READ_MAX_BYTES = 20_000_000


class BudgetStop(RuntimeError):
    """A budget refusal; the message must not contain 429/529/overloaded/ratelimit (with_retry) nor both
    'prompt' and 'long' (reactive compaction)."""


class PromptTooLong(RuntimeError):
    pass


def jsonable(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    for attr in ("model_dump", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return jsonable(fn())
            except Exception:                                   # noqa: BLE001
                pass
    return str(obj)


class Run:
    """Shared state of the trial: config, RPC, logs, counters."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.staging = Path(cfg["staging"]).resolve()
        self.cwd = cfg["cwd"]
        self.rpc = RpcClient(cfg["rpc_path"])
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.calls = open(self.staging / "calls.jsonl", "a", encoding="utf-8")
        self.events = open(self.staging / "events.jsonl", "a", encoding="utf-8")
        self.counter = {"calls": 0, "lead": 0, "sub": 0, "other": 0, "errors": 0, "overflows": 0,
                        "refused": 0, "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0,
                        "lead_out_tokens": 0, "tool_calls": 0, "rpc_exec": 0, "rpc_upload": 0}
        self.compactions = {}
        self.stop_reason = None
        self.has_timeout_cmd = None

    def log_call(self, rec: dict) -> None:
        with self.lock:
            self.calls.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.calls.flush()

    def log_event(self, rec: dict) -> None:
        rec.setdefault("t", round(time.time() - self.t0, 3))
        rec.setdefault("lead_calls", self.counter["lead"])
        with self.lock:
            self.events.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.events.flush()


RUN: Run | None = None


# -- model calls ------------------------------------------------------------------------------------------

def install_client_patch(run: Run) -> None:
    """Patch anthropic's Messages.create at class level before s15 loads (so the raw and the traced client
    both go through it): budgets, overflow shim, deterministic tool ids, per-call log."""
    from anthropic.resources.messages import messages as sdk_messages

    original = sdk_messages.Messages.create
    cfg = run.cfg
    max_lead = int(cfg.get("max_lead_calls") or 0)
    out_cap = int(cfg.get("out_cap") or 0)
    deadline = float(cfg.get("deadline_s") or 0)
    call_timeout = float(cfg.get("call_timeout") or 3600)

    def purpose() -> tuple[str | None, str | None]:
        harness = sys.modules.get(MODULE_NAME)
        trace = getattr(harness, "TRACE", None)
        try:
            ctx = trace.capture_context() if trace is not None else {}
        except Exception:                                       # noqa: BLE001
            ctx = {}
        return ctx.get("model_purpose"), ctx.get("agent_kind")

    def create(self, *args, **kwargs):
        what, kind = purpose()
        lead = what == "lead"
        with run.lock:
            call = run.counter["calls"]
            run.counter["calls"] += 1
        rec = {"call": call, "purpose": what, "agent_kind": kind, "t": round(time.time() - run.t0, 3),
               "max_tokens": kwargs.get("max_tokens")}
        if lead:
            reason = None
            if max_lead and run.counter["lead"] >= max_lead:
                reason = "lead call budget spent"
            elif out_cap and run.counter["lead_out_tokens"] >= out_cap:
                reason = "output token budget spent"
            elif deadline and time.time() - run.t0 >= deadline:
                reason = "wall clock budget spent"
            if reason:
                run.counter["refused"] += 1
                run.stop_reason = reason
                rec["refused"] = reason
                run.log_call(rec)
                raise BudgetStop(reason)
        if cfg.get("log_requests", True):
            rec["request"] = {"system": jsonable(kwargs.get("system")),
                              "messages": jsonable(kwargs.get("messages")),
                              "tools": [t.get("name") for t in kwargs.get("tools") or []]}
        kwargs.setdefault("timeout", call_timeout)
        if cfg.get("extra_body"):
            body = dict(kwargs.get("extra_body") or {})
            body.update(cfg["extra_body"])
            kwargs["extra_body"] = body
        epic = cfg.get("epic")
        if lead and epic:
            # EPIC round: tag the lead call for the EPIC server (epic/epic_server_ext.py). `call` is the number
            # of lead calls completed so far, so a retried call keeps its index and its common-random-number
            # seed sha256(seed_key|call) stays aligned across the arms of a block
            n = run.counter["lead"]
            body = dict(kwargs.get("extra_body") or {})
            kvp = dict(body.get("kv_transfer_params") or {})
            kvp["epic"] = {"arm": epic["arm"], "spec": epic["spec"], "call": n, "seed_key": epic["seed_key"]}
            body["kv_transfer_params"] = kvp
            kwargs["extra_body"] = body
            headers = dict(kwargs.get("extra_headers") or {})
            headers["X-Request-Id"] = f"{epic['spec']}-{n}-{call}"
            kwargs["extra_headers"] = headers
            rec["epic"] = {"arm": epic["arm"], "n": n}
        started = time.monotonic()
        try:
            response = original(self, *args, **kwargs)
        except Exception as exc:
            rec["ms"] = round((time.monotonic() - started) * 1000, 1)
            rec["error"] = f"{type(exc).__name__}: {exc}"[:2000]
            if OVERFLOW.search(str(exc)):
                with run.lock:
                    run.counter["overflows"] += 1
                rec["overflow"] = True
                run.log_call(rec)
                raise PromptTooLong("prompt is too long for the model context window") from None
            with run.lock:
                run.counter["errors"] += 1
            run.log_call(rec)
            raise
        rec["ms"] = round((time.monotonic() - started) * 1000, 1)
        tool_ids = {}
        for j, block in enumerate(b for b in response.content if getattr(b, "type", None) == "tool_use"):
            tool_ids[block.id] = f"toolu_{call:05d}_{j}"
            block.id = tool_ids[block.id]
        usage = response.usage
        cin = int(getattr(usage, "input_tokens", 0) or 0)
        cout = int(getattr(usage, "output_tokens", 0) or 0)
        cread = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        thinking = sum(len(getattr(b, "thinking", "") or "") for b in response.content
                       if getattr(b, "type", None) == "thinking")
        text = sum(len(getattr(b, "text", "") or "") for b in response.content
                   if getattr(b, "type", None) == "text")
        if epic and lead:
            extra = getattr(response, "model_extra", None) or {}
            rec["kv_transfer_params"] = jsonable(extra.get("kv_transfer_params"))
        rec.update(stop_reason=response.stop_reason, usage=jsonable(usage), thinking_chars=thinking,
                   text_chars=text, tools=[getattr(b, "name", None) for b in response.content
                                           if getattr(b, "type", None) == "tool_use"],
                   response=jsonable(response.content) if cfg.get("log_requests", True) else None)
        with run.lock:
            run.counter["lead" if lead else ("sub" if what == "one_shot" else "other")] += 1
            run.counter["in_tokens"] += cin
            run.counter["out_tokens"] += cout
            run.counter["cache_read_tokens"] += cread
            run.counter["tool_calls"] += len(tool_ids)
            if lead:
                run.counter["lead_out_tokens"] += cout
        run.log_call(rec)
        return response

    def stream(self, *args, **kwargs):
        raise RuntimeError("the benchmark profile captures Messages.create only")

    sdk_messages.Messages.create = create
    sdk_messages.Messages.stream = stream


# -- container tools ---------------------------------------------------------------------------------------

def container_path(path: str) -> str:
    path = str(path or ".")
    if posixpath.isabs(path):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(RUN.cwd, path))


def host_path(path: str) -> Path | None:
    """s15's own files (persisted tool outputs, transcripts) live in the host staging dir."""
    p = Path(str(path))
    if not p.is_absolute():
        first = p.parts[0] if p.parts else ""
        if first in (".task_outputs", ".transcripts", ".tasks", "skills"):
            return RUN.staging / p
        return None
    try:
        resolved = p.resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(RUN.staging) else None


def wrap_timeout(cmd: str, seconds: int) -> str:
    if RUN.has_timeout_cmd is None:
        out, rc = RUN.rpc.exec("command -v timeout >/dev/null 2>&1 && echo yes || echo no", None, 30)
        RUN.has_timeout_cmd = out.strip().endswith("yes")
    if RUN.has_timeout_cmd:
        return f"timeout -k 10 {int(seconds)} bash -c {shlex.quote(cmd)}"
    return cmd


def bench_bash_process(command: str, cwd=None) -> tuple[str, int | None]:
    """Replaces s15's _run_bash_process: same (output, exit code) contract, same 50,000-char cap; the
    command runs in the task container's working directory."""
    seconds = int(RUN.cfg.get("bash_timeout") or 600)
    RUN.counter["rpc_exec"] += 1
    try:
        out, rc = RUN.rpc.exec(wrap_timeout(command, seconds), RUN.cwd, seconds + 60)
    except Exception as exc:                                    # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}", None
    if rc is None or (RUN.has_timeout_cmd and rc in (124, 137) and not out.strip()):
        return f"Error: Timeout ({seconds}s)", None
    out = out.strip()
    return (out[:50000] if out else "(no output)"), rc


def _container_read(path: str) -> tuple[str | None, str | None]:
    p = container_path(path)
    q = shlex.quote(p)
    script = (f"if [ -d {q} ]; then echo __IS_DIR__; elif [ ! -e {q} ]; then echo __MISSING__; "
              f"else s=$(wc -c < {q}); if [ \"$s\" -gt {READ_MAX_BYTES} ]; then echo __TOO_LARGE__$s; "
              f"else base64 {q} | tr -d '\\n'; fi; fi")
    RUN.counter["rpc_exec"] += 1
    out, rc = RUN.rpc.exec(script, None, 120)
    out = out.strip()
    if rc is None:
        return None, f"Error: Timeout reading {path}"
    if out == "__IS_DIR__":
        return None, f"Error: [Errno 21] Is a directory: '{p}'"
    if out == "__MISSING__":
        return None, f"Error: [Errno 2] No such file or directory: '{p}'"
    if out.startswith("__TOO_LARGE__"):
        return None, f"Error: file is too large to read at once ({out[13:]} bytes); inspect it with bash"
    if rc != 0:
        return None, f"Error: {out[:2000]}"
    try:
        return base64.b64decode(out).decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"Error: 'utf-8' codec can't decode {p} (binary file?)"
    except Exception as exc:                                    # noqa: BLE001
        return None, f"Error: {type(exc).__name__}: {exc}"


def bench_read(path: str, limit=None, offset=0, cwd=None) -> str:
    hp = host_path(path)
    if hp is not None:
        return ORIG["_run_read_impl"](str(hp), limit, offset, None)
    text, err = _container_read(path)
    if err:
        return err
    lines = text.splitlines()
    offset = max(int(offset or 0), 0)
    limit = int(limit) if limit is not None else None
    lines = lines[offset:]
    if limit is not None and limit < len(lines):
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines)


def _container_write(p: str, data: bytes) -> None:
    q = shlex.quote(p)
    RUN.counter["rpc_exec"] += 1
    out, _ = RUN.rpc.exec(f"mkdir -p \"$(dirname {q})\"; stat -c %a {q} 2>/dev/null || true", None, 60)
    mode = out.strip().splitlines()[-1] if out.strip() else ""
    RUN.counter["rpc_upload"] += 1
    RUN.rpc.upload(p, data)
    if re.fullmatch(r"[0-7]{3,4}", mode):
        RUN.counter["rpc_exec"] += 1
        RUN.rpc.exec(f"chmod {mode} {q}", None, 60)


def bench_write(path: str, content: str, cwd=None) -> str:
    hp = host_path(path)
    if hp is not None:
        return ORIG["run_write"](str(hp), content, None)
    try:
        _container_write(container_path(path), content.encode("utf-8"))
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:                                    # noqa: BLE001
        return f"Error: {exc}"


def bench_edit(path: str, old_text: str, new_text: str, cwd=None) -> str:
    hp = host_path(path)
    if hp is not None:
        return ORIG["run_edit"](str(hp), old_text, new_text, None)
    text, err = _container_read(path)
    if err:
        return err
    if old_text not in text:
        return f"Error: text not found in {path}"
    try:
        _container_write(container_path(path), text.replace(old_text, new_text, 1).encode("utf-8"))
        return f"Edited {path}"
    except Exception as exc:                                    # noqa: BLE001
        return f"Error: {exc}"


GLOB_PY = r'''
import glob, os, sys
base, pat = sys.argv[1], sys.argv[2]
seen = set()
for m in glob.glob(pat, root_dir=base, recursive=True):
    full = os.path.realpath(os.path.join(base, m))
    if full == os.path.realpath(base) or full.startswith(os.path.realpath(base) + os.sep):
        seen.add(m)
for m in sorted(seen):
    print(m)
'''


def bench_glob(pattern: str, cwd=None) -> str:
    base = RUN.cwd
    q = shlex.quote
    script = (f"if command -v python3 >/dev/null 2>&1; then python3 -c {q(GLOB_PY)} {q(base)} {q(pattern)}; "
              f"else cd {q(base)} && bash -O globstar -O nullglob -c 'for f in '{q(pattern)}'; do echo \"$f\"; "
              f"done' | sort -u; fi")
    RUN.counter["rpc_exec"] += 1
    out, rc = RUN.rpc.exec(script, None, 120)
    if rc is None:
        return "Error: glob timed out"
    if rc != 0:
        return f"Error: {out.strip()[:2000]}"
    matches = [line for line in out.splitlines() if line.strip()]
    shown = matches[:200]
    if len(matches) > 200:
        shown.append("... (more matches omitted; narrow the pattern)")
    return "\n".join(shown) if shown else "(no matches)"


ORIG: dict = {}


# -- installing the benchmark profile ----------------------------------------------------------------------

def load_s15():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, CODE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


def limited_recovery_state(base, limit: int):
    """research/common/profile_run.py:403, copied: reactive compaction allowed `limit` times per loop."""
    class LimitedRecoveryState(base):
        def __init__(self):
            self._reactive_compacts = 0
            super().__init__()

        @property
        def has_attempted_reactive_compact(self):
            return self._reactive_compacts >= limit

        @has_attempted_reactive_compact.setter
        def has_attempted_reactive_compact(self, value):
            self._reactive_compacts = self._reactive_compacts + 1 if value else 0

    return LimitedRecoveryState


def install_profile(mod, run: Run) -> None:
    cfg = run.cfg
    for name in ("_run_read_impl", "run_write", "run_edit", "run_glob", "_run_bash_process", "run_read"):
        ORIG[name] = getattr(mod, name)
    mod._run_bash_process = bench_bash_process
    mod.run_read = bench_read
    mod.run_write = bench_write
    mod.run_edit = bench_edit
    mod.run_glob = bench_glob
    mod.SUB_HANDLERS = {"bash": mod.run_bash, "read_file": bench_read, "write_file": bench_write,
                        "edit_file": bench_edit, "glob": bench_glob}

    web = bool(cfg.get("web"))
    keep = list(KEEP_TOOLS)
    tools = [t for t in mod.BUILTIN_TOOLS if t["name"] in keep]
    handlers = {k: v for k, v in mod.BUILTIN_HANDLERS.items() if k in keep}
    if web:
        import web_tools
        tools += web_tools.TOOLS
        handlers["web_search"] = web_tools.run_web_search
        handlers["fetch_url"] = web_tools.run_fetch_url
        keep += ["web_search", "fetch_url"]
    mod.BUILTIN_TOOLS = tools
    mod.BUILTIN_HANDLERS = handlers
    listed = ", ".join(k for k in keep)
    mod.PROMPT_SECTIONS["tools"] = f"Available tools: {listed}."
    mod.PROMPT_SECTIONS["teams"] = ""
    mod.PROMPT_SECTIONS["workspace"] = f"Working directory: {run.cwd}"
    mod.SUB_SYSTEM = (f"You are a coding subagent at {run.cwd}. "
                      "Complete the task, then return a concise final summary. "
                      "Do not spawn more agents.")

    allowed = set(keep)

    def bench_permission(block):
        if block.name in allowed:
            return None
        return f"Permission denied: {block.name} is not available in this environment."

    hooks = mod.HOOKS["PreToolUse"]
    hooks[hooks.index(mod.permission_hook)] = bench_permission
    mod.CONSOLE.reader = lambda prompt: "n"

    runtime = mod.MEMORY_RUNTIME
    runtime.load_memories = lambda messages: ""
    runtime.read_memory_index = lambda: ""
    mod.remember_after_turn_async = lambda messages: None

    if cfg.get("context_limit"):
        mod.CONTEXT_LIMIT = int(cfg["context_limit"])
    if cfg.get("max_tokens"):
        mod.DEFAULT_MAX_TOKENS = int(cfg["max_tokens"])
    if cfg.get("max_tokens_escalated"):
        mod.ESCALATED_MAX_TOKENS = int(cfg["max_tokens_escalated"])
    mod.RecoveryState = limited_recovery_state(mod.RecoveryState, int(cfg.get("reactive_limit") or 10))

    counter = itertools.count()

    relative = bool(cfg.get("epic"))

    def write_transcript(messages):
        mod.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        path = mod.TRANSCRIPT_DIR / f"transcript_{next(counter):05d}.jsonl"
        with path.open("x", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=str) + "\n")
        # EPIC round: the snip marker carries this path; relative to the staging dir (the child's cwd) it is the
        # same in every arm and trial, so the arms' prompts stay byte-identical (host_path serves it)
        return path.relative_to(mod.WORKDIR) if relative else path

    mod.write_transcript = write_transcript

    def watch(name: str, has_active: bool):
        fn = getattr(mod, name)

        def wrapped(messages, *args, **kwargs):
            n0, c0 = len(messages), mod.estimate_size(messages)
            out = fn(messages, *args, **kwargs)
            n1, c1 = len(out), mod.estimate_size(out)
            if (n1, c1) != (n0, c0):
                run.compactions[name] = run.compactions.get(name, 0) + 1
                run.log_event({"kind": name, "msgs_before": n0, "msgs_after": n1,
                               "chars_before": c0, "chars_after": c1})
            return out

        setattr(mod, name, wrapped)

    for name in ("tool_result_budget", "snip_compact", "micro_compact", "fit_tool_results",
                 "compact_history", "reactive_compact"):
        watch(name, name in ("compact_history", "reactive_compact"))


def final_text(history: list) -> str:
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        parts = []
        for block in content or []:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype == "text":
                parts.append(block.get("text") if isinstance(block, dict) else block.text)
        if parts:
            return "\n".join(parts)
    return ""


def main() -> int:
    global RUN
    cfg = json.loads(Path(sys.argv[1]).read_text())
    staging = Path(cfg["staging"])
    staging.mkdir(parents=True, exist_ok=True)
    os.chdir(staging)
    skills = staging / "skills"
    if not skills.exists():
        skills.symlink_to(VENDOR / "skills")
    os.environ.update({
        "ANTHROPIC_BASE_URL": cfg["base_url"], "ANTHROPIC_API_KEY": "EMPTY", "MODEL_ID": cfg["model_id"],
        "HARNESS_TRACE": "1", "HARNESS_TRACE_DIR": str(staging / "traces"), "HARNESS_SYNC_MEMORY": "1",
    })
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    for key, value in (cfg.get("env") or {}).items():
        os.environ[key] = str(value)
    RUN = Run(cfg)
    install_client_patch(RUN)
    status, error = "completed", None
    history: list = []
    mod = None
    try:
        mod = load_s15()
        install_profile(mod, RUN)
        mod.initialize_tracing("s15-bench")
        prompt = cfg["prompt"]
        with mod.TRACE.agent_scope("agent-root", None, "lead"):
            context = mod.update_context({}, [])
        with mod.agent_lock:
            with mod.traced_lead_turn("user", prompt):
                mod.trigger_hooks("UserPromptSubmit", prompt)
                history.append({"role": "user", "content": prompt})
                mod.agent_loop(history, context, prompt)
        last = final_text(history)
        if RUN.stop_reason:
            status = "budget"
        elif last.startswith("[Error]"):
            status = "error"
            error = last[:2000]
    except BaseException as exc:                                # noqa: BLE001
        status, error = "crash", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-4000:]}"
    result = {"status": status, "stop_reason": RUN.stop_reason, "error": error,
              "wall_s": round(time.time() - RUN.t0, 1), "counter": RUN.counter,
              "compactions": RUN.compactions, "final_text": final_text(history)[-8000:],
              "messages": len(history)}
    if cfg.get("web"):
        try:
            import web_tools
            result["web"] = dict(web_tools.STATS)
        except Exception:                                       # noqa: BLE001
            pass
    (staging / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    try:
        (staging / "history.json").write_text(json.dumps(jsonable(history), ensure_ascii=False))
    except Exception:                                           # noqa: BLE001
        pass
    if cfg.get("epic"):
        # free the EPIC server's session rows (the round runner also ends every spec after each chunk, for
        # trials killed before they get here)
        try:
            import urllib.parse
            import urllib.request
            spec = urllib.parse.quote(str(cfg["epic"]["spec"]), safe="")
            with urllib.request.urlopen(f"{cfg['base_url'].rstrip('/')}/epic/end?spec={spec}", timeout=10) as r:
                result["epic_end"] = json.loads(r.read() or b"{}")
            (staging / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
        except Exception as exc:                                # noqa: BLE001
            print(f"epic end: {exc!r}", flush=True)
    RUN.calls.close()
    RUN.events.close()
    RUN.rpc.close()
    sys.stdout.flush()
    os._exit(0)     # s15 leaves daemon threads (trace writer, background bash); do not wait for them


if __name__ == "__main__":
    main()
