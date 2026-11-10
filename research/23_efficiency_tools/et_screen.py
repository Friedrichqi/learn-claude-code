#!/usr/bin/env python3
"""Topic 23, S2: the prompt screens on Qwen3.8-27B, before the main runs (screen_prereg.md fixes the rules).

Agent screens (topic 19's replay method, et_screen's own port to Qwen and the Anthropic endpoint s15 uses): recorded
lead calls of the `wait` pilot (pilot_wait) are re-sent with their newest [server] line at KV 0.62 or 0.93 (the earlier
lines as recorded; amended 2026-11-02, see rewrite_lines), and the model's replies are sampled 4 times per prompt:
  B0       the request as recorded (the baseline for `kept`);
  W        wait text v5 as recorded, lines rewritten (does Qwen follow the final wait prompt?);
  S1..S4   W plus save_context with description s1..s4 (hold_runtime.SAVE_TEXTS), lead-only as in the runs.
Controller screen: one-minute snapshots of the `none` pilot and its K sweep (pilot_none: et_hw.py records, the gate's
state) are rendered with each controller variant c1..c4 (et_prompts.py) at the default and low reasoning effort and
sent as the controller sends them (et_controller.py), 3 samples each; the chosen K's direction (lower / keep / raise)
is compared with the reference rule's (`label`).

    python research/23_efficiency_tools/et_screen.py items          # data/screen/items.json
    python research/23_efficiency_tools/et_screen.py snapshots      # data/screen/snapshots.json
    python research/23_efficiency_tools/et_screen.py serve --probe agent --slot a --hours 2.5
    python research/23_efficiency_tools/et_screen.py serve --probe controller --slot b --hours 2.0
    python research/23_efficiency_tools/et_screen.py analyze        # data/tables/screen.{md,json}, data/screen/winners.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import hashlib
import importlib.util
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
import et_hw  # noqa: E402
import et_prompts  # noqa: E402

DATA = DIR / "data"
SCREEN = DATA / "screen"
JOBS = DATA / "jobs"
RUNS = DATA / "runs"
MODEL = "Qwen/Qwen3.8-27B-FP8"
SERVER_RE = re.compile(r"\[server\] KV cache (\d+)% full; (\d+) requests waiting"
                       r"(?:\. At \d+% or more: add a wait call to your response\.)?")
LEVELS = (0.62, 0.93)
SAVE_VARIANTS = ("s1", "s2", "s3", "s4")
# the revised save_context screen (2026-11-02, the user chose it after s1-s4 failed): name -> (save text, line format)
REVISED = {"S5": ("s1", "kv_cue_save"), "S6": ("s6", "kv_cue")}
SAMPLES = 4
STRATA = {"work": 42, "start": 9, "final": 9}
SEED = 2213
C_SAMPLES = 3
EFFORTS = ("default", "low")
MAX_TOKENS = 8192
IN_FLIGHT = 16
PACING = {"wait", "save_context", "wait_for_capacity", "server_status"}


def hold(env: dict):
    """The pinned hold_runtime under `env` (its CONFIG is read at load)."""
    path = DIR / "vendor" / "s15" / "s15_integrated_harness" / "hold_runtime.py"
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        name = "hold_runtime_screen_" + hashlib.sha1(json.dumps(env, sort_keys=True).encode()).hexdigest()[:8]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


WAIT_ENV = {"S15_WAIT_TOOL": "1", "S15_WAIT_TEXT": "v5", "S15_SERVER_SECTION": "wait_kv", "S15_STATUS_LINE": "1",
            "S15_STATUS_FORMAT": "kv_cue", "S15_WAIT_KV": "0.8"}


def rewrite_lines(messages: list, level: float, h) -> tuple[list, int]:
    """The messages with the newest [server] line at `level` (its count of waiting requests kept); the earlier lines
    stay as recorded. (Amended 2026-11-02 22:06 UTC, README Changes: topic 19 capped the earlier lines at 0.75 because
    MiniMax's histories showed the rule ignored; Qwen followed it live, so capping made its histories read "the line
    said 75 % and I waited", which teaches waiting below the threshold.) The line has no characters JSON escapes, so
    the JSON text is rewritten in place. Returns the messages and the number of lines in them."""
    text = json.dumps(messages)
    found = list(SERVER_RE.finditer(text))
    if not found:
        return messages, 0
    m = found[-1]
    line = h.kv_line({"kv_usage": level, "waiting": int(m.group(2))})
    return json.loads(text[:m.start()] + line + text[m.end():]), len(found)


def conditions2(req: dict) -> dict[str, dict]:
    """The revised screen: S5 = s1 with the newest line's cue naming save_context (kv_cue_save), S6 = s6 (s1 plus a
    worked example) with the usual line. B0 and W are the first screen's."""
    out = {}
    for level in LEVELS:
        tag = f"{round(100 * level)}"
        for name, (text, fmt) in REVISED.items():
            msgs, n = rewrite_lines(req["messages"], level, hold({**WAIT_ENV, "S15_STATUS_FORMAT": fmt}))
            st = add_save(req["system"], req["tools"], text) if n else None
            if st:
                out[f"{name}@{tag}"] = {"system": st[0], "messages": msgs, "tools": st[1]}
    return out


