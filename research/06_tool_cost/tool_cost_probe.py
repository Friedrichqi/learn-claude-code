#!/usr/bin/env python3
"""Does advertised hardware/latency cost in a tool's description change which tool the model calls?

    python3 research/06_tool_cost/tool_cost_probe.py --probe all --reps 40 \
        --out research/06_tool_cost/data/tool_cost/stage1.jsonl

Question.  The s15 latency work (`research/05_latency_breakdown/README.md` Part A)
prices every boundary the harness crosses: 0.038 ms per uncached prompt token, 17 ms per output
token, 3.7 s fixed per agent call, 13-21 s per turn-end memory extraction.  None of that price is
visible to the model that decides which tool to call: the harness tells it WHAT a tool does and
never WHAT IT COSTS.  This probe asks whether making the cost visible -- in the tool description or
in the system prompt -- changes the tool_use blocks the model generates, and how big an advertised
cost has to be before it does.

Three single-shot probes, one model call per trial, no tool execution:

  A  discrimination.  Two channels that return byte-identical file contents and differ ONLY in
     advertised cost.  Which one does the model call?  `none` measures the residual name/position
     bias; the three framings (seconds / hardware / qualitative) all describe the same ~125x ratio,
     so a framing difference is a difference in how the cost is EXPRESSED, not in what it is.
     Crossed with placement: the cost sits in each tool's description, or in a cost table in the
     system prompt, or in both.
  B  dose-response.  Same two channels, seconds framing in the description, cheap channel pinned at
     0.04 s and the expensive one swept 0.04 -> 300 s.  Where is the threshold?
  C  substitution.  A realistic pool (read_file, grep, glob, bash, recall_summary) and a real
     question about a real repository file.  read_file is annotated at 0 / 0.05 / 5 / 60 s; the
     placebo arm annotates grep instead, so a drop in read_file can be told apart from a general
     "something in this prompt is expensive" effect.  Here the cheap routes are LOSSY (grep returns
     matching lines, recall_summary returns a possibly stale summary), so this probe also shows
     whether advertised cost pushes the model into shortcuts that can cost accuracy.

Counterbalancing.  Every probe-A/B trial randomises, in balanced blocks of four, which concrete
tool name carries the expensive label (swap) and the order of the two tools in the `tools` array
(order), because both name priors and list position move tool choice on their own.

Output: one JSONL record per trial (factors + chosen tools + usage + wall time).  Analyse with
research/06_tool_cost/tool_cost_analyze.py.

NB the SDK retries twice by default; retries are disabled here and 429/5xx are retried explicitly
with jitter so a retry can never be mistaken for an extra trial.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Both tiers describe the same working set; the hardware framing is calibrated so that a reader who
# does the division arrives at the numbers the seconds framing states outright.
FAST_S, SLOW_S = 0.04, 5.00
WORKING_SET_GIB = 128
PCIE_GBPS = 25          # 128 GiB / 25 GB/s  = 5.5 s
HBM_TBPS = 3.35         # 128 GiB / 3.35 TB/s = 0.041 s

MENTION = re.compile(
    r"\b(latenc|slow|slower|fast|faster|cheap|expensive|cost|costly|millisecond|\bms\b|second|"
    r"pcie|hbm|dram|bandwidth|transfer|stag(?:e|ing)|resident|budget|overhead)", re.I)


# ---------------------------------------------------------------- cost text

def cost_text(framing: str, tier: str, seconds: float | None = None) -> str:
    """The cost annotation for one tier, in one framing.  '' means no annotation."""
    if framing == "none":
        return ""
    if framing == "seconds":
        value = seconds if seconds is not None else (FAST_S if tier == "fast" else SLOW_S)
        return f"Measured latency: {value:.2f} s per call."
    if framing == "hardware":
        if tier == "fast":
            return (f"The {WORKING_SET_GIB} GiB working set for this channel is already resident in "
                    f"HBM3 on the accelerator ({HBM_TBPS} TB/s); no host transfer is performed.")
        return (f"The {WORKING_SET_GIB} GiB working set for this channel is held in host DRAM and is "
                f"staged across PCIe 4.0 x16 ({PCIE_GBPS} GB/s effective) into HBM3 on every call.")
    if framing == "qualitative":
        return ("This channel is fast." if tier == "fast"
                else "This channel is slow.")
    raise ValueError(f"unknown framing {framing!r}")


def join_desc(base: str, note: str) -> str:
    return f"{base} {note}".strip() if note else base


# ---------------------------------------------------------------- probe A / B

CHANNEL_BASE = ("Read a file through storage channel {ch}. Channels alpha and beta are "
                "interchangeable: both return the same bytes for the same path.")

A_TASK = ("The file /srv/data/report.txt ends with a line of the form 'TOTAL: <number>'. "
          "Use the tools to get that number, then report it.")


def build_ab(framing: str, placement: str, slow_seconds: float,
             swap: bool, order_swap: bool) -> tuple[list[dict], str]:
    """Two interchangeable read channels; `swap` decides which NAME carries the expensive label."""
    names = ["read_via_alpha", "read_via_beta"]
    tiers = ["slow", "fast"] if swap else ["fast", "slow"]
    in_desc = placement in ("tool_desc", "both")
    in_sys = placement in ("system_prompt", "both")

    tools, table = [], []
    for name, tier in zip(names, tiers):
        secs = FAST_S if tier == "fast" else slow_seconds
        note = cost_text(framing, tier, secs)
        base = CHANNEL_BASE.format(ch=name.rsplit("_", 1)[-1])
        tools.append({
            "name": name,
            "description": join_desc(base, note if in_desc else ""),
            "input_schema": {"type": "object",
                             "properties": {"path": {"type": "string"}},
                             "required": ["path"]},
        })
        if note:
            table.append(f"- {name}: {note}")

    if order_swap:
        tools.reverse()

    system = "You are a coding agent. Act, don't explain.\n\nWorking directory: /srv/data"
    if in_sys and table:
        system += ("\n\nTool cost table (measured on this host):\n"
                   + "\n".join(sorted(table)))
    return tools, system


# ---------------------------------------------------------------- probe C

C_FILE = "s15_integrated_harness/GLOSSARY.md"
C_TASK = ("In this repository, {f} defines the term \"compaction\". "
          "Tell me what the glossary says it means.").format(f=C_FILE)

C_POOL = [
    ("read_file", "Read file contents. Returns the whole file.",
     {"type": "object", "properties": {"path": {"type": "string"},
                                       "limit": {"type": "integer"},
                                       "offset": {"type": "integer"}},
      "required": ["path"]}),
    ("grep", "Search files for a regular expression. Returns only the matching lines.",
     {"type": "object", "properties": {"pattern": {"type": "string"},
                                       "path": {"type": "string"}},
      "required": ["pattern"]}),
    ("glob", "Find files matching a glob pattern; ** matches recursively.",
     {"type": "object", "properties": {"pattern": {"type": "string"}},
      "required": ["pattern"]}),
    ("bash", "Run a shell command.",
     {"type": "object", "properties": {"command": {"type": "string"}},
      "required": ["command"]}),
    ("recall_summary", "Return a cached one-paragraph summary of a file. May be stale and omits detail.",
     {"type": "object", "properties": {"path": {"type": "string"}},
      "required": ["path"]}),
]

# Cheap-route latencies used whenever ANY annotation is shown, so the expensive tool is expensive
# relative to a stated alternative rather than relative to silence.
C_BASE_COST = {"read_file": 0.05, "grep": 0.05, "glob": 0.03, "bash": 0.08, "recall_summary": 0.02}


def build_c(target: str | None, seconds: float, framing: str, placement: str) -> tuple[list[dict], str]:
    """Realistic pool; `target` is the tool whose advertised latency is raised to `seconds`."""
    in_desc = placement in ("tool_desc", "both")
    in_sys = placement in ("system_prompt", "both")
    tools, table = [], []
    for name, desc, schema in C_POOL:
        note = ""
        if target is not None:
            value = seconds if name == target else C_BASE_COST[name]
            tier = "slow" if name == target else "fast"
            note = cost_text(framing, tier, value)
        tools.append({"name": name,
                      "description": join_desc(desc, note if in_desc else ""),
                      "input_schema": schema})
        if note:
            table.append(f"- {name}: {note}")
    system = f"You are a coding agent. Act, don't explain.\n\nWorking directory: {REPO}"
    if in_sys and table:
        system += "\n\nTool cost table (measured on this host):\n" + "\n".join(table)
    return tools, system


# ---------------------------------------------------------------- probe D

# Probe C shows the model never mentions the advertised cost when the tools differ in function.
# D asks whether that is missing INFORMATION or a missing OBJECTIVE: the cost table is the same,
# and a one-line policy (and, in one cell, a hard budget) is added on top.
POLICY = ("Tool calls are not free. Prefer the cheapest tool that can still answer the question "
          "correctly.")
BUDGET = "You have a total tool-latency budget of 1.00 s for this task. Stay within it."


def build_d(target: str | None, seconds: float, framing: str,
            policy: bool, budget: bool) -> tuple[list[dict], str]:
    tools, system = build_c(target, seconds, framing, "tool_desc")
    extra = [line for line, on in ((POLICY, policy), (BUDGET, budget)) if on]
    if extra:
        system += "\n\n" + " ".join(extra)
    return tools, system


def trials_D(reps: int) -> list[dict]:
    cells = [
        (None, 0.0, "none", False, False),        # nothing said about cost at all
        ("read_file", 5.0, "seconds", False, False),   # cost table only (same cell as probe C)
        (None, 0.0, "none", True, False),          # policy only, no numbers
        ("read_file", 5.0, "seconds", True, False),    # cost table + policy
        ("read_file", 5.0, "seconds", True, True),     # cost table + policy + hard budget
    ]
    out = []
    for target, seconds, framing, policy, budget in cells:
        for rep in range(reps):
            out.append({"probe": "D", "target": target, "slow_seconds": seconds,
                        "framing": framing, "placement": "tool_desc",
                        "policy": policy, "budget": budget, "rep": rep})
    return out


# ---------------------------------------------------------------- driving

def client_and_model():
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env", override=True)
    import anthropic
    return (anthropic.Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"), max_retries=0),
            os.environ["MODEL_ID"])


_PRINT_LOCK = threading.Lock()


def call_with_retry(client, model, system, tools, task, max_tokens=1024, attempts=6):
    last = None
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=model, system=system, tools=tools, max_tokens=max_tokens,
                messages=[{"role": "user", "content": task}])
            return response, (time.perf_counter() - started), attempt
        except Exception as exc:                                   # noqa: BLE001
            last = exc
            status = getattr(exc, "status_code", None)
            if status is not None and status not in (429, 500, 502, 503, 504, 529):
                raise
            time.sleep(min(2 ** attempt, 20) * (0.6 + random.random() * 0.8))
    raise last


def summarize(response) -> dict:
    """Which tools did the model ask for, and did it talk about cost?"""
    calls, texts, thinking = [], [], []
    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "tool_use":
            calls.append({"name": block.name, "input": block.input})
        elif kind == "text":
            texts.append(block.text)
        elif kind in ("thinking", "redacted_thinking"):
            thinking.append(getattr(block, "thinking", "") or "")
    prose = "\n".join(texts + thinking)
    usage = response.usage
    return {
        "calls": calls,
        "tools_called": [c["name"] for c in calls],
        "first_tool": calls[0]["name"] if calls else None,
        "n_tool_use": len(calls),
        "stop_reason": response.stop_reason,
        "text": "\n".join(texts)[:1500],
        "thinking": "\n".join(thinking)[:1500],
        "mentions_cost": bool(MENTION.search(prose)),
        "usage": {"input_tokens": usage.input_tokens,
                  "output_tokens": usage.output_tokens,
                  "cache_read": getattr(usage, "cache_read_input_tokens", None)},
    }


def trials_A(reps: int) -> list[dict]:
    cells = [("none", "tool_desc")]
    for framing in ("seconds", "hardware", "qualitative"):
        for placement in ("tool_desc", "system_prompt", "both"):
            cells.append((framing, placement))
    out = []
    for framing, placement in cells:
        for rep in range(reps):
            out.append({"probe": "A", "framing": framing, "placement": placement,
                        "slow_seconds": SLOW_S, "rep": rep,
                        "swap": bool(rep % 2), "order_swap": bool((rep // 2) % 2)})
    return out


def trials_B(reps: int) -> list[dict]:
    out = []
    for slow in (0.04, 0.2, 1.0, 5.0, 30.0, 300.0):
        for rep in range(reps):
            out.append({"probe": "B", "framing": "seconds", "placement": "tool_desc",
                        "slow_seconds": slow, "rep": rep,
                        "swap": bool(rep % 2), "order_swap": bool((rep // 2) % 2)})
    return out


def trials_C(reps: int) -> list[dict]:
    cells = [(None, 0.0, "none"), ("read_file", 0.05, "seconds"), ("read_file", 5.0, "seconds"),
             ("read_file", 60.0, "seconds"), ("grep", 5.0, "seconds"),
             ("read_file", 5.0, "hardware"), ("read_file", 5.0, "qualitative")]
    out = []
    for target, seconds, framing in cells:
        for rep in range(reps):
            out.append({"probe": "C", "target": target, "slow_seconds": seconds,
                        "framing": framing, "placement": "tool_desc", "rep": rep})
    return out


def cell_key(spec: dict) -> tuple:
    """Identity of one trial: its cell plus its rep index (which fixes the counterbalancing)."""
    return (spec["probe"], spec.get("framing"), spec.get("placement"),
            spec.get("slow_seconds"), spec.get("target"), spec.get("policy"),
            spec.get("budget"), spec["rep"])


def run_trial(spec: dict, client, model) -> dict:
    if spec["probe"] in ("A", "B"):
        tools, system = build_ab(spec["framing"], spec["placement"], spec["slow_seconds"],
                                 spec["swap"], spec["order_swap"])
        task = A_TASK
        names = ["read_via_alpha", "read_via_beta"]
        tiers = ["slow", "fast"] if spec["swap"] else ["fast", "slow"]
        tier_of = dict(zip(names, tiers))
    elif spec["probe"] == "D":
        tools, system = build_d(spec["target"], spec["slow_seconds"], spec["framing"],
                                spec["policy"], spec["budget"])
        task = C_TASK
        tier_of = {}
    else:
        tools, system = build_c(spec["target"], spec["slow_seconds"],
                                spec["framing"], spec["placement"])
        task = C_TASK
        tier_of = {}

    response, wall, retries = call_with_retry(client, model, system, tools, task)
    record = dict(spec)
    record.update(summarize(response))
    record.update({"wall_s": round(wall, 3), "retries": retries,
                   "system_chars": len(system),
                   "tool_desc_chars": sum(len(t["description"]) for t in tools)})
    if tier_of:
        first = record["first_tool"]
        record["chose"] = tier_of.get(first, "none") if first else "none"
        record["cheap_name"] = names[0] if not spec["swap"] else names[1]
        record["tool_order"] = [t["name"] for t in tools]
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", default="all", choices=["A", "B", "C", "D", "all"])
    parser.add_argument("--reps", type=int, default=40)
    parser.add_argument("--out", default=str(REPO / "research" / "06_tool_cost" / "data"
                                             / "tool_cost" / "stage1.jsonl"))
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true",
                        help="print one rendered prompt per cell and exit")
    parser.add_argument("--topup", action="store_true",
                        help="append to --out instead of overwriting, and run only the (cell, rep) "
                             "trials that have no successful record yet, so cells stay balanced "
                             "after provider rate limiting drops trials")
    args = parser.parse_args()

    specs: list[dict] = []
    if args.probe in ("A", "all"):
        specs += trials_A(args.reps)
    if args.probe in ("B", "all"):
        specs += trials_B(args.reps)
    if args.probe in ("C", "all"):
        specs += trials_C(args.reps)
    if args.probe in ("D", "all"):
        specs += trials_D(args.reps)

    if args.dry_run:
        seen = set()
        for spec in specs:
            key = (spec["probe"], spec.get("framing"), spec.get("placement"),
                   spec.get("slow_seconds"), spec.get("target"), spec.get("policy"),
                   spec.get("budget"))
            if key in seen:
                continue
            seen.add(key)
            if spec["probe"] in ("A", "B"):
                tools, system = build_ab(spec["framing"], spec["placement"],
                                         spec["slow_seconds"], spec["swap"], spec["order_swap"])
            elif spec["probe"] == "D":
                tools, system = build_d(spec["target"], spec["slow_seconds"], spec["framing"],
                                        spec["policy"], spec["budget"])
            else:
                tools, system = build_c(spec["target"], spec["slow_seconds"],
                                        spec["framing"], spec["placement"])
            print("=" * 100)
            print(key)
            print("-- system --"); print(system)
            print("-- tools --")
            for tool in tools:
                print(f"  {tool['name']}: {tool['description']}")
        print(f"\n{len(specs)} trials, {len(seen)} cells")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    mode = "w"
    if args.topup and out_path.exists():
        mode = "a"
        have = set()
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("error"):
                continue
            have.add(cell_key(record))
        before = len(specs)
        specs = [spec for spec in specs if cell_key(spec) not in have]
        print(f"top-up: {before - len(specs)} trials already recorded, {len(specs)} to run")
        if not specs:
            print("nothing to top up")
            return

    client, model = client_and_model()
    random.seed(20260914)
    random.shuffle(specs)                      # spread provider drift over cells

    done = [0]
    started = time.time()
    lock = threading.Lock()
    with out_path.open(mode, encoding="utf-8") as handle:
        def work(spec):
            try:
                record = run_trial(spec, client, model)
            except Exception as exc:           # noqa: BLE001
                record = dict(spec, error=f"{type(exc).__name__}: {exc}")
            with lock:
                handle.write(json.dumps(record, default=str) + "\n")
                handle.flush()
                done[0] += 1
                if done[0] % 20 == 0 or done[0] == len(specs):
                    rate = done[0] / max(time.time() - started, 1e-9)
                    print(f"  {done[0]}/{len(specs)}  {rate:.2f} trial/s", flush=True)
            return record

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(work, specs))

    print(f"wrote {out_path} ({len(specs)} trials, {time.time() - started:.0f} s)")


if __name__ == "__main__":
    main()
