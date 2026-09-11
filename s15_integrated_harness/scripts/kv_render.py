#!/usr/bin/env python3
"""Render a dumped s15 lead request (Anthropic Messages format) into Qwen2.5 chat-template segments.

Each segment is tokenized on its own so a compaction edit changes exactly one segment and the
splice can diff at segment granularity.  The text follows Qwen2.5-Instruct's chat template
(Hermes-style tool calls) so a local Qwen model sees a well-formed agent transcript.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

IM_START, IM_END = "<|im_start|>", "<|im_end|>"
GEN_PROMPT = f"{IM_START}assistant\n"
TOOLS_HEAD = ("\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
              "You are provided with function signatures within <tools></tools> XML tags:\n<tools>")
TOOLS_TAIL = ("\n</tools>\n\nFor each function call, return a json object with function name and arguments "
              "within <tool_call></tool_call> XML tags:\n<tool_call>\n{\"name\": <function-name>, "
              "\"arguments\": <args-json-object>}\n</tool_call>" + IM_END + "\n")


def tojson(x) -> str:
    return json.dumps(x, ensure_ascii=False)


@dataclass
class Segment:
    text: str
    kind: str                       # system | user | tool_result | assistant | gen | probe | response
    meta: dict = field(default_factory=dict)
    ids: list[int] | None = None


def anthropic_tool_to_openai(tool: dict) -> dict:
    return {"type": "function", "function": {"name": tool.get("name"), "description": tool.get("description", ""),
                                             "parameters": tool.get("input_schema", {"type": "object", "properties": {}})}}


def render_system(system: str, tools: list[dict]) -> list[Segment]:
    """System prompt as several segments: one per blank-line section and one per tool schema, so a
    change to one section (or the tool list) leaves the other segments reusable."""
    segs: list[Segment] = []
    sections = system.split("\n\n")
    for i, sec in enumerate(sections):
        text = (f"{IM_START}system\n" if i == 0 else "") + sec + ("\n\n" if i < len(sections) - 1 else "")
        segs.append(Segment(text, "system", {"section": sec[:40]}))
    if tools:
        segs.append(Segment(TOOLS_HEAD, "system"))
        for tool in tools:
            segs.append(Segment("\n" + tojson(anthropic_tool_to_openai(tool)), "system_tool", {"tool": tool.get("name")}))
        segs.append(Segment(TOOLS_TAIL, "system"))
    else:
        segs.append(Segment(IM_END + "\n", "system"))
    return segs


def render_assistant(blocks) -> Segment:
    """One assistant message: text + tool calls (thinking dropped)."""
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    calls = [b for b in blocks if b.get("type") == "tool_use"]
    out = f"{IM_START}assistant"
    if text:
        out += "\n" + text
    for call in calls:
        out += "\n<tool_call>\n{\"name\": \"" + str(call.get("name")) + "\", \"arguments\": " + tojson(call.get("input") or {}) + "}\n</tool_call>"
    out += IM_END + "\n"
    return Segment(out, "assistant", {"tool_use_ids": [c.get("id") for c in calls],
                                       "tools": [c.get("name") for c in calls], "has_text": bool(text)})


def render_messages(messages: list[dict], tool_index: dict | None = None) -> list[Segment]:
    """Body segments for a message list. tool_index maps tool_use_id -> (tool, input) for result labelling."""
    tool_index = dict(tool_index or {})
    for m in messages:
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    tool_index[b.get("id")] = (b.get("name"), b.get("input"))
    segs: list[Segment] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "assistant":
            segs.append(render_assistant(content))
            continue
        if isinstance(content, str):
            segs.append(Segment(f"{IM_START}user\n{content}{IM_END}\n", "user", {"text": content[:80]}))
            continue
        results = [b for b in content if b.get("type") == "tool_result"]
        texts = [b for b in content if b.get("type") == "text"]
        if results:
            segs.append(Segment(f"{IM_START}user", "user_head"))
            for b in results:
                body = b.get("content", "")
                if not isinstance(body, str):
                    body = tojson(body)
                tool, tool_input = tool_index.get(b.get("tool_use_id"), (None, None))
                placeholder = body.startswith("[Earlier tool result saved at") or body.startswith("<persisted-output>")
                segs.append(Segment(f"\n<tool_response>\n{body}\n</tool_response>", "tool_result",
                                    {"tool_use_id": b.get("tool_use_id"), "tool": tool, "input": tool_input,
                                     "placeholder": placeholder, "chars": len(body)}))
            for b in texts:
                segs.append(Segment("\n" + b.get("text", ""), "user_text"))
            segs.append(Segment(IM_END + "\n", "user_tail"))
        else:
            text = "\n".join(b.get("text", "") for b in texts)
            segs.append(Segment(f"{IM_START}user\n{text}{IM_END}\n", "user", {"text": text[:80]}))
    return segs


def render_request(req: dict) -> list[Segment]:
    return render_system(req["system"], req.get("tools") or []) + render_messages(req["messages"])


def tokenize_segments(tok, segs: list[Segment]) -> list[Segment]:
    for s in segs:
        if s.ids is None:
            s.ids = tok(s.text, add_special_tokens=False).input_ids
    return segs


def flatten(segs: list[Segment]) -> list[int]:
    out = []
    for s in segs:
        out.extend(s.ids)
    return out


def check_against_template(tok) -> None:
    """The renderer must equal tokenizer.apply_chat_template on an OpenAI-format transcript."""
    tools = [{"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
    system = "You are a coding agent."
    messages = [
        {"role": "user", "content": "Read a.txt and b.txt"},
        {"role": "assistant", "content": [{"type": "text", "text": "Reading."},
                                          {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.txt"}},
                                          {"type": "tool_use", "id": "c2", "name": "read_file", "input": {"path": "b.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "alpha\nbeta"},
                                     {"type": "tool_result", "tool_use_id": "c2", "content": "gamma"}]},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"},
                                          {"type": "tool_use", "id": "c3", "name": "read_file", "input": {"path": "c.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c3", "content": "[Earlier tool result saved at /x/y.txt]"}]},
    ]
    mine = "".join(s.text for s in render_request({"system": system, "tools": tools, "messages": messages})) + GEN_PROMPT
    oa_msgs = [{"role": "system", "content": system}, {"role": "user", "content": "Read a.txt and b.txt"},
               {"role": "assistant", "content": "Reading.", "tool_calls": [
                   {"type": "function", "function": {"name": "read_file", "arguments": {"path": "a.txt"}}},
                   {"type": "function", "function": {"name": "read_file", "arguments": {"path": "b.txt"}}}]},
               {"role": "tool", "content": "alpha\nbeta"}, {"role": "tool", "content": "gamma"},
               {"role": "assistant", "content": "", "tool_calls": [
                   {"type": "function", "function": {"name": "read_file", "arguments": {"path": "c.txt"}}}]},
               {"role": "tool", "content": "[Earlier tool result saved at /x/y.txt]"}]
    ref = tok.apply_chat_template(oa_msgs, tools=[anthropic_tool_to_openai(t) for t in tools], tokenize=False, add_generation_prompt=True)
    if mine != ref:
        import difflib
        for line in difflib.unified_diff(ref.splitlines(), mine.splitlines(), "template", "renderer", lineterm=""):
            print(line)
        raise AssertionError("renderer differs from chat template")
    seg_ids = flatten(tokenize_segments(tok, render_request({"system": system, "tools": tools, "messages": messages})))
    whole_ids = tok(mine[: -len(GEN_PROMPT)], add_special_tokens=False).input_ids
    print(f"renderer == chat template (text). segment-wise tokens={len(seg_ids)} whole-string tokens={len(whole_ids)}")


if __name__ == "__main__":
    from transformers import AutoTokenizer
    import sys
    tok = AutoTokenizer.from_pretrained(sys.argv[1] if len(sys.argv) > 1 else "Qwen/Qwen2.5-1.5B-Instruct")
    check_against_template(tok)
