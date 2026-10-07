"""Tests of research/13_tiered_memory (CPU only; the D1 test loads the Qwen3 tokenizer from the HF cache).

    pytest research/13_tiered_memory/test_tm.py -q
    TM_SLOW=1 pytest research/13_tiered_memory/test_tm.py -q -k pipeline     # + the fake-server pipeline run (~5 min)
"""
from __future__ import annotations

import asyncio
import json
import lzma
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import _paths  # noqa: E402,F401

import tm_common as tc  # noqa: E402

REPO = HERE.parents[1]
P1B = HERE.parent / "12_hw_utilization" / "data" / "cells" / "p1b" / "levels" / "hw_p1b" / "runs"


# -- tm_common -----------------------------------------------------------------------------------------------------
def test_tool_key():
    assert tc.tool_key("read_file", {"path": "x"}) == "read_file"
    assert tc.tool_key("bash", {"command": "python3 -m pytest -q -p no:cacheprovider tests"}) == "bash:pytest/suite"
    assert tc.tool_key("bash", {"command": "python3 -m pytest -q profiling_sandbox/heavy"}) == "bash:pytest"
    site = "/mnt/home/yqi10/.venv/lib/python3.12/site-packages"
    assert tc.tool_key("bash", {"command": f"grep -rl 'import torch' {site}/vllm | wc -l"}) == "bash:grep/wide"
    assert tc.tool_key("bash", {"command": f"grep -c def {site}/vllm/__init__.py"}) == "bash:grep"
    assert tc.tool_key("bash", {"command": f"find {site} -name '*.py' | wc -l"}) == "bash:find/wide"
    assert tc.tool_key("bash", {"command": "find . -name '*.py'"}) == "bash:find"
    assert tc.tool_key("bash", {"command": "ls /tmp/yqi10/x"}) == "bash:view"


def test_predict_lookup(tmp_path):
    t = {"windows": {"bash:pytest/suite": {"p50_s": 18.0, "p90_s": 20.0}, "read_file": {"p50_s": 0.01, "p90_s": 0.07}},
         "tool_time": {"bash:find": {"p50_s": 0.02, "p90_s": 0.05}}, "overhead_s": 0.1,
         "global": {"p50_s": 0.5, "p90_s": 9.0}}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(t))
    assert tc.predict("bash:pytest/suite", str(path))[:2] == (18.0, 20.0)
    assert tc.predict("bash:find/wide", str(path))[2] == "tool_time:bash:find"
    assert tc.predict("nope", str(path))[:2] == (0.5, 9.0)
    tc._table.cache_clear()


def test_tool_calls_in_and_chain():
    text = ('ok\n<tool_call>\n{"name": "bash", "arguments": {"command": "ls"}}\n</tool_call>\n'
            '<tool_call>\n{"name": "read_file", "arguments": "{\\"path\\": \\"a\\"}"}\n</tool_call>')
    assert tc.tool_calls_in(text) == [("bash", {"command": "ls"}), ("read_file", {"path": "a"})]
    assert tc.chain_of("MU-x-L1~a1~00003~ld~root", "MU-x-L1~a1") == "MU-x-L1~a1~ld~root"
    assert tc.chain_of(None, "s") == "s"
    p50, p90, src = tc.predict_reply([], 30.0)
    assert (p50, src) == (30.0, "think")


