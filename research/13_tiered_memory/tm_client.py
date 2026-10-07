"""Client side of research/13_tiered_memory's D1 and B1, installed in the harness process before 11's mu_session
wraps the Anthropic SDK (so mu_session's tags and request capture still see every full request):

    mu_sweep.py run ... --python research/13_tiered_memory/bin/tmpython      (tm_boot.py calls install())

Env (passed through mu_sweep to every session):
  TM_CLIENT_D1=1       send only the new messages (X-TM-Base: k); the server holds messages[:k] and the replies
  TM_CLIENT_VERIFY=1   with D1: send all messages plus X-TM-Verify, so the server checks its D1 ids (S2)
  TM_CLIENT_HINTS=1    at exit, POST /tm/hint {"ev": "session_end"} for every session this process ran
  TM_CLIENT_LOG=PATH   one JSON line per call (mode, k, messages sent, client-side diff time, resyncs)

Per chain (session, purpose, agent from mu_session's X-Request-Id) it keeps a fingerprint of every message the server
holds: the messages last sent plus the reply as returned, fingerprinted AT SEND TIME. k is the common prefix of those
fingerprints with the new list's. The harness edits old messages in place (micro-compaction replaces a tool result's
content inside the same dict), so neither object identity nor a reference to the old dict can show an edit; a
fingerprint taken when the message was sent can. Python caches each string's hash, so fingerprinting the unchanged
history again costs little. An edit just gives a smaller k. A 409 `tm_resync` answer is retried once in full.
[2026-10-04, S2: the first version compared `msgs[i] is known[i]` and missed in-place compactions: 52 of 767 live
verify calls reused stale tool results.]
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tm_common as tc  # noqa: E402

D1 = os.environ.get("TM_CLIENT_D1", "0") == "1"
VERIFY = os.environ.get("TM_CLIENT_VERIFY", "0") == "1"
HINTS = os.environ.get("TM_CLIENT_HINTS", "0") == "1"
LOG = os.environ.get("TM_CLIENT_LOG", "")
STATS: Counter = Counter()
_LOCK = threading.Lock()
_SESSIONS: dict[str, str] = {}          # session -> base url (for the exit hint)


class View:
    __slots__ = ("known", "fp")

    def __init__(self, known: list, fp: str) -> None:
        self.known, self.fp = known, fp             # known: per-message fingerprints taken when sent


STATE: dict[str, View] = {}


def _log(rec: dict) -> None:
    if LOG:
        rec["wall"] = time.time()
        with _LOCK, open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")


def _jsonable(x):
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def fingerprint(model, system, tools) -> str:
    raw = json.dumps([model, _jsonable(system), _jsonable(tools)], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _blocks(content) -> list[tuple]:
    if isinstance(content, str):
        return [("text", content)]
    out = []
    for b in content or []:
        get = (lambda k, b=b: b.get(k)) if isinstance(b, dict) else (lambda k, b=b: getattr(b, k, None))
        t = get("type")
        if t == "text":
            out.append(("text", get("text") or ""))
        elif t == "tool_use":
            out.append(("tool_use", get("id"), get("name"), json.dumps(_jsonable(get("input")), sort_keys=True)))
        elif t == "tool_result":
            out.append(("tool_result", get("tool_use_id"), json.dumps(_jsonable(get("content")), sort_keys=True,
                                                                       default=str)))
        else:
            out.append((t, json.dumps(_jsonable(b), sort_keys=True, default=str)))
    return out


def same_message(a, b) -> bool:
    if a is b:
        return True
    try:
        if a == b:
            return True
    except Exception:
        pass
    ra = a.get("role") if isinstance(a, dict) else getattr(a, "role", None)
    rb = b.get("role") if isinstance(b, dict) else getattr(b, "role", None)
    if ra != rb:
        return False
    ca = a.get("content") if isinstance(a, dict) else getattr(a, "content", None)
    cb = b.get("content") if isinstance(b, dict) else getattr(b, "content", None)
    return _blocks(ca) == _blocks(cb)


def _hval(x) -> int:
    if isinstance(x, str):
        return hash(x)                              # cached on the str object
    try:
        return hash(json.dumps(_jsonable(x), sort_keys=True, default=str))
    except Exception:
        return hash(repr(x))


def msg_fp(m) -> int:
    """Fingerprint of one message's content, whatever form the harness keeps it in (dicts or SDK objects)."""
    role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
    if isinstance(content, str):
        return hash((role, hash(content)))
    parts = []
    for b in content or []:
        get = (lambda k, b=b: b.get(k)) if isinstance(b, dict) else (lambda k, b=b: getattr(b, k, None))
        t = get("type")
        if t == "text":
            parts.append((t, _hval(get("text") or "")))
        elif t == "tool_use":
            parts.append((t, get("id"), get("name"), _hval(get("input"))))
        elif t == "tool_result":
            c = get("content")
            parts.append((t, get("tool_use_id"), _hval(c) if not isinstance(c, list) else
                          tuple(_hval(x.get("text") if isinstance(x, dict) else x) for x in c)))
        else:
            parts.append((t, _hval(b)))
    return hash((role, tuple(parts)))


