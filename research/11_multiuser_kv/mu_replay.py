#!/usr/bin/env python3
"""Trace replay of recorded 11_multiuser_kv cells under alternative serving policies.

`prepare --src n16` turns every session of a recorded cell into a replay plan: each model call's
exact prompt token ids (research/10_rope_shift/render.py reproduces vLLM's /v1/messages prompt; the
rendered length is checked against the usage the server reported), its recorded output length, and
the gap before it (tool + harness time inside a turn; the think pause plus quiescence at a turn
boundary; process start before the first call). Prompts are stored delta-encoded per session
(common prefix with the previous call + suffix) in data/multiuser_replay/plans/<src>/.

`run --cond r_<src>_<policy>` replays the cell closed-loop against one server: the same N slots as
live, each replaying its recorded sequence of sessions (warm-up filler, counted sessions, drain
fillers) with the recorded gaps; a call is sent `gap` seconds after the previous call of its chain
returned, as `/v1/completions` with the prompt ids, `max_tokens` = recorded output tokens and
`ignore_eos`, tagged like live (X-Request-Id `<label>~r<policy>~<call>~<purpose>~root`,
X-Session-ID, X-Vllm-Priority = the session's live arrival index). Generated tokens differ from
live, which does not change reuse: Qwen3's template re-renders earlier assistant turns differently
from the generation prompt, so no output block is ever reused live either. Client-side policies:
`turn_gate_frac` (at most frac*N sessions mid-turn; others wait at the turn boundary) and
`think_scale`. Server-side policies come from the condition's vLLM flags / MU_* environment.

    python research/11_multiuser_kv/mu_replay.py prepare --src n16 [--workers 16]
    python research/11_multiuser_kv/mu_replay.py run --cond r_n16_default --base-url URL --out-dir DIR
    python research/11_multiuser_kv/mu_replay.py show --src n16
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import lzma
import os
import sys
import time
from multiprocessing import get_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import mu_workloads as mw  # noqa: E402

DIR = Path(__file__).resolve().parent
CELLS = mw.DATA / "cells"
PLANS = DIR / "data" / "multiuser_replay" / "plans"
CHAIN = {"lead", "compaction_summary", "one_shot"}         # purposes on the session's critical path
GAP_CAP_S = 60.0                                            # mu_sweep's gaps are U(5,30) s
CODES = {"lead": "ld", "memory_extract": "mx", "memory_recall": "mr", "memory_consolidate": "mc",
         "compaction_summary": "cs", "one_shot": "os", "teammate": "tm"}


def mw_stagger_cap() -> float:
    return 300.0 + 60.0                                     # mu_sweep's STAGGER_S plus jitter


def read_jsonl(path: Path) -> list[dict]:
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass                                     # a killed session's half-written last line
    return out


def lcp(a: list[int], b: list[int]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# -- prepare ---------------------------------------------------------------------------------------------
_RENDERER = None


def _renderer():
    global _RENDERER
    if _RENDERER is None:
        import render                                     # research/10_rope_shift/render.py
        _RENDERER = render.Renderer(render.MODELS["qwen32b"], max_model_len=mw.MAX_MODEL_LEN)
    return _RENDERER


def prepare_session(job: tuple[str, str, dict]) -> dict:
    """Render one session's calls; write <out>/<label>.npz; return its plan row (no ids)."""
    import numpy as np

    run_dir, out_dir, row = job
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    label = row["label"]
    calls = read_jsonl(run_dir / f"{label}.requests.jsonl.xz")
    think = list(row.get("think_s") or [])
    rend = _renderer()
    arrays, plan_calls = {}, []
    prev_ids: list[int] = []
    prev_recv = row["t0"]
    turn = None
    gap_index = 0
    mismatches = 0
    for c in calls:
        purpose = c.get("purpose") or "unknown"
        t_send = c.get("t_send")
        if t_send is None:
            continue
        chain = purpose in CHAIN
        new_turn = chain and c.get("turn_id") != turn and turn is not None
        entry = {"call": c["call_index"], "purpose": purpose, "code": CODES.get(purpose, "un"),
                 "turn": c.get("turn_id"), "chain": chain, "gap": round(t_send - prev_recv, 4) if chain else None,
                 "think": None, "live_s": c.get("duration_ms", 0) / 1000.0, "kind": "call"}
        if new_turn and gap_index < len(think):
            entry["think"] = think[gap_index] * float(row.get("think_scale") or 1.0)
            gap_index += 1
        if chain:
            turn = c.get("turn_id")
        if c.get("refused") or ("response" not in c and not c.get("overflow")):
            entry["kind"] = "refused" if c.get("refused") else "error"
        elif c.get("overflow"):
            entry["kind"] = "overflow"                    # rejected at once live: no server work to replay
        else:
            usage = c["response"].get("usage") or {}
            reported = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) \
                + int(usage.get("cache_creation_input_tokens") or 0)
            _, ids = rend.render(c)
            if len(ids) != reported:
                mismatches += 1
            entry.update(n_prompt=len(ids), reported=reported, n_out=int(usage.get("output_tokens") or 0),
                         live_cached=int(usage.get("cache_read_input_tokens") or 0))
            base = lcp(prev_ids, ids)
            arrays[f"c{c['call_index']}_lcp"] = np.array([base], dtype=np.int64)
            arrays[f"c{c['call_index']}_suf"] = np.array(ids[base:], dtype=np.int32)
            entry["sha1"] = hashlib.sha1(np.array(ids, dtype=np.int32).tobytes()).hexdigest()[:16]
            prev_ids = ids
        if not chain:                                     # background call: anchored to the last chain return
            entry["offset"] = round(t_send - prev_recv, 4)
        if chain and c.get("t_recv") is not None:
            prev_recv = c["t_recv"]
        plan_calls.append(entry)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / f"{label}.npz", **arrays)
    return {**{k: row[k] for k in ("label", "slot", "kind", "filler", "arrival", "t0", "t1", "status")},
            "calls": plan_calls, "mismatches": mismatches}


