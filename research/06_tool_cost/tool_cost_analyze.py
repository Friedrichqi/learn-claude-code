#!/usr/bin/env python3
"""Tables and tests for the advertised-tool-cost experiment (stage 1 probes, stage 2 loop runs).

    python3 research/06_tool_cost/tool_cost_analyze.py \
        --stage1 research/06_tool_cost/data/tool_cost/stage1.jsonl \
        --stage2 research/06_tool_cost/data/tool_cost/stage2.jsonl \
        --md research/06_tool_cost/data/tool_cost/tool_cost_tables.md \
        --json research/06_tool_cost/data/tool_cost/tool_cost_tables.json

No numpy/scipy on this host, so the Wilson interval, Fisher's exact test and the two-parameter
logistic fit (Newton-Raphson) are implemented here directly.

Probe A is reported three ways on purpose.  "cheap" is the quantity of interest, but a model that
simply always names the first tool in the array, or always prefers the name `read_via_alpha`, would
score 50% cheap only because the design counterbalances those two nuisances; reporting P(alpha) and
P(first-listed) alongside shows which of the three the model is actually tracking.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


# ---------------------------------------------------------------- statistics

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a,b],[c,d]] (a,b = successes/failures in group 1)."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, col1 = a + b, a + c

    def prob(x: int) -> float:
        return (math.comb(row1, x) * math.comb(n - row1, col1 - x)) / math.comb(n, col1)

    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    observed = prob(a)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed * (1 + 1e-9)))


def logistic_fit(xs: list[float], ys: list[int], iters: int = 60) -> tuple[float, float] | None:
    """P(y=1) = sigmoid(b0 + b1*x) by Newton-Raphson with a small ridge for separation."""
    b0 = b1 = 0.0
    ridge = 1e-6
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            eta = b0 + b1 * x
            p = 1 / (1 + math.exp(-max(-30, min(30, eta))))
            w = max(p * (1 - p), 1e-9)
            r = y - p
            g0 += r
            g1 += r * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        h00 += ridge
        h11 += ridge
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            return None
        d0 = (h11 * g0 - h01 * g1) / det
        d1 = (h00 * g1 - h01 * g0) / det
        b0 += d0
        b1 += d1
        if abs(d0) + abs(d1) < 1e-9:
            break
    return b0, b1


def pct(k: int, n: int) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.0f}% [{100 * lo:.0f}-{100 * hi:.0f}] ({k}/{n})"


def load(path: str | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- stage 1

def stage1_tables(rows: list[dict], out: list[str], blob: dict):
    ok = [r for r in rows if not r.get("error")]
    errors = len(rows) - len(ok)
    out.append(f"\n## Stage 1 -- single-shot tool choice ({len(ok)} trials"
               + (f", {errors} errored" if errors else "") + ")\n")

    # ---- probe A
    ab = [r for r in ok if r["probe"] == "A"]
    out.append("### A. Two interchangeable read channels, expensive one advertised at 5.00 s\n")
    out.append("`cheap` = called the channel with the lower advertised cost. `alpha` and "
               "`first-listed` are the two nuisance variables the design counterbalances.\n")
    out.append("| framing | placement | chose cheap | chose alpha | chose first-listed | "
               "no tool | cost mentioned |")
    out.append("|---|---|---|---|---|---|---|")
    baseline = None
    cellsA = {}
    for framing in ("none", "seconds", "hardware", "qualitative"):
        for placement in ("tool_desc", "system_prompt", "both"):
            cell = [r for r in ab if r["framing"] == framing and r["placement"] == placement]
            if not cell:
                continue
            n = len(cell)
            cheap = sum(1 for r in cell if r.get("chose") == "fast")
            alpha = sum(1 for r in cell if r.get("first_tool") == "read_via_alpha")
            first = sum(1 for r in cell if r.get("first_tool") == (r.get("tool_order") or [None])[0])
            none_ = sum(1 for r in cell if not r.get("first_tool"))
            mention = sum(1 for r in cell if r.get("mentions_cost"))
            label = "(no cost shown)" if framing == "none" else placement
            out.append(f"| {framing} | {label} | {pct(cheap, n)} | {pct(alpha, n)} | "
                       f"{pct(first, n)} | {none_} | {pct(mention, n)} |")
            cellsA[(framing, placement)] = (cheap, n)
            if framing == "none":
                baseline = (cheap, n)
        if framing == "none":
            continue
    blob["probeA"] = {f"{k[0]}/{k[1]}": {"cheap": v[0], "n": v[1]} for k, v in cellsA.items()}

    if baseline:
        bk, bn = baseline
        out.append(f"\nAgainst the unannotated baseline ({pct(bk, bn)} cheap), Fisher exact "
                   "two-sided:\n")
        out.append("| framing | placement | cheap | p |")
        out.append("|---|---|---|---|")
        for (framing, placement), (k, n) in sorted(cellsA.items()):
            if framing == "none":
                continue
            p = fisher_exact(k, n - k, bk, bn - bk)
            out.append(f"| {framing} | {placement} | {pct(k, n)} | "
                       + (f"{p:.2e}" if p < 1e-3 else f"{p:.3f}") + " |")

    # ---- probe B
    bb = [r for r in ok if r["probe"] == "B"]
    if bb:
        out.append("\n### B. Dose-response: cheap channel pinned at 0.04 s, expensive one swept\n")
        out.append("| advertised cost of the expensive channel | ratio | chose cheap | cost mentioned |")
        out.append("|---|---|---|---|")
        xs, ys = [], []
        dose = {}
        for slow in sorted({r["slow_seconds"] for r in bb}):
            cell = [r for r in bb if r["slow_seconds"] == slow]
            n = len(cell)
            cheap = sum(1 for r in cell if r.get("chose") == "fast")
            mention = sum(1 for r in cell if r.get("mentions_cost"))
            ratio = slow / 0.04
            out.append(f"| {slow:g} s | {ratio:.0f}x | {pct(cheap, n)} | {pct(mention, n)} |")
            dose[str(slow)] = {"cheap": cheap, "n": n}
            for r in cell:
                xs.append(math.log10(max(slow, 1e-3) / 0.04))
                ys.append(1 if r.get("chose") == "fast" else 0)
        blob["probeB"] = dose
        # With a saturating response the data are quasi-separated (every dose above the tie is
        # 100% cheap), the likelihood has no interior maximum and the slope runs away; reporting a
        # fitted "threshold" from such a fit would be an artefact, so the separation is reported
        # instead and the threshold is bracketed by the two doses that straddle it.
        nonzero = sorted({x for x in xs if x > 0})
        separated = all(y == 1 for x, y in zip(xs, ys) if x > 0)
        fit = logistic_fit(xs, ys)
        if separated and nonzero:
            lowest = 0.04 * 10 ** min(nonzero)
            out.append(f"\nEvery dose above the tie is at 100%: the response is already saturated "
                       f"at the smallest cost difference tested ({lowest:.2g} s against 0.04 s, "
                       f"{10 ** min(nonzero):.0f}x). The data are completely separated, so no "
                       f"logistic threshold is estimable; the switch point lies at or below a "
                       f"{10 ** min(nonzero):.0f}x ratio.")
            blob["probeB_fit"] = {"separated": True,
                                  "smallest_ratio_tested": 10 ** min(nonzero)}
        elif fit:
            b0, b1 = fit
            out.append(f"\nLogistic fit on log10(cost ratio): P(cheap) = sigmoid({b0:.2f} "
                       f"{'+' if b1 >= 0 else '-'} {abs(b1):.2f} * log10 ratio).")
            if b1 > 1e-6:
                for target in (0.5, 0.9):
                    x = (math.log(target / (1 - target)) - b0) / b1
                    out.append(f"  P = {target:.0%} at a cost ratio of {10 ** x:.1f}x "
                               f"({0.04 * 10 ** x:.3g} s against 0.04 s).")
            blob["probeB_fit"] = {"b0": b0, "b1": b1, "separated": False}

    # ---- probe C
    cc = [r for r in ok if r["probe"] == "C"]
    if cc:
        out.append("\n### C. Realistic pool, one real question, first move\n")
        tools = ["read_file", "grep", "glob", "bash", "recall_summary"]
        out.append("| annotated tool | its advertised cost | framing | "
                   + " | ".join(f"first={t}" for t in tools) + " | no tool | n |")
        out.append("|---|---|---|" + "---|" * (len(tools) + 2))
        cells = {}
        for target, slow, framing in [(None, 0.0, "none"), ("read_file", 0.05, "seconds"),
                                      ("read_file", 5.0, "seconds"), ("read_file", 60.0, "seconds"),
                                      ("read_file", 5.0, "hardware"),
                                      ("read_file", 5.0, "qualitative"), ("grep", 5.0, "seconds")]:
            cell = [r for r in cc if r.get("target") == target
                    and r["slow_seconds"] == slow and r["framing"] == framing]
            if not cell:
                continue
            n = len(cell)
            counts = Counter(r.get("first_tool") for r in cell)
            row = [f"{100 * counts.get(t, 0) / n:.0f}%" for t in tools]
            label = "(none)" if target is None else target
            out.append(f"| {label} | {slow:g} s | {framing} | " + " | ".join(row)
                       + f" | {counts.get(None, 0)} | {n} |")
            cells[(target, slow, framing)] = (counts.get("read_file", 0), n)
        blob["probeC"] = {f"{k[0]}/{k[1]}/{k[2]}": {"read_file_first": v[0], "n": v[1]}
                          for k, v in cells.items()}
        base = cells.get((None, 0.0, "none"))
        if base:
            bk, bn = base
            out.append(f"\nP(first move is read_file) against the unannotated baseline "
                       f"{pct(bk, bn)}:\n")
            out.append("| annotated tool | cost | framing | read_file first | p |")
            out.append("|---|---|---|---|---|")
            for (target, slow, framing), (k, n) in cells.items():
                if target is None:
                    continue
                p = fisher_exact(k, n - k, bk, bn - bk)
                out.append(f"| {target} | {slow:g} s | {framing} | {pct(k, n)} | "
                           + (f"{p:.2e}" if p < 1e-3 else f"{p:.3f}") + " |")

    probe_d(ok, out, blob)


def probe_d(rows: list[dict], out: list[str], blob: dict):
    dd = [r for r in rows if r["probe"] == "D"]
    if not dd:
        return
    out.append("\n### D. Same realistic pool: is the null in C missing information or a missing "
               "objective?\n")
    tools = ["read_file", "grep", "glob", "bash", "recall_summary"]
    out.append("| cost table | policy line | hard budget | "
               + " | ".join(f"first={t}" for t in tools)
               + " | cost mentioned | n |")
    out.append("|---|---|---|" + "---|" * (len(tools) + 2))
    cells = {}
    order = [(None, False, False), ("read_file", False, False), (None, True, False),
             ("read_file", True, False), ("read_file", True, True)]
    for target, policy, budget in order:
        cell = [r for r in dd if r.get("target") == target
                and bool(r.get("policy")) == policy and bool(r.get("budget")) == budget]
        if not cell:
            continue
        n = len(cell)
        counts = Counter(r.get("first_tool") for r in cell)
        row = [f"{100 * counts.get(t, 0) / n:.0f}%" for t in tools]
        mention = sum(1 for r in cell if r.get("mentions_cost"))
        out.append(f"| {'yes' if target else 'no'} | {'yes' if policy else 'no'} | "
                   f"{'yes' if budget else 'no'} | " + " | ".join(row)
                   + f" | {100 * mention / n:.0f}% | {n} |")
        cells[(bool(target), policy, budget)] = (counts.get("read_file", 0), n, mention)
    blob["probeD"] = {f"table={k[0]},policy={k[1]},budget={k[2]}":
                      {"read_file_first": v[0], "n": v[1], "mentions_cost": v[2]}
                      for k, v in cells.items()}
    base = cells.get((False, False, False))
    if base:
        bk, bn, _ = base
        out.append(f"\nP(first move is read_file) against the cell with nothing said about cost "
                   f"{pct(bk, bn)}:\n")
        out.append("| cost table | policy | budget | read_file first | p |")
        out.append("|---|---|---|---|---|")
        for (table, policy, budget), (k, n, _) in cells.items():
            if (table, policy, budget) == (False, False, False):
                continue
            p_value = fisher_exact(k, n - k, bk, bn - bk)
            out.append(f"| {'yes' if table else 'no'} | {'yes' if policy else 'no'} | "
                       f"{'yes' if budget else 'no'} | {pct(k, n)} | "
                       + (f"{p_value:.2e}" if p_value < 1e-3 else f"{p_value:.3f}") + " |")


# ---------------------------------------------------------------- stage 2

def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else float("nan")


def stage2_tables(rows: list[dict], out: list[str], blob: dict):
    ok = [r for r in rows if not r.get("error")]
    if not ok:
        return
    errors = len(rows) - len(ok)
    out.append(f"\n## Stage 2 -- multi-round loop, tools really execute ({len(ok)} runs"
               + (f", {errors} errored" if errors else "") + ")\n")
    arms = ["plain", "advertise", "enforce", "both"]
    tasks = sorted({r["task"] for r in ok})

    out.append("| arm | advertised | enforced | runs | rounds | read_file calls | grep calls | "
               "recall calls | result KB | advertised bill s | real tool s | wall s | correct |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    summary = {}
    for arm in arms:
        cell = [r for r in ok if r["arm"] == arm]
        if not cell:
            continue
        n = len(cell)
        correct = sum(1 for r in cell if r.get("correct"))
        row = {
            "n": n,
            "rounds": mean([r["rounds"] for r in cell]),
            "read": mean([r["n_read_file"] for r in cell]),
            "grep": mean([r["n_grep"] for r in cell]),
            "recall": mean([r["n_recall"] for r in cell]),
            "kb": mean([r["result_bytes"] / 1024 for r in cell]),
            "bill": mean([r["advertised_bill_s"] for r in cell]),
            "real": mean([r["real_tool_s"] for r in cell]),
            "wall": mean([r["wall_s"] for r in cell]),
            "correct": correct,
        }
        summary[arm] = row
        adv = "yes" if arm in ("advertise", "both") else "no"
        enf = "yes" if arm in ("enforce", "both") else "no"
        out.append(f"| {arm} | {adv} | {enf} | {n} | {row['rounds']:.1f} | {row['read']:.2f} | "
                   f"{row['grep']:.2f} | {row['recall']:.2f} | {row['kb']:.1f} | "
                   f"{row['bill']:.2f} | {row['real']:.1f} | {row['wall']:.1f} | "
                   f"{pct(correct, n)} |")
    blob["stage2"] = summary

    out.append("\nPer task, share of runs that called read_file at all:\n")
    out.append("| task | " + " | ".join(arms) + " |")
    out.append("|---|" + "---|" * len(arms))
    for task in tasks:
        cells = []
        for arm in arms:
            cell = [r for r in ok if r["arm"] == arm and r["task"] == task]
            k = sum(1 for r in cell if r.get("used_read_file"))
            cells.append(pct(k, len(cell)) if cell else "-")
        out.append(f"| {task} | " + " | ".join(cells) + " |")

    out.append("\nPer task, correct answers:\n")
    out.append("| task | " + " | ".join(arms) + " |")
    out.append("|---|" + "---|" * len(arms))
    for task in tasks:
        cells = []
        for arm in arms:
            cell = [r for r in ok if r["arm"] == arm and r["task"] == task]
            k = sum(1 for r in cell if r.get("correct"))
            cells.append(pct(k, len(cell)) if cell else "-")
        out.append(f"| {task} | " + " | ".join(cells) + " |")

    def contrast(a: str, b: str, label: str):
        ca = [r for r in ok if r["arm"] == a]
        cb = [r for r in ok if r["arm"] == b]
        if not ca or not cb:
            return
        ka = sum(1 for r in ca if r.get("used_read_file"))
        kb = sum(1 for r in cb if r.get("used_read_file"))
        p = fisher_exact(ka, len(ca) - ka, kb, len(cb) - kb)
        out.append(f"| {label} | {pct(ka, len(ca))} | {pct(kb, len(cb))} | "
                   + (f"{p:.2e}" if p < 1e-3 else f"{p:.3f}")
                   + f" | {mean([r['wall_s'] for r in ca]):.1f} -> "
                   f"{mean([r['wall_s'] for r in cb]):.1f} s |")

    out.append("\nContrasts (read_file used, and mean wall):\n")
    out.append("| contrast | left | right | Fisher p | wall |")
    out.append("|---|---|---|---|---|")
    contrast("plain", "advertise", "advertising only (no real cost)")
    contrast("enforce", "both", "advertising when the cost is real")
    contrast("plain", "enforce", "real cost, agent not told")
    contrast("plain", "both", "real cost, agent told")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage1", default=None)
    parser.add_argument("--stage2", default=None)
    parser.add_argument("--md", default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    out: list[str] = ["# Advertised tool cost -- measured tables",
                      "",
                      "Generated by `research/06_tool_cost/tool_cost_analyze.py`. Percentages carry 95% Wilson "
                      "intervals; `p` is a two-sided Fisher exact test."]
    blob: dict = {}
    s1 = load(args.stage1)
    if s1:
        stage1_tables(s1, out, blob)
    s2 = load(args.stage2)
    if s2:
        stage2_tables(s2, out, blob)

    text = "\n".join(out) + "\n"
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(text, encoding="utf-8")
        print(f"wrote {args.md}")
    else:
        print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(blob, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
