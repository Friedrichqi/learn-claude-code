#!/usr/bin/env python3
"""E0b lookahead (rule D4): which of s15's context edits, and which edit tokens, have their surviving layout fixed
at round start, at decode end and at tool end.

Data: every harness_eval profile trial (hs_common.he_trials(): q-phase, the four models x swe / swepro / tbench);
each calls.jsonl record holds the full lead request (system, messages, tool names) and the reply.

For every consecutive pair of successful lead calls (j, n) of a trial the script

1. classifies the transition. The cached sequence is request j + reply j (what exact prefix caching holds after
   call j). The transition is append-only when request n extends it; otherwise its kind is the edit that made the
   first differing message: snip (the archive marker), placeholder (micro_compact), fit preview, summary
   (compact_history or the compact tool), reactive, or other. tool_result_budget only rewrites the newest user
   message, which is new content, so it never makes an edit region (counted as "append, new content trimmed").
2. counts its edit tokens: the tokens of request n after the first divergence that are not newly appended content
   (the tool-result message, notices and the todo reminder added after reply j), i.e. what exact prefix caching
   re-prefills because of the edit, including the few bytes the edit inserts (marker, placeholder, preview).
   Tokens: the Qwen3-32B tokenizer on a simple per-message render (Qwen3 im_start/im_end framing, the append-only
   template's think block, tool calls as <tool_call> JSON, tool results as <tool_response>); a message's tokens do
   not depend on its neighbours, so prefixes compare message by message, then token by token inside the first
   differing message. Tool schemas are not logged (names only); they sit in the static prefix and never move
   (TOOL_SCHEMA_TOKENS, calibrated on qwen3-32b's served prompt tokens). Exact prefix caching, no eviction.
3. re-runs s15's edit rules (reimplemented below; line numbers are s15_integrated_harness/code.py, sha256
   a6aa29a0..., byte-identical to harness_eval/vendor/s15) on the state the harness has at three decision points,
   filling in what is not known yet:
     (i)   round start: request j only; reply j assumed to be one foreground non-todo tool call with an empty
           result (so the todo counter advances by one);
     (ii)  decode end: + reply j; every tool result empty; todo counter advanced by reply j's tool calls exactly as
           agent_loop does (code.py:3698-3763); max_tokens retry / continuation as code.py:3640-3672;
     (iii) tool end: + the real tool results (originals restored from the trial's saved outputs when a fit or
           budget preview replaced them), background notices and the reminder. LLM summaries stay unknown.
   A token of the edit region is "known" at a decision point when the prediction puts the same message (same
   rendered bytes; reply j by identity, since at round start it is not decoded yet) at the same token offset.
   Content whose size is unknown (an LLM summary) makes every later offset unknown. An edit is "fully known" when
   all of its edit tokens are. The transcript counter in the marker path comes from events.jsonl (every snip /
   summary / reactive event wrote one transcript, s15_child.py:475-501). Transitions with an overflowed call in
   between (vLLM's 400, then reactive_compact and its summary call) are taken from the log: nothing after the
   summary's unknown bytes is known at any point.

Rule D4 (README section 2): passes if >= 50 % of edit tokens have their layout fixed at decode end (ii).

Fidelity checks reported with the table: (iii) re-run with the real summaries reproduces request n exactly;
the reimplemented estimate_size reproduces the adapter's logged chars_before; the todo-reminder rule reproduces
every logged reminder; the transcript counter (events.jsonl) reproduces every marker path.

Outputs: data/tables/E0b_lookahead.md, data/e0b/transitions.jsonl (one record per transition), data/e0b/summary.json.

    /data/fritz/venv/bin/python research/17_harness_aware_serving/hs_lookahead.py [--workers 24] [--limit N]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import numpy as np  # noqa: E402

import hs_common as hc  # noqa: E402

OUT = hc.DATA / "e0b"

# -- s15 constants ------------------------------------------------------------------------------------------
KEEP_RECENT_TOOL_RESULTS = 3          # code.py:103
PERSIST_THRESHOLD = 30000             # code.py:104
CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."   # code.py:105
MAX_RECOVERY_RETRIES = 2              # code.py:97
SNIP_MAX_MESSAGES = 50                # snip_compact default, code.py:2450
BUDGET_MAX_BYTES = 200_000            # tool_result_budget default, code.py:2415
REMINDER = "<reminder>Update your todos.</reminder>"   # code.py:3611-3614
MARKER_RE = re.compile(r"\[\d+ messages archived at (.+)\]")   # is_archive_marker, code.py:2439-2447
# the benchmark profile's tool pool; any other tool is denied by its PreToolUse hook (s15_child.py:47-48, 420-425)
KEEP_TOOLS = frozenset(["bash", "read_file", "write_file", "edit_file", "glob", "todo_write", "task", "load_skill",
                        "compact", "create_task", "update_task", "list_tasks", "get_task", "claim_task",
                        "complete_task"])
WEB_TOOLS = frozenset(["web_search", "fetch_url"])
# anthropic SDK block classes: s15 keeps response.content (SDK objects) in its history, and estimate_size's
# json.dumps(default=str) serializes each one as its repr (checked against anthropic 1.12.1 on 4,778 blocks)
SDK_CLASS = {"text": "TextBlock", "tool_use": "ToolUseBlock", "thinking": "ThinkingBlock",
             "redacted_thinking": "RedactedThinkingBlock"}
POINTS = ("i", "ii", "iii")
POINT_NAMES = {"i": "round start", "ii": "decode end", "iii": "tool end"}
CLASSES = ("append", "snip", "placeholder", "fit", "summary", "reactive", "other")


# -- the harness environment the rules read ------------------------------------------------------------------

class Env:
    """What s15's edit rules read besides the messages, kept in memory (nothing is written anywhere).

    TRANSCRIPT_DIR / TOOL_RESULTS_DIR are WORKDIR/.transcripts and WORKDIR/.task_outputs/tool-results
    (code.py:91-92), WORKDIR = the trial's staging dir (s15_child.py:540). Transcript names come from the
    adapter's counter (s15_child.py:475-483). `saved` holds the tool outputs save_output would have written;
    with disk=True a path missing there is looked up read-only in the trial's own staging dir."""

    def __init__(self, staging: str, context_limit: int, disk: bool = False):
        self.staging = str(staging).rstrip("/")
        self.transcript_dir = self.staging + "/.transcripts"
        self.tool_results_dir = self.staging + "/.task_outputs/tool-results"
        self.context_limit = int(context_limit)
        self.counter = 0
        self.saved: dict[str, str] = {}
        self.disk = disk
        self.applied: list[str] = []

    def fork(self) -> "Env":
        e = copy.copy(self)
        e.saved = dict(self.saved)
        e.applied = []
        return e

    def write_transcript(self) -> str:
        path = f"{self.transcript_dir}/transcript_{self.counter:05d}.jsonl"
        self.counter += 1
        return path

    def transcript_exists(self, path: str) -> bool:
        m = re.fullmatch(re.escape(self.transcript_dir) + r"/transcript_(\d+)\.jsonl", norm_path(path))
        return bool(m) and int(m.group(1)) < self.counter

    def saved_text(self, path: str) -> str | None:
        if path in self.saved:
            return self.saved[path]
        if self.disk and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace", newline="") as fh:
                self.saved[path] = fh.read()
            return self.saved[path]
        return None


