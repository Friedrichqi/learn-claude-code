# Related work: one memory table over a six-tier memory hierarchy, managed as virtual memory

Background research, 2026-11-01: 05:05–06:10 UTC (rounds 1–2, 8 search agents) and 16:40–16:50 UTC (round 3, gap fill).

**Question (user):** Is there a paper that keeps one unified memory space, a "memory table", over a hierarchical memory architecture with these six tiers?

| tier | memory | how it is reached |
|---|---|---|
| T0 | the accelerator's own HBM | local |
| T1 | peer HBM in the same node | the scale-up link ("KV Link") |
| T2 | HBM in other nodes | the switch ("KV Switch") |
| T3 | local DRAM | host |
| T4 | DRAM in other nodes | network or fabric |
| T5 | flash | local or remote |

If such a table exists, the whole hierarchy can be run with the virtual-memory paradigm: translation, faults, migration, eviction and protection.

**Reading of the user's terms.** No fabric called "KV Link" or "KV Switch" exists in any paper, spec or product. arXiv searches for those strings return only *KVLink*, a technique for reusing KV caches, [2502.16002](https://arxiv.org/abs/2502.16002), unrelated here. This note therefore reads them as follows:
- **"KV Link"** is the scale-up accelerator link: NVLink / NVLink-C2C, UALink, AMD Infinity Fabric, or Huawei UB/HCCS.
- **"KV Switch"** is the switch layer that extends that link across nodes: NVLink Switch in GB200 NVL72 and DGX GH200, a UALink switch, the UB switches of CloudMatrix384, or a CXL switch.

**Method.**
- **Round 1:** three parallel sweeps of about 45 minutes each.
  - Hardware/fabric addressing and GPU virtual memory.
  - OS/virtual memory for tiered and disaggregated memory, including flash.
  - LLM KV-cache tiering and pooling, including the open-source designs.
