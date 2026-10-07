#!/usr/bin/env python3
"""Calibration and gates of research/14_pd_crossover (PLAN.md §7, stages S1 / S3). Microbenchmarks only calibrate.

    python research/14_pd_crossover/pd_calib.py prompts --out data/calib/prompts24.json [--n 24]
    python research/14_pd_crossover/pd_calib.py pd --instances SRV/instances.json --out OUT.json [--all]
        [--lengths 8192,24576] [--prompts data/calib/prompts24.json] [--correct-only --n-prompts 8]
    python research/14_pd_crossover/pd_calib.py hop --instances SRV/instances.json --out OUT.json
    python research/14_pd_crossover/pd_calib.py decide --calib data/calib/JOB
prompts  24 lead-call prompts of 12's p2a N=4 level (rendered with 10's renderer: exact prompt ids), spread evenly
         over 4k-31k tokens, for the correctness gate (CPU; no server)
pd       against the first prefill (P) and decode (D) instance of a running arm, all through /v1/completions with
         prompt ids (no chat template):
           transfer  random-token prompts of 1k-32k tokens (5 distinct each): P with max_tokens 1 and
                     do_remote_decode, then D with P's kv_transfer_params; D's NIXL counters before / after each pair
           delta     a 16,384-token prompt through P->D, then the same prompt + {16, 128, 1024, 4096} new tokens: how
                     many tokens D pulls the second time (G3: <= delta + 16)
           correct   each prompt greedy (64 tokens, top-5 logprobs) through P->D, then on D alone after a D prefix-cache
                     reset; token sequences compared (G2: first token 24/24 and all 64 tokens >= 22/24; or every
                     divergence a numeric near-tie, <= 0.5 nats, with the first token identical in >= 22/24)
           reset     prefix-cache reset on P and D after the traffic: seconds until success (G4: <= 90 s)
hop      router overhead: 50 short calls straight to the instance vs 50 through pd_router.py in pass mode
decide   gates G1-G4 from the calib JSONs and the sampler streams stored beside each run (srv_A / srv_B / srv_U):
         G1 NIXL bytes vs the P GPU's NVLink TX (ratio in [0.8, 1.25]) and PCIe bytes during the transfers
         (< 10 % of NIXL bytes). Picks visibility B if it passes G1 and its D-side time at 8k / 24k tokens is within
         10 % of A's, else A; else the explicit UCX transport list. Writes data/decisions.json and gates.json.
         Exit 0 = pass; 1 = pass with notes (G3 or G4); 2 = G2 failed (stop); 3 = G1 failed for A and B (try UCX);
         4 = G1 failed with UCX too (stop).
"""
from __future__ import annotations

import argparse
import json
import lzma
import random
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import pd_workloads as pw  # noqa: E402

DIR = Path(__file__).resolve().parent
P_PARAMS = {"do_remote_decode": True, "do_remote_prefill": False, "remote_engine_id": None, "remote_block_ids": None,
            "remote_host": None, "remote_port": None}
LENGTHS = (1024, 2048, 4096, 8192, 16384, 24576, 32768)
DELTAS = (16, 128, 1024, 4096)
VOCAB = (1000, 100000)