# -- tm_client ---------------------------------------------------------------------------------------------------------
def test_client_prefix_matches_sdk_objects():
    from anthropic.types import TextBlock, ToolUseBlock
    import tm_client
    use = ToolUseBlock(id="t1", name="bash", input={"command": "ls"}, type="tool_use")
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [use]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a b"}]}]
    as_dicts = [msgs[0], {"role": "assistant", "content": [use.model_dump()]}, msgs[2]]
    assert tm_client.common_prefix(as_dicts, msgs) == 3
    edited = [msgs[0], msgs[1], {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                                              "content": "[saved]"}]}]
    assert tm_client.common_prefix(edited, msgs) == 2
    assert not tm_client.same_message({"role": "assistant", "content": [TextBlock(text="x", type="text")]},
                                      {"role": "assistant", "content": [TextBlock(text="y", type="text")]})
    # fingerprints taken at send time see an in-place edit of the SAME dict (the harness's micro-compaction)
    known = [tm_client.msg_fp(m) for m in msgs]
    msgs[2]["content"][0]["content"] = "[Earlier tool result saved at /tmp/x]"
    assert tm_client.common_prefix(msgs, known) == 2
    assert tm_client.msg_fp({"role": "assistant", "content": [use]}) == tm_client.msg_fp(as_dicts[1])


# -- D1: delta render + offsets vs a full render, on recorded sessions --------------------------------------------------
def _tokenizer():
    os.environ.setdefault("HF_HOME", "/projects/co/jlin4/agenticllm/hf_cache")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained("Qwen/Qwen3-8B")


def _flatten(messages: list) -> list:
    """vLLM-like conversation for an HF template: text parts joined, tool-call arguments as dicts."""
    out = []
    for m in messages:
        m = dict(m)
        c = m.get("content")
        if isinstance(c, list):
            m["content"] = "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
        if m.get("tool_calls"):
            calls = []
            for tcall in m["tool_calls"]:
                f = dict(tcall["function"])
                if isinstance(f.get("arguments"), str):
                    try:
                        f["arguments"] = json.loads(f["arguments"])
                    except json.JSONDecodeError:
                        pass
                calls.append({**tcall, "function": f})
            m["tool_calls"] = calls
        out.append(m)
    return out


class _FakeRenderer:
    chat_template = None
    chat_template_content_format = "auto"
    default_chat_template_kwargs = {"enable_thinking": False}

    def __init__(self, tok):
        self.tok = tok

    def _ids(self, messages, tools=None):
        text = self.tok.apply_chat_template(_flatten(messages), tools=tools, add_generation_prompt=True,
                                            enable_thinking=False, tokenize=False)
        return self.tok.encode(text, add_special_tokens=False)

    async def preprocess_chat(self, request, messages, default_template=None, default_template_content_format=None,
                              default_template_kwargs=None, tool_dicts=None, parser=None):
        return [], [{"prompt_token_ids": list(self._ids(messages, tool_dicts))}]

    async def render_chat(self, chat_req):
        tools = [t.model_dump()["function"] if "function" in t.model_dump() else t.model_dump()
                 for t in chat_req.tools] if chat_req.tools else None
        return [], [{"prompt_token_ids": list(self._ids(chat_req.messages, tools))}]


def _serving(tok):
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages as A
    return SimpleNamespace(_convert_messages=A._convert_messages, to_chat_completion_request=A.to_chat_completion_request,
                           _merge_inline_system=False, online_renderer=_FakeRenderer(tok),
                           renderer=SimpleNamespace(tokenizer=tok))


def _reply_ids(tok, serving, assistant_msg) -> list[int]:
    """Token ids of an assistant turn as the model would generate it after the generation prompt."""
    oa = [{"role": "user", "content": "x"}]
    serving._convert_messages([assistant_msg], oa)
    text = tok.apply_chat_template(_flatten(oa), add_generation_prompt=False, enable_thinking=False, tokenize=False)
    body = text.split("<|im_start|>assistant\n", 1)[1].split("<|im_end|>", 1)[0]
    if body.startswith("<think>\n\n</think>\n\n"):
        body = body[len("<think>\n\n</think>\n\n"):]
    return tok.encode(body, add_special_tokens=False) + [tc.IM_END]


def _lead_chain(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in lzma.open(path, "rt", encoding="utf-8")]
    return [r for r in rows if r.get("purpose") == "lead" and r.get("response") and not r.get("error")]


