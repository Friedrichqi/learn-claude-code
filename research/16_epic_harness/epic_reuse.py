#!/usr/bin/env python3
"""E-0: how much KV does the "original system" reuse, before and after making it append-only?

Three configurations of the same s15 sessions on stock vLLM 0.31 (prefix caching on, CUDA graphs,
`--enable-prompt-tokens-details`, so every response reports `cache_read_input_tokens`):

  A  the harness as it was (git HEAD before this topic): per-second timestamp and live memory/MCP
     sections in the system prompt; the official Qwen3 chat template
  B  the append-only system prompt (s15 change of this topic); the official template
  C  the append-only system prompt and the append-only template (qwen3_append_only.jinja), which
     keeps the empty think block when a previous reply is re-rendered

under two conditions: OFF (compaction off: --no-snip, no char limit; 12 cells, 40 lead calls) and R1
(topic 10's snip + micro at a 50k-char limit; 6 cells, 100 lead calls). A and B share one server
epoch (official template), C has its own (append-only template); each epoch runs two lanes at once.

`report` joins the per-call cache counts with an attribution of every miss, from the exact prompt
tokens each call had (10_rope_shift/render.py with the configuration's template): where call k's
prompt first differs from call k-1's prompt plus its reply -- in the system prompt or tool schemas,
at the re-rendered reply, or earlier in the history (a compaction edit, classified with topic 10's
anchored markers) -- or nowhere (a pure extension). `kl` measures how much the append-only template
itself moves the model: on sampled C calls, the first-token top-20 KL and greedy agreement between the
official and the append-only rendering of the same request, against an A/A pass (official twice).

    python research/16_epic_harness/epic_reuse.py run [--only A,B] [--cells N]
    python research/16_epic_harness/epic_reuse.py kl --calls 150
    python research/16_epic_harness/epic_reuse.py report
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import lzma
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import epic_common as ec  # noqa: E402

OUT = ec.DATA / "e0_reuse"
RUNS = OUT / "runs"
MANIFEST10 = ec.REPO / "research" / "10_rope_shift" / "data" / "rope_shift_live" / "manifest.jsonl"
SITE = Path("/data/fritz/venv/lib/python3.12/site-packages")
CELLS_OFF = [("packaging", "E1"), ("tqdm", "E1"), ("packaging", "E2"), ("click", "E2"),
             ("click", "E3"), ("typer", "E3"), ("yaml", "M1"), ("packaging", "M1"),
             ("jinja2", "M2"), ("tqdm", "M2"), ("packaging", "M3"), ("httpx", "M3")]
CELLS_R1 = [("tqdm", "E1"), ("packaging", "E2"), ("click", "E3"), ("yaml", "M1"), ("jinja2", "M2"),
            ("httpx", "M3")]
CAPS = {"OFF": (40, 2400.0), "R1": (100, 5400.0)}
CONFIGS = ("A", "B", "C")


# -- rows ---------------------------------------------------------------------------------------------

def local_row(row: dict) -> dict:
    out = dict(row)
    if row["codebase"] != "s15_harness":
        out["seed"] = str(SITE / Path(row["seed"]).name)
    else:
        out["seed"] = str(ec.REPO / "s15_integrated_harness")
    return out


def rows(condition: str) -> list[dict]:
    manifest = [json.loads(line) for line in MANIFEST10.read_text(encoding="utf-8").splitlines()]
    first = {}
    for row in sorted(manifest, key=lambda r: (r["repeat"], r["label"])):
        first.setdefault((row["codebase"], row["template"]), row)
    cells = CELLS_OFF if condition == "OFF" else CELLS_R1
    return [{**local_row(first[cell]), "cell": f"{cell[0]}-{cell[1]}"} for cell in cells]


def label_of(row: dict, config: str, condition: str) -> str:
    return f"E0-{condition}-{config}-{row['codebase']}-{row['template']}"


# -- run ----------------------------------------------------------------------------------------------

def run_epoch(base_url: str, gpu: int, jobs: list[tuple[str, dict, str]], lanes: dict[str, Path]) -> None:
    """jobs: (config, row, condition); one worker per lane, a lane serves only its configuration."""
    by_lane = defaultdict(list)
    for config, row, condition in jobs:
        label = label_of(row, config, condition)
        if (RUNS / label / f"{label}.done").exists():
            continue
        own = sorted(name for name in lanes if name.startswith(config))
        lane = min(own, key=lambda name: len(by_lane[name]))
        by_lane[lane].append((config, row, condition, label))

    def worker(lane_name: str):
        for config, row, condition, label in by_lane[lane_name]:
            cap, deadline = CAPS[condition]
            if not ec.healthy(base_url):
                raise RuntimeError("server unhealthy")
            meta = ec.run_session(lanes[lane_name], row, "vanilla", condition, RUNS / label, label,
                                  lead_cap=cap, deadline_s=deadline)
            ec.log(f"{lane_name} {label}: {meta.get('status')} {meta.get('wall_s')} s "
                   f"lead={meta.get('lead')}")

    with cf.ThreadPoolExecutor(len(by_lane) or 1) as pool:
        for future in [pool.submit(worker, name) for name in by_lane]:
            future.result()


def cmd_run(args) -> None:
    configs = [c for c in (args.only.split(",") if args.only else CONFIGS)]
    gpu = ec.pick_gpu(args.gpu)
    ec.log(f"E-0 on GPU {gpu}; configs {configs}")
    jobs = {c: [(c, row, cond) for cond in ("OFF", "R1") for row in rows(cond)[: args.cells or None]]
            for c in configs}
    scripts = [Path("research/16_epic_harness")]
    common = ["--enable-prefix-caching", "--enable-prompt-tokens-details"]
    if "A" in configs or "B" in configs:
        with ec.served(gpu, OUT / "logs" / "serve_official.log", common) as base_url:
            lanes = {}
            if "A" in configs:
                tree = ec.export_commit(args.pre_commit, ec.SCRATCH / "trees" / "pre_change")
                lanes["A1"] = ec.make_lane(ec.LANES / "e0_A1", base_url, source=tree, extra_files=scripts)
            if "B" in configs:
                lanes["B1"] = ec.make_lane(ec.LANES / "e0_B1", base_url)
            if len(lanes) == 1:                         # one configuration: two lanes of it
                name = next(iter(lanes))
                lanes[name[0] + "2"] = ec.make_lane(ec.LANES / f"e0_{name[0]}2", base_url,
                                                    source=ec.SCRATCH / "trees" / "pre_change"
                                                    if name[0] == "A" else ec.REPO,
                                                    extra_files=scripts if name[0] == "A" else ())
            run_epoch(base_url, gpu, jobs.get("A", []) + jobs.get("B", []), lanes)
    if "C" in configs:
        extra = common + ["--chat-template", str(ec.TEMPLATE)]
        with ec.served(gpu, OUT / "logs" / "serve_append_only.log", extra) as base_url:
            lanes = {"C1": ec.make_lane(ec.LANES / "e0_C1", base_url),
                     "C2": ec.make_lane(ec.LANES / "e0_C2", base_url)}
            run_epoch(base_url, gpu, jobs["C"], lanes)
    ec.log("E-0 runs done")


# -- report -------------------------------------------------------------------------------------------

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


def lead_records(run_dir: Path) -> list[dict]:
    paths = sorted(run_dir.glob("*.requests.jsonl*"))
    if not paths:
        return []
    recs = [r for r in open_jsonl(paths[0]) if r.get("purpose") == "lead" and r.get("response")]
    return sorted(recs, key=lambda r: r["call_index"])


def first_diff(a: list[int], b: list[int]) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def attribute(prev: dict, cur: dict, rend, im_start: int) -> dict:
    """Where call `cur` stops extending call `prev` (prompt + re-rendered reply), in tokens."""
    from events import classify  # 10_rope_shift
    from render import with_assistant

    _, prev_prompt = rend.render(prev)
    _, prev_seq = rend.render(with_assistant(prev, prev["response"]["content"]), add_generation_prompt=False)
    prev_seq = prev_seq[:-2]                            # the closing <|im_end|>\n is not reused
    _, cur_ids = rend.render(cur)
    k = first_diff(prev_seq, cur_ids)
    starts = [i for i, t in enumerate(cur_ids) if t == im_start]
    system_end = starts[1] if len(starts) > 1 else len(cur_ids)
    edit = classify(prev, cur)
    kinds = [k_ for k_ in (edit or {}).get("kinds", []) if k_ not in ("system", "tools", "other")]
    if k >= len(prev_seq):
        cause = "extension"
    elif k < system_end:
        cause = "system/tools"
    elif kinds:
        cause = "compaction:" + "+".join(sorted(kinds))
    elif k >= len(prev_prompt) - 8:
        cause = "reply re-render"
    else:
        cause = "history (other)"
    return {"prompt": len(cur_ids), "prev_seq": len(prev_seq), "common": k, "cause": cause,
            "reusable": min(k, len(prev_seq))}


def cmd_report(args) -> None:
    from collections import OrderedDict

    rend = {"official": ec.renderer(None), "append_only": ec.renderer(ec.TEMPLATE.read_text())}
    im_start = rend["official"].tokenizer.convert_tokens_to_ids("<|im_start|>")
    per_call = []
    for run_dir in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        label = run_dir.name
        _, condition, config = label.split("-")[:3]
        recs = lead_records(run_dir)
        r = rend["append_only" if config == "C" else "official"]
        for i, cur in enumerate(recs):
            usage = cur["response"].get("usage") or {}
            cached = int(usage.get("cache_read_input_tokens") or 0)
            # vLLM 0.31 splits prompt_tokens into cache reads, cache creation (newly cached full
            # blocks) and the uncached remainder (anthropic/serving.py _build_anthropic_usage)
            prompt = (int(usage.get("input_tokens") or 0) + cached
                      + int(usage.get("cache_creation_input_tokens") or 0))
            row = {"label": label, "config": config, "condition": condition, "call": cur["call_index"],
                   "k": i, "prompt_tokens": prompt, "cached": cached}
            if i > 0:
                row.update(attribute(recs[i - 1], cur, r, im_start))
            per_call.append(row)
    (OUT / "per_call.jsonl").write_text("".join(json.dumps(x) + "\n" for x in per_call), encoding="utf-8")

    lines = ["# E-0: KV reuse of the original system (A) and its append-only versions (B, C)", "",
             "Generated by `epic_reuse.py report` from `data/e0_reuse/runs/`. Stock vLLM 0.31, prefix "
             "caching on; `cached` is the server's `cache_read_input_tokens` per lead call. Calls after "
             "the first of each run; the first call's hits come from other sessions' identical prefixes "
             "and are reported separately.", ""]
    lines += ["| condition | config | runs | lead calls | token-weighted reuse (calls ≥ 2) | first-call reuse "
              "| exact extensions | reusable tokens realized |", "|---|---|---|---|---|---|---|---|"]
    causes = OrderedDict()
    for condition in ("OFF", "R1"):
        for config in CONFIGS:
            calls = [c for c in per_call if c["config"] == config and c["condition"] == condition]
            if not calls:
                continue
            later = [c for c in calls if c["k"] > 0]
            first = [c for c in calls if c["k"] == 0]
            reuse = sum(c["cached"] for c in later) / max(1, sum(c["prompt_tokens"] for c in later))
            reuse1 = sum(c["cached"] for c in first) / max(1, sum(c["prompt_tokens"] for c in first))
            ext = sum(c.get("cause") == "extension" for c in later) / max(1, len(later))
            realized = sum(c["cached"] for c in later) / max(1, sum(c.get("reusable", 0) for c in later))
            runs = len({c["label"] for c in calls})
            lines.append(f"| {condition} | {config} | {runs} | {len(calls)} | {reuse:.1%} | {reuse1:.1%} | "
                         f"{ext:.1%} | {realized:.1%} |")
            causes[(condition, config)] = Counter(c.get("cause") for c in later)
    lines += ["", "**Where call k stops extending call k−1** (share of lead calls ≥ 2):", "",
              "| condition | config | " + " | ".join(
                  ["extension", "system/tools", "reply re-render", "compaction", "history (other)"]) + " |",
              "|---|---|---|---|---|---|---|"]
    for (condition, config), counter in causes.items():
        total = sum(counter.values()) or 1
        comp = sum(v for k, v in counter.items() if k and k.startswith("compaction"))
        cells = [counter.get("extension", 0), counter.get("system/tools", 0),
                 counter.get("reply re-render", 0), comp, counter.get("history (other)", 0)]
        lines.append(f"| {condition} | {config} | " + " | ".join(f"{v / total:.1%}" for v in cells) + " |")
    lines += ["", "**Compaction-free calls** (calls ≥ 2 whose miss is not a compaction edit): token-weighted "
              "reuse against its ceiling, the share of the prompt that the previous call's sequence could "
              "supply at all (the rest is new content: the tool results and the new turn):", "",
              "| condition | config | calls | reuse | ceiling | realized share of the reusable prefix |",
              "|---|---|---|---|---|---|"]
    for condition in ("OFF", "R1"):
        for config in CONFIGS:
            free = [c for c in per_call if c["config"] == config and c["condition"] == condition and c["k"] > 0
                    and not str(c.get("cause", "")).startswith("compaction")]
            if not free:
                continue
            P = sum(c["prompt_tokens"] for c in free)
            cached = sum(c["cached"] for c in free)
            reusable = sum(c.get("reusable", 0) for c in free)
            lines.append(f"| {condition} | {config} | {len(free)} | {cached / P:.1%} | {reusable / P:.1%} | "
                         f"{cached / max(1, reusable):.1%} |")
    off_c = [c for c in per_call if c["config"] == "C" and c["condition"] == "OFF" and c["k"] > 0
             and not str(c.get("cause", "")).startswith("compaction")]
    if off_c:
        ext = sum(c["cause"] == "extension" for c in off_c) / len(off_c)
        realized = sum(c["cached"] for c in off_c) / max(1, sum(c.get("reusable", 0) for c in off_c))
        gate = ext >= 0.98 and realized >= 0.98
        lines += ["", f"**Gate E0** (C, compaction-free calls): exact extensions {ext:.1%} (≥ 98%) and "
                  f"{realized:.1%} of the reusable prefix realized (≥ 98%): **{'pass' if gate else 'FAIL'}**. "
                  "[corrected 2026-10-09 before any EPIC run: the pre-registered second condition, "
                  "token-weighted reuse ≥ 95%, is infeasible, since its ceiling (the reusable share of the "
                  "prompt) is 92.0% for these calls]"]
    kl_path = OUT / "kl.jsonl"
    if kl_path.exists():
        lines += ["", *kl_table(open_jsonl(kl_path))]
    (OUT / "e0_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# -- template-shift check -----------------------------------------------------------------------------

def topk_kl(p: dict, q: dict) -> float:
    """KL(p || q) over p's top-20 tokens; q's missing entries floored at q's smallest logprob."""
    floor = min(q.values()) if q else -30.0
    return sum(math.exp(lp) * (lp - q.get(t, floor)) for t, lp in p.items())


