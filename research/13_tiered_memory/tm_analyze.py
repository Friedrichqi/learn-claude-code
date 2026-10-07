#!/usr/bin/env python3
"""Analysis of research/13_tiered_memory's live runs (offline; heavy runs as a CPU job: tm_cpu.sbatch).

    python research/13_tiered_memory/tm_analyze.py probe [--job JOB]     # S2a: node, tiers, vLLM capacity / rates
    python research/13_tiered_memory/tm_analyze.py d1 [--cell s2d]       # S2 D1 smoke
    python research/13_tiered_memory/tm_analyze.py b1 [--cell s2b]       # S2 B1 smoke
    python research/13_tiered_memory/tm_analyze.py all
Tables go to data/tables/S2_*.md, numbers to data/s2/*.json.

Sources per level: request captures (11's mu_session: t_send, t_recv, request id, response), the scheduler log
(11's MUScheduler records + 13's tm* records), tm_server_ext's log (ext.jsonl: one line per call / prefetch / hint),
tm_client's log (client.jsonl), the pipeline markers (which server served which level, its window).
Definitions:
  front end      client send -> scheduler arrival (arr.wall - t_send); split at vLLM's request.arrival_time (arr.fe,
                 stamped when chat rendering starts): SDK + HTTP + parse | template + tokenise + hand-off to the
                 engine (+ the wait for the running engine step, which D1 does not touch)
  prefilled      prompt tokens the engine computed for a call (np - first-admission hit); D1 also removes the
                 re-prefill of the previous reply
  evicted        arrival ideal hit - first-admission hit (11's definition): recompute caused by eviction
  loaded         first-admission external hit (num_external_cached_tokens): tokens restored from the CPU tier
  TTFT (engine)  first token - scheduler arrival
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: E402,F401

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
TABLES = DATA / "tables"
OUT = DATA / "s2"
KV_BYTES = {"Qwen/Qwen3-32B": 262144, "Qwen/Qwen3-8B": 147456}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        alt = path.with_name(path.name + ".xz")
        if alt.exists():
            path = alt
        else:
            return []
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def f(x, d=1):
    return "—" if x is None else f"{x:,.{d}f}"


def pc(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f} %"


def div(a, b):
    return a / b if b else None


# -- probe ---------------------------------------------------------------------------------------------------------
def probe(job: str | None) -> str:
    root = DATA / "probe"
    jobs = sorted(p for p in root.glob("*") if p.is_dir())
    d = (root / job) if job else (jobs[-1] if jobs else None)
    if d is None or not d.exists():
        return "no probe data\n"
    load = lambda n: json.loads((d / n).read_text()) if (d / n).exists() else {}
    sysi, pcie, blocks, flash = load("calib_sys.json"), load("calib_pcie.json"), load("calib_blocks.json"), load("calib_flash.json")
    lines = [f"# S2a · The RTX PRO 6000 node and what moving KV costs there (job {d.name})", ""]
    cpu = next((l.split(":", 1)[1].strip() for l in sysi.get("lscpu", "").splitlines() if l.startswith("Model name")), "?")
    ncpu = next((l.split(":", 1)[1].strip() for l in sysi.get("lscpu", "").splitlines() if l.startswith("CPU(s):")), "?")
    mem = (sysi.get("free_gib", "").splitlines() or ["", ""])[1].split()[1:2] if sysi.get("free_gib") else ["?"]
    lines += ["| fact | value |", "|---|---|", f"| host | {sysi.get('host')} |", f"| GPU (index, name, memory, PCIe gen now/max, width now/max, power) | {sysi.get('gpus')} |",
              f"| CPU | {cpu}, {ncpu} CPUs |", f"| host memory | {mem[0] if mem else '?'} GiB |",
              f"| local disks | {' · '.join(l.strip() for l in sysi.get('lsblk', '').splitlines()[1:])} |",
              f"| /dev/shm | {next((m['size_gib'] for m in sysi.get('mounts', []) if m['mount'] == '/dev/shm'), '?')} GiB tmpfs |", ""]
    if pcie.get("rows"):
        lines += ["**One large copy over PCIe** (GB/s):", "", "| size | pinned H2D | pinned D2H | pageable H2D | pageable D2H |", "|---|---|---|---|---|"]
        by = defaultdict(dict)
        for r in pcie["rows"]:
            by[r["bytes"]]["p" if r["pinned"] else "u"] = r
        for size, r in sorted(by.items()):
            lines.append(f"| {size >> 20} MiB | {r['p']['h2d_gbs']} | {r['p']['d2h_gbs']} | {r['u']['h2d_gbs']} | {r['u']['d2h_gbs']} |")
        lines.append("")
    if blocks.get("rows"):
        lines += ["**vLLM's block-copy kernel** (`swap_blocks_batch`, one cuMemcpyBatchAsync of random pages; GB/s):", "",
                  "| page size | copies | DRAM → GPU | GPU → DRAM |", "|---|---|---|---|"]
        for r in blocks["rows"]:
            lines.append(f"| {r['chunk_bytes'] >> 10} KiB | {r['copies']:,} | {r['h2d_gbs']} | {r['d2h_gbs']} |")
        if blocks.get("restore_23k_32b_s"):
            lines.append(f"\nA 23k-token Qwen3-32B context (1,438 blocks × 64 layers × K and V pages of 32 KiB = 5.6 GiB) "
                         f"takes **{blocks['restore_23k_32b_s'] * 1000:.0f} ms** through the kernel alone.")
        lines.append("")
    if flash.get("rows"):
        lines += ["**Local storage** (O_DIRECT where supported, 4 MiB I/Os; GB/s):", "",
                  "| directory | filesystem | sequential write | sequential read | 4 MiB files write | 4 MiB files read (random order) |",
                  "|---|---|---|---|---|---|"]
        for r in flash["rows"]:
            lines.append(f"| {r['dir']} | {r.get('fs')} | {r.get('seq_write_gbs')} | {r.get('seq_read_gbs')} | "
                         f"{r.get('files_write_gbs')} | {r.get('files_read_gbs')} |")
        lines.append("")
    res = {"job": d.name, "models": {}}
    for p in sorted(d.glob("probe_*.json")):
        m = json.loads(p.read_text())
        res["models"][m["model"]] = m
        mm = m.get("memory", {})
        lines += [f"## {m['model']}", "",
                  f"Weights {f(mm.get('weights_gib'), 2)} GiB; KV pool {f(mm.get('kv_mem_gib'), 2)} GiB = "
                  f"**{f(mm.get('kv_tokens'), 0)} tokens** (concurrency {f(mm.get('max_concurrency'), 2)}× at the "
                  f"40,960-token window); CUDA graphs {f(mm.get('graph_gib'), 2)} GiB; CPU tier {f(mm.get('offload_gb'), 1)} GB.", "",
                  "| prompt tokens | prefill (recompute) latency, s | tokens/s |", "|---|---|---|"]
        for r in m.get("prefill", []):
            lines.append(f"| {r['tokens']:,} | {min(r['latency_s']):.3f} | {r['tok_per_s']:,} |")
        dec = m.get("decode", {})
        lines += ["", f"Decode at batch 1: **{dec.get('ms_per_token')} ms per token**.", ""]
        if m.get("restore"):
            kvb = KV_BYTES.get(m["model"], 0)
            lines += ["| tokens | recompute (cold), s | GPU hit, s | restore from DRAM, s | DRAM restore above a GPU hit, s | effective GB/s | recompute / DRAM restore |",
                      "|---|---|---|---|---|---|---|"]
            for r in m["restore"]:
                extra = r["dram_s"] - r["gpu_hit_s"]
                lines.append(f"| {r['tokens']:,} | {r['cold_s']:.3f} | {r['gpu_hit_s']:.3f} | {r['dram_s']:.3f} | {extra:.3f} | "
                             f"{f(r['tokens'] * kvb / extra / 1e9 if extra > 0 else None, 1)} | {f(r['cold_s'] / r['dram_s'], 1)}× |")
            lines.append("")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "probe.json").write_text(json.dumps({"sys": {k: v for k, v in sysi.items() if k not in ("lscpu",)},
                                                "pcie": pcie, "blocks": blocks, "flash": flash, **res}, indent=1))
    return "\n".join(lines) + "\n"


# -- live levels -----------------------------------------------------------------------------------------------------
class Level:
    """One level: captures, scheduler log (mu_analyze.Server of the serving server), tm logs, markers."""

    def __init__(self, cell: str, name: str):
        import mu_analyze
        self.cell, self.name = cell, name
        self.dir = DATA / "cells" / cell / "levels" / name
        self.caps = []
        for p in sorted(self.dir.glob("runs/*/*.requests.jsonl*")):
            self.caps += [c for c in read_jsonl(p) if c.get("t_send") and c.get("t_recv")]
        self.client = read_jsonl(self.dir / "client.jsonl")
        self.srv_dir, self.window = None, None
        for sd in sorted((DATA / "cells" / cell / "server").glob("*/")):
            for mk in read_jsonl(sd / "markers.jsonl"):
                if mk.get("ev") == "level_start" and mk.get("level") == name:
                    self.srv_dir = sd
                    self.window = (mk["wall"] + mk.get("warmup_s", 0), mk.get("hard"))
        self.srv = mu_analyze.Server(self.srv_dir) if self.srv_dir and (self.srv_dir / "sched.jsonl.xz").exists() else None
        self.ext = [r for r in read_jsonl(self.srv_dir / "ext.jsonl") if r] if self.srv_dir else []
        rids = {c["request_id"] for c in self.caps}
        self.ext_calls = {r["rid"]: r for r in self.ext if r.get("ev") == "call" and r.get("rid") in rids}
        self.tm = defaultdict(list)
        if self.srv_dir:
            for r in read_jsonl(self.srv_dir / "sched.jsonl"):
                if str(r.get("k", "")).startswith("tm"):
                    self.tm[r["k"]].append(r)

    def calls(self) -> list[dict]:
        """Per captured call: timings joined with the scheduler's arrival / admission / finish."""
        out = []
        for c in self.caps:
            rec = self.srv.by_ext.get(c["request_id"]) if self.srv else None
            row = {"rid": c["request_id"], "lead": "~ld~" in c["request_id"], "dur": c["t_recv"] - c["t_send"],
                   "t_send": c["t_send"], "ext": self.ext_calls.get(c["request_id"])}
            if rec is not None:
                a = rec["arr"]
                first = [x for x in rec["adm"] if not x["res"]]
                row.update(np=a["np"], ideal=a["ideal"], hit_now=a["hit_now"], fe_in=a["wall"] - c["t_send"],
                           http=(a["fe"] - c["t_send"]) if a.get("fe") else None,
                           hit=first[0]["hit"] if first else None, he=(first[0].get("he") or 0) if first else 0,
                           load=first[0].get("load") if first else None,
                           ttft=(rec["tok1"] - a["t"]) if rec["tok1"] else None,
                           out=rec["fin"]["no"] if rec["fin"] else None)
                row["render"] = row["fe_in"] - row["http"] if row["http"] is not None else None
                if row["hit"] is not None:
                    row["prefilled"] = a["np"] - row["hit"]
                    row["evicted"] = max(0, max(row["hit"], a["ideal"]) - row["hit"])
            out.append(row)
        return out

    def in_window(self, t: float) -> bool:
        return self.window is None or (self.window[0] <= t <= (self.window[1] or 1e18))


