#!/usr/bin/env python3
"""Tables of the EPIC round (plan "EPIC round"), from jobs/<server>+<arm>/<bench>/<phase>__*/ and the server's
EPIC_LOG. Writes report/epic_<server>_<bench>.md and prints it.

    .venv/bin/python report/epic_report.py --server minimax-m2.7-epic --bench swe --phase e1 \
        [--log runs/epic/minimax-m2.7-epic.calls.jsonl] [--baseline minimax-m2.7]

**Primary analysis**, fixed in advance:
- A block is one (replicate phase, task); a rerun phase "<phase>r<r>x<n>" counts toward block "<phase>r<r>". Each
  arm contributes its first valid trial per block, ordered by start time.
- Control = the mean of `prefix` and `prefix_aa` in the block (just `prefix` when there is no A/A arm).
- Per task: the mean over its blocks of (arm − control). The interval is a t-interval over the task means.
- Non-inferior when the one-sided 95 % lower bound is above −margin. Arms are tested in a fixed order
  (default: epic128a1, epic128, shift), stopping at the first failure.

**Secondary**, reported without multiplicity correction:
- the same analysis restricted to tasks whose baseline runs snipped (EPIC can only act after a snip);
- per arm: pass@1, pass^3 and consistency over the replicates, runs at the budget, lead calls, output tokens;
- from EPIC_LOG: computed / prompt tokens, fallbacks and resets per post-snip call, and the shadow probes
  (reused rows' D^K by age, first-token KL, reply KL and top-1);
- first divergence: the first lead call whose reply differs from `prefix`'s in the same block (A/A included).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
import collect  # noqa: E402

AGE_BINS = (0, 1, 2, 4, 8, 16)
OFF_N = 5
T95 = {29: 1.699, 28: 1.701, 27: 1.703, 26: 1.706, 25: 1.708, 24: 1.711, 20: 1.725, 15: 1.753, 10: 1.812,
       5: 2.015, 3: 2.353, 2: 2.920, 1: 6.314}      # one-sided 95% t quantiles by degrees of freedom


T95_LARGE = {40: 1.684, 50: 1.676, 60: 1.671, 70: 1.667, 80: 1.664, 90: 1.662, 99: 1.660, 120: 1.658}


def t95(df: int) -> float:
    if df in T95:
        return T95[df]
    if df < 30:
        keys = sorted(T95)
        return T95[min(keys, key=lambda k: abs(k - df))]
    if df > 120:
        return 1.645
    # e3's 100 task means: df 99 -> 1.660 (the normal 1.645 would be slightly anti-conservative)
    keys = sorted(T95_LARGE)
    return T95_LARGE[min(keys, key=lambda k: abs(k - df))]


def block_of(phase: str) -> str:
    """"<p>r<r>", per-block "<p>r<r>b<i>" and reruns "<p>r<r>[b<i>]x<n>" all belong to block phase "<p>r<r>"."""
    return re.sub(r"(b\d+)?(x\d+)?$", "", phase)


def first_valid(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    """(arm, block, task) -> the first valid trial (finished, not infrastructure) by start time."""
    out: dict = {}
    for r in sorted(rows, key=lambda r: r.get("started") or ""):
        if r.get("infra") or not r.get("finished"):
            continue
        key = (r["model"].split("+", 1)[1], block_of(r["phase"]), r["task"])
        out.setdefault(key, r)
    return out


def paired(trials: dict, arm: str, control: list[str], tasks: set[str] | None = None,
           skip_blocks: set[tuple[str, str]] | None = None) -> dict:
    per_task: dict[str, list[float]] = defaultdict(list)
    for (a, block, task), r in trials.items():
        if a != arm or (tasks is not None and task not in tasks):
            continue
        if skip_blocks and (block, task) in skip_blocks:
            continue
        ctl = [trials.get((c, block, task)) for c in control]
        if any(c is None for c in ctl):
            continue
        per_task[task].append(collect.success(r) - st.mean(collect.success(c) for c in ctl))
    means = [st.mean(v) for v in per_task.values()]
    n = len(means)
    if n < 2:
        return {"tasks": n, "blocks": sum(len(v) for v in per_task.values()), "diff": None, "lo": None, "hi": None}
    m, sd = st.mean(means), st.stdev(means)
    half = t95(n - 1) * sd / math.sqrt(n)
    return {"tasks": n, "blocks": sum(len(v) for v in per_task.values()), "diff": m, "lo": m - half,
            "hi": m + half, "se": sd / math.sqrt(n)}


def snipped_tasks(baseline: str, bench: str) -> set[str]:
    out = set()
    for r in collect.load_trials():
        if r["model"] == baseline and r["bench"] == bench and r["phase"].startswith("q") and not r.get("infra"):
            if (r.get("compactions") or {}).get("snip_compact", 0) > 0:
                out.add(r["task"])
    return out


def server_metrics(log_path: "Path | list[Path]", specs_by_arm: dict[str, set[str]]) -> dict[str, dict]:
    """Per-arm EPIC_LOG metrics; `log_path` may be a list (one log per server instance, e.g. Qwen3.8's four)."""
    out = {arm: defaultdict(float) for arm in specs_by_arm}
    shadow = {arm: defaultdict(list) for arm in specs_by_arm}
    spec_arm = {s: arm for arm, ss in specs_by_arm.items() for s in ss}
    paths = [p for p in (log_path if isinstance(log_path, list) else [log_path]) if p.is_file()]
    if not paths:
        return {}
    for line in (ln for p in paths for ln in p.read_text().splitlines()):
        try:
            x = json.loads(line)
        except json.JSONDecodeError:
            continue
        arm = spec_arm.get(str(x.get("spec")))
        if arm is None:
            continue
        o = out[arm]
        if x.get("ev") == "error":
            o["errors"] += 1
            continue
        if x.get("ev") != "call":
            continue
        o["calls"] += 1
        P = x.get("P") or 0
        o["P"] += P
        if "C" in x:                                         # EPIC arms: the plan's accounting
            o["C"] += x["C"]
            o["S"] += x.get("S", 0)
            o["edited"] += int(bool(x.get("edited")))
        elif "ideal_C" in x:                                 # prefix arms: an unbounded prefix cache's recompute
            o["C"] += int(x["ideal_C"])
            o["C_served"] += max(0, P - int(x.get("apc_hit") or 0))   # what the (evicting) cache actually left
        else:
            o["C"] += max(0, P - int(x.get("apc_hit") or 0))
        o["fallback"] += int(bool(x.get("fallback")))
        o["resets"] += int(bool(x.get("entry_reset")))
        o["aborted"] += int(bool(x.get("aborted")))
        sh = x.get("shadow")
        if sh and not sh.get("floor"):
            for k in ("first_kl", "tf_kl", "tf_top1"):
                if isinstance(sh.get(k), (int, float)) and not math.isnan(sh[k]):
                    shadow[arm][k].append(sh[k])
            for b, rows in (sh.get("l1") or {}).items():
                b = int(b)
                cls, rest = divmod(b, len(AGE_BINS) * OFF_N)
                age = rest // OFF_N
                if cls == 1:                                 # reused rows
                    shadow[arm][f"dk_age{age}"].append((sum(r[0] for r in rows), sum(r[1] for r in rows)))
    res = {}
    for arm, o in out.items():
        sm = shadow[arm]
        dk = {}
        for i in range(len(AGE_BINS)):
            pairs = sm.get(f"dk_age{i}", [])
            if pairs:
                dk[i] = sum(p[0] for p in pairs) / max(1e-12, sum(p[1] for p in pairs))
        res[arm] = {**o, "computed_share": o["C"] / o["P"] if o["P"] else None,
                    "first_kl": st.mean(sm["first_kl"]) if sm.get("first_kl") else None,
                    "tf_kl": st.mean(sm["tf_kl"]) if sm.get("tf_kl") else None,
                    "tf_top1": st.mean(sm["tf_top1"]) if sm.get("tf_top1") else None,
                    "probes": len(sm.get("first_kl", [])), "dk_by_age": dk}
    return res


def first_divergence(trials: dict, arm: str, ref: str = "prefix") -> list[int]:
    """Per block: the first lead call whose reply differs from the reference arm's (None if identical)."""
    out = []
    for (a, block, task), r in trials.items():
        if a != arm or (ref, block, task) not in trials:
            continue
        def replies(row):
            path = ROOT / "jobs" / row["model"] / row["bench"] / row["job"] / row["trial"] / "agent" / "s15" / "calls.jsonl"
            seq = []
            for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
                c = json.loads(line)
                if c.get("purpose") == "lead" and c.get("response") is not None:
                    seq.append(json.dumps(c["response"], sort_keys=True))
            return seq
        x, y = replies(r), replies(trials[(ref, block, task)])
        k = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), None)
        out.append(k if k is not None else (min(len(x), len(y)) if len(x) != len(y) else -1))
    return out


