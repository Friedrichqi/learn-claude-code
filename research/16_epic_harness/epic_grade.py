#!/usr/bin/env python3
"""Deterministic task graders for the EPIC study's live runs (offline, CPU, stdlib only).

Every grader returns {"score": float in [0, 1], "pass": bool, "details": {...}} from the run's
final state, judged against gold computed on THIS machine from the run's seed tree (plan §3.3). E1, E2 and
M1 give partial credit (`partial`: half content, half grounded content; the strict product is kept in
details as score_strict); pass rules are unchanged:

  E1 inventory    gold: every .py module under the root (10_rope_shift.workloads.py_files: no tests,
                  no __pycache__). pass: module recall >= 0.9, citation validity >= 0.9 and >= 90% of
                  the named modules cited inside their own file. score = (recall + recall x validity) / 2.
  E2 trace        gold: the template's entry call executed under sys.setprofile in a subprocess
                  against the seed (local HTTP server for requests/httpx, StringIO consoles), giving
                  the executed package functions and the call tree. The "spine" is the path to the
                  code that produces the result: from each top-level package call, descend to the
                  child call that returned the parent's return object (largest subtree below a
                  procedure returning None; constructors are not descended). DEFAULT_SPINES curates
                  click / typer / jinja2, e2_spines.json overrides any codebase, the deepest stack is
                  kept for reference, and s15_harness has a static spine. recall: spine FILES named (spine FUNCTIONS when the
                  spine spans <= 2 files); precision: min(share of citations inside executed
                  function / class spans, share of named package files that executed).
                  pass: recall >= 0.8 and precision >= 0.8; score = (recall + recall x precision) / 2.
  E3 orientation  each cited file:line (+-2 lines) is classed by AST as entry point (public API from
                  the package __init__, CLI decorator, console script, __main__), data structure
                  (non-exception class) or error handling (exception class, raise / try / except).
                  pass: every class has >= 1 valid grounded citation and >= 80% of citations are
                  valid; score = classes covered / 3 x validity.
  M1 build_tag    G = .py files directly under the root, not __init__, not tests. pass: every file
                  in G has a module-level BUILD_TAG = 'profiling' before its first def/class, removing
                  that node gives the seed's AST, nothing else changed except CHANGES_BUILD_TAG.md,
                  which lists every file of G at its BUILD_TAG line (+-1). score = (tagged/|G| +
                  tagged/|G| x changelog accuracy) / 2; 0 on any collateral change.
  M2 rename       sites = the target's references resolved by AST (its def, imports of it, uses of the
                  imported name, module-qualified attributes, __all__ strings); same-named methods,
                  parameters and attributes are not sites. pass: every site renamed, renaming
                  <f>_v2 back to <f> gives every file's seed AST (no collateral change, no other
                  entity renamed) and the package imports. score = sites renamed / sites; 0 on
                  collateral change.
  M3 docstrings   pass: every function / method of the target module that had no docstring
                  (typing.overload stubs excepted) gets a non-empty one, existing docstrings unchanged, the module's AST without docstrings
                  (and no-op statements) equals the seed's, no other file changed. score = coverage;
                  0 on behaviour change.

Inputs per run (epic_session.py / live_run.finalize): <label>.meta.json, <label>.answer.json
(EXPLAIN answers; MODIFY answers are kept as details), <label>.sandbox/ (MODIFY final tree) or else
<label>.diff (`diff -ruN seed sandbox`, applied to the seed).

    python research/16_epic_harness/epic_grade.py grade --manifest MANIFEST.jsonl --runs RUNS_DIR \
        --out GRADED.jsonl [--gold-cache DIR]
    python research/16_epic_harness/epic_grade.py audit --graded GRADED.jsonl --n 30 --out AUDIT.md
    python research/16_epic_harness/epic_grade.py gold --codebase packaging [--gold-cache DIR]
"""
from __future__ import annotations

import argparse
import ast
import csv
import fnmatch
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

os.environ.setdefault("HF_HOME", "/data/shared/huggingface")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import workloads as W10  # noqa: E402  (10_rope_shift: CODEBASES, SEED_IGNORE, py_files)
from score import CITE_RE  # noqa: E402  (10_rope_shift: `file.py:N`, `file.py line N`, `file.py#LN`)

# The models cite in many shapes besides topic 10's `file.py:N`: "`specifiers.py` (Line 123)",
# "core.py, starting from line 1", "line 45 of _ranges.py", "lines 10-20 in utils.py", "(L12)".
# A citation is a .py path and the line number written next to it in the same text line: the first
# number after the path (before the next path), else the closest number before it.
NUM_AFTER = re.compile(r"""^[`'"*\s]*(?:\)\s*)?(?::(\d{1,5})|#L(\d{1,5})|[\s,(\-–—:]*(?:\w+\s+){0,3}?"""
                       r"""(?:lines?|ln\.?|l\.|L)\s*#?\s*(\d{1,5}))""", re.I)
NUM_BEFORE = re.compile(r"""(?:lines?|ln\.?|L)\s*#?\s*(\d{1,5})(?:\s*[-–]\s*\d{1,5})?\s+(?:of|in|from)\s*[`'"*]*$""", re.I)
PATH_TOKEN_RE = re.compile(r"[\w./-]+\.py\b")
# A line reference set apart from the one path its line or block names (second grader audit, 2026-10-09):
# "- `api.py`: Implements the API. (Line 1)", or a block "1. **`core.py`**" / "- **File/Line**: `class
# Command` (line 10)", or "- **File:** `x.py`" / "- **Line Number:** 2". A block ends at a blank line or at
# a line naming another path.
PAREN_LINE = re.compile(r"\((?:lines?|L)\s*#?\s*(\d{1,5})(?:\s*[-–]\s*\d{1,5})?\)", re.I)
LABEL_LINE = re.compile(r"\blines?(?:\s+number)?\**\s*:\**\s*`?(\d{1,5})\b", re.I)


def extract_citations(text: str) -> list[tuple[str, int]]:
    """(path, line) pairs from free text, in order; CITE_RE's matches are a subset."""
    out = []
    block_path = None                       # the one path named by the current block's header line
    for raw in (text or "").splitlines():
        if not raw.strip():
            block_path = None
            continue
        tokens = list(PATH_TOKEN_RE.finditer(raw))
        cited = False
        for i, tok in enumerate(tokens):
            end = tokens[i + 1].start() if i + 1 < len(tokens) else len(raw)
            after = raw[tok.end():end]
            m = NUM_AFTER.match(after)
            num = next((g for g in m.groups() if g), None) if m else None
            if num is None:
                start = tokens[i - 1].end() if i > 0 else 0
                b = NUM_BEFORE.search(raw[start:tok.start()])
                num = b.group(1) if b else None
            if num is not None:
                out.append((tok.group(0), int(num)))
                cited = True
        if len(tokens) == 1:
            if not cited:
                m = PAREN_LINE.search(raw, tokens[0].end())
                if m:
                    out.append((tokens[0].group(0), int(m.group(1))))
                    cited = True
            block_path = None if cited else tokens[0].group(0)
        elif tokens:
            block_path = None
        elif block_path is not None:
            m = PAREN_LINE.search(raw) or LABEL_LINE.search(raw)
            if m:
                out.append((block_path, int(m.group(1))))
    return out

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON = sys.executable
GOLD_CACHE = HERE / "data" / "e2_gold"
SPINE_OVERRIDES = HERE / "e2_spines.json"
IGNORE = tuple(W10.SEED_IGNORE)
TAG_LINE_TOLERANCE = 1
THRESH = {"E1_recall": 0.9, "E1_valid": 0.9, "E1_self": 0.9, "E2_recall": 0.8, "E2_precision": 0.8,
          "E3_valid": 0.8}
PATH_RE = re.compile(r"[\w./-]+\.py\b")
ENTRY_DECORATORS = {"command", "group", "callback"}
DATA_BASES = {"NamedTuple", "TypedDict", "Enum", "IntEnum", "Flag", "IntFlag", "StrEnum", "Protocol"}
EXC_BASES = {"Exception", "BaseException"}
# s15_harness is a script, not an importable package: its E2 spine is static (one tool round)
S15_SPINE = ["agent_loop", "prepare_context", "update_context", "assemble_tool_pool",
             "assemble_system_prompt", "call_llm", "call_tool_handler", "build_user_content"]


