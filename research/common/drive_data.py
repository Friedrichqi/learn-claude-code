#!/usr/bin/env python3
"""Mirror the research results (research/<topic>/data/) to Google Drive with rclone.

    python3 research/common/drive_data.py status [TOPIC ...] [--quick]
    python3 research/common/drive_data.py push   [TOPIC ...] [--dry-run]
    python3 research/common/drive_data.py pull   [TOPIC ...] [--overwrite] [--dry-run]

The data folders are not in git (see .gitignore and research/DATA.md); this keeps a file-by-file
copy of each one at <remote>/research/<topic>/data/.  TOPIC is a folder name or its number prefix
("11" = 11_multiuser_kv); no TOPIC means every research/NN_*/data folder (pull: every topic on the
remote).  The remote root is --remote, else $LCC_DATA_REMOTE, else gdrive:learn-claude-code-data
(an rclone remote; any rclone path works, including a local directory).

  status  rclone check, local against remote, by MD5 (--quick: by size only).  Per topic: files
          that are the same, only local (not pushed), only on the remote (not pulled), or
          different.  Exit 1 unless every topic is in sync.
  push    rclone copy local -> remote.  Never deletes on the remote; a file it replaces moves to
          <remote>/_replaced/<UTC time>/research/<topic>/data/.
  pull    rclone copy remote -> local.  Only adds files that are missing locally, so results not
          pushed yet are never overwritten; --overwrite replaces local files that differ.

Symlinks travel as .rclonelink files (--links); __pycache__ and .pytest_cache are skipped.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "gdrive:learn-claude-code-data"
FILTERS = ["--exclude", "__pycache__/**", "--exclude", ".pytest_cache/**"]
COPY_FLAGS = ["--links", "--create-empty-src-dirs", "--fast-list", "--transfers", "8",
              "--checkers", "16", "--drive-chunk-size", "64M", "--stats", "60s", "--stats-one-line", "-v"]
# rclone check --combined marks: '=' same, '+' only in source (local), '-' only in destination
# (remote), '*' different, '!' error while reading either side.
MARKS = {"=": "same", "+": "only local", "-": "only remote", "*": "different", "!": "error"}


def rclone(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    cmd = ["rclone", *args]
    print("$ " + shlex.join(cmd), file=sys.stderr, flush=True)
    return subprocess.run(cmd, text=True, capture_output=capture)


def local_topics(research: Path) -> list[str]:
    return sorted(p.parent.name for p in research.glob("[0-9][0-9]_*/data") if p.is_dir())


def remote_topics(remote: str) -> list[str]:
    res = rclone(["lsf", "--dirs-only", f"{remote}/research"], capture=True)
    if res.returncode != 0:
        sys.exit(f"cannot list {remote}/research:\n{res.stderr.strip()}")
    return sorted(line.rstrip("/") for line in res.stdout.splitlines() if line.strip())


def resolve(names: list[str], known: list[str]) -> list[str]:
    out = []
    for name in names:
        name = name.strip("/").removeprefix("research/").removesuffix("/data")
        hits = [t for t in known if t == name] or [t for t in known if t.startswith(name + "_")] \
            or [t for t in known if t.startswith(name)]
        if len(hits) != 1:
            sys.exit(f"topic {name!r}: {'ambiguous: ' + ', '.join(hits) if hits else 'not found'}"
                     f" (known: {', '.join(known) or 'none'})")
        out.append(hits[0])
    return out


def status(research: Path, remote: str, topics: list[str], quick: bool) -> int:
    in_sync = True
    for t in topics:
        local, dst = research / t / "data", f"{remote}/research/{t}/data"
        args = ["check", str(local), dst, "--combined", "-", "--links", "--fast-list",
                "--checkers", "16", *FILTERS] + (["--size-only"] if quick else [])
        res = rclone(args, capture=True)
        groups: dict[str, list[str]] = {}
        for line in res.stdout.splitlines():
            if len(line) > 2 and line[0] in MARKS and line[1] == " ":
                groups.setdefault(line[0], []).append(line[2:])
        if not groups and res.returncode != 0:          # e.g. the topic was never pushed
            print(f"{t}: rclone check failed\n  " + res.stderr.strip().replace("\n", "\n  "))
            in_sync = False
            continue
        counts = ", ".join(f"{len(groups.get(m, []))} {MARKS[m]}" for m in "=+-*!" if groups.get(m) or m == "=")
        ok = res.returncode == 0 and not any(groups.get(m) for m in "+-*!")
        in_sync &= ok
        print(f"{t}: {'in sync' if ok else 'OUT OF SYNC'} ({counts})")
        for m in "+-*!":
            for path in groups.get(m, [])[:10]:
                print(f"    {m} {path}")
            if len(groups.get(m, [])) > 10:
                print(f"    {m} ... {len(groups[m]) - 10} more")
    return 0 if in_sync else 1


def push(research: Path, remote: str, topics: list[str], dry_run: bool) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rc = 0
    for t in topics:
        args = ["copy", str(research / t / "data"), f"{remote}/research/{t}/data", *COPY_FLAGS,
                "--backup-dir", f"{remote}/_replaced/{stamp}/research/{t}/data", *FILTERS]
        rc |= rclone(args + (["--dry-run"] if dry_run else [])).returncode
    return rc


def pull(research: Path, remote: str, topics: list[str], overwrite: bool, dry_run: bool) -> int:
    rc = 0
    for t in topics:
        args = ["copy", f"{remote}/research/{t}/data", str(research / t / "data"), *COPY_FLAGS, *FILTERS]
        args += [] if overwrite else ["--ignore-existing"]
        rc |= rclone(args + (["--dry-run"] if dry_run else [])).returncode
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "push", "pull"])
    ap.add_argument("topics", nargs="*", help="topic folders or number prefixes (default: all)")
    ap.add_argument("--remote", default=os.environ.get("LCC_DATA_REMOTE", DEFAULT_REMOTE),
                    help="rclone root of the mirror (default: $LCC_DATA_REMOTE or %(default)s)")
    ap.add_argument("--research-dir", type=Path, default=RESEARCH, help="research/ folder (default: this repo's)")
    ap.add_argument("--quick", action="store_true", help="status: compare sizes only, not MD5")
    ap.add_argument("--overwrite", action="store_true", help="pull: replace local files that differ")
    ap.add_argument("--dry-run", action="store_true", help="push/pull: show what would be copied")
    a = ap.parse_args()
    if shutil.which("rclone") is None:
        sys.exit("rclone is not on PATH (see research/DATA.md)")
    remote, research = a.remote.rstrip("/"), a.research_dir.resolve()

    known = remote_topics(remote) if a.command == "pull" else local_topics(research)
    topics = resolve(a.topics, known) if a.topics else known
    if not topics:
        sys.exit("no topics found")
    if a.command == "status":
        return status(research, remote, topics, a.quick)
    if a.command == "push":
        return push(research, remote, topics, a.dry_run)
    return pull(research, remote, topics, a.overwrite, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
