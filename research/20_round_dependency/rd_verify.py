#!/usr/bin/env python3
"""Independent check of rd_analyze.py's headline numbers for one replay run of research/20_round_dependency.

It reads the raw records again and recomputes, by its own simpler code path:
  - every sample's match against the recorded next action, from the stored sample keys and the recorded calls'
    keys rebuilt from the rounds table (rd_cf.keys_of is shared; test_rd.py round-trips it), against the stored
    'same' flags and p values;
  - per pair: determinate, independent of the previous round (p_P >= TAU * p_C, p_C >= 2/S);
  - per source and in all: the independent share of determinate pairs, rho = sum p_P / sum p_C, the mergeable
    share of chain-tested pairs (from the chain records alone), the calls a merge removes per turn;
and compares each with the point estimate in data/tables/cf_<run>.json. Prints every value and exits 1 on any
mismatch beyond 1e-9.

    python research/20_round_dependency/rd_verify.py --run main
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

TOL = 1e-9


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    run_dir = cf.CF / a.run
    meta = json.loads((run_dir / "run.json").read_text())
    S, tau = meta["S"], meta["tau"]
    table = json.loads((rr.DATA / "tables" / f"cf_{a.run}.json").read_text())
    _, sessions, rows = cf.load_sample(meta["sample"])
    by_key = {}
    for sid, rs_ in rows.items():
        for r in rs_:
            by_key[(sid, r["turn"], r["j"])] = r
    recs = rr.read_jsonl(run_dir / "records.jsonl")
    var = {}
    chains = {}
    for r in recs:
        if r["variant"] == "chain":
            chains[(r["sid"], r["turn"])] = r["chain"]
        elif r["variant"] in ("C", "P") and r.get("status") == "ok":
            var[(r["sid"], r["turn"], r["j"], r["variant"])] = r
    # 1. sample matches, rebuilt
    flags_bad = p_bad = 0
    for (sid, turn, j, v), r in var.items():
        row = by_key[(sid, turn, j)]
        rec_keys = {tuple(map(str, k)) for k in cf.key_set(row["calls"], cf.session_cwd(sessions[sid]), "target")}
        hits = 0
        for s in r["samples"]:
            mine = len({tuple(k) for k in s["keys"]} & rec_keys) * 2 >= len(rec_keys)
            flags_bad += mine != s["same"]["target"]
            hits += mine
        p_bad += abs(hits / len(r["samples"]) - r["p"]["target"]) > TOL
    print(f"sample matches rebuilt: {len(var)} batches, {flags_bad} flag mismatches, {p_bad} p mismatches")
    # 2. per-pair labels and aggregates
    def p(sid, turn, j, v):
        x = var.get((sid, turn, j, v))
        return None if x is None else x["p"]["target"]
    agg = defaultdict(lambda: {"det": 0, "ind": 0, "sp": 0.0, "sc": 0.0, "chain": 0, "joined": 0})
    calls_turn = defaultdict(lambda: [0, 0])
    for (sid, turn, j), row in by_key.items():
        if p(sid, turn, j, "C") is None:
            continue
        src = sessions[sid]["src"]
        ct = calls_turn[(src, sid, turn)]
        ct[0] += 1
        chain = {c["j"]: c for c in chains.get((sid, turn), [])}
        if j > 0 and not row["final"]:
            pc, pp = p(sid, turn, j, "C"), p(sid, turn, j, "P")
            for key in ("all", src):
                g = agg[key]
                if pp is not None:
                    g["sp"] += pp
                    g["sc"] += pc
                if pp is not None and pc >= 2 / S:
                    g["det"] += 1
                    g["ind"] += pp >= tau * pc
                if j in chain:
                    g["chain"] += 1
                    g["joined"] += not chain[j]["start"]
        if j in chain and not chain[j]["start"]:
            ct[1] += 1
    removed = defaultdict(lambda: [0, 0])
    for (src, sid, turn), (n, k) in calls_turn.items():
        for key in ("all", src):
            removed[key][0] += n
            removed[key][1] += k
    bad = 0
    for key in ("all", "t19", "t16"):
        g = agg[key]
        sub = table["subsets"].get(key)
        if not sub or not g["det"]:
            continue
        mine = {"independent": g["ind"] / g["det"], "rho": g["sp"] / g["sc"] if g["sc"] else None,
                "mergeable": g["joined"] / g["chain"] if g["chain"] else None,
                "calls_removed": removed[key][1] / removed[key][0] if removed[key][0] else None}
        theirs = {"independent": sub["independent"][0], "rho": sub["rho"][0], "mergeable": sub["mergeable"][0],
                  "calls_removed": (sub.get("merge") or {}).get("calls_removed")}
        for name in mine:
            ok = mine[name] is None and theirs[name] is None or (
                mine[name] is not None and theirs[name] is not None and abs(mine[name] - theirs[name]) <= TOL)
            bad += not ok
            print(f"  {key:4s} {name:14s} verify {mine[name]!s:22.22s} analyze {theirs[name]!s:22.22s} "
                  f"{'ok' if ok else 'MISMATCH'}")
    total_bad = bad + flags_bad + p_bad
    print(f"{'all values agree' if not total_bad else f'{total_bad} mismatches'}")
    sys.exit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
