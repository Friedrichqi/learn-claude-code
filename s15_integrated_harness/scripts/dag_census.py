#!/usr/bin/env python3
"""Analyse the cross-category task-DAG census.

    python3 s15_integrated_harness/scripts/dag_census.py <trace|dir> [<trace|dir> ...]
        [--json OUT] [--per-run] [--quiet]

Primary question: does the Lead put dependency edges on the task board?  Primary measures are
`tasks`, `edges`, `update_task` calls and -- for the DEP workloads, whose correct board is known --
`edge_recall` against the hand-authored reference graph in census_workloads.WORKLOADS.

The secondary measures are the point of the experiment when the answer is "no edges".  An empty
graph does not mean the ordering vanished; it means the ordering is carried somewhere else, and
where it is carried decides whether a scheduler could still recover it:

  prose        the order is written into the task subject/description ("using the inventory
               produced by the previous item").  Recoverable by reading text, not the graph.
  staged       the Lead withholds later tasks and creates them only once earlier ones finish.
               Recoverable from creation timestamps; invisible to a board snapshot.
  spawn gating the Lead delays spawning the agent that will do the later work.
  lead-side    the Lead does the dependent stage itself instead of boarding it at all.

and the cost of the empty graph:

  order violations  a task whose true predecessor had not completed was claimed anyway.

Run it against traces/dag_redundancy (known non-empty boards) and traces/latency_profiling (known
empty ones) to confirm the detector reports edges when they exist and none when they do not.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from trace_task_dag import parse_tasks

try:
    from census_workloads import WORKLOADS
except Exception:                                    # analysing foreign corpora is still allowed
    WORKLOADS = {}

LABEL_RE = re.compile(r"^(?P<cat>[A-Z]+)-(?P<struct>DEP|FLAT)-(?P<prov>[^-]+)-(?P<rep>r\d+)$")

# ordering written into the task text rather than onto the board
# Ordering written into the task text rather than onto the board.  PROSE_RE is the broad form (the
# task refers to another item's output at all); EXPLICIT_RE is the strong form, where the Lead
# states the constraint in words -- the exact claim `blockedBy` exists to carry.
PROSE_RE = re.compile(
    r"(depends? on|blocked by|waits? for|after item|once item|requires item|"
    r"using the (?:\w+\s+){0,4}(?:results?|output|outputs|findings|inventory|checklist|quotes?|"
    r"list|lists|table|totals?|surveys?|definitions?|locations?|implementation|renamed|sum|count|"
    r"counts|note|notes|answer|answers|ranking|solution)|"
    r"produced by|written by|created by|returned by|"
    r"(?:from|in) the previous|previous item|previous stage|previous three items|prior item|"
    r"\bitem \d|\bstep \d|\bstage \d|based on the)", re.IGNORECASE)

EXPLICIT_RE = re.compile(r"(depends? on|blocked by|waits? for|must (?:not )?(?:begin|start|run)|"
                         r"only after|requires item)", re.IGNORECASE)

STOP = {"the", "and", "for", "with", "that", "each", "every", "from", "into", "its", "run",
        "list", "line", "lines", "file", "files", "item", "items", "one", "three", "all", "any",
        "this", "what", "which", "where", "with", "your", "you", "are", "was", "has", "have"}
NONCE_RE = re.compile(r"\[run [0-9a-f]{4,10}\]", re.IGNORECASE)


def toks(text: str) -> set[str]:
    text = NONCE_RE.sub(" ", text or "").lower()
    return {t for t in re.split(r"[^a-z0-9_]+", text) if len(t) >= 3 and t not in STOP}


def load_records(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return out


def match_tasks_to_items(tasks: dict, items: list[str]) -> tuple[dict[int, str], float]:
    """Greedy one-to-one match of board tasks to workload items by token overlap.

    Returns {item_index: task_id} and the mean overlap of the accepted matches, so a zero
    edge_recall can be told apart from a bad match."""
    item_toks = [toks(t) for t in items]
    cand = []
    for idx, it in enumerate(item_toks):
        for tid, t in tasks.items():
            tt = toks((t.get("subject") or "") + " " + (t.get("desc") or ""))
            if not it or not tt:
                continue
            cand.append((len(it & tt) / len(it), idx, tid))
    cand.sort(reverse=True)
    used_i: set[int] = set()
    used_t: set[str] = set()
    match: dict[int, str] = {}
    scores = []
    for score, idx, tid in cand:
        if idx in used_i or tid in used_t or score <= 0:
            continue
        match[idx] = tid
        used_i.add(idx)
        used_t.add(tid)
        scores.append(score)
    return match, (statistics.mean(scores) if scores else 0.0)


def analyse(path: Path) -> dict:
    try:
        parsed = parse_tasks(path)
    except SystemExit:
        return {}
    tasks = parsed["tasks"]
    records = load_records(path)

    label = next((r["data"].get("label") for r in records if r.get("event") == "profile_meta"), None)
    model = next((r["data"].get("model") for r in records if r.get("event") == "run_start"), None)
    end = next((r["data"] for r in records if r.get("event") == "profile_end"), {})
    m = LABEL_RE.match(label or "")
    cat = m.group("cat") if m else None
    struct = m.group("struct") if m else None
    w = WORKLOADS.get((cat, struct)) if cat else None

    ids = set(tasks)
    emitted = {(t["id"], b) for t in tasks.values() for b in t["deps"]}     # (successor, predecessor)
    updates = sum(1 for r in records
                  if r.get("event") == "task_update" and (r.get("data") or {}).get("blocked_by_task_ids"))

    # -- primary -------------------------------------------------------------------------------
    row = {
        "trace": path.name, "label": label, "model": model, "category": cat, "structure": struct,
        "shape": (w or {}).get("shape"), "tasks": len(tasks), "edges": len(emitted),
        "has_edge": bool(emitted), "update_task_calls": updates,
        "blocked_tasks": sum(1 for t in tasks.values() if t["deps"]),
        "status": end.get("status"), "wall_seconds": end.get("wall_seconds"),
    }

    # -- reference graph, where we know it ------------------------------------------------------
    ref_pairs: set[tuple[str, str]] = set()
    if w:
        match, score = match_tasks_to_items(tasks, w["items"])
        row["match_score"] = round(score, 3)
        row["matched_items"] = len(match)
        row["reference_edges"] = len(w["edges"])
        for succ_i, pred_i in w["edges"]:
            if succ_i in match and pred_i in match:
                ref_pairs.add((match[succ_i], match[pred_i]))
        row["reference_edges_resolvable"] = len(ref_pairs)
        row["edge_recall"] = (round(len(emitted & ref_pairs) / len(ref_pairs), 3)
                              if ref_pairs else None)
        row["false_edges"] = len(emitted - ref_pairs)

        # cost of the empty graph: a task started before its true predecessor finished
        viol = 0
        for succ, pred in ref_pairs:
            s, p = tasks[succ], tasks[pred]
            if s["claim_s"] is None:
                continue
            if p["done_s"] is None or s["claim_s"] < p["done_s"]:
                viol += 1
        row["order_violations"] = viol
        row["order_violation_rate"] = round(viol / len(ref_pairs), 3) if ref_pairs else None

    # -- where the ordering went ---------------------------------------------------------------
    texts = [(t.get("subject") or "") + " " + (t.get("desc") or "") for t in tasks.values()]
    prose = sum(1 for tx in texts if PROSE_RE.search(tx))
    explicit = sum(1 for tx in texts if EXPLICIT_RE.search(tx))
    row["prose_ordered_tasks"] = prose
    row["prose_share"] = round(prose / len(tasks), 3) if tasks else None
    row["explicit_dep_tasks"] = explicit
    row["explicit_dep_share"] = round(explicit / len(tasks), 3) if tasks else None

    created = sorted(t["created_s"] for t in tasks.values())
    first_done = min((t["done_s"] for t in tasks.values() if t["done_s"] is not None), default=None)
    row["tasks_created_after_first_completion"] = (
        sum(1 for c in created if first_done is not None and c > first_done))
    waves = 1
    for a, b in zip(created, created[1:]):
        if b - a > 5000:                      # 5 s quiet gap starts a new creation wave
            waves += 1
    row["creation_waves"] = waves if tasks else 0
    row["creation_span_s"] = round((created[-1] - created[0]) / 1000.0, 1) if len(created) > 1 else 0.0

    spawns = [r["elapsed_ms"] for r in records if r.get("event") == "agent_create"]
    row["spawns"] = len(spawns)
    row["spawns_after_first_completion"] = (
        sum(1 for s in spawns if first_done is not None and s > first_done))

    lead_calls_after = sum(
        1 for r in records
        if r.get("event") == "tool_start" and r.get("agent_kind") == "lead"
        and first_done is not None and r["elapsed_ms"] > first_done
        and (r.get("data") or {}).get("tool") in {"read_file", "bash", "glob"})
    row["lead_work_calls_after_first_completion"] = lead_calls_after
    row["rounds"] = sum(1 for r in records if r.get("event") == "model_request")
    row["model_responses"] = sum(1 for r in records if r.get("event") == "model_response")
    # a run the provider refused (402/429/5xx) has no board because it never ran, which is a
    # different fact from a Lead that boarded nothing.  Never let the two pool.
    row["provider_failed"] = bool(not tasks and row["model_responses"] < 2)
    return row


def collect(paths: list[str]) -> list[dict]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files += [f for f in sorted(p.rglob("run_*.jsonl")) if f.name.count(".") == 1]
        elif p.is_file():
            files.append(p)
    rows = [analyse(f) for f in files]
    return [r for r in rows if r]


def pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.0f}%" if d else "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", default=None)
    ap.add_argument("--per-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rows = collect(args.paths)
    if not rows:
        print("no parseable runs", file=sys.stderr)
        return 1
    dead = [r for r in rows if r.get("provider_failed")]
    rows = [r for r in rows if not r.get("provider_failed")]
    if dead:
        print(f"\n> Excluded {len(dead)} run(s) the provider refused before any work began "
              f"(no board, <2 model responses): {', '.join(sorted(d['label'] or d['trace'] for d in dead))}. "
              "These are not evidence about board shape.\n")
    if not rows:
        print("every run was refused by the provider", file=sys.stderr)
        return 1
    boarded = [r for r in rows if r["tasks"]]

    print(f"\n**Table A. Corpus.** {len(rows)} runs parsed, {len(boarded)} created at least one task.\n")
    print("| runs | runs with a board | tasks | edges | runs with >=1 edge | update_task calls |")
    print("|---:|---:|---:|---:|---:|---:|")
    print(f"| {len(rows)} | {len(boarded)} | {sum(r['tasks'] for r in rows)} | "
          f"{sum(r['edges'] for r in rows)} | {sum(1 for r in rows if r['has_edge'])} | "
          f"{sum(r['update_task_calls'] for r in rows)} |")

    keyed = [r for r in boarded if r["category"]]
    if keyed:
        print("\n**Table B. Board shape by category and latent structure.** "
              "`ref` is the number of edges a correct board would carry.\n")
        print("| category | structure | runs | tasks (mean) | edges | ref | runs with >=1 edge | "
              "edge recall | match |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        groups: dict[tuple, list] = defaultdict(list)
        for r in keyed:
            groups[(r["category"], r["structure"])].append(r)
        for k in sorted(groups):
            g = groups[k]
            rec = [r["edge_recall"] for r in g if r.get("edge_recall") is not None]
            ms = [r["match_score"] for r in g if r.get("match_score") is not None]
            print(f"| {k[0]} | {k[1]} | {len(g)} | "
                  f"{statistics.mean([r['tasks'] for r in g]):.1f} | "
                  f"{sum(r['edges'] for r in g)} | {g[0].get('reference_edges', 0)} | "
                  f"{sum(1 for r in g if r['has_edge'])} | "
                  f"{(f'{statistics.mean(rec):.2f}' if rec else '-')} | "
                  f"{(f'{statistics.mean(ms):.2f}' if ms else '-')} |")

        print("\n**Table C. Where the ordering went instead.** Shares are of the runs in the cell.\n")
        print("| category | structure | prose-ordered | says so in words | creation waves | "
              "created after a completion | spawns after a completion | lead calls after a "
              "completion | order violations |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for k in sorted(groups):
            g = groups[k]
            viol = [r["order_violation_rate"] for r in g if r.get("order_violation_rate") is not None]
            print(f"| {k[0]} | {k[1]} | "
                  f"{statistics.mean([r['prose_share'] or 0 for r in g]):.2f} | "
                  f"{statistics.mean([r['explicit_dep_share'] or 0 for r in g]):.2f} | "
                  f"{statistics.mean([r['creation_waves'] for r in g]):.1f} | "
                  f"{statistics.mean([r['tasks_created_after_first_completion'] for r in g]):.1f} | "
                  f"{statistics.mean([r['spawns_after_first_completion'] for r in g]):.1f} | "
                  f"{statistics.mean([r['lead_work_calls_after_first_completion'] for r in g]):.1f} | "
                  f"{(f'{statistics.mean(viol):.2f}' if viol else '-')} |")

        bymodel: dict[str, list] = defaultdict(list)
        for r in keyed:
            bymodel[r["model"] or "?"].append(r)
        if len(bymodel) > 1:
            print("\n**Table D. By model.** DEP rows only -- the cells where a correct board has edges.\n")
            print("| model | DEP runs | tasks | edges | runs with >=1 edge | edge recall | wall s (mean) |")
            print("|---|---:|---:|---:|---:|---:|---:|")
            for mk in sorted(bymodel):
                g = [r for r in bymodel[mk] if r["structure"] == "DEP"]
                if not g:
                    continue
                rec = [r["edge_recall"] for r in g if r.get("edge_recall") is not None]
                wall = [r["wall_seconds"] for r in g if r.get("wall_seconds")]
                print(f"| {mk} | {len(g)} | {sum(r['tasks'] for r in g)} | "
                      f"{sum(r['edges'] for r in g)} | {sum(1 for r in g if r['has_edge'])} | "
                      f"{(f'{statistics.mean(rec):.2f}' if rec else '-')} | "
                      f"{(f'{statistics.mean(wall):.0f}' if wall else '-')} |")

    print("\n**Table V. Validity (read this before any claim).**\n")
    print("| check | value | why it matters |")
    print("|---|---:|---|")
    print(f"| V0 runs refused by the provider (excluded above) | {len(dead)} | "
          "a provider error is not a measurement; pooling them would fake an empty-board result |")
    print(f"| V1 runs with no board at all | {len(rows) - len(boarded)} | "
          "a run with no tasks is a different finding from a run with tasks and no edges |")
    if keyed:
        low = [r for r in keyed if (r.get("match_score") or 0) < 0.25]
        print(f"| V2 runs whose item match is weak (<0.25) | {len(low)} | "
              "a zero edge_recall from a bad match is an artefact, not a result |")
        unres = [r for r in keyed
                 if r.get("reference_edges") and r.get("reference_edges_resolvable") == 0]
        print(f"| V3 DEP runs whose reference graph could not be resolved | {len(unres)} | "
              "these cannot contribute to edge recall |")
        inc = sum(1 for r in keyed if r["status"] not in ("ok", "completed", None))
        print(f"| V4 runs not finishing cleanly | {inc} | a truncated run may simply not have got "
              "to the later items |")
        flat = [r for r in keyed if r["structure"] == "FLAT"]
        print(f"| V5 FLAT runs emitting an edge | {sum(1 for r in flat if r['has_edge'])} of {len(flat)} | "
              "the specificity control: edges here would mean the Lead relates items indiscriminately |")
    print(f"| V6 update_task calls minus edges | "
          f"{sum(r['update_task_calls'] for r in rows) - sum(r['edges'] for r in rows)} | "
          "one call can carry several blockers (addBlockedBy is a list), so this is normally "
          "negative; a positive value means calls were rejected (cycle, or not pending/unowned) |")

    if args.per_run and not args.quiet:
        print("\n**Table E. Per run.**\n")
        print("| label | model | tasks | edges | ref | recall | prose | waves | viol | rounds | wall s | status |")
        print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for r in sorted(rows, key=lambda x: (x["label"] or x["trace"])):
            print(f"| {r['label'] or r['trace']} | {r['model'] or '-'} | {r['tasks']} | {r['edges']} | "
                  f"{r.get('reference_edges', '-')} | {r.get('edge_recall', '-')} | "
                  f"{r.get('prose_share', '-')} | {r['creation_waves']} | "
                  f"{r.get('order_violations', '-')} | {r['rounds']} | "
                  f"{r.get('wall_seconds') or '-'} | {r['status'] or '-'} |")

    if args.json:
        Path(args.json).write_text(json.dumps({"runs": rows}, indent=2), encoding="utf-8")
        print(f"\n[census] wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
