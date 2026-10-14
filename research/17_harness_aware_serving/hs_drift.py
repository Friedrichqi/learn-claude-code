#!/usr/bin/env python3
"""E0e drift (README section 3): the refresh value in topic 16's E-A compounded replay, what predicts it, and whether
a refresh policy passes D2 (README section 2: >= 50 % of the total refresh value while computing <= 1/3 of the tokens
a full re-prefill of the same calls computes). CPU only, a few minutes.

    /data/fritz/venv/bin/python research/17_harness_aware_serving/hs_drift.py [--reps 30] [--boot 200]

Writes data/tables/E0e_drift.md (hs_common.write_table), data/e0e/calls.jsonl (one line per edited call),
data/e0e/policies.jsonl (one line per policy) and data/e0e/summary.json.

Inputs (read-only). The E-A replay as topic 16's report merged it (16 README section 7, `epic_replay.py report --src`):
the eight shards live/main:{replay,replay_b}, live/screen:replay and
live/pilot:{replay,replay_b,replay_c,replay_d,replay_e} -- 30 recorded `prefix` runs, their first 80 lead calls,
2,290 calls, 2,260 "edited" (16's flag: some arm reused or recomputed a row, i.e. every call but a run's first).
live/pilot/replay_test (the run before the slot fix, 16 cleanup note 11:40) is not part of it. The replayed runs'
captured requests (runs/<run>/<run>.requests.jsonl.xz; replay call k is the run's k-th lead call, as
epic_replay.lead_calls takes them) give the replies' text for the read-aware proxy.

Definitions (every number in the table follows them)
- refresh value v of an edited call = reply KL(compounded arm) - reply KL(one-step arm) (README section 1), for the
  pairs shift/shift1 and epic128/epic128_1. Policies are costed for EPIC-128 serving and use the EPIC-128 pair.
- prefix cost of a call = P - E: E is the plan's exact prefix p0 (identical in every arm: they see the same tokens),
  what `prefix` serving takes from vLLM's cache; 16's `epic_latency.py jct` prices the replayed calls' prefix serving
  with the same P - E.
- a refresh at call r serves r as a full re-prefill from its first non-fresh row f, the cheapest exact refresh in prefix
  order: f = the earliest edit point (plan E of an edit call) since the last refresh. For a snip call f is the run head,
  so the refresh tokens P - f - C are exactly EPIC-128's reused rows S (every other row after them is in C already); for
  an append-only call they are the whole span since the last edit. Every row is exact afterwards.
- value credited at r: v_r (conservative: the one-step state, which the pre-registered value counts as 100 %), or
  KL_comp(r) (exact credit: a re-prefill leaves KL ~ 0).
- later calls: no reused row is older than the m edits since r. Measured: the first edit after a refresh is the
  one-step arm (m = 1) and append-only calls before it are fresh (m = 0); both get KL_one. Otherwise (README section 2,
  model-based) KL = KL_one + v * phi, phi = (s(min(a, m)) - s(anchor)) / (s(a) - s(anchor)); a = the call's age_max_over
  (an append-only call carries its last edit's), anchor = 1 at an edit call (the one-step arm's age), 0 at an
  append-only call (the one-step arm is fresh there); s = KL(compounded) / KL(one-step) over age_max, fitted on the edit
  calls (M1), or its isotonic version; "measured only" credits nothing model-based; M2 takes phi from each call's
  stale-row age histogram (its l1 bins) and the measured per-row K deviation by age.
- policy cost = (EPIC-128's computed tokens C + refresh tokens) summed over the edited calls, over the sum of P - E.
"""
from __future__ import annotations

import os

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):   # tiny matrices; the machine is shared
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hs_common as hc  # noqa: E402

SOURCES = (("main", "replay"), ("main", "replay_b"), ("screen", "replay"), ("pilot", "replay"),
           ("pilot", "replay_b"), ("pilot", "replay_c"), ("pilot", "replay_d"), ("pilot", "replay_e"))
ARMS = ("shift", "epic32", "epic64", "epic128", "shift1", "epic128_1")
PAIRS = {"shift": "shift1", "epic128": "epic128_1"}
SERVING = "epic128"
EXPECT = {"runs": 30, "calls": 2290, "edited": 2260}
HEADLINE = {"shift": 0.0127, "epic32": 0.0113, "epic64": 0.0098, "epic128": 0.0091,      # 16 data/replay_tables.md
            "shift1": 0.0043, "epic128_1": 0.0031}
N_CLS, N_AGE, N_OFF, N_LG = 4, 6, 5, 16      # epic_replay: bin = (cls * 6 + age bin) * 5 + offset bin; 16 x 4 layers
AGE_LABEL = ("0", "1", "2-3", "4-7", "8-15", "16+")
AGE_REP = (0.0, 1.0, 2.5, 5.5, 11.5, 18.0)   # representative age of each bin (M2)
OFF_LABEL = ("0-31", "32-63", "64-127", "128-511", "512+")
CLS_LABEL = ("exact prefix", "reused (overridden)", "run head (recomputed)", "computed")
AGE_FIT_BINS = ((1, 1), (2, 2), (3, 3), (4, 5), (6, 8), (9, 12), (13, 16), (17, 20), (21, 999))
MAX_AGE = 64
TEMPLATES = ("E1", "E2", "E3", "M1", "M2", "M3")
LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0)
BUDGET = 1 / 3
VALUE_BAR = 0.5
QS = (0.05, 0.10, 0.20, 0.30, 0.50)
EVERY_N = (1, 2, 3, 4, 5, 6, 7, 8, 16)
PREREG_N = (2, 4, 8)
THETAS = (0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)
THETAS_PER_TOKEN = tuple(float(x) for x in np.geomspace(1e-8, 1e-4, 17))
VALUE_MODELS = ("M1", "measured", "iso", "M2")
SHINGLE = 24
PROXY_KEYS = ("tool", "reread", "copy_moved_only", "prev_tool", "prev_reread")
OUT = hc.DATA / "e0e"


# -- inputs -----------------------------------------------------------------------------------------------------

def load_records(root: Path = hc.T16) -> list[dict]:
    """Every record of the eight E-A shards, tagged with its shard, ordered by (run, call)."""
    recs = []
    for live, name in SOURCES:
        for rec in hc.read_jsonl(root / "live" / live / name / "replay_calls.jsonl"):
            rec["src"] = f"{live}:{name}"
            recs.append(rec)
    recs.sort(key=lambda r: (r["run"], r["call"]))
    return recs


def reproduce(recs: list[dict], expect: dict = EXPECT, headline: dict = HEADLINE) -> dict:
    """16's E-A headline: counts, and the pooled mean reply KL over edited calls per arm (within rounding)."""
    edited = [r for r in recs if r.get("edited")]
    means = {}
    for arm in headline:
        vals = [r["arms"][arm]["kl"] for r in edited if (r["arms"].get(arm) or {}).get("kl") is not None]
        means[arm] = float(np.mean(vals)) if vals else float("nan")
    counts = {"runs": len({r["run"] for r in recs}), "calls": len(recs), "edited": len(edited),
              "missing_arm_kl": sum(1 for r in edited for a in ARMS if (r["arms"].get(a) or {}).get("kl") is None)}
    ok_counts = all(counts[k] == v for k, v in expect.items()) and counts["missing_arm_kl"] == 0
    ok_means = all(abs(means[a] - headline[a]) <= 0.5e-4 + 1e-12 for a in headline)
    return {"counts": counts, "means": means, "ok_counts": ok_counts, "ok_means": ok_means,
            "ok": ok_counts and ok_means}


def template_of(run: str) -> str:
    return next((p for p in run.split("-") if p[:1] in "EM" and p[1:].isdigit()), "?")


def regime_of(run: str) -> str:
    return next((p for p in run.split("-") if p in ("R1", "R2")), "?")


