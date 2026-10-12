#!/usr/bin/env python3
"""E-A: compounded replay -- every arm on the SAME recorded trajectories, call after call.

The live runs (E-B) let each arm follow its own trajectory, so a difference in a later call mixes
the KV approximation with trajectory divergence. Here the prompts are fixed: the lead calls of
recorded `prefix` runs (append-only template; each call's prompt plus its recorded reply is exactly
the next prompt's prefix outside compaction edits). Each arm keeps its own session entry across the
run, exactly as the live server does, so staleness compounds; the one-step arms rebuild their entry
from fresh rows at every call (topic 10's setting). Per call:

  ref        a fresh full recompute of the prompt (rows saved: the reference for L1), then a
             teacher-forced pass of the recorded reply over that fresh prefix (reference logprobs)
  shift, epic32, epic64, epic128           compounded: plan against the arm's own entry
  shift1, epic128_1                        one-step: plan against ref's (fresh) entry of call k-1
  for each arm: the plan request (prompt only, rows saved), L1 deviation against ref's rows, and the
  teacher-forced pass of the recorded reply over the arm's rows -> per-token KL / top-1 / dNLL against
  ref, first-token KL; on every 5th edited call a greedy next round (512 tokens) per arm vs ref's.

Engine: offline vLLM with the EpicConnector (eager, hooks; prefix caching on, salted per arm and run).

    python research/16_epic_harness/epic_replay.py run --live data/live/main [--runs 40]
    python research/16_epic_harness/epic_replay.py report --live data/live/main
"""
from __future__ import annotations

import argparse
import json
import lzma
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402

COMPOUNDED = ["shift", "epic32", "epic64", "epic128"]
ONESTEP = {"shift1": "shift", "epic128_1": "epic128"}
POOL_ROWS = 160_000
TOPK = 20
TF_MAX = 384
OUT_NAME = "replay"
AGE_BINS = (0, 1, 2, 4, 8, 16)
OFF_BINS = (0, 32, 64, 128, 512)


def open_jsonl(path: Path) -> list[dict]:
    opener = lzma.open if path.suffix == ".xz" else open
    out = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def lead_calls(run_dir: Path) -> list[dict]:
    paths = sorted(run_dir.glob("*.requests.jsonl*"))
    if not paths:
        return []
    recs = [r for r in open_jsonl(paths[0]) if r.get("purpose") == "lead" and r.get("response")
            and not r.get("error")]
    return sorted(recs, key=lambda r: r["call_index"])


def topk_kl(p: dict, q: dict) -> float:
    if not p or not q:
        return float("nan")
    floor = min(q.values())
    return sum(math.exp(lp) * (lp - q.get(t, floor)) for t, lp in p.items())


def _bin(val: int, edges) -> int:
    b = 0
    for i, e in enumerate(edges):
        if val >= e:
            b = i
    return b


