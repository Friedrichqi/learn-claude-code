"""CPU tests of research/20_round_dependency: the rounds table, the static census helpers, the counterfactual
requests, parsing and keys, and an end-to-end replay against a fake /v1/completions server.

Tests that read recorded traces skip when the data folders are absent (they live on Drive, not in git). Runs as
a script with the vLLM venv, like topic 19's tests (that venv has no pytest):

    /data/fritz/venv/bin/python research/20_round_dependency/test_rd.py
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402
import rd_static as rs  # noqa: E402

S000 = rr.T19_CELLS_ROOT / "w08_none" / "runs" / "MU-w08_none-S000" / "MU-w08_none-S000.meta.json"


class Skip(Exception):
    pass


def needs(cond: bool, why: str):
    def wrap(fn):
        def run(*a, **k):
            if not cond:
                raise Skip(why)
            return fn(*a, **k)
        run.__name__ = fn.__name__
        return run
    return wrap


need_t19 = needs(S000.exists(), "topic 19 cells not on disk")
need_rounds = needs((rr.ROUNDS / "rounds_t19.jsonl.xz").exists(), "rounds table not built")
need_pilot = needs((cf.CF / "sample_pilot.json").exists(), "pilot sample not drawn")


# -- static helpers ------------------------------------------------------------------------------------------------
def test_found_respects_identifier_boundaries():
    assert rs.found("see profiling_sandbox/typer/core.py:12: def", "core.py")
    assert not rs.found("score.py", "core.py") and not rs.found("_core.py", "core.py")
    assert not rs.found("all_literal_values", "literal_values") and rs.found("x literal_values(", "literal_values")
    assert not rs.found("anything", "")


def test_overlap_and_conflict():
    assert rs.overlap({"a/"}, {"a/b.py"}) and not rs.overlap({"a.py"}, {"b.py"})
    assert rs.overlap({"*"}, {"x"}) and not rs.overlap({"*"}, {"#todo"}) and rs.overlap({"#todo"}, {"#todo"})
    assert rs.conflict({"a.py"}, set(), {"a.py"}, {"a.py"})            # read then edit: write-after-read
    assert not rs.conflict({"a.py"}, set(), {"b.py"}, set())


def test_effects_of_tools():
    call = lambda n, **k: {"name": n, "input": k}
    assert rs.effects(call("read_file", path="./x/y.py"), "...", None) == ({"x/y.py"}, set(), False)
    assert rs.effects(call("edit_file", path="a.py", old_text="q", new_text="r"), "Edited a.py", None)[1] == {"a.py"}
    assert rs.effects(call("edit_file", path="a.py", old_text="q", new_text="r"), "Error: not found", None)[1] == set()
    assert rs.effects(call("bash", command="grep -rn foo src/"), "", None) == ({"src"}, set(), False)
    assert rs.effects(call("bash", command="python3 x.py"), "", None) == ({"*"}, {"*"}, True)
    assert rs.effects(call("bash", command="rm x"), "Permission denied: ...", None) == (set(), set(), False)
    assert rs.effects(call("read_file", path="/app/x.go"), "", "/app") == ({"x.go"}, set(), False)


def test_salient_strings():
    s = rs.salient({"name": "bash", "input": {"command": 'grep -rn "literal_values" profiling_sandbox/typer/'}})
    assert ("pattern", "literal_values") in s and ("path", "profiling_sandbox/typer") in s
    s = rs.salient({"name": "edit_file", "input": {"path": "a.py", "old_text": "\n  def foo_bar(x):\n", "new_text": ""}})
    assert ("anchor", "def foo_bar(x):") in s
    s = rs.salient({"name": "bash", "input": {"command": "pip install docutils -q"}})
    assert ("ident", "docutils") in s and ("ident", "install") not in s


def test_session_static_greedy_groups():
    sess = {"turns": {"t1": {"prompt": "Rename foo_bar in pkg/"}}}
    def row(j, calls, results, final=False):
        return {"src": "t19", "sid": "s", "group": "g", "kind": "M2", "turn": "t1", "j": j, "call": j,
                "calls": calls, "results": results, "text": "", "final": final, "append_ok": True, "tail_ok": True,
                "results_src": "next", "tool_ms": [1.0] * len(calls), "np": 10, "cached": 0, "out": 5}
    grep = {"id": "c0", "name": "bash", "input": {"command": "grep -rn foo_bar pkg/"}}
    e1 = {"id": "c1", "name": "edit_file", "input": {"path": "pkg/a.py", "old_text": "def foo_bar():", "new_text": ""}}
    e2 = {"id": "c2", "name": "edit_file", "input": {"path": "pkg/b.py", "old_text": "x = foo_bar()", "new_text": ""}}
    rows = [row(0, [grep], ["pkg/a.py:3:def foo_bar():\npkg/b.py:9:x = foo_bar()"]),
            row(1, [e1], ["Edited pkg/a.py"]), row(2, [e2], ["Edited pkg/b.py"]), row(3, [], [], final=True)]
    out = rs.session_static(sess, rows, "system", None)
    by_j = {r["j"]: r for r in out}
    assert by_j[1]["lex_prev"] and by_j[1]["lex_deps"] == [0]            # the edit copies the grep's line
    assert not by_j[2]["lex_prev"] and by_j[2]["lex_deps"] == [0]        # the second edit needs the grep, not edit 1
    assert by_j[2]["joins"] and by_j[2]["commutes"] and by_j[2]["mgroup"] == by_j[1]["mgroup"]
    assert by_j[1]["mgroup"] != by_j[0]["mgroup"] and by_j[3]["level"] == 3


# -- rounds table ---------------------------------------------------------------------------------------------------
@need_t19
def test_t19_rounds_of_one_session():
    sess, rows = rr.t19_one(str(S000))
    assert sess["rounds"] == len(rows) == 109 and sess["templates"] == ["E1", "E2", "M2", "M3"]
    assert [r["n_turn"] for r in rows if r["j"] == 0] == [5, 9, 9, 86]
    tool_rounds = [r for r in rows if not r["final"]]
    assert all(r["results_src"] in ("next", "trace") for r in tool_rounds)
    assert sum(r["final"] for r in rows) == 4
    edit = next(r for r in rows if r["turn"] == "turn_000003" and r["j"] == 2)
    assert edit["calls"][0]["name"] == "edit_file" and edit["results"][0].startswith("Edited")


@need_rounds
def test_calls_per_turn_match_topic19():
    """Topic 19's main table reports 10.3-11.3 lead calls per turn in `none`; the rounds table must agree."""
    sessions, rows = rr.load("t19")
    for cell in ("w08_none", "w16_none", "w32_none"):
        turns = {}
        for r in rows:
            if r["group"] == cell:
                turns[(r["sid"], r["turn"])] = r["n_turn"]
        mean = sum(turns.values()) / len(turns)
        assert 9.0 <= mean <= 12.0, (cell, mean)


