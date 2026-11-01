#!/usr/bin/env python3
"""Static (CPU) dependency census of the rounds table (rd_rounds.py), for every round of both sources.

For round j of a turn (its tool calls c_j, the results O_j):
  - lexical provenance: the salient strings of c_j's arguments (paths, line anchors, grep patterns and code
    identifiers in shell commands, the first lines of an edit's old_text, a glob's literal prefix) are traced to the
    earliest text that contains them: the system prompt, the turn's task, an earlier result O_r, the model's own
    earlier text or calls, or nothing (novel). Round j depends lexically on round r when one of its strings first
    appeared in O_r. This finds dependence where the arguments copy a result; it cannot prove independence (a
    decision can follow from a result without copying it). rd_cf.py tests that causally on a sample.
  - effects: read and write sets per call (read_file and glob read; edit_file and write_file write; bash goes through
    s15's own display-only classifier, code.py _is_read_only_command, and any other command reads and writes
    everything; denied or failed calls change nothing). Two calls conflict when one writes what the other reads or
    writes.
  - resources: GPU side in tokens (prompt, cached, output), CPU side in tool seconds; a round is CPU-heavy when its
    tools ran >= 1 s (HEAVY_S).
Per turn, the static dependency graph: level(j) = 1 + the highest level among the rounds j depends on lexically
(the turn's task is level 0; the final answer waits for every round), the greedy merge groups (round j joins the
current group unless it depends lexically on a round of that group) and whether a joining round's effects commute
with the group's (statically reorderable).

    python research/20_round_dependency/rd_static.py build [--src t19,t16] [--workers 48]
    python research/20_round_dependency/rd_static.py tables        # data/tables/census.md + .json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from multiprocessing import get_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_rounds as rr  # noqa: E402

STATIC = rr.DATA / "static"
TABLES = rr.DATA / "tables"
HARNESS = rr.REPO / "s15_integrated_harness" / "code.py"
HEAVY_S = 1.0
BOOKKEEPING = {"todo_write", "list_tasks", "get_task", "list_crons", "list_teammates"}
EXT = re.compile(r"\.(py|pyi|go|js|mjs|ts|tsx|jsx|java|kt|rb|rs|c|h|cc|cpp|hpp|cs|php|md|rst|txt|json|ya?ml|toml|"
                 r"cfg|ini|sh|html?|css|scss|sql|lock|xml|proto|mod|sum|csv|ipynb|r|jl|lua|swift|scala|vue)$", re.I)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{3,}$")
SHELL_WORDS = {"grep", "rg", "find", "ls", "cat", "head", "tail", "wc", "sed", "awk", "sort", "uniq", "cut", "echo",
               "python", "python3", "pip", "pytest", "cd", "git", "make", "npm", "node", "go", "cargo", "bash", "sh",
               "xargs", "tr", "diff", "tree", "stat", "file", "test", "true", "false", "then", "else", "done", "for",
               "while", "print", "import", "from", "return", "None", "True", "False", "self", "exit", "status",
               "type", "name", "path", "line", "lines", "file", "files", "with", "open", "read", "write", "def",
               "class", "null", "echo", "printf", "export", "source", "timeout", "time", "nproc", "which", "env",
               "include", "exclude", "head", "count", "main", "init", "test", "tests", "data", "list", "dict"}


# -- s15's own classifier, without importing the harness --------------------------------------------------------------
def _load_readonly():
    """code.py's READ_ONLY_* constants and _is_read_only_command, compiled from the source with ast."""
    want = {"READ_ONLY_COMMANDS", "READ_ONLY_GIT_SUBCOMMANDS", "FIND_WRITE_FLAGS", "_strip_quoted_text",
            "_is_read_only_command"}
    tree = ast.parse(HARNESS.read_text())
    body = [n for n in tree.body
            if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name in want)]
    ns = {"re": re, "Path": Path}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(HARNESS), "exec"), ns)
    return ns["_is_read_only_command"]


_IS_READ_ONLY = _load_readonly()
_DEVNULL = re.compile(r"\s*\d*&?>{1,2}\s*/dev/null|\s*\d>&\d")


