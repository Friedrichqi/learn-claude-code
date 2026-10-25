"""vLLM API-server extension of the live EPIC server (research/16_epic_harness). Loaded by vLLM's own
`--middleware` hook, in the API-server process:

    PYTHONPATH=research/16_epic_harness EPIC_LOG=... EPIC_POOL_ROWS=... VLLM_ALLOW_INSECURE_SERIALIZATION=1 \
      vllm serve Qwen/Qwen3-32B ... --enforce-eager --enable-prefix-caching \
      --kv-transfer-config '{"kv_connector":"EpicConnector","kv_connector_module_path":"epic_connector",...}' \
      --middleware epic_server_ext.Middleware

The first Middleware instance patches AsyncLLM.generate. A request is handled here only when its
kv_transfer_params carry {"epic": {arm, spec, call, seed_key}} (epic_session.py tags lead calls only):

  * every arm gets cache_salt = "<arm>:<spec>", so vLLM's prefix cache serves each arm-session its own
    exact prefix and never another arm's blocks (an EPIC arm's blocks hold stale rows by design). When
    the cache matches beyond the planned prefix (an older sequence of the session shares more), the
    connector copies those rows to a reserve (apc_save) and the entry takes them; a match beyond the
    reserve resets the session under salt "<arm>:<spec>:r<n>" (fresh rows, empty cache);
  * the per-call seed is SHA256(seed_key, call): the same in every arm of a block (common random
    numbers; vLLM's /v1/messages drops request seeds, so it is set here);
  * prefix / prefix_aa: nothing else -- vanilla prefix caching (full re-prefill after the first edit);
  * shift / epic32 / epic64 / epic128: epic_plan aligns the prompt with the session's last sequence and
    the plan (loads below p0 on a cache miss, overrides of reused rows, saves of computed rows and of
    decode rows) goes to epic_connector in kv_transfer_params["splice"]; the outputs are teed and the
    session entry (slot, birth, age of every position's row) advances when the call finishes;
  * shadow probes on sampled calls of EPIC arms whose KV holds rows that survived an edit
    (EPIC_SHADOW_EVERY, default 4; every call whose plan shifts a run by >= EPIC_BIG_DELTA tokens, i.e. an
    LLM-summary compaction), before the response is returned: a fresh full recompute of the same prompt saved to temporary slots -> L1
    deviation per layer and row class (epic_compare); a teacher-forced pass of the arm's reply over the
    fresh prefix -> per-token KL / top-1 / dNLL; on a third of them a counterfactual next round decoded
    from the fresh prefix with the call's seed.

harness_eval e4: the recompute arms (`<base>r<metric>`, e.g. epic128rjsd). At every curated (edited) call, before
it is served, two concurrent 1-token probes give the first-token distribution under the original cache (every row in
its first-ever version, epic_plan.orig_view) and under the arm's own plan; their divergences (KL, JSD, TVD,
EXP = 1 - exp(-KL), on the partition {tokens in both top-K lists} + rest) go to the record's `dec`, and the call is
served as a full recomputation of [h, P) (epic_plan.recompute_plan, a new cache salt) with probability = the arm's
metric, drawn from u = SHA256(seed_key|recompute, call). A failed decision serves the arm's plan (`dec.error`).

Per-call JSON lines go to EPIC_LOG; GET /epic/stats reports the pool and sessions (?check=1 audits the slot
reference counts); POST /epic/end?spec=S frees a session. Every patch fails open: an exception logs an `error` line and the call is served as
prefix caching would serve it (flagged `fallback`).
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import epic_plan as ep  # noqa: E402

IM_START = int(os.environ.get("EPIC_IM_START", "151644"))      # Qwen3 <|im_start|>; MiniMax-M2 ]~b] = 200019
POOL_ROWS = int(os.environ.get("EPIC_POOL_ROWS", "200000"))
MAX_MODEL_LEN = int(os.environ.get("EPIC_MAX_MODEL_LEN", "32768"))
# harness_eval port: decode rows reserved per call. Topic 16 reserved max_tokens (32K for a thinking model's
# budget), which a pool shared by many sessions cannot afford; a reply that outgrows the reserve resets its
# session (logged `entry_reset: decode_overflow`) instead of leaving the entry short of its tokens
DECODE_RESERVE = int(os.environ.get("EPIC_DECODE_RESERVE", str(1 << 30)))
LOG_DIV = os.environ.get("EPIC_LOG_DIV") == "1"     # log token windows where a prompt parts from the previous sequence
# hybrid models (Qwen3.8): no loads; a row restored from the pool would lack its tokens' DeltaNet state, so rows of
# [0, p0) the prefix cache does not serve are computed (exact) instead
NO_LOAD = os.environ.get("EPIC_NO_LOAD") == "1"
SHADOW_EVERY = int(os.environ.get("EPIC_SHADOW_EVERY", "4"))
APC_RESERVE = int(os.environ.get("EPIC_APC_RESERVE", "256"))   # rows past p0 the prefix cache may serve
BIG_DELTA = int(os.environ.get("EPIC_BIG_DELTA", "8000"))       # shifts at least this large are always probed
CF_EVERY = int(os.environ.get("EPIC_CF_EVERY", "3"))
TF_MAX = int(os.environ.get("EPIC_TF_MAX", "512"))
LOG_PATH = os.environ.get("EPIC_LOG", "")
TOPK = 20
DEC_K = int(os.environ.get("EPIC_DEC_K", "128"))                 # e4: top-K logprobs of the decision probes
DEC_FLOOR_EVERY = int(os.environ.get("EPIC_DEC_FLOOR_EVERY", "16"))   # a duplicate E probe: the decision's floor
DEC_CONCURRENCY = int(os.environ.get("EPIC_DEC_CONCURRENCY", "0"))    # decisions probing at once (0: no limit)
AGE_BINS = (0, 1, 2, 4, 8, 16)            # [0], [1], [2,3], [4,7], [8,15], [16,)
OFF_BINS = (0, 32, 64, 128, 512)          # offset from the run head: [0,31] ... [512,)
CLASSES = ("exact", "reused", "head", "computed")

_ORIG: dict = {}
_INSTALLED = False
_PROBE_SEQ = itertools.count()             # every probe gets its own cache salt and request id
_DEC_SEM: list = []                        # the decision semaphore, made in the server's event loop
_LOG_LOCK = threading.Lock()
STATS: Counter = Counter()
POOL = ep.SlotPool(POOL_ROWS)


@dataclass
class Session:
    spec: str
    arm: str
    entry: ep.Entry | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    calls: int = 0
    t_last: float = 0.0
    salt_gen: int = 0           # bumped when the entry is reset: the prefix cache starts empty for it
    prev_tokens: list | None = None   # prefix arms: the last sequence (prompt + generated but the last token)

    @property
    def salt(self) -> str:
        return f"{self.arm}:{self.spec}" + (f":r{self.salt_gen}" if self.salt_gen else "")


SESSIONS: dict[str, Session] = {}


def _log(rec: dict) -> None:
    if not LOG_PATH:
        return
    rec.setdefault("t", round(time.time(), 3))
    with _LOG_LOCK, open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec, default=str) + "\n")


def _error(where: str, exc: BaseException, **extra) -> None:
    STATS[f"error_{where}"] += 1
    _log({"ev": "error", "where": where, "error": repr(exc), "tb": traceback.format_exc()[-2000:], **extra})


def seed_of(key: str, call: int) -> int:
    return int(hashlib.sha256(f"{key}|{call}".encode()).hexdigest()[:8], 16)


def _tag(sampling_params) -> dict | None:
    extra = getattr(sampling_params, "extra_args", None) or {}
    kvp = extra.get("kv_transfer_params") or {}
    tag = kvp.get("epic") if isinstance(kvp, dict) else None
    return tag if isinstance(tag, dict) and ep.is_arm(tag.get("arm")) else None


def _bin(val: int, edges: tuple) -> int:
    b = 0
    for i, e in enumerate(edges):
        if val >= e:
            b = i
    return b


def topk_kl(p: dict, q: dict) -> float:
    """KL(p || q) over p's top tokens; q's missing entries floored at q's smallest logprob."""
    if not p or not q:
        return float("nan")
    floor = min(q.values())
    return sum(math.exp(lp) * (lp - q.get(t, floor)) for t, lp in p.items())


