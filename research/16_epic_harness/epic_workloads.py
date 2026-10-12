#!/usr/bin/env python3
"""Workloads of the EPIC study: cells, paired block designs and manifests (offline, CPU).

Rebuilds topic 10's code-explanation / fake-modification workloads (`10_rope_shift/workloads.py`:
ten real codebases x six templates, the same prompt texts) on THIS machine and lays them out as the
paired designs of plan §3.6 / §5:

  cells    the 60 cells (codebase x template) with ONE fixed M2 / M3 target each (a seeded choice
           among topic 10's auto-picked candidates, M2 candidates validated on NAME tokens), each
           seed's version and a SHA-256 tree hash of exactly what a run copies into its sandbox
  select   from pilot base rates: the 32 R1 cells (5-6 per template, >= 6 codebases, prefix success
           in [0.15, 0.90], nearest 0.5 first) and R2's 12 largest-context cells
  build    the manifest of one phase, one row per (block, arm):
             pilot  R1: 12 cells x {prefix, prefix_aa, shift, epic128}; R2: 8 cells x {prefix, epic128}
             main   R1: 32 cells x 3 repeats = 96 blocks x {prefix, epic32, epic64, epic128},
                    shift on 32 blocks (one per cell), prefix_aa on 24 (4 per template);
                    R2: 12 blocks x {prefix, epic32, epic128}
           A block is (phase, regime, cell, repeat). All arms of a block share its nonce (so the
           prompt bytes and the server's per-call seeds are identical: common random numbers) and
           its lane (lane affinity: identical sandbox paths). Block order and the arm order inside a
           block are randomized from --seed; blocks are dealt to `--lanes` lanes so the lanes carry
           equal numbers of runs and two concurrent sessions always come from different blocks.
  check    validate a manifest (prompts, CRN, lane affinity, arm counts)

Row fields: topic 10's schema (label, codebase, template, family, repeat, nonce, seed, version,
sandbox, target, prompt, index) where `label` is the BLOCK label (epic_session.py appends the arm),
plus phase, regime, block, cell, arm, run_label (= label-arm), seed_hash, lane, seq.

    python research/16_epic_harness/epic_workloads.py cells --out CELLS.json
    python research/16_epic_harness/epic_workloads.py build --phase pilot --out DIR/manifest_pilot.jsonl
    python research/16_epic_harness/epic_workloads.py select --cells CELLS.json --base-rates RATES.json \
        [--context-sizes SIZES.json] --out SELECTION.json
    python research/16_epic_harness/epic_workloads.py build --phase main --selection SELECTION.json \
        --out DIR/manifest_main.jsonl
    python research/16_epic_harness/epic_workloads.py check --manifest DIR/manifest_main.jsonl

`--seed-override CODEBASE=PATH` (repeatable) points a codebase at another seed tree, e.g. a frozen
copy of s15_integrated_harness while that folder is being edited.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

os.environ.setdefault("HF_HOME", "/data/shared/huggingface")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import workloads as W10  # noqa: E402  (10_rope_shift: CODEBASES, prompts, candidates)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = HERE / "data"
TEMPLATES = W10.TEMPLATES
FAMILY = W10.FAMILY
ARMS = ("prefix", "prefix_aa", "shift", "epic32", "epic64", "epic128")
REGIMES = ("R1", "R2")
# what live_run / epic_session copy into a sandbox (shutil.ignore_patterns(*SEED_IGNORE))
IGNORE = tuple(W10.SEED_IGNORE)
PILOT = {"R1": ("prefix", "prefix_aa", "shift", "epic128"), "R2": ("prefix", "epic128")}
MAIN_CORE = {"R1": ("prefix", "epic32", "epic64", "epic128"), "R2": ("prefix", "epic32", "epic128")}
# rough share of a codebase a template's run reads (only used to rank cells by context before a
# pilot has measured them)
CONTEXT_FACTOR = {"E1": 1.0, "M1": 1.0, "E3": 0.7, "E2": 0.5, "M2": 0.5, "M3": 0.3}


# -- seeds ------------------------------------------------------------------------------------------

def ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORE)


def seed_files(root: Path) -> list[Path]:
    """Every file a run's sandbox receives (shutil.copytree with live_run's SEED_IGNORE)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not ignored(d))
        for name in sorted(filenames):
            if not ignored(name):
                out.append(Path(dirpath) / name)
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def tree_hash(root: Path) -> str:
    """SHA-256 over (relative path, bytes) of every file the sandbox receives, in sorted order."""
    digest = hashlib.sha256()
    for path in seed_files(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def seed_root(codebase: str, overrides: dict[str, str] | None = None) -> Path:
    """The seed tree: an override, topic 10's sys.prefix-based path, else wherever Python imports
    the package from (sys.prefix differs under e.g. `uv run --with`)."""
    if (overrides or {}).get(codebase):
        return Path(overrides[codebase])
    root = Path(W10.seed_dir(codebase))
    kind, rel = W10.CODEBASES[codebase][0].split(":", 1)
    if not root.is_dir() and kind == "pip":
        spec = importlib.util.find_spec(rel)
        if spec is not None and spec.submodule_search_locations:
            return Path(list(spec.submodule_search_locations)[0])
    return root


def version_of(codebase: str, root: Path) -> str:
    if W10.CODEBASES[codebase][1] is None:          # the repository's own harness
        return "repo"
    return W10.version_of(codebase)


# -- targets ----------------------------------------------------------------------------------------

def m2_check(texts: dict[str, str], pkg: str, cand: dict) -> dict:
    """Rename sites of a candidate as epic_grade resolves them (AST: the def, imports of it, uses of
    the imported name, module-qualified attributes, __all__), and same-named other identifiers."""
    from epic_grade import rename_sites
    func = cand["func"]
    clash = any(re.search(rf"\b{re.escape(func)}_v2\b", t) for t in texts.values())
    sites = rename_sites(texts, pkg, cand["def_file"], func)
    n_sites = sum(len(s) for s, _ in sites.values())
    n_files = sum(1 for s, _ in sites.values() if s)
    n_other = sum(len(o) for _, o in sites.values())
    return {"sites": n_sites, "site_files": n_files, "others": n_other, "clash": clash,
            "valid": n_sites >= 3 and n_files >= 2 and n_other == 0 and not clash}


def pick_targets(codebase: str, root: Path) -> dict:
    """One fixed M2 and M3 target per codebase: a seeded choice among topic 10's first three
    candidates. M2 candidates must be unambiguous for the grader (>= 3 resolved sites in >= 2 files,
    no same-named method / parameter / attribute, `<f>_v2` unused)."""
    from epic_grade import package_name, read_tree, text_of
    texts = {rel: text_of(b) for rel, b in read_tree(root).items() if rel.endswith(".py")}
    pkg = package_name(codebase, root)
    m2_pool, checked = [], []
    for cand in W10.rename_candidates(root, k=1000):
        k = m2_check(texts, pkg, cand)
        checked.append((cand, k))
        if k["valid"]:
            m2_pool.append({**cand, "sites": k["sites"]})
            if len(m2_pool) == 3:
                break
    if not m2_pool and checked:                     # fallback: the least ambiguous candidate
        c, k = min(checked, key=lambda ck: (ck[1]["clash"], ck[1]["others"], -ck[1]["sites"]))
        m2_pool = [{**c, "sites": k["sites"], "fallback": True, "others": k["others"],
                    "site_files": k["site_files"]}]
    m3_pool = W10.docstring_candidates(root, k=3)
    assert m2_pool, f"{codebase}: no rename candidate"
    assert m3_pool, f"{codebase}: no docstring candidate"
    m2 = random.Random(f"epic16-m2-{codebase}").choice(m2_pool)
    m3 = random.Random(f"epic16-m3-{codebase}").choice(m3_pool)
    return {"M2": dict(m2), "M3": dict(m3)}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text.replace(".py", "")).strip("_")


