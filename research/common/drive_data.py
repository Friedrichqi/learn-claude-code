#!/usr/bin/env python3
"""Mirror the research results (research/<topic>/data/) to Google Drive with rclone.

    python3 research/common/drive_data.py status [TOPIC ...] [--quick]
    python3 research/common/drive_data.py push   [TOPIC ...] [--min-age 30m] [--dry-run]
    python3 research/common/drive_data.py pull   [TOPIC ...] [--overwrite] [--dry-run]
    python3 research/common/drive_data.py watch  [--ensure | --stop | --status | --once] [--every 1h] [--min-age 30m]

The data folders are not in git (see .gitignore and research/DATA.md); this keeps a file-by-file
copy of each one at <remote>/research/<topic>/data/.  TOPIC is a folder name or its number prefix
("11" = 11_multiuser_kv); no TOPIC means every research/NN_*/data folder (pull: every topic on the
remote).  The remote root is --remote, else $LCC_DATA_REMOTE, else gdrive:learn-claude-code-data
(an rclone remote; any rclone path works, including a local directory).

  status  rclone check, local against remote, by MD5 (--quick: by size only).  Per topic: files
          that are the same, only local (not pushed), only on the remote (not pulled), or
          different.  Exit 1 unless every topic is in sync.
  push    rclone copy local -> remote.  Never deletes on the remote; a file it replaces moves to
          <remote>/_replaced/<UTC time>/research/<topic>/data/.  --min-age 30m leaves files
          modified in the last 30 minutes (a run may still be writing them) for a later push.
  pull    rclone copy remote -> local.  Only adds files that are missing locally, so results not
          pushed yet are never overwritten; --overwrite replaces local files that differ.
  watch   automatic sync: push every topic (new ones included) every --every (default 1h),
          leaving files younger than --min-age (default 30m) for the next round.  --ensure starts
          it detached unless one is already running (the Claude Code SessionStart hook calls it),
          --stop stops it, --status shows whether it runs and how its last round went, --once
          runs one round in the foreground.

push and pull never overlap: they share a lock, so a watcher round is skipped while a manual push
or pull runs and a manual one waits for the round to finish.  Locks, log and state files live in
$LCC_DATA_STATE (default ~/.cache/lcc-drive-sync/).  Symlinks travel as .rclonelink files
(--links); __pycache__, .pytest_cache and rclone's *.partial download files are skipped.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "gdrive:learn-claude-code-data"
STATE = Path(os.environ.get("LCC_DATA_STATE", Path.home() / ".cache" / "lcc-drive-sync"))
RCLONE = shutil.which("rclone") or str(Path.home() / ".local" / "bin" / "rclone")
LOG_MAX = 5 * 2**20                                     # watch.log rotates to watch.log.1 past this
FILTERS = ["--exclude", "__pycache__/**", "--exclude", ".pytest_cache/**", "--exclude", "*.partial"]
COPY_FLAGS = ["--links", "--create-empty-src-dirs", "--fast-list", "--transfers", "8",
              "--checkers", "16", "--drive-chunk-size", "64M", "--stats", "60s", "--stats-one-line", "-v"]
# rclone check --combined marks: '=' same, '+' only in source (local), '-' only in destination
# (remote), '*' different, '!' error while reading either side.
MARKS = {"=": "same", "+": "only local", "-": "only remote", "*": "different", "!": "error"}


def rclone(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [RCLONE, *args]
    print("$ " + shlex.join(["rclone", *args]), file=sys.stderr, flush=True)
    return subprocess.run(cmd, text=True, capture_output=capture)


def seconds(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"bad duration {text!r} (use e.g. 90s, 30m, 1h)")
    return float(m[1]) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]


def stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def lock(name: str, wait: bool):
    """Exclusive flock on STATE/name (NFSv4 makes it hold across nodes); None if busy and not wait."""
    STATE.mkdir(parents=True, exist_ok=True)
    f = open(STATE / name, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        f.close()
        return None
    return f


def transfer_lock(wait: bool):
    held = lock("transfer.lock", wait=False)
    if held is None and wait:
        print("another push/pull is running (see `watch --status`); waiting for it ...", file=sys.stderr, flush=True)
        held = lock("transfer.lock", wait=True)
    return held


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


def push(research: Path, remote: str, topics: list[str], dry_run: bool = False, min_age: str | None = None) -> int:
    utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    young = ["--min-age", min_age] if min_age and seconds(min_age) > 0 else []
    rc = 0
    for t in topics:
        args = ["copy", str(research / t / "data"), f"{remote}/research/{t}/data", *COPY_FLAGS,
                "--backup-dir", f"{remote}/_replaced/{utc}/research/{t}/data", *FILTERS, *young]
        rc |= rclone(args + (["--dry-run"] if dry_run else [])).returncode
    return rc


def pull(research: Path, remote: str, topics: list[str], overwrite: bool, dry_run: bool) -> int:
    rc = 0
    for t in topics:
        args = ["copy", f"{remote}/research/{t}/data", str(research / t / "data"), *COPY_FLAGS, *FILTERS]
        args += [] if overwrite else ["--ignore-existing"]
        rc |= rclone(args + (["--dry-run"] if dry_run else [])).returncode
    return rc


# ---- automatic sync -------------------------------------------------------------------------

def watcher_info() -> dict:
    try:
        return json.loads((STATE / "watch.json").read_text())
    except (OSError, ValueError):
        return {}


def alive(info: dict) -> bool:
    """Is the watcher recorded in watch.json running on this host?"""
    if not info or info.get("host") != socket.gethostname():
        return False
    try:
        argv = Path(f"/proc/{info['pid']}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(arg.endswith(b"drive_data.py") for arg in argv) and b"watch" in argv


def open_log(path: Path) -> None:
    """Send stdout/stderr (and so rclone's output) to path, rotating it to path.1 past LOG_MAX."""
    if path.exists() and path.stat().st_size > LOG_MAX:
        path.replace(path.with_name(path.name + ".1"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def sync_round(research: Path, remote: str, min_age: str) -> int:
    start, t0 = stamp(), time.monotonic()
    print(f"=== {start} round: push every topic, files younger than {min_age} wait", flush=True)
    rc: int | None = None
    held = transfer_lock(wait=False)
    if held is None:
        print("=== skipped: a manual push/pull holds the lock", flush=True)
    else:
        try:
            rc = push(research, remote, local_topics(research), min_age=min_age)
        except Exception:                                # keep the watcher alive; record the failure
            traceback.print_exc()
            rc = -1
        finally:
            held.close()
    run = {"start": start, "end": stamp(), "seconds": round(time.monotonic() - t0), "rc": rc}
    (STATE / "last_round.json").write_text(json.dumps(run) + "\n")
    if rc == 0:
        (STATE / "last_ok.json").write_text(json.dumps(run) + "\n")
    print(f"=== {run['end']} done: rc {rc}, {run['seconds']} s", flush=True)
    return rc or 0


def watch(research: Path, remote: str, every: str, min_age: str, log: Path | None) -> int:
    held = lock("watch.lock", wait=False)               # one watcher at a time, on any node
    if held is None:
        print("a watcher is already running (see `watch --status`)", file=sys.stderr)
        return 0
    info = {"pid": os.getpid(), "host": socket.gethostname(), "started": stamp(), "every": every, "min_age": min_age}
    (STATE / "watch.json").write_text(json.dumps(info) + "\n")
    while True:
        if log:
            open_log(log)
        sync_round(research, remote, min_age)
        time.sleep(seconds(every))


def ensure(research: Path, remote: str, every: str, min_age: str) -> int:
    if os.environ.get("SLURM_JOB_ID"):
        print("not starting a watcher inside a Slurm job")
        return 0
    probe = lock("watch.lock", wait=False)
    if probe is None:
        info = watcher_info()
        print(f"watcher already running (pid {info.get('pid')} on {info.get('host')})")
        return 0
    probe.close()       # the child takes the lock itself; if two --ensure race, the loser's child exits
    cmd = [sys.executable, str(Path(__file__).resolve()), "watch", "--every", every, "--min-age", min_age,
           "--remote", remote, "--research-dir", str(research), "--log", str(STATE / "watch.log")]
    env = dict(os.environ, PATH=f"{Path(RCLONE).parent}{os.pathsep}{os.environ.get('PATH', '')}")
    child = subprocess.Popen(cmd, cwd=research.parent, env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"started watcher pid {child.pid}: every {every}, files younger than {min_age} wait; "
          f"log {STATE / 'watch.log'}")
    return 0


def stop() -> int:
    info = watcher_info()
    if not alive(info):
        print("no watcher running on this host")
        return 0
    pid = info["pid"]
    try:                                    # the whole process group, so a running rclone stops too
        os.killpg(pid, signal.SIGTERM) if os.getpgid(pid) == pid else os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(100):
        if not alive(info):
            print(f"stopped watcher pid {pid}")
            return 0
        time.sleep(0.1)
    print(f"watcher pid {pid} did not stop")
    return 1


def watch_status() -> int:
    info = watcher_info()
    running = alive(info)
    if running:
        print(f"watcher: running, pid {info['pid']} on {info['host']} since {info['started']}, "
              f"every {info['every']}, files younger than {info['min_age']} wait")
    elif info and info.get("host") != socket.gethostname():
        print(f"watcher: last started on {info.get('host')} (pid {info.get('pid')}); check from that host")
    else:
        print("watcher: not running (start it with `drive_data.py watch --ensure`)")
    for name, label in (("last_round.json", "last round"), ("last_ok.json", "last success")):
        try:
            r = json.loads((STATE / name).read_text())
            print(f"{label}: {r['start']} -> {r['end']}, rc {r['rc']}")
        except (OSError, ValueError, KeyError):
            print(f"{label}: none")
    print(f"log: {STATE / 'watch.log'}")
    return 0 if running else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "push", "pull", "watch"])
    ap.add_argument("topics", nargs="*", help="topic folders or number prefixes (default: all)")
    ap.add_argument("--remote", default=os.environ.get("LCC_DATA_REMOTE", DEFAULT_REMOTE),
                    help="rclone root of the mirror (default: $LCC_DATA_REMOTE or %(default)s)")
    ap.add_argument("--research-dir", type=Path, default=RESEARCH, help="research/ folder (default: this repo's)")
    ap.add_argument("--quick", action="store_true", help="status: compare sizes only, not MD5")
    ap.add_argument("--overwrite", action="store_true", help="pull: replace local files that differ")
    ap.add_argument("--dry-run", action="store_true", help="push/pull: show what would be copied")
    ap.add_argument("--min-age", help="push/watch: leave files modified more recently than this (watch default: 30m)")
    ap.add_argument("--every", default="1h", help="watch: time between rounds (default: %(default)s)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ensure", action="store_true", help="watch: start detached unless already running")
    mode.add_argument("--stop", action="store_true", help="watch: stop the running watcher")
    mode.add_argument("--status", action="store_true", help="watch: is it running, how did the last round go")
    mode.add_argument("--once", action="store_true", help="watch: run one round in the foreground")
    ap.add_argument("--log", type=Path, help=argparse.SUPPRESS)
    a = ap.parse_args()
    for text in (a.every, a.min_age):
        if text is not None:
            try:
                seconds(text)
            except argparse.ArgumentTypeError as e:
                ap.error(str(e))
    remote, research = a.remote.rstrip("/"), a.research_dir.resolve()

    if a.command == "watch":
        if a.topics:
            ap.error("watch covers every topic; give no TOPIC")
        if a.status:
            return watch_status()
        if a.stop:
            return stop()
        min_age = a.min_age or "30m"
        if a.ensure:
            return ensure(research, remote, a.every, min_age)
        if not os.access(RCLONE, os.X_OK):
            sys.exit("rclone not found (see research/DATA.md)")
        if a.once:
            return sync_round(research, remote, min_age)
        return watch(research, remote, a.every, min_age, a.log)
    if not os.access(RCLONE, os.X_OK):
        sys.exit("rclone not found (see research/DATA.md)")

    known = remote_topics(remote) if a.command == "pull" else local_topics(research)
    topics = resolve(a.topics, known) if a.topics else known
    if not topics:
        sys.exit("no topics found")
    if a.command == "status":
        return status(research, remote, topics, a.quick)
    held = transfer_lock(wait=True)                     # noqa: F841 -- held until the process exits
    if a.command == "push":
        return push(research, remote, topics, a.dry_run, a.min_age)
    return pull(research, remote, topics, a.overwrite, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
