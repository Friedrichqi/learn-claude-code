#!/usr/bin/env python3
"""Topic 24 checks before the main cells (PLAN.md, stages S1a and S1b).

  server     start vLLM with the final flags and the plugin; check the pool (blocks, block size, cache mode) and send a
             scripted three-call session: plugin records, request ids, and cached tokens at the 784-token boundary
  counters   S1a: known loads (umbench GPU and CPU streams, a BF16 GEMM) while the root sampler (sp_hw.py) runs;
             writes the load windows; sp_analyze.py check compares them with the counters
  overhead   S1b: exp02's synthetic decode load at 2 users with GPU sampling on and paused
  containers start all task containers once, idle, and read their memory
  dryrun     no GPU: Harbor, the agent, the child and a task container against a fake model (one bash call)

    ~/.venv/bin/python research/24_spark_concurrency/sp_check.py server
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402

CHECKS = C.RUNS / "checks"


def _post_json(url: str, body: dict, headers: dict | None = None, timeout: float = 1800) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def gpu_apps() -> str:
    return subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
                          capture_output=True, text=True).stdout.strip()


# ---------------------------------------------------------------------------------------------------------------------
def scripted_session(n_calls: int = 3, max_tokens: int = 160) -> list[dict]:
    """Three append-only calls through vLLM's Anthropic endpoint, as s15 makes them (system + growing messages), each
    with X-Request-Id check~<k>. The system prompt is a frozen repo document (~6k tokens), so the prompt spans several
    784-token blocks."""
    system = (C.REPO / "s15_integrated_harness" / "ARCHITECTURE.md").read_text()[:24000]
    msgs = [{"role": "user", "content": "In two sentences, what does this document describe?"}]
    follow = ["Name one component it mentions, in one sentence.", "Name another component, in one sentence."]
    out = []
    for k in range(n_calls):
        body = {"model": C.MODEL, "system": system, "messages": msgs, "max_tokens": max_tokens}
        t0 = time.monotonic()
        r = _post_json(f"{C.URL}/v1/messages", body, {"X-Request-Id": f"check~{k}", "x-api-key": "EMPTY",
                                                      "anthropic-version": "2023-06-01"})
        dt = time.monotonic() - t0
        u = r.get("usage", {})
        prompt = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0) or 0) + \
            int(u.get("cache_creation_input_tokens", 0) or 0)
        out.append({"k": k, "s": round(dt, 2), "prompt": prompt, "usage": u, "stop": r.get("stop_reason"),
                    "types": [b.get("type") for b in r.get("content", [])]})
        C.log(f"  call {k}: {dt:.1f} s, prompt {prompt}, usage {u}, stop {r.get('stop_reason')}")
        msgs = msgs + [{"role": "assistant", "content": r.get("content", [])},
                       {"role": "user", "content": follow[k] if k < len(follow) else "Thanks."}]
    return out


BF16_PROC_GIB = 86.97      # GPU memory of the BF16 server with a 36 GiB pool, idle after its first requests (cell n08,
                           # baseline minute); under 8 users it was 87.71
TOOLS = [{"name": "bash", "description": "Run a shell command and return its output.",
          "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}]


def gpu_proc_gib() -> float | None:
    """GPU memory of the largest compute process (the vLLM engine), GiB."""
    p = subprocess.run(["nvidia-smi", "--query-compute-apps=used_memory", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    vals = [float(x) for x in p.stdout.split() if x.strip().replace(".", "").isdigit()]
    return max(vals) / 1024 if vals else None


def tool_call_check() -> dict:
    """One request that should come back as a tool call: the quantized checkpoints must still drive the harness."""
    body = {"model": C.MODEL, "max_tokens": 300, "tools": TOOLS, "system": "You are a coding agent. Act, don't explain.",
            "messages": [{"role": "user", "content": "List the files in /tmp with the bash tool."}]}
    r = _post_json(f"{C.URL}/v1/messages", body, {"X-Request-Id": "check~tool", "x-api-key": "EMPTY",
                                                  "anthropic-version": "2023-06-01"})
    blocks = r.get("content", [])
    tu = [b for b in blocks if b.get("type") == "tool_use"]
    return {"stop": r.get("stop_reason"), "types": [b.get("type") for b in blocks],
            "tool": tu[0].get("name") if tu else None, "input": tu[0].get("input") if tu else None,
            "ok": bool(tu) and tu[0].get("name") == "bash" and isinstance(tu[0].get("input"), dict)
            and "command" in tu[0]["input"]}


def cmd_server(a) -> int:
    C.use_variant(a.variant, a.pool)
    out = CHECKS / f"server_{a.variant}_{time.strftime('%m%d_%H%M%S')}"
    raw, logs, reqlog = out / "raw", out / "logs", out / "reqlog"
    rs = C.exp02(raw, logs, out / "check.log")
    apps = gpu_apps()
    if apps:
        C.log(f"refused: GPU compute apps running: {apps}")
        return 2
    res: dict = {"t_start": C.clocks(), "variant": C.VARIANT, "repo": C.MODEL, "snapshot": str(C.snapshot_dir() or ""),
                 "pool": C.POOL, "flags": C.BASE + C.EXTRA}
    rs.evict_model_pages()
    tag = "check"
    srv = rs.Server(tag, extra=C.EXTRA, env_extra=C.server_env(reqlog), pool=C.POOL, prefix_caching=True)
    try:
        srv.wait_ready(a.ready_timeout)
        res["ready_s"] = (srv.t_ready - srv.t_launch) / 1e9
        rs.dump_metric_names(tag)
        res["server_log"] = rs.parse_server_log(tag)
        rs.evict_model_pages()
        metas = sorted(reqlog.glob("meta_*.json"))
        res["meta"] = json.loads(metas[-1].read_text()) if metas else None
        mem = C.meminfo()
        res["mem_after_ready_gib"] = {k: round(mem[k] / 2**30, 2) for k in ("MemAvailable", "Cached", "MemFree")}
        C.log(f"server ready after {res['ready_s']:.0f} s; meta {res['meta']}")
        res["calls"] = scripted_session()
        res["tool_call"] = tool_call_check()
        C.log(f"  tool call: {res['tool_call']}")
        time.sleep(3)
        res["gpu_proc_gib"] = gpu_proc_gib()
        mem = C.meminfo()
        res["mem_after_calls_gib"] = round(mem["MemAvailable"] / 2**30, 2)
        import sp_cell
        res["kernel_lines"] = sp_cell.kernel_lines(logs / f"server_{tag}.log", 60)
    finally:
        srv.stop()
    reqs = [json.loads(line) for p in sorted(reqlog.glob("requests_*.jsonl")) for line in open(p) if line.strip()]
    steps = [json.loads(line) for p in sorted(reqlog.glob("steps_*.jsonl")) for line in open(p) if line.strip()]
    res["plugin"] = {"requests": reqs, "n_steps": len(steps), "step_keys": sorted({k for s in steps for k in s})}
    # checks
    meta = res.get("meta") or {}
    ok = {}
    ok["blocks_784"] = meta.get("block_size") == 784
    ok["align_mode"] = str(meta.get("mamba_cache_mode")) == "align"
    ok["num_gpu_blocks"] = meta.get("num_gpu_blocks")
    by_id = {r["id"]: r for r in reqs}
    ok["ids"] = all(f"chatcmpl-check~{c['k']}" in by_id for c in res["calls"])
    bounds = []
    for prev, cur in zip(res["calls"], res["calls"][1:]):
        want = math.floor((prev["prompt"] - 1) / 784) * 784
        got = int(cur["usage"].get("cache_read_input_tokens") or 0)
        plug = by_id.get(f"chatcmpl-check~{cur['k']}", {}).get("c")
        bounds.append({"k": cur["k"], "prev_prompt": prev["prompt"], "want": want, "usage_cached": got,
                       "plugin_cached": plug})
    ok["boundary"] = bounds
    ok["boundary_ok"] = all(b["usage_cached"] == b["want"] == b["plugin_cached"] for b in bounds)
    ok["steps_have_details"] = bool(steps) and all(k in res["plugin"]["step_keys"] for k in ("it", "ct", "gt", "ms"))
    ok["tool_call"] = bool(res.get("tool_call", {}).get("ok"))
    tp = [r["tpot"] for r in reqs if r["id"].startswith("chatcmpl-check~") and r.get("tpot")]
    res["tpot_ms_1user"] = round(1000 * sorted(tp)[len(tp) // 2], 1) if tp else None
    kl = " ".join(res.get("kernel_lines") or []).lower()
    # (the FP8 model logs "Auto-disabled DeepGemm ... Falling back to CUTLASS": that is the native CUTLASS kernel)
    ok["no_fallback_kernel"] = not any(w in kl for w in ("emulation", "marlin", "weight-only"))
    # the pool this variant gets: BF16's 36 GiB plus the GPU memory its weights free (PLAN2.md), whole GiB
    if res.get("gpu_proc_gib") and C.POOL == "36G":
        freed = BF16_PROC_GIB - res["gpu_proc_gib"]
        res["pool_rule"] = {"bf16_proc_gib": BF16_PROC_GIB, "proc_gib": round(res["gpu_proc_gib"], 2),
                            "freed_gib": round(freed, 2), "pool": f"{int(36 + max(freed, 0))}G"}
        if a.write_pool and C.VARIANT != "bf16":
            try:
                allv = json.loads(C.VARIANTS_JSON.read_text())
            except (OSError, ValueError):
                allv = {}
            allv.setdefault(C.VARIANT, {})["pool"] = res["pool_rule"]["pool"]
            C.VARIANTS_JSON.write_text(json.dumps(allv, indent=1))
        C.log(f"pool rule: {res['pool_rule']}")
    C.log(f"TPOT at one user {res['tpot_ms_1user']} ms; kernels: " + " | ".join((res.get("kernel_lines") or [])[:60]))
    res["ok"] = ok
    (out / "check.json").write_text(json.dumps(res, indent=1, default=str))
    C.log(f"server check: {json.dumps(ok, default=str)}")
    C.log(f"written {out / 'check.json'}")
    return 0 if (ok["blocks_784"] and ok["align_mode"] and ok["ids"] and ok["boundary_ok"]
                 and ok["steps_have_details"] and ok["tool_call"]) else 1


# ---------------------------------------------------------------------------------------------------------------------
# S1a: known loads while the root sampler runs
GEMM = r"""
import json, sys, time, torch
n = 8192
a = torch.randn(n, n, dtype=torch.bfloat16, device="cuda"); b = torch.randn_like(a)
for _ in range(3):
    c = a @ b