def norm_path(path: str) -> str:
    return str(PurePosixPath(path))


# -- s15's helpers (code.py:2309-2381) ----------------------------------------------------------------------

def block_type(block):
    return block.get("type") if isinstance(block, dict) else None


def message_has_tool_use(message: dict) -> bool:                       # code.py:2316-2322
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    return isinstance(content, list) and any(block_type(b) == "tool_use" for b in content)


def is_tool_result_message(message: dict) -> bool:                    # code.py:2325-2332
    if message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                             for b in content)


def collect_tool_results(messages: list) -> list[tuple[int, int]]:     # code.py:2335-2344
    found = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if msg.get("role") != "user" or not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                found.append((mi, bi))
    return found


def unseen_tool_result_positions(messages: list) -> set[tuple[int, int]]:   # code.py:2347-2361
    last_assistant = next((i for i in range(len(messages) - 1, -1, -1)
                           if messages[i].get("role") == "assistant"), -1)
    return {(mi, bi) for mi in range(last_assistant + 1, len(messages))
            if messages[mi].get("role") == "user" and isinstance(messages[mi].get("content"), list)
            for bi, block in enumerate(messages[mi]["content"])
            if isinstance(block, dict) and block.get("type") == "tool_result"}


def sdk_str(block: dict) -> str:
    """str() of the SDK content block the logged dict came from (anthropic BaseModel.__str__ is its repr)."""
    return SDK_CLASS.get(block.get("type"), "ContentBlock") + "(" + ", ".join(
        f"{k}={v!r}" for k, v in block.items()) + ")"


_SIZE: dict[int, tuple[dict, int]] = {}


def _message_chars(m: dict) -> int:
    hit = _SIZE.get(id(m))
    if hit is not None and hit[0] is m:
        return hit[1]
    if m.get("role") == "assistant" and isinstance(m.get("content"), list):
        live = {**m, "content": [sdk_str(b) if isinstance(b, dict) else b for b in m["content"]]}
    else:
        live = m
    n = len(json.dumps(live, default=str))
    _SIZE[id(m)] = (m, n)
    return n


def estimate_size(messages: list) -> int:
    """code.py:2309-2310, len(json.dumps(messages, default=str)) on the live history, where assistant contents are
    SDK objects. Messages are never mutated here (the rules copy on write), so per-message sizes are cached."""
    if not messages:
        return 2
    return 2 + sum(_message_chars(m) for m in messages) + 2 * (len(messages) - 1)


def clear_caches() -> None:
    _SIZE.clear()


def persisted_output_path(env: Env, output: str) -> str | None:        # code.py:2364-2381
    candidate = None
    if output.startswith("<persisted-output>\n"):
        candidate = next((line.removeprefix("Full output: ") for line in output.splitlines()
                          if line.startswith("Full output: ")), None)
    prefix = "[Earlier tool result saved at "
    if output.startswith(prefix) and output.endswith("]"):
        candidate = output.removeprefix(prefix).removesuffix("]")
    if not candidate:
        return None
    path = norm_path(candidate)
    if not path.startswith(env.tool_results_dir + "/") or env.saved_text(path) is None:
        return None
    return path


def save_output(env: Env, tool_use_id, output: str) -> str:            # code.py:2384-2389
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(tool_use_id))[:120] or "unknown"
    path = f"{env.tool_results_dir}/{safe_id}.txt"
    env.saved[path] = output
    return path


def _universal_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def persisted_preview(env: Env, tool_use_id, output: str, preview_chars: int = 2000) -> str:   # code.py:2392-2406
    saved_path = persisted_output_path(env, output)
    if saved_path:
        path = saved_path
        # s15 reads the saved file in text mode (universal newlines), code.py:2397-2398
        preview = _universal_newlines(env.saved_text(saved_path) or "")[:preview_chars]
    else:
        path = save_output(env, tool_use_id, output)
        preview = output[:preview_chars]
    return f"<persisted-output>\nFull output: {path}\nPreview:\n{preview}\n</persisted-output>"


def persist_large_output(env: Env, tool_use_id, output: str) -> str:   # code.py:2409-2412
    if len(output) <= PERSIST_THRESHOLD:
        return output
    return persisted_preview(env, tool_use_id, output)


def _with_block(messages: list, mi: int, bi: int, content) -> None:
    """messages[mi].content[bi].content = content, copying the message and the block (copy on write)."""
    m = messages[mi]
    blocks = list(m["content"])
    blk = dict(blocks[bi])
    blk["content"] = content
    blocks[bi] = blk
    messages[mi] = {**m, "content": blocks}


# -- s15's edit rules (code.py:2415-2566), non-mutating ------------------------------------------------------

def tool_result_budget(env: Env, messages: list, max_bytes: int = BUDGET_MAX_BYTES) -> list:   # code.py:2415-2436
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if last.get("role") != "user" or not isinstance(content, list):
        return messages
    blocks = [i for i, b in enumerate(content) if isinstance(b, dict) and b.get("type") == "tool_result"]
    cur = list(content)
    size = lambda: sum(len(str(cur[i].get("content", ""))) for i in blocks)  # noqa: E731
    total = size()
    if total <= max_bytes:
        return messages
    changed = False
    for i in sorted(blocks, key=lambda i: len(str(content[i].get("content", ""))), reverse=True):
        if total <= max_bytes:
            break
        text = str(cur[i].get("content", ""))
        new = persist_large_output(env, cur[i].get("tool_use_id", "unknown"), text)
        if new != text:
            cur[i] = {**cur[i], "content": new}
            changed = True
        total = size()
    if not changed:
        return messages
    env.applied.append("budget")
    return messages[:-1] + [{**last, "content": cur}]


def is_archive_marker(env: Env, message: dict) -> bool:                # code.py:2439-2447
    content = message.get("content")
    match = MARKER_RE.fullmatch(content) if isinstance(content, str) else None
    return bool(match) and env.transcript_exists(match.group(1))


