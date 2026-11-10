#!/usr/bin/env python3
"""Topic 23's analysis. Per run: request latency, entry (task) latency and vLLM throughput as topic 22 measured them,
plus what explains them; then every setup against `none` (and `none_r2` - `none`, the noise yardstick).

Inputs per run (data/jobs/<run>/, data/runs/<run>/):
  - every trial: config.json, result.json, agent/s15/calls.jsonl (`ms`, usage, the tools a reply called, `purpose`:
    lead / save / ...), agent/s15/wb_t0.json, agent/s15/traces (each wait's real duration), agent/s15/et_limit.json;
  - hw_*.jsonl: et_hw.py's records once a second (vLLM /metrics, NVML and GPM, LMCache, the gate's state);
  - gate_events.jsonl: the gate's slots (each "on" with the seconds the session waited) and K changes;
  - controller.jsonl (agate): every controller round.
Definitions (topic 22's, with what the gate adds):
  - full-N window: from the first moment 32 agents run until the last task's agent starts (Harbor's agent phases);
  - request latency: a model call's `ms` as the agent sees it, gate waits included (the server-side time is vLLM's
    e2e histogram); working calls: not wait-only, not a save_context re-send;
  - entry e2e: agent_execution finished - started; active time: e2e minus the session's gate-held seconds;
  - throughput: counter deltas over the window (requests/h, tokens/s, generated tokens/s), plus working requests/h
    (client side), tasks finished per hour in the window, and the makespan.
Intervals: 95 % bootstrap, 2,000 draws; requests clustered by task, entries paired by task, throughput by 10-min blocks.

    python research/23_efficiency_tools/et_analyze.py pilot      # the S1 decision point
    python research/23_efficiency_tools/et_analyze.py main       # data/tables/et.{md,json}
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent / "22_wait_bench"))
import wb_analyze as A  # noqa: E402

DATA = DIR / "data"
JOBS = DATA / "jobs"
RUNS = DATA / "runs"
TABLES = DATA / "tables"
N = 32
KV_RULE = 0.8
LINE = re.compile(r"\[server\] KV cache (\d+)% full")
PAIRS = {"P1": ("none", "lmc_all"), "P2": ("wait", "lmc"), "P3": ("gate8", "agate"), "P4": ("gate16", "none_r2")}
SETUPS = ("none", "none_r2", "gate8", "gate16", "agate", "wait", "lmc", "lmc_all")


def bench_map() -> dict[str, str]:
    out = {t["id"]: t["bench"] for t in json.loads((DIR.parent / "22_wait_bench/data/tasks.json").read_text())["main"]}
    pilot = DATA / "tasks_pilot.json"
    if pilot.exists():
        out.update({t["id"]: t["bench"] for t in json.loads(pilot.read_text())["pilot"]})
    return out


def load_hw(name: str) -> list[list[dict]]:
    """One segment per monitor file (one server), each sorted by time; wb_analyze's rates() reads these."""
    out = []
    for path in sorted((RUNS / name).glob("hw_*.jsonl")):
        seg = [r for r in A.read_jsonl(path) if r.get("ok") and r.get("m")]
        if seg:
            out.append(sorted(seg, key=lambda r: r["wall"]))
    return out


def call_meta(trial: Path) -> list[dict]:
    """Per answered call, in calls.jsonl order: purpose, max_tokens, the tools its reply called, and the KV the newest
    [server] line of its request showed (None without a line)."""
    out = []
    for c in A.read_jsonl(trial / "agent" / "s15" / "calls.jsonl"):
        if c.get("refused") or c.get("error") or c.get("ms") is None:
            continue
        kv = None
        req = c.get("request") or {}
        found = LINE.findall(json.dumps(req.get("messages") or [])[-6000:])
        if found:
            kv = int(found[-1]) / 100.0
        out.append({"purpose": c.get("purpose"), "max_tokens": c.get("max_tokens"), "tools": c.get("tools") or [],
                    "kv_seen": kv})
    return out


