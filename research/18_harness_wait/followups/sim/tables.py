#!/usr/bin/env python3
"""Every simulator number in FOLLOWUPS.md, from the final kvsim.py: mean of three session orders (seeds 18-20).

Run from research/18_harness_wait/data/followups/ (the session scripts live there):
    PY=/data/fritz/venv/bin/python
    $PY ../../followups/sim/extract.py w08_none w16_sgate w32_sgate      # scripts.pkl
    $PY ../../followups/sim/variants.py                                  # scripts_appendonly.pkl
    $PY ../../followups/sim/tables.py --transforms                       # the other script variants
    PYTHONPATH=../../followups/sim $PY ../../followups/sim/tables.py > ../../followups/sim_tables.md
"""
from __future__ import annotations

import copy
import pickle
import random
import sys
from multiprocessing import Pool

import numpy as np

from kvsim import Sim, load

SEEDS = (18, 19, 20)
P0, NAT, FP8 = 16136, 20011, 32272
S = "scripts.pkl"

SECTIONS = [
    ("Validation against the measured cells", [
        (S, P0, 8, "none"), (S, P0, 16, "none"), (S, P0, 32, "none"), (S, P0, 16, "gate:8"), (S, P0, 32, "gate:8"),
        (S, P0, 16, "backoff:mid"), (S, P0, 32, "backoff:mid"), (S, P0, 16, "share:0.9:mid"),
        (S, P0, 32, "share:0.9:mid"), (S, P0, 16, "share:0.9:start+mid")]),
    ("Signal realism (C1): backoff on a stock-/metrics-like signal (0.5-s samples, KV noise sd 0.2); share rule on a stale status", [
        (S, P0, 16, "backoff:mid:0.2"), (S, P0, 32, "backoff:mid:0.2"),
        (S, P0, 16, "share:0.9:mid:600:0.5:10"), (S, P0, 32, "share:0.9:mid:600:0.5:10")]),
    ("Request-level server policies (no session gate)", [
        (S, P0, 16, "qpin:0.5"), (S, P0, 32, "qpin:0.5"), (S, P0, 16, "qlru"), (S, P0, 32, "qlru"),
        (S, P0, 16, "lpm:120"), (S, P0, 32, "lpm:120"), (S, P0, 16, "phase"), (S, P0, 32, "phase")]),
    ("Client-side rules: hold points, margin, a client-only signal", [
        (S, P0, 16, "share:0.9:start"), (S, P0, 32, "share:0.9:start:3600"), (S, P0, 32, "share:0.9:start+mid:3600"),
        (S, P0, 16, "share:0.7:mid"), (S, P0, 16, "share:1.0:mid"), (S, P0, 16, "share:1.2:mid"),
        (S, P0, 32, "share:1.1:mid"), (S, P0, 16, "pff:60"), (S, P0, 32, "pff:60")]),
    ("A client-only gate (F7): holds on a probe signal (queued requests only) or the stock backoff signal", [
        (S, P0, 16, "probe:start"), (S, P0, 32, "probe:start"), (S, P0, 16, "backoff:start"), (S, P0, 32, "backoff:start"),
        (S, P0, 16, "probe:start+mid"), (S, P0, 32, "probe:start+mid")]),
    ("Capacity: gate size, a working-set gate, bigger pools", [
        (S, P0, 16, "gate:10"), (S, P0, 16, "gate:12"), (S, P0, 16, "wsgate:0.9"), (S, P0, 32, "wsgate:0.9"),
        (S, NAT, 16, "none"), (S, NAT, 32, "none"), (S, NAT, 32, "gate:12"),
        (S, FP8, 16, "none"), (S, FP8, 32, "none"), (S, FP8, 32, "gate:16")]),
    ("The harness's own rewrites (micro_compact) and a think-pause offload bound", [
        ("scripts_batchc4.pkl", P0, 8, "none"), ("scripts_batchc4.pkl", P0, 16, "gate:8"),
        ("scripts_batchc4.pkl", P0, 32, "gate:8"), ("scripts_batchc8.pkl", P0, 8, "none"),
        ("scripts_batchc8.pkl", P0, 16, "gate:8"), ("scripts_batchc8.pkl", P0, 32, "gate:8"),
        ("scripts_appendonly.pkl", P0, 8, "none"), ("scripts_appendonly.pkl", P0, 16, "gate:8"),
        ("scripts_appendonly.pkl", P0, 32, "gate:8"), ("scripts_appendonly.pkl", FP8, 32, "gate:20"),
        ("scripts_offload.pkl", P0, 16, "gate:8"), ("scripts_offload.pkl", P0, 32, "gate:8"),
        ("scripts_dedup77.pkl", P0, 8, "none"), ("scripts_dedup77.pkl", P0, 16, "gate:8"),
        ("scripts_dedup77.pkl", P0, 32, "gate:8")]),
    ("A model-called wait: loops of short waits vs one blocking wait", [
        (S, P0, 8, "mwait:0.03:1:0.97:loop"), (S, P0, 8, "mwait:0.03:1:0.97:block"),
        (S, P0, 16, "mwait:0.042:5:0.9:loop"), (S, P0, 16, "mwait:0.042:5:0.9:block"),
        (S, P0, 16, "mwait:0.30:5:0:block"), (S, P0, 32, "mwait:0.05:10:0.9:loop"),
        (S, P0, 32, "mwait:0.05:10:0.9:block"), (S, P0, 32, "mwait:0.30:10:0:block")]),
    ("Partial adoption: a share of the sessions run the rule (start+mid), the rest do not", [
        (S, P0, 16, "mix:0.9:start+mid:0.5"), (S, P0, 16, "mix:0.9:start+mid:0.75"),
        (S, P0, 32, "mix:0.9:start+mid:0.5"), (S, P0, 32, "mix:0.9:start+mid:0.75")]),
    ("Gate admission order and turn latency", [
        (S, P0, 32, "gate:8"), (S, P0, 32, "gate:8:las"), (S, P0, 32, "gate:8:srpt")]),
    ("F3a and the harness rules: share rule and backoff (realistic signal) on the deduplicated scripts", [
        ("scripts_dedup77.pkl", P0, 16, "share:0.9:mid"), ("scripts_dedup77.pkl", P0, 32, "share:0.9:mid"),
        ("scripts_dedup77.pkl", P0, 16, "backoff:mid:0.2"), ("scripts_dedup77.pkl", P0, 32, "backoff:mid:0.2")]),
    ("The levers stacked at N=32: gate, then F3a (deduplicated re-reads), then an FP8-sized pool with a larger K", [
        ("scripts_dedup77.pkl", FP8, 32, "gate:16"), ("scripts_dedup77.pkl", FP8, 32, "gate:20")]),
    ("Think pauses 4x longer (about 2 min at the median)", [
        ("scripts_think4.pkl", P0, 12, "none"), ("scripts_think4.pkl", P0, 16, "none"), ("scripts_think4.pkl", P0, 24, "none"),
        ("scripts_think4.pkl", P0, 12, "gate:8"), ("scripts_think4.pkl", P0, 16, "gate:8"), (S, P0, 12, "none")]),
    ("Long tools: 10 % of tool calls take 10-60 s more", [
        ("scripts_longtools.pkl", P0, 16, "none"), ("scripts_longtools.pkl", P0, 32, "none"),
        ("scripts_longtools.pkl", P0, 32, "gate:8"), ("scripts_longtools.pkl", P0, 32, "share:0.9:start"),
        ("scripts_longtools.pkl", P0, 32, "share:0.9:start+aftlong")]),
]


