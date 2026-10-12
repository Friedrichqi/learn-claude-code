#!/usr/bin/env python3
"""X2: the SWE-agent observation-masking replica (JetBrains, "The Complexity Trap") under EPIC serving.

Harness: JetBrains' SWE-agent fork (github.com/JetBrains-Research/the-complexity-trap, SWE-agent 1.0.1,
commit bf15b5f) in its own venv (/data/fritz/epic/x2/the-complexity-trap/.venv), with their masking
configuration `config/default_no_demo_N=1_M=10.yaml`: function calling, `last_n_observations` n = 10
(every turn past ten observations masks the oldest one -- a mid-context edit on every call, the
"breaks prompt caching" case). Model settings follow their local Qwen3-32B runs (hosted_vllm provider,
temperature 0.8, 8K output tokens, cost limits off) with two documented deviations: the native 32K
window instead of YaRN-128K (the connector's RoPE rotation supports no scaling), so the input budget is
24,576 tokens, and 150 calls per instance instead of 250 (GPU budget).

Instances: SWE-bench Verified Mini (MariusHobbhahn/swe-bench-verified-mini: 50 instances, 25 django +
25 sphinx; few environment images). Arms {prefix, epic32, epic64, epic128} x 1 seed, served by the same
EPIC server as the s15 runs (epic_sweep.server_flags); the arm and session travel in
`completion_kwargs.extra_body.kv_transfer_params.epic` and the server counts calls per session.
Grading: the official swebench harness (FAIL_TO_PASS / PASS_TO_PASS) on prebuilt images; an instance's
image is removed after its four arms are graded (never images this study did not pull).

Several GPUs: one `run` process per GPU (EPIC_GPU, its own server and --tag for the logs); the processes
share the work through claim directories (runs are taken in instance order, a claim of a dead process is
taken over), pull images one at a time under a file lock, and the process finishing an instance's last arm
grades it.

    python research/16_epic_harness/epic_swe.py run [--instances N] [--lanes 2] [--tag T]
    python research/16_epic_harness/epic_swe.py grade
    python research/16_epic_harness/epic_swe.py report
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402
import epic_sweep as es  # noqa: E402

FORK = Path("/data/fritz/epic/x2/the-complexity-trap")
SWE_PY = FORK / ".venv" / "bin" / "python"
SWEAGENT = FORK / ".venv" / "bin" / "sweagent"
CONFIG = FORK / "config" / "default_no_demo_N=1_M=10.yaml"
IDS = Path("/data/fritz/epic/x2/verified_mini_ids.json")
OUT = ec.DATA / "x2_swe"
ARMS = ["prefix", "epic32", "epic64", "epic128"]
MIN_FREE_GB = 20


def free_gb(path: str = "/var/lib/docker") -> float:
    target = path if os.path.exists(path) else "/"
    st = os.statvfs(target)
    return st.f_bavail * st.f_frsize / 2**30


def image_of(instance: str) -> str:
    return f"swebench/sweb.eval.x86_64.{instance.replace('__', '_1776_')}:latest"


def own_images() -> set[str]:
    path = OUT / "pulled_images.txt"
    return set(path.read_text().split()) if path.exists() else set()


def note_image(image: str) -> None:
    """Pull an instance image unless present; one pull at a time across all run processes."""
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / ".pull.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        have = subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0
        if not have and image not in own_images():
            subprocess.run(["docker", "pull", "-q", image], capture_output=True)
            with (OUT / "pulled_images.txt").open("a") as handle:
                handle.write(image + "\n")


def claim(name: str) -> bool:
    """Atomically take `name` (an instance-arm run, or an instance's grading) for this process. A claim
    whose process is gone is taken over (renamed away first, so only one taker wins)."""
    path = OUT / "claims" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        owner = (path / "pid").read_text().strip() if (path / "pid").exists() else ""
        try:
            alive = bool(owner) and (os.kill(int(owner), 0) is None)
        except (ProcessLookupError, ValueError):
            alive = False
        except PermissionError:
            alive = True
        if alive:
            return False
        try:
            path.rename(path.with_name(f"{name}.stale.{os.getpid()}.{time.time_ns()}"))
            path.mkdir()
        except (FileNotFoundError, FileExistsError, OSError):
            return False
    (path / "pid").write_text(str(os.getpid()))
    return True


def run_instance(base_url: str, instance: str, arm: str) -> dict:
    label = f"{instance}-{arm}"
    out = OUT / "runs" / label
    if (out / "done.json").exists():
        return json.loads((out / "done.json").read_text())
    if out.exists():                    # a cut-short attempt: SWE-agent would skip its existing trajectory
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    tag = {"arm": arm, "spec": f"x2:{label}", "seed_key": f"x2:{instance}"}
    # the client timeout must outlast any generation: an 8K-token reply with both lanes decoding and KV-pool
    # preemption took over 600 s, and litellm then resent the call (same seed), so a run could loop for hours
    kwargs = {"timeout": "1800", "extra_body": {"kv_transfer_params": {"epic": tag}}}
    cmd = [str(SWEAGENT), "run-batch", "--config", str(CONFIG),
           "--agent.model.name", "hosted_vllm/Qwen/Qwen3-32B",
           "--agent.model.api_base", f"{base_url}/v1/",
           "--agent.model.per_instance_call_limit", "150", "--agent.model.per_instance_cost_limit", "0",
           "--agent.model.total_cost_limit", "0", "--agent.model.max_input_tokens", "24576",
           "--agent.model.max_output_tokens", "8192", "--agent.model.temperature", "0.8",
           "--agent.model.completion_kwargs", json.dumps(kwargs), "--agent.type", "default",
           "--instances.type", "swe_bench", "--instances.subset", "verified", "--instances.split", "test",
           "--instances.filter", f"^{instance}$", "--num_workers", "1", "--output_dir", str(out)]
    started = time.time()
    with (out / "console.log").open("w") as console:
        proc = subprocess.run(cmd, cwd=FORK, stdout=console, stderr=subprocess.STDOUT,
                              env={**os.environ, "HF_HUB_OFFLINE": "0", "PYTHONUNBUFFERED": "1"},
                              timeout=4 * 3600)
    preds = out / "preds.json"
    patch = None
    if preds.exists():
        data = json.loads(preds.read_text())
        patch = (data.get(instance) or {}).get("model_patch")
    traj = out / instance / f"{instance}.traj"
    info = json.loads(traj.read_text()).get("info", {}) if traj.exists() else {}
    status = info.get("exit_status") or ("no trajectory" if proc.returncode == 0 else f"exit {proc.returncode}")
    res = {"label": label, "instance": instance, "arm": arm, "exit": proc.returncode,
           "wall_s": round(time.time() - started, 1), "has_patch": bool(patch), "exit_status": status,
           "api_calls": (info.get("model_stats") or {}).get("api_calls")}
    try:
        urllib.request.urlopen(f"{base_url}/epic/end?spec=" + urllib.parse.quote(f"x2:{label}"), timeout=30).read()
    except Exception:
        pass
    # an infrastructure failure (API, Docker, crash) is retried once; the second attempt's outcome stands
    infra = (not traj.exists() or proc.returncode != 0
             or str(status).startswith(("exit_api", "exit_environment_error", "exit_error", "skipped")))
    tries = OUT / "runs" / f"{label}.tries"             # beside the run directory, which a retry clears
    attempt = int(tries.read_text()) + 1 if tries.exists() else 1
    tries.write_text(str(attempt))
    res.update(attempt=attempt, infra=infra)
    if infra and attempt < 2:
        return {**res, "retry": True}
    (out / "done.json").write_text(json.dumps(res))
    return res


def grade_instance(instance: str) -> None:
    """Official harness on each arm's prediction of one instance, then remove its image (ours only)."""
    for arm in ARMS:
        out = OUT / "runs" / f"{instance}-{arm}"
        preds, done = out / "preds.json", out / "done.json"
        if not preds.exists() or not done.exists() or graded_outcome(out, json.loads(done.read_text()))[1] not in (
                "not graded", "harness infrastructure failure"):
            continue                    # unfinished, or evaluated already (a test timeout is not rerun)
        run_id = f"x2-{instance}-{arm}"
        # swebench 5.0.2 reads each instance's image name from the dataset's `image` column (the
        # SWE-bench org copy has it: the same Docker Hub images SWE-agent ran in)
        subprocess.run([str(SWE_PY), "-m", "swebench.harness.run_evaluation",
                        "--dataset_name", "SWE-bench/SWE-bench_Verified", "--predictions_path", str(preds),
                        "--instance_ids", instance, "--max_workers", "1", "--run_id", run_id,
                        "--report_dir", str(out)],
                       cwd=out, env={**os.environ, "HF_HUB_OFFLINE": "0"}, capture_output=True)
        reports = list(out.rglob("report.json"))
        if reports:
            shutil.copy(reports[0], out / "report.json")
    image = image_of(instance)
    if image in own_images() and all((OUT / "runs" / f"{instance}-{a}" / "done.json").exists() for a in ARMS):
        subprocess.run(["docker", "rmi", image], capture_output=True)


def cmd_run(args) -> None:
    """Instance-arm runs in instance order, each claimed by one lane of one process; a grader thread grades an
    instance (and removes its image) once its four arms are done, so at most a few images are on disk."""
    import queue
    import threading
    ids = json.loads(IDS.read_text())[: args.instances or None]
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(instance, arm) for instance in ids for arm in ARMS]
    to_grade: "queue.Queue" = queue.Queue()
    lock = threading.Lock()

    def all_done(instance: str) -> bool:
        return all((OUT / "runs" / f"{instance}-{a}" / "done.json").exists() for a in ARMS)

    def next_job():
        with lock:
            for instance, arm in jobs:
                if not (OUT / "runs" / f"{instance}-{arm}" / "done.json").exists() and claim(f"{instance}-{arm}"):
                    return instance, arm
        return None

    def grader():
        while True:
            instance = to_grade.get()
            if instance is None:
                return
            grade_instance(instance)
            ec.log(f"x2 graded {instance}")

    gpu = ec.pick_gpu(args.gpu)
    serve_log = es.log_names(args.tag)[0]
    with ec.served(gpu, OUT / serve_log, es.server_flags(), env_extra=es.server_env(OUT, args.tag)) as base_url:
        grader_thread = threading.Thread(target=grader, daemon=True)
        grader_thread.start()

        def lane(k: int):
            failures = 0
            while not ec.STOPPING.is_set():
                if free_gb() < MIN_FREE_GB:
                    ec.log(f"stopping lane {k}: {free_gb():.0f} GB free on the docker disk")
                    return
                if not ec.healthy(base_url):
                    raise RuntimeError("server unhealthy")
                job = next_job()
                if job is None:
                    return
                instance, arm = job
                note_image(image_of(instance))
                res = run_instance(base_url, instance, arm)
                ec.log(f"x2 gpu{gpu} lane{k} {res['label']}: exit {res['exit']} {res['wall_s']} s "
                       f"status={res['exit_status']} calls={res['api_calls']} patch={res['has_patch']}"
                       + (" (infrastructure failure, will retry)" if res.get("retry") else ""))
                if res.get("retry"):
                    shutil.rmtree(OUT / "claims" / res["label"], ignore_errors=True)
                failures = failures + 1 if res.get("infra") else 0
                if failures >= 3:
                    ec.log(f"stopping lane {k}: 3 infrastructure failures in a row")
                    return
                if all_done(instance) and claim(f"{instance}-grade"):
                    to_grade.put(instance)

        with cf.ThreadPoolExecutor(args.lanes) as pool:
            for fut in [pool.submit(lane, k) for k in range(args.lanes)]:
                fut.result()
    to_grade.put(None)
    grader_thread.join()


