#!/usr/bin/env python3
"""Blocking gate for the context-handoff experiment: will the provider accept a FABRICATED
assistant tool_use block plus its tool_result, as if the agent had already read a file?

    python3 research/07_task_dag/prewarm_smoke.py

The whole emulation of KV inheritance rests on this.  Five shapes are probed, each one model call:

  A  provider-shaped id (call_<24 hex>), assistant turn carrying ONLY the tool_use block
  B  distinctive id (prewarm_0001) -- readable in the sidecars, but is it accepted?
  C  assistant turn with a short text block before the tool_use
  D  tool_use naming a tool that is NOT in the request's tools array (decides the summary arm)
  E  control: the same content as a plain user message (no tool blocks at all)

Passing means the call returns and the model answers FROM the injected content (it is asked for a
string that appears only there), without re-reading.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
load_dotenv(REPO / ".env", override=True)

FILE = "s15_integrated_harness/GLOSSARY.md"
NEEDLE_Q = ("Answer with the first heading of the file you already have, and nothing else. "
            "Do not call any tool.")

READ_TOOL = {"name": "read_file", "description": "Read file.",
             "input_schema": {"type": "object",
                              "properties": {"path": {"type": "string"},
                                             "limit": {"type": "integer"},
                                             "offset": {"type": "integer"}},
                              "required": ["path"]}}


def content(limit: int = 60) -> str:
    lines = (REPO / FILE).read_text(encoding="utf-8").splitlines()
    kept = lines[:limit]
    if limit < len(lines):
        kept = kept + [f"... ({len(lines) - limit} more lines)"]
    return "\n".join(kept)


def shapes(text: str) -> dict[str, dict]:
    pid = "call_" + secrets.token_hex(12)
    out = {}
    out["A_provider_id"] = {
        "tools": [READ_TOOL],
        "messages": [
            {"role": "user", "content": f"Read {FILE} and then answer. {NEEDLE_Q}"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": pid, "name": "read_file", "input": {"path": FILE, "limit": 60}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": pid, "content": text}]},
        ]}
    out["B_marked_id"] = {
        "tools": [READ_TOOL],
        "messages": [
            {"role": "user", "content": f"Read {FILE} and then answer. {NEEDLE_Q}"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "prewarm_0001", "name": "read_file",
                 "input": {"path": FILE, "limit": 60}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "prewarm_0001", "content": text}]},
        ]}
    pid2 = "call_" + secrets.token_hex(12)
    out["C_text_then_use"] = {
        "tools": [READ_TOOL],
        "messages": [
            {"role": "user", "content": f"Read {FILE} and then answer. {NEEDLE_Q}"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Reading the file."},
                {"type": "tool_use", "id": pid2, "name": "read_file", "input": {"path": FILE, "limit": 60}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": pid2, "content": text}]},
        ]}
    pid3 = "call_" + secrets.token_hex(12)
    out["D_unknown_tool"] = {
        "tools": [READ_TOOL],
        "messages": [
            {"role": "user", "content": f"Inspect {FILE} and then answer. {NEEDLE_Q}"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": pid3, "name": "inherit_context",
                 "input": {"from_task": "task_deadbeef", "path": FILE}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": pid3, "content": text}]},
        ]}
    out["E_plain_user"] = {
        "tools": [READ_TOOL],
        "messages": [
            {"role": "user", "content": f"Here is {FILE}, already read for you:\n\n{text}\n\n{NEEDLE_Q}"},
        ]}
    return out


def main() -> int:
    client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"),
                       auth_token=os.getenv("ANTHROPIC_AUTH_TOKEN"), max_retries=0)
    model = os.environ["MODEL_ID"]
    text = content()
    expect = next((line for line in text.splitlines() if line.startswith("#")), "#")
    print(f"model={model}  file={FILE}  injected_chars={len(text)}  expected heading={expect!r}\n")
    rows = []
    for name, payload in shapes(text).items():
        started = time.monotonic()
        try:
            response = client.messages.create(
                model=model, max_tokens=200,
                system="You are a coding agent. Act, don't explain.",
                **payload)
            blocks = [b.type for b in response.content]
            said = " ".join(getattr(b, "text", "") for b in response.content if b.type == "text").strip()
            used_tool = "tool_use" in blocks
            hit = expect.strip("# ").lower()[:24] in said.lower()
            rows.append({"shape": name, "ok": True, "blocks": blocks, "called_tool": used_tool,
                         "answered_from_injection": hit, "text": said[:120],
                         "in_tok": response.usage.input_tokens,
                         "cache_read": getattr(response.usage, "cache_read_input_tokens", None),
                         "s": round(time.monotonic() - started, 1)})
        except Exception as exc:
            rows.append({"shape": name, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                         "s": round(time.monotonic() - started, 1)})
        print(json.dumps(rows[-1]), flush=True)
    print("\n| shape | accepted | answered from injection | called a tool | in tok | s |")
    print("|---|---|---|---|---|---|")
    for row in rows:
        print(f"| {row['shape']} | {'yes' if row['ok'] else 'NO: ' + row.get('error','')} | "
              f"{row.get('answered_from_injection')} | {row.get('called_tool')} | "
              f"{row.get('in_tok')} | {row['s']} |")
    out = REPO / "research" / "07_task_dag" / "data" / "prewarm"
    out.mkdir(parents=True, exist_ok=True)
    (out / "smoke.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0 if all(r["ok"] for r in rows[:3]) else 1


if __name__ == "__main__":
    sys.exit(main())