def add_save(system: str, tools: list, variant: str) -> tuple[str, list] | None:
    """The request's system prompt and tools with save_context (description `variant`) advertised as the runs do it."""
    h = hold({**WAIT_ENV, "S15_SAVE_TOOL": "1", "S15_SAVE_TEXT": variant})
    for old, new in (("connect_mcp, wait.", "connect_mcp, wait, save_context."),
                     ("Also available: wait.", "Also available: wait, save_context.")):
        if old in system:
            return system.replace(old, new, 1), list(tools) + [h.save_tool()]
    return None


# -- items ---------------------------------------------------------------------------------------------------------------
def lead_calls(trial: Path) -> list[dict]:
    rows = []
    for line in (trial / "agent" / "s15" / "calls.jsonl").open() if (trial / "agent/s15/calls.jsonl").is_file() else []:
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if c.get("purpose") == "lead" and c.get("request") and not c.get("refused") and not c.get("error"):
            rows.append(c)
    return rows


def items(run: str) -> int:
    rng = random.Random(SEED)
    pools: dict[str, list] = {k: [] for k in STRATA}
    for trial in sorted(p for p in (JOBS / run).iterdir() if (p / "agent/s15/tools.json").is_file()):
        calls = lead_calls(trial)
        calls = [c for c in calls if SERVER_RE.search(json.dumps(c["request"]["messages"]))]
        if len(calls) < 3:
            continue
        for i, c in enumerate(calls):
            stratum = "start" if i == 0 else ("final" if not (c.get("tools") or []) else "work")
            pools[stratum].append((trial.name, c["call"]))
    picked, per_session = [], {}
    for stratum, n in STRATA.items():
        pool = pools[stratum]
        rng.shuffle(pool)
        k = 0
        for sess, call in pool:
            if k >= n:
                break
            if per_session.get(sess, 0) >= 2:
                continue
            per_session[sess] = per_session.get(sess, 0) + 1
            picked.append({"id": f"{sess}#{call}", "session": sess, "call": call, "stratum": stratum})
            k += 1
    SCREEN.mkdir(parents=True, exist_ok=True)
    (SCREEN / "items.json").write_text(json.dumps({"run": run, "seed": SEED, "items": picked,
                                                   "pools": {k: len(v) for k, v in pools.items()}}, indent=1))
    print(f"[items] {len(picked)} items from {len(per_session)} sessions; pools {[(k, len(v)) for k, v in pools.items()]}")
    return 0


def load_item(run: str, item: dict) -> dict:
    trial = JOBS / run / item["session"]
    call = next(c for c in lead_calls(trial) if c["call"] == item["call"])
    tools = json.loads((trial / "agent/s15/tools.json").read_text())
    return {"system": call["request"]["system"], "messages": call["request"]["messages"], "tools": tools,
            "recorded_tools": call.get("tools") or []}


