#!/usr/bin/env python3
"""Stage 2a of research/21_precautionary_work (GPU, about 2-3 GPU-h): does MiniMax-M2.7 follow a targeted front-load
line? Recorded SWE-bench Pro / Terminal-Bench requests are sampled again with a line appended to the newest user
message, the way the harness appends its status line, and the samples are scored against what the trial did later.

Items (`items`; from topic 20's 58 labeled Pro + TB trials, `rd_analyze.load_run("main")`):
  - pair: for each heavy round h (>= 1 s of tools) that joins an earlier chain group k..h, every request L_j with j in
    [k, h). Could L_j's response issue c_h (and the rounds between) without seeing their results? The chain test says
    it could; does the line make it do so? h's tools >= 10 s: the primary items;
  - premature: heavy rounds labeled dependent on round h-1, probed at L_{h-1}: issuing c_h there would run it before
    the results it needs;
  - spurious: 100 requests (50 Pro, 50 TB, seeded) whose own round and next three rounds have < 1 s of tools: any
    long-running command there is invented.
Conditions per distinct request, S samples each (topic 20's sampling: temperature 1.0, top_p 0.95, top_k 40):
  F the front-load line (pw_rules.LINE), N a neutral line of about the same length (pw_rules.NEUTRAL); C, no line,
  is topic 20's stored control samples of the same request (its texts.jsonl), plus a fresh C on 40 seeded requests
  with topic 20's seeds to check for drift.

Scoring per sample (`analyze`; action keys and matching from rd_cf): early = it issues c_h (target level; loose level
as a secondary); valid = early, and every round m in [j, h) whose effects h depends on (static eff_deps) is issued
too; kept = it still issues c_j, the recorded action; long = it issues a long-running command class
(pw_rules.long_class). Statistics: per item the share of samples, averaged over items; 95 % intervals from a
trial-cluster bootstrap (2,000 draws).

    python research/21_precautionary_work/pw_probe.py items              # data/probe/items.json
    python research/21_precautionary_work/pw_probe.py check              # CPU: renders, append-only, line tokens
    python research/21_precautionary_work/pw_probe.py run --url URL --out main
    python research/21_precautionary_work/pw_probe.py analyze --out main # data/tables/probe_main.{md,json}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import pw_rules as pr  # noqa: E402
import rd_analyze as ra  # noqa: E402
import rd_cf as cf  # noqa: E402
import rd_rounds as rr  # noqa: E402

DIR = Path(__file__).resolve().parent
PROBE = DIR / "data" / "probe"
TABLES = DIR / "data" / "tables"
T20_TEXTS = cf.CF / "main" / "texts.jsonl"
HEAVY_S, LONG_S = 1.0, 10.0
KINDS = ("swepro", "tbench")
N_SPURIOUS, N_FRESH_C, SEED = 100, 40, 21
LINES = {"F": pr.LINE, "N": pr.NEUTRAL}
DRAWS = 2000
# G2's probe part, frozen in prereg_2a.md
H1_MIN, H1_LO_MIN = 0.15, 0.05
OUT_RATIO_MAX, PREMATURE_MAX, SPURIOUS_MAX, KEPT_MIN = 1.05, 0.05, 0.05, -0.05


# -- items ----------------------------------------------------------------------------------------------------------
def build_items(data: dict) -> dict:
    S, tau = data["meta"]["S"], data["meta"]["tau"]
    items = [i for i in data["items"] if i["src"] == "t16" and i["kind"] in KINDS]
    by = {(i["sid"], i["turn"], i["j"]): i for i in items}
    last = defaultdict(int)
    for i in items:
        last[(i["sid"], i["turn"])] = max(last[(i["sid"], i["turn"])], i["j"])
    reqs: dict[tuple, dict] = {}

    def req(sid, turn, j) -> dict:
        key = (sid, turn, j)
        if key not in reqs:
            reqs[key] = {"sid": sid, "turn": turn, "j": j, "kind": by[key]["kind"], "pair": [], "premature": [],
                         "spurious": False}
        return reqs[key]

    for h in items:
        t = h["tool_s"] or 0.0
        if t < HEAVY_S or h["j"] == 0:
            continue
        if h["chain"] is not None and not h["chain"]["start"]:
            k = h["chain"]["k"]
            deps = sorted(set((h["static"] or {}).get("eff_deps") or []))
            for j in range(k, h["j"]):
                req(h["sid"], h["turn"], j)["pair"].append({"h": h["j"], "k": k, "tool_s": t, "long": t >= LONG_S,
                                                            "deps": deps})
        elif ra.label(h, S, tau) == "dependent":
            req(h["sid"], h["turn"], h["j"] - 1)["premature"].append({"h": h["j"], "tool_s": t})
    cands = defaultdict(list)
    for i in sorted(items, key=lambda i: (i["sid"], i["turn"], i["j"])):
        key = (i["sid"], i["turn"], i["j"])
        if i["j"] == 0 or i["final"] or key in reqs:
            continue
        nxt = [by.get((i["sid"], i["turn"], x)) for x in range(i["j"], min(i["j"] + 3, last[key[:2]]) + 1)]
        if all(x is not None and (x["tool_s"] or 0.0) < HEAVY_S for x in nxt):
            cands[i["kind"]].append(key)
    rng = random.Random(SEED)
    for kind in KINDS:
        for key in rng.sample(cands[kind], min(N_SPURIOUS // len(KINDS), len(cands[kind]))):
            req(*key)["spurious"] = True
    out = sorted(reqs.values(), key=lambda r: (r["sid"], r["turn"], r["j"]))
    pair_keys = [(r["sid"], r["turn"], r["j"]) for r in out if r["pair"]]
    fresh = sorted(random.Random(SEED + 1).sample(pair_keys, min(N_FRESH_C, len(pair_keys))))
    for r in out:
        r["fresh_c"] = (r["sid"], r["turn"], r["j"]) in fresh
    return {"seed": SEED, "line": pr.LINE, "neutral": pr.NEUTRAL, "requests": out,
            "counts": {"requests": len(out), "pair_requests": sum(bool(r["pair"]) for r in out),
                       "pairs": sum(len(r["pair"]) for r in out),
                       "pairs_long": sum(p["long"] for r in out for p in r["pair"]),
                       "premature": sum(len(r["premature"]) for r in out),
                       "spurious": sum(r["spurious"] for r in out), "fresh_c": len(fresh)}}


def load_items() -> dict:
    return json.loads((PROBE / "items.json").read_text())


def context(data: dict) -> tuple[dict, dict]:
    """(sessions by sid, rows by (sid, turn, j)) for the probe's trials."""
    rows = {(sid, r["turn"], r["j"]): r for sid, rs_ in data["rows"].items() for r in rs_}
    return data["sessions"], rows


