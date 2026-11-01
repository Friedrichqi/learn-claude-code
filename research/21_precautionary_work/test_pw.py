"""CPU tests of research/21_precautionary_work: the shared rules, the census values, and the compliance probe end to
end against a fake /v1/completions server.

Tests that read topic 20's replay records skip when they are absent (they live on Drive, not in git). Runs as a
script with the vLLM venv (it has no pytest):

    /data/fritz/venv/bin/python research/21_precautionary_work/test_pw.py
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import pw_rules as pr  # noqa: E402
import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

DIR = Path(__file__).resolve().parent


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


need_main = needs((cf.CF / "main" / "records.jsonl").exists(), "topic 20's main replay records not on disk")
_DATA = {}


def main_run() -> dict:
    if "main" not in _DATA:
        import rd_analyze as ra
        _DATA["main"] = ra.load_run("main")
    return _DATA["main"]


# -- shared rules ------------------------------------------------------------------------------------------------------
def test_append_line_like_the_harness():
    msgs = [{"role": "user", "content": "task"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}]
    before = json.dumps(msgs)
    out = pr.append_line(msgs, "L")
    assert json.dumps(msgs) == before                                   # the input is not changed
    assert out[-1]["content"][-1] == {"type": "text", "text": "L"} and out[-1]["content"][0]["type"] == "tool_result"
    assert out[:2] == msgs[:2] and len(out) == 3
    out = pr.append_line([{"role": "user", "content": "task"}], "L")      # a string message becomes blocks
    assert out == [{"role": "user", "content": [{"type": "text", "text": "task"}, {"type": "text", "text": "L"}]}]
    out = pr.append_line(msgs[:2], "L")                                   # not the user's turn: a new user message
    assert out[-1] == {"role": "user", "content": [{"type": "text", "text": "L"}]}
    req = {"system": "s", "tools": [], "messages": msgs}
    assert pr.with_line(req, "L")["system"] == "s" and req["messages"] is msgs


def test_long_class():
    yes = {"cd /app && python -m pytest tests/ -x": "test", "go test ./...": "test", "npm test -- --watch=false": "test",
           "make -j4 opt 2>&1 | tail": "build", "npx tsc --noEmit": "build", "cd /app && go build ./...": "build",
           "pip install grpcio==1.73.0": "install", "apt-get update && apt-get install -y gcc": "install",
           "curl -sL https://x/y.tar.gz -o y.tgz": "download", "git clone https://github.com/a/b": "download",
           "cd /app && python3 train_model.py 2>&1": "run", "sleep 15 && cat log": "run"}
    for cmd, cls in yes.items():
        assert pr.long_class(cmd) == cls, (cmd, pr.long_class(cmd))
    for cmd in ("ls -la /app", "cat src/a.py | head -50", "grep -rn foo pkg/", "sed -n 1,40p x.py", "echo done"):
        assert pr.long_class(cmd) is None, cmd
    assert pr.long_calls([{"name": "bash", "input": {"command": "pytest -q"}}, {"name": "read_file", "input": {}}]) == ["test"]


def test_lines_differ_only_in_text():
    assert pr.LINE != pr.NEUTRAL and "long-running" in pr.LINE and "capacity" not in pr.NEUTRAL.lower()


# -- census -----------------------------------------------------------------------------------------------------------
@need_main
def test_census_reproduces_the_planned_values():
    import pw_census
    c = pw_census.census()
    assert not pw_census.check(c), pw_census.check(c)


# -- the compliance probe ------------------------------------------------------------------------------------------
@need_main
def test_probe_items():
    import pw_probe
    items = pw_probe.build_items(main_run())
    n = items["counts"]
    assert n == {"requests": 302, "pair_requests": 141, "pairs": 144, "pairs_long": 76, "premature": 61,
                 "spurious": 100, "fresh_c": 40}, n
    again = pw_probe.build_items(main_run())
    assert json.dumps(again, sort_keys=True) == json.dumps(items, sort_keys=True)      # deterministic
    for it in items["requests"]:
        for p in it["pair"]:
            assert p["k"] <= it["j"] < p["h"]
        for p in it["premature"]:
            assert p["h"] == it["j"] + 1
        assert not (it["spurious"] and (it["pair"] or it["premature"]))


def test_boot_is_a_cluster_mean():
    import pw_probe
    units = [("a", 1.0), ("a", 0.0), ("b", 1.0)]
    m, lo, hi = pw_probe.boot(units, random.Random(1))
    assert abs(m - 2 / 3) < 1e-12 and 0.0 <= lo <= m <= hi <= 1.0
    assert pw_probe.boot(units, random.Random(1)) == (m, lo, hi)
    assert pw_probe.boot([], random.Random(1)) == (None, None, None)


def gen_for(calls: list[dict]) -> str:
    inv = "".join(f'<invoke name="{c["name"]}">' + "".join(
        f'<parameter name="{k}">{v if isinstance(v, str) else json.dumps(v)}</parameter>\n'
        for k, v in c["input"].items()) + "</invoke>\n" for c in calls)
    return f"ok\n</think>\n\n<minimax:tool_call>\n{inv}</minimax:tool_call>[e~[" if calls else "done\n</think>\n\nanswer[e~["


@need_main
def test_probe_end_to_end_with_fake_server():
    """The fake answers F prompts as a compliant model (round j's calls plus every later round up to the target) and
    N prompts with round j's recorded calls; the scores must read 1 and 0."""
    from aiohttp import web
    import pw_probe
    data = main_run()
    sessions, rows = pw_probe.context(data)
    items = pw_probe.build_items(data)["requests"]
    pick = [next(it for it in items if any(p["long"] and p["h"] > it["j"] + 1 for p in it["pair"])),
            next(it for it in items if it["pair"] and all(p["h"] == it["j"] + 1 for p in it["pair"])),
            next(it for it in items if it["premature"]), next(it for it in items if it["spurious"])]
    by_len, seen = {}, []
    jobs = []
    for it in pick:
        sess, row = sessions[it["sid"]], rows[(it["sid"], it["turn"], it["j"])]
        cwd = cf.session_cwd(sess)
        hs = [p["h"] for p in it["pair"] + it["premature"]]
        comply = [c for m in range(it["j"], max(hs, default=it["j"]) + 1)
                  for c in rows[(it["sid"], it["turn"], m)]["calls"]]
        if it["spurious"]:
            comply = row["calls"] + [{"name": "bash", "input": {"command": "cd /app && python -m pytest -x"}}]
        reqs = pw_probe.requests_for({**it, "fresh_c": False}, sess, row)
        for v, req in reqs.items():
            n = len(cf.render_ids((req, sess["template"])))
            assert n not in by_len, "two test prompts of the same length"
            by_len[n] = gen_for(comply if v == "F" else row["calls"])
            rk = {lv: cf.key_set(row["calls"], cwd, lv) for lv in ("target", "exact", "loose")}
            jobs.append((sess, row, v, req, rk, cwd))

    async def completions(request):
        body = await request.json()
        seen.append(body)
        n = body.get("n", 1)
        if body["max_tokens"] == 1:
            return web.json_response({"choices": [{"index": 0, "text": "x", "finish_reason": "length"}],
                                      "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 1}})
        text = by_len[len(body["prompt"])]
        return web.json_response({"choices": [{"index": i, "text": text, "finish_reason": "stop"} for i in range(n)],
                                  "usage": {"prompt_tokens": len(body["prompt"]), "completion_tokens": 7 * n}})

    app = web.Application()
    app.router.add_post("/v1/completions", completions)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 0)
    loop.run_until_complete(site.start())
    port = site._server.sockets[0].getsockname()[1]
    threading.Thread(target=loop.run_forever, daemon=True).start()
    out = Path(tempfile.mkdtemp(prefix="pw_probe_")) / "e2e"
    run = pw_probe.ProbeRunner(f"http://127.0.0.1:{port}", out, 4, 0.5, 4, 2)
    asyncio.run(run.run_items(jobs))
    recs = rr.read_jsonl(out / "records.jsonl")
    assert len(recs) == len(jobs) and all(r["status"] == "ok" for r in recs)
    assert all(b.get("top_k") == 40 and b.get("n") == 4 for b in seen if b["max_tokens"] > 1)
    want = {(it["sid"], it["turn"], it["j"], v) for it in pick for v in ("F", "N")}
    texts = pw_probe.read_texts(out / "texts.jsonl", want)
    for it in pick:
        cwd = cf.session_cwd(sessions[it["sid"]])
        sf = pw_probe.score_request(it, texts[(it["sid"], it["turn"], it["j"], "F")], rows, cwd)
        sn = pw_probe.score_request(it, texts[(it["sid"], it["turn"], it["j"], "N")], rows, cwd)
        assert sf["n"] == sn["n"] == 4
        for p in it["pair"]:
            assert all(s["valid"][p["h"]] and s["early"][p["h"]] for s in sf["samples"]), (it["j"], p)
            assert not any(s["early"][p["h"]] for s in sn["samples"]) or rows[(it["sid"], it["turn"], p["h"])]["calls"] \
                == rows[(it["sid"], it["turn"], it["j"])]["calls"]
        for p in it["premature"]:
            assert all(s["premature"][p["h"]] for s in sf["samples"])
        if it["spurious"]:
            assert all(s["long"] for s in sf["samples"])
        assert all(s["kept"] for s in sf["samples"] + sn["samples"])
    # the whole analysis on these samples: items without samples are skipped, the sampled ones read 1 and 0
    saved = pw_probe.PROBE
    try:
        pw_probe.PROBE = out.parent
        (out.parent / "items.json").write_text(json.dumps(pw_probe.build_items(data)))
        res = pw_probe.analyze("e2e")
        lines = pw_probe.render(res, "e2e")
    finally:
        pw_probe.PROBE = saved
    c = res["conditions"]
    assert c["pair_all.valid.F"][0] == 1.0 and c["pair_all.valid.F-N"][0] > 0.5, c["pair_all.valid.F-N"]
    assert c["premature.F"][0] == 1.0 and c["spurious_long.F"][0] == 1.0
    assert res["gate"]["out_ratio_F/N"] == 1.0 and any("Probe part of G2" in x for x in lines)
    assert res["for_sim"]["reference"] == "C" and res["drift"]["requests"] == 0


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
