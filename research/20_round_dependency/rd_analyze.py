#!/usr/bin/env python3
"""Tables and decisions of the counterfactual replay (rd_cf.py) of research/20_round_dependency (offline).

For each tested round j (it has a predecessor in its turn and is not the final answer), at the target key level:
  - p_C, p_P (and p_A in the pilot, p_M in a chain): the share of S samples that reproduce c_j;
  - determinate when p_C >= 2/S (the recorded action is reproducible at all);
  - independent of round j-1 when p_P >= TAU * p_C; dependent otherwise;
  - mergeable when the chain took it (its group's earlier results all pending, M or P);
  - reorderable when mergeable and its effects commute with the group's earlier rounds (rd_static eff_deps);
  - order-free when the control samples of round j-1 already produce c_j (p_early >= TAU * p_C).
Aggregates, by source, task kind (topic 19's E1-E3 / M1-M3; topic 16's benchmark) and tool class:
  - Q1 strength: the shares above, rho = sum p_P / sum p_C, the outcomes of the pending samples;
  - Q2 merge: the turn's chain groups over its recorded calls; the output and prompt tokens of the calls a merge
    would remove;
  - Q3 reorderable share, Q4 order-free share;
  - Q5 overlap by resource: tool seconds spent while an independent next call could already run, and the GPU tokens
    of the calls that could be issued early;
  - the static census against the replay labels (precision, recall).
Validity gates (the plan's V1-V3) and the pilot's rules for TAU and S. 95 % intervals: percentile bootstrap over
sessions (topic 19) or trials (topic 16), 2,000 draws.

    python research/20_round_dependency/rd_analyze.py --run pilot [--pilot] [--tau 0.5]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

TABLES = rr.DATA / "tables"
DRAWS, SEED = 2000, 20
TAU_GRID = (0.3, 0.4, 0.5, 0.6, 0.7)
AA_FALSE_MAX = 0.10                      # the plan: at most 10 % of A/A rounds called dependent
V1_MIN, V2_MAX, V3_RANGE = 0.95, 0.30, (0.85, 1.15)
PROFILE_CAP_GPU_H = 35.0                 # S1-S3 together


def tool_class(tools: list[str], static_row: dict | None) -> str:
    names = set(tools)
    if not names:
        return "final"
    if names <= {"todo_write"}:
        return "todo"
    if names <= {"read_file", "glob"} or (names <= {"read_file", "glob", "bash"} and static_row
                                          and not static_row.get("eff_unknown") and "bash" in names):
        return "read"
    if names <= {"edit_file", "write_file", "todo_write"}:
        return "edit"
    if names <= {"bash", "todo_write"}:
        return "shell"
    return "mixed"


def load_run(run: str) -> dict:
    """Per tested round: the replay results joined with the rounds table and the static census."""
    out_dir = cf.CF / run
    recs = rr.read_jsonl(out_dir / "records.jsonl")
    meta = json.loads((out_dir / "run.json").read_text())
    sample, sessions, rows = cf.load_sample(meta["sample"])
    by = {}
    chains = {}
    for r in recs:
        if r["variant"] == "chain":
            chains[(r["sid"], r["turn"])] = r["chain"]
        else:
            by[(r["sid"], r["turn"], r["j"], r["variant"], r.get("k"))] = r
    static = {}
    for src in {s["src"] for s in sessions.values()}:
        for x in rr.read_jsonl(rr.DATA / "static" / f"static_{src}.jsonl.xz"):
            if x["sid"] in sessions:
                static[(x["sid"], x["call"])] = x
    tested = []
    for sid, rs_ in rows.items():
        sess = sessions[sid]
        cwd = cf.session_cwd(sess)
        turns = defaultdict(list)
        for r in rs_:
            turns[r["turn"]].append(r)
        for turn, trs in turns.items():
            trs.sort(key=lambda r: r["j"])
            chain = {c["j"]: c for c in chains.get((sid, turn), [])}
            for j, row in enumerate(trs):
                c = by.get((sid, turn, j, "C", None))
                if c is None:
                    continue                         # not reached (a smoke's --max-rounds)
                st = static.get((sid, row["call"]))
                item = {"sid": sid, "src": sess["src"], "kind": row.get("kind"), "turn": turn, "j": j,
                        "call": row["call"], "final": row["final"], "tools": [x["name"] for x in row["calls"]],
                        "np": row["np"], "out": row["out"], "tool_s": (st or {}).get("tool_s", 0.0),
                        "static": st, "C": c, "A": by.get((sid, turn, j, "A", None)),
                        "P": by.get((sid, turn, j, "P", None)), "chain": chain.get(j)}
                item["cls"] = tool_class(item["tools"], st)
                if j > 0:
                    prev_c = by.get((sid, turn, j - 1, "C", None))
                    keys = cf.key_set(row["calls"], cwd, "target")
                    if prev_c and prev_c.get("status") == "ok":
                        hits = sum(cf.matches({tuple(k) for k in s["keys"]}, {tuple(map(str, x)) for x in keys})
                                   for s in prev_c["samples"])
                        item["p_early"] = hits / max(1, len(prev_c["samples"]))
                k = (item["chain"] or {}).get("k")
                if k is not None:
                    item["M"] = by.get((sid, turn, j, "M", k)) or item["P"]
                tested.append(item)
    return {"meta": meta, "sample": sample, "sessions": sessions, "rows": rows, "items": tested, "chains": chains}


def p(rec: dict | None) -> float | None:
    if not rec or rec.get("status") != "ok":
        return None
    return rec["p"]["target"]


def label(item: dict, S: int, tau: float, test: str = "P") -> str:
    pc, pt = p(item["C"]), p(item.get(test))
    if pc is None or pt is None:
        return "missing"
    if pc < 2 / S:
        return "undetermined"
    return "independent" if pt >= tau * pc else "dependent"


def pairs(items: list[dict]) -> list[dict]:
    return [i for i in items if i["j"] > 0 and not i["final"]]


def boot_ratio(items: list[dict], num, den, rng) -> tuple[float | None, float | None, float | None]:
    by = defaultdict(list)
    for i in items:
        by[i["sid"]].append(i)
    keys = list(by)
    def ratio(sel):
        a = sum(num(i) for s in sel for i in by[s] if num(i) is not None and den(i) is not None)
        b = sum(den(i) for s in sel for i in by[s] if num(i) is not None and den(i) is not None)
        return a / b if b else None
    point = ratio(keys)
    if point is None or len(keys) < 2:
        return point, None, None
    draws = sorted(x for x in (ratio([rng.choice(keys) for _ in keys]) for _ in range(DRAWS)) if x is not None)
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def boot_share(items: list[dict], f, rng):
    return boot_ratio(items, lambda i: float(bool(f(i))), lambda i: 1.0, rng)


def fmt_ci(t, pct=True) -> str:
    v, lo, hi = t
    if v is None:
        return "—"
    f = (lambda x: f"{x:.0%}") if pct else (lambda x: f"{x:.2f}")
    return f"{f(v)} [{f(lo)}, {f(hi)}]" if lo is not None else f(v)


def summarize(items: list[dict], S: int, tau: float, rng) -> dict:
    pr = pairs(items)
    det = [i for i in pr if label(i, S, tau) in ("independent", "dependent")]
    outcomes = Counter()
    for i in pr:
        if p(i["P"]) is not None:
            outcomes.update(i["P"].get("outcomes") or {})
    n_out = sum(outcomes.values()) or 1
    chain_items = [i for i in pr if i["chain"] is not None]
    joined = [i for i in chain_items if not i["chain"]["start"]]
    reorder = [i for i in joined if i["static"] is not None
               and not (set(i["static"]["eff_deps"]) & set(range(i["chain"]["k"], i["j"])))]
    early = [i for i in det if i.get("p_early") is not None]
    return {
        "pairs": len(pr), "determinate": len(det) / len(pr) if pr else None,
        "independent": boot_share(det, lambda i: label(i, S, tau) == "independent", rng),
        "rho": boot_ratio(pr, lambda i: p(i["P"]), lambda i: p(i["C"]), rng),
        "rho_exact": boot_ratio(pr, lambda i: i["P"]["p"]["exact"] if p(i["P"]) is not None else None,
                                lambda i: i["C"]["p"]["exact"] if p(i["C"]) is not None else None, rng),
        "pc_mean": sum(p(i["C"]) for i in pr if p(i["C"]) is not None) / max(1, sum(p(i["C"]) is not None for i in pr)),
        "outcomes": {k: v / n_out for k, v in outcomes.most_common()},
        "mergeable": boot_share(chain_items, lambda i: not i["chain"]["start"], rng),
        "reorderable": boot_share(chain_items, lambda i: i in reorder, rng),
        "order_free": boot_share(early, lambda i: i["p_early"] >= tau * p(i["C"]), rng),
    }


def turn_merge(items: list[dict]) -> dict:
    """Calls, output tokens and prompt tokens a perfect merge of the chain groups removes, per turn."""
    turns = defaultdict(list)
    for i in items:
        turns[(i["sid"], i["turn"])].append(i)
    calls = removed = out = out_removed = np_ = np_removed = 0
    for trs in turns.values():
        for i in trs:
            calls += 1
            out += i["out"]
            np_ += i["np"]
            if i["chain"] is not None and not i["chain"]["start"]:
                removed += 1
                out_removed += i["out"]
                np_removed += i["np"]
    return {"turns": len(turns), "calls": calls, "calls_removed": removed / calls if calls else None,
            "out_removed": out_removed / out if out else None, "np_removed": np_removed / np_ if np_ else None}


def overlap(items: list[dict]) -> dict:
    """Tool seconds of round j-1 when round j joins j-1's group: CPU work an early round j could run beside."""
    idx = {(i["sid"], i["turn"], i["j"]): i for i in items}
    tool = beside = 0.0
    heavy_join = heavy = 0
    for i in items:
        tool += i["tool_s"] or 0.0
        nxt = idx.get((i["sid"], i["turn"], i["j"] + 1))
        if nxt is not None and nxt["chain"] is not None and not nxt["chain"]["start"]:
            beside += i["tool_s"] or 0.0
        if (i["tool_s"] or 0) >= 1.0 and not i["final"]:
            heavy += 1
            heavy_join += bool(nxt is not None and nxt["chain"] is not None and not nxt["chain"]["start"])
    return {"tool_s": tool, "tool_s_beside_independent": beside / tool if tool else None,
            "heavy_rounds": heavy, "heavy_followed_by_independent": heavy_join / heavy if heavy else None}


