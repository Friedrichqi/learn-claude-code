#!/usr/bin/env python3
"""Probe which reasoning-effort lever this vLLM build actually honors, before the matrix runs.

Step 2 depends on the server reducing output volume when asked.  vLLM's Anthropic endpoint
exposes two levers -- output_config.effort and chat_template_kwargs.enable_thinking -- and
which one changes anything depends on the model's chat template.  This probe fires one fixed
reasoning prompt in five configurations, reports output tokens / thinking chars / duration
for each, and prints the JSON verdict the pipeline stores as effort_mode.json:

  {"mode": "output_config"|"nothink"|"default", "detail": {...}}

'default' means nothing moved the needle and the matrix should run without --effort-policy
(main-low would then only add risk, though 'memory' stays safe and free).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROMPT = (
    "A train leaves A at 60 km/h. Two hours later a train leaves B, 480 km away from A, "
    "toward A at 90 km/h. How far from A do they meet? Think it through, then give the "
    "final number on its own line."
)


def one_call(client, model: str, label: str, **kwargs) -> dict:
    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=3000,
            messages=[{"role": "user", "content": PROMPT}],
            **kwargs,
        )
    except Exception as exc:                                           # noqa: BLE001
        return {"label": label, "error": f"{type(exc).__name__}: {exc}"[:300]}
    usage = getattr(response, "usage", None)
    think = text = 0
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind in ("thinking", "redacted_thinking"):
            think += len(getattr(block, "thinking", "") or "")
        elif kind == "text":
            text += len(getattr(block, "text", "") or "")
    return {
        "label": label,
        "output_tokens": getattr(usage, "output_tokens", None),
        "input_tokens": getattr(usage, "input_tokens", None),
        "thinking_chars": think,
        "text_chars": text,
        "duration_s": round(time.perf_counter() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--out", default=None, help="write the verdict JSON here")
    args = parser.parse_args()

    from anthropic import Anthropic
    client = Anthropic(base_url=args.base_url, api_key=args.api_key, max_retries=0)

    results = [
        one_call(client, args.model, "default"),
        one_call(client, args.model, "effort_low",
                 extra_body={"output_config": {"effort": "low"}}),
        one_call(client, args.model, "effort_medium",
                 extra_body={"output_config": {"effort": "medium"}}),
        one_call(client, args.model, "nothink",
                 extra_body={"chat_template_kwargs": {"enable_thinking": False}}),
        one_call(client, args.model, "default_repeat"),
    ]
    for result in results:
        print(json.dumps(result), flush=True)

    by_label = {r["label"]: r for r in results if "error" not in r}
    baseline = by_label.get("default") or {}
    base_tokens = baseline.get("output_tokens") or 0
    base_think = baseline.get("thinking_chars") or 0

    def moved(result, token_ratio=0.7, think_ratio=0.5):
        if not result or base_tokens == 0:
            return False
        tokens = result.get("output_tokens") or 0
        think = result.get("thinking_chars") or 0
        return tokens <= base_tokens * token_ratio or (base_think > 0 and think <= base_think * think_ratio)

    mode = "default"
    detail = {"baseline_out_tokens": base_tokens, "baseline_thinking_chars": base_think}
    if "effort_low" in by_label and moved(by_label["effort_low"]):
        mode = "output_config"
    elif "nothink" in by_label and moved(by_label["nothink"]):
        mode = "nothink"
    detail["results"] = results
    verdict = {"mode": mode, "detail": detail}
    print("VERDICT:", json.dumps({"mode": mode}))
    if args.out:
        Path(args.out).write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
