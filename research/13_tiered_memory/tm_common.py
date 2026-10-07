"""Small helpers shared by research/13_tiered_memory's analysis scripts and its server / client extensions.

Kept free of heavy imports (no numpy, no vLLM): the API server, the EngineCore and the harness process all
import it.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Qwen3 special tokens (the same ids for Qwen3-8B and Qwen3-32B)
IM_START = 151644      # <|im_start|>
IM_END = 151645        # <|im_end|>
NL = 198               # "\n" (the separator the template puts after every <|im_end|>)
TOOL_CALL = 151657     # <tool_call>

# -- tool keys (the predictor's vocabulary) ----------------------------------------------------------------------
ABS_PATH = re.compile(r"(?:^|[\s'\"=])(/[^\s'\";|&()]+)")
SLEEP_CMD = re.compile(r"(^|[;&|(\s])sleep\s+\d|time\.sleep\(")


def bash_class(command: str) -> str:
    """06's command classes (research/06_tool_cost/tool_census.py bash_class), copied so the server needs no 06."""
    c = (command or "").strip()
    if SLEEP_CMD.search(c):
        return "sleep"
    if "pytest" in c:
        return "pytest"
    head = re.sub(r"^(cd\s+\S+\s*(&&|;)\s*)+", "", c)
    head = re.sub(r"^(timeout\s+\S+\s+|env\s+(\w+=\S+\s+)*)", "", head)
    word = Path(head.split()[0]).name if head.split() else ""
    if word in {"python", "python3"}:
        return "python-test" if re.search(r"test_\w+\.py|unittest", head) else "python"
    if word in {"grep", "rg", "egrep"}:
        return "grep"
    if word == "find":
        return "find"
    if word in {"ls", "cat", "head", "tail", "wc", "sed", "awk", "tree", "echo", "pwd", "stat", "file", "du"}:
        return "view"
    if word in {"curl", "wget"}:
        return "network"
    if word == "git":
        return "git"
    return "other"


def tool_key(name: str, inp: dict | None) -> str:
    """Predictor key of one tool call: the tool name; bash split by command class plus a scope -- pytest
    `/suite` when it runs a whole tests/ directory, find / recursive grep / view / python / other `/wide` when
    the command walks a huge tree: site-packages (7.6 GB, 63k files on NFS) or a path at most two levels
    below / (outside /tmp and /dev)."""
    if name != "bash":
        return name
    cmd = (inp or {}).get("command") or ""
    cls = bash_class(cmd)
    key = f"bash:{cls}"
    if cls == "pytest":
        if re.search(r"(^|\s)(\./)?tests/?(\s|$)", cmd):
            key += "/suite"
    elif cls == "grep" and not re.search(r"(^|\s)(-\w*[rR]\w*|--recursive)\b|(^|[\s;&|(])rg\s", cmd):
        pass                                          # a non-recursive grep reads named files only
    elif "site-packages" in cmd or any(path.rstrip("/").count("/") <= 2 and not path.startswith(("/tmp", "/dev"))
                                       for path in ABS_PATH.findall(cmd)):
        key += "/wide"
    return key


# -- the duration predictor (tool_durations.json, written by tm_predict.py) ----------------------------------------
@lru_cache(maxsize=1)
def _table(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def predict(key: str, path: str | None = None) -> tuple[float, float, str]:
    """(p50 s, p90 s, source) of how long a session is away after a reply whose dominant call is `key`.
    Lookup: windows[key] -> windows[key without '/scope'] -> tool_time[...] + overhead -> global."""
    t = _table(path or os.environ.get("TM_DURATIONS", str(HERE / "tool_durations.json")))
    if not t:
        return 1.0, 5.0, "default"
    base = key.split("/", 1)[0]
    for k, src in ((key, "windows"), (base, "windows")):
        r = t.get("windows", {}).get(k)
        if r:
            return r["p50_s"], r["p90_s"], f"windows:{k}"
    over = t.get("overhead_s", 0.0)
    r = t.get("tool_time", {}).get(base)
    if r:
        return r["p50_s"] + over, r["p90_s"] + over, f"tool_time:{base}"
    g = t.get("global", {})
    return g.get("p50_s", 1.0), g.get("p90_s", 5.0), "global"


def predict_reply(keys: list[str], turn_end_s: float | None = None) -> tuple[float, float, str]:
    """Prediction for a whole reply: no tool call -> a turn end (TM_THINK_S overrides the table, since the think
    time is a property of the workload); several calls -> the dominant one (largest p50)."""
    if not keys:
        if turn_end_s is not None:
            return turn_end_s, 2.0 * turn_end_s, "think"
        return predict("(turn end)")
    best = max((predict(k) + (k,) for k in keys), key=lambda x: x[0])
    return best[0], best[1], f"{best[2]}"


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def tool_calls_in(text: str) -> list[tuple[str, dict]]:
    """(name, arguments) of every hermes-format tool call in a generated text."""
    out = []
    for m in TOOL_CALL_RE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        args = obj.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        out.append((obj.get("name") or "?", args if isinstance(args, dict) else {}))
    return out


# -- request ids (set by research/11_multiuser_kv/mu_session.py) -----------------------------------------------------
def chain_of(request_id: str | None, session: str | None = None) -> str | None:
    """X-Request-Id '<label>~a<k>~<call>~<purpose>~<agent>' -> '<label>~a<k>~<purpose>~<agent>': one message list
    per (session, purpose, agent) -- the lead, its memory calls and each subagent keep separate histories."""
    if not request_id:
        return session
    parts = request_id.split("~")
    if len(parts) == 5:
        return "~".join((parts[0], parts[1], parts[3], parts[4]))
    return session or request_id


def session_of_chain(chain: str) -> str:
    parts = chain.split("~")
    return "~".join(parts[:2]) if len(parts) >= 2 else chain
