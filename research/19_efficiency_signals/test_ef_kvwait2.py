#!/usr/bin/env python3
"""CPU tests of ef_kvwait2.py (the revised screen): the capped history and wait text v6 in the rewrite, and the whole
run (screen, contrasts, readings, resume) against a fake model that waits only when no earlier [server] line shows
80 % or more. Runs as a script with the vLLM venv:

    /data/fritz/venv/bin/python research/19_efficiency_signals/test_ef_kvwait2.py
"""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_kvwait as ek  # noqa: E402
import ef_kvwait2 as e2  # noqa: E402
import rd_cf as cf  # noqa: E402
from test_ef_kvwait import Fake, gen_for, synthetic  # noqa: E402

CUE = ". At 80% or more: add a wait call to your response."


def test_capped_history_and_the_worked_example():
    req = synthetic()
    keep = copy.deepcopy(req)
    lines = [(0.9, 4), (0.95, 5)]
    rx = e2.request(req, "RXcap", lines, 0.93)
    assert req == keep, "the recorded request must not change"
    assert rx["messages"][0]["content"][1]["text"] == "[server] KV cache 75% full; 4 requests waiting"
    assert rx["messages"][2]["content"][1]["text"] == "[server] KV cache 93% full; 5 requests waiting" + CUE
    desc = rx["tools"][1]["description"]
    assert desc.startswith("Pause this session for `seconds`") and "{kv" not in desc
    assert 'reads "[server] KV cache 90% full; 12 requests waiting", call your next tools and add wait' in desc
    assert 'reads "[server] KV cache 55% full; 2 requests waiting", do not call wait.' in desc
    rc = e2.request(req, "RC", lines, 0.62)
    assert rc["messages"][0]["content"][1]["text"] == "[server] KV cache 90% full; 4 requests waiting" + CUE
    assert rc["messages"][2]["content"][1]["text"] == "[server] KV cache 62% full; 5 requests waiting"
    assert "Example:" not in rc["tools"][1]["description"]
    assert rc["system"] == rx["system"], "both use the paragraph wait_kv"
    assert e2.capped([(None, 3), (0.5, 1), (0.99, 2)]) == [(None, 3), (0.5, 1), (0.75, 2)]
    for cond in e2.CONDS:
        assert ek.strip_variable(e2.request(req, cond, lines, 0.93)) == ek.strip_variable(req), cond
    assert e2.request(req, "B0", lines, None) is req


def _items() -> list[dict]:
    """3 work items whose recorded history holds a line at >= 80 %, and 1 final item, from the confirm pool."""
    pool = ek.load_items()["confirm"]
    work = [it for it in pool if it["stratum"] == "work" and it["prior_high"] >= 1][:3]
    return work + [it for it in pool if it["stratum"] == "final"][:1]


def _e2e() -> tuple[dict, list]:
    from aiohttp import web
    import ef_render
    items = _items()
    by_len, seen = {}, []
    for sess, row, rk, variants in e2.chains(items):
        calls = next(it["calls"] for it in items if (it["sid"], it["turn"], it["j"]) == (sess["sid"], row["turn"],
                                                                                          row["j"]))
        for _, req in variants:
            n = len(cf.render_ids((req, sess["template"])))
            assert by_len.get(n, calls) == calls, "two test prompts of the same length"
            by_len[n] = calls
    tok = ef_render.tokenizer()
    line = re.compile(r"\[server\] KV cache (\d+)% full; \d+ requests waiting(?![\\\"])")

    async def completions(request):
        body = await request.json()
        seen.append({k: v for k, v in body.items() if k != "prompt"})
        n = body.get("n", 1)
        if body["max_tokens"] == 1:
            return web.json_response({"choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                                      "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 1}})
        calls = by_len[len(body["prompt"])]
        shown = [int(x) for x in line.findall(tok.decode(body["prompt"]))]
        wait = bool(calls) and bool(shown) and shown[-1] >= 80 and all(x < 80 for x in shown[:-1])
        text = gen_for(calls, wait)
        return web.json_response({"choices": [{"index": i, "text": text, "finish_reason": "stop"} for i in range(n)],
                                  "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 7 * n}})
    fake = Fake([("POST", "/v1/completions", completions)])
    tmp = Path(tempfile.mkdtemp(prefix="kvwait2_e2e_"))
    saved = (e2.OUT, e2.ITEMS, ek.TABLES, e2.changed, ek.RENDERERS, e2.INFLIGHT)
    try:
        e2.OUT, e2.ITEMS, ek.TABLES, ek.RENDERERS, e2.INFLIGHT = tmp / "kvwait2", tmp / "items.json", tmp / "t", 2, 2
        e2.changed = lambda: []
        e2.ITEMS.write_text(json.dumps({"items": items}))
        assert e2.run(fake.url) == 0
        first = len(seen)
        assert e2.run(fake.url) == 0                   # resumes: everything is on disk already
        assert len(seen) == first, "a second run must not send anything"
        res = json.loads((ek.TABLES / "kvwait2.json").read_text())
        assert "**Readings**" in (ek.TABLES / "kvwait2.md").read_text()
    finally:
        e2.OUT, e2.ITEMS, ek.TABLES, e2.changed, ek.RENDERERS, e2.INFLIGHT = saved
    return res, seen


def test_end_to_end_history_shows_and_the_example_does_not():
    res, seen = _e2e()
    assert all(b["n"] == ek.S for b in seen if b["max_tokens"] > 1)
    vs = res["variants"]
    for c in ("RC", "RX"):
        assert vs[f"{c}@0.93"]["work"]["wait"] == 0.0 and vs[f"{c}cap@0.93"]["work"]["wait"] == 1.0, c
        assert vs[f"{c}@0.62"]["work"]["wait"] == 0.0 and vs[f"{c}cap@0.62"]["work"]["wait"] == 0.0, c
        assert vs[f"{c}cap@0.93"]["final"]["wait"] == 0.0 and vs[f"{c}cap@0.93"]["work"]["kept"] == 1.0, c
    assert res["contrasts"]["history"][0] == 1.0 and res["contrasts"]["example"][0] == 0.0
    r = res["readings"]
    assert set(r["usable"]) == {"RCcap", "RXcap"} and r["history"] == "holds it back", r
    assert r["example"] == "not shown to help", r


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
