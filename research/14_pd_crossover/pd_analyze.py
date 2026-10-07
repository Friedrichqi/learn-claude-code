#!/usr/bin/env python3
"""Tables of research/14_pd_crossover (PLAN.md §9).

    python research/14_pd_crossover/pd_analyze.py levels   [--cells l2a,l2b]   # per-level metrics -> data/levels.json
    python research/14_pd_crossover/pd_analyze.py tables                        # T2-T10 from data/levels.json
    python research/14_pd_crossover/pd_analyze.py gates --cell s1               # G5 on smoke levels (+ lease rule)
    python research/14_pd_crossover/pd_analyze.py calib --calib data/calib/JOB  # T0 calibration table
    sbatch --export=NONE research/14_pd_crossover/pd_cpu.sbatch pd_analyze.py levels
Per call, the client capture (live: mu_session's requests.jsonl; replay: replay_calls.jsonl) is joined to the router
record and to the scheduler log of the instance(s) that served it, by (role, X-Request-Id): prefill (P) and decode (D)
instances log the same id. All instances share the node's CLOCK_MONOTONIC; wall time = monotonic + each log's offset.
Window of a level: W0 = level start + warm-up, W1 = W0 + window (the hard stop).
  throughput   calls with t_recv in [W0, W1] (lead: purpose lead / code ld; status ok)
  latency      lead calls with t_send >= W0 and t_recv <= W1: call time (client), TTFT = decode-side tok1 - t_send,
               TPOT = (fin - tok1) / (out - 1), ITL = gaps between token times (tok1, then u0 of the request's
               decode steps, 12's verified mapping)
  decomposition (s per lead call, sums to the call time): front end in, queue, prefill, [P out, hand-off, KV wait,
               D queue, D first step], pure decode, decode stall (step GPU time beyond the step model's pure-decode
               cost, charged to each decoding request: 13's S0 method; step model fitted per TP size over all logs
               analysed together), front end out
Crossovers (T7): a(N) = ln(M_X / M_B), sign-flipped for lower-is-better metrics. sigma: live = pooled run-to-run
sd of ln(metric) over the duplicate pairs (floor 0.03; max(., 0.07) if fewer than 4 pairs); replay throughput =
replay duplicate pairs (floor 0.01); replay latency = block bootstrap over slots on the calls completed by both arms.
N* by linear interpolation at sign changes; 90 % CI by Monte Carlo (10,000 draws); resolved if both bracketing points
have |a| / sigma >= 1.645.
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_analyze  # noqa: E402  research/11_multiuser_kv
import pd_workloads as pw  # noqa: E402
from tm_ceilings import pure_decode_cost  # noqa: E402  research/13_tiered_memory

DATA = pw.DATA
DROPPED_JSON = pw.DROPPED_JSON
DROPPED = pw.DROPPED
CELLS = DATA / "cells"
TABLES = DATA / "tables"
LEVELS_JSON = DATA / "levels.json"
FAMILIES = {"2L-live": (("l2a", "l2b"), "L", "live"), "2L-replay": (("r2a", "r2b"), "L", "replay"),
            "4L-live": (("l4a", "l4b"), "L", "live"), "2H-live": (("x2",), "H", "live"),
            "2T-live": (("x2",), "T", "live")}
METRICS = {"lead_h": 1, "call_p50": -1, "call_p95": -1, "tpot_p99": -1, "ttft_p95": -1}
MLABEL = {"lead_h": "lead calls/h", "call_p50": "call p50", "call_p95": "call p95", "tpot_p99": "TPOT p99",
          "ttft_p95": "TTFT p95"}
EXT = "vllm:prompt_tokens_by_source_total{external_kv_transfer}"
LOC = "vllm:prompt_tokens_by_source_total{local_compute}"
HIT = "vllm:prompt_tokens_by_source_total{local_cache_hit}"
COMPONENTS = ("fe_in", "queue", "prefill", "p_out", "handoff", "kv_wait", "d_queue", "d_first", "decode_pure",
              "decode_stall", "fe_out")


# -- helpers ------------------------------------------------------------------------------------------------------
def q(xs, p):
    xs = sorted(x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x)))
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.fmean(xs) if xs else None


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


class PDServer(mu_analyze.Server):
    """11's Server plus the records it skips: hw (12), pdhdr / pdh (14), pre, err."""

    def __init__(self, path: Path):
        captured: list = []
        orig = mu_analyze.read_jsonl

        def capture(p):
            recs = orig(p) if Path(p).exists() else []
            captured.append(recs)
            return recs
        mu_analyze.read_jsonl = capture
        try:
            super().__init__(path)
        finally:
            mu_analyze.read_jsonl = orig
        recs = next((r for r in captured if r), [])
        self.hw = [r for r in recs if r["k"] == "hw"]
        self.pdh = [r for r in recs if r["k"] == "pdh"]
        self.pdhdr = next((r for r in recs if r["k"] == "pdhdr"), {})
        self.pres = [r for r in recs if r["k"] == "pre"]
        self.lost: dict[int, float] = {}
        self.new_tok: dict[int, int] = defaultdict(int)       # request -> prompt tokens computed here
        for st in self.steps:
            for ch in st["pfl"]:
                self.new_tok[ch[0]] += ch[2]


_SERVERS: dict[str, PDServer] = {}


def server(path: Path) -> PDServer | None:
    key = str(path)
    if key not in _SERVERS:
        if not (path / "sched.jsonl.xz").exists() and not (path / "sched.jsonl").exists():
            return None
        _SERVERS[key] = PDServer(path)
    return _SERVERS[key]


