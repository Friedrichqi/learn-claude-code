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
  * --vllm-metrics URL scrapes a local vLLM server's Prometheus /metrics after every model call and
    stores the deltas (server-side prefill / decode / queue seconds, prefix-cache hits, tokens) in the
    inputs sidecar as record['vllm']; --client-max-retries 0 disables the SDK's silent retries;
    --server-info records the endpoint's /version and /v1/models in profile_meta.
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
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_redundancy import classify_bash                                          # noqa: E402
import evict_policies as ep                                                         # noqa: E402

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


EVICT_CHOICES = tuple(ep.POLICIES)


def parse_budget(text: str) -> int | None:
    """'8k' | '16384' | 'unlimited' -> bytes or None.  KiB, matching evict_policies.BUDGETS."""
    value = (text or "").strip().lower()
    if value in {"", "none", "unlimited", "inf"}:
        return None
    if value.endswith(("k", "kb", "kib")):
        return int(float(value.rstrip("bik")) * 1024)
    if value.endswith(("m", "mb", "mib")):
        return int(float(value.rstrip("bim")) * 1024 * 1024)
    return int(value)


def resident_key(entry: dict) -> tuple:
    """Identity of one recorded read, for residency-skip and for dedupe across predecessors.
    Covers both shapes: a `read_file` span is (path, offset, limit), a `bash` read is its command."""
    if entry.get("tool") == "bash":
        return ("bash", entry.get("command"))
    return ("read_file", entry.get("path"), entry.get("offset") or 0, entry.get("limit"))


def is_mutating(command: str) -> bool:
    """Mutating shell command?  Redirections are only looked for OUTSIDE quoted strings, so a
    python3 -c "... if a > b ..." comparison or a grep pattern is not mistaken for one (the
    latency_profiling runs of 2026-09-12 still used the stricter form and denied ~20 such calls)."""
    text = HARMLESS_REDIRECT.sub(" ", command)
    if REDIRECT.search(QUOTED.sub(" ", text)):
        return True
    return bool(MUTATE_CMD.search(text))


COST_POLICY = ("Tool calls are not free. Prefer the cheapest tool that can still complete the "
               "step correctly.")
SLOW_THRESHOLD_S = 1.0
PCIE_GBPS = 25          # PCIe 4.0 x16, effective
HBM_TBPS = 3.35         # HBM3


def parse_tool_costs(spec: str | None) -> dict[str, float]:
    costs: dict[str, float] = {}
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition("=")
        costs[name.strip()] = float(value)
    return costs


# -- reasoning-effort policy (Step 2 of the efficiency methodology) ---------------------------
MECHANICAL_PURPOSES = {"memory_recall", "memory_extract", "memory_consolidate",
                       "compaction_summary"}
MAIN_PURPOSES = {"lead", "teammate", "one_shot"}


def effort_for_purpose(purpose: str | None, policy: str) -> str | None:
    """Map a call's trace purpose to a vLLM effort level, or None to leave the request alone.

    'memory'      -> only the mechanical calls (memory ops, compaction) run at low effort;
                     main rounds keep the server default.
    'main-low'    -> mechanical calls AND lead/teammate/one-shot rounds at low effort.
    'main-medium' -> mechanical at low, main rounds at medium."""
    if purpose in MECHANICAL_PURPOSES:
        return "low"
    if policy == "main-low" and purpose in MAIN_PURPOSES:
        return "low"
    if policy == "main-medium" and purpose in MAIN_PURPOSES:
        return "medium"
    return None


def apply_effort(kwargs: dict, effort: str, mode: str) -> dict:
    extra = dict(kwargs.get("extra_body") or {})
    if mode == "nothink":
        template_kwargs = dict(extra.get("chat_template_kwargs") or {})
        template_kwargs["enable_thinking"] = False
        extra["chat_template_kwargs"] = template_kwargs
    else:
        extra["output_config"] = {"effort": effort}
    return dict(kwargs, extra_body=extra)


def cost_note(seconds: float, framing: str) -> str:
    """One sentence of advertised cost, in the requested wording."""
    if framing == "seconds":
        return f"Measured latency: {seconds:.2f} s per call."
    if framing == "qualitative":
        return ("This tool is slow." if seconds >= SLOW_THRESHOLD_S else "This tool is fast.")
    if framing == "hardware":
        if seconds >= SLOW_THRESHOLD_S:
            gib = seconds * PCIE_GBPS / 1.0737
            return (f"Each call stages about {gib:.0f} GiB from host DRAM across PCIe 4.0 x16 "
                    f"({PCIE_GBPS} GB/s effective) into HBM3 before it can return.")
        return (f"The working set for this tool is resident in HBM3 on the accelerator "
                f"({HBM_TBPS} TB/s); a call performs no host transfer.")
    raise ValueError(f"unknown framing {framing!r}")


