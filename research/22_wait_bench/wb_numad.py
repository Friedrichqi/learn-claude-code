#!/usr/bin/env python3
"""Topic 22, post hoc (not in prereg.md): how often did the host's numad daemon move each run's vLLM processes?

numad (root, `numad -i 15`) rebinds any process over 300 MB and half a CPU to one NUMA node's whole CPU list and
migrates its memory there. Per run, from numad's log lines about `VLLM::` processes:
  - moves within the run (client start to end), and how many went to a node other than 0;
  - per process, the share of the run its memory was on a node other than 0 (from each move until the next; every
    process starts on node 0, where rd_serve's taskset and wb_vllm.sh's numactl put it). wb_cell.pin_tree resets the
    CPUs within 2 s, so for N=8 and N=4 this is memory only.

    python research/22_wait_bench/wb_numad.py --copy   # first: today's lines of /var/log/numad.log -> data/runs/numad.log
    python research/22_wait_bench/wb_numad.py          # -> data/tables/wb_numad.{md,json}
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
from pathlib import Path

DIR = Path(__file__).resolve().parent
RUNS = DIR / "data" / "runs"
TABLES = DIR / "data" / "tables"
LOG = RUNS / "numad.log"
LINE = re.compile(r"^(\w{3} \w{3} +\d+ \d\d:\d\d:\d\d \d{4}): (.*)$")
ADVISE = re.compile(r"Advising pid (\d+) \(([^)]*)\) move from nodes \(([^)]*)\) to nodes \(([^)]*)\)")
MOVED = re.compile(r"PID (\d+) moved to node\(s\) (\S+) in")
LEAD = 300    # s before the client starts: the server's own start (about 80 s) and numad's first moves


def events(path: Path) -> list[tuple[float, int, str, str]]:
    """[(t, pid, node list, name)] for every finished move; the name is the pid's at its latest advice (pids are
    reused within hours here). The log's times are UTC."""
    moves, names = [], {}
    for raw in path.open(errors="replace"):
        m = LINE.match(raw.rstrip())
        if not m:
            continue
        t = dt.datetime.strptime(m[1], "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc).timestamp()
        if a := ADVISE.match(m[2]):
            names[int(a[1])] = a[2]
        elif b := MOVED.match(m[2]):
            moves.append((t, int(b[1]), b[2], names.get(int(b[1]), "")))
    return [mv for mv in moves if mv[3].startswith("VLLM")]


def windows() -> dict[str, tuple[float, float]]:
    out = {}
    for p in sorted(RUNS.glob("*/cell.jsonl")):
        for r in (json.loads(x) for x in p.open() if x.strip()):
            if r.get("t_client") and r.get("t_end") and not p.parent.name.startswith("dry"):
                out[p.parent.name] = (r["t_client"], r["t_end"])
    return dict(sorted(out.items(), key=lambda kv: kv[1][0]))


def owners(moves: list, wins: dict) -> dict[int, str]:
    """Each vLLM pid -> its run: the earliest run whose [client start - LEAD, end] holds the pid's first move."""
    first = {}
    for t, pid, _, _ in moves:
        first.setdefault(pid, t)
    return {pid: next((name for name, (t0, t1) in wins.items() if t0 - LEAD <= t <= t1), None)
            for pid, t in first.items()}


def run_moves(name: str, t0: float, t1: float, moves: list, owner: dict) -> dict:
    mine = [(t, pid, n, who) for t, pid, n, who in moves if owner.get(pid) == name and t <= t1]
    node, since, away, label = collections.defaultdict(lambda: "0"), {}, collections.Counter(), {}
    for t, pid, n, who in mine:
        if node[pid] != "0":
            away[pid] += max(0.0, min(t, t1) - max(since[pid], t0))
        node[pid], since[pid], label[pid] = n, t, who
    for pid in node:
        if node[pid] != "0":
            away[pid] += max(0.0, t1 - max(since[pid], t0))
    inside = [(t, n) for t, _, n, _ in mine if t >= t0]
    return {"moves": len(inside), "to_other_node": sum(n != "0" for _, n in inside),
            "minutes_with_moves": len({int((t - t0) // 60) for t, _ in inside}),
            "share_off_node0": {f"{label[pid]} {pid}": away[pid] / (t1 - t0) for pid in sorted(node)}}


def render(rows: dict) -> str:
    out = ["# Topic 22: numad's moves of each run's vLLM processes (wb_numad.py, post hoc)", "",
           "moves: numad rebinding a `VLLM::` process (its CPUs and memory) between the run's client start and end. "
           "share off node 0: the part of the run each process's memory was on another node (EngineCore, then the "
           "4 workers).", "",
           "| run | moves | to another node | minutes with a move | share off node 0 per process |",
           "|---|---|---|---|---|"]
    for name, r in rows.items():
        shares = " ".join(f"{100 * s:.0f} %" for s in r["share_off_node0"].values()) or "-"
        out.append(f"| `{name}` | {r['moves']} | {r['to_other_node']} | {r['minutes_with_moves']} | {shares} |")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--copy", action="store_true", help="append /var/log/numad.log's new lines since 2026-11-01")
    ap.add_argument("--out", default="wb_numad")
    a = ap.parse_args()
    if a.copy:
        have = LOG.read_text(errors="replace").splitlines() if LOG.exists() else []
        seen = set(have)    # every line carries its second, so a repeat is the same event
        new = [raw for raw in (x.rstrip("\n") for x in Path("/var/log/numad.log").open(errors="replace"))
               if raw.startswith(("Sun Nov  1", "Mon Nov  2")) and "Could not open stat" not in raw and raw not in seen]
        LOG.write_text("\n".join(have + new) + "\n")
        print(f"{LOG}: {len(have)} + {len(new)} lines")
        return
    moves, wins = events(LOG), windows()
    owner = owners(moves, wins)
    rows = {name: run_moves(name, t0, t1, moves, owner) for name, (t0, t1) in wins.items()}
    TABLES.mkdir(parents=True, exist_ok=True)
    (TABLES / f"{a.out}.json").write_text(json.dumps(rows, indent=1) + "\n")
    (TABLES / f"{a.out}.md").write_text(render(rows))
    print((TABLES / f"{a.out}.md").read_text())


if __name__ == "__main__":
    main()