def load(name: str) -> list[dict]:
    """wb_analyze's trial rows, each call marked: save (a save_context re-send), with_save, kv_seen."""
    trials = A.load_trials(JOBS / name, bench_map())
    for t in trials:
        meta = call_meta(JOBS / name / t["trial"])
        if len(meta) == len(t["calls"]):
            for c, m in zip(t["calls"], meta):
                c["save"] = m["purpose"] == "save"
                c["with_save"] = "save_context" in m["tools"]
                c["kv_seen"] = m["kv_seen"]
                c["purpose"] = m["purpose"]
                c["working"] = not c["save"] and not c["wait_only"] and not (
                    m["tools"] and set(m["tools"]) <= {"wait", "save_context"})
        else:
            for c in t["calls"]:
                c.update(save=False, with_save=False, kv_seen=None, purpose=None, working=not c["wait_only"])
    return trials


def gate_held(name: str) -> dict[str, float]:
    held: dict[str, float] = {}
    for e in A.read_jsonl(RUNS / name / "gate_events.jsonl"):
        if e.get("k") == "gate" and e.get("on") == 1:
            held[e["s"]] = held.get(e["s"], 0.0) + float(e.get("waited") or 0.0)
    return held


def pilot() -> int:
    """S1's decision point: is there KV pressure at N=32 (pilot_none's first 60 min, K=32)? And the run-time
    projection, and a first look at the live wait rule (pilot_wait)."""
    out = {}
    for name in ("pilot_none", "pilot_wait"):
        if not (JOBS / name).is_dir():
            continue
        trials = load(name)
        segs = load_hw(name)
        if not segs:
            continue
        t0 = segs[0][0]["wall"]
        lo, hi = t0 + 600, t0 + 3600                      # skip the first 10 min (the ramp)
        kv = A.kv_profile(segs, lo, hi)
        thr = A.rates(segs, lo, hi) or {}
        done = [t for t in trials if t["e2e"] is not None]
        calls = [c for t in trials for c in t["calls"]]
        on = [c for c in calls if c["lead"] and c["kv_seen"] is not None and c["kv_seen"] >= KV_RULE]
        off = [c for c in calls if c["lead"] and c["kv_seen"] is not None and c["kv_seen"] < KV_RULE]
        out[name] = {
            "kv_share_at_80": kv.get("kv_share_at_rule"), "kv_mean": kv.get("kv_mean"),
            "running_mean": kv.get("running_mean"), "waiting_mean": kv.get("waiting_mean"),
            "prefix_hit_rate": thr.get("prefix_hit_rate"), "preemptions_per_min": thr.get("preemptions_per_min"),
            "requests_per_h": thr.get("requests_per_h"), "gen_tok_s": thr.get("gen_tokens_per_s"),
            "trials_done": len(done), "e2e_p50_s": A.pct([t["e2e"] for t in done], 50),
            "e2e_mean_s": (sum(t["e2e"] for t in done) / len(done)) if done else None,
            "limit_hits": sum(t["limit"] for t in trials), "calls": len(calls),
            "out_tokens_per_call": (sum(c["out"] for c in calls) / len(calls)) if calls else None,
            "wait_on_rule": (sum(c["wait"] for c in on) / len(on)) if on else None, "n_on_rule": len(on),
            "wait_off_rule": (sum(c["wait"] for c in off) / len(off)) if off else None, "n_off_rule": len(off),
        }
    pn = out.get("pilot_none") or {}
    if pn:
        pn["pressure"] = bool((pn["kv_share_at_80"] or 0) >= 0.30 and (pn["prefix_hit_rate"] or 1) <= 0.80)
        if pn["e2e_mean_s"]:
            # 96 tasks over 32 agents: three rounds of the mean task time, plus a tail of one limit-length task
            pn["projected_run_h"] = (3 * pn["e2e_mean_s"] + 3600) / 3600
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "pilot.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return 0


