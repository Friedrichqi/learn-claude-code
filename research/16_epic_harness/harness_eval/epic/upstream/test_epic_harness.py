#!/usr/bin/env python3
"""CPU tests of research/16_epic_harness (plan §6, C1-C9, C12, C13; C10 is test_epic_grade.py, C11 is
tests/test_append_only_context.py). Runnable directly (no pytest in the venv) or under pytest:

    python research/16_epic_harness/test_epic_harness.py
    uv run --no-project --python /data/fritz/venv/bin/python --with pytest python -m pytest \
        research/16_epic_harness/test_epic_harness.py -q -p no:cacheprovider
"""
from __future__ import annotations

import copy
import glob
import json
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import numpy as np  # noqa: E402

import epic_common as ec  # noqa: E402
import epic_plan as ep  # noqa: E402

FIXTURES = sorted(glob.glob(str(ec.REPO / "research/04_kv_splice/data/kv_splice/run_*.requests.jsonl")))
_R = {}


def renderers():
    if not _R:
        _R["off"] = ec.renderer(None)
        _R["ao"] = ec.renderer(ec.TEMPLATE.read_text())
        _R["im"] = _R["ao"].tokenizer.convert_tokens_to_ids("<|im_start|>")
    return _R


def _no_thinking(content):
    if isinstance(content, list):
        return [b for b in content if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
    return content


def norm(req: dict) -> dict:
    """The fixtures are GLM runs from the old harness: fix the per-second timestamp, and drop the
    thinking blocks (the Qwen runs of this topic are non-thinking; Qwen's template renders reasoning
    only after the last user query, which is itself a non-append-only edit)."""
    req = copy.deepcopy(req)
    req["system"] = re.sub(r"Current time: \S+", "Current time: 2026-09-11T00:00:00", req["system"])
    for m in req.get("messages") or []:
        if m.get("role") == "assistant":
            m["content"] = _no_thinking(m["content"])
    if req.get("response"):
        req["response"]["content"] = _no_thinking(req["response"]["content"])
    return req


def transitions(limit: int | None = None):
    """(prev, cur, old ids = prev prompt + reply without <|im_end|>\\n, new ids) on the fixtures."""
    from render import open_jsonl, with_assistant
    r = renderers()["ao"]
    out = []
    for path in FIXTURES:
        reqs = [norm(x) for x in open_jsonl(path) if x.get("purpose") == "lead" and x.get("response")]
        for prev, cur in zip(reqs, reqs[1:]):
            _, old = r.render(with_assistant(prev, prev["response"]["content"]), add_generation_prompt=False)
            _, new = r.render(cur)
            out.append((prev, cur, old[:-2], new))
            if limit and len(out) >= limit:
                return out
    return out


# -- C1/C2: alignment ---------------------------------------------------------------------------------

def check_tiling(segs, old, new):
    pos = 0
    for s in segs:
        assert s[1] == pos, (s, pos)
        assert s[2] > s[1]
        if s[0] == "reuse":
            assert s[2] - s[1] >= ep.MIN_RUN
            assert old[s[3]:s[3] + s[2] - s[1]] == new[s[1]:s[2]]
        pos = s[2]
    assert pos == len(new) and segs[-1][0] == "fresh"


def test_c1_c2_block_plan_tiles_and_matches_difflib():
    im = renderers()["im"]
    tot_ref = tot_blk = 0
    for _prev, _cur, old, new in transitions(60):
        ref = ep.splice_plan(old, new)
        blk = ep.block_plan(old, new, im)
        check_tiling(ref, old, new)
        check_tiling(blk, old, new)
        tot_ref += sum(s[2] - s[1] for s in ref if s[0] == "reuse")
        tot_blk += sum(s[2] - s[1] for s in blk if s[0] == "reuse")
    assert tot_blk >= 0.995 * tot_ref, (tot_blk, tot_ref)


def test_splice_plan_is_topic10_verbatim():
    import arms  # noqa: F401  (10_rope_shift; sets env, harmless here)
    from arms import splice_plan as ref
    rng = np.random.default_rng(0)
    for _ in range(50):
        old = rng.integers(0, 50, size=int(rng.integers(20, 300))).tolist()
        new = list(old)
        for _ in range(int(rng.integers(0, 4))):
            i = int(rng.integers(0, len(new)))
            new[i:i + int(rng.integers(0, 30))] = rng.integers(0, 50, size=int(rng.integers(0, 30))).tolist()
        new.append(7)
        assert ep.splice_plan(old, new) == ref(old, new)


# -- C3: the think-strip ----------------------------------------------------------------------------------

def test_c3_think_strip_shifts_reply_by_four_under_official_template_only():
    from render import open_jsonl, with_assistant
    r = renderers()
    reqs = [norm(x) for x in open_jsonl(FIXTURES[0]) if x.get("purpose") == "lead" and x.get("response")]
    prev, cur = reqs[0], reqs[1]
    for name, delta_expected in (("off", -4), ("ao", 0)):
        rend = r[name]
        _, gen_prompt = rend.render(prev)                       # ends with the empty think block
        _, seq = rend.render(with_assistant(prev, prev["response"]["content"]), add_generation_prompt=False)
        # both templates render the LAST assistant turn with the think block, exactly as generated
        assert seq[:len(gen_prompt)] == gen_prompt
        old = seq[:-2]                                           # what the server's KV holds
        _, new = rend.render(cur)
        segs = ep.block_plan(old, new, r["im"])
        runs = [s for s in segs if s[0] == "reuse" and s[3] >= len(gen_prompt)]
        if name == "off":
            assert runs and runs[0][1] - runs[0][3] == delta_expected, segs[:4]
        else:
            assert segs[0][0] == "reuse" and segs[0][2] >= len(old) - 1, segs[:3]


# -- C4/C5: plans and rows --------------------------------------------------------------------------------

def test_c4_partitions_per_arm():
    im = renderers()["im"]
    for _prev, _cur, old, new in transitions(25):
        entry = ep.first_entry(old, np.arange(len(old)), len(old) - 50, 0)
        stats = {}
        for arm in ("prefix", "shift", "epic32", "epic64", "epic128"):
            plan = ep.plan_call(arm, entry, new, im)
            n = len(new)
            covered = np.zeros(n, dtype=int)
            covered[:plan.p0] += 1
            for lo, hi, _s, _d in plan.override:
                covered[lo:hi] += 1
            for lo, hi in plan.compute:
                covered[lo:hi] += 1
            assert (covered == 1).all(), arm
            assert plan.p0 % ep.BLOCK == 0 and plan.p0 < n
            if arm == "prefix":
                assert not plan.override and plan.heads == 0
            heads = ep.run_heads(plan.segs, plan.k, plan.p0, n) if plan.k else set()
            assert plan.heads == len(heads)
            for lo, hi, slot_lo, delta in plan.override:
                for p in range(lo, hi):
                    assert p not in heads
                    src = slot_lo + (p - lo)                     # first_entry: slot == old position
                    assert old[src] == new[p] and p - src == delta
            stats[arm] = plan.stats["C"]
        assert stats["prefix"] >= stats["epic128"] >= stats["epic64"] >= stats["epic32"] >= stats["shift"]


def test_c5_ages_compound_across_chained_calls():
    """A run reused across three edits ages 1, 2, 3; a head recomputed by EPIC resets to 0; the
    prefix arm keeps everything fresh (age 0)."""
    rng = np.random.default_rng(1)
    head = rng.integers(1000, 2000, size=64).tolist()
    body = rng.integers(2000, 3000, size=400).tolist()
    for arm in ("shift", "epic32"):
        pool = ep.SlotPool(10_000)
        seq = head + [5] * 20 + body + [9]
        entry = None
        slots_entry = None
        for step in range(4):
            marker = rng.integers(3000, 4000, size=20 + step).tolist()  # an edit before the body
            new = head + marker + body + [9]
            plan = ep.plan_call(arm, entry, new, im_start=-1)
            new_slots = pool.alloc(plan.stats["C"])
            decode = pool.alloc(4)
            entry2 = ep.next_entry(plan, new, [11, 12, 13], new_slots, decode, step)
            pool.retain(entry2.slot)                      # as epic_server_ext._finish does
            pool.release(new_slots)
            pool.release(decode)
            if entry is not None:
                pool.release(entry.slot)
            entry = entry2
            seq = new
        b0 = len(head) + 20 + 3
        mid = b0 + 200
        assert entry.age[mid] == 3, (arm, entry.age[mid])
        if arm == "epic32":
            assert entry.age[b0] == 0 and entry.age[b0 + 31] == 0 and entry.age[b0 + 32] == 3
        assert pool.used == len(np.unique(entry.slot))
        del seq, slots_entry


# -- C6: slot pool --------------------------------------------------------------------------------------

def test_c6_slot_pool_refcounts():
    pool = ep.SlotPool(100)
    a = pool.alloc(40)
    b = pool.alloc(40)
    try:
        pool.alloc(30)
        raise AssertionError("expected exhaustion")
    except MemoryError:
        pass
    assert pool.used == 80
    pool.retain(a[:10])
    pool.release(a)
    assert pool.used == 50                                # a[:10] still referenced once
    c = pool.alloc(30)
    assert len(c) == 30 and not set(c) & set(a[:10]) and not set(c) & set(b)
    pool.release(a[:10])
    pool.release(b)
    pool.release(c)
    assert pool.used == 0 and pool.free_ranges == [[0, 100]]


def test_runs_of_groups_contiguous_rows():
    pos = np.array([5, 6, 7, 10, 11, 12])
    slot = np.zeros(20, dtype=np.int64)
    birth = np.arange(20)
    slot[[5, 6, 7]] = [100, 101, 102]
    slot[[10, 11, 12]] = [103, 104, 200]
    birth[[10, 11, 12]] = [3, 4, 5]
    runs = ep.runs_of(pos, slot, birth)
    assert runs == [[5, 8, 100, 0], [10, 12, 103, 7], [12, 13, 200, 7]], runs


# -- C7: the session's client patch ------------------------------------------------------------------------

def test_c7_capture_tags_lead_calls_rewrites_ids_and_translates_overflow(tmp_path=None):
    import tempfile
    from anthropic.resources.messages import messages as sdk_messages
    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

    import epic_session as es
    from mu_session import Budget

    saved = (sdk_messages.Messages.create, sdk_messages.Messages.stream)
    seen = []

    def fake_create(self, *args, **kwargs):
        seen.append(kwargs)
        if kwargs.get("max_tokens") == 999:
            exc = Exception("Error code: 400 - This model's maximum context length is 32768 tokens")
            exc.status_code = 400
            raise exc
        return Message(id="m", type="message", role="assistant", model="x", stop_reason="tool_use",
                       content=[TextBlock(type="text", text="hi"),
                                ToolUseBlock(type="tool_use", id="chatcmpl-tool-abc", name="glob", input={})],
                       usage=Usage(input_tokens=1, output_tokens=1))

    purposes = iter(["lead", "compaction_summary", "lead"])
    harness = types.SimpleNamespace(TRACE=types.SimpleNamespace(
        capture_context=lambda: {"model_purpose": next(purposes), "agent_id": "a", "turn_id": "t"}))
    sys.modules["s15_profiled_harness"] = harness
    sdk_messages.Messages.create = fake_create
    try:
        with tempfile.TemporaryDirectory() as tmp:
            counter = es.install_capture(Path(tmp) / "r.jsonl", "L-epic64", "epic64", 1,
                                         Budget(10, 10, 100), 60.0, seed_key="L~a1")
            client = types.SimpleNamespace()
            r1 = sdk_messages.Messages.create(client, model="m", max_tokens=10, messages=[])
            sdk_messages.Messages.create(client, model="m", max_tokens=10, messages=[])
            try:
                sdk_messages.Messages.create(client, model="m", max_tokens=999, messages=[])
                raise AssertionError("expected PromptTooLong")
            except es.PromptTooLong as exc:
                assert not re.search(r"\d", str(exc))
            try:
                sdk_messages.Messages.stream(client, model="m")
                raise AssertionError("stream must be refused")
            except RuntimeError:
                pass
            counter["_handle"].close()
            recs = [json.loads(x) for x in (Path(tmp) / "r.jsonl").read_text().splitlines()]
        assert r1.content[1].id == "toolu_00000_0"
        tags = [(kw.get("extra_body") or {}).get("kv_transfer_params") for kw in seen]
        assert tags[0]["epic"] == {"arm": "epic64", "spec": "L-epic64~a1", "call": 0, "seed_key": "L~a1"}
        assert tags[1] is None                       # compaction summary: untagged
        assert recs[0]["response"]["tool_id_map"] == {"chatcmpl-tool-abc": "toolu_00000_0"}
        assert recs[2].get("overflow") is True
    finally:
        sdk_messages.Messages.create, sdk_messages.Messages.stream = saved
        sys.modules.pop("s15_profiled_harness", None)


# -- C12: the append-only template --------------------------------------------------------------------------

def test_c12_append_only_template_extends_every_non_compaction_transition():
    from events import classify
    ext = {"off": 0, "ao": 0}
    n = 0
    from render import open_jsonl, with_assistant
    r = renderers()
    for path in FIXTURES:
        reqs = [norm(x) for x in open_jsonl(path) if x.get("purpose") == "lead" and x.get("response")]
        for prev, cur in zip(reqs, reqs[1:]):
            if classify(prev, cur) is not None:               # a compaction edit: not append-only
                continue
            n += 1
            for name in ("off", "ao"):
                _, old = r[name].render(with_assistant(prev, prev["response"]["content"]),
                                        add_generation_prompt=False)
                _, new = r[name].render(cur)
                old = old[:-2]
                ext[name] += new[:len(old)] == old
    assert n > 0
    assert ext["ao"] == n, (ext, n)
    assert ext["off"] < n, (ext, n)


# -- C8: the middleware against a fake engine ----------------------------------------------------------------

def test_c8_middleware_plans_tracks_entries_and_frees_slots():
    """epic_server_ext's patched generate on a fake AsyncLLM: tagged EPIC calls get a splice plan,
    salt and seed; the session entry advances; slots are reference-counted and freed at session end;
    shadow probes run (fake RPC) without leaking slots; prefix calls carry no plan."""
    import asyncio
    import importlib
    import os
    import tempfile

    from vllm.sampling_params import SamplingParams

    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update(EPIC_LOG=str(Path(tmp) / "calls.jsonl"), EPIC_POOL_ROWS="50000",
                          EPIC_SHADOW_EVERY="1", EPIC_CF_EVERY="1", EPIC_IM_START="-7")
        import epic_server_ext as ext
        ext = importlib.reload(ext)
        seen = []

        class FakeOut:
            def __init__(self, ids, gen, finished):
                self.prompt_token_ids = ids
                self.finished = finished
                self.num_cached_tokens = 0
                self.kv_transfer_params = {"epic_server": {"apc_hit": 0}}
                lp = [{t: types.SimpleNamespace(logprob=-0.1)} for t in gen]
                self.outputs = [types.SimpleNamespace(token_ids=list(gen), logprobs=lp, text="x",
                                                      finish_reason="stop" if finished else None)]
                self.prompt_logprobs = [None] * len(ids) + []

        class FakeEngine:
            async def collective_rpc(self, fn, args=(), kwargs=None):
                arm_slots, deltas, fresh, bins, n_bins = args
                return [[[[0.0, 1.0, 0.0, 1.0, 1.0] for _ in range(n_bins)] for _ in range(2)]]

        async def fake_generate(self, prompt, sp, rid, *a, **k):
            seen.append((rid, dict(prompt), sp))
            ids = prompt["prompt_token_ids"]
            if sp.prompt_logprobs:
                out = FakeOut(ids, [5], True)
                out.prompt_logprobs = [None] + [{t: types.SimpleNamespace(logprob=-0.2)} for t in ids[1:]]
                yield out
                return
            yield FakeOut(ids, [11, 12, 13][: sp.max_tokens or 3], True)

        ext._ORIG["generate"] = fake_generate
        engine = FakeEngine()
        rng = np.random.default_rng(3)
        head = rng.integers(100, 200, size=80).tolist()
        body = rng.integers(200, 300, size=300).tolist()

        async def call(arm, n, prompt):
            sp = SamplingParams(max_tokens=3, extra_args={"kv_transfer_params": {"epic": {
                "arm": arm, "spec": f"S-{arm}", "call": n, "seed_key": "BLOCK"}}})
            async for _ in ext._generate(engine, {"prompt_token_ids": prompt}, sp, f"r{n}"):
                pass
            return sp

        async def main():
            sps = {}
            for arm in ("prefix", "epic32"):
                p1 = head + body + [9]
                await call(arm, 0, p1)
                p2 = head + rng.integers(300, 400, size=7).tolist() + body + [9, 11, 12] + [8]
                sps[arm] = await call(arm, 1, p2)
            return sps

        sps = asyncio.run(main())
        logs = [json.loads(x) for x in (Path(tmp) / "calls.jsonl").read_text().splitlines()]
        calls = [x for x in logs if x.get("ev") == "call"]
        errors = [x for x in logs if x.get("ev") == "error"]
        assert not errors, errors[:1]
        epic = [x for x in calls if x["arm"] == "epic32"]
        assert epic[1]["S"] > 0 and epic[1]["H"] == 32 and "shadow" in epic[1], epic[1]
        assert "splice" in sps["epic32"].extra_args["kv_transfer_params"]
        assert "splice" not in sps["prefix"].extra_args["kv_transfer_params"]
        assert sps["prefix"].seed == sps["epic32"].seed            # common random numbers
        salts = {p.get("cache_salt") for _r, p, _s in seen if not _r.startswith("epicshadow")}
        assert salts == {"prefix:S-prefix", "epic32:S-epic32"}
        used = ext.POOL.used
        assert used == len(np.unique(ext.SESSIONS["S-epic32"].entry.slot))
        ext.end_session("S-epic32")
        ext.end_session("S-prefix")
        assert ext.POOL.used == 0


# -- C13: the prefix cache serving beyond p0 (overshoot) ------------------------------------------------------

class _FakeReq:
    def __init__(self, rid, n, splice):
        self.request_id, self.num_tokens = rid, n
        self.kv_transfer_params = {"splice": splice}


def _sched_output(rid, blocks, computed, scheduled):
    new = types.SimpleNamespace(req_id=rid, block_ids=(blocks,), num_computed_tokens=computed)
    cached = types.SimpleNamespace(req_ids=[], new_block_ids=[], resumed_req_ids=set(), num_computed_tokens=[])
    return types.SimpleNamespace(scheduled_new_reqs=[new], scheduled_cached_reqs=cached,
                                 num_scheduled_tokens={rid: scheduled})


def test_c13_connector_copies_rows_the_prefix_cache_served_beyond_p0():
    """A request planned with p0 = 2848 whose prefix-cache hit is 2880: the first step saves [2848, 2880)
    from the cached blocks into the apc_save reserve (and the computed [2880, n) into its own slots); the
    overrides it would have applied below 2880 are not emitted; request_finished reports it."""
    import epic_connector as ecn

    cfg = types.SimpleNamespace(cache_config=types.SimpleNamespace(block_size=16))
    side = ecn._SchedulerSide(cfg)
    n = 4000
    splice = {"p0": 2848, "load": [], "from_layer": 0,
              "override": [[2860, 3900, 500, 7]],
              "save": [[2848, 2860, 10_000], [3900, 4000, 10_012]],
              "apc_save": [[2848, 3104, 20_000]]}
    req = _FakeReq("r", n, splice)
    assert side.get_num_new_matched_tokens(req, 2880) == (0, False)
    blocks = list(range(100, 100 + n // 16 + 1))
    meta = side.build_connector_meta(_sched_output("r", blocks, 2880, n - 2880))
    apc = [op for op in meta.saves if op.pos and op.pos[0] == 2848]
    assert len(apc) == 1 and apc[0].pos == list(range(2848, 2880)) and apc[0].slots == list(range(20_000, 20_032))
    rest = [op for op in meta.saves if op is not apc[0]]
    assert rest and rest[0].pos[0] == 3900                   # [2848, 2860) was cache-served, not computed
    assert meta.overrides and meta.overrides[0].segs[0][:3] == [2880, 3900, 520]
    fin = side.request_finished(req)["epic_server"]
    assert fin["overshoot"] == 32 and fin["apc_saved"] == 32 and fin["apc_hit"] == 2880
    # no overshoot: nothing extra is saved and none is reported
    req2 = _FakeReq("q", n, dict(splice))
    side.get_num_new_matched_tokens(req2, 2848)
    meta2 = side.build_connector_meta(_sched_output("q", blocks, 2848, n - 2848))
    assert all(op.pos[0] != 2848 or op.slots[0] == 10_000 for op in meta2.saves)
    fin2 = side.request_finished(req2)["epic_server"]
    assert fin2["overshoot"] == 0 and fin2["apc_saved"] == 0


def test_c13_middleware_absorbs_small_overshoots_and_resets_large_ones():
    """The entry takes the reserve's rows for [p0, p0 + over) (fresh, birth = position) when the connector
    caught them; an overshoot beyond the reserve resets the session under a new salt, whose next call
    plans from scratch; slots stay balanced throughout."""
    import asyncio
    import importlib
    import os
    import tempfile

    from vllm.sampling_params import SamplingParams

    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update(EPIC_LOG=str(Path(tmp) / "calls.jsonl"), EPIC_POOL_ROWS="50000",
                          EPIC_SHADOW_EVERY="0", EPIC_IM_START="-7", EPIC_APC_RESERVE="64")
        import epic_server_ext as ext
        ext = importlib.reload(ext)
        seen = []
        server = {}

        async def fake_generate(self, prompt, sp, rid, *a, **k):
            seen.append((rid, dict(prompt), sp))
            splice = (sp.extra_args or {}).get("kv_transfer_params", {}).get("splice") or {}
            over = server.get(rid, 0)
            saved = sum(min(hi, splice.get("p0", 0) + over) - lo for lo, hi, _s in splice.get("apc_save", [])
                        if lo < splice.get("p0", 0) + over)
            gen = [11, 12, 13]
            out = types.SimpleNamespace(
                finished=True, num_cached_tokens=splice.get("p0", 0) + over,
                kv_transfer_params={"epic_server": {"apc_hit": splice.get("p0", 0) + over, "overshoot": over,
                                                    "apc_saved": saved}},
                outputs=[types.SimpleNamespace(token_ids=gen, text="x", finish_reason="stop",
                                               logprobs=[{t: types.SimpleNamespace(logprob=-0.1)} for t in gen])])
            yield out

        ext._ORIG["generate"] = fake_generate
        rng = np.random.default_rng(5)
        head = rng.integers(100, 200, size=160).tolist()
        body = rng.integers(200, 300, size=400).tolist()

        async def call(n, prompt):
            sp = SamplingParams(max_tokens=3, extra_args={"kv_transfer_params": {"epic": {
                "arm": "epic32", "spec": "S", "call": n, "seed_key": "B"}}})
            async for _ in ext._generate(None, {"prompt_token_ids": prompt}, sp, f"r{n}"):
                pass

        p1 = head + body + [9]
        p2 = head + rng.integers(300, 400, size=9).tolist() + body + [9, 11, 12] + [8]
        p3 = head + rng.integers(400, 500, size=5).tolist() + body + [9, 11, 12, 8, 21, 22] + [7]
        p4 = p3[:-1] + [31, 32, 7]
        server.update({"r1": 32, "r2": 200})

        async def main():
            await call(0, p1)
            await call(1, p2)
            entry = ext.SESSIONS["S"].entry
            p0 = 160 // 16 * 16
            assert list(entry.birth[p0:p0 + 32]) == list(range(p0, p0 + 32))
            assert int(entry.age[p0:p0 + 32].max()) == 0
            assert ext.POOL.used == len(np.unique(entry.slot))
            await call(2, p3)                               # overshoot 200 > reserve 64: reset
            assert ext.SESSIONS["S"].entry is None and ext.SESSIONS["S"].salt == "epic32:S:r1"
            assert ext.POOL.used == 0
            await call(3, p4)
            assert ext.SESSIONS["S"].entry is not None

        asyncio.run(main())
        logs = [json.loads(x) for x in (Path(tmp) / "calls.jsonl").read_text().splitlines()]
        calls = {x["call"]: x for x in logs if x.get("ev") == "call"}
        assert not [x for x in logs if x.get("ev") == "error"]
        assert calls[1]["apc_over"] == 32 and "entry_reset" not in calls[1]
        assert calls[1]["apc_over_S"] + calls[1]["apc_over_C"] == 32
        assert calls[2]["entry_reset"] == "overshoot"
        assert calls[3]["E"] == 0                           # planned from scratch after the reset
        salts = [p.get("cache_salt") for _r, p, _s in seen]
        assert salts == ["epic32:S", "epic32:S", "epic32:S", "epic32:S:r1"], salts
        ext.end_session("S")
        assert ext.POOL.used == 0


def test_c9_latency_model_arithmetic():
    import epic_latency as el
    beta = [40.0, 0.1, 0.002, 0.003]
    assert el.predict(beta, 1000, 500, 8000) == 40.0 + 100.0 + 0.002 * 1000 * 8.0 + 1.5
    tp = [{"ctx": 2000, "n_seq": 1, "tpot_ms": 26.0}, {"ctx": 10000, "n_seq": 1, "tpot_ms": 28.0},
          {"ctx": 2000, "n_seq": 2, "tpot_ms": float("nan")}]
    assert el.tpot_at(tp, 1000) == 26.0 and el.tpot_at(tp, 6000) == 27.0 and el.tpot_at(tp, 30000) == 28.0
    timing = [(0, 0.0, 1.0), (1, 3.0, 4.0), (2, 4.5, 6.0)]
    assert el.tool_s(timing) == 2.5 and el.tool_s(timing, 2) == 2.0
    # busy share: the union of the other calls' overlap, not the sum
    calls = [{"t0": 0.0, "t1": 10.0}, {"t0": 2.0, "t1": 5.0}, {"t0": 4.0, "t1": 6.0}, {"t0": 20.0, "t1": 21.0}]
    el.busy_share(calls)
    assert abs(calls[0]["share2"] - 0.4) < 1e-12 and calls[1]["share2"] == 1.0 and calls[3]["share2"] == 0.0


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