# -- one level ----------------------------------------------------------------------------------------------------
class Level:
    def __init__(self, lv: Path):
        self.dir = lv
        self.meta = json.loads((lv / "level.json").read_text())
        self.name = self.meta["level"]
        self.cell = self.meta["cell"]
        self.pd = (pw.CONDITIONS.get(self.name) or {}).get("pd") or {}
        self.arm, self.n, self.wl, self.kind = self.pd.get("arm"), self.pd.get("n"), self.pd.get("wl"), self.pd.get("kind")
        self.dup = bool(self.pd.get("dup"))
        self.srv_dir = Path(self.meta["server_dir"])
        self.W0 = self.meta["start"] + self.meta["warmup_s"]
        self.W1 = self.meta["hard"]
        self.insts = json.loads((self.srv_dir / "instances.json").read_text())
        self.p_list = [i for i in self.insts if i["role"] == "P"]
        self.d_list = [i for i in self.insts if i["role"] == "D"]
        self.c_list = [i for i in self.insts if i["role"] == "C"]
        self.servers = {i["idx"]: server(self.srv_dir / f"i{i['idx']}") for i in self.insts}
        self.calls = self._calls()
        rids = {c["rid"] for c in self.calls}
        self.router = {r["rid"]: r for r in read_jsonl(self.srv_dir / "router.jsonl.xz") if r.get("rid") in rids}

    def _calls(self) -> list[dict]:
        out = []
        if self.kind in ("replay", "rsmoke"):
            for r in read_jsonl(self.dir / "replay_calls.jsonl"):
                out.append({"rid": r["rid"], "lead": r.get("code") == "ld", "t_send": r["t_send"], "t_recv": r["t_recv"],
                            "ok": r.get("status") == "ok", "label": r["label"], "call": r["call"],
                            "n_out": r.get("n_out")})
            return out
        for f in sorted(self.dir.glob("runs/*/*.requests.jsonl.xz")):
            for c in read_jsonl(f):
                if c.get("t_send") is None or c.get("t_recv") is None:
                    continue
                out.append({"rid": c.get("request_id"), "lead": c.get("purpose") == "lead", "t_send": c["t_send"],
                            "t_recv": c["t_recv"], "ok": "response" in c, "label": f.parent.name,
                            "call": c.get("call_index")})
        return out

    # per-call join and decomposition ------------------------------------------------------------------------------
    def serving(self, c: dict) -> dict | None:
        rr = self.router.get(c["rid"])
        if rr is None:
            return None
        if "p" in rr:
            p, d = self.p_list[rr["p"]], self.d_list[rr["d"]]
            ps, ds = self.servers.get(p["idx"]), self.servers.get(d["idx"])
            if ps is None or ds is None:
                return None
            prec, drec = ps.by_ext.get(c["rid"]), ds.by_ext.get(c["rid"])
            if prec is None or drec is None:
                return None
            return {"router": rr, "p": (ps, prec), "d": (ds, drec)}
        inst = (self.c_list or self.insts)[rr.get("b", 0)]
        cs = self.servers.get(inst["idx"])
        rec = cs.by_ext.get(c["rid"]) if cs else None
        return None if rec is None else {"router": rr, "c": (cs, rec)}

    @staticmethod
    def _first_adm(rec):
        firsts = [x for x in rec["adm"] if not x["res"]]
        return firsts[0] if firsts else None

    def decompose(self, c: dict, j: dict) -> dict | None:
        out = dict.fromkeys(COMPONENTS, 0.0)
        dsrv, drec = j["d"] if "d" in j else j["c"]
        fin, tok1 = drec["fin"], drec["tok1"]
        if fin is None or tok1 is None:
            return None
        if "p" in j:
            psrv, prec = j["p"]
            padm = self._first_adm(prec)
            dadm = self._first_adm(drec)
            if padm is None or dadm is None or prec["tok1"] is None:
                return None
            rr = j["router"]
            r_off = rr["w_in"] - rr["t_in"]
            out["fe_in"] = psrv.wall(prec["arr"]["t"]) - c["t_send"]
            out["queue"] = padm["t"] - prec["arr"]["t"]
            out["prefill"] = prec["tok1"] - padm["t"]
            out["p_out"] = (rr["t_p1"] + r_off) - psrv.wall(prec["tok1"])
            out["handoff"] = dsrv.wall(drec["arr"]["t"]) - (rr["t_p1"] + r_off)
            load = dadm.get("load") or 0.0
            out["kv_wait"] = load
            out["d_queue"] = max(0.0, (dadm["t"] - drec["arr"]["t"]) - load)
            out["d_first"] = tok1 - dadm["t"]
        else:
            adm = self._first_adm(drec)
            if adm is None:
                return None
            out["fe_in"] = dsrv.wall(drec["arr"]["t"]) - c["t_send"]
            out["queue"] = adm["t"] - drec["arr"]["t"]
            out["prefill"] = tok1 - adm["t"]
        dec = fin["t"] - tok1
        stall = min(dec, dsrv.lost.get(drec["arr"]["r"], 0.0))
        out["decode_stall"] = stall
        out["decode_pure"] = dec - stall
        out["fe_out"] = c["t_recv"] - dsrv.wall(fin["t"])
        out["total"] = c["t_recv"] - c["t_send"]
        out["resid"] = out["total"] - sum(out[k] for k in COMPONENTS)
        return out

    @staticmethod
    def token_times(srv, rec) -> list[float]:
        no = (rec["fin"] or {}).get("no") or 0
        if rec["tok1"] is None or no < 1:
            return []
        steps = [srv.step_by_n[n]["u0"] for n in rec["dec"][: max(0, no - 1)] if n in srv.step_by_n]
        return [rec["tok1"]] + steps

    # metrics ------------------------------------------------------------------------------------------------------
    def metrics(self) -> dict:
        W0, W1 = self.W0, self.W1
        span = max(1.0, W1 - W0)
        done = [c for c in self.calls if c["ok"] and W0 <= c["t_recv"] <= W1]
        lead_done = [c for c in done if c["lead"]]
        inside = [c for c in self.calls if c["ok"] and c["lead"] and c["t_send"] >= W0 and c["t_recv"] <= W1]
        m = {"level": self.name, "cell": self.cell, "arm": self.arm, "n": self.n, "wl": self.wl, "kind": self.kind,
             "dup": self.dup, "g": self.pd.get("g"), "lead_h": len(lead_done) * 3600 / span,
             "calls_h": len(done) * 3600 / span, "n_lead_window": len(inside),
             "errors": sum(1 for c in self.calls if not c["ok"])}
        times = [c["t_recv"] - c["t_send"] for c in inside]
        m["call_p50"], m["call_p95"], m["call_mean"] = q(times, 0.5), q(times, 0.95), mean(times)
        ttft, tpot, itl, outs, comps, prompts, news, edits = [], [], [], [], defaultdict(list), [], [], []
        he_tok, kvw = [], []
        per_call = {}
        for c in inside:
            j = self.serving(c)
            if j is None:
                continue
            dsrv, drec = j["d"] if "d" in j else j["c"]
            if drec["fin"] is None or drec["tok1"] is None:
                continue
            ttft.append(dsrv.wall(drec["tok1"]) - c["t_send"])
            no = drec["fin"]["no"]
            outs.append(no)
            if no >= 2:
                tpot.append((drec["fin"]["t"] - drec["tok1"]) / (no - 1))
            tt = self.token_times(dsrv, drec)
            itl += [b - a for a, b in zip(tt, tt[1:])]
            psrv, prec = j["p"] if "p" in j else j["c"]
            prompts.append(prec["arr"]["np"])
            news.append(psrv.new_tok.get(prec["arr"]["r"], 0))
            d = self.decompose(c, j)
            if d:
                for k, v in d.items():
                    comps[k].append(v)
            if "p" in j:
                adm = self._first_adm(drec)
                if adm:
                    he_tok.append(adm.get("he") or 0)
                    kvw.append(adm.get("load") or 0.0)
            per_call[(c["label"], c["call"])] = {"t": c["t_recv"] - c["t_send"],
                                                 "ttft": ttft[-1], "tpot": tpot[-1] if no >= 2 else None}
        m.update(ttft_p50=q(ttft, 0.5), ttft_p95=q(ttft, 0.95), tpot_p50=q(tpot, 0.5), tpot_p99=q(tpot, 0.99),
                 itl_p50=q(itl, 0.5), itl_p99=q(itl, 0.99), out_mean=mean(outs), prompt_mean=mean(prompts),
                 new_mean=mean(news), joined=len(comps.get("total", [])))
        m["decomp"] = {k: mean(v) for k, v in comps.items()}
        dec = (m["decomp"].get("decode_pure") or 0) + (m["decomp"].get("decode_stall") or 0)
        m["stall_share"] = (m["decomp"].get("decode_stall") or 0) / dec if dec else None
        m["xfer_tok"] = mean(he_tok)
        m["xfer_gib"] = (mean(he_tok) or 0) * pw.KV_BYTES_PER_TOKEN / (1 << 30) if he_tok else None
        m["kv_wait"] = mean(kvw)
        m["edit_share"] = self._edit_share()
        m["instances"] = self._instance_metrics()
        m["gpus"] = self._gpu_metrics()
        m["nixl"] = self._nixl_deltas()
        m["per_call"] = {f"{k[0]}|{k[1]}": v for k, v in per_call.items()} if self.kind == "replay" else None
        m["slots"] = self._slots() if self.kind == "replay" else None
        m["sessions"] = self._sessions()
        return m

    def _edit_share(self) -> float | None:
        """Share of window lead calls whose prompt diverges > 64 tokens before the end of the chain's previous one."""
        srvs = [s for s in self.servers.values() if s is not None]
        tot = hits = 0
        for s in srvs:
            role = s.pdhdr.get("role", "C")
            if role == "D":
                continue
            chains = defaultdict(list)
            for rec in s.req.values():
                chains[rec["arr"]["c"]].append(rec)
            for rs in chains.values():
                rs.sort(key=lambda r: r["arr"]["t"])
                for a, b in zip(rs, rs[1:]):
                    w = s.wall(b["arr"]["t"])
                    if not (self.W0 <= w <= self.W1):
                        continue
                    tot += 1
                    lcp = b["arr"].get("lcp") or 0
                    hits += lcp < a["arr"]["np"] - 64
        return hits / tot if tot else None

    def _instance_metrics(self) -> list[dict]:
        out = []
        for inst in self.insts:
            s = self.servers.get(inst["idx"])
            if s is None:
                continue
            lo, hi = self.W0 - s.offset, self.W1 - s.offset
            busy = pf_busy = 0.0
            pf_tok = ev_tok = 0
            for st in s.steps:
                g0, g1 = st.get("g0"), st.get("g1")
                if g0 is None or g1 is None:
                    continue
                ov = max(0.0, min(g1, hi) - max(g0, lo))
                busy += ov
                if st["pfl"]:
                    pf_busy += ov
                if lo <= g1 <= hi:
                    pf_tok += sum(ch[2] for ch in st["pfl"])
                    ev_tok += sum(ch[4] for ch in st["pfl"] if len(ch) > 4)
            hw = [r for r in s.hw if lo <= r["t"] <= hi and r.get("usable")]
            live = [r["live"] / r["usable"] for r in hw]
            pdh = [r for r in s.pdh if lo <= r["t"] <= hi]
            out.append({"idx": inst["idx"], "role": inst["role"], "tp": inst["tp"],
                        "busy": busy / max(1.0, hi - lo), "pf_share": pf_busy / busy if busy else None,
                        "recompute_share": ev_tok / pf_tok if pf_tok else None, "prefill_tok": pf_tok,
                        "live_mean": mean(live), "live_max": max(live) if live else None,
                        "held_blk_mean": mean([r["held_blk"] for r in pdh]),
                        "held_blk_max": max((r["held_blk"] for r in pdh), default=None),
                        "rkv_mean": mean([r["rkv"] for r in pdh]),
                        "preempt": sum(1 for r in s.pres if lo <= r["t"] <= hi),
                        "sched_errors": len(s.errors)})
        return out

    def _gpu_metrics(self) -> list[dict]:
        samp = self.srv_dir / "samp"
        if not (samp / "meta.json").exists():
            return []
        order = str(json.loads((samp / "meta.json").read_text())["args"]["gpus"]).split(",")
        fast = [r for r in read_jsonl(samp / "fast.jsonl.xz") if self.W0 <= r.get("wall", 0) <= self.W1 and r.get("gpu")]
        slow = [r for r in read_jsonl(samp / "slow.jsonl.xz") if self.W0 <= r.get("wall", 0) <= self.W1 and r.get("gpu")]
        out = []
        for j, g in enumerate(order):
            rec = {"gpu": g}
            for key in ("sm", "tensor", "dram"):
                vals = [r["gpu"][j].get(key) for r in fast if j < len(r["gpu"])]
                rec[key] = mean([v for v in vals if v is not None])
            pcie = [((r["gpu"][j].get("pcie_tx") or 0) + (r["gpu"][j].get("pcie_rx") or 0)) * (1 << 20) / 1e9
                    for r in fast if j < len(r["gpu"])]
            rec["pcie_gbs"], rec["pcie_gbs_p99"] = mean(pcie), q(pcie, 0.99)
            rates = []
            for a, b in zip(slow, slow[1:]):
                dt = b["wall"] - a["wall"]
                if dt > 0 and j < len(a["gpu"]):
                    tx = (b["gpu"][j]["nvl_tx_kib"] - a["gpu"][j]["nvl_tx_kib"]) * 1024 / dt / 1e9
                    rates.append(tx)
            rec["nvl_tx_gbs"], rec["nvl_tx_gbs_p99"] = mean(rates), q(rates, 0.99)
            out.append(rec)
        return out

    def _nixl_deltas(self) -> dict:
        try:
            m0 = json.loads((self.dir / "metrics_start.json").read_text())
            m1 = json.loads((self.dir / "metrics_end.json").read_text())
        except Exception:
            return {}
        out = Counter()
        for idx, a in m0.items():
            b = m1.get(idx) or {}
            a = a or {}
            for k in ("vllm:nixl_num_failed_transfers_total", "vllm:nixl_num_kv_expired_reqs_total",
                      "vllm:nixl_num_failed_notifications_total", EXT, LOC, HIT, "vllm:nixl_bytes_transferred_sum",
                      "vllm:nixl_xfer_time_seconds_sum", "vllm:nixl_bytes_transferred_count"):
                out[k.replace("vllm:", "")] += (b.get(k) or 0) - (a.get(k) or 0)
        return dict(out)

    def _slots(self) -> dict:
        src = (pw.CONDITIONS.get(self.name) or {}).get("replay", {}).get("src")
        plan = read_jsonl(DATA / "replay" / "plans" / str(src) / "plan.jsonl.xz") if src else []
        return {r["label"]: r["slot"] for r in plan}

    def _sessions(self) -> int | None:
        rows = read_jsonl(self.dir / "cell_log.jsonl")
        return sum(1 for r in rows if r.get("status") in ("ok", "budget") and self.W0 <= r.get("t1", 0) <= self.W1)


