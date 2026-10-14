"""CPU tests of hs_drift (E0e) on small synthetic fixtures (no topic-16 data needed).

    <python with pytest> -m pytest research/17_harness_aware_serving/test_hs_drift.py -q
"""
from __future__ import annotations

import json
import math
import random
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_drift as hd  # noqa: E402


# -- fixtures --------------------------------------------------------------------------------------------------------

def l1_of(bins: dict) -> dict:
    """{(cls, age bin, off bin): (rows, per-row dK)} -> the replay's sparse l1 (16 layer groups, n summed over 4)."""
    out = {}
    for (c, a, o), (n, d) in bins.items():
        b = (c * hd.N_AGE + a) * hd.N_OFF + o
        out[str(b)] = [[d * n, float(n), d * n, float(n), 4.0 * n] for _ in range(hd.N_LG)]
    return out


def arm(P, E, S, H, C, ped, age_mean, age_max, kl, l1=None):
    return {"stats": {"P": P, "E": E, "S": S, "H": H, "C": C, "F": C - H, "reuse_avail": E + S, "exact": E,
                      "runs": 1 if ped else 0, "reply_reused": 0, "edited": ped, "max_delta": 50 if ped else 0,
                      "age_mean_over": age_mean, "age_max_over": age_max},
            "kl": kl, "first_kl": kl / 2, "top1": 1.0, "dnll": 0.0, "l1": l1}


def synth_records(n_runs: int = 9, n_calls: int = 12, seed: int = 0) -> list[dict]:
    """Runs whose odd calls are snips (edit: E = head, S reused rows aging by one per edit) and even calls appends."""
    rng = random.Random(seed)
    recs = []
    for r in range(n_runs):
        run = f"EHM-R1-pkg{r}-{'M2' if r % 2 else 'E2'}-x-r0-prefix"
        for k in range(n_calls):
            P = 1000 + 10 * k
            if k == 0:
                a0 = arm(P, 0, 0, 0, P, False, 0.0, 0, 0.0)
                recs.append({"run": run, "call": 0, "P": P, "reply": 30, "edited": False,
                             "arms": {x: a0 for x in hd.ARMS}})
                continue
            ped = k % 2 == 1
            edits = (k + 1) // 2
            if ped:
                E, S, H = 100, 700, 32
                C = P - E - S
                age_max, age_mean = edits, (edits + 1) / 2
                kl_c = 0.002 * (1 + k % 3) * (1 + r % 2) * (1 + min(edits, 4) / 2) + rng.random() * 1e-4
                kl_1 = 0.002 * (1 + k % 3) * (1 + r % 2)
                age_bin = min(5, max(1, int(math.log2(edits)) + 1))
                l1c = l1_of({(0, 0, 4): (E, 0.001), (1, age_bin, 4): (S - 100, 0.01 * (1 + edits / 4)),
                             (1, age_bin, 3): (100, 0.03), (2, 0, 0): (H, 0.005), (3, 0, 4): (C, 0.005)})
                l1o = l1_of({(0, 0, 4): (E, 0.0), (1, 1, 4): (S - 100, 0.01), (1, 1, 3): (100, 0.02),
                             (2, 0, 0): (H, 0.003), (3, 0, 4): (C, 0.004)})
            else:
                E, S, H = P - 20, 5, 0
                C = P - E - S
                age_max, age_mean = 0, 0.0
                kl_c, kl_1 = 0.001 * (1 + r % 3), 0.0
                l1c = l1_of({(0, 0, 4): (E - 300, 0.001), (0, 2, 4): (300, 0.02), (1, 0, 4): (S, 0.001),
                             (3, 0, 4): (C, 0.004)})
                l1o = l1_of({(0, 0, 4): (E, 0.0), (1, 0, 4): (S, 0.0), (3, 0, 4): (C, 0.001)})
            comp = arm(P, E, S, H, C, ped, age_mean, age_max, kl_c, l1c)
            one = arm(P, E, S, H, C, ped, 1.0 if ped else 0.0, 1 if ped else 0, kl_1, l1o)
            recs.append({"run": run, "call": k, "P": P, "reply": 20 + 5 * (k % 4), "edited": True,
                         "arms": {"shift": comp, "epic32": comp, "epic64": comp, "epic128": comp,
                                  "shift1": one, "epic128_1": one}})
    return recs


