#!/usr/bin/env python3
"""push / status / pull round trip of drive_data.py against a local directory standing in for Google
Drive.  Run: python3 -m pytest research/common/test_drive_data.py (skips without rclone)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().with_name("drive_data.py")
pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def run(research: Path, remote: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), "--research-dir", str(research),
                           "--remote", str(remote), *args], capture_output=True, text=True)


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
