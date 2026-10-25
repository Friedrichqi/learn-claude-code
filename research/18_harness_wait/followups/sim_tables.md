
### Validation against the measured cells

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 8 | `none` | 6,529 | 5,705 / 7,159 / 6,724 | 0.87 | 2 % | 131 / 511 |  |
| as run | 16,136 | 16 | `none` | 1,905 | 1,941 / 1,928 / 1,846 | 0.11 | 78 % | 1,462 / 3,125 |  |
| as run | 16,136 | 32 | `none` | 1,905 | 1,899 / 1,945 / 1,872 | 0.09 | 73 % | 1,394 / 5,150 |  |
| as run | 16,136 | 16 | `gate:8` | 6,948 | 6,207 / 7,685 / 6,953 | 0.87 | 5 % | 275 / 840 |  |
| as run | 16,136 | 32 | `gate:8` | 7,007 | 6,295 / 7,713 / 7,015 | 0.87 | 5 % | 612 / 1,531 |  |
| as run | 16,136 | 16 | `backoff:mid` | 6,155 | 6,061 / 6,267 / 6,137 | 0.81 | 20 % | 308 / 1,059 |  |
| as run | 16,136 | 32 | `backoff:mid` | 5,144 | 5,343 / 5,077 / 5,012 | 0.74 | 37 % | 831 / 2,638 |  |
| as run | 16,136 | 16 | `share:0.9:mid` | 6,695 | 6,167 / 7,159 / 6,759 | 0.84 | 13 % | 322 / 827 |  |
| as run | 16,136 | 32 | `share:0.9:mid` | 6,330 | 5,485 / 7,239 / 6,265 | 0.82 | 20 % | 812 / 1,401 | cap releases 72 |
| as run | 16,136 | 16 | `share:0.9:start+mid` | 7,111 | 6,375 / 7,896 / 7,061 | 0.86 | 7 % | 295 / 750 |  |

### Signal realism (C1): backoff on a stock-/metrics-like signal (0.5-s samples, KV noise sd 0.2); share rule on a stale status

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 16 | `backoff:mid:0.2` | 5,057 | 4,594 / 5,409 / 5,168 | 0.75 | 33 % | 386 / 1,267 |  |
| as run | 16,136 | 32 | `backoff:mid:0.2` | 3,284 | 3,236 / 3,393 / 3,223 | 0.54 | 58 % | 1,196 / 3,683 |  |
| as run | 16,136 | 16 | `share:0.9:mid:600:0.5:10` | 6,700 | 6,109 / 7,325 / 6,664 | 0.85 | 12 % | 321 / 808 |  |
| as run | 16,136 | 32 | `share:0.9:mid:600:0.5:10` | 6,520 | 5,633 / 7,387 / 6,541 | 0.83 | 17 % | 812 / 1,324 | cap releases 79 |

### Request-level server policies (no session gate)

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 16 | `qpin:0.5` | 1,870 | 1,798 / 1,944 / 1,868 | 0.64 | 64 % | 1,230 / 2,825 |  |
| as run | 16,136 | 32 | `qpin:0.5` | 1,656 | 1,577 / 1,753 / 1,638 | 0.48 | 65 % | 1,081 / 5,688 |  |
| as run | 16,136 | 16 | `qlru` | 2,068 | 2,113 / 2,092 / 1,999 | 0.20 | 76 % | 1,361 / 2,846 |  |
| as run | 16,136 | 32 | `qlru` | 1,914 | 1,907 / 1,956 / 1,880 | 0.10 | 73 % | 1,398 / 5,123 |  |
| as run | 16,136 | 16 | `lpm:120` | 3,233 | 3,253 / 3,237 / 3,208 | 0.54 | 60 % | 705 / 1,993 |  |
| as run | 16,136 | 32 | `lpm:120` | 2,889 | 2,878 / 2,987 / 2,801 | 0.47 | 64 % | 1,405 / 3,734 |  |
| as run | 16,136 | 16 | `phase` | 2,172 | 2,125 / 2,255 / 2,135 | 0.23 | 74 % | 1,213 / 2,649 |  |
| as run | 16,136 | 32 | `phase` | 2,097 | 2,142 / 2,121 / 2,028 | 0.19 | 75 % | 2,160 / 3,519 |  |

