#!/usr/bin/env python3
"""Topic 19 follow-up (2026-10-30): does MiniMax-M2.7 call `wait` when the prompt gives it a KV-cache threshold?

The user asked for a wait prompt that MiniMax acts on when HBM use approaches 80 %. In topic 19 it called text v3's
`wait` 3 times in 21,081 lead calls, while the KV cache was >= 80 % behind 92-97 % of the [server] lines it read in
the m_wait cells (lines that showed token counts, never that number). This probe samples recorded requests of
w16_m_wait and w32_m_wait again with the KV rule of s15_integrated_harness/hold_runtime.py (wait text v5, the
paragraph wait_kv, S15_STATUS_FORMAT kv / kv_cue, S15_WAIT_KV 0.8) and counts the samples that call `wait`. The
rules, items and pass criteria are in followups/kvwait_prereg.md; `run` checks its sha256 list first.

A recorded request becomes a condition's request in three places, each of which must match exactly once:
  - the system prompt's "wait" paragraph becomes the condition's paragraph;
  - the `wait` tool's v3 description becomes the condition's;
  - every [server] text block becomes the condition's line (hold_runtime.round_line), built from the kv_usage and
    waiting the session saw at that point (the status server's log, server/*/wt_ext.jsonl, state "line", joined in
    order on the line's own numbers); the newest line, this call's, shows the KV level under test instead.
Conditions, S samples each (topic 20's sampling: the model's generation config):
  B0  the request as recorded: v3, the "wait" paragraph, the share line
  T   v3 + "wait" + the kv line            R   v5 + wait_kv + the kv line
  C   v3 + "wait" + the kv_cue line        RC  v5 + wait_kv + the kv_cue line
Screen: 60 items x (B0, the four at newest-line KV 0.62 and 0.93); the frozen rule (pick) chooses the winner.
Confirm: 150 fresh items x (B0, the winner at 0.60, 0.75, 0.85, 0.95). Items are lead calls of counted sessions that
never called wait, by stratum: work (a reply with tool calls after the turn's first call), start (the turn's first
call), final (the turn's answer); the screen and the confirm draw from disjoint sessions.

The GPU part runs only as a client of topic 21's s2a probe server (the user's choice): `run` looks the URL's port up
in that stage's server_info.json and refuses any other server, since its traffic would skew a load or timing stage.

    python research/19_efficiency_signals/ef_kvwait.py items      # data/kvwait/items.json
    python research/19_efficiency_signals/ef_kvwait.py check      # CPU: rewrites, renders, append-only
    python research/19_efficiency_signals/ef_kvwait.py freeze     # followups/kvwait_prereg.sha256
    python research/19_efficiency_signals/ef_kvwait.py run --url URL      # screen, winner, confirm (resumable)
    python research/19_efficiency_signals/ef_kvwait.py analyze    # data/tables/kvwait.{md,json}
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

CELLS = ("w16_m_wait", "w32_m_wait")
CELLS_ROOT = DIR / "data" / "cells"
KV = DIR / "data" / "kvwait"
ITEMS = KV / "items.json"
TABLES = DIR / "data" / "tables"
PREREG = DIR / "followups" / "kvwait_prereg.md"
PREREG_SHA = DIR / "followups" / "kvwait_prereg.sha256"
READY = DIR / "data" / "jobs" / "kvwait.ready"
T21_SERVERS = REPO / "research" / "21_precautionary_work" / "data" / "servers"
HOLD_PATH = REPO / "s15_integrated_harness" / "hold_runtime.py"
# what the run depends on; repo-relative, and checked against the repo only (topic 19's own lists look inside the
# topic folder first, where s15_integrated_harness/hold_runtime.py is the frozen copy topic 19 ran)
FROZEN = [PREREG, Path(__file__).resolve(), ITEMS, HOLD_PATH, Path(cf.__file__).resolve(),
          Path(rr.__file__).resolve(), DIR / "ef_render.py", rr.T19_TEMPLATE]

SEED = 1030
S, INFLIGHT, RENDERERS = 4, 4, 8
CAP_PER_SESSION = 2
SCREEN_N = {"work": 42, "start": 9, "final": 9}
CONFIRM_N = {"work": 105, "start": 22, "final": 23}
SCREEN_LEVELS = (0.62, 0.93)
CONFIRM_LEVELS = (0.60, 0.75, 0.85, 0.95)
WAIT_KV = 0.8
CONDS = {"T": ("v3", "wait", "kv"), "R": ("v5", "wait_kv", "kv"),            # name: wait text, paragraph, line
         "C": ("v3", "wait", "kv_cue"), "RC": ("v5", "wait_kv", "kv_cue")}
PREFER = ("R", "RC", "C", "T")                    # ties: the least per-round text first
RULE = {"low_max": 0.10, "wait_only_max": 0.05, "kept_drop_max": 0.10, "tie": 0.05, "min_high": 0.30}
PASS = {"high_min": 0.50, "high_lo_min": 0.30, "low_max": 0.10, "wait_only_max": 0.05, "final_max": 0.10,
        "kept_drop_max": 0.10, "out_rise_max": 0.15}
DRAWS = 2000
# the share-view line (hold_runtime.format_line, view "share"; as ef_highn.SERVER_LINE)
SERVER_LINE = re.compile(r"\[server\] KV pool ([\d,]+) tokens; sessions ahead of you hold ([\d,]+); your context "
                         r"([\d,]+), ([\d,]+) still cached; (\d+) requests waiting")


def load_hold():
    spec = importlib.util.spec_from_file_location("ef_kvwait_hold_runtime", HOLD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                       # dataclasses look the module up while it loads
    spec.loader.exec_module(mod)
    return mod


HR = load_hold()


class Unbuildable(Exception):
    pass


# -- a condition's request ------------------------------------------------------------------------------------------
def configure(cond: str) -> None:
    """Point hold_runtime at the condition, as the harness would be with these S15_* settings."""
    text, section, line_format = CONDS[cond]
    HR.CONFIG = HR.Config(tools=frozenset({HR.WAIT_TOOL_NAME}), section=section, status_line=True, view="share",
                          wait_text=text, line_format=line_format, wait_kv=WAIT_KV)


def server_blocks(messages: list) -> list[tuple[int, int]]:
    """(message, block) of every [server] text block in user messages, in order."""
    out = []
    for i, m in enumerate(messages):
        if m.get("role") != "user" or isinstance(m.get("content"), str):
            continue
        for k, b in enumerate(m.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text", "")).startswith("[server]"):
                out.append((i, k))
    return out


def rewrite(req: dict, cond: str, lines: list, level: float | None) -> dict:
    """The recorded request under a condition. lines: (kv_usage, waiting) of its [server] blocks in order; the newest
    shows `level`."""
    if cond == "B0":
        return req
    configure(cond)
    out = copy.deepcopy(req)
    old = HR.SERVER_SECTIONS["wait"]
    if not isinstance(out.get("system"), str) or out["system"].count(old) != 1:
        raise Unbuildable("the wait paragraph")
    out["system"] = out["system"].replace(old, HR.server_section())
    waits = [t for t in out.get("tools") or [] if t.get("name") == HR.WAIT_TOOL_NAME]
    if len(waits) != 1 or waits[0].get("description") != HR.WAIT_TEXTS["v3"]:
        raise Unbuildable("the wait description")
    waits[0]["description"] = HR.wait_tool()["description"]
    blocks = server_blocks(out["messages"])
    if not blocks or len(blocks) != len(lines):
        raise Unbuildable("the [server] lines")
    for n, ((i, k), (kv, waiting)) in enumerate(zip(blocks, lines)):
        status = {"kv_usage": level if n == len(blocks) - 1 else kv, "waiting": waiting}
        out["messages"][i]["content"][k] = {**out["messages"][i]["content"][k], "text": HR.round_line(status)}
    return out


def variant_name(cond: str, level: float | None) -> str:
    return "B0" if cond == "B0" else f"{cond}@{level:.2f}"


# -- items ----------------------------------------------------------------------------------------------------------
def ext_lines(cell: str) -> dict[str, list]:
    """Per session label, the status answers its [server] lines were built from: ((ahead, ws, prefix, waiting),
    kv_usage), in the log's order."""
    out = defaultdict(list)
    for path in sorted((CELLS_ROOT / cell / "server").glob("*/wt_ext.jsonl")):
        with path.open() as f:
            for line in f:
                r = json.loads(line)
                if r.get("state") != "line":
                    continue
                o = r.get("out") or {}
                me = o.get("me") or {}
                key = (int(me.get("ahead_ws") or 0), int(me.get("ws") or 0), int(me.get("prefix") or 0),
                       int(o.get("waiting") or 0))
                out[str(r.get("sid", "")).split("~")[0]].append((key, o.get("kv_usage")))
    return out