def gpm_means(segs: list[list[dict]], lo: float, hi: float) -> dict:
    """Means over the window's samples. NVML sometimes returns a sample as NaN (HBM bandwidth: 2 of `none`'s 11,406
    records and 1 of `lmc_all`'s 9,977), and a NaN would make the whole mean NaN, so those samples are left out."""
    keys = ("sm_util", "sm_occupancy", "tensor_util", "dram_bw_util")
    rows = [r["gpu"][0].get("gpm") or {} for seg in segs for r in seg if lo <= r["wall"] <= hi and r.get("gpu")]
    out = {}
    for k in keys:
        v = [g[k] for g in rows if isinstance(g.get(k), (int, float)) and math.isfinite(g[k])]
        out[k] = (sum(v) / len(v)) if v else None
    pw = [r["gpu"][0].get("power_w") for seg in segs for r in seg if lo <= r["wall"] <= hi and r.get("gpu")]
    pw = [p for p in pw if p is not None]
    out["power_w"] = (sum(pw) / len(pw)) if pw else None
    lmc = [r.get("lmc") or {} for seg in segs for r in seg if lo <= r["wall"] <= hi and r.get("lmc")]
    out["lmc_l1_usage_mean"] = (sum(x.get("lmcache_mp_l1_usage_ratio", 0.0) for x in lmc) / len(lmc)) if lmc else None
    return out


def source_rates(segs: list[list[dict]], lo: float, hi: float) -> dict:
    """Prompt tokens by source over the window: computed, GPU prefix-cache hits, offload (external) hits."""
    keys = {"computed": "vllm:prompt_tokens_by_source_total{local_compute}",
            "gpu_hit": "vllm:prompt_tokens_by_source_total{local_cache_hit}",
            "offload_hit": "vllm:prompt_tokens_by_source_total{external_kv_transfer}"}
    dt, d = 0.0, {k: 0.0 for k in keys}
    for seg in segs:
        s_lo, s_hi = max(lo, seg[0]["wall"]), min(hi, seg[-1]["wall"])
        if s_hi <= s_lo:
            continue
        a, b = A.counter_at(seg, s_lo), A.counter_at(seg, s_hi)
        ra = next(r for r in seg if r["wall"] == a["wall"])["m"]
        rb = next(r for r in seg if r["wall"] == b["wall"])["m"]
        dt += b["wall"] - a["wall"]
        for k, name in keys.items():
            d[k] += float(rb.get(name) or 0.0) - float(ra.get(name) or 0.0)
    tot = sum(d.values())
    out = {f"{k}_tok_s": (v / dt if dt else None) for k, v in d.items()}
    out.update({f"{k}_share": (v / tot if tot else None) for k, v in d.items()})
    return out


def session_profile(segs: list[list[dict]], lo: float, hi: float) -> dict:
    """The gate's sessions over the window (et_hw's 1 Hz gate state): sessions holding a slot, sessions waiting, and
    the share of time more held a slot than K allowed (after `agate` lowered K)."""
    g = [r["gate"] for seg in segs for r in seg if lo <= r["wall"] <= hi and r.get("gate") and r["gate"].get("k")]
    if not g:
        return {}
    return {"active_mean": sum(x["active"] for x in g) / len(g), "waiting_mean": sum(x["waiting"] for x in g) / len(g),
            "over_k_share": sum(x["active"] > x["k"] for x in g) / len(g)}


def overhead(name: str) -> list[tuple[float, int, int]]:
    """The harness's own calls, which vLLM's counters include but which do no agent work: `save_context` re-sends (the
    proxy's store-flagged rows) and `agate`'s controller calls (controller.jsonl; they go to vLLM directly). Each
    answered call as (end time, prompt tokens, generated tokens). A call cut by its client (a controller call at its
    300-s limit, a 504 at the proxy's cap) is left out: vLLM's request counter does not count it (smoke_agate: two
    such controller calls, `request_success_total{abort}` 0), and its tokens are unknown."""
    out = []
    for r in A.read_jsonl(RUNS / name / "proxy_requests.jsonl"):
        if r.get("store") and r.get("status") == 200:
            out.append((float(r["t_done"]), int(r.get("prompt") or 0), int(r.get("out") or 0)))
    for r in A.read_jsonl(RUNS / name / "controller.jsonl"):
        u = r.get("usage") or {}
        if r.get("status") == 200:
            out.append((float(r["t"]) + float(r.get("s") or 0.0), int(u.get("prompt_tokens") or 0),
                        int(u.get("completion_tokens") or 0)))
    return out


