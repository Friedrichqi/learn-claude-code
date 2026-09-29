#!/usr/bin/env python3
"""Find compaction events in the captured live runs and build their probe tails (offline, CPU).

An event is a pair of consecutive lead main-loop calls (k-1, k) of one run where the request of
call k is NOT an append-only extension of call k-1's request plus the assistant turn it produced:
the harness's prepare_context edited earlier history in between. Its edit kinds come from the
harness's own ANCHORED matchers (the marker strings also occur inside files the agent reads, e.g.
s08_context_compact/README.md, so substring search would misfire):

    placeholder  tool_result content == "[Earlier tool result saved at <path>]"        (micro_compact)
    snip         user message fullmatches r"\\[\\d+ messages archived at (.+)\\]"     (snip_compact)
    preview      tool_result content startswith "<persisted-output>\\n"   (tool_result_budget / fit)
    summary      first message startswith "[Compacted]\\n\\nAuthoritative request:\\n"  (compact_history)
    system       the system prompt changed;   tools   the tool list changed

Per run at most two events are sampled for the arms (seeded by the run label): one with a
placeholder edit and one with a snip edit when both exist, else any two; summary-only events are
listed but not sampled (after a summary nothing survives to be reused). Each sampled event gets:

  natural    the harness's real call-k request (the round that follows the compaction)
  probe      one gold-scored question appended as a user text block after the tool results, about
             a read_file result of a .py file that SURVIVES the edit and sits AFTER the first edited
             message -- the content whose KV a RoPE-shift server reuses at shifted positions:
               explain    purpose + every function/class the code defines        (names in the text)
               modify     one SEARCH/REPLACE block renaming a function           (Aider format)
               tool_call  an edit_file call renaming a function on its def line  (BFCL AST)
  retention  (when available) a question about a read evicted by THIS step's edit, whose answer
             appears nowhere in the call-k request: does stale reused KV leak evicted content?

Token ids are not stored: arms.py re-renders old/new per model from the captured requests.

    python research/10_rope_shift/events.py extract [--shard S --shards N] [--labels a,b]
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

from render import open_jsonl  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "rope_shift_live"
PLACEHOLDER = "[Earlier tool result saved at "
PREVIEW = "<persisted-output>\n"
ARCHIVE_RE = re.compile(r"\[\d+ messages archived at (.+)\]")
SUMMARY_HEADS = ("[Compacted]\n\nAuthoritative request:\n", "[Reactive compact]\n\nAuthoritative request:\n")
DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)[ \t]*\(", re.M)
CLASS_RE = re.compile(r"^[ \t]*class[ \t]+([A-Za-z_]\w*)", re.M)
HEAD_RE = re.compile(r"^#{1,3}[ \t]+(.+?)[ \t]*$", re.M)
TOP_RE = re.compile(r"^(?:(?:async[ \t]+)?def|class)[ \t]+([A-Za-z_]\w*)", re.M)     # column 0 only


# -- message helpers --------------------------------------------------------------------------------

def assistant_turn(record: dict) -> dict:
    return {"role": "assistant", "content": copy.deepcopy(record["response"]["content"])}


def history_after(record: dict) -> list:
    """What the server's cache covers after the call: the request's messages + its reply."""
    return list(record["messages"]) + [assistant_turn(record)]


def fingerprint(message) -> str:
    return hashlib.sha1(json.dumps(message, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tool_results(messages: list):
    for mi, msg in enumerate(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    yield mi, bi, block


def result_text(block: dict) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    return str(content)


def is_placeholder(text: str) -> bool:
    return text.startswith(PLACEHOLDER) and text.endswith("]")


def is_preview(text: str) -> bool:
    return text.startswith(PREVIEW)


def is_archive(msg: dict) -> bool:
    return msg.get("role") == "user" and isinstance(msg.get("content"), str) and \
        bool(ARCHIVE_RE.fullmatch(msg["content"]))


def is_summary(msg: dict) -> bool:
    content = msg.get("content")
    return msg.get("role") == "user" and isinstance(content, str) and content.startswith(SUMMARY_HEADS)


def tool_uses(messages: list) -> dict:
    """tool_use id -> (name, input) over assistant turns."""
    out = {}
    for msg in messages:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    out[block.get("id")] = (block.get("name"), block.get("input") or {})
    return out


def marker_counts(messages: list) -> dict:
    counts = Counter()
    for _, _, block in tool_results(messages):
        text = result_text(block)
        counts["placeholders"] += is_placeholder(text)
        counts["previews"] += is_preview(text)
    counts["archives"] = sum(is_archive(m) for m in messages)
    counts["summaries"] = sum(is_summary(m) for m in messages)
    return dict(counts)


# -- events -----------------------------------------------------------------------------------------

def lead_calls(records: list[dict]) -> list[dict]:
    calls = [r for r in records if r.get("purpose") == "lead" and "response" in r and not r.get("error")]
    return sorted(calls, key=lambda r: r["call_index"])


def classify(prev: dict, cur: dict) -> dict | None:
    old_msgs = history_after(prev)
    new_msgs = cur["messages"]
    same_system = prev.get("system") == cur.get("system")
    same_tools = prev.get("tools") == cur.get("tools")
    if same_system and same_tools and new_msgs[:len(old_msgs)] == old_msgs:
        return None                                    # append-only: plain prefix reuse
    old_fp = [fingerprint(m) for m in old_msgs]
    new_fp = [fingerprint(m) for m in new_msgs]
    ops = difflib.SequenceMatcher(None, old_fp, new_fp, autojunk=False).get_opcodes()
    first_edit = next((j1 for tag, i1, i2, j1, j2 in ops if tag != "equal" and i1 < len(old_msgs)),
                      len(new_msgs))
    if not (same_system and same_tools):
        first_edit = 0          # the system prompt / tool list precede every message
    before, after = marker_counts(old_msgs), marker_counts(new_msgs)
    kinds = []
    if after.get("placeholders", 0) > before.get("placeholders", 0):
        kinds.append("placeholder")
    new_archives = [m["content"] for m in new_msgs if is_archive(m)]
    old_archives = [m["content"] for m in old_msgs if is_archive(m)]
    if new_archives and new_archives != old_archives:
        kinds.append("snip")
    if after.get("previews", 0) > before.get("previews", 0):
        kinds.append("preview")
    if new_msgs and is_summary(new_msgs[0]) and not (old_msgs and is_summary(old_msgs[0]) and
                                                        new_msgs[0] == old_msgs[0]):
        kinds.append("summary")
    if not same_system:
        kinds.append("system")
    if not same_tools:
        kinds.append("tools")
    if not kinds:
        kinds.append("other")
    # where the newly appended part of call k starts: just after call k-1's reply
    reply = assistant_turn(prev)
    appended_from = next((j + 1 for j in range(len(new_msgs) - 1, -1, -1) if new_msgs[j] == reply),
                         len(new_msgs) - 1)
    return {"kinds": kinds, "first_edit_msg": first_edit, "appended_from": appended_from,
            "n_msgs_old": len(old_msgs), "n_msgs_new": len(new_msgs),
            "markers_old": before, "markers_new": after,
            "chars_old": len(json.dumps(old_msgs, default=str)),
            "chars_new": len(json.dumps(new_msgs, default=str))}


def oracle_messages(prev: dict, cur: dict, appended_from: int) -> list:
    """Call k's request with this step's compaction undone: k-1's history + the new messages."""
    return history_after(prev) + copy.deepcopy(cur["messages"][appended_from:])


