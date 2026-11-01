#!/usr/bin/env python3
"""Topic 19 follow-up (2026-10-29): does MiniMax-M2.7 call `wait` more often when far more users compete?

The user's hypothesis: N <= 32 was too few users for four GPUs, so the model was not crowded enough to wait. These
cells repeat topic 19's `m_wait` setup (the `wait` tool, a [server] line every round in the share view, the server
paragraph) at N=64 and N=128 on topic 19's server and harness, unchanged: every hash in prereg.sha256 and
followups/highn_prereg.sha256 is checked before a cell starts. Warm-up 20 min, window 40 min; the warm-up fillers
stop inside the warm-up (session_est_s = 1,200 s). A `none` cell at the lowest N whose verdict is "rare" or
"uses it" runs only then. The rules are in followups/highn_prereg.md.

    python research/19_efficiency_signals/ef_highn.py conditions     # data/conditions_highn.json
    python research/19_efficiency_signals/ef_highn.py dryrun         # CPU: the sweep at N=128 against a fake server
    python research/19_efficiency_signals/ef_highn.py chain x64_m_wait x128_m_wait
    python research/19_efficiency_signals/ef_highn.py report         # data/tables/highn.{md,json}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_pipeline as ep  # noqa: E402  (topic 19's data root, cap, lanes and conditions)
import ef_workloads as ew  # noqa: E402
import mu_workloads as mw  # noqa: E402

WARMUP_S, WINDOW_S, SESSION_EST_S = 1200, 2400, 1200
NS = (64, 128)
CONDITIONS_JSON = ew.DATA / "conditions_highn.json"
PREREG_SHA = DIR / "followups" / "highn_prereg.sha256"
BASELINE = ("w16_m_wait", "w32_m_wait")              # topic 19's m_wait cells, N = 16 and 32
CONTEXT = ("w16_none", "w32_none")                   # the same load without signals
RARE, USES = 0.001, 0.01                             # r < 0.1 %: still ignores it; 0.1-1 %: rare; >= 1 %: uses it
SERVER_LINE = re.compile(r"\[server\] KV pool ([\d,]+) tokens; sessions ahead of you hold ([\d,]+); your context "
                         r"([\d,]+), ([\d,]+) still cached; (\d+) requests waiting")
SIGNAL_RE = re.compile(r"\[server\]|\bserver\b|\bcrowd\w*|\bcongest\w*|\bother (?:sessions|users)\b|"
                       r"\brequests waiting\b|\bKV pool\b", re.I)
THINK_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.S)


def conditions() -> dict:
    out = {}
    for n in NS:
        for arm in ("m_wait", "none"):
            c = ew.cell("highn", n, arm, WARMUP_S, WINDOW_S, cycle=True)
            c["session_est_s"] = SESSION_EST_S
            out[f"x{n}_{arm}"] = c
    return out


mw.CONDITIONS.update(conditions())


def register() -> Path:
    """Write topic 19's conditions plus these to data/conditions_highn.json for the sweep and session
    subprocesses (MU_EXTRA_CONDITIONS); ef_workloads' own data/conditions.json is left alone."""
    text = json.dumps({**ew.conditions(), **conditions()}, indent=1) + "\n"
    if not CONDITIONS_JSON.exists() or CONDITIONS_JSON.read_text(encoding="utf-8") != text:
        CONDITIONS_JSON.write_text(text, encoding="utf-8")
    os.environ["MU_EXTRA_CONDITIONS"] = str(CONDITIONS_JSON)
    return CONDITIONS_JSON


def changed(listing: Path) -> list[str]:
    """Files of a sha256 listing that are missing or no longer match (topic-relative paths first, then repo)."""
    bad = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        name = name.strip().lstrip("*")
        path = DIR / name if (DIR / name).exists() else REPO / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            bad.append(name)
    return bad


def run(cond: str) -> int:
    register()
    if cond.endswith("_none"):                     # conditional: only the cell the report named
        rep = ew.DATA / "tables" / "highn.json"
        named = json.loads(rep.read_text())["conditional_none"] if rep.exists() else None
        if named != cond:
            print(f"[highn] {cond} refused: the report names {named!r} as the conditional none cell", flush=True)
            return 5
    bad = changed(DIR / "prereg.sha256")
    bad += changed(PREREG_SHA) if PREREG_SHA.exists() else [f"{PREREG_SHA.name} (missing: freeze it first)"]
    if bad:
        print(f"[highn] {cond} refused: frozen files changed or missing: {bad}", flush=True)
        return 4
    return ep.run(cond)


