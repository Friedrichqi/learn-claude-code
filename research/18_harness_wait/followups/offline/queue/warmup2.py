"""Per 2-min bin from server start: lead calls/h, admission hit / prompt, arrival hit / ideal, mean queue wait,
distinct sessions calling. Admission joined to arrival by the request index r."""
import json, lzma, sys, glob
from collections import defaultdict
ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
BIN = float(sys.argv[1]); cells = sys.argv[2:]
for cell in cells:
    win = json.load(open(f"{ROOT}/{cell}/window.json"))
    t0 = win["t_start"]
    arr = {}; adm = {}
    for p in sorted(glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz")):
        with lzma.open(p, "rt") as f:
            for line in f:
                if line.startswith('{"k":"arr"'):
                    r = json.loads(line)
                    if "~ld~" in (r.get("id") or ""):
                        arr[r["r"]] = r
                elif line.startswith('{"k":"adm"'):
                    r = json.loads(line); adm[r["r"]] = r
    bins = defaultdict(lambda: [0, 0, 0, 0, 0, 0.0, set()])   # calls, np, admhit, ideal, hitnow, wait, sess
    for ri, a in arr.items():
        d = adm.get(ri)
        if d is None:
            continue
        x = bins[int((a["wall"] - t0) // BIN)]
        x[0] += 1; x[1] += a["np"]; x[2] += d.get("hit") or 0; x[3] += a.get("ideal") or 0
        x[4] += a.get("hit_now") or 0; x[5] += d.get("wait") or 0.0; x[6].add(a["id"].split("~")[0])
    print(f"\n{cell} (window {(win['window_start']-t0)/60:.0f}-{(win['window_end']-t0)/60:.0f} min)")
    print("  min  calls/h  adm.hit/np  hit_now/ideal  wait(s)  sess")
    for b in sorted(bins):
        if b * BIN > win["window_end"] - t0:
            break
        x = bins[b]
        print(f"{b*BIN/60:5.0f} {x[0]*3600/BIN:8.0f} {x[2]/max(1,x[1]):10.2f} {x[4]/max(1,x[3]):13.2f} {x[5]/max(1,x[0]):8.1f} {len(x[6]):5d}")
