#!/usr/bin/env python3
"""Cells and levels of research/13_tiered_memory (stage S2 smoke runs on one GPU; S3 cells are added here later).

Sessions are 12's frozen session list (research/12_hw_utilization/data/sessions.jsonl: LIGHT 4-turn sessions on
the 10 real codebases, and the HEAVY mix with 06's test-suite and site-packages-search tasks), driven by 11's
mu_sweep -> mu_session -> profile_run unchanged. A level reuses 12's `level()` shape (the MU_EXTRA_CONDITIONS
hook of 11's mu_workloads) and adds three 13-only fields that tm_pipeline.sh reads:
  server      {"env": {...}, "vllm": [...]}: extra server environment and vLLM flags; the pipeline restarts the
              server whenever the next level needs a different server
  client_env  environment of the harness sessions (TM_CLIENT_D1 / _VERIFY / _HINTS: tm_client.py)
  tm          notes for the analysis (arm name, what it tests)
Every server runs 11's flags with tm_scheduler.TMScheduler (12's HW records + 13's hint log) and tm_server_ext as
middleware; TM_D1=1 always (the per-chain store is what D1 and B1's prefetch read; it changes nothing for a
client that sends full requests).

    python research/13_tiered_memory/tm_workloads.py build            # data/conditions.json
    python research/13_tiered_memory/tm_workloads.py cell s2d --field levels
    python research/13_tiered_memory/tm_workloads.py level tm_s2d_base --field window_s
    python research/13_tiered_memory/tm_workloads.py server tm_s2b_a3   # -> one line: env assignments, then a
                                                                          #    shell-quoted vLLM flag list
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # research/
import _paths  # noqa: F401,E402

import hw_workloads as hw  # noqa: E402  research/12_hw_utilization
import mu_workloads as mw  # noqa: E402  research/11_multiuser_kv

DIR = Path(__file__).resolve().parent
DATA = DIR / "data"
CONDITIONS_JSON = DATA / "conditions.json"
SESSIONS = hw.SESSIONS                      # 12's frozen session list
M32, M8 = hw.M32, hw.M8
GiB = 1 << 30

OFFLOAD64 = ["--kv-offloading-size", "64", "--kv-offloading-backend", "native"]
POLICY = ["--kv-transfer-config", json.dumps({"kv_connector_extra_config": {
    "eviction_policy": "SessionAwareLRU", "cache_policy_module_path": "tm_policy"}})]
B1_ENV = {"TM_DEMOTE": "1", "TM_PREFETCH": "1", "TM_POLICY": "1", "TM_DEMOTE_S": "2.0", "TM_AWAY_S": "10",
          "TM_RESTORE_TOK_S": "40000"}          # S2 probe: 32B DRAM restore 68-75k tok/s idle on RTX PRO 6000


def level(n: int, specs, model: str, window_s: int | None, *, server: dict | None = None,
          client_env: dict | None = None, tm: dict | None = None, vllm: int = 4, agent: int = 11) -> dict:
    c = hw.level(n, tuple(specs), model, window_s, vllm=vllm, agent=agent)
    if window_s:
        c["hw"]["warmup_s"] = min(c["hw"]["warmup_s"], 60 * n)
    c["server"] = server or {"env": {}, "vllm": []}
    c["client_env"] = client_env or {}
    c["tm"] = tm or {}
    return c


W_S2 = 600
CONDITIONS: dict[str, dict] = {
    # S2 D1 smoke, Qwen3-8B (KV holds every session: a pure front-end comparison), LIGHT closed loop
    "tm_s2d_base": level(4, hw.LPOOL, M8, W_S2, tm={"arm": "baseline", "idea": "D1"}),
    "tm_s2d_d1v": level(2, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1", "TM_CLIENT_VERIFY": "1"},
                        tm={"arm": "D1 verify", "idea": "D1"}),
    "tm_s2d_d1": level(4, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1"}, tm={"arm": "D1", "idea": "D1"}),
    # S2 B1 rerun after the 2026-10-04 fixes: DRAM order protects away sessions (tm_policy), FCFS scheduling
    # (priority served only prefetch, which never fired), no demotion at a closed-loop turn end (TM_THINK_S=0);
    # paired A1
    "tm_s2f_a1": level(4, hw.HPOOL, M32, 720, server={"env": {}, "vllm": OFFLOAD64},
                       tm={"arm": "A1 + DRAM 64 GiB", "idea": "B1"}),
    "tm_s2f_a3b": level(4, hw.HPOOL, M32, 720, server={"env": {**B1_ENV, "TM_THINK_S": "0"}, "vllm": OFFLOAD64 + POLICY},
                        client_env={"TM_CLIENT_HINTS": "1"}, tm={"arm": "A3b + hints (fixed)", "idea": "B1"}),
    # S2 D1 rerun after the second 2026-10-04 fix: the client fingerprints messages at send time (the harness edits
    # old tool results in place; identity checks missed it). Same arms as s2d / s2e
    "tm_s2g_base": level(4, hw.LPOOL, M8, W_S2, tm={"arm": "baseline", "idea": "D1"}),
    "tm_s2g_d1v": level(2, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1", "TM_CLIENT_VERIFY": "1"},
                        tm={"arm": "D1 verify", "idea": "D1"}),
    "tm_s2g_d1": level(4, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1"}, tm={"arm": "D1", "idea": "D1"}),
    # S2 D1 rerun after the 2026-10-04 fix of the cut at a history edit (tm_server_ext.cut_at): same arms
    "tm_s2e_base": level(4, hw.LPOOL, M8, W_S2, tm={"arm": "baseline", "idea": "D1"}),
    "tm_s2e_d1v": level(2, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1", "TM_CLIENT_VERIFY": "1"},
                        tm={"arm": "D1 verify", "idea": "D1"}),
    "tm_s2e_d1": level(4, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1"}, tm={"arm": "D1", "idea": "D1"}),
    # S2 B1 smoke, Qwen3-32B (about three sessions' KV on 96 GB), HEAVY mix closed loop
    "tm_s2b_a0": level(4, hw.HPOOL, M32, 720, tm={"arm": "A0 GPU only", "idea": "B1"}),
    "tm_s2b_a1": level(4, hw.HPOOL, M32, 720, server={"env": {}, "vllm": OFFLOAD64},
                       tm={"arm": "A1 + DRAM 64 GiB", "idea": "B1"}),
    "tm_s2b_a3": level(4, hw.HPOOL, M32, 720, server={"env": dict(B1_ENV), "vllm": OFFLOAD64 + POLICY +
                                                      ["--scheduling-policy", "priority"]},
                       client_env={"TM_CLIENT_HINTS": "1"}, tm={"arm": "A3 + hints", "idea": "B1"}),
}

CELLS: dict[str, dict] = {
    "s2d": {"model": M8, "levels": ["tm_s2d_base", "tm_s2d_d1v", "tm_s2d_d1"]},
    "s2b": {"model": M32, "levels": ["tm_s2b_a1", "tm_s2b_a3", "tm_s2b_a0"]},
    "s2e": {"model": M8, "levels": ["tm_s2e_d1v", "tm_s2e_d1", "tm_s2e_base"]},
    "s2f": {"model": M32, "levels": ["tm_s2f_a1", "tm_s2f_a3b"]},
    "s2g": {"model": M8, "levels": ["tm_s2g_d1v", "tm_s2g_d1", "tm_s2g_base"]},
}

# -- 2-GPU levels (tm2_pipeline.sh; S4 calibration, S5 B2 / B3). A 2-GPU level adds:
#   layout   dp2 (two TP=1 servers, each seeing both GPUs own-first) | pd (prefill on GPU0, decode on GPU1) | tp2
#   servers  [{"env": {...}, "vllm": [...]}, ...]: one per server (tp2: one)
#   proxy    {"mode": "affine" | "pd" | "pass", "weights": [a, b]}: pd_proxy.py in front of every arm
K32, L_PEER = 60 * GiB, 24 * GiB                      # H200 32B KV pool K (default ~62 GiB) and the lent region L


def kvmem(n_bytes: int) -> list[str]:
    return ["--kv-cache-memory-bytes", str(n_bytes)]


def offload_cfg(spec: str, size: int, **extra) -> dict:
    cfg = {"kv_connector": "OffloadingConnector", "kv_role": "kv_both",
           "kv_connector_extra_config": {"cpu_bytes_to_use": size, **extra}}
    if spec != "cpu":
        cfg["kv_connector_extra_config"].update(spec_name="PeerHBMOffloadingSpec", spec_module_path="tm_peer_spec",
                                                peer_device=1)
    return cfg


def ktc(cfg: dict) -> list[str]:
    return ["--kv-transfer-config", json.dumps(cfg)]


PEER = offload_cfg("peer", L_PEER)
MULTI = {"kv_connector": "MultiConnector", "kv_role": "kv_both",
         "kv_connector_extra_config": {"connectors": [offload_cfg("peer", L_PEER), offload_cfg("cpu", 64 * GiB)]}}
NIXL = {"kv_connector": "NixlConnector", "kv_role": "kv_both"}


def level2(n: int, specs, model: str, window_s: int, layout: str, servers: list[dict], proxy: dict,
           tm: dict | None = None, agent: int = 15) -> dict:
    c = level(n, specs, model, window_s, tm=tm, vllm=8, agent=agent)
    c.update(layout=layout, servers=servers, proxy=proxy)
    return c


def srv(vllm: list[str] | None = None, **env) -> dict:
    return {"env": {k: str(v) for k, v in env.items()}, "vllm": list(vllm or [])}


W_S5 = 1800
CONDITIONS.update({
    # B2: uneven DP=2 load (A heavy, B light), peer HBM vs DRAM spill tiers at matched HBM
    "tm_b2_dl": level2(24, hw.LPOOL, M32, W_S5, "dp2", [srv(kvmem(K32) + ["--kv-offloading-size", "24", "--kv-offloading-backend", "native"])] * 2,
                       {"mode": "affine", "weights": [3, 1]}, {"arm": "D-L", "idea": "B2"}),
    "tm_b2_pl": level2(24, hw.LPOOL, M32, W_S5, "dp2", [srv(kvmem(K32) + ktc(PEER)), srv(kvmem(K32 - L_PEER))],
                       {"mode": "affine", "weights": [3, 1]}, {"arm": "P-L", "idea": "B2"}),
    **{f"tm_b2_d64_n{n}": level2(n, hw.LPOOL, M32, W_S5, "dp2", [srv(kvmem(K32) + OFFLOAD64)] * 2,
                                 {"mode": "affine", "weights": [3, 1]}, {"arm": "D-64", "idea": "B2"}) for n in (24, 32)},
    **{f"tm_b2_pd_n{n}": level2(n, hw.LPOOL, M32, W_S5, "dp2", [srv(kvmem(K32) + ktc(MULTI)), srv(kvmem(K32 - L_PEER) + OFFLOAD64)],
                                {"mode": "affine", "weights": [3, 1]}, {"arm": "P+D", "idea": "B2"}) for n in (24, 32)},
    "tm_b2_tp2": level2(24, hw.LPOOL, M32, W_S5, "tp2", [srv(["--tensor-parallel-size", "2", "--kv-offloading-size", "128",
                                                              "--kv-offloading-backend", "native"])],
                        {"mode": "pass"}, {"arm": "TP2 + DRAM 128", "idea": "B2"}),
    # B3: prefill/decode disaggregation vs DP=2 (and DP=2 with smaller chunks, and TP=2)
    **{f"tm_b3_dp2_n{n:02d}": level2(n, hw.LPOOL, M32, W_S5, "dp2", [srv()] * 2, {"mode": "affine", "weights": [1, 1]},
                                     {"arm": "DP2", "idea": "B3"}) for n in (4, 8, 16)},
    **{f"tm_b3_c2k_n{n:02d}": level2(n, hw.LPOOL, M32, W_S5, "dp2", [srv(["--max-num-batched-tokens", "2048"])] * 2,
                                     {"mode": "affine", "weights": [1, 1]}, {"arm": "DP2-c2k", "idea": "B3"}) for n in (4, 8)},
    **{f"tm_b3_pd_n{n:02d}": level2(n, hw.LPOOL, M32, W_S5, "pd", [srv(ktc(NIXL)), srv(ktc(NIXL))], {"mode": "pd"},
                                    {"arm": "1P1D", "idea": "B3"}) for n in (4, 8, 16)},
    "tm_b3_tp2_n08": level2(8, hw.LPOOL, M32, W_S5, "tp2", [srv(["--tensor-parallel-size", "2"])], {"mode": "pass"},
                            {"arm": "TP2", "idea": "B3"}),
})
CELLS.update({
    "s4": {"model": M8, "layout": "calib", "levels": []},
    "b2": {"model": M32, "levels": ["tm_b2_dl", "tm_b2_pl", "tm_b2_d64_n24", "tm_b2_pd_n24", "tm_b2_d64_n32",
                                    "tm_b2_pd_n32", "tm_b2_tp2"]},
    "b3": {"model": M32, "levels": ["tm_b3_dp2_n04", "tm_b3_pd_n04", "tm_b3_c2k_n04", "tm_b3_dp2_n08", "tm_b3_pd_n08",
                                    "tm_b3_c2k_n08", "tm_b3_dp2_n16", "tm_b3_pd_n16", "tm_b3_tp2_n08"]},
})


if os.environ.get("TM_TEST_CELLS"):                   # test_tm.py's pipeline test: tiny cells, same code paths
    CONDITIONS.update({
        "tm_tt_base": level(2, hw.LPOOL, M8, W_S2, tm={"arm": "baseline"}),
        "tm_tt_d1v": level(2, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1", "TM_CLIENT_VERIFY": "1"}),
        "tm_tt_d1": level(2, hw.LPOOL, M8, W_S2, client_env={"TM_CLIENT_D1": "1", "TM_CLIENT_HINTS": "1"},
                          server={"env": {"TM_PREFETCH": "1"}, "vllm": []}),
    })
    CONDITIONS.update({
        "tm_tt2": level2(2, hw.LPOOL, M8, W_S2, "dp2", [srv(), srv()], {"mode": "affine", "weights": [1, 1]}),
        "tm_tt3": level2(2, hw.LPOOL, M8, W_S2, "pd", [srv(ktc(NIXL)), srv(ktc(NIXL))], {"mode": "pd"}),
        "tm_tt4": level2(2, hw.LPOOL, M8, W_S2, "tp2", [srv(["--tensor-parallel-size", "2"])], {"mode": "pass"}),
    })
    CELLS.update({"tt": {"model": M8, "levels": ["tm_tt_base", "tm_tt_d1v", "tm_tt_d1"]},
                  "tt2": {"model": M8, "levels": ["tm_tt2", "tm_tt3", "tm_tt4"]}})


def cond(name: str) -> dict:
    c = dict(CONDITIONS[name])
    c["name"] = name
    return c


def server_line(name: str, idx: int | None = None) -> str:
    """'ENV=a ENV2=b<TAB>flag flag ...': the level's server environment and its full vLLM flag list (2-GPU levels:
    server `idx`)."""
    c = CONDITIONS[name]
    sc = c["servers"][idx or 0] if c.get("servers") else c["server"]
    args = list(mw.VLLM_COMMON)
    i = args.index("--scheduler-cls")
    args[i + 1] = "tm_scheduler.TMScheduler"
    args += ["--middleware", "tm_server_ext.Middleware"] + list(sc["vllm"])
    env = {"TM_D1": "1", **sc["env"]}
    return " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())) + "\t" + shlex.join(args)


def export_conditions(path: Path = CONDITIONS_JSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CONDITIONS, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--out", default=str(CONDITIONS_JSON))
    p = sub.add_parser("cell")
    p.add_argument("name")
    p.add_argument("--field", required=True)
    p = sub.add_parser("level")
    p.add_argument("name")
    p.add_argument("--field", required=True)
    p = sub.add_parser("server")
    p.add_argument("name")
    p.add_argument("--idx", type=int, default=None)
    p = sub.add_parser("layout")
    p.add_argument("name")
    p.add_argument("cpus")
    a = ap.parse_args()
    if a.cmd == "build":
        export_conditions(Path(a.out))
        print(f"[tm_workloads] {len(CONDITIONS)} conditions -> {a.out}")
    elif a.cmd == "cell":
        v = CELLS[a.name].get(a.field, "")
        print(" ".join(v) if isinstance(v, list) else v)
    elif a.cmd == "level":
        c = CONDITIONS[a.name]
        if a.field == "client_env":
            print(" ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(c["client_env"].items())))
        elif a.field in ("proxy_mode", "proxy_weights", "nservers"):
            px = c.get("proxy") or {}
            print({"proxy_mode": px.get("mode", ""), "proxy_weights": ",".join(map(str, px.get("weights", []))),
                   "nservers": len(c.get("servers") or [1])}[a.field])
        else:
            v = c.get(a.field, c["hw"].get(a.field))
            print(0 if v is None else v)
    elif a.cmd == "server":
        print(server_line(a.name, a.idx))
    elif a.cmd == "layout":
        cpus = [int(x) for x in a.cpus.split(",")]
        hwc = CONDITIONS[a.name]["hw"]
        v = cpus[:hwc["vllm_cpus"]]
        s = cpus[hwc["vllm_cpus"]:hwc["vllm_cpus"] + 1]
        g = cpus[hwc["vllm_cpus"] + 1:hwc["vllm_cpus"] + 1 + hwc["agent_cpus"]]
        for xs in (v, s, g, cpus):
            print(",".join(map(str, xs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
