#!/usr/bin/env python3
"""Load tests of research/20_round_dependency (S5): the sampled sessions replayed closed-loop on a crowded
MiniMax-M2.7 server, as recorded and with the independent rounds the counterfactual replay found merged or reordered.

Variants (one cell each):
  rec          every recorded lead call in order; each waits for the previous call and its tool step (the recorded
               gap after it)
  merge        each chain group k..m (rd_cf's chain) is one call: L_k's request with every earlier group folded the
               same way (a_k with the group's later tool calls appended, their thinking dropped, and all the group's
               results in one message). Its output is a_k's plus the folded calls' tool-call tokens plus BETA tokens
               of reasoning per folded call (fitted on recorded multi-call responses). Then the group's tool steps.
  reorder_low  each chain group keeps its calls, but call i+1 of a group is sent as soon as call i returns, while
               call i's tool step runs. The group's earlier results appear as s15's background placeholder, and when
               the group closes they arrive as <task_notification> blocks (full output) beside the last round's
               results. Tool steps of a group run concurrently when their effects commute (rd_static), else in
               order. The next group's first call waits for every tool step of the group. Priorities (vLLM
               --scheduling-policy priority, lower is served first): a call that blocks its session 0, an early
               call 1, so a busy server delays early calls while the tools run.
  reorder_cpu  as reorder_low, but an early call whose own round runs >= HEAVY_S of tools gets -1: the call that
               starts CPU-heavy work goes first (the tool time comes from the recording: an oracle predictor).
Every call is /v1/completions with the rendered prompt ids (ef_render, the source's template), max_tokens = its
output length and ignore_eos, tagged X-Request-Id "<copy>~r<variant>~<call>~ld~root" for the server's MU scheduler
log. Generated tokens differ from the recorded reply, so no variant reuses a reply's KV in the next call (live runs
did); the bias is the same in every variant.

Closed loop: N slots; each slot replays copies of the units in its own seeded order (the same order in every
variant), each copy with a fresh run nonce in its first user message so copies share no KV beyond the system
prompt and tools; U(5, 30) s between sessions; the first --warmup-s seconds are not counted, then a --window-s
window. Primary metric: recorded rounds completed per hour inside the window (a round is complete when its call
has returned and its tool step has ended; every variant does the same recorded work).

    python research/20_round_dependency/rd_loadreplay.py prepare --run main --units t19 --out load19
    python research/20_round_dependency/rd_loadreplay.py run --url URL --plan load19 --variant merge --n 16 \\
        --out m16_merge --warmup-s 900 --window-s 3600
    python research/20_round_dependency/rd_loadreplay.py chain m16_rec:load19:rec:16 m16_merge:load19:merge:16
    python research/20_round_dependency/rd_loadreplay.py show --cells m16_rec,m16_merge --ref m16_rec
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402
import rd_static as rs  # noqa: E402

LOAD = rr.DATA / "load"
GAP_CAP_S = 600.0                      # a recorded gap inside a turn (tools + harness); turn gaps are think pauses
THINK_CAP_S = 240.0                    # topic 11's think-time cap
HEAVY_S = rs.HEAVY_S
VARIANTS = ("rec", "merge", "reorder_low", "reorder_cpu")
NONCE_RE = re.compile(r"^\[run [0-9a-f]{8}\]")


# -- message surgery ------------------------------------------------------------------------------------------------
def notification(bg: str, call: dict, output: str) -> str:
    """s15's background-task notification (code.py collect_background_results), with the full output."""
    inp = call.get("input") or {}
    command = inp.get("command") if call.get("name") == "bash" else f"{call.get('name')} {json.dumps(inp, ensure_ascii=False)}"
    return (f"<task_notification>\n  <task_id>{bg}</task_id>\n  <status>completed</status>\n"
            f"  <command>{command}</command>\n  <summary>{output}</summary>\n</task_notification>")


