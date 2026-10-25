"""Server-aware pacing for the s15 harness (research/18_harness_wait).

Three pieces, all off unless an S15_* variable turns them on (with none set, loading this module
changes nothing and makes no network request):

* a `wait(seconds, reason?)` tool the model can call: it runs after the round's other tool calls,
  i.e. at the end of the round, and holds the session's next model call;
* a one-line server status appended to the newest user message before every lead call, so the
  model can see when the server is crowded (append-only: earlier bytes of the request never change,
  so the prefix cache keeps working);
* two harness-side hold rules, decided before a lead call without involving the model.

    S15_WAIT_TOOL=1          advertise `wait`
    S15_STATUS_LINE=1        append the status line every round
    S15_STATUS_VIEW=stock    what the line and the rules read. stock: only what vLLM's /metrics also
                             exposes (running, waiting, KV in use, prompt-cache hits, preemptions);
                             share: the session's share of the KV pool (needs S15_SESSION_ID)
    S15_HOLD_POLICY=none     harness holds: none | backoff (stock view) | share (share view)
    S15_HOLD_PHASES=mid      where holds happen: mid = after a tool round, start = a turn's first call
    S15_HOLD_MAX_S=600       cap per hold point; S15_WAIT_MAX_S=300: cap per wait call
    S15_STATUS_URL           default $ANTHROPIC_BASE_URL/wt/status (served by wt_server_ext)
    S15_WAIT_TEXT=v1         the tool description: v1, or v2 (adds the cost of a wait-only round)
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


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Config:
    wait_tool: bool = False
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

    @property
    def any(self) -> bool:
        return self.wait_tool or self.status_line or self.policy != "none"

    def as_dict(self) -> dict:
        out = dict(self.__dict__)
        out["phases"] = sorted(self.phases)
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
    return Config(
        wait_tool=env.get("S15_WAIT_TOOL", "0") == "1",
        status_line=env.get("S15_STATUS_LINE", "0") == "1",
        view=view, policy=policy, phases=phases & {"mid", "start"} or frozenset({"mid"}),
        hold_max_s=_float("S15_HOLD_MAX_S", 600.0), wait_max_s=_float("S15_WAIT_MAX_S", 300.0),
        url=env.get("S15_STATUS_URL") or None, session_id=env.get("S15_SESSION_ID") or None,
        d0=_float("S15_BACKOFF_D0", 2.0), dmax=_float("S15_BACKOFF_DMAX", 30.0),
        enter_kv=_float("S15_BACKOFF_ENTER_KV", 0.92), exit_kv=_float("S15_BACKOFF_EXIT_KV", 0.85),
        tau=_float("S15_SHARE_TAU", 0.9), poll_s=_float("S15_SHARE_POLL_S", 2.0),
        seed=env.get("S15_HOLD_SEED", ""), timeout_s=_float("S15_STATUS_TIMEOUT_S", 0.5),
        wait_text=env.get("S15_WAIT_TEXT", "v1") if env.get("S15_WAIT_TEXT", "v1") in WAIT_TEXTS else "v1")


CONFIG = load_config()
_lock = threading.Lock()
_state = {"fails": 0, "rest_until": 0.0, "requests": 0, "errors": 0, "turn_t0": None}
_rng: random.Random | None = None
SLEEP = time.sleep          # tests replace these two
CLOCK = time.monotonic


def wait_tool() -> dict:
    """The `wait` schema with the configured description (S15_WAIT_TEXT)."""
    return {**WAIT_TOOL, "description": WAIT_TEXTS[CONFIG.wait_text]}


def reload() -> Config:
    """Re-read the environment (tests; a driver that sets S15_* after loading the harness)."""
    global CONFIG, _rng
    CONFIG = load_config()
    _rng = None
    with _lock:
        _state.update(fails=0, rest_until=0.0)
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
    return out


def append_status_line(messages: list) -> str:
    """Append the status line to the newest user message (the model has not seen it yet)."""
    if not CONFIG.status_line:
        return ""
    line = format_line(fetch(CONFIG.view, "line"))
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
    line = format_line(after)
    return f"{text}\n{line}" if line else text


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