# =================================================================================================
# trees: seed, final state, diffs
# =================================================================================================

def ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORE)


def read_tree(root: Path) -> dict[str, bytes]:
    """relative path -> bytes of every file a sandbox receives (live_run's SEED_IGNORE)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not ignored(d))
        for name in sorted(filenames):
            if not ignored(name):
                path = Path(dirpath) / name
                out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def tree_hash_of(tree: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(tree):
        digest.update(rel.encode() + b"\0")
        digest.update(tree[rel] + b"\0")
    return digest.hexdigest()


def materialize(tree: dict[str, bytes], dest: Path) -> Path:
    for rel, data in tree.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dest


def text_of(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", errors="replace")


class DiffError(ValueError):
    pass


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
EPOCH_RE = re.compile(r"\s1970-01-01 00:00:00")      # diff -N dates a missing side to the epoch


def _rel_from(header_path: str, sandbox_marker: str | None, seed_prefix: str | None) -> str | None:
    path = header_path.split("\t", 1)[0].strip()
    if sandbox_marker:
        marker = sandbox_marker.rstrip("/") + "/"
        if marker in path:
            return path.split(marker, 1)[1]
    if seed_prefix:
        prefix = seed_prefix.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return None


def parse_diff(text: str, sandbox_marker: str | None = None,
               seed_prefix: str | None = None) -> list[dict]:
    """Files of a `diff -ruN A B` (unified) output: [{rel, new_file, deleted, hunks: [...]}].
    `rel` comes from the +++ path below `sandbox_marker` (profiling_sandbox/<cb>), else from the
    --- path below `seed_prefix`; when neither applies it stays None and apply_diff places the file
    by the longest common path suffix that exists in the seed."""
    files, cur = [], None
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff "):
            cur = None
            i += 1
            continue
        if line.startswith("Binary files ") or line.startswith("Only in "):
            files.append({"rel": None, "binary": line.strip(), "hunks": []})
            i += 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = line[4:].rstrip("\n"), lines[i + 1][4:].rstrip("\n")
            rel = _rel_from(new, sandbox_marker, None) or _rel_from(old, None, seed_prefix)
            cur = {"rel": rel, "old_path": old.split("\t", 1)[0], "new_path": new.split("\t", 1)[0],
                   "hunks": [], "old_epoch": bool(EPOCH_RE.search(old)),
                   "new_epoch": bool(EPOCH_RE.search(new))}
            files.append(cur)
            i += 2
            continue
        match = HUNK_RE.match(line)
        if match and cur is not None:
            o_start, o_len = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
            n_start, n_len = int(match.group(3)), int(match.group(4) if match.group(4) is not None else 1)
            body = []
            i += 1
            o_left, n_left = o_len, n_len                     # consume exactly the hunk's lines
            while i < len(lines) and (o_left > 0 or n_left > 0 or lines[i].startswith("\\")):
                raw = lines[i]
                tag = raw[:1]
                if tag == "\\":
                    pass
                elif tag == " " or raw in ("\n", "\r\n"):
                    raw = raw if tag == " " else " " + raw
                    o_left -= 1
                    n_left -= 1
                elif tag == "-":
                    o_left -= 1
                elif tag == "+":
                    n_left -= 1
                else:
                    raise DiffError(f"{cur['rel']}: malformed hunk line {raw[:60]!r}")
                body.append(raw)
                i += 1
            cur["hunks"].append({"o_start": o_start, "o_len": o_len, "n_start": n_start,
                                 "n_len": n_len, "lines": body})
            continue
        i += 1
    return files


def _apply_hunks(old: str | None, hunks: list[dict], rel: str) -> str | None:
    src = [] if old is None else old.splitlines(keepends=True)
    out, pos = [], 0
    for h in hunks:
        start = h["o_start"] - 1 if h["o_len"] > 0 else h["o_start"]
        if start < pos or start > len(src):
            raise DiffError(f"{rel}: hunk at {h['o_start']} out of order")
        out.extend(src[pos:start])
        pos = start
        prev = None
        for raw in h["lines"]:
            tag, body = raw[:1], raw[1:]
            if tag == "\\":                                  # "\ No newline at end of file"
                if prev in ("+", " ") and out and out[-1].endswith("\n"):
                    out[-1] = out[-1][:-1]
                continue
            if tag in (" ", "-"):
                if pos >= len(src) or src[pos].rstrip("\n") != body.rstrip("\n"):
                    raise DiffError(f"{rel}: context mismatch at line {pos + 1}")
                if tag == " ":
                    out.append(src[pos])
                pos += 1
            elif tag == "+":
                out.append(body)
            prev = tag
    out.extend(src[pos:])
    return "".join(out)


def _guess_rel(old_path: str, new_path: str, tree: dict) -> str:
    """Relative path from the common suffix of the two header paths: the longest suffix present in
    the seed, else (an added file) the longest suffix whose directory exists in the seed."""
    a, b = old_path.split("/"), new_path.split("/")
    common: list[str] = []
    while a and b and a[-1] == b[-1]:
        common.insert(0, a.pop())
        b.pop()
    for k in range(len(common), 0, -1):
        if "/".join(common[-k:]) in tree:
            return "/".join(common[-k:])
    dirs = {r.rsplit("/", 1)[0] for r in tree if "/" in r}
    for k in range(len(common), 0, -1):
        parent = "/".join(common[-k:-1])
        if parent in dirs:
            return "/".join(common[-k:])
    if not common:
        raise DiffError(f"cannot place diff header {new_path!r}")
    return common[-1]


def apply_diff(seed_tree: dict[str, bytes], diff_text: str, sandbox_marker: str | None = None,
               seed_prefix: str | None = None) -> dict[str, bytes]:
    """The final tree: the seed with a `diff -ruN seed sandbox` applied (new and deleted files too)."""
    tree = dict(seed_tree)
    for f in parse_diff(diff_text, sandbox_marker, seed_prefix):
        if f.get("binary"):
            continue                                         # binary file: reported, not applied
        rel = f["rel"] or _guess_rel(f["old_path"], f["new_path"], seed_tree)
        old = text_of(tree.get(rel))
        new = _apply_hunks(old, f["hunks"], rel)
        if f.get("new_epoch") and not new:                   # deleted (+++ side is the epoch)
            tree.pop(rel, None)
        else:
            tree[rel] = new.encode("utf-8")
    return tree


def changed_files(seed_tree: dict[str, bytes], final_tree: dict[str, bytes]) -> dict[str, str]:
    """rel -> added | deleted | modified."""
    out = {}
    for rel in sorted(set(seed_tree) | set(final_tree)):
        a, b = seed_tree.get(rel), final_tree.get(rel)
        if a == b:
            continue
        out[rel] = "added" if a is None else "deleted" if b is None else "modified"
    return out


# =================================================================================================
# package index (AST facts for the EXPLAIN graders)
# =================================================================================================

def dotted(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func)
    if isinstance(node, ast.Subscript):
        return dotted(node.value)
    return None


def first_line(node) -> int:
    lines = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
    return min(lines)


class PackageIndex:
    """Files, line counts, functions / classes with spans, error-handling lines, __main__ blocks,
    the public API names and console-script targets of one seed tree."""

    def __init__(self, root: Path, codebase: str | None = None, tree: dict[str, bytes] | None = None):
        self.root = Path(root)
        self.codebase = codebase
        tree = tree if tree is not None else read_tree(self.root)
        self.texts = {rel: text_of(b) for rel, b in tree.items() if rel.endswith(".py")}
        self.lines = {rel: t.count("\n") + (0 if t.endswith("\n") else 1) for rel, t in self.texts.items()}
        self.modules = sorted(p.relative_to(self.root).as_posix() for p in W10.py_files(self.root)) \
            if self.root.is_dir() else sorted(self.texts)
        self.modules = [m for m in self.modules if m in self.texts]
        self.funcs, self.classes = [], []
        self.error_lines: dict[str, set[int]] = defaultdict(set)
        self.main_blocks: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.trees = {}
        for rel, text in self.texts.items():
            try:
                tree_ = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            self.trees[rel] = tree_
            self._walk(rel, tree_)
        self._mark_exceptions()
        self.exported = self._exported()
        self.console = self._console_scripts()

    def _walk(self, rel: str, tree_: ast.AST) -> None:
        def visit(node, prefix: str, in_class: bool):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qual = f"{prefix}{child.name}"
                    self.funcs.append({"rel": rel, "qualname": qual, "name": child.name,
                                       "start": first_line(child), "def_line": child.lineno,
                                       "end": child.end_lineno, "method": in_class,
                                       "decorators": [dotted(d) or "" for d in child.decorator_list]})
                    visit(child, f"{qual}.<locals>.", False)
                elif isinstance(child, ast.ClassDef):
                    qual = f"{prefix}{child.name}"
                    self.classes.append({"rel": rel, "qualname": qual, "name": child.name,
                                         "start": first_line(child), "def_line": child.lineno,
                                         "end": child.end_lineno,
                                         "bases": [dotted(b) or "" for b in child.bases],
                                         "decorators": [dotted(d) or "" for d in child.decorator_list],
                                         "is_exception": False})
                    visit(child, f"{qual}.", True)
                else:
                    if isinstance(child, ast.Raise):
                        self.error_lines[rel].add(child.lineno)
                    elif isinstance(child, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                        self.error_lines[rel].add(child.lineno)
                        for handler in child.handlers:
                            self.error_lines[rel].add(handler.lineno)
                    elif isinstance(child, ast.If) and self._is_main_guard(child.test):
                        self.main_blocks[rel].append((child.lineno, child.end_lineno))
                    visit(child, prefix, in_class)
        visit(tree_, "", False)

    @staticmethod
    def _is_main_guard(test) -> bool:
        return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "__name__" and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value == "__main__")

    def _mark_exceptions(self) -> None:
        names = set()
        changed = True
        while changed:
            changed = False
            for c in self.classes:
                if c["is_exception"]:
                    continue
                last = [b.rsplit(".", 1)[-1] for b in c["bases"]]
                if any(b in EXC_BASES or b.endswith(("Error", "Exception", "Warning")) or b in names
                       for b in last):
                    c["is_exception"] = True
                    names.add(c["name"])
                    changed = True

    def _exported(self) -> set[str]:
        """Names a package __init__ binds or lists in __all__ (the public API)."""
        out = set()
        for rel, tree_ in self.trees.items():
            if not rel.endswith("__init__.py") or rel.count("/") > 1:
                continue
            for node in tree_.body:
                if isinstance(node, ast.ImportFrom):
                    out.update(a.asname or a.name for a in node.names if a.name != "*")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    out.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "__all__":
                            for elt in ast.walk(node.value):
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    out.add(elt.value)
        return {n for n in out if not n.startswith("_")}

    def _console_scripts(self) -> set[tuple[str, str]]:
        dist = W10.CODEBASES.get(self.codebase, (None, None))[1] if self.codebase else None
        if not dist:
            return set()
        out = set()
        try:
            eps = importlib.metadata.distribution(dist).entry_points
        except importlib.metadata.PackageNotFoundError:
            return out
        pkg = self.root.name
        for ep in eps:
            if ep.group != "console_scripts" or ":" not in ep.value:
                continue
            module, attr = ep.value.split(":", 1)
            parts = module.split(".")
            if parts[0] != pkg:
                continue
            rel = "/".join(parts[1:]) + ".py" if len(parts) > 1 else "__init__.py"
            if rel not in self.texts:
                rel = "/".join(parts[1:] + ["__init__.py"])
            out.add((rel, attr.split(".")[0].strip()))
        return out

    # -- citations --------------------------------------------------------------------------------

    def resolve(self, cited: str) -> list[str]:
        """Package files a cited path can mean (exact > suffix match on path components)."""
        p = cited.strip().strip("`'\"()[]").replace("\\", "/")
        p = re.sub(r"^(\./)+", "", p)
        if "profiling_sandbox/" in p:
            p = p.split("profiling_sandbox/", 1)[1]
            p = p.split("/", 1)[1] if "/" in p else p
        if p in self.texts:
            return [p]
        cands = [rel for rel in self.texts if rel.endswith("/" + p) or p.endswith("/" + rel)]
        if len(cands) > 1:
            longest = max(len(r) for r in cands)
            exactish = [r for r in cands if p.endswith("/" + r) and len(r) == longest]
            if len(exactish) == 1:
                return exactish
        return sorted(cands)

    def citations(self, text: str) -> list[dict]:
        out = []
        for path, line in extract_citations(text or ""):
            line = int(line)
            rels = self.resolve(path)
            valid = [r for r in rels if 1 <= line <= self.lines.get(r, 0)]
            out.append({"path": path, "line": line, "rels": rels, "valid_rels": valid,
                        "valid": bool(valid)})
        return out

    def named_files(self, text: str) -> set[str]:
        """Package files the text names by path (path tokens that resolve to one file)."""
        out = set()
        for token in PATH_RE.findall(text or ""):
            rels = self.resolve(token)
            if len(rels) == 1:
                out.add(rels[0])
        return out

    def module_mentions(self, text: str) -> set[str]:
        """Modules the text names: by path, by unique basename, or by dotted module name."""
        text = text or ""
        found = set(self.named_files(text))
        pkg = self.root.name
        for rel in self.modules:
            if rel in found:
                continue
            stem = rel[:-3]
            if stem.endswith("__init__"):
                continue                                      # an __init__ needs a path mention
            dotted_rel = stem.replace("/", ".")
            names = [f"{pkg}.{dotted_rel}"] + ([dotted_rel] if "." in dotted_rel else [])
            if any(re.search(rf"(?<![\w.]){re.escape(n)}(?![\w])", text) for n in names):
                found.add(rel)
        return found

    def span_of(self, rel: str, qualname: str, firstlineno: int | None = None) -> dict | None:
        """The function `qualname` of `rel`: the definition starting at `firstlineno` when given,
        else the implementation (the last definition that is not an @overload stub)."""
        cands = [f for f in self.funcs if f["rel"] == rel and f["qualname"] == qualname]
        if firstlineno is not None:
            for f in cands:
                if f["start"] <= firstlineno <= f["def_line"]:
                    return f
        impl = [f for f in cands if not is_overload_stub(f)]
        return (impl or cands or [None])[-1]

    def defs_of(self, rel: str, qualname: str) -> list[dict]:
        """Every definition of `qualname` in `rel` (implementation and @overload stubs)."""
        return [f for f in self.funcs if f["rel"] == rel and f["qualname"] == qualname]


def is_overload_stub(func) -> bool:
    """A typing.overload stub (an index dict or an ast def)."""
    decorators = func["decorators"] if isinstance(func, dict) else [dotted(d) or "" for d in func.decorator_list]
    return any(d.rsplit(".", 1)[-1] == "overload" for d in decorators)


# =================================================================================================
# EXPLAIN graders
# =================================================================================================

def partial(content: float, grounded: float) -> float:
    """Partial credit (G7 calibration, 2026-10-09): half for the content (modules / spine / files tagged), half
    for the content that is also grounded (valid citations, precise trace, correct changelog lines). The strict
    product alone floored most pilot runs: an answer with every module but no line numbers scored 0."""
    return 0.5 * content + 0.5 * grounded


def grade_e1(index: PackageIndex, answer: str) -> dict:
    gold = list(index.modules)
    mentioned = index.module_mentions(answer)
    cites = index.citations(answer)
    valid = [c for c in cites if c["valid"]]
    recall = len([m for m in gold if m in mentioned]) / len(gold) if gold else 0.0
    validity = len(valid) / len(cites) if cites else 0.0
    cited_files = {r for c in valid for r in c["valid_rels"]}
    covered = [m for m in gold if m in mentioned]
    self_cited = sum(m in cited_files for m in covered) / len(covered) if covered else 0.0
    ok = recall >= THRESH["E1_recall"] and validity >= THRESH["E1_valid"] and \
        self_cited >= THRESH["E1_self"]
    return {"score": round(partial(recall, recall * validity), 4), "pass": ok,
            "details": {"score_strict": round(recall * validity, 4),
                        "modules": len(gold), "mentioned": len(covered), "recall": round(recall, 4),
                        "citations": len(cites), "valid": len(valid), "validity": round(validity, 4),
                        "self_cited": round(self_cited, 4),
                        "missed": [m for m in gold if m not in mentioned][:40]}}


def classify_citation(index: PackageIndex, rel: str, line: int, window: int = 2) -> set[str]:
    lo, hi = line - window, line + window
    kinds = set()
    pkg_entry_names = index.exported
    for f in index.funcs:
        if f["rel"] != rel:
            continue
        header = (f["start"], f["def_line"])
        if header[0] <= hi and header[1] >= lo:
            deco = [d.rsplit(".", 1)[-1] for d in f["decorators"]]
            if (not f["method"] and f["name"] in pkg_entry_names) or \
                    any(d in ENTRY_DECORATORS for d in deco) or (rel, f["name"]) in index.console:
                kinds.add("entry")
    for c in index.classes:
        if c["rel"] != rel:
            continue
        header = (c["start"], c["def_line"])
        in_header = header[0] <= hi and header[1] >= lo
        if in_header:
            if c["is_exception"]:
                kinds.add("error")
            else:
                kinds.add("data")
                if c["name"] in pkg_entry_names:
                    kinds.add("entry")
        elif c["start"] <= line <= c["end"] and not c["is_exception"]:
            inside_method = any(f["rel"] == rel and f["method"] and f["start"] <= line <= f["end"]
                                and c["start"] <= f["start"] <= c["end"] for f in index.funcs)
            if not inside_method:
                kinds.add("data")                             # a field / class-level attribute
    if any(lo <= n <= hi for n in index.error_lines.get(rel, ())):
        kinds.add("error")
    if any(s <= line <= e for s, e in index.main_blocks.get(rel, ())) or rel.endswith("__main__.py"):
        kinds.add("entry")
    return kinds


def grade_e3(index: PackageIndex, answer: str) -> dict:
    cites = index.citations(answer)
    per_kind: Counter = Counter()
    grounded = 0
    for c in cites:
        if not c["valid"]:
            continue
        kinds = set()
        for rel in c["valid_rels"]:
            kinds |= classify_citation(index, rel, c["line"])
        c["kinds"] = sorted(kinds)
        grounded += bool(kinds)
        per_kind.update(kinds)
    validity = sum(c["valid"] for c in cites) / len(cites) if cites else 0.0
    covered = [k for k in ("entry", "data", "error") if per_kind[k] > 0]
    ok = len(covered) == 3 and validity >= THRESH["E3_valid"]
    return {"score": round(len(covered) / 3 * validity, 4), "pass": ok,
            "details": {"citations": len(cites), "valid": sum(c["valid"] for c in cites),
                        "validity": round(validity, 4), "grounded": grounded,
                        "per_kind": dict(per_kind), "covered": covered}}


# -- E2: execution-traced gold ----------------------------------------------------------------------

E2_SPECS = {
    "requests": {"setup": "import requests", "call": "requests.get(URL)", "server": True},
    "click": {"setup": ("import click\n@click.command()\n@click.option('--name', default='world')\n"
                        "def hello(name):\n    click.echo('Hello ' + name)\n"),
              "call": "hello.main(args=['--name', 'x'], standalone_mode=False)"},
    "jinja2": {"setup": "from jinja2 import Environment",
               "call": "Environment().from_string('Hello {{ name }}!').render(name='World')"},
    "rich": {"setup": "import io\nfrom rich.console import Console",
             "call": "Console(file=io.StringIO(), width=80).print('Hello [bold]World[/bold]')"},
    "httpx": {"setup": "import httpx", "call": "httpx.get(URL)", "server": True},
    "yaml": {"setup": "import yaml", "call": "yaml.safe_load('a: 1\\nb: [1, 2]\\n')"},
    "packaging": {"setup": "from packaging.specifiers import SpecifierSet",
                  "call": "SpecifierSet('>=1.0').contains('1.2')"},
    "tqdm": {"setup": "import io\nfrom tqdm import tqdm",
             "call": "[x for x in tqdm(range(3), file=io.StringIO())]"},
    "typer": {"setup": ("import sys, typer\ndef main(name: str = 'world'):\n    print('Hello ' + name)\n"
                        "sys.argv = ['prog', '--name', 'x']"),
              "call": "typer.run(main)"},
}

TRACER = r'''
import contextlib, io, json, os, sys, threading
root = os.path.realpath(sys.argv[1]); out_path = sys.argv[2]; spec = json.loads(sys.argv[3])
sys.path.insert(0, os.path.dirname(root))
for key in list(os.environ):
    if key.lower() in ("http_proxy", "https_proxy", "all_proxy"):
        del os.environ[key]
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
URL = None
if spec.get("server"):
    import http.server
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    URL = "http://127.0.0.1:%d/" % server.server_address[1]
env = {"URL": URL, "__name__": "__e2_trace__"}
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    exec(spec["setup"], env)
prefix = root + os.sep
executed, stack, best, top, nodes = {}, [], {}, [-1], []
def key_of(code):
    path = os.path.realpath(code.co_filename)
    if not path.startswith(prefix) or code.co_qualname.startswith("<") or ".<lambda>" in code.co_qualname:
        return None
    return (os.path.relpath(path, root).replace(os.sep, "/"), code.co_qualname, code.co_firstlineno)
def ret_kind(value):
    if value is None:
        return "none"
    if isinstance(value, bool) or (isinstance(value, int) and -5 <= value <= 256) or \
            (isinstance(value, str) and len(value) <= 1):
        return "single"                      # identity says nothing about who produced it
    return "object"
def prof(frame, event, arg):
    if event == "call":
        k = key_of(frame.f_code)
        if k is None:
            return
        if not stack:
            top[0] += 1
        parent = stack[-1][2] if stack else -1
        nodes.append([k[0], k[1], k[2], parent, top[0], None, None])
        stack.append((k, id(frame), len(nodes) - 1))
        executed[k] = executed.get(k, 0) + 1
        distinct = list(dict.fromkeys(s[0] for s in stack))
        cur = best.get(top[0])
        if cur is None or len(distinct) > len(cur):
            best[top[0]] = distinct
    elif event == "return":
        if stack and stack[-1][1] == id(frame):
            idx = stack.pop()[2]
            nodes[idx][5] = id(arg)
            nodes[idx][6] = ret_kind(arg)
error = None
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    sys.setprofile(prof)
    try:
        exec(spec["call"], env)
    except BaseException as exc:
        error = type(exc).__name__ + ": " + str(exc)[:200]
    finally:
        sys.setprofile(None)
json.dump({"executed": [[*k, n] for k, n in sorted(executed.items())],
           "stacks": [best[i] for i in sorted(best)], "nodes": nodes, "error": error},
          open(out_path, "w"))
'''


def value_spine(nodes: list[list], max_len: int = 30) -> list[tuple[str, str]]:
    """The path to the code that produces the result, per top-level package call: descend to the
    child call that returned the parent's return object (the first such child for objects, the last
    for singletons such as True / small ints); below a call that returns None (a procedure, e.g.
    Console.print) descend to the child with the largest subtree. Union over top-level calls."""
    kids: dict[int, list[int]] = defaultdict(list)
    for i, n in enumerate(nodes):
        if n[3] >= 0:
            kids[n[3]].append(i)
    size = [1] * len(nodes)
    for i in range(len(nodes) - 1, -1, -1):          # children always come after their parent
        if nodes[i][3] >= 0:
            size[nodes[i][3]] += size[i]
    out: list[tuple[str, str]] = []
    for i, n in enumerate(nodes):
        if n[3] != -1:
            continue
        cur, path = i, [i]
        if n[1].endswith((".__init__", ".__new__")):
            out.append((n[0], n[1]))                       # a constructor in the chain: not descended
            continue
        while kids.get(cur) and len(path) < max_len:
            ret_id, kind = nodes[cur][5], nodes[cur][6]
            children = kids[cur]
            if kind in (None, "none"):
                nxt = max(children, key=lambda c: (size[c], -c))
            else:
                same = [c for c in children if nodes[c][5] == ret_id]
                if not same:
                    break                              # produced by non-package code at this level
                nxt = same[0] if kind == "object" else same[-1]
            path.append(nxt)
            cur = nxt
        out.extend((nodes[j][0], nodes[j][1]) for j in path)
    return list(dict.fromkeys(out))


def trace_gold(root: Path, spec: dict, timeout: float = 300.0) -> dict:
    """Run `spec` (setup untraced, call traced) against the package at `root` in a subprocess."""
    root = Path(root)
    with tempfile.TemporaryDirectory(prefix="e2trace-") as tmp:
        # a private copy, so the trace never imports anything from the live seed's __pycache__ state
        pkg_dir = Path(tmp) / root.name
        materialize(read_tree(root), pkg_dir)
        out = Path(tmp) / "trace.json"
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run([PYTHON, "-I", "-c", TRACER, str(pkg_dir), str(out), json.dumps(spec)],
                              capture_output=True, text=True, timeout=timeout, env=env, cwd=tmp)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"E2 trace failed ({proc.returncode}): {proc.stderr[-2000:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def static_gold_s15(index: PackageIndex) -> dict:
    keys = []
    for name in S15_SPINE:
        hit = next((f for f in index.funcs if f["name"] == name and not f["method"]
                    and "<locals>" not in f["qualname"]), None)
        if hit:
            keys.append([hit["rel"], hit["qualname"], hit["start"]])
    executed = [[f["rel"], f["qualname"], f["start"], 1] for f in index.funcs
                if f["rel"] in {k[0] for k in keys}]
    return {"executed": executed, "stacks": [keys], "edges": [], "error": None, "static": True}


# Curated spines where the automatic one misses the point: click / typer return the callback's None,
# so the value path cannot follow `invoke`; jinja2's from_string passes compile()'s result as an
# argument, so the value path skips the compile pipeline. e2_spines.json (same shape) overrides these.
DEFAULT_SPINES = {
    "click": {"spine_funcs": ["core.py::Command.main", "core.py::Command.make_context",
                              "core.py::Command.invoke", "core.py::Context.invoke"]},
    "typer": {"spine_funcs": ["main.py::run", "main.py::Typer.__call__", "core.py::TyperCommand.main",
                              "core.py::_main", "_click/core.py::Command.invoke",
                              "_click/core.py::Context.invoke", "main.py::get_callback.<locals>.wrapper"]},
    "jinja2": {"spine_funcs": ["environment.py::Environment.from_string", "environment.py::Environment.compile",
                               "environment.py::Environment._parse", "environment.py::Environment._generate",
                               "environment.py::Environment._compile", "environment.py::Template.from_code",
                               "environment.py::Template.render"]},
}


def spine_overrides() -> dict:
    out = {cb: {**v, "source": "curated"} for cb, v in DEFAULT_SPINES.items()}
    if SPINE_OVERRIDES.exists():
        for cb, v in json.loads(SPINE_OVERRIDES.read_text(encoding="utf-8")).items():
            out[cb] = {**v, "source": "file"}
    return out


def gold_for(codebase: str, root: Path, index: PackageIndex | None = None, cache: Path | None = GOLD_CACHE,
             spec: dict | None = None, seed_hash: str | None = None) -> dict:
    """E2 gold of a codebase's seed (cached by seed hash): executed functions with spans, the
    automatic spine (union of the deepest stack under each top-level package call), and its files."""
    index = index or PackageIndex(root, codebase)
    seed_hash = seed_hash or tree_hash_of(read_tree(root))
    path = Path(cache) / f"{codebase}-{seed_hash[:12]}.json" if cache else None
    if path is not None and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if codebase == "s15_harness":
        raw = static_gold_s15(index)
    else:
        raw = trace_gold(root, spec or E2_SPECS[codebase])
    executed = []
    for rel, qual, first, count in raw["executed"]:
        f = index.span_of(rel, qual, first)
        executed.append({"rel": rel, "qualname": qual, "start": f["start"] if f else first,
                         "end": f["end"] if f else first, "count": count, "matched": f is not None})
    deepest = list(dict.fromkeys(tuple(k[:2]) for stack in raw["stacks"] for k in stack))
    spine_keys = value_spine(raw["nodes"]) if raw.get("nodes") else deepest
    mode = "value" if raw.get("nodes") else ("static" if raw.get("static") else "deepest")
    spine_files = list(dict.fromkeys(rel for rel, _ in spine_keys))
    override = spine_overrides().get(codebase)
    if override:
        ran = {(e["rel"], e["qualname"]) for e in executed}
        wanted = [tuple(s.split("::", 1)) for s in override.get("spine_funcs", [])]
        kept = [k for k in wanted if k in ran or raw.get("static")]
        if kept:
            spine_keys = kept
            spine_files = override.get("spine_files") or list(dict.fromkeys(r for r, _ in kept))
            mode = override.get("source", "override")
        override = {"source": override.get("source"), "dropped": ["::".join(k) for k in wanted
                                                                   if k not in kept]}
    gold = {"codebase": codebase, "seed_hash": seed_hash, "spec": spec or E2_SPECS.get(codebase),
            "executed": executed, "spine_funcs": [list(k) for k in spine_keys],
            "spine_files": spine_files, "spine_mode": mode,
            "spine_deepest": [list(k) for k in deepest], "error": raw.get("error"),
            "static": bool(raw.get("static")), "override": override or None}
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(gold, indent=1), encoding="utf-8")
    return gold


def grade_e2(index: PackageIndex, answer: str, gold: dict) -> dict:
    executed = gold["executed"]
    exec_files = {e["rel"] for e in executed}
    spans = defaultdict(list)
    for e in executed:
        spans[e["rel"]].append((e["start"], e["end"]))
        for f in index.defs_of(e["rel"], e["qualname"]):              # its @overload stubs too
            spans[e["rel"]].append((f["start"], f["end"]))
    # class-level lines of classes that own an executed method also count as on the path
    for c in index.classes:
        if any(e["rel"] == c["rel"] and e["qualname"].startswith(c["qualname"] + ".") for e in executed):
            spans[c["rel"]].append((c["start"], c["def_line"]))
    cites = index.citations(answer)
    on_path = [c for c in cites if c["valid"] and any(
        s <= c["line"] <= e for rel in c["valid_rels"] for s, e in spans.get(rel, ()))]
    cite_precision = len(on_path) / len(cites) if cites else 0.0
    named = index.named_files(answer) | {r for c in cites if c["valid"] for r in c["valid_rels"]}
    named_pkg = {r for r in named if r in index.texts}
    file_precision = len(named_pkg & exec_files) / len(named_pkg) if named_pkg else 0.0
    spine_files = gold["spine_files"]
    file_recall = sum(f in named for f in spine_files) / len(spine_files) if spine_files else 0.0
    words = set(re.findall(r"[A-Za-z_][\w]*", answer or ""))
    func_hits = []
    for rel, qual in (tuple(k) for k in gold["spine_funcs"]):
        defs = index.defs_of(rel, qual)
        cited_in = any(rel in c["valid_rels"] and d["start"] <= c["line"] <= d["end"]
                       for d in defs for c in cites)
        name = qual.rsplit(".", 1)[-1]
        func_hits.append(bool(cited_in or (name in words and rel in named)))
    func_recall = sum(func_hits) / len(func_hits) if func_hits else 0.0
    level = "function" if len(spine_files) <= 2 else "file"
    recall = func_recall if level == "function" else file_recall
    precision = min(cite_precision, file_precision)
    ok = recall >= THRESH["E2_recall"] and precision >= THRESH["E2_precision"]
    return {"score": round(partial(recall, recall * precision), 4), "pass": ok,
            "details": {"score_strict": round(recall * precision, 4),
                        "level": level, "recall": round(recall, 4), "file_recall": round(file_recall, 4),
                        "func_recall": round(func_recall, 4), "precision": round(precision, 4),
                        "cite_precision": round(cite_precision, 4),
                        "file_precision": round(file_precision, 4), "citations": len(cites),
                        "spine_files": spine_files, "spine_funcs": len(gold["spine_funcs"]),
                        "named_files": sorted(named_pkg)[:40], "gold_error": gold.get("error"),
                        "gold_static": gold.get("static"), "gold_override": gold.get("override")}}


# =================================================================================================
# MODIFY graders
# =================================================================================================

def parse_or_none(text: str | None) -> ast.Module | None:
    if text is None:
        return None
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def same_ast(a: ast.AST | None, b: ast.AST | None) -> bool:
    return a is not None and b is not None and ast.dump(a) == ast.dump(b)


def is_test_file(name: str) -> bool:
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _is_build_tag(node) -> bool:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
    else:
        return False
    return (len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == "BUILD_TAG"
            and isinstance(node.value, ast.Constant) and node.value.value == "profiling")


def grade_m1(seed_tree: dict, final_tree: dict, answer: str = "") -> dict:
    G = sorted(rel for rel in seed_tree if "/" not in rel and rel.endswith(".py")
               and rel != "__init__.py" and not is_test_file(rel))
    changes = changed_files(seed_tree, final_tree)
    allowed = set(G) | {"CHANGES_BUILD_TAG.md"}
    collateral = sorted(rel for rel in changes if rel not in allowed or
                        (rel in G and changes[rel] != "modified"))
    tagged, tag_lines, broken = [], {}, []
    for rel in G:
        tree_ = parse_or_none(text_of(final_tree.get(rel)))
        seed_ast = parse_or_none(text_of(seed_tree.get(rel)))
        if tree_ is None:
            if rel in changes:
                broken.append(rel)
            continue
        body = tree_.body
        first_def = next((i for i, n in enumerate(body)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))), len(body))
        tags = [i for i, n in enumerate(body) if _is_build_tag(n)]
        if len(tags) == 1 and tags[0] < first_def:
            stripped = ast.Module(body=[n for i, n in enumerate(body) if i != tags[0]],
                                  type_ignores=tree_.type_ignores)
            if same_ast(stripped, seed_ast):
                tagged.append(rel)
                tag_lines[rel] = body[tags[0]].lineno
                continue
        if rel in changes:
            broken.append(rel)                             # changed, but not just a BUILD_TAG line
    collateral = sorted(set(collateral) | set(broken))
    log = text_of(final_tree.get("CHANGES_BUILD_TAG.md")) or ""
    listed = {}
    for rel in G:
        name = re.escape(rel)
        hit = re.search(rf"(?<![\w/.-]){name}\b[^\n]*", log) or \
            re.search(rf"(?<![\w.-]){re.escape(Path(rel).name)}\b[^\n]*", log)
        if hit:
            nums = [int(n) for n in re.findall(r"(?:line\s*|:|L)(\d+)", hit.group(0), re.I)] or \
                [int(n) for n in re.findall(r"\b(\d+)\b", hit.group(0))]
            listed[rel] = nums
    log_ok = [rel for rel in G if rel in tag_lines and rel in listed and
              any(abs(n - tag_lines[rel]) <= TAG_LINE_TOLERANCE for n in listed[rel])]
    coverage = len(tagged) / len(G) if G else 0.0
    log_acc = len(log_ok) / len(G) if G else 0.0
    strict = 0.0 if collateral else coverage * log_acc
    score = 0.0 if collateral else partial(coverage, coverage * log_acc)
    ok = not collateral and len(tagged) == len(G) and len(log_ok) == len(G)
    return {"score": round(score, 4), "pass": ok,
            "details": {"score_strict": round(strict, 4),
                        "G": len(G), "tagged": len(tagged), "changelog": bool(log),
                        "changelog_ok": len(log_ok), "coverage": round(coverage, 4),
                        "changelog_accuracy": round(log_acc, 4), "collateral": collateral[:20],
                        "untagged": [r for r in G if r not in tagged][:20]}}


# -- M2: rename sites resolved by AST ---------------------------------------------------------------

def module_of(rel: str, pkg: str) -> tuple[str, bool]:
    parts = rel[:-3].split("/")
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    name = ".".join(([pkg] if pkg else []) + parts)
    return name, is_init


def absolute_from(node: ast.ImportFrom, mod: str, is_init: bool) -> str | None:
    if node.level == 0:
        return node.module
    base = mod.split(".") if is_init else mod.split(".")[:-1]
    if node.level - 1 > len(base):
        return None
    base = base[:len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _bound_names(func) -> set[str]:
    """Parameters and names stored in a function body (nested scopes excluded)."""
    a = func.args
    names = {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    stack = list(func.body) if not isinstance(func, ast.Lambda) else [func.body]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((x.asname or x.name).split(".")[0] for x in node.names)
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            names.difference_update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names


def rename_sites(texts: dict[str, str], pkg: str, def_file: str, f: str) -> dict:
    """{rel: (site walk-indices, other walk-indices)}: occurrences of identifier `f` that refer to the
    module-level function `f` of `def_file` (sites) and those that do not (others)."""
    trees = {rel: parse_or_none(t) for rel, t in texts.items() if rel.endswith(".py")}
    mods = {rel: module_of(rel, pkg) for rel in trees}
    target_mod = mods.get(def_file, module_of(def_file, pkg))[0]
    exporters, changed = {target_mod}, True
    while changed:                                            # modules re-exporting f (e.g. __init__)
        changed = False
        for rel, tree_ in trees.items():
            if tree_ is None or mods[rel][0] in exporters:
                continue
            for node in tree_.body:
                if isinstance(node, ast.ImportFrom) and absolute_from(node, *mods[rel]) in exporters \
                        and any(a.name in (f, "*") for a in node.names):
                    exporters.add(mods[rel][0])
                    changed = True
    out = {}
    for rel, tree_ in trees.items():
        if tree_ is None:
            continue
        mod, is_init = mods[rel]
        local_f = mod in exporters                            # f is bound at this module's top level
        mod_aliases: set[str] = set()
        for node in ast.walk(tree_):
            if isinstance(node, ast.ImportFrom):
                src = absolute_from(node, mod, is_init)
                for a in node.names:
                    if src in exporters and a.name in (f, "*") and (a.asname in (None, f)):
                        local_f = True
                    if src is not None and f"{src}.{a.name}" in exporters:
                        mod_aliases.add(a.asname or a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in exporters:
                        mod_aliases.add(a.asname or a.name)
        index = {id(n): i for i, n in enumerate(ast.walk(tree_))}
        sites, others = [], []

        def visit(node, shadowed: bool, depth_func: int, depth_class: int):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == f:
                    (sites if (mod == target_mod and depth_func == 0 and depth_class == 0) else others) \
                        .append(index[id(node)])
                for child in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
                    if child is not None:
                        visit(child, shadowed, depth_func, depth_class)
                for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                            node.args.vararg, node.args.kwarg]:
                    if arg is not None and arg.arg == f:
                        others.append(index[id(arg)])
                inner = shadowed or f in _bound_names(node)
                for child in node.body:
                    visit(child, inner, depth_func + 1, depth_class)
                return
            if isinstance(node, ast.Lambda):
                inner = shadowed or f in _bound_names(node)
                visit(node.body, inner, depth_func + 1, depth_class)
                return
            if isinstance(node, ast.ClassDef):
                if node.name == f:
                    others.append(index[id(node)])
                for child in [*node.decorator_list, *node.bases, *node.keywords]:
                    visit(child, shadowed, depth_func, depth_class)
                for child in node.body:
                    visit(child, shadowed, depth_func, depth_class + 1)
                return
            if isinstance(node, ast.Name) and node.id == f:
                (sites if (local_f and not shadowed) else others).append(index[id(node)])
            elif isinstance(node, ast.Attribute) and node.attr == f:
                base = dotted(node.value)
                (sites if (base in mod_aliases or base in exporters) else others).append(index[id(node)])
            elif isinstance(node, ast.alias) and node.name == f:
                parent_src = getattr(node, "_src", None)
                (sites if parent_src in exporters else others).append(index[id(node)])
            elif isinstance(node, ast.keyword) and node.arg == f:
                others.append(index[id(node)])
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and mod in exporters:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets) and node.value:
                    for elt in ast.walk(node.value):
                        if isinstance(elt, ast.Constant) and elt.value == f:
                            sites.append(index[id(elt)])
            if isinstance(node, ast.ImportFrom):
                src = absolute_from(node, mod, is_init)
                for a in node.names:
                    a._src = src
            for child in ast.iter_child_nodes(node):
                visit(child, shadowed, depth_func, depth_class)

        visit(tree_, False, 0, 0)
        if sites or others:
            out[rel] = (sorted(set(sites)), sorted(set(others)))
    return out


def _ident(node) -> str | None:
    for attr in ("name", "id", "attr", "arg"):
        value = getattr(node, attr, None)
        if isinstance(value, str):
            return value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def import_check(final_tree: dict[str, bytes], pkg: str, changed: list[str], timeout: float = 120.0) -> dict:
    """The package imports from its final tree (scripts: changed files compile)."""
    with tempfile.TemporaryDirectory(prefix="m2import-") as tmp:
        if not pkg:
            bad = []
            for rel in changed:
                if rel.endswith(".py") and rel in final_tree:
                    try:
                        compile(final_tree[rel].decode("utf-8", errors="replace"), rel, "exec")
                    except SyntaxError as exc:
                        bad.append(f"{rel}: {exc}")
            return {"ok": not bad, "error": "; ".join(bad)[:500] or None, "mode": "compile"}
        materialize(final_tree, Path(tmp) / pkg)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=tmp)
        proc = subprocess.run([PYTHON, "-c", f"import {pkg}"], capture_output=True, text=True,
                              timeout=timeout, env=env, cwd=tmp)
        return {"ok": proc.returncode == 0, "error": proc.stderr.strip().splitlines()[-1][:300]
                if proc.returncode else None, "mode": "import"}


def grade_m2(seed_tree: dict, final_tree: dict, target: dict, pkg: str, answer: str = "") -> dict:
    f, new = target["func"], target["func"] + "_v2"
    seed_texts = {rel: text_of(b) for rel, b in seed_tree.items() if rel.endswith(".py")}
    sites = rename_sites(seed_texts, pkg, target["def_file"], f)
    total = sum(len(s) for s, _ in sites.values())
    changes = changed_files(seed_tree, final_tree)
    collateral, renamed, over = [], 0, []
    for rel, kind in changes.items():
        if kind != "modified" or not rel.endswith(".py"):
            collateral.append(rel)
            continue
        final_text = text_of(final_tree[rel])
        back = re.sub(rf"\b{re.escape(new)}\b", f, final_text)
        if not same_ast(parse_or_none(back), parse_or_none(seed_texts.get(rel))):
            collateral.append(rel)
    for rel, (site_idx, other_idx) in sites.items():
        final_ast = parse_or_none(text_of(final_tree.get(rel)))
        seed_ast = parse_or_none(seed_texts.get(rel))
        if final_ast is None or seed_ast is None or rel in collateral:
            continue
        seed_nodes, final_nodes = list(ast.walk(seed_ast)), list(ast.walk(final_ast))
        if len(seed_nodes) != len(final_nodes):
            collateral.append(rel)
            continue
        renamed += sum(_ident(final_nodes[i]) == new for i in site_idx)
        over += [f"{rel}:{getattr(final_nodes[i], 'lineno', '?')}" for i in other_idx
                 if _ident(final_nodes[i]) == new]
    collateral = sorted(set(collateral))
    if over:
        collateral.append("renamed-other-entities")
    imp = import_check(final_tree, pkg, sorted(changes)) if changes else {"ok": True, "error": None,
                                                                          "mode": "unchanged"}
    coverage = renamed / total if total else 0.0
    score = 0.0 if collateral else coverage
    ok = not collateral and total > 0 and renamed == total and imp["ok"]
    return {"score": round(score, 4), "pass": ok,
            "details": {"sites": total, "renamed": renamed, "coverage": round(coverage, 4),
                        "files_with_sites": len([r for r, (s, _) in sites.items() if s]),
                        "collateral": collateral[:20], "over_renamed": over[:20],
                        "import_ok": imp["ok"], "import_error": imp["error"], "check": imp["mode"],
                        "changed": sorted(changes)[:30]}}


# -- M3: docstrings -----------------------------------------------------------------------------------

def _def_keys(tree_: ast.Module) -> list[tuple[tuple, ast.AST]]:
    """(qualname, occurrence) -> node for module, classes and functions, in source order."""
    seen: Counter = Counter()
    out = [(("<module>", 0), tree_)]

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                key = (qual, seen[qual])
                seen[qual] += 1
                out.append((key, child))
                visit(child, f"{qual}.<locals>." if not isinstance(child, ast.ClassDef) else f"{qual}.")
            else:
                visit(child, prefix)
    visit(tree_, "")
    return out


class _StripNoOps(ast.NodeTransformer):
    """Remove docstrings, `pass` and bare-constant statements (no behaviour) from every body."""

    def generic_visit(self, node):
        super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
                setattr(node, field, [s for s in body if not (
                    isinstance(s, ast.Pass) or
                    (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)))])
        return node


def behaviour_dump(text: str | None) -> str | None:
    tree_ = parse_or_none(text)
    return None if tree_ is None else ast.dump(_StripNoOps().visit(tree_))


def grade_m3(seed_tree: dict, final_tree: dict, target: dict, answer: str = "") -> dict:
    rel = target["module"]
    seed_ast = parse_or_none(text_of(seed_tree.get(rel)))
    final_text = text_of(final_tree.get(rel))
    final_ast = parse_or_none(final_text)
    changes = changed_files(seed_tree, final_tree)
    collateral = []
    for other, kind in changes.items():
        if other == rel:
            continue
        if kind == "modified" and other.endswith(".py") and same_ast(
                parse_or_none(text_of(seed_tree[other])), parse_or_none(text_of(final_tree[other]))):
            continue                                          # whitespace / comments only
        collateral.append(other)
    behaviour_same = final_ast is not None and \
        behaviour_dump(text_of(seed_tree.get(rel))) == behaviour_dump(final_text)
    if seed_ast is None:
        return {"score": 0.0, "pass": False, "details": {"error": f"seed module {rel} does not parse"}}
    seed_defs = dict(_def_keys(seed_ast))
    final_defs = dict(_def_keys(final_ast)) if final_ast is not None else {}
    required = [k for k, n in seed_defs.items()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(n) is None
                and not is_overload_stub(n)]                  # nobody documents typing.overload stubs
    covered, one_line = [], 0
    for k in required:
        node = final_defs.get(k)
        doc = ast.get_docstring(node) if node is not None else None
        if doc and doc.strip():
            covered.append(k)
            one_line += "\n" not in doc.strip()
    existing_changed = [k[0] for k, n in seed_defs.items()
                        if ast.get_docstring(n) is not None and
                        (k not in final_defs or ast.get_docstring(final_defs[k]) != ast.get_docstring(n))]
    coverage = len(covered) / len(required) if required else 0.0
    bad = collateral or not behaviour_same
    score = 0.0 if bad else coverage
    ok = not bad and bool(required) and len(covered) == len(required) and not existing_changed
    names = set(re.findall(r"[A-Za-z_]\w*", answer or ""))
    listed = sum(k[0].rsplit(".", 1)[-1] in names for k in covered)
    return {"score": round(score, 4), "pass": ok,
            "details": {"required": len(required), "covered": len(covered),
                        "coverage": round(coverage, 4), "one_line": one_line,
                        "behaviour_same": behaviour_same, "existing_changed": existing_changed[:10],
                        "collateral": collateral[:20], "answer_lists": listed,
                        "missing": [k[0] for k in required if k not in covered][:30]}}


# =================================================================================================
# runs
# =================================================================================================

def package_name(codebase: str, seed: Path) -> str:
    return "" if codebase == "s15_harness" else seed.name


def find_run_files(runs_dir: Path, label: str) -> dict:
    """The run's files under RUNS/<label>/ or RUNS/ (whichever holds <label>.meta.json)."""
    for base in (runs_dir / label, runs_dir):
        if (base / f"{label}.meta.json").exists() or (base / f"{label}.answer.json").exists():
            return {"dir": base, "meta": base / f"{label}.meta.json",
                    "answer": base / f"{label}.answer.json", "diff": base / f"{label}.diff",
                    "sandbox": base / f"{label}.sandbox"}
    return {}


