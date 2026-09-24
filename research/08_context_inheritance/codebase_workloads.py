#!/usr/bin/env python3
"""Codebase comprehension and modification as the live workload for the three inheritance arms.

    python3 research/08_context_inheritance/codebase_workloads.py --check
    python3 research/08_context_inheritance/codebase_workloads.py --census --reps 2
    python3 research/08_context_inheritance/codebase_workloads.py --arms none,dag,ancestors --reps 4

WHY THIS REPLACES THE lm-eval WORKLOAD.  The G1 census settled it: over 15 runs on gsm8k,
mmlu_pro_biology, two longproc tasks and longbench2, the lead created **no task board at all** — not
merely no edges.  A benchmark question is one deliverable, however long the document attached to it,
and one deliverable cannot be decomposed.  The arms differ only at depth >= 3, so there was nothing
to measure.  Real project work over a real codebase is several deliverables that genuinely stack,
which is the condition the 2026-09-17 census got edge recall 1.00 under.

THE TWO WORKLOADS ARE A CONTRAST PAIR, NOT TWO SAMPLES.  The replay half of this study found that
inheritance pays off 6x better on tau-bench than on SWE-bench and attributed the gap to MUTATION:
agents edit the files they read, so only 4-6% of repeat reads return the same bytes and 7.6-14.3% of
an inherited set is stale.  That attribution is currently an inference from a difference between two
benchmarks that differ in many other ways too.  Running EXPLAIN (read-only) and MODIFY (edits the
same files) over ONE codebase isolates mutation as the variable.

  MEASURED (6 census runs on lab-group-56): MODIFY reaches depth 3 in 3 of 3 runs, EXPLAIN tops out
  at depth 2 in 3 of 3 -- and not because it was cut off, since the one EXPLAIN run that finished
  all four tasks was also depth 2.  Comprehension work FANS IN (inventory and trace are independent,
  everything after needs both), modification work CHAINS (each edit builds on the last).  At depth 2
  a successor's direct predecessors ARE its ancestors, so EXPLAIN cannot separate the `dag` and
  `ancestors` arms; it remains usable for `none` vs `ancestors` and for the budget and policy sweeps.

  EXPLAIN   read-only comprehension.  Sources are shared across deliverables and never change.
            Structurally tau-bench-shaped: expect high reuse, low stale.
  MODIFY    staged edits in a sandbox copy.  The same files, now moving under the agents' feet.
            Structurally SWE-bench-shaped: expect low reuse, high stale.

WHAT THE PROMPT MAY AND MAY NOT SAY.  It carries the census's own neutral PREAMBLE, which names the
task board without naming its shape -- not how many items, not how they relate.  That framing is
load-bearing and is NOT a fabrication of the graph: without it the lead has no reason to open a board
at all (the lm-eval census is the evidence), and the 2026-09-17 result that the lead emits a complete
graph unprompted was measured under exactly this frame.  What must never appear is dependency
language, and `--check` refuses to run if it does, using that study's own FORBIDDEN regex.

Deliverables are stated in the order the work really has to happen, because that is the signal being
measured -- whether the lead converts an honest prose ordering into `blockedBy` edges.  It is told
nothing about edges, and a run where it decides the work is flat is recorded as depth 0 and kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
from census_workloads import EPILOGUE, FOLLOWUP, FORBIDDEN, NOWRITE, PREAMBLE  # noqa: E402
from inherit_analyze import boot, stage2_rows                                  # noqa: E402
from lmeval_workloads import board_of                                          # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "research" / "common" / "profile_run.py"
TRACE_ROOT = REPO / "research" / "08_context_inheritance" / "data" / "codebase"
SANDBOX = "profiling_sandbox/codebase"

# Deliverables stack: the third needs the first two, the last needs all of them.  Nothing here
# names the board, an edge, or a dependency -- `--check` enforces that with the census's own regex.
#
# The targets are named by ROLE, not by path, so one workload text fits any codebase: the runner
# passes the two subdirectories it found.  That also means the agents have to locate things
# themselves, which is the read behaviour being measured.
EXPLAIN_ITEMS = [
    "Inventory every design module under {a} and under {b}, and for each state in one line what it "
    "provides that the others do not",
    "Trace one request through {a} from its top-level interface down to the arithmetic that "
    "produces the result, naming every file and module on that path",
    "Using the inventory and the trace, list the three components under {lib} that both {a} and "
    "{b} instantiate, and for each give the file and the line of every instantiation",
    "Write an orientation note for someone joining this repository that cites a specific file and "
    "line number from the inventory, from the trace, and from each of the three shared components",
]
EXPLAIN_SYNTHESIS = "assemble the orientation note from the findings that came back"

# DELIBERATELY INERT.  This target is a graded course repository with a second author, so the edits
# are additive provenance stamps that change no behaviour and implement no assignment.  They exist
# only to make agents read many files and then write to them, which is the whole point: the contrast
# with EXPLAIN is mutation, nothing else.  Every run works on a fresh sandbox copy.
MODIFY_ITEMS = [
    "Add a module-level constant named BUILD_TAG, set to the string 'profiling', near the top of "
    "every Python file directly under {a} that is not __init__.py and is not a test",
    "Add that same constant, with the same name and the same value, near the top of every Python "
    "file directly under {b} that is not __init__.py and is not a test",
    "Add one new Python file that imports every module you changed and asserts that BUILD_TAG is "
    "present and identical in all of them",
    "Write a changelog listing every file changed and the line number where the constant was added "
    "in each",
]
MODIFY_SYNTHESIS = "assemble the changelog from the reports that came back"

# Deadlines are measured, not guessed.  The first two runs completed a task every 205 s (EXPLAIN)
# and every 377 s (MODIFY), and both hit a 600 s cap having finished 2 of 4 and 1 of 4 items.  A run
# that is cut off never reaches the final synthesis task -- which is the SUCCESSOR, the one the arms
# exist to accelerate -- so too short a deadline does not add noise, it removes the measurement.
WORKLOADS = {
    # 1200 s truncated 2 of 3 census runs; the completed one took 915 s, so the tail needs headroom
    "EXPLAIN": {"items": EXPLAIN_ITEMS, "synthesis": EXPLAIN_SYNTHESIS, "readonly": True,
                "seconds": 1500},
    "MODIFY": {"items": MODIFY_ITEMS, "synthesis": MODIFY_SYNTHESIS, "readonly": False,
               "seconds": 2100},
}
ARMS = ["none", "dag", "ancestors"]


def roles(target: str) -> dict[str, str]:
    """Pick the two design directories and the shared component library out of the target tree.

    `a` and `b` are the two source directories with the most code, `lib` the one the most other
    directories reference.  Resolving this here rather than hard-coding paths keeps one workload
    text usable on any codebase, and keeps the prompt free of hints about what depends on what."""
    root = REPO / target
    dirs = []
    for d in sorted(p for p in root.rglob("*") if p.is_dir()):
        # test and benchmark directories are not designs: on this target `lab2_proc/test` has 52
        # files and would otherwise outrank every design module by sheer count
        if any(part.startswith(".") or part in {"__pycache__", "test", "tests", "ubmark",
                                                "build", "node_modules"} for part in d.parts):
            continue
        n = sum(1 for f in d.iterdir() if f.is_file() and f.suffix in {".py", ".v", ".c", ".ts"}
                and not f.name.startswith("__"))
        if n:
            dirs.append((n, d))
    if not dirs:
        return {"a": target, "b": target, "lib": target}
    dirs.sort(key=lambda t: -t[0])
    rel = lambda d: str(d.relative_to(REPO))
    # the library is the directory whose files are most often named by files in the others
    names = {d: {f.stem for f in d.iterdir() if f.is_file()} for _, d in dirs}
    def referenced(cand):
        hits = 0
        for _, other in dirs:
            if other == cand:
                continue
            for f in other.rglob("*"):
                if f.is_file() and f.suffix in {".py", ".v"}:
                    try:
                        text = f.read_text(errors="ignore")
                    except OSError:
                        continue
                    hits += sum(1 for n in names[cand] if n and n in text)
        return hits
    lib = max((d for _, d in dirs), key=referenced)
    design = [d for _, d in dirs if d != lib][:2] or [dirs[0][1]]
    a = rel(design[0])
    b = rel(design[1]) if len(design) > 1 else a
    return {"a": a, "b": b, "lib": rel(lib)}


def build_prompt(wid: str, target: str, nonce: str) -> str:
    """One run's user turn.  Items are nonce-stamped so two runs of the same workload cannot serve
    each other's teammate prefix out of the provider's prefix cache."""
    w = WORKLOADS[wid]
    r = roles(target)
    if not w["readonly"]:
        # MODIFY works on the sandbox COPY, and writes anywhere else are denied by the driver.  If
        # the prompt kept pointing at the original the agents would spend every round being refused,
        # and the run would measure the permission gate rather than inheritance.
        r = {k: v.replace(target, SANDBOX, 1) for k, v in r.items()}
    body = " ".join(f"({i + 1}) [run {nonce}] {t.format(**r)}."
                    for i, t in enumerate(w["items"]))
    prompt = body + " " + PREAMBLE + EPILOGUE.format(synthesis=w["synthesis"])
    if w["readonly"]:
        prompt += NOWRITE
    return prompt


def check_prompts(target: str, verbose: bool = True) -> int:
    """Refuse to spend a single call if a prompt names the mechanism being measured."""
    bad = 0
    for wid in WORKLOADS:
        prompt = build_prompt(wid, target, "check")
        hits = sorted({m.group(0).lower() for m in FORBIDDEN.finditer(prompt)})
        if hits:
            bad += 1
            print(f"[codebase] CONTAMINATED {wid}: {hits}", file=sys.stderr)
        elif verbose:
            print(f"[codebase] clean {wid} ({len(prompt)} chars, "
                  f"{len(WORKLOADS[wid]['items'])} items)")
    hits = sorted({m.group(0).lower() for m in FORBIDDEN.finditer(FOLLOWUP)})
    if hits:
        bad += 1
        print(f"[codebase] CONTAMINATED followup: {hits}", file=sys.stderr)
    if bad:
        print(f"[codebase] {bad} contaminated prompt(s): DO NOT RUN", file=sys.stderr)
    return bad


def run_cell(wid: str, target: str, arm: str, evict: str, budget: str, rep: str,
             model: str, max_seconds: int, dry: bool) -> dict | None:
    label = f"{wid}-{arm}-{evict}-{budget}-{rep}"
    out_dir = TRACE_ROOT / wid
    out_dir.mkdir(parents=True, exist_ok=True)
    nonce = hashlib.sha1(label.encode()).hexdigest()[:8]
    prompt = build_prompt(wid, target, nonce)
    prompt_path = out_dir / f"{label}.prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    cmd = [sys.executable, str(DRIVER), "--label", label, "--trace-output", "full",
           "--trace-dir", str(out_dir), "--max-seconds", str(max_seconds),
           "--quiet-seconds", "30", "--model", model,
           "--prompt-file", str(prompt_path),
           "--followup-if-no-team", FOLLOWUP,
           "--prewarm", arm, "--prewarm-evict", evict, "--prewarm-budget", budget,
           "--answer-out", str(out_dir / f"{label}.answer.json"),
           "--prewarm-dump", str(out_dir / f"{label}.prewarm.json")]
    if not WORKLOADS[wid]["readonly"]:
        # the agents edit a COPY.  --sandbox-from wipes the write root and re-seeds it per run, so
        # every repetition starts from the same bytes and one run cannot inherit another's edits.
        cmd += ["--allow-writes", "--write-root", SANDBOX, "--sandbox-from", target,
                "--allow-python"]
    if dry:
        print(f"[dry] {label}\n      {prompt[:150]}...")
        return None

    started = time.time()
    subprocess.run(cmd, cwd=REPO, check=False, capture_output=True, text=True,
                   timeout=max_seconds + 300)
    traces = [t for t in sorted(out_dir.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
              if t.name.count(".") == 1 and t.stat().st_mtime >= started - 5]
    row = {"workload": wid, "target": target, "arm": arm, "evict": evict, "budget": budget,
           "rep": rep, "label": label, "wall_s": round(time.time() - started, 1)}
    answer = out_dir / f"{label}.answer.json"
    if answer.exists():
        row.update(json.loads(answer.read_text()))
    if traces:
        row["trace"] = traces[-1].name
        try:
            row.update(board_of(traces[-1]))
        except Exception as exc:                                                # noqa: BLE001
            row["board_error"] = f"{type(exc).__name__}: {exc}"
    return row


def report(path: Path) -> str:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    # Re-derive the board from the trace rather than trusting what was recorded at run time: the
    # first version of board_of read the wrong key and reported every run as edgeless, which is
    # indistinguishable from a lead that never built a graph.  Recomputing here means a parser fix
    # repairs old rows for free instead of silently leaving them wrong.
    for r in rows:
        t = TRACE_ROOT / r["workload"] / (r.get("trace") or "")
        if r.get("trace") and t.exists():
            try:
                r.update(board_of(t))
            except Exception:                                                   # noqa: BLE001
                pass
    by = defaultdict(list)
    for r in rows:
        by[(r["workload"], r["arm"])].append(r)
    out = ["## Codebase workloads — does the lead build a graph on real project work?\n",
           "Prompts carry the census's neutral board frame and are checked against its FORBIDDEN "
           "regex, so no dependency language reaches the model. Deliverables are stated in the "
           "order the work must happen; whether that becomes `blockedBy` edges is the measurement.\n",
           "| workload | arm | runs | any board | any edge | max depth | depth>=3 | median tasks | "
           "median edges | median wall s |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for (wid, arm), g in sorted(by.items()):
        depths = [r.get("depth") or 0 for r in g]
        tasks = sorted(r.get("tasks") or 0 for r in g)
        edges = sorted(r.get("edges") or 0 for r in g)
        walls = sorted(r.get("wall_s", 0) for r in g)
        out.append(
            f"| {wid} | {arm} | {len(g)} | {sum(1 for r in g if (r.get('tasks') or 0) > 0)} "
            f"| {sum(1 for r in g if (r.get('edges') or 0) > 0)} | {max(depths)} "
            f"| {sum(1 for d in depths if d >= 3)} | {tasks[len(tasks) // 2]} "
            f"| {edges[len(edges) // 2]} | {walls[len(walls) // 2]:.0f} |")
    deep = sum(1 for r in rows if (r.get("depth") or 0) >= 3)
    out.append(f"\n**{deep} of {len(rows)} runs reached depth >= 3**, the only depth at which the "
               "`dag` and `ancestors` arms see different content.")

    # The per-successor metrics the arm comparison will be made of.  A successor that never ran is
    # counted separately rather than folded in: the 2026-09-17 study scored one at zero and turned a
    # scheduling artefact into a 17-point accuracy penalty.
    out += ["\n### Per-successor baseline — what an arm would have to beat\n",
            "| workload | arm | successors | completed | rounds | reads | span s | prompt tok | "
            "cross-owner |", "|---|---|---|---|---|---|---|---|---|"]
    for (wid, arm), g in sorted(by.items()):
        succ = []
        for r in g:
            t = TRACE_ROOT / wid / (r.get("trace") or "")
            if r.get("trace") and t.exists():
                try:
                    succ += stage2_rows(t)
                except Exception:                                               # noqa: BLE001
                    pass
        if not succ:
            continue
        fin = [x for x in succ if x.get("completed")]
        use = fin or succ
        med = lambda k: sorted(x.get(k, 0) for x in use)[len(use) // 2]
        out.append(f"| {wid} | {arm} | {len(succ)} | {len(fin)} | {med('rounds')} | "
                   f"{med('reads')} | {med('span_s'):.0f} | {med('in_tok'):,} | "
                   f"{sum(1 for x in use if x.get('cross_owner'))}/{len(use)} |")
    out.append("\nMedians over completed successors. `cross-owner` counts successors picked up by an "
               "agent that did not do their predecessor — the population inheritance has to serve, "
               "since a same-agent successor already holds the bytes in its own history.")

    # Contrasts against the baseline arm, bootstrapped.  Same estimator, seed and shape as
    # inherit_analyze.boot, so a codebase number can sit beside a 2026-09-17 one without being a
    # different statistic wearing the same name.
    succ_by = defaultdict(list)
    for (wid, arm), g in by.items():
        for r in g:
            t = TRACE_ROOT / wid / (r.get("trace") or "")
            if r.get("trace") and t.exists():
                try:
                    succ_by[(wid, arm)] += [x for x in stage2_rows(t) if x.get("completed")]
                except Exception:                                               # noqa: BLE001
                    pass
    contrasts = []
    for wid in sorted({w for w, _ in succ_by}):
        base = succ_by.get((wid, "none")) or []
        if not base:
            continue
        for arm in ("dag", "ancestors"):
            g = succ_by.get((wid, arm)) or []
            if not g:
                continue
            for metric, label in (("rounds", "rounds"), ("span_s", "span s"),
                                  ("in_tok", "prompt tok")):
                pt, lo, hi = boot([x.get(metric, 0) for x in g],
                                  [x.get(metric, 0) for x in base])
                excl = "yes" if (lo > 0 or hi < 0) else "no"
                contrasts.append(f"| {wid} | {arm} | {label} | {pt:+.1f} | "
                                 f"[{lo:+.1f}, {hi:+.1f}] | {excl} |")
    if contrasts:
        out += ["\n### Contrasts against the baseline arm (bootstrap, 4000 resamples, seed 20260918)\n",
                "| workload | arm | metric | delta | 95% CI | CI excludes 0 |",
                "|---|---|---|---|---|---|", *contrasts,
                "\nNegative is better for rounds, span and prompt tokens. An interval straddling "
                "zero is not evidence of no effect at this sample size — the 2026-09-17 harness "
                "stage needed roughly 4x these runs to resolve a one-round difference."]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="agents",
                    help="repo-relative codebase the workloads read and modify")
    ap.add_argument("--only", default="", help="EXPLAIN and/or MODIFY, comma separated")
    ap.add_argument("--census", action="store_true", help="baseline arm only")
    ap.add_argument("--arms", default="none")
    ap.add_argument("--evict", default="lru")
    ap.add_argument("--budgets", default="unlimited")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default="glm-5.3-flash")
    ap.add_argument("--max-seconds", type=int, default=0,
                    help="override the per-workload deadline; 0 uses WORKLOADS[wid]['seconds']")
    ap.add_argument("--out", type=Path, default=TRACE_ROOT / "runs.jsonl")
    ap.add_argument("--check", action="store_true", help="contamination gate only")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.report:
        print(report(args.out))
        return 0
    if check_prompts(args.target):
        return 2
    if args.check:
        return 0
    src, dst = (REPO / args.target).resolve(), (REPO / SANDBOX).resolve()
    if src == dst or dst.is_relative_to(src) or src.is_relative_to(dst):
        print(f"[codebase] refusing: sandbox {dst} overlaps the source {src}. profile_run wipes the "
              "write root with rmtree before copying into it, so an overlap would delete the "
              "target.", file=sys.stderr)
        return 2
    if not (REPO / args.target).is_dir():
        print(f"[codebase] --target {args.target} is not a directory under {REPO}", file=sys.stderr)
        return 2

    wids = [w for w in args.only.split(",") if w] or list(WORKLOADS)
    arms = ["none"] if args.census else [a for a in args.arms.split(",") if a]
    cells = [(w, a, b, f"r{i + 1}")
             for w in wids for a in arms for b in args.budgets.split(",")
             for i in range(args.reps)]
    # interleave arms within a repetition and shuffle on a fixed seed: provider drift over a long
    # run must not be able to line up with an arm
    random.Random(20260918).shuffle(cells)
    print(f"[codebase] {len(cells)} cells, target={args.target}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if args.out.exists():
        done = {json.loads(l)["label"] for l in args.out.read_text().splitlines() if l.strip()}
    with args.out.open("a") as fh:
        for i, (wid, arm, budget, rep) in enumerate(cells, 1):
            label = f"{wid}-{arm}-{args.evict}-{budget}-{rep}"
            if label in done:
                continue
            secs = args.max_seconds or WORKLOADS[wid]["seconds"]
            row = run_cell(wid, args.target, arm, args.evict, budget, rep,
                           args.model, secs, args.dry_run)
            if row is None:
                continue
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"[codebase] {i}/{len(cells)} {row['label']} tasks={row.get('tasks')} "
                  f"edges={row.get('edges')} depth={row.get('depth')} {row.get('wall_s')}s",
                  file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