def join(messages: list, recs: list) -> list | None:
    """(kv_usage, waiting) for each [server] block, by a forward scan of the session's answers (a compaction may have
    dropped lines); None when a line has no answer."""
    out, p = [], 0
    for i, k in server_blocks(messages):
        m = SERVER_LINE.match(messages[i]["content"][k]["text"])
        if not m:
            return None
        key = tuple(int(g.replace(",", "")) for g in m.groups()[1:])
        while p < len(recs) and recs[p][0] != key:
            p += 1
        if p == len(recs) or recs[p][1] is None:
            return None
        out.append((float(recs[p][1]), key[3]))
        p += 1
    return out


def stratum(row: dict) -> str:
    return "final" if row["final"] else ("start" if row["j"] == 0 else "work")


def _one(meta: str):
    return rr.t19_one(meta)


def make_item(sess: dict, row: dict, recs: list) -> dict | None:
    req = rr.request(row["ptr"])
    msgs = req.get("messages") or []
    blocks = server_blocks(msgs)
    if (not blocks or blocks[-1][0] != len(msgs) - 1 or blocks[-1][1] != len(msgs[-1]["content"]) - 1
            or not isinstance(req.get("system"), str) or req["system"].count(HR.SERVER_SECTIONS["wait"]) != 1):
        return None
    lines = join(msgs, recs)
    if lines is None:
        return None
    pct = [round(100 * kv) for kv, _ in lines]
    return {"sid": sess["sid"], "cell": sess["group"], "label": sess["label"], "turn": row["turn"], "j": row["j"],
            "call": row["call"], "stratum": stratum(row), "np": row["np"], "ptr": row["ptr"],
            "template": sess["template"], "calls": [{"name": c["name"], "input": c["input"]} for c in row["calls"]],
            "lines": [[kv, w] for kv, w in lines], "kv_real": lines[-1][0],
            "prior_high": sum(1 for p in pct[:-1] if p >= round(100 * WAIT_KV))}


