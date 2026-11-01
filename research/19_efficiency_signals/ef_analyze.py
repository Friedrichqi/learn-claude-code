#!/usr/bin/env python3
"""Tables and checks of research/19_efficiency_signals (offline): what exposing the server's state does to how
MiniMax-M2.7 works when the server is crowded.

Built on research/18_harness_wait/wt_analyze.py. Its WCell holds the window, the captures, the traces, the
WTScheduler log and the /wt/status audit log; topic 11's mu_analyze does the joins, the token classes and the step
model, refit on the cells given. Everything is clipped to the measurement window. A lead call is overhead-only when
all its tool calls are pacing tools (wait, wait_for_capacity, server_status); every other lead call is work.

  T  throughput and efficiency:
     - completed turns / h (primary) and work lead calls / h, with 10-min blocks;
     - Kaplan-Meier turn p50 / p95, GPU-seconds per completed turn;
     - hit rate, the GPU share by class, output tokens per call, max_tokens escalations
  R  the pre-registered decisions:
     - R1 recovery (X - none) / (sgate - none), with X in {m_wait, m_tools}, at N = 16 and 32;
     - R2 m_tools - m_wait;
     - R3 no harm at N_light;
     - floor F = 10 % of none; block bootstrap, 4,000 draws
  Q  quality guard: turns that end normally; complete sessions' M turns that did their edit (M1: BUILD_TAG and its
     changelog, M2: the _v2 rename, M3: docstrings in the module)
  B  behaviour:
     - B1: waits and step-backs placed in a response that also does work; tool calls per response;
     - B2: overhead-only calls per 100 work calls and their GPU share;
     - B3: P(wait or step-back | crowded) - P(... | not crowded). Crowded means the sessions ahead plus the
       session's own context exceed the pool, by the status the harness fetched before the call;
     - B4: tool calls per response, output tokens per call, calls per turn and exact re-reads, crowded vs not;
     - B5: status reads per 100 work calls, waits after a read, reads after a costly step

    python research/19_efficiency_signals/ef_analyze.py --smoke s_none,s_m_wait,s_m_tools,s_gate2
    python research/19_efficiency_signals/ef_analyze.py --pilot p08_none,p16_none,p16_m_wait,p16_m_tools[,p16_sgate]
    python research/19_efficiency_signals/ef_analyze.py --cells w16_none,... --out research/19_efficiency_signals/data/tables/main.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import ef_workloads as ew  # noqa: E402
import wt_analyze as wa  # noqa: E402  research/18_harness_wait

ma = wa.ma
ma.mw.CONDITIONS.update(ew.conditions())
ma.TEMPLATE_TAIL = 0                # the MiniMax template renders the generation prompt again in the next request

PACING = {"wait", "wait_for_capacity", "server_status"}
STEP_BACK = {"wait", "wait_for_capacity"}
FLOOR = 0.10
ITERS, SEED = 4000, 19
ANCHOR = 23322.4                    # ef_calibrate: mean lead prompt tokens of topic 18's w08_none
COST_RE = re.compile(r"\[cost\] your last step: ([\d,]+) prompt tokens, ([\d,]+) reused from the server's cache"
                     r"(?:, ([\d,]+) recomputed because they had been evicted, ([\d,]+) new or changed; queued "
                     r"([\d.]+) s)?")
q = wa.q
ts = wa.ts


def num(s: str | None) -> int | None:
    return None if s is None else int(s.replace(",", ""))


class EfCell(wa.WCell):
    """A topic-19 window cell: overhead-only (all pacing tools) replaces topic 18's wait-only."""

    def __init__(self, cond: str, root: Path = ew.CELLS):
        super().__init__(cond, root)
        self.pool = None
        for srv in self.cell.servers:
            hdr = getattr(srv, "hdr", None) or {}
            if hdr.get("nblk"):
                self.pool = (hdr["nblk"] - 1) * hdr.get("bs", 16)
        self.pool = self.pool or (ew.POOL_BLOCKS - 1) * 16
        self.sid = {s["meta"]["label"]: f"{s['meta']['label']}~a{s['meta'].get('attempt', 1)}" for s in self.cell.sessions}
        self.requests = {}                              # (label, call index) -> the request's messages
        for sess in self.cell.sessions:
            for c in sess["calls"]:
                if c.get("messages") is not None:
                    self.requests[(sess["meta"]["label"], c["call_index"])] = c["messages"]

    def uses(self, row) -> list[str]:
        blocks = self.content.get((row["label"], row["call"])) or []
        return [b.get("name") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]

    def wait_only(self, row) -> bool:                   # overhead-only
        names = self.uses(row)
        return bool(names) and all(n in PACING for n in names)

    def newest_texts(self, row) -> list[str]:
        msgs = self.requests.get((row["label"], row["call"])) or []
        content = (msgs[-1] if msgs else {}).get("content")
        if isinstance(content, str):
            return [content]
        return [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]

    def crowded_at(self) -> dict:
        """(label, call) -> was the server crowded by the status the harness fetched just before the call
        (sessions ahead + own context > pool)? Only for arms that fetch a status every round."""
        by_sid = defaultdict(list)
        for r in self.ext:
            me = (r.get("out") or {}).get("me")
            if r.get("state") == "line" and isinstance(me, dict) and r.get("wall") is not None:
                by_sid[r.get("sid")].append((r["wall"], (me.get("ahead_ws") or 0) + (me.get("ws") or 0) > self.pool))
        for v in by_sid.values():
            v.sort()
        out = {}
        for row in self.lead():
            seq = by_sid.get(self.sid.get(row["label"]))
            if not seq or row.get("t_send") is None:
                continue
            i = np.searchsorted([t for t, _ in seq], row["t_send"] + 0.5, side="right") - 1
            if i >= 0 and row["t_send"] - seq[i][0] < 30:
                out[(row["label"], row["call"])] = seq[i][1]
        return out