def conditions(req: dict) -> dict[str, dict]:
    h = hold(WAIT_ENV)
    out = {"B0": {"system": req["system"], "messages": req["messages"], "tools": req["tools"]}}
    for level in LEVELS:
        msgs, n = rewrite_lines(req["messages"], level, h)
        if not n:
            continue
        tag = f"{round(100 * level)}"
        out[f"W@{tag}"] = {"system": req["system"], "messages": msgs, "tools": req["tools"]}
        for v in SAVE_VARIANTS:
            st = add_save(req["system"], req["tools"], v)
            if st:
                out[f"{v.upper()}@{tag}"] = {"system": st[0], "messages": msgs, "tools": st[1]}
    return out


# -- snapshots -----------------------------------------------------------------------------------------------------------
def label(now: dict, gate: dict) -> str:
    """The reference direction (topics 11 and 18: keep the active sessions' contexts within the KV pool)."""
    cap = now.get("kv_capacity_tokens") or 0
    act = gate.get("active_context_tokens") or 0
    nxt = gate.get("next_waiting_context_tokens") or 0
    hit = now.get("gpu_hit_rate")
    pressed = (now.get("preemptions_per_min") or 0) > 0 or (now.get("waiting_capacity_mean") or 0) > 0.5
    if cap and (act > 0.9 * cap or (hit is not None and hit < 0.5 and pressed)):
        return "lower"
    if cap and gate.get("waiting", 0) > 0 and act + nxt < 0.8 * cap:
        return "raise"
    return "keep"


def snapshots(run: str) -> int:
    recs = []
    for path in sorted((RUNS / run).glob("hw_*.jsonl")):
        recs += [json.loads(l) for l in path.open() if l.strip()]
    recs = sorted((r for r in recs if r.get("ok")), key=lambda r: r["wall"])
    if not recs:
        raise SystemExit(f"no monitor records in {RUNS / run}")
    import bisect
    out, t = [], recs[0]["wall"] + 120
    walls = [r["wall"] for r in recs]
    pick = lambda lo, hi: recs[bisect.bisect_left(walls, lo):bisect.bisect_right(walls, hi)]
    while t <= recs[-1]["wall"]:
        now = et_hw.summarize(pick(t - 60, t), CAPACITY)
        gate = pick(t - 2, t)[-1].get("gate") if pick(t - 2, t) else None
        if now and gate:
            minutes = [et_hw.summarize(pick(t - 60 - 60 * i, t - 60 * i), CAPACITY) for i in range(5, 0, -1)]
            out.append({"t": t, "summary": {"window_s": 60, "now": now, "minutes": [m for m in minutes if m]},
                        "gate": gate, "label": label(now, gate)})
        t += 60
    SCREEN.mkdir(parents=True, exist_ok=True)
    (SCREEN / "snapshots.json").write_text(json.dumps({"run": run, "snapshots": out}, indent=1))
    labels = [s["label"] for s in out]
    print(f"[snapshots] {len(out)}: " + ", ".join(f"{k} {labels.count(k)}" for k in ("lower", "keep", "raise")))
    return 0


CAPACITY: int | None = None


# -- sending -------------------------------------------------------------------------------------------------------------
def post(url: str, body: dict, timeout: float = 900.0) -> tuple[float, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return time.monotonic() - t0, json.loads(r.read())


def agent_reply(r: dict) -> dict:
    blocks = r.get("content") or []
    tools = [b.get("name") for b in blocks if b.get("type") == "tool_use"]
    thinking = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
    return {"tools": tools, "text": any(b.get("type") == "text" and b.get("text", "").strip() for b in blocks),
            "stop": r.get("stop_reason"), "usage": r.get("usage"), "thinking": thinking[:600]}


def run_agent(url: str, probe: str = "agent") -> int:
    meta = json.loads((SCREEN / "items.json").read_text())
    out = SCREEN / probe / "records.jsonl"
    build = conditions2 if probe == "agent2" else conditions
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["item"], r["cond"], r["sample"]) for r in (json.loads(l) for l in out.open())} if out.exists() else set()
    jobs = []
    for it in meta["items"]:
        req = load_item(meta["run"], it)
        for cond, body in build(req).items():
            for s in range(SAMPLES):
                if (it["id"], cond, s) not in done:
                    jobs.append((it, cond, s, body, req["recorded_tools"]))
    print(f"[agent] {len(jobs)} requests to send ({len(done)} done)", flush=True)

    def one(job):
        it, cond, s, body, recorded = job
        try:
            dt, r = post(f"{url}/v1/messages", {"model": MODEL, **body, "max_tokens": MAX_TOKENS})
            rec = {"item": it["id"], "stratum": it["stratum"], "session": it["session"], "cond": cond, "sample": s,
                   "s": round(dt, 2), "recorded": recorded, **agent_reply(r)}
        except Exception as exc:                                  # noqa: BLE001
            rec = {"item": it["id"], "stratum": it["stratum"], "session": it["session"], "cond": cond, "sample": s,
                   "error": f"{type(exc).__name__}: {exc}"[:300]}
        return rec
    with out.open("a") as f, cf.ThreadPoolExecutor(IN_FLIGHT) as ex:
        for rec in ex.map(one, jobs):
            f.write(json.dumps(rec) + "\n")
            f.flush()
    return 0


