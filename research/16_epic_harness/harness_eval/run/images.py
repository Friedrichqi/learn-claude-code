#!/usr/bin/env python3
"""Track and remove the Docker images this study pulls or builds; never touch anything else.

The daemon is shared (other users' images and containers live on the same root disk), so `docker image prune`,
`system prune` and `harbor cache clean` are off limits. Instead:

    images.py snapshot NAME    record every image ID, intermediates included (run/state/images_NAME.txt)
    images.py cleanup NAME     remove images that are new since NAME and belong to us: tagged with a benchmark
                               pattern, or descended from / an ancestor of such an image (Harbor builds task
                               images FROM the benchmark images and leaves untagged intermediates). Children
                               first, never an image a container uses. Each removal is logged to
                               pulled_images.txt.
    images.py free             free GB on Docker's root disk
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/data/fritz/learn-claude-code/research/16_epic_harness/harness_eval")
STATE = ROOT / "run" / "state"
LOG = ROOT / "pulled_images.txt"
OURS = [r"^alexgshaw/", r"^swebench/sweb\.eval\.", r"^sweb\.(eval|env|base)\.", r"^ghcr\.io/scaleapi/swe-bench_pro", r"^jefzda/sweap-images",
        r"^hb__", r"^sb__", r"^harbor-prebuilt", r"^python:3\.11-slim-bookworm$", r"^alpine:3\.23",
        r"^ghcr\.io/epoch-research/swe-bench", r"__env-main(:|$)"]
KEEP = [r"^s15-eval:", r"^harbor-prebuilt:harbor-docker-egress-control-sidecar"]   # the sidecar is shared by every Pro trial


def run(*cmd: str) -> str:
    return subprocess.run(list(cmd), capture_output=True, text=True).stdout


def all_ids() -> set[str]:
    return {line.strip() for line in run("docker", "images", "-a", "--no-trunc", "-q").splitlines() if line.strip()}


def inspect(ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        raw = run("docker", "image", "inspect", *chunk)
        try:
            for item in json.loads(raw or "[]"):
                out[item["Id"]] = {"parent": item.get("Parent") or "", "tags": item.get("RepoTags") or []}
        except json.JSONDecodeError:
            pass
    return out


def used_image_ids() -> set[str]:
    return {line.strip() for line in run("docker", "ps", "-a", "--no-trunc", "--format", "{{.ImageID}}").splitlines()
            if line.strip()}


# Docker-in-Docker mode (run/dind.md): task containers live in our own daemon whose data root is on /data, so
# free space is measured there and images are kept for reuse (no snapshot/cleanup/sweep on a 17 TB disk).
DOCKER_ROOT = os.environ.get("S15_DOCKER_ROOT", "/var/lib/docker")
KEEP_IMAGES = os.environ.get("S15_KEEP_IMAGES") == "1"


def free_gb() -> float:
    return shutil.disk_usage(DOCKER_ROOT).free / 1e9


def snapshot(name: str) -> None:
    if KEEP_IMAGES:
        return
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"images_{name}.txt").write_text("\n".join(sorted(all_ids())) + "\n")


def cleanup(name: str, tasks: list[str] | None = None) -> list[str]:
    """`tasks`: only images whose name contains one of these task ids seed the removal (several batches pull
    in parallel on one daemon, so "new since the snapshot" alone would also catch other chunks' images)."""
    if KEEP_IMAGES:
        return []
    before = set((STATE / f"images_{name}.txt").read_text().split())
    new = sorted(all_ids() - before)
    info = inspect(new)
    match = lambda tags, pats: any(re.search(p, t) for t in tags for p in pats)  # noqa: E731
    keys = [t.lower() for t in (tasks or [])]
    mine = lambda tags: not keys or any(k in t.lower() for t in tags for k in keys)  # noqa: E731
    ours = {i for i in new if match(info.get(i, {}).get("tags", []), OURS) and mine(info.get(i, {}).get("tags", []))}
    keep = {i for i in new if match(info.get(i, {}).get("tags", []), KEEP)}
    changed = True
    while changed:
        changed = False
        for i in new:
            if i in ours or i in keep:
                continue
            parent = info.get(i, {}).get("parent")
            children = [j for j in new if info.get(j, {}).get("parent") == i]
            if parent in ours or any(c in ours for c in children):
                ours.add(i)
                changed = True
    used = used_image_ids()
    removed: list[str] = []
    remaining = set(ours) - used
    for _ in range(50):                               # children first
        leaves = [i for i in remaining if not any(info.get(j, {}).get("parent") == i for j in remaining)]
        if not leaves:
            break
        for i in leaves:
            tags = [t for t in info.get(i, {}).get("tags", []) if not t.endswith(":<none>")]
            r = subprocess.run(["docker", "rmi", *(tags or [i])], capture_output=True, text=True)
            if r.returncode == 0 and tags and i in all_ids():
                subprocess.run(["docker", "rmi", i], capture_output=True, text=True)
            ok = i not in all_ids()
            with LOG.open("a") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "id": i, "tags": tags,
                                    "status": "removed" if ok else f"failed: {r.stderr.strip()[:200]}",
                                    "batch": name}) + "\n")
            if ok:
                removed.append(tags[0] if tags else i[7:19])
            remaining.discard(i)
    return removed


def sweep() -> list[str]:
    """Remove our task images that no container uses and no running chunk needs (chunks killed before their
    own cleanup leak images). Running chunks list their tasks in jobs/*/*/*.lock/tasks."""
    if KEEP_IMAGES:
        return []
    protected: list[str] = []
    for lock in ROOT.glob("jobs/*/*/*.lock"):
        try:
            pid = int((lock / "pid").read_text() or 0)
            os.kill(pid, 0)
        except (OSError, ValueError):
            continue
        tf = lock / "tasks"
        if tf.is_file():
            protected += [t.lower() for t in tf.read_text().split()]
        else:
            return []                                   # an old-style lock: we cannot tell what it needs
    used = used_image_ids()
    info = inspect(sorted(all_ids()))
    removed = []
    for iid, meta in info.items():
        tags = [t for t in meta.get("tags", []) if not t.endswith(":<none>")]
        if not tags or iid in used:
            continue
        if not any(re.search(p, t) for t in tags for p in OURS) or any(re.search(p, t) for t in tags for p in KEEP):
            continue
        if any(k in t.lower() for t in tags for k in protected):
            continue
        r = subprocess.run(["docker", "rmi", *tags], capture_output=True, text=True)
        if r.returncode == 0:
            removed += tags
            with LOG.open("a") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "id": iid, "tags": tags,
                                    "status": "removed", "batch": "sweep"}) + "\n")
    return removed


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "snapshot":
        snapshot(sys.argv[2])
    elif cmd == "cleanup":
        gone = cleanup(sys.argv[2])
        print(f"removed {len(gone)}: {' '.join(gone)[:300]}  (free {free_gb():.1f} GB)")
    elif cmd == "sweep":
        print("swept", sweep())
    elif cmd == "free":
        print(f"{free_gb():.1f}")