# -- probes -----------------------------------------------------------------------------------------

def normpath(path: str) -> str:
    return re.sub(r"^(\./)+", "", str(path).strip()).rstrip("/")


def survivors(cur: dict, first_edit: int, appended_from: int) -> list[dict]:
    """read_file results of .py files still intact in call k's request, located after the first
    edited message and before the newly appended results (so every arm but recompute reuses them)."""
    uses = tool_uses(cur["messages"])
    out = []
    for mi, bi, block in tool_results(cur["messages"]):
        if not (first_edit <= mi < appended_from):
            continue
        name, args = uses.get(block.get("tool_use_id"), (None, {}))
        text = result_text(block)
        path = normpath(args.get("path", ""))
        if name != "read_file" or not path.endswith(".py") or is_placeholder(text) or is_preview(text):
            continue
        if text.startswith("Error:") or len(text) < 400:
            continue
        defs = list(dict.fromkeys(DEF_RE.findall(text)))
        if len(defs) < 2:
            continue
        out.append({"msg": mi, "block": bi, "path": path, "text": text, "defs": defs,
                    "classes": list(dict.fromkeys(CLASS_RE.findall(text)))})
    return out


def def_line(text: str, name: str) -> str | None:
    match = re.search(rf"^[ \t]*(?:async[ \t]+)?def[ \t]+{re.escape(name)}[ \t]*\(.*$", text, re.M)
    return match.group(0) if match else None


def pick_rename(surv: dict, rng: random.Random) -> tuple[str, str] | None:
    names = [n for n in surv["defs"] if not n.startswith("__")
             and 1 <= len(re.findall(rf"\b{re.escape(n)}\b", surv["text"])) <= 6
             and def_line(surv["text"], n) and surv["text"].count(def_line(surv["text"], n)) == 1]
    if not names:
        return None
    name = rng.choice(sorted(names))
    return name, def_line(surv["text"], name)