def cell_id(codebase: str, template: str, target: dict | None) -> str:
    if template == "M2":
        return f"{codebase}-{template}-{target['func']}"
    if template == "M3":
        return f"{codebase}-{template}-{slug(target['module'])}"
    return f"{codebase}-{template}"


def enumerate_cells(overrides: dict[str, str] | None = None) -> list[dict]:
    """The 60 cells (10 codebases x 6 templates) with fixed targets and seed provenance."""
    cells = []
    for cb in W10.CODEBASES:
        root = seed_root(cb, overrides)
        assert root.is_dir(), f"missing seed {root}"
        targets = pick_targets(cb, root)
        files = W10.py_files(root)
        py_chars = sum(p.stat().st_size for p in files)
        info = {"codebase": cb, "seed": str(root), "version": version_of(cb, root),
                "seed_hash": tree_hash(root), "sandbox": f"{W10.SANDBOX_ROOT}/{cb}",
                "py_files": len(files), "py_chars": py_chars}
        for t in TEMPLATES:
            target = targets.get(t)
            proxy = py_chars * CONTEXT_FACTOR[t]
            if t == "M3":
                proxy = (root / target["module"]).stat().st_size * 3
            cells.append({**info, "cell": cell_id(cb, t, target), "template": t, "family": FAMILY[t],
                          "target": target, "context_proxy": int(proxy)})
    return cells


