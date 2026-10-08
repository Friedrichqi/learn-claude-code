#!/usr/bin/env python3
"""push / status / pull round trip and the automatic watcher of drive_data.py, against a local
directory standing in for Google Drive and a temporary state directory (so the real watcher's lock
is never touched).  Run: python3 -m pytest research/common/test_drive_data.py (skips without rclone)."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().with_name("drive_data.py")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def env(research: Path) -> dict[str, str]:
    out = dict(os.environ, LCC_DATA_STATE=str(research.parent / "state"))
    out.pop("SLURM_JOB_ID", None)                       # watch --ensure refuses to start inside a job
    return out


def cmd(research: Path, remote: Path, *args: str) -> list[str]:
    return [sys.executable, str(TOOL), "--research-dir", str(research), "--remote", str(remote), *args]


def run(research: Path, remote: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd(research, remote, *args), capture_output=True, text=True, env=env(research))


def age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t), follow_symlinks=False)


def make_topic(research: Path) -> Path:
    data = research / "01_demo" / "data"
    (data / "runs").mkdir(parents=True)
    (data / "runs" / "run_1.jsonl").write_text('{"call": 1}\n')
    (data / "demo_tables.md").write_text("| a | b |\n")
    (data / "empty_leaf").mkdir()
    (data / "X-r1.sandbox").mkdir()
    (data / "X-r1.sandbox" / "README.md").write_text("seed task\n")
    (data / "server_1.log").write_text("vllm log\n")
    os.symlink("server_1.log", data / "server.log")
    (data / "__pycache__").mkdir()
    (data / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\0")
    (research / "common").mkdir()                       # no data/ folder: not a topic
    return data


def test_round_trip(tmp_path: Path) -> None:
    research, remote = tmp_path / "research", tmp_path / "drive"
    data = make_topic(research)

    res = run(research, remote, "status")
    assert res.returncode == 1                          # never pushed

    res = run(research, remote, "push")
    assert res.returncode == 0, res.stderr
    mirror = remote / "research" / "01_demo" / "data"
    assert (mirror / "runs" / "run_1.jsonl").read_text() == '{"call": 1}\n'
    link = mirror / "server.log"                        # Drive stores it as server.log.rclonelink
    assert os.readlink(link) == "server_1.log" if link.is_symlink() else \
        (mirror / "server.log.rclonelink").read_text() == "server_1.log"
    assert (mirror / "empty_leaf").is_dir()
    assert not (mirror / "__pycache__").exists()

    res = run(research, remote, "status", "01")
    assert res.returncode == 0 and "01_demo: in sync" in res.stdout, res.stdout + res.stderr

    (data / "runs" / "run_1.jsonl").write_text('{"call": 1, "edited": true}\n')
    (data / "runs" / "run_2.jsonl").write_text("{}\n")
    res = run(research, remote, "status")
    assert res.returncode == 1
    assert "* runs/run_1.jsonl" in res.stdout and "+ runs/run_2.jsonl" in res.stdout, res.stdout

    res = run(research, remote, "push", "01_demo")      # replaced version is kept on the remote
    assert res.returncode == 0, res.stderr
    assert run(research, remote, "status").returncode == 0
    kept = list((remote / "_replaced").glob("*/research/01_demo/data/runs/run_1.jsonl"))
    assert len(kept) == 1 and kept[0].read_text() == '{"call": 1}\n'

    fresh = tmp_path / "clone" / "research"
    (fresh / "01_demo" / "data").mkdir(parents=True)
    (fresh / "01_demo" / "data" / "demo_tables.md").write_text("local edit\n")
    res = run(fresh, remote, "pull")
    assert res.returncode == 0, res.stderr
    got = fresh / "01_demo" / "data"
    assert (got / "demo_tables.md").read_text() == "local edit\n"     # never clobbered by default
    assert (got / "runs" / "run_2.jsonl").read_text() == "{}\n"
    assert os.readlink(got / "server.log") == "server_1.log"
    assert (got / "empty_leaf").is_dir()
    assert (got / "X-r1.sandbox" / "README.md").read_text() == "seed task\n"

    res = run(fresh, remote, "pull", "--overwrite")
    assert res.returncode == 0, res.stderr
    assert (got / "demo_tables.md").read_text() == "| a | b |\n"
    assert run(fresh, remote, "status").returncode == 0


def test_unknown_topic(tmp_path: Path) -> None:
    research = tmp_path / "research"
    make_topic(research)
    res = run(research, tmp_path / "drive", "push", "99")
    assert res.returncode != 0 and "not found" in res.stderr


def test_round_leaves_young_files_and_yields_to_a_manual_push(tmp_path: Path) -> None:
    research, remote = tmp_path / "research", tmp_path / "drive"
    data = make_topic(research)
    for p in data.rglob("*"):
        age(p, 7200)                                    # everything two hours old ...
    (data / "runs" / "run_live.jsonl").write_text("{}\n")   # ... except a file a run is still writing
    mirror = remote / "research" / "01_demo" / "data"

    (tmp_path / "state").mkdir()
    with open(tmp_path / "state" / "transfer.lock", "a") as held:   # a manual push is running
        fcntl.flock(held, fcntl.LOCK_EX)
        res = run(research, remote, "watch", "--once")
        assert res.returncode == 0 and "skipped" in res.stdout, res.stdout + res.stderr
        assert not mirror.exists()
        waiting = subprocess.Popen(cmd(research, remote, "push", "--min-age", "1h"), env=env(research),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        assert waiting.poll() is None and not mirror.exists()          # a manual push waits its turn
    out, err = waiting.communicate(timeout=60)
    assert waiting.returncode == 0 and "waiting for it" in err, err
    assert (mirror / "runs" / "run_1.jsonl").exists() and not (mirror / "runs" / "run_live.jsonl").exists()

    res = run(research, remote, "watch", "--once", "--min-age", "1h")
    assert res.returncode == 0 and "done: rc 0" in res.stdout, res.stdout + res.stderr
    assert not (mirror / "runs" / "run_live.jsonl").exists()          # still too young
    st = run(research, remote, "watch", "--status")
    assert st.returncode == 1 and "not running" in st.stdout and "last success:" in st.stdout, st.stdout

    age(data / "runs" / "run_live.jsonl", 7200)                       # the run finished long ago
    assert run(research, remote, "watch", "--once", "--min-age", "1h").returncode == 0
    assert (mirror / "runs" / "run_live.jsonl").read_text() == "{}\n"


def test_ensure_starts_one_detached_watcher_and_stop_ends_it(tmp_path: Path) -> None:
    research, remote, state = tmp_path / "research", tmp_path / "drive", tmp_path / "state"
    make_topic(research)
    try:
        res = run(research, remote, "watch", "--ensure", "--every", "1h", "--min-age", "0s")
        assert res.returncode == 0 and "started watcher" in res.stdout, res.stdout + res.stderr
        deadline = time.time() + 60
        while not (state / "last_round.json").exists() and time.time() < deadline:
            time.sleep(0.2)
        st = run(research, remote, "watch", "--status")
        assert st.returncode == 0 and "watcher: running" in st.stdout and "rc 0" in st.stdout, st.stdout
        assert (remote / "research" / "01_demo" / "data" / "runs" / "run_1.jsonl").exists()
        assert "done: rc 0" in (state / "watch.log").read_text()
        again = run(research, remote, "watch", "--ensure")
        assert "already running" in again.stdout, again.stdout
    finally:
        res = run(research, remote, "watch", "--stop")
    assert "stopped watcher" in res.stdout, res.stdout
    assert run(research, remote, "watch", "--status").returncode == 1
