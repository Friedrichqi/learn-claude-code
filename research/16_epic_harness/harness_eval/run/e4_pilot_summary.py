#!/usr/bin/env python3
"""Process-only summary of e4's pilot (phase e4p): what the main run's settings and budget rest on. Reads no reward.

    .venv/bin/python run/e4_pilot_summary.py [--phase e4p] [--since EPOCH]

Per benchmark and arm: trials, agent minutes per trial, lead calls and curated calls per trial, charged computed
tokens per call. For the recompute arm: decisions, JSD (mean, median, p90), the decision floor (a duplicate E probe),
top-128 mass, probe time, recompute rate, the decision overhead. Server: preemptions, KV use, host pool, errors (the
health snapshots since --since). Cost: GPU-hours of the phase's server runs and the projection for the main run.
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
import e4_report as rep  # noqa: E402
import gpu_hours  # noqa: E402

SERVER = "minimax-m2.7-epic"
ARMS = rep.ARMS


def ts(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp() if x else None


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="e4p")
    ap.add_argument("--since", type=float, default=0.0, help="the pilot server's start (epoch) for the health snapshots")
    ap.add_argument("--main-trials", default="swe=630,swepro=630,tbench=630")
    a = ap.parse_args()
    out = {"phase": a.phase, "benches": {}}
    specs = {}
    for bench in ("swe", "swepro", "tbench"):
        for arm in ARMS:
            for res in glob.glob(str(ROOT / "jobs" / f"{SERVER}+{arm}" / bench / f"{a.phase}r*__*" / "*__*" / "result.json")):
                specs[Path(res).parent.name] = (bench, arm, Path(res).parent)
    recs: dict[str, list] = {}
    errors = []
    with open(ROOT / "runs" / "epic" / f"{SERVER}.calls.jsonl", "rb") as f:
        for raw in f:
            if b'"spec"' not in raw:
                continue
            try:
                x = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if x.get("spec") in specs:
                (errors if x.get("ev") == "error" else recs.setdefault(x["spec"], [])).append(x)
    for bench in ("swe", "swepro", "tbench"):
        b = out["benches"][bench] = {}
        for arm in ARMS:
            rows = [(s, d) for s, (bb, aa, d) in specs.items() if bb == bench and aa == arm]
            mins, leads, cur, ch, n_calls, decs = [], [], [], 0, 0, []
            for spec, d in rows:
                r = json.loads((d / "result.json").read_text())
                ae = r.get("agent_execution") or {}
                if ae.get("finished_at"):
                    mins.append((ts(ae["finished_at"]) - ts(ae["started_at"])) / 60)
                rr = [x for x in recs.get(spec, []) if x.get("ev") == "call" and not x.get("aborted")]
                leads.append(len(rr))
                flags = rep.post_snip_flags(rr, arm not in ("prefix", "prefix_aa"))
                cur.append(sum(flags.values()))
                for x in rr:
                    ch += rep.charged(x)
                    n_calls += 1
                    if isinstance(x.get("dec"), dict):
                        decs.append(x["dec"])
            e = {"trials": len(rows), "finished": len(mins), "agent_min_mean": st.mean(mins) if mins else None,
                 "agent_min_max": max(mins) if mins else None, "lead_calls_per_trial": st.mean(leads) if leads else None,
                 "curated_per_trial": st.mean(cur) if cur else None,
                 "charged_per_call": ch / n_calls if n_calls else None}
            if decs:
                ok = [x for x in decs if not x.get("error")]
                js = [x["jsd"] for x in ok]
                fl = [x["floor"]["jsd"] for x in ok if isinstance(x.get("floor"), dict)]
                e["decisions"] = {
                    "n": len(decs), "errors": len(decs) - len(ok), "same": sum(1 for x in ok if x.get("same")),
                    "jsd_mean": st.mean(js) if js else None, "jsd_median": st.median(js) if js else None,
                    "jsd_p90": q(js, 0.9), "floor_jsd_mean": st.mean(fl) if fl else None, "floor_n": len(fl),
                    "mass_o_mean": st.mean(x["mass_o"] for x in ok if "mass_o" in x) if any("mass_o" in x for x in ok) else None,
                    "mass_e_mean": st.mean(x["mass_e"] for x in ok if "mass_e" in x) if any("mass_e" in x for x in ok) else None,
                    "ms_mean": st.mean(x["ms"] for x in ok) if ok else None, "ms_p90": q([x["ms"] for x in ok], 0.9),
                    "recompute_rate": sum(1 for x in ok if x["recompute"]) / len(ok) if ok else None,
                    "C_O_mean": st.mean(x["C_O"] for x in ok) if ok else None,
                    "C_E_mean": st.mean(x["C_E"] for x in ok) if ok else None,
                    "examples_error": [x.get("error") for x in decs if x.get("error")][:3]}
            b[arm] = e
    out["server_errors"] = {}
    for x in errors:
        out["server_errors"][x.get("where")] = out["server_errors"].get(x.get("where"), 0) + 1
    snaps = [json.loads(line) for line in (ROOT / "runs" / "epic" / f"health_{SERVER}.jsonl").read_text().splitlines()
             if line.strip()] if (ROOT / "runs" / "epic" / f"health_{SERVER}.jsonl").is_file() else []
    snaps = [s for s in snaps if float(s.get("t") or 0) >= a.since]
    kv = [float(s["kv"]) for s in snaps if isinstance(s.get("kv"), (int, float))]
    out["server"] = {"snapshots": len(snaps), "preemptions_last": snaps[-1].get("preemptions") if snaps else None,
                     "kv_p50": q(kv, 0.5), "kv_p90": q(kv, 0.9), "kv_max": max(kv) if kv else None,
                     "pool_used_max": max((s.get("pool_used") or 0) for s in snaps) if snaps else None,
                     "sessions_max": max((s.get("sessions") or 0) for s in snaps) if snaps else None}
    runs = [r for r in gpu_hours.servers() if r["name"] == SERVER and r["start"] >= a.since - 600]
    out["gpu_h"] = sum(r["gpu_hours"] for r in runs)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