def baseline_section(trials: dict, arms: list[str], baseline: str, bench: str) -> list[str]:
    """Each arm against the baseline profile on the same tasks (paired by task; t-interval over task means). The
    baseline ran on another server (CUDA graphs, the vendor template), on another day, with the default agent
    timeout, so the difference mixes the arm with those changes."""
    base = [r for r in collect.profile_rows([r for r in collect.load_trials() if r["model"] == baseline
                                             and r["bench"] == bench]) if not r.get("infra")]
    if not base:
        return []
    bt: dict[str, list[float]] = defaultdict(list)
    for r in base:
        bt[r["task"]].append(collect.success(r))

    def proc(rs):
        return (st.mean(collect.success(r) for r in rs), st.mean(r.get("lead_calls") or 0 for r in rs),
                st.mean(float(r.get("s15_status") == "budget") for r in rs),
                sum(1 for r in rs if r.get("s15_status") == "timeout"))

    p1, lead, bud, tmo = proc(base)
    lines = [f"### Against the baseline profile (`{baseline}`, same tasks; another server, template and day)", "",
             "| run | trials | pass@1 | minus baseline, 90% [lo, hi] (task-paired) | lead calls | at budget | agent timeout |",
             "|---|---|---|---|---|---|---|",
             f"| baseline | {len(base)} | {p1:.3f} | — | {lead:.1f} | {bud:.1%} | {tmo} |"]
    for arm in arms:
        rs = [r for (a2, _b, _t), r in trials.items() if a2 == arm]
        if not rs:
            continue
        at: dict[str, list[float]] = defaultdict(list)
        for (a2, _b, t), r in trials.items():
            if a2 == arm:
                at[t].append(collect.success(r))
        d = [st.mean(at[t]) - st.mean(bt[t]) for t in sorted(at) if t in bt]
        if len(d) >= 2:
            m, half = st.mean(d), t95(len(d) - 1) * st.stdev(d) / math.sqrt(len(d))
            cell = f"{m:+.3f} [{m - half:+.3f}, {m + half:+.3f}]"
        else:
            cell = "—"
        q1, ql, qb, qt = proc(rs)
        lines.append(f"| {arm} | {len(rs)} | {q1:.3f} | {cell} | {ql:.1f} | {qb:.1%} | {qt} |")
    return lines + [""]


