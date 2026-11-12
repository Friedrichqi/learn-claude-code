#!/usr/bin/env bash
# Pin today's s15 harness for topic 24 (as topic 23's et_vendor.sh, with the repository path derived from this file):
# code.py, trace_runtime.py, hold_runtime.py, s09_memory/code.py and skills/ into research/24_spark_concurrency/vendor/s15,
# with SNAPSHOT.json recording the commit, dirty files and a sha256 per file. The child loads code.py from here
# (S15_VENDOR, set by sp_cell.py); later edits to s15 in the repository do not reach a running chain.
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DST=$REPO/research/24_spark_concurrency/vendor/s15
rm -rf "$DST"
mkdir -p "$DST/s15_integrated_harness" "$DST/s09_memory"
cp "$REPO/s15_integrated_harness/code.py" "$REPO/s15_integrated_harness/trace_runtime.py" \
   "$REPO/s15_integrated_harness/hold_runtime.py" "$DST/s15_integrated_harness/"
cp "$REPO/s09_memory/code.py" "$DST/s09_memory/"
cp -r "$REPO/skills" "$DST/skills"
python3 - "$REPO" "$DST" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path
repo, dst = Path(sys.argv[1]), Path(sys.argv[2])
files = {str(p.relative_to(dst)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dst.rglob("*")) if p.is_file()}
git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True).stdout.strip()
snap = {"repo": str(repo), "head": git("rev-parse", "HEAD"),
        "dirty": [l for l in git("status", "--porcelain", "--", "s15_integrated_harness", "s09_memory", "skills").splitlines()],
        "files": files}
(dst / "SNAPSHOT.json").write_text(json.dumps(snap, indent=1) + "\n")
print(f"{len(files)} files; HEAD {snap['head'][:8]}; dirty under s15/s09/skills: {len(snap['dirty'])}")
PY