@pytest.mark.skipif(not P1B.exists(), reason="12's p1b captures are not on disk")
def test_d1_matches_full_render_on_recorded_sessions():
    import tm_client
    import tm_server_ext as ext
    from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
    tok = _tokenizer()
    serving = _serving(tok)
    caps = sorted(P1B.glob("*/*.requests.jsonl.xz"))[:3]
    assert caps
    checked = edits = 0
    for cap in caps:
        rows = _lead_chain(cap)[:40]
        st = None
        known = None
        for row in rows:
            body = {k: row[k] for k in ("model", "max_tokens", "system", "tools", "messages") if row.get(k) is not None}
            req = AnthropicMessagesRequest(**body)
            msgs = row["messages"]
            k = tm_client.common_prefix(msgs, known) if known is not None else 0
            full_ids = asyncio.run(serving.online_renderer.render_chat(serving.to_chat_completion_request(req)))[1][0][
                "prompt_token_ids"]
            assistant = {"role": "assistant", "content": row["response"]["content"]}
            gen = _reply_ids(tok, serving, req.model_validate({**body, "messages": [assistant]}).messages[0])
            if st is None or k <= 0 or k >= len(msgs):
                blocks, sys_block, _ = ext._message_blocks(serving, req)
                ctx = ext.Ctx(chain="c", session="s", rid=None, mode="init", msg_blocks=blocks, sys_block=sys_block)
                ids, offsets = ext.next_state(ctx, None, full_ids, gen)
            else:
                prefix = st.ids[:ext.cut_at(st, k)]
                delta, rel = asyncio.run(ext._render_delta(serving, req, req.messages[k:]))
                d1 = list(prefix) + delta
                cmp = ext.compare_blocks(tok, d1, full_ids)
                assert cmp["other_diff"] == 0, cmp["first_other_diff"]
                assert cmp["blocks_d1"] == cmp["blocks_full"]
                checked += 1
                edits += k < len(st.offsets)
                ctx = ext.Ctx(chain="c", session="s", rid=None, mode="delta", k=k, prefix_len=len(prefix),
                              rel_offsets=rel)
                ids, offsets = ext.next_state(ctx, st, d1, gen)
            st = ext.ChainState(ids, offsets)
            assert len(st.offsets) == len(msgs) + 1
            known = msgs + [assistant]
    assert checked >= 20, checked


# -- B1: demotion order and the DRAM policy ------------------------------------------------------------------------------
def test_demote_moves_session_blocks_to_the_front():
    from vllm.v1.core.kv_cache_utils import FreeKVCacheBlockQueue, KVCacheBlock
    import tm_scheduler
    n = 12
    blocks = [KVCacheBlock(i) for i in range(n)]
    q = FreeKVCacheBlockQueue(blocks)
    owner, cidx, chash = [-1] * n, [0] * n, [0] * n
    for bid, (ci, i) in {3: (0, 0), 5: (0, 1), 7: (0, 2), 8: (1, 0), 9: (0, 3)}.items():
        blocks[bid].set_block_hash(b"h%d" % bid, 16)
        owner[bid], cidx[bid], chash[bid] = ci, i, 100 + bid
    chains = [SimpleNamespace(idx=0, key=("A", "ld"), inflight=0, last_fin=None, last_hashes=[]),
              SimpleNamespace(idx=1, key=("B", "ld"), inflight=1, last_fin=None, last_hashes=[103])]
    events = []
    fake = SimpleNamespace(connector=object(), tm_no_tier=False, chain_list=chains, nblk=n, owner=owner, cidx=cidx,
                           chash=chash, bp=SimpleNamespace(blocks=blocks, free_block_queue=q),
                           tm_sess={"A": {"demoted": False, "t_away": 0.0}}, _emit=events.append)
    tm_scheduler.TMScheduler._tm_demote(fake, "A", 1.0, "predicted")
    order = [q.popleft().block_id for _ in range(4)]
    assert order == [9, 7, 5, 0]          # A's blocks tail first; block 3 is in B's last prompt (shared): kept
    assert events[-1]["moved"] == 3 and events[-1]["kept_shared"] == 1


