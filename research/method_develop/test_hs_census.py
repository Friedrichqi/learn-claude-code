"""CPU tests for E0a (hs_census) and E0c (hs_slack) on synthetic records; no data files are read.

    python -m pytest research/17_harness_aware_serving/test_hs_census.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_census as hcen  # noqa: E402
import hs_slack as hsl  # noqa: E402

MODELS = {"compute": {"beta": [40.0, 0.1, 0.002, 0.0]}, "reuse": {"beta": [30.0, 0.08, 0.003, 0.003]}}
TPOT = [{"ctx": 1000, "n_seq": 1, "tpot_ms": 25.0}, {"ctx": 30000, "n_seq": 1, "tpot_ms": 30.0}]


def usage(P: int, cached: int, out: int, block: int = 16) -> dict:
    """vLLM-style usage: the computed tokens split into full blocks (creation) and the tail (input)."""
    U = P - cached
    return {"input_tokens": U % block, "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": U - U % block, "output_tokens": out}


def msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def call(i: int, t: float, ms: float, messages: list, reply: list, P: int, cached: int, out: int,
         purpose: str = "lead", system: str = "SYS") -> dict:
    return {"call": i, "purpose": purpose, "agent_kind": "one_shot" if purpose == "one_shot" else "lead",
            "t": t, "ms": ms, "usage": usage(P, cached, out),
            "request": {"system": system, "messages": messages, "tools": ["bash"]},
            "response": reply, "tools": [b["name"] for b in reply if b.get("type") == "tool_use"],
            "stop_reason": "tool_use"}


def bash(cmd: str, i: int = 0) -> dict:
    return {"type": "tool_use", "id": f"t{i}", "name": "bash", "input": {"command": cmd}}


def chars(content) -> int:
    return hcen.units({"messages": [{"role": "x", "content": content}]})[-1][1]


@pytest.fixture
def exact_tokens(monkeypatch):
    """One token per character and no per-message template tokens, so `new` is exact."""
    monkeypatch.setattr(hcen, "fit_rho", lambda points: (1.0, True))
    monkeypatch.setattr(hcen, "TAU", 0.0)


# -- E0a: usage, diff, records --------------------------------------------------------------------------------

def test_usage_tokens_follow_vllm_semantics():
    P, cached, U, inp, cr, out = hcen.usage_tokens(usage(1000, 640, 7))
    assert (P, cached, U, out) == (1000, 640, 360, 7)
    assert inp < 16 and inp + cr == U


def test_diff_labels_extension_snip_and_placeholder():
    prev = ["s", "t", "a", "b", "c", "d", "e", "r"]
    assert hcen.diff_labels(prev, prev + ["x"]) == ["prefix"] * 8 + ["fresh"]
    snip = ["s", "t", "a", "b", "M", "e", "r", "x"]                   # c, d archived behind a marker M
    assert hcen.diff_labels(prev, snip) == ["prefix"] * 4 + ["fresh", "moved", "moved", "fresh"]
    placeholder = ["s", "t", "a", "P", "c", "d", "e", "r", "x"]       # b replaced by a placeholder
    assert hcen.diff_labels(prev, placeholder) == ["prefix"] * 3 + ["fresh"] + ["moved"] * 4 + ["fresh"]
    assert hcen.diff_labels([], ["s", "t"]) == ["fresh", "fresh"]


def test_session_records_estimate_new_and_edit(exact_tokens):
    head = [msg("user", "task description")]
    r0 = [bash("ls", 0)]
    tool0 = msg("user", "result zero")
    r1 = [bash("pytest -q", 1)]
    tool1 = msg("user", "result one")
    m1 = head + [{"role": "assistant", "content": r0}, tool0]
    m2 = [msg("user", "[archived]")] + [{"role": "assistant", "content": r0}, tool0,
                                        {"role": "assistant", "content": r1}, tool1]
    new1 = chars("result zero")
    moved2 = chars(r0) + chars("result zero") + chars(r1)
    new2 = chars("[archived]") + chars("result one")
    calls = [call(0, 0.0, 1000, head, r0, P=500, cached=0, out=20),
             call(1, 2.0, 1000, m1, r1, P=600, cached=512, out=30),                  # extension: U = 88
             call(2, 4.0, 1000, m2, [bash("cat x", 2)], P=650, cached=0, out=10)]   # summary-like edit
    recs = hcen.session_records("x", "m", "b", "s", calls, events=[{"kind": "snip_compact", "t": 3.9}])
    assert [r["chain"] for r in recs] == ["lead"] * 3
    assert recs[0]["edit"] == 0 and recs[0]["new"] == 500 and not recs[0]["edited"]
    assert not recs[1]["edited"] and recs[1]["new"] == new1 and recs[1]["edit"] == 88 - new1
    assert recs[2]["edited"] and recs[2]["kinds"] == ["snip_compact"]
    assert recs[2]["moved"] == moved2 and recs[2]["new"] == new2 and recs[2]["edit"] == 650 - new2
    assert recs[2]["Pprev"] == 600 and recs[1]["tool_uses"] == [["bash", "pytest -q"]]


def test_chains_split_subagent_instances_and_compactions():
    a = {"purpose": "one_shot", "request": {"messages": [msg("user", "job A")]}}
    a2 = {"purpose": "one_shot", "request": {"messages": [msg("user", "job A"), msg("assistant", "x"),
                                                          msg("user", "y")]}}
    b = {"purpose": "one_shot", "request": {"messages": [msg("user", "job B")]}}
    calls = [{"purpose": "lead"}, a, a2, b, {"purpose": "lead"}, a, {"purpose": "compaction_summary"},
             {"purpose": "memory_extract"}]
    assert hcen.assign_chains(calls) == ["lead", "os1", "os1", "os2", "lead", "os3", "cs1", "memory_extract"]


def test_fit_rho_recovers_tokens_per_char_and_falls_back():
    pts = [(c, 1500 + 0.25 * c) for c in range(1000, 30000, 4000)]
    rho, fitted = hcen.fit_rho(pts)
    assert fitted and rho == pytest.approx(0.25)
    assert hcen.fit_rho(pts[:3]) == (hcen.RHO_FALLBACK, False)


# -- E0a: pricing and census ----------------------------------------------------------------------------------

def rec(purpose="lead", P=10000, U=4000, out=100, edit=3000.0, edited=True, session="s1", **kw):
    eff, urg = hcen.hc.call_type(purpose)
    r = {"src": "x", "model": "m", "bench": "b", "session": session, "purpose": purpose, "eff": eff, "urg": urg,
         "served": True, "P": P, "U": U, "cached": P - U, "out": out, "edit": edit, "edited": edited,
         "new": U - edit, "Pprev": None}
    r.update(kw)
    return r


def test_pricer_splits_prefill_into_new_and_edit():
    price = hcen.N1Pricer(MODELS, TPOT)
    pre, edit_ms, dec = price(rec())
    assert pre == pytest.approx(40 + 0.1 * 4000 + 0.002 * 4000 * 10)
    assert edit_ms == pytest.approx((0.1 + 0.002 * 10) * 3000)
    tpot = 25 + 5 * (10050 - 1000) / 29000
    assert dec == pytest.approx(100 * tpot)


def test_census_categories_and_d1_shares():
    price = hcen.N1Pricer(MODELS, TPOT)
    rs = [rec(), rec(edited=False, edit=100.0), rec(purpose="compaction_summary", edit=0.0, edited=False),
          rec(purpose="memory_extract", edit=0.0, edited=False), {**rec(), "served": False}]
    agg = hcen.census(rs, price)[("x", "m", "b")]
    cat = agg["cat"]
    p0, e0, d0 = price(rs[0])
    p1, e1, d1 = price(rs[1])
    p2, _e2, d2 = price(rs[2])
    p3, _e3, d3 = price(rs[3])
    assert cat["prefill_edit_struct"] == pytest.approx(e0)
    assert cat["prefill_edit_other"] == pytest.approx(e1)
    assert cat["prefill_new"] == pytest.approx(p0 - e0 + p1 - e1)
    assert cat["decode"] == pytest.approx(d0 + d1)
    assert cat["compaction"] == pytest.approx(p2 + d2) and cat["side"] == pytest.approx(p3 + d3)
    s = hcen.shares(cat)
    cp = p0 + d0 + p1 + d1 + p2 + d2
    assert s["edit_cp"] == pytest.approx((e0 + e1 + p2 + d2) / cp)
    assert s["edit_gpu"] == pytest.approx((e0 + e1 + p2 + d2) / (cp + p3 + d3))
    assert s["edit_struct_cp"] == pytest.approx((e0 + p2 + d2) / cp)
    assert agg["unserved"] == 1 and agg["calls"]["lead"] == 2 and agg["types"]["M/C"] == 1


def test_n1_cache_removes_evictions_only_on_extensions():
    ext = rec(P=5000, U=4800, edit=4500.0, edited=False, Pprev=4600, cached=208, new=300.0)   # granularity:
    snip = rec(P=5000, U=4800, edit=4500.0, edited=True, Pprev=4600, cached=192, new=300.0)   # gcd = 16
    out = hcen.n1_cache([ext, snip])
    assert out[0]["U"] == 5000 - 4592 and out[0]["edit"] == pytest.approx(5000 - 4592 - 300)
    assert out[0]["evicted"] == 4800 - 408
    assert out[1] is snip


# -- E0c: windows and refresh fit -----------------------------------------------------------------------------

@pytest.mark.parametrize("cmd,cls", [("cd /testbed && python -m pytest tests -q", "bash_test"),
                                     ("go test ./...", "bash_test"), ("cd /app; make test", "bash_test"),
                                     ("python tests/runtests.py admin_docs", "bash_test"),
                                     ("cd /testbed && grep -rn foo .", "search"), ("git grep foo", "search"),
                                     ("find . -name '*.py'", "search"), ("ls -la", "bash_other"),
                                     ("cat a | grep b", "bash_other")])
def test_bash_classes(cmd, cls):
    assert hsl.use_class("bash", cmd) == cls


def test_window_class_priority():
    assert hsl.window_class([["read_file", None], ["bash", "pytest"]]) == "bash_test"
    assert hsl.window_class([["glob", None], ["edit_file", None]]) == "search"
    assert hsl.window_class([["task", None], ["bash", "ls"]]) == "task"
    assert hsl.window_class([["todo_write", None]]) == "other" and hsl.window_class([]) == "none"


def wrec(idx, t, ms, purpose="lead", edited=False, edit=0.0, P=1000, new=100.0, uses=()):
    return {"src": "x", "model": "m", "bench": "b", "session": "s", "idx": idx, "call": idx, "purpose": purpose,
            "served": True, "t": t, "ms": ms, "edited": edited, "edit": edit, "P": P, "new": new,
            "tool_uses": [list(u) for u in uses], "U": 500, "out": 10}


def test_windows_subtract_subagent_calls_and_price_refresh():
    rs = [wrec(0, 0.0, 1000, uses=[("task", None)]),
          wrec(1, 1.5, 2000, purpose="one_shot"),              # inside the window [1.0, 10.0)
          wrec(2, 10.0, 1000, edited=True, edit=2000.0, P=8000, uses=[("bash", "pytest")]),
          wrec(3, 12.0, 1000, P=8100, new=50.0)]
    w = hsl.windows(rs, hcen.N1Pricer(MODELS, TPOT))
    assert [x["call"] for x in w] == [2, 3]
    assert w[0]["gap_s"] == pytest.approx(9.0) and w[0]["idle_s"] == pytest.approx(7.0) and w[0]["cls"] == "task"
    assert w[1]["idle_s"] == pytest.approx(1.0) and w[1]["cls"] == "bash_test"
    assert w[0]["ref_edit_ms"] == pytest.approx(40 + 0.1 * 2000 + 0.002 * 2000 * 8, abs=0.1)
    assert w[1]["ref_edit_ms"] == 0.0
    assert w[1]["ref_all_ms"] == pytest.approx(40 + 0.1 * 8050 + 0.002 * 8050 * 8.1, abs=0.1)


def test_fit_counts_whole_and_partial_windows():
    rows = [{"idle_s": 1.0, "r": 500.0}, {"idle_s": 0.2, "r": 1000.0}, {"idle_s": 5.0, "r": 0.0}]
    f = hsl.fit(rows, "r")
    assert f["n"] == 2 and f["calls_fit"] == 0.5
    assert f["work_fit"] == pytest.approx(500 / 1500)
    assert f["work_partial"] == pytest.approx((500 + 200) / 1500)