def static_vs_replay(items: list[dict], S: int, tau: float) -> dict:
    det = [i for i in pairs(items) if label(i, S, tau) in ("independent", "dependent") and i["static"]]
    tp = sum(1 for i in det if i["static"]["lex_prev"] and label(i, S, tau) == "dependent")
    fp = sum(1 for i in det if i["static"]["lex_prev"] and label(i, S, tau) == "independent")
    fn = sum(1 for i in det if not i["static"]["lex_prev"] and label(i, S, tau) == "dependent")
    tn = sum(1 for i in det if not i["static"]["lex_prev"] and label(i, S, tau) == "independent")
    ch = [i for i in pairs(items) if i["chain"] is not None and i["static"]]
    agree = sum(1 for i in ch if bool(i["static"]["joins"]) == (not i["chain"]["start"]))
    return {"n": len(det), "copy_precision": tp / (tp + fp) if tp + fp else None,
            "copy_recall": tp / (tp + fn) if tp + fn else None, "independent_but_copied": fp,
            "dependent_without_copy": fn, "both_independent": tn,
            "static_join_agreement": agree / len(ch) if ch else None}


def gates(items: list[dict], S: int, tau: float, rng) -> dict:
    cs = [i["C"] for i in items if i["C"].get("status") == "ok" and "render_ok" in i["C"]]
    v1 = sum(c["render_ok"] for c in cs) / len(cs) if cs else None
    pos = [i for i in pairs(items) if i["static"] and i["static"]["lex_prev"]]
    v2 = boot_ratio(pos, lambda i: p(i["P"]), lambda i: p(i["C"]), rng)
    aa = [i for i in pairs(items) if p(i.get("A")) is not None]
    v3 = boot_ratio(aa, lambda i: p(i["A"]), lambda i: p(i["C"]), rng) if aa else (None, None, None)
    return {"V1_render": v1, "V1_ok": v1 is not None and v1 >= V1_MIN, "V2_rho_copied": v2, "V2_n": len(pos),
            "V2_ok": v2[0] is not None and v2[0] <= V2_MAX,
            "V3_rho_aa": v3, "V3_n": len(aa),
            "V3_ok": v3[0] is not None and V3_RANGE[0] <= v3[0] <= V3_RANGE[1]}


