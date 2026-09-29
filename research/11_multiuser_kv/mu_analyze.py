#!/usr/bin/env python3
"""Tables of research/11_multiuser_kv (offline): where a multi-user harness spends its time.

Inputs per condition (data/multiuser_live/cells/<cond>/): every session's capture (client send /
receive, usage, purpose, turn) and trace (turns, think spans, tools, compaction decisions), and every
server instance's MUScheduler log (arrival, admission with the actual and the ideal prefix hit,
preemptions, first token, finish, per-step prefill/decode composition and stamps, evictions with
their owners) plus the 1 Hz scrape.

Per model call (joined by request id):
  waterfall   send -> vLLM arrival (HTTP) -> engine add (template + tokenize + IPC) -> admission
              (queue) -> first token (prefill) -> finish (decode) -> receive; additive to the latency.
  tokens      prompt = hit + evicted (ideal - hit: had been computed, was gone) + fresh; fresh is split
              into "after a harness edit" (the prompt diverges from the chain's previous prompt before
              its reusable end: reactive compaction, memory or timestamp sections) and "new".
  GPU time    a Huber-fit step model t = c0 + c0m*mixed + c1*prefill + c2*prefill attention context +
              c3*decode seqs + c4*decode context attributes every step's GPU time to its tokens by class
              (decode, fresh prefill, evicted recompute, preemption recompute) and to the fixed cost.
  excess      each second of a call's server time is charged to what the GPU was doing in it (own work
              by class, other requests' decode = batching, other requests' prefill by class =
              interference, not scheduled, idle); queue time is charged to the reason the head of the
              queue waited (KV capacity, preemption this step, token budget, max seqs, loads).
Per session: turn latency (user-visible), think, tool and harness time; per cell: steady-window
throughput and the eviction survival of paused prefixes by pause kind and length.

    python research/11_multiuser_kv/mu_analyze.py [--cond n04,n08,...] [--out data/multiuser_live/multiuser_tables.md]
    python research/11_multiuser_kv/mu_analyze.py --smoke           # V2 checks on the smoke cell
    python research/11_multiuser_kv/mu_analyze.py --replay          # replay policy tables
"""
from __future__ import annotations

import argparse
import bisect
import json
import lzma
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402

DIR = Path(__file__).resolve().parent
LIVE = mw.DATA
CELLS = LIVE / "cells"
REPLAY_OUT = DIR / "data" / "multiuser_replay"
OK = {"ok", "budget", "deadline"}
LIVE_ORDER = ["n01a", "n01b", "n04", "n08", "n16", "n32", "n16kvh", "n16mem", "n16ts", "pilot1", "pilot8", "smoke"]