def call(ped, P, E, C, S, kl_c, kl_1, a, call_i=0, i=0):
    return {"i": i, "call": call_i, "run": "r", "ped": ped, "P": P, "E": E, "C": C, "S": S, "kl_c": kl_c, "kl_1": kl_1,
            "v": kl_c - kl_1, "a": a, "anchor": 1 if ped else 0, "n0": [0.0] * 5, "n1": [0.0] * 5}


# -- statistics --------------------------------------------------------------------------------------------------------

def test_rankdata_averages_ties():
    assert hd.rankdata([3, 1, 2, 2]).tolist() == [3.0, 0.0, 1.5, 1.5]


def test_spearman_known_values():
    assert hd.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == pytest.approx(1.0)
    assert hd.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    assert hd.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)     # 1 - 6 * 4 / (5 * 24)
    assert math.isnan(hd.spearman([1, 2, 3], [7, 7, 7]))


def test_top_share_and_captured():
    v = [5, 3, 1, 1, 0, 0, 0, 0, 0, 0]
    assert hd.top_share(v, 0.1) == pytest.approx(0.5)
    assert hd.top_share(v, 0.2) == pytest.approx(0.8)
    assert hd.captured(v, v, 0.2) == pytest.approx(0.8)
    assert hd.captured([-x for x in v], v, 0.2) == pytest.approx(0.0)


def test_ridge_recovers_a_linear_function():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(300, 3))
    y = 1.0 + 2.0 * X[:, 0] - X[:, 1] + 1e-4 * rng.normal(size=300)
    m = hd.ridge_fit(X, y, 1e-6)
    Xn = rng.normal(size=(20, 3))
    assert np.allclose(hd.ridge_predict(m, Xn), 1.0 + 2.0 * Xn[:, 0] - Xn[:, 1], atol=1e-2)


def test_run_folds_partition_runs():
    runs = [f"run{i}" for i in range(30)] * 3
    folds = hd.run_folds(runs, 3, "s")
    assert [len(f) for f in folds] == [10, 10, 10]
    assert set().union(*map(set, folds)) == set(runs)
    assert not set(folds[0]) & set(folds[1])
    assert folds == hd.run_folds(runs, 3, "s")


def test_pava_pools_violators():
    assert hd.pava([1, 3, 2, 4], [1, 1, 1, 1]) == pytest.approx([1, 2.5, 2.5, 4])
    assert hd.pava([2, 1], [3, 1]) == pytest.approx([1.75, 1.75])


# -- inputs ------------------------------------------------------------------------------------------------------------

def test_l1_hist_decodes_bins():
    rec = {"l1": l1_of({(1, 3, 4): (10, 0.5), (0, 0, 2): (7, 0.0)})}
    rows, dk, kk = hd.l1_hist(rec)
    assert rows[1, 3] == pytest.approx(10) and rows[0, 0] == pytest.approx(7)
    assert dk[1, 3] == pytest.approx(16 * 5.0) and kk[1, 3] == pytest.approx(16 * 10.0)
    assert set(hd.l1_bins(rec)) == {(1, 3, 4), (0, 0, 2)}


def test_reproduce_checks_counts_and_means():
    recs = synth_records(3, 4)
    means = {a: float(np.mean([r["arms"][a]["kl"] for r in recs if r["edited"]])) for a in ("epic128", "epic128_1")}
    head = {a: round(m, 4) for a, m in means.items()}
    ok = hd.reproduce(recs, {"runs": 3, "calls": 12, "edited": 9}, head)
    assert ok["ok"] and ok["counts"] == {"runs": 3, "calls": 12, "edited": 9, "missing_arm_kl": 0}
    assert not hd.reproduce(recs, {"runs": 3, "calls": 12, "edited": 9}, {"epic128": head["epic128"] + 0.001})["ok"]
    assert not hd.reproduce(recs, {"runs": 4, "calls": 12, "edited": 9}, head)["ok"]
    recs[1] = {**recs[1], "arms": {k: x for k, x in recs[1]["arms"].items() if k != "shift1"}}
    assert not hd.reproduce(recs, {"runs": 3, "calls": 12, "edited": 9}, head)["ok"]     # an arm missing