def annotate_tools(tools, costs: dict[str, float], default: float | None,
                   framing: str, placement: str) -> tuple[list, str]:
    """Return (tools with cost sentences appended, cost table for the system prompt).

    Tools arrive as dicts from the harness tool tables; they are copied, never mutated in place,
    so the harness's own BUILTIN_TOOLS/SUB_TOOLS stay clean across calls.
    """
    in_desc = placement in ("tool_desc", "both")
    in_sys = placement in ("system_prompt", "both")
    out, table = [], []
    for tool in tools or []:
        if not isinstance(tool, dict):
            out.append(tool)
            continue
        name = tool.get("name")
        seconds = costs.get(name, default)
        if seconds is None:
            out.append(tool)
            continue
        note = cost_note(seconds, framing)
        copy = dict(tool)
        if in_desc:
            copy["description"] = f"{tool.get('description', '').rstrip()} {note}".strip()
        out.append(copy)
        table.append(f"- {name}: {note}")
    return out, ("Tool cost table (measured on this host):\n" + "\n".join(table)
                 if in_sys and table else "")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="experiment label written into the trace run_start data")
    parser.add_argument("--prompt", action="append", default=[], help="user turn(s), in order")
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
    parser.add_argument("--tool-cost", default=None,
                        help="intervention: advertise a per-call latency in the tool descriptions, "
                             "as 'name=seconds' pairs, e.g. 'read_file=5.0,bash=0.08'")
    parser.add_argument("--tool-cost-default", type=float, default=None,
                        help="advertised latency for every tool NOT named in --tool-cost "
                             "(omit to annotate only the named tools)")
    parser.add_argument("--tool-cost-framing", choices=["seconds", "qualitative", "hardware"],
                        default="seconds",
                        help="how the advertised cost is worded")
    parser.add_argument("--tool-cost-placement", choices=["tool_desc", "system_prompt", "both"],
                        default="tool_desc",
                        help="where the cost is shown: in each tool description, in a cost table "
                             "appended to the system prompt, or both")
    parser.add_argument("--tool-cost-policy", action="store_true",
                        help="also append a one-line cost-aware policy to the system prompt "
                             "(exposing the cost is not the same as asking the agent to act on it)")
    parser.add_argument("--effort-policy", choices=["off", "memory", "main-low", "main-medium"],
                        default="off",
                        help="reasoning-effort policy (Step 2): 'memory' runs only the mechanical "
                             "calls (memory ops, compaction) at low effort; 'main-low' / "
                             "'main-medium' also set lead/teammate/one-shot rounds.  Requires a "
                             "provider that accepts output_config or chat_template_kwargs.")
    parser.add_argument("--effort-mode", choices=["output_config", "nothink"], default="output_config",
                        help="how the effort level reaches the server: vLLM's output_config.effort, "
                             "or chat_template_kwargs.enable_thinking=false (binary, ignores level)")
    parser.add_argument("--max-teammate-concurrent", type=int, default=0,
                        help="cap in-flight teammate model requests (0 = uncapped).  Lead and "
                             "harness calls never wait behind more than this many teammate "
                             "decodes -- client-side priority scheduling.")
    parser.add_argument("--no-retry-429", action="store_true",
                        help="disable the driver-level retry of provider 429s (the harness itself retries only lead calls, 3x)")
    parser.add_argument("--trace-output", choices=["summary", "full"], default="summary",
                        help="HARNESS_TRACE_OUTPUT mode: 'full' stores every tool result verbatim in the trace")
    parser.add_argument("--no-reads-log", action="store_true",
                        help="do not write the <trace>.reads.jsonl content sidecar")
    parser.add_argument("--prewarm",
                        choices=["none", "oracle", "dag", "ancestors", "pollute", "summary"],
                        default="none",
                        help="emulate KV inheritance: give a teammate the file content a predecessor "
                             "task already read, as a synthetic read_file tool_use/tool_result pair "
                             "at the head of its history. 'dag' takes the DIRECT predecessors' "
                             "reads live from this run; 'ancestors' takes the whole transitive "
                             "closure (predecessors of predecessors too); "
                             "from this run; 'oracle' takes them from --prewarm-spec; 'pollute' sends "
                             "an equal-size irrelevant file; 'summary' sends the predecessors' result "
                             "text instead of the bytes")
    parser.add_argument("--prewarm-spec", default=None,
                        help="JSON written by --prewarm-dump on a baseline run: per task-creation "
                             "index, the reads to inject (used by oracle and to size pollute)")
    parser.add_argument("--prewarm-pollute", default="s15_integrated_harness/ARCHITECTURE.md",
                        help="file the pollute arm injects (truncated to the matched size)")
    parser.add_argument("--prewarm-max-bytes", type=int, default=200_000,
                        help="cap on injected bytes per teammate")
    parser.add_argument("--prewarm-evict", default="lru", choices=EVICT_CHOICES,
                        help="how the inherited pool is cut down to --prewarm-budget.  The pool is "
                             "the arm's candidate set; the policy decides what survives.  See "
                             "scripts/evict_policies.py -- the same module the offline sweep uses, "
                             "so live and replayed cells are comparable.")
    parser.add_argument("--prewarm-budget", default="unlimited",
                        help="byte cap on what one agent inherits: an int, '8k'/'16k'/'32k'/'64k', "
                             "or 'unlimited'.  Distinct from --prewarm-max-bytes, which is the old "
                             "unconditional cap and stays as an outer safety limit.")
    parser.add_argument("--prompt-file", action="append", default=[],
                        help="read a user turn from this file instead of argv.  Long-context task "
                             "prompts (longbench2, graphwalks, ruler) exceed Linux's 128 KB "
                             "MAX_ARG_STRLEN and fail the exec with Errno 7 before the harness "
                             "starts, so any driver feeding real benchmark documents needs this.")
    parser.add_argument("--model", default=None,
                        help="override MODEL_ID for this run.  code.py:77 calls "
                             "load_dotenv(override=True), so the .env wins over the process "
                             "environment and exporting MODEL_ID has no effect -- this patches the "
                             "loaded module's globals instead, which is why the earlier studies "
                             "concluded a cross-model run needed a whole separate lane.")
    parser.add_argument("--vllm-metrics", default=None,
                        help="URL of a vLLM Prometheus endpoint (http://host:port/metrics).  After every "
                             "model call the driver scrapes it under a lock and stores the deltas since the "
                             "previous scrape in the inputs sidecar as record['vllm']: server-side prefill / "
                             "decode / queue / inference seconds, prefix-cache hits, prompt and generation "
                             "tokens, finished requests.  When exactly one request finished in between, the "
                             "deltas are this call's own (record['vllm']['exact']); with concurrent teammates "
                             "they may pool several requests and only the run totals (profile_end.vllm_totals) "
                             "stay exact.  The scrape itself (~5-10 ms) is recorded as scrape_ms so the "
                             "analyzer can keep it out of the harness buckets.")
    parser.add_argument("--client-max-retries", type=int, default=None,
                        help="set the Anthropic client's max_retries before tracing wraps it (0 disables the "
                             "SDK's silent retries, which otherwise double a slow request's load and hide "
                             "provider errors; the harness keeps its own lead-call retries)")
    parser.add_argument("--server-info", action="store_true",
                        help="record GET /version and /v1/models of the configured base URL in profile_meta "
                             "(vLLM exposes both; other providers may 404)")
    parser.add_argument("--answer-out", default=None,
                        help="write the lead's final assistant text here.  This is how the lm-eval "
                             "endpoint gets an answer back out of an agentic session: lm-eval hands "
                             "us a task prompt, the harness runs its rounds, and the last thing the "
                             "lead says is scored by the task's own process_results.")
    parser.add_argument("--prewarm-dump", default=None,
                        help="write per-task read sets and result texts of this run to this JSON "
                             "(this is how an oracle spec is built from a baseline run)")
    args = parser.parse_args()
    if args.prompt_file:
        args.prompt = list(args.prompt or []) + [
            Path(f).read_text(encoding="utf-8") for f in args.prompt_file]
    if not args.prompt:
        parser.error("pass --prompt or --prompt-file")
    if args.prewarm_evict in ep.CEILING:
        parser.error(f"--prewarm-evict {args.prewarm_evict} is a CEILING: it ranks on what the "
                     "successor will go on to read, which no engine knows at handoff time.  It is "
                     "computable offline in replay_sweep.py and nowhere else.")
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