# -- CPU dry run ------------------------------------------------------------------------------------------------------
def dryrun(users: int, warmup_s: float, window_s: float, tmp_root: str | None) -> int:
    """The sweep at N=`users` with this follow-up's settings (fillers stopped inside the warm-up, cycled specs)
    against test_ef_e2e's fake MiniMax server: every slot runs, fillers cycle, sessions end, the cell completes."""
    from http.server import ThreadingHTTPServer
    import test_ef_e2e as te
    tmp = Path(tempfile.mkdtemp(prefix="ef_highn_dry_", dir=tmp_root))
    c = ew.cell("test", users, "m_wait", warmup_s, window_s, cycle=True)
    c["session_est_s"] = warmup_s
    (tmp / "conditions.json").write_text(json.dumps({"t_highn": c}))
    port = te.free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), te.Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "MU_EXTRA_CONDITIONS": str(tmp / "conditions.json")}
    cmd = [sys.executable, str(te.WT / "wt_sweep.py"), "run", "--cond", "t_highn", "--users", str(users),
           "--base-url", f"http://127.0.0.1:{port}", "--lane-root", str(tmp / "lanes"), "--sessions",
           str(ew.SESSIONS), "--cells-root", str(tmp / "cells"), "--think-scale", "0.02", "--stagger-s", "10"]
    t0 = time.time()
    out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=3600)
    server.shutdown()
    root = tmp / "cells" / "t_highn"
    log = [json.loads(x) for x in (root / "cell_log.jsonl").read_text().splitlines() if x.strip()] \
        if (root / "cell_log.jsonl").exists() else []
    sess = [r for r in log if "label" in r]
    fillers = [r for r in sess if r.get("filler")]
    counted = [r for r in sess if not r.get("filler")]
    win = json.loads((root / "window.json").read_text()) if (root / "window.json").exists() else {}
    ws, we = win.get("window_start", 0), win.get("window_end", 0)
    late = [r for r in fillers if (r.get("t1") or float("inf")) > ws + 5]
    # a session launched in the window's last seconds can be stopped before it writes anything ("killed")
    racing = [r for r in sess if str(r.get("status")).startswith("killed") and r.get("t0", 0) > we - 2]
    status = Counter(r.get("status") for r in sess if r not in racing)
    waits = sum(1 for m in te.Fake.messages if "wait" in m["tools"])
    checks = {"sweep exit 0": out.returncode == 0, "COMPLETE": (root / "COMPLETE").exists(),
              f"all {users} slots ran": len({r["slot"] for r in sess}) == users,
              "fillers cycled past the 32 specs": len(fillers) >= min(users, 33),
              "fillers ended inside the warm-up": not late,
              "counted sessions started": bool(counted),
              "every session ended ok/budget/terminated/filler-stopped":
                  set(status) <= {"ok", "budget", "terminated", "filler-stopped"},
              "requests carried the wait tool": waits > 0}
    print(f"[dryrun] N={users}, {time.time() - t0:.0f} s, {len(sess)} sessions ({len(fillers)} fillers), status "
          f"{dict(status)}, {len(racing)} launched in the window's last 2 s and stopped before starting, "
          f"{len(te.Fake.messages)} requests; cell {root}")
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if out.returncode != 0:
        print(out.stdout[-3000:])
    return 0 if all(checks.values()) else 1


