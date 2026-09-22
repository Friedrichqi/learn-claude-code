#!/usr/bin/env python3
"""Direct probes of the configured provider: prefill rate, decode rate, cache semantics, headers.

    python3 s15_integrated_harness/scripts/provider_probe.py --all [--json out.json]
    python3 s15_integrated_harness/scripts/provider_probe.py --prefill --reps 3
    python3 s15_integrated_harness/scripts/provider_probe.py --cache
    python3 s15_integrated_harness/scripts/provider_probe.py --headers

Why this exists.  The per-round breakdown (scripts/latency_breakdown.py, report
`s15_integrated_harness/latency_profile.md`) cannot read prefill off the wire: the provider
delivers thinking and tool_use blocks in bursts, so the client-side time to the first content
block is the whole call for a third to a half of all agent calls, and the median gap between
stream deltas measures burst spacing (0.2 ms), not token spacing.  The report therefore separates
prefill from decode by regressing call duration on uncached prompt tokens and output tokens; this
script checks that coefficient directly, by two routes that do not depend on the regression:

  --prefill   sweep the UNCACHED prompt size with the output length held fixed and fit the time to
              the first block against uncached tokens; then re-send an identical prompt so the same
              tokens are served from cache and take the difference (the cached-resend route).
              Also reports the decode rate (output tokens / streamed tail) at each size and for one
              long-output call.
  --cache     is the prompt cache prefix-based?  Sends the same body with an identical prefix, a new
              prefix, a unique block inserted mid-prompt, and a document never sent before.
  --headers   what the HTTP response exposes (this provider returns `x-process-time`, the server's
              own processing seconds, which tracks prefill; there is no timing in `usage`).

Against vLLM the `--headers` probe shows no timing header either, but the server exposes Prometheus
/metrics (scraped per call by profile_run.py --vllm-metrics) and its usage reports the computed tokens as
input_tokens + cache_creation_input_tokens (see stream_call).

Results as of 2026-09-13 on z.ai `glm-5.3-flash` are in `weekly_progress/091626/latency_breakdown.md`
section 4: prefill 0.034-0.042 ms per uncached token (~26,000 tok/s), fixed part 3.1-3.8 s with
+-2 s of jitter, decode 39-104 tok/s, cache strictly prefix-based.

NB the SDK retries twice by default; a retried call hits the cache entry its failed attempt created
and looks like a cache hit on a fresh prompt.  Retries are disabled here for that reason.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIZES = [2000, 8000, 20000, 50000, 100000, 160000]      # characters of unique prompt text
FILLER = REPO / "s15_integrated_harness" / "code.py"     # real text, ~0.23 tokens per character
OTHER = REPO / "s15_integrated_harness" / "input_redundancy_runs.md"
QUESTION = "\n\nHow many characters of text are above this line? Reply with one short sentence."


def client_and_model():
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env", override=True)
    import anthropic
    return (anthropic.Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"), max_retries=0),
            os.environ["MODEL_ID"])


def stream_call(client, model, prompt: str, label: str, chars: int, max_tokens: int = 300) -> dict:
    """One streamed call with client-side timing of every stream event."""
    started = time.perf_counter()
    message_start = first_block = None
    deltas: list[float] = []
    with client.messages.stream(model=model, max_tokens=max_tokens,
                                messages=[{"role": "user", "content": prompt}]) as stream:
        for event in stream:
            now = time.perf_counter() - started
            if event.type == "message_start" and message_start is None:
                message_start = now
            elif event.type == "content_block_start" and first_block is None:
                first_block = now
            elif event.type == "content_block_delta":
                deltas.append(now)
        message = stream.get_final_message()
    end = time.perf_counter() - started
    usage = message.usage
    gaps = [b - a for a, b in zip(deltas, deltas[1:])]
    tail = end - (first_block if first_block is not None else end)
    row = {
        "label": label, "chars": chars,
        "ttft_s": round(first_block if first_block is not None else end, 4),
        "message_start_s": round(message_start or 0.0, 4), "end_s": round(end, 4),
        # vLLM reports the computed tokens it wrote into its prefix cache as cache_creation_input_tokens and
        # leaves only the partial tail block in input_tokens; z.ai never fills cache_creation.  "uncached" =
        # tokens the server computed = both parts.
        "uncached_tok": (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", None) or 0),
        "input_tok": usage.input_tokens or 0, "created_tok": getattr(usage, "cache_creation_input_tokens", None) or 0,
        "cached_tok": usage.cache_read_input_tokens or 0,
        "output_tok": usage.output_tokens or 0, "deltas": len(deltas),
        "gap_median_ms": round(1000 * statistics.median(gaps), 2) if gaps else None,
        "gap_mean_ms": round(1000 * statistics.fmean(gaps), 2) if gaps else None,
        "tail_s": round(tail, 4), "stop": message.stop_reason,
    }
    row["decode_tok_s"] = round(row["output_tok"] / tail, 1) if tail > 0.05 else None
    return row


def linfit(pairs):
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    if sxx == 0:
        return None
    b = sum((p[0] - mx) * (p[1] - my) for p in pairs) / sxx
    a = my - b * mx
    ss_res = sum((p[1] - (a + b * p[0])) ** 2 for p in pairs)
    ss_tot = sum((p[1] - my) ** 2 for p in pairs) or 1.0
    return a, b, 1 - ss_res / ss_tot, n


def probe_headers(client, model) -> dict:
    raw = client.messages.with_raw_response.create(
        model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with OK."}])
    headers = {k: v for k, v in raw.headers.items()
               if not k.lower().startswith(("set-cookie", "cf-", "x-amz"))}
    print("-- response headers (timing fields, if any)")
    for key in sorted(headers):
        print(f"   {key}: {headers[key]}")
    usage = raw.parse().usage
    print(f"-- usage fields carry token counts only: {usage}")
    return headers


def probe_prefill(client, model, reps: int, rows: list) -> None:
    filler = FILLER.read_text(encoding="utf-8")
    print(f"-- prefill sweep: {len(SIZES)} sizes x {reps} reps, output held at 300 tokens, no tools")
    import random
    for rep in range(reps):
        sizes = SIZES[:]
        random.Random(rep).shuffle(sizes)                 # spread provider drift over the sizes
        for size in sizes:
            prompt = f"[document {uuid.uuid4()}]\n" + filler[:size] + QUESTION
            try:
                row = stream_call(client, model, prompt, f"uncached-r{rep + 1}", size)
            except Exception as exc:
                print(f"   ERROR {size} chars: {type(exc).__name__}: {exc}")
                time.sleep(10)
                continue
            rows.append(row)
            print(f"   {size:>7} chars  uncached {row['uncached_tok']:>6,}  cached {row['cached_tok']:>6,}"
                  f"  first block {row['ttft_s']:5.2f}s  tail {row['tail_s']:5.2f}s  {row['decode_tok_s']} tok/s")
            time.sleep(2)
            if rep == 0 and size in (50000, 160000):       # identical resend -> cache-served
                row = stream_call(client, model, prompt, "cached-resend", size)
                rows.append(row)
                print(f"   {size:>7} chars  RESEND   uncached {row['uncached_tok']:>6,} cached {row['cached_tok']:>6,}"
                      f"  first block {row['ttft_s']:5.2f}s")
                time.sleep(2)
    try:
        rows.append(stream_call(client, model,
                                "Write a 400-word explanation of how an LRU cache with a per-entry "
                                "time-to-live works.", "decode-long", 0, max_tokens=1200))
        print(f"   long output: {rows[-1]['output_tok']} tokens at {rows[-1]['decode_tok_s']} tok/s")
    except Exception as exc:
        print(f"   ERROR long-output call: {type(exc).__name__}: {exc}")


def report_prefill(rows: list) -> None:
    fresh = [r for r in rows if r["label"].startswith("uncached") and r["cached_tok"] == 0]
    served = [r for r in rows if r["label"] == "cached-resend"]
    contaminated = [r for r in rows if r["label"].startswith("uncached") and r["cached_tok"]]
    if not fresh:
        return
    print("\n== prefill")
    fit = linfit([(r["uncached_tok"], 1000 * r["ttft_s"]) for r in fresh])
    if fit:
        a, b, r2, n = fit
        rate = f"{1000 / b:,.0f} tok/s" if b > 0 else "slope not positive"
        print(f"   all fresh calls (n={n}): first block = {a / 1000:.2f} s + {b:.4f} ms/uncached token (r2 {r2:.2f}) -> {rate}")
    rep1 = [r for r in fresh if r["label"] == "uncached-r1"]
    fit = linfit([(r["uncached_tok"], 1000 * r["ttft_s"]) for r in rep1])
    if fit:
        a, b, r2, n = fit
        rate = f"{1000 / b:,.0f} tok/s" if b > 0 else "slope not positive"
        print(f"   one clean sweep (n={n}):  first block = {a / 1000:.2f} s + {b:.4f} ms/uncached token (r2 {r2:.2f}) -> {rate}")
    for resend in served:
        match = [r for r in fresh if r["chars"] == resend["chars"] and r["label"] == "uncached-r1"]
        if match:
            saving = match[0]["ttft_s"] - resend["ttft_s"]
            print(f"   cached resend of {resend['cached_tok']:,} tokens: {match[0]['ttft_s']:.2f}s -> {resend['ttft_s']:.2f}s"
                  f"  = {1000 * saving / max(match[0]['uncached_tok'], 1):.4f} ms saved per cached token")
    if contaminated:
        print(f"   NOTE {len(contaminated)} nominally fresh call(s) came back cached; check for SDK retries")
    decode = [r for r in rows if r["decode_tok_s"]]
    if decode:
        print(f"\n== decode: median {statistics.median(r['decode_tok_s'] for r in decode):.0f} tok/s, "
              f"range {min(r['decode_tok_s'] for r in decode):.0f}-{max(r['decode_tok_s'] for r in decode):.0f}")
        gaps = [r["gap_mean_ms"] for r in rows if r["gap_mean_ms"]]
        meds = [r["gap_median_ms"] for r in rows if r["gap_median_ms"]]
        if gaps:
            print(f"   inter-delta gap: mean {statistics.fmean(gaps):.1f} ms but median {statistics.fmean(meds):.1f} ms "
                  f"-- events arrive in bursts, so use tokens/s, not the median gap")
    bursts = [r for r in rows if r["tail_s"] < 0.1]
    if bursts:
        print(f"   {len(bursts)} of {len(rows)} calls delivered the whole response in one burst (tail < 0.1 s)")


def probe_cache(client, model, rows: list) -> None:
    body = FILLER.read_text(encoding="utf-8")[:120000]
    never_sent = OTHER.read_text(encoding="utf-8")[:120000]
    tag = uuid.uuid4()
    middle = len(body) // 2
    cases = [
        ("1 first send, unique prefix", f"[marker {tag}]\n" + body + QUESTION),
        ("2 identical resend", f"[marker {tag}]\n" + body + QUESTION),
        ("3 new prefix, same body", f"[marker {uuid.uuid4()}]\n" + body + QUESTION),
        ("4 unique block inserted mid-prompt",
         f"[marker {tag}]\n" + body[:middle] + f"\n[inserted {uuid.uuid4()}]\n" + body[middle:] + QUESTION),
        ("5 document never sent before", f"[marker {uuid.uuid4()}]\n" + never_sent + QUESTION),
        ("6 that document again, new prefix", f"[marker {uuid.uuid4()}]\n" + never_sent + QUESTION),
    ]
    print("-- cache semantics: is the prompt cache prefix-based?")
    for label, prompt in cases:
        try:
            row = stream_call(client, model, prompt, label, len(prompt), max_tokens=100)
        except Exception as exc:
            print(f"   ERROR {label}: {type(exc).__name__}: {exc}")
            continue
        rows.append(row)
        total = row["uncached_tok"] + row["cached_tok"]
        print(f"   {label:36} first block {row['ttft_s']:5.2f}s  cached {row['cached_tok']:>6,} of {total:>6,}"
              f"  ({100 * row['cached_tok'] / total if total else 0:5.1f}%)")
        time.sleep(2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefill", action="store_true", help="prompt-size sweep + cached resends + decode rate")
    parser.add_argument("--cache", action="store_true", help="prefix-cache semantics")
    parser.add_argument("--headers", action="store_true", help="what the HTTP response exposes")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--reps", type=int, default=3, help="repetitions of the prefill sweep (default 3)")
    parser.add_argument("--json", default=None, help="write every call's timing to this file")
    args = parser.parse_args(argv)
    if not (args.prefill or args.cache or args.headers or args.all):
        parser.error("choose at least one of --prefill / --cache / --headers / --all")
    client, model = client_and_model()
    print(f"provider {os.getenv('ANTHROPIC_BASE_URL')} model {model}\n")
    rows: list[dict] = []
    if args.headers or args.all:
        probe_headers(client, model)
        print()
    if args.prefill or args.all:
        probe_prefill(client, model, args.reps, rows)
        report_prefill(rows)
        print()
    if args.cache or args.all:
        probe_cache(client, model, rows)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"\nwrote {len(rows)} calls to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