def _lp_dict(entry) -> dict:
    return {int(t): float(lp.logprob) for t, lp in (entry or {}).items()}


def _topn(d: dict, n: int) -> dict:
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n])


def partition_div(p: dict, q: dict) -> dict:
    """Divergences of two top-k logprob dicts (token -> logprob of the full-vocabulary distribution) on the
    partition {tokens in both lists} + one rest bucket. Exact for that partition, so each value is a lower bound of
    the full-vocabulary one (data processing). KL(p || q) in nats; JSD = sqrt(JS divergence in bits), in [0, 1];
    TVD = 1/2 sum |p - q|, in [0, 1]; EXP = 1 - exp(-KL)."""
    common = [t for t in p if t in q]
    pp = [math.exp(p[t]) for t in common]
    qq = [math.exp(q[t]) for t in common]
    mass_p, mass_q = sum(pp), sum(qq)
    pp.append(max(0.0, 1.0 - mass_p))
    qq.append(max(0.0, 1.0 - mass_q))
    zp, zq = sum(pp), sum(qq)                 # renormalize fp32 rounding (mass a hair above 1)
    pp = [x / zp for x in pp]
    qq = [x / zq for x in qq]
    kl = 0.0
    js = 0.0
    for a, b in zip(pp, qq):
        if a > 0:
            kl += a * math.log(a / max(b, 1e-12))
        mid = 0.5 * (a + b)
        if a > 0:
            js += 0.5 * a * math.log2(a / mid)
        if b > 0:
            js += 0.5 * b * math.log2(b / mid)
    kl = max(0.0, kl)
    js = min(1.0, max(0.0, js))
    tvd = min(1.0, 0.5 * sum(abs(a - b) for a, b in zip(pp, qq)))
    return {"kl": kl, "jsd": math.sqrt(js), "tvd": tvd, "exp": 1.0 - math.exp(-kl), "n_common": len(common),
            "mass_p": mass_p, "mass_q": mass_q}


def _st(spec: str) -> bool:
    """Selftest sessions (selftest_live.py specs st1-..., st5-...) may force a decision."""
    return bool(re.match(r"st\d", str(spec)))