# -- T: throughput and efficiency ----------------------------------------------------------------------------------
def turn_records(w: EfCell) -> list[dict]:
    """Every turn that ended inside the window: its end, whether it ended normally (the model answered without a tool
    call) and its lead calls."""
    out = []
    for sess in w.cell.sessions:
        label = sess["meta"]["label"]
        stops, ends = {}, {}
        for e in sess["events"]:
            if e["event"] == "harness_decision" and (e.get("data") or {}).get("decision") == "stop":
                stops[e.get("turn_id")] = e["data"].get("reason")
            elif e["event"] == "turn_end":
                ends[e.get("turn_id")] = ts(e)
        calls = Counter(c.get("turn_id") for c in sess["calls"] if c.get("purpose") == "lead" and c.get("response"))
        for tid, t in ends.items():
            if w.inside(t):
                out.append({"label": label, "turn": tid, "t": t, "normal": stops.get(tid) == "no_tool_use",
                            "calls": calls.get(tid, 0)})
    return out


def tp_of(c: dict) -> int:
    args = [str(a) for a in c.get("vllm") or []]
    return int(args[len(args) - 1 - args[::-1].index("--tensor-parallel-size") + 1]) \
        if "--tensor-parallel-size" in args else 1


def t_row(w: EfCell) -> dict:
    base = wa.h5_row(w)
    turns = turn_records(w)
    blocks = wa.block_counts([r["t"] for r in turns], w.ws, w.we)
    work = w.lead_in_window(True)
    g = base["gpu"]
    busy_s = g.get("busy", 0.0) * (w.we - w.ws) * tp_of(w.c)      # engine-busy seconds x GPUs (TP)
    outs = [r.get("out") for r in w.lead_in_window(False) if r.get("out") is not None]
    esc = sum(1 for _, e in w.events("harness_decision") if w.inside(ts(e))
              and (e.get("data") or {}).get("action") == "increase_max_tokens")
    return {**base, "turns_h": len(turns) / w.hours, "turn_blocks": blocks, "turns": len(turns),
            "work_blocks": base["blocks"], "gpu_s_per_turn": busy_s / len(turns) if turns else None,
            "out_p50": q(outs, 0.5), "out_p95": q(outs, 0.95), "escalations": esc,
            "overhead_calls": base["wait_only_calls"], "work_calls": len(work)}


# -- R: the pre-registered decisions ---------------------------------------------------------------------------------
def _boot(blocks: list[float], rng, iters: int) -> np.ndarray:
    b = np.array(blocks, dtype=float)
    return np.array([b[rng.integers(0, len(b), len(b))].mean() for _ in range(iters)])