def post(base: str, path: str, body: dict | None = None, timeout: float = 900.0) -> dict:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(base + path, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def metrics(base: str) -> dict:
    import pd_scrape
    return pd_scrape.scrape(base) or {}


def ids_for(n: int, *seed) -> list[int]:
    rng = random.Random(":".join(map(str, seed)))
    return [rng.randrange(*VOCAB) for _ in range(n)]


def complete(base: str, prompt: list[int], max_tokens: int, kv: dict | None = None, logprobs: bool = False) -> tuple:
    body = {"model": pw.MODEL, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0.0, "ignore_eos": True}
    if kv is not None:
        body["kv_transfer_params"] = kv
    if logprobs:
        body["logprobs"] = 5
    t0 = time.monotonic()
    out = post(base, "/v1/completions", body)
    return out, time.monotonic() - t0


def delta(before: dict, after: dict, key: str) -> float:
    return (after.get(key) or 0.0) - (before.get(key) or 0.0)


EXT = "vllm:prompt_tokens_by_source_total{external_kv_transfer}"


def pd_pair(p: str, d: str, prompt: list[int], max_tokens: int = 1, logprobs: bool = False) -> dict:
    m0 = metrics(d)
    w0 = time.time()
    pout, p_s = complete(p, prompt, 1, dict(P_PARAMS))
    kv = pout.get("kv_transfer_params")
    dout, d_s = complete(d, prompt, max_tokens, kv, logprobs)
    w1 = time.time()
    m1 = metrics(d)
    rec = {"n": len(prompt), "p_s": round(p_s, 4), "d_s": round(d_s, 4), "w0": w0, "w1": w1, "kv": bool(kv),
           "nixl_bytes": delta(m0, m1, "vllm:nixl_bytes_transferred_sum"),
           "nixl_xfers": delta(m0, m1, "vllm:nixl_bytes_transferred_count"),
           "nixl_s": delta(m0, m1, "vllm:nixl_xfer_time_seconds_sum"),
           "ext_tokens": delta(m0, m1, EXT),
           "failed": delta(m0, m1, "vllm:nixl_num_failed_transfers_total"),
           "expired": delta(m0, m1, "vllm:nixl_num_kv_expired_reqs_total")}
    if logprobs:
        lp = (dout.get("choices") or [{}])[0].get("logprobs") or {}
        rec["tokens"], rec["top"] = lp.get("tokens"), lp.get("top_logprobs")
    return rec


def divergence(pd_tokens: list | None, local: dict) -> dict:
    """Where the P/D sequence first differs from the local one, and how close the local model was to choosing the
    P/D token there (local top-1 logprob minus the local logprob of the P/D token; None = not in the local top 5).
    A numeric near-tie (the last prompt token is computed in a 1-token step on the P/D path but inside a prefill
    chunk locally) gives a small margin; wrong KV gives tokens the local model would never choose."""
    toks, top, lps = local.get("tokens") or [], local.get("top_logprobs") or [], local.get("token_logprobs") or []
    if not pd_tokens or not toks:
        return {"k": None, "margin": None, "same": False}
    k = next((i for i, (a, b) in enumerate(zip(pd_tokens, toks)) if a != b), None)
    if k is None:
        return {"k": None, "margin": 0.0, "same": len(pd_tokens) == len(toks)}
    alt = (top[k] or {}).get(pd_tokens[k]) if k < len(top) else None
    margin = (lps[k] - alt) if (alt is not None and k < len(lps) and lps[k] is not None) else None
    return {"k": k, "margin": margin, "same": False}


def reset(base: str, tries: int = 45) -> float | None:
    t0 = time.monotonic()
    for _ in range(tries):
        try:
            out = post(base, "/reset_prefix_cache?reset_external=true", None, 30)
            if out.get("success") is True or "true" in str(out.get("raw", "")).lower():
                return time.monotonic() - t0
        except Exception:
            pass
        time.sleep(2)
    return None


def cmd_pd(a) -> int:
    insts = json.loads(Path(a.instances).read_text())
    p = next(i["base"] for i in insts if i["role"] == "P")
    d = next(i["base"] for i in insts if i["role"] == "D")
    out = {"instances": insts, "t0": time.time()}
    if not a.correct_only:
        lengths = LENGTHS if a.all else [int(x) for x in a.lengths.split(",")]
        complete(p, ids_for(512, "warm", "p"), 1)                         # first call pays the NIXL handshake
        pd_pair(p, d, ids_for(512, "warm", "pd"))
        out["transfer"] = []
        for n in lengths:
            for rep in range(5):
                out["transfer"].append(dict(pd_pair(p, d, ids_for(n, "xfer", n, rep, time.time())), rep=rep))
                time.sleep(1.0)
        if a.all:
            out["delta"] = []
            for k, dn in enumerate(DELTAS):
                base_ids = ids_for(16384, "delta", k, time.time())
                first = pd_pair(p, d, base_ids)
                second = pd_pair(p, d, base_ids + ids_for(dn, "delta-new", k))
                out["delta"].append({"delta": dn, "first_ext": first["ext_tokens"], "second_ext": second["ext_tokens"],
                                     "second_d_s": second["d_s"]})
                time.sleep(1.0)
    if a.prompts and Path(a.prompts).exists() and (a.all or a.correct_only):
        prompts = json.loads(Path(a.prompts).read_text())[: a.n_prompts]
        out["correct"] = []
        for pr in prompts:
            reset(p, 10)
            reset(d, 10)
            via = pd_pair(p, d, pr["ids"], 64, logprobs=True)
            reset(d, 10)
            local, _ = complete(d, pr["ids"], 64, None, logprobs=True)
            loc = (local.get("choices") or [{}])[0].get("logprobs") or {}
            loc_tokens = loc.get("tokens")
            pd_tokens = via.get("tokens")
            div = divergence(pd_tokens, loc)
            out["correct"].append({"id": pr["id"], "n": len(pr["ids"]), "ext_tokens": via["ext_tokens"],
                                   "first_same": bool(pd_tokens and loc_tokens and pd_tokens[0] == loc_tokens[0]),
                                   "all_same": bool(pd_tokens and pd_tokens == loc_tokens),
                                   "div_k": div["k"], "div_margin": div["margin"],
                                   "pd": pd_tokens[:8] if pd_tokens else None,
                                   "local": loc_tokens[:8] if loc_tokens else None})
    if not a.correct_only:
        out["reset"] = {"p_s": reset(p), "d_s": reset(d)}
    out["t1"] = time.time()
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in out.items() if k != "instances"}))
    return 0