# -- cell selection ---------------------------------------------------------------------------------

def select_cells(cells: list[dict], base_rates: dict[str, float], n: int = 32, per_min: int = 5,
                 per_max: int = 6, min_codebases: int = 6, lo: float = 0.15, hi: float = 0.90,
                 seed: str = "epic16-select") -> dict:
    """The R1 cells: prefix success in [lo, hi], `per_min`..`per_max` per template, nearest 0.5
    first (most informative for a paired comparison), then swaps for >= `min_codebases`."""
    rng = random.Random(seed)
    eligible = [c for c in cells if c["cell"] in base_rates and lo <= base_rates[c["cell"]] <= hi]
    by_t: dict[str, list[dict]] = defaultdict(list)
    for c in eligible:
        by_t[c["template"]].append(c)
    for t in by_t:
        rng.shuffle(by_t[t])                                     # seeded tie-break
        by_t[t].sort(key=lambda c: abs(base_rates[c["cell"]] - 0.5))
    chosen = {t: list(by_t[t][:per_min]) for t in TEMPLATES}
    spare = sorted(((abs(base_rates[c["cell"]] - 0.5), TEMPLATES.index(t), c["cell"], t, c)
                    for t in TEMPLATES for c in by_t[t][per_min:per_max]), key=lambda x: x[:3])
    for _, _, _, t, c in spare:
        if sum(len(v) for v in chosen.values()) >= n:
            break
        if len(chosen[t]) < per_max:
            chosen[t].append(c)

    def codebases() -> Counter:
        return Counter(c["codebase"] for v in chosen.values() for c in v)

    swaps = []
    while len(codebases()) < min_codebases:
        covered = codebases()
        best = None
        for t in TEMPLATES:
            picked = {c["cell"] for c in chosen[t]}
            outs = [c for c in by_t[t] if c["cell"] not in picked and c["codebase"] not in covered]
            ins = [c for c in chosen[t] if covered[c["codebase"]] >= 2]
            if outs and ins:
                drop = max(ins, key=lambda c: abs(base_rates[c["cell"]] - 0.5))
                add = outs[0]
                score = abs(base_rates[add["cell"]] - 0.5)
                if best is None or score < best[0]:
                    best = (score, t, drop, add)
        if best is None:
            break
        _, t, drop, add = best
        chosen[t] = [add if c["cell"] == drop["cell"] else c for c in chosen[t]]
        swaps.append({"template": t, "out": drop["cell"], "in": add["cell"]})
    selected = [c["cell"] for t in TEMPLATES for c in chosen[t]]
    report = {"eligible": len(eligible), "selected": len(selected),
              "per_template": {t: len(chosen[t]) for t in TEMPLATES},
              "codebases": len(codebases()), "swaps": swaps,
              "short": max(0, n - len(selected)),
              "missing_rates": sorted(c["cell"] for c in cells if c["cell"] not in base_rates)}
    return {"r1": selected, "report": report}


