"""Tests for topic 23's code without a GPU: the gate, the proxy's store policy, the controller's parsing and prompt,
the hardware monitor's summary, the save_context tool, and the stage runner's helpers.

    /data/fritz/venv23/bin/python -m pytest -q research/23_efficiency_tools/test_et.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR))

import et_controller  # noqa: E402
import et_hw  # noqa: E402
import et_prompts  # noqa: E402
import et_proxy  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class NullLog:
    def __init__(self):
        self.rows = []

    def write(self, rec):
        self.rows.append(rec)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# -- the gate -----------------------------------------------------------------------------------------------------------
def test_gate_holds_k_and_admits_first_come_first_served():
    async def go():
        clock, log = Clock(), NullLog()
        g = et_proxy.Gate(2, 32, log, clock=clock)
        await g.acquire("a")
        await g.acquire("b")
        waits = {s: asyncio.ensure_future(g.acquire(s)) for s in ("c", "d")}
        await asyncio.sleep(0)
        assert len(g.active) == 2 and list(g.waiting) == ["c", "d"]
        g.release("a", 1000)
        clock.t += 4.9
        g.tick()
        assert "a" in g.active                       # idle for less than 5 s: keeps its slot
        clock.t += 0.2
        g.tick()
        await asyncio.sleep(0)
        assert "a" not in g.active and "c" in g.active and waits["c"].done() and not waits["d"].done()
        assert len(g.active) <= 2
        assert g.session("c")["blocked_s"] == 5.1 and g.context["a"] == 1000
        waits["d"].cancel()
    run(go())


def test_gate_runtime_k_up_admits_and_down_does_not_evict():
    async def go():
        clock = Clock()
        g = et_proxy.Gate(1, 4, NullLog(), clock=clock)
        await g.acquire("a")
        w = asyncio.ensure_future(g.acquire("b"))
        await asyncio.sleep(0)
        assert not w.done()
        g.set_k(3, by="test")
        await asyncio.sleep(0)
        assert w.done() and len(g.active) == 2
        g.set_k(1, by="test")
        assert len(g.active) == 2                    # nobody is evicted
        g.set_k(99, by="test")
        assert g.k == 4                              # clamped to n_max
    run(go())


def test_gate_lowered_k_takes_slots_back_at_request_ends():
    """After K is lowered, a session over the limit gives its slot up when its request ends (nothing is cut), and its
    next request queues behind the waiting sessions; at or under the limit sessions keep their slots."""
    async def go():
        clock = Clock()
        log = NullLog()
        g = et_proxy.Gate(4, 4, log, clock=clock)
        for s in "abcd":
            await g.acquire(s)
        g.set_k(2, by="test")
        assert len(g.active) == 4
        g.release("a", 100)
        assert "a" not in g.active and len(g.active) == 3
        w = asyncio.ensure_future(g.acquire("a"))   # a's next request waits
        await asyncio.sleep(0)
        assert not w.done() and "a" in g.waiting
        g.release("b", 100)
        assert len(g.active) == 2
        g.release("c", 100)                         # at the limit now: c keeps its slot until idle
        assert "c" in g.active and len(g.active) == 2
        assert [r["why"] for r in log.rows if r.get("on") == 0] == ["over", "over"]
        clock.t += 10
        g.tick()                                    # c idle 10 s: freed, and a is admitted
        await asyncio.sleep(0)
        assert w.done() and "a" in g.active and "c" not in g.active
        assert g.session("a")["blocked_s"] == 10.0
    run(go())


def test_gate_busy_session_keeps_slot():
    async def go():
        clock = Clock()
        g = et_proxy.Gate(1, 4, NullLog(), clock=clock)
        await g.acquire("a")                         # in flight
        clock.t += 60
        g.tick()
        assert "a" in g.active
    run(go())


# -- the proxy's store policy and usage parsing ---------------------------------------------------------------------------
def proxy(policy: str):
    args = SimpleNamespace(store_policy=policy, no_store_key="lmcache.max_offload_tokens", out="/tmp/et_test_proxy",
                           gate_k=None, n_max=32)
    return et_proxy.Proxy(args)


def test_store_policy_flagged_marks_all_but_saves():
    p = proxy("flagged")
    body = json.dumps({"messages": [], "kv_transfer_params": {"x": 1}}).encode()
    normal = json.loads(p.body_for(body, "/v1/messages", store=False))
    assert normal["kv_transfer_params"] == {"x": 1, "lmcache.max_offload_tokens": 0}
    save = json.loads(p.body_for(body, "/v1/messages", store=True))
    assert save["kv_transfer_params"] == {"x": 1}
    assert p.body_for(b"{}", "/metrics", store=False) == b"{}"


def test_store_policy_all_and_off_leave_bodies():
    body = json.dumps({"messages": []}).encode()
    assert "kv_transfer_params" not in json.loads(proxy("all").body_for(body, "/v1/messages", store=False))
    assert proxy("off").body_for(body, "/v1/messages", store=False) == body


def test_usage_tokens_anthropic_and_openai():
    a = json.dumps({"usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50,
                              "output_tokens": 7}}).encode()
    assert et_proxy.usage_tokens(a) == (1050, 900, 7)
    o = json.dumps({"usage": {"prompt_tokens": 10, "completion_tokens": 3,
                              "prompt_tokens_details": {"cached_tokens": 4}}}).encode()
    assert et_proxy.usage_tokens(o) == (10, 4, 3)
    assert et_proxy.usage_tokens(b"not json") == (None, None, None)


# -- the controller ------------------------------------------------------------------------------------------------------
def reply(name="set_session_limit", args='{"k": 12, "reason": "fits"}'):
    return {"choices": [{"message": {"tool_calls": [{"function": {"name": name, "arguments": args}}]}}]}


def test_controller_parses_and_clamps():
    assert et_controller.parse_call(reply(), 32) == (12, "fits")
    assert et_controller.parse_call(reply(args='{"k": 99}'), 32)[0] == 32
    assert et_controller.parse_call(reply(args='{"k": 0}'), 32)[0] == 1
    assert et_controller.parse_call(reply(name="other"), 32)[0] is None
    assert et_controller.parse_call(reply(args="{bad"), 32)[0] is None
    assert et_controller.parse_call({"choices": []}, 32)[0] is None


def test_controller_rounds_start_every_interval():
    """The interval counts from a round's start: rounds of 0.2 s every 0.3 s start 0.3 s apart (not 0.5 s), and a
    round longer than the interval is followed at once."""
    for interval, took, gap in ((0.3, 0.2, 0.3), (0.1, 0.25, 0.25)):
        c = et_controller.Controller.__new__(et_controller.Controller)
        c.args = SimpleNamespace(controller_interval=interval)
        starts = []

        async def decide():
            starts.append(time.time())
            await asyncio.sleep(took)
        c.decide = decide

        async def go():
            task = asyncio.ensure_future(c.loop())
            await asyncio.sleep(interval + 3 * max(interval, took) + 0.05)
            task.cancel()
        run(go())
        diffs = [b - a for a, b in zip(starts, starts[1:])]
        assert len(diffs) >= 2 and all(abs(d - gap) < 0.08 for d in diffs), (interval, took, diffs)


def test_prompts_render_every_variant_with_partial_metrics():
    gate = {"k": 16, "active": 16, "active_idle": 3, "waiting": 9, "oldest_wait_s": 240,
            "active_context_tokens": 610000, "next_waiting_context_tokens": 31000}
    summary = {"window_s": 60, "now": {"kv_usage_mean": 0.93, "kv_capacity_tokens": 673438, "prompt_tok_s": 1000.0,
                                       "computed_tok_s": 600.0, "gpu_hit_tok_s": 400.0},
               "minutes": [{"k": 16, "requests_per_min": 30.0}]}
    hist = [{"old": 32, "new": 16, "reason": "thrash", "after": {"requests_per_min": 33.0}}]
    for v in et_prompts.VARIANTS:
        msgs = et_prompts.messages(summary, gate, hist, v, 32)
        assert msgs[0]["role"] == "system" and "32 coding agents" in msgs[0]["content"]
        assert "set_session_limit" in msgs[1]["content"] and "673,438" in msgs[1]["content"]
        assert ("Your last decisions" in msgs[1]["content"]) == (v == "c4")
    assert et_prompts.render({}, {}, None, "c1")                 # nothing known yet: still renders
    assert et_prompts.tool(32)["function"]["parameters"]["properties"]["k"]["maximum"] == 32


# -- the hardware monitor's summary --------------------------------------------------------------------------------------
def rec(wall, prompt, computed, hit, gen, req, kv, running):
    return {"wall": wall, "ok": True, "gpu": [{"power_w": 600.0, "gpm": {"sm_util": 0.9, "dram_bw_util": 0.5}}],
            "m": {"vllm:prompt_tokens_total": prompt, "vllm:prompt_tokens_by_source_total{local_compute}": computed,
                  "vllm:prompt_tokens_by_source_total{local_cache_hit}": hit, "vllm:generation_tokens_total": gen,
                  "vllm:request_success_total": req, "vllm:kv_cache_usage_perc": kv,
                  "vllm:num_requests_running": running}}


def test_hw_summary_rates_and_means():
    recs = [rec(0, 0, 0, 0, 0, 0, 0.5, 10), rec(60, 60000, 45000, 15000, 6000, 30, 0.9, 14)]
    s = et_hw.summarize(recs, 673438)
    assert s["prompt_tok_s"] == 1000 and s["computed_tok_s"] == 750 and s["gen_tok_s"] == 100
    assert s["requests_per_min"] == 30 and abs(s["gpu_hit_rate"] - 0.25) < 1e-9
    assert abs(s["kv_usage_mean"] - 0.7) < 1e-9 and s["kv_usage_max"] == 0.9 and s["running_mean"] == 12
    assert s["sm_util"] == 0.9 and s["kv_capacity_tokens"] == 673438
    assert et_hw.summarize(recs[:1], None) == {}


def test_hw_parsers():
    text = ('vllm:cache_config_info{block_size="784",num_gpu_blocks="859",x="y"} 1.0\n'
            'lmcache_mp_lookup_hit{cache_salt=""} 10\nlmcache_mp_lookup_hit{cache_salt="a"} 5\nother 3\n')
    assert et_hw.kv_capacity(text) == 784 * 859
    assert et_hw.parse_lmcache(text) == {"lmcache_mp_lookup_hit": 15.0}


# -- save_context in the pinned harness ----------------------------------------------------------------------------------
def load_hold(env: dict):
    path = DIR / "vendor" / "s15" / "s15_integrated_harness" / "hold_runtime.py"
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("hold_runtime_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod                 # its dataclasses look their module up there (as code.py loads it)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_save_tool_is_opt_in_and_lead_only():
    off = load_hold({})
    assert off.pacing_tools(lead=True) == []
    on = load_hold({"S15_WAIT_TOOL": "1", "S15_SAVE_TOOL": "1", "S15_SAVE_TEXT": "s4", "S15_WAIT_KV": "0.8"})
    assert [t["name"] for t in on.pacing_tools(lead=True)] == ["wait", "save_context"]
    assert [t["name"] for t in on.pacing_tools()] == ["wait"]
    desc = on.pacing_tools(lead=True)[1]["description"]
    assert "80%" in desc and "90%" in desc and "{kv" not in desc
    assert on.is_deferred("save_context")


def test_run_save_reports_and_fails_quietly():
    h = load_hold({"S15_SAVE_TOOL": "1"})
    assert "Saved 1234 tokens" in h.run_save(lambda: 1234)
    assert h.run_save(None).startswith("Error:")

    def boom():
        raise RuntimeError("HTTP 529 overloaded")
    out = h.run_save(boom)
    assert out.startswith("Error:") and "529" not in out and "429" not in out


# -- the stage runner's helpers ------------------------------------------------------------------------------------------
def test_trial_of_matches_harbor_project_names():
    import et_cell
    assert et_cell.trial_of("astropy__astropy-12907__sitp7v9__env") == "sitp7v9"
    assert et_cell.trial_of("instance_future-architect__vuls__sagzp2y__env") == "sagzp2y"
    assert et_cell.trial_of("bridge") is None


def test_setups_map_to_proxy_and_server_flags():
    import et_cell
    url = "http://127.0.0.1:31431"
    p = et_cell.proxy_cmd("gate8", "a", url, Path("/tmp/x"))
    assert p[p.index("--gate-k") + 1] == "8" and "--store-policy" not in p
    p = et_cell.proxy_cmd("lmc", "b", url, Path("/tmp/x"))
    assert p[p.index("--store-policy") + 1] == "flagged" and "--gate-k" not in p
    assert et_cell.proxy_cmd("agate", "a", url, Path("/tmp/x")).count("--controller") == 1
    assert "--kv-transfer-config" in et_cell.server_args("lmc_all", "a")
    assert "--kv-transfer-config" not in et_cell.server_args("wait", "a")
    assert et_cell.SETUPS["lmc"]["env"]["S15_SAVE_TOOL"] == "1" and et_cell.SETUPS["lmc"]["env"]["S15_WAIT_TOOL"] == "1"
    assert not et_cell.SETUPS["none"] and et_cell.SETUPS["none_r2"] == et_cell.SETUPS["none"]


def test_child_session_id(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"staging": "/x/jobs/run/astropy__astropy-1__AbC1234/agent/s15"}))
    spec = importlib.util.spec_from_file_location("et_child_src", DIR / "et_child.py")
    src = (DIR / "et_child.py").read_text()
    ns: dict = {}
    exec(src[src.index("def session_id"):src.index("sc.install_profile =")], {"json": json, "Path": Path}, ns)
    assert ns["session_id"](str(cfg)) == "astropy__astropy-1__AbC1234"


# -- the proxy over HTTP, against a fake vLLM ----------------------------------------------------------------------------
def test_proxy_end_to_end_gate_and_store(tmp_path):
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer

    async def go():
        seen, live = [], {"now": 0, "max": 0, "sessions": set(), "max_sessions": 0}

        async def fake(request: web.Request):
            body = await request.json()
            seen.append({"sid": request.headers.get("X-Session-ID"), "kv": body.get("kv_transfer_params")})
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
            await asyncio.sleep(0.05)
            live["now"] -= 1
            return web.json_response({"usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 1}})

        up_app = web.Application()
        up_app.router.add_post("/v1/messages", fake)
        up = TestServer(up_app)
        await up.start_server()
        args = SimpleNamespace(upstream=str(up.make_url("")).rstrip("/"), out=str(tmp_path), gate_k=2, n_max=32,
                               store_policy="flagged", no_store_key="lmcache.max_offload_tokens", controller=None,
                               k_schedule=None)
        p = et_proxy.Proxy(args)
        app = web.Application()
        app.router.add_get("/gate/session/{sid}", p.gate_session)
        app.router.add_route("*", "/{tail:.*}", p.forward)
        app.on_startup.append(p.start)
        app.on_cleanup.append(p.stop)
        px = TestServer(app)
        await px.start_server()
        old_idle = et_proxy.IDLE_S
        p.gate.idle_s = 0.2
        async with ClientSession() as s:
            async def call(sid, store=False):
                h = {"X-Session-ID": sid, **({"X-KV-Store": "1"} if store else {})}
                async with s.post(px.make_url("/v1/messages"), json={"messages": []}, headers=h) as r:
                    assert r.status == 200
            await asyncio.gather(*(call(f"s{i}") for i in range(5)), call("s0", store=True))
            async with s.get(px.make_url("/gate/session/s4")) as r:
                info = await r.json()
        await px.close()
        await up.close()
        et_proxy.IDLE_S = old_idle
        assert len(seen) == 6
        assert all(x["sid"] is None for x in seen)                # the session header is not forwarded
        kvs = [x["kv"] for x in seen]
        assert kvs.count({"lmcache.max_offload_tokens": 0}) == 5 and kvs.count(None) == 1
        assert info["blocked_s"] > 0.1                             # s4 waited for a slot
        rows = [json.loads(l) for l in (tmp_path / "proxy_requests.jsonl").read_text().splitlines()]
        assert len(rows) == 6 and all(r["prompt"] == 100 for r in rows)
        # sessions with a slot at once never exceed K (gate events: +1 on, -1 off)
        active = peak = 0
        for e in (json.loads(l) for l in (tmp_path / "gate_events.jsonl").read_text().splitlines()):
            active += 1 if e.get("on") == 1 else (-1 if e.get("on") == 0 else 0)
            peak = max(peak, active)
        assert peak <= 2
    run(go())


# -- the prompt screens --------------------------------------------------------------------------------------------------
def test_screen_rewrites_only_the_newest_line():
    import et_screen
    h = et_screen.hold(et_screen.WAIT_ENV)
    msgs = [{"role": "user", "content": [{"type": "text", "text": "task"},
                                         {"type": "text", "text": "[server] KV cache 95% full; 7 requests waiting. "
                                                                  "At 80% or more: add a wait call to your response."}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                          "content": "Waited 20 s.\n[server] KV cache 50% full; 1 requests waiting"},
                                         {"type": "text", "text": "[server] KV cache 70% full; 3 requests waiting"}]}]
    out, n = et_screen.rewrite_lines(msgs, 0.93, h)
    text = json.dumps(out)
    assert n == 3
    assert "KV cache 95% full; 7 requests waiting. At 80% or more: add a wait call to your response." in text  # kept
    assert "KV cache 50% full; 1 requests waiting" in text
    assert "KV cache 93% full; 3 requests waiting. At 80% or more: add a wait call to your response." in text
    low, _ = et_screen.rewrite_lines(msgs, 0.62, h)
    assert "KV cache 62% full; 3 requests waiting\"" in json.dumps(low)
    assert msgs[2]["content"][1]["text"] == "[server] KV cache 70% full; 3 requests waiting"   # the input is not changed


def test_screen_add_save_advertises_like_the_runs():
    import et_screen
    st = et_screen.add_save("Tools: bash, read_file. Also available: wait.", [{"name": "bash"}, {"name": "wait"}], "s2")
    assert st[0].endswith("Also available: wait, save_context.")
    assert [t["name"] for t in st[1]] == ["bash", "wait", "save_context"] and "80%" in st[1][2]["description"]
    assert et_screen.add_save("no tool sentence", [], "s1") is None


def test_screen_reference_label():
    import et_screen
    now = {"kv_capacity_tokens": 600000, "gpu_hit_rate": 0.8}
    assert et_screen.label(now, {"active_context_tokens": 590000, "waiting": 3}) == "lower"
    assert et_screen.label(now, {"active_context_tokens": 300000, "next_waiting_context_tokens": 50000,
                                 "waiting": 2}) == "raise"
    assert et_screen.label(now, {"active_context_tokens": 300000, "waiting": 0}) == "keep"
    pressed = {**now, "gpu_hit_rate": 0.3, "preemptions_per_min": 2}
    assert et_screen.label(pressed, {"active_context_tokens": 400000, "waiting": 0}) == "lower"


def test_revised_screen_conditions():
    import et_screen
    h = et_screen.hold({**et_screen.WAIT_ENV, "S15_STATUS_FORMAT": "kv_cue_save"})
    assert h.kv_line({"kv_usage": 0.93, "waiting": 1}).endswith("add a wait call and a save_context call to your response.")
    assert "save_context" not in h.kv_line({"kv_usage": 0.62, "waiting": 1})
    req = {"system": "x. Also available: wait.", "tools": [{"name": "wait"}],
           "messages": [{"role": "user", "content": [{"type": "text", "text": "[server] KV cache 85% full; 2 requests waiting"}]}]}
    c = et_screen.conditions2(req)
    assert sorted(c) == ["S5@62", "S5@93", "S6@62", "S6@93"]
    assert "and a save_context call" in json.dumps(c["S5@93"]["messages"])
    assert "and a save_context call" not in json.dumps(c["S6@93"]["messages"])
    assert "Example:" in c["S6@93"]["tools"][-1]["description"] and "Example:" not in c["S5@93"]["tools"][-1]["description"]


def test_analysis_counts_the_agents_work_and_the_controller(tmp_path, monkeypatch):
    """Throughput leaves out the harness's own calls (save re-sends, controller calls); validity counts the
    controller's calls, which skip the proxy."""
    import et_analyze
    run = tmp_path / "runs" / "x"
    run.mkdir(parents=True)
    monkeypatch.setattr(et_analyze, "RUNS", tmp_path / "runs")
    rows = [{"t_done": 110.0, "status": 200, "store": True, "prompt": 30000, "out": 1},
            {"t_done": 120.0, "status": 200, "store": False, "prompt": 20000, "out": 500},
            {"t_done": 500.0, "status": 200, "store": True, "prompt": 9, "out": 1}]          # outside [100, 200]
    (run / "proxy_requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ctl = [{"t": 130.0, "s": 40.0, "status": 200, "usage": {"prompt_tokens": 1000, "completion_tokens": 2000}},
           {"t": 140.0, "s": 300.0, "status": None, "reason": "TimeoutError: "},              # ends at 440
           {"t": 150.0, "s": 1.0, "status": None, "reason": "ClientConnectorError: refused"}]  # never reached vLLM
    (run / "controller.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ctl))
    over = et_analyze.overhead("x")
    assert sorted(over) == [(110.0, 30000, 1), (170.0, 1000, 2000), (500.0, 9, 1)]   # the cut call is left out

    def seg(t, n, p, g):
        return {"wall": t, "m": {"vllm:request_success_total": n, "vllm:prompt_tokens_total": p,
                                 "vllm:generation_tokens_total": g, "vllm:num_preemptions_total": 0,
                                 "vllm:prefix_cache_queries_total": 0, "vllm:prefix_cache_hits_total": 0}}
    segs = [[seg(100.0, 0, 0, 0), seg(200.0, 3, 51000, 2501)]]
    net = et_analyze.net_rates(segs, 100.0, 200.0, over)
    assert net["overhead_requests"] == 2
    assert abs(net["requests_per_h"] - 1 / 100 * 3600) < 1e-6
    assert abs(net["prompt_tokens_per_s"] - 200.0) < 1e-6 and abs(net["gen_tokens_per_s"] - 5.0) < 1e-6


def test_gpm_means_skip_nan_samples():
    """A sample NVML returns as NaN is left out of the utilization means instead of making them NaN."""
    import et_analyze

    def rec(t, bw):
        return {"wall": t, "gpu": [{"gpm": {"sm_util": 0.8, "sm_occupancy": 0.2, "tensor_util": 0.4,
                                             "dram_bw_util": bw}, "power_w": 500.0}]}
    segs = [[rec(100.0, 0.5), rec(101.0, float("nan")), rec(102.0, 0.7), rec(300.0, float("nan"))]]
    g = et_analyze.gpm_means(segs, 100.0, 200.0)
    assert abs(g["dram_bw_util"] - 0.6) < 1e-9 and abs(g["sm_util"] - 0.8) < 1e-9
    assert et_analyze.gpm_means([[rec(100.0, float("nan"))]], 100.0, 200.0)["dram_bw_util"] is None
