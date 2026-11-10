"""Server-aware pacing for the s15 harness (research/18_harness_wait).

Three pieces, all off unless an S15_* variable turns them on (with none set, loading this module
changes nothing and makes no network request):

* a `wait(seconds, reason?)` tool the model can call: it runs after the round's other tool calls,
  i.e. at the end of the round, and holds the session's next model call;
* a one-line server status appended to the newest user message before every lead call, so the
  model can see when the server is crowded (append-only: earlier bytes of the request never change,
  so the prefix cache keeps working);
* two harness-side hold rules, decided before a lead call without involving the model;
* (research/19_efficiency_signals) two more tools, `wait_for_capacity` (the model asks to step back,
  the harness releases it when the share rule says its context fits) and `server_status` (the model
  reads the status when it wants to), a per-round `[cost]` receipt of the session's own last step,
  and a system-prompt paragraph about the shared server;
* (2026-10-30, a topic 19 follow-up) a KV rule: wait text v5 and the paragraph `wait_kv` tell the model
  to add `wait` whenever the [server] line shows the KV cache at S15_WAIT_KV or more, and the line can
  show that number (S15_STATUS_FORMAT=kv), with a one-sentence cue at or above it (kv_cue).

    S15_WAIT_TOOL=1          advertise `wait`
    S15_TOOLS=a,b            advertise these pacing tools: wait, wait_for_capacity, server_status
                             (S15_WAIT_TOOL=1 is the same as S15_TOOLS=wait)
    S15_SERVER_SECTION       none | wait | tools | wait_kv: the system-prompt paragraph about the shared server
    S15_RECEIPT=1            append the [cost] receipt of the last model step every round
    S15_YIELD_MAX_S=1800     cap of wait_for_capacity; S15_YIELD_DEFAULT_S=600 when the model gives none
    S15_STATUS_LINE=1        append the status line every round
    S15_STATUS_VIEW=stock    what the line and the rules read. stock: only what vLLM's /metrics also
                             exposes (running, waiting, KV in use, prompt-cache hits, preemptions);
                             share: the session's share of the KV pool (needs S15_SESSION_ID)
    S15_HOLD_POLICY=none     harness holds: none | backoff (stock view) | share (share view)
    S15_HOLD_PHASES=mid      where holds happen: mid = after a tool round, start = a turn's first call
    S15_HOLD_MAX_S=600       cap per hold point; S15_WAIT_MAX_S=300: cap per wait call
    S15_STATUS_URL           default $ANTHROPIC_BASE_URL/wt/status (served by wt_server_ext)
    S15_WAIT_TEXT=v1         the tool description: v1, v2 (adds the cost of a wait-only round), v3
                             (topic 19: runs after the round's other tools, with the server paragraph), v5
                             (the KV rule, with the paragraph wait_kv) or v6 (v5 plus a worked example)
    S15_STATUS_FORMAT        the line's wording: unset (format_line's, by view) | kv ("KV cache 94% full;
                             12 requests waiting") | kv_cue (kv, and at or above S15_WAIT_KV also "At 80% or
                             more: add a wait call to your response.")
    S15_WAIT_KV=0.8          the KV-cache use that text v5, paragraph wait_kv and the kv_cue line name ("80%")
    S15_SAVE_TOOL=1          advertise `save_context` to the lead (research/23_efficiency_tools): copy the session's
                             KV cache to host memory (same as listing save_context in S15_TOOLS)
    S15_SAVE_TEXT=s1         its description: s1 (with every wait), s2 (its own KV rule), s3 (s2, when the
                             response pauses the session), s4 (s2 plus a worked example), s6 (s1 plus a worked
                             example); S15_STATUS_FORMAT=kv_cue_save names save_context in the line's cue too
    S15_SESSION_ID           this session's id on the server (the X-Session-ID header the driver sends)
    rule parameters          S15_BACKOFF_D0=2, S15_BACKOFF_DMAX=30, S15_BACKOFF_ENTER_KV=0.92,
                             S15_BACKOFF_EXIT_KV=0.85, S15_SHARE_TAU=0.9, S15_SHARE_POLL_S=2,
                             S15_HOLD_SEED (jitter seed; default: the session id)

backoff: a hold starts when a request waits for KV capacity or KV in use >= ENTER_KV; it sleeps
D0 * 2**k * U(0.5, 1.5) seconds (at most DMAX), re-reads the status and ends when nothing waits for
capacity and KV in use < EXIT_KV (hysteresis), or at the cap.
share: the server ranks mid-turn sessions first come, first served by the start of their current
turn; a hold lasts while the working sets of the sessions ranked ahead plus this session's own exceed
TAU * pool, re-checked every POLL_S * U(0.5, 1.5) seconds, up to the cap.

The status client fails open: a timeout, an error, a missing URL or a snapshot older than 5 s count
as "no status" (no hold, no line); it never raises, and after 3 failures in a row it rests for 30 s.
Its messages never contain "429" or "529" (the harness retries errors that do).

wait_for_capacity is deferred to the end of the round like `wait`. It then holds while the share rule
says the session does not fit (the sessions ranked ahead plus its own working set > TAU * pool),
re-checking every POLL_S * U(0.5, 1.5) seconds, until it fits, its cap, or no status. The [cost]
receipt reads the last lead call's usage (prompt, cache hit, output) and the harness's own clock. When
the server's record of that call's admission is in the share view (`me.last`, matched by its prompt
tokens), it adds the queue wait and splits the uncached tokens: recomputed after eviction = ideal -
hit, new or changed = prompt - ideal (ideal: the leading tokens ever cached on the server).
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import random
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

WAIT_TOOL_NAME = "wait"
# S15_WAIT_TEXT picks the description. v2 (2026-10-24, after the topic 18 pilot, where the model chained dozens of
# 1-second wait-only rounds) adds what such a round costs: one more model step on the same server.
WAIT_TEXTS = {
    "v1": ("Pause this session before its next step, to let a crowded model server catch up. "
           "Many sessions share one model server; when its KV-cache memory is full, requests must "
           "recompute context that was evicted, and every session slows down. Each round ends with a "
           "[server] status line. Call wait together with your other tool calls in the same response: "
           "it runs after them, and your next step starts when it ends. The result reports the server "
           "status after the pause."),
    "v2": ("Pause this session before its next step, to let a crowded model server catch up. "
           "Many sessions share one model server; when its KV-cache memory is full, requests must "
           "recompute context that was evicted, and every session slows down. Each round ends with a "
           "[server] status line. Call wait together with your other tool calls in the same response: "
           "it runs after them, and your next step starts when it ends. A response that holds only a "
           "wait call still costs one model step on the server when the wait ends, so prefer one longer "
           "wait to many short ones, and keep working when the server is not crowded. The result reports "
           "the server status after the pause."),
    # v3 (2026-10-26, topic 19): the same facts, worded for a model that also reads the server paragraph
    "v3": ("Pause this session after this response's other tool calls, so a crowded model server can catch "
           "up. The harness runs your other tool calls first, then waits `seconds` (1-300) before your next "
           "step; the result reports the server status after the pause. Put wait in a response that also "
           "does work: a response with only wait costs a full model step on the server."),
    # v4 (2026-10-26, written before topic 19's pilot P2 ran): v3 plus one sentence against wait-only replies.
    # Used only if P2 trips the revision rule of the plan (>= 30 wait-only calls per 100 work calls, or > 25 % of
    # sessions with a run of >= 10 wait-only rounds), and tested in P2b.
    "v4": ("Pause this session after this response's other tool calls, so a crowded model server can catch "
           "up. The harness runs your other tool calls first, then waits `seconds` (1-300) before your next "
           "step; the result reports the server status after the pause. Put wait in a response that also "
           "does work: a response with only wait costs a full model step on the server. Never reply with only "
           "wait: if you would only wait, take your next useful step and add wait to that response."),
    # v5 (2026-10-30, after topic 19, where MiniMax-M2.7 called v3's wait 3 times in 21,081 calls): a rule on a number
    # the [server] line shows (S15_STATUS_FORMAT=kv) instead of "crowded", and no warning about what a wait costs.
    # {kv} is S15_WAIT_KV as a percentage.
    "v5": ("Pause this session for `seconds` after this response's other tool calls, so the shared model server can "
           "catch up. Rule: whenever the newest [server] line shows the KV cache {kv} full or more, include one wait "
           "call in your response next to your other tool calls; the harness runs them first and then pauses. Wait "
           "about 10-30 seconds, longer when many requests are waiting. Below {kv}, do not wait, and never add wait "
           "to your final answer. The result reports the server status after the pause."),
}
# v6 (2026-10-31, after the v5 replay screen, where MiniMax-M2.7 added wait to at most ~5 % of replies at 93 % KV):
# v5 plus a worked example on each side of the threshold. {kv_hi} / {kv_lo} are the threshold + 10 / - 25 points.
WAIT_TEXTS["v6"] = WAIT_TEXTS["v5"] + (
    " Example: if the newest line reads \"[server] KV cache {kv_hi} full; 12 requests waiting\", call your next tools "
    "and add wait with seconds 20 in the same response; if it reads \"[server] KV cache {kv_lo} full; 2 requests "
    "waiting\", do not call wait.")
YIELD_TOOL_NAME = "wait_for_capacity"
STATUS_TOOL_NAME = "server_status"
SAVE_TOOL_NAME = "save_context"
PACING_TOOLS = (WAIT_TOOL_NAME, YIELD_TOOL_NAME, STATUS_TOOL_NAME, SAVE_TOOL_NAME)  # the order they are advertised in
DEFERRED_TOOLS = (WAIT_TOOL_NAME, YIELD_TOOL_NAME, SAVE_TOOL_NAME)                 # run after the round's other tools
LEAD_ONLY_TOOLS = (SAVE_TOOL_NAME,)     # it saves the lead's last request, so subagents and teammates don't get it
# S15_SAVE_TEXT picks save_context's description (research/23_efficiency_tools, 2026-11-02). The tool copies the
# session's context (its KV cache) to host memory: the harness re-sends the last lead request with max_tokens 1 and
# the store flag (X-KV-Store: 1), and the server's KV offload (LMCache) keeps it, so a later step that finds it
# evicted from GPU memory reloads it instead of recomputing it. s1 ties it to the wait rule; s2 gives it its own KV
# rule; s3 adds long commands to s2; s4 is s2 plus a worked example. {kv} is S15_WAIT_KV as a percentage.
_SAVE_MECHANISM = (
    "Copy this session's processed context (its KV cache) from the model server's GPU memory to host memory, after "
    "this response's other tool calls. When the GPU's KV cache is full, the server evicts the contexts of sessions "
    "that are paused, and their next step must recompute them; a saved context is reloaded from host memory "
    "instead, which is several times faster. It takes no arguments and returns at once.")
SAVE_TEXTS = {
    "s1": _SAVE_MECHANISM + " Rule: whenever you add a wait call, also add save_context to the same response.",
    "s2": _SAVE_MECHANISM + (" Rule: whenever the newest [server] line shows the KV cache {kv} full or more, include "
                             "one save_context call in your response next to your other tool calls. Below {kv}, do "
                             "not call it."),
    "s3": _SAVE_MECHANISM + (" Rule: whenever the newest [server] line shows the KV cache {kv} full or more and your "
                             "response pauses the session (it adds wait, or starts a command that may run for more "
                             "than a minute), include one save_context call in that response. Below {kv}, do not "
                             "call it."),
}
SAVE_TEXTS["s4"] = SAVE_TEXTS["s2"] + (
    " Example: if the newest line reads \"[server] KV cache {kv_hi} full; 12 requests waiting\", add save_context "
    "(and wait) to your response; if it reads \"[server] KV cache {kv_lo} full; 2 requests waiting\", add neither.")
# s6 (2026-11-02, the revised screen, after s1-s4 failed: s1 reached 18 % of work replies at 93 % KV, bar 30 %): s1 plus
# a worked example. The other revised variant is s1 with the [server] line's cue naming save_context (kv_cue_save).
SAVE_TEXTS["s6"] = SAVE_TEXTS["s1"] + (
    " Example: if the newest line reads \"[server] KV cache {kv_hi} full; 12 requests waiting\", call your next tools "
    "and add wait with seconds 20 and save_context to the same response; if it reads \"[server] KV cache {kv_lo} full; "
    "2 requests waiting\", add neither.")
SAVE_TOOL = {
    "name": SAVE_TOOL_NAME,
    "description": SAVE_TEXTS["s1"],
    "input_schema": {"type": "object", "properties": {}},
}
YIELD_TOOL = {
    "name": YIELD_TOOL_NAME,
    "description": (
        "Step back until the model server has room for your context. After this response's other tool "
        "calls, the harness holds this session until the sessions ahead of you (first come, first served "
        "by the start of the current turn) and your context fit in the server's KV-cache pool, or until "
        "max_seconds pass (default 600, at most 1800). While you wait, your own cached context may be "
        "evicted, but the sessions ahead of you keep theirs and finish sooner. The result says how long "
        "you waited and why the wait ended."),
    "input_schema": {
        "type": "object",
        "properties": {
            "max_seconds": {"type": "number", "description": "The longest you are willing to wait, 1-1800 seconds."},
            "reason": {"type": "string", "description": "Optional: why you are stepping back."},
        },
    },
}
STATUS_TOOL = {
    "name": STATUS_TOOL_NAME,
    "description": (
        "Read the model server's current status: running and waiting requests, KV-cache memory in use, "
        "prompt-cache hits and preemptions in the last 10 s, the size of the KV-cache pool, what the "
        "sessions ahead of you hold, and your context with how much of it is still cached. It runs with "
        "this response's other tool calls, and its result comes back with theirs."),
    "input_schema": {"type": "object", "properties": {}},
}
# S15_SERVER_SECTION: a paragraph appended to the system prompt (after its efficiency section)
_SERVER_MECHANISM = (
    "Shared server: you share one model server with other sessions. When its KV-cache memory is full, it "
    "evicts cached context, and every session's next step must then recompute its context, so all "
    "sessions slow down.")
SERVER_SECTIONS = {
    "wait": (_SERVER_MECHANISM + " Each round ends with a [server] line. When the server is crowded, add "
             "wait at the end of a response that also does work: the harness runs your other tool calls "
             "first and then pauses before your next step. A response that contains only wait costs a full "
             "model step on the crowded server."),
    "tools": (_SERVER_MECHANISM + " Each round ends with a [cost] line about your last step: how much of "
              "its prompt was reused from the server's cache, how much had to be recomputed because it had "
              "been evicted, and how long it queued. server_status shows the whole server. When the server "
              "is crowded, you can pause with wait (a fixed time) or wait_for_capacity (until there is room "
              "for your context), at the end of a response that also does work: the harness runs your other "
              "tool calls first. A response that contains only wait, wait_for_capacity or server_status "
              "costs a full model step."),
    # the paragraph that goes with wait text v4 (P2b only; see WAIT_TEXTS["v4"])
    "wait_v2": (_SERVER_MECHANISM + " Each round ends with a [server] line. When the server is crowded, add "
                "wait at the end of a response that also does work: the harness runs your other tool calls "
                "first and then pauses before your next step. A response that contains only wait costs a full "
                "model step on the crowded server. Never reply with only wait: if you would only wait, take your "
                "next useful step and add wait to that response."),
    # the paragraph that goes with wait text v5 (2026-10-30): the same rule, stated as the server's
    "wait_kv": (_SERVER_MECHANISM + " Each round ends with a [server] line that shows how full the KV cache is. "
                "Server rule: when that line shows {kv} or more, include a wait call in your response next to your "
                "other tool calls; the harness runs them first and then pauses. Below {kv}, keep working without "
                "waiting."),
}
WAIT_TOOL = {
    "name": WAIT_TOOL_NAME,
    "description": WAIT_TEXTS["v1"],
    "input_schema": {
        "type": "object",
        "properties": {
            "seconds": {"type": "number", "description": "How long to wait, 1-300 seconds."},
            "reason": {"type": "string", "description": "Optional: why you are waiting."},
        },
        "required": ["seconds"],
    },
}
STALE_S = 5.0
LINE_FORMATS = ("kv", "kv_cue", "kv_cue_save")   # S15_STATUS_FORMAT (unset: format_line's wording for the view)
# kv_cue_save (research/23_efficiency_tools, 2026-11-02, the revised save_context screen): kv_cue whose cue also names
# save_context ("At 80% or more: add a wait call and a save_context call to your response.")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Config:
    tools: frozenset = frozenset()      # the pacing tools advertised (PACING_TOOLS)
    section: str = "none"               # S15_SERVER_SECTION
    receipt: bool = False
    yield_max_s: float = 1800.0
    yield_default_s: float = 600.0
    status_line: bool = False
    view: str = "stock"
    policy: str = "none"
    phases: frozenset = frozenset({"mid"})
    hold_max_s: float = 600.0
    wait_max_s: float = 300.0
    url: str | None = None
    session_id: str | None = None
    d0: float = 2.0
    dmax: float = 30.0
    enter_kv: float = 0.92
    exit_kv: float = 0.85
    tau: float = 0.9
    poll_s: float = 2.0
    seed: str = ""
    timeout_s: float = 0.5
    wait_text: str = "v1"
    line_format: str = ""               # S15_STATUS_FORMAT (LINE_FORMATS)
    wait_kv: float = 0.8                # S15_WAIT_KV
    save_text: str = "s1"               # S15_SAVE_TEXT (SAVE_TEXTS)

    @property
    def wait_tool(self) -> bool:
        return WAIT_TOOL_NAME in self.tools

    @property
    def any(self) -> bool:
        return bool(self.tools) or self.status_line or self.receipt or self.policy != "none" \
            or self.section != "none"

    def as_dict(self) -> dict:
        out = dict(self.__dict__)
        out["phases"] = sorted(self.phases)
        out["tools"] = [name for name in PACING_TOOLS if name in self.tools]
        return out


def load_config() -> Config:
    env = os.environ
    policy = env.get("S15_HOLD_POLICY", "none").strip().lower() or "none"
    if policy not in ("none", "backoff", "share"):
        policy = "none"
    view = env.get("S15_STATUS_VIEW", "").strip().lower()
    if view not in ("stock", "share"):
        view = "share" if policy == "share" else "stock"
    phases = frozenset(p.strip() for p in env.get("S15_HOLD_PHASES", "mid").split(",") if p.strip())
    tools = {t.strip() for t in env.get("S15_TOOLS", "").split(",") if t.strip()} & set(PACING_TOOLS)
    if env.get("S15_WAIT_TOOL", "0") == "1":
        tools.add(WAIT_TOOL_NAME)
    if env.get("S15_SAVE_TOOL", "0") == "1":
        tools.add(SAVE_TOOL_NAME)
    section = env.get("S15_SERVER_SECTION", "none").strip().lower()
    line_format = env.get("S15_STATUS_FORMAT", "").strip().lower()
    wait_kv = _float("S15_WAIT_KV", 0.8)
    return Config(
        tools=frozenset(tools), section=section if section in SERVER_SECTIONS else "none",
        receipt=env.get("S15_RECEIPT", "0") == "1",
        yield_max_s=_float("S15_YIELD_MAX_S", 1800.0), yield_default_s=_float("S15_YIELD_DEFAULT_S", 600.0),
        status_line=env.get("S15_STATUS_LINE", "0") == "1",
        view=view, policy=policy, phases=phases & {"mid", "start"} or frozenset({"mid"}),
        hold_max_s=_float("S15_HOLD_MAX_S", 600.0), wait_max_s=_float("S15_WAIT_MAX_S", 300.0),
        url=env.get("S15_STATUS_URL") or None, session_id=env.get("S15_SESSION_ID") or None,
        d0=_float("S15_BACKOFF_D0", 2.0), dmax=_float("S15_BACKOFF_DMAX", 30.0),
        enter_kv=_float("S15_BACKOFF_ENTER_KV", 0.92), exit_kv=_float("S15_BACKOFF_EXIT_KV", 0.85),
        tau=_float("S15_SHARE_TAU", 0.9), poll_s=_float("S15_SHARE_POLL_S", 2.0),
        seed=env.get("S15_HOLD_SEED", ""), timeout_s=_float("S15_STATUS_TIMEOUT_S", 0.5),
        wait_text=env.get("S15_WAIT_TEXT", "v1") if env.get("S15_WAIT_TEXT", "v1") in WAIT_TEXTS else "v1",
        line_format=line_format if line_format in LINE_FORMATS else "",
        wait_kv=wait_kv if 0.0 < wait_kv <= 1.0 else 0.8,
        save_text=env.get("S15_SAVE_TEXT", "s1") if env.get("S15_SAVE_TEXT", "s1") in SAVE_TEXTS else "s1")


CONFIG = load_config()
_lock = threading.Lock()
_state = {"fails": 0, "rest_until": 0.0, "requests": 0, "errors": 0, "turn_t0": None, "last_call": None}
_rng: random.Random | None = None
SLEEP = time.sleep          # tests replace these two
CLOCK = time.monotonic


def kv_threshold() -> str:
    """S15_WAIT_KV as the texts and the cue write it ("80%")."""
    return f"{round(100 * CONFIG.wait_kv)}%"


def _with_kv(text: str) -> str:
    t = round(100 * CONFIG.wait_kv)
    return (text.replace("{kv}", kv_threshold()).replace("{kv_hi}", f"{min(99, t + 10)}%")
            .replace("{kv_lo}", f"{max(1, t - 25)}%"))


def wait_tool() -> dict:
    """The `wait` schema with the configured description (S15_WAIT_TEXT)."""
    return {**WAIT_TOOL, "description": _with_kv(WAIT_TEXTS[CONFIG.wait_text])}


def enabled(name: str) -> bool:
    return name in CONFIG.tools


def save_tool() -> dict:
    """The `save_context` schema with the configured description (S15_SAVE_TEXT)."""
    return {**SAVE_TOOL, "description": _with_kv(SAVE_TEXTS[CONFIG.save_text])}


def pacing_tools(lead: bool = False) -> list[dict]:
    """The schemas of the advertised pacing tools, in PACING_TOOLS order (none by default). LEAD_ONLY_TOOLS only for
    the lead agent (lead=True)."""
    schemas = {WAIT_TOOL_NAME: wait_tool, YIELD_TOOL_NAME: lambda: dict(YIELD_TOOL),
               STATUS_TOOL_NAME: lambda: dict(STATUS_TOOL), SAVE_TOOL_NAME: save_tool}
    return [schemas[name]() for name in PACING_TOOLS
            if name in CONFIG.tools and (lead or name not in LEAD_ONLY_TOOLS)]


def is_deferred(name: str | None) -> bool:
    """Does this tool run at the end of the round, after the round's other tool calls?"""
    return name in DEFERRED_TOOLS and name in CONFIG.tools