# -- report -----------------------------------------------------------------------------------------------------------
def poisson_ci(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (Garwood) interval for a rate of k events in n calls."""
    def cdf(x: int, lam: float) -> float:          # P(X <= x)
        if lam <= 0:
            return 1.0
        return sum(math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1)) for i in range(x + 1))

    def solve(f, target: float) -> float:          # f decreasing in lam
        lo, hi = 0.0, max(10.0, 10.0 * (k + 1))
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) > target else (lo, mid)
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else solve(lambda lam: cdf(k - 1, lam), 1 - alpha / 2)
    upper = solve(lambda lam: cdf(k, lam), alpha / 2)
    return lower / n, upper / n


def verdict(r: float) -> str:
    return "uses it" if r >= USES else "rare" if r >= RARE else "still ignores it"


def thinking(blocks: list) -> str:
    out = []
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "text":
            out += THINK_RE.findall(b.get("text") or "")
        elif isinstance(b, dict) and b.get("type") == "thinking":
            out.append(b.get("thinking") or "")
    return "\n".join(out)


def q(xs: list, p: float):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def cell_row(cond: str) -> dict:
    import ef_analyze as ea
    w = ea.EfCell(cond)
    lead = [r for r in w.lead_in_window(False) if not r.get("filler")]
    uses = {(r["label"], r["call"]): w.uses(r) for r in lead}
    k_wait = sum("wait" in u for u in uses.values())
    k_pacing = sum(any(n in ea.PACING for n in u) for u in uses.values())
    wait_only = sum(w.wait_only(r) for r in lead)
    shown = []
    for r in lead:
        for t in w.newest_texts(r):
            m = SERVER_LINE.search(t)
            if m:
                pool, ahead, ctx, cached, waiting = (int(g.replace(",", "")) for g in m.groups())
                shown.append({"ahead_over_pool": ahead / pool if pool else None, "waiting": waiting,
                              "cached_share": cached / ctx if ctx else None})
                break
    mentions = sum(bool(SIGNAL_RE.search(thinking(w.content.get((r["label"], r["call"]))))) for r in lead)
    crowded = w.crowded_at()
    cr = [crowded[k] for k in uses if k in crowded]
    np_tot = sum(r.get("np") or 0 for r in lead)
    lat = [r.get("lat") for r in lead]
    queue = [(r.get("parts") or {}).get("queue") for r in lead]
    waits = [e["data"] for _, e in w.events("wait_detail") if w.inside(ea.wa.ts(e))]
    n = len(lead)
    lo, hi = poisson_ci(k_wait, n) if n else (None, None)
    return {"cond": cond, "n_users": w.n, "arm": w.arm, "lead_calls": n, "calls_with_wait": k_wait,
            "rate": k_wait / n if n else None, "rate_ci": [lo, hi], "verdict": verdict(k_wait / n) if n else None,
            "calls_with_pacing": k_pacing, "wait_only_calls": wait_only, "waits_run": len(waits),
            "wait_requested_s": [d.get("requested_s") for d in waits],
            "reasoning_mentions": mentions, "server_lines": len(shown),
            "ahead_over_pool_p50": q([s["ahead_over_pool"] for s in shown], 0.5),
            "ahead_over_pool_p90": q([s["ahead_over_pool"] for s in shown], 0.9),
            "waiting_p50": q([s["waiting"] for s in shown], 0.5), "waiting_p90": q([s["waiting"] for s in shown], 0.9),
            "cached_share_p50": q([s["cached_share"] for s in shown], 0.5),
            "crowded_share": sum(cr) / len(cr) if cr else None,
            "hit_rate": sum(r.get("cached") or 0 for r in lead) / np_tot if np_tot else None,
            "call_p50": q(lat, 0.5), "call_p95": q(lat, 0.95), "queue_p50": q(queue, 0.5), "queue_p95": q(queue, 0.95)}


def report(conds: list[str]) -> dict:
    rows = {}
    for cond in conds:
        if not (ew.CELLS / cond / "COMPLETE").exists():
            print(f"[report] {cond}: no complete cell, skipped", flush=True)
            continue
        print(f"[report] {cond} ...", flush=True)
        rows[cond] = cell_row(cond)
    base = [rows[c] for c in BASELINE if c in rows]
    kb, nb = sum(r["calls_with_wait"] for r in base), sum(r["lead_calls"] for r in base)
    pooled = {"calls_with_wait": kb, "lead_calls": nb, "rate": kb / nb if nb else None,
              "rate_ci": list(poisson_ci(kb, nb)) if nb else None}
    for r in rows.values():
        if r["arm"] == "m_wait" and nb and r["lead_calls"]:
            r["ratio_to_baseline"] = r["rate"] / pooled["rate"] if pooled["rate"] else None
            r["clearly_above_baseline"] = r["rate_ci"][0] > pooled["rate_ci"][1]
    triggered = next((c for c in (f"x{n}_m_wait" for n in NS)
                      if c in rows and rows[c]["verdict"] in ("rare", "uses it")), None)
    out = {"baseline": pooled, "cells": rows, "conditional_none": triggered.replace("m_wait", "none") if triggered else None}
    pct = lambda x: "—" if x is None else f"{100 * x:.2f} %"  # noqa: E731
    num = lambda x, nd=0: "—" if x is None else f"{x:,.{nd}f}"  # noqa: E731
    lines = ["# High-N follow-up: does MiniMax-M2.7 wait more when far more users compete? (ef_highn.py)", "",
             f"Pooled N ≤ 32 baseline ({' + '.join(BASELINE)}): {kb} of {nb:,} lead calls carry `wait` "
             f"= {pct(pooled['rate'])} [{pct((pooled['rate_ci'] or [None])[0])}, "
             f"{pct((pooled['rate_ci'] or [None, None])[1])}].", "",
             "| cell | N | lead calls | with `wait` | rate [95 %] | verdict | wait-only | reasoning mentions | "
             "ahead / pool p50 (p90) | requests waiting p50 (p90) | crowded share | hit rate | call p50 / p95 (s) | "
             "queue p50 / p95 (s) |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows.values():
        lines.append(
            f"| {r['cond']} | {r['n_users']} | {r['lead_calls']:,} | {r['calls_with_wait']} | "
            + (f"{pct(r['rate'])} [{pct(r['rate_ci'][0])}, {pct(r['rate_ci'][1])}] | {r['verdict']} | "
               if r["arm"] == "m_wait" else "— | — | ")
            + f"{r['wait_only_calls']} | {r['reasoning_mentions']} | {num(r['ahead_over_pool_p50'], 2)} "
              f"({num(r['ahead_over_pool_p90'], 2)}) | {num(r['waiting_p50'])} ({num(r['waiting_p90'])}) | "
              f"{pct(r['crowded_share'])} | {pct(r['hit_rate'])} | {num(r['call_p50'], 1)} / {num(r['call_p95'], 1)} | "
              f"{num(r['queue_p50'], 1)} / {num(r['queue_p95'], 1)} |")
    lines += ["", f"Conditional `none` cell: {out['conditional_none'] or 'not triggered'}.", ""]
    tables = ew.DATA / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    (tables / "highn.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tables / "highn.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("conditions", help="write data/conditions_highn.json")
    d = sub.add_parser("dryrun", help="CPU: the sweep against a fake server")
    d.add_argument("--users", type=int, default=128)
    d.add_argument("--warmup-s", type=float, default=30)
    d.add_argument("--window-s", type=float, default=90)
    d.add_argument("--tmp", default=os.environ.get("WT_TMP"))
    c = sub.add_parser("cell", help="run one cell on GPUs 0-3")
    c.add_argument("--cond", required=True, choices=sorted(conditions()))
    ch = sub.add_parser("chain", help="run cells one after another; stop at the first failure")
    ch.add_argument("conds", nargs="+", choices=sorted(conditions()))
    r = sub.add_parser("report", help="data/tables/highn.{md,json}")
    r.add_argument("--conds", default=",".join([*BASELINE, *CONTEXT, *(f"x{n}_{a}" for n in NS
                                                                       for a in ("m_wait", "none"))]))
    args = ap.parse_args()
    if args.cmd == "conditions":
        print(register())
    elif args.cmd == "dryrun":
        sys.exit(dryrun(args.users, args.warmup_s, args.window_s, args.tmp))
    elif args.cmd == "cell":
        sys.exit(run(args.cond))
    elif args.cmd == "chain":
        for cond in args.conds:
            print(f"[chain] {cond} at {time.strftime('%FT%TZ', time.gmtime())}", flush=True)
            rc = run(cond)
            if rc != 0:
                print(f"[chain] {cond} failed (rc {rc}): the chain stops here", flush=True)
                sys.exit(rc)
        print("[chain] done", flush=True)
    else:
        report(args.conds.split(","))


if __name__ == "__main__":
    main()
