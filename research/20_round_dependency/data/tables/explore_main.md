# Exploratory breakdowns of `main` (rd_explore.py; not pre-registered)

## t19: by tool transition (round j-1 -> round j)

| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |
|---|---|---|---|---|---|
| read -> read | 574 | 480 | 44% | 0.43 | 34% |
| edit -> edit | 289 | 269 | 98% | 0.99 | 88% |
| read -> edit | 217 | 187 | 10% | 0.12 | 10% |
| edit -> read | 143 | 108 | 65% | 0.65 | 48% |
| read -> shell | 83 | 19 | 16% | 0.29 | 4% |
| shell -> read | 82 | 62 | 15% | 0.23 | 11% |
| edit -> shell | 60 | 9 | 89% | 1.07 | 12% |
| shell -> shell | 55 | 11 | 0% | 0.11 | 0% |
| shell -> edit | 40 | 33 | 3% | 0.06 | 2% |
| mixed -> read | 33 | 30 | 40% | 0.44 | 42% |
| read -> mixed | 27 | 22 | 32% | 0.40 | 19% |

## t19: by position in the turn (j over the turn's rounds)

| quartile | pairs | independent of j-1 | mergeable |
|---|---|---|---|
| Q1 | 371 | 42% | 31% |
| Q2 | 495 | 50% | 40% |
| Q3 | 507 | 55% | 39% |
| Q4 | 248 | 47% | 30% |

t19: when round j depends on round j-1, the pending samples other_tool 64%, reissue_pending 29%, same 6%, no_call 1%

## t16: by tool transition (round j-1 -> round j)

| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |
|---|---|---|---|---|---|
| shell -> shell | 697 | 260 | 25% | 0.30 | 9% |
| read -> read | 252 | 203 | 25% | 0.28 | 21% |
| edit -> shell | 178 | 145 | 95% | 0.97 | 75% |
| shell -> read | 160 | 112 | 18% | 0.24 | 12% |
| read -> shell | 124 | 48 | 21% | 0.28 | 6% |
| shell -> edit | 104 | 81 | 4% | 0.06 | 2% |
| read -> edit | 94 | 78 | 8% | 0.10 | 9% |
| edit -> edit | 87 | 80 | 95% | 0.96 | 86% |
| todo -> edit | 74 | 65 | 97% | 1.06 | 74% |
| shell -> todo | 63 | 57 | 19% | 0.23 | 17% |
| read -> todo | 60 | 50 | 18% | 0.22 | 13% |
| edit -> todo | 53 | 51 | 92% | 0.92 | 77% |
| todo -> shell | 48 | 25 | 100% | 1.02 | 50% |
| edit -> read | 47 | 31 | 87% | 1.02 | 57% |
| todo -> read | 27 | 21 | 100% | 1.10 | 70% |
| mixed -> shell | 24 | 15 | 73% | 0.95 | 46% |
| mixed -> read | 21 | 19 | 16% | 0.14 | 14% |
| read -> mixed | 20 | 15 | 13% | 0.18 | 5% |
| mixed -> mixed | 19 | 15 | 33% | 0.38 | 26% |
| shell -> mixed | 16 | 15 | 27% | 0.29 | 31% |

## t16: by position in the turn (j over the turn's rounds)

| quartile | pairs | independent of j-1 | mergeable |
|---|---|---|---|
| Q1 | 537 | 39% | 26% |
| Q2 | 570 | 54% | 35% |
| Q3 | 594 | 42% | 24% |
| Q4 | 482 | 35% | 19% |

t16: when round j depends on round j-1, the pending samples other_tool 65%, reissue_pending 30%, same 5%, no_call 0%

## all: by tool transition (round j-1 -> round j)

| transition | pairs | determinate | independent of j-1 | rho P/C | mergeable |
|---|---|---|---|---|---|
| read -> read | 826 | 683 | 39% | 0.39 | 30% |
| shell -> shell | 752 | 271 | 24% | 0.29 | 8% |
| edit -> edit | 376 | 349 | 97% | 0.99 | 88% |
| read -> edit | 311 | 265 | 9% | 0.11 | 9% |
| shell -> read | 242 | 174 | 17% | 0.23 | 12% |
| edit -> shell | 238 | 154 | 95% | 0.97 | 59% |
| read -> shell | 207 | 67 | 19% | 0.28 | 5% |
| edit -> read | 190 | 139 | 70% | 0.73 | 51% |
| shell -> edit | 144 | 114 | 4% | 0.06 | 2% |
| todo -> edit | 75 | 65 | 97% | 1.06 | 73% |
| shell -> todo | 64 | 58 | 21% | 0.24 | 19% |
| read -> todo | 61 | 50 | 18% | 0.22 | 13% |
| mixed -> read | 54 | 49 | 31% | 0.34 | 31% |
| edit -> todo | 53 | 51 | 92% | 0.92 | 77% |
| todo -> shell | 48 | 25 | 100% | 1.02 | 50% |
| read -> mixed | 47 | 37 | 24% | 0.31 | 13% |
| mixed -> shell | 27 | 15 | 73% | 0.98 | 41% |
| todo -> read | 27 | 21 | 100% | 1.10 | 70% |
| mixed -> mixed | 25 | 20 | 40% | 0.46 | 32% |
| shell -> mixed | 19 | 18 | 22% | 0.26 | 26% |

## all: by position in the turn (j over the turn's rounds)

| quartile | pairs | independent of j-1 | mergeable |
|---|---|---|---|
| Q1 | 908 | 40% | 28% |
| Q2 | 1065 | 52% | 37% |
| Q3 | 1101 | 49% | 31% |
| Q4 | 730 | 39% | 23% |

all: when round j depends on round j-1, the pending samples other_tool 64%, reissue_pending 30%, same 6%, no_call 0%

## Chain group lengths (rounds per merged call)

- t16: 1: 1364 (78%), 2: 291 (17%), 3: 67 (4%), 4: 20 (1%), 5: 4 (0%), 6: 2 (0%), >6: 9; mean 1.33
- t19: 1: 1193 (80%), 2: 206 (14%), 3: 57 (4%), 4: 12 (1%), 5: 8 (1%), 6: 3 (0%), >6: 15; mean 1.39

## CPU-heavy rounds (>= 1 s of tools)

346 rounds (median 6.9 s of tools); the next round is independent of their results in 37/198 determinate cases (19%).