def snip_compact(env: Env, messages: list, max_messages: int = SNIP_MAX_MESSAGES,
                 keep: int | None = None) -> list:
    """code.py:2450-2472. keep=None is s15's rolling rule (cut back to max_messages on every call past it);
    keep=k is E0f's hysteresis rule (past max_messages, cut back to k: 0-2 + marker + the newest k-4)."""
    if len(messages) <= max_messages:
        return messages
    keep = max_messages if keep is None else keep
    head_end = 3
    tail_start = len(messages) - (keep - head_end - 1)
    if head_end > 0 and message_has_tool_use(messages[head_end - 1]):
        while head_end < len(messages) and is_tool_result_message(messages[head_end]):
            head_end += 1
    if (0 < tail_start < len(messages) and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    if head_end >= tail_start:
        return messages
    middle = messages[head_end:tail_start]
    if len(middle) == 1 and is_archive_marker(env, middle[0]):
        return messages
    snipped = tail_start - head_end
    transcript = env.write_transcript()
    env.applied.append("snip")
    return (messages[:head_end] + [{"role": "user", "content": f"[{snipped} messages archived at {transcript}]"}]
            + messages[tail_start:])


def micro_compact(env: Env, messages: list, target_chars: int | None = None) -> list:   # code.py:2475-2490
    unseen = unseen_tool_result_positions(messages)
    consumed = [p for p in collect_tool_results(messages) if p not in unseen]
    out, changed = messages, False
    for mi, bi in consumed[:-KEEP_RECENT_TOOL_RESULTS]:
        if target_chars is not None and estimate_size(out) <= target_chars:
            break
        block = out[mi]["content"][bi]
        content = str(block.get("content", ""))
        if len(content) <= 120:
            continue
        saved_path = persisted_output_path(env, content)
        if not saved_path:
            saved_path = save_output(env, block.get("tool_use_id", "unknown"), content)
        new = f"[Earlier tool result saved at {saved_path}]"
        if new == block.get("content"):
            continue
        if not changed:
            out, changed = list(out), True
        _with_block(out, mi, bi, new)
    if changed:
        env.applied.append("placeholder")
    return out


def fit_tool_results(env: Env, messages: list, target_chars: int) -> list:   # code.py:2493-2506
    order = sorted(collect_tool_results(messages),
                   key=lambda p: len(str(messages[p[0]]["content"][p[1]].get("content", ""))), reverse=True)
    out, changed = messages, False
    for mi, bi in order:
        if estimate_size(out) <= target_chars:
            break
        block = out[mi]["content"][bi]
        output = str(block.get("content", ""))
        replacement = persisted_preview(env, block.get("tool_use_id", "unknown"), output, preview_chars=1000)
        if len(replacement) < len(output):
            if not changed:
                out, changed = list(out), True
            _with_block(out, mi, bi, replacement)
    if changed:
        env.applied.append("fit")
    return out


def unknown_message(what: str) -> dict:
    """Stands for content only an LLM call determines (a summary). Its bytes and size are unknown."""
    return {"role": "user", "content": f"[{what}: unknown until the summary call returns]", "_unknown": True}


def compact_history(env: Env, messages: list, summary_message: str | None) -> list:   # code.py:2536-2545
    """s15 returns [{"role": "user", "content": "[Compacted]..." + the summary}]; the replay takes that message's
    content from the next logged request (summary_message) or leaves it unknown (None)."""
    env.write_transcript()
    env.applied.append("summary")
    if summary_message is None:
        return [unknown_message("Compacted")]
    return [{"role": "user", "content": summary_message}]


def reactive_compact(env: Env, messages: list, summary_message: str | None) -> list:   # code.py:2548-2566
    env.write_transcript()
    env.applied.append("reactive")
    tail_start = max(0, len(messages) - 5)
    if (0 < tail_start < len(messages) and is_tool_result_message(messages[tail_start])
            and message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    head = unknown_message("Reactive compact") if summary_message is None else {"role": "user",
                                                                                 "content": summary_message}
    return [head, *messages[tail_start:]]


def prepare_context(env: Env, messages: list, snip_keep: int | None = None,
                    summary_message: str | None = None) -> list:       # code.py:3529-3555
    messages = tool_result_budget(env, messages)
    messages = snip_compact(env, messages, keep=snip_keep)
    limit = env.context_limit
    if estimate_size(messages) > limit:
        target = int(limit * 0.8)
        messages = micro_compact(env, messages, target)
        if estimate_size(messages) > limit:
            messages = fit_tool_results(env, messages, target)
    if estimate_size(messages) > limit:
        messages = compact_history(env, messages, summary_message)
    return messages


# -- agent_loop bookkeeping (code.py:3588-3779) ---------------------------------------------------------------

def tool_blocks(content) -> list[dict]:
    return [b for b in (content or []) if block_type(b) == "tool_use"]


def todo_after_tools(r: int, content, allowed=KEEP_TOOLS) -> int:
    """rounds_since_todo after the lead runs reply's tool calls: compact, denied and background calls skip the
    update (code.py:3705-3750); todo_write resets it, any other tool adds one (code.py:3760-3763)."""
    for b in tool_blocks(content):
        name = b.get("name")
        args = b.get("input") if isinstance(b.get("input"), dict) else {}
        if name == "compact" or name not in allowed:
            continue
        if name == "bash" and args.get("run_in_background") is True:   # should_run_background, code.py:2644-2648
            continue
        r = 0 if name == "todo_write" else r + 1
    return r


def results_fill(content) -> dict:
    """The tool-result message build_user_content would append (code.py:3558-3564, 3775), outputs not known yet."""
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.get("id"), "content": ""}
                                        for b in tool_blocks(content)]}


def reply_fill() -> dict:
    """Round start: reply j is not decoded yet; assumed to be one tool call."""
    return {"role": "assistant", "content": [{"id": "", "input": {}, "name": "", "type": "tool_use"}]}


def is_notice(m: dict) -> bool:
    """inject_background_notifications' message (code.py:3567-3571)."""
    c = m.get("content")
    return (m.get("role") == "user" and isinstance(c, list) and bool(c)
            and all(isinstance(b, dict) and b.get("type") == "text"
                    and str(b.get("text", "")).startswith("<task_notification>") for b in c))


def is_reminder(m: dict) -> bool:
    return m.get("role") == "user" and m.get("content") == REMINDER


def is_marker_message(m: dict) -> bool:
    return isinstance(m.get("content"), str) and bool(MARKER_RE.fullmatch(m["content"]))


def marker_index(m: dict) -> int | None:
    mm = re.search(r"/transcript_(\d+)\.jsonl\]$", m["content"]) if is_marker_message(m) else None
    return int(mm.group(1)) if mm else None


# -- rendering and tokens -----------------------------------------------------------------------------------

def render_block(b) -> str:
    if not isinstance(b, dict):
        return str(b)
    t = b.get("type")
    if t == "tool_result":
        c = b.get("content", "")
        if isinstance(c, list):
            c = "\n".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
        return "<tool_response>\n" + str(c) + "\n</tool_response>"
    if t == "text":
        return str(b.get("text", ""))
    return json.dumps(b, ensure_ascii=False)


def render_message(m: dict) -> str:
    """A simple Qwen3 render (vendor/s15/templates/qwen3_append_only.jinja): per-message framing, the
    append-only think block on every assistant turn (reasoning kept), tool calls as <tool_call> JSON."""
    role, c = m.get("role"), m.get("content")
    if role == "assistant":
        if isinstance(c, list):
            think = "\n".join(str(b.get("thinking", "")) for b in c if block_type(b) == "thinking")
            text = "\n".join(str(b.get("text", "")) for b in c if block_type(b) == "text").lstrip("\n")
            calls = [b for b in c if block_type(b) == "tool_use"]
        else:
            think, text, calls = "", str(c or ""), []
        out = "<|im_start|>assistant\n" + ("<think>\n" + think.strip("\n") + "\n</think>\n\n" if think.strip()
                                           else "<think>\n\n</think>\n\n") + text
        for k, b in enumerate(calls):
            if k or text:
                out += "\n"
            out += ('<tool_call>\n{"name": "' + str(b.get("name")) + '", "arguments": '
                    + json.dumps(b.get("input"), ensure_ascii=False) + "}\n</tool_call>")
        return out + "<|im_end|>\n"
    body = c if isinstance(c, str) else "\n".join(render_block(b) for b in (c or []))
    return f"<|im_start|>{role}\n{body}<|im_end|>\n"


def render_system(system) -> str:
    if isinstance(system, list):
        system = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in system)
    return f"<|im_start|>system\n{system or ''}<|im_end|>\n"


