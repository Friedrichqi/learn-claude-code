#!/usr/bin/env python3
"""Analysis of research/12_hw_utilization (offline; heavy runs as a CPU job, see hw_cpu.sbatch).

    python research/12_hw_utilization/hw_analyze.py smoke --server SRV --samp DIR --level LEVELDIR [--sched F]
    python research/12_hw_utilization/hw_analyze.py probe [--job JOB]      # Phase 0 table
    python research/12_hw_utilization/hw_analyze.py ab                      # sampler-overhead A/B
    python research/12_hw_utilization/hw_analyze.py tables [--cells p1a,p2a] # T1-T6 -> data/tables/
    python research/12_hw_utilization/hw_analyze.py phases [--cells p1a,s1]  # T7-T9 -> data/tables/

Time base: every stream is put on CLOCK_MONOTONIC (sampler `t`, trace `monotonic_ns`, scheduler `t`);
wall-clock stamps (markers, request captures) are mapped with the sampler's own wall-t offset.

Windows: a windowed level is measured from its start + warm-up to its hard stop; a Phase 1 level (N=1,
sessions to completion) from its start to its end.

Statistics: mean / median / p95 over the window's samples, `peak` = the largest 1-s mean, `max` = the
largest raw sample. Agent state of a moment: `model` (a model call in flight, no tool), `tool`, `both`,
`idle`, from the level's harness traces.
"""
from __future__ import annotations

import argparse
import bisect
import json
import linecache
import lzma
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

DIR = Path(__file__).resolve().parent
DATA = Path(os.environ.get("HW_DATA", DIR / "data"))      # tests point this at a temporary root
CELLS = DATA / "cells"
TABLES = DATA / "tables"
GIB = 2**30
# fallbacks when the probe did not measure a link (H200 SXM: PCIe Gen5 x16, NVLink 4 = 18 x 25 GB/s per direction)
NOMINAL = {"pcie_gb_s": 64.0, "nvlink_gb_s": 450.0}
CPU_GROUPS = ("api", "engine", "worker", "vother", "driver", "harness", "tools", "sampler")


# --------------------------------------------------------------------------------------- loaders
def read_jsonl(path: Path) -> list[dict]:
    """A JSONL file, plain or .xz (tolerates a last line cut by a kill)."""
    path = Path(path)
    if not path.exists() and path.with_name(path.name + ".xz").exists():
        path = path.with_name(path.name + ".xz")
    if not path.exists():
        return []
    opener = lzma.open if path.suffix == ".xz" else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def trace_events(run_dir: Path) -> list[dict]:
    """The s15 trace of one session run (run_*.jsonl[.xz], not the inputs sidecar)."""
    for path in sorted(Path(run_dir).glob("run_*.jsonl*")):
        if ".inputs." not in path.name:
            return read_jsonl(path)
    return []


def q(xs, p: float) -> float | None:
    xs = sorted(x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x)))
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stats(xs, ts=None) -> dict:
    """mean / median / p95 / max of samples; with their times `ts`, also `peak` = the largest 1-s mean."""
    pairs = [(t, x) for t, x in zip(ts or [None] * len(xs), xs)
             if x is not None and not (isinstance(x, float) and math.isnan(x))]      # GPM gives NaN on idle GPUs
    vals = [x for _, x in pairs]
    if not vals:
        return {"n": 0}
    out = {"n": len(vals), "mean": statistics.fmean(vals), "median": q(vals, 0.5), "p95": q(vals, 0.95),
           "max": max(vals)}
    if ts is not None:
        buckets: dict[int, list[float]] = defaultdict(list)
        for t, x in pairs:
            buckets[int(t)].append(x)
        out["peak"] = max(statistics.fmean(v) for v in buckets.values())
    return out


def cpulist(text: str | None) -> list[int]:
    out = []
    for part in (text or "").split(","):
        if part.strip():
            out.append(int(part))
    return out


VLLM_LOG = {
    "weights_gib": re.compile(r"Model loading took ([0-9.]+) GiB"),
    "graphs_gib": re.compile(r"Graph capturing finished in [0-9]+ secs, took ([0-9.]+) GiB"),
    "kv_gib": re.compile(r"Available KV cache memory: ([0-9.]+) GiB"),
    "kv_tokens": re.compile(r"GPU KV cache size: ([0-9,]+) tokens"),
    "torch_peak_gib": re.compile(r"torch peak memory increase: ([0-9.]+)GiB"),
}


def parse_vllm_log(path: Path) -> dict:
    """Static HBM facts from a vLLM server log (the last occurrence of each line wins: per TP rank the same)."""
    out: dict = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        for key, rx in VLLM_LOG.items():
            m = rx.search(line)
            if m:
                out[key] = float(m.group(1).replace(",", ""))
    return out


class Server:
    """One server lifetime of a cell: markers, sampler streams, scheduler log, static memory facts."""

    def __init__(self, srv: Path):
        self.dir = Path(srv)
        self.markers = read_jsonl(self.dir / "markers.jsonl")
        self.info = json.loads((self.dir / "server_info.json").read_text()) if (self.dir / "server_info.json").exists() else {}
        samp = self.dir / "samp"
        self.meta = json.loads((samp / "meta.json").read_text()) if (samp / "meta.json").exists() else {}
        self.fast = read_jsonl(samp / "fast.jsonl")
        self.host = read_jsonl(samp / "host.jsonl")
        self.slow = read_jsonl(samp / "slow.jsonl")
        self.load_sched(self.dir / "sched.jsonl")
        self.vlog = parse_vllm_log(self.dir / "vllm.log")
        offs = [r["wall"] - r["t"] for r in self.slow[:200] + self.host[:200] if "wall" in r and "t" in r]
        self.offset = statistics.median(offs) if offs else None        # wall = t + offset
        self.fast_t = [r["t"] for r in self.fast]
        self.host_t = [r["t"] for r in self.host]
        self.slow_t = [r["t"] for r in self.slow]

    def load_sched(self, path: Path) -> None:
        """Stream the scheduler log, keeping only what the analysis uses (an 8B log at N=32 holds millions of
        step records): step GPU-interval stamps, request arrival / finish, pool walks, HW records."""
        self.steps: list[tuple] = []          # (n, launch, u0)
        self.arr: dict[str, dict] = {}        # external request id -> {r, t, fe, wall, np}
        self.fin: dict[int, float] = {}       # request idx -> finish t
        self.pools: list[dict] = []
        self.hws: list[dict] = []
        path = Path(path)
        if not path.exists() and path.with_name(path.name + ".xz").exists():
            path = path.with_name(path.name + ".xz")
        if not path.exists():
            return
        opener = lzma.open if path.suffix == ".xz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if '"step"' not in line[:20] and '"arr"' not in line[:20] and '"fin"' not in line[:20] \
                        and '"pool"' not in line[:20] and '"hw"' not in line[:20]:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = r.get("k")
                if kind == "step":
                    if r.get("u0") is not None:
                        self.steps.append((r["n"], r.get("l") or r.get("s1"), r["u0"]))
                elif kind == "arr":
                    self.arr[r["id"]] = {"r": r["r"], "t": r["t"], "fe": r.get("fe"), "wall": r.get("wall"), "np": r.get("np")}
                elif kind == "fin":
                    self.fin[r["r"]] = r["t"]
                elif kind == "pool":
                    self.pools.append({"t": r["t"], "free": r["free"], "cfree": r["cfree"]})
                elif kind == "hw":
                    self.hws.append(r)
        self.steps.sort()

    def mono(self, wall: float) -> float:
        return wall - self.offset

    def levels(self) -> list[dict]:
        out, open_ = [], {}
        for m in self.markers:
            if m["ev"] == "level_start":
                open_[m["level"]] = m
            elif m["ev"] == "level_end" and m["level"] in open_:
                s = open_.pop(m["level"])
                win = s.get("window_s") or 0
                t0 = s["wall"] + (s.get("warmup_s") or 0) if win else s["wall"]
                t1 = min(s["hard"], m["wall"]) if win else m["wall"]
                out.append({"level": s["level"], "n": s.get("n"), "window_s": win, "w0": t0, "w1": t1,
                            "start": s["wall"], "end": m["wall"], "rc": m.get("rc"),
                            "agent_cpus": cpulist(s.get("agent_cpus")), "vllm_cpus": cpulist(s.get("vllm_cpus"))})
        return out

    def window(self, rows, ts, w0: float, w1: float) -> list[dict]:
        a, b = bisect.bisect_left(ts, self.mono(w0)), bisect.bisect_right(ts, self.mono(w1))
        return rows[a:b]


# ------------------------------------------------------------------------------------ agent state
def level_sessions(level_dir: Path) -> list[dict]:
    """Per session run of a level: model / tool intervals (monotonic s), status, calls."""
    out = []
    for run in sorted((Path(level_dir) / "runs").glob("*")):
        if not run.is_dir() or run.name.startswith("failed_a"):
            continue
        ev = trace_events(run)
        if not ev:
            continue
        starts: dict[str, tuple[float, dict]] = {}
        model, tool, prep = [], [], []
        for e in ev:
            k = e.get("event")
            t = e.get("monotonic_ns", 0) / 1e9
            if k in ("model_request", "tool_start"):
                starts[e.get("span_id")] = (t, e.get("data") or {})
            elif k in ("model_response", "model_error", "tool_end"):
                s = starts.pop(e.get("span_id"), None)
                if s is None:
                    continue
                d = s[1]
                if k == "tool_end":
                    tool.append((s[0], t, d.get("tool")))
                else:
                    usage = (e.get("data") or {}).get("usage") or {}
                    model.append((s[0], t, d.get("purpose"), usage.get("output_tokens"), usage.get("input_tokens")))
        metas = list(run.glob("*.meta.json"))
        meta = json.loads(metas[0].read_text()) if metas else {}
        t_all = [e["monotonic_ns"] / 1e9 for e in ev if "monotonic_ns" in e]
        out.append({"label": run.name, "status": meta.get("status"), "kind": (meta.get("templates") or ["?"])[0],
                    "model": model, "tool": tool, "t0": min(t_all), "t1": max(t_all), "dir": run})
    return out


