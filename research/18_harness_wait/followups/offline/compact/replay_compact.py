#!/usr/bin/env python3
"""C2 (FOLLOWUPS.md): replay the recorded lead conversations through micro_compact-style clearing policies, offline.

Per session (lead calls in order), within segments where the history only grows (a snip, a compaction or a new
turn's summary starts a new segment), the original tool results are restored from the earlier calls that still had
them, and a clearing policy is replayed on top:

A rule is (B, keep[, target]):
  (1, 3)        s15 as run: over CONTEXT_LIMIT (134,000 chars, the workload's), clear consumed results older than the
                3 most recent, oldest first, while longer than 120 chars, until at most 0.8 x the limit
  (B, 3)        batched: over the limit, wait until B results can be cleared (or the size passes 1.1 x the limit),
                then clear them oldest first down to the same target
  (1, keep)     keep fewer recent results in full
  (1, keep, 0)  over the limit, clear every clearable result at once (target 0)

For each call the prompt is compared with the previous call's (same segment): the first message that differs is the
break, and everything after it is re-prefilled. Characters are turned into tokens with the session's own ratio
(the scheduler's np over json.dumps(messages) characters, from the same calls).

Measured against the run with B=1: the replay's breaks should match the recorded placeholder changes.

    python research/18_harness_wait/followups/offline/compact/replay_compact.py w16_sgate w08_none
    python research/18_harness_wait/followups/offline/compact/replay_compact.py --dedup w16_sgate w08_none

--dedup first replaces every exact re-read (same path, offset, limit) of an unchanged file whose earlier result is
still in full with a short pointer (C4), then replays the as-run rule and "clear all but the latest".
"""
from __future__ import annotations

import glob
import json
import lzma
import sys
from collections import Counter

ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
LIMIT = 134_000
KEEP = 3
PH = "[Earlier tool result saved at /data/fritz/wt_lanes/xxx/lane00/.task_outputs/tool-results/chatcmpl-tool-0000000000000000.txt]"


def is_ph(text) -> bool:
    return isinstance(text, str) and text.startswith("[Earlier tool result saved at")


def tool_results(msgs):
    for mi, m in enumerate(msgs):
        c = m.get("content")
        if m.get("role") == "user" and isinstance(c, list):
            for bi, b in enumerate(c):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    yield mi, bi, b


def last_assistant(msgs) -> int:
    return next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "assistant"), -1)


def render(raw, cleared):
    out = []
    for m in raw:
        c = m.get("content")
        if m.get("role") == "user" and isinstance(c, list):
            c = [dict(b, content=PH) if isinstance(b, dict) and b.get("type") == "tool_result"
                 and b.get("tool_use_id") in cleared else b for b in c]
            m = dict(m, content=c)
        out.append(m)
    return out


def size(msgs) -> int:
    return len(json.dumps(msgs, default=str))


def clearable(raw, cleared, keep=KEEP):
    """Consumed results older than the `keep` most recent, longer than 120 chars, not yet cleared (oldest first)."""
    la = last_assistant(raw)
    consumed = [(mi, b) for mi, bi, b in tool_results(raw) if mi <= la]
    out = []
    for mi, b in (consumed[:-keep] if keep else consumed) if len(consumed) > keep else []:
        if b.get("tool_use_id") in cleared or len(str(b.get("content", ""))) <= 120:
            continue
        out.append(b.get("tool_use_id"))
    return out


def apply(raw, cleared, pol):
    B, keep = pol[0], pol[1]
    target = pol[2] if len(pol) > 2 else 0.8
    msgs = render(raw, cleared)
    s = size(msgs)
    if s <= LIMIT:
        return cleared
    cand = clearable(raw, cleared, keep)
    if B > 1 and len(cand) < B and s <= 1.1 * LIMIT:
        return cleared
    new = set(cleared)
    for tid in cand:
        if size(render(raw, new)) <= target * LIMIT:
            break
        new.add(tid)
    return new


def first_diff(a, b) -> int | None:
    for i in range(min(len(a), len(b))):
        if json.dumps(a[i], default=str) != json.dumps(b[i], default=str):
            return i
    return None


def session_calls(cap):
    calls = [json.loads(l) for l in lzma.open(cap, "rt")]
    return [c for c in calls if c.get("purpose") == "lead" and isinstance(c.get("messages"), list)]


def np_by_id(cell):
    out = {}
    for p in glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz"):
        with lzma.open(p, "rt") as f:
            for line in f:
                if line.startswith('{"k":"arr"'):
                    r = json.loads(line)
                    out[r["id"]] = r["np"]
    return out


DEDUP = "--dedup" in sys.argv
POINTER = "[unchanged since your earlier read of this file above; see that result]"


