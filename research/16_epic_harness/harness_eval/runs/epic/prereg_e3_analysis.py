#!/usr/bin/env python3
"""The pre-registered analysis of e3 (runs/epic/prereg_e3.md): MiniMax-M2.7, 100 new SWE-bench Verified tasks x 3
replicates, arms prefix / prefix_aa / epic128 / epic128a1 / epic128a4.

    .venv/bin/python report/e3_analysis.py [--tasks run/state/e3_tasks.json]

Unit and statistic (as pre-registered):
- A block is one (replicate, task). Each arm contributes its first valid trial per block, by start time; reruns of
  infrastructure failures belong to their block. Control = mean of prefix and prefix_aa in the block.
- d = arm - control per block; per task the mean of its blocks' d; the estimate is the mean over tasks, with a
  t-interval over the task means (df = tasks - 1; one-sided 95 % bounds = 90 % two-sided interval).
- H1 (primary): epic128 is non-inferior at margin 0.05: the one-sided 95 % lower bound > -0.05.
- H2 (key secondary): epic128a1 is worse than the control: the one-sided 95 % upper bound < 0.
- Checks: the A/A difference (prefix_aa - prefix) should have an interval covering 0; the validity gates per
  replicate come from epic/pilot_gates.py (runs/epic/gates_minimax-m2.7-epic_e3r<k>.json).
Everything after the two hypotheses is exploratory and carries no verdict.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path("/data/fritz/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
import collect  # noqa: E402
import epic_report as er  # noqa: E402

SERVER = "minimax-m2.7-epic"
ARMS = ["prefix", "prefix_aa", "epic128", "epic128a1", "epic128a4"]
MARGIN = 0.05


def diff_stats(trials: dict, arm: str, ref: list[str], tasks: set[str], blocks_ok=None) -> dict:
    """epic_report.paired over `tasks`, restricted to the blocks in `blocks_ok` when given."""
    skip = None
    if blocks_ok is not None:
        skip = {(b, t) for (_a, b, t) in trials} - blocks_ok
    return er.paired(trials, arm, ref, tasks, skip_blocks=skip)


def fmt(r: dict) -> str:
    if r["diff"] is None:
        return f"| {r['tasks']} | {r['blocks']} | — | — |"
    return f"| {r['tasks']} | {r['blocks']} | {r['diff']:+.3f} | [{r['lo']:+.3f}, {r['hi']:+.3f}] |"


def verdict_h1(r: dict) -> str:
    if r["diff"] is None:
        return "no data"
    if r["lo"] > -MARGIN:
        return "**non-inferior** (lower bound above −0.05)"
    if r["hi"] < -MARGIN:
        return "**inferior beyond the margin** (the whole interval below −0.05)"
    return "not shown (the interval reaches below −0.05)"


def verdict_h2(r: dict) -> str:
    if r["diff"] is None:
        return "no data"
    return "**worse than the control** (upper bound below 0)" if r["hi"] < 0 else "not shown (the interval reaches 0)"


def aa_check(r: dict) -> str:
    if r["diff"] is None:
        return "no data"
    return "covers 0" if r["lo"] <= 0 <= r["hi"] else "**does not cover 0: flagged**"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=str(ROOT / "run" / "state" / "e3_tasks.json"))
    a = ap.parse_args()
    spec = json.loads(Path(a.tasks).read_text())
    tasks = set(spec["tasks"])
    stratum = {t: label for label, s in spec["strata"].items() for t in s["tasks"]}
    rows = [r for r in collect.load_trials() if r["model"].startswith(SERVER + "+") and r["bench"] == "swe"
            and er.block_of(r["phase"]).startswith("e3r") and r["task"] in tasks]
    trials = er.first_valid(rows)
    ctl = ["prefix", "prefix_aa"]
    h1 = diff_stats(trials, "epic128", ctl, tasks)
    h2 = diff_stats(trials, "epic128a1", ctl, tasks)
    aa = diff_stats(trials, "prefix_aa", ["prefix"], tasks)
    lines = ["## e3: pre-registered test of EPIC's accuracy cost (MiniMax-M2.7, SWE-bench Verified, new tasks)", "",
             f"Pre-registration: `runs/epic/prereg_e3.md` (sha256 in `runs/epic/prereg_e3.sha256`). Tasks: "
             f"{len(tasks)} new SWE-bench Verified tasks (seed {spec['seed']}; strata "
             + ", ".join(f"{k} {len(v['tasks'])}" for k, v in spec["strata"].items())
             + f"; {len(spec.get('replacements') or [])} replaced after the oracle check). Arms "
             + ", ".join(ARMS) + "; control = mean of prefix and prefix_aa per block. Differences are in success "
             "rate (0.05 = 5 points); intervals are 90 % two-sided t-intervals over task means, i.e. one-sided 95 % "
             "bounds.", "",
             "### Hypotheses", "",
             "| test | tasks | blocks | difference | 90% [lo, hi] | result |", "|---|---|---|---|---|---|"]
    lines.append(f"| H1 (primary): epic128 − control, non-inferior at margin 0.05 {fmt(h1)} {verdict_h1(h1)} |")
    lines.append(f"| H2: epic128a1 − control, worse than the control {fmt(h2)} {verdict_h2(h2)} |")
    lines.append(f"| check: prefix_aa − prefix (A/A) {fmt(aa)} {aa_check(aa)} |")
    lines.append("")
    # gates
    lines += ["### Validity gates per replicate (epic/pilot_gates.py)", "",
              "| replicate | trials | E-0 | preemptions | fallbacks+resets / post-snip | records | ranks | sessions left |",
              "|---|---|---|---|---|---|---|---|"]
    for k in range(3):
        f = ROOT / "runs" / "epic" / f"gates_{SERVER}_e3r{k}.json"
        if not f.is_file():
            lines.append(f"| e3r{k} | — | — | — | — | — | — | — |")
            continue
        g = json.loads(f.read_text())
        gg, p = g["gates"], g["pass"]
        ok = lambda b: "pass" if b else "**fail**"  # noqa: E731
        lines.append(f"| e3r{k} | {g['cost']['trials']} | {g['e0']['share_ge_99'] or 0:.1%} {ok(p['E0_append_only'])} | "
                     f"{gg['preemptions']} {ok(p['no_preemptions'])} | {gg['fallback_reset_share']:.2%} "
                     f"{ok(p['fallback_reset_lt_5pct'])} | {ok(p['one_record_per_call'])} | {ok(p['ranks_consistent'])} | "
                     f"{ok(p['no_sessions_left'])} |")
    lines.append("")
    # exploratory
    lines += ["### Exploratory (no verdicts)", "",
              "| comparison | tasks | blocks | difference | 90% [lo, hi] |", "|---|---|---|---|---|"]
    lines.append(f"| epic128a4 − control {fmt(diff_stats(trials, 'epic128a4', ctl, tasks))}")
    lines.append(f"| epic128a4 − epic128a1 {fmt(diff_stats(trials, 'epic128a4', ['epic128a1'], tasks))}")
    lines.append(f"| epic128 − epic128a1 {fmt(diff_stats(trials, 'epic128', ['epic128a1'], tasks))}")
    for label in spec["strata"]:
        sub = {t for t in tasks if stratum.get(t) == label}
        for arm in ("epic128", "epic128a1", "epic128a4"):
            lines.append(f"| {arm} − control, stratum “{label}” {fmt(diff_stats(trials, arm, ctl, sub))}")
    snipped_blocks = {(b, t) for (a2, b, t), r in trials.items() if a2 in ctl
                      and (r.get("compactions") or {}).get("snip_compact", 0) > 0}
    for arm in ("epic128", "epic128a1", "epic128a4"):
        lines.append(f"| {arm} − control, blocks whose control snipped {fmt(diff_stats(trials, arm, ctl, tasks, snipped_blocks))}")
    lines.append("")
    lines += ["| arm | trials | pass@1 | lead calls | at budget | agent timeout |", "|---|---|---|---|---|---|"]
    for arm in ARMS:
        rs = [r for (a2, _b, _t), r in trials.items() if a2 == arm]
        if rs:
            lines.append(f"| {arm} | {len(rs)} | {st.mean(collect.success(r) for r in rs):.3f} | "
                         f"{st.mean(r.get('lead_calls') or 0 for r in rs):.1f} | "
                         f"{st.mean(float(r.get('s15_status') == 'budget') for r in rs):.1%} | "
                         f"{sum(1 for r in rs if r.get('s15_status') == 'timeout')} |")
    lines.append("")
    text = "\n".join(lines) + "\n"
    (ROOT / "report" / f"e3_{SERVER}_swe.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
