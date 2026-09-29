#!/usr/bin/env python3
"""Aggregate the scored arms into the tables of research/10_rope_shift/README.md (offline).

Unit = one compaction event (paired across arms). Binary metrics get Wilson 95% intervals and exact
McNemar tests on discordant pairs; means get a bootstrap interval that resamples RUNS (events of one
run are correlated). The noise floor is recompute_alt: the same prompt as recompute, decoded in a
different batch, so "disagrees with recompute" has to be read against how often recompute disagrees
with itself.

    python research/10_rope_shift/analyze.py [--models qwen32b,qwen8b,glm47flash]
    python research/10_rope_shift/analyze.py --repair      # Part B tables (repair.py arms)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

DATA = Path(__file__).resolve().parent / "data" / "rope_shift_live"
Z = 1.959963984540054
ARMS = ["recompute_alt", "prefix", "shift", "norope", "oracle"]
REPAIR = Path(__file__).resolve().parent / "data" / "rope_shift_repair"
REPAIR_ARMS = ["shift", "epic32", "epic128", "blend05", "blend15", "rand15"]
MODEL_NAMES = {"qwen32b": "Qwen3-32B (GQA)", "qwen8b": "Qwen3-8B (GQA)", "glm47flash": "GLM-4.7-Flash (MLA)"}


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return p, centre - half, centre + half


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def boot_mean(values: list[float], clusters: list[str], iters: int = 2000, seed: int = 0):
    vals = np.asarray(values, dtype=float)
    if len(vals) == 0:
        return (float("nan"),) * 3
    groups = defaultdict(list)
    for v, c in zip(vals, clusters):
        groups[c].append(v)
    keys = list(groups)
    sums = np.array([sum(groups[k]) for k in keys])
    counts = np.array([len(groups[k]) for k in keys])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(iters, len(keys)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return float(vals.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def fmt_rate(k: int, n: int) -> str:
    if n == 0:
        return "—"
    p, lo, hi = wilson(k, n)
    return f"{100 * p:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}] (n={n})"


def fmt_mean(values, clusters, digits=3) -> str:
    if not values:
        return "—"
    m, lo, hi = boot_mean(values, clusters)
    return f"{m:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"


def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


OUT = DATA          # where scored_*.jsonl / events_all*.jsonl are read (--dir); runs + manifest stay in DATA


def load(model: str) -> list[dict]:
    path = OUT / f"scored_{model}.jsonl"
    return [json.loads(l) for l in open(path, encoding="utf-8")] if path.exists() else []


def index(recs: list[dict]) -> dict:
    return {(r["event_id"], r["tail"], r["arm"]): r for r in recs}


def paired(idx: dict, tail: str, arm: str, metric, base_arm: str = "recompute"):
    """[(event_id, label, value_arm, value_base)] for events that have both records."""
    out = []
    for (eid, t, a), rec in idx.items():
        if t != tail or a != arm:
            continue
        base = idx.get((eid, t, base_arm))
        va = metric(rec)
        vb = metric(base) if base is not None else None
        if va is not None:
            out.append((eid, rec["label"], va, vb))
    return out


# -- tables -----------------------------------------------------------------------------------------

def run_table() -> tuple[str, dict]:
    manifest = [json.loads(l) for l in open(DATA / "manifest.jsonl", encoding="utf-8")]
    metas = []
    for row in manifest:
        meta = DATA / "runs" / row["label"] / f"{row['label']}.meta.json"
        if meta.exists():
            metas.append({**json.loads(meta.read_text()), "family": row["family"], "codebase": row["codebase"]})
    from score import merged
    events = [json.loads(l) for l in merged(OUT / "events_all.jsonl", "events_all_s*.jsonl")]
    labels = {e["label"] for e in events}
    if OUT != DATA:                          # a smoke folder: only the runs it covers
        metas = [m for m in metas if m["label"] in labels]
    per_run = Counter(e["label"] for e in events)
    rows = []
    for fam in ("EXPLAIN", "MODIFY", "all"):
        ms = [m for m in metas if fam == "all" or m["family"] == fam]
        if not ms:
            continue
        ended = Counter(m["status"].split(" ")[0] for m in ms)
        ok = " / ".join(f"{ended.get(k, 0)}" for k in ("ok", "deadline", "killed"))
        lead = [m["lead_calls"] for m in ms]
        wall = [m["wall_s"] for m in ms if m.get("wall_s") is not None]   # salvaged runs have none
        ev = [per_run.get(m["label"], 0) for m in ms]
        rows.append([fam, len(ms), ok, f"{np.median(lead):.0f} ({np.percentile(lead, 10):.0f}–{np.percentile(lead, 90):.0f})",
                     f"{np.median(wall):.0f}", f"{np.mean(ev):.1f}", sum(1 for e in ev if e > 0)])
    kinds = Counter("+".join(e["kinds"]) for e in events)
    detail = {"runs": len(metas), "events": len(events), "kinds": dict(kinds.most_common())}
    md = table(["family", "runs", "ended: finished / 900 s deadline / killed", "lead calls, median (p10–p90)", "wall s, median",
                "compaction events / run", "runs with ≥1 event"], rows)
    md += "\n\n" + table(["edit kinds (all events)", "events", "share"],
                         [[k, v, f"{100 * v / max(1, len(events)):.1f}%"] for k, v in kinds.most_common(12)])
    return md, detail


def geometry_table(recs_by_model: dict) -> str:
    rows = []
    for model, recs in recs_by_model.items():
        shift = [r for r in recs if r["tail"] == "natural" and r["arm"] == "shift"]
        if not shift:
            continue
        clusters = [r["label"] for r in shift]
        g = lambda key: [r["plan"][key] for r in shift]
        after_prefix = [r["plan"]["new_len"] - r["plan"]["prefix_len"] for r in shift]
        saved = [1 - r["plan"]["fresh"] / a for r, a in zip(shift, after_prefix) if a > 0]
        rows.append([MODEL_NAMES.get(model, model), len(shift), f"{np.mean(g('new_len')):.0f}",
                     f"{np.mean(g('prefix_len')):.0f}", f"{np.mean(g('reused_after_edit')):.0f}",
                     f"{np.mean(g('fresh')):.0f}", fmt_mean(saved, clusters[:len(saved)]),
                     f"{np.median(np.abs(g('delta_min'))):.0f}", f"{np.mean(g('stages')):.2f}"])
    return table(["model", "events", "prompt tokens", "exact prefix", "reused after the edit",
                  "fresh (prefilled by shift)", "prefill saved vs prefix caching", "median |δ| (largest shift)",
                  "stages"], rows)


def next_round_table(idx: dict) -> str:
    rows = []
    for arm in ARMS:
        exact = paired(idx, "natural", arm, lambda r: r["same_as_recompute"])
        calls = paired(idx, "natural", arm, lambda r: r["metrics"].get("calls_match") if r["metrics"].get("kind") == "call" else None)
        names = paired(idx, "natural", arm, lambda r: r["metrics"].get("name_match")
                       if r["metrics"].get("kind") in ("call", "truncated_call") else None)
        inv = paired(idx, "natural", arm, lambda r: r["metrics"].get("inv_f1"))
        rouge = paired(idx, "natural", arm, lambda r: r["metrics"].get("text_rougeL") if r["metrics"].get("kind") == "text" else None)
        if not exact:
            continue
        rows.append([arm, fmt_rate(sum(v for _, _, v, _ in exact), len(exact)),
                     fmt_rate(sum(v for _, _, v, _ in calls), len(calls)),
                     fmt_rate(sum(v for _, _, v, _ in names), len(names)),
                     fmt_mean([v for _, _, v, _ in inv], [c for _, c, _, _ in inv]),
                     fmt_mean([v for _, _, v, _ in rouge], [c for _, c, _, _ in rouge])])
    return table(["arm (vs recompute)", "identical output", "same tool calls (AST)", "same tool names",
                  "invocation F1", "ROUGE-L (text rounds)"], rows)


def noise_test(idx: dict) -> str:
    """shift vs recompute_alt on the events that have both: McNemar on 'agrees with recompute'."""
    rows = []
    for arm in ("shift", "norope", "oracle", "prefix"):
        b = c = n = 0
        for (eid, tail, a), rec in idx.items():
            if tail != "natural" or a != "recompute_alt":
                continue
            other = idx.get((eid, tail, arm))
            if other is None:
                continue
            n += 1
            x, y = rec["same_as_recompute"], other["same_as_recompute"]
            b += x and not y
            c += y and not x
        if n:
            rows.append([arm, n, b, c, f"{mcnemar(b, c):.3g}"])
    # the cure against the compaction itself, on every event
    b = c = n = 0
    for (eid, tail, a), rec in idx.items():
        if tail != "natural" or a != "shift" or (eid, tail, "oracle") not in idx:
            continue
        n += 1
        x, y = idx[(eid, tail, "oracle")]["same_as_recompute"], rec["same_as_recompute"]
        b += x and not y
        c += y and not x
    rows.append(["shift vs oracle (all events)", n, f"oracle agrees, shift does not: {b}",
                 f"shift agrees, oracle does not: {c}", f"{mcnemar(b, c):.3g}"])
    return table(["arm", "paired events", "alt agrees, arm does not", "arm agrees, alt does not",
                  "McNemar p (identical output)"], rows)


def validity_table(idx: dict) -> str:
    rows = []
    for arm in ["recompute"] + ARMS:
        recs = [r for (e, t, a), r in idx.items() if t == "natural" and a == arm]
        if not recs:
            continue
        def rate(key, want=True):
            vals = [r["metrics"].get(key) for r in recs if r["metrics"].get(key) is not None]
            return fmt_rate(sum(1 for v in vals if v == want), len(vals))
        cite = [r["metrics"]["cite_valid"] for r in recs if r["metrics"].get("cite_valid") is not None]
        rows.append([arm, rate("malformed"), rate("unknown_tool"), rate("schema_ok", False),
                     rate("path_missing"), rate("edit_applies", False), rate("refetch"), rate("truncated"),
                     fmt_mean(cite, [r["label"] for r in recs if r["metrics"].get("cite_valid") is not None])])
    return table(["arm", "malformed call", "unknown tool", "schema violation", "missing path",
                  "edit does not apply", "re-fetch of in-context file", "truncated", "valid file:line citations"], rows)


def distribution_table(idx: dict) -> str:
    rows = []
    for tail in ("natural", "probe", "retention"):
        for arm in ARMS:
            recs = [r for (e, t, a), r in idx.items() if t == tail and a == arm and r.get("tf")
                    and "kl_mean" in (r.get("tf") or {})]
            if not recs:
                continue
            cl = [r["label"] for r in recs]
            kl = [r["tf"]["kl_mean"] for r in recs]
            top1 = [r["tf"]["top1_agree"] for r in recs]
            dnll = [r["tf"]["nll_arm"] - r["tf"]["nll_ref"] for r in recs]
            first = [r["first_div"] != 0 for r in recs]
            rows.append([tail, arm, len(recs), fmt_mean(kl, cl, 4), f"{np.median(kl):.4f}",
                         fmt_mean(top1, cl, 4), fmt_mean(dnll, cl, 4),
                         fmt_rate(sum(first), len(first))])
    return table(["tail", "arm", "n", "KL(recompute‖arm) nats/token", "median KL", "top-1 agreement",
                  "ΔNLL of recompute's tokens", "same first token"], rows)


def probe_table(idx: dict) -> str:
    rows = []
    for family in ("explain", "modify", "tool_call"):
        for arm in ["recompute"] + ARMS:
            recs = [r for (e, t, a), r in idx.items() if t == "probe" and a == arm and r["probe_family"] == family]
            if not recs:
                continue
            passed = sum(bool(r["metrics"].get("pass")) for r in recs)
            if arm == "recompute":
                test = ""
            else:
                b = c = 0
                for r in recs:
                    base = idx.get((r["event_id"], "probe", "recompute"))
                    if base is None:
                        continue
                    x, y = bool(base["metrics"].get("pass")), bool(r["metrics"].get("pass"))
                    b += x and not y
                    c += y and not x
                test = f"b={b} c={c} p={mcnemar(b, c):.3g}"
            extra = ""
            if family == "explain":
                extra = fmt_mean([r["metrics"]["recall"] for r in recs if r["metrics"].get("recall") is not None],
                                 [r["label"] for r in recs if r["metrics"].get("recall") is not None])
            elif family == "modify":
                wf = [r["metrics"].get("well_formed") for r in recs]
                extra = "well-formed " + fmt_rate(sum(bool(v) for v in wf), len(wf))
            else:
                ok = [r["metrics"].get("old_ok") for r in recs]
                extra = "old_text exact " + fmt_rate(sum(bool(v) for v in ok), len(ok))
            rows.append([family, arm, fmt_rate(passed, len(recs)), extra, test])
    return table(["probe", "arm", "pass rate", "component", "McNemar vs recompute (b: only recompute passes)"], rows)


def retention_table(idx: dict) -> str:
    rows = []
    for arm in ["recompute"] + ARMS:
        recs = [r for (e, t, a), r in idx.items() if t == "retention" and a == arm]
        if not recs:
            continue
        n = len(recs)
        rows.append([arm, n, fmt_rate(sum(r["metrics"]["answered"] for r in recs), n),
                     fmt_rate(sum(r["metrics"]["abstained"] for r in recs), n),
                     fmt_rate(sum(bool(r["metrics"].get("refetch")) for r in recs), n),
                     fmt_rate(sum(r["metrics"]["hallucinated"] for r in recs), n)])
    return table(["arm", "n", "recalls evicted content", "says it is gone", "re-reads the file", "wrong answer"], rows)


def strata_table(idx: dict) -> str:
    """Shift next to oracle on the same events: is the cure worse than the compaction itself?"""
    recs = [r for (e, t, a), r in idx.items() if t == "natural" and a == "shift"]
    groups: dict[str, list] = defaultdict(list)
    for r in recs:
        kinds = r["kinds"]
        groups["kind: " + ("snip" if "snip" in kinds else "placeholder" if "placeholder" in kinds
                           else "+".join(kinds))].append(r)
        d = abs(r["plan"]["delta_min"])
        groups["|δ|: " + ("<512" if d < 512 else "512–2k" if d < 2048 else "2k–8k" if d < 8192 else "≥8k")].append(r)
        share = r["plan"]["reused_after_edit"] / max(1, r["plan"]["new_len"])
        groups["reused-after-edit share: " + ("<25%" if share < .25 else "25–50%" if share < .5
                                              else "50–75%" if share < .75 else "≥75%")].append(r)
        groups["family: " + r["family"]].append(r)
    rows = []
    for name in sorted(groups):
        rs = groups[name]
        orc = [idx[(r["event_id"], "natural", "oracle")] for r in rs if (r["event_id"], "natural", "oracle") in idx]
        cells = []
        for arm_recs in (rs, orc):
            tf = [r for r in arm_recs if (r.get("tf") or {}).get("kl_mean") is not None]
            cells += [fmt_rate(sum(r["same_as_recompute"] for r in arm_recs), len(arm_recs)),
                      fmt_mean([r["tf"]["kl_mean"] for r in tf], [r["label"] for r in tf], 4)]
        rows.append([name, len(rs), *cells])
    return table(["stratum (natural tail)", "n", "shift: identical output", "shift: KL",
                  "oracle: identical output", "oracle: KL"], rows)


def cross_model_table(indices: dict) -> str:
    rows = []
    for model, idx in indices.items():
        for arm in ("recompute_alt", "shift", "norope", "oracle"):
            exact = paired(idx, "natural", arm, lambda r: r["same_as_recompute"])
            if not exact:
                continue
            tf = [r for (e, t, a), r in idx.items() if t == "natural" and a == arm
                  and (r.get("tf") or {}).get("kl_mean") is not None]
            probes = [r for (e, t, a), r in idx.items() if t == "probe" and a == arm]
            ret = [r for (e, t, a), r in idx.items() if t == "retention" and a == arm]
            rows.append([MODEL_NAMES.get(model, model), arm,
                         fmt_rate(sum(v for _, _, v, _ in exact), len(exact)),
                         fmt_mean([r["tf"]["top1_agree"] for r in tf], [r["label"] for r in tf], 4),
                         fmt_mean([r["tf"]["kl_mean"] for r in tf], [r["label"] for r in tf], 4),
                         fmt_rate(sum(bool(r["metrics"].get("pass")) for r in probes), len(probes)),
                         fmt_rate(sum(bool(r["metrics"].get("answered")) for r in ret), len(ret))])
    return table(["model (cache)", "arm", "identical next round", "top-1 agreement", "KL", "probe pass",
                  "retention recall"], rows)


# -- Part B: repair arms -----------------------------------------------------------------------------

def raw_texts(folder: Path, model: str, arms: tuple) -> dict:
    """(event, tail, arm) -> generated text, from a part's raw arms files."""
    out = {}
    for f in sorted(folder.glob(f"arms_{model}_s*.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["arm"] in arms:
                out.setdefault((r["event_id"], r["tail"], r["arm"]), r["text"])
    return out


def mechanism_table(models: list[str]) -> str:
    """Part B's one-pass override against Part A's staged splice, on the same prompts: shift should
    match Part A as often as recompute matches itself across the two sessions (batch noise)."""
    rows = []
    for model in models:
        a = raw_texts(DATA, model, ("recompute", "shift"))
        b = raw_texts(REPAIR, model, ("recompute", "shift"))
        for tail in ("natural", "probe", "retention"):
            cells = []
            for arm in ("recompute", "shift"):
                keys = [k for k in b if k[1] == tail and k[2] == arm and k in a]
                cells.append(fmt_rate(sum(a[k] == b[k] for k in keys), len(keys)) if keys else "—")
            rows.append([MODEL_NAMES.get(model, model), tail, *cells])
    return table(["model", "tail", "recompute: same text as Part A", "shift: same text as Part A"], rows)


def budget_table(idx: dict) -> str:
    rows = []
    for arm in REPAIR_ARMS:
        recs = [r for (e, t, a), r in idx.items() if t == "natural" and a == arm and r.get("repair")]
        if not recs:
            continue
        rep = [r["repair"] for r in recs]
        with_ratio = [(x["ratio"], r["label"]) for r, x in zip(recs, rep) if x.get("ratio") is not None]
        rows.append([arm, rep[0]["from_layer"], fmt_mean([v for v, _ in with_ratio], [c for _, c in with_ratio], 3),
                     f"{np.mean([x['selected'] for x in rep]):.0f}", f"{np.mean([x['candidates'] for x in rep]):.0f}",
                     f"{np.mean([x['spans'] for x in rep]):.1f}"])
    devs = [(r["deviation"], r["label"]) for (e, t, a), r in idx.items()
            if t == "natural" and a == "shift" and (r.get("deviation") or {}).get("candidates")]
    diag = []
    for key, what in (("mass_top5", "share of the check-layer key deviation carried by the top 5% of reused tokens"),
                      ("mass_top15", "… by the top 15%"),
                      ("blend15_in_run_heads", "blend15 tokens that are among the first 32 of their run"),
                      ("run_head_share", "run heads among all reused tokens (what chance would give)"),
                      ("blend15_spans", "contiguous spans per blend15 selection")):
        vals = [(d[key], c) for d, c in devs if d.get(key) is not None]
        diag.append([what, fmt_mean([v for v, _ in vals], [c for _, c in vals], 3)])
    return (table(["arm", "recomputed from layer", "share of reused tokens recomputed", "tokens recomputed",
                   "reused tokens after the edit", "spans"], rows) + "\n\n"
            + table(["CacheBlend selection diagnostic (natural tail, per event)", "mean [95% CI]"], diag))


def _mc(idx: dict, recs: list[dict], tail: str, base: str, pred) -> str:
    b = c = 0
    for r in recs:
        o = idx.get((r["event_id"], tail, base))
        if o is None:
            continue
        x, y = bool(pred(o)), bool(pred(r))
        b += x and not y
        c += y and not x
    return f"{b} / {c}, p={mcnemar(b, c):.2g}"


def costs_table(idx: dict, part_a: dict | None = None) -> str:
    """Part A's two costs under each repair arm. McNemar cells: b = only the base arm has the
    property, c = only this arm has it. The first row is Part A's recompute of the same prompts,
    tested against this session's: how far two recomputes differ from batch nondeterminism alone."""
    passed = lambda r: r["metrics"].get("pass")          # noqa: E731
    verbatim = lambda r: r["metrics"].get("search_in_context")  # noqa: E731
    gone = lambda r: r["metrics"].get("abstained")       # noqa: E731
    wrong = lambda r: r["metrics"].get("hallucinated")   # noqa: E731
    rows = []
    if part_a:
        mod = [r for (e, t, a), r in part_a.items() if t == "probe" and a == "recompute"
               and r["probe_family"] == "modify" and (e, t, "recompute") in idx]
        ret = [r for (e, t, a), r in part_a.items() if t == "retention" and a == "recompute"
               and (e, t, "recompute") in idx]
        rows.append(["recompute, Part A run", fmt_rate(sum(bool(passed(r)) for r in mod), len(mod)), "",
                     _mc(idx, mod, "probe", "recompute", passed),
                     fmt_rate(sum(bool(verbatim(r)) for r in mod), len(mod)),
                     fmt_rate(sum(bool(gone(r)) for r in ret), len(ret)), "",
                     _mc(idx, ret, "retention", "recompute", gone),
                     fmt_rate(sum(bool(wrong(r)) for r in ret), len(ret)),
                     _mc(idx, ret, "retention", "recompute", wrong)])
    for arm in ["recompute"] + REPAIR_ARMS:
        mod = [r for (e, t, a), r in idx.items() if t == "probe" and a == arm and r["probe_family"] == "modify"]
        ret = [r for (e, t, a), r in idx.items() if t == "retention" and a == arm]
        if not mod and not ret:
            continue
        vs = lambda recs, tail, pred, base: "" if arm == base else _mc(idx, recs, tail, base, pred)  # noqa: E731
        rows.append([arm, fmt_rate(sum(bool(passed(r)) for r in mod), len(mod)),
                     vs(mod, "probe", passed, "shift"), vs(mod, "probe", passed, "recompute"),
                     fmt_rate(sum(bool(verbatim(r)) for r in mod), len(mod)),
                     fmt_rate(sum(bool(gone(r)) for r in ret), len(ret)),
                     vs(ret, "retention", gone, "shift"), vs(ret, "retention", gone, "recompute"),
                     fmt_rate(sum(bool(wrong(r)) for r in ret), len(ret)), vs(ret, "retention", wrong, "recompute")])
    return table(["arm", "modify probe: pass", "vs shift (b / c)", "vs recompute (b / c)",
                  "SEARCH block verbatim in context", "retention: says it is gone", "vs shift (b / c)",
                  "vs recompute (b / c)", "retention: wrong answer", "vs recompute (b / c)"], rows)


def selection_table(idx: dict) -> str:
    """Arms at (about) the same budget, paired per event: does the selection rule matter?
    KL cells are the paired mean difference first − second (negative: the first is closer to
    recompute); McNemar cells read b / c with b = only the first arm has the property."""
    kl = lambda r: (r.get("tf") or {}).get("kl_mean")    # noqa: E731
    rows = []
    for a, b in (("blend15", "rand15"), ("blend05", "epic32"), ("blend15", "epic128"), ("epic128", "rand15")):
        nat = [(r, idx[(e, t, b)]) for (e, t, arm), r in idx.items()
               if t == "natural" and arm == a and (e, t, b) in idx]
        if not nat:
            continue
        both = [(x, y) for x, y in nat if kl(x) is not None and kl(y) is not None]
        same_b = sum(x["same_as_recompute"] and not y["same_as_recompute"] for x, y in nat)
        same_c = sum(y["same_as_recompute"] and not x["same_as_recompute"] for x, y in nat)

        def mc(tail, pred, family=None):
            pairs = [(r, idx[(e, t, b)]) for (e, t, arm), r in idx.items()
                     if t == tail and arm == a and (e, t, b) in idx
                     and (family is None or r["probe_family"] == family)]
            bb = sum(bool(pred(x)) and not pred(y) for x, y in pairs)
            cc = sum(bool(pred(y)) and not pred(x) for x, y in pairs)
            return f"{bb} / {cc}, p={mcnemar(bb, cc):.2g}"
        rows.append([f"{a} vs {b}", len(nat), f"{same_b} / {same_c}, p={mcnemar(same_b, same_c):.2g}",
                     fmt_mean([kl(x) - kl(y) for x, y in both], [x["label"] for x, _ in both], 4),
                     mc("probe", lambda r: r["metrics"].get("pass"), "modify"),
                     mc("retention", lambda r: r["metrics"].get("abstained"))])
    return table(["pair (first vs second)", "events", "identical next round (b / c)",
                  "KL difference, first − second", "modify probe pass (b / c)",
                  "retention: says it is gone (b / c)"], rows)


def repair_kind_table(idx: dict) -> str:
    """The repair arms by the kind of compaction edit: snip (a run of messages archived) or placeholder
    (one tool result replaced), on the natural tail (KL) and the retention tail."""
    def kind(r):
        k = r["kinds"]
        return "snip" if "snip" in k else "placeholder" if "placeholder" in k else "+".join(k)
    rows = []
    for arm in ["recompute"] + REPAIR_ARMS:
        for kd in ("snip", "placeholder"):
            nat = [r for (e, t, a), r in idx.items() if t == "natural" and a == arm and kind(r) == kd]
            ret = [r for (e, t, a), r in idx.items() if t == "retention" and a == arm and kind(r) == kd]
            if not nat and not ret:
                continue
            tf = [r for r in nat if (r.get("tf") or {}).get("kl_mean") is not None]
            rows.append([arm, kd, len(nat),
                         fmt_mean([r["tf"]["kl_mean"] for r in tf], [r["label"] for r in tf], 4) if tf else "—",
                         fmt_rate(sum(bool(r["metrics"].get("abstained")) for r in ret), len(ret)),
                         fmt_rate(sum(bool(r["metrics"].get("hallucinated")) for r in ret), len(ret))])
    return table(["arm", "edit kind", "events", "KL (natural)", "retention: says it is gone",
                  "retention: wrong answer"], rows)


def repair_summary_table(indices: dict) -> str:
    rows = []
    for model, idx in indices.items():
        for arm in ["recompute"] + REPAIR_ARMS:
            nat = [r for (e, t, a), r in idx.items() if t == "natural" and a == arm]
            if not nat:
                continue
            tf = [r for r in nat if (r.get("tf") or {}).get("kl_mean") is not None]
            mod = [r for (e, t, a), r in idx.items() if t == "probe" and a == arm and r["probe_family"] == "modify"]
            ret = [r for (e, t, a), r in idx.items() if t == "retention" and a == arm]
            share = [r["repair"]["ratio"] for r in nat if (r.get("repair") or {}).get("ratio") is not None]
            rows.append([MODEL_NAMES.get(model, model), arm, f"{100 * np.mean(share):.1f}%" if share else "100%",
                         fmt_rate(sum(r["same_as_recompute"] for r in nat), len(nat)) if arm != "recompute" else "—",
                         fmt_mean([r["tf"]["kl_mean"] for r in tf], [r["label"] for r in tf], 4) if tf else "—",
                         fmt_rate(sum(bool(r["metrics"].get("pass")) for r in mod), len(mod)),
                         fmt_rate(sum(bool(r["metrics"].get("abstained")) for r in ret), len(ret)),
                         fmt_rate(sum(bool(r["metrics"].get("hallucinated")) for r in ret), len(ret))])
    return table(["model (cache)", "arm", "reused tokens recomputed", "identical next round", "KL (natural)",
                  "modify probe pass", "retention: says it is gone", "retention: wrong answer"], rows)


def part_a_index(model: str) -> dict:
    path = DATA / f"scored_{model}.jsonl"
    return index([json.loads(l) for l in open(path, encoding="utf-8")]) if path.exists() else {}


def repair_main(models: list[str], out_md: str) -> None:
    global OUT, ARMS
    OUT, ARMS = REPAIR, REPAIR_ARMS
    recs_by_model = {m: r for m in models if (r := load(m))}
    indices = {m: index(r) for m, r in recs_by_model.items()}
    md = ["# 10_rope_shift Part B tables: selective recompute", "",
          "Generated by `analyze.py --repair` from `data/rope_shift_repair/scored_*.jsonl` (`repair.py`). "
          "Unit = one compaction event; `recompute` is this session's full prefill. Rates carry Wilson 95% "
          "intervals; means carry a bootstrap interval over runs; McNemar cells read b / c.", "",
          "## B0 Mechanism: one-pass override against Part A's staged splice", "", mechanism_table(list(indices)), "",
          "## B1 The two costs of the RoPE cure under each repair", "",
          *[x for m, idx in indices.items()
            for x in (f"### {MODEL_NAMES.get(m, m)}", "", costs_table(idx, part_a_index(m)), "")],
          "## B2 Across models", "", repair_summary_table(indices), ""]
    for model, idx in indices.items():
        md += [f"## {MODEL_NAMES.get(model, model)}", "", "### B3 Budgets and what CacheBlend selects", "",
               budget_table(idx), "", "### B3b Selection rules at equal budget", "", selection_table(idx), "",
               "### B3c By edit kind", "", repair_kind_table(idx), "",
               "### B4 Next round against recompute", "", next_round_table(idx), "",
               "### B5 Validity of the next round", "", validity_table(idx), "",
               "### B6 Distribution fidelity (teacher-forced on recompute's output)", "", distribution_table(idx), "",
               "### B7 Gold probes", "", probe_table(idx), "", "### B8 Retention", "", retention_table(idx), ""]
    Path(out_md).write_text("\n".join(md), encoding="utf-8")
    Path(out_md).with_suffix(".json").write_text(json.dumps(
        {"models": {m: len(r) for m, r in recs_by_model.items()}}, indent=2), encoding="utf-8")
    print(f"tables -> {out_md}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="qwen32b,qwen8b,glm47flash")
    ap.add_argument("--dir", default=str(DATA), help="folder with scored_*.jsonl and events_all*.jsonl")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--repair", action="store_true", help="Part B tables from data/rope_shift_repair/")
    args = ap.parse_args()
    if args.repair:
        repair_main(args.models.split(","), args.out_md or str(REPAIR / "rope_shift_repair_tables.md"))
        return
    global OUT
    OUT = Path(args.dir)
    args.out_md = args.out_md or str(OUT / "rope_shift_tables.md")
    recs_by_model = {m: load(m) for m in args.models.split(",")}
    recs_by_model = {m: r for m, r in recs_by_model.items() if r}
    indices = {m: index(r) for m, r in recs_by_model.items()}
    runs_md, runs_detail = run_table()
    md = ["# 10_rope_shift tables", "",
          "Generated by `analyze.py`. Unit = one compaction event; every arm is compared with the same "
          "model's `recompute` of the same prompt. Rates carry Wilson 95% intervals; means carry a "
          "bootstrap interval over runs.", "", "## T0 Runs and events", "", runs_md, "",
          "## T1 Splice geometry (natural tail)", "", geometry_table(recs_by_model), ""]
    for model, idx in indices.items():
        name = MODEL_NAMES.get(model, model)
        md += [f"## {name}", "", "### T2 Next round against recompute", "", next_round_table(idx), "",
               "### T3 Noise floor: does the arm disagree more often than recompute disagrees with itself?", "",
               noise_test(idx), "", "### T4 Validity of the next round (no reference needed)", "",
               validity_table(idx), "", "### T5 Distribution fidelity (teacher-forced on recompute's output)", "",
               distribution_table(idx), "", "### T6 Gold probes", "", probe_table(idx), "",
               "### T7 Retention of content evicted by this step", "", retention_table(idx), "",
               "### T8 Strata", "", strata_table(idx), ""]
    md += ["## T9 Across models and cache layouts", "", cross_model_table(indices), ""]
    Path(args.out_md).write_text("\n".join(md), encoding="utf-8")
    Path(args.out_md).with_suffix(".json").write_text(json.dumps(
        {"runs": runs_detail, "models": {m: len(r) for m, r in recs_by_model.items()}}, indent=2), encoding="utf-8")
    print(f"tables -> {args.out_md}")


if __name__ == "__main__":
    main()
