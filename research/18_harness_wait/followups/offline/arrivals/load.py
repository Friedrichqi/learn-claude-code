#!/usr/bin/env python3
"""Load per-call arrival/admission records + capture turn info for the 17 topic-18 cells into one pickle.

Per lead call (purpose 'ld' from the X-Request-Id), all calls (not only the window), with:
  cell, srv, r, wall, sess, c (chain idx), np, ideal, hit_now (arrival), hit_adm (first admission, None if never
  admitted), lcp, gap, prev_np (previous lead call of the same chain, by r), turn_id, call_index,
  turn_start (first lead call of its turn_id in the session), wait_only, in_win.
"""
import glob
import json
import lzma
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = "/data/fritz/learn-claude-code/research/18_harness_wait/data/cells"
CELLS = ["w08_none", "w08_m_stock", "w08_m_share", "w16_none", "w16_none_aa", "w16_m_stock", "w16_m_share",
         "w16_h_backoff", "w16_h_share", "x16_h_share_start", "w16_sgate", "w32_none", "w32_m_stock",
         "w32_m_share", "w32_h_backoff", "w32_h_share", "w32_sgate"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "followups", "calls.pkl")


def load_cell(cell):
    win = json.load(open(f"{ROOT}/{cell}/window.json"))
    ws, we = win["window_start"], win["window_end"]
    calls = []
    purposes = {}
    for sp in sorted(glob.glob(f"{ROOT}/{cell}/server/*/sched.jsonl.xz")):
        srv = os.path.basename(os.path.dirname(sp))
        arr, adm = {}, {}
        with lzma.open(sp, "rt") as f:
            for line in f:
                if '"k":"arr"' in line:
                    r = json.loads(line)
                    arr[r["r"]] = r
                elif '"k":"adm"' in line:
                    r = json.loads(line)
                    if not r.get("res") and r["r"] not in adm:
                        adm[r["r"]] = r
        for ri in sorted(arr):
            a = arr[ri]
            parts = a["id"].split("~")
            purpose = parts[3] if len(parts) == 5 else "un"
            purposes[purpose] = purposes.get(purpose, 0) + 1
            if purpose != "ld":
                continue
            d = adm.get(ri)
            calls.append({"cell": cell, "srv": srv, "r": ri, "id": a["id"], "wall": a["wall"],
                          "sess": f"{parts[0]}~{parts[1]}", "c": a["c"], "np": a["np"], "ideal": a["ideal"],
                          "hit_now": a["hit_now"], "hit_adm": None if d is None else d["hit"],
                          "qwait": None if d is None else d["wait"], "lcp": a["lcp"], "gap": a["gap"],
                          "in_win": ws <= a["wall"] <= we})
    # previous lead call of the same chain (server, chain idx), by arrival order
    last = {}
    for c in calls:
        k = (c["srv"], c["c"])
        c["prev_np"] = last.get(k)
        last[k] = c["np"]
    # capture: turn ids and wait-only replies
    cap = {}
    for p in glob.glob(f"{ROOT}/{cell}/runs/*/*.requests.jsonl.xz"):
        with lzma.open(p, "rt") as f:
            for line in f:
                x = json.loads(line)
                if x.get("purpose") != "lead":
                    continue
                resp = x.get("response") or {}
                uses = [b for b in (resp.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
                cap[x["request_id"]] = (x.get("turn_id"), x.get("call_index"),
                                        bool(uses) and all(u.get("name") == "wait" for u in uses),
                                        x.get("t_send"), x.get("t_recv"),
                                        ((resp.get("usage") or {}).get("cache_read_input_tokens")))
    first_of_turn = {}
    for rid, (tid, ci, *_rest) in cap.items():
        sess = "~".join(rid.split("~")[:2])
        k = (sess, tid)
        if ci is not None and (k not in first_of_turn or ci < first_of_turn[k][0]):
            first_of_turn[k] = (ci, rid)
    starts = {v[1] for v in first_of_turn.values()}
    joined = 0
    for c in calls:
        x = cap.get(c["id"])
        if x is None:
            c.update(turn_id=None, call_index=None, turn_start=None, wait_only=None, cache_read=None)
            continue
        joined += 1
        c.update(turn_id=x[0], call_index=x[1], turn_start=c["id"] in starts, wait_only=x[2], cache_read=x[5])
    return cell, calls, {"purposes": purposes, "joined": joined, "n_lead": len(calls),
                         "n_cap": len(cap), "ws": ws, "we": we}


def main():
    cells = sys.argv[1].split(",") if len(sys.argv) > 1 else CELLS
    out = {}
    with ProcessPoolExecutor(max_workers=len(cells)) as ex:
        for cell, calls, meta in ex.map(load_cell, cells):
            out[cell] = {"calls": calls, "meta": meta}
            print(cell, meta["n_lead"], "lead arr;", meta["joined"], "joined to capture of", meta["n_cap"],
                  "; purposes", meta["purposes"], flush=True)
    pickle.dump(out, open(OUT, "wb"))


if __name__ == "__main__":
    main()