def cmd_hop(a) -> int:
    insts = json.loads(Path(a.instances).read_text())
    base = insts[0]["base"]
    import pd_launch
    port = pd_launch.free_port()
    router = subprocess.Popen([sys.executable, str(DIR / "pd_router.py"), "--port", str(port), "--mode", "pass",
                               "--backends", base])
    rbase = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(rbase + "/health", timeout=5) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(1)
        prompt = ids_for(256, "hop")
        direct, routed = [], []
        complete(base, prompt, 8)
        for _ in range(50):
            direct.append(complete(base, prompt, 8)[1])
            routed.append(complete(rbase, prompt, 8)[1])
        out = {"direct_ms_p50": 1000 * statistics.median(direct), "routed_ms_p50": 1000 * statistics.median(routed),
               "overhead_ms_p50": 1000 * (statistics.median(routed) - statistics.median(direct)),
               "direct": direct, "routed": routed}
    finally:
        router.terminate()
        router.wait(timeout=30)
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if not isinstance(v, list)}))
    return 0


# -- decide -------------------------------------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict]:
    opener = lzma.open if path.suffix == ".xz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def link_check(calib: dict, srv: Path) -> dict:
    """G1 for one calibration run: NIXL bytes vs NVLink / PCIe bytes during the transfer phase."""
    tr = calib.get("transfer") or []
    if not tr:
        return {"ok": False, "why": "no transfers"}
    meta = json.loads((srv / "samp" / "meta.json").read_text())
    order = [g for g in str(meta["args"]["gpus"]).split(",")]
    insts = calib["instances"]
    pg = str(next(i for i in insts if i["role"] == "P")["gpus"][0])
    dg = str(next(i for i in insts if i["role"] == "D")["gpus"][0])
    ip, idd = order.index(pg), order.index(dg)
    w0, w1 = min(x["w0"] for x in tr) - 1.0, max(x["w1"] for x in tr) + 1.5
    slow = [r for r in read_jsonl(srv / "samp" / "slow.jsonl.xz") if r.get("gpu")]
    before = [r for r in slow if r["wall"] <= w0]
    after = [r for r in slow if r["wall"] >= w1]
    if not before or not after:
        return {"ok": False, "why": "sampler window does not cover the transfers"}
    r0, r1 = before[-1], after[0]
    nvl_p_tx = (r1["gpu"][ip]["nvl_tx_kib"] - r0["gpu"][ip]["nvl_tx_kib"]) * 1024
    nvl_d_rx = (r1["gpu"][idd]["nvl_rx_kib"] - r0["gpu"][idd]["nvl_rx_kib"]) * 1024
    fast = [r for r in read_jsonl(srv / "samp" / "fast.jsonl.xz") if r.get("gpu")]
    pcie = 0.0
    spans = [(x["w0"], x["w1"]) for x in tr]
    for prev, cur in zip(fast, fast[1:]):
        mid = (prev["wall"] + cur["wall"]) / 2
        if any(a <= mid <= b for a, b in spans):
            dt = cur["wall"] - prev["wall"]
            for j in (ip, idd):
                g = cur["gpu"][j]
                pcie += ((g.get("pcie_tx") or 0) + (g.get("pcie_rx") or 0)) * (1 << 20) * dt
    nixl = sum(x["nixl_bytes"] for x in tr)
    expect = sum(x["ext_tokens"] for x in tr) * pw.KV_BYTES_PER_TOKEN
    ratio = nvl_p_tx / nixl if nixl else None
    share = pcie / nixl if nixl else None
    ok = ratio is not None and 0.8 <= ratio <= 1.25 and share is not None and share < 0.10
    by_len = {}
    for x in tr:
        by_len.setdefault(x["n"], []).append(x["d_s"])
    return {"ok": ok, "nixl_bytes": nixl, "expected_bytes": expect, "nvl_p_tx": nvl_p_tx, "nvl_d_rx": nvl_d_rx,
            "pcie_bytes": pcie, "nvl_ratio": ratio, "pcie_share": share,
            "d_s_median": {str(k): statistics.median(v) for k, v in sorted(by_len.items())},
            "failed": sum(x["failed"] for x in tr), "expired": sum(x["expired"] for x in tr)}


