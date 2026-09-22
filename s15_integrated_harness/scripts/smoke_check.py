#!/usr/bin/env python3
"""Gate for the vLLM/Qwen pipeline: check one smoke run before the 24-run matrix is started.

    python3 s15_integrated_harness/scripts/smoke_check.py <trace-dir> <label> [--min-coverage 0.7]

Hard checks (exit 1 when one fails): the run completed (status completed / teammates-idle / no-teammates),
at least one teammate was spawned, at least three model responses carried a tool_use block, every
successful call reports input_tokens > 0 and output_tokens > 0, streaming timing (ttft_ms) is present,
cache_read_input_tokens is reported (the server runs with --enable-prompt-tokens-details) and is > 0 at
least once, server metrics are present when the driver scraped them, and the FQA coverage score reaches
--min-coverage.

Soft checks (printed only): thinking share of output, stop_reason distribution and the max_tokens share,
share of calls whose server metrics are exactly attributable, literal '<tool_call>' / '<function=' text in
model output (tool-parser failures), first-block type distribution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trace_dir")
    parser.add_argument("label")
    parser.add_argument("--min-coverage", type=float, default=0.7)
    parser.add_argument("--min-tool-use", type=int, default=3)
    args = parser.parse_args(argv)
    tdir = Path(args.trace_dir)
    failures: list[str] = []
    notes: list[str] = []

    score_path = tdir / f"{args.label}.score.json"
    if not score_path.exists():
        print(f"FAIL: no score file {score_path} (the run did not finish or was not scored)")
        return 1
    score = json.loads(score_path.read_text(encoding="utf-8"))
    trace = Path(score.get("trace") or "")
    if not trace.exists():
        print(f"FAIL: trace {trace} missing")
        return 1
    records = load_jsonl(trace)
    inputs = [r for r in load_jsonl(trace.with_name(trace.name.removesuffix(".jsonl") + ".inputs.jsonl")) if r.get("status") == "ok"]
    console = tdir / f"{args.label}.console.log"

    status = score.get("status")
    if status not in {"completed", "teammates-idle", "no-teammates"}:
        failures.append(f"run status {status!r}")
    teammates = [r for r in records if r.get("event") == "agent_create" and (r.get("agent_kind") or r.get("data", {}).get("agent_kind")) == "teammate"]
    if not teammates:
        failures.append("no teammate was spawned")
    notes.append(f"status={status} wall={score.get('wall_seconds')}s teammates={len(teammates)} model_calls={len(inputs)}")

    if not inputs:
        failures.append("no successful model calls in the inputs sidecar")
    agent_calls = [r for r in inputs if r.get("purpose") in (None, "lead", "teammate", "one_shot")]
    tool_use_calls = [r for r in agent_calls if (r.get("output_blocks") or {}).get("tool_use")]
    if len(tool_use_calls) < args.min_tool_use:
        failures.append(f"only {len(tool_use_calls)} responses carried a tool_use block (need >= {args.min_tool_use})")
    bad_usage = [r for r in inputs if not ((r.get("usage") or {}).get("input_tokens") or 0) > 0 or not ((r.get("usage") or {}).get("output_tokens") or 0) > 0]
    if bad_usage:
        failures.append(f"{len(bad_usage)} calls report input_tokens or output_tokens <= 0 (streaming usage broken?)")
    no_ttft = [r for r in inputs if (r.get("stream") or {}).get("ttft_ms") is None]
    if no_ttft:
        failures.append(f"{len(no_ttft)} calls have no stream.ttft_ms (was --stream passed?)")
    cache_reported = [r for r in inputs if (r.get("usage") or {}).get("cache_read_input_tokens") is not None]
    cache_hits = [r for r in cache_reported if (r["usage"].get("cache_read_input_tokens") or 0) > 0]
    if not cache_reported:
        failures.append("cache_read_input_tokens is never reported: start vLLM with --enable-prompt-tokens-details")
    elif not cache_hits and len(inputs) > 3:
        failures.append("cache_read_input_tokens is always 0: prefix caching is not working")
    created = sum((r.get("usage") or {}).get("cache_creation_input_tokens") or 0 for r in inputs)
    notes.append(f"cache: reported on {len(cache_reported)}/{len(inputs)} calls, >0 on {len(cache_hits)}, cache_creation total {created:,}")

    vllm = [r for r in inputs if r.get("vllm")]
    meta = next((r["data"] for r in records if r.get("event") == "profile_meta"), {})
    if meta.get("vllm_metrics") and not vllm:
        failures.append("driver was told to scrape vLLM metrics but no call carries a vllm record")
    if vllm:
        exact = sum(1 for r in vllm if r["vllm"].get("exact"))
        prefill = [r["vllm"].get("prefill_s") for r in vllm if r["vllm"].get("exact")]
        decode = [r["vllm"].get("decode_s") for r in vllm if r["vllm"].get("exact")]
        notes.append(f"server metrics on {len(vllm)} calls, exact attribution {exact}/{len(vllm)}; "
                     f"exact-call means prefill {sum(prefill) / len(prefill):.2f}s decode {sum(decode) / len(decode):.2f}s" if prefill else
                     f"server metrics on {len(vllm)} calls, exact attribution {exact}/{len(vllm)}")
        over = [r for r in vllm if r["vllm"].get("exact") and (r["vllm"].get("prefill_s") or 0) + (r["vllm"].get("decode_s") or 0) + (r["vllm"].get("queue_s") or 0) > (r.get("duration_ms") or 0) / 1000 * 1.05 + 0.05]
        if over:
            notes.append(f"WARNING {len(over)} exact calls have server prefill+decode+queue > client duration")

    stops = Counter(r.get("stop_reason") for r in agent_calls)
    maxed = stops.get("max_tokens", 0)
    notes.append(f"stop_reason: {dict(stops)}; max_tokens share {100 * maxed / max(len(agent_calls), 1):.0f}%")
    think = sum(r.get("output_thinking_chars") or 0 for r in agent_calls)
    total = sum((r.get("output_thinking_chars") or 0) + (r.get("output_text_chars") or 0) + (r.get("output_tool_input_chars") or 0) for r in agent_calls)
    notes.append(f"thinking share of output chars {100 * think / max(total, 1):.0f}%; first block types {dict(Counter((r.get('stream') or {}).get('first_block_type') for r in agent_calls))}")
    prompt = [((r.get("usage") or {}).get("input_tokens") or 0) + ((r.get("usage") or {}).get("cache_read_input_tokens") or 0) + ((r.get("usage") or {}).get("cache_creation_input_tokens") or 0) for r in agent_calls]
    outp = [(r.get("usage") or {}).get("output_tokens") or 0 for r in agent_calls]
    if agent_calls:
        notes.append(f"prompt tok mean {sum(prompt) / len(prompt):,.0f} max {max(prompt):,}; output tok mean {sum(outp) / len(outp):,.0f} max {max(outp):,}; "
                     f"client call mean {sum(r.get('duration_ms') or 0 for r in agent_calls) / len(agent_calls) / 1000:.1f}s")

    leaked = 0
    texts = []
    if console.exists():
        texts.append(re.sub(r"\x1b\[[0-9;]*m", "", console.read_text(encoding="utf-8", errors="replace")))
    for r in records:
        if r.get("event") == "message_send":
            content = r.get("data", {}).get("content")
            texts.append(json.dumps(content) if not isinstance(content, str) else content)
    for text in texts:
        leaked += text.count("<tool_call>") + text.count("<function=")
    if leaked:
        notes.append(f"WARNING literal tool-call syntax appeared {leaked}x in model text (tool parser failures)")

    coverage = score.get("coverage")
    if coverage is not None and coverage < args.min_coverage:
        failures.append(f"FQA coverage {coverage} < {args.min_coverage}")
    if "correct" in score:
        notes.append(f"score {score.get('correct')}/{score.get('total')}")
    else:
        notes.append(f"FQA coverage {coverage}")
    denials = score.get("denials") or {}
    if denials:
        notes.append(f"denied tool calls {denials}")

    print(f"smoke check {args.label} ({trace.name})")
    for note in notes:
        print(f"  - {note}")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  ! {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