def build_items(workers: int = 16) -> dict:
    metas = [str(p) for cell in CELLS for p in sorted((CELLS_ROOT / cell / "runs").glob("*/*.meta.json"))]
    with ProcessPoolExecutor(workers) as ex:
        loaded = list(ex.map(_one, metas, chunksize=4))
    ext = {cell: ext_lines(cell) for cell in CELLS}
    stats = Counter(sessions_read=len(loaded))
    sessions = []
    for sess, rows in loaded:
        if sess["filler"] or not rows:
            stats["filler_or_empty"] += 1
        elif any(c.get("name") == HR.WAIT_TOOL_NAME for r in rows for c in r["calls"]):
            stats["called_wait"] += 1
        else:
            sessions.append((sess, rows))
    sessions.sort(key=lambda x: x[0]["sid"])
    rng = random.Random(SEED)
    rng.shuffle(sessions)
    cut = len(sessions) // 3
    pools = {"screen": sessions[:cut], "confirm": sessions[cut:]}
    out = {"seed": SEED, "cells": list(CELLS), "S": S, "screen": [], "confirm": []}
    for phase, want in (("screen", SCREEN_N), ("confirm", CONFIRM_N)):
        cands = defaultdict(list)
        for sess, rows in pools[phase]:
            for row in rows:
                if row.get("stop") != "max_tokens":
                    cands[stratum(row)].append((sess, row))
        per_sess = Counter()
        for st, n in want.items():
            pool = list(cands[st])
            rng.shuffle(pool)
            got = 0
            for sess, row in pool:
                if got == n:
                    break
                if per_sess[sess["sid"]] >= CAP_PER_SESSION:
                    continue
                item = make_item(sess, row, ext[sess["group"]].get(sess["label"], []))
                if item is None:
                    stats[f"{phase}_{st}_unjoined"] += 1
                    continue
                item["id"] = f"{phase[0]}{len(out[phase]) + 1:03d}"
                out[phase].append(item)
                per_sess[sess["sid"]] += 1
                got += 1
            if got < n:
                raise SystemExit(f"{phase}/{st}: only {got} of {n} items")
        stats[f"{phase}_sessions"] = len(pools[phase])
    stats["sessions_used"] = len(sessions)
    out["stats"] = dict(stats)
    return out


def load_items() -> dict:
    return json.loads(ITEMS.read_text())


def chains(items: list[dict], plan: list[tuple[str, tuple]]) -> list[tuple]:
    """(sess, row, recorded keys, [(variant, request)]) per item and condition, items in a seeded order that mixes
    the strata (a stopped run leaves a balanced part done); one condition's levels share a chain, so they run one
    after another and reuse the prefix (only the newest line differs)."""
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
                    req = rewrite(base, cond, lines, level)
                except Unbuildable:
                    req = None
                variants.append((variant_name(cond, level), req))
            out.append((sess, row, rk, variants))
    return out