def net_rates(segs: list[list[dict]], lo: float, hi: float, over: list[tuple[float, int, int]]) -> dict | None:
    """wb_analyze.rates over [lo, hi] minus the harness's own calls that ended in it: the agents' work at vLLM."""
    r = A.rates(segs, lo, hi)
    if not r:
        return r
    ev = [(p, g) for t, p, g in over if lo <= t <= hi]
    dt, p, g = r["seconds"], sum(x for x, _ in ev), sum(y for _, y in ev)
    out = dict(r, overhead_requests=len(ev))
    out["requests_per_h"] = r["requests_per_h"] - len(ev) / dt * 3600.0
    out["prompt_tokens_per_s"] = r["prompt_tokens_per_s"] - p / dt
    out["gen_tokens_per_s"] = r["gen_tokens_per_s"] - g / dt
    out["tokens_per_s"] = out["prompt_tokens_per_s"] + out["gen_tokens_per_s"]
    return out


def net_blocks(segs: list[list[dict]], lo: float, hi: float, over: list[tuple[float, int, int]]) -> list[dict]:
    out, t = [], lo
    while t + A.BLOCK_S <= hi + 1e-6:
        r = net_rates(segs, t, t + A.BLOCK_S, over)
        if r:
            out.append(r)
        t += A.BLOCK_S
    return out


def k_profile(name: str, lo: float, hi: float) -> dict | None:
    """agate (and the gates): the time-weighted K over the window, its range, and the number of changes."""
    events = [e for e in A.read_jsonl(RUNS / name / "gate_events.jsonl") if e.get("k") == "k"]
    cell = (A.read_jsonl(RUNS / name / "cell.jsonl") or [{}])[0]
    k0 = cell.get("gate_k")
    if k0 is None:
        return None
    steps, k, t = [], k0, lo
    for e in sorted(events, key=lambda e: e["t"]):
        if e["t"] <= lo:
            k = e["new"]
            continue
        if e["t"] >= hi:
            break
        steps.append((e["t"] - t, k))
        t, k = e["t"], e["new"]
    steps.append((hi - t, k))
    span = sum(d for d, _ in steps) or 1.0
    ks = [k for _, k in steps]
    return {"k_mean": sum(d * k for d, k in steps) / span, "k_min": min(ks), "k_max": max(ks),
            "changes": sum(1 for e in events if lo < e["t"] < hi)}