def kl_table(rows: list[dict]) -> list[str]:
    out = ["**Template shift** (same request rendered with the official vs the append-only template; "
           "A/A = the official rendering twice in different batches):", "",
           "| comparison | calls | first-token KL mean / median (nats) | top-1 agreement | "
           "greedy 48-token identical | common prefix (tokens, mean) |", "|---|---|---|---|---|---|"]
    for name in ("shift", "aa"):
        sub = [r for r in rows if f"kl_{name}" in r]
        if not sub:
            continue
        kl_mean = statistics.mean(r[f"kl_{name}"] for r in sub)
        kl_med = statistics.median(r[f"kl_{name}"] for r in sub)
        top1 = statistics.mean(r[f"top1_{name}"] for r in sub)
        same = statistics.mean(r[f"same_{name}"] for r in sub)
        pref = statistics.mean(r[f"prefix_{name}"] for r in sub)
        out.append(f"| {'official vs append-only' if name == 'shift' else 'A/A (official twice)'} | "
                   f"{len(sub)} | {kl_mean:.4f} / {kl_med:.4f} | {top1:.1%} | {same:.1%} | {pref:.1f} |")
    return out


def cmd_kl(args) -> None:
    if not args.inner:
        gpu = ec.pick_gpu(args.gpu)
        code = ec.run_isolated(gpu, [ec.PYTHON, __file__, "kl", "--inner", "--calls", str(args.calls)],
                               OUT / "logs" / "kl.log")
        raise SystemExit(code)
    from vllm import LLM, SamplingParams

    rend = {"off": ec.renderer(None), "ao": ec.renderer(ec.TEMPLATE.read_text())}
    pool = []
    for run_dir in sorted(RUNS.glob("E0-*-C-*")):
        recs = lead_records(run_dir)
        pool += [r for r in recs[2:]]
    rng = random.Random("e0-kl")
    sample = rng.sample(pool, min(args.calls, len(pool)))
    prompts = []
    for rec in sample:
        _, off = rend["off"].render(rec)
        _, ao = rend["ao"].render(rec)
        if max(len(off), len(ao)) > 32000:
            continue
        prompts.append((rec, off, ao))
    llm = LLM(model=ec.MODEL, max_model_len=32768, gpu_memory_utilization=0.92, enable_prefix_caching=True,
              seed=0, max_num_seqs=16)
    sp = SamplingParams(max_tokens=48, temperature=0.0, logprobs=20)

    def gen(token_lists):
        outs = llm.generate([{"prompt_token_ids": ids} for ids in token_lists], sp, use_tqdm=False)
        res = []
        for out in outs:
            o = out.outputs[0]
            first = {t: lp.logprob for t, lp in o.logprobs[0].items()} if o.logprobs else {}
            res.append((list(o.token_ids), first))
        return res

    ao_out = gen([p[2] for p in prompts])
    off_out = gen([p[1] for p in prompts])
    order = list(range(len(prompts)))
    rng.shuffle(order)
    off2 = gen([prompts[i][1] for i in order])
    off2 = [off2[order.index(i)] for i in range(len(prompts))]
    rows = []
    for (rec, off, ao), (ta, fa), (to, fo), (to2, fo2) in zip(prompts, ao_out, off_out, off2):
        row = {"label": rec.get("request_id"), "len_off": len(off), "len_ao": len(ao)}
        for name, (tb, fb) in (("shift", (ta, fa)), ("aa", (to2, fo2))):
            row[f"kl_{name}"] = topk_kl(fo, fb)
            row[f"top1_{name}"] = int(bool(to) and bool(tb) and to[0] == tb[0])
            row[f"same_{name}"] = int(to == tb)
            row[f"prefix_{name}"] = first_diff(to, tb)
        rows.append(row)
    (OUT / "kl.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print("\n".join(kl_table(rows)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--only", default="", help="comma-separated subset of A,B,C")
    r.add_argument("--cells", type=int, default=0, help="first N cells per condition (smoke)")
    r.add_argument("--gpu", type=int, default=None)
    r.add_argument("--pre-commit", default="b64bc9f", help="the tree of configuration A (pre-change)")
    k = sub.add_parser("kl")
    k.add_argument("--calls", type=int, default=150)
    k.add_argument("--gpu", type=int, default=None)
    k.add_argument("--inner", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    {"run": cmd_run, "kl": cmd_kl, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
