#!/usr/bin/env python3
"""Topic 24's analysis: every metric of PLAN.md's dictionary, per cell (one N), for the whole window and per phase.

    ~/dgxspark-profiling/exp01_um_profiling/.venv/bin/python research/24_spark_concurrency/sp_analyze.py
    ... sp_analyze.py --cells smoke           # one cell

Run with exp01's venv (pandas, numpy, matplotlib). Writes data/tables/sp.json (every number), data/tables/sp.md (the
tables) and data/tables/figs/*.svg.

Sources per cell (PLAN.md, "What is measured"): R requests, I steps (the sp_reqlog plugin), H the harness's calls and
events, S exp02's sampler, P the probe, X sp_extra, G and C the root counter sampler (shared across cells, cut by time).

Phases. A call is in the snip phase once its session has been trimmed (the first `snip_compact` event in its
events.jsonl); before that, in the append phase. For numbers over time, a second belongs to the append period when at
least 80% of the sessions active in it are in the append phase, and to the snip period when at least 80% are in the snip
phase (README, Changes: the plan's "first trim to first end" definition is empty when a session finishes early).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp_common as C  # noqa: E402
import sp_hwread as H  # noqa: E402

sys.path.insert(0, str(C.EXP02 / "src"))
sys.path.insert(0, str(C.EXP02 / "scripts"))

BLOCK = 784
SECTOR = 32
LINE = 64
PEAK_GBPS = 273.06
GPU_HZ = 100                 # sp_hw.py samples the GPU counters at 100 Hz
IDLE_PROBE_NS = 151.0
PHASE_SHARE = 0.8
BOOT = 2000
_MB: dict = {}


def mbytes(variant: str) -> dict:
    """The byte model of a variant's checkpoint (sp_modelbytes.py); for BF16, exp02's file if ours is not written."""
    if variant not in _MB:
        p = C.DATA / f"model_bytes_{variant}.json"
        if not p.exists() and variant == "bf16":
            p = C.EXP02 / "raw" / "model_bytes.json"
        _MB[variant] = json.loads(p.read_text())
    return _MB[variant]


def cell_order() -> list[str]:
    import sp_chain
    return [name for name, *_ in sp_chain.CELLS] + ["smoke"]


# ---------------------------------------------------------------------------------------------------------------------
# loading
def jl(paths) -> pd.DataFrame:
    rows = []
    for p in paths:
        rows += C.read_jsonl(p)
    return pd.DataFrame(rows)


class Cell:
    def __init__(self, name: str, light: bool = False, until_s: float | None = None):
        """light: only the trial files and the server's request and step records (the live stop rule, sp_stable.py).
        until_s: cut the window that many seconds after the users started (replays of the stop rule)."""
        self.name = name
        self.dir = C.RUNS / name
        self.info = json.loads((self.dir / "cell.json").read_text())
        self.n = int(self.info["n"])
        self.variant = self.info.get("variant", "bf16")
        self.until_s = until_s
        self.req = jl(sorted((self.dir / "reqlog").glob("requests_*.jsonl")))
        self.steps = jl(sorted((self.dir / "reqlog").glob("steps_*.jsonl")))
        if len(self.steps):
            self.steps = self.steps.sort_values("t").reset_index(drop=True)
        self.extra = pd.DataFrame() if light else jl([self.dir / "extra.jsonl"])
        smp = self.dir / "raw" / f"sampler_{name}.csv"
        self.smp = pd.read_csv(smp) if smp.exists() and not light else pd.DataFrame()
        self.windows = pd.DataFrame() if light else jl([self.dir / "raw" / "windows.jsonl"])
        self.load_trials()
        self.window()

    def load_trials(self):
        calls, sess, events = [], [], []
        job = C.JOBS / self.name
        for d in sorted(p for p in job.iterdir() if p.is_dir()) if job.exists() else []:
            s15 = d / "agent" / "s15"
            try:
                t0 = json.loads((s15 / "wb_t0.json").read_text())
            except (OSError, ValueError):
                continue
            res = {}
            try:
                res = json.loads((s15 / "result.json").read_text())
            except (OSError, ValueError):
                pass
            for c in C.read_jsonl(s15 / "calls.jsonl"):
                c = {k: c.get(k) for k in ("call", "purpose", "t", "ms", "rid", "t_send", "t_log", "stop_reason",
                                           "usage", "max_tokens", "error", "refused", "tools", "thinking_chars")}
                c["trial"] = d.name
                calls.append(c)
            evs = C.read_jsonl(s15 / "events.jsonl")
            for e in evs:
                events.append({"trial": d.name, "kind": e.get("kind"), "t": e.get("t"), "lead_calls": e.get("lead_calls"),
                               "t_mono": t0["t0_mono"] + int(float(e.get("t", 0)) * 1e9)})
            snips = [e for e in evs if e.get("kind") == "snip_compact"]
            last_t = max([c["t"] for c in calls if c["trial"] == d.name and c.get("t") is not None] or [0])
            sess.append({"trial": d.name, "task": d.name.rsplit("__", 1)[0], "t0": t0["t0"], "t0_mono": t0["t0_mono"],
                         "snip_mono": (t0["t0_mono"] + int(float(snips[0]["t"]) * 1e9)) if snips else None,
                         "snip_lead": snips[0].get("lead_calls") if snips else None,
                         "end_wall_rel": res.get("wall_s"), "status": res.get("status"),
                         "stop_reason": res.get("stop_reason"), "lead": (res.get("counter") or {}).get("lead"),
                         "last_call_t": last_t})
        self.calls = pd.DataFrame(calls)
        self.sess = pd.DataFrame(sess)
        self.events = pd.DataFrame(events)
        if len(self.sess):
            self.sess = self.sess.sort_values("t0_mono").reset_index(drop=True)
            self.sess["first"] = self.sess.index < self.n
            for col in ("snip_mono", "snip_lead", "end_wall_rel", "lead"):
                self.sess[col] = pd.to_numeric(self.sess[col], errors="coerce")
            end = self.sess.t0_mono + (self.sess.end_wall_rel.fillna(self.sess.last_call_t) * 1e9)
            self.sess["end_mono"] = end.astype("int64")

    def window(self):
        m = self.info.get("marks", {})
        first = self.sess[self.sess["first"]] if len(self.sess) else self.sess
        self.t0 = int(first.t0_mono.min()) if len(first) else m.get("users_start", {}).get("t")
        self.t1 = m.get("window_end", {}).get("t") or (int(self.steps.t.max()) if len(self.steps) else None)
        if self.until_s is not None and self.t0 and self.t1:
            self.t1 = min(self.t1, self.t0 + int(self.until_s * 1e9))
        if self.t1 and len(self.sess):
            # a session with no result yet (live cell, or cut off at teardown) is active up to the window's end
            live = self.sess.status.isna() & (self.sess.end_mono < self.t1)
            self.sess.loc[live, "end_mono"] = self.t1
        self.secs = (self.t1 - self.t0) / 1e9 if self.t0 and self.t1 else float("nan")


# ---------------------------------------------------------------------------------------------------------------------
# calls: harness records joined with the server's
def calls_table(c: Cell) -> pd.DataFrame:
    if not len(c.calls):
        return pd.DataFrame()
    k = c.calls.dropna(subset=["rid"]).copy()
    k["id"] = "chatcmpl-" + k.rid
    r = c.req.drop_duplicates("id", keep="last") if len(c.req) else pd.DataFrame(columns=["id"])
    k = k.merge(r, on="id", how="left", suffixes=("", "_srv"))
    k["latency"] = k.ms / 1000
    k["ttft"] = k.e2e - k.decode
    k["recomputed"] = k.p - k.c
    k["frontend"] = k.latency - k.e2e
    k["t_send"] = pd.to_numeric(k.t_send, errors="coerce")
    # phase: after the session's first snip
    snip = c.sess.set_index("trial").snip_mono if len(c.sess) else pd.Series(dtype=float)
    k["snip_t"] = pd.to_numeric(k.trial.map(snip), errors="coerce")
    k["phase"] = np.where(k.snip_t.notna() & (k.t_send >= k.snip_t), "snip", "append")
    k["first"] = k.trial.map(c.sess.set_index("trial")["first"]) if len(c.sess) else False
    k["in_window"] = (k.t_send >= c.t0) & (k.t_send < c.t1)
    k = k.sort_values(["trial", "call"]).reset_index(drop=True)
    # time between this call's reply and the session's next call: tools and harness work
    k["t_log"] = pd.to_numeric(k.t_log, errors="coerce")
    k["tool_s"] = (k.groupby("trial").t_send.shift(-1) - k.t_log) / 1e9
    # cache loss: a lead call that reuses less than its session's previous lead prompt up to the last 784 boundary,
    # with no harness edit (any compaction event) in between
    k["expected_cached"] = np.nan
    k["cache_loss_tok"] = np.nan
    ev = c.events
    for trial, g in k[k.purpose == "lead"].groupby("trial"):
        prev = None
        for i, row in g.iterrows():
            if prev is not None and not math.isnan(prev["p"]) and not math.isnan(row["p"]) and row["phase"] == "append":
                edits = ev[(ev.trial == trial) & (ev.t_mono > prev["t_send"]) & (ev.t_mono <= row["t_send"])] \
                    if len(ev) else []
                if len(edits) == 0:
                    exp_c = math.floor((prev["p"] - 1) / BLOCK) * BLOCK
                    k.at[i, "expected_cached"] = exp_c
                    k.at[i, "cache_loss_tok"] = max(0.0, exp_c - row["c"])
            prev = row
    return k


def q(xs, p):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(np.percentile(xs, p)) if xs else None


def dist(xs, n_calls: int) -> dict:
    """Mean and p50 always; p90 from 80 values, p95 from 160, p99 from 500 (PLAN.md)."""
    xs = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    out = {"n": len(xs), "mean": float(np.mean(xs)) if xs else None, "p50": q(xs, 50)}
    for p, need in ((90, 80), (95, 160), (99, 500)):
        out[f"p{p}"] = q(xs, p) if len(xs) >= need else None
    return out


def boot_sessions(df: pd.DataFrame, col: str, stat, seed: str) -> list | None:
    """95% interval of stat(values) resampling sessions (a session's calls stay together)."""
    groups = [g[col].dropna().tolist() for _, g in df.groupby("trial")]
    groups = [g for g in groups if g]
    if len(groups) < 5:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOT):
        vals = [v for _ in groups for v in rng.choice(groups)]
        draws.append(stat(vals))
    draws.sort()
    return [draws[int(0.025 * BOOT)], draws[int(0.975 * BOOT) - 1]]