class VllmMetrics:
    """Scrape a vLLM Prometheus endpoint and attribute server-side request timing to model calls.

    vLLM records a finished request's prefill / decode / queue / inference seconds, its prompt and generation
    tokens and the prefix-cache hits into Prometheus histograms and counters in the same engine-loop
    iteration that releases the final output, i.e. before the final stream chunk reaches the client.
    `after_call()` therefore scrapes right after a response is complete and takes the deltas since the
    previous scrape (all scrapes are serialized by one lock, so consecutive deltas never overlap).  If
    exactly one request finished in between, the deltas are this call's own (`exact`); when concurrent
    teammates finish together the deltas pool several requests and only the run totals stay exact.
    """

    HISTOGRAMS = ("request_prefill_time_seconds", "request_decode_time_seconds", "request_queue_time_seconds",
                  "request_inference_time_seconds", "e2e_request_latency_seconds", "time_to_first_token_seconds",
                  "request_prefill_kv_computed_tokens", "request_generation_tokens", "request_prompt_tokens")
    COUNTERS = ("request_success", "prompt_tokens", "prompt_tokens_cached", "generation_tokens",
                "prefix_cache_queries", "prefix_cache_hits", "num_preemptions")
    GAUGES = ("num_requests_running", "num_requests_waiting", "kv_cache_usage_perc")
    LINE = re.compile(r"^vllm:(?P<name>[A-Za-z0-9_]+)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)")
    LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')

    def __init__(self, url: str, lock: threading.Lock, retry_s: float = 1.0, step_s: float = 0.05):
        self.url = url
        self.lock = lock
        self.retry_s = retry_s
        self.step_s = step_s
        self.baseline: dict | None = None
        self.last: dict | None = None
        self.errors = 0

    def scrape(self) -> dict | None:
        import urllib.request
        try:
            with urllib.request.urlopen(self.url, timeout=2.0) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception:
            self.errors += 1
            return None
        snap: dict = {"ts": time.time()}
        for line in text.splitlines():
            if not line.startswith("vllm:"):
                continue
            m = self.LINE.match(line)
            if not m:
                continue
            name, labels, value = m.group("name"), m.group("labels") or "", m.group("value")
            try:
                v = float(value)
            except ValueError:
                continue
            if name.endswith(("_created", "_bucket")):
                continue
            if name.endswith("_total"):
                name = name[:-6]
            base = name
            for suffix in ("_sum", "_count"):
                if name.endswith(suffix):
                    base = name[: -len(suffix)]
            if base in self.HISTOGRAMS or name in self.COUNTERS or name in self.GAUGES:
                snap[name] = snap.get(name, 0.0) + v
                if name == "request_success":
                    reason = dict(self.LABEL.findall(labels)).get("finished_reason", "?")
                    key = f"request_success[{reason}]"
                    snap[key] = snap.get(key, 0.0) + v
        return snap

    @staticmethod
    def _delta(now: dict, prev: dict, key: str):
        if key not in now:
            return None
        return now[key] - prev.get(key, 0.0)

    def start(self) -> dict | None:
        with self.lock:
            self.baseline = self.last = self.scrape()
        return self.baseline

    def after_call(self) -> dict:
        with self.lock:
            prev = self.last
            started = time.perf_counter()
            retries = 0
            now = self.scrape()
            while (now is not None and prev is not None
                   and (now.get("request_success", 0.0) - prev.get("request_success", 0.0)) < 1
                   and time.perf_counter() - started < self.retry_s):
                time.sleep(self.step_s)
                retries += 1
                now = self.scrape()
            if now is None:
                return {"error": "scrape failed", "errors": self.errors}
            self.last = now
            if prev is None:
                return {"error": "no previous snapshot"}
            out = self._summarize(now, prev)
            out["scrape_ms"] = round((time.perf_counter() - started) * 1000, 1)
            out["retries"] = retries
            return out

    def _summarize(self, now: dict, prev: dict) -> dict:
        d = lambda key: self._delta(now, prev, key)  # noqa: E731
        finished = {k[len("request_success["):-1]: now[k] - prev.get(k, 0.0) for k in now if k.startswith("request_success[")}
        success = d("request_success") or 0.0
        return {
            "exact": success == 1,
            "finished_requests": success,
            "finished": {k: v for k, v in finished.items() if v},
            "prefill_s": d("request_prefill_time_seconds_sum"),
            "decode_s": d("request_decode_time_seconds_sum"),
            "queue_s": d("request_queue_time_seconds_sum"),
            "inference_s": d("request_inference_time_seconds_sum"),
            "e2e_s": d("e2e_request_latency_seconds_sum"),
            "ttft_s": d("time_to_first_token_seconds_sum"),
            "kv_computed_tokens": d("request_prefill_kv_computed_tokens_sum"),
            "request_prompt_tokens": d("request_prompt_tokens_sum"),
            "request_generation_tokens": d("request_generation_tokens_sum"),
            "prompt_tokens": d("prompt_tokens"),
            "prompt_tokens_cached": d("prompt_tokens_cached"),
            "generation_tokens": d("generation_tokens"),
            "prefix_cache_queries": d("prefix_cache_queries"),
            "prefix_cache_hits": d("prefix_cache_hits"),
            "preemptions": d("num_preemptions"),
            "running": now.get("num_requests_running"),
            "waiting": now.get("num_requests_waiting"),
            "kv_usage": now.get("kv_cache_usage_perc"),
            "span_s": round(now["ts"] - prev["ts"], 3),
        }

    def totals(self) -> dict | None:
        """Deltas from the run's baseline snapshot to a fresh scrape (exact run totals)."""
        with self.lock:
            now = self.scrape()
            if now is None or self.baseline is None:
                return None
            self.last = now
            out = self._summarize(now, self.baseline)
            out["scrape_errors"] = self.errors
            return out