torch.cuda.synchronize()
t0 = time.monotonic_ns(); k = 0
while time.monotonic_ns() - t0 < float(sys.argv[1]) * 1e9:
    for _ in range(10):
        c = a @ b
    torch.cuda.synchronize(); k += 10
t1 = time.monotonic_ns()
print(json.dumps({"t0": t0, "t1": t1, "iters": k, "flops": 2 * n ** 3 * k, "tflops": 2 * n ** 3 * k / ((t1 - t0) / 1e9) / 1e12}))
"""


def control(word: str) -> None:
    (C.HW / "control").write_text(word + "\n")


def control_ok() -> bool:
    """The sampler's folder must be ours to write the control file (sp_hw.py hands it to --owner)."""
    try:
        control("run")
        return True
    except OSError as e:
        C.log(f"refused: cannot write {C.HW / 'control'} ({e!r}); restart sp_hw.py (PLAN.md, Prerequisites 3)")
        return False


def heartbeat() -> dict:
    try:
        hb = json.loads((C.HW / "heartbeat.json").read_text())
        hb["age_s"] = (time.monotonic_ns() - hb.get("t", 0)) / 1e9
        return hb
    except (OSError, ValueError):
        return {}


def wait_hw(pred, timeout: float = 240) -> dict:
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        hb = heartbeat()
        if hb and hb.get("age_s", 1e9) < 15 and pred(hb):
            return hb
        time.sleep(2)
    raise TimeoutError(f"counter sampler did not reach the expected state; last heartbeat {heartbeat()}")


