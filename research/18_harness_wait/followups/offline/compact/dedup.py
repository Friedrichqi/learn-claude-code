#!/usr/bin/env python3
"""C4 (FOLLOWUPS.md): how much of the tool-result text added to the lead's context repeats a read_file result
that is still in the prompt in full, for an unchanged file (no write_file / edit_file on that path in between).

Each lead call's newest user message carries the results just added. A read_file result there is a duplicate when
the same (path, offset, limit) was read earlier in the same prompt, that earlier result is still in full (not a
micro_compact placeholder or a persisted-output preview), and no write or edit touched the path in between.

    python research/18_harness_wait/followups/offline/compact/dedup.py w16_sgate w08_none
"""
import glob
import json
import lzma
import sys
from collections import Counter

ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"


def full(text) -> bool:
    return isinstance(text, str) and not text.startswith("[Earlier tool result saved at") \
        and not text.startswith("<persisted-output>")


def path_of(inp) -> str:
    inp = inp or {}
    return str(inp.get("path") or inp.get("file_path"))


for cell in sys.argv[1:]:
    c = Counter()
    for cap in sorted(glob.glob(f"{ROOT}/{cell}/runs/*/*.requests.jsonl.xz")):
        for line in lzma.open(cap, "rt"):
            x = json.loads(line)
            msgs = x.get("messages")
            if x.get("purpose") != "lead" or not isinstance(msgs, list) or not msgs:
                continue
            last = msgs[-1]
            if last.get("role") != "user" or not isinstance(last.get("content"), list):
                continue
            uses = {}                                      # tool_use id -> (name, input)
            seen = {}                                      # (path, offset, limit) -> True while a full result stands
            for m in msgs[:-1]:
                cont = m.get("content")
                if not isinstance(cont, list):
                    continue
                for b in cont:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        uses[b.get("id")] = (b.get("name"), b.get("input") or {})
                        if b.get("name") in ("write_file", "edit_file"):
                            p = path_of(b.get("input"))
                            for k in [k for k in seen if k[0] == p]:
                                del seen[k]                # the file changed: earlier reads are stale
                    elif b.get("type") == "tool_result":
                        name, inp = uses.get(b.get("tool_use_id"), (None, {}))
                        if name == "read_file" and full(b.get("content")):
                            seen[(path_of(inp), inp.get("offset"), inp.get("limit"))] = True
            for m in msgs:                                 # the uses that produced the newest results
                if isinstance(m.get("content"), list):
                    for b in m["content"]:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            uses[b.get("id")] = (b.get("name"), b.get("input") or {})
            for b in last["content"]:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                n = len(str(b.get("content", "")))
                c["result_chars"] += n
                name, inp = uses.get(b.get("tool_use_id"), (None, {}))
                if name == "read_file":
                    c["read_chars"] += n
                    c["reads"] += 1
                    if (path_of(inp), inp.get("offset"), inp.get("limit")) in seen:
                        c["dup_chars"] += n
                        c["dups"] += 1
    rc = c["result_chars"] or 1
    print(f"{cell}: tool-result text added {c['result_chars'] / 1e6:.1f}M chars, read_file {100 * c['read_chars'] / rc:.0f} % of it; "
          f"duplicate reads of unchanged, still-visible results: {c['dups']} of {c['reads']} reads, "
          f"{c['dup_chars'] / 1e6:.1f}M chars = {100 * c['dup_chars'] / rc:.1f} % of all tool-result text")