def cmd_decide(a) -> int:
    cd = Path(a.calib)
    runs = {}
    for v in ("A", "B", "U"):
        cj, sj = cd / f"calib_{v}.json", cd / f"srv_{v}"
        if cj.exists() and sj.exists():
            calib = json.loads(cj.read_text())
            runs[v] = {"calib": calib, "g1": link_check(calib, Path(sj.read_text().strip()))}
    gates = {"runs": {v: r["g1"] for v, r in runs.items()}}
    dec = pw.decisions()
    A, B, U = runs.get("A"), runs.get("B"), runs.get("U")
    choice = None
    if A and A["g1"]["ok"]:
        choice = "A"
        if B and B["g1"]["ok"]:
            close = all(B["g1"]["d_s_median"].get(k, 1e9) <= 1.10 * A["g1"]["d_s_median"][k]
                        for k in ("8192", "24576") if k in A["g1"]["d_s_median"])
            if close:
                choice = "B"
    elif B and B["g1"]["ok"]:
        choice = "B"
    elif U and U["g1"]["ok"]:
        choice = "U"
    gates["G1"] = {"pass": choice is not None, "choice": choice}
    rc = 0
    if choice is None:
        rc = 4 if U else 3
    else:
        dec["visibility"] = "B" if choice == "B" else "A"
        dec["ucx_tls"] = "cuda_ipc,cuda_copy,sm,self,tcp" if choice == "U" else None
    corr = (A or {}).get("calib", {}).get("correct") or []
    first = sum(c["first_same"] for c in corr)
    full = sum(c["all_same"] for c in corr)
    strict = bool(corr) and first == len(corr) and full >= len(corr) - 2
    # tolerance added at implementation (README): every divergence a numeric near-tie (P/D token within 0.5 nats of
    # the local top-1 at the first differing position) and the first token identical in >= n - 2 prompts
    ties = [c for c in corr if not c["all_same"]]
    near = all(c.get("div_margin") is not None and c["div_margin"] <= 0.5 for c in ties)
    tolerant = bool(corr) and near and first >= len(corr) - 2
    g2 = strict or tolerant
    gates["G2"] = {"pass": g2, "strict": strict, "near_ties_only": near, "n": len(corr), "first_same": first,
                   "all_same": full, "margins": [c.get("div_margin") for c in ties]}
    if rc == 0 and not g2:
        rc = 2
    dl = (A or {}).get("calib", {}).get("delta") or []
    ok3 = [x["second_ext"] <= x["delta"] + 16 for x in dl]
    g3 = bool(ok3) and sum(ok3) >= 0.95 * len(ok3)
    gates["G3"] = {"pass": g3, "items": dl}
    rs = (A or {}).get("calib", {}).get("reset") or {}
    g4 = rs.get("p_s") is not None and rs.get("d_s") is not None and rs["p_s"] <= 90 and rs["d_s"] <= 90
    gates["G4"] = {"pass": g4, **rs}
    dec["restart_each_level"] = not g4
    if rc == 0 and not (g3 and g4):
        rc = 1
    gates["rc"] = rc
    (cd / "gates.json").write_text(json.dumps(gates, indent=1))
    if rc in (0, 1):
        pw.DECISIONS_JSON.write_text(json.dumps(dec, indent=1))
    print(json.dumps({k: v for k, v in gates.items() if k != "runs"}, default=str)[:2000])
    print(f"decide rc={rc} decisions={json.dumps(dec)}")
    return rc


