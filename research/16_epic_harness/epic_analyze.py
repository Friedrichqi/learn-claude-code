#!/usr/bin/env python3
"""Analysis of the EPIC study's live runs (offline, CPU): grading, per-run metrics, pilot gates, tables.

    python research/16_epic_harness/epic_analyze.py runs  --live data/live/pilot     # per-run metrics
    python research/16_epic_harness/epic_analyze.py pilot --live data/live/pilot     # gates G1-G4, G6, G8
    python research/16_epic_harness/epic_analyze.py main  --live data/live/main      # tables T0-T9

`runs` grades every finished run with epic_grade (deterministic graders) and joins, per run: status,
lead calls, cap hits, edit events by kind (topic 10's anchored markers, events.classify), process
metrics (tool calls, failed edits, re-fetches of content still in context, malformed calls), the
server's per-call statistics (prefill accounting P/E/S/H/C, shadow probes) and the A/A pairing keys.
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import random
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402

ARMS_ORDER = ["prefix", "prefix_aa", "shift", "epic32", "epic64", "epic128"]


def live_dir(arg: str) -> Path:
    return ec.DATA / arg if not Path(arg).is_absolute() else Path(arg)


def open_jsonl(path: Path) -> list[dict]:
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def manifest_of(live: Path) -> list[dict]:
    paths = sorted(live.glob("manifest_*.jsonl"))
    return [json.loads(l) for p in paths for l in p.read_text().splitlines() if l.strip()]


# -- per-run metrics ----------------------------------------------------------------------------------

def run_metrics(run_dir: Path) -> dict:
    from events import classify, lead_calls  # 10_rope_shift
    label = run_dir.name
    meta = json.loads((run_dir / f"{label}.meta.json").read_text())
    reqs = sorted(run_dir.glob("*.requests.jsonl*"))
    recs = open_jsonl(reqs[0]) if reqs else []
    leads = lead_calls(recs)
    kinds = Counter()
    first_edit = None
    for i, (prev, cur) in enumerate(zip(leads, leads[1:])):
        e = classify(prev, cur)
        if e is not None:
            for k in e["kinds"]:
                kinds[k] += 1
            if first_edit is None:
                first_edit = i + 1
    tool_calls = 0
    actions = []
    reads: Counter = Counter()
    rereads = 0
    for r in leads:
        for b in (r.get("response") or {}).get("content") or []:
            if b.get("type") == "tool_use":
                tool_calls += 1
                actions.append(f"{b.get('name')}:{json.dumps(b.get('input'), sort_keys=True)[:200]}")
                if b.get("name") == "read_file":
                    key = json.dumps(b.get("input"), sort_keys=True)
                    rereads += reads[key] > 0
                    reads[key] += 1
    # tool errors: the tool results each lead call delivers (its last user message holds the new ones)
    tool_errors = 0
    for r in leads[1:]:
        last = (r.get("messages") or [{}])[-1]
        content = last.get("content") if isinstance(last, dict) else None
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                text = b.get("content")
                text = text if isinstance(text, str) else json.dumps(text)
                tool_errors += text.lstrip().startswith("Error")
    out_tokens = sum(int(((r.get("response") or {}).get("usage") or {}).get("output_tokens") or 0) for r in leads)
    return {"label": label, "status": meta.get("status"), "wall_s": meta.get("wall_s"),
            "lead": meta.get("lead"), "calls": meta.get("calls"), "overflows": meta.get("overflows"),
            "refused": meta.get("refused"), "edits": dict(kinds), "n_edit_calls": sum(
                1 for p, c in zip(leads, leads[1:]) if classify(p, c) is not None),
            "first_edit_call": first_edit, "tool_calls": tool_calls, "out_tokens": out_tokens,
            "rereads": rereads, "tool_errors": tool_errors, "cap_hit": int(meta.get("status") == "budget"),
            "actions": actions}


def grade(live: Path, manifest_path: Path | None = None) -> Path:
    out = live / "graded.jsonl"
    man = manifest_path or sorted(live.glob("manifest_*.jsonl"))[0]
    cmd = [ec.PYTHON, str(ec.HERE / "epic_grade.py"), "grade", "--manifest", str(man), "--runs",
           str(live / "runs"), "--out", str(out)]
    subprocess.run(cmd, check=True, cwd=ec.REPO)
    return out


def server_calls(live: Path) -> dict[tuple[str, int], dict]:
    out = {}
    for path in ec.server_logs(live):
        for rec in open_jsonl(path):
            if rec.get("ev") == "call":
                out[(rec["spec"], rec["call"])] = rec
    return out


def cmd_runs(args) -> None:
    live = live_dir(args.live)
    graded = {}
    if not args.no_grade:
        for rec in open_jsonl(grade(live)):
            graded[rec.get("run_label") or rec.get("label")] = rec
    man = {r["run_label"]: r for r in manifest_of(live)}
    srv = server_calls(live)
    by_spec = defaultdict(list)
    for (spec, _call), rec in srv.items():
        by_spec[spec.rsplit("~a", 1)[0]].append(rec)
    rows = []
    for run_dir in sorted((live / "runs").iterdir()):
        label = run_dir.name
        if not (run_dir / f"{label}.done").exists():
            continue
        m = run_metrics(run_dir)
        row = man.get(label, {})
        g = graded.get(label, {})
        calls = by_spec.get(label, [])
        acc = {k: sum(int(c.get(k) or 0) for c in calls) for k in ("P", "E", "S", "H", "C", "F")}
        # rows vLLM's prefix cache served beyond the plan's p0 were neither computed nor overridden
        acc["S"] -= sum(int(c.get("apc_over_S") or 0) for c in calls)
        acc["C"] -= sum(int(c.get("apc_over_C") or 0) for c in calls)
        shadows = [c["shadow"] for c in calls if c.get("shadow")]
        m.update({"arm": row.get("arm"), "regime": row.get("regime"), "cell": row.get("cell"),
                  "block": row.get("block"), "template": row.get("template"), "codebase": row.get("codebase"),
                  "score": g.get("score"), "pass": g.get("pass"), "grade_error": g.get("error"),
                  "server": acc, "server_calls": len(calls), "apc_hit": sum(int(c.get("apc_hit") or 0) for c in calls),
                  "shadow_n": len(shadows),
                  "shadow_tf_kl": _mean(s.get("tf_kl") for s in shadows),
                  "shadow_first_kl": _mean(s.get("first_kl") for s in shadows),
                  "shadow_top1": _mean(s.get("tf_top1") for s in shadows),
                  "cf_identical": _mean(s.get("cf_identical") for s in shadows if "cf_identical" in s)})
        rows.append(m)
    path = live / "runs_metrics.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} runs -> {path}")


def _mean(values):
    v = [x for x in values if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return statistics.mean(v) if v else None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


# -- pilot gates ----------------------------------------------------------------------------------------

def cmd_pilot(args) -> None:
    live = live_dir(args.live)
    rows = [json.loads(l) for l in (live / "runs_metrics.jsonl").read_text().splitlines()]
    lines = ["# Pilot gates (plan §5)", ""]
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[(r["regime"], r["arm"])].append(r)
    lines += ["| regime | arm | runs | statuses | lead calls (median) | edit calls/run (median) | "
              "score (mean) | pass | wall s (median) |", "|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(by_arm):
        rs = by_arm[key]
        st = Counter(r["status"] for r in rs)
        sc = [r["score"] for r in rs if r["score"] is not None]
        ps = [r["pass"] for r in rs if r["pass"] is not None]
        lines.append(f"| {key[0]} | {key[1]} | {len(rs)} | {dict(st)} | {statistics.median(r['lead'] or 0 for r in rs)} | "
                     f"{statistics.median(r['n_edit_calls'] for r in rs)} | "
                     f"{statistics.mean(sc) if sc else float('nan'):.2f} | {sum(ps)}/{len(ps)} | "
                     f"{statistics.median(r['wall_s'] or 0 for r in rs):.0f} |")
    # G2: A/A identical through the first edit
    blocks = defaultdict(dict)
    for r in rows:
        blocks[r["block"]][r["arm"]] = r
    aa_same, aa_n, arm_same = 0, 0, Counter()
    for b, arms in blocks.items():
        if "prefix" in arms and "prefix_aa" in arms:
            a, c = arms["prefix"]["actions"], arms["prefix_aa"]["actions"]
            fe = arms["prefix"]["first_edit_call"] or len(a)
            aa_n += 1
            aa_same += a[:fe] == c[:fe]
    g2 = aa_same / aa_n if aa_n else float("nan")
    # G3 edit rates
    r1 = [r for r in rows if r["regime"] == "R1"]
    r2 = [r for r in rows if r["regime"] == "R2"]
    g3a = sum(1 for r in r1 if r["n_edit_calls"] > 0) / max(1, len(r1))
    g3b = statistics.median(r["n_edit_calls"] for r in r1) if r1 else 0
    g3c = sum(1 for r in r2 if r["edits"].get("summary", 0) > 0) / max(1, len(r2))
    g3d = sum(1 for r in r2 if str(r["status"]).startswith(("error", "exit"))) / max(1, len(r2))
    # G4 base rates per template (prefix arm)
    tmpl = defaultdict(list)
    for r in rows:
        if r["arm"] in ("prefix", "prefix_aa") and r["pass"] is not None:
            tmpl[r["template"]].append(int(r["pass"]))
    # G6 run time and caps
    pre = [r["wall_s"] for r in rows if r["arm"] == "prefix" and r["wall_s"]]
    emu = [r["wall_s"] for r in rows if r["arm"] in ("shift", "epic128") and r["wall_s"]]
    caps = sum(1 for r in rows if r["status"] == "budget") / max(1, len(rows))
    lines += ["", f"- **G2 CRN**: A/A identical through the first edit in {aa_same}/{aa_n} blocks ({g2:.0%}; gate >= 80%)",
              f"- **G3 edits**: R1 runs with an edit {g3a:.0%} (>= 75%), median edit calls {g3b} (>= 10); "
              f"R2 runs with a summary/reactive compaction {g3c:.0%} (>= 50%), fatal {g3d:.0%} (< 10%)",
              "- **G4 base rates** (prefix + prefix_aa pass rate per template): " +
              ", ".join(f"{t} {statistics.mean(v):.2f} (n={len(v)})" for t, v in sorted(tmpl.items())),
              f"- **G6**: emulated / prefix median wall {statistics.median(emu) / statistics.median(pre) if pre and emu else float('nan'):.2f}x "
              f"(<= 1.5x); cap hits {caps:.0%} (<= 25%)"]
    (live / "pilot_gates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    del arm_same


# -- main tables --------------------------------------------------------------------------------------------

def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts b (only base) and c (only arm)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_blocks(rows: list[dict], base: str, arm: str, key: str) -> list[tuple]:
    """(cell, base value, arm value) for every block run under both arms."""
    blocks = defaultdict(dict)
    for r in rows:
        if r.get(key) is not None:
            blocks[r["block"]][r["arm"]] = r
    return [(b[base]["cell"], b[base][key], b[arm][key]) for b in blocks.values() if base in b and arm in b]


def boot_diffs(pairs: list[tuple], reps: int = 10_000, seed: str = "boot") -> tuple[float, list[float]]:
    """Mean paired difference (arm - base) and its sorted bootstrap distribution, resampling cells (clusters)."""
    by_cell = defaultdict(list)
    for cell, b, a in pairs:
        by_cell[cell].append(float(a) - float(b))
    cells = list(by_cell)
    mean = statistics.mean(d for c in cells for d in by_cell[c])
    rng = random.Random(seed)
    boots = []
    for _ in range(reps):
        sample = [d for _ in cells for d in by_cell[rng.choice(cells)]]
        boots.append(statistics.mean(sample))
    return mean, sorted(boots)


def cluster_bootstrap(pairs: list[tuple], reps: int = 10_000, seed: str = "boot") -> tuple[float, float, float]:
    """Mean paired difference (arm - base) with a 95% interval resampling cells (clusters)."""
    if not pairs:
        return (float("nan"),) * 3
    mean, boots = boot_diffs(pairs, reps, seed)
    return mean, boots[int(0.025 * reps)], boots[int(0.975 * reps) - 1]


def boot_p(boots: list[float]) -> float:
    """Two-sided bootstrap p-value of a zero difference (floored at 1 / reps)."""
    below = sum(1 for b in boots if b <= 0) / len(boots)
    above = sum(1 for b in boots if b >= 0) / len(boots)
    return max(1 / len(boots), min(1.0, 2 * min(below, above)))


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def pass_hat_k(passes: list[int], k: int) -> float:
    """tau-bench's unbiased pass^k for one task with n trials, c successes: C(c,k)/C(n,k)."""
    n, c = len(passes), sum(passes)
    return math.comb(c, k) / math.comb(n, k) if n >= k else float("nan")