# -- counterfactual requests, parsing, keys --------------------------------------------------------------------------
GEN = ('I should read the module next.\n</think>\n\n<minimax:tool_call>\n<invoke name="read_file">'
       '<parameter name="path">pkg/a.py</parameter>\n<parameter name="offset">12</parameter>\n</invoke>\n'
       '<invoke name="bash"><parameter name="command">grep -rn "foo_bar" pkg/</parameter>\n</invoke>\n'
       '</minimax:tool_call>[e~[')


def test_parse_generation_and_keys():
    g = cf.parse_generation(GEN)
    assert g["closed"] and [c["name"] for c in g["calls"]] == ["read_file", "bash"]
    assert g["calls"][0]["input"] == {"path": "pkg/a.py", "offset": "12"}
    rec = [{"name": "read_file", "input": {"path": "./pkg/a.py", "offset": 12}},
           {"name": "bash", "input": {"command": "grep -rn foo_bar pkg"}}]
    assert cf.key_set(g["calls"], None, "target") == cf.key_set(rec, None, "target")
    assert cf.keys_of(g["calls"][0], None, "exact") != cf.keys_of(rec[0], None, "exact")      # './' differs
    assert cf.matches({("a",)}, {("a",), ("b",)}) and not cf.matches(set(), {("a",)})
    assert cf.key_set([], None, "target") == {("final",)}
    assert cf.parse_generation("still thinking")["calls"] == []
    assert cf.norm_value('[{"a": 1}]') == cf.norm_value([{"a": 1}])


def _pair_rows():
    sample, sessions, rows = cf.load_sample("pilot")
    for sid, rs_ in rows.items():
        for a, b in zip(rs_, rs_[1:]):
            if a["turn"] == b["turn"] and a["calls"] and a.get("append_ok") and not b["final"]:
                return sessions[sid], a, b
    raise Skip("no pair")


@need_pilot
def test_pending_masks_only_the_previous_results():
    sess, a, b = _pair_rows()
    ctrl, pend = cf.control_request(b), cf.pending_request(b, a)
    assert len(ctrl["messages"]) == len(pend["messages"])
    diff = [i for i, (x, y) in enumerate(zip(ctrl["messages"], pend["messages"])) if x != y]
    assert len(diff) == 1
    ids = {c["id"] for c in a["calls"]}
    blocks = [x for x in pend["messages"][diff[0]]["content"] if x.get("type") == "tool_result"]
    assert {x["tool_use_id"] for x in blocks} == ids
    assert all(x["content"].startswith("[Background task bg_") for x in blocks)
    assert json.dumps(cf.merged_request([a], b), sort_keys=True) == json.dumps(pend, sort_keys=True)


