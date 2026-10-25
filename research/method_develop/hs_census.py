#!/usr/bin/env python3
"""E0a census at N = 1 (input to D1): what share of the lead chain's time is edit work, per source and model.

Sources (read-only):
  he    harness_eval profile trials (hc.he_trials(): q-phase, 250 lead calls): s15 on SWE-bench Verified / Pro and
        Terminal-Bench with qwen3-32b, qwen3.8-27b, deepseek-v4-flash, minimax-m2.7 on vLLM 0.31. Its servers ran
        several lanes each, so only its tokens are used; time is priced with topic 16's H100 models.
  t16   topic 16's main sweep, prefix and prefix_aa arms (R1, Qwen3-32B, one H100, two sessions at a time):
        runs/*/*.requests.jsonl.xz (+ server_calls*.jsonl). The EPIC arms' live plans and the E-A replays of recorded
        prefix runs give the exact edit work, against which the estimate below is validated.

Usage semantics (vLLM 0.31's Anthropic endpoint; checked in the table's first section): prompt P = input_tokens +
cache_read + cache_creation; computed U = input_tokens + cache_creation (creation = tokens computed and written to
the prefix cache as full blocks; input_tokens = the computed tail that fills no block).

Per call, a generic record (RECORD_FIELDS; built by `session_records`, priced by `N1Pricer`, summed by `census`):
  new     tokens of the prompt units (messages, system, tools) that are not in the previous call of the same chain
          (its prompt + its reply): a message-level diff, then characters -> tokens with a per-session fit
          P = a + rho * chars(messages), plus TAU tokens per message.
  edit    the brief's estimator: max(0, U - new) = tokens the server computed before but recomputes because the
          prompt diverged before the newly appended content (validated against topic 16, section 4 of the table).
  edited  a structural edit: some unit of the previous prompt + reply comes back after the first divergence
          (snip, placeholder, summary, fit). `edit` on calls without one is re-prefill of the previous reply
          (template re-render, decode state not cached), block granularity and eviction under contention.
Time at N = 1 (topic 16's models): prefill = ttft_ms_16(compute, computed=U, loaded=0, prompt=P); decode = out x
tpot_ms_16(P + out/2); the edit part of a prefill is its marginal cost (b1 + b2 P / 1e3) x edit. The lead chain
(critical path at N = 1) is every lead, one_shot and compaction_summary call; side calls only add GPU time.
Edit work (README section 1) = edit prefill + the whole time of Modify calls (compaction summaries).

    /data/fritz/venv/bin/python research/17_harness_aware_serving/hs_census.py [--workers 24] [--sources he,t16]

Writes data/e0a/calls_<src>.jsonl (one record per call), data/e0a/validation_t16.jsonl, data/tables/E0a_census_n1.md.
Another source (e.g. topic 11's scheduler logs at N > 1) only has to produce the same records: `census(records,
pricer)` takes any pricer that returns the per-call split in ms.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_common as hc  # noqa: E402

E0A = hc.DATA / "e0a"
TAU = 5.0                     # template tokens per message (role header, end marker), on top of the characters
RHO_FALLBACK = 0.23           # tokens per character for sessions too short to fit
RHO_RANGE = (0.08, 0.6)
CHAIN = ("lead", "one_shot", "workflow_agent", "compaction_summary")   # the lead blocks on these (critical path)
MODIFY = ("compaction_summary",)                                         # Modify calls: their whole time is edit work
RECORD_FIELDS = ("src", "model", "bench", "session", "idx", "call", "purpose", "eff", "urg", "chain", "t", "ms",
                 "served", "P", "cached", "U", "uin", "ucr", "out", "Pprev", "new", "moved", "edit", "edited", "kinds",
                 "stop", "tool_uses", "rho", "rho_fit")
CMD_CHARS = 400               # bash command prefix kept per tool use (E0c classifies windows by it)


# -- one session -> records ------------------------------------------------------------------------------------

def usage_tokens(u: dict | None) -> tuple[int, int, int, int, int, int]:
    """(P, cached, U, input, creation, out) of an Anthropic-style usage block."""
    u = u or {}
    inp, rd = int(u.get("input_tokens") or 0), int(u.get("cache_read_input_tokens") or 0)
    cr, out = int(u.get("cache_creation_input_tokens") or 0), int(u.get("output_tokens") or 0)
    return inp + rd + cr, rd, inp + cr, inp, cr, out


def _key(role: str, text: str) -> str:
    return role + ":" + hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=8).hexdigest()


def units(req: dict) -> list[tuple[str, int, bool]]:
    """(key, characters, is_message) per prompt unit: the system prompt, the tool list, then each message."""
    out = [(_key("system", req.get("system") or ""), len(req.get("system") or ""), False)]
    tools = json.dumps(req.get("tools") or [], sort_keys=True)
    out.append((_key("tools", tools), len(tools), False))
    for m in req.get("messages") or []:
        text = json.dumps(m.get("content"), sort_keys=True, ensure_ascii=False)
        out.append((_key(m.get("role") or "?", text), len(text), True))
    return out


def reply_unit(content) -> tuple[str, int, bool]:
    text = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return _key("assistant", text), len(text), True


def diff_labels(prev: list[str], cur: list[str]) -> list[str]:
    """'prefix' / 'moved' / 'fresh' per unit of `cur` against `prev` (the previous prompt + its reply): a leading
    run of identical units is the exact prefix; units of `prev` that come back after the first divergence moved;
    everything else is new."""
    lab = ["fresh"] * len(cur)
    ops = difflib.SequenceMatcher(None, prev, cur, autojunk=False).get_opcodes()
    for n, (tag, i1, _i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            first = n == 0 and i1 == 0 and j1 == 0
            for j in range(j1, j2):
                lab[j] = "prefix" if first else "moved"
    return lab


def fit_rho(points: list[tuple[float, float]]) -> tuple[float, bool]:
    """Tokens per character from (chars of the messages, P) of a session's lead calls: P = a + rho * chars."""
    if len(points) >= 6:
        x = np.array([p[0] for p in points], float)
        y = np.array([p[1] for p in points], float)
        if x.max() - x.min() >= 2000:
            (a, rho), *_ = np.linalg.lstsq(np.stack([np.ones_like(x), x], 1), y, rcond=None)
            if RHO_RANGE[0] <= rho <= RHO_RANGE[1]:
                return float(rho), True
    return RHO_FALLBACK, False