def gpu_cfg(op: str, rate: float) -> dict:
    return dict(cpu_op="none", gpu_op=op, gpu_alloc="cudamalloc", gpu_buf_gib=8, gpu_rate=rate)


def cpu_cfg(op: str, cores: str) -> dict:
    return dict(cpu_op=op, cpu_n=len(cores.split(",")), cpu_order=cores, cpu_buf_gib=4, cpu_rate=0)


LOADS = [  # name, umbench config, what it checks
    ("gpu_read_50", gpu_cfg("read", 50), "gpu_read"),
    ("gpu_read_150", gpu_cfg("read", 150), "gpu_read"),
    ("gpu_read_max", gpu_cfg("read", 0), "gpu_read"),
    ("gpu_write_max", gpu_cfg("write", 0), "gpu_write"),
    ("cpu_read_x925", cpu_cfg("read", "15,5,16,6"), "cpu_read"),
    ("cpu_read_a725", cpu_cfg("read", "1,2,3,4"), "cpu_read"),
    ("cpu_write_x925", cpu_cfg("write", "15,5,16,6"), "cpu_write"),
]


def cmd_counters(a) -> int:
    hb = heartbeat()
    if not hb or hb.get("age_s", 1e9) > 30 or hb.get("state") != "run" or hb.get("fake"):
        C.log(f"refused: the root counter sampler is not running (heartbeat {hb}); see PLAN.md, Prerequisites 3")
        return 2
    if gpu_apps():
        C.log(f"refused: GPU compute apps running: {gpu_apps()}")
        return 2
    if not control_ok():
        return 2
    out = CHECKS / f"counters_{time.strftime('%m%d_%H%M%S')}"
    rs = C.exp02(out / "raw", out / "logs", out / "check.log")
    loads = out / "loads.jsonl"
    wait_hw(lambda h: h.get("gpu") and h.get("perf"))
    time.sleep(20)                                  # let Nsight's sampling settle
    t0 = C.mono()
    time.sleep(a.secs * 3)
    C.append_jsonl(loads, {"name": "idle", "check": "idle", "t0": t0, "t1": C.mono(), "sampling": True})
    umb = rs.Umbench(name="umbench_counters")
    try:
        for name, cfg, check in LOADS:
            r = umb.run(tag=name, window=a.secs, warmup=1.0, gap=0.5, **cfg)
            C.append_jsonl(loads, {"name": name, "check": check, "t0": r["t0_ns"], "t1": r["t1_ns"], "sampling": True,
                                   "cpu_gbps": r.get("cpu_gbps"), "gpu_gbps": r.get("gpu_gbps"), "cfg": cfg})
            C.log(f"  {name}: cpu {r.get('cpu_gbps')} GB/s, gpu {r.get('gpu_gbps')} GB/s")
        p = subprocess.run(["taskset", "-c", "0", str(C.VLLM_PY), "-c", GEMM, str(a.secs)], capture_output=True,
                           text=True, timeout=600)
        g = json.loads(p.stdout.strip().splitlines()[-1])
        C.append_jsonl(loads, {"name": "gemm_bf16", "check": "tensor", "sampling": True, **g})
        C.log(f"  gemm: {g['tflops']:.1f} TFLOPS")
        # overhead: the unpaced GPU read stream with all sampling paused, then on again
        for state in ("pause", "run"):
            control(state)
            if state == "pause":
                wait_hw(lambda h: h.get("state") == "pause")
            else:
                wait_hw(lambda h: h.get("state") == "run" and h.get("gpu"))
                time.sleep(20)
            r = umb.run(tag=f"overhead_{state}", window=a.secs, warmup=1.0, gap=0.5, **gpu_cfg("read", 0))
            C.append_jsonl(loads, {"name": f"overhead_{state}", "check": "overhead", "t0": r["t0_ns"], "t1": r["t1_ns"],
                                   "sampling": state == "run", "gpu_gbps": r.get("gpu_gbps")})
            C.log(f"  overhead, sampling {state}: gpu {r.get('gpu_gbps')} GB/s")
    finally:
        umb.close()
    # close the current Nsight report so it can be read now, then sample again
    control("pause")
    wait_hw(lambda h: h.get("state") == "pause")
    control("run")
    return compare(out)