def common_prefix(msgs: list, known: list) -> int:
    """k = the longest prefix of `msgs` whose fingerprints equal `known` (fingerprints, or messages)."""
    n = min(len(msgs), len(known))
    k = 0
    while k < n:
        kf = known[k] if isinstance(known[k], int) else msg_fp(known[k])
        if msg_fp(msgs[k]) != kf:
            break
        k += 1
    return k


def install() -> None:
    from anthropic import ConflictError
    from anthropic.resources.messages import messages as sdk_messages

    orig = sdk_messages.Messages.create

    def create(self, *args, **kwargs):
        if not (D1 or HINTS) or kwargs.get("stream"):
            return orig(self, *args, **kwargs)
        h = dict(kwargs.get("extra_headers") or {})
        sess, rid = h.get("X-Session-ID"), h.get("X-Request-Id")
        if not sess:
            return orig(self, *args, **kwargs)
        try:
            _SESSIONS.setdefault(sess, str(self._client.base_url).rstrip("/"))
        except Exception:
            pass
        if not D1:
            return orig(self, *args, **kwargs)
        chain = tc.chain_of(rid, sess)
        msgs = list(kwargs.get("messages") or [])
        t0 = time.perf_counter()
        fp = fingerprint(kwargs.get("model"), kwargs.get("system"), kwargs.get("tools"))
        view = STATE.get(chain)
        k = common_prefix(msgs, view.known) if view is not None and view.fp == fp else 0
        full = dict(kwargs, extra_headers={**h, "X-TM-Init": "1"})
        if k <= 0 or k >= len(msgs):
            send, mode = full, "init"
        elif VERIFY:
            send, mode = dict(kwargs, extra_headers={**h, "X-TM-Base": str(k), "X-TM-Verify": "1"}), "verify"
        else:
            send = {key: v for key, v in kwargs.items() if key not in ("system", "tools", "tool_choice")}
            send.update(messages=msgs[k:], extra_headers={**h, "X-TM-Base": str(k)})
            mode = "delta"
        diff_ms = 1000 * (time.perf_counter() - t0)
        resync = False
        try:
            resp = orig(self, *args, **send)
        except ConflictError as exc:
            if "tm_resync" not in str(exc) and "session state" not in str(exc):
                raise
            resync, mode = True, mode + "+resync"
            STATS["resync"] += 1
            resp = orig(self, *args, **full)
        STATS[mode] += 1
        STATE[chain] = View([msg_fp(m) for m in msgs] + [msg_fp({"role": "assistant", "content": resp.content})], fp)
        _log({"rid": rid, "chain": chain, "mode": mode, "k": k, "n_msgs": len(msgs),
              "sent_msgs": len(send.get("messages") or []), "diff_ms": round(diff_ms, 3), "resync": resync})
        return resp

    sdk_messages.Messages.create = create
    if HINTS:
        atexit.register(_session_end)


def _session_end() -> None:
    import urllib.request
    for sess, base in list(_SESSIONS.items()):
        url = base[:-3] + "/tm/hint" if base.endswith("/v1") else base + "/tm/hint"
        body = json.dumps({"s": sess, "ev": "session_end", "t": time.time()}).encode()
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass
