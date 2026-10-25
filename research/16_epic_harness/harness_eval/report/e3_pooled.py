#!/usr/bin/env python3
"""Descriptive pooling across the EPIC rounds (runs/epic/prereg_e3.md: exploratory, no verdicts): arm − control on
MiniMax-M2.7 in e1 (SWE-bench Verified, 30 tasks × 3), e2 (SWE-bench Pro, 30 × 1) and e3 (SWE-bench Verified, 100
new tasks × 3), each as epic_report.paired computes it; pooled by inverse variance, with 90 % normal intervals.

    .venv/bin/python report/e3_pooled.py [--rounds e1:swe,e2:swepro,e3:swe] [--out report/e3_pooled.md]

Terminal-Bench (e2) is left out: its A/A difference of 0.30 left it without an accuracy conclusion.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
import collect  # noqa: E402
import epic_report as er  # noqa: E402

SERVER = "minimax-m2.7-epic"
Z90 = 1.645


def round_stats(rows: list[dict], phase: str, bench: str, arm: str) -> dict:
    trials = er.first_valid([r for r in rows if r["bench"] == bench and er.block_of(r["phase"]).startswith(phase + "r")])
    return er.paired(trials, arm, ["prefix", "prefix_aa"])


def pool(parts: list[dict]) -> dict | None:
    parts = [p for p in parts if p.get("se")]
    if not parts:
        return None
    w = [1 / p["se"] ** 2 for p in parts]
    est = sum(wi * p["diff"] for wi, p in zip(w, parts)) / sum(w)
    se = math.sqrt(1 / sum(w))
    return {"diff": est, "lo": est - Z90 * se, "hi": est + Z90 * se, "se": se}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default="e1:swe,e2:swepro,e3:swe")
    ap.add_argument("--arms", default="epic128,epic128a1,epic128a4,shift")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    rounds = [tuple(x.split(":")) for x in a.rounds.split(",")]
    rows = [r for r in collect.load_trials() if r["model"].startswith(SERVER + "+")]
    lines = ["### Pooled with e1 and e2 (descriptive; inverse-variance weights, 90 % normal intervals)", "",
             "| arm | " + " | ".join(f"{ph} {b}" for ph, b in rounds) + " | pooled |",
             "|---|" + "---|" * (len(rounds) + 1)]
    for arm in a.arms.split(","):
        parts = [round_stats(rows, ph, b, arm) for ph, b in rounds]
        if not any(p.get("se") for p in parts):
            continue
        cells = [f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}] ({p['tasks']} tasks)" if p.get("se") else "—"
                 for p in parts]
        pooled = pool(parts)
        lines.append(f"| {arm} | " + " | ".join(cells) +
                     f" | **{pooled['diff']:+.3f}** [{pooled['lo']:+.3f}, {pooled['hi']:+.3f}] |")
    lines += ["", "Per round: the round's own t-interval over task means (90 %). The pooled interval weights each round "
              "by the inverse of its squared standard error.", ""]
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
