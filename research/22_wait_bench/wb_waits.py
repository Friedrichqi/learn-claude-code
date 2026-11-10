#!/usr/bin/env python3
"""Topic 22, post hoc (not in prereg.md): does the model wait when the rule says so, and why does it say it waits?

Per `wait` run, every model call that got a reply is sorted by the KV use on the newest [server] line it saw (the
end of its request's last user message):
  - on-rule: the line shows 80 % or more; off-rule: below 80 %;
  - the share of calls with `wait` in each, and the seconds those waits asked for;
  - each wait's `reason` input: does it cite the server (KV cache, load, capacity ...) or something else (network,
    a build, a process)?
  - the trials with the most off-rule waits.

    python research/22_wait_bench/wb_waits.py               # every n*_wait run -> data/tables/wb_waits.{md,json}
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

DIR = Path(__file__).resolve().parent
JOBS = DIR / "data" / "jobs"
TABLES = DIR / "data" / "tables"
KV_RULE = 80
LINE = re.compile(r"\[server\] KV cache (\d+)% full")
CITES_SERVER = re.compile(r"server|kv|cache|load|capacity|congest|busy|memory|pressure|catch up", re.I)


def kv_seen(request: dict) -> int | None:
    """The KV % of the newest [server] line in the request's last user message, or None."""
    for m in reversed(request.get("messages", [])):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        blocks = [{"type": "text", "text": c}] if isinstance(c, str) else [b for b in c or [] if isinstance(b, dict)]
        texts = [b.get("text") or "" if b.get("type") == "text" else json.dumps(b.get("content", "")) for b in blocks]
        for text in reversed(texts):
            found = LINE.findall(text)
            if found:
                return int(found[-1])
        return None
    return None


def run_waits(name: str) -> dict:
    calls = {"on": collections.Counter(), "off": collections.Counter(), "none": collections.Counter()}
    secs = collections.Counter()
    reasons = {"on": collections.Counter(), "off": collections.Counter()}
    off_reasons = collections.Counter()
    off_trials = collections.Counter()
    for path in sorted(JOBS.glob(f"{name}/*/agent/s15/calls.jsonl")):
        trial = path.parents[2].name
        for line in path.open(errors="replace"):
            if not line.strip() or '"request"' not in line:
                continue
            r = json.loads(line)
            if r.get("ms") is None:
                continue
            kv = kv_seen(r["request"])
            side = "none" if kv is None else ("on" if kv >= KV_RULE else "off")
            uses = [b for b in r.get("response") or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
            waits = [b for b in uses if b.get("name") == "wait"]
            calls[side]["calls"] += 1
            if not waits:
                continue
            calls[side]["with_wait"] += 1
            calls[side]["wait_only"] += len(waits) == len(uses)
            if side == "none":
                continue
            secs[side] += sum(float((b.get("input") or {}).get("seconds") or 0) for b in waits)
            reason = " ".join(str((b.get("input") or {}).get("reason") or "") for b in waits).strip()
            reasons[side]["server" if CITES_SERVER.search(reason) else ("other" if reason else "no reason")] += 1
            if side == "off":
                off_reasons[reason[:80]] += 1
                off_trials[trial] += 1
    share = {s: (calls[s]["with_wait"] / calls[s]["calls"]) if calls[s]["calls"] else None for s in ("on", "off")}
    return {"run": name, "calls": {s: dict(c) for s, c in calls.items()}, "share_with_wait": share,
            "seconds_asked": dict(secs), "reasons": {s: dict(c) for s, c in reasons.items()},
            "off_rule_reasons": off_reasons.most_common(10), "off_rule_trials": off_trials.most_common(5)}


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f} %"


def render(rows: list[dict]) -> str:
    out = ["# Topic 22: waits by the KV use the model saw (wb_waits.py, post hoc)", "",
           "on-rule: the newest [server] line showed 80 % or more; off-rule: below 80 %. A reason cites the server "
           "when it names the KV cache, load, capacity and the like.", "",
           "| run | on-rule calls | with wait | wait-only | off-rule calls | with wait | wait-only | s asked on / off "
           "| on-rule reasons: server / other / none | off-rule reasons: server / other / none |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        on, off = r["calls"].get("on", {}), r["calls"].get("off", {})
        ro, rf = r["reasons"].get("on", {}), r["reasons"].get("off", {})
        out.append(f"| `{r['run']}` | {on.get('calls', 0)} | {pct(r['share_with_wait']['on'])} | {on.get('wait_only', 0)} "
                   f"| {off.get('calls', 0)} | {pct(r['share_with_wait']['off'])} | {off.get('wait_only', 0)} "
                   f"| {r['seconds_asked'].get('on', 0):.0f} / {r['seconds_asked'].get('off', 0):.0f} "
                   f"| {ro.get('server', 0)} / {ro.get('other', 0)} / {ro.get('no reason', 0)} "
                   f"| {rf.get('server', 0)} / {rf.get('other', 0)} / {rf.get('no reason', 0)} |")
    for r in rows:
        out += ["", f"**`{r['run']}` off-rule:** trials {', '.join(f'`{t}` ({n})' for t, n in r['off_rule_trials']) or '-'}; "
                f"reasons {'; '.join(f'\"{s}\" ({n})' for s, n in r['off_rule_reasons'][:6]) or '-'}"]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="*", help="default: every n*_wait job")
    ap.add_argument("--out", default="wb_waits")
    a = ap.parse_args()
    names = a.runs or sorted(p.name for p in JOBS.glob("n*_wait") if p.is_dir())
    rows = [run_waits(n) for n in names]
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / f"{a.out}.json").write_text(json.dumps(rows, indent=1) + "\n")
    (TABLES / f"{a.out}.md").write_text(render(rows))
    print((TABLES / f"{a.out}.md").read_text())


if __name__ == "__main__":
    main()
