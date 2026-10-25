#!/usr/bin/env python3
"""How often the lead re-reads a file whose earlier read had been cleared to a placeholder (micro_compact) by the
time of the re-read, against re-reads of results still in full. Lead calls only; from the request captures.

    python research/18_harness_wait/followups/offline/compact/rereads.py w16_sgate w08_none
"""
import glob, json, lzma, sys
from collections import Counter

ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"


EXACT = True                                            # the same path, offset and limit; False: the same path


def key(inp):
    inp = inp or {}
    path = str(inp.get("path") or inp.get("file_path"))
    return (path, inp.get("offset"), inp.get("limit")) if EXACT else path


def is_preview(t):
    return isinstance(t, str) and t.startswith("<persisted-output>")  # a large output (> 30k chars) shown as a 2k preview


def is_ph(t):
    return isinstance(t, str) and t.startswith("[Earlier tool result saved at")


if "--path-only" in sys.argv:
    EXACT = False
for cell in [a for a in sys.argv[1:] if not a.startswith("--")]:
    c = Counter()
    for cap in sorted(glob.glob(f"{ROOT}/{cell}/runs/*/*.requests.jsonl.xz")):
        lead = [json.loads(l) for l in lzma.open(cap, "rt")]
        lead = [x for x in lead if x.get("purpose") == "lead" and isinstance(x.get("messages"), list)]
        for x in lead:
            c["calls"] += 1
            resp = x.get("response") or {}
            uses = [b for b in (resp.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
            reads = [u for u in uses if u.get("name") == "read_file"]
            if not reads:
                continue
            # what this call's prompt shows: earlier read_file results by path, cleared or in full
            seen = {}
            ids = {}
            for m in x["messages"]:
                cont = m.get("content")
                if not isinstance(cont, list):
                    continue
                for b in cont:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("name") == "read_file":
                        ids[b.get("id")] = key(b.get("input"))
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in ids:
                        path = ids[b["tool_use_id"]]
                        txt = b.get("content")
                        state = "cleared" if is_ph(txt) else ("preview" if is_preview(txt) else "full")
                        if seen.get(path) != "full":
                            seen[path] = state
            for u in reads:
                c["reads"] += 1
                k = key(u.get("input"))
                if k in seen:
                    c["reread_" + seen[k]] += 1
    r = c["reads"] or 1
    print(f"{cell}: lead calls {c['calls']}, read_file calls {c['reads']} ({100 * c['reads'] / max(1, c['calls']):.0f} per 100 calls); "
          f"re-reads of a file shown only as a cleared placeholder {c['reread_cleared']} ({100 * c['reread_cleared'] / r:.1f} % of reads), "
          f"re-reads of a file still in full {c['reread_full']} ({100 * c['reread_full'] / r:.1f} %), "
          f"of a large output shown as a preview {c['reread_preview']} ({100 * c['reread_preview'] / r:.1f} %)")