def requests_for(item: dict, sess: dict, row: dict) -> dict:
    base = cf.control_request(row)
    out = {v: pr.with_line(base, text) for v, text in LINES.items()}
    if item.get("fresh_c"):
        out["C"] = base
    return out


# -- CPU check --------------------------------------------------------------------------------------------------------
def check(workers: int) -> int:
    """Before any GPU time: every control renders to the recorded prompt tokens, and the lines only append: the
    rendered F and N prompts keep the control's tokens up to its generation tail."""
    data = ra.load_run("main")
    sessions, rows = context(data)
    its = load_items()["requests"]
    jobs = []
    for it in its:
        sess, row = sessions[it["sid"]], rows[(it["sid"], it["turn"], it["j"])]
        base = cf.control_request(row)
        jobs += [(base, sess["template"]), (pr.with_line(base, pr.LINE), sess["template"]),
                 (pr.with_line(base, pr.NEUTRAL), sess["template"])]
    with ProcessPoolExecutor(workers, initializer=cf._init_worker) as pool:
        ids = list(pool.map(cf.render_ids, jobs, chunksize=4))
    v1 = tails = 0
    extra = Counter()
    worst = 0
    for n, it in enumerate(its):
        c, f, nn = ids[3 * n], ids[3 * n + 1], ids[3 * n + 2]
        row = rows[(it["sid"], it["turn"], it["j"])]
        v1 += len(c) == row["np"]
        for v, x in (("F", f), ("N", nn)):
            lcp = next((i for i, (a, b) in enumerate(zip(c, x)) if a != b), min(len(c), len(x)))
            worst = max(worst, len(c) - lcp)
            tails += len(c) - lcp <= 8
            extra[(v, len(x) - len(c))] += 1
    print(f"[check] control renders == recorded prompt tokens: {v1}/{len(its)}")
    print(f"[check] F and N keep the control up to its last <= 8 tokens: {tails}/{2 * len(its)} (worst {worst})")
    print(f"[check] tokens added (condition, count): {sorted(extra.items())}")
    ok = v1 == len(its) and tails == 2 * len(its)
    print(f"[check] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


# -- the GPU run ------------------------------------------------------------------------------------------------------
class ProbeRunner(cf.Runner):
    """rd_cf's sampler (warm-up request, then n=S samples with a per-request seed; records and texts appended, so a
    stopped run resumes), driven by a list of requests instead of turns."""

    async def run_items(self, jobs: list[tuple]) -> None:
        import aiohttp
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0))
        t0 = time.time()
        try:
            await asyncio.gather(*(self.variant(sess, row, v, req, rk, cwd, set()) for sess, row, v, req, rk, cwd in jobs))
        finally:
            await self.session.close()
            self.texts.close()
            self.records.close()
            self.pool.shutdown()
        print(f"[probe] done in {time.time() - t0:.0f} s: {dict(self.stats)}", flush=True)


