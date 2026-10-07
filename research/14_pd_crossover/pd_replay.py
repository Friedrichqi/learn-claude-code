#!/usr/bin/env python3
"""Paired trace replay for research/14_pd_crossover (PLAN.md §6): 11's mu_replay, extended to any N.

    python research/14_pd_crossover/pd_replay.py prepare --levels DIR [DIR ...] --pool l2 [--min 48] [--workers 16]
    python research/14_pd_crossover/pd_replay.py mkplan --pool l2 --n 8           # -> plans/l2_n08/
    python research/14_pd_crossover/pd_replay.py run --level pd_1p1d_L_n08_rep --base-url URL --out-dir LV \
        --hard-stop EPOCH
prepare  the pool: completed sessions (status ok or budget) of the given live level directories (LIGHT, think 0),
         one recording per spec (the earliest t0), ordered by spec; rendered with mu_replay.prepare_session (exact
         prompt ids through 10's renderer, recorded output lengths, recorded gaps) into plans/<pool>/ (npz per
         session + pool.jsonl.xz). Fails if fewer than --min sessions.
mkplan   the plan for N slots: slot i replays pool[i], pool[i+N], pool[i+2N], ...; synthetic t0 / t1 so that 11's
         slot loop starts slot i at i*S/N + U(0,5) s (S = min(300, 60N), mu_sweep's stagger) and waits U(5,30) s
         between its sessions (mu_sweep's gap), both seeded by (slot, k). Every arm at this N therefore replays the
         same calls in the same order. npz files are symlinked from the pool.
run      PDReplay = mu_replay.Replay plus a session_end hint (POST /tm/hint) after each session, so the router stops
         counting it, and temperature 0 on every call (cost-identical with ignore_eos). The condition is the level's
         entry in data/conditions.json (MU_EXTRA_CONDITIONS); calls go to /v1/completions through the router.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import lzma
import os
import random
import re
import sys
from multiprocessing import get_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

DIR = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PD_DATA") or DIR / "data")
PLANS = DATA / "replay" / "plans"
OK_STATUS = ("ok", "budget")


def _ensure_conditions() -> None:
    cj = DATA / "conditions.json"
    if not os.environ.get("MU_EXTRA_CONDITIONS"):
        if not cj.exists():
            import pd_workloads
            pd_workloads.export_conditions(cj)
        os.environ["MU_EXTRA_CONDITIONS"] = str(cj)


def read_jsonl(path: Path) -> list[dict]:
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def write_jsonl_xz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(path, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def spec_key(spec: str) -> tuple:
    m = re.match(r"([A-Za-z]+)(\d+)$", spec or "")
    return (m.group(1), int(m.group(2))) if m else (spec or "", 0)


# -- prepare ------------------------------------------------------------------------------------------------------
def pool_rows(level_dirs: list[Path]) -> list[dict]:
    best: dict[str, dict] = {}
    for ld in level_dirs:
        log = ld / "cell_log.jsonl"
        if not log.exists():
            continue
        for row in read_jsonl(log):
            if row.get("status") not in OK_STATUS or row.get("filler") or row.get("kind") != "counted":
                continue
            run_dir = ld / "runs" / row["label"]
            if not (run_dir / f"{row['label']}.requests.jsonl.xz").exists():
                continue
            prev = best.get(row["spec"])
            if prev is None or row["t0"] < prev["t0"]:
                best[row["spec"]] = dict(row, run_dir=str(run_dir), think_s=[], think_scale=0.0)
    return [best[s] for s in sorted(best, key=spec_key)]


def live_level_dirs(cells: list[str]) -> list[Path]:
    """LIGHT, think-0, live level directories of the given cells (no smoke, replay or aborted levels)."""
    import pd_workloads as pw
    out = []
    for cell in cells:
        for lv in sorted((DATA / "cells" / cell / "levels").glob("pd_*")):
            c = (pw.CONDITIONS.get(lv.name) or {}).get("pd") or {}
            if c.get("kind") == "live" and c.get("wl") == "L":
                out.append(lv)
    return out


def prepare(a) -> int:
    import mu_replay                                     # research/11_multiuser_kv
    dirs = [Path(d) for d in (a.levels or [])] + (live_level_dirs(a.cells.split(",")) if a.cells else [])
    rows = pool_rows(dirs)
    if len(rows) < a.min:
        print(f"[pd_replay] pool {a.pool}: only {len(rows)} sessions (< {a.min})", flush=True)
        return 2
    out = PLANS / a.pool
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(r["run_dir"], str(out), r) for r in rows]
    with get_context("spawn").Pool(a.workers) as pool:
        plans = pool.map(mu_replay.prepare_session, jobs, chunksize=1)
    keep = []
    for r, p in zip(rows, plans):
        p.update(spec=r["spec"], src_dir=r["run_dir"])
        keep.append(p)
    write_jsonl_xz(out / "pool.jsonl.xz", keep)
    mism = sum(p["mismatches"] for p in keep)
    calls = sum(1 for p in keep for c in p["calls"] if c["kind"] == "call")
    print(f"[pd_replay] pool {a.pool}: {len(keep)} sessions, {calls} calls, {mism} render-length mismatches",
          flush=True)
    for n in [int(x) for x in (a.mkplans or "").split(",") if x]:
        mkplan(argparse.Namespace(pool=a.pool, n=n, name=None))
    return 0


# -- mkplan -------------------------------------------------------------------------------------------------------
def make_plan(pool: list[dict], n: int) -> list[dict]:
    stagger = min(300, 60 * n)
    out = []
    for i in range(n):
        rng = random.Random(f"slot{i}:start")
        t = i * stagger / n + rng.uniform(0, 5)
        k = 0
        for j in range(i, len(pool), n):
            src = pool[j]
            dur = max(1.0, float(src["t1"]) - float(src["t0"]))
            row = {kk: v for kk, v in src.items() if kk not in ("t0", "t1", "slot", "kind", "filler", "arrival")}
            row.update(slot=i, kind="counted", filler=False, arrival=len(out), t0=round(t, 3),
                       t1=round(t + dur, 3), status="ok")
            out.append(row)
            k += 1
            t = t + dur + random.Random(f"slot{i}:gap{k}").uniform(5, 30)
    return out


def mkplan(a) -> int:
    pool_dir = PLANS / a.pool
    pool = read_jsonl(pool_dir / "pool.jsonl.xz")
    name = a.name or f"{a.pool}_n{a.n:02d}"
    out = PLANS / name
    out.mkdir(parents=True, exist_ok=True)
    plan = make_plan(pool, a.n)
    for row in plan:
        link = out / f"{row['label']}.npz"
        if not link.exists():
            link.symlink_to(os.path.relpath(pool_dir / f"{row['label']}.npz", out))
    write_jsonl_xz(out / "plan.jsonl.xz", plan)
    per_slot = len(plan) / max(1, a.n)
    print(f"[pd_replay] plan {name}: {len(plan)} sessions over {a.n} slots ({per_slot:.1f} per slot)", flush=True)
    return 0


# -- run ----------------------------------------------------------------------------------------------------------
class _ZeroTemp:
    """httpx client wrapper: every JSON body gets temperature 0."""

    def __init__(self, client):
        self._c = client

    async def post(self, path, json=None, headers=None, **kw):
        if isinstance(json, dict) and "prompt" in json:
            json = dict(json, temperature=0.0)
        return await self._c.post(path, json=json, headers=headers, **kw)


def run(a) -> int:
    _ensure_conditions()
    import mu_replay                                     # research/11_multiuser_kv (reads MU_EXTRA_CONDITIONS)

    class PDReplay(mu_replay.Replay):
        async def session(self, client, session: dict) -> None:
            try:
                await super().session(_ZeroTemp(client), session)
            finally:
                try:
                    await client.post("/tm/hint", json={"s": f"{session['label']}~r{self.tag}",
                                                        "ev": "session_end"})
                except Exception:
                    pass

    ns = argparse.Namespace(cond=a.level, base_url=a.base_url, out_dir=a.out_dir, hard_stop=a.hard_stop,
                            window_s=None, cells_root=str(Path(a.out_dir).parent), plans_root=str(PLANS))
    return asyncio.run(PDReplay(ns).main())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--levels", nargs="*", default=[])
    p.add_argument("--cells", default=None, help="take every LIGHT live level of these cells (e.g. l2a,l2b)")
    p.add_argument("--mkplans", default=None, help="then write the plans for these N (e.g. 2,3,4,5,6,8,10,12,16)")
    p.add_argument("--pool", required=True)
    p.add_argument("--min", type=int, default=48)
    p.add_argument("--workers", type=int, default=16)
    p = sub.add_parser("mkplan")
    p.add_argument("--pool", required=True)
    p.add_argument("--n", type=int, required=True)
    p.add_argument("--name", default=None)
    p = sub.add_parser("run")
    p.add_argument("--level", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--hard-stop", type=float, default=0.0)
    a = ap.parse_args()
    return {"prepare": prepare, "mkplan": mkplan, "run": run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