# -- step models and decode stall ---------------------------------------------------------------------------------
def fit_models(levels: list[Level]) -> dict[int, dict]:
    by_tp: dict[int, list] = defaultdict(list)
    seen = set()
    for lv in levels:
        for inst in lv.insts:
            s = lv.servers.get(inst["idx"])
            if s is not None and id(s) not in seen:
                seen.add(id(s))
                by_tp[inst["tp"]].append(s)
    models = {}
    for tp, srvs in by_tp.items():
        mu_analyze.step_features(srvs)
        models[tp] = mu_analyze.fit_step_model(srvs)
        coef = models[tp].get("coef")
        for s in srvs:
            s.lost = defaultdict(float)
            if coef is None:
                continue
            for st in s.steps:
                if st["pfl"] and st["dcr"] and st.get("gpu"):
                    lost = max(0.0, st["gpu"] - pure_decode_cost(coef, s, st))
                    for ri in st["dcr"]:
                        s.lost[ri] += lost
    return {tp: {k: v for k, v in m.items()} for tp, m in models.items()}


def find_levels(cells: list[str]) -> list[Level]:
    out = []
    for cell in cells:
        for lv in sorted((CELLS / cell / "levels").glob("pd_*")):
            if (lv / "WINDOW_DONE").exists() and (lv / "level.json").exists():
                out.append(Level(lv))
    return out