def l1_bins(arm_rec: dict) -> dict[tuple[int, int, int], list]:
    """The call's L1 bins as {(class, age bin, offset bin): [[dK, K*, dV, V*, n] per group of 4 layers]}."""
    out = {}
    for b, groups in (arm_rec.get("l1") or {}).items():
        b = int(b)
        out[(b // (N_AGE * N_OFF), (b % (N_AGE * N_OFF)) // N_OFF, b % N_OFF)] = groups
    return out


def l1_hist(arm_rec: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows, sum dK and sum |K*|^2 per (class, age bin); rows = the first layer group's count / 4 (n is summed over
    the group's 4 layers)."""
    rows, dk, kk = (np.zeros((N_CLS, N_AGE)) for _ in range(3))
    for (c, a, _o), groups in l1_bins(arm_rec).items():
        rows[c, a] += groups[0][4] / 4.0
        dk[c, a] += sum(g[0] for g in groups)
        kk[c, a] += sum(g[1] for g in groups)
    return rows, dk, kk


# -- the read-aware proxy (recorded requests) ---------------------------------------------------------------------

def lead_requests(run_dir: Path) -> list[dict]:
    """The run's lead calls as epic_replay.lead_calls takes them: purpose lead, a response, no error, by call_index."""
    paths = sorted(run_dir.glob("*.requests.jsonl*"))
    if not paths:
        return []
    recs = [r for r in hc.read_jsonl(paths[0]) if r.get("purpose") == "lead" and r.get("response")
            and not r.get("error")]
    return sorted(recs, key=lambda r: r["call_index"])


def _block_text(b: dict) -> str:
    t = b.get("type")
    if t == "text":
        return b.get("text") or ""
    if t == "tool_use":
        inp = b.get("input")
        return "\n".join(str(x) for x in inp.values()) if isinstance(inp, dict) else str(inp or "")
    if t == "tool_result":
        c = b.get("content")
        if isinstance(c, list):
            return "\n".join(z.get("text", "") for z in c if isinstance(z, dict))
        return str(c or "")
    return ""


def msg_text(m: dict) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else "\n".join(_block_text(b) for b in c or [] if isinstance(b, dict))


def msg_key(m: dict) -> str:
    """Identity of a message across calls (tool ids are deterministic; content compared exactly)."""
    c = m.get("content")
    if isinstance(c, str):
        return json.dumps([m.get("role"), c])
    parts = [[b.get("type"), b.get("name"), json.dumps(b.get("input"), sort_keys=True)] if b.get("type") == "tool_use"
             else [b.get("type"), _block_text(b)] for b in c or [] if isinstance(b, dict)]
    return json.dumps([m.get("role"), parts])


def read_paths(blocks) -> set[str]:
    return {str((b.get("input") or {}).get("path")) for b in blocks or []
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "read_file"
            and isinstance(b.get("input"), dict) and b["input"].get("path")}


def reply_proxy(prev_msgs: list[dict] | None, msgs: list[dict], reply_blocks: list[dict]) -> dict:
    """How the reply relates to the content the call's edit moved. `prev_msgs` = the previous call's messages plus its
    reply. Messages before the first difference are the exact head; later messages already present in prev_msgs moved
    (they survived the edit at new positions); the rest is fresh. reread: the reply reads a file whose earlier
    read_file call sits in a moved message; copy_moved_only: share of the reply's 24-character shingles (stride 4) that
    occur in moved text and nowhere in the head or fresh text."""
    names = [b.get("name") for b in reply_blocks or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
    tool = ",".join(sorted(set(n for n in names if n))) or ("text" if reply_blocks else "")
    out = {"tool": tool, "reread": False, "copy_moved_only": 0.0}
    if prev_msgs is None:
        return out
    keys, pkeys = [msg_key(m) for m in msgs], [msg_key(m) for m in prev_msgs]
    d = 0
    while d < min(len(keys), len(pkeys)) and keys[d] == pkeys[d]:
        d += 1
    old = set(pkeys)
    moved = [m for m, k in zip(msgs[d:], keys[d:]) if k in old]
    fresh = [m for m, k in zip(msgs[d:], keys[d:]) if k not in old]
    moved_reads = set().union(*[read_paths(m.get("content")) for m in moved if m.get("role") == "assistant"
                                and isinstance(m.get("content"), list)]) if moved else set()
    out["reread"] = bool(read_paths(reply_blocks) & moved_reads)
    reply = "\n".join(_block_text(b) for b in reply_blocks or [] if isinstance(b, dict))
    sh = [reply[i:i + SHINGLE] for i in range(0, max(0, len(reply) - SHINGLE + 1), 4)]
    sh = [s for s in sh if s.strip()]
    if sh:
        mv = "\n".join(msg_text(m) for m in moved)
        other = "\n".join(msg_text(m) for m in msgs[:d] + fresh)
        out["copy_moved_only"] = sum(1 for s in sh if s in mv and s not in other) / len(sh)
    return out


def read_proxy(recs: list[dict], root: Path = hc.T16) -> tuple[dict, dict]:
    """{(run, call): proxy} for every replayed call (prev_* = the previous call's reply), and alignment counts:
    replay reply tokens against the recorded output tokens (the reply excludes the final <|im_end|>)."""
    calls_of = defaultdict(set)
    src_of = {}
    for r in recs:
        calls_of[r["run"]].add(r["call"])
        src_of[r["run"]] = r["src"].split(":")[0]
    reply_of = {(r["run"], r["call"]): r.get("reply") for r in recs}
    out, align = {}, Counter()
    for run in sorted(calls_of):
        reqs = lead_requests(root / "live" / src_of[run] / "runs" / run)
        if len(reqs) <= max(calls_of[run]):
            align["missing_runs"] += 1
            continue
        prev, prev_px = None, None
        for k in range(max(calls_of[run]) + 1):
            cur = reqs[k]
            blocks = (cur["response"].get("content") or [])
            pm = None if prev is None else list(prev["messages"]) + [{"role": "assistant",
                                                                      "content": prev["response"].get("content") or []}]
            px = reply_proxy(pm, cur["messages"], blocks)
            px["prev_tool"] = prev_px["tool"] if prev_px else ""
            px["prev_reread"] = bool(prev_px["reread"]) if prev_px else False
            if k in calls_of[run]:
                out[(run, k)] = px
                ot = (cur["response"].get("usage") or {}).get("output_tokens")
                align["aligned" if ot is not None and abs(int(reply_of[(run, k)]) - (int(ot) - 1)) <= 2 else "off"] += 1
            prev, prev_px = cur, px
    return out, dict(align)


# -- the per-call table -------------------------------------------------------------------------------------------

def call_table(recs: list[dict], comp: str = SERVING, proxy: dict | None = None) -> list[dict]:
    """One row per edited call for the compounded arm `comp` and its one-step twin: plan stats, KLs, v, run history
    (edits so far, the last edit's stats), the stale-row age histogram (l1 counts) and the read-aware proxy."""
    one = PAIRS[comp]
    by_run = defaultdict(list)
    for r in recs:
        by_run[r["run"]].append(r)
    rows = []
    for run in sorted(by_run):
        last, n_edit, since, prev_reply, prev_v = None, 0, 0, 0, 0.0
        for r in sorted(by_run[run], key=lambda x: x["call"]):
            if not r.get("edited"):
                prev_reply = int(r.get("reply") or 0)
                continue
            a, b = r["arms"][comp], r["arms"][one]
            st = a["stats"]
            ped = bool(st["edited"])
            if ped:
                n_edit, since, last = n_edit + 1, 0, st
            else:
                since += 1
            hist, dk, kk = l1_hist(a)
            v = float(a["kl"]) - float(b["kl"])
            px = (proxy or {}).get((run, r["call"])) or {}
            rows.append({
                "i": len(rows), "run": run, "call": int(r["call"]), "src": r.get("src", ""),
                "template": template_of(run), "regime": regime_of(run),
                "P": int(r["P"]), "reply": int(r.get("reply") or 0), "E": int(st["E"]), "S": int(st["S"]),
                "H": int(st["H"]), "C": int(st["C"]), "runs": int(st["runs"]), "max_delta": int(st["max_delta"]),
                "age_mean": float(st["age_mean_over"]), "age_max": int(st["age_max_over"]),
                "reply_reused": int(st["reply_reused"]), "reuse_avail": int(st["reuse_avail"]), "ped": ped,
                "kl_c": float(a["kl"]), "kl_1": float(b["kl"]), "first_kl_c": float(a.get("first_kl") or 0.0), "v": v,
                "n_edit": n_edit, "since": since,
                "last_age_mean": float(last["age_mean_over"]) if last else 0.0,
                "last_age_max": int(last["age_max_over"]) if last else 0,
                "last_S": int(last["S"]) if last else 0, "prev_reply": prev_reply, "prev_v": prev_v,
                "a": int(st["age_max_over"]) if ped else (int(last["age_max_over"]) if last else 0),
                "anchor": 1 if ped else 0,
                "n0": [float(x) for x in hist[0, 1:]], "n1": [float(x) for x in hist[1, 1:]],
                "stale": float(hist[0, 1:].sum() + hist[1, 1:].sum()),
                "dk_reused": float(dk[1].sum() / kk[1].sum()) if kk[1].sum() else 0.0,
                **{k: px.get(k) for k in PROXY_KEYS},
            })
            prev_reply, prev_v = int(r.get("reply") or 0), v
    return rows


def col(rows: list[dict], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


# -- statistics ---------------------------------------------------------------------------------------------------

def rankdata(x) -> np.ndarray:
    """Ranks 0..n-1 with ties given their average rank."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    order = np.argsort(x, kind="mergesort")
    xs = x[order]
    starts = np.r_[0, np.flatnonzero(np.diff(xs) != 0) + 1]
    ends = np.r_[starts[1:], n]
    ranks = np.empty(n)
    ranks[order] = np.repeat((starts + ends - 1) / 2.0, ends - starts)
    return ranks


def spearman(x, y) -> float:
    rx, ry = rankdata(x), rankdata(y)
    rx, ry = rx - rx.mean(), ry - ry.mean()
    d = math.sqrt(float(rx @ rx) * float(ry @ ry))
    return float(rx @ ry) / d if d > 0 else float("nan")


def top_share(v, q: float) -> float:
    """Share of sum(v) held by the top round(q * n) values."""
    v = np.sort(np.asarray(v, dtype=float))[::-1]
    k = int(round(q * len(v)))
    return float(v[:k].sum() / v.sum()) if v.sum() else float("nan")


def captured(score, v, q: float) -> float:
    """Share of sum(v) on the round(q * n) calls ranked highest by `score`."""
    v = np.asarray(v, dtype=float)
    k = int(round(q * len(v)))
    top = np.argsort(-np.asarray(score, dtype=float), kind="mergesort")[:k]
    return float(v[top].sum() / v.sum()) if v.sum() else float("nan")


def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float) -> dict:
    mu = X.mean(0)
    sd = X.std(0)
    sd = np.where(sd > 0, sd, 1.0)
    Z = (X - mu) / sd
    y0 = float(y.mean())
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y0))
    return {"mu": mu, "sd": sd, "beta": beta, "y0": y0}


def ridge_predict(model: dict, X: np.ndarray) -> np.ndarray:
    return model["y0"] + ((X - model["mu"]) / model["sd"]) @ model["beta"]


def run_folds(runs, k: int, seed: str) -> list[list[str]]:
    """k disjoint groups of runs (a seeded shuffle dealt round-robin): 30 runs -> 3 x 10, i.e. 2/3 train, 1/3 test."""
    rs = sorted(set(runs))
    random.Random(seed).shuffle(rs)
    return [sorted(rs[i::k]) for i in range(k)]


def fit_predict(X, v, runs, train, test, seed: str) -> tuple[np.ndarray, float]:
    """Ridge on asinh(v / s), s = median |v| of the training calls, lambda by an inner 3-fold split of the training
    runs (squared error); returns the predictions for `test` back in v units, and the lambda."""
    s = float(np.median(np.abs(v[train]))) or 1e-6
    y = np.arcsinh(v / s)
    inner = run_folds(runs[train], 3, f"{seed}:inner")
    best = None
    for lam in LAMBDAS:
        err = 0.0
        for f in inner:
            held = np.isin(runs, f)
            m = ridge_fit(X[train & ~held], y[train & ~held], lam)
            err += float(((ridge_predict(m, X[train & held]) - y[train & held]) ** 2).sum())
        if best is None or err < best[0]:
            best = (err, lam)
    m = ridge_fit(X[train], y[train], best[1])
    return s * np.sinh(ridge_predict(m, X[test])), best[1]


def cross_validate(X: np.ndarray, v: np.ndarray, runs: np.ndarray, reps: int, seed: str) -> list[dict]:
    """`reps` random 3-fold splits by run: every call gets one out-of-fold prediction per repeat."""
    out = []
    for rep in range(reps):
        oof = np.full(len(v), np.nan)
        fold_rho, lams = [], []
        for f in run_folds(runs, 3, f"{seed}:{rep}"):
            test = np.isin(runs, f)
            pred, lam = fit_predict(X, v, runs, ~test, test, f"{seed}:{rep}:{f[0]}")
            oof[test] = pred
            fold_rho.append(spearman(pred, v[test]))
            lams.append(lam)
        out.append({"oof": oof, "rho": spearman(oof, v), "fold_rho": fold_rho, "lams": lams})
    return out


def pct(vals, lo: float = 5, hi: float = 95) -> tuple[float, float, float]:
    a = np.asarray([x for x in vals if x == x], dtype=float)
    if not len(a):
        return float("nan"), float("nan"), float("nan")
    return float(np.median(a)), float(np.percentile(a, lo)), float(np.percentile(a, hi))


def boot_runs(rows: list[dict], stat, reps: int, seed: str) -> tuple[float, float, float]:
    """stat(rows) with a 95 % interval from resampling runs (clusters)."""
    by_run = defaultdict(list)
    for r in rows:
        by_run[r["run"]].append(r)
    runs = sorted(by_run)
    rng = random.Random(seed)
    vals = [stat([x for _ in runs for x in by_run[rng.choice(runs)]]) for _ in range(reps)]
    _, lo, hi = pct(vals, 2.5, 97.5)
    return stat(rows), lo, hi


# -- the age models ------------------------------------------------------------------------------------------------

def s_param(a: float, A: float, tau: float) -> float:
    """M1: KL(compounded) / KL(one-step) at reused-row age a = 1 + A (1 - exp(-(a - 1) / tau)); s(0) = 0 (fresh)."""
    if a <= 0:
        return 0.0
    if a < 1:
        return float(a)
    return 1.0 + A * (1.0 - math.exp(-(a - 1.0) / tau))


def s_curve(a, A, tau):
    a = np.asarray(a, dtype=float)
    return np.where(a <= 0, 0.0, np.where(a < 1, a, 1.0 + A * (1.0 - np.exp(-(a - 1.0) / tau))))


def pava(values, weights) -> list[float]:
    """Weighted pool-adjacent-violators: the closest non-decreasing sequence."""
    blocks: list[list[float]] = []                     # [value, weight, members]
    for v, w in zip(values, weights):
        blocks.append([float(v), float(w), 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    return [b[0] for b in blocks for _ in range(int(b[2]))]


def fit_age_model(a, kl_c, kl_1, bins=AGE_FIT_BINS, A_grid=None, tau_grid=None) -> dict:
    """The pooled ratio sum KL_comp / sum KL_one of the edit calls per age_max bin, and M1 fitted to it: grid search of
    (A, tau) for the squared log error, weighted by calls per bin; plus the isotonic (non-decreasing) ratios, and the
    plateau (the pooled ratio over every age >= 2)."""
    A_grid = np.linspace(0.0, 8.0, 201) if A_grid is None else A_grid
    tau_grid = np.geomspace(0.2, 40.0, 100) if tau_grid is None else tau_grid
    a, kl_c, kl_1 = (np.asarray(x, dtype=float) for x in (a, kl_c, kl_1))
    use = (a >= 1) & (kl_1 > 0)
    ages = np.unique(a[use])
    S = s_curve(ages[:, None, None], A_grid[None, :, None], tau_grid[None, None, :])
    loss = np.zeros((len(A_grid), len(tau_grid)))
    table = []
    for lo, hi in bins:
        sel = use & (a >= lo) & (a <= hi)
        if not sel.any() or kl_c[sel].sum() <= 0:
            continue
        R = kl_c[sel].sum() / kl_1[sel].sum()
        w = np.array([kl_1[sel & (a == x)].sum() for x in ages]) / kl_1[sel].sum()
        loss += sel.sum() * (math.log(R) - np.log(np.tensordot(w, S, axes=(0, 0)))) ** 2
        table.append({"lo": lo, "hi": hi, "n": int(sel.sum()), "ratio": float(R), "w": w})
    i, j = np.unravel_index(int(np.argmin(loss)), loss.shape)
    A, tau = float(A_grid[i]), float(tau_grid[j])
    for t in table:
        t["fit"] = float(np.dot(t.pop("w"), s_curve(ages, A, tau)))
    later = [t for t in table if t["lo"] >= 2]
    for t, iso in zip(later, pava([max(1.0, t["ratio"]) for t in later], [t["n"] for t in later])):
        t["iso"] = iso
    for t in table:
        t.setdefault("iso", 1.0)
    old = use & (a >= 2)
    plateau = float(kl_c[old].sum() / kl_1[old].sum()) if old.any() else 1.0
    return {"A": A, "tau": tau, "bins": table, "n": int(use.sum()), "plateau": plateau}


def curve_table(kind: str, model: dict) -> list[float]:
    """s(a) for a = 0..MAX_AGE: 'M1' the fitted curve, 'iso' the isotonic bin ratios (s(1) = 1), 'step' every age >= 2
    at the plateau (all compounding at the second edit), 'flat' no compounding (s = 1)."""
    if kind == "M1":
        return [s_param(a, model["A"], model["tau"]) for a in range(MAX_AGE + 1)]
    t = [0.0] + [1.0] * MAX_AGE
    if kind == "iso":
        covered = set()
        for b in model["bins"]:
            if b["lo"] >= 2:
                for x in range(b["lo"], min(b["hi"], MAX_AGE) + 1):
                    t[x] = b["iso"]
                    covered.add(x)
        for x in range(2, MAX_AGE + 1):              # ages no call had (a resample can miss a bin): carry forward
            if x not in covered:
                t[x] = t[x - 1]
    elif kind == "step":
        t = [0.0, 1.0] + [model["plateau"]] * (MAX_AGE - 1)
    return t


def phi_curve(c: dict, m, table: list[float]) -> float:
    """Share of the call's refresh value left when no reused row is older than m edits (m = None: no refresh yet)."""
    a, anchor = c["a"], c["anchor"]
    if m is None or a <= m:
        return 1.0
    sa, s0 = table[min(a, MAX_AGE)], table[anchor]
    if sa <= s0:
        return 1.0
    return min(1.0, max(0.0, (table[min(m, MAX_AGE)] - s0) / (sa - s0)))


def phi_measured(c: dict, m) -> float:
    """Only the states a replay arm measured: the one-step arm (an edit call with m = 1, an append-only call with
    m = 0); every other capped call is credited nothing."""
    if m is None or c["a"] <= m:
        return 1.0
    return 0.0 if m == 0 or (c["ped"] and m == 1) else 1.0


def d_curve(ds: list[float]):
    """Per-row K deviation by age: piecewise linear through (AGE_REP[b], ds[b]), flat beyond the last bin."""
    xs, ys = list(AGE_REP), list(ds)

    def d(age: float) -> float:
        if age >= xs[-1]:
            return ys[-1]
        for x0, y0, x1, y1 in zip(xs, ys, xs[1:], ys[1:]):
            if age <= x1:
                return y0 + (y1 - y0) * (age - x0) / (x1 - x0)
        return ys[-1]
    return d


def phi_m2(c: dict, m, d) -> float:
    """M2: the call's excess K-deviation load (stale rows per age bin x the per-row deviation at that age) left under a
    cap of m edits, relative to its one-step anchor. Exact-prefix rows did not cross this call's edit (cap m - 1)."""
    if m is None:
        return 1.0
    reps = AGE_REP[1:]
    m0 = m - 1 if c["ped"] else m
    full = sum((n1 + n0) * d(x) for n1, n0, x in zip(c["n1"], c["n0"], reps))
    capped = sum(n1 * d(min(x, m)) + n0 * d(min(x, max(m0, 0))) for n1, n0, x in zip(c["n1"], c["n0"], reps))
    anchor = sum(c["n1"]) * d(1.0 if c["ped"] else 0.0) + sum(c["n0"]) * d(0.0)
    if full <= anchor:
        return 1.0
    return min(1.0, max(0.0, (capped - anchor) / (full - anchor)))


def value_models(model: dict, d) -> dict:
    """phi per value model: M1 (fitted), measured only, isotonic, M2 (KV load)."""
    t1, ti = curve_table("M1", model), curve_table("iso", model)
    return {"M1": lambda c, m: phi_curve(c, m, t1), "measured": phi_measured,
            "iso": lambda c, m: phi_curve(c, m, ti), "M2": lambda c, m: phi_m2(c, m, d)}


def curve_check(rows: list[dict], reps: int, seed: str) -> dict:
    """Held-out check of the age curve: fit on 2/3 of the runs, predict the other third's sum KL_comp per age bin from
    its KL_one and ages; mean |log(actual / predicted)| weighted by calls, per curve."""
    ed = [r for r in rows if r["ped"] and r["kl_1"] > 0 and r["age_max"] >= 1]
    runs = np.array([r["run"] for r in ed])
    a = np.array([r["age_max"] for r in ed])
    kc, k1 = col(ed, "kl_c"), col(ed, "kl_1")
    errs = defaultdict(list)
    for rep in range(reps):
        for f in run_folds(runs, 3, f"{seed}:curve:{rep}"):
            test = np.isin(runs, f)
            m = fit_age_model(a[~test], kc[~test], k1[~test])
            for kind in ("M1", "iso", "step", "flat"):
                t = np.array(curve_table(kind, m))
                num = den = 0.0
                for lo, hi in AGE_FIT_BINS:
                    sel = test & (a >= lo) & (a <= hi)
                    if sel.any() and kc[sel].sum() > 0:
                        pred = float((k1[sel] * t[np.minimum(a[sel], MAX_AGE)]).sum())
                        num += sel.sum() * abs(math.log(kc[sel].sum() / pred))
                        den += sel.sum()
                errs[kind].append(num / den if den else float("nan"))
    return {k: pct(v) for k, v in errs.items()}


# -- policies ------------------------------------------------------------------------------------------------------

def by_run_calls(rows: list[dict]) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for r in rows:
        out[r["run"]].append(r)
    return {k: sorted(v, key=lambda c: c["call"]) for k, v in sorted(out.items())}


def simulate(runs: dict[str, list[dict]], decide, phi, credit: str = "conservative") -> dict:
    """Serve every run's edited calls with EPIC-128 plus the policy's refreshes.

    decide(call, refresh_tokens, edits_since_refresh) -> bool; phi(call, m) -> remaining share of v at a call whose
    reused rows are at most m edits old (None before the run's first refresh). A refreshed call is a full re-prefill
    from its first non-fresh row: it computes C + rt, rt = P - f - C, f = the earliest edit point since the last
    refresh; its KL is the one-step arm's (credit 'conservative') or 0 ('exact'), and every row is exact after it."""
    tot_v = rec = lb = cost = epic = prefix = rt_sum = 0.0
    n = n_ref = 0
    for calls in runs.values():
        m, since, stale_from = None, 0, None
        for c in calls:
            if c["ped"]:
                stale_from = c["E"] if stale_from is None else min(stale_from, c["E"])
                since += 1
            rt = max(0, c["P"] - stale_from - c["C"]) if stale_from is not None else 0
            n += 1
            tot_v += c["v"]
            prefix += c["P"] - c["E"]
            epic += c["C"]
            if decide(c, rt, since):
                kl = c["kl_1"] if credit == "conservative" else 0.0
                cost += c["C"] + rt
                rt_sum += rt
                n_ref += 1
                lb += c["v"]
                m, since, stale_from = 0, 0, None
            else:
                m = None if m is None else m + (1 if c["ped"] else 0)
                kl = c["kl_1"] + c["v"] * phi(c, m)
                cost += c["C"]
            rec += c["kl_c"] - kl
    return {"calls": n, "refreshed": n_ref, "refresh_share": n_ref / n if n else float("nan"),
            "value": rec / tot_v if tot_v else float("nan"), "value_lb": lb / tot_v if tot_v else float("nan"),
            "rt_per_call": rt_sum / n if n else float("nan"), "cost": cost / prefix if prefix else float("nan"),
            "epic_only": epic / prefix if prefix else float("nan"), "total_v": tot_v}


def evaluate(runs: dict, decide, models: dict, which=VALUE_MODELS, exact: bool = True) -> dict:
    """One policy under several value models: the M1 run's counts and cost, value_<model> per model, value_exact (M1
    downstream, the refreshed calls at KL 0)."""
    base = simulate(runs, decide, models["M1"])
    out = {**base, "value_M1": base["value"]}
    for name in which:
        if name != "M1":
            out[f"value_{name}"] = simulate(runs, decide, models[name])["value"]
    if exact:
        out["value_exact"] = simulate(runs, decide, models["M1"], "exact")["value"]
    return out


def fixed_policies() -> list[tuple[str, object, str]]:
    """(name, decide, tag): tag 'pre-registered' (README section 2 candidates), 'reference', 'post hoc', 'ex post'
    (uses the reply: not implementable) or 'ex ante'."""
    pol = [("none: EPIC-128 alone", lambda c, rt, k: False, "reference"),
           ("(a) refresh before every call", lambda c, rt, k: True, "pre-registered")]
    for N in EVERY_N:
        tag = "pre-registered" if N in PREREG_N else ("reference" if N in (1, 16) else "post hoc")
        pol.append((f"(b) every {N} edit{'s' if N > 1 else ''}", lambda c, rt, k, N=N: c["ped"] and k >= N, tag))
    pol += [("(d) ex post: the reply re-reads a moved file", lambda c, rt, k: rt > 0 and bool(c.get("reread")),
             "ex post"),
            ("(d) ex post: >= 50 % of the reply copies moved-only text",
             lambda c, rt, k: rt > 0 and (c.get("copy_moved_only") or 0.0) >= 0.5, "ex post"),
            ("(d) ex ante: the previous reply re-read a moved file",
             lambda c, rt, k: rt > 0 and bool(c.get("prev_reread")), "ex ante"),
            ("(d) ex ante: the previous reply was read_file",
             lambda c, rt, k: rt > 0 and c.get("prev_tool") == "read_file", "ex ante")]
    return pol


def threshold_rule(pred: np.ndarray, theta: float):
    return lambda c, rt, k: rt > 0 and pred[c["i"]] >= theta


def per_token_rule(score: np.ndarray, theta: float):
    return lambda c, rt, k: rt > 0 and score[c["i"]] / rt >= theta


def best_at_budget(points: list[dict], key: str = "value_M1", budget: float = BUDGET) -> dict | None:
    ok = [p for p in points if p["cost"] <= budget + 1e-12]
    return max(ok, key=lambda p: p[key]) if ok else None


def d2_verdict(value: float, cost: float) -> str:
    if value >= VALUE_BAR and cost <= BUDGET + 1e-12:
        return "pass"
    return "fail (" + " and ".join(x for x, bad in (("value", value < VALUE_BAR), ("cost", cost > BUDGET + 1e-12))
                                   if bad) + ")"


# -- where the deviation sits -------------------------------------------------------------------------------------

def l1_accumulate(recs: list[dict], arm: str) -> np.ndarray:
    """Sum of the edited calls' L1 bins: [class, age bin, offset bin, layer group, (dK, K*, dV, V*, n)]."""
    acc = np.zeros((N_CLS, N_AGE, N_OFF, N_LG, 5))
    for r in recs:
        if r.get("edited") and arm in r["arms"]:
            for (c, a, o), groups in l1_bins(r["arms"][arm]).items():
                acc[c, a, o] += np.asarray(groups, dtype=float)
    return acc


def where_tables(recs: list[dict], comp: str = SERVING) -> tuple[list[str], dict]:
    one = PAIRS[comp]
    A, B = l1_accumulate(recs, comp), l1_accumulate(recs, one)
    dA, kA, dB, kB = A[..., 0], A[..., 1], B[..., 0], B[..., 1]
    rowsA = A[:, :, :, 0, 4] / 4.0
    excess_tot = dA.sum() - dB.sum()
    lines = [f"**Where {comp}'s deviation sits** (E-A replay, the edited calls; D^K = sum |K - K*|^2 / sum |K*|^2; "
             f"excess = the compounded arm's sum |K - K*|^2 minus its one-step twin's ({one}) on the same calls, "
             f"{excess_tot / dA.sum():.0%} of the compounded arm's total):", "",
             "| rows | share of rows | D^K compounded | D^K one-step | share of the excess |", "|---|---|---|---|---|"]
    for c, name in enumerate(CLS_LABEL):
        if rowsA[c].sum() == 0:
            continue
        ex = dA[c].sum() - dB[c].sum()
        lines.append(f"| {name} | {rowsA[c].sum() / rowsA.sum():.1%} | {dA[c].sum() / kA[c].sum():.4f} | "
                     f"{dB[c].sum() / max(kB[c].sum(), 1e-30):.4f} | {ex / excess_tot:.1%} |")
    # by age: the stale rows (every reused row, and exact-prefix rows that crossed an edit) of the compounded arm;
    # excess over the one-step arm's level (its reused rows are all one edit old)
    d_one = dB[1].sum() / kB[1].sum()
    keep = np.ones((N_CLS, N_AGE, 1, 1))
    keep[0, 0] = 0.0                                   # fresh exact-prefix rows are not stale
    keep[2:] = 0.0
    stale_d = (dA * keep).sum(axis=(0, 2, 3))
    stale_k = (kA * keep).sum(axis=(0, 2, 3))
    stale_n = (rowsA * keep[..., 0]).sum(axis=(0, 2))
    ex_age = np.maximum(stale_d - d_one * stale_k, 0.0)
    lines += ["", f"Stale rows (every reused row, and exact-prefix rows that crossed an edit) by **edits survived** "
              f"(age; the one-step arm's reused rows are all one edit old, D^K {d_one:.4f}; excess = sum |K - K*|^2 "
              "above that level):", "",
              "| age | share of the stale rows | D^K | share of their excess |", "|---|---|---|---|"]
    for a in range(N_AGE):
        lines.append(f"| {AGE_LABEL[a]} | {stale_n[a] / stale_n.sum():.1%} | "
                     f"{stale_d[a] / max(stale_k[a], 1e-30):.4f} | {ex_age[a] / ex_age.sum():.1%} |")
    off_d, off_k = dA[1].sum(axis=(0, 2)), kA[1].sum(axis=(0, 2))
    off_db, off_kb = dB[1].sum(axis=(0, 2)), kB[1].sum(axis=(0, 2))
    off_n = rowsA[1].sum(axis=0)
    ex_off = off_d - off_db
    lines += ["", "Reused rows by **offset from the start of their run** (EPIC-128 recomputes the first 128):", "",
              "| offset | share of reused rows | D^K compounded | D^K one-step | share of the reused rows' excess |",
              "|---|---|---|---|---|"]
    for o in range(N_OFF):
        if off_n[o] == 0:
            continue
        lines.append(f"| {OFF_LABEL[o]} | {off_n[o] / off_n.sum():.1%} | {off_d[o] / off_k[o]:.4f} | "
                     f"{off_db[o] / max(off_kb[o], 1e-30):.4f} | {ex_off[o] / ex_off.sum():.1%} |")
    lay_a, lay_k = dA[:2].sum(axis=(0, 1, 2)), kA[:2].sum(axis=(0, 1, 2))
    lay_b, lay_kb = dB[:2].sum(axis=(0, 1, 2)), kB[:2].sum(axis=(0, 1, 2))
    ex_lay = lay_a - lay_b
    groups = [(0, 8), (8, 10), (10, 12), (12, 14), (14, 16)]
    lines += ["", "Exact-prefix and reused rows by **layer**:", "",
              "| layers | D^K compounded | D^K one-step | share of the excess |", "|---|---|---|---|"]
    for g0, g1 in groups:
        lines.append(f"| {4 * g0}-{4 * g1 - 1} | {lay_a[g0:g1].sum() / lay_k[g0:g1].sum():.4f} | "
                     f"{lay_b[g0:g1].sum() / lay_kb[g0:g1].sum():.4f} | {ex_lay[g0:g1].sum() / ex_lay.sum():.1%} |")
    old = slice(4, 6)
    fresh_d = dA[0, 0].sum() / kA[0, 0].sum() if kA[0, 0].sum() else 0.0
    info = {"d_one": float(d_one), "d_by_age": [float(fresh_d)] + [float(stale_d[a] / stale_k[a]) if stale_k[a] else 0.0
                                                                   for a in range(1, N_AGE)],
            "old_rows": float(stale_n[old].sum() / stale_n.sum()),
            "old_excess": float(ex_age[old].sum() / ex_age.sum()),
            "young_excess": float(ex_age[1:3].sum() / ex_age.sum()),
            "off_far_rows": float(off_n[4] / off_n.sum()), "off_far_excess": float(ex_off[4] / ex_off.sum()),
            "off_near_d": float(off_d[3] / off_k[3]) if off_k[3] else 0.0, "off_far_d": float(off_d[4] / off_k[4]),
            "deep_excess": float(ex_lay[10:].sum() / ex_lay.sum()),
            "shallow_excess": float(ex_lay[:8].sum() / ex_lay.sum()),
            "reused_excess": float((dA[1].sum() - dB[1].sum()) / excess_tot),
            "prefix_excess": float((dA[0].sum() - dB[0].sum()) / excess_tot)}
    return lines, info



# -- analysis -----------------------------------------------------------------------------------------------------

SPEARMAN_FEATURES = [  # (label, value of a row, known before the call)
    ("age_mean_over", lambda r: r["age_mean"], True),
    ("age_max_over", lambda r: r["age_max"], True),
    ("age carried (age_max_over; an append-only call: its last edit's)", lambda r: r["a"], True),
    ("max_delta", lambda r: r["max_delta"], True),
    ("runs (reused runs moved)", lambda r: r["runs"], True),
    ("S / P (reuse share)", lambda r: r["S"] / r["P"], True),
    ("reuse_avail / P", lambda r: r["reuse_avail"] / r["P"], True),
    ("(P - E) / P (prefix-cost share)", lambda r: (r["P"] - r["E"]) / r["P"], True),
    ("S (reused rows)", lambda r: r["S"], True),
    ("C (EPIC-128 computed)", lambda r: r["C"], True),
    ("P (prompt tokens)", lambda r: r["P"], True),
    ("reply_reused", lambda r: r["reply_reused"], True),
    ("edit call (plan-level edit)", lambda r: float(r["ped"]), True),
    ("call index", lambda r: r["call"], True),
    ("edits so far in the run", lambda r: r["n_edit"], True),
    ("calls since the last edit", lambda r: r["since"], True),
    ("stale rows (age >= 1; exact prefix + reused)", lambda r: r["stale"], True),
    ("stale rows / P", lambda r: r["stale"] / r["P"], True),
    ("stale rows of age >= 8", lambda r: r["n0"][3] + r["n0"][4] + r["n1"][3] + r["n1"][4], True),
    ("previous reply length", lambda r: r["prev_reply"], True),
    ("previous reply was read_file", lambda r: float(r.get("prev_tool") == "read_file"), True),
    ("previous reply re-read a moved file", lambda r: float(bool(r.get("prev_reread"))), True),
    ("reply length", lambda r: r["reply"], False),
    ("reply is read_file", lambda r: float(r.get("tool") == "read_file"), False),
    ("reply re-reads a moved file", lambda r: float(bool(r.get("reread"))), False),
    ("reply's moved-only copy share", lambda r: r.get("copy_moved_only") or 0.0, False),
    ("one-step reply KL", lambda r: r["kl_1"], False),
    ("first-token KL (compounded)", lambda r: r["first_kl_c"], False),
    ("reused rows' D^K (compounded)", lambda r: r["dk_reused"], False),
    ("previous call's v (needs a fresh probe of that call)", lambda r: r["prev_v"], False),
]


def ridge_features(r: dict) -> list[float]:
    """Features known when the call arrives: plan stats, row ages, run history, the previous reply, the task type."""
    lg = math.log1p
    P = r["P"]
    return [lg(r["S"]), lg(r["C"]), lg(r["H"]), lg(P), lg(P - r["E"]), r["S"] / P, (P - r["E"]) / P, lg(r["runs"]),
            lg(r["max_delta"]), r["age_mean"], lg(r["age_max"]), float(r["ped"]), lg(r["n_edit"]), lg(r["since"]),
            lg(r["call"]), r["last_age_mean"], lg(r["last_age_max"]), lg(r["last_S"]), lg(r["prev_reply"]),
            *[lg(n0 + n1) for n0, n1 in zip(r["n0"], r["n1"])], r["stale"] / P, lg(r["reply_reused"]),
            lg(r["reuse_avail"]), float(r.get("prev_tool") == "read_file"), float(bool(r.get("prev_reread"))),
            *[float(r["template"] == t) for t in TEMPLATES], float(r["regime"] == "R2")]


def distribution(rows: list[dict]) -> dict:
    v = col(rows, "v")
    ped = col(rows, "ped") > 0
    before = (~ped) & (col(rows, "n_edit") == 0)
    after = (~ped) & ~before
    per_run = defaultdict(float)
    for r in rows:
        per_run[r["run"]] += r["v"]
    return {"calls": len(v), "total": float(v.sum()), "mean": float(v.mean()), "median": float(np.median(v)),
            "le0": float((v <= 0).mean()), **{f"top{int(q * 100)}": top_share(v, q) for q in (0.1, 0.2, 0.5)},
            "top3_runs": sum(sorted(per_run.values(), reverse=True)[:3]) / float(v.sum()),
            "edit": float(v[ped].sum() / v.sum()), "after": float(v[after].sum() / v.sum()),
            "before": float(v[before].sum() / v.sum()), "n_edit": int(ped.sum()), "n_after": int(after.sum()),
            "n_before": int(before.sum())}


def groups(rows: list[dict]) -> list[dict]:
    v = col(rows, "v")
    rep = col(rows, "reply")
    out = []

    def add(name, sel):
        if sel.any():
            out.append({"group": name, "calls": int(sel.sum()), "share_calls": float(sel.mean()),
                        "mean_v": float(v[sel].mean()), "share_v": float(v[sel].sum() / v.sum())})
    for lo, hi in ((0, 30), (30, 60), (60, 120), (120, 10 ** 9)):
        add(f"reply {lo}-{hi - 1} tokens" if hi < 10 ** 9 else f"reply >= {lo} tokens", (rep >= lo) & (rep < hi))
    for t, _n in Counter(r.get("tool") or "?" for r in rows).most_common(5):
        add(f"reply tool `{t}`", np.array([(r.get("tool") or "?") == t for r in rows]))
    add("reply re-reads a moved file", np.array([bool(r.get("reread")) for r in rows]))
    for t in TEMPLATES:
        add(f"template {t}", np.array([r["template"] == t for r in rows]))
    return out


def predictor(rows: list[dict], reps: int, seed: str) -> dict:
    v = col(rows, "v")
    runs = np.array([r["run"] for r in rows])
    X = np.array([ridge_features(r) for r in rows])
    cv = cross_validate(X, v, runs, reps, seed)
    Xp = np.c_[X, np.arcsinh(col(rows, "prev_v") / (np.median(np.abs(v)) or 1e-6))]
    cv_probe = cross_validate(Xp, v, runs, reps, seed)                 # the same splits
    return {"n_features": X.shape[1], "cv": cv, "rho": pct([c["rho"] for c in cv]),
            "fold_rho": pct([x for c in cv for x in c["fold_rho"]]), "rho_probe": pct([c["rho"] for c in cv_probe]),
            "captured": {q: pct([captured(c["oof"], v, q) for c in cv]) for q in QS},
            "captured_probe": {q: pct([captured(c["oof"], v, q) for c in cv_probe]) for q in QS},
            "captured_oracle": {q: captured(v, v, q) for q in QS},
            "lambdas": dict(sorted(Counter(lam for c in cv for lam in c["lams"]).items()))}


def age_analysis(tabs: dict, boot: int, reps: int, seed: str) -> dict:
    rows = tabs[SERVING]
    ped = col(rows, "ped") > 0
    am = fit_age_model(col(rows, "age_max")[ped], col(rows, "kl_c")[ped], col(rows, "kl_1")[ped])
    sh = tabs["shift"]
    sp_ = col(sh, "ped") > 0
    am_shift = fit_age_model(col(sh, "age_max")[sp_], col(sh, "kl_c")[sp_], col(sh, "kl_1")[sp_])
    for b in am["bins"]:
        def stat(rs, lo=b["lo"], hi=b["hi"]):
            sel = [r for r in rs if r["ped"] and lo <= r["age_max"] <= hi and r["kl_1"] > 0]
            den = sum(r["kl_1"] for r in sel)
            return sum(r["kl_c"] for r in sel) / den if den else float("nan")
        _, b["lo95"], b["hi95"] = boot_runs(rows, stat, boot, f"{seed}:bin{b['lo']}")
    shift_bins = {(b["lo"], b["hi"]): b["ratio"] for b in am_shift["bins"]}
    for b in am["bins"]:
        b["shift_ratio"] = shift_bins.get((b["lo"], b["hi"]), float("nan"))
    # run bootstrap: the same resampled runs serve every policy's interval; the age model is refit on each sample
    runs = by_run_calls(rows)
    names = list(runs)
    rng = random.Random(f"{seed}:polboot")
    samples = []
    for _ in range(boot):
        sample = {f"{r}#{j}": runs[r] for j, r in enumerate(rng.choice(names) for _ in names)}
        flat = [c for cs in sample.values() for c in cs if c["ped"]]
        samples.append((sample, fit_age_model(col(flat, "age_max"), col(flat, "kl_c"), col(flat, "kl_1"))))
    return {"model": am, "shift": am_shift, "samples": samples, "check": curve_check(rows, min(reps, 10), seed),
            "A_ci": pct([m["A"] for _s, m in samples], 2.5, 97.5),
            "tau_ci": pct([m["tau"] for _s, m in samples], 2.5, 97.5)}


def policy_analysis(rows: list[dict], age: dict, cv: list[dict], d_by_age: list[float]) -> dict:
    runs = by_run_calls(rows)
    dfun = d_curve(d_by_age)
    models = value_models(age["model"], dfun)
    fixed = []
    for name, decide, tag in fixed_policies():
        res = {"policy": name, "tag": tag, "family": name.split(" ")[0] if name.startswith("(") else "none",
               **evaluate(runs, decide, models)}
        if name.startswith(("(a)", "(b)")):
            vals = [simulate(s_, decide, value_models(m_, dfun)["M1"]) for s_, m_ in age["samples"]]
            res["value_ci"] = list(pct([x["value"] for x in vals], 2.5, 97.5)[1:])
            res["cost_ci"] = list(pct([x["cost"] for x in vals], 2.5, 97.5)[1:])
            res["pass_share"] = float(np.mean([d2_verdict(x["value"], x["cost"]) == "pass" for x in vals]))
        fixed.append(res)
    v = col(rows, "v")
    sweep_c = {th: [] for th in THETAS}
    sweep_cp = {th: [] for th in THETAS_PER_TOKEN}
    for c in cv:
        for th in THETAS:
            sweep_c[th].append(evaluate(runs, threshold_rule(c["oof"], th), models))
        for th in THETAS_PER_TOKEN:
            sweep_cp[th].append(evaluate(runs, per_token_rule(c["oof"], th), models, ("M1", "measured"), exact=False))
    keys = ("refresh_share", "value_M1", "value_measured", "value_iso", "value_M2", "value_exact", "value_lb",
            "rt_per_call", "cost")

    def summarize(name, family, pts, theta):
        out = {"policy": name, "tag": "pre-registered" if family == "(c)" else "post hoc", "family": family,
               "theta": theta}
        for k in keys:
            if k in pts[0]:
                out[k] = float(np.median([p[k] for p in pts]))
        out["value_M1_range"] = list(pct([p["value_M1"] for p in pts])[1:])
        out["cost_range"] = list(pct([p["cost"] for p in pts])[1:])
        out["pass_share"] = float(np.mean([d2_verdict(p["value_M1"], p["cost"]) == "pass" for p in pts]))
        return out
    c_rows = [summarize(f"(c) predicted v >= {th:g}", "(c)", sweep_c[th], th) for th in THETAS]
    cp_rows = [summarize(f"(c') predicted v per refresh token >= {th:.1e}", "(c')", sweep_cp[th], th)
               for th in THETAS_PER_TOKEN]
    oracle = [{"policy": f"oracle: true v per refresh token >= {th:.1e}", "tag": "oracle", "family": "oracle",
               "theta": th, **evaluate(runs, per_token_rule(v, th), models)} for th in THETAS_PER_TOKEN]
    per_split = [[sweep_c[th][i] for th in THETAS] for i in range(len(cv))]
    per_split_cp = [[sweep_cp[th][i] for th in THETAS_PER_TOKEN] for i in range(len(cv))]
    best = {
        "(b)": best_at_budget([r for r in fixed if r["family"] == "(b)"]),
        "(b) pre-registered N": best_at_budget([r for r in fixed if r["family"] == "(b)"
                                               and r["tag"] == "pre-registered"]),
        "(b) measured only": best_at_budget([r for r in fixed if r["family"] == "(b)"], "value_measured"),
        "(c)": pct([(best_at_budget(s) or {"value_M1": 0.0})["value_M1"] for s in per_split]),
        "(c) own value only": pct([(best_at_budget(s, "value_lb") or {"value_lb": 0.0})["value_lb"]
                                   for s in per_split]),
        "(c')": pct([(best_at_budget(s) or {"value_M1": 0.0})["value_M1"] for s in per_split_cp]),
        "(d) ex ante": best_at_budget([r for r in fixed if r["tag"] == "ex ante"]),
        "oracle": best_at_budget(oracle), "oracle own value only": best_at_budget(oracle, "value_lb"),
    }
    implementable = [r for r in fixed + c_rows + cp_rows if r["tag"] in ("pre-registered", "post hoc", "ex ante",
                                                                         "reference")]
    passing = {m: [r["policy"] for r in implementable if f"value_{m}" in r
                   and d2_verdict(r[f"value_{m}"], r["cost"]) == "pass"] for m in (*VALUE_MODELS, "exact")}
    enough = [r for r in implementable if r["value_M1"] >= VALUE_BAR]
    closest = {"best value under the budget": best_at_budget(implementable),
               "cheapest with >= 50 %": min(enough, key=lambda r: r["cost"]) if enough else None,
               "best pre-registered under the budget": best_at_budget([r for r in implementable
                                                                       if r["tag"] == "pre-registered"])}
    return {"fixed": fixed, "c": c_rows, "cp": cp_rows, "oracle": oracle, "best": best, "passing": passing,
            "closest": closest}


def analyze(recs: list[dict], proxy: dict, reps: int, boot: int, seed: str) -> dict:
    tabs = {comp: call_table(recs, comp, proxy) for comp in PAIRS}
    rows = tabs[SERVING]
    sp_rows = []
    for label, fn, ex_ante in SPEARMAN_FEATURES:
        sp_rows.append({"feature": label, "ex_ante": ex_ante,
                        **{comp: spearman([fn(r) for r in t], col(t, "v")) for comp, t in tabs.items()}})
    where_lines, where = where_tables(recs, SERVING)
    pred = predictor(rows, reps, seed)
    age = age_analysis(tabs, boot, reps, seed)
    pol = policy_analysis(rows, age, pred["cv"], where["d_by_age"])
    prefix = float(sum(r["P"] - r["E"] for r in rows))
    epic = float(sum(r["C"] for r in rows))
    return {"tabs": tabs, "dist": {comp: distribution(t) for comp, t in tabs.items()}, "groups": groups(rows),
            "spearman": sp_rows, "predictor": pred, "age": age, "policies": pol, "where_lines": where_lines,
            "where": where, "tokens": {"prefix": prefix, "epic128": epic, "epic_share": epic / prefix,
                                       "S": float(sum(r["S"] for r in rows)),
                                       "budget": (BUDGET - epic / prefix) * prefix, "calls": len(rows)}}


# -- report ---------------------------------------------------------------------------------------------------------

def pc(x, digits: int = 0) -> str:
    if x is None or x != x:
        return "—"
    x = 0.0 if abs(x) < 0.5 * 10 ** -(digits + 2) else x
    return f"{x:.{digits}%}"


def bin_label(b: dict) -> str:
    if b["lo"] == b["hi"]:
        return str(b["lo"])
    return f"{b['lo']}-{b['hi']}" if b["hi"] < 999 else f">= {b['lo']}"


def policy_line(r: dict) -> str:
    vci = f" [{pc(r['value_ci'][0])}, {pc(r['value_ci'][1])}]" if r.get("value_ci") else ""
    cci = f" [{r['cost_ci'][0]:.2f}, {r['cost_ci'][1]:.2f}]" if r.get("cost_ci") else ""
    if r.get("value_M1_range"):
        vci = f" [{pc(r['value_M1_range'][0])}, {pc(r['value_M1_range'][1])}]"
        cci = f" [{r['cost_range'][0]:.2f}, {r['cost_range'][1]:.2f}]"
    share = f"; passes in {pc(r['pass_share'])}" if r.get("pass_share") is not None else ""
    return (f"| {r['policy']} | {r['tag']} | {pc(r['refresh_share'])} | {pc(r['value_M1'])}{vci} | "
            f"{pc(r.get('value_measured'))} | {pc(r.get('value_iso'))} | {pc(r.get('value_M2'))} | "
            f"{pc(r.get('value_exact'))} | {pc(r['value_lb'])} | {r['rt_per_call']:,.0f} | {r['cost']:.2f}{cci} | "
            f"{d2_verdict(r['value_M1'], r['cost'])}{share} |")


def report_lines(res: dict, rep: dict, align: dict, args) -> list[str]:
    tok, dist, pred, age, pol, where = (res[k] for k in ("tokens", "dist", "predictor", "age", "policies", "where"))
    am = age["model"]
    best = pol["best"]
    n = tok["calls"]
    lines = [
        "## 0. Input and reproduction", "",
        f"Sources: {', '.join(f'live/{a}:{b}' for a, b in SOURCES)}, as `epic_replay.py report` merged them for 16's "
        f"`data/replay_tables.md` (`live/pilot/replay_test` is excluded). {rep['counts']['runs']} runs, "
        f"{rep['counts']['calls']:,} calls, {rep['counts']['edited']:,} edited (every call but a run's first). Pooled "
        "mean reply KL over the edited calls against 16's §4.5 table:", "",
        "| arm | 16 §4.5 | reproduced |", "|---|---|---|",
        *[f"| {a} | {HEADLINE[a]:.4f} | {rep['means'][a]:.5f} |" for a in HEADLINE], "",
        f"Reproduced within rounding: **{'yes' if rep['ok'] else 'NO'}**. The read-aware proxy reads the replayed "
        "runs' captured requests: replay reply tokens = recorded output tokens - 1 (the final `<|im_end|>`) within 2 "
        "tokens "
        f"on {align.get('aligned', 0):,} of {align.get('aligned', 0) + align.get('off', 0):,} calls.", "",
        f"**Token accounting** (edited calls). The `prefix` serving computes P - E = {tok['prefix']:,.0f} tokens "
        f"({tok['prefix'] / n:,.0f} per call; E = the plan's exact prefix, the same in every arm; 16's "
        "`epic_latency.py jct` prices these calls' prefix serving with the same P - E). EPIC-128 computes "
        f"{tok['epic_share']:.1%} of that ({tok['epic128'] / n:,.0f} per call), so refreshes may add "
        f"{BUDGET - tok['epic_share']:.1%} ({tok['budget']:,.0f} tokens, {tok['budget'] / n:,.0f} per call) before a "
        f"policy crosses 1/3. EPIC-128's reused rows S total {tok['S']:,.0f}: the budget re-prefills about "
        f"{tok['budget'] / tok['S']:.0%} of them.", "",
        "## 1. Refresh value distribution", "",
        "v = reply KL(compounded) - reply KL(one-step) per edited call (nats per reply token). Concentration = share "
        "of "
        "the total v on the calls with the largest v (above 100 %: the rest sums below 0).", "",
        "| pair | calls | total v | mean v | median v | v <= 0 | top 10 % of calls | top 20 % | top 50 % | top 3 of 30 "
        "runs | edit calls | append-only after an edit | before the first edit |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        *[f"| {comp} - {PAIRS[comp]} | {d['calls']} | {d['total']:.2f} | {d['mean']:.4f} | {d['median']:.5f} | "
          f"{pc(d['le0'], 1)} | {pc(d['top10'])} | {pc(d['top20'])} | {pc(d['top50'])} | {pc(d['top3_runs'])} | "
          f"{pc(d['edit'])} ({d['n_edit']} calls) | {pc(d['after'])} ({d['n_after']}) | {pc(d['before'], 1)} "
          f"({d['n_before']}) |" for comp, d in dist.items()], "",
        "Where the EPIC-128 value sits (reply tool and re-reads from the recorded requests; read-only data):", "",
        "| group | calls | share of calls | mean v | share of the total v |", "|---|---|---|---|---|",
        *[f"| {g['group']} | {g['calls']} | {pc(g['share_calls'])} | {g['mean_v']:.4f} | {pc(g['share_v'])} |"
          for g in res["groups"]], "",
        "## 2. Predictors of v", "",
        "Rank correlation (Spearman, average ranks for ties) over the 2,260 edited calls. 'Known before the call': "
        "available to the server when the call arrives (plan stats and row ages from its own bookkeeping, the previous "
        "reply from the session); the others need the reply or a fresh recompute.", "",
        "| feature | known before the call | Spearman with v (epic128) | with v (shift) |", "|---|---|---|---|",
        *[f"| {s['feature']} | {'yes' if s['ex_ante'] else 'no'} | {s['epic128']:+.2f} | {s['shift']:+.2f} |"
          for s in sorted(res["spearman"], key=lambda s: (not s["ex_ante"], -abs(s["epic128"])
                                                         if s["epic128"] == s["epic128"] else 0.0))], "",
        f"**Predictor.** Ridge regression of asinh(v / s) (s = median |v| of the training calls) on "
        f"{pred['n_features']} ex-ante features (log plan stats, ages, the stale-row age histogram, run history, the "
        "previous reply, task type); lambda from an inner 3-fold split by run. Held out by run: every split trains on "
        f"20 runs (2/3) and tests on 10 (1/3), 3 folds, {args.reps} random splits. Held-out Spearman with v (median "
        f"[5 %, 95 %] over splits): pooled out-of-fold {pred['rho'][0]:.2f} [{pred['rho'][1]:.2f}, "
        f"{pred['rho'][2]:.2f}], per test fold {pred['fold_rho'][0]:.2f} [{pred['fold_rho'][1]:.2f}, "
        f"{pred['fold_rho'][2]:.2f}]; with the previous call's v added (needs a fresh probe of every call) "
        f"{pred['rho_probe'][0]:.2f}. Lambdas chosen: {pred['lambdas']}.", "",
        "Share of the total EPIC-128 refresh value on the calls ranked highest (own value only, no downstream):", "",
        "| refresh the top q of calls by ... | " + " | ".join(f"q = {q:.0%}" for q in QS) + " |",
        "|---|" + "---|" * len(QS),
        "| ridge, ex-ante features (held-out runs; median [5 %, 95 %] over splits) | "
        + " | ".join(f"{pc(pred['captured'][q][0])} [{pc(pred['captured'][q][1])}, {pc(pred['captured'][q][2])}]"
                     for q in QS) + " |",
        "| ridge + the previous call's v (a probe of every call) | "
        + " | ".join(pc(pred["captured_probe"][q][0]) for q in QS) + " |",
        "| oracle (the true v) | " + " | ".join(pc(pred["captured_oracle"][q]) for q in QS) + " |",
        "| random | " + " | ".join(pc(q) for q in QS) + " |", "",
        "## 3. Policies (D2)", "",
        "**Age model (model-based; README §2).** s(a) = sum KL(epic128) / sum KL(epic128_1) over the edit calls with "
        f"age_max_over = a. M1: 1 + A (1 - exp(-(a - 1) / tau)), A = {am['A']:.2f} [{age['A_ci'][1]:.2f}, "
        f"{age['A_ci'][2]:.2f}], tau = {am['tau']:.2f} [{age['tau_ci'][1]:.2f}, {age['tau_ci'][2]:.2f}] (95 % over the "
        f"run bootstrap; shift / shift1: A = {age['shift']['A']:.2f}, tau = {age['shift']['tau']:.2f}); s(1) = 1 "
        "anchors the one-step arm. Isotonic: the bin ratios made non-decreasing (pool-adjacent-violators).", "",
        "| age_max_over (edit calls) | calls | ratio [95 % CI, runs] | M1 fit | isotonic | shift / shift1 |",
        "|---|---|---|---|---|---|",
        *[f"| {bin_label(b)} | {b['n']} | {b['ratio']:.2f} [{b['lo95']:.2f}, {b['hi95']:.2f}] | {b['fit']:.2f} | "
          f"{b['iso']:.2f} | {b['shift_ratio']:.2f} |" for b in am["bins"]], "",
        "Held-out check (fit on 2/3 of the runs, predict the rest's pooled KL per age bin from their one-step KL; mean "
        "|log(actual / predicted)|, median [5 %, 95 %] over 30 folds): "
        + "; ".join(f"{name} {age['check'][k][0]:.3f} [{age['check'][k][1]:.3f}, {age['check'][k][2]:.3f}]"
                    for k, name in (("M1", "M1"), ("iso", "isotonic"),
                                    ("step", "step (all compounding at the 2nd edit)"),
                                    ("flat", "no compounding"))) + ".", "",
        "**Policies.** Value = the share of the total EPIC-128 refresh value (sum of v over the edited calls, "
        f"{dist[SERVING]['total']:.2f}) a policy removes. A refreshed call is served as a full re-prefill from its "
        "first "
        "non-fresh row: refresh tokens = EPIC-128's reused rows S at an edit call (everything else behind the first "
        "reused row is computed by EPIC anyway), the whole stale span since the last edit at an append-only call. The "
        "refreshed call is credited its refresh value v (the pre-registered value of a call is capped at v, and "
        "refreshing every call is 100 %); 'exact credit' counts its whole KL instead (a re-prefill leaves KL ~ 0), "
        "i.e. also the one-step residual, which is not refresh value (it can exceed 100 %). After a refresh no reused "
        "row is older than the edits since: the first edit after "
        "it is the measured one-step arm and the append-only calls before that are fresh (measured); later calls use "
        "the age model: M1 (main), 'measured only' (no model: those calls get nothing), isotonic, M2 (KV load). Cost = "
        "(EPIC-128's computed tokens + refresh tokens) / (P - E). Intervals: (a), (b) run bootstrap with the age model "
        f"refit per replicate ({args.boot} replicates); (c), (c') [5 %, 95 %] over the {args.reps} held-out prediction "
        "splits (median shown); 'passes in' = the share of replicates (splits) in which the policy passes D2. Counting "
        "reuse_avail instead of S would re-prefill the exact head as well (never stale here), i.e. a full recompute "
        "per "
        "refresh: every cost only rises. (d): the replay records carry no read proxy (their only text is the greedy "
        "next round of every 5th edited call; reply_reused counts reused rows that came from the previous reply, not "
        "reads of moved content), so (d) uses the replayed runs' captured requests: a reply re-reads a moved file when "
        "it calls read_file on a path whose earlier read_file call sits in a message the edit moved. Tag: "
        "'pre-registered' = README §2's candidates; 'post hoc' = added here; 'ex post' "
        "uses the reply and cannot be implemented; the oracle ranks by the true v. D2 is judged on M1.", "",
        "| policy | tag | calls refreshed | value, M1 [95 % CI] | measured only | isotonic | M2 | exact credit | own "
        "value "
        "only | refresh tokens per call | cost vs prefix [CI] | D2 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        *[policy_line(r) for r in pol["fixed"] if not r["policy"].startswith("(d)")],
        *[policy_line(r) for r in pol["c"] if 0.01 <= r["refresh_share"] <= 0.6],
        *[policy_line(r) for r in pol["cp"] if 0.01 <= r["refresh_share"] <= 0.6][::2],
        *[policy_line(r) for r in pol["fixed"] if r["policy"].startswith("(d)")],
        *[policy_line(r) for r in pol["oracle"] if 0.01 <= r["refresh_share"] <= 0.6][::2], "",
    ]
    bb, bpre, bmeas, bd, bor, borl, bcp = (best[k] for k in ("(b)", "(b) pre-registered N", "(b) measured only",
                                                              "(d) ex ante", "oracle", "oracle own value only", "(c')"))
    parts = [f"(b) {bb['policy'][4:]} ({bb['tag']}): {pc(bb['value_M1'])} at {bb['cost']:.2f}" if bb else "(b) none",
             f"pre-registered N: {bpre['policy'][4:]} {pc(bpre['value_M1'])}" if bpre else "pre-registered N: none",
             f"(b) measured only: {pc(bmeas['value_measured'])}" if bmeas else "(b) measured only: none",
             f"(c) {pc(best['(c)'][0])} [{pc(best['(c)'][1])}, {pc(best['(c)'][2])}] (own value only "
             f"{pc(best['(c) own value only'][0])})",
             f"(c') {pc(bcp[0])} [{pc(bcp[1])}, {pc(bcp[2])}]",
             f"(d) ex ante: {bd['policy'][13:]} {pc(bd['value_M1'])}" if bd else "(d) ex ante: none",
             f"oracle {pc(bor['value_M1'])} at {bor['cost']:.2f} (own value only {pc(borl['value_lb'])})"
             if bor and borl else "oracle: none"]
    lines += ["Best value at cost <= 1/3, per family (M1; for (c), (c') the best threshold of each split, chosen on "
              "the same calls, so optimistic): " + "; ".join(parts) + ".", ""]
    lines += [f"**D2 verdict: {'PASS' if pol['passing']['M1'] else 'FAIL'}.** " + verdict_text(res), ""]
    lines += ["## 4. Where the deviation sits (what a partial refresh should target)", "", *res["where_lines"], "",
              "For the design:",
              f"- **Age is the lever.** Per-row K deviation grows with the edits a row survived (EPIC-128: "
              f"{where['d_by_age'][1]:.3f} after one edit, {where['d_by_age'][3]:.3f} after 4-7, "
              f"{where['d_by_age'][4]:.3f} after 8-15, {where['d_by_age'][5]:.3f} after 16+; one-step "
              f"{where['d_one']:.3f}); rows that survived >= 8 edits are {pc(where['old_rows'])} of the stale rows but "
              f"carry {pc(where['old_excess'])} of their excess. A partial refresh should recompute the oldest "
              "surviving "
              "rows: in s15's chronological history they open the moved tail, right behind the head, so a prefix-order "
              "refresh of the tail's first part reaches them first (structure of the snip, not measured here).",
              f"- **Offset is not.** Rows within 512 tokens of a run head deviate more ({where['off_near_d']:.3f} vs "
              f"{where['off_far_d']:.3f} beyond, behind EPIC's 128-token head), but {pc(where['off_far_excess'])} of "
              "the "
              "reused rows' excess lies >= 512 tokens from any head: a longer head (larger k) cannot reach it.",
              f"- **Layers 40-63 carry {pc(where['deep_excess'])} of the excess** (layers 0-31: "
              f"{pc(where['shallow_excess'])}). A refresh still recomputes every layer of a row (a deep layer's KV "
              "depends on the same row's shallow layers), so depth does not cut its cost; a drift probe can read the "
              "deep layers only."]
    return lines


def verdict_text(res: dict) -> str:
    pol = res["policies"]
    fixed = {r["policy"]: r for r in pol["fixed"]}
    best, close = pol["best"], pol["closest"]
    b8, b4 = fixed["(b) every 8 edits"], fixed["(b) every 4 edits"]
    a = fixed["(a) refresh before every call"]
    if pol["passing"]["M1"]:
        out = f"Passing (M1): {', '.join(pol['passing']['M1'])}."
        for m, name in (("measured", "measured only"), ("iso", "isotonic"), ("M2", "M2")):
            out += f" Under {name}: {', '.join(pol['passing'][m]) or 'none'}."
        return out
    out = ("No implementable policy recovers >= 50 % of the refresh value at <= 1/3 of the re-prefill tokens. "
           f"Refreshing before every call is the re-prefill itself (cost {a['cost']:.2f}). Of the pre-registered "
           f"candidates, every 2 and every 4 edits recover enough ({pc(fixed['(b) every 2 edits']['value_M1'])}, "
           f"{pc(b4['value_M1'])}) but cost {fixed['(b) every 2 edits']['cost']:.2f} and {b4['cost']:.2f}; every 8 "
           f"edits fits the budget ({b8['cost']:.2f} [{b8['cost_ci'][0]:.2f}, {b8['cost_ci'][1]:.2f}]) but recovers "
           f"{pc(b8['value_M1'])} [{pc(b8['value_ci'][0])}, {pc(b8['value_ci'][1])}] (passes in "
           f"{pc(b8['pass_share'])} of the run-bootstrap replicates; measured part "
           f"{pc(b8['value_measured'])}, isotonic {pc(b8['value_iso'])}, M2 {pc(b8['value_M2'])}); the predictor's "
           f"best threshold under the budget recovers {pc(best['(c)'][0])} (own value only "
           f"{pc(best['(c) own value only'][0])}).")
    bv, ch = close["best value under the budget"], close["cheapest with >= 50 %"]
    if bv and ch:
        out += (f" The margin is thin only for post hoc schedules: {bv['policy'][4:]} recovers {pc(bv['value_M1'])} "
                f"[{pc(bv['value_ci'][0]) if bv.get('value_ci') else '—'}, "
                f"{pc(bv['value_ci'][1]) if bv.get('value_ci') else '—'}] at {bv['cost']:.2f}, and the cheapest policy "
                f"with >= 50 % ({ch['policy']}) costs {ch['cost']:.2f}; each passes in "
                f"{pc(bv.get('pass_share'))} / {pc(ch.get('pass_share'))} of the run-bootstrap replicates.")
    others = [f"{name}: {', '.join(pol['passing'][m])}" for m, name in
              (("measured", "measured only"), ("iso", "isotonic"), ("M2", "M2"), ("exact", "exact credit"))
              if pol["passing"][m]]
    out += (" Policies that would pass under another value model: " + "; ".join(others) + " (the isotonic curve "
            "fits held-out runs as well as M1; exact credit counts more than the refresh value)." if others else
            " No other value model (measured only, isotonic, M2, exact credit) makes a policy pass.")
    if best["oracle"] and best["oracle"]["value_M1"] >= VALUE_BAR:
        out += (f" An oracle that knew each call's v would pass ({pc(best['oracle']['value_M1'])} at "
                f"{best['oracle']['cost']:.2f}): the value is concentrated enough, but not predictable enough from "
                "what "
                "the server knows before the call.")
    return out


def write_outputs(res: dict, rep: dict, align: dict, out_dir: Path = OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = res["tabs"][SERVING]
    cv = res["predictor"]["cv"]
    sh_by = {(r["run"], r["call"]): r for r in res["tabs"]["shift"]}
    keep = ("run", "call", "src", "template", "regime", "P", "reply", "E", "S", "H", "C", "runs", "max_delta",
            "age_mean", "age_max", "reply_reused", "reuse_avail", "ped", "n_edit", "since", "a", "anchor", "n0", "n1",
            "stale", "dk_reused", *PROXY_KEYS)
    with (out_dir / "calls.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            s_r = sh_by[(r["run"], r["call"])]
            out = {k: r[k] for k in keep}
            out.update({"kl_epic128": r["kl_c"], "kl_epic128_1": r["kl_1"], "v_epic128": r["v"],
                        "kl_shift": s_r["kl_c"], "kl_shift1": s_r["kl_1"], "v_shift": s_r["v"],
                        "pred_v_epic128_split0": float(cv[0]["oof"][r["i"]]),
                        "pred_v_epic128_median": float(np.median([c["oof"][r["i"]] for c in cv]))})
            fh.write(json.dumps(out) + "\n")
    pol = res["policies"]
    with (out_dir / "policies.jsonl").open("w", encoding="utf-8") as fh:
        for r in pol["fixed"] + pol["c"] + pol["cp"] + pol["oracle"]:
            fh.write(json.dumps(r) + "\n")
    age = res["age"]
    pred = res["predictor"]
    summary = {"reproduction": rep, "proxy_alignment": align, "tokens": res["tokens"], "distribution": res["dist"],
               "groups": res["groups"], "spearman": res["spearman"],
               "predictor": {k: pred[k] for k in ("n_features", "rho", "fold_rho", "rho_probe", "lambdas")}
               | {k: {str(q): v for q, v in pred[k].items()}
                  for k in ("captured", "captured_probe", "captured_oracle")},
               "age_model": {"epic128": {k: age["model"][k] for k in ("A", "tau", "n", "plateau", "bins")},
                             "A_ci": age["A_ci"], "tau_ci": age["tau_ci"],
                             "shift": {k: age["shift"][k] for k in ("A", "tau", "n")}, "check": age["check"]},
               "best_at_budget": pol["best"], "closest": pol["closest"], "passing": pol["passing"],
               "d2": "PASS" if pol["passing"]["M1"] else "FAIL", "where": res["where"]}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=float) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=30, help="random 3-fold splits by run for the predictor")
    ap.add_argument("--boot", type=int, default=200, help="run-bootstrap replicates (age model refit in each)")
    ap.add_argument("--seed", default="e0e")
    args = ap.parse_args()
    recs = load_records()
    rep = reproduce(recs)
    print(f"reproduction: {rep['counts']}; " + ", ".join(f"{a} {m:.5f}" for a, m in rep["means"].items())
          + f" -> {'OK' if rep['ok'] else 'MISMATCH'}")
    if not rep["ok"]:
        raise SystemExit("16's E-A headline is not reproduced; stopping before any new analysis")
    proxy, align = read_proxy(recs)
    res = analyze(recs, proxy, args.reps, args.boot, args.seed)
    write_outputs(res, rep, align)
    note = ("Offline, CPU only, on topic 16's local E-A replay (README §3, part E0e; decision rule D2 in §2). "
            "Measured: the refresh value, its distribution and predictors, policy (a), and every call a replay arm "
            "measured (refreshed calls, the first edit after a refresh, the fresh append-only calls before it); "
            "model-based: the rest of the policy values (age model fitted on the same data). Regenerate with "
            "`/data/fritz/venv/bin/python research/17_harness_aware_serving/hs_drift.py`.")
    out = hc.write_table("E0e_drift", "E0e · Refresh value of reused KV and the refresh-policy check (D2)",
                         report_lines(res, rep, align, args), note)
    print(out)
    pol = res["policies"]
    print(f"D2 (M1): {'PASS' if pol['passing']['M1'] else 'FAIL'}; passing by model: "
          + "; ".join(f"{m} {len(v)}" for m, v in pol["passing"].items()))


if __name__ == "__main__":
    main()