def levenshtein(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cmd_main(args) -> None:
    live = live_dir(args.live)
    rows = [json.loads(l) for l in (live / "runs_metrics.jsonl").read_text().splitlines()]
    margin = args.margin
    lines = [f"# E-B live runs: {live.name}", "",
             "Generated by `epic_analyze.py main`. Paired by block (same cell, repeat, nonce, lane, seeds); "
             "differences are arm minus prefix; 95% intervals from a cluster bootstrap over cells (10k).", ""]
    for regime in ("R1", "R2"):
        rs = [r for r in rows if r["regime"] == regime]
        if not rs:
            continue
        arms = [a for a in ARMS_ORDER if any(r["arm"] == a for r in rs)]
        lines += [f"## {regime}", "", "**T0 runs**", "",
                  "| arm | runs | ok / budget / deadline / other | lead calls (median) | edit calls (median) | "
                  "output tokens (median) |", "|---|---|---|---|---|---|"]
        for arm in arms:
            ar = [r for r in rs if r["arm"] == arm]
            st = Counter(r["status"] if r["status"] in ("ok", "budget", "deadline") else "other" for r in ar)
            lines.append(f"| {arm} | {len(ar)} | {st['ok']} / {st['budget']} / {st['deadline']} / {st['other']} | "
                         f"{statistics.median(r['lead'] or 0 for r in ar):.0f} | "
                         f"{statistics.median(r['n_edit_calls'] for r in ar):.0f} | "
                         f"{statistics.median(r['out_tokens'] for r in ar):.0f} |")
        lines += ["", f"**T1 primary: task score, paired non-inferiority (margin -{margin})**", "",
                  "| arm | pairs | prefix mean | arm mean | difference [95% CI] | NI (lower > -margin) | "
                  "pass: only prefix / only arm (McNemar p) |", "|---|---|---|---|---|---|---|"]
        for arm in arms:
            if arm == "prefix":
                continue
            pairs = paired_blocks(rs, "prefix", arm, "score")
            if not pairs:
                continue
            d, lo, hi = cluster_bootstrap(pairs)
            pp = paired_blocks(rs, "prefix", arm, "pass")
            b = sum(1 for _c, x, y in pp if x and not y)
            c = sum(1 for _c, x, y in pp if y and not x)
            lines.append(f"| {arm} | {len(pairs)} | {statistics.mean(p[1] for p in pairs):.3f} | "
                         f"{statistics.mean(p[2] for p in pairs):.3f} | {d:+.3f} [{lo:+.3f}, {hi:+.3f}] | "
                         f"{'yes' if lo > -margin else 'no'} | {b} / {c} (p={mcnemar_exact(b, c):.3f}) |")
        lines += secondary_tables(rs, arms)
        # pass^k over repeats of a cell
        lines += ["", "**T4 reliability** (pass^k over the repeats of each cell, mean over cells):", "",
                  "| arm | cells | pass^1 | pass^2 | pass^3 |", "|---|---|---|---|---|"]
        for arm in arms:
            cells = defaultdict(list)
            for r in rs:
                if r["arm"] == arm and r["pass"] is not None:
                    cells[r["cell"]].append(int(r["pass"]))
            vals = {k: [pass_hat_k(v, k) for v in cells.values() if len(v) >= k] for k in (1, 2, 3)}
            lines.append(f"| {arm} | {len(cells)} | " + " | ".join(
                f"{statistics.mean(vals[k]):.2f}" if vals[k] else "nan" for k in (1, 2, 3)) + " |")
        # process, divergence
        lines += ["", "**T3 process and divergence from prefix** (per run; divergence = normalized edit distance "
                  "of the action sequence, against the A/A pair)", "",
                  "| arm | tool calls | edit calls | first action differing (median) | action edit distance (mean) |",
                  "|---|---|---|---|---|"]
        blocks = defaultdict(dict)
        for r in rs:
            blocks[r["block"]][r["arm"]] = r
        for arm in arms:
            ar = [r for r in rs if r["arm"] == arm]
            divs, firsts = [], []
            for b in blocks.values():
                if arm in b and "prefix" in b and arm != "prefix":
                    x, y = b["prefix"]["actions"], b[arm]["actions"]
                    k = next((i for i, (u, v) in enumerate(zip(x, y)) if u != v), min(len(x), len(y)))
                    firsts.append(k)
                    divs.append(levenshtein(x[:200], y[:200]) / max(1, max(len(x[:200]), len(y[:200]))))
            lines.append(f"| {arm} | {statistics.median(r['tool_calls'] for r in ar):.0f} | "
                         f"{statistics.median(r['n_edit_calls'] for r in ar):.0f} | "
                         f"{statistics.median(firsts) if firsts else '—'} | "
                         f"{statistics.mean(divs) if divs else float('nan'):.3f} |")
        # L4 accounting and on-policy shadow fidelity
        lines += ["", "**T7 prefill accounting** (server plans, lead calls of EPIC arms; prefix from vLLM's cache "
                  "hits) and **on-policy shadow fidelity** (fresh recompute of the same prompt)", "",
                  "| arm | prompt tokens | computed | reused stale | run heads | KV hit rate | shadows | "
                  "first-token KL | reply KL | reply top-1 | counterfactual identical |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm in arms:
            ar = [r for r in rs if r["arm"] == arm]
            P = sum(r["server"]["P"] for r in ar)
            if arm in ("prefix", "prefix_aa"):
                C = P - sum(r["apc_hit"] for r in ar)
                S = H = 0
            else:
                C = sum(r["server"]["C"] for r in ar)
                S = sum(r["server"]["S"] for r in ar)
                H = sum(r["server"]["H"] for r in ar)
            hit = 1 - C / P if P else float("nan")
            sh = [r for r in ar if r["shadow_n"]]
            lines.append(f"| {arm} | {P:,} | {C:,} | {S:,} | {H:,} | {hit:.1%} | {sum(r['shadow_n'] for r in ar)} | "
                         f"{_fmt(_mean(r['shadow_first_kl'] for r in sh))} | {_fmt(_mean(r['shadow_tf_kl'] for r in sh))} | "
                         f"{_fmt(_mean(r['shadow_top1'] for r in sh))} | {_fmt(_mean(r['cf_identical'] for r in sh))} |")
        lines.append("")
    (live / "main_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.4f}" if abs(v) < 1 else f"{v:.2f}"


def _ci(pairs: list[tuple]) -> str:
    d, lo, hi = cluster_bootstrap(pairs)
    return f"{d:+.3f} [{lo:+.3f}, {hi:+.3f}]"


def secondary_tables(rs: list[dict], arms: list[str]) -> list[str]:
    """Plan §3.6 secondaries: the repair benefit (EPIC-k minus shift, on the blocks that ran shift) and the dose
    trend over k (per-block least-squares slope of the score on k in {0 (shift), 32, 64, 128}), Holm-corrected;
    the score by template; loops (call-cap hits) and lead calls, paired against prefix."""
    epics = [a for a in ("epic32", "epic64", "epic128") if a in arms]
    fam = []
    for arm in epics:
        pairs = paired_blocks(rs, "shift", arm, "score")
        if pairs:
            d, boots = boot_diffs(pairs)
            fam.append((f"{arm} - shift", len(pairs), d, boots))
    blocks = defaultdict(dict)
    for r in rs:
        if r.get("score") is not None:
            blocks[r["block"]][r["arm"]] = r
    kval = {"shift": 0, "epic32": 32, "epic64": 64, "epic128": 128}
    slopes = []
    for b in blocks.values():
        pts = [(kval[a], float(b[a]["score"])) for a in kval if a in b]
        if len(pts) >= 3:
            mk = statistics.mean(k for k, _ in pts)
            my = statistics.mean(y for _, y in pts)
            sxx = sum((k - mk) ** 2 for k, _ in pts)
            slopes.append((next(iter(b.values()))["cell"], 0.0, 100 * sum((k - mk) * (y - my) for k, y in pts) / sxx))
    if slopes:
        d, boots = boot_diffs(slopes)
        fam.append(("dose: score per 100 tokens of k", len(slopes), d, boots))
    lines = []
    if fam:
        adj = holm([boot_p(bt) for *_x, bt in fam])
        lines += ["", "**T1b secondary** (score; cluster bootstrap over cells, 10k; Holm over the four tests):", "",
                  "| comparison | blocks | difference [95% CI] | p | Holm p |", "|---|---|---|---|---|"]
        for (name, n, d, bt), pa in zip(fam, adj):
            lo, hi = bt[int(0.025 * len(bt))], bt[int(0.975 * len(bt)) - 1]
            lines.append(f"| {name} | {n} | {d:+.3f} [{lo:+.3f}, {hi:+.3f}] | {boot_p(bt):.3f} | {pa:.3f} |")
    others = [a for a in arms if a != "prefix"]
    lines += ["", "**T2 score by template** (arm minus prefix, paired by block; cluster bootstrap over the "
              "template's cells):", "", "| template | cells | blocks | prefix mean | " +
              " | ".join(others) + " |", "|---|---|---|---|" + "---|" * len(others)]
    for tpl in sorted({r["template"] for r in rs if r.get("template")}):
        sub = [r for r in rs if r.get("template") == tpl]
        base = [r for r in sub if r["arm"] == "prefix" and r.get("score") is not None]
        cols = []
        for arm in others:
            pairs = paired_blocks(sub, "prefix", arm, "score")
            cols.append(_ci(pairs) if pairs else "—")
        lines.append(f"| {tpl} | {len({r['cell'] for r in sub})} | {len(base)} | "
                     f"{statistics.mean(float(r['score']) for r in base):.3f} | " + " | ".join(cols) + " |")
    lines += ["", "**T3b loops and length** (paired by block; cluster bootstrap over cells; the comparison with "
              "shift, on its 24 blocks, is exploratory):", "",
              "| arm | runs ending at the budget (150 lead calls or 32K output tokens) | budget: arm - prefix "
              "[95% CI] | budget: arm - shift [95% CI] | "
              "lead calls (mean) | lead calls: arm - prefix [95% CI] |", "|---|---|---|---|---|---|"]
    for r in rs:
        r["_cap"] = int(r["status"] == "budget")
        r["_lead"] = int(r["lead"] or 0)
    for arm in arms:
        ar = [r for r in rs if r["arm"] == arm]
        cap = paired_blocks(rs, "prefix", arm, "_cap") if arm != "prefix" else []
        cap_s = paired_blocks(rs, "shift", arm, "_cap") if arm != "shift" else []
        lead = paired_blocks(rs, "prefix", arm, "_lead") if arm != "prefix" else []
        lines.append(f"| {arm} | {sum(r['_cap'] for r in ar)} / {len(ar)} | {_ci(cap) if cap else '—'} | "
                     f"{_ci(cap_s) if cap_s else '—'} | {statistics.mean(r['_lead'] for r in ar):.1f} | "
                     f"{_ci(lead) if lead else '—'} |")
    return lines


# -- per-cell profile for the main sweep's cell selection -------------------------------------------------

def cell_profile(rows: list[dict]) -> dict:
    """Per R1 cell, from its prefix-type runs (prefix, prefix_aa): outcome and cost, for epic_workloads select."""
    by = defaultdict(list)
    for r in rows:
        if r.get("regime") == "R1" and r.get("arm") in ("prefix", "prefix_aa") and r.get("score") is not None:
            by[r["cell"]].append(r)
    out = {}
    for cell, rs in sorted(by.items()):
        out[cell] = {"template": rs[0].get("template"), "codebase": rs[0].get("codebase"), "n": len(rs),
                     "score_mean": statistics.mean(float(r["score"]) for r in rs),
                     "pass_mean": statistics.mean(int(bool(r["pass"])) for r in rs),
                     "cap_rate": statistics.mean(int(r["status"] == "budget") for r in rs),
                     "lead_med": statistics.median(int(r["lead"] or 0) for r in rs),
                     "edit_calls_med": statistics.median(int(r["n_edit_calls"]) for r in rs),
                     "wall_mean": statistics.mean(float(r["wall_s"] or 0) for r in rs),
                     "out_tokens_med": statistics.median(int(r["out_tokens"]) for r in rs)}
    return out


def cmd_profile(args) -> None:
    rows = []
    for live in args.live:
        path = live_dir(live) / "runs_metrics.jsonl"
        rows += [json.loads(l) for l in path.read_text().splitlines()]
    prof = cell_profile(rows)
    out = Path(args.out) if args.out else ec.DATA / "cell_profile.json"
    out.write_text(json.dumps(prof, indent=1), encoding="utf-8")
    lines = ["| cell | n | score | pass | cap | lead (median) | edit calls (median) | wall s (mean) |",
             "|---|---|---|---|---|---|---|---|"]
    for cell, v in sorted(prof.items(), key=lambda kv: (kv[1]["template"], kv[0])):
        lines.append(f"| {cell} | {v['n']} | {v['score_mean']:.2f} | {v['pass_mean']:.2f} | {v['cap_rate']:.2f} | "
                     f"{v['lead_med']:.0f} | {v['edit_calls_med']:.0f} | {v['wall_mean']:.0f} |")
    print("\n".join(lines))
    print(f"{len(prof)} cells -> {out}")


# -- L1: KV deviation from the shadow probes ---------------------------------------------------------------

L1_CLASSES = ("exact", "reused", "head", "computed")
L1_AGE = ("0", "1", "2-3", "4-7", "8-15", "16+")
L1_OFF = ("0-31", "32-63", "64-127", "128-511", "512+")


def l1_accumulate(calls: list[dict]) -> dict:
    """Sum the shadow probes' binned deviation sums per arm: {arm: {(cls, age, off): [[dK, K, dV, V, n] per
    layer group]}}. Bin b of the middleware = (cls * 6 + age) * 5 + off."""
    acc: dict = defaultdict(lambda: defaultdict(lambda: None))
    for rec in calls:
        sh = rec.get("shadow") or {}
        if not sh.get("l1"):
            continue
        for b, groups in sh["l1"].items():
            b = int(b)
            key = (b // 30, (b % 30) // 5, b % 5)
            cur = acc[rec["arm"]][key]
            if cur is None:
                acc[rec["arm"]][key] = [list(g) for g in groups]
            else:
                for g, row in zip(cur, groups):
                    for j in range(5):
                        g[j] += row[j]
    return acc


def l1_dev(acc_arm: dict, pick, layers: slice = slice(None)) -> tuple[float, float, int]:
    """Relative deviation D^K, D^V over the bins `pick(cls, age, off)` selects and the layer groups `layers`."""
    dk = k = dv = v = 0.0
    n = 0
    for key, groups in acc_arm.items():
        if not pick(*key):
            continue
        for g in groups[layers]:
            dk, k, dv, v = dk + g[0], k + g[1], dv + g[2], v + g[3]
        n += int(groups[0][4])
    return (dk / k if k else float("nan"), dv / v if v else float("nan"), n)


def l1_tables(calls: list[dict], arms: list[str]) -> list[str]:
    acc = l1_accumulate(calls)
    arms = [a for a in arms if a in acc]
    if not arms:
        return []
    lines = ["**T6 KV deviation from a fresh recompute** (shadow probes: D = sum |K - K*|^2 / sum |K*|^2 over "
             "the rows of a class, all 64 layers; V likewise)", "",
             "| arm | shadows | " + " | ".join(f"{c} D^K / D^V (rows)" for c in L1_CLASSES) + " |",
             "|---|---|" + "---|" * len(L1_CLASSES)]
    n_sh = Counter(r["arm"] for r in calls if (r.get("shadow") or {}).get("l1"))
    for arm in arms:
        cells = []
        for ci in range(len(L1_CLASSES)):
            dk, dv, n = l1_dev(acc[arm], lambda c, a, o, ci=ci: c == ci)
            cells.append(f"{dk:.2e} / {dv:.2e} ({n:,})" if n else "—")
        lines.append(f"| {arm} | {n_sh[arm]} | " + " | ".join(cells) + " |")
    lines += ["", "Reused rows by **edits survived** (age) and by **offset from the run head**:", "",
              "| arm | " + " | ".join(f"age {a}" for a in L1_AGE) + " | " + " | ".join(f"off {o}" for o in L1_OFF) + " |",
              "|---|" + "---|" * (len(L1_AGE) + len(L1_OFF))]
    for arm in arms:
        ages = [l1_dev(acc[arm], lambda c, a, o, ai=ai: c == 1 and a == ai) for ai in range(len(L1_AGE))]
        offs = [l1_dev(acc[arm], lambda c, a, o, oi=oi: c == 1 and o == oi) for oi in range(len(L1_OFF))]
        lines.append(f"| {arm} | " + " | ".join(f"{d[0]:.2e}" if d[2] else "—" for d in ages + offs) + " |")
    lines += ["", "Reused rows by **layer** (groups of 4):", "",
              "| arm | " + " | ".join(f"L{4 * g}-{4 * g + 3}" for g in range(0, 16, 3)) + " |",
              "|---|" + "---|" * len(range(0, 16, 3))]
    for arm in arms:
        vals = [l1_dev(acc[arm], lambda c, a, o: c == 1, slice(g, g + 1)) for g in range(0, 16, 3)]
        lines.append(f"| {arm} | " + " | ".join(f"{d[0]:.2e}" if d[2] else "—" for d in vals) + " |")
    return lines + [""]


def cmd_l1(args) -> None:
    live = live_dir(args.live)
    calls = [r for path in ec.server_logs(live) for r in open_jsonl(path) if r.get("ev") == "call"]
    lines = l1_tables(calls, ARMS_ORDER)
    (live / "l1_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# -- hand audit (G7) --------------------------------------------------------------------------------------

def cmd_audit(args) -> None:
    """A readable sheet for the hand audit: per sampled graded run, the task, the final answer, a diff
    summary (MODIFY) and the grader's verdict; the auditor fills `human` blind to the arm (the arm is
    written only at the end of the file)."""
    live = live_dir(args.live)
    graded = [json.loads(l) for l in (live / "graded.jsonl").read_text().splitlines()]
    graded = [g for g in graded if g.get("score") is not None]
    rng = random.Random(args.seed)
    by_t = defaultdict(list)
    for g in graded:
        by_t[g["template"]].append(g)
    picks = []
    while len(picks) < args.n and any(by_t.values()):
        for t in sorted(by_t):
            if by_t[t] and len(picks) < args.n:
                picks.append(by_t[t].pop(rng.randrange(len(by_t[t]))))
    man = {r["run_label"]: r for r in manifest_of(live)}
    out = ["# Grader audit (blind to arm)", ""]
    key = []
    for i, g in enumerate(picks, 1):
        label = g["label"]
        row = man.get(label, {})
        d = live / "runs" / label
        ans = json.loads((d / f"{label}.answer.json").read_text()).get("answer", "") if (d / f"{label}.answer.json").exists() else ""
        diff = (d / f"{label}.diff").read_text() if (d / f"{label}.diff").exists() else ""
        files = sorted({ln[4:].split("\t")[0] for ln in diff.splitlines() if ln.startswith("+++ ")})
        plus = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        minus = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        det = {k: v for k, v in (g.get("details") or {}).items() if not isinstance(v, (list, dict))}
        out += [f"## {i}. {row.get('cell')} ({g['template']})", "",
                f"**Task:** {row.get('prompt', '')[:600]}", "",
                f"**Status:** {g.get('status')} · **grader:** score {g['score']}, pass {g['pass']} · `{json.dumps(det)[:500]}`", ""]
        if diff:
            out += [f"**Diff:** {len(files)} files, +{plus} / -{minus} lines: {', '.join(files[:12])}", ""]
            out += ["```diff", "\n".join(diff.splitlines()[:60]), "```", ""]
        out += ["**Final answer:**", "", "```", (ans or "")[:2500], "```", "", "human: ____  note: ____", ""]
        key.append(f"{i}. {label}")
    out += ["---", "", "Arm key (read after judging):", "", *key]
    path = live / "audit_view.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{len(picks)} runs -> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("runs")
    r.add_argument("--live", required=True)
    r.add_argument("--no-grade", action="store_true")
    p = sub.add_parser("pilot")
    p.add_argument("--live", required=True)
    m = sub.add_parser("main")
    m.add_argument("--live", required=True)
    m.add_argument("--margin", type=float, default=0.08)
    pr = sub.add_parser("profile")
    pr.add_argument("--live", action="append", required=True)
    pr.add_argument("--out", default=None)
    l1 = sub.add_parser("l1")
    l1.add_argument("--live", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--live", required=True)
    a.add_argument("--n", type=int, default=30)
    a.add_argument("--seed", default="epic16-audit")
    args = ap.parse_args()
    {"runs": cmd_runs, "pilot": cmd_pilot, "main": cmd_main, "l1": cmd_l1, "profile": cmd_profile,
     "audit": cmd_audit}[args.cmd](args)


if __name__ == "__main__":
    main()