class StateIndex:
    """How many model calls / tools are in flight at a time (all sessions of a level)."""

    def __init__(self, sessions: list[dict]):
        self.ms = sorted(a for s in sessions for a, *_ in s["model"])
        self.me = sorted(b for s in sessions for _, b, *_ in s["model"])
        self.ts = sorted(a for s in sessions for a, _, _ in s["tool"])
        self.te = sorted(b for s in sessions for _, b, _ in s["tool"])

    def counts(self, t: float) -> tuple[int, int]:
        return (bisect.bisect_right(self.ms, t) - bisect.bisect_right(self.me, t),
                bisect.bisect_right(self.ts, t) - bisect.bisect_right(self.te, t))

    def state(self, t: float) -> str:
        m, k = self.counts(t)
        return "both" if m and k else "model" if m else "tool" if k else "idle"


# ------------------------------------------------------------------------------------- per level
def cpu_rates(host: list[dict], stride: int = 2) -> list[dict]:
    """Cores per group over consecutive host records `stride` apart (stride 2 = 1-s means at 2 Hz)."""
    out = []
    for a, b in zip(host, host[stride:]):
        dt = b["t"] - a["t"]
        if dt <= 0:
            continue
        ca, cb = a["cpu"], b["cpu"]

        def d(key):
            return ((cb.get(key) or 0) - (ca.get(key) or 0)) / dt
        rec = {"t": b["t"], "vllm": d("vllm_total"), "agent": d("agent_total")}
        for g in ("api", "engine", "worker", "driver", "harness", "sampler"):
            rec[g] = d(g)
        rec["vother"] = rec["vllm"] - rec["api"] - rec["engine"] - rec["worker"]
        rec["tools"] = rec["agent"] - rec["driver"] - rec["harness"]
        rec["total"] = rec["vllm"] + rec["agent"] + rec["sampler"]
        cga, cgb = a.get("cg") or {}, b.get("cg") or {}
        if cga.get("cpu_us") is not None and cgb.get("cpu_us") is not None:
            rec["job_cg"] = (cgb["cpu_us"] - cga["cpu_us"]) / 1e6 / dt
        pa, pb = (cga.get("psi_cpu") or {}), (cgb.get("psi_cpu") or {})
        if "some" in pa and "some" in pb:
            rec["psi_some"] = (pb["some"] - pa["some"]) / 1e6 / dt
        out.append(rec)
    return out


def analyze_level(srv: Server, lv: dict, level_dir: Path, peaks: dict) -> dict:
    w0, w1 = lv["w0"], lv["w1"]
    dur = w1 - w0
    res: dict = {"level": lv["level"], "n": lv["n"], "window_s": round(dur, 1) if lv["window_s"] else 0,
                 "measured_s": round(dur, 1), "rc": lv["rc"],
                 "layout": {"agent": lv["agent_cpus"], "vllm": lv["vllm_cpus"]}}
    sessions = level_sessions(level_dir)
    idx = StateIndex(sessions)
    m0, m1 = srv.mono(w0), srv.mono(w1)
    res["sessions"] = {"runs": len(sessions), "in_window": sum(1 for s in sessions if s["t1"] > m0 and s["t0"] < m1),
                       "status": dict(_count(s["status"] for s in sessions))}
    # --- CPU
    host = srv.window(srv.host, srv.host_t, w0, w1)
    rates = cpu_rates(host)
    raw = cpu_rates(host, stride=1)
    watch = srv.meta.get("cpus") or []
    cpu: dict = {}
    for g in CPU_GROUPS + ("vllm", "agent", "total", "job_cg", "psi_some"):
        vals = [r.get(g) for r in rates]
        if any(v is not None for v in vals):
            cpu[g] = stats(vals, [r["t"] for r in rates])
            cpu[g]["max"] = max((r.get(g) or 0) for r in raw) if raw else None
    if watch and "total" in cpu and cpu["total"].get("n"):
        cpu["total_pct_of_cell"] = 100 * cpu["total"]["mean"] / len(watch)
    hot = defaultdict(list)
    delay = defaultdict(list)
    for r in host:
        for cls, threads in (r.get("hot") or {}).items():
            if threads:
                hot[cls].append(threads[0]["cpu"])
                delay[cls].append(threads[0]["delay"])
    cpu["hottest_thread"] = {cls: stats(v) for cls, v in hot.items()}
    cpu["hottest_thread_runq_delay"] = {cls: stats(v) for cls, v in delay.items()}
    ctx = defaultdict(lambda: [0, 0, 0.0])
    for r in host:
        for cls, c in (r.get("ctx") or {}).items():
            ctx[cls][0] += c["vcs"]
            ctx[cls][1] += c["ncs"]
            ctx[cls][2] += c["delay_ms"]
    cpu["ctx_per_s"] = {cls: {"vol": v[0] / dur, "invol": v[1] / dur, "runq_delay_cores": v[2] / 1000 / dur}
                        for cls, v in ctx.items()} if dur > 0 else {}
    res["cpu"] = cpu
    # --- tool CPU from the cell's own cores (cannot double count): the agent cores' busy time minus the
    # harness and driver (and, where vLLM shares those cores, minus vLLM). The tree count is kept as
    # `tools_tree`: before 2026-10-03 04:10 the sampler double-counted a child that exited together with
    # its parent (J0, J1 and J2's first cells), so the per-core figure is the primary one.
    fast = srv.window(srv.fast, srv.fast_t, w0, w1)
    watch = srv.meta.get("cpus") or []
    if fast and watch and lv["agent_cpus"]:
        pos = {c: i for i, c in enumerate(watch)}
        ag = [pos[c] for c in lv["agent_cpus"] if c in pos]
        shared = bool(set(lv["agent_cpus"]) & set(lv["vllm_cpus"]))
        bins: dict[int, list[float]] = defaultdict(list)
        for r in fast:
            if r.get("core"):
                v = [r["core"][i] for i in ag if r["core"][i] is not None]
                if v:
                    bins[int(r["t"])].append(sum(v))
        busy = {k: statistics.fmean(v) for k, v in bins.items()}
        pc = []
        for r in rates:
            b = busy.get(int(r["t"])) or busy.get(int(r["t"]) - 1)
            if b is None:
                continue
            other = r["harness"] + r["driver"] + (r["vllm"] if shared else 0.0)
            pc.append((r["t"], max(0.0, b - other)))
            r["tools_tree"], r["tools"] = r["tools"], pc[-1][1]
            r["total"] = r["vllm"] + r["harness"] + r["driver"] + r["sampler"] + r["tools"]
        if pc:
            cpu["tools_tree"] = cpu.get("tools")
            cpu["tools"] = stats([x for _, x in pc], [t for t, _ in pc])
            cpu["tools"]["source"] = "per-core busy of the agent cores minus harness and driver" + (" and vLLM" if shared else "")
            cpu["agent_cores_busy"] = stats(list(busy.values()), [float(k) for k in busy])
            total_new = [r["vllm"] + r["harness"] + r["driver"] + r["sampler"] + x for r, (_, x) in zip(rates, pc)]
            cpu["total_tree"] = cpu.get("total")
            cpu["total"] = stats(total_new, [t for t, _ in pc])
            if watch:
                cpu["total_pct_of_cell"] = 100 * cpu["total"]["mean"] / len(watch)
            res["_tools_pc_mean"] = statistics.fmean(x for _, x in pc)
    # cores by agent state (N=1 is where this is exact)
    by_state = defaultdict(list)
    for r in rates:
        by_state[idx.state(r["t"] - 0.5)].append(r)
    cpu["by_state"] = {st: {g: statistics.fmean(x[g] for x in rs) for g in ("total", "vllm", "engine", "api", "harness", "tools")}
                       | {"frac_time": len(rs) / max(1, len(rates))} for st, rs in by_state.items()}
    # --- per-core bursts (10 Hz)
    if fast and watch:
        pos = {c: i for i, c in enumerate(watch)}
        agent_i = [pos[c] for c in lv["agent_cpus"] if c in pos]
        vllm_i = [pos[c] for c in lv["vllm_cpus"] if c in pos]

        def mean_of(r, ids):
            vals = [r["core"][i] for i in ids if r.get("core") and r["core"][i] is not None]
            return statistics.fmean(vals) if vals else None
        ag = [mean_of(r, agent_i) for r in fast]
        vl = [max((r["core"][i] or 0) for i in vllm_i) if r.get("core") and vllm_i else None for r in fast]
        cellm = [mean_of(r, range(len(watch))) for r in fast]
        res["cores"] = {"agent_mean_busy": stats(ag, [r["t"] for r in fast]),
                        "agent_saturated_frac": _frac(ag, 0.9), "vllm_max_core_busy": stats(vl),
                        "vllm_core_saturated_frac": _frac(vl, 0.9), "cell_mean_busy": stats(cellm)}
    # --- GPU (10 Hz) per device
    gpus = []
    ndev = max((len(r.get("gpu") or []) for r in fast), default=0)
    for k in range(ndev):
        rows = [(r["t"], r["gpu"][k]) for r in fast if r.get("gpu") and len(r["gpu"]) > k]
        g: dict = {}
        for key in ("util", "mutil", "sm", "occ", "tensor", "hmma", "dram", "gr", "pcie_tx", "pcie_rx", "nvl_tx", "nvl_rx"):
            vals = [x.get(key) for _, x in rows]
            if any(v is not None for v in vals):
                g[key] = stats(vals, [t for t, _ in rows])
        st_rows = defaultdict(list)
        for t, x in rows:
            st_rows[idx.state(t)].append(x)
        g["by_state"] = {st: {key: _mean(x.get(key) for x in xs) for key in ("gr", "util", "sm", "tensor", "dram")}
                         | {"frac_time": len(xs) / max(1, len(rows))} for st, xs in st_rows.items()}
        gpus.append(g)
    res["gpu"] = gpus
    # --- slow: memory, power, links, vLLM metrics
    slow = srv.window(srv.slow, srv.slow_t, w0, w1)
    res["hbm"] = hbm_split(srv, slow, m0, m1)
    res["links"] = links(srv, slow, peaks, fast)
    res["serving"] = serving(slow)
    res["dram"] = dram(host, slow)
    res["power"] = [{"w": stats([r["gpu"][k].get("power_w") for r in slow if r.get("gpu") and len(r["gpu"]) > k])}
                    for k in range(ndev)]
    # --- critical path and host gaps per model call
    res["calls"] = call_breakdown(srv, sessions, m0, m1)
    res["agent_time"] = agent_time(sessions, m0, m1, lv["n"])
    res["cpu_cost"] = cpu_cost(host, res)
    return res


