#!/usr/bin/env python3
"""Exploratory breakdowns of a replay run of research/20_round_dependency (not pre-registered; rd_analyze.py holds
the pre-registered outcomes and is hashed in prereg.sha256, so these live here).

  - by tool transition: the tool class of round j-1 -> round j (read, edit, shell, todo, mixed), the share of
    determinate pairs independent of round j-1 and rho;
  - by position in the turn: the pair's index j over the turn's rounds, in quartiles;
  - the chain groups' length distribution (rounds per merged call) and the longest groups;
  - what the pending samples did instead, for dependent pairs (re-issue the pending call, another tool, no call);
  - CPU-heavy rounds (>= 1 s of tools): is the next round independent of their results?

    python research/20_round_dependency/rd_explore.py --run main
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_analyze as ra  # noqa: E402
import rd_rounds as rr  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    data = ra.load_run(a.run)
    S, tau = data["meta"]["S"], data["meta"]["tau"]
    items = data["items"]
    idx = {(i["sid"], i["turn"], i["j"]): i for i in items}
    lines = [f"# Exploratory breakdowns of `{a.run}` (rd_explore.py; not pre-registered)", ""]
    out = {}
    for src in ("t19", "t16", "all"):
        sub = [i for i in ra.pairs(items) if src == "all" or i["src"] == src]
        if not sub:
            continue
        # transitions
        trans = defaultdict(list)
        for i in sub:
            prev = idx.get((i["sid"], i["turn"], i["j"] - 1))
            if prev is not None:
                trans[(prev["cls"], i["cls"])].append(i)
        lines += [f"## {src}: by tool transition (round j-1 -> round j)", "",
                  "| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |", "|---|---|---|---|---|---|"]
        rows = []
        for (a_, b_), xs in sorted(trans.items(), key=lambda kv: -len(kv[1])):
            det = [x for x in xs if ra.label(x, S, tau) in ("independent", "dependent")]
            ind = sum(ra.label(x, S, tau) == "independent" for x in det)
            sp = sum(ra.p(x["P"]) for x in xs if ra.p(x["P"]) is not None and ra.p(x["C"]) is not None)
            sc = sum(ra.p(x["C"]) for x in xs if ra.p(x["P"]) is not None and ra.p(x["C"]) is not None)
            ch = [x for x in xs if x["chain"] is not None]
            mg = sum(not x["chain"]["start"] for x in ch)
            rows.append({"from": a_, "to": b_, "pairs": len(xs), "determinate": len(det),
                         "independent": ind / len(det) if det else None, "rho": sp / sc if sc else None,
                         "mergeable": mg / len(ch) if ch else None})
            if len(xs) >= 10:
                pct = lambda v: "—" if v is None else f"{v:.0%}"
                lines.append(f"| {a_} -> {b_} | {len(xs)} | {len(det)} | {pct(ind / len(det) if det else None)} | "
                             f"{'—' if not sc else f'{sp / sc:.2f}'} | {pct(mg / len(ch) if ch else None)} |")
        out.setdefault(src, {})["transitions"] = rows
        # position
        lines += ["", f"## {src}: by position in the turn (j over the turn's rounds)", "",
                  "| quartile | pairs | independent of j-1 | mergeable |", "|---|---|---|---|"]
        turns = defaultdict(int)
        for i in items:
            turns[(i["sid"], i["turn"])] += 1
        quart = defaultdict(list)
        for i in sub:
            n = turns[(i["sid"], i["turn"])]
            quart[min(3, int(4 * i["j"] / max(1, n)))].append(i)
        out[src]["position"] = {}
        for qn in range(4):
            xs = quart[qn]
            det = [x for x in xs if ra.label(x, S, tau) in ("independent", "dependent")]
            ind = sum(ra.label(x, S, tau) == "independent" for x in det)
            ch = [x for x in xs if x["chain"] is not None]
            mg = sum(not x["chain"]["start"] for x in ch)
            out[src]["position"][qn] = {"pairs": len(xs), "independent": ind / len(det) if det else None,
                                        "mergeable": mg / len(ch) if ch else None}
            lines.append(f"| Q{qn + 1} | {len(xs)} | {ind / len(det):.0%} | {mg / len(ch):.0%} |" if det and ch
                         else f"| Q{qn + 1} | {len(xs)} | — | — |")
        # dependent pairs: what the pending samples did
        dep = [i for i in sub if ra.label(i, S, tau) == "dependent"]
        oc = Counter()
        for i in dep:
            oc.update(i["P"].get("outcomes") or {})
        tot = sum(oc.values()) or 1
        out[src]["dependent_outcomes"] = {k: v / tot for k, v in oc.items()}
        lines += ["", f"{src}: when round j depends on round j-1, the pending samples "
                  + ", ".join(f"{k} {v / tot:.0%}" for k, v in oc.most_common()), ""]
    # chain group lengths
    lengths = Counter()
    by_src = defaultdict(Counter)
    for (sid, turn), chain in data["chains"].items():
        src = data["sessions"][sid]["src"]
        g = 0
        for c in sorted(chain, key=lambda c: c["j"]):
            if c["start"]:
                if g:
                    lengths[g] += 1
                    by_src[src][g] += 1
                g = 1
            else:
                g += 1
        if g:
            lengths[g] += 1
            by_src[src][g] += 1
    out["group_lengths"] = {s: dict(sorted(c.items())) for s, c in by_src.items()}
    lines += ["## Chain group lengths (rounds per merged call)", ""]
    for s, c in sorted(by_src.items()):
        tot = sum(c.values())
        lines.append(f"- {s}: " + ", ".join(f"{k}: {v} ({v / tot:.0%})" for k, v in sorted(c.items()) if k <= 6)
                     + (f", >6: {sum(v for k, v in c.items() if k > 6)}" if any(k > 6 for k in c) else "")
                     + f"; mean {sum(k * v for k, v in c.items()) / tot:.2f}")
    # CPU-heavy rounds
    heavy = [i for i in items if (i["tool_s"] or 0) >= 1.0 and not i["final"]]
    nxt = [idx.get((i["sid"], i["turn"], i["j"] + 1)) for i in heavy]
    pairs_h = [(h, n) for h, n in zip(heavy, nxt) if n is not None and not n["final"]]
    det_h = [(h, n) for h, n in pairs_h if ra.label(n, S, tau) in ("independent", "dependent")]
    ind_h = sum(ra.label(n, S, tau) == "independent" for _, n in det_h)
    out["cpu_heavy"] = {"rounds": len(heavy), "next_determinate": len(det_h),
                        "next_independent": ind_h / len(det_h) if det_h else None,
                        "tool_s_median": sorted(h["tool_s"] for h in heavy)[len(heavy) // 2] if heavy else None}
    lines += ["", "## CPU-heavy rounds (>= 1 s of tools)", "",
              f"{len(heavy)} rounds (median {out['cpu_heavy']['tool_s_median'] or 0:.1f} s of tools); the next round is "
              f"independent of their results in {ind_h}/{len(det_h)} determinate cases"
              + (f" ({ind_h / len(det_h):.0%})." if det_h else "."), ""]
    (rr.DATA / "tables" / f"explore_{a.run}.md").write_text("\n".join(lines) + "\n")
    (rr.DATA / "tables" / f"explore_{a.run}.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