def tool_uses(content) -> list[list]:
    """[name, command prefix or None] per tool_use block of a reply."""
    out = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            cmd = inp.get("command")
            out.append([b.get("name"), cmd[:CMD_CHARS] if isinstance(cmd, str) else None])
    return out


def assign_chains(calls: list[dict]) -> list[str]:
    """Chain id per call (calls in time order): lead; os<k> per one_shot subagent instance (consecutive one_shot
    calls that extend the same first message); cs<k> per compaction summary; the purpose itself for side calls."""
    out, n_os, n_cs, last_os = [], 0, 0, None
    for c in calls:
        p = c.get("purpose") or "?"
        if p == "one_shot" or c.get("agent_kind") == "one_shot":
            msgs = (c.get("request") or {}).get("messages") or []
            first = json.dumps(msgs[0], sort_keys=True) if msgs else ""
            if last_os is None or last_os[0] != first or len(msgs) < last_os[1]:
                n_os += 1
            last_os = (first, len(msgs))
            out.append(f"os{n_os}")
            continue
        if p == "lead":
            last_os = None
            out.append("lead")
        elif p == "compaction_summary":
            n_cs += 1
            out.append(f"cs{n_cs}")
        else:
            out.append(p)
    return out


def session_records(src: str, model: str, bench: str, session: str, calls: list[dict],
                    events: list[dict] = ()) -> list[dict]:
    """Generic per-call records of one session. `calls` are harness_eval-style dicts (purpose, agent_kind, call, t
    in s, ms, usage, request {system, messages, tools}, response = content blocks, stop_reason); `events` are s15's
    context edits ({kind, t}), each charged to the next lead call."""
    calls = sorted((c for c in calls if c.get("t") is not None), key=lambda c: (float(c["t"]), c.get("call") or 0))
    chains = assign_chains(calls)
    served = [isinstance(c.get("usage"), dict) and isinstance(c.get("request"), dict) for c in calls]
    pts = []
    for c, ok in zip(calls, served):
        if ok and c.get("purpose") == "lead":
            pts.append((sum(n for _k, n, m in units(c["request"]) if m), usage_tokens(c["usage"])[0]))
    rho, fitted = fit_rho(pts)
    ev = sorted((float(e["t"]), e.get("kind") or "?") for e in events if e.get("t") is not None)
    lead_t = [float(c["t"]) for c, ok in zip(calls, served) if ok and c.get("purpose") == "lead"]
    kinds_at = defaultdict(set)                       # lead call start -> edit kinds logged since the previous one
    for te, kind in ev:
        nxt = next((t for t in lead_t if t >= te), None)
        if nxt is not None:
            kinds_at[nxt].add(kind)
    prev_of: dict[str, dict] = {}
    out = []
    for i, (c, chain, ok) in enumerate(zip(calls, chains, served)):
        purpose = c.get("purpose") or "?"
        eff, urg = hc.call_type(purpose)
        rec = {"src": src, "model": model, "bench": bench, "session": session, "idx": i, "call": c.get("call"),
               "purpose": purpose, "eff": eff, "urg": urg, "chain": chain, "t": round(float(c["t"]), 3),
               "ms": float(c.get("ms") or 0.0), "served": bool(ok), "rho": round(rho, 4), "rho_fit": fitted}
        if not ok:
            rec.update(P=0, cached=0, U=0, uin=0, ucr=0, out=0, Pprev=None, new=0, moved=0, edit=0, edited=False,
                       kinds=[], stop=c.get("stop_reason"), tool_uses=[])
            out.append(rec)
            continue
        P, cached, U, uin, ucr, n_out = usage_tokens(c["usage"])
        cur = units(c["request"])
        prev = prev_of.get(chain)
        if prev is None:
            new, moved, edited = float(U), 0.0, False
        else:
            pu = units(prev["request"]) + [reply_unit(prev.get("response"))]
            lab = diff_labels([k for k, _n, _m in pu], [k for k, _n, _m in cur])
            new = sum(rho * n + (TAU if m else 0.0) for (_k, n, m), lb in zip(cur, lab) if lb == "fresh")
            moved = sum(rho * n + (TAU if m else 0.0) for (_k, n, m), lb in zip(cur, lab) if lb == "moved")
            edited = any(lb == "moved" for lb in lab)
        edit = max(0.0, U - new) if prev is not None else 0.0
        rec.update(P=P, cached=cached, U=U, uin=uin, ucr=ucr, out=n_out,
                   Pprev=usage_tokens(prev["usage"])[0] if prev is not None else None,
                   new=round(new, 1), moved=round(moved, 1),
                   edit=round(edit, 1), edited=edited,
                   kinds=sorted(kinds_at.get(float(c["t"]), ())) if purpose == "lead" else [],
                   stop=c.get("stop_reason"), tool_uses=tool_uses(c.get("response")))
        prev_of[chain] = c
        out.append(rec)
    return out


