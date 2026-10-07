#!/usr/bin/env python3
"""Trace-driven KV pool simulation: LRU vs dead-first eviction (offline) -- research/15_agent_serving_synthesis.

Replays the arrival / admission / finish records of an MUScheduler log (research/11_multiuser_kv) through a
vLLM-like block pool and compares two eviction policies on the same trace:

  lru         vLLM's free queue: a finished request's blocks go to the tail in reverse order (its tail block
              is evicted first), and allocation evicts from the head.
  dead_first  the same, but when a chain's next request arrives, every block of the chain's previous version
              past the new prompt's common prefix (lcp) and the previous reply's blocks -- blocks no request of
              that chain can hit again -- move to the head of the queue (edit-aware garbage collection, I15).

Blocks are modelled per chain: a new prompt reuses the previous version's first lcp/16 block identities and
creates new identities after them; a chain's first prompt starts with `ideal`/16 shared "root" blocks (the
system prompt and tools every chain has). Output blocks are allocated at finish. The trace keeps the recorded
timing (no feedback: fewer misses would also make calls shorter), so the result is a first-order estimate,
like 13's S0 ceilings; the LRU arm is checked against the recompute the log actually recorded.

    python research/15_agent_serving_synthesis/pool_sim.py [--cells n08,s3gate16,...] [--root DIR]
"""
from __future__ import annotations

import argparse
import json
import lzma
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: E402,F401

REPO = Path(__file__).resolve().parents[2]
ROOT11 = REPO / "research/11_multiuser_kv/data/multiuser_live/cells"
HERE = Path(__file__).resolve().parent


def load(path: Path):
    hdr, ev = {}, []
    with lzma.open(path, "rt") as f:
        for line in f:
            r = json.loads(line)
            k = r.get("k")
            if k == "hdr":
                hdr = r
            elif k == "arr":
                ev.append((r["t"], 0, "arr", r))
            elif k == "adm" and not r.get("res"):
                ev.append((r["t"], 1, "adm", r))
            elif k == "fin":
                ev.append((r["t"], 2, "fin", r))
    ev.sort(key=lambda e: (e[0], e[1]))
    return hdr, ev