def select_from_profile(cells: list[dict], profile: dict, n: int = 32, per_min: int = 3, per_max: int = 7,
                        min_edits: float = 5.0, max_cap: float = 0.5, lo: float = 0.05, hi: float = 0.95,
                        seed: str = "epic16-select") -> dict:
    """The main sweep's R1 cells from measured prefix runs (pilot + screen; epic_analyze profile). A cell is
    eligible when its runs compact (median edit calls >= min_edits: EPIC acts only on edited calls), are not
    bound by the call cap (cap rate <= max_cap) and have an outcome that can move (mean score in [lo, hi],
    or mixed pass/fail). Per template `per_min`..`per_max` cells, mean score nearest 0.5 first, then the
    cheaper (mean wall time); the report gives the measured cost of one prefix run per selected cell."""
    rng = random.Random(seed)

    def informative(v: dict) -> bool:
        return lo <= v["score_mean"] <= hi or 0 < v["pass_mean"] < 1

    eligible = [c for c in cells if c["cell"] in profile and profile[c["cell"]]["edit_calls_med"] >= min_edits
                and profile[c["cell"]]["cap_rate"] <= max_cap and informative(profile[c["cell"]])]
    by_t: dict[str, list[dict]] = defaultdict(list)
    for c in eligible:
        by_t[c["template"]].append(c)

    def rank(c):
        v = profile[c["cell"]]
        return (abs(v["score_mean"] - 0.5), v["wall_mean"])

    for t in by_t:
        rng.shuffle(by_t[t])                                     # seeded tie-break
        by_t[t].sort(key=rank)
    chosen = {t: list(by_t[t][:per_min]) for t in TEMPLATES}
    spare = sorted(((*rank(c), c["cell"], t, c) for t in TEMPLATES for c in by_t[t][per_min:per_max]),
                   key=lambda x: x[:3])
    for *_, t, c in spare:
        if sum(len(v) for v in chosen.values()) >= n:
            break
        if len(chosen[t]) < per_max:
            chosen[t].append(c)
    selected = [c["cell"] for t in TEMPLATES for c in chosen[t]]
    report = {"eligible": len(eligible), "selected": len(selected),
              "per_template": {t: len(chosen[t]) for t in TEMPLATES},
              "codebases": len({c["codebase"] for v in chosen.values() for c in v}),
              "prefix_wall_s": round(sum(profile[cid]["wall_mean"] for cid in selected)),
              "criteria": {"min_edits": min_edits, "max_cap": max_cap, "score": [lo, hi]},
              "excluded": {cid: {k: v for k, v in prof.items() if k in ("score_mean", "pass_mean", "cap_rate",
                                                                      "edit_calls_med")}
                           for cid, prof in profile.items() if cid not in {c["cell"] for c in eligible}}}
    return {"r1": selected, "report": report}


def select_r2(cells: list[dict], pool: list[str] | None = None, context_sizes: dict | None = None,
              n: int = 12) -> list[str]:
    """R2's cells: the `n` cells with the largest contexts (measured sizes, else the proxy)."""
    by_id = {c["cell"]: c for c in cells}
    ids = list(pool) if pool else list(by_id)
    size = (lambda cid: float(context_sizes.get(cid, -1))) if context_sizes \
        else (lambda cid: float(by_id[cid]["context_proxy"]))
    return sorted(ids, key=lambda cid: (-size(cid), cid))[:n]