def _segments(positions, slots) -> list[list]:
    """[[pos_lo, pos_hi, slot_lo]] over parallel position/slot lists, contiguous in both."""
    out: list[list] = []
    for p, s in zip(positions, slots):
        p, s = int(p), int(s)
        if out and out[-1][1] == p and out[-1][2] + (out[-1][1] - out[-1][0]) == s:
            out[-1][1] = p + 1
        else:
            out.append([p, p + 1, s])
    return out


# -- the patched generate --------------------------------------------------------------------------

async def _generate(self, prompt, sampling_params, request_id, *args, **kwargs):
    tag = _tag(sampling_params)
    if tag is None or not isinstance(prompt, dict) or "prompt_token_ids" not in prompt:
        async for out in _ORIG["generate"](self, prompt, sampling_params, request_id, *args, **kwargs):
            yield out
        return
    _ORIG.setdefault("engine", self)
    arm, spec = tag["arm"], str(tag["spec"])
    sess = SESSIONS.get(spec)
    if sess is None:
        sess = SESSIONS[spec] = Session(spec, arm)
    async with sess.lock:
        if "call" not in tag:                   # static tags (SWE-agent): count the session's calls here
            tag = {**tag, "call": sess.calls}
        async for out in _serve(self, sess, tag, prompt, sampling_params, request_id, args, kwargs):
            yield out


