"""Session scripts for the simulator: every lead call of every session in the given cells, in order, with the
client delay before it (arr.gap), prompt tokens, reusable prefix (arr.ideal), output tokens (fin.no) and
whether it opens a turn (capture turn_id changes)."""
import glob, json, lzma, pickle, sys
ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
import os
# STATUSES=ok,budget keeps only sessions that ran to their own end (all 4 turns, or the call budget): fillers stopped
# at the warm-up's end and sessions cut by the window's end end early when replayed (complete.py's check); the
# default keeps every session, as the tables do
STATUSES = set(os.environ["STATUSES"].split(",")) if os.environ.get("STATUSES") else None   # None: every session
OUT = os.environ.get("OUT", "scripts.pkl")
scripts = []
for cell in sys.argv[1:]:
    arr, fin = {}, {}
    for p in sorted(glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz")):
        with lzma.open(p, "rt") as f:
            for line in f:
                if line.startswith('{"k":"arr"'):
                    r = json.loads(line); arr[r["id"]] = r
                elif line.startswith('{"k":"fin"'):
                    r = json.loads(line); fin[r["r"]] = r
    for run in sorted(glob.glob(f"{ROOT}/{cell}/runs/*/")):
        caps = glob.glob(run + "*.requests.jsonl.xz")
        metas = glob.glob(run + "*.meta.json")
        if not caps or (STATUSES is not None and (not metas or json.load(open(metas[0])).get("status") not in STATUSES)):
            continue
        calls, last_turn = [], None
        with lzma.open(caps[0], "rt") as f:
            for line in f:
                c = json.loads(line)
                if c.get("purpose") != "lead":
                    continue
                a = arr.get(c["request_id"])
                if a is None:
                    continue
                fr = fin.get(a["r"])
                out = (fr or {}).get("no") or 0
                ts = c.get("turn_id") != last_turn
                last_turn = c.get("turn_id")
                calls.append({"delay": a.get("gap") or 0.0, "np": a["np"], "ideal": a.get("ideal") or 0,
                              "out": max(1, out), "start": ts, "sid": c["request_id"].split("~")[0]})
        if calls:
            scripts.append({"cell": cell, "label": run.rstrip("/").split("/")[-1], "calls": calls})
pickle.dump(scripts, open(OUT, "wb"))
n = sum(len(s["calls"]) for s in scripts)
import statistics as st
d_mid = [c["delay"] for s in scripts for c in s["calls"][1:] if not c["start"]]
d_start = [c["delay"] for s in scripts for c in s["calls"][1:] if c["start"]]
print(len(scripts), "scripts,", n, "calls; np median", st.median(c["np"] for s in scripts for c in s["calls"]),
      "; out median", st.median(c["out"] for s in scripts for c in s["calls"]),
      "; mid delay p50/p90", round(st.median(d_mid), 2), round(sorted(d_mid)[int(.9 * len(d_mid))], 2),
      "; start delay p50", round(st.median(d_start), 1), "n starts", len(d_start))