def run(url: str, out: str, S: int, inflight: int, renderers: int) -> None:
    data = ra.load_run("main")
    sessions, rows = context(data)
    items = load_items()
    odir = PROBE / out
    odir.mkdir(parents=True, exist_ok=True)
    (odir / "run.json").write_text(json.dumps({"url": url, "S": S, "inflight": inflight, "t_start": time.time(),
                                               "model": cf.MODEL, "sampling": cf.SAMPLING,
                                               "max_tokens": cf.MAX_TOKENS, "line": pr.LINE,
                                               "neutral": pr.NEUTRAL}, indent=1) + "\n")
    jobs = []
    for it in sorted(items["requests"], key=lambda it: -rows[(it["sid"], it["turn"], it["j"])]["np"]):
        sess, row = sessions[it["sid"]], rows[(it["sid"], it["turn"], it["j"])]
        cwd = cf.session_cwd(sess)
        rk = {lv: cf.key_set(row["calls"], cwd, lv) for lv in ("target", "exact", "loose")}
        for v, req in requests_for(it, sess, row).items():
            jobs.append((sess, row, v, req, rk, cwd))
    print(f"[probe] {len(jobs)} sampling requests ({Counter(j[2] for j in jobs)}), S={S}", flush=True)
    runner = ProbeRunner(url, odir, S, cf.TAU_DEFAULT, inflight, renderers)
    asyncio.run(runner.run_items(jobs))


# -- analysis ---------------------------------------------------------------------------------------------------------
def read_texts(path: Path, keys: set) -> dict:
    """(sid, turn, j, variant) -> {sample index: text}, for the wanted keys only."""
    out = defaultdict(dict)
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            d = json.loads(line)
            key = (d["sid"], d["turn"], d["j"], d["variant"])
            if key in keys and d.get("k") is None:
                out[key][d["i"]] = d.get("text") or ""
    return out


def score_request(it: dict, texts: dict, rows: dict, cwd) -> dict:
    """Per sample of one (request, condition): early/valid per pair target, kept, long, premature per target."""
    row_j = rows[(it["sid"], it["turn"], it["j"])]
    kj = cf.key_set(row_j["calls"], cwd, "target")
    tk = {}
    for p in it["pair"] + it["premature"]:
        rh = rows[(it["sid"], it["turn"], p["h"])]
        tk[p["h"]] = {lv: cf.key_set(rh["calls"], cwd, lv) for lv in ("target", "loose")}
    mid = {m: cf.key_set(rows[(it["sid"], it["turn"], m)]["calls"], cwd, "target")
           for p in it["pair"] for m in range(it["j"], p["h"])}
    samples = []
    for i in sorted(texts):
        g = cf.parse_generation(texts[i])
        ks = {lv: cf.key_set(g["calls"], cwd, lv) for lv in ("target", "loose")}
        s = {"kept": cf.matches(ks["target"], kj), "long": bool(pr.long_calls(g["calls"])), "n_calls": len(g["calls"]),
             "closed": g["closed"], "early": {}, "early_loose": {}, "valid": {}, "premature": {}}
        for p in it["pair"]:
            e = cf.matches(ks["target"], tk[p["h"]]["target"])
            s["early"][p["h"]] = e
            s["early_loose"][p["h"]] = cf.matches(ks["loose"], tk[p["h"]]["loose"])
            s["valid"][p["h"]] = e and all(m not in p["deps"] or cf.matches(ks["target"], mid[m])
                                           for m in range(it["j"], p["h"]))
        for p in it["premature"]:
            s["premature"][p["h"]] = cf.matches(ks["target"], tk[p["h"]]["target"])
        samples.append(s)
    return {"n": len(samples), "samples": samples}


