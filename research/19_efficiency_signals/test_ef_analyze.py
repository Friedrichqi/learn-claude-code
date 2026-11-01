#!/usr/bin/env python3
"""Unit tests of ef_analyze's own logic (no cells needed):

  * the M-turn edit checks on small diffs (M1 BUILD_TAG + changelog, M2 the _v2 rename, M3 docstrings in the module);
  * the decision rules on synthetic 10-min blocks: recovers / hurts / neither, R2, R3;
  * the analysis parses the [cost] receipt exactly as s15's hold_runtime writes it;
  * exact re-reads: same (path, offset, limit), still in full, no write in between.

    /data/fritz/venv/bin/python research/19_efficiency_signals/test_ef_analyze.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
REPO = DIR.parents[1]
sys.path.insert(0, str(DIR))
sys.path.insert(0, str(DIR.parent))
import ef_analyze as ea  # noqa: E402


def hold_runtime():
    spec = importlib.util.spec_from_file_location("ef_test_hold", REPO / "s15_integrated_harness" / "hold_runtime.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def diff(path: str, added: list[str]) -> str:
    head = f"diff -ruN a/{path} b/{path}\n--- /seeds/{path}\t2026-10-08 15:06:56 +0000\n+++ /lanes/x/{path}\t2026-10-26 01:00:00 +0000\n@@ -1,1 +1,2 @@\n"
    return head + "".join(f"+{line}\n" for line in added)


def test_m_checks():
    m2 = {"func": "literal_values", "def_file": "_typing.py"}
    assert ea.m_passed("M2", m2, diff("profiling_sandbox/typer/_typing.py", ["def literal_values_v2(x):"]))
    assert not ea.m_passed("M2", m2, diff("profiling_sandbox/typer/_typing.py", ["def other(x):"]))
    m3 = {"module": "core.py", "missing": 51}
    assert ea.m_passed("M3", m3, diff("profiling_sandbox/typer/core.py", ['    """Split the option."""']))
    assert not ea.m_passed("M3", m3, diff("profiling_sandbox/typer/main.py", ['    """Split the option."""']))
    assert not ea.m_passed("M3", m3, diff("profiling_sandbox/typer/core.py", ["x = 1"]))
    m1 = diff("profiling_sandbox/jinja2/compiler.py", ["BUILD_TAG = 'profiling'"]) + \
        diff("profiling_sandbox/jinja2/CHANGES_BUILD_TAG.md", ["- compiler.py"])
    assert ea.m_passed("M1", None, m1)
    assert not ea.m_passed("M1", None, diff("profiling_sandbox/jinja2/compiler.py", ["BUILD_TAG = 'profiling'"]))


def row(level: float, n: int = 9, hit: float = 0.5) -> dict:
    blocks = [level * (0.9 + 0.2 * (i % 2)) for i in range(n)]          # +-10 % around the level
    return {"turn_blocks": blocks, "work_blocks": [b * 40 for b in blocks], "hit_rate": hit}


def test_decisions():
    t = {"w16_none": row(40), "w16_sgate": row(130), "w16_m_wait": row(100), "w16_m_tools": row(30),
         "w32_none": row(60), "w32_sgate": row(150), "w32_m_wait": row(62), "w32_m_tools": row(64),
         "w08_none": row(140, hit=0.90), "w08_m_tools": row(139, hit=0.895)}
    d = ea.decisions(t, 8)
    assert d["R1"]["m_wait@16"]["verdict"] == "recovers", d["R1"]["m_wait@16"]
    assert d["R1"]["m_tools@16"]["verdict"] == "hurts", d["R1"]["m_tools@16"]
    assert d["R1"]["m_wait@32"]["verdict"] == "neither" and d["R1"]["m_tools@32"]["verdict"] == "neither"
    assert abs(d["R1"]["m_wait@16"]["R"]["R"] - 60 / 90) < 1e-9
    assert d["R2"]["16"]["verdict"] == "toolbox worse" and d["R2"]["32"]["verdict"] == "no clear difference"
    assert d["R3"]["verdict"] == "no harm", d["R3"]
    t["w08_m_tools"] = row(139, hit=0.85)                                # 5 points of hit rate lost
    assert ea.decisions(t, 8)["R3"]["verdict"] == "harm"


def test_receipt_text_parses():
    hold = hold_runtime()
    hold._state["last_call"] = {"prompt": 24512, "hit": 18304, "out": 412, "secs": 14.2}
    line = hold.receipt_line({"me": {"last": {"np": 24512, "ideal": 23110, "hit": 18304, "wait_s": 4.1}}})
    m = ea.COST_RE.search(line)
    assert m and [ea.num(m.group(i)) for i in (1, 2, 3, 4)] == [24512, 18304, 4806, 1402] and m.group(5) == "4.1", line
    short = hold.receipt_line(None)
    m = ea.COST_RE.search(short)
    assert m and ea.num(m.group(1)) == 24512 and m.group(3) is None, short


def test_rereads():
    use = lambda i, name, inp: {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}  # noqa: E731
    res = lambda i, text: {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}  # noqa: E731
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": [use(1, "read_file", {"path": "a.py"})]},
            {"role": "user", "content": [res(1, "A" * 50)]},
            {"role": "assistant", "content": [use(2, "read_file", {"path": "a.py"}), use(3, "read_file", {"path": "b.py"})]},
            {"role": "user", "content": [res(2, "A" * 50), res(3, "B")]}]
    assert ea.rereads(msgs) == (1, 2)
    edited = msgs[:3] + [{"role": "assistant", "content": [use(4, "edit_file", {"path": "a.py"})]},
                         {"role": "user", "content": [res(4, "ok")]}] + msgs[3:]
    assert ea.rereads(edited) == (0, 2), "a write in between makes the re-read necessary"


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