class Replayer:
    def __init__(self, out: Path, pool_rows: int = POOL_ROWS):
        import os
        os.environ["EPIC_POOL_ROWS"] = str(pool_rows)
        os.environ["PYTHONPATH"] = f"{ec.HERE}:{os.environ.get('PYTHONPATH', '')}"
        os.environ["VLLM_ALLOW_INSECURE_SERIALIZATION"] = "1"
        from vllm import LLM

        import epic_plan as ep
        self.ep = ep
        self.llm = LLM(model=ec.MODEL, max_model_len=32768, gpu_memory_utilization=0.92, enforce_eager=True,
                       enable_prefix_caching=True, async_scheduling=False, seed=0, max_num_seqs=8,
                       max_num_batched_tokens=8192, attention_backend="FLASH_ATTN",
                       kv_transfer_config={"kv_connector": "EpicConnector",
                                           "kv_connector_module_path": "epic_connector",
                                           "kv_role": "kv_both", "kv_load_failure_policy": "fail",
                                           "kv_connector_extra_config": {"store_device": "cpu",
                                                                         "pool_rows": pool_rows}})
        self.tok = self.llm.get_tokenizer()
        self.im_start = self.tok.convert_tokens_to_ids("<|im_start|>")
        self.pool = ep.SlotPool(pool_rows)
        self.rend = ec.renderer(ec.TEMPLATE.read_text())
        self.out = out

    # -- engine requests ------------------------------------------------------------------------------
    def request(self, ids, salt, splice, max_tokens=1, prompt_logprobs=None, logprobs=TOPK):
        from vllm import SamplingParams
        sp = SamplingParams(max_tokens=max_tokens, temperature=0.0, logprobs=logprobs,
                            prompt_logprobs=prompt_logprobs, skip_reading_prefix_cache=False,
                            extra_args={"kv_transfer_params": {"splice": splice}})
        return self.llm.generate([{"prompt_token_ids": ids, "cache_salt": salt}], sp, use_tqdm=False)[0]

    def segs(self, positions, slots):
        out = []
        for p, s in zip(positions, slots):
            p, s = int(p), int(s)
            if out and out[-1][1] == p and out[-1][2] + (out[-1][1] - out[-1][0]) == s:
                out[-1][1] = p + 1
            else:
                out.append([p, p + 1, s])
        return out

    def run_plan(self, arm, entry, ids, salt):
        """The arm's prompt-only request: returns (plan, new entry rows for [0, P), new slots)."""
        ep = self.ep
        plan = ep.plan_call(arm, entry, ids, self.im_start)
        new_slots = self.pool.alloc(plan.stats["C"])
        cpos = [p for a, b in plan.compute for p in range(a, b)]
        out = self.request(ids, salt, {"p0": plan.p0, "load": plan.load, "override": plan.override,
                                       "from_layer": 0, "save": self.segs(cpos, new_slots)})
        return plan, new_slots, out

    def tf(self, ids, reply, rows_slot, rows_birth, salt):
        """Teacher-forced reply over the given prompt rows (loaded, re-rotated): per-token top-20."""
        P = len(ids)
        base = (P // 16) * 16
        load = []
        for lo, hi, s, d in self.ep.runs_of(self._np.arange(base), rows_slot, rows_birth):
            load.append([lo, hi, s, d])
        n = min(len(reply), TF_MAX)
        out = self.request(ids + reply[:n], salt, {"p0": base, "load": load, "override": [], "save": []},
                           prompt_logprobs=TOPK)
        plp = out.prompt_logprobs or []
        dists = []
        for i in range(n):
            k = P + i - base                 # [None] + one entry per computed position (from base)
            entry = plp[k] if 0 < k < len(plp) else None
            dists.append({int(t): float(v.logprob) for t, v in entry.items()} if entry else None)
        return dists

    def compare(self, arm_slot, arm_birth, fresh_slot, bins, n_bins):
        import epic_connector as ecn
        P = len(arm_slot)
        deltas = [int(p - arm_birth[p]) for p in range(P)]
        return self.llm.collective_rpc(ecn.epic_compare, args=(
            [int(s) for s in arm_slot], deltas, [int(s) for s in fresh_slot], bins, n_bins))[0]

    # -- one run ------------------------------------------------------------------------------------------
    def replay_run(self, run_dir: Path, greedy_every: int = 5, max_calls: int = 0) -> list[dict]:
        import numpy as np
        self._np = np
        ep = self.ep
        from render import with_assistant
        calls = lead_calls(run_dir)
        if max_calls:
            calls = calls[:max_calls]
        if len(calls) < 2:
            return []
        records = []
        entries: dict[str, ep.Entry | None] = {a: None for a in COMPOUNDED}
        ref_entry = None
        label = run_dir.name
        for k, call in enumerate(calls):
            _, ids = self.rend.render(call)
            _, seq = self.rend.render(with_assistant(call, call["response"]["content"]),
                                      add_generation_prompt=False)
            reply = seq[len(ids):-2]                          # the recorded reply as the server held it
            P = len(ids)
            if P + len(reply) > 32000:
                break
            t0 = time.perf_counter()
            # ref: fresh rows of the whole sequence (prompt + reply), one request
            fresh_slots = self.pool.alloc(len(seq) - 2)
            self.request(seq[:-2], f"ref:{label}:{k}",
                         {"p0": 0, "load": [], "override": [], "save": [[0, len(seq) - 2, int(fresh_slots[0])]]
                          if int(fresh_slots[-1]) - int(fresh_slots[0]) == len(seq) - 3 else
                          self.segs(range(len(seq) - 2), fresh_slots)})
            ref_new = ep.first_entry(seq[:-2], fresh_slots, P, k)
            ref_tf = self.tf(ids, reply, ref_new.slot[:P], ref_new.birth[:P], f"reftf:{label}:{k}")
            rec = {"run": label, "call": k, "P": P, "reply": len(reply), "arms": {}}
            edited = False
            arm_entries = {}
            for arm in COMPOUNDED + list(ONESTEP):
                base_arm = ONESTEP.get(arm, arm)
                prev = ref_entry if arm in ONESTEP else entries[arm]
                plan, new_slots, out = self.run_plan(base_arm, prev, ids, f"{arm}:{label}:{k}")
                gen_slots = self.pool.alloc(max(0, len(reply) - 1))
                # the arm's entry: prompt rows from the plan, reply rows teacher-forced over them
                entry = ep.next_entry(plan, ids, reply, new_slots, gen_slots, k)
                tf = self.tf(ids, reply, entry.slot[:P], entry.birth[:P], f"tf:{arm}:{label}:{k}")
                # save the reply rows the arm would hold (teacher-forced prefill over the arm's prompt rows)
                base = ((P - 1) // 16) * 16                    # leave >= 1 prompt token to compute
                load = [[lo, hi, s, d] for lo, hi, s, d in ep.runs_of(np.arange(base), entry.slot, entry.birth)]
                if len(reply) > 1:
                    self.request(ids + reply[:-1], f"rows:{arm}:{label}:{k}",
                                 {"p0": base, "load": load, "override": [], "from_layer": 0,
                                  "save": self.segs(range(P, P + len(reply) - 1), gen_slots)})
                stats = dict(plan.stats)
                edited = edited or stats["S"] > 0 or stats["H"] > 0
                # L1 against ref's fresh rows of [0, P)
                n_age, n_off = len(AGE_BINS), len(OFF_BINS)
                heads = ep.run_heads(plan.segs, plan.k, plan.p0, P) if plan.k else set()
                cls = np.full(P, 3, dtype=int)
                cls[:plan.p0] = 0
                for lo, hi, _s, _d in plan.override:
                    cls[lo:hi] = 1
                for h in heads:
                    cls[h] = 2
                start = np.zeros(P, dtype=int)
                for seg in plan.segs:
                    if seg[0] == "reuse":
                        start[seg[1]:seg[2]] = seg[1]
                bins = [int((cls[p] * n_age + _bin(int(entry.age[p]), AGE_BINS)) * n_off
                            + _bin(p - int(start[p]), OFF_BINS)) for p in range(P)]
                l1 = self.compare(entry.slot[:P], entry.birth[:P], fresh_slots[:P], bins,
                                  4 * n_age * n_off) if stats["S"] or plan.p0 else None
                kls, top1, dnll = [], [], []
                for i, (fd, ad) in enumerate(zip(ref_tf, tf)):
                    if not fd or not ad:
                        continue
                    kls.append(topk_kl(fd, ad))
                    top1.append(int(max(fd, key=fd.get) == max(ad, key=ad.get)))
                    tok = reply[i]
                    if tok in fd and tok in ad:
                        dnll.append(fd[tok] - ad[tok])
                arm_rec = {"stats": stats, "kl": statistics.mean(kls) if kls else None,
                           "first_kl": kls[0] if kls else None, "top1": statistics.mean(top1) if top1 else None,
                           "dnll": statistics.mean(dnll) if dnll else None,
                           "l1": _sparse(l1) if l1 else None}
                if edited and greedy_every and k % greedy_every == 0:
                    g = self.request(ids, f"g:{arm}:{label}:{k}", {"p0": base, "load": load, "override": [],
                                                                   "save": []}, max_tokens=512, logprobs=None)
                    arm_rec["greedy"] = list(g.outputs[0].token_ids)
                rec["arms"][arm] = arm_rec
                arm_entries[arm] = entry
                # the entry takes its references BEFORE the call's allocations are dropped: released first,
                # newly computed rows would return to the free list while the entry still names them, and the
                # next arm's allocation would overwrite them (cleanup note 2026-10-09 11:40)
                self.pool.retain(entry.slot)
                self.pool.release(new_slots)
                self.pool.release(gen_slots)
                self.pool.assert_held(entry.slot)
            if edited and greedy_every and k % greedy_every == 0:
                base = ((P - 1) // 16) * 16
                g = self.request(ids, f"g:ref:{label}:{k}",
                                 {"p0": base, "load": [[lo, hi, s, d] for lo, hi, s, d in
                                                       ep.runs_of(np.arange(base), ref_new.slot, ref_new.birth)],
                                  "override": [], "save": []}, max_tokens=512, logprobs=None)
                ref_ids = list(g.outputs[0].token_ids)
                for arm, arm_rec in rec["arms"].items():
                    gi = arm_rec.pop("greedy", None)
                    if gi is not None:
                        div = next((i for i, (x, y) in enumerate(zip(gi, ref_ids)) if x != y), min(len(gi), len(ref_ids)))
                        arm_rec.update(greedy_identical=int(gi == ref_ids), greedy_first_div=div,
                                       greedy_text=self.tok.decode(gi))
                rec["ref_greedy_text"] = self.tok.decode(ref_ids)
            rec["edited"] = edited
            rec["ms"] = round(1000 * (time.perf_counter() - t0))
            records.append(rec)
            # advance entries; free the previous call's rows
            for arm in COMPOUNDED:
                if entries[arm] is not None:
                    self.pool.release(entries[arm].slot)
                entries[arm] = arm_entries[arm]
            for arm in ONESTEP:
                self.pool.release(arm_entries[arm].slot)
            if ref_entry is not None:
                self.pool.release(ref_entry.slot)
            ref_entry = ref_new
        for arm in COMPOUNDED:
            if entries[arm] is not None:
                self.pool.release(entries[arm].slot)
        if ref_entry is not None:
            self.pool.release(ref_entry.slot)
        return records


def _sparse(per_layer: list, group: int = 4) -> dict:
    L = len(per_layer)
    out = {}
    for b in range(len(per_layer[0]) if L else 0):
        if not any(per_layer[layer][b][4] for layer in range(L)):
            continue
        rows = []
        for g in range(0, L, group):
            acc = [sum(per_layer[layer][b][j] for layer in range(g, min(g + group, L))) for j in range(5)]
            rows.append([float(f"{v:.6g}") for v in acc])
        out[b] = rows
    return out


def pick_runs(live: Path, n: int, labels: list[str] | None = None, min_calls: int = 0) -> list[Path]:
    """`n` recorded prefix runs (seeded draw), or the named ones; `min_calls` keeps runs long enough to have
    passed the snip threshold (a run that never compacts gives every arm the same KV)."""
    if labels:
        return [live / "runs" / lab for lab in labels]

    def long_enough(d: Path) -> bool:
        meta = d / f"{d.name}.meta.json"
        return not min_calls or (meta.exists() and int(json.loads(meta.read_text()).get("lead") or 0) >= min_calls)

    runs = sorted(d for d in (live / "runs").iterdir() if d.name.endswith("-prefix")
                  and (d / f"{d.name}.done").exists() and long_enough(d))
    rng = random.Random("ea-runs")
    rng.shuffle(runs)
    return sorted(runs[:n])


def cmd_run(args) -> None:
    live = ec.DATA / args.live if not Path(args.live).is_absolute() else Path(args.live)
    out = live / args.out_name
    out.mkdir(parents=True, exist_ok=True)
    if not args.inner:
        gpu = ec.pick_gpu(args.gpu)
        extra = [x for lab in args.label for x in ("--label", lab)] + ["--max-calls", str(args.max_calls),
                                                                       "--out-name", args.out_name,
                                                                       "--min-calls", str(args.min_calls),
                                                                       "--shard", args.shard,
                                                                       "--pool-rows", str(args.pool_rows)]
        code = ec.run_isolated(gpu, [ec.PYTHON, __file__, "run", "--live", args.live, "--runs", str(args.runs),
                                     *extra, "--inner"], out / "replay.log",
                               env_extra={"VLLM_ALLOW_INSECURE_SERIALIZATION": "1", "PYTHONPATH": str(ec.HERE)})
        raise SystemExit(code)
    rp = Replayer(out, args.pool_rows)
    path = out / "replay_calls.jsonl"
    done = {json.loads(l)["run"] for l in path.read_text().splitlines()} if path.exists() else set()
    shard, n_shards = (int(x) for x in args.shard.split("/"))
    for run_dir in pick_runs(live, args.runs, args.label, args.min_calls)[shard::n_shards]:
        if run_dir.name in done:
            continue
        t = time.perf_counter()
        recs = rp.replay_run(run_dir, max_calls=args.max_calls)
        with path.open("a", encoding="utf-8") as handle:
            for rec in recs:
                handle.write(json.dumps(rec) + "\n")
        ec.log(f"{run_dir.name}: {len(recs)} calls in {time.perf_counter() - t:.0f} s; pool {rp.pool.used}")


def _load(srcs: list[str]) -> list[dict]:
    """replay_calls.jsonl of every source `live_dir[:out_name]`, each record tagged with its source."""
    recs = []
    for src in srcs:
        live, _, name = src.partition(":")
        path = (ec.DATA / live if not Path(live).is_absolute() else Path(live)) / (name or OUT_NAME)
        for line in (path / "replay_calls.jsonl").read_text().splitlines():
            rec = json.loads(line)
            rec["src"] = live
            recs.append(rec)
    return recs


def _l1_reused(arm_rec: dict, layers: slice = slice(None)) -> tuple[float, float]:
    """(sum dK, sum |K*|^2) over the reused-stale rows (class 1) of one call, chosen layer groups."""
    dk = k = 0.0
    for b, groups in (arm_rec.get("l1") or {}).items():
        if int(b) // (len(AGE_BINS) * len(OFF_BINS)) != 1:
            continue
        for g in groups[layers]:
            dk, k = dk + g[0], k + g[1]
    return dk, k


def _boot_runs(recs: list[dict], stat, reps: int = 2000, seed: str = "ea-boot") -> tuple[float, float, float]:
    """Statistic over calls with a 95% interval from resampling runs (clusters)."""
    by_run = defaultdict(list)
    for r in recs:
        by_run[r["run"]].append(r)
    runs = sorted(by_run)
    point = stat(recs)
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [r for _ in runs for r in by_run[rng.choice(runs)]]
        v = stat(sample)
        if v == v:
            vals.append(v)
    vals.sort()
    if not vals:
        return point, float("nan"), float("nan")
    return point, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def cmd_report(args) -> None:
    srcs = args.src or [f"{args.live}:{args.out_name}"]
    recs = _load(srcs)
    edited = [r for r in recs if r.get("edited")]
    arms = COMPOUNDED + list(ONESTEP)
    lines = ["# E-A: compounded replay on recorded prefix trajectories", "",
             f"Sources: {', '.join(srcs)}. {len({r['run'] for r in recs})} runs, {len(recs)} lead calls, "
             f"{len(edited)} after an edit (the calls below). Each arm's KV evolves under its own policy over the "
             "recorded prompts; the one-step arms (`*_1`) rebuild from fresh rows every call. KL: top-20, "
             "teacher-forced over the recorded reply, against a fresh recompute (nats/token); 95% intervals "
             "resample runs.", "",
             "| arm | recomputed share of reused | reply KL [95% CI] | first-token KL | top-1 | dNLL | greedy next round identical |",
             "|---|---|---|---|---|---|---|"]

    def mean_of(arm, key):
        def stat(rs):
            v = [r["arms"][arm][key] for r in rs if arm in r["arms"] and r["arms"][arm].get(key) is not None]
            return statistics.mean(v) if v else float("nan")
        return stat

    for arm in arms:
        rows = [r["arms"][arm] for r in edited if arm in r["arms"]]
        if not rows:
            continue
        share = [x["stats"]["H"] / max(1, x["stats"]["S"] + x["stats"]["H"]) for x in rows]
        kl, lo, hi = _boot_runs(edited, mean_of(arm, "kl"))
        lines.append(f"| {arm} | {statistics.mean(share):.1%} | {kl:.4f} [{lo:.4f}, {hi:.4f}] | "
                     f"{mean_of(arm, 'first_kl')(edited):.4f} | {mean_of(arm, 'top1')(edited):.2%} | "
                     f"{mean_of(arm, 'dnll')(edited):.4f} | {mean_of(arm, 'greedy_identical')(edited):.1%} |")
    # paired restoration of the stale-row deviation, pooled over calls: rho_k = 1 - D(epic-k) / D(shift)
    lines += ["", "**KV restoration** on the same calls: rho = 1 - D(arm) / D(shift), D = sum |K - K*|^2 / "
              "sum |K*|^2 over the reused-stale rows, pooled over calls (95% CI over runs):", "",
              "| arm | all layers | layers 0-15 | 16-31 | 32-47 | 48-63 |", "|---|---|---|---|---|---|"]

    def rho(arm, layers):
        key = (arm, layers.start, layers.stop)
        for r in edited:                # per call once (not per resample): the arm's and shift's deviation
            a, b = r["arms"].get(arm), r["arms"].get("shift")
            v = None
            if a and b and a.get("l1") and b.get("l1"):
                da, ka = _l1_reused(a, layers)
                ds, ks = _l1_reused(b, layers)
                v = (da / ka, ds / ks) if ka and ks else None
            r.setdefault("_rho", {})[key] = v

        def stat(rs):
            num = den = 0.0
            for r in rs:
                v = r["_rho"][key]
                if v:
                    num, den = num + v[0], den + v[1]
            return 1 - num / den if den else float("nan")
        return stat

    for arm in ["epic32", "epic64", "epic128", "epic128_1", "shift1"]:
        cells = []
        for layers in (slice(None), slice(0, 4), slice(4, 8), slice(8, 12), slice(12, 16)):
            v, lo, hi = _boot_runs(edited, rho(arm, layers))
            cells.append(f"{v:.0%} [{lo:.0%}, {hi:.0%}]")
        lines.append(f"| {arm} | " + " | ".join(cells) + " |")
    def ratio(num_arm, den_arm, key="kl"):
        """mean of num_arm's per-call metric / den_arm's, over the calls where both have it"""
        def stat(rs):
            pairs = [(r["arms"][num_arm][key], r["arms"][den_arm][key]) for r in rs
                     if r["arms"].get(num_arm, {}).get(key) is not None
                     and r["arms"].get(den_arm, {}).get(key) is not None]
            den = statistics.mean(b for _a, b in pairs) if pairs else 0
            return statistics.mean(a for a, _b in pairs) / den if den > 0 else float("nan")
        return stat

    # paired: each EPIC arm against shift on the same calls (the restoration of step fidelity)
    lines += ["", "**Paired against shift** (same calls): mean reply KL and first-token KL of the arm / of shift "
              "(compounded arms against compounded shift, one-step against one-step):", "",
              "| arm | reply KL ratio [95% CI] | first-token KL ratio [95% CI] |", "|---|---|---|"]
    for arm, base in (("epic32", "shift"), ("epic64", "shift"), ("epic128", "shift"), ("epic128_1", "shift1")):
        cells = []
        for key in ("kl", "first_kl"):
            v, lo, hi = _boot_runs(edited, ratio(arm, base, key))
            cells.append(f"{v:.2f} [{lo:.2f}, {hi:.2f}]")
        lines.append(f"| {arm} / {base} | " + " | ".join(cells) + " |")
    # compounding: compounded vs one-step on the same calls
    lines += ["", "**Compounding** (same calls): reply KL of the compounded arm / its one-step twin:", ""]
    for comp, one in (("shift", "shift1"), ("epic128", "epic128_1")):
        v, lo, hi = _boot_runs(edited, ratio(comp, one))
        lines.append(f"- {comp} / {one}: {v:.2f}× [{lo:.2f}, {hi:.2f}]")
    # by template (cell = label part)
    lines += ["", "**Reply KL by template** (edited calls):", "", "| template | calls | " +
              " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    by_t = defaultdict(list)
    for r in edited:
        parts = r["run"].split("-")
        t = next((x for x in parts if x[:1] in "EM" and x[1:].isdigit()), "?")
        by_t[t].append(r)
    for t in sorted(by_t):
        rs = by_t[t]
        lines.append(f"| {t} | {len(rs)} | " + " | ".join(f"{mean_of(a, 'kl')(rs):.4f}" for a in arms) + " |")
    out = Path(args.out) if args.out else (ec.DATA / "replay_tables.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--live", required=True)
    r.add_argument("--runs", type=int, default=40)
    r.add_argument("--label", action="append", default=[], help="replay these prefix runs (default: a random draw)")
    r.add_argument("--max-calls", type=int, default=0, help="stop each run after this many lead calls")
    r.add_argument("--min-calls", type=int, default=0, help="only runs with at least this many lead calls")
    r.add_argument("--out-name", default=OUT_NAME)
    r.add_argument("--shard", default="0/1", help="i/n: replay every n-th picked run from the i-th (one process "
                                                    "per GPU, each with its own --out-name; report merges them)")
    r.add_argument("--gpu", type=int, default=None)
    r.add_argument("--pool-rows", type=int, default=POOL_ROWS,
                   help="host-RAM row pool (256 KB per row; seven entries of an R2 run's 20K+ prompts need > 160K)")
    r.add_argument("--inner", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("--live", default=None)
    rep.add_argument("--out-name", default=OUT_NAME)
    rep.add_argument("--src", action="append", default=[], help="live_dir[:out_name], repeatable (merged)")
    rep.add_argument("--out", default=None, help="Markdown output (default data/replay_tables.md)")
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
