#!/usr/bin/env python3
"""CPU tests of epic_grade.py and epic_workloads.py (pytest-compatible; or run this file directly).

    python research/16_epic_harness/test_epic_grade.py
    uv run --no-project --python /data/fritz/venv/bin/python --with pytest \
        python -m pytest research/16_epic_harness/test_epic_grade.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import epic_grade as G  # noqa: E402
import epic_workloads as EW  # noqa: E402

DIFF_EXCLUDES = ["-x", "__pycache__", "-x", "*.pyc", "-x", "*.so", "-x", "traces", "-x", "*.jsonl",
                 "-x", "*.pyi", "-x", "py.typed"]

PKG = {
    "__init__.py": '"""Demo package."""\nfrom .core import Thing, greet\nfrom .util import helper\n\n'
                   '__all__ = ["greet", "Thing", "helper"]\n',
    "core.py": '"""Core module."""\nfrom dataclasses import dataclass\n\nfrom .util import helper\n\n\n'
               'class DemoError(Exception):\n    """Raised on bad input."""\n\n\n'
               '@dataclass\nclass Thing:\n    name: str\n    size: int = 0\n\n\n'
               'def greet(name):\n    if not name:\n        raise DemoError("empty")\n'
               '    return "Hello " + name + str(helper(1))\n\n\n'
               'def _private(x):\n    return x * 2\n\n\n'
               'def stub():\n    pass\n\n\n'
               'class Box:\n    def __init__(self, value):\n        self._value = value\n\n'
               '    @property\n    def value(self):\n        return self._value\n\n'
               '    @value.setter\n    def value(self, new):\n        self._value = new\n\n'
               '    def outer(self):\n        def inner():\n            return 1\n        return inner()\n'
               '\n\nfrom typing import overload\n\n\n@overload\ndef conv(x: int) -> int: ...\n\n\n'
               '@overload\ndef conv(x: str) -> str: ...\n\n\ndef conv(x):\n    return x\n',
    "util.py": 'def helper(x):\n    return x + 1\n\n\ndef other():\n    return helper(2)\n\n\n'
               'class Holder:\n    def helper(self):\n        return 0\n',
    "cli.py": 'import sys\n\nfrom .core import greet\n\n\ndef main(argv=None):\n    print(greet("cli"))\n'
              '    return 0\n\n\nif __name__ == "__main__":\n    sys.exit(main())\n',
    "notes.py": "X = 1",                                     # no trailing newline on purpose
    "tests/test_core.py": "def test_x():\n    pass\n",
}


def make_pkg(base: Path, name: str = "demo") -> Path:
    root = base / name
    for rel, text in PKG.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def line_of(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not found")


def edit(tree: dict, rel: str, old: str, new: str, count: int = 1) -> dict:
    out = dict(tree)
    text = out[rel].decode()
    assert old in text, (rel, old)
    out[rel] = text.replace(old, new, count).encode()
    return out


def add_tags(tree: dict, files: list[str]) -> dict:
    out = dict(tree)
    for rel in files:
        text = out[rel].decode()
        lines = text.split("\n")
        # after the last top-level import (or at the top), before any def/class
        idx = 0
        for i, line in enumerate(lines):
            if line.startswith(("import ", "from ")):
                idx = i + 1
            if line.startswith(("def ", "class ", "@")):
                break
        lines.insert(idx, "BUILD_TAG = 'profiling'")
        out[rel] = "\n".join(lines).encode()
    return out


def changelog(tree: dict, files: list[str], offset: int = 0) -> bytes:
    rows = ["# BUILD_TAG changes", ""]
    for rel in files:
        n = line_of(tree[rel].decode(), "BUILD_TAG = ") + offset
        rows.append(f"- {rel}: line {n}")
    return ("\n".join(rows) + "\n").encode()


# -- trees and diffs --------------------------------------------------------------------------------

def test_diff_roundtrip_with_real_diff():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        seed = make_pkg(tmp / "seed")
        lane = tmp / "lane" / "profiling_sandbox" / "demo"
        shutil.copytree(seed, lane)
        (lane / "core.py").write_text(PKG["core.py"].replace("return x * 2", "return x * 3")
                                      .replace('"""Core module."""', '"""Core module, edited."""'))
        (lane / "notes.py").write_text("X = 2")                       # still no newline at EOF
        (lane / "util.py").write_text(PKG["util.py"] + "\n\ndef extra():\n    return 3\n")
        (lane / "NEW.md").write_text("new file\n")
        (lane / "tests" / "test_core.py").unlink()                    # a deleted file
        diff = subprocess.run(["diff", "-ruN", *DIFF_EXCLUDES, str(seed), str(lane)],
                              capture_output=True, text=True).stdout
        seed_tree = G.read_tree(seed)
        final = G.apply_diff(seed_tree, diff, sandbox_marker="profiling_sandbox/demo",
                             seed_prefix=str(seed))
        assert final == G.read_tree(lane)
        assert G.changed_files(seed_tree, final) == {
            "NEW.md": "added", "core.py": "modified", "notes.py": "modified",
            "tests/test_core.py": "deleted", "util.py": "modified"}


def test_diff_rel_from_command_line_when_marker_absent():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        seed = make_pkg(tmp / "a")
        other = tmp / "b" / "demo"
        shutil.copytree(seed, other)
        (other / "cli.py").write_text(PKG["cli.py"].replace('"cli"', '"CLI"'))
        diff = subprocess.run(["diff", "-ruN", str(seed), str(other)], capture_output=True, text=True).stdout
        final = G.apply_diff(G.read_tree(seed), diff)
        assert final == G.read_tree(other)


# -- M1 ---------------------------------------------------------------------------------------------

def m1_trees(tmp: Path):
    seed_tree = G.read_tree(make_pkg(tmp))
    G_files = ["cli.py", "core.py", "notes.py", "util.py"]
    tagged = add_tags(seed_tree, G_files)
    return seed_tree, G_files, tagged


def test_m1_pass_and_failures():
    with tempfile.TemporaryDirectory() as tmp:
        seed_tree, G_files, tagged = m1_trees(Path(tmp))
        good = {**tagged, "CHANGES_BUILD_TAG.md": changelog(tagged, G_files)}
        r = G.grade_m1(seed_tree, good)
        assert r["pass"] and r["score"] == 1.0, r
        # off by one in the changelog is tolerated, off by five is not
        assert G.grade_m1(seed_tree, {**tagged, "CHANGES_BUILD_TAG.md": changelog(tagged, G_files, 1)})["pass"]
        r = G.grade_m1(seed_tree, {**tagged, "CHANGES_BUILD_TAG.md": changelog(tagged, G_files, 5)})
        # partial credit: every file tagged, no changelog line right -> half the score (strict product 0)
        assert not r["pass"] and r["score"] == 0.5 and r["details"]["score_strict"] == 0.0 \
            and r["details"]["changelog_ok"] == 0
        # a file left untagged
        partial = add_tags(seed_tree, G_files[:3])
        r = G.grade_m1(seed_tree, {**partial, "CHANGES_BUILD_TAG.md": changelog(partial, G_files[:3])})
        assert not r["pass"] and r["details"]["tagged"] == 3 and 0 < r["score"] < 1
        # no changelog
        r = G.grade_m1(seed_tree, tagged)
        assert not r["pass"] and r["score"] == 0.5 and r["details"]["score_strict"] == 0.0
        # collateral change anywhere -> 0
        bad = edit(good, "util.py", "return x + 1", "return x + 2")
        r = G.grade_m1(seed_tree, bad)
        assert r["score"] == 0.0 and "util.py" in r["details"]["collateral"]
        bad = {**good, "__init__.py": good["__init__.py"] + b"# touched\nY = 1\n"}
        assert G.grade_m1(seed_tree, bad)["score"] == 0.0
        # the tag must precede the first def/class
        late = dict(good)
        late["util.py"] = (seed_tree["util.py"].decode() + "\nBUILD_TAG = 'profiling'\n").encode()
        r = G.grade_m1(seed_tree, late)
        assert not r["pass"] and "util.py" in r["details"]["collateral"]


# -- M2 ---------------------------------------------------------------------------------------------

TARGET = {"func": "helper", "def_file": "util.py"}


def renamed(seed_tree: dict, skip_all: bool = False, also_method: bool = False,
            only_def: bool = False) -> dict:
    t = dict(seed_tree)
    t = edit(t, "util.py", "def helper(x)", "def helper_v2(x)")
    if only_def:
        return t
    t = edit(t, "util.py", "return helper(2)", "return helper_v2(2)")
    if also_method:
        t = edit(t, "util.py", "def helper(self)", "def helper_v2(self)")
    t = edit(t, "core.py", "from .util import helper", "from .util import helper_v2")
    t = edit(t, "core.py", "str(helper(1))", "str(helper_v2(1))")
    t = edit(t, "__init__.py", "from .util import helper", "from .util import helper_v2")
    if not skip_all:
        t = edit(t, "__init__.py", '"helper"]', '"helper_v2"]')
    return t


def test_m2_sites_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        seed_tree = G.read_tree(make_pkg(Path(tmp)))
        texts = {k: v.decode() for k, v in seed_tree.items() if k.endswith(".py")}
        sites = G.rename_sites(texts, "demo", "util.py", "helper")
        n_sites = sum(len(s) for s, _ in sites.values())
        n_other = sum(len(o) for _, o in sites.values())
        # def + call in util.py; import + call in core.py; import + __all__ in __init__.py
        assert n_sites == 6, sites
        assert n_other == 1                                  # Holder.helper (a method)
        assert set(sites) == {"util.py", "core.py", "__init__.py"}


def test_m2_pass_partial_collateral_and_import():
    with tempfile.TemporaryDirectory() as tmp:
        seed_tree = G.read_tree(make_pkg(Path(tmp)))
        r = G.grade_m2(seed_tree, renamed(seed_tree), TARGET, "demo")
        assert r["pass"] and r["score"] == 1.0 and r["details"]["import_ok"], r
        r = G.grade_m2(seed_tree, renamed(seed_tree, skip_all=True), TARGET, "demo")
        assert not r["pass"] and abs(r["score"] - 5 / 6) < 1e-3, r
        r = G.grade_m2(seed_tree, renamed(seed_tree, also_method=True), TARGET, "demo")
        assert r["score"] == 0.0 and r["details"]["over_renamed"], r
        bad = edit(renamed(seed_tree), "core.py", "return x * 2", "return x * 4")
        r = G.grade_m2(seed_tree, bad, TARGET, "demo")
        assert r["score"] == 0.0 and "core.py" in r["details"]["collateral"], r
        r = G.grade_m2(seed_tree, renamed(seed_tree, only_def=True), TARGET, "demo")
        assert not r["pass"] and not r["details"]["import_ok"] and r["details"]["renamed"] == 1, r
        r = G.grade_m2(seed_tree, dict(seed_tree), TARGET, "demo")      # nothing done
        assert not r["pass"] and r["score"] == 0.0


def test_m2_script_codebase_uses_compile_check():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "scripts"
        root.mkdir()
        (root / "main.py").write_text("from helpers import tool\n\n\ndef run():\n    return tool(1)\n")
        (root / "helpers.py").write_text("def tool(x):\n    return x\n\n\ndef twice(x):\n    return tool(tool(x))\n")
        seed_tree = G.read_tree(root)
        final = {k: v.decode().replace("tool", "tool_v2").encode() for k, v in seed_tree.items()}
        r = G.grade_m2(seed_tree, final, {"func": "tool", "def_file": "helpers.py"}, "")
        assert r["pass"] and r["details"]["check"] == "compile" and r["details"]["sites"] == 5, r


# -- M3 ---------------------------------------------------------------------------------------------

DOCS = [("def greet(name):\n", 'def greet(name):\n    """Greet someone."""\n'),
        ("def _private(x):\n", 'def _private(x):\n    """Double x."""\n'),
        ("def stub():\n    pass\n", 'def stub():\n    """Do nothing."""\n'),        # pass replaced
        ("def __init__(self, value):\n", 'def __init__(self, value):\n        """Store value."""\n'),
        ("def value(self):\n", 'def value(self):\n        """The value."""\n'),
        ("def value(self, new):\n", 'def value(self, new):\n        """Set value."""\n'),
        ("def outer(self):\n", 'def outer(self):\n        """Call inner."""\n'),
        ("def inner():\n", 'def inner():\n            """One."""\n'),
        ("def conv(x):\n", 'def conv(x):\n    """Identity."""\n')]           # overload stubs need none


def documented(seed_tree: dict, skip: int | None = None) -> dict:
    t = dict(seed_tree)
    for i, (old, new) in enumerate(DOCS):
        if i != skip:
            t = edit(t, "core.py", old, new)
    return t


def test_m3_pass_and_failures():
    with tempfile.TemporaryDirectory() as tmp:
        seed_tree = G.read_tree(make_pkg(Path(tmp)))
        target = {"module": "core.py"}
        r = G.grade_m3(seed_tree, documented(seed_tree), target)
        assert r["pass"] and r["score"] == 1.0 and r["details"]["required"] == 9, r
        r = G.grade_m3(seed_tree, documented(seed_tree, skip=1), target)
        assert not r["pass"] and abs(r["score"] - 8 / 9) < 1e-3, r
        bad = edit(documented(seed_tree), "core.py", "return x * 2", "return x * 5")
        r = G.grade_m3(seed_tree, bad, target)
        assert r["score"] == 0.0 and not r["details"]["behaviour_same"], r
        bad = edit(documented(seed_tree), "core.py", '"""Raised on bad input."""', '"""Bad input."""')
        r = G.grade_m3(seed_tree, bad, target)
        assert not r["pass"] and r["score"] == 1.0 and r["details"]["existing_changed"], r
        bad = edit(documented(seed_tree), "util.py", "return x + 1", "return x + 9")
        r = G.grade_m3(seed_tree, bad, target)
        assert r["score"] == 0.0 and r["details"]["collateral"] == ["util.py"], r
        harmless = edit(documented(seed_tree), "util.py", "def other():", "def other():  # comment")
        assert G.grade_m3(seed_tree, harmless, target)["pass"]


# -- EXPLAIN ----------------------------------------------------------------------------------------

def test_e1_inventory():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_pkg(Path(tmp))
        index = G.PackageIndex(root, "demo")
        assert index.modules == ["__init__.py", "cli.py", "core.py", "notes.py", "util.py"]
        good = ("- demo/__init__.py:2 re-exports the API\n- cli.py:6 command line entry\n"
                "- core.py:17 greeting logic\n- notes.py:1 a constant\n- util.py:1 helpers\n")
        r = G.grade_e1(index, good)
        assert r["pass"] and r["score"] == 1.0, r
        r = G.grade_e1(index, "- core.py:17 greeting\n- util.py:1 helpers\n")
        assert not r["pass"] and r["details"]["recall"] == 0.4
        r = G.grade_e1(index, good.replace("util.py:1", "util.py:900"))
        assert not r["pass"] and r["details"]["validity"] == 0.8
        r = G.grade_e1(index, "Modules: `demo.core`, `demo.util`, `demo.cli`, `demo.notes`, demo/__init__.py:1")
        assert r["details"]["recall"] == 1.0 and not r["pass"]          # only one citation


def test_citation_block_formats():
    """Second audit (2026-10-09): a line reference set apart from the one path of its line or block."""
    trailing = "- `__init__.py`: Provides the entry point. (Line 1)\n- `api.py`: Sends requests. (Line 4)"
    assert G.extract_citations(trailing) == [("__init__.py", 1), ("api.py", 4)]
    block = ("1. **`__init__.py`**\n   - **Unique Contribution**: x\n   - **File/Line**: `__version__` (line 5)\n\n"
             "2. **`core.py`**\n   - **File/Line**: `class Command` (line 10)")
    assert G.extract_citations(block) == [("__init__.py", 5), ("core.py", 10)]
    assert G.extract_citations("- **File:** `__main__.py`\n- **Line Number:** 2") == [("__main__.py", 2)]
    # no attribution across a blank line, to a line naming two paths, or without a line reference
    assert G.extract_citations("- `utils.py`: helpers\n\nThe line 7 here is unrelated.") == []
    assert G.extract_citations("`a.py` and `b.py`\n- (line 3)") == []
    assert G.extract_citations("- `a.py` imports `b.py` (line 3)") == [("b.py", 3)]


def test_e3_orientation():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_pkg(Path(tmp))
        index = G.PackageIndex(root, "demo")
        core = PKG["core.py"]
        entry = line_of(core, "def greet")
        data = line_of(core, "class Thing")
        error = line_of(core, "raise DemoError")
        main = line_of(PKG["cli.py"], 'if __name__ == "__main__"') + 1
        good = (f"Entry point: core.py:{entry} (greet). CLI: cli.py:{main}. "
                f"Data: core.py:{data} (Thing). Errors: core.py:{error}.")
        r = G.grade_e3(index, good)
        assert r["pass"] and r["score"] == 1.0, r
        r = G.grade_e3(index, f"CLI: cli.py:{main}. Data: core.py:{data}.")
        assert not r["pass"] and abs(r["score"] - 2 / 3) < 1e-3, r
        # a def line within two lines of a raise also counts as error handling (the +-2 window)
        assert "error" in G.classify_citation(index, "core.py", entry)
        r = G.grade_e3(index, good + " Also core.py:999 and util.py:777.")
        assert not r["pass"] and r["details"]["validity"] < 0.8, r
        impl = index.span_of("core.py", "conv")
        assert impl["def_line"] == line_of(core, "def conv(x):") and len(index.defs_of("core.py", "conv")) == 3
        kinds = G.classify_citation(index, "core.py", line_of(core, "class DemoError"))
        assert kinds == {"error"}
        assert "data" in G.classify_citation(index, "core.py", line_of(core, "size: int = 0"))


def test_value_spine_rules():
    # nodes: [rel, qualname, first, parent, top, ret_id, ret_kind]
    nodes = [["a.py", "Api.__init__", 1, -1, 0, 1, "none"],
             ["a.py", "helper", 5, 0, 0, 2, "object"],           # not descended (constructor)
             ["a.py", "get", 10, -1, 1, 7, "object"],
             ["b.py", "prepare", 20, 2, 1, 8, "object"],         # returns something else
             ["b.py", "send", 30, 2, 1, 7, "object"],            # produced get's result
             ["b.py", "hook", 40, 2, 1, 7, "object"],            # passes it through later
             ["c.py", "build", 50, 4, 1, 7, "object"],
             ["c.py", "Response.__init__", 60, 6, 1, 9, "none"]]
    spine = G.value_spine(nodes)
    assert spine == [("a.py", "Api.__init__"), ("a.py", "get"), ("b.py", "send"), ("c.py", "build")]


def test_e2_traced_gold_and_grading():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_pkg(Path(tmp))
        index = G.PackageIndex(root, "demo")
        spec = {"setup": "import demo.core as c", "call": "c.Box(3).outer()"}
        gold = G.gold_for("demo", root, index, cache=None, spec=spec)
        ran = {(e["rel"], e["qualname"]) for e in gold["executed"]}
        assert ("core.py", "Box.outer") in ran and ("core.py", "Box.outer.<locals>.inner") in ran
        assert gold["spine_funcs"] == [["core.py", "Box.__init__"], ["core.py", "Box.outer"],
                                       ["core.py", "Box.outer.<locals>.inner"]], gold["spine_funcs"]
        core = PKG["core.py"]
        good = (f"Box.__init__ at core.py:{line_of(core, 'def __init__')} stores the value; "
                f"Box.outer at core.py:{line_of(core, 'def outer')} calls inner "
                f"(core.py:{line_of(core, 'def inner')}) which returns 1.")
        r = G.grade_e2(index, good, gold)
        assert r["pass"] and r["score"] == 1.0 and r["details"]["level"] == "function", r
        bad = (f"It starts in cli.py:{line_of(PKG['cli.py'], 'def main')}, then util.py:1, then "
               f"core.py:{line_of(core, 'def _private')}.")
        r = G.grade_e2(index, bad, gold)
        assert not r["pass"] and r["details"]["precision"] < 0.8, r
        # the real tracer on the greet path crosses into util.py
        gold2 = G.gold_for("demo", root, index, cache=None,
                           spec={"setup": "import demo", "call": "demo.greet('x')"})
        assert {("core.py", "greet"), ("util.py", "helper")} <= {(e["rel"], e["qualname"])
                                                                for e in gold2["executed"]}


# -- runs -------------------------------------------------------------------------------------------

def test_grade_run_from_run_files_and_audit():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        seed = make_pkg(tmp / "seed")
        seed_tree = G.read_tree(seed)
        runs = tmp / "runs"
        # an M2 run that kept only a diff, an M1 run that kept the sandbox, an E1 run (answer only)
        lane = tmp / "lane" / "profiling_sandbox" / "demo"
        G.materialize(renamed(seed_tree), lane)
        diff = subprocess.run(["diff", "-ruN", *DIFF_EXCLUDES, str(seed), str(lane)],
                              capture_output=True, text=True).stdout
        rows = []
        for label, template, target in (("R-m2-epic64", "M2", TARGET), ("R-m1-prefix", "M1", None),
                                        ("R-e1-shift", "E1", None)):
            d = runs / label
            d.mkdir(parents=True)
            (d / f"{label}.meta.json").write_text(json.dumps({"label": label, "status": "ok",
                                                              "seed": str(seed)}))
            answer = "- demo/__init__.py:1\n- cli.py:1\n- core.py:1\n- notes.py:1\n- util.py:1\n" \
                if template == "E1" else "done"
            (d / f"{label}.answer.json").write_text(json.dumps({"answer": answer}))
            if template == "M2":
                (d / f"{label}.diff").write_text(diff)
            if template == "M1":
                tagged = add_tags(seed_tree, ["cli.py", "core.py", "notes.py", "util.py"])
                tagged["CHANGES_BUILD_TAG.md"] = changelog(tagged, ["cli.py", "core.py", "notes.py", "util.py"])
                G.materialize(tagged, d / f"{label}.sandbox")
            rows.append({"label": label.rsplit("-", 1)[0], "run_label": label, "arm": label.rsplit("-", 1)[1],
                         "template": template, "codebase": "demo", "seed": str(seed),
                         "seed_hash": G.tree_hash_of(seed_tree), "sandbox": "profiling_sandbox/demo",
                         "target": target, "cell": f"demo-{template}"})
        graded = [G.grade_run(row, runs, label) for row, label in G.run_labels(rows, runs)]
        by = {g["label"]: g for g in graded}
        assert by["R-m2-epic64"]["pass"] and by["R-m2-epic64"]["source"] == "diff", by["R-m2-epic64"]
        assert by["R-m1-prefix"]["pass"] and by["R-m1-prefix"]["source"] == "sandbox", by["R-m1-prefix"]
        assert by["R-e1-shift"]["pass"], by["R-e1-shift"]
        # a seed that changed after the manifest was built is an error, not a score
        stale = {**rows[0], "seed_hash": "0" * 64}
        assert G.grade_run(stale, runs, "R-m2-epic64")["score"] is None
        out = tmp / "audit.md"
        picks = G.audit(graded, 2, out)
        assert len(picks) == 2 and out.exists() and out.with_suffix(".csv").exists()
        assert G.grade_run(rows[0], runs, "missing-label")["status"] == "missing"


# -- workloads: selection and designs ----------------------------------------------------------------

CODEBASES = list(EW.W10.CODEBASES)


def fake_cells() -> list[dict]:
    cells = []
    for i, cb in enumerate(CODEBASES):
        for t in EW.TEMPLATES:
            target = {"func": f"f{i}", "def_file": "x.py"} if t == "M2" else \
                {"module": "m.py", "missing": 9} if t == "M3" else None
            cells.append({"codebase": cb, "seed": f"/seed/{cb}", "version": "1", "seed_hash": f"h{i}",
                          "sandbox": f"profiling_sandbox/{cb}", "template": t, "family": EW.FAMILY[t],
                          "target": target, "cell": EW.cell_id(cb, t, target),
                          "context_proxy": (i + 1) * 1000 + EW.TEMPLATES.index(t)})
    return cells


def test_select_cells_constraints():
    cells = fake_cells()
    rates = {c["cell"]: 0.1 + 0.8 * ((hash(c["cell"]) % 97) / 97) for c in cells}
    sel = EW.select_cells(cells, rates)
    chosen = [c for c in cells if c["cell"] in sel["r1"]]
    assert len(chosen) == 32
    per_t = Counter(c["template"] for c in chosen)
    assert all(5 <= per_t[t] <= 6 for t in EW.TEMPLATES), per_t
    assert len({c["codebase"] for c in chosen}) >= 6
    assert all(0.15 <= rates[c["cell"]] <= 0.90 for c in chosen)
    assert sel == EW.select_cells(cells, rates)                          # reproducible
    # five codebases closest to 0.5 fill every template's first five slots; covering eight
    # codebases then needs swaps
    near = {cb: 0.5 + 0.03 * i for i, cb in enumerate(CODEBASES[:5])}
    skewed = {c["cell"]: near.get(c["codebase"], 0.89) for c in cells}
    sel2 = EW.select_cells(cells, skewed, min_codebases=8)
    chosen2 = [c for c in cells if c["cell"] in sel2["r1"]]
    assert len({c["codebase"] for c in chosen2}) >= 8 and sel2["report"]["swaps"], sel2["report"]
    assert len(chosen2) == 32
    r2 = EW.select_r2(cells, pool=sel["r1"], n=12)
    assert len(r2) == 12 and set(r2) <= set(sel["r1"])


def design_rows(phase: str, seed: str = "epic16"):
    cells = fake_cells()
    if phase == "pilot":
        sel = EW.pilot_cells(cells)
    else:
        rates = {c["cell"]: 0.5 for c in cells}
        sel = EW.select_cells(cells, rates)
        sel["r2"] = EW.select_r2(cells, pool=sel["r1"], n=12)
    return EW.build_manifest(phase, cells, sel, seed=seed)


def test_main_design_counts_crn_and_lanes():
    rows, meta = design_rows("main")
    arms = meta["summary"]["arms"]
    assert arms["R1"] == {"epic128": 96, "epic32": 96, "epic64": 96, "prefix": 96, "prefix_aa": 24,
                          "shift": 32}, arms
    assert arms["R2"] == {"epic128": 12, "epic32": 12, "prefix": 12}, arms
    blocks = {}
    for r in rows:
        blocks.setdefault(r["block"], []).append(r)
    assert len(blocks) == 108
    for rs in blocks.values():
        assert len({r["nonce"] for r in rs}) == 1 and len({r["lane"] for r in rs}) == 1
        assert len({r["prompt"] for r in rs}) == 1 and len({r["run_label"] for r in rs}) == len(rs)
    # shift: one block per R1 cell; prefix_aa: 4 cells per template, on a repeat other than shift's
    r1 = [r for r in rows if r["regime"] == "R1"]
    shift_cells = Counter(r["cell"] for r in r1 if r["arm"] == "shift")
    assert len(shift_cells) == 32 and set(shift_cells.values()) == {1}
    aa = [r for r in r1 if r["arm"] == "prefix_aa"]
    assert Counter(r["template"] for r in aa) == Counter({t: 4 for t in EW.TEMPLATES})
    shift_block = {r["cell"]: r["block"] for r in r1 if r["arm"] == "shift"}
    assert all(r["block"] != shift_block[r["cell"]] for r in aa)
    repeats = Counter(r["repeat"] for r in r1 if r["arm"] == "shift")
    assert max(repeats.values()) - min(repeats.values()) <= 1
    lanes = Counter(r["lane"] for r in rows)
    assert abs(lanes[0] - lanes[1]) <= 6
    # reproducible from the seed, different under another seed
    again, _ = design_rows("main")
    assert [r["run_label"] for r in again] == [r["run_label"] for r in rows]
    other, _ = design_rows("main", seed="other")
    assert [r["run_label"] for r in other] != [r["run_label"] for r in rows]


def test_pilot_design_and_separate_nonces():
    rows, meta = design_rows("pilot")
    arms = meta["summary"]["arms"]
    assert arms["R1"] == {"epic128": 12, "prefix": 12, "prefix_aa": 12, "shift": 12}, arms
    assert arms["R2"] == {"epic128": 8, "prefix": 8}, arms
    r1_cells = {r["cell"] for r in rows if r["regime"] == "R1"}
    assert Counter(r["template"] for r in rows if r["regime"] == "R1" and r["arm"] == "prefix") == \
        Counter({t: 2 for t in EW.TEMPLATES})
    assert len({r["codebase"] for r in rows if r["regime"] == "R1"}) >= 6
    main_rows, _ = design_rows("main")
    main_nonce = {(r["cell"], r["regime"]): r["nonce"] for r in main_rows if r["repeat"] == 1}
    for r in rows:
        if (r["cell"], r["regime"]) in main_nonce:
            assert r["nonce"] != main_nonce[(r["cell"], r["regime"])]
    assert all(r["label"].startswith("EHP-") for r in rows) and r1_cells
    EW.check(rows)


def test_seed_tree_hash_ignores_caches():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_pkg(Path(tmp))
        h = EW.tree_hash(root)
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
        (root / "trace.jsonl").write_text("{}\n")
        assert EW.tree_hash(root) == h == G.tree_hash_of(G.read_tree(root))
        (root / "util.py").write_text(PKG["util.py"] + "# changed\n")
        assert EW.tree_hash(root) != h


def test_enumerate_cells_on_real_seeds():
    """The 60 real cells (10 codebases x 6 templates) with unambiguous M2 targets where possible."""
    cells = EW.enumerate_cells()
    assert len(cells) == 60 and len({c["cell"] for c in cells}) == 60
    for c in cells:
        assert len(c["seed_hash"]) == 64
        if c["template"] == "M2":
            assert c["target"]["sites"] >= 2, c
        if c["template"] == "M3":
            assert (Path(c["seed"]) / c["target"]["module"]).is_file()
    prompts = EW.rows_for(EW.schedule(EW.design("pilot", [cells[0]["cell"]], [], cells=cells)), cells)
    assert prompts[0]["prompt"].startswith(f"[run {prompts[0]['nonce']}]")


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception:                                   # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print(f"{failures} failed" if failures else "all passed")
    sys.exit(1 if failures else 0)