# -- crossovers ---------------------------------------------------------------------------------------------------
def sigma_pairs(rows: list[dict], kind: str) -> dict[str, tuple[float, int]]:
    """Pooled run-to-run sd of ln(metric) over duplicate pairs (a dup level and its main level)."""
    main = {(r["cell"], r["arm"], r["wl"], r["n"]): r for r in rows if not r["dup"] and r["kind"] == kind}
    pairs = [(main.get((r["cell"], r["arm"], r["wl"], r["n"])), r) for r in rows if r["dup"] and r["kind"] == kind]
    pairs = [(a, b) for a, b in pairs if a is not None]
    out = {}
    for met in METRICS:
        d = [math.log(a[met] / b[met]) for a, b in pairs if a.get(met) and b.get(met)]
        est = math.sqrt(sum(x * x for x in d) / (2 * len(d))) if d else 0.0
        if kind == "replay":
            s = max(est, 0.01)
        else:
            s = max(est, 0.03) if len(d) >= 4 else max(est, 0.07)
        out[met] = (s, len(d))
    return out


def curve(rows: list[dict], arm: str, met: str) -> dict[int, float]:
    vals = defaultdict(list)
    for r in rows:
        if r["arm"] == arm and r.get(met):
            vals[r["n"]].append(math.log(r[met]))
    return {n: math.exp(statistics.fmean(v)) for n, v in vals.items()}


def paired_sigma(rx: dict, rb: dict, met: str, boots: int = 1000) -> float | None:
    """Replay latency: block bootstrap over slots of ln(stat_B / stat_X) on calls both arms completed."""
    if not rx.get("per_call") or not rb.get("per_call"):
        return None
    key = {"call_p50": ("t", 0.5), "call_p95": ("t", 0.95), "tpot_p99": ("tpot", 0.99), "ttft_p95": ("ttft", 0.95)}
    if met not in key:
        return None
    field, p = key[met]
    common = [k for k in rx["per_call"] if k in rb["per_call"]]
    slots = rx.get("slots") or {}
    by_slot = defaultdict(list)
    for k in common:
        by_slot[slots.get(k.split("|")[0], k)].append(k)
    groups = list(by_slot.values())
    if len(groups) < 2:
        return None
    rng = random.Random(f"{rx['level']}|{rb['level']}|{met}")
    vals = []
    for _ in range(boots):
        pick = [k for _ in groups for k in groups[rng.randrange(len(groups))]]
        a = q([rx["per_call"][k][field] for k in pick], p)
        b = q([rb["per_call"][k][field] for k in pick], p)
        if a and b:
            vals.append(math.log(b / a))
    return statistics.pstdev(vals) if len(vals) > 10 else None


