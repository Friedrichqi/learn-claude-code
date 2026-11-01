#!/usr/bin/env python3
"""CPU check of the MiniMax-M2.7 chat template topic 19 serves (minimax_m2_append_only.jinja, this folder):
each lead request must render as a prefix of the next, so vLLM's prefix cache can reuse a session's history.

It runs vLLM 0.31's own server path without a GPU or the weights:
  1. the Anthropic /v1/messages request goes through AnthropicServingMessages.to_chat_completion_request;
  2. parse_chat_messages parses it with the content format vLLM detects for the template;
  3. safe_apply_chat_template renders it with the model's tokenizer.

Inputs:
  * recorded lead requests of topic 16's MiniMax-M2.7 baseline (research/16_epic_harness/harness_eval/jobs/
    minimax-m2.7: single-task SWE / Pro / Terminal-Bench runs with real reasoning, tool calls and results).
    These are consecutive pairs in which the harness itself did not rewrite the history;
  * synthetic 4-turn sessions in the harness's own message shapes, one per topic-19 arm:
    - final answers followed by a new turn;
    - a [server] line or a [cost] receipt appended to the newest user message (a turn prompt or tool results);
    - several tool calls in one response, wait-only rounds, background notes and todo reminders.

Checks, for every pair (request k, request k+1):
  * text: render(k, with the generation prompt) is a prefix of render(k+1) (append-only);
  * tokens: the tokens of render(k) are a prefix of the tokens of render(k+1) (no BPE merge across the seam);
  * separation: an appended [server] / [cost] line starts on a line of its own (the one change from topic 16's
    template; vLLM passes this template content lists, and its text parts used to run together).

The vendor template is rendered too, for comparison only.

    CUDA_VISIBLE_DEVICES= /data/fritz/venv/bin/python research/19_efficiency_signals/test_minimax_template.py
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR))
from ef_render import SNAPSHOT, render, tokens  # noqa: E402

TOPIC16 = REPO / "research" / "16_epic_harness" / "harness_eval" / "jobs" / "minimax-m2.7"
RUNS_PER_BENCH = 4                       # recorded runs per benchmark (first in sorted order)


def check_pair(a: dict, b: dict, template: str | None = None) -> tuple[bool, bool]:
    """(text prefix, token prefix) for consecutive requests a, b."""
    ra, rb = render(a, True, template), render(b, True, template)
    if not rb.startswith(ra):
        return False, False
    ta, tb = tokens(ra), tokens(rb)
    return True, tb[:len(ta)] == ta


# -- recorded pairs (topic 16) -----------------------------------------------------------------------------
def recorded_pairs() -> list[tuple[dict, dict]]:
    pairs = []
    for bench in ("swe", "swepro", "tbench"):
        for path in sorted(glob.glob(str(TOPIC16 / bench / "*" / "*" / "agent" / "s15" / "calls.jsonl")))[:RUNS_PER_BENCH]:
            lead = [json.loads(line) for line in open(path)]
            lead = [r for r in lead if r.get("purpose") == "lead" and r.get("agent_kind") == "lead"]
            for x, y in zip(lead, lead[1:]):
                ma, mb = x["request"]["messages"], y["request"]["messages"]
                if mb[:len(ma)] == ma and len(mb) > len(ma):       # the harness appended, did not rewrite
                    pairs.append((x["request"], y["request"]))
    return pairs


# -- synthetic sessions in the harness's shapes (topic 19's arms) -------------------------------------------
SERVER = ("[server] KV pool 258,192 tokens; sessions ahead of you hold 231,040; your context 24,512, 18,304 still "
          "cached; 5 requests waiting")
COST = ("[cost] your last step: 24,512 prompt tokens, 18,304 reused from the server's cache, 4,806 recomputed "
        "because they had been evicted, 1,402 new or changed; queued 4.1 s; 412 tokens generated; 14.2 s in all")
TOOLS = [{"name": n, "description": f"The {n} tool.",
          "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}}}
         for n in ("bash", "read_file", "glob", "todo_write", "wait", "wait_for_capacity", "server_status")]


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def call(i: int, name: str, args: dict) -> dict:
    return {"type": "tool_use", "id": f"toolu_{i:05d}", "name": name, "input": args}


def result(i: int, out: str) -> dict:
    return {"type": "tool_result", "tool_use_id": f"toolu_{i:05d}", "content": out}


def synthetic_session(line: str | None, receipt: bool) -> list[dict]:
    """The lead requests of one 3-turn session as the harness sends them (each with the line(s) appended)."""
    system = "You are a coding agent. Act, don't explain.\n\nShared server: you share one model server ..."
    turns = [
        # (prompt, rounds): a round = (assistant reply blocks, tool results, extra user text blocks)
        ("[run 1a2b] Explain how the CLI parses its options.", [
            ([text("<think>I should list the package first."), call(1, "glob", {"x": "src/**/*.py"})],
             [result(1, "src/cli.py\nsrc/core.py")], []),
            ([text("<think>Two files matter.\n\n\nI will read both."), call(2, "read_file", {"x": "src/cli.py"}),
              call(3, "read_file", {"x": "src/core.py"}), call(4, "wait", {"x": "20"})],
             [result(2, "import argparse\n..." * 40), result(3, "def run():\n    pass\n" * 30),
              result(4, "Waited 20 s.\n" + SERVER)], ["[background] task b1 finished: ok"]),
            ([text("<think>The server is crowded; I have nothing else to do."),
              call(5, "wait_for_capacity", {"x": "300"})],
             [result(5, "Waited 47 s: there is room for your context now.\n" + SERVER)], []),
            ([text("<think>Done reading. The parser is argparse.\n</think>\n\nThe CLI builds an argparse "
                   "parser in `src/cli.py` and dispatches to `run()`.")], None, []),
        ]),
        ("Now add a --verbose flag.", [
            ([text("<think>Edit cli.py."), call(6, "bash", {"x": "grep -n add_argument src/cli.py"}),
              call(7, "server_status", {})], [result(6, "12: parser.add_argument('--name')"),
                                               result(7, SERVER)], []),
            ([text("<think>Add it after line 12.\n\n\nThen check the help text."),
              call(8, "bash", {"x": "sed -i ... src/cli.py"})], [result(8, "")], []),
            ([text("<think>"), call(10, "bash", {"x": "python -m src.cli --help"})],   # reasoning closed at once
             [result(10, "usage: cli [--name NAME] [--verbose]")], []),
            ([text("<think>Done.\n</think>\n\nAdded `--verbose` (store_true) after `--name`.")], None, []),
        ]),
        ("Thanks. Run the tests.", [
            ([text("<think>Run pytest."), call(9, "bash", {"x": "pytest -q"})],
             [result(9, "3 passed")], ["<reminder>Update your todos.</reminder>"]),
            ([text("<think>All good.\n</think>\n\nAll 3 tests pass.")], None, []),
        ]),
    ]
    requests, messages, n_calls = [], [], 0

    def append_lines():
        lines = []
        if line:
            lines.append(line)
        if receipt and n_calls:
            lines.append(COST)
        if not lines:
            return
        last = messages[-1]
        content = last["content"] if isinstance(last["content"], list) else [text(last["content"])]
        last["content"] = content + [text("\n".join(lines))]

    for prompt, rounds in turns:
        messages.append({"role": "user", "content": prompt})
        for reply, results, extra in rounds:
            append_lines()
            requests.append({"system": system, "tools": TOOLS, "messages": json.loads(json.dumps(messages))})
            n_calls += 1
            messages.append({"role": "assistant", "content": reply})
            if results is None:
                break                                   # a final answer ends the turn
            messages.append({"role": "user", "content": list(results) + [text(t) for t in extra if
                                                                          not t.startswith("<reminder>")]})
            for t in extra:                             # the todo reminder is a separate user message
                if t.startswith("<reminder>"):
                    messages.append({"role": "user", "content": t})
    return requests


# -- tests ----------------------------------------------------------------------------------------------------
def test_recorded_pairs_are_append_only():
    pairs = recorded_pairs()
    assert len(pairs) >= 100, len(pairs)
    bad_text = bad_tok = 0
    for a, b in pairs:
        ok_text, ok_tok = check_pair(a, b)
        bad_text += not ok_text
        bad_tok += ok_text and not ok_tok
    print(f"  recorded: {len(pairs)} pairs, text breaks {bad_text}, token breaks {bad_tok}")
    assert bad_text == 0 and bad_tok == 0, (bad_text, bad_tok)


def test_synthetic_sessions_are_append_only_across_turns():
    for arm, line, receipt in (("none", None, False), ("m_wait", SERVER, False), ("m_tools", None, True)):
        requests = synthetic_session(line, receipt)
        for k, (a, b) in enumerate(zip(requests, requests[1:])):
            ok_text, ok_tok = check_pair(a, b)
            assert ok_text and ok_tok, (arm, k, ok_text, ok_tok)
        print(f"  {arm}: {len(requests)} requests, {len(requests) - 1} pairs append-only (3 turns)")


def test_appended_lines_start_on_their_own_line():
    for line, receipt, marker in ((SERVER, False, "[server]"), (None, True, "[cost]")):
        rendered = render(synthetic_session(line, receipt)[-1], True)
        k, seen = rendered.find(marker), 0
        while k >= 0:
            # after a newline (own line, or a user message's header) or a tool result's <response>
            assert rendered[k - 1] in ("\n", ">"), (marker, rendered[max(0, k - 60):k + 20])
            seen += 1
            k = rendered.find(marker, k + 1)
        assert seen >= 3, (marker, seen)
    last = synthetic_session(None, True)[-1]
    raw16 = (REPO / "research" / "16_epic_harness" / "harness_eval" / "epic" / "templates" /
             "minimax_m2_append_only.jinja").read_text()
    assert "Run the tests.[cost]" in render(last, True, raw16), "topic 16's template ran the parts together"
    assert "Run the tests.\n[cost]" in render(last, True)


def test_vendor_template_for_comparison():
    """Not a gate: how often the vendor template (reasoning dropped before the last user message) breaks."""
    vendor = (SNAPSHOT / "chat_template.jinja").read_text()
    requests = synthetic_session(None, True)
    breaks = sum(not check_pair(a, b, vendor)[0] for a, b in zip(requests, requests[1:]))
    print(f"  vendor template: {breaks} of {len(requests) - 1} synthetic pairs break the prefix")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
