#!/usr/bin/env python3
"""Build the 500-run manifest of real code-explanation and fake-modification workloads.

Ten real codebases: nine widely used packages copied from this venv's site-packages (requests,
click, jinja2, rich, httpx, yaml, packaging, tqdm, typer) plus this repository's own harness
(`s15_integrated_harness/`). Six task templates:

  EXPLAIN (code explanation, read-only), adapted from 08_context_inheritance's EXPLAIN items
    E1 inventory    08 item 1: inventory every module, one line each on what it alone provides
    E2 trace        08 item 2: trace one call from the public interface to the code doing the work
    E3 orientation  08 item 4: an orientation note citing file:line for entry points / data / errors
  MODIFY (fake modification: runs edit a sandbox copy that is thrown away after the run)
    M1 build_tag    08 MODIFY items 1 + 4: add BUILD_TAG to every module, then a changelog
    M2 rename       census REFACTOR-style: rename one function everywhere (auto-picked, 3-10 refs)
    M3 docstrings   add a one-line docstring to every undocumented function of one module

Adaptations of the 08 texts (their target was a PyMTL hardware lab, run by a 3-teammate board):
"arithmetic that produces the result" -> "code that produces the result", "every design module" ->
"every module"; single-root packages drop "and under {b}"; every prompt is solo ("Work alone: ...")
with one item and an 08-style run nonce. The 08 source strings are read with `ast` (not imported: the module imports inherit_analyze) and
recorded in the manifest header so the provenance is checkable.

10 codebases x 6 templates = 60 cells; 8 repeats each plus a 9th for 20 cells = 500 runs. Repeats
differ by nonce and by the server's sampling (the harness sets no temperature), and M2/M3 rotate
across up to three auto-picked targets.

    python research/10_rope_shift/workloads.py build [--runs 500]   # writes data/rope_shift_live/manifest.jsonl
    python research/10_rope_shift/workloads.py --check                # validate prompts, print a summary
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data" / "rope_shift_live"
SITE = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
SANDBOX_ROOT = "profiling_sandbox"          # gitignored; lives inside each lane
CODEBASE_08 = REPO / "research" / "08_context_inheritance" / "codebase_workloads.py"

# name -> (seed directory, distribution name for the version, E2 entry point)
CODEBASES = {
    "requests": ("pip:requests", "requests", "requests.get(url)"),
    "click": ("pip:click", "click", "a @click.command() callback being invoked through Command.main()"),
    "jinja2": ("pip:jinja2", "Jinja2", "Environment().from_string(source).render(**context)"),
    "rich": ("pip:rich", "rich", "Console().print(renderable)"),
    "httpx": ("pip:httpx", "httpx", "httpx.get(url)"),
    "yaml": ("pip:yaml", "PyYAML", "yaml.safe_load(stream)"),
    "packaging": ("pip:packaging", "packaging", "SpecifierSet('>=1.0').contains('1.2')"),
    "tqdm": ("pip:tqdm", "tqdm", "iterating over tqdm(iterable)"),
    "typer": ("pip:typer", "typer", "typer.run(main)"),
    "s15_harness": ("repo:s15_integrated_harness", None,
                    "agent_loop(messages, context, active_request) for one tool round"),
}
SEED_IGNORE = ("__pycache__", "*.pyc", "traces", "*.jsonl", "*.so", "*.pyi", "py.typed")

TEMPLATES = ["E1", "E2", "E3", "M1", "M2", "M3"]
FAMILY = {"E1": "EXPLAIN", "E2": "EXPLAIN", "E3": "EXPLAIN", "M1": "MODIFY", "M2": "MODIFY", "M3": "MODIFY"}
SOLO = "Work alone: do not delegate to teammates or subagents, and do not create tasks."
NOWRITE = " Do not create or modify any files."
FORBIDDEN = re.compile(r"\b(teammate|spawn|delegate to a team|task board)\b", re.I)


def seed_dir(name: str) -> Path:
    where = CODEBASES[name][0]
    kind, rel = where.split(":", 1)
    return (SITE / rel) if kind == "pip" else (REPO / rel)


def version_of(name: str) -> str:
    dist = CODEBASES[name][1]
    if dist is None:
        return "repo"
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def items_08() -> dict[str, list[str]]:
    """EXPLAIN_ITEMS / MODIFY_ITEMS from 08's codebase_workloads.py, read without importing it."""
    tree = ast.parse(CODEBASE_08.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("EXPLAIN_ITEMS", "MODIFY_ITEMS"):
                out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


def py_files(root: Path) -> list[Path]:
    skip = {"__pycache__", "traces", "tests", "test"}
    return sorted(p for p in root.rglob("*.py") if not (set(p.relative_to(root).parts) & skip))


def rename_candidates(root: Path, k: int = 3) -> list[dict]:
    """Module-level functions referenced 3-10 times in total across at least 2 files."""
    files = py_files(root)
    texts = {p: p.read_text(encoding="utf-8", errors="ignore") for p in files}
    defs: dict[str, Path] = {}
    for path, text in texts.items():
        for m in re.finditer(r"^def ([a-z][a-z0-9_]{3,})\(", text, re.M):
            defs.setdefault(m.group(1), path)
    cands = []
    for name, path in sorted(defs.items()):
        per_file = {p: len(re.findall(rf"\b{re.escape(name)}\b", t)) for p, t in texts.items()}
        total = sum(per_file.values())
        nfiles = sum(1 for v in per_file.values() if v)
        if 3 <= total <= 10 and nfiles >= 2:
            cands.append({"func": name, "def_file": str(path.relative_to(root)), "refs": total,
                          "files": nfiles})
    rng = random.Random(f"10rope-rename-{root.name}")
    rng.shuffle(cands)
    return cands[:k]


def docstring_candidates(root: Path, k: int = 3) -> list[dict]:
    """Modules with the most functions lacking a docstring (5..60 such functions, <= 2500 lines)."""
    out = []
    for path in py_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if text.count("\n") > 2500:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        missing = [n.name for n in funcs if ast.get_docstring(n) is None]
        if 5 <= len(missing) <= 60:
            out.append({"module": str(path.relative_to(root)), "missing": len(missing)})
    out.sort(key=lambda d: (-d["missing"], d["module"]))
    return out[:k]


def build_prompt(template: str, codebase: str, nonce: str, target: dict | None, src08: dict) -> str:
    root = f"{SANDBOX_ROOT}/{codebase}"
    entry = CODEBASES[codebase][2]
    ex, mo = src08["EXPLAIN_ITEMS"], src08["MODIFY_ITEMS"]
    if template == "E1":
        item = (ex[0].replace(" and under {b}", "").replace("every design module", "every module")
                .replace("{a}", f"{root}/") + ", citing a file and line for each module")
    elif template == "E2":
        item = (ex[1].replace("Trace one request through {a}", f"Trace one call of {entry} through {root}/")
                .replace("the arithmetic that produces the result", "the code that produces the result")
                + ", with line numbers")
    elif template == "E3":
        item = ("Write an orientation note for someone joining this codebase at " + root + "/ that cites "
                "a specific file and line number for its main entry points, its core data structures "
                "and its error handling")
        assert ex[3].startswith("Write an orientation note for someone joining this repository")
    elif template == "M1":
        item = (mo[0].replace("{a}", f"{root}/") + ". Then write a changelog file " + root
                + "/CHANGES_BUILD_TAG.md listing every file changed and the line number where the "
                "constant was added in each")
    elif template == "M2":
        item = (f"Rename the function {target['func']} (defined in {root}/{target['def_file']}) to "
                f"{target['func']}_v2 everywhere under {root}/: its definition and every call site or "
                "reference. Then list every file and line you changed")
    elif template == "M3":
        item = (f"Add a one-line docstring to every function and method in {root}/{target['module']} "
                "that does not have one. Do not change any behaviour. Then list the functions you "
                "documented")
    else:
        raise ValueError(template)
    tail = NOWRITE if FAMILY[template] == "EXPLAIN" else f" Only modify files under {root}/."
    return f"[run {nonce}] {item}. {SOLO}{tail}"


def build(runs: int) -> list[dict]:
    src08 = items_08()
    assert len(src08.get("EXPLAIN_ITEMS", [])) == 4 and len(src08.get("MODIFY_ITEMS", [])) == 4
    targets = {}
    for cb in CODEBASES:
        root = seed_dir(cb)
        assert root.is_dir(), f"missing seed {root}"
        targets[cb] = {"M2": rename_candidates(root), "M3": docstring_candidates(root)}
        assert targets[cb]["M2"], f"{cb}: no rename candidate"
        assert targets[cb]["M3"], f"{cb}: no docstring candidate"
    cells = [(cb, t) for cb in CODEBASES for t in TEMPLATES]
    base, extra = divmod(runs, len(cells))
    rng = random.Random("10rope-manifest")
    bonus = set(rng.sample(range(len(cells)), extra))
    rows = []
    for ci, (cb, t) in enumerate(cells):
        for rep in range(1, base + 1 + (ci in bonus)):
            label = f"RS-{cb}-{t}-r{rep}"
            nonce = hashlib.sha1(label.encode()).hexdigest()[:8]
            target = None
            if t in ("M2", "M3"):
                pool = targets[cb][t]
                target = pool[(rep - 1) % len(pool)]
            rows.append({
                "label": label, "codebase": cb, "template": t, "family": FAMILY[t], "repeat": rep,
                "nonce": nonce, "seed": str(seed_dir(cb)), "version": version_of(cb),
                "sandbox": f"{SANDBOX_ROOT}/{cb}", "target": target,
                "prompt": build_prompt(t, cb, nonce, target, src08),
            })
    rng.shuffle(rows)                       # interleave cells so any shard slice is a balanced mix
    for i, row in enumerate(rows):
        row["index"] = i
    return rows


def check(rows: list[dict]) -> dict:
    bad = [r["label"] for r in rows if FORBIDDEN.search(r["prompt"].replace(SOLO, ""))]
    assert not bad, f"prompts mention team words outside the solo preamble: {bad[:5]}"
    assert len({r["label"] for r in rows}) == len(rows), "duplicate labels"
    for r in rows:
        assert r["prompt"].count("[run ") == 1 and len(r["prompt"]) < 1200, r["label"]
    return {"runs": len(rows), "per_template": dict(Counter(r["template"] for r in rows)),
            "per_codebase": dict(Counter(r["codebase"] for r in rows)),
            "versions": {cb: version_of(cb) for cb in CODEBASES}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "show"])
    ap.add_argument("--runs", type=int, default=500)
    ap.add_argument("--out", default=str(DATA / "manifest.jsonl"))
    ap.add_argument("--check", action="store_true", help="validate prompts and print a summary only")
    args = ap.parse_args()
    rows = build(args.runs)
    summary = check(rows)
    if args.check or args.cmd == "show":
        print(json.dumps(summary, indent=2))
        for t in TEMPLATES:
            example = next(r for r in rows if r["template"] == t)
            print(f"\n[{t}] {example['label']}\n{example['prompt']}")
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out.parent / "manifest_meta.json").write_text(json.dumps(
        {**summary, "source_08": str(CODEBASE_08.relative_to(REPO)), "items_08": items_08()},
        indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
