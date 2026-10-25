"""Topic 16 E-A replay (written 2026-10-21 for weekly_progress/102126): reply KL (and first-token KL) against the number of snips the session has gone through.

Same sources, call filter and KL definition as epic_replay.py cmd_report (replay_tables.md): edited calls of the
30 recorded prefix runs; KL = top-20 teacher-forced reply KL against a fresh recompute (nats/token). k = how many
edited calls the run has had up to and including this one (each snip moves the surviving tail once more).
95 % intervals resample runs (2,000 reps), as in the published tables."""
import json, random, statistics, sys
from collections import defaultdict
from pathlib import Path

DATA = Path("/data/fritz/learn-claude-code/research/16_epic_harness/data")
SRCS = ["live/main:replay", "live/main:replay_b", "live/screen:replay", "live/pilot:replay", "live/pilot:replay_b",
        "live/pilot:replay_c", "live/pilot:replay_d", "live/pilot:replay_e"]
recs = []
for src in SRCS:
    live, _, name = src.partition(":")
    for line in (DATA / live / name / "replay_calls.jsonl").read_text().splitlines():
        r = json.loads(line); r["src"] = live; recs.append(r)
runs = defaultdict(list)
for r in recs:
    runs[r["run"]].append(r)
for rs in runs.values():
    rs.sort(key=lambda r: r["call"])
    k = 0
    for r in rs:
        if r.get("edited"):
            k += 1
        r["_k"] = k
edited = [r for r in recs if r.get("edited")]
print(f"runs {len(runs)}, calls {len(recs)}, edited {len(edited)}")
ARMS = ["shift", "epic32", "epic64", "epic128", "shift1", "epic128_1"]
for arm in ARMS:
    v = [r["arms"][arm]["kl"] for r in edited if r["arms"].get(arm, {}).get("kl") is not None]
    print(f"check pooled reply KL {arm}: {statistics.mean(v):.4f} (n={len(v)})")
BINS = [(1, 1), (2, 3), (4, 7), (8, 15), (16, 31), (32, 63), (64, 10**9)]
def lab(lo, hi):
    return f"{lo}" if lo == hi else (f"{lo}+" if hi > 10**8 else f"{lo}-{hi}")
def boot(rs, arm, key, reps=2000, seed="kl-moves"):
    by = defaultdict(list)
    for r in rs:
        by[r["run"]].append(r)
    keys = sorted(by)
    def stat(sample):
        v = [r["arms"][arm][key] for r in sample if r["arms"].get(arm, {}).get(key) is not None]
        return statistics.mean(v) if v else float("nan")
    pt = stat(rs); rng = random.Random(seed); vals = []
    for _ in range(reps):
        s = [r for _ in keys for r in by[rng.choice(keys)]]
        x = stat(s)
        if x == x: vals.append(x)
    vals.sort()
    return pt, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]
out = ["# Topic 16 E-A replay: reply KL by the number of snips the session has gone through", "",
       f"Computed 2026-10-21 by kl_by_moves.py from the E-A replay records ({len(runs)} runs, {len(edited)} edited calls; "
       "same sources and KL as replay_tables.md). k = snips so far in the run, including this call's. 95 % intervals resample runs.", "",
       "| snips so far (k) | calls | runs | mean moves of shift's reused rows | " + " | ".join(f"{a} reply KL [95% CI]" for a in ARMS) + " |",
       "|---|---|---|---|" + "---|" * len(ARMS)]
for lo, hi in BINS:
    rs = [r for r in edited if lo <= r["_k"] <= hi]
    if not rs:
        continue
    cells = []
    for arm in ARMS:
        pt, l, h = boot(rs, arm, "kl")
        cells.append(f"{pt:.4f} [{l:.4f}, {h:.4f}]")
    ages = [r["arms"]["shift"]["stats"]["age_mean_over"] for r in rs if r["arms"]["shift"]["stats"]["S"] > 0]
    out.append(f"| {lab(lo, hi)} | {len(rs)} | {len({r['run'] for r in rs})} | {statistics.mean(ages):.1f} | " + " | ".join(cells) + " |")
# Spearman rank correlation between k and per-call reply KL, per arm
def spearman(x, y):
    def ranks(a):
        o = sorted(range(len(a)), key=lambda i: a[i]); r = [0.0] * len(a); i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and a[o[j + 1]] == a[o[i]]: j += 1
            for t in range(i, j + 1): r[o[t]] = (i + j) / 2 + 1
            i = j + 1
        return r
    rx, ry = ranks(x), ranks(y); mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den
out += ["", "| arm | Spearman rho (k, per-call reply KL) over edited calls |", "|---|---|"]
for arm in ARMS:
    pairs = [(r["_k"], r["arms"][arm]["kl"]) for r in edited if r["arms"].get(arm, {}).get("kl") is not None]
    out.append(f"| {arm} | {spearman([p[0] for p in pairs], [p[1] for p in pairs]):+.3f} |")
text = "\n".join(out) + "\n"
Path(sys.argv[1]).write_text(text)
print(text)
