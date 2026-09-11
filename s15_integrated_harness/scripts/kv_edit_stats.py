#!/usr/bin/env python3
"""Tokenizer-only pass over dumped lead requests: where does each compaction edit sit in the prompt,
how many tokens follow it (re-prefill a splice avoids), how many were evicted, how big is the tail?

    python3 kv_edit_stats.py <trace>.requests.jsonl ... [--model Qwen/Qwen2.5-1.5B-Instruct]
"""
from __future__ import annotations
import argparse, json, re, statistics, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kv_render import render_request, render_assistant, tokenize_segments, flatten
from kv_replay import seg_ops


def run_stats(path, tok, keep_timestamp=False):
    recs = sorted([json.loads(l) for l in open(path, encoding="utf-8") if l.strip()], key=lambda r: r["call_index"])
    recs = [r for r in recs if r.get("purpose") == "lead"]
    prev = None
    rows = []
    for r in recs:
        if not keep_timestamp:
            r["system"] = re.sub(r"Current time: \S+", "Current time: 2026-09-11T00:00:00", r["system"])
        segs = tokenize_segments(tok, render_request(r))
        ids = flatten(segs)
        resp = render_assistant(r["response"]["content"]); tokenize_segments(tok, [resp])
        if prev is not None:
            seg_level, token_level = seg_ops(prev, segs)
            tail = token_level[-1] if seg_level[-1][0] == "insert" else None
            edits = [(sl, tl) for sl, tl in zip(seg_level, token_level) if sl[0] != "equal" and not (tail and tl == tail)]
            kinds = Counter()
            evicted_tokens = 0
            for (tag, i1, i2, j1, j2), tl in edits:
                new_txt = " ".join(s.text[:60] for s in segs[j1:j2])
                if "[Compacted]" in new_txt or "[Reactive compact]" in new_txt: kinds["summary"] += 1
                elif "messages archived at" in new_txt: kinds["snip"] += 1
                elif any(s.meta.get("placeholder") for s in segs[j1:j2]): kinds["placeholder"] += 1
                else: kinds[f"{tag}:{','.join(sorted({s.kind for s in segs[j1:j2]} | {s.kind for s in prev[i1:i2]}))}"] += 1
                evicted_tokens += sum(len(s.ids) for s in prev[i1:i2] if s.kind == "tool_result")
            first = min((tl[3] for _, tl in edits), default=None)
            rows.append({"call": r["call_index"], "tokens": len(ids), "prev_tokens": len(prev_ids), "edit": bool(edits),
                         "kinds": dict(kinds), "first_change": first,
                         "after_first_change": (len(ids) - first) if first is not None else 0,
                         "tail": (tail[4] - tail[3]) if tail else 0,
                         "fresh_recompute": (len(ids) - first) if first is not None else ((tail[4] - tail[3]) if tail else 0),
                         "fresh_splice": sum(tl[4] - tl[3] for _, tl in edits) + ((tail[4] - tail[3]) if tail else 0),
                         "evicted_tokens": evicted_tokens, "response_tokens": len(resp.ids)})
        prev = segs + [resp]
        prev_ids = flatten(prev)
    return rows