async def _serve(engine, sess: Session, tag: dict, prompt: dict, sp, request_id, args, kwargs):
    arm, call = tag["arm"], int(tag.get("call", 0))
    ids = list(prompt["prompt_token_ids"])
    t0 = time.perf_counter()
    rec = {"ev": "call", "spec": sess.spec, "arm": arm, "call": call, "rid": request_id, "P": len(ids)}
    plan = new_slots = decode_slots = apc_slots = None
    dec = None
    try:
        prompt["cache_salt"] = sess.salt
        sp.seed = seed_of(str(tag.get("seed_key") or sess.spec), call)
        sp.logprobs = max(int(sp.logprobs or 0), TOPK)
        extra = dict(sp.extra_args or {})
        kvp = {k: v for k, v in (extra.get("kv_transfer_params") or {}).items() if k != "epic"}
        if ep.arm_params(arm)[0] is not None:
            plan = eplan = ep.plan_call(arm, sess.entry, ids, IM_START)
            if ep.recompute_metric(arm) and sess.entry is not None and plan.stats["edited"]:
                # e4: a curated call of a recompute arm -- decide before serving (never raises an Exception)
                try:
                    dec = rec["dec"] = await _decide(engine, sess, tag, ids, plan, arm)
                except BaseException:                      # cancelled mid-probe: the call still gets its record
                    rec["aborted"] = True
                    _log(rec)
                    raise
                if dec["recompute"]:
                    plan = ep.recompute_plan(arm, sess.entry, ids, IM_START, dec["h"])
            try:
                new_slots, decode_slots, apc_slots, save, n_apc = _alloc(plan, ids, sp)
            except MemoryError as exc:
                if plan is eplan:
                    raise
                # no pool rows for the recomputation: serve the arm's plan (a decision error, not a fallback)
                _error("decide", exc, spec=sess.spec, call=call)
                dec.update(recompute=False, error=f"pool: {exc!r}"[:300])
                STATS[f"recomputes_{arm}"] -= 1
                STATS[f"dec_errors_{arm}"] += 1
                plan = eplan
                new_slots, decode_slots, apc_slots, save, n_apc = _alloc(plan, ids, sp)
            kvp["splice"] = {"p0": plan.p0, "load": None if NO_LOAD else plan.load, "override": plan.override,
                             "from_layer": 0, "save": save,
                             "apc_save": _segments(range(plan.p0, plan.p0 + n_apc), apc_slots)}
            rec.update(plan.stats)
            if dec is not None:
                if plan.recompute:
                    dec["E"] = {k: eplan.stats[k] for k in ("E", "S", "H", "C", "F", "exact", "runs", "max_delta",
                                                             "reply_reused")}
                # what a real server computes: the served plan, the original-cache forward, and on a
                # recomputation also the arm's prefill it ran before deciding
                rec["C_charged"] = int(plan.stats["C"]) + int(dec["C_O"]) + (int(dec["C_E"]) if plan.recompute else 0)
            elif ep.recompute_metric(arm):
                rec["C_charged"] = int(plan.stats["C"])
            if plan.recompute:
                # vLLM's prefix cache would keep serving the stale blocks under the old salt (the first block
                # inserted for a hash wins): the recomputation and every later call run under a new one
                sess.salt_gen += 1
                prompt["cache_salt"] = sess.salt
                rec["salt_gen"] = sess.salt_gen
            if LOG_DIV and sess.entry is not None and eplan.stats["exact"] < len(sess.entry):
                # where the new prompt parts from the session's previous sequence (append-only diagnosis)
                x = int(eplan.stats["exact"])
                rec["div"] = {"at": x, "prev_prompt_len": sess.entry.prompt_len, "prev_len": len(sess.entry),
                              "old": [int(t) for t in sess.entry.tokens[max(0, x - 8):x + 24]],
                              "new": [int(t) for t in ids[max(0, x - 8):x + 24]]}
        else:
            # prefix arms: what an unbounded prefix cache would recompute (prompt minus the 16-aligned common prefix
            # with the session's previous sequence), so eviction under load does not inflate the reported share
            prev = sess.prev_tokens or []
            k = next((i for i, (u, v) in enumerate(zip(prev, ids)) if u != v), min(len(prev), len(ids)))
            p0 = (min(k, len(ids) - 1) // ep.BLOCK) * ep.BLOCK
            rec.update(ideal_E=p0, ideal_C=len(ids) - p0, exact=k)
        extra["kv_transfer_params"] = kvp
        sp.extra_args = extra
    except Exception as exc:
        _error("plan", exc, spec=sess.spec, call=call)
        for slots in (new_slots, decode_slots, apc_slots):
            if slots is not None:
                POOL.release(slots)
        plan = new_slots = decode_slots = apc_slots = None
        extra = dict(sp.extra_args or {})
        extra["kv_transfer_params"] = {}
        sp.extra_args = extra
        rec["fallback"] = True
        if dec is not None and dec.get("recompute"):      # the decision was not carried out
            dec.update(recompute=False, error=dec.get("error") or "fallback after the decision")
            STATS[f"recomputes_{arm}"] -= 1
    rec["plan_ms"] = round(1000 * (time.perf_counter() - t0) - (dec or {}).get("ms", 0.0), 2)

    from vllm.sampling_params import RequestOutputKind
    delta = getattr(sp, "output_kind", None) == RequestOutputKind.DELTA
    gen: list[int] = []
    lps: list[dict] = []
    finished = False
    try:
        async for out in _ORIG["generate"](engine, prompt, sp, request_id, *args, **kwargs):
            if out.outputs:
                c = out.outputs[0]
                if delta:
                    gen.extend(c.token_ids)
                    lps.extend(c.logprobs or [])
                else:
                    gen, lps = list(c.token_ids), list(c.logprobs or [])
            if out.finished and not finished:
                finished = True
                rec.update(gen=len(gen), finish=str(out.outputs[0].finish_reason if out.outputs else None),
                           apc_hit=getattr(out, "num_cached_tokens", None),
                           server=(getattr(out, "kv_transfer_params", None) or {}).get("epic_server"),
                           ms=round(1000 * (time.perf_counter() - t0), 1))
                try:
                    await _finish(engine, sess, tag, ids, gen, lps, plan, new_slots, decode_slots, apc_slots,
                                  sp, rec)
                except Exception as exc:
                    _error("finish", exc, spec=sess.spec, call=call)
                _log(rec)
            yield out
    finally:
        if not finished:                               # aborted: keep the old entry, give slots back
            for slots in (new_slots, decode_slots, apc_slots):
                if slots is not None:
                    POOL.release(slots)
            if plan is not None and plan.recompute:
                # the aborted recomputation's fresh blocks sit under the new salt beyond the old entry's p0; a
                # retry planned from the old entry must not be served those, so it gets yet another salt
                sess.salt_gen += 1
            rec["aborted"] = True
            _log(rec)


def _alloc(plan, ids, sp):
    """Pool slots of a plan: computed rows, the decode reserve and the prefix-cache reserve; all or nothing."""
    new_slots = decode_slots = apc_slots = None
    try:
        new_slots = POOL.alloc(plan.stats["C"])
        reserve = max(0, min(int(sp.max_tokens or 0), MAX_MODEL_LEN - len(ids), DECODE_RESERVE))
        decode_slots = POOL.alloc(reserve)
        # vLLM's prefix cache always leaves the last prompt token to compute
        n_apc = max(0, min(APC_RESERVE, len(ids) - 1 - plan.p0))
        apc_slots = POOL.alloc(n_apc)
    except BaseException:
        for slots in (new_slots, decode_slots, apc_slots):
            if slots is not None:
                POOL.release(slots)
        raise
    cpos = [p for a, b in plan.compute for p in range(a, b)]
    save = _segments(cpos, new_slots) + _segments(range(len(ids), len(ids) + reserve), decode_slots)
    return new_slots, decode_slots, apc_slots, save, n_apc


async def _finish(engine, sess: Session, tag: dict, ids, gen, lps, plan, new_slots, decode_slots, apc_slots,
                  sp, rec):
    sess.calls += 1
    sess.t_last = time.time()
    STATS[f"calls_{sess.arm}"] += 1
    if plan is None:
        sess.prev_tokens = list(ids) + list(gen[:max(0, len(gen) - 1)])
        # the prefix arms' KV is exact up to numerics: their shadows (every 2*SHADOW_EVERY-th call) are
        # the noise floor of the shadow metrics themselves (batch composition, chunking)
        pick = seed_of(sess.spec, int(tag.get("call", 0)))
        if SHADOW_EVERY and int(tag.get("call", 0)) > 0 and pick % (2 * SHADOW_EVERY) == 0:
            rec["shadow"] = await _shadow(engine, sess, None, None, ids, gen, lps, sp,
                                          counterfactual=(pick // SHADOW_EVERY) % CF_EVERY == 0)
        return
    server = rec.get("server") or {}
    over = int(server.get("overshoot") or 0)
    if len(gen) - 1 > len(decode_slots):
        # the reply outgrew the decode reserve: its later rows were never saved, so the entry cannot cover
        # the sequence. Restart the session from fresh rows under a new salt (as for an overshoot).
        STATS["decode_overflow_resets"] += 1
        rec["entry_reset"] = "decode_overflow"
        for slots in (new_slots, decode_slots, apc_slots):
            POOL.release(slots)
        if sess.entry is not None:
            POOL.release(sess.entry.held())
        sess.entry = None
        sess.salt_gen += 1
        return
    if over and (over > len(apc_slots) or int(server.get("apc_saved") or 0) != over):
        # vLLM's prefix cache served more than the planned exact prefix and more than the reserve
        # caught: rows of those positions are unknown to the entry. The session restarts from fresh
        # rows under a new cache salt (an empty prefix cache), so no later call reads an unsaved row.
        STATS["overshoot_resets"] += 1
        rec["entry_reset"] = "overshoot"
        for slots in (new_slots, decode_slots, apc_slots):
            POOL.release(slots)
        if sess.entry is not None:
            POOL.release(sess.entry.held())
        sess.entry = None
        sess.salt_gen += 1
        return
    entry = ep.next_entry(plan, ids, gen, new_slots, decode_slots, int(tag.get("call", 0)))
    if over:
        # the cache's rows of [p0, p0 + over) were copied to the reserve: the entry takes them
        STATS["overshoot_absorbed"] += 1
        rec.update(ep.absorb_apc(plan, entry, over, apc_slots))
    POOL.retain(entry.held())
    POOL.release(new_slots)
    POOL.release(decode_slots)
    POOL.release(apc_slots)
    old, sess.entry = sess.entry, entry
    if old is not None:
        POOL.release(old.held())
    rec["pool_used"] = POOL.used
    # the arm's KV differs from a fresh prefill wherever a row has survived an edit (age > 0): at the
    # edit itself and, inherited through the exact prefix, at every later call (compounding)
    P = len(ids)
    stale_rows = int((entry.age[:P] > 0).sum())
    rec["stale_rows"] = stale_rows
    rec["age_max"] = int(entry.age[:P].max()) if P else 0
    pick = seed_of(sess.spec, int(tag.get("call", 0)))
    # a large shift is an LLM-summary compaction (R2's reactive_compact moves the kept tail by ~15-22K
    # tokens; snips move it by at most a few thousand): rare and the case EPIC is stressed by, so always probed
    big = int(rec.get("max_delta") or 0) >= BIG_DELTA
    if stale_rows and SHADOW_EVERY and (pick % SHADOW_EVERY == 0 or big):
        rec["shadow"] = await _shadow(engine, sess, plan, entry, ids, gen, lps, sp,
                                      counterfactual=big or (pick // SHADOW_EVERY) % CF_EVERY == 0)


# -- shadow probes ------------------------------------------------------------------------------------

async def _run(engine, prompt_ids: list[int], sp, rid: str, splice: dict | None, salt: str):
    extra = dict(sp.extra_args or {})
    extra["kv_transfer_params"] = {"splice": splice} if splice else {}
    sp.extra_args = extra
    final = None
    async for out in _ORIG["generate"](engine, {"prompt_token_ids": prompt_ids, "cache_salt": salt}, sp, rid):
        final = out
    return final


async def _probe(engine, ids: list[int], plan, name: str, tag: str) -> dict:
    """e4: the first-token logprobs (top DEC_K) of one forward of `plan` -- loads of [0, p0), overrides, nothing
    saved -- under a never-used cache salt, so the prefix cache serves nothing. No prompt_logprobs: vLLM would
    then skip the prefix-cache read and with it the connector's loads."""
    from vllm.sampling_params import SamplingParams
    seq = next(_PROBE_SEQ)
    sp = SamplingParams(max_tokens=1, temperature=0.0, logprobs=DEC_K)
    out = await _run(engine, ids, sp, f"epicdec-{name}-{tag}:{seq}",
                     {"p0": plan.p0, "load": plan.load, "override": plan.override, "from_layer": 0, "save": []},
                     f"dec:{tag}:{seq}:{name}")
    c = out.outputs[0] if out is not None and out.outputs else None
    if c is None or not c.logprobs or not c.logprobs[0]:
        raise RuntimeError(f"probe {name}: no logprobs (finish {getattr(c, 'finish_reason', None)})")
    srv = (getattr(out, "kv_transfer_params", None) or {}).get("epic_server") or {}
    if int(srv.get("apc_hit") or 0):
        raise RuntimeError(f"probe {name}: the prefix cache served {srv.get('apc_hit')} tokens under a new salt")
    if plan.p0 and "loaded" in srv and int(srv.get("loaded") or 0) != plan.p0:
        raise RuntimeError(f"probe {name}: loaded {srv.get('loaded')} rows, planned {plan.p0}")
    return _lp_dict(c.logprobs[0])


async def _decide(engine, sess: Session, tag: dict, ids: list[int], plan, arm: str) -> dict:
    """e4: the recompute decision of a curated call (see the module docstring). Never raises an Exception: a
    failure is logged and returned as `error` with recompute False, so the call is served by the arm's plan."""
    t0 = time.perf_counter()
    call = int(tag.get("call", 0))
    key = str(tag.get("seed_key") or sess.spec)
    metric = ep.recompute_metric(arm)
    dec: dict = {"metric": metric, "u": seed_of(f"{key}|recompute", call) / 2 ** 32, "h": plan.h,
                 "C_E": int(plan.stats["C"]), "C_O": 0, "recompute": False}
    try:
        if NO_LOAD:
            raise RuntimeError("the original cache needs loads (EPIC_NO_LOAD is set)")
        if ep.same_as_original(plan):
            dec.update(same=True, kl=0.0, jsd=0.0, tvd=0.0, exp=0.0, kl20=0.0, jsd20=0.0, tvd20=0.0, exp20=0.0)
        else:
            mc = getattr(engine, "model_config", None)
            mx = getattr(mc, "max_logprobs", None)
            if isinstance(mx, int) and mx != -1 and mx < DEC_K:
                raise RuntimeError(f"the server allows {mx} logprobs, decisions need {DEC_K} (--max-logprobs)")
            oplan = ep.plan_call("shift", ep.orig_view(sess.entry), ids, IM_START, segs=plan.segs)
            dec["C_O"] = int(oplan.stats["C"])
            floor = DEC_FLOOR_EVERY > 0 and seed_of(f"{key}|floor", call) % DEC_FLOOR_EVERY == 0
            if _st(sess.spec) and tag.get("dec_floor") is not None:
                floor = bool(tag.get("dec_floor"))
            ptag = f"{sess.spec}:{call}"
            jobs = [_probe(engine, ids, oplan, "O", ptag), _probe(engine, ids, plan, "E", ptag)]
            if floor:
                jobs.append(_probe(engine, ids, plan, "E2", ptag))
            if DEC_CONCURRENCY > 0:
                if not _DEC_SEM:
                    _DEC_SEM.append(asyncio.Semaphore(DEC_CONCURRENCY))
                async with _DEC_SEM[0]:
                    res = await asyncio.gather(*jobs, return_exceptions=True)
            else:
                res = await asyncio.gather(*jobs, return_exceptions=True)
            for r in res:
                if isinstance(r, BaseException):
                    raise r
            o, e = res[0], res[1]
            d = partition_div(o, e)
            d20 = partition_div(_topn(o, TOPK), _topn(e, TOPK))
            dec.update(same=False, kl=d["kl"], jsd=d["jsd"], tvd=d["tvd"], exp=d["exp"],
                       kl20=d20["kl"], jsd20=d20["jsd"], tvd20=d20["tvd"], exp20=d20["exp"],
                       n_common=d["n_common"], mass_o=round(d["mass_p"], 6), mass_e=round(d["mass_q"], 6),
                       top1=int(max(o, key=o.get) == max(e, key=e.get)))
            if floor:
                f = partition_div(res[2], e)
                dec["floor"] = {k: f[k] for k in ("kl", "jsd", "tvd", "exp")}
            if _st(sess.spec):                      # selftests compare the probes with served calls
                dec["o20"] = {str(t): v for t, v in _topn(o, TOPK).items()}
                dec["e20"] = {str(t): v for t, v in _topn(e, TOPK).items()}
        m = float(dec[metric])
        if not 0.0 <= m <= 1.0:
            raise ValueError(f"{metric} = {m} outside [0, 1]")
        dec["m"] = m
        force = tag.get("dec_force") if _st(sess.spec) else None
        if force in ("recompute", "keep"):
            dec["recompute"] = force == "recompute"
            dec["forced"] = force
        else:
            dec["recompute"] = dec["u"] < m
    except Exception as exc:                          # noqa: BLE001
        _error("decide", exc, spec=sess.spec, call=call)
        dec.update(recompute=False, error=repr(exc)[:500])
        STATS[f"dec_errors_{arm}"] += 1
    dec["ms"] = round(1000 * (time.perf_counter() - t0), 1)
    STATS[f"decisions_{arm}"] += 1
    STATS[f"dec_same_{arm}"] += int(bool(dec.get("same")))
    STATS[f"recomputes_{arm}"] += int(bool(dec["recompute"]))
    return dec


async def _shadow(engine, sess: Session, plan, entry, ids, gen, lps, arm_sp, counterfactual: bool) -> dict:
    from vllm.sampling_params import SamplingParams

    P = len(ids)
    res: dict = {"floor": entry is None}
    tmp = POOL.alloc(P)
    # e4: a per-probe counter in the tag, so a retried call index never reuses an earlier probe's cache salt
    tag = f"{sess.spec}:{entry.call if entry is not None else sess.calls}:{next(_PROBE_SEQ)}"
    try:
        t = time.perf_counter()
        fresh = await _run(engine, ids, SamplingParams(max_tokens=1, temperature=0.0, logprobs=TOPK),
                           f"epicshadow-fresh-{tag}", {"p0": 0, "load": [], "override": [], "from_layer": 0,
                                                       "save": _segments(range(P), tmp)},
                           f"shadow:{tag}:fresh")
        res["fresh_ms"] = round(1000 * (time.perf_counter() - t), 1)
        if entry is not None:
            res.update(await _l1(engine, plan, entry, P, tmp))
        # first-token distribution of the fresh prefix vs the arm's
        f0 = _lp_dict(fresh.outputs[0].logprobs[0]) if fresh and fresh.outputs and fresh.outputs[0].logprobs else {}
        a0 = _lp_dict(lps[0]) if lps else {}
        res["first_kl"] = topk_kl(f0, a0)
        res["first_top1"] = int(bool(f0) and bool(a0) and max(f0, key=f0.get) == max(a0, key=a0.get))
        if f0 and a0:
            # e4: the decision metrics for fresh vs arm (the urgency they are meant to track), same partition
            pd = partition_div(_topn(f0, TOPK), _topn(a0, TOPK))
            res.update(first_klp=pd["kl"], first_jsd=pd["jsd"], first_tvd=pd["tvd"], first_exp=pd["exp"])
        # teacher-forced reply over the fresh prefix
        n = min(len(gen), TF_MAX)
        if n > 1:
            base = (P // ep.BLOCK) * ep.BLOCK
            sp = SamplingParams(max_tokens=1, temperature=0.0, prompt_logprobs=TOPK,
                                skip_reading_prefix_cache=False)
            t = time.perf_counter()
            tf = await _run(engine, ids + list(gen[:n]), sp, f"epicshadow-tf-{tag}",
                            {"p0": base, "load": [[lo, hi, s, 0] for lo, hi, s in _segments(range(base), tmp[:base])],
                             "override": [], "from_layer": 0, "save": []},
                            f"shadow:{tag}:tf")
            res["tf_ms"] = round(1000 * (time.perf_counter() - t), 1)
            kls, top1, dnll = [], [], []
            plp = tf.prompt_logprobs if tf is not None else None
            # vLLM returns [None] + one entry per COMPUTED prompt position: entry k is the distribution of the
            # token at position start + k, where start is what was not computed. With [0, base) loaded,
            # start = base; on a hybrid model the loads are refused (EPIC_NO_LOAD) and start = 0. (Before this fix
            # the offset was always base, which misaligned the hybrid model's teacher-forced metrics.)
            start = (len(ids) + n - len(plp)) if plp is not None else base
            for i in range(n):
                k = P + i - start
                if plp is None or k <= 0 or k >= len(plp) or plp[k] is None or i >= len(lps):
                    continue
                fd, ad = _lp_dict(plp[k]), _lp_dict(lps[i])
                tok = int(gen[i])
                kls.append(topk_kl(fd, ad))
                top1.append(int(max(fd, key=fd.get) == max(ad, key=ad.get)))
                if tok in fd and tok in ad:
                    dnll.append(ad[tok] - fd[tok])      # = -log p_fresh - (-log p_arm)
            if kls:
                res.update(tf_kl=sum(kls) / len(kls), tf_top1=sum(top1) / len(top1),
                           tf_dnll=sum(dnll) / len(dnll) if dnll else None, tf_n=len(kls))
        if counterfactual:
            base = ((P - 1) // ep.BLOCK) * ep.BLOCK          # vLLM must compute >= 1 prompt token
            cf_sp = SamplingParams(max_tokens=min(max(len(gen), 1) + 32, 1024),
                                   temperature=arm_sp.temperature, top_p=arm_sp.top_p, top_k=arm_sp.top_k,
                                   min_p=arm_sp.min_p, seed=arm_sp.seed, stop_token_ids=arm_sp.stop_token_ids)
            t = time.perf_counter()
            cf = await _run(engine, ids, cf_sp, f"epicshadow-cf-{tag}",
                            {"p0": base, "load": [[lo, hi, s, 0] for lo, hi, s in _segments(range(base), tmp[:base])],
                             "override": [], "from_layer": 0, "save": []},
                            f"shadow:{tag}:cf")
            res["cf_ms"] = round(1000 * (time.perf_counter() - t), 1)
            cf_ids = list(cf.outputs[0].token_ids) if cf is not None and cf.outputs else []
            k = next((i for i, (x, y) in enumerate(zip(cf_ids, gen)) if x != y), min(len(cf_ids), len(gen)))
            res.update(cf_identical=int(cf_ids == list(gen)), cf_first_div=k, cf_len=len(cf_ids),
                       cf_text=cf.outputs[0].text if cf is not None and cf.outputs else None)
    except Exception as exc:
        _error("shadow", exc, spec=sess.spec, tag=tag)
        res["error"] = repr(exc)
    finally:
        POOL.release(tmp)
    return res


async def _l1(engine, plan, entry, P: int, tmp) -> dict:
    """L1: the arm's rows of [0, P) (as used, re-rotated) against the fresh rows, binned."""
    import epic_connector as ec_mod
    res: dict = {}
    cls = [0] * P
    for lo, hi, _s, _d in plan.override:
        for p in range(lo, hi):
            cls[p] = 1
    heads = ep.run_heads(plan.segs, plan.k or 0, plan.p0, P) if plan.k else set()
    for a, b in plan.compute:
        for p in range(a, b):
            cls[p] = 2 if p in heads else 3
    for p in range(max(plan.p0, plan.served)):  # the exact prefix, with whatever the cache served beyond p0
        cls[p] = 0
    run_start = [0] * P
    for seg in plan.segs:
        if seg[0] == "reuse":
            for p in range(seg[1], seg[2]):
                run_start[p] = seg[1]
    n_age, n_off = len(AGE_BINS), len(OFF_BINS)
    bins = [(cls[p] * n_age + _bin(int(entry.age[p]), AGE_BINS)) * n_off
            + _bin(p - run_start[p], OFF_BINS) for p in range(P)]
    n_bins = len(CLASSES) * n_age * n_off
    deltas = [int(p - entry.birth[p]) for p in range(P)]
    t = time.perf_counter()
    out = await engine.collective_rpc(ec_mod.epic_compare, args=(
        [int(s) for s in entry.slot[:P]], deltas, [int(s) for s in tmp], bins, n_bins))
    res["compare_ms"] = round(1000 * (time.perf_counter() - t), 1)
    # one result per TP rank, each over its own KV heads (harness_eval port: topic 16 read out[0] only)
    res["l1"] = _sparse_l1(ec_mod.merge_compare(out))    # {bin: [[dK, |K*|, dV, |V*|, n] per 4-layer group]}
    res["tp_ranks"] = len(out)
    res["bins"] = {"classes": CLASSES, "age": AGE_BINS, "offset": OFF_BINS, "layer_group": 4}
    return res


def _sparse_l1(per_layer: list, group: int = 4) -> dict:
    """[L][n_bins][5] -> {bin: [[dK, |K*|, dV, |V*|, n] per group of `group` layers]}, populated bins only."""
    L = len(per_layer)
    n_bins = len(per_layer[0]) if L else 0
    out = {}
    for b in range(n_bins):
        if not any(per_layer[layer][b][4] for layer in range(L)):
            continue
        rows = []
        for g in range(0, L, group):
            acc = [0.0] * 5
            for layer in range(g, min(g + group, L)):
                for j in range(5):
                    acc[j] += per_layer[layer][b][j]
            rows.append([float(f"{v:.6g}") for v in acc])
        out[b] = rows
    return out


# -- install and the ASGI middleware ----------------------------------------------------------------------

def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from vllm.v1.engine.async_llm import AsyncLLM
    _ORIG["generate"] = AsyncLLM.generate
    AsyncLLM.generate = _generate
    _INSTALLED = True
    _log({"ev": "installed", "pool_rows": POOL_ROWS, "shadow_every": SHADOW_EVERY, "cf_every": CF_EVERY,
          "decode_reserve": DECODE_RESERVE, "dec_k": DEC_K, "dec_floor_every": DEC_FLOOR_EVERY,
          "dec_concurrency": DEC_CONCURRENCY, "pid": os.getpid()})


def end_session(spec: str) -> dict:
    sess = SESSIONS.pop(spec, None)
    if sess is None:
        return {"spec": spec, "found": False}
    if sess.entry is not None:
        POOL.release(sess.entry.held())
        sess.entry = None
    return {"spec": spec, "found": True, "calls": sess.calls, "pool_used": POOL.used}


async def end_session_locked(spec: str) -> dict:
    """/epic/end: waits for the session's call in flight (its entry would otherwise be released twice)."""
    sess = SESSIONS.get(spec)
    if sess is None:
        return {"spec": spec, "found": False}
    async with sess.lock:
        return end_session(spec)


def ref_check() -> dict:
    """The pool's reference counts against the live entries (exact only with no call in flight)."""
    import numpy as np
    want = np.zeros(POOL.capacity, dtype=np.int64)
    for sess in SESSIONS.values():
        if sess.entry is not None:
            held = sess.entry.held()
            np.add.at(want, held[held >= 0], 1)
    bad = np.nonzero(want != POOL.ref)[0]
    return {"ok": not len(bad), "mismatches": int(len(bad)), "examples": [int(x) for x in bad[:10]]}


def stats(check: bool = False) -> dict:
    out = {"pool_rows": POOL_ROWS, "pool_used": POOL.used, "sessions": len(SESSIONS),
           "stats": dict(STATS), "session_calls": {s.spec: s.calls for s in SESSIONS.values()},
           "config": {"im_start": IM_START, "max_model_len": MAX_MODEL_LEN, "decode_reserve": DECODE_RESERVE,
                      "shadow_every": SHADOW_EVERY, "no_load": NO_LOAD, "log_div": LOG_DIV, "dec_k": DEC_K,
                      "dec_floor_every": DEC_FLOOR_EVERY, "dec_concurrency": DEC_CONCURRENCY}}
    if check:
        out["ref_check"] = ref_check()
    return out


async def _send_json(send, status: int, obj) -> None:
    body = json.dumps(obj, default=str).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class Middleware:
    def __init__(self, app):
        self.app = app
        install()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path == "/epic/stats":
                query = parse_qs((scope.get("query_string") or b"").decode())
                await _send_json(send, 200, stats(check=(query.get("check") or ["0"])[0] == "1"))
                return
            if path == "/epic/end":
                query = parse_qs((scope.get("query_string") or b"").decode())
                spec = (query.get("spec") or [""])[0]
                await _send_json(send, 200, await end_session_locked(spec))
                return
            if path == "/epic/workers":
                # harness_eval port: every TP rank's connector counters (hook calls, overridden and saved
                # tokens), so a run can check that each rank applied every planned override
                engine = _ORIG.get("engine")
                if engine is None:
                    await _send_json(send, 200, {"workers": [], "note": "no tagged call served yet"})
                    return
                import epic_connector as ec_mod
                try:
                    out = await engine.collective_rpc(ec_mod.epic_stats)
                    await _send_json(send, 200, {"workers": out, "server": dict(STATS)})
                except Exception as exc:                       # noqa: BLE001
                    await _send_json(send, 500, {"error": repr(exc)})
                return
        await self.app(scope, receive, send)