def server_section() -> str:
    """The system-prompt paragraph about the shared server ("" by default)."""
    return _with_kv(SERVER_SECTIONS.get(CONFIG.section, ""))


def reload() -> Config:
    """Re-read the environment (tests; a driver that sets S15_* after loading the harness)."""
    global CONFIG, _rng
    CONFIG = load_config()
    _rng = None
    with _lock:
        _state.update(fails=0, rest_until=0.0, last_call=None)
    return CONFIG


def begin_turn() -> None:
    """The lead starts a turn: reported to the server with every status request, so its ranking
    (first come, first served by turn start) is exact even after a long hold."""
    _state["turn_t0"] = round(time.time(), 3)


def rng() -> random.Random:
    global _rng
    if _rng is None:
        _rng = random.Random(f"s15-hold-{CONFIG.seed or CONFIG.session_id or os.getpid()}")
    return _rng


def status_url() -> str | None:
    if CONFIG.url:
        return CONFIG.url
    base = os.environ.get("ANTHROPIC_BASE_URL")
    return base.rstrip("/") + "/wt/status" if base else None


def fetch(view: str | None = None, state: str = "line") -> dict | None:
    """GET the server status (fail-open: None when unavailable). `state` tells the server what this
    session is doing (line: about to call the model; check / hold: a harness hold; wait: the wait tool),
    so it can count a holding session as mid-turn."""
    url = status_url()
    if not url:
        return None
    now = time.monotonic()
    with _lock:
        if now < _state["rest_until"]:
            return None
        _state["requests"] += 1
    query = {"view": view or CONFIG.view, "state": state}
    if CONFIG.session_id:
        query["sid"] = CONFIG.session_id
    if _state["turn_t0"] is not None:
        query["turn"] = _state["turn_t0"]
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(query)}", timeout=CONFIG.timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(body, dict) or float(body.get("age_ms", 0) or 0) > STALE_S * 1000:
            raise ValueError("stale or malformed status")
    except Exception:
        with _lock:
            _state["errors"] += 1
            _state["fails"] += 1
            if _state["fails"] >= 3:
                _state["fails"] = 0
                _state["rest_until"] = time.monotonic() + 30.0
        return None
    with _lock:
        _state["fails"] = 0
    return body