- **Round 2:** a citation snowball and gap fill.
- **Sources:** arXiv API queries; Semantic Scholar citations of the closest works; OpenAlex/Crossref for ACM/IEEE metadata (ACM/IEEE pages return 403 to scripts, and the DBLP API was bot-blocked); conference program pages (OSDI'26, SOSP'25/'26, NSDI'26, FAST'26 and others); vendor docs, specs and GitHub design docs.
- **Rows:** 492 for this topic in [`lit_review_1101_papers.csv`](lit_review_1101_papers.csv): 37 directly relevant, 128 building blocks, 327 background.
- **Verification:** every arXiv ID was re-resolved through the arXiv API with a title match. DOIs were matched by title through Crossref, and other URLs were checked for HTTP 200. Only one row stays unverified (Phantom Walk, DAC'26; its DOI does not resolve yet), and it is not cited below.
- **Bodies read:** for every relevance-3 item, the agents read the body, not only the abstract. Each row's tier claims carry a quote in the CSV `evidence` column.
- **Query log:** the full log is in [`lit_review_1101_search_log.md`](lit_review_1101_search_log.md).

**Coverage caveat (read this).** The session's safety check stopped two follow-up runs for this topic early, each after its web calls began to be refused.
- **Round 2:** the citation snowball of the closest works, the arXiv sweep of Aug–Nov 2026 and the openEuler primary document were done.
- **Round 3**, a rerun the user approved: most of the vendor specs and docs were done (UALink, OBMM, NVIDIA CMX, Dynamo router, HBF, Samsung CMM-D, openYuanrong). Neither run worked around the refusals.
- **Not done in either:** the 2025–26 venue lists for ISCA, MICRO, ASPLOS, EuroSys, HPCA, ATC, MLSys and SC; full-text re-checks of four closest works; a set of leads, including the unverified MemFabric_Hybrid. All are listed in §7.

Round 1 had already reached saturation in most sub-areas (§7). A missed item is therefore most likely a vendor document or a 2026 venue paper.

**Short answer: no.** No paper, spec or system keeps one table that translates, faults, migrates and evicts across all six tiers. Each piece exists, but in a different layer:

| piece | example | tiers it covers |
|---|---|---|
| (a) fabric-level global *physical* addressing | Huawei UB, NVIDIA multi-node NVLink with fabric handles and EGM, the UALink network physical address space | T0–T2, sometimes T3/T4 |
| (b) virtual memory whose page table also points into flash | G10, FlatFlash, FlashMap, Dragon | T0/T3/T5 on one node |
| (c) KV-block tier managers | Mooncake, CloudMatrix EMS, Dynamo KVBM, LMCache, HiCache | DRAM/SSD across nodes |
| (d) a switch that holds the page table | MIND | CPU DRAM only |

The nearest physical match to the user's picture is **Huawei CloudMatrix384**. Its UB fabric gives one address space over every NPU's HBM and every CPU's DRAM in the supernode. Its EMS pool adds an SSD tier under the DRAM pool. But the address space, the KV pool and the SSD tier are three separate mechanisms with three kinds of metadata, and none of them does demand paging. The nearest *virtual-memory* match is **G10** (MICRO'23): one GPU page table whose entries point to HBM, host DRAM or flash. It is single-GPU and simulated. So the gap the user describes is open. A 2026 survey of KV-cache management reaches the same conclusion (§5).

---

## 1. What "one table over six tiers, managed as virtual memory" requires

A design has to provide five things together to count as the user's idea. They are the matrix columns in §2:

1. **One namespace.** Every byte of KV (or tensor) state has one logical address, whichever of T0–T5 holds it.
2. **One table.** A single translation structure, possibly distributed or cached, maps logical to physical address plus tier. It is not one index per tier.
3. **Virtual-memory mechanisms on that table:**
   - translation on the data path, done by a GPU MMU, a switch, a NIC or a memory node;
   - fault or miss handling;
   - migration and promotion/demotion between tiers;
   - eviction or replacement;
   - protection and sharing;
   - coherence or invalidation when an entry moves.
4. **All six tiers in that table.** Peer and remote HBM are *managed tiers*, not just transfer paths.
5. **A policy** that places and moves data across tiers using the table.

---

## 2. Coverage matrix of the closest works

Legend:
- **Y** = the tier is in the work's one namespace/table and managed;
- **p** = reachable or used, but outside the table, managed by a separate mechanism, or not managed;
- **·** = absent.

"VM mechanisms" lists only the mechanisms that act on the unified structure.

| work | T0 | T1 | T2 | T3 | T4 | T5 | one table? | VM mechanisms on it | where translation lives | eval |
|---|---|---|---|---|---|---|---|---|---|---|
| **Huawei CloudMatrix384 + EMS** ([2506.12708](https://arxiv.org/abs/2506.12708)) | p | p | p | Y | Y | p | partial: UB physical space + separate EMS DHT + SSD tier inside the pool | eviction DRAM→SSD (LRU), sharing; no faults | NPU page tables + UB switch decode; EMS key→address DHT | production |
| **xDeepServe / StrataCL on CM384** ([2508.02520](https://arxiv.org/abs/2508.02520), [2607.26444](https://arxiv.org/abs/2607.26444)) | Y | Y | Y | Y | Y | · | partial: one physical space, per-device page tables, explicit registrations | translation, sharing | NPU MMU + UB switches | production |
| **NVIDIA MNNVL: CUDA fabric handles + IMEX + EGM; DGX GH200** ([VMM](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/virtual-memory-management.html), [IMEX](https://docs.nvidia.com/multi-node-nvlink-systems/imex-guide/overview.html), [EGM](https://docs.nvidia.com/cuda/archive/12.6.1/cuda-c-programming-guide/egm.html), [GH200](https://developer.nvidia.com/blog/announcing-nvidia-dgx-gh200-first-100-terabyte-gpu-memory-system)) | Y | Y | Y | Y | Y | · | partial: one GPU VA namespace, explicit map/unmap | translation (VA→PA→fabric address), protection | GPU MMU; importer uses the exporter's page tables | production |
| **UALink 1.0 + amdgpu UALink driver + reverse-translation study** ([spec](https://ualinkconsortium.org/blog/ualink-200g-1-0-specification-overview-802/), [patches](https://ratatoskr.run/amd-gfx/2026/08/17487069/t), [2604.02473](https://arxiv.org/abs/2604.02473)) | Y | Y | Y | · | p | · | partial: pod-wide network physical address space | translation (illustrated as VA→NPA, then NPA→SPA in a Link MMU; the spec leaves it to implementations), remote TLB shootdown | accelerator MMU + Link MMU at the target | spec + simulation |
| **G10** (MICRO'23, [2310.09443](https://arxiv.org/abs/2310.09443)) | Y | · | · | Y | · | Y | **yes** | translation, demand fault, migration, eviction, prefetch | GPU UVM page table (FTL mapping folded in) | simulation, 1 GPU |
| **MIND** (SOSP'21, [2107.00164](https://arxiv.org/abs/2107.00164)) | · | · | · | p | Y | · | **yes** (in the switch) | translation, demand fault, protection, coherence | programmable switch data plane | 1 rack, CPU only |
| **HUVM / memHarvester** (ATC'22, [link](https://www.usenix.org/conference/atc22/presentation/choi-sangjin)) | Y | Y | · | Y | · | · | **yes** (UVM) | demand fault, migration, eviction (peer HBM as victim cache), prefetch | NVIDIA UVM driver | real 1 node |
| **UlaCache** ([2610.29820](https://arxiv.org/abs/2610.29820)) | Y | Y | · | Y | · | · | **yes** (logical KV addresses) | translation, on-demand fetch, migration, LRU eviction | engine software | real H200/B300 |
| **Harvest** ([2602.00328](https://arxiv.org/abs/2602.00328)) / **EMA** ([2609.27040](https://arxiv.org/abs/2609.27040)) | Y | Y | · | Y / · | · | · | yes / partial | translation, revocation-driven invalidation, reclaim | engine software / memory-slice processes | real 1 node |
| **Mooncake + Mooncake Store/TE** (FAST'25, [link](https://www.usenix.org/conference/fast25/presentation/qin), [design](https://github.com/kvcache-ai/Mooncake/blob/main/docs/source/design/store/mooncake-store.md)) | Y | p | p | Y | Y | Y | partial: key → replica index | key→location lookup, LRU eviction, leases, pins, replication | master service (lookup before an explicit transfer) | production |
| **Dynamo KVBM + NIXL** ([KVBM](https://docs.nvidia.com/dynamo/v1.1.1/design-docs/component-design/kvbm-design), [NIXL](https://github.com/ai-dynamo/nixl)) | Y | p | p | Y | Y | Y | partial: per-instance block registry | lookup, LRU/TinyLFU eviction, promotion, RDMA pull | leader's registry keyed by sequence hash | open source |
| **LMCache** ([2510.09665](https://arxiv.org/abs/2510.09665)) / **SGLang HiCache** ([doc](https://docs.sglang.io/advanced_features/hicache_design.html)) | Y | p | p | Y | Y | Y | partial | lookup/move/pin, eviction, prefetch | controller / HiRadixTree (L1/L2 only) | open source |
| **MemServe** ([2406.17565](https://arxiv.org/abs/2406.17565)) / **Infinite-LLM** ([2401.02669](https://arxiv.org/abs/2401.02669)) | Y | Y | Y | Y | p | · | partial: pool API, best-effort global index | swap in/out, transfer / HBM borrowing | global scheduler / gManager + rManagers | real cluster |
| **Aqua** (ASPLOS'25, [2407.21255](https://arxiv.org/abs/2407.21255)) | Y | Y | p | Y | · | · | no (coordinator tracks offloaded tensors) | explicit offload/prefetch | coordinator | real NVSwitch box |
| **Beluga** (SIGMOD'26, [2511.20172](https://arxiv.org/abs/2511.20172)) / **TraCT** ([2512.18194](https://arxiv.org/abs/2512.18194)) | Y | · | · | p | Y | · | yes: CXL pool as one symmetric space + global block index | translation, sharing, eviction (TraCT) | host DAX mapping + index in the pool | real CXL switch |
| **Predictive Multi-Tier KV** ([2604.26968](https://arxiv.org/abs/2604.26968)) | Y | · | · | Y | Y | Y | no (one TierManager per tier) | prediction-driven promotion/demotion, prefetch | none global | mostly analytical |
| **FengHuang** ([2511.10753](https://arxiv.org/abs/2511.10753)) | Y | p | Y | · | p | · | partial: HBM as a cache over a switch-attached pool | "active tensor paging": prefetch + page-out | Tensor Addressable Bridge switch | vision + simulation |
| **LegoOS** (OSDI'18, [link](https://www.usenix.org/conference/osdi18/presentation/shan)) | · | · | · | p | Y | p | **yes** (global VA, 1 GB vRegions) | translation, fault, eviction, protection | memory components | emulated, CPU only |

What the matrix shows:
- The rows with a real single table (G10, MIND, HUVM, UlaCache, Harvest, LegoOS, Beluga/TraCT) each cover two or three tiers.
- The rows that reach five or six tiers (the CM384 family, NVIDIA MNNVL, Mooncake, KVBM, LMCache) do it with explicit mappings or key-value indexes, not with one table under virtual-memory management.

---

## 3. The literature in five layers

### 3.1 Fabric-level global addressing: the "KV Link / KV Switch" hardware

- **Huawei UnifiedBus (UB) and CloudMatrix384** ([2506.12708](https://arxiv.org/abs/2506.12708); xDeepServe [2508.02520](https://arxiv.org/abs/2508.02520); StrataCL [2607.26444](https://arxiv.org/abs/2607.26444); UB-Mesh [2503.20377](https://arxiv.org/abs/2503.20377); UMDK/URMA [gitee](https://gitee.com/openeuler/umdk); OpenURMA [2605.28717](https://arxiv.org/abs/2605.28717)). The closest physical realisation:
  - **Fabric:** 384 Ascend NPUs and 192 Kunpeng CPUs on a two-level UB switch fabric with "global unified memory addressing and routing".
  - **Remote HBM (T1/T2):** a local virtual address can map onto a remote NPU's HBM page. StrataCL registers buffers at allocation and gives them identical virtual addresses on all peers.
  - **Remote DRAM (T4):** UB switches route NPU DMA to any node's DRAM.
  - **What it leaves out:** all mappings are explicit, and there are no faults, migration or eviction at this layer.
- **openEuler UB Service Core (the "SuperPoD OS")** ([reference design 2.0](https://www.openeuler.org/projects/ub-service-core/white-paper/UB-Service-Core-SW-Arch-RD-2.0-en.pdf), read in round 2). It splits the job into four separate services:
  - MemFabric: "unified virtual addressing of memory across hosts", for host memory only;
  - MemCache: a weakly consistent KV-cache store;
  - UBS IO: the HBM↔SSD path, with pattern-based prefetch and evict;
  - UBS Engine: memory borrowing for VMs.

  Press write-ups of a 2026 keynote describe a "four-tier KV pool + global index". The primary document does not describe one table.
- **NVIDIA multi-node NVLink (GB200 NVL72, DGX GH200).**
  - CUDA virtual memory with `CU_MEM_HANDLE_TYPE_FABRIC` and the IMEX service builds a two-stage mapping, VA → PA → fabric address, so that any GPU can map any other GPU's HBM in the NVLink domain (T0–T2).
  - EGM extends the GPU virtual address space to local and remote Grace LPDDR (T3/T4). DGX GH200 advertised 144 TB of GPU-addressable memory over 256 superchips.
  - What it leaves out: mappings are explicit export/import, with no cross-node paging, migration or eviction, and flash is outside the space (GPUDirect Storage is explicit I/O).
  - Measurements of GH200 and Grace-Blackwell unified memory are in [2408.11556](https://arxiv.org/abs/2408.11556) and [2610.20354](https://arxiv.org/abs/2610.20354).
- **UALink 1.0** ([overview](https://ualinkconsortium.org/blog/ualink-200g-1-0-specification-overview-802/); full 1.0 spec and the January 2026 white paper read in round 3). It defines a pod-wide network physical address space.
  - The spec illustrates translation as accelerator MMU (VA → NPA) plus a Link MMU at the target (NPA → system physical address). It also says this path is "only for illustration. This specification leaves the address translation as an implementation choice."
  - An accelerator "may access any Accelerator memory or Host Memory in the Remote System node", so remote-node DRAM is reachable (T4 partial). Hosts may not access remote memory, and coherence is left to software.
  - The amdgpu UALink driver series (Aug 2026) implements export/import and remote TLB shootdown.
  - AMD's reverse-translation study ([2604.02473](https://arxiv.org/abs/2604.02473)) measures cold Link-TLB misses at up to 1.4× slowdown on small all-to-alls.
  - What it leaves out: exported memory is pinned, local host DRAM and flash are not in the space, and there is no paging.
- **Other fabrics and visions:**
  - Co-Fabric ([2609.25560](https://arxiv.org/abs/2609.25560)): a cross-host unified xPU address space.
  - ScalePool ([2510.14580](https://arxiv.org/abs/2510.14580)): an XLink (NVLink/UALink) domain plus CXL tier-2 memory nodes.
  - FengHuang ([2511.10753](https://arxiv.org/abs/2511.10753)): HBM as a cache of a switch-attached memory pool, with active tensor paging.
  - HyperOffload ([2602.00748](https://arxiv.org/abs/2602.00748)): compiler-scheduled offload to a supernode memory pool.
  - SambaNova SN40L ([2405.07518](https://arxiv.org/abs/2405.07518)): SRAM/HBM/DDR, three tiers on one node.
  - CXL 3.x GFAM ([Q&A](https://computeexpresslink.org/?p=1075)).
  - NVIDIA BlueField-4 ICMS, now named **CMX** ([press](https://nvidianews.nvidia.com/news/nvidia-bluefield-4-powers-new-class-of-ai-native-storage-infrastructure-for-the-next-frontier-of-ai); [developer blog](https://developer.nvidia.com/blog/?p=111143), read in round 3). It adds an Ethernet-attached flash tier "G3.5" for KV between node SSD and shared storage, and KV managers prestage blocks back into DRAM or HBM. The developer blog keeps block location in the frameworks' "KV block managers together with NIXL", so there is no single index or address space.
  - Huawei OceanStor M900 ([press](https://kyodonewsprwire.jp/release/202609176196)).
  - **Huawei software pools on UB, read in round 3:**
    - OBMM ([Gitee](https://gitee.com/openeuler/obmm)), the kernel module behind MemFabric, imports a remote node's DRAM over UB as a remote NUMA node, so Linux `mbind`/`move_pages` migrate pages between local and borrowed DRAM. This is real virtual-memory management, but only for host DRAM (T3/T4).
    - openYuanrong datasystem ([docs](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/0.7.0/)) is a multi-level HBM/DRAM/SSD cache accessed by object key, not by address.
    - MemFabric_Hybrid (Ascend) reportedly pools HBM and DRAM across nodes under one 64-bit global virtual address. Its README could not be fetched, so it is unverified (§7).
  - "Many Processors, Still One Computer" ([2609.16787](https://arxiv.org/abs/2609.16787)), a position paper arguing for one address space across a scale-up domain.

### 3.2 Address translation for multi-GPU and wafer-scale systems (computer architecture)

This line makes translation over T0–T1, and lately T2, efficient. It does not add DRAM or flash tiers.
- NUMA-aware GPUs (MICRO'17) and MCM-GPU ([ISCA'17](https://doi.org/10.1145/3079856.3080231)).
- Page migration and invalidation in multi-GPU systems: Griffin ([HPCA'20](https://doi.org/10.1109/HPCA47549.2020.00055)), Trans-FW ([HPCA'23](https://doi.org/10.1109/HPCA56546.2023.10071054)), IDYLL ([MICRO'23](https://doi.org/10.1145/3613424.3614269)), GRIT ([HPCA'24](https://doi.org/10.1109/HPCA57654.2024.00085)), OASIS (HPCA'25), invalidation-based mapping propagation (ISCA'26).
- Wafer-scale distributed translation: HDPAT ([HPCA'26](https://doi.org/10.1109/HPCA68181.2026.11408538)), WaferTrans ([2609.06125](https://arxiv.org/abs/2609.06125)).
- UVM practice: GPUVM ([2411.05309](https://arxiv.org/abs/2411.05309)), Ember ([2610.13408](https://arxiv.org/abs/2610.13408)), DREAM ([ICS'25](https://doi.org/10.1145/3721145.3725748)), Linux HMM ([doc](https://docs.kernel.org/mm/hmm.html)), GMEM ([2310.12554](https://arxiv.org/abs/2310.12554)).

Relevance: if the unified table is enforced by GPU page tables and Link MMUs, this is the cost model. ConServe ([ISCA'26](https://doi.org/10.1109/ISCA66397.2026.00075)) is the only paper found that measures GPU TLB locality for *paged KV*.

### 3.3 Virtual memory that reaches flash

- **G10** ([2310.09443](https://arxiv.org/abs/2310.09443)): "extends the page table of UVM by storing flash page addresses in its leaf-level page table entries. The unified page table can point to an address in either host memory, GPU memory, or flash memory." Compiler-planned pre-eviction and prefetch run over that one table.
- **GMT** ([ASPLOS'24](https://doi.org/10.1145/3620666.3651353)): GPU-orchestrated tiering over HBM, host DRAM and SSD.
- **Dragon** ([SC'18](https://doi.org/10.1109/SC.2018.00035)): UVM extended to NVMe.
- **BaM** ([ASPLOS'23](https://arxiv.org/abs/2203.04910)) and **GoFS** (SOSP'25): GPU-initiated storage access with a software cache. These are not page tables.
- **CPU side:** FlatFlash ([ASPLOS'19](https://doi.org/10.1145/3297858.3304061)), a unified memory-storage hierarchy; FlashMap ([ISCA'15](https://doi.org/10.1145/2749469.2750420)), one translation for memory-mapped SSDs; MegaMmap ([SC'24](https://doi.org/10.1109/SC41406.2024.00114)), cluster DRAM + SSD under one mapping; SkyByte (HPCA'25), a memory-semantic CXL SSD.
- **High-bandwidth flash (HBF), 2026:**
  - SK hynix **H3** ([IEEE CAL'26](https://doi.org/10.1109/LCA.2026.3660969)) puts HBM and HBF side by side on one accelerator. The abstract describes static placement ("storing read-only data in HBF and other data in HBM"). The "shared address space" wording comes only from press write-ups, and the full text is not public.
  - A hardware-managed HBM + HBF memory with prediction-based migration ([IEEE CAL'26](https://doi.org/10.1109/LCA.2026.3723326), Oxford) reports 2.79× over HBF-only on one device.
  - SPLASH ([2609.23816](https://arxiv.org/abs/2609.23816)) co-designs sparse attention with HBF.
  - Characterizing HBF ([2609.39131](https://arxiv.org/abs/2609.39131)).

  HBF makes "flash as a memory tier" an on-package question. It is still single-device.

### 3.4 OS/VM for disaggregated and tiered memory: the conceptual precedents

- **The switch holds the table.** **MIND** ([SOSP'21](https://arxiv.org/abs/2107.00164)) places one global virtual address space, range-partitioned over memory blades, in a programmable switch. Translation (range entries plus TCAM outliers), protection and an MSI coherence directory all run there, and compute blades run a fault-driven DRAM cache.

  This is the direct precedent for a "KV Switch that holds the memory table". Its limits: one rack, CPU and DRAM only, and switch SRAM caps it at about 45k translation rules and 30k directory entries.
- **Memory-side or OS-level global virtual address space:**
  - LegoOS ([OSDI'18](https://www.usenix.org/conference/osdi18/presentation/shan)): a global VA in 1 GB vRegions owned by memory components, with storage behind them.
  - Clio ([ASPLOS'22](https://arxiv.org/abs/2108.03492)): translation in the hardware memory node.
  - DeACT ([HPCA'21](https://arxiv.org/abs/2008.00171)): virtual memory for fabric-attached memory.
  - ThymesisFlow ([MICRO'20](https://doi.org/10.1109/MICRO50266.2020.00075)).
  - ODRP ([NSDI'25](https://www.usenix.org/conference/nsdi25/presentation/wang-zixuan)): the memory-node NIC owns remote-paging metadata.
  - StarfishOS ([SOSP'26](https://doi.org/10.1145/3830418.3843894)): a single system image over CXL.
  - Infiniswap ([NSDI'17](https://www.usenix.org/conference/nsdi17/technical-sessions/presentation/gu)), Fastswap, AIFM, Kona, Hermit, Canvas and the rest of the far-memory line are listed in the CSV.
- **Policy for more than two tiers:**
  - multi-tier page management ([ATC'21](https://www.usenix.org/conference/atc21/presentation/kim-jonghyeon));
  - HeMem ([SOSP'21](https://doi.org/10.1145/3477132.3483550));
  - TPP ([ASPLOS'23](https://arxiv.org/abs/2206.02878));
  - Pond ([ASPLOS'23](https://arxiv.org/abs/2203.00241));
  - Memtis (SOSP'23);
  - Colloid ([SOSP'24](https://doi.org/10.1145/3694715.3695968)), whose point is that access latency, not hotness, should drive placement — directly relevant when T1/T2 latency changes with fabric load.
  - All of these are CPU-only.

### 3.5 LLM KV-cache tiering and pooling: the payload layer

- **Cluster KV stores with a global index:**
  - Mooncake (FAST'25) and Mooncake Store/Transfer Engine. The Transfer Engine names every node's DRAM, VRAM and NVMe-oF as "segments" reachable over RDMA, NVLink and multi-node NVLink. The master maps object keys to replica segments with LRU, leases and pins.
  - CloudMatrix EMS: a DRAM pool over all nodes with a DHT and an SSD tier.
  - LMCache ([2510.09665](https://arxiv.org/abs/2510.09665)).
  - Dynamo KVBM + NIXL, with tiers G1 GPU, G2 host DRAM, G3 NVMe and G4 object storage.
  - SGLang HiCache.
  - Huawei UCM ([GitHub](https://github.com/ModelEngine-Group/unified-cache-management)), FlexKV, llm-d KV-cache indexer, and the vLLM KV connector and offloading API.
  - xLLM ([2510.14686](https://arxiv.org/abs/2510.14686)): on-demand page mapping in HBM plus a Mooncake-based global KV pool.
  - TokenLake ([2508.17219](https://arxiv.org/abs/2508.17219)).
  - KVDrive ([2605.18071](https://arxiv.org/abs/2605.18071)).
  - Strata ([OSDI'26](https://www.usenix.org/conference/osdi26/presentation/xie-zhiqiang)).
  - IMPRESS ([FAST'25](https://www.usenix.org/conference/fast25/presentation/chen-weijian-impress)).
  - CachedAttention ([ATC'24](https://arxiv.org/abs/2403.19708)).
- **Peer HBM as a tier (T1):**
  - Aqua ([ASPLOS'25](https://arxiv.org/abs/2407.21255));
  - HUVM;
  - Harvest ([2602.00328](https://arxiv.org/abs/2602.00328)), a "unified KV block table" over local HBM, peer GPU and host DRAM;
  - EMA ([2609.27040](https://arxiv.org/abs/2609.27040)), elastic borrowing of HBM across GPUs with translation and reclaim;
  - UlaCache ([2610.29820](https://arxiv.org/abs/2610.29820)), one logical KV address space over own and peer GPU pools plus CPU memory, remapped after migration — the most VM-like KV design found;
  - DynamoServe ([SIGCOMM'26 workshop](https://doi.org/10.1145/3789240.3829347));
  - Infinite-LLM and MemServe, which pool HBM across instances.

  All of these stop at one server or one tensor-parallel group, except Infinite-LLM and MemServe, which have no flash tier.
- **CXL pools for KV (T4 as load/store memory):**
  - Beluga ([SIGMOD'26](https://arxiv.org/abs/2511.20172));
  - TraCT ([2512.18194](https://arxiv.org/abs/2512.18194));
  - a CXL memory rack ([2607.18141](https://arxiv.org/abs/2607.18141));
  - composable CXL on Kubernetes ([2609.10790](https://arxiv.org/abs/2609.10790));
  - cross-layer eBPF policies for GPU–CXL fabrics ([2607.26335](https://arxiv.org/abs/2607.26335)).
- **GPU virtual memory inside one GPU:**
  - PagedAttention's block table is a page table for KV ([SOSP'23](https://arxiv.org/abs/2309.06180));
  - vAttention ([ASPLOS'25](https://arxiv.org/abs/2405.04437));
  - vTensor ([2407.15309](https://arxiv.org/abs/2407.15309));
  - Prism / kvcached ([OSDI'26](https://www.usenix.org/conference/osdi26/presentation/yu-shan));
  - eLLM ([2506.15155](https://arxiv.org/abs/2506.15155));
  - MoonBright ([OSDI'26](https://www.usenix.org/conference/osdi26/presentation/zhang-yangyu)), which materialises the page table on the device.

  These are T0 only, but they are the place a unified table would plug in.
- **Six tiers named, no table:** Predictive Multi-Tier Memory Management ([2604.26968](https://arxiv.org/abs/2604.26968)) lists HBM, pinned DRAM, CXL, NVMe+GDS, RDMA fabric and a parallel file system. It runs one manager per tier with a reuse predictor, and most of its cluster results are analytical.
- **Offloading for training and inference:** ZeRO-Infinity ([SC'21](https://arxiv.org/abs/2104.07857)) and FlexGen ([ICML'23](https://arxiv.org/abs/2303.06865)). Placement is explicit; there is no table.

---

## 4. The five closest works and exactly what each misses

1. **Huawei CloudMatrix384, with xDeepServe, StrataCL, EMS and the UB Service Core.**
   - Has: physically all six tiers on one fabric, with hardware global addressing over every NPU's HBM and every CPU's DRAM, an EMS DRAM pool across all 48 nodes, and an SSD tier beneath it.
   - Misses:
     - **one table:** the UB physical address space (per-device page tables, explicit registration), the EMS key→DRAM DHT and the SSD tiering inside the pool are separate layers;
     - **HBM as a managed KV tier:** HBM is the consumer; NPU-to-NPU UB access is used for expert-parallel and prefill/decode communication;
     - **demand faults and migration across tiers:** no fault path or migration across HBM, DRAM and SSD through a table.
2. **NVIDIA multi-node NVLink: fabric handles + IMEX + EGM, DGX GH200 / GB200 NVL72.**
   - Has: one GPU virtual address namespace over T0–T4, with a two-stage VA → PA → fabric-address translation done by GPU MMUs.
   - Misses:
     - **flash:** T5 is outside the space;
     - **paging:** map/unmap is explicit, with no faults, migration or eviction across nodes and no global replacement policy;
     - **KV awareness:** none.
3. **G10 (MICRO'23).**
   - Has: the VM paradigm done right for three tiers. One UVM page table whose entries point to GPU, host or flash pages, with demand faults, compiler-planned prefetch and eviction, and the SSD controller updating PTEs during garbage collection.
   - Misses:
     - **peer and remote tiers:** no T1, T2 or T4;
     - **scale:** single GPU, simulated (GPGPU-Sim + SSD-Sim);
     - **inference fit:** it relies on tensor lifetimes being known at compile time, which LLM KV with unknown output lengths and agent pauses does not have.
4. **MIND (SOSP'21).**
   - Has: the switch holds the memory table, with global VA, translation, protection and coherence on the data path — exactly the "KV Switch" role.
   - Misses:
     - **accelerator memory:** CPU DRAM only, no HBM tiers;
     - **flash:** none;
     - **scale:** one rack, with switch SRAM capacity limits;
     - **policy:** no multi-tier placement.
5. **UlaCache, with Harvest, EMA and HUVM, the peer-HBM line.**
   - Has: a real logical-to-physical KV table over own HBM, peer HBM and host DRAM, remapped after migration and with LRU eviction; HUVM does the same at the UVM page level.
   - Misses:
     - **scope:** one server or tensor-parallel group, so no T2, T4 or T5;
     - **fault path:** fetch is software-scheduled, not fault-driven;
     - **lifetime:** the table is not cluster-wide or persistent.

**Runners-up:**
- Mooncake/Store and Dynamo KVBM span T0, T3, T4 and T5 with a global index. It is a key-value lookup before an explicit copy, not translation, and peer or remote HBM is only a transfer path.
- MemServe and Infinite-LLM pool HBM and DRAM across instances, with no flash and a best-effort index.
- Beluga and TraCT put a CXL pool under one symmetric address space with a global block index, covering T0 and T4 only.
- FengHuang, ScalePool and Co-Fabric are fabric visions without page tables.

---

## 5. Gap statement and open design questions

**Gap.** Six-tier coverage today comes from *stacking* separate mechanisms:
- fabric addressing for HBM and DRAM;
- KV-store indexes for DRAM and SSD;
- explicit I/O for flash.

Each has its own metadata, its own placement policy and no fault path. Single-table virtual-memory designs exist only for subsets: HBM + DRAM + flash on one GPU (G10); own + peer HBM + DRAM in one server (HUVM, UlaCache); remote DRAM through a switch (MIND).

The 2026 survey of KV-cache management ([2607.02574](https://arxiv.org/abs/2607.02574)) classifies systems into five archetypes: local-paged, disaggregated-pipeline, shared-store, memory-pool and hybrid-tier. It notes that none combines pooled load/store memory with a distributed prefix directory and explicit ownership semantics. That is the same gap, seen from the KV side.

**Questions a paper would have to answer** (each grounded in what the closest works show to be hard):
1. **Where the table lives.**
   - Options: in GPU page tables (G10, UVM — a per-device copy, so invalidation cost; see IDYLL and Trans-FW); in the switch (MIND — SRAM limits); in memory-side Link MMUs (UALink — cold Link-TLB misses up to 1.4× on small collectives, [2604.02473](https://arxiv.org/abs/2604.02473)); or as a software index (Mooncake, KVBM — no fault path).
   - A hybrid is likely: a hardware path for T0–T2, a software miss handler for T3–T5.
2. **Granularity.** 4 KB or 2 MB pages (UVM), KV blocks of 16–512 tokens (PagedAttention, Mooncake, EMS), or tensors (G10, FengHuang). Agent KV is long-lived and append-only per session, which favours block granularity with a session-level ownership record.
3. **Faults versus prefetch.** GPUs cannot cheaply take page faults in the middle of a kernel. Every working system prefetches: G10 by compiler, UlaCache per step, HiCache by threshold. A unified table probably needs an "access-intent" path from the scheduler or harness instead of faults. This is exactly where agent-harness hints (topics 13 and 18–21) can supply the missing lifetime information.
4. **Placement policy across six latency classes.** Peer HBM over NVLink can be faster than local DRAM. Remote HBM through a switch and remote DRAM over the same fabric may cost the same. Hotness-only tiering (TPP, Memtis) is the wrong model. Colloid's latency-balancing argument (SOSP'24) is the closest CPU precedent.
5. **Ownership and revocation.** Lent peer HBM must be reclaimable at once (Harvest, EMA, HUVM). A unified table needs lease or revocation semantics, which classic virtual memory lacks.
6. **Coherence and sharing.** Prefix KV is shared, read-only and append-only. That makes invalidation simpler than CPU coherence, so MIND-style directories may be overkill — an opportunity.
7. **Metadata scale.** At 64 tokens per block, a 1 M-token context per session × 1k sessions is about 16 M entries. That is far beyond switch SRAM (MIND: about 30–45k entries) but feasible in DRAM, which argues for a hierarchy: a switch cache over a memory-node table.

---

## 6. Where our work sits

- [`research/13_tiered_memory`](../13_tiered_memory/README.md) measured pieces of this hierarchy on real harness runs:
  - **B1**, GPU → DRAM → flash KV driven by harness hints: the DRAM tier gives 1.65× over GPU-only.
  - **B2**, peer-GPU HBM over NVLink as a KV tier.
  - **D1**, session-resident state in DRAM.

  Each runs as its own mechanism, the same pattern §4 finds in the literature. A unified table would replace per-tier connectors with one logical KV address per session block. The harness's knowledge of turn boundaries and tool pauses is the "access intent" in question 3 of §5.
- [`serving_memory_balancing.md`](serving_memory_balancing.md) (2026-09-12) already covers how engines ration HBM. This note adds the hardware and OS layers below it.

---

## 7. Coverage and what remains unchecked

**Saturation by sub-area** (two consecutive query batches adding fewer than 2 new relevant items):

| sub-area | status |
|---|---|
| fabric and CXL, academic papers | saturated |
| multi-GPU translation / UVM | saturated |
| GPU + storage unification | saturated, except 2026 HBF preprints (sampled) |
| disaggregated-memory OS/VM | saturated |
| CPU tiered memory / CXL | cut by time (2025–26 CXL tiering preprints not exhausted) |
| flash in the VM hierarchy | saturated |
| KV-cache tiering / pooling and GPU virtual memory for serving | saturated |
| offloading | near-saturated |
| vendor docs (NVIDIA, Huawei/openEuler, UALink) | not saturated: each search still found 1–2 new pages |

**Round 3 (2026-11-01, 16:40 UTC, approved by the user).** It reran this list but was again stopped after about 8 minutes by the session's safety check, which refused a GitHub fetch. Per instructions, it did not retry or work around the refusal.

Read in round 3:
- the full UALink 1.0 spec, the January 2026 UALink white paper and the UALink 2.0 announcement;
- OBMM and libummu;
- the NVIDIA CMX developer blog and the Dynamo router global index, a fleet-wide prefix tree that stores "the worker id on each node", not a translation table;
- the SK hynix H3 abstract and MARS;
- the Samsung CMM-D white paper;
- the Mooncake transports over Kunpeng UB and MemFabric;
- openYuanrong datasystem.

None comes closer than §4's closest works.

**Not checked:**
- **Specs and vendor docs:**
  - the text of UB Base Specification 2.0/2.1 (behind a license page);
  - **MemFabric_Hybrid** (Ascend; [GitHub](https://github.com/Ascend/memfabric_hybrid), unverified). Search summaries describe one 64-bit global virtual address over HBM and DRAM across nodes. If confirmed, it is a software peer of the NVIDIA multi-node NVLink row (T0–T4, no flash). Whether it has faults, migration or eviction is unknown. It is the top open lead;
  - Enfabrica EMFASYS tiering (the MUG'25 tutorial was not found);
  - the Vera Rubin / NVLink Fusion memory model.
- **Leads:** Tierce and Anchor (SOSP'26), Cxlalloc (ASPLOS'26), GrOUT, AgileOS, DxPU, Alibaba PolarKV, MemNetAI'26 workshop papers, PMKS, MPiCO, DeepPiC, Phantom Walk.
- **Citations:** citers of HUVM, and Mooncake citers beyond the first 1,000.
- **Venue lists** not systematically read: EuroSys'26, ASPLOS'26, ISCA'26, MICRO'25, HPCA'26, ATC'25/26, MLSys'26, SC'25, HotStorage'26. OSDI'26, SOSP'25/'26, NSDI'26 and FAST'26 were read.
- **Full-text re-checks** (round 2 compared the round-1 records against each other but did not re-read these):
  - where translation happens in xDeepServe/StrataCL: NPU MMU versus UB switch decode;
  - whether remote-node EGM memory can be imported through IMEX fabric handles (DGX GH200 documents access to "extended CPU memory" of any superchip);
  - G10's multi-GPU section.

**Corrections from the round-2 and round-3 cross-checks, already applied in the CSV `note` column:**
- CloudMatrix384: peer and remote HBM are "partial", because they are reachable but not managed KV tiers.
- xDeepServe: "one table" is "partial".
- Mooncake: the master's mapping is a key → location index, not address translation.
- G10: T1 is "no".
- UALink: T4 is "partial", and the translation path is illustrative only (round 3).
- H3: static placement; "one address space" is not supported by the available text (round 3).
