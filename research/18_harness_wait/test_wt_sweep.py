#!/usr/bin/env python3
"""Unit test of wt_sweep.WindowCell's counted queue: by default each spec runs once; with the condition key
cycle_specs (topic 19) the queue starts the specs again, in order, as pass 2, 3, ... (no server, lanes or sessions).

    python research/18_harness_wait/test_wt_sweep.py
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
import wt_sweep  # noqa: E402

import mu_sweep  # noqa: E402  (on the path once wt_sweep is imported)


def bare(cycle: bool, specs=("S000", "S001")) -> wt_sweep.WindowCell:
    """A WindowCell holding only the queue's state."""
    cell = object.__new__(wt_sweep.WindowCell)
    cell.c = {"name": "t_q", **({"cycle_specs": True} if cycle else {})}
    cell.args = types.SimpleNamespace(dispatch_until=0.0)
    cell.counted = list(specs)
    cell.queue = cell._todo()
    cell.lock, cell.stop = threading.Lock(), threading.Event()
    cell.arrival = cell.running_counted = 0
    cell.window_end = time.time() + 3600
    cell.passes = 1
    return cell


def test_default_runs_each_spec_once():
    cell = bare(False)
    got = [cell.next_counted() for _ in range(3)]
    assert [g[0] for g in got[:2]] == ["S000", "S001"] and got[2] is None, got
    assert cell.label("S001") == "MU-t_q-S001"


def test_cycle_specs_queues_the_next_pass_in_order():
    cell = bare(True)
    specs = [cell.next_counted()[0] for _ in range(5)]
    assert specs == ["S000", "S001", "S000@2", "S001@2", "S000@3"], specs
    assert cell.passes == 3 and cell.queue == [("S001@3", 1)], cell.queue
    assert [cell.label(s) for s in specs[:4]] == ["MU-t_q-S000", "MU-t_q-S001", "MU-t_q-S000-p2", "MU-t_q-S001-p2"]
    cell.window_end = time.time() - 1                    # the window is over: nothing more is handed out
    assert cell.next_counted() is None


def test_cycle_hands_the_session_the_manifest_spec():
    cell = bare(True)
    seen = {}
    real = mu_sweep.Cell.launch, mu_sweep.Cell.finish
    mu_sweep.Cell.launch = lambda self, lane, spec, label, *rest: seen.setdefault("launch", (spec, label))
    mu_sweep.Cell.finish = lambda self, spec, label, *rest: seen.setdefault("finish", (spec, label))
    try:
        cell.launch(Path("/nonexistent"), "S001@2", "MU-t_q-S001-p2", 1, 7)
        cell.finish("S001@2", "MU-t_q-S001-p2", Path("/nonexistent"), None, 0.0, 0, 1, 7, False, "counted")
    finally:
        mu_sweep.Cell.launch, mu_sweep.Cell.finish = real
    assert seen == {"launch": ("S001", "MU-t_q-S001-p2"), "finish": ("S001", "MU-t_q-S001-p2")}, seen


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