def locate(messages: list[dict], round_row: dict) -> tuple[int | None, int | None]:
    """(index of the assistant message carrying the round's tool calls, index of the user message with its results)."""
    ids = {c["id"] for c in round_row["calls"]}
    a = r = None
    for i, m in enumerate(messages):
        if isinstance(m.get("content"), str):
            continue
        blocks = [b for b in m["content"] if isinstance(b, dict)]
        if m.get("role") == "assistant" and ids & {b.get("id") for b in blocks if b.get("type") == "tool_use"}:
            a = i
        elif m.get("role") == "user" and ids & {b.get("tool_use_id") for b in blocks if b.get("type") == "tool_result"}:
            r = i
    return a, r


def fold_group(messages: list[dict], group: list[dict], form: str, close: bool) -> list[dict]:
    """Rewrite one group's span of a recorded message list.
    form 'merge': [a_k + later tool_use blocks, one message with every result of the group, the other messages];
    form 'reorder': each round keeps its assistant message; its results become background placeholders, except that
    a closed group's last round keeps its real results and gets the others' as <task_notification> blocks."""
    spans = [locate(messages, r) for r in group]
    if any(a is None for a, _ in spans):
        return messages                              # the group was compacted away: leave the history as recorded
    first_a = spans[0][0]
    last = max(x for span in spans for x in span if x is not None)
    span_msgs = messages[first_a:last + 1]
    a_idx = {spans[i][0] - first_a for i in range(len(group))}
    r_idx = {spans[i][1] - first_a for i in range(len(group)) if spans[i][1] is not None}
    others = [copy.deepcopy(m) for i, m in enumerate(span_msgs) if i not in a_idx and i not in r_idx]
    results = {}                                       # tool_use id -> the recorded result block
    extra_blocks = []                                  # non-result blocks of the results messages (notifications)
    for i in sorted(r_idx):
        for b in span_msgs[i]["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                results[b["tool_use_id"]] = copy.deepcopy(b)
            else:
                extra_blocks.append(copy.deepcopy(b))
    calls = [(r, c) for r in group for c in r["calls"]]
    bg = {c["id"]: f"bg_{n + 1:04d}" for n, (_, c) in enumerate(calls)}
    if form == "merge":
        a = copy.deepcopy(span_msgs[spans[0][0] - first_a])
        a["content"] = list(a["content"]) + [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]}
                                             for r in group[1:] for c in r["calls"]]
        merged = [results.get(c["id"]) or {"type": "tool_result", "tool_use_id": c["id"], "content": ""}
                  for _, c in calls]
        new = [a, {"role": "user", "content": merged + extra_blocks}] + others
    else:
        new = []
        for gi, (row, (ai, ri)) in enumerate(zip(group, spans)):
            new.append(copy.deepcopy(span_msgs[ai - first_a]))
            last_round = gi == len(group) - 1
            content = []
            for c in row["calls"]:
                if close and last_round:
                    content.append(results.get(c["id"]) or {"type": "tool_result", "tool_use_id": c["id"], "content": ""})
                else:
                    content.append({"type": "tool_result", "tool_use_id": c["id"],
                                    "content": cf.PLACEHOLDER.format(bg=bg[c["id"]])})
            if close and last_round:
                for r2, c2 in calls:
                    if r2 is not row:
                        out = rr.block_text((results.get(c2["id"]) or {}).get("content"))
                        content.append({"type": "text", "text": notification(bg[c2["id"]], c2, out)})
                content += extra_blocks
            new.append({"role": "user", "content": content})
        new += others
    return messages[:first_a] + new + messages[last + 1:]


def world_request(base: dict, groups_before: list[list[dict]], open_group: list[dict] | None, form: str) -> dict:
    """The request of a call in the merged or reordered world: the recorded request with every earlier group of the
    session folded (closed), and for reorder the group the call belongs to folded as still open."""
    req = copy.deepcopy(base)
    msgs = req["messages"]
    for g in groups_before:
        if len(g) > 1 or form == "reorder":
            msgs = fold_group(msgs, g, form, close=True)
    if open_group and form == "reorder":
        msgs = fold_group(msgs, open_group, form, close=False)
    req["messages"] = msgs
    return req