def test_call_table_history_and_value():
    recs = synth_records(1, 6)
    rows = hd.call_table(recs, "epic128")
    assert [r["call"] for r in rows] == [1, 2, 3, 4, 5]
    assert [r["ped"] for r in rows] == [True, False, True, False, True]
    assert [r["n_edit"] for r in rows] == [1, 1, 2, 2, 3]
    assert [r["since"] for r in rows] == [0, 1, 0, 1, 0]
    by = {r["call"]: r for r in rows}
    assert by[2]["a"] == by[1]["age_max"] and by[2]["anchor"] == 0 and by[3]["anchor"] == 1
    assert by[1]["prev_reply"] == 30                       # call 0's reply
    src = next(r for r in recs if r["call"] == 3)
    assert by[3]["v"] == pytest.approx(src["arms"]["epic128"]["kl"] - src["arms"]["epic128_1"]["kl"])
    assert by[2]["n0"][1] == pytest.approx(300)            # inherited stale exact-prefix rows (age bin 2-3)


# -- age models ------------------------------------------------------------------------------------------------------

def test_fit_age_model_recovers_the_curve():
    a = np.repeat(np.arange(1, 25), 30)
    kl_1 = np.full(len(a), 0.01)
    kl_c = kl_1 * hd.s_curve(a, 2.0, 3.0)
    m = hd.fit_age_model(a, kl_c, kl_1)
    assert m["A"] == pytest.approx(2.0, abs=0.1) and m["tau"] == pytest.approx(3.0, rel=0.15)
    assert m["bins"][0]["ratio"] == pytest.approx(1.0)
    assert all(b["iso"] >= 1.0 for b in m["bins"])


def test_curve_tables_and_phi_endpoints():
    model = {"A": 2.0, "tau": 3.0, "plateau": 2.5,
             "bins": [{"lo": 1, "hi": 1, "iso": 1.0}, {"lo": 2, "hi": 3, "iso": 1.5}, {"lo": 6, "hi": 999, "iso": 3.0}]}
    iso = hd.curve_table("iso", model)
    assert iso[:4] == [0.0, 1.0, 1.5, 1.5] and iso[4] == 1.5 and iso[6] == 3.0     # age 4-5: carried forward
    assert hd.curve_table("step", model)[:3] == [0.0, 1.0, 2.5]
    t = hd.curve_table("M1", model)
    edit = call(True, 1000, 100, 200, 700, 0.03, 0.01, a=10)
    app = call(False, 1000, 980, 15, 5, 0.02, 0.0, a=10)
    assert hd.phi_curve(edit, None, t) == 1.0                 # no refresh yet
    assert hd.phi_curve(edit, 10, t) == 1.0                   # cap above the call's age
    assert hd.phi_curve(edit, 1, t) == 0.0                    # first edit after a refresh: the one-step arm
    assert hd.phi_curve(app, 0, t) == 0.0                     # appends right after a refresh: fresh
    vals = [hd.phi_curve(edit, m, t) for m in range(1, 11)]
    assert vals == sorted(vals) and 0 < vals[2] < 1
    assert hd.phi_measured(edit, 1) == 0.0 and hd.phi_measured(edit, 3) == 1.0 and hd.phi_measured(app, 0) == 0.0


def test_phi_m2_uses_the_age_histogram():
    d = hd.d_curve([0.001, 0.02, 0.03, 0.03, 0.05, 0.09])
    c = call(True, 1000, 100, 200, 700, 0.03, 0.01, a=12)
    c["n1"] = [0.0, 0.0, 0.0, 10.0, 0.0]                     # ten reused rows of age 8-15
    assert hd.phi_m2(c, None, d) == 1.0
    assert hd.phi_m2(c, 1, d) == pytest.approx(0.0)
    assert hd.phi_m2(c, 20, d) == pytest.approx(1.0)
    assert 0.0 < hd.phi_m2(c, 4, d) < 1.0
    assert d(1.0) == pytest.approx(0.02) and d(40.0) == pytest.approx(0.09)


