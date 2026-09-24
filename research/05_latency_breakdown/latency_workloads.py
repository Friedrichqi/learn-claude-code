#!/usr/bin/env python3
"""Run the end-to-end latency-breakdown workloads (file Q&A, coding bench, math) through profile_run.py.

    python3 research/05_latency_breakdown/latency_workloads.py --rep r1 [--only FQA,CODE,MATH]
        [--mode team|solo|both] [--max-seconds 1200] [--trace-dir DIR] [--repo LANE]

Three task categories, each a set of three tasks:

  FQA   file question answering: each task reads two repository documents (one private, the shared
        GLOSSARY.md) and answers a factual question in <= 15 lines with citations
  CODE  coding bench: each task implements a specification (research/common/fixtures/latency_bench/coding/<p>/README.md)
        in a sandbox copy and iterates until the unittest file passes; writes are confined to the sandbox
  MATH  three AIME-style problems solved by reasoning, full written solution ending in a final answer

Modes:  team = the lead creates three task-board items and one teammate each (lead + 3 teammates);
        solo = the lead does the three tasks itself in one turn (no delegation).

Each run is one fresh non-interactive s15 session (research/common/profile_run.py) with streaming requests
(time-to-first-token per call), full tool outputs in the trace, the reads sidecar and prep-timing
events.  After a run the runner scores it (<label>.score.json): CODE by running the archived
sandbox's tests, MATH by checking the expected answers in the teammates'/lead's results, FQA by
keyword coverage of the expected facts.  Traces land in research/05_latency_breakdown/data/latency_profiling/ with a console log.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO / "research" / "05_latency_breakdown" / "data" / "latency_profiling"
BENCH = "research/common/fixtures/latency_bench/coding"
SANDBOX = "profiling_sandbox/coding"

TEAM_PREAMBLE = ("Create three independent task-board items, one per item below, and delegate each to its own "
                 "teammate. I confirm this team now: spawn the three teammates immediately without asking for "
                 "confirmation. Do not create worktrees. Copy each item's text verbatim into the task description. ")
TEAM_EPILOGUE = ("When all three results have arrived, {synthesis}, then request shutdown of all three teammates.")
SOLO_PREAMBLE = ("Do the following three items yourself, one after another, in this single turn: do not create "
                 "tasks, do not spawn teammates or subagents, do not create worktrees. ")
SOLO_EPILOGUE = "When all three are done, {synthesis}."

FQA_TAIL = (" Read both files with read_file (paging is fine) before answering; answer in at most 15 lines and "
            "cite the file and section heading for each claim. Do not create or modify any files.")
FQA_ITEMS = [
    "Q1 (compaction): Using s15_integrated_harness/ARCHITECTURE.md (section 6, Context compaction) and the "
    "'Compaction' entry of s15_integrated_harness/GLOSSARY.md, list the compaction layers in the order they "
    "run, the condition that triggers each layer, and what each layer replaces or archives." + FQA_TAIL,
    "Q2 (teams): Using s13_agent_teams/README.md and the 'Teammate' and 'Plan gate' entries of "
    "s15_integrated_harness/GLOSSARY.md, explain how a teammate obtains work (spawn-time assignment versus idle "
    "auto-claim), why 'result' and 'idle_notification' are separate events, and which tools the plan gate "
    "blocks while a plan is not approved." + FQA_TAIL,
    "Q3 (task board): Using s10_task_system/README.md and the 'Task board' entry of "
    "s15_integrated_harness/GLOSSARY.md, give the task states and the two actions that move a task between "
    "them, how blockedBy dependencies are checked when a task is claimed, and who is allowed to add "
    "dependencies and when." + FQA_TAIL,
]
FQA_SYNTHESIS = "combine the three answers into one summary of at most 20 lines"

CODE_PROBLEMS = ["intervals", "ttl_cache", "expr"]
CODE_ITEM = ("P{n} ({p}): read {sb}/{p}/README.md, then implement the specification in a new file "
             "{sb}/{p}/solution.py (create it with write_file, fix it with edit_file). Run the tests with the shell "
             "command `python3 {sb}/{p}/test_{p}.py` and iterate until every test passes. Do not modify the README or "
             "the test file and do not write anywhere else. Final reply: at most 10 lines with the number of test runs "
             "and the final unittest result line.")
CODE_ITEMS = [CODE_ITEM.format(n=i + 1, p=p, sb=SANDBOX) for i, p in enumerate(CODE_PROBLEMS)]
CODE_SYNTHESIS = "report for each problem whether its tests passed and how many test runs it took"

MATH_TAIL = (" Solve the problem by mathematical reasoning and write out the full solution (you may verify "
             "arithmetic with a `python3 -c` command if you wish; no other tools are needed). Final reply: the "
             "complete solution ending with a line 'Final answer: <integer>'.")
MATH_ITEMS = [
    "M1: Let x, y and z all exceed 1 and let w be a positive number such that log_x(w) = 24, log_y(w) = 40 and "
    "log_(xyz)(w) = 12. Find log_z(w)." + MATH_TAIL,
    "M2: What is the largest positive integer n for which n^3 + 100 is divisible by n + 10?" + MATH_TAIL,
    "M3: Find the largest possible value of k for which 3^11 is expressible as the sum of k consecutive positive "
    "integers." + MATH_TAIL,
]
MATH_SYNTHESIS = "reply with the three final answers, one per line"
MATH_ANSWERS = {"M1": 60, "M2": 890, "M3": 486}

FQA_EXPECTED = {
    "Q1": ["tool_result_budget", "snip_compact", "micro_compact", "fit_tool_results", "compact_history",
           "50 messages", "512", "3", "1000", "[Compacted]"],
    "Q2": ["spawn_teammate", "claim_next_task", "idle_notification", "result", "bash", "write_file", "edit_file",
           "approved", "task_id", "wait"],
    "Q3": ["pending", "in_progress", "completed", "claim_task", "complete_task", "blockedBy", "can_start",
           "update_task", "Lead", "pending and unowned"],
}

CATEGORIES = {
    "FQA": (FQA_ITEMS, FQA_SYNTHESIS),
    "CODE": (CODE_ITEMS, CODE_SYNTHESIS),
    "MATH": (MATH_ITEMS, MATH_SYNTHESIS),
}


def build_prompt(category: str, mode: str) -> str:
    items, synthesis = CATEGORIES[category]
    body = " ".join(f"({i + 1}) {item}" for i, item in enumerate(items))
    if mode == "team":
        return TEAM_PREAMBLE + body + " " + TEAM_EPILOGUE.format(synthesis=synthesis)
    return SOLO_PREAMBLE + body + " " + SOLO_EPILOGUE.format(synthesis=synthesis)


# ----------------------------------------------------------------------------- scoring
def load_trace(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def full_text(value) -> str:
    if isinstance(value, dict):
        full = value.get("full")
        if isinstance(full, str):
            return full
        if isinstance(full, dict) and isinstance(full.get("preview"), str):
            return full["preview"]
        return str(value.get("preview") or "")
    return str(value or "")


def result_texts(records: list[dict]) -> dict[str, list[str]]:
    """Teammate name -> texts of its result/message sends to the lead."""
    out: dict[str, list[str]] = {}
    for record in records:
        if record.get("event") != "message_send":
            continue
        data = record.get("data", {})
        if data.get("to") != "lead" or data.get("message_type") not in {"result", "message"}:
            continue
        out.setdefault(str(data.get("from")), []).append(full_text(data.get("content")))
    return out


def task_map(records: list[dict]) -> dict[str, str]:
    """Teammate name -> concatenated subject+description of the tasks it claimed."""
    tasks: dict[str, str] = {}
    owner_text: dict[str, str] = {}
    for record in records:
        event, data = record.get("event"), record.get("data", {})
        if event == "task_create":
            tasks[str(data.get("id"))] = f"{data.get('subject', '')}\n{data.get('description', '')}"
        elif event == "task_claim":
            owner = str(data.get("owner"))
            owner_text[owner] = owner_text.get(owner, "") + "\n" + tasks.get(str(data.get("task_id")), "")
        elif event == "agent_create" and data.get("task_id"):
            owner = str(data.get("name"))
            owner_text[owner] = owner_text.get(owner, "") + "\n" + tasks.get(str(data.get("task_id")), "") + "\n" + str(data.get("task", ""))
    return owner_text


def lead_final_text(console_log: Path) -> str:
    try:
        text = console_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def score_math(records, console: Path, mode: str) -> dict:
    results = result_texts(records)
    tasks = task_map(records)
    lead_text = lead_final_text(console)
    score = {}
    for pid, answer in MATH_ANSWERS.items():
        pattern = re.compile(rf"(?i)final answer\s*[:=]?\s*\**\s*{answer}\b")
        loose = re.compile(rf"\b{answer}\b")
        found = None
        if mode == "team":
            owners = [name for name, text in tasks.items() if pid in text]
            for name in owners:
                joined = "\n".join(results.get(name, []))
                if pattern.search(joined):
                    found = "final-answer-line"
                    break
                if loose.search(joined):
                    found = "mentioned"
        # the trace keeps at most 2,048 chars of a teammate result, so a long solution can lose its
        # closing line; the lead is asked to reply with the three final answers, so its reply counts
        lead_line = re.compile(rf"(?im)^[^\n]*(?:\b{pid}\b|answer)[^\n]*\b{answer}\b[^\n]*$")
        if found is None and pattern.search(lead_text):
            found = "final-answer-line(lead)"
        elif found in (None, "mentioned") and lead_line.search(lead_text):
            found = "final-answer-line(lead reply)"
        elif found is None and loose.search(lead_text):
            found = "mentioned(lead)"
        score[pid] = {"expected": answer, "found": found, "correct": found is not None and found.startswith("final")}
    score["correct"] = sum(1 for pid in MATH_ANSWERS if score[pid]["correct"])
    score["total"] = len(MATH_ANSWERS)
    return score


def score_code(archive: Path) -> dict:
    score = {}
    passed_total = 0
    for problem in CODE_PROBLEMS:
        test = archive / problem / f"test_{problem}.py"
        solution = archive / problem / "solution.py"
        entry = {"solution_exists": solution.exists(), "passed": False, "summary": None}
        if solution.exists() and test.exists():
            try:
                proc = subprocess.run([sys.executable, str(test)], capture_output=True, text=True, timeout=120,
                                      cwd=str(archive / problem))
                tail = (proc.stdout + proc.stderr).strip().splitlines()
                entry["summary"] = tail[-1] if tail else None
                ran = next((line for line in tail if line.startswith("Ran ")), None)
                entry["ran"] = ran
                entry["passed"] = proc.returncode == 0 and any(line.strip() == "OK" for line in tail)
            except subprocess.TimeoutExpired:
                entry["summary"] = "timeout"
        passed_total += int(entry["passed"])
        score[problem] = entry
    score["correct"] = passed_total
    score["total"] = len(CODE_PROBLEMS)
    return score


def score_fqa(records, console: Path, mode: str) -> dict:
    results = result_texts(records)
    tasks = task_map(records)
    lead_text = lead_final_text(console)
    score = {}
    total = 0.0
    for qid, expected in FQA_EXPECTED.items():
        text = ""
        if mode == "team":
            owners = [name for name, task in tasks.items() if qid in task]
            text = "\n".join("\n".join(results.get(name, [])) for name in owners)
        # the trace keeps at most 2,048 chars of a teammate result; the lead's synthesis (console
        # output) is what the user finally sees, so both count
        text = text + "\n" + lead_text
        haystack = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text.lower())  # 1,000 and 1000 are the same number
        hits = [key for key in expected if key.lower() in haystack]
        score[qid] = {"expected": len(expected), "hits": len(hits), "missing": [k for k in expected if k not in hits],
                      "answered": bool(text.strip())}
        total += len(hits) / len(expected)
    score["coverage"] = round(total / len(FQA_EXPECTED), 3)
    score["total"] = len(FQA_EXPECTED)
    return score


def score_run(category: str, mode: str, label: str, trace_dir: Path, console: Path) -> dict | None:
    text = lead_final_text(console)
    match = re.search(r"\[profile\] trace=(\S+)", text)
    if not match:
        return None
    trace_path = Path(match.group(1))
    if not trace_path.exists():
        return None
    records = load_trace(trace_path)
    meta_end = next((r["data"] for r in records if r.get("event") == "profile_end"), {})
    if category == "MATH":
        score = score_math(records, console, mode)
    elif category == "CODE":
        score = score_code(trace_dir / f"{label}.sandbox")
    else:
        score = score_fqa(records, console, mode)
    score.update({"label": label, "category": category, "mode": mode, "trace": str(trace_path),
                  "status": meta_end.get("status"), "wall_seconds": meta_end.get("wall_seconds"),
                  "denials": meta_end.get("denials"), "rate_limit_retries": meta_end.get("rate_limit_retries")})
    (trace_dir / f"{label}.score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rep", required=True, help="repetition tag appended to the label, e.g. r1")
    parser.add_argument("--only", default=None, help="comma-separated category ids (default: FQA,CODE,MATH)")
    parser.add_argument("--mode", choices=["team", "solo", "both"], default="team")
    parser.add_argument("--max-seconds", type=float, default=1200.0)
    parser.add_argument("--quiet-seconds", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=20.0, help="seconds between runs (lets provider limits settle)")
    parser.add_argument("--trace-dir", default=str(TRACE_DIR), help="where traces, sidecars, sandboxes and console logs go")
    parser.add_argument("--repo", default=str(REPO), help="repository (or lane copy) whose profile_run.py drives the session")
    parser.add_argument("--no-stream", action="store_true", help="do not use streaming requests (no prefill/decode split)")
    parser.add_argument("--score-only", action="store_true", help="only (re)score existing runs with these labels")
    parser.add_argument("--print-prompts", action="store_true")
    parser.add_argument("--driver-arg", action="append", default=[],
                        help="extra argument passed verbatim to profile_run.py (repeatable), e.g. "
                             "--driver-arg=--vllm-metrics=http://127.0.0.1:8011/metrics")
    parser.add_argument("--skip-existing", action="store_true",
                        help="skip a label whose <label>.score.json already reports a completed run (resumable matrix)")
    args = parser.parse_args()

    ids = args.only.split(",") if args.only else list(CATEGORIES)
    modes = ["team", "solo"] if args.mode == "both" else [args.mode]
    trace_dir = Path(args.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    lane = Path(args.repo)
    driver = lane / "research" / "common" / "profile_run.py"
    try:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO, check=True).stdout.strip()
    except Exception:
        git_head = ""

    for mode in modes:
        for category in ids:
            label = f"{category}-{mode}-{args.rep}"
            prompt = build_prompt(category, mode)
            if args.print_prompts:
                print(f"\n=== {label}\n{prompt}\n")
                continue
            log = trace_dir / f"{label}.console.log"
            if args.skip_existing and not args.score_only:
                existing = trace_dir / f"{label}.score.json"
                if existing.exists():
                    try:
                        status = json.loads(existing.read_text(encoding="utf-8")).get("status")
                    except Exception:
                        status = None
                    if status in {"completed", "teammates-idle", "no-teammates"}:
                        print(f"[workloads] skip {label}: already {status}", flush=True)
                        continue
            if not args.score_only:
                cmd = [sys.executable, str(driver), "--label", label, "--trace-output", "full", "--trace-dir", str(trace_dir),
                       "--max-seconds", str(args.max_seconds), "--quiet-seconds", str(args.quiet_seconds),
                       "--prompt", prompt]
                if not args.no_stream:
                    cmd.append("--stream")
                if category in {"CODE", "MATH"}:
                    cmd.append("--allow-python")
                if category == "CODE":
                    cmd += ["--write-root", SANDBOX, "--sandbox-from", BENCH]
                if mode == "team":
                    cmd += ["--followup-if-no-team", "Confirmed, proceed: create the three tasks and spawn the three teammates now."]
                cmd += list(args.driver_arg)
                print(f"[workloads] {time.strftime('%H:%M:%S')} start {label} (log {log})", flush=True)
                env = dict(**__import__("os").environ, PROFILE_GIT_HEAD=git_head)
                with log.open("w", encoding="utf-8") as handle:
                    code = subprocess.run(cmd, cwd=lane, stdout=handle, stderr=subprocess.STDOUT, env=env).returncode
                print(f"[workloads] {time.strftime('%H:%M:%S')} end {label} exit={code}", flush=True)
            score = score_run(category, mode, label, trace_dir, log)
            if score:
                if "correct" in score:
                    headline = f"{score['correct']}/{score.get('total')} correct"
                else:
                    headline = f"keyword coverage {score.get('coverage')}"
                print(f"[workloads] score {label}: {headline} status={score.get('status')} "
                      f"wall={score.get('wall_seconds')}s", flush=True)
            else:
                print(f"[workloads] score {label}: no trace found in {log}", flush=True)
            if not args.score_only:
                time.sleep(args.pause)
    return 0


if __name__ == "__main__":
    sys.exit(main())
