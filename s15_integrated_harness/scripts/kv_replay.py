#!/usr/bin/env python3
"""Replay a dumped s15 lead session on a local model and compare KV-cache policies at every compaction edit.

    numactl --cpunodebind=0 --membind=0 python3 kv_replay.py --requests <trace>.requests.jsonl \
        --model Qwen/Qwen2.5-1.5B-Instruct --threads 64 --out <label>.replay.jsonl

Every lead request k is rendered into Qwen chat segments (kv_render.py).  Between request k-1 (plus
the model's actual response R_{k-1}) and request k, the harness may have edited the middle of the
prompt (micro_compact placeholders, snip_compact archive marker, summary compaction) and appended a
tail (tool results, reminders).  For each step the following caches are built:

    recompute   what a provider does: reuse the common prefix, recompute from the first changed token
    shift       keep the stale KV of everything after the edit, RoPE-rotate keys to compacted positions
    gap         keep the stale KV at its old positions; new tokens continue after the largest position
    shift1      like shift but from an exact (recompute) cache, i.e. one edit of staleness only
    oracle      the uncompacted prompt (previous prompt + tail, nothing evicted) -- the information ceiling

shift/gap compound across the run (a stale cache is spliced again at the next edit), which is how a
deployed system would behave.  Metrics per step: tokens computed, first-token KL against recompute and
oracle, teacher-forced NLL of the real (GLM) response, greedy behaviour (does the model re-fetch the
evicted content?), content probes about the evicted text, and stale-vs-recomputed KV deviation.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import random
import re
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kv_render import GEN_PROMPT, Segment, flatten, render_assistant, render_request, tokenize_segments  # noqa: E402
from kv_splice import CacheState, SpliceEngine  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PLACEHOLDER_RE = re.compile(r"\[Earlier tool result saved at (.+?)\]")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(\S.*)$")
FILE_ARG_RE = re.compile(r"((?:[\w.-]+/)*[\w.-]+\.(?:md|py|txt|json|html|sh|yaml|yml|toml))")


# -- helpers -------------------------------------------------------------------------------------
def kl(p_logits: torch.Tensor, q_logits: torch.Tensor) -> float:
    p = torch.log_softmax(p_logits, -1)
    q = torch.log_softmax(q_logits, -1)
    return float(torch.sum(p.exp() * (p - q)))


def entropy(logits: torch.Tensor) -> float:
    p = torch.log_softmax(logits, -1)
    return float(-torch.sum(p.exp() * p))


def norm_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[`*_\[\]()#>|:;.,!?\"'“”‘’—–-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_tool_call(text: str) -> dict | None:
    """Name and arguments of the first <tool_call>; tolerant of JSON cut off by the decode budget."""
    if "<tool_call>" not in text:
        return None
    body = text.split("<tool_call>", 1)[1]
    body = body.split("</tool_call>", 1)[0]
    m = re.search(r"\{.*\}", body, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return {"name": obj.get("name"), "arguments": obj.get("arguments")}
        except json.JSONDecodeError:
            pass
    name = re.search(r'"name"\s*:\s*"([^"]+)"', body)
    args = body.split('"arguments"', 1)[1] if '"arguments"' in body else body
    return {"name": name.group(1) if name else None, "arguments": args.strip()[:400], "partial": True}


def label_of(tool: str | None, tool_input) -> str:
    """Short human label of what a tool result contained (a path for reads, the command for bash)."""
    if not isinstance(tool_input, dict):
        return f"{tool}"
    if tool in ("read_file", "read_files"):
        return str(tool_input.get("path") or tool_input.get("files"))
    if tool == "bash":
        return str(tool_input.get("command", ""))[:160]
    if tool == "glob":
        return f"glob {tool_input.get('pattern')}"
    return f"{tool} {json.dumps(tool_input)[:100]}"


def files_in(text: str) -> set[str]:
    return set(FILE_ARG_RE.findall(text or ""))


def targets(call: dict | None, evicted: list[dict]) -> list[str]:
    """Which evicted items a tool call re-fetches (by file path or by spill-file path)."""
    if not call:
        return []
    args = json.dumps(call.get("arguments"), ensure_ascii=False) if not isinstance(call.get("arguments"), str) else call["arguments"]
    hits = []
    for item in evicted:
        if item.get("spill") and item["spill"] in args:
            hits.append(item["label"])
            continue
        for f in item.get("files", []):
            if f in args:
                hits.append(item["label"])
                break
    return hits


# -- probes --------------------------------------------------------------------------------------
class ProbeMaker:
    def __init__(self, repo: Path, seed: int = 0):
        self.repo = repo
        self.negatives = []
        for path in sorted(repo.glob("s[0-9][0-9]_*/README.md")):
            try:
                self.negatives.append((str(path.relative_to(repo)), path.read_text(encoding="utf-8")))
            except OSError:
                pass
        self.seed = seed

    @staticmethod
    def candidate_lines(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            s = line.strip()
            if not (50 <= len(s) <= 170) or s.startswith(("#", "|", "```", "<!--", "![", "[English]")):
                continue
            words = s.split()
            alpha = sum(ch.isalpha() or ch.isspace() for ch in s) / len(s)
            if len(words) >= 9 and alpha >= 0.7:
                out.append(s)
        return out

    def make(self, item: dict, context_text: str) -> list[dict]:
        """Probes about one evicted result. Every probe carries a `truth` text scored by teacher-forced
        NLL (works for any model, no compliance needed); some add a short greedy decode or a forced choice."""
        text = item["content"]
        rng = random.Random(hashlib.sha1((item["tool_use_id"] + str(self.seed)).encode()).hexdigest())
        label = item["label"]
        what = f"the file {item['files'][0]}" if item.get("files") else f"the output of `{label}`"
        probes = []
        headings = [m.group(1).strip() for m in (HEADING_RE.match(l) for l in text.splitlines()) if m]
        headings = [h for h in headings if len(norm_text(h)) >= 4]
        if len(headings) >= 2:
            probes.append({"kind": "headings",
                           "prompt": (f"Earlier in this conversation you saw {what}. Without calling any tool, list the "
                                      f"markdown section headings that appeared in it, one per line, in order."),
                           "truth": "\n".join(headings), "n_true": len(headings), "greedy": 0})
        lines = self.candidate_lines(text)
        if lines:
            cloze_line = rng.choice(lines)
            words = cloze_line.split()
            cut = max(4, int(len(words) * 0.55))
            probes.append({"kind": "cloze",
                           "prompt": (f"Earlier in this conversation you saw {what}. Without calling any tool, complete this "
                                      f"sentence from it exactly as it appeared, writing only the missing words:\n"
                                      f"\"{' '.join(words[:cut])}"),
                           "truth": " ".join(words[cut:]), "greedy": 24})
            others = [l for l in lines if l != cloze_line] or lines
            pos_line = rng.choice(others).split()
            start = rng.randrange(0, max(1, len(pos_line) - 6))
            pos_phrase = " ".join(pos_line[start:start + 6])
            probes.append({"kind": "presence", "polarity": "yes",
                           "prompt": (f"Earlier in this conversation you saw {what}. Without calling any tool: does it "
                                      f"contain the exact phrase \"{pos_phrase}\"? Answer yes or no."),
                           "truth": "Yes", "choice": True, "greedy": 0})
            ctx_norm = norm_text(context_text)
            neg_phrase = None
            for _ in range(60):
                _, neg_text = rng.choice(self.negatives) if self.negatives else (None, "")
                cands = self.candidate_lines(neg_text)
                if not cands:
                    continue
                w = rng.choice(cands).split()
                st = rng.randrange(0, max(1, len(w) - 6))
                phrase = " ".join(w[st:st + 6])
                if norm_text(phrase) and norm_text(phrase) not in ctx_norm and norm_text(phrase) not in norm_text(text):
                    neg_phrase = phrase
                    break
            if neg_phrase:
                probes.append({"kind": "presence", "polarity": "no",
                               "prompt": (f"Earlier in this conversation you saw {what}. Without calling any tool: does it "
                                          f"contain the exact phrase \"{neg_phrase}\"? Answer yes or no."),
                               "truth": "No", "choice": True, "greedy": 0})
        # verbatim copy: 40 tokens of a random paragraph given its first 20 tokens (pure memory test)
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.split()) >= 45 and not p.strip().startswith("```")]
        if paras:
            para = rng.choice(paras)
            w = para.split()
            probes.append({"kind": "copy",
                           "prompt": (f"Earlier in this conversation you saw {what}. Without calling any tool, continue this "
                                      f"passage from it verbatim:\n{' '.join(w[:15])}"),
                           "truth": " ".join(w[15:45]), "greedy": 0})
        return probes

    @staticmethod
    def score(probe: dict, output: str) -> dict:
        call = parse_tool_call(output)
        if probe["kind"] == "headings":
            out_norm = norm_text(output)
            hit = [h for h in probe["truth"] if norm_text(h) in out_norm]
            lines = [norm_text(l.lstrip("#-*0123456789. ")) for l in output.splitlines() if l.strip()]
            lines = [l for l in lines if l]
            good = sum(1 for l in lines if any(norm_text(h) in l or l in norm_text(h) for h in probe["truth"] if len(l) >= 4))
            return {"recall": len(hit) / len(probe["truth"]), "precision": (good / len(lines)) if lines else 0.0,
                    "n_true": len(probe["truth"]), "n_out": len(lines), "tool_call": bool(call)}
        if probe["kind"] == "cloze":
            truth_w = norm_text(probe["truth"]).split()
            out_w = norm_text(output.split("\n")[0] if output.strip() else "").split()[: len(truth_w) + 3]
            sm = difflib.SequenceMatcher(None, truth_w, out_w, autojunk=False)
            lcs = sum(b.size for b in sm.get_matching_blocks())
            return {"lcs_ratio": lcs / max(1, len(truth_w)), "prefix3": out_w[:3] == truth_w[:3] and len(truth_w) >= 3,
                    "tool_call": bool(call)}
        if probe["kind"] == "presence":
            m = re.search(r"\b(yes|no)\b", output.lower())
            ans = m.group(1) if m else None
            return {"answer": ans, "correct": ans == probe["truth"], "tool_call": bool(call)}
        return {}


# -- main replay ---------------------------------------------------------------------------------
def seg_ops(old_segs: list[Segment], new_segs: list[Segment]):
    """Segment-level diff translated to token-level opcodes."""
    old_keys = [tuple(s.ids) for s in old_segs]
    new_keys = [tuple(s.ids) for s in new_segs]
    sm = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    old_off = [0]
    for s in old_segs:
        old_off.append(old_off[-1] + len(s.ids))
    new_off = [0]
    for s in new_segs:
        new_off.append(new_off[-1] + len(s.ids))
    seg_level = sm.get_opcodes()
    token_level = [(t, old_off[i1], old_off[i2], new_off[j1], new_off[j2]) for t, i1, i2, j1, j2 in seg_level]
    return seg_level, token_level


def kv_deviation(a: CacheState, b: CacheState, ranges: list[tuple[int, int]], eng: SpliceEngine | None = None) -> dict:
    """Cosine similarity of a's keys/values to b's at the same physical slots (reused blocks only).
    Keys are first rotated to b's RoPE positions, so a gap-mode cache is compared on content, not on
    the position offset it deliberately keeps."""
    if not ranges:
        return {}
    kc, vc, low = [], [], []
    for la, lb in zip(a.cache.layers, b.cache.layers):
        ks, vs = [], []
        for s, e in ranges:
            ka = la.keys[0, :, s:e]
            deltas = torch.tensor([b.pos[i] - a.pos[i] for i in range(s, e)])
            if eng is not None and bool((deltas != 0).any()):
                cos, sin = eng.rotary(ka, deltas[None, :])                 # [1, n, hd]
                ka = ka * cos[0][None] + __import__("kv_splice").rotate_half(ka) * sin[0][None]
            ks.append(torch.nn.functional.cosine_similarity(ka, lb.keys[0, :, s:e], dim=-1).mean(0))
            vs.append(torch.nn.functional.cosine_similarity(la.values[0, :, s:e], lb.values[0, :, s:e], dim=-1).mean(0))
        k = torch.cat(ks)
        v = torch.cat(vs)
        kc.append(float(k.mean()))
        vc.append(float(v.mean()))
        low.append(float((k < 0.9).float().mean()))
    return {"key_cos_by_layer": [round(x, 4) for x in kc], "value_cos_by_layer": [round(x, 4) for x in vc],
            "key_cos_mean": sum(kc) / len(kc), "value_cos_mean": sum(vc) / len(vc),
            "frac_tokens_key_cos_below_0.9": sum(low) / len(low), "tokens": sum(e - s for s, e in ranges)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True)
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--max-context", type=int, default=30000, help="stop when a request exceeds this many tokens")
    ap.add_argument("--decode-tokens", type=int, default=64)
    ap.add_argument("--probe-conditions", default="recompute,shift,gap,oracle")
    ap.add_argument("--probes-per-step", type=int, default=1, help="evicted items probed per edit step (largest first)")
    ap.add_argument("--min-probe-chars", type=int, default=800)
    ap.add_argument("--no-behaviour", action="store_true", help="skip greedy decodes of the next action")
    ap.add_argument("--conditions", default="recompute,shift,gap,shift1,oracle")
    ap.add_argument("--keep-timestamp", action="store_true",
                    help="keep the per-second 'Current time' line (otherwise fixed, so the only edits are the harness's compactions)")
    args = ap.parse_args()

    conds = args.conditions.split(",")
    probe_conds = [c for c in args.probe_conditions.split(",") if c in conds]
    eng = SpliceEngine(args.model, threads=args.threads)
    tok = eng.tok
    G = tok(GEN_PROMPT, add_special_tokens=False).input_ids
    im_end = tok.convert_tokens_to_ids("<|im_end|>")
    stop_ids = {im_end, tok.convert_tokens_to_ids("<|endoftext|>"), tok.convert_tokens_to_ids("</tool_call>")}
    probes = ProbeMaker(REPO)

    recs = [json.loads(l) for l in open(args.requests, encoding="utf-8")]
    lead = sorted([r for r in recs if r.get("purpose") == "lead"], key=lambda r: r["call_index"])
    if args.max_steps:
        lead = lead[: args.max_steps]
    out = open(args.out, "w", encoding="utf-8")
    meta = {"type": "meta", "requests": args.requests, "model": args.model, "steps": len(lead), "conditions": conds,
            "probe_conditions": probe_conds, "decode_tokens": args.decode_tokens, "ts": time.time()}
    out.write(json.dumps(meta) + "\n")
    out.flush()

    states: dict[str, CacheState] = {}
    persistent = [c for c in conds if c in ("recompute", "shift", "gap")]   # carried across steps
    transient = [c for c in conds if c in ("shift1", "oracle")]           # rebuilt from recompute on edit steps only
    diverged = False                                                        # shift/gap differ from recompute after the first edit
    prev_segs: list[Segment] | None = None
    evicted_so_far: list[dict] = []          # every item ever evicted, for re-acquisition bookkeeping
    total_t0 = time.time()

    for step, req in enumerate(lead):
        t_step = time.time()
        if not args.keep_timestamp:
            req["system"] = re.sub(r"Current time: \S+", "Current time: 2026-09-11T00:00:00", req["system"])
        new_segs = tokenize_segments(tok, render_request(req))
        new_ids = flatten(new_segs)
        resp_seg = render_assistant(req["response"]["content"])
        tokenize_segments(tok, [resp_seg])
        record = {"type": "step", "step": step, "call_index": req["call_index"], "tokens_new": len(new_ids),
                  "messages": len(req["messages"]), "stop_reason": req["response"]["stop_reason"],
                  "glm_usage": req["response"].get("usage")}
        if len(new_ids) > args.max_context:
            record.update({"skipped": f"context {len(new_ids)} tokens exceeds --max-context"})
            out.write(json.dumps(record) + "\n")
            out.flush()
            break

        # -- describe the real response (what glm did at this step) ---------------------------
        real_calls = [b for b in req["response"]["content"] if b.get("type") == "tool_use"]
        record["real_response"] = {
            "tools": [c.get("name") for c in real_calls],
            "labels": [label_of(c.get("name"), c.get("input")) for c in real_calls],
            "text_chars": sum(len(b.get("text", "")) for b in req["response"]["content"] if b.get("type") == "text"),
            "reacquires": sorted({t for c in real_calls for t in targets({"name": c.get("name"), "arguments": c.get("input")}, evicted_so_far)}),
            "tokens": len(resp_seg.ids),
        }

        active = list(conds)
        if prev_segs is None:
            st, _ = eng.prefill(new_ids)
            for c in persistent:
                states[c] = eng.clone(st) if c != persistent[0] else st
            active = list(persistent)
            record.update({"edit": False, "tokens_fresh": {c: len(new_ids) for c in persistent}, "tokens_reused": {c: 0 for c in persistent}})
        else:
            seg_level, token_level = seg_ops(prev_segs, new_segs)
            # tail = trailing insert; edits = everything else that is not equal
            tail_op = None
            if seg_level and seg_level[-1][0] == "insert":
                tail_op = seg_level[-1]
            edits = [op for op in seg_level[:-1 if tail_op else None] if op[0] != "equal"]
            evicted_now = []
            for tag, i1, i2, j1, j2 in edits:
                for s in prev_segs[i1:i2]:
                    if s.kind == "tool_result" and not s.meta.get("placeholder") and s.meta.get("chars", 0) > 120:
                        body = s.text.removeprefix("\n<tool_response>\n").removesuffix("\n</tool_response>")
                        spill = None
                        for n in new_segs[j1:j2]:
                            if n.kind == "tool_result" and n.meta.get("tool_use_id") == s.meta.get("tool_use_id"):
                                m = PLACEHOLDER_RE.match(n.text.removeprefix("\n<tool_response>\n"))
                                spill = m.group(1) if m else None
                        item = {"tool_use_id": s.meta.get("tool_use_id"), "tool": s.meta.get("tool"),
                                "label": label_of(s.meta.get("tool"), s.meta.get("input")),
                                "files": sorted(files_in(label_of(s.meta.get("tool"), s.meta.get("input")))),
                                "chars": len(body), "tokens": len(s.ids), "spill": spill, "content": body}
                        evicted_now.append(item)
                    elif s.kind not in ("tool_result",) and tag == "replace":
                        pass
            edit_kinds = []
            for tag, i1, i2, j1, j2 in edits:
                kinds_new = {n.kind for n in new_segs[j1:j2]}
                texts_new = " ".join(n.text[:60] for n in new_segs[j1:j2])
                if "[Compacted]" in texts_new or "[Reactive compact]" in texts_new:
                    edit_kinds.append("summary")
                elif "messages archived at" in texts_new:
                    edit_kinds.append("snip")
                elif tag == "replace" and any(n.meta.get("placeholder") for n in new_segs[j1:j2]):
                    edit_kinds.append("placeholder")
                else:
                    edit_kinds.append(f"{tag}:{','.join(sorted(kinds_new))}")
            first_change = min((tl[3] for tl, sl in zip(token_level, seg_level) if sl[0] != "equal"), default=len(new_ids))
            record.update({"edit": bool(edits), "edit_kinds": edit_kinds, "n_edits": len(edits),
                           "tail_tokens": (token_level[-1][4] - token_level[-1][3]) if tail_op else 0,
                           "first_change_token": first_change,
                           "tokens_after_first_change": len(new_ids) - first_change,
                           "evicted": [{k: v for k, v in it.items() if k != "content"} for it in evicted_now]})
            evicted_so_far.extend({k: v for k, v in it.items() if k != "content"} for it in evicted_now)

            new_states: dict[str, CacheState] = {}
            stats_all = {}
            if edits:
                diverged = True
            active = list(conds) if edits else list(persistent)
            if not diverged:
                # nothing has been spliced yet: every persistent state is identical, compute once and clone
                t0 = time.time()
                new_states["recompute"], stats_all["recompute"] = eng.splice(states["recompute"], new_ids, "recompute", ops=token_level)
                stats_all["recompute"]["seconds"] = round(time.time() - t0, 2)
                for c in persistent:
                    if c != "recompute":
                        new_states[c] = eng.clone(new_states["recompute"])
                        stats_all[c] = dict(stats_all["recompute"])
                active = list(persistent)
            for c in (active if diverged else []):
                t0 = time.time()
                if c == "recompute":
                    new_states[c], stats_all[c] = eng.splice(states["recompute"], new_ids, "recompute", ops=token_level)
                elif c == "shift":
                    new_states[c], stats_all[c] = eng.splice(states["shift"], new_ids, "shift", ops=token_level)
                elif c == "gap":
                    new_states[c], stats_all[c] = eng.splice(states["gap"], new_ids, "gap", ops=token_level)
                elif c == "shift1":
                    new_states[c], stats_all[c] = eng.splice(states["recompute"], new_ids, "shift", ops=token_level)
                elif c == "oracle":
                    record["oracle_valid"] = bool(edits and tail_op)
                    if edits and tail_op:
                        tail_ids = new_ids[token_level[-1][3]: token_level[-1][4]]
                        o_ids = states["recompute"].ids + tail_ids
                        o_ops = [("equal", 0, len(states["recompute"].ids), 0, len(states["recompute"].ids)),
                                 ("insert", len(states["recompute"].ids), len(states["recompute"].ids), len(states["recompute"].ids), len(o_ids))]
                        new_states[c], stats_all[c] = eng.splice(states["recompute"], o_ids, "recompute", ops=o_ops)
                    else:
                        new_states[c], stats_all[c] = eng.splice(states["recompute"], new_ids, "recompute", ops=token_level)
                stats_all[c]["seconds"] = round(time.time() - t0, 2)
            states = new_states
            record["tokens_fresh"] = {c: stats_all[c]["fresh"] for c in active}
            record["tokens_reused"] = {c: stats_all[c]["reused"] for c in active}
            record["splice_seconds"] = {c: stats_all[c]["seconds"] for c in active}
            if edits and "shift" in active:
                record["shift_blocks"] = stats_all["shift"]["blocks"]
                reuse_ranges = [(b["new"][0], b["new"][1]) for b in stats_all["shift"]["blocks"] if b["op"] == "reuse" and b["new"][0] >= first_change]
                record["kv_deviation"] = {c: kv_deviation(states[c], states["recompute"], reuse_ranges, eng) for c in ("shift", "shift1", "gap") if c in active}
                if "gap" in conds:
                    record["gap_max_position"] = max(states["gap"].pos)

        # -- behaviour: greedy next action under every condition ---------------------------------
        if not args.no_behaviour and (record.get("edit") or step == 0):
            beh = {}
            for c in active:
                t0 = time.time()
                ids = eng.greedy(states[c], G, max_new=args.decode_tokens, stop_ids=stop_ids)
                text = tok.decode(ids, skip_special_tokens=False)
                call = parse_tool_call(text)
                beh[c] = {"text": text[:400], "tool": call.get("name") if call else None,
                          "reacquires": targets(call, record.get("evicted", []) or []),
                          "reacquires_any_evicted": targets(call, evicted_so_far), "seconds": round(time.time() - t0, 1)}
            record["behaviour"] = beh

        # -- first-token distribution and teacher-forced NLL of the real response ---------------
        first_logits = {}
        nll = {}
        tf_logp = {}
        r_ids = resp_seg.ids
        has_body = r_ids[: len(G)] == G and len(r_ids) > len(G)
        for c in active:
            logits = eng.extend(states[c], G, keep_logits="last")
            first_logits[c] = logits[-1]
            eng.crop(states[c], len(G))
            if has_body:
                logits = eng.extend(states[c], r_ids, keep_logits="all")
                lp = torch.log_softmax(logits[len(G) - 1: -1], -1)
                tgt = torch.tensor(r_ids[len(G):])
                tok_lp = lp[torch.arange(len(tgt)), tgt]
                tf_logp[c] = lp
                nll[c] = {"mean_nll": float(-tok_lp.mean()), "sum_nll": float(-tok_lp.sum()), "n": int(len(tgt)),
                          "argmax_agree_real": float((lp.argmax(-1) == tgt).float().mean()),
                          "first_token_nll": float(-tok_lp[0])}
            else:
                eng.extend(states[c], r_ids, keep_logits="none")
        if has_body and "recompute" in tf_logp:
            ref_lp = tf_logp["recompute"]
            for c in active:
                lp = tf_logp[c]
                nll[c]["tf_kl_from_recompute"] = float(torch.sum(ref_lp.exp() * (ref_lp - lp), -1).mean())
                nll[c]["tf_top1_agree_recompute"] = float((lp.argmax(-1) == ref_lp.argmax(-1)).float().mean())
                if "oracle" in tf_logp:
                    olp = tf_logp["oracle"]
                    nll[c]["tf_kl_from_oracle"] = float(torch.sum(olp.exp() * (olp - lp), -1).mean())
                    nll[c]["tf_top1_agree_oracle"] = float((lp.argmax(-1) == olp.argmax(-1)).float().mean())
        del tf_logp
        record["nll"] = nll
        ref = first_logits.get("recompute")
        record["first_token"] = {c: {"top1": tok.decode([int(first_logits[c].argmax())]), "entropy": entropy(first_logits[c]),
                                     "kl_from_recompute": kl(ref, first_logits[c]) if ref is not None else None,
                                     "kl_from_oracle": kl(first_logits["oracle"], first_logits[c]) if "oracle" in first_logits else None}
                                 for c in active}
        record["conditions_active"] = active

        # -- probes about the evicted content ----------------------------------------------------
        if record.get("edit") and record.get("evicted"):
            items = sorted([it for it in evicted_now if it["chars"] >= args.min_probe_chars], key=lambda it: -it["chars"])[: args.probes_per_step]
            context_text = req["system"] + json.dumps(req["messages"], ensure_ascii=False)
            probe_records = []
            yes_ids = [tok(v, add_special_tokens=False).input_ids[0] for v in ("Yes", "yes", " Yes", " yes")]
            no_ids = [tok(v, add_special_tokens=False).input_ids[0] for v in ("No", "no", " No", " no")]
            for item in items:
                for probe in probes.make(item, context_text):
                    res = {"item": item["label"], "kind": probe["kind"], "polarity": probe.get("polarity"),
                           "truth": probe["truth"][:120], "n_true": probe.get("n_true"), "outputs": {}}
                    pseg = Segment(f"<|im_start|>user\n{probe['prompt']}<|im_end|>\n" + GEN_PROMPT, "probe")
                    tokenize_segments(tok, [pseg])
                    truth_ids = tok(probe["truth"], add_special_tokens=False).input_ids
                    for c in [c for c in probe_conds if c in active]:
                        # the probe is asked as the next user turn after R_k, as a follow-up question would arrive
                        logits = eng.extend(states[c], pseg.ids + truth_ids, keep_logits="all")
                        lp = torch.log_softmax(logits[len(pseg.ids) - 1: -1], -1)
                        tgt = torch.tensor(truth_ids)
                        tok_lp = lp[torch.arange(len(tgt)), tgt]
                        o = {"nll": float(-tok_lp.mean()), "nll_first": float(-tok_lp[0]), "n": int(len(tgt)),
                             "argmax_agree": float((lp.argmax(-1) == tgt).float().mean())}
                        if probe.get("choice"):
                            first = torch.log_softmax(logits[len(pseg.ids) - 1], -1)
                            p_yes = float(torch.logsumexp(first[yes_ids], 0))
                            p_no = float(torch.logsumexp(first[no_ids], 0))
                            o.update({"margin_yes": p_yes - p_no, "choice": "Yes" if p_yes > p_no else "No",
                                      "correct": (p_yes > p_no) == (probe["truth"] == "Yes")})
                        eng.crop(states[c], len(pseg.ids) + len(truth_ids))
                        if probe.get("greedy"):
                            ids = eng.greedy(states[c], pseg.ids, max_new=probe["greedy"], stop_ids=stop_ids)
                            text = tok.decode(ids, skip_special_tokens=True)
                            o["text"] = text[:200]
                            if probe["kind"] == "cloze":
                                o.update(ProbeMaker.score({"kind": "cloze", "truth": probe["truth"]}, text))
                        res["outputs"][c] = o
                    probe_records.append(res)
            record["probes"] = probe_records

        # -- advance: states now contain body_k + R_k ------------------------------------------
        for c in transient:
            states.pop(c, None)
        prev_segs = new_segs + [resp_seg]
        record["seconds"] = round(time.time() - t_step, 1)
        record["elapsed_total"] = round(time.time() - total_t0, 1)
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        print(f"[replay] step {step} call={req['call_index']} tokens={len(new_ids)} edit={record.get('edit')} "
              f"kinds={record.get('edit_kinds')} fresh={record.get('tokens_fresh')} {record['seconds']}s", flush=True)
    out.close()


if __name__ == "__main__":
    main()