# -- what the model sees ------------------------------------------------------------------------------
def _pct(value) -> str:
    return "n/a" if value is None else f"{round(100 * float(value))}%"


def format_line(status: dict | None, view: str | None = None) -> str:
    if not status:
        return ""
    view = view or status.get("view") or CONFIG.view
    if view == "share":
        me = status.get("me") or {}
        return (f"[server] KV pool {int(status.get('pool_tokens') or 0):,} tokens; sessions ahead of you hold "
                f"{int(me.get('ahead_ws') or 0):,}; your context {int(me.get('ws') or 0):,}, "
                f"{int(me.get('prefix') or 0):,} still cached; {int(status.get('waiting') or 0)} requests waiting")
    return (f"[server] running {int(status.get('running') or 0)}, waiting {int(status.get('waiting') or 0)} "
            f"({int(status.get('waiting_capacity') or 0)} for KV memory); KV memory in use "
            f"{_pct(status.get('kv_usage'))}; prompt-cache hits {_pct(status.get('hit_rate_10s'))} and "
            f"preemptions {int(status.get('preemptions_10s') or 0)} in the last 10 s")


def kv_line(status: dict | None) -> str:
    """The line with S15_STATUS_FORMAT=kv: the KV cache's use and the queue; with kv_cue, at or above S15_WAIT_KV
    (compared in the whole percentages the line shows, so "80%" always comes with the cue), the rule's cue too."""
    if not status:
        return ""
    kv = status.get("kv_usage")
    line = f"[server] KV cache {_pct(kv)} full; {int(status.get('waiting') or 0)} requests waiting"
    if CONFIG.line_format in ("kv_cue", "kv_cue_save") and kv is not None \
            and round(100 * float(kv)) >= round(100 * CONFIG.wait_kv):
        what = "a wait call and a save_context call" if CONFIG.line_format == "kv_cue_save" else "a wait call"
        line += f". At {kv_threshold()} or more: add {what} to your response."
    return line


