#!/usr/bin/env python3
"""Score the arms' outputs (offline, CPU, stdlib only).

Metrics, with their sources (details in README):

Natural tail -- the harness's real next round -- against the same model's recompute output:
  calls_match   BFCL-style AST match: same multiset of (tool name, normalized arguments)
                (Patil et al., "The Berkeley Function Calling Leaderboard", ICML 2025)
  name_match    same multiset of tool names;  inv_f1 / id_f1: ReCache's invocation F1 over
                (name, arguments) and over names (turn-level, handles parallel calls)
  text_rougeL   ROUGE-L F1 of the non-call text (CacheBlend / AgentKVShift report F1 / ROUGE-L)
and on its own (no reference):
  malformed     a tool-call block that does not parse;  unknown_tool: name not in the request's tools
  schema_ok     required arguments present with the schema's JSON types
  path_missing  read/edit target that does not exist in the run's sandbox at that point
  edit_applies  edit_file old_text found in the file as the run had left it (seed + replayed writes)
  refetch       read_file of a file whose full content is still intact in context (TRACE's
                "repeated action" at a compaction boundary)
  cite_valid    share of `file.py:N` citations whose line exists (code explanation answers)

Probe tails, against gold taken from the surviving read:
  explain       recall of the defined function/class names; extra_names (listed, not in the file)
  modify        Aider edit format: well_formed, search_in_context (SEARCH copied verbatim), renamed,
                compiles (full-file survivors); pass = all of them
  tool_call     BFCL exact match of edit_file(path, old_text, new_text) and per-argument accuracy
  retention     answered (gold present), abstained (NOT_IN_CONTEXT), hallucinated (anything else)

    python research/10_rope_shift/score.py --model qwen32b
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

from events import is_placeholder, is_preview, normpath, result_text, tool_results, tool_uses  # noqa: E402
from render import open_jsonl  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data" / "rope_shift_live"
HERMES_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
GLM_RE = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)
GLM_ARG_RE = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.S)
XML_FN_RE = re.compile(r"<function=([^>\s]+)>(.*?)(?:</function>|$)", re.S)
XML_PARAM_RE = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)
BLOCK_RE = re.compile(r"<<<<<<< SEARCH\n(.*?)\n?=======\n(.*?)\n?>>>>>>> REPLACE", re.S)
CITE_RE = re.compile(r"([\w./-]+\.py)(?::|\s+line\s+|#L)(\d+)")
ABSTAIN_RE = re.compile(r"not_in_context|not in (?:the |my |your )?(?:current )?context|not (?:available|shown|"
                        r"visible|present)|no longer (?:available|shown|visible|in)|(?:cannot|can't|can not|unable to) "
                        r"(?:tell|determine|recall|see|answer)|don't have|do not have")
TYPE_OK = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list,
           "object": dict}


# -- parsing ----------------------------------------------------------------------------------------

def _maybe_json(value: str):
    v = value.strip()
    if v[:1] in "[{" or v in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", v):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return value
    return value


def parse_calls(text: str, fmt: str) -> tuple[list[tuple[str, dict]], bool, str]:
    """(calls, malformed, text outside the calls)."""
    calls, malformed = [], False
    if fmt == "glm47":
        for body in GLM_RE.findall(text):
            name = body.split("<arg_key>", 1)[0].strip()
            args = {k.strip(): _maybe_json(v) for k, v in GLM_ARG_RE.findall(body)}
            if not name or not re.fullmatch(r"[\w.-]+", name):
                malformed = True
                continue
            calls.append((name, args))
        rest = GLM_RE.sub("", text)
    elif "<function=" in text:
        for name, body in XML_FN_RE.findall(text):
            calls.append((name.strip(), {k: _maybe_json(v) for k, v in XML_PARAM_RE.findall(body)}))
        rest = re.sub(r"<tool_call>.*?(?:</tool_call>|$)", "", text, flags=re.S)
    else:
        for body in HERMES_RE.findall(text):
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(body)
                except (json.JSONDecodeError, ValueError):
                    malformed = True
                    continue
            if not isinstance(obj, dict) or "name" not in obj:
                malformed = True
                continue
            args = obj.get("arguments", obj.get("parameters", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    malformed = True
                    args = {"_raw": args}
            calls.append((str(obj["name"]), args if isinstance(args, dict) else {"_raw": args}))
        rest = HERMES_RE.sub("", text)
    return calls, malformed, re.sub(r"<think>.*?</think>", "", rest, flags=re.S).strip()


def partial_call_name(text: str, fmt: str) -> str | None:
    """Tool name of a call cut off by max_tokens (its JSON / XML never closes)."""
    if "<tool_call>" not in text:
        return None
    body = text.split("<tool_call>", 1)[1]
    if fmt == "glm47":
        name = body.split("<arg_key>", 1)[0].strip()
        return name if re.fullmatch(r"[\w.-]+", name or "") else None
    match = re.search(r'"name"\s*:\s*"([^"]+)"', body) or re.search(r"<function=([^>\s]+)>", body)
    return match.group(1) if match else None


def norm_value(key: str, value):
    if isinstance(value, str):
        v = value.strip()
        return normpath(v) if key in ("path", "file_path", "pattern") else v
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: norm_value(k, v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [norm_value(key, v) for v in value]
    return value


def canon(call: tuple[str, dict]) -> str:
    name, args = call
    return json.dumps([name, {k: norm_value(k, v) for k, v in sorted(args.items())}], sort_keys=True,
                      ensure_ascii=False)


def f1(a: list, b: list) -> float | None:
    if not a and not b:
        return None
    ca, cb = Counter(a), Counter(b)
    hit = sum((ca & cb).values())
    if hit == 0:
        return 0.0
    p, r = hit / sum(ca.values()), hit / sum(cb.values())
    return 2 * p * r / (p + r)


def rouge_l(a: str, b: str) -> float | None:
    x, y = a.split(), b.split()
    if not x and not y:
        return None
    if not x or not y:
        return 0.0
    x, y = x[:600], y[:600]
    prev = [0] * (len(y) + 1)
    for tok in x:
        cur = [0]
        for j, t2 in enumerate(y):
            cur.append(prev[j] + 1 if tok == t2 else max(prev[j + 1], cur[j]))
        prev = cur
    lcs = prev[-1]
    if lcs == 0:
        return 0.0
    p, r = lcs / len(x), lcs / len(y)
    return round(2 * p * r / (p + r), 5)


# -- run state --------------------------------------------------------------------------------------

class RunState:
    """Captured requests of one run, for tool schemas, sandbox reconstruction and context checks."""

    def __init__(self, label: str, manifest_row: dict, runs_dir: Path):
        path = runs_dir / label / f"{label}.requests.jsonl.xz"
        if not path.exists():
            path = runs_dir / label / f"{label}.requests.jsonl"
        self.records = {r["call_index"]: r for r in open_jsonl(path)}
        self.row = manifest_row
        self.seed = Path(manifest_row["seed"])
        self.sandbox = manifest_row["sandbox"]

    def local(self, path: str) -> Path | None:
        path = normpath(path)
        prefix = self.sandbox.rstrip("/") + "/"
        if path.startswith(prefix):
            return self.seed / path[len(prefix):]
        if path == self.sandbox.rstrip("/"):
            return self.seed
        candidate = REPO / path
        return candidate

    def writes_before(self, call_index: int) -> list[tuple[str, str, dict]]:
        """Successful write_file / edit_file calls issued before `call_index`, in order."""
        seen, ops = set(), []
        for idx in sorted(i for i in self.records if i < call_index):
            record = self.records[idx]
            if record.get("purpose") != "lead":
                continue
            messages = list(record["messages"])
            uses = tool_uses(messages)
            for _, _, block in tool_results(messages):
                tid = block.get("tool_use_id")
                if tid in seen or tid not in uses:
                    continue
                name, args = uses[tid]
                text = result_text(block)
                if name == "write_file" and text.startswith("Wrote "):
                    ops.append((tid, name, args))
                elif name == "edit_file" and text.startswith("Edited "):
                    ops.append((tid, name, args))
                seen.add(tid)
        return ops

    def file_at(self, path: str, call_index: int) -> str | None:
        local = self.local(path)
        content = None
        if local is not None and local.is_file():
            try:
                content = local.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = None
        target = normpath(path)
        for _, name, args in self.writes_before(call_index):
            if normpath(args.get("path", "")) != target:
                continue
            if name == "write_file":
                content = str(args.get("content", ""))
            elif content is not None and args.get("old_text") in content:
                content = content.replace(args["old_text"], str(args.get("new_text", "")), 1)
        return content


# -- scoring ----------------------------------------------------------------------------------------

def schema_ok(call: tuple[str, dict], tools: dict) -> bool:
    name, args = call
    schema = (tools.get(name) or {}).get("input_schema") or {}
    for key in schema.get("required", []):
        if key not in args:
            return False
    props = schema.get("properties", {})
    for key, value in args.items():
        kind = (props.get(key) or {}).get("type")
        if kind in TYPE_OK and not isinstance(value, TYPE_OK[kind]):
            return False
        if kind == "integer" and isinstance(value, bool):
            return False
    return True


def intact_reads(record: dict) -> set[str]:
    uses = tool_uses(record["messages"])
    out = set()
    for _, _, block in tool_results(record["messages"]):
        name, args = uses.get(block.get("tool_use_id"), (None, {}))
        text = result_text(block)
        if name == "read_file" and not args.get("offset") and args.get("limit") is None \
                and not (is_placeholder(text) or is_preview(text) or text.startswith("Error:")):
            out.add(normpath(args.get("path", "")))
    return out


def citations(text: str, state: RunState) -> tuple[int, int]:
    n = valid = 0
    for path, line in CITE_RE.findall(text):
        n += 1
        local = state.local(path)
        if local is None or not local.is_file():
            matches = list(state.seed.rglob(Path(path).name)) if state.seed.is_dir() else []
            local = matches[0] if len(matches) == 1 else None
        if local is not None and local.is_file():
            try:
                nlines = local.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
            except OSError:
                continue
            valid += 1 <= int(line) <= nlines
    return n, valid


def score_natural(rec: dict, ref: dict | None, event: dict, state: RunState, fmt: str) -> dict:
    cur = state.records[event["cur_call"]]
    tools = {t.get("name"): t for t in (cur.get("tools") or [])}
    calls, malformed, rest = parse_calls(rec["text"], fmt)
    truncated = rec["finish"] == "length"
    # a call cut off by the token cap is not malformed (truncation is reported on its own)
    m = {"n_calls": len(calls), "malformed": malformed and not truncated, "truncated": truncated,
         "unknown_tool": any(name not in tools for name, _ in calls),
         "schema_ok": all(schema_ok(c, tools) for c in calls) if calls else None}
    missing, applies, refetch = [], [], []
    intact = intact_reads(cur)
    for name, args in calls:
        path = args.get("path")
        if name in ("read_file", "edit_file") and isinstance(path, str):
            content = state.file_at(path, event["cur_call"])
            missing.append(content is None)
            if name == "edit_file" and content is not None:
                applies.append(str(args.get("old_text", "")) in content and bool(args.get("old_text")))
            if name == "read_file" and not args.get("offset") and args.get("limit") is None:
                refetch.append(normpath(path) in intact)
    m["path_missing"] = any(missing) if missing else None
    m["edit_applies"] = all(applies) if applies else None
    m["refetch"] = any(refetch) if refetch else None
    n_cite, n_valid = citations(rest, state)
    m["citations"] = n_cite
    m["cite_valid"] = round(n_valid / n_cite, 4) if n_cite else None
    if ref is not None:
        rcalls, _, rrest = parse_calls(ref["text"], fmt)
        m["exact"] = rec["same_as_recompute"]
        ref_cut = ref["finish"] == "length" and partial_call_name(ref["text"], fmt) is not None
        if rcalls and not ref_cut:
            a, b = [canon(c) for c in calls], [canon(c) for c in rcalls]
            m["kind"] = "call"
            m["calls_match"] = Counter(a) == Counter(b)
            m["name_match"] = Counter(n for n, _ in calls) == Counter(n for n, _ in rcalls)
            m["inv_f1"] = f1(a, b)
            m["id_f1"] = f1([n for n, _ in calls], [n for n, _ in rcalls])
        elif ref_cut:
            # recompute's call was cut by the token cap (typically write_file with a whole file):
            # only the tool name is comparable
            m["kind"] = "truncated_call"
            names = [n for n, _ in calls] or [partial_call_name(rec["text"], fmt)]
            m["name_match"] = names[:1] == [partial_call_name(ref["text"], fmt)]
        else:
            m["kind"] = "text"
            m["text_rougeL"] = rouge_l(rest, rrest)
    return m


def score_probe(rec: dict, probe: dict, state: RunState, event: dict, fmt: str) -> dict:
    family, gold, text = probe["family"], probe["gold"], rec["text"]
    cur = state.records[event["cur_call"]]
    mi, bi = probe.get("where", [None, None])
    survivor = ""
    if mi is not None:
        survivor = result_text(cur["messages"][mi]["content"][bi])
    if family == "explain":
        names = set(gold.get("names") or (set(gold.get("defs", [])) | set(gold.get("classes", []))))
        found = {n for n in names if re.search(rf"\b{re.escape(n)}\b", text)}
        listed = re.findall(r"`?\b([A-Za-z_][A-Za-z0-9_]{2,})\b`?", text.split("(2)")[-1])
        extra = sorted({t for t in listed if ("_" in t or t[:1].isupper() or t in names)
                        and t not in names and t not in survivor})
        recall = len(found) / len(names) if names else None
        return {"recall": round(recall, 4) if recall is not None else None, "extra_names": len(extra),
                "pass": bool(recall is not None and recall >= 0.8)}
    if family == "modify":
        match = BLOCK_RE.search(text)
        if not match:
            return {"well_formed": False, "pass": False}
        search, replace = match.group(1), match.group(2)
        old, new = gold["old_name"], gold["new_name"]
        leftover = re.sub(rf"\b{re.escape(new)}\b", "", replace)
        m = {"well_formed": True, "search_in_context": bool(search) and search in survivor,
             "has_def": gold["def_line"].strip() in search,
             "renamed": bool(re.search(rf"\b{re.escape(new)}\b", replace))
             and not re.search(rf"\b{re.escape(old)}\b", leftover)}
        full_file = not re.search(r"\n\.\.\. \(\d+ more lines\)$", survivor)
        m["compiles"] = None
        if m["search_in_context"] and full_file:
            try:
                compile(survivor.replace(search, replace, 1), "<probe>", "exec")
                m["compiles"] = True
            except (SyntaxError, ValueError):
                m["compiles"] = False
        m["pass"] = m["search_in_context"] and m["has_def"] and m["renamed"] and m["compiles"] is not False
        return m
    if family == "tool_call":
        calls, malformed, _ = parse_calls(text, fmt)
        call = next((c for c in calls if c[0] == "edit_file"), calls[0] if calls else None)
        want = gold["arguments"]
        if call is None:
            return {"parsed": False, "malformed": malformed, "pass": False}
        name, args = call
        m = {"parsed": True, "malformed": malformed, "name_ok": name == "edit_file",
             "path_ok": normpath(str(args.get("path", ""))) == normpath(want["path"]),
             "old_ok": str(args.get("old_text", "")).rstrip("\n") == want["old_text"].rstrip("\n"),
             "new_ok": str(args.get("new_text", "")).rstrip("\n") == want["new_text"].rstrip("\n"),
             "old_in_context": bool(args.get("old_text")) and str(args.get("old_text")) in survivor}
        m["pass"] = all(m[k] for k in ("name_ok", "path_ok", "old_ok", "new_ok"))
        return m
    if family == "retention":
        answer = gold["answer"].strip().lower()
        low = text.lower()
        calls, _, _ = parse_calls(text, fmt)
        hit = bool(re.search(rf"(?<![\w]){re.escape(answer)}(?![\w])", low))
        abstain = bool(ABSTAIN_RE.search(low))
        refetch = any(name == "read_file" for name, _ in calls)
        return {"answered": hit and not abstain, "abstained": abstain and not hit,
                "refetch": refetch and not hit and not abstain,
                "hallucinated": not hit and not abstain and not refetch, "pass": hit and not abstain}
    raise ValueError(family)


def merged(path: Path, pattern: str) -> list[str]:
    """Lines of `path`, or of every shard file matching `pattern` when `path` does not exist."""
    files = [path] if path.exists() else sorted(path.parent.glob(pattern))
    lines = []
    for f in files:
        lines += [line for line in open(f, encoding="utf-8") if line.strip()]
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen32b")
    ap.add_argument("--arms", default="")
    ap.add_argument("--events", default=str(DATA / "events.jsonl"))
    ap.add_argument("--manifest", default=str(DATA / "manifest.jsonl"))
    ap.add_argument("--runs-dir", default=str(DATA / "runs"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    from render import MODELS
    fmt = MODELS[args.model]["tool_format"]
    arms_path = Path(args.arms or DATA / f"arms_{args.model}.jsonl")
    out_path = Path(args.out or DATA / f"scored_{args.model}.jsonl")
    events = {json.loads(l)["event_id"]: json.loads(l) for l in merged(Path(args.events), "events_s*.jsonl")}
    manifest = {json.loads(l)["label"]: json.loads(l) for l in open(args.manifest, encoding="utf-8")}
    records = []
    for line in merged(arms_path, f"arms_{args.model}_s*.jsonl"):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:        # the last line of a shard killed mid-write
            print(f"skipping a truncated record: {line[:80]!r}")
    # a shard rerun can repeat a record: keep the first per (event, tail, arm)
    seen, unique = set(), []
    for r in records:
        key = (r["event_id"], r["tail"], r["arm"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    records = unique
    records.sort(key=lambda r: (r["label"], r["event_id"]))
    refs = {(r["event_id"], r["tail"]): r for r in records if r["arm"] == "recompute"}
    state, n = None, 0
    with out_path.open("w", encoding="utf-8") as sink:
        for rec in records:
            event = events.get(rec["event_id"])
            if event is None:
                continue
            if state is None or state.row["label"] != rec["label"]:
                state = RunState(rec["label"], manifest[rec["label"]], Path(args.runs_dir))
            if rec["tail"] == "natural":
                ref = None if rec["arm"] == "recompute" else refs.get((rec["event_id"], "natural"))
                metrics = score_natural(rec, ref, event, state, fmt)
            else:
                metrics = score_probe(rec, event[rec["tail"]], state, event, fmt)
            out = {k: rec[k] for k in ("event_id", "label", "model", "tail", "arm", "n_new", "finish",
                                       "same_as_recompute", "first_div", "kinds", "plan", "probe_family")}
            out["tf"] = rec.get("tf")
            out["metrics"] = metrics
            for key in ("repair", "deviation"):         # Part B (repair.py) records only
                if key in rec:
                    out[key] = rec[key]
            out["codebase"], out["template"], out["family"] = event["codebase"], event["template"], event["family"]
            sink.write(json.dumps(out, ensure_ascii=False) + "\n")
            n += 1
    print(f"scored {n} records -> {out_path}")


if __name__ == "__main__":
    main()