def run(name: str) -> dict:
    cell = (A.read_jsonl(RUNS / name / "cell.jsonl") or [{}])[-1]
    trials = load(name)
    win = A.window(trials, N)
    segs = load_hw(name)
    held = gate_held(name)
    in_win = lambda c: win.get("w0") is not None and win["w0"] <= c["t"] <= win["w1"]
    req = {}
    for key, keep in (("all", in_win), ("working", lambda c: in_win(c) and c["working"])):
        groups = {t["id"]: [c["ms"] / 1000.0 for c in t["calls"] if keep(c)] for t in trials}
        groups = {k: v for k, v in groups.items() if v}
        req[key] = {**A.summary([v for g in groups.values() for v in g]),
                    "ci": A.boot_clustered(groups, f"req|{name}|{key}") if groups else {}}
    e2e = {t["id"]: [t["e2e"]] for t in trials if t["e2e"] is not None}
    active = {t["id"]: [t["e2e"] - held.get(t["trial"], 0.0)] for t in trials if t["e2e"] is not None}
    lo, hi = win.get("w0"), win.get("w1")
    over = overhead(name)
    thr = net_rates(segs, lo, hi, over) if lo else None
    gross = A.rates(segs, lo, hi) if lo else None
    blocks = net_blocks(segs, lo, hi, over) if lo else []
    hours = (hi - lo) / 3600 if lo else None
    calls_w = [c for t in trials for c in t["calls"] if in_win(c)]
    finished = [t for t in trials if t["a1"] is not None and lo is not None and lo <= t["a1"] <= hi]
    lead = [c for t in trials for c in t["calls"] if c["lead"]]
    on = [c for c in lead if c.get("kv_seen") is not None and c["kv_seen"] >= KV_RULE]
    off = [c for c in lead if c.get("kv_seen") is not None and c["kv_seen"] < KV_RULE]
    after = {"wait": [], "save": [], "other": []}
    for t in trials:
        ls = sorted((c for c in t["calls"] if c["lead"]), key=lambda c: c["t"])
        for prev, c in zip(ls, ls[1:]):
            k = "save" if prev.get("with_save") else ("wait" if prev["wait"] else "other")
            after[k].append((c["cached"], c["prompt"]))
    hit = {k: (sum(x for x, _ in v) / sum(y for _, y in v)) if v and sum(y for _, y in v) else None
           for k, v in after.items()}
    return {
        "name": name, "setup": cell.get("setup"), "gpu": cell.get("gpu"), "trials": len(trials),
        "window": {k: win.get(k) for k in ("minutes", "run_minutes", "agents_in_window", "reached_n")},
        "request": req,
        "entry": {**A.summary([v[0] for v in e2e.values()]), "ci": A.boot_clustered(e2e, f"e2e|{name}") if e2e else {}},
        "active": A.summary([v[0] for v in active.values()]),
        "gate_held_s_per_task": (sum(held.values()) / len(trials)) if trials else None,
        "throughput": thr, "throughput_gross": gross, "blocks": len(blocks), "_blocks": blocks,
        "working_requests_per_h": (sum(c["working"] for c in calls_w) / hours) if hours else None,
        "tasks_per_h": (len(finished) / hours) if hours else None,
        "makespan_min": win.get("run_minutes"),
        "sources": source_rates(segs, lo, hi) if lo else {}, "kv": A.kv_profile(segs, lo, hi) if lo else {},
        "util": gpm_means(segs, lo, hi) if lo else {}, "k": k_profile(name, lo, hi) if lo else None,
        "sessions": session_profile(segs, lo, hi) if lo else {},
        "pass_rate": (sum((t["reward"] or 0.0) for t in trials) / len(trials)) if trials else None,
        "limit_hits": sum(t["limit"] for t in trials),
        "decisions": {"waits_per_task": (sum(c["wait"] for t in trials for c in t["calls"]) / len(trials)) if trials else None,
                      "saves_per_task": (sum(c["save"] for t in trials for c in t["calls"]) / len(trials)) if trials else None,
                      "wait_s_per_task": (sum(t["wait_s"] for t in trials) / len(trials)) if trials else None,
                      "wait_only_share": (sum(c["wait_only"] for c in calls_w) / len(calls_w)) if calls_w else None,
                      "wait_on_rule": (sum(c["wait"] for c in on) / len(on)) if on else None,
                      "wait_off_rule": (sum(c["wait"] for c in off) / len(off)) if off else None,
                      "save_on_rule": (sum(c.get("with_save", False) for c in on) / len(on)) if on else None,
                      "save_off_rule": (sum(c.get("with_save", False) for c in off) / len(off)) if off else None,
                      "hit_after": hit},
        "recomputed_tokens_per_task": (sum(c["prompt"] - c["cached"] for t in trials for c in t["calls"]) / len(trials))
        if trials else None,
    }


