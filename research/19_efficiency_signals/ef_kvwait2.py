#!/usr/bin/env python3
"""Topic 19 follow-up, revised screen (2026-10-31): does MiniMax-M2.7 act on the KV rule once the replayed history no
longer shows it ignored, or once the wait text carries a worked example?

ef_kvwait.py's screen (2026-10-30; followups/kvwait_prereg.md) found no wording that reaches its 30 % bar. With the
newest [server] line at 93 %, the rule plus cue (RC) put `wait` in 2 % of work replies (12 of 240 samples). The rate
fell to 0 % when the context held more than 20 earlier lines at >= 80 % that the recorded replies had ignored. That
pre-registration hands any revised wording back to the user, who chose this one revised screen. It crosses two
factors on 60 fresh items, drawn from ef_kvwait's 150 unused confirm items:
  - history: as recorded, or capped (every earlier [server] line shows min(kv, 0.75), so no earlier reply ignored
    the rule);
  - wording: RC (wait text v5, paragraph wait_kv, the kv_cue line) or RX (the same with wait text v6: v5 plus a
    worked example on each side of the threshold, at 90 % and 55 %).
Conditions, S = 4 samples each, with the newest line at 0.62 and at 0.93:
  B0     the request as recorded
  RC     v5, history as recorded          RCcap  v5, earlier lines capped at 75 %
  RX     v6, history as recorded          RXcap  v6, earlier lines capped at 75 %
The rewrite, the joined lines, the sampler, the per-item metrics and the bootstrap are ef_kvwait's. The readings are
in followups/kvwait2_prereg.md; `run` checks its sha256 list first. The GPU part runs on a stage of its own
(ef_kvserve.py run --probe kvwait2).

    python research/19_efficiency_signals/ef_kvwait2.py items     # data/kvwait2/items.json
    python research/19_efficiency_signals/ef_kvwait2.py check     # CPU: rewrites, caps, renders
    python research/19_efficiency_signals/ef_kvwait2.py freeze    # followups/kvwait2_prereg.sha256
    python research/19_efficiency_signals/ef_kvwait2.py run --url URL        # the screen (resumable)
    python research/19_efficiency_signals/ef_kvwait2.py analyze   # data/tables/kvwait2.{md,json}
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_kvwait as K  # noqa: E402
import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

OUT = DIR / "data" / "kvwait2"
ITEMS = OUT / "items.json"
PREREG = DIR / "followups" / "kvwait2_prereg.md"
PREREG_SHA = DIR / "followups" / "kvwait2_prereg.sha256"
FROZEN = [PREREG, Path(__file__).resolve(), ITEMS, Path(K.__file__).resolve(), DIR / "ef_kvserve.py", K.HOLD_PATH,
          Path(cf.__file__).resolve(), Path(rr.__file__).resolve(), DIR / "ef_render.py", rr.T19_TEMPLATE]

SEED = 1031
N = {"work": 42, "start": 9, "final": 9}
LEVELS = (0.62, 0.93)
CAP = 0.75
INFLIGHT = 8
K.CONDS["RX"] = ("v6", "wait_kv", "kv_cue")        # ef_kvwait.configure reads its CONDS; v6 is in hold_runtime
CONDS = {"RC": ("RC", False), "RCcap": ("RC", True), "RX": ("RX", False), "RXcap": ("RX", True)}  # source, capped
RULE = {"low_max": 0.10, "wait_only_max": 0.05, "kept_drop_max": 0.10, "min_high": 0.30}   # ef_kvwait's screen rule
EFFECT = {"min": 0.15, "lo_min": 0.05}            # a factor matters: mean paired difference >= 15 points, low > 5
CONTRASTS = {"history": (("RCcap", "RC"), ("RXcap", "RX")), "example": (("RX", "RC"), ("RXcap", "RCcap"))}
MENTION = re.compile(r"KV cache|\b\d\d ?% full|\[server\]|server rule|shared server|cache (is )?(at|full)|"
                     r"server load|wait (call|tool)|\bpause\b", re.I)
CUE = "At 80% or more"


# -- items and requests ---------------------------------------------------------------------------------------------
def build_items() -> dict:
    pool = K.load_items()["confirm"]
    rng = random.Random(SEED)
    chosen = []
    for st, n in N.items():
        chosen += rng.sample(sorted((it for it in pool if it["stratum"] == st), key=lambda it: it["id"]), n)
    chosen.sort(key=lambda it: it["id"])
    return {"seed": SEED, "source": "data/kvwait/items.json, confirm pool (unused)", "items": chosen,
            "counts": dict(Counter(it["stratum"] for it in chosen)), "sessions": len({it["sid"] for it in chosen})}


def load_items() -> list[dict]:
    return json.loads(ITEMS.read_text())["items"]


def capped(lines: list) -> list:
    return [(None if kv is None else min(kv, CAP), waiting) for kv, waiting in lines]


def request(base: dict, cond: str, lines: list, level: float | None) -> dict:
    """The recorded request under a condition: ef_kvwait.rewrite, with the earlier lines capped for the *cap ones."""
    if cond == "B0":
        return base
    src, cap = CONDS[cond]
    return K.rewrite(base, src, capped(lines) if cap else lines, level)


def chains(items: list[dict]) -> list[tuple]:
    """As ef_kvwait.chains: per item and condition, its levels in one chain (they share the prefix)."""
    plan = [("B0", (None,))] + [(c, LEVELS) for c in CONDS]
    out = []
    for it in sorted(items, key=lambda it: hashlib.sha256(f"{SEED}|{it['id']}".encode()).hexdigest()):
        base = rr.request(it["ptr"])
        sess = {"sid": it["sid"], "src": "t19", "template": it["template"]}
        row = {"turn": it["turn"], "j": it["j"], "call": it["call"], "np": it["np"]}
        rk = {lv: cf.key_set(it["calls"], None, lv) for lv in ("target", "exact", "loose")}
        lines = [tuple(x) for x in it["lines"]]
        for cond, levels in plan:
            variants = []
            for level in levels:
                try:
                    req = request(base, cond, lines, level)
                except K.Unbuildable:
                    req = None
                variants.append((K.variant_name(cond, level), req))
            out.append((sess, row, rk, variants))
    return out


# -- the GPU run ----------------------------------------------------------------------------------------------------
def changed() -> list[str]:
    saved = K.PREREG_SHA
    try:
        K.PREREG_SHA = PREREG_SHA
        return K.changed()
    finally:
        K.PREREG_SHA = saved


def freeze() -> None:
    saved = (K.PREREG_SHA, K.FROZEN)
    try:
        K.PREREG_SHA, K.FROZEN = PREREG_SHA, FROZEN
        K.freeze()
    finally:
        K.PREREG_SHA, K.FROZEN = saved


def run(url: str) -> int:
    bad = changed()
    if bad:
        print(f"[kvwait2] refused: frozen files changed or missing: {bad}", flush=True)
        return 4
    items = load_items()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "runs.jsonl").open("a") as f:
        f.write(json.dumps({"t_start": time.time(), "url": url, "server": K.server_of(url), "S": K.S,
                            "inflight": INFLIGHT, "model": cf.MODEL, "sampling": cf.SAMPLING,
                            "max_tokens": cf.MAX_TOKENS}) + "\n")
    todo = chains(items)
    print(f"[kvwait2] screen: {sum(len(c[3]) for c in todo)} sampling requests in {len(todo)} chains, S={K.S}, "
          f"inflight {INFLIGHT}", flush=True)
    runner = K.KVRunner(url, OUT / "screen", K.S, cf.TAU_DEFAULT, INFLIGHT, K.RENDERERS)
    asyncio.run(runner.run_chains(todo))
    r = write_tables()["readings"]
    print(f"[kvwait2] usable: {r['usable'] or 'none'}; the replayed history {r['history']}; the worked example "
          f"{r['example']}", flush=True)
    return 0


# -- analysis -------------------------------------------------------------------------------------------------------
def excerpts(texts: dict, items: list[dict], n: int = 3) -> dict:
    """A few thinking excerpts per condition at 0.93: work samples that waited and that did not."""
    work = {(it["sid"], it["turn"], it["j"]) for it in items if it["stratum"] == "work"}
    out = {}
    for c in CONDS:
        v = K.variant_name(c, LEVELS[1])
        keys = sorted(k for k in texts if k[3] == v and k[:3] in work)
        random.Random(f"kvwait2|excerpts|{v}").shuffle(keys)
        out[v] = {"waited": [texts[k][1][:300] for k in keys if texts[k][0] is not None][:n],
                  "did_not": [texts[k][1][:300] for k in keys if texts[k][0] is None][:n]}
    return out


def analyze() -> dict:
    items = load_items()
    saved = K.KV
    try:
        K.KV = OUT                                     # ef_kvwait's analysis reads KV/<phase>/
        res = K.analyze_phase("screen", items)
        texts = K.read_texts("screen")
    finally:
        K.KV = saved
    path = OUT / "screen" / "records.jsonl"
    recs = {(r["sid"], r["turn"], r["j"], r["variant"]): r for r in (rr.read_jsonl(path) if path.exists() else [])}
    vs = res["variants"]
    for v, row in vs.items():
        th = [t for k, (_, t) in texts.items() if k[3] == v]
        row["mentions"] = sum(bool(MENTION.search(t)) for t in th) / len(th) if th else None

    def rate(it, v):
        m = K.item_metrics(recs.get((it["sid"], it["turn"], it["j"], v)))
        return None if m is None else m["wait"]
    hi, lo = (lambda c: K.variant_name(c, LEVELS[1])), (lambda c: K.variant_name(c, LEVELS[0]))
    work = [it for it in items if it["stratum"] == "work"]
    contrasts = {}
    for name, pairs in CONTRASTS.items():
        per_item = []
        for a, b in pairs:
            units = [(it["sid"], rate(it, hi(a)) - rate(it, hi(b))) for it in work
                     if rate(it, hi(a)) is not None and rate(it, hi(b)) is not None]
            contrasts[f"{a} - {b}"] = K.boot(units, random.Random(f"kvwait2|{a}|{b}"))
        for it in work:
            ds = [rate(it, hi(a)) - rate(it, hi(b)) for a, b in pairs
                  if rate(it, hi(a)) is not None and rate(it, hi(b)) is not None]
            if len(ds) == len(pairs):
                per_item.append((it["sid"], sum(ds) / len(ds)))
        contrasts[name] = K.boot(per_item, random.Random(f"kvwait2|{name}"))
    b0_kept = (vs.get("B0") or {}).get("work", {}).get("kept")
    rows = {}
    for c in CONDS:
        h, l = vs.get(hi(c)), vs.get(lo(c))
        if not h or not l or h["work"]["wait"] is None or l["work"]["wait"] is None:
            rows[c] = {"eligible": False, "why": "no data"}
            continue
        only = max(h["all"]["only"] or 0.0, l["all"]["only"] or 0.0)
        kept_ok = b0_kept is None or h["work"]["kept"] >= b0_kept - RULE["kept_drop_max"]
        rows[c] = {"eligible": l["work"]["wait"] <= RULE["low_max"] and only <= RULE["wait_only_max"] and kept_ok,
                   "high": h["work"]["wait"], "low": l["work"]["wait"], "wait_only": only,
                   "kept_high": h["work"]["kept"], "kept_b0": b0_kept}

    def shown(t):
        return t[0] is not None and t[0] >= EFFECT["min"] and t[1] is not None and t[1] > EFFECT["lo_min"]
    res["contrasts"], res["rows"] = contrasts, rows
    res["readings"] = {
        "usable": sorted((c for c, r in rows.items() if r["eligible"] and r["high"] >= RULE["min_high"]),
                         key=lambda c: -rows[c]["high"]),
        "history": "holds it back" if shown(contrasts["history"]) else "not shown to hold it back",
        "example": "helps" if shown(contrasts["example"]) else "not shown to help"}
    res["excerpts"] = excerpts(texts, items)
    return res


def write_tables() -> dict:
    res = analyze()
    K.TABLES.mkdir(parents=True, exist_ok=True)
    (K.TABLES / "kvwait2.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    (K.TABLES / "kvwait2.md").write_text(render_md(res))
    return res


def _pts(x) -> str:
    return "—" if x is None else f"{100 * x:+.1f}"


def render_md(res: dict) -> str:
    p = K._p
    out = ["# KV-threshold wait prompt, revised screen: the replayed history and a worked example (ef_kvwait2.py)",
           "", "Wait rate = share of samples whose reply calls `wait` (mean over items; 95 % session-cluster "
           "bootstrap). work = replies with tool calls after the turn's first call; kept = the recorded action still "
           "issued; cap = every earlier [server] line shows at most 75 %; mentions = samples whose thinking mentions "
           "the line, the rule or a pause.", "", f"## screen ({res['items']} items)", "",
           "| condition | work: wait [95 %] | start: wait | final: wait | wait-only | kept (work) | mentions | "
           "out tokens p50 | seconds p50 (n) |", "|---|---|---|---|---|---|---|---|---|"]
    for v, row in res["variants"].items():
        w = row["work"]
        ci = w.get("wait_ci") or [None, None]
        out.append(f"| `{v}` | {p(w['wait'])} [{p(ci[0])}, {p(ci[1])}] (n {w['n']}) | {p(row['start']['wait'])} | "
                   f"{p(row['final']['wait'])} | {p(row['all']['only'], 1)} | {p(w['kept'])} | "
                   f"{p(row.get('mentions'), 1)} | {K._n(row['all']['out_p50'])} | {K._n(row['seconds_p50'])} "
                   f"({row['seconds_n']}) |")
    out += ["", f"Paired differences in the work wait rate at {LEVELS[1]} (points; 95 % session-cluster bootstrap; "
                "history and example are the means of their two pairs):", ""]
    out += [f"- {k}: {_pts(m)} [{_pts(lo)}, {_pts(hi)}]" for k, (m, lo, hi) in res["contrasts"].items()]
    r = res["readings"]
    out += ["", f"**Readings** (followups/kvwait2_prereg.md): usable at {LEVELS[1]} (eligible and >= 30 %): "
                f"{', '.join(f'`{c}`' for c in r['usable']) or 'none'}; the replayed history: {r['history']}; the "
                f"worked example: {r['example']}.", ""]
    prior = {v: row.get("by_prior_high") for v, row in res["variants"].items() if row.get("by_prior_high")}
    if prior:
        out += ["Wait rate on work items by the number of earlier lines at or above the rule as recorded (items, "
                "rate); under cap those lines show at most 75 %:", ""]
        out += [f"- `{v}`: " + "; ".join(f"{k}: {n}, {p(x)}" for k, (n, x) in pr.items()) for v, pr in prior.items()]
        out.append("")
    return "\n".join(out) + "\n"


# -- CPU check ------------------------------------------------------------------------------------------------------
def check(workers: int) -> int:
    """Each condition differs from the recording only in ef_kvwait's three places; RX carries v6 and RC v5; the
    newest line shows the level, with the cue at 0.93 only; under cap no earlier line shows more than 75 % or a cue;
    B0 renders to the recorded prompt tokens and RXcap at 0.93 renders to a plausible length."""
    items = load_items()
    problems = Counter()
    pct = re.compile(r"KV cache (\d+)% full")
    renders = []
    for it in items:
        base = rr.request(it["ptr"])
        lines = [tuple(x) for x in it["lines"]]
        for cond, (src, cap) in CONDS.items():
            for level in LEVELS:
                req = request(base, cond, lines, level)
                if K.strip_variable(req) != K.strip_variable(base):
                    problems[f"{cond}: differs outside the three places"] += 1
                desc = next(t["description"] for t in req["tools"] if t.get("name") == K.HR.WAIT_TOOL_NAME)
                if ("Example:" in desc) != (src == "RX") or "{kv" in desc:
                    problems[f"{cond}: wait text"] += 1
                texts = [req["messages"][i]["content"][k]["text"] for i, k in K.server_blocks(req["messages"])]
                shown = [int(m.group(1)) if (m := pct.search(t)) else None for t in texts]
                if shown[-1] != round(100 * level) or (CUE in texts[-1]) != (level >= 0.8):
                    problems[f"{cond}@{level}: the newest line"] += 1
                if cap and any((s is not None and s > round(100 * CAP)) or CUE in t
                               for s, t in zip(shown[:-1], texts[:-1])):
                    problems[f"{cond}: an earlier line above the cap"] += 1
        renders += [(base, it["template"]), (request(base, "RXcap", lines, LEVELS[1]), it["template"])]
    print(f"[check] {len(items)} items x {len(CONDS)} conditions x {len(LEVELS)} levels rewritten; problems: "
          f"{dict(problems) or 'none'}", flush=True)
    with ProcessPoolExecutor(workers, initializer=cf._init_worker) as ex:
        lens = [len(ids) for ids in ex.map(cf.render_ids, renders, chunksize=2)]
    off = [it["id"] for n, it in enumerate(items) if lens[2 * n] != it["np"]]
    # the kv line is shorter than the recorded share line, so a long history renders shorter under a condition
    ratios = [lens[2 * n + 1] / lens[2 * n] for n in range(len(items))]
    odd = [it["id"] for it, r in zip(items, ratios) if not 0.7 < r < 1.3]
    print(f"[check] B0 renders to the recorded prompt tokens: {len(items) - len(off)}/{len(items)}; RXcap@0.93 / B0 "
          f"rendered length {min(ratios):.3f}-{max(ratios):.3f} (outside 0.7-1.3: {len(odd)})", flush=True)
    n_bad = sum(problems.values()) + len(off) + len(odd)
    print(f"[check] {'OK' if not n_bad else 'PROBLEMS'}", flush=True)
    return 0 if not n_bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("items")
    c = sub.add_parser("check")
    c.add_argument("--workers", type=int, default=16)
    sub.add_parser("freeze")
    r = sub.add_parser("run")
    r.add_argument("--url", default=os.environ.get("RD_URL"))
    sub.add_parser("analyze")
    a = ap.parse_args()
    if a.cmd == "items":
        if ITEMS.exists():
            raise SystemExit(f"{ITEMS} exists; the items are drawn once")
        OUT.mkdir(parents=True, exist_ok=True)
        data = build_items()
        ITEMS.write_text(json.dumps(data, indent=1) + "\n")
        print(f"[items] {data['counts']} from {data['sessions']} sessions")
    elif a.cmd == "check":
        sys.exit(check(a.workers))
    elif a.cmd == "freeze":
        freeze()
    elif a.cmd == "run":
        if not a.url:
            raise SystemExit("give --url (or RD_URL)")
        sys.exit(run(a.url))
    else:
        write_tables()
        print((K.TABLES / "kvwait2.md").read_text())


if __name__ == "__main__":
    main()