def IS_READ_ONLY(command: str) -> bool:
    """s15's classifier fails closed on any '>'; output thrown into /dev/null (2>/dev/null, 2>&1) writes nothing."""
    return _IS_READ_ONLY(_DEVNULL.sub("", command))


def redirect(tok: str) -> bool:
    return bool(re.match(r"^\d*&?>{1,2}|^\d>&\d$", tok)) or tok == "/dev/null"


# -- salient strings ------------------------------------------------------------------------------------------------
def shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def first_lines(text: str, k: int = 2, min_len: int = 8) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if len(s) >= min_len:
            out.append(s)
        if len(out) >= k:
            break
    return out


COMMON = {"print", "import", "from", "class", "return", "lambda", "while", "assert", "except", "finally", "raise",
          "yield", "global", "async", "await", "string", "format", "result", "results", "value", "values", "output",
          "error", "errors", "false", "true", "none", "input", "first", "check", "should", "would", "could", "there",
          "these", "those", "which", "where", "using", "where", "other", "about", "after", "before", "between",
          "default", "content", "contents", "number", "length", "object", "objects", "method", "function", "module",
          "python", "python3", "pytest", "tests", "testing", "import", "config", "settings", "update", "create",
          "delete", "append", "extend", "remove", "items", "range", "split", "strip", "replace", "startswith",
          "endswith", "isinstance", "exists", "sys.path", "os.path", "subprocess", "encoding", "utf-8", "install",
          "testbed", "upgrade", "version", "verbose", "quiet", "requirements", "directory", "recursive"}


def code_words(text: str, limit: int = 40) -> list[str]:
    """Distinctive names inside a command or file body (python -c scripts, heredocs): dotted module paths and
    identifiers with an underscore or a capital inside, or plain words of 6+ letters outside a common list. Common
    words still resolve to the system prompt or the task, which come first, so they rarely fake a dependency."""
    out = []
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text or ""):
        if len(w) < 5 or w.lower() in COMMON or w in SHELL_WORDS:
            continue
        if "." in w or "_" in w.strip("_") or re.search(r"[a-z][A-Z]", w) or len(w) >= 6:
            out.append(w)
        if len(out) >= limit:
            break
    return out


def salient(call: dict) -> list[tuple[str, str]]:
    """(kind, string) pairs that identify what the call acts on."""
    name, inp = call.get("name"), call.get("input") or {}
    out = []
    if name in ("read_file", "edit_file", "write_file") and isinstance(inp.get("path"), str):
        out.append(("path", inp["path"]))
        if name == "read_file" and str(inp.get("offset") or "").isdigit() and int(inp["offset"]) > 1:
            out.append(("line", str(int(inp["offset"]))))
        if name == "edit_file":
            out += [("anchor", s) for s in first_lines(inp.get("old_text") or "")]
        if name == "write_file" and isinstance(inp.get("content"), str):
            out += [("ident", w) for w in code_words(inp["content"])]
    elif name == "glob" and isinstance(inp.get("pattern"), str):
        lit = re.split(r"[*?\[{]", inp["pattern"])[0].rstrip("/")
        if len(lit) >= 3:
            out.append(("path", lit))
    elif name == "bash" and isinstance(inp.get("command"), str):
        toks = shell_tokens(inp["command"])
        for i, t in enumerate(toks):
            if not t or t.startswith("-") or t in ("|", "||", "&&", ";", ">", ">>", "<") or redirect(t):
                continue
            base = t.rstrip("/").split("/")[-1]
            if "/" in t.rstrip("/") or EXT.search(t):          # 'pkg/' is the same argument as 'pkg
                out.append(("path", t.rstrip("/")))
            elif i > 0 and toks[i - 1] in ("grep", "rg", "egrep") or (i > 1 and toks[i - 2] in ("grep", "rg")
                                                                       and toks[i - 1].startswith("-")):
                for part in re.split(r"\\\||\|", t):
                    part = re.sub(r"[\\^$]", "", part).strip()
                    if len(part) >= 4:
                        out.append(("pattern", part))
            elif IDENT.match(base) and base not in SHELL_WORDS and ("_" in base or re.search(r"[a-z][A-Z]", base)):
                out.append(("ident", base))
        for m in re.finditer(r"sed -n ['\"]?(\d+)[,p]", inp["command"]):
            out.append(("line", m.group(1)))
        out += [("ident", w) for w in code_words(inp["command"])]
    seen, uniq = set(), []
    for k, s in out:
        if (k, s) not in seen:
            seen.add((k, s))
            uniq.append((k, s))
    return uniq


