"""Within the window: retained = adm.hit / arr.hit_now, by the request's queue wait (adm.wait), lead calls."""
import json, lzma, sys, glob
from collections import defaultdict
ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
EDGES = [0, 0.5, 1, 2, 4, 8, 16, 32, 1e9]
for cell in sys.argv[1:]:
    win = json.load(open(f"{ROOT}/{cell}/window.json"))
    arr, adm = {}, {}
    for p in sorted(glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz")):
        with lzma.open(p, "rt") as f:
            for line in f:
                if line.startswith('{"k":"arr"'):
                    r = json.loads(line)
                    if "~ld~" in (r.get("id") or "") and win["window_start"] <= r["wall"] <= win["window_end"]:
                        arr[r["r"]] = r
                elif line.startswith('{"k":"adm"'):
                    r = json.loads(line); adm[r["r"]] = r
    b = defaultdict(lambda: [0, 0, 0, 0])   # n, hit_now, adm.hit, np
    for ri, a in arr.items():
        d = adm.get(ri)
        if d is None or not a.get("hit_now"):
            continue
        w = d.get("wait") or 0.0
        k = next(i for i in range(len(EDGES) - 1) if EDGES[i] <= w < EDGES[i + 1])
        x = b[k]; x[0] += 1; x[1] += a["hit_now"]; x[2] += min(d.get("hit") or 0, a["hit_now"]); x[3] += a["np"]
    tot = sum(x[0] for x in b.values())
    print(f"\n{cell}: queue wait (s) | share of calls | kept = adm.hit / hit_now")
    for k in sorted(b):
        x = b[k]
        print(f"  {EDGES[k]:>5g}-{EDGES[k+1]:<5g} {100*x[0]/tot:5.1f}%  {x[2]/max(1,x[1]):.2f}")