def cmd_grade(args) -> None:
    for instance in json.loads(IDS.read_text()):
        grade_instance(instance)


def graded_outcome(d: Path, res: dict) -> tuple[bool | None, str]:
    """(resolved, how) of one instance-arm run from the official harness's outputs. A patch whose evaluation
    errors (its tests time out after 1,800 s, or fail to run) is unresolved, as SWE-bench scores it; an
    infrastructure failure of the harness, or a run not graded yet, is None (left out, and counted)."""
    rep = d / "report.json"
    if not rep.exists():                               # the harness's own copy, if the grader stopped early
        rep = next(d.glob("logs/run_evaluation/*/*/*/report.json"), rep)
    if rep.exists():
        return bool((json.loads(rep.read_text()).get(res["instance"]) or {}).get("resolved")), "report"
    if res.get("has_patch") is False:
        return False, "no patch"                       # nothing to apply: unresolved
    for summary in d.glob("*.x2-*.json"):
        data = json.loads(summary.read_text())
        if data.get("infra_failure_instances"):
            return None, "harness infrastructure failure"
        if data.get("empty_patch_instances"):
            return False, "empty patch"
        if data.get("error_instances"):
            logs = " ".join(log.read_text(errors="replace")[-4000:]
                            for log in d.glob("logs/run_evaluation/*/*/*/run_instance.log"))
            return False, "test timeout" if "timed out" in logs else "evaluation error"
    return None, "not graded"