def transforms():
    """The script variants: costed batched clearing (B = 4, 8), the think-pause offload flag, long tools."""
    s = pickle.load(open(S, "rb"))
    for B in (4, 8):
        b = copy.deepcopy(s)
        for sc in b:
            prev, batch = None, []
            for c in sc["calls"]:
                if prev is not None:
                    want = min(c["np"], prev["np"] + prev["out"])
                    if c["ideal"] < want - 1024:                 # a prefix break (micro_compact rewrote history)
                        batch.append(c["ideal"])
                        if len(batch) < B:
                            c["ideal"] = want                     # deferred: append-only this time
                        else:
                            c["ideal"], batch = min(batch), []    # the batch's rewrite starts at its oldest result
                prev = c
        pickle.dump(b, open(f"scripts_batchc{B}.pkl", "wb"))
    o = copy.deepcopy(s)
    for sc in o:
        for i, c in enumerate(sc["calls"]):
            if c["start"] and i > 0:
                c["offload"] = True
    pickle.dump(o, open("scripts_offload.pkl", "wb"))
    d, rng = copy.deepcopy(s), random.Random(11)        # F3a: deduplication removes 77 % of the rewrite volume (replay)
    for sc in d:
        prev = None
        for c in sc["calls"]:
            if prev is not None:
                want = min(c["np"], prev["np"] + prev["out"])
                if c["ideal"] < want - 1024 and rng.random() < 0.77:
                    c["ideal"] = want
            prev = c
    pickle.dump(d, open("scripts_dedup77.pkl", "wb"))
    lt, rng = copy.deepcopy(s), random.Random(7)
    for sc in lt:
        for i, c in enumerate(sc["calls"]):
            if i > 0 and not c["start"] and rng.random() < 0.10:
                c["delay"] += rng.uniform(10, 60)
    pickle.dump(lt, open("scripts_longtools.pkl", "wb"))
    th = copy.deepcopy(s)                                # think pauses 4x longer (about 2 min at the median)
    for sc in th:
        for i, c in enumerate(sc["calls"]):
            if c["start"] and i > 0:
                c["delay"] *= 4.0
    pickle.dump(th, open("scripts_think4.pkl", "wb"))