# -- sources --------------------------------------------------------------------------------------------------

def he_session(arg: tuple[str, str, str]) -> list[dict]:
    model, bench, trial = arg
    t = Path(trial)
    return session_records("he", model, bench, f"{model}/{bench}/{t.parent.name}/{t.name}", list(hc.he_calls(t)),
                           list(hc.he_events(t)))


def t16_calls(run_dir: Path) -> list[dict]:
    """A topic 16 run's request log as harness_eval-style call dicts (t relative to the first send)."""
    path = run_dir / f"{run_dir.name}.requests.jsonl.xz"
    if not path.exists():
        path = run_dir / f"{run_dir.name}.requests.jsonl"
    if not path.exists():
        return []
    rows = [r for r in hc.read_jsonl(path) if r.get("t_send") is not None]
    if not rows:
        return []
    t0 = min(float(r["t_send"]) for r in rows)
    out = []
    for r in rows:
        resp = r.get("response") or {}
        out.append({"purpose": r.get("purpose"), "agent_kind": r.get("agent_kind"), "call": r.get("call_index"),
                    "t": float(r["t_send"]) - t0,
                    "ms": float(r.get("duration_ms") or 1e3 * (float(r.get("t_recv") or r["t_send"]) - float(r["t_send"]))),
                    "usage": resp.get("usage") if resp.get("usage") else None,
                    "request": {"system": r.get("system"), "messages": r.get("messages"), "tools": r.get("tools")},
                    "response": resp.get("content"), "stop_reason": resp.get("stop_reason")})
    return out


def t16_session(arg: tuple[str, str]) -> list[dict]:
    live, run = arg
    d = hc.T16 / "live" / live / "runs" / run
    parts = run.split("-")
    regime = parts[1] if len(parts) > 2 else "R?"
    return session_records(f"t16-{live}", "qwen3-32b", f"{regime}-{parts[-1]}", f"{live}/{run}", t16_calls(d))


def t16_server(live: str) -> dict:
    out = {}
    for p in sorted((hc.T16 / "live" / live).glob("server_calls*.jsonl")):
        for r in hc.read_jsonl(p):
            if r.get("ev") == "call":
                out[(r["spec"].rsplit("~a", 1)[0], int(r["call"]))] = r
    return out


def t16_replay_truth() -> dict:
    """(live, run, call) -> the E-A replay's alignment of a recorded prefix call against its previous call + reply
    (exact prefix, reused tokens, fresh tokens; arm-independent)."""
    out = {}
    for path in sorted((hc.T16 / "live").glob("*/replay*/replay_calls.jsonl")):
        live = path.parent.parent.name
        for r in hc.read_jsonl(path):
            st = next(iter(r["arms"].values()))["stats"]
            out[(live, r["run"], int(r["call"]))] = {"P": r["P"], **st}
    return out


# -- pricing and aggregation ----------------------------------------------------------------------------------

class N1Pricer:
    """N = 1 prices with topic 16's H100 models: rec -> (prefill ms, of which edit ms, decode ms). A pricer for
    N > 1 (topic 11's step attribution) only has to offer the same call and `slope`."""

    def __init__(self, models: dict, tp: list[dict]):
        self.models, self.tp, self.b = models, tp, models["compute"]["beta"]

    def slope(self, P: float) -> float:
        """Marginal prefill ms per computed token at prompt length P (compute family)."""
        return self.b[1] + self.b[2] * P / 1e3

    def __call__(self, rec: dict) -> tuple[float, float, float]:
        P, U, out = rec["P"], rec["U"], rec["out"]
        pre = hc.ttft_ms_16(self.models, "compute", U, 0, P)
        edit_ms = min(max(0.0, pre - self.b[0]), self.slope(P) * rec["edit"])
        return pre, edit_ms, out * hc.tpot_ms_16(self.tp, P + out / 2)


