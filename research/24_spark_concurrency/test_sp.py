#!/usr/bin/env python3
"""CPU-only tests for topic 24. Run as a script with the vLLM venv (it has no pytest):

    ~/.venv/bin/python research/24_spark_concurrency/test_sp.py

  plugin      sp_reqlog writes one request line and one step line from synthetic vLLM stats, and vLLM finds it
  hw          sp_hw.py's control protocol (run, pause, run, stop) with stand-in processes, no root
  hwread      perf CSV parsing, the monotonic anchoring, window sums; GPU rows from a synthetic Nsight SQLite export
  trials      the cell runner's view of trials: first sessions by agent start, the stop rule
  taskdirs    every generated task folder is a valid Harbor 0.24.0 task with verification off (Harbor venv)
  child       sp_child's request ids, clock stamps and kept long replies (Harbor venv: anthropic 1.12.1)
  variants    use_variant() switches the model, pool and flags for the server and for exp02; BF16 keeps part 1's flags
  modelbytes  the byte model's categories and totals on a synthetic quantized checkpoint (headers only)
  stable      the stop rule on synthetic series (analysis venv: numpy, pandas)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
import sp_common as C  # noqa: E402

HARBOR_PY = str(C.HARBOR_VENV / "bin" / "python")


def test_plugin():
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, SP_REQLOG_DIR=d, PYTHONPATH=str(C.PLUGIN))
        code = r"""
from vllm.v1.metrics.loggers import load_stat_logger_plugin_factories
from vllm.v1.metrics import stats as st
cls = [c for c in load_stat_logger_plugin_factories() if c.__name__ == "SPReqLog"][0]
lg = cls(None, 0)
it = st.IterationStats()
it.finished_requests.append(st.FinishedRequestStats(finish_reason="stop", request_id="chatcmpl-t~1", e2e_latency=10.0,
    num_prompt_tokens=2000, num_generation_tokens=40, queued_time=0.5, prefill_time=1.5, inference_time=9.5,
    decode_time=8.0, mean_time_per_output_token=0.205, num_cached_tokens=1568))
it.num_generation_tokens = 1; it.inter_token_latencies_iter = [0.2]
s = st.SchedulerStats(num_running_reqs=1, kv_cache_usage=0.1)
s.iteration_details = st.SchedulerIterationDetails(iteration_index=3, num_ctx_requests=0, num_ctx_tokens=0,
    num_generation_requests=1, num_generation_tokens=1, elapsed_ms=0.1)