def level_of(ext_id: str) -> str:
    label = ext_id.split("~", 1)[0]
    parts = label.split("-")
    return parts[1] if len(parts) > 1 and parts[0] == "MU" else label


def reply_reprefill(srv, level: str | None = None) -> tuple[list[int], list[int]]:
    """Per append-only call (the prompt extends the chain's previous prompt): tokens of the previous reply that were
    prefilled again (previous prompt + reply, minus the cache hit; at most reply + 4), and the reply lengths."""
    by_chain = defaultdict(list)
    for ri, rec in srv.req.items():
        if level is None or level_of(rec["arr"]["id"]) == level:
            by_chain[rec["arr"]["c"]].append(ri)
    re_, gen = [], []
    for rs in by_chain.values():
        rs.sort(key=lambda r: srv.req[r]["arr"]["t"])
        for a, b in zip(rs, rs[1:]):
            A, B = srv.req[a], srv.req[b]
            if A["fin"] is None or not B["adm"]:
                continue
            first = [x for x in B["adm"] if not x["res"]]
            if not first:
                continue
            np_a, no_a, hit = A["arr"]["np"], A["fin"]["no"], first[0]["hit"]
            if B["arr"]["np"] <= np_a or B["arr"]["lcp"] < (np_a - 4) // 16 * 16 - 16:
                continue                                    # not an append (an edit, or a new chain state)
            if hit < (np_a - 4) // 16 * 16 - 16:
                continue                                    # the previous prompt itself was evicted: not this effect
            re_.append(max(0, min(no_a + 4, np_a + no_a - hit)))
            gen.append(no_a)
    return re_, gen