# -- policies ----------------------------------------------------------------------------------------------------------

def a_run():
    """edit, append, edit, edit, edit: E = head 100 at every edit."""
    return {"r": [call(True, 1000, 100, 200, 700, 0.03, 0.01, a=3, call_i=1, i=0),
                  call(False, 1100, 1080, 15, 5, 0.02, 0.0, a=3, call_i=2, i=1),
                  call(True, 1200, 100, 300, 800, 0.05, 0.02, a=4, call_i=3, i=2),
                  call(True, 1200, 100, 300, 800, 0.04, 0.01, a=5, call_i=4, i=3),
                  call(True, 1200, 100, 300, 800, 0.06, 0.01, a=6, call_i=5, i=4)]}


def test_simulate_none_and_every_call():
    runs = a_run()
    calls = runs["r"]
    prefix = sum(c["P"] - c["E"] for c in calls)
    none = hd.simulate(runs, lambda c, rt, k: False, hd.phi_measured)
    assert none["value"] == pytest.approx(0.0) and none["refreshed"] == 0
    assert none["cost"] == pytest.approx(sum(c["C"] for c in calls) / prefix)
    every = hd.simulate(runs, lambda c, rt, k: True, hd.phi_measured)
    assert every["value"] == pytest.approx(1.0) and every["value_lb"] == pytest.approx(1.0)
    # edit calls re-prefill P - E (= C + S); the append-only call after a refresh has nothing stale (rt = 0)
    assert every["cost"] == pytest.approx((sum(c["P"] - c["E"] for c in calls if c["ped"]) + calls[1]["C"]) / prefix)
    exact = hd.simulate(runs, lambda c, rt, k: True, hd.phi_measured, "exact")
    assert exact["value"] == pytest.approx(sum(c["kl_c"] for c in calls) / sum(c["v"] for c in calls))


def test_refresh_tokens_of_an_append_only_call():
    seen = {}

    def decide(c, rt, k):
        seen[c["call"]] = rt
        return False
    hd.simulate(a_run(), decide, hd.phi_measured)
    assert seen[1] == 700                                   # an edit call: its reused rows S
    assert seen[2] == 1100 - 100 - 15                       # append-only: everything since the last edit point


def test_every_n_edits_caps_and_measured_value():
    runs = a_run()
    calls = runs["r"]
    res = hd.simulate(runs, lambda c, rt, k: c["ped"] and k >= 2, hd.phi_measured)
    # refreshes at the 2nd and 4th edit (calls 3 and 5); call 4 is the first edit after a refresh (one-step, measured)
    assert res["refreshed"] == 2
    tot = sum(c["v"] for c in calls)
    assert res["value"] == pytest.approx((calls[2]["v"] + calls[3]["v"] + calls[4]["v"]) / tot)
    assert res["value_lb"] == pytest.approx((calls[2]["v"] + calls[4]["v"]) / tot)
    assert res["cost"] == pytest.approx((sum(c["C"] for c in calls) + 800 + 800) / sum(c["P"] - c["E"] for c in calls))


def test_threshold_rules_skip_calls_with_nothing_stale():
    pred = np.array([1.0, 1.0, 0.0, 1.0, 0.0])
    rule = hd.threshold_rule(pred, 0.5)
    assert rule({"i": 0}, 10, 1) and not rule({"i": 0}, 0, 1) and not rule({"i": 2}, 10, 1)
    per = hd.per_token_rule(pred, 0.01)
    assert per({"i": 0}, 50, 1) and not per({"i": 0}, 500, 1) and not per({"i": 0}, 0, 1)


def test_d2_verdict_and_budget():
    assert hd.d2_verdict(0.6, 0.30) == "pass"
    assert hd.d2_verdict(0.4, 0.30) == "fail (value)"
    assert hd.d2_verdict(0.6, 0.50) == "fail (cost)"
    assert hd.d2_verdict(0.4, 0.50) == "fail (value and cost)"
    pts = [{"value_M1": 0.9, "cost": 0.5}, {"value_M1": 0.4, "cost": 0.3}, {"value_M1": 0.3, "cost": 0.2}]
    assert hd.best_at_budget(pts)["value_M1"] == 0.4


