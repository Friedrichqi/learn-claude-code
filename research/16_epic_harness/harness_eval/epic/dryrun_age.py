#!/usr/bin/env python3
"""Dry-run the EPIC planner on recorded baseline trajectories to choose the age cap N of `epic128aN`.

    /data/fritz/venv/bin/python epic/dryrun_age.py --model minimax-m2.7 --bench swe [--caps 1,2,4,8,16,32]

Each baseline lead call's request (system + messages, as s15 sent it) becomes a token sequence: one block per
message, opened by MiniMax's role-start token (]~b], the planner's block delimiter) and tokenized with the
model's tokenizer from a canonical rendering of the message. The same rendering is used for a call's reply
(its `response` record), so a reply is reused verbatim in the next prompt, as with the append-only template.
For every arm the planner then runs the trial call by call (plan_call -> next_entry, with dummy slots), and
the script reports per arm the share of prompt tokens served from reused rows (S / P), computed (C / P) and
the run heads (H / P). The plan picks the smallest N whose reuse is at least half of shift's.
Token-level details inside a message differ from the real chat template, but the alignment and the age
dynamics happen at message granularity (snip removes whole messages), which is what the cap acts on.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import epic_plan as ep  # noqa: E402

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
TOKENIZER = {"minimax-m2.7": "MiniMaxAI--MiniMax-M2.7"}
ROLE_START = {"minimax-m2.7": 200019}
BOS = {"minimax-m2.7": 200034}


def canonical(role: str, content) -> str:
    if isinstance(content, str):
        return f"{role}\n{content}"
    parts = []
    for b in content or []:
        if not isinstance(b, dict):
            parts.append(str(b))
        elif b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            parts.append(f"<invoke name=\"{b.get('name')}\">{json.dumps(b.get('input'), sort_keys=True)}</invoke>")
        elif b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            parts.append(f"<response>{c}</response>")
        elif b.get("type") == "thinking":
            parts.append(f"<think>{b.get('thinking', '')}</think>")
    return f"{role}\n" + "\n".join(parts)


class Renderer:
    def __init__(self, model: str):
        from tokenizers import Tokenizer
        snap = sorted(glob.glob(f"/data/fritz/hf/hub/models--{TOKENIZER[model]}/snapshots/*/tokenizer.json"))[0]
        self.tok = Tokenizer.from_file(snap)
        self.rs, self.bos = ROLE_START[model], BOS[model]
        self.cache: dict[str, list[int]] = {}

    def block(self, text: str) -> list[int]:
        ids = self.cache.get(text)
        if ids is None:
            ids = self.cache[text] = [self.rs] + self.tok.encode(text, add_special_tokens=False).ids
        return ids

    def prompt(self, system: str, messages: list) -> list[int]:
        ids = [self.bos] + self.block(canonical("system", system))
        for m in messages:
            ids += self.block(canonical(m.get("role", "?"), m.get("content")))
        return ids + [self.rs]                      # the generation prompt opens the reply's block


def trajectories(model: str, bench: str, min_calls: int) -> list[list[dict]]:
    out = []
    for f in sorted(glob.glob(str(ROOT / "jobs" / model / bench / "q*" / "*" / "agent" / "s15" / "calls.jsonl"))):
        calls = []
        for line in open(f, errors="replace"):
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            if c.get("purpose") == "lead" and isinstance(c.get("request"), dict) and c.get("response") is not None:
                calls.append(c)
        if len(calls) >= min_calls:
            out.append(calls)
    return out


def simulate(arm: str, prompts: list[list[int]], replies: list[list[int]]) -> dict:
    entry, nxt = None, 0
    tot = defaultdict(int)
    for i, (new, gen) in enumerate(zip(prompts, replies)):
        plan = ep.plan_call(arm, entry, new, ep_im)
        c = plan.stats["C"]
        new_slots = np.arange(nxt, nxt + c, dtype=np.int64)
        nxt += c
        dec = np.arange(nxt, nxt + len(gen), dtype=np.int64)
        nxt += len(gen)
        entry = ep.next_entry(plan, new, gen, new_slots, dec, i)
        for k in ("P", "S", "C", "H", "aged_out"):
            tot[k] += int(plan.stats.get(k, 0))
        tot["calls"] += 1
        tot["edited"] += int(plan.stats["edited"])
    return dict(tot)


def main() -> None:
    global ep_im
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="minimax-m2.7")
    ap.add_argument("--bench", default="swe")
    ap.add_argument("--caps", default="1,2,4,8,16,32")
    ap.add_argument("--min-calls", type=int, default=30)
    ap.add_argument("--max-trials", type=int, default=40)
    a = ap.parse_args()
    ep_im = ROLE_START[a.model]
    r = Renderer(a.model)
    trajs = trajectories(a.model, a.bench, a.min_calls)[: a.max_trials]
    caps = [int(x) for x in a.caps.split(",")]
    arms = ["prefix", "shift", "epic128"] + [f"epic128a{n}" for n in caps]
    agg = {arm: defaultdict(int) for arm in arms}
    for calls in trajs:
        prompts = [r.prompt(c["request"].get("system") or "", c["request"].get("messages") or []) for c in calls]
        replies = [r.block(canonical("assistant", c["response"]))[1:] for c in calls]
        for arm in arms:
            for k, v in simulate(arm, prompts, replies).items():
                agg[arm][k] += v
    print(f"{a.model} {a.bench}: {len(trajs)} trajectories with >= {a.min_calls} lead calls, "
          f"{agg['shift']['calls']} calls ({agg['shift']['edited']} after an edit)")
    print(f"{'arm':14} {'reused S/P':>10} {'computed C/P':>12} {'heads H/P':>9} {'aged out /P':>11} {'reuse vs shift':>14}")
    base = agg["shift"]["S"] or 1
    for arm in arms:
        t = agg[arm]
        P = t["P"] or 1
        print(f"{arm:14} {t['S'] / P:10.1%} {t['C'] / P:12.1%} {t['H'] / P:9.2%} {t['aged_out'] / P:11.1%} "
              f"{t['S'] / base:14.2f}")
    pick = next((n for n in caps if agg[f"epic128a{n}"]["S"] >= 0.5 * agg["shift"]["S"]), None)
    print(f"smallest cap keeping >= half of shift's reuse: N = {pick}")


ep_im = 0

if __name__ == "__main__":
    main()