def granularity(records: list[dict]) -> dict:
    """(src, model) -> the prefix cache's granularity: gcd of every cache read (16; 256 DeepSeek-V4; 784 Qwen3.8)."""
    g = defaultdict(int)
    for r in records:
        if r["served"] and r["cached"]:
            g[(r["src"], r["model"])] = int(np.gcd(g[(r["src"], r["model"])], r["cached"]))
    return dict(g)


def n1_cache(records: list[dict]) -> list[dict]:
    """The records as an unshared server would see them: a chain call without a structural edit extends the previous
    prompt, so at N = 1 its cache hit is at least the previous prompt rounded down to the cache granularity (the
    previous reply may still be re-prefilled: template re-render, decode state not cached). Computed tokens above
    that are evictions by the other lanes of the shared servers; they are removed (and so is their edit share)."""
    gran = granularity(records)
    out = []
    for r in records:
        g = gran.get((r["src"], r["model"]), 0)
        if r["served"] and r["purpose"] in CHAIN and not r["edited"] and r.get("Pprev") is not None and g:
            cap = r["P"] - (r["Pprev"] // g) * g
            if 0 < cap < r["U"]:
                u = cap
                r = dict(r, U=u, edit=round(max(0.0, u - r["new"]), 1), evicted=r["U"] - u)
        out.append(r)
    return out


CATS = ("decode", "prefill_new", "prefill_edit_struct", "prefill_edit_other", "compaction")


def split(rec: dict, price) -> dict:
    """ms per category of one served record; side calls go to 'side' (GPU only)."""
    pre, edit_ms, dec = price(rec)
    if rec["purpose"] in MODIFY:
        return {"compaction": pre + dec}
    if rec["purpose"] not in CHAIN:
        return {"side": pre + dec}
    key = "prefill_edit_struct" if rec["edited"] else "prefill_edit_other"
    return {"decode": dec, "prefill_new": pre - edit_ms, key: edit_ms}


def census(records: list[dict], price, key=lambda r: (r["src"], r["model"], r["bench"])) -> dict:
    """Sums per group: calls by purpose and by effect x urgency, computed tokens, GPU ms and critical-path ms by
    category and by effect x urgency, plus each session's edit-work share (for its median)."""
    agg = defaultdict(lambda: {"calls": Counter(), "types": Counter(), "tok": Counter(), "gpu": Counter(),
                               "cp": Counter(), "cat": Counter(), "unserved": 0, "sessions": defaultdict(Counter)})
    for r in records:
        a = agg[key(r)]
        if not r["served"]:
            a["unserved"] += 1
            continue
        parts = split(r, price)
        ms = sum(parts.values())
        ty = f"{r['eff']}/{r['urg']}"
        a["calls"][r["purpose"]] += 1
        a["types"][ty] += 1
        a["tok"][ty] += r["U"]
        a["gpu"][ty] += ms
        if r["purpose"] in CHAIN:
            a["cp"][ty] += ms
        a["cat"].update(parts)
        a["sessions"][r["session"]].update(parts)
    return agg


def shares(cat: Counter) -> dict:
    cp = sum(cat[c] for c in CATS)
    gpu = cp + cat["side"]
    edit_all = cat["prefill_edit_struct"] + cat["prefill_edit_other"] + cat["compaction"]
    edit_struct = cat["prefill_edit_struct"] + cat["compaction"]
    return {"cp_s": cp / 1e3, "gpu_s": gpu / 1e3, **{c: cat[c] / cp if cp else 0.0 for c in CATS},
            "edit_cp": edit_all / cp if cp else 0.0, "edit_gpu": edit_all / gpu if gpu else 0.0,
            "edit_struct_cp": edit_struct / cp if cp else 0.0}


# -- validation against topic 16 ------------------------------------------------------------------------------

def validation_rows(records: list[dict], server: dict, replay: dict, price) -> list[dict]:
    """Per topic 16 lead call with a measured alignment: the estimate next to the truth.
    EPIC arms (live plans): truth = reuse_avail - exact, U = P - E (what vanilla prefix caching computes).
    prefix arms (E-A replays): truth from the replay's alignment, U = the server's measured computed tokens."""
    rows, prev = [], {}
    for r in records:
        if not r["served"] or r["purpose"] != "lead":
            continue
        live, run = r["session"].split("/", 1)
        arm = run.rsplit("-", 1)[1]
        last = prev.get(r["session"])
        prev[r["session"]] = r
        if last is None or last["call"] != r["call"] - 1:
            continue
        num_new = max(0, r["P"] - last["P"] - last["out"])
        row = {"live": live, "run": run, "arm": arm, "call": r["call"], "P": r["P"], "out": r["out"],
               "new": r["new"], "moved": r["moved"], "edited_struct": r["edited"]}
        if arm in ("prefix", "prefix_aa"):
            st = replay.get((live, run, r["call"]))
            if not st or st["P"] != r["P"]:
                continue
            row.update(set="prefix (E-A replay)", U=r["U"], U_exact=r["P"] - st["E"])
        else:
            st = server.get((run, r["call"]))
            if not st or "E" not in st or st.get("fallback") or st["P"] != r["P"]:
                continue
            row.update(set="EPIC arms (live plan)", U=st["P"] - st["E"], U_exact=st["P"] - st["E"])
        row.update(truth=st["reuse_avail"] - st["exact"], fresh=st["F"], edited=bool(st["edited"]),
                   est=max(0.0, row["U"] - r["new"]), est_exact=max(0.0, row["U_exact"] - r["new"]),
                   est_num=max(0.0, row["U"] - num_new))
        pre, _e, dec = price({"P": r["P"], "U": row["U"], "out": r["out"], "edit": 0.0})
        row.update(call_ms=pre + dec, slope=price.slope(r["P"]))
        rows.append(row)
    return rows


def validation_lines(rows: list[dict]) -> list[str]:
    lines = ["| set | calls | edited (truth) | struct. edit flag: recall / precision | edit tokens, truth | "
             "estimate / truth | per-call error, median [p10, p90] | MdAE (tokens) | corr. | with P-(Pprev+out) "
             "as 'new' | new / fresh (truth) | edit share of chain time: truth / estimate |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in ("EPIC arms (live plan)", "prefix (E-A replay)"):
        rs = [r for r in rows if r["set"] == name]
        if not rs:
            continue
        tr = np.array([r["truth"] for r in rs], float)
        es = np.array([r["est"] for r in rs], float)
        en = np.array([r["est_num"] for r in rs], float)
        ed = np.array([r["edited"] for r in rs])
        fl = np.array([r["edited_struct"] for r in rs])
        err = es - tr
        rec_ = (ed & fl).sum() / max(1, ed.sum())
        prec = (ed & fl).sum() / max(1, fl.sum())
        sl = np.array([r["slope"] for r in rs])
        ms = np.array([r["call_ms"] for r in rs])
        lines.append(
            f"| {name} | {len(rs):,} | {ed.mean():.1%} | {rec_:.1%} / {prec:.1%} | {tr.sum() / 1e6:.2f} M | "
            f"{es.sum() / tr.sum():.3f} | {np.median(err):+.0f} [{np.percentile(err, 10):+.0f}, "
            f"{np.percentile(err, 90):+.0f}] | {np.median(np.abs(err)):.0f} | {np.corrcoef(es, tr)[0, 1]:.3f} | "
            f"{en.sum() / tr.sum():.3f} | {sum(r['new'] for r in rs) / max(1, sum(r['fresh'] for r in rs)):.3f} | "
            f"{(sl * tr).sum() / ms.sum():.1%} / {(sl * es).sum() / ms.sum():.1%} |")
    pr = [r for r in rows if r["set"] == "prefix (E-A replay)"]
    if pr:
        tr = sum(r["truth"] for r in pr)
        lines += ["", f"Prefix arms with U = P - E instead of the server's computed tokens (the exact prefix rounded "
                  f"to the block, i.e. without the cache's hits beyond it and its evictions): estimate / truth = "
                  f"{sum(r['est_exact'] for r in pr) / tr:.3f}; the server computed {sum(r['U'] for r in pr) / max(1, sum(r['U_exact'] for r in pr)):.3f}x "
                  f"the tokens P - E."]
    return lines


# -- tables ---------------------------------------------------------------------------------------------------

def fmt_pct(x: float) -> str:
    return f"{x:.1%}"


def usage_lines(records: list[dict]) -> list[str]:
    """The usage-semantics check: block granularity of cache reads, input_tokens below one block (it is the computed
    tail that fills no block), computed = input + creation."""
    lines = ["| source | model | served calls | cache_read granularity (gcd) | calls with input_tokens < gcd | "
             "computed U = input + creation: share in creation | example (in, read, creation, out -> P) |",
             "|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in records:
        if r["served"]:
            groups[(r["src"], r["model"])].append(r)
    for (src, model), rs in sorted(groups.items()):
        g = 0
        for r in rs:
            g = int(np.gcd(g, r["cached"]))
        below = sum(1 for r in rs if g and r["uin"] < g) / len(rs)
        ucr = sum(r["ucr"] for r in rs) / max(1, sum(r["U"] for r in rs))
        ex = next((r for r in rs if r["cached"] and r["ucr"] and r["purpose"] == "lead"), rs[0])
        lines.append(f"| {src} | {model} | {len(rs):,} | {g} | {below:.1%} | {ucr:.1%} | "
                     f"({ex['uin']}, {ex['cached']}, {ex['ucr']}, {ex['out']}) -> {ex['P']} |")
    return lines


def type_lines(agg: dict) -> list[str]:
    types = sorted({t for a in agg.values() for t in a["types"]})
    purposes = sorted({p for a in agg.values() for p in a["calls"]}, key=lambda p: (p != "lead", p))
    head = ("| source | model | served calls | " + " | ".join(f"{p}" for p in purposes) + " | "
            + " | ".join(f"{t}: calls / computed tok / GPU-s / crit.-path s" for t in types) + " |")
    lines = [head, "|" + "---|" * (3 + len(purposes) + len(types))]
    for (src, model, _b), a in sorted(agg.items()):
        n = sum(a["calls"].values())
        tok, gpu, cp = sum(a["tok"].values()), sum(a["gpu"].values()), sum(a["cp"].values())
        lines.append(f"| {src} | {model} | {n:,} | " + " | ".join(fmt_pct(a["calls"][p] / n) for p in purposes)
                     + " | " + " | ".join(f"{a['types'][t] / n:.1%} / {a['tok'][t] / tok:.1%} / "
                                          f"{a['gpu'][t] / gpu:.1%} / {a['cp'][t] / cp if cp else 0:.1%}"
                                          for t in types) + " |")
    return lines


def time_lines(agg: dict, agg_n1: dict) -> list[str]:
    lines = ["| source | model | bench | sessions | crit.-path h | decode | prefill new | prefill edit: structural | "
             "prefill edit: re-render / other | compaction | **edit work / crit. path** (= / GPU) | "
             "N = 1 cache (evictions removed) | structural edits + compaction only | "
             "median session edit share [IQR] |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    order = lambda kv: (kv[0][0], hc.HE_MODELS.index(kv[0][1]) if kv[0][1] in hc.HE_MODELS else 9,  # noqa: E731
                        kv[0][2] == "all", kv[0][2])
    for k, a in sorted(agg.items(), key=order):
        s = shares(a["cat"])
        s1 = shares(agg_n1[k]["cat"])
        per = [shares(c)["edit_cp"] for c in a["sessions"].values() if sum(c[x] for x in CATS) > 0]
        q = np.percentile(per, [25, 50, 75]) if per else [0, 0, 0]
        gpu = "" if abs(s["edit_gpu"] - s["edit_cp"]) < 5e-4 else f" ({s['edit_gpu']:.1%} of GPU)"
        lines.append(f"| {k[0]} | {k[1]} | {k[2]} | {len(a['sessions'])} | {s['cp_s'] / 3600:.1f} | "
                     + " | ".join(fmt_pct(s[c]) for c in CATS)
                     + f" | **{s['edit_cp']:.1%}**{gpu} | {s1['edit_cp']:.1%} | {s['edit_struct_cp']:.1%} | "
                       f"{q[1]:.1%} [{q[0]:.1%}, {q[2]:.1%}] |")
    return lines


def kind_lines(records: list[dict]) -> list[str]:
    """Edit tokens of lead calls by the s15 edit logged before the call (harness_eval)."""
    order = ("reactive_compact", "compact_history", "snip_compact", "micro_compact", "fit_tool_results",
             "tool_result_budget")
    agg = defaultdict(Counter)
    for r in records:
        if not r["served"] or r["purpose"] != "lead":
            continue
        kind = next((k for k in order if k in r["kinds"]), None)
        label = kind or ("no logged edit, structural diff" if r["edited"] else "no edit (re-render / other)")
        agg[r["model"]][label] += r["edit"]
    labels = list(order) + ["no logged edit, structural diff", "no edit (re-render / other)"]
    labels = [lb for lb in labels if any(agg[m][lb] for m in agg)]
    lines = ["| model | edit tokens (M) | " + " | ".join(labels) + " |", "|" + "---|" * (2 + len(labels))]
    for m in sorted(agg, key=lambda m: hc.HE_MODELS.index(m) if m in hc.HE_MODELS else 9):
        tot = sum(agg[m].values())
        lines.append(f"| {m} | {tot / 1e6:.1f} | " + " | ".join(fmt_pct(agg[m][lb] / tot) for lb in labels) + " |")
    return lines


def context_lines(records: list[dict]) -> list[str]:
    lines = ["| source | model | lead calls | P median | P p90 | P max | output median | output p90 | "
             "share of lead calls with a structural edit | rho median (tok/char) | sessions with a fitted rho |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in records:
        if r["served"] and r["purpose"] == "lead":
            groups[(r["src"], r["model"])].append(r)
    for (src, model), rs in sorted(groups.items()):
        P = np.array([r["P"] for r in rs])
        o = np.array([r["out"] for r in rs])
        lines.append(f"| {src} | {model} | {len(rs):,} | {np.median(P):,.0f} | {np.percentile(P, 90):,.0f} | "
                     f"{P.max():,} | {np.median(o):,.0f} | {np.percentile(o, 90):,.0f} | "
                     f"{np.mean([r['edited'] for r in rs]):.1%} | {statistics.median(r['rho'] for r in rs):.3f} | "
                     f"{np.mean(list({r['session']: r['rho_fit'] for r in rs}.values())):.1%} |")
    return lines


def d1_lines(agg_model: dict, agg_n1: dict) -> list[str]:
    def verdict(x: dict) -> str:
        return "passes" if x["edit_cp"] >= 0.05 or x["edit_gpu"] >= 0.20 else "fails"
    lines = []
    order = lambda kv: (kv[0][0] != "he", kv[0][1] != "all models",  # noqa: E731
                        hc.HE_MODELS.index(kv[0][1]) if kv[0][1] in hc.HE_MODELS else 9)
    for k, a in sorted(agg_model.items(), key=order):
        s, s1 = shares(a["cat"]), shares(agg_n1[k]["cat"])
        st = "passes" if s["edit_struct_cp"] >= 0.05 else "fails"
        lines.append(f"- **{k[0]} / {k[1]}**: edit work {s['edit_cp']:.1%} of critical-path time and {s['edit_gpu']:.1%} "
                     f"of GPU time as measured ({verdict(s)}); {s1['edit_cp']:.1%} with the N = 1 cache "
                     f"({verdict(s1)}); structural edits and compaction alone {s['edit_struct_cp']:.1%} ({st}).")
    return lines


# -- main -----------------------------------------------------------------------------------------------------

def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def build(sources: tuple[str, ...], workers: int, limit: int = 0) -> dict[str, list[dict]]:
    out = {}
    with Pool(workers) as pool:
        if "he" in sources:
            trials = [(m, b, str(t)) for m, b, t in hc.he_trials()]
            if limit:
                trials = [t for i, t in enumerate(trials) if i % max(1, len(trials) // limit) == 0][:limit]
            out["he"] = [r for rs in pool.imap(he_session, trials, chunksize=2) for r in rs]
        if "t16" in sources:
            runs = []
            for live in ("main", "screen", "pilot"):
                base = hc.T16 / "live" / live / "runs"
                for d in sorted(base.iterdir()) if base.is_dir() else []:
                    if d.is_dir() and (live == "main" or d.name.endswith(("prefix", "prefix_aa"))):
                        runs.append((live, d.name))
            out["t16"] = [r for rs in pool.imap(t16_session, runs, chunksize=2) for r in rs]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=24, help="worker processes (<= 32 on this shared machine)")
    ap.add_argument("--sources", default="he,t16")
    ap.add_argument("--limit", type=int, default=0, help="development: an evenly spaced subset of N trials")
    a = ap.parse_args()
    sources = tuple(s for s in a.sources.split(",") if s)
    built = build(sources, min(32, max(1, a.workers)), a.limit)
    price = N1Pricer(*hc.latency_models_16())
    he = built.get("he", [])
    t16 = built.get("t16", [])
    t16_census = [r for r in t16 if r["src"] == "t16-main" and r["bench"] in ("R1-prefix", "R1-prefix_aa")]
    if he:
        write_jsonl(E0A / "calls_he.jsonl", he)
    if t16:
        write_jsonl(E0A / "calls_t16.jsonl", t16_census)
    val = []
    if t16:
        server = {**t16_server("pilot"), **t16_server("screen"), **t16_server("main")}
        val = validation_rows(t16, server, t16_replay_truth(), price)
        write_jsonl(E0A / "validation_t16.jsonl", val)
    recs = he + [dict(r, src="t16", bench="R1") for r in t16_census]
    recs1 = n1_cache(recs)
    keys = (lambda r: (r["src"], r["model"], r["bench"]), lambda r: (r["src"], r["model"], "all"))
    agg, agg1 = {}, {}
    for key in keys:
        agg.update(census(recs, price, key))
        agg1.update(census(recs1, price, key))
    pool_key = lambda r: ("he", "all models", "all")  # noqa: E731
    agg.update(census([r for r in recs if r["src"] == "he"], price, pool_key))
    agg1.update(census([r for r in recs1 if r["src"] == "he"], price, pool_key))
    benches = defaultdict(set)
    for r in recs:
        benches[(r["src"], r["model"])].add(r["bench"])
    for k in [k for k in agg if k[2] != "all" and len(benches[(k[0], k[1])]) == 1]:
        del agg[k], agg1[k]                           # a single bench: its row is the model's row
    evicted = defaultdict(float)
    for r in recs1:
        evicted[(r["src"], r["model"])] += r.get("evicted", 0)
    tot_u = defaultdict(float)
    for r in recs:
        if r["served"]:
            tot_u[(r["src"], r["model"])] += r["U"]
    unserved = Counter()
    for r in recs:
        if not r["served"]:
            unserved[(r["src"], r["model"])] += 1
    lines = ["## 1. Usage semantics (checked)", "",
             "vLLM 0.31's Anthropic endpoint reports `input_tokens = prompt - cache_read - cache_creation`, where "
             "`cache_creation` counts the tokens computed and written to the prefix cache (full blocks). Every "
             "`cache_read` below is a multiple of the block (16 tokens; 256 for DeepSeek-V4's `--block-size 256`; "
             "784 for Qwen3.8's hybrid state checkpoints), and `input_tokens` stays below one block in (almost) every "
             "call: it is the computed tail that fills no block. So P = input + cache_read + creation (as "
             "`cache_snip.py`) and computed U = input + creation.", ""]
    lines += usage_lines(recs)
    lines += ["", f"Calls without usage (refused at the budget, or overflow errors) are left out: "
              + ", ".join(f"{s}/{m} {n}" for (s, m), n in sorted(unserved.items())) + ".", "",
              "## 2. Calls by purpose and by effect x urgency", "",
              "Effect x urgency from `hc.call_type` (README section 1): A/C = append, critical (lead, one_shot); "
              "M/C = modify, critical (compaction_summary). Neither source has a side call (no memory_* or teammate "
              "purpose; memory is off in the harness_eval profile), so GPU time equals critical-path time at N = 1. "
              "Shares of served calls; then per type: calls / computed tokens / modeled GPU-s / critical-path s.", ""]
    lines += type_lines({k: v for k, v in agg.items() if k[2] == "all" and k[1] != "all models"})
    lines += ["", "## 3. Where the lead chain's time goes at N = 1 (topic 16's H100 models)", "",
              "Shares of modeled critical-path time. Edit work = prefill edit (structural + re-render / other) + "
              "compaction; 'structural' = calls whose prompt diverged at a message the previous call had (snip, "
              "placeholder, summary, fit); 're-render / other' = the estimator's remainder on calls without one: "
              "the previous reply re-prefilled (template re-render or decode state not cached; README section 1 "
              "counts template re-render as an edit), block granularity, eviction on the shared servers. The last "
              "columns are the same share computed per session (median, IQR).", ""]
    lines += time_lines(agg, agg1)
    lines += ["", "N = 1 cache: computed tokens removed as evictions (share of all computed tokens): "
              + ", ".join(f"{s_}/{m} {evicted[(s_, m)] / tot_u[(s_, m)]:.1%}" for (s_, m) in sorted(tot_u)
                          if tot_u[(s_, m)]) + "."]
    lines += ["", "Context and the token estimate (lead calls):", ""]
    lines += context_lines(recs)
    if he:
        lines += ["", "Edit tokens by the s15 edit logged before the call (harness_eval; a call with several logged "
                  "edits is charged to the first in this order):", ""]
        lines += kind_lines(he)
    if val:
        lines += ["", "## 4. Validation of the edit estimate against topic 16's measured alignments", "",
                  "Truth per call: `reuse_avail - exact` from the plan (tokens of the previous prompt + reply that "
                  "the call reuses after the first divergence; what a prefix cache recomputes because of the edit) "
                  f"and `edited`. EPIC arms: every lead call of the {len({r['run'] for r in val if r['arm'] not in ('prefix', 'prefix_aa')})} "
                  "main EPIC-arm runs, priced as vanilla prefix caching (U = P - E). Prefix arms: the "
                  f"{len({(r['live'], r['run']) for r in val if r['arm'] in ('prefix', 'prefix_aa')})} recorded prefix "
                  "runs replayed by E-A (main, screen, pilot; their first 80 calls), "
                  "with U = the server's own computed tokens (its cache can serve more than the exact prefix from "
                  "older sequences, or less after an eviction). 'new / fresh' compares the character-based estimate "
                  "of the appended tokens with the plan's fresh tokens F. The last column prices both with the H100 "
                  "model, as section 3 does.", ""]
        lines += validation_lines(val)
    lines += ["", "## 5. D1 reading at N = 1", "",
              "D1 passes in a (config, N) cell if edit work is >= 5 % of critical-path time or >= 20 % of GPU time "
              "(README section 2). Here every row is config R1 at N = 1:", ""]
    lines += d1_lines({k: v for k, v in agg.items() if k[2] == "all"}, agg1)
    note = ("E0a at N = 1 (D1 input). Sources: harness_eval profile trials (`hc.he_trials()`), topic 16's main "
            "sweep prefix arms. Records: `data/e0a/calls_he.jsonl`, `data/e0a/calls_t16.jsonl`, "
            "`data/e0a/validation_t16.jsonl`. Time is modeled with topic 16's H100 Qwen3-32B TTFT/TPOT models for "
            "every model (TPOT is flat beyond 30K context), not measured.")
    out = hc.write_table("E0a_census_n1", "E0a census at N = 1: edit work on the critical path", lines, note)
    print(out)


if __name__ == "__main__":
    main()