def d1(cell: str) -> str:
    import tm_workloads as tw
    names = tw.CELLS[cell]["levels"]
    lines = [f"# S2 · D1 smoke: session-resident token state (cell {cell}, {tw.CELLS[cell]['model']}, RTX PRO 6000)", "",
             "| level | arm | N | calls (lead) | client modes | SDK+HTTP+parse ms (p50) | template+tokenise+hand-off ms (p50) | front end ms per call (mean) | "
             "front end, share of call time | prefilled tokens per call (mean) | call s (p50 / p95) | engine TTFT s (p50) | lead calls per h (window) |",
             "|" + "---|" * 13]
    res = {}
    for name in names:
        lv = Level(cell, name)
        if not lv.caps:
            lines.append(f"| {name} | — | | no data | | | | | | | | | |")
            continue
        calls = lv.calls()
        lead = [c for c in calls if c["lead"]]
        modes = Counter(c["mode"] for c in lv.client)
        win = [c for c in lead if lv.in_window(c["t_send"])]
        span = (lv.window[1] - lv.window[0]) if lv.window and lv.window[1] else None
        fe = [c["fe_in"] for c in calls if c.get("fe_in") is not None]
        r = {"calls": len(calls), "lead": len(lead), "modes": dict(modes),
             "http_ms_p50": 1000 * (q([c.get("http") for c in calls], 0.5) or 0),
             "render_ms_p50": 1000 * (q([c.get("render") for c in calls], 0.5) or 0),
             "fe_ms_mean": 1000 * (mean(fe) or 0), "fe_share": div(sum(fe), sum(c["dur"] for c in calls if c.get("fe_in") is not None)),
             "prefilled_mean": mean([c.get("prefilled") for c in calls]), "dur_p50": q([c["dur"] for c in calls], 0.5),
             "dur_p95": q([c["dur"] for c in calls], 0.95), "ttft_p50": q([c.get("ttft") for c in calls], 0.5),
             "lead_per_h": div(len(win) * 3600, span), "n": tw.CONDITIONS[name]["n"], "arm": tw.CONDITIONS[name]["tm"].get("arm"),
             "client_diff_ms_p50": q([c.get("diff_ms") for c in lv.client], 0.5),
             "resyncs": sum(1 for c in lv.client if c.get("resync")),
             "delta_render_ms_p50": q([e.get("render_ms") for e in lv.ext_calls.values() if e.get("mode") in ("delta", "verify")], 0.5),
             "ids_mismatch": sum(1 for e in lv.ext_calls.values() if e.get("ids_mismatch"))}
        if lv.srv:
            rr, gg = reply_reprefill(lv.srv, name)
            r["reply_reprefill_mean"], r["reply_mean"], r["append_calls"] = mean(rr), mean(gg), len(rr)
        ver = [e["verify"] for e in lv.ext_calls.values() if e.get("verify")]
        if ver:
            r["verify"] = {"calls": len(ver), "ok": sum(v["ok"] for v in ver), "other_diff_blocks": sum(v["other_diff"] for v in ver),
                           "assistant_blocks": sum(v["assistant_blocks"] for v in ver), "assistant_diff_blocks": sum(v["assistant_diff"] for v in ver),
                           "assistant_tok_d1": sum(v["assistant_tok_d1"] for v in ver), "assistant_tok_full": sum(v["assistant_tok_full"] for v in ver),
                           "first_bad": next((v["first_other_diff"] for v in ver if v["first_other_diff"]), None)}
        res[name] = r
        lines.append(f"| {name} | {r['arm']} | {r['n']} | {r['calls']} ({r['lead']}) | {' '.join(f'{k}:{v}' for k, v in sorted(modes.items())) or 'full'} | "
                     f"{f(r['http_ms_p50'], 0)} | {f(r['render_ms_p50'], 0)} | {f(r['fe_ms_mean'], 0)} | {pc(r['fe_share'])} | {f(r['prefilled_mean'], 0)} | "
                     f"{f(r['dur_p50'], 2)} / {f(r['dur_p95'], 2)} | {f(r['ttft_p50'], 3)} | {f(r['lead_per_h'], 0)} |")
    lines += ["", "| level | append-only calls | previous reply, tokens (mean) | previous reply re-prefilled, tokens per call (mean) |",
              "|---|---|---|---|"]
    for name, r in res.items():
        lines.append(f"| {name} | {f(r.get('append_calls'), 0)} | {f(r.get('reply_mean'), 1)} | {f(r.get('reply_reprefill_mean'), 1)} |")
    lines += ["", "*Key.* **front end**: client send → scheduler arrival; its first part is the SDK, HTTP and request parsing "
              "(send → vLLM starts rendering), the second the chat template, tokenisation and hand-off (with D1: the stored ids "
              "plus a render of only the new messages). **prefilled**: prompt tokens the engine computed (prompt − cache hit). "
              "**engine TTFT**: scheduler arrival → first token.", ""]
    for name, r in res.items():
        if r.get("verify"):
            v = r["verify"]
            lines += [f"**D1 exactness ({name})**: {v['calls']} calls checked against vLLM's own full render: {v['ok']} identical outside the "
                      f"assistant turns and at every turn boundary ({v['other_diff_blocks']} differing blocks counted over all calls). Assistant turns: {v['assistant_diff_blocks']} of "
                      f"{v['assistant_blocks']} differ as expected (D1 keeps each reply as generated: {v['assistant_tok_d1']:,} tokens vs "
                      f"{v['assistant_tok_full']:,} re-rendered).", ""]
            if v["first_bad"]:
                lines.append(f"First non-assistant difference: `{json.dumps(v['first_bad'])[:400]}`\n")
        if r.get("modes", {}).get("delta"):
            lines.append(f"**{name}**: client diff p50 {f(r['client_diff_ms_p50'], 2)} ms; server delta render p50 {f(r['delta_render_ms_p50'], 1)} ms; "
                         f"resyncs {r['resyncs']}; engine prompt ≠ D1 ids {r['ids_mismatch']}.\n")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"d1_{cell}.json").write_text(json.dumps(res, indent=1, default=str))
    return "\n".join(lines) + "\n"


