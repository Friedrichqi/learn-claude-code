# Exploratory breakdowns of `pilot` (rd_explore.py; not pre-registered)

## t19: by tool transition (round j-1 -> round j)

| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |
|---|---|---|---|---|---|
| read -> read | 48 | 39 | 38% | 0.40 | 33% |
| shell -> shell | 17 | 3 | 0% | 0.10 | 0% |
| read -> edit | 12 | 11 | 0% | 0.04 | 0% |
| edit -> edit | 12 | 12 | 100% | 0.97 | 100% |
| edit -> read | 10 | 9 | 78% | 0.98 | 70% |

## t19: by position in the turn (j over the turn's rounds)

| quartile | pairs | independent of j-1 | mergeable |
|---|---|---|---|
| Q1 | 30 | 33% | 23% |
| Q2 | 39 | 41% | 33% |
| Q3 | 42 | 54% | 40% |
| Q4 | 21 | 38% | 24% |

t19: when round j depends on round j-1, the pending samples other_tool 58%, reissue_pending 37%, same 5%, truncated 0%

## t16: by tool transition (round j-1 -> round j)

| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |
|---|---|---|---|---|---|
| shell -> shell | 54 | 25 | 16% | 0.18 | 7% |
| read -> read | 18 | 15 | 13% | 0.27 | 11% |
| edit -> shell | 17 | 14 | 100% | 1.00 | 82% |
| shell -> read | 13 | 9 | 44% | 0.51 | 31% |
| read -> edit | 11 | 9 | 22% | 0.16 | 18% |

## t16: by position in the turn (j over the turn's rounds)

| quartile | pairs | independent of j-1 | mergeable |
|---|---|---|---|
| Q1 | 49 | 38% | 22% |
| Q2 | 51 | 64% | 49% |
| Q3 | 53 | 49% | 34% |
| Q4 | 40 | 38% | 20% |

t16: when round j depends on round j-1, the pending samples other_tool 64%, reissue_pending 29%, same 7%

## all: by tool transition (round j-1 -> round j)

| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |
|---|---|---|---|---|---|
| shell -> shell | 71 | 28 | 14% | 0.18 | 6% |
| read -> read | 66 | 54 | 31% | 0.37 | 27% |
| read -> edit | 23 | 20 | 10% | 0.09 | 9% |
| edit -> shell | 23 | 19 | 89% | 0.94 | 74% |
| shell -> read | 20 | 13 | 38% | 0.35 | 25% |
| edit -> edit | 17 | 17 | 100% | 0.96 | 100% |
| read -> shell | 16 | 5 | 0% | 0.14 | 0% |
| shell -> edit | 12 | 10 | 10% | 0.12 | 8% |
| edit -> read | 10 | 9 | 78% | 0.98 | 70% |

## all: by position in the turn (j over the turn's rounds)

| quartile | pairs | independent of j-1 | mergeable |
|---|---|---|---|
| Q1 | 79 | 36% | 23% |
| Q2 | 90 | 54% | 42% |
| Q3 | 95 | 51% | 37% |
| Q4 | 61 | 38% | 21% |

all: when round j depends on round j-1, the pending samples other_tool 61%, reissue_pending 32%, same 6%, truncated 0%

## Chain group lengths (rounds per merged call)

- t16: 1: 108 (72%), 2: 30 (20%), 3: 7 (5%), 4: 2 (1%), 5: 1 (1%), >6: 1; mean 1.42
- t19: 1: 100 (82%), 2: 10 (8%), 3: 9 (7%), 4: 1 (1%), 5: 1 (1%), >6: 1; mean 1.34

## CPU-heavy rounds (>= 1 s of tools)

33 rounds (median 2.1 s of tools); the next round is independent of their results in 5/15 determinate cases (33%).

