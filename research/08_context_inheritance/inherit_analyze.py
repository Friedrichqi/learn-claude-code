#!/usr/bin/env python3
"""The five metrics for the three-arm inheritance experiment.

    python3 research/08_context_inheritance/inherit_analyze.py \
        --stage1 research/08_context_inheritance/data/inherit/stage1.jsonl --stage2 research/08_context_inheritance/data/inherit --md

Arms: none (baseline) / dag (direct predecessors only) / ancestors (transitive closure).

  1  byte-level recall     what share of the bytes the successor needed was in the handed-over set
  2  rounds reduced        against the baseline arm
  3  E2E latency           wall, split into model / tool / other, and priced with the measured
                           constants so it generalises past this provider
  4  accuracy              graded, and split into the half only the ancestors arm was handed
                           versus the half the dag arm already had
  5  recalled-token ratio  injected tokens as a share of the prompt they were injected into
"""

from __future__ import annotations

import argparse
import json
import random
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
ARM_ORDER = ["none", "dag", "ancestors"]
ARM_LABEL = {"none": "none (baseline)", "dag": "dag (direct preds)",
             "ancestors": "ancestors (closure)"}


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def sd(v):
    v = [x for x in v if x is not None]
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5


def fmt(x, d=2):
    return "-" if x is None else f"{x:.{d}f}"


def boot(a: list[float], b: list[float], reps: int = 10000, seed: int = 20260918):
    """Bootstrap of mean(a) - mean(b)."""
    if not a or not b:
        return (None, None, None)
    rng = random.Random(seed)
    point = sum(a) / len(a) - sum(b) / len(b)
    out = []
    for _ in range(reps):
        sa = [a[rng.randrange(len(a))] for _ in a]
        sb = [b[rng.randrange(len(b))] for _ in b]
        out.append(sum(sa) / len(sa) - sum(sb) / len(sb))
    out.sort()
    return (point, out[int(0.025 * reps)], out[int(0.975 * reps) - 1])


# ----------------------------------------------------------------------------- stage 1
def _opening_prompt_chars(row: dict) -> int | None:
    """Exact size of the round-1 prompt: system + question + whatever was injected.

    The provider's own `input_tokens` cannot be used as the denominator here: z.ai reports it
    EXCLUDING cached tokens, so a trial served from the prefix cache reports 20 tokens for a 1,428
    token prompt and the ratio explodes.  Composing it from the pieces is exact and provider-neutral.
    """
    try:
        from inherit_loop import SYSTEM, TASKS
    except Exception:
        return None
    task = TASKS.get(row.get("task"))
    if task is None:
        return None
    return (len(SYSTEM.format(repo=REPO)) + len(task["question"]) + row.get("injected_chars", 0))