### Client-side rules: hold points, margin, a client-only signal

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 16 | `share:0.9:start` | 6,832 | 6,130 / 7,682 / 6,685 | 0.85 | 12 % | 298 / 872 |  |
| as run | 16,136 | 32 | `share:0.9:start:3600` | 6,532 | 4,915 / 7,692 / 6,989 | 0.83 | 18 % | 728 / 1,498 |  |
| as run | 16,136 | 32 | `share:0.9:start+mid:3600` | 7,242 | 6,335 / 8,067 / 7,322 | 0.87 | 7 % | 680 / 1,101 |  |
| as run | 16,136 | 16 | `share:0.7:mid` | 6,495 | 5,659 / 7,179 / 6,649 | 0.86 | 8 % | 358 / 747 |  |
| as run | 16,136 | 16 | `share:1.0:mid` | 6,002 | 5,865 / 6,000 / 6,142 | 0.81 | 23 % | 348 / 1,023 |  |
| as run | 16,136 | 16 | `share:1.2:mid` | 2,614 | 2,889 / 2,450 / 2,505 | 0.38 | 68 % | 923 / 2,410 | cap releases 1 |
| as run | 16,136 | 32 | `share:1.1:mid` | 2,835 | 2,841 / 3,193 / 2,471 | 0.44 | 64 % | 1,764 / 3,075 | cap releases 121 |
| as run | 16,136 | 16 | `pff:60` | 2,125 | 2,203 / 2,318 / 1,854 | 0.20 | 74 % | 1,327 / 2,872 |  |
| as run | 16,136 | 32 | `pff:60` | 1,907 | 1,900 / 1,947 / 1,873 | 0.10 | 73 % | 1,401 / 5,113 |  |

### A client-only gate (F7): holds on a probe signal (queued requests only) or the stock backoff signal

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 16 | `probe:start` | 2,379 | 2,334 / 2,501 / 2,303 | 0.31 | 71 % | 986 / 2,352 |  |
| as run | 16,136 | 32 | `probe:start` | 1,904 | 1,896 / 1,927 / 1,891 | 0.09 | 74 % | 1,271 / 4,473 |  |
| as run | 16,136 | 16 | `backoff:start` | 3,845 | 4,048 / 4,021 / 3,465 | 0.63 | 51 % | 515 / 1,719 |  |
| as run | 16,136 | 32 | `backoff:start` | 1,909 | 1,915 / 1,919 / 1,894 | 0.10 | 75 % | 1,300 / 4,404 |  |
| as run | 16,136 | 16 | `probe:start+mid` | 4,154 | 4,003 / 4,387 / 4,071 | 0.67 | 48 % | 514 / 1,495 |  |
| as run | 16,136 | 32 | `probe:start+mid` | 2,984 | 2,997 / 3,066 / 2,891 | 0.49 | 62 % | 1,241 / 3,901 |  |

### Capacity: gate size, a working-set gate, bigger pools

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 16 | `gate:10` | 7,150 | 6,223 / 8,053 / 7,173 | 0.86 | 8 % | 263 / 884 |  |
| as run | 16,136 | 16 | `gate:12` | 3,371 | 3,451 / 3,677 / 2,985 | 0.55 | 57 % | 580 / 2,148 |  |
| as run | 16,136 | 16 | `wsgate:0.9` | 7,003 | 6,445 / 7,801 / 6,762 | 0.85 | 11 % | 261 / 893 |  |
| as run | 16,136 | 32 | `wsgate:0.9` | 6,547 | 6,756 / 6,530 / 6,355 | 0.83 | 15 % | 706 / 1,923 |  |
| as run | 20,011 | 16 | `none` | 3,139 | 3,353 / 3,277 / 2,786 | 0.49 | 62 % | 571 / 2,170 |  |
| as run | 20,011 | 32 | `none` | 1,928 | 1,929 / 1,962 / 1,893 | 0.09 | 74 % | 1,064 / 4,979 |  |
| as run | 20,011 | 32 | `gate:12` | 7,932 | 7,396 / 8,433 / 7,968 | 0.87 | 5 % | 545 / 1,392 |  |
| as run | 32,272 | 16 | `none` | 8,309 | 7,994 / 8,545 / 8,388 | 0.88 | 2 % | 206 / 801 |  |
| as run | 32,272 | 32 | `none` | 1,946 | 1,972 / 1,953 / 1,914 | 0.09 | 77 % | 1,368 / 4,596 |  |
| as run | 32,272 | 32 | `gate:16` | 8,476 | 8,055 / 8,861 / 8,513 | 0.88 | 6 % | 502 / 1,347 |  |

