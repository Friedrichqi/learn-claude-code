#!/usr/bin/env python3
"""Stage 0 census of research/21_precautionary_work (CPU, read-only on topic 20's data): on SWE-bench Pro and
Terminal-Bench, how much tool work could a front-load line move earlier, and how many model calls would that save?

Unit: the 58 Pro + Terminal-Bench trials of topic 20's main replay (`rd_analyze.load_run("main")`, 1,557 rounds),
the only trials with chain labels; SWE-bench Verified is shown beside them. A heavy round has >= 1 s of tools and
j > 0. It is decidable earlier when it joins an earlier chain group (`chain.start` is False): the group's first call
L_k could already have issued it.

  A. topic 20's exploratory numbers (run in the session on 2026-10-29 and never saved), by the same definitions:
     the heavy rounds' own labels, joins, order-free, the next round's label, exact repeats; how many calls and how
     much recorded model time a joining round could start earlier; the early-launch and repeat-speculation
     ceilings as shares of trial time (recorded model time + tool time);
  B. folds: calls removed by folding each chain group up to its last heavy joining round (>= 1 s, >= 10 s) and by
     folding every join; joining heavy rounds that commute with the group's earlier rounds (static eff_deps), the
     only ones a background launch could move alone;
  C. concentration: command timeouts, the largest rounds' and trials' shares, the decidable >= 10 s rounds'
     trials and position in the trial;
  D. compliance-probe items: eligible (j, h) pairs (j in [k, h) for a heavy joining round h of group k..h) and the
     controls (heavy rounds that start a group);
  E. tool share of trial time by job and benchmark, over every MiniMax trial topic 20 loaded.

    python research/21_precautionary_work/pw_census.py            # data/tables/census.{md,json}
    python research/21_precautionary_work/pw_census.py --check    # and assert the values the plan relies on
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_analyze as ra  # noqa: E402
import rd_rounds as rr  # noqa: E402

DIR = Path(__file__).resolve().parent
TABLES = DIR / "data" / "tables"
HEAVY_S, LONG_S, TIMEOUT_S = 1.0, 10.0, 1790.0
KINDS = {"protb": ("swepro", "tbench"), "swe": ("swe",)}

# the values the plan relies on (2026-10-30), asserted by --check
EXPECT = {"protb.heavy": 259, "protb.joins": 97, "protb.share.joins": 0.441, "protb.calls_earlier": 1.48,
          "protb.model_s_earlier": 28.8, "protb.share.order_free": 0.067, "protb.share.next_independent": 0.046,
          "protb.share.repeats": 0.183, "protb.model_h": 10.97, "protb.tool_h": 4.02, "protb.early_launch": 0.052,
          "protb.repeat_speculation": 0.003, "folds.calls": 1557, "folds.heavy": 141, "folds.long": 75,
          "folds.all": 407, "folds.commute": 11, "conc.long_joins": 51, "conc.long_join_trials": 20,
          "conc.position_p50": 0.59, "conc.top2_share": 0.58}


def norm(cmd: str) -> str:
    return re.sub(r"\s+", " ", cmd.strip())


def bash_cmds(row: dict) -> list[str]:
    return [norm((c.get("input") or {}).get("command", "")) for c in row["calls"] if c["name"] == "bash"]


def census() -> dict:
    data = ra.load_run("main")
    S, tau = data["meta"]["S"], data["meta"]["tau"]
    items, rows = data["items"], data["rows"]
    idx = {(i["sid"], i["turn"], i["j"]): i for i in items}
    row = {(sid, r["turn"], r["j"]): r for sid, rs_ in rows.items() for r in rs_}

    def dur(k) -> float:
        r = row.get(k)
        return (r["t1"] - r["t0"]) if r and r["t0"] is not None and r["t1"] is not None else 0.0

    def joins(i) -> bool:
        return i["chain"] is not None and not i["chain"]["start"]

    out = {}
    # A. topic 20's exploratory numbers
    for name, kinds in KINDS.items():
        tot = model = tools = early = hidden = earlier_s = 0.0
        agg, n = defaultdict(float), defaultdict(int)
        earlier_calls = 0
        for sid in sorted({i["sid"] for i in items if i["src"] == "t16" and i["kind"] in kinds}):
            seen = set()
            for i in sorted((i for i in items if i["sid"] == sid), key=lambda i: (i["turn"], i["j"])):
                k = (sid, i["turn"], i["j"])
                d, t = dur(k), i["tool_s"] or 0.0
                model += d
                tools += t
                cmds = bash_cmds(row[k])
                if t >= HEAVY_S and i["j"] > 0:
                    tot += t
                    n["heavy"] += 1
                    agg["own " + ra.label(i, S, tau)] += t
                    if joins(i):
                        agg["joins"] += t
                        n["joins"] += 1
                        gap = sum(dur((sid, i["turn"], x)) for x in range(i["chain"]["k"] + 1, i["j"] + 1))
                        earlier_s += gap
                        early += gap
                        earlier_calls += i["j"] - i["chain"]["k"]
                    pc = ra.p(i["C"])
                    if i.get("p_early") is not None and pc and pc >= 2 / S and i["p_early"] >= tau * pc:
                        agg["order_free"] += t
                    nxt = idx.get((sid, i["turn"], i["j"] + 1))
                    if nxt is not None and not nxt["final"]:
                        agg["next_" + ra.label(nxt, S, tau)] += t
                    if any(c in seen for c in cmds):
                        agg["repeats"] += t
                        n["repeats"] += 1
                        hidden += min(t, d)
                seen.update(cmds)
        out[name] = {"heavy": n["heavy"], "heavy_tool_h": tot / 3600, "joins": n["joins"],
                     "share": {k: v / tot for k, v in sorted(agg.items())} if tot else {},
                     "calls_earlier": earlier_calls / n["joins"] if n["joins"] else None,
                     "model_s_earlier": earlier_s / n["joins"] if n["joins"] else None,
                     "model_h": model / 3600, "tool_h": tools / 3600,
                     "early_launch": early / (model + tools) if model + tools else None,
                     "repeated_heavy": n["repeats"],
                     "repeat_speculation": hidden / (model + tools) if model + tools else None}

    # B-D on Pro + Terminal-Bench
    pt = [i for i in items if i["src"] == "t16" and i["kind"] in KINDS["protb"]]
    heavy = [i for i in pt if (i["tool_s"] or 0.0) >= HEAVY_S and i["j"] > 0]
    jh = [i for i in heavy if joins(i)]
    groups = defaultdict(list)
    for i in pt:
        k = i["chain"]["k"] if joins(i) else i["j"]
        groups[(i["sid"], i["turn"], k)].append(i)
    fold = {"calls": len(pt), "heavy": 0, "long": 0,
            "all": sum(joins(i) for i in pt)}
    for (sid, turn, k), g in groups.items():
        h1 = [i["j"] for i in g if i["j"] != k and (i["tool_s"] or 0.0) >= HEAVY_S]
        h10 = [i["j"] for i in g if i["j"] != k and (i["tool_s"] or 0.0) >= LONG_S]
        fold["heavy"] += max(h1) - k if h1 else 0
        fold["long"] += max(h10) - k if h10 else 0
    commute = [i for i in jh if i["static"] is not None
               and not (set(i["static"]["eff_deps"]) & set(range(i["chain"]["k"], i["j"])))]
    fold.update({"commute": len(commute), "joins_heavy": len(jh),
                 "adjacent": sum(i["j"] == i["chain"]["k"] + 1 for i in jh),
                 "bash_only": sum(set(i["tools"]) <= {"bash"} for i in jh)})
    for key in ("heavy", "long", "all"):
        fold[key + "_share"] = fold[key] / fold["calls"]

    tool_h = sum(i["tool_s"] for i in heavy)
    timeouts = [i for i in heavy if i["tool_s"] >= TIMEOUT_S]
    dec_s = sorted((i["tool_s"] for i in jh), reverse=True)
    by_trial = defaultdict(float)
    for i in jh:
        by_trial[i["sid"]] += i["tool_s"]
    trial_s = sorted(by_trial.values(), reverse=True)
    long_j = [i for i in jh if i["tool_s"] >= LONG_S]
    pos = sorted(i["j"] / max(1, i.get("n_turn", 0) - 1) if i.get("n_turn") else
                 i["j"] / max(1, max(x["j"] for x in pt if x["sid"] == i["sid"])) for i in long_j)
    q = statistics.quantiles(pos, n=4) if len(pos) >= 4 else [None, None, None]
    conc = {"heavy_tool_h": tool_h / 3600, "timeouts": len(timeouts),
            "timeouts_h": sum(i["tool_s"] for i in timeouts) / 3600,
            "timeouts_share_heavy": sum(i["tool_s"] for i in timeouts) / sum(i["tool_s"] for i in heavy),
            "timeouts_decidable": sum(joins(i) for i in timeouts),
            "decidable_h": sum(dec_s) / 3600, "top2_share": sum(dec_s[:2]) / sum(dec_s),
            "top3_trials_share": sum(trial_s[:3]) / sum(trial_s), "trials_with_joins": len(by_trial),
            "long_joins": len(long_j), "long_join_trials": len({i["sid"] for i in long_j}),
            "position_p50": statistics.median(pos), "position_q1": q[0], "position_q3": q[2]}

    pairs, starts = [], set()
    for h in jh:
        k = h["chain"]["k"]
        starts.add((h["sid"], h["turn"], k))
        for j in range(k, h["j"]):
            pairs.append({"sid": h["sid"], "turn": h["turn"], "j": j, "h": h["j"], "k": k,
                          "tool_s": h["tool_s"], "long": h["tool_s"] >= LONG_S})
    group_start_heavy = [i for i in heavy if not joins(i)]
    probe = {"pairs": len(pairs), "pairs_long": sum(p["long"] for p in pairs),
             "distinct_j": len({(p["sid"], p["turn"], p["j"]) for p in pairs}), "group_starts": len(starts),
             "controls_heavy_start": len(group_start_heavy),
             "controls_by_label": {lab: sum(ra.label(i, S, tau) == lab for i in group_start_heavy)
                                   for lab in ("dependent", "independent", "undetermined", "missing")}}

    # E. tool share by job and benchmark, every MiniMax trial topic 20 loaded
    sess, rws = rr.load("t16")
    job_of = {s["sid"]: (s["job"], s["bench"]) for s in sess}
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for r in rws:
        key = job_of[r["sid"]]
        if r["t0"] is not None and r["t1"] is not None:
            acc[key][0] += r["t1"] - r["t0"]
        acc[key][1] += sum(x for x in r["tool_ms"] if x) / 1000.0
    for s in sess:
        acc[(s["job"], s["bench"])][2] += 1
    share = {f"{job}|{bench}": {"trials": v[2], "model_h": v[0] / 3600, "tool_h": v[1] / 3600,
                                "tool_share": v[1] / (v[0] + v[1]) if v[0] + v[1] else None}
             for (job, bench), v in sorted(acc.items())}
    return {"by_kind": out, "folds": fold, "concentration": conc, "probe": probe, "tool_share": share,
            "probe_pairs": pairs}


def pct(v, d=1) -> str:
    return "—" if v is None else f"{100 * v:.{d}f} %"


def render(c: dict) -> list[str]:
    a, f, k, p = c["by_kind"]["protb"], c["folds"], c["concentration"], c["probe"]
    sw = c["by_kind"]["swe"]
    L = ["# Census: how much tool work could a front-load line move earlier? (pw_census.py; Stage 0, CPU)", "",
         "Unit: the 58 SWE-bench Pro + Terminal-Bench trials of topic 20's main replay (1,557 rounds), the only "
         "trials with chain labels; SWE-bench Verified beside them. Heavy round: >= 1 s of tools, j > 0. "
         "Decidable earlier: the round joins an earlier chain group, so the group's first call could have issued it.",
         "", "## A. Topic 20's exploratory numbers, reproduced", "",
         "| | Pro + TB | SWE Verified |", "|---|---|---|",
         f"| heavy rounds (tool hours) | {a['heavy']} ({a['heavy_tool_h']:.2f} h) | {sw['heavy']} ({sw['heavy_tool_h']:.2f} h) |"]
    for key, lab in (("joins", "decidable earlier (joins an earlier group)"), ("own independent", "own label: independent"),
                     ("own dependent", "own label: dependent"), ("own undetermined", "own label: undetermined"),
                     ("order_free", "order-free (already sampled one call earlier)"),
                     ("next_independent", "next round independent"), ("next_dependent", "next round dependent"),
                     ("next_undetermined", "next round undetermined"), ("repeats", "repeats an earlier command")):
        L.append(f"| {lab}, share of heavy tool time | {pct(a['share'].get(key, 0.0))} | {pct(sw['share'].get(key, 0.0))} |")
    L += [f"| a joining round could start earlier by | {a['calls_earlier']:.2f} calls, {a['model_s_earlier']:.1f} s | "
          f"{sw['calls_earlier']:.2f} calls, {sw['model_s_earlier']:.1f} s |",
          f"| trial time: recorded model / tools | {a['model_h']:.2f} h / {a['tool_h']:.2f} h | "
          f"{sw['model_h']:.2f} h / {sw['tool_h']:.2f} h |",
          f"| ceiling: launch every decidable heavy command at its group's first call | {pct(a['early_launch'])} of trial time | "
          f"{pct(sw['early_launch'])} |",
          f"| ceiling: speculate on repeated heavy commands ({a['repeated_heavy']} rounds) | {pct(a['repeat_speculation'])} | "
          f"{pct(sw['repeat_speculation'])} |", "",
          "The 'starts earlier by' seconds are recorded model time of calls k+1..h, on topic 16's loaded server.", "",
          "## B. Folds (Pro + TB)", "",
          "| fold | calls removed | share of the 1,557 calls |", "|---|---|---|",
          f"| each group up to its last heavy (>= 1 s) joining round | {f['heavy']} | {pct(f['heavy_share'])} |",
          f"| each group up to its last >= 10 s joining round | {f['long']} | {pct(f['long_share'])} |",
          f"| every join (topic 20's merged world) | {f['all']} | {pct(f['all_share'])} |", "",
          f"- Joining heavy rounds: {f['joins_heavy']}; adjacent to their group start (h = k+1): {f['adjacent']}; "
          f"bash only: {f['bash_only']}.",
          f"- Only {f['commute']} of {f['joins_heavy']} commute with the group's earlier rounds (static eff_deps), so "
          "front-loading must fold rounds k..h into one response (merge-prefix); a background launch of the heavy "
          "command alone is valid only for those.", "",
          "## C. Concentration (Pro + TB)", "",
          f"- Heavy tool time {k['heavy_tool_h']:.2f} h; command timeouts (>= 1,790 s): {k['timeouts']} rounds, "
          f"{k['timeouts_h']:.2f} h = {pct(k['timeouts_share_heavy'])} of it; {k['timeouts_decidable']} of them are "
          "decidable earlier.",
          f"- Decidable heavy tool time {k['decidable_h']:.2f} h: the two largest rounds hold {pct(k['top2_share'], 0)}, "
          f"the three largest trials {pct(k['top3_trials_share'], 0)}; {k['trials_with_joins']} trials have any.",
          f"- Decidable rounds of >= 10 s: {k['long_joins']}, in {k['long_join_trials']} trials; position in the trial "
          f"(j over the last j) median {k['position_p50']:.2f}, IQR {k['position_q1']:.2f}-{k['position_q3']:.2f}.", "",
          "## D. Compliance-probe items (Pro + TB)", "",
          f"- Eligible (j, h) pairs: {p['pairs']} ({p['pairs_long']} with h >= 10 s), {p['distinct_j']} distinct "
          f"requests L_j, {p['group_starts']} group starts.",
          f"- Controls: {p['controls_heavy_start']} heavy rounds start a group; by their own label "
          + ", ".join(f"{lab} {n}" for lab, n in p["controls_by_label"].items() if n) + ".", "",
          "## E. Tool share of trial time (every MiniMax trial topic 20 loaded)", "",
          "| job | benchmark | trials | model h | tool h | tool share |", "|---|---|---|---|---|---|"]
    for key, v in c["tool_share"].items():
        job, bench = key.split("|")
        L.append(f"| {job} | {bench} | {v['trials']} | {v['model_h']:.1f} | {v['tool_h']:.1f} | {pct(v['tool_share'])} |")
    L += ["", "Model time is the recorded call time (t1 - t0) on topic 16's servers, which ran other trials "
          "concurrently, so tool shares depend on that load.", ""]
    return L


def flat(c: dict) -> dict:
    a, f, k = c["by_kind"]["protb"], c["folds"], c["concentration"]
    return {"protb.heavy": a["heavy"], "protb.joins": a["joins"], "protb.share.joins": a["share"]["joins"],
            "protb.calls_earlier": a["calls_earlier"], "protb.model_s_earlier": a["model_s_earlier"],
            "protb.share.order_free": a["share"].get("order_free", 0.0),
            "protb.share.next_independent": a["share"].get("next_independent", 0.0),
            "protb.share.repeats": a["share"].get("repeats", 0.0), "protb.model_h": a["model_h"],
            "protb.tool_h": a["tool_h"], "protb.early_launch": a["early_launch"],
            "protb.repeat_speculation": a["repeat_speculation"], "folds.calls": f["calls"], "folds.heavy": f["heavy"],
            "folds.long": f["long"], "folds.all": f["all"], "folds.commute": f["commute"],
            "conc.long_joins": k["long_joins"], "conc.long_join_trials": k["long_join_trials"],
            "conc.position_p50": k["position_p50"], "conc.top2_share": k["top2_share"]}


def check(c: dict) -> list[str]:
    got, bad = flat(c), []
    for key, want in EXPECT.items():
        v = got[key]
        tol = 0 if isinstance(want, int) else 0.5 * 10 ** -len(str(want).split(".")[1])
        if v is None or abs(v - want) > tol + 1e-12:
            bad.append(f"{key}: got {v}, expected {want}")
    return bad


def rounded(x):
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="assert the values the plan relies on (exit 1 if not)")
    a = ap.parse_args()
    c = census()
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "census.md").write_text("\n".join(render(c)))
    (TABLES / "census.json").write_text(json.dumps(rounded(c), indent=1, sort_keys=True, allow_nan=False) + "\n")
    print("\n".join(render(c)))
    print(f"[census] wrote {TABLES / 'census.md'} and census.json")
    if a.check:
        bad = check(c)
        for b in bad:
            print("[check] MISMATCH", b)
        print(f"[check] {len(EXPECT) - len(bad)}/{len(EXPECT)} values as planned")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