def round_line(status: dict | None) -> str:
    """The [server] line the model reads every round and after a wait: kv_line when S15_STATUS_FORMAT asks for it,
    else format_line's wording for the view."""
    return kv_line(status) if CONFIG.line_format in LINE_FORMATS else format_line(status)


def format_full(status: dict | None) -> str:
    """server_status: the stock fields and, from the share view, the session's own place, in one line."""
    if not status:
        return ""
    line = format_line(status, "stock")
    if status.get("pool_tokens"):
        me = status.get("me") or {}
        line += (f"; KV pool {int(status.get('pool_tokens') or 0):,} tokens; sessions ahead of you hold "
                 f"{int(me.get('ahead_ws') or 0):,}; your context {int(me.get('ws') or 0):,}, "
                 f"{int(me.get('prefix') or 0):,} still cached")
    return line


def signals(status: dict | None) -> dict | None:
    """The fields a decision used, for the trace."""
    if not status:
        return None
    keys = ("age_ms", "running", "waiting", "waiting_capacity", "kv_usage", "hit_rate_10s",
            "preemptions_10s", "pool_tokens", "mid_sessions", "holders")
    out = {k: status.get(k) for k in keys if k in status}
    me = status.get("me")
    if isinstance(me, dict):
        out["me"] = {k: me.get(k) for k in ("ws", "prefix", "res", "ahead_ws", "ahead_n", "rank", "mid")}
        if me.get("last") is not None:
            out["me"]["last"] = me.get("last")
    return out