def cpu_cost(host: list[dict], res: dict) -> dict:
    """CPU-seconds per group in the window, and per unit of work: vLLM CPU per 1k output tokens, harness and
    tool CPU per lead call, tool CPU per second of tool wall time (1.0 = a tool keeps one core busy)."""
    if len(host) < 2:
        return {}
    a, b = host[0]["cpu"], host[-1]["cpu"]
    keys = ("vllm_total", "agent_total", "api", "engine", "worker", "driver", "harness", "sampler")
    secs = {k: (b.get(k) or 0) - (a.get(k) or 0) for k in keys}
    secs["tools_tree"] = secs["agent_total"] - secs["driver"] - secs["harness"]
    secs["vother"] = secs["vllm_total"] - secs["api"] - secs["engine"] - secs["worker"]
    at, sv = res.get("agent_time", {}), res.get("serving", {})
    dur = host[-1]["t"] - host[0]["t"]
    secs["tools"] = res["_tools_pc_mean"] * dur if "_tools_pc_mean" in res else secs["tools_tree"]
    out_tok = (sv.get("out_tok_s") or 0) * dur
    lead = at.get("lead_calls") or 0
    return {"seconds": secs,
            "vllm_cpu_s_per_1k_out_tok": 1000 * secs["vllm_total"] / out_tok if out_tok else None,
            "harness_cpu_s_per_lead_call": secs["harness"] / lead if lead else None,
            "tool_cpu_s_per_lead_call": secs["tools"] / lead if lead else None,
            "tool_cpu_per_tool_wall_s": secs["tools"] / at["tool_s"] if at.get("tool_s") else None}


def _count(it):
    out = defaultdict(int)
    for x in it:
        out[str(x)] += 1
    return out


def _frac(vals, thr):
    v = [x for x in vals if x is not None]
    return sum(1 for x in v if x >= thr) / len(v) if v else None