GEN_PROMPT = "<|im_start|>assistant\n"
# calls.jsonl logs tool names only; the schemas sit in the system turn ahead of every message and never move.
# Calibrated: served prompt tokens (usage) minus rendered tokens on all 46,932 qwen3-32b lead transitions of the
# profile is 1,143 at p10, median and p90 (same tool pool for every trial and model; Qwen3-32B tokens).
TOOL_SCHEMA_TOKENS = 1143
GEN_PROMPT_TOKENS = 3


def qwen_encoder():
    """The Qwen3-32B tokenizer from the shared HF cache (offline), single-threaded."""
    os.environ.setdefault("HF_HOME", "/data/shared/huggingface")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from transformers import AutoTokenizer
    backend = AutoTokenizer.from_pretrained("Qwen/Qwen3-32B").backend_tokenizer

    def encode(texts: list[str]) -> list[list[int]]:
        return [e.ids for e in backend.encode_batch(texts, add_special_tokens=False)]
    return encode


class Tok:
    """Token ids per rendered message, cached by content (sha1 of the render); keys cached per message object."""

    def __init__(self, encode):
        self.encode = encode
        self.ids_by_key: dict[str, np.ndarray] = {}
        self._key: dict[int, tuple[dict, str]] = {}
        self._pending: dict[str, str] = {}

    def clear(self) -> None:
        self.ids_by_key.clear()
        self._key.clear()
        self._pending.clear()

    def key(self, m: dict) -> str:
        hit = self._key.get(id(m))
        if hit is None or hit[0] is not m:
            text = render_message(m)
            k = hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()
            if k not in self.ids_by_key:
                self._pending[k] = text
            hit = (m, k)
            self._key[id(m)] = hit
        return hit[1]

    def ensure(self, msgs) -> None:
        for m in msgs:
            self.key(m)
        if self._pending:
            keys = list(self._pending)
            for k, ids in zip(keys, self.encode([self._pending[k] for k in keys])):
                self.ids_by_key[k] = np.asarray(ids, dtype=np.int32)
            self._pending.clear()

    def ids(self, m: dict) -> np.ndarray:
        k = self.key(m)
        if k not in self.ids_by_key:
            self.ensure([m])
        return self.ids_by_key[k]

    def n(self, m: dict) -> int:
        return int(self.ids(m).shape[0])

    def text_tokens(self, text: str) -> int:
        return len(self.encode([text])[0])

    def lcp(self, a: dict, b: dict) -> int:
        x, y = self.ids(a), self.ids(b)
        k = min(len(x), len(y))
        if k == 0:
            return 0
        neq = np.flatnonzero(x[:k] != y[:k])
        return int(neq[0]) if neq.size else k


# -- trials ---------------------------------------------------------------------------------------------------

@dataclass
class Trial:
    model: str
    bench: str
    path: Path
    staging: str
    context_limit: int
    allowed: frozenset
    calls: list[dict]                 # lead records with a request, in call order (budget refusals dropped)
    events: list[dict]

    @property
    def s15(self) -> Path:
        return self.path / "agent" / "s15"

    @property
    def name(self) -> str:
        return str(self.path.relative_to(hc.HE_JOBS)) if self.path.is_relative_to(hc.HE_JOBS) else str(self.path)


def load_trial(model: str, bench: str, path: Path) -> Trial:
    s15 = path / "agent" / "s15"
    cfg = json.loads((s15 / "config.json").read_text())
    calls = sorted((r for r in hc.he_calls(path) if r.get("purpose") == "lead" and "refused" not in r
                    and isinstance(r.get("request"), dict)), key=lambda r: r["call"])
    allowed = KEEP_TOOLS | (WEB_TOOLS if cfg.get("web") else frozenset())
    return Trial(model, bench, path, harness_staging(calls, str(cfg.get("staging") or s15)),
                 int(cfg.get("context_limit") or 512000), allowed, calls, list(hc.he_events(path)))


def harness_staging(calls: list[dict], default: str) -> str:
    """WORKDIR as s15 saw it (Path.cwd() after the adapter's chdir, s15_child.py:540): from the first archive
    marker or saved-output path in the logged requests, else the config's staging dir."""
    for rec in calls:
        for m in rec["request"].get("messages") or []:
            c = m.get("content")
            if isinstance(c, str):
                mm = MARKER_RE.fullmatch(c)
                if mm and "/.transcripts/" in mm.group(1):
                    return mm.group(1).rsplit("/.transcripts/", 1)[0]
            elif isinstance(c, list):
                for b in c:
                    t = b.get("content") if isinstance(b, dict) else None
                    if isinstance(t, str) and "/.task_outputs/tool-results/" in t[:2000] and (
                            t.startswith("[Earlier tool result saved at ") or t.startswith("<persisted-output>")):
                        line = t.split("\n", 2)[1] if t.startswith("<persisted") else t
                        frag = line.split("/.task_outputs/tool-results/", 1)[0]
                        return frag.removeprefix("Full output: ").removeprefix("[Earlier tool result saved at ")
    return default


def ok_call(rec: dict) -> bool:
    return "error" not in rec and isinstance(rec.get("response"), list)


def reply_message(rec: dict) -> dict:
    return {"role": "assistant", "content": rec["response"]}


def restore_new(new: list[dict]) -> list[dict]:
    """Undo previews of the newest tool results (fit_tool_results / tool_result_budget rewrote them in this very
    prepare_context): the originals are the files save_output wrote (read-only, no newline translation)."""
    out = []
    for m in new:
        c = m.get("content")
        if m.get("role") == "user" and isinstance(c, list):
            blocks = []
            for b in c:
                text = b.get("content") if isinstance(b, dict) and b.get("type") == "tool_result" else None
                if isinstance(text, str) and text.startswith("<persisted-output>\nFull output: "):
                    path = text.split("\n", 2)[1].removeprefix("Full output: ")
                    if os.path.isfile(path):
                        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
                            b = {**b, "content": fh.read()}
                blocks.append(b)
            m = {**m, "content": blocks}
        out.append(m)
    return out


def summary_text(m: dict) -> str | None:
    c = m.get("content")
    if isinstance(c, str) and (c.startswith("[Compacted]") or c.startswith("[Reactive compact]")):
        return c
    return None


@dataclass
class Round:
    """One transition between consecutive successful lead calls j -> n."""
    j: dict
    n: dict
    failed: list[dict]                # overflowed lead calls in between (reactive compaction)
    kind: str                         # tool | retry | continuation | summary | reactive | other
    reply: dict | None                # reply j as the assistant message the loop appends (None: retry drops it)
    new: list[dict] | None            # messages appended after reply j, originals restored (None: unknown)
    m_index: int = 0                  # n is the m-th successful lead call (events' lead_calls counter)
    flags: dict = field(default_factory=dict)