def screen_plan() -> list[tuple[str, tuple]]:
    return [("B0", (None,))] + [(c, SCREEN_LEVELS) for c in CONDS]


def confirm_plan(winner: str) -> list[tuple[str, tuple]]:
    return [("B0", (None,)), (winner, CONFIRM_LEVELS)]


# -- the GPU run ----------------------------------------------------------------------------------------------------
class KVRunner(cf.Runner):
    """rd_cf's sampler (a warm-up request, then n=S samples with a per-request seed; records and texts appended, so a
    stopped run resumes), driven by chains of requests."""

    async def run_chains(self, todo: list[tuple]) -> None:
        import aiohttp
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0))
        t0 = time.time()
        try:
            await asyncio.gather(*(self.chain(c) for c in todo))
        finally:
            await self.session.close()
            self.texts.close()
            self.records.close()
            self.pool.shutdown()
        print(f"[kvwait] {self.out.name} done in {time.time() - t0:.0f} s: {dict(self.stats)}", flush=True)

    async def chain(self, c: tuple) -> None:
        sess, row, rk, variants = c
        for name, req in variants:
            await self.variant(sess, row, name, req, rk, None, set())


def changed() -> list[str]:
    """Entries of the prereg's sha256 list that are missing or differ (repo-relative paths)."""
    if not PREREG_SHA.exists():
        return [f"{PREREG_SHA.name} (missing: freeze first)"]
    bad = []
    for line in PREREG_SHA.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        want, rel = line.split(maxsplit=1)
        path = REPO / rel.strip()
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != want:
            bad.append(rel.strip())
    return bad


def freeze() -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [f"# frozen {stamp}, before the probe's first request; paths are relative to the repo"]
    for path in FROZEN:
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(REPO)}")
    PREREG_SHA.write_text("\n".join(lines) + "\n")
    print(PREREG_SHA.read_text(), end="")


def server_of(url: str) -> dict | None:
    """The live topic-21 stage whose server listens on the URL's port."""
    port = urllib.parse.urlparse(url).port
    for path in sorted(T21_SERVERS.glob("*/server_info.json"), reverse=True):
        try:
            info = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if info.get("port") == port and "t_stop" not in info:
            return info
    return None


def guard(url: str) -> str | None:
    """Why this server must not get the probe's traffic (None: it is topic 21's s2a probe server, serving MiniMax)."""
    info = server_of(url)
    if info is None:
        return "no live topic-21 stage listens on this port"
    if not str(info.get("stage", "")).startswith("s2a") or info.get("config") != "profile":
        return f"stage {info.get('stage')!r} with config {info.get('config')!r} is not an s2a probe stage"
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=30) as resp:
            models = [m.get("id") for m in json.loads(resp.read()).get("data") or []]
    except Exception as exc:                          # noqa: BLE001
        return f"/v1/models failed: {exc}"
    return None if cf.MODEL in models else f"the server serves {models}, not {cf.MODEL}"


def run_phase(url: str, phase: str, todo: list[tuple]) -> None:
    n = sum(len(c[3]) for c in todo)
    print(f"[kvwait] {phase}: {n} sampling requests in {len(todo)} chains, S={S}, inflight {INFLIGHT}", flush=True)
    runner = KVRunner(url, KV / phase, S, cf.TAU_DEFAULT, INFLIGHT, RENDERERS)
    asyncio.run(runner.run_chains(todo))


def run(url: str, any_server: bool = False) -> int:
    bad = changed()
    if bad:
        print(f"[kvwait] refused: frozen files changed or missing: {bad}", flush=True)
        return 4
    if not any_server:
        why = guard(url)
        if why:
            print(f"[kvwait] refused: {why}", flush=True)
            return 6
    items = load_items()
    KV.mkdir(parents=True, exist_ok=True)
    with (KV / "runs.jsonl").open("a") as f:
        f.write(json.dumps({"t_start": time.time(), "url": url, "server": server_of(url), "S": S,
                            "inflight": INFLIGHT, "model": cf.MODEL, "sampling": cf.SAMPLING,
                            "max_tokens": cf.MAX_TOKENS}) + "\n")
    run_phase(url, "screen", chains(items["screen"], screen_plan()))
    res = analyze_phase("screen", items["screen"])
    choice = pick(res)
    (KV / "winner.json").write_text(json.dumps(choice, indent=1) + "\n")
    print(f"[kvwait] screen winner: {choice['winner']} ({choice['why']})", flush=True)
    if choice["winner"]:
        run_phase(url, "confirm", chains(items["confirm"], confirm_plan(choice["winner"])))
    write_tables()
    return 0


