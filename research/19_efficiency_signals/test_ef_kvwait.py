#!/usr/bin/env python3
"""CPU tests of ef_kvwait.py (the KV-threshold wait-prompt probe): the request rewrite, the join of [server] lines to
the status log, the frozen winner rule, the server guard, and the whole run (screen, winner, confirm, verdict, resume)
against a fake /v1/completions server. Runs as a script with the vLLM venv (it has no pytest):

    /data/fritz/venv/bin/python research/19_efficiency_signals/test_ef_kvwait.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_kvwait as ek  # noqa: E402
import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

HR = ek.HR
SHARE = "[server] KV pool 258,176 tokens; sessions ahead of you hold {a}; your context 10,688, 10,608 still cached; " \
        "{w} requests waiting"


def synthetic() -> dict:
    return {"system": "Identity.\n\n" + HR.SERVER_SECTIONS["wait"] + "\n\nSkills catalog:",
            "tools": [{"name": "bash", "description": "Run.", "input_schema": {"type": "object"}},
                      {"name": "wait", "description": HR.WAIT_TEXTS["v3"], "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Fix it."},
                                                      {"type": "text", "text": SHARE.format(a="1,000", w=4)}]},
                         {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "bash",
                                                            "input": {"command": "ls"}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a.py"},
                                                      {"type": "text", "text": SHARE.format(a="2,000", w=5)}]}]}


def test_rewrite_changes_only_three_places():
    req = synthetic()
    keep = copy.deepcopy(req)
    lines = [(0.5, 4), (0.9, 5)]
    assert ek.rewrite(req, "B0", lines, None) is req
    rc = ek.rewrite(req, "RC", lines, 0.93)
    assert req == keep, "the recorded request must not change"
    assert "Server rule: when that line shows 80% or more" in rc["system"] and "{kv}" not in rc["system"]
    assert rc["tools"][1]["description"].startswith("Pause this session for `seconds`") and "80% full" in \
        rc["tools"][1]["description"]
    assert rc["messages"][0]["content"][1]["text"] == "[server] KV cache 50% full; 4 requests waiting"
    assert rc["messages"][2]["content"][1]["text"] == ("[server] KV cache 93% full; 5 requests waiting. At 80% or "
                                                       "more: add a wait call to your response.")
    t = ek.rewrite(req, "T", lines, 0.62)
    assert t["system"] == req["system"] and t["tools"] == req["tools"]
    assert t["messages"][2]["content"][1]["text"] == "[server] KV cache 62% full; 5 requests waiting"
    c = ek.rewrite(req, "C", lines, 0.62)
    assert c["messages"][2]["content"][1]["text"] == "[server] KV cache 62% full; 5 requests waiting"
    assert c["messages"][0]["content"][1]["text"] == "[server] KV cache 50% full; 4 requests waiting"
    for cond in ek.CONDS:
        assert ek.strip_variable(ek.rewrite(req, cond, lines, 0.85)) == ek.strip_variable(req), cond
    for bad, why in (({**req, "system": "no paragraph"}, "paragraph"), (req, "lines")):
        try:
            ek.rewrite(bad, "R", lines if why == "paragraph" else lines[:1], 0.9)
            raise AssertionError(f"rewrite must refuse: {why}")
        except ek.Unbuildable:
            pass


def test_join_skips_answers_without_a_line():
    msgs = synthetic()["messages"]
    recs = [((1000, 10688, 10608, 4), 0.51), ((7, 7, 7, 7), 0.99), ((2000, 10688, 10608, 5), 0.93)]
    assert ek.join(msgs, recs) == [(0.51, 4), (0.93, 5)]
    assert ek.join(msgs, recs[:2]) is None
    assert ek.join(msgs, [((1000, 10688, 10608, 4), None)] + recs[2:]) is None


def _row(high, low, only=0.0, kept=1.0):
    return {"work": {"wait": high, "kept": kept}, "all": {"only": only}}, {"work": {"wait": low, "kept": kept},
                                                                           "all": {"only": only}}


def test_pick_follows_the_frozen_rule():
    def res(rows, b0_kept=1.0):
        vs = {"B0": {"work": {"wait": 0.0, "kept": b0_kept}, "all": {"only": 0.0}}}
        for c, (h, lo) in rows.items():
            vs[f"{c}@0.93"], vs[f"{c}@0.62"] = h, lo
        return {"variants": vs}
    base = {"T": _row(0.05, 0.0), "R": _row(0.60, 0.02), "RC": _row(0.64, 0.03), "C": _row(0.70, 0.30)}
    got = ek.pick(res(base))
    assert got["winner"] == "R", got                   # C waits too often when calm; R ties RC within 0.05
    assert ek.pick(res({**base, "RC": _row(0.70, 0.03)}))["winner"] == "RC"
    assert ek.pick(res({**base, "R": _row(0.60, 0.02, only=0.08)}))["winner"] == "RC"
    only_t = ek.pick(res({**base, "R": _row(0.60, 0.02, kept=0.70), "RC": _row(0.64, 0.03, kept=0.70)},
                         b0_kept=0.95))                # R and RC lose the recorded action; C waits when calm
    assert only_t["winner"] is None and only_t["rows"]["T"]["eligible"], only_t     # T alone, below min_high
    low = ek.pick(res({c: _row(0.2, 0.0) for c in ek.CONDS}))
    assert low["winner"] is None and "< 0.3" in low["why"], low


class Fake:
    """An aiohttp app on its own loop and thread."""

    def __init__(self, routes):
        from aiohttp import web
        self.app = web.Application()
        for method, path, handler in routes:
            self.app.router.add_route(method, path, handler)
        self.loop = asyncio.new_event_loop()
        self.runner = web.AppRunner(self.app)
        self.loop.run_until_complete(self.runner.setup())
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        self.loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.loop.run_forever, daemon=True).start()


def test_guard_accepts_only_an_s2a_profile_server():
    from aiohttp import web

    async def models(request):
        return web.json_response({"data": [{"id": request.app["model"]}]})
    fake = Fake([("GET", "/v1/models", models)])
    fake.app["model"] = cf.MODEL
    saved = ek.T21_SERVERS
    try:
        ek.T21_SERVERS = Path(tempfile.mkdtemp(prefix="kvwait_guard_"))
        assert "no live topic-21 stage" in ek.guard(fake.url)
        info = ek.T21_SERVERS / "s2a_probe_20261030T193000Z" / "server_info.json"
        info.parent.mkdir()
        for stage, config, ok in (("s2b_share", "load16", False), ("s2a_probe", "load19", False),
                                  ("s2a_probe", "profile", True)):
            info.write_text(json.dumps({"stage": stage, "config": config, "port": fake.port}))
            assert (ek.guard(fake.url) is None) == ok, (stage, config, ek.guard(fake.url))
        fake.app["model"] = "Qwen/Qwen3-32B"
        assert "not MiniMaxAI" in ek.guard(fake.url)
        fake.app["model"] = cf.MODEL
        info.write_text(json.dumps({"stage": "s2a_probe", "config": "profile", "port": fake.port, "t_stop": 1}))
        assert "no live topic-21 stage" in ek.guard(fake.url)
    finally:
        ek.T21_SERVERS = saved


def gen_for(calls: list[dict], wait: bool) -> str:
    calls = calls + ([{"name": "wait", "input": {"seconds": 15}}] if wait else [])
    inv = "".join(f'<invoke name="{c["name"]}">' + "".join(
        f'<parameter name="{k}">{v if isinstance(v, str) else json.dumps(v)}</parameter>\n'
        for k, v in c["input"].items()) + "</invoke>\n" for c in calls)
    return f"ok\n</think>\n\n<minimax:tool_call>\n{inv}</minimax:tool_call>[e~[" if calls else "done\n</think>\n\nanswer[e~["


def _subset(items: dict) -> dict:
    """3 work items and 1 final item per phase."""
    out = {k: v for k, v in items.items() if k not in ("screen", "confirm")}
    for phase in ("screen", "confirm"):
        work = [it for it in items[phase] if it["stratum"] == "work"][:3]
        final = [it for it in items[phase] if it["stratum"] == "final"][:1]
        out[phase] = work + final
    return out


def _e2e(complies: bool) -> tuple[dict, list, Path]:
    """The whole run against a fake that answers with the recorded calls, plus `wait` when it complies and the
    newest line shows 80 % or more (never on a final answer)."""
    from aiohttp import web
    import ef_render
    items = _subset(ek.load_items())
    by_len, seen = {}, []
    for phase, plan in (("screen", ek.screen_plan()), ("confirm", ek.confirm_plan("R"))):
        for sess, row, rk, variants in ek.chains(items[phase], plan):
            calls = next(it["calls"] for it in items[phase] if (it["sid"], it["turn"], it["j"]) ==
                         (sess["sid"], row["turn"], row["j"]))
            for _, req in variants:
                n = len(cf.render_ids((req, sess["template"])))
                assert by_len.get(n, calls) == calls, "two test prompts of the same length"
                by_len[n] = calls
    tok = ef_render.tokenizer()
    newest = re.compile(r"\[server\] KV cache (\d+)% full")

    async def completions(request):
        body = await request.json()
        seen.append({k: v for k, v in body.items() if k != "prompt"})
        n = body.get("n", 1)
        if body["max_tokens"] == 1:
            return web.json_response({"choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                                      "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 1}})
        calls = by_len[len(body["prompt"])]
        levels = newest.findall(tok.decode(body["prompt"][-400:]))
        wait = complies and bool(calls) and bool(levels) and int(levels[-1]) >= 80
        text = gen_for(calls, wait)
        return web.json_response({"choices": [{"index": i, "text": text, "finish_reason": "stop"} for i in range(n)],
                                  "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 7 * n}})
    fake = Fake([("POST", "/v1/completions", completions)])
    tmp = Path(tempfile.mkdtemp(prefix="kvwait_e2e_"))
    saved = (ek.KV, ek.ITEMS, ek.TABLES, ek.changed, ek.RENDERERS)
    try:
        ek.KV, ek.ITEMS, ek.TABLES, ek.RENDERERS = tmp / "kvwait", tmp / "items.json", tmp / "tables", 2
        ek.changed = lambda: []
        ek.ITEMS.write_text(json.dumps(items))
        assert ek.run(fake.url, any_server=True) == 0
        first = len(seen)
        assert ek.run(fake.url, any_server=True) == 0          # resumes: everything is on disk already
        assert len(seen) == first, "a second run must not send anything"
        res = json.loads((ek.TABLES / "kvwait.json").read_text())
    finally:
        ek.KV, ek.ITEMS, ek.TABLES, ek.changed, ek.RENDERERS = saved
    return res, seen, tmp


def test_end_to_end_compliant_model_passes():
    res, seen, tmp = _e2e(complies=True)
    assert all(b["n"] == ek.S and b["top_k"] == 40 and b["temperature"] == 1.0 for b in seen if b["max_tokens"] > 1)
    sv = res["screen"]["variants"]
    assert sv["B0"]["work"]["wait"] == 0.0
    for c in ek.CONDS:
        assert sv[f"{c}@0.93"]["work"]["wait"] == 1.0 and sv[f"{c}@0.62"]["work"]["wait"] == 0.0, c
        assert sv[f"{c}@0.93"]["final"]["wait"] == 0.0 and sv[f"{c}@0.93"]["work"]["kept"] == 1.0, c
    assert res["winner"]["winner"] == "R", res["winner"]
    cv = res["confirm"]["variants"]
    assert [cv[f"R@{lv:.2f}"]["work"]["wait"] for lv in ek.CONFIRM_LEVELS] == [0.0, 0.0, 1.0, 1.0]
    assert cv["R@0.95"]["seconds_p50"] == 15.0
    assert res["confirm"]["verdict"]["pass"], res["confirm"]["verdict"]
    md = (tmp / "tables" / "kvwait.md").read_text()
    assert "**Winner:** `R`" in md and "**Verdict: PASS**" in md


def test_end_to_end_ignoring_model_stops_after_the_screen():
    res, seen, tmp = _e2e(complies=False)
    assert res["winner"]["winner"] is None and "confirm" not in res
    assert not (tmp / "kvwait" / "confirm" / "records.jsonl").exists()


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}", flush=True)
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
    sys.exit(1 if failures else 0)