def crossings(ns: list[int], a: list[float], s: list[float], draws: int = 10000) -> list[dict]:
    out = []
    rng = np.random.default_rng(12345)
    for i in range(len(ns) - 1):
        if a[i] == 0 or (a[i] > 0) == (a[i + 1] > 0):
            continue
        n_lo, n_hi = ns[i], ns[i + 1]
        est = n_lo + (n_hi - n_lo) * a[i] / (a[i] - a[i + 1])
        x0 = rng.normal(a[i], s[i], draws)
        x1 = rng.normal(a[i + 1], s[i + 1], draws)
        cross = (x0 > 0) != (x1 > 0)
        ns_star = n_lo + (n_hi - n_lo) * x0[cross] / (x0[cross] - x1[cross])
        lo, hi = (float(np.percentile(ns_star, 5)), float(np.percentile(ns_star, 95))) if cross.any() else (None, None)
        out.append({"type": "low" if a[i] < 0 else "high", "n_star": est, "ci": [lo, hi],
                    "p_none": float(1 - cross.mean()), "n_lo": n_lo, "n_hi": n_hi,
                    "resolved": abs(a[i]) / s[i] >= 1.645 and abs(a[i + 1]) / s[i + 1] >= 1.645})
    return out


def comparisons(rows: list[dict]) -> list[dict]:
    out = []
    for fam, (cells, wl, kind) in FAMILIES.items():
        frows = [r for r in rows if r["cell"] in cells and r["wl"] == wl and r["kind"] == kind]
        if not frows:
            continue
        arms = sorted({r["arm"] for r in frows})
        pds = [a for a in arms if a not in pw.COLOCATED]
        cols = [a for a in arms if a in pw.COLOCATED]
        sig = sigma_pairs(rows, kind)
        g = frows[0]["g"]
        for met, sign in METRICS.items():
            curves = {a: curve(frows, a, met) for a in arms}
            for x in pds:
                bases = cols + ["best colocated"]
                for b in bases:
                    ns, av, sv, who = [], [], [], []
                    for n in sorted(curves[x]):
                        if b == "best colocated":
                            cand = [(c, curves[c][n]) for c in cols if n in curves[c]]
                            if not cand:
                                continue
                            bc, bv = (max if sign > 0 else min)(cand, key=lambda t: t[1])
                        else:
                            if n not in curves[b]:
                                continue
                            bc, bv = b, curves[b][n]
                        av.append(sign * math.log(curves[x][n] / bv))
                        sd = None
                        if kind == "replay" and met != "lead_h":
                            rx = next((r for r in frows if r["arm"] == x and r["n"] == n and not r["dup"]), None)
                            rb = next((r for r in frows if r["arm"] == bc and r["n"] == n and not r["dup"]), None)
                            sd = paired_sigma(rx, rb, met) if rx and rb else None
                        sv.append(sd if sd else sig[met][0] * math.sqrt(2))
                        ns.append(n)
                        who.append(bc)
                    if len(ns) < 2:
                        continue
                    out.append({"family": fam, "g": g, "pd": x, "base": b, "metric": met, "ns": ns, "a": av,
                                "sigma": sv, "who": who, "cross": crossings(ns, av, sv)})
    return out


# -- tables -------------------------------------------------------------------------------------------------------
def f(x, d=2, pct=False):
    if x is None:
        return "—"
    return f"{100 * x:.{max(0, d - 1)}f} %" if pct else (f"{x:,.0f}" if d == 0 else f"{x:.{d}f}")


