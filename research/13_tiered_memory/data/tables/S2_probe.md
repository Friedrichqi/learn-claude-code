# S2a · The RTX PRO 6000 node and what moving KV costs there (job 117716)

| fact | value |
|---|---|
| host | alphagpu51 |
| GPU (index, name, memory, PCIe gen now/max, width now/max, power) | 0, NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB, 1, 5, 16, 16, 600.00 W |
| CPU | Intel(R) Xeon(R) 6960P, 144 CPUs |
| host memory | 1007 GiB |
| local disks | nvme0n1    0 nvme   Micron_7450_MTFDKCC960TFR 894.3G disk · nvme1n1    0 nvme   Micron_7450_MTFDKCC960TFR 894.3G disk |
| /dev/shm | 503.7 GiB tmpfs |

**One large copy over PCIe** (GB/s):

| size | pinned H2D | pinned D2H | pageable H2D | pageable D2H |
|---|---|---|---|---|
| 64 MiB | 56.13 | 57.11 | 11.82 | 11.76 |
| 256 MiB | 54.41 | 57.36 | 10.52 | 11.85 |
| 1024 MiB | 56.62 | 57.4 | 10.47 | 11.78 |
| 4096 MiB | 56.63 | 57.41 | 10.69 | 11.8 |

**vLLM's block-copy kernel** (`swap_blocks_batch`, one cuMemcpyBatchAsync of random pages; GB/s):

| page size | copies | DRAM → GPU | GPU → DRAM |
|---|---|---|---|
| 32 KiB | 65,536 | 36.64 | 38.2 |
| 64 KiB | 32,768 | 42.97 | 44.01 |
| 256 KiB | 8,192 | 50.62 | 51.44 |
| 1024 KiB | 2,048 | 54.94 | 55.8 |
| 4096 KiB | 512 | 56.19 | 57.0 |

A 23k-token Qwen3-32B context (1,438 blocks × 64 layers × K and V pages of 32 KiB = 5.6 GiB) takes **135 ms** through the kernel alone.

**Local storage** (O_DIRECT where supported, 4 MiB I/Os; GB/s):

| directory | filesystem | sequential write | sequential read | 4 MiB files write | 4 MiB files read (random order) |
|---|---|---|---|---|---|
| /tmp | /dev/nvme0n1p3 xfs | 1.47 | 3.98 | 1.01 | 2.94 |
| /dev/shm | none tmpfs | 2.99 | 5.35 | 2.63 | 4.72 |
| /local | /dev/nvme0n1p3 xfs | 1.05 | 4.21 | 0.92 | 2.93 |

## Qwen/Qwen3-32B

Weights 61.03 GiB; KV pool 21.70 GiB = **88,848 tokens** (concurrency 2.17× at the 40,960-token window); CUDA graphs 0.32 GiB; CPU tier 68.7 GB.

| prompt tokens | prefill (recompute) latency, s | tokens/s |
|---|---|---|
| 1,024 | 0.201 | 5,062 |
| 4,096 | 0.770 | 5,316 |
| 8,192 | 1.652 | 4,943 |
| 16,384 | 3.780 | 4,332 |
| 23,552 | 6.038 | 3,897 |
| 32,768 | 9.465 | 3,454 |

Decode at batch 1: **46.97 ms per token**.

| tokens | recompute (cold), s | GPU hit, s | restore from DRAM, s | DRAM restore above a GPU hit, s | effective GB/s | recompute / DRAM restore |
|---|---|---|---|---|---|---|
| 4,096 | 0.791 | 0.090 | 0.137 | 0.047 | 23.0 | 5.8× |
| 16,384 | 3.810 | 0.123 | 0.241 | 0.118 | 36.4 | 15.8× |
| 23,552 | 6.082 | 0.146 | 0.312 | 0.167 | 37.1 | 19.5× |

## Qwen/Qwen3-8B

Weights 15.27 GiB; KV pool 68.42 GiB = **498,208 tokens** (concurrency 12.16× at the 40,960-token window); CUDA graphs 0.21 GiB; CPU tier 68.7 GB.

| prompt tokens | prefill (recompute) latency, s | tokens/s |
|---|---|---|
| 1,024 | 0.072 | 13,704 |
| 4,096 | 0.224 | 18,078 |
| 8,192 | 0.433 | 18,496 |
| 16,384 | 0.973 | 16,822 |
| 23,552 | 1.557 | 15,122 |
| 32,768 | 2.453 | 13,358 |

Decode at batch 1: **11.78 ms per token**.

| tokens | recompute (cold), s | GPU hit, s | restore from DRAM, s | DRAM restore above a GPU hit, s | effective GB/s | recompute / DRAM restore |
|---|---|---|---|---|---|---|
| 4,096 | 0.225 | 0.058 | 0.085 | 0.027 | 22.4 | 2.7× |
| 16,384 | 0.994 | 0.096 | 0.174 | 0.078 | 30.9 | 5.7× |
| 23,552 | 1.577 | 0.119 | 0.212 | 0.093 | 37.3 | 7.4× |

