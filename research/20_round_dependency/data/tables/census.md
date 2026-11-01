# Static census (rd_static.py)

Every round of both sources. 'copies prev' = a salient argument string first appeared in the previous round's results; 'copies none' = no argument string came from any result of the turn; 'effect prev' = the round's effects conflict with the previous round's; 'free of prev' = neither; 'joins group' = the greedy static merge group takes the round; 'groups/rounds' = merged calls per turn over recorded calls; 'path/rounds' = the static critical path over recorded calls; 'tool s beside join' = share of tool seconds spent in rounds whose next round joins their group (CPU work an early next call could overlap).

## t19

| subset | rounds | turns | rounds/turn | multi-call | todo-only | copies prev | copies none | effect prev | free of prev | joins group | of these commute | groups/rounds | path/rounds | tool s/round | >=1 s tools | tool s beside join | out tok/round |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 39,924 | 3,887 | 10.3 | 41% | 1% | 13% | 58% | 56% | 39% | 84% | 33% | 0.32 | 0.28 | 0.01 | 0% | 77% | 369 |
| kind E1 | 4,009 | 692 | 5.8 | 77% | 1% | 18% | 44% | 11% | 72% | 80% | 84% | 0.47 | 0.47 | 0.01 | 0% | 69% | 512 |
| kind E2 | 8,167 | 677 | 12.1 | 42% | 0% | 29% | 29% | 40% | 48% | 67% | 62% | 0.44 | 0.35 | 0.00 | 0% | 53% | 287 |
| kind E3 | 2,547 | 669 | 3.8 | 71% | 0% | 36% | 7% | 28% | 46% | 58% | 71% | 0.66 | 0.66 | 0.00 | 0% | 50% | 422 |
| kind M1 | 8,789 | 614 | 14.3 | 48% | 1% | 6% | 81% | 48% | 52% | 91% | 37% | 0.21 | 0.18 | 0.01 | 0% | 89% | 418 |
| kind M2 | 3,563 | 615 | 5.8 | 34% | 0% | 8% | 79% | 62% | 37% | 83% | 33% | 0.46 | 0.45 | 0.00 | 0% | 53% | 217 |
| kind M3 | 12,849 | 620 | 20.7 | 22% | 1% | 4% | 66% | 82% | 18% | 93% | 6% | 0.15 | 0.13 | 0.01 | 0% | 81% | 375 |
| cell w08_none | 6,376 | 594 | 10.7 | 39% | 1% | 11% | 61% | 56% | 39% | 85% | 33% | 0.30 | 0.27 | 0.01 | 0% | 77% | 364 |
| cell w16_none | 5,567 | 520 | 10.7 | 39% | 1% | 12% | 59% | 56% | 39% | 84% | 34% | 0.31 | 0.27 | 0.00 | 0% | 75% | 354 |
| cell w32_none | 5,343 | 547 | 9.8 | 38% | 1% | 12% | 55% | 55% | 40% | 84% | 35% | 0.32 | 0.29 | 0.01 | 0% | 74% | 352 |
| cell p08_none | 2,465 | 249 | 9.9 | 43% | 2% | 13% | 60% | 55% | 39% | 83% | 33% | 0.33 | 0.29 | 0.01 | 0% | 70% | 376 |
| cell p16_none | 2,277 | 245 | 9.3 | 44% | 0% | 15% | 57% | 54% | 40% | 82% | 37% | 0.34 | 0.31 | 0.01 | 0% | 73% | 375 |
| cell w16_sgate | 7,467 | 737 | 10.1 | 40% | 1% | 13% | 57% | 59% | 37% | 84% | 31% | 0.32 | 0.29 | 0.01 | 0% | 82% | 376 |
| cell w32_sgate | 7,532 | 691 | 10.9 | 41% | 1% | 13% | 58% | 55% | 39% | 84% | 32% | 0.31 | 0.27 | 0.01 | 0% | 74% | 378 |
| cell p16_sgate | 2,897 | 304 | 9.5 | 44% | 0% | 14% | 60% | 50% | 43% | 82% | 36% | 0.35 | 0.31 | 0.01 | 0% | 73% | 391 |

## t16

| subset | rounds | turns | rounds/turn | multi-call | todo-only | copies prev | copies none | effect prev | free of prev | joins group | of these commute | groups/rounds | path/rounds | tool s/round | >=1 s tools | tool s beside join | out tok/round |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all | 77,445 | 1,760 | 44.0 | 9% | 5% | 7% | 39% | 76% | 22% | 90% | 10% | 0.14 | 0.10 | 2.85 | 12% | 88% | 278 |
| swe (all jobs) | 53,614 | 1,072 | 50.0 | 6% | 4% | 8% | 34% | 78% | 20% | 89% | 9% | 0.15 | 0.10 | 0.82 | 10% | 91% | 212 |
| swepro (all jobs) | 10,956 | 334 | 32.8 | 19% | 11% | 6% | 39% | 52% | 45% | 89% | 23% | 0.16 | 0.14 | 5.29 | 15% | 96% | 253 |
| tbench (all jobs) | 12,875 | 354 | 36.4 | 13% | 3% | 3% | 60% | 88% | 11% | 94% | 4% | 0.10 | 0.08 | 9.19 | 16% | 83% | 576 |
| mm/swe | 6,214 | 90 | 69.0 | 4% | 3% | 6% | 32% | 79% | 19% | 91% | 8% | 0.12 | 0.07 | 0.52 | 7% | 93% | 169 |
| mm/swepro | 3,368 | 90 | 37.4 | 18% | 10% | 6% | 39% | 52% | 45% | 90% | 22% | 0.14 | 0.12 | 4.71 | 17% | 96% | 233 |
| mm/tbench | 3,866 | 110 | 35.1 | 15% | 4% | 3% | 57% | 88% | 12% | 94% | 4% | 0.10 | 0.08 | 4.73 | 14% | 92% | 545 |
| mm-epic+prefix/swe | 23,029 | 491 | 46.9 | 6% | 4% | 8% | 34% | 78% | 20% | 88% | 9% | 0.15 | 0.10 | 0.85 | 10% | 91% | 219 |
| mm-epic+prefix/swepro | 3,750 | 122 | 30.7 | 19% | 12% | 7% | 39% | 53% | 43% | 88% | 23% | 0.18 | 0.15 | 5.61 | 16% | 95% | 263 |
| mm-epic+prefix/tbench | 4,405 | 122 | 36.1 | 12% | 4% | 3% | 63% | 88% | 12% | 94% | 4% | 0.10 | 0.08 | 11.77 | 16% | 81% | 592 |
| mm-epic+prefix_aa/swe | 24,371 | 491 | 49.6 | 6% | 4% | 8% | 34% | 78% | 20% | 88% | 9% | 0.15 | 0.10 | 0.87 | 10% | 90% | 216 |
| mm-epic+prefix_aa/swepro | 3,838 | 122 | 31.5 | 19% | 12% | 6% | 38% | 51% | 46% | 89% | 24% | 0.17 | 0.14 | 5.49 | 14% | 95% | 261 |
| mm-epic+prefix_aa/tbench | 4,604 | 122 | 37.7 | 13% | 3% | 3% | 60% | 89% | 11% | 95% | 4% | 0.09 | 0.08 | 10.47 | 17% | 82% | 587 |

