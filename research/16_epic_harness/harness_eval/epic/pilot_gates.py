#!/usr/bin/env python3
"""E-0 and the pilot gates of an EPIC round phase (plan "EPIC round", Verification).

    python3 epic/pilot_gates.py --server minimax-m2.7-epic --bench swe --phase p0r0 --url http://127.0.0.1:PORT

E-0, append-only fidelity, on the EPIC arms' calls before their first snip:
- The planner's `exact` (the leading run where the new prompt equals the session's previous sequence, i.e. the
  previous prompt plus its generated tokens) should cover the whole previous sequence.
- Share = exact / (previous prompt + generated − 1), per call, joined between the child's calls.jsonl and the
  server log by (spec, call).
- Each miss is located: at the reply boundary (exact ≈ previous prompt length) or inside the previous prompt.

Gates:
- zero preemptions (vLLM metric);
- fallbacks and resets below 5 % of post-snip calls;
- exactly one server `call` record per tagged lead call;
- identical override counts on every TP rank;
- no session of the phase left on the server;
- (e4) the recompute arms: one decision record (`dec`) on every curated (edited) call that was neither a fallback
  nor aborted, none anywhere else, and no failed decisions (`dec.error`); per arm, decisions and recomputations.
Also counts the server's `error` records of the phase's sessions, by where they were raised.

Cost: wall time of the phase's jobs × GPUs / trials.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics as st
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import epic_plan as ep  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--bench", default="swe")
    ap.add_argument("--phase", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--gpus", type=int, default=4)
    ap.add_argument("--log", default="")
    ap.add_argument("--model-dir", default="/data/fritz/hf/hub/models--MiniMaxAI--MiniMax-M2.7")
    ap.add_argument("--out", default="", help="report path (default runs/epic/gates_<server>_<phase>.json)")
    a = ap.parse_args()
    report: dict = {"phase": a.phase, "arms": {}}
    # the phase's own jobs plus its per-block and rerun jobs ("<phase>b<i>", "<phase>[b<i>]x<n>")
    trials = sorted(t for t in glob.glob(str(ROOT / "jobs" / f"{a.server}+*" / a.bench / f"{a.phase}*__*" / "*__*"))
                    if re.fullmatch(re.escape(a.phase) + r"(b\d+)?(x\d+)?", Path(t).parts[-2].split("__")[0]))
    # The agents' lead calls first, the server log after them: the server writes a call's record before the reply
    # reaches the agent, so a log read after the agents' logs holds the record of every call they list. (Read the
    # other way round, a check while trials run counts the calls that finished in between as missing records: e3's
    # interim check, 2026-10-14 02:01 UTC.) Per call: its number and whether its request follows a snip.
    lead = {}
    for tdir in trials:
        lead[tdir] = [(int(c["epic"]["n"]), any(isinstance(m.get("content"), str) and " messages archived at " in m["content"]
                                                for m in ((c.get("request") or {}).get("messages") or [])[:5]))
                      for c in load_jsonl(Path(tdir) / "agent" / "s15" / "calls.jsonl")
                      if c.get("purpose") == "lead" and c.get("epic") and c.get("response") is not None]
    log = load_jsonl(Path(a.log) if a.log else ROOT / "runs" / "epic" / f"{a.server}.calls.jsonl")
    calls_by = {}
    for x in log:
        if x.get("ev") == "call":
            calls_by.setdefault((x["spec"], int(x.get("call", -1))), []).append(x)
    starts, ends = [], []
    e0_share, e0_miss_at_reply, e0_miss_inside = [], 0, 0
    post_snip = fallbacks = resets = 0
    missing_records = duplicate_records = tagged = 0
    dec_missing = dec_leaked = dec_errors = 0
    dec_examples: list = []
    for tdir in trials:
        tdir = Path(tdir)
        arm = tdir.parts[-4].split("+", 1)[1]
        spec = tdir.name
        res = json.loads((tdir / "result.json").read_text()) if (tdir / "result.json").is_file() else {}
        for k, lst in (("started_at", starts), ("finished_at", ends)):
            if res.get(k):
                lst.append(datetime.fromisoformat(res[k].replace("Z", "+00:00")))
        calls = lead[str(tdir)]
        arm_stats = report["arms"].setdefault(arm, {"trials": 0, "lead_calls": 0})
        arm_stats["trials"] += 1
        arm_stats["lead_calls"] += len(calls)
        prev = None
        for n, snipped in calls:
            recs = calls_by.get((spec, n), [])
            tagged += 1
            if not recs:
                missing_records += 1
                continue
            if len(recs) > 1:
                duplicate_records += len(recs) - 1
            r = recs[-1]
            if ep.recompute_metric(arm) and r.get("edited") and not r.get("fallback") and not r.get("aborted"):
                d = r.get("dec")
                a_dec = arm_stats.setdefault("decisions", {"n": 0, "recompute": 0, "same": 0, "errors": 0})
                if not isinstance(d, dict) or ("m" not in d and "error" not in d):
                    dec_missing += 1
                    if len(dec_examples) < 5:
                        dec_examples.append({"spec": spec, "call": n, "problem": "missing"})
                else:
                    a_dec["n"] += 1
                    a_dec["recompute"] += int(bool(d.get("recompute")))
                    a_dec["same"] += int(bool(d.get("same")))
                    if d.get("error"):
                        a_dec["errors"] += 1
                        dec_errors += 1
                        if len(dec_examples) < 5:
                            dec_examples.append({"spec": spec, "call": n, "problem": d["error"][:200]})
            elif r.get("dec") is not None or r.get("recompute"):
                if not (ep.recompute_metric(arm) and (r.get("fallback") or r.get("aborted"))):
                    dec_leaked += 1
                    if len(dec_examples) < 5:
                        dec_examples.append({"spec": spec, "call": n, "problem": "decision on a call without one"})
            if snipped:
                post_snip += 1
                fallbacks += int(bool(r.get("fallback")))
                resets += int(bool(r.get("entry_reset")))
            elif prev is not None and "exact" in r and prev.get("P") and "C" in r:
                seq = int(prev["P"]) + max(0, int(prev.get("gen") or 0) - 1)
                share = min(1.0, int(r["exact"]) / seq) if seq else 1.0
                e0_share.append(share)
                if share < 0.99:
                    if abs(int(r["exact"]) - int(prev["P"])) <= 16:
                        e0_miss_at_reply += 1
                    else:
                        e0_miss_inside += 1
                    if r.get("div") and len(report.setdefault("div_examples", [])) < 6:
                        report["div_examples"].append({"spec": spec, "call": n, **r["div"]})
            prev = r
    source = "live"
    try:
        metrics = urllib.request.urlopen(f"{a.url}/metrics", timeout=30).read().decode()
        preempt = sum(float(l.split()[-1]) for l in metrics.splitlines() if l.startswith("vllm:num_preemptions_total"))
    except Exception:                                              # noqa: BLE001
        preempt = None
    try:
        workers = json.loads(urllib.request.urlopen(f"{a.url}/epic/workers", timeout=60).read()).get("workers") or []
        st_ = json.loads(urllib.request.urlopen(f"{a.url}/epic/stats", timeout=30).read())
    except Exception:                                              # noqa: BLE001
        workers, st_ = [], {}
    phase_specs = {Path(t).name for t in trials}
    errors_by: dict = {}
    for x in log:
        if x.get("ev") == "error" and str(x.get("spec", "")) in phase_specs:
            errors_by[x.get("where")] = errors_by.get(x.get("where"), 0) + 1
    for arm_stats in report["arms"].values():
        d = arm_stats.get("decisions")
        if d:
            d["recompute_rate"] = d["recompute"] / d["n"] if d["n"] else None
    left = sorted(phase_specs & set((st_.get("session_calls") or {}).keys()))
    snaps = load_jsonl(ROOT / "runs" / "epic" / f"health_{a.server}.jsonl")
    if preempt is None and snaps:
        # the round's runner stopped the server: the last snapshots of run/epic_health.py (taken every minute,
        # per-rank counters every 5 min) stand in for the live counters
        source = "snapshot"
        last = snaps[-1]
        preempt = last.get("preemptions")
        workers = next((x["workers"] for x in reversed(snaps) if x.get("workers")), [])
        left = [] if not last.get("sessions") else [f"{last.get('sessions')} sessions on the server at the last snapshot"]
    ov = [w.get("overridden_tokens") for w in workers]
    wall_h = ((max(ends) - min(starts)).total_seconds() / 3600) if starts and ends else None
    n_trials = len(trials)
    report.update({
        "e0": {"calls": len(e0_share), "share_ge_99": (sum(s >= 0.99 for s in e0_share) / len(e0_share)) if e0_share else None,
               "mean_share": st.mean(e0_share) if e0_share else None, "miss_at_reply": e0_miss_at_reply,
               "miss_inside": e0_miss_inside},
        "gates": {"counters_from": source, "preemptions": preempt, "post_snip_calls": post_snip, "fallbacks": fallbacks,
                  "resets": resets,
                  "fallback_reset_share": (fallbacks + resets) / post_snip if post_snip else 0.0,
                  "tagged_lead_calls": tagged, "missing_server_records": missing_records,
                  "duplicate_server_records": duplicate_records, "ranks_overridden_tokens": ov,
                  "ranks_consistent": len(set(ov)) == 1 if ov else None, "sessions_left": left,
                  "server_errors": errors_by, "decisions_missing": dec_missing, "decisions_leaked": dec_leaked,
                  "decision_errors": dec_errors, "decision_examples": dec_examples},
        "cost": {"trials": n_trials, "wall_h": wall_h,
                 "gpu_h_per_trial": (wall_h * a.gpus / n_trials) if wall_h and n_trials else None}})
    g = report["gates"]
    e0 = report["e0"]
    report["pass"] = {
        "E0_append_only": e0["share_ge_99"] is not None and e0["share_ge_99"] >= 0.99,
        "no_preemptions": preempt == 0,
        "fallback_reset_lt_5pct": g["fallback_reset_share"] < 0.05,
        "one_record_per_call": missing_records == 0 and duplicate_records == 0,
        "ranks_consistent": bool(g["ranks_consistent"]),
        "no_sessions_left": not left,
        "one_dec_per_edited_call": dec_missing == 0 and dec_leaked == 0,
        "no_decision_errors": dec_errors == 0}
    if report.get("div_examples"):
        try:
            from tokenizers import Tokenizer
            tk = Tokenizer.from_file(sorted(glob.glob(f"{a.model_dir}/snapshots/*/tokenizer.json"))[0])
            for ex in report["div_examples"]:
                ex["old_text"] = tk.decode(ex["old"], skip_special_tokens=False)
                ex["new_text"] = tk.decode(ex["new"], skip_special_tokens=False)
        except Exception as exc:                                    # noqa: BLE001
            report["div_decode_error"] = repr(exc)
    out = Path(a.out) if a.out else ROOT / "runs" / "epic" / f"gates_{a.server}_{a.phase}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report, indent=1, default=str))
    sys.exit(0 if all(report["pass"].values()) else 1)


if __name__ == "__main__":
    main()