### The harness's own rewrites (micro_compact) and a think-pause offload bound

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| _batchc4 | 16,136 | 8 | `none` | 8,212 | 7,581 / 8,473 / 8,581 | 0.94 | 2 % | 105 / 413 |  |
| _batchc4 | 16,136 | 16 | `gate:8` | 8,614 | 8,008 / 9,118 / 8,715 | 0.93 | 6 % | 213 / 700 |  |
| _batchc4 | 16,136 | 32 | `gate:8` | 8,818 | 8,037 / 9,374 / 9,042 | 0.93 | 7 % | 469 / 1,241 |  |
| _batchc8 | 16,136 | 8 | `none` | 8,521 | 7,985 / 8,781 / 8,797 | 0.95 | 2 % | 98 / 387 |  |
| _batchc8 | 16,136 | 16 | `gate:8` | 8,979 | 8,403 / 9,439 / 9,096 | 0.94 | 7 % | 204 / 666 |  |
| _batchc8 | 16,136 | 32 | `gate:8` | 9,188 | 8,592 / 9,697 / 9,277 | 0.94 | 7 % | 453 / 1,208 |  |
| _appendonly | 16,136 | 8 | `none` | 8,962 | 8,433 / 9,348 / 9,105 | 0.96 | 1 % | 91 / 371 |  |
| _appendonly | 16,136 | 16 | `gate:8` | 9,439 | 8,882 / 9,998 / 9,436 | 0.95 | 7 % | 190 / 636 |  |
| _appendonly | 16,136 | 32 | `gate:8` | 9,564 | 8,877 / 10,203 / 9,611 | 0.95 | 7 % | 421 / 1,166 |  |
| _appendonly | 32,272 | 32 | `gate:20` | 12,757 | 12,447 / 12,541 / 13,284 | 0.95 | 10 % | 298 / 1,011 |  |
| _offload | 16,136 | 16 | `gate:8` | 7,227 | 6,441 / 8,030 / 7,211 | 0.88 | 0 % | 263 / 793 |  |
| _offload | 16,136 | 32 | `gate:8` | 7,315 | 6,500 / 8,007 / 7,437 | 0.88 | 0 % | 583 / 1,492 |  |
| _dedup77 | 16,136 | 8 | `none` | 8,271 | 7,666 / 8,551 / 8,595 | 0.94 | 2 % | 104 / 419 |  |
| _dedup77 | 16,136 | 16 | `gate:8` | 8,700 | 8,079 / 9,177 / 8,845 | 0.93 | 7 % | 206 / 694 |  |
| _dedup77 | 16,136 | 32 | `gate:8` | 8,897 | 8,163 / 9,478 / 9,049 | 0.94 | 7 % | 466 / 1,236 |  |

### A model-called wait: loops of short waits vs one blocking wait

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 8 | `mwait:0.03:1:0.97:loop` | 5,015 | 4,534 / 5,291 / 5,221 | 0.93 | 2 % | 160 / 684 | wait-only calls per 100 work: 97 |
| as run | 16,136 | 8 | `mwait:0.03:1:0.97:block` | 6,500 | 5,699 / 7,122 / 6,681 | 0.87 | 2 % | 131 / 470 | wait-only calls per 100 work: 3 |
| as run | 16,136 | 16 | `mwait:0.042:5:0.9:loop` | 1,324 | 1,335 / 1,420 / 1,216 | 0.10 | 83 % | 2,179 / 4,014 | wait-only calls per 100 work: 42 |
| as run | 16,136 | 16 | `mwait:0.042:5:0.9:block` | 2,551 | 2,431 / 2,743 / 2,480 | 0.39 | 68 % | 917 / 2,310 | wait-only calls per 100 work: 4 |
| as run | 16,136 | 16 | `mwait:0.30:5:0:block` | 5,094 | 5,047 / 5,009 / 5,227 | 0.82 | 30 % | 418 / 1,096 | wait-only calls per 100 work: 29 |
| as run | 16,136 | 32 | `mwait:0.05:10:0.9:loop` | 1,315 | 1,252 / 1,459 / 1,233 | 0.10 | 80 % | 908 / 5,694 | wait-only calls per 100 work: 54 |
| as run | 16,136 | 32 | `mwait:0.05:10:0.9:block` | 1,824 | 1,835 / 1,856 / 1,783 | 0.10 | 74 % | 1,315 / 5,137 | wait-only calls per 100 work: 6 |
| as run | 16,136 | 32 | `mwait:0.30:10:0:block` | 2,384 | 1,942 / 3,170 / 2,039 | 0.44 | 68 % | 1,783 / 3,925 | wait-only calls per 100 work: 29 |

### Partial adoption: a share of the sessions run the rule (start+mid), the rest do not

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 16 | `mix:0.9:start+mid:0.5` | 2,881 | 3,540 / 2,927 / 2,175 | 0.43 | 64 % | 748 / 2,268 | per session/h: rule 137, others 223; cap releases 8 |
| as run | 16,136 | 16 | `mix:0.9:start+mid:0.75` | 5,950 | 5,750 / 6,661 / 5,440 | 0.80 | 23 % | 334 / 1,037 | per session/h: rule 361, others 405; cap releases 0 |
| as run | 16,136 | 32 | `mix:0.9:start+mid:0.5` | 1,896 | 1,894 / 1,934 / 1,859 | 0.09 | 75 % | 1,887 / 3,810 | per session/h: rule 32, others 86; cap releases 71 |
| as run | 16,136 | 32 | `mix:0.9:start+mid:0.75` | 1,899 | 1,890 / 1,938 / 1,869 | 0.09 | 76 % | 1,988 / 4,156 | per session/h: rule 42, others 112; cap releases 103 |

