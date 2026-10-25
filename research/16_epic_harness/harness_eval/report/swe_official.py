#!/usr/bin/env python3
"""Grade s15's SWE-bench Verified patches with the official swebench harness (5.0.2, X2's venv) and compare
with Harbor's verifier, one instance at a time (each image is ~4 GB on a 36 GB disk; removed afterwards).

    python3 report/swe_official.py --model qwen3-32b --phase p1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
SWE_PY = "/data/fritz/epic/x2/the-complexity-trap/.venv/bin/python"
sys.path.insert(0, str(ROOT / "run"))
import images  # noqa: E402
import batch  # noqa: E402
import shutil  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--phase", required=True)
    a = ap.parse_args()
    out = ROOT / "report" / "swe_official" / f"{a.model}_{a.phase}"
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    if (out / "results.json").is_file():
        results = json.loads((out / "results.json").read_text())
    for res in sorted((ROOT / "jobs" / a.model / "swe").glob(f"{a.phase}__c*/*/result.json")):
        trial = res.parent
        r = json.loads(res.read_text())
        task = r.get("task_name", "").split("/")[-1]
        if not task or trial.name in results:
            continue
        patch_file = trial / "agent" / "model.patch"
        patch = patch_file.read_text() if patch_file.is_file() else ""
        harbor = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if not patch.strip():
            results[trial.name] = {"task": task, "official": False, "harbor": harbor, "note": "empty patch"}
            continue
        preds = out / f"{trial.name}.preds.json"
        preds.write_text(json.dumps([{"instance_id": task, "model_name_or_path": f"s15-{a.model}",
                                      "model_patch": patch}]))
        slot = batch.acquire("heavy", open(out / "slots.log", "a"))
        images.snapshot(f"official_{trial.name}")
        run_id = f"s15-{a.model}-{a.phase}-{trial.name}"[:120]
        p = subprocess.run([SWE_PY, "-m", "swebench.harness.run_evaluation", "--dataset_name",
                            "SWE-bench/SWE-bench_Verified", "--predictions_path", str(preds), "--instance_ids", task,
                            "--max_workers", "1", "--run_id", run_id, "--report_dir", str(out)],
                           cwd=out, capture_output=True, text=True, timeout=3600)
        reports = list(out.glob(f"logs/run_evaluation/{run_id}/*/{task}/report.json"))
        official = None
        if reports:
            official = bool(json.loads(reports[0].read_text()).get(task, {}).get("resolved"))
        results[trial.name] = {"task": task, "official": official, "harbor": harbor, "rc": p.returncode}
        images.cleanup(f"official_{trial.name}", [task.replace("__", "_1776_"), task])
        batch.release(slot)
        (out / "results.json").write_text(json.dumps(results, indent=1))
        print(trial.name, task, "official", official, "harbor", harbor, flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=1))
    agree = sum(1 for v in results.values() if v["official"] is not None and bool(v["official"]) == ((v["harbor"] or 0) >= 1))
    n = sum(1 for v in results.values() if v["official"] is not None)
    print(f"agreement {agree}/{n}; official resolved {sum(1 for v in results.values() if v['official'])}; "
          f"harbor resolved {sum(1 for v in results.values() if (v['harbor'] or 0) >= 1)}")


if __name__ == "__main__":
    main()
