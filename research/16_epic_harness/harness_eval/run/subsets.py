#!/usr/bin/env python3
"""Draw the task subsets once (fixed seed), the same for every model.

    python3 run/subsets.py            -> run/subsets.json

Each benchmark gets an ordered list: the first 12 are the qualification set (P3), the first N the profile set
(P4; N = 40, or 50 for SWE-bench Verified Mini). Strata are interleaved round-robin so any prefix is spread
across them. Reserves (the rest of each stratum, shuffled) replace tasks whose oracle fails.
Later replacements (SWE-bench Pro protonmail, Terminal-Bench disk-heavy tasks) were applied to subsets.json
in place and are recorded under each benchmark's "excluded" key; rerunning this script would drop them.
"""
from __future__ import annotations

import json
import random
import re
import tomllib
from collections import defaultdict
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
SEED = 20261012
N = 40
LANG = {"internetarchive/openlibrary": "python", "ansible/ansible": "python", "qutebrowser/qutebrowser": "python",
        "flipt-io/flipt": "go", "gravitational/teleport": "go", "future-architect/vuls": "go",
        "navidrome/navidrome": "go", "protonmail/webclients": "js", "element-hq/element-web": "js",
        "NodeBB/NodeBB": "js", "tutao/tutanota": "ts"}


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Proportional allocation with largest remainders."""
    total = sum(sizes.values())
    raw = {k: n * v / total for k, v in sizes.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: n - sum(out.values())]:
        out[k] += 1
    return out


def interleave(groups: dict[str, list[str]]) -> list[str]:
    order, keys = [], sorted(groups, key=lambda k: -len(groups[k]))
    while any(groups[k] for k in keys):
        for k in keys:
            if groups[k]:
                order.append(groups[k].pop(0))
    return order


def stratified(strata: dict[str, list[str]], n: int, rng: random.Random) -> tuple[list[str], list[str]]:
    for items in strata.values():
        rng.shuffle(items)
    quota = allocate({k: len(v) for k, v in strata.items()}, n)
    chosen = {k: v[: quota[k]] for k, v in strata.items()}
    reserve = {k: v[quota[k]:] for k, v in strata.items()}
    return interleave(chosen), interleave(reserve)


def main() -> None:
    rng = random.Random(SEED)
    out: dict = {"seed": SEED}

    mini = json.loads(Path("/data/fritz/epic/x2/verified_mini_ids.json").read_text())
    swe = list(mini)
    rng.shuffle(swe)
    by_repo = defaultdict(list)
    for t in swe:
        by_repo[t.split("__")[0]].append(t)
    out["swe"] = {"tasks": interleave(by_repo), "reserve": [], "source": "datasets/swe-bench-verified",
                  "note": "SWE-bench Verified Mini (the 50 X2 instances)"}

    pro = defaultdict(list)
    for t in sorted(p.name for p in (ROOT / "vendor/SWE-bench_Pro-os/v2/tasks").iterdir()):
        m = re.match(r"instance_([^_]+)__(.+?)-[0-9a-f]{7,}", t)
        repo = f"{m.group(1)}/{m.group(2)}"
        pro[f"{LANG[repo]}|{repo}"].append(t)
    by_lang = defaultdict(dict)
    for key, items in pro.items():
        lang, repo = key.split("|")
        by_lang[lang][repo] = items
    lang_quota = allocate({lang: sum(map(len, repos.values())) for lang, repos in by_lang.items()}, N)
    chosen, reserve = {}, {}
    for lang, repos in by_lang.items():
        if lang_quota[lang] == 0:
            continue
        c, r = stratified({k: list(v) for k, v in repos.items()}, lang_quota[lang], rng)
        chosen[lang], reserve[lang] = c, r
    out["swepro"] = {"tasks": interleave(chosen), "reserve": interleave(reserve),
                     "source": "vendor/SWE-bench_Pro-os/v2/tasks", "quota": lang_quota}

    tb = defaultdict(list)
    for p in sorted((ROOT / "datasets/terminal-bench-2-1").iterdir()):
        meta = tomllib.loads((p / "task.toml").read_text()).get("metadata", {})
        tb[meta.get("category") or "other"].append(p.name)
    c, r = stratified(tb, N, rng)
    out["tbench"] = {"tasks": c, "reserve": r, "source": "datasets/terminal-bench-2-1"}

    gaia = defaultdict(list)
    for p in sorted((ROOT / "datasets/gaia").iterdir()):
        if (p / "environment" / "workspace").exists():
            continue                                    # attached file: not in the text-only pool
        tags = tomllib.loads((p / "task.toml").read_text()).get("metadata", {}).get("tags", [])
        level = next((t for t in tags if t.startswith("level")), "level?")
        gaia[level].append(p.name)
    c, r = stratified(gaia, N, rng)
    out["gaia"] = {"tasks": c, "reserve": r, "source": "datasets/gaia",
                   "pool": {k: len(v) for k, v in gaia.items()}, "note": "questions without an attached file"}

    (ROOT / "run" / "subsets.json").write_text(json.dumps(out, indent=1))
    for k in ("swe", "swepro", "tbench", "gaia"):
        print(k, len(out[k]["tasks"]), "first 12:", out[k]["tasks"][:12][:3], "...")
    print("pro quota", lang_quota, "gaia pool", out["gaia"]["pool"])


if __name__ == "__main__":
    main()