# -- analysis -------------------------------------------------------------------------------------------------------
def boot(units: list[tuple[str, float]], rng: random.Random) -> tuple:
    """Mean over units with a session-cluster bootstrap: (mean, lo, hi); as pw_probe.boot."""
    if not units:
        return (None, None, None)
    by = defaultdict(list)
    for sid, v in units:
        by[sid].append(v)
    keys = sorted(by)
    mean = sum(v for _, v in units) / len(units)
    draws = []
    for _ in range(DRAWS):
        vals = [v for k in (rng.choice(keys) for _ in keys) for v in by[k]]
        draws.append(sum(vals) / len(vals))
    draws.sort()
    return (mean, draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1])


def item_metrics(rec: dict | None) -> dict | None:
    ss = (rec or {}).get("samples") or []
    if not rec or rec.get("status") != "ok" or not ss:
        return None
    n = len(ss)
    return {"wait": sum("wait" in s["tools"] for s in ss) / n,
            "only": sum(bool(s["tools"]) and set(s["tools"]) == {"wait"} for s in ss) / n,
            "final": sum(s["n_calls"] == 0 and s["finish"] != "length" for s in ss) / n,
            "kept": sum(bool(s["same"]["target"]) for s in ss) / n,
            "out": (rec.get("out_tokens") or 0) / n}


def read_texts(phase: str) -> dict:
    """(sid, turn, j, variant, i) -> (requested seconds of the first wait or None, the thinking)."""
    out = {}
    path = KV / phase / "texts.jsonl"
    if not path.exists():
        return out
    for t in rr.read_jsonl(path):
        g = cf.parse_generation(t.get("text") or "")
        secs = [c["input"].get("seconds") for c in g["calls"] if c["name"] == HR.WAIT_TOOL_NAME]
        try:
            sec = float(secs[0]) if secs else None
        except (TypeError, ValueError):
            sec = None
        out[(t["sid"], t["turn"], t["j"], t["variant"], t["i"])] = (sec, g["think"].strip())
    return out


def median(xs: list):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def analyze_phase(phase: str, items: list[dict]) -> dict:
    recs = {}
    for r in rr.read_jsonl(KV / phase / "records.jsonl") if (KV / phase / "records.jsonl").exists() else []:
        recs[(r["sid"], r["turn"], r["j"], r["variant"])] = r
    texts = read_texts(phase)
    variants = sorted({k[3] for k in recs}, key=lambda v: (v != "B0", v))
    res = {"phase": phase, "items": len(items), "variants": {}}
    for v in variants:
        row = {"status": dict(Counter(recs[(it["sid"], it["turn"], it["j"], v)].get("status")
                                      for it in items if (it["sid"], it["turn"], it["j"], v) in recs))}
        for name, strata in (("work", {"work"}), ("start", {"start"}), ("final", {"final"}),
                             ("all", {"work", "start", "final"})):
            units = []
            for it in items:
                if it["stratum"] in strata:
                    m = item_metrics(recs.get((it["sid"], it["turn"], it["j"], v)))
                    if m:
                        units.append((it["sid"], m))
            rng = random.Random(f"kvwait|{phase}|{v}|{name}")
            out = {"n": len(units)}
            for key in ("wait", "kept"):
                mean, lo, hi = boot([(sid, m[key]) for sid, m in units], rng)
                out[key], out[f"{key}_ci"] = mean, [lo, hi]
            for key in ("only", "final", "out"):
                out[key] = sum(m[key] for _, m in units) / len(units) if units else None
            out["out_p50"] = median([m["out"] for _, m in units])
            row[name] = out
        secs = [s for (sid, turn, j, vv, _), (s, _) in texts.items() if vv == v and s is not None]
        row["seconds_p50"], row["seconds_n"] = median(secs), len(secs)
        if v != "B0":                                  # by how many earlier lines in the context were >= the rule
            by = defaultdict(list)
            for it in items:
                if it["stratum"] == "work":
                    m = item_metrics(recs.get((it["sid"], it["turn"], it["j"], v)))
                    if m:
                        p = it["prior_high"]
                        by["0" if p == 0 else "1-5" if p <= 5 else "6-20" if p <= 20 else ">20"].append(m["wait"])
            row["by_prior_high"] = {k: [len(xs), sum(xs) / len(xs)] for k, xs in sorted(by.items())}
        res["variants"][v] = row
    res["excerpts"] = excerpts(texts, recs, items)
    return res