def trial_rounds(trial: Trial):
    """Rounds in order, with the agent loop's own bookkeeping: max_tokens is retried once with the same messages
    (has_escalated), later max_tokens replies get the continuation prompt (code.py:3640-3676)."""
    prev, failed, m, escalated = None, [], 0, False
    for rec in trial.calls:
        if not ok_call(rec):
            if prev is not None:
                failed.append(rec)
            continue
        if prev is None:
            prev, m = rec, 1
            continue
        Rj, Rn = prev["request"]["messages"], rec["request"]["messages"]
        reply = reply_message(prev)
        stop = prev.get("stop_reason")
        first = Rn[0] if Rn else {}
        fresh = summary_text(first) is not None and (not Rj or first != Rj[0])   # a new summary message
        new = None
        if failed:   # overflowed call(s), each followed by reactive_compact (the fallback summary text can repeat)
            kind = "reactive" if summary_text(first) and first["content"].startswith("[Reactive") else "other"
        elif fresh and first["content"].startswith("[Compacted]"):
            kind = "summary"
            new = Rn[1:]
        elif stop == "max_tokens" and not escalated:
            kind = "retry"
            new = Rn[len(Rj):] if Rn[:len(Rj)] == Rj else None
            reply = None
        else:
            kind = ("continuation" if stop == "max_tokens" else
                    "tool" if tool_blocks(prev["response"]) else "other")
            pos = next((i for i in range(len(Rn) - 1, -1, -1) if Rn[i] == reply), None)
            new = restore_new(Rn[pos + 1:]) if pos is not None else None
        yield Round(prev, rec, failed, kind, reply, new, m)
        if stop == "max_tokens":
            escalated = True
        else:
            escalated = False
        prev, failed, m = rec, [], m + 1


# -- layouts --------------------------------------------------------------------------------------------------

def old_region_end(messages: list, reply_ids: set[int], reply_key: str | None, tok: Tok) -> int:
    """Index after reply j (the last message that is reply j's object, or renders like it), else len."""
    for i in range(len(messages) - 1, -1, -1):
        if id(messages[i]) in reply_ids or (reply_key is not None and messages[i].get("role") == "assistant"
                                            and tok.key(messages[i]) == reply_key):
            return i + 1
    return len(messages)


def layout(messages: list, end: int, tok: Tok, reply_index: int | None) -> list[tuple]:
    """[(token offset or None, identity, n tokens)] for messages[:end]. The message at reply_index is reply j
    (identity "REPLY"; at round start a fill of unknown size); offsets after content of unknown size are None."""
    out, off = [], 0
    for i, m in enumerate(messages[:end]):
        if m.get("_unknown"):
            out.append((off, None, 0))
            off = None
            continue
        n = 0 if m.get("_fill") else tok.n(m)
        out.append((off, "REPLY" if i == reply_index else tok.key(m), n))
        if off is not None:
            off = None if m.get("_fill") else off + n
    return out


def divergence(actual: list, cached: list, tok: Tok) -> tuple[int, int, int]:
    """(index of the first differing message, its cached-prefix tokens, total cached prefix tokens d)."""
    d = 0
    for i, m in enumerate(actual):
        if i >= len(cached) or tok.key(m) != tok.key(cached[i]):
            lcp = tok.lcp(m, cached[i]) if i < len(cached) else 0
            return i, lcp, d + lcp
        d += tok.n(m)
    return len(actual), 0, d


def classify(actual_msg: dict, cached_msg: dict | None) -> str:
    c = actual_msg.get("content")
    if isinstance(c, str):
        if MARKER_RE.fullmatch(c):
            return "snip"
        if c.startswith("[Compacted]"):
            return "summary"
        if c.startswith("[Reactive compact]"):
            return "reactive"
    if cached_msg is not None and is_tool_result_message(actual_msg) and isinstance(cached_msg.get("content"), list):
        for a, b in zip(c, cached_msg["content"]):
            if a != b and isinstance(a, dict):
                text = str(a.get("content", ""))
                if text.startswith("[Earlier tool result saved at "):
                    return "placeholder"
                if text.startswith("<persisted-output>"):
                    return "fit"
    return "other"


# -- one trial --------------------------------------------------------------------------------------------------

def transcript_writes(events: list[dict]) -> list[int]:
    """lead_calls value of every adapter event whose rule wrote a transcript (snip / summary / reactive)."""
    return sorted(e.get("lead_calls", 0) for e in events
                  if e.get("kind") in ("snip_compact", "compact_history", "reactive_compact"))


def counter_before(writes: list[int], m: int) -> int:
    import bisect
    return bisect.bisect_left(writes, m)


def predict(env: Env, Rj: list, reply: dict | None, kind: str, r_start: int, point: str, rnd: Round,
            allowed, summary_message: str | None = None):
    """(pre-edit state, output, env, info) of the harness rules for request n as seen at a decision point."""
    e = env.fork()
    info = {"reminder": False, "notice": False, "n_new": 0}
    if point == "i":
        fill = reply_fill()
        fill["_fill"] = True
        new = [results_fill(fill["content"])]
        if r_start + 1 >= 3:
            new.append({"role": "user", "content": REMINDER})
            info["reminder"] = True
        pre = Rj + [fill] + new
        info.update(n_new=len(new), reply_obj=fill)
    elif point == "ii":
        if kind == "retry":
            pre, new = list(Rj), []
        elif kind == "continuation":
            new = [{"role": "user", "content": CONTINUATION_PROMPT}]
            pre = Rj + [reply] + new
        else:
            content = reply["content"]
            r_after = todo_after_tools(r_start, content, allowed)
            new = [results_fill(content)]
            if any(b.get("name") == "compact" for b in tool_blocks(content)):
                e.write_transcript()
                e.applied.append("summary")
                pre = [unknown_message("Compacted")]
                tail = [{"role": "user", "content": REMINDER}] if r_after >= 3 else []
                info.update(reminder=bool(tail), n_new=len(new) + len(tail), reply_obj=reply)
                out = prepare_context(e, pre + tail)
                return pre + tail, out, e, info
            if r_after >= 3:
                new.append({"role": "user", "content": REMINDER})
                info["reminder"] = True
            pre = Rj + [reply] + new
        info.update(n_new=len(new), reply_obj=reply)
    else:  # iii: the real appended messages; summaries unknown unless summary_message is given
        new = rnd.new or []
        info.update(reminder=any(is_reminder(m) for m in new), notice=any(is_notice(m) for m in new),
                    n_new=len(new), reply_obj=reply)
        if kind == "retry":
            pre = list(Rj) + new
            info["new_tail"] = new
        elif kind == "summary":
            # compact tool (agent_loop, code.py:3776-3782) or compact_history in prepare_context: either way the
            # history becomes the summary message, then this iteration's appends (new = request n after it)
            e.write_transcript()
            e.applied.append("summary")
            head = ({"role": "user", "content": summary_message} if summary_message is not None
                    else unknown_message("Compacted"))
            out = prepare_context(e, [head] + new)
            return [head] + new, out, e, info
        else:
            pre = Rj + ([reply] if reply is not None else []) + new
    out = prepare_context(e, pre, summary_message=summary_message)
    return pre, out, e, info


def miss_reason(point: str, cls: str, pinfo: dict, ainfo: dict, penv: Env, aenv_applied: list,
                pred_old: list, act_old: list, tok: Tok) -> str:
    if cls in ("summary", "reactive"):
        return "LLM summary bytes"
    if pinfo.get("reminder") != ainfo.get("reminder"):
        return "todo reminder"
    if ainfo.get("notice") and not pinfo.get("notice"):
        return "background notice"
    if pinfo.get("n_new") != ainfo.get("n_new"):
        return "reply shape"
    sized = lambda xs: [x for x in xs if x in ("placeholder", "fit", "summary")]  # noqa: E731
    if sized(penv.applied) != sized(aenv_applied):
        return "tool-result sizes" if point != "i" else "reply / tool-result sizes"
    for p, a in zip(pred_old, act_old):
        if p is a or tok.key(p) == tok.key(a):
            continue
        if is_marker_message(a):
            return "marker bytes"
        if is_tool_result_message(a):
            return "tool-result sizes" if point != "i" else "reply / tool-result sizes"
        return "other"
    return "other"