def with_nonce(req: dict, nonce: str) -> dict:
    """A copy's request: its first user message carries this copy's run nonce."""
    req = copy.deepcopy(req)
    for m in req["messages"]:
        if m.get("role") == "user":
            if isinstance(m["content"], str):
                m["content"] = (NONCE_RE.sub(f"[run {nonce}]", m["content"]) if NONCE_RE.match(m["content"])
                                else f"[run {nonce}] {m['content']}")
            break
    return req


# -- plans ------------------------------------------------------------------------------------------------------------
def chain_groups(run: str) -> dict[tuple[str, str], list[list[int]]]:
    """(sid, turn) -> the chain groups as lists of round indices j, from rd_cf's chain records."""
    out = {}
    for r in rr.read_jsonl(cf.CF / run / "records.jsonl"):
        if r["variant"] != "chain":
            continue
        groups = []
        for c in sorted(r["chain"], key=lambda c: c["j"]):
            if c["start"] or not groups:
                groups.append([c["j"]])
            else:
                groups[-1].append(c["j"])
        out[(r["sid"], r["turn"])] = groups
    return out


def tool_tokens(calls: list[dict]) -> int:
    import ef_render
    text = "".join(f'<invoke name="{c["name"]}">' + "".join(
        f'<parameter name="{k}">{v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}</parameter>\n'
        for k, v in (c.get("input") or {}).items()) + "</invoke>\n" for c in calls)
    return len(ef_render.tokens(text)) if text else 0


def fit_beta(rows: list[dict]) -> float:
    """Reasoning tokens per extra tool call: least squares of (output - tool-call tokens) on (calls - 1) over
    recorded tool rounds with 1-6 calls."""
    xs, ys = [], []
    for r in rows:
        n = len(r["calls"])
        if 1 <= n <= 6:
            xs.append(n - 1)
            ys.append(r["out"] - tool_tokens(r["calls"]))
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx) if sxx else 0.0


def prepare(run: str, units_src: str, out: str, benches: set[str] | None) -> None:
    sample = json.loads((cf.CF / run / "run.json").read_text())["sample"]
    _, sessions, rows = cf.load_sample(sample)
    groups = chain_groups(run)
    keep = [sid for sid in rows if sessions[sid]["src"] == units_src
            and (benches is None or sessions[sid].get("bench") in benches)]
    all_rows = [r for sid in keep for r in rows[sid] if not r["final"]]
    beta = fit_beta(all_rows)
    plan = {"run": run, "src": units_src, "benches": sorted(benches) if benches else None, "beta": beta,
            "units": []}
    for sid in keep:
        sess = sessions[sid]
        cwd = cf.session_cwd(sess)
        rs_ = sorted(rows[sid], key=lambda r: r["call"])
        turns = defaultdict(list)
        for r in rs_:
            turns[r["turn"]].append(r)
        complete = all((sid, t) in groups for t in turns)
        if not complete:
            print(f"[prepare] {sid}: missing chain records, left out", flush=True)
            continue
        unit = {"sid": sid, "template": sess["template"], "turns": []}
        prev_t1 = None
        for t in sorted(turns):
            trs = sorted(turns[t], key=lambda r: r["j"])
            think = 0.0
            if prev_t1 is not None and trs[0]["t0"] is not None:
                think = min(THINK_CAP_S, max(0.0, trs[0]["t0"] - prev_t1))
            rounds = []
            for i, r in enumerate(trs):
                nxt = trs[i + 1] if i + 1 < len(trs) else None
                gap = 0.0
                if nxt is not None and nxt["t0"] is not None and r["t1"] is not None:
                    gap = min(GAP_CAP_S, max(0.0, nxt["t0"] - r["t1"]))
                rd, wr, _ = (set(), set(), False)
                for c, res in zip(r["calls"], r["results"]):
                    a, b, _u = rs.effects(c, res, cwd)
                    rd |= a
                    wr |= b
                rounds.append({"j": r["j"], "call": r["call"], "ptr": r["ptr"], "out": r["out"], "np": r["np"],
                               "gap": gap, "tool_s": sum(x for x in r["tool_ms"] if x) / 1000.0,
                               "calls": r["calls"], "reads": sorted(rd), "writes": sorted(wr),
                               "tool_tokens": tool_tokens(r["calls"]), "final": r["final"]})
            unit["turns"].append({"turn": t, "think": think, "groups": groups[(sid, t)], "rounds": rounds})
            prev_t1 = trs[-1]["t1"]
        plan["units"].append(unit)
    LOAD.mkdir(parents=True, exist_ok=True)
    path = LOAD / f"plan_{out}.json"
    path.write_text(json.dumps(plan) + "\n")
    n_rounds = sum(len(t["rounds"]) for u in plan["units"] for t in u["turns"])
    n_groups = sum(len(t["groups"]) for u in plan["units"] for t in u["turns"])
    print(f"[prepare] {out}: {len(plan['units'])} units, {n_rounds} rounds in {n_groups} chain groups, "
          f"BETA {beta:.1f} tokens per folded call -> {path}", flush=True)