def tau_rule(items: list[dict], S: int) -> dict:
    """A/A: the share of determinate rounds the rule calls dependent when the 'test' is just another control."""
    aa = [i for i in pairs(items) if p(i.get("A")) is not None]
    out = {}
    for tau in TAU_GRID:
        det = [i for i in aa if p(i["C"]) >= 2 / S]
        out[tau] = sum(1 for i in det if p(i["A"]) < tau * p(i["C"])) / len(det) if det else None
    ok = [t for t in TAU_GRID if out[t] is not None and out[t] <= AA_FALSE_MAX]
    chosen = 0.5 if 0.5 in ok else (max(t for t in ok if t < 0.5) if any(t < 0.5 for t in ok) else None)
    return {"aa_false_dependent": out, "tau": chosen}


def s_rule(run: str, items: list[dict], S: int) -> dict:
    """GPU-hours per sampling batch in this run, projected to the main sample's batches (C and P per pair, M at this
    run's rate, no A) at S = 8, else 6; 75 % of a batch's cost scales with S."""
    path = rr.DATA / "gpu_hours.jsonl"
    hours = [json.loads(line) for line in path.open()] if path.exists() else []
    gpu_h = sum(h["gpu_h"] for h in hours if h["stage"] in (run, f"s2_{run}"))
    spent = sum(h["gpu_h"] for h in hours)
    recs = rr.read_jsonl(cf.CF / run / "records.jsonl")
    batches = sum(1 for r in recs if r["variant"] in ("C", "A", "P", "M") and r.get("status") == "ok")
    n_pairs = len(pairs(items)) or 1
    m_rate = sum(1 for r in recs if r["variant"] == "M" and r.get("status") == "ok") / n_pairs
    main = json.loads(cf.sample_path("main").read_text())
    main_rounds = sum(u["rounds"] for u in main["units"])
    pair_share = n_pairs / max(1, len(items))
    main_batches = main_rounds * (1 + pair_share * (1 + m_rate))
    per_batch = gpu_h / batches if batches else None
    proj = {}
    if per_batch:
        for s_new in (8, 6):
            proj[s_new] = main_batches * per_batch * (0.25 + 0.75 * s_new / S)
    left = PROFILE_CAP_GPU_H - spent
    chosen = 8 if proj.get(8) is not None and proj[8] <= left else 6
    frac = None if proj.get(chosen) is None or proj[chosen] <= left else left / proj[chosen]
    return {"stage_gpu_h": gpu_h, "spent_gpu_h": spent, "batches": batches, "main_rounds": main_rounds,
            "main_batches": main_batches, "gpu_h_per_batch": per_batch, "projected": proj, "left_for_main": left,
            "S": chosen, "sample_fraction": frac}


