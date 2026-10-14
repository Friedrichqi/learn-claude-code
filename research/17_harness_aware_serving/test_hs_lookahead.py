"""Tests for E0b (hs_lookahead) and E0f (hs_hysteresis): s15's edit rules on small synthetic histories, the
lookahead layouts, and the hysteresis replay. No tokenizer model: a byte-level fake encoder (pytest + numpy only).

    python -m pytest research/17_harness_aware_serving/test_hs_lookahead.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_hysteresis as hy  # noqa: E402
import hs_lookahead as la  # noqa: E402

STAGE = "/stage/jobs/m/b/q__c00/t__x/agent/s15"
BIG = 10 ** 9


def env(limit: int = BIG) -> la.Env:
    return la.Env(STAGE, limit)


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def tool_call(k: int, n: int = 1, name: str = "bash") -> dict:
    return {"role": "assistant", "content": [
        {"id": f"toolu_{k:05d}_{i}", "caller": None, "input": {"command": f"echo {k} {i}"}, "name": name,
         "type": "tool_use", "toolset_name": None} for i in range(n)]}


def results(k: int, text: str = "ok", n: int = 1) -> dict:
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{k:05d}_{i}", "content": text}
                                        for i in range(n)]}


def history(rounds: int, size: int = 10) -> list[dict]:
    msgs = [user("task")]
    for k in range(rounds):
        msgs += [tool_call(k), results(k, "x" * size)]
    return msgs


def byte_tok() -> la.Tok:
    return la.Tok(lambda texts: [list(t.encode("utf-8")) for t in texts])


# -- snip: rolling and hysteresis --------------------------------------------------------------------------------

def test_snip_leaves_50_messages_alone():
    msgs = history(24) + [user(la.REMINDER)]
    assert len(msgs) == 50
    assert la.snip_compact(env(), msgs) is msgs


def test_rolling_snip_keeps_head_marker_and_last_46():
    e = env()
    msgs = history(25)                                   # 51 messages: prompt + 25 (call, result) pairs
    out = la.snip_compact(e, msgs)
    assert len(out) == 50
    assert out[:3] == msgs[:3]
    assert out[3] == user(f"[2 messages archived at {STAGE}/.transcripts/transcript_00000.jsonl]")
    assert out[4:] == msgs[5:]
    assert e.counter == 1 and e.applied == ["snip"]


def test_snip_keeps_tool_use_with_its_result():
    msgs = history(25) + [user(la.REMINDER)]             # 52: the cut would start on a tool_result message
    out = la.snip_compact(env(), msgs)
    assert len(out) == 51
    assert out[4] is msgs[5] and la.message_has_tool_use(out[4]) and la.is_tool_result_message(out[5])


def test_rolling_snip_cuts_again_every_call_and_rewrites_the_marker():
    e = env()
    s1 = la.snip_compact(e, history(25))
    s2 = la.snip_compact(e, s1 + [tool_call(25), results(25)])
    assert len(s2) == 50
    assert s2[3]["content"] == f"[3 messages archived at {STAGE}/.transcripts/transcript_00001.jsonl]"
    assert s2[4] is s1[6]                                # the whole tail moved one round toward the head


def test_snip_is_stable_without_new_messages():
    e = env()
    out = la.snip_compact(e, history(25) + [user(la.REMINDER)])
    assert len(out) == 51                                # pair-adjusted cut: middle is just the marker now
    assert la.snip_compact(e, out) is out
    assert e.counter == 1


def test_hysteresis_trims_to_30_then_appends_until_50():
    e = env()
    out = la.snip_compact(e, history(25), keep=30)
    assert len(out) == 30 and la.is_marker_message(out[3])
    msgs, sizes = out, []
    for k in range(25, 45):
        msgs = la.snip_compact(e, msgs + [tool_call(k), results(k)], keep=30)
        sizes.append(len(msgs))
    # 30 + 2 x 10 = 50 is kept; the 11th round reaches 52 and is cut back to 30; then nine more rounds
    assert sizes == list(range(32, 51, 2)) + [30] + list(range(32, 49, 2))
    assert e.counter == 2 and msgs[3]["content"].startswith("[23 messages archived at ")


# -- the other rules ---------------------------------------------------------------------------------------------

def test_micro_compact_placeholders_old_consumed_results_only():
    e = env()
    msgs = history(8, size=500) + [tool_call(8)] + [results(8, "y" * 500)]
    out = la.micro_compact(e, msgs, target_chars=0)
    # 9 results; the last is unseen (after the last assistant), the 3 newest consumed ones are kept
    changed = [i for i, (a, b) in enumerate(zip(msgs, out)) if a is not b]
    assert changed == [2, 4, 6, 8, 10]
    assert out[2]["content"][0]["content"] == f"[Earlier tool result saved at {STAGE}/.task_outputs/tool-results/toolu_00000_0.txt]"
    assert e.saved[f"{STAGE}/.task_outputs/tool-results/toolu_00000_0.txt"] == "x" * 500
    assert msgs[2]["content"][0]["content"] == "x" * 500        # the input is never mutated


def test_micro_compact_stops_at_target():
    e = env()
    msgs = history(8, size=500) + [tool_call(8), results(8)]
    full = la.estimate_size(msgs)
    out = la.micro_compact(e, msgs, target_chars=full - 300)
    assert sum(a is not b for a, b in zip(msgs, out)) == 1


def test_fit_tool_results_previews_the_largest_first():
    e = env()
    msgs = history(3, size=50) + [tool_call(3), results(3, "z" * 5000)]
    out = la.fit_tool_results(e, msgs, target_chars=la.estimate_size(msgs) - 1000)
    preview = out[-1]["content"][0]["content"]
    path = f"{STAGE}/.task_outputs/tool-results/toolu_00003_0.txt"
    assert preview == f"<persisted-output>\nFull output: {path}\nPreview:\n{'z' * 1000}\n</persisted-output>"
    assert out[:-1] == msgs[:-1] and e.applied == ["fit"]


def test_tool_result_budget_only_touches_the_newest_message():
    e = env()
    last = results(9, "q" * 31_000, n=7)                 # 217,000 chars > 200,000
    out = la.tool_result_budget(e, history(2) + [tool_call(9, n=7), last])
    contents = [b["content"] for b in out[-1]["content"]]
    assert sum(c.startswith("<persisted-output>") for c in contents) == 1
    assert la.tool_result_budget(e, history(2) + [tool_call(9, 7), last, user(la.REMINDER)])[-2] is last


def test_reactive_compact_keeps_the_last_five_with_their_tool_use():
    e = env()
    msgs = history(5)                                    # 11 messages; the last 5 start on a tool result
    out = la.reactive_compact(e, msgs, "[Reactive compact]\n\nsummary")
    assert out[0] == user("[Reactive compact]\n\nsummary") and out[1:] == msgs[5:]
    assert la.reactive_compact(e, msgs, None)[0].get("_unknown") and e.counter == 2


def test_estimate_size_serializes_assistant_blocks_like_sdk_objects():
    class ToolUseBlock:                                  # what anthropic's BaseModel.__str__ produces
        def __init__(self, d):
            self.d = d

        def __str__(self):
            return "ToolUseBlock(" + ", ".join(f"{k}={v!r}" for k, v in self.d.items()) + ")"

    msgs = history(3)
    live = [{**m, "content": [ToolUseBlock(b) for b in m["content"]]} if m["role"] == "assistant" else m
            for m in msgs]
    assert la.estimate_size(msgs) == len(json.dumps(live, default=str))


def test_todo_counter_follows_agent_loop():
    blocks = lambda *names: [{"type": "tool_use", "name": n, "input": {}} for n in names]  # noqa: E731
    assert la.todo_after_tools(0, blocks("bash", "read_file", "glob")) == 3
    assert la.todo_after_tools(2, blocks("bash", "todo_write", "bash")) == 1
    assert la.todo_after_tools(1, blocks("compact", "cron_create")) == 1          # compact / denied: skipped
    bg = [{"type": "tool_use", "name": "bash", "input": {"run_in_background": True}}]
    assert la.todo_after_tools(1, bg) == 1


# -- edit tokens and layouts --------------------------------------------------------------------------------------

def test_edit_tokens_start_inside_the_rewritten_marker():
    tok, e = byte_tok(), env()
    Rj = la.snip_compact(e, history(25))
    reply = tool_call(25)
    Rn = la.prepare_context(e, Rj + [reply, results(25, "new")])
    cached = Rj + [reply]
    tok.ensure(Rn + cached)
    j, lcp, d = la.divergence(Rn, cached, tok)
    assert j == 3
    a, b = la.render_message(Rn[3]).encode(), la.render_message(Rj[3]).encode()
    assert lcp == next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
    end = la.old_region_end(Rn, set(), tok.key(reply), tok)
    assert Rn[end - 1] == reply and end == len(Rn) - 1
    E = sum(tok.n(m) for m in Rn[3:end]) - lcp
    assert E > 0.8 * sum(tok.n(m) for m in Rn[:end])     # nearly the whole history is re-prefilled


def synth_trial(rounds: int, multi=(), big=(), limit: int = BIG) -> la.Trial:
    """A trial generated by the reimplemented harness loop itself (reminders, snips, events, calls records)."""
    e = la.Env(STAGE, limit)
    msgs, calls, events, r = [user("task")], [], [], 0
    for k in range(rounds):
        if r >= 3:
            msgs = msgs + [user(la.REMINDER)]
            r = 0
        before = e.counter
        msgs = la.prepare_context(e, msgs)
        events += [{"kind": "snip_compact", "lead_calls": k}] * (e.counter - before)
        req = json.loads(json.dumps(msgs))
        reply = tool_call(k, n=2 if k in multi else 1)
        calls.append({"call": k, "purpose": "lead", "request": {"system": "sys", "messages": req, "tools": []},
                      "response": reply["content"], "stop_reason": "tool_use",
                      "usage": {"input_tokens": 1, "output_tokens": 5, "cache_read_input_tokens": 0,
                                "cache_creation_input_tokens": 0}})
        msgs = req + [json.loads(json.dumps(reply)),
                      results(k, ("w" * 4000) if k in big else f"out {k}", n=len(reply["content"]))]
        r = la.todo_after_tools(r, reply["content"])
    return la.Trial("m", "b", Path("/nonexistent/m/b/trial"), STAGE, limit, la.KEEP_TOOLS, calls, events)


def test_lookahead_on_a_synthetic_trial():
    trial = synth_trial(45, multi={27, 30})
    recs, diag = la.e0b_trial(trial, byte_tok())
    assert diag["fidelity_ok"] == len(recs) == 44 and diag["reminder_ok"] == 44
    snips = [r for r in recs if r["cls"] == "snip"]
    assert snips and all(r["E"] > 0 for r in snips)
    assert all(r["known"][1] == r["E"] for r in recs)    # snip layouts are fixed at decode end
    # calls 27 and 30 make two tool calls: the round-start guess (one call) misses the reminders they trigger; at
    # 27 the pair rule absorbs the one-message shift (see the next test), at 30 it does not
    miss = [r for r in recs if r["known"][0] < r["E"]]
    assert [r["j"] for r in miss] == [30] and miss[0]["reason"][0] == "todo reminder"
    assert miss[0]["known"][0] == 0                      # another cut moves every surviving message


def test_one_extra_message_shifts_the_cut_unless_the_pair_rule_absorbs_it():
    reply = tool_call(24, n=2)                           # two calls: todo counter 1 -> 3, a reminder follows
    new = [results(24, n=2), user(la.REMINDER)]
    rnd = la.Round(j={}, n={}, failed=[], kind="tool", reply=reply, new=new)
    h = history(24)                                      # 49 messages; Rj has 50, the guess appends 2, reality 3
    # the guessed cut (index 6) lands on a tool result and moves back to its call; the real one (7) on a call: miss
    # one user message earlier, the guessed cut lands on a call and the real one on its result, which moves back
    for Rj, absorbed in ((h + [user("context")], False), (h[:3] + [user("context")] + h[3:], True)):
        outs = {p: la.predict(env(), Rj, reply, "tool", 1, p, rnd, la.KEEP_TOOLS)[1] for p in la.POINTS}
        assert outs["ii"][3] == outs["iii"][3]           # decode end knows the reminder: same marker, same cut
        assert (outs["i"][3] == outs["iii"][3]) is absorbed


def test_size_triggered_placeholders_are_unknown_at_decode_end():
    big = set(range(20, 45))
    trial = synth_trial(45, big=big, limit=60_000)
    recs, diag = la.e0b_trial(trial, byte_tok())
    assert diag["fidelity_ok"] == len(recs)
    assert any("placeholder" in r["applied"] for r in recs)
    late = [r for r in recs if r["reason"][1] == "tool-result sizes"]
    assert late and all(r["known"][2] == r["E"] for r in late)


# -- hysteresis replay ------------------------------------------------------------------------------------------

def test_hysteresis_replay_cuts_every_tenth_round():
    trial = synth_trial(90)
    tok = byte_tok()
    rolling, d = hy.replay(trial, tok, keep=None)
    assert d["reproduced"] == 89 and not d.get("not_reproduced")
    recs, _ = la.e0b_trial(trial, byte_tok())
    assert [r["E"] for r in rolling[1:]] == [r["E"] for r in recs]
    hyst, _ = hy.replay(trial, tok, keep=30)
    n_roll = sum(r["E"] > 0 for r in rolling)
    n_hyst = sum(r["E"] > 0 for r in hyst)
    assert n_roll > 50 and 4 <= n_hyst <= n_roll / 6
    assert sum(r["E"] for r in hyst) < 0.25 * sum(r["E"] for r in rolling)
    assert max(r["nm"] for r in hyst) <= 51 and min(r["nm"] for r in hyst[60:]) >= 30


def test_edit_work_is_the_prefill_share_of_the_edit_tokens():
    cost = hy.Cost.__new__(hy.Cost)
    cost.models = {"compute": {"beta": [40.0, 0.09, 0.003, 0.0]}}
    cost.tpot = [{"ctx": 1000, "n_seq": 1, "tpot_ms": 25.0}, {"ctx": 9000, "n_seq": 1, "tpot_ms": 29.0}]
    cost.b = cost.models["compute"]["beta"]
    C, E, P = 6000, 5000, 8000
    assert cost.edit(E, P) == pytest.approx(cost.prefill(C, P) - cost.prefill(C - E, P))
    assert cost.decode(100, 4950) == pytest.approx(100 * 27.0)