def prepare(args) -> None:
    src = args.src
    cell = Path(args.cells_root) / src
    log = read_jsonl(cell / "cell_log.jsonl")
    out_dir = Path(args.plans_root) / src
    jobs = []
    for rec in log:
        run_dir = cell / "runs" / rec["label"]
        meta_path = run_dir / f"{rec['label']}.meta.json"
        if not meta_path.exists() or not (run_dir / f"{rec['label']}.requests.jsonl.xz").exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("attempt") != rec.get("attempt", 1) and not rec.get("filler"):
            continue                                       # a retried session: replay its final attempt only
        row = {"label": rec["label"], "slot": rec["slot"], "kind": rec["kind"], "filler": rec["filler"],
               "arrival": rec["arrival"], "t0": rec["t0"], "t1": rec["t1"], "status": rec["status"],
               "think_s": meta.get("think_s"), "think_scale": meta.get("think_scale", 1.0)}
        jobs.append((str(run_dir), str(out_dir), row))
    print(f"[prepare] {src}: {len(jobs)} sessions, {args.workers} workers", flush=True)
    # spawn, not fork: a forked child that imports torch/vLLM after the parent did can hang
    rows = []
    with get_context("spawn").Pool(args.workers) as pool:
        for i, row in enumerate(pool.imap_unordered(prepare_session, jobs, chunksize=1), 1):
            rows.append(row)
            print(f"[prepare] {i}/{len(jobs)} {row['label']} calls={len(row['calls'])} "
                  f"mismatches={row['mismatches']}", flush=True)
    rows.sort(key=lambda r: (r["slot"], r["t0"]))
    bad = sum(r["mismatches"] for r in rows)
    calls = sum(1 for r in rows for c in r["calls"] if c["kind"] == "call")
    meta = {"src": src, "sessions": len(rows), "calls": calls, "render_mismatches": bad,
            "n": mw.cond(src)["n"], "built": time.strftime("%FT%T")}
    with lzma.open(out_dir / "plan.jsonl.xz", "wt", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r) + "\n")
    (out_dir / "plan_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta), flush=True)


# -- run -------------------------------------------------------------------------------------------------
class Replay:
    def __init__(self, args):
        import numpy as np

        self.np = np
        self.args = args
        self.c = mw.cond(args.cond)
        self.policy = self.c["replay"]
        self.src = self.policy["src"]
        self.n = self.c["n"]
        plan_dir = Path(args.plans_root) / self.src
        self.plan = read_jsonl(plan_dir / "plan.jsonl.xz")
        self.plan_dir = plan_dir
        self.tag = self.policy["policy"]
        self.think_scale = float(self.policy.get("think_scale", 1.0))
        # closed-loop throughput over a fixed window (metrics after the warm-up) instead of the whole cell
        self.window_s = float(args.window_s if args.window_s is not None else self.policy.get("window_s", 0) or 0)
        frac = self.policy.get("turn_gate_frac")
        self.gate = asyncio.Semaphore(max(1, int(round(frac * self.n)))) if frac else None
        self.priority = bool(self.policy.get("priority"))
        self.out = Path(args.out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.results = (self.out / "replay_calls.jsonl").open("a", encoding="utf-8")
        self.counted = {r["label"] for r in self.plan if not r["filler"]}
        self.counted_done: set[str] = set()
        self.all_counted = asyncio.Event()
        self.t_start = None

    def ids(self, label: str) -> dict:
        data = self.np.load(self.plan_dir / f"{label}.npz")
        out, prev = {}, []
        calls = sorted({int(k[1:].split("_")[0]) for k in data.files})
        for call in calls:
            base = int(data[f"c{call}_lcp"][0])
            full = prev[:base] + data[f"c{call}_suf"].tolist()
            out[call] = full
            prev = full
        return out

    async def call(self, client, session: dict, entry: dict, ids: list[int]) -> None:
        rid = f"{session['label']}~r{self.tag}~{entry['call']:05d}~{entry['code']}~root"
        headers = {"X-Request-Id": rid, "X-Session-ID": f"{session['label']}~r{self.tag}"}
        if self.priority:
            headers["X-Vllm-Priority"] = str(session["arrival"])
        body = {"model": mw.MODEL, "prompt": ids, "max_tokens": max(1, entry["n_out"]), "ignore_eos": True}
        t_send = time.time()
        status, usage = "ok", {}
        try:
            response = await client.post("/v1/completions", json=body, headers=headers)
            if response.status_code != 200:
                status = f"http {response.status_code}: {response.text[:300]}"
            else:
                usage = response.json().get("usage") or {}
        except Exception as exc:
            status = f"{type(exc).__name__}: {exc}"[:300]
        t_recv = time.time()
        details = usage.get("prompt_tokens_details") or {}
        self.results.write(json.dumps({"label": session["label"], "call": entry["call"], "code": entry["code"],
                                       "turn": entry.get("turn"), "t0": self.t_start_wall,
                                       "rid": rid, "t_send": t_send, "t_recv": t_recv, "status": status,
                                       "n_prompt": usage.get("prompt_tokens"), "n_out": usage.get("completion_tokens"),
                                       "cached": details.get("cached_tokens"), "planned_out": entry["n_out"],
                                       "live_s": entry["live_s"], "filler": session["filler"]}) + "\n")
        self.results.flush()

    async def session(self, client, session: dict) -> None:
        ids = self.ids(session["label"])
        background = []
        last_recv = time.monotonic()
        holding = False
        turn = None
        for entry in session["calls"]:
            if not entry["chain"]:
                async def later(e=entry, anchor=last_recv):
                    await asyncio.sleep(max(0.0, anchor + e["offset"] - time.monotonic()))
                    if e["kind"] == "call":
                        await self.call(client, session, e, ids[e["call"]])
                background.append(asyncio.create_task(later()))
                continue
            gap = entry["gap"] or 0.0
            if entry["think"] is not None:
                gap = max(0.0, gap - entry["think"] + entry["think"] * self.think_scale)
            if self.gate is not None and entry["turn"] != turn:
                if holding:
                    self.gate.release()
                    holding = False
            await asyncio.sleep(max(0.0, last_recv + gap - time.monotonic()))
            if self.gate is not None and entry["turn"] != turn:
                await self.gate.acquire()                 # a new turn waits for a free mid-turn slot
                holding = True
            turn = entry["turn"]
            if entry["kind"] == "call":
                await self.call(client, session, entry, ids[entry["call"]])
            else:
                await asyncio.sleep(entry["live_s"])      # refused / overflowed live: no server work
            last_recv = time.monotonic()
        if holding:
            self.gate.release()
        for task in background:
            await task

    async def slot(self, client, slot: int, sessions: list[dict]) -> None:
        # a cell resumed by a later job has hour-long holes between sessions: cap the recorded
        # stagger and inter-session gaps at what the sweep can produce
        start = self.t_start + min(mw_stagger_cap(), sessions[0]["t0"] - self.cell_t0)
        await asyncio.sleep(max(0.0, start - time.monotonic()))
        prev_t1 = None
        for session in sessions:
            if prev_t1 is not None:
                await asyncio.sleep(min(GAP_CAP_S, max(0.0, session["t0"] - prev_t1)))
            if session["kind"] == "drain":
                task = asyncio.create_task(self.session(client, session))
                waiter = asyncio.create_task(self.all_counted.wait())
                await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
                for t in (task, waiter):
                    if not t.done():
                        t.cancel()
            else:
                await self.session(client, session)
                if session["label"] in self.counted:
                    self.counted_done.add(session["label"])
                    if self.counted_done >= self.counted:
                        self.all_counted.set()
            prev_t1 = session["t1"]

    async def main(self) -> int:
        import httpx

        slots: dict[int, list[dict]] = {}
        for session in self.plan:
            slots.setdefault(session["slot"], []).append(session)
        self.cell_t0 = min(s["t0"] for s in self.plan)
        self.t_start = time.monotonic() + 5
        self.t_start_wall = time.time() + 5
        limits = httpx.Limits(max_connections=4 * self.n + 8, max_keepalive_connections=4 * self.n + 8)
        async with httpx.AsyncClient(base_url=self.args.base_url, timeout=3600, limits=limits) as client:
            tasks = [asyncio.create_task(self.slot(client, i, s)) for i, s in sorted(slots.items())]
            limits_s = [x for x in (self.args.hard_stop - time.time() if self.args.hard_stop else None,
                                    self.window_s or None) if x]
            done, pending = await asyncio.wait(tasks, timeout=min(limits_s) if limits_s else None)
            windowed = bool(pending) and self.window_s and (time.monotonic() - self.t_start) >= self.window_s - 5
            for t in pending:
                t.cancel()
        complete = self.counted_done >= self.counted or bool(windowed)
        meta = {"cond": self.args.cond, "src": self.src, "policy": self.policy, "n": self.n, "window_s": self.window_s,
                "counted": len(self.counted), "counted_done": len(self.counted_done), "complete": complete,
                "wall_s": round(time.monotonic() - self.t_start, 1)}
        (self.out / "replay_meta.json").write_text(json.dumps(meta, indent=2))
        if complete:
            done = Path(self.args.cells_root) / self.args.cond
            done.mkdir(parents=True, exist_ok=True)
            (done / "COMPLETE").write_text(time.strftime("%FT%T") + "\n")
        print(json.dumps(meta), flush=True)
        return 0 if complete else 4


def show(args) -> None:
    plan = read_jsonl(Path(args.plans_root) / args.src / "plan.jsonl.xz")
    calls = [c for s in plan for c in s["calls"]]
    kinds = {}
    for c in calls:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(json.dumps({"sessions": len(plan), "slots": len({s["slot"] for s in plan}),
                      "counted": sum(not s["filler"] for s in plan), "calls": kinds,
                      "render_mismatches": sum(s["mismatches"] for s in plan)}, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="render a recorded cell into a replay plan (CPU)")
    p.add_argument("--src", required=True, choices=sorted(n for n in mw.CONDITIONS if not n.startswith("r_")))
    p.add_argument("--workers", type=int, default=16)
    for q in (p,):
        q.add_argument("--cells-root", default=str(CELLS))
    r = sub.add_parser("run", help="replay one r_* condition against a server")
    r.add_argument("--cond", required=True, choices=sorted(n for n in mw.CONDITIONS if n.startswith("r_")))
    r.add_argument("--base-url", required=True)
    r.add_argument("--out-dir", required=True)
    r.add_argument("--hard-stop", type=float, default=0.0)
    r.add_argument("--window-s", type=float, default=None, help="replay only this long (default: the condition's)")
    r.add_argument("--cells-root", default=str(CELLS))
    s = sub.add_parser("show", help="summarize a prepared plan")
    s.add_argument("--src", required=True)
    for q in (p, r, s):
        q.add_argument("--plans-root", default=str(PLANS))
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args)
    elif args.cmd == "show":
        show(args)
    else:
        sys.exit(asyncio.run(Replay(args).main()))


if __name__ == "__main__":
    main()