lg.record(s, it); lg.log()
assert lg.errors == 0
"""
        p = subprocess.run([str(C.VLLM_PY), "-c", code], env=env, capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stderr[-2000:]
        req = [json.loads(x) for f in Path(d).glob("requests_*.jsonl") for x in f.read_text().splitlines()]
        step = [json.loads(x) for f in Path(d).glob("steps_*.jsonl") for x in f.read_text().splitlines()]
        assert len(req) == 1 and req[0]["id"] == "chatcmpl-t~1" and req[0]["c"] == 1568 and req[0]["g"] == 40
        assert len(step) == 1 and step[0]["it"] == 3 and step[0]["gr"] == 1 and step[0]["itl"] == [0.2]


def test_hw():
    with tempfile.TemporaryDirectory() as d:
        p = subprocess.Popen(["/usr/bin/python3", "-I", str(DIR / "sp_hw.py"), "run", "--out", d, "--owner",
                              os.environ.get("USER") or "friedrichqi", "--fake"])
        try:
            def hb():
                for _ in range(100):
                    try:
                        return json.loads((Path(d) / "heartbeat.json").read_text())
                    except (OSError, ValueError):
                        time.sleep(0.1)
                return {}

            def until(pred, timeout=15):
                t_end = time.monotonic() + timeout
                while time.monotonic() < t_end:
                    if pred(hb()):
                        return True
                    time.sleep(0.3)
                return False

            assert until(lambda h: h.get("state") == "run" and h.get("perf") and h.get("gpu"))
            (Path(d) / "control").write_text("pause\n")
            assert until(lambda h: h.get("state") == "pause" and not h.get("perf") and not h.get("gpu"))
            (Path(d) / "control").write_text("run\n")
            assert until(lambda h: h.get("state") == "run" and h.get("perf"))
            (Path(d) / "control").write_text("stop\n")
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
        s = next(Path(d).glob("session_*"))
        segs = [json.loads(x) for x in (s / "segments.jsonl").read_text().splitlines()]
        assert [x["kind"] for x in segs].count("perf") == 2 and all("start" in x and "end" in x for x in segs)
        assert json.loads((s / "heartbeat.json").read_text())["state"] == "stopped"


def test_hwread():
    import sp_hwread as H
    with tempfile.TemporaryDirectory() as d:
        s = Path(d) / "session_x"
        s.mkdir()
        t0 = 10_000_000_000
        (s / "perf_000.csv").write_text(
            "# started on Mon Nov  9 23:00:00 2026\n\n"
            "1.000100000,CPU5,2000000,,armv8_pmuv3_1/ll_cache_miss_rd/,1000000000,100.00,,\n"
            "1.000100000,CPU6,1000000,,armv8_pmuv3_1/ll_cache_miss_rd/,1000000000,100.00,,\n"
            "1.000100000,CPU1,<not counted>,,armv8_pmuv3_0/ll_cache_miss_rd/,0,0.00,,\n"
            "2.000100000,CPU5,2000000,,armv8_pmuv3_1/ll_cache_miss_rd/,1000000000,100.00,,\n")
        rep = s / "gpu_000.nsys-rep"
        rep.write_text("x")
        db = s / "gpu_000.sqlite"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE TARGET_INFO_SESSION_START_TIME (utcEpochNs INTEGER, utcTime TEXT, localTime TEXT);
            CREATE TABLE TARGET_INFO_GPU_METRICS (typeId INTEGER, metricId INTEGER, metricName TEXT);
            CREATE TABLE GPU_METRICS (timestamp INTEGER, typeId INTEGER, metricId INTEGER, value INTEGER);
            INSERT INTO TARGET_INFO_SESSION_START_TIME VALUES (5000000000000, '', '');
            INSERT INTO TARGET_INFO_GPU_METRICS VALUES (1, 7, 'L2 Read Misses [Sectors]');
            INSERT INTO GPU_METRICS VALUES (1000000, 1, 7, 100), (11000000, 1, 7, 300);
        """)
        con.commit()
        con.close()
        os.utime(rep, (time.time() - 100, time.time() - 100))
        segs = [{"kind": "perf", "file": "perf_000.csv", "rc": 0, "start": {"t": t0, "w": 5_000_000_000_000 + t0}},
                {"kind": "gpu", "file": "gpu_000.nsys-rep", "rc": 0, "report": True,
                 "start": {"t": t0, "w": 5_000_000_000_000 + t0}}]
        (s / "segments.jsonl").write_text("".join(json.dumps(x) + "\n" for x in segs))
        rows = H.perf_rows(s)
        assert len(rows) == 3 and rows[0][0] == t0 + 1_000_100_000 and rows[0][1] == 5
        w = H.window_perf(rows, t0, t0 + 3_000_000_000, {5, 6})
        assert w["ll_cache_miss_rd"] == 5_000_000 and w["_seconds"] == 2.0
        g = H.gpu_rows(s)
        # session start (wall) 5e12, mono = wall - (w - t) = 5e12 + ts - 5e12 = ts
        assert g == [(1000000, "L2 Read Misses [Sectors]", 100), (11000000, "L2 Read Misses [Sectors]", 300)]
        gw = H.window_gpu(g, 0, 20_000_000)
        assert gw["L2 Read Misses [Sectors]"]["sum"] == 400 and gw["L2 Read Misses [Sectors]"]["n"] == 2


def test_trials():
    import sp_cell as SC
    with tempfile.TemporaryDirectory() as d:
        job = Path(d)

        def trial(name, t0_mono, ended=False, snipped=False):
            s = job / name / "agent" / "s15"
            s.mkdir(parents=True)
            (s / "wb_t0.json").write_text(json.dumps({"t0": 1.0, "t0_mono": t0_mono, "pid": 999999999}))
            (s / "calls.jsonl").write_text("{}\n{}\n")
            if ended:
                (s / "result.json").write_text(json.dumps({"status": "budget", "counter": {"lead": 40}}))
            if snipped:
                (s / "events.jsonl").write_text('{"kind": "snip_compact"}\n')

        trial("a__1", 30, ended=True, snipped=True)
        trial("b__2", 10, ended=False)
        trial("c__3", 20, ended=True)
        (job / "d__4").mkdir()                       # environment still starting: no agent yet
        states = [SC.trial_state(p) for p in SC.trial_dirs(job)]
        first = SC.first_sessions(states, 2)
        assert [s["trial"] for s in first] == ["b__2", "c__3"]
        # b__2's child pid does not exist, so it counts as ended ("gone")
        assert first[0]["ended"] and first[0]["status"] == "gone"
        assert SC.window_done(states, 2)
        assert not SC.window_done(states, 4)
        assert states[0]["snipped"] and states[0]["calls"] == 2