def e0b_trial(trial: Trial, tok: Tok) -> tuple[list[dict], dict]:
    clear_caches()
    tok.clear()
    writes = transcript_writes(trial.events)
    env = Env(trial.staging, trial.context_limit, disk=True)
    diag = Counter()
    recs = []
    r = 0                                    # rounds_since_todo at round start (module global, code.py:3525)
    system0 = trial.calls[0]["request"].get("system") if trial.calls else None
    sys_tokens = tok.text_tokens(render_system(system0)) if trial.calls else 0
    first_ok = next((c for c in trial.calls if ok_call(c)), None)
    if first_ok is not None:
        tok.ensure(first_ok["request"]["messages"])
    for rnd in trial_rounds(trial):
        Rj, Rn = rnd.j["request"]["messages"], rnd.n["request"]["messages"]
        if rnd.n["request"].get("system") != system0:
            diag["system_changed"] += 1
        tok.ensure(Rn)
        env.counter = counter_before(writes, rnd.m_index)
        reply = rnd.reply
        reply_key = tok.key(reply) if reply is not None else None
        cached = Rj + ([reply] if reply is not None else [])
        tok.ensure(cached)
        # actual layout of request n
        if rnd.kind == "retry":
            a_end = min(len(Rn), len(Rj))
        elif rnd.kind == "summary":
            a_end = len(Rn) - len(rnd.new or [])
        else:
            a_end = old_region_end(Rn, set(), reply_key, tok)
        j_div, lcp, d = divergence(Rn, cached, tok)
        a_reply = a_end - 1 if reply is not None and rnd.kind not in ("retry", "summary") and a_end and (
            tok.key(Rn[a_end - 1]) == reply_key) else None
        a_lay = layout(Rn, a_end, tok, a_reply)
        P = TOOL_SCHEMA_TOKENS + sys_tokens + sum(tok.n(m) for m in Rn) + GEN_PROMPT_TOKENS
        E = 0
        edit_idx = []
        if j_div < a_end:
            for i in range(j_div, a_end):
                t = a_lay[i][2] - (lcp if i == j_div else 0)
                E += t
                edit_idx.append((i, t))
        cls = "append" if E == 0 else classify(Rn[j_div], cached[j_div] if j_div < len(cached) else None)
        if rnd.kind in ("summary", "reactive") and E > 0:
            cls = rnd.kind
        # the todo counter (validated against the logged reminders)
        # reply j's tools ran unless max_tokens cut it (retry / continuation); before an overflowed call too
        if reply is not None and rnd.j.get("stop_reason") != "max_tokens":
            r_after = todo_after_tools(r, reply["content"], trial.allowed)
        else:
            r_after = r
        act_rem = any(is_reminder(m) for m in (rnd.new or []))
        if rnd.new is not None and rnd.kind in ("tool", "continuation", "retry"):
            diag["reminder_ok" if act_rem == (r_after >= 3) else "reminder_bad"] += 1
        # (iii) with the real summaries = fidelity check; without = decision point (iii)
        summ = summary_text(Rn[0]) if Rn and rnd.kind in ("summary", "reactive") else None
        fid = None
        known = {p: 0 for p in POINTS}
        known_reply_i = 0
        reasons = {p: None for p in POINTS}
        applied = []
        if rnd.kind in ("reactive", "other") or rnd.new is None:
            # an overflow (vLLM's 400) and a summary call sit between the calls: nothing about request n's
            # layout is known before the summary returns; the replay re-syncs on request n
            for p in POINTS:
                reasons[p] = "LLM summary bytes" if rnd.kind == "reactive" else "unreconstructed"
            applied = [rnd.kind]
            env.applied = []
            diag[f"skip_{rnd.kind}"] += 1
        else:
            fenv = env.fork()
            _, fout, fenv, finfo = predict(fenv, Rj, reply, rnd.kind, r, "iii", rnd, trial.allowed,
                                           summary_message=summ)
            fid = (fout == Rn)
            diag["fidelity_ok" if fid else "fidelity_bad"] += 1
            applied = list(fenv.applied)
            for p in POINTS:
                _, pout, penv, pinfo = predict(env, Rj, reply, rnd.kind, r, p, rnd, trial.allowed)
                rid = {id(pinfo["reply_obj"])} if pinfo.get("reply_obj") is not None else set()
                p_end = old_region_end(pout, rid, None, tok) if rid else (
                    len(pout) - len(pinfo.get("new_tail", [])))
                tok.ensure([m for m in pout[:p_end] if not m.get("_unknown") and not m.get("_fill")])
                p_reply = p_end - 1 if rid and p_end and id(pout[p_end - 1]) in rid else None
                p_lay = layout(pout, p_end, tok, p_reply)
                pset = {(o, ident) for o, ident, _ in p_lay if o is not None and ident is not None}
                hits = [(i, t) for i, t in edit_idx if a_lay[i][0] is not None and (a_lay[i][0], a_lay[i][1]) in pset]
                kn = sum(t for _, t in hits)
                known[p] = kn
                if p == "i":   # reply j's own tokens: placed right at round start, but not decoded yet
                    known_reply_i = sum(t for i, t in hits if a_lay[i][1] == "REPLY")
                if E and kn < E:
                    reasons[p] = miss_reason(p, cls, pinfo, finfo, penv, applied, pout[:p_end], Rn[:a_end], tok)
            if fid:
                env.saved = fenv.saved
        # markers and the transcript counter
        for m_ in Rn[:6]:
            k_ = marker_index(m_)
            if k_ is not None:
                diag["marker_counter_ok" if k_ < counter_before(writes, rnd.m_index + 1) else "marker_counter_bad"] += 1
        u = rnd.n.get("usage") or {}
        recs.append({
            "model": trial.model, "bench": trial.bench, "trial": trial.name, "j": rnd.j["call"], "n": rnd.n["call"],
            "kind": rnd.kind, "cls": cls, "applied": applied, "E": E, "d": TOOL_SCHEMA_TOKENS + sys_tokens + d, "P": P, "nm": len(Rn),
            "known": [known[p] for p in POINTS], "known_reply_i": known_reply_i,
            "reason": [reasons[p] for p in POINTS], "fid": fid,
            "rem": act_rem, "notice": any(is_notice(m) for m in (rnd.new or [])),
            "u_read": u.get("cache_read_input_tokens"),
            "u_P": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                   + (u.get("cache_creation_input_tokens") or 0),
            "out": u.get("output_tokens"),
        })
        # the next iteration's reminder check (code.py:3611-3614)
        r = 0 if r_after >= 3 else r_after
    diag["transitions"] = len(recs)
    return recs, dict(diag)


# -- size check against the adapter's events ------------------------------------------------------------------

def size_check(trial: Trial) -> Counter:
    """estimate_size of the reconstructed pre-edit state vs each round's first logged chars_before."""
    c = Counter()
    by_m = defaultdict(list)
    for e in trial.events:
        by_m[e.get("lead_calls")].append(e)
    for rnd in trial_rounds(trial):
        if rnd.kind != "tool" or rnd.new is None:
            continue
        evs = by_m.get(rnd.m_index)
        if not evs:
            continue
        pre = rnd.j["request"]["messages"] + [rnd.reply] + rnd.new
        e = evs[0]
        c["ok" if (e.get("msgs_before") == len(pre) and e.get("chars_before") == estimate_size(pre)) else "bad"] += 1
    return c