def boot(units: list[tuple[str, float]], rng: random.Random) -> tuple:
    """Mean over units with a cluster (trial) bootstrap: (mean, lo, hi)."""
    if not units:
        return (None, None, None)
    by = defaultdict(list)
    for sid, v in units:
        by[sid].append(v)
    keys = sorted(by)
    mean = sum(v for _, v in units) / len(units)
    draws = []
    for _ in range(DRAWS):
        pick = [rng.choice(keys) for _ in keys]
        vals = [v for k in pick for v in by[k]]
        draws.append(sum(vals) / len(vals))
    draws.sort()
    return (mean, draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1])


def analyze(out: str) -> dict:
    data = ra.load_run("main")
    sessions, rows = context(data)
    items = load_items()["requests"]
    odir = PROBE / out
    recs = {}
    for r in rr.read_jsonl(odir / "records.jsonl"):
        recs[(r["sid"], r["turn"], r["j"], r["variant"])] = r
    want = {(it["sid"], it["turn"], it["j"], v) for it in items for v in ("F", "N", "C")}
    mine = read_texts(odir / "texts.jsonl", want)
    t20 = read_texts(T20_TEXTS, {(it["sid"], it["turn"], it["j"], "C") for it in items})
    sc = {}
    for it in items:
        cwd = cf.session_cwd(sessions[it["sid"]])
        key = (it["sid"], it["turn"], it["j"])
        for v in ("F", "N"):
            if mine.get((*key, v)):
                sc[(*key, v)] = score_request(it, mine[(*key, v)], rows, cwd)
        if t20.get((*key, "C")):
            sc[(*key, "C")] = score_request(it, t20[(*key, "C")], rows, cwd)
        if it.get("fresh_c") and mine.get((*key, "C")):
            sc[(*key, "C1")] = score_request(it, mine[(*key, "C")], rows, cwd)

    def rate(s, f):
        return sum(f(x) for x in s["samples"]) / s["n"] if s and s["n"] else None

    rng = random.Random(SEED)
    res = {"counts": load_items()["counts"], "conditions": {}}
    # pair items: per (request, h)
    for scope, keep in (("long", lambda p: p["long"]), ("all", lambda p: True)):
        for metric in ("valid", "early", "early_loose"):
            for v in ("F", "N", "C"):
                units = []
                for it in items:
                    s = sc.get((it["sid"], it["turn"], it["j"], v))
                    for p in it["pair"]:
                        if keep(p) and s:
                            units.append((it["sid"], rate(s, lambda x, h=p["h"], m=metric: x[m][h])))
                res["conditions"][f"pair_{scope}.{metric}.{v}"] = boot(units, rng)
            diff = []
            for it in items:
                sf, sn = sc.get((it["sid"], it["turn"], it["j"], "F")), sc.get((it["sid"], it["turn"], it["j"], "N"))
                for p in it["pair"]:
                    if keep(p) and sf and sn:
                        diff.append((it["sid"], rate(sf, lambda x, h=p["h"], m=metric: x[m][h])
                                     - rate(sn, lambda x, h=p["h"], m=metric: x[m][h])))
            res["conditions"][f"pair_{scope}.{metric}.F-N"] = boot(diff, rng)
            diffc = []
            for it in items:
                sf, sc_ = sc.get((it["sid"], it["turn"], it["j"], "F")), sc.get((it["sid"], it["turn"], it["j"], "C"))
                for p in it["pair"]:
                    if keep(p) and sf and sc_:
                        diffc.append((it["sid"], rate(sf, lambda x, h=p["h"], m=metric: x[m][h])
                                      - rate(sc_, lambda x, h=p["h"], m=metric: x[m][h])))
            res["conditions"][f"pair_{scope}.{metric}.F-C"] = boot(diffc, rng)
    # by benchmark and adjacency, valid F-N on all pairs
    for name, keep in (("swepro", lambda it, p: it["kind"] == "swepro"), ("tbench", lambda it, p: it["kind"] == "tbench"),
                       ("adjacent", lambda it, p: p["h"] == it["j"] + 1), ("farther", lambda it, p: p["h"] > it["j"] + 1)):
        diff = []
        for it in items:
            sf, sn = sc.get((it["sid"], it["turn"], it["j"], "F")), sc.get((it["sid"], it["turn"], it["j"], "N"))
            for p in it["pair"]:
                if keep(it, p) and sf and sn:
                    diff.append((it["sid"], rate(sf, lambda x, h=p["h"]: x["valid"][h])
                                 - rate(sn, lambda x, h=p["h"]: x["valid"][h])))
        res["conditions"][f"by.{name}.valid.F-N"] = boot(diff, rng)
    # premature, spurious, kept, output tokens
    for v in ("F", "N", "C"):
        prem = [(it["sid"], rate(sc[(it["sid"], it["turn"], it["j"], v)], lambda x, h=p["h"]: x["premature"][h]))
                for it in items for p in it["premature"] if (it["sid"], it["turn"], it["j"], v) in sc]
        res["conditions"][f"premature.{v}"] = boot(prem, rng)
        spur = [(it["sid"], rate(sc[(it["sid"], it["turn"], it["j"], v)], lambda x: x["long"]))
                for it in items if it["spurious"] and (it["sid"], it["turn"], it["j"], v) in sc]
        res["conditions"][f"spurious_long.{v}"] = boot(spur, rng)
        kept = [(it["sid"], rate(sc[(it["sid"], it["turn"], it["j"], v)], lambda x: x["kept"]))
                for it in items if (it["sid"], it["turn"], it["j"], v) in sc]
        res["conditions"][f"kept.{v}"] = boot(kept, rng)
    for name, f in (("premature", lambda s, it: [rate(s, lambda x, h=p["h"]: x["premature"][h])
                                                 for p in it["premature"]]),
                    ("spurious_long", lambda s, it: [rate(s, lambda x: x["long"])] if it["spurious"] else []),
                    ("kept", lambda s, it: [rate(s, lambda x: x["kept"])])):
        diff = []
        for it in items:
            sf, sn = sc.get((it["sid"], it["turn"], it["j"], "F")), sc.get((it["sid"], it["turn"], it["j"], "N"))
            if sf and sn:
                diff += [(it["sid"], a - b) for a, b in zip(f(sf, it), f(sn, it)) if a is not None and b is not None]
        res["conditions"][f"{name}.F-N"] = boot(diff, rng)
    out_tok = defaultdict(list)
    for (sid, turn, j, v), r in recs.items():
        if r.get("status") == "ok" and r.get("out_tokens") is not None:
            out_tok[v].append(r["out_tokens"] / max(1, len(r.get("samples") or [])))
    res["out_tokens_per_sample"] = {v: sum(x) / len(x) for v, x in out_tok.items() if x}
    t20_out = []
    for r in rr.read_jsonl(cf.CF / "main" / "records.jsonl"):
        if r["variant"] == "C" and (r["sid"], r["turn"], r["j"]) in {(it["sid"], it["turn"], it["j"]) for it in items} \
                and r.get("status") == "ok" and r.get("out_tokens") is not None:
            t20_out.append(r["out_tokens"] / max(1, len(r.get("samples") or [])))
    if t20_out:
        res["out_tokens_per_sample"]["C"] = sum(t20_out) / len(t20_out)
    # drift: fresh C (topic 20's seeds) against topic 20's stored C
    drift, same_text = [], 0
    n_fresh = 0
    for it in items:
        key = (it["sid"], it["turn"], it["j"])
        if (*key, "C1") in sc and (*key, "C") in sc:
            n_fresh += 1
            drift.append((it["sid"], rate(sc[(*key, "C1")], lambda x: x["kept"]) - rate(sc[(*key, "C")], lambda x: x["kept"])))
            same_text += mine.get((*key, "C"), {}) == t20.get((*key, "C"), {})
    res["drift"] = {"requests": n_fresh, "kept.C1-C": boot(drift, rng), "identical_texts": same_text}
    # gate G2 (probe part) and the compliance estimate the simulator uses
    c = res["conditions"]
    h1, h1_lo = c["pair_long.valid.F-N"][0], c["pair_long.valid.F-N"][1]
    ratio = (res["out_tokens_per_sample"].get("F") or 0) / (res["out_tokens_per_sample"].get("N") or 1)
    res["gate"] = {"H1_valid_F-N": h1, "H1_lo": h1_lo, "H1": h1 is not None and h1 >= H1_MIN and h1_lo > H1_LO_MIN,
                   "out_ratio_F/N": ratio, "out_ok": ratio <= OUT_RATIO_MAX,
                   "premature_F-N": c["premature.F-N"][0],
                   "premature_ok": c["premature.F-N"][0] is not None and c["premature.F-N"][0] <= PREMATURE_MAX,
                   "spurious_F-N": c["spurious_long.F-N"][0],
                   "spurious_ok": c["spurious_long.F-N"][0] is not None and c["spurious_long.F-N"][0] <= SPURIOUS_MAX,
                   "kept_F-N": c["kept.F-N"][0], "kept_ok": c["kept.F-N"][0] is not None and c["kept.F-N"][0] >= KEPT_MIN}
    res["gate"]["probe_part_passes"] = all(res["gate"][k] for k in ("H1", "out_ok", "premature_ok", "spurious_ok",
                                                                    "kept_ok"))
    # the reference for the simulator's inputs: C, unless the fresh controls drifted > 10 points from topic 20's
    d = res["drift"]["kept.C1-C"][0]
    ref = "N" if d is not None and abs(d) > 0.10 else "C"
    o = res["out_tokens_per_sample"]
    res["for_sim"] = {"reference": ref,
                      "p_hat": max(0.0, (c["pair_all.valid.F"][0] or 0) - (c[f"pair_all.valid.{ref}"][0] or 0)),
                      "p_hat_long": max(0.0, (c["pair_long.valid.F"][0] or 0) - (c[f"pair_long.valid.{ref}"][0] or 0)),
                      f"out_inflation": ((o.get("F") or 0) / (o.get(ref) or 1)) - 1,
                      "q_spurious": max(0.0, (c["spurious_long.F"][0] or 0) - (c[f"spurious_long.{ref}"][0] or 0))}
    return res


