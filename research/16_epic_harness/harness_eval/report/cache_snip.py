#!/usr/bin/env python3
"""Prefix caching before and after s15's rolling snip, from every profile trial's calls.jsonl.

s15 caps the conversation at 50 messages (snip_compact). Once a run passes that, every lead call cuts the middle
again and rewrites the archive marker, so everything after the first three messages moves: the tail's content is
unchanged but its positions are not, and vLLM's prefix cache cannot reuse it. This is the edit the shift / EPIC
round targets. A call counts as post-snip when its request carries the archive marker.

    .venv/bin/python report/cache_snip.py        (also used by make_report.py)
"""
from __future__ import annotations

import glob
import json
from collections import defaultdict

ROOT = "/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval/jobs"
MARKER = " messages archived at "


def scan(models=None, benches=("swe", "swepro", "tbench")) -> dict:
    """Per (model, bench): calls, prompt tokens, cached tokens, for pre- and post-snip lead calls."""
    out = defaultdict(lambda: {"pre": [0, 0, 0], "post": [0, 0, 0]})   # [calls, prompt tokens, cached tokens]
    for path in sorted(glob.glob(f"{ROOT}/*/*/q*/*/agent/s15/calls.jsonl")):
        model, bench = path.split("/")[-7], path.split("/")[-6]
        if (models and model not in models) or bench not in benches or model.startswith("fake"):
            continue
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    c = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if c.get("purpose") != "lead" or not isinstance(c.get("usage"), dict):
                    continue
                u = c["usage"]
                cached = u.get("cache_read_input_tokens") or 0
                total = (u.get("input_tokens") or 0) + cached + (u.get("cache_creation_input_tokens") or 0)
                msgs = (c.get("request") or {}).get("messages") or []
                post = any(isinstance(m.get("content"), str) and MARKER in m["content"] for m in msgs[:5])
                k = out[(model, bench)]["post" if post else "pre"]
                k[0] += 1
                k[1] += total
                k[2] += cached
    return dict(out)


def table(data: dict, order: list[str]) -> list[str]:
    lines = ["| model | bench | lead calls after snip starts | prefix-cache hit before | after | share of re-prefilled tokens after |",
             "|---|---|---|---|---|---|"]
    key = lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 9, kv[0][1])  # noqa: E731
    for (m, b), d in sorted(data.items(), key=key):
        pre, post = d["pre"], d["post"]
        calls = pre[0] + post[0]
        hit = lambda k: f"{k[2] / k[1]:.0%}" if k[1] else "—"  # noqa: E731
        unc_pre, unc_post = pre[1] - pre[2], post[1] - post[2]
        share = f"{unc_post / (unc_pre + unc_post):.0%}" if unc_pre + unc_post else "—"
        lines.append(f"| {m} | {b} | {post[0] / calls:.0%} of {calls} | {hit(pre)} | {hit(post)} | {share} |")
    return lines


if __name__ == "__main__":
    print("\n".join(table(scan(), ["qwen3-32b", "qwen3.8-27b", "deepseek-v4-flash", "glm-4.7-flash",
                                   "minimax-m2.7"])))