def pilot_cells(cells: list[dict], n_r1: int = 12, n_r2: int = 8, seed: str = "epic16-pilot") -> dict:
    """Pilot cells before any base rate exists: R1 = 2 per template spread over the codebases;
    R2 = the largest context proxies."""
    rng = random.Random(seed)
    per = max(1, n_r1 // len(TEMPLATES))
    used: Counter = Counter()
    r1 = []
    for t in TEMPLATES:
        pool = [c for c in cells if c["template"] == t]
        rng.shuffle(pool)
        for _ in range(per):
            pool.sort(key=lambda c: used[c["codebase"]])
            pick = pool.pop(0)
            used[pick["codebase"]] += 1
            r1.append(pick["cell"])
    return {"r1": r1[:n_r1], "r2": select_r2(cells, n=n_r2)}


# -- designs ----------------------------------------------------------------------------------------

def block_nonce(phase: str, regime: str, cell: str, repeat: int) -> str:
    return hashlib.sha1(f"epic16|{phase}|{regime}|{cell}|r{repeat}".encode()).hexdigest()[:8]


def design(phase: str, r1: list[str], r2: list[str], repeats: int | None = None,
           n_shift: int | None = None, n_aa: int | None = None, cells: list[dict] | None = None,
           seed: str = "epic16", shift_reps: int = 1, aa_reps: int = 1) -> list[dict]:
    """Blocks with their arms (no prompts yet): [{phase, regime, cell, repeat, arms}]."""
    tmpl = {c["cell"]: c["template"] for c in cells or []}
    template_of = (lambda cid: tmpl.get(cid) or cid.split("-")[1])
    blocks = []
    if phase == "pilot":
        for cid in r1:
            blocks.append({"phase": phase, "regime": "R1", "cell": cid, "repeat": 1,
                           "arms": list(PILOT["R1"])})
        for cid in r2:
            blocks.append({"phase": phase, "regime": "R2", "cell": cid, "repeat": 1,
                           "arms": list(PILOT["R2"])})
        return blocks
    if phase != "main":
        raise ValueError(phase)
    repeats = repeats or 3
    n_shift = len(r1) if n_shift is None else n_shift
    n_aa = round(0.75 * len(r1)) if n_aa is None else n_aa
    rng = random.Random(f"{seed}-alloc")
    # shift: one block per cell (repeats rotate so every repeat index carries shift equally often)
    order = list(r1)
    rng.shuffle(order)
    order.sort(key=lambda cid: TEMPLATES.index(template_of(cid)))       # stratify by template
    shift_rep = {cid: 1 + i % repeats for i, cid in enumerate(order)}
    shift_cells = set(order[:n_shift]) if n_shift < len(order) else set(order)
    # prefix_aa: n_aa cells drawn round-robin over templates, on a repeat other than shift's
    by_t: dict[str, list[str]] = defaultdict(list)
    for cid in order:
        by_t[template_of(cid)].append(cid)
    aa_cells: list[str] = []
    while len(aa_cells) < min(n_aa, len(r1)):
        progressed = False
        for t in TEMPLATES:
            if by_t[t] and len(aa_cells) < n_aa:
                aa_cells.append(by_t[t].pop(0))
                progressed = True
        if not progressed:
            break
    # shift on `shift_reps` consecutive repeats from the cell's start, prefix_aa on the `aa_reps` after them
    shift_set = {cid: {1 + (shift_rep[cid] - 1 + j) % repeats for j in range(shift_reps)} for cid in shift_cells}
    aa_set = {cid: {1 + (shift_rep[cid] - 1 + shift_reps + j) % repeats for j in range(aa_reps)} for cid in aa_cells}
    for cid in r1:
        for rep in range(1, repeats + 1):
            arms = list(MAIN_CORE["R1"])
            if rep in shift_set.get(cid, ()):
                arms.append("shift")
            if rep in aa_set.get(cid, ()):
                arms.append("prefix_aa")
            blocks.append({"phase": phase, "regime": "R1", "cell": cid, "repeat": rep, "arms": arms})
    for cid in r2:
        blocks.append({"phase": phase, "regime": "R2", "cell": cid, "repeat": 1,
                       "arms": list(MAIN_CORE["R2"])})
    return blocks


def schedule(blocks: list[dict], lanes: int = 2, seed: str = "epic16") -> list[dict]:
    """Randomize block order and arm order within blocks; deal blocks to lanes (lane affinity),
    keeping the lanes' run counts balanced. Returns one dict per (block, arm) with lane and seq."""
    rng = random.Random(f"{seed}-schedule")
    order = sorted(blocks, key=lambda b: (b["phase"], b["regime"], b["cell"], b["repeat"]))
    rng.shuffle(order)
    load, seq = [0] * lanes, [0] * lanes
    out = []
    for b in order:
        arms = list(b["arms"])
        rng.shuffle(arms)
        lane = min(range(lanes), key=lambda i: (load[i], i))
        for arm in arms:
            out.append({**{k: v for k, v in b.items() if k != "arms"}, "arm": arm, "lane": lane,
                        "seq": seq[lane]})
            seq[lane] += 1
        load[lane] += len(arms)
    # global order: lanes interleaved by their sequence number
    out.sort(key=lambda r: (r["seq"], r["lane"]))
    for i, r in enumerate(out):
        r["index"] = i
    return out


def block_label(phase: str, regime: str, cell: str, repeat: int) -> str:
    return f"EH{'P' if phase == 'pilot' else 'M'}-{regime}-{cell}-r{repeat}"


def rows_for(scheduled: list[dict], cells: list[dict]) -> list[dict]:
    """Manifest rows: topic 10's schema plus the design fields; prompts built by topic 10's code."""
    by_id = {c["cell"]: c for c in cells}
    src08 = W10.items_08()
    rows = []
    for s in scheduled:
        c = by_id[s["cell"]]
        nonce = block_nonce(s["phase"], s["regime"], s["cell"], s["repeat"])
        label = block_label(s["phase"], s["regime"], s["cell"], s["repeat"])
        rows.append({
            "label": label, "run_label": f"{label}-{s['arm']}", "phase": s["phase"],
            "regime": s["regime"], "block": f"{s['phase']}:{s['regime']}:{s['cell']}:r{s['repeat']}",
            "cell": s["cell"], "codebase": c["codebase"], "template": c["template"],
            "family": c["family"], "repeat": s["repeat"], "arm": s["arm"], "nonce": nonce,
            "seed": c["seed"], "seed_hash": c["seed_hash"], "version": c["version"],
            "sandbox": c["sandbox"], "target": c["target"],
            "prompt": W10.build_prompt(c["template"], c["codebase"], nonce, c["target"], src08),
            "lane": s["lane"], "seq": s["seq"], "index": s["index"]})
    return rows


def check(rows: list[dict]) -> dict:
    """Validate a manifest; raises AssertionError on the first problem, else returns a summary."""
    bad = [r["run_label"] for r in rows if W10.FORBIDDEN.search(r["prompt"].replace(W10.SOLO, ""))]
    assert not bad, f"prompts mention team words outside the solo preamble: {bad[:5]}"
    assert len({r["run_label"] for r in rows}) == len(rows), "duplicate run labels"
    for r in rows:
        assert r["prompt"].count("[run ") == 1 and len(r["prompt"]) < 1200, r["run_label"]
        assert r["arm"] in ARMS and r["regime"] in REGIMES, r["run_label"]
        assert f"[run {r['nonce']}]" in r["prompt"], r["run_label"]
    per_block: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        per_block[r["block"]].append(r)
    for block, rs in per_block.items():
        assert len({r["arm"] for r in rs}) == len(rs), f"{block}: repeated arm"
        for key in ("nonce", "lane", "prompt", "label", "seed_hash", "cell", "regime"):
            assert len({json.dumps(r[key]) for r in rs}) == 1, f"{block}: {key} differs across arms"
        seqs = sorted(r["seq"] for r in rs)
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), f"{block}: arms not contiguous"
    lane_runs = Counter(r["lane"] for r in rows)
    biggest = max((len(rs) for rs in per_block.values()), default=0)
    assert not lane_runs or max(lane_runs.values()) - min(lane_runs.values()) <= biggest, \
        f"lanes unbalanced: {dict(lane_runs)}"
    return {"runs": len(rows), "blocks": len(per_block),
            "arms": {reg: dict(sorted(Counter(r["arm"] for r in rows if r["regime"] == reg).items()))
                     for reg in REGIMES},
            "cells": len({r["cell"] for r in rows}),
            "per_template": dict(Counter(r["template"] for r in rows)),
            "codebases": dict(Counter(r["codebase"] for r in rows)),
            "lanes": dict(sorted(lane_runs.items()))}