def forms(kind: str, s: str, cwd: str | None) -> list[str]:
    """Spellings of a salient string that count as the same thing in earlier text."""
    if kind != "path":
        return [s]
    p = s[2:] if s.startswith("./") else s
    out = [p]
    if cwd and p.startswith(cwd.rstrip("/") + "/"):
        out.append(p[len(cwd.rstrip("/")) + 1:])
    elif cwd and not p.startswith("/"):
        out.append(cwd.rstrip("/") + "/" + p)
    base = p.rstrip("/").split("/")[-1]
    if base != p and len(base) >= 6 and "." in base:
        out.append(base)
    return [f for f in out if len(f) >= 3]


def _word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def found(doc: str, form: str) -> bool:
    """`form` occurs in `doc` not glued to an identifier character on either side (so core.py is not score.py)."""
    if not form:
        return False
    start = 0
    while True:
        i = doc.find(form, start)
        if i < 0:
            return False
        before = doc[i - 1] if i > 0 else " "
        after = doc[i + len(form)] if i + len(form) < len(doc) else " "
        if (form[0] in "/." or not (_word(before) or before in ".-")) and (form[-1] == "/" or not _word(after)):
            return True
        start = i + 1


LINE_CTX = "(?:^|\n|:)\\s*{n}(?:[:\\t|-]|\\s{{2}})"


def found_line(doc: str, n: str) -> bool:
    return re.search(LINE_CTX.format(n=n), doc) is not None


# -- effects ------------------------------------------------------------------------------------------------------
def norm_path(p: str, cwd: str | None) -> str:
    p = p.strip().rstrip("/") if p.strip() not in ("/", "") else p.strip()
    if p.startswith("./"):
        p = p[2:]
    if cwd and p.startswith(cwd.rstrip("/") + "/"):
        p = p[len(cwd.rstrip("/")) + 1:]
    return p or "."


def effects(call: dict, result: str | None, cwd: str | None) -> tuple[set, set, bool]:
    """(reads, writes, unknown): '*' means everything. Denied or failed calls change nothing."""
    name, inp = call.get("name"), call.get("input") or {}
    res = (result or "").lstrip()
    failed = res.startswith("Error") or res.startswith("Permission denied")
    if name == "read_file" and isinstance(inp.get("path"), str):
        return {norm_path(inp["path"], cwd)}, set(), False
    if name == "glob" and isinstance(inp.get("pattern"), str):
        lit = re.split(r"[*?\[{]", inp["pattern"])[0]
        return {norm_path(lit, cwd) + "/" if lit else "*"}, set(), False
    if name in ("edit_file", "write_file") and isinstance(inp.get("path"), str):
        p = norm_path(inp["path"], cwd)
        return ({p} if name == "edit_file" else set()), (set() if failed else {p}), False
    if name in BOOKKEEPING:
        return {"#todo"}, ({"#todo"} if name == "todo_write" else set()), False
    if name == "bash" and isinstance(inp.get("command"), str):
        if res.startswith("Permission denied"):
            return set(), set(), False
        if IS_READ_ONLY(inp["command"]):
            paths = {norm_path(t, cwd) for t in shell_tokens(_DEVNULL.sub("", inp["command"]))
                     if t and not t.startswith("-") and not redirect(t) and ("/" in t or EXT.search(t))}
            return (paths or {"*"}), set(), False
        return {"*"}, {"*"}, True
    if name in ("create_task", "update_task", "claim_task", "complete_task"):
        return {"#tasks"}, {"#tasks"}, False
    return {"*"}, {"*"}, True