@need_pilot
def test_merged_request_pairs_every_tool_use():
    sample, sessions, rows = cf.load_sample("pilot")
    for sid, rs_ in rows.items():
        for t in sorted({r["turn"] for r in rs_}):
            turn = [r for r in rs_ if r["turn"] == t and r["calls"]]
            if len(turn) >= 3 and all(r.get("tail_ok") for r in turn[:2]):
                m = cf.merged_request(turn[:2], turn[2])
                uses = [x["id"] for msg in m["messages"] if msg["role"] == "assistant"
                        and not isinstance(msg["content"], str) for x in msg["content"] if x.get("type") == "tool_use"]
                results = [x["tool_use_id"] for msg in m["messages"] if msg["role"] == "user"
                           and not isinstance(msg["content"], str) for x in msg["content"]
                           if x.get("type") == "tool_result"]
                assert sorted(uses) == sorted(results)
                assert {c["id"] for c in turn[1]["calls"]} <= set(uses)
                return
    raise Skip("no three-round turn")


# -- end to end against a fake server --------------------------------------------------------------------------------
@need_pilot
def test_runner_end_to_end_with_fake_server():
    """The fake answers every sampling request with the recorded next call of the round whose prompt length it
    sees (so C and P match and the chain grows), and 1-token warm-ups with one token."""
    from aiohttp import web
    sample, sessions, rows = cf.load_sample("pilot")
    sid = next(s for s in rows if sessions[s]["src"] == "t19")
    turn_rows = [r for r in rows[sid] if r["turn"] == rows[sid][0]["turn"]][:4]
    calls_by_len = {}
    seen = []

    def gen_for(calls):
        inv = "".join(f'<invoke name="{c["name"]}">' + "".join(
            f'<parameter name="{k}">{v if isinstance(v, str) else json.dumps(v)}</parameter>\n'
            for k, v in c["input"].items()) + "</invoke>\n" for c in calls)
        return f"ok\n</think>\n\n<minimax:tool_call>\n{inv}</minimax:tool_call>[e~[" if calls else "done\n</think>\n\nanswer[e~["

    async def completions(request):
        body = await request.json()
        seen.append(body)
        n = body.get("n", 1)
        if body["max_tokens"] == 1:
            return web.json_response({"choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                                      "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 1}})
        text = calls_by_len.get(len(body["prompt"]), gen_for([]))
        return web.json_response({"choices": [{"index": i, "text": text, "finish_reason": "stop"} for i in range(n)],
                                  "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 5 * n}})

    app = web.Application()
    app.router.add_post("/v1/completions", completions)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 0)
    loop.run_until_complete(site.start())
    port = site._server.sockets[0].getsockname()[1]
    threading.Thread(target=loop.run_forever, daemon=True).start()
    # control prompts are the recorded ones: answer each with its own recorded calls; P/M prompts of the next round
    # differ in length, so answer the pending prompt of round j with round j's calls as well
    for j, r in enumerate(turn_rows):
        calls_by_len[r["np"]] = gen_for(r["calls"])
        if j > 0:
            p = cf.pending_request(r, turn_rows[j - 1])
            if p is not None:
                calls_by_len[len(cf.render_ids((p, sessions[sid]["template"])))] = gen_for(r["calls"])
    tmp_path = Path(tempfile.mkdtemp(prefix="rd_e2e_"))
    run = cf.Runner(f"http://127.0.0.1:{port}", tmp_path / "e2e", S=4, tau=0.5, inflight=4, renderers=2)
    asyncio.run(run.run({sid: sessions[sid]}, {sid: turn_rows}, turns_inflight=2))
    recs = rr.read_jsonl(tmp_path / "e2e" / "records.jsonl")
    by = {(r["j"], r["variant"]): r for r in recs}
    assert all(by[(j, "C")]["render_ok"] for j in range(len(turn_rows)))
    assert all(by[(j, "C")]["p"]["target"] == 1.0 for j in range(len(turn_rows)) if turn_rows[j]["calls"])
    chain = next(r for r in recs if r["variant"] == "chain")["chain"]
    assert chain[0]["start"] and len(chain) == len(turn_rows)
    assert any(not c["start"] for c in chain) or all(r["final"] for r in turn_rows[1:])
    assert all(b.get("skip_special_tokens") is False and b.get("top_k") == 40 for b in seen if b["max_tokens"] > 1)


# -- the load replay (S5) ---------------------------------------------------------------------------------------------
need_pilot_run = needs((cf.CF / "pilot" / "records.jsonl").exists(), "pilot run not done")


@need_pilot_run
def test_world_requests_are_valid_and_merge_drops_thinking():
    import rd_loadreplay as lr
    _, sessions, rows = cf.load_sample("pilot")
    groups = lr.chain_groups("pilot")
    checked = 0
    for sid, rs_ in rows.items():
        by_turn = defaultdict(list)
        for r in rs_:
            by_turn[r["turn"]].append(r)
        done = []
        for t in sorted(by_turn):
            if (sid, t) not in groups:
                break
            trs = {r["j"]: r for r in by_turn[t]}
            for g in groups[(sid, t)]:
                grp = [trs[j] for j in g]
                for form in ("merge", "reorder"):
                    req = lr.world_request(cf.base_request(grp[0]), done, None, form)
                    uses, results = set(), set()
                    for m in req["messages"]:
                        if isinstance(m["content"], str):
                            continue
                        uses |= {b["id"] for b in m["content"] if b.get("type") == "tool_use"}
                        results |= {b["tool_use_id"] for b in m["content"] if b.get("type") == "tool_result"}
                    assert uses == results, (sid, t, g, form)
                    if form == "merge" and any(len(x) > 1 for x in done):
                        rec = cf.base_request(grp[0])
                        assert len(json.dumps(req)) < len(json.dumps(rec))
                    checked += 1
                if len(grp) > 1:
                    open_req = lr.world_request(cf.base_request(grp[1]), done, grp[:1], "reorder")
                    holders = [b for m in open_req["messages"] if not isinstance(m["content"], str)
                               for b in m["content"] if b.get("type") == "tool_result"
                               and str(b.get("content", "")).startswith("[Background task bg_")]
                    assert {b["tool_use_id"] for b in holders} >= {c["id"] for c in grp[0]["calls"]}
                done.append(grp)
    if not checked:
        raise Skip("no complete pilot turns")


@need_pilot_run
def test_loadreplay_cell_against_fake_server():
    """Every variant runs a few sessions against a fake server; merge sends fewer calls than rec, reorder sends
    early calls at priority 1 and every recorded round of a finished session is reported complete."""
    import argparse
    import rd_loadreplay as lr
    from aiohttp import web
    tmp = Path(tempfile.mkdtemp(prefix="rd_load_"))
    lr.LOAD = tmp
    lr.prepare("pilot", "t19", "fake", None)
    plan = json.loads((tmp / "plan_fake.json").read_text())
    plan["units"] = plan["units"][:1]
    seen = []

    async def completions(request):
        body = await request.json()
        seen.append({"priority": body.get("priority"), "n": len(body["prompt"])})
        return web.json_response({"choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                                  "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": body["max_tokens"],
                                            "prompt_tokens_details": {"cached_tokens": 0}}})
    app = web.Application()
    app.router.add_post("/v1/completions", completions)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 0)
    loop.run_until_complete(site.start())
    port = site._server.sockets[0].getsockname()[1]
    threading.Thread(target=loop.run_forever, daemon=True).start()
    calls = {}
    for variant in ("rec", "merge", "reorder_low"):
        seen.clear()
        a = argparse.Namespace(url=f"http://127.0.0.1:{port}", plan="fake", variant=variant, n=1, out=f"fake_{variant}",
                               warmup_s=0, window_s=40, stagger_s=0, seed=1, renderers=4,
                               priority_policy=variant.startswith("reorder"), time_scale=0.0)
        asyncio.run(lr.Cell(a, plan).main())
        cell = tmp / "cells" / f"fake_{variant}"
        crec = rr.read_jsonl(cell / "calls.jsonl")
        rounds = rr.read_jsonl(cell / "rounds.jsonl")
        assert crec and all(c["status"] == "ok" for c in crec)
        first = crec[0]["label"]
        calls[variant] = sum(1 for c in crec if c["label"] == first)
        n_rounds = sum(len(t["rounds"]) for t in plan["units"][0]["turns"])
        done = {(r["turn"], r["j"]) for r in rounds if r["label"] == first}
        assert len(done) == n_rounds, (variant, len(done), n_rounds)
        if variant == "reorder_low":
            assert any(s["priority"] == 1 for s in seen) and any(s["priority"] == 0 for s in seen)
    assert calls["merge"] < calls["rec"] == calls["reorder_low"]


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}", flush=True)
            except Skip as exc:
                print(f"SKIP {name}: {exc}", flush=True)
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
    sys.exit(1 if failures else 0)
