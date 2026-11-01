#!/usr/bin/env python3
"""Offline calibration for research/19_efficiency_signals (CPU only; writes data/calibration.json).

1. The harness's context limit for MiniMax-M2.7, by topic 11's rule (mu_workloads.context_limit):
   - the history (characters of json.dumps(messages), the harness's own measure) may fill the window left after
     the output budget and the fixed prompt (system prompt + tool schemas);
   - characters are converted at the 5th percentile of characters per token, so 95 % of prompts fit.

   Topic 19 reserves the escalated output budget (32,000) in a 65,536-token window. That keeps topic 18's history
   budget of about 31k tokens: topic 18 had a 40,960-token window with 8,000 reserved.

   The ratio is measured on topic 18's recorded lead requests (the same workload), rendered with MiniMax's
   template and tokenizer exactly as the server renders them (ef_render). The fixed prompt is measured for the
   largest arm (m_tools: the server paragraph and three more tools), so every arm gets the same limit.

2. The gate-size anchor: the mean prompt tokens of the lead calls in topic 18's w08_none window (Qwen3-32B, the
   workload under which topic 11's K=8 worked). Topic 19's rule is K = clip(round(8 x this / the same mean in
   pilot P1's none8), 4, 12).

    /data/fritz/venv/bin/python research/19_efficiency_signals/ef_calibrate.py
"""
from __future__ import annotations

import glob
import json
import lzma
import random
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(REPO / "s15_integrated_harness"))
from ef_render import render, tokens  # noqa: E402

T18 = REPO / "research" / "18_harness_wait" / "data" / "cells"
OUT = DIR / "data" / "calibration.json"
MAX_MODEL_LEN = 65536
RESERVED_OUTPUT = 32000                   # S15_MAX_TOKENS_ESCALATED
MIN_CHARS = 30000                         # topic 11 used contexts > 30k chars for the ratio
SAMPLE = 400
CELLS = ("w08_none", "w16_sgate", "w32_sgate")


def lead_requests(cell: str):
    for path in sorted(glob.glob(str(T18 / cell / "runs" / "*" / "*.requests.jsonl.xz"))):
        for line in lzma.open(path, "rt"):
            x = json.loads(line)
            if x.get("purpose") == "lead" and isinstance(x.get("messages"), list) and x["messages"]:
                yield x


def hold_runtime():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ef_hold_runtime", REPO / "s15_integrated_harness" / "hold_runtime.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module                    # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


def main():
    rng = random.Random(19)
    rows = [x for cell in CELLS for x in lead_requests(cell)]
    big = [x for x in rows if len(json.dumps(x["messages"], default=str)) > MIN_CHARS]
    sample = rng.sample(big, min(SAMPLE, len(big)))
    hold = hold_runtime()
    probe = {"role": "user", "content": "x"}

    def fixed_tokens(system: str, tools: list) -> int:
        """Tokens of the prompt around a one-character history (system prompt, tool schemas, template frame)."""
        return len(tokens(render({"system": system, "tools": tools, "messages": [probe]}))) - len(tokens("x"))

    base = sample[0]
    overhead_none = fixed_tokens(base["system"], base["tools"])
    section = hold.SERVER_SECTIONS["tools"]
    tools_toolbox = list(base["tools"]) + [{**hold.WAIT_TOOL, "description": hold.WAIT_TEXTS["v3"]},
                                           dict(hold.YIELD_TOOL), dict(hold.STATUS_TOOL)]
    overhead_tools = fixed_tokens(base["system"] + "\n\n" + section, tools_toolbox)
    ratios = []
    for x in sample:
        chars = len(json.dumps(x["messages"], default=str))
        n = len(tokens(render({"system": x["system"], "tools": x["tools"], "messages": x["messages"]})))
        o = fixed_tokens(x["system"], x["tools"])
        ratios.append(chars / max(1, n - o))
    ratios.sort()
    p5 = ratios[int(0.05 * (len(ratios) - 1))]
    p50 = ratios[len(ratios) // 2]
    history_tokens = MAX_MODEL_LEN - RESERVED_OUTPUT - overhead_tools
    limit = int(history_tokens * p5 // 1000) * 1000

    # the gate anchor: topic 18's w08_none lead calls inside the measurement window
    window = json.loads((T18 / "w08_none" / "window.json").read_text())
    prompts = []
    for x in lead_requests("w08_none"):
        usage = (x.get("response") or {}).get("usage") or {}
        t = x.get("t_send") or 0
        if window["window_start"] <= t <= window["window_end"] and usage:
            prompts.append(sum(int(usage.get(k) or 0) for k in
                               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
    out = {
        "context": {"source_cells": list(CELLS), "requests": len(rows), "over_30k_chars": len(big),
                    "sampled": len(sample), "chars_per_token_p5": round(p5, 3), "chars_per_token_p50": round(p50, 3),
                    "fixed_prompt_tokens_none": overhead_none, "fixed_prompt_tokens_m_tools": overhead_tools,
                    "max_model_len": MAX_MODEL_LEN, "reserved_output": RESERVED_OUTPUT,
                    "history_tokens": history_tokens, "context_limit_chars": limit,
                    "topic18": {"max_model_len": 40960, "reserved_output": 8000, "fixed_prompt_tokens": 1795,
                                "chars_per_token_p5": 4.33, "history_tokens": 40960 - 8000 - 1795,
                                "context_limit_chars": 134000}},
        "gate_anchor": {"cell": "w08_none", "lead_calls_in_window": len(prompts),
                        "mean_prompt_tokens": round(sum(prompts) / max(1, len(prompts)), 1),
                        "rule": "K = clip(round(8 * mean_prompt_tokens / mean prompt tokens of P1 none8's lead calls), 4, 12)"},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
