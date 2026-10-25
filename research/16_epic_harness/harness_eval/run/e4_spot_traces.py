#!/usr/bin/env python3
"""Spot traces of e4's table (plan §5.6): for 2 runs per arm and benchmark (the first two analysis trials by block and
task), every column's per-run input traced back to its raw lines -- the trial's result.json, the agent's lead calls and
the server records they join -- call by call. Writes runs/epic/e4_spot_traces.md.

    .venv/bin/python run/e4_spot_traces.py [--json report/e4_minimax-m2.7-epic.json] [--per 2]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
sys.path.insert(0, str(ROOT / "report"))
import e4_report as rep  # noqa: E402
import latency_model as lm  # noqa: E402


def ts(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "report" / f"e4_{rep.SERVER}.json"))
    ap.add_argument("--per", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "runs" / "epic" / "e4_spot_traces.md"))
    a = ap.parse_args()
    js = json.loads(Path(a.json).read_text())
    log = ROOT / js["log"]["path"]
    picks = []
    for bench, b in js["benches"].items():
        for arm in b["table"]:
            ts_ = [x for x in b["trial_set"] if x["arm"] == arm][:a.per]
            picks += [(bench, arm, x) for x in ts_]
    specs = {x["spec"] for _b, _a, x in picks}
    recs: dict[str, list] = {}
    with open(log, "rb") as f:
        data = f.read(js["log"]["bytes"])
    for raw in data.splitlines():
        if b'"spec"' not in raw:
            continue
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if r.get("spec") in specs:
            recs.setdefault(r["spec"], []).append(r)
    lines = ["# e4 spot traces", "",
             f"Generated from `{Path(a.json).relative_to(ROOT)}` (log `{js['log']['path']}`, first {js['log']['bytes']:,} bytes, "
             f"sha256 `{js['log']['sha256'][:16]}…`). Per run: the joined lead calls (agent `calls.jsonl` record → the last "
             "non-aborted server record of (spec, call)), the charged tokens and their source field, the time model's price "
             "of each call, the post-snip flag, then the run's totals as they enter the table's per-run means. Outcomes "
             "(reward) are shown because the run is over.", ""]
    for bench, arm, x in picks:
        b = js["benches"][bench]
        beta = b["fit"]["beta"]
        row_cells = b["table"][arm]["cells"]
        tdir = ROOT / "jobs" / f"{rep.SERVER}+{arm}" / bench / x["job"] / x["spec"]
        res = json.loads((tdir / "result.json").read_text())
        ae = res.get("agent_execution") or {}
        wall = ts(ae["finished_at"]) - ts(ae["started_at"])
        reward = ((res.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        rr = [r for r in recs.get(x["spec"], []) if r.get("ev") == "call" and not r.get("aborted")]
        flags = rep.post_snip_flags(rr, arm not in ("prefix", "prefix_aa"))
        last: dict[int, dict] = {}
        for r in rr:
            k = int(r["call"])
            if k not in last or float(r.get("t") or 0) >= float(last[k].get("t") or 0):
                last[k] = r
        calls = [c for c in rep.load_jsonl(tdir / "agent" / "s15" / "calls.jsonl")
                 if c.get("purpose") == "lead" and c.get("epic") and c.get("response") is not None]
        lines += [f"## {bench} · {rep.LABEL.get(arm, arm)} · block {x['block']} · {x['task']}", "",
                  f"- trial `{x['spec']}` (job `{x['job']}`): reward {reward} → success {x['success']}; s15 status "
                  f"{x['status']}; agent wall {wall:.1f} s",
                  f"- table row (all 90 runs of this arm): runs {row_cells['runs']['text']}, calls {row_cells['calls']['text']}, "
                  f"computed tok / call {row_cells['c_call']['text']}, / run {row_cells['c_run']['text']}, LLM {row_cells['llm_s']['text']}, "
                  f"tool {row_cells['tool_s']['text']}, job {row_cells['job_s']['text']}", "",
                  "| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        tot_c = tot_p = tot_llm = tot_ms = post = 0
        for c in calls:
            k = int(c["epic"]["n"])
            r = last.get(k)
            if r is None:
                lines.append(f"| {k} | — | — | no server record | | {c.get('ms')} | | | | |")
                continue
            ch = rep.charged(r)
            src = ("P−apc_hit (fallback)" if r.get("fallback") else "ideal_C" if "ideal_C" in r else
                   "C_charged" if "C_charged" in r else "C")
            out = int((c.get("usage") or {}).get("output_tokens") or 0)
            ms = lm.price(beta, ch, int(r.get("P") or 0), out)
            tot_c += ch
            tot_p += int(r.get("P") or 0)
            tot_llm += ms
            tot_ms += float(c.get("ms") or 0)
            post += int(flags.get(k, False))
            sh = r.get("shadow") or {}
            kl = f"{sh['first_kl']:.4f}" if sh and not sh.get("floor") and isinstance(sh.get("first_kl"), (int, float)) else ""
            d = r.get("dec")
            dt = (f"{d['jsd']:.4f}, {d['u']:.3f}, {d['recompute']}, {d['C_O']}, {d['C_E']}" if isinstance(d, dict) and "jsd" in d
                  else (f"error: {d.get('error')}" if isinstance(d, dict) else ""))
            lines.append(f"| {k} | {r.get('P')} | {ch} | {src} | {out} | {float(c.get('ms') or 0):.0f} | {ms:.0f} | "
                         f"{'yes' if flags.get(k) else ''} | {kl} | {dt} |")
        tool = max(0.0, wall - tot_ms / 1000)
        lines += ["", f"Run totals: calls {len([c for c in calls if int(c['epic']['n']) in last])}; charged {tot_c:,} of "
                      f"{tot_p:,} prompt tokens ({tot_c / max(1, tot_p):.1%}); model LLM time {tot_llm / 1000:.1f} s; tool+harness "
                      f"max(0, {wall:.1f} − {tot_ms / 1000:.1f}) = {tool:.1f} s; model job time {tot_llm / 1000 + tool:.1f} s; "
                      f"post-snip calls {post}.", ""]
    Path(a.out).write_text("\n".join(lines) + "\n")
    print(f"{len(picks)} runs traced → {a.out} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
