"""vLLM API-server extension of research/13_tiered_memory: D1 (session-resident token state) and the API-server half
of B1 (server-inferred hints, prefetch). Loaded by vLLM's own `--middleware` hook:

    PYTHONPATH=research/13_tiered_memory TM_D1=1 vllm serve ... --middleware tm_server_ext.Middleware

vLLM imports this module while building the app, in the API-server process; the first Middleware instance
installs three patches (vLLM 0.29.0):
  - AnthropicServingMessages.create_messages: a D1 request (headers below) gets its prompt token ids from the
    chain's store plus a render of only the new messages (a two-message stub + the new messages through vLLM's
    own renderer, cut after the stub);
  - vllm.renderers.online_renderer._reused_prompt_token_ids: hands those ids to vLLM's "decode-side token
    reuse" path (the one disaggregated serving uses), which skips templating and tokenisation but still sets
    up the tool and reasoning parsers;
  - AsyncLLM.generate: tees the outputs, so at completion the store holds prompt + reply ids exactly as
    generated (Qwen3's template would drop the generation prompt's empty think block when re-rendering).
A chain is one message history: (session, purpose, agent) from mu_session's X-Request-Id
('<label>~a<k>~<call>~<purpose>~<agent>'), so a session's lead, memory calls and subagents stay apart.

Headers (tm_client.py sets the X-TM-* ones; 11's mu_session sets X-Session-ID and X-Request-Id):
  X-TM-Init: 1     a full request; the chain's store restarts from it
  X-TM-Base: k     the body carries messages[k:] only; the store holds messages[:k]; system / tools come from
                   the store. 409 (type tm_resync) when the store cannot serve k: the client resends in full.
  X-TM-Verify: 1   with X-TM-Base: the body carries ALL messages; the D1 ids are served, and the full request is
                   also rendered the normal way and compared block by block (one block per <|im_start|>)
Env:
  TM_D1=1                keep per-chain token state (D1 and B1's prefetch need it)
  TM_HINTS=ipc://PATH    send B1 hints to tm_scheduler.TMScheduler (it binds the PULL end)
  TM_PREFETCH=1          a 1-token shadow request shortly before a predicted tool end
  TM_PREFETCH_MIN_S      only for predicted waits of at least this (default 2.0)
  TM_RESTORE_TOK_S       restore rate for the prefetch lead, tokens/s (default 30000)
  TM_THINK_S             expected think time after a turn end (default: the predictor's "(turn end)")
  TM_EXT_LOG=PATH        one JSON line per call, prefetch, hint and error
Every patch fails open: an exception writes an `error` line and the request continues on vLLM's normal path
where that is possible (a delta request cannot be rendered without its history: it gets 409 instead).
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sys
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from http import HTTPStatus

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tm_common as tc  # noqa: E402

D1_ON = os.environ.get("TM_D1", "0") == "1"
PREFETCH_ON = os.environ.get("TM_PREFETCH", "0") == "1"
PREFETCH_MIN_S = float(os.environ.get("TM_PREFETCH_MIN_S", "2.0"))
RESTORE_TOK_S = float(os.environ.get("TM_RESTORE_TOK_S", "30000"))
THINK_S = float(os.environ["TM_THINK_S"]) if os.environ.get("TM_THINK_S") else None
HINTS_ADDR = os.environ.get("TM_HINTS", "")
LOG_PATH = os.environ.get("TM_EXT_LOG", "")
PREFETCH_PRIORITY = int(os.environ.get("TM_PREFETCH_PRIORITY", "1000000"))
STUB_U, STUB_A = "TMSTUB user", "TMSTUB assistant"

CTX: contextvars.ContextVar = contextvars.ContextVar("tm_ctx", default=None)
STATS: Counter = Counter()
_ORIG: dict = {}
_LOCK = threading.Lock()
_INSTALLED = False


@dataclass
class Ctx:
    chain: str
    session: str
    rid: str | None
    init: bool = False
    base: int | None = None
    verify: bool = False
    shadow: bool = False
    mode: str = "full"
    prompt_ids: list | None = None
    used: bool = False
    rendering: bool = False
    k: int = 0
    prefix_len: int = 0
    rel_offsets: list | None = None
    msg_blocks: list | None = None
    sys_block: int = 0
    system: object = None
    tools: object = None
    tool_choice: object = None
    n_msgs: int = 0
    t0: float = 0.0
    render_ms: float = 0.0
    verify_rec: dict | None = None


@dataclass
class ChainState:
    ids: list
    offsets: list                       # token offset where each Anthropic message starts (reply included)
    system: object = None
    tools: object = None
    tool_choice: object = None
    epoch: int = 0
    t_fin: float = 0.0
    inflight: int = 0
    prefetch: object = None             # asyncio.Task of a pending prefetch
    session: str = ""
    extra: dict = field(default_factory=dict)


STORE: dict[str, ChainState] = {}


# -- logging and the hint bus --------------------------------------------------------------------------------------
def _log(rec: dict) -> None:
    if not LOG_PATH:
        return
    rec.setdefault("wall", time.time())
    line = json.dumps(rec, default=str)
    with _LOCK:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _error(where: str, exc: BaseException) -> None:
    STATS[f"error_{where}"] += 1
    _log({"ev": "error", "where": where, "exc": repr(exc), "tb": traceback.format_exc()[-2500:]})


class _Bus:
    def __init__(self) -> None:
        self.sock = None

    def send(self, msg: dict) -> None:
        if not HINTS_ADDR:
            return
        try:
            if self.sock is None:
                import zmq
                self.sock = zmq.Context.instance().socket(zmq.PUSH)
                self.sock.setsockopt(zmq.SNDHWM, 10000)
                self.sock.setsockopt(zmq.LINGER, 0)
                self.sock.connect(HINTS_ADDR)
            import zmq
            self.sock.send_json(msg, flags=zmq.NOBLOCK)
            STATS["hints_sent"] += 1
        except Exception as exc:          # the scheduler may not be up yet; hints are best effort
            STATS["hints_dropped"] += 1
            if STATS["hints_dropped"] <= 5:
                _error("bus", exc)


BUS = _Bus()


# -- rendering helpers -------------------------------------------------------------------------------------------
def _blocks_of(roles: list[str], prev: str = "assistant") -> list[int]:
    """Block index of each OpenAI message: Qwen3's template opens one <|im_start|> per message, except that a run
    of consecutive tool messages shares one `<|im_start|>user` block."""
    out, b = [], -1
    for r in roles:
        if not (r == "tool" and prev == "tool"):
            b += 1
        out.append(b)
        prev = r
    return out


def _convert_each(serving, messages: list, oa: list) -> list[int]:
    """Append the OpenAI form of each Anthropic message to `oa`; return how many OpenAI messages each gave."""
    counts = []
    for m in messages:
        before = len(oa)
        serving._convert_messages([m], oa, merge_inline_system=getattr(serving, "_merge_inline_system", False))
        counts.append(len(oa) - before)
    return counts


def _first_block_offsets(starts: list[int], blocks: list[int], counts: list[int], end: int) -> list[int]:
    """Token offset of each Anthropic message: the start of its first OpenAI message's block; a message that gave
    no OpenAI message (empty) takes the next message's offset."""
    rel, j = [], 0
    for c in counts:
        rel.append(starts[blocks[j]] if c and blocks[j] < len(starts) else None)
        j += c
    nxt = end
    for i in range(len(rel) - 1, -1, -1):
        if rel[i] is None:
            rel[i] = nxt
        nxt = rel[i]
    return rel