# -- reading -------------------------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def q(values, p: float):
    vals = sorted(v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not vals:
        return float("nan")
    k = (len(vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else float("nan")


def boot_ci(values: list[float], clusters: list[str], stat=np.mean, iters: int = 1000, seed: int = 0):
    """Percentile CI resampling clusters (sessions)."""
    groups = defaultdict(list)
    for v, c in zip(values, clusters):
        if v is not None:
            groups[c].append(v)
    keys = list(groups)
    if not keys:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    point = float(stat(np.concatenate([groups[k] for k in keys])))
    boots = []
    for _ in range(iters):
        pick = rng.integers(0, len(keys), len(keys))
        boots.append(float(stat(np.concatenate([groups[keys[i]] for i in pick]))))
    return point, float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


# -- server logs ------------------------------------------------------------------------------------------
class Server:
    """One vLLM instance's MUScheduler log, with engine time mapped to wall clock."""

    def __init__(self, path: Path):
        self.dir = path
        self.info = json.loads((path / "server_info.json").read_text()) if (path / "server_info.json").exists() else {}
        recs = read_jsonl(path / "sched.jsonl.xz") or read_jsonl(path / "sched.jsonl")
        self.hdr = next((r for r in recs if r["k"] == "hdr"), {})
        clocks = [r["wall"] - r["t"] for r in recs if r["k"] in ("clk", "hdr") and "wall" in r]
        self.offset = statistics.median(clocks) if clocks else 0.0
        self.req: dict[int, dict] = {}
        self.by_ext: dict[str, dict] = {}
        self.steps: list[dict] = []
        self.ev: list[dict] = []
        self.pool: list[dict] = []
        self.idle: list[dict] = []
        self.chains: dict[int, dict] = {}
        self.pins: list[dict] = []
        self.errors = [r for r in recs if r["k"] == "err"]
        for r in recs:
            k = r["k"]
            if k == "arr":
                rec = {"arr": r, "adm": [], "pre": [], "tok1": None, "fin": None, "chunks": [], "dec": []}
                self.req[r["r"]] = rec
                self.by_ext[r["id"]] = rec
            elif k == "adm" and r["r"] in self.req:
                self.req[r["r"]]["adm"].append(r)
            elif k == "pre" and r["r"] in self.req:
                self.req[r["r"]]["pre"].append(r)
            elif k == "tok1" and r["r"] in self.req:
                self.req[r["r"]]["tok1"] = r["t"]
            elif k == "fin" and r["r"] in self.req:
                self.req[r["r"]]["fin"] = r
            elif k == "step":
                self.steps.append(r)
            elif k == "ev":
                self.ev.append(r)
            elif k == "pool":
                self.pool.append(r)
            elif k == "idle":
                self.idle.append(r)
            elif k == "chain":
                self.chains[r["c"]] = r
            elif k in ("pin", "unpin"):
                self.pins.append(r)
        self.steps.sort(key=lambda s: s["n"])
        prev_u0 = None
        for s in self.steps:                              # GPU interval of each step
            launch = s.get("l") or s.get("s1")
            start = max(prev_u0, launch) if prev_u0 is not None else launch
            s["g0"], s["g1"] = start, s["u0"]
            prev_u0 = s["u0"]
            for ri in s["dcr"]:
                if ri in self.req:
                    self.req[ri]["dec"].append(s["n"])
        self.step_by_n = {s["n"]: s for s in self.steps}
        self._reclassify()

    def _reclassify(self) -> None:
        """Chunk classes from raw fields: evicted = [first-admission hit, ARRIVAL ideal), preempted =
        [hit on resume, computed tokens lost at the preemption), fresh = the rest. Servers started
        before the 2026-09-26 scheduler fix logged an admission ideal inflated by the request's own
        first chunk; the arrival ideal is correct in both versions."""
        for s in self.steps:
            new_pfl, tot = [], [0, 0, 0]
            for chunk in s["pfl"]:
                ri, a, n = chunk[0], chunk[1], chunk[2]
                rec = self.req.get(ri)
                if rec is None:
                    new_pfl.append(chunk)
                    continue
                first = [x for x in rec["adm"] if not x["res"]]
                hit = first[0]["hit"] if first else a
                ideal = max(hit, rec["arr"]["ideal"])
                lo, hi = a, a + n
                pre = 0
                resumes = [x for x in rec["adm"] if x["res"] and x["t"] <= s["s1"] + 1e-6]
                if resumes and rec["pre"]:
                    rh = resumes[-1]["hit"]
                    lost = max(p["nc"] for p in rec["pre"] if p["t"] <= resumes[-1]["t"])
                    pre = max(0, min(hi, lost) - max(lo, rh))
                    both = max(0, min(hi, lost, ideal) - max(lo, rh, hit))
                else:
                    both = 0
                ev = max(0, min(hi, ideal) - max(lo, hit)) - both
                fresh = n - pre - ev
                new_pfl.append([ri, a, n, fresh, ev, pre])
                tot[0] += fresh
                tot[1] += ev
                tot[2] += pre
            s["pfl"] = new_pfl
            s["pf"] = tot
        for rec in self.req.values():                       # admission ideal := arrival ideal
            for x in rec["adm"]:
                if not x["res"]:
                    x["ideal"] = max(x["hit"], rec["arr"]["ideal"])

    def wall(self, t):
        return None if t is None else t + self.offset


def edit_causes(events: list[dict]) -> dict[str, list[str]]:
    """request id -> harness edits made since the previous model call: 'reactive' (prompt-too-long
    compaction), 'summary' (compact_history), 'proactive' (micro_compact / fit_tool_results shrank a
    history of more than 20k characters by more than 10% in context preparation)."""
    starts = {e.get("span_id"): (e.get("data") or {}).get("characters_before") for e in events
              if e.get("event") == "context_prepare"}
    out, pending = {}, []
    for e in events:
        ev, data = e.get("event"), e.get("data") or {}
        if ev == "harness_decision" and data.get("reason") == "prompt_too_long":
            pending.append("reactive")
        elif ev == "context_compact":
            pending.append("summary")
        elif ev == "context_prepared":
            before, after = starts.get(e.get("span_id")), data.get("characters_after")
            if before and after is not None and before > 20000 and after < 0.9 * before:
                pending.append("proactive")
        elif ev == "mu_call" and data.get("purpose") == "lead":   # a compaction's own summary call
            out[data.get("request_id")] = pending                  # must not consume the marker
            pending = []
    return out


# -- step model -----------------------------------------------------------------------------------------------
FEATURES = ["fixed", "mixed", "pf_tok", "pf_attn", "dec_seq", "dec_ctx"]


def step_features(servers: list[Server]) -> None:
    """Attach the model features to every step; decode context from each request's decode count."""
    for srv in servers:
        dec_count: dict[int, int] = defaultdict(int)
        for s in srv.steps:
            pf_tok = sum(c[2] for c in s["pfl"])
            pf_attn = sum(c[2] * (c[1] + c[2] / 2) for c in s["pfl"]) / 1e6
            ctx = 0
            for ri in s["dcr"]:
                rec = srv.req.get(ri)
                base = rec["arr"]["np"] if rec else 0
                dec_count[ri] += 1
                ctx += base + dec_count[ri]
            s["x"] = [1.0, 1.0 if pf_tok else 0.0, pf_tok / 1e3, pf_attn, len(s["dcr"]), ctx / 1e6]
            s["gpu"] = (s["g1"] - s["g0"]) if s["g0"] is not None and s["g1"] is not None else None


def fit_step_model(servers: list[Server]) -> dict:
    rows = [(s["x"], s["gpu"]) for srv in servers for s in srv.steps
            if s.get("gpu") is not None and 0 < s["gpu"] < 30 and s["tok"] > 0]
    if len(rows) < 50:
        return {"coef": None, "n": len(rows)}
    X = np.array([r[0] for r in rows])
    y = np.array([r[1] for r in rows])
    w = np.ones(len(y))
    coef = np.zeros(X.shape[1])
    for _ in range(30):                                  # Huber IRLS
        W = np.sqrt(w)[:, None]
        coef, *_ = np.linalg.lstsq(X * W, y * W[:, 0], rcond=None)
        resid = y - X @ coef
        scale = 1.4826 * np.median(np.abs(resid - np.median(resid))) + 1e-9
        k = 1.345 * scale
        w = np.where(np.abs(resid) <= k, 1.0, k / np.maximum(np.abs(resid), 1e-12))
    pred = X @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
    mape = float(np.median(np.abs(y - pred) / np.maximum(y, 1e-4)))
    return {"coef": dict(zip(FEATURES, coef.tolist())), "n": len(rows), "r2": 1 - ss_res / ss_tot,
            "median_ape": mape}


def attribute_steps(servers: list[Server], model: dict) -> None:
    """Split every step's GPU time into fixed / per-chunk prefill by class / per-request decode."""
    c = model["coef"]
    for srv in servers:
        for s in srv.steps:
            gpu = s.get("gpu")
            if gpu is None or gpu <= 0 or c is None:
                s["share"] = None
                continue
            fixed = c["fixed"] + c["mixed"] * s["x"][1]
            chunks = []
            for chunk in s["pfl"]:
                ri, a, n, fresh, ev, pre = chunk
                cost = c["pf_tok"] * n / 1e3 + c["pf_attn"] * n * (a + n / 2) / 1e6
                chunks.append((ri, max(cost, 0.0), n, fresh, ev, pre))
            decs = []
            for ri in s["dcr"]:
                rec = srv.req.get(ri)
                ctx = (rec["arr"]["np"] if rec else 0)
                decs.append((ri, max(c["dec_seq"] + c["dec_ctx"] * ctx / 1e6, 0.0)))
            total = max(fixed, 0.0) + sum(x[1] for x in chunks) + sum(x[1] for x in decs)
            scale = gpu / total if total > 0 else 0.0
            s["share"] = {"fixed": max(fixed, 0.0) * scale,
                          "pf": [(ri, cost * scale, n, fresh, ev, pre) for ri, cost, n, fresh, ev, pre in chunks],
                          "dec": [(ri, cost * scale) for ri, cost in decs]}


def gpu_by_class(servers: list[Server]) -> dict:
    out = Counter()
    busy = wall = 0.0
    for srv in servers:
        if not srv.steps:
            continue
        wall += srv.steps[-1]["g1"] - srv.steps[0]["g0"]
        for s in srv.steps:
            sh = s.get("share")
            if not sh:
                continue
            busy += s["gpu"]
            # the step's base cost (weights streamed once per step) is shared equally by its requests
            k = len({c[0] for c in sh["pf"]} | {d[0] for d in sh["dec"]}) or 1
            base = sh["fixed"] / k
            for ri, cost, n, fresh, ev, pre in sh["pf"]:
                if n:
                    out["prefill_fresh"] += (cost + base) * fresh / n
                    out["prefill_evicted"] += (cost + base) * ev / n
                    out["prefill_preempted"] += (cost + base) * pre / n
            out["decode"] += sum(cost + base for _, cost in sh["dec"])
            out["base_share_check"] += sh["fixed"]
    out["idle"] = max(0.0, wall - busy)
    out["wall"] = wall
    return dict(out)


# -- sessions and calls ----------------------------------------------------------------------------------------
class Cell:
    def __init__(self, cond: str, root: Path = CELLS):
        self.cond = cond
        self.root = root / cond
        self.servers = [Server(p) for p in sorted((self.root / "server").glob("*")) if p.is_dir()]
        self.log = read_jsonl(self.root / "cell_log.jsonl")
        self.sessions = []
        for meta_path in sorted(self.root.glob("runs/*/*.meta.json")):
            meta = json.loads(meta_path.read_text())
            run = meta_path.parent
            label = meta["label"]
            calls = read_jsonl(run / f"{label}.requests.jsonl.xz")
            traces = [p for p in run.glob("run_*.jsonl.xz") if ".inputs." not in p.name]
            events = read_jsonl(traces[0]) if traces else []
            self.sessions.append({"meta": meta, "calls": calls, "events": events})
        self.calls = []
        self._join()

    def _join(self):
        index = {}
        for srv in self.servers:
            for ext, rec in srv.by_ext.items():
                index[ext] = (srv, rec)
        for sess in self.sessions:
            meta = sess["meta"]
            prev_by_chain: dict[str, dict] = {}
            causes = edit_causes(sess["events"])
            for c in sess["calls"]:
                purpose = c.get("purpose") or "unknown"
                row = {"label": meta["label"], "filler": meta.get("filler"), "status": meta.get("status"),
                       "call": c["call_index"], "purpose": purpose, "turn": c.get("turn_id"),
                       "t_send": c.get("t_send"), "t_recv": c.get("t_recv"),
                       "overflow": bool(c.get("overflow")), "refused": c.get("refused"),
                       "error": c.get("error") if "response" not in c else None}
                resp = c.get("response")
                if resp:
                    u = resp.get("usage") or {}
                    row.update(np=int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                               + int(u.get("cache_creation_input_tokens") or 0),
                               cached=int(u.get("cache_read_input_tokens") or 0), out=int(u.get("output_tokens") or 0),
                               stop=resp.get("stop_reason"), lat=c["t_recv"] - c["t_send"])
                hit = index.get(c.get("request_id"))
                if hit and resp:
                    srv, rec = hit
                    self._server_fields(row, srv, rec)
                row["system_sig"] = hash(json.dumps(c.get("system"), sort_keys=True)) if c.get("system") else None
                row["trace_causes"] = causes.get(c.get("request_id"), [])
                prev = prev_by_chain.get(purpose)
                row["prev_np"] = prev.get("np") if prev else None
                row["same_turn"] = prev is not None and prev.get("turn") == row["turn"]
                row["system_changed"] = prev is not None and prev.get("system_sig") != row["system_sig"]
                if resp:
                    prev_by_chain[purpose] = row
                self.calls.append(row)

    @staticmethod
    def _server_fields(row, srv: Server, rec: dict):
        arr = rec["arr"]
        adm = [a for a in rec["adm"] if not a["res"]]
        first = adm[0] if adm else None
        row.update(srv=srv, rec=rec, fe=arr.get("fe"), t_add=srv.wall(arr["t"]),
                   t_adm=srv.wall(first["t"]) if first else None, t_tok1=srv.wall(rec["tok1"]),
                   t_fin=srv.wall(rec["fin"]["t"]) if rec["fin"] else None,
                   hit=first["hit"] if first else None, ideal=first["ideal"] if first else arr["ideal"],
                   ideal_arr=arr["ideal"], hit_now=arr["hit_now"], lcp=arr["lcp"], gap=arr["gap"],
                   npre=len(rec["pre"]), srv_np=arr["np"])
        if row["t_fin"] is not None:
            parts = {"http_in": row["fe"] - row["t_send"] if row["fe"] else None,
                     "prep": row["t_add"] - row["fe"] if row["fe"] else None,
                     "queue": (row["t_adm"] - row["t_add"]) if row["t_adm"] else None,
                     "prefill": (row["t_tok1"] - row["t_adm"]) if row["t_tok1"] and row["t_adm"] else None,
                     "decode": (row["t_fin"] - row["t_tok1"]) if row["t_tok1"] else None,
                     "out": row["t_recv"] - row["t_fin"]}
            row["parts"] = parts

    # -- per-call token classes -----------------------------------------------------------------
    def token_classes(self):
        for row in self.calls:
            if row.get("hit") is None:
                continue
            np_ = row["srv_np"]
            ideal, hit = max(row["ideal"], row["hit"]), row["hit"]
            prev = row.get("prev_np")
            reusable_prev = ((prev - 4) // 16) * 16 if prev else 0
            edited = prev is not None and row["lcp"] + 16 < reusable_prev
            if edited:
                tc = row.get("trace_causes") or []
                row["edit_cause"] = ("system" if row.get("system_changed") else
                                     "reactive" if "reactive" in tc else "summary" if "summary" in tc else
                                     "proactive" if "proactive" in tc else "other")
            fresh = np_ - ideal
            row["cls"] = {"hit": hit, "evicted": ideal - hit, "pause_lost": max(0, row["ideal_arr"] - row["hit_now"]),
                          "queue_lost": max(0, row["hit_now"] - hit),
                          "after_edit": fresh if edited else 0, "new": 0 if edited else fresh}
            row["edited"] = edited

    # -- per-call time attribution --------------------------------------------------------------------
    def attribute_calls(self):
        for row in self.calls:
            srv, rec = row.get("srv"), row.get("rec")
            if srv is None or row.get("t_adm") is None or row.get("t_fin") is None:
                continue
            me = rec["arr"]["r"]
            t0, t1 = rec["adm"][0]["t"], rec["fin"]["t"]
            acc = Counter()
            covered = 0.0
            for s in self._steps_between(srv, t0, t1):
                sh = s.get("share")
                if not sh or s["g0"] is None:
                    continue
                d = min(s["g1"], t1) - max(s["g0"], t0)
                if d <= 0:
                    continue
                covered += d
                frac = d / s["gpu"] if s["gpu"] > 0 else 0.0
                part = Counter()
                mine = False
                part["fixed"] += sh["fixed"] * frac
                for ri, cost, n, fresh, ev, pre in sh["pf"]:
                    who = "own" if ri == me else "other"
                    mine |= ri == me
                    if n:
                        part[f"{who}_pf_fresh"] += cost * frac * fresh / n
                        part[f"{who}_pf_evicted"] += cost * frac * ev / n
                        part[f"{who}_pf_preempted"] += cost * frac * pre / n
                for ri, cost in sh["dec"]:
                    who = "own" if ri == me else "other"
                    mine |= ri == me
                    part[f"{who}_dec"] += cost * frac
                if mine:
                    acc.update(part)
                else:                       # running but left out of this step (budget, preemption)
                    acc["not_scheduled"] += d
            span = t1 - t0
            acc["gaps"] = max(0.0, span - covered)
            row["att"] = dict(acc)
            stalls = 0.0
            for p in rec["pre"]:
                res = [a for a in rec["adm"] if a["res"] and a["t"] >= p["t"]]
                if res:
                    stalls += res[0]["t"] - p["t"]
            row["preempt_stall"] = stalls

    @staticmethod
    def _steps_between(srv: Server, t0: float, t1: float):
        lo, hi = 0, len(srv.steps)
        while lo < hi:                                     # first step ending after t0
            mid = (lo + hi) // 2
            if srv.steps[mid]["g1"] is None or srv.steps[mid]["g1"] < t0:
                lo = mid + 1
            else:
                hi = mid
        i = lo
        while i < len(srv.steps) and srv.steps[i]["g0"] is not None and srv.steps[i]["g0"] < t1:
            yield srv.steps[i]
            i += 1

    # -- queue reasons -----------------------------------------------------------------------------------
    def queue_reasons(self):
        for srv in self.servers:
            timeline = [(s["s0"], s.get("head"), s.get("hwhy")) for s in srv.steps]
            timeline += [(i["t0"], i.get("head"), i.get("hwhy")) for i in srv.idle]
            timeline.sort(key=lambda x: x[0])
            srv.head_timeline = timeline
            srv.head_times = [x[0] for x in timeline]
        for row in self.calls:
            srv, rec = row.get("srv"), row.get("rec")
            if srv is None or not rec["adm"]:
                continue
            t0, t1 = rec["arr"]["t"], rec["adm"][0]["t"]
            me = rec["arr"]["r"]
            acc = Counter()
            tl = srv.head_timeline
            i = max(0, bisect.bisect_right(srv.head_times, t0) - 1)
            while i < len(tl) and tl[i][0] < t1:
                ta, head, why = tl[i]
                nxt = tl[i + 1][0] if i + 1 < len(tl) else t1
                a, b = max(ta, t0), min(nxt, t1)
                if b > a:
                    key = (why or "none") if head == me else f"behind_{why or 'none'}"
                    acc[key] += b - a
                i += 1
            row["queue_why"] = dict(acc)


# -- sessions -------------------------------------------------------------------------------------------------
def session_rows(cell: Cell) -> list[dict]:
    out = []
    for sess in cell.sessions:
        meta, events = sess["meta"], sess["events"]
        turns = []
        starts = {}
        for e in events:
            if e["event"] == "turn_start":
                starts[e["turn_id"]] = e["monotonic_ns"]
            elif e["event"] == "turn_end" and e["turn_id"] in starts:
                turns.append((e["monotonic_ns"] - starts[e["turn_id"]]) / 1e9)
        thinks = [e["data"].get("duration_ms", 0) / 1000 for e in events
                  if e["event"] == "input_wait_end" and (e["data"] or {}).get("status") == "ok"]
        tools = [e["data"].get("duration_ms", 0) / 1000 for e in events if e["event"] == "tool_end"]
        model = [e["data"].get("duration_ms", 0) / 1000 for e in events if e["event"] == "model_response"]
        compactions = sum(1 for e in events if e["event"] == "harness_decision"
                          and (e["data"] or {}).get("action") == "reactive_compact")
        calls = [c for c in sess["calls"] if c.get("purpose") == "lead"]
        out.append({"label": meta["label"], "filler": meta.get("filler"), "status": meta.get("status"),
                    "turns": turns, "turn_total": sum(turns), "think": sum(thinks), "tool": sum(tools),
                    "model": sum(model), "wall": meta.get("wall_s"), "lead_calls": len(calls),
                    "compactions": compactions, "overflows": meta.get("overflows", 0),
                    "t_start": meta.get("t_start"), "t_end": meta.get("t_end")})
    return out


def steady_window(cell: Cell, n: int) -> tuple[float, float] | None:
    """Longest interval with at least n-1 sessions (counted or filler) live."""
    iv = [(r["t0"], r["t1"]) for r in cell.log if r.get("t0") and r.get("t1")]
    if not iv:
        return None
    edges = sorted([(a, 1) for a, _ in iv] + [(b, -1) for _, b in iv])
    live, start, best = 0, None, (0.0, None)
    for t, d in edges:
        live += d
        if live >= max(1, n - 1) and start is None:
            start = t
        elif live < max(1, n - 1) and start is not None:
            if t - start > best[0]:
                best = (t - start, (start, t))
            start = None
    return best[1]


# -- tables ---------------------------------------------------------------------------------------------------
def fmt(x, nd=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(fmt(v) if not isinstance(v, str) else v for v in row) + " |" for row in rows]
    return "\n".join(out)


def analyze_cells(conds: list[str], root: Path = CELLS) -> tuple[str, dict]:
    cells = {c: Cell(c, root) for c in conds if (root / c).exists()}
    servers = [s for cell in cells.values() for s in cell.servers]
    step_features(servers)
    model = fit_step_model(servers)
    attribute_steps(servers, model)
    js = {"step_model": model, "cells": {}}
    md = [f"# 11_multiuser_kv tables\n\nGenerated by `research/11_multiuser_kv/mu_analyze.py` from "
          f"{len(cells)} cells, {sum(len(c.sessions) for c in cells.values())} sessions.\n",
          f"Step model (all cells pooled, {model.get('n')} steps): "
          + (", ".join(f"{k}={v:.4g}" for k, v in (model.get("coef") or {}).items()) or "not enough steps")
          + (f"; R²={model['r2']:.3f}, median APE={model['median_ape']:.1%}" if model.get("coef") else "") + "\n"]
    t1, t2, t3, t4, t5 = [], [], [], [], []
    for cond, cell in cells.items():
        cell.token_classes()
        cell.attribute_calls()
        cell.queue_reasons()
        counted = [s for s in session_rows(cell) if not s["filler"]]
        calls = [r for r in cell.calls if not r["filler"] and r["purpose"] == "lead" and r.get("lat") is not None]
        joined = [r for r in calls if r.get("parts")]
        n = mw.cond(cond)["n"] if cond in mw.CONDITIONS else None
        win = steady_window(cell, n or 1)
        turns_all = [t for s in counted for t in s["turns"]]
        # T1 runs and outcomes
        status = Counter(s["status"] for s in counted)
        t1.append([cond, n, len(counted), dict(status).__repr__().replace("|", "/"), len(calls),
                   len(joined) / len(calls) if calls else float("nan"), sum(s["overflows"] or 0 for s in counted),
                   sum(s["compactions"] for s in counted), sum(len(srv.errors) for srv in cell.servers)])
        # T2 waterfall (medians and means, seconds per lead call)
        parts = {k: [r["parts"][k] for r in joined if r["parts"].get(k) is not None]
                 for k in ("http_in", "prep", "queue", "prefill", "decode", "out")}
        lat = [r["lat"] for r in joined]
        t2.append([cond, n, q(lat, 0.5), q(lat, 0.95), mean(lat)] + [mean(parts[k]) for k in parts])
        # T3 token classes (sums over lead calls) and GPU time by class
        cls = Counter()
        for r in joined:
            for k, v in (r.get("cls") or {}).items():
                cls[k] += v
        total_prompt = sum(r["srv_np"] for r in joined if r.get("srv_np"))
        gpu = gpu_by_class(cell.servers)
        t3.append([cond, n, total_prompt, cls["hit"] / total_prompt if total_prompt else float("nan"),
                   cls["evicted"] / total_prompt if total_prompt else float("nan"),
                   cls["after_edit"] / total_prompt if total_prompt else float("nan"),
                   cls["new"] / total_prompt if total_prompt else float("nan"),
                   gpu.get("prefill_evicted", 0) / gpu["wall"] if gpu.get("wall") else float("nan"),
                   gpu.get("prefill_preempted", 0) / gpu["wall"] if gpu.get("wall") else float("nan"),
                   gpu.get("prefill_fresh", 0) / gpu["wall"] if gpu.get("wall") else float("nan"),
                   gpu.get("decode", 0) / gpu["wall"] if gpu.get("wall") else float("nan"),
                   gpu.get("idle", 0) / gpu["wall"] if gpu.get("wall") else float("nan")])
        # T4 where each call's server time went (mean seconds per lead call)
        att = Counter()
        qwhy = Counter()
        stall = 0.0
        for r in joined:
            for k, v in (r.get("att") or {}).items():
                att[k] += v
            for k, v in (r.get("queue_why") or {}).items():
                qwhy[k] += v
            stall += r.get("preempt_stall") or 0
        m = len(joined) or 1
        kv_queue = sum(v for k, v in qwhy.items() if k.endswith(("kv", "pre")))
        t4.append([cond, n, mean([r["parts"]["queue"] for r in joined if r["parts"]["queue"] is not None]),
                   kv_queue / m, (sum(qwhy.values()) - kv_queue) / m,
                   att["own_pf_fresh"] / m, att["own_pf_evicted"] / m, att["own_pf_preempted"] / m,
                   att["own_dec"] / m, att["other_dec"] / m,
                   (att["other_pf_fresh"]) / m, (att["other_pf_evicted"] + att["other_pf_preempted"]) / m,
                   att["fixed"] / m, att["not_scheduled"] / m, stall / m])
        # T5 session level
        thr = None
        if win:
            done = [s for s in counted if s["t_end"] and win[0] <= s["t_end"] <= win[1]]
            thr = sum(len(s["turns"]) for s in done) / ((win[1] - win[0]) / 3600)
        t5.append([cond, n, q(turns_all, 0.5), q(turns_all, 0.95), mean([s["turn_total"] for s in counted]),
                   mean([s["lead_calls"] for s in counted]), mean([s["tool"] for s in counted]),
                   mean([s["think"] for s in counted]), thr])
        js["cells"][cond] = {"n": n, "sessions": len(counted), "calls": len(calls), "joined": len(joined),
                             "token_classes": dict(cls), "total_prompt": total_prompt, "gpu": gpu,
                             "attribution": dict(att), "queue_why": dict(qwhy), "status": dict(status),
                             "turn_p50": q(turns_all, 0.5), "turn_p95": q(turns_all, 0.95),
                             "throughput_turns_h": thr, "window": win}
    md.append("## T1 · Runs and outcomes\n")
    md.append(table(["cond", "N", "sessions", "status", "lead calls", "joined", "overflows", "compactions",
                     "sched errors"], t1))
    md.append("\n## T2 · Lead-call latency and its waterfall (s)\n")
    md.append(table(["cond", "N", "p50", "p95", "mean", "http in", "template+tok", "queue", "prefill", "decode",
                     "response"], t2))
    md.append("\n## T3 · Prompt tokens by class (share of all lead-call prompt tokens) and GPU time by class "
              "(share of wall)\n")
    md.append(table(["cond", "N", "prompt tok", "hit", "evicted", "after edit", "new", "GPU evict recompute",
                     "GPU preempt recompute", "GPU fresh prefill", "GPU decode", "GPU idle"], t3))
    md.append("\n## T4 · Where a lead call's server time goes (mean s per call)\n")
    md.append(table(["cond", "N", "queue", "queue: KV/preempt", "queue: other", "own fresh pf", "own evict pf",
                     "own preempt pf", "own decode", "others' decode", "others' fresh pf",
                     "others' recompute pf", "fixed", "not scheduled", "preempt stall"], t4))
    ex_md, ex_js = excess_table(cells, model)
    js["excess"] = ex_js
    md.append("\n## T4b · Excess latency over the solo model, per lead call (s): what each mechanism adds\n")
    md.append(ex_md)
    md.append("\n## T6 · Eviction survival: share of a session's own reusable prefix still cached when its next "
              "request arrives, by pause kind and length\n")
    md.append(survival_table(cells))
    # T7 harness edits by cause, T8 queue reasons, T9 verdict
    t7, t8, t9 = [], [], []
    for cond, cell in cells.items():
        joined = [r for r in cell.calls if not r["filler"] and r["purpose"] == "lead" and r.get("cls")]
        edit = Counter()
        for r in joined:
            if r.get("edited"):
                edit[r.get("edit_cause") or "other"] += r["cls"]["after_edit"]
        tot = sum(edit.values()) or 1
        t7.append([cond, sum(1 for r in joined if r.get("edited")), len(joined)]
                  + [edit[k] / tot for k in ("proactive", "summary", "reactive", "system", "other")])
        qc = Counter()
        for r in joined:
            for k, v in (r.get("queue_why") or {}).items():
                qc[k] += v
        qt = sum(qc.values())
        t8.append([cond, qt / max(1, len(joined))] + [qc[k] / qt if qt else float("nan")
                   for k in ("kv", "behind_kv", "pre", "behind_pre", "tok", "behind_tok", "seq", "load")])
        ex = js.get("excess", {}).get(cond)
        gpu = js["cells"][cond]["gpu"]
        if ex:
            kv = ex["queue_kv"] + ex["own_recompute"] + ex["preempt"] + ex["interference_recompute"]
            share = ex["batching"] + ex["interference_fresh"] + ex["not_scheduled"]
            exc = ex["lat"] - ex["solo"]
            nan = float("nan")
            small = exc < 0.05 * ex["solo"]              # no excess to split (the solo cells)
            t9.append([cond, js["cells"][cond]["n"], ex["lat"], ex["solo"], ex["lat"] / ex["solo"],
                       nan if small else kv / exc, nan if small else share / exc,
                       nan if small else (ex["frontend"] + ex["queue_other"]) / exc,
                       nan if small else ex["residual"] / exc,
                       gpu.get("idle", 0) / gpu["wall"] if gpu.get("wall") else float("nan"),
                       (gpu.get("prefill_evicted", 0) + gpu.get("prefill_preempted", 0)) / gpu["wall"]
                       if gpu.get("wall") else float("nan"), js["cells"][cond]["throughput_turns_h"]])
    md.append("\n## T7 · Harness edits: lead calls whose prompt diverged from the previous one before its reusable "
              "end, and the tokens re-prefilled after the edit by cause (share)\n")
    md.append(table(["cond", "edited calls", "calls", "proactive compaction", "summary", "reactive (overflow)",
                     "system prompt", "other"], t7))
    md.append("\n## T8 · Queueing: mean queue s per lead call and its split by the reason the head of the queue waited "
              "(own = this call was the head; behind_ = it waited behind a head blocked for that reason)\n")
    md.append(table(["cond", "queue s/call", "KV", "behind KV", "preempt step", "behind preempt", "token budget",
                     "behind budget", "max seqs", "async load"], t8))
    md.append("\n## T9 · Verdict per load: slowdown and what makes it (shares of the excess over the solo model)\n")
    md.append(table(["cond", "N", "latency s", "solo model s", "slowdown", "KV contention", "compute sharing",
                     "frontend+other queue", "residual", "GPU idle", "GPU on recompute", "turns / h"], t9))
    md.append("\n## T5 · Sessions: turn latency (s), work and steady-window throughput\n")
    md.append(table(["cond", "N", "turn p50", "turn p95", "turn time / session", "lead calls / session",
                     "tool s / session", "think s / session", "turns / h"], t5))
    return "\n".join(md) + "\n", js


# -- excess over solo ------------------------------------------------------------------------------------------
def solo_model(model: dict, cells: dict, chunk: int = 8192):
    """Latency a call would have alone on the server with an ideal cache: its fresh prompt tokens in
    8192-token chunks at batch 1, its decode at batch 1, plus the frontend time of the solo cells."""
    c = model.get("coef")
    if not c:
        return None
    fe = [(r["srv_np"], r["parts"]["http_in"] + r["parts"]["prep"] + r["parts"]["out"])
          for cond in ("n01a", "n01b", "pilot1") if cond in cells for r in cells[cond].calls
          if r.get("parts") and None not in (r["parts"]["http_in"], r["parts"]["prep"], r["parts"]["out"])]
    if len(fe) >= 10:
        X = np.array([[1.0, n / 1e3] for n, _ in fe])
        fe_coef = np.linalg.lstsq(X, np.array([t for _, t in fe]), rcond=None)[0]
    else:
        fe_coef = np.array([0.0, 0.0])

    def solo(row) -> dict:
        np_, ideal, out = row["srv_np"], min(row["ideal"], row["srv_np"] - 1), max(1, row.get("out") or 1)
        pf, a, left = 0.0, ideal, np_ - ideal
        while left > 0:
            n = min(chunk, left)
            pf += c["fixed"] + c["mixed"] + c["pf_tok"] * n / 1e3 + c["pf_attn"] * n * (a + n / 2) / 1e6
            a += n
            left -= n
        k = np.arange(1, out)
        dec = float(np.sum(c["fixed"] + c["dec_seq"] + c["dec_ctx"] * (np_ + k) / 1e6)) if out > 1 else 0.0
        front = float(fe_coef[0] + fe_coef[1] * np_ / 1e3)
        return {"front": front, "prefill": pf, "decode": dec, "total": front + pf + dec}
    return solo


EXCESS_KEYS = ["frontend", "queue_kv", "queue_other", "own_recompute", "preempt", "batching",
               "interference_fresh", "interference_recompute", "not_scheduled", "residual"]


def excess_row(row, solo) -> dict | None:
    """Split latency - solo into additive parts (seconds)."""
    if solo is None or not row.get("parts") or row.get("att") is None or None in row["parts"].values():
        return None
    s = solo(row)
    parts, att, qwhy = row["parts"], row["att"], row.get("queue_why") or {}
    kv_q = sum(v for k, v in qwhy.items() if k.endswith(("kv", "pre")))
    out = {"solo": s["total"], "lat": row["lat"],
           "frontend": parts["http_in"] + parts["prep"] + parts["out"] - s["front"],
           "queue_kv": kv_q, "queue_other": max(0.0, parts["queue"] - kv_q),
           "own_recompute": att.get("own_pf_evicted", 0.0),
           "preempt": att.get("own_pf_preempted", 0.0) + (row.get("preempt_stall") or 0.0),
           "batching": att.get("other_dec", 0.0),
           "interference_fresh": att.get("other_pf_fresh", 0.0),
           "interference_recompute": att.get("other_pf_evicted", 0.0) + att.get("other_pf_preempted", 0.0),
           "not_scheduled": att.get("not_scheduled", 0.0)}
    out["residual"] = (row["lat"] - s["total"]) - sum(out[k] for k in EXCESS_KEYS if k != "residual")
    return out


def excess_table(cells: dict, model: dict) -> tuple[str, dict]:
    solo = solo_model(model, cells)
    rows, js = [], {}
    for cond, cell in cells.items():
        ex = [e for e in (excess_row(r, solo) for r in cell.calls
                          if not r["filler"] and r["purpose"] == "lead") if e]
        if not ex:
            continue
        m = len(ex)
        tot = {k: sum(e[k] for e in ex) / m for k in EXCESS_KEYS}
        lat, sol = sum(e["lat"] for e in ex) / m, sum(e["solo"] for e in ex) / m
        kv = tot["queue_kv"] + tot["own_recompute"] + tot["preempt"] + tot["interference_recompute"]
        rows.append([cond, m, lat, sol, lat - sol] + [tot[k] for k in EXCESS_KEYS] + [kv / max(1e-9, lat - sol)])
        js[cond] = {"calls": m, "lat": lat, "solo": sol, **tot, "kv_share_of_excess": kv / max(1e-9, lat - sol)}
    return table(["cond", "calls", "latency", "solo model", "excess"] + EXCESS_KEYS + ["KV share of excess"], rows), js


# -- eviction survival ------------------------------------------------------------------------------------------
PAUSE_BINS = [(0, 1), (1, 5), (5, 15), (15, 30), (30, 60), (60, 120), (120, 240), (240, 1e9)]


def survival_rows(cell: Cell) -> list[dict]:
    """One row per pause of a chain: how much of the session's own reusable prefix was still resident
    when its next request arrived. The session's first call can only hit what other sessions share
    (the common system-prompt head), so that hit is subtracted as the shared part."""
    shared: dict[str, int] = {}
    out = []
    for r in cell.calls:
        if r.get("rec") is None or r["purpose"] != "lead":
            continue
        if r["label"] not in shared:
            shared[r["label"]] = r["hit_now"] or 0
            continue
        gap = r.get("gap")
        own = (r["ideal_arr"] or 0) - shared[r["label"]]
        if gap is None or own <= 16 * 4:
            continue
        kept = max(0, (r["hit_now"] or 0) - shared[r["label"]])
        out.append({"label": r["label"], "filler": r["filler"], "kind": "tool" if r["same_turn"] else "think",
                    "gap": gap, "own": own, "survive": min(1.0, kept / own)})
    return out


def survival_table(cells: dict) -> str:
    rows = []
    for cond, cell in cells.items():
        pauses = [p for p in survival_rows(cell) if not p["filler"]]
        for kind in ("tool", "think"):
            for lo, hi in PAUSE_BINS:
                sel = [p for p in pauses if p["kind"] == kind and lo <= p["gap"] < hi]
                if not sel:
                    continue
                rows.append([cond, kind, f"{lo:g}-{hi:g}" if hi < 1e9 else f">{lo:g}", len(sel),
                             mean([p["survive"] for p in sel]),
                             sum(p["survive"] >= 0.99 for p in sel) / len(sel),
                             sum(p["survive"] <= 0.01 for p in sel) / len(sel),
                             mean([p["own"] for p in sel])])
    return table(["cond", "pause", "seconds", "pauses", "mean kept", "all kept", "all lost", "own prefix tok"], rows)


# -- replay -------------------------------------------------------------------------------------------------
def replay_run(cond: str, root: Path = CELLS) -> dict | None:
    """Client and server view of one replay condition over its measurement window (all of its server
    instances pooled): throughput of completed calls and turns, call latency, and the scheduler's view
    (prefix tokens lost to eviction, preemptions, queue wait)."""
    cell_dir = root / cond
    srv_dirs = sorted(p for p in (cell_dir / "server").glob("*") if p.is_dir())
    rows = [r for d in srv_dirs for r in read_jsonl(d / "replay_calls.jsonl")]
    if not rows:
        return None
    c = mw.cond(cond)
    window = float(c["replay"].get("window_s") or 0)
    t0 = min(r.get("t0") or r["t_send"] for r in rows)
    lo = t0 + (mw.REPLAY_WARMUP_S if window else 0)
    hi = t0 + window if window else max(r["t_recv"] for r in rows)
    ok = [r for r in rows if r["status"] == "ok"]
    in_win = [r for r in ok if lo <= r["t_recv"] <= hi]
    hours = max(1e-9, (hi - lo) / 3600)
    turns = defaultdict(list)
    for r in ok:
        turns[(r["label"], r.get("turn"))].append(r)
    turn_done = [max(x["t_recv"] for x in v) for v in turns.values()]
    turn_lat = [max(x["t_recv"] for x in v) - min(x["t_send"] for x in v) for v in turns.values()
                if lo <= max(x["t_recv"] for x in v) <= hi]
    servers = [Server(d) for d in srv_dirs if (d / "sched.jsonl.xz").exists() or (d / "sched.jsonl").exists()]
    adm = [(a["hit"], max(a["hit"], rec["arr"]["ideal"]), rec["arr"]["np"], a["wait"]) for srv in servers
           for rec in srv.req.values() for a in rec["adm"] if not a["res"]
           and lo <= srv.wall(a["t"]) <= hi]
    prompt = sum(x[2] for x in adm)
    return {"cond": cond, "policy": c["replay"]["policy"], "calls": len(in_win), "calls_h": len(in_win) / hours,
            "turns_h": sum(lo <= t <= hi for t in turn_done) / hours, "out_tok_h": sum(r["n_out"] or 0 for r in in_win) / hours,
            "lat_p50": q([r["t_recv"] - r["t_send"] for r in in_win], 0.5),
            "lat_p95": q([r["t_recv"] - r["t_send"] for r in in_win], 0.95),
            "turn_p50": q(turn_lat, 0.5), "turn_p95": q(turn_lat, 0.95),
            "evicted_frac": sum(x[1] - x[0] for x in adm) / prompt if prompt else float("nan"),
            "queue_p50": q([x[3] for x in adm], 0.5),
            "preemptions": sum(1 for srv in servers for rec in srv.req.values() for p in rec["pre"]
                               if lo <= srv.wall(p["t"]) <= hi),
            "errors": sum(1 for r in rows if r["status"] != "ok"), "window_h": hours}


def replay_tables(root: Path = CELLS) -> tuple[str, dict]:
    md, js = ["# 11_multiuser_kv replay tables\n",
              "Each replay drives the recorded sessions of one live cell closed-loop (same N, same slot order, "
              "exact prompt ids, recorded output lengths and gaps) against a fresh server with one policy, and is "
              f"measured over its window after a {mw.REPLAY_WARMUP_S // 60}-minute warm-up.\n"], {}
    for src, pols in mw.REPLAY_SOURCES.items():
        runs = [r for r in (replay_run(f"r_{src}_{p}", root) for p in pols) if r]
        if not runs:
            continue
        base = next((r for r in runs if r["policy"] == "default"), runs[0])
        rows = []
        for r in runs:
            rows.append([r["policy"], r["calls"], r["calls_h"], r["turns_h"], r["turns_h"] / base["turns_h"]
                         if base["turns_h"] else float("nan"), r["lat_p50"], r["lat_p95"], r["turn_p50"],
                         r["turn_p95"], r["evicted_frac"], r["queue_p50"], r["preemptions"], r["errors"]])
            js[f"{src}/{r['policy']}"] = r
        md.append(f"## Replay of {src} (N={mw.cond(src)['n']})\n")
        md.append(table(["policy", "calls", "calls / h", "turns / h", "turns vs default", "call p50 s", "call p95 s",
                         "turn p50 s", "turn p95 s", "evicted / prompt", "queue p50 s", "preemptions", "errors"], rows))
        md.append("")
    return "\n".join(md) + "\n", js


# -- smoke checks (V2) ----------------------------------------------------------------------------------------
def smoke(cond: str = "smoke", root: Path = CELLS) -> dict:
    cell = Cell(cond, root)
    out = {"servers": len(cell.servers), "sessions": len(cell.sessions)}
    calls = [r for r in cell.calls if r.get("rec")]
    exact = [r for r in calls if r.get("hit") is not None and r.get("cached") is not None]
    out["hit_equals_cache_read"] = sum(1 for r in exact if r["hit"] == r["cached"]) / len(exact) if exact else None
    out["hit_mismatch_examples"] = [(r["label"], r["call"], r["hit"], r["cached"]) for r in exact
                                    if r["hit"] != r["cached"]][:5]
    out["joined"] = len(calls) / max(1, sum(1 for r in cell.calls if r.get("lat") is not None))
    for srv in cell.servers:
        ev = sum(g[1] for r in srv.ev for g in r["g"])
        com = sum(s["com"] for s in srv.steps)
        pre = sum(len(rec["pre"]) for rec in srv.req.values())
        kv = json.loads((srv.dir / "kv_events.json").read_text()) if (srv.dir / "kv_events.json").exists() else {}
        final = srv.dir / "metrics_final.prom"
        prom = {}
        if final.exists():
            import mu_scrape
            prom = mu_scrape.parse_metrics(final.read_text())
        out[srv.dir.name] = {"evictions": ev, "commits": com, "preemptions": pre,
                             "kv_events_removed": kv.get("removed_hashes"), "kv_events_stored": kv.get("stored_hashes"),
                             "kv_events_gaps": kv.get("gaps"), "prom_preemptions": prom.get("vllm:num_preemptions_total"),
                             "prom_local_compute": prom.get("vllm:prompt_tokens_by_source_total{local_compute}"),
                             "our_fresh_evict": sum(s["pf"][0] + s["pf"][1] for s in srv.steps),
                             "errors": srv.errors[:3], "idle_evictions": sum(g[1] for r in srv.ev for g in r["g"]
                                                                             if g[4] == "idle")}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cond", default=",".join(LIVE_ORDER))
    ap.add_argument("--out", default=str(LIVE / "multiuser_tables.md"))
    ap.add_argument("--smoke", action="store_true", help="V2 checks on the smoke cell")
    ap.add_argument("--cells-root", default=str(CELLS))
    ap.add_argument("--replay", action="store_true", help="replay policy tables")
    args = ap.parse_args()
    if args.replay:
        md, js = replay_tables(Path(args.cells_root))
        REPLAY_OUT.mkdir(parents=True, exist_ok=True)
        (REPLAY_OUT / "replay_tables.md").write_text(md, encoding="utf-8")
        (REPLAY_OUT / "replay_tables.json").write_text(json.dumps(js, indent=1, default=str), encoding="utf-8")
        print(md)
        return
    if args.smoke:
        print(json.dumps(smoke(root=Path(args.cells_root)), indent=2, default=str))
        return
    md, js = analyze_cells([c for c in args.cond.split(",") if c], Path(args.cells_root))
    Path(args.out).write_text(md, encoding="utf-8")
    Path(args.out).with_suffix(".json").write_text(json.dumps(js, indent=1, default=str), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