def _mean(it):
    v = [x for x in it if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.fmean(v) if v else None


def hbm_split(srv: Server, slow: list[dict], m0: float, m1: float) -> dict:
    """Static split (vLLM log) and the dynamic KV / activation split (HWScheduler records)."""
    out: dict = {"static": dict(srv.vlog)}
    used = [r["gpu"][0].get("used_mib") for r in slow if r.get("gpu")]
    total = next((r["gpu"][0].get("total_mib") for r in slow if r.get("gpu") and r["gpu"][0].get("total_mib")), None)
    if used and total:
        out["nvml_used_gib"] = stats([u / 1024 for u in used if u is not None])
        out["total_gib"] = total / 1024
        sv = srv.vlog
        known = sum(sv.get(k, 0) for k in ("weights_gib", "graphs_gib", "kv_gib"))
        out["static"]["other_reserve_gib"] = statistics.median(u / 1024 for u in used if u) - known
        out["static"]["unallocated_gib"] = total / 1024 - statistics.median(u / 1024 for u in used if u)
    kv_gib, nblk = srv.vlog.get("kv_gib"), srv.info.get("num_gpu_blocks")
    blk_gib = kv_gib / nblk if kv_gib and nblk else None
    pools = [r for r in srv.pools if m0 <= r["t"] <= m1]
    hws = [r for r in srv.hws if m0 <= r["t"] <= m1]
    if pools and nblk:
        usable = nblk - 1
        live = [(usable - p["free"]) / usable for p in pools]
        cfree = [p["cfree"] / usable for p in pools]
        empty = [(p["free"] - p["cfree"]) / usable for p in pools]
        out["kv_frac"] = {"live": stats(live), "valid_cached": stats(cfree), "empty": stats(empty)}
        if blk_gib:
            out["kv_gib"] = {k: {s: (v * usable * blk_gib if isinstance(v, float) else v) for s, v in d.items()}
                             for k, d in out["kv_frac"].items()}
    if hws:
        tok = [h["run_tok"] for h in hws]
        need = [h["run_need_blk"] for h in hws]
        livb = [h["live"] for h in hws]
        out["live_tokens"] = stats(tok)
        out["sharing_or_waste"] = stats([(lb - nb) / lb if lb else None for lb, nb in zip(livb, need)])
        cuda = [h["cuda"] for h in hws if h.get("cuda")]
        if cuda:
            base = min(c["alloc"] for c in cuda)
            out["activation_peak_gib"] = stats([(c["peak"] - base) / GIB for c in cuda])
            out["torch_reserved_gib"] = stats([c["resv"] / GIB for c in cuda])
    usage = [r["m"].get("vllm:kv_cache_usage_perc") for r in slow if r.get("m")]
    if usage:
        out["vllm_kv_usage"] = stats(usage)
    return out


def _counter_rate(slow, k, key, scale=1.0, wrap=None):
    """Rates from a cumulative NVML counter sampled at 1 Hz. The PCIe byte counters wrap at 2^32 (J1 probe):
    pass wrap=2**32 (valid while the link moves < 4.29 GB per sample, i.e. everywhere but weight loading)."""
    rows = [(r["t"], r["gpu"][k].get(key)) for r in slow if r.get("gpu") and len(r["gpu"]) > k
            and r["gpu"][k].get(key) is not None]
    rates, total = [], 0.0
    for (ta, a), (tb, b) in zip(rows, rows[1:]):
        if tb <= ta:
            continue
        d = b - a
        if d < 0:
            if not wrap:
                continue
            d %= wrap
        rates.append((d * scale / (tb - ta), tb))
        total += d * scale
    return rates, (total if rows else None)


def _gpm_rate(fast, k, key):
    """Rates from GPM's per-interval MiB/s (10 Hz): accurate at the sampler's 100-ms cadence (J0 start-up:
    59.3 GiB integrated vs 61 GiB of weights loaded)."""
    rows = [(r["t"], r["gpu"][k].get(key)) for r in fast if r.get("gpu") and len(r["gpu"]) > k]
    rates, total = [], 0.0
    for (ta, _), (tb, v) in zip(rows, rows[1:]):
        if v is None or v != v or tb <= ta:
            continue
        rates.append((v * 2**20, tb))
        total += v * 2**20 * (tb - ta)
    return rates, (total if rows else None)


def links(srv: Server, slow: list[dict], peaks: dict, fast: list[dict] | None = None) -> list[dict]:
    """PCIe from GPM (10 Hz; the NVML byte counter, wrap-corrected, as a cross-check: it counts raw link bytes,
    ~8 % above payload) and NVLink from the exact per-link NVML counters; GB/s and % of the measured peak."""
    out = []
    ndev = max((len(r.get("gpu") or []) for r in slow), default=0)
    gen = [r["m"].get("vllm:generation_tokens_total") for r in slow if r.get("m")]
    prm = [r["m"].get("vllm:prompt_tokens_total") for r in slow if r.get("m")]
    toks_out = (gen[-1] - gen[0]) if len(gen) > 1 else None
    toks_in = (prm[-1] - prm[0]) if len(prm) > 1 else None
    for k in range(ndev):
        rec: dict = {}
        for name, key, scale, peak in (("pcie_tx", "pcie_tx", None, peaks["pcie_gb_s"]),
                                       ("pcie_rx", "pcie_rx", None, peaks["pcie_gb_s"]),
                                       ("nvl_tx", "nvl_tx_kib", 1024.0, peaks["nvlink_gb_s"]),
                                       ("nvl_rx", "nvl_rx_kib", 1024.0, peaks["nvlink_gb_s"])):
            if scale is None:                                     # PCIe: GPM, cross-checked by the counter
                rates, total = _gpm_rate(fast or [], k, key)
                _, ctotal = _counter_rate(slow, k, key + "_b", 1.0, wrap=2**32)
            else:
                rates, total = _counter_rate(slow, k, key, scale)
                ctotal = None
            if not rates:
                continue
            gb = [r / 1e9 for r, _ in rates]
            s = stats(gb, [t for _, t in rates])
            s["pct_of_peak_mean"] = 100 * s["mean"] / peak
            s["pct_of_peak_peak"] = 100 * s["peak"] / peak
            s["bytes"] = total
            if ctotal is not None:
                s["counter_bytes"] = ctotal
            if toks_out:
                s["bytes_per_out_token"] = total / toks_out
            if toks_in:
                s["bytes_per_in_token"] = total / toks_in
            rec[name] = s
        out.append(rec)
    return out


def serving(slow: list[dict]) -> dict:
    ms = [(r["t"], r["m"]) for r in slow if r.get("m")]
    if len(ms) < 2:
        return {}
    dt = ms[-1][0] - ms[0][0]

    def delta(name):
        a, b = ms[0][1].get(name), ms[-1][1].get(name)
        return (b - a) if a is not None and b is not None else None
    out = {"running": stats([m.get("vllm:num_requests_running") for _, m in ms]),
           "waiting": stats([m.get("vllm:num_requests_waiting") for _, m in ms])}
    for name, key in (("out_tok_s", "vllm:generation_tokens_total"), ("in_tok_s", "vllm:prompt_tokens_total")):
        d = delta(key)
        out[name] = d / dt if d is not None and dt > 0 else None
    hits, qs = delta("vllm:prefix_cache_hits_total"), delta("vllm:prefix_cache_queries_total")
    out["prefix_hit_rate"] = hits / qs if hits is not None and qs else None
    out["preemptions"] = delta("vllm:num_preemptions_total")
    return out


def dram(host: list[dict], slow: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in host:
        for cls, m in (r.get("mem_kib") or {}).items():
            groups[cls].append((m.get("anon", 0) + m.get("shmem", 0)) / 2**20)
    out = {"rss_anon_shmem_gib": {cls: stats(v) for cls, v in groups.items()}}
    cg = [r["cg"] for r in host if r.get("cg")]
    if cg:
        out["job_cgroup_gib"] = stats([(c.get("mem_cur") or 0) / GIB for c in cg])
        out["job_cgroup_peak_gib"] = max((c.get("mem_peak") or 0) for c in cg) / GIB
        for key in ("anon", "file", "shmem"):
            vals = [(c.get("mem") or {}).get(key) for c in cg]
            if any(v is not None for v in vals):
                out[f"job_{key}_gib"] = stats([v / GIB for v in vals if v is not None])
    pss = defaultdict(list)
    for r in slow:
        for cls, m in (r.get("pss_kib") or {}).items():
            pss[cls].append(m.get("pss", 0) / 2**20)
    out["vllm_pss_gib"] = {cls: stats(v) for cls, v in pss.items()}
    return out


def gpu_busy_intervals(steps: list[tuple]) -> tuple[list[float], list[float]]:
    """11's per-step GPU interval [max(u0[n-1], launch[n]), u0[n]] (mu_analyze.py:152-157), merged.
    `steps` = sorted (n, launch, u0) tuples from Server.load_sched."""
    starts, ends = [], []
    prev = None
    for _n, launch, u0 in steps:
        a = max(prev, launch) if prev is not None else launch
        b = u0
        prev = b
        if b > a:
            if ends and a <= ends[-1]:
                ends[-1] = max(ends[-1], b)
            else:
                starts.append(a)
                ends.append(b)
    return starts, ends


def busy_in(starts, ends, a: float, b: float) -> float:
    i = max(0, bisect.bisect_right(ends, a))
    total = 0.0
    while i < len(starts) and starts[i] < b:
        total += max(0.0, min(ends[i], b) - max(starts[i], a))
        i += 1
    return total


def call_breakdown(srv: Server, sessions: list[dict], m0: float, m1: float) -> dict:
    """Each model call of the window split into front-end in (client send -> scheduler arrival: HTTP, chat
    template, tokenisation), engine time with a GPU step running, engine time without one (host gaps:
    scheduling, queueing, detokenisation bookkeeping), and front-end out (finish -> client receive)."""
    if not srv.steps or srv.offset is None:
        return {}
    arr, fin = srv.arr, srv.fin
    starts, ends = gpu_busy_intervals(srv.steps)
    parts = defaultdict(float)
    n = 0
    per_call = []
    for s in sessions:
        cap = next(iter(s["dir"].glob("*.requests.jsonl*")), None)
        if cap is None:
            continue
        for c in read_jsonl(cap):
            if c.get("t_send") is None or c.get("t_recv") is None:
                continue
            ts, tr = srv.mono(c["t_send"]), srv.mono(c["t_recv"])
            if ts < m0 or tr > m1:
                continue
            a = arr.get(c.get("request_id"))
            ft = fin.get(a["r"]) if a else None
            if not a or ft is None:
                continue
            fe_in, eng, fe_out = max(0.0, a["t"] - ts), max(0.0, ft - a["t"]), max(0.0, tr - ft)
            busy = busy_in(starts, ends, a["t"], ft)
            # vLLM stamps request.arrival_time (`fe`) when chat rendering starts (renderers/base.py render_chat):
            # send -> fe = SDK + HTTP + request parsing; fe -> engine add = chat template + tokenisation + IPC
            if a.get("fe") is not None and a.get("wall") is not None:
                http = min(fe_in, max(0.0, a["fe"] - c["t_send"]))
                parts["fe_http_parse"] += http
                parts["fe_render_tokenize"] += fe_in - http
            parts["frontend_in"] += fe_in
            parts["engine_gpu_busy"] += busy
            parts["engine_no_step"] += eng - busy
            parts["frontend_out"] += fe_out
            n += 1
            per_call.append({"dur": tr - ts, "fe_in": fe_in, "busy": busy, "gap": eng - busy, "fe_out": fe_out,
                             "np": a.get("np"), "out": (c.get("response") or {}).get("usage", {}).get("output_tokens")})
    split = {k: parts.pop(k) for k in ("fe_http_parse", "fe_render_tokenize") if k in parts}
    total = sum(parts.values())
    # front-end time vs prompt length: chat template + tokenisation of the whole conversation on every call
    buckets = {}
    for lo, hi in ((0, 4000), (4000, 8000), (8000, 16000), (16000, 24000), (24000, 32000), (32000, 10**9)):
        sel = [p for p in per_call if p["np"] is not None and lo <= p["np"] < hi]
        if sel:
            buckets[f"{lo // 1000}-{hi // 1000 if hi < 10**9 else 'inf'}k"] = {
                "calls": len(sel), "fe_in_ms_median": 1000 * q([p["fe_in"] for p in sel], 0.5),
                "fe_in_ms_mean": 1000 * statistics.fmean(p["fe_in"] for p in sel),
                "engine_s_median": q([p["busy"] + p["gap"] for p in sel], 0.5)}
    return {"calls": n, "seconds": dict(parts), "share": {k: v / total for k, v in parts.items()} if total else {},
            "median_call_s": q([p["dur"] for p in per_call], 0.5),
            "median_gap_share": q([p["gap"] / p["dur"] for p in per_call if p["dur"] > 0], 0.5),
            "fe_in_ms": stats([1000 * p["fe_in"] for p in per_call]), "fe_in_by_prompt": buckets,
            "frontend_in_split_s": split}


def agent_time(sessions: list[dict], m0: float, m1: float, n: int) -> dict:
    """Where the sessions' wall time goes inside the window: model calls, tools, harness (the rest)."""
    model = tool = wall = 0.0
    calls = lead_calls = out_tok = 0
    tools_by = defaultdict(lambda: [0, 0.0])
    for s in sessions:
        a, b = max(m0, s["t0"]), min(m1, s["t1"])
        if b <= a:
            continue
        wall += b - a
        for t0, t1, purpose, out, _inp in s["model"]:
            ov = max(0.0, min(b, t1) - max(a, t0))
            model += ov
            if ov > 0 and t1 <= m1:
                calls += 1
                lead_calls += purpose == "lead"
                out_tok += out or 0
        for t0, t1, name in s["tool"]:
            ov = max(0.0, min(b, t1) - max(a, t0))
            tool += ov
            if ov > 0:
                tools_by[name][0] += 1
                tools_by[name][1] += t1 - t0
    hours = (m1 - m0) / 3600
    return {"session_wall_s": wall, "model_s": model, "tool_s": tool, "harness_s": wall - model - tool,
            "model_share": model / wall if wall else None, "tool_share": tool / wall if wall else None,
            "harness_share": (wall - model - tool) / wall if wall else None,
            "lead_calls_per_h": lead_calls / hours if hours > 0 else None, "calls": calls, "lead_calls": lead_calls,
            "out_tokens_per_s_harness": out_tok / (m1 - m0) if m1 > m0 else None,
            "tools": {k: {"calls": v[0], "seconds": v[1], "mean_ms": 1000 * v[1] / v[0]} for k, v in tools_by.items()}}


# ------------------------------------------------------------------------------------------- probe
def latest_probe(job: str | None = None) -> dict | None:
    paths = sorted((DATA / "probe").glob("*.json"))
    if job:
        paths = [p for p in paths if p.stem == job]
    return json.loads(paths[-1].read_text()) if paths else None


def link_peaks(probe: dict | None) -> dict:
    out = dict(NOMINAL)
    out["source"] = "nominal"
    if probe and isinstance(probe.get("gpu"), dict):
        g = probe["gpu"]
        pcie = [g.get(k, {}).get("gb_s") for k in ("pcie_h2d", "pcie_d2h") if isinstance(g.get(k), dict)]
        pcie = [x for x in pcie if x]
        if pcie:
            out["pcie_gb_s"] = max(pcie)
            out["source"] = "probe"
        nc = g.get("nccl_allreduce") or {}
        p2p = (g.get("nvlink_p2p") or {}).get("gb_s")
        best = max([x for x in (nc.get("peak_busbw_gb_s"), p2p) if x] or [0])
        if best:
            out["nvlink_gb_s"] = best
    return out


def probe_table(args) -> int:
    p = latest_probe(args.job)
    if not p:
        print("no probe result")
        return 1
    print(json.dumps({k: p.get(k) for k in ("started", "seconds")}, indent=1))
    from hw_probe import summary
    for line in summary(p):
        print(line)
    print("link peaks used by the tables:", link_peaks(p))
    return 0


# --------------------------------------------------------------------------------------- A/B test
def ab(args) -> int:
    """Sampler-overhead A/B: the same 10 min of 11's pilot8 replay, sampler off / on / off."""
    root = CELLS / "ab"
    arms = {}
    for arm in ("off1", "on", "off2"):
        rows = [r for r in read_jsonl(root / arm / "replay_calls.jsonl") if r.get("status") == "ok"]
        if not rows:
            continue
        t0 = min(r["t_send"] for r in rows)
        t1 = max(r["t_recv"] for r in rows)
        arms[arm] = {"calls": len(rows), "out_tok_s": sum(r["n_out"] or 0 for r in rows) / (t1 - t0),
                     "lat": {(r["label"], r["call"]): r["t_recv"] - r["t_send"] for r in rows}}
    if len(arms) < 2:
        print("A/B incomplete:", list(arms))
        return 1
    out = {}
    for arm, a in arms.items():
        out[arm] = {"calls": a["calls"], "out_tok_s": a["out_tok_s"], "median_latency_s": q(list(a["lat"].values()), 0.5)}
    if "on" in arms:
        for off in ("off1", "off2"):
            if off in arms:
                common = set(arms["on"]["lat"]) & set(arms[off]["lat"])
                ratios = [arms["on"]["lat"][k] / arms[off]["lat"][k] for k in common if arms[off]["lat"][k] > 0]
                out[f"on_vs_{off}"] = {"paired_calls": len(common), "median_latency_ratio": q(ratios, 0.5),
                                       "tok_s_ratio": arms["on"]["out_tok_s"] / arms[off]["out_tok_s"]}
    if "off1" in arms and "off2" in arms:
        common = set(arms["off1"]["lat"]) & set(arms["off2"]["lat"])
        ratios = [arms["off2"]["lat"][k] / arms["off1"]["lat"][k] for k in common if arms["off1"]["lat"][k] > 0]
        out["noise_off2_vs_off1"] = {"paired_calls": len(common), "median_latency_ratio": q(ratios, 0.5),
                                     "tok_s_ratio": arms["off2"]["out_tok_s"] / arms["off1"]["out_tok_s"]}
    samp = next((CELLS / "ab" / "server").glob("*/samp/meta.json"), None)
    if samp:
        out["sampler_cores"] = json.loads(samp.read_text()).get("sampler_cores")
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "ab.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


# ----------------------------------------------------------------------------------------- tables
def analyze_cells(names: list[str]) -> dict:
    peaks = link_peaks(latest_probe())
    out = {"peaks": peaks, "cells": {}}
    for name in names:
        cell = CELLS / name
        levels = {}
        for srv_dir in sorted((cell / "server").glob("*")):
            srv = Server(srv_dir)
            if srv.offset is None:
                continue
            for lv in srv.levels():
                level_dir = cell / "levels" / lv["level"]
                done = (level_dir / "WINDOW_DONE").exists() or (level_dir / "COMPLETE").exists()
                if not done and lv["level"] in levels:
                    continue
                res = analyze_level(srv, lv, level_dir, peaks)
                res["server"] = srv_dir.name
                res["done"] = done
                res["gpu_name"] = srv.info.get("gpu_name")
                levels[lv["level"]] = res
        out["cells"][name] = levels
    return out


def fmt(x, nd=2):
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def mmp(s: dict | None, nd=2, scale=1.0) -> str:
    """'mean / median / p95 / peak' of a stats dict."""
    if not s or not s.get("n"):
        return "–"
    vals = [s.get("mean"), s.get("median"), s.get("p95"), s.get("peak", s.get("max"))]
    return " / ".join(fmt(v * scale if v is not None else None, nd) for v in vals)


def write_tables(res: dict) -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "summary.json").write_text(json.dumps(res, indent=1, default=str))
    lines = ["# T1 · Utilization per cell and level (mean / median / p95 / peak of 1-s means)", "",
             f"Link peaks: PCIe {res['peaks']['pcie_gb_s']:.1f} GB/s, NVLink {res['peaks']['nvlink_gb_s']:.1f} GB/s "
             f"({res['peaks']['source']}).", "",
             "| cell / level | N | measured s | CPU cores total | vLLM cores | EngineCore cores | hottest engine thread | "
             "harness cores | tool cores | GPU busy % (GPM) | SM % | tensor % | HBM BW % | KV live % | KV valid-idle % | "
             "PCIe GB/s (tx+rx) | NVLink GB/s (tx+rx) |",
             "|" + "---|" * 17]
    for cname, levels in res["cells"].items():
        for lname, r in levels.items():
            c, g = r.get("cpu", {}), (r.get("gpu") or [{}])
            g0 = g[0] if g else {}
            hb = r.get("hbm", {})
            kv = hb.get("kv_frac", {})
            lk = (r.get("links") or [{}])
            pcie = sum((lk[k].get("pcie_tx", {}).get("mean") or 0) + (lk[k].get("pcie_rx", {}).get("mean") or 0)
                       for k in range(len(lk)))
            nvl = sum((lk[k].get("nvl_tx", {}).get("mean") or 0) + (lk[k].get("nvl_rx", {}).get("mean") or 0)
                      for k in range(len(lk)))
            lines.append(f"| {cname} / {lname} | {r['n']} | {fmt(r.get('measured_s'), 0)} | {mmp(c.get('total'))} | "
                         f"{mmp(c.get('vllm'))} | {mmp(c.get('engine'))} | {mmp(c.get('hottest_thread', {}).get('engine'))} | "
                         f"{mmp(c.get('harness'))} | {mmp(c.get('tools'))} | {mmp(g0.get('gr') or g0.get('util'), 0)} | {mmp(g0.get('sm'), 0)} | "
                         f"{mmp(g0.get('tensor'), 0)} | {mmp(g0.get('dram'), 0)} | {mmp(kv.get('live'), 0, 100)} | "
                         f"{mmp(kv.get('valid_cached'), 0, 100)} | {fmt(pcie, 3)} | {fmt(nvl, 3)} |")
    (TABLES / "T1_utilization.md").write_text("\n".join(lines) + "\n")
    # T3: where model time goes
    lines = ["# T3 · Agent time and model-call breakdown", "",
             "| cell / level | N | model share | tool share | harness share | lead calls/h | calls | "
             "front-end in | engine GPU busy | engine no step | front-end out |", "|" + "---|" * 11]
    for cname, levels in res["cells"].items():
        for lname, r in levels.items():
            a, cb = r.get("agent_time", {}), r.get("calls", {}).get("share", {})
            lines.append(f"| {cname} / {lname} | {r['n']} | {fmt(a.get('model_share'), 3)} | {fmt(a.get('tool_share'), 3)} | "
                         f"{fmt(a.get('harness_share'), 3)} | {fmt(a.get('lead_calls_per_h'), 0)} | {r.get('calls', {}).get('calls', '–')} | "
                         f"{fmt(cb.get('frontend_in'), 3)} | {fmt(cb.get('engine_gpu_busy'), 3)} | {fmt(cb.get('engine_no_step'), 3)} | "
                         f"{fmt(cb.get('frontend_out'), 3)} |")
    (TABLES / "T3_critical_path.md").write_text("\n".join(lines) + "\n")


def _g(r, k=0):
    g = r.get("gpu") or []
    return g[k] if len(g) > k else {}


def _rows(res, cells=None, levels=None):
    for cname, lv in res["cells"].items():
        if cells and cname not in cells:
            continue
        for lname, r in lv.items():
            if levels and lname not in levels:
                continue
            yield cname, lname, r


def write_more_tables(res: dict) -> None:
    """T2 HBM split, T4 load curve, T5 knock-downs, T6 links."""
    L = ["# T2 · HBM: reserved by vLLM vs genuinely used (GiB; KV columns mean / peak)", "",
         "| cell / level | N | GPU total | NVML used | weights | CUDA graphs | KV pool | other reserve | unallocated | "
         "KV live | KV valid-idle | KV empty | activation peak used | vLLM kv usage % |", "|" + "---|" * 14]
    for c, l, r in _rows(res):
        h = r.get("hbm", {})
        st = h.get("static", {})
        kv = h.get("kv_gib", {})

        def mp(d):
            return f"{fmt(d.get('mean'), 1)} / {fmt(d.get('max'), 1)}" if d and d.get("n") else "–"
        L.append(f"| {c} / {l} | {r['n']} | {fmt(h.get('total_gib'), 1)} | {fmt((h.get('nvml_used_gib') or {}).get('median'), 1)} | "
                 f"{fmt(st.get('weights_gib'), 2)} | {fmt(st.get('graphs_gib'), 2)} | {fmt(st.get('kv_gib'), 2)} | "
                 f"{fmt(st.get('other_reserve_gib'), 2)} | {fmt(st.get('unallocated_gib'), 1)} | {mp(kv.get('live'))} | "
                 f"{mp(kv.get('valid_cached'))} | {mp(kv.get('empty'))} | {fmt((h.get('activation_peak_gib') or {}).get('max'), 2)} | "
                 f"{mmp(h.get('vllm_kv_usage'), 0, 100)} |")
    (TABLES / "T2_hbm.md").write_text("\n".join(L) + "\n")
    L = ["# T4 · Load curve: throughput and the resource closest to saturation, per windowed level", "",
         "| cell / level | N | lead calls/h | output tok/s | running (mean) | waiting (mean) | KV live % | GPU SM % | "
         "HBM BW % | CPU cores (mean / peak) | CPU % of cell | tool cores (mean / peak) | agent cores ≥90 % busy (share of 100 ms) | "
         "engine thread busy (mean / p95) | CPU PSI some (mean) |", "|" + "---|" * 15]
    for c, l, r in _rows(res):
        if not r.get("window_s"):
            continue
        cpu, g0, sv = r.get("cpu", {}), _g(r), r.get("serving", {})
        tot, tl = cpu.get("total") or {}, cpu.get("tools") or {}
        eng = (cpu.get("hottest_thread") or {}).get("engine") or {}
        L.append(f"| {c} / {l} | {r['n']} | {fmt(r.get('agent_time', {}).get('lead_calls_per_h'), 0)} | {fmt(sv.get('out_tok_s'), 0)} | "
                 f"{fmt((sv.get('running') or {}).get('mean'), 1)} | {fmt((sv.get('waiting') or {}).get('mean'), 1)} | "
                 f"{fmt(((r.get('hbm') or {}).get('kv_frac') or {}).get('live', {}).get('mean', None) and 100 * r['hbm']['kv_frac']['live']['mean'], 0)} | "
                 f"{fmt((g0.get('sm') or {}).get('mean'), 0)} | {fmt((g0.get('dram') or {}).get('mean'), 0)} | "
                 f"{fmt(tot.get('mean'))} / {fmt(tot.get('peak'))} | {fmt(cpu.get('total_pct_of_cell'), 0)} | "
                 f"{fmt(tl.get('mean'))} / {fmt(tl.get('peak'))} | {fmt((r.get('cores') or {}).get('agent_saturated_frac'), 3)} | "
                 f"{fmt(eng.get('mean'))} / {fmt(eng.get('p95'))} | {fmt((cpu.get('psi_some') or {}).get('mean'), 3)} |")
    (TABLES / "T4_load_curve.md").write_text("\n".join(L) + "\n")
    L = ["# T5 · Knock-downs: does taking cores away change throughput or latency?", "",
         "| cell / level | N | agent cores | vLLM cores | lead calls/h | output tok/s | median model call s | "
         "engine no-step share | tool mean ms (bash) | agent cores ≥90 % busy | engine thread run-queue delay (mean) | PSI some |",
         "|" + "---|" * 12]
    for c, l, r in _rows(res, cells={"k1h", "k1l", "k2", "k2t", "k3", "t2", "p2b"}):
        cpu = r.get("cpu", {})
        lay = r.get("layout", {})
        bash = (r.get("agent_time", {}).get("tools") or {}).get("bash", {})
        dly = (cpu.get("hottest_thread_runq_delay") or {}).get("engine") or {}
        L.append(f"| {c} / {l} | {r['n']} | {len(lay.get('agent', [])) or '–'} | {len(lay.get('vllm', [])) or '–'} | "
                 f"{fmt(r.get('agent_time', {}).get('lead_calls_per_h'), 0)} | {fmt(r.get('serving', {}).get('out_tok_s'), 0)} | "
                 f"{fmt(r.get('calls', {}).get('median_call_s'), 2)} | {fmt(r.get('calls', {}).get('share', {}).get('engine_no_step'), 3)} | "
                 f"{fmt(bash.get('mean_ms'), 0)} | {fmt((r.get('cores') or {}).get('agent_saturated_frac'), 3)} | "
                 f"{fmt(dly.get('mean'), 3)} | {fmt((cpu.get('psi_some') or {}).get('mean'), 3)} |")
    (TABLES / "T5_knockdowns.md").write_text("\n".join(L) + "\n")
    L = ["# T6 · PCIe and NVLink traffic per GPU (GB/s mean / peak, % of the measured peak, bytes per output token)", "",
         f"Peaks: PCIe {res['peaks']['pcie_gb_s']:.1f} GB/s, NVLink {res['peaks']['nvlink_gb_s']:.1f} GB/s ({res['peaks']['source']}).", "",
         "| cell / level | N | GPU | PCIe TX | PCIe RX | PCIe % of peak (mean / peak) | PCIe B/out-token | NVLink TX | NVLink RX | "
         "NVLink % of peak (mean / peak) | NVLink B/out-token |", "|" + "---|" * 11]
    for c, l, r in _rows(res):
        for k, lk in enumerate(r.get("links") or []):
            pt, pr, nt, nr = (lk.get(x) or {} for x in ("pcie_tx", "pcie_rx", "nvl_tx", "nvl_rx"))
            pp = max(pt.get("pct_of_peak_mean") or 0, pr.get("pct_of_peak_mean") or 0)
            ppk = max(pt.get("pct_of_peak_peak") or 0, pr.get("pct_of_peak_peak") or 0)
            npm = max(nt.get("pct_of_peak_mean") or 0, nr.get("pct_of_peak_mean") or 0)
            npk = max(nt.get("pct_of_peak_peak") or 0, nr.get("pct_of_peak_peak") or 0)
            bpt = ((pt.get("bytes_per_out_token") or 0) + (pr.get("bytes_per_out_token") or 0)) or None
            bnt = ((nt.get("bytes_per_out_token") or 0) + (nr.get("bytes_per_out_token") or 0)) or None
            L.append(f"| {c} / {l} | {r['n']} | {k} | {fmt(pt.get('mean'), 4)} / {fmt(pt.get('peak'), 3)} | "
                     f"{fmt(pr.get('mean'), 4)} / {fmt(pr.get('peak'), 3)} | {fmt(pp, 3)} / {fmt(ppk, 2)} | {fmt(bpt, 0)} | "
                     f"{fmt(nt.get('mean'), 4)} / {fmt(nt.get('peak'), 3)} | {fmt(nr.get('mean'), 4)} / {fmt(nr.get('peak'), 3)} | "
                     f"{fmt(npm, 3)} / {fmt(npk, 2)} | {fmt(bnt, 0)} |")
    (TABLES / "T6_links.md").write_text("\n".join(L) + "\n")


def tables(args) -> int:
    names = args.cells.split(",") if args.cells else sorted(p.name for p in CELLS.iterdir() if p.is_dir() and p.name != "ab")
    res = analyze_cells(names)
    write_tables(res)
    write_more_tables(res)
    print(f"wrote {TABLES}/summary.json and T1-T6 for {names}")
    return 0


# ------------------------------------------------------- GPU phases and the EngineCore thread (T7-T9)
LONG_STEP_S = 1.0       # launch -> result of a Qwen3-32B 8,192-token prefill chunk is ~1.2 s
SHORT_STEP_S = 0.1
GPM_KEYS = ("sm", "occ", "tensor", "dram")


def step_detail(srv: Server) -> tuple[list[tuple], list[dict], dict]:
    """The scheduler log's step records as (launch, u0, schedule s, update s, prefill tokens, decode seqs),
    sorted by launch, plus the engine-stack samples (`stk`) and stack table (`stkdef`, id -> frames)."""
    path = srv.dir / "sched.jsonl"
    if not path.exists() and path.with_name(path.name + ".xz").exists():
        path = path.with_name(path.name + ".xz")
    steps, stk, defs = [], [], {}
    if not path.exists():
        return steps, stk, defs
    opener = lzma.open if path.suffix == ".xz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            head = line[:16]
            if '"step"' not in head and '"stk' not in head:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = r.get("k")
            if k == "step" and r.get("u0") is not None and r.get("l") is not None:
                steps.append((r["l"], r["u0"], r["s1"] - r["s0"], r["u1"] - r["u0"], sum(r["pf"]), r["dc"]))
            elif k == "stk":
                stk.append(r)
            elif k == "stkdef":
                defs[r["id"]] = r["st"]
    steps.sort()
    return steps, stk, defs


def _union(intervals: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    starts, ends = [], []
    for a, b in sorted(intervals):
        if ends and a <= ends[-1]:
            ends[-1] = max(ends[-1], b)
        else:
            starts.append(a)
            ends.append(b)
    return starts, ends


def _inside(starts, ends, a: float, b: float) -> bool:
    i = bisect.bisect_right(starts, a) - 1
    return i >= 0 and ends[i] >= b


def gpu_phases(srv: Server, steps: list[tuple], m0: float, m1: float) -> dict:
    """GPM samples split by what the GPU ran in their 100 ms: `decode` = only decode steps (>= 90 % of the
    window, no prefill token), `prefill` = steps with prefill tokens for >= 50 % of it. A step runs on the
    GPU over [max(previous step's result, its launch), its result] (gpu_busy_intervals): with async
    scheduling a step is launched while the one before it still runs."""
    spans = []
    prev = None
    for l, u0, _s, _u, pf, dc in steps:
        a = max(prev, l) if prev is not None else l
        prev = max(prev, u0) if prev is not None else u0
        if u0 > a:
            spans.append((a, u0, pf > 0, dc))
    spans.sort()
    starts = [s[0] for s in spans]
    acc = {c: {"n": 0, "batch": 0.0, **{k: 0.0 for k in GPM_KEYS}} for c in ("decode", "prefill")}
    total = 0
    fast = srv.window(srv.fast, srv.fast_t, srv.offset + m0, srv.offset + m1)
    for prev, cur in zip(fast, fast[1:]):
        lo, hi = prev["t"], cur["t"]
        g = (cur.get("gpu") or [{}])[0]
        if any(g.get(k) is None or (isinstance(g[k], float) and math.isnan(g[k])) for k in GPM_KEYS):
            continue
        total += 1
        cov_pf = cov_dc = bsum = 0.0
        j = max(0, bisect.bisect_left(starts, lo) - 1)
        while j < len(spans) and spans[j][0] < hi:
            a, b, has_pf, dc = spans[j]
            ov = max(0.0, min(b, hi) - max(a, lo))
            if has_pf:
                cov_pf += ov
            else:
                cov_dc += ov
                bsum += ov * dc
            j += 1
        if cov_pf == 0 and cov_dc >= 0.9 * (hi - lo):
            cls = "decode"
        elif cov_pf >= 0.5 * (hi - lo):
            cls = "prefill"
        else:
            continue
        a = acc[cls]
        a["n"] += 1
        for k in GPM_KEYS:
            a[k] += g[k]
        if cls == "decode":
            a["batch"] += bsum / cov_dc
    out = {"samples": total}
    for cls, a in acc.items():
        n = a["n"]
        out[cls] = {"share": n / total if total else None, "n": n,
                    **{k: (a[k] / n if n else None) for k in GPM_KEYS}}
        if cls == "decode":
            out[cls]["batch"] = a["batch"] / n if n else None
    return out


def engine_thread(srv: Server, steps: list[tuple], m0: float, m1: float) -> dict:
    """The EngineCore thread's CPU (hottest thread of the engine process, 2 Hz) against its scheduler work,
    and inside long GPU steps vs. stretches of short steps only."""
    host = srv.window(srv.host, srv.host_t, srv.offset + m0, srv.offset + m1)
    busy = [((r.get("hot") or {}).get("engine") or [{}])[0].get("cpu") for r in host]
    busy_t = [(r["t"], b) for r, b in zip(host, busy) if b is not None]
    lv_steps = [s for s in steps if m0 <= s[0] < m1]
    dur = m1 - m0
    res = {"busy": statistics.fmean(b for _, b in busy_t) if busy_t else None,
           "steps_per_s": len(lv_steps) / dur if dur > 0 else None,
           "schedule_ms": 1e3 * statistics.fmean(s[2] for s in lv_steps) if lv_steps else None,
           "update_ms": 1e3 * statistics.fmean(s[3] for s in lv_steps) if lv_steps else None}
    if res["busy"] is not None and res["steps_per_s"]:
        res["cpu_ms_per_step"] = 1e3 * res["busy"] / res["steps_per_s"]
        res["sched_share"] = (res["schedule_ms"] + res["update_ms"]) / res["cpu_ms_per_step"]
    ls, le = _union([(s[0], s[1]) for s in steps if s[1] - s[0] > LONG_STEP_S])
    launches = [s[0] for s in steps]
    inside, short = [], []
    for (t0, _), (t1, b) in zip(busy_t, busy_t[1:]):
        if _inside(ls, le, t0, t1):
            inside.append(b)
            continue
        i, j = bisect.bisect_left(launches, t0), bisect.bisect_right(launches, t1)
        if j - i >= 5 and all(steps[k][1] - steps[k][0] < SHORT_STEP_S for k in range(i, j)):
            short.append(b)
    res["long_steps"] = sum(1 for s in lv_steps if s[1] - s[0] > LONG_STEP_S)
    res["busy_inside_long"] = statistics.fmean(inside) if inside else None
    res["n_inside_long"] = len(inside)
    res["share_inside_long_ge95"] = sum(b >= 0.95 for b in inside) / len(inside) if inside else None
    res["busy_short_only"] = statistics.fmean(short) if short else None
    res["n_short_only"] = len(short)
    return res


def _label(frame: str) -> str:
    """A frame for a table. Inductor's generated code (vLLM's torch.compile cache) becomes
    `compiled layer:<line> <call on that line>`, read from the cached source while it exists."""
    if "torch_compile_cache" not in frame:
        return frame
    path, line, _func = frame.rsplit(":", 2)
    code = linecache.getline(path, int(line)).strip()
    return f"compiled layer:{line} {code.split('(', 1)[0]}" if code else f"compiled layer:{line}"


def _frame(st: list[str], skip=("threading.py", "torch/", "torch_compile_cache", "<string>")) -> str:
    """The innermost frame outside `skip`: for a stack inside torch or compiled code, the vLLM caller."""
    for f in st:
        if not any(s in f for s in skip):
            return f
    return st[-1] if st else "?"


STACK_GROUPS = ("GEMM launch", "attention-op launch", "other compiled-layer launch", "end-of-step wait",
                "other (scheduling, input preparation, sampling)")


def _group(st: list[str]) -> str:
    head = st[:3]
    if any("async_utils.py" in f and f.endswith(":get_output") for f in head):
        return "end-of-step wait"                      # AsyncOutput.copy_event.synchronize(), blocking=True
    if any("flash_attn" in f or "reshape_and_cache" in f for f in head):
        return "attention-op launch"
    if st and "torch_compile_cache" in st[0]:
        label = _label(st[0])
        return "GEMM launch" if "extern_kernels." in label and label.endswith("mm") else "other compiled-layer launch"
    return STACK_GROUPS[-1]


def engine_stacks(steps: list[tuple], stk: list[dict], defs: dict, m0: float, m1: float, top: int = 6) -> dict:
    """Where the engine thread is (share of samples) and the CPU charged there, for samples inside a long
    GPU step, in short steps, or neither. A sample's CPU is that of the 20 ms before it, charged to the
    stack at its end: right where the thread stays put (long steps), smeared onto the wait where it
    alternates work and sleep within 20 ms (short steps). Time shares have no such bias."""
    ls, le = _union([(s[0], s[1]) for s in steps if s[1] - s[0] > LONG_STEP_S])
    ss, se = _union([(s[0], s[1]) for s in steps if s[1] - s[0] < SHORT_STEP_S])
    loc_cpu = {c: defaultdict(float) for c in ("long", "short", "other")}
    loc_n = {c: defaultdict(int) for c in loc_cpu}
    grp_n = {c: defaultdict(int) for c in loc_cpu}
    cpu = defaultdict(float)
    wall = defaultdict(float)
    held = defaultdict(int)
    count = defaultdict(int)
    ms = statistics.median(x["dt"] for x in stk[:200]) if stk else None      # the sampling period
    for r in stk:
        if not m0 <= r["t"] < m1:
            continue
        t = r["t"]
        cls = "long" if _inside(ls, le, t, t) else "short" if _inside(ss, se, t, t) else "other"
        st = defs.get(r["id"], [])
        key = (_label(st[0]) if st else "?", _frame(st))
        loc_cpu[cls][key] += r["cpu"]
        loc_n[cls][key] += 1
        grp_n[cls][_group(st)] += 1
        cpu[cls] += r["cpu"]
        wall[cls] += r["dt"]
        count[cls] += 1
        if ms and r["dt"] > 3 * ms:
            held[cls] += 1
    out = {}
    for cls in loc_cpu:
        n = count[cls]
        if not n:
            continue
        rows = sorted(loc_n[cls], key=lambda k: -loc_n[cls][k])[:top]
        out[cls] = {"samples": n, "busy": cpu[cls] / wall[cls] if wall[cls] else None,
                    "gil_held_share": held[cls] / n,
                    "groups": {g: grp_n[cls][g] / n for g in STACK_GROUPS},
                    "top": [{"frame": k[0], "vllm": k[1], "time_share": loc_n[cls][k] / n, "cpu_s": loc_cpu[cls][k],
                             "cpu_share": loc_cpu[cls][k] / cpu[cls] if cpu[cls] else None} for k in rows]}
    return out


def phases(args) -> int:
    names = args.cells.split(",") if args.cells else sorted(p.name for p in CELLS.iterdir() if p.is_dir() and p.name != "ab")
    t7 = ["# T7 · GPU counters by what the GPU ran (GPM, 100-ms samples)", "",
          "Decode = only decode steps in the 100 ms; prefill = steps with prefill tokens for at least half of it; "
          "share = of the level's GPU samples (the rest is idle or mixed). SM active counts SMs holding a warp, "
          "stalled or not; tensor active counts cycles the tensor pipe issues (100 % = peak).", "",
          "| cell / level | N | decode share | decode batch | decode SM % | occ % | tensor % | HBM BW % | "
          "prefill share | prefill SM % | occ % | tensor % | HBM BW % |", "|" + "---|" * 13]
    t8 = ["# T8 · The EngineCore thread: CPU used vs. scheduler work", "",
          f"busy = the engine process's hottest thread (always `VLLM::EngineCor`), cores, mean of 2-Hz samples. "
          f"schedule / update = vLLM's `schedule()` and `update_from_output()` per step (MUScheduler stamps). "
          f"Inside long step = 0.5-s samples entirely inside one step with launch -> result > {LONG_STEP_S:.0f} s; "
          f"short steps only = samples spanning >= 5 steps, all < {1e3 * SHORT_STEP_S:.0f} ms.", "",
          "| cell / level | N | busy | steps/s | thread CPU ms/step | schedule ms | update ms | scheduler share of CPU | "
          "long steps | busy inside long step (n; share >= 0.95) | busy, short steps only (n) |", "|" + "---|" * 11]
    t9 = ["# T9 · Where the EngineCore thread is, and its CPU (stack samples, cells run with HW_STACK_MS)", "",
          f"Classes: samples inside a step whose launch -> result exceeds {LONG_STEP_S:.0f} s (`long`), inside "
          f"steps under {1e3 * SHORT_STEP_S:.0f} ms (`short`), or neither. busy = thread CPU / wall; GIL held = "
          "share of samples where the sampler waited > 3 periods. Group and location columns are shares of "
          "samples (where the thread is). CPU s charges each 20-ms interval's CPU to the stack at its end: "
          "reliable where the thread stays at one location (long steps), smeared onto the wait in short "
          "steps, where the thread works and sleeps within 20 ms.", ""]
    any_stk = False
    for name in names:
        for srv_dir in sorted((CELLS / name / "server").glob("*")):
            srv = Server(srv_dir)
            if srv.offset is None:
                continue
            steps, stk, defs = step_detail(srv)
            for lv in srv.levels():
                m0, m1 = srv.mono(lv["w0"]), srv.mono(lv["w1"])
                tag = f"{name} / {lv['level']}"
                p = gpu_phases(srv, steps, m0, m1)
                d, f = p["decode"], p["prefill"]
                t7.append(f"| {tag} | {lv['n']} | {fmt(d['share'], 2)} | {fmt(d.get('batch'), 1)} | {fmt(d['sm'], 0)} | "
                          f"{fmt(d['occ'], 0)} | {fmt(d['tensor'], 0)} | {fmt(d['dram'], 0)} | {fmt(f['share'], 2)} | "
                          f"{fmt(f['sm'], 0)} | {fmt(f['occ'], 0)} | {fmt(f['tensor'], 0)} | {fmt(f['dram'], 0)} |")
                e = engine_thread(srv, steps, m0, m1)
                t8.append(f"| {tag} | {lv['n']} | {fmt(e['busy'])} | {fmt(e['steps_per_s'], 1)} | "
                          f"{fmt(e.get('cpu_ms_per_step'), 1)} | {fmt(e['schedule_ms'])} | {fmt(e['update_ms'])} | "
                          f"{fmt(e.get('sched_share'), 3)} | {e['long_steps']} | {fmt(e['busy_inside_long'])} "
                          f"({e['n_inside_long']}; {fmt(e['share_inside_long_ge95'])}) | "
                          f"{fmt(e['busy_short_only'])} ({e['n_short_only']}) |")
                if stk:
                    any_stk = True
                    es = engine_stacks(steps, stk, defs, m0, m1)
                    t9 += [f"## {tag} (N = {lv['n']})", "",
                           "| class | samples | busy | GIL held | " + " | ".join(STACK_GROUPS) + " |",
                           "|" + "---|" * (4 + len(STACK_GROUPS))]
                    for cls, c in es.items():
                        t9.append(f"| {cls} | {c['samples']} | {fmt(c['busy'])} | {fmt(c['gil_held_share'])} | "
                                  + " | ".join(fmt(c["groups"][g]) for g in STACK_GROUPS) + " |")
                    t9 += ["", "| class | location (innermost frame) | vLLM caller | time share | CPU s | CPU share |",
                           "|" + "---|" * 6]
                    for cls, c in es.items():
                        for i, row in enumerate(c["top"]):
                            t9.append(f"| {cls if i == 0 else ''} | `{row['frame']}` | `{row['vllm']}` | "
                                      f"{fmt(row['time_share'])} | {fmt(row['cpu_s'], 1)} | {fmt(row['cpu_share'])} |")
                    t9.append("")
            print(f"{name}: {srv_dir.name} done", flush=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "T7_gpu_phases.md").write_text("\n".join(t7) + "\n")
    (TABLES / "T8_engine_thread.md").write_text("\n".join(t8) + "\n")
    if any_stk:
        (TABLES / "T9_engine_stacks.md").write_text("\n".join(t9) + "\n")
    print(f"wrote T7, T8{', T9' if any_stk else ''} for {names}")
    return 0


# ----------------------------------------------------------------------------------------- smoke
def smoke(args) -> int:
    """Gate after a smoke level: every stream present, non-empty, plausible and joinable."""
    samp, server, level = Path(args.samp), Path(args.server), Path(args.level)
    problems, notes = [], []

    def need(cond: bool, msg: str) -> None:
        (notes if cond else problems).append(("ok   " if cond else "FAIL ") + msg)

    meta = json.loads((samp / "meta.json").read_text()) if (samp / "meta.json").exists() else {}
    fast, host, slow = (read_jsonl(samp / f"{k}.jsonl") for k in ("fast", "host", "slow"))
    need(bool(meta.get("gpus")), f"sampler sees GPUs {[(g.get('name'), g.get('gpm')) for g in meta.get('gpus', [])]}")
    need(len(fast) > 50 and len(host) > 10 and len(slow) > 10, f"streams fast/host/slow = {len(fast)}/{len(host)}/{len(slow)}")
    gpm = any(g.get("gpm") for g in meta.get("gpus", []))
    if fast:
        g0 = [r["gpu"][0] for r in fast if r.get("gpu")]
        busy = [x.get("util", 0) for x in g0]
        need(bool(busy) and max(busy) > 0, f"GPU util seen (max {max(busy) if busy else None} %)")
        if gpm:
            sm = [x.get("sm") for x in g0 if x.get("sm") is not None]
            need(bool(sm) and max(sm) > 0, f"GPM SM util seen (max {max(sm) if sm else None})")
        cores = [c for r in fast for c in (r.get("core") or []) if c is not None]
        need(bool(cores), f"per-core busy recorded ({len(cores)} values)")
    if host:
        last = host[-1]["cpu"]
        need((last.get("vllm_total") or 0) > 0 and "engine" in last,
             f"vLLM CPU api={last.get('api')} engine={last.get('engine')} total={last.get('vllm_total')}")
        need(any((r["pids"].get("n_harness") or 0) > 0 for r in host), "a harness session was seen under the driver")
        need((last.get("agent_total") or 0) > 0, f"agent CPU total {last.get('agent_total')} s (harness {last.get('harness')})")
        need(any(r.get("hot", {}).get("engine") for r in host), "EngineCore threads seen")
        need(any("cg" in r for r in host), f"cgroup stats (v{next((r['cg'].get('ver') for r in host if 'cg' in r), None)})")
    if slow:
        ms = [r for r in slow if r.get("m")]
        need(bool(ms) and any("vllm:kv_cache_usage_perc" in r["m"] for r in ms), f"/metrics scraped ({len(ms)} ok)")
        used = [r["gpu"][0].get("used_mib") for r in slow if r.get("gpu")]
        need(bool(used) and max(u or 0 for u in used) > 10000, f"HBM used up to {max(u or 0 for u in used) if used else None} MiB")
        # the sampled GPU must be OURS: the server's process tree must own memory on it (NVML index mapping)
        try:
            import hw_sampler as hs
            root = int((server / "server.pid").read_text().split()[0])
            tree = {pid for pid, _ in hs.walk(root, lambda q: hs.children_of(q) or [])}
            gpu_pids = set()
            for r in slow[-5:]:
                for g in r.get("gpu") or []:
                    gpu_pids |= {int(x) for x in (g.get("procs") or {})}
            need(bool(tree & gpu_pids), f"sampled GPU(s) hold our vLLM processes ({sorted(tree & gpu_pids)[:4]} of NVML pids {sorted(gpu_pids)[:6]})")
        except Exception as exc:
            problems.append(f"FAIL GPU ownership check: {exc}")
    sched = Path(args.sched) if args.sched else None
    if sched and sched.exists():
        kinds: dict = {}
        for r in read_jsonl(sched):
            kinds[r.get("k")] = kinds.get(r.get("k"), 0) + 1
        need(kinds.get("step", 0) > 0 and kinds.get("pool", 0) > 0 and kinds.get("hw", 0) > 0,
             f"scheduler log kinds {kinds}")
        need(not kinds.get("err"), f"no scheduler errors ({kinds.get('err', 0)})")
    elif sched:
        problems.append(f"FAIL scheduler log missing: {sched}")
    runs = sorted((level / "runs").glob("*"))
    need(len(runs) >= 1, f"{len(runs)} session run(s) in {level.name}")
    for run in runs[:1]:
        metas = list(run.glob("*.meta.json"))
        status = json.loads(metas[0].read_text()).get("status") if metas else None
        need(status in ("ok", "budget", "deadline"), f"session status {status}")
        ev = trace_events(run)
        mono = [e["monotonic_ns"] / 1e9 for e in ev if "monotonic_ns" in e]
        need(any(e.get("event") == "model_request" for e in ev) and any(e.get("event") == "tool_start" for e in ev),
             f"trace has model and tool events ({len(ev)} events)")
        if mono and host:
            overlap = min(max(mono), host[-1]["t"]) - max(min(mono), host[0]["t"])
            need(overlap > 0, f"trace and sampler clocks overlap by {overlap:.0f} s (CLOCK_MONOTONIC join)")
    info = server / "server_info.json"
    need(info.exists(), f"server_info {json.loads(info.read_text()).get('num_gpu_blocks') if info.exists() else None} blocks")
    for line in notes + problems:
        print(f"[smoke] {line}")
    print(f"[smoke] {'PASS' if not problems else 'FAIL'} ({len(problems)} problems)")
    return 0 if not problems else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke")
    s.add_argument("--server", required=True)
    s.add_argument("--samp", required=True)
    s.add_argument("--level", required=True)
    s.add_argument("--sched", default=None)
    p = sub.add_parser("probe")
    p.add_argument("--job", default=None)
    sub.add_parser("ab")
    t = sub.add_parser("tables")
    t.add_argument("--cells", default="")
    ph = sub.add_parser("phases", help="T7 GPU counters by decode / prefill, T8-T9 the EngineCore thread")
    ph.add_argument("--cells", default="")
    args = ap.parse_args()
    return {"smoke": smoke, "probe": probe_table, "ab": ab, "tables": tables, "phases": phases}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