def final_state(row: dict, files: dict, seed_tree: dict, meta: dict) -> tuple[dict, str]:
    if files.get("sandbox") and files["sandbox"].is_dir():
        return read_tree(files["sandbox"]), "sandbox"
    if files.get("diff") and files["diff"].exists():
        text = files["diff"].read_text(encoding="utf-8", errors="replace")
        return apply_diff(seed_tree, text, sandbox_marker=row.get("sandbox"),
                          seed_prefix=meta.get("seed") or row.get("seed")), "diff"
    return dict(seed_tree), "unchanged"


_INDEX: dict[str, PackageIndex] = {}
_SEED: dict[str, tuple[dict, str]] = {}


def seed_state(seed: Path) -> tuple[dict, str]:
    key = str(seed)
    if key not in _SEED:
        tree = read_tree(seed)
        _SEED[key] = (tree, tree_hash_of(tree))
    return _SEED[key]


def index_for(codebase: str, seed: Path) -> PackageIndex:
    key = f"{codebase}:{seed}"
    if key not in _INDEX:
        _INDEX[key] = PackageIndex(seed, codebase, seed_state(seed)[0])
    return _INDEX[key]


def grade_run(row: dict, runs_dir: Path, label: str, gold_cache: Path | None = GOLD_CACHE) -> dict:
    t, cb = row["template"], row["codebase"]
    rec = {"label": label, "row": row.get("label"), "block": row.get("block"), "cell": row.get("cell"),
           "arm": row.get("arm"), "regime": row.get("regime"), "template": t, "codebase": cb}
    files = find_run_files(runs_dir, label)
    if not files:
        return {**rec, "status": "missing", "score": None, "pass": None, "error": "run not found"}
    meta = json.loads(files["meta"].read_text(encoding="utf-8")) if files["meta"].exists() else {}
    rec["status"] = meta.get("status")
    rec["arm"] = rec["arm"] or meta.get("arm")
    answer = ""
    if files["answer"].exists():
        answer = json.loads(files["answer"].read_text(encoding="utf-8")).get("answer") or ""
    seed = Path(row["seed"])
    try:
        seed_tree, seed_hash = seed_state(seed)
        if row.get("seed_hash") and row["seed_hash"] != seed_hash:
            raise RuntimeError(f"seed {seed} changed since the manifest was built "
                               f"({seed_hash[:12]} != {row['seed_hash'][:12]})")
        if W10.FAMILY[t] == "EXPLAIN":
            index = index_for(cb, seed)
            if t == "E1":
                result = grade_e1(index, answer)
            elif t == "E2":
                result = grade_e2(index, answer, gold_for(cb, seed, index, gold_cache, seed_hash=seed_hash))
            else:
                result = grade_e3(index, answer)
            source = "answer"
        else:
            final_tree, source = final_state(row, files, seed_tree, meta)
            if t == "M1":
                result = grade_m1(seed_tree, final_tree, answer)
            elif t == "M2":
                result = grade_m2(seed_tree, final_tree, row["target"], package_name(cb, seed), answer)
            else:
                result = grade_m3(seed_tree, final_tree, row["target"], answer)
        return {**rec, **result, "source": source, "answer_chars": len(answer), "error": None}
    except Exception as exc:                                  # a grading error is reported, never a 0
        return {**rec, "score": None, "pass": None, "error": f"{type(exc).__name__}: {exc}"[:500]}