def overlap(a: set, b: set) -> bool:
    """Do two sets of paths touch the same state? A directory covers the files under it; '*' (an unknown command)
    covers every file but not the harness's own state ('#todo', '#tasks')."""
    if a & b & {"#todo", "#tasks"}:
        return True
    fa = {x for x in a if not x.startswith("#")}
    fb = {x for x in b if not x.startswith("#")}
    if not fa or not fb:
        return False
    if "*" in fa or "*" in fb:
        return True
    for x in fa:
        for y in fb:
            if x == y or x == "." or y == "." or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/"):
                return True
    return False


def conflict(ra: set, wa: set, rb: set, wb: set) -> bool:
    """Do two calls fail to commute (write/read, read/write or write/write on overlapping state)?"""
    return overlap(wa, rb | wb) or overlap(ra, wb)


# -- one session ----------------------------------------------------------------------------------------------------
def session_static(sess: dict, rows: list[dict], system_text: str, cwd: str | None) -> list[dict]:
    docs: list[tuple[tuple, str]] = [(("system",), system_text)]
    out = []
    by_turn = defaultdict(list)
    for r in rows:
        by_turn[r["turn"]].append(r)
    for turn in sorted(by_turn):
        rounds = sorted(by_turn[turn], key=lambda r: r["j"])
        prompt = (sess["turns"].get(turn) or {}).get("prompt") or ""
        docs.append((("task", turn), prompt))
        level, group_of, eff_of = {}, {}, {}
        group, group_rounds, group_effects = 0, [], []
        for r in rounds:
            j = r["j"]
            srcs = Counter()
            dep_rounds = set()
            for call in r["calls"]:
                for kind, s in salient(call):
                    src = None
                    for key, doc in docs:
                        if (found_line(doc, s) if kind == "line" else any(found(doc, f) for f in forms(kind, s, cwd))):
                            src = key
                            break
                    if src is None:
                        srcs["novel"] += 1
                    elif src[0] == "result" and src[1] == turn:
                        dep_rounds.add(src[2])
                        srcs["result"] += 1
                    elif src[0] == "result":
                        srcs["prior_turn"] += 1
                    elif src[0] == "model" and src[1] == turn:
                        srcs["model"] += 1
                    else:
                        srcs[src[0] if src[0] in ("system", "task") else "prior_turn"] += 1
            reads, writes, unknown = set(), set(), False
            per_call = []
            for call, res in zip(r["calls"], r["results"]):
                rd, wr, unk = effects(call, res, cwd)
                per_call.append((rd, wr))
                reads |= rd
                writes |= wr
                unknown |= unk
            eff_of[j] = (reads, writes)
            eff_deps = {k for k, (rk, wk) in eff_of.items() if k < j and conflict(rk, wk, reads, writes)}
            if r["final"]:
                lvl = 1 + max(level.values(), default=0)
            else:
                lvl = 1 + max((level[k] for k in dep_rounds if k in level), default=0)
            level[j] = lvl
            # greedy merge groups: join unless a lexical dependency points into the current group
            joins = (not r["final"]) and bool(group_rounds) and not (dep_rounds & set(group_rounds)) \
                and not any(x["final"] for x in rounds if x["j"] in group_rounds)
            if joins:
                commutes = not any(conflict(rg, wg, reads, writes) for rg, wg in group_effects)
                group_rounds.append(j)
                group_effects.append((reads, writes))
            else:
                group += 1 if group_rounds else 0
                group_rounds, group_effects = [j], [(reads, writes)]
                commutes = None
            group_of[j] = group
            tool_s = sum(x for x in r["tool_ms"] if x) / 1000.0 if r["tool_ms"] else 0.0
            out.append({"src": r["src"], "sid": r["sid"], "group": r["group"], "kind": r.get("kind"), "turn": turn,
                        "j": j, "call": r["call"], "final": r["final"], "n_calls": len(r["calls"]),
                        "tools": [c["name"] for c in r["calls"]], "append_ok": r["append_ok"],
                        "tail_ok": r.get("tail_ok"),
                        "results_src": r["results_src"],
                        "lex_prev": (j - 1) in dep_rounds, "lex_deps": sorted(dep_rounds),
                        "lex_src": dict(srcs), "eff_prev": (j - 1) in eff_deps, "eff_deps": sorted(eff_deps),
                        "eff_unknown": unknown, "level": lvl, "mgroup": group, "joins": joins,
                        "commutes": commutes, "tool_s": round(tool_s, 4), "heavy": tool_s >= HEAVY_S,
                        "np": r["np"], "cached": r["cached"], "out": r["out"],
                        "bookkeeping": bool(r["calls"]) and all(c["name"] in BOOKKEEPING for c in r["calls"])})
            docs.append((("model", turn, j), r["text"] + "\n" + "\n".join(json.dumps(c["input"], ensure_ascii=False)
                                                                        for c in r["calls"])))
            if r["calls"]:
                docs.append((("result", turn, j), "\n".join(x or "" for x in r["results"])))
    return out


