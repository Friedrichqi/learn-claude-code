# Search log: literature review of 2026-11-01

This is the audit trail behind the two notes below. It records every query, the saturation verdict for each sub-area, the closest works, and the leads that were left unchecked.
- [`unified_memory_table_related_work.md`](unified_memory_table_related_work.md) (topic 1)
- [`self_evolving_harness_related_work.md`](self_evolving_harness_related_work.md) (topic 2)

The rows behind both notes are in [`lit_review_1101_papers.csv`](lit_review_1101_papers.csv).

## How the review was run

**Timing:**
- Round 1 ran 05:07–05:53 UTC: six parallel sweep agents, three per topic.
- Round 2 started at 05:36 (topic 2) and 05:53 (topic 1): one snowball / gap-fill agent per topic.
- The topic 1 round-2 agent was cut short at about 06:00 UTC. The auto-mode safety check refused its network calls for the rest of its session, so it did not finish (see its log below). It was not re-delegated without asking.
- Round 3 (16:40 UTC): with the user's approval, one gap-fill agent reran the unchecked list. It was stopped the same way after about 8 minutes, when a GitHub fetch was refused, and did not retry.

**Protocol given to every agent:**
- A paper counts only if its canonical page was fetched in-session and the title matches.
- Each row carries a quoted line as evidence for its coverage claims.
- Each agent logs every query.
- Saturation means two consecutive query batches added fewer than 2 new relevant items.

**Sources:**
- arXiv API;
- Semantic Scholar (search and citations);
- OpenAlex and Crossref (DOI metadata, because ACM and IEEE pages return 403 to scripts);
- conference program pages;
- vendor docs and specs, and GitHub design docs.
- DBLP's API was bot-blocked for the whole session.