def run_controller(url: str) -> int:
    snaps = json.loads((SCREEN / "snapshots.json").read_text())["snapshots"]
    out = SCREEN / "controller" / "records.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(r["t"], r["variant"], r["effort"], r["sample"]) for r in (json.loads(l) for l in out.open())} \
        if out.exists() else set()
    import et_controller
    jobs = [(sn, v, e, s) for sn in snaps for v in et_prompts.VARIANTS for e in EFFORTS for s in range(C_SAMPLES)
            if (sn["t"], v, e, s) not in done]
    print(f"[controller] {len(jobs)} requests to send ({len(done)} done)", flush=True)

    def one(job):
        sn, v, e, s = job
        hist = [{"old": m.get("k"), "new": m.get("k"), "reason": "", "after": m} for m in sn["summary"]["minutes"]]
        msgs = et_prompts.messages(sn["summary"], sn["gate"], hist, v, 32)
        body = {"model": MODEL, "messages": msgs, "tools": [et_prompts.tool(32)], "tool_choice": "required",
                "max_tokens": MAX_TOKENS}
        if e != "default":
            body["chat_template_kwargs"] = {"reasoning_effort": e}
        rec = {"t": sn["t"], "variant": v, "effort": e, "sample": s, "label": sn["label"], "k_now": sn["gate"].get("k")}
        try:
            dt, r = post(f"{url}/v1/chat/completions", body)
            k, why = et_controller.parse_call(r, 32)
            rec.update(s_=round(dt, 2), k=k, why=why[:300], usage=r.get("usage"))
        except Exception as exc:                                  # noqa: BLE001
            rec.update(k=None, error=f"{type(exc).__name__}: {exc}"[:300])
        return rec
    with out.open("a") as f, cf.ThreadPoolExecutor(IN_FLIGHT) as ex:
        for rec in ex.map(one, jobs):
            f.write(json.dumps(rec) + "\n")
            f.flush()
    return 0


# -- analysis ------------------------------------------------------------------------------------------------------------
def boot(groups: dict[str, list[float]], seed: str, draws: int = 2000) -> list[float | None]:
    keys = sorted(groups)
    if not keys:
        return [None, None]
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        pick = [v for k in (rng.choice(keys) for _ in keys) for v in groups[k]]
        vals.append(sum(pick) / len(pick) if pick else 0.0)
    vals.sort()
    return [vals[int(0.025 * draws)], vals[int(0.975 * draws) - 1]]


def item_rate(rows: list[dict], test) -> tuple[float | None, list[float] | None, dict]:
    """Mean over items of each item's share of samples where test(row); 95 % session-cluster bootstrap."""
    per_item: dict[str, list] = {}
    sess: dict[str, str] = {}
    for r in rows:
        if r.get("error"):
            continue
        per_item.setdefault(r["item"], []).append(1.0 if test(r) else 0.0)
        sess[r["item"]] = r["session"]
    means = {i: sum(v) / len(v) for i, v in per_item.items()}
    if not means:
        return None, None, {}
    groups: dict[str, list[float]] = {}
    for i, m in means.items():
        groups.setdefault(sess[i], []).append(m)
    return sum(means.values()) / len(means), boot(groups, json.dumps(sorted(means))[:64]), means


