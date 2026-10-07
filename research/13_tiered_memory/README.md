# 13 · Tiered memory for the harness: can HBM, a peer GPU's HBM, host DRAM and flash remove the bottlenecks 12 found?

> **Status:** stages S0 (offline ceilings), S1 (code and tests) and S2 (one RTX PRO 6000: node probe, tier
> calibration, live D1 / B1 smoke runs) are done. Results and every observed phenomenon are in
> [RESULTS.md](RESULTS.md); the plan is in [PROPOSAL.md](PROPOSAL.md). S3 (D1 / B1 cells), S4 (2×H200
> calibration) and S5 (B2 / B3 cells) are next.
>
> **Question (user, 2026-10-03):** which parts of the harness system are the bottleneck now, and how can a tiered
> memory hierarchy (HBM, several GPUs' HBM, host DRAM, flash) remove them? The user picked four ideas:
> - **B1:** agent-aware tiered KV, GPU → DRAM → flash, driven by harness hints;
> - **B2:** a peer GPU's HBM as a KV tier over NVLink;
> - **B3:** prefill/decode disaggregation with KV over NVLink;
> - **D1:** session-resident token state in host DRAM.
>
> **Constraints (user, 2026-10-03):**
> - new folder; no existing research directory is replaced or edited;
> - more than one GPU per job only when TP / DP / P-D > 1, so B1 and D1 run on 1 RTX PRO 6000 and B2 and B3 on an
>   H200 pair;
> - QoS `standard`;
> - real harness and real tasks only.
>
> **Jobs:**
> - S2a 117716; S2b 117737, 117827, 117864, 117867. All 1 GPU, RTX PRO 6000 (alphagpu51 / 54), 2026-10-04.
> - S0 / S1 CPU jobs: 117680-117828.
>
> **Headline (RESULTS.md):**
> - **S0:** B3 has the largest ceiling (decode stalls: 21-45 % of 32B call time at N = 4-8).
> - **D1 live:** exact (794 / 794); front end −77 %; reply re-prefill −90 %.
> - **B1 live:** the DRAM tier gives 1.65× over GPU only; hints are neutral on throughput once fixed.
>
> **Commits:** none yet.

## Files

| file | stage | what it is |
|---|---|---|
| `PROPOSAL.md`, `RESULTS.md` | — | the plan; the S0-S2 results and phenomena |
| `tm_ceilings.py` → `data/tables/S0_ceilings.md` | S0 | what each idea can win at most, from 11/12's scheduler logs and request captures |
| `tm_predict.py` → `tool_durations.json` | S0 | B1's tool-duration predictor (a frozen input): 137k server-side windows by the reply's dominant tool |
| `tm_server_ext.py` | S1 | vLLM `--middleware`: D1 (per-chain token store, delta render through vLLM's decode-side token reuse, verify mode) and B1's API-server half (hints, prefetch shadow requests) |
| `tm_client.py`, `tm_boot.py`, `bin/tmpython` | S1 | D1 client and B1 client hints, installed under 11's mu_session through mu_sweep's `--python` |
| `tm_scheduler.py`, `tm_policy.py` | S1 | B1 scheduler (hint socket, demote / protect, auto-sized gate) and the session-aware DRAM eviction policy |
| `tm_peer_spec.py`, `pd_proxy.py` | S1 | B2 peer-HBM offload spec; the B2 / B3 proxy (session-affine, P/D, pass) |
| `tm_calib.py`, `tm_vllm_probe.py`, `tm_probe.sbatch` | S2a / S4 | tier calibration; a live server's capacity, prefill, decode and restore rates |
| `tm_workloads.py`, `tm_pipeline.sh` + `tm1.sbatch`, `tm2_pipeline.sh` + `tm2.sbatch` | S2-S5 | cells and levels; 1-GPU and 2-GPU orchestration |
| `tm_analyze.py` | S1 / S2 | `probe`, `d1`, `b1`, `d1replay` → `data/tables/S1_*.md`, `S2_*.md` |
| `tm_common.py`, `tm_fake_vllm.py`, `test_tm.py`, `tm_cpu.sbatch` | — | shared helpers, a D1-aware fake vLLM, tests, offline jobs |

`data/` is gitignored except the `*.md` tables (research-data convention; raw streams go to Google Drive with
`research/common/drive_data.py push 13`).