def render(res: dict, out: str) -> list[str]:
    c, g = res["conditions"], res["gate"]

    def f(t):
        v, lo, hi = t
        if v is None:
            return "—"
        return f"{100 * v:+.1f} [{100 * lo:+.1f}, {100 * hi:+.1f}]" if lo is not None else f"{100 * v:+.1f}"

    def r(t):
        v, lo, hi = t
        return "—" if v is None else (f"{100 * v:.1f} % [{100 * lo:.1f}, {100 * hi:.1f}]" if lo is not None else f"{100 * v:.1f} %")

    n = res["counts"]
    L = [f"# Compliance probe `{out}` (pw_probe.py; Stage 2a)", "",
         f"Items: {n['pair_requests']} requests carry {n['pairs']} (request, heavy round) pairs ({n['pairs_long']} with "
         f">= 10 s of tools, the primary items); {n['premature']} premature controls; {n['spurious']} spurious controls; "
         f"{n['fresh_c']} fresh controls. F: the front-load line; N: a neutral line of about the same length; C: no line "
         "(topic 20's stored samples).", "",
         "## Pairs: does the response at L_j issue the heavy round's command?", "",
         "| scope | measure | F | N | C | F − N (points) | F − C (points) |", "|---|---|---|---|---|---|---|"]
    for scope in ("long", "all"):
        for m, lab in (("valid", "valid early issue"), ("early", "issues it (target)"), ("early_loose", "issues it (loose)")):
            L.append(f"| {'>= 10 s' if scope == 'long' else '>= 1 s'} | {lab} | {r(c[f'pair_{scope}.{m}.F'])} | "
                     f"{r(c[f'pair_{scope}.{m}.N'])} | {r(c[f'pair_{scope}.{m}.C'])} | {f(c[f'pair_{scope}.{m}.F-N'])} | "
                     f"{f(c[f'pair_{scope}.{m}.F-C'])} |")
    L += ["", "| split (all pairs) | valid F − N (points) |", "|---|---|"]
    for name in ("swepro", "tbench", "adjacent", "farther"):
        L.append(f"| {name} | {f(c[f'by.{name}.valid.F-N'])} |")
    L += ["", "## Costs", "", "| measure | F | N | C | F − N (points) |", "|---|---|---|---|---|"]
    for m, lab in (("premature", "premature: issues a dependent heavy command one call early"),
                   ("spurious_long", "spurious: issues a long-running command where none follows"),
                   ("kept", "kept: still issues the recorded action")):
        L.append(f"| {lab} | {r(c[f'{m}.F'])} | {r(c[f'{m}.N'])} | {r(c[f'{m}.C'])} | {f(c[f'{m}.F-N'])} |")
    o = res["out_tokens_per_sample"]
    L += ["", "Output tokens per sample: " + ", ".join(f"{v} {o[v]:.0f}" for v in ("F", "N", "C") if v in o)
          + f" (F/N {g['out_ratio_F/N']:.3f}).",
          f"Drift: {res['drift']['requests']} fresh controls with topic 20's seeds; kept C1 − C {f(res['drift']['kept.C1-C'])} "
          f"points; identical texts {res['drift']['identical_texts']}.", "",
          "## Gate G2, probe part (prereg_2a.md)", "",
          f"- H1, valid early issue F − N on the >= 10 s pairs: {f(c['pair_long.valid.F-N'])} points; needs >= "
          f"{100 * H1_MIN:.0f} with the lower bound > {100 * H1_LO_MIN:.0f}: **{'pass' if g['H1'] else 'fail'}**.",
          f"- Output tokens F/N {g['out_ratio_F/N']:.3f} (<= {OUT_RATIO_MAX}): {'pass' if g['out_ok'] else 'fail'}.",
          f"- Premature F − N {f(c['premature.F-N'])} (<= {100 * PREMATURE_MAX:.0f}): {'pass' if g['premature_ok'] else 'fail'}.",
          f"- Spurious F − N {f(c['spurious_long.F-N'])} (<= {100 * SPURIOUS_MAX:.0f}): {'pass' if g['spurious_ok'] else 'fail'}.",
          f"- Kept F − N {f(c['kept.F-N'])} (>= {100 * KEPT_MIN:.0f}): {'pass' if g['kept_ok'] else 'fail'}.",
          f"- **Probe part of G2: {'passes' if g['probe_part_passes'] else 'fails'}.**", "",
          f"For the simulator (reference {res['for_sim']['reference']}): p̂ = valid F − {res['for_sim']['reference']} on all "
          f"pairs = {res['for_sim']['p_hat']:.3f} (>= 10 s pairs {res['for_sim']['p_hat_long']:.3f}); output inflation = "
          f"{res['for_sim']['out_inflation']:+.3f}; spurious long commands q = {res['for_sim']['q_spurious']:.3f}.", ""]
    return L


def rounded(x):
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, (list, tuple)):
        return [rounded(v) for v in x]
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("items")
    c = sub.add_parser("check")
    c.add_argument("--workers", type=int, default=32)
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--S", type=int, default=cf.S_DEFAULT)
    r.add_argument("--inflight", type=int, default=16)
    r.add_argument("--renderers", type=int, default=24)
    z = sub.add_parser("analyze")
    z.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "items":
        PROBE.mkdir(parents=True, exist_ok=True)
        items = build_items(ra.load_run("main"))
        (PROBE / "items.json").write_text(json.dumps(items, indent=1, sort_keys=True) + "\n")
        print(f"[probe] items: {items['counts']} -> {PROBE / 'items.json'}")
    elif a.cmd == "check":
        sys.exit(check(a.workers))
    elif a.cmd == "run":
        run(a.url, a.out, a.S, a.inflight, a.renderers)
    else:
        res = analyze(a.out)
        TABLES.mkdir(parents=True, exist_ok=True)
        lines = render(res, a.out)
        (TABLES / f"probe_{a.out}.md").write_text("\n".join(lines))
        (TABLES / f"probe_{a.out}.json").write_text(json.dumps(rounded(res), indent=1, sort_keys=True,
                                                               allow_nan=False) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
