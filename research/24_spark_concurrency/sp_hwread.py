"""Topic 24: read the root counter sampler's files (sp_hw.py) into samples on CLOCK_MONOTONIC. Standard library only.

  perf_rows(session)   one row per (interval end, cpu, event): (t_mono_ns, cpu, event, value, run_pct)
                       `perf stat -I` prints seconds since perf started; segments.jsonl has perf's start on both
                       clocks (recorded just before it was launched, so times are late by perf's start-up, ~10-50 ms)
  gpu_rows(session)    one row per (sample, metric): (t_mono_ns, metric name, value), from each gpu_<n>.nsys-rep
                       exported once to SQLite (`nsys export --type sqlite`, as the owner, no root). Nsight's
                       timestamps count from the session start; TARGET_INFO_SESSION_START_TIME gives that start on
                       the wall clock, and sp_hw.py's clock pair maps wall to monotonic.
  window_*(...)        sums and means over a [t0, t1) window
"""
from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
from pathlib import Path

NSYS = "/usr/local/cuda/bin/nsys"


def sessions(hw_dir: Path) -> list[Path]:
    return sorted(p for p in Path(hw_dir).glob("session_*") if p.is_dir())


def segments(session: Path) -> list[dict]:
    out = []
    p = session / "segments.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


# -- perf ---------------------------------------------------------------------------------------------------------------
def perf_rows(session: Path) -> list[tuple]:
    rows = []
    for seg in segments(session):
        if seg["kind"] != "perf":
            continue
        t_start = seg["start"]["t"]
        path = session / seg["file"]
        if not path.exists():
            continue
        with open(path, newline="") as f:
            for rec in csv.reader(f):
                if not rec or rec[0].startswith("#") or len(rec) < 7:
                    continue
                try:
                    t = float(rec[0])
                    value = float(rec[2])
                except ValueError:
                    continue                       # <not counted>, <not supported>
                cpu = int(rec[1][3:]) if rec[1].startswith("CPU") else -1
                event = rec[4].split("/")[1] if "/" in rec[4] else rec[4]
                try:
                    run_pct = float(rec[6])
                except ValueError:
                    run_pct = None
                rows.append((t_start + int(t * 1e9), cpu, event, value, run_pct))
    return rows


# -- gpu ----------------------------------------------------------------------------------------------------------------
def export_sqlite(rep: Path) -> Path | None:
    db = rep.with_suffix(".sqlite")
    if db.exists() and db.stat().st_mtime >= rep.stat().st_mtime:
        return db
    p = subprocess.run([NSYS, "export", "--type", "sqlite", "--force-overwrite", "true", "--output", str(db), str(rep)],
                       capture_output=True, text=True)
    return db if p.returncode == 0 and db.exists() else None


def gpu_rows(session: Path, metrics: set[str] | None = None) -> list[tuple]:
    rows = []
    for seg in segments(session):
        if seg["kind"] != "gpu" or not seg.get("report"):
            continue
        rep = session / seg["file"]
        db = export_sqlite(rep) if rep.exists() else None
        if db is None:
            continue
        off = seg["start"]["w"] - seg["start"]["t"]                     # wall - mono at the segment's start
        con = sqlite3.connect(str(db))
        try:
            start_w = con.execute("SELECT utcEpochNs FROM TARGET_INFO_SESSION_START_TIME").fetchone()[0]
            names = {(m, t): n for m, n, t in con.execute(
                "SELECT metricId, metricName, typeId FROM TARGET_INFO_GPU_METRICS")}
            for ts, type_id, mid, value in con.execute(
                    "SELECT timestamp, typeId, metricId, value FROM GPU_METRICS ORDER BY timestamp"):
                name = names.get((mid, type_id), str(mid))
                if metrics is None or name in metrics:
                    rows.append((start_w + ts - off, name, value))
        finally:
            con.close()
    return rows


def metric_names(session: Path) -> list[str]:
    for seg in segments(session):
        if seg["kind"] == "gpu" and seg.get("report"):
            db = export_sqlite(session / seg["file"])
            if db:
                con = sqlite3.connect(str(db))
                try:
                    return [n for (n,) in con.execute("SELECT DISTINCT metricName FROM TARGET_INFO_GPU_METRICS")]
                finally:
                    con.close()
    return []


# -- windows ------------------------------------------------------------------------------------------------------------
def window_perf(rows: list[tuple], t0: int, t1: int, cpus: set[int] | None = None) -> dict:
    """Sum of each event over the intervals that end inside [t0, t1), over the given cpus; plus the seconds covered."""
    out: dict = {}
    ts = set()
    for t, cpu, event, value, _ in rows:
        if t0 <= t < t1 and (cpus is None or cpu in cpus):
            out[event] = out.get(event, 0.0) + value
            ts.add(t)
    out["_seconds"] = len(ts) * 1.0
    return out


def window_gpu(rows: list[tuple], t0: int, t1: int) -> dict:
    """Per metric: sum and mean of the samples inside [t0, t1), and the number of samples."""
    acc: dict = {}
    for t, name, value in rows:
        if t0 <= t < t1:
            s = acc.setdefault(name, [0.0, 0])
            s[0] += value
            s[1] += 1
    return {k: {"sum": v[0], "mean": v[0] / v[1], "n": v[1]} for k, v in acc.items()}