def valid(name: str, n_tasks: int = 96) -> dict:
    """The pre-registered validity of a run (prereg.md):
      - every task reaches an end state (verifier reward, after a normal end or the time limit); anything else counts
        as an infrastructure error, which must stay under 2 %;
      - the proxy's answered calls (plus `agate`'s answered controller calls, which go to vLLM directly) match vLLM's
        request counter within 1 %, over each monitor file's span (calls that finished after the monitor stopped are
        left out; vLLM does not count a call its client cut, such as a 504 at the per-call cap);
      - the monitor covers the full-N window (≥ 0.95 records/s, no gap over 10 s inside one server's span);
      - the GPU was empty after every stage of the run."""
    trials = load(name)
    ids = {t["id"] for t in trials}
    ended = [t for t in trials if t["reward"] is not None and (t["exception"] is None or t["limit"])]
    infra = [t for t in trials if t not in ended]
    px = A.read_jsonl(RUNS / name / "proxy_requests.jsonl")
    ctl = [float(r["t"]) + float(r.get("s") or 0.0) for r in A.read_jsonl(RUNS / name / "controller.jsonl")
           if r.get("status") == 200]
    segs = load_hw(name)
    n_px = n_v = 0.0
    for seg in segs:
        lo, hi = seg[0]["wall"], seg[-1]["wall"]
        n_px += sum(1 for r in px if r.get("status") == 200 and r.get("t_done") and lo <= r["t_done"] <= hi)
        n_px += sum(1 for t in ctl if lo <= t <= hi)
        n_v += float(seg[-1]["m"].get("vllm:request_success_total") or 0.0) - float(
            seg[0]["m"].get("vllm:request_success_total") or 0.0)
    win = A.window(trials, N)
    lo, hi = win.get("w0"), win.get("w1")
    recs = [r["wall"] for seg in segs for r in seg if lo is not None and lo <= r["wall"] <= hi]
    gaps = [b["wall"] - a["wall"] for seg in segs for a, b in zip(seg, seg[1:]) if lo is not None and lo <= a["wall"] <= hi]
    stages = [r for r in A.read_jsonl(DATA / "gpu_hours.jsonl") if r.get("stage") == name]
    out = {
        "tasks": len(ids), "trials": len(trials), "done": sum(1 for t in ended if not t["limit"]),
        "at_limit": sum(1 for t in ended if t["limit"]),
        "infra": [(t["trial"], t["exception"], t["reward"]) for t in infra],
        "calls_proxy": int(n_px), "calls_vllm": int(n_v),
        "calls_diff_pct": (100.0 * (n_px - n_v) / n_v) if n_v else None,
        "monitor_per_s": (len(recs) / (hi - lo)) if recs and hi > lo else None,
        "monitor_max_gap_s": max(gaps) if gaps else None,
        "stages": len(stages), "gpu_empty_after": all(r.get("gpus_empty_after") for r in stages) if stages else None,
    }
    out["valid"] = bool(
        len(ids) == n_tasks and len(infra) <= 0.02 * n_tasks
        and out["calls_diff_pct"] is not None and abs(out["calls_diff_pct"]) <= 1.0
        and (out["monitor_per_s"] or 0) >= 0.95 and (out["monitor_max_gap_s"] or 99) <= 10.0
        and out["gpu_empty_after"])
    return out


def contrast(base: dict, other: dict, base_trials: list, other_trials: list, tag: str) -> dict:
    out = {}
    for key in ("all", "working"):
        keep = (lambda c: True) if key == "all" else (lambda c: c["working"])
        groups = []
        for r, trials in ((base, base_trials), (other, other_trials)):
            w0, w1 = r["_w"]
            groups.append({t["id"]: [c["ms"] / 1000.0 for c in t["calls"] if w0 <= c["t"] <= w1 and keep(c)]
                           for t in trials})
        ga, gb = ({k: v for k, v in g.items() if v} for g in groups)
        out[f"request_{key}"] = A.boot_paired(ga, gb, f"c|req|{key}|{tag}")
    ea = {t["id"]: [t["e2e"]] for t in base_trials if t["e2e"] is not None}
    eb = {t["id"]: [t["e2e"]] for t in other_trials if t["e2e"] is not None}
    out["entry"] = A.boot_paired(ea, eb, f"c|e2e|{tag}")
    import random
    rng = random.Random(f"thr|{tag}")
    for k in ("requests_per_h", "tokens_per_s", "gen_tokens_per_s"):
        ba, bb = base["_blocks"], other["_blocks"]
        if not ba or not bb:
            continue
        draws = []
        for _ in range(A.DRAWS):
            ma = sum(rng.choice(ba)[k] for _ in ba) / len(ba)
            mb = sum(rng.choice(bb)[k] for _ in bb) / len(bb)
            draws.append(mb / ma if ma else None)
        ra = (base["throughput"] or {}).get(k)
        rb = (other["throughput"] or {}).get(k)
        out[f"ratio_{k}"] = {"ratio": (rb / ra) if ra else None, "ci": A.ci(draws)}
    pa = {t["id"]: t["reward"] or 0.0 for t in base_trials}
    pb = {t["id"]: t["reward"] or 0.0 for t in other_trials}
    keys = sorted(set(pa) & set(pb))
    rng = random.Random(f"pass|{tag}")
    draws = []
    for _ in range(A.DRAWS if keys else 0):
        pick = [rng.choice(keys) for _ in keys]
        draws.append(sum(pb[k] - pa[k] for k in pick) / len(pick))
    out["pass_diff"] = {"diff": (sum(pb[k] - pa[k] for k in keys) / len(keys)) if keys else None, "ci": A.ci(draws)}
    return out


