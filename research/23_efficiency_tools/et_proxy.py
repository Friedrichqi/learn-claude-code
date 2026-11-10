#!/usr/bin/env python3
"""Topic 23's gate proxy: an asyncio reverse proxy between the s15 agents and vLLM, in every setup (so every run has
the same hop). It does three things.

- **Session gate** (gate8, gate16, agate; off elsewhere): topic 11's rule (mu_scheduler MU_GATE) moved in front of the
  server. At most K sessions are active; a session is the X-Session-ID header (et_child.py sends the trial name). A
  request of an inactive session is held until the session gets a slot: sessions get slots first come, first served by
  when their first request started waiting, and an active session frees its slot once it has had nothing in flight for
  IDLE_S (5 s). Lowering K never takes a slot away; it stops new admissions until enough sessions leave. Unlike topic
  11's, K can change at run time (POST /gate/k: the agate controller, et_controller.py). Each session's held seconds are
  served at GET /gate/session/<id> (et_agent.py leaves them out of the task's time limit).
- **KV-offload store policy** (lmc, lmc_all): `flagged` adds the no-store switch to every request's kv_transfer_params
  except the ones save_context sends (header X-KV-Store: 1), so only those are stored; `all` stores everything (no
  switch); `off` (no offload connector) leaves the body alone. The switch is LMCache's lmcache.max_offload_tokens: 0, or
  vLLM's native max_offload_tokens: 0 (--no-store-key); neither is part of LMCache's cache key (lmcache/utils.py
  CacheEngineKey keys on lmcache.tag.* only), so a stored prefix is found by the requests that did not store it.
- **Per-call server cap**: a request may spend at most CALL_CAP_S (1,800 s, topic 22's call_timeout) at vLLM after
  admission; then the proxy answers 504. The agents' own call timeout is raised (et_cell.py) so time held at the gate
  never counts toward it.

X-Session-ID and X-KV-Store are not forwarded (vLLM's scheduler ignores the session id anyway). Requests of the session
"controller" (or with X-ET-Bypass: 1) bypass the gate. Logs: <out>/proxy_requests.jsonl (one line per request) and
<out>/gate_events.jsonl (slots, K changes).

    python research/23_efficiency_tools/et_proxy.py --upstream http://127.0.0.1:31431 --port 31441 --out <run dir> \
        [--gate-k 8] [--store-policy flagged --no-store-key lmcache.max_offload_tokens] [--controller c2 ...]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

IDLE_S = 5.0
CALL_CAP_S = 1800.0
TICK_S = 0.25
MODEL_PATHS = ("/v1/messages", "/v1/chat/completions", "/v1/completions")
DROP_HEADERS = {"host", "content-length", "x-session-id", "x-kv-store", "x-et-bypass", "transfer-encoding",
                "connection", "accept-encoding"}


class JsonLog:
    def __init__(self, path: Path):
        self.f = path.open("a", buffering=1)

    def write(self, rec: dict) -> None:
        self.f.write(json.dumps(rec) + "\n")


class Gate:
    """At most k active sessions (k None: no gate). Sessions wait first come, first served."""

    def __init__(self, k: int | None, n_max: int, log: JsonLog, clock=time.time, idle_s: float = IDLE_S):
        self.k, self.n_max, self.log, self.clock, self.idle_s = k, n_max, log, clock, idle_s
        self.active: dict[str, dict] = {}                       # sid -> since, inflight, last_done
        self.waiting: "collections.OrderedDict[str, dict]" = collections.OrderedDict()  # sid -> since, event
        self.blocked: dict[str, float] = collections.defaultdict(float)
        self.context: dict[str, int] = {}                       # sid -> prompt tokens of its last finished request
        self.k_changes: list[dict] = []

    def enabled(self) -> bool:
        return self.k is not None

    def _activate(self, sid: str, now: float) -> None:
        w = self.waiting.pop(sid, None)
        waited = now - w["since"] if w else 0.0
        self.blocked[sid] += waited
        self.active[sid] = {"since": now, "inflight": 0, "last_done": now}
        self.log.write({"t": now, "k": "gate", "s": sid, "on": 1, "active": len(self.active), "waited": round(waited, 3)})
        if w:
            w["event"].set()

    def _admit_waiting(self, now: float) -> None:
        while self.waiting and (self.k is None or len(self.active) < self.k):
            self._activate(next(iter(self.waiting)), now)

    async def acquire(self, sid: str) -> None:
        """Return once the session holds a slot, counting its request in flight."""
        now = self.clock()
        if self.k is None or sid in self.active:
            self.active.setdefault(sid, {"since": now, "inflight": 0, "last_done": now})["inflight"] += 1
            return
        if not self.waiting and len(self.active) < self.k:
            self._activate(sid, now)
        else:
            w = self.waiting.setdefault(sid, {"since": now, "event": asyncio.Event()})
            await w["event"].wait()
        self.active[sid]["inflight"] += 1

    def _deactivate(self, sid: str, now: float, why: str) -> None:
        a = self.active.pop(sid)
        self.log.write({"t": now, "k": "gate", "s": sid, "on": 0, "active": len(self.active),
                        "held_s": round(now - a["since"], 3), "why": why})

    def release(self, sid: str, prompt_tokens: int | None) -> None:
        """A request finished. While more sessions hold slots than K allows (K was lowered), a session gives its slot
        up as soon as it has nothing in flight; otherwise it keeps it until idle_s idle (tick). No request is cut."""
        a = self.active.get(sid)
        now = self.clock()
        if a is not None:
            a["inflight"] = max(0, a["inflight"] - 1)
            a["last_done"] = now
            if self.k is not None and a["inflight"] == 0 and len(self.active) > self.k:
                self._deactivate(sid, now, "over")
        if prompt_tokens:
            self.context[sid] = int(prompt_tokens)

    def tick(self) -> None:
        if self.k is None:
            return
        now = self.clock()
        for sid, a in list(self.active.items()):
            if a["inflight"] == 0 and now - a["last_done"] >= self.idle_s:
                self._deactivate(sid, now, "idle")
        self._admit_waiting(now)

    def set_k(self, k: int, by: str, why: str = "") -> dict:
        now = self.clock()
        old = self.k
        self.k = max(1, min(int(k), self.n_max))
        rec = {"t": now, "k": "k", "old": old, "new": self.k, "by": by, "why": why[:500]}
        self.k_changes.append(rec)
        self.log.write(rec)
        self._admit_waiting(now)
        return rec

    def session(self, sid: str) -> dict:
        now = self.clock()
        held = self.blocked.get(sid, 0.0)
        if sid in self.waiting:
            held += now - self.waiting[sid]["since"]
        return {"session": sid, "active": sid in self.active, "waiting": sid in self.waiting, "blocked_s": round(held, 3),
                "context_tokens": self.context.get(sid)}

    def state(self) -> dict:
        now = self.clock()
        idle = [s for s, a in self.active.items() if a["inflight"] == 0]
        return {"t": now, "k": self.k, "n_max": self.n_max, "active": len(self.active), "waiting": len(self.waiting),
                "active_idle": len(idle), "active_inflight": sum(a["inflight"] for a in self.active.values()),
                "active_context_tokens": sum(self.context.get(s, 0) for s in self.active),
                "next_waiting_context_tokens": next((self.context.get(s, 0) for s in self.waiting), 0),
                "oldest_wait_s": round(max((now - w["since"] for w in self.waiting.values()), default=0.0), 1),
                "sessions_seen": len(self.context), "k_changes": len(self.k_changes)}


def usage_tokens(body: bytes) -> tuple[int | None, int | None, int | None]:
    """(prompt tokens, cached prompt tokens, output tokens) of an Anthropic or OpenAI reply, when it has them."""
    try:
        u = json.loads(body).get("usage") or {}
    except (ValueError, AttributeError):
        return None, None, None
    if "input_tokens" in u:
        cached = int(u.get("cache_read_input_tokens") or 0)
        prompt = int(u.get("input_tokens") or 0) + cached + int(u.get("cache_creation_input_tokens") or 0)
        return prompt, cached, int(u.get("output_tokens") or 0)
    if "prompt_tokens" in u:
        cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        return int(u.get("prompt_tokens") or 0), cached, int(u.get("completion_tokens") or 0)
    return None, None, None


class Proxy:
    def __init__(self, args):
        self.args = args
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        self.req_log = JsonLog(out / "proxy_requests.jsonl")
        self.gate = Gate(args.gate_k, args.n_max, JsonLog(out / "gate_events.jsonl"))
        self.session: ClientSession | None = None
        self.controller = None

    def body_for(self, body: bytes, path: str, store: bool) -> bytes:
        policy = self.args.store_policy
        if policy == "off" or path not in MODEL_PATHS or not body:
            return body
        try:
            obj = json.loads(body)
        except ValueError:
            return body
        kvp = dict(obj.get("kv_transfer_params") or {})
        if policy == "flagged" and not store:
            kvp[self.args.no_store_key] = 0
        else:
            kvp.pop(self.args.no_store_key, None)
        if kvp:
            obj["kv_transfer_params"] = kvp
        else:
            obj.pop("kv_transfer_params", None)
        return json.dumps(obj).encode()

    async def forward(self, request: web.Request) -> web.Response:
        t_in = time.time()
        path = request.path
        sid = request.headers.get("X-Session-ID") or "anon"
        store = request.headers.get("X-KV-Store") == "1"
        bypass = sid == "controller" or request.headers.get("X-ET-Bypass") == "1"
        gated = path in MODEL_PATHS and request.method == "POST" and not bypass and self.gate.enabled()
        body = await request.read()
        if path in MODEL_PATHS:
            body = self.body_for(body, path, store)
        if gated:
            await self.gate.acquire(sid)
        t_admit = time.time()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_HEADERS}
        status, out, ctype, err = 502, b"", "application/json", None
        try:
            async with self.session.request(request.method, self.args.upstream + request.path_qs, data=body or None,
                                            headers=headers, timeout=ClientTimeout(total=CALL_CAP_S)) as r:
                out = await r.read()
                status, ctype = r.status, r.headers.get("Content-Type", "application/json")
        except asyncio.TimeoutError:
            status, err = 504, f"call exceeded {CALL_CAP_S:.0f} s at the server"
            out = json.dumps({"error": {"type": "timeout", "message": err}}).encode()
        except Exception as exc:                                  # noqa: BLE001
            status, err = 502, f"{type(exc).__name__}: {exc}"[:300]
            out = json.dumps({"error": {"type": "proxy", "message": err}}).encode()
        finally:
            prompt = cached = gen = None
            if path in MODEL_PATHS and status == 200:
                prompt, cached, gen = usage_tokens(out)
            if gated:
                self.gate.release(sid, prompt)
            elif prompt:
                self.gate.context[sid] = prompt
        if path in MODEL_PATHS:
            self.req_log.write({"t_in": round(t_in, 3), "t_admit": round(t_admit, 3), "t_done": round(time.time(), 3),
                                "s": sid, "path": path, "status": status, "store": store, "bypass": bypass,
                                "gated": gated, "prompt": prompt, "cached": cached, "out": gen, "err": err})
        return web.Response(status=status, body=out, content_type=ctype.split(";")[0] if ctype else None)

    async def gate_state(self, request: web.Request) -> web.Response:
        return web.json_response(self.gate.state())

    async def gate_session(self, request: web.Request) -> web.Response:
        return web.json_response(self.gate.session(request.match_info["sid"]))

    async def gate_k(self, request: web.Request) -> web.Response:
        if not self.gate.enabled():
            return web.json_response({"error": "the gate is off"}, status=409)
        d = await request.json()
        return web.json_response(self.gate.set_k(int(d["k"]), str(d.get("by") or "http"), str(d.get("why") or "")))

    async def ticker(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            self.gate.tick()

    async def schedule(self, steps: list[tuple[float, int]]) -> None:
        """The pilot's K sweep: set K at fixed times after the proxy started."""
        t0 = time.time()
        for at, k in sorted(steps):
            await asyncio.sleep(max(0.0, t0 + at - time.time()))
            self.gate.set_k(k, by="schedule", why=f"pilot sweep at {at:.0f} s")

    async def start(self, app: web.Application) -> None:
        self.session = ClientSession(connector=TCPConnector(limit=0, keepalive_timeout=60))
        app["ticker"] = asyncio.create_task(self.ticker())
        if self.args.k_schedule:
            steps = [(float(at), int(k)) for at, k in (s.split(":") for s in self.args.k_schedule.split(","))]
            app["schedule"] = asyncio.create_task(self.schedule(steps))
        if self.args.controller:
            import et_controller
            self.controller = et_controller.Controller(self.args, self.gate, self.session)
            app["controller"] = asyncio.create_task(self.controller.loop())

    async def stop(self, app: web.Application) -> None:
        for key in ("ticker", "controller", "schedule"):
            if key in app:
                app[key].cancel()
        if self.session is not None:
            await self.session.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gate-k", type=int, default=None, help="session gate with this K (default: off)")
    ap.add_argument("--n-max", type=int, default=32, help="the largest K the controller may set")
    ap.add_argument("--k-schedule", default=None, help="'seconds:K,...' after start (the pilot's sweep)")
    ap.add_argument("--store-policy", choices=("off", "all", "flagged"), default="off")
    ap.add_argument("--no-store-key", default="lmcache.max_offload_tokens")
    ap.add_argument("--controller", default=None, help="agate: the controller prompt variant (et_prompts.py)")
    ap.add_argument("--controller-effort", default="default", choices=("default", "medium", "low"))
    ap.add_argument("--controller-interval", type=float, default=60.0)
    ap.add_argument("--controller-tool-choice", default="required", choices=("required", "auto"))
    ap.add_argument("--hw-url", default=None, help="et_hw.py's summary server, for the controller")
    ap.add_argument("--model", default="Qwen/Qwen3.8-27B-FP8")
    a = ap.parse_args()
    a.upstream = a.upstream.rstrip("/")
    proxy = Proxy(a)
    app = web.Application(client_max_size=1 << 30)
    app.router.add_get("/gate/state", proxy.gate_state)
    app.router.add_get("/gate/session/{sid}", proxy.gate_session)
    app.router.add_post("/gate/k", proxy.gate_k)
    app.router.add_route("*", "/{tail:.*}", proxy.forward)
    app.on_startup.append(proxy.start)
    app.on_cleanup.append(proxy.stop)
    web.run_app(app, host="127.0.0.1", port=a.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