def t_levels(rows: list[dict], title: str) -> list[str]:
    md = [f"## {title}", "", "| level | arm | N | lead calls/h | call p50 / p95 s | TTFT p50 / p95 s | TPOT p50 / p99 ms "
          "| ITL p99 ms | decode stall share | prompt / new / out tokens | history edits | calls in window | errors |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["n"], r["arm"], r["dup"])):
        md.append(f"| {r['level']} | {pw.display(r['arm'])} | {r['n']} | {f(r['lead_h'], 0)} | {f(r['call_p50'])} / "
                  f"{f(r['call_p95'])} | {f(r['ttft_p50'])} / {f(r['ttft_p95'])} | "
                  f"{f(r['tpot_p50'] and 1000 * r['tpot_p50'], 1)} / {f(r['tpot_p99'] and 1000 * r['tpot_p99'], 1)} | "
                  f"{f(r['itl_p99'] and 1000 * r['itl_p99'], 1)} | {f(r['stall_share'], 1, True)} | "
                  f"{f(r['prompt_mean'], 0)} / {f(r['new_mean'], 0)} / {f(r['out_mean'], 0)} | "
                  f"{f(r['edit_share'], 1, True)} | {r['n_lead_window']} | {r['errors']} |")
    return md + [""]


def t_decomp(rows: list[dict]) -> list[str]:
    md = ["## T6 · Where a lead call's time goes (mean s per call)", "",
          "| level | arm | N | " + " | ".join(COMPONENTS) + " | total | residual |",
          "|---|---|---|" + "---|" * (len(COMPONENTS) + 2)]
    for r in sorted(rows, key=lambda r: (r["kind"], r["g"], r["wl"], r["n"], r["arm"])):
        d = r["decomp"]
        md.append(f"| {r['level']} | {pw.display(r['arm'])} | {r['n']} | "
                  + " | ".join(f(d.get(k)) for k in COMPONENTS) + f" | {f(d.get('total'))} | {f(d.get('resid'))} |")
    return md + [""]


def binding(r: dict) -> str:
    if r is None:
        return "—"
    inst = r["instances"]
    p = [i for i in inst if i["role"] == "P"]
    d = [i for i in inst if i["role"] == "D"]
    tags = []
    if p and max((i["busy"] or 0) for i in p) >= 0.90:
        tags.append("P compute")
    if p and max((i["recompute_share"] or 0) for i in p) > 0.05:
        tags.append("P cache")
    if d and (max((i["live_max"] or 0) for i in d) >= 0.95 or max((i["rkv_mean"] or 0) for i in d) > 0.5
              or (r["decomp"].get("d_queue") or 0) > 0.1 * (r["decomp"].get("total") or 1e9)):
        tags.append("D capacity")
    if (r["decomp"].get("kv_wait") or 0) > 0.1 * (r["decomp"].get("total") or 1e9):
        tags.append("transfer")
    return ", ".join(tags) or "none of the four"


def t_cross(comps: list[dict], rows: list[dict]) -> list[str]:
    md = ["## T7 · Turning points", "",
          "| family | P/D arm | baseline | metric | a(N) by N (P/D better > 0) | N*_low | N*_high | 90 % CI (high) | "
          "resolved | per-GPU N*_high | binds at N*_high |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    idx = {(r["cell"], r["arm"], r["n"], r["kind"]): r for r in rows if not r["dup"]}
    for c in comps:
        lows = [x for x in c["cross"] if x["type"] == "low"]
        highs = [x for x in c["cross"] if x["type"] == "high"]
        hi = highs[0] if highs else None
        prof = " ".join(f"{n}:{a:+.2f}" for n, a in zip(c["ns"], c["a"]))
        bind = "—"
        if hi:
            cells = FAMILIES[c["family"]][0]
            r = next((idx.get((cell, c["pd"], hi["n_hi"], FAMILIES[c["family"]][2])) for cell in cells
                      if idx.get((cell, c["pd"], hi["n_hi"], FAMILIES[c["family"]][2]))), None)
            bind = binding(r)
        md.append(f"| {c['family']} | {pw.display(c['pd'])} | {pw.display(c['base'])} | {MLABEL[c['metric']]} | {prof} | "
                  f"{', '.join(f(x['n_star'], 1) for x in lows) or '—'} | {f(hi['n_star'], 1) if hi else '—'} | "
                  f"{(f(hi['ci'][0], 1) + '-' + f(hi['ci'][1], 1)) if hi and hi['ci'][0] is not None else '—'} | "
                  f"{'yes' if hi and hi['resolved'] else ('no' if hi else '—')} | "
                  f"{f(hi['n_star'] / c['g'], 1) if hi else '—'} | {bind} |")
    md += ["", "*Key.* **a(N)** = ln(P/D arm / baseline) of the metric, sign-flipped for latencies, so a > 0 means "
           "the P/D arm is better at that N. **N*_low**: P/D starts winning; **N*_high**: P/D stops winning "
           "(linear interpolation between grid points). **resolved**: both bracketing points are at least 1.645 "
           "sigma from 0. **binds**: P compute (a P instance >= 90 % busy), P cache (> 5 % of P prefill is "
           "recompute after eviction), D capacity (D pool >= 95 % live, requests waiting for KV, or D queueing "
           "> 10 % of the call), transfer (KV wait > 10 % of the call), at the grid point just past N*_high.", ""]
    return md


def t_regime(rows: list[dict]) -> list[str]:
    md = ["## T8 · Regime map: which arm wins at each N", ""]
    sig = {k: sigma_pairs(rows, k) for k in ("live", "replay")}
    for fam, (cells, wl, kind) in FAMILIES.items():
        frows = [r for r in rows if r["cell"] in cells and r["wl"] == wl and r["kind"] == kind and not r["dup"]]
        if not frows:
            continue
        ns = sorted({r["n"] for r in frows})
        md += [f"**{fam}**", "", "| metric | " + " | ".join(f"N={n}" for n in ns) + " |", "|---|" + "---|" * len(ns)]
        for met, sign in METRICS.items():
            cells_txt = []
            for n in ns:
                cand = sorted(((r[met], r["arm"]) for r in frows if r["n"] == n and r.get(met)),
                              reverse=sign > 0)
                if not cand:
                    cells_txt.append("—")
                    continue
                best = pw.display(cand[0][1])
                tie = len(cand) > 1 and abs(math.log(cand[0][0] / cand[1][0])) < 1.645 * sig[kind][met][0] * math.sqrt(2)
                cells_txt.append(best + (f" ~ {pw.display(cand[1][1])}" if tie else ""))
            md.append(f"| {MLABEL[met]} | " + " | ".join(cells_txt) + " |")
        md.append("")
    md += ["*Key.* Winner per N; `a ~ b` = the best two are within 1.645·sigma·√2 (a tie).", ""]
    return md


def t_mech(rows: list[dict]) -> list[str]:
    md = ["## T9 · Mechanism per P/D level", "",
          "| level | N | P busy (max) | P prefill share | P recompute share | P held blocks mean / max | D pool live mean / "
          "max | D waiting for KV | KV wait s | D queue s | tokens / GiB pulled per call | NVLink TX GB/s (max GPU) | "
          "PCIe GB/s (max GPU) | NIXL failed / expired |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["arm"] not in pw.COLOCATED), key=lambda r: (r["kind"], r["g"], r["n"], r["arm"])):
        p = [i for i in r["instances"] if i["role"] == "P"]
        d = [i for i in r["instances"] if i["role"] == "D"]
        mx = lambda xs, k: max((x[k] for x in xs if x.get(k) is not None), default=None)  # noqa: E731
        g = r["gpus"] or []
        nx = r["nixl"] or {}
        md.append(f"| {r['level']} | {r['n']} | {f(mx(p, 'busy'), 1, True)} | {f(mx(p, 'pf_share'), 1, True)} | "
                  f"{f(mx(p, 'recompute_share'), 1, True)} | {f(mx(p, 'held_blk_mean'), 0)} / {f(mx(p, 'held_blk_max'), 0)} | "
                  f"{f(mx(d, 'live_mean'), 1, True)} / {f(mx(d, 'live_max'), 1, True)} | {f(mx(d, 'rkv_mean'))} | "
                  f"{f(r['decomp'].get('kv_wait'), 3)} | {f(r['decomp'].get('d_queue'), 3)} | "
                  f"{f(r['xfer_tok'], 0)} / {f(r['xfer_gib'])} | {f(max((x['nvl_tx_gbs'] or 0) for x in g) if g else None)} | "
                  f"{f(max((x['pcie_gbs'] or 0) for x in g) if g else None)} | "
                  f"{f(nx.get('nixl_num_failed_transfers_total'), 0)} / {f(nx.get('nixl_num_kv_expired_reqs_total'), 0)} |")
    return md + [""]


def t_scaling(rows: list[dict]) -> list[str]:
    md = ["## T10 · Scaling checks and replay fidelity", "",
          "| check | N | value A | value B | B / A |", "|---|---|---|---|---|"]
    by = {(r["arm"], r["n"], r["kind"], r["wl"]): r for r in rows if not r["dup"]}
    for n in (2, 4, 8, 12, 16, 24):
        a, b = by.get(("1p1d", n, "live", "L")), by.get(("2p2d", 2 * n, "live", "L"))
        if a and b:
            md.append(f"| 2p2d at 2N vs 1p1d at N, lead calls/h per GPU | {n} | {f(a['lead_h'] / 2, 0)} | "
                      f"{f(b['lead_h'] / 4, 0)} | {f(b['lead_h'] / 2 / a['lead_h'])} |")
        a, b = by.get(("dp2", n, "live", "L")), by.get(("dp4", 2 * n, "live", "L"))
        if a and b:
            md.append(f"| dp4 at 2N vs dp2 at N, lead calls/h per GPU | {n} | {f(a['lead_h'] / 2, 0)} | "
                      f"{f(b['lead_h'] / 4, 0)} | {f(b['lead_h'] / 2 / a['lead_h'])} |")
    a, b = by.get(("dp2", 8, "live", "L")), by.get(("dp2", 8, "replay", "L"))
    if a and b:
        def eng(r):
            return (r["decomp"].get("total") or 0) - (r["decomp"].get("fe_in") or 0) - (r["decomp"].get("fe_out") or 0)
        md.append(f"| replay fidelity (G7): engine time per lead call, live vs replay dp2 | 8 | {f(eng(a))} | "
                  f"{f(eng(b))} | {f(eng(b) / eng(a) if eng(a) else None)} |")
        pa = next((i['pf_share'] for i in a['instances'] if i.get('pf_share') is not None), None)
        pb = next((i['pf_share'] for i in b['instances'] if i.get('pf_share') is not None), None)
        md.append(f"| replay fidelity (G7): prefill share of GPU busy | 8 | {f(pa, 1, True)} | {f(pb, 1, True)} | — |")
    return md + [""]


def cmd_levels(a) -> int:
    cells = a.cells.split(",") if a.cells else [c for c in pw.CELLS if (CELLS / c / "levels").exists()]
    levels = find_levels(cells)
    models = fit_models(levels)
    rows = []
    for lv in levels:
        try:
            rows.append(lv.metrics())
        except Exception as exc:                          # one broken level must not hide the rest
            print(f"[pd_analyze] {lv.name}: {exc!r}", file=sys.stderr)
    out = Path(a.out)
    old = json.loads(out.read_text()) if out.exists() and a.cells else {"rows": [], "models": {}}
    keep = [r for r in old["rows"] if r["cell"] not in cells]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": keep + rows, "models": {**old.get("models", {}), **{str(k): v for k, v in models.items()}}},
                              default=str))
    print(f"[pd_analyze] {len(rows)} levels from {cells} -> {out}")
    return 0


def cmd_tables(a) -> int:
    d = json.loads(Path(a.levels).read_text())
    rows = [r for r in d["rows"] if r["kind"] in ("live", "replay")]
    comps = comparisons(rows)
    TABLES.mkdir(parents=True, exist_ok=True)
    md = ["# research/14_pd_crossover · results tables", "",
          "Generated by `pd_analyze.py tables` from `data/levels.json`. Step models per TP size: "
          + "; ".join(f"TP{k} R² {v.get('r2', 0):.3f} (n = {v.get('n')})" for k, v in d.get("models", {}).items()
                      if v.get("coef")), ""]
    for title, sel in (("T2 · 2 GPUs, live, LIGHT", lambda r: r["g"] == 2 and r["kind"] == "live" and r["wl"] == "L"),
                       ("T3 · 4 GPUs, live, LIGHT", lambda r: r["g"] == 4 and r["kind"] == "live"),
                       ("T4 · 2 GPUs, paired replay", lambda r: r["kind"] == "replay"),
                       ("T5 · 2 GPUs, live, HEAVY and think time", lambda r: r["wl"] in ("H", "T"))):
        sub = [r for r in rows if sel(r)]
        if sub:
            md += t_levels(sub, title)
    md += t_decomp(rows) + t_cross(comps, rows) + t_regime(rows) + t_mech(rows) + t_scaling(rows)
    sig = {k: sigma_pairs(rows, k) for k in ("live", "replay")}
    md += ["## Noise", "", "| kind | " + " | ".join(MLABEL[m] for m in METRICS) + " |", "|---|" + "---|" * len(METRICS)]
    for k, s in sig.items():
        md.append(f"| {k} | " + " | ".join(f"{v[0]:.3f} ({v[1]} pairs)" for v in s.values()) + " |")
    (TABLES / "results_tables.md").write_text("\n".join(md) + "\n")
    (DATA / "crossovers.json").write_text(json.dumps(comps, indent=1))
    print(f"[pd_analyze] tables -> {TABLES / 'results_tables.md'} ({len(comps)} comparisons)")
    return 0


def cmd_gates(a) -> int:
    """Smoke-cell gates (PLAN.md §7). G5: every smoke level finished its window with 0 router errors, 0 NIXL failed
    transfers, 0 NIXL lease expiries and no STALL. Sanity of the analysis on the real logs: >= 20 lead calls in the
    window, >= 50 % of them joined to the scheduler logs, mean |residual| of the decomposition < 0.05 s, and P/D levels
    show KV pulled. s3 also: G6 (mixed-TP correctness, 2p1d-dtp2) -> data/dropped_arms.json on failure. All pass ->
    data/PASS_<cell>; an expiry with --apply raises the NIXL lease to 120 s (a rerun of the smoke cell is then needed)."""
    rows, ok_all, expired_any = [], True, False
    for lv in sorted((CELLS / a.cell / "levels").glob("pd_*")):
        if lv.name.startswith("aborted_"):
            continue
        rs = json.loads((lv / "router_stats.json").read_text()) if (lv / "router_stats.json").exists() else {}
        st = rs.get("stats", {})
        rec = {"level": lv.name, "window_done": (lv / "WINDOW_DONE").exists(), "stall": (lv / "STALL").exists(),
               "router_errors": st.get("errors", 0) + st.get("p_errors", 0), "p_no_params": st.get("p_no_params", 0),
               "calls": sum(v for k, v in st.items() if k[:1] in "bd" and k[1:].isdigit())}
        try:
            lev = Level(lv)
            nx = lev._nixl_deltas()
            m = lev.metrics()
            rec.update(nixl_failed=nx.get("nixl_num_failed_transfers_total", 0),
                       nixl_expired=nx.get("nixl_num_kv_expired_reqs_total", 0), lead_window=m["n_lead_window"],
                       joined=m["joined"], resid=(m["decomp"] or {}).get("resid"), xfer_tok=m["xfer_tok"],
                       pd=m["arm"] not in pw.COLOCATED, lead_h=m["lead_h"], call_p50=m["call_p50"])
        except Exception as exc:
            rec.update(error=repr(exc)[:300])
        sane = ("error" not in rec and rec.get("lead_window", 0) >= 20 and rec["joined"] >= 0.5 * rec["lead_window"]
                and rec["resid"] is not None and abs(rec["resid"]) < 0.05 and (not rec["pd"] or (rec["xfer_tok"] or 0) > 0))
        rec["sane"] = bool(sane)
        rec["pass"] = bool(rec["window_done"] and not rec["stall"] and rec["router_errors"] == 0
                           and rec.get("nixl_failed", 1) == 0 and rec.get("nixl_expired", 1) == 0 and rec["calls"] > 0
                           and sane)
        ok_all &= rec["pass"]
        expired_any |= (rec.get("nixl_expired") or 0) > 0
        rows.append(rec)
    ok_all &= bool(rows)
    out = {"pass": ok_all, "levels": rows}
    if a.cell == "s3":
        # secondary 4-GPU arms that fail their smoke level are dropped (reported), not allowed to block the core sweep
        bad_sec = sorted({r["level"].split("_")[1] for r in rows if not r["pass"]} & set(pw.SEC4))
        bad_core = [r["level"] for r in rows if not r["pass"] and r["level"].split("_")[1] not in pw.SEC4]
        ok_all = bool(rows) and not bad_core
        out.update(pass_=ok_all, dropped_secondary=bad_sec, failed_core=bad_core)
        if bad_sec:
            DROPPED_JSON.write_text(json.dumps(sorted(set(json.loads(DROPPED_JSON.read_text()) if DROPPED_JSON.exists()
                                                          else []) | set(bad_sec))))
        out["pass"] = ok_all
        g6 = None
        for f in sorted((DATA / "calib").glob("*/calib_2p1dt2.json")):
            corr = json.loads(f.read_text()).get("correct") or []
            ties = [c for c in corr if not c["all_same"]]
            first = sum(c["first_same"] for c in corr)
            g6 = bool(corr) and first >= len(corr) - 1 and all(c.get("div_margin") is not None and c["div_margin"] <= 0.5
                                                                for c in ties)
        out["G6"] = g6
        if not g6:
            cur = set(json.loads(DROPPED_JSON.read_text())) if DROPPED_JSON.exists() else set()
            DROPPED_JSON.write_text(json.dumps(sorted(cur | {"2p1dt2"})))
            out["dropped"] = ["2p1dt2"]
    if expired_any and a.apply:
        dec = pw.decisions()
        dec["kv_lease_duration"] = 120
        pw.DECISIONS_JSON.write_text(json.dumps(dec, indent=1))
    (DATA / f"gates_{a.cell}.json").write_text(json.dumps(out, indent=1, default=str))
    if ok_all:
        (DATA / f"PASS_{a.cell}").write_text(json.dumps({"t": time.strftime("%FT%T")}) + "\n")
    for r in rows:
        print(json.dumps(r, default=str))
    print(f"gates {a.cell}: {'PASS' if ok_all else 'FAIL'}" + (" (lease raised to 120 s)" if expired_any and a.apply else ""))
    return 0 if ok_all else 1


def cmd_calib(a) -> int:
    cd = Path(a.calib)
    g = json.loads((cd / "gates.json").read_text()) if (cd / "gates.json").exists() else {}
    cal = json.loads((cd / "calib_A.json").read_text()) if (cd / "calib_A.json").exists() else {}
    md = ["# T0 · S1 calibration and gates", "", f"Source: `{cd}`.", "",
          "| tokens | P call s (median) | D call s (median) | NIXL GB/s | tokens pulled (median) |", "|---|---|---|---|---|"]
    by = defaultdict(list)
    for x in cal.get("transfer", []):
        by[x["n"]].append(x)
    for n, xs in sorted(by.items()):
        rate = [x["nixl_bytes"] / x["nixl_s"] / 1e9 for x in xs if x["nixl_s"]]
        md.append(f"| {n:,} | {f(q([x['p_s'] for x in xs], .5), 3)} | {f(q([x['d_s'] for x in xs], .5), 3)} | "
                  f"{f(q(rate, .5), 1)} | {f(q([x['ext_tokens'] for x in xs], .5), 0)} |")
    md += ["", "| gate | pass | detail |", "|---|---|---|"]
    for k in ("G1", "G2", "G3", "G4"):
        v = g.get(k, {})
        md.append(f"| {k} | {v.get('pass')} | {json.dumps({kk: vv for kk, vv in v.items() if kk not in ('pass', 'items')})} |")
    for v, r in (g.get("runs") or {}).items():
        md.append(f"| G1 run {v} | {r.get('ok')} | NVLink/NIXL {f(r.get('nvl_ratio'))}, PCIe share "
                  f"{f(r.get('pcie_share'), 3)}, D s at 8k / 24k {r.get('d_s_median', {}).get('8192')} / "
                  f"{r.get('d_s_median', {}).get('24576')} |")
    for name in ("hop.json",):
        if (cd / name).exists():
            h = json.loads((cd / name).read_text())
            md.append(f"| router hop | — | {h['overhead_ms_p50']:.2f} ms (p50; direct {h['direct_ms_p50']:.1f}, "
                      f"routed {h['routed_ms_p50']:.1f}) |")
    for name in sorted(cd.glob("probe_*.json")):
        p = json.loads(name.read_text())
        md.append(f"| {name.stem} | — | {json.dumps({k: p[k] for k in list(p)[:6]}, default=str)[:400]} |")
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "T0_calibration.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("levels")
    p.add_argument("--cells", default=None)
    p.add_argument("--out", default=str(LEVELS_JSON))
    p = sub.add_parser("tables")
    p.add_argument("--levels", default=str(LEVELS_JSON))
    p = sub.add_parser("gates")
    p.add_argument("--cell", required=True)
    p.add_argument("--apply", action="store_true", help="raise the NIXL lease to 120 s if any expiry happened")
    p = sub.add_parser("calib")
    p.add_argument("--calib", required=True)
    a = ap.parse_args()
    return {"levels": cmd_levels, "tables": cmd_tables, "gates": cmd_gates, "calib": cmd_calib}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
