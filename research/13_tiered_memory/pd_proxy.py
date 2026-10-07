#!/usr/bin/env python3
"""Anthropic-API proxy for research/13_tiered_memory's 2-GPU cells (B2, B3). Every arm of a cell goes through it,
so all arms pay the same extra hop.

    python research/13_tiered_memory/pd_proxy.py --port P --mode affine --backends URL_A,URL_B [--weights 3,1]
    python research/13_tiered_memory/pd_proxy.py --port P --mode pd --prefill URL_A --decode URL_B
    python research/13_tiered_memory/pd_proxy.py --port P --mode pass --backends URL
Modes:
  affine   every call of a session (X-Session-ID) goes to one backend, chosen at the session's first call by weighted
           round robin (--weights 3,1: three sessions to A for each one to B -- B2's uneven load). DP=2 baselines.
  pd       1P1D (B3): the call goes to the prefill server with max_tokens=1, stream false and kv_transfer_params
           {"do_remote_decode": true}; the kv_transfer_params it returns go with the original call to the decode
           server (vLLM's NixlConnector: the decode side pulls only the blocks it does not hold).
  pass     one backend (the TP=2 reference): the same hop, no routing.
Other paths (GET /health, /v1/models, /metrics, /tm/*) go to the first backend (pd: the decode server).
GET /proxy/stats: calls per backend, errors, P / D milliseconds. --log PATH: one JSON line per call.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
from collections import Counter

from aiohttp import ClientSession, ClientTimeout, web

FWD = ("x-request-id", "x-session-id", "x-vllm-priority", "x-tm-init", "x-tm-base", "x-tm-verify", "anthropic-version",
       "x-api-key", "authorization", "content-type")
P_PARAMS = {"do_remote_decode": True, "do_remote_prefill": False, "remote_engine_id": None, "remote_block_ids": None,
            "remote_host": None, "remote_port": None}


class Proxy:
    def __init__(self, mode: str, backends: list[str], weights: list[int] | None, prefill: str | None,
                 decode: str | None, log: str | None) -> None:
        self.mode, self.log = mode, log
        self.backends = [b.rstrip("/") for b in backends]
        if mode == "pd":
            self.prefill, self.decode = prefill.rstrip("/"), decode.rstrip("/")
            self.backends = [self.decode, self.prefill]
        w = weights or [1] * len(self.backends)
        self.cycle = itertools.cycle([b for b, n in zip(self.backends, w) for _ in range(n)])
        self.affinity: dict[str, str] = {}
        self.stats: Counter = Counter()
        self.ms: Counter = Counter()
        self.http: ClientSession | None = None

    async def start(self, app) -> None:
        self.http = ClientSession(timeout=ClientTimeout(total=3600))

    async def stop(self, app) -> None:
        if self.http is not None:
            await self.http.close()

    def _headers(self, req: web.Request) -> dict:
        return {k: v for k, v in req.headers.items() if k.lower() in FWD}

    def _write(self, rec: dict) -> None:
        if self.log:
            with open(self.log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    async def _post(self, url: str, body: dict, headers: dict) -> tuple[int, bytes, str]:
        async with self.http.post(url, json=body, headers=headers) as r:
            return r.status, await r.read(), r.headers.get("Content-Type", "application/json")

    async def messages(self, req: web.Request) -> web.Response:
        t0 = time.perf_counter()
        body = await req.json()
        h = self._headers(req)
        sess = req.headers.get("X-Session-ID") or req.headers.get("X-Request-Id") or "-"
        rec = {"t": time.time(), "session": sess, "rid": req.headers.get("X-Request-Id"), "mode": self.mode}
        try:
            if self.mode == "pd":
                pbody = dict(body, max_tokens=1, stream=False, kv_transfer_params=dict(P_PARAMS))
                st, raw, _ = await self._post(self.prefill + req.path, pbody, h)
                rec["p_ms"] = round(1000 * (time.perf_counter() - t0), 1)
                if st != 200:
                    self.stats["p_errors"] += 1
                    rec["status"] = st
                    self._write(rec)
                    return web.Response(status=st, body=raw, content_type="application/json")
                kv = json.loads(raw).get("kv_transfer_params")
                t1 = time.perf_counter()
                st, raw, ctype = await self._post(self.decode + req.path, dict(body, kv_transfer_params=kv), h)
                rec["d_ms"] = round(1000 * (time.perf_counter() - t1), 1)
                rec["kv"] = bool(kv)
                backend = "pd"
            else:
                if self.mode == "affine":
                    backend = self.affinity.get(sess)
                    if backend is None:
                        backend = self.affinity[sess] = next(self.cycle)
                else:
                    backend = self.backends[0]
                st, raw, ctype = await self._post(backend + req.path, body, h)
            self.stats[backend] += 1
            rec.update(backend=backend, status=st, total_ms=round(1000 * (time.perf_counter() - t0), 1))
            self.ms["p"] += rec.get("p_ms", 0)
            self.ms["d"] += rec.get("d_ms", 0)
            self._write(rec)
            return web.Response(status=st, body=raw, content_type=ctype.split(";")[0])
        except Exception as exc:
            self.stats["errors"] += 1
            rec.update(status=502, error=repr(exc))
            self._write(rec)
            return web.json_response({"type": "error", "error": {"type": "proxy_error", "message": repr(exc)}},
                                     status=502)

    async def other(self, req: web.Request) -> web.StreamResponse:
        if req.path == "/proxy/stats":
            return web.json_response({"stats": dict(self.stats), "ms": dict(self.ms), "sessions": len(self.affinity),
                                      "mode": self.mode, "backends": self.backends})
        url = self.backends[0] + req.path_qs
        data = await req.read()
        async with self.http.request(req.method, url, data=data or None, headers=self._headers(req)) as r:
            return web.Response(status=r.status, body=await r.read(),
                                content_type=r.headers.get("Content-Type", "text/plain").split(";")[0])


def make_app(mode: str, backends: list[str], weights=None, prefill=None, decode=None, log=None) -> web.Application:
    p = Proxy(mode, backends, weights, prefill, decode, log)
    app = web.Application(client_max_size=1 << 30)
    app["proxy"] = p
    app.on_startup.append(p.start)
    app.on_cleanup.append(p.stop)
    app.router.add_post("/v1/messages", p.messages)
    app.router.add_route("*", "/{tail:.*}", p.other)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--mode", choices=["affine", "pd", "pass"], required=True)
    ap.add_argument("--backends", default="")
    ap.add_argument("--weights", default="")
    ap.add_argument("--prefill", default=None)
    ap.add_argument("--decode", default=None)
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    backends = [b for b in a.backends.split(",") if b]
    weights = [int(x) for x in a.weights.split(",")] if a.weights else None
    web.run_app(make_app(a.mode, backends, weights, a.prefill, a.decode, a.log), host=a.host, port=a.port,
                access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