# ---------------------------------------------------------------------------------------------------------------------
# time: a 1-second grid over the window with the phase mix of active sessions
def grid(c: Cell) -> pd.DataFrame:
    t = np.arange(c.t0, c.t1, int(1e9), dtype="int64")
    g = pd.DataFrame({"t": t})
    s = c.sess
    act = np.zeros(len(t))
    snp = np.zeros(len(t))
    for _, r in s.iterrows():
        a = (t >= r.t0_mono) & (t < r.end_mono)
        act += a
        if not math.isnan(r.snip_mono):
            snp += a & (t >= r.snip_mono)
    g["active"] = act
    g["snip_share"] = np.where(act > 0, snp / np.maximum(act, 1), np.nan)
    g["period"] = np.where(g.snip_share <= 1 - PHASE_SHARE, "append",
                           np.where(g.snip_share >= PHASE_SHARE, "snip", "mixed"))
    # steady: from the first second when half of the active sessions have been trimmed. Ended sessions are replaced
    # by fresh ones, so the population stays a mix of both phases from then on (the pure snip period never comes).
    half = np.where(g.snip_share.values >= 0.5)[0]
    g["steady"] = (np.arange(len(g)) >= half[0]) if len(half) else False
    return g


def periods(g: pd.DataFrame) -> dict:
    """Masks over the grid: whole window, append period, snip period, steady period."""
    return {"all": np.ones(len(g), bool), "append": (g.period == "append").values, "snip": (g.period == "snip").values,
            "steady": g.steady.values.astype(bool)}