def compare(out: Path) -> int:
    import sp_hwread as H
    loads = C.read_jsonl(out / "loads.jsonl")
    t_lo, t_hi = min(x["t0"] for x in loads if "t0" in x), max(x["t1"] for x in loads if "t1" in x)
    sess = [s for s in H.sessions(C.HW)]
    prow, grow, names = [], [], []
    for s in sess:
        prow += [r for r in H.perf_rows(s) if t_lo - 60e9 <= r[0] <= t_hi + 60e9]
        grow += [r for r in H.gpu_rows(s) if t_lo - 60e9 <= r[0] <= t_hi + 60e9]
        names = names or H.metric_names(s)
    C.log(f"GPU metric names: {names}")

    def gmetric(w: dict, key: str) -> dict | None:
        for k, v in w.items():
            if key.lower() in k.lower():
                return v
        return None

    rows, ok_all = [], True
    for x in loads:
        if "t0" not in x:
            continue
        dt = (x["t1"] - x["t0"]) / 1e9
        gw = H.window_gpu(grow, x["t0"], x["t1"])
        res = {"name": x["name"], "check": x["check"], "secs": round(dt, 2), "gpu_samples":
               max((v["n"] for v in gw.values()), default=0)}
        if x["check"] == "gpu_read":
            m = gmetric(gw, "L2 Read Misses")
            got = m["sum"] * 32 / dt / 1e9 if m else None
            res.update(known=x.get("gpu_gbps"), counter=got, tol=0.05)
        elif x["check"] == "gpu_write":
            m = gmetric(gw, "L2 Write Sectors")
            got = m["sum"] * 32 / dt / 1e9 if m else None
            res.update(known=x.get("gpu_gbps"), counter=got, tol=0.10)
        elif x["check"] in ("cpu_read", "cpu_write"):
            cores = {int(c) for c in x["cfg"]["cpu_order"].split(",")}
            pw = H.window_perf(prow, x["t0"], x["t1"], cores)
            secs = pw.get("_seconds") or dt
            # every recorded event as 64-byte lines per second, against the known traffic: the event whose ratio is
            # closest to 1 on reads is the one to use (sp_analyze.py --cpu-read-event); ll_cache_miss_rd counts
            # demand misses only, so on a prefetched stream it is a lower bound
            known = x.get("cpu_gbps")
            ev = {k: v * 64 / secs / 1e9 for k, v in pw.items() if not k.startswith("_") and k not in (
                "cpu_cycles", "inst_retired", "stall_backend_mem")}
            if "bus_access" in ev:
                ev["bus_access_half"] = ev["bus_access"] / 2     # two bus transactions per 64-byte line
            ratios = {k: round(v / known, 3) for k, v in ev.items()} if known else {}
            best = min(ratios, key=lambda k: abs(ratios[k] - 1)) if ratios else None
            res.update(known=known, counter=ev.get(best), event=best, ratios=ratios,
                       mem_stall_share=(pw.get("stall_backend_mem", 0) / pw["cpu_cycles"]) if pw.get("cpu_cycles") else None,
                       tol=0.10)
        elif x["check"] == "tensor":
            m = gmetric(gw, "Tensor HMMA Insts")
            res.update(known_flops=x["flops"], counted_ops=m["sum"] if m else None,
                       scale=(x["flops"] / m["sum"]) if m and m["sum"] else None, tflops=x.get("tflops"),
                       tensor_active=(gmetric(gw, "Tensor Active") or {}).get("mean"))
        elif x["check"] == "idle":
            res.update({k: round(v["mean"], 3) for k, v in gw.items()})
            pw = H.window_perf(prow, x["t0"], x["t1"])
            res["cpu_read_gbps_all"] = pw.get("ll_cache_miss_rd", 0) * 64 / max(pw.get("_seconds", dt), 1) / 1e9
        elif x["check"] == "overhead":
            res.update(gpu_gbps=x.get("gpu_gbps"), sampling=x["sampling"])
        if res.get("counter") is not None and res.get("known"):
            ratio = res["counter"] / res["known"]
            res["ratio"] = round(ratio, 3)
            if res.get("tol") is not None:
                res["pass"] = abs(ratio - 1) <= res["tol"]
            elif res.get("band"):
                res["pass"] = res["band"][0] <= ratio <= res["band"][1]
            ok_all &= bool(res.get("pass"))
        rows.append(res)
    ov = {r["sampling"]: r["gpu_gbps"] for r in rows if r["check"] == "overhead"}
    if True in ov and False in ov and ov[False]:
        slow = 1 - ov[True] / ov[False]
        rows.append({"name": "overhead", "slowdown": round(slow, 4), "pass": slow < 0.01})
        ok_all &= slow < 0.01
    (out / "compare.json").write_text(json.dumps({"rows": rows, "gpu_metrics": names}, indent=1))
    for r in rows:
        C.log(f"  {json.dumps(r, default=lambda v: round(v, 3) if isinstance(v, float) else str(v))}")
    C.log(f"counter check {'passed' if ok_all else 'has failures'}: {out / 'compare.json'}")
    return 0 if ok_all else 1