def excerpts(texts: dict, recs: dict, items: list[dict], n: int = 3) -> dict:
    """A few thinking excerpts at the highest level: work samples that waited and that did not."""
    high = sorted({k[3] for k in recs if k[3] != "B0"}, key=lambda v: -float(v.split("@")[1]))
    out = {}
    for v in high[:2]:
        rng = random.Random(f"kvwait|excerpts|{v}")
        keys = [k for k in texts if k[3] == v]
        keys.sort()
        rng.shuffle(keys)
        work = {(it["sid"], it["turn"], it["j"]) for it in items if it["stratum"] == "work"}
        waited = [texts[k][1][:300] for k in keys if k[:3] in work and texts[k][0] is not None][:n]
        not_waited = [texts[k][1][:300] for k in keys if k[:3] in work and texts[k][0] is None][:n]
        out[v] = {"waited": waited, "did_not": not_waited}
    return out


def pick(res: dict) -> dict:
    """The frozen rule: the highest wait rate at the high level on work items, among candidates with at most
    RULE.low_max at the low level, at most RULE.wait_only_max wait-only samples (all items, both levels) and the
    recorded action kept within RULE.kept_drop_max of B0; within RULE.tie of the best, PREFER's order. None when the
    best is below RULE.min_high."""
    vs = res["variants"]
    lo_v, hi_v = (f"@{SCREEN_LEVELS[0]:.2f}", f"@{SCREEN_LEVELS[1]:.2f}")
    b0_kept = (vs.get("B0") or {}).get("work", {}).get("kept")
    rows = {}
    for c in CONDS:
        hi, lo = vs.get(c + hi_v), vs.get(c + lo_v)
        if not hi or not lo or hi["work"]["wait"] is None or lo["work"]["wait"] is None:
            rows[c] = {"eligible": False, "why": "no data"}
            continue
        only = max(hi["all"]["only"] or 0.0, lo["all"]["only"] or 0.0)
        kept_ok = b0_kept is None or hi["work"]["kept"] >= b0_kept - RULE["kept_drop_max"]
        ok = lo["work"]["wait"] <= RULE["low_max"] and only <= RULE["wait_only_max"] and kept_ok
        rows[c] = {"eligible": ok, "high": hi["work"]["wait"], "low": lo["work"]["wait"], "wait_only": only,
                   "kept_high": hi["work"]["kept"], "kept_b0": b0_kept}
    good = {c: r for c, r in rows.items() if r["eligible"]}
    if not good:
        return {"winner": None, "why": "no candidate meets the rule", "rows": rows, "rule": RULE}
    best = max(r["high"] for r in good.values())
    winner = next(c for c in PREFER if c in good and good[c]["high"] >= best - RULE["tie"])
    if good[winner]["high"] < RULE["min_high"]:
        return {"winner": None, "why": f"the best rate at {SCREEN_LEVELS[1]} is {best:.2f} < {RULE['min_high']}",
                "rows": rows, "rule": RULE}
    return {"winner": winner, "why": f"rate {good[winner]['high']:.2f} at {SCREEN_LEVELS[1]} (best {best:.2f})",
            "rows": rows, "rule": RULE}


def verdict(res: dict, winner: str) -> dict:
    """The confirm's pass criteria (PASS), each with its value."""
    vs = res["variants"]
    b0 = vs.get("B0") or {}
    at = {lv: vs.get(variant_name(winner, lv)) or {} for lv in CONFIRM_LEVELS}
    out = {}
    for lv in (0.85, 0.95):
        w = at[lv].get("work", {})
        out[f"wait at {lv:.2f} >= {PASS['high_min']}, low bound >= {PASS['high_lo_min']}"] = (
            [w.get("wait"), (w.get("wait_ci") or [None])[0]],
            w.get("wait") is not None and w["wait"] >= PASS["high_min"] and w["wait_ci"][0] >= PASS["high_lo_min"])
    low = at[0.60].get("work", {}).get("wait")
    out[f"wait at 0.60 <= {PASS['low_max']}"] = (low, low is not None and low <= PASS["low_max"])
    only = max((at[lv].get("all", {}).get("only") or 0.0) for lv in CONFIRM_LEVELS)
    out[f"wait-only <= {PASS['wait_only_max']}"] = (only, only <= PASS["wait_only_max"])
    fin = max((at[lv].get("final", {}).get("wait") or 0.0) for lv in (0.85, 0.95))
    out[f"waits on final-answer items <= {PASS['final_max']}"] = (fin, fin <= PASS["final_max"])
    kb = b0.get("work", {}).get("kept")
    kmin = min((at[lv].get("work", {}).get("kept") or 0.0) for lv in (0.85, 0.95))
    out[f"recorded action kept within {PASS['kept_drop_max']} of B0"] = (
        [kmin, kb], kb is not None and kmin >= kb - PASS["kept_drop_max"])
    ob = b0.get("all", {}).get("out_p50")
    omax = max((at[lv].get("all", {}).get("out_p50") or 0.0) for lv in CONFIRM_LEVELS)
    out[f"output tokens p50 at most +{PASS['out_rise_max']:.0%}"] = (
        [omax, ob], bool(ob) and omax <= ob * (1 + PASS["out_rise_max"]))
    return {"criteria": {k: {"value": v, "pass": ok} for k, (v, ok) in out.items()},
            "pass": all(ok for _, ok in out.values())}