# -- rendering in worker processes ------------------------------------------------------------------------------------
def _render(args) -> list[int]:
    req, template = args
    return cf.render_ids((req, template))


def build_calls(unit: dict, variant: str, beta: float) -> list[dict]:
    """The unit's calls in the variant's world: request (without the nonce), max_tokens, the rounds it completes,
    its priority class and its tool steps. Calls are listed in issue order; 'early' calls may be sent before the
    previous call's tool step ends."""
    out = []
    groups_done: list[list[dict]] = []
    for ti, turn in enumerate(unit["turns"]):
        by_j = {r["j"]: r for r in turn["rounds"]}
        for gi, g in enumerate(turn["groups"]):
            grp = [by_j[j] for j in g]
            if variant == "rec":
                for r in grp:
                    out.append({"turn": ti, "group": gi, "rounds": [r["j"]], "row": r, "prompt_of": r,
                                "groups_before": None, "open": None, "max_tokens": max(1, r["out"]),
                                "early": False, "tools": [r]})
            elif variant == "merge":
                k = grp[0]
                extra = sum(r["tool_tokens"] + beta for r in grp[1:])
                out.append({"turn": ti, "group": gi, "rounds": [r["j"] for r in grp], "row": k, "prompt_of": k,
                            "groups_before": list(groups_done), "open": None,
                            "max_tokens": max(1, int(round(k["out"] + extra))), "early": False, "tools": grp})
            else:
                for i, r in enumerate(grp):
                    out.append({"turn": ti, "group": gi, "rounds": [r["j"]], "row": r, "prompt_of": r,
                                "groups_before": list(groups_done), "open": grp[:i] if i else None,
                                "max_tokens": max(1, r["out"]), "early": i > 0, "tools": [r]})
            groups_done.append(grp)
    return out


def request_for(call: dict, variant: str) -> dict:
    base = cf.base_request(call["prompt_of"])
    if variant == "rec":
        return base
    form = "merge" if variant == "merge" else "reorder"
    return world_request(base, call["groups_before"] or [], call["open"], form)


def priority(call: dict, variant: str) -> int:
    if not variant.startswith("reorder") or not call["early"]:
        return 0
    if variant == "reorder_cpu" and call["row"]["tool_s"] >= HEAVY_S:
        return -1
    return 1


def check(plan_name: str, workers: int) -> int:
    """Part 2's CPU validity check: every rec call of the plan, rendered with a fresh nonce, has the recorded prompt
    length up to the nonce (the same for every call of a unit). The merge and reorder prompts render too; merge
    prompts are no longer than the recorded ones."""
    plan = json.loads((LOAD / f"plan_{plan_name}.json").read_text())
    jobs, meta = [], []
    for unit in plan["units"]:
        for variant in ("rec", "merge", "reorder_low"):
            for call in build_calls(unit, variant, plan["beta"]):
                jobs.append((with_nonce(request_for(call, variant), "0f1e2d3c"), unit["template"]))
                meta.append((unit["sid"], variant, call["prompt_of"]["np"]))
    with ProcessPoolExecutor(workers, initializer=cf._init_worker) as pool:
        lens = list(pool.map(_render, jobs, chunksize=4))
    offsets = defaultdict(set)
    longer = 0
    for (sid, variant, np_), ids in zip(meta, lens):
        if variant == "rec":
            offsets[sid].add(len(ids) - np_)
        elif variant == "merge":
            longer += len(ids) > np_ + max(offsets[sid] or {0})
    bad = [sid for sid, o in offsets.items() if len(o) != 1]
    print(f"[check] {plan_name}: {len(jobs)} prompts rendered; rec offsets per unit "
          f"{sorted({next(iter(o)) for o in offsets.values() if len(o) == 1})}; units with varying offsets: "
          f"{len(bad)}; merge prompts longer than recorded: {longer}", flush=True)
    return 1 if bad or longer else 0