def fetch_server_info(base_url: str | None) -> dict | None:
    """GET /version and /v1/models of an OpenAI/Anthropic-compatible server (vLLM exposes both)."""
    if not base_url:
        return None
    import urllib.request
    info: dict = {"base_url": base_url}
    for key, path in (("version", "/version"), ("models", "/v1/models")):
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=5.0) as resp:
                raw = resp.read().decode("utf-8", "replace")
            try:
                info[key] = json.loads(raw)
            except json.JSONDecodeError:
                info[key] = raw[:500]
        except Exception as exc:
            info[key] = f"error: {type(exc).__name__}: {exc}"
    return info


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
    if args.model:
        # every call site reads the module global at call time (code.py:1739, 2182, 2451), and the
        # retry state machine seeds itself from PRIMARY_MODEL, so both have to move together
        mod.MODEL = mod.PRIMARY_MODEL = args.model
        # MEMORY_RUNTIME is the s09 module loaded at import time with a COPY of MODEL, so the
        # memory-extraction calls would keep using the old id unless it is moved too
        mod.MEMORY_RUNTIME.MODEL = args.model
        print(f"[profile] MODEL_ID overridden -> {args.model}", flush=True)
    if args.client_max_retries is not None:
        # the SDK reads self.max_retries per request; set it on the raw client BEFORE initialize_tracing()
        # wraps it in TracedClient (an attribute write would otherwise land on the wrapper)
        mod.client.max_retries = args.client_max_retries
        print(f"[profile] client max_retries -> {args.client_max_retries}", flush=True)
    server_info = fetch_server_info(os.environ.get("ANTHROPIC_BASE_URL")) if args.server_info else None
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
    trace.emit("profile_meta", {
        "prewarm": {"arm": args.prewarm, "spec": args.prewarm_spec,
                    "pollute": args.prewarm_pollute, "max_bytes": args.prewarm_max_bytes,
                    "evict": args.prewarm_evict, "budget": parse_budget(args.prewarm_budget)},"label": args.label, "prompts": args.prompt,
                                "allow_writes": args.allow_writes, "driver": "scripts/profile_run.py",
                                "no_timestamp": args.no_timestamp, "context_limit": mod.CONTEXT_LIMIT,
                                "git_head": git_head, "trace_output": args.trace_output,
                                "reads_log": not args.no_reads_log, "stream": args.stream,
                                "write_root": args.write_root, "sandbox_from": args.sandbox_from,
                                "allow_python": args.allow_python, "prep_timing": not args.no_prep_timing,
                                "tool_cost": args.tool_cost, "tool_cost_default": args.tool_cost_default,
                                "tool_cost_framing": args.tool_cost_framing,
                                "tool_cost_placement": args.tool_cost_placement,
                                "tool_cost_policy": args.tool_cost_policy,
                                "repo": str(REPO),
                                "model": mod.MODEL, "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
                                "vllm_metrics": args.vllm_metrics, "client_max_retries": args.client_max_retries,
                                "server_info": server_info})
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

    # -- context handoff: emulate inheriting a predecessor agent's KV ----------------------
    # The provider cannot transfer KV, so we hand the successor the same TOKENS instead: the exact
    # assistant tool_use / user tool_result pair a real read_file would have produced.  What that
    # removes is the agent-loop round the successor would have spent opening the file; what it costs
    # is prefill of bytes the baseline prefills one round later anyway.
    prewarm_order: list[str] = []            # task ids in creation order (the spec's key)
    prewarm_reads: dict[str, list[dict]] = defaultdict(list)   # task id -> reads it issued
    prewarm_agent_reads: dict[str, list[dict]] = defaultdict(list)   # agent name -> reads it issued
    prewarm_results: dict[str, str] = {}     # task id -> the result text it sent to the lead
    prewarm_blocks: dict[str, list] = defaultdict(list)        # agent id -> injected messages
    prewarm_done: dict[str, set] = defaultdict(set)            # agent id -> tasks already injected
    prewarm_log: list[dict] = []
    prewarm_skips: dict[str, int] = {}
    prewarm_lock = threading.Lock()
    prewarm_budget = parse_budget(args.prewarm_budget)
    prewarm_spec = {}
    if args.prewarm_spec:
        prewarm_spec = json.loads(Path(args.prewarm_spec).read_text(encoding="utf-8"))

    def current_owner() -> str | None:
        name = threading.current_thread().name
        return name[len("teammate-"):] if name.startswith("teammate-") else None

    def current_task(owner: str | None) -> str | None:
        if not owner:
            return None
        assignment = mod.teammate_assignments.get(owner) or {}
        return assignment.get("task_id")

    original_create_task = mod.create_task

    def traced_create_task(subject, description=""):
        task = original_create_task(subject, description)
        with prewarm_lock:
            prewarm_order.append(task.id)
        return task

    mod.create_task = traced_create_task

    original_send = mod.BUS.send

    def traced_send(from_agent, to_agent, content, msg_type="message", metadata=None):
        if msg_type == "result":
            task_id = current_task(from_agent)
            if task_id:
                with prewarm_lock:
                    prewarm_results[task_id] = content
        return original_send(from_agent, to_agent, content, msg_type, metadata)

    mod.BUS.send = traced_send

    def record_read(block):
        """Second PreToolUse hook: index every read by the task that issued it."""
        if block.name not in {"read_file", "bash"}:
            return None
        task_id = current_task(current_owner())
        if not task_id:
            return None
        payload = dict(block.input or {})
        entry = ({"tool": "read_file", "path": payload.get("path"),
                  "offset": payload.get("offset") or 0, "limit": payload.get("limit")}
                 if block.name == "read_file" else
                 {"tool": "bash", "command": payload.get("command", "")})
        owner = current_owner()
        with prewarm_lock:
            if entry not in prewarm_reads[task_id]:
                prewarm_reads[task_id].append(entry)
            if owner and entry not in prewarm_agent_reads[owner]:
                prewarm_agent_reads[owner].append(entry)
        return None

    hooks.insert(0, record_read)

    def read_bytes(entry: dict) -> tuple[str, dict, str] | None:
        """Reproduce a recorded read with the harness's own reader, so the injected tokens are the
        ones a real read would have produced.  Returns (tool, tool_input, text).

        Both read shapes are replayed.  `read_file` goes through `run_read`; a `bash` call is
        replayed through `run_bash` only when `classify_bash` (the same classifier the measurement
        side uses) says it is a file reader AND it clears the mutation guard.  Before this, the
        injector dropped every bash read while `input_redundancy` counted them in the recall
        denominator, so the two sides were measuring different sets: across the 28 prewarm dumps on
        disk there are 301 bash entries against 373 read_file entries."""
        tool = entry.get("tool")
        if tool == "read_file":
            if not entry.get("path"):
                return None
            text = mod.run_read(entry["path"], entry.get("limit"), entry.get("offset") or 0,
                                cwd=mod.WORKDIR)
            if text.startswith("Error:"):
                return None
            payload = {"path": entry["path"]}
            if entry.get("offset"):
                payload["offset"] = entry["offset"]
            if entry.get("limit"):
                payload["limit"] = entry["limit"]
            return "read_file", payload, text
        if tool == "bash":
            command = entry.get("command") or ""
            is_reader, _ = classify_bash(command)
            if not is_reader or is_mutating(command):
                return None
            try:
                text = mod.run_bash(command, cwd=mod.WORKDIR)
            except Exception:                                                  # noqa: BLE001
                return None
            if not text or text.startswith("Error:"):
                return None
            return "bash", {"command": command}, text
        return None

    def sources_for(task_id: str) -> list[str]:
        """Direct predecessors only."""
        try:
            return list(mod.load_task(task_id).blockedBy)
        except Exception:
            return []

    def ancestors_for(task_id: str) -> list[str]:
        """The whole transitive closure of blockedBy, OLDEST FIRST.

        In a chain A -> B -> C the direct arm hands C only B's reads; this hands it A's too.  The two
        differ only at depth 3 or more, which is the entire reason this arm exists.  Ordering by
        distance from the successor keeps the injected block in the order the work actually happened,
        which the earlier study found matters: a handoff that reads like a coherent history is
        trusted, a stitched-together one is re-read."""
        depth: dict[str, int] = {}
        frontier = [(t, 1) for t in sources_for(task_id)]
        while frontier:
            node, d = frontier.pop()
            if node in depth and depth[node] >= d:
                continue
            depth[node] = d
            frontier.extend((p, d + 1) for p in sources_for(node))
        # deepest (earliest in the DAG) first
        return [t for t, _ in sorted(depth.items(), key=lambda kv: -kv[1])]

    def spec_reads(task_id: str) -> list[dict]:
        """The oracle arm's frozen read set, keyed by the task's creation index."""
        try:
            index = prewarm_order.index(task_id)
        except ValueError:
            return []
        return (prewarm_spec.get("tasks") or {}).get(str(index), {}).get("reads", [])

    def build_blocks(task_id: str) -> list[dict]:
        """The (assistant tool_use, user tool_result) pairs to prepend for this task."""
        arm = args.prewarm
        entries: list[dict] = []
        if arm == "oracle":
            seen = set()
            for entry in spec_reads(task_id):
                key = resident_key(entry)
                if key not in seen:
                    seen.add(key)
                    entries.append(entry)
        elif arm in {"dag", "ancestors", "pollute"}:
            # several predecessors usually read the SAME span; dedupe on resident_key so a fan-in
            # does not inject three copies of it
            seen = set()
            chain = ancestors_for(task_id) if arm == "ancestors" else sources_for(task_id)
            with prewarm_lock:
                searches = [e.get("command", "") for s in chain
                            for e in prewarm_reads.get(s, []) if e.get("tool") == "bash"]
                index: dict[tuple, dict] = {}
                for chain_idx, source in enumerate(chain):
                    for entry in prewarm_reads.get(source, []):
                        key = resident_key(entry)
                        if key in seen:
                            index[key]["_freq"] = index[key].get("_freq", 1) + 1
                            continue
                        seen.add(key)
                        tagged = dict(entry, _source=source, _chain_idx=chain_idx, _freq=1)
                        path = entry.get("path") or ""
                        tagged["_located"] = bool(path) and any(path in c for c in searches)
                        index[key] = tagged
                        entries.append(tagged)
        elif arm == "summary":
            with prewarm_lock:
                texts = [prewarm_results.get(s) for s in sources_for(task_id)]
            texts = [t for t in texts if t]
            if not texts:
                return []
            joined = "\n\n".join(texts)[: args.prewarm_max_bytes]
            return _pair("prior_result", {"from_tasks": sources_for(task_id)}, joined)
        if arm == "pollute":
            # match the size the content arm would have sent, with an irrelevant file
            want = sum(len(got[2]) for got in (read_bytes(e) for e in entries) if got)
            if not want:
                return []
            text = mod.run_read(args.prewarm_pollute, None, 0, cwd=mod.WORKDIR)[:want]
            return _pair("read_file", {"path": args.prewarm_pollute}, text)
        # do not send what the successor already holds: its own history is never cleared, so a file
        # it read under an earlier task is still resident and re-sending it only costs prefill
        owner = current_owner()
        with prewarm_lock:
            resident = {resident_key(e) for e in prewarm_agent_reads.get(owner or "", [])}

        # 1. replay every candidate read, so selection can price real byte sizes rather than guesses
        direct = set(sources_for(task_id))
        closure = set(ancestors_for(task_id))
        replayed: list[tuple[dict, str, dict, str]] = []
        skipped = 0
        for entry in entries:
            if resident_key(entry) in resident:
                skipped += 1
                continue
            got = read_bytes(entry)
            if got:
                replayed.append((entry, *got))
        prewarm_skips[task_id] = skipped

        # 2. the retention decision, through the SAME module the offline sweep uses.  `last_ms` is
        #    the position in the chain, which is oldest-predecessor-first, so LRU means "keep what
        #    the most recent predecessor read".  A whole source task is one `round_idx`, so the
        #    sliding-window policy keeps whole predecessors rather than slicing one.
        candidates = []
        for i, (entry, tool, payload, text) in enumerate(replayed):
            src = entry.get("_source")
            candidates.append(ep.Item(
                key=resident_key(entry), nbytes=len(text), round_idx=entry.get("_chain_idx", 0),
                first_ms=float(i), last_ms=float(i), freq=entry.get("_freq", 1),
                tier=0 if src in direct else (1 if src in closure else 2),
                locate_s=ep.FIXED_ROUND_S if entry.get("_located") else 0.0))
        keep = ep.retained(candidates, args.prewarm_evict, prewarm_budget) if candidates else set()

        blocks: list[dict] = []
        outer = args.prewarm_max_bytes                  # the old unconditional cap, kept as a guard
        for item, (entry, tool, payload, text) in zip(candidates, replayed):
            if item.key not in keep or len(text) > outer:
                continue
            outer -= len(text)
            blocks.extend(_pair(tool, payload, text))
        return blocks

    def _pair(tool: str, payload: dict, text: str) -> list[dict]:
        use_id = "prewarm_" + hashlib.sha1(
            f"{tool}{json.dumps(payload, sort_keys=True)}{len(text)}".encode()).hexdigest()[:16]
        return [{"role": "assistant",
                 "content": [{"type": "tool_use", "id": use_id, "name": tool, "input": payload}]},
                {"role": "user",
                 "content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}]

    def prewarm_messages(ctx: dict, messages: list) -> list:
        """Prepend this agent's inherited blocks after its opening user turn.  Blocks are built once
        per (agent, task) and reused verbatim, so the injected prefix is byte-stable and the
        provider's prefix cache behaves exactly as it does without the injection."""
        agent = ctx.get("agent_id")
        owner = current_owner()
        task_id = current_task(owner)
        if not agent or not messages:
            return messages
        if task_id and task_id not in prewarm_done[agent]:
            prewarm_done[agent].add(task_id)
            blocks = build_blocks(task_id)
            if blocks:
                prewarm_blocks[agent].extend(blocks)
                chars = sum(len(b["content"][0].get("content", ""))
                            for b in blocks if b["role"] == "user")
                record = {"agent_id": agent, "owner": owner, "task_id": task_id,
                          "arm": args.prewarm, "pairs": len(blocks) // 2, "chars": chars,
                          "sources": sources_for(task_id),
                          "ancestors": ancestors_for(task_id),
                          "skipped_resident": prewarm_skips.get(task_id, 0)}
                prewarm_log.append(record)
                trace.emit("context_injection", record)
                print(f"  \033[36m[prewarm] {owner} task={task_id} arm={args.prewarm} "
                      f"pairs={len(blocks) // 2} chars={chars}\033[0m", flush=True)
        blocks = prewarm_blocks.get(agent)
        if not blocks:
            return messages
        return [messages[0]] + blocks + list(messages[1:])

    # -- input-composition profiler around the traced client ------------------------------
    traced_messages = mod.client.messages
    tool_costs = parse_tool_costs(args.tool_cost)
    if tool_costs or args.tool_cost_default is not None:
        print(f"[profile] tool cost: {tool_costs or '{}'} default={args.tool_cost_default} "
              f"framing={args.tool_cost_framing} placement={args.tool_cost_placement}", flush=True)
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

    vllm_metrics = VllmMetrics(args.vllm_metrics, threading.Lock()) if args.vllm_metrics else None
    if vllm_metrics is not None:
        baseline = vllm_metrics.start()
        print(f"[profile] vllm metrics {'reachable' if baseline else 'UNREACHABLE'} at {args.vllm_metrics}", flush=True)

    # Client-side priority (Step 6): cap how many teammate decode requests are in flight at
    # once, so lead-critical calls schedule immediately instead of queueing behind the whole
    # team (Autellix-style ordering, applied where we control it -- the client).
    teammate_slots = (threading.Semaphore(args.max_teammate_concurrent)
                      if args.max_teammate_concurrent > 0 else None)

    def profiled_create(**kwargs):
        # -- advertised-tool-cost intervention ---------------------------------------------
        # Rewriting the request here (rather than the harness's tool tables) annotates the lead,
        # the teammates and the one-shot subagents in one place, and keeps the annotation out of
        # the auxiliary calls that carry no tools at all (memory extraction, summarisation).
        if (tool_costs or args.tool_cost_default is not None) and kwargs.get("tools"):
            annotated, table = annotate_tools(kwargs["tools"], tool_costs,
                                              args.tool_cost_default, args.tool_cost_framing,
                                              args.tool_cost_placement)
            kwargs = dict(kwargs, tools=annotated)
            extra = "\n\n".join(part for part in
                                (table, COST_POLICY if args.tool_cost_policy else "") if part)
            if extra:
                kwargs["system"] = ((kwargs.get("system") or "") + "\n\n" + extra).strip()
        ctx = trace.capture_context()
        # -- reasoning-effort policy (Step 2): decode is 94-98% of server time and thinking is
        # 33-68% of Qwen's output, so mechanical calls run at low effort and main rounds follow
        # the arm's policy.  vLLM takes output_config.effort; --effort-mode nothink instead
        # passes chat_template_kwargs.enable_thinking=false for providers whose template
        # ignores effort levels. --------------------------------------------------------------
        if args.effort_policy != "off":
            effort = effort_for_purpose(ctx.get("model_purpose"), args.effort_policy)
            if effort:
                kwargs = apply_effort(kwargs, effort, args.effort_mode)
        # -- context-handoff injection (must precede the accounting below so the injected bytes are
        # counted by the same instruments as everything else) -------------------------------------
        if (args.prewarm != "none" and ctx.get("agent_kind") == "teammate"
                and ctx.get("model_purpose") in (None, "teammate") and kwargs.get("messages")):
            rewritten = prewarm_messages(ctx, kwargs["messages"])
            if rewritten is not kwargs["messages"]:
                kwargs = dict(kwargs, messages=rewritten)
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
        gate = (teammate_slots
                if teammate_slots is not None and ctx.get("agent_kind") == "teammate"
                and ctx.get("model_purpose") in ("teammate", None) else None)
        if gate is not None:
            gate.acquire()
        try:
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
        finally:
            if gate is not None:
                gate.release()
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
        if vllm_metrics is not None:
            record["vllm"] = vllm_metrics.after_call()
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
                                   "wall_seconds": round(time.monotonic() - started, 1),
                                   "vllm_totals": vllm_metrics.totals() if vllm_metrics is not None else None})
        flush = getattr(mod, "flush_memory", None)
        if flush is not None:
            flush(180)  # let a background extraction finish so its spans land in the trace
        mod.close_tracing(status)
        if args.answer_out:
            # the last thing the lead said.  lm-eval scores a single string, so an agentic session
            # has to end in one: the task's own filters extract from it (a #### answer, a boxed
            # expression, a code block), which is why the answer contract is appended to the prompt.
            text = ""
            for message in reversed(history):
                if message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    # history holds the SDK's own block objects (code.py:1745, 3535, 3555), not
                    # dicts, so both shapes have to be read -- and the `thinking` blocks that GLM
                    # returns first must be skipped or the answer comes back empty
                    parts = []
                    for block in content:
                        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                        if kind != "text":
                            continue
                        piece = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
                        if piece:
                            parts.append(piece)
                    text = "\n".join(parts)
                if text.strip():
                    break
            Path(args.answer_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.answer_out).write_text(json.dumps(
                {"label": args.label, "status": status, "answer": text,
                 "wall_seconds": round(time.monotonic() - started, 2)}), encoding="utf-8")
            print(f"[profile] answer={len(text)} chars -> {args.answer_out}", flush=True)
        if args.prewarm_dump:
            dump = {"label": args.label, "arm": args.prewarm,
                    "tasks": {str(i): {"task_id": tid,
                                       "reads": prewarm_reads.get(tid, []),
                                       "result_chars": len(prewarm_results.get(tid, ""))}
                              for i, tid in enumerate(prewarm_order)},
                    "injections": prewarm_log}
            Path(args.prewarm_dump).write_text(json.dumps(dump, indent=2), encoding="utf-8")
            print(f"[profile] prewarm dump={args.prewarm_dump}", flush=True)
        try:
            archive_sandbox()
        except Exception as exc:
            print(f"[profile] sandbox archive failed: {type(exc).__name__}: {exc}", flush=True)
        print(f"\n[profile] done status={status} denials={dict(denials)} rate_limit_retries={dict(rate_limited)} wall={time.monotonic() - started:.0f}s", flush=True)
        print(f"[profile] trace={trace.path}\n[profile] inputs={inputs_path}\n[profile] reads={reads_path}", flush=True)
    return 0 if status in {"completed", "teammates-idle"} else 1


if __name__ == "__main__":
    sys.exit(main())