def cmd_report(args) -> None:
    import statistics
    from collections import Counter, defaultdict

    import epic_analyze as ea
    rows = []
    for d in sorted((OUT / "runs").iterdir()):
        done = d / "done.json"
        if not d.is_dir() or not done.exists():
            continue
        res = json.loads(done.read_text())
        resolved, harness = graded_outcome(d, res)
        rows.append({**res, "resolved": resolved, "harness": harness})
    calls = defaultdict(list)
    for path in ec.server_logs(OUT):
        for rec in ea.open_jsonl(path):
            if rec.get("ev") == "call" and str(rec.get("spec", "")).startswith("x2:"):
                calls[rec["spec"][3:]].append(rec)
    n_inst = len({r["instance"] for r in rows})
    lines = ["# X2: SWE-agent observation masking under EPIC serving (SWE-bench Verified Mini)", "",
             f"Generated by `epic_swe.py report`. {len(rows)} instance-arm runs over {n_inst} instances; resolved = "
             "the official harness's FAIL_TO_PASS and PASS_TO_PASS on the run's patch (no patch: unresolved).", "",
             "| arm | runs | resolved [95% CI] | patch produced | test timeout / evaluation error | not graded | "
             "model calls (median) | wall s (median) | exit statuses | infrastructure retried |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        res_ = [r["resolved"] for r in rs if r["resolved"] is not None]
        k = sum(res_)
        lo, hi = ea.wilson(k, len(res_)) if res_ else (float("nan"), float("nan"))
        st = Counter(str(r.get("exit_status")).split(" (")[0] for r in rs).most_common(3)
        calls_n = [r["api_calls"] for r in rs if r.get("api_calls") is not None]
        hz = Counter(r["harness"] for r in rs)
        lines.append(f"| {arm} | {len(rs)} | {k}/{len(res_)} [{lo:.0%}, {hi:.0%}] | "
                     f"{sum(bool(r['has_patch']) for r in rs)} | {hz['test timeout']} / {hz['evaluation error']} | "
                     f"{hz['not graded'] + hz['harness infrastructure failure']} | "
                     f"{statistics.median(calls_n) if calls_n else 'nan'} | "
                     f"{statistics.median(r['wall_s'] for r in rs):.0f} | "
                     + ", ".join(f"{a} {b}" for a, b in st) + f" | {sum(int(r.get('attempt') or 1) > 1 for r in rs)} |")
    by_inst = defaultdict(dict)
    for r in rows:
        if r["resolved"] is not None:
            by_inst[r["instance"]][r["arm"]] = r["resolved"]
    lines += ["", "**Paired with prefix** (same instance and seed key):", "",
              "| arm | instances | both | only prefix | only arm | neither | McNemar p |",
              "|---|---|---|---|---|---|---|"]
    for arm in ARMS[1:]:
        pairs = [(v["prefix"], v[arm]) for v in by_inst.values() if "prefix" in v and arm in v]
        if not pairs:
            continue
        b = sum(1 for x, y in pairs if x and not y)
        c = sum(1 for x, y in pairs if y and not x)
        lines.append(f"| {arm} | {len(pairs)} | {sum(1 for x, y in pairs if x and y)} | {b} | {c} | "
                     f"{sum(1 for x, y in pairs if not x and not y)} | {ea.mcnemar_exact(b, c):.3f} |")
    lines += ["", "**Prefill accounting** (lead calls as served; prefix from vLLM's cache hits) and **on-policy "
              "shadow fidelity** (fresh recompute of the same prompt on sampled calls with stale rows):", "",
              "| arm | calls | calls after an edit | prompt tokens | computed | reused stale | run heads | "
              "KV hit rate | probes | reused rows D^K | first-token KL | reply KL | reply top-1 | "
              "counterfactual identical |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    all_calls = [c for cs in calls.values() for c in cs]
    acc = ea.l1_accumulate(all_calls)
    for arm in ARMS:
        cs = [c for c in all_calls if c.get("arm") == arm]
        if not cs:
            continue
        P = sum(c["P"] for c in cs)
        if arm == "prefix":
            C, S, H = P - sum(int(c.get("apc_hit") or 0) for c in cs), 0, 0
        else:
            C = sum(c.get("C", c["P"] - int(c.get("apc_hit") or 0)) - int(c.get("apc_over_C") or 0)
                    for c in cs)
            S = sum(int(c.get("S") or 0) - int(c.get("apc_over_S") or 0) for c in cs)
            H = sum(int(c.get("H") or 0) for c in cs)
        sh = [c["shadow"] for c in cs if c.get("shadow")]
        dk = ea.l1_dev(acc[arm], lambda cl, a, o: cl == 1)[0] if arm in acc else float("nan")
        fid = [ea._fmt(ea._mean(x.get(k) for x in sh if k in x)) for k in ("first_kl", "tf_kl", "tf_top1",
                                                                          "cf_identical")]
        lines.append(f"| {arm} | {len(cs)} | {sum(bool(c.get('edited')) for c in cs)} | {P:,} | {C:,} | {S:,} | "
                     f"{H:,} | {1 - C / P:.1%} | {len(sh)} | {dk:.4f} | " + " | ".join(fid) + " |")
    (OUT / "x2_tables.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--instances", type=int, default=0)
    r.add_argument("--lanes", type=int, default=2)
    r.add_argument("--gpu", type=int, default=None)
    r.add_argument("--tag", default="", help="this process's server log tag (one process per GPU)")
    sub.add_parser("grade")
    sub.add_parser("report")
    args = ap.parse_args()
    {"run": cmd_run, "grade": cmd_grade, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