def stage1(path: Path) -> str:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("stopped") in ("answered", "max_rounds")]
    if not rows:
        return "_stage 1: no completed trials_"
    by = defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    out = ["## Stage 1 — scripted successor agent (one agent, real tools, no team)\n",
           "| arm | n | rounds | censored | read_file | locate | wall s | model s | tool s | "
           "prompt tok | injected tok | **inherited share of opening prompt** | "
           "inherited share of all prompt tokens | accuracy | of which GRAND half | DIRECT half |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARM_ORDER:
        g = by.get(arm)
        if not g:
            continue
        # metric 5, two readings: the share of the OPENING prompt that is inherited content, and
        # the share of ALL prompt tokens the trial ever processed (the block is re-sent every round)
        opens = [(r["injected_chars"], _opening_prompt_chars(r)) for r in g]
        ratio = mean([i / o for i, o in opens if o])
        totals = mean([(r["injected_chars"] / CHARS_PER_TOKEN * r["rounds"])
                       / max(r["input_tokens"] + r["cached_tokens"], 1) for r in g])
        out.append(
            f"| {ARM_LABEL[arm]} | {len(g)} | {fmt(mean([r['rounds'] for r in g]))} "
            f"(sd {fmt(sd([r['rounds'] for r in g]))}) | "
            f"{sum(1 for r in g if r['stopped'] == 'max_rounds')} | "
            f"{fmt(mean([r['n_read_file'] for r in g]))} | "
            f"{fmt(mean([r['locate_calls'] for r in g]))} | "
            f"{fmt(mean([r['wall_s'] for r in g]), 1)} | "
            f"{fmt(mean([r['model_s'] for r in g]), 1)} | "
            f"{fmt(mean([r['tool_s'] for r in g]), 2)} | "
            f"{fmt(mean([r['input_tokens'] for r in g]), 0)} | "
            f"{fmt(mean([r['injected_chars'] for r in g]) / CHARS_PER_TOKEN, 0)} | "
            f"**{fmt(100 * ratio, 1) if ratio is not None else '-'}%** | "
            f"{fmt(100 * totals, 1) if totals is not None else '-'}% | "
            f"{fmt(100 * mean([r['hits'] / r['total'] for r in g]), 0)}% | "
            f"{fmt(100 * mean([r['hits_grand'] / r['total_grand'] for r in g]), 0)}% | "
            f"{fmt(100 * mean([r['hits_direct'] / r['total_direct'] for r in g]), 0)}% |")

    base = by.get("none", [])
    if base:
        out.append("\n### Contrasts against the baseline arm\n")
        out.append("| arm | Δ rounds | 95% CI | Δ wall s | 95% CI | Δ accuracy | Δ GRAND half | "
                   "Δ DIRECT half |")
        out.append("|---|---|---|---|---|---|---|---|")
        for arm in ("dag", "ancestors"):
            g = by.get(arm)
            if not g:
                continue
            pr, lo, hi = boot([r["rounds"] for r in g], [r["rounds"] for r in base])
            pw, wlo, whi = boot([r["wall_s"] for r in g], [r["wall_s"] for r in base])
            da = (mean([r["hits"] / r["total"] for r in g])
                  - mean([r["hits"] / r["total"] for r in base]))
            dg = (mean([r["hits_grand"] / r["total_grand"] for r in g])
                  - mean([r["hits_grand"] / r["total_grand"] for r in base]))
            dd = (mean([r["hits_direct"] / r["total_direct"] for r in g])
                  - mean([r["hits_direct"] / r["total_direct"] for r in base]))
            out.append(f"| {ARM_LABEL[arm]} | {pr:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {pw:+.1f} | "
                       f"[{wlo:+.1f}, {whi:+.1f}] | {100 * da:+.0f} pts | {100 * dg:+.0f} pts | "
                       f"{100 * dd:+.0f} pts |")
        # the decisive contrast: does reaching past the direct predecessor add anything?
        d, a = by.get("dag"), by.get("ancestors")
        if d and a:
            out.append("\n### The decisive contrast: ancestors vs dag (is reaching further worth it?)\n")
            out.append("| measure | dag | ancestors | difference | 95% CI |")
            out.append("|---|---|---|---|---|")
            for name, key, dec in (("rounds", "rounds", 2), ("wall s", "wall_s", 1)):
                p, lo, hi = boot([r[key] for r in a], [r[key] for r in d])
                out.append(f"| {name} | {fmt(mean([r[key] for r in d]), dec)} | "
                           f"{fmt(mean([r[key] for r in a]), dec)} | {p:+.2f} | "
                           f"[{lo:+.2f}, {hi:+.2f}] |")
            for name, num, den in (("accuracy (all)", "hits", "total"),
                                   ("accuracy GRAND half", "hits_grand", "total_grand"),
                                   ("accuracy DIRECT half", "hits_direct", "total_direct")):
                av = [r[num] / r[den] for r in a]
                dv = [r[num] / r[den] for r in d]
                p, lo, hi = boot(av, dv)
                out.append(f"| {name} | {fmt(100 * mean(dv), 0)}% | {fmt(100 * mean(av), 0)}% | "
                           f"{100 * p:+.0f} pts | [{100 * lo:+.0f}, {100 * hi:+.0f}] |")

    out.append("\n### Per task (rounds / accuracy)\n")
    tasks = sorted({r["task"] for r in rows})
    out.append("| arm | " + " | ".join(tasks) + " |")
    out.append("|---|" + "---|" * len(tasks))
    for arm in ARM_ORDER:
        if arm not in by:
            continue
        cells = []
        for t in tasks:
            g = [r for r in by[arm] if r["task"] == t]
            cells.append(f"{fmt(mean([r['rounds'] for r in g]), 1)} / "
                         f"{fmt(100 * mean([r['hits'] / r['total'] for r in g]), 0)}%" if g else "-")
        out.append(f"| {ARM_LABEL[arm]} | " + " | ".join(cells) + " |")

    ref = [r for r in rows if r.get("refetch_after_injection") is not None]
    if ref:
        by_arm_ref = defaultdict(list)
        for r in ref:
            by_arm_ref[r["arm"]].append(1.0 if r["refetch_after_injection"] else 0.0)
        bits = ", ".join(f"{ARM_LABEL[a]} {100 * mean(v):.0f}%" for a, v in by_arm_ref.items())
        out.append(f"\nRe-fetch rate despite being handed the content: {bits}. "
                   "A round the agent insists on spending is not one a cache can remove.")
    return "\n".join(out)


# ----------------------------------------------------------------------------- stage 2
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
    """One row per successor task (a task with at least one blocker) that was claimed."""
    records = load(trace)
    meta = next((r["data"] for r in records if r.get("event") == "profile_meta"), {})
    arm = (meta.get("prewarm") or {}).get("arm", "none")
    label = meta.get("label", trace.stem)
    end_meta = next((r["data"] for r in records if r.get("event") == "profile_end"), {})
    tasks, names, injected = {}, {}, {}
    for r in records:
        d = r.get("data") or {}
        ev, t = r.get("event"), r.get("elapsed_ms", 0.0)
        if ev == "task_create":
            tasks[d["id"]] = {"id": d["id"], "blk": [], "owner": None, "claim": None,
                              "done": None, "subject": d.get("subject", "")}
        elif ev == "task_update" and d.get("blocked_by_task_ids") is not None:
            if d.get("task_id") in tasks:
                tasks[d["task_id"]]["blk"] = list(d["blocked_by_task_ids"])
        elif ev == "task_claim" and d.get("task_id") in tasks:
            tasks[d["task_id"]].update(owner=d.get("owner"), claim=t)
        elif ev == "task_complete" and d.get("task_id") in tasks:
            tasks[d["task_id"]]["done"] = t
        elif ev == "agent_create":
            names[d.get("name")] = r.get("agent_id")
        elif ev == "context_injection":
            injected[d.get("task_id")] = d
    end = max((r.get("elapsed_ms", 0.0) for r in records), default=0.0)

    out = []
    for task in tasks.values():
        if not task["blk"] or task["claim"] is None:
            continue
        agent = names.get(task["owner"])
        if agent is None:
            continue
        stop = task["done"] if task["done"] is not None else end
        rounds = reads = locates = 0
        model_ms = tool_ms = 0.0
        in_tok = out_tok = cached = 0
        first_prompt = None
        spans = {}
        for r in records:
            if r.get("agent_id") != agent:
                continue
            t = r.get("elapsed_ms", 0.0)
            if not (task["claim"] <= t <= stop):
                continue
            d = r.get("data") or {}
            ev = r.get("event")
            if ev == "model_request":
                rounds += 1
            elif ev == "model_response":
                u = d.get("usage") or {}
                model_ms += d.get("duration_ms", 0.0) or 0.0
                tok = u.get("input_tokens") or 0
                if first_prompt is None:
                    first_prompt = tok
                in_tok += tok
                out_tok += u.get("output_tokens") or 0
                cached += u.get("cache_read_input_tokens") or 0
            elif ev == "tool_start":
                spans[r.get("span_id")] = t
                tool = d.get("tool")
                args = d.get("arguments") or {}
                if tool == "read_file":
                    reads += 1
                elif tool == "bash" and classify_bash(args.get("command", ""))[0]:
                    reads += 1
                elif tool == "glob":
                    locates += 1
            elif ev == "tool_end":
                t0 = spans.pop(r.get("span_id"), None)
                if t0 is not None:
                    tool_ms += t - t0
        inj = injected.get(task["id"]) or {}
        pred_owners = {tasks[b]["owner"] for b in task["blk"] if b in tasks}
        span_ms = stop - task["claim"]
        out.append({
            "run": label, "arm": arm, "task": task["id"], "subject": task["subject"][:36],
            "owner": task["owner"], "rounds": rounds, "reads": reads, "locates": locates,
            "span_s": round(span_ms / 1000.0, 1),
            "model_s": round(model_ms / 1000.0, 1),
            "tool_s": round(tool_ms / 1000.0, 2),
            "other_s": round((span_ms - model_ms - tool_ms) / 1000.0, 1),
            "in_tok": in_tok, "out_tok": out_tok, "cached": cached,
            "first_prompt_tok": first_prompt or 0,
            "injected_chars": inj.get("chars", 0), "injected_pairs": inj.get("pairs", 0),
            "n_sources": len(inj.get("sources") or []),
            "n_ancestors": len(inj.get("ancestors") or []),
            "skipped_resident": inj.get("skipped_resident", 0),
            "cross_owner": 1 if pred_owners - {task["owner"], None} else 0,
            "completed": 1 if task["done"] is not None else 0,
            "run_wall_s": end_meta.get("wall_seconds"),
        })
    return out


def stage2(trace_dir: Path) -> str:
    traces = sorted(p for p in trace_dir.rglob("run_*.jsonl") if p.name.count(".") == 1)
    rows = [r for t in traces for r in stage2_rows(t)]
    cells = {}
    for p in trace_dir.rglob("*.cell.json"):
        try:
            c = json.loads(p.read_text())
            cells[c["label"]] = c
        except Exception:
            continue
    if not rows:
        return "_stage 2: no successor tasks found_"
    by = defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    out = ["## Stage 2 — the real s15 team harness (lead + 3 teammates), successor tasks only\n",
           "| arm | successors | cross-owner | rounds | reads | span s | model s | tool s | other s "
           "| prompt tok | injected tok | **inherited share of all prompt tokens** | completed |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARM_ORDER:
        g = by.get(arm)
        if not g:
            continue
        # the harness records system_chars/messages_chars only in the sidecar; here the honest
        # denominator is all prompt tokens the successor processed, cached included
        ratio = mean([(r["injected_chars"] / CHARS_PER_TOKEN * max(r["rounds"], 1))
                      / max(r["in_tok"] + r["cached"], 1) for r in g])
        out.append(
            f"| {ARM_LABEL[arm]} | {len(g)} | {sum(r['cross_owner'] for r in g)} | "
            f"{fmt(mean([r['rounds'] for r in g]))} | {fmt(mean([r['reads'] for r in g]))} | "
            f"{fmt(mean([r['span_s'] for r in g]), 1)} | {fmt(mean([r['model_s'] for r in g]), 1)} | "
            f"{fmt(mean([r['tool_s'] for r in g]), 2)} | {fmt(mean([r['other_s'] for r in g]), 1)} | "
            f"{fmt(mean([r['in_tok'] for r in g]), 0)} | "
            f"{fmt(mean([r['injected_chars'] for r in g]) / CHARS_PER_TOKEN, 0)} | "
            f"**{fmt(100 * ratio, 1) if ratio is not None else '-'}%** | "
            f"{fmt(100 * mean([float(r['completed']) for r in g]), 0)}% |")

    base = by.get("none", [])
    if base:
        out.append("\n### Contrasts against baseline, with the latency priced\n")
        out.append("| arm | Δ rounds | 95% CI | Δ span s | Δ model s | rounds×3.72 s | "
                   "injected prefill s | net s |")
        out.append("|---|---|---|---|---|---|---|---|")
        for arm in ("dag", "ancestors"):
            g = by.get(arm)
            if not g:
                continue
            p, lo, hi = boot([r["rounds"] for r in g], [r["rounds"] for r in base])
            dspan = mean([r["span_s"] for r in g]) - mean([r["span_s"] for r in base])
            dmodel = mean([r["model_s"] for r in g]) - mean([r["model_s"] for r in base])
            saved = -p * FIXED_ROUND_S
            paid = mean([r["injected_chars"] for r in g]) / CHARS_PER_TOKEN * MS_PER_UNCACHED_TOKEN / 1000
            out.append(f"| {ARM_LABEL[arm]} | {p:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {dspan:+.1f} | "
                       f"{dmodel:+.1f} | {saved:+.1f} | {paid:.2f} | {saved - paid:+.1f} |")

    if cells:
        # A graded successor that never finished cannot be scored: the team was shut down while it
        # was still working, so a 0 would be a scheduling artefact recorded as an accuracy loss.
        # Those cells are excluded and counted separately.
        done = {(r["run"], r["task"]) for r in rows if r["completed"]}
        out.append("\n### Accuracy of the graded successor report, and run wall\n")
        out.append("| arm | runs | gradeable | accuracy | edges | executed | run wall s | "
                   "successor unfinished |")
        out.append("|---|---|---|---|---|---|---|---|")
        bya = defaultdict(list)
        for c in cells.values():
            bya[c.get("arm", "?")].append(c)
        for arm in ARM_ORDER:
            g = bya.get(arm)
            if not g:
                continue
            ok = [c for c in g if (c["label"].rsplit("-", 0)[0], c.get("successor")) in done
                  or any(r["run"] == c["label"] and r["task"] == c.get("successor") and r["completed"]
                         for r in rows)]
            unfinished = len(g) - len(ok)
            acc = mean([c["hits"] / c["total"] for c in ok if c.get("total")])
            out.append(f"| {ARM_LABEL[arm]} | {len(g)} | {len(ok)} | "
                       f"{fmt(100 * acc, 0) if acc is not None else '-'}% | "
                       f"{fmt(mean([c['edges'] for c in g]), 1)} | "
                       f"{fmt(mean([c['executed_edges'] for c in g]), 1)} | "
                       f"{fmt(mean([c['wall_seconds'] for c in g]), 0)} | {unfinished} |")
    return "\n".join(out)


# ----------------------------------------------------------------------------- byte recall
def byte_recall(trace_dir: Path, repo: Path) -> str:
    """Metric 1, measured on the BASELINE runs: of the bytes a successor went on to read, what share
    would each policy have handed it?  It has to be measured on the untreated arm -- in a treated run
    the successor does not read, which is the point."""
    try:
        from dag_redundancy import DagRun, collect
    except Exception as exc:                                        # noqa: BLE001
        return f"_byte recall unavailable: {exc}_"
    rows = []
    exclude = re.compile(r"traces/|\.task_outputs")
    for path in collect([str(trace_dir)]):
        try:
            run = DagRun(path, repo, None, exclude, True, "range")
        except Exception:
            continue
        # labels are "<WORKLOAD>-<arm>-<rep>" and workload names contain hyphens (FAB-DEEP), so the
        # arm cannot be taken positionally
        if "-none-" not in (run.red.label or ""):
            continue                                                # baseline arm only
        rows.extend(run.policy_rows())
    rows = [r for r in rows if r.get("has_pred")]
    if not rows:
        return "_byte recall: no baseline successor with reads yet_"
    out = ["## Metric 1 — byte-level recall, measured on the baseline arm\n",
           "For every successor that actually read something, the share of ITS read bytes that each "
           "policy would already have held, and the share of its read-only rounds that become "
           "removable.\n",
           "| policy | successors | byte recall | round recall | precision (used/sent) | "
           "sent KB median |",
           "|---|---|---|---|---|---|"]
    for policy, label in (("direct_pred", "direct predecessors (`dag` arm)"),
                          ("ancestors", "transitive closure (`ancestors` arm)"),
                          ("all_earlier", "every earlier task (upper reference)"),
                          ("oracle", "oracle (ceiling)")):
        g = [r for r in rows if r["policy"] == policy]
        if not g:
            continue
        cov = sum(r["covered"] for r in g)
        tot = sum(r["total"] for r in g)
        rem = sum(r["removable"] for r in g)
        nr = sum(r["rounds"] for r in g)
        sent = sum(r["sent_bytes"] for r in g)
        used = sum(r["used_bytes"] for r in g)
        sizes = sorted(r["sent_bytes"] for r in g)
        out.append(f"| {label} | {len(g)} | {100 * cov / tot if tot else 0:.1f}% | "
                   f"{rem}/{nr} ({100 * rem / nr if nr else 0:.1f}%) | "
                   f"{100 * used / sent if sent else 0:.1f}% | "
                   f"{sizes[len(sizes) // 2] / 1024:.1f} |")
    return "\n".join(out)


# ----------------------------------------------------------------------------- F1 coverage curve
def coverage_curve(path: Path) -> str:
    """Rounds against k = documents supplied, and the step-vs-slope fit."""
    try:
        from latency_breakdown import fit3
    except Exception:
        fit3 = None
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("stopped") in ("answered", "max_rounds")]
    if not rows:
        return "_F1: no completed trials_"
    by = defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    order = ["k0", "k1", "k2", "k3", "k4", "k4trunc"]
    lab = {"k0": "k=0 (nothing)", "k1": "k=1", "k2": "k=2", "k3": "k=3",
           "k4": "k=4 (complete)", "k4trunc": "k=4 but each truncated ~50%"}
    out = ["## F1 — rounds against number of documents supplied (N=4)\n",
           "| arm | n | rounds | sd | read_file | locate | wall s | injected tok | accuracy | "
           "facts whose doc WAS supplied | facts whose doc was MISSING |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in order:
        g = by.get(arm)
        if not g:
            continue
        sup = [r["hits_supplied"] / r["total_supplied"] for r in g if r["total_supplied"]]
        mis = [r["hits_missing"] / r["total_missing"] for r in g if r["total_missing"]]
        out.append(
            f"| {lab[arm]} | {len(g)} | {fmt(mean([r['rounds'] for r in g]))} | "
            f"{fmt(sd([r['rounds'] for r in g]))} | {fmt(mean([r['n_read_file'] for r in g]))} | "
            f"{fmt(mean([r['locate_calls'] for r in g]))} | "
            f"{fmt(mean([r['wall_s'] for r in g]), 1)} | "
            f"{fmt(mean([r['injected_chars'] for r in g]) / CHARS_PER_TOKEN, 0)} | "
            f"{fmt(100 * mean([r['hits'] / r['total'] for r in g]), 0)}% | "
            f"{fmt(100 * mean(sup), 0) + '%' if sup else '-'} | "
            f"{fmt(100 * mean(mis), 0) + '%' if mis else '-'} |")

    # --- step or slope?  rounds = c + a*1[anything missing] + b*(items missing)
    breadth = [r for r in rows if r["arm"] in ("k0", "k1", "k2", "k3", "k4")]
    # fit3 takes rows of (x1, x2, y) and returns ((intercept, b1, b2), r2) or None
    fitted = None
    if fit3 is not None and len(breadth) >= 6:
        fitted = fit3([(1.0 if r["k"] < 4 else 0.0, float(4 - r["k"]), float(r["rounds"]))
                       for r in breadth])
    if fitted:
        (c, a, b), r2 = fitted
        out.append("\n### Step or slope?\n")
        out.append("Fit `rounds = c + a x 1[anything missing] + b x (documents missing)` over the "
                   "breadth arms:\n")
        out.append("| c (floor) | a (cost of anything missing) | b (cost per missing document) | r2 |")
        out.append("|---|---|---|---|")
        out.append(f"| {c:.2f} | **{a:.2f}** | **{b:.2f}** | {r2:.2f} |")
        verdict = ("a STEP — partial coverage buys almost nothing, so a retention budget should be "
                   "concentrated on covering some successors completely"
                   if a > 2 * b else
                   "a SLOPE — every document pays about the same, so a retention budget should be "
                   "spread" if b > 2 * a else
                   "mixed — both terms matter and neither dominates")
        ratio = f"{a / b:.1f}" if abs(b) > 1e-9 else "infinite"
        out.append(f"\nRatio a/b = **{ratio}**. Reading: {verdict}.")

    # --- the pre-registered convexity test
    means = {k: mean([r["rounds"] for r in by[k]]) for k in ("k0", "k1", "k2", "k3", "k4")
             if by.get(k)}
    if len(means) == 5:
        last = means["k3"] - means["k4"]
        early = (means["k0"] - means["k3"]) / 3.0
        pv = None
        if by.get("k3") and by.get("k4"):
            pv, lo, hi = boot([r["rounds"] for r in by["k4"]], [r["rounds"] for r in by["k3"]])
        out.append("\n### Pre-registered convexity test\n")
        out.append(f"- drop from k=3 to k=4 (the last document): **{last:.2f} rounds**")
        out.append(f"- mean drop per document over k=0 to k=3: **{early:.2f} rounds**")
        out.append(f"- ratio: **{last / early:.1f}x**" if early else "- ratio: undefined")
        if pv is not None:
            out.append(f"- k4 vs k3 difference {pv:+.2f} rounds, 95% CI [{lo:+.2f}, {hi:+.2f}]")
        out.append("\nThe prediction was that the last document is worth more than the average of "
                   "the earlier ones. A ratio near 1 falsifies the step story and flips the advice "
                   "to spreading the budget.")

    # --- does a partly-present document count as present?
    if by.get("k4trunc") and by.get("k4") and by.get("k3"):
        t = mean([r["rounds"] for r in by["k4trunc"]])
        out.append("\n### Is a partially present document present?\n")
        out.append(f"| k=4 complete | k=4 truncated ~50% | k=3 (one document absent) |")
        out.append("|---|---|---|")
        out.append(f"| {means.get('k4', 0):.2f} | **{t:.2f}** | {means.get('k3', 0):.2f} |")
        near = "complete" if abs(t - means["k4"]) < abs(t - means["k3"]) else "absent"
        out.append(f"\nTruncated-but-contiguous lands nearer **{near}**. If it behaves like absent, "
                   "the step is over completeness of each span, not merely over document count, and "
                   "an engine must keep whole spans rather than trim them.")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stage1", default=None)
    p.add_argument("--coverage", default=None, help="F1 coverage.jsonl")
    p.add_argument("--stage2", default=None)
    p.add_argument("--repo", default=str(REPO))
    p.add_argument("--md", action="store_true")
    p.add_argument("--json", default=None)
    args = p.parse_args()
    blocks = []
    if args.coverage and Path(args.coverage).exists():
        blocks.append(coverage_curve(Path(args.coverage)))
    if args.stage2 and Path(args.stage2).exists():
        blocks.append(byte_recall(Path(args.stage2), Path(args.repo)))
    if args.stage1 and Path(args.stage1).exists():
        blocks.append(stage1(Path(args.stage1)))
    if args.stage2 and Path(args.stage2).exists():
        blocks.append(stage2(Path(args.stage2)))
    print("\n\n".join(blocks) if blocks else "nothing to analyse")
    if args.json and args.stage2:
        Path(args.json).write_text(json.dumps(
            {"stage2": [r for t in sorted(Path(args.stage2).rglob("run_*.jsonl"))
                        if t.name.count(".") == 1 for r in stage2_rows(t)]}, indent=2),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
