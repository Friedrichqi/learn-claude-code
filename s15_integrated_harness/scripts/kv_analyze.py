#!/usr/bin/env python3
"""Aggregate kv_replay.py outputs into the tables of the KV-splice report.

    python3 kv_analyze.py traces/kv_splice/*.replay.jsonl [--tables] [--json summary.json]
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path


import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kv_replay import parse_tool_call, targets  # noqa: E402


def reclassify(steps):
    """Recompute behaviour tool names and re-fetch flags from the stored greedy text (the replay's
    original parser missed tool calls whose JSON was cut off by the decode budget)."""
    evicted_so_far = []
    last_step = -1
    for s in steps:
        if s["step"] <= last_step:
            evicted_so_far = []
        last_step = s["step"]
        this_step = s.get("evicted", []) or []
        for c, b in (s.get("behaviour") or {}).items():
            call = parse_tool_call(b.get("text", ""))
            b["tool"] = call.get("name") if call else None
            b["reacquires"] = targets(call, this_step)
            b["reacquires_any_evicted"] = targets(call, evicted_so_far + this_step)
        for o in (p["outputs"] for p in s.get("probes", []) or []):
            for c, v in o.items():
                if "text" in v:
                    v["tool_call"] = bool(parse_tool_call(v["text"]))
        evicted_so_far.extend(this_step)
    return steps


def load(path: str):
    meta, steps = None, []
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if r["type"] == "meta":
            meta = r
        elif r["type"] == "step" and not r.get("skipped"):
            steps.append(r)
    return meta, reclassify(steps)


def med(xs, digits=3):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), digits) if xs else None


def mean(xs, digits=3):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), digits) if xs else None


def fmt(x, digits=3):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


def analyze(paths: list[str]):
    runs = []
    for p in paths:
        meta, steps = load(p)
        if not meta:
            continue
        runs.append((Path(p).name, meta, steps))
    # pooled pseudo-runs: all runs replayed with the same model, steps concatenated
    by_model = defaultdict(list)
    for name, meta, steps in runs:
        by_model[meta["model"]].append((name, meta, steps))
    for model, group in by_model.items():
        if len(group) > 1:
            pooled_steps = [dict(s) for _, _, steps in group for s in steps]
            meta = dict(group[0][1]); meta["conditions"] = sorted({c for _, m, _ in group for c in m["conditions"]},
                                                                  key=lambda c: ["recompute", "shift", "gap", "shift1", "oracle"].index(c) if c in ["recompute", "shift", "gap", "shift1", "oracle"] else 99)
            runs.append((f"POOLED[{len(group)} runs]", meta, pooled_steps))
    out = {"runs": []}
    for name, meta, steps in runs:
        conds = meta["conditions"]
        edit_steps = [s for s in steps if s.get("edit")]
        placeholder_steps = [s for s in edit_steps if "placeholder" in s.get("edit_kinds", [])]
        summary = {
            "run": name, "model": meta["model"], "steps": len(steps), "edit_steps": len(edit_steps),
            "placeholder_steps": len(placeholder_steps),
            "edit_kinds": dict(Counter(k for s in edit_steps for k in s["edit_kinds"])),
            "evicted_items": sum(len(s.get("evicted", [])) for s in edit_steps),
            "evicted_tokens": sum(e["tokens"] for s in edit_steps for e in s.get("evicted", [])),
            "context_tokens_median": med([s["tokens_new"] for s in steps], 0),
            "context_tokens_max": max(s["tokens_new"] for s in steps),
        }
        # -- compute cost: tokens the model had to run per policy ---------------------------------
        cost = {c: sum(s["tokens_fresh"].get(c, 0) for s in steps if s["step"] > 0) for c in conds}
        cost_edit = {c: sum(s["tokens_fresh"].get(c, 0) for s in edit_steps) for c in conds}
        summary["tokens_computed_total"] = cost
        summary["tokens_computed_on_edit_steps"] = cost_edit
        summary["tail_tokens_total"] = sum(s.get("tail_tokens", 0) for s in steps if s["step"] > 0)
        summary["tokens_after_first_change_edit_steps"] = {"median": med([s["tokens_after_first_change"] for s in edit_steps], 0),
                                                           "sum": sum(s["tokens_after_first_change"] for s in edit_steps)}
        # -- fidelity ------------------------------------------------------------------------------
        fid = {}
        for c in conds:
            rows = [s for s in edit_steps if c in s.get("nll", {}) and "tf_kl_from_recompute" in s["nll"][c]]
            fid[c] = {
                "n": len(rows),
                "first_token_kl_from_recompute_median": med([s["first_token"][c]["kl_from_recompute"] for s in edit_steps if c in s.get("first_token", {})]),
                "first_token_kl_from_oracle_median": med([s["first_token"][c]["kl_from_oracle"] for s in edit_steps if c in s.get("first_token", {}) and s.get("oracle_valid")]),
                "first_token_top1_eq_recompute": mean([float(s["first_token"][c]["top1"] == s["first_token"]["recompute"]["top1"]) for s in edit_steps if c in s.get("first_token", {})]),
                "tf_kl_from_recompute_mean": mean([s["nll"][c]["tf_kl_from_recompute"] for s in rows]),
                "tf_kl_from_recompute_median": med([s["nll"][c]["tf_kl_from_recompute"] for s in rows]),
                "tf_kl_from_recompute_p90": (round(sorted(s["nll"][c]["tf_kl_from_recompute"] for s in rows)[int(0.9 * (len(rows) - 1))], 3) if rows else None),
                "tf_kl_from_recompute_max": (round(max(s["nll"][c]["tf_kl_from_recompute"] for s in rows), 3) if rows else None),
                "real_nll_ratio_to_recompute": mean([s["nll"][c]["mean_nll"] / max(1e-6, s["nll"]["recompute"]["mean_nll"]) for s in rows if "recompute" in s["nll"]]),
                "tf_top1_agree_recompute_mean": mean([s["nll"][c]["tf_top1_agree_recompute"] for s in rows]),
                "tf_kl_from_oracle_mean": mean([s["nll"][c].get("tf_kl_from_oracle") for s in rows if s.get("oracle_valid")]),
                "tf_top1_agree_oracle_mean": mean([s["nll"][c].get("tf_top1_agree_oracle") for s in rows if s.get("oracle_valid")]),
                "real_response_nll_mean": mean([s["nll"][c]["mean_nll"] for s in rows]),
                "real_response_argmax_agree_mean": mean([s["nll"][c]["argmax_agree_real"] for s in rows]),
            }
        # nll on ALL steps (compounded staleness shows up on non-edit steps too)
        for c in conds:
            rows = [s for s in steps if c in s.get("nll", {}) and "tf_kl_from_recompute" in s["nll"][c]]
            fid[c]["all_steps_tf_kl_from_recompute_mean"] = mean([s["nll"][c]["tf_kl_from_recompute"] for s in rows])
            fid[c]["all_steps_real_response_nll_mean"] = mean([s["nll"][c]["mean_nll"] for s in rows])
            fid[c]["all_steps_n"] = len(rows)
        summary["fidelity"] = fid
        # -- behaviour -----------------------------------------------------------------------------
        beh = {}
        rows = [s for s in edit_steps if "behaviour" in s]
        for c in conds:
            b = [s["behaviour"][c] for s in rows if c in s["behaviour"]]
            beh[c] = {"n": len(b), "tool_call_rate": mean([float(bool(x["tool"])) for x in b]),
                      "tools": dict(Counter(x["tool"] for x in b if x["tool"])),
                      "refetch_this_step_evicted": mean([float(bool(x["reacquires"])) for x in b]),
                      "refetch_any_evicted": mean([float(bool(x["reacquires_any_evicted"])) for x in b]),
                      "same_tool_as_recompute": mean([float(x["tool"] == s["behaviour"]["recompute"]["tool"]) for s, x in zip(rows, b)]),
                      "same_text_as_recompute": mean([float(x["text"] == s["behaviour"]["recompute"]["text"]) for s, x in zip(rows, b)])}
        summary["behaviour"] = beh
        real = [s["real_response"] for s in steps]
        summary["real_trajectory"] = {"tool_calls": sum(len(r["tools"]) for r in real),
                                      "steps_reacquiring_evicted": sum(1 for r in real if r["reacquires"]),
                                      "tools": dict(Counter(t for r in real for t in r["tools"]))}
        # -- probes (teacher-forced NLL of the truth; lower = the content is more available) -------
        probes = defaultdict(lambda: defaultdict(list))
        for s in edit_steps:
            for p in s.get("probes", []):
                outs = p["outputs"]
                if "recompute" not in outs:
                    continue
                for c, o in outs.items():
                    k = p["kind"] + ("_" + p["polarity"] if p.get("polarity") else "")
                    probes[f"{k}:nll"][c].append(o["nll"])
                    probes[f"{k}:nll_minus_recompute"][c].append(o["nll"] - outs["recompute"]["nll"])
                    if "oracle" in outs:
                        probes[f"{k}:nll_minus_oracle"][c].append(o["nll"] - outs["oracle"]["nll"])
                    probes[f"{k}:argmax_agree"][c].append(o["argmax_agree"])
                    if "correct" in o:
                        probes[f"{k}:forced_choice_correct"][c].append(float(o["correct"]))
                        probes[f"{k}:margin_yes"][c].append(o["margin_yes"])
                    if "lcs_ratio" in o:
                        probes[f"{k}:greedy_lcs"][c].append(o["lcs_ratio"])
        summary["probes"] = {k: {c: {"mean": mean(v), "median": med(v), "n": len(v)} for c, v in d.items()} for k, d in probes.items()}
        # fidelity split by whether glm's real response re-fetched previously evicted content
        split = {}
        for name, rows in (("real_refetch", [s for s in edit_steps if s["real_response"]["reacquires"]]),
                           ("real_no_refetch", [s for s in edit_steps if not s["real_response"]["reacquires"]])):
            split[name] = {"n": len(rows)}
            for c in conds:
                rr = [s for s in rows if c in s.get("nll", {})]
                split[name][c] = {"real_response_nll_mean": mean([s["nll"][c]["mean_nll"] for s in rr]),
                                  "tf_kl_from_recompute_mean": mean([s["nll"][c].get("tf_kl_from_recompute") for s in rr])}
        summary["fidelity_by_real_refetch"] = split
        # timeline of compounding: KL from recompute per edit step for shift vs shift1
        buckets = defaultdict(lambda: defaultdict(list))
        edits_seen = 0
        last_run_step = -1
        for s in steps:
            if s["step"] <= last_run_step:      # a new run starts (pooled input)
                edits_seen = 0
            last_run_step = s["step"]
            if not s.get("edit"):
                continue
            edits_seen += 1
            b = "1-3" if edits_seen <= 3 else "4-10" if edits_seen <= 10 else "11-20" if edits_seen <= 20 else "21+"
            for c in conds:
                v = s.get("nll", {}).get(c, {}).get("tf_kl_from_recompute")
                if v is not None:
                    buckets[b][c].append(v)
        summary["compounding"] = {b: {c: {"mean": mean(v), "median": med(v), "n": len(v)} for c, v in d.items()} for b, d in buckets.items()}
        summary["timeline"] = [{"step": s["step"], "kinds": s["edit_kinds"],
                                "kl": {c: round(s["nll"][c].get("tf_kl_from_recompute", float("nan")), 4) for c in conds if c in s.get("nll", {})},
                                "first_kl": {c: round(s["first_token"][c]["kl_from_recompute"], 4) for c in conds if c in s.get("first_token", {})}}
                               for s in edit_steps]
        # -- KV deviation --------------------------------------------------------------------------
        dev = defaultdict(list)
        for s in edit_steps:
            for c, v in s.get("kv_deviation", {}).items():
                if v:
                    dev[c].append(v)
        summary["kv_deviation"] = {c: {"key_cos_mean": mean([v["key_cos_mean"] for v in vs]),
                                       "value_cos_mean": mean([v["value_cos_mean"] for v in vs]),
                                       "frac_tokens_key_cos_below_0.9": mean([v["frac_tokens_key_cos_below_0.9"] for v in vs]),
                                       "key_cos_by_layer_mean": [round(sum(v["key_cos_by_layer"][i] for v in vs) / len(vs), 3)
                                                                 for i in range(len(vs[0]["key_cos_by_layer"]))] if vs else None}
                                   for c, vs in dev.items()}
        summary["gap_max_position"] = max((s.get("gap_max_position", 0) for s in steps), default=None)
        summary["seconds_total"] = steps[-1].get("elapsed_total") if steps else None
        out["runs"].append(summary)
    return out


def tables(summary: dict):
    for run in summary["runs"]:
        conds = list(run["fidelity"].keys())
        print(f"\n## {run['run']}  ({run['model']})")
        print(f"steps={run['steps']} edit_steps={run['edit_steps']} placeholder_steps={run['placeholder_steps']} "
              f"evicted_items={run['evicted_items']} evicted_tokens={run['evicted_tokens']} ctx_median={run['context_tokens_median']} "
              f"ctx_max={run['context_tokens_max']} edit_kinds={run['edit_kinds']}")
        print(f"real trajectory: {run['real_trajectory']}")
        print("\n### compute (tokens the model ran)")
        print("| policy | total tokens computed | on edit steps | share of recompute |")
        print("|---|---:|---:|---:|")
        base = run["tokens_computed_total"].get("recompute") or 1
        for c in conds:
            print(f"| {c} | {run['tokens_computed_total'][c]:,} | {run['tokens_computed_on_edit_steps'][c]:,} | {run['tokens_computed_total'][c] / base:.1%} |")
        print(f"tail tokens (unavoidable new input) = {run['tail_tokens_total']:,}; tokens after first change on edit steps: "
              f"median {run['tokens_after_first_change_edit_steps']['median']}, sum {run['tokens_after_first_change_edit_steps']['sum']:,}")
        print("\n### fidelity on edit steps (teacher-forced over the real response)")
        print("| policy | n | KL from recompute (mean / median / p90 / max) | top-1 agree w/ recompute | KL from oracle | top-1 agree w/ oracle | NLL of real response | first-token KL from recompute (median) | first-token top1 = recompute |")
        print("|---|---:|---|---:|---:|---:|---:|---:|---:|")
        for c in conds:
            f = run["fidelity"][c]
            print(f"| {c} | {f['n']} | {fmt(f['tf_kl_from_recompute_mean'])} / {fmt(f['tf_kl_from_recompute_median'])} / {fmt(f['tf_kl_from_recompute_p90'])} / {fmt(f['tf_kl_from_recompute_max'])} | {fmt(f['tf_top1_agree_recompute_mean'])} | "
                  f"{fmt(f['tf_kl_from_oracle_mean'])} | {fmt(f['tf_top1_agree_oracle_mean'])} | {fmt(f['real_response_nll_mean'])} | "
                  f"{fmt(f['first_token_kl_from_recompute_median'])} | {fmt(f['first_token_top1_eq_recompute'], 2)} |")
        print("\n### fidelity on ALL steps")
        print("| policy | n | KL from recompute (mean) | NLL of real response |")
        print("|---|---:|---:|---:|")
        for c in conds:
            f = run["fidelity"][c]
            print(f"| {c} | {f['all_steps_n']} | {fmt(f['all_steps_tf_kl_from_recompute_mean'])} | {fmt(f['all_steps_real_response_nll_mean'])} |")
        print("\n### behaviour: greedy next action on edit steps")
        print("| policy | n | tool-call rate | re-fetches content evicted this step | re-fetches any evicted content | same tool as recompute | identical text to recompute | tools |")
        print("|---|---:|---:|---:|---:|---:|---:|---|")
        for c in conds:
            b = run["behaviour"][c]
            print(f"| {c} | {b['n']} | {fmt(b['tool_call_rate'], 2)} | {fmt(b['refetch_this_step_evicted'], 2)} | {fmt(b['refetch_any_evicted'], 2)} | "
                  f"{fmt(b['same_tool_as_recompute'], 2)} | {fmt(b['same_text_as_recompute'], 2)} | {b['tools']} |")
        if run["probes"]:
            print("\n### probes about the evicted content (asked as the next user turn; NLL per token of the true answer)")
            kinds = list(run["probes"].keys())
            pconds = sorted({c for k in kinds for c in run["probes"][k]}, key=lambda c: conds.index(c) if c in conds else 99)
            print("| metric | " + " | ".join(pconds) + " | n |")
            print("|---|" + "---:|" * (len(pconds) + 1))
            for k in kinds:
                row = run["probes"][k]
                n = next(iter(row.values()))["n"] if row else 0
                print(f"| {k} | " + " | ".join(fmt(row.get(c, {}).get("mean"), 3) for c in pconds) + f" | {n} |")
        comp = run.get("compounding", {})
        if comp:
            print("\n### compounding: KL from recompute by number of edit steps already spliced in the run (mean / median)")
            cs = [c for c in conds if c != "recompute"]
            print("| edits so far | n | " + " | ".join(cs) + " |")
            print("|---|---:|" + "---|" * len(cs))
            for b in ("1-3", "4-10", "11-20", "21+"):
                if b in comp:
                    n = next(iter(comp[b].values()))["n"]
                    print(f"| {b} | {n} | " + " | ".join(f"{fmt(comp[b].get(c, {}).get('mean'))} / {fmt(comp[b].get(c, {}).get('median'))}" for c in cs) + " |")
        sp = run.get("fidelity_by_real_refetch", {})
        if sp:
            print("\n### edit steps split by whether the real (glm) response re-fetched evicted content")
            print("| subset | n | " + " | ".join(f"{c}: real-response NLL / KL from recompute" for c in conds) + " |")
            print("|---|---:|" + "---|" * len(conds))
            for name, d in sp.items():
                print(f"| {name} | {d['n']} | " + " | ".join(f"{fmt(d[c]['real_response_nll_mean'])} / {fmt(d[c]['tf_kl_from_recompute_mean'])}" for c in conds) + " |")
        if run["kv_deviation"]:
            print("\n### stale vs recomputed KV on reused blocks after the edit")
            print("| policy | key cos | value cos | frac tokens key cos < 0.9 |")
            print("|---|---:|---:|---:|")
            for c, v in run["kv_deviation"].items():
                print(f"| {c} | {fmt(v['key_cos_mean'])} | {fmt(v['value_cos_mean'])} | {fmt(v['frac_tokens_key_cos_below_0.9'])} |")
            sh = run["kv_deviation"].get("shift")
            if sh and sh.get("key_cos_by_layer_mean"):
                print("shift key cos by layer:", sh["key_cos_by_layer_mean"])
        print(f"gap-mode max RoPE position reached: {run['gap_max_position']}; replay wall time {run['seconds_total']} s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    summary = analyze(a.paths)
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=1))
    if a.tables or not a.json:
        tables(summary)