# -- I/O --------------------------------------------------------------------------------------------

def load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_rows(path: str | Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_overrides(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items or []:
        name, _, path = item.partition("=")
        if name not in W10.CODEBASES or not path:
            raise SystemExit(f"bad --seed-override {item!r} (want CODEBASE=PATH)")
        out[name] = str(Path(path).resolve())
    return out


def build_manifest(phase: str, cells: list[dict], selection: dict | None = None, repeats: int | None = None,
                   lanes: int = 2, seed: str = "epic16", n_shift: int | None = None,
                   n_aa: int | None = None, shift_reps: int = 1, aa_reps: int = 1) -> tuple[list[dict], dict]:
    if phase == "pilot":
        sel = selection or pilot_cells(cells)
    else:
        if not selection:
            raise SystemExit("--phase main needs --selection (from `select`)")
        sel = selection
    known = {c["cell"] for c in cells}
    missing = [cid for cid in [*sel["r1"], *sel.get("r2", [])] if cid not in known]
    assert not missing, f"selection names unknown cells: {missing}"
    blocks = design(phase, sel["r1"], sel.get("r2", []), repeats=repeats, n_shift=n_shift,
                    n_aa=n_aa, cells=cells, seed=seed, shift_reps=shift_reps, aa_reps=aa_reps)
    rows = rows_for(schedule(blocks, lanes=lanes, seed=f"{seed}-{phase}"), cells)
    summary = check(rows)
    meta = {"phase": phase, "seed": seed, "lanes": lanes, "repeats": repeats, "shift_reps": shift_reps,
            "aa_reps": aa_reps,
            "selection": {"r1": sel["r1"], "r2": sel.get("r2", [])}, "summary": summary,
            "codebases": {c["codebase"]: {"seed": c["seed"], "version": c["version"],
                                          "seed_hash": c["seed_hash"]} for c in cells},
            "source_10": "research/10_rope_shift/workloads.py",
            "source_08": str(W10.CODEBASE_08.relative_to(REPO))}
    return rows, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cells", help="the 60 cells with fixed targets and seed hashes")
    p.add_argument("--out", default=None)
    p.add_argument("--seed-override", action="append", default=[])
    p = sub.add_parser("select", help="R1/R2 cells from pilot base rates")
    p.add_argument("--cells", required=True, help="JSON from `cells`")
    p.add_argument("--base-rates", required=True, help="JSON {cell: prefix success rate}")
    p.add_argument("--context-sizes", default=None, help="JSON {cell: mean prompt tokens}")
    p.add_argument("--n", type=int, default=32)
    p.add_argument("--n-r2", type=int, default=12)
    p.add_argument("--out", default=None)
    p = sub.add_parser("select-profile", help="R1 cells from measured prefix runs (epic_analyze profile)")
    p.add_argument("--cells", required=True, help="JSON from `cells`")
    p.add_argument("--profile", required=True, help="JSON from `epic_analyze.py profile`")
    p.add_argument("--r2", default=None, help="comma-separated R2 cells (default: largest contexts)")
    p.add_argument("--n", type=int, default=32)
    p.add_argument("--n-r2", type=int, default=12)
    p.add_argument("--min-edits", type=float, default=5.0)
    p.add_argument("--max-cap", type=float, default=0.5)
    p.add_argument("--out", default=None)
    p = sub.add_parser("build", help="a phase's manifest")
    p.add_argument("--phase", required=True, choices=["pilot", "main"])
    p.add_argument("--cells", default=None, help="JSON from `cells` (default: enumerate now)")
    p.add_argument("--selection", default=None, help="JSON from `select` (main) or {r1, r2} lists")
    p.add_argument("--repeats", type=int, default=None)
    p.add_argument("--n-shift", type=int, default=None)
    p.add_argument("--n-aa", type=int, default=None)
    p.add_argument("--shift-reps", type=int, default=1, help="repeats per cell that also run shift")
    p.add_argument("--aa-reps", type=int, default=1, help="repeats per cell that also run prefix_aa")
    p.add_argument("--lanes", type=int, default=2)
    p.add_argument("--seed", default="epic16")
    p.add_argument("--seed-override", action="append", default=[])
    p.add_argument("--out", default=None, help="manifest JSONL (default data/manifest_<phase>.jsonl)")
    p.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    p = sub.add_parser("check", help="validate a manifest")
    p.add_argument("--manifest", required=True)
    args = ap.parse_args()

    if args.cmd == "cells":
        cells = enumerate_cells(parse_overrides(args.seed_override))
        text = json.dumps(cells, indent=1, ensure_ascii=False)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"{len(cells)} cells -> {args.out}")
        else:
            print(text)
    elif args.cmd == "select":
        cells = load_json(args.cells)
        rates = {k: float(v) for k, v in load_json(args.base_rates).items()}
        sizes = load_json(args.context_sizes) if args.context_sizes else None
        sel = select_cells(cells, rates, n=args.n)
        sel["r2"] = select_r2(cells, pool=sel["r1"], context_sizes=sizes, n=args.n_r2)
        text = json.dumps(sel, indent=1)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
    elif args.cmd == "select-profile":
        cells = load_json(args.cells)
        sel = select_from_profile(cells, load_json(args.profile), n=args.n, min_edits=args.min_edits,
                                  max_cap=args.max_cap)
        sel["r2"] = args.r2.split(",") if args.r2 else select_r2(cells, n=args.n_r2)
        text = json.dumps(sel, indent=1)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
    elif args.cmd == "build":
        cells = load_json(args.cells) if args.cells else enumerate_cells(parse_overrides(args.seed_override))
        selection = load_json(args.selection) if args.selection else None
        rows, meta = build_manifest(args.phase, cells, selection, repeats=args.repeats, lanes=args.lanes,
                                    seed=args.seed, n_shift=args.n_shift, n_aa=args.n_aa,
                                    shift_reps=args.shift_reps, aa_reps=args.aa_reps)
        print(json.dumps(meta["summary"], indent=1))
        if args.dry_run:
            return
        out = Path(args.out) if args.out else DATA / f"manifest_{args.phase}.jsonl"
        write_rows(out, rows)
        out.with_name(out.stem + "_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print(f"{len(rows)} rows -> {out}")
    elif args.cmd == "check":
        print(json.dumps(check(read_rows(args.manifest)), indent=1))


if __name__ == "__main__":
    main()
