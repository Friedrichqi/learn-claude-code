#!/usr/bin/env python3
"""E0a at N > 1, first order: how much GPU time and lead-call time harness edits cost on a shared server.

Input: topic 11's committed tables (research/11_multiuser_kv/data/multiuser_live/multiuser_tables.md), the
snip-off configuration (H200, Qwen3-32B, 40,960-token window, rolling snip off, original template):
  T3   lead-call prompt tokens by class (hit / evicted / after edit / new) and GPU time by class
  T4   where a lead call's server time goes (own fresh prefill, others' fresh prefill = interference, ...)
  T4b  lead-call latency and the solo model

First-order attribution (stated in the README): an edit's share of fresh prefill time equals its share of
fresh prefill tokens, edit_frac = after_edit / (after_edit + new), applied to
  GPU time          GPU fresh prefill share x edit_frac            (share of wall; busy share = / (1 - idle))
  critical path     (own fresh pf + others' fresh pf) x edit_frac / lead-call latency
Others' fresh prefill is the interference a lead call's decode suffers from co-scheduled prefill chunks
(T4's "others' fresh pf"); at N >= 4 the GPU is never idle, so that time is on the call's critical path.
Edits re-prefill 12-16k tokens deep in the prompt while new tokens sit at its end, so per-token attention cost
differs; the exact split needs the raw step logs (hs_stall.py / a refined census once Drive data is pulled).

    python research/17_harness_aware_serving/hs_census_mu.py      -> data/tables/E0a_census_mu.md
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_common as hc  # noqa: E402

TABLES_11 = hc.RESEARCH / "11_multiuser_kv" / "data" / "multiuser_live" / "multiuser_tables.md"
LIVE_CELLS = ["n01a", "n01b", "n04", "n08", "n12", "n16", "n32"]      # the N sweep; variants listed after
VARIANTS = ["n08kvh", "n08mem", "n08ts", "s3gate16", "s3gate32"]


def md_tables(path: Path) -> dict[str, list[dict]]:
    """'## T3 · ...' sections -> list of row dicts keyed by the header cells."""
    out: dict[str, list[dict]] = {}
    name, header = None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            name = line[3:].split(" ")[0]
            header = None
            continue
        if name is None or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
            out[name] = []
        elif set(line.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        else:
            out[name].append(dict(zip(header, cells)))
    return out


def num(row: dict, key: str) -> float:
    return float(row[key])


def census(t: dict[str, list[dict]]) -> list[dict]:
    t3 = {r["cond"]: r for r in t["T3"]}
    t4 = {r["cond"]: r for r in t["T4"]}
    t4b = {r["cond"]: r for r in t["T4b"]}
    rows = []
    for cond in LIVE_CELLS + VARIANTS:
        if cond not in t3 or cond not in t4 or cond not in t4b:
            continue
        a, b, c = t3[cond], t4[cond], t4b[cond]
        edit, new = num(a, "after edit"), num(a, "new")
        frac = edit / (edit + new) if edit + new else 0.0
        idle = num(a, "GPU idle")
        gpu_fresh = num(a, "GPU fresh prefill")
        gpu_edit = gpu_fresh * frac
        own, others = num(b, "own fresh pf"), num(b, "others' fresh pf")
        latency = num(c, "latency")
        crit_edit = (own + others) * frac
        rows.append({"cond": cond, "N": int(a["N"]), "edit_frac": frac, "gpu_fresh": gpu_fresh, "idle": idle,
                     "gpu_edit_wall": gpu_edit, "gpu_edit_busy": gpu_edit / (1 - idle) if idle < 1 else 0.0,
                     "gpu_evict": num(a, "GPU evict recompute"), "own_pf": own, "others_pf": others,
                     "latency": latency, "crit_edit_s": crit_edit, "crit_edit_share": crit_edit / latency})
    return rows


def main() -> None:
    rows = census(md_tables(TABLES_11))
    lines = ["| cell | N | edit share of fresh prefill tokens | GPU fresh prefill (share of wall) | GPU idle | "
             "**edit work, share of GPU wall** | edit work, share of busy GPU | GPU on eviction recompute | "
             "own fresh pf (s/call) | others' fresh pf (s/call) | lead-call latency (s) | edit time on the call's path (s) | "
             "**share of the call's critical path** |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['cond']} | {r['N']} | {r['edit_frac']:.0%} | {r['gpu_fresh']:.2f} | {r['idle']:.2f} | "
                     f"**{r['gpu_edit_wall']:.0%}** | {r['gpu_edit_busy']:.0%} | {r['gpu_evict']:.2f} | "
                     f"{r['own_pf']:.2f} | {r['others_pf']:.2f} | {r['latency']:.2f} | {r['crit_edit_s']:.2f} | "
                     f"**{r['crit_edit_share']:.0%}** |")
    sweep = [r for r in rows if r["cond"] in LIVE_CELLS]
    best_gpu = max(sweep, key=lambda r: r["gpu_edit_wall"])
    best_cp = max(sweep, key=lambda r: r["crit_edit_share"])
    d1 = best_gpu["gpu_edit_wall"] >= 0.20 or best_cp["crit_edit_share"] >= 0.05
    lines += ["", f"**D1 reading (snip-off, as run, first order): {'PASS' if d1 else 'FAIL'}.** Largest GPU share "
              f"{best_gpu['gpu_edit_wall']:.0%} ({best_gpu['cond']}; threshold 20 %), largest critical-path share "
              f"{best_cp['crit_edit_share']:.0%} ({best_cp['cond']}; threshold 5 %). The pre-registered counterfactual "
              "(snip hysteresis) does not apply to snip-off runs, whose edits are 88-92 % proactive compaction at the "
              "window (T7); see E0f for R1."]
    note = ("Source: research/11_multiuser_kv/data/multiuser_live/multiuser_tables.md (T3, T4, T4b; 2,033,364 steps, 903 "
            "sessions). First-order attribution: edit time = fresh prefill time x the edit share of fresh prefill tokens "
            "(module docstring). Variant cells: kvh = halved KV pool, mem = memory on, ts = per-second timestamp on, "
            "s3gate = session gate K = 8.")
    out = hc.write_table("E0a_census_mu", "E0a · edit work on a shared server (topic 11 cells, first order)", lines, note)
    print(out)
    for r in sweep:
        print(f"{r['cond']:6s} N={r['N']:2d} edit/fresh={r['edit_frac']:.2f} gpu_edit={r['gpu_edit_wall']:.3f} "
              f"crit={r['crit_edit_share']:.3f}")


if __name__ == "__main__":
    main()
