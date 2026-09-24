#!/usr/bin/env python3
"""Tables for the context-handoff experiment (stage 1 micro-loop and stage 2 real harness).

    python3 research/07_task_dag/prewarm_analyze.py \
        --stage1 research/07_task_dag/data/prewarm/stage1.jsonl --stage2 research/07_task_dag/data/prewarm/harness --md

Primary outcome is ROUNDS of the successor: prefill is under 2% of a call on this provider, so the
only thing a context handoff can buy is the agent-loop round the successor would have spent opening
the file.  Everything is priced with the measured constants so the result survives the provider:

    saved  = rounds_removed x (3.72 s fixed + 17.4 ms x output tokens of the removed round)
    paid   = injected tokens x 0.033 ms of prefill

The negative control (pollution: an equal-size irrelevant file) is what decides whether a reduction
is caused by the CONTENT or merely by a bulky early tool result.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
from input_redundancy import classify_bash  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FIXED_ROUND_S = 3.72
MS_PER_OUTPUT_TOKEN = 17.44
MS_PER_UNCACHED_TOKEN = 0.033
CHARS_PER_TOKEN = 4.0


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def sd(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def fmt(value, digits=2):
    return "-" if value is None else f"{value:.{digits}f}"


def bootstrap_diff(a: list[float], b: list[float], reps: int = 10000,
                   seed: int = 20260916) -> tuple[float, float, float]:
    """Unpaired bootstrap of mean(a) - mean(b)."""
    import random
    if not a or not b:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    point = sum(a) / len(a) - sum(b) / len(b)
    diffs = []
    for _ in range(reps):
        sa = [a[rng.randrange(len(a))] for _ in a]
        sb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(sum(sa) / len(sa) - sum(sb) / len(sb))
    diffs.sort()
    return (point, diffs[int(0.025 * reps)], diffs[int(0.975 * reps) - 1])


# ----------------------------------------------------------------------------- stage 1
def stage1_tables(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # a trial that ran out of rounds is a valid, RIGHT-CENSORED observation: dropping it would throw
    # away exactly the baseline trials that flailed, biasing the effect towards zero... in the wrong
    # direction.  Keep them, and report how many there were.
    rows = [r for r in rows if r.get("stopped") in ("answered", "max_rounds")]
    if not rows:
        return "_stage 1: no completed trials_"
    out = ["## Stage 1. Scripted successor agent (micro-loop)\n",
           "| arm | n | rounds | censored | read_file calls | locate calls | correct | "
           "injected tok | prompt tok | output tok | wall s |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    by_arm = defaultdict(list)
    for row in rows:
        by_arm[row["arm"]].append(row)
    for arm in ("baseline", "oracle", "pollution", "summary"):
        group = by_arm.get(arm)
        if not group:
            continue
        out.append(
            f"| {arm} | {len(group)} | {fmt(mean([g['rounds'] for g in group]))} "
            f"(sd {fmt(sd([g['rounds'] for g in group]))}) | "
            f"{sum(1 for g in group if g.get('stopped') == 'max_rounds')} | "
            f"{fmt(mean([g['n_read_file'] for g in group]))} | "
            f"{fmt(mean([g['locate_calls'] for g in group]))} | "
            f"{fmt(100 * mean([1.0 if g['correct'] else 0.0 for g in group]), 0)}% | "
            f"{fmt(mean([g['injected_chars'] for g in group]) / CHARS_PER_TOKEN, 0)} | "
            f"{fmt(mean([g['input_tokens'] for g in group]), 0)} | "
            f"{fmt(mean([g['output_tokens'] for g in group]), 0)} | "
            f"{fmt(mean([g['wall_s'] for g in group]))} |")

    base = [g["rounds"] for g in by_arm.get("baseline", [])]
    censored = sum(1 for r in rows if r.get("stopped") == "max_rounds")
    if censored:
        out.append(f"\n{censored} of {len(rows)} trials hit the {12}-round cap (all of them are kept, "
                   "right-censored, so every round difference below is a LOWER bound).")
    out.append("\n### Stage 1 contrasts against baseline (unpaired bootstrap)\n")
    out.append("| arm | delta rounds | 95% CI | delta read_file | delta correct | delta wall s |")
    out.append("|---|---|---|---|---|---|")
    for arm in ("oracle", "pollution", "summary"):
        group = by_arm.get(arm)
        if not group or not base:
            continue
        point, lo, hi = bootstrap_diff([g["rounds"] for g in group], base)
        d_read = mean([g["n_read_file"] for g in group]) - mean(
            [g["n_read_file"] for g in by_arm["baseline"]])
        d_ok = (mean([1.0 if g["correct"] else 0.0 for g in group])
                - mean([1.0 if g["correct"] else 0.0 for g in by_arm["baseline"]]))
        d_wall = mean([g["wall_s"] for g in group]) - mean([g["wall_s"] for g in by_arm["baseline"]])
        out.append(f"| {arm} | {point:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {d_read:+.2f} | "
                   f"{100 * d_ok:+.0f} pts | {d_wall:+.1f} |")

    out.append("\n### Stage 1 by task (rounds)\n")
    tasks = sorted({r["task"] for r in rows})
    out.append("| arm | " + " | ".join(tasks) + " |")
    out.append("|---|" + "---|" * len(tasks))
    for arm in ("baseline", "oracle", "pollution", "summary"):
        if arm not in by_arm:
            continue
        cells = []
        for task in tasks:
            values = [g["rounds"] for g in by_arm[arm] if g["task"] == task]
            cells.append(fmt(mean(values), 1))
        out.append(f"| {arm} | " + " | ".join(cells) + " |")

    # did the agent fetch what it was already handed?
    ora = [g for g in by_arm.get("oracle", []) if g.get("re_read_after_injection") is not None]
    if ora:
        rate = mean([1.0 if g["re_read_after_injection"] else 0.0 for g in ora])
        out.append(f"\nRe-read rate under oracle injection: {100 * rate:.0f}% of trials still issued "
                   f"a read_file ({len(ora)} trials). A high rate means the handoff fails "
                   "behaviourally -- no serving-side mechanism can remove a round the agent insists "
                   "on spending.")
    return "\n".join(out)


# ----------------------------------------------------------------------------- stage 2
def full_text(value) -> str:
    """trace_runtime records a message as {characters, sha256, preview, truncated, full}."""
    if isinstance(value, dict):
        full = value.get("full")
        if isinstance(full, str):
            return full
        if isinstance(full, dict):
            return str(full.get("preview") or "")
        return str(value.get("preview") or "")
    return str(value or "")


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return out


def stage2_rows(trace: Path) -> list[dict]:
    """One row per successor task (a task with at least one blocker) that was actually claimed."""
    records = load(trace)
    meta = next((r["data"] for r in records if r.get("event") == "profile_meta"), {})
    arm = (meta.get("prewarm") or {}).get("arm", "none")
    label = meta.get("label", trace.stem)
    tasks: dict[str, dict] = {}
    names: dict[str, str] = {}
    injected: dict[str, dict] = {}
    results: dict[str, list] = {}
    for record in records:
        data = record.get("data") or {}
        event = record.get("event")
        elapsed = record.get("elapsed_ms", 0.0)
        if event == "task_create":
            tasks[data.get("id")] = {"id": data.get("id"), "blocked": list(data.get("blockedBy") or []),
                                     "owner": None, "claim_s": None, "done_s": None,
                                     "subject": data.get("subject", "")}
        elif event == "task_update" and data.get("blocked_by_task_ids") is not None:
            if data.get("task_id") in tasks:
                tasks[data["task_id"]]["blocked"] = list(data["blocked_by_task_ids"])
        elif event == "task_claim" and data.get("task_id") in tasks:
            tasks[data["task_id"]].update(owner=data.get("owner"), claim_s=elapsed)
        elif event == "task_complete" and data.get("task_id") in tasks:
            tasks[data["task_id"]]["done_s"] = elapsed
        elif event == "agent_create":
            names[data.get("name")] = record.get("agent_id")
        elif event == "context_injection":
            injected[data.get("task_id")] = data
        elif event == "message_send" and data.get("message_type") == "result":
            results.setdefault(data.get("from"), []).append((elapsed, full_text(data.get("content"))))
    end = max((r.get("elapsed_ms", 0.0) for r in records), default=0.0)

    rows = []
    for task in tasks.values():
        if not task["blocked"] or task["claim_s"] is None:
            continue
        agent = names.get(task["owner"])
        if agent is None:
            continue
        stop = task["done_s"] if task["done_s"] is not None else end
        rounds = reads = locates = 0
        model_ms = out_tok = in_tok = cached = 0.0
        starts: dict[str, dict] = {}
        for record in records:
            if record.get("agent_id") != agent:
                continue
            elapsed = record.get("elapsed_ms", 0.0)
            if not (task["claim_s"] <= elapsed <= stop):
                continue
            data = record.get("data") or {}
            if record["event"] == "model_request":
                rounds += 1
                starts[record.get("span_id")] = record
            elif record["event"] == "model_response":
                usage = data.get("usage") or {}
                model_ms += data.get("duration_ms", 0.0) or 0.0
                in_tok += usage.get("input_tokens") or 0
                out_tok += usage.get("output_tokens") or 0
                cached += usage.get("cache_read_input_tokens") or 0
            elif record["event"] == "tool_start":
                tool = data.get("tool")
                args = data.get("arguments") or {}
                if tool == "read_file":
                    reads += 1
                elif tool == "bash" and classify_bash(args.get("command", ""))[0]:
                    reads += 1
                elif tool == "glob":
                    locates += 1
        # quality proxy: how many distinct repository files the successor's own report cites.
        # A handoff that removes rounds by making the agent answer from less evidence shows up here.
        # the final text is sent AFTER complete_task, i.e. after done_s, so the window needs slack
        report = ""
        for when, text in results.get(task["owner"], []):
            if task["claim_s"] <= when <= stop + 120_000:
                report = text
        cited = len(set(re.findall(r"[A-Za-z0-9_/]+\.(?:py|md)", report or "")))
        inj = injected.get(task["id"]) or {}
        pred_owners = {tasks[b]["owner"] for b in task["blocked"] if b in tasks}
        rows.append({
            "run": label, "arm": arm, "task": task["id"], "subject": task["subject"][:40],
            "owner": task["owner"], "rounds": rounds, "reads": reads, "locates": locates,
            "active_s": round(model_ms / 1000.0, 1), "in_tok": in_tok, "out_tok": out_tok,
            "cached": cached, "injected_chars": inj.get("chars", 0),
            "injected_pairs": inj.get("pairs", 0),
            "cross_owner": 1 if pred_owners - {task["owner"], None} else 0,
            "completed": 1 if task["done_s"] is not None else 0,
            "report_chars": len(report or ""), "cited_files": cited,
        })
    return rows


def stage2_tables(trace_dir: Path) -> str:
    traces = sorted(p for p in trace_dir.rglob("run_*.jsonl") if p.name.count(".") == 1)
    rows = [row for trace in traces for row in stage2_rows(trace)]
    if not rows:
        return "_stage 2: no successor tasks found_"
    by_arm = defaultdict(list)
    for row in rows:
        by_arm[row["arm"]].append(row)
    out = ["## Stage 2. Real s15 team harness, successor tasks only\n",
           "| arm | successors | cross-owner | rounds | reads | active s | prompt tok | out tok | "
           "injected tok | files cited | completed |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ("none", "dag", "oracle", "pollute", "summary"):
        group = by_arm.get(arm)
        if not group:
            continue
        out.append(
            f"| {arm} | {len(group)} | {sum(g['cross_owner'] for g in group)} | "
            f"{fmt(mean([g['rounds'] for g in group]))} | {fmt(mean([g['reads'] for g in group]))} | "
            f"{fmt(mean([g['active_s'] for g in group]), 1)} | "
            f"{fmt(mean([g['in_tok'] for g in group]), 0)} | "
            f"{fmt(mean([g['out_tok'] for g in group]), 0)} | "
            f"{fmt(mean([g['injected_chars'] for g in group]) / CHARS_PER_TOKEN, 0)} | "
            f"{fmt(mean([float(g['cited_files']) for g in group]), 1)} | "
            f"{fmt(100 * mean([float(g['completed']) for g in group]), 0)}% |")

    base = by_arm.get("none", [])
    if base:
        out.append("\n### Stage 2 contrasts against baseline\n")
        out.append("| arm | delta rounds | 95% CI | delta reads | delta active s | "
                   "saved s (rounds x fixed) | prefill paid s | net s |")
        out.append("|---|---|---|---|---|---|---|---|")
        for arm in ("dag", "oracle", "pollute", "summary"):
            group = by_arm.get(arm)
            if not group:
                continue
            point, lo, hi = bootstrap_diff([g["rounds"] for g in group],
                                           [g["rounds"] for g in base])
            d_reads = mean([g["reads"] for g in group]) - mean([g["reads"] for g in base])
            d_active = mean([g["active_s"] for g in group]) - mean([g["active_s"] for g in base])
            saved = -point * (FIXED_ROUND_S + MS_PER_OUTPUT_TOKEN
                              * mean([g["out_tok"] for g in base]) / max(mean([g["rounds"] for g in base]), 1)
                              / 1000.0)
            paid = mean([g["injected_chars"] for g in group]) / CHARS_PER_TOKEN * MS_PER_UNCACHED_TOKEN / 1000.0
            out.append(f"| {arm} | {point:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {d_reads:+.2f} | "
                       f"{d_active:+.1f} | {saved:+.1f} | {paid:.2f} | {saved - paid:+.1f} |")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage1", default=None)
    parser.add_argument("--stage2", default=None)
    parser.add_argument("--json", default=None)
    parser.add_argument("--md", action="store_true")
    args = parser.parse_args()
    blocks = []
    if args.stage1 and Path(args.stage1).exists():
        blocks.append(stage1_tables(Path(args.stage1)))
    if args.stage2 and Path(args.stage2).exists():
        blocks.append(stage2_tables(Path(args.stage2)))
    print("\n\n".join(blocks) if blocks else "nothing to analyse")
    if args.json:
        payload = {}
        if args.stage2 and Path(args.stage2).exists():
            payload["stage2"] = [row for trace in sorted(Path(args.stage2).rglob("run_*.jsonl"))
                                 if trace.name.count(".") == 1 for row in stage2_rows(trace)]
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
