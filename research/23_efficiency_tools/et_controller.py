"""Topic 23's agate controller: once a minute, one model call chooses K, the session gate's limit.

Runs inside et_proxy.py (it owns the gate). Each round it reads et_hw.py's /hw/summary (the last 60 s and the previous
5 minutes) and the gate's state, renders et_prompts' variant into two messages and sends them straight to vLLM as an
OpenAI chat request with one tool, set_session_limit(k, reason), under the session "controller" (never gated). A valid
call sets K, clamped to 1..n_max; anything else leaves K as it was. Every round is logged to
<out>/controller.jsonl: the prompt's hash and text, the reply, K before and after, latency and usage. K starts at the
proxy's --gate-k (32: no limit in practice), so until the first call `agate` runs like `none`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from aiohttp import ClientTimeout

import et_prompts

CALL_TIMEOUT_S = 300.0
MAX_TOKENS = 8192


def parse_call(reply: dict, n_max: int) -> tuple[int | None, str]:
    """(K, reason) from an OpenAI chat reply's first set_session_limit call; (None, why) when there is none."""
    try:
        msg = reply["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, "no message"
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        if fn.get("name") != "set_session_limit":
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
            k = int(args["k"])
        except (ValueError, KeyError, TypeError):
            return None, "bad arguments"
        return max(1, min(k, n_max)), str(args.get("reason") or "")
    return None, "no set_session_limit call"


class Controller:
    def __init__(self, args, gate, session):
        self.args, self.gate, self.session = args, gate, session
        self.log = Path(args.out) / "controller.jsonl"
        self.history: list[dict] = []

    async def summary(self) -> dict:
        if not self.args.hw_url:
            return {}
        try:
            async with self.session.get(f"{self.args.hw_url.rstrip('/')}/hw/summary?window=60&minutes=5",
                                        timeout=ClientTimeout(total=10)) as r:
                return await r.json()
        except Exception:                                         # noqa: BLE001
            return {}

    async def decide(self) -> dict:
        summary = await self.summary()
        gate = self.gate.state()
        if self.history and "after" not in self.history[-1]:
            self.history[-1]["after"] = summary.get("now") or {}
        msgs = et_prompts.messages(summary, gate, self.history, self.args.controller, self.args.n_max)
        body = {"model": self.args.model, "messages": msgs, "tools": [et_prompts.tool(self.args.n_max)],
                "tool_choice": self.args.controller_tool_choice, "max_tokens": MAX_TOKENS}
        if self.args.controller_effort != "default":
            body["chat_template_kwargs"] = {"reasoning_effort": self.args.controller_effort}
        t0 = time.time()
        rec = {"t": t0, "variant": self.args.controller, "effort": self.args.controller_effort, "k_before": gate["k"],
               "prompt_sha": hashlib.sha256(json.dumps(msgs).encode()).hexdigest()[:16], "user": msgs[1]["content"]}
        try:
            async with self.session.post(f"{self.args.upstream}/v1/chat/completions", json=body,
                                         headers={"X-Session-ID": "controller"},
                                         timeout=ClientTimeout(total=CALL_TIMEOUT_S)) as r:
                reply = await r.json(content_type=None)
            k, reason = parse_call(reply, self.args.n_max)
            msg = ((reply.get("choices") or [{}])[0].get("message") or {})
            rec.update(status=r.status, usage=reply.get("usage"), reply=(msg.get("content") or "")[:2000],
                       reasoning=(msg.get("reasoning_content") or msg.get("reasoning") or "")[:4000],
                       tool_calls=msg.get("tool_calls"))
        except Exception as exc:                                  # noqa: BLE001
            k, reason = None, f"{type(exc).__name__}: {exc}"[:300]
        rec.update(s=round(time.time() - t0, 2), k=k, reason=reason)
        if k is not None:
            change = self.gate.set_k(k, by="controller", why=reason)
            self.history.append({"old": change["old"], "new": change["new"], "reason": reason, "t": t0})
        rec["k_after"] = self.gate.k
        with self.log.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    async def loop(self) -> None:
        """A round every controller_interval seconds, counted from the previous round's start; a round longer than
        the interval is followed at once."""
        start = time.time()
        while True:
            await asyncio.sleep(max(0.0, start + self.args.controller_interval - time.time()))
            start = time.time()
            try:
                await self.decide()
            except asyncio.CancelledError:
                raise
            except Exception:                                     # noqa: BLE001
                pass