def test_taskdirs():
    if not C.TASKDIRS.exists():
        print("  (skipped: no task folders; run sp_tasks.py draw and build)")
        return
    code = r"""
import sys
from pathlib import Path
from harbor.models.task.task import Task
bad = [p.name for p in Path(sys.argv[1]).iterdir() if not Task.is_valid_dir(p, disable_verification=True)]
print("invalid:", bad)
sys.exit(1 if bad else 0)
"""
    p = subprocess.run([HARBOR_PY, "-c", code, str(C.TASKDIRS)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr[-1500:]
    for q in sorted((C.QUEUE / "main").iterdir()):
        assert (q / "task.toml").exists() and not any((q / "environment").iterdir())


def test_child():
    code = r"""
import json, sys, tempfile, types
from pathlib import Path
sys.argv = ["sp_child.py"]
import importlib.util
spec = importlib.util.spec_from_file_location("sp_child", sys.argv[0] if False else "%s")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
logged = []
class FakeRun:
    def __init__(self):
        self.staging = Path(tempfile.mkdtemp())
    def log_call(self, rec):
        logged.append(rec)
run = FakeRun()
# stand-in for s15_child's own patch: a create that logs a record like s15_child does, then returns
def fake_inner_patch(run_):
    from anthropic.resources.messages import messages as sdk
    def create(self, *a, **kw):
        run_.log_call({"call": len(logged), "headers": kw.get("extra_headers")})
        return "reply"
    sdk.Messages.create = create
m._install_client_patch = fake_inner_patch
import os
os.environ["S15_SESSION_ID"] = "django__django-1__abc"
m.install_client_patch(run)
from anthropic.resources.messages import messages as sdk
assert sdk.Messages.create(None, model="x") == "reply"
assert sdk.Messages.create(None, model="x") == "reply"
assert [r["rid"] for r in logged] == ["django__django-1__abc~0", "django__django-1__abc~1"], logged
assert logged[0]["headers"]["X-Request-Id"] == "django__django-1__abc~0"
assert logged[0]["t_send"] <= logged[0]["t_log"]
# kept long replies
class Base:
    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
mod = types.SimpleNamespace(RecoveryState=Base)
m._install_profile = lambda mod_, run_: None
m.install_profile(mod, run)
st = mod.RecoveryState()
st.has_escalated = False
assert st.has_escalated is True
print("ok")
""" % (DIR / "sp_child.py")
    env = dict(os.environ, WB_ADAPTER_DIR=str(C.REPO / "research" / "16_epic_harness" / "harness_eval" / "adapter"),
               S15_VENDOR=str(DIR / "vendor" / "s15"))
    p = subprocess.run([HARBOR_PY, "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert p.returncode == 0 and "ok" in p.stdout, p.stdout + p.stderr[-2000:]


def test_variants():
    code = r"""
import sys
sys.path.insert(0, sys.argv[1])
import sp_common as C
base0, extra0 = list(C.BASE), list(C.EXTRA)
v = C.use_variant("nvfp4", "66G")
assert C.MODEL == "unsloth/Qwen3.8-27B-NVFP4" and C.POOL == "66G" and C.VARIANT == "nvfp4"
assert C.BASE[C.BASE.index("--served-model-name") + 1] == C.MODEL
assert C.EXTRA[-2:] == ["--kv-cache-dtype", "bfloat16"]
assert str(C.hub_dir()).endswith("models--unsloth--Qwen3.8-27B-NVFP4")
C.use_variant("bf16")
assert C.BASE == base0 and C.EXTRA == extra0 and C.POOL == "36G", "BF16 must keep the flags of part 1"
import sp_chain
names = [c[0] for c in sp_chain.CELLS]
assert len(names) == len(set(names)) == 12 and names[0] == "n08"
assert {(c[1], c[2]) for c in sp_chain.CELLS} == {(v, n) for v in C.VARIANTS for n in (1, 2, 4, 8)}
print("ok")
"""
    env = {k: v for k, v in os.environ.items() if k != "SP_POOL"}
    p = subprocess.run([sys.executable, "-c", code, str(DIR)], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and "ok" in p.stdout, p.stderr[-800:]


def test_modelbytes():
    import struct
    import sp_modelbytes as MB
    with tempfile.TemporaryDirectory() as d:
        snap = Path(d) / "hub" / "models--x--y" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        cfg = {"text_config": {"layer_types": ["linear_attention", "full_attention"], "linear_num_value_heads": 48,
                               "linear_key_head_dim": 128, "linear_value_head_dim": 128, "linear_num_key_heads": 16,
                               "linear_conv_kernel_dim": 4, "num_key_value_heads": 4, "head_dim": 256,
                               "hidden_size": 5120, "intermediate_size": 17408, "vocab_size": 248320},
               "quantization_config": {"quant_method": "compressed-tensors", "format": "mixed-precision"}}
        (snap / "config.json").write_text(json.dumps(cfg))
        tens = {  # name: (dtype, shape, bytes)
            "model.language_model.embed_tokens.weight": ("BF16", [10, 4], 80),
            "lm_head.weight": ("F8_E4M3", [10, 4], 40), "lm_head.weight_scale": ("BF16", [10, 1], 20),
            "model.language_model.layers.0.mlp.up_proj.weight_packed": ("U8", [8, 2], 16),
            "model.language_model.layers.0.mlp.up_proj.weight_scale": ("F8_E4M3", [8, 1], 8),
            "model.language_model.layers.0.linear_attn.out_proj.weight": ("F8_E4M3", [4, 6], 24),
            "model.language_model.layers.0.linear_attn.conv1d.weight": ("BF16", [6, 1, 4], 48),
            "model.language_model.layers.1.self_attn.q_proj.weight": ("F8_E4M3", [4, 4], 16),
            "model.language_model.layers.1.input_layernorm.weight": ("BF16", [4], 8),
            "model.visual.blocks.0.attn.qkv.weight": ("BF16", [4, 4], 32),
        }
        def write(name, items):
            h, off = {}, 0
            for k, (dt, shape, b) in items.items():
                h[k] = {"dtype": dt, "shape": shape, "data_offsets": [off, off + b]}
                off += b
            raw = json.dumps(h).encode()
            (snap / name).write_bytes(struct.pack("<Q", len(raw)) + raw)
        write("model.safetensors", tens)
        write("model_mtp.safetensors", {"mtp.layers.0.mlp.up_proj.weight": ("BF16", [4, 4], 32)})
        old_home, old_var = C.HF_HOME, dict(C.VARIANTS)
        try:
            C.HF_HOME = d
            C.VARIANTS["t"] = {"repo": "x/y", "label": "t", "pool": "36G", "flags": []}
            m = MB.budget("t")
        finally:
            C.HF_HOME = old_home
            C.VARIANTS.clear()
            C.VARIANTS.update(old_var)
        cat = m["categories"]
        assert cat == {"embed": 80, "lm_head": 60, "mlp": 24, "gdn_proj": 72, "attn_proj": 16, "norms": 8,
                       "vision": 32, "mtp": 32}, cat
        assert m["w_step"] == 60 + 24 + 72 + 16 + 8 and m["resident_bytes"] == m["w_step"] + 80
        # matmul weights: lm_head 40, packed FP4 8x2 bytes = 32 values, out_proj 24, q_proj 16 (conv1d is 3-D: not one)
        assert m["linear_weights"] == 40 + 32 + 24 + 16, m["linear_weights"]
        assert m["kv_bytes_token"] == 1 * 2 * 4 * 256 * 2 and m["by_dtype"]["mlp"] == {"U8": 16, "F8_E4M3": 8}


def test_stable():
    code = r"""
import sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import sp_stable as S
n = 4 * 3600
def series(out, ended):
    z = np.ones(n)
    return {"steady": np.arange(n) >= 1800, "n": 8, "first_ended_s": np.array(ended, float), "output_tok_s": out,
            "computed_prompt_tok_s": 300 * z, "kv_pinned": 0.3 * z, "step_ms": 500 * z}
flat = 20 * np.ones(n)
r = S.check(series(flat, [3000, 3100, 3200, 3300]), 2 * 3600)
assert r["stable"] and r["steady_s"] == 5400, r
assert not S.check(series(flat, [3000, 3100, 3200, 3300]), 3000 + 1800)["stable"]              # steady < 90 min
r = S.check(series(flat, [3000, 3100, 3200]), 2 * 3600)                                         # 3 of 8 ended
assert not r["stable"] and "first sessions" in r["why"], r
ramp = np.where(np.arange(n) < 6000, 30.0, 20.0)                                                # a late change
r = S.check(series(ramp, [1, 2, 3, 4]), 2 * 3600)
assert not r["stable"] and "output_tok_s" in r["why"], r
assert S.check(series(ramp, [1, 2, 3, 4]), 4 * 3600)["stable"]                                  # settled later
assert not S.check(None)["stable"]
print("ok")
"""
    p = subprocess.run([str(C.EXP01_VENV_PY), "-c", code, str(DIR)], capture_output=True, text=True,
                       env=dict(os.environ, HF_HOME=C.HF_HOME))
    assert p.returncode == 0 and "ok" in p.stdout, (p.stdout + p.stderr)[-1200:]


TESTS = [test_plugin, test_hw, test_hwread, test_trials, test_taskdirs, test_child, test_variants, test_modelbytes,
         test_stable]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        t0 = time.monotonic()
        try:
            t()
            print(f"PASS {t.__name__} ({time.monotonic() - t0:.1f} s)")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
