"""Tests of research/12_hw_utilization (no GPU needed).

    ~/.venv/bin/python research/12_hw_utilization/test_hw.py        # or: pytest research/12_hw_utilization

The end-to-end test runs 12's conditions through 11's driver (mu_sweep -> mu_session -> profile_run, via the
MU_EXTRA_CONDITIONS hook) against a fake Anthropic-compatible server whose model calls one read-only bash
tool (`grep -rc`) per turn, with hw_sampler.py watching the driver tree; set HW_SKIP_E2E=1 to skip it.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hw_analyze as ha  # noqa: E402
import hw_sampler as hs  # noqa: E402
import hw_workloads as hw  # noqa: E402

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
DIR11 = REPO / "research" / "11_multiuser_kv"


# ------------------------------------------------------------------------------------------ units
def st(start: int, ppid: int, ut: int, sy: int = 0, cu: int = 0, cs: int = 0) -> dict:
    return {"start": start, "ppid": ppid, "utime": ut, "stime": sy, "cutime": cu, "cstime": cs}


def test_tree_totals_reaped_children():
    clk = hs.CLK
    acc = hs.TreeTotals(("cpu",))
    # driver 1 -> harness 2 -> tool 3
    t1, _ = acc.update([hs.cpu_member(1, "driver", st(10, 0, 1 * clk)),
                        hs.cpu_member(2, "harness", st(11, 1, 2 * clk)),
                        hs.cpu_member(3, "tools", st(12, 2, 3 * clk))])
    assert abs(t1["cpu"] - 6.0) < 1e-9
    # tool 3 finished with 5 s and was reaped by the harness; a tool 4 lived and died between samples (4 s)
    t2, own2 = acc.update([hs.cpu_member(1, "driver", st(10, 0, 1 * clk)),
                           hs.cpu_member(2, "harness", st(11, 1, 2 * clk, cu=9 * clk))])
    assert abs(t2["cpu"] - 12.0) < 1e-9, t2
    assert abs(own2["harness"] - 2.0) < 1e-9 and abs(own2["tools"] - 3.0) < 1e-9, own2
    # the harness finished (reaped by the driver): its 2 + 9 s move into the driver's cutime
    t3, own3 = acc.update([hs.cpu_member(1, "driver", st(10, 0, 1 * clk, cu=11 * clk))])
    assert abs(t3["cpu"] - 12.0) < 1e-9, t3
    assert abs(own3["harness"] - 2.0) < 1e-9
    # the root itself goes away (cell end): its total is kept
    t4, _ = acc.update([])
    assert abs(t4["cpu"] - 12.0) < 1e-9, t4
    # a new root (server restart) adds on top
    t5, _ = acc.update([hs.cpu_member(7, "driver", st(99, 0, 2 * clk))])
    assert abs(t5["cpu"] - 14.0) < 1e-9, t5


def test_tree_totals_parent_and_child_exit_together():
    """harness 2 -> bash 3 -> pytest 4: pytest and bash both exit between two samples; the harness's cutime
    then holds both, and nothing may be counted twice (the J1 bug)."""
    clk = hs.CLK
    acc = hs.TreeTotals(("cpu",))
    t1, _ = acc.update([hs.cpu_member(1, "driver", st(10, 0, 1 * clk)),
                        hs.cpu_member(2, "harness", st(11, 1, 2 * clk)),
                        hs.cpu_member(3, "tools", st(12, 2, 0)),
                        hs.cpu_member(4, "tools", st(13, 3, 80 * clk))])
    assert abs(t1["cpu"] - 83.0) < 1e-9
    # pytest finished with 90 s, bash reaped it (cutime 90) and exited, the harness reaped bash
    t2, _ = acc.update([hs.cpu_member(1, "driver", st(10, 0, 1 * clk)),
                        hs.cpu_member(2, "harness", st(11, 1, 2 * clk, cu=90 * clk))])
    assert abs(t2["cpu"] - 93.0) < 1e-9, t2


def test_parse_stat_odd_comm():
    text = "1234 (VLLM::Engine Core) S 1 1234 1234 0 -1 4194560 100 0 0 0 250 50 7 3 20 0 42 0 999 0 0 " \
           + " ".join(["0"] * 15) + " 17 5 0 0"
    st_ = hs.parse_stat(text)
    assert st_["comm"] == "VLLM::Engine Core" and st_["ppid"] == 1 and st_["utime"] == 250 and st_["stime"] == 50
    assert st_["cutime"] == 7 and st_["cstime"] == 3 and st_["threads"] == 42 and st_["start"] == 999


def test_proc_stat_and_pressure():
    text = "cpu  1 2 3 4 5 6 7 8 0 0\ncpu0 10 0 5 80 5 0 0 0 0 0\ncpu1 1 1 1 1 1 1 1 1 0 0\n"
    got = hs.parse_proc_stat(text, [0])
    assert got == {0: (15, 100)}, got
    psi = hs.parse_pressure("some avg10=0.00 avg60=0.00 avg300=0.00 total=1234\nfull avg10=0 avg60=0 avg300=0 total=5\n")
    assert psi == {"some": 1234, "full": 5}
    assert hs.parse_cpulist("0-2,7,9-10") == [0, 1, 2, 7, 9, 10]


def test_classify():
    assert hs.classify_vllm(0, "vllm", "vllm serve x") == "api"
    assert hs.classify_vllm(1, "VLLM::EngineCor", "VLLM::EngineCore") == "engine"
    assert hs.classify_vllm(2, "VLLM::Worker_TP", "VLLM::Worker_TP0") == "worker"
    assert hs.classify_vllm(1, "python3", "-c from multiprocessing") == "vother"
    assert [hs.classify_agent(d) for d in (0, 1, 2, 3)] == ["driver", "harness", "tools", "tools"]


def test_layouts_and_specs():
    rows = hw.build_specs()
    msgs = hw.check(rows)
    assert "1038 specs" in msgs[0], msgs
    cell, c = hw.CELLS["k1h"], hw.CONDITIONS["hw_k1h_k1"]
    lay = hw.layout(cell, c, list(range(100, 112)))
    assert lay["vllm"] == [100, 101, 102, 103] and lay["sampler"] == [104] and lay["agent"] == [105]
    lay = hw.layout(hw.CELLS["t2"], hw.CONDITIONS["hw_t2_n24"], list(range(24)))
    assert lay["vllm"] == list(range(8)) and lay["sampler"] == [8] and lay["agent"] == list(range(9, 24))
    lay = hw.layout(hw.CELLS["k2"], hw.CONDITIONS["hw_k2_v1"], list(range(12)))
    assert lay["vllm"] == [0] and lay["agent"] == list(range(5, 12))      # agent cores unchanged across K2
    args = hw.server_args(hw.CELLS["t2"])
    assert "hw_scheduler.HWScheduler" in args and args[-2:] == ["--tensor-parallel-size", "2"]
    assert "--kv-offloading-size" in hw.server_args(hw.CELLS["o1"])
    assert hw.CELLS["s1"]["server_env"] == {"HW_STACK_MS": "20"} and "server_env" not in hw.CELLS["p2a"]


def test_stack_sampler():
    """HW_STACK_MS: the sampler sees a busy engine thread's frame and charges that thread's CPU to it."""
    import queue
    import types
    import hw_scheduler as hsch                     # imports vLLM (CPU only)
    stop = time.monotonic() + 0.4

    def spin_target():
        while time.monotonic() < stop:
            pass

    th = threading.Thread(target=spin_target)
    th.start()
    fake = types.SimpleNamespace(sink=types.SimpleNamespace(q=queue.SimpleQueue()), _stk_ms=10.0,
                                 _stack_key=hsch.HWScheduler._stack_key)
    hsch.HWScheduler._stack_loop(fake, th.ident)    # returns once the thread is gone
    th.join()
    recs = []
    while not fake.sink.q.empty():
        recs += fake.sink.q.get()
    assert not [r for r in recs if r["k"] == "err"], recs
    defs = {r["id"]: r["st"] for r in recs if r["k"] == "stkdef"}
    hits = [r for r in recs if r["k"] == "stk" and defs[r["id"]][0].endswith(":spin_target")]
    assert len(hits) >= 10, len(hits)
    assert 0.2 < sum(r["cpu"] for r in hits) < 0.5, sum(r["cpu"] for r in hits)
    assert defs[hits[0]["id"]][0].startswith("12_hw_utilization/test_hw.py:")


def test_stats():
    s = ha.stats([1, 2, 3, 4, 100])
    assert s["median"] == 3 and s["max"] == 100 and abs(s["mean"] - 22) < 1e-9


# ------------------------------------------------------------------------------------ end to end
class FakeModel(BaseHTTPRequestHandler):
    """/v1/messages: a new user turn gets one read-only bash call (grep -rc), the tool result the answer."""
    calls = 0
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, "")
        elif self.path == "/metrics":
            self._send(200, f"vllm:generation_tokens_total {FakeModel.calls * 5}\nvllm:num_requests_running 0\n"
                            "vllm:num_requests_waiting 0\nvllm:kv_cache_usage_perc 0.1\n", "text/plain")
        else:
            self._send(200, {"version": "fake"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        rid = self.headers.get("X-Request-Id")
        with FakeModel.lock:
            FakeModel.calls += 1
            n = FakeModel.calls
        last = body["messages"][-1].get("content")
        tool_result = isinstance(last, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in last)
        if tool_result:
            blocks, stop = [{"type": "text", "text": "Done."}], "end_turn"
        else:
            blocks = [{"type": "tool_use", "id": f"toolu_{n:05d}", "name": "bash",
                       "input": {"command": "grep -rc def research/common s15_integrated_harness | tail -3"}}]
            stop = "tool_use"
        self._send(200, {"id": f"chatcmpl-{rid}", "type": "message", "role": "assistant", "model": body.get("model"),
                         "content": blocks, "stop_reason": stop, "stop_sequence": None,
                         "usage": {"input_tokens": 100, "output_tokens": 5,
                                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}})


def free_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if "429" not in str(port) and "529" not in str(port):
            return port


def test_e2e_fake_server():
    if os.environ.get("HW_SKIP_E2E") == "1":
        print("  (skipped: HW_SKIP_E2E=1)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="hw_e2e_", dir=os.environ.get("HW_TMP")))
    sessions = tmp / "sessions.jsonl"
    with sessions.open("w", encoding="utf-8") as handle:
        for row in hw.build_specs()[:hw.P1[1]]:
            handle.write(json.dumps(row) + "\n")
    conds = tmp / "conditions.json"
    extra = {"hw_t_light": hw.level(2, (0, 2), hw.M32, None), "hw_t_heavy": hw.level(1, (8, 10), hw.M8, None)}
    conds.write_text(json.dumps(extra))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "MU_EXTRA_CONDITIONS": str(conds)}
    try:
        for cond_name in ("hw_t_light", "hw_t_heavy"):
            pidfile = tmp / f"{cond_name}.driver.pid"
            samp = tmp / f"{cond_name}.samp"
            sampler = subprocess.Popen([sys.executable, str(DIR / "hw_sampler.py"), "run", "--out", str(samp),
                                        "--driver-pid-file", str(pidfile), "--metrics-url",
                                        f"http://127.0.0.1:{port}/metrics", "--duration", "900"], env=env)
            cmd = [sys.executable, str(DIR11 / "mu_sweep.py"), "run", "--cond", cond_name,
                   "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(tmp / "lanes"),
                   "--sessions", str(sessions), "--cells-root", str(tmp / "cells"), "--think-scale", "0",
                   "--fillers", "off", "--stagger-s", "1"]
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            pidfile.write_text(str(proc.pid))
            out, _ = proc.communicate(timeout=1500)
            time.sleep(1.5)
            sampler.terminate()
            sampler.wait(timeout=60)
            assert proc.returncode == 0, out[-3000:]
            runs = sorted((tmp / "cells" / cond_name / "runs").glob("MU-*"))
            assert len(runs) == 2, runs
            for run in runs:
                meta = json.loads(next(run.glob("*.meta.json")).read_text())
                assert meta["status"] == "ok", meta
                ev = ha.trace_events(run)
                bash = [e for e in ev if e.get("event") == "tool_start" and (e.get("data") or {}).get("tool") == "bash"
                        or e.get("event") == "tool_start" and "bash" in json.dumps(e.get("data", {}))]
                assert bash, f"no bash tool call in {run.name}"
            host = ha.read_jsonl(samp / "host.jsonl")
            last = host[-1]["cpu"]
            tools = last["agent_total"] - last.get("driver", 0) - last.get("harness", 0)
            assert last["agent_total"] > 0 and last.get("harness", 0) > 0, last
            assert tools > 0, f"tool CPU not accounted: {last}"
            assert max(r["pids"]["n_harness"] for r in host) >= 1
            slow = ha.read_jsonl(samp / "slow.jsonl")
            assert any((r.get("m") or {}).get("vllm:kv_cache_usage_perc") == 0.1 for r in slow)
            print(f"  {cond_name}: agent {last['agent_total']:.2f} s = driver {last.get('driver', 0):.2f} + "
                  f"harness {last.get('harness', 0):.2f} + tools {tools:.2f}")
    finally:
        server.shutdown()


def test_pipeline_fake_vllm():
    """hw_pipeline.sh end to end without a GPU: a windowed cell (two levels, hard stop, prefix-cache reset)
    and a restart-per-level cell, served by fake_vllm.py, with windows scaled to seconds."""
    if os.environ.get("HW_SKIP_E2E") == "1":
        print("  (skipped: HW_SKIP_E2E=1)")
        return
    tmp = Path(tempfile.mkdtemp(prefix="hw_pipe_", dir=os.environ.get("HW_TMP")))
    data = tmp / "data"
    env = {**os.environ, "HW_TEST_CELLS": "1"}
    subprocess.run([sys.executable, str(DIR / "hw_workloads.py"), "build", "--data", str(data)], env=env,
                   check=True, capture_output=True)
    wrapper = tmp / "vllm"
    wrapper.write_text(f"#!/bin/bash\nexec {sys.executable} {DIR / 'fake_vllm.py'} \"$@\"\n")
    wrapper.chmod(0o755)
    env.update(HW_DATA=str(data), HW_SESSIONS=str(data / "sessions.jsonl"),
               MU_EXTRA_CONDITIONS=str(data / "conditions.json"), VLLM_BIN=str(wrapper), HW_WINDOW_SCALE="0.03",
               LOCAL_ROOT=str(tmp / "local"), JOB_SECONDS="3600")
    proc = subprocess.run(["bash", str(DIR / "hw_pipeline.sh"), "-", "tw", "tr"], env=env, capture_output=True,
                          text=True, timeout=1800)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    cells = data / "cells"
    joblog = next((data / "jobs").glob("*.log")).read_text()
    for cell, levels, servers in (("tw", ["hw_tw_n02", "hw_tw_n03"], 1), ("tr", ["hw_tr_v4", "hw_tr_v2"], 2)):
        assert (cells / cell / "COMPLETE").exists(), joblog[-3000:]
        for lv in levels:
            assert (cells / cell / "levels" / lv / "WINDOW_DONE").exists(), (lv, joblog[-2000:])
            runs = list((cells / cell / "levels" / lv / "runs").glob("MU-*"))
            assert runs, lv
        srvs = sorted((cells / cell / "server").glob("*"))
        assert len(srvs) == servers, srvs
        for srv in srvs:
            marks = [json.loads(line) for line in (srv / "markers.jsonl").read_text().splitlines()]
            evs = [m["ev"] for m in marks]
            assert evs[:2] == ["launch", "healthy"] and "level_start" in evs and "level_end" in evs, evs
            host = ha.read_jsonl(srv / "samp" / "host.jsonl.xz")
            assert host and max(r["pids"]["n_harness"] for r in host) >= 2, srv
            assert host[-1]["cpu"]["agent_total"] > 0 and host[-1]["cpu"].get("vllm_total", 0) >= 0
            assert any("engine" in r["cpu"] for r in host), "EngineCore child not classified"
            assert (srv / "samp" / "fast.jsonl.xz").exists() and (srv / "samp" / "slow.jsonl.xz").exists()
    assert joblog.count("prefix cache reset") >= 4, joblog[-2000:]
    print(f"  pipeline ok: {joblog.count('sweep exit')} levels")


if __name__ == "__main__":      # the project venv has no pytest; run the tests directly
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