def _system_and_cwd(sess: dict, first_row: dict | None) -> tuple[str, str | None]:
    if first_row is None:
        return "", None
    req = rr.request(first_row["ptr"])
    system = req.get("system") or ""
    if not isinstance(system, str):
        system = rr.block_text(system)
    tools = json.dumps(req.get("tools") or [], ensure_ascii=False)
    cwd = None
    if sess["src"] == "t16":
        cfg = rr.REPO / Path(sess["requests"]).parent / "config.json"
        try:
            cwd = json.loads(cfg.read_text()).get("cwd")
        except (OSError, ValueError):
            cwd = None
    return system + "\n" + tools, cwd


def _one(args) -> list[dict]:
    sess, rows = args
    rows = sorted(rows, key=lambda r: r["call"])
    system, cwd = _system_and_cwd(sess, rows[0] if rows else None)
    return session_static(sess, rows, system, cwd)


def build(src: str, workers: int) -> None:
    sessions, rows = rr.load(src)
    groups = rr.by_session(rows)
    work = [(s, groups.get(s["sid"], [])) for s in sessions if groups.get(s["sid"])]
    out = []
    with get_context("fork").Pool(workers) as pool:
        for rs in pool.imap(_one, work, chunksize=2):
            out += rs
    n = rr.write_jsonl_xz(STATIC / f"static_{src}.jsonl.xz", out)
    print(f"[{src}] {n} rounds -> {STATIC}", flush=True)


# -- tables ---------------------------------------------------------------------------------------------------------
def summarize(rows: list[dict]) -> dict:
    tool = [r for r in rows if not r["final"]]
    pairs = [r for r in tool if r["j"] > 0]          # rounds with a predecessor in the turn
    turns = defaultdict(list)
    for r in rows:
        turns[(r["sid"], r["turn"])].append(r)
    groups = [len({x["mgroup"] for x in t}) for t in turns.values()]
    levels = [max(x["level"] for x in t) for t in turns.values()]
    sizes = [len(t) for t in turns.values()]
    joins = [r for r in pairs if r["joins"]]
    tool_s = sum(r["tool_s"] for r in rows)

    def share(xs, f):
        return sum(1 for x in xs if f(x)) / len(xs) if xs else None

    return {
        "rounds": len(rows), "turns": len(turns), "tool_rounds": len(tool),
        "multi_call": share(tool, lambda r: r["n_calls"] > 1),
        "bookkeeping": share(tool, lambda r: r["bookkeeping"]),
        "lex_prev": share(pairs, lambda r: r["lex_prev"]),
        "lex_any": share(pairs, lambda r: bool(r["lex_deps"])),
        "lex_none_in_turn": share(pairs, lambda r: not r["lex_deps"]),
        "eff_prev": share(pairs, lambda r: r["eff_prev"]),
        "eff_unknown": share(tool, lambda r: r["eff_unknown"]),
        "free_prev": share(pairs, lambda r: not r["lex_prev"] and not r["eff_prev"]),
        "joins": share(pairs, lambda r: r["joins"]),
        "joins_commute": share(joins, lambda r: bool(r["commutes"])),
        "rounds_per_turn": sum(sizes) / len(sizes) if sizes else None,
        "groups_per_turn": sum(groups) / len(groups) if groups else None,
        "compression": sum(groups) / sum(sizes) if sizes else None,
        "critical_path": sum(levels) / sum(sizes) if sizes else None,
        "tool_s_per_round": tool_s / len(rows) if rows else None,
        "heavy": share(tool, lambda r: r["heavy"]),
        "tool_s_beside_join": (sum(r_prev["tool_s"] for r_prev, r in _pairs_prev(rows) if r["joins"]) / tool_s)
        if tool_s else None,
        "out_per_round": sum(r["out"] for r in rows) / len(rows) if rows else None,
    }


