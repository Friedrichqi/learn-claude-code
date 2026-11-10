#!/usr/bin/env python3
"""A fake MiniMax server for topic 22's CPU dry run: the whole pipeline (Harbor, wb_agent, wb_child, the env file, the
queue order, --n-concurrent-agents, the scrape and the analysis) without a GPU.

- POST /v1/messages (Anthropic format, as vLLM serves it): when the request offers `wait` and the history has no wait
  yet, the reply calls bash (pwd) and wait (2 s) together; otherwise it answers "Done." and ends the turn. Every
  request is summarized in --log: whether the system prompt has the wait_kv paragraph, whether `wait` is offered with
  the v5 text, the newest [server] line, and the tools.
- GET /wt/status: a stock-view answer at KV 93 % (the cue fires); GET /metrics: vLLM-named counters; /health; /v1/models.

    python research/22_wait_bench/wb_fake.py --port 50223 --log data/dry/fake.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from aiohttp import web

MODEL = "MiniMaxAI/MiniMax-M2.7"
COUNT = {"requests": 0, "prompt": 0, "generation": 0}


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                out.append(text_of(b.get("content")))
    return "\n".join(out)


def make_app(log_path: str) -> web.Application:
    log = open(log_path, "a")

    async def messages(request: web.Request) -> web.Response:
        body = await request.json()
        tools = {t.get("name"): t.get("description", "") for t in body.get("tools") or []}
        msgs = body.get("messages") or []
        system = body.get("system")
        system = system if isinstance(system, str) else text_of(system)
        newest = text_of(msgs[-1].get("content")) if msgs else ""
        lines = [ln for ln in newest.splitlines() if ln.startswith("[server]")]
        waited = any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "wait"
                     for m in msgs if m.get("role") == "assistant" for b in (m.get("content") or [])
                     if isinstance(m.get("content"), list))
        log.write(json.dumps({"t": time.time(), "tools": sorted(tools), "wait_offered": "wait" in tools,
                              "wait_v5": "KV cache 80% full or more" in tools.get("wait", ""),
                              "paragraph": "Server rule: when that line shows 80% or more" in system,
                              "server_line": lines[-1] if lines else None, "waited_before": waited,
                              "n_messages": len(msgs)}) + "\n")
        log.flush()
        n_in = max(1, len(json.dumps(msgs)) // 4)
        if "wait" in tools and not waited:
            content = [{"type": "text", "text": "Checking the directory, and the server is busy."},
                       {"type": "tool_use", "id": "toolu_fake_1", "name": "bash", "input": {"command": "pwd"}},
                       {"type": "tool_use", "id": "toolu_fake_2", "name": "wait", "input": {"seconds": 2,
                                                                                            "reason": "KV cache 93%"}}]
            stop = "tool_use"
        else:
            content, stop = [{"type": "text", "text": "Done."}], "end_turn"
        COUNT["requests"] += 1
        COUNT["prompt"] += n_in
        COUNT["generation"] += 20
        await asyncio.sleep(0.2)
        return web.json_response({"id": f"msg_fake_{COUNT['requests']}", "type": "message", "role": "assistant",
                                  "model": MODEL, "content": content, "stop_reason": stop, "stop_sequence": None,
                                  "usage": {"input_tokens": n_in, "output_tokens": 20, "cache_read_input_tokens": 0,
                                            "cache_creation_input_tokens": 0}})

    async def status(request: web.Request) -> web.Response:
        return web.json_response({"age_ms": 5.0, "running": 2, "waiting": 3, "waiting_capacity": 3,
                                  "waiting_deferred": 0, "kv_usage": 0.93, "hit_rate_10s": 0.9, "preemptions_10s": 0,
                                  "window_s": 10.0})

    async def metrics(request: web.Request) -> web.Response:
        m = MODEL
        text = (f'vllm:request_success_total{{finished_reason="stop",model_name="{m}"}} {COUNT["requests"]}\n'
                f'vllm:prompt_tokens_total{{model_name="{m}"}} {COUNT["prompt"]}\n'
                f'vllm:generation_tokens_total{{model_name="{m}"}} {COUNT["generation"]}\n'
                f'vllm:kv_cache_usage_perc{{model_name="{m}"}} 0.93\n'
                f'vllm:num_requests_running{{model_name="{m}"}} 2\n'
                f'vllm:num_requests_waiting{{model_name="{m}"}} 3\n')
        return web.Response(text=text)

    async def ok(request: web.Request) -> web.Response:
        return web.json_response({"data": [{"id": MODEL}]})

    app = web.Application(client_max_size=256 * 1024 * 1024)
    app.router.add_post("/v1/messages", messages)
    app.router.add_get("/wt/status", status)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/health", ok)
    app.router.add_get("/v1/models", ok)
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", required=True)
    a = ap.parse_args()
    web.run_app(make_app(a.log), host="127.0.0.1", port=a.port, print=None)


if __name__ == "__main__":
    main()