def gold_probe(kind: str, surv: dict, rng: random.Random) -> dict | None:
    path = surv["path"]
    if kind == "explain":
        top = list(dict.fromkeys(TOP_RE.findall(surv["text"])))
        if len(top) < 2:
            return None
        text = (f"Without calling any tool, answer from the earlier read_file output of `{path}` that is "
                "still in your context: (1) one sentence on what this code is for; (2) the names of "
                "every top-level function and class it defines (not methods), as one comma-separated list.")
        return {"family": "explain", "text": text, "path": path, "gold": {"names": top}}
    picked = pick_rename(surv, rng)
    if picked is None:
        return None
    name, line = picked
    new_name = f"{name}_v2"
    renamed = re.sub(rf"\b{re.escape(name)}\b", new_name, line, count=1)
    if kind == "modify":
        text = (f"Without calling any tool, rename the function `{name}` to `{new_name}` in the code of "
                f"`{path}` that you read earlier (its def line and every reference inside that code). "
                "Reply with exactly one SEARCH/REPLACE block:\n<<<<<<< SEARCH\n(lines copied verbatim "
                "from that file)\n=======\n(the same lines with the rename applied)\n>>>>>>> REPLACE")
        return {"family": "modify", "text": text, "path": path,
                "gold": {"old_name": name, "new_name": new_name, "def_line": line}}
    if kind == "tool_call":
        text = (f"Use the edit_file tool to rename the function `{name}` defined in `{path}` to "
                f"`{new_name}` on its def line only: old_text must be that def line exactly as it "
                "appears in the file, and new_text the same line with the new name. Call the tool now.")
        return {"family": "tool_call", "text": text, "path": path,
                "gold": {"name": "edit_file",
                         "arguments": {"path": path, "old_text": line, "new_text": renamed}}}
    raise ValueError(kind)


def evicted_reads(prev: dict, cur: dict, records_by_index: dict) -> list[dict]:
    """read_file results present (intact) in call k-1's history but evicted from call k's request
    by this step's edit (placeholder, preview or snipped away)."""
    old_msgs = history_after(prev)
    uses = tool_uses(old_msgs)
    new_state = {block.get("tool_use_id"): result_text(block) for _, _, block in tool_results(cur["messages"])}
    out = []
    for _, _, block in tool_results(old_msgs):
        tid = block.get("tool_use_id")
        text = result_text(block)
        if is_placeholder(text) or is_preview(text):
            continue
        after = new_state.get(tid)
        if after is not None and not (is_placeholder(after) or is_preview(after)):
            continue                                   # still intact in call k
        name, args = uses.get(tid, (None, {}))
        path = normpath(args.get("path", ""))
        if name != "read_file" or text.startswith("Error:"):
            continue
        if path.endswith(".py"):
            classes = CLASS_RE.findall(text)
            if classes:
                first, question = classes[0], "the name of the first class defined in that file"
            else:
                defs = DEF_RE.findall(text)
                first = defs[0] if defs else None
                question = "the name of the first function defined in that file (its first `def` line)"
        elif path.endswith(".md"):
            heads = HEAD_RE.findall(text)
            first = heads[0].strip() if heads else None
            question = "its first markdown heading, verbatim"
        else:
            continue
        if not first or len(first) < 4:
            continue
        out.append({"path": path, "gold": first, "question": question,
                    "how": "snip" if after is None else "placeholder_or_preview", "chars": len(text)})
    return out


def retention_probe(prev: dict, cur: dict, records_by_index: dict, rng: random.Random) -> dict | None:
    cur_blob = json.dumps({"s": cur.get("system"), "m": cur["messages"]}, ensure_ascii=False)
    options = [e for e in evicted_reads(prev, cur, records_by_index) if e["gold"] not in cur_blob]
    if not options:
        return None
    pick = rng.choice(sorted(options, key=lambda e: (e["path"], e["gold"])))
    text = (f"Earlier in this session you read `{pick['path']}` with read_file; that output is no longer "
            f"shown above. Without calling any tool, what is {pick['question']}? Reply with just that, "
            "or NOT_IN_CONTEXT if you cannot tell.")
    return {"family": "retention", "text": text, "path": pick["path"],
            "gold": {"answer": pick["gold"], "how": pick["how"]}}