def write_tables() -> dict:
    items = load_items()
    res = {"screen": analyze_phase("screen", items["screen"])}
    choice = json.loads((KV / "winner.json").read_text()) if (KV / "winner.json").exists() else None
    res["winner"] = choice
    if choice and choice.get("winner") and (KV / "confirm" / "records.jsonl").exists():
        res["confirm"] = analyze_phase("confirm", items["confirm"])
        res["confirm"]["verdict"] = verdict(res["confirm"], choice["winner"])
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / "kvwait.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    (TABLES / "kvwait.md").write_text(render_md(res))
    return res


def _p(x, digits: int = 0) -> str:
    return "—" if x is None else f"{100 * x:.{digits}f} %"


def _n(x) -> str:
    return "—" if x is None else f"{x:.0f}"


def render_md(res: dict) -> str:
    out = ["# KV-threshold wait prompt: MiniMax-M2.7 probe (ef_kvwait.py)", "",
           "Wait rate = share of samples whose reply calls `wait` (mean over items; 95 % session-cluster bootstrap). "
           "work = replies with tool calls after the turn's first call; kept = the recorded action still issued.", ""]
    for phase in ("screen", "confirm"):
        r = res.get(phase)
        if not r:
            continue
        out += [f"## {phase} ({r['items']} items)", "",
                "| condition | work: wait [95 %] | start: wait | final: wait | wait-only | kept (work) | "
                "out tokens p50 | seconds p50 (n) |", "|---|---|---|---|---|---|---|---|"]
        for v, row in r["variants"].items():
            w = row["work"]
            ci = w.get("wait_ci") or [None, None]
            out.append(f"| `{v}` | {_p(w['wait'])} [{_p(ci[0])}, {_p(ci[1])}] (n {w['n']}) | "
                       f"{_p(row['start']['wait'])} | {_p(row['final']['wait'])} | {_p(row['all']['only'], 1)} | "
                       f"{_p(w['kept'])} | {_n(row['all']['out_p50'])} | {_n(row['seconds_p50'])} "
                       f"({row['seconds_n']}) |")
        out.append("")
        if phase == "screen" and res.get("winner"):
            out += [f"**Winner:** `{res['winner']['winner']}`: {res['winner']['why']}.", ""]
        if phase == "confirm" and r.get("verdict"):
            out += [f"**Verdict: {'PASS' if r['verdict']['pass'] else 'FAIL'}**", ""]
            out += [f"- {k}: {v['value']} → {'pass' if v['pass'] else 'fail'}" for k, v in r["verdict"]["criteria"].items()]
            out.append("")
        prior = {v: row.get("by_prior_high") for v, row in r["variants"].items() if row.get("by_prior_high")}
        if prior:
            out += ["Wait rate on work items by the number of earlier lines at or above the rule (items, rate):", ""]
            out += [f"- `{v}`: " + "; ".join(f"{k}: {n}, {_p(x)}" for k, (n, x) in p.items()) for v, p in prior.items()]
            out.append("")
    return "\n".join(out) + "\n"


# -- CPU checks -----------------------------------------------------------------------------------------------------
def strip_variable(req: dict) -> str:
    """The request without the three places a condition may change."""
    r = copy.deepcopy(req)
    configure("R")                                     # the filled-in wait_kv paragraph
    r["system"] = r["system"].replace(HR.server_section(), "<P>").replace(HR.SERVER_SECTIONS["wait"], "<P>")
    for t in r.get("tools") or []:
        if t.get("name") == HR.WAIT_TOOL_NAME:
            t["description"] = "<D>"
    for i, k in server_blocks(r["messages"]):
        r["messages"][i]["content"][k]["text"] = "<L>"
    return json.dumps(r, sort_keys=True)


