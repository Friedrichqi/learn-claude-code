## F2 — removable rounds versus retention budget (26 baseline successors)

Unit of retention is one whole read item. Each cell is pooled removable read-rounds as a share of all read-issuing rounds; the last column is an unlimited budget.

| ordering | 2 KB | 4 KB | 8 KB | 16 KB | 32 KB | 64 KB | 128 KB | 256 KB | 512 KB | 1024 KB | unlimited |
|---|---|---|---|---|---|---|---|---|---|---|---|
| most-recent-first (LRU) | 0% | 0% | 2% | 6% | 19% | 36% | 49% | 52% | 52% | 52% | 52% |
| graph-ordered (preds → closure → rest) | 0% | 0% | 2% | 6% | 18% | 35% | 51% | 52% | 52% | 52% | 52% |
| largest spans first | 0% | 0% | 0% | 6% | 13% | 40% | 52% | 52% | 52% | 52% | 52% |
| smallest spans first | 0% | 0% | 0% | 6% | 19% | 38% | 51% | 52% | 52% | 52% | 52% |
| oracle by byte density | 0% | 0% | 1% | 8% | 39% | 51% | 52% | 52% | 52% | 52% | 52% |
| **cheapest whole rounds first** | 0% | 0% | 2% | 9% | 39% | 51% | 52% | 52% | 52% | 52% | 52% |

### The same grid as byte recall (what accumulates smoothly)

| ordering | 2 KB | 4 KB | 8 KB | 16 KB | 32 KB | 64 KB | 128 KB | 256 KB | 512 KB | 1024 KB | unlimited |
|---|---|---|---|---|---|---|---|---|---|---|---|
| most-recent-first (LRU) | 2% | 4% | 6% | 12% | 20% | 41% | 50% | 55% | 55% | 55% | 55% |
| graph-ordered (preds → closure → rest) | 2% | 4% | 7% | 11% | 15% | 35% | 53% | 55% | 55% | 55% | 55% |
| largest spans first | 2% | 4% | 5% | 11% | 20% | 44% | 55% | 55% | 55% | 55% | 55% |
| smallest spans first | 2% | 3% | 6% | 12% | 22% | 31% | 49% | 55% | 55% | 55% | 55% |
| oracle by byte density | 3% | 5% | 7% | 14% | 36% | 49% | 55% | 55% | 55% | 55% | 55% |
| **cheapest whole rounds first** | 0% | 0% | 1% | 11% | 32% | 47% | 55% | 55% | 55% | 55% | 55% |

### Graph-ordered minus most-recent-first, at each budget (removable rounds)

| budget | graph | recent | difference |
|---|---|---|---|
| 2 KB | 0% | 0% | +0 pts |
| 4 KB | 0% | 0% | +0 pts |
| 8 KB | 2% | 2% | +0 pts |
| 16 KB | 6% | 6% | +0 pts |
| 32 KB | 18% | 19% | -1 pts |
| 64 KB | 35% | 36% | -1 pts |
| 128 KB | 51% | 49% | +1 pts |
| 256 KB | 52% | 52% | +0 pts |
| 512 KB | 52% | 52% | +0 pts |
| 1024 KB | 52% | 52% | +0 pts |
| unlimited | 52% | 52% | +0 pts |

Graph ordering is ahead at **1 of 11** budget points. The pre-registered rule: if it is never ahead, the task board has nothing to offer a retention policy and the DAG-directed retention proposal should be retired rather than prototyped.

Median candidate pool per successor: 75 KB.

### Invariants (these must hold or the budgeted key-set builder is wrong)

- every ordering converges at an unlimited budget: **True** (ordering can only matter under a constraint) — 52% of rounds, which is the ceiling of this candidate pool.
- buying cheapest whole rounds first dominates every other ordering at every budget: **True** (it optimises the metric directly; the byte-density oracle does not, which is the point).
- removable rounds are non-decreasing in budget for every ordering: **True**.
