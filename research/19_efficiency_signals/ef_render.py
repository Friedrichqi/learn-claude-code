"""Render an s15 lead request the way topic 19's MiniMax-M2.7 server does, on CPU (no GPU, no weights).

vLLM 0.31's own path:
  1. the Anthropic /v1/messages request goes through AnthropicServingMessages.to_chat_completion_request;
  2. parse_chat_messages parses it with the content format vLLM detects for the template;
  3. safe_apply_chat_template renders it with the model's tokenizer.

Used by test_minimax_template.py and ef_calibrate.py.

    from ef_render import render, tokens
    text = render({"system": ..., "tools": [...], "messages": [...]})   # with the generation prompt
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

DIR = Path(__file__).resolve().parent
SNAPSHOT = Path("/data/fritz/hf/hub/models--MiniMaxAI--MiniMax-M2.7/snapshots/d494266a4affc0d2995ba1fa35c8481cbd84294b")
TEMPLATE = DIR / "minimax_m2_append_only.jinja"

_STATE: dict = {}


def _setup():
    if _STATE:
        return
    from vllm.config import ModelConfig
    from vllm.tokenizers import get_tokenizer

    _STATE["mc"] = ModelConfig(model=str(SNAPSHOT), tokenizer=str(SNAPSHOT), trust_remote_code=True,
                               max_model_len=65536)
    _STATE["tok"] = get_tokenizer(str(SNAPSHOT), trust_remote_code=True)
    _STATE["formats"] = {}


def tokenizer():
    _setup()
    return _STATE["tok"]


def render(request: dict, gen: bool = True, template: str | None = None) -> str:
    """The prompt text of `request` ({system, tools, messages} as s15 sends them; tools may be bare names)."""
    from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages
    from vllm.entrypoints.chat_utils import parse_chat_messages
    from vllm.renderers.hf import resolve_chat_template_content_format, safe_apply_chat_template

    _setup()
    mc, tok, formats = _STATE["mc"], _STATE["tok"], _STATE["formats"]
    template = template or TEMPLATE.read_text()
    if template not in formats:
        formats[template] = resolve_chat_template_content_format(
            chat_template=template, tools=None, given_format="auto", tokenizer=tok, model_config=mc)
    tools = [t if isinstance(t, dict) else {"name": t, "description": f"The {t} tool.",
                                              "input_schema": {"type": "object", "properties": {}}}
             for t in request.get("tools") or []]
    areq = AnthropicMessagesRequest(model="m", max_tokens=1, system=request.get("system"),
                                    messages=request["messages"], tools=tools or None)
    creq = AnthropicServingMessages.to_chat_completion_request(areq)
    conv, _, _ = parse_chat_messages(creq.messages, mc, content_format=formats[template])
    tool_dicts = [t.model_dump() for t in creq.tools] if creq.tools else None
    return safe_apply_chat_template(mc, tok, conv, chat_template=template, tools=tool_dicts,
                                    add_generation_prompt=gen, tokenize=False)


def tokens(text: str) -> list[int]:
    return tokenizer().encode(text, add_special_tokens=False)
