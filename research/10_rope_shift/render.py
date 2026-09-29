#!/usr/bin/env python3
"""Render captured s15 Messages requests into the exact prompt token ids a vLLM server builds.

The live runs talk to vLLM's Anthropic-compatible /v1/messages endpoint, so the prompt the model saw
is whatever vLLM derived from the Messages request: `AnthropicServingMessages.
to_chat_completion_request` (Anthropic -> OpenAI chat), then the HF renderer path
(`parse_chat_messages` -> `safe_apply_chat_template` -> tokenize with add_special_tokens=False),
with the server's `--default-chat-template-kwargs` merged under the request's own kwargs. This module
calls those same functions offline (CPU, no weights), so an event's `old` / `new` token ids are the
server's, not a re-implementation of the chat template. `check` compares the rendered length with
the `usage.input_tokens` the server reported for every captured call.

    python research/10_rope_shift/render.py check --requests <run>.requests.jsonl.xz --model qwen32b

Library use: `Renderer(MODELS["qwen32b"]).render(request_dict)` -> (prompt_text, token_ids).
"""
from __future__ import annotations

import argparse
import copy
import json
import lzma
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

os.environ.setdefault("HF_HOME", "/projects/co/jlin4/agenticllm/hf_cache")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Every model of the experiment renders with thinking off: the live server runs Qwen3-32B with
# --default-chat-template-kwargs '{"enable_thinking": false}', and the replays use the same setting.
MODELS = {
    "qwen32b": {"hf": "Qwen/Qwen3-32B", "template_kwargs": {"enable_thinking": False},
                "tool_format": "hermes", "cache": "gqa"},
    "qwen8b": {"hf": "Qwen/Qwen3-8B", "template_kwargs": {"enable_thinking": False},
               "tool_format": "hermes", "cache": "gqa"},
    "glm47flash": {"hf": "zai-org/GLM-4.7-Flash", "template_kwargs": {"enable_thinking": False},
                   "tool_format": "glm47", "cache": "mla"},
}
MAX_MODEL_LEN = 40960


def open_jsonl(path) -> list[dict]:
    """Read a .jsonl or .jsonl.xz file. A run the sweep had to kill can end in a half-written line;
    that last line is dropped (a malformed line anywhere else is still an error)."""
    path = Path(path)
    opener = lzma.open if path.suffix == ".xz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    out = []
    for i, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                raise
    return out


def with_assistant(request: dict, response_content: list) -> dict:
    """The request's history plus the assistant turn it produced (what the server's cache holds
    after the call, re-rendered by the template)."""
    out = copy.deepcopy(request)
    out["messages"] = list(out["messages"]) + [{"role": "assistant", "content": response_content}]
    return out


class Renderer:
    """Offline copy of vLLM's /v1/messages prompt construction for one model."""

    def __init__(self, spec: dict, max_model_len: int = MAX_MODEL_LEN):
        from vllm.config import ModelConfig
        from vllm.tokenizers import get_tokenizer

        self.spec = spec
        self.model_id = spec["hf"]
        self.default_kwargs = dict(spec.get("template_kwargs") or {})
        self.model_config = ModelConfig(model=self.model_id, tokenizer=self.model_id,
                                        max_model_len=max_model_len)
        self.tokenizer = get_tokenizer(self.model_id)

    def chat_request(self, request: dict, add_generation_prompt: bool = True):
        from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
        from vllm.entrypoints.anthropic.serving import AnthropicServingMessages

        body = {"model": self.model_id, "messages": request["messages"],
                "max_tokens": int(request.get("max_tokens") or 1)}
        if request.get("system") is not None:
            body["system"] = request["system"]
        if request.get("tools"):
            body["tools"] = request["tools"]
        extra = request.get("extra_body") or {}
        if extra.get("chat_template_kwargs"):
            body["chat_template_kwargs"] = extra["chat_template_kwargs"]
        anthropic_request = AnthropicMessagesRequest(**body)
        # The server passes merge_inline_system=True whenever it runs without --chat-template.
        chat = AnthropicServingMessages.to_chat_completion_request(
            anthropic_request, merge_inline_system=True)
        chat.add_generation_prompt = add_generation_prompt
        return chat

    def render(self, request: dict, add_generation_prompt: bool = True) -> tuple[str, list[int]]:
        from vllm.entrypoints.chat_utils import parse_chat_messages
        from vllm.renderers.hf import resolve_chat_template_content_format, safe_apply_chat_template

        chat = self.chat_request(request, add_generation_prompt)
        tool_dicts = [tool.model_dump() for tool in chat.tools] if chat.tools else None
        # OnlineRenderer.preprocess_chat: server defaults first, then tools/tokenize, then the
        # request's own kwargs on top (ChatParams.with_defaults).
        defaults = dict(self.default_kwargs, tools=tool_dicts, tokenize=False)
        params = chat.build_chat_params(None, "auto").with_defaults(defaults)
        content_format = resolve_chat_template_content_format(
            chat_template=params.chat_template, tools=tool_dicts,
            given_format=params.chat_template_content_format, tokenizer=self.tokenizer,
            model_config=self.model_config)
        conversation, _, _ = parse_chat_messages(chat.messages, self.model_config,
                                                 content_format=content_format)
        prompt = safe_apply_chat_template(self.model_config, self.tokenizer, conversation,
                                          **params.get_apply_chat_template_kwargs())
        ids = self.tokenizer.encode(prompt, add_special_tokens=False)
        return prompt, ids

    def encode(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False)

    def decode(self, ids: list[int]) -> str:
        return self.tokenizer.decode(ids, skip_special_tokens=False)


def check(requests_path: Path, model: str, limit: int) -> dict:
    """Rendered prompt length against the server's reported input tokens, per captured call."""
    renderer = Renderer(MODELS[model])
    rows = open_jsonl(requests_path)
    checked = matched = 0
    mismatches = []
    for record in rows[: limit or None]:
        usage = (record.get("response") or {}).get("usage") or {}
        reported = usage.get("input_tokens")
        if reported is None:
            continue
        # vLLM's Anthropic usage splits prompt_tokens into input + cache_read + cache_creation
        # (vllm/entrypoints/anthropic/serving.py _build_anthropic_usage).
        reported = (int(reported) + int(usage.get("cache_read_input_tokens") or 0)
                    + int(usage.get("cache_creation_input_tokens") or 0))
        _, ids = renderer.render(record)
        checked += 1
        if len(ids) == reported:
            matched += 1
        else:
            mismatches.append({"call_index": record.get("call_index"), "purpose": record.get("purpose"),
                               "rendered": len(ids), "reported": reported})
    return {"requests": str(requests_path), "model": model, "checked": checked, "matched": matched,
            "mismatches": mismatches[:20]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="rendered length vs usage.input_tokens for a captured run")
    c.add_argument("--requests", required=True)
    c.add_argument("--model", default="qwen32b", choices=sorted(MODELS))
    c.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("show", help="print one rendered request (head and tail)")
    s.add_argument("--requests", required=True)
    s.add_argument("--index", type=int, default=0)
    s.add_argument("--model", default="qwen32b", choices=sorted(MODELS))
    args = ap.parse_args()
    if args.cmd == "check":
        print(json.dumps(check(Path(args.requests), args.model, args.limit), indent=2))
    else:
        record = open_jsonl(args.requests)[args.index]
        prompt, ids = Renderer(MODELS[args.model]).render(record)
        print(f"{len(ids)} tokens, {len(prompt)} chars\n{prompt[:1500]}\n.....\n{prompt[-1500:]}")


if __name__ == "__main__":
    main()
