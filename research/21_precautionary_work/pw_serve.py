#!/usr/bin/env python3
"""Serve MiniMax-M2.7 on GPUs 0-3 (TP=4) for one GPU stage of research/21_precautionary_work.

This is topic 20's stage runner (`rd_serve.run_stage`: empty GPUs, the lab-sheet claim for every 30-min slot, the cap,
start, health, client, stop, GPU-hour log), pointed at topic 21's own GPU-hour log, server folders and 40 GPU-h cap.
Before a stage it checks that the files other topics froze are unchanged (topics 19 and 20's sha256 lists), and the
stage's own pre-registration (prereg_2a.sha256 for an s2a_* stage, prereg_2b.sha256 for s2b_*); a changed file
refuses the stage (exit 4).

    python research/21_precautionary_work/pw_serve.py run --stage s2a_probe --hours 1.5 --config profile -- \\
        /data/fritz/venv/bin/python research/21_precautionary_work/pw_probe.py run --url {url} --out main
    python research/21_precautionary_work/pw_serve.py hours      # the GPU-hour tally against the 40 GPU-h cap
    python research/21_precautionary_work/pw_serve.py frozen     # the hash checks alone
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import rd_serve as rs_  # noqa: E402

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
DATA = DIR / "data"
CAP_GPU_H = 40.0
# hash lists whose files topic 21 must leave alone; paths inside are relative to the list's topic, or to the repo
FROZEN = [REPO / "research/19_efficiency_signals/prereg.sha256",
          REPO / "research/19_efficiency_signals/followups/highn_prereg.sha256",
          REPO / "research/20_round_dependency/prereg.sha256",
          REPO / "research/20_round_dependency/prereg_part2.sha256"]
# topic 21's own: a stage named s2a_* must match prereg_2a.sha256, s2b_* prereg_2b.sha256
STAGE_FROZEN = {"s2a": DIR / "prereg_2a.sha256", "s2b": DIR / "prereg_2b.sha256"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def changed(listing: Path) -> list[str]:
    """Entries of a sha256 list whose file is missing or differs. A path is looked up next to the topic folder first
    (the list may sit in a subfolder such as followups/), then under the repo."""
    topic = next(p for p in listing.parents if p.parent.name == "research")
    bad = []
    for line in listing.read_text().splitlines():
        if not line.strip():
            continue
        want, rel = line.split(maxsplit=1)
        rel = rel.lstrip("*")
        path = next((p for p in (topic / rel, REPO / rel) if p.exists()), None)
        if path is None or sha256(path) != want:
            bad.append(f"{listing.relative_to(REPO)}: {rel}")
    return bad


def frozen_report(stage: str = "") -> list[str]:
    bad = []
    lists = list(FROZEN)
    own = STAGE_FROZEN.get(stage.split("_")[0])
    if own is not None:
        if not own.exists():
            return [f"{own.relative_to(REPO)} is missing: freeze the stage's pre-registration first"]
        lists.append(own)
    for listing in lists:
        if listing.exists():
            bad += changed(listing)
    return bad


def configure() -> None:
    """Point topic 20's stage runner at topic 21's log, folders and cap (its functions read these at call time)."""
    rs_.HOURS = DATA / "gpu_hours.jsonl"
    rs_.SERVERS = DATA / "servers"
    rs_.CAP_GPU_H = CAP_GPU_H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--stage", required=True)
    r.add_argument("--hours", type=float, required=True, help="expected server lifetime (claims, cap)")
    r.add_argument("--config", default="profile", choices=sorted(rs_.CONFIGS))
    r.add_argument("--no-claim-check", action="store_true", help="tests only")
    r.add_argument("client", nargs=argparse.REMAINDER)
    sub.add_parser("hours")
    fz = sub.add_parser("frozen")
    fz.add_argument("--stage", default="", help="also the stage's own list (s2a_..., s2b_...)")
    a = ap.parse_args()
    configure()
    if a.cmd == "run":
        bad = frozen_report(a.stage)
        if bad:
            print("[serve] refused: frozen files changed:\n  " + "\n  ".join(bad), flush=True)
            sys.exit(4)
        client = a.client[1:] if a.client[:1] == ["--"] else a.client
        if not client:
            raise SystemExit("give the client command after --")
        sys.exit(rs_.run_stage(a.stage, a.hours, a.config, client, not a.no_claim_check))
    elif a.cmd == "hours":
        for line in rs_.HOURS.open() if rs_.HOURS.exists() else []:
            print(line.rstrip())
        print(f"tally {rs_.tally():.2f} / {rs_.CAP_GPU_H} GPU-h")
    else:
        bad = frozen_report(a.stage)
        print("frozen files: " + ("all unchanged" if not bad else "CHANGED:\n  " + "\n  ".join(bad)))
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