def dedup(raw):
    """Replace exact re-reads (same path, offset, limit) of an unchanged file whose earlier result is in full."""
    uses, seen, out = {}, {}, []
    for m in raw:
        cont = m.get("content")
        if not isinstance(cont, list):
            out.append(m)
            continue
        new = []
        for b in cont:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                inp = b.get("input") or {}
                uses[b.get("id")] = (b.get("name"), inp)
                if b.get("name") in ("write_file", "edit_file"):
                    p = str(inp.get("path") or inp.get("file_path"))
                    for k in [k for k in seen if k[0] == p]:
                        del seen[k]
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                name, inp = uses.get(b.get("tool_use_id"), (None, {}))
                if name == "read_file":
                    k = (str(inp.get("path") or inp.get("file_path")), inp.get("offset"), inp.get("limit"))
                    txt = b.get("content")
                    if k in seen and isinstance(txt, str) and not is_ph(txt):
                        b = dict(b, content=POINTER)
                    elif isinstance(txt, str) and not is_ph(txt) and not txt.startswith("<persisted-output>"):
                        seen[k] = True
            new.append(b)
        out.append(dict(m, content=new))
    return out


def replay_session(lead, nps, Bs):
    originals = {}
    res = {B: Counter() for B in Bs}
    res["real"] = Counter()
    seg_prev = None                                       # the previous call's recorded messages
    state = {}
    ratio_num = ratio_den = 0
    for c in lead:
        msgs = c["messages"]
        for _, _, b in tool_results(msgs):
            tid = b.get("tool_use_id")
            if tid not in originals and not is_ph(b.get("content")):
                originals[tid] = b.get("content")
        raw = [dict(m, content=[dict(b, content=originals.get(b.get("tool_use_id"), b.get("content")))
                                if isinstance(b, dict) and b.get("type") == "tool_result" else b
                                for b in m["content"]]) if isinstance(m.get("content"), list) else m for m in msgs]
        if DEDUP:
            raw = dedup(raw)
        np_ = nps.get(c["request_id"])
        if np_:
            ratio_num += np_; ratio_den += size(msgs)
        new_segment = seg_prev is None or len(msgs) < len(seg_prev) or \
            json.dumps(msgs[0], default=str) != json.dumps(seg_prev[0], default=str)
        cleared_now = {b.get("tool_use_id") for _, _, b in tool_results(msgs) if is_ph(b.get("content"))}
        if new_segment:
            for B in Bs:
                state[B] = {"cleared": set(cleared_now), "prev": None}
            res["real"]["segments"] += 1
        # the run as recorded
        if not new_segment:
            i = first_diff(seg_prev, msgs)
            res["real"]["calls"] += 1
            if i is not None:
                res["real"]["breaks"] += 1
                res["real"]["rewrite_chars"] += size(msgs[i:])
        # the policies
        for B in Bs:
            st = state[B]
            st["cleared"] = apply(raw, st["cleared"], B)
            cur = render(raw, st["cleared"])
            if st["prev"] is not None:
                i = first_diff(st["prev"], cur)
                res[B]["calls"] += 1
                if i is not None:
                    res[B]["breaks"] += 1
                    res[B]["rewrite_chars"] += size(cur[i:])
            res[B]["size_sum"] += size(cur)
            res[B]["size_max"] = max(res[B]["size_max"], size(cur))
            res[B]["n"] += 1
            st["prev"] = cur
        seg_prev = msgs
    ratio = ratio_num / ratio_den if ratio_den else 0.25
    return res, ratio


def main():
    Bs = [(1, 3), (8, 3), (1, 2), (1, 1), (1, 3, 0.0), (1, 1, 0.0)] if not DEDUP else [(1, 3), (1, 1, 0.0)]
    for cell in [a for a in sys.argv[1:] if not a.startswith("--")]:
        nps = np_by_id(cell)
        tot = {k: Counter() for k in Bs + ["real"]}
        rewrite_tok = Counter()
        for cap in sorted(glob.glob(f"{ROOT}/{cell}/runs/*/*.requests.jsonl.xz")):
            lead = session_calls(cap)
            if len(lead) < 2:
                continue
            res, ratio = replay_session(lead, nps, Bs)
            for k, v in res.items():
                tot[k].update(v)
                rewrite_tok[k] += v["rewrite_chars"] * ratio
        print(f"\n{cell}: lead calls {tot['real']['calls']} in {tot['real']['segments']} growing segments")
        print("| policy | breaks per 100 calls | rewritten tokens (M) | vs as run | mean / max prompt (k chars) |")
        print("|---|---|---|---|---|")
        base = rewrite_tok["real"] or 1
        r = tot["real"]
        print(f"| as recorded | {100 * r['breaks'] / max(1, r['calls']):.1f} | {rewrite_tok['real'] / 1e6:.1f} | 1.00 | — |")
        for B in Bs:
            t = tot[B]
            print(f"| B={B[0]}, keep {B[1]}, clear to {B[2] if len(B) > 2 else 0.8} x limit | {100 * t['breaks'] / max(1, t['calls']):.1f} | {rewrite_tok[B] / 1e6:.1f} | "
                  f"{rewrite_tok[B] / base:.2f} | {t['size_sum'] / max(1, t['n']) / 1e3:.0f} / {t['size_max'] / 1e3:.0f} |")


if __name__ == "__main__":
    main()
