#!/usr/bin/env python3
"""Serve MiniMax-M2.7 on GPUs 0-3 (TP=4) for the rest of the KV-threshold `wait` probe (ef_kvwait.py), on a stage of
its own (topic 19 follow-up, 2026-10-30).

The probe was frozen to ride as a second client on topic 21's s2a_probe server. That stage's own probe (60-70k-token
prompts, S=8, 16 in flight) filled the 277,440-token KV pool, so at topic 21's request the client stopped at 20:04 UTC
with 13 of 540 screen records. The user then chose a stage of its own for the rest.

`run` is topic 20's stage runner (rd_serve.run_stage: empty GPUs, the lab-sheet claim for every 30-min slot, the cap,
the `profile` server, health, client, stop, GPU-hour log), pointed at data/kvwait/ for its log and server folders and
capped at the rest of topic 19's 120 GPU-h (108.52 spent before this stage), as pw_serve.configure does for topic 21.

`client` runs ef_kvwait.run unchanged except for two things, both logged in the README's Changes:
  - 8 requests in flight instead of 4 (the 4 only spared topic 21's probe on the shared server);
  - the frozen server check accepts only topic 21's s2a stages; this stage's own check replaces it: the stage named
    after the probe, config `profile`, and /v1/models lists MiniMaxAI/MiniMax-M2.7.
`--probe kvwait2` (2026-10-31) serves the revised screen (ef_kvwait2.run, 8 in flight) the same way, as stage
`kvwait2`.

    python research/19_efficiency_signals/ef_kvserve.py run --hours 2.5
    python research/19_efficiency_signals/ef_kvserve.py run --hours 1.0 --probe kvwait2
    python research/19_efficiency_signals/ef_kvserve.py hours
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))  # research/
import _paths  # noqa: F401,E402

import rd_serve as rs_  # noqa: E402

PY = "/data/fritz/venv/bin/python"
KV = DIR / "data" / "kvwait"
STAGE, CONFIG = "kvwait", "profile"
PROBES = ("kvwait", "kvwait2")                    # the stage is named after the probe
INFLIGHT = 8
CAP_GPU_H = round(120.0 - 108.52, 2)      # topic 19's cap minus the README's GPU-hour log before this stage


def configure() -> None:
    """Point topic 20's stage runner at this probe's log, server folders and cap (its functions read these at call
    time)."""
    rs_.HOURS = KV / "gpu_hours.jsonl"
    rs_.SERVERS = KV / "servers"
    rs_.CAP_GPU_H = CAP_GPU_H


def check_server(url: str, ek, stage: str = STAGE) -> str | None:
    """Why this server must not get the probe's traffic (None: this stage's `profile` server, serving MiniMax)."""
    info = ek.server_of(url)
    if info is None:
        return f"no live {stage} stage listens on this port"
    if info.get("stage") != stage or info.get("config") != CONFIG:
        return f"stage {info.get('stage')!r} with config {info.get('config')!r} is not this probe's stage"
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=30) as resp:
            models = [m.get("id") for m in json.loads(resp.read()).get("data") or []]
    except Exception as exc:                          # noqa: BLE001
        return f"/v1/models failed: {exc}"
    return None if ek.cf.MODEL in models else f"the server serves {models}, not {ek.cf.MODEL}"


def client(url: str, probe: str = STAGE) -> int:
    import ef_kvwait as ek
    ek.T21_SERVERS = KV / "servers"       # server_of (the check below, runs.jsonl) looks in this stage's folders
    why = check_server(url, ek, probe)
    if why:
        print(f"[{probe}] refused: {why}", flush=True)
        return 6
    if probe == "kvwait2":
        import ef_kvwait2
        return ef_kvwait2.run(url)
    ek.INFLIGHT = INFLIGHT
    return ek.run(url, any_server=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--hours", type=float, required=True, help="expected server lifetime (claims, cap)")
    r.add_argument("--probe", default=STAGE, choices=PROBES)
    c = sub.add_parser("client")
    c.add_argument("--url", required=True)
    c.add_argument("--probe", default=STAGE, choices=PROBES)
    sub.add_parser("hours")
    a = ap.parse_args()
    configure()
    if a.cmd == "run":
        sys.exit(rs_.run_stage(a.probe, a.hours, CONFIG, [PY, str(Path(__file__).resolve()), "client", "--probe",
                                                          a.probe, "--url", "{url}"]))
    elif a.cmd == "client":
        sys.exit(client(a.url, a.probe))
    else:
        for line in rs_.HOURS.open() if rs_.HOURS.exists() else []:
            print(line.rstrip())
        print(f"tally {rs_.tally():.2f} / {CAP_GPU_H} GPU-h")


if __name__ == "__main__":
    main()
