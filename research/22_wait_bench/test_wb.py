#!/usr/bin/env python3
"""CPU tests of topic 22's code: the full-N window and running-agent average, the percentiles, the trial and scrape
parsing, the paired bootstrap, and wb_child's permission hook. The whole pipeline (Harbor, wb_agent, wb_child, the
queue order, --n-concurrent-agents) is checked by the dry run against wb_fake.py (README). Runs as a script:

    /data/fritz/venv/bin/python research/22_wait_bench/test_wb.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
os.environ.setdefault("WB_ADAPTER_DIR", str(DIR.parents[0] / "16_epic_harness" / "harness_eval" / "adapter"))

import wb_analyze as wa  # noqa: E402


def test_window_and_running_agents():
    # N=2: agents [0,10], [1,4], [4,12], [11,20]; the queue empties at 11 (the last start); N first reached at 1
    trials = [{"a0": a, "a1": b} for a, b in ((0, 10), (1, 4), (4, 12), (11, 20))]
    w = wa.window(trials, 2)
    assert (w["w0"], w["w1"]) == (1, 11) and w["peak_agents"] == 2 and w["reached_n"], w
    # running agents over [1, 11]: [0,10] gives 9, [1,4] 3, [4,12] 7, [11,20] 0 -> 19 / 10
    assert abs(w["agents_in_window"] - 1.9) < 1e-9, w
    assert w["start"] == 0 and w["end"] == 20 and abs(w["agents_whole_run"] - (10 + 3 + 8 + 9) / 20) < 1e-9


def test_percentiles_match_numpy():
    import numpy as np
    xs = [3.0, 1.0, 4.0, 1.5, 9.0, 2.6, 5.0, 3.5, 8.9, 7.9, 0.2]
    for q in (0, 50, 95, 99, 100):
        assert abs(wa.pct(xs, q) - float(np.percentile(xs, q))) < 1e-9, q
    s = wa.summary(xs)
    assert s["n"] == len(xs) and abs(s["avg"] - sum(xs) / len(xs)) < 1e-12
    assert wa.pct([], 50) is None and wa.summary([])["avg"] is None


def _trial(job: Path, name: str, link: str, a0: str, a1: str, reward, calls: list[dict], waits_ms: list[float],
           t0: float, exc: str | None = None) -> None:
    d = job / name
    s15 = d / "agent" / "s15"
    (s15 / "traces").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"task": {"path": f"data/queue/main/{link}"}}))
    (d / "result.json").write_text(json.dumps({
        "started_at": a0, "finished_at": a1, "agent_execution": {"started_at": a0, "finished_at": a1},
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
        "exception_info": {"exception_type": exc} if exc else None}))
    (s15 / "wb_t0.json").write_text(json.dumps({"t0": t0}))
    (s15 / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    events = [{"event": "tool_end", "data": {"tool": "wait", "duration_ms": ms}} for ms in waits_ms]
    events.append({"event": "tool_end", "data": {"tool": "bash", "duration_ms": 1500.0}})
    (s15 / "traces" / "run_1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_load_trials_and_rates():
    root = Path(tempfile.mkdtemp(prefix="wb_test_"))
    job = root / "job"
    t0 = 1_800_000_000.0
    usage = {"input_tokens": 10, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 0, "output_tokens": 5}
    _trial(job, "q001__a__X", "q001__a", "2027-01-15T08:00:00Z", "2027-01-15T08:01:40Z", 1.0,
           [{"t": 1.0, "ms": 2000.0, "purpose": "lead", "usage": usage, "tools": ["bash", "wait"]},
            {"t": 5.0, "ms": 3000.0, "purpose": "lead", "usage": usage, "tools": ["wait"]},
            {"t": 9.0, "error": "APITimeoutError"}, {"t": 9.5, "refused": "lead call budget spent"}],
           [20000.0, 5000.0], t0)
    _trial(job, "q002__b__Y", "q002__b", "2027-01-15T08:00:30Z", "2027-01-15T08:03:50Z", 0.0,
           [{"t": 2.0, "ms": 1000.0, "purpose": "sub", "usage": usage, "tools": []}], [], t0 + 30,
           exc="AgentTimeoutError")
    rows = wa.load_trials(job, {"a": "swe", "b": "tbench"})
    assert [r["pos"] for r in rows] == [1, 2] and [r["bench"] for r in rows] == ["swe", "tbench"]
    a, b = rows
    assert a["e2e"] == 100.0 and b["e2e"] == 200.0 and b["limit"] and not a["limit"]
    assert a["errors"] == 1 and len(a["calls"]) == 2 and a["calls"][0]["t"] == t0 + 1.0
    assert a["calls"][0]["wait"] and not a["calls"][0]["wait_only"] and a["calls"][1]["wait_only"]
    assert a["calls"][0]["prompt"] == 100 and a["calls"][0]["cached"] == 90 and a["calls"][0]["out"] == 5
    assert abs(a["wait_s"] - 25.0) < 1e-9 and abs(a["tool_s"] - 1.5) < 1e-9 and b["wait_s"] == 0.0
    # counters: 3600 requests and 36,000 generation tokens over 1,000 s
    scrape = [{"wall": 1000.0 + s, "m": {"vllm:request_success_total": 3.6 * s, "vllm:generation_tokens_total": 36.0 * s,
                                         "vllm:prompt_tokens_total": 100.0 * s, "vllm:num_preemptions_total": 0.0,
                                         "vllm:prefix_cache_queries_total": 10.0 * s,
                                         "vllm:prefix_cache_hits_total": 9.0 * s,
                                         "vllm:kv_cache_usage_perc": 0.9 if s % 2 else 0.5}} for s in range(0, 1001)]
    r = wa.rates(scrape, 1000.0, 2000.0)
    assert abs(r["requests_per_h"] - 3.6 * 3600) < 1e-6 and abs(r["gen_tokens_per_s"] - 36.0) < 1e-9
    assert abs(r["tokens_per_s"] - 136.0) < 1e-9 and abs(r["prefix_hit_rate"] - 0.9) < 1e-9
    assert len(wa.block_rates(scrape, 1000.0, 2000.0)) == 1
    kv = wa.kv_profile(scrape, 1000.0, 2000.0)
    assert abs(kv["kv_share_at_rule"] - 500 / 1001) < 1e-9


def test_rates_across_a_server_restart():
    # stage 1: 0-100 s at 1 request/s; a 50-s gap; stage 2 (counters from 0 again): 150-250 s at 2 requests/s
    seg1 = [{"wall": float(s), "m": {"vllm:request_success_total": float(s), "vllm:generation_tokens_total": 0.0,
                                     "vllm:prompt_tokens_total": 0.0}} for s in range(0, 101)]
    seg2 = [{"wall": 150.0 + s, "m": {"vllm:request_success_total": 2.0 * s, "vllm:generation_tokens_total": 0.0,
                                      "vllm:prompt_tokens_total": 0.0}} for s in range(0, 101)]
    r = wa.rates([seg1, seg2], 0.0, 250.0)
    assert r["seconds"] == 200.0 and abs(r["requests_per_h"] - 300.0 / 200.0 * 3600) < 1e-9, r
    assert abs(wa.rates([seg1, seg2], 50.0, 200.0)["requests_per_h"] - (50.0 + 100.0) / 100.0 * 3600) < 1e-9


def test_paired_bootstrap():
    a = {f"t{i}": [float(i), float(i) + 1] for i in range(20)}
    same = wa.boot_paired(a, a, "x")
    assert same["n_tasks"] == 20 and all(same[s]["diff"] == 0 and same[s]["ci"] == [0.0, 0.0] for s in wa.STATS)
    b = {k: [v + 2.0 for v in vs] for k, vs in a.items()}
    shift = wa.boot_paired(a, b, "y")
    assert all(abs(shift[s]["diff"] - 2.0) < 1e-9 and abs(shift[s]["ci"][0] - 2.0) < 1e-9 for s in wa.STATS)
    assert wa.boot_paired(a, {"zz": [1.0]}, "z") == {"n_tasks": 0}


def test_paired_pass_rate():
    def run(rewards):
        return {"n": 4, "throughput": {}, "_groups": {"req_window": {}, "e2e": {},
                                                      "reward": {f"t{i}": [r] for i, r in enumerate(rewards)}}}
    none = run([1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0])
    wait = run([1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0])
    c = wa.contrast(none, wait)
    assert c["pass"]["n_tasks"] == 8 and abs(c["pass"]["diff"] - 0.25) < 1e-9
    assert c["pass"]["ci"][0] <= 0.25 <= c["pass"]["ci"][1] and c["pass"]["ci"][0] >= 0.0
    table = wa.render({"runs": {}, "contrasts": {"4": c}})
    assert "pass rate (points)" in table and "| 25.0 [" in table


def test_pin_tree_resets_widened_threads():
    import subprocess
    import threading
    import time
    import wb_cell
    assert wb_cell.cpu_list("0-3,8,10-11") == {0, 1, 2, 3, 8, 10, 11}
    every = set(range(os.cpu_count()))
    want = wb_cell.cpu_list(wb_cell.SERVER_CPUS)
    child = subprocess.Popen([sys.executable, "-c", "import threading, time\n"
                              "ts = [threading.Thread(target=time.sleep, args=(20,)) for _ in range(3)]\n"
                              "[t.start() for t in ts]; time.sleep(20)"])
    try:
        time.sleep(0.5)
        tids = [int(t) for t in os.listdir(f"/proc/{child.pid}/task")]
        for tid in tids:                                  # widen, as vLLM's stack did
            os.sched_setaffinity(tid, every)
        with tempfile.TemporaryDirectory() as d:
            log, stop = Path(d) / "pin.jsonl", threading.Event()
            th = threading.Thread(target=wb_cell.pin_tree, args=(os.getpid(), want, log, stop, 0.2), daemon=True)
            th.start()
            time.sleep(1.0)
            stop.set()
            th.join(2)
            assert all(os.sched_getaffinity(tid) == want for tid in tids)
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            assert rows and rows[0]["pid"] == child.pid and rows[0]["threads_reset"] == len(tids) == 4
    finally:
        child.kill()


def test_child_lets_pacing_tools_through():
    import wb_child

    class Block:
        def __init__(self, name):
            self.name = name

    def permission_hook(block):
        return None

    class Mod:
        HOOKS = {"PreToolUse": ["earlier_hook", permission_hook]}
        tools = []

        def wait_tools(self):
            return self.tools
    mod = Mod()
    mod.permission_hook = permission_hook

    def fake_install(m, run):                  # what s15_child.install_profile does to the hook
        hooks = m.HOOKS["PreToolUse"]
        hooks[hooks.index(m.permission_hook)] = lambda b: None if b.name in ("bash",) else f"denied {b.name}"
    saved = wb_child._install_profile
    try:
        wb_child._install_profile = fake_install
        mod.tools = [{"name": "wait"}]
        wb_child.install_profile(mod, None)
        hook = mod.HOOKS["PreToolUse"][1]
        assert mod.HOOKS["PreToolUse"][0] == "earlier_hook"
        assert hook(Block("wait")) is None and hook(Block("bash")) is None and hook(Block("web")) == "denied web"
        mod.HOOKS = {"PreToolUse": [permission_hook]}
        mod.tools = []                         # pacing off: wait stays denied
        wb_child.install_profile(mod, None)
        assert mod.HOOKS["PreToolUse"][0](Block("wait")) == "denied wait"
    finally:
        wb_child._install_profile = saved


def test_exec_output_cap():
    import asyncio
    import wb_cap

    class Result:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    async def terminate(p):
        if p.returncode is None:
            p.kill()
            await p.wait()

    async def run(cmd, cap, log, stream, timeout=30):
        p = await asyncio.create_subprocess_exec("bash", "-c", cmd, stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        lines = []

        async def on_output(line, _):
            lines.append(line)
        r = await wb_cap.collect(p, timeout_sec=timeout, cap=cap, log=log, terminate=terminate, result=Result,
                                 on_output=on_output if stream else None)
        return r, lines

    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "exec_cap.jsonl"
        for stream in (False, True):
            r, lines = asyncio.run(run("printf 'a\\nb'; echo err >&2; exit 3", 1 << 20, log, stream))
            assert (r.stdout, r.stderr, r.return_code) == ("a\nberr\n", None, 3)   # under the cap: as Harbor's
            assert lines == (["a\n", "berr\n"] if stream else [])
            assert asyncio.run(run("true", 1 << 20, log, stream))[0].stdout is None
            r, lines = asyncio.run(run("head -c 5000000 /dev/zero | tr '\\0' x; exit 4", 1_000_000, log, stream))
            assert r.stdout == "x" * 1_000_000 and r.return_code == 4
            assert lines == (["x" * 1_000_000] if stream else [])
            try:
                asyncio.run(run("sleep 5", 1 << 20, log, stream, timeout=1))
                raise AssertionError("no timeout")
            except RuntimeError as exc:
                assert str(exc) == "Command timed out after 1 seconds"
        notes = [json.loads(x) for x in log.read_text().splitlines()]
        assert [(n["kept"], n["dropped"], n["rc"]) for n in notes] == [(1_000_000, 4_000_000, 4)] * 2


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