async def _render_delta(serving, request, new_msgs: list) -> tuple[list[int], list[int]]:
    """Token ids of `new_msgs` + the generation prompt as they follow a completed assistant turn, and the offset
    of each new message inside them. The stub (user, assistant) is rendered with no system and no tools, so the
    cost is O(new messages)."""
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    oa = [{"role": "user", "content": STUB_U}, {"role": "assistant", "content": STUB_A}]
    counts = _convert_each(serving, new_msgs, oa)
    chat_req = ChatCompletionRequest(model=request.model, messages=oa, max_tokens=1,
                                     chat_template_kwargs=getattr(request, "chat_template_kwargs", None))
    ren = serving.online_renderer
    _, engine_inputs = await ren.preprocess_chat(
        chat_req, chat_req.messages, default_template=ren.chat_template,
        default_template_content_format=ren.chat_template_content_format,
        default_template_kwargs=ren.default_chat_template_kwargs, tool_dicts=None, parser=None)
    ids = list(engine_inputs[0]["prompt_token_ids"])
    ends = [i for i, t in enumerate(ids) if t == tc.IM_END]
    if len(ends) < 2:
        raise RuntimeError(f"stub render has {len(ends)} <|im_end|>")
    delta = ids[ends[1] + 1:]
    starts = [i for i, t in enumerate(delta) if t == tc.IM_START]
    blocks = _blocks_of([m["role"] for m in oa[2:]])
    rel = _first_block_offsets(starts, blocks, counts, starts[-1] if starts else len(delta))
    return delta, rel


