#!/usr/bin/env python3
"""Tool-heavy end-to-end sessions through profile_run.py (research/06_tool_cost Part C, C4b).

    python3 research/06_tool_cost/tool_heavy_workloads.py --rep r1 [--only HTEST,HSEARCH,HWEB]
        [--repo LANE] [--trace-dir DIR] [--max-seconds 3600] [--driver-arg=...] [--skip-existing]

Every workload in research/05 and research/11 is light on tools (0.2-0.6 % of wall).  These three
make the supported tools do the heavy work the characterisation papers profile, with nothing but
the harness's own bash / read_file / edit_file / glob:

  HTEST    SWE-bench-like: fix two injected bugs in a sandbox copy of the s10 task-system lesson
           (research/common/fixtures/tool_heavy/task_system), rerunning its pytest file after each
           edit and the whole lesson suite (tests/, ~45 s, 517 tests) once at the end
  HSEARCH  repository-scale search: three exact-count questions over the installed site-packages
           (vllm, transformers, all packages; 63k files, 7.6 GB on NFS) answered with grep / find
  HWEB     web research: fetch two arXiv abstract pages with curl and report titles and v1 dates
           (only meaningful where the node has outbound HTTPS; the pipeline checks first)

Solo sessions (the lead alone, no tasks, teammates or subagents), streaming requests, full tool
outputs in the trace, --no-timestamp, and no injected delay.  Scoring (<label>.score.json):
HTEST by running the archived sandbox's tests, HSEARCH by the three integers in the lead's reply,
HWEB by the two titles.  Labels are <CAT>-solo-<rep>, as research/05_latency_breakdown's analyzers
expect.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401
from latency_workloads import lead_final_text, load_trace                  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "research" / "06_tool_cost" / "data" / "tool_heavy"
SITE = "/mnt/home/yqi10/.venv/lib/python3.12/site-packages"
FIXTURE = "research/common/fixtures/tool_heavy/task_system"
SANDBOX = "profiling_sandbox/heavy"
SOLO = ("Do the following yourself in this single turn: do not create tasks, do not spawn teammates "
        "or subagents, do not create worktrees. ")

KNOWN_FAILURES = ("tests/test_s08_context_compact.py::test_prepare_persists_oversized_unseen_result_before_full_compact "
                  "and tests/test_web_scenarios.py::test_generated_s16_metadata_extends_s15_without_registry_false_positives")
PROMPTS = {
    "HTEST": SOLO + (
        f"The unit tests in {SANDBOX}/test_task_system.py fail. Find and fix the bug(s) in {SANDBOX}/code.py "
        "(change only that file, with edit_file). After every edit run the sandbox tests with the shell "
        f"command `python3 -m pytest -q -p no:cacheprovider {SANDBOX}`. When they all pass, run the complete "
        "lesson test suite once with `python3 -m pytest -q -p no:cacheprovider tests` to check for "
        f"regressions; two failures there are known and unrelated: {KNOWN_FAILURES}. Final reply: at most 8 "
        "lines naming each bug you fixed, the final sandbox test result line and the final full-suite "
        "result line."),
    "HSEARCH": SOLO + (
        f"Answer three questions about the Python packages installed in {SITE}, using read-only shell "
        "commands (grep, find, wc, sort) and the glob tool; do not modify any files. "
        f"(1) How many .py files under {SITE}/vllm define at least one class whose name ends in ForCausalLM? "
        "Count files, not classes. "
        f"(2) How many modeling_*.py files under {SITE}/transformers/models define a top-level function named "
        "apply_rotary_pos_emb? "
        f"(3) Across all of {SITE}, how many distinct top-level entries (the first path component below "
        "site-packages) contain at least one .py file that mentions the string flash_attn? "
        "Final reply: exactly three lines 'Q1: <integer>', 'Q2: <integer>', 'Q3: <integer>'."),
    "HWEB": SOLO + (
        "Using curl through the shell (read-only: print to stdout, do not save files), fetch "
        "https://arxiv.org/abs/2601.12967 and https://arxiv.org/abs/2511.02230 and report each paper's "
        "full title and the date of its first version (v1). Final reply: two lines "
        "'<arXiv id>: <title> (v1 <date>)'."),
}
HSEARCH_ANSWERS = {"Q1": 150, "Q2": 169, "Q3": 28}   # computed 2026-09-29 on this venv (see README Part C)
HWEB_TITLES = {"2601.12967": "Sutradhara", "2511.02230": "Continuum"}


def score_run(cat: str, label: str, trace_dir: Path, console: Path) -> dict | None:
    text = lead_final_text(console)
    match = re.search(r"\[profile\] trace=(\S+)", text)
    if not match or not Path(match.group(1)).exists():
        return None
    trace_path = Path(match.group(1))
    records = load_trace(trace_path)
    meta_end = next((r["data"] for r in records if r.get("event") == "profile_end"), {})
    commands = [r["data"].get("arguments", {}).get("command", "")
                for r in records if r.get("event") == "tool_start" and r.get("data", {}).get("tool") == "bash"]
    score: dict = {"label": label, "category": cat, "mode": "solo", "trace": str(trace_path),
                   "status": meta_end.get("status"), "wall_seconds": meta_end.get("wall_seconds"),
                   "denials": meta_end.get("denials"), "bash_calls": len(commands)}
    tail = text[-4000:]
    if cat == "HTEST":
        archive = trace_dir / f"{label}.sandbox"
        passed = False
        if (archive / "test_task_system.py").exists():
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                   "test_task_system.py"], capture_output=True, text=True, timeout=300,
                                  cwd=str(archive))
            passed = proc.returncode == 0
            score["summary"] = (proc.stdout.strip().splitlines() or [""])[-1]
        score.update(correct=int(passed), total=1,
                     sandbox_test_runs=sum(1 for c in commands if "pytest" in c and SANDBOX in c),
                     suite_runs=sum(1 for c in commands if "pytest" in c and re.search(r"\stests/?(\s|$)", c)))
    elif cat == "HSEARCH":
        found = {q: bool(re.search(rf"{q}\s*[:=]\s*\**\s*{a}\b", tail)) for q, a in HSEARCH_ANSWERS.items()}
        score.update(answers=found, correct=sum(found.values()), total=len(found))
    else:
        found = {k: v.lower() in tail.lower() for k, v in HWEB_TITLES.items()}
        score.update(answers=found, correct=sum(found.values()), total=len(found),
                     curl_calls=sum(1 for c in commands if "curl" in c))
    (trace_dir / f"{label}.score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rep", required=True)
    parser.add_argument("--only", default="HTEST,HSEARCH,HWEB")
    parser.add_argument("--repo", default=str(REPO), help="lane whose profile_run.py drives the session")
    parser.add_argument("--trace-dir", default=str(TRACE_DIR))
    parser.add_argument("--max-seconds", type=float, default=3600.0)
    parser.add_argument("--quiet-seconds", type=float, default=20.0)
    parser.add_argument("--pause", type=float, default=5.0)
    parser.add_argument("--driver-arg", action="append", default=[])
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--print-prompts", action="store_true")
    args = parser.parse_args()

    trace_dir = Path(args.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    lane = Path(args.repo)
    driver = lane / "research" / "common" / "profile_run.py"
    for cat in args.only.split(","):
        label = f"{cat}-solo-{args.rep}"
        if args.print_prompts:
            print(f"\n=== {label}\n{PROMPTS[cat]}\n")
            continue
        log = trace_dir / f"{label}.console.log"
        existing = trace_dir / f"{label}.score.json"
        if args.skip_existing and not args.score_only and existing.exists():
            try:
                status = json.loads(existing.read_text(encoding="utf-8")).get("status")
            except Exception:
                status = None
            if status in {"completed", "teammates-idle", "no-teammates"}:
                print(f"[heavy] skip {label}: already {status}", flush=True)
                continue
        if not args.score_only:
            cmd = [sys.executable, str(driver), "--label", label, "--trace-output", "full",
                   "--trace-dir", str(trace_dir), "--max-seconds", str(args.max_seconds),
                   "--quiet-seconds", str(args.quiet_seconds), "--stream", "--no-timestamp",
                   "--prompt", PROMPTS[cat]]
            if cat == "HTEST":
                cmd += ["--write-root", SANDBOX, "--sandbox-from", FIXTURE]
            cmd += list(args.driver_arg)
            print(f"[heavy] {time.strftime('%H:%M:%S')} start {label} (log {log})", flush=True)
            with log.open("w", encoding="utf-8") as handle:
                code = subprocess.run(cmd, cwd=lane, stdout=handle, stderr=subprocess.STDOUT,
                                      env=dict(os.environ)).returncode
            print(f"[heavy] {time.strftime('%H:%M:%S')} end {label} exit={code}", flush=True)
        score = score_run(cat, label, trace_dir, log)
        if score:
            print(f"[heavy] score {label}: {score.get('correct')}/{score.get('total')} status={score.get('status')} "
                  f"wall={score.get('wall_seconds')}s bash={score.get('bash_calls')}", flush=True)
        else:
            print(f"[heavy] score {label}: no trace found in {log}", flush=True)
        if not args.score_only:
            time.sleep(args.pause)
    return 0


if __name__ == "__main__":
    sys.exit(main())