# -- the read-aware proxy -------------------------------------------------------------------------------------------

def tool_use(name, **inp):
    return {"type": "tool_use", "id": f"t-{name}", "name": name, "input": inp}


def result(text):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": text}]}


SNIPPET = "def alpha_function_with_a_rather_long_name(arg_one, arg_two): return arg_one"


def proxy_msgs():
    prev = [{"role": "user", "content": "task"}, {"role": "user", "content": "[2 messages archived at t1]"},
            {"role": "assistant", "content": [tool_use("read_file", path="a.py")]}, result(SNIPPET),
            {"role": "assistant", "content": [tool_use("glob", pattern="*.py")]}]
    cur = [{"role": "user", "content": "task"}, {"role": "user", "content": "[3 messages archived at t2]"},
           *prev[2:], result("x.py y.py")]
    return prev, cur


def test_reply_proxy_flags_rereads_and_moved_copies():
    prev, cur = proxy_msgs()
    p = hd.reply_proxy(prev, cur, [tool_use("read_file", path="a.py")])
    assert p["tool"] == "read_file" and p["reread"]
    p = hd.reply_proxy(prev, cur, [tool_use("read_file", path="b.py")])
    assert not p["reread"]
    p = hd.reply_proxy(prev, cur, [tool_use("edit_file", path="a.py", old_text=SNIPPET, new_text="x")])
    assert p["copy_moved_only"] > 0.8 and not p["reread"]
    assert hd.reply_proxy(None, cur, [tool_use("read_file", path="a.py")])["reread"] is False


def test_read_proxy_aligns_calls(tmp_path):
    prev, cur = proxy_msgs()
    run = "EHM-R1-pkg-M2-x-r0-prefix"
    d = tmp_path / "live" / "main" / "runs" / run
    d.mkdir(parents=True)
    reqs = [{"call_index": 0, "purpose": "lead", "messages": prev[:4],
             "response": {"content": [tool_use("glob", pattern="*.py")], "usage": {"output_tokens": 11}}},
            {"call_index": 1, "purpose": "mx", "messages": [], "response": {"content": []}},
            {"call_index": 2, "purpose": "lead", "messages": cur,
             "response": {"content": [tool_use("read_file", path="a.py")], "usage": {"output_tokens": 21}}}]
    (d / f"{run}.requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reqs))
    recs = [{"run": run, "call": 0, "reply": 10, "src": "main:replay"}, {"run": run, "call": 1, "reply": 20,
                                                                        "src": "main:replay"}]
    out, align = hd.read_proxy(recs, tmp_path)
    assert align == {"aligned": 2}
    assert out[(run, 1)]["reread"] and out[(run, 1)]["prev_tool"] == "glob"
    assert out[(run, 0)]["tool"] == "glob" and not out[(run, 0)]["reread"]


# -- end to end -------------------------------------------------------------------------------------------------

def test_analyze_and_report_smoke(tmp_path):
    recs = synth_records()
    res = hd.analyze(recs, {}, reps=2, boot=3, seed="t")
    names = [r["policy"] for r in res["policies"]["fixed"]]
    assert "(a) refresh before every call" in names and "(b) every 8 edits" in names
    a = next(r for r in res["policies"]["fixed"] if r["policy"].startswith("(a)"))
    assert a["value_M1"] == pytest.approx(1.0) and a["cost"] == pytest.approx(1.0, abs=0.02)
    rep = {"counts": {"runs": 9, "calls": 108, "edited": 99}, "means": {k: 0.0 for k in hd.HEADLINE}, "ok": True}
    lines = hd.report_lines(res, rep, {}, types.SimpleNamespace(reps=2, boot=3))
    assert any(line.startswith("**D2 verdict:") for line in lines)
    hd.write_outputs(res, rep, {}, tmp_path)
    assert (tmp_path / "calls.jsonl").read_text().count("\n") == 99
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["d2"] in ("PASS", "FAIL")