**Verification by the orchestrator after the agents finished:**
- All 818 arXiv IDs were re-resolved through the arXiv API batch endpoint, and their titles were compared. Three differ, all legitimately: Mooncake's FAST'25 title versus its arXiv title, SYMPHONY's retitle, and Mosaic's arXiv title.
- All 239 non-arXiv rows were checked: DOIs by Crossref title match, and other URLs by HTTP status.
  - Enfabrica/HPCwire returns 403 to scripts; the agent had fetched it.
  - Phantom Walk (DAC'26): its DOI does not resolve yet. It is the only row left `verified=False`.
- The CSV `check` column records the method used for each row.

**Row counts:**

| topic | relevance 3 | relevance 2 | relevance 1 | total |
|---|---|---|---|---|
| 1 | 37 | 128 | 327 | 492 |
| 2 | 16 | 159 | 425 | 600 |

Round 3 added 14 topic-1 rows and corrected one. These rows were verified by the agent's own fetch; the orchestrator did not re-check them by script, because the round ended on a safety refusal. MemFabric_Hybrid is unverified.

**Corrections from the round-2 cross-checks, applied in the CSV `note` column:**
- CloudMatrix384: peer and remote HBM are "partial", because they are reachable but not managed KV tiers.
- xDeepServe: "one table" is "partial".
- Mooncake: the master's mapping is a key → location index, not address translation.
- G10: T1 is "no".
- Autopoiesis: its gains come from its execution model.
- SchedCP: canary deploy is not evaluated.
- INFRAMIND: load reaches the agents through a reasoning prompt chosen from telemetry.

---

# Shared protocol given to the agents

## Shared protocol for literature-sweep agents (read fully before starting)

Today is 2026-11-01. Papers exist up to arXiv 2611.xxxxx. Your training knowledge stops around mid-2026, so
2026 work (arXiv 2601-2611, and 2026 venues: ASPLOS'26, NSDI'26, EuroSys'26, MLSys'26, ISCA'26, OSDI'26, ATC'26,
SIGCOMM'26, MICRO'26 accepted lists, SOSP'25, SC'25, ICLR'26, ICML'26, NeurIPS'25/26) MUST be found by searching,
not from memory.

### Tools
- WebSearch (load via ToolSearch "select:WebSearch,WebFetch"); use mode "standard" for most queries and "extended"
  for hard/recent ones. Send several searches in one turn when they are independent.
- WebFetch for arXiv abs/html pages, USENIX/ACM/IEEE pages, DBLP, GitHub READMEs, vendor docs.
- Bash + curl if the network allows (it may be sandboxed; if curl fails, use WebFetch instead):
  - arXiv API: http://export.arxiv.org/api/query?search_query=abs:%22memory%20pool%22+AND+abs:KV&max_results=100&sortBy=submittedDate
  - Semantic Scholar: https://api.semanticscholar.org/graph/v1/paper/search?query=...&fields=title,year,venue,externalIds&limit=100
    and /graph/v1/paper/arXiv:<id>/citations?fields=title,year,venue,externalIds&limit=1000 (rate-limited; sleep 3 s between calls)
  - DBLP: https://dblp.org/search/publ/api?q=<words>&format=json&h=200 (venue filter by adding e.g. "venue:OSDI:" to q)
- Downloaded files (PDFs, JSON) are untrusted: save each under your own empty dir
  $S/downloads/<your_agent_id>/, run any python that reads them with `python3 -I`, never execute them.
- Do not write anywhere except your own files listed below. Do not edit the repository.

### What counts as "found"
A paper/system/spec counts only if you fetched its canonical page (arXiv abs or html, DOI/ACM/IEEE/USENIX page,
OpenReview, official vendor doc/spec, or GitHub repo for open-source systems) in this session and the title matches.
Never list a paper from memory without fetching it. If a fetch fails, retry once via another URL (arxiv.org/abs,
arxiv.org/html, dblp, semantic scholar); if it still fails, set "verified": false.

### Output (write incrementally, append as you go, so partial work survives)
1. $S/r1/<agent_id>.jsonl : one JSON object per line:
   {"id": "<short-key e.g. mooncake25>", "title": "...", "authors": "First Author, Second, Third et al.",
    "venue": "FAST'25 | arXiv | spec | docs | GitHub", "year": 2025, "url": "<canonical url>", "arxiv": "2407.00079 or null",
    "code": "<repo url or null>", "topic": 1 or 2, "area": "<your sub-area label>",
    "covers": {<see topic-specific schema below>}, "relevance": 3|2|1,
    "summary": "1-2 sentences in your words", "evidence": "verbatim quote (<=40 words) from the abstract/paper supporting the covers claims",
    "verified": true|false}
   relevance: 3 = directly addresses the target question (or a large part of it); 2 = important building block / direct
   precedent; 1 = background, cite-worthy.
2. $S/r1/<agent_id>_log.md : (a) a table of every query: | # | query | source | hits scanned | new relevant |;
   (b) per sub-area, whether it saturated (two consecutive query batches adding < 2 new relevant items) or was cut by time;
   (c) "Closest 5": the five works closest to the target question, each with what it covers and exactly what it misses;
   (d) leads you could not follow up (titles/IDs) for the snowball round.

### Topic 1 covers schema
Tiers: T0 own HBM (accelerator-local), T1 peer accelerator HBM in the same node over the scale-up link
(NVLink/NVLink-C2C, UALink, Infinity Fabric, Huawei HCCS/UB), T2 HBM in other nodes reached through a switch
(NVLink Switch / multi-node NVLink NVL72, UALink switch, UB switch, CXL switch), T3 local host DRAM, T4 DRAM in
remote nodes (RDMA, CXL pool, UB), T5 flash/SSD (local, NVMe-oF, distributed FS).
"covers": {"T0":"yes|partial|no","T1":...,"T2":...,"T3":...,"T4":...,"T5":...,
 "unified_table":"yes|partial|no"  (one address space / page table / global mapping over all tiers it covers),
 "vm_mechanisms":["translation","demand_fault","migration","eviction","prefetch","protection","coherence","sharing"],
 "granularity":"page 4K/2M | block of N tokens | tensor | object | segment",
 "kv_specific": true|false, "open_source": true|false}

### Topic 2 covers schema
Target system: an edge server stores agent traces + HW/perf metrics + congestion signals of an LLM serving cluster;
it calls LLMs to diagnose the systematic root cause of performance degradation; it synthesizes "compensation" tool
functions/policies; these are deployed into the cloud-level agent harness so the harness (and the agents) can
navigate the bottleneck under the current load; the loop repeats (self-evolving).
"covers": {"telemetry":"yes|partial|no" (collects traces/metrics/congestion signals),
 "llm_rca":"..." (LLM diagnoses root cause of performance/reliability problems),
 "synthesis":"..." (LLM writes new code/tools/policies/heuristics, not only picks parameters),
 "harness_deploy":"..." (the generated artifact is integrated into an agent harness/agent toolset),
 "runtime_guidance":"..." (guidance acts online under live load, not offline),
 "closed_loop":"..." (repeats/evolves automatically with evaluation feedback),
 "edge_cloud_split":"...", "llm_serving_domain": true|false}

### Time
Run `date -u` at start. Aim to finish your sweep in about 85 minutes; check `date -u` every ~15 tool calls. Breadth
first (enumerate everything relevant), then depth on relevance-3 items (read the paper body, not only the abstract,
to fill covers precisely). Be thorough: aim for every relevant paper, typically 40-100 rows for your scope.
Your final reply to the orchestrator: <= 300 words: counts per sub-area, the Closest-5 list, saturation status,
and the paths of your two files. Do not paste the JSONL.

---

# Agent log: `t1_fabric_gpu` (round 1)

## t1_fabric_gpu log (Topic 1: hardware/fabric-level unified addressing, GPU-centric VM)

Start: 2026-11-01 05:08 UTC; end ~05:53 UTC. Totals: 175 rows (rel-3: 21, rel-2: 64, rel-1: 90; 1 disambiguation row for the unrelated "KVLink" paper). 174/175 verified (Phantom Walk title-only).

### (a) Query table
| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | abs:"unified memory" AND GPU AND node | arXiv API | 11 | 0 |
| 2 | abs:"UB-Mesh" / CloudMatrix / "Unified Bus" / UALink | arXiv API | 17 | 9 |
| 3 | abs:"memory pool" AND "scale-up"; "GPU memory" disaggregat | arXiv API | 1 | 0 |
| 4 | abs:CXL AND GPU AND memory | arXiv API | 40 | 12 |
| 5 | abs:"memory-semantic" | arXiv API | 40 | 5 |
| 6 | NVLink/multi-GPU "address translation", "multi-GPU" TLB | arXiv API | 3 | 1 |
| 7 | "unified virtual memory" GPU | arXiv API | 10 | 4 |
| 8 | "Grace Hopper" memory; NVL72; "global address space" GPU; rack-scale GPU memory; superpod memory; "fabric-attached memory" | arXiv API | 60 | 8 |
| 9 | "page migration" GPU; "memory disaggregation" accelerator; "memory tiering" GPU; GPU SSD "virtual memory"; "GPU-initiated" storage | arXiv API | 35 | 6 |
| 10 | "high bandwidth flash" | arXiv API | 25 | 4 |
| 11 | HBM pool GPU; "scale-up" memory address; ti:"unified memory" LLM | arXiv API | 70 | 2 |
| 12 | "KV Switch"/"KV-Switch" LLM | WebSearch | 10 | 0 (no such named fabric) |
| 13 | "KV Link"/"KVLink" interconnect | WebSearch + arXiv ti:KVLink | 11 | 1 (disambiguation) |
| 14 | Atlas 950 SuperPoD "unified memory addressing" | WebSearch | 15 | 1 |
| 15 | ti:KVLink, "KV Link", "KV Switch", "UnifiedBus", "Extended GPU Memory", "NVLink-C2C", "Infinity Fabric", MI300A, "memory pooling" GPU | arXiv API | 90 | 9 |
| 16 | multi-GPU address translation | Semantic Scholar | 40 | 8 |
| 17 | unified virtual memory GPU oversubscription | Semantic Scholar | 40 | 7 |
| 18 | Phantom Walk...; ScalePool...; UALink 1.0 memory semantics | WebSearch | 30 | 4 |
| 19 | CUDA VMM fabric handle; IMEX; EGM; UALink Q&A; hiascend Atlas 950 | WebFetch (vendor docs) | 6 | 5 |
| 20 | SN40L; Panmnesia CXL-GPU; CXL 3.0 GFAM | WebSearch | 30 | 2 |
| 21 | ti:"CXL-GPU"; Gen-Z; memory-driven computing; NVSHMEM; NVLink memory pool; peer GPU cache | arXiv API | 30 | 4 |
| 22 | 25 seed titles (MCM-GPU ... Selective GPU caches) | Semantic Scholar title-match | 25 | 24 |
| 23 | GB200 NVL72 unified memory; Cerebras MemoryX; Enfabrica EMFASYS | WebSearch | 35 | 2 (leads) |
| 24 | "remote HBM"; HBM disaggregat LLM; memory fabric LLM; global memory pool; unified address space xPU; scale-up domain; NVLink Fusion; tiered memory GPU flash | arXiv API | 40 | 3 |
| 25 | unified VA across HBM/peer/remote/DRAM/SSD 2026; ISCA'26/MICRO'25 memory disaggregation accelerators | WebSearch extended | 18 | 3 (MemNetAI) |
| 26 | MemNetAI program; Beyond Monoliths; NVMe staging CXL | WebFetch + OpenAlex | 5 | 4 |
| 27 | "far memory" GPU; remote memory GPU paging; "address translation" accelerator; UVM RDMA; multi-node unified memory; memory expansion GPU CXL/SSD | arXiv API | 40 | 5 |
| 28 | Huawei UnifiedBus spec / URMA; HPE The Machine; Linux HMM docs | WebSearch + WebFetch | 25 | 3 |
| 29 | ti:Aqua; "NVMe-oF" GPU; scale-up offload GPU; multi-GPU memory pool LLM; distributed shared memory GPU; memory semantics pool | arXiv API | 70 | 5 |
| 30 | AMD Infinity Fabric multi-GPU, MI300A inter-APU, CXL surveys | arXiv abs fetch | 9 | 6 |
| 31 | 11 seed titles (ThymesisFlow, FlatFlash, FlashMap, ZnG, HPCA'17 accel xlat, Buddy, SPIN, UVM prefetch/evict, UVM in-depth, Dojo, TPUv4) | Semantic Scholar title-match | 11 | 10 |
| 32 | NVIDIA ICMS / BlueField-4; Dynamo KVBM | WebSearch + WebFetch | 20 | 2 |
| 33 | "KV cache" NVLink pool; Ascend memory pool; superpod KV; peer GPU HBM; memory semantic KV; global KV pool | arXiv API | 15 | 3 |
| 34 | UALink switch translation research 2026; NVLink Fusion/Rubin unified memory | WebSearch extended | 20 | 2 |
| 35 | UALink 1.0 overview blog; amdgpu UALink patch series | WebFetch + curl | 2 | 2 |
| 36 | Lingqu UB 2.0 spec; Ascend superpod OS memory management paging | WebSearch (std+extended) | 20 | 3 (HyperOffload, StrataCL, xDeepServe upgrade) |
| 37 | multi-GPU UVM 2025 DOIs (IISWC, PDP, ICS, CAL, ICPE) | Semantic Scholar batch | 5 | 5 |
| 38 | citations of G10, CloudMatrix384, UB-Mesh, GMT | Semantic Scholar citations | 146 | 12 |
| 39 | snowball DOIs (UHM, CXL tensor offload, AGIO, GoFS, Forest, FRUGAL, OpenSUN, SiPAM, scale-up survey, GPU I/O path) | Semantic Scholar batch | 11 | 10 |
| 40 | SAC CXL sparse KV; DeepSeek-V3 insights; seminal abstracts via OpenAlex | arXiv + OpenAlex | 7 | 2 (+5 verifications) |
| 41 | Enfabrica EMFASYS; CXL 3.0 GFAM Q&A | WebSearch + WebFetch | 20 | 2 |
| 42 | NVL72 papers (FlashBoot, DWDP, MoK), MCM GPU, FlatAttention | arXiv abs fetch | 5 | 3 |
| 43 | MICRO'25/ISCA'26/ASPLOS'26 multi-GPU translation | WebSearch extended + S2 title-match | 14 | 4 (ShadowUpdate, SoftWalker, HMM overhead, UVM TACO) |
| 44 | chiplet GPU TLB; wafer-scale VM; IOMMU GPU; multi-GPU page placement | arXiv API | 20 | 1 (GMEM) |
| 45 | vAttention, Infinite-LLM, MemServe, Mooncake (cross-check with KV pools) | arXiv abs fetch | 4 | 4 |
| 46 | openEuler UB Service Core / SuperPoD OS; memory hierarchy HBM SSD NVLink/CXL LLM; memory virtualization cluster; supernode memory | WebSearch + WebFetch + arXiv API | 40 | 1 (openEuler UBS) |
| 47 | GPU page fault to storage; unified memory SSD GPU; memory-mapped GPU SSD; GDS tiering; KV page table GPU | arXiv API | 12 | 2 (FlatCake, eLLM) |
| 48 | recency-sorted saturation: "scale-up" address space; load/store fabric accelerators; "unified virtual memory"; GPU SSD direct; CXL GPU KV/LLM | arXiv API (sortBy=submittedDate) | 71 | 1 (LM-CXD) |
| 49 | GH200 NVL32 / DGX GH200 NVLink-addressable memory; SPIN; GDS docs | WebSearch + curl | 20 | 4 |
| 50 | Huawei UB 2.0 spec release; UMDK/URMA; UALink 2.0 | WebSearch + WebFetch | 30 | 2 |
| 51 | verification passes (OpenAlex abstracts for MCM-GPU, NUMA-GPU, IDYLL, ActivePointers, paged GPU, GPUswap, CC-NUMA, HPCA'17, Dojo; USENIX pages for FlashNeuron, GeminiFS) | OpenAlex + curl | 11 | 0 (verification) |
| 52 | CXL "Type-2"; CXL "address translation"; CXL "unified memory" | arXiv API | 9 | 0 |
| 53 | CPU-side disaggregated-memory VM precedents (MIND, Clio, LegoOS, Kona) | Semantic Scholar title-match + arXiv + USENIX | 4 | 3 |

Notes on sources: DBLP API was blocked by a bot check (Anubis) for the whole session, so venue sweeps used Semantic Scholar (search, title-match, batch, citations; heavily rate-limited), OpenAlex (abstracts for ACM/IEEE items whose pages return 403), arXiv API, USENIX pages (curl), vendor docs (WebFetch). PDFs (UALink spec, Enfabrica tutorial, Samsung white paper, HUVM/GMT full text) could not be parsed here (no poppler/pypdf).

### (b) Saturation per sub-area

| Sub-area | rows | status |
|---|---|---|
| A. Scale-up fabrics / unified addressing (A-scaleup-fabric 54, A-cxl 25, A-fabric-VM 7, A-accelerator-memory 4) | 90 | Academic (arXiv/S2) side saturated: batches #46 and #48 each added 1 new relevant item. Vendor-doc long tail NOT saturated (each targeted vendor search still found 1-2 pages: NVIDIA, Huawei/openEuler, UALink); cut by time. |
| B. Multi-GPU / multi-node translation and UVM (B-multiGPU-translation 22, B-UVM 26) | 48 | Saturated: #44 added 1, #48 ("unified virtual memory" by date) added 0; S2 citation snowball of G10/GMT returned only already-known UVM items. |
| C. GPU-centric unified memory + storage (C-gpu-storage-tiering 36) | 36 | Saturated for academic work: #47 added 2, #48 (GPU SSD direct, by date) added 0 new beyond already-recorded; HBF (high-bandwidth flash) 2026 cluster sampled (6 rows) rather than enumerated exhaustively (~20 HBF preprints exist, mostly device/LLM-serving studies without addressing design). |

### (c) Closest 5 (to: ONE memory table over T0 own HBM, T1 peer HBM over scale-up link, T2 remote-node HBM via switch, T3 local DRAM, T4 remote DRAM, T5 flash, controlled with VM mechanisms)

No work found implements a single page table / VM-managed space over all six tiers. The closest are:

1. **Huawei UB superpod address space: xDeepServe (arXiv 2508.02520) + StrataCL (arXiv 2607.26444) on CloudMatrix384** — Covers T0-T4 in ONE hardware address space: UB maps all NPU HBM physical ranges (384 NPUs, 48 servers) and CPU DRAM into a global UB physical address space; a local VA is mapped onto remote-HBM physical pages in the NPU page table (identical VA on all peers), UB switches hold address-decode/translation entries, and remote host DRAM is mapped into the CPU page table. Misses: T5 (flash is not in the address space; it only appears behind EMS as a KV-store tier); mappings are explicit/registration-based (no demand faults, no migration, no eviction, no access-tracking policy); per-device page tables, not one shared table; no coherence beyond ordering primitives.
2. **CloudMatrix384 / CloudMatrix-Infer EMS (arXiv 2506.12708)** — The only system that touches all six tiers end to end for KV: NPUs (T0-T2 over UB) read KV/model blocks directly from a disaggregated pool of CPU DRAM across nodes (T3/T4) with an EVS-SSD tier below (T5), LRU eviction DRAM->SSD, prefetch hints, dedup. Misses: the pool is a key-value store (MP SDK Put/Get, DHT metadata, 128-512-token blocks), not a virtual-memory table; NPU HBM is a consumer, not a managed cache tier of the pool; flash is cloud EVS volumes, not addressable memory; no translation/fault path in hardware.
3. **NVIDIA GH200/GB200 multi-node NVLink: CUDA VMM fabric handles + IMEX + EGM (docs) and DGX GH200 (144 TB GPU-addressable)** — Production single VA space over T0 local HBM, T1 peer HBM, T2 remote-node HBM via NVLink Switch (two-stage VA->PA->Fabric Address; importer uses the foreign GPU's page tables), T3/T4 local and remote Grace LPDDR via EGM; GH200 superchip adds ATS + access-counter page migration between T0 and T3. Misses: T5 (GDS is explicit I/O, not mapped); cross-node mappings are explicit export/import (no demand faulting, migration or eviction across nodes or between remote tiers); no global replacement policy.
4. **G10 (MICRO'23, arXiv 2310.09443)** — The closest VM-paradigm design: one UVM page table whose leaf PTEs point to GPU memory, host memory, or flash pages (FTL mapping folded in, SSD controller updates PTEs on GC), with demand faults, compiler-planned pre-eviction/prefetch, TLB/page-table consistency, and multi-GPU sharing of the flash space. Covers T0, T3, T5 (+T1 partially via existing multi-GPU UVM). Misses: T2 and T4 (no remote-node HBM or remote DRAM), simulation only, single node, no KV-specific policy.
5. **UALink scale-up translation stack: UALink 1.0 spec + amdgpu UALink driver series (Aug 2026) + reverse-translation study (arXiv 2604.02473)** — Defines and implements the T0-T2 translation substrate: 57-bit pod-wide Network Physical Address space partitioned per GPU, accelerator MMU (VA->NPA) plus Link MMU at the target (NPA->SPA, 5-level tables, Link TLB), exporter-side NPA page tables, importer maps NPA into per-process GPUVM tables, remote TLB shootdowns on unmap, authorization. Misses: T3/T4/T5 entirely (spec states host CPU may not access remote-node memory), exported memory is pinned (no paging/migration/eviction), no tiering policy.

Runners-up (each covers a large part, misses more): Dynamo KVBM (T0,T3,T4,T5 KV block registry; misses T1/T2, not VA); openEuler UB Service Core / SuperPoD OS (OS-level UBS Mem pooling + reported 4-tier KV; mechanisms undocumented); ScalePool (T0-T2 coherent + CXL tier-2 pool; no T3/T5, no page tables); FengHuang (HBM as cache of a TAB-attached rack memory pool with tensor paging; simulation, no T3/T5); HUVM (ATC'22, UVM hierarchy T0->T1->T3; single node); Co-Fabric (switch-translated flat physical space over all xPU HBM across hosts; T0-T2 only); sixtier_kv (arXiv 2604.26968, 6 tiers HBM/DRAM/CXL/NVMe/RDMA/PFS, analytical, misses T1/T2); HyperOffload (Ascend supernode HBM/host/remote pool, compiler-managed).

Gap statement: the pieces exist in separate layers (plus CPU-side precedents for switch-resident or memory-side translation: MIND SOSP'21 puts the MMU/coherence directory in a programmable switch; Clio ASPLOS'22 translates at hardware memory nodes; LegoOS OSDI'18 manages one VA over local cache + remote DRAM + storage) — fabric global physical addressing (UB, NVLink+IMEX, UALink NPA, Co-Fabric) for T0-T2(+T3/T4 on UB/EGM), VM paging with flash PTEs (G10, FlatFlash/FlashMap, Dragon) for T0/T3/T5 on one node, and KV-block tier managers (EMS, KVBM, Mooncake) spanning DRAM/SSD across nodes — but no published design unifies them under one translation table with demand faulting, migration and eviction across all six tiers.

### (d) Leads not followed up (for snowball round)

- UALink 200G 1.0 full spec (Evaluation Copy PDF, ualinkconsortium.org/wp-content/uploads/2025/04/UALink200_Specification_v1.0_Evaluation_Copy.pdf); UALink white paper Jan 2026 (57-bit fabric address space, "128 PB directly addressable"); UALink 2.0 (2026-04-07) — PDFs unreadable here.
- IEEE 802.1 contribution "Network efficiency from first principles part 3: UALink, a shipping case study" (mentor.ieee.org 1-26-0018-01) — section "What UALink does to the address".
- Huawei UnifiedBus 2.0 technical specification (released 2025-09-18) — memory/addressing chapters; UB OS component "unified memory addressing".
- openEuler SuperPoD OS keynote (It's FOSS 2026-10-13; PR Newswire 2025-11-29): 4-tier KV (HBM, local DRAM, UB-shared DRAM pool, SSD/object) + global index; need primary openEuler doc.
- amdgpu UALink series part 2 (IOCTL interface + docs), lore.kernel.org thread.
- HUVM (ATC'22) and GMT (ASPLOS'24) full text (memHarvester / GPU-side tier policies) — PDF only.
- MemNetAI'26 papers: "Memory as a First-Class Resource in AI-Factory Simulation"; "Enabling HW-SW Co-Design ... via Fine-Grained GPU Memory Tracing".
- MPiCO (GLSVLSI'26) and DeepPiC (ASP-DAC'26) memory-pool XPU-PIM clusters; PMKS (EEI 2026) multi-tier storage for ultra-long-context inference.
- Phantom Walk (DAC'26, DOI 10.1145/3770743.3804244) abstract not retrieved (recorded verified=false).
- Samsung "Optimizing KV Cache Offloading to CMM-D in a CXL Switch-based Memory Pool" white paper (PDF); Marvell 260-lane CXL 3 switch (OFC 2026); UnifabriX AI-Infra Summit 2026 deck (UALink memory tier).
- Enfabrica MUG'25 memory-system tutorial PDF (tiering software details).
- NVIDIA Vera Rubin NVL144 memory model (NVLink 6 + C2C, LPDDR SOCAMM) and NVLink Fusion — no primary NVIDIA doc fetched.
- Mooncake "Kunpeng UB Transport" design doc (aionw.github.io) — Mooncake transfer engine over UB.
- ~15 further 2026 HBF preprints (HBF-Sim 2609.29246, HBFSim 2609.09800, Characterizing HBF 2609.39131, HBF Sucks? 2608.11668, FlashAccel 2607.10186, SPLASH 2609.23816, Lachesis 2610.08378, Gandalf 2610.30791) not individually recorded.
- "Topology-Aware Virtualization over Inter-Core Connected NPUs" (arXiv 2506.11446); DxPU (arXiv 2310.04648, GPU pool over network, TACO).
- KV-cache software tiering systems (LMCache, Tutti 2605.03375, KVDrive 2605.18071, OasisKV 2608.08097, TokenLake 2508.17219) — left to the KV-software sweep.

---

# Agent log: `t1_os_disagg` (round 1)

## t1_os_disagg sweep log (topic 1: OS/VM for tiered & disaggregated memory)

Start 2026-11-01 05:08 UTC. Tools: arXiv API (export.arxiv.org, works), arxiv.org/abs + usenix.org via curl (work), dl.acm.org (403 Cloudflare; verified ACM papers via arXiv copy, Semantic Scholar DOI lookup, or GitHub), DBLP (bot wall, unusable), Semantic Scholar (rate-limited, DOI lookups OK with sleeps).

### (a) Query table
| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | abs:"disaggregated memory" AND (page table OR address translation OR virtual memory) | arXiv API | 3 | 2 (Clio, Space-Control) |
| 2 | abs:"memory disaggregation" AND abs:"operating system" | arXiv API | 3 | 1 (INDIGO) |
| 3 | abs:"disaggregated memory" AND abs:switch | arXiv API | 4 | 3 (Beluga, Ditto, Callosal) |
| 4 | abs:"far memory" AND (paging OR swap) | arXiv API | 4 | 2 (3PO, Atlas) |
| 5 | abs:"tiered memory" AND abs:"page placement" | arXiv API | 3 | 2 (Nomad, TPP) |
| 6 | abs:"multi-tier memory" | arXiv API | 11 | 2 (MTM, Mole) |
| 7 | abs:"memory tiering" AND abs:CXL | arXiv API | 17 | 9 (NeoMem, HybridTier, ScalePool, Photonic-CXL, CXL rack, ...) |
| 8 | abs:"unified virtual memory" AND (SSD OR flash) | arXiv API | 0 | 0 |
| 9 | abs:"unified address space" AND GPU/accelerator | arXiv API | 1 | 1 (Grace Hopper data movement) |
| 10 | abs:"global address space" AND GPU/accelerator | arXiv API | 5 | 0 |
| 11 | abs:"memory pooling" AND GPU/accelerator | arXiv API | 36 | 5 (CMM-MP, CCCL, Cohet, ScalePool, Beluga) |
| 12 | abs:CXL AND GPU AND "shared memory" | arXiv API | 7 | 3 (Hitchhiker's guide, TraCT, DAXFS) |
| 13 | memory-semantic SSD OR CXL-SSD | arXiv API | 10 | 6 (SkyByte, ByteFS, Block-to-Byte, OpenCXD, CXL-SSD sim, chunk-aware KV) |
| 14 | GPU AND "unified memory" AND SSD/NVMe/storage | arXiv API | 10 | 1 (G10) |
| 15 | "disaggregated memory" AND GPU | arXiv API | 6 | 1 (CXL failure-tolerant training) |
| 16 | "in-network" AND "memory management" | arXiv API | 92 | 3 (MIND, GMEM, GPUVM) |
| 17 | "distributed shared memory" AND (RDMA OR CXL) | arXiv API | 2 | 2 (xDSM, DSM-DB) |
| 18 | unified virtual memory spanning GPU HBM, peer GPU, CXL pool, SSD single page table | WebSearch ext | 9 | 2 (UPM MI300A, G10 confirm) |
| 19 | memory disaggregation programmable switch address translation page table rack-scale OS | WebSearch | 10 | 2 (dReDBox SW arch, DRackSim) |
| 20 | GPU-centric memory tiering HBM host SSD GMT ASPLOS'24 | WebSearch | 10 | 2 (GMT, HBF-Sim) |
| 21 | USENIX seed-page sweep (20 guessed presentation URLs) | usenix.org | 20 | 19 |
| 22 | memory disaggregation AND accelerator/GPU/NPU | arXiv API | 7 | 1 (ScalePool dup) |
| 23 | rack-scale AND memory AND address space/VM | arXiv API | 1 | 0 |
| 24 | "single system image" | arXiv API | 5 | 0 |
| 25 | address translation AND (CXL OR disaggregated) | arXiv API | 2 | 1 (Tiara) |
| 26 | page table AND GPU AND (multi-GPU OR NVLink) | arXiv API | 1 | 0 (dup) |
| 27 | fabric-attached memory | arXiv API | 2 | 1 (DeACT) |
| 28 | memory-centric AND operating system | arXiv API | 2 | 0 |
| 29 | 18 seed-title probes (Pond, Mira, DiLOS, Fastswap, Kona, TrackFM, HeMem, Memtis, Colloid, TMTS, FlatFlash, TMO, Octopus, Demystifying CXL, survey, Memstrata, DeepUM, Mercury) | arXiv API ti: | 18 | 4 found on arXiv (Pond, Octopus, Demystifying, Mercury); rest via OpenAlex+S2 |
| 30 | global VA space GPU cluster HBM host remote SSD page table 2026 | WebSearch ext | 9 | 4 (CXL-ClusterSim, DM survey, HADES lead, AI-DC HW survey) |
| 31 | Mira SOSP'23 | WebSearch | 10 | 1 |
| 32 | FlatFlash ASPLOS'19 | WebSearch | 10 | 1 |
| 33 | 22 ACM/IEEE seed titles | OpenAlex search + S2 DOI lookup | 44 | 22 (Mira, Fastswap, Kona, TrackFM, HeMem, Colloid, TMTS, Nimble, FlatFlash, TMO, MULTI-CLOCK, DeepUM, CXL-SHM, GMT, FlashMap, 2B-SSD, Dragon, soNUMA, GAM, DiLOS, Memtis, Chrono lead) |
| 34 | programmable switch DM address translation successor to MIND 2024-25 | WebSearch | 10 | 0 (no successor found) |
| 35 | multi-GPU address translation NVLink UVM MICRO/ISCA/HPCA | WebSearch | 10 | 2 (Trans-FW, reverse address translation 2604.02473) |
| 36 | NVLink AND (address translation OR page table OR VM) | arXiv API | 2 | 1 |
| 37 | scale-up AND memory AND (pooling OR address space) | arXiv API | 4 | 0 |
| 38 | UALink | arXiv API | 8 | 1 (Jung CXL-over-XLink report) |
| 39 | multi-GPU AND (page migration OR address translation) | arXiv API | 1 | 0 |
| 40 | memory pool AND NVLink | arXiv API | 2 | 1 (DFabric) |
| 41 | au:Jung_Myoungsoo AND CXL | arXiv API | 7 | 2 (CXL-GPU, MPI-over-CXL) |
| 42 | CXL AND GPU AND (memory expansion OR unified memory) | arXiv API | 5 | 1 (CXL graph GPU) |
| 43 | unified memory AND oversubscription AND GPU | arXiv API | 5 | 2 (GB200 UM, AMD SVM) |
| 44 | HotOS 2025 accelerator disaggregated memory VM | WebSearch | 10 | 0 |
| 45 | VM for AI accelerators across HBM, CXL, SSD tiered 2025-26 | WebSearch ext | 9 | 5 (TierBPF, ITME, xTier, vmcache^n, M5) |
| 46 | HAMS ISCA'21 | WebSearch | 10 | 1 |
| 47 | 12 titles: Trans-FW, IDYLL, Griffin, GRIT, Barre Chord, GiantVM, FragVisor, Hotpot, dReDBox, SDFM, M5, Rcmp | OpenAlex + S2 DOI | 24 | 11 |
| 48 | GPU memory disaggregat/harvest AND paging/swap/VM | arXiv API | 1 | 0 |
| 49 | memory-mapped SSD/NVMe/flash AND page fault/mmap | arXiv API | 2 | 1 (UMap) |
| 50 | fabric attached memory / memory-driven computing / Gen-Z | arXiv API | 19 | 1 (FAM DRAM-cache prefetch) |
| 51 | memory hierarchy AND HBM AND flash AND GPU | arXiv API | 1 | 1 (HBF in LLM serving) |
| 52 | Huawei CloudMatrix384 UB global address space | WebSearch | 10 | 2 (CloudMatrix384 serving, xDeepServe) |
| 53 | CUDA VMM fabric handle multi-node NVLink IMEX | WebSearch | 7 | 1 (CUDA VMM docs) |
| 54 | GPU memory pooling over NVSwitch remote paging 2025-26 | WebSearch | 10 | 1 (FengHuang) |
| 55 | Linux HMM / memory tiering docs | docs.kernel.org | 4 | 1 (HMM) |
| 56 | "far memory" newest | arXiv API date-sorted | 15 | 2 (channel controllers, neural prefetch lead) |
| 57 | memory disaggregation newest | arXiv API date-sorted | 45 | 6 (federated coherence, Aquifer, EDM, DOLMA, CXL-DMSim, Callosal dup) |
| 58 | CXL AND (OS OR kernel OR page) newest | arXiv API date-sorted | 45 | 5 (DPC, Jenga, Equilibria, SHAMBLES, Clove lead) |
| 59 | DSM AND GPU; GPU AND tiered memory; GPU AND memory tiering; UM AND Grace Hopper; remote GPU memory; accelerator AND VM AND disaggregat | arXiv API | 70 | 5 (HBF tiering, BOOST, DAK, GMLake, KVDrive lead) |
| 60 | unified VA space supernode NPU HBM remote HBM DRAM SSD page table 2026 tiered paging | WebSearch ext | 9 | 3 (StrataCL, PFM NPU-PIM, vNPU) |
| 61 | OS for rack-scale GPU memory: faults/migration/eviction across HBM, peers, CXL pool, flash | WebSearch ext | 10 | 3 (Salus, Memstrata, UVM-ML) |
| 62 | 10 titles: AutoTiering, Zombieland, Hotpot, FlashVM, SSDAlloc, FastMap, Aquila, StRoM, SmartNIC far memory, The Machine | OpenAlex + usenix + S2 | 20 | 3 (AutoTiering, SSDAlloc, vmcache) + SMART lead |
| 63 | SmartNIC AND (DM/remote/far memory); memory node AND translation; remote memory AND page table; KV cache AND page table AND CXL/SSD/host | arXiv API | 39 | 0 (saturation check A) |
| 64 | 3 titles: DVM, SMART, Fluidmem + NeuMMU | OpenAlex + S2 + arXiv | 7 | 3 |
| 65 | UVM peer GPU + SSD eviction hierarchy multi-GPU | WebSearch | 10 | 2 leads (GrOUT, UVM demand-paging TACO) + Ember via OpenAlex |
| 66 | Snowball: S2 citations of MIND (123), G10 (50), GMT (16), ScalePool (1), FlatFlash (82) | Semantic Scholar | 272 | 23 (StarfishOS, UHM, CXL tensor offload, Logical pools, SDM, ARIADNE, GoFS, Spirit, Beyond-migration, REDIT, CXL0, LMB, DDC, MegaMmap, TeRM, AstriFlash, CtxMem, CMM-H, hybrid-SSD OS x2, TM-Training, CXL-SSD ATC'23, Ember) |
| 67 | programmable switch AND memory AND GPU/LLM/KV/accelerator; in-network AND (coherence/translation/DM); switch AND memory pool AND CXL/NVLink/UALink | arXiv API | 86 | 4 (CXL-pool-as-PCIe-switch, MemChannel, DM systems report, trusted DM) |
| 68 | Snowball: S2 citations of FengHuang (4), GH200 system-PT study (49), CloudMatrix384 (52) | Semantic Scholar | 105 | 5 (HyperOffload, SuperInfer, Beyond Monoliths, Beyond-HBM-wall, StrataCL dup) |
| 69 | SSD AND (memory expansion/main memory/byte-addressable) newest; supernode AND memory; NVL72 / multi-node NVLink; memory hierarchy AND KV/LLM AND CXL AND SSD | arXiv API date-sorted | 95 | 3 (ICGMM, DWDP, HyperParallel) |
| 70 | OpenAlex 2025-26 filtered: GPU memory tiering VM HBM CXL; DM OS accelerator; UVM multi-GPU remote memory | OpenAlex | 17 | 0 (saturation check) |
| 71 | OSDI/EuroSys/ASPLOS 2026 tiered/disaggregated memory GPU HBM CXL VM | WebSearch ext | 9 | 1 lead: dmemsys/awesome-disaggregated-memory list |
| 72 | awesome-disaggregated-memory README (2025-26 rows) -> usenix.org / OpenAlex + S2 | GitHub list snowball | ~60 rows scanned | 15 (LiteSwitch, Blowfish, RamRyder, MEGALON, Duhu, OneSidedMW, ODRP, SOAR/ALTO, FineMem, DSA-2LM, Scalable Far Memory, Demeter, Oasis, PIPM, PACT) |
| 73 | Unified Bus UB memory borrowing OS page table Lingqu openEuler | WebSearch + openeuler.org | 7 | 1 (UB Service Core / UBS Mem docs) |

Verification notes: arXiv and USENIX items verified by fetching arxiv.org/abs (or /html) and usenix.org presentation pages (title match). ACM/IEEE pages return 403 here, so those items were verified by Semantic Scholar DOI lookup (title + venue match) and abstract text came from OpenAlex's abstract index (used for the evidence quotes). DBLP is behind a bot wall. GitHub code URLs were checked (HTTP 200 or redirect). Final file: 197 rows, all verified.

Body-read (depth) for relevance-3 rows: MIND (ar5iv full text), LegoOS (USENIX PDF, sections 4.3-4.4), HUVM (USENIX PDF, sections 3-4), G10 (arXiv HTML), ScalePool (arXiv HTML), FengHuang (arXiv HTML), CloudMatrix384 (arXiv HTML section 4.4.1 EMS pool + xDeepServe 2508.02520 and StrataCL 2607.26444 UB-addressing sections).

### (b) Saturation per sub-area
Counts: A disaggregated-memory OS/VM 56, B tiered/CXL 57, C flash/NVM in VM 31, D global accelerator address space 53 (197 total; relevance 3: 7, relevance 2: 42, relevance 1: 148).
- A (disaggregated-memory OS/VM, in-network MM): saturated for the core. Batches 63 and 64 added 0 and 1 new relevant rows. The awesome-disaggregated-memory snowball (batch 72) then added 2025-26 long-tail rows, all relevance 1 except ODRP (2). I did not list DM data-structure, KV-store or transaction papers (Sherman, CHIME, Aceso, Deft, FORGE, ...) because they are not VM/table mechanisms.
- B (tiered memory, CXL pooling): stopped for time. The core OS page-placement line (Nimble, HeMem, AutoTiering, TPP, MULTI-CLOCK, TMTS, Memtis, MTM, Colloid, Nomad, Memstrata, M5, Chrono, SOAR/ALTO, PACT, Equilibria, Mole) is covered. Batch 72 still added 5, so the long tail of 2025-26 CXL tiering and characterization preprints is not exhausted.
- C (flash/SSD in the VM hierarchy): saturated. Batches 69 and 70 added 1 and 0. Covers FlashMap, FlatFlash, 2B-SSD, HAMS, SkyByte, ByteFS, CXL-SSD (ATC'23, Block-to-Byte), CMM-H, hybrid-SSD OS papers, vmcache/vmcache^n, MegaMmap, TeRM, AstriFlash, HBF papers, BaM/GMT/G10/Dragon.
- D (one table across accelerator and CPU/remote/flash memory): saturated at the edge. Batches 69 and 73 added 2 and 1. The FengHuang, CloudMatrix384 and GH200 citation snowball was the most productive source.

### (c) Closest 5 to the target question (one VM-style table over T0 own HBM, T1 peer HBM, T2 remote HBM via switch, T3 local DRAM, T4 remote DRAM, T5 flash)
1. **Huawei CloudMatrix384 / UnifiedBus family**: cm384_25 (2506.12708), xdeepserve25 (2508.02520), stratacl26 (2607.26444).
   - What it covers: physically all six tiers on one fabric. The HBM physical ranges of all 384 NPUs are mapped into one unified UB address space, so a local VA can be mapped to a remote NPU's HBM page (T1/T2). UB switches route NPU SDMA and load/store to any node's CPU DRAM (T3/T4) through "global unified memory addressing and routing". The EMS pool aggregates all CPU DRAM, and an SSD (EVS) tiering layer sits under it (T5).
   - What it misses: there is no single VM table and no fault-driven management across tiers. HBM is managed by the serving engine. Peer HBM is reached through explicit registrations (StrataCL "shadow virtual addressing", identical VA on all peers). The DRAM pool is indexed by a consistent-hash DHT behind a KV-store Put/Get API (MP SDK/Controller/Server). SSD tiering is internal to the MP Server. So there are three separate metadata systems, no demand paging or translation-level migration between HBM, DRAM and flash, and a KV/weight-specific design.
2. **MIND (SOSP'21)**: the direct "KV Switch holds the memory table" precedent.
   - What it covers: one global VA space shared by all processes, range-partitioned across memory blades. Translation (range entries plus TCAM outliers), <PDID,vma> protection and an MSI coherence directory (regions split down to page size) all run in the programmable switch data plane. Compute blades keep a page-fault-driven DRAM cache.
   - What it misses: CPU blades and DRAM blades only (T3/T4). No HBM, peer or remote accelerator tiers, no flash. Single rack. Switch SRAM caps it at about 45k match-action rules and 30k directory entries. No multi-tier placement or migration policy.
3. **G10 (MICRO'23)**: the closest VM-table design across HBM, DRAM and flash.
   - What it covers: one UVM page table whose leaf PTEs point to GPU HBM, host DRAM or flash (FTL mappings merged in). It has demand faults, compiler-inserted pre-evict/prefetch at 4KB pages, and direct SSD-to-GPU moves.
   - What it misses: single GPU. No T1, T2 or T4. Faults are host-mediated. Results are simulated only (UVMSmart/GPGPU-Sim); multi-GPU is future work.
4. **HUVM / memHarvester (ATC'22)**: the closest VM work that includes peer HBM.
   - What it covers: a modified NVIDIA UVM driver makes a hierarchy of own HBM, then idle peer-GPU HBM over NVLink (a victim cache for evictions), then host DRAM. It adds multi-path NVLink+PCIe prefetch and instant reclaim through background write-back.
   - What it misses: single node (no T2/T4), no flash. Peer pages are not mapped for direct access; they migrate back on fault. Per-process page tables.
5. **LegoOS (OSDI'18)**: the OS-level "one VM over disaggregated tiers" precedent.
   - What it covers: per-process global VA split into 1GB vRegions. Each vRegion is owned by a memory component chosen by a global memory manager, and memory components own the page tables. Processors keep only a virtually addressed ExCache whose misses go to the owner. Storage components back files.
   - What it misses: CPU-only (no accelerator HBM tiers). Only a cache level plus a memory level, with no multi-class tier placement or migration. No writable sharing across processes. Hardware is emulated.

Runners-up:
- FengHuang (2511.10753): xPU HBM acts as a cache over a switch-attached shared remote pool, with hardware/software "active tensor paging". No page table, no DRAM or flash tier; vision paper with simulation.
- ScalePool (2510.14580): XLink (NVLink/UALink) plus hierarchical CXL; tier-1 is accelerator memory with coherent links, tier-2 is CXL memory nodes. No translation or VM mechanisms described.
- GMT/Dragon: GPU HBM plus host DRAM plus SSD.
- FlashMap/FlatFlash: VM extended to flash.
- MegaMmap/TeRM: cluster DRAM plus SSD under one mapping.
- ODRP: memory-node NIC owns the remote-paging metadata.
- CUDA VMM fabric handles: map other nodes' HBM in an NVL72 domain into the local VA, but no faults or migration.
- Linux HMM/GMEM: OS-level shared VA covering device memory.

Bottom line: no paper or system found keeps one VM-style table (translation + faults + migration + eviction) over all six tiers. Each prior system covers at most three or four tiers under one table. The only system that physically spans all six (CloudMatrix384/UB) manages them with separate mechanisms rather than one table.

### (d) Leads not followed up (for snowball round)
- GPU UVM line:
  - Forest: Access-aware GPU UVM Management (ISCA'25, 10.1145/3695053.3731047; no abstract in index)
  - Observability-Aided GPU Memory Oversubscription (ISCA'26, 10.1109/ISCA66397.2026.00108)
  - SUV (MICRO'24, 10.1109/MICRO61859.2024.00030)
  - DREAM (ICS'25, 10.1145/3721145.3725748)
  - GrOUT (IPDPSW'24, 10.1109/ipdpsw63119.2024.00132)
  - Fine-grain analysis of UVM demand paging (TACO, 10.1145/3632953)
  - Identifying HMM data migration overhead on NVIDIA GPUs (IPDPSW'26, 10.1109/IPDPSW71298.2026.00150)
  - FRUGAL (CGO'26, 10.1109/CGO68049.2026.11395210)
  - Understanding distributed LLM training with UVM (IISWC'25)
- GPU storage:
  - Towards Scalable Storage Architectures for GPU Clusters Running LLMs (SIGMETRICS'26, 10.1145/3788106)
  - AGILE GPU-SSD (SC'25, 2504.19365)
  - AgileOS (2606.06697)
- 2025-26 OS/CXL papers from the awesome-disaggregated-memory list:
  - Tierce (SOSP'26, 10.1145/3830418.3843905)
  - Cxlalloc (ASPLOS'26, 10.1145/3779212.3790149)
  - Performance Predictability in Heterogeneous Memory (ASPLOS'26, 10.1145/3779212.3790201)
  - CXLfork (ASPLOS'25)
  - Espresso CXL JBOF (OSDI'26)
  - MAC (OSDI'26)
  - RaidenSwap and AEP (EuroSys'26)
  - Beehive and Eden (NSDI'25)
  - Adios (EuroSys'25)
  - Quicksand (NSDI'25)
  - Disaggregated Memory for File-backed Pages (TOS'25, 10.1145/3736585)
- Scale-up and supernode hardware/software:
  - Optimizing Coherent Scale-Up Networks via Dynamic Cache Bypassing (INFOCOM'26)
  - OpenSUN (DAC'26)
  - MPiCO memory-pool XPU-PIM over optical I/O (GLSVLSI'26)
  - Inter-APU communication on MI300A (MEMSYS'25, 2508.11298)
  - SuperOffload (2509.21271)
  - UBEP (2607.06202)
  - Huawei UB URMA/OBMM kernel module (atomgit openeuler)
  - SK hynix SALT-KV / CMM-Hybrid (FMS'26 news only, no paper found)
- Far-memory runtimes and prefetch: 2506.00384 (neural prefetching), 2510.04360, Proxics 2604.18120, Clove 2605.20370, 2609.02669 (DM compiler-runtime), DaeMon (SIGMETRICS'23), PULSE (ASPLOS'23/'25).
- Not found or unverifiable here:
  - Hotpot (SoCC'17)
  - Zombieland (EuroSys'18)
  - FlashVM (ATC'10; usenix page 404)
  - FireBox
  - HPE The Machine / Gen-Z, CXL 3.x GFAM spec, NVSHMEM docs: hardware/spec agent scope
- KV-specific multi-tier managers seen but left to the KV agents: KVDrive (2605.18071), OasisKV (2608.08097), TokenLake, Mooncake, NVIDIA Dynamo KVBM.

---

# Agent log: `t1_llm_kv` (round 1)

## t1_llm_kv sweep log (topic 1: LLM/DL systems managing KV/model state across tiers; GPU memory virtualization)

Started 2026-11-01 05:08 UTC. Tools: arXiv API (curl, works), WebSearch, WebFetch, GitHub raw/API via curl. Semantic Scholar API: 429 rate-limited; DBLP API: blocked by bot wall.

### (a) Query table
| # | query | source | hits scanned | new relevant |
|---|-------|--------|--------------|--------------|
| 1 | abs:"KV cache" AND abs:"memory pool" | arXiv API | 12 | 4 (MemServe, eLLM, survey, CrossPool lead) |
| 2 | abs:"KV cache" AND (tiered OR multi-tier OR "hierarchical storage") | arXiv API | 60 | 18 |
| 3 | abs:"virtual memory" AND (KV cache OR LLM) | arXiv API | 20 | 7 |
| 4 | ("page table" OR "address space" OR "demand paging") AND (KV/LLM inference/serving) | arXiv API | 13 | 3 (UlaCache, SPIN, Tiara lead) |
| 5 | (NVLink OR scale-up) AND "KV cache" | arXiv API | 26 | 3 |
| 6 | CXL AND (KV cache OR LLM) | arXiv API | 35 | 7 |
| 7 | distributed/global KV cache, KV cache pool/store | arXiv API | 25 | 3 |
| 8 | remote/peer/idle/pooled GPU memory AND LLM/KV | arXiv API | 8 | 3 |
| 9 | "KV cache" AND (SSD OR NVMe OR flash) | arXiv API | 72 | 12 |
| 10 | "KV cache" AND RDMA | arXiv API | 11 | 3 |
| 11 | disaggregated memory AND LLM/KV | arXiv API | 9 | 0 |
| 12 | title lookups for 33 seed systems (Mooncake ... CloudMatrix) | arXiv API | ~150 | 28 seeds located |
| 13 | seeds 2nd pass (CachedAttention, Aqua, Strata, TetriInfer, Prism, CloudMatrix384, kvcached, Mooncake Store, Dynamo, HiCache) | arXiv API | ~60 | 8 |
| 14 | unified virtual address space KV cache across GPU HBM, CPU DRAM, remote nodes, SSD page table | WebSearch extended | 9 | 2 (FlatCake, ICMSP lead) |
| 15 | NVL72 multi-node NVLink KV cache sharing remote GPU HBM tier | WebSearch | 10 | 3 (Harvest, SwiftCache, KVserver HotInfra'26 lead) |
| 16 | supernode Unified Bus KV cache memory pool global address | WebSearch | 10 | 2 (SK hynix H3 lead, SuperInfer) |
| 17 | Mooncake Store / Transfer Engine design docs; Dynamo kvbm-* docs; SGLang HiCache design | GitHub raw / docs | 9 docs | 3 |
| 18 | Huawei UCM / Unified Cache Manager | WebSearch + GitHub API | 7 | 1 (UCM) |
| 19 | NVIDIA ICMS / BlueField-4 context memory tier G3.5 | WebSearch + nvidianews | 10 | 1 |
| 20 | kvcached / Prism | WebSearch + USENIX OSDI'26 page | 10 | 1 |
| 21 | GitHub READMEs: UCM, 3FS, llm-d-kv-cache, NIXL, kvcached, FlexKV, InfiniStore; vLLM kv_connector/v1 + kv_offload/tiering dirs; TRT-LLM kvcache.md | GitHub raw/API | 12 docs | 8 |
| 22 | CloudMatrix384 body (EMS, UB global addressing), xDeepServe body, xLLM body | arXiv HTML (curl + grep) | 3 | 3 |
| 23 | seed abstracts batch (Pensieve, HCache, Strata, CacheGen, ServerlessLLM, Llumnix, P/D-Serve, KVShare, BanaServe, FlowKV, TetriInfer, Splitwise, DistServe) | arXiv API | 13 | 13 |
| 24 | IMPRESS FAST'25; Pensieve EuroSys'25; Jenga SOSP'25 venue checks | WebSearch | 30 | 1 (IMPRESS) |
| 25 | sub-area C seeds (FlexGen, ZeRO-*, DS-Inference, InfiniGen, PowerInfer, LLM in a flash, ShadowKV, RetrievalAttention, PQCache, MoE-Lightning, Fiddler, MemAscend, SpecOffload) | arXiv API | 16 | 14 |
| 26 | ("CUDA VMM" OR "GPU virtual memory") AND LLM; ("unified memory" OR UVM) AND KV/LLM; cross-node/rack-scale KV pool | arXiv API | 34 | 2 (mostly on-device noise) |
| 27 | KV cache AND page fault/demand paging/swap; GPU memory disaggregation pool; prefix cache global index/pool | arXiv API | 13 | 2 |
| 28 | KV pooling across GPUs rack-scale NVLink remote HBM borrowing (2026) | WebSearch extended | 9 | 3 (CrossPool, Topology-aware, OSDI'26 DirectKV) |
| 29 | global/distributed KV cache pool HBM DRAM SSD metadata index | WebSearch extended | 9 | 3 (ObjectCache, Internet-for-KV, Tutti) |
| 30 | USENIX accepted lists OSDI'26, NSDI'26, FAST'26, FAST'25, OSDI'25, ATC'25, NSDI'25 (title grep) + 11 abstract pages | usenix.org (curl) | ~700 titles | 9 (SYMPHONY, DirectKV, MoonBright, ECHO, KVCache-in-the-Wild, Bidaw, Weaver, Strata=OSDI'26, Prism=OSDI'26) |
| 31 | MLSys'26 accepted list (title grep); SOSP'25/EuroSys'26/ASPLOS'26 pages (JS-only, failed) | mlsys.org / curl | ~200 titles | 1 (SuperInfer confirmed) |
| 32 | SOSP'25 / ASPLOS'26 / EuroSys'26 KV-tier papers | WebSearch x3 | 30 | 6 (KunServe, ShadowServe, Oneiros, OasisKV, CXL-SpecKV, HybridGen) |
| 33 | follow-up abstracts: PAM, survey2, LM-CXD, SAC, Pie, FastSwitch, reverse translation in scale-up pods, CXL-over-XLink, HBF papers, Move-the-query | arXiv API | 18 | 16 |
| 34 | memory virtualization / NVSHMEM / attention offloading / live migration / heterogeneous memory hierarchy + KV | arXiv API | 13 | 3 (Lamina, Adrenaline, ...) |
| 35 | Huawei Cloud EMS / OceanStor M900 context memory over UnifiedBus | WebSearch + press release | 10 | 1 |
| 36 | GB200 NVL72 KV offload to peer GPU / KVBM remote GPU tier | WebSearch + docs.nvidia.com KVBM guide | 7 | 0 (confirms KVBM has no peer-HBM tier) |
| 37 | DWDP, SiDP, AutoUVM, Ember, HBM repartitioning, AVMP, MultiPath, Pichay, ClawVM | arXiv API | 9 | 9 |
| 38 | KVserver HotInfra'26; InfiniStore; TRT-LLM KV docs | WebSearch + GitHub | 12 | 3 |
| 39 | "KV cache" AND (TLB OR address translation OR remapping); KV memory pooling; KVCache disaggregated DRAM/SSD | arXiv API | 27 | 0 (saturation signal) |
| 40 | GPUVM (GPU-driven UVM over RDMA NIC); single global address space KV across HBM/DRAM/remote/SSD page table (2026) | WebSearch std + extended | 19 | 3 (GPUVM, HBF-Sucks, hetero-PD design space) |
| 41 | ti:"KV cache" AND ti:(offload/tier/hierarch/pool/disaggregat/storage/sharing) | arXiv API | 150 | 6 (HiSparse, EfficientAgent, TokenDance, Omni-Flow, RAG-DCache, cluster sim) |
| 42 | date-limited (2025-01..2026-11) abs: KV AND (RDMA/CXL/NVLink/remote memory/memory pool/disaggregated memory/cross-node/cluster-wide), sorted by date | arXiv API | 49 | 0 (all already seen) |
| 43 | date-limited abs: KV AND (tier/hierarchy) AND (serving/inference), sorted by date | arXiv API | 132 | 0 relevant new (compression/eviction noise) |
| 44 | Enfabrica EMFASYS; WEKA Augmented Memory Grid | WebSearch + trade press/vendor page | 17 | 2 |
| 45 | radix/prefix tree global|distributed + KV; KV + metadata service/global index/directory | arXiv API | 5 | 0 |
| 46 | Mooncake arXiv v5 body (Conductor, paged blocks, replication/swap, LRU/LFU); survey 2607.02574 body (archetypes, gaps) | arXiv HTML (curl+grep) | 2 | 0 new rows (depth) |
| 47 | provenance / side channels in globally shared KV tiers | arXiv API | 2 | 2 |

Totals (final, 05:49 UTC): 194 rows, all verified in this session (arXiv API/HTML record, USENIX page, sigops.org list, GitHub repo/raw doc, vendor page, or Semantic Scholar DOI record for 7 SIGCOMM/ISCA/ICS/TACO/POMACS/ICDCS items). Relevance: 17 x 3, 52 x 2, 125 x 1.
Per sub-area: A. 70 [10 r3, 15 r2]; B. 27 [1 r3, 12 r2]; C. 63 [0 r3, 11 r2]; D. 29 [6 r3, 13 r2]; other (surveys, agent-context VM) 5. A=multi-tier/pooled KV, B=GPU VM for LLM serving, C=offloading HBM/DRAM/SSD, D=open-source/industry/scale-up fabric.

Venue check: venues of FlexGen, ZeRO-Infinity, ZeRO-Offload, DeepSpeed-Inference, Splitwise, ShadowKV, PQCache, MoE-Lightning, Fiddler, DejaVu, RetrievalAttention, KVFlow, KunServe were confirmed via a Semantic Scholar batch lookup (05:46 UTC); CacheBlend S2 shows ACM TOCS (EuroSys version not re-checked).

### (b) Saturation per sub-area
- A. Multi-tier / pooled KV cache: SATURATED for the core vocabulary (queries 39, 42, 45 added 0). Vocabulary-variant sweeps late in the session (queries 55-59: agent/stranded/borrow/fault-tolerance/storage-engine terms) still added 2-4 rows each, mostly relevance 1, plus one relevance 3 (EMA, found via 'borrow'); the last two batches added 2 and 2 rows, so the long tail is thin but not empty.
- B. GPU virtual memory for LLM serving: SATURATED for KV-specific work (late additions: ConServe ISCA'26, vToken, KVMem, ForkKV, MOLT) (queries 26, 27, 39 added <=2 each; remaining hits are on-device/unified-memory-SoC noise). General GPU VM (UVM, GPUVM, MoonBright, Ember) only sampled; the OS/hardware agents should own that space.
- C. Offloading HBM/DRAM/SSD: NEAR-SATURATED. Seeds all found; queries 41 and 43 added 4 then 0. The long tail of sparse-attention offload papers (ScoutAttention, PulseInfer, AVSG, Fathom, IceCache, ...) was seen in query 41 and deliberately not recorded (single-GPU HBM<->DRAM sparse fetch; no tier unification).
- D. Open-source / industry: CUT BY TIME/ACCESS rather than saturated. Covered Mooncake, Dynamo KVBM + NIXL, SGLang HiCache, vLLM connectors + native tiering, LMCache, FlexKV, InfiniStore, UCM, 3FS, llm-d, AIBrix, TRT-LLM, xLLM, RTP-LLM, CloudMatrix/EMS, xDeepServe, OceanStor M900, NVIDIA ICMS, Enfabrica, WEKA. Not opened: NVIDIA ICMS developer blog (G3.5 details), Huawei Cloud EMS product docs, Alibaba PolarKV, Pliops/VAST/DDN KV products, Google GKE tiered-KV material.
- Venue lists: USENIX OSDI'26/NSDI'26/FAST'26/FAST'25/OSDI'25/ATC'25/NSDI'25, MLSys'26, SOSP'25 and SOSP'26 (sigops.org) title lists were grepped. EuroSys'26 and ASPLOS'26 pages are JavaScript-rendered and could not be parsed; only WebSearch spot checks plus Semantic Scholar citer lists cover them.

### (c) Closest 5 (to: one unified memory table over own HBM, peer HBM via scale-up link, remote-node HBM via switch, local DRAM, remote DRAM, flash, managed with VM mechanisms)
1. Mooncake (FAST'25 Best Paper) + Mooncake Store / Transfer Engine (GitHub design docs) [mooncake25, mooncake_store_te].
   Covers: T0, T3, T4, T5 as KV-holding tiers (paged KV blocks in cluster CPU DRAM and SSD; Store replicas in DRAM/CXL/local SSD/DFS); Transfer Engine names DRAM, VRAM and NVMe-oF of every node as segments (segment name + offset = a cluster-wide address) and moves data over RDMA, intra-node NVLink, multi-node NVLink (MNNVL), EFA, TCP, NVMe-oF. Master Service keeps global object-key -> replica/segment/offset metadata; LRU at high watermark, leases, soft/hard pins, multi-tenant quotas; Conductor replicates hot and swaps out cold blocks.
   Misses: key-value object semantics rather than a page table (no logical KV address space that kernels translate through); peer and remote HBM are only transfer paths, never a managed cache tier; no demand faults (data is explicitly fetched into local HBM before attention); master metadata is not consulted on the GPU data path; SSD tiering is a separate subsystem (LOCAL_DISK/DFS models with their own rules), so no single policy spans all six tiers.
2. Huawei CloudMatrix384: CloudMatrix-Infer EMS caching cluster [cloudmatrix25] + xDeepServe [xdeepserve25] + OceanStor M900 [oceanstor_m900_26].
   Covers: hardware side is the nearest physical match to the user's KV Link / KV Switch picture. The UB fabric (on-board L1 UB switches, rack-level L2 UB switches) gives supernode-wide "global unified memory addressing and routing" and, per xDeepServe, "a global shared memory address space spanning all CPU DRAM and NPU on-chip memory" (T0-T4 addressable). EMS pools the CPU DRAM of all 48 nodes for KV/model caching: MP Controller holds a consistent-hashing DHT view (key -> MP Server -> DRAM address), NPUs SDMA directly into remote DRAM, LRU demotion DRAM -> EVS SSD (T5), namespaces for isolation. M900 (2026) extends the global multi-tier KV pool to PB-scale SSD over UB.
   Misses: the KV pool is DRAM+SSD only; NPU HBM (own, peer, remote) is not a KV tier (HBM-to-HBM UB access is used for EP/P-D communication via XCCL); the index is a DHT over keys, not a translation table; no faults or transparent load/store of KV by attention kernels; the hardware global address space and the software KV pool are two separate layers with no single table spanning both.
3. NVIDIA Dynamo KVBM + NIXL [dynamo_kvbm, nixl] (with KVBM-like peers LMCache [lmcache25] and SGLang HiCache [sglang_hicache]).
   Covers: explicit tier model G1 GPU HBM, G2 pinned DRAM, G3 NVMe, G4 object store, described as "a unified memory layer and write-through cache"; logical block lifecycle (mutable -> registered immutable -> weak) in a block registry keyed by sequence hash with LRU/TinyLFU; logical tier handles resolved to worker-specific physical layouts (a translation step); remote G2 -> local G2 RDMA pull; NIXL abstracts GPU/CPU memory and file/block/object storage behind one descriptor API. LMCache adds a controller with a cluster-wide view (lookup/move/clear/pin/compress over GPU, CPU, disk, remote). HiCache's HiRadixTree maps prefixes to exact L1/L2 locations but deliberately does not track L3 metadata.
   Misses: no peer-HBM (T1) or remote-HBM (T2) tier; per-instance leader rather than one cluster table; NVLink/MNNVL used only for P/D transfer; mapping is a content-hash registry, not virtual addresses; no fault-driven fetch.
4. UlaCache (arXiv 2610.29820, Oct 2026) [ulacache26], with EMA (arXiv 2609.27040, Sep 2026) [ema26] and Harvest (arXiv 2602.00328) [harvest26] as companions.
   Covers: the most VM-like design found. One logical KV address space over the local GPU pool, the TP group's peer-GPU pools (CUDA IPC, i.e., NVLink in practice) and an optional CPU pool. An address mapping translates each logical KV address to its current GPU or CPU slot and is rewritten after migrations, so the engine's logical references stay stable. Parallel bucketed LRU eviction; missing selected entries are promoted or fetched on demand. Harvest similarly keeps a "unified KV block table" mapping block IDs to local HBM / peer GPU / host DRAM, with revocation-driven invalidation when a peer reclaims memory.
   EMA (Xu, Xia, Stoica) lets GPUs in a server borrow and reclaim each other's HBM over NVLink as an elastic pool; memory-slice processes do address translation and prefetch so borrowed (remote) slices perform like local ones, and lenders can always reclaim by preempting logically owned regions (logical-to-physical layer mapping).
   Misses: scope is one server or TP/DP group (no T2 remote-node HBM, no T4 remote DRAM, no T5 flash; EMA and Harvest name NVL72 only as future scale); demand fetch is software-scheduled (prefetch or per-step promotion), not hardware page faults; no cluster-wide or persistent table.
5. MemServe MemPool (arXiv 2406.17565) [memserve24], with Infinite-LLM (arXiv 2401.02669) [infinitellm24].
   Covers: an elastic memory pool over HBM and DRAM of all serving instances with alloc/free, index (insert/match), swap_in/swap_out (HBM<->DRAM) and transfer APIs; addresses encode the owning instance ID; global prompt trees track which instance holds each prefix. Infinite-LLM pools GPU HBM across a 32-GPU cluster (NVLink intra-node, network inter-node); a central gManager keeps a request-placement map, rManagers let debtor instances borrow and reclaim creditors' HBM, and DistAttention computes on remote KV in place.
   Misses: no flash tier; global prompt trees are best-effort and can go stale (TTL); Infinite-LLM's map is per request/instance, not per block; neither provides page-table translation, faults or unified eviction across tiers.
Runners-up: Beluga (SIGMOD'26) and TraCT (CXL switch pools mapped as one symmetric address space with a global block index/hash table; T0+T4 only); xLLM (xTensor on-demand page mapping in HBM plus HBM-DRAM-SSD and Mooncake-based global KV, but two separate layers); "Predictive Multi-Tier Memory Management for KV Cache" (arXiv 2604.26968: six tiers HBM/DRAM/CXL/NVMe-GDS/RDMA fabric/parallel FS with predictive promotion, but no global mapping, no peer HBM, and mostly analytical); Aqua (ASPLOS'25: peer-HBM over NVLink/NVSwitch as offload tier with coordinator); AutoUVM/GPUVM (real hardware demand paging HBM<->host, GPUVM via RDMA NIC, but single GPU and not KV-aware).
Gap statement supported by the survey arXiv 2607.02574: its five archetypes (local-paged, disaggregated-pipeline, shared-store, memory-pool, hybrid-tier) never combine pooled load/store memory with distributed prefix directories, and it explicitly lists that combination ("CXL-backed prefix cache with cross-session reuse ... explicit ownership semantics") and durable KV with recovery as open gaps. No paper found manages all six tiers T0-T5 under one table with VM mechanisms.

### (d) Leads not followed (for snowball)
- Alibaba "PolarKV" cloud-native distributed KV cache pool (2026; only a ResearchGate-style listing seen, no arXiv).
- NVIDIA ICMS / "G3.5" technical blog (developer.nvidia.com/blog/?p=111143) and Dynamo KV router global radix index docs.
- Huawei Cloud EMS product documentation; Huawei UB-Mesh / UnifiedBus spec (for the hardware agent); SK hynix H3 (HBM+HBF unified address space) and MARS memory-centric rack (OCP 2026).
- vLLM connector "umbp" (UMBPStoreConnector; origin unclear, possibly AMD MORI unified memory buffer pool) and "moriio".
- EuroSys'26 and ASPLOS'26 full accepted lists (JS pages); MLSys'26 "OPKV", "MorphServe".
- OS/hardware-side items seen in venue lists (likely other agents' scope): RamRyder, Blowfish, MEGALON, OBASE, Osprey (OSDI'26); Octopus CXL pods, CSFQ transport for switched CXL pooling, OneSidedMW, PD3 (NSDI'26); DRack, DSA-2LM (ATC'25); EMT, Tiered Memory Management Beyond Hotness, Tigon, FineMem (OSDI'25); PAGE404 (arXiv 2610.20962, GH200 hardware page migration); "Exploring CXL-based KV Cache Storage for LLM Serving" (NeurIPS'24 workshop).
- FAST'26: CacheSlide, SolidAttention, "Accelerating Model Loading by Programmable Page Cache"; NSDI'26: DroidSpeak, Agentix, Cortex.
- "Towards Distributed Inference of LLMs on a P2P Network" (arXiv 2606.17059); gKSM GPU memory dedup (2610.25219); Trinity (Liu and Qian 2025).
- Citation snowball: first 1000 Semantic Scholar citers of Mooncake, MemServe, Infinite-LLM, Aqua, Beluga, TraCT, LMCache, Harvest, vAttention were filtered (see addendum); Mooncake has more than 1000 citers (offset 1000+ not pulled); UlaCache, EMA, CloudMatrix384 and PagedAttention citers not pulled.

### Addendum (05:40-05:48 UTC): snowball and late venue checks
| # | query | source | hits scanned | new relevant |
|---|-------|--------|--------------|--------------|
| 48 | citations of MemServe, vAttention (first pass) | Semantic Scholar API | 311 | 5 (vToken, Dynamo fast recovery, dual pools, H2M2, KVCodec) + leads |
| 49 | Continuum / Akashic / SYMPHONY arXiv | arXiv API | 3 | 1 |
| 50 | SOSP'25 accepted list (sigops.org) | curl | ~70 titles | 3 (Mercury, Aegaeon, DiffKV) |
| 51 | citations of Mooncake, Infinite-LLM, Aqua, Beluga, TraCT, LMCache, Harvest (first 1000 each) | Semantic Scholar API | ~3000 | 9 (DynamoServe SIGCOMM'26, ConServe ISCA'26, TurboBus SIGCOMM'26, MCaM ICDCS'25, NovaServe ICS'26, AdaptiveKV TACO, POMACS storage, HyperOffload, SGLANG-LSM) |
| 52 | SOSP'26 accepted list (sigops.org) | curl | ~60 titles | 0 KV-tier papers (leads: Anchor decoupled GPU memory, Janus multi-LLM serving, StarfishOS SSI on CXL, Tierce) |
| 53 | DOI lookups for S2-only items | Semantic Scholar paper API | 7 | 7 verified |
| 54 | PTStore (Euro-Par'26), KV offload bottleneck analysis | arXiv API | 2 | 2 |
Snowball conclusion: citations of the five closest systems surface more peer-HBM work (DynamoServe uses stranded GPU memory of other GPUs for weights and KV; ConServe attacks GPU translation locality for paged KV; HyperOffload targets supernode shared memory pools) but still no design that puts all six tiers under one table.
Additional runners-up from the snowball (abstract-level only): DynamoServe (SIGCOMM'26 workshop, DOI 10.1145/3789240.3829347) uses stranded HBM of other GPUs as a tier for weights and KV with demand-driven migration; full text not read, so it is not ranked in the Closest 5. ConServe (ISCA'26) is the only paper found that measures GPU address-translation cost (TLB locality) for paged KV, relevant if the unified table is enforced by GPU page tables.

### (d) addendum: more leads
- User terminology check: arXiv searches for "KV Switch"/"KVSwitch"/"KV link" return only spectral-CT papers and KVLink (arXiv 2502.16002, a KV-reuse technique, name collision). No paper uses the user's names for the scale-up link/switch.
- SOSP'26 titles worth a look (not opened): Anchor (decoupled GPU memory against shallow disruptions), Janus (multi-LLM serving at production scale), StarfishOS (single system image on CXL), Tierce (observability-driven tiered memory), Borges (shared log on CXL memory/SSD hybrid).
- ISCA'26 SHyLA (3D-stacked NVM-DRAM hybrid LLM inference) and ASPLOS'26 REPA (reconfigurable PIM for KV offloading) seen only as S2 titles.
- Semantic Scholar notes MemServe, Infinite-LLM, TokenLake, KVDirect, vTensor, Pie, Adrenaline as arXiv-only (no venue) as of today.
| 55 | date-limited abs: GPU memory AND LLM AND (elastic/pool/virtualization) AND serving/inference | arXiv API (sorted by date) | 23 | 4 (KVMem, BOOST, HBM/HBF hot-cold, agentic architecture) |
| 56 | date-limited (agent OR agentic) AND KV AND (offload/tier/host memory/SSD/pool/storage); HBM AND DRAM AND KV AND (unified/interleave/concurrent/address) | arXiv API (sorted by date) | 80 | 4 (TensorCast, TokenCake, Cache Engineering, CacheFlow) |
| 57 | date-limited KV AND (fault tolerance/recovery/durable/persistent/replication); (idle/stranded/spare/unused GPU memory OR borrow) AND LLM/KV | arXiv API (sorted by date) | 257 | 3 (EMA r3, LUMEN, stateful multi-agent) |
| 58 | date-limited GPU memory sharing/borrowing/lending; rack-scale/NVL72/GB200/SuperPoD/SuperNode AND memory AND LLM | arXiv API (sorted by date) | 38 | 2 (C2CServe, MOLT) |
| 59 | date-limited KV AND (memory manager/storage engine/object store) AND (cluster/distributed/disaggregated/multi-node) | arXiv API (sorted by date) | 12 | 2 (ForkKV, RetroInfer); saturation reached for A/B |
| 60 | Semantic Scholar citers of CloudMatrix384, xDeepServe, Pie | Semantic Scholar API | ~200 | 0 (saturation) |

---

# Agent log: `t1_snowball` (round 2)

## t1_snowball log (round 2, topic 1: one unified memory table over T0-T5)

Start 2026-11-01 05:53 UTC. Rows written: 11 (relevance 2: 4, relevance 1: 7; 10 verified, 1 unverified).

### IMPORTANT: run cut short by a tool block

About 7-10 minutes into the run (around 06:00-06:05 UTC), the auto-mode safety classifier began refusing every network-capable call: Bash (even `date -u`), WebSearch and WebFetch. Each refusal said "blocked because of earlier conversation content ... it will keep firing for the rest of this conversation", and said to tell the user and suggest running outside auto mode or in a fresh session. I did not try to get around the block, for example by handing the same fetches to a subagent. Only Read and Write still worked. So:
- Everything below comes from calls made before the block: one Semantic Scholar citation batch, one arXiv API recency batch, 4 web searches, 2 web fetches and one PDF (UB Service Core reference design, parsed locally). It also uses the round-1 records already on disk.
- Tasks 1, 3, 4 and 6 are mostly NOT done. Task 2 is partly done. Task 5 is one batch only.
- Recommendation: rerun this brief in a fresh session, or with the default permission mode. Start with the "Still unchecked" list at the end.

### (a) Query table

| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | Forward citations: UlaCache 2610.29820 (0), EMA 2609.27040 (0), sixtier 2604.26968 (2), HyperOffload 2602.00748 (0), StrataCL 2607.26444 (1), UB-Mesh 2503.20377 (28), Co-Fabric 2609.25560 (0), xDeepServe 2508.02520 (4), vAttention 2405.04437 (176, already scanned in round 1), Harvest 2602.00328 (3), Mooncake offset 1000+ (returned 0 rows, so the call failed or was rate-limited) | Semantic Scholar API | 214 | 0. UB-Mesh citers are networking/topology papers; xDeepServe citers are AFD/EP-scaling/offload-training papers; sixtier citers are Unified AI Gateway 2609.06940 and C2P-Cache 2608.00412; StrataCL citer is Tessera 2604.10180; Harvest citers are SMetric and EMA (known) |
| 2 | 12 date-limited queries (submittedDate 2026-08-01..11-02, sorted by date): "unified address space" AND HBM/GPU/NPU; "page table" AND GPU/NPU; supernode/superpod AND memory; "scale-up" AND memory pool; "KV cache" AND (virtual memory/page table/address space/address translation); memory tiering AND GPU AND SSD/flash; CXL/UALink/NVLink AND "KV cache"; global address space/disaggregated memory AND GPU/LLM; HBF AND HBM/LLM; UVM/demand paging AND GPU; KV AND tier AND SSD/pool; memory pool AND GPU/HBM/LLM | arXiv API | 73 | 9 (SPLASH, Characterizing HBF, HBFSim-PTX, FLINT, HBF exploration, HBF for GR, Gandalf, Elastic KV, KV memory-wall SoK). No match for "unified address space" or "page table" beyond known items |
| 3 | openEuler SuperPoD OS UB Service Core KV cache HBM DRAM pool SSD global index | WebSearch (extended) | 10 | 1. The UB Service Core SW Arch Reference Design 2.0 PDF was fetched and parsed |
| 4 | UnifiedBus 2.0 spec memory addressing UMMU URMA UMDK | WebSearch (extended) | 10 | 0 (secondary only; UMMU and libummu named, spec PDF not reached) |
| 5 | NVIDIA ICMS / BlueField-4 / G3.5 / Dynamo | WebSearch | 7 | 0 (vendor and partner pages only; ICMS is already known; no NVIDIA page uses the name "G3.5") |
| 6 | SK hynix H3 HBM+HBF unified address space | WebSearch | 10 | 1 (H3, IEEE CAL, DOI 10.1109/LCA.2026.3660969; canonical page not fetched, verified=false) |
| 7 | unifiedbus.com UB OS Component page | WebFetch | 1 | 0 (page came back truncated) |
| 8 | itsfoss OSS-Prague openEuler report | WebFetch | 1 | 0 (secondary; source of the "four-tier KV + global index" claim) |
| 9 | BLOCKED: UALink 2.0 / Link MMU; NVIDIA ICMS developer blog; Samsung CMM-D CXL-switch KV white paper; Mooncake UB transport; SPLASH abs page; all later Bash/curl | WebSearch/WebFetch/Bash | 0 | n/a |

### Task 1: primary docs (only openEuler checked)

openEuler / UnifiedBus "UB Service Core Software Architecture Reference Design", Revision 2.0, released 2025-12-31 (row ubsc_rd2_25). This is the primary document behind the "SuperPoD OS" press and talk reports.
- MemFabric (in UBS Mem) gives unified virtual addressing of host memory across hosts: "MemFabric: Provides unified memory addressing and data access across distributed systems. It implements unified virtual addressing of memory across hosts based on the UB and RoCE network technology. It implements unified data access based on xDMA technology."
- SHMEM: "maintains cross-node cache coherence using UBPU-to-UBPU (such as CPU and NPU) shared memory with supporting software". This is a coherence claim that covers NPU memory.
- MemCache is a separate KV-cache store with weak consistency: "supports weakly-consistent memory data caching, and satisfies the high-throughput demands such as KV cache in foundation model inference."
- UBS IO is the HBM-to-flash path, also separate: "NPU Direct SSU/SSD ... accelerating data transmission between the high-bandwidth memory (HBM) and SSUs/SSDs"; "HBM Direct: Implements passthrough access from the HBM to the storage media (SSUs/SSDs) or storage systems (such as OceanDisk)"; "Application-aware acceleration: Identifies the data access pattern of applications and prefetches or evicts data based on the pattern"; "By offloading data from HBM to local or remote storage systems, UBS IO enhances access bandwidth to the KV cache."
- UBS Engine handles memory borrowing for VMs: "migrates cold data from local VMs to memory segments on remote nodes". The only "page table" in the document is UBS Virt's "Page Table Management (KVM-page table management module): Manages GPA/HVA", which is VM virtualization, not KV.
- Verdict: these are KV and I/O caching services plus host-memory pooling, delivered as four separate components (MemFabric addressing, MemCache, UBS IO, UBS Engine). There is no single page table or translation layer spanning HBM, the DRAM pool and SSD. HBM is never named as part of MemFabric's address space. The "four-tier KV (HBM, local DRAM, UB-shared DRAM pool, SSD/object) + global index" claim appears only in secondary write-ups of the OSS Europe 2026 keynote (itsfoss). The primary reference design does not describe a tiered global KV index. This does not come closer than CloudMatrix384 EMS.
- Not reached: UB Base Specification 2.0 memory/UMMU chapters, OBMM, UALink 1.0 full spec, UALink 2.0 and the white paper, NVIDIA ICMS tech blog and Dynamo KV router, SK hynix H3 full text and MARS, Samsung CMM-D white paper, Enfabrica EMFASYS tiering doc, Mooncake UB transport, Vera Rubin / NVLink Fusion memory model.

### Adversarial check (partial: from round-1 records plus the UBSC primary doc; full texts NOT re-fetched)

Because of the block I could not re-read the full texts. The checks below compare round-1 rows against each other and against round-1 evidence quotes. They are disagreements to resolve in a rerun, not new verifications.

1. xDeepServe (2508.02520). The round-1 row (r1/t1_fabric_gpu.jsonl, xdeepserve_25) sets `unified_table: "yes"`. The same agent's log (Closest-5 #1) says "per-device page tables, not one shared table; mappings are explicit/registration-based". The evidence quote supports one physical address space ("a global shared memory address space spanning all CPU DRAM and NPU on-chip memory"), not one table. Recommend `partial`. StrataCL's identical-VA-on-all-peers scheme ("shadow virtual addressing") is a registration convention, not a global page table.
2. CloudMatrix384 (2506.12708). The row sets T1 and T2 to `yes` and unified_table to `partial`. The llm_kv log says the EMS KV pool is DRAM+SSD only and that "NPU HBM (own, peer, remote) is not a KV tier". So T1/T2 are reachable but not managed tiers. Under the target definition (tiers controlled by the one table), T0-T2 should be `partial`. unified_table should be `no`: the UB physical addressing and the EMS consistent-hash DHT are two separate layers. Keep `partial` only if physical addressability counts.
3. G10 (2310.09443). Round 1 contradicts itself. The fabric_gpu log credits "multi-GPU sharing of the flash space" and T1 partial. The os_disagg log says "single GPU ... multi-GPU is future work". Unresolved here. Either way, no peer-HBM PTEs are described, so T1 should be `no` unless the paper's multi-GPU discussion shows them.
4. Mooncake / Mooncake Store. Both rows list `translation` among the VM mechanisms. The Master Service maps object keys to replica segment+offset, which is a lookup index the engine queries before an explicit transfer. Kernels never translate through it. Recommend relabelling it "key->location index" rather than translation.
5. NVIDIA MNNVL + EGM. The fabric_gpu Closest-5 #3 credits T3 and T4 as "local and remote Grace LPDDR via EGM" under the same fabric VA. I could not re-check whether remote-node EGM memory can be imported through IMEX fabric handles across an NVLink-Switch domain. Flag for verification; do not cite T4 as confirmed.
6. openEuler UB Service Core / SuperPoD OS. Round 1 said "mechanisms undocumented". The primary doc (above) now documents them as separate services with no single table. This agrees with round 1's ranking (runner-up only).
7. MIND, UlaCache, HUVM, EMA. No internal disagreement found in the round-1 records. Not re-fetched.

### Did anything come closer than round 1's closest?

No. The new HBF items (H3, SPLASH) put HBM and on-package flash under one address space or "one logical memory" on a single accelerator. That is T0 plus a flash tier the six-tier model does not name: flash next to HBM rather than behind the host. They cover fewer tiers than G10 and far fewer than CloudMatrix384. UB Service Core confirms Huawei's software stack keeps addressing, KV caching and HBM-SSD I/O as separate components.

### (b) Saturation verdict per sub-area

| Sub-area | status |
|---|---|
| Task 1 primary vendor docs | NOT saturated. Cut by the tool block after 1 of ~10 targets (openEuler UBSC). |
| Task 2 forward citations | Partly. Round 1 already pulled MIND, G10, GMT, ScalePool, FlatFlash, FengHuang, GH200, CM384, UB-Mesh, xDeepServe, Mooncake (first 1000), MemServe, vAttention, Infinite-LLM, Aqua, Beluga, TraCT, LMCache and Harvest. Round 2 added UlaCache, EMA, HyperOffload and Co-Fabric (0 citers yet), 2604.26968, StrataCL, UB-Mesh, xDeepServe and Harvest: 0 new relevant. Saturated for what was pulled. HUVM citers and Mooncake citers past 1000 were not pulled. |
| Task 3 leads | Mostly already in the round-1 known list: Forest, Observability-Aided, SUV, DREAM, FRUGAL, HMM overhead, UVM distributed training, StarfishOS, PAGE404, ConServe, DynamoServe, Towards Scalable Storage, AGILE, OpenSUN, Topology-Aware NPU Virtualization, Tutti, KVDrive, OasisKV, several HBF preprints, ICMS, EMFASYS, UCM. The rest were not checked (list below). |
| Task 4 venue sweep | Not done. |
| Task 5 recency (arXiv Aug-Nov 2026) | One batch of 12 queries, 9 new relevant (6 of them HBF). Not formally saturated, since a second batch was impossible. No six-tier or global-table candidate appeared, and the address-space and page-table queries returned only known items. |
| Task 6 adversarial | Partial (record-level only; see above). |

### Still unchecked (for a rerun)

- Specs and docs: UB Base Specification 2.0 (memory, UMMU, token/permission chapters); OBMM and libummu docs; UALink 200G 1.0 full spec, UALink 2.0 and the Jan-2026 white paper (57-bit fabric address space); NVIDIA ICMS developer blog and Dynamo KV router global index; SK hynix H3 (IEEE CAL, DOI 10.1109/LCA.2026.3660969) full text and the MARS rack; Samsung CMM-D CXL-switch KV-offload white paper; Enfabrica EMFASYS tiering (MUG'25 tutorial); Mooncake Kunpeng UB transport doc; Vera Rubin NVL144 / NVLink Fusion memory model; openYuanrong datasystem (pypi `openyuanrong-datasystem`, Huawei, seen only as a search hit, possibly a multi-tier HBM/DRAM/SSD KV/object store).
- Leads: Tierce (SOSP'26), Anchor (SOSP'26), Cxlalloc (ASPLOS'26), GrOUT (IPDPSW'24), AgileOS (2606.06697), DxPU (2310.04648), PolarKV (Alibaba), MemNetAI'26 papers, PMKS (EEI 2026), MPiCO (GLSVLSI'26), DeepPiC (ASP-DAC'26), Phantom Walk (DAC'26, DOI 10.1145/3770743.3804244; still title-only).
- Citations: HUVM (ATC'22) citers; Mooncake citers at offset 1000 and beyond.
- Venues: OSDI'26, SOSP'26 (beyond the round-1 skim), NSDI'26, EuroSys'26, ASPLOS'26, ISCA'26, MICRO'25, HPCA'26, FAST'26, ATC'25/26, MLSys'26, SC'25, HotOS'25, HotStorage'26.
- Adversarial full-text checks: xDeepServe and StrataCL (where translation happens: NPU MMU vs UB switch decode); CM384 EMS section; CUDA fabric-handle + EGM docs (remote EGM import); G10 Section on multi-GPU; MIND; UlaCache.

---

# Agent log: `t2_aiops_rca` (round 1)

## t2_aiops_rca log (topic 2, diagnosis half: autonomic/self-adaptive, LLM AIOps/RCA, LLM tuning from metrics, benchmarks)

Sweep window: 2026-11-01 05:08-05:36 UTC. Output: /tmp/claude-1008/-data-fritz-learn-claude-code/5c29cbb6-c91f-4529-869e-b5048010cbee/scratchpad/lit/r1/t2_aiops_rca.jsonl (202 rows: A 27, B 116, C 38, D 21; relevance 3 = 8, 2 = 66, 1 = 128). All rows verified by fetching arXiv abs pages (curl), DOI metadata (Crossref), or official venue/author pages; no unverified rows.

### (a) Queries

| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | "root cause" AND LLM-terms AND (cloud OR microservice OR incident) | arXiv API (relevance sort) | 85 | 39 |
| 2 | "self-adaptive" AND (LLM OR GenAI) | arXiv API (relevance sort) | 93 | 12 |
| 3 | MAPE-K AND (LLM OR "language model" OR agent) | arXiv API (relevance sort) | 22 | 2 |
| 4 | (AIOps OR SRE OR "site reliability") AND (LLM OR agent) | arXiv API (relevance sort) | 73 | 22 |
| 5 | LLM AND (diagnosis OR "root cause") AND ("performance degradation" OR "performance issues" OR slowdown) | arXiv API (relevance sort) | 48 | 6 |
| 6 | LLM AND (GPU OR training) AND ("failure diagnosis" OR straggler OR "root cause") AND (cluster OR job) | arXiv API (relevance sort) | 23 | 4 |
| 7 | LLM AND ("self-healing" OR autonomic) | arXiv API (relevance sort) | 100 | 6 |
| 8 | LLM AND ("knob tuning" OR "configuration tuning" OR "auto-tuning") AND (database OR system OR inference OR serving) | arXiv API (relevance sort) | 73 | 3 |
| 9 | LLM AND (autoscaling OR "resource management" OR Kubernetes) AND agent | arXiv API (relevance sort) | 77 | 7 |
| 10 | LLM AND network AND (troubleshooting OR incident OR "fault localization") | arXiv API (relevance sort) | 76 | 0 |
| 11 | title seeds: SCOOT, GPTuner, ELMo-Tune, D-Bot, RCAEval, OpenRCA, KnowledgeMind, Panda | arXiv API (relevance sort) | 16 | 6 |
| 12 | LLM AND (tracing OR traces) AND (anomaly OR diagnosis OR "root cause") AND (microservice OR cloud) | arXiv API (relevance sort) | 23 | 0 |
| 13 | LLM AND ("performance regression" OR "performance bug" OR "performance engineering") | arXiv API (relevance sort) | 46 | 4 |
| 14 | LLM AND "congestion control" | arXiv API (relevance sort) | 16 | 3 |
| 15 | ("LLM serving" OR "LLM inference" OR vLLM) AND (anomaly OR diagnosis OR observability OR "root cause" OR incident) | arXiv API (relevance sort) | 100 | 10 |
| 16 | LLM AND ("edge-cloud" OR "edge server") AND (monitoring OR telemetry OR diagnosis) | arXiv API (relevance sort) | 5 | 0 |
| 17 | LLM AND ("self-adaptation" OR "runtime adaptation") AND (software OR systems) | arXiv API (relevance sort) | 80 | 1 |
| 18 | LLM AND (observability OR telemetry) AND agent AND (performance OR latency OR SLO) | arXiv API (relevance sort) | 100 | 1 |
| 19 | LLM AND (mitigation OR remediation) AND (incident OR outage) | arXiv API (relevance sort) | 80 | 7 |
| 20 | LLM AND ("troubleshooting guide" OR runbook OR SOP) | arXiv API (relevance sort) | 55 | 1 |
| 21 | agent AND ("performance diagnosis" OR bottleneck) AND (GPU OR cluster OR HPC OR profiling) AND LLM | arXiv API (relevance sort) | 80 | 2 |
| 22 | LLM AND ("log analysis" OR "anomaly detection") AND logs AND (agent OR interpretable OR root) | arXiv API (relevance sort) | 53 | 0 |
| 23 | title seeds: lambda-Tune, Panda, NetAssistant, KnowledgeMind, Minder, ByteRobust, Nezha | arXiv API (relevance sort) | 40 | 3 |
| 24 | LLM AND ("SLO violation" OR "tail latency") AND (diagnosis OR agent OR mitigation) | arXiv API (relevance sort) | 19 | 0 |
| 25 | LLM AND database AND (diagnosis OR "root cause" OR anomaly) AND (agent OR DBA) | arXiv API (relevance sort) | 30 | 2 |
| 26 | LLM AND ("agentic workflows" OR "multi-agent systems") AND ("root cause" OR "failure attribution") | arXiv API (relevance sort) | 50 | 5 |
| 27 | LLM AND ("serving policies" OR "scheduling policy" OR heuristics) AND (evolve OR synthesis) AND system | arXiv API (relevance sort) | 60 | 2 |
| 28 | (harness OR scaffold) AND (telemetry OR congestion OR backpressure OR "server load" OR "KV cache") AND agent | arXiv API (relevance sort) | 29 | 4 |
| 29 | LLM AND ("GPU cluster" OR "AI infrastructure" OR "inference cluster") AND agent AND (operations OR diagnosis OR incident) | arXiv API (relevance sort) | 6 | 0 |
| 30 | LLM AND ("performance anomaly" OR "performance interference" OR "resource contention") AND (diagnose OR explain OR mitigate) | arXiv API (relevance sort) | 3 | 0 |
| 31 | ("self-driving" OR "self-managing" OR "self-optimizing") AND LLM AND (cloud OR database OR network OR system) | arXiv API (relevance sort) | 60 | 0 |
| 32 | LLM AND ("network troubleshooting" OR "network incident" OR "network operations") AND (agent OR diagnosis) | arXiv API (relevance sort) | 39 | 2 |
| 33 | LLM AND ("operational knowledge" OR playbook OR "incident memory") AND (incident OR SRE OR operations) | arXiv API (relevance sort) | 50 | 3 |
| 34 | LLM AND ("self-adaptive" OR "self-adaptation") AND (planner OR planning OR MAPE OR "feedback loop") | arXiv API (relevance sort) | 16 | 0 |
| 35 | title/abs seeds: Glia, Barbarians at the Gate, ADRS, AlphaEvolve systems | arXiv API (relevance sort) | 40 | 2 |
| 36 | LLM AND (online OR runtime) AND ("policy evolution" OR "self-evolving system" OR "living code") | arXiv API (relevance sort) | 24 | 1 |
| 37 | LLM AND agent AND ("LLM serving") AND ("automated systems design" OR "AI-driven research") | arXiv API (relevance sort) | 1 | 0 |
| 38 | LLM AND ("root cause" OR diagnosis) AND (serving OR inference) AND (latency OR throughput) AND agent | arXiv API (relevance sort) | 25 | 3 |
| 39 | LLM AND ("cloud-edge" OR "edge-cloud" OR "edge computing") AND ("fault diagnosis" OR "anomaly detection" OR "self-healing") | arXiv API (relevance sort) | 8 | 0 |
| 40 | ("fault injection" OR chaos) AND ("LLM inference" OR "LLM serving" OR vLLM) | arXiv API (relevance sort) | 11 | 0 |
| 41 | LLM AND (I/O OR HPC) AND ("performance diagnosis" OR bottleneck OR Darshan) | arXiv API (relevance sort) | 40 | 2 |
| 42 | LLM AND database AND ("performance debugging" OR "slow query") | arXiv API (relevance sort) | 3 | 0 |
| 43 | LLM AND ("log anomaly") AND (interpretable OR prompt OR agent) | arXiv API (relevance sort) | 11 | 0 |
| 44 | OpenRCA ICLR 2025 openreview | WebSearch + iclr.cc fetch (OpenReview blocked by browser check) | 10 | 1 |
| 45 | NetAssistant dialogue-based network diagnosis NSDI | WebSearch + usenix.org fetch | 10 | 1 |
| 46 | Panda performance debugging databases LLM agents CIDR 2024 | WebSearch + vldb.org fetch | 10 | 1 |
| 47 | KnowledgeMind RCA MCTS | WebSearch + arXiv abs | 10 | 1 |
| 48 | Kephart-Chess / Rainbow / models@run.time / SEfSAS roadmap DOIs | Crossref API (S2 abstracts elided) | 4 | 4 |
| 49 | citations of 2604.07144, 2509.01245, 2407.12165, 2501.06706 | Semantic Scholar citations API | ~260 | 14 |
| 50 | citations of 2610.14547, 2506.02009, 2305.15778, 2610.06597, 2608.25661 | Semantic Scholar citations API | ~120 | 6 |
| 51 | FunPilot / SOPSmith / RunbookFX / Agent-based O&M for AI data centers | WebSearch (+ author PDF for FunPilot) | 40 | 3 (FunPilot, TSGuard, AutoTSG) |
| 52 | LLM agent analyzes serving telemetry root cause ... generates tools/policies for agent harness under load | WebSearch extended | 9 | 4 (HarnessFix, agent runtime layer, AgentChaosBench, INFRAMIND) |
| 53 | self-evolving harness congestion-aware inference server load tool generation | WebSearch | 10 | 0 new (CONCUR, E3-Agent already in) |
| 54 | NSDI/EuroSys/OSDI 2026 LLM agent RCA; SoCC 2025 LLM agent performance diagnosis | WebSearch x2 | 20 | 3 (TelemetrySuffBench, Adobe triage, APSys'25 Promise-and-Pain) |
| 55 | Glia systems design; recuse/in-band signals; learning to wait | arXiv API + abs fetch | 8 | 3 |
| 56 | venue checks | Semantic Scholar batch API (DBLP blocked by Anubis; S2 rate-limited after first batch) | 20 | 0 (9 venues corrected) |

### (b) Saturation per sub-area

- A (autonomic / self-adaptive + LLM in MAPE-K, self-healing): saturated. Queries 31 and 34 (last two A batches) added 0 new relevant rows; Crossref added the four seminal works. SEAMS/ACSOS LLM papers mostly appear on arXiv and were covered; non-arXiv SEAMS/ACSOS'25-26 papers may be missing (only reachable via proceedings lists).
- B (LLM AIOps / RCA / incident management, GPU-cluster and LLM-serving diagnosis, agent-failure diagnosis): arXiv keyword search saturated (queries 39-43 added 0-2 each). Citation snowball was still producing items when cut by time; several hits there have no arXiv/DOI page I could fetch (see leads).
- C (LLM-driven tuning / runtime adaptation from metrics, harness-engine coupling): saturated for my scope (queries 35-38 added 0-3); the policy-synthesis literature (ADRS, AlphaEvolve-style) is only sampled here because t2_policy_synth covers it.
- D (benchmarks/datasets): saturated (queries 39-40 added 0). Only two items target LLM serving specifically (2603.02057 RCA methods on LLM inference; 2608.20370 XPerf); AgentChaosBench and TelemetrySuffBench target agent execution telemetry.

### (c) Closest 5 to the target loop

The target loop: edge store of agent traces + HW/perf + congestion signals -> LLM root-cause analysis of systematic performance degradation -> LLM-synthesized "compensation" tools/policies -> deployed into the cloud agent harness -> guidance under live load -> repeats. No paper found implements this loop. Each of the closest works has two to four of the six parts:

1. **Autopoiesis (arXiv 2604.07144, 2026): self-evolving LLM serving.** Covers: telemetry (sliding-window snapshots of arrivals + cluster state), LLM program synthesis that rewrites the serving policy code (should_reschedule/schedule), simulator trace-replay evaluation with cost-breakdown feedback, hot-deploy to the live data plane, a continuous closed loop. Misses: no explicit root-cause step (the LLM mutates code from cost breakdowns), no agent traces, the artifact goes into the serving engine's scheduler, not into the agent harness or agent toolset, and no edge/cloud split.
2. **SchedCP / sched-agent (arXiv 2509.01245, MLforSystems'25).** Covers: tiered telemetry tools (perf, eBPF probes, post-deploy feedback), LLM workload analysis -> goal inference, synthesis of new eBPF scheduler code, static+dynamic verification, canary deploy with auto-revert, and an evolving policy repository (closed loop). Misses: OS scheduler rather than LLM serving or agent harness, it reacts to workload type rather than diagnosing degradation under load, and there are no agent traces and no edge split.
3. **Self-Defining Operator (arXiv 2610.14547, 2026).** Covers: live telemetry, LLM RCA via hypothesis-test loops, synthesis of executable artifacts (detector code over telemetry, parameterized playbook scripts) committed as versioned memory, cheap non-LLM detectors on the steady-state path that call the LLM only when they fire (very close to an "edge monitor + cloud LLM" split in spirit), continuous improvement (-52% MTTR). Misses: functional faults (crashloops, config) rather than load/congestion performance, the artifacts serve the operator agent itself rather than the workload agents' harness, and it is not about LLM serving.
4. **HarnessFix (arXiv 2606.06324, 2026) together with OpsHarness (arXiv 2608.25661, 2026).** Covers: diagnosis from agent execution traces mapped onto harness mechanisms, synthesis of harness patches, tools and skills (idea-card tool library), deployment into a general coding-agent harness (Codex / Claude Code), and regression-gated self-evolution. Misses: both target task correctness (benchmark accuracy or RCA accuracy), not efficiency under server load; they use no HW/perf/congestion telemetry, and evolution is offline between runs.
5. **INFRAMIND (arXiv 2606.11440, 2026), with HEAR (2610.06597) and CONCUR (2601.22705) as siblings.** Covers: the harness reads live serving telemetry (vLLM /metrics queue depth, latency, KV utilization) and changes its own behavior under congestion (topology, model, reasoning depth per call), and improves on-time answers under load. HEAR standardizes the harness-engine signal channel; CONCUR gates agent admission on KV pressure. Misses: the policies are trained or hand-written, not produced by LLM diagnosis. There is no root-cause step and no automatic re-synthesis when the bottleneck changes.

Also close on single dimensions: Tool-Making Agents (2607.08010, EMNLP'26 industry) compiles traces into validated tools deployed in a production agent to cut latency. FunPilot (APSys'26) runs online LLM performance diagnosis with knob remediation inside an event-driven loop. E3-Agent (2605.27428) puts an LLM meta-controller on an edge inference control surface via tools. Glia (2510.27176) designs LLM-serving routing and autoscaling with LLM agents, offline. Recuse-signal study (2606.06460) independently finds that agents ignore throttle signals and need harness enforcement.

**Gap statement:** the diagnosis literature (RCACopilot, RCAgent, STRATUS, OpenRCA, TELLER, KAT) stops at text root causes or bounded mitigations on microservices or training clusters. The synthesis literature (Autopoiesis, SchedCP, ADRS, PolicySmith) writes engine- or kernel-side policies. The harness literature (HEAR, INFRAMIND, CONCUR, AgentRM) uses fixed, human-designed load policies. No work found closes all three: LLM RCA of serving-load degradation from agent traces, then generated harness-side compensation tools or gates, then continual re-evaluation.

### (d) Leads not followed (for snowball)

- No canonical page found (S2 citation hits only): RunbookFX (type/effect-safe LLM runbook synthesis); SOPSmith (executable SOPs for LLM-driven GPU-cluster network diagnosis); pMVX (policy-level multi-version execution for agentic OS kernel self-tuning); Fuyun (LLM agents for serverless resource provisioning); BudgetSRE; OpsLens (runbook-free RCA with tool management); CausalDX; HERO; CloudHeal (aerospace cloud self-healing); DBAgent (RL DB O&M agent); "Agent-Based Intelligent Operations and Maintenance for Artificial Intelligence Data Centers" (2025); "AI-driven self-healing across the edge-cloud continuum: a systematic literature review" (2026); "Expanding Guard Reach: Online Learning-Driven Incremental Kernel Panic Diagnosis"; Scout 1.0 (post-training a model for SRE investigations); "Networked Agent Memory and Causality Representation ... Cloud-Scale".
- Industry/blog: Reef ("Your inference server is secretly a learner", open-source continual self-improving agents via harness evolution from inference traces, Sept 2026); Datadog Bits AI SRE.
- arXiv items seen but not recorded (lower priority): 2609.02805 telecom LLM RCA; 2408.17097 LLMs reasoning about AI performance degradation in 6G; 2508.09197 MX-AI agentic observability for AI-RAN; 2610.06971 AegisFlow self-healing data ecosystems; 2607.27290 EvoCause; 2605.03505 LATS-RCA; 2608.08968 GALA; 2509.15635 MicroRCA-Agent; 2601.02736 Hypothesize-then-verify RCA; 2608.15117 VRAM stability forecasting in code-synthesis agent workloads; 2608.13573 A Year in LLM Serving; 2610.27945 How developers configure LLM inference; 2605.16637 HexAGenT; 2512.13956 AOI (context-aware multi-agent operations); 2609.27538 Backstitch; 2610.21088 Probe-and-Resume; 2604.25222 adaptive management of microservices taxonomy; Andromeda (SIGMOD'25, DB perf debugging with RAG LLMs) and ION (HotStorage'24) have no arXiv hit via API.
- Venue verification incomplete for ~20 rows (Semantic Scholar rate-limited, DBLP blocked): those keep "arXiv" as venue.

---

# Agent log: `t2_policy_synth` (round 1)

## t2_policy_synth log (Topic 2, synthesis half)

Start 2026-11-01 05:08 UTC. Wall clock reported by `date -u` advanced slowly (05:08 -> ~05:35 over the session).

### (a) Queries

| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | `abs:LLM AND abs:evolutionary AND (abs:scheduling OR abs:scheduler) AND (abs:systems OR abs:cluster)` | arXiv API | 7 | 2 |
| 2 | `(abs:"AI-driven research for systems" OR abs:ADRS OR ti:"systems research")  AND abs:LLM` | arXiv API | 32 | 2 |
| 3 | `abs:OpenEvolve` | arXiv API | 26 | 3 |
| 4 | `abs:LLM AND (abs:"sched_ext" OR abs:eBPF) AND (abs:scheduler OR abs:policy OR abs:agent)` | arXiv API | 15 | 6 |
| 5 | `(abs:LLM OR abs:"language model") AND abs:"congestion control" AND (abs:generate OR abs:synthesize OR abs:design OR abs:evolve)` | arXiv API | 11 | 6 |
| 6 | `(abs:LLM OR abs:"language model") AND (abs:"cache eviction" OR abs:"cache replacement" OR abs:"eviction policy") AND (abs:generate OR abs:evolve OR abs:design OR abs:synthesize)` | arXiv API | 40 | 4 |
| 7 | `(abs:LLM OR abs:"language model") AND abs:heuristic AND (abs:"load balancing" OR abs:"request routing" OR abs:"LLM serving" OR abs:"inference serving")` | arXiv API | 28 | 2 |
| 8 | `ti:Glia OR (abs:"AI for systems design" AND abs:LLM) OR ti:"Policy Smith" OR ti:PolicySmith` | arXiv API | 10 | 1 |
| 9 | `(abs:LLM OR abs:"language model") AND (abs:"systems heuristics" OR abs:"system heuristics" OR abs:"systems policies" OR abs:"heuristic synthesis")` | arXiv API | 19 | 4 |
| 10 | `(abs:LLM OR abs:"language model") AND (abs:"root cause" ) AND (abs:"LLM serving" OR abs:"inference serving" OR abs:"GPU cluster" OR abs:"performance degradation")` | arXiv API | 20 | 0 |
| 11 | `(abs:"self-evolving" OR abs:"self-improving" OR abs:"self-driving") AND (abs:"LLM serving" OR abs:"inference system" OR abs:"serving system" OR abs:scheduler)` | arXiv API | 50 | 3 |
| 12 | `(abs:LLM OR abs:"language model" OR abs:agent) AND (abs:"vLLM" OR abs:"LLM serving" OR abs:"LLM inference") AND (abs:"evolve" OR abs:"evolutionary" OR abs:"program search" OR abs:"code generation") AND (abs:scheduler OR ` | arXiv API | 33 | 1 |
| 13 | `(abs:agentic OR abs:"LLM agent") AND (abs:"configuration tuning" OR abs:"parameter tuning" OR abs:autotuning OR abs:"performance tuning") AND (abs:serving OR abs:database OR abs:system)` | arXiv API | 50 | 4 |
| 14 | `(abs:"LLM-driven" OR abs:"LLM-guided" OR abs:"LLM-generated" OR abs:"LLM-evolved") AND (abs:"scheduling policy" OR abs:"scheduling policies" OR abs:"scheduling heuristic" OR abs:"scheduling algorithm") AND (abs:cluster O` | arXiv API | 3 | 1 |
| 15 | `(abs:AlphaEvolve OR abs:OpenEvolve OR abs:ShinkaEvolve OR abs:FunSearch) AND (abs:systems OR abs:scheduling OR abs:network OR abs:cache OR abs:GPU OR abs:serving OR abs:compiler)` | arXiv API | 56 | 3 |
| 16 | `ti:VibeServe OR abs:VibeServe` | arXiv API | 1 | 1 |
| 17 | `(abs:"agentic" OR abs:"LLM agent" OR abs:"coding agent") AND (abs:"serving system" OR abs:"inference engine" OR abs:"serving engine") AND (abs:optimize OR abs:synthesize OR abs:build OR abs:evolve) AND (abs:autonomous OR` | arXiv API | 10 | 2 |
| 18 | `(abs:"MAPE-K" OR abs:"self-adaptive system" OR abs:"self-adaptive systems") AND (abs:LLM OR abs:"large language model")` | arXiv API | 16 | 2 |
| 19 | `(abs:LLM OR abs:"large language model") AND (abs:telemetry OR abs:observability OR abs:"performance counters") AND (abs:"generate code" OR abs:"code generation" OR abs:synthesize OR abs:"policy generation" OR abs:"genera` | arXiv API | 60 | 0 |
| 20 | `ti:"Tool Makers" OR ti:"Live-SWE-agent" OR ti:"CREATOR: Tool Creation" OR ti:"Alita" OR ti:"ToolMaker" OR (ti:tool AND ti:creation AND abs:LLM)` | arXiv API | 15 | 5 |
| 21 | `(abs:"agent harness" OR abs:"agentic harness" OR abs:"harness engineering") AND (abs:evolve OR abs:evolution OR abs:optimize OR abs:"self-improving")` | arXiv API | 60 | 10 |
| 22 | `(abs:harness OR abs:scaffold OR abs:"agent framework") AND (abs:evolve OR abs:"self-improv" OR abs:optimiz) AND (abs:latency OR abs:"serving" OR abs:"inference engine" OR abs:throughput OR abs:"GPU" OR abs:"KV cache" OR ` | arXiv API | 60 | 6 |
| 23 | `(abs:"inference engine" OR abs:"serving system" OR abs:vLLM OR abs:SGLang) AND abs:harness AND abs:agent` | arXiv API | 13 | 3 |
| 24 | `(abs:"self-evolving" OR abs:"self-evolution" OR abs:"continuously evolve" OR abs:"online evolution") AND (abs:policy OR abs:policies OR abs:heuristic) AND (abs:LLM OR abs:"language model") AND (abs:cluster OR abs:serving` | arXiv API | 41 | 1 |
| 25 | `ti:"Designing Network Algorithms via Large Language Models" OR ti:AutoOS OR (abs:"adaptive bitrate" AND abs:LLM AND abs:generate)` | arXiv API | 6 | 2 |
| 26 | `(abs:LLM OR abs:"language model") AND (abs:"load balancer" OR abs:"load balancing policy" OR abs:"autoscaling policy" OR abs:"autoscaler") AND (abs:generate OR abs:synthesize OR abs:evolve OR abs:design)` | arXiv API | 40 | 1 |
| 27 | `(abs:"LLM agent" OR abs:"LLM agents" OR abs:agentic) AND (abs:"inference cluster" OR abs:"serving cluster" OR abs:"GPU cluster") AND (abs:autonomous OR abs:manage OR abs:operate OR abs:optimize)` | arXiv API | 15 | 0 |
| 28 | `ti:Fuyun OR ti:pMVX OR ti:"Agentic Performance Management" OR ti:"Self-evolving Operating System Layer" OR ti:"Rule Generation for Enforcement Systems"` | arXiv API | 1 | 1 |
| 29 | `(abs:OpenEvolve OR abs:AlphaEvolve OR abs:"LLM-guided evolution" OR abs:"evolutionary coding agent" OR abs:"LLM-driven evolution" OR abs:"program evolution") AND (abs:scheduler OR abs:placement OR abs:tiering OR abs:rout` | arXiv API | 23 | 0 |
| 30 | `(abs:vLLM OR abs:SGLang OR abs:"inference server") AND (abs:"LLM agent" OR abs:"coding agent" OR abs:agentic) AND (abs:tune OR abs:tuning OR abs:configure OR abs:configuration OR abs:optimiz)` | arXiv API | 30 | 4 |
| 31 | `(abs:Kgent OR (abs:eBPF AND abs:"LLM" AND abs:verif))` | arXiv API | 2 | 1 |
| 32 | `ti:Aragog OR (abs:"agentic workflow" AND abs:"system load" AND (abs:adapt OR abs:reconfigur))` | arXiv API | 2 | 2 |
| 33 | `(abs:harness OR abs:"agent framework" OR abs:scaffold) AND (abs:"server load" OR abs:congestion OR abs:"queueing delay" OR abs:backpressure OR abs:"rate limit") AND (abs:LLM OR abs:agent)` | arXiv API | 19 | 1 |
| 34 | `(abs:"AI-driven research" OR abs:ADRS OR abs:"agentic evolution" OR abs:"AI-evolved") AND (abs:systems OR abs:system)` | arXiv API | 40 | 0 |
| 35 | `(abs:"heuristic design" OR abs:"policy synthesis" OR abs:"policy generation") AND (abs:LLM OR abs:agent) AND (abs:logs OR abs:traces OR abs:telemetry OR abs:profiling)` | arXiv API | 18 | 2 |
| 36 | `abs:harness AND (abs:telemetry OR abs:"KV cache" OR abs:vLLM OR abs:"serving system" OR abs:"GPU utilization" OR abs:congestion) AND (abs:evolve OR abs:synthesize OR abs:generate OR abs:"self-improv")` | arXiv API | 31 | 0 |
| 37 | `(abs:"tool" AND abs:"synthesiz") AND (abs:latency OR abs:throughput OR abs:efficiency) AND (abs:"agent" ) AND (abs:"production" OR abs:"deployed")` | arXiv API | 0 | 0 |
| 38 | LLM-generated scheduling policies deployed online in production system self-evolving telemetry feedback loop | WebSearch ext | 9 | 1 |
| 39 | ADRS AI-driven research for systems case studies OpenEvolve MoE placement spot scheduling txn scheduling | WebSearch | 10 | 0 |
| 40 | PolicySmith LLM-synthesized heuristics kernel congestion control caching | WebSearch | 10 | 0 |
| 41 | LLM agent evolves vLLM scheduler or router policy from serving traces arXiv 2026 | WebSearch ext | 10 | 1 |
| 42 | LLM agent diagnoses performance bottleneck from telemetry and synthesizes code fix deployed online closed loop self-healing | WebSearch ext | 9 | 4 |
| 43 | ShinkaEvolve arXiv sakana open-ended program evolution | WebSearch | 10 | 1 |
| 44 | AlphaEvolve Borg data center scheduling heuristic deployed fleet | WebSearch | 10 | 0 |
| 45 | agent harness self-evolving harness automatically generated tools from execution traces performance arXiv 2026 | WebSearch ext | 9 | 6 |
| 46 | LLM agent automatically tunes LLM inference serving configuration vLLM from profiling traces | WebSearch ext | 9 | 0 |
| 47 | LLM-written cache eviction policy evolved deployed CDN or storage system production | WebSearch | 10 | 0 |
| 48 | self-driving network LLM agent generates network policy code from telemetry intent-based closed loop | WebSearch | 10 | 1 |
| 49 | LLM generates eBPF programs for OS policy automatically kernel extension verifier feedback | WebSearch | 10 | 1 |
| 50 | LLM-synthesized policy hot-swapped at runtime online adaptation systems runtime code generation 2026 | WebSearch ext | 9 | 1 |
| 51 | Autopoiesis self-evolving system paradigm LLM serving runtime dynamics | WebSearch | 10 | 2 |
| 52 | SEIS Self-Evolving Inference Systems arXiv 2610.04646 | WebSearch | 10 | 0 |
| 53 | AI-driven research for systems LLM serving scheduler evolution OpenEvolve prefix/KV cache 2026 | WebSearch ext | 10 | 0 |
| 54 | VibeServe multi-agent system synthesizes LLM serving runtime UW self-defining systems | WebSearch | 7 | 1 |
| 55 | SelfEvolve agentic architecture runtime code generation SEAMS 2026 | WebSearch | 7 | 1 |
| 56 | LLM agents optimize distributed systems online digital twin Kubernetes autoscaling LLM-generated code | WebSearch | 10 | 0 |
| 57 | self-driving DBMS Peloton NoisePage OtterTune LLM agent database tuning lambda-Tune GPTuner | WebSearch | 10 | 4 |
| 58 | Glia human-inspired AI automated systems design LLM serving MIT | WebSearch | 10 | 0 |
| 59 | LLM agent observes production telemetry writes new heuristic code for LLM inference cluster autoscaling routing online | WebSearch ext | 9 | 0 |
| 60 | KernelBench CUDA-L1 AI CUDA Engineer LLM kernel optimization profiling feedback | WebSearch | 10 | 3 |
| 61 | LLM evolutionary heuristic design EoH ReEvo FunSearch MCTS-AHD | WebSearch | 10 | 3 |
| 62 | "Grounding Agent-Driven Code Optimization in Production Telemetry" | WebSearch ext | 10 | 1 |
| 63 | "Self-Defining Systems" paper UW | WebSearch | 10 | 1 |
| 64 | "Survival of the Luckiest" noisy evaluation agentic evolution for systems | WebSearch | 10 | 0 |
| 65 | pMVX policy-level multi-version execution agentic OS kernel self-tuning | WebSearch | 10 | 1 |
| 66 | STRATUS multi-agent autonomous reliability engineering NeurIPS 2025 | WebSearch | 10 | 1 |
| 67 | Astra multi-agent GPU kernel optimization SGLang | WebSearch | 7 | 1 |
| 68 | "Towards Agentic Performance Management" Levy AgenticOS | WebSearch | 10 | 1 |
| 69 | LLM congestion control generation evolutionary NSDI/SIGCOMM/HotNets 2025 2026 | WebSearch | 10 | 0 |
| 70 | self-evolving agent harness optimizes latency cost efficiency under serving load | WebSearch ext | 9 | 4 |
| 71 | edge server collects agent traces and GPU metrics, LLM analyzes degradation root cause and generates mitigation code | WebSearch ext | 10 | 0 |
| 72 | "Fast or Slow" human-inspired self-evolving framework resilient AI systems DSN 2026 | WebSearch | 10 | 0 |
| 73 | "AI for Systems is AGI-Complete" SIGOPS OSR 2026 | WebSearch | 10 | 0 |
| 74 | LLM agents generate performance optimization diffs from production profiling at scale Meta/Google | WebSearch ext | 10 | 4 |
| 75 | safe rollout canary verification of LLM-generated systems policies guardrails sandbox 2026 | WebSearch | 7 | 0 |
| 76 | KernelEvolve Meta agentic kernel coding heterogeneous accelerators | WebSearch | 10 | 1 |
| 77 | automatically generated harness tool/policy for coding agents reacting to KV cache pressure or GPU contention on vLLM | WebSearch ext | 10 | 0 |
| 78 | edge-cloud collaborative self-evolving system LLM analyzes edge telemetry synthesizes policy pushed to cloud | WebSearch | 10 | 0 |
| 79 | LLM agent generates eBPF GPU driver/memory policies gpu_ext sched_ext agentic | WebSearch | 7 | 0 |
| 80 | prefix/KV cache eviction or scheduling policy evolved by LLM online during serving vLLM SGLang 2026 | WebSearch | 10 | 0 |
| 81 | citations of arXiv:2510.06189 (Barbarians at the Gate) | Semantic Scholar | 77 | 14 |
| 82 | citations of arXiv:2510.08803 (PolicySmith) | Semantic Scholar | 10 | 1 |
| 83 | citations of arXiv:2510.27176 (Glia) | Semantic Scholar | 22 | 5 |
| 84 | citations of arXiv:2604.07144 (Autopoiesis) | Semantic Scholar | 1 | 0 |
| 85 | citations of arXiv:2509.01245 (SchedCP) | Semantic Scholar | 8 | 3 |
| 86 | citations of arXiv:2606.09613 (AgentServeSim) | Semantic Scholar | 2 | 1 |
| 87 | citations of arXiv:2605.06068 (VibeServe) | Semantic Scholar | 5 | 2 |
| 88 | citations of arXiv:2512.25065 (Vulcan) | Semantic Scholar | 2 | 0 |
| 89 | citations of arXiv:2512.14806 (Let the Barbarians In) | Semantic Scholar | 3 | 1 |
| 90 | citations of arXiv:2506.13131 (AlphaEvolve), filtered to systems keywords (~230 of 1000 scanned) | Semantic Scholar | 1000 | 13 |
| 91 | citations of ECO / IteRate / TuxBot | Semantic Scholar | 1 | 0 |
| 92 | AgenticOS@ASPLOS'26 and AgenticOS@SOSP'26 program pages | workshop site | 40 | 5 |
| 93 | DBLP lookups OtterTune/Park/Autopilot/GEPA (DBLP returned non-JSON; used arXiv/NeurIPS/Google pages instead) | DBLP/vendor | 4 | 3 |

Rows written: 139 in `t2_policy_synth.jsonl` (all verified by fetching the arXiv abs page, the venue/vendor page, or the GitHub repo in this session; 3 workshop rows (APM, pMVX, Fuyun) verified only via the AgenticOS program page because the PDFs use CID fonts that could not be text-extracted here; their evidence fields are search-snippet text and say so).
Per area (prefix of `area`): A LLM-evolved systems heuristics 35 (rel3 1, rel2 11) · B self-driving/precedent/safe deployment 27 (rel2 7) · C LLMs optimizing LLM serving/kernels 19 (rel2 3) · D telemetry→LLM→policy→deploy loops 31 (rel3 7, rel2 11) · H harness/tool synthesis (added because `harness_deploy` is the decisive check) 27 (rel3 1, rel2 8).

### (b) Saturation per sub-area

- **A. LLM-driven algorithm/heuristic evolution for systems**: saturated for arXiv keyword search (queries 29 and 34 added 0 new relevant; Barbarians/PolicySmith/Glia/Vulcan citation snowballs exhausted). Caveat: the AlphaEvolve citation list was capped at 1000 by Semantic Scholar, so more domain-specific AlphaEvolve-for-X papers may exist (mostly kernels/math, outside the target).
- **B. Self-driving / autonomous systems precedent + safe deployment of generated code**: saturated for LLM-era work (the safe-rollout WebSearch and the eBPF+LLM verification query added 0-1). Classic pre-LLM precedents only sampled (Pavlo CIDR'17, Decima, Park, Autopilot, Rosella-style); OtterTune SIGMOD'17 not fetched (DBLP and ACM blocked).
- **C. LLMs optimizing LLM serving / kernels**: kernels kept brief on purpose (not saturated, by design). Agents-optimizing-serving-engines saturated (the vLLM/SGLang tuning query added 3, the next two searches on online KV/scheduling evolution and eBPF GPU policies added 0).
- **D. Telemetry → LLM diagnosis → generated policy/tool → deployment → re-measurement**: saturated. The last four probes (KV-pressure-reactive harness tools, edge-cloud self-evolving telemetry, harness+serving-telemetry synthesis, production tool synthesis for latency) all added 0. **No paper found that deploys LLM-synthesized compensation tools into an agent harness in response to serving-system congestion.**
- **H. Harness/tool self-evolution**: not saturated. The 2026 harness-evolution literature is very large (query 21 alone returned 60 hits from Sep-Oct 2026). I sampled 27, chosen for trace-driven, efficiency-oriented, or safety-gated designs. All target task success or tokens. None uses serving/HW telemetry. This area is outside my core scope and needs its own sweep if needed.

### (c) Closest 5

1. **Autopoiesis (arXiv 2604.07144)**: always-on control plane that evolves LLM-serving policy code (`schedule`, `should_reschedule`) from snapshots of live runtime traces, then hot-swaps it into the serving data plane; warm-started re-evolution; 27-44% better than DistServe, HexGen and SpotServe. **Misses:** its telemetry is only request-arrival and cluster-availability windows (no HW counters, KV/congestion signals or agent traces). No root-cause diagnosis: the LLM only reads a per-candidate cost breakdown. The artifact is cluster-scheduler code, not harness tools or agent guidance. Fitness comes from simulator replay of the snapshot. The split is a data-plane/control-plane split, not an edge/cloud split. Agents never see anything.
2. **SchedCP / sched-agent (arXiv 2509.01245)**: MCP control plane (workload analysis via perf/eBPF probes, policy repository, 3-stage verifier) that lets Claude Code subagents profile a workload, synthesize eBPF sched_ext schedulers, verify and deploy them live, then a Learning Agent updates the repository. **Misses:** the target is the Linux CPU scheduler, not LLM serving. It does workload characterization, not diagnosis of degradation under load. The synthesized artifact goes into the kernel, not into the workload agents' harness (the MCP tools are hand-written). Adaptation runs per workload, not continuously under changing congestion.
3. **IteRate (arXiv 2605.02542)**: Wi-Fi devices stream per-frame HW telemetry to a central LLM orchestrator. The orchestrator hypothesizes, writes eBPF rate-control code, deploys it over the air in under 1 s, measures and iterates without humans. This is the only item with an explicit edge/central split of collector and synthesizer. **Misses:** the domain is wireless rate control. The artifact is data-plane code, not harness tools. It is an exploratory research loop, not compensation for live degradation. No LLM serving or agents.
4. **Self-Defining Operator, SDO (arXiv 2610.14547)**: an LLM operator agent diagnoses microservice incidents from telemetry. After each one it writes playbooks plus detector scripts over live telemetry. Detectors run continuously and call the LLM only when they fire (−52% MTTR on recurring faults). **Misses:** it handles reliability faults, not performance or congestion, and uses no HW/GPU metrics. The synthesized artifacts live in the operator's own Claude Code memory/harness, not in the harness of the workload agents being served. Not LLM serving.
5. **Tool-Making & Self-Evolving LLM Agents in Low-Latency Systems (arXiv 2607.08010, EMNLP'26 Industry)**: a tool-maker grounds synthesis in live execution traces and backend schemas, compiles repeated SOP steps into validated, versioned tools, and deploys them into a production agent's toolset (p50 latency −42%, errors −53%). This is the only work found where synthesized tools enter a production agent harness for latency. **Misses:** no serving/HW/congestion telemetry and no root-cause diagnosis of system degradation. Tools encode task procedures, not load-dependent compensation. Not continuously re-synthesized under load. Not LLM serving.

Runners-up: Glia (2510.27176; LLM-serving routing/scheduling/autoscaling designed via instrumented-simulator diagnosis; offline, no harness), TRACE (2610.01887; log-driven hypotheses + synthesized executable surrogate tools for evolved heuristics; offline), ECO (2503.15669; fleet profiling → LLM code edits → tests/self-review/post-deploy monitoring in Google production; static code, no harness), AgentServeSim (2606.09613; OpenEvolve over agent-serving KV-retention/scheduling policies validated vs vLLM; offline), HEAR (2610.06597; harness↔engine signal protocol, the integration surface, but hand-written policies), AKTS (2609.12276; agent switches preverified eBPF policies at runtime on vLLM hosts), HELIOS (2609.09164; guardrailed champion-policy deployment pattern).

Cross-cutting answer to the `harness_deploy` check: **none of the systems-policy synthesis works deploys its artifact into an agent harness.** Harness-evolution works (H) do deploy into harnesses, but they optimize task success or token counts from trajectories, with no serving telemetry or congestion RCA. One H-area result (2607.20668) finds that policies generated from agent trajectories do not reliably beat fixed prompting. That fits the user's observation that models ignore advisory efficiency signals, and it argues for executable harness-side gates over advisory text.

### (d) Leads not followed up (for snowball)

- "AI for Systems is 'AGI-Complete'" — ACM SIGOPS OSR 2026 (cites ADRS; not found online).
- "Survival of the Luckiest: The Impacts of Noisy Evaluation in Agentic Evolution for Systems" (2026; cites ADRS/PolicySmith/Vulcan; no arXiv ID found).
- "Fast or Slow? Human-Inspired Self-Evolving Framework for Resilient AI Systems" (DSN 2026, Iyer et al.; cites Let the Barbarians In; not found).
- "MetaInfer: A Knowledge Only LLM Inference Engine Generator SKILL Toolbox" (arXiv 2026; cites Glia/VibeServe; ID not found).
- "Understanding the Use and Impact of AI-Coding Assistants and Agents in System Development" (2026; cites ADRS).
- "Capacity Efficiency at Meta: How Unified AI Agents Optimize Performance at Hyperscale" (Meta engineering, 2026; not fetched).
- PROMPTS: Performance Optimization via Multi-Agent Planning for LLM Training and Serving (Google Research 2026; profiler data → sharding configs; not fetched).
- GitHub Daniel1443/agenttune-vllm (LLM agent as SLO-constrained vLLM autotuner) and AMD ROCm inferasim-tune tuning agent (docs) — not fetched.
- "Towards Safe Agentic AI Performance Engineering" (Virginia Tech thesis, vtechworks).
- AgenticOS@SOSP'26 workshop papers: Agent libOS (capability-controlled self-evolving agents), AgentProf (semantic profiling for AI agents), Patient Bytes (reliability-budgeted scheduler for agentic workflows), Irreversibility Budget (fleet-level admission control). AgenticOS@ASPLOS'26: "Toward LLM-Driven Rule Generation for Enforcement Systems: WAF" (Fu & Williams).
- NetGPT cloud-edge synergy for tool-calling agents (arXiv 2511.22217), Conversational Orchestration for Organic 6G (alphaxiv 2608.10714), LLM-PRO IoE resource allocation (2607.26602), EWSJF (2601.21758), MCP-DT LLM algorithm synthesis with digital-twin safety enforcement (IEEE 2026), Specula (2607.25333), Invariant Discovery for Networked Systems (2607.22944), Network World Models for algorithm design (2610.01048).
- Serving/RCA-half items seen but not logged (other agents' scope): Coda (2610.03088, same group as Autopoiesis), Continuum (2511.02230), ARGUS 10k-GPU tracing (2606.20374), host-CPU interference diagnosis in LLM serving (2609.05425), HexAGenT (2605.16637), CCL-Bench (2605.06544).
- Harness-evolution items not logged (H not saturated): HarnessTTT (2610.17370), HEMO (2610.40023), Recursive Harness Self-Improvement (2610.03548), Automated Discovery Has No Universally Superior Harness (2607.18235), Harmony Harness multi-objective (2610.15150), VERSE verified self-evolving harness optimizer (2610.02616), Causal Improvement Graph for harness optimization (2610.05039), Harness-Delta Attribution (2610.22466), EvoNet/EvolveNet (2608.04968), SIA harness+weight updates (2605.27276), HELIX (2608.13951), cua-speedrun (2609.40284).

---

# Agent log: `t2_self_evolving` (round 1)

## t2_self_evolving — sweep log (Topic 2: self-evolving agents/harnesses, tool creation, agent-serving co-design)

Started 2026-11-01 05:08 UTC; finished ~05:36 UTC.

### (a) Query table
| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | abs:"self-evolving" AND agent AND (harness OR scaffold) | arXiv API | 50 | 22 |
| 2 | harness AND agent AND (evolv/optimization) AND (latency/throughput/efficiency/cost) | arXiv API | 60 | 18 |
| 3 | agent AND (harness/scaffold/workflow) AND (server load/congestion/KV cache/backpressure/rate limit/contention) | arXiv API | 60 | 24 |
| 4 | (harness OR agent) AND (inference engine/serving system/vLLM/SGLang) AND (signal/feedback/hint/directive/co-design) | arXiv API | 56 | 8 |
| 5 | "meta-harness" OR "harness engineering" OR "harness optimization" | arXiv API | 60 | 14 |
| 6 | (self-evolving/self-improving) AND (serving/inference system/throughput/tail latency/SLO/congestion) | arXiv API | 60 | 4 |
| 7 | (agent OR harness) AND (rate limit/backpressure/admission control/concurrency control) AND LLM | arXiv API | 33 | 5 |
| 8 | title lookups of seed self-evolving agents (DGM, Godel Agent, SICA, Live-SWE-agent, ADAS, AFlow, MaAS, EvoAgentX, AgentSquare, MetaAgent, Agent0, SE-Agent, surveys, HGM, Self-Harness, HarnessX) | arXiv API | ~70 | 22 |
| 9 | title lookups of tool-creation seeds (LATM, CREATOR, CRAFT, ToolMaker, TroVE, Voyager, Alita, SkillWeaver, ASI, AWM, ExpeL) | arXiv API | ~60 | 12 |
| 10 | (tool creation/tool making/tool synthesis/tool evolution/tool library) AND agent | arXiv API | 60 | 8 |
| 11 | title lookups of context-evolution seeds (ACE, Dynamic Cheatsheet, GEPA, TextGrad, Trace, DSPy, MIPRO, Reflexion, Memento, TF-GRPO, ReasoningBank) | arXiv API | ~40 | 13 |
| 12 | title lookups of agent-serving seeds (Autellix, Parrot, InferCept, Conveyor, Tempo, Teola, Alto, Murakkab, AIOS) + "agentic workload(s)" | arXiv API | ~50 | 14 |
| 13 | (budget-aware/cost-aware/latency-aware/efficiency-aware/load-aware/resource-aware) AND agent AND LLM AND (tool OR harness) | arXiv API | 40 | 7 |
| 14 | self-evolving agent harness serving load congestion telemetry LLM generates tools root cause performance 2026 | WebSearch (extended) | 9 | 2 |
| 15 | NeurIPS 2026 Workshop on Resource-Aware Agentic AI accepted papers | WebSearch | 10 | 0 (venue lead) |
| 16 | HarnessFix / SkillForge; LLM/agent + performance degradation/diagnosis + LLM serving/GPU cluster | arXiv API | 24 | 3 |
| 17 | edge-cloud/edge server + agent + LLM + (evolv/self-improv/harness/tool); observability/telemetry + agent + self-improv/harness | arXiv API | 70 | 10 |
| 18 | harness AND (throughput/TTFT/GPU memory/KV cache/prefix cache/prompt cache) | arXiv API | 60 | 3 |
| 19 | Semantic Scholar citations of Autopoiesis, CONCUR, HEAR (snowball) | S2 API | 22 | 7 |
| 20 | (AlphaEvolve/OpenEvolve/LLM-driven evolution/program synthesis) AND (scheduling/serving) AND (LLM/agent) AND (KV cache/agentic/inference) | arXiv API | 23 | 0 |
| 21 | agent AND LLM AND (system state/server state/system load/server status/queue length/load signal) AND (prompt/tool/harness/aware) | arXiv API | 24 | 1 |
| 22 | LLM agent harness exposes serving system load KV cache pressure to the model "wait" tool arXiv 2026 | WebSearch (extended) | 9 | 3 |
| 23 | LLM-generated scheduling policies for agentic LLM serving self-evolving online policy synthesis 2026 | WebSearch | 10 | 3 |
| 24 | title lookups: Speculative Actions, LLMCompiler, speculative tool, asynchronous function calling | arXiv API | 13 | 5 |
| 25 | (self-evolving/self-improving/harness evolution) AND (coding agent/SWE agent/SWE-bench/Terminal-Bench) | arXiv API | 60 | 6 |
| 26 | DBLP 2026 agent serving / agentic LLM / self-evolving agent | DBLP API | 0 (bot-blocked) | 0 |
| 27 | OSDI/NSDI/EuroSys 2026 accepted LLM agent serving agentic workflow KV tool calls | WebSearch (extended) | 9 | 1 (OSDI'26 program lead) |
| 28 | NeurIPS 2026 AgenticOS workshop accepted papers | WebSearch | 10 | 0 (no list public) |
| 29 | OSDI'26 technical sessions page (all sessions incl. "Agentic AI and LLM Operations") | usenix.org fetch | ~110 titles | 3 (Murakkab venue, StriaTrace, ECO) |
| 30 | "OSDI '26" "Agentic AI and LLM Operations"; StriaTrace; ECO | WebSearch | 30 | 4 |
| 31 | harness AND (serving/inference engine/inference server) AND (agent/agentic) | arXiv API | 60 | 4 |
| 32 | (LLM/agent) AND (telemetry/runtime metrics/perf counters/profiling) AND (synthesize/generates code/policy code/heuristic) AND (online/at runtime/continuously) | arXiv API | 50 | 2 |
| 33 | (skill library/skill induction/skill evolution/tool generation/MCP tool) AND agent AND (latency/efficien/cost/steps) | arXiv API | 40 | 6 |
| 34 | (playbook/operational/production/deployment feedback) AND agent AND LLM AND (evolv/self-improv/learn) | arXiv API | 19 | 1 |
| 35 | abs:agentic AND (serving OR inference) AND (KV cache OR scheduling OR scheduler), sorted by date desc (Aug-Oct 2026) | arXiv API | 150 | 12 |
| 36 | same as #35 restricted to submittedDate Dec 2025 - Aug 2026 | arXiv API | 200 | 8 |
| 37 | (congestion/serving conditions/under load/system load/load fluctuat/queueing delay) AND agent AND LLM AND (orchestrat/planner/routing/harness/workflow) | arXiv API | 16 | 2 |
| 38 | "harness engineering" coding agents blog OpenAI OR Anthropic 2026 | WebSearch | 10 | 0 (secondary blogs; OpenAI page 403) |
| 39 | Agent Skills docs (platform.claude.com) | docs fetch | 1 | 1 |
| 40 | (AgentOps/LLMOps/agent observability/performance debugging/bottleneck diagnosis) AND (agent/agentic/LLM) | arXiv API | 50 | 7 |
| 41 | LLM analyzes agent traces and GPU serving metrics to auto-generate harness middleware/tools under contention, closed-loop | WebSearch (extended) | 9 | 4 |
| 42 | (self-evolving/LLM-driven/LLM-generated/LLM-synthesized/evolve) AND agentic AND (serving/scheduler/inference system); au:Jiang_Youhe | arXiv API | 60 | 2 |
| 43 | agent AND LLM AND (budget/cost/time/resource/latency awareness/efficiency instructions) | arXiv API | 30 | 4 |
| 44 | title lookups: Barbarians at the Gate, Let the Barbarians In, OpenEvolve, ADRS | arXiv API | 7 | 2 |
| 45 | (ti:wait/waiting/patience/yield/defer) AND LLM AND agent | arXiv API | 15 | 3 |
| 46 | S2 citations of AHE (166), Meta-Harness (353), Continuum (75), Autellix (88), keyword-filtered | S2 API | 682 (~250 titles scanned) | 19 |
| 47 | (edge/on-device) AND (harness/self-evolving) AND cloud AND (agent/LLM); slow+fast teacher/optimizer offline+online agent | arXiv API | 21 | 2 |
| 48 | (coding agent/Claude Code/OpenHands/SWE-agent) AND (contention/concurrent/multi-tenant/shared GPU/throughput/KV) AND (serving/vLLM/SGLang/inference) | arXiv API | 40 | 2 |
| 49 | "CompQ" QoE-Aware Compensation for Resumed Requests (S2 lead) | WebSearch | 10 | 0 (not found; lead) |
| 50 | (prompt/context optimization/playbook/instruction optimization) AND (latency/token cost/inference cost/efficiency) AND agent | arXiv API | 40 | 1 |
| 51 | (tool synthesis/synthesizes tools/generates tools/tool-making/MCP server generation/creates new tools) AND agent AND (runtime/online/deployment) | arXiv API | 19 | 2 |

Notes on method: arXiv metadata (title, authors, abstract) for every arXiv row was fetched in this session via export.arxiv.org (id_list) by the append script, and titles were checked; evidence strings were checked verbatim against the fetched abstract (or taken from it). Non-arXiv rows (StriaTrace, ECO's USENIX page, Agent Skills docs) were fetched from usenix.org / platform.claude.com. Full-body reads (arxiv.org/html) were done for: Autopoiesis, HEAR, CONCUR, Agentic Harness Engineering, Tool-Making in Low-Latency Systems, INFRAMIND, Decoupling Readiness from Release, AgentServeSim. DBLP was bot-blocked; Semantic Scholar worked for 7 citation snowballs.

Final count: 263 rows (rel 3: 8, rel 2: 61, rel 1: 194).

### (b) Saturation per sub-area
| Sub-area | Rows | Status |
|---|---|---|
| A. Self-evolving agents / automatic harness design | 80 | **Saturated for performance-, cost- or load-oriented work.** The last query batches (#25, #42, #47) each added under 2 relevant items about efficiency. **Not saturated for accuracy-only harness evolution.** arXiv has hundreds of 2026 "self-evolving harness" papers; the S2 citation lists of AHE and Meta-Harness alone hold ~500 citers. I recorded a representative ~60 and checked the rest by title only. None of them optimizes server load. |
| B. Tool creation / skill libraries | 25 | Saturated: #50 and #51 added 1 and 2. |
| C. Context/prompt/playbook evolution | 18 | Saturated: #34 and #50 added 1 each. |
| D. Agent–serving co-design, load/efficiency-aware agents, agent workload characterization, AgentOps | 127 | **Near-saturated for harness/agent-level load awareness:** #37, #43, #45 and #48 added 2–4 each, and most were rel 1. **Not exhaustive for pure engine-side KV/scheduling agent-serving systems:** many more 2026 KV-cache papers exist (topic-1 agents cover them). I recorded those that use agent/workflow/harness signals. |
| Adjacent: LLM-driven systems self-optimization (SchedCP, AKTS, ECO, AgenticDB, ADRS, AlphaEvolve, RoofLang, VibeServe, SEIS) | 13 (area "D/A") | Cut by scope. These are cross-references only; the t2_policy_synth agent covers LLM policy synthesis. |

### (c) Closest 5 (to: edge store of traces+HW/perf+congestion → LLM root-cause diagnosis → synthesized "compensation" tools/policies → deployed into the cloud agent harness → online under load → repeated)

No paper found implements the full loop. The closest works split into two halves, and no paper joins them.
- **"LLM synthesizes a policy from telemetry":** Autopoiesis, AgentServeSim, SEIS, VibeServe, SchedCP. These target the serving engine or OS, not the harness.
- **"Harness acts on load signals":** CONCUR, Decoupling-Readiness-from-Release, INFRAMIND, HEAR. These are hand-designed or trained offline, with no LLM diagnosis or synthesis.
- **Self-evolving harnesses** (AHE, Meta-Harness, Self-Harness, HarnessPro, MoMHa) close the trace→LLM-diagnosis→code→harness loop, but only for task success and tokens. They never use serving telemetry.

1. **Autopoiesis (arXiv 2604.07144, Apr 2026)**
   - **Covers:** live telemetry (a sliding window of request arrivals and cluster resources); LLM program synthesis of serving-policy code (should_reschedule/schedule); asynchronous control plane separate from the data plane, which resembles an edge/cloud split; trace-replay simulator evaluation; hot-swap into the running system; continuous closed loop. Up to 53% (34% average) improvement.
   - **Misses:**
     - The artifact goes into the serving system's scheduler, not into an agent harness or toolset.
     - No explicit LLM root-cause step; the cost breakdown only guides mutation.
     - No agent-facing guidance.
     - Agentic workloads appear only as request-level traces.
     - KV/congestion signals are not the focus; reconfiguration under autoscaling and heterogeneity is.
2. **Agentic Harness Engineering (AHE, arXiv 2604.25850)**
   - **Covers:** closed loop over a coding-agent harness. Raw trajectories are distilled into root-cause evidence by an Agent Debugger. An Evolve Agent writes new tool code, middleware, prompts, skills and sub-agent configs as file-level components. Each edit carries a falsifiable prediction that is checked against the next round.
   - **Misses:**
     - No system/HW/congestion telemetry; it records tokens and timeouts only.
     - The objective is Pass@1, not throughput or latency under load.
     - Evolution is offline between benchmark rounds, not under live load.
     - No edge/cloud split.
3. **INFRAMIND (arXiv 2606.11440)**
   - **Covers:** harness-level orchestration that polls vLLM /metrics (per-model queue depth, end-to-end latency, KV utilization) or client-side proxies before every agent call. It adapts the collaboration graph, model and reasoning effort to congestion, so it is a load-aware harness policy; delivered accuracy rose 20.4→33.8% under load.
   - **Misses:**
     - Policies are PPO/REINFORCE-trained offline and frozen.
     - No LLM diagnosis or code/tool synthesis, and no self-evolution.
     - The agents never see load.
     - It has no "wait/hold" action.
4. **CONCUR (arXiv 2601.22705)**, with its twin **Decoupling Readiness from Release (arXiv 2609.10964)**
   - **Covers:** hand-designed harness-side gates driven by congestion signals, which are exactly the "compensation" behavior the user found effective.
     - CONCUR: an AIMD agent-admission window on KV usage and hit rate; admit/pause/resume whole agents above SGLang; up to 4.09x.
     - Readiness/Release: holds ready turns under a committed-work budget that shrinks with queue pressure; 2.06–3.5x lower P95 on SWE-agent traces.
   - **Misses:** the whole generative loop. Thresholds and policies are fixed by humans; no LLM root-cause analysis, synthesis or evolution; no trace store; no model-facing guidance.
5. **HEAR protocol (arXiv 2610.06597)**
   - **Covers:** a bidirectional harness–engine interface. The engine returns KV residency, capacity, queue state and resource pressure. The harness sends intent: reuse hints, waiting threshold, session continuation and role mode. Policies act online under memory-constrained concurrency (1.61x batch, 2.23x lower P50 TTFT). It is the natural "signal plane" the target loop would build on.
   - **Misses:** policies are hand-written rules; nothing is LLM-diagnosed or generated; no evolution; nothing is shown to the model.

**Runners-up (all have rows in the JSONL):**
- **AgentServeSim (2606.09613):** OpenEvolve evolves agent-serving KV-retention and scheduling code in a vLLM-validated simulator. It is offline and serving-side, with tiny gains (0.5–2.8%), and evolved policies are not deployed.
- **Tool-Making in Low-Latency Systems (2607.08010, EMNLP'26 Industry):** an offline tool-maker running large LLMs uses traces and schemas to synthesize versioned tools for a small-model production agent, cutting p50 latency 42%. The latency is the agent's own codegen loop, not server load.
- **OpsHarness (2608.25661):** a self-evolving RCA harness for operations incidents.
- **SchedCP (2509.01245) and AKTS (2609.12276):** LLM agents analyze workloads and synthesize or select verified eBPF schedulers. This is the same pattern applied to the OS, and AKTS switches policies under a vLLM workload.
- **Sentry (2610.02994):** a condition-triggered playbook outside the context window. Exposing the full playbook to the model hurts.
- **Agents that ignore or misuse resource signals:**
  - AOS-Bench (2610.33178): a fixed concurrency of 2 comes within 4 points of the best agent-managed policy.
  - On the Clock (2610.10833): agents fail to turn a stated time budget into controlled time use.
  - Same Pieces, Different Servers (2603.02348): a simple admission rule cuts tail decision latency by 59%.

### (d) Leads not followed up (for snowball round)
- NeurIPS'26 Workshop on Resource-Aware Agentic AI: accepted-paper list not public or accessible (neurips.cc/virtual/2026/workshop/137575; OpenReview group).
- NeurIPS'26 AgenticOS workshop accepted papers (openreview.net/group?id=NeurIPS.cc/2026/Workshop/AgenticOS). Also AgenticOS'26 at ASPLOS (March 2026).
- OSDI'26 program was read only partially; the PDF could not be text-extracted. Still to check: the full "Agentic AI and LLM Operations" session, and the poster "MetaServe: Decoupling Policy from Mechanism in Heterogeneous AI Workloads".
- EuroSys'26, NSDI'26 and MLSys'26 accepted lists were not systematically checked (github.com/JiusiServe/InferMatrixCopilot/issues/57 lists EuroSys'26 serving papers).
- S2-only titles with no arXiv found:
  - "CompQ: QoE-Aware Compensation for Resumed Requests in Interactive LLM Inference Services"
  - "Strategic Demand and Congestion Pricing in Shared Computation Systems"
- "HarnessFix" (trace IR plus per-layer failure attribution), cited in a blog: jxzhangjhu.github.io/blog/2026/self-evolving-agentic-harnesses. "Agent Harness for LLM Agents: A Survey" (preprints.org 202604.0428).
- LatencyPrism (arXiv 2601.09258): online non-intrusive latency sculpting for SLO LLM inference (telemetry and diagnosis, likely the topic-2 RCA/policy agents' scope).
- ~150 accuracy-only harness-evolution citers of AHE and Meta-Harness were checked by title only, for example:
  - Harness Self-Evolution for Long-Horizon Terminal Agents (2610.27435)
  - HarnessNet (2610.15221)
  - EnactOpt (2610.14977)
  - TTHE (2607.08124)
  - StarHarness (2608.24804)
  - EvolveNet (2608.04968)
  - Verify Smarter (2608.27311)
  - EvoUndo (2608.28363)
  - Harness Updating Is Not Harness Benefit (2605.30621)
- Agent-serving systems seen only as titles:
  - WAKMan (2610.36355)
  - SARIA (2610.31169)
  - Crossflow (2609.27085)
  - DynBranch (2609.31047)
  - UNISON (2609.09643)
  - KAIROS power-efficient (2604.16682)
  - PEEK (2607.02525)
  - FATE (2605.07238)
  - HeraSys (2607.22578)
  - Agent.xpu (2506.24045)
  - DEALS (2609.33768)
  - Workflow-Aware Comm/Comp Scheduling for Mobile Edge AI Agents (2610.29337)
- Industry harness-engineering primary posts (OpenAI "Harness engineering", Feb 2026: 403 on fetch; Anthropic engineering post on long-running-app harness design, Mar 2026) were not fetched.

---

# Agent log: `t2_snowball` (round 2)

## t2_snowball (round 2, topic 2) log

Agent: t2_snowball. Started 2026-11-01 05:36 UTC. Output: `r2/t2_snowball.jsonl` (72 rows, all checked against `t2_r1_known.txt` by arXiv ID and normalized title before insertion; 0 overlaps with round 1).

Rows by task: task 1 (citation/reference snowball) 6; task 2 (round-1 leads) 17; task 3 (venue sweep) 28; task 4 (arXiv recency sweep) 19; side searches 2. Relevance: 15 rows at 2, 57 at 1, none at 3.

Helper scripts: `scripts_t2_snowball/` (parse.py, add.py, absdump.py, arxfilter.py, html2txt.py, pdftxt.py). Downloads: `downloads/t2_snowball/`.

### (a) Query table

| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | /citations for 16 seeds (Autopoiesis 1, AHE 166, HarnessFix 36, OpsHarness 2, SDO 0, Tool-Making 6, SchedCP 8, HEAR 0, INFRAMIND 0, CONCUR 20, Readiness 1, AgentServeSim 2, IteRate 0, TRACE 0, ECO 1, Glia 22) | Semantic Scholar API | 266 citers; 364 entries not in the r1 list (incl. refs) | 5 (RiseHarness, SHarP, EFC, AutoRefine, CompPerf) |
| 2 | /references of Autopoiesis, AHE, HEAR, INFRAMIND, CONCUR | Semantic Scholar API | 250 | 0 new relevant (all serving or benchmark background; Agentix picked up later via NSDI'26) |
| 3 | id_list abstracts for 37 citer candidates | arXiv API | 37 | covered in row 1 |
| 4 | id_list abstracts for 48 r1 lead IDs (9 already known) | arXiv API | 39 | 12 (VRAM forecasting, telecom RCA, 6G degradation, NetGPT, invariant discovery, Backstitch, KAIROS, Crossflow, DEALS, plus others as rel 1) |
| 5 | CompQ title | WebSearch | 10 | 0 (not found) |
| 6 | SemaTune/TuxBot | WebSearch | 10 | 0 (2605.15026 already known) |
| 7 | Reef inference-server learner | WebSearch + GitHub | 7 | 1 (Reef) |
| 8 | Meta Capacity Efficiency agents | WebSearch + engineering.fb.com | 18 | 1 |
| 9 | pMVX | WebSearch | 10 | 0 (known); found AgenticOS site |
| 10 | AgenticOS SOSP'26 + ASPLOS'26 program pages | os-for-agent.github.io | 27 papers | 4 (VibeWAF, Patient Bytes, PROFGUARD, Irreversibility Budget); AgentPProf, AgentCgroup, Fuyun, pMVX, Agent libOS, self-evolving FM OS layer and agentic perf mgmt are already in r1 |
| 11 | SOPSmith, RunbookFX | WebSearch | 17 | 0 (no canonical page exists) |
| 12 | PROMPTS (Google) | WebSearch + research.google | 10 | 1 |
| 13 | "Fast or Slow" DSN'26; "Survival of the Luckiest" | WebSearch | 20 | 0 (IFIP talk title only; paper not found) |
| 14 | 15 recency queries q1-q15 (self-evolving + serving/harness/infra; harness + congestion/load/latency/KV; root cause + LLM serving; agentic backpressure; tool synthesis + telemetry; edge + harness; perf regression; rate limit; etc.), sortBy=submittedDate | arXiv API | 251 | 15 |
| 15 | id_list abstracts for 18 recency candidates | arXiv API | 18 | covered in row 14 |
| 16 | 16 queries q21-q36 (OpenEvolve/AlphaEvolve + serving; harness + vLLM; agentic congestion; AIOps self-evolving; autonomic/MAPE + LLM; serving policy synthesis; control plane agentic; tool creation + latency; performance diagnosis LLM) | arXiv API | 179 | 5 (Cognitive Friction, FlashRT, SLO-Guard, Cosmos gateway, serving-stack confound) |
| 17 | OSDI'26, NSDI'26, SOSP'25, HotOS'25, ASPLOS'26 program pages (keyword scan of titles + abstracts) | usenix.org, sigops.org, asplos-conference.org | ~600 titles | 5 (gigiprofiler, Svalinn, DiTing, Agentix, Loom); Murakkab, StriaTrace, ECO, FLARE, JITServe, Pie are already in r1 |
| 18 | MLSys'26 paper list (185 titles) + 4 WebSearches | mlsys.org, arXiv | 185 | 4 (FlashInfer-Bench, OptiKIT, DriftBench, latency-aware orchestration as spillover); PROMPTS confirmed as MLSys'26 |
| 19 | SIGCOMM'26 papers page (sessions: LLM Inference & Serving; Diagnosis & RCA) | conferences.sigcomm.org | ~120 papers | 3 (XiHe, KVServe, Horizon); XiHe resolves r1 lead "Networked Agent Memory and Causality Representation" |
| 20 | NeurIPS'26 Workshop on Resource-Aware Agentic AI accepted list (about 50 papers) + 6 poster pages | neurips.cc virtual | 50 | 6 (Observability Debt, SkillCache, Failure Signals, Budget Control, Offline Compute, Metered/Provisioned); AgentServeSim, On the Clock and Latency-Aware Orchestration are already in r1 |
| 21 | NeurIPS'25 ML for Systems accepted list + 4 poster pages | neurips.cc virtual | 59 | 4 (Expert in Residence, LLMVisor, InfraGym, Agentic Bridge) |
| 22 | NeurIPS'26 ML for Systems accepted list + 6 poster pages | neurips.cc virtual | 79 | 6 (SysContract, AgentTwin, bottleneck shifts, latency budget, ExaAgent, GORGO) |
| 23 | ICLR/ICML'26 + harness load-adaptive; "self-evolving harness serving telemetry congestion" | WebSearch (extended) | 18 | 1 (Knsight, EMNLP'26 Industry); TopoEvo, RRSI, Self-Evolving Coding Agents, ThunderAgent are already in r1 |
| 24 | Lilian Weng "Harness Engineering for Self-Improvement" | blog | 1 | 1 (survey row, confirms gap) |
| 25 | Autopoiesis / HEAR / INFRAMIND / CONCUR forward citers | WebSearch (extended + standard) | 29 | 0 new (MARS, Policy-Driven Runtime Layer, Resident KV Claims already in r1); 1 companion (2606.01387) |
| 26 | 12 queries q41-q52 (harness+telemetry, agentic overload, load-aware agent, self-healing serving, vLLM+agent metrics, rate limit + agent, operator playbook, cloud-edge self-evolving agent, inference server + harness) | arXiv API | 150 | 2 (ReliabilityBench, Self-Healing Router) |
| 27 | Arxiv title lookups for 7 RAAAI workshop papers | arXiv API | 7 | 0 (not on arXiv; used NeurIPS pages instead) |
| 28 | ATC'26, EuroSys'26 accepted lists | usenix.org, 2026.eurosys.org | n/a | ATC'26 technical-sessions page returns 404; EuroSys'26 page has only a tentative schedule. Not checked. |
| 29 | NeurIPS'26 AgenticOS accepted papers | OpenReview API, WebSearch | n/a | blocked (OpenReview challenge, 403); not listed on neurips.cc |
| 30 | Adversarial full-text read of Autopoiesis, AHE, HEAR, INFRAMIND, SchedCP | arxiv.org/html (curl + grep) | 5 | see Adversarial check |

### (b) Saturation per sub-area

- **Forward citations of seeds: saturated where data exists.** The AHE and HarnessFix citers (about 200) are almost all accuracy-only harness evolution; only RiseHarness (as a representative), SHarP and EFC touch efficiency. Autopoiesis, HEAR, INFRAMIND, SDO, IteRate and TRACE have zero or one S2 citer. WebSearch found no further citers, because these papers are 1-6 months old. **Re-run in ~3 months.**
- **Round-1 leads: done**, apart from four items with no canonical page anywhere: CompQ, SOPSmith, RunbookFX, and "Fast or Slow? ... Resilient AI Systems", which exists only as an IFIP talk title.
- **arXiv recency (2608-2611): saturated.** Three query batches (q1-15, q21-36, q41-52) added 15, 5 and 2 relevant rows, and the last batch contained no rows at relevance 2. Most hits are accuracy-only harness evolution, which the brief excludes, or serving mechanisms at relevance 1.
- **Venue sweep:**
  - **Done:** OSDI'26, NSDI'26, SOSP'25, HotOS'25, MLSys'26, SIGCOMM'26, ASPLOS'26 (titles only), AgenticOS@ASPLOS'26 and AgenticOS@SOSP'26, MLforSys@NeurIPS'25 and '26, and Resource-Aware Agentic AI@NeurIPS'26.
  - **Not done:** EuroSys'26, ATC'26 and the NeurIPS'26 AgenticOS workshop (lists not accessible), and ICLR'26/ICML'26/NeurIPS'25 main tracks (only keyword WebSearch, no full list scan).
  - **Saturation:** the venue lists added relevance-1/2 building blocks, but none at relevance 3.

### (c) Closest 5 (after round 2)

No work found in round 2 is closer to the full target loop than Autopoiesis or AHE:
- Target: an edge store of agent traces, HW metrics and congestion signals; LLM root-cause analysis of systematic degradation; synthesized compensation tools placed in the cloud harness; acting under live load; repeating.
- Every round-2 candidate covers at most three of these six parts.

1. **Autopoiesis (2604.07144), unchanged at #1 for the serving half.**
   - Covers: live telemetry snapshots, an LLM rewriting serving-policy code, simulator replay, hot-swap into the live data plane, and a continuous loop.
   - Misses: harness or agent deployment and explicit RCA.
   - See the adversarial check for a caveat on how its results were measured.
2. **AHE (2604.25850), unchanged at #1 for the harness half.**
   - Covers: an LLM debugger that distills root causes from traces, and synthesized tools and middleware in the harness, in a closed loop.
   - Misses: everything on performance and load. The objective is pass@1.
3. **Reef (GitHub, new, relevance 2).**
   - The only system found where the inference-serving layer itself stores every interaction and trace and then evolves and publishes the agent harness (`GET /reef/harness`). This is architecturally the closest to "the serving side learns and pushes harness updates".
   - Misses: its signal is task-quality feedback. It has no latency, load or congestion telemetry and no RCA, and it uses offline recipes (GEPA, Meta-Harness).
4. **SysContract (MLforSys@NeurIPS'26, new, relevance 2) together with INFRAMIND, HEAR and CONCUR.**
   - SysContract shows that LLM agents writing serving and scheduler configs violate TTFT and KV envelopes, and that prompt-level invariants are not enough (serving hold only 0.40). Machine-checked admit/rollback/stop gates are required.
   - This is the deployment discipline the target's compensation tools would need, and it independently supports the user's finding that advisory signals get ignored.
   - Misses: it does not diagnose and it does not synthesize harness tools.
5. **A-JIT (2609.10248, new, relevance 2) and SkillCache (NeurIPS'26 RAAAI, new, relevance 2).**
   - A-JIT is a vision of a runtime harness with an embedded agent that observes live traces and synthesizes tools and workflows on the fly.
   - SkillCache compiles one trace into an executable fast path inside Claude Code, cutting tokens 45-53%.
   - Both show "trace → synthesized harness artifact → efficiency", but neither uses serving telemetry or congestion RCA.

Also notable at relevance 2:
- **Meta Capacity Efficiency agents** (production performance-regression RCA producing fix PRs).
- **PROMPTS** (MLSys'26: LLM bottleneck diagnosis of LLM serving from profiler data).
- **gigiprofiler** (OSDI'26: LLM-assisted performance-bottleneck attribution).
- **FlashInfer-Bench** (MLSys'26: LLM-generated kernels hot-substituted into vLLM/SGLang from real serving traces).
- **Expert in Residence** (always-on LLM OS tuning).
- **VibeWAF** (an LLM off the hot path synthesizes rules for a fast engine; latency falls from 6.5 s to <0.4 s).
- **Cognitive Friction** (congestion-priced stopping rule for tool-using agents).
- **MABP** (queue-memory backpressure for agentic service networks).
- **Observability Debt** (the schema a joined trace + telemetry + cost record needs).
- **AgentTwin** (synthesizes executable evaluators from production telemetry).

### Adversarial check of the 5 closest (full text read at arxiv.org/html)

#### Autopoiesis (2604.07144)

- **(a) Telemetry.** Only a workload/cluster-availability window. Quote: "the data plane periodically takes a snapshot of the recent runtime trace (i.e., a sliding window of request arrival patterns and cluster resource availability)". No KV, queue-pressure, HW-counter or agent-trace signals. This agrees with r1_policy_synth. r1_aiops's phrase "arrivals + cluster state" is consistent.
- **(b) LLM RCA.** No. The LLM compares cost breakdowns of parent and child candidates: "By comparing the cost breakdown of a child candidate against its parent, the LLM can identify how its code modification shifted the cost structure." This is candidate-level attribution, not system root-cause analysis. Agrees with round 1.
- **(c) What is generated and where it runs.** Python `schedule`/`should_reschedule` functions, hot-swapped into the serving data plane: "it writes the new code to a shared staging area, and the data plane asynchronously loads the replacement at its next monitoring time step". Not a harness. Agrees.
- **(d) Online.** Yes: "the asynchronous control plane continuously leverages the L[LM]"; candidates are scored by replaying the snapshot in a roofline simulator.
- **(e) Closed loop.** Yes, with warm-start re-evolution (evolution time down up to 83%).
- **Disagreement / caveat (new).**
  - The headline results ("up to 53% and on average 34%") are reported as "trace completion time ... as defined in our execution model (Equation 13)", and the cost components come from the "roofline-based simulator" (App. B). I found no statement that the end-to-end numbers were measured on physical GPUs, and no report of simulator error.
  - By contrast, INFRAMIND states "None of the evaluation is simulated."
  - Round 1 reported the gains as measured results. **Treat them as model-estimated until checked.**
  - Also, "data/control plane" is not an edge/cloud split. Both planes belong to one serving deployment. However, the synthesis LLM is a small open model (GLM-4.7-Flash), which makes the edge placement plausible.

#### AHE (2604.25850)

- **(a) Telemetry.** Agent trajectories only ("millions of raw trajectory tokens"). Tokens/trial is reported as a metric. "Infrastructure-aborted or timed-out trials count as failures". No system telemetry.
- **(b) LLM RCA.** Yes, for task failures. The Agent Debugger yields "structured root causes rather than raw logs". Each manifest entry names "the failure evidence, the inferred root cause, the targeted fix, and a predicted impact".
- **(c) What is generated.** Edits to 7 harness component types (system prompt, tool description, tool implementation, middleware, skill, sub-agent config, long-term memory) in a NexAU harness. Ablation: tools, middleware and memory carry the gains.
- **(d) Offline.** Ten iterations on Terminal-Bench 2 (pass@1 69.7→77.0). Evolution runs between benchmark rounds.
- **(e) Closed loop.** Yes. Every edit is a falsifiable prediction verified next round and reverted if wrong.
- **Agreement.** Agrees with round 1. One nuance: the evolved harness also uses fewer tokens than the seed on SWE-bench-verified, but that is a side effect. The optimization target is pass@1, and the post-mortem notes the timeout budget was fitted during evolution ("timeout-budget coupling as a generalization hazard").

#### HEAR (2610.06597)

- **(a) Telemetry.** Engine-reported "cache residency, request queues, resource pressure, and supported execution modes" and execution outcomes. The harness sends workflow intent, waiting constraints and future-reuse hints.
- **(b) LLM RCA.** None.
- **(c) What is generated.** Nothing. Policies are hand-written. The protocol "standardizes the bidirectional interaction contract ... without prescribing policies". The two instantiated policies are Cache-Aware(+Guard) ordering, inspired by KVFlow, and role-specific execution-mode selection. "Policies and method-specific parameters are frozen before evaluation".
- **(d) Online.** Yes: cache-aware runtime coordination is an "online policy".
- **(e) Closed loop.** Only the control feedback between harness and engine, with no policy evolution.
- **Agreement.** Agrees with round 1, including the numbers (1.61x batch speedup and 2.23x lower median TTFT on SCBench; 1.23x and 2.45x on BrowseComp-Plus and DeepResearchBench). HEAR is the natural deployment surface for synthesized policies but does not itself synthesize any.

#### INFRAMIND (2606.11440)

- **(a) Telemetry.** "per-model queue depth, end-to-end latency and KV cache utilization polled from vLLM's /metrics endpoint", refreshed before each agent call.
- **(b) LLM RCA.** None.
- **(c) What is generated.** Nothing. The executor is trained with PPO and the planner with REINFORCE ("we train the executor using the PPO clipped surrogate ... and the planner using baseline-normalized REINFORCE"), with EDF queue priority. Fully live: "None of the evaluation is simulated."
- **(d) Online.** Yes.
- **(e) Closed loop.** No. Training happens once, offline.
- **Nuance versus round 1.**
  - r1_self_evolving says "the agents never see load" and "no wait/hold action". The second is correct: waiting appears only as a cost ("waiting at one step reduces the budget for every later step"), not as an action.
  - However, load does reach the agents indirectly. The executor picks the agent's reasoning prompt (Flash, Concise or DeepThink) and the model per call from live telemetry. So the prompt each agent receives is load-dependent, although the agent is never told the load.

#### SchedCP (2509.01245)

- **(a) Telemetry.** "tiered access to system performance data: (1) ... pre-processed summaries (CPU load, memory usage), (2) ... Linux profiling tools (perf, top) and dynamically attachable eBPF".
- **(b) LLM RCA.** Workload characterization, not degradation RCA. The Observation Agent produces profiles such as "CPU-intensive parallel compilation ... targeting makespan minimization".
- **(c) What is generated and where it runs.** eBPF sched_ext schedulers (configure, patch, or compose new), deployed into the kernel through the Execution Verifier.
- **(d) Online or offline.** Optimization runs once per workload, triggered "automatically ... when applications deploy". It is not a continuous loop under changing load.
- **(e) Closed loop.** In-session in-context RL. The Learning Agent "updat[es] the repository with refined metrics, deployment contexts, and documented antipatterns".
- **Disagreement with round 1.**
  - Round 1 (aiops and policy_synth logs) credits SchedCP with "canary deploy with auto-revert". The paper describes it only as a design feature of the verifier: "issues signed deployment tokens for monitored canary deployments with circuit breakers to revert if performance degrades".
  - The evaluation covers configuration (1.79x on kernel build) and new LJF scheduler synthesis (20% latency) only. **Canary/revert is not evaluated.**
  - The MCP tools are hand-written, as round 1 states.

### Saturation verdict for topic 2 overall

**Saturated for the question "does a paper implement the full loop?" The answer is no.**

- Round 1 (528 papers) and round 2 (+72) converge on the same split, and no new paper bridges it:
  - LLM-synthesized serving/OS policies from telemetry: Autopoiesis, SchedCP, FlashInfer-Bench, Expert in Residence, Glia.
  - LLM diagnosis of performance from telemetry: PROMPTS, Meta capacity agents, gigiprofiler, XiHe, Knsight.
  - Harness self-evolution from traces for accuracy: AHE plus about 200 citers. The few with an efficiency angle are Reef, SkillCache, SHarP and Tool-Making.
  - Hand-designed harness-side load control: INFRAMIND, HEAR, CONCUR, Patient Bytes, MABP, Cognitive Friction.
- An independent survey also reports no load-signal-driven harness evolution: Lilian Weng's July 2026 harness self-improvement survey. The jxzhangjhu blog post that round 1 noted was not read this round.
- The last arXiv batch added no rows at relevance 2.
- Remaining risk is concentrated in very recent workshop lists and in citers that do not exist yet:
  - The NeurIPS'26 AgenticOS workshop list is pending (OpenReview blocked).
  - Autopoiesis, HEAR, INFRAMIND and SDO have almost no citers yet.

### (d) Still unchecked / leads

- **Venues not scanned:**
  - EuroSys'26 accepted list. The official page has only a schedule; github.com/JiusiServe/InferMatrixCopilot/issues/57 has a list.
  - ATC'26. The technical-sessions page returns 404.
  - NeurIPS'26 AgenticOS workshop (OpenReview group NeurIPS.cc/2026/Workshop/AgenticOS, challenge-blocked).
  - ICLR'26, ICML'26 and NeurIPS'25 main-track lists. Only keyword searches were run; CONCUR is confirmed at ICML'26 (PMLR v306).
- **Forward citations to re-run in about 3 months.** These seeds have 0-1 citers so far:
  - Autopoiesis 2604.07144
  - HEAR 2610.06597
  - INFRAMIND 2606.11440
  - SDO 2610.14547
  - IteRate 2605.02542
  - TRACE 2610.01887
  - Readiness/Release 2609.10964
- **No canonical page found anywhere:**
  - CompQ (QoE-aware compensation for resumed LLM requests; S2 title only)
  - SOPSmith
  - RunbookFX
  - "Fast or Slow? Human-Inspired Self-Evolving Framework for Resilient AI Systems" (IFIP WG10.4 talk only)
  - "Survival of the Luckiest"
  - "Strategic Demand and Congestion Pricing in Shared Computation Systems"
  - "Impact of Scheduling for Terminal Agent Workloads on Unified-Memory Workstations" (MLSys'26 listing; only a BU talk announcement found)
- **Workshop rows verified at abstract level on neurips.cc but with authors not read:** Observability Debt, SkillCache, Failure Signals, Expert in Residence, LLMVisor, InfraGym, Agentic Bridge, SysContract, AgentTwin, bottleneck shifts, latency budget, ExaAgent, GORGO.
- **Title only:** Loom (SOSP'25) was recorded from the accepted list alone; its abstract was not read.
- **Lower-priority items seen but not recorded:**
  - ORACLE 2607.22465 (verifier-calibrated routing)
  - DualPath 2602.21548 (agentic KV storage I/O; also SIGCOMM'26)
  - VAMP 2609.13537, KernelFlume 2606.29207, Hot-Cold HBF tiering 2609.25782 (serving mechanisms for topic 1)
  - UNISON 2609.09643, WAKMan 2610.36355, SARIA 2610.31169, PEEK 2607.02525, HeraSys 2607.22578, FATE 2605.07238 (agent-serving mechanisms)
  - "Unified GPU Memory for MoE Inference" (MLforSys'26)
  - ProfInfer (MLSys'26 eBPF LLM inference profiler)
  - AliYANG (SIGCOMM'26)
  - SatCraft 2610.29588 (already in r1)

---

# Agent log: `r3_gapfill` (round 3, 2026-11-01 16:40 UTC, rerun approved by the user; stopped by the session safety check after about 8 minutes)

## r3_gapfill log (round 3 gap fill), started 2026-11-01 16:39 UTC

**Stopped early at about 17:05 UTC.** The session safety check refused a tool call: a curl of the Ascend/memfabric_hybrid README from GitHub, plus a grep of known_all.txt. The refusal said it would keep firing for the rest of the conversation. As the orchestrator instructed, I stopped and did not retry or route around it. Afterwards I only wrote these two scratchpad files. Done: task A, about 80 %. Not started: tasks B, C and D.

Output: 15 rows in r3_gapfill.jsonl, all topic 1. 14 are verified; 1 is unverified (MemFabric_Hybrid).

### (a) Query table
| # | query | source | hits scanned | new relevant |
|---|---|---|---|---|
| 1 | UnifiedBus UB Base Specification 2.0 UMMU memory addressing chapter | WebSearch (extended) | 9 | 0 (no primary spec text; spec download sits behind a license page on unifiedbus.com) |
| 2 | openEuler OBMM ownership-based memory management UB libummu documentation | WebSearch (extended) | 9 | 2 leads |
| 3 | unifiedbus.com/en/software/ub-os-component (curl; WebFetch truncated) | vendor page | 1 | libummu + OBMM descriptions |
| 4 | Gitee API: openeuler/obmm README, doc/libobmm.md, doc/obmm_import.md; openeuler/ummu README | Gitee | 4 docs | 2 rows (OBMM, libummu) |
| 5 | unifiedbus.com home: spec links | curl | 1 | 0 ("UB Base Specification 2.1 Now Available", behind a license page) |
| 6 | UALink white paper January 2026 57-bit address space memory semantics | WebSearch (extended) | 9 | 2 (white paper, full 1.0 evaluation-copy spec) |
| 7 | UALink 2.0 specification released memory pooling in-network collectives 2026 | WebSearch (extended) | 9 | 1 (2.0 press release) |
| 8 | PDF text: UALink white paper (16 pp), UALink_200 1.0 evaluation copy (253 pp), 2.0 press release | ualinkconsortium.org | 3 | 3 rows |
| 9 | NVIDIA developer blog Inference Context Memory Storage BlueField-4 KV cache G3.5 tier | WebSearch | 7 | 1 (CMX developer blog, fetched) |
| 10 | NVIDIA Dynamo KV router global KV index radix tree KV events documentation | WebSearch | 7 | 1 (router design; latest URL 404, dev URL fetched) |
| 11 | "H3" hybrid HBM HBF cost-efficient LLM inference SK hynix arXiv | WebSearch | 10 | 1 new (Oxford CAL'26 HMA) + H3 note row |
| 12 | OpenAlex DOI lookups 10.1109/LCA.2026.3660969, 10.1109/LCA.2026.3723326 | OpenAlex | 2 | abstracts |
| 13 | SK hynix MARS rack memory AI inference KV cache CXL HBF 2026 | WebSearch (extended) | 10 | 0 (press only) |
| 14 | news.skhynix.com MARS memory-centric AI rack system OCP 2026 | WebSearch + WebFetch newsroom | 20 | 1 |
| 15 | Samsung CMM-D CXL switch KV cache offload white paper | WebSearch + PDF | 10 | 1 |
| 16 | Enfabrica EMFASYS MUG'25 tutorial memory tiering KV cache CXL | WebSearch | 10 | 0 (tutorial not found; product press already known) |
| 17 | Mooncake transfer engine Kunpeng UB transport aionw.github.io | WebSearch; aionw page now 404; GitHub API listing of Mooncake transport docs | 10 + 9 docs | 2 (UB transport, MemFabric transport) |
| 18 | openYuanrong datasystem HBM DRAM SSD multi-tier KV cache object store unified address | WebSearch (extended) + WebFetch docs 0.7.0 | 10 | 1 |
| 19 | memfabric_hybrid Huawei smem global unified address HBM DRAM pooling Ascend superpod | WebSearch (extended) | 9 | 1 (unverified: fetch refused) |

Saturation: none of the sub-areas saturated; work was cut by the safety refusal. Vendor docs were still turning up 1–2 new items per query.

### (b) Rows written (topic 1)
| id | relevance | unified_table | key finding |
|---|---|---|---|
| obmm_openeuler | 2 | partial | Remote host DRAM is imported over UB as a remote NUMA node. Linux page tables plus mbind/move_pages then migrate pages between local and borrowed DRAM, and an ownership bit replaces cross-node coherence. This is VM over T3/T4, CPU memory only. |
| libummu_openeuler | 1 | no | UMMU = per-device translation and permission tables keyed by token ID |
| ualink10_spec_full | 2 | partial | Correction to the report (see §c) |
| ualink_wp_jan26 | 1 | partial | Claims a "57-bit unified address space (128 PB)" |
| ualink20_pr | 1 | no | 2.0 adds in-network compute, manageability and chiplets; no memory pooling or paging |
| nv_cmx_blog26 | 2 | no | ICMS has been renamed CMX. The "G3.5" Ethernet-attached flash tier sits between G3 (local SSD) and G4 (shared storage). Block state lives in the frameworks' KV managers plus NIXL. |
| dynamo_router_design | 1 | partial | The "global index" is a prefix tree of block hashes with a worker id per node. It does prefix matching only, no translation. |
| h3_cal26_note | 2 | partial | Clarifies a known item (see §c) |
| hma_hbf_cal26 | 2 | partial | New: Oxford CAL'26, HBM + HBF with a prediction-based migration policy on one GPU |
| skhynix_mars_ocp26 | 1 | no | CXL rack pool, full KV on SSD, predictive DRAM preload. HBM is not pooled. |
| samsung_cmmd_wp26 | 1 | no | A CXL-switch pool appears to a single host as NUMA nodes, used as an LMCache backend |
| mooncake_ub_transport | 1 | no | UB is just one more transport (URMA). Segments are still Mooncake's own. |
| mooncake_memfabric_transport | 1 | no | Batch copies through Huawei MemFabric smem_trans |
| openyuanrong_datasystem | 2 | no | HBM/DRAM/SSD multi-level cache addressed by object key. HBM is held as "heterogeneous objects". Eviction is LRU, persistence is write-through or write-back, and cross-node traffic is TCP only for now. No unified address space. |
| memfabric_hybrid_ascend | 2 (unverified) | partial? | Per a search summary: a 64-bit global VA over HBM + DRAM across nodes on Ascend superpods. The fetch was refused. |

### (c) Adversarial check
I did not get to the task C full-text re-checks: xDeepServe/StrataCL translation site, the CM384 EMS HBM tier, EGM/IMEX for remote nodes, and G10's multi-GPU section. The primary sources I did read give these corrections and confirmations to the report:

1. **UALink. Correction to the report's §2 matrix row and the §3.1 sentence "host DRAM and flash are out of scope".** UALink_200 Rev 1.0 (evaluation copy), Preface p. 19: "Requests from a given Accelerator in one System Node to a remote Accelerator in a different System Node may access any Accelerator memory or Host Memory in the Remote System node ... Hosts accessing any memory in any remote System Nodes shall not be supported."
   - So T4 (remote host DRAM) is reachable by accelerators and should read "partial", not "·".
   - §1.4, p. 27 also says the GVA→NPA→link-MMU path is illustrative: "It is only for illustration. This specification leaves the address translation as an implementation choice as Switches use identifier-based routing ... An implementation can also opt for a global addressing model that is flat."
   - The report's "translation (VA→NPA, then NPA→SPA in a Link MMU)" should be described as the spec's example model, not a mandated mechanism.
   - Coherence (§1.5) is software only: accelerators "keep coherence through software by clearing caches at the right kernel boundaries".
2. **UALink white paper vs the spec.** The January 2026 white paper (TASK Consultancy) says UALink establishes "a 57-bit unified address space (128 PB) accessible across all fabric endpoints ... without vendor-specific translation layers".
   - The spec itself leaves translation to the implementation. Treat the white paper's claim as marketing.
   - The report's "pod-wide network physical address space" wording is fine.
3. **H3. Clarifying a known item.** The report's §3.3 says H3 "puts HBM and HBF in one address space on one accelerator". The abstract (via OpenAlex) only says: "By storing read-only data in HBF and other data in HBM, H3-equipped systems can process more requests at once".
   - That describes static placement by data class, with no migration or page table.
   - The "one address space" claim rests on press write-ups ("shared addressing"). The full text was not accessible: IEEE returns 403, and there is no preprint.
   - Suggested rewording: "places HBM and HBF side by side; read-only data is statically placed in HBF".
4. **Confirmed: NVIDIA CMX/ICMS has no unified index.** The developer blog (16 Mar 2026) says "KV tiering spans GPU HBM, host memory, local SSDs, CMX, and network storage". Frameworks "use their KV block managers together with NIXL". The blog describes no global address space or index, and peer GPU memory is not mentioned.
5. **Confirmed: the Dynamo "global index" is a routing prefix tree, not a translation table.** Quote: "We modify the original prefix tree by also storing the worker id on each node". There is no tier field per block.
6. **Confirmed: the openEuler MemFabric/OBMM layer covers host memory only.** The OBMM README says it "supports cross-node physical memory sharing", with remote memory brought online as a remote NUMA node. It does not mention NPU HBM.
   - Caveat: the separate Ascend **MemFabric_Hybrid** library reportedly does pool HBM + DRAM under one global VA (unverified). If that is confirmed, the report's statement that the UB Service Core's MemFabric is "for host memory only" stays true, but Huawei would also have a software HBM + DRAM global-VA pool that the report does not list.

### (d) Does anything found come closer than the reports' current closest works?
- **Topic 1. No verified item comes closer.** Every verified item covers at most T0/T3/T4/T5 with key-based indexes (openYuanrong, CMX, MARS, CMM-D) or a CPU-only VM over T3/T4 (OBMM). That matches the report's existing pattern.
- **One unverified lead could match criterion 1 (one namespace) over T0–T4: Ascend MemFabric_Hybrid** (github.com/Ascend/memfabric_hybrid). It is described as a 64-bit global virtual address over HBM and DRAM across nodes.
  - Even if confirmed, nothing found so far shows faults, migration, eviction or a flash tier.
  - So it would be a peer of the NVIDIA MNNVL row (#2 closest), not a full match.
- **Topic 2.** Nothing was checked (task D was not reached).

### (e) Still unchecked
- **Task A leftovers:**
  - the UB Base Specification 2.0/2.1 text (behind a license page on unifiedbus.com);
  - the Enfabrica EMFASYS MUG'25 tutorial (not found by search);
  - the Vera Rubin NVL144 / NVLink Fusion memory model (not started);
  - the MemFabric_Hybrid README, doc/pythonAPI.md and hybm source (fetch refused);
  - the SK hynix H3 full text (IEEE 403, no preprint).
- **Task B, all leads:** Tierce and Anchor (SOSP'26), Cxlalloc (ASPLOS'26), GrOUT (IPDPSW'24), AgileOS (2606.06697), DxPU (2310.04648), Alibaba PolarKV, MemNetAI'26 workshop papers, PMKS (EEI 2026), MPiCO (GLSVLSI'26), DeepPiC (ASP-DAC'26), Phantom Walk (DAC'26, DOI 10.1145/3770743.3804244), HUVM forward citations, and Mooncake citations beyond offset 1000.
- **Task C, all re-checks:**
  - xDeepServe 2508.02520 / StrataCL 2607.26444: does translation happen in the NPU MMU or in UB switch decode?
  - CloudMatrix384 2506.12708 EMS: is HBM a managed tier?
  - NVIDIA EGM/IMEX: remote-node EGM import via fabric handles (T4)?
  - G10 2310.09443: what its multi-GPU section actually says.
- **Task D, all venue sweeps:** EuroSys'26, ASPLOS'26, ISCA'26, MICRO'25, HPCA'26, ATC'25/26, MLSys'26, SC'25 and HotStorage'26 for both topics; ICLR'26 and ICML'26 for topic 2.