def b1(cell: str) -> str:
    import tm_workloads as tw
    names = tw.CELLS[cell]["levels"]
    lines = [f"# S2 · B1 smoke: tiered KV with agent hints (cell {cell}, {tw.CELLS[cell]['model']}, RTX PRO 6000)", "",
             "| level | arm | N | lead calls per h (window) | calls | prefilled tokens per call | evicted, share of prompt tokens | loaded from DRAM, share of prompt tokens | "
             "load wait per loading call, s | engine TTFT s (p50 / p95) | call s (p50 / p95) |", "|" + "---|" * 11]
    res = {}
    for name in names:
        lv = Level(cell, name)
        if not lv.caps:
            lines.append(f"| {name} | | | no data | | | | | | | |")
            continue
        calls = lv.calls()
        lead = [c for c in calls if c["lead"]]
        win = [c for c in lead if lv.in_window(c["t_send"])]
        span = (lv.window[1] - lv.window[0]) if lv.window and lv.window[1] else None
        npt = sum(c.get("np") or 0 for c in calls)
        loads = [c for c in calls if (c.get("he") or 0) > 0]
        r = {"arm": tw.CONDITIONS[name]["tm"].get("arm"), "n": tw.CONDITIONS[name]["n"], "calls": len(calls),
             "lead_per_h": div(len(win) * 3600, span), "prefilled_mean": mean([c.get("prefilled") for c in calls]),
             "evicted_share": div(sum(c.get("evicted") or 0 for c in calls), npt), "loaded_share": div(sum(c.get("he") or 0 for c in calls), npt),
             "load_wait": mean([c.get("load") for c in loads]), "ttft_p50": q([c.get("ttft") for c in calls], 0.5),
             "ttft_p95": q([c.get("ttft") for c in calls], 0.95), "dur_p50": q([c["dur"] for c in calls], 0.5),
             "dur_p95": q([c["dur"] for c in calls], 0.95)}
        tm = lv.tm
        if tm:
            hints = Counter(h["ev"] for h in tm.get("tmh", []))
            dem = tm.get("tmd", [])
            ret = tm.get("tmr", [])
            err = [abs(x["away_s"] - x["p50"]) for x in ret if x.get("away_s") is not None and x.get("p50") is not None]
            cover = [x["away_s"] <= x["p90"] for x in ret if x.get("away_s") is not None and x.get("p90") is not None]
            pf = [e for e in lv.ext if e.get("ev") == "prefetch"]
            r["events"] = {"hints": dict(hints), "demotions": len(dem), "demoted_blocks": sum(d["moved"] for d in dem),
                           "demote_why": dict(Counter(d["why"] for d in dem)), "kept_shared": sum(d["kept_shared"] for d in dem),
                           "returns": len(ret), "returns_demoted": sum(1 for x in ret if x.get("demoted")),
                           "pred_abs_err_p50_s": q(err, 0.5), "p90_coverage": div(sum(cover), len(cover)),
                           "prefetch_arrivals": len(tm.get("tmp", [])), "prefetch": dict(Counter(e.get("state") for e in pf)),
                           "prefetch_ms_p50": q([e.get("ms") for e in pf if e.get("state") == "done"], 0.5),
                           "gate_changes": len(tm.get("tmg", []))}
            # did a returning session that was demoted or prefetched find its prefix in DRAM / GPU?
            by_r = {x["r"]: x for x in ret if x.get("r") is not None}
            if lv.srv:
                rows = []
                for ri, x in by_r.items():
                    rec = lv.srv.req.get(ri)
                    if rec and rec["adm"]:
                        a0 = [y for y in rec["adm"] if not y["res"]]
                        if a0:
                            rows.append({"demoted": x.get("demoted"), "hit": a0[0]["hit"], "he": a0[0].get("he") or 0,
                                         "np": rec["arr"]["np"], "ideal": rec["arr"]["ideal"]})
                for flag in (True, False):
                    sel = [y for y in rows if bool(y["demoted"]) == flag]
                    if sel:
                        r["events"][f"returns_{'demoted' if flag else 'kept'}"] = {
                            "n": len(sel), "gpu_hit_share": div(sum(y["hit"] - y["he"] for y in sel), sum(y["np"] for y in sel)),
                            "dram_share": div(sum(y["he"] for y in sel), sum(y["np"] for y in sel)),
                            "recompute_share": div(sum(max(0, max(y["hit"], y["ideal"]) - y["hit"]) for y in sel), sum(y["np"] for y in sel))}
        res[name] = r
        lines.append(f"| {name} | {r['arm']} | {r['n']} | {f(r['lead_per_h'], 0)} | {r['calls']} | {f(r['prefilled_mean'], 0)} | {pc(r['evicted_share'])} | "
                     f"{pc(r['loaded_share'])} | {f(r['load_wait'], 3)} | {f(r['ttft_p50'], 2)} / {f(r['ttft_p95'], 2)} | {f(r['dur_p50'], 2)} / {f(r['dur_p95'], 2)} |")
    lines += ["", "*Key.* **evicted**: tokens inside the arrival ideal hit that the call did not hit (recomputed because evicted). "
              "**loaded from DRAM**: tokens restored from the CPU tier at admission. **engine TTFT**: scheduler arrival → first token.", ""]
    for name, r in res.items():
        if r.get("events"):
            e = r["events"]
            lines.append(f"**{name} events**: hints {e['hints']}; demotions {e['demotions']} ({e['demote_why']}, {e['demoted_blocks']:,} blocks moved, "
                         f"{e['kept_shared']:,} shared kept); returns {e['returns']} ({e['returns_demoted']} after a demotion); predicted vs actual away "
                         f"time |error| p50 {f(e['pred_abs_err_p50_s'], 2)} s, p90 covers {pc(e['p90_coverage'], 0)}; prefetch shadow requests "
                         f"{e['prefetch_arrivals']} ({e['prefetch']}, p50 {f(e['prefetch_ms_p50'], 0)} ms); gate changes {e['gate_changes']}.")
            for k in ("returns_demoted", "returns_kept"):
                if e.get(k):
                    x = e[k]
                    lines.append(f"  - {k.replace('_', ' ')}: {x['n']} calls; prompt tokens from GPU cache {pc(x['gpu_hit_share'])}, from DRAM "
                                 f"{pc(x['dram_share'])}, recomputed after eviction {pc(x['recompute_share'])}")
            lines.append("")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"b1_{cell}.json").write_text(json.dumps(res, indent=1, default=str))
    return "\n".join(lines) + "\n"