def test_session_aware_policy_order():
    import tm_policy
    from vllm.v1.kv_offload.base import make_offload_key
    from vllm.v1.kv_offload.cpu.policies.base import BlockStatus
    pol = tm_policy.SessionAwareLRU(16)
    sess = {}
    for i, s in enumerate(["C", "A", "B", "C", "A", "B"]):
        h = b"hash%02d" % i
        b = BlockStatus(i)
        b.ref_cnt = 0
        pol.insert(make_offload_key(h, 0), b)
        sess[h] = s
    tm_policy.REG["sess_of_hash"].clear()
    tm_policy.REG["sess_of_hash"].update(sess)
    tm_policy.REG["state"].clear()
    tm_policy.REG["state"].update({"A": {"ended": True, "state": "away", "ret": 1e9},
                                   "B": {"ended": False, "state": "away", "ret": 50.0},
                                   "C": {"ended": False, "state": "running"}})
    out = pol.evict(5, set())
    got = [sess[k[:-4]] for k, _ in out]
    assert got == ["A", "A", "C", "C", "B"]       # ended first, then LRU, the away session (about to return) last
    tm_policy.REG["state"].clear()
    tm_policy.REG["sess_of_hash"].clear()


# -- the pipeline against the D1 fake server ------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("TM_SLOW") != "1", reason="slow: set TM_SLOW=1")
def test_pipeline_fake_server():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        env = {**os.environ, "TM_TEST_CELLS": "1", "TM_DATA": str(d / "data"), "TM_WINDOW_SCALE": "0.1",
               "VLLM_BIN": f"{sys.executable} {HERE / 'tm_fake_vllm.py'}", "JOB_SECONDS": "3000",
               "LOCAL_ROOT": str(d / "local"), "TM_FAKE_LOG": str(d / "fake.jsonl")}
        env.pop("SLURM_JOB_ID", None)
        r = subprocess.run(["bash", str(HERE / "tm_pipeline.sh"), "tt"], env=env, capture_output=True, text=True,
                           timeout=1800)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        levels = d / "data" / "cells" / "tt" / "levels"
        for name in ("tm_tt_base", "tm_tt_d1v", "tm_tt_d1"):
            assert (levels / name / "WINDOW_DONE").exists(), name
        fake = [json.loads(line) for line in open(d / "fake.jsonl")]
        modes = {m: sum(r.get("mode") == m for r in fake) for m in ("full", "delta", "verify", "409")}
        assert modes["delta"] > 0 and modes["verify"] > 0, modes
        assert all(r.get("verify_ok", True) for r in fake)
        assert not any(r.get("delta_had_tools") for r in fake)
        client = [json.loads(line) for line in open(levels / "tm_tt_d1" / "client.jsonl")]
        assert any(c["mode"] == "delta" and c["sent_msgs"] < c["n_msgs"] for c in client)