def main_tables(prefix: str = "", out: str = "et") -> int:
    """Every setup's run (named <prefix><setup>) against <prefix>none; the smoke runs use prefix smoke_."""
    runs, trials = {}, {}
    for setup in SETUPS:
        name = prefix + setup
        if (JOBS / name).is_dir() and (RUNS / name / "cell.jsonl").is_file():
            runs[setup] = run(name)
            trials[setup] = load(name)
            w = A.window(trials[setup], N)
            runs[setup]["_w"] = (w.get("w0"), w.get("w1"))
    con = {}
    if "none" in runs:
        for name in runs:
            if name != "none":
                con[name] = contrast(runs["none"], runs[name], trials["none"], trials[name], prefix + name)
    # prereg readings 2 and 3: lmc against wait; agate against the fixed gate with the higher (net) requests/h
    readings = {}
    if "wait" in runs and "lmc" in runs:
        readings["lmc − wait"] = contrast(runs["wait"], runs["lmc"], trials["wait"], trials["lmc"], prefix + "lmc|wait")
    gates = [g for g in ("gate8", "gate16") if g in runs and (runs[g]["throughput"] or {}).get("requests_per_h")]
    if "agate" in runs and gates:
        best = max(gates, key=lambda g: runs[g]["throughput"]["requests_per_h"])
        readings[f"agate − {best}"] = contrast(runs[best], runs["agate"], trials[best], trials["agate"],
                                                prefix + f"agate|{best}")
    TABLES.mkdir(parents=True, exist_ok=True)
    clean = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in runs.items()}
    (TABLES / f"{out}.json").write_text(json.dumps({"runs": clean, "vs_none": con, "readings": readings}, indent=1,
                                                   default=str) + "\n")
    f = lambda x, s="{:.1f}": "–" if x is None else s.format(x)
    lines = ["# Topic 23: efficiency tools on Qwen3.8-27B, 32 agents, 96 tasks (et_analyze.py)", "",
             "Seconds; full-N window for requests and throughput. `x` − `none` with 95 % bootstrap intervals.",
             "Throughput is the agents' work: vLLM's counters minus the harness's own calls (`save_context` re-sends, "
             "the controller's calls); the gross figures are in the mechanism table.", "",
             "## Per run", "",
             "| setup | GPU | req avg | p50 | p95 | p99 | entry avg | p50 | p95 | p99 | req/h | tok/s | gen tok/s | "
             "working req/h | tasks/h | pass |", "|" + "---|" * 16]
    for name, r in runs.items():
        q, e, t = r["request"]["all"], r["entry"], r["throughput"] or {}
        lines.append(f"| {name} | {r['gpu']} | {f(q['avg'])} | {f(q['p50'])} | {f(q['p95'])} | {f(q['p99'])} | "
                     f"{f(e['avg'], '{:.0f}')} | {f(e['p50'], '{:.0f}')} | {f(e['p95'], '{:.0f}')} | {f(e['p99'], '{:.0f}')} | "
                     f"{f(t.get('requests_per_h'), '{:,.0f}')} | {f(t.get('tokens_per_s'), '{:,.0f}')} | "
                     f"{f(t.get('gen_tokens_per_s'), '{:,.0f}')} | {f(r['working_requests_per_h'], '{:,.0f}')} | "
                     f"{f(r['tasks_per_h'])} | {f(100 * r['pass_rate'] if r['pass_rate'] is not None else None, '{:.0f} %')} |")
    lines += ["", "## Against `none` (and, in bold, the pre-registered readings 2 and 3: `lmc` − `wait`, `agate` − the better fixed gate)", "",
              "| setup | req avg | req p50 | req p95 | req p99 | entry avg | entry p50 | entry p95 | entry p99 | req/h × | "
              "tok/s × | pass (points) |", "|" + "---|" * 12]
    cell = lambda d, s: "–" if not d or d.get(s) is None or d[s].get("diff") is None else (
        f"{d[s]['diff']:+.1f} [{d[s]['ci'][0]:.1f}, {d[s]['ci'][1]:.1f}]" if d[s]["ci"][0] is not None else f"{d[s]['diff']:+.1f}")
    rows_con = list(con.items())
    if readings:
        rows_con += [(f"**{k}**", v) for k, v in readings.items()]
    for name, c in rows_con:
        rq, en = c["request_all"], c["entry"]
        rat = lambda k: "–" if f"ratio_{k}" not in c or c[f"ratio_{k}"]["ratio"] is None else (
            f"{c[f'ratio_{k}']['ratio']:.2f} [{c[f'ratio_{k}']['ci'][0]:.2f}, {c[f'ratio_{k}']['ci'][1]:.2f}]")
        pd = c["pass_diff"]
        lines.append(f"| {name} | {cell(rq, 'avg')} | {cell(rq, 'p50')} | {cell(rq, 'p95')} | {cell(rq, 'p99')} | "
                     f"{cell(en, 'avg')} | {cell(en, 'p50')} | {cell(en, 'p95')} | {cell(en, 'p99')} | "
                     f"{rat('requests_per_h')} | {rat('tokens_per_s')} | "
                     f"{f(100 * pd['diff'] if pd['diff'] is not None else None, '{:+.1f}')} |")
    lines += ["", "## Mechanism", "",
              "| setup | KV mean | KV ≥ 80 % | running | GPU hit share | offload hit share | recomputed tok/task | "
              "SM | tensor | HBM bw | K / sessions with a slot | waits/task | saves/task | wait on/off rule | hit after wait/save/other | "
              "gross req/h | gross tok/s | own calls/h |",
              "|" + "---|" * 18]
    for name, r in runs.items():
        kv, so, g, d, k = r["kv"], r["sources"], r["util"], r["decisions"], r["k"] or {}
        gr, t = r["throughput_gross"] or {}, r["throughput"] or {}
        own = (t["overhead_requests"] / t["seconds"] * 3600.0) if t.get("seconds") else None
        pct = lambda x: "–" if x is None else f"{100 * x:.0f} %"
        h = d["hit_after"]
        lines.append(f"| {name} | {pct(kv.get('kv_mean'))} | {pct(kv.get('kv_share_at_rule'))} | "
                     f"{f(kv.get('running_mean'))} | {pct(so.get('gpu_hit_share'))} | {pct(so.get('offload_hit_share'))} | "
                     f"{f(r['recomputed_tokens_per_task'], '{:,.0f}')} | {pct(g.get('sm_util'))} | {pct(g.get('tensor_util'))} | "
                     f"{pct(g.get('dram_bw_util'))} | {f(k.get('k_mean'))} / {f(r['sessions'].get('active_mean'))} | {f(d['waits_per_task'])} | "
                     f"{f(d['saves_per_task'])} | {pct(d['wait_on_rule'])} / {pct(d['wait_off_rule'])} | "
                     f"{pct(h.get('wait'))} / {pct(h.get('save'))} / {pct(h.get('other'))} | "
                     f"{f(gr.get('requests_per_h'), '{:,.0f}')} | {f(gr.get('tokens_per_s'), '{:,.0f}')} | "
                     f"{f(own, '{:,.0f}')} |")
    (TABLES / f"{out}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("pilot", "main", "smoke", "valid"))
    ap.add_argument("--run", action="append", help="valid: the run(s) to check (default: every main run present)")
    ap.add_argument("--tasks", type=int, default=96, help="valid: the tasks a run must cover")
    a = ap.parse_args()
    if a.what == "pilot":
        sys.exit(pilot())
    if a.what == "valid":
        names = a.run or [s for s in SETUPS if (JOBS / s).is_dir()]
        res = {n: valid(n, a.tasks) for n in names}
        print(json.dumps(res, indent=1))
        sys.exit(0 if all(r["valid"] for r in res.values()) else 1)
    sys.exit(main_tables("smoke_", "et_smoke") if a.what == "smoke" else main_tables())


if __name__ == "__main__":
    main()