def note_call(response, seconds: float) -> None:
    """Remember the last lead call's usage and duration for the next round's [cost] receipt."""
    if not CONFIG.receipt:
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    def tokens(name: str) -> int:
        try:
            return int(getattr(usage, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    hit = tokens("cache_read_input_tokens")
    _state["last_call"] = {"prompt": tokens("input_tokens") + hit + tokens("cache_creation_input_tokens"),
                           "hit": hit, "out": tokens("output_tokens"), "secs": round(float(seconds), 3)}


def receipt_line(status: dict | None) -> str:
    """The [cost] line for the last lead call ("" before the first one)."""
    call = _state.get("last_call")
    if not call or not call["prompt"]:
        return ""
    prompt, hit = call["prompt"], call["hit"]
    line = f"[cost] your last step: {prompt:,} prompt tokens, {hit:,} reused from the server's cache"
    last = ((status or {}).get("me") or {}).get("last") or {}
    if last.get("np") == prompt and last.get("ideal") is not None and last.get("wait_s") is not None:
        ideal = max(hit, min(prompt, int(last["ideal"])))
        line += (f", {ideal - hit:,} recomputed because they had been evicted, {prompt - ideal:,} new or "
                 f"changed; queued {float(last['wait_s']):.1f} s")
    else:
        line += f", {prompt - hit:,} computed"
    return line + f"; {call['out']:,} tokens generated; {call['secs']:.1f} s in all"


def append_status_line(messages: list) -> str:
    """Append this round's lines -- the status line (S15_STATUS_LINE) and the [cost] receipt
    (S15_RECEIPT) -- to the newest user message (the model has not seen it yet)."""
    if not CONFIG.status_line and not (CONFIG.receipt and _state.get("last_call")):
        return ""
    status = fetch(CONFIG.view if CONFIG.status_line else "share", "line")
    lines = []
    if CONFIG.status_line:
        lines.append(round_line(status))
    if CONFIG.receipt:
        lines.append(receipt_line(status))
    line = "\n".join(text for text in lines if text)
    if not line:
        return ""
    block = {"type": "text", "text": line}
    if messages and messages[-1].get("role") == "user":
        content = messages[-1]["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        messages[-1]["content"] = list(content) + [block]
    else:
        messages.append({"role": "user", "content": [block]})
    return line


# -- the wait tool --------------------------------------------------------------------------------------
def run_wait(seconds=None, reason: str | None = None, trace=None) -> str:
    """Body of the `wait` tool: sleep (capped), heartbeating in the share view, then report the status."""
    try:
        requested = float(seconds)
    except (TypeError, ValueError):
        return "Error: wait needs a number of seconds."
    if not math.isfinite(requested) or requested <= 0:
        return "Error: wait needs a positive number of seconds."
    target = min(requested, CONFIG.wait_max_s)
    before = fetch(CONFIG.view, "wait")
    t0 = CLOCK()
    while True:
        left = target - (CLOCK() - t0)
        if left <= 0:
            break
        SLEEP(min(5.0, left))
        if CONFIG.view == "share" and target - (CLOCK() - t0) > 0:
            fetch("share", "wait")                       # heartbeat: still mid-turn
    slept = CLOCK() - t0
    after = fetch(CONFIG.view, "line")
    if trace is not None:
        trace.emit("wait_detail", {"requested_s": requested, "target_s": target, "slept_s": round(slept, 3),
                                   "reason": (reason or "")[:200], "view": CONFIG.view,
                                   "before": signals(before), "after": signals(after)})
    text = f"Waited {slept:.0f} s."
    if target < requested:
        text += f" (wait is capped at {CONFIG.wait_max_s:.0f} s.)"
    line = round_line(after)
    return f"{text}\n{line}" if line else text


def run_save(resend=None, trace=None) -> str:
    """Body of `save_context`: resend() re-sends the session's last lead request with max_tokens 1 and the store flag
    (the caller owns the client) and returns its prompt tokens; the server's KV offload then holds that context in
    host memory. The result never contains "429" or "529" (the harness retries errors that do)."""
    if resend is None:
        return "Error: save_context has nothing to save yet."
    t0 = CLOCK()
    try:
        tokens = int(resend() or 0)
    except Exception as exc:                                   # noqa: BLE001
        if trace is not None:
            trace.emit("save_detail", {"ok": False, "error": type(exc).__name__, "s": round(CLOCK() - t0, 3)})
        return "Error: save_context could not reach the server; your context was not saved."
    if trace is not None:
        trace.emit("save_detail", {"ok": True, "tokens": tokens, "s": round(CLOCK() - t0, 3)})
    return (f"Saved {tokens} tokens of your context to host memory. If the server evicts them while this session "
            f"is paused, your next step reloads them instead of recomputing them.")


def run_wait_for_capacity(max_seconds=None, reason: str | None = None, trace=None) -> str:
    """Body of `wait_for_capacity`: hold, by the share rule, until this session fits, its cap, or no status."""
    try:
        requested = CONFIG.yield_default_s if max_seconds is None else float(max_seconds)
    except (TypeError, ValueError):
        return "Error: wait_for_capacity needs max_seconds as a number."
    if not math.isfinite(requested) or requested <= 0:
        return "Error: wait_for_capacity needs a positive max_seconds."
    cap = min(requested, CONFIG.yield_max_s)
    t0 = CLOCK()
    first = last = fetch("share", "wait")
    polls, why = 0, "no_status"
    if first is not None:
        why = "fits"
        while _share_over(last):
            elapsed = CLOCK() - t0
            if elapsed >= cap:
                why = "cap"
                break
            SLEEP(max(0.0, min(CONFIG.poll_s * rng().uniform(0.5, 1.5), cap - elapsed)))
            polls += 1
            status = fetch("share", "wait")                 # also the heartbeat: still mid-turn
            if status is None:
                why = "no_status"
                break
            last = status
    waited = CLOCK() - t0
    if trace is not None:
        trace.emit("yield_detail", {"requested_s": requested, "cap_s": cap, "waited_s": round(waited, 3),
                                    "polls": polls, "why": why, "reason": (reason or "")[:200],
                                    "before": signals(first), "after": signals(last)})
    ends = {"fits": "there is room for your context now",
            "cap": f"the {cap:.0f} s limit was reached and the server is still crowded",
            "no_status": "the server status is not available"}
    text = f"Waited {waited:.0f} s: {ends[why]}."
    line = format_full(last) if why != "no_status" else ""
    return f"{text}\n{line}" if line else text


def run_server_status(trace=None) -> str:
    """Body of `server_status`: the stock fields and the session's share fields, read now."""
    status = fetch("share", "pull")
    if trace is not None:
        trace.emit("status_pull", {"signals": signals(status)})
    return format_full(status) or "The server status is not available right now."


# -- harness-side holds ---------------------------------------------------------------------------------
def _backoff_enter(s: dict) -> bool:
    return (s.get("waiting_capacity") or 0) >= 1 or (s.get("kv_usage") or 0.0) >= CONFIG.enter_kv


def _backoff_exit(s: dict) -> bool:
    return (s.get("waiting_capacity") or 0) == 0 and (s.get("kv_usage") or 0.0) < CONFIG.exit_kv


def _share_over(s: dict) -> bool:
    me = s.get("me")
    pool = s.get("pool_tokens") or 0
    if not isinstance(me, dict) or not pool:
        return False                                      # unknown session or pool: fail open
    return (me.get("ahead_ws") or 0) + (me.get("ws") or 0) > CONFIG.tau * pool


def maybe_hold(kind: str, trace=None) -> float:
    """Hold before a lead call when the configured rule says so; returns the seconds held."""
    policy = CONFIG.policy
    if policy == "none" or kind not in CONFIG.phases:
        return 0.0
    view = "share" if policy == "share" else "stock"
    first = fetch(view, "check")
    hold = first is not None and (_share_over(first) if policy == "share" else _backoff_enter(first))
    if trace is not None:
        trace.emit("hold_check", {"kind": kind, "policy": policy, "hold": hold, "signals": signals(first)})
    if not hold:
        return 0.0
    t0 = CLOCK()
    polls, step, last, why = 0, 0, first, "cap"
    span = (trace.span("hold_start", "hold_end", {"kind": kind, "policy": policy, "view": view,
                                                  "signals": signals(first)})
            if trace is not None else contextlib.nullcontext(None))
    with span as handle:
        while True:
            elapsed = CLOCK() - t0
            if elapsed >= CONFIG.hold_max_s:
                why = "cap"
                break
            if policy == "backoff":
                delay = min(CONFIG.dmax, CONFIG.d0 * (2 ** step)) * rng().uniform(0.5, 1.5)
            else:
                delay = CONFIG.poll_s * rng().uniform(0.5, 1.5)
            SLEEP(max(0.0, min(delay, CONFIG.hold_max_s - elapsed)))
            step += 1
            polls += 1
            status = fetch(view, "hold")
            if status is None:
                why = "no_status"
                break
            last = status
            if policy == "backoff" and _backoff_exit(status):
                why = "clear"
                break
            if policy == "share" and not _share_over(status):
                why = "fits"
                break
        held = CLOCK() - t0
        if handle is not None:
            handle.finish(held_s=round(held, 3), polls=polls, why=why, signals_end=signals(last))
    return held