### Gate admission order and turn latency

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| as run | 16,136 | 32 | `gate:8` | 7,007 | 6,295 / 7,713 / 7,015 | 0.87 | 5 % | 612 / 1,531 |  |
| as run | 16,136 | 32 | `gate:8:las` | 7,306 | 6,537 / 7,904 / 7,476 | 0.88 | 5 % | 226 / 2,001 |  |
| as run | 16,136 | 32 | `gate:8:srpt` | 6,680 | 6,161 / 7,145 / 6,733 | 0.87 | 4 % | 231 / 2,166 |  |

### F3a and the harness rules: share rule and backoff (realistic signal) on the deduplicated scripts

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| _dedup77 | 16,136 | 16 | `share:0.9:mid` | 8,601 | 7,943 / 8,917 / 8,943 | 0.91 | 16 % | 230 / 663 |  |
| _dedup77 | 16,136 | 32 | `share:0.9:mid` | 8,590 | 7,889 / 8,840 / 9,042 | 0.91 | 19 % | 594 / 997 | cap releases 18 |
| _dedup77 | 16,136 | 16 | `backoff:mid:0.2` | 6,589 | 5,866 / 7,390 / 6,511 | 0.84 | 39 % | 286 / 1,026 |  |
| _dedup77 | 16,136 | 32 | `backoff:mid:0.2` | 3,588 | 3,225 / 3,775 / 3,764 | 0.59 | 67 % | 1,057 / 3,423 |  |

### The levers stacked at N=32: gate, then F3a (deduplicated re-reads), then an FP8-sized pool with a larger K

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| _dedup77 | 32,272 | 32 | `gate:16` | 10,958 | 10,376 / 11,421 / 11,076 | 0.94 | 8 % | 355 / 1,088 |  |
| _dedup77 | 32,272 | 32 | `gate:20` | 11,569 | 11,089 / 11,827 / 11,791 | 0.94 | 9 % | 334 / 1,088 |  |

### Think pauses 4x longer (about 2 min at the median)

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| _think4 | 16,136 | 12 | `none` | 6,125 | 5,824 / 6,754 / 5,797 | 0.84 | 10 % | 146 / 730 |  |
| _think4 | 16,136 | 16 | `none` | 2,680 | 2,926 / 2,771 / 2,344 | 0.39 | 65 % | 647 / 2,252 |  |
| _think4 | 16,136 | 24 | `none` | 1,868 | 1,899 / 1,876 / 1,829 | 0.09 | 76 % | 1,156 / 3,871 |  |
| _think4 | 16,136 | 12 | `gate:8` | 6,438 | 5,707 / 6,957 / 6,652 | 0.86 | 4 % | 141 / 543 |  |
| _think4 | 16,136 | 16 | `gate:8` | 6,887 | 6,142 / 7,531 / 6,987 | 0.87 | 4 % | 210 / 699 |  |
| as run | 16,136 | 12 | `none` | 4,843 | 4,739 / 4,853 / 4,938 | 0.73 | 36 % | 232 / 1,158 |  |

### Long tools: 10 % of tool calls take 10-60 s more

| scripts | pool (blocks) | N | policy | work calls/h | per seed | hit | GPU on evicted recompute | turn p50 / p95 (s) | extra |
|---|---|---|---|---|---|---|---|---|---|
| _longtools | 16,136 | 16 | `none` | 2,814 | 2,617 / 3,039 / 2,785 | 0.43 | 63 % | 878 / 2,099 |  |
| _longtools | 16,136 | 32 | `none` | 1,920 | 1,932 / 1,967 / 1,860 | 0.10 | 73 % | 1,649 / 5,124 |  |
| _longtools | 16,136 | 32 | `gate:8` | 5,297 | 4,933 / 5,550 / 5,407 | 0.79 | 24 % | 841 / 2,271 |  |
| _longtools | 16,136 | 32 | `share:0.9:start` | 2,372 | 2,961 / 2,218 / 1,938 | 0.29 | 68 % | 1,082 / 4,263 | cap releases 80 |
| _longtools | 16,136 | 32 | `share:0.9:start+aftlong` | 4,955 | 4,582 / 5,385 / 4,899 | 0.79 | 21 % | 971 / 1,629 | cap releases 130 |