# -- workers ---------------------------------------------------------------------------------------------------

_TOK: Tok | None = None


def _init_worker() -> None:
    global _TOK
    _TOK = Tok(qwen_encoder())


def _run_trial(args) -> tuple[list[dict], dict, dict]:
    model, bench, path = args
    t0 = time.time()
    try:
        trial = load_trial(model, bench, Path(path))
        recs, diag = e0b_trial(trial, _TOK)
        sc = size_check(trial)
        diag.update({f"size_{k}": v for k, v in sc.items()})
    except Exception as exc:                                     # noqa: BLE001
        import traceback
        return [], {"error": 1}, {"trial": str(path), "error": f"{type(exc).__name__}: {exc}",
                                  "tb": traceback.format_exc()[-3000:]}
    finally:
        clear_caches()
    return recs, diag, {"trial": str(path), "s": round(time.time() - t0, 1)}


# -- aggregation and table ---------------------------------------------------------------------------------------

def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f} %" if b else "—"


def aggregate(recs: list[dict]) -> dict:
    agg = {}
    for key_fn, name in ((lambda r: (r["model"], r["cls"]), "model_cls"), (lambda r: (r["model"], "*"), "model"),
                         (lambda r: ("*", r["cls"]), "cls"), (lambda r: ("*", "*"), "all"),
                         (lambda r: (r["model"], r["bench"]), "model_bench")):
        g = defaultdict(lambda: {"n": 0, "edits": 0, "E": 0, "known": [0, 0, 0], "full": [0, 0, 0], "P": 0,
                                 "reason_tok": [Counter(), Counter(), Counter()],
                                 "reason_n": [Counter(), Counter(), Counter()]})
        for r in recs:
            a = g[key_fn(r)]
            a["n"] += 1
            a["P"] += r["P"]
            if r["E"] <= 0:
                continue
            a["edits"] += 1
            a["E"] += r["E"]
            a["known_reply_i"] = a.get("known_reply_i", 0) + r.get("known_reply_i", 0)
            for k in range(3):
                a["known"][k] += r["known"][k]
                a["full"][k] += int(r["known"][k] >= r["E"])
                if r["reason"][k]:
                    a["reason_tok"][k][r["reason"][k]] += r["E"] - r["known"][k]
                    a["reason_n"][k][r["reason"][k]] += 1
        agg[name] = {f"{k[0]}|{k[1]}": {**v, "reason_tok": [dict(c) for c in v["reason_tok"]],
                                        "reason_n": [dict(c) for c in v["reason_n"]]} for k, v in g.items()}
    return agg


def grp(agg: dict, model: str, cls: str = "*") -> dict | None:
    if model == "*":
        return agg["all"].get("*|*") if cls == "*" else agg["cls"].get(f"*|{cls}")
    return agg["model"].get(f"{model}|*") if cls == "*" else agg["model_cls"].get(f"{model}|{cls}")


REASONS = ["todo reminder", "background notice", "reply shape", "reply / tool-result sizes", "tool-result sizes",
           "marker bytes", "LLM summary bytes", "unreconstructed", "other"]


