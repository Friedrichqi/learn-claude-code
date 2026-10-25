#!/usr/bin/env python3
"""Selftests of a running EPIC server (epic/, harness_eval port), through its HTTP API.

    python3 epic/selftest_live.py --url http://127.0.0.1:PORT --model minimax-m2.7-epic [--out runs/epic/selftest.json]

Synthetic sessions are built from token ids (the model's role-start token as block delimiter: MiniMax ]~b],
Qwen ChatML <|im_start|>; --format) and sent to /v1/completions with kv_transfer_params.epic tags, greedy
decoding.
  S1 exactness at delta 0: an append-only 3-call session. `shift` reuses rows from the pool at unchanged
     positions (the tail of each previous sequence past the 16-token-aligned prefix), so its greedy outputs
     must equal `prefix`'s token for token; `epicinf` (every reused run recomputed) likewise.
  S2 a large snip: a session whose 2nd call drops a middle chunk of >= 8000 tokens (a shift that big is
     always shadow-probed), served by shift / epic128 / epic128a1 next to prefix: no errors, rows reused,
     a shadow probe whose L1 covers every TP rank, and the reused rows' K deviation within bounds.
  S3 every TP rank applied the same overrides (per-rank connector counters, /epic/workers), with one hook per
     attention layer (62 for MiniMax-M2.7, 16 for Qwen3.8).
  S4 ending the sessions returns every pool slot.
  S5 (e4, minimax only) the recompute arm epic128rjsd on a two-edit history with 1-token replies (so every arm's
     prompts are identical): complete, bounded decision records; u common to the sessions of a seed key; the
     unforced rule recompute == (u < m); the decision's E probe reproduces the served call and epic128's; its O
     probe equals shift at the first edit; a forced recomputation serves [h, P) fresh under a new salt and
     matches a fresh prefill; h is the running minimum of p0 and restarts after a recomputation; the noise floor
     is logged; the pool's reference counts match the live entries.
  S6 shadow probes carry the partition metrics (first_klp / first_jsd / first_tvd / first_exp).
Writes a JSON report; exits non-zero if a check fails.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

FORMATS = {
    # MiniMax-M2: ]~!b[ once, then ]~b]role\n text per message (no end token); the assistant role is "ai"
    "minimax": {"role_start": 200019, "bos": [200034], "end": "", "ai": "ai", "model": "minimax-m2.7-epic",
                "model_dir": "/data/fritz/hf/hub/models--MiniMaxAI--MiniMax-M2.7", "ranks": 4, "layers": 62},
    # Qwen ChatML: <|im_start|>role\n text <|im_end|>\n
    "qwen": {"role_start": 248045, "bos": [], "end": "<|im_end|>\n", "ai": "assistant", "model": "qwen3.8-27b-epic",
             "model_dir": "/data/fritz/hf/hub/models--Qwen--Qwen3.8-27B-FP8", "ranks": 1, "layers": 16},
    # DeepSeek-V4 (epic_dsv4, design B): synthetic blocks all start with <｜User｜> (the planner's one delimiter);
    # no shadow probes (they read per-head K rows), so S2 checks reuse and the first-token distribution instead;
    # a smaller S2 snip keeps the [43, S, 4096] attention-input pool small
    "dsv4": {"role_start": 128803, "bos": [0], "end": "", "ai": "Assistant", "model": "deepseek-v4-flash-epic",
             "model_dir": "/data/fritz/hf/hub/models--deepseek-ai--DeepSeek-V4-Flash-0731", "ranks": 4, "layers": 43,
             "probes": False, "big_chunks": 10, "noisy": True},
}
ROLE_START = FORMATS["minimax"]["role_start"]
BOS = FORMATS["minimax"]["bos"][0]
END = ""
AI = "ai"


def post(url: str, body: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(url: str, timeout: int = 120) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class Tok:
    def __init__(self, model_dir: str):
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(sorted(glob.glob(f"{model_dir}/snapshots/*/tokenizer.json"))[0])

    def block(self, role: str, text: str) -> list[int]:
        return [ROLE_START] + self.tok.encode(f"{role}\n{text}{END}", add_special_tokens=False).ids

    def ids(self, text: str) -> list[int]:
        return self.tok.encode(text, add_special_tokens=False).ids


def corpus(n_chunks: int, seed: int) -> list[str]:
    """Real text to fill messages: chunks of Python source from the vLLM install."""
    files = sorted(glob.glob("/data/fritz/venv/lib/python3.12/site-packages/vllm/v1/core/*.py"))
    rng = random.Random(seed)
    out = []
    while len(out) < n_chunks:
        text = Path(rng.choice(files)).read_text(errors="replace")
        a = rng.randrange(0, max(1, len(text) - 6000))
        out.append(text[a:a + 4000])
    return out


class Session:
    def __init__(self, base: str, model: str, arm: str, spec: str, seed_key: str):
        self.base, self.model, self.arm, self.spec, self.seed_key = base, model, arm, spec, seed_key
        self.n = 0
        self.outputs: list[list[int]] = []
        self.texts: list[str] = []
        self.first: list[dict] = []
        self.prompts: list[list[int]] = []

    def call(self, prompt: list[int], max_tokens: int = 48, extra: dict | None = None,
             token_ids: bool = False) -> list[int]:
        body = {"model": self.model, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0.0,
                "logprobs": 20, "return_token_ids": True, "kv_transfer_params": {"epic": {
                    "arm": self.arm, "spec": self.spec, "call": self.n, "seed_key": self.seed_key, **(extra or {})}}}
        if token_ids:                       # top_logprobs keyed "token_id:<id>", comparable with the server's ids
            body["return_tokens_as_token_ids"] = True
        out = post(f"{self.base}/v1/completions", body)
        choice = out["choices"][0]
        ids = choice.get("token_ids") or []
        self.outputs.append(list(ids))
        self.texts.append(choice.get("text") or "")
        tops = ((choice.get("logprobs") or {}).get("top_logprobs") or [{}])[0] or {}
        if token_ids:
            def tid(k):                     # "token_id:123" -> "123"; anything else stays as is (the check then fails)
                t = str(k).rsplit(":", 1)[-1]
                return t if t.isdigit() else str(k)
            self.first.append({tid(k): float(v) for k, v in tops.items()})
        else:
            self.first.append({k: float(v) for k, v in tops.items()})
        self.prompts.append(list(prompt))
        self.n += 1
        return list(ids)

    def end(self) -> dict:
        return get(f"{self.base}/epic/end?spec={self.spec}")


def run_session(base, model, arm, spec, prompts_fn, n_calls):
    s = Session(base, model, arm, spec, seed_key="selftest")
    prev_reply: list[int] = []
    for i in range(n_calls):
        prompt = prompts_fn(i, prev_reply)
        prev_reply = s.call(prompt)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--format", default="minimax", choices=sorted(FORMATS))
    ap.add_argument("--model", default="")
    ap.add_argument("--model-dir", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--ranks", type=int, default=0)
    ap.add_argument("--layers", type=int, default=0)
    ap.add_argument("--out", default="/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval/runs/epic/selftest.json")
    a = ap.parse_args()
    fmt = FORMATS[a.format]
    global ROLE_START, BOS, END, AI
    ROLE_START, END, AI = fmt["role_start"], fmt["end"], fmt["ai"]
    a.model = a.model or fmt["model"]
    a.model_dir = a.model_dir or fmt["model_dir"]
    a.log = a.log or f"/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval/runs/epic/{a.model}.calls.jsonl"
    a.ranks = a.ranks or fmt["ranks"]
    a.layers = a.layers or fmt["layers"]
    tok = Tok(a.model_dir)
    end_ids = tok.ids(END) if END else []
    texts = corpus(40, seed=7)
    tag = time.strftime("%H%M%S")
    report: dict = {"checks": {}}
    ok = True

    def check(name, cond, **info):
        nonlocal ok
        report["checks"][name] = {"ok": bool(cond), **info}
        ok &= bool(cond)
        print(("ok    " if cond else "FAIL  ") + name, json.dumps(info)[:300], flush=True)

    def dx_now() -> tuple[float, float]:
        """DeepSeek-V4 connector: rank 0's summed squared deviation of computed vs substituted attention inputs."""
        try:
            w = get(f"{a.url}/epic/workers").get("workers") or []
            return (float(w[0].get("dx_num") or 0), float(w[0].get("dx_den") or 0)) if w else (0.0, 0.0)
        except Exception:                                           # noqa: BLE001
            return (0.0, 0.0)

    def dx_since(before) -> float | None:
        import math
        n, d = dx_now()
        return math.sqrt((n - before[0]) / (d - before[1])) if d > before[1] else None

    system = list(fmt["bos"]) + tok.block("system", "You are a coding agent. " + texts[0][:1500])
    task = tok.block("user", "Explain what the following code does.\n" + texts[1])

    # S1: append-only session (no edits)
    def s1_prompt(i, prev_reply):
        p = list(system) + list(task)
        for j in range(i):
            p += [ROLE_START] + tok.ids(f"{AI}\n") + s1_replies[j] + end_ids
            p += tok.block("tool", texts[2 + j])
        return p + [ROLE_START] + tok.ids(f"{AI}\n")

    def kl(p: dict, q: dict) -> float:
        import math
        if not p or not q:
            return float("nan")
        floor = min(q.values())
        return sum(math.exp(lp) * (lp - q.get(t, floor)) for t, lp in p.items())

    # Without batch-invariant kernels two identical computations in different batch shapes differ in the last
    # bits, so long greedy outputs can part ways; the check is the first-token distribution of calls whose
    # prompts are identical across arms (top-20 KL against prefix: numerical noise only, if the reused rows
    # are the right ones), using short replies so the prompts stay identical.
    outs = {}
    prefix_replies: list[list[int]] = []
    for arm in ("prefix", "shift", "epicinf"):
        s1_replies: list[list[int]] = []
        before = dx_now() if fmt.get("noisy") else None
        sess = Session(a.url, a.model, arm, f"st1-{arm}-{tag}", "selftest-s1")
        for i in range(3):
            if fmt.get("noisy") and arm != "prefix":
                # noisy model: the arms' own replies part ways, so every arm's prompts carry prefix's replies
                # (identical prompts keep the first-token comparison possible)
                s1_replies[:] = prefix_replies[:i]
            r = sess.call(s1_prompt(i, None), max_tokens=6)
            s1_replies.append(r)
            if arm == "prefix":
                prefix_replies.append(r)
        outs[arm] = sess
        if before is not None and arm == "shift":
            dx = dx_since(before)
            report.setdefault("dx", {})["S1_shift_delta0"] = dx
            check("S1_dx_reused_inputs_match_fresh_at_delta_0", dx is not None and dx < 0.05, dx=dx)
    if fmt.get("noisy"):
        # the same append-only session with 1-token replies: the generated token's row is never reused (the session
        # keeps prompt + reply minus its last token), so every reused row was computed in a PREFILL; against the
        # session above (whose reused rows include decode-time rows) this separates decode-vs-prefill numerics
        before = dx_now()
        sess = Session(a.url, a.model, "shift", f"st1p-shift-{tag}", "selftest-s1p")
        p_replies: list[list[int]] = []
        for i in range(3):
            s1_replies[:] = p_replies[:i]
            p_replies.append(sess.call(s1_prompt(i, None), max_tokens=1))
        outs["shift@p"] = sess
        dx = dx_since(before)
        report.setdefault("dx", {})["S1_shift_delta0_prefill_rows_only"] = dx
        check("S1p_dx_prefill_rows_match_fresh_at_delta_0", dx is not None and dx < 0.05, dx=dx)
    def first_kls(x, y):
        return [kl(outs[x].first[i], outs[y].first[i]) for i in range(1, 3) if outs[x].prompts[i] == outs[y].prompts[i]]

    # the override path itself: shift writes back pool rows at unchanged positions, epicinf recomputes them;
    # both plan the same p0 and loads, so their distributions must agree to numerical noise
    kl_si = first_kls("epicinf", "shift")
    if fmt.get("noisy"):
        # DeepSeek-V4: two recomputes of the same prompt already differ (FP8 cache, top-k indexer selection under
        # batch effects), so substitution at delta 0 must stay within that recompute noise, not below 1e-3
        kl_ep = first_kls("prefix", "epicinf")
        floor = max(kl_ep) if kl_ep else 0.0
        check("S1_shift_substitution_within_recompute_noise_at_delta_0",
              bool(kl_si) and max(kl_si) <= 2 * max(floor, 1e-3),
              kl_shift_vs_epicinf=[round(x, 6) for x in kl_si], kl_epicinf_vs_prefix=[round(x, 6) for x in kl_ep])
    else:
        check("S1_shift_override_equals_recompute_at_delta_0", bool(kl_si) and max(kl_si) < 1e-3,
              kl=[round(x, 6) for x in kl_si])
    # against prefix, which serves the same prompt from vLLM's prefix cache in another split: a looser bound
    for arm in ("shift", "epicinf"):
        k = first_kls("prefix", arm)
        check(f"S1_{arm}_vs_prefix_within_noise", bool(k) and max(k) < 0.05, kl=[round(x, 6) for x in k])

    # S2: a large snip at call 2 (drop >= 8000 tokens of tool output from the middle)
    nbig = fmt.get("big_chunks", 20)
    big = [tok.block("tool", t) for t in texts[5:5 + nbig]]        # ~nbig x ~1000 tokens: a shift >= 8000
    tail = [tok.block("tool", texts[30]), tok.block("tool", texts[31]),
            tok.block("user", "Now summarize the last tool output.")]
    marker = tok.block("user", "[12 messages archived at .transcripts/transcript_00000.jsonl]")
    gen_prompt = [ROLE_START] + tok.ids(f"{AI}\n")

    def s2_prompt(i):
        if i == 0:
            return list(system) + list(task) + [x for b in big for x in b] + [x for b in tail for x in b] + gen_prompt
        return list(system) + list(task) + marker + [x for b in tail for x in b] + gen_prompt + \
            s2_reply[0] + end_ids + tok.block("user", "Continue.") + gen_prompt

    s2 = {}
    if not fmt.get("probes", True):
        # no shadow probes: measure the snip directly. Every arm's 2nd prompt carries the same reply (prefix's), so
        # the prompts are identical and the first-token distributions comparable; a second variant trims the dropped
        # span so the shift is a multiple of 128 (DeepSeek-V4's pooled windows then keep their membership)
        fixed: list[int] = []
        variants = {"": big}
        dropped = sum(len(b) for b in big) - len(marker)
        trim = dropped % 128
        variants["a128"] = big[:-1] + [big[-1][:len(big[-1]) - trim]] if trim else big
        for vname, vbig in variants.items():
            def vprompt(i, vbig=vbig):
                if i == 0:
                    return list(system) + list(task) + [x for b in vbig for x in b] + [x for b in tail for x in b] + gen_prompt
                return list(system) + list(task) + marker + [x for b in tail for x in b] + gen_prompt + \
                    fixed + end_ids + tok.block("user", "Continue.") + gen_prompt
            for arm in (("prefix", "shift", "epic128", "epic128a1") if not vname else ("prefix", "shift")):
                before = dx_now()
                sess = Session(a.url, a.model, arm, f"st2{vname}-{arm}-{tag}", "selftest-s2")
                r0 = sess.call(vprompt(0))
                if arm == "prefix":
                    fixed[:] = r0
                sess.call(vprompt(1))
                s2[f"{arm}{'@' + vname if vname else ''}"] = sess
                if arm != "prefix":
                    dx = dx_since(before)
                    report.setdefault("dx", {})[f"S2{vname}_{arm}"] = dx
                    check(f"S2{vname}_{arm}_dx_sane", dx is not None and dx < 1.0, dx=dx)
            delta = sum(len(b) for b in vbig) - len(marker)
            report.setdefault("s2_variants", {})[vname or "unaligned"] = {"delta": delta, "delta_mod_128": delta % 128}
    else:
        for arm in ("prefix", "shift", "epic128", "epic128a1"):
            s2_reply: list[list[int]] = []
            sess = Session(a.url, a.model, arm, f"st2-{arm}-{tag}", "selftest-s2")
            s2_reply.append(sess.call(s2_prompt(0)))
            sess.call(s2_prompt(1))
            s2[arm] = sess
    time.sleep(2)
    logs = [json.loads(x) for x in Path(a.log).read_text().splitlines() if x.strip()]
    mine = [x for x in logs if str(x.get("spec", "")).endswith(tag)]
    errors = [x for x in logs if x.get("ev") == "error" and str(x.get("spec", "")).endswith(tag)]
    check("S2_no_errors", not errors, n=len(errors), first=(errors[0].get("error") if errors else None))
    for arm in ("shift", "epic128", "epic128a1"):
        recs = [x for x in mine if x.get("ev") == "call" and x.get("arm") == arm and x.get("spec") == f"st2-{arm}-{tag}"]
        second = next((x for x in recs if x.get("call") == 1), {})
        sh = second.get("shadow") or {}
        reused_bins = []
        l1 = sh.get("l1") or {}
        for b, rows in l1.items():
            cls = int(b) // (6 * 5)
            if cls == 1:                                            # reused rows
                dk = sum(r[0] for r in rows)
                nk = sum(r[1] for r in rows)
                reused_bins.append(dk / nk if nk else 0.0)
        dk_reused = max(reused_bins) if reused_bins else None
        if not fmt.get("probes", True):
            for vname in ("", "a128"):
                key = f"{arm}{'@' + vname if vname else ''}"
                if key not in s2:
                    continue
                spec = f"st2{vname}-{arm}-{tag}"
                rec = next((x for x in mine if x.get("ev") == "call" and x.get("spec") == spec and x.get("call") == 1), {})
                ref = s2[f"prefix{'@' + vname if vname else ''}"]
                same = len(ref.prompts) > 1 and len(s2[key].prompts) > 1 and ref.prompts[1] == s2[key].prompts[1]
                k1 = kl(ref.first[1], s2[key].first[1]) if same else None
                check(f"S2{vname}_{arm}_reuses_and_stays_sane", rec.get("S", 0) > 500 and same and k1 is not None
                      and k1 < 2.0, S=rec.get("S"), C=rec.get("C"), max_delta=rec.get("max_delta"),
                      first_kl_vs_prefix=None if k1 is None else round(k1, 6), same_prompt=same)
            continue
        check(f"S2_{arm}_reuses_and_probes", second.get("S", 0) > 500 and sh.get("tp_ranks") == a.ranks
              and not sh.get("error"),
              S=second.get("S"), C=second.get("C"), H=second.get("H"), aged_out=second.get("aged_out"),
              max_delta=second.get("max_delta"), tp_ranks=sh.get("tp_ranks"), first_kl=sh.get("first_kl"),
              tf_kl=sh.get("tf_kl"), dk_reused_max=dk_reused)
        if dk_reused is not None:
            check(f"S2_{arm}_reused_K_deviation_bounded", dk_reused < 0.5, dk_reused_max=dk_reused)
        part = {k: sh.get(k) for k in ("first_klp", "first_jsd", "first_tvd", "first_exp")}
        check(f"S6_{arm}_shadow_partition_metrics", all(isinstance(v, (int, float)) for v in part.values())
              and 0 <= part["first_jsd"] <= 1 and 0 <= part["first_tvd"] <= 1 and 0 <= part["first_exp"] < 1
              and part["first_klp"] >= 0, **part)

    # S5 (e4): the recompute arm. A two-edit history whose replies are fixed text (1-token calls: the entry holds the
    # prompt only), so all six sessions see identical prompts:
    #   call 0  system, task, 20 big tool blocks, tool A, tool B, tool C, question
    #   call 1  the big blocks snipped behind marker 1 (the first edit: p0 just past the task)
    #   call 2  tool B dropped as well (an edit further right, so p0 is larger than at call 1)
    s5 = {}
    if a.format == "minimax":
        reply = [ROLE_START] + tok.ids(f"{AI}\nI will inspect the code and continue.") + end_ids
        tools = [tok.block("tool", texts[t]) for t in (32, 33, 34)]
        question = tok.block("user", "Which function is the entry point?")
        cont = tok.block("user", "Continue.")
        goon = tok.block("user", "Go on.")
        flat = lambda blocks: [x for b in blocks for x in b]           # noqa: E731

        def s5_prompt(i):
            if i == 0:
                return list(system) + list(task) + flat(big) + flat(tools) + question + gen_prompt
            if i == 1:
                return list(system) + list(task) + marker + flat(tools) + question + gen_prompt + reply + cont + \
                    gen_prompt
            return list(system) + list(task) + marker + tools[0] + tools[2] + question + gen_prompt + reply + \
                cont + gen_prompt + reply + goon + gen_prompt

        plan5 = [("prefix", "", {}), ("shift", "", {}), ("epic128", "", {}),
                 ("epic128rjsd", "keep", {"dec_force": "keep", "dec_floor": True}),
                 ("epic128rjsd", "rc", {}), ("epic128rjsd", "free", {})]
        for arm, role, extra in plan5:
            name = f"{arm}-{role}" if role else arm
            sess = Session(a.url, a.model, arm, f"st5-{name}-{tag}", "selftest-s5")
            for i in range(3):
                x = dict(extra)
                if role == "rc":
                    x = {"dec_force": "recompute" if i == 1 else "keep"}
                sess.call(s5_prompt(i), max_tokens=1, extra=x, token_ids=True)
            s5[name] = sess
        st5 = get(f"{a.url}/epic/stats?check=1")
        ref = st5.get("ref_check") or {}
        check("S5_ref_check", bool(ref.get("ok")), **ref)
        time.sleep(1)
        logs5 = [json.loads(x) for x in Path(a.log).read_text().splitlines() if x.strip()]
        rec5 = {(x["spec"], x["call"]): x for x in logs5 if x.get("ev") == "call" and str(x.get("spec", "")).startswith("st5-")
                and str(x.get("spec", "")).endswith(tag)}
        err5 = [x for x in logs5 if x.get("ev") == "error" and str(x.get("spec", "")).startswith("st5-")
                and str(x.get("spec", "")).endswith(tag)]
        check("S5_no_errors", not err5, n=len(err5), first=(err5[0].get("error") if err5 else None))

        def r5(name, i):
            return rec5.get((f"st5-{name}-{tag}", i), {})

        keys = ("kl", "jsd", "tvd", "exp", "kl20", "jsd20", "tvd20", "exp20", "m", "u", "recompute", "C_E", "C_O",
                "h", "ms")
        bad = []
        for name in ("epic128rjsd-keep", "epic128rjsd-rc", "epic128rjsd-free"):
            if "dec" in r5(name, 0):
                bad.append((name, 0, "decision on a first call"))
            for i in (1, 2):
                r = r5(name, i)
                d = r.get("dec") or {}
                miss = [k for k in keys if k not in d]
                if miss or d.get("error") or r.get("fallback") or r.get("entry_reset"):
                    bad.append((name, i, miss, d.get("error"), r.get("fallback"), r.get("entry_reset")))
                    continue
                if not (0 <= d["jsd"] <= 1 and 0 <= d["tvd"] <= 1 and 0 <= d["exp"] < 1 and 0 <= d["u"] < 1
                        and d["m"] == d["jsd"] and d["C_O"] <= d["C_E"] <= r["P"]
                        and d["kl20"] <= d["kl"] + 1e-9 and d["jsd20"] <= d["jsd"] + 1e-9 and d["tvd20"] <= d["tvd"] + 1e-9):
                    bad.append((name, i, {k: d.get(k) for k in keys}))
        check("S5_dec_records_complete_and_bounded", not bad, bad=bad[:4])
        us = {(r5(n, i).get("dec") or {}).get("u") for n in ("epic128rjsd-keep", "epic128rjsd-rc", "epic128rjsd-free")
              for i in (1,)}
        check("S5_u_common_across_sessions", len(us) == 1 and None not in us, u=sorted(x for x in us if x is not None))
        free = [(r5("epic128rjsd-free", i).get("dec") or {}) for i in (1, 2)]
        check("S5_unforced_rule", all(d and d["recompute"] == (d["u"] < d["m"]) and "forced" not in d for d in free),
              decisions=[{k: d.get(k) for k in ("u", "m", "recompute")} for d in free])

        def first5(name, i):
            return s5[name].first[i]

        def e20(name, i):
            return (r5(name, i).get("dec") or {}).get("e20") or {}

        def o20(name, i):
            return (r5(name, i).get("dec") or {}).get("o20") or {}
        # Two separate forwards of the same cache are not bit-identical on this server (batch composition, chunking):
        # the first start of the e4 pilot (2026-10-15 16:29 UTC) measured KL 0.0021 between two concurrent probes of
        # one plan (the decision's own floor), 0.0019-0.0021 here and 0.0048 for O against shift, and S1 allows 0.011
        # against prefix. Wrong rows give KL far above 0.1, so these same-computation checks use 0.02 (the plan's
        # structure is checked exactly: C_O = C_E - the run heads, and the unit tests pin the probes' splices).
        noise = 0.02
        kl_served = [kl(first5("epic128rjsd-keep", i), e20("epic128rjsd-keep", i)) for i in (1, 2)]
        check("S5_E_reproduces_served_call", all(x == x and x < noise for x in kl_served),
              kl=[round(x, 6) for x in kl_served])
        k_e = kl(first5("epic128", 1), e20("epic128rjsd-keep", 1))
        check("S5_E_equals_epic128", k_e == k_e and k_e < noise, kl=round(k_e, 6))
        k_o = kl(first5("shift", 1), o20("epic128rjsd-keep", 1))
        dk1 = r5("epic128rjsd-keep", 1).get("dec") or {}
        check("S5_O_equals_shift_at_first_edit", k_o == k_o and k_o < noise
              and dk1.get("C_O") == dk1.get("C_E", 0) - r5("epic128rjsd-keep", 1).get("H", -1),
              kl=round(k_o, 6), jsd=dk1.get("jsd"), C_O=dk1.get("C_O"), C_E=dk1.get("C_E"),
              H=r5("epic128rjsd-keep", 1).get("H"))
        rc1, rc2 = r5("epic128rjsd-rc", 1), r5("epic128rjsd-rc", 2)
        d1 = rc1.get("dec") or {}
        h1 = d1.get("h")
        check("S5_forced_recompute_record", rc1.get("recompute") is True and d1.get("recompute") is True
              and rc1.get("C") == rc1.get("P", 0) - (h1 or 0) and rc1.get("E") == h1 and rc1.get("S") == 0
              and rc1.get("H") == 0 and rc1.get("stale_rows") == 0 and rc1.get("salt_gen") == 1
              and rc1.get("C_charged") == rc1.get("C", 0) + d1.get("C_O", 0) + d1.get("C_E", 0),
              recompute=rc1.get("recompute"), P=rc1.get("P"), C=rc1.get("C"), E=rc1.get("E"), h=h1,
              S=rc1.get("S"), H=rc1.get("H"), stale_rows=rc1.get("stale_rows"), salt_gen=rc1.get("salt_gen"))
        keep1, keep2 = r5("epic128rjsd-keep", 1), r5("epic128rjsd-keep", 2)
        check("S5_h_running_min_and_restart", (keep2.get("dec") or {}).get("h") == keep1.get("E")
              and (rc2.get("dec") or {}).get("h") == rc2.get("E") and rc2.get("E", 0) > keep1.get("E", 0),
              keep_h=[(keep1.get("dec") or {}).get("h"), (keep2.get("dec") or {}).get("h")],
              keep_p0=[keep1.get("E"), keep2.get("E")], rc_h2=(rc2.get("dec") or {}).get("h"), rc_p0_2=rc2.get("E"))
        pre1 = r5("prefix", 1)
        same_split = pre1.get("ideal_E") == h1
        k_rc = kl(first5("prefix", 1), first5("epic128rjsd-rc", 1))
        check("S5_forced_recompute_equals_fresh", k_rc == k_rc and k_rc < (1e-3 if same_split else 0.05),
              kl=round(k_rc, 6), same_split=same_split, prefix_ideal_E=pre1.get("ideal_E"), h=h1)
        floors = [(r5("epic128rjsd-keep", i).get("dec") or {}).get("floor") for i in (1, 2)]
        check("S5_floor_logged", all(f and f.get("jsd") is not None and f["jsd"] <= 0.05 for f in floors), floor=floors)
        report["s5"] = {name: {str(i): {k: v for k, v in (r5(name, i).get("dec") or {}).items() if k not in ("o20", "e20")}
                                for i in (1, 2)} for name in ("epic128rjsd-keep", "epic128rjsd-rc", "epic128rjsd-free")}

    # S3: every rank applied the same overrides
    w = get(f"{a.url}/epic/workers")
    workers = w.get("workers") or []
    ov = [x.get("overridden_tokens") for x in workers]
    hooks = [x.get("hooks") for x in workers]
    check("S3_ranks_consistent", len(workers) == a.ranks and len(set(ov)) == 1 and ov[0] > 0
          and len(set(hooks)) == 1 and hooks[0] == a.layers,
          overridden_tokens=ov, hooks=hooks, rope_dim=[x.get("rope_dim") for x in workers],
          view_shape=workers[0].get("view_shape") if workers else None)

    # S4: ending every session frees the pool
    for sess in list(outs.values()) + list(s2.values()) + list(s5.values()):
        sess.end()
    st = get(f"{a.url}/epic/stats")
    check("S4_pool_empty_after_end", st.get("pool_used") == 0, pool_used=st.get("pool_used"),
          sessions=st.get("sessions"))
    report["ok"] = ok
    Path(a.out).write_text(json.dumps(report, indent=1))
    print("ALL OK" if ok else "SOME CHECKS FAILED", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
