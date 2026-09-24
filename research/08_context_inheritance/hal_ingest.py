#!/usr/bin/env python3
"""HAL (Holistic Agent Leaderboard) run archives -> the same canonical read-item form.

    python3 research/08_context_inheritance/hal_ingest.py --archive taubench_airline_..._UPLOAD.zip
    python3 research/08_context_inheritance/hal_ingest.py --list                       # what is available, by benchmark

WHAT THIS ADDS OVER THE SWE CORPORA.  Two things nothing else here has.

  1. BENCHMARKS THAT ARE NOT CODING.  HAL publishes runs over tau-bench (airline and retail),
     GAIA, AppWorld, CORE-bench, USACO, SciCode, ScienceAgentBench, AssistantBench and Online
     Mind2Web.  A tau-bench "read" is a database tool call, not a file -- which is the point: if
     inheritance only pays off on file reads then it is a property of coding agents, not of agents.
  2. MEASURED PER-CALL LATENCY.  Every record is a Weave call with `started_at`, `ended_at` and
     `summary.weave.latency_ms`, plus `summary.usage` token counts.  The SWE trajectory corpora
     carry no timing at all, so every latency number there has to be modelled from the constants
     measured on this harness; here it is observed, on production runs of frontier models.

DECRYPTION.  The archives hold one Fernet-encrypted JSON.  The password is published in HAL's own
`hal/utils/decrypt.py` alongside the `hal-decrypt` CLI: it is a contamination guard, so the traces
are not scraped into training corpora verbatim, not access control.  Reading them this way is the
intended use.

ONE CALL PER ROUND.  A record's `inputs.messages` is the entire conversation the round was issued
against, so the last record of a task carries the whole trajectory and is parsed with the same
`parse_rounds` the SWE corpora use.  Timing comes from the ordered records instead.  Only top-level
records (`parent_id is None`) are counted: litellm wraps the provider SDK, so each call is logged
twice and summing both would double every latency.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1])); import _paths  # noqa: E401,E402,F401 -- research/_paths.py
from traj_ingest import parse_rounds, to_items                                  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
OUT_ROOT = REPO / "research" / "08_context_inheritance" / "data" / "realworld" / "hal"
HF_REPO = "agent-evals/hal_traces"
PASSWORD = b"hal1234"                    # published in princeton-pli/hal-harness, hal/utils/decrypt.py


def decrypt(path: Path) -> dict:
    from cryptography.fernet import Fernet                                      # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes                           # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC            # noqa: PLC0415

    with zipfile.ZipFile(path) as z:
        blob = json.load(z.open(z.namelist()[0]))
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=base64.b64decode(blob["salt"]), iterations=480_000)
    cipher = Fernet(base64.urlsafe_b64encode(kdf.derive(PASSWORD)))
    return json.loads(cipher.decrypt(base64.b64decode(blob["encrypted_data"])))


CALLING_TOOLS = re.compile(r"Calling tools:\s*(\[.*?\])\s*$", re.S)
CALL_RESULT = re.compile(r"^Call id: (\S+)\s*\n(Observation|Error):?\s*\n?(.*)$", re.S)


def _text(content) -> str:
    """HAL stores message content as a string, a block list, or a JSON-encoded block list."""
    if isinstance(content, str):
        stripped = content.lstrip()
        if stripped.startswith("[") and '"type"' in stripped[:80]:
            try:
                blocks = json.loads(content)
            except ValueError:
                return content
            if isinstance(blocks, list):
                return "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


def normalize_messages(messages: list) -> list:
    """Put every agent style into the one shape `parse_rounds` understands.

    HAL aggregates whatever each submitted agent logged, and they do not agree.  tau-bench's
    tool-calling agent emits OpenAI `tool_calls` and `tool` messages and needs nothing.  HAL's own
    generalist agent is a smolagents CodeAgent: it writes `Thought: ... Code: ```py ...``` `, appends
    the call list as a PYTHON REPR under `Calling tools:`, and gets results back as user messages
    headed `Call id: call_N / Observation:`.  Without this the GAIA and CORE-bench runs parse to zero
    rounds and the non-coding evidence quietly shrinks to tau-bench alone.

    A failed call (`Error:` instead of `Observation:`) is kept as a round but carries no content, so
    it counts toward rounds and never toward reusable bytes -- which is right: a retry fetched
    nothing to hold."""
    import ast                                                                  # noqa: PLC0415
    out, changed = [], False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role, text = message.get("role"), _text(message.get("content"))
        if role == "assistant" and not message.get("tool_calls"):
            match = CALLING_TOOLS.search(text)
            if match:
                try:
                    calls = ast.literal_eval(match.group(1))
                except (ValueError, SyntaxError):
                    calls = None
                if isinstance(calls, list) and calls:
                    out.append({"role": "assistant", "content": text[: match.start()],
                                "tool_calls": calls})
                    changed = True
                    continue
        if role == "user":
            match = CALL_RESULT.match(text.strip())
            if match:
                call_id, kind, body = match.groups()
                out.append({"role": "tool", "tool_call_id": call_id,
                            "content": "" if kind == "Error" else body})
                changed = True
                continue
        out.append(dict(message, content=text) if text else message)
    return out if changed else messages


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def ingest(archive: Path) -> list[dict]:
    data = decrypt(archive)
    config = data.get("config") or {}
    benchmark = config.get("benchmark_name") or archive.name.split("_")[0]
    agent = config.get("agent_name") or ""
    graded = data.get("results") or {}
    run_id = config.get("run_id") or archive.stem.replace("_UPLOAD", "")
    passed = set(map(str, graded.get("successful_tasks") or []))

    calls = [c for c in (data.get("raw_logging_results") or [])
             if isinstance(c, dict) and c.get("parent_id") is None]
    by_task: dict[str, list[dict]] = {}
    for call in calls:
        by_task.setdefault(str(call.get("weave_task_id")), []).append(call)

    out = []
    for task, records in by_task.items():
        records.sort(key=lambda c: _ts(c.get("started_at")) or 0.0)
        best = max(records, key=lambda c: len(((c.get("inputs") or {}).get("messages")) or []))
        messages = ((best.get("inputs") or {}).get("messages")) or []
        # the model's own last reply is not in its own request; append it so the final round counts
        messages = [m for m in messages if isinstance(m, dict)]
        choice = (((best.get("output") or {}).get("choices")) or [{}])[0]
        if isinstance(choice, dict) and isinstance(choice.get("message"), dict):
            messages = list(messages) + [choice["message"]]
        rounds = parse_rounds(normalize_messages(messages))
        if not rounds:
            continue
        items = to_items(rounds)

        timing, usage = [], []
        for i, call in enumerate(records):
            summary = call.get("summary") or {}
            weave = summary.get("weave") or {}
            tokens = summary.get("usage") or {}
            first = next(iter(tokens.values()), {}) if isinstance(tokens, dict) else {}
            start, end = _ts(call.get("started_at")), _ts(call.get("ended_at"))
            timing.append({
                "idx": i,
                "latency_s": (weave.get("latency_ms") or 0) / 1000.0
                             or ((end - start) if (start and end) else 0.0),
                "prompt_tokens": first.get("prompt_tokens"),
                "completion_tokens": first.get("completion_tokens"),
                "status": weave.get("status")})
            usage.append(first)
        span = 0.0
        starts = [_ts(c.get("started_at")) for c in records if _ts(c.get("started_at"))]
        ends = [_ts(c.get("ended_at")) for c in records if _ts(c.get("ended_at"))]
        if starts and ends:
            span = max(ends) - min(starts)

        out.append({
            "corpus": "hal", "harness": agent, "benchmark": benchmark,
            # unique per RUN.  HAL publishes the same benchmark over many models, and every run
            # reuses task ids 0..N, so a shared id made the cross-task population pair a task with
            # ITSELF solved by another model -- not inheritance at all.  `task_key` keeps the
            # benchmark id so those pairs can be excluded rather than silently counted.
            "instance_id": f"{benchmark}:{run_id}:{task}", "task_key": f"{benchmark}:{task}",
            "repo": benchmark,
            "resolved": task in passed,
            "n_messages": len(messages), "n_rounds": len(rounds), "n_items": len(items),
            "read_bytes": sum(i["bytes"] for i in items),
            "model_calls": len(records),
            "measured_span_s": round(span, 2),
            "measured_model_s": round(sum(t["latency_s"] for t in timing), 2),
            "timing": timing,
            "rounds": [{"idx": r["idx"], "ctx_chars": r["ctx_chars"], "out_chars": r["out_chars"],
                        "n_calls": len(r["calls"]), "read_bytes": r["read_bytes"],
                        "tools": r["tools"]} for r in rounds],
            "items": items,
        })
    return out


def list_archives(pattern: str = "") -> list[tuple[str, int]]:
    import urllib.request                                                       # noqa: PLC0415
    url = f"https://huggingface.co/api/datasets/{HF_REPO}/tree/main?recursive=true"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        tree = json.loads(fh.read())
    rows = [(x["path"], x.get("size") or 0) for x in tree
            if isinstance(x, dict) and str(x.get("path", "")).endswith(".zip")]
    if pattern:
        rows = [r for r in rows if re.search(pattern, r[0], re.I)]
    return sorted(rows, key=lambda r: r[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", type=Path, nargs="*", default=[])
    ap.add_argument("--list", dest="do_list", action="store_true")
    ap.add_argument("--pattern", default="")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.do_list:
        for path, size in list_archives(args.pattern):
            print(f"{size / 1e6:9.1f} MB  {path}")
        return 0
    if not args.archive:
        ap.error("pass --archive <zip> ... or --list")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for archive in args.archive:
        records = ingest(archive)
        name = archive.stem.replace("_UPLOAD", "")
        out = args.out or (OUT_ROOT / f"{name}.jsonl")
        with out.open("w") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
        reads = sum(r["n_items"] for r in records)
        print(f"[hal] {len(records)} tasks, {reads} read items, "
              f"{sum(r['model_calls'] for r in records)} model calls -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
