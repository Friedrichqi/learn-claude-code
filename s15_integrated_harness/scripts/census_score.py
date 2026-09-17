#!/usr/bin/env python3
"""Score the archived sandboxes of the DAG-census runs.

    python3 s15_integrated_harness/scripts/census_score.py <trace-dir> [--json OUT]

Board shape is the experiment's subject, but a correct board is only interesting if the work behind
it also came out right -- otherwise a tidy graph could just be a Lead that planned and never
delivered.  This re-runs each archived sandbox's own tests in a scratch copy:

  CODE-DEP     one problem (intervals) carried through checklist -> implementation -> green tests.
               The other two problems SHOULD be absent; DEP asks for one problem, not three.
  CODE-FLAT    three independent problems, all three expected green.
  REFACTOR-DEP inventory -> rename -> green tests, which pass only if every call site moved.

Runs that the provider refused have an untouched seed sandbox and score as `not attempted`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CODE_PROBLEMS = ("intervals", "ttl_cache", "expr")


def run_tests(workdir: Path, script: str) -> str:
    try:
        out = subprocess.run([sys.executable, script], cwd=workdir, capture_output=True,
                             text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "timeout"
    tail = (out.stdout + out.stderr).strip().splitlines()
    if not tail:
        return "no output"
    last = tail[-1].strip()
    return "OK" if last.startswith("OK") else last[:60]


def score_sandbox(sandbox: Path) -> dict:
    label = sandbox.name.removesuffix(".sandbox")
    kind = "REFACTOR" if label.startswith("REFACTOR") else "CODE" if label.startswith("CODE") else None
    if kind is None:
        return {}
    row = {"label": label, "kind": kind}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        shutil.copytree(sandbox, work)
        if kind == "REFACTOR":
            test = work / "test_pkg.py"
            row["attempted"] = (work / "pkg.py").read_text(encoding="utf-8").find("canonical_key") >= 0
            row["result"] = run_tests(work, "test_pkg.py") if test.is_file() else "no test file"
            row["passed"] = row["result"] == "OK"
        else:
            results = {}
            for p in CODE_PROBLEMS:
                sol = work / p / "solution.py"
                if not sol.is_file():
                    results[p] = "not attempted"
                    continue
                results[p] = run_tests(work / p, f"test_{p}.py")
            row["results"] = results
            row["attempted"] = any(v != "not attempted" for v in results.values())
            expected = ("intervals",) if "-DEP-" in label else CODE_PROBLEMS
            row["passed"] = all(results.get(p) == "OK" for p in expected)
            row["expected"] = list(expected)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace_dir")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    rows = [r for r in (score_sandbox(s) for s in sorted(Path(args.trace_dir).glob("*.sandbox"))) if r]
    if not rows:
        print("no sandboxes found", file=sys.stderr)
        return 1

    print("\n**Table F. Did the work come out right?** Runs the provider refused score "
          "`not attempted` and are excluded from the rate.\n")
    print("| label | attempted | result | passed |")
    print("|---|---|---|---|")
    for r in rows:
        res = r.get("result") or ", ".join(f"{k}:{v}" for k, v in r["results"].items())
        print(f"| {r['label']} | {'yes' if r['attempted'] else 'no'} | {res} | "
              f"{'yes' if r['passed'] else 'no'} |")
    tried = [r for r in rows if r["attempted"]]
    print(f"\n{sum(1 for r in tried if r['passed'])} of {len(tried)} attempted sandboxes passed "
          f"({len(rows) - len(tried)} not attempted).")
    if args.json:
        Path(args.json).write_text(json.dumps({"sandboxes": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
