#!/usr/bin/env python3
"""Post hoc, not pre-registered (2026-11-03): where an LLM call's time and a task's time go in each main run.

  - LLM calls, at the proxy: gate wait (t_admit - t_in) and server time (t_done - t_admit), for the agents' calls that
    start in the full-N window (no save_context re-sends; agate's controller calls skip the proxy). The agent-seen
    latency of et_analyze.py is shown next to it, with and without lmc's re-sends.
  - Inside vLLM: its histograms' sum and count over the window, so averages only: e2e, queue, TTFT, prefill, decode,
    inter-token latency, tokens per request. They count every request vLLM served, the harness's own included.
  - Tasks: entry e2e = active time + gate-held time, the avg and p50 of each part.

    python research/23_efficiency_tools/et_latency.py      # data/tables/latency.{md,json}
"""
from __future__ import annotations

import json

import et_analyze as E

A = E.A
RUNS = ["none", "none_r2", "gate8", "gate16", "agate", "wait", "lmc", "lmc_all"]
HIST = {"e2e": "vllm:e2e_request_latency_seconds", "queue": "vllm:request_queue_time_seconds",
        "ttft": "vllm:time_to_first_token_seconds", "prefill": "vllm:request_prefill_time_seconds",
        "decode": "vllm:request_decode_time_seconds", "itl": "vllm:inter_token_latency_seconds",
        "prompt_tok": "vllm:request_prompt_tokens", "computed_tok": "vllm:request_prefill_kv_computed_tokens",
        "gen_tok": "vllm:request_generation_tokens"}


def hist_means(segs: list[list[dict]], lo: float, hi: float) -> dict:
    """Each histogram's mean over the window: (sum, count) deltas between a segment's first and last record in it."""
    acc = {k: [0.0, 0.0] for k in HIST}
    for seg in segs:
        rows = [r for r in seg if lo <= r["wall"] <= hi]
        if len(rows) < 2:
            continue
        a, b = rows[0]["m"], rows[-1]["m"]
        for k, name in HIST.items():
            vals = [m.get(name + s) for m in (a, b) for s in ("_sum", "_count")]
            if None not in vals:
                acc[k][0] += vals[2] - vals[0]
                acc[k][1] += vals[3] - vals[1]
    return {**{k: (s / c if c else None) for k, (s, c) in acc.items()}, "requests": acc["e2e"][1]}


def run(name: str) -> dict:
    trials = E.load(name)
    win = A.window(trials, E.N)
    lo, hi = win["w0"], win["w1"]
    held = E.gate_held(name)
    rows = [r for r in A.read_jsonl(E.RUNS / name / "proxy_requests.jsonl")
            if r.get("status") == 200 and not r.get("store") and r.get("t_admit") is not None and lo <= r["t_in"] <= hi]
    calls = [c for t in trials for c in t["calls"] if lo <= c["t"] <= hi]
    ended = [t for t in trials if t["e2e"] is not None]
    return {
        "agent_seen": A.summary([c["ms"] / 1000 for c in calls]),
        "agent_seen_no_saves": A.summary([c["ms"] / 1000 for c in calls if not c.get("save")]),
        "proxy_calls": len(rows),
        "gate_wait": A.summary([r["t_admit"] - r["t_in"] for r in rows]),
        "held_1s_share": (sum(r["t_admit"] - r["t_in"] >= 1.0 for r in rows) / len(rows)) if rows else None,
        "server": A.summary([r["t_done"] - r["t_admit"] for r in rows]),
        "vllm": hist_means(E.load_hw(name), lo, hi),
        "entry": A.summary([t["e2e"] for t in ended]),
        "active": A.summary([t["e2e"] - held.get(t["trial"], 0.0) for t in ended]),
        "gate_per_task": A.summary([held.get(t["trial"], 0.0) for t in ended]),
    }


def f(x, nd: int = 1) -> str:
    return "–" if x is None else f"{x:,.{nd}f}"


def main() -> int:
    out = {name: run(name) for name in RUNS}
    (E.TABLES / "latency.json").write_text(json.dumps(out, indent=1))
    md = ["# Topic 23: where an LLM call's and a task's time goes (et_latency.py; post hoc, not pre-registered)", "",
          "Seconds. Calls that start in the full-N window; tasks: all 96. The agent-seen columns are et.md's.", "",
          "## LLM calls", "",
          "| setup | agent-seen avg | p50 | p95 | p99 | without save re-sends: avg | p50 | at the proxy: gate wait avg "
          "| p50 | p95 | calls held ≥ 1 s | server avg | p50 | p95 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in out.items():
        a, s, g, v = r["agent_seen"], r["agent_seen_no_saves"], r["gate_wait"], r["server"]
        md.append(f"| {n} | {f(a['avg'])} | {f(a['p50'])} | {f(a['p95'])} | {f(a['p99'])} | {f(s['avg'])} | "
                  f"{f(s['p50'])} | {f(g['avg'])} | {f(g['p50'])} | {f(g['p95'])} | {r['held_1s_share']:.1%} | "
                  f"{f(v['avg'])} | {f(v['p50'])} | {f(v['p95'])} |")
    md += ["", "## Inside vLLM (its histograms over the window: averages over every request it served, the harness's "
           "own included)", "",
           "| setup | requests | e2e | queue | TTFT | prefill | decode | inter-token (ms) | prompt tokens | computed "
           "tokens | generated tokens |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in out.items():
        m = r["vllm"]
        md.append(f"| {n} | {m['requests']:,.0f} | {f(m['e2e'])} | {f(m['queue'], 2)} | {f(m['ttft'], 2)} | "
                  f"{f(m['prefill'], 2)} | {f(m['decode'])} | {f(m['itl'] * 1000)} | {f(m['prompt_tok'], 0)} | "
                  f"{f(m['computed_tok'], 0)} | {f(m['gen_tok'], 0)} |")
    md += ["", "## Tasks (entry e2e = active time + gate-held time)", "",
           "| setup | entry avg | p50 | active avg | p50 | gate-held avg | p50 |", "|---|---|---|---|---|---|---|"]
    for n, r in out.items():
        e, a, g = r["entry"], r["active"], r["gate_per_task"]
        md.append(f"| {n} | {f(e['avg'], 0)} | {f(e['p50'], 0)} | {f(a['avg'], 0)} | {f(a['p50'], 0)} | "
                  f"{f(g['avg'], 0)} | {f(g['p50'], 0)} |")
    (E.TABLES / "latency.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
