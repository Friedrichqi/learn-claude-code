#!/usr/bin/env python3
"""Add exact prompt token counts to a provider_probe.json written against vLLM before 2026-09-22.

    python3 s15_integrated_harness/scripts/probe_tokens.py traces/latency_profiling_qwen/provider_probe.json [--model Qwen/Qwen3.8-27B]

Those rows carry vLLM's `input_tokens` (only the partial tail block of a prompt) as `uncached_tok`, so the prefill
fit of provider_probe.py/latency_compare.py has no usable x-axis.  The probe's prompts are deterministic up to a
uuid marker line, so this script rebuilds each prompt from `chars` (the same filler file) and counts its tokens
with the model's tokenizer, writing `prompt_tok` (the marker is counted with a fixed uuid, +-1 token) next to the
existing fields.  It also prints the prefill fit over the fresh calls and the per-token saving of the cached resends.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provider_probe as pp  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("json_path")
    parser.add_argument("--model", default="Qwen/Qwen3.8-27B")
    args = parser.parse_args(argv)
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model)
    rows = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    filler = pp.FILLER.read_text(encoding="utf-8")
    other = pp.OTHER.read_text(encoding="utf-8")
    marker = "[document 00000000-0000-0000-0000-000000000000]\n"
    for r in rows:
        label = str(r.get("label", ""))
        if label.startswith("uncached") or label == "cached-resend":
            prompt = marker + filler[:r["chars"]] + pp.QUESTION
        elif label == "decode-long":
            prompt = "Write a 400-word explanation of how an LRU cache with a per-entry time-to-live works."
        elif label[:1].isdigit():
            body = filler[:120000]
            never = other[:120000]
            middle = len(body) // 2
            if label.startswith("4"):
                prompt = f"[marker x]\n" + body[:middle] + "\n[inserted x]\n" + body[middle:] + pp.QUESTION
            elif label.startswith(("5", "6")):
                prompt = "[marker x]\n" + never + pp.QUESTION
            else:
                prompt = "[marker x]\n" + body + pp.QUESTION
        else:
            continue
        r["prompt_tok"] = len(tok(prompt, add_special_tokens=False)["input_ids"]) + 8  # chat-template wrapper
    Path(args.json_path).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    fresh = [r for r in rows if str(r["label"]).startswith("uncached") and not r.get("cached_tok")]
    fit = pp.linfit([(r["prompt_tok"], 1000 * r["ttft_s"]) for r in fresh])
    if fit:
        a, b, r2, n = fit
        print(f"prefill fit over {n} fresh calls: first block = {a / 1000:.2f} s + {b:.4f} ms/prompt token (r2 {r2:.2f}) -> {1000 / b:,.0f} tok/s")
    for resend in [r for r in rows if r["label"] == "cached-resend"]:
        match = [r for r in fresh if r["chars"] == resend["chars"] and r["label"] == "uncached-r1"]
        if match:
            saving = match[0]["ttft_s"] - resend["ttft_s"]
            print(f"cached resend of {resend['cached_tok']:,} of {resend['prompt_tok']:,} tokens: {match[0]['ttft_s']:.2f}s -> {resend['ttft_s']:.2f}s = {1000 * saving / resend['cached_tok']:.4f} ms saved per cached token")
    for r in rows:
        if str(r["label"]).startswith("uncached-r1") or r["label"] == "decode-long":
            print(f"   {r['label']:12s} {r['chars']:>7,} chars {r.get('prompt_tok', 0):>7,} tok  first block {r['ttft_s']:5.2f}s  decode {r['decode_tok_s']} tok/s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