def _pairs_prev(rows):
    idx = {(r["sid"], r["turn"], r["j"]): r for r in rows}
    for r in rows:
        prev = idx.get((r["sid"], r["turn"], r["j"] - 1))
        if prev is not None:
            yield prev, r


COLS = [("rounds", "rounds", "{:,}"), ("turns", "turns", "{:,}"), ("rounds_per_turn", "rounds/turn", "{:.1f}"),
        ("multi_call", "multi-call", "{:.0%}"), ("bookkeeping", "todo-only", "{:.0%}"),
        ("lex_prev", "copies prev", "{:.0%}"), ("lex_none_in_turn", "copies none", "{:.0%}"),
        ("eff_prev", "effect prev", "{:.0%}"), ("free_prev", "free of prev", "{:.0%}"),
        ("joins", "joins group", "{:.0%}"), ("joins_commute", "of these commute", "{:.0%}"),
        ("compression", "groups/rounds", "{:.2f}"), ("critical_path", "path/rounds", "{:.2f}"),
        ("tool_s_per_round", "tool s/round", "{:.2f}"), ("heavy", ">=1 s tools", "{:.0%}"),
        ("tool_s_beside_join", "tool s beside join", "{:.0%}"), ("out_per_round", "out tok/round", "{:.0f}")]


def fmt(v, f):
    return "—" if v is None else f.format(v)


def tables() -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    lines = ["# Static census (rd_static.py)", "",
             "Every round of both sources. 'copies prev' = a salient argument string first appeared in the previous "
             "round's results; 'copies none' = no argument string came from any result of the turn; 'effect prev' = "
             "the round's effects conflict with the previous round's; 'free of prev' = neither; 'joins group' = the "
             "greedy static merge group takes the round; 'groups/rounds' = merged calls per turn over recorded calls; "
             "'path/rounds' = the static critical path over recorded calls; 'tool s beside join' = share of tool "
             "seconds spent in rounds whose next round joins their group (CPU work an early next call could overlap).",
             ""]
    out = {}
    for src in ("t19", "t16"):
        rows = rr.read_jsonl(STATIC / f"static_{src}.jsonl.xz")
        if not rows:
            continue
        sessions, _ = rr.load(src)
        meta = {s["sid"]: s for s in sessions}
        if src == "t19":
            keys = [("all", lambda r: True)] + [(f"kind {k}", (lambda k: lambda r: r["kind"] == k)(k))
                                                for k in ("E1", "E2", "E3", "M1", "M2", "M3")] + \
                   [(f"cell {c}", (lambda c: lambda r: r["group"] == c)(c)) for c in rr.T19_CELLS]
        else:
            keys = [("all", lambda r: True)] + \
                   [(f"{b} (all jobs)", (lambda b: lambda r: r["kind"] == b)(b)) for b in rr.T16_BENCHES] + \
                   [(f"{j.replace('minimax-m2.7', 'mm')}/{b}", (lambda j, b: lambda r: r["group"] == f"{j}/{b}")(j, b))
                    for j in rr.T16_JOBS for b in rr.T16_BENCHES]
        lines += [f"## {src}", "", "| subset | " + " | ".join(c[1] for c in COLS) + " |",
                  "|---|" + "---|" * len(COLS)]
        out[src] = {}
        for name, f in keys:
            s = summarize([r for r in rows if f(r)])
            out[src][name] = s
            lines.append(f"| {name} | " + " | ".join(fmt(s[c[0]], c[2]) for c in COLS) + " |")
        lines.append("")
        del meta
    (TABLES / "census.md").write_text("\n".join(lines) + "\n")
    (TABLES / "census.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--src", default="t19,t16")
    b.add_argument("--workers", type=int, default=48)
    sub.add_parser("tables")
    a = ap.parse_args()
    if a.cmd == "build":
        for src in a.src.split(","):
            build(src, a.workers)
    else:
        tables()


if __name__ == "__main__":
    main()