def with_probe(messages: list, text: str) -> list:
    """Append the probe as a user text block after the last user message's tool results."""
    out = copy.deepcopy(messages)
    last = out[-1]
    if last.get("role") == "user":
        content = last["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        last["content"] = list(content) + [{"type": "text", "text": text}]
    else:
        out.append({"role": "user", "content": [{"type": "text", "text": text}]})
    return out


# -- per run ----------------------------------------------------------------------------------------

def run_events(run_dir: Path, manifest_row: dict) -> tuple[list[dict], list[dict]]:
    label = manifest_row["label"]
    req_path = run_dir / f"{label}.requests.jsonl.xz"
    if not req_path.exists():
        req_path = run_dir / f"{label}.requests.jsonl"
    if not req_path.exists():
        return [], []
    records = open_jsonl(req_path)
    by_index = {r["call_index"]: r for r in records}
    calls = lead_calls(records)
    events = []
    for step in range(1, len(calls)):
        prev, cur = calls[step - 1], calls[step]
        if prev.get("turn_id") != cur.get("turn_id"):
            continue                                   # a new user turn is not a compaction step
        info = classify(prev, cur)
        if info is None:
            continue
        events.append({"event_id": f"{label}#c{cur['call_index']}", "label": label,
                       "codebase": manifest_row["codebase"], "template": manifest_row["template"],
                       "family": manifest_row["family"], "prev_call": prev["call_index"],
                       "cur_call": cur["call_index"], "lead_step": step, "lead_calls": len(calls),
                       **info})
    rng = random.Random(f"10rope-events-{label}")
    eligible = [e for e in events if e["kinds"] != ["summary"] and "summary" not in e["kinds"]]
    chosen: list[dict] = []
    for kind in ("placeholder", "snip"):
        pool = [e for e in eligible if kind in e["kinds"] and e not in chosen]
        if pool and len(chosen) < 2:
            chosen.append(rng.choice(pool))
    rest = [e for e in eligible if e not in chosen]
    while len(chosen) < 2 and rest:
        chosen.append(rest.pop(rng.randrange(len(rest))))
    chosen.sort(key=lambda e: e["cur_call"])
    sampled = []
    families = ["explain", "modify", "tool_call"]
    for order, event in enumerate(chosen):
        prev, cur = by_index[event["prev_call"]], by_index[event["cur_call"]]
        erng = random.Random(f"10rope-probe-{event['event_id']}")
        surv = survivors(cur, event["first_edit_msg"], event["appended_from"])
        start = int(hashlib.sha1(event["event_id"].encode()).hexdigest(), 16) % 3
        probe = None
        for k in range(3):
            if surv and probe is None:
                pick = erng.choice(surv)
                probe = gold_probe(families[(start + k) % 3], pick, erng)
                if probe:
                    probe["where"] = [pick["msg"], pick["block"]]    # survivor block in call k
        retention = retention_probe(prev, cur, by_index, erng)
        sampled.append({**event, "order": order, "probe": probe, "retention": retention,
                        "survivors": len(surv)})
    return events, sampled


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["extract"])
    ap.add_argument("--manifest", default=str(DATA / "manifest.jsonl"))
    ap.add_argument("--runs-dir", default=str(DATA / "runs"))
    ap.add_argument("--labels", default="")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--out-all", default=str(DATA / "events_all.jsonl"))
    ap.add_argument("--out", default=str(DATA / "events.jsonl"))
    args = ap.parse_args()
    rows = {json.loads(line)["label"]: json.loads(line) for line in open(args.manifest, encoding="utf-8")}
    wanted = set(args.labels.split(",")) if args.labels else None
    all_events, sampled, runs_with_capture = [], [], 0
    for label, row in sorted(rows.items()):
        if wanted and label not in wanted:
            continue
        if row["index"] % args.shards != args.shard:
            continue
        run_dir = Path(args.runs_dir) / label
        if not (run_dir / f"{label}.done").exists():
            continue
        events, chosen = run_events(run_dir, row)
        runs_with_capture += 1
        all_events += events
        sampled += chosen
    with open(args.out_all, "w", encoding="utf-8") as handle:
        for event in all_events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    with open(args.out, "w", encoding="utf-8") as handle:
        for event in sampled:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    kinds = Counter("+".join(e["kinds"]) for e in all_events)
    print(json.dumps({"runs": runs_with_capture, "events": len(all_events), "sampled": len(sampled),
                      "sampled_with_probe": sum(1 for e in sampled if e["probe"]),
                      "sampled_with_retention": sum(1 for e in sampled if e["retention"]),
                      "probe_families": dict(Counter(e["probe"]["family"] for e in sampled if e["probe"])),
                      "kinds": dict(kinds.most_common(12))}, indent=2))


if __name__ == "__main__":
    main()