def run_labels(manifest_rows: list[dict], runs_dir: Path) -> list[tuple[dict, str]]:
    """(row, run label) for every run present: per-(block, arm) rows use run_label; per-block rows
    are matched to every <label>-<arm> run of that block."""
    out = []
    for row in manifest_rows:
        if row.get("run_label"):
            out.append((row, row["run_label"]))
        else:
            for meta in sorted(runs_dir.glob(f"**/{row['label']}-*.meta.json")):
                out.append((row, meta.name[:-len(".meta.json")]))
    return out


def audit(graded: list[dict], n: int, out: Path, runs_dir: Path | None = None, seed: str = "epic16-audit"):
    rng = random.Random(seed)
    by_t = defaultdict(list)
    for g in graded:
        if g.get("score") is not None:
            by_t[g["template"]].append(g)
    picks = []
    while len(picks) < n and any(by_t.values()):
        for t in sorted(by_t):
            if by_t[t] and len(picks) < n:
                picks.append(by_t[t].pop(rng.randrange(len(by_t[t]))))
    lines = ["# Grader audit sheet", "", f"{len(picks)} runs sampled (stratified by template, seed "
             f"{seed!r}). Fill in `human` (pass / fail) and `note`; agreement = share of rows where "
             "`human` equals `pass`.", "",
             "| # | label | template | arm | score | pass | key details | human | note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, g in enumerate(picks, 1):
        keys = {k: v for k, v in (g.get("details") or {}).items()
                if not isinstance(v, (list, dict)) or k in ("collateral", "covered", "per_kind")}
        lines.append(f"| {i} | {g['label']} | {g['template']} | {g.get('arm')} | {g['score']} | "
                     f"{g['pass']} | `{json.dumps(keys)[:300]}` |  |  |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "template", "arm", "score", "pass", "details", "human", "note"])
        for g in picks:
            writer.writerow([g["label"], g["template"], g.get("arm"), g["score"], g["pass"],
                             json.dumps(g.get("details"))[:2000], "", ""])
    return picks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("grade", help="grade every run of a manifest found under --runs")
    p.add_argument("--manifest", required=True)
    p.add_argument("--runs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--gold-cache", default=str(GOLD_CACHE))
    p = sub.add_parser("audit", help="a stratified hand-audit sheet of graded runs")
    p.add_argument("--graded", required=True)
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--out", required=True)
    p = sub.add_parser("gold", help="compute (and cache) the E2 gold of codebases")
    p.add_argument("--codebase", action="append", default=[])
    p.add_argument("--gold-cache", default=str(GOLD_CACHE))
    p.add_argument("--seed-override", action="append", default=[])
    args = ap.parse_args()

    if args.cmd == "grade":
        rows = [json.loads(line) for line in Path(args.manifest).read_text(encoding="utf-8").splitlines()
                if line.strip()]
        runs_dir = Path(args.runs)
        cache = Path(args.gold_cache) if args.gold_cache else None
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        stats = Counter()
        with out.open("w", encoding="utf-8") as handle:
            for row, label in run_labels(rows, runs_dir):
                rec = grade_run(row, runs_dir, label, cache)
                stats["graded" if rec.get("score") is not None else rec.get("status") or "error"] += 1
                handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps(dict(stats)))
    elif args.cmd == "audit":
        graded = [json.loads(line) for line in Path(args.graded).read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        picks = audit(graded, args.n, Path(args.out))
        print(f"{len(picks)} runs -> {args.out}")
    elif args.cmd == "gold":
        overrides = dict(item.split("=", 1) for item in args.seed_override)
        for cb in args.codebase or list(E2_SPECS) + ["s15_harness"]:
            seed = Path(overrides.get(cb) or W10.seed_dir(cb))
            if not seed.is_dir() and W10.CODEBASES[cb][0].startswith("pip:"):
                spec = importlib.util.find_spec(W10.CODEBASES[cb][0][4:])      # sys.prefix may differ
                seed = Path(list(spec.submodule_search_locations)[0]) if spec else seed
            gold = gold_for(cb, seed, cache=Path(args.gold_cache) if args.gold_cache else None)
            print(json.dumps({"codebase": cb, "executed": len(gold["executed"]),
                              "spine_files": gold["spine_files"], "spine_funcs": len(gold["spine_funcs"]),
                              "error": gold["error"]}))


if __name__ == "__main__":
    main()
