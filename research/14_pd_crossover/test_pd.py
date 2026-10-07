"""Tests of research/14_pd_crossover (PLAN.md §10). The slow end-to-end test runs the real harness against the fake
vLLM for 2- and 4-GPU layouts, live and replay (set PD_SLOW=1).

    pytest research/14_pd_crossover/test_pd.py
    PD_SLOW=1 pytest research/14_pd_crossover/test_pd.py -k pipeline
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import _paths  # noqa: E402,F401

import pd_analyze as pa  # noqa: E402
import pd_replay as pr  # noqa: E402
import pd_workloads as pw  # noqa: E402

REPO = HERE.parents[1]
PY = sys.executable


# -- workloads ----------------------------------------------------------------------------------------------------
def test_cells_match_plan():
    want = {"s1": 7, "l2a": 15, "l2b": 12, "x2": 19, "s3": 8, "l4a": 22, "l4b": 19, "r2a": 21, "r2b": 17}
    for cell, n in want.items():
        assert len(pw.CELLS[cell]["levels"]) == n, cell
        for name in pw.CELLS[cell]["levels"]:
            c = pw.CONDITIONS[name]
            assert pw.ARMS[c["pd"]["arm"]]["g"] == pw.CELLS[cell]["g"], name
            assert pw.cores_needed(c["pd"]["arm"]) <= 4 * pw.CELLS[cell]["g"], name
    assert sum(len(pw.CELLS[c]["levels"]) for c in want if c not in ("s1", "s3")) == 125
    # N-major order, arms rotated left by the N's position, then the duplicate
    assert pw.CELLS["l2a"]["levels"][:6] == ["pd_dp2_L_n01", "pd_tp2_L_n01", "pd_1p1d_L_n01",
                                             "pd_tp2_L_n02", "pd_1p1d_L_n02", "pd_dp2_L_n02"]
    assert pw.CELLS["l2a"]["levels"][-1] == "pd_1p1d_L_n08_dup"
    assert pw.CONDITIONS["pd_1p1d_L_n08_dup"]["pd"]["dup"]
    x2 = pw.CELLS["x2"]["levels"]
    assert sum(n.split("_")[2] == "H" for n in x2) == 9 and sum(n.split("_")[2] == "T" for n in x2) == 10
    assert pw.CONDITIONS["pd_dp2_T_n08"]["pd"]["think_scale"] == 1.0 and pw.CONDITIONS["pd_dp2_T_n08"]["pd"]["idle_s"] == 300
    assert pw.CONDITIONS["pd_dp2_H_n08"]["specs"] == list(pw.hw.HPOOL)
    assert pw.CONDITIONS["pd_dp2_L_n24"]["specs"] == [14 + 129, 526] and pw.CONDITIONS["pd_dp4_L_n24"]["specs"] == [143, 526]
    assert pw.CONDITIONS["pd_dp2_T_n24"]["specs"] == list(pw.hw.LPOOL)


def test_windows_and_replay_blocks():
    c = pw.CONDITIONS["pd_1p1d_L_n08"]["pd"]
    assert (c["window_s"], c["warmup_s"]) == (1200, 300)
    assert pw.CONDITIONS["pd_dp2_L_n01"]["pd"]["warmup_s"] == 60
    r = pw.CONDITIONS["pd_1p1d_L_n04_rep"]
    assert r["pd"]["window_s"] == 900 and r["replay"] == {"src": "l2_n04", "policy": "1p1d", "think_scale": 0.0,
                                                          "window_s": 240 + 900}


def test_server_args_per_role():
    dec = {"visibility": "A", "ucx_tls": None, "kv_lease_duration": None, "restart_each_level": False}
    insts = pw.instances("pd_2p1dt2_L_n08", dec)
    assert [i["role"] for i in insts] == ["P", "P", "D"]
    cfgs = [json.loads(i["args"][i["args"].index("--kv-transfer-config") + 1]) for i in insts]
    assert cfgs[0] == {"kv_connector": "NixlConnector", "kv_role": "kv_producer"}
    assert cfgs[2] == {"kv_connector": "NixlConnector", "kv_role": "kv_consumer", "kv_load_failure_policy": "recompute"}
    assert insts[2]["args"][insts[2]["args"].index("--tensor-parallel-size") + 1] == "2"
    assert insts[2]["visible"] == [2, 3, 0, 1] and insts[2]["cores"] == [8, 16]
    assert "pd_scheduler.PDScheduler" in insts[0]["args"]
    b = pw.instances("pd_2p1dt2_L_n08", dict(dec, visibility="B", kv_lease_duration=120, ucx_tls="cuda_ipc"))
    assert b[0]["visible"] == [0] and b[0]["env"]["UCX_TLS"] == "cuda_ipc"
    assert json.loads(b[0]["args"][b[0]["args"].index("--kv-transfer-config") + 1])["kv_connector_extra_config"] == \
        {"kv_lease_duration": 120}
    c = pw.instances("pd_dp2c_L_n08", dec)
    assert all("--kv-transfer-config" not in i["args"] and "2048" in i["args"] for i in c)


def test_conditions_load_through_11s_hook(tmp_path):
    out = tmp_path / "conditions.json"
    pw.export_conditions(out)
    code = ("import sys; sys.path.insert(0, %r); import _paths, mu_workloads as m; c = m.cond('pd_1p1d_L_n08'); "
            "print(c['n'], c['specs'], m.client_flags(c)[-3:])" % str(HERE.parent))
    r = subprocess.run([PY, "-c", code], env={**os.environ, "MU_EXTRA_CONDITIONS": str(out)}, capture_output=True,
                       text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.startswith("8 [35, 526]")                    # LIGHT offset of N = 8 (21 sessions)


# -- router -------------------------------------------------------------------------------------------------------
async def _serve(app, runners):
    from aiohttp import web
    r = web.AppRunner(app)
    await r.setup()
    site = web.TCPSite(r, "127.0.0.1", 0)
    await site.start()
    runners.append(r)
    return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


def _fake(name, seen, fail=False, no_params=False):
    from aiohttp import web

    async def call(req):
        body = await req.json()
        seen.setdefault(name, []).append((req.headers.get("X-Session-ID"), req.headers.get("X-Request-Id"),
                                          req.path, body))
        if fail:
            return web.json_response({"error": "boom"}, status=500)
        out = {"id": "x", "type": "message", "content": [{"type": "text", "text": name}],
               "usage": {"input_tokens": 1, "output_tokens": 1}}
        if (body.get("kv_transfer_params") or {}).get("do_remote_decode") and not no_params:
            out["kv_transfer_params"] = {"remote_engine_id": name, "remote_block_ids": [[1, 2, 3]]}
        return web.json_response(out)

    async def metrics(req):
        return web.Response(text="vllm:generation_tokens_total 7\nvllm:num_requests_running 1\n"
                                 "vllm:num_requests_waiting{x=\"y\"} 2\n")

    async def health(req):
        return web.Response(status=200)
    app = web.Application()
    app.router.add_post("/v1/messages", call)
    app.router.add_post("/v1/completions", call)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/health", health)
    return app


def test_router_dp_least_active_sticky_release():
    from aiohttp import ClientSession
    import pd_router

    async def run():
        seen, runners = {}, []
        b = [await _serve(_fake(k, seen), runners) for k in ("A", "B", "C")]
        r = await _serve(pd_router.make_app("dp", b, idle_s=60), runners)
        body = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}
        async with ClientSession() as c:
            for s in ["s1", "s2", "s3", "s4", "s1", "s2"]:
                async with c.post(r + "/v1/messages", json=body, headers={"X-Session-ID": s}) as x:
                    assert x.status == 200
            async with c.post(r + "/tm/hint", json={"s": "s2", "ev": "session_end"}) as x:
                assert x.status == 200
            async with c.post(r + "/v1/messages", json=body, headers={"X-Session-ID": "s5"}) as x:
                assert x.status == 200
            async with c.get(r + "/metrics") as x:
                text = await x.text()
            async with c.get(r + "/health") as x:
                assert x.status == 200
        who = {k: [s for s, *_ in v] for k, v in seen.items()}
        assert who["A"] == ["s1", "s4", "s1"] and who["B"] == ["s2", "s2", "s5"] and who["C"] == ["s3"]
        assert "vllm:generation_tokens_total 21.0" in text and "vllm:num_requests_waiting 6.0" in text
        for x in runners:
            await x.cleanup()
    asyncio.run(run())


def test_router_pd_two_by_two():
    from aiohttp import ClientSession
    import pd_router

    async def run():
        seen, runners = {}, []
        p = [await _serve(_fake(k, seen), runners) for k in ("P0", "P1")]
        d = [await _serve(_fake(k, seen), runners) for k in ("D0", "D1")]
        r = await _serve(pd_router.make_app("pd", prefill=p, decode=d, idle_s=60), runners)
        body = {"model": "m", "max_tokens": 7, "prompt": [1, 2, 3]}
        async with ClientSession() as c:
            for i, s in enumerate(["s1", "s2", "s1", "s3"]):
                async with c.post(r + "/v1/completions", json=body,
                                  headers={"X-Session-ID": s, "X-Request-Id": f"rid{i}"}) as x:
                    j = await x.json()
                    assert x.status == 200 and j["content"][0]["text"].startswith("D")
            async with c.get(r + "/router/stats") as x:
                st = await x.json()
        assert [s for s, *_ in seen["P0"]] == ["s1", "s1", "s3"] and [s for s, *_ in seen["P1"]] == ["s2"]
        assert [s for s, *_ in seen["D0"]] == ["s1", "s1", "s3"] and [s for s, *_ in seen["D1"]] == ["s2"]
        _, rid_p, path_p, pbody = seen["P0"][0]
        _, rid_d, path_d, dbody = seen["D0"][0]
        assert rid_p == rid_d == "rid0" and path_p == path_d == "/v1/completions"
        assert pbody["max_tokens"] == 1 and pbody["stream"] is False and pbody["kv_transfer_params"]["do_remote_decode"]
        assert dbody["max_tokens"] == 7 and dbody["kv_transfer_params"] == {"remote_engine_id": "P0",
                                                                           "remote_block_ids": [[1, 2, 3]]}
        assert st["pools"]["p"]["sessions"] == 3 and st["stats"]["p0"] == 3
        for x in runners:
            await x.cleanup()
    asyncio.run(run())


def test_router_pd_errors_and_no_params():
    from aiohttp import ClientSession
    import pd_router

    async def run():
        seen, runners = {}, []
        bad = await _serve(_fake("PB", seen, fail=True), runners)
        quiet = await _serve(_fake("PQ", seen, no_params=True), runners)
        d = await _serve(_fake("D", seen), runners)
        r1 = await _serve(pd_router.make_app("pd", prefill=[bad], decode=[d]), runners)
        r2 = await _serve(pd_router.make_app("pd", prefill=[quiet], decode=[d]), runners)
        body = {"model": "m", "max_tokens": 3, "messages": [{"role": "user", "content": "x"}]}
        async with ClientSession() as c:
            async with c.post(r1 + "/v1/messages", json=body, headers={"X-Session-ID": "a"}) as x:
                assert x.status == 500
            async with c.post(r2 + "/v1/messages", json=body, headers={"X-Session-ID": "b"}) as x:
                assert x.status == 200
            async with c.get(r2 + "/router/stats") as x:
                st = await x.json()
        assert "D" in seen and len(seen["D"]) == 1 and "kv_transfer_params" not in seen["D"][0][3]
        assert st["stats"]["p_no_params"] == 1
        for x in runners:
            await x.cleanup()
    asyncio.run(run())


# -- scheduler ----------------------------------------------------------------------------------------------------
def test_scheduler_class_imports():
    code = ("import sys; sys.path[:0] = [%r, %r, %r]; import pd_scheduler; "
            "from vllm.v1.core.sched.async_scheduler import AsyncScheduler; "
            "assert issubclass(pd_scheduler.PDScheduler, AsyncScheduler); "
            "from vllm.v1.request import RequestStatus; RequestStatus.WAITING_FOR_REMOTE_KVS; print('ok')"
            % (str(HERE), str(HERE.parent / "12_hw_utilization"), str(HERE.parent / "11_multiuser_kv")))
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]


# -- replay -------------------------------------------------------------------------------------------------------
def test_replay_plan_slots_and_gaps():
    pool = [{"label": f"S{i}", "t0": 100.0 * i, "t1": 100.0 * i + 60 + i, "spec": f"L{i}", "calls": [],
             "slot": 9, "kind": "x", "filler": False, "arrival": 0, "status": "ok"} for i in range(10)]
    plan = pr.make_plan(pool, 3)
    assert [r["label"] for r in plan if r["slot"] == 0] == ["S0", "S3", "S6", "S9"]
    assert [r["label"] for r in plan if r["slot"] == 2] == ["S2", "S5", "S8"]
    assert plan == pr.make_plan(pool, 3)                        # deterministic: every arm replays the same plan
    s0 = [r for r in plan if r["slot"] == 0]
    for a, b in zip(s0, s0[1:]):
        assert 5 <= b["t0"] - a["t1"] <= 30
        assert abs((a["t1"] - a["t0"]) - (pool[int(a["label"][1:])]["t1"] - pool[int(a["label"][1:])]["t0"])) < 1e-6
    starts = sorted(r["t0"] for r in plan if r["label"] in ("S0", "S1", "S2"))
    assert starts[1] - starts[0] >= 60 - 5 and starts[2] <= 2 * 60 + 5   # i * min(300, 60N) / N + U(0, 5)
    assert all(r["kind"] == "counted" and not r["filler"] for r in plan)


# -- analysis -----------------------------------------------------------------------------------------------------
def test_crossing_recovers_known_turning_point():
    ns = [2, 4, 6, 8, 10, 12]
    a = [-0.1, 0.1, 0.1, -0.1, -0.2, -0.3]                     # piecewise linear: P/D wins between N = 3 and 7
    out = pa.crossings(ns, a, [0.02] * len(ns))
    kinds = [(x["type"], round(x["n_star"], 2)) for x in out]
    assert kinds[0] == ("low", 3.0)
    hi = [x for x in out if x["type"] == "high"][0]
    assert abs(hi["n_star"] - 7) < 1e-9 and hi["ci"][0] <= 7 <= hi["ci"][1] and hi["resolved"]
    noisy = pa.crossings(ns, a, [0.5] * len(ns))
    assert not [x for x in noisy if x["type"] == "high"][0]["resolved"]


def test_comparisons_envelope_and_sigma():
    rows = []
    for n, (dp, tp, pd) in {2: (100, 120, 90), 4: (200, 230, 240), 8: (300, 380, 320), 12: (330, 450, 300)}.items():
        for arm, v in (("dp2", dp), ("tp2", tp), ("1p1d", pd)):
            rows.append({"cell": "l2a", "arm": arm, "n": n, "wl": "L", "kind": "live", "dup": False, "g": 2,
                         "lead_h": v, "call_p50": 1.0 / v, "call_p95": None, "tpot_p99": None, "ttft_p95": None})
    rows.append(dict(rows[0], dup=True, lead_h=105))
    comps = pa.comparisons(rows)
    by = {(c["base"], c["metric"]): c for c in comps}
    vs_dp = by[("dp2", "lead_h")]
    assert vs_dp["ns"] == [2, 4, 8, 12] and vs_dp["a"][0] < 0 < vs_dp["a"][1]
    assert [x["type"] for x in vs_dp["cross"]] == ["low", "high"]
    env = by[("best colocated", "lead_h")]
    assert env["who"] == ["tp2", "tp2", "tp2", "tp2"]
    assert [x["type"] for x in env["cross"]] == ["low", "high"] and env["cross"][0]["n_lo"] == 2
    sig = pa.sigma_pairs(rows, "live")
    assert sig["lead_h"][1] == 1 and sig["lead_h"][0] >= 0.07         # < 4 pairs: floor at 12's 0.07


# -- end to end ---------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("PD_SLOW") != "1", reason="slow: set PD_SLOW=1")
def test_pipeline_fake_server_live_replay_analysis():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        env = {**os.environ, "PD_TEST_CELLS": "1", "PD_DATA": str(d / "data"), "PD_WINDOW_SCALE": "0.1",
               "VLLM_BIN": f"{PY} {HERE / 'pd_fake_vllm.py'}", "JOB_SECONDS": "6000", "LOCAL_ROOT": str(d / "local"),
               "PD_FAKE_LOG": str(d / "fake.jsonl"), "CUDA_VISIBLE_DEVICES": "0,1,2,3",
               "HF_HOME": "/projects/co/jlin4/agenticllm/hf_cache", "HF_HUB_OFFLINE": "1"}
        env.pop("SLURM_JOB_ID", None)
        r = subprocess.run(["bash", str(HERE / "pd_pipeline.sh"), "tt2,tt4"], env=env, capture_output=True, text=True,
                           timeout=3600)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]

        def names(cell):                                     # the tt* cells exist only with PD_TEST_CELLS set
            out = subprocess.run([PY, str(HERE / "pd_workloads.py"), "cell", cell, "--field", "levels"], env=env,
                                 capture_output=True, text=True, cwd=REPO)
            return out.stdout.split()
        for cell in ("tt2", "tt4"):
            assert names(cell)
            for name in names(cell):
                assert (d / "data" / "cells" / cell / "levels" / name / "WINDOW_DONE").exists(), name
        levels = [str(p) for p in (d / "data" / "cells" / "tt2" / "levels").glob("pd_*")]
        r = subprocess.run([PY, str(HERE / "pd_replay.py"), "prepare", "--levels", *levels, "--pool", "tt", "--min", "2",
                            "--workers", "2"], env=env, capture_output=True, text=True, timeout=1200, cwd=REPO)
        assert r.returncode == 0, r.stderr[-2000:]
        r = subprocess.run([PY, str(HERE / "pd_replay.py"), "mkplan", "--pool", "tt", "--n", "2", "--name", "tt_n02"],
                           env=env, capture_output=True, text=True, cwd=REPO)
        assert r.returncode == 0, r.stderr[-2000:]
        r = subprocess.run(["bash", str(HERE / "pd_pipeline.sh"), "ttr"], env={**env, "PD_WINDOW_SCALE": "0.2"},
                           capture_output=True, text=True, timeout=1800)
        assert r.returncode == 0
        for name in names("ttr"):
            assert (d / "data" / "cells" / "ttr" / "levels" / name / "WINDOW_DONE").exists(), name
        r = subprocess.run([PY, str(HERE / "pd_analyze.py"), "levels", "--cells", "tt2,tt4,ttr"], env=env,
                           capture_output=True, text=True, timeout=1200, cwd=REPO)
        assert r.returncode == 0, r.stderr[-3000:]
        rows = json.loads((d / "data" / "levels.json").read_text())["rows"]
        assert len(rows) == 9
        for row in rows:
            assert row["joined"] > 0, row["level"]
            assert abs(row["decomp"]["resid"]) < 1e-3, row["level"]
            if row["arm"] not in pw.COLOCATED:
                assert row["xfer_tok"] and row["decomp"]["kv_wait"] > 0
        fake = [json.loads(x) for x in open(d / "fake.jsonl")]
        assert {("P", "producer"), ("D", "consumer"), ("C", None)} <= {(x["role"], x["kv"]) for x in fake}
        assert not math.isnan(rows[0]["lead_h"])