def one(job):
    scr, pool, n, pol = job
    scripts = load(scr)
    rs = [Sim(scripts, n, pol, pool=pool, seed=sd).run() for sd in SEEDS]
    f = lambda k: float(np.mean([r.get(k, 0.0) for r in rs]))
    return job, {k: f(k) for k in ("calls_h", "hit", "evict_gpu", "turn_p50", "turn_p95", "turn_mean",
                                   "wait_calls_per_100", "coop_per_sess_h", "free_per_sess_h", "cap_rel")}, \
        [r["calls_h"] for r in rs]


def main():
    if "--transforms" in sys.argv:
        transforms()
        return
    jobs = [j for _, js in SECTIONS for j in js]
    with Pool(16) as p:
        res = {job: (m, per) for job, m, per in p.map(one, jobs)}
    for title, js in SECTIONS:
        print(f"\n### {title}\n")
        print("| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for j in js:
            m, per = res[j]
            extra = []
            if m["wait_calls_per_100"]:
                extra.append(f"wait-only calls per 100 work: {m['wait_calls_per_100']:.0f}")
            if "mix" in j[3]:
                extra.append(f"per session/h: rule {m['coop_per_sess_h']:.0f}, others {m['free_per_sess_h']:.0f}")
            if m["cap_rel"]:
                extra.append(f"cap releases {m['cap_rel']:.0f}")
            print(f"| {j[0].replace('scripts', '').replace('.pkl', '') or 'as run'} | {j[1]:,} | {j[2]} | `{j[3]}` | "
                  f"{m['calls_h']:,.0f} | {' / '.join(f'{x:,.0f}' for x in per)} | {m['hit']:.2f} | {100 * m['evict_gpu']:.0f} % | "
                  f"{m['turn_p50']:,.0f} / {m['turn_p95']:,.0f} | {'; '.join(extra)} |")


if __name__ == "__main__":
    main()
