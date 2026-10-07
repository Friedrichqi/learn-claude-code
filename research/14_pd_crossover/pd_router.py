#!/usr/bin/env python3
"""Router in front of every arm of research/14_pd_crossover (PLAN.md §4). All arms pay its hop.

    python research/14_pd_crossover/pd_router.py --port P --mode dp   --backends URL0,URL1[,...] [--idle-s 60]
    python research/14_pd_crossover/pd_router.py --port P --mode pd   --prefill URLP0[,...] --decode URLD0[,...]
    python research/14_pd_crossover/pd_router.py --port P --mode pass --backends URL
Placement: a session (X-Session-ID, else X-Request-Id) is placed at its first call on the backend with the fewest
active sessions (ties: lowest index) and never moves; in pd mode the prefill and the decode instance are chosen that
way independently. A session stops counting as active at POST /tm/hint {"ev": "session_end", "s": ...} (13's client
hint) or after --idle-s seconds with no call in flight and no activity.
pd call (vLLM NixlConnector, pull mode): the prefill instance gets the body with max_tokens 1, stream false and
kv_transfer_params {do_remote_decode: true}; the decode instance then gets the original body plus the
kv_transfer_params the prefill instance returned (same X-Request-Id on both). No params back -> the decode instance
computes the prompt itself (counted). /v1/messages (live harness) and /v1/completions (replay) are both handled.
Other routes:
  GET /health          200 only if every backend's /health is 200 (cached 5 s)
  GET /metrics         vllm:generation_tokens_total, vllm:num_requests_running, vllm:num_requests_waiting summed over
                       backends (what 11's mu_sweep watchdog reads); GET /metrics/<i> passes backend i's text through
  GET /router/stats    per-backend calls, active sessions, errors, P / D milliseconds
  POST /reset_prefix_cache   forwarded to every backend; 200 only if all succeeded
--log PATH: one JSON line per call (wall-clock and monotonic stamps at each hop).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

from pd_proxy import FWD, P_PARAMS  # noqa: E402  research/13_tiered_memory

WATCHED = ("vllm:generation_tokens_total", "vllm:num_requests_running", "vllm:num_requests_waiting")
POST_PATHS = ("/v1/messages", "/v1/completions", "/v1/chat/completions")
ROUTER_KEY = web.AppKey("router", object)


class Pool:
    """Least-active, sticky session placement over one list of backends."""

    def __init__(self, urls: list[str], idle_s: float) -> None:
        self.urls = urls
        self.idle_s = idle_s
        self.home: dict[str, int] = {}               # session -> backend index (forever)
        self.active: dict[str, int] = {}              # session -> backend index while active
        self.inflight: Counter = Counter()            # session -> calls in flight
        self.last: dict[str, float] = {}              # session -> last activity (monotonic)

    def counts(self) -> list[int]:
        c = [0] * len(self.urls)
        for i in self.active.values():
            c[i] += 1
        return c

    def expire(self, now: float) -> None:
        for s in [s for s, t in self.last.items()
                  if s in self.active and not self.inflight[s] and now - t > self.idle_s]:
            self.active.pop(s, None)

    def pick(self, sess: str, now: float) -> int:
        self.expire(now)
        i = self.home.get(sess)
        if i is None:
            c = self.counts()
            i = min(range(len(c)), key=lambda k: (c[k], k))
            self.home[sess] = i
        self.active[sess] = i
        self.last[sess] = now
        return i

    def begin(self, sess: str) -> None:
        self.inflight[sess] += 1

    def end(self, sess: str, now: float) -> None:
        self.inflight[sess] = max(0, self.inflight[sess] - 1)
        self.last[sess] = now

    def release(self, sess: str) -> None:
        self.active.pop(sess, None)


class Router:
    def __init__(self, mode: str, backends: list[str], prefill: list[str], decode: list[str], idle_s: float,
                 log: str | None) -> None:
        self.mode, self.log_path = mode, log
        strip = lambda xs: [x.rstrip("/") for x in xs if x]  # noqa: E731
        if mode == "pd":
            self.p = Pool(strip(prefill), idle_s)
            self.d = Pool(strip(decode), idle_s)
            self.all = self.d.urls + self.p.urls
        else:
            self.c = Pool(strip(backends), idle_s)
            self.all = self.c.urls
        if not self.all:
            raise SystemExit("no backends")
        self.stats: Counter = Counter()
        self.ms: Counter = Counter()
        self.http: ClientSession | None = None
        self.health_cache = (0.0, False)
        self.logf = open(log, "a", encoding="utf-8") if log else None

    async def start(self, app) -> None:
        self.http = ClientSession(timeout=ClientTimeout(total=3600),
                                  connector=TCPConnector(limit=0, limit_per_host=0))

    async def stop(self, app) -> None:
        if self.http is not None:
            await self.http.close()
        if self.logf:
            self.logf.close()

    def _write(self, rec: dict) -> None:
        if self.logf:
            self.logf.write(json.dumps(rec) + "\n")
            self.logf.flush()

    async def _post(self, url: str, body: dict, headers: dict) -> tuple[int, bytes, str]:
        async with self.http.post(url, json=body, headers=headers) as r:
            return r.status, await r.read(), r.headers.get("Content-Type", "application/json")

    # -- calls ------------------------------------------------------------------------------------------------
    async def call(self, req: web.Request) -> web.Response:
        t_in, w_in = time.monotonic(), time.time()
        body = await req.json()
        h = {k: v for k, v in req.headers.items() if k.lower() in FWD}
        sess = req.headers.get("X-Session-ID") or req.headers.get("X-Request-Id") or "-"
        rec = {"w_in": w_in, "t_in": t_in, "session": sess, "rid": req.headers.get("X-Request-Id"),
               "path": req.path, "mode": self.mode}
        pools = [self.p, self.d] if self.mode == "pd" else [self.c]
        try:
            if self.mode == "pd":
                ip, idd = self.p.pick(sess, t_in), self.d.pick(sess, t_in)
                for pool in pools:
                    pool.begin(sess)
                rec.update(p=ip, d=idd)
                pbody = dict(body, max_tokens=1, stream=False, kv_transfer_params=dict(P_PARAMS))
                if "max_completion_tokens" in pbody:
                    pbody["max_completion_tokens"] = 1
                rec["t_p0"] = time.monotonic()
                st, raw, ctype = await self._post(self.p.urls[ip] + req.path, pbody, h)
                rec["t_p1"] = time.monotonic()
                rec["p_status"] = st
                if st != 200:
                    self.stats["p_errors"] += 1
                    rec.update(status=st, t_out=time.monotonic())
                    self._write(rec)
                    return web.Response(status=st, body=raw, content_type=ctype.split(";")[0])
                kv = json.loads(raw).get("kv_transfer_params")
                rec["kv_blocks"] = sum(len(g) if isinstance(g, list) else 1
                                       for g in (kv or {}).get("remote_block_ids") or [])
                if not kv:
                    self.stats["p_no_params"] += 1
                dbody = dict(body, kv_transfer_params=kv) if kv else dict(body)
                rec["t_d0"] = time.monotonic()
                st, raw, ctype = await self._post(self.d.urls[idd] + req.path, dbody, h)
                rec["t_d1"] = time.monotonic()
                self.stats[f"p{ip}"] += 1
                self.stats[f"d{idd}"] += 1
                self.ms["p"] += 1000 * (rec["t_p1"] - rec["t_p0"])
                self.ms["d"] += 1000 * (rec["t_d1"] - rec["t_d0"])
            else:
                i = self.c.pick(sess, t_in)
                self.c.begin(sess)
                rec["b"] = i
                rec["t_d0"] = time.monotonic()
                st, raw, ctype = await self._post(self.c.urls[i] + req.path, body, h)
                rec["t_d1"] = time.monotonic()
                self.stats[f"b{i}"] += 1
            if st != 200:
                self.stats["errors"] += 1
            rec.update(status=st, t_out=time.monotonic())
            self._write(rec)
            return web.Response(status=st, body=raw, content_type=ctype.split(";")[0])
        except Exception as exc:
            self.stats["errors"] += 1
            rec.update(status=502, error=repr(exc)[:300], t_out=time.monotonic())
            self._write(rec)
            return web.json_response({"type": "error", "error": {"type": "router_error", "message": repr(exc)}},
                                     status=502)
        finally:
            now = time.monotonic()
            for pool in pools:
                if sess in pool.inflight:
                    pool.end(sess, now)

    async def hint(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            body = {}
        if body.get("ev") == "session_end" and body.get("s"):
            for pool in ([self.p, self.d] if self.mode == "pd" else [self.c]):
                pool.release(body["s"])
            self.stats["session_end"] += 1
        return web.json_response({"ok": True})

    # -- control routes ---------------------------------------------------------------------------------------
    async def _get(self, url: str, timeout: float = 10.0) -> tuple[int, str]:
        async with self.http.get(url, timeout=ClientTimeout(total=timeout)) as r:
            return r.status, await r.text()

    async def health(self, req: web.Request) -> web.Response:
        now = time.monotonic()
        if now - self.health_cache[0] > 5:
            try:
                res = await asyncio.gather(*(self._get(u + "/health", 10) for u in self.all), return_exceptions=True)
                ok = all(not isinstance(r, Exception) and r[0] == 200 for r in res)
            except Exception:
                ok = False
            self.health_cache = (now, ok)
        return web.Response(status=200 if self.health_cache[1] else 503, text="")

    async def metrics(self, req: web.Request) -> web.Response:
        idx = req.match_info.get("idx")
        if idx is not None:
            st, text = await self._get(self.all[int(idx)] + "/metrics", 20)
            return web.Response(status=st, text=text, content_type="text/plain")
        sums = dict.fromkeys(WATCHED, 0.0)
        res = await asyncio.gather(*(self._get(u + "/metrics", 20) for u in self.all), return_exceptions=True)
        for r in res:
            if isinstance(r, Exception) or r[0] != 200:
                return web.Response(status=503, text="backend metrics unavailable")
            for line in r[1].splitlines():
                if not line or line[0] == "#":
                    continue
                name = line.split("{", 1)[0].split(" ", 1)[0]
                if name in sums:
                    try:
                        sums[name] += float(line.rsplit(" ", 1)[1])
                    except ValueError:
                        pass
        return web.Response(text="".join(f"{k} {v}\n" for k, v in sums.items()), content_type="text/plain")

    async def reset(self, req: web.Request) -> web.Response:
        res = []
        for u in self.all:
            try:
                async with self.http.post(u + req.path_qs) as r:
                    res.append((r.status, await r.text()))
            except Exception as exc:
                res.append((599, repr(exc)))
        ok = all(st == 200 and '"success"' in t.replace(" ", "") and "true" in t for st, t in res)
        return web.json_response({"success": ok, "backends": [{"status": s, "text": t[:200]} for s, t in res]},
                                 status=200 if ok else 500)

    async def router_stats(self, req: web.Request) -> web.Response:
        pools = {"p": self.p, "d": self.d} if self.mode == "pd" else {"c": self.c}
        return web.json_response({"mode": self.mode, "stats": dict(self.stats), "ms": dict(self.ms),
                                  "pools": {k: {"urls": p.urls, "active": p.counts(), "sessions": len(p.home)}
                                            for k, p in pools.items()}})

    async def other(self, req: web.Request) -> web.StreamResponse:
        url = self.all[0] + req.path_qs
        data = await req.read()
        async with self.http.request(req.method, url, data=data or None,
                                     headers={k: v for k, v in req.headers.items() if k.lower() in FWD}) as r:
            return web.Response(status=r.status, body=await r.read(),
                                content_type=r.headers.get("Content-Type", "text/plain").split(";")[0])


def make_app(mode: str, backends=(), prefill=(), decode=(), idle_s: float = 60.0, log: str | None = None):
    r = Router(mode, list(backends), list(prefill), list(decode), idle_s, log)
    app = web.Application(client_max_size=1 << 30)
    app[ROUTER_KEY] = r
    app.on_startup.append(r.start)
    app.on_cleanup.append(r.stop)
    for path in POST_PATHS:
        app.router.add_post(path, r.call)
    app.router.add_post("/tm/hint", r.hint)
    app.router.add_get("/health", r.health)
    app.router.add_get("/metrics", r.metrics)
    app.router.add_get("/metrics/{idx}", r.metrics)
    app.router.add_get("/router/stats", r.router_stats)
    app.router.add_post("/reset_prefix_cache", r.reset)
    app.router.add_route("*", "/{tail:.*}", r.other)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mode", choices=["dp", "pd", "pass"], required=True)
    ap.add_argument("--backends", default="")
    ap.add_argument("--prefill", default="")
    ap.add_argument("--decode", default="")
    ap.add_argument("--idle-s", type=float, default=60.0)
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    split = lambda s: [x for x in s.split(",") if x]  # noqa: E731
    web.run_app(make_app(a.mode, split(a.backends), split(a.prefill), split(a.decode), a.idle_s, a.log),
                host=a.host, port=a.port, access_log=None, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
