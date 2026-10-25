#!/usr/bin/env python3
"""Attribute each prefix break (from calls.pkl) to what changed in the request, by diffing consecutive lead
requests of a session in its capture. Cause = first differing message's new content kind."""
import glob, json, lzma, os, pickle, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
HERE = os.path.dirname(os.path.abspath(__file__))

def kind(text):
    if "[Earlier tool result saved at" in text: return "micro_compact placeholder"
    if "<persisted-output>" in text: return "persisted preview (fit/budget)"
    if re.search(r"messages archived at", text): return "snip archive"
    return None

def flat(m):
    c = m.get("content")
    return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)

def cell_causes(cell):
    out = {}
    for p in glob.glob(f"{ROOT}/{cell}/runs/*/*.requests.jsonl.xz"):
        recs = []
        with lzma.open(p, "rt") as f:
            for line in f:
                x = json.loads(line)
                if x.get("purpose") == "lead":
                    recs.append(x)
        recs.sort(key=lambda x: x["call_index"])
        for A, B in zip(recs, recs[1:]):
            if A["system"] != B["system"]:
                cause = "system prompt changed"
            elif A["tools"] != B["tools"]:
                cause = "tools changed"
            else:
                ma, mb = A["messages"], B["messages"]
                i = next((j for j in range(min(len(ma), len(mb))) if ma[j] != mb[j]), None)
                if i is None:
                    cause = "append-only" if len(mb) >= len(ma) else "messages dropped at end"
                else:
                    tb = flat(mb[i])
                    k = kind(tb)
                    if k:
                        cause = k
                    elif len(mb) < len(ma) - 2:
                        cause = "history shrank (compact/summary)"
                    else:
                        cause = f"other: msg {mb[i].get('role')}"
            out[B["request_id"]] = cause
    return cell, out

if __name__ == "__main__":
    D = pickle.load(open(os.path.join(HERE, "..", "..", "..", "data", "followups", "calls.pkl"), "rb"))
    cells = list(D)
    with ProcessPoolExecutor(max_workers=len(cells)) as ex:
        res = dict(ex.map(cell_causes, cells))
    pickle.dump(res, open(os.path.join(HERE, "..", "..", "..", "data", "followups", "causes.pkl"), "wb"))
    print("| cell | breaks | micro_compact placeholder | persisted preview | snip archive | history shrank | system/tools changed | other / unjoined | forced tok by placeholder (% of all forced) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for cell in cells:
        cnt, ftok = Counter(), Counter()
        for c in D[cell]["calls"]:
            if not c["in_win"] or c["prev_np"] is None or c["hit_adm"] is None:
                continue
            base = 16 * (c["prev_np"] // 16)
            if c["lcp"] >= base - 1024:
                continue
            cause = res[cell].get(c["id"], "unjoined")
            cnt[cause] += 1
            ftok[cause] += max(0, base - c["ideal"])
        n = sum(cnt.values())
        f = lambda k: f"{cnt[k] / max(1, n):.0%}"
        sysk = cnt["system prompt changed"] + cnt["tools changed"]
        oth = n - cnt["micro_compact placeholder"] - cnt["persisted preview (fit/budget)"] - cnt["snip archive"] - cnt["history shrank (compact/summary)"] - sysk
        print(f"| {cell} | {n} | {f('micro_compact placeholder')} | {f('persisted preview (fit/budget)')} | {f('snip archive')} | {f('history shrank (compact/summary)')} | {sysk / max(1, n):.0%} | {oth / max(1, n):.0%} | {ftok['micro_compact placeholder'] / max(1, sum(ftok.values())):.0%} |")
    # what the 'other' causes are, pooled
    oth = Counter()
    for cell in cells:
        for c in D[cell]["calls"]:
            if c["in_win"] and c["prev_np"] is not None and c["hit_adm"] is not None and c["lcp"] < 16 * (c["prev_np"] // 16) - 1024:
                cs = res[cell].get(c["id"], "unjoined")
                if cs.startswith("other") or cs in ("append-only", "messages dropped at end", "unjoined"):
                    oth[cs] += 1
    print("\nother/unjoined pooled:", dict(oth.most_common(8)))
