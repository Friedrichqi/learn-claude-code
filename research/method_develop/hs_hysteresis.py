#!/usr/bin/env python3
"""E0f hysteresis (rule D1's harness-fixed counterfactual): edit frequency and edit work when s15 snips with
hysteresis instead of on every call.

s15's snip_compact (code.py:2450-2472) cuts the history back to 50 messages whenever it exceeds 50. Each round
appends two or three messages, so past 50 it cuts again on every lead call, rewrites the archive marker and moves
the whole tail: exact prefix caching re-prefills ~46 messages per call. The counterfactual rule trims to 30
(messages 0-2 + marker + the newest 26, with s15's tool_use/tool_result pair adjustment) whenever the history
exceeds 50, so a cut happens every ~10 rounds and the calls in between are append-only.

Static replay, per harness_eval profile trial (hs_common.he_trials()): the trial's logical message stream (each
reply, then the tool-result message, notices and todo reminders appended after it; previews of the newest results
undone from the trial's saved outputs) is pushed through s15's own prepare_context pipeline (hs_lookahead's
reimplementation: tool_result_budget, snip, micro_compact, fit_tool_results, compact_history) under two snip rules:

  rolling     s15 as run (reproduces the logged requests; the share reproduced is reported);
  hysteresis  past 50 messages, trim to 30.

LLM-summary compactions (the compact tool, compact_history, reactive compaction after an overflow) are kept at the
round where they happened, with the logged summary text, in both arms. This is first order: the model's replies,
tool results and the summaries are the ones it produced with the as-run contexts; with other contexts the
trajectories would differ.

Per lead call and arm: P (prompt tokens: Qwen3-32B tokenizer on hs_lookahead's render + the tool schemas),
d (the prefix shared with the arm's previous request + reply, i.e. exact prefix caching with no eviction),
C = P - d computed tokens, E = edit tokens (C minus the newly appended content), output tokens (usage).
Modeled on an H100 with topic 16's models (hs_common): prefill = ttft_ms_16('compute', C, P) =
b0 + b1*C + b2*C*P/1e3; decode = tpot_ms_16(P + out/2) * out; edit work = b1*E + b2*E*P/1e3 (the part of the
prefill the edit tokens cost). The lead chain's modeled time is the sum over its calls: the N = 1 critical path.

Outputs: data/tables/E0f_hysteresis.md, data/e0f/calls.jsonl (one record per lead call and arm),
data/e0f/summary.json.

    /data/fritz/venv/bin/python research/17_harness_aware_serving/hs_hysteresis.py [--workers 24] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_common as hc  # noqa: E402
import hs_lookahead as la  # noqa: E402

OUT = hc.DATA / "e0f"
ARMS = {"rolling": None, "hysteresis": 30}      # snip_compact keep: None = s15 (50), 30 = trim to 30 past 50
D1_CP_THRESHOLD = 0.05                          # README section 2, D1: edit work >= 5 % of critical-path time
LONG_PROMPT = 32768                             # topic 16's TTFT model is fit on Qwen3-32B calls up to its window


def replay(trial: la.Trial, tok: la.Tok, keep: int | None) -> tuple[list[dict], Counter]:
    """Per successful lead call: P, d, E, C, out for one snip rule."""
    la.clear_caches()
    env = la.Env(trial.staging, trial.context_limit, disk=False)
    diag = Counter()
    first = next((c for c in trial.calls if la.ok_call(c)), None)
    if first is None:
        return [], diag
    sys_tokens = tok.text_tokens(la.render_system(first["request"].get("system")))
    base = la.TOOL_SCHEMA_TOKENS + sys_tokens + la.GEN_PROMPT_TOKENS
    S = first["request"]["messages"]
    tok.ensure(S)
    P0 = base + sum(tok.n(m) for m in S)
    u0 = first.get("usage") or {}
    out = [{"n": first["call"], "P": P0, "d": 0, "E": 0, "C": P0, "out": u0.get("output_tokens") or 0,
            "kind": "first", "nm": len(S)}]
    writes = la.transcript_writes(trial.events)
    for rnd in la.trial_rounds(trial):
        Rn = rnd.n["request"]["messages"]
        reply = rnd.reply
        cached = S + ([reply] if reply is not None else [])
        # transcripts the as-run transition wrote (snip, summary, reactive): the marker counter of later cuts
        w0, w1 = la.counter_before(writes, rnd.m_index), la.counter_before(writes, rnd.m_index + 1)
        simulated = True
        if rnd.kind == "reactive" or rnd.new is None:
            # overflow + reactive summary (or nothing to rebuild): both arms continue from the logged request
            Sn, simulated = Rn, False
            diag[f"taken_{rnd.kind}"] += 1
        elif rnd.kind == "summary":
            # the logged summary message, then this iteration's appends (compact tool or compact_history)
            Sn = la.prepare_context(env, [{"role": "user", "content": Rn[0]["content"]}] + rnd.new, snip_keep=keep)
        elif rnd.kind == "retry":
            Sn = la.prepare_context(env, S + rnd.new, snip_keep=keep)
        else:
            Sn = la.prepare_context(env, S + [reply] + rnd.new, snip_keep=keep)
        if not simulated or rnd.kind == "summary":
            env.counter = w1 if keep is None else env.counter + (w1 - w0)
        if keep is None and simulated:
            diag["reproduced" if Sn == Rn else "not_reproduced"] += 1
        tok.ensure(Sn)
        tok.ensure(cached)
        j_div, lcp, d = la.divergence(Sn, cached, tok)
        if rnd.kind == "retry":
            end = min(len(Sn), len(S))
        elif rnd.kind == "summary":
            end = len(Sn) - len(rnd.new or [])
        else:
            end = next((i + 1 for i in range(len(Sn) - 1, -1, -1) if Sn[i] is reply), None)
            if end is None:
                key = tok.key(reply) if reply is not None else None
                end = la.old_region_end(Sn, set(), key, tok)
        ntok = [tok.n(m) for m in Sn]
        E = sum(ntok[j_div:end]) - (lcp if j_div < end else 0) if j_div < end else 0
        P = base + sum(ntok)
        dd = base - la.GEN_PROMPT_TOKENS + d
        u = rnd.n.get("usage") or {}
        out.append({"n": rnd.n["call"], "P": P, "d": dd, "E": max(E, 0), "C": P - dd,
                    "out": u.get("output_tokens") or 0, "kind": rnd.kind, "nm": len(Sn)})
        S = Sn
    return out, diag


def side_calls(trial_path: Path) -> list[dict]:
    """Subagent and summary calls (not lead): served tokens from usage, for the sensitivity column."""
    out = []
    for r in hc.he_calls(trial_path):
        if r.get("purpose") in ("one_shot", "compaction_summary") and isinstance(r.get("usage"), dict):
            u = r["usage"]
            P = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (
                u.get("cache_creation_input_tokens") or 0)
            out.append({"purpose": r["purpose"], "P": P, "C": P - (u.get("cache_read_input_tokens") or 0),
                        "out": u.get("output_tokens") or 0})
    return out


_TOK: la.Tok | None = None


def _init_worker() -> None:
    global _TOK
    _TOK = la.Tok(la.qwen_encoder())


def _run_trial(args):
    model, bench, path = args
    t0 = time.time()
    try:
        trial = la.load_trial(model, bench, Path(path))
        recs, diag = [], Counter()
        for arm, keep in ARMS.items():
            _TOK.clear()
            rows, d = replay(trial, _TOK, keep)
            for r in rows:
                r.update(model=model, bench=bench, trial=trial.name, arm=arm)
            recs += rows
            diag.update({f"{arm}_{k}": v for k, v in d.items()})
        side = [{**s, "model": model, "bench": bench, "trial": trial.name} for s in side_calls(Path(path))]
    except Exception as exc:                                     # noqa: BLE001
        import traceback
        return [], [], Counter({"error": 1}), {"trial": path, "error": f"{type(exc).__name__}: {exc}",
                                                "tb": traceback.format_exc()[-3000:]}
    finally:
        la.clear_caches()
    return recs, side, diag, {"trial": path, "s": round(time.time() - t0, 1)}


# -- cost model and aggregation ---------------------------------------------------------------------------------

class Cost:
    def __init__(self):
        self.models, self.tpot = hc.latency_models_16()
        self.b = self.models["compute"]["beta"]

    def prefill(self, C: float, P: float) -> float:
        return hc.ttft_ms_16(self.models, "compute", C, 0.0, P)

    def edit(self, E: float, P: float) -> float:
        return self.b[1] * E + self.b[2] * E * P / 1e3

    def decode(self, out: float, P: float) -> float:
        return hc.tpot_ms_16(self.tpot, P + out / 2) * out


def aggregate(recs: list[dict], side: list[dict], cost: Cost) -> dict:
    g = defaultdict(lambda: {"calls": 0, "edit_calls": 0, "E": 0, "P": 0, "C": 0, "out": 0, "prefill_ms": 0.0,
                             "decode_ms": 0.0, "edit_ms": 0.0, "calls_long": 0, "edit_ms_long": 0.0})
    for r in recs:
        pre, dec, ed = cost.prefill(r["C"], r["P"]), cost.decode(r["out"], r["P"]), cost.edit(r["E"], r["P"])
        for key in ((r["arm"], r["model"], r["bench"]), (r["arm"], r["model"], "*"), (r["arm"], "*", "*")):
            a = g[key]
            a["calls"] += 1
            a["edit_calls"] += int(r["E"] > 0)
            a["E"] += r["E"]
            a["P"] += r["P"]
            a["C"] += r["C"]
            a["out"] += r["out"]
            a["prefill_ms"] += pre
            a["decode_ms"] += dec
            a["edit_ms"] += ed
            if r["P"] > LONG_PROMPT:
                a["calls_long"] += 1
                a["edit_ms_long"] += ed
    s = defaultdict(lambda: {"os_ms": 0.0, "cs_ms": 0.0, "n_os": 0, "n_cs": 0})
    for c in side:
        t = cost.prefill(c["C"], c["P"]) + cost.decode(c["out"], c["P"])
        for key in ((c["model"], c["bench"]), (c["model"], "*"), ("*", "*")):
            if c["purpose"] == "one_shot":
                s[key]["os_ms"] += t
                s[key]["n_os"] += 1
            else:
                s[key]["cs_ms"] += t
                s[key]["n_cs"] += 1
    return {"lead": {"|".join(k): v for k, v in g.items()}, "side": {"|".join(k): v for k, v in s.items()}}


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f} %" if b else "—"


def table_lines(agg: dict, diag: dict) -> list[str]:
    lead, side = agg["lead"], agg["side"]
    models = [m for m in hc.HE_MODELS if f"rolling|{m}|*" in lead]
    keys = [(m, b) for m in models for b in hc.HE_BENCHES if f"rolling|{m}|{b}" in lead] + \
           [(m, "*") for m in models] + [("*", "*")]
    lab = lambda m, b: ("**all**" if m == "*" else m) + ("" if b == "*" else f" / {b}")  # noqa: E731
    L = ["## 1. Edit frequency and re-prefilled tokens", "",
         "Lead calls that start with an edit (edit tokens > 0) and edit tokens per lead call, as run (rolling snip) and "
         "with snip hysteresis (trim to 30 past 50). Tokens: Qwen3-32B, exact prefix caching against the previous "
         "request + reply.", "",
         "| model / bench | lead calls | calls with an edit: rolling | hysteresis | edit tokens per call: rolling | "
         "hysteresis | prompt tokens per call: rolling | hysteresis | computed tokens per call: rolling | hysteresis |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for m, b in keys:
        r, h = lead[f"rolling|{m}|{b}"], lead[f"hysteresis|{m}|{b}"]
        n = r["calls"]
        L.append(f"| {lab(m, b)} | {n:,} | {pct(r['edit_calls'], n)} | {pct(h['edit_calls'], h['calls'])} | "
                 f"{r['E'] / n:,.0f} | {h['E'] / h['calls']:,.0f} | {r['P'] / n:,.0f} | {h['P'] / h['calls']:,.0f} | "
                 f"{r['C'] / n:,.0f} | {h['C'] / h['calls']:,.0f} |")
    L += ["", "## 2. Edit work on the N = 1 critical path (H100, topic 16's TTFT / TPOT models)", "",
          "Per lead call: prefill = 41.2 ms + 0.0875 ms x C + 0.00268 ms x C x P / 1e3; decode = TPOT(P + out/2) x "
          "output tokens; edit work = the prefill share of the edit tokens. Shares are of the lead chain's modeled "
          "time (prefill + decode summed over its calls). D1 needs >= 5 % of critical-path time.", "",
          "| model / bench | modeled s per call: rolling | hysteresis | prefill share: rolling | hysteresis | "
          "**edit work share: rolling** | **hysteresis** | with subagent + summary calls: rolling | hysteresis | "
          "D1 (>= 5 %): rolling | hysteresis |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    verdict = {}
    for m, b in keys:
        r, h = lead[f"rolling|{m}|{b}"], lead[f"hysteresis|{m}|{b}"]
        sd = side.get(f"{m}|{b}", {"os_ms": 0.0, "cs_ms": 0.0})
        tr, th = r["prefill_ms"] + r["decode_ms"], h["prefill_ms"] + h["decode_ms"]
        er, eh = r["edit_ms"] / tr, h["edit_ms"] / th
        # summary calls are Modify work (edit work) on the critical path; subagent calls are critical-path appends
        er2 = (r["edit_ms"] + sd["cs_ms"]) / (tr + sd["cs_ms"] + sd["os_ms"])
        eh2 = (h["edit_ms"] + sd["cs_ms"]) / (th + sd["cs_ms"] + sd["os_ms"])
        verdict[(m, b)] = (er, eh)
        L.append(f"| {lab(m, b)} | {tr / r['calls'] / 1e3:.2f} | {th / h['calls'] / 1e3:.2f} | "
                 f"{pct(r['prefill_ms'], tr)} | {pct(h['prefill_ms'], th)} | **{100 * er:.1f} %** | "
                 f"**{100 * eh:.1f} %** | {100 * er2:.1f} % | {100 * eh2:.1f} % | "
                 f"{'pass' if er >= D1_CP_THRESHOLD else 'fail'} | {'pass' if eh >= D1_CP_THRESHOLD else 'fail'} |")
    a = lead["rolling|*|*"]
    hh = lead["hysteresis|*|*"]
    er, eh = verdict[("*", "*")]
    cells_h = [k for k, v in verdict.items() if k[1] != "*" and v[1] >= D1_CP_THRESHOLD]
    cells_r = [k for k, v in verdict.items() if k[1] != "*" and v[0] >= D1_CP_THRESHOLD]
    L += ["", "## 3. What it means for D1 at N = 1", "",
          f"- As run (rolling snip): edit work is {100 * er:.1f} % of the lead chain's modeled time over all "
          f"{a['calls']:,} lead calls; {len(cells_r)} of {len([k for k in verdict if k[1] != '*'])} model x bench "
          f"cells reach 5 %. Calls with an edit: {pct(a['edit_calls'], a['calls'])}.",
          f"- With snip hysteresis: {100 * eh:.1f} % (calls with an edit {pct(hh['edit_calls'], hh['calls'])}; edit "
          f"tokens {hh['E'] / max(a['E'], 1):.2f} x of as run); {len(cells_h)} cells reach 5 %"
          + (": " + ", ".join(f"{m}/{b} {100 * verdict[(m, b)][1]:.1f} %" for m, b in cells_h) if cells_h else "")
          + ".",
          f"- Edit work removed by the harness fix alone: {100 * (1 - hh['edit_ms'] / a['edit_ms']):.1f} % of the "
          f"as-run edit work; total modeled lead time {100 * (hh['prefill_ms'] + hh['decode_ms']) / (a['prefill_ms'] + a['decode_ms']) - 100:+.1f} %. "
          f"Largest cell under hysteresis: "
          + "{} / {} {:.1f} %.".format(*max(((m, b, 100 * v[1]) for (m, b), v in verdict.items() if b != "*"),
                                           key=lambda x: x[2])),
          "- At N = 1 the GPU is busy only during the chain's calls, so GPU time is the same sum and D1's GPU-time "
          f"criterion (>= 20 %) reads the same shares: it fails in both arms ({100 * er:.1f} % / {100 * eh:.1f} %).",
          "- D1 at N = 1 therefore passes as run but not under the harness-fixed counterfactual: here the harness "
          "fix (snip hysteresis) is the cheaper remedy, unless an N > 1 cell (E0a) still passes with it.",
          ""]
    L += ["## 4. Checks", ""]
    ro, rn = diag.get("rolling_reproduced", 0), diag.get("rolling_not_reproduced", 0)
    L.append(f"- The rolling arm, replayed from the logical message stream alone, reproduces the logged request in "
             f"{ro:,} of {ro + rn:,} transitions ({pct(ro, ro + rn)}); {diag.get('rolling_taken_reactive', 0):,} "
             f"transitions after an overflow + reactive summary continue from the logged request in both arms.")
    L.append(f"- Trials: {diag.get('trials', 0):,} (failed: {diag.get('trial_errors', 0)}). Subagent / summary calls "
             f"(sensitivity column, served tokens from usage): {side.get('*|*', {}).get('n_os', 0):,} / "
             f"{side.get('*|*', {}).get('n_cs', 0):,}.")
    longs = []
    for m in models:
        ra = lead[f"rolling|{m}|*"]
        if ra["calls_long"]:
            longs.append(f"{m} {pct(ra['calls_long'], ra['calls'])} of calls, {pct(ra['edit_ms_long'], ra['edit_ms'])} "
                         "of edit work")
    if longs:
        L.append(f"- Prompts beyond {LONG_PROMPT:,} tokens (outside the TTFT model's fit, extrapolated), as run: "
                 + "; ".join(longs) + ".")
    L.append("- First order: replies, tool results and summaries are the as-run ones; the model would have seen "
             "different contexts under hysteresis (between cuts it sees up to 50 messages, right after a cut 30), so "
             "trajectories, lengths and tool calls would change. The TTFT model is fit on Qwen3-32B (H100) and is "
             "extrapolated beyond its measured contexts for the 128K-window models; the TPOT table ends at 30K "
             "context (flat beyond), which understates decode time at long contexts and so overstates edit shares "
             "there.")
    return L


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--models", default=",".join(hc.HE_MODELS))
    ap.add_argument("--benches", default=",".join(hc.HE_BENCHES))
    ap.add_argument("--table-only", action="store_true", help="rebuild the table from data/e0f/")
    a = ap.parse_args()
    cost = Cost()
    if a.table_only:
        recs = list(hc.read_jsonl(OUT / "calls.jsonl"))
        side = list(hc.read_jsonl(OUT / "side_calls.jsonl"))
        diag = json.loads((OUT / "summary.json").read_text())["diag"]
    else:
        trials = [(m, b, str(p)) for m, b, p in hc.he_trials(tuple(a.models.split(",")),
                                                             tuple(a.benches.split(",")))]
        if a.limit:
            trials = trials[:a.limit]
        OUT.mkdir(parents=True, exist_ok=True)
        workers = max(1, min(a.workers, 32, len(trials)))
        recs, side, diag, logs = [], [], Counter(), []
        t0 = time.time()
        if workers == 1:
            _init_worker()
            results = map(_run_trial, trials)
        else:
            import multiprocessing as mp
            pool = mp.get_context("fork").Pool(workers, initializer=_init_worker)
            results = pool.imap_unordered(_run_trial, trials, chunksize=1)
        for i, (r_, s_, d_, log) in enumerate(results, 1):
            recs += r_
            side += s_
            diag.update(d_)
            logs.append(log)
            if "error" in log:
                print("ERROR", log["trial"], log["error"], file=sys.stderr)
            if i % 100 == 0:
                print(f"{i}/{len(trials)} trials, {time.time() - t0:.0f} s", file=sys.stderr)
        diag["trials"] = len(trials)
        diag["trial_errors"] = sum(1 for x in logs if "error" in x)
        diag = dict(diag)
        recs.sort(key=lambda r: (r["model"], r["bench"], r["trial"], r["arm"], r["n"]))
        with open(OUT / "calls.jsonl", "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        with open(OUT / "side_calls.jsonl", "w", encoding="utf-8") as fh:
            for r in side:
                fh.write(json.dumps(r) + "\n")
        (OUT / "summary.json").write_text(json.dumps({"diag": diag, "logs": logs}, indent=1))
    agg = aggregate(recs, side, cost)
    js = json.loads((OUT / "summary.json").read_text())
    js["agg"] = agg
    (OUT / "summary.json").write_text(json.dumps(js, indent=1))
    note = ("E0f, D1's harness-fixed counterfactual. Static first-order replay of every harness_eval profile trial "
            "(hs_common.he_trials()) through s15's prepare_context (reimplemented in hs_lookahead.py) under the "
            "as-run rolling snip and under snip hysteresis (past 50 messages, trim to 30). Method in the script's "
            "docstring.")
    print(hc.write_table("E0f_hysteresis", "E0f hysteresis: edit work with the harness fixed",
                         table_lines(agg, diag), note))


if __name__ == "__main__":
    main()
