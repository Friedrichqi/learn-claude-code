"""Script transforms: 'appendonly' makes every call reuse the whole previous context (micro_compact's in-place
rewrites removed; a bound on fixing them)."""
import copy, pickle
s = pickle.load(open("scripts.pkl", "rb"))
a = copy.deepcopy(s)
n = 0
for sc in a:
    prev = None
    for c in sc["calls"]:
        if prev is not None and not c["start"] or (prev is not None and c["start"]):
            want = min(c["np"], prev["np"] + prev["out"])
            if c["ideal"] < want - 1024:
                c["ideal"] = want; n += 1
        prev = c
pickle.dump(a, open("scripts_appendonly.pkl", "wb"))
print("calls changed:", n, "of", sum(len(x["calls"]) for x in a))