def analyze() -> int:
    rules = json.loads((DIR / "screen_rules.json").read_text())
    out = {"agent": {}, "controller": {}}
    lines = ["# Topic 23 prompt screens (et_screen.py; rules: screen_prereg.md, screen_rules.json)", ""]
    rows = [json.loads(l) for probe in ("agent", "agent2") if (SCREEN / probe / "records.jsonl").exists()
            for l in (SCREEN / probe / "records.jsonl").open()]
    if rows:
        work = [r for r in rows if r["stratum"] == "work"]
        has = lambda name: (lambda r: name in (r.get("tools") or []))
        kept = lambda r: bool(set(r.get("tools") or []) - PACING & set(r.get("recorded") or [])) or (
            not (set(r.get("tools") or []) - PACING) and not (set(r.get("recorded") or []) - PACING))
        conds = sorted({r["cond"] for r in rows})
        lines += ["## Agent replies (work stratum; item means, 95 % session-cluster bootstrap)", "",
                  "| condition | items | wait | save_context | save with wait | wait-only | kept |",
                  "|---|---|---|---|---|---|---|"]
        for c in conds:
            rs = [r for r in work if r["cond"] == c]
            m = {"wait": item_rate(rs, has("wait")), "save": item_rate(rs, has("save_context")),
                 "save_wait": item_rate(rs, lambda r: {"wait", "save_context"} <= set(r.get("tools") or [])),
                 "wait_only": item_rate(rs, lambda r: bool(r.get("tools")) and set(r["tools"]) <= PACING),
                 "kept": item_rate(rs, kept)}
            out["agent"][c] = {k: {"mean": v[0], "ci": v[1]} for k, v in m.items()}
            cell = lambda k: "–" if m[k][0] is None else f"{100 * m[k][0]:.0f} % [{100 * m[k][1][0]:.0f}, {100 * m[k][1][1]:.0f}]"
            lines.append(f"| {c} | {len(m['wait'][2])} | {cell('wait')} | {cell('save')} | {cell('save_wait')} | "
                         f"{cell('wait_only')} | {cell('kept')} |")
        lines.append("")
    rec_path = SCREEN / "controller" / "records.jsonl"
    if rec_path.exists():
        rows = [json.loads(l) for l in rec_path.open()]
        lines += ["## Controller (direction of the chosen K against the reference rule)", "",
                  "| variant | effort | calls | valid | agree | agree when lower | agree when raise | agree when keep |",
                  "|---|---|---|---|---|---|---|---|"]
        direction = lambda r: None if r.get("k") is None else ("lower" if r["k"] < r["k_now"] else
                                                               ("raise" if r["k"] > r["k_now"] else "keep"))
        for v in et_prompts.VARIANTS:
            for e in EFFORTS:
                rs = [r for r in rows if r["variant"] == v and r["effort"] == e]
                if not rs:
                    continue
                valid = [r for r in rs if r.get("k") is not None]
                agree = lambda sub: (sum(direction(r) == r["label"] for r in sub) / len(sub)) if sub else None
                by = {lab: agree([r for r in valid if r["label"] == lab]) for lab in ("lower", "raise", "keep")}
                res = {"calls": len(rs), "valid": len(valid) / len(rs), "agree": agree(valid) if valid else None, **by}
                out["controller"][f"{v}|{e}"] = res
                pct = lambda x: "–" if x is None else f"{100 * x:.0f} %"
                lines.append(f"| {v} | {e} | {len(rs)} | {pct(res['valid'])} | {pct(res['agree'])} | "
                             f"{pct(by['lower'])} | {pct(by['raise'])} | {pct(by['keep'])} |")
        lines.append("")
    winners = pick_winners(out, rules)
    out["winners"] = winners
    lines += ["## Winners by the pre-registered rules", "", "```", json.dumps(winners, indent=1), "```"]
    (DATA / "tables").mkdir(parents=True, exist_ok=True)
    (DATA / "tables" / "screen.json").write_text(json.dumps(out, indent=1) + "\n")
    (DATA / "tables" / "screen.md").write_text("\n".join(lines) + "\n")
    (SCREEN / "winners.json").write_text(json.dumps(winners, indent=1) + "\n")
    print("\n".join(lines))
    return 0