def table(run: str, tau: float, pilot: bool) -> str:
    data = load_run(run)
    S = data["meta"]["S"]
    rng = random.Random(SEED)
    items = data["items"]
    lines = [f"# Counterfactual replay `{run}` (rd_analyze.py)", "",
             f"S = {S} samples per variant; TAU = {tau}; target key level. Tested rounds: {len(items)} "
             f"({len(pairs(items))} with a predecessor in the turn and not a final answer).", ""]
    g = gates(items, S, tau, rng)
    lines += ["## Validity gates", "",
              f"- V1 control render = recorded prompt tokens: {g['V1_render']:.1%} (need >= {V1_MIN:.0%}): "
              f"{'pass' if g['V1_ok'] else 'FAIL'}" if g["V1_render"] is not None else "- V1: no records",
              f"- V2 positive control (rounds whose arguments copy the previous results, n={g['V2_n']}): "
              f"rho = {fmt_ci(g['V2_rho_copied'], pct=False)} (need <= {V2_MAX}): {'pass' if g['V2_ok'] else 'FAIL'}",
              f"- V3 A/A (n={g['V3_n']}): rho = {fmt_ci(g['V3_rho_aa'], pct=False)} (need {V3_RANGE[0]}-{V3_RANGE[1]}): "
              f"{'pass' if g['V3_ok'] else ('—' if not g['V3_n'] else 'FAIL')}", ""]
    out = {"run": run, "S": S, "tau": tau, "gates": g}
    if pilot:
        tr, sr = tau_rule(items, S), s_rule(run, items, S)
        out.update(tau_rule=tr, s_rule=sr)
        lines += ["## Pilot rules", "",
                  "- A/A false-dependent share by TAU: " + ", ".join(
                      f"{t}: {v:.0%}" if v is not None else f"{t}: —" for t, v in tr["aa_false_dependent"].items())
                  + f" -> TAU = {tr['tau']}",
                  f"- GPU-h this run {sr['stage_gpu_h']:.2f} for {sr['batches']} sampling batches; spent "
                  f"{sr['spent_gpu_h']:.2f}; main sample {sr['main_rounds']} rounds = {sr['main_batches']:.0f} batches; "
                  "projected " + ", ".join(f"S={k}: {v:.1f}" for k, v in sr["projected"].items()) +
                  f"; left {sr['left_for_main']:.1f} -> S = {sr['S']}"
                  + (f", sample fraction {sr['sample_fraction']:.2f}" if sr["sample_fraction"] else ""),
                  ""]
    subsets = [("all", lambda i: True)]
    for src in ("t19", "t16"):
        subsets.append((src, (lambda s: lambda i: i["src"] == s)(src)))
        kinds = sorted({i["kind"] for i in items if i["src"] == src and i["kind"]})
        subsets += [(f"{src} {k}", (lambda s, k: lambda i: i["src"] == s and i["kind"] == k)(src, k)) for k in kinds]
    for cls in ("read", "edit", "shell", "todo", "mixed"):
        subsets.append((f"class {cls}", (lambda c: lambda i: i["cls"] == c)(cls)))
    lines += ["## Q1-Q4 by subset", "",
              "| subset | pairs | determinate | independent of prev | rho P/C | rho exact | mean p_C | "
              "pending outcomes (same/reissue/other/none/trunc) | mergeable | reorderable | order-free |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    out["subsets"] = {}
    for name, f in subsets:
        sub = [i for i in items if f(i)]
        if not pairs(sub):
            continue
        s = summarize(sub, S, tau, rng)
        out["subsets"][name] = s
        oc = s["outcomes"]
        lines.append(f"| {name} | {s['pairs']} | {s['determinate']:.0%} | {fmt_ci(s['independent'])} | "
                     f"{fmt_ci(s['rho'], pct=False)} | {fmt_ci(s['rho_exact'], pct=False)} | {s['pc_mean']:.2f} | "
                     + "/".join(f"{oc.get(k, 0):.0%}" for k in ("same", "reissue_pending", "other_tool", "no_call",
                                                               "truncated"))
                     + f" | {fmt_ci(s['mergeable'])} | {fmt_ci(s['reorderable'])} | {fmt_ci(s['order_free'])} |")
    lines += ["", "## Q2 merge and Q5 overlap by source", "",
              "| subset | turns | calls | calls a merge removes | output tokens removed | prompt tokens of removed calls |"
              " tool s beside an independent next call | rounds with >= 1 s tools | of these followed by an independent round |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, f in subsets[: 1 + 2 + sum(1 for n, _ in subsets if n.startswith(("t19 ", "t16 ")))]:
        sub = [i for i in items if f(i)]
        if not sub:
            continue
        m, o = turn_merge(sub), overlap(sub)
        out["subsets"].setdefault(name, {}).update(merge=m, overlap=o)
        pct = lambda x: "—" if x is None else f"{x:.0%}"
        lines.append(f"| {name} | {m['turns']} | {m['calls']} | {pct(m['calls_removed'])} | {pct(m['out_removed'])} | "
                     f"{pct(m['np_removed'])} | {pct(o['tool_s_beside_independent'])} | {o['heavy_rounds']} | "
                     f"{pct(o['heavy_followed_by_independent'])} |")
    sv = static_vs_replay(items, S, tau)
    out["static_vs_replay"] = sv
    pct = lambda x: "—" if x is None else f"{x:.0%}"
    lines += ["", "## Static census against the replay", "",
              f"Determinate pairs n={sv['n']}: 'copies prev' predicts 'dependent' with precision "
              f"{pct(sv['copy_precision'])} and recall {pct(sv['copy_recall'])}; {sv['dependent_without_copy']} "
              f"dependent rounds copy nothing from the previous results. Static merge groups agree with the replay's "
              f"chain on {pct(sv['static_join_agreement'])} of rounds.", ""]
    if not pilot:
        t19 = out["subsets"].get("t19", {}).get("merge", {})
        pro_tb = [i for i in items if i["src"] == "t16" and i["kind"] in ("swepro", "tbench")]
        ov = overlap(pro_tb) if pro_tb else {}
        gm = t19.get("calls_removed")
        gr = ov.get("tool_s_beside_independent")
        out["load_gates"] = {"G_merge": gm, "G_merge_ok": gm is not None and gm >= 0.10,
                             "G_reorder": gr, "G_reorder_ok": gr is not None and gr >= 0.10}
        lines += ["## Load-test gates", "",
                  f"- G_merge (topic 19: calls a merge removes >= 10 %): {pct(gm)} -> "
                  f"{'run the merge test' if gm is not None and gm >= 0.10 else 'no merge test'}",
                  f"- G_reorder (Pro + Terminal-Bench: tool seconds beside an independent next call >= 10 %): "
                  f"{pct(gr)} -> {'run the reorder test' if gr is not None and gr >= 0.10 else 'no reorder test'}", ""]
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / f"cf_{run}.md").write_text("\n".join(lines) + "\n")
    (TABLES / f"cf_{run}.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--tau", type=float, default=None, help="default: the run's own TAU")
    ap.add_argument("--pilot", action="store_true")
    a = ap.parse_args()
    meta = json.loads((cf.CF / a.run / "run.json").read_text())
    print(table(a.run, a.tau if a.tau is not None else meta["tau"], a.pilot))


if __name__ == "__main__":
    main()