def fmt(v, pct=False, nd=3):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}" if not pct else f"{v:.1%}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--bench", default="swe")
    ap.add_argument("--phase", default="e1")
    ap.add_argument("--log", default="")
    ap.add_argument("--baseline", default="")
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--order", default="epic128a1,epic128,shift")
    ap.add_argument("--tf-invalid", default="", help="reason the teacher-forced reply metrics are not shown")
    ap.add_argument("--no-notes", action="store_true", help="leave the hand-written notes out (another part has them)")
    ap.add_argument("--out", default="", help="default report/epic_<server>_<bench>.md (e3: another SWE part, so not e1's)")
    ap.add_argument("--exploratory", action="store_true",
                    help="intervals only, no non-inferiority verdicts (plan: Qwen3.8 and DeepSeek are exploratory)")
    a = ap.parse_args()
    rows = [r for r in collect.load_trials() if r["model"].startswith(a.server + "+") and r["bench"] == a.bench
            and any(block_of(r["phase"]).startswith(ph + "r") for ph in a.phase.split(","))]
    trials = first_valid(rows)
    arms = sorted({k[0] for k in trials})
    control = [c for c in ("prefix", "prefix_aa") if c in arms] or ["prefix"]
    lines = [f"## EPIC round: {a.server} on {a.bench} (phase {a.phase})", "",
             f"Arms: {', '.join(arms)}. Control: mean of {' and '.join(control)} per block. Blocks: "
             f"{len({(k[1], k[2]) for k in trials})}. Infrastructure failures are excluded and rerun under the same "
             "block key.", "",
             (f"### Exploratory: resolve rate, arm minus control (t-interval over task means; intervals only, no "
              f"verdicts)" if a.exploratory else
              f"### Primary: resolve rate, arm minus control (t-interval over task means; non-inferior if the one-sided "
              f"95% bound > −{a.margin:.2f}; fixed order {a.order}, stop at first failure)"), "",
             "| arm | tasks | blocks | difference | 90% two-sided [lo, hi] | non-inferior |", "|---|---|---|---|---|---|"]
    stopped = False
    for arm in a.order.split(","):
        if arm not in arms:
            continue
        r = paired(trials, arm, control)
        verdict = "—"
        if a.exploratory:
            pass
        elif r["lo"] is not None and not stopped:
            ni = r["lo"] > -a.margin
            verdict = "**yes**" if ni else "no (stop)"
            stopped = stopped or not ni
        elif stopped and not a.exploratory:
            verdict = "not tested (sequence stopped)"
        lines.append(f"| {arm} | {r['tasks']} | {r['blocks']} | {fmt(r['diff'])} | [{fmt(r['lo'])}, {fmt(r['hi'])}] | {verdict} |")
    if "prefix_aa" in arms:
        r = paired(trials, "prefix_aa", ["prefix"])
        lines.append(f"| prefix_aa vs prefix (A/A) | {r['tasks']} | {r['blocks']} | {fmt(r['diff'])} | "
                     f"[{fmt(r['lo'])}, {fmt(r['hi'])}] | noise floor |")
    lines.append("")
    # sensitivity (added during e2): blocks in which any call of an EPIC arm fell back to prefix (host pool exhausted)
    log_paths = [Path(x) for x in a.log.split(",")] if a.log else [ROOT / "runs" / "epic" / f"{a.server}.calls.jsonl"]
    spec_block = {r["trial"]: (block_of(r["phase"]), r["task"]) for r in rows}
    fb_blocks = set()
    for lp in log_paths:
        for line in (lp.read_text().splitlines() if lp.is_file() else []):
            if '"fallback": true' not in line:
                continue
            try:
                x = json.loads(line)
            except json.JSONDecodeError:
                continue
            if x.get("ev") == "call" and x.get("spec") in spec_block:
                fb_blocks.add(spec_block[x["spec"]])
    if fb_blocks:
        lines += [f"Sensitivity: the same, without the {len(fb_blocks)} blocks in which an EPIC arm's call fell back to "
                  "prefix (host pool exhausted):", "",
                  "| arm | tasks | blocks | difference | 90% two-sided [lo, hi] |", "|---|---|---|---|---|"]
        for arm in a.order.split(","):
            if arm in arms:
                r = paired(trials, arm, control, skip_blocks=fb_blocks)
                lines.append(f"| {arm} | {r['tasks']} | {r['blocks']} | {fmt(r['diff'])} | [{fmt(r['lo'])}, {fmt(r['hi'])}] |")
        lines.append("")
    # sensitivity (added during e1): the 50-min agent timeout binds on long runs, and on the emulating server the
    # EPIC arms' calls are slower (overrides, twice the shadow probes), so a timeout cuts them off at fewer calls
    timed_out = {(b, t) for (_a, b, t), r in trials.items() if r.get("s15_status") == "timeout"}
    if timed_out:
        lines += [f"Sensitivity: the same, without the {len(timed_out)} blocks where any arm hit the agent timeout "
                  "(the timeout binds earlier for the EPIC arms, whose calls are slower on the emulating server):", "",
                  "| arm | tasks | blocks | difference | 90% two-sided [lo, hi] |", "|---|---|---|---|---|"]
        for arm in a.order.split(","):
            if arm in arms:
                r = paired(trials, arm, control, skip_blocks=timed_out)
                lines.append(f"| {arm} | {r['tasks']} | {r['blocks']} | {fmt(r['diff'])} | [{fmt(r['lo'])}, {fmt(r['hi'])}] |")
        lines.append("")
    if a.baseline:
        snip = snipped_tasks(a.baseline, a.bench)
        lines += [f"### Secondary: tasks whose baseline runs snipped ({len(snip)} tasks)", "",
                  "| arm | tasks | blocks | difference | 90% [lo, hi] |", "|---|---|---|---|---|"]
        for arm in a.order.split(","):
            if arm in arms:
                r = paired(trials, arm, control, snip)
                lines.append(f"| {arm} | {r['tasks']} | {r['blocks']} | {fmt(r['diff'])} | [{fmt(r['lo'])}, {fmt(r['hi'])}] |")
        lines.append("")
    # per-arm process table
    lines += ["### Per arm", "", "| arm | trials | pass@1 | pass^3 | consistent | at budget | agent timeout | lead calls | out tok |",
              "|---|---|---|---|---|---|---|---|---|"]
    specs_by_arm: dict[str, set[str]] = defaultdict(set)
    for arm in arms:
        rs = [r for (a2, _b, _t), r in trials.items() if a2 == arm]
        by_task = defaultdict(list)
        for (a2, _b, t), r in trials.items():
            if a2 == arm:
                by_task[t].append(collect.success(r))
        full = [v for v in by_task.values() if len(v) >= 3]
        p3 = st.mean(float(all(v[:3])) for v in full) if full else None
        cons = st.mean(float(len(set(v[:3])) == 1) for v in full) if full else None
        budget = st.mean(float(r.get("s15_status") == "budget") for r in rs) if rs else None
        timeouts = sum(1 for r in rs if r.get("s15_status") == "timeout")
        lead = st.mean(r.get("lead_calls") or 0 for r in rs) if rs else None
        out_tok = st.mean(r.get("out_tokens") or 0 for r in rs) if rs else None
        p1 = st.mean(collect.success(r) for r in rs) if rs else None
        lines.append(f"| {arm} | {len(rs)} | {p1:.3f} | {p3 if p3 is None else f'{p3:.3f}'} | "
                     f"{cons if cons is None else f'{cons:.2f}'} | {budget:.1%} | {timeouts} | {lead:.1f} | {out_tok:,.0f} |")
        for r in rows:
            if r["model"].endswith("+" + arm):
                specs_by_arm[arm].add(r["trial"])
    lines.append("")
    if a.baseline:
        lines += baseline_section(trials, arms, a.baseline, a.bench)
    log_path = [Path(x) for x in a.log.split(",")] if a.log else ROOT / "runs" / "epic" / f"{a.server}.calls.jsonl"
    sm = server_metrics(log_path, specs_by_arm)
    if sm:
        lines += ["### Server side (EPIC_LOG)", "",
                  "| arm | lead calls logged | computed / prompt | post-snip calls | fallbacks | resets | errors | "
                  "probes | first-token KL | reply KL | reply top-1 | D^K reused by age (0,1,2-3,4-7,8-15,16+) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm in arms:
            m = sm.get(arm) or {}
            dk = m.get("dk_by_age") or {}
            dks = " / ".join(f"{dk[i]:.3f}" if i in dk else "—" for i in range(len(AGE_BINS)))
            lines.append(f"| {arm} | {int(m.get('calls', 0))} | {fmt(m.get('computed_share'), pct=True)} | "
                         f"{int(m.get('edited', 0))} | {int(m.get('fallback', 0))} | {int(m.get('resets', 0))} | "
                         f"{int(m.get('errors', 0))} | {m.get('probes', 0)} | "
                         f"{'—' if m.get('first_kl') is None else f'{m['first_kl']:.4f}'} | "
                         f"{'invalid' if a.tf_invalid else '—' if m.get('tf_kl') is None else f'{m['tf_kl']:.4f}'} | "
                         f"{'invalid' if a.tf_invalid else '—' if m.get('tf_top1') is None else f'{m['tf_top1']:.2%}'} | {dks} |")
        if a.tf_invalid:
            lines += ["", f"Reply KL and top-1 are not shown: {a.tf_invalid}"]
        lines.append("")
    lines += ["### First divergence from `prefix` in the same block (lead-call index; −1 = identical run)", "",
              "| arm | blocks | identical | median first divergent call |", "|---|---|---|---|"]
    for arm in arms:
        if arm == "prefix":
            continue
        fd = first_divergence(trials, arm)
        ident = sum(1 for k in fd if k == -1)
        div = [k for k in fd if k >= 0]
        lines.append(f"| {arm} | {len(fd)} | {ident} | {st.median(div) if div else '—'} |")
    lines.append("")
    # pilots and deviations, written by hand: the phase's own notes if there are any, else the server's
    notes = ROOT / "runs" / "epic" / f"notes_{a.server}_{a.phase}.md"
    if not notes.is_file():
        notes = ROOT / "runs" / "epic" / f"notes_{a.server}.md"
    if notes.is_file() and not a.no_notes:
        lines += [notes.read_text().rstrip(), ""]
    elif notes.is_file():
        lines += [f"(Setup and deviations of phase {a.phase}: see the {a.phase} notes in the other benchmark's part.)", ""]
    text = "\n".join(lines) + "\n"
    out = Path(a.out) if a.out else ROOT / "report" / f"epic_{a.server}_{a.bench}.md"
    out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