def pick_winners(out: dict, rules: dict) -> dict:
    a, c = out["agent"], out["controller"]
    res = {}
    ok = lambda x: x is not None
    kept0 = (a.get("B0") or {}).get("kept", {}).get("mean")
    w_hi, w_lo = (a.get("W@93") or {}), (a.get("W@62") or {})
    if w_hi and w_lo:
        hi, lo = w_hi["wait"]["mean"], w_lo["wait"]["mean"]
        kept_ok = kept0 is None or (w_hi["kept"]["mean"] is not None and kept0 - w_hi["kept"]["mean"] <= rules["kept_drop"])
        res["wait_v5"] = {"high": hi, "low": lo, "passes": ok(hi) and ok(lo) and hi >= rules["high"]
                          and lo <= rules["low"] and kept_ok}
    cands = []
    for v in (*SAVE_VARIANTS, *(k.lower() for k in REVISED)):
        hi_c, lo_c = a.get(f"{v.upper()}@93"), a.get(f"{v.upper()}@62")
        if not hi_c or not lo_c:
            continue
        hi, lo = hi_c["save_wait"]["mean"], lo_c["save"]["mean"]
        more_wait_only = (hi_c["wait_only"]["mean"] or 0) - ((w_hi.get("wait_only") or {}).get("mean") or 0)
        passes = ok(hi) and ok(lo) and hi >= rules["high"] and lo <= rules["low"] and more_wait_only <= rules["kept_drop"]
        res[f"save_{v}"] = {"save_with_wait_high": hi, "save_low": lo, "wait_only_rise": more_wait_only,
                            "passes": passes}
        if passes:
            cands.append((-hi, len(v), v))
    res["save_winner"] = sorted(cands)[0][2] if cands else None
    ccands = []
    for key, r in c.items():
        v, e = key.split("|")
        passes = r["valid"] >= rules["controller_valid"] and (r["agree"] or 0) >= rules["controller_agree"]
        res[f"controller_{key}"] = {**r, "passes": passes}
        if passes:
            ccands.append((-(r["agree"] or 0), et_prompts.VARIANTS.index(v), EFFORTS.index(e) * -1, key))
    res["controller_winner"] = sorted(ccands)[0][3] if ccands else None
    return res


# -- the GPU stage -------------------------------------------------------------------------------------------------------
def serve(probe: str, slot: str, hours: float) -> int:
    import et_cell
    import rd_serve as rs_
    name = f"screen_{probe}"
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    et_cell.configure("none", slot, run_dir)
    cmd = [et_cell.PY, str(Path(__file__).resolve()), "run", "--probe", probe, "--url", "{url}"]
    import threading
    stop = threading.Event()
    threading.Thread(target=et_cell.pin_roles, args=(os.getpid(), et_cell.wb_cell.cpu_list(et_cell.SLOTS[slot]["server_cpus"]),
                                                     et_cell.wb_cell.cpu_list(et_cell.CLIENT_CPUS), run_dir / "pin.jsonl",
                                                     stop), daemon=True).start()
    rc = rs_.run_stage(name, hours, "et", cmd, claim_check=False)
    stop.set()
    return rc


def main():
    global CAPACITY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("items")
    p.add_argument("--run", default="pilot_wait")
    p = sub.add_parser("snapshots")
    p.add_argument("--run", default="pilot_none")
    p.add_argument("--kv-tokens", type=int, required=True, help="the server's KV capacity (its log: GPU KV cache size)")
    p = sub.add_parser("run")
    p.add_argument("--probe", choices=("agent", "agent2", "controller"), required=True)
    p.add_argument("--url", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--probe", choices=("agent", "agent2", "controller"), required=True)
    p.add_argument("--slot", choices=("a", "b"), required=True)
    p.add_argument("--hours", type=float, required=True)
    sub.add_parser("analyze")
    a = ap.parse_args()
    if a.cmd == "items":
        sys.exit(items(a.run))
    if a.cmd == "snapshots":
        CAPACITY = a.kv_tokens
        sys.exit(snapshots(a.run))
    if a.cmd == "run":
        sys.exit(run_controller(a.url.rstrip("/")) if a.probe == "controller" else run_agent(a.url.rstrip("/"), a.probe))
    if a.cmd == "serve":
        sys.exit(serve(a.probe, a.slot, a.hours))
    sys.exit(analyze())


if __name__ == "__main__":
    main()