# -- the closed loop --------------------------------------------------------------------------------------------------
class Cell:
    def __init__(self, a, plan: dict):
        self.a, self.plan = a, plan
        self.variant = a.variant
        self.out = LOAD / "cells" / a.out
        self.out.mkdir(parents=True, exist_ok=True)
        self.calls_log = (self.out / "calls.jsonl").open("a", encoding="utf-8")
        self.rounds_log = (self.out / "rounds.jsonl").open("a", encoding="utf-8")
        self.pool = ProcessPoolExecutor(a.renderers, initializer=cf._init_worker)
        self.copies = 0
        self.t0 = None
        self.stop = asyncio.Event()

    def log(self, handle, rec: dict) -> None:
        handle.write(json.dumps(rec) + "\n")
        handle.flush()

    async def send(self, client, label: str, call: dict, req: dict, n: int, template: str) -> dict:
        ids = await asyncio.get_running_loop().run_in_executor(self.pool, _render, (req, template))
        rid = f"{label}~r{self.variant}~{n:05d}~ld~root"
        body = {"model": cf.MODEL, "prompt": ids, "max_tokens": call["max_tokens"], "ignore_eos": True,
                "temperature": 1.0}
        pr = priority(call, self.variant)
        if self.a.priority_policy:
            body["priority"] = pr
        t_send = time.time()
        status, usage = "ok", {}
        try:
            resp = await client.post("/v1/completions", json=body,
                                     headers={"X-Request-Id": rid, "X-Session-ID": label})
            if resp.status_code != 200:
                status = f"http {resp.status_code}: {resp.text[:300]}"
            else:
                usage = resp.json().get("usage") or {}
        except Exception as exc:
            status = f"{type(exc).__name__}: {exc}"[:300]
        t_recv = time.time()
        rec = {"label": label, "rid": rid, "variant": self.variant, "turn": call["turn"], "group": call["group"],
               "rounds": call["rounds"], "early": call["early"], "priority": pr, "t_send": t_send,
               "t_recv": t_recv, "status": status, "n_prompt": usage.get("prompt_tokens"),
               "n_out": usage.get("completion_tokens"), "planned_out": call["max_tokens"],
               "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")}
        self.log(self.calls_log, rec)
        return rec

    async def session(self, client, unit: dict, label: str, nonce: str) -> None:
        calls = build_calls(unit, self.variant, self.plan["beta"])
        n = 0
        turn_prev = None
        running: dict[int, asyncio.Task] = {}          # round j -> its tool step (current group only)
        effects: dict[int, tuple[set, set]] = {}
        for call in calls:
            if self.stop.is_set():
                return
            if call["turn"] != turn_prev:
                if running:
                    await asyncio.gather(*running.values())
                    running.clear()
                if turn_prev is not None:
                    await asyncio.sleep(self.unit_turn(unit, call["turn"])["think"] * self.a.time_scale)
                turn_prev = call["turn"]
            if not call["early"] and running:          # a group's first call waits for every tool step before it
                await asyncio.gather(*running.values())
                running.clear()
                effects.clear()
            req = with_nonce(request_for(call, self.variant), nonce)
            n += 1
            rec = await self.send(client, label, call, req, n, unit["template"])
            if rec["status"] != "ok":
                return
            # the call's tool step(s): merge runs the group's steps in order; reorder runs one step per call, beside
            # the group's earlier steps when their effects commute
            async def step(rows=call["tools"], wait_for=None, rec=rec, turn=call["turn"]):
                # bound at creation: reorder runs this as a task after the loop has moved on to later calls
                if wait_for:
                    await asyncio.gather(*wait_for)
                for r in rows:
                    await asyncio.sleep(r["gap"] * self.a.time_scale)
                t_done = time.time()
                for r in rows:
                    self.log(self.rounds_log, {"label": label, "variant": self.variant, "turn": turn,
                                               "j": r["j"], "t_done": t_done, "call_rid": rec["rid"],
                                               "tool_s": r["tool_s"], "final": r["final"]})
            if self.variant.startswith("reorder"):
                r = call["row"]
                mine = (set(r["reads"]), set(r["writes"]))
                deps = [t for j, t in running.items() if rs.conflict(*effects[j], *mine)]
                running[r["j"]] = asyncio.create_task(step(wait_for=deps))
                effects[r["j"]] = mine
            else:
                await step()
        if running:
            await asyncio.gather(*running.values())

    def unit_turn(self, unit: dict, ti: int) -> dict:
        return unit["turns"][ti]

    async def slot(self, client, slot: int) -> None:
        rng = random.Random(f"{self.a.seed}|{slot}")
        units = list(self.plan["units"])
        await asyncio.sleep(rng.uniform(0, self.a.stagger_s) * self.a.time_scale)
        while not self.stop.is_set():
            rng.shuffle(units)
            for unit in units:
                if self.stop.is_set():
                    return
                nonce = f"{rng.getrandbits(32):08x}"
                self.copies += 1
                label = f"LR-{self.a.out}-s{slot:02d}-c{self.copies:04d}"
                await self.session(client, unit, label, nonce)
                await asyncio.sleep(rng.uniform(5, 30) * self.a.time_scale)

    async def main(self) -> None:
        import httpx
        self.t0 = time.time()
        (self.out / "cell.json").write_text(json.dumps({**vars(self.a), "t0": self.t0, "beta": self.plan["beta"],
                                                        "units": len(self.plan["units"])}, indent=1) + "\n")
        limits = httpx.Limits(max_connections=8 * self.a.n + 8, max_keepalive_connections=8 * self.a.n + 8)
        async with httpx.AsyncClient(base_url=self.a.url, timeout=7200, limits=limits) as client:
            tasks = [asyncio.create_task(self.slot(client, i)) for i in range(self.a.n)]
            await asyncio.sleep(self.a.warmup_s + self.a.window_s)
            self.stop.set()
            (self.out / "WINDOW_END").write_text(f"{time.time()}\n")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pool.shutdown(cancel_futures=True)
        self.calls_log.close()
        self.rounds_log.close()