def table_lines(recs: list[dict], diag: dict, agg: dict) -> list[str]:
    models = [m for m in hc.HE_MODELS if grp(agg, m)]
    rows_m = models + ["*"]
    label = lambda m: m if m != "*" else "**all**"  # noqa: E731
    L = []
    # 1. transitions by class
    L += ["## 1. Lead-call transitions by kind", "",
          "Share of transitions (and of all edit tokens) per kind. A transition is consecutive successful lead calls "
          "j -> n; edit tokens are the tokens of request n after its first divergence from request j + reply j that "
          "are not newly appended content (Qwen3-32B tokens on the simple render; prompt tokens include the tool "
          f"schemas, a calibrated {TOOL_SCHEMA_TOKENS:,}).", "",
          "| model | transitions | append-only | snip | placeholder | fit | summary | reactive | other | "
          "edit tokens per edit | edit tokens / prompt tokens |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in rows_m:
        tot = grp(agg, m)
        cells = []
        for c in CLASSES:
            a = grp(agg, m, c)
            n = a["n"] if a else 0
            tok_share = f" ({pct(a['E'], tot['E'])} tok)" if a and c != "append" and tot["E"] else ""
            cells.append(pct(n, tot["n"]) + tok_share)
        L.append(f"| {label(m)} | {tot['n']:,} | " + " | ".join(cells)
                 + f" | {tot['E'] / max(tot['edits'], 1):,.0f} | {pct(tot['E'], tot['P'])} |")
    trimmed = sum(1 for r in recs if r["E"] == 0 and set(r["applied"]) & {"fit", "budget"})
    L += ["", f"Append-only includes {trimmed:,} transitions where fit_tool_results or tool_result_budget rewrote only "
          "the newest tool results (new content, nothing re-prefilled) and "
          f"{sum(1 for r in recs if r['kind'] == 'retry'):,} max_tokens retries of the same request.", ""]
    # 2. known shares
    L += ["## 2. Edit layout known at round start (i), decode end (ii), tool end (iii)", "",
          "Edits = transitions with edit tokens > 0. *Fully known*: every edit token's message is predicted at the "
          "right token offset. *Tokens known*: token-weighted.", "",
          "| model | kind | edits | edit tokens | edits fully known (i / ii / iii) | edit tokens known (i / ii / iii) |",
          "|---|---|---|---|---|---|"]
    for m in rows_m:
        rows = [(c, grp(agg, m, c)) for c in CLASSES[1:]] + [("**all edits**", grp(agg, m))]
        for c, a in rows:
            if not a or not a["edits"]:
                continue
            L.append(f"| {label(m)} | {c} | {a['edits']:,} | {a['E']:,} | "
                     + " / ".join(pct(a["full"][k], a["edits"]) for k in range(3)) + " | "
                     + " / ".join(pct(a["known"][k], a["E"]) for k in range(3)) + " |")
    # 3. reasons
    L += ["", "## 3. Why edit tokens are not known", "",
          "Share of all edit tokens of the model whose layout the decision point misses, by the first reason that "
          "applies (edits in parentheses). Order of attribution: LLM summary, todo reminder, background notice, "
          "reply shape (another number of appended messages), size-triggered rules (micro_compact / fit / "
          "compact_history decided differently), marker bytes, other.", "",
          "| model | point | " + " | ".join(REASONS) + " |", "|---|---|" + "---|" * len(REASONS)]
    for m in rows_m:
        a = grp(agg, m)
        for k in range(3):
            if not any(a["reason_n"][k].values()):
                continue
            L.append(f"| {label(m)} | ({POINTS[k]}) {POINT_NAMES[POINTS[k]]} | " + " | ".join(
                (f"{pct(a['reason_tok'][k].get(x, 0), a['E'])} ({a['reason_n'][k].get(x, 0):,})"
                 if a["reason_n"][k].get(x) else "—") for x in REASONS) + " |")
    # 4. D4
    a = grp(agg, "*")
    share = a["known"][1] / a["E"] if a["E"] else 0.0
    pm = {m: grp(agg, m)["known"][1] / grp(agg, m)["E"] for m in models if grp(agg, m)["E"]}
    verdict = "PASSES" if share >= 0.5 else "FAILS"
    same = all((v >= 0.5) == (share >= 0.5) for v in pm.values())
    L += ["", "## 4. Rule D4 (lookahead)", "",
          f"Edit tokens whose surviving layout is fixed at the previous round's decode end: **{100 * share:.1f} %** "
          f"of {a['E']:,} (all models pooled, token-weighted); per model "
          + ", ".join(f"{m} {100 * v:.1f} %" for m, v in pm.items())
          + f"; at round start {100 * a['known'][0] / a['E']:.1f} % ({100 * (a['known'][0] - a.get('known_reply_i', 0)) / a['E']:.1f} % "
          f"without reply j's own tokens, which are placed right but not decoded yet), at tool end "
          f"{100 * a['known'][2] / a['E']:.1f} %. "
          f"Threshold 50 %: **D4 {verdict}**" + (" for every model." if same else " pooled, but not for every model."),
          ""]
    # 5. per bench
    L += ["## 5. Per benchmark", "",
          "| model | bench | transitions | edits | edit tokens per transition | edit tokens known (i / ii / iii) |",
          "|---|---|---|---|---|---|"]
    order = {m: i for i, m in enumerate(hc.HE_MODELS)}
    for k, v in sorted(agg["model_bench"].items(), key=lambda kv: (order.get(kv[0].split("|")[0], 9), kv[0])):
        mm, b = k.split("|")
        L.append(f"| {mm} | {b} | {v['n']:,} | {v['edits']:,} ({pct(v['edits'], v['n'])}) | "
                 f"{v['E'] / max(v['n'], 1):,.0f} | " + " / ".join(pct(v["known"][i], v["E"]) for i in range(3)) + " |")
    # 6. checks
    L += ["", "## 6. Checks", ""]
    L.append(f"- Trials: {diag.get('trials', 0):,} (failed to process: {diag.get('trial_errors', 0)}); transitions: "
             f"{diag.get('transitions', 0):,}.")
    fo, fb = diag.get("fidelity_ok", 0), diag.get("fidelity_bad", 0)
    skipped = sum(v for k, v in diag.items() if k.startswith("skip_"))
    L.append(f"- Fidelity: the reimplemented rules, re-run on the tool-end state with the logged summaries, reproduce "
             f"request n byte for byte in {fo:,} of {fo + fb:,} transitions ({pct(fo, fo + fb)}). {skipped:,} "
             f"transitions are taken from the log as they are ("
             + ", ".join(f"{k.removeprefix('skip_')}: {v}" for k, v in sorted(diag.items()) if k.startswith("skip_"))
             + "; reactive = an overflowed call and a reactive summary in between).")
    so, sb = diag.get("size_ok", 0), diag.get("size_bad", 0)
    L.append(f"- estimate_size (SDK objects emulated) equals the adapter's logged chars_before in {so:,} of {so + sb:,} "
             f"rounds with an edit event ({pct(so, so + sb)}).")
    ro, rb = diag.get("reminder_ok", 0), diag.get("reminder_bad", 0)
    L.append(f"- The todo-reminder rule (code.py:3611-3614, 3760-3763) predicts the logged reminder in {ro:,} of "
             f"{ro + rb:,} rounds ({pct(ro, ro + rb)}).")
    L.append(f"- System prompt changed within a trial: {diag.get('system_changed', 0)} times.")
    q = [r for r in recs if r["model"] == "qwen3-32b" and r.get("u_P")]
    if q:
        off = sorted(r["u_P"] - (r["P"] - TOOL_SCHEMA_TOKENS) for r in q)
        hit = sorted((r["u_read"] or 0) - r["d"] for r in q)
        L.append(f"- Render calibration on qwen3-32b ({len(q):,} calls; the tokenizer is its own): served prompt tokens "
                 f"minus rendered tokens: median {off[len(off) // 2]:,}, p10 {off[len(off) // 10]:,}, p90 "
                 f"{off[9 * len(off) // 10]:,}, i.e. the unlogged tool schemas (TOOL_SCHEMA_TOKENS = "
                 f"{TOOL_SCHEMA_TOKENS:,}, added to every prompt); vLLM's cache_read_input_tokens minus the modeled "
                 f"cached prefix: median {hit[len(hit) // 2]:,}, p10 {hit[len(hit) // 10]:,}, p90 "
                 f"{hit[9 * len(hit) // 10]:,} tokens (16-token blocks; the low tail is evictions, 40.8K tokens of "
                 f"KV shared by four lanes).")
    return L


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0, help="first N trials only (testing)")
    ap.add_argument("--models", default=",".join(hc.HE_MODELS))
    ap.add_argument("--benches", default=",".join(hc.HE_BENCHES))
    ap.add_argument("--table-only", action="store_true", help="rebuild the table from data/e0b/")
    a = ap.parse_args()
    if a.table_only:
        recs = list(hc.read_jsonl(OUT / "transitions.jsonl"))
        diag = json.loads((OUT / "summary.json").read_text())["diag"]
        write_e0b_table(recs, diag)
        return
    trials = [(m, b, str(p)) for m, b, p in hc.he_trials(tuple(a.models.split(",")), tuple(a.benches.split(",")))]
    if a.limit:
        trials = trials[:a.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(a.workers, 32, len(trials)))
    recs, diag, logs = [], Counter(), []
    t0 = time.time()
    if workers == 1:
        _init_worker()
        results = map(_run_trial, trials)
    else:
        import multiprocessing as mp
        pool = mp.get_context("fork").Pool(workers, initializer=_init_worker)
        results = pool.imap_unordered(_run_trial, trials, chunksize=1)
    for i, (r_, d_, log) in enumerate(results, 1):
        recs += r_
        diag.update(d_)
        logs.append(log)
        if "error" in log:
            print("ERROR", log["trial"], log["error"], file=sys.stderr)
        if i % 50 == 0:
            print(f"{i}/{len(trials)} trials, {len(recs):,} transitions, {time.time() - t0:.0f} s", file=sys.stderr)
    diag["trials"] = len(trials)
    diag["trial_errors"] = sum(1 for x in logs if "error" in x)
    recs.sort(key=lambda r: (r["model"], r["bench"], r["trial"], r["n"]))
    with open(OUT / "transitions.jsonl", "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    agg = aggregate(recs)
    (OUT / "summary.json").write_text(json.dumps({"diag": dict(diag), "agg": agg, "logs": logs}, indent=1))
    write_e0b_table(recs, dict(diag))


def write_e0b_table(recs: list[dict], diag: dict) -> None:
    agg = aggregate(recs)
    note = ("E0b, rule D4. Method in the script's docstring: s15's edit rules (code.py:2415-2566, prepare_context "
            "3529-3555) re-run on the state the harness has at (i) round start, (ii) decode end, (iii) tool end, "
            "compared with the logged request n. Tokens: Qwen3-32B tokenizer on a simple per-message render (all "
            "models; the unlogged tool schemas as a calibrated constant). Exact prefix caching against request j + "
            "reply j, no eviction. Trials: hs_common.he_trials() (q-phase profile, infra reruns included).")
    out = hc.write_table("E0b_lookahead", "E0b lookahead: is an edit's surviving layout known before the next call?",
                         table_lines(recs, diag, agg), note)
    print(out)


if __name__ == "__main__":
    main()