def suffix_stats(path, tok, keep_timestamp=False):
    """For every evicted tool result, how many tokens of the NEW prompt follow the point where it sat.

    This is the capacity of the stale suffix that a splice keeps: if an evicted block sits at the very
    end of the prompt there is nothing after it that could have absorbed its content, and a null
    retention result would be trivial.  Reported for all evicted items and for the LARGEST one per step
    (the item the replay probes)."""
    recs = sorted([json.loads(l) for l in open(path, encoding="utf-8") if l.strip()], key=lambda r: r["call_index"])
    recs = [r for r in recs if r.get("purpose") == "lead"]
    prev = None
    rows = []
    for r in recs:
        if not keep_timestamp:
            r["system"] = re.sub(r"Current time: \S+", "Current time: 2026-09-11T00:00:00", r["system"])
        segs = tokenize_segments(tok, render_request(r))
        resp = render_assistant(r["response"]["content"]); tokenize_segments(tok, [resp])
        if prev is not None:
            seg_level, token_level = seg_ops(prev, segs)
            total_new = sum(len(x.ids) for x in segs)
            tail = token_level[-1] if seg_level[-1][0] == "insert" else None
            step_items = []
            for (tag, i1, i2, j1, j2), tl in zip(seg_level, token_level):
                if tag == "equal" or (tail and tl == tail):
                    continue
                for sg in prev[i1:i2]:
                    if sg.kind == "tool_result" and not sg.meta.get("placeholder") and sg.meta.get("chars", 0) > 800:
                        step_items.append({"call": r["call_index"], "tokens": len(sg.ids),
                                           "chars": sg.meta.get("chars"),
                                           "suffix_tokens": total_new - tl[3],   # tokens of the new prompt at/after the edit point
                                           "suffix_frac": (total_new - tl[3]) / total_new})
            if step_items:
                rows.append(step_items)
        prev = segs + [resp]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--keep-timestamp", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--suffix", action="store_true", help="report how much prompt follows each evicted block")
    a = ap.parse_args()
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.model)
    allrows = {}
    print("| run | steps | edit steps | placeholder / snip / summary edits | ctx tokens med (max) | tokens after first change on edit steps: med / sum | tail tokens sum | tokens computed: recompute / splice (ratio) | evicted tokens |")
    print("|---|---:|---:|---|---|---|---:|---|---:|")
    for p in a.paths:
        rows = run_stats(p, tok, a.keep_timestamp)
        allrows[p] = rows
        label = Path(p).name.split("_")[1][:15]
        e = [r for r in rows if r["edit"]]
        kinds = Counter()
        for r in e:
            kinds.update(r["kinds"])
        rc = sum(r["fresh_recompute"] for r in rows); sp = sum(r["fresh_splice"] for r in rows)
        print(f"| {label} | {len(rows)} | {len(e)} | {kinds.get('placeholder',0)} / {kinds.get('snip',0)} / {kinds.get('summary',0)} "
              f"(other: {sum(v for k,v in kinds.items() if k not in ('placeholder','snip','summary'))}) | "
              f"{int(statistics.median(r['tokens'] for r in rows))} ({max(r['tokens'] for r in rows)}) | "
              f"{int(statistics.median(r['after_first_change'] for r in e)) if e else 0} / {sum(r['after_first_change'] for r in e):,} | "
              f"{sum(r['tail'] for r in rows):,} | {rc:,} / {sp:,} ({sp/rc:.0%}) | {sum(r['evicted_tokens'] for r in e):,} |")
    if a.suffix:
        print("\n### tokens of the new prompt that follow an evicted block (the stale suffix a splice keeps)")
        print("| run | evicted blocks >800 chars | suffix tokens, all blocks (median / p10) | suffix tokens, largest block per step (median / min) | suffix as share of prompt (median) |")
        print("|---|---:|---|---|---:|")
        for p in a.paths:
            rows = suffix_stats(p, tok, a.keep_timestamp)
            allv = [it["suffix_tokens"] for step in rows for it in step]
            big = [max(step, key=lambda it: it["chars"])["suffix_tokens"] for step in rows]
            fr = sorted(it["suffix_frac"] for step in rows for it in step)
            allv_s, big_s = sorted(allv), sorted(big)
            label = Path(p).name.split("_")[1][:15]
            print(f"| {label} | {len(allv)} | {allv_s[len(allv_s)//2]:,} / {allv_s[len(allv_s)//10]:,} | "
                  f"{big_s[len(big_s)//2]:,} / {min(big_s):,} | {fr[len(fr)//2]:.0%} |")
    if a.json:
        Path(a.json).write_text(json.dumps(allrows, indent=1))


if __name__ == "__main__":
    main()
