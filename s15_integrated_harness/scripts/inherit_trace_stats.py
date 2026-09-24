#!/usr/bin/env python3
"""Per-teammate extraction for the inherit arms: rounds, reads, injections, re-reads.

Built for the Step 3 A/B (none vs ancestors under the 900 s cap, where wall and accuracy
are capped into uselessness): what compares is what each successor DID — model rounds,
read_file dispatches, whether injected content got re-read anyway (reacquire_warn), and
how much handoff volume actually landed (context_injection events).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def records(path: Path):
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def analyze(trace: Path):
    recs = records(trace)
    kinds = {"agent-root": "lead"}
    for r in recs:
        if r.get("event") == "agent_create":
            kinds[r.get("agent_id")] = r.get("agent_kind") or "teammate"
    rounds = defaultdict(int)
    reads = defaultdict(int)
    warns = defaultdict(int)
    for r in recs:
        aid = r.get("agent_id") or "agent-root"
        ev = r.get("event")
        if ev == "model_response" and (r["data"].get("purpose") or "teammate") == "teammate" \
                and kinds.get(aid) == "teammate":
            rounds[aid] += 1
        elif ev == "tool_end" and kinds.get(aid) == "teammate" \
                and (r["data"].get("tool") or "").startswith("read"):
            reads[aid] += 1
        elif ev == "reacquire_warn":
            warns[aid] += 1
    injections = [(r["data"].get("task_id"), r["data"].get("pairs"), r["data"].get("chars"))
                  for r in recs if r.get("event") == "context_injection"]
    teammates = [a for a, k in kinds.items() if k == "teammate"]
    return {
        "trace": trace.name,
        "teammates": len(teammates),
        "rounds": sum(rounds.values()),
        "reads": sum(reads.values()),
        "reacquire_warns": sum(warns.values()),
        "injections": injections,
        "injected_chars": sum(c or 0 for _, _, c in injections),
    }


def main() -> int:
    target = Path(sys.argv[1])
    print(f"| cell | arm | mates | rounds | reads | warns | injected pairs/chars |")
    print("|---|---|---|---|---|---|---|")
    for cell in sorted(target.glob("*.cell.json")):
        data = json.loads(cell.read_text(encoding="utf-8"))
        trace = Path(data["trace"])
        if not trace.exists():
            continue
        a = analyze(trace)
        inj = f"{len(a['injections'])} / {a['injected_chars']:,}"
        print(f"| {data['label']} | {data['arm']} | {a['teammates']} | {a['rounds']} "
              f"| {a['reads']} | {a['reacquire_warns']} | {inj} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