def per_second(t_ns, values, g: pd.DataFrame, how: str = "sum") -> np.ndarray:
    """Bin samples at times t_ns onto the 1 s grid g (sum or mean per second)."""
    if len(g) == 0:
        return np.array([])
    idx = ((np.asarray(t_ns, dtype="int64") - g.t.iloc[0]) // int(1e9)).astype(int)
    ok = (idx >= 0) & (idx < len(g))
    s = pd.Series(np.asarray(values, dtype=float)[ok]).groupby(idx[ok])
    out = np.full(len(g), np.nan if how == "mean" else 0.0)
    agg = s.mean() if how == "mean" else s.sum()
    out[agg.index.values] = agg.values
    return out


def per_second_counted(t_ns, values, g: pd.DataFrame, expect: float) -> np.ndarray:
    """Per-second sums of a counter sampled `expect` times per second. A second with fewer than half its samples is
    NaN (no report covers it: sampler restart, segment not written yet); a partly covered one is scaled up."""
    tot = per_second(t_ns, values, g, "sum")
    n = per_second(t_ns, np.ones(len(values)), g, "sum")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(n >= expect / 2, tot * np.maximum(1.0, expect / np.maximum(n, 1)), np.nan)


def spread(t_end_ns, dur_s, g: pd.DataFrame) -> np.ndarray:
    """Seconds of step time in each grid second: a step that ends at t_end and lasted dur is spread over the seconds
    it spans (a 1.6 s prefill step would otherwise put all its time into the second it ends in)."""
    out = np.zeros(len(g))
    if len(g) == 0:
        return out
    t0 = int(g.t.iloc[0])
    end = (np.asarray(t_end_ns, dtype="int64") - t0) / 1e9
    dur = np.nan_to_num(np.asarray(dur_s, dtype=float))
    for e, d in zip(end, dur):
        if d <= 0:
            continue
        a = e - d
        for k in range(max(int(np.floor(a)), 0), min(int(np.floor(e)), len(out) - 1) + 1):
            out[k] += max(0.0, min(e, k + 1) - max(a, k))
    return out


def nmean(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(np.nanmean(x)) if np.isfinite(x).any() else float("nan")


def block_boot_mean(x: np.ndarray, block: int, seed: str) -> list | None:
    x = x[~np.isnan(x)]
    nb = len(x) // block
    if nb < 4:
        return None
    blocks = [x[i * block:(i + 1) * block] for i in range(nb)]
    rng = random.Random(seed)
    draws = sorted(float(np.mean(np.concatenate([rng.choice(blocks) for _ in range(nb)]))) for _ in range(BOOT))
    return [draws[int(0.025 * BOOT)], draws[int(0.975 * BOOT) - 1]]


def stability(x: np.ndarray, mask: np.ndarray) -> dict | None:
    """Snip period cut into three equal parts by time; stable if each part's mean is within 10% of their mean."""
    v = x[mask]
    v = v[~np.isnan(v)]
    if len(v) < 300:
        return None
    parts = [float(np.mean(p)) for p in np.array_split(v, 3)]
    m = float(np.mean(parts))
    return {"parts": parts, "stable": bool(m and all(abs(p / m - 1) <= 0.10 for p in parts))}


# ---------------------------------------------------------------------------------------------------------------------
# step-level: durations, types, byte model
def step_table(c: Cell) -> pd.DataFrame:
    s = c.steps.copy()
    if not len(s):
        return s
    s = s[(s.t >= c.t0) & (s.t < c.t1)].copy()
    s["dur"] = s.t.diff() / 1e9
    for k in ("ct", "gt", "gr", "ng", "np", "run", "wait", "kv"):
        if k not in s:
            s[k] = 0
    s = s.fillna({"ct": 0, "gt": 0, "gr": 0, "ng": 0, "np": 0})
    s["prefill"] = (s.ct > 0) | (s["gt"] > s["gr"])
    # a gap over 5 s means the engine idled (no step); count it as idle, not as a step
    s["busy"] = s.dur.where(s.dur <= 5.0, np.nan)
    return s


def context_at(c: Cell, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(B, S) per time: number of decoding requests and the sum of their context lengths, from R (each request's
    first token at t - decode, last at t, context growing linearly from its prompt to prompt + generated)."""
    r = c.req.dropna(subset=["decode", "p", "g"])
    t_last = r.t.values.astype("int64")
    t_first = t_last - (r.decode.values * 1e9).astype("int64")
    p, gen = r.p.values.astype(float), r.g.values.astype(float)
    B = np.zeros(len(times))
    S = np.zeros(len(times))
    for i, t in enumerate(times):
        a = (t_first <= t) & (t <= t_last)
        if a.any():
            frac = np.where(t_last[a] > t_first[a], (t - t_first[a]) / np.maximum(t_last[a] - t_first[a], 1), 1.0)
            B[i] = a.sum()
            S[i] = float(np.sum(p[a] + gen[a] * frac))
    return B, S


def model_bytes(B: np.ndarray, S: np.ndarray, m: dict) -> dict:
    return {"weights": np.full(len(B), m["w_step"]) * (B > 0), "state": 2 * B * m["state_bytes_seq"],
            "kv_read": S * m["kv_bytes_token"], "kv_write": B * m["kv_bytes_token"],
            "io": B * (m["embed_row"] + 2 * m["logits_row"])}


# ---------------------------------------------------------------------------------------------------------------------
# hardware counters, cut to the cell's window
def gpu_series(c: Cell, g: pd.DataFrame) -> dict:
    rows = []
    for s in H.sessions(C.HW):
        rows += [r for r in H.gpu_rows(s) if c.t0 <= r[0] < c.t1]
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["t", "name", "value"])
    out = {"_samples_per_s": len(df.t.unique()) / max(c.secs, 1)}
    for name, d in df.groupby("name"):
        if "Sectors" in name or "Insts" in name or "Misses" in name:
            out[name] = per_second_counted(d.t.values, d.value.values, g, GPU_HZ)
        else:
            out[name] = per_second(d.t.values, d.value.values, g, "mean")
    return out


def gpu_named(gs: dict, key: str):
    for k, v in gs.items():
        if not k.startswith("_") and key.lower() in k.lower():
            return v
    return None


def perf_series(c: Cell, g: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for s in H.sessions(C.HW):
        rows += [r for r in H.perf_rows(s) if c.t0 <= r[0] < c.t1]
    return pd.DataFrame(rows, columns=["t", "cpu", "event", "value", "run_pct"]) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------------------------------------------------
def gates(c: Cell, k: pd.DataFrame, st: pd.DataFrame) -> dict:
    """Data-quality checks on one cell. Each entry: {"ok": bool, "value": ..., "need": text}."""
    G = {}

    def gate(name, value, ok, need):
        G[name] = {"ok": bool(ok), "value": value, "need": need}

    # 1. every harness call found in the server's request log
    if len(k):
        # calls cut off by the teardown have no stop reason: they were in flight when the server stopped
        done = k[k.stop_reason.notna()]
        # (a session that ends at its call budget also logs one last record without a stop reason: not a cut call)
        ended = c.sess.set_index("trial").status.notna() if len(c.sess) else pd.Series(dtype=bool)
        cut = int((k.stop_reason.isna() & ~k.trial.map(ended).fillna(False).astype(bool)).sum())
        gate("calls_cut_at_teardown", cut, cut <= 2 * c.n + 2, "calls in flight at teardown <= 2N + 2")
        j = float(done.e2e.notna().mean()) if len(done) else float("nan")
        gate("calls_joined", round(j, 4), j >= 0.99, "share of harness calls with a server record >= 0.99")
        # 2. harness and server agree on the tokens of each call
        u = done[done.e2e.notna()]
        ho = pd.to_numeric(u.usage.map(lambda x: (x or {}).get("output_tokens")), errors="coerce")
        hp = pd.to_numeric(u.usage.map(lambda x: sum((x or {}).get(f) or 0 for f in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))), errors="coerce")
        gate("output_tokens_match", int((ho != u.g).sum()), (ho == u.g).all(), "calls whose output tokens differ = 0")
        gate("prompt_tokens_match", int((hp != u.p).sum()), (hp == u.p).all(), "calls whose prompt tokens differ = 0")
        # 3. the client can never see less time than the server spent
        neg = int((u.latency < u.e2e - 0.05).sum())
        gate("client_ge_server", neg, neg == 0, "calls where client latency < server e2e = 0")
    r = c.req
    if len(r):
        over = int((r.queue + r.prefill + r.decode > r.e2e + 0.05).sum())
        gate("phases_within_e2e", over, over == 0, "requests with queue+prefill+decode > e2e = 0")
        # 4. the plugin's totals against vLLM's own Prometheus counters over the same server life. The counters
        # tick per engine step, like the step records; request records exist only for finished requests.
        if len(c.smp) and "m_generation_tokens_total" in c.smp and len(c.steps):
            cnt = {col: pd.to_numeric(c.smp[col], errors="coerce").max() for col in (
                "m_generation_tokens_total", "m_prompt_tokens_total", "m_prompt_tokens_cached_total",
                "m_request_success_total")}
            for name, mine, theirs in (
                    ("counter_generation_tokens", c.steps.ng.sum(), cnt["m_generation_tokens_total"]),
                    ("counter_computed_prompt_tokens", c.steps.ct.sum(),
                     cnt["m_prompt_tokens_total"] - cnt["m_prompt_tokens_cached_total"]),
                    ("counter_requests", len(r), cnt["m_request_success_total"])):
                if theirs and theirs > 0:
                    ratio = float(mine / theirs)
                    gate(name, round(ratio, 4), abs(ratio - 1) <= 0.01, "plugin total / vLLM counter within 1%")
            unfinished = float(c.steps.ng.sum() - r.g.sum())
            gate("tokens_of_unfinished_requests", int(unfinished), 0 <= unfinished <= 8000 * 2 * c.n,
                 "step tokens minus finished requests' tokens: >= 0 and small")
    # 5. samplers kept their rates over the window
    if len(c.smp) and c.secs == c.secs:
        n = int(((c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)).sum())
        gate("sampler_hz", round(n / c.secs, 2), n / c.secs >= 9.0, "10 Hz sampler >= 9 Hz")
    if len(c.extra) and c.secs == c.secs:
        n = int(((c.extra.t >= c.t0) & (c.extra.t < c.t1)).sum())
        gate("extra_hz", round(n / c.secs, 3), n / c.secs >= 0.9, "1 Hz sampler >= 0.9 Hz")
    if len(c.windows) and "kind" in c.windows:
        pr = c.windows[(c.windows.kind == "probe") & (c.windows.t1_ns > c.t0) & (c.windows.t0_ns < c.t1)]
        cov = float(((pr.t1_ns.clip(upper=c.t1) - pr.t0_ns.clip(lower=c.t0)).sum() / 1e9) / c.secs) if len(pr) else 0.0
        gate("probe_coverage", round(cov, 3), cov >= 0.9, "probe windows cover >= 90% of the window")
    # 6. the cell ended the way it should, with memory to spare
    gate("cell_done", c.info.get("stop_reason"), c.info.get("stop_reason") in ("done", "hard_limit", "stable"),
         "stop reason is done, hard_limit or stable")
    if len(c.smp):
        sm = c.smp[(c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)]
        if len(sm):
            lo = float(sm.mem_MemAvailable.min() / 2**30)
            gate("mem_available_min_gib", round(lo, 1), lo >= 4.0, "MemAvailable never below 4 GiB")
            # pages swapped out during the window (swap already used before it, e.g. while the weights loaded
            # through the page cache, may slowly come back in: that is not counted)
            sw = float((sm.mem_SwapFree.iloc[0] - sm.mem_SwapFree.min()) / 2**30)
            gate("swap_out_in_window_gib", round(sw, 2), sw < 0.25, "swap use grew by < 0.25 GiB during the window")
    pre = int(r.pre.sum()) if len(r) and "pre" in r else 0
    gate("preemptions", pre, pre == 0, "no preempted requests")
    gate("hw_counters", bool(c.info.get("hw_ok")), bool(c.info.get("hw_ok")), "root counter sampler ran for the cell")
    G["all_ok"] = all(v["ok"] for v in G.values())
    G["serving_ok"] = all(v["ok"] for n, v in G.items() if isinstance(v, dict) and n != "hw_counters")
    return G


def analyze(c: Cell, cpu_read_event: str = "l2d_cache_refill", tensor_scale: float | None = None) -> dict:
    out: dict = {"cell": c.name, "n": c.n, "window_h": c.secs / 3600, "status": c.info.get("status"),
                 "stop_reason": c.info.get("stop_reason"), "hw_ok": c.info.get("hw_ok"), "pool": c.info.get("pool"),
                 "variant": c.variant, "label": C.VARIANTS[c.variant]["label"], "repo": C.VARIANTS[c.variant]["repo"]}
    MB = mbytes(c.variant)
    out["model"] = {"w_step_gb": MB["w_step"] / 1e9, "resident_gb": MB["resident_bytes"] / 1e9,
                    "flops_per_token_linear": MB.get("flops_per_token_linear"),
                    "kv_tokens": (c.info.get("server_log") or {}).get("kv_tokens"),
                    "model_gib": (c.info.get("server_log") or {}).get("model_gib"),
                    "weights_load_s": (c.info.get("server_log") or {}).get("weights_s"),
                    "kernel_lines": c.info.get("kernel_lines")}
    if c.variant != "bf16":
        tensor_scale = None        # one FLOPs-per-instruction scale holds for BF16 only (PLAN2.md, "Tensor work")
    fpt = MB.get("flops_per_token_linear") or mbytes("bf16").get("flops_per_token_linear")
    k = calls_table(c)
    g = grid(c)
    P = periods(g)
    out["periods_s"] = {p: int(m.sum()) for p, m in P.items()}
    st = step_table(c)
    # ---- 1. latency (first sessions' calls, as PLAN.md says; all calls in a supporting entry)
    lat = {}
    if len(k):
        kf = k[k["first"] & k.e2e.notna()]
        steady_t0 = int(g.t[g.steady.values.astype(bool)].iloc[0]) if g.steady.any() else None
        for ph in ("all", "append", "snip", "steady"):
            kk = kf if ph == "all" else kf[kf.t_send >= steady_t0] if ph == "steady" and steady_t0 else \
                kf.iloc[:0] if ph == "steady" else kf[kf.phase == ph]
            lat[ph] = {m: dist(kk[col].tolist(), len(kk)) for m, col in
                       (("call_latency_s", "latency"), ("ttft_s", "ttft"), ("queue_s", "queue"),
                        ("prefill_s", "prefill"), ("tpot_ms", None), ("decode_s", "decode"), ("e2e_s", "e2e"),
                        ("frontend_s", "frontend"), ("tool_s", "tool_s")) if col} | {
                            "tpot_ms": dist((kk.tpot * 1000).tolist(), len(kk))}
        lat["all_calls"] = {"call_latency_s": dist(k[k.e2e.notna()].latency.tolist(), len(k)),
                            "tpot_ms": dist((k.tpot * 1000).tolist(), len(k))}
        lat["ci"] = {"tpot_ms_p50": boot_sessions(kf.assign(x=kf.tpot * 1000), "x", lambda v: float(np.median(v)),
                                                  f"tpot|{c.name}"),
                     "ttft_s_mean": boot_sessions(kf, "ttft", lambda v: float(np.mean(v)), f"ttft|{c.name}"),
                     "call_latency_s_mean": boot_sessions(kf, "latency", lambda v: float(np.mean(v)),
                                                          f"lat|{c.name}")}
        # ITL from the steps' token gaps
        itl = [x for v in st.get("itl", pd.Series(dtype=object)).dropna() for x in v] if len(st) else []
        lat["itl_ms"] = {"p50": q([x * 1000 for x in itl], 50), "p99": q([x * 1000 for x in itl], 99),
                         "max": max(itl) * 1000 if itl else None, "n": len(itl)}
    out["latency"] = lat
    # ---- 2. throughput (per second, then per period)
    thr = {}
    if len(st):
        gen = per_second(st.t, st.ng, g)
        ptok = per_second(st.t, [v[1] if isinstance(v, list) else 0 for v in st.get("pt", [None] * len(st))], g)
        reqs = per_second(c.req.t, np.ones(len(c.req)), g) if len(c.req) else np.zeros(len(g))
        for ph, m in P.items():
            if m.sum() == 0:
                continue
            thr[ph] = {"output_tok_s": float(gen[m].mean()), "computed_prompt_tok_s": float(ptok[m].mean()),
                       "requests_h": float(reqs[m].mean() * 3600)}
            if fpt:      # matrix math of the linear layers for the tokens actually computed, any precision
                thr[ph]["workload_tflops"] = (thr[ph]["output_tok_s"] + thr[ph]["computed_prompt_tok_s"]) * fpt / 1e12
        blk = max(600, int(3 * (k.latency.median() if len(k) else 200)))
        thr["ci_output_tok_s"] = block_boot_mean(gen, blk, f"gen|{c.name}")
        thr["stability_output_tok_s"] = stability(gen, P["steady"])
        if len(k):
            kw = k[k.in_window]
            thr["requests_h_by_purpose"] = (kw.groupby("purpose").size() / (c.secs / 3600)).round(1).to_dict()
            lead = kw[kw.purpose == "lead"]
            thr["calls_per_user_h"] = len(lead) / (c.n * c.secs / 3600)
            tp = k[k["first"]].tpot.dropna()
            thr["per_user_decode_tok_s"] = 1 / float(tp.median()) if len(tp) else None
    out["throughput"] = thr
    # ---- 3. cache and scheduler
    cache = {}
    if len(k):
        lead = k[(k.purpose == "lead") & k.p.notna()]
        for ph in ("all", "append", "snip", "steady"):
            kk = lead if ph == "all" else lead[lead.t_send >= steady_t0] if ph == "steady" and steady_t0 else \
                lead.iloc[:0] if ph == "steady" else lead[lead.phase == ph]
            if len(kk):
                cache[ph] = {"recomputed_tok_per_call": dist(kk.recomputed.tolist(), len(kk)),
                             "hit_rate": float(kk.c.sum() / kk.p.sum()) if kk.p.sum() else None,
                             "prompt_tok_per_call": dist(kk.p.tolist(), len(kk)),
                             "output_tok_per_call": dist(kk.g.tolist(), len(kk))}
        cl = k.dropna(subset=["cache_loss_tok"])
        cache["cache_loss"] = {"calls_checked": len(cl), "share": float((cl.cache_loss_tok > 0).mean()) if len(cl) else None,
                               "tokens_mean": float(cl.cache_loss_tok.mean()) if len(cl) else None}
    if len(st):
        w = st.busy.fillna(0)
        for ph, m in P.items():
            sec = per_second(st.t, np.ones(len(st)), g)
            kv = per_second(st.t, st.kv, g, "mean")
            run = per_second(st.t, st.run, g, "mean")
            wait = per_second(st.t, st.wait, g, "mean")
            if m.sum() == 0:
                continue
            cache.setdefault(ph, {}).update(
                kv_pinned_mean=float(np.nanmean(kv[m])), kv_pinned_p95=q(kv[m], 95),
                kv_pinned_ge95_share=float(np.nanmean(kv[m] >= 0.95)),
                running_mean=float(np.nanmean(run[m])), waiting_mean=float(np.nanmean(wait[m])),
                running_max=float(np.nanmax(run[m])), waiting_max=float(np.nanmax(wait[m])),
                in_flight_share=float(np.nanmean((run[m] + wait[m]) / c.n)), steps_per_s=float(sec[m].mean()))
        cache["preemptions_per_min"] = float(st.np.sum() / (c.secs / 60))
        cache["steps_with_prefill_share"] = float(st.prefill.mean())
        cache["time_in_prefill_steps_share"] = float(w[st.prefill].sum() / max(w.sum(), 1e-9))
        cache["stability_kv_pinned"] = stability(per_second(st.t, st.kv, g, "mean"), P["steady"])
    out["cache"] = cache
    # ---- 4/5. GPU counters, byte model
    gs = gpu_series(c, g) if c.info.get("hw_ok") else {}
    mem, gpu = {}, {}
    rd, wr, hub = gpu_named(gs, "L2 Read Misses"), gpu_named(gs, "L2 Write Sectors"), gpu_named(gs, "from HUB")
    tensor, issue, smact = gpu_named(gs, "Tensor Active"), gpu_named(gs, "SM Issue"), gpu_named(gs, "SMs Active")
    warps, fma, ops = gpu_named(gs, "Compute Warps"), gpu_named(gs, "FP32 FMA"), gpu_named(gs, "Tensor HMMA Insts")
    rsec = gpu_named(gs, "L2 Read Sectors")
    pf = perf_series(c, g) if c.info.get("hw_ok") else pd.DataFrame()
    # CPU traffic from per-core counters, as the counter check (S1a) calibrated them against known streams:
    #   reads  = L2 refills x 64 B (within 1% on both core types; counts refills served by L3 too, so an upper bound
    #            when the working set fits L3)                         lower bound = demand LLC read misses x 64 B
    #   total  = bus transactions x 32 B (2.04-2.08 transactions per 64-byte line, reads and writes alike)
    #   writes = total - reads, floored at 0
    cpu_rd = cpu_wb = cpu_lo = cpu_tot = None
    if len(pf):
        def ev(name, nbytes, frame=pf):
            sel = frame[frame.event == name]
            return per_second_counted(sel.t, sel.value * nbytes / 1e9, g, sel.cpu.nunique()) if len(sel) else None
        cpu_rd, cpu_lo, cpu_tot = ev(cpu_read_event, LINE), ev("ll_cache_miss_rd", LINE), ev("bus_access", LINE / 2)
        if cpu_rd is not None and cpu_tot is not None:
            cpu_wb = np.clip(cpu_tot - cpu_rd, 0, None)
    for ph, m in P.items():
        if m.sum() == 0:
            continue
        e = {}
        if rd is not None:
            e["gpu_read_gbps"] = float(nmean(rd[m]) * SECTOR / 1e9)
        if wr is not None:
            e["gpu_write_gbps"] = float(nmean(wr[m]) * SECTOR / 1e9)
        if hub is not None:
            e["gpu_hub_gbps"] = float(nmean(hub[m]) * SECTOR / 1e9)
        if cpu_rd is not None:
            e["cpu_read_gbps"] = float(nmean(cpu_rd[m]))
            if cpu_lo is not None:
                e["cpu_read_gbps_lower"] = float(nmean(cpu_lo[m]))
            if cpu_wb is not None:
                e["cpu_write_gbps"] = float(nmean(cpu_wb[m]))
                e["cpu_total_gbps"] = float(nmean(cpu_tot[m]))
            for grp, cores in C.CORE_GROUPS.items():
                sub = pf[pf.cpu.isin(cores)]
                r_, t_ = ev(cpu_read_event, LINE, sub), ev("bus_access", LINE / 2, sub)
                if r_ is not None:
                    e[f"cpu_read_gbps_{grp}"] = float(nmean(r_[m]))
                if t_ is not None:
                    e[f"cpu_total_gbps_{grp}"] = float(nmean(t_[m]))
        parts = [e.get(x) for x in ("gpu_read_gbps", "gpu_write_gbps", "cpu_read_gbps", "cpu_write_gbps")]
        if all(v is not None for v in parts):
            e["total_gbps"] = float(sum(parts))
            e["total_pct_peak"] = e["total_gbps"] / PEAK_GBPS * 100
        mem[ph] = e
        ge = {}
        for key, ser in (("tensor_active_pct", tensor), ("sm_issue_pct", issue), ("sms_active_pct", smact),
                         ("warps_in_flight_pct", warps), ("fp32_pipe_pct", fma)):
            if ser is not None:
                ge[key] = float(np.nanmean(ser[m]))
        if ops is not None:
            ge["tensor_insts_per_s"] = float(nmean(ops[m]))
        if ops is not None and tensor_scale:
            ge["achieved_tflops"] = float(nmean(ops[m]) * tensor_scale / 1e12)
        if rsec is not None and rd is not None and np.nansum(rsec[m]):
            ge["l2_read_hit_rate"] = float(1 - np.nansum(rd[m]) / np.nansum(rsec[m]))
        gpu[ph] = ge
    # by step type: a second is "decode" when at least 90% of its step time is in decode-only steps, "prefill" when at
    # least half is in steps that carry a prefill chunk (as topic 12's T7)
    if len(st):
        dsec = spread(st.t[~st.prefill], st.busy[~st.prefill], g)
        psec = spread(st.t[st.prefill], st.busy[st.prefill], g)
        tot = dsec + psec
        kinds = {"decode": (tot > 0.5) & (dsec >= 0.9 * tot), "prefill": (tot > 0.5) & (psec >= 0.5 * tot),
                 "idle": tot <= 0.05}
        by = {}
        for kind, mk in kinds.items():
            e = {"seconds": int(mk.sum())}
            if mk.sum():
                for key, ser, scale in (("gpu_read_gbps", rd, SECTOR / 1e9), ("gpu_write_gbps", wr, SECTOR / 1e9),
                                        ("tensor_active_pct", tensor, 1), ("sm_issue_pct", issue, 1),
                                        ("sms_active_pct", smact, 1), ("fp32_pipe_pct", fma, 1),
                                        ("cpu_read_gbps", cpu_rd, 1)):
                    if ser is not None:
                        e[key] = float(np.nanmean(ser[mk]) * scale)
                if ops is not None:
                    e["tensor_insts_per_s"] = float(nmean(ops[mk]))
                if ops is not None and tensor_scale:
                    e["achieved_tflops"] = float(nmean(ops[mk]) * tensor_scale / 1e12)
            by[kind] = e
        gpu["by_step_type"] = by
    if rd is not None:
        mem["stability_gpu_read"] = stability(rd * SECTOR / 1e9, P["steady"])
        mem["ci_gpu_read_gbps"] = block_boot_mean(rd * SECTOR / 1e9, 600, f"rd|{c.name}")
    # byte model on decode-only steps, against the measured GPU traffic in those steps
    if len(st) and len(c.req):
        dec = st[~st.prefill & st.busy.notna() & (st["gr"] > 0)]
        if len(dec):
            B, S = context_at(c, dec.t.values)
            mb = model_bytes(B, S, MB)
            dur = dec.busy.values
            tot = sum(mb.values())
            mem["model_decode_steps"] = {"n_steps": len(dec), "B_mean": float(B.mean()), "S_mean": float(S.mean()),
                                         **{f"{k_}_gbps": float(v.sum() / dur.sum() / 1e9) for k_, v in mb.items()},
                                         "total_gbps": float(tot.sum() / dur.sum() / 1e9)}
            if rd is not None:
                # measured reads in decode-only steps: per-second series weighted by the decode-only time in that second
                dec_sec = spread(dec.t, dur, g)
                gbps = (rd + (wr if wr is not None else 0)) * SECTOR / 1e9
                have = np.isfinite(gbps)
                wsum = dec_sec[have].sum()
                if wsum:
                    meas = float(np.sum(gbps[have] * dec_sec[have]) / wsum)
                    mem["model_decode_steps"]["measured_gbps"] = meas
                    mem["model_decode_steps"]["measured_over_model"] = meas / mem["model_decode_steps"]["total_gbps"]
    # probe and meters
    pr = c.windows[c.windows.get("kind", pd.Series(dtype=str)) == "probe"] if len(c.windows) else pd.DataFrame()
    if len(pr):
        inw = pr[(pr.t0_ns >= c.t0) & (pr.t1_ns <= c.t1)]
        base = pr[pr.t1_ns <= c.info.get("marks", {}).get("baseline_end", {}).get("t", 0)]
        idle = float(base.probe_ns.median()) if len(base) else IDLE_PROBE_NS
        mem["probe"] = {"mean_ns": float(inw.probe_ns.mean()) if len(inw) else None,
                        "p50_ns": float(inw.probe_p50.median()) if len(inw) else None,
                        "p99_ns": float(inw.probe_p99.median()) if len(inw) else None,
                        "idle_ns": idle, "x_idle": float(inw.probe_ns.mean() / idle) if len(inw) else None}
        try:
            import analyze as exp02a                      # exp02's calibration (scripts/analyze.py)
            cal = pd.read_csv(C.EXP02 / "raw" / "tables" / "calib_meters.csv")
            inv = exp02a.Inverse(cal, "probe", "probe_ns")
            gq, sat = inv(mem["probe"]["mean_ns"]) if mem["probe"]["mean_ns"] else (None, None)
            mem["probe"]["exp02_equiv_gbps"], mem["probe"]["exp02_saturated"] = gq, sat
        except Exception as e:                            # noqa: BLE001
            mem["probe"]["exp02_equiv_error"] = repr(e)
    mem["meters"] = {"baseline": c.info.get("baseline_meters"), "coda": c.info.get("coda_meters")}
    tok = thr.get("all", {}).get("output_tok_s")
    if tok and mem.get("all", {}).get("total_gbps"):
        mem["gb_per_output_token"] = mem["all"]["total_gbps"] / tok
    out["memory"] = mem
    # NVML (S, X) and the step-based GPU time split
    if len(c.smp):
        sm = c.smp[(c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)]
        pw = per_second(sm.t_ns, sm.nv_power_w, g, "mean")
        for ph, m in P.items():
            if m.sum():
                gpu.setdefault(ph, {})["power_w"] = nmean(pw[m])
        gpu["nvml"] = {"util_gpu_mean": float(sm.nv_util_gpu.mean()), "power_w_mean": float(sm.nv_power_w.mean()),
                       "sm_mhz_mean": float(sm.nv_sm_mhz.mean()), "throttle_share": float((sm.nv_reasons.fillna(0) != 0).mean()),
                       "proc_mem_gib": float(sm.nv_proc_mem_b.median() / 2**30)}
    if len(c.extra):
        x = c.extra[(c.extra.t >= c.t0) & (c.extra.t < c.t1)]
        en = [v.get("energy_mj") for v in x.nv if isinstance(v, dict) and v.get("energy_mj") is not None]
        if len(en) >= 2 and tok:
            joules = (en[-1] - en[0]) / 1000
            gpu["energy_kj"] = joules / 1000
            gpu["energy_kj_per_1k_tokens"] = joules / 1000 / (tok * c.secs / 1000)
            gpu["tokens_per_joule"] = tok * c.secs / joules
        temps = [v.get("temp_c") for v in x.nv if isinstance(v, dict) and v.get("temp_c") is not None]
        gpu["temp_c_max"] = max(temps) if temps else None
    if len(st):
        busy = st.busy.fillna(0)
        gpu["time_split"] = {"decode_steps": float(busy[~st.prefill].sum() / c.secs),
                             "prefill_steps": float(busy[st.prefill].sum() / c.secs),
                             "idle": float(max(0.0, 1 - busy.sum() / c.secs))}
    out["gpu"] = gpu
    # ---- 6. CPU
    cpu = {}
    if len(c.smp):
        sm = c.smp[(c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)]
        busy = sm[[f"busy{i}" for i in range(20)]].astype(float)
        cpu["busy_cores_mean"] = float(busy.sum(axis=1).mean())
        cpu["utilization_pct"] = cpu["busy_cores_mean"] / 20 * 100
        cpu["busy_cores_x925"] = float(busy[[f"busy{i}" for i in C.X925]].sum(axis=1).mean())
        cpu["busy_cores_a725"] = float(busy[[f"busy{i}" for i in C.A725]].sum(axis=1).mean())
        cpu["per_core_busy"] = {i: float(busy[f"busy{i}"].mean()) for i in range(20)}
        for p, mm in P.items():
            if mm.sum():
                bc = per_second(sm.t_ns, busy.sum(axis=1), g, "mean")
                cpu.setdefault("busy_cores_by_period", {})[p] = float(np.nanmean(bc[mm]))
    if len(c.extra) >= 2:
        x = c.extra[(c.extra.t >= c.t0) & (c.extra.t < c.t1)]
        if len(x) >= 2:
            a0, a1 = x.iloc[0], x.iloc[-1]
            dt = (a1.t - a0.t) / 1e9
            groups = {}
            for key in ("vllm_api", "vllm_engine", "vllm_other", "harbor", "child", "docker_cli", "containers",
                        "dockerd", "containerd", "measurement"):
                v0, v1 = a0.cpu.get(key), a1.cpu.get(key)
                if v0 is not None and v1 is not None:
                    groups[key] = (v1 - v0) / dt
            if "busy_cores_mean" in cpu:
                groups["other"] = cpu["busy_cores_mean"] - sum(groups.values())
            cpu["cores_by_group"] = groups
            p0, p1 = a0.psi.get("cpu", {}), a1.psi.get("cpu", {})
            cpu["psi_cpu_some_pct"] = (p1.get("some", 0) - p0.get("some", 0)) / 1e6 / dt * 100
            hot = [h[0][0] for h in x.hot if isinstance(h, list) and h]
            cpu["hottest_vllm_thread"] = {"mean": float(np.mean(hot)) if hot else None, "max": max(hot) if hot else None}
            if tok and "vllm_api" in groups:
                vllm = sum(groups.get(k_, 0) for k_ in ("vllm_api", "vllm_engine", "vllm_other"))
                cpu["vllm_cpu_s_per_1k_tokens"] = vllm / tok * 1000
            if len(k):
                lead_n = int((k.in_window & (k.purpose == "lead")).sum())
                if lead_n:
                    cpu["tool_cpu_s_per_lead_call"] = groups.get("containers", 0) * c.secs / lead_n
    if len(pf):
        by = {}
        for grp, cores in C.CORE_GROUPS.items():
            sel = pf[pf.cpu.isin(cores)]
            ev = sel.groupby("event").value.sum()
            cyc = ev.get("cpu_cycles", 0)
            if cyc:
                by[grp] = {"ipc": float(ev.get("inst_retired", 0) / cyc),
                           "mem_stall_share": float(ev.get("stall_backend_mem", 0) / cyc)}
                if len(c.smp):
                    sm = c.smp[(c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)]
                    busy_s = float(sm[[f"busy{i}" for i in cores]].astype(float).sum(axis=1).mean() * c.secs)
                    by[grp]["effective_ghz"] = float(cyc / busy_s / 1e9) if busy_s else None
        cpu["counters_by_group"] = by
        cpu["perf_full_counting"] = bool((pf.run_pct.dropna() >= 99.9).all())
    out["cpu"] = cpu
    # ---- 7. memory capacity, thermal, disk
    cap = {}
    if len(c.smp):
        sm = c.smp[(c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)]
        cap.update(mem_available_min_gib=float(sm.mem_MemAvailable.min() / 2**30),
                   page_cache_gib=float(sm.mem_Cached.mean() / 2**30), anon_gib=float(sm.mem_AnonPages.mean() / 2**30),
                   shmem_gib=float(sm.mem_Shmem.mean() / 2**30), vllm_rss_gib=float(sm.vllm_rss_b.mean() / 2**30),
                   swap_used_gib=float((16 * 2**30 - sm.mem_SwapFree.min()) / 2**30) if "mem_SwapFree" in sm else None)
    if len(c.extra) >= 2:
        x = c.extra[(c.extra.t >= c.t0) & (c.extra.t < c.t1)]
        if len(x) >= 2:
            a0, a1 = x.iloc[0], x.iloc[-1]
            dt = (a1.t - a0.t) / 1e9
            cap["swap_in_mb_s"] = (a1.sys.get("pswpin", 0) - a0.sys.get("pswpin", 0)) * 4096 / 1e6 / dt
            cap["swap_out_mb_s"] = (a1.sys.get("pswpout", 0) - a0.sys.get("pswpout", 0)) * 4096 / 1e6 / dt
            pm0, pm1 = a0.psi.get("memory", {}), a1.psi.get("memory", {})
            cap["psi_memory_some_pct"] = (pm1.get("some", 0) - pm0.get("some", 0)) / 1e6 / dt * 100
            cap["containers_mem_gib_max"] = float(max(v.get("containers_b", 0) for v in x["mem"]) / 2**30)
            cap["soc_temp_c_max"] = max(v.get("max") or 0 for v in x.therm) / 1000
            if a0.disk and a1.disk:
                cap["disk_read_mb_s"] = (a1.disk[0] - a0.disk[0]) / 1e6 / dt
                cap["disk_write_mb_s"] = (a1.disk[1] - a0.disk[1]) / 1e6 / dt
    out["capacity"] = cap
    # time series for the per-cell page: 30 s means
    ser = {"period": g.period.tolist()[::30], "steady": [bool(v) for v in g.steady.tolist()[::30]]}
    def bins(x):
        x = np.asarray(x, dtype=float)
        n = len(x) // 30
        return [None if np.isnan(v) else round(float(v), 4) for v in
                (np.nanmean(x[: n * 30].reshape(n, 30), axis=1) if n else [])]
    if len(st):
        ser["output_tok_s"] = bins(per_second(st.t, st.ng, g))
        ser["kv_pinned"] = bins(per_second(st.t, st.kv, g, "mean"))
        ser["running"] = bins(per_second(st.t, st.run, g, "mean"))
        ser["prefill_time_share"] = bins(spread(st.t[st.prefill], st.busy[st.prefill], g))
    if rd is not None:
        ser["gpu_read_gbps"] = bins(rd * SECTOR / 1e9)
    if tensor is not None:
        ser["tensor_active_pct"] = bins(tensor)
    if len(c.smp):
        sm = c.smp[(c.smp.t_ns >= c.t0) & (c.smp.t_ns < c.t1)]
        ser["busy_cores"] = bins(per_second(sm.t_ns, sm[[f"busy{i}" for i in range(20)]].astype(float).sum(axis=1), g, "mean"))
        ser["power_w"] = bins(per_second(sm.t_ns, sm.nv_power_w, g, "mean"))
    pr2 = c.windows[c.windows.get("kind", pd.Series(dtype=str)) == "probe"] if len(c.windows) else pd.DataFrame()
    if len(pr2):
        ser["probe_ns"] = bins(per_second(pr2.t0_ns, pr2.probe_ns, g, "mean"))
    out["_series"] = ser
    out["gates"] = gates(c, k, st)
    # sessions
    if len(c.sess):
        f = c.sess[c.sess["first"]]
        out["sessions"] = {"first": len(f), "total_started": len(c.sess),
                           "first_snipped": int(f.snip_mono.notna().sum()),
                           "first_lead_calls_mean": float(f.lead.dropna().mean()) if f.lead.notna().any() else None,
                           "first_status": f.status.value_counts().to_dict(),
                           "snip_at_lead_call_median": float(f.snip_lead.dropna().median()) if f.snip_lead.notna().any() else None}
    return out


# ---------------------------------------------------------------------------------------------------------------------
# rendering
def fmt(v, nd=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def get(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return default
        d = d[p]
    return d


def render(results: list[dict]) -> str:
    out = ["# Topic 24 tables", "", "Generated by `sp_analyze.py`. Every number is also in `sp.json`. Periods: all = the "
           "whole window; append / snip = seconds when at least 80% of the active sessions were in that phase; steady = "
           "from the second when half of the active sessions have been trimmed.", "",
           "Models: " + "; ".join(f"`{k}` = {v['label']} (`{v['repo']}`)" for k, v in C.VARIANTS.items()) + ".", ""]
    out += ["## Models as served", "", "| model | N | weights read per step GB | resident GB | loaded GiB | KV pool | "
            "KV tokens | weights load s |", "|" + "---|" * 8]
    for r in results:
        md = r.get("model") or {}
        one = lambda v: (v[0] if isinstance(v, list) and v else v)      # noqa: E731
        out.append("| " + " | ".join([r["variant"], str(r["n"]), fmt(md.get("w_step_gb"), 2), fmt(md.get("resident_gb"), 2),
                                      str(one(md.get("model_gib"))), str(r.get("pool")), str(one(md.get("kv_tokens"))),
                                      str(one(md.get("weights_load_s")))]) + " |")
    out += [""]
    out += ["## Serving (headline)", "",
            "| model | N | window h | output tok/s | requests/h | call latency mean s | TTFT mean s | TPOT p50 ms | "
            "recomputed tok/call | hit rate | cache loss | KV pinned |", "|" + "---|" * 12]
    for r in results:
        out.append("| " + " | ".join([
            r["variant"], str(r["n"]), fmt(r["window_h"], 2), fmt(get(r, "throughput", "all", "output_tok_s")),
            fmt(get(r, "throughput", "all", "requests_h"), 0), fmt(get(r, "latency", "all", "call_latency_s", "mean")),
            fmt(get(r, "latency", "all", "ttft_s", "mean"), 2), fmt(get(r, "latency", "all", "tpot_ms", "p50"), 0),
            fmt(get(r, "cache", "all", "recomputed_tok_per_call", "mean"), 0),
            fmt((get(r, "cache", "all", "hit_rate") or 0) * 100, 0) + "%",
            fmt((get(r, "cache", "cache_loss", "share") or 0) * 100, 0) + "%",
            fmt((get(r, "cache", "all", "kv_pinned_mean") or 0) * 100, 0) + "%"]) + " |")
    out += ["", "## Hardware (headline)", "",
            "| model | N | DRAM total GB/s (% peak) | GPU read / write GB/s | CPU read GB/s | memory latency × idle | "
            "tensor active % | TFLOPS | SM issue % | GPU W | kJ / 1k tok | busy cores | CPU mem-stall % (agents) | "
            "MemAvailable min GiB |", "|" + "---|" * 14]
    for r in results:
        m = get(r, "memory", "all") or {}
        out.append("| " + " | ".join([
            r["variant"], str(r["n"]), f"{fmt(m.get('total_gbps'))} ({fmt(m.get('total_pct_peak'), 0)}%)",
            f"{fmt(m.get('gpu_read_gbps'))} / {fmt(m.get('gpu_write_gbps'))}", fmt(m.get("cpu_read_gbps")),
            fmt(get(r, "memory", "probe", "x_idle"), 2), fmt(get(r, "gpu", "all", "tensor_active_pct")),
            fmt(get(r, "gpu", "all", "achieved_tflops")), fmt(get(r, "gpu", "all", "sm_issue_pct")),
            fmt(get(r, "gpu", "nvml", "power_w_mean")), fmt(get(r, "gpu", "energy_kj_per_1k_tokens"), 2),
            fmt(get(r, "cpu", "busy_cores_mean"), 2),
            fmt((get(r, "cpu", "counters_by_group", "agents", "mem_stall_share") or float("nan")) * 100),
            fmt(get(r, "capacity", "mem_available_min_gib"))]) + " |")
    out += ["", "## By period of the window", "", "append = at least 80% of the active sessions not yet trimmed; steady = "
            "from the second when half of them have been trimmed (finished sessions are replaced by fresh ones, so it "
            "stays a mix).", "",
            "| model | N | period | seconds | output tok/s | computed prompt tok/s | call latency mean s | TTFT mean s | "
            "TPOT p50 ms | recomputed tok/call | KV pinned | GPU read GB/s | DRAM total GB/s | tensor active % | TFLOPS | "
            "GPU W |", "|" + "---|" * 16]
    for r in results:
        for ph in ("append", "steady"):
            out.append("| " + " | ".join([
                r["variant"], str(r["n"]), ph, str(get(r, "periods_s", ph)), fmt(get(r, "throughput", ph, "output_tok_s")),
                fmt(get(r, "throughput", ph, "computed_prompt_tok_s"), 0),
                fmt(get(r, "latency", ph, "call_latency_s", "mean")), fmt(get(r, "latency", ph, "ttft_s", "mean"), 2),
                fmt(get(r, "latency", ph, "tpot_ms", "p50"), 0),
                fmt(get(r, "cache", ph, "recomputed_tok_per_call", "mean"), 0),
                fmt((get(r, "cache", ph, "kv_pinned_mean") or float("nan")) * 100, 0) + "%",
                fmt(get(r, "memory", ph, "gpu_read_gbps")), fmt(get(r, "memory", ph, "total_gbps")),
                fmt(get(r, "gpu", ph, "tensor_active_pct")), fmt(get(r, "gpu", ph, "achieved_tflops")),
                fmt(get(r, "gpu", ph, "power_w"))]) + " |")
    out += ["", "## By call phase (first sessions' calls)", "", "append = before the session's first trim; snip = after it "
            "(the prompt changes near its start on every call, so most of it is recomputed).", "",
            "| model | N | phase | calls | call latency mean s | TTFT p50 s | TTFT mean s | prefill mean s | decode mean s | "
            "TPOT p50 ms | recomputed tok/call | hit rate |", "|" + "---|" * 12]
    for r in results:
        for ph in ("append", "snip"):
            out.append("| " + " | ".join([
                r["variant"], str(r["n"]), ph, str(get(r, "latency", ph, "call_latency_s", "n")),
                fmt(get(r, "latency", ph, "call_latency_s", "mean")), fmt(get(r, "latency", ph, "ttft_s", "p50"), 2),
                fmt(get(r, "latency", ph, "ttft_s", "mean"), 2), fmt(get(r, "latency", ph, "prefill_s", "mean"), 2),
                fmt(get(r, "latency", ph, "decode_s", "mean")), fmt(get(r, "latency", ph, "tpot_ms", "p50"), 0),
                fmt(get(r, "cache", ph, "recomputed_tok_per_call", "mean"), 0),
                fmt((get(r, "cache", ph, "hit_rate") or float("nan")) * 100, 0) + "%"]) + " |")
    out += ["", "## CPU cores by group", "", "| model | N | vLLM API | vLLM engine | harness (Harbor + s15) | docker CLI | tools "
            "(containers) | Docker daemons | measurement | other |", "|" + "---|" * 10]
    for r in results:
        gr = get(r, "cpu", "cores_by_group") or {}
        out.append("| " + " | ".join([r["variant"], str(r["n"])] + [fmt(v, 2) for v in (
            gr.get("vllm_api"), gr.get("vllm_engine"), (gr.get("harbor") or 0) + (gr.get("child") or 0),
            gr.get("docker_cli"), gr.get("containers"), (gr.get("dockerd") or 0) + (gr.get("containerd") or 0),
            gr.get("measurement"), gr.get("other"))]) + " |")
    out += ["", "## Byte model vs measured GPU traffic (decode-only steps)", "",
            "| model | N | steps | B mean | context S mean (tok) | weights | state | KV read | model total GB/s | measured GB/s | "
            "measured / model |", "|" + "---|" * 11]
    for r in results:
        md = get(r, "memory", "model_decode_steps") or {}
        out.append("| " + " | ".join([r["variant"], str(r["n"]), str(md.get("n_steps")), fmt(md.get("B_mean")), fmt(md.get("S_mean"), 0),
                                      fmt(md.get("weights_gbps")), fmt(md.get("state_gbps")), fmt(md.get("kv_read_gbps")),
                                      fmt(md.get("total_gbps")), fmt(md.get("measured_gbps")),
                                      fmt(md.get("measured_over_model"), 2)]) + " |")
    out += ["", "## Stability (steady period in thirds; stable = each third within 10% of their mean)", "",
            "| model | N | output tok/s | KV pinned | GPU read |", "|---|---|---|---|---|"]
    for r in results:
        def s(v):
            return "–" if not v else ("stable" if v["stable"] else "NOT stable") + " " + "/".join(f"{x:.3g}" for x in v["parts"])
        out.append(f"| {r['variant']} | {r['n']} | {s(get(r, 'throughput', 'stability_output_tok_s'))} | "
                   f"{s(get(r, 'cache', 'stability_kv_pinned'))} | {s(get(r, 'memory', 'stability_gpu_read'))} |")
    out += compare(results)
    out += ["", "## Data-quality gates", "", "| model | N | cell | gate | value | need | ok |", "|---|---|---|---|---|---|---|"]
    for r in results:
        for name, v in (r.get("gates") or {}).items():
            if isinstance(v, dict):
                out.append(f"| {r['variant']} | {r['n']} | {r['cell']} | {name} | {v['value']} | {v['need']} | "
                           f"{'yes' if v['ok'] else '**NO**'} |")
    return "\n".join(out) + "\n"


COMPARE = [  # label, path into a result, decimals
    ("output tok/s (steady)", ("throughput", "steady", "output_tok_s"), 1),
    ("output tok/s (whole window)", ("throughput", "all", "output_tok_s"), 1),
    ("decode tok/s per user (1 / TPOT p50)", ("throughput", "per_user_decode_tok_s"), 2),
    ("TPOT p50 ms", ("latency", "all", "tpot_ms", "p50"), 0),
    ("TTFT mean s", ("latency", "all", "ttft_s", "mean"), 2),
    ("call latency mean s", ("latency", "all", "call_latency_s", "mean"), 1),
    ("output tokens per call", ("cache", "all", "output_tok_per_call", "mean"), 0),
    ("prompt tokens recomputed per call", ("cache", "all", "recomputed_tok_per_call", "mean"), 0),
    ("KV pool pinned", ("cache", "all", "kv_pinned_mean"), 3),
    ("cache loss share", ("cache", "cache_loss", "share"), 3),
    ("weights read per step GB", ("model", "w_step_gb"), 2),
    ("GPU read GB/s", ("memory", "all", "gpu_read_gbps"), 1),
    ("GPU read GB/s, decode-only seconds", ("gpu", "by_step_type", "decode", "gpu_read_gbps"), 1),
    ("DRAM total GB/s", ("memory", "all", "total_gbps"), 1),
    ("DRAM GB per output token", ("memory", "gb_per_output_token"), 2),
    ("memory latency x idle", ("memory", "probe", "x_idle"), 2),
    ("tensor active %", ("gpu", "all", "tensor_active_pct"), 1),
    ("tensor instructions/s (1e9)", ("gpu", "all", "tensor_insts_per_s"), 2),
    ("workload TFLOPS (linear layers)", ("throughput", "all", "workload_tflops"), 1),
    ("GPU W", ("gpu", "nvml", "power_w_mean"), 1),
    ("GPU kJ per 1k output tokens", ("gpu", "energy_kj_per_1k_tokens"), 2),
    ("busy CPU cores", ("cpu", "busy_cores_mean"), 2),
    ("MemAvailable min GiB", ("capacity", "mem_available_min_gib"), 1),
]


def compare(results: list[dict]) -> list[str]:
    """Each quantized cell against the BF16 cell with the same number of users."""
    out = ["", "## Against BF16 at the same number of users", "",
           "| N | metric | " + " | ".join(C.VARIANT_ORDER) + " | " +
           " | ".join(f"{v} / bf16" for v in C.VARIANT_ORDER[1:]) + " |", "|" + "---|" * (2 + 2 * len(C.VARIANT_ORDER) - 1)]
    for n in sorted({r["n"] for r in results}):
        by = {}
        for r in results:
            if r["n"] == n and r["variant"] not in by:
                by[r["variant"]] = r
        if len(by) < 2:
            continue
        for label, path, nd in COMPARE:
            vals = {v: get(by[v], *path) if v in by else None for v in C.VARIANT_ORDER}
            if all(x is None for x in vals.values()):
                continue
            scale = 1e-9 if "1e9" in label else 1.0
            cells = [fmt(vals[v] * scale, nd) if isinstance(vals[v], (int, float)) else "–" for v in C.VARIANT_ORDER]
            base = vals["bf16"]
            ratios = [f"{vals[v] / base:.2f}" if isinstance(vals[v], (int, float)) and isinstance(base, (int, float))
                      and base else "–" for v in C.VARIANT_ORDER[1:]]
            out.append(f"| {n} | {label} | " + " | ".join(cells) + " | " + " | ".join(ratios) + " |")
    return out


def checked_tensor_scale() -> float | None:
    """FLOPs per tensor-pipe instruction, from the newest counter check (the BF16 matrix-multiply load)."""
    for d in sorted((C.RUNS / "checks").glob("counters_*"), reverse=True):
        try:
            rows = json.loads((d / "compare.json").read_text())["rows"]
        except (OSError, ValueError, KeyError):
            continue
        for r in rows:
            if r.get("check") == "tensor" and r.get("scale"):
                return float(r["scale"])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", nargs="*", default=None)
    ap.add_argument("--cpu-read-event", default="l2d_cache_refill", help="set by the counter check (S1a)")
    ap.add_argument("--tensor-scale", type=float, default=None, help="FLOPs per counted tensor op (S1a)")
    ap.add_argument("--reuse", action="store_true",
                    help="take a cell's result from data/tables/cells/<cell>.json if it is there (a finished cell "
                         "does not change; analysing one takes minutes and gigabytes, which disturbs a running cell)")
    a = ap.parse_args()
    def finished(n):                       # without --cells: only cells that have ended (never a running one)
        try:
            return json.loads((C.RUNS / n / "cell.json").read_text()).get("status") == "done"   # not failed ones
        except (OSError, ValueError):
            return False
    names = a.cells or [n for n in cell_order() if n != "smoke" and finished(n)]
    results = []
    cache = C.TABLES / "cells"
    cache.mkdir(parents=True, exist_ok=True)
    for n in names:
        cf = cache / f"{n}.json"
        if a.reuse and cf.exists():
            r = json.loads(cf.read_text())
        else:
            c = Cell(n)
            r = analyze(c, a.cpu_read_event, a.tensor_scale or checked_tensor_scale())
            (C.TABLES / f"series_{n}.json").write_text(json.dumps(r.pop("_series", {})))
            cf.write_text(json.dumps(r, default=lambda v: None))
            r = json.loads(cf.read_text())
        results.append(r)
        print(f"{n}: {r['variant']}, N={r['n']}, {r['window_h']:.2f} h, out tok/s "
              f"{get(r, 'throughput', 'all', 'output_tok_s')}")
    results.sort(key=lambda r: (r["n"], C.VARIANT_ORDER.index(r["variant"]), r["cell"]))
    C.TABLES.mkdir(parents=True, exist_ok=True)
    tag = "" if a.cells is None else "_" + "_".join(names)
    (C.TABLES / f"sp{tag}.json").write_text(json.dumps(results, indent=1, default=lambda v: None))
    (C.TABLES / f"sp{tag}.md").write_text(render(results))
    print(f"written {C.TABLES / f'sp{tag}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