# ---------------------------------------------------------------------------------------------------------------------
# S1b: counter overhead on vLLM (exp02's synthetic decode load at 2 users, sampling on / off / on)
def cmd_overhead(a) -> int:
    out = CHECKS / f"overhead_{time.strftime('%m%d_%H%M%S')}"
    raw, logs, reqlog = out / "raw", out / "logs", out / "reqlog"
    rs = C.exp02(raw, logs, out / "check.log")
    if gpu_apps():
        C.log(f"refused: GPU compute apps running: {gpu_apps()}")
        return 2
    hb = heartbeat()
    if not hb or hb.get("age_s", 1e9) > 30 or hb.get("fake") or not control_ok():
        C.log(f"refused: the root counter sampler is not running or not controllable (heartbeat {hb})")
        return 2
    rs.evict_model_pages()
    srv = rs.Server("overhead", extra=C.EXTRA, env_extra=C.server_env(reqlog), pool=C.POOL, prefix_caching=True)
    windows = []
    try:
        srv.wait_ready(2400)
        rs.evict_model_pages()
        lg = rs.LoadGen("D", 2, raw / "lg_overhead", seed=7)
        time.sleep(90)
        for state in ("run", "pause", "run"):
            control(state)
            wait_hw(lambda h, s=state: h.get("state") == s and (s == "pause" or h.get("gpu")))
            time.sleep(20 if state == "run" else 5)
            t0 = C.mono()
            time.sleep(a.secs)
            windows.append({"sampling": state == "run", "t0": t0, "t1": C.mono()})
            C.log(f"  window sampling={state}: {a.secs} s")
        lg.stop()
    finally:
        try:
            control("run")
        finally:
            srv.stop()
    steps = [json.loads(line) for p in sorted(reqlog.glob("steps_*.jsonl")) for line in open(p) if line.strip()]
    for w in windows:
        s = [x for x in steps if w["t0"] <= x["t"] < w["t1"]]
        gen = sum(x.get("ng", 0) for x in s)
        w["tok_s"] = gen / ((w["t1"] - w["t0"]) / 1e9)
        gaps = sorted((b["t"] - a_["t"]) / 1e6 for a_, b in zip(s, s[1:]))
        w["step_ms_p50"] = gaps[len(gaps) // 2] if gaps else None
    on = [w["tok_s"] for w in windows if w["sampling"]]
    off = [w["tok_s"] for w in windows if not w["sampling"]]
    slow = 1 - (sum(on) / len(on)) / off[0] if on and off and off[0] else None
    res = {"windows": windows, "slowdown": slow, "pass": slow is not None and slow < 0.02}
    (out / "overhead.json").write_text(json.dumps(res, indent=1))
    C.log(f"overhead on vLLM decode at 2 users: {json.dumps(res)}")
    return 0 if res["pass"] else 1


# ---------------------------------------------------------------------------------------------------------------------
# S1b: memory of 32 idle task containers (two per task), as the cells will start them
def cmd_containers(a) -> int:
    import sp_tasks as T
    out = CHECKS / f"containers_{time.strftime('%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    tasks = T.load()["main"]
    m0 = C.meminfo()
    ids = []
    try:
        for k in range(a.n):
            t = tasks[k % len(tasks)]
            p = subprocess.run(["docker", "run", "-d", "--rm", "--network", "none", "--cpuset-cpus", C.AGENT_CORES_RANGE,
                                "-m", "6g", "-e", f"PATH={T.TESTBED_PATH}", "--label", "sp24=containers-check",
                                t["image"], "sleep", "1800"], capture_output=True, text=True)
            if p.returncode:
                raise RuntimeError(p.stderr.strip()[-300:])
            ids.append(p.stdout.strip())
        time.sleep(30)
        m1 = C.meminfo()
        cg = 0
        for d in Path("/sys/fs/cgroup/system.slice").glob("docker-*.scope"):
            try:
                cg += int((d / "memory.current").read_text())
            except (OSError, ValueError):
                pass
        res = {"n": len(ids), "mem_available_drop_gib": round((m0["MemAvailable"] - m1["MemAvailable"]) / 2**30, 2),
               "cgroup_current_gib": round(cg / 2**30, 2), "per_container_mib": round(cg / max(1, len(ids)) / 2**20, 1)}
    finally:
        if ids:
            subprocess.run(["docker", "rm", "-f", *ids], capture_output=True)
    (out / "containers.json").write_text(json.dumps(res, indent=1))
    C.log(f"containers: {res}")
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# dry run without a GPU: Harbor -> SPAgent -> sp_child -> s15 -> tools in a task container, against a fake model that
# asks for one bash command and then answers
def fake_model(port: int, log_path: Path):
    from aiohttp import web

    async def messages(request):
        body = await request.json()
        hdr = dict(request.headers)
        msgs = body.get("messages", [])
        used = any(isinstance(m.get("content"), list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                              for b in m["content"]) for m in msgs)
        C.append_jsonl(log_path, {"rid": hdr.get("X-Request-Id"), "sid": hdr.get("X-Session-ID"), "n_msgs": len(msgs),
                                  "tools": [t.get("name") for t in body.get("tools") or []], "used_tool": used,
                                  "last": json.dumps(msgs[-1])[:1500] if msgs else None})
        if not used:
            content = [{"type": "text", "text": "Checking the environment."},
                       {"type": "tool_use", "id": "toolu_dry_1", "name": "bash",
                        "input": {"command": "pwd; uname -m; which python; python -c 'import sys; print(sys.version)'; "
                                             "nproc; cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null; "
                                             "cat /sys/fs/cgroup/memory.max 2>/dev/null; git status --short | head -3"}}]
            stop = "tool_use"
        else:
            content, stop = [{"type": "text", "text": "Done."}], "end_turn"
        return web.json_response({"id": "msg_dry", "type": "message", "role": "assistant", "model": C.MODEL,
                                  "content": content, "stop_reason": stop, "stop_sequence": None,
                                  "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 0,
                                            "cache_creation_input_tokens": 0}})

    async def ok(request):
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=64 * 2**20)
    app.router.add_post("/v1/messages", messages)
    app.router.add_get("/health", ok)
    app.router.add_get("/v1/models", ok)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