def simulate(hdr: dict, events: list, policy: str, cap_frac: float = 1.0, pin_arrival: bool = False) -> dict:
    bs = hdr.get("bs", 16)
    cap = int(hdr.get("nblk", 16137) * cap_frac)
    free: OrderedDict = OrderedDict()        # evictable cached blocks, head = next victim
    ref: dict = {}                           # block -> refcount (blocks in the pool)
    used = 0
    chain_cur: dict = {}                     # chain -> prompt block keys of its latest version
    chain_out: dict = {}                     # chain -> output block keys of its latest finished request
    chain_ver: dict = {}
    req: dict = {}                           # request idx -> (chain, keys, ideal_blocks, np)
    pins: dict = {}                          # request idx -> blocks pinned at arrival (I22)
    stats = {"calls": 0, "ideal": 0, "hit": 0, "computed": 0, "evict_rc": 0, "overflow": 0}

    def evict_one():
        nonlocal used
        if not free:
            return False
        key, _ = free.popitem(last=False)
        del ref[key]
        used -= 1
        return True

    def ensure(key):
        """Make `key` resident and referenced (allocate if absent)."""
        nonlocal used
        if key in ref:
            if ref[key] == 0:
                free.pop(key, None)
            ref[key] += 1
            return
        if used >= cap and not evict_one():
            stats["overflow"] += 1
            return
        ref[key] = 1
        used += 1

    def release(keys):
        for key in reversed(keys):           # the tail goes in first, so it is evicted first
            if key in ref and ref[key] > 0:
                ref[key] -= 1
                if ref[key] == 0:
                    free[key] = None

    for _t, _o, kind, r in events:
        if kind == "arr":
            c, np_, lcp = r["c"], r["np"], r.get("lcp", 0)
            nb, lb = np_ // bs, lcp // bs
            v = chain_ver.get(c, -1) + 1
            chain_ver[c] = v
            prev = chain_cur.get(c)
            if prev is None:
                root = min(nb, r.get("ideal", 0) // bs)
                keys = [("root", i) for i in range(root)] + [(c, v, i) for i in range(root, nb)]
                ideal = root
            else:
                lb = min(lb, len(prev), nb)
                keys = prev[:lb] + [(c, v, i) for i in range(lb, nb)]
                ideal = lb
                if policy == "dead_first":
                    for key in prev[lb:] + chain_out.get(c, []):
                        if key in free:
                            free.move_to_end(key, last=False)
            chain_cur[c] = keys
            req[r["r"]] = (c, keys, ideal, np_)
            if pin_arrival:                      # I22: hold the cached leading run until admission
                pinned = []
                for key in keys:
                    if key not in ref:
                        break
                    if ref[key] == 0:
                        free.pop(key, None)
                    ref[key] += 1
                    pinned.append(key)
                pins[r["r"]] = pinned
        elif kind == "adm":
            info = req.get(r["r"])
            if info is None:
                continue
            c, keys, ideal, np_ = info
            hit = 0
            while hit < len(keys) and keys[hit] in ref:
                hit += 1
            for key in keys:
                ensure(key)
            if pin_arrival:
                release(pins.pop(r["r"], []))
            stats["calls"] += 1
            stats["ideal"] += ideal
            stats["hit"] += min(hit, len(keys))
            stats["computed"] += len(keys) - hit
            stats["evict_rc"] += max(0, ideal - hit)
        elif kind == "fin":
            info = req.pop(r["r"], None)
            if info is None:
                continue
            c, keys, _ideal, np_ = info
            n_out = (np_ + r.get("no", 0)) // bs - np_ // bs
            out = [(c, "out", r["r"], j) for j in range(n_out)]
            for key in out:
                ensure(key)
            release(keys + out)
            chain_out[c] = out
    s = stats
    s["hit_rate"] = 100.0 * s["hit"] / max(1, s["ideal"])
    s["evict_share"] = 100.0 * s["evict_rc"] / max(1, s["computed"])
    return s


def simulate_tiered(hdr: dict, events: list, dram_gib: float, dram_gc: bool) -> dict:
    """GPU pool (LRU, as above) plus a host-DRAM tier fed write-through, as vLLM's native CPU offload does.

    A newly computed block is also written to DRAM (MRU); DRAM evicts LRU. At admission the prompt's leading run is
    served from the GPU, then continued from DRAM (a load), and only the rest is recomputed. With dram_gc, a chain's
    dead blocks (its previous version past the new lcp, and its previous reply) are invalidated in DRAM when its next
    request arrives -- edit-aware garbage collection in the lower tier.
    """
    bs = hdr.get("bs", 16)
    cap = int(hdr.get("nblk", 16137))
    kv_per_block = bs * 256 * 1024                      # Qwen3-32B: 256 KiB per token
    dcap = int(dram_gib * 2**30 / kv_per_block)
    free: OrderedDict = OrderedDict()
    ref: dict = {}
    dram: OrderedDict = OrderedDict()
    used = 0
    chain_cur, chain_out, chain_ver, req = {}, {}, {}, {}
    st = {"ideal": 0, "gpu_hit": 0, "dram_hit": 0, "computed": 0, "evict_rc": 0, "loaded": 0}

    def dram_put(key):
        if dcap <= 0:
            return
        if key in dram:
            dram.move_to_end(key)
            return
        dram[key] = None
        if len(dram) > dcap:
            dram.popitem(last=False)

    def ensure(key, computed: bool):
        nonlocal used
        if key in ref:
            if ref[key] == 0:
                free.pop(key, None)
            ref[key] += 1
            return
        if used >= cap:
            if not free:
                return
            victim, _ = free.popitem(last=False)
            del ref[victim]
            used -= 1
        ref[key] = 1
        used += 1
        if computed:
            dram_put(key)

    def release(keys):
        for key in reversed(keys):
            if key in ref and ref[key] > 0:
                ref[key] -= 1
                if ref[key] == 0:
                    free[key] = None

    for _t, _o, kind, r in events:
        if kind == "arr":
            c, np_, lcp = r["c"], r["np"], r.get("lcp", 0)
            nb, lb = np_ // bs, lcp // bs
            v = chain_ver.get(c, -1) + 1
            chain_ver[c] = v
            prev = chain_cur.get(c)
            if prev is None:
                root = min(nb, r.get("ideal", 0) // bs)
                keys = [("root", i) for i in range(root)] + [(c, v, i) for i in range(root, nb)]
                ideal = root
            else:
                lb = min(lb, len(prev), nb)
                keys = prev[:lb] + [(c, v, i) for i in range(lb, nb)]
                ideal = lb
                if dram_gc:
                    for key in prev[lb:] + chain_out.get(c, []):
                        dram.pop(key, None)
            chain_cur[c] = keys
            req[r["r"]] = (c, keys, ideal, np_)
        elif kind == "adm":
            info = req.get(r["r"])
            if info is None:
                continue
            c, keys, ideal, np_ = info
            g = 0
            while g < len(keys) and keys[g] in ref:
                g += 1
            d = g
            while d < len(keys) and keys[d] in dram:
                d += 1
            for i, key in enumerate(keys):
                if i < g:
                    ensure(key, False)
                elif i < d:
                    dram.move_to_end(key)
                    ensure(key, False)                   # loaded back from DRAM
                else:
                    ensure(key, True)                    # recomputed (and written through)
            st["ideal"] += ideal
            st["gpu_hit"] += g
            st["dram_hit"] += d - g
            st["computed"] += len(keys) - d
            st["evict_rc"] += max(0, ideal - d)
        elif kind == "fin":
            info = req.pop(r["r"], None)
            if info is None:
                continue
            c, keys, _ideal, np_ = info
            n_out = (np_ + r.get("no", 0)) // bs - np_ // bs
            out = [(c, "out", r["r"], j) for j in range(n_out)]
            for key in out:
                ensure(key, True)
            release(keys + out)
            chain_out[c] = out
    st["dram_cap_blocks"] = dcap
    st["hit_rate"] = 100.0 * (st["gpu_hit"] + st["dram_hit"]) / max(1, st["ideal"])
    st["evict_share"] = 100.0 * st["evict_rc"] / max(1, st["computed"])
    return st


def actual(events: list, bs: int) -> dict:
    """What the log recorded: eviction recompute = arrival ideal - admission hit, over computed tokens."""
    arr = {r["r"]: r for _t, _o, k, r in events if k == "arr"}
    ideal = hit = comp = 0
    for _t, _o, k, r in events:
        if k == "adm":
            a = arr.get(r["r"])
            if a is None:
                continue
            ideal += a.get("ideal", 0)
            hit += r.get("hit", 0)
            comp += max(0, a["np"] - r.get("hit", 0))
    return {"hit_rate": 100.0 * hit / max(1, ideal), "evict_share": 100.0 * max(0, ideal - hit) / max(1, comp)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cells", default="n04,n08,n08mem,s3gate16,s3gate32,n12,n08kvh")
    ap.add_argument("--root", default=str(ROOT11))
    ap.add_argument("--caps", default="1.0", help="pool fractions to simulate, e.g. 1.0,0.75")
    ap.add_argument("--out", default=str(HERE / "data/tables/pool_sim.md"))
    ap.add_argument("--dram", default="", help="DRAM tier sizes in GiB, e.g. 0,128,256: run the tiered arms instead")
    args = ap.parse_args()
    if args.dram:
        sizes = [float(x) for x in args.dram.split(",")]
        lines = ["# Trace-driven simulation with a write-through DRAM tier (11's logs, offline)", "",
                 "Generated by `research/15_agent_serving_synthesis/pool_sim.py --dram`; see `simulate_tiered`. "
                 "Recorded timing, no feedback; Qwen3-32B KV at 256 KiB per token (4 MiB per 16-token block).", "",
                 "| cell | DRAM tier | DRAM GC | prompt blocks served (GPU + DRAM), % of ideal | from DRAM, % of ideal | "
                 "eviction recompute, % of computed | computed blocks vs GPU-only |",
                 "|---|---:|---|---:|---:|---:|---:|"]
        for cell in [c for c in args.cells.split(",") if c]:
            for p in sorted((Path(args.root) / cell / "server").glob("*/sched.jsonl.xz")):
                hdr, events = load(p)
                base = None
                for gib in sizes:
                    for gc in ([False] if gib == 0 else [False, True]):
                        s = simulate_tiered(hdr, events, gib, gc)
                        base = s["computed"] if base is None else base
                        row = (f"| {cell} | {gib:.0f} GiB | {'yes' if gc else 'no'} | {s['hit_rate']:.1f} | "
                               f"{100.0 * s['dram_hit'] / max(1, s['ideal']):.1f} | {s['evict_share']:.1f} | "
                               f"{s['computed'] / max(1, base):.2f}× |")
                        print(row, file=sys.stderr)
                        lines.append(row)
        lines += ["", "*Key.* **computed blocks vs GPU-only**: prefill work relative to the same trace without a DRAM tier "
                  "(lower is better). **DRAM GC**: dead versions invalidated in DRAM when the owner's next request "
                  "arrives. The trace is the live run's; a real tier would also shorten calls and change the arrivals.", ""]
        out = Path(args.out).with_name("pool_sim_dram.md")
        out.write_text("\n".join(lines) + "\n")
        print(f"wrote {out}", file=sys.stderr)
        return
    lines = ["# Trace-driven pool simulation: LRU vs dead-first eviction (11's logs, offline)", "",
             "Generated by `research/15_agent_serving_synthesis/pool_sim.py`; definitions in its docstring. Shares of "
             "prompt blocks; recorded timing, no feedback.", "",
             "| cell | pool | logged: hit % / evicted % of computed | LRU sim: hit % / evicted % of computed | "
             "dead-first sim: hit % / evicted % of computed | dead-first removes, % of LRU's eviction recompute |",
             "|---|---:|---|---|---|---:|"]
    for cell in [c for c in args.cells.split(",") if c]:
        for p in sorted((Path(args.root) / cell / "server").glob("*/sched.jsonl.xz")):
            hdr, events = load(p)
            act = actual(events, hdr.get("bs", 16))
            for frac in [float(x) for x in args.caps.split(",")]:
                lru = simulate(hdr, events, "lru", frac)
                dfs = simulate(hdr, events, "dead_first", frac)
                gain = 100.0 * (lru["evict_rc"] - dfs["evict_rc"]) / max(1, lru["evict_rc"])
                row = (f"| {cell} | {frac:.2f} | {act['hit_rate']:.1f} / {act['evict_share']:.1f} | "
                       f"{lru['hit_rate']:.1f} / {lru['evict_share']:.1f} | {dfs['hit_rate']:.1f} / {dfs['evict_share']:.1f} | "
                       f"{gain:.0f} |")
                print(row, file=sys.stderr)
                lines.append(row)
    lines += ["", "*Key.* **hit %**: prompt blocks hit / the arrival's ideal blocks. **evicted %**: (ideal − hit) / computed "
              "blocks. **logged**: what the live run recorded (the LRU arm should land near it). **pool 0.75**: the same "
              "trace on three quarters of the blocks, a stand-in for higher load without changing the trace.", ""]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