def _message_blocks(serving, request) -> tuple[list[int], int, list[int]]:
    """For a full request: per Anthropic message, the block index of its first OpenAI message (system block not
    counted), the number of leading system blocks, and the per-message OpenAI message counts."""
    oa: list = []
    counts = _convert_each(serving, request.messages, oa)
    blocks = _blocks_of([m["role"] for m in oa])
    firsts, j = [], 0
    for c in counts:
        firsts.append(blocks[j] if c else -1)
        j += c
    sys_block = 1 if (request.system or request.tools) else 0
    return firsts, sys_block, counts


def _role_of(tok, block: list[int]) -> str:
    try:
        return tok.decode(block[1:2]).strip()
    except Exception:
        return "?"


def compare_blocks(tok, a: list[int], b: list[int]) -> dict:
    """Split both id lists at <|im_start|>; compare block by block. Assistant blocks may differ (D1 keeps the reply
    as generated); any other differing block is a D1 error."""
    def split(ids):
        starts = [i for i, t in enumerate(ids) if t == tc.IM_START] or [0]
        if starts[0] != 0:
            starts = [0] + starts
        return [ids[s:e] for s, e in zip(starts, starts[1:] + [len(ids)])]
    ba, bb = split(a), split(b)
    rec = {"blocks_d1": len(ba), "blocks_full": len(bb), "tokens_d1": len(a), "tokens_full": len(b),
           "assistant_blocks": 0, "assistant_diff": 0, "assistant_tok_d1": 0, "assistant_tok_full": 0,
           "other_diff": 0, "first_other_diff": None}
    for i, (x, y) in enumerate(zip(ba, bb)):
        role = _role_of(tok, x)
        if role == "assistant":
            rec["assistant_blocks"] += 1
            rec["assistant_tok_d1"] += len(x)
            rec["assistant_tok_full"] += len(y)
            rec["assistant_diff"] += x != y
            if i < len(ba) - 1 and x[-2:] != y[-2:]:          # the turn must close the same way: <|im_end|> "\n"
                rec["boundary_diff"] = rec.get("boundary_diff", 0) + 1
                rec["other_diff"] += 1
                if rec["first_other_diff"] is None:
                    rec["first_other_diff"] = {"block": i, "role": "assistant-end", "d1": tok.decode(x[-4:]),
                                               "full": tok.decode(y[-4:])}
        elif x != y:
            rec["other_diff"] += 1
            if rec["first_other_diff"] is None:
                j = next((n for n, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
                rec["first_other_diff"] = {"block": i, "role": role, "at": j, "d1": tok.decode(x[max(0, j - 8):j + 24]),
                                           "full": tok.decode(y[max(0, j - 8):j + 24])}
    rec["ok"] = rec["other_diff"] == 0 and len(ba) == len(bb)
    return rec


def cut_at(st: ChainState, k: int) -> int:
    """Where the stored ids end when the store keeps messages[:k]. Appending (k = all held) keeps everything; an
    edit at k cuts before message k's block AND before the template's "\n" separator after the previous
    <|im_end|>, because the delta render starts with that separator itself."""
    if k >= len(st.offsets):
        return len(st.ids)
    cut = st.offsets[k]
    if cut >= 2 and st.ids[cut - 1] == tc.NL and st.ids[cut - 2] == tc.IM_END:
        cut -= 1
    return cut


# -- patch 1: create_messages --------------------------------------------------------------------------------------
async def _create_messages(self, request, raw_request=None):
    ctx = CTX.get()
    if ctx is None or ctx.shadow:
        return await _ORIG["create_messages"](self, request, raw_request)
    try:
        st = STORE.get(ctx.chain)
        if st is not None and st.prefetch is not None and not st.prefetch.done():
            st.prefetch.cancel()                          # the real call came before the prefetch fired
            STATS["prefetch_cancelled"] += 1
        if ctx.base is None or st is None:
            ctx.mode = "init" if ctx.init else "full"
            ctx.system, ctx.tools, ctx.tool_choice = request.system, request.tools, request.tool_choice
            ctx.msg_blocks, ctx.sys_block, _ = _message_blocks(self, request)
            ctx.n_msgs = len(request.messages)
        else:
            k = ctx.base
            new = request.messages[k:] if ctx.verify else request.messages
            prefix = st.ids[:cut_at(st, k)]
            ctx.rendering = True
            t = time.perf_counter()
            delta, rel = await _render_delta(self, request, new)
            ctx.render_ms = 1000 * (time.perf_counter() - t)
            ctx.rendering = False
            ctx.prompt_ids = list(prefix) + delta
            ctx.k, ctx.prefix_len, ctx.rel_offsets = k, len(prefix), rel
            ctx.n_msgs = k + len(new)
            ctx.mode = "verify" if ctx.verify else "delta"
            if ctx.verify:
                ctx.rendering = True
                chat_full = self.to_chat_completion_request(
                    request, merge_inline_system=getattr(self, "_merge_inline_system", False))
                res = await self.online_renderer.render_chat(chat_full)
                ctx.rendering = False
                full_ids = list(res[1][0]["prompt_token_ids"])
                ctx.verify_rec = compare_blocks(self.renderer.tokenizer, ctx.prompt_ids, full_ids)
                STATS["verify_ok" if ctx.verify_rec["ok"] else "verify_bad"] += 1
            else:
                request.system, request.tools, request.tool_choice = st.system, st.tools, st.tool_choice
            st.inflight += 1
        STATS[f"calls_{ctx.mode}"] += 1
    except Exception as exc:
        ctx.rendering = False
        _error("create_messages", exc)
        if ctx.base is not None and not ctx.verify:
            from vllm.entrypoints.serve.exception_handling.error_response import create_error_response
            STATS["resync_error"] += 1
            return create_error_response(f"tm_resync: {exc!r}", "tm_resync", HTTPStatus.CONFLICT)
        ctx.prompt_ids, ctx.mode = None, "full_fallback"
    return await _ORIG["create_messages"](self, request, raw_request)


# -- patch 2: the decode-side token reuse hook ----------------------------------------------------------------------
def _reused(request):
    ctx = CTX.get()
    if ctx is not None and ctx.prompt_ids is not None and not ctx.rendering and not ctx.used:
        ctx.used = True
        return ctx.prompt_ids
    return _ORIG["reused"](request)


# -- patch 3: tee the engine's outputs --------------------------------------------------------------------------------
async def _generate(self, prompt, sampling_params, request_id, *args, **kwargs):
    ctx = CTX.get()
    agen = _ORIG["generate"](self, prompt, sampling_params, request_id, *args, **kwargs)
    if ctx is None or ctx.shadow:
        async for out in agen:
            yield out
        return
    from vllm.sampling_params import RequestOutputKind
    delta_kind = getattr(sampling_params, "output_kind", None) == RequestOutputKind.DELTA
    prompt_ids, gen, text, done = None, [], [], False
    try:
        async for out in agen:
            try:
                if prompt_ids is None and getattr(out, "prompt_token_ids", None) is not None:
                    prompt_ids = list(out.prompt_token_ids)
                if out.outputs:
                    c = out.outputs[0]
                    if delta_kind:
                        gen.extend(c.token_ids)
                        text.append(c.text or "")
                    else:
                        gen, text = list(c.token_ids), [c.text or ""]
                    if out.finished and not done:
                        done = True
                        _on_finish(self, ctx, prompt_ids, gen, "".join(text), c.finish_reason, request_id)
            except Exception as exc:
                _error("generate_tee", exc)
            yield out
    finally:
        if not done:
            st = STORE.get(ctx.chain)
            if st is not None and ctx.mode in ("delta", "verify"):
                st.inflight = max(0, st.inflight - 1)


def next_state(ctx: Ctx, st: ChainState | None, prompt_ids: list, gen: list) -> tuple[list, list]:
    """(ids, offsets) of the chain after a call: prompt + reply exactly as generated (closed with <|im_end|> if a
    length cap left it open); one offset per Anthropic message, the reply's at the generation prompt."""
    ids = list(prompt_ids) + list(gen)
    if not gen or gen[-1] != tc.IM_END:
        ids.append(tc.IM_END)
    starts = [i for i, t in enumerate(prompt_ids) if t == tc.IM_START]
    gen_start = starts[-1] if starts else len(prompt_ids)
    if ctx.mode in ("delta", "verify"):
        offsets = st.offsets[:ctx.k] + [ctx.prefix_len + r for r in ctx.rel_offsets] + [gen_start]
    else:
        offsets = [starts[ctx.sys_block + b] if b >= 0 and ctx.sys_block + b < len(starts) else gen_start
                   for b in (ctx.msg_blocks or [])] + [gen_start]
    return ids, offsets


def _on_finish(engine, ctx: Ctx, prompt_ids, gen, text: str, reason, request_id) -> None:
    now = time.time()
    if prompt_ids is None:
        prompt_ids = ctx.prompt_ids or []
    if ctx.mode in ("delta", "verify"):
        st = STORE[ctx.chain]
        st.ids, st.offsets = next_state(ctx, st, prompt_ids, gen)
        st.epoch, st.inflight = st.epoch + 1, max(0, st.inflight - 1)
        mismatch = ctx.prompt_ids is not None and list(prompt_ids) != ctx.prompt_ids
    else:
        old = STORE.get(ctx.chain)
        ids, offsets = next_state(ctx, None, prompt_ids, gen)
        st = STORE[ctx.chain] = ChainState(ids, offsets, ctx.system, ctx.tools, ctx.tool_choice,
                                           epoch=(old.epoch + 1) if old else 0, session=ctx.session)
        mismatch = False
    ids = st.ids
    st.t_fin = now
    st.session = ctx.session
    calls = tc.tool_calls_in(text)
    keys = [tc.tool_key(n, a) for n, a in calls]
    p50, p90, src = tc.predict_reply(keys, THINK_S)
    rec = {"ev": "call", "rid": ctx.rid, "chain": ctx.chain, "mode": ctx.mode, "prompt": len(prompt_ids),
           "gen": len(gen), "reason": str(reason), "k": ctx.k if ctx.mode in ("delta", "verify") else None,
           "prefix": ctx.prefix_len, "render_ms": round(ctx.render_ms, 3),
           "server_ms": round(1000 * (time.perf_counter() - ctx.t0), 1) if ctx.t0 else None,
           "keys": keys, "pred_p50": p50, "pred_p90": p90, "pred_src": src, "ids_mismatch": mismatch,
           "n_msgs": len(st.offsets)}
    if ctx.verify_rec is not None:
        rec["verify"] = ctx.verify_rec
    _log(rec)
    hint = {"s": ctx.session, "chain": ctx.chain, "ev": "tool_start" if keys else "turn_end", "keys": keys,
            "p50": p50, "p90": p90, "n_tok": len(ids), "t": now}
    BUS.send(hint)
    if PREFETCH_ON and keys and p50 >= PREFETCH_MIN_S:
        lead = len(ids) / RESTORE_TOK_S + 0.05
        delay = max(0.0, p50 - lead)
        try:
            loop = asyncio.get_running_loop()
            st.prefetch = loop.create_task(_prefetch(engine, ctx.chain, ctx.session, st.epoch, delay, now))
            STATS["prefetch_scheduled"] += 1
        except RuntimeError as exc:
            _error("prefetch_schedule", exc)


async def _prefetch(engine, chain: str, session: str, epoch: int, delay: float, t_hint: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        _log({"ev": "prefetch", "chain": chain, "state": "cancelled", "delay": round(delay, 3)})
        raise
    st = STORE.get(chain)
    if st is None or st.epoch != epoch or st.inflight:
        STATS["prefetch_stale"] += 1
        _log({"ev": "prefetch", "chain": chain, "state": "stale"})
        return
    from vllm.sampling_params import SamplingParams
    ids = list(st.ids)
    rid = f"tmpf-{chain}-{epoch}"
    token = CTX.set(Ctx(chain=chain, session=session, rid=rid, shadow=True, mode="shadow"))
    t0 = time.perf_counter()
    try:
        async for _ in engine.generate({"prompt_token_ids": ids}, SamplingParams(max_tokens=1, temperature=0.0),
                                       rid, priority=PREFETCH_PRIORITY, session_id=session):
            pass
        STATS["prefetch_done"] += 1
        _log({"ev": "prefetch", "chain": chain, "state": "done", "n_tok": len(ids), "delay": round(delay, 3),
              "ms": round(1000 * (time.perf_counter() - t0), 1), "since_hint_s": round(time.time() - t_hint, 3)})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _error("prefetch", exc)
    finally:
        CTX.reset(token)


# -- install and the ASGI middleware ----------------------------------------------------------------------------------
def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from vllm.entrypoints.anthropic import serving as aserv
    from vllm.renderers import online_renderer as orr
    from vllm.v1.engine.async_llm import AsyncLLM
    _ORIG["create_messages"] = aserv.AnthropicServingMessages.create_messages
    aserv.AnthropicServingMessages.create_messages = _create_messages
    _ORIG["reused"] = orr._reused_prompt_token_ids
    orr._reused_prompt_token_ids = _reused
    _ORIG["generate"] = AsyncLLM.generate
    AsyncLLM.generate = _generate
    _INSTALLED = True
    _log({"ev": "installed", "d1": D1_ON, "prefetch": PREFETCH_ON, "hints": HINTS_ADDR, "think_s": THINK_S,
          "restore_tok_s": RESTORE_TOK_S, "pid": os.getpid()})


async def _send_json(send, status: int, obj) -> None:
    body = json.dumps(obj, default=str).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            return b"".join(chunks)


class Middleware:
    """Pure ASGI middleware (no BaseHTTPMiddleware): sets the request's Ctx in a contextvar that the patches read,
    answers 409 for a D1 base the store cannot serve, and serves /tm/hint and /tm/stats."""

    def __init__(self, app) -> None:
        self.app = app
        install()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith("/tm/"):
            return await self._tm(scope, receive, send, path)
        if path != "/v1/messages" or scope.get("method") != "POST" or not D1_ON:
            return await self.app(scope, receive, send)
        h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        sess, rid = h.get("x-session-id"), h.get("x-request-id")
        if not sess:
            return await self.app(scope, receive, send)
        base = h.get("x-tm-base")
        ctx = Ctx(chain=tc.chain_of(rid, sess), session=sess, rid=rid, init=h.get("x-tm-init") == "1",
                  base=int(base) if base not in (None, "") else None, verify=h.get("x-tm-verify") == "1",
                  t0=time.perf_counter())
        if ctx.base is not None:
            st = STORE.get(ctx.chain)
            if st is None or ctx.base > len(st.offsets) or ctx.base < 1 or st.inflight:
                STATS["resync_409"] += 1
                _log({"ev": "resync", "chain": ctx.chain, "rid": rid, "base": ctx.base,
                      "held": len(st.offsets) if st else None, "inflight": st.inflight if st else None})
                return await _send_json(send, 409, {"type": "error", "error": {
                    "type": "tm_resync", "message": "session state unavailable; resend the full request"}})
        token = CTX.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            CTX.reset(token)

    async def _tm(self, scope, receive, send, path: str) -> None:
        if path == "/tm/stats":
            return await _send_json(send, 200, {"stats": dict(STATS), "chains": len(STORE), "d1": D1_ON,
                                                "prefetch": PREFETCH_ON, "hints": HINTS_ADDR})
        if path == "/tm/hint" and scope.get("method") == "POST":
            try:
                msg = json.loads(await _read_body(receive) or b"{}")
                msg.setdefault("t", time.time())
                BUS.send(msg)
                _log({"ev": "hint_in", **msg})
                return await _send_json(send, 200, {"ok": True})
            except Exception as exc:
                return await _send_json(send, 400, {"ok": False, "error": repr(exc)})
        return await _send_json(send, 404, {"error": "unknown /tm route"})