def d1replay(max_sessions: int = 8, max_calls: int = 120) -> str:
    """S1 offline check of D1 on recorded sessions (12's p1a / p1b, 11's n01a): replay each session's lead chain
    through tm_server_ext's own delta render and bookkeeping (vLLM's Anthropic converter, the Qwen3 template via
    the HF tokenizer) and compare every call with a full re-render. Also counts, exactly, the prompt tokens D1 would
    reuse that the re-render cannot (the previous reply)."""
    import asyncio
    import tm_client
    import tm_server_ext as ext
    from test_tm import _lead_chain, _reply_ids, _serving, _tokenizer
    from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
    tok = _tokenizer()
    serving = _serving(tok)
    roots = {"p1a (32B)": HERE.parent / "12_hw_utilization/data/cells/p1a/levels/hw_p1a/runs",
             "p1b (8B)": HERE.parent / "12_hw_utilization/data/cells/p1b/levels/hw_p1b/runs",
             "n01a (32B, 11)": HERE.parent / "11_multiuser_kv/data/multiuser_live/cells/n01a/runs"}
    rows, res = [], {}
    for name, root in roots.items():
        caps = sorted(root.glob("*/*.requests.jsonl.xz"))[:max_sessions]
        agg = Counter()
        for cap in caps:
            st = known = prev_full = None
            for row in _lead_chain(cap)[:max_calls]:
                body = {k: row[k] for k in ("model", "max_tokens", "system", "tools", "messages") if row.get(k) is not None}
                req = AnthropicMessagesRequest(**body)
                msgs = row["messages"]
                full = asyncio.run(serving.online_renderer.render_chat(serving.to_chat_completion_request(req)))[1][0]["prompt_token_ids"]
                assistant = {"role": "assistant", "content": row["response"]["content"]}
                gen = _reply_ids(tok, serving, req.model_validate({**body, "messages": [assistant]}).messages[0])
                k = tm_client.common_prefix(msgs, known) if known is not None else 0
                agg["calls"] += 1
                if st is None or k <= 0 or k >= len(msgs):
                    blocks, sys_block, _ = ext._message_blocks(serving, req)
                    ctx = ext.Ctx(chain="c", session="s", rid=None, mode="init", msg_blocks=blocks, sys_block=sys_block)
                    ids, offsets = ext.next_state(ctx, None, full, gen)
                    agg["init"] += 1
                else:
                    edit = k < len(st.offsets)
                    prefix = st.ids[:ext.cut_at(st, k)]
                    delta, rel = asyncio.run(ext._render_delta(serving, req, req.messages[k:]))
                    d1ids = list(prefix) + delta
                    cmp = ext.compare_blocks(tok, d1ids, full)
                    agg["delta"] += 1
                    agg["edits"] += edit
                    agg["ok"] += cmp["ok"]
                    agg["other_diff"] += cmp["other_diff"]
                    agg["tok_d1"] += len(d1ids)
                    agg["tok_full"] += len(full)
                    # reusable prefix with the previous call: D1 = the stored prefix (prompt + reply as generated);
                    # re-render = longest common prefix of this full render with the previous full render
                    lcp = 0
                    for x, y in zip(full, prev_full or []):
                        if x != y:
                            break
                        lcp += 1
                    agg["reuse_d1"] += (len(prefix) // 16) * 16
                    agg["reuse_full"] += (lcp // 16) * 16
                    agg["new_full"] += len(full) - (lcp // 16) * 16
                    agg["new_d1"] += len(d1ids) - (len(prefix) // 16) * 16
                    ctx = ext.Ctx(chain="c", session="s", rid=None, mode="delta", k=k, prefix_len=len(prefix), rel_offsets=rel)
                    ids, offsets = ext.next_state(ctx, st, d1ids, gen)
                st = ext.ChainState(ids, offsets)
                known = msgs + [assistant]
                prev_full = full
            agg["sessions"] += 1
        res[name] = dict(agg)
        rows.append(f"| {name} | {agg['sessions']} | {agg['calls']:,} | {agg['delta']:,} | {agg['edits']:,} | {agg['ok']:,} | "
                    f"{agg['other_diff']} | {f(div(agg['new_full'], agg['delta']), 0)} | {f(div(agg['new_d1'], agg['delta']), 0)} | "
                    f"{pc(1 - div(agg['new_d1'], agg['new_full']) if agg['new_full'] else None)} |")
    md = ["# S1 · D1 replayed on recorded sessions (offline, CPU)", "",
          "Each session's lead chain is replayed through tm_server_ext's own delta render and bookkeeping (vLLM's "
          "Anthropic converter, the Qwen3 template via the HF tokenizer) and every call is compared with a full re-render, "
          "block by block (one block per `<|im_start|>`). Replies stand in for generated tokens as the template renders them "
          "after the generation prompt.", "",
          "| sessions from | sessions | lead calls | D1 calls (delta) | of which after a history edit (truncation at k) | identical outside assistant turns | differing non-assistant blocks | new tokens per call, re-render | new tokens per call, D1 | prefill D1 saves |",
          "|---|---|---|---|---|---|---|---|---|---|", *rows, "",
          "*Key.* **new tokens**: prompt tokens past the reusable prefix (whole 16-token blocks) of the previous call: what "
          "the engine must prefill even with a warm cache. Re-render: the template drops the generation prompt's empty "
          "think block, so the prefix ends 4 tokens before the previous prompt's end. D1: the prefix runs through the "
          "previous reply as generated."]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "d1replay.json").write_text(json.dumps(res, indent=1))
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["probe", "d1", "b1", "d1replay", "all"])
    ap.add_argument("--job", default=None)
    ap.add_argument("--cell", default=None)
    a = ap.parse_args()
    TABLES.mkdir(parents=True, exist_ok=True)
    if a.what in ("probe", "all"):
        md = probe(a.job)
        (TABLES / "S2_probe.md").write_text(md)
        print(md)
    if a.what in ("d1", "all"):
        md = d1(a.cell or "s2d")
        (TABLES / f"S2_d1_{a.cell or 's2d'}.md").write_text(md)
        print(md)
    if a.what == "d1replay":
        md = d1replay()
        (TABLES / "S1_d1_replay.md").write_text(md)
        print(md)
    if a.what in ("b1", "all"):
        md = b1(a.cell or "s2b")
        (TABLES / f"S2_b1_{a.cell or 's2b'}.md").write_text(md)
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