def check(workers: int, n_append: int = 20) -> int:
    items = load_items()
    every = items["screen"] + items["confirm"]
    problems = Counter()
    pairs = []
    for it in every:
        base = rr.request(it["ptr"])
        lines = [tuple(x) for x in it["lines"]]
        for cond in CONDS:
            for level in SCREEN_LEVELS + CONFIRM_LEVELS:
                req = rewrite(base, cond, lines, level)
                if strip_variable(req) != strip_variable(base):
                    problems[f"{cond}: differs outside the three places"] += 1
                newest = req["messages"][-1]["content"][-1]["text"]
                cued = "At 80% or more" in newest
                if cued != (CONDS[cond][2] == "kv_cue" and round(100 * level) >= 80):
                    problems[f"{cond}@{level}: cue"] += 1
        pairs.append((base, it["template"], it["np"]))
    print(f"[check] {len(every)} items x {len(CONDS)} conditions x {len(SCREEN_LEVELS + CONFIRM_LEVELS)} levels "
          f"rewritten; problems: {dict(problems) or 'none'}", flush=True)
    with ProcessPoolExecutor(workers, initializer=cf._init_worker) as ex:
        lens = list(ex.map(cf.render_ids, [(req, tmpl) for req, tmpl, _ in pairs], chunksize=2))
    off = [(it["id"], n, len(ids)) for it, (_, _, n), ids in zip(every, pairs, lens) if len(ids) != n]
    print(f"[check] B0 renders to the recorded prompt tokens: {len(every) - len(off)}/{len(every)}"
          + (f"; off: {off[:5]}" if off else ""), flush=True)
    # append-only: a session's previous call under a condition is a prefix of this call under it
    rng = random.Random(SEED)
    cand = [it for it in every if it["stratum"] == "work" and it["j"] >= 1]
    rng.shuffle(cand)
    jobs, skipped = [], 0
    for it in cand:
        if len(jobs) == 3 * n_append:
            break
        sess, rows = rr.t19_one(str(REPO / Path(it["ptr"]["file"]).parent / f"{it['label']}.meta.json"))
        prev = next((r for r in rows if r["turn"] == it["turn"] and r["j"] == it["j"] - 1), None)
        if prev is None or not prev.get("append_ok"):
            skipped += 1                               # the recording itself rewrote the history in between
            continue
        a, b = rr.request(prev["ptr"]), rr.request(it["ptr"])
        lines = [tuple(x) for x in it["lines"]]
        for cond in ("B0", "R", "RC"):
            pa = rewrite(a, cond, lines[:-1], lines[-2][0]) if cond != "B0" else a
            pb = rewrite(b, cond, lines, 0.93) if cond != "B0" else b
            jobs.append((it["id"], cond, pa, pb, it["template"]))
    with ProcessPoolExecutor(workers, initializer=cf._init_worker) as ex:
        ta = list(ex.map(cf.render_text, [(pa, t, True) for _, _, pa, _, t in jobs], chunksize=2))
        tb = list(ex.map(cf.render_text, [(pb, t, True) for _, _, _, pb, t in jobs], chunksize=2))
    bad = [(i, c) for (i, c, *_), x, y in zip(jobs, ta, tb) if not y.startswith(x)]
    print(f"[check] append-only under B0/R/RC: {len(jobs) - len(bad)}/{len(jobs)} ({skipped} pairs skipped: not "
          "append-only as recorded)" + (f"; not: {bad[:6]}" if bad else ""), flush=True)
    n_bad = sum(problems.values()) + len(off) + len(bad)
    print(f"[check] {'OK' if not n_bad else 'PROBLEMS'}: {dict(problems)}", flush=True)
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
    r.add_argument("--any-server", action="store_true", help="tests only: skip the s2a server check")
    sub.add_parser("analyze")
    a = ap.parse_args()
    if a.cmd == "items":
        if ITEMS.exists():
            raise SystemExit(f"{ITEMS} exists; the items are drawn once")
        KV.mkdir(parents=True, exist_ok=True)
        data = build_items()
        ITEMS.write_text(json.dumps(data, indent=1) + "\n")
        print(f"[items] screen {len(data['screen'])}, confirm {len(data['confirm'])}; {data['stats']}")
    elif a.cmd == "check":
        sys.exit(check(a.workers))
    elif a.cmd == "freeze":
        freeze()
    elif a.cmd == "run":
        if not a.url:
            raise SystemExit("give --url (or RD_URL)")
        sys.exit(run(a.url, a.any_server))
    else:
        res = write_tables()
        print((TABLES / "kvwait.md").read_text())
        if res.get("winner") is None:
            print("(no winner yet)")


if __name__ == "__main__":
    main()