# -- prompts ------------------------------------------------------------------------------------------------------
def cmd_prompts(a) -> int:
    import render                                         # research/10_rope_shift
    src = Path(a.src)
    calls = []
    for f in sorted(src.glob("runs/*/*.requests.jsonl.xz")):
        for c in read_jsonl(f):
            if c.get("purpose") == "lead" and "response" in c and c.get("messages"):
                calls.append(c)
    rend = render.Renderer(render.MODELS["qwen32b"])
    sized = []
    rng = random.Random("prompts24")
    rng.shuffle(calls)
    for c in calls[: 400]:
        _, ids = rend.render(c)
        if 4000 <= len(ids) <= 31000:
            sized.append((len(ids), c["request_id"], ids))
    sized.sort()
    step = max(1, len(sized) // a.n)
    pick = sized[::step][: a.n]
    out = [{"id": rid, "n": n, "ids": ids} for n, rid, ids in pick]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out))
    print(f"[pd_calib] {len(out)} prompts, {min(x['n'] for x in out)}-{max(x['n'] for x in out)} tokens -> {a.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prompts")
    p.add_argument("--src", default=str(DIR.parent / "12_hw_utilization" / "data" / "cells" / "p2a" / "levels" /
                                        "hw_p2a_n04"))
    p.add_argument("--out", default=str(pw.DATA / "calib" / "prompts24.json"))
    p.add_argument("--n", type=int, default=24)
    p = sub.add_parser("pd")
    p.add_argument("--instances", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--all", action="store_true")
    p.add_argument("--lengths", default="8192,24576")
    p.add_argument("--prompts", default=None)
    p.add_argument("--correct-only", action="store_true")
    p.add_argument("--n-prompts", type=int, default=24)
    p = sub.add_parser("hop")
    p.add_argument("--instances", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("decide")
    p.add_argument("--calib", required=True)
    a = ap.parse_args()
    return {"prompts": cmd_prompts, "pd": cmd_pd, "hop": cmd_hop, "decide": cmd_decide}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