def contrast(x: dict, base: dict, key: str = "turn_blocks", iters: int = ITERS, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    bx, bb = _boot(x[key], rng, iters), _boot(base[key], rng, iters)
    d = bx - bb
    return {"diff": float(np.mean(x[key]) - np.mean(base[key])), "lo": float(np.percentile(d, 2.5)),
            "hi": float(np.percentile(d, 97.5))}


def recovery(x: dict, none: dict, gate: dict, key: str = "turn_blocks", iters: int = ITERS, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    bx, bn, bg = (_boot(r[key], rng, iters) for r in (x, none, gate))
    den = bg - bn
    ok = den != 0
    draws = (bx[ok] - bn[ok]) / den[ok]
    pd = np.mean(gate[key]) - np.mean(none[key])
    point = (np.mean(x[key]) - np.mean(none[key])) / pd if pd else None
    return {"R": None if point is None else float(point), "lo": float(np.percentile(draws, 2.5)) if len(draws) else None,
            "hi": float(np.percentile(draws, 97.5)) if len(draws) else None}


def decisions(t: dict, n_light: int | None) -> dict:
    """t: cond -> t_row. Cells are found by name (wNN_arm)."""
    out = {"floor": FLOOR, "R1": {}, "R2": {}, "R3": {}}
    for n in (16, 32):
        none, gate = t.get(f"w{n:02d}_none"), t.get(f"w{n:02d}_sgate")
        if not (none and gate):
            continue
        floor = FLOOR * float(np.mean(none["turn_blocks"]))
        for arm in ("m_wait", "m_tools"):
            x = t.get(f"w{n:02d}_{arm}")
            if not x:
                continue
            g, r = contrast(x, none), recovery(x, none, gate)
            verdict = ("recovers" if r["R"] is not None and r["R"] >= 0.5 and g["lo"] > floor else
                       "hurts" if g["hi"] < -floor else "neither")
            out["R1"][f"{arm}@{n}"] = {"gain": g, "R": r, "floor": floor, "verdict": verdict,
                                       "work_calls_R": recovery(x, none, gate, key="work_blocks")}
        if t.get(f"w{n:02d}_m_tools") and t.get(f"w{n:02d}_m_wait"):
            d = contrast(t[f"w{n:02d}_m_tools"], t[f"w{n:02d}_m_wait"])
            out["R2"][str(n)] = {**d, "verdict": "toolbox better" if d["lo"] > floor else
                                 "toolbox worse" if d["hi"] < -floor else "no clear difference"}
    if n_light:
        none, x = t.get(f"w{n_light:02d}_none"), t.get(f"w{n_light:02d}_m_tools")
        if none and x:
            floor = FLOOR * float(np.mean(none["turn_blocks"]))
            drop = (none["hit_rate"] or 0) - (x["hit_rate"] or 0)
            diff = float(np.mean(x["turn_blocks"]) - np.mean(none["turn_blocks"]))
            out["R3"] = {"n": n_light, "diff": diff, "floor": floor, "hit_drop_points": 100 * drop,
                         "verdict": "no harm" if diff >= -floor and drop <= 0.02 else "harm"}
    return out


# -- Q: quality guard ---------------------------------------------------------------------------------------------
def m_passed(template: str, target: dict | None, diff: str) -> bool:
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    if template == "M1":
        return any("BUILD_TAG" in a for a in added) and "CHANGES_BUILD_TAG.md" in diff
    if template == "M2" and target:
        return any(f"{target['func']}_v2" in a for a in added)
    if template == "M3" and target:
        # "+++ <path>\t<timestamp>": the path is the second field
        touched = any(line.startswith("+++ ") and len(line.split()) > 1
                      and line.split()[1].endswith("/" + target["module"]) for line in diff.splitlines())
        return touched and any('"""' in a for a in added)
    return False


def q_row(w: EfCell, specs: dict) -> dict:
    turns = turn_records(w)
    normal = sum(1 for r in turns if r["normal"]) / len(turns) if turns else None
    passed = total = 0
    for sess in w.cell.sessions:
        meta = sess["meta"]
        if meta.get("filler") or meta.get("status") != "ok" or not w.inside(meta.get("t_end")):
            continue
        spec = specs.get(meta.get("spec"))
        diff_path = w.root / "runs" / meta["label"] / f"{meta['label']}.diff"
        if not spec or not diff_path.exists():
            continue
        diff = diff_path.read_text(errors="replace")
        for turn in spec["turns"]:
            if turn["family"] == "MODIFY":
                total += 1
                passed += m_passed(turn["template"], turn.get("target"), diff)
    return {"cond": w.cond, "turns_ended": len(turns), "normal_end": normal, "m_turns": total,
            "m_passed": passed / total if total else None}


# -- B: behaviour ---------------------------------------------------------------------------------------------------
def rereads(messages: list) -> tuple[int, int]:
    """(exact re-reads, reads) among the read_file results in the newest user message: same (path, offset, limit)
    as an earlier read whose result is still in full in the prompt, with no write or edit of the path in between."""
    if not messages:
        return 0, 0
    last = messages[-1]
    if last.get("role") != "user" or not isinstance(last.get("content"), list):
        return 0, 0
    uses, seen = {}, {}

    def path_of(inp):
        inp = inp or {}
        return str(inp.get("path") or inp.get("file_path"))

    def full(text):
        return isinstance(text, str) and not text.startswith("[Earlier tool result saved at") \
            and not text.startswith("<persisted-output>")

    for m in messages[:-1]:
        for b in m.get("content") if isinstance(m.get("content"), list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uses[b.get("id")] = (b.get("name"), b.get("input") or {})
                if b.get("name") in ("write_file", "edit_file"):
                    p = path_of(b.get("input"))
                    for k in [k for k in seen if k[0] == p]:
                        del seen[k]
            elif b.get("type") == "tool_result":
                name, inp = uses.get(b.get("tool_use_id"), (None, {}))
                if name == "read_file" and full(b.get("content")):
                    seen[(path_of(inp), inp.get("offset"), inp.get("limit"))] = True
    dups = reads = 0
    for b in last["content"]:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            name, inp = uses.get(b.get("tool_use_id"), (None, {}))
            if name == "read_file":
                reads += 1
                dups += (path_of(inp), inp.get("offset"), inp.get("limit")) in seen
    return dups, reads


def b_row(w: EfCell) -> dict:
    lead = sorted(w.lead_in_window(False), key=lambda r: (r["label"], r["call"]))
    work = [r for r in lead if not w.wait_only(r)]
    crowded = w.crowded_at()
    sizes, combined, step_backs, pulls = Counter(), 0, 0, 0
    per = {True: defaultdict(list), False: defaultdict(list), None: defaultdict(list)}
    track = {True: [0, 0], False: [0, 0]}
    rr = [0, 0]
    for r in lead:
        names = w.uses(r)
        sizes[min(len(names), 4)] += 1
        sb = any(n in STEP_BACK for n in names)
        pulls += "server_status" in names
        if sb:
            step_backs += 1
            combined += any(n not in PACING for n in names)
        c = crowded.get((r["label"], r["call"]))
        if c is not None:
            track[c][0] += sb
            track[c][1] += 1
        if not w.wait_only(r):
            per[c]["tools"].append(sum(1 for n in names if n not in PACING))
            if r.get("out") is not None:
                per[c]["out"].append(r["out"])
        d, n = rereads(w.requests.get((r["label"], r["call"])) or [])
        rr[0] += d
        rr[1] += n
    p = {k: (v[0] / v[1] if v[1] else None) for k, v in track.items()}
    turns = turn_records(w)
    # B5: status reads, the reads before a step-back, and reads after a costly step (from the [cost] receipts)
    by_label = defaultdict(list)
    for r in sorted(w.lead(), key=lambda r: (r["label"], r["call"])):
        by_label[r["label"]].append(r)
    pulled_before, after_costly, after_cheap = [], [], []
    for rows in by_label.values():
        for i, r in enumerate(rows):
            if not w.inside(r.get("t_recv")):
                continue
            names = w.uses(r)
            if any(n in STEP_BACK for n in names):
                prior = rows[max(0, i - 2):i + 1]
                pulled_before.append(any("server_status" in w.uses(x) for x in prior))
            m = next((COST_RE.search(t) for t in w.newest_texts(r) if t.startswith("[cost]")), None)
            if m and m.group(3) is not None and num(m.group(1)):
                share = num(m.group(3)) / num(m.group(1))
                (after_costly if share >= 0.5 else after_cheap).append("server_status" in names)
    yields = [e["data"] for _, e in w.events("yield_detail") if w.inside(ts(e))]
    waits = [e["data"] for _, e in w.events("wait_detail") if w.inside(ts(e))]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    gpu = wa.gpu_by_reply(w)
    return {"cond": w.cond, "arm": w.arm, "lead_calls": len(lead), "work_calls": len(work),
            "tools_per_response": dict(sorted(sizes.items())),
            "step_back_responses": step_backs, "step_back_combined": combined / step_backs if step_backs else None,
            "overhead_per_100_work": 100 * (len(lead) - len(work)) / len(work) if work else None,
            "gpu_overhead": gpu["wait_only"], "gpu_overhead_evicted": gpu["wait_only_evicted"],
            "p_step_back_crowded": p[True], "p_step_back_calm": p[False],
            "tracking_points": 100 * (p[True] - p[False]) if p[True] is not None and p[False] is not None else None,
            "crowded_share": track[True][1] / (track[True][1] + track[False][1]) if track[True][1] + track[False][1] else None,
            "tools_per_work_response": {str(k): mean(v["tools"]) for k, v in per.items() if v["tools"]},
            "out_tokens_per_work_call": {str(k): q(v["out"], 0.5) for k, v in per.items() if v["out"]},
            "calls_per_turn": mean([r["calls"] for r in turns]),
            "rereads_per_100_reads": 100 * rr[0] / rr[1] if rr[1] else None,
            "pulls_per_100_work": 100 * pulls / len(work) if work else None,
            "step_backs_after_a_read": mean(pulled_before),
            "pull_after_costly_step": mean(after_costly), "pull_after_cheap_step": mean(after_cheap),
            "waits": len(waits), "wait_req_p50": q([d.get("requested_s") for d in waits], 0.5),
            "yields": len(yields), "yield_p50": q([d.get("waited_s") for d in yields], 0.5),
            "yield_p95": q([d.get("waited_s") for d in yields], 0.95),
            "yield_why": dict(Counter(d.get("why") for d in yields))}


# -- smoke ----------------------------------------------------------------------------------------------------------
def natural_pool(w: EfCell) -> int | None:
    for srv_dir in sorted((w.root / "server").glob("*")):
        log = srv_dir / "vllm.log"
        if log.exists():
            m = re.search(r"Overriding num_gpu_blocks=(\d+) with num_gpu_blocks_override=(\d+)", log.read_text(errors="replace"))
            if m:
                return int(m.group(1))
    return None


def append_only(w: EfCell) -> dict:
    """Consecutive lead calls of a session whose history the harness did not rewrite: does the new prompt reuse the
    previous prompt and reply, block for block (the server's ideal covers their full blocks)?"""
    by_label = defaultdict(list)
    for r in w.lead():
        if r.get("ideal_arr") is not None:
            by_label[r["label"]].append(r)
    res = {"pairs": 0, "rewritten": 0, "prompt_kept": 0, "reply_kept": 0, "turn_pairs": 0, "turn_reply_kept": 0}
    for label, rows in by_label.items():
        rows.sort(key=lambda r: r["call"])
        for a, b in zip(rows, rows[1:]):
            ma_, mb = w.requests.get((label, a["call"])), w.requests.get((label, b["call"]))
            harness_rewrite = ma_ is not None and mb is not None and mb[:len(ma_)] != ma_
            if b.get("trace_causes") or harness_rewrite:
                res["rewritten"] += 1          # micro_compact placeholders, compaction: the harness's, not the template's
                continue
            res["pairs"] += 1
            prompt_ok = not b.get("edited")
            want = ((a["srv_np"] + (a.get("out") or 0) - 1) // 16) * 16
            reply_ok = prompt_ok and b["ideal_arr"] >= want - 16
            res["prompt_kept"] += prompt_ok
            res["reply_kept"] += reply_ok
            if not b.get("same_turn"):
                res["turn_pairs"] += 1
                res["turn_reply_kept"] += reply_ok
    for k in ("prompt_kept", "reply_kept"):
        res[k + "_share"] = res[k] / res["pairs"] if res["pairs"] else None
    res["turn_reply_kept_share"] = res["turn_reply_kept"] / res["turn_pairs"] if res["turn_pairs"] else None
    return res


def tool_token(w: EfCell) -> dict:
    """The scheduler's tool-call token (MU_TOOLCALL_TOKEN) against the replies that called a tool."""
    agree = total = 0
    for r in w.lead():
        rec = r.get("rec")
        if not rec or not rec.get("fin") or "tool" not in rec["fin"]:
            continue
        total += 1
        agree += bool(rec["fin"]["tool"]) == bool(w.uses(r))
    return {"lead_calls": total, "agree": agree / total if total else None}


def receipts(w: EfCell) -> dict:
    """Each [cost] receipt against the server's record of the call it describes (the session's previous lead call)."""
    by_label = defaultdict(list)
    for r in w.lead():
        by_label[r["label"]].append(r)
    n = exact = split = 0
    bad = []
    for rows in by_label.values():
        rows.sort(key=lambda r: r["call"])
        for a, b in zip(rows, rows[1:]):
            m = next((COST_RE.search(t) for t in w.newest_texts(b) if t.startswith("[cost]")), None)
            if not m or a.get("hit") is None:
                continue
            n += 1
            ok = num(m.group(1)) == a["np"] and num(m.group(2)) == a["cached"]
            if m.group(3) is not None:
                split += 1
                ideal = max(a["hit"], min(a["srv_np"], a["ideal"]))
                queue = (a.get("parts") or {}).get("queue")
                ok = ok and num(m.group(3)) == ideal - a["hit"] and queue is not None \
                    and abs(float(m.group(5)) - queue) <= 0.06
            exact += ok
            if not ok and len(bad) < 3:
                bad.append({"receipt": m.group(0), "np": a["np"], "cached": a["cached"], "hit": a["hit"],
                            "ideal": a["ideal"], "queue": (a.get("parts") or {}).get("queue")})
    return {"receipts": n, "with_server_split": split, "match": exact / n if n else None, "mismatches": bad}


def smoke(conds: list[str], root: Path = ew.CELLS) -> dict:
    base = wa.smoke(conds, root)
    out = {}
    for cond in conds:
        w = EfCell(cond, root)
        spans = {k: sum(1 for _ in w.events(k)) for k in ("wait_detail", "yield_detail", "status_pull", "hold_start")}
        deferred = [e["data"] for _, e in w.events("tool_end") if (e.get("data") or {}).get("tool") in STEP_BACK]
        lead = w.lead()
        out[cond] = {**base[cond], "natural_pool_blocks": natural_pool(w), "append_only": append_only(w),
                     "tool_token": tool_token(w), "receipts": receipts(w), "pacing_spans": spans,
                     "step_backs_deferred": (sum(1 for d in deferred if d.get("deferred")) / len(deferred)) if deferred else None,
                     "out_p50_p95": (q([r.get("out") for r in lead], 0.5), q([r.get("out") for r in lead], 0.95)),
                     "escalations": sum(1 for _, e in w.events("harness_decision")
                                        if (e.get("data") or {}).get("action") == "increase_max_tokens")}
    return out


# -- pilot rules ----------------------------------------------------------------------------------------------------
def fit_steps(cells: dict) -> dict:
    """Topic 11's step model, refit on these cells' engine steps; it splits each step's GPU time by class."""
    servers = [s for w in cells.values() for s in w.cell.servers]
    ma.step_features(servers)
    model = ma.fit_step_model(servers)
    if model.get("coef"):
        ma.attribute_steps(servers, model)
    return model


def pilot(conds: list[str], root: Path = ew.CELLS) -> dict:
    cells = {c: EfCell(c, root) for c in conds}
    model = fit_steps(cells)
    t = {c: t_row(w) for c, w in cells.items()}
    b = {c: b_row(w) for c, w in cells.items() if w.arm and w.arm.startswith("m_")}
    rules = {}
    if "p16_none" in t:
        r = t["p16_none"]
        rules["regime_none16_thrashes"] = bool(r["hit_rate"] is not None and r["hit_rate"] < 0.5
                                               and r["gpu"].get("prefill_evicted", 0) > 0.30)
    if "p08_none" in t:
        r = t["p08_none"]
        light = r["hit_rate"] is not None and r["hit_rate"] >= 0.80 and r["gpu"].get("prefill_evicted", 0) <= 0.10
        rules["n_light"] = 8 if light else 4
        mean_np = r["prompt_per_call"]
        lead = [x.get("np") for x in cells["p08_none"].lead_in_window(True) if x.get("np")]
        mean_np = sum(lead) / len(lead) if lead else mean_np
        rules["p08_mean_lead_prompt"] = mean_np
        rules["gate_k"] = int(min(12, max(4, round(8 * ANCHOR / mean_np)))) if mean_np else None
    est = {}
    for c, w in cells.items():
        if w.arm == "none":
            walls = [s["meta"].get("wall_s") for s in w.cell.sessions
                     if not s["meta"].get("filler") and s["meta"].get("status") == "ok"]
            if walls:
                est[w.n] = float(np.mean(walls))
    if est:
        rules["session_est_s_measured"] = est
    if "p16_m_wait" in b:
        w = cells["p16_m_wait"]
        h = wa.h1b_row(w)
        sessions = len({s["meta"]["label"] for s in w.cell.sessions})
        rules["m_wait_overhead_per_100_work"] = b["p16_m_wait"]["overhead_per_100_work"]
        rules["m_wait_sessions_with_run_ge10"] = h["sessions_ge10"] / sessions if sessions else None
        rules["revise_text"] = bool((b["p16_m_wait"]["overhead_per_100_work"] or 0) >= 30
                                    or (rules["m_wait_sessions_with_run_ge10"] or 0) > 0.25)
    for c in ("p16_m_wait", "p16_m_tools"):
        if c in b:
            got = Counter(n for r in cells[c].lead() for n in cells[c].uses(r) if n in PACING)
            rules[f"{c}_pacing_calls"] = dict(got)
            hw = h4_max_hold(cells[c])
            rules[f"{c}_no_deadlock"] = hw < 1800
    return {"step_model": model, "t": t, "b": b, "rules": rules}


def h4_max_hold(w: EfCell) -> float:
    tot = defaultdict(float)
    for label, e in w.events("wait_detail"):
        tot[label] += e["data"].get("slept_s") or 0.0
    for label, e in w.events("yield_detail"):
        tot[label] += e["data"].get("waited_s") or 0.0
    return max(tot.values()) if tot else 0.0


# -- tables ---------------------------------------------------------------------------------------------------------
def fmt(x, nd=0, pct=False):
    if x is None:
        return "—"
    if pct:
        return f"{100 * x:.1f} %"
    return f"{x:,.{nd}f}"


def tables(t: dict, b: dict, qd: dict, dec: dict) -> str:
    lines = ["## T: throughput and efficiency (measurement window)", "",
             "| cell | turns/h [95 %] | work lead calls/h | turn p50 / p95 (KM, s) | GPU-s per turn (busy x TP) | hit rate | "
             "GPU evicted / preempted | out tokens p50 / p95 | escalations |", "|---|---|---|---|---|---|---|---|---|"]
    for c, r in t.items():
        lo, hi = q(r["turn_blocks"], 0.025), q(r["turn_blocks"], 0.975)
        lines.append(f"| {c} | {fmt(r['turns_h'], 1)} [{fmt(lo, 0)}, {fmt(hi, 0)}] | {fmt(r['work_calls_h'])} | "
                     f"{fmt(r['turn_p50'])} / {fmt(r['turn_p95'])} | {fmt(r['gpu_s_per_turn'], 1)} | "
                     f"{fmt(r['hit_rate'], pct=True)} | {fmt(r['gpu'].get('prefill_evicted'), pct=True)} / "
                     f"{fmt(r['gpu'].get('prefill_preempted'), pct=True)} | {fmt(r['out_p50'])} / {fmt(r['out_p95'])} | "
                     f"{r['escalations']} |")
    if dec.get("R1") or dec.get("R2") or dec.get("R3"):
        lines += ["", "## R: decisions (completed turns / h; floor F = 10 % of none)", "",
                  "| contrast | gain [95 %] | R [95 %] | verdict | R on work calls |", "|---|---|---|---|---|"]
        for k, v in dec["R1"].items():
            g, r, rw = v["gain"], v["R"], v["work_calls_R"]
            lines.append(f"| R1 {k} | {fmt(g['diff'], 1)} [{fmt(g['lo'], 1)}, {fmt(g['hi'], 1)}] | "
                         f"{fmt(r['R'], 2)} [{fmt(r['lo'], 2)}, {fmt(r['hi'], 2)}] | {v['verdict']} | {fmt(rw['R'], 2)} |")
        for n, v in dec["R2"].items():
            lines.append(f"| R2 m_tools − m_wait @{n} | {fmt(v['diff'], 1)} [{fmt(v['lo'], 1)}, {fmt(v['hi'], 1)}] | — | "
                         f"{v['verdict']} | — |")
        if dec.get("R3"):
            v = dec["R3"]
            lines.append(f"| R3 m_tools − none @{v['n']} | {fmt(v['diff'], 1)} (floor {fmt(v['floor'], 1)}; hit −"
                         f"{fmt(v['hit_drop_points'], 1)} points) | — | {v['verdict']} | — |")
    lines += ["", "## Q: quality guard", "", "| cell | turns ended | ended normally | M turns (complete sessions) | "
              "M edits done |", "|---|---|---|---|---|"]
    for c, r in qd.items():
        lines.append(f"| {c} | {r['turns_ended']} | {fmt(r['normal_end'], pct=True)} | {r['m_turns']} | "
                     f"{fmt(r['m_passed'], pct=True)} |")
    if b:
        lines += ["", "## B: behaviour (model arms)", "",
                  "| cell | tool calls per response (0/1/2/3/4+) | step-backs with work | overhead per 100 work | "
                  "GPU on overhead | P(step-back) crowded / calm | crowded share | status reads per 100 work | "
                  "step-backs after a read | reads after costly / cheap step | waits, yields (p50 / p95 s) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for c, r in b.items():
            sz = "/".join(str(r["tools_per_response"].get(k, 0)) for k in range(5))
            lines.append(f"| {c} | {sz} | {fmt(r['step_back_combined'], pct=True)} | {fmt(r['overhead_per_100_work'], 1)} | "
                         f"{fmt(r['gpu_overhead'], pct=True)} | {fmt(r['p_step_back_crowded'], pct=True)} / "
                         f"{fmt(r['p_step_back_calm'], pct=True)} | {fmt(r['crowded_share'], pct=True)} | "
                         f"{fmt(r['pulls_per_100_work'], 1)} | {fmt(r['step_backs_after_a_read'], pct=True)} | "
                         f"{fmt(r['pull_after_costly_step'], pct=True)} / {fmt(r['pull_after_cheap_step'], pct=True)} | "
                         f"{r['waits']}, {r['yields']} ({fmt(r['yield_p50'])} / {fmt(r['yield_p95'])}) |")
        lines += ["", "| cell | tools per work response (crowded / calm) | output tokens per work call (crowded / calm) | "
                  "calls per turn | exact re-reads per 100 reads |", "|---|---|---|---|---|"]
        for c, r in b.items():
            tp, ot = r["tools_per_work_response"], r["out_tokens_per_work_call"]
            lines.append(f"| {c} | {fmt(tp.get('True'), 2)} / {fmt(tp.get('False'), 2)} | {fmt(ot.get('True'))} / "
                         f"{fmt(ot.get('False'))} | {fmt(r['calls_per_turn'], 1)} | {fmt(r['rereads_per_100_reads'], 1)} |")
    return "\n".join(lines) + "\n"


def analyze(conds: list[str], root: Path = ew.CELLS, n_light: int | None = None) -> tuple[str, dict]:
    specs = {json.loads(line)["spec"]: json.loads(line) for line in open(ew.SESSIONS, encoding="utf-8")}
    cells = {c: EfCell(c, root) for c in conds}
    model = fit_steps(cells)
    t = {c: t_row(w) for c, w in cells.items()}
    b = {c: b_row(w) for c, w in cells.items()}
    qd = {c: q_row(w, specs) for c, w in cells.items()}
    dec = decisions(t, n_light if n_light is not None else ew.N_LIGHT)
    for r in t.values():                                    # JSON-friendly
        r.pop("blocks", None)
    return tables(t, {c: r for c, r in b.items() if (cells[c].arm or "").startswith("m_")}, qd, dec), \
        {"step_model": model, "t": t, "b": b, "q": qd, "decisions": dec}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", default="")
    ap.add_argument("--smoke", default="")
    ap.add_argument("--pilot", default="")
    ap.add_argument("--root", default=str(ew.CELLS))
    ap.add_argument("--n-light", type=int, default=None)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.root)
    if args.smoke:
        print(json.dumps(smoke(args.smoke.split(","), root), indent=1, default=str))
        return
    if args.pilot:
        out = pilot(args.pilot.split(","), root)
        text = json.dumps(out["rules"], indent=1, default=str)
        print(text)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
        return
    text, out = analyze(args.cells.split(","), root, args.n_light)
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.with_suffix(".json").write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
