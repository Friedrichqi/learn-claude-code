#!/usr/bin/env python3
"""CPU tests of wt_scheduler.WTScheduler with vLLM 0.31's real scheduler in the loop (no GPU, no model).

Reuses topic 11's harness (research/11_multiuser_kv/test_mu_scheduler.py: a 48-block pool, five users
whose prompts grow append-only, the engine's async step order, fake outputs). Checks:

  * the status export changes no scheduling decision (identical to the stock AsyncScheduler);
  * per-session residency equals a brute-force count of the hashed blocks each session's chains own;
  * the cached prefix equals a brute-force walk of the session's hashes and never exceeds its tokens;
  * the snapshot file is written, parses, and lists every session; turns start after a reply without a
    tool call and not after one with a tool call; mid-turn expires;
  * topic 11's session gate still bounds the active sessions under the subclass.

    CUDA_VISIBLE_DEVICES= /data/fritz/venv/bin/python research/18_harness_wait/test_wt_scheduler.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import test_mu_scheduler as tmu  # noqa: E402  research/11_multiuser_kv (builds vLLM configs on CPU)
from vllm.v1.core.sched.async_scheduler import AsyncScheduler  # noqa: E402

import wt_scheduler  # noqa: E402


def run_wt(env_extra: dict, plan=None, tick_s: float = 0.0):
    log = tempfile.mktemp(suffix=".jsonl", prefix="wt_sched_test_")
    snap = tempfile.mktemp(suffix=".json", prefix="wt_status_test_")
    s = tmu.build(wt_scheduler.WTScheduler, {"MU_SCHED_LOG": log, "MU_POOL_WALK_S": "0", "WT_STATUS_PATH": snap,
                                             "WT_SNAP_S": "0", **env_extra})
    schedules, _ = tmu.drive(s, plan or tmu.workload(), tick_s=tick_s)
    final = s.wt_snapshot(time.monotonic())
    s.shutdown()
    return s, schedules, tmu.read_log(log), final, snap


def owned_tokens_by_session(s) -> dict:
    out = {}
    for b in s.bp.blocks:
        if b._block_hash is None:
            continue
        ci = s.owner[b.block_id]
        if ci < 0:
            continue
        sid = s.chain_list[ci].key[0]
        out[sid] = out.get(sid, 0) + s.bs
    return out


def test_status_export_changes_no_decision():
    plan = tmu.workload()
    ref, _ = tmu.drive(tmu.build(AsyncScheduler, {}), plan)
    _, got, log, _, _ = run_wt({}, plan)
    assert got == ref, "WTScheduler scheduled differently from AsyncScheduler"
    assert not [r for r in log if r["k"] == "err"], [r for r in log if r["k"] == "err"][:1]
    assert any(r["k"] == "wthdr" for r in log)


def test_residency_and_prefix_match_the_pool():
    s, _, log, final, _ = run_wt({})
    assert not [r for r in log if r["k"] == "err"], [r for r in log if r["k"] == "err"][:1]
    owned = owned_tokens_by_session(s)
    sessions = final["sessions"]
    assert set(sessions) == {f"MU-t-S{u:03d}~a1" for u in range(5)}, sorted(sessions)
    for sid, info in sessions.items():
        assert info["res"] == owned.get(sid, 0), (sid, info["res"], owned.get(sid, 0))
        sess = s.wt_sess[sid]
        brute = 0
        for h in sess.hashes:
            if s.bp.get_cached_block(h, [0]) is None:
                break
            brute += 1
        assert info["prefix"] == min(brute * s.bs, info["np"] + info["out"]), (sid, info["prefix"], brute)
        assert 0 <= info["prefix"] <= info["np"] + info["out"] <= info["ws"]
        assert info["inflight"] == 0 and not info["queued"]
    evictions = sum(g[1] for r in log if r["k"] == "ev" for g in r["g"])
    assert evictions > 0 and final["cum"]["evict"] == evictions, (final["cum"], evictions)
    assert final["cum"]["preempt"] == sum(1 for r in log if r["k"] == "pre") > 0
    first_adm = [r for r in log if r["k"] == "adm" and not r["res"]]
    assert final["cum"]["adm"] == len(first_adm) == 20
    assert final["cum"]["adm_hit"] == sum(r["hit"] for r in first_adm)
    pf = [0, 0, 0]
    for r in log:
        if r["k"] == "step":
            pf = [a + b for a, b in zip(pf, r["pf"])]
    assert [final["cum"]["pf_fresh"], final["cum"]["pf_evict"], final["cum"]["pf_pre"]] == pf
    wt = [r for r in log if r["k"] == "wt"]
    assert wt and all({"pool", "running", "waiting", "cum"} <= set(r) for r in wt)


def test_snapshot_file_turns_and_mid():
    # one user, four calls: tool, no tool (ends a turn), tool, tool; pauses between them
    base = [7000 + (i % 89) for i in range(100)]
    plan = [[]]
    prompt = list(base)
    for k, tool in enumerate([True, False, True, True]):
        prompt = prompt + [8000 + k * 7 + (i % 13) for i in range(24)]
        plan[0].append((list(prompt), 8, 2, tool))
    s, _, log, final, path = run_wt({"WT_MID_EXPIRE_S": "0.2"}, plan, tick_s=0.002)
    snap = json.loads(Path(path).read_text())
    assert snap["sessions"] and snap["pool"]["blocks"] == tmu.BLOCKS - 1 and snap["seq"] >= 1
    turn_starts = [r["wall"] for r in log if r["k"] == "arr"]
    sess = s.wt_sess["MU-t-S000~a1"]
    assert sess.turn_t0 == turn_starts[2] or abs(sess.turn_t0 - turn_starts[2]) < 0.05, (sess.turn_t0, turn_starts)
    assert sess.turn_t0 > turn_starts[1], "the call after a reply with no tool call starts a turn"
    assert sess.last_tool is True
    assert final["sessions"]["MU-t-S000~a1"]["mid"] is True        # just replied with a tool call
    time.sleep(0.25)
    later = s.wt_snapshot(time.monotonic())
    assert later["sessions"]["MU-t-S000~a1"]["mid"] is False      # expired


def test_gate_still_bounds_active_sessions():
    s, _, log, _, _ = run_wt({"MU_GATE": "2", "MU_GATE_IDLE_S": "0.01"}, tick_s=0.001)
    assert not [r for r in log if r["k"] == "err"], [r for r in log if r["k"] == "err"][:1]
    arr = {r["r"]: r for r in log if r["k"] == "arr"}
    live, peak = set(), 0
    for r in log:
        if r["k"] == "adm" and not r["res"]:
            live.add(arr[r["r"]]["c"])
            peak = max(peak, len(live))
        elif r["k"] == "fin":
            live.discard(arr[r["r"]]["c"])
    assert peak <= 2 and len([r for r in log if r["k"] == "fin"]) == 20


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