BLOCK_S, FLOOR, DRAWS = 600.0, 0.10, 4000


def server_dir(cell: str) -> Path | None:
    """The rd_serve stage folder of a cell (stage name = cell name)."""
    dirs = sorted((rr.DATA / "servers").glob(f"{cell}_*"))
    return dirs[-1] if dirs else None


def cell_summary(name: str) -> dict:
    """Window metrics of one load cell: client side from its logs, server side from the MU scheduler log."""
    import mu_analyze as ma
    d = LOAD / "cells" / name
    meta = json.loads((d / "cell.json").read_text())
    w0 = meta["t0"] + meta["warmup_s"]
    w1 = w0 + meta["window_s"]
    rounds = [r for r in rr.read_jsonl(d / "rounds.jsonl") if w0 <= r["t_done"] < w1]
    calls = [c for c in rr.read_jsonl(d / "calls.jsonl") if w0 <= c["t_recv"] < w1]
    nb = int(meta["window_s"] // BLOCK_S)
    blocks = [0] * nb
    for r in rounds:
        b = int((r["t_done"] - w0) // BLOCK_S)
        if 0 <= b < nb:
            blocks[b] += 1
    lat = sorted(c["t_recv"] - c["t_send"] for c in calls if c["status"] == "ok")
    q = lambda v, x: v[min(len(v) - 1, int(x * len(v)))] if v else None
    out = {"cell": name, "variant": meta["variant"], "n": meta["n"], "window_s": meta["window_s"],
           "rounds_h": len(rounds) * 3600 / meta["window_s"], "blocks": blocks,
           "turns_h": len({(r["label"], r["turn"]) for r in rounds if r["final"]}) * 3600 / meta["window_s"],
           "calls_h": len(calls) * 3600 / meta["window_s"],
           "hit": sum(c["cached"] or 0 for c in calls) / max(1, sum(c["n_prompt"] or 0 for c in calls)),
           "lat_p50": q(lat, 0.5), "lat_p95": q(lat, 0.95), "errors": sum(c["status"] != "ok" for c in calls),
           "early_share": sum(bool(c.get("early")) for c in calls) / max(1, len(calls))}
    sdir = server_dir(name)
    if sdir is not None and ((sdir / "sched.jsonl").exists() or (sdir / "sched.jsonl.xz").exists()):
        srv = ma.Server(sdir)
        ma.step_features([srv])
        model = ma.fit_step_model([srv])
        ma.attribute_steps([srv], model)
        cls = defaultdict(float)
        busy = 0.0
        for st in srv.steps:
            if not (w0 <= srv.wall(st["g0"]) < w1) or not st.get("share"):
                continue
            sh = st["share"]
            busy += st["gpu"]
            k = len({c[0] for c in sh["pf"]} | {x[0] for x in sh["dec"]}) or 1
            base = sh["fixed"] / k
            for ri, cost, n, fresh, ev, pre in sh["pf"]:
                if n:
                    cls["prefill_fresh"] += (cost + base) * fresh / n
                    cls["prefill_evicted"] += (cost + base) * ev / n
                    cls["prefill_preempted"] += (cost + base) * pre / n
            cls["decode"] += sum(cost + base for _, cost in sh["dec"])
        waits = sorted(srv.wall(r["adm"][0]["t"]) - srv.wall(r["arr"]["t"]) for r in srv.req.values()
                       if r["adm"] and w0 <= srv.wall(r["arr"]["t"]) < w1)
        out.update(gpu_busy_s=busy, gpu_s_per_round=busy / max(1, len(rounds)),
                   gpu_share={k: v / busy for k, v in cls.items()} if busy else {},
                   queue_p50=q(waits, 0.5), queue_p95=q(waits, 0.95),
                   preemptions=sum(len(r["pre"]) for r in srv.req.values() if w0 <= srv.wall(r["arr"]["t"]) < w1),
                   step_r2=model.get("r2"))
    return out


def compare(cell: dict, ref: dict, seed: int = 20) -> dict:
    """Gain of rounds/h over the reference cell, with a bootstrap over the 10-min blocks of both cells."""
    rng = random.Random(seed)
    a, b = cell["blocks"], ref["blocks"]
    point = (sum(a) - sum(b)) / sum(b) if sum(b) else None
    draws = []
    for _ in range(DRAWS):
        sa = sum(rng.choice(a) for _ in a)
        sb = sum(rng.choice(b) for _ in b)
        if sb:
            draws.append((sa - sb) / sb)
    draws.sort()
    lo, hi = (draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]) if draws else (None, None)
    verdict = "no clear difference"
    if lo is not None and lo > 0 and point >= FLOOR:
        verdict = "helps"
    elif hi is not None and hi < 0 and point <= -FLOOR:
        verdict = "hurts"
    return {"gain": point, "lo": lo, "hi": hi, "verdict": verdict}


def show(cells: list[str], ref: str | None) -> None:
    rows = {c: cell_summary(c) for c in cells}
    print("| cell | variant | N | rounds/h | turns/h | calls/h | hit | call p50 / p95 (s) | queue p50 / p95 (s) | "
          "GPU-s per round | GPU: decode / fresh / evicted | early calls | errors | vs ref |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    f = lambda x, nd=1: "—" if x is None else f"{x:.{nd}f}"
    for c, r in rows.items():
        g = r.get("gpu_share") or {}
        cmp_ = compare(r, rows[ref]) if ref and c != ref else None
        print(f"| {c} | {r['variant']} | {r['n']} | {r['rounds_h']:.0f} | {r['turns_h']:.0f} | {r['calls_h']:.0f} | "
              f"{r['hit']:.1%} | {f(r['lat_p50'])} / {f(r['lat_p95'])} | {f(r.get('queue_p50'))} / {f(r.get('queue_p95'))} | "
              f"{f(r.get('gpu_s_per_round'), 2)} | {g.get('decode', 0):.0%} / {g.get('prefill_fresh', 0):.0%} / "
              f"{g.get('prefill_evicted', 0):.0%} | {r['early_share']:.0%} | {r['errors']} | "
              + (f"{cmp_['gain']:+.0%} [{cmp_['lo']:+.0%}, {cmp_['hi']:+.0%}] {cmp_['verdict']}" if cmp_ else "—") + " |")
    out = LOAD / "tables"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"load_{'_'.join(cells)}.json"[:200]).write_text(json.dumps(rows, indent=1, default=str) + "\n")


def chain(specs: list[str], warmup_s: float, window_s: float) -> int:
    """Run load cells one after another, each in its own rd_serve stage (claims checked, cap kept, server stopped).
    A spec is cell:plan:variant:N (t16 plans run on load16 with priority scheduling, t19 plans on load19)."""
    import subprocess
    py = sys.executable
    for spec in specs:
        cell, plan, variant, n = spec.split(":")
        src = json.loads((LOAD / f"plan_{plan}.json").read_text())["src"]
        config = "load16" if src == "t16" else "load19"
        hours = (warmup_s + window_s + 600) / 3600
        cmd = [py, str(rr.DIR / "rd_serve.py"), "run", "--stage", cell, "--hours", f"{hours:.2f}", "--config", config,
               "--", py, str(rr.DIR / "rd_loadreplay.py"), "run", "--url", "{url}", "--plan", plan, "--variant",
               variant, "--n", n, "--out", cell, "--warmup-s", str(warmup_s), "--window-s", str(window_s)]
        if config == "load16":
            cmd.append("--priority-policy")
        print(f"[chain] {cell}: {' '.join(cmd[2:])}", flush=True)
        rc = subprocess.call(cmd, cwd=rr.REPO)
        print(f"[chain] {cell}: exit {rc}", flush=True)
        if rc != 0:
            return rc
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--run", default="main")
    p.add_argument("--units", choices=("t19", "t16"), required=True)
    p.add_argument("--benches", default="", help="t16 only, e.g. swepro,tbench")
    p.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--plan", required=True)
    r.add_argument("--variant", choices=VARIANTS, required=True)
    r.add_argument("--n", type=int, required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--warmup-s", type=float, default=900)
    r.add_argument("--window-s", type=float, default=3600)
    r.add_argument("--stagger-s", type=float, default=300)
    r.add_argument("--seed", type=int, default=20)
    r.add_argument("--renderers", type=int, default=16)
    r.add_argument("--priority-policy", action="store_true", help="the server runs --scheduling-policy priority")
    r.add_argument("--time-scale", type=float, default=1.0, help="tests only: scale every recorded pause")
    ch = sub.add_parser("chain")
    ch.add_argument("specs", nargs="+", help="cell:plan:variant:N")
    ch.add_argument("--warmup-s", type=float, default=900)
    ch.add_argument("--window-s", type=float, default=3600)
    c = sub.add_parser("check")
    c.add_argument("--plan", required=True)
    c.add_argument("--workers", type=int, default=48)
    s = sub.add_parser("show")
    s.add_argument("--cells", required=True)
    s.add_argument("--ref", default=None, help="the cell the others are compared with (rounds/h)")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.run, a.units, a.out, set(a.benches.split(",")) if a.benches else None)
    elif a.cmd == "chain":
        sys.exit(chain(a.specs, a.warmup_s, a.window_s))
    elif a.cmd == "check":
        sys.exit(check(a.plan, a.workers))
    elif a.cmd == "run":
        plan = json.loads((LOAD / f"plan_{a.plan}.json").read_text())
        asyncio.run(Cell(a, plan).main())
    else:
        show(a.cells.split(","), a.ref)


if __name__ == "__main__":
    main()