# -- B2 / B3: the proxy ------------------------------------------------------------------------------------------------
def test_pd_proxy_routing():
    from aiohttp import ClientSession, web
    import pd_proxy

    async def run():
        seen = {"A": [], "B": []}

        def fake(name):
            async def messages(req):
                body = await req.json()
                seen[name].append((req.headers.get("X-Session-ID"), body))
                out = {"id": "x", "type": "message", "role": "assistant", "model": "m", "stop_reason": "end_turn",
                       "content": [{"type": "text", "text": name}], "usage": {"input_tokens": 1, "output_tokens": 1}}
                if (body.get("kv_transfer_params") or {}).get("do_remote_decode"):
                    out["kv_transfer_params"] = {"remote_engine_id": "P", "remote_block_ids": [1, 2]}
                return web.json_response(out)
            app = web.Application()
            app.router.add_post("/v1/messages", messages)
            return app

        runners = []

        async def serve(app):
            r = web.AppRunner(app)
            await r.setup()
            site = web.TCPSite(r, "127.0.0.1", 0)
            await site.start()
            runners.append(r)
            return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

        body = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}
        a, b = await serve(fake("A")), await serve(fake("B"))
        p1 = await serve(pd_proxy.make_app("affine", [a, b], [3, 1]))
        async with ClientSession() as c:
            for s in ["s1", "s2", "s3", "s4", "s1", "s4"]:
                async with c.post(p1 + "/v1/messages", json=body, headers={"X-Session-ID": s}) as r:
                    assert r.status == 200
        assert {s for s, _ in seen["A"]} == {"s1", "s2", "s3"} and [s for s, _ in seen["B"]] == ["s4", "s4"]
        seen["A"].clear()
        seen["B"].clear()
        p2 = await serve(pd_proxy.make_app("pd", [], prefill=a, decode=b))
        async with ClientSession() as c:
            async with c.post(p2 + "/v1/messages", json=body, headers={"X-Session-ID": "s9"}) as r:
                j = await r.json()
        assert j["content"][0]["text"] == "B"
        (_, pbody), = seen["A"]
        (_, dbody), = seen["B"]
        assert pbody["max_tokens"] == 1 and pbody["kv_transfer_params"]["do_remote_decode"]
        assert dbody["kv_transfer_params"] == {"remote_engine_id": "P", "remote_block_ids": [1, 2]}
        assert dbody["max_tokens"] == 5
        for r in runners:
            await r.cleanup()

    asyncio.run(run())


def test_peer_spec_imports():
    import tm_peer_spec
    from vllm.v1.kv_offload.factory import OffloadingSpecFactory
    cls = OffloadingSpecFactory.get_spec_cls({"spec_name": "PeerHBMOffloadingSpec", "spec_module_path": "tm_peer_spec"})
    assert cls is tm_peer_spec.PeerHBMOffloadingSpec


def test_d1_edit_right_after_a_user_message():
    """Regression (S2, 2026-10-04): an edit at k whose previous message is a user message (the harness's todo
    reminder) left the template's "\\n" separator in the stored prefix, and the delta render added its own."""
    import tm_server_ext as ext
    from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
    tok = _tokenizer()
    serving = _serving(tok)
    use = {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}}
    msgs = [{"role": "user", "content": "Explain a.py"},
            {"role": "user", "content": "<reminder>Update your todos.</reminder>"},
            {"role": "assistant", "content": [use]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x = 1\n" * 50}]}]
    body = {"model": "m", "max_tokens": 10, "system": "sys", "messages": msgs[:2]}
    req = AnthropicMessagesRequest(**body)
    full0 = asyncio.run(serving.online_renderer.render_chat(serving.to_chat_completion_request(req)))[1][0]["prompt_token_ids"]
    blocks, sys_block, _ = ext._message_blocks(serving, req)
    gen = _reply_ids(tok, serving, AnthropicMessagesRequest(**{**body, "messages": [msgs[2]]}).messages[0])
    ids, offsets = ext.next_state(ext.Ctx(chain="c", session="s", rid=None, mode="init", msg_blocks=blocks,
                                          sys_block=sys_block), None, full0, gen)
    st = ext.ChainState(ids, offsets)
    edited = [msgs[0], {"role": "user", "content": "<reminder>Update todos now.</reminder>"}, msgs[2], msgs[3]]
    req2 = AnthropicMessagesRequest(**{**body, "messages": edited})
    full = asyncio.run(serving.online_renderer.render_chat(serving.to_chat_completion_request(req2)))[1][0]["prompt_token_ids"]
    k = 1                                                    # message 1 changed: keep only message 0
    delta, _ = asyncio.run(ext._render_delta(serving, req2, req2.messages[k:]))
    cmp = ext.compare_blocks(tok, list(st.ids[:ext.cut_at(st, k)]) + delta, full)
    assert cmp["ok"], cmp["first_other_diff"]
    old = list(st.ids[:st.offsets[k]]) + delta              # the pre-fix cut must be caught by the check
    assert not ext.compare_blocks(tok, old, full)["ok"]
