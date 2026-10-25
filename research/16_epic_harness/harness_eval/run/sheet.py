#!/usr/bin/env python3
"""Check the lab GPU sheet (read-only CSV export) before using a GPU.

    sheet.py check --gpus 0,1 --start "2026-10-11 22:00" --end "2026-10-12 08:00"   (US Eastern)
    sheet.py wait  --gpus 0 --start ... --end ...    poll every 5 min until claimed by Yijiahao, then exit 0
    sheet.py show  --days 10/11,10/12

A slot counts as ours only if its cell names Yijiahao and nobody else. Tabs: one per week (Mon-Sun).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

SHEET = "https://docs.google.com/spreadsheets/d/1ZUfNV5mGOmWK_-Tzc6zMmV6LWqNUFn41sG-XcCtyIog/export?format=csv&gid={gid}"
WEEK_GID = {dt.date(2026, 10, 5): 700000002, dt.date(2026, 10, 12): 700000003, dt.date(2026, 10, 19): 700000004}
ME = "Yijiahao"
ET = ZoneInfo("America/New_York")


def tab(monday: dt.date) -> dict[tuple[str, str], list[str]]:
    gid = WEEK_GID[monday]
    raw = urllib.request.urlopen(SHEET.format(gid=gid), timeout=30).read().decode()
    rows = list(csv.reader(io.StringIO(raw)))
    out, hdr, date = {}, None, None
    for r in rows:
        if r and r[0].strip().lower() == "date":
            hdr = r
            continue
        if hdr is None or len(r) < 3:
            continue
        if r[0].strip():
            date = r[0].strip()
        out[(date, r[1].strip())] = [c.strip() for c in r[2:10]]
    return out


def slots(start: dt.datetime, end: dt.datetime):
    t = start
    while t < end:
        yield t
        t += dt.timedelta(minutes=30)


def label(t: dt.datetime) -> tuple[str, str]:
    day = f"{t.strftime('%a')} {t.month}/{t.day}"
    hour = t.strftime("%I:%M %p").lstrip("0")
    return day, hour


def check(gpus: list[int], start: dt.datetime, end: dt.datetime) -> tuple[bool, list[str]]:
    tabs: dict[dt.date, dict] = {}
    problems = []
    for t in slots(start, end):
        monday = t.date() - dt.timedelta(days=t.weekday())
        if monday not in tabs:
            tabs[monday] = tab(monday)
        day, hour = label(t)
        cells = tabs[monday].get((day, hour))
        if cells is None:
            problems.append(f"{day} {hour}: row not found")
            continue
        for g in gpus:
            cell = cells[g] if g < len(cells) else ""
            if cell != ME:
                problems.append(f"{day} {hour} GPU{g}: {cell or 'unclaimed'}")
    return not problems, problems


def parse(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=ET)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "wait"])
    ap.add_argument("--gpus", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    gpus = [int(g) for g in a.gpus.split(",")]
    start, end = parse(a.start), parse(a.end)
    while True:
        ok, problems = check(gpus, start, end)
        if ok:
            print(f"claimed: GPUs {gpus} {a.start} -> {a.end} ET")
            sys.exit(0)
        if a.cmd == "check":
            print(f"NOT claimed ({len(problems)} slot problems), e.g. {problems[:4]}")
            sys.exit(1)
        print(f"{time.strftime('%H:%M:%S')} waiting for claims: {problems[:2]}", flush=True)
        time.sleep(300)


if __name__ == "__main__":
    main()
