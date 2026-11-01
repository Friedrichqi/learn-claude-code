#!/usr/bin/env python3
"""Shared pure logic of research/21_precautionary_work: the line texts, how a line is added to a request, and the
long-running-command classifier. The simulator, the compliance probe and the load replay all take these from here,
so the line the probe tests is byte for byte the line the replay sends.
"""
from __future__ import annotations

import copy
import re

# the front-load line (frozen by prereg_2a before the probe; the 10-29 proposal's wording)
LINE = ("Capacity is short and your next call will wait. Put any long-running commands that are ready (tests, "
        "builds, installs, downloads, searches) in this response.")
# the neutral line: the same position and about the same length, no instruction and no capacity claim
NEUTRAL = ("Session note: this message was logged by the harness as part of its routine bookkeeping for this run "
           "and carries no new information about the task or its files.")          # 31 tokens; LINE 33


def append_line(messages: list, text: str) -> list:
    """The newest user message gets one more text block, exactly as hold_runtime.append_status_line adds the status
    line (a new user message when the last one is not the user's). Returns a new list; the input is not changed."""
    msgs = copy.deepcopy(messages)
    block = {"type": "text", "text": text}
    if msgs and msgs[-1].get("role") == "user":
        content = msgs[-1]["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        msgs[-1]["content"] = list(content) + [block]
    else:
        msgs.append({"role": "user", "content": [block]})
    return msgs


def with_line(req: dict, text: str) -> dict:
    """A request (system, tools, messages) with the line appended."""
    out = dict(req)
    out["messages"] = append_line(req.get("messages") or [], text)
    return out


# -- long-running commands --------------------------------------------------------------------------------------------
# classes of shell commands that usually run for seconds to minutes; checked on the program and its first words
_LONG = [
    ("test", r"\b(pytest|py\.test|tox|nox|jest|vitest|mocha|karma|phpunit|rspec|ctest)\b"
             r"|\b(go|cargo|npm|yarn|pnpm|mvn|gradle|dotnet|mix|sbt|bazel|make)\s+(\S+\s+)*?test\b"
             r"|\bpython[\d.]*\s+(-m\s+)?(unittest|pytest)\b|\bruntests?\b|\bmake\b.*\b(check|test)\b"),
    ("build", r"\b(make|cmake|ninja|meson|scons|tsc|gcc|g\+\+|clang|javac|rustc|ocamlopt|ocamlc)\b"
              r"|\b(go|cargo|npm|yarn|pnpm|mvn|gradle|dotnet|sbt|bazel)\s+(\S+\s+)*?(build|compile|install|package)\b"
              r"|\bnpm\s+run\s+build\b|\bdocker\s+(build|compose)\b"),
    ("install", r"\b(pip3?|uv\s+pip|conda|mamba|poetry)\s+(\S+\s+)*?install\b|\bapt(-get)?\s+(\S+\s+)*?(install|update)\b"
                r"|\b(npm|yarn|pnpm)\s+(ci|install|add)\b|\bgo\s+(get|mod\s+download)\b|\bcargo\s+fetch\b"
                r"|\bgem\s+install\b|\bbrew\s+install\b"),
    ("download", r"\b(curl|wget|aria2c|scp|rsync)\b|\bgit\s+(clone|fetch|pull)\b|\bhuggingface-cli\s+download\b"),
    ("run", r"\bpython[\d.]*\s+(\S*/)?\S*(train|finetune|fine_tune|eval|evaluate|benchmark|bench|experiment)\S*\.py\b"
            r"|\b(caffe|torchrun|accelerate|deepspeed)\b|\bsleep\s+\d|\btimeout\s+\d"),
]
# an install or a download of a compiler is not a build: those classes are tried first
_ORDER = ("test", "install", "download", "build", "run")
_LONG_RE = sorted(((name, re.compile(pat)) for name, pat in _LONG), key=lambda x: _ORDER.index(x[0]))


def long_class(command: str) -> str | None:
    """The first long-running class a shell command belongs to, or None."""
    cmd = re.sub(r"\s+", " ", command or "")
    for name, rx in _LONG_RE:
        if rx.search(cmd):
            return name
    return None


def long_calls(calls: list[dict]) -> list[str]:
    """The long-running classes among a response's tool calls (bash only)."""
    out = []
    for c in calls or []:
        if c.get("name") == "bash":
            cls = long_class(str((c.get("input") or {}).get("command", "")))
            if cls:
                out.append(cls)
    return out