def cmd_dryrun(a) -> int:
    import multiprocessing
    import shutil
    import sp_cell as SC
    out = CHECKS / f"dryrun_{time.strftime('%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    port = 8099
    srv = multiprocessing.Process(target=fake_model, args=(port, out / "fake.jsonl"), daemon=True)
    srv.start()
    time.sleep(2)
    name = f"dryrun_{time.strftime('%H%M%S')}"
    ns = argparse.Namespace(n=1, name=name, max_lead_calls=3, passes=1, hard_limit_h=0.2, force=False)
    cell = SC.Cell(ns)
    cell.run_dir = out
    cmd = cell.harbor_cmd()
    cmd = [x.replace(C.URL, f"http://127.0.0.1:{port}") for x in cmd]
    i = cmd.index("-n")
    cmd[i + 1] = "1"
    cmd += ["--n-tasks", "1"]
    C.log("dry run: " + " ".join(cmd))
    try:
        p = subprocess.run(cmd, cwd=str(C.DIR), env=cell.harbor_env(), capture_output=True, text=True, timeout=900)
    finally:
        srv.terminate()
    (out / "harbor.txt").write_text(p.stdout + p.stderr)
    job = C.JOBS / name
    trials = SC.trial_dirs(job)
    res = {"harbor_rc": p.returncode, "trials": [t.name for t in trials]}
    if trials:
        s15 = trials[0] / "agent" / "s15"
        res["files"] = sorted(x.name for x in s15.iterdir()) if s15.exists() else []
        res["calls"] = C.read_jsonl(s15 / "calls.jsonl")
        res["result"] = json.loads((s15 / "result.json").read_text()) if (s15 / "result.json").exists() else None
        res["wb_t0"] = json.loads((s15 / "wb_t0.json").read_text()) if (s15 / "wb_t0.json").exists() else None
    res["fake_log"] = C.read_jsonl(out / "fake.jsonl")
    (out / "dryrun.json").write_text(json.dumps(res, indent=1, default=str))
    tool_out = next((r["last"] for r in res["fake_log"] if r["used_tool"]), None)
    C.log(f"dry run: harbor rc {p.returncode}, trials {res['trials']}, result "
          f"{(res.get('result') or {}).get('status')}, rids {[c.get('rid') for c in res.get('calls', [])]}")
    C.log(f"tool output seen by the model: {tool_out}")
    left = SC.remove_containers()
    C.log(f"containers left and removed: {left}; job folder {job}")
    if a.clean and job.exists():
        shutil.rmtree(job)
    ok = p.returncode == 0 and res.get("result") and all(c.get("rid") for c in res.get("calls", []))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("dryrun")
    p.add_argument("--clean", action="store_true")
    p = sub.add_parser("server")
    p.add_argument("--ready-timeout", type=float, default=2400)
    p.add_argument("--variant", choices=sorted(C.VARIANTS), default="bf16")
    p.add_argument("--pool", default=None, help="KV pool (default: the variant's; the pool rule needs 36G)")
    p.add_argument("--write-pool", action="store_true", help="store the pool from the pool rule in data/variants.json")
    p = sub.add_parser("counters")
    p.add_argument("--secs", type=float, default=20)
    p = sub.add_parser("compare")
    p.add_argument("dir")
    p = sub.add_parser("overhead")
    p.add_argument("--secs", type=float, default=120)
    p = sub.add_parser("containers")
    p.add_argument("--n", type=int, default=32)
    a = ap.parse_args()
    if a.cmd == "compare":
        return compare(Path(a.dir))
    return {"server": cmd_server, "counters": cmd_counters, "overhead": cmd_overhead,
            "containers": cmd_containers, "dryrun": cmd_dryrun}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
