# e4 spot traces

Generated from `report/e4_minimax-m2.7-epic.json` (log `runs/epic/minimax-m2.7-epic.calls.jsonl`, first 177,539,639 bytes, sha256 `33407b5cda6350f3…`). Per run: the joined lead calls (agent `calls.jsonl` record → the last non-aborted server record of (spec, call)), the charged tokens and their source field, the time model's price of each call, the post-snip flag, then the run's totals as they enter the table's per-run means. Outcomes (reward) are shown because the run is over.

## swe · baseline (full recomputation) · block e4r0 · django__django-11815

- trial `django__django-11815__tLcVUsM` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 358.5 s
- table row (all 90 runs of this arm): runs 90, calls 4178, computed tok / call 7,223, / run 335,313, LLM 219 s, tool 28 s, job 247 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | ideal_C | 188 | 9668 | 4122 |  |  |  |
| 1 | 5207 | 3079 | ideal_C | 53 | 3055 | 1516 |  |  |  |
| 2 | 7913 | 2665 | ideal_C | 375 | 24060 | 7846 |  |  |  |
| 3 | 8309 | 37 | ideal_C | 108 | 6499 | 2449 |  |  |  |
| 4 | 8534 | 118 | ideal_C | 49 | 2611 | 1290 |  |  |  |
| 5 | 8712 | 136 | ideal_C | 106 | 4542 | 2415 |  |  |  |
| 6 | 8869 | 53 | ideal_C | 197 | 11525 | 4205 |  |  |  |
| 7 | 9113 | 57 | ideal_C | 214 | 10074 | 4541 |  |  |  |
| 8 | 9348 | 36 | ideal_C | 272 | 12099 | 5684 |  |  |  |
| 9 | 9648 | 32 | ideal_C | 142 | 6954 | 3120 |  |  |  |
| 10 | 9849 | 73 | ideal_C | 241 | 13215 | 5074 |  |  |  |
| 11 | 10179 | 99 | ideal_C | 94 | 4658 | 2176 |  |  |  |
| 12 | 10302 | 30 | ideal_C | 59 | 2637 | 1482 |  |  |  |
| 13 | 11824 | 1472 | ideal_C | 277 | 14643 | 5854 |  |  |  |
| 14 | 12209 | 113 | ideal_C | 118 | 6390 | 2650 |  |  |  |
| 15 | 12793 | 473 | ideal_C | 464 | 26490 | 9492 |  |  |  |
| 16 | 13315 | 67 | ideal_C | 117 | 8861 | 2628 |  |  |  |
| 17 | 13453 | 29 | ideal_C | 68 | 4595 | 1660 |  |  |  |
| 18 | 14266 | 746 | ideal_C | 881 | 59858 | 17731 |  |  |  |
| 19 | 15173 | 37 | ideal_C | 73 | 7372 | 1759 |  |  |  |
| 20 | 15312 | 80 | ideal_C | 61 | 5326 | 1524 |  |  |  |
| 21 | 15593 | 233 | ideal_C | 126 | 9439 | 2814 |  |  |  |
| 22 | 15745 | 33 | ideal_C | 63 | 6054 | 1561 |  |  |  |
| 23 | 13655 | 8455 | ideal_C | 132 | 8398 | 3342 | yes |  |  |
| 24 | 13412 | 8196 | ideal_C | 273 | 19375 | 6110 | yes |  |  |
| 25 | 13570 | 8354 | ideal_C | 78 | 7929 | 2272 | yes |  |  |
| 26 | 13686 | 8470 | ideal_C | 67 | 8915 | 2060 | yes |  |  |
| 27 | 13652 | 8452 | ideal_C | 112 | 8702 | 2947 | yes |  |  |
| 28 | 13541 | 8341 | ideal_C | 287 | 23779 | 6393 | yes |  |  |

Run totals: calls 29; charged 61,908 of 329,124 prompt tokens (18.8%); model LLM time 116.7 s; tool+harness max(0, 358.5 − 337.7) = 20.8 s; model job time 137.5 s; post-snip calls 6.

## swe · baseline (full recomputation) · block e4r0 · django__django-11951

- trial `django__django-11951__tMmKG8L` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 223.9 s
- table row (all 90 runs of this arm): runs 90, calls 4178, computed tok / call 7,223, / run 335,313, LLM 219 s, tool 28 s, job 247 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | ideal_C | 156 | 14843 | 3480 |  |  |  |
| 1 | 2414 | 542 | ideal_C | 146 | 12350 | 3224 |  |  |  |
| 2 | 3166 | 622 | ideal_C | 298 | 26520 | 6226 |  |  |  |
| 3 | 3523 | 67 | ideal_C | 166 | 17002 | 3595 |  |  |  |
| 4 | 3710 | 30 | ideal_C | 207 | 17411 | 4401 |  |  |  |
| 5 | 3944 | 40 | ideal_C | 79 | 5251 | 1877 |  |  |  |
| 6 | 4274 | 258 | ideal_C | 92 | 6492 | 2145 |  |  |  |
| 7 | 4387 | 35 | ideal_C | 833 | 51051 | 16749 |  |  |  |
| 8 | 5252 | 36 | ideal_C | 48 | 4203 | 1266 |  |  |  |
| 9 | 5456 | 160 | ideal_C | 57 | 5417 | 1449 |  |  |  |
| 10 | 6160 | 656 | ideal_C | 55 | 3280 | 1435 |  |  |  |
| 11 | 7013 | 805 | ideal_C | 54 | 4821 | 1422 |  |  |  |
| 12 | 7199 | 143 | ideal_C | 111 | 10211 | 2514 |  |  |  |
| 13 | 7452 | 156 | ideal_C | 71 | 7011 | 1725 |  |  |  |
| 14 | 8582 | 1062 | ideal_C | 69 | 5165 | 1731 |  |  |  |
| 15 | 8670 | 30 | ideal_C | 82 | 4136 | 1936 |  |  |  |
| 16 | 8773 | 37 | ideal_C | 209 | 14972 | 4441 |  |  |  |

Run totals: calls 17; charged 6,411 of 91,707 prompt tokens (7.0%); model LLM time 59.6 s; tool+harness max(0, 223.9 − 210.1) = 13.8 s; model job time 73.4 s; post-snip calls 0.

## swe · prefix_aa · block e4r0 · django__django-11815

- trial `django__django-11815__w8xsgSH` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 452.7 s
- table row (all 90 runs of this arm): runs 90, calls 5059, computed tok / call 7,903, / run 444,214, LLM 277 s, tool 39 s, job 315 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | ideal_C | 111 | 5879 | 2603 |  |  |  |
| 1 | 4774 | 2726 | ideal_C | 54 | 2639 | 1518 |  |  |  |
| 2 | 7481 | 2665 | ideal_C | 286 | 18964 | 6091 |  |  |  |
| 3 | 7788 | 28 | ideal_C | 665 | 35588 | 13435 |  |  |  |
| 4 | 8485 | 37 | ideal_C | 47 | 2475 | 1246 |  |  |  |
| 5 | 8688 | 160 | ideal_C | 49 | 2296 | 1292 |  |  |  |
| 6 | 8815 | 79 | ideal_C | 44 | 2585 | 1189 |  |  |  |
| 7 | 9444 | 596 | ideal_C | 58 | 2877 | 1491 |  |  |  |
| 8 | 9521 | 33 | ideal_C | 43 | 2220 | 1167 |  |  |  |
| 9 | 9779 | 227 | ideal_C | 130 | 7622 | 2893 |  |  |  |
| 10 | 9930 | 26 | ideal_C | 414 | 21247 | 8484 |  |  |  |
| 11 | 10372 | 36 | ideal_C | 48 | 2842 | 1266 |  |  |  |
| 12 | 10619 | 203 | ideal_C | 187 | 8707 | 4016 |  |  |  |
| 13 | 10827 | 27 | ideal_C | 64 | 3691 | 1581 |  |  |  |
| 14 | 10910 | 30 | ideal_C | 60 | 3515 | 1502 |  |  |  |
| 15 | 11424 | 464 | ideal_C | 84 | 3504 | 1997 |  |  |  |
| 16 | 18081 | 6577 | ideal_C | 1274 | 82341 | 25772 |  |  |  |
| 17 | 19381 | 37 | ideal_C | 71 | 7563 | 1719 |  |  |  |
| 18 | 19481 | 41 | ideal_C | 69 | 6646 | 1680 |  |  |  |
| 19 | 19738 | 202 | ideal_C | 69 | 8337 | 1688 |  |  |  |
| 20 | 19903 | 111 | ideal_C | 91 | 7681 | 2118 |  |  |  |
| 21 | 20090 | 106 | ideal_C | 837 | 56721 | 16831 |  |  |  |
| 22 | 18304 | 13536 | ideal_C | 52 | 6978 | 2017 | yes |  |  |
| 23 | 19046 | 14262 | ideal_C | 80 | 8985 | 2605 | yes |  |  |
| 24 | 20521 | 15753 | ideal_C | 153 | 13777 | 4119 | yes |  |  |
| 25 | 20177 | 15409 | ideal_C | 240 | 17869 | 5818 | yes |  |  |
| 26 | 20332 | 15564 | ideal_C | 55 | 7292 | 2177 | yes |  |  |
| 27 | 21514 | 16746 | ideal_C | 100 | 8918 | 3123 | yes |  |  |
| 28 | 21006 | 16238 | ideal_C | 50 | 8329 | 2112 | yes |  |  |
| 29 | 20998 | 16214 | ideal_C | 67 | 8937 | 2446 | yes |  |  |
| 30 | 21214 | 16446 | ideal_C | 51 | 7275 | 2142 | yes |  |  |
| 31 | 21154 | 16386 | ideal_C | 63 | 6156 | 2375 | yes |  |  |
| 32 | 20890 | 16106 | ideal_C | 225 | 14776 | 5557 | yes |  |  |
| 33 | 20920 | 16136 | ideal_C | 364 | 18873 | 8300 | yes |  |  |

Run totals: calls 34; charged 205,149 of 503,549 prompt tokens (40.7%); model LLM time 144.4 s; tool+harness max(0, 452.7 − 424.1) = 28.6 s; model job time 173.0 s; post-snip calls 12.

## swe · prefix_aa · block e4r0 · django__django-11951

- trial `django__django-11951__ouK7Kd8` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 397.0 s
- table row (all 90 runs of this arm): runs 90, calls 5059, computed tok / call 7,903, / run 444,214, LLM 277 s, tool 39 s, job 315 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | ideal_C | 207 | 16805 | 4486 |  |  |  |
| 1 | 3060 | 1124 | ideal_C | 259 | 23600 | 5482 |  |  |  |
| 2 | 3651 | 339 | ideal_C | 216 | 21372 | 4594 |  |  |  |
| 3 | 3888 | 32 | ideal_C | 915 | 64697 | 18366 |  |  |  |
| 4 | 4836 | 36 | ideal_C | 47 | 5510 | 1246 |  |  |  |
| 5 | 5040 | 160 | ideal_C | 56 | 3897 | 1430 |  |  |  |
| 6 | 5744 | 656 | ideal_C | 51 | 3164 | 1356 |  |  |  |
| 7 | 6594 | 802 | ideal_C | 52 | 2489 | 1383 |  |  |  |
| 8 | 6736 | 96 | ideal_C | 819 | 58100 | 16476 |  |  |  |
| 9 | 7604 | 52 | ideal_C | 43 | 3279 | 1168 |  |  |  |
| 10 | 8472 | 840 | ideal_C | 868 | 59820 | 17479 |  |  |  |
| 11 | 9373 | 45 | ideal_C | 43 | 1967 | 1168 |  |  |  |
| 12 | 9664 | 256 | ideal_C | 131 | 8324 | 2914 |  |  |  |
| 13 | 9816 | 24 | ideal_C | 148 | 10000 | 3237 |  |  |  |
| 14 | 9991 | 39 | ideal_C | 75 | 5512 | 1798 |  |  |  |
| 15 | 10284 | 220 | ideal_C | 893 | 65164 | 17941 |  |  |  |
| 16 | 11226 | 58 | ideal_C | 46 | 4446 | 1227 |  |  |  |
| 17 | 11444 | 180 | ideal_C | 96 | 6375 | 2220 |  |  |  |
| 18 | 11569 | 33 | ideal_C | 61 | 2265 | 1522 |  |  |  |
| 19 | 11763 | 147 | ideal_C | 67 | 3403 | 1646 |  |  |  |
| 20 | 12889 | 1065 | ideal_C | 149 | 6118 | 3309 |  |  |  |
| 21 | 13067 | 43 | ideal_C | 248 | 9324 | 5211 |  |  |  |

Run totals: calls 22; charged 7,979 of 178,443 prompt tokens (4.5%); model LLM time 115.7 s; tool+harness max(0, 397.0 − 385.6) = 11.3 s; model job time 127.0 s; post-snip calls 0.

## swe · shift · block e4r0 · django__django-11815

- trial `django__django-11815__cPXv5cf` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 1063.3 s
- table row (all 90 runs of this arm): runs 90, calls 4698, computed tok / call 543, / run 28,357, LLM 234 s, tool 55 s, job 290 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | C | 128 | 6704 | 2938 |  |  |  |
| 1 | 4873 | 2804 | C | 53 | 5524 | 1502 |  |  |  |
| 2 | 7579 | 2654 | C | 309 | 18582 | 6544 |  |  |  |
| 3 | 7926 | 39 | C | 56 | 2677 | 1424 |  |  |  |
| 4 | 8384 | 403 | C | 373 | 17933 | 7694 |  |  |  |
| 5 | 8890 | 134 | C | 60 | 3551 | 1507 |  |  |  |
| 6 | 9058 | 109 | C | 169 | 8451 | 3656 |  |  |  |
| 7 | 9288 | 62 | C | 409 | 18438 | 8387 |  |  |  |
| 8 | 9735 | 39 | C | 219 | 12592 | 4639 |  |  |  |
| 9 | 10083 | 130 | C | 249 | 12008 | 5235 |  |  |  |
| 10 | 10438 | 107 | C | 245 | 13506 | 5155 |  |  |  |
| 11 | 10740 | 58 | C | 119 | 5142 | 2667 |  |  |  |
| 12 | 11202 | 344 | C | 82 | 5074 | 1952 |  |  |  |
| 13 | 11840 | 557 | C | 867 | 52862 | 17445 |  |  |  |
| 14 | 12733 | 27 | C | 61 | 5188 | 1522 |  |  |  |
| 15 | 13141 | 348 | C | 151 | 11116 | 3313 |  |  |  |
| 16 | 13616 | 325 | C | 103 | 6785 | 2365 |  |  |  |
| 17 | 13823 | 105 | C | 172 | 11000 | 3715 |  |  |  |
| 18 | 14118 | 124 | C | 151 | 14920 | 3302 |  |  |  |
| 19 | 14343 | 75 | C | 227 | 18882 | 4798 |  |  |  |
| 20 | 14791 | 222 | C | 61 | 5168 | 1531 |  |  |  |
| 21 | 15318 | 467 | C | 127 | 9176 | 2845 |  |  |  |
| 22 | 12903 | 163 | C | 151 | 11434 | 3304 | yes |  |  |
| 23 | 12754 | 54 | C | 108 | 10992 | 2450 | yes |  |  |
| 24 | 12621 | 224 | C | 67 | 6519 | 1650 | yes |  |  |
| 25 | 12219 | 55 | C | 70 | 12323 | 1701 | yes |  |  |
| 26 | 12676 | 573 | C | 50 | 5455 | 1332 | yes |  |  |
| 27 | 12529 | 40 | C | 63 | 6798 | 1562 | yes |  |  |
| 28 | 12304 | 166 | C | 150 | 13710 | 3284 | yes |  |  |
| 29 | 12164 | 65 | C | 118 | 10349 | 2648 | yes |  |  |
| 30 | 11964 | 55 | C | 103 | 10857 | 2351 | yes |  |  |
| 31 | 11877 | 130 | C | 166 | 14150 | 3598 | yes |  |  |
| 32 | 11640 | 66 | C | 117 | 10129 | 2628 | yes |  |  |
| 33 | 11312 | 211 | C | 278 | 22149 | 5811 | yes |  |  |
| 34 | 10898 | 219 | C | 67 | 6711 | 1650 | yes |  |  |
| 35 | 10735 | 185 | C | 350 | 16530 | 7230 | yes |  |  |
| 36 | 10898 | 306 | C | 106 | 8424 | 2423 | yes |  |  |
| 37 | 10909 | 130 | C | 63 | 4652 | 1566 | yes |  |  |
| 38 | 10882 | 212 | C | 71 | 5563 | 1728 | yes |  |  |
| 39 | 11002 | 292 | C | 64 | 3233 | 1594 | yes |  |  |
| 40 | 10953 | 353 | C | 181 | 10430 | 3905 | yes |  |  |
| 41 | 10910 | 310 | C | 63 | 4908 | 1575 | yes |  |  |
| 42 | 10911 | 226 | C | 50 | 3615 | 1315 | yes |  |  |
| 43 | 10803 | 58 | C | 63 | 4147 | 1563 | yes |  |  |
| 44 | 10719 | 185 | C | 221 | 11382 | 4685 | yes |  |  |
| 45 | 11092 | 274 | C | 320 | 17151 | 6642 | yes |  |  |
| 46 | 10851 | 82 | C | 306 | 20032 | 6357 | yes | 0.0971 |  |
| 47 | 11230 | 163 | C | 212 | 15103 | 4507 | yes |  |  |
| 48 | 11316 | 114 | C | 274 | 23180 | 5727 | yes |  |  |
| 49 | 11770 | 406 | C | 136 | 12538 | 3020 | yes |  |  |
| 50 | 11775 | 31 | C | 757 | 67803 | 15249 | yes |  |  |
| 51 | 12473 | 174 | C | 98 | 8610 | 2259 | yes |  |  |
| 52 | 12514 | 186 | C | 225 | 17241 | 4764 | yes |  |  |
| 53 | 12457 | 35 | C | 121 | 7553 | 2705 | yes |  |  |
| 54 | 12232 | 151 | C | 884 | 56431 | 17760 | yes |  |  |
| 55 | 12967 | 114 | C | 750 | 52872 | 15116 | yes |  |  |
| 56 | 13330 | 258 | C | 73 | 4970 | 1770 | yes |  |  |
| 57 | 13528 | 361 | C | 100 | 7258 | 2308 | yes |  |  |
| 58 | 13852 | 510 | C | 527 | 35809 | 10737 | yes |  |  |
| 59 | 14062 | 35 | C | 68 | 4356 | 1660 | yes |  |  |
| 60 | 14055 | 342 | C | 77 | 6020 | 1853 | yes |  |  |
| 61 | 14446 | 816 | C | 860 | 63374 | 17320 | yes |  |  |
| 62 | 15061 | 33 | C | 61 | 4418 | 1522 | yes |  |  |
| 63 | 15128 | 114 | C | 70 | 4602 | 1704 | yes |  |  |
| 64 | 15523 | 584 | C | 84 | 6123 | 2003 | yes |  |  |
| 65 | 15490 | 367 | C | 260 | 12848 | 5464 | yes |  |  |
| 66 | 15421 | 73 | C | 116 | 5598 | 2609 | yes |  |  |
| 67 | 15478 | 421 | C | 288 | 9604 | 6019 | yes |  |  |
| 68 | 15484 | 33 | C | 68 | 3110 | 1660 | yes |  |  |
| 69 | 15005 | 133 | C | 60 | 2138 | 1507 | yes |  |  |
| 70 | 16064 | 1177 | C | 67 | 2531 | 1697 | yes |  |  |
| 71 | 15483 | 272 | C | 447 | 15991 | 9147 | yes |  |  |
| 72 | 15747 | 101 | C | 264 | 10671 | 5529 | yes |  |  |
| 73 | 15814 | 74 | C | 75 | 3744 | 1800 | yes |  |  |
| 74 | 15659 | 31 | C | 63 | 2916 | 1561 | yes |  |  |
| 75 | 14834 | 110 | C | 65 | 3008 | 1605 | yes |  |  |
| 76 | 14177 | 297 | C | 48 | 2168 | 1279 | yes |  |  |
| 77 | 13890 | 88 | C | 59 | 3190 | 1485 | yes |  |  |
| 78 | 13386 | 47 | C | 59 | 2267 | 1483 | yes |  |  |
| 79 | 13049 | 177 | C | 70 | 1800 | 1707 | yes |  |  |
| 80 | 12755 | 35 | C | 76 | 2739 | 1818 | yes |  |  |
| 81 | 12634 | 696 | C | 53 | 1352 | 1397 | yes |  |  |
| 82 | 11841 | 58 | C | 63 | 1961 | 1563 | yes |  |  |
| 83 | 12007 | 267 | C | 78 | 4241 | 1869 | yes |  |  |
| 84 | 12498 | 1067 | C | 65 | 2089 | 1652 | yes |  |  |
| 85 | 12196 | 95 | C | 371 | 18192 | 7639 | yes |  |  |

Run totals: calls 86; charged 25,755 of 1,059,018 prompt tokens (2.4%); model LLM time 341.2 s; tool+harness max(0, 1063.3 − 1027.3) = 36.0 s; model job time 377.1 s; post-snip calls 64.

## swe · shift · block e4r0 · django__django-11951

- trial `django__django-11951__dYcZsnn` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 153.2 s
- table row (all 90 runs of this arm): runs 90, calls 4698, computed tok / call 543, / run 28,357, LLM 234 s, tool 55 s, job 290 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | C | 209 | 19482 | 4526 |  |  |  |
| 1 | 2841 | 901 | C | 411 | 37284 | 8468 |  |  |  |
| 2 | 3295 | 44 | C | 86 | 7710 | 2016 |  |  |  |
| 3 | 3645 | 265 | C | 561 | 43746 | 11395 |  |  |  |
| 4 | 4323 | 118 | C | 493 | 29030 | 10047 |  |  |  |
| 5 | 4973 | 158 | C | 231 | 12316 | 4881 |  |  |  |

Run totals: calls 6; charged 3,218 of 20,809 prompt tokens (15.5%); model LLM time 41.3 s; tool+harness max(0, 153.2 − 149.6) = 3.6 s; model job time 44.9 s; post-snip calls 0.

## swe · shift+EPIC · block e4r0 · django__django-11815

- trial `django__django-11815__cXf9iN5` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 417.8 s
- table row (all 90 runs of this arm): runs 90, calls 4564, computed tok / call 647, / run 32,804, LLM 233 s, tool 50 s, job 283 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | C | 263 | 15758 | 5601 |  |  |  |
| 1 | 5008 | 2804 | C | 53 | 2649 | 1502 |  |  |  |
| 2 | 7714 | 2654 | C | 258 | 16744 | 5538 |  |  |  |
| 3 | 14529 | 6558 | C | 350 | 15882 | 7547 |  |  |  |
| 4 | 14996 | 118 | C | 69 | 5669 | 1684 |  |  |  |
| 5 | 15100 | 36 | C | 133 | 6853 | 2942 |  |  |  |
| 6 | 15254 | 22 | C | 230 | 12714 | 4855 |  |  |  |
| 7 | 15546 | 63 | C | 225 | 11187 | 4758 |  |  |  |
| 8 | 15817 | 47 | C | 220 | 11593 | 4659 |  |  |  |
| 9 | 16058 | 22 | C | 220 | 10670 | 4657 |  |  |  |
| 10 | 16306 | 29 | C | 232 | 13059 | 4895 |  |  |  |
| 11 | 16604 | 67 | C | 155 | 9124 | 3378 |  |  |  |
| 12 | 16780 | 22 | C | 92 | 5215 | 2133 |  |  |  |
| 13 | 17242 | 371 | C | 364 | 25188 | 7515 |  |  |  |
| 14 | 17632 | 27 | C | 657 | 44030 | 13277 |  |  |  |
| 15 | 18331 | 43 | C | 70 | 6557 | 1700 |  |  |  |
| 16 | 18430 | 30 | C | 67 | 8035 | 1640 |  |  |  |
| 17 | 18669 | 173 | C | 63 | 8296 | 1568 |  |  |  |
| 18 | 18844 | 113 | C | 80 | 8242 | 1901 |  |  |  |
| 19 | 20626 | 1703 | C | 103 | 7165 | 2434 |  |  |  |
| 20 | 20779 | 51 | C | 87 | 7647 | 2036 |  |  |  |
| 21 | 21145 | 280 | C | 222 | 14054 | 4710 |  |  |  |
| 22 | 21393 | 27 | C | 58 | 5631 | 1463 |  |  |  |
| 23 | 19909 | 1291 | C | 143 | 8813 | 3202 | yes |  |  |
| 24 | 13258 | 156 | C | 242 | 21022 | 5098 | yes |  |  |
| 25 | 13077 | 179 | C | 74 | 8418 | 1786 | yes |  |  |
| 26 | 13144 | 216 | C | 75 | 6069 | 1807 | yes |  |  |
| 27 | 13084 | 181 | C | 62 | 6021 | 1549 | yes |  |  |
| 28 | 12951 | 243 | C | 271 | 21284 | 5674 | yes |  |  |
| 29 | 13009 | 193 | C | 139 | 10852 | 3068 | yes |  |  |
| 30 | 12928 | 156 | C | 396 | 31543 | 8135 | yes |  |  |

Run totals: calls 31; charged 19,817 of 476,105 prompt tokens (4.2%); model LLM time 122.7 s; tool+harness max(0, 417.8 − 386.0) = 31.8 s; model job time 154.5 s; post-snip calls 8.

## swe · shift+EPIC · block e4r0 · django__django-11951

- trial `django__django-11951__6jG8UgB` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 515.4 s
- table row (all 90 runs of this arm): runs 90, calls 4564, computed tok / call 647, / run 32,804, LLM 233 s, tool 50 s, job 283 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | C | 189 | 17390 | 4131 |  |  |  |
| 1 | 2821 | 901 | C | 343 | 30148 | 7127 |  |  |  |
| 2 | 3373 | 210 | C | 127 | 12836 | 2833 |  |  |  |
| 3 | 3521 | 22 | C | 243 | 21322 | 5111 |  |  |  |
| 4 | 3791 | 28 | C | 79 | 5389 | 1877 |  |  |  |
| 5 | 4160 | 291 | C | 104 | 7113 | 2383 |  |  |  |
| 6 | 4309 | 46 | C | 64 | 6730 | 1582 |  |  |  |
| 7 | 4662 | 290 | C | 60 | 5049 | 1515 |  |  |  |
| 8 | 4839 | 118 | C | 56 | 4467 | 1428 |  |  |  |
| 9 | 4973 | 79 | C | 61 | 4508 | 1524 |  |  |  |
| 10 | 5079 | 46 | C | 113 | 9484 | 2548 |  |  |  |
| 11 | 5627 | 436 | C | 70 | 5792 | 1720 |  |  |  |
| 12 | 6171 | 475 | C | 966 | 67592 | 19394 |  |  |  |
| 13 | 7375 | 239 | C | 913 | 61992 | 18337 |  |  |  |
| 14 | 8426 | 139 | C | 1112 | 79948 | 22257 |  |  |  |
| 15 | 9839 | 302 | C | 1256 | 55600 | 25105 |  |  |  |
| 16 | 11350 | 256 | C | 1166 | 43042 | 23328 |  |  |  |
| 17 | 12614 | 99 | C | 543 | 18753 | 11032 |  |  |  |
| 18 | 13317 | 161 | C | 608 | 26934 | 12317 |  |  |  |
| 19 | 14102 | 178 | C | 173 | 7224 | 3738 |  |  |  |
| 20 | 14365 | 91 | C | 122 | 3587 | 2728 |  |  |  |
| 21 | 14508 | 22 | C | 43 | 927 | 1166 |  |  |  |
| 22 | 14039 | 167 | C | 70 | 2303 | 1706 | yes |  |  |
| 23 | 14179 | 353 | C | 177 | 4513 | 3826 | yes |  |  |

Run totals: calls 24; charged 6,681 of 189,172 prompt tokens (3.5%); model LLM time 178.7 s; tool+harness max(0, 515.4 − 502.6) = 12.8 s; model job time 191.5 s; post-snip calls 2.

## swe · shift+EPICa1 · block e4r0 · django__django-11815

- trial `django__django-11815__VqU69xJ` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 518.6 s
- table row (all 90 runs of this arm): runs 90, calls 5180, computed tok / call 4,108, / run 236,428, LLM 259 s, tool 37 s, job 296 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | C | 111 | 5956 | 2603 |  |  |  |
| 1 | 4774 | 2722 | C | 54 | 2895 | 1518 |  |  |  |
| 2 | 7481 | 2654 | C | 299 | 18243 | 6347 |  |  |  |
| 3 | 7801 | 22 | C | 868 | 47144 | 17438 |  |  |  |
| 4 | 8701 | 33 | C | 47 | 1852 | 1246 |  |  |  |
| 5 | 8904 | 157 | C | 70 | 3692 | 1706 |  |  |  |
| 6 | 9052 | 79 | C | 44 | 2419 | 1189 |  |  |  |
| 7 | 9681 | 586 | C | 55 | 2694 | 1431 |  |  |  |
| 8 | 10534 | 799 | C | 72 | 3777 | 1777 |  |  |  |
| 9 | 10684 | 79 | C | 44 | 2184 | 1189 |  |  |  |
| 10 | 10991 | 264 | C | 214 | 11399 | 4551 |  |  |  |
| 11 | 11233 | 29 | C | 53 | 2894 | 1364 |  |  |  |
| 12 | 11326 | 41 | C | 61 | 2817 | 1522 |  |  |  |
| 13 | 12107 | 721 | C | 221 | 11878 | 4712 |  |  |  |
| 14 | 12349 | 22 | C | 76 | 4018 | 1817 |  |  |  |
| 15 | 12670 | 246 | C | 382 | 19883 | 7864 |  |  |  |
| 16 | 13080 | 29 | C | 764 | 49795 | 15387 |  |  |  |
| 17 | 13891 | 48 | C | 46 | 5860 | 1227 |  |  |  |
| 18 | 14029 | 93 | C | 74 | 5580 | 1781 |  |  |  |
| 19 | 14128 | 26 | C | 42 | 2980 | 1147 |  |  |  |
| 20 | 14403 | 234 | C | 84 | 5124 | 1986 |  |  |  |
| 21 | 14516 | 30 | C | 72 | 6983 | 1739 |  |  |  |
| 22 | 12080 | 311 | C | 65 | 7694 | 1615 | yes |  |  |
| 23 | 11942 | 6942 | C | 76 | 6751 | 2162 | yes |  |  |
| 24 | 11433 | 688 | C | 109 | 10930 | 2501 | yes |  |  |
| 25 | 11887 | 6452 | C | 883 | 59052 | 18054 | yes |  |  |
| 26 | 12664 | 839 | C | 71 | 7279 | 1759 | yes |  |  |
| 27 | 12202 | 6665 | C | 75 | 8285 | 2128 | yes |  |  |
| 28 | 11974 | 1449 | C | 128 | 12513 | 2914 | yes |  |  |
| 29 | 12097 | 5882 | C | 65 | 9164 | 1892 | yes |  |  |
| 30 | 12275 | 2008 | C | 260 | 20996 | 5545 | yes |  |  |
| 31 | 12319 | 5413 | C | 74 | 7138 | 2046 | yes |  |  |
| 32 | 12431 | 2399 | C | 72 | 7856 | 1857 | yes |  |  |
| 33 | 12238 | 5140 | C | 198 | 18638 | 4478 | yes |  |  |
| 34 | 12215 | 2481 | C | 78 | 6831 | 1979 | yes |  |  |
| 35 | 12163 | 4955 | C | 90 | 5727 | 2339 | yes |  |  |
| 36 | 11924 | 2640 | C | 542 | 27188 | 11139 | yes |  |  |
| 37 | 11703 | 3883 | C | 54 | 5164 | 1576 | yes |  |  |
| 38 | 11730 | 3304 | C | 74 | 5230 | 1941 | yes |  |  |
| 39 | 11745 | 3729 | C | 72 | 4022 | 1923 | yes |  |  |
| 40 | 11577 | 3413 | C | 65 | 5110 | 1769 | yes |  |  |
| 41 | 11832 | 3779 | C | 53 | 4092 | 1551 | yes |  |  |
| 42 | 11843 | 3432 | C | 181 | 9698 | 4058 | yes |  |  |
| 43 | 11863 | 3633 | C | 317 | 17027 | 6750 | yes |  |  |

Run totals: calls 44; charged 90,293 of 498,414 prompt tokens (18.1%); model LLM time 163.5 s; tool+harness max(0, 518.6 − 486.5) = 32.1 s; model job time 195.7 s; post-snip calls 22.

## swe · shift+EPICa1 · block e4r0 · django__django-11951

- trial `django__django-11951__NuiGYd5` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 183.2 s
- table row (all 90 runs of this arm): runs 90, calls 5180, computed tok / call 4,108, / run 236,428, LLM 259 s, tool 37 s, job 296 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | C | 200 | 18888 | 4348 |  |  |  |
| 1 | 3053 | 1122 | C | 260 | 24341 | 5501 |  |  |  |
| 2 | 3366 | 54 | C | 68 | 5513 | 1661 |  |  |  |
| 3 | 4659 | 1226 | C | 329 | 30879 | 6867 |  |  |  |
| 4 | 5031 | 44 | C | 220 | 16964 | 4659 |  |  |  |
| 5 | 5294 | 44 | C | 83 | 7149 | 1956 |  |  |  |
| 6 | 5667 | 291 | C | 492 | 29028 | 10036 |  |  |  |
| 7 | 6276 | 118 | C | 292 | 17921 | 6082 |  |  |  |
| 8 | 6707 | 140 | C | 101 | 8459 | 2316 |  |  |  |
| 9 | 6899 | 92 | C | 204 | 16353 | 4345 |  |  |  |

Run totals: calls 10; charged 4,863 of 48,684 prompt tokens (10.0%); model LLM time 47.8 s; tool+harness max(0, 183.2 − 175.5) = 7.7 s; model job time 55.5 s; post-snip calls 0.

## swe · shift+EPICa4 · block e4r0 · django__django-11815

- trial `django__django-11815__yJw92vL` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 516.9 s
- table row (all 90 runs of this arm): runs 90, calls 4569, computed tok / call 1,738, / run 88,210, LLM 235 s, tool 32 s, job 266 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | C | 210 | 10536 | 4556 |  |  |  |
| 1 | 4955 | 2804 | C | 54 | 2441 | 1522 |  |  |  |
| 2 | 7662 | 2654 | C | 356 | 22906 | 7471 |  |  |  |
| 3 | 14585 | 6568 | C | 835 | 43096 | 17113 |  |  |  |
| 4 | 15462 | 43 | C | 46 | 2366 | 1227 |  |  |  |
| 5 | 15664 | 157 | C | 50 | 1994 | 1311 |  |  |  |
| 6 | 15792 | 79 | C | 43 | 2100 | 1169 |  |  |  |
| 7 | 16420 | 586 | C | 51 | 2391 | 1352 |  |  |  |
| 8 | 16649 | 179 | C | 695 | 35972 | 14034 |  |  |  |
| 9 | 17393 | 50 | C | 48 | 2461 | 1266 |  |  |  |
| 10 | 17515 | 75 | C | 102 | 4512 | 2333 |  |  |  |
| 11 | 17644 | 28 | C | 42 | 2844 | 1147 |  |  |  |
| 12 | 17892 | 207 | C | 208 | 11167 | 4430 |  |  |  |
| 13 | 18121 | 22 | C | 413 | 27084 | 8464 |  |  |  |
| 14 | 18562 | 29 | C | 48 | 2981 | 1265 |  |  |  |
| 15 | 18800 | 191 | C | 222 | 13482 | 4705 |  |  |  |
| 16 | 19043 | 22 | C | 83 | 6528 | 1955 |  |  |  |
| 17 | 19155 | 30 | C | 64 | 5974 | 1581 |  |  |  |
| 18 | 20022 | 804 | C | 68 | 5225 | 1698 |  |  |  |
| 19 | 20278 | 189 | C | 64 | 4584 | 1589 |  |  |  |
| 20 | 20404 | 63 | C | 63 | 5346 | 1563 |  |  |  |
| 21 | 20791 | 325 | C | 93 | 9048 | 2168 |  |  |  |
| 22 | 19180 | 1115 | C | 369 | 30790 | 7651 | yes |  |  |
| 23 | 19559 | 161 | C | 666 | 45715 | 13461 | yes |  |  |
| 24 | 13328 | 172 | C | 66 | 8172 | 1628 | yes |  |  |
| 25 | 12427 | 258 | C | 70 | 5912 | 1711 | yes |  |  |
| 26 | 14087 | 6832 | C | 136 | 10550 | 3340 | yes |  |  |
| 27 | 14765 | 2039 | C | 247 | 20959 | 5290 | yes |  |  |
| 28 | 14197 | 880 | C | 69 | 8974 | 1722 | yes |  |  |
| 29 | 14674 | 1374 | C | 82 | 9747 | 2003 | yes |  |  |
| 30 | 14838 | 426 | C | 81 | 11219 | 1936 | yes |  |  |
| 31 | 14894 | 5326 | C | 78 | 9277 | 2121 | yes |  |  |
| 32 | 14876 | 2422 | C | 54 | 6914 | 1503 | yes |  |  |
| 33 | 15104 | 1139 | C | 162 | 10091 | 3569 | yes |  |  |
| 34 | 14715 | 1575 | C | 152 | 8502 | 3394 | yes |  |  |
| 35 | 14727 | 605 | C | 327 | 15178 | 6797 | yes |  |  |
| 36 | 14888 | 4071 | C | 386 | 24406 | 8133 | yes |  |  |
| 37 | 15275 | 2578 | C | 116 | 7237 | 2733 | yes |  |  |
| 38 | 14578 | 1344 | C | 52 | 3979 | 1410 | yes |  |  |
| 39 | 15423 | 2776 | C | 519 | 27438 | 10692 | yes |  |  |

Run totals: calls 40; charged 52,140 of 626,286 prompt tokens (8.3%); model LLM time 163.0 s; tool+harness max(0, 516.9 − 490.1) = 26.8 s; model job time 189.8 s; post-snip calls 18.

## swe · shift+EPICa4 · block e4r0 · django__django-11951

- trial `django__django-11951__s6frmp9` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 252.5 s
- table row (all 90 runs of this arm): runs 90, calls 4569, computed tok / call 1,738, / run 88,210, LLM 235 s, tool 32 s, job 266 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | C | 128 | 12387 | 2928 |  |  |  |
| 1 | 1882 | 23 | C | 126 | 10698 | 2804 |  |  |  |
| 2 | 3150 | 1143 | C | 369 | 33837 | 7652 |  |  |  |
| 3 | 3540 | 22 | C | 243 | 21330 | 5111 |  |  |  |
| 4 | 3810 | 28 | C | 68 | 4439 | 1660 |  |  |  |
| 5 | 4168 | 291 | C | 122 | 9018 | 2738 |  |  |  |
| 6 | 4311 | 22 | C | 1329 | 87461 | 26531 |  |  |  |
| 7 | 5672 | 33 | C | 48 | 5174 | 1266 |  |  |  |
| 8 | 5876 | 157 | C | 51 | 3033 | 1331 |  |  |  |
| 9 | 6574 | 648 | C | 53 | 3714 | 1395 |  |  |  |
| 10 | 7219 | 593 | C | 52 | 3570 | 1372 |  |  |  |
| 11 | 7413 | 143 | C | 107 | 6795 | 2435 |  |  |  |
| 12 | 7565 | 46 | C | 63 | 5104 | 1562 |  |  |  |
| 13 | 7745 | 118 | C | 53 | 3526 | 1368 |  |  |  |
| 14 | 7860 | 63 | C | 59 | 3813 | 1484 |  |  |  |
| 15 | 8026 | 108 | C | 158 | 10049 | 3439 |  |  |  |
| 16 | 8213 | 30 | C | 263 | 18670 | 5506 |  |  |  |

Run totals: calls 17; charged 5,200 of 94,756 prompt tokens (5.5%); model LLM time 70.6 s; tool+harness max(0, 252.5 − 242.6) = 9.9 s; model job time 80.5 s; post-snip calls 0.

## swe · shift+EPIC+recompute@JSD · block e4r0 · django__django-11815

- trial `django__django-11815__Prf47h7` (job `e4r0b11__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 411.9 s
- table row (all 90 runs of this arm): runs 90, calls 5263, computed tok / call 1,301, / run 76,058, LLM 269 s, tool 37 s, job 306 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1942 | 1942 | C_charged | 226 | 11441 | 4871 |  |  |  |
| 1 | 4975 | 2808 | C_charged | 53 | 2725 | 1502 |  |  |  |
| 2 | 7681 | 2654 | C_charged | 255 | 17543 | 5479 |  |  |  |
| 3 | 7957 | 22 | C_charged | 51 | 3592 | 1324 |  |  |  |
| 4 | 8030 | 23 | C_charged | 64 | 3352 | 1581 |  |  |  |
| 5 | 8772 | 679 | C_charged | 598 | 31051 | 12146 |  |  |  |
| 6 | 9418 | 49 | C_charged | 47 | 2155 | 1247 |  |  |  |
| 7 | 9600 | 136 | C_charged | 52 | 3180 | 1350 |  |  |  |
| 8 | 9797 | 146 | C_charged | 139 | 6946 | 3066 |  |  |  |
| 9 | 9957 | 22 | C_charged | 402 | 21140 | 8247 |  |  |  |
| 10 | 10387 | 29 | C_charged | 48 | 2273 | 1265 |  |  |  |
| 11 | 10534 | 100 | C_charged | 127 | 7037 | 2827 |  |  |  |
| 12 | 10682 | 22 | C_charged | 65 | 3876 | 1600 |  |  |  |
| 13 | 10785 | 39 | C_charged | 63 | 3083 | 1562 |  |  |  |
| 14 | 11172 | 325 | C_charged | 90 | 4028 | 2109 |  |  |  |
| 15 | 11922 | 661 | C_charged | 876 | 53546 | 17628 |  |  |  |
| 16 | 12824 | 27 | C_charged | 65 | 4093 | 1601 |  |  |  |
| 17 | 12985 | 97 | C_charged | 83 | 5580 | 1959 |  |  |  |
| 18 | 13150 | 83 | C_charged | 78 | 5282 | 1860 |  |  |  |
| 19 | 14930 | 1703 | C_charged | 84 | 5756 | 2059 |  |  |  |
| 20 | 15434 | 421 | C_charged | 301 | 19774 | 6275 |  |  |  |
| 21 | 15777 | 43 | C_charged | 58 | 6094 | 1463 |  |  |  |
| 22 | 16978 | 1144 | C_charged | 79 | 8184 | 1932 |  |  |  |
| 23 | 14470 | 364 | C_charged | 521 | 40333 | 10611 | yes |  | 0.0014, 0.883, False, 118, 246 |
| 24 | 14764 | 240 | C_charged | 46 | 6622 | 1236 | yes |  | 0.0146, 0.931, False, 56, 184 |
| 25 | 14853 | 374 | C_charged | 498 | 38106 | 10158 | yes |  | 0.1126, 0.557, False, 123, 251 |
| 26 | 14642 | 208 | C_charged | 43 | 10456 | 1176 | yes |  | 0.0128, 0.515, False, 40, 168 |
| 27 | 14211 | 508 | C_charged | 100 | 13084 | 2315 | yes |  | 0.0135, 0.785, False, 190, 318 |
| 28 | 14150 | 206 | C_charged | 50 | 10021 | 1314 | yes |  | 0.0188, 0.509, False, 39, 167 |
| 29 | 14022 | 180 | C_charged | 53 | 8826 | 1372 | yes |  | 0.0538, 0.540, False, 26, 154 |
| 30 | 14133 | 578 | C_charged | 257 | 27165 | 5415 | yes |  | 0.0655, 0.403, False, 225, 353 |

Run totals: calls 31; charged 15,833 of 360,934 prompt tokens (4.4%); model LLM time 118.5 s; tool+harness max(0, 411.9 − 386.3) = 25.6 s; model job time 144.1 s; post-snip calls 8.

## swe · shift+EPIC+recompute@JSD · block e4r0 · django__django-11951

- trial `django__django-11951__9ZX7cPm` (job `e4r0b13__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 379.4 s
- table row (all 90 runs of this arm): runs 90, calls 5263, computed tok / call 1,301, / run 76,058, LLM 269 s, tool 37 s, job 306 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1732 | 1732 | C_charged | 200 | 17870 | 4348 |  |  |  |
| 1 | 3053 | 1122 | C_charged | 245 | 20519 | 5205 |  |  |  |
| 2 | 3357 | 60 | C_charged | 198 | 18758 | 4225 |  |  |  |
| 3 | 3576 | 22 | C_charged | 127 | 14632 | 2823 |  |  |  |
| 4 | 3730 | 28 | C_charged | 77 | 7204 | 1837 |  |  |  |
| 5 | 4071 | 265 | C_charged | 115 | 9529 | 2599 |  |  |  |
| 6 | 4207 | 22 | C_charged | 78 | 4187 | 1857 |  |  |  |
| 7 | 4351 | 67 | C_charged | 49 | 4564 | 1287 |  |  |  |
| 8 | 4514 | 115 | C_charged | 76 | 4792 | 1822 |  |  |  |
| 9 | 5178 | 589 | C_charged | 103 | 9054 | 2378 |  |  |  |
| 10 | 5381 | 101 | C_charged | 74 | 4937 | 1782 |  |  |  |
| 11 | 5588 | 134 | C_charged | 113 | 6215 | 2553 |  |  |  |
| 12 | 6046 | 346 | C_charged | 105 | 5330 | 2405 |  |  |  |
| 13 | 7091 | 941 | C_charged | 74 | 6940 | 1824 |  |  |  |
| 14 | 7609 | 445 | C_charged | 368 | 20547 | 7598 |  |  |  |
| 15 | 8020 | 44 | C_charged | 78 | 8007 | 1858 |  |  |  |
| 16 | 8454 | 357 | C_charged | 77 | 5703 | 1854 |  |  |  |
| 17 | 8560 | 30 | C_charged | 67 | 5611 | 1640 |  |  |  |
| 18 | 8769 | 143 | C_charged | 86 | 9176 | 2021 |  |  |  |
| 19 | 9520 | 666 | C_charged | 465 | 31996 | 9522 |  |  |  |
| 20 | 10012 | 28 | C_charged | 71 | 5556 | 1719 |  |  |  |
| 21 | 10721 | 639 | C_charged | 54 | 3542 | 1414 |  |  |  |
| 22 | 10537 | 228 | C_charged | 458 | 31379 | 9362 | yes |  | 0.0528, 0.559, False, 50, 178 |
| 23 | 11006 | 196 | C_charged | 65 | 6681 | 1609 | yes |  | 0.0178, 0.070, False, 34, 162 |
| 24 | 11389 | 1546 | C_charged | 583 | 37201 | 11893 | yes |  | 0.0470, 0.577, False, 709, 837 |
| 25 | 11658 | 218 | C_charged | 66 | 6069 | 1630 | yes |  | 0.0138, 0.187, False, 45, 173 |
| 26 | 11965 | 896 | C_charged | 107 | 10440 | 2472 | yes |  | 0.0104, 0.774, False, 384, 512 |
| 27 | 12140 | 566 | C_charged | 105 | 8261 | 2416 | yes |  | 0.2307, 0.284, False, 219, 347 |
| 28 | 13179 | 2336 | C_charged | 84 | 8189 | 2090 | yes |  | 0.0124, 0.871, False, 1104, 1232 |
| 29 | 12784 | 534 | C_charged | 46 | 3618 | 1251 | yes |  | 0.0308, 0.202, False, 203, 331 |
| 30 | 12689 | 288 | C_charged | 57 | 4731 | 1456 | yes |  | 0.0134, 0.774, False, 80, 208 |
| 31 | 14172 | 3408 | C_charged | 99 | 6871 | 2439 | yes |  | 0.0151, 0.180, False, 1640, 1768 |
| 32 | 13834 | 206 | C_charged | 264 | 16670 | 5534 | yes |  | 0.0144, 0.205, False, 39, 167 |

Run totals: calls 33; charged 18,318 of 268,893 prompt tokens (6.8%); model LLM time 106.7 s; tool+harness max(0, 379.4 − 364.8) = 14.6 s; model job time 121.3 s; post-snip calls 11.

## swepro · baseline (full recomputation) · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__ZxHhpub` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 217.1 s
- table row (all 90 runs of this arm): runs 90, calls 2716, computed tok / call 6,734, / run 203,215, LLM 149 s, tool 183 s, job 333 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | ideal_C | 51 | 3060 | 918 |  |  |  |
| 1 | 4725 | 2741 | ideal_C | 72 | 3628 | 1308 |  |  |  |
| 2 | 5555 | 771 | ideal_C | 161 | 7874 | 2808 |  |  |  |
| 3 | 7054 | 1342 | ideal_C | 142 | 6855 | 2495 |  |  |  |
| 4 | 9738 | 2554 | ideal_C | 282 | 12433 | 4964 |  |  |  |
| 5 | 11411 | 1395 | ideal_C | 110 | 6423 | 1952 |  |  |  |
| 6 | 11798 | 278 | ideal_C | 47 | 5448 | 824 |  |  |  |
| 7 | 13828 | 1988 | ideal_C | 302 | 13400 | 5307 |  |  |  |
| 8 | 14151 | 23 | ideal_C | 335 | 12463 | 5806 |  |  |  |
| 9 | 14512 | 32 | ideal_C | 582 | 24458 | 10087 |  |  |  |
| 10 | 15130 | 42 | ideal_C | 320 | 15549 | 5547 |  |  |  |
| 11 | 15486 | 46 | ideal_C | 776 | 36469 | 13449 |  |  |  |
| 12 | 16296 | 40 | ideal_C | 114 | 9233 | 1977 |  |  |  |
| 13 | 19182 | 2782 | ideal_C | 53 | 6304 | 1045 |  |  |  |
| 14 | 21665 | 2433 | ideal_C | 169 | 13466 | 3049 |  |  |  |
| 15 | 21855 | 31 | ideal_C | 702 | 30676 | 12167 |  |  |  |

Run totals: calls 16; charged 18,438 of 204,326 prompt tokens (9.0%); model LLM time 73.7 s; tool+harness max(0, 217.1 − 207.7) = 9.4 s; model job time 83.1 s; post-snip calls 0.

## swepro · baseline (full recomputation) · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__uhx6oEB` (job `e4r0b08__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 787.3 s
- table row (all 90 runs of this arm): runs 90, calls 2716, computed tok / call 6,734, / run 203,215, LLM 149 s, tool 183 s, job 333 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | ideal_C | 189 | 8855 | 3318 |  |  |  |
| 1 | 6744 | 4232 | ideal_C | 111 | 6084 | 2031 |  |  |  |
| 2 | 16047 | 9199 | ideal_C | 379 | 20111 | 6939 |  |  |  |
| 3 | 16510 | 94 | ideal_C | 49 | 9874 | 853 |  |  |  |
| 4 | 20454 | 3910 | ideal_C | 85 | 10992 | 1659 |  |  |  |
| 5 | 23343 | 2815 | ideal_C | 44 | 13451 | 909 |  |  |  |
| 6 | 23702 | 326 | ideal_C | 47 | 8700 | 832 |  |  |  |
| 7 | 33062 | 9318 | ideal_C | 60 | 12412 | 1672 |  |  |  |
| 8 | 33157 | 37 | ideal_C | 38 | 13149 | 661 |  |  |  |
| 9 | 33581 | 397 | ideal_C | 51 | 13821 | 911 |  |  |  |
| 10 | 35285 | 1669 | ideal_C | 707 | 34800 | 12371 |  |  |  |
| 11 | 36013 | 29 | ideal_C | 334 | 26863 | 5790 |  |  |  |
| 12 | 36372 | 36 | ideal_C | 459 | 39273 | 7957 |  |  |  |
| 13 | 36866 | 50 | ideal_C | 383 | 36474 | 6641 |  |  |  |
| 14 | 37283 | 35 | ideal_C | 609 | 44636 | 10556 |  |  |  |
| 15 | 37912 | 248 | ideal_C | 79 | 14009 | 1388 | yes |  |  |
| 16 | 42669 | 4685 | ideal_C | 284 | 22918 | 5312 |  |  |  |
| 17 | 42994 | 50 | ideal_C | 182 | 15396 | 3158 |  |  |  |
| 18 | 43827 | 659 | ideal_C | 123 | 14903 | 2188 |  |  |  |
| 19 | 44303 | 367 | ideal_C | 65 | 13828 | 1158 |  |  |  |
| 20 | 44611 | 259 | ideal_C | 66 | 12743 | 1166 |  |  |  |
| 21 | 44837 | 165 | ideal_C | 86 | 11436 | 1505 |  |  |  |
| 22 | 36056 | 29320 | ideal_C | 99 | 11012 | 3847 | yes |  |  |
| 23 | 35721 | 28985 | ideal_C | 148 | 14734 | 4656 | yes |  |  |
| 24 | 31964 | 25228 | ideal_C | 579 | 46382 | 11701 | yes |  |  |
| 25 | 29850 | 23098 | ideal_C | 935 | 58713 | 17650 | yes |  |  |
| 26 | 30465 | 23729 | ideal_C | 630 | 43209 | 12428 | yes |  |  |
| 27 | 22029 | 15293 | ideal_C | 1253 | 63094 | 22479 | yes |  |  |
| 28 | 23228 | 16476 | ideal_C | 278 | 10677 | 5674 | yes |  |  |
| 29 | 23185 | 16449 | ideal_C | 681 | 31136 | 12655 | yes |  |  |
| 30 | 22187 | 15451 | ideal_C | 337 | 12955 | 6617 | yes |  |  |
| 31 | 21915 | 15163 | ideal_C | 78 | 4066 | 2108 | yes |  |  |
| 32 | 21673 | 14921 | ideal_C | 170 | 10969 | 3684 | yes |  |  |
| 33 | 21860 | 15124 | ideal_C | 401 | 31045 | 7702 | yes |  |  |
| 34 | 21653 | 14917 | ideal_C | 402 | 22018 | 7704 | yes |  |  |

Run totals: calls 35; charged 295,071 of 1,033,695 prompt tokens (28.5%); model LLM time 197.9 s; tool+harness max(0, 787.3 − 764.7) = 22.6 s; model job time 220.5 s; post-snip calls 14.

## swepro · prefix_aa · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__RM4HWkt` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 308.0 s
- table row (all 90 runs of this arm): runs 90, calls 2890, computed tok / call 6,692, / run 214,888, LLM 154 s, tool 189 s, job 343 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | ideal_C | 51 | 2953 | 918 |  |  |  |
| 1 | 4725 | 2741 | ideal_C | 50 | 2695 | 927 |  |  |  |
| 2 | 6103 | 1335 | ideal_C | 149 | 7362 | 2615 |  |  |  |
| 3 | 8794 | 2554 | ideal_C | 261 | 12626 | 4596 |  |  |  |
| 4 | 9214 | 174 | ideal_C | 107 | 4798 | 1859 |  |  |  |
| 5 | 10648 | 1336 | ideal_C | 57 | 4528 | 1030 |  |  |  |
| 6 | 12688 | 1984 | ideal_C | 472 | 23666 | 8249 |  |  |  |
| 7 | 13181 | 29 | ideal_C | 177 | 6989 | 3068 |  |  |  |
| 8 | 13384 | 40 | ideal_C | 392 | 13262 | 6794 |  |  |  |
| 9 | 13812 | 52 | ideal_C | 603 | 29360 | 10451 |  |  |  |
| 10 | 14451 | 51 | ideal_C | 231 | 12074 | 4005 |  |  |  |
| 11 | 17452 | 2780 | ideal_C | 90 | 11352 | 1678 |  |  |  |
| 12 | 17817 | 281 | ideal_C | 68 | 9652 | 1191 |  |  |  |
| 13 | 20044 | 2172 | ideal_C | 66 | 9201 | 1245 |  |  |  |
| 14 | 20256 | 160 | ideal_C | 112 | 9032 | 1948 |  |  |  |
| 15 | 20397 | 45 | ideal_C | 65 | 6356 | 1129 |  |  |  |
| 16 | 20496 | 48 | ideal_C | 49 | 5916 | 851 |  |  |  |
| 17 | 20630 | 86 | ideal_C | 63 | 5593 | 1096 |  |  |  |
| 18 | 21145 | 457 | ideal_C | 109 | 10767 | 1911 |  |  |  |
| 19 | 21659 | 411 | ideal_C | 380 | 19902 | 6605 |  |  |  |
| 20 | 22063 | 31 | ideal_C | 62 | 8294 | 1076 |  |  |  |
| 21 | 22577 | 465 | ideal_C | 95 | 10065 | 1670 |  |  |  |
| 22 | 21783 | 17063 | ideal_C | 155 | 14766 | 3533 | yes |  |  |
| 23 | 19268 | 14548 | ideal_C | 447 | 31632 | 8409 | yes |  |  |

Run totals: calls 24; charged 50,783 of 374,527 prompt tokens (13.6%); model LLM time 76.9 s; tool+harness max(0, 308.0 − 272.8) = 35.1 s; model job time 112.0 s; post-snip calls 2.

## swepro · prefix_aa · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__4BHf3EX` (job `e4r0b08__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 528.2 s
- table row (all 90 runs of this arm): runs 90, calls 2890, computed tok / call 6,692, / run 214,888, LLM 154 s, tool 189 s, job 343 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | ideal_C | 209 | 8886 | 3664 |  |  |  |
| 1 | 6764 | 4220 | ideal_C | 166 | 7859 | 2984 |  |  |  |
| 2 | 17221 | 10293 | ideal_C | 609 | 29935 | 10989 |  |  |  |
| 3 | 17877 | 53 | ideal_C | 58 | 10730 | 1007 |  |  |  |
| 4 | 19341 | 1421 | ideal_C | 81 | 9827 | 1469 |  |  |  |
| 5 | 25196 | 5788 | ideal_C | 625 | 37482 | 11150 |  |  |  |
| 6 | 25842 | 34 | ideal_C | 198 | 20118 | 3433 |  |  |  |
| 7 | 26065 | 33 | ideal_C | 200 | 19772 | 3468 |  |  |  |
| 8 | 26286 | 30 | ideal_C | 590 | 36548 | 10226 |  |  |  |
| 9 | 26901 | 37 | ideal_C | 178 | 24410 | 3087 |  |  |  |
| 10 | 27100 | 28 | ideal_C | 156 | 18548 | 2705 |  |  |  |
| 11 | 27280 | 32 | ideal_C | 423 | 40346 | 7332 |  |  |  |
| 12 | 27724 | 28 | ideal_C | 162 | 18256 | 2809 |  |  |  |
| 13 | 28084 | 212 | ideal_C | 74 | 15029 | 1295 |  |  |  |
| 14 | 29976 | 1832 | ideal_C | 121 | 17187 | 2212 |  |  |  |
| 15 | 30276 | 180 | ideal_C | 140 | 19271 | 2437 |  |  |  |
| 16 | 31353 | 953 | ideal_C | 486 | 34915 | 8484 |  |  |  |
| 17 | 32615 | 791 | ideal_C | 70 | 8173 | 1266 |  |  |  |
| 18 | 32774 | 102 | ideal_C | 162 | 14026 | 2814 |  |  |  |
| 19 | 33571 | 643 | ideal_C | 552 | 37565 | 9610 |  |  |  |
| 20 | 34153 | 41 | ideal_C | 88 | 13108 | 1528 |  |  |  |
| 21 | 34323 | 83 | ideal_C | 139 | 11050 | 2415 |  |  |  |
| 22 | 24069 | 17317 | ideal_C | 46 | 12909 | 1721 | yes |  |  |
| 23 | 23774 | 17006 | ideal_C | 76 | 5729 | 2216 | yes |  |  |
| 24 | 22421 | 15653 | ideal_C | 670 | 45207 | 12404 | yes |  |  |

Run totals: calls 25; charged 79,147 of 633,323 prompt tokens (12.5%); model LLM time 112.7 s; tool+harness max(0, 528.2 − 516.9) = 11.3 s; model job time 124.0 s; post-snip calls 3.

## swepro · shift · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__XpbzQHr` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 333.4 s
- table row (all 90 runs of this arm): runs 90, calls 2831, computed tok / call 910, / run 28,635, LLM 138 s, tool 153 s, job 291 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | C | 48 | 2654 | 866 |  |  |  |
| 1 | 4722 | 2735 | C | 86 | 4611 | 1551 |  |  |  |
| 2 | 5821 | 1014 | C | 223 | 10827 | 3889 |  |  |  |
| 3 | 10568 | 4525 | C | 496 | 23962 | 8738 |  |  |  |
| 4 | 11491 | 428 | C | 333 | 14312 | 5785 |  |  |  |
| 5 | 11845 | 22 | C | 376 | 14418 | 6516 |  |  |  |
| 6 | 12247 | 27 | C | 572 | 25302 | 9913 |  |  |  |
| 7 | 12855 | 37 | C | 297 | 14485 | 5148 |  |  |  |
| 8 | 13188 | 37 | C | 141 | 7231 | 2445 |  |  |  |
| 9 | 13350 | 22 | C | 1033 | 61059 | 17902 |  |  |  |
| 10 | 14407 | 25 | C | 70 | 7903 | 1214 |  |  |  |
| 11 | 14750 | 274 | C | 72 | 9672 | 1258 |  |  |  |
| 12 | 16997 | 2176 | C | 72 | 8142 | 1339 |  |  |  |
| 13 | 17154 | 86 | C | 110 | 11585 | 1910 |  |  |  |
| 14 | 17298 | 35 | C | 214 | 13501 | 3710 |  |  |  |
| 15 | 17575 | 64 | C | 60 | 8338 | 1042 |  |  |  |
| 16 | 17656 | 22 | C | 121 | 13381 | 2098 |  |  |  |
| 17 | 20563 | 2787 | C | 43 | 11405 | 878 |  |  |  |
| 18 | 22972 | 2367 | C | 136 | 13296 | 2479 |  |  |  |
| 19 | 23129 | 22 | C | 769 | 37217 | 13327 |  |  |  |

Run totals: calls 20; charged 18,645 of 280,528 prompt tokens (6.6%); model LLM time 92.0 s; tool+harness max(0, 333.4 − 313.3) = 20.1 s; model job time 112.1 s; post-snip calls 0.

## swepro · shift · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__mWVphwc` (job `e4r0b08__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 941.1 s
- table row (all 90 runs of this arm): runs 90, calls 2831, computed tok / call 910, / run 28,635, LLM 138 s, tool 153 s, job 291 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | C | 189 | 7926 | 3318 |  |  |  |
| 1 | 6728 | 4203 | C | 100 | 4378 | 1839 |  |  |  |
| 2 | 17119 | 10292 | C | 411 | 21372 | 7556 |  |  |  |
| 3 | 17577 | 48 | C | 37 | 7138 | 643 |  |  |  |
| 4 | 17635 | 22 | C | 58 | 8696 | 1006 |  |  |  |
| 5 | 17955 | 263 | C | 57 | 7579 | 999 |  |  |  |
| 6 | 21907 | 3896 | C | 268 | 18664 | 4838 |  |  |  |
| 7 | 31488 | 9314 | C | 62 | 10696 | 1683 |  |  |  |
| 8 | 33219 | 1670 | C | 624 | 39158 | 10927 |  |  |  |
| 9 | 33864 | 22 | C | 521 | 27007 | 9030 |  |  |  |
| 10 | 34410 | 26 | C | 1065 | 58873 | 18457 |  |  |  |
| 11 | 35510 | 36 | C | 537 | 45552 | 9308 |  |  |  |
| 12 | 36081 | 35 | C | 1263 | 86720 | 21889 |  |  |  |
| 13 | 37365 | 25 | C | 44 | 12113 | 764 | yes |  |  |
| 14 | 38044 | 636 | C | 51 | 13151 | 932 |  |  |  |
| 15 | 44558 | 6464 | C | 600 | 38110 | 10956 |  |  |  |
| 16 | 45835 | 678 | C | 77 | 14341 | 1394 |  |  |  |
| 17 | 46212 | 301 | C | 55 | 10255 | 980 |  |  |  |
| 18 | 46306 | 40 | C | 67 | 10064 | 1165 |  |  |  |
| 19 | 46653 | 281 | C | 138 | 14430 | 2417 |  |  |  |
| 20 | 47191 | 401 | C | 58 | 8517 | 1041 |  |  |  |
| 21 | 47268 | 20 | C | 59 | 9128 | 1024 |  |  |  |
| 22 | 37974 | 1022 | C | 639 | 81460 | 11151 | yes | 0.0387 |  |
| 23 | 38920 | 330 | C | 2152 | 117717 | 37318 | yes |  |  |
| 24 | 40638 | 42 | C | 58 | 7018 | 1008 | yes | 0.0022 |  |
| 25 | 40956 | 640 | C | 269 | 11008 | 4713 | yes |  |  |
| 26 | 41457 | 266 | C | 97 | 6716 | 1703 | yes |  |  |
| 27 | 37808 | 224 | C | 235 | 16902 | 4089 | yes |  |  |
| 28 | 26786 | 57 | C | 47 | 10524 | 818 | yes | 0.0008 |  |
| 29 | 28477 | 1678 | C | 363 | 20787 | 6392 | yes |  |  |
| 30 | 28214 | 37 | C | 67 | 7771 | 1163 | yes | 0.0003 |  |
| 31 | 26670 | 53 | C | 59 | 4013 | 1025 | yes |  |  |
| 32 | 26776 | 636 | C | 69 | 3878 | 1232 | yes |  |  |
| 33 | 26442 | 888 | C | 124 | 7222 | 2200 | yes |  |  |
| 34 | 25931 | 51 | C | 54 | 4920 | 939 | yes |  |  |
| 35 | 19499 | 35 | C | 76 | 3242 | 1319 | yes |  |  |
| 36 | 18447 | 167 | C | 41 | 2103 | 718 | yes |  |  |
| 37 | 18146 | 53 | C | 93 | 3502 | 1614 | yes |  |  |
| 38 | 18388 | 250 | C | 420 | 17059 | 7289 | yes |  |  |
| 39 | 18862 | 419 | C | 264 | 15079 | 4594 | yes |  |  |
| 40 | 19056 | 486 | C | 114 | 7145 | 1998 | yes |  |  |
| 41 | 19400 | 314 | C | 86 | 4051 | 1505 | yes |  |  |
| 42 | 18577 | 169 | C | 57 | 2452 | 995 | yes |  |  |
| 43 | 21263 | 3609 | C | 104 | 3788 | 1978 | yes |  |  |
| 44 | 24870 | 5686 | C | 290 | 10392 | 5336 | yes |  |  |
| 45 | 24727 | 265 | C | 270 | 11531 | 4693 | yes |  |  |
| 46 | 24501 | 39 | C | 392 | 14227 | 6795 | yes |  |  |

Run totals: calls 47; charged 58,426 of 1,378,047 prompt tokens (4.2%); model LLM time 224.8 s; tool+harness max(0, 941.1 − 868.4) = 72.7 s; model job time 297.5 s; post-snip calls 26.

## swepro · shift+EPIC · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__NpoRRrm` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 313.1 s
- table row (all 90 runs of this arm): runs 90, calls 2884, computed tok / call 953, / run 30,523, LLM 136 s, tool 159 s, job 295 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | C | 51 | 1693 | 918 |  |  |  |
| 1 | 4725 | 2735 | C | 72 | 3244 | 1308 |  |  |  |
| 2 | 5555 | 759 | C | 72 | 3633 | 1265 |  |  |  |
| 3 | 6955 | 1329 | C | 122 | 6377 | 2148 |  |  |  |
| 4 | 9619 | 2543 | C | 360 | 17437 | 6315 |  |  |  |
| 5 | 10266 | 288 | C | 56 | 2443 | 979 |  |  |  |
| 6 | 12305 | 1984 | C | 172 | 7588 | 3049 |  |  |  |
| 7 | 13797 | 1321 | C | 251 | 14252 | 4398 |  |  |  |
| 8 | 14069 | 22 | C | 226 | 9493 | 3917 |  |  |  |
| 9 | 14321 | 27 | C | 158 | 5976 | 2739 |  |  |  |
| 10 | 14500 | 22 | C | 277 | 10148 | 4801 |  |  |  |
| 11 | 14803 | 27 | C | 170 | 5336 | 2947 |  |  |  |
| 12 | 14994 | 22 | C | 483 | 24061 | 8371 |  |  |  |
| 13 | 15503 | 27 | C | 152 | 7148 | 2635 |  |  |  |
| 14 | 15676 | 22 | C | 1024 | 49937 | 17746 |  |  |  |
| 15 | 16724 | 25 | C | 136 | 11919 | 2358 |  |  |  |
| 16 | 16881 | 22 | C | 54 | 6230 | 937 |  |  |  |
| 17 | 17127 | 193 | C | 67 | 7063 | 1169 |  |  |  |
| 18 | 17467 | 274 | C | 56 | 6811 | 982 |  |  |  |
| 19 | 17669 | 147 | C | 61 | 8821 | 1063 |  |  |  |
| 20 | 19889 | 2160 | C | 65 | 8673 | 1227 |  |  |  |
| 21 | 20039 | 86 | C | 108 | 11260 | 1876 |  |  |  |
| 22 | 20187 | 41 | C | 130 | 9419 | 2255 |  |  |  |
| 23 | 22284 | 2924 | C | 57 | 7808 | 1135 | yes |  |  |
| 24 | 20965 | 159 | C | 140 | 13689 | 2434 | yes |  |  |
| 25 | 18462 | 167 | C | 412 | 33049 | 7147 | yes |  |  |

Run totals: calls 26; charged 19,266 of 376,722 prompt tokens (5.1%); model LLM time 86.1 s; tool+harness max(0, 313.1 − 293.5) = 19.6 s; model job time 105.7 s; post-snip calls 3.

## swepro · shift+EPIC · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__5HLNcBZ` (job `e4r0b08__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 547.2 s
- table row (all 90 runs of this arm): runs 90, calls 2884, computed tok / call 953, / run 30,523, LLM 136 s, tool 159 s, job 295 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | C | 189 | 8481 | 3318 |  |  |  |
| 1 | 6744 | 4219 | C | 157 | 9211 | 2828 |  |  |  |
| 2 | 18388 | 11488 | C | 102 | 7800 | 2275 |  |  |  |
| 3 | 18831 | 342 | C | 75 | 11968 | 1315 |  |  |  |
| 4 | 28243 | 9338 | C | 61 | 9469 | 1618 |  |  |  |
| 5 | 28690 | 387 | C | 50 | 6467 | 890 |  |  |  |
| 6 | 30393 | 1654 | C | 365 | 24316 | 6430 |  |  |  |
| 7 | 30779 | 22 | C | 570 | 33216 | 9879 |  |  |  |
| 8 | 31374 | 26 | C | 352 | 24598 | 6102 |  |  |  |
| 9 | 31747 | 22 | C | 368 | 25753 | 6379 |  |  |  |
| 10 | 32140 | 26 | C | 242 | 22244 | 4195 |  |  |  |
| 11 | 32403 | 22 | C | 225 | 28177 | 3901 |  |  |  |
| 12 | 32652 | 25 | C | 145 | 28138 | 2514 |  |  |  |
| 13 | 32818 | 22 | C | 50 | 12573 | 868 |  |  |  |
| 14 | 32915 | 48 | C | 56 | 14345 | 974 |  |  |  |
| 15 | 33018 | 48 | C | 57 | 9930 | 991 |  |  |  |
| 16 | 36986 | 3912 | C | 394 | 31344 | 7118 |  |  |  |
| 17 | 38115 | 736 | C | 76 | 14927 | 1373 |  |  |  |
| 18 | 38427 | 237 | C | 51 | 9954 | 902 |  |  |  |
| 19 | 38513 | 36 | C | 67 | 17125 | 1164 |  |  |  |
| 20 | 38760 | 181 | C | 164 | 19784 | 2856 |  |  |  |
| 21 | 38945 | 22 | C | 57 | 8159 | 989 |  |  |  |
| 22 | 39402 | 401 | C | 357 | 30115 | 6218 |  |  |  |
| 23 | 28308 | 305 | C | 103 | 38353 | 1803 | yes | 0.0260 |  |
| 24 | 28250 | 444 | C | 144 | 11442 | 2522 | yes |  |  |
| 25 | 19019 | 167 | C | 630 | 69484 | 10925 | yes | 0.0004 |  |

Run totals: calls 26; charged 36,467 of 768,197 prompt tokens (4.7%); model LLM time 90.3 s; tool+harness max(0, 547.2 − 527.4) = 19.9 s; model job time 110.2 s; post-snip calls 3.

## swepro · shift+EPICa1 · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__43JKC9W` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 300.9 s
- table row (all 90 runs of this arm): runs 90, calls 3092, computed tok / call 4,174, / run 143,395, LLM 155 s, tool 142 s, job 297 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | C | 51 | 1856 | 918 |  |  |  |
| 1 | 4725 | 2735 | C | 47 | 2068 | 875 |  |  |  |
| 2 | 6100 | 1329 | C | 152 | 8225 | 2666 |  |  |  |
| 3 | 8794 | 2543 | C | 236 | 11817 | 4163 |  |  |  |
| 4 | 10145 | 1116 | C | 66 | 3042 | 1178 |  |  |  |
| 5 | 10504 | 294 | C | 48 | 2388 | 841 |  |  |  |
| 6 | 12535 | 1984 | C | 1129 | 48787 | 19633 |  |  |  |
| 7 | 13700 | 37 | C | 346 | 11972 | 5997 |  |  |  |
| 8 | 14082 | 37 | C | 544 | 27946 | 9428 |  |  |  |
| 9 | 14662 | 37 | C | 122 | 7376 | 2116 |  |  |  |
| 10 | 17558 | 2775 | C | 870 | 46576 | 15195 |  |  |  |
| 11 | 18462 | 35 | C | 60 | 8910 | 1041 |  |  |  |
| 12 | 20611 | 2090 | C | 56 | 5748 | 1070 |  |  |  |
| 13 | 22842 | 2176 | C | 74 | 10065 | 1394 |  |  |  |
| 14 | 23001 | 86 | C | 188 | 9276 | 3262 |  |  |  |
| 15 | 23472 | 284 | C | 73 | 8342 | 1280 |  |  |  |
| 16 | 23563 | 19 | C | 58 | 8992 | 1006 |  |  |  |
| 17 | 24089 | 469 | C | 102 | 9379 | 1793 |  |  |  |
| 18 | 24643 | 453 | C | 168 | 9947 | 2936 |  |  |  |
| 19 | 24832 | 22 | C | 463 | 26655 | 8025 |  |  |  |

Run totals: calls 20; charged 20,461 of 320,260 prompt tokens (6.4%); model LLM time 84.8 s; tool+harness max(0, 300.9 − 269.4) = 31.5 s; model job time 116.3 s; post-snip calls 0.

## swepro · shift+EPICa1 · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__msefSog` (job `e4r0b08__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 602.8 s
- table row (all 90 runs of this arm): runs 90, calls 3092, computed tok / call 4,174, / run 143,395, LLM 155 s, tool 142 s, job 297 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | C | 175 | 8015 | 3075 |  |  |  |
| 1 | 8822 | 6311 | C | 67 | 5289 | 1342 |  |  |  |
| 2 | 10232 | 1344 | C | 85 | 5368 | 1515 |  |  |  |
| 3 | 19493 | 9177 | C | 429 | 23849 | 7856 |  |  |  |
| 4 | 21836 | 1915 | C | 102 | 12521 | 1863 |  |  |  |
| 5 | 25842 | 3905 | C | 140 | 12487 | 2646 |  |  |  |
| 6 | 26208 | 227 | C | 44 | 10742 | 775 |  |  |  |
| 7 | 35565 | 9314 | C | 80 | 11970 | 2056 |  |  |  |
| 8 | 35686 | 42 | C | 70 | 12491 | 1216 |  |  |  |
| 9 | 36303 | 548 | C | 54 | 14394 | 976 |  |  |  |
| 10 | 38010 | 1654 | C | 1805 | 77038 | 31404 |  |  |  |
| 11 | 39836 | 22 | C | 408 | 41269 | 7072 |  |  |  |
| 12 | 40269 | 26 | C | 301 | 32278 | 5218 |  |  |  |
| 13 | 40591 | 22 | C | 365 | 28903 | 6327 |  |  |  |
| 14 | 40981 | 26 | C | 231 | 22065 | 4005 |  |  |  |
| 15 | 41233 | 22 | C | 356 | 26126 | 6171 |  |  |  |
| 16 | 41613 | 25 | C | 586 | 39269 | 10157 |  |  |  |
| 17 | 42220 | 22 | C | 145 | 11344 | 2515 |  |  |  |
| 18 | 42654 | 290 | C | 62 | 11781 | 1099 |  |  |  |
| 19 | 43048 | 333 | C | 391 | 34211 | 6804 |  |  |  |
| 20 | 43972 | 534 | C | 97 | 14122 | 1727 |  |  |  |
| 21 | 44393 | 325 | C | 152 | 12839 | 2662 |  |  |  |
| 22 | 44566 | 22 | C | 68 | 6576 | 1180 |  |  |  |
| 23 | 43644 | 547 | C | 206 | 16312 | 3616 | yes |  |  |
| 24 | 34608 | 25171 | C | 109 | 23763 | 3660 | yes | 0.0046 |  |
| 25 | 33223 | 1600 | C | 88 | 7944 | 1634 | yes |  |  |
| 26 | 29344 | 18981 | C | 66 | 4214 | 2318 | yes |  |  |
| 27 | 29243 | 1860 | C | 155 | 8361 | 2801 | yes |  |  |
| 28 | 29403 | 18702 | C | 739 | 47642 | 13965 | yes |  |  |

Run totals: calls 29; charged 105,304 of 965,175 prompt tokens (10.9%); model LLM time 137.7 s; tool+harness max(0, 602.8 − 583.2) = 19.7 s; model job time 157.3 s; post-snip calls 6.

## swepro · shift+EPICa4 · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__f3wsDha` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 359.7 s
- table row (all 90 runs of this arm): runs 90, calls 3173, computed tok / call 1,893, / run 66,747, LLM 156 s, tool 162 s, job 318 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | C | 51 | 2602 | 918 |  |  |  |
| 1 | 4725 | 2735 | C | 50 | 3031 | 927 |  |  |  |
| 2 | 4811 | 37 | C | 67 | 3506 | 1162 |  |  |  |
| 3 | 7271 | 2394 | C | 59 | 3291 | 1085 |  |  |  |
| 4 | 8658 | 1329 | C | 232 | 11177 | 4058 |  |  |  |
| 5 | 11442 | 2553 | C | 573 | 27965 | 10014 |  |  |  |
| 6 | 13335 | 1321 | C | 128 | 6140 | 2266 |  |  |  |
| 7 | 13693 | 231 | C | 51 | 2305 | 892 |  |  |  |
| 8 | 15727 | 1984 | C | 207 | 7741 | 3666 |  |  |  |
| 9 | 16231 | 298 | C | 252 | 9363 | 4379 |  |  |  |
| 10 | 16504 | 22 | C | 191 | 6158 | 3311 |  |  |  |
| 11 | 16721 | 27 | C | 170 | 6772 | 2947 |  |  |  |
| 12 | 16912 | 22 | C | 275 | 15583 | 4766 |  |  |  |
| 13 | 17213 | 27 | C | 261 | 12899 | 4524 |  |  |  |
| 14 | 17495 | 22 | C | 534 | 26631 | 9255 |  |  |  |
| 15 | 18055 | 27 | C | 159 | 15211 | 2757 |  |  |  |
| 16 | 18235 | 22 | C | 116 | 10485 | 2011 |  |  |  |
| 17 | 20520 | 2170 | C | 62 | 8028 | 1178 |  |  |  |
| 18 | 20855 | 274 | C | 56 | 7903 | 984 |  |  |  |
| 19 | 23070 | 2160 | C | 77 | 6379 | 1446 |  |  |  |
| 20 | 23248 | 102 | C | 105 | 10084 | 1825 |  |  |  |
| 21 | 23391 | 39 | C | 65 | 6829 | 1128 |  |  |  |
| 22 | 23419 | 177 | C | 57 | 7668 | 997 | yes |  |  |
| 23 | 21054 | 184 | C | 58 | 9906 | 1014 | yes |  |  |
| 24 | 20177 | 598 | C | 97 | 10674 | 1709 | yes |  |  |
| 25 | 17989 | 618 | C | 385 | 21211 | 6699 | yes |  |  |
| 26 | 16521 | 10199 | C | 54 | 11248 | 1355 | yes |  |  |
| 27 | 16947 | 619 | C | 93 | 10501 | 1638 | yes |  |  |
| 28 | 17134 | 694 | C | 149 | 16112 | 2611 | yes |  |  |
| 29 | 15270 | 705 | C | 353 | 27222 | 6145 | yes |  |  |

Run totals: calls 30; charged 33,530 of 478,563 prompt tokens (7.0%); model LLM time 87.7 s; tool+harness max(0, 359.7 − 324.6) = 35.1 s; model job time 122.8 s; post-snip calls 8.

## swepro · shift+EPICa4 · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__jxZaQK8` (job `e4r0b08__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 901.8 s
- table row (all 90 runs of this arm): runs 90, calls 3173, computed tok / call 1,893, / run 66,747, LLM 156 s, tool 162 s, job 318 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | C | 164 | 7626 | 2885 |  |  |  |
| 1 | 6719 | 4219 | C | 127 | 7455 | 2308 |  |  |  |
| 2 | 16038 | 9193 | C | 283 | 18038 | 5275 |  |  |  |
| 3 | 18712 | 2392 | C | 75 | 9564 | 1407 |  |  |  |
| 4 | 23002 | 4216 | C | 73 | 8544 | 1483 |  |  |  |
| 5 | 32388 | 9314 | C | 475 | 29809 | 8853 |  |  |  |
| 6 | 33249 | 387 | C | 59 | 10869 | 1049 |  |  |  |
| 7 | 34977 | 1670 | C | 596 | 36206 | 10447 |  |  |  |
| 8 | 35594 | 22 | C | 392 | 21559 | 6795 |  |  |  |
| 9 | 36011 | 26 | C | 370 | 24725 | 6414 |  |  |  |
| 10 | 36406 | 26 | C | 151 | 26612 | 2619 |  |  |  |
| 11 | 36578 | 22 | C | 238 | 20660 | 4126 |  |  |  |
| 12 | 36840 | 25 | C | 153 | 20411 | 2653 |  |  |  |
| 13 | 37014 | 22 | C | 108 | 15363 | 1873 |  |  |  |
| 14 | 40525 | 3404 | C | 72 | 13097 | 1520 |  |  |  |
| 15 | 41014 | 418 | C | 66 | 10118 | 1177 |  |  |  |
| 16 | 41519 | 440 | C | 75 | 15830 | 1336 |  |  |  |
| 17 | 43847 | 2254 | C | 172 | 16509 | 3173 |  |  |  |
| 18 | 44040 | 22 | C | 98 | 12758 | 1700 |  |  |  |
| 19 | 44170 | 33 | C | 1422 | 85706 | 24645 |  |  |  |
| 20 | 45647 | 56 | C | 1062 | 76468 | 18408 |  |  |  |
| 21 | 46748 | 40 | C | 579 | 38854 | 10037 |  |  |  |
| 22 | 47355 | 29 | C | 722 | 40947 | 12514 |  |  |  |
| 23 | 38834 | 203 | C | 1246 | 106964 | 21608 | yes | 0.0122 |  |
| 24 | 37435 | 164 | C | 45 | 3272 | 792 | yes |  |  |
| 25 | 33209 | 165 | C | 64 | 8008 | 1120 | yes |  |  |
| 26 | 23942 | 201 | C | 1269 | 66608 | 22001 | yes | 0.0108 |  |
| 27 | 24379 | 14868 | C | 42 | 5155 | 1528 | yes |  |  |
| 28 | 22737 | 1491 | C | 1487 | 75236 | 25845 | yes |  |  |
| 29 | 23652 | 265 | C | 51 | 3793 | 898 | yes |  |  |
| 30 | 23450 | 382 | C | 123 | 6744 | 2151 | yes |  |  |
| 31 | 23559 | 2023 | C | 129 | 8563 | 2342 | yes |  |  |
| 32 | 23905 | 11809 | C | 77 | 5027 | 1961 | yes |  |  |
| 33 | 24005 | 3164 | C | 740 | 31549 | 12992 | yes |  |  |

Run totals: calls 34; charged 75,302 of 1,079,837 prompt tokens (7.0%); model LLM time 225.9 s; tool+harness max(0, 901.8 − 888.6) = 13.2 s; model job time 239.1 s; post-snip calls 11.

## swepro · shift+EPIC+recompute@JSD · block e4r0 · instance_NodeBB__NodeBB-397835a05a8e2897324e566b41c5e616e172b4af-v89631a1cdb318276acb48860c5d78077211397c6

- trial `instance_NodeBB__NodeBB-397835a0__iWdaFmv` (job `e4r0b17__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 263.9 s
- table row (all 90 runs of this arm): runs 90, calls 2785, computed tok / call 1,583, / run 48,997, LLM 138 s, tool 136 s, job 274 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1940 | 1940 | C_charged | 51 | 2754 | 918 |  |  |  |
| 1 | 4725 | 2735 | C_charged | 72 | 3887 | 1308 |  |  |  |
| 2 | 5555 | 759 | C_charged | 72 | 4046 | 1265 |  |  |  |
| 3 | 6955 | 1329 | C_charged | 148 | 7058 | 2599 |  |  |  |
| 4 | 9645 | 2543 | C_charged | 519 | 23449 | 9070 |  |  |  |
| 5 | 10606 | 443 | C_charged | 45 | 3212 | 794 |  |  |  |
| 6 | 12650 | 2000 | C_charged | 217 | 11911 | 3830 |  |  |  |
| 7 | 12888 | 22 | C_charged | 267 | 10528 | 4628 |  |  |  |
| 8 | 13181 | 27 | C_charged | 118 | 4492 | 2046 |  |  |  |
| 9 | 13320 | 22 | C_charged | 527 | 19099 | 9133 |  |  |  |
| 10 | 13873 | 27 | C_charged | 145 | 8854 | 2514 |  |  |  |
| 11 | 14039 | 22 | C_charged | 198 | 9715 | 3432 |  |  |  |
| 12 | 14263 | 27 | C_charged | 124 | 6433 | 2150 |  |  |  |
| 13 | 14408 | 22 | C_charged | 969 | 46725 | 16793 |  |  |  |
| 14 | 15401 | 25 | C_charged | 143 | 10824 | 2479 |  |  |  |
| 15 | 15565 | 22 | C_charged | 94 | 8679 | 1630 |  |  |  |
| 16 | 18376 | 2718 | C_charged | 46 | 8679 | 917 |  |  |  |
| 17 | 20732 | 2311 | C_charged | 64 | 5950 | 1220 |  |  |  |
| 18 | 21069 | 274 | C_charged | 152 | 11680 | 2647 |  |  |  |
| 19 | 21242 | 22 | C_charged | 685 | 37911 | 11871 |  |  |  |

Run totals: calls 20; charged 17,290 of 260,433 prompt tokens (6.6%); model LLM time 81.2 s; tool+harness max(0, 263.9 − 245.9) = 18.0 s; model job time 99.3 s; post-snip calls 0.

## swepro · shift+EPIC+recompute@JSD · block e4r0 · instance_NodeBB__NodeBB-445b70deda20201b7d9a68f7224da751b3db728c-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed

- trial `instance_NodeBB__NodeBB-445b70de__sMvuyJQ` (job `e4r0b08__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 359.1 s
- table row (all 90 runs of this arm): runs 90, calls 2785, computed tok / call 1,583, / run 48,997, LLM 138 s, tool 136 s, job 274 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2337 | 2337 | C_charged | 164 | 7893 | 2885 |  |  |  |
| 1 | 6719 | 4219 | C_charged | 136 | 7568 | 2464 |  |  |  |
| 2 | 17146 | 10292 | C_charged | 383 | 20381 | 7071 |  |  |  |
| 3 | 19482 | 1954 | C_charged | 81 | 10530 | 1493 |  |  |  |
| 4 | 28669 | 9107 | C_charged | 945 | 47380 | 16929 |  |  |  |
| 5 | 29635 | 22 | C_charged | 498 | 33009 | 8631 |  |  |  |
| 6 | 30158 | 26 | C_charged | 347 | 21851 | 6015 |  |  |  |
| 7 | 30530 | 26 | C_charged | 114 | 13988 | 1977 |  |  |  |
| 8 | 30665 | 22 | C_charged | 226 | 31443 | 3918 |  |  |  |
| 9 | 30915 | 25 | C_charged | 107 | 13156 | 1856 |  |  |  |
| 10 | 31043 | 22 | C_charged | 68 | 20806 | 1180 |  |  |  |
| 11 | 40418 | 9308 | C_charged | 74 | 15950 | 2025 |  |  |  |
| 12 | 40527 | 36 | C_charged | 81 | 17803 | 1407 |  |  |  |
| 13 | 40890 | 283 | C_charged | 61 | 9479 | 1080 |  |  |  |
| 14 | 41153 | 203 | C_charged | 74 | 10690 | 1299 |  |  |  |
| 15 | 41289 | 63 | C_charged | 60 | 15981 | 1045 |  |  |  |
| 16 | 41455 | 107 | C_charged | 790 | 52061 | 13699 |  |  |  |

Run totals: calls 17; charged 38,052 of 503,031 prompt tokens (7.6%); model LLM time 75.0 s; tool+harness max(0, 359.1 − 350.0) = 9.1 s; model job time 84.1 s; post-snip calls 0.

## tbench · baseline (full recomputation) · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__YV5F3Hw` (job `e4r0b28__c00`): reward 1.0 → success 1.0; s15 status timeout; agent wall 2700.0 s
- table row (all 90 runs of this arm): runs 90, calls 3494, computed tok / call 9,939, / run 385,845, LLM 484 s, tool 519 s, job 1003 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | ideal_C | 380 | 9785 | 7692 |  |  |  |
| 1 | 20763 | 18283 | ideal_C | 8596 | 357597 | 173990 |  |  |  |
| 2 | 29386 | 1370 | ideal_C | 64 | 9295 | 1295 | yes |  |  |
| 3 | 30013 | 573 | ideal_C | 328 | 25244 | 6639 |  |  |  |
| 4 | 30362 | 26 | ideal_C | 9806 | 369072 | 198481 |  |  |  |
| 5 | 40196 | 420 | ideal_C | 54 | 22416 | 1093 | yes |  |  |
| 6 | 41025 | 785 | ideal_C | 299 | 56735 | 6052 |  |  |  |
| 7 | 41345 | 33 | ideal_C | 1183 | 52187 | 23945 |  |  |  |
| 8 | 42551 | 39 | ideal_C | 292 | 37291 | 5910 |  |  |  |
| 9 | 42866 | 34 | ideal_C | 166 | 23432 | 3360 |  |  |  |
| 10 | 43071 | 47 | ideal_C | 88 | 43534 | 1781 |  |  |  |
| 11 | 43180 | 28 | ideal_C | 53 | 14769 | 1073 |  |  |  |
| 12 | 44008 | 776 | ideal_C | 333 | 35163 | 6740 |  |  |  |
| 13 | 44462 | 126 | ideal_C | 822 | 71555 | 16638 |  |  |  |
| 14 | 45323 | 43 | ideal_C | 110 | 45026 | 2226 |  |  |  |
| 15 | 45454 | 30 | ideal_C | 47 | 38701 | 951 |  |  |  |
| 16 | 46282 | 794 | ideal_C | 309 | 72300 | 6254 |  |  |  |
| 17 | 46720 | 144 | ideal_C | 212 | 32490 | 4291 |  |  |  |
| 18 | 46991 | 63 | ideal_C | 1088 | 51643 | 22022 |  |  |  |
| 19 | 48102 | 38 | ideal_C | 48 | 163690 | 972 |  |  |  |
| 20 | 48925 | 781 | ideal_C | 206 | 29251 | 4170 |  |  |  |
| 21 | 49202 | 82 | ideal_C | 422 | 61669 | 8542 |  |  |  |
| 22 | 49832 | 216 | ideal_C | 641 | 35535 | 12974 |  |  |  |
| 23 | 41901 | 21149 | ideal_C | 189 | 24503 | 3826 | yes |  |  |
| 24 | 41522 | 20770 | ideal_C | 188 | 24182 | 3805 | yes |  |  |
| 25 | 41407 | 20655 | ideal_C | 798 | 47184 | 16152 | yes |  |  |
| 26 | 32551 | 11783 | ideal_C | 272 | 28846 | 5505 | yes |  |  |
| 27 | 32390 | 11638 | ideal_C | 64 | 18180 | 1295 | yes |  |  |
| 28 | 31871 | 11119 | ideal_C | 198 | 22921 | 4008 | yes |  |  |
| 29 | 32336 | 11568 | ideal_C | 251 | 25479 | 5080 | yes |  |  |
| 30 | 32495 | 11743 | ideal_C | 573 | 37505 | 11598 | yes |  |  |
| 31 | 33096 | 12344 | ideal_C | 829 | 41105 | 16780 | yes |  |  |
| 32 | 33267 | 12499 | ideal_C | 276 | 27161 | 5586 | yes |  |  |
| 33 | 33158 | 12390 | ideal_C | 167 | 20156 | 3380 | yes |  |  |
| 34 | 32516 | 11764 | ideal_C | 203 | 21225 | 4109 | yes |  |  |
| 35 | 32792 | 12040 | ideal_C | 447 | 34340 | 9048 | yes |  |  |
| 36 | 32632 | 11864 | ideal_C | 382 | 35953 | 7732 | yes |  |  |
| 37 | 32702 | 11934 | ideal_C | 228 | 25217 | 4615 | yes |  |  |
| 38 | 32763 | 12011 | ideal_C | 94 | 22059 | 1903 | yes |  |  |
| 39 | 31781 | 11029 | ideal_C | 69 | 18495 | 1397 | yes |  |  |
| 40 | 31063 | 10295 | ideal_C | 642 | 39616 | 12995 | yes |  |  |
| 41 | 31947 | 11195 | ideal_C | 109 | 17338 | 2206 | yes |  |  |
| 42 | 32009 | 11257 | ideal_C | 82 | 12477 | 1660 | yes |  |  |
| 43 | 31474 | 10706 | ideal_C | 68 | 14031 | 1376 | yes |  |  |
| 44 | 31790 | 11038 | ideal_C | 369 | 29496 | 7469 | yes |  |  |
| 45 | 32074 | 11322 | ideal_C | 364 | 24970 | 7368 | yes |  |  |
| 46 | 31500 | 10732 | ideal_C | 109 | 20142 | 2206 | yes |  |  |
| 47 | 30815 | 10063 | ideal_C | 397 | 25857 | 8036 | yes |  |  |
| 48 | 31008 | 10256 | ideal_C | 422 | 27261 | 8542 | yes |  |  |
| 49 | 30813 | 10045 | ideal_C | 404 | 27544 | 8177 | yes |  |  |
| 50 | 31000 | 10248 | ideal_C | 407 | 25395 | 8238 | yes |  |  |
| 51 | 30847 | 10095 | ideal_C | 372 | 45552 | 7530 | yes |  |  |
| 52 | 30290 | 9522 | ideal_C | 204 | 24150 | 4129 | yes |  |  |
| 53 | 30538 | 9786 | ideal_C | 275 | 46694 | 5566 | yes |  |  |
| 54 | 30633 | 9881 | ideal_C | 76 | 18698 | 1538 | yes |  |  |
| 55 | 30666 | 9898 | ideal_C | 778 | 63894 | 15747 | yes |  |  |
| 56 | 30799 | 10047 | ideal_C | 323 | 30161 | 6538 | yes |  |  |

Run totals: calls 57; charged 431,520 of 2,032,608 prompt tokens (21.2%); model LLM time 732.2 s; tool+harness max(0, 2700.0 − 2626.2) = 73.9 s; model job time 806.1 s; post-snip calls 36.

## tbench · baseline (full recomputation) · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__dk7GL7L` (job `e4r0b02__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 794.3 s
- table row (all 90 runs of this arm): runs 90, calls 3494, computed tok / call 9,939, / run 385,845, LLM 484 s, tool 519 s, job 1003 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | ideal_C | 95 | 4495 | 1923 |  |  |  |
| 1 | 2725 | 981 | ideal_C | 7132 | 288851 | 144357 |  |  |  |
| 2 | 9903 | 2303 | ideal_C | 231 | 8908 | 4676 | yes |  |  |
| 3 | 10231 | 103 | ideal_C | 425 | 16549 | 8602 |  |  |  |
| 4 | 10741 | 101 | ideal_C | 335 | 13950 | 6781 |  |  |  |
| 5 | 11170 | 98 | ideal_C | 311 | 12930 | 6295 |  |  |  |
| 6 | 11599 | 127 | ideal_C | 475 | 19511 | 9614 |  |  |  |
| 7 | 12315 | 251 | ideal_C | 358 | 14359 | 7246 |  |  |  |
| 8 | 12813 | 141 | ideal_C | 381 | 16998 | 7712 |  |  |  |
| 9 | 13362 | 178 | ideal_C | 364 | 15644 | 7368 |  |  |  |
| 10 | 13872 | 160 | ideal_C | 562 | 22546 | 11375 |  |  |  |
| 11 | 14611 | 179 | ideal_C | 529 | 20053 | 10707 |  |  |  |
| 12 | 15271 | 135 | ideal_C | 431 | 18740 | 8724 |  |  |  |
| 13 | 15844 | 148 | ideal_C | 267 | 14651 | 5404 |  |  |  |
| 14 | 16155 | 59 | ideal_C | 200 | 9000 | 4048 |  |  |  |
| 15 | 16382 | 30 | ideal_C | 46 | 2399 | 931 |  |  |  |
| 16 | 16463 | 47 | ideal_C | 54 | 2657 | 1093 |  |  |  |
| 17 | 16539 | 27 | ideal_C | 67 | 3592 | 1356 |  |  |  |
| 18 | 16649 | 57 | ideal_C | 138 | 6602 | 2793 |  |  |  |
| 19 | 9777 | 7057 | ideal_C | 93 | 4836 | 1882 | yes |  |  |
| 20 | 9588 | 6868 | ideal_C | 92 | 4646 | 1862 | yes |  |  |
| 21 | 9268 | 6548 | ideal_C | 44 | 2116 | 891 | yes |  |  |
| 22 | 8918 | 6198 | ideal_C | 60 | 2771 | 1214 | yes |  |  |
| 23 | 8997 | 6277 | ideal_C | 54 | 1942 | 1093 | yes |  |  |
| 24 | 8641 | 5921 | ideal_C | 66 | 3588 | 1336 | yes |  |  |
| 25 | 8039 | 5319 | ideal_C | 462 | 24267 | 9351 | yes |  |  |
| 26 | 8108 | 5388 | ideal_C | 466 | 22185 | 9432 | yes |  |  |
| 27 | 9214 | 6494 | ideal_C | 672 | 31540 | 13602 | yes |  |  |
| 28 | 9519 | 6799 | ideal_C | 428 | 16790 | 8663 | yes |  |  |
| 29 | 9793 | 7073 | ideal_C | 509 | 19430 | 10303 | yes |  |  |
| 30 | 9638 | 6918 | ideal_C | 516 | 25028 | 10444 | yes |  |  |
| 31 | 9911 | 7191 | ideal_C | 637 | 27177 | 12893 | yes |  |  |
| 32 | 10593 | 7873 | ideal_C | 240 | 8158 | 4858 | yes |  |  |
| 33 | 10304 | 7584 | ideal_C | 74 | 3270 | 1498 | yes |  |  |
| 34 | 10110 | 7390 | ideal_C | 43 | 2003 | 870 | yes |  |  |
| 35 | 9945 | 7225 | ideal_C | 51 | 1713 | 1032 | yes |  |  |
| 36 | 9936 | 7216 | ideal_C | 76 | 2884 | 1538 | yes |  |  |
| 37 | 9984 | 7264 | ideal_C | 72 | 4883 | 1457 | yes |  |  |
| 38 | 9981 | 7261 | ideal_C | 315 | 11189 | 6376 | yes |  |  |

Run totals: calls 39; charged 142,641 of 428,561 prompt tokens (33.3%); model LLM time 351.6 s; tool+harness max(0, 794.3 − 732.9) = 61.5 s; model job time 413.1 s; post-snip calls 21.

## tbench · prefix_aa · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__aXqXBDo` (job `e4r0b28__c00`): reward 0.0 → success 0.0; s15 status timeout; agent wall 2700.1 s
- table row (all 90 runs of this arm): runs 90, calls 3541, computed tok / call 9,652, / run 379,750, LLM 474 s, tool 462 s, job 936 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | ideal_C | 487 | 21455 | 9857 |  |  |  |
| 1 | 2617 | 41 | ideal_C | 63 | 2293 | 1275 |  |  |  |
| 2 | 3538 | 866 | ideal_C | 200 | 5096 | 4048 |  |  |  |
| 3 | 3759 | 31 | ideal_C | 10229 | 470219 | 207043 |  |  |  |
| 4 | 14016 | 1408 | ideal_C | 235 | 60549 | 4757 | yes |  |  |
| 5 | 15623 | 1383 | ideal_C | 10560 | 394852 | 213743 |  |  |  |
| 6 | 26212 | 36 | ideal_C | 58 | 29318 | 1174 |  |  |  |
| 7 | 27183 | 927 | ideal_C | 9352 | 315728 | 189292 |  |  |  |
| 8 | 36564 | 36 | ideal_C | 57 | 32093 | 1154 |  |  |  |
| 9 | 37524 | 916 | ideal_C | 8665 | 330787 | 175387 |  |  |  |
| 10 | 46234 | 58 | ideal_C | 66 | 18706 | 1336 |  |  |  |
| 11 | 47183 | 895 | ideal_C | 7601 | 299407 | 153850 |  |  |  |
| 12 | 54813 | 45 | ideal_C | 58 | 17443 | 1174 |  |  |  |
| 13 | 55670 | 806 | ideal_C | 314 | 30606 | 6356 |  |  |  |
| 14 | 56511 | 543 | ideal_C | 484 | 37729 | 9797 |  |  |  |
| 15 | 57631 | 639 | ideal_C | 2339 | 126140 | 47343 |  |  |  |
| 16 | 60403 | 435 | ideal_C | 7818 | 300189 | 158243 |  |  |  |
| 17 | 68250 | 42 | ideal_C | 57 | 14943 | 1154 |  |  |  |
| 18 | 69090 | 786 | ideal_C | 161 | 21891 | 3259 |  |  |  |
| 19 | 69497 | 249 | ideal_C | 138 | 32124 | 2793 |  |  |  |
| 20 | 70165 | 533 | ideal_C | 171 | 26449 | 3461 |  |  |  |

Run totals: calls 21; charged 12,778 of 824,586 prompt tokens (1.5%); model LLM time 1196.5 s; tool+harness max(0, 2700.1 − 2588.0) = 112.0 s; model job time 1308.5 s; post-snip calls 1.

## tbench · prefix_aa · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__wJ8qnQ5` (job `e4r0b02__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 233.4 s
- table row (all 90 runs of this arm): runs 90, calls 3541, computed tok / call 9,652, / run 379,750, LLM 474 s, tool 462 s, job 936 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | ideal_C | 95 | 4384 | 1923 |  |  |  |
| 1 | 2725 | 981 | ideal_C | 4601 | 187787 | 93128 |  |  |  |
| 2 | 7347 | 35 | ideal_C | 345 | 14206 | 6983 |  |  |  |
| 3 | 7719 | 39 | ideal_C | 66 | 2560 | 1336 |  |  |  |
| 4 | 7834 | 58 | ideal_C | 85 | 3525 | 1720 |  |  |  |
| 5 | 7954 | 50 | ideal_C | 73 | 3025 | 1478 |  |  |  |
| 6 | 8049 | 33 | ideal_C | 120 | 4880 | 2429 |  |  |  |
| 7 | 8190 | 30 | ideal_C | 273 | 10424 | 5526 |  |  |  |

Run totals: calls 8; charged 2,878 of 51,470 prompt tokens (5.6%); model LLM time 114.5 s; tool+harness max(0, 233.4 − 230.8) = 2.6 s; model job time 117.1 s; post-snip calls 0.

## tbench · shift · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__kQnvJ6w` (job `e4r0b28__c00`): reward 1.0 → success 1.0; s15 status timeout; agent wall 2700.1 s
- table row (all 90 runs of this arm): runs 90, calls 3403, computed tok / call 596, / run 22,518, LLM 470 s, tool 539 s, job 1008 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | C | 498 | 28259 | 10080 |  |  |  |
| 1 | 2706 | 106 | C | 170 | 7463 | 3441 |  |  |  |
| 2 | 3728 | 853 | C | 49 | 1435 | 992 |  |  |  |
| 3 | 3843 | 67 | C | 9302 | 452190 | 188280 |  |  |  |
| 4 | 13184 | 40 | C | 167 | 21608 | 3380 |  |  |  |
| 5 | 13962 | 612 | C | 147 | 14679 | 2975 |  |  |  |
| 6 | 14187 | 79 | C | 192 | 15339 | 3886 |  |  |  |
| 7 | 15894 | 1516 | C | 2696 | 105726 | 54569 |  |  |  |
| 8 | 18613 | 24 | C | 1979 | 111036 | 40057 |  |  |  |
| 9 | 20615 | 24 | C | 665 | 51924 | 13460 |  |  |  |
| 10 | 21319 | 40 | C | 825 | 44382 | 16699 |  |  |  |
| 11 | 22167 | 24 | C | 729 | 73257 | 14756 |  |  |  |
| 12 | 22919 | 24 | C | 615 | 47754 | 12448 |  |  |  |
| 13 | 23573 | 40 | C | 59 | 13394 | 1194 |  |  |  |
| 14 | 24233 | 602 | C | 269 | 29453 | 5445 |  |  |  |
| 15 | 24597 | 96 | C | 2467 | 93015 | 49934 |  |  |  |
| 16 | 27108 | 45 | C | 64 | 33701 | 1295 |  |  |  |
| 17 | 28261 | 1090 | C | 447 | 50620 | 9048 |  |  |  |
| 18 | 28731 | 24 | C | 195 | 81407 | 3947 |  |  |  |
| 19 | 29003 | 78 | C | 151 | 19188 | 3056 |  |  |  |
| 20 | 29217 | 64 | C | 513 | 48589 | 10384 |  |  |  |
| 21 | 30093 | 364 | C | 1348 | 76018 | 27285 |  |  |  |
| 22 | 30463 | 58 | C | 136 | 28365 | 2753 | yes |  |  |
| 23 | 30537 | 60 | C | 71 | 61518 | 1437 | yes |  |  |
| 24 | 21887 | 627 | C | 114 | 113694 | 2307 | yes | 0.1136 |  |
| 25 | 22240 | 1024 | C | 107 | 27813 | 2166 | yes |  |  |
| 26 | 23458 | 1343 | C | 94 | 17123 | 1903 | yes |  |  |
| 27 | 22468 | 641 | C | 225 | 20826 | 4554 | yes |  |  |
| 28 | 20026 | 70 | C | 260 | 32407 | 5263 | yes |  |  |
| 29 | 18330 | 53 | C | 537 | 39670 | 10869 | yes |  |  |
| 30 | 18384 | 239 | C | 379 | 25900 | 7671 | yes |  |  |
| 31 | 18057 | 160 | C | 168 | 13940 | 3400 | yes |  |  |
| 32 | 17502 | 36 | C | 371 | 33297 | 7509 | yes |  |  |
| 33 | 17329 | 128 | C | 873 | 45530 | 17670 | yes |  |  |
| 34 | 17690 | 166 | C | 634 | 67426 | 12833 | yes | 0.0645 |  |
| 35 | 18076 | 123 | C | 742 | 42371 | 15019 | yes |  |  |
| 36 | 16589 | 300 | C | 86 | 20379 | 1741 | yes |  |  |
| 37 | 16449 | 945 | C | 337 | 29674 | 6821 | yes |  |  |
| 38 | 16419 | 110 | C | 89 | 18092 | 1801 | yes |  |  |
| 39 | 16728 | 510 | C | 50 | 19405 | 1012 | yes |  |  |
| 40 | 16727 | 181 | C | 59 | 23105 | 1194 | yes |  |  |
| 41 | 16116 | 213 | C | 193 | 25011 | 3906 | yes |  |  |
| 42 | 14945 | 41 | C | 151 | 16498 | 3056 | yes |  |  |
| 43 | 14958 | 69 | C | 770 | 44064 | 15585 | yes |  |  |
| 44 | 15484 | 454 | C | 259 | 23535 | 5242 | yes |  |  |
| 45 | 14907 | 313 | C | 50 | 13920 | 1012 | yes |  |  |
| 46 | 13598 | 102 | C | 61 | 11766 | 1235 | yes |  |  |
| 47 | 13426 | 491 | C | 658 | 41443 | 13318 | yes |  |  |
| 48 | 13891 | 102 | C | 402 | 33968 | 8137 | yes |  |  |
| 49 | 14173 | 204 | C | 538 | 39522 | 10890 | yes |  |  |
| 50 | 14035 | 89 | C | 824 | 49038 | 16678 | yes |  |  |
| 51 | 14361 | 41 | C | 59 | 12181 | 1194 | yes |  |  |
| 52 | 14281 | 76 | C | 166 | 15932 | 3360 | yes |  |  |
| 53 | 13999 | 40 | C | 86 | 17494 | 1741 | yes |  |  |
| 54 | 13686 | 640 | C | 192 | 45349 | 3886 | yes |  |  |
| 55 | 13211 | 101 | C | 80 | 16992 | 1619 | yes |  |  |
| 56 | 13795 | 1535 | C | 74 | 29801 | 1498 | yes |  |  |
| 57 | 13657 | 819 | C | 215 | 40881 | 4352 | yes |  |  |

Run totals: calls 58; charged 20,019 of 1,041,938 prompt tokens (1.9%); model LLM time 681.2 s; tool+harness max(0, 2700.1 − 2578.4) = 121.7 s; model job time 803.0 s; post-snip calls 36.

## tbench · shift · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__uRCJmSa` (job `e4r0b02__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 359.2 s
- table row (all 90 runs of this arm): runs 90, calls 3403, computed tok / call 596, / run 22,518, LLM 470 s, tool 539 s, job 1008 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | C | 93 | 1299 | 1882 |  |  |  |
| 1 | 2723 | 979 | C | 2569 | 100859 | 51999 |  |  |  |
| 2 | 5633 | 342 | C | 228 | 10318 | 4615 |  |  |  |
| 3 | 5882 | 22 | C | 535 | 21980 | 10829 |  |  |  |
| 4 | 6774 | 358 | C | 517 | 21076 | 10464 |  |  |  |
| 5 | 7692 | 402 | C | 850 | 35198 | 17205 |  |  |  |
| 6 | 8912 | 371 | C | 437 | 17459 | 8845 |  |  |  |
| 7 | 9658 | 310 | C | 605 | 24544 | 12246 |  |  |  |
| 8 | 10646 | 384 | C | 727 | 27895 | 14715 |  |  |  |
| 9 | 11884 | 512 | C | 541 | 22068 | 10950 |  |  |  |
| 10 | 12753 | 329 | C | 414 | 16886 | 8380 |  |  |  |
| 11 | 13330 | 164 | C | 158 | 5916 | 3198 |  |  |  |
| 12 | 13509 | 22 | C | 79 | 3254 | 1599 |  |  |  |
| 13 | 13615 | 28 | C | 48 | 2205 | 972 |  |  |  |
| 14 | 13682 | 20 | C | 62 | 2689 | 1255 |  |  |  |
| 15 | 13782 | 39 | C | 74 | 3196 | 1498 |  |  |  |
| 16 | 13901 | 46 | C | 216 | 8006 | 4372 |  |  |  |
| 17 | 14137 | 21 | C | 116 | 4883 | 2348 |  |  |  |
| 18 | 14274 | 22 | C | 51 | 2010 | 1032 |  |  |  |
| 19 | 14370 | 46 | C | 100 | 4097 | 2024 |  |  |  |
| 20 | 14546 | 77 | C | 73 | 3500 | 1478 |  |  |  |
| 21 | 14654 | 36 | C | 337 | 13913 | 6821 |  |  |  |

Run totals: calls 22; charged 6,182 of 238,009 prompt tokens (2.6%); model LLM time 178.7 s; tool+harness max(0, 359.2 − 353.3) = 5.9 s; model job time 184.7 s; post-snip calls 0.

## tbench · shift+EPIC · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__LNGBkwW` (job `e4r0b28__c00`): reward 1.0 → success 1.0; s15 status timeout; agent wall 2700.1 s
- table row (all 90 runs of this arm): runs 90, calls 3840, computed tok / call 614, / run 26,190, LLM 529 s, tool 431 s, job 960 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | C | 357 | 18635 | 7226 |  |  |  |
| 1 | 2479 | 20 | C | 47 | 6013 | 951 |  |  |  |
| 2 | 20806 | 18281 | C | 9839 | 475354 | 199149 |  |  |  |
| 3 | 30690 | 46 | C | 49 | 13356 | 992 |  |  |  |
| 4 | 30953 | 215 | C | 10862 | 402358 | 219856 |  |  |  |
| 5 | 41844 | 30 | C | 51 | 42899 | 1032 |  |  |  |
| 6 | 42613 | 719 | C | 9410 | 319848 | 190466 |  |  |  |
| 7 | 52052 | 30 | C | 50 | 13281 | 1012 |  |  |  |
| 8 | 52770 | 669 | C | 7432 | 275997 | 150430 |  |  |  |
| 9 | 60247 | 46 | C | 50 | 24869 | 1012 |  |  |  |
| 10 | 60855 | 559 | C | 8008 | 305939 | 162088 |  |  |  |
| 11 | 68892 | 169 | C | 50 | 21347 | 1012 | yes |  |  |
| 12 | 69741 | 800 | C | 726 | 48709 | 14695 |  |  |  |
| 13 | 70490 | 24 | C | 52 | 17508 | 1053 |  |  |  |
| 14 | 71097 | 556 | C | 1946 | 102391 | 39389 |  |  |  |
| 15 | 73082 | 40 | C | 57 | 13503 | 1154 |  |  |  |
| 16 | 73694 | 556 | C | 7309 | 293324 | 147940 |  |  |  |
| 17 | 81032 | 30 | C | 48 | 10563 | 972 |  |  |  |
| 18 | 81592 | 513 | C | 1560 | 63033 | 31576 |  |  |  |
| 19 | 83175 | 24 | C | 52 | 16487 | 1053 |  |  |  |
| 20 | 83752 | 526 | C | 1228 | 69584 | 24856 |  |  |  |

Run totals: calls 21; charged 25,956 of 1,153,959 prompt tokens (2.2%); model LLM time 1197.9 s; tool+harness max(0, 2700.1 − 2555.0) = 145.1 s; model job time 1343.0 s; post-snip calls 1.

## tbench · shift+EPIC · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__aiSyFQY` (job `e4r0b02__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 913.5 s
- table row (all 90 runs of this arm): runs 90, calls 3840, computed tok / call 614, / run 26,190, LLM 529 s, tool 431 s, job 960 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | C | 95 | 1920 | 1923 |  |  |  |
| 1 | 2725 | 979 | C | 5886 | 239121 | 119137 |  |  |  |
| 2 | 8937 | 327 | C | 563 | 21783 | 11396 |  |  |  |
| 3 | 9598 | 99 | C | 779 | 32070 | 15768 |  |  |  |
| 4 | 10398 | 248 | C | 509 | 20253 | 10303 | yes |  |  |
| 5 | 11221 | 315 | C | 905 | 36250 | 18318 |  |  |  |
| 6 | 12362 | 237 | C | 895 | 37009 | 18116 |  |  |  |
| 7 | 13538 | 282 | C | 219 | 9050 | 4433 |  |  |  |
| 8 | 13818 | 62 | C | 127 | 6063 | 2571 |  |  |  |
| 9 | 13964 | 20 | C | 51 | 2072 | 1032 |  |  |  |
| 10 | 14050 | 36 | C | 65 | 3173 | 1316 |  |  |  |
| 11 | 14137 | 23 | C | 108 | 4460 | 2186 |  |  |  |
| 12 | 14276 | 32 | C | 67 | 2939 | 1356 |  |  |  |
| 13 | 14380 | 38 | C | 68 | 3059 | 1376 |  |  |  |
| 14 | 14467 | 20 | C | 59 | 2621 | 1194 |  |  |  |
| 15 | 14552 | 27 | C | 504 | 19944 | 10201 |  |  |  |
| 16 | 15222 | 167 | C | 791 | 31050 | 16010 |  |  |  |
| 17 | 16521 | 509 | C | 1327 | 65277 | 26860 |  |  |  |
| 18 | 17888 | 41 | C | 401 | 22287 | 8117 |  |  |  |
| 19 | 18367 | 79 | C | 1163 | 54126 | 23540 |  |  |  |
| 20 | 19793 | 264 | C | 1842 | 74979 | 37284 |  |  |  |
| 21 | 21817 | 335 | C | 168 | 8108 | 3400 | yes |  |  |
| 22 | 15900 | 254 | C | 1277 | 52972 | 25848 | yes |  |  |
| 23 | 16559 | 178 | C | 990 | 41128 | 20038 | yes |  |  |
| 24 | 16954 | 340 | C | 817 | 47866 | 16537 | yes | 0.1137 |  |
| 25 | 17164 | 351 | C | 570 | 22554 | 11537 | yes |  |  |
| 26 | 16629 | 171 | C | 122 | 4962 | 2469 | yes |  |  |
| 27 | 15596 | 167 | C | 69 | 4208 | 1397 | yes | 0.2020 |  |
| 28 | 15442 | 203 | C | 208 | 9455 | 4210 | yes |  |  |
| 29 | 15525 | 156 | C | 216 | 8316 | 4372 | yes |  |  |

Run totals: calls 30; charged 7,612 of 423,452 prompt tokens (1.8%); model LLM time 422.2 s; tool+harness max(0, 913.5 − 889.1) = 24.4 s; model job time 446.7 s; post-snip calls 10.

## tbench · shift+EPICa1 · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__nvBP6wU` (job `e4r0b28__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 2479.1 s
- table row (all 90 runs of this arm): runs 90, calls 3743, computed tok / call 5,596, / run 232,745, LLM 499 s, tool 525 s, job 1024 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | C | 495 | 31144 | 10019 |  |  |  |
| 1 | 2635 | 38 | C | 124 | 10319 | 2510 |  |  |  |
| 2 | 21039 | 18281 | C | 9281 | 454492 | 187855 |  |  |  |
| 3 | 30365 | 46 | C | 140 | 20082 | 2834 |  |  |  |
| 4 | 31178 | 674 | C | 1308 | 58805 | 26475 |  |  |  |
| 5 | 32509 | 24 | C | 1806 | 107006 | 36555 |  |  |  |
| 6 | 34354 | 40 | C | 65 | 42278 | 1316 |  |  |  |
| 7 | 35035 | 617 | C | 868 | 47795 | 17569 |  |  |  |
| 8 | 35926 | 24 | C | 57 | 42941 | 1154 |  |  |  |
| 9 | 36498 | 516 | C | 722 | 55974 | 14614 |  |  |  |
| 10 | 37243 | 24 | C | 253 | 45887 | 5121 |  |  |  |
| 11 | 37604 | 109 | C | 604 | 45819 | 12225 |  |  |  |
| 12 | 38247 | 40 | C | 61 | 17295 | 1235 |  |  |  |
| 13 | 38807 | 500 | C | 365 | 28607 | 7388 |  |  |  |
| 14 | 39317 | 146 | C | 1519 | 61025 | 30746 |  |  |  |
| 15 | 40875 | 40 | C | 57 | 59436 | 1154 |  |  |  |
| 16 | 41548 | 617 | C | 246 | 26253 | 4979 |  |  |  |
| 17 | 41896 | 103 | C | 390 | 42239 | 7894 |  |  |  |
| 18 | 42410 | 125 | C | 314 | 81085 | 6356 |  |  |  |
| 19 | 42872 | 149 | C | 992 | 47721 | 20079 |  |  |  |
| 20 | 43887 | 24 | C | 57 | 41634 | 1154 |  |  |  |
| 21 | 44459 | 516 | C | 374 | 59282 | 7570 |  |  |  |
| 22 | 26511 | 210 | C | 1034 | 188415 | 20929 | yes | 0.0608 |  |
| 23 | 18242 | 14513 | C | 58 | 79132 | 1174 | yes | 0.0272 |  |
| 24 | 18166 | 1920 | C | 321 | 41101 | 6497 | yes |  |  |
| 25 | 17265 | 12525 | C | 499 | 39409 | 10100 | yes |  |  |
| 26 | 16033 | 2355 | C | 448 | 26368 | 9068 | yes |  |  |
| 27 | 16019 | 10728 | C | 193 | 25260 | 3906 | yes |  |  |
| 28 | 25054 | 12525 | C | 1370 | 58207 | 27730 | yes |  |  |
| 29 | 25961 | 9826 | C | 81 | 26898 | 1640 | yes |  |  |
| 30 | 25338 | 13688 | C | 126 | 18148 | 2550 | yes |  |  |
| 31 | 25139 | 8826 | C | 337 | 27618 | 6821 | yes |  |  |
| 32 | 24980 | 13961 | C | 460 | 35000 | 9311 | yes |  |  |
| 33 | 25137 | 8228 | C | 244 | 26647 | 4939 | yes |  |  |
| 34 | 24960 | 14499 | C | 101 | 19542 | 2044 | yes |  |  |
| 35 | 23678 | 6579 | C | 415 | 33105 | 8400 | yes |  |  |
| 36 | 23604 | 14795 | C | 353 | 42013 | 7145 | yes |  |  |
| 37 | 23657 | 6010 | C | 109 | 15282 | 2206 | yes |  |  |
| 38 | 23915 | 15811 | C | 275 | 22362 | 5566 | yes |  |  |
| 39 | 23883 | 5309 | C | 313 | 26112 | 6335 | yes |  |  |
| 40 | 23277 | 16171 | C | 224 | 21522 | 4534 | yes |  |  |
| 41 | 23046 | 4152 | C | 1307 | 64911 | 26455 | yes |  |  |
| 42 | 23957 | 16260 | C | 215 | 20892 | 4352 | yes |  |  |
| 43 | 23238 | 5240 | C | 103 | 23927 | 2085 | yes |  |  |
| 44 | 23267 | 15425 | C | 114 | 19857 | 2307 | yes |  |  |
| 45 | 23206 | 5376 | C | 68 | 16752 | 1376 | yes |  |  |
| 46 | 22691 | 15123 | C | 470 | 36712 | 9513 | yes |  |  |

Run totals: calls 47; charged 274,811 of 1,321,031 prompt tokens (20.8%); model LLM time 593.8 s; tool+harness max(0, 2479.1 − 2382.3) = 96.8 s; model job time 690.6 s; post-snip calls 25.

## tbench · shift+EPICa1 · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__WE4krZP` (job `e4r0b02__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 1601.5 s
- table row (all 90 runs of this arm): runs 90, calls 3743, computed tok / call 5,596, / run 232,745, LLM 499 s, tool 525 s, job 1024 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | C | 95 | 3209 | 1923 |  |  |  |
| 1 | 2725 | 979 | C | 4389 | 180438 | 88837 |  |  |  |
| 2 | 7165 | 52 | C | 48 | 2219 | 972 |  |  |  |
| 3 | 7232 | 20 | C | 62 | 2684 | 1255 |  |  |  |
| 4 | 7316 | 23 | C | 125 | 4727 | 2530 |  |  |  |
| 5 | 7517 | 77 | C | 197 | 8150 | 3987 |  |  |  |
| 6 | 7745 | 32 | C | 117 | 4794 | 2368 |  |  |  |
| 7 | 7881 | 20 | C | 147 | 5840 | 2975 |  |  |  |
| 8 | 8092 | 65 | C | 108 | 4330 | 2186 |  |  |  |
| 9 | 8282 | 83 | C | 91 | 4052 | 1842 |  |  |  |
| 10 | 8424 | 52 | C | 607 | 23672 | 12286 |  |  |  |
| 11 | 9105 | 75 | C | 84 | 3357 | 1700 |  |  |  |
| 12 | 9240 | 52 | C | 419 | 17186 | 8481 |  |  |  |
| 13 | 9699 | 41 | C | 237 | 10254 | 4797 |  |  |  |
| 14 | 10003 | 68 | C | 280 | 10556 | 5667 |  |  |  |
| 15 | 10358 | 76 | C | 322 | 13660 | 6518 |  |  |  |
| 16 | 10731 | 52 | C | 308 | 13038 | 6234 |  |  |  |
| 17 | 11129 | 91 | C | 118 | 5135 | 2388 |  |  |  |
| 18 | 11298 | 52 | C | 420 | 16620 | 8501 |  |  |  |
| 19 | 11849 | 132 | C | 117 | 5384 | 2368 |  |  |  |
| 20 | 12033 | 68 | C | 1400 | 58145 | 28337 |  |  |  |
| 21 | 13473 | 41 | C | 126 | 5378 | 2550 |  |  |  |
| 22 | 9231 | 199 | C | 543 | 21826 | 10991 | yes |  |  |
| 23 | 9770 | 6441 | C | 156 | 6245 | 3158 | yes |  |  |
| 24 | 9893 | 780 | C | 174 | 9172 | 3522 | yes |  |  |
| 25 | 9910 | 6367 | C | 108 | 6044 | 2186 | yes |  |  |
| 26 | 9857 | 1032 | C | 137 | 6422 | 2773 | yes |  |  |
| 27 | 9901 | 6143 | C | 998 | 50262 | 20200 | yes |  |  |
| 28 | 10725 | 1253 | C | 127 | 7459 | 2571 | yes |  |  |
| 29 | 10729 | 6771 | C | 622 | 30009 | 12590 | yes |  |  |
| 30 | 11238 | 1398 | C | 97 | 4089 | 1963 | yes |  |  |
| 31 | 10691 | 6607 | C | 718 | 29908 | 14533 | yes |  |  |
| 32 | 11353 | 1585 | C | 542 | 20680 | 10971 | yes |  |  |
| 33 | 11516 | 6800 | C | 106 | 5003 | 2146 | yes |  |  |
| 34 | 11369 | 2178 | C | 116 | 4490 | 2348 | yes |  |  |
| 35 | 11200 | 6328 | C | 127 | 4980 | 2571 | yes |  |  |
| 36 | 10999 | 2328 | C | 108 | 6002 | 2186 | yes |  |  |
| 37 | 10758 | 5733 | C | 153 | 7556 | 3097 | yes |  |  |
| 38 | 10868 | 2573 | C | 213 | 10128 | 4311 | yes |  |  |
| 39 | 10669 | 5294 | C | 289 | 14178 | 5850 | yes |  |  |
| 40 | 10850 | 2862 | C | 133 | 6263 | 2692 | yes |  |  |
| 41 | 9646 | 4073 | C | 1134 | 44631 | 22953 | yes |  |  |
| 42 | 10654 | 3066 | C | 96 | 3768 | 1943 | yes |  |  |
| 43 | 10188 | 4856 | C | 90 | 6268 | 1822 | yes |  |  |
| 44 | 10138 | 2646 | C | 366 | 15642 | 7408 | yes |  |  |
| 45 | 10359 | 4824 | C | 100 | 4414 | 2024 | yes |  |  |
| 46 | 10320 | 2807 | C | 140 | 4887 | 2834 | yes |  |  |
| 47 | 10382 | 4870 | C | 83 | 3017 | 1680 | yes |  |  |
| 48 | 9481 | 2766 | C | 520 | 18497 | 10525 | yes |  |  |
| 49 | 9953 | 4081 | C | 144 | 5193 | 2915 | yes |  |  |
| 50 | 9549 | 3271 | C | 288 | 11362 | 5829 | yes |  |  |
| 51 | 9754 | 3606 | C | 259 | 11113 | 5242 | yes |  |  |
| 52 | 9260 | 3400 | C | 150 | 6296 | 3036 | yes |  |  |
| 53 | 8910 | 3218 | C | 399 | 17208 | 8076 | yes |  |  |
| 54 | 9173 | 2975 | C | 121 | 4454 | 2449 | yes |  |  |
| 55 | 9142 | 3457 | C | 117 | 5916 | 2368 | yes |  |  |
| 56 | 9154 | 3021 | C | 106 | 5214 | 2146 | yes |  |  |
| 57 | 9152 | 3436 | C | 127 | 5544 | 2571 | yes |  |  |
| 58 | 9022 | 2899 | C | 106 | 3902 | 2146 | yes |  |  |
| 59 | 8843 | 3349 | C | 92 | 5166 | 1862 | yes |  |  |
| 60 | 8590 | 2725 | C | 75 | 3188 | 1518 | yes |  |  |
| 61 | 8463 | 3083 | C | 186 | 6715 | 3765 | yes |  |  |
| 62 | 7500 | 2485 | C | 278 | 10209 | 5627 | yes |  |  |
| 63 | 7665 | 2347 | C | 156 | 5787 | 3158 | yes |  |  |
| 64 | 7717 | 2625 | C | 354 | 14412 | 7165 | yes |  |  |
| 65 | 7677 | 2463 | C | 122 | 4247 | 2469 | yes |  |  |
| 66 | 7714 | 2540 | C | 98 | 3674 | 1984 | yes |  |  |
| 67 | 7591 | 2421 | C | 105 | 6958 | 2125 | yes | 0.0512 |  |
| 68 | 7614 | 2510 | C | 212 | 10321 | 4291 | yes |  |  |
| 69 | 7210 | 2295 | C | 363 | 14557 | 7347 | yes |  |  |
| 70 | 7404 | 2177 | C | 207 | 14532 | 4190 | yes |  |  |
| 71 | 7368 | 2570 | C | 159 | 10563 | 3218 | yes |  |  |
| 72 | 7289 | 2106 | C | 407 | 19274 | 8238 | yes |  |  |
| 73 | 7462 | 2386 | C | 88 | 3074 | 1781 | yes |  |  |
| 74 | 7180 | 2406 | C | 221 | 10391 | 4473 | yes |  |  |
| 75 | 7280 | 2071 | C | 104 | 3403 | 2105 | yes |  |  |
| 76 | 7251 | 2505 | C | 90 | 4052 | 1822 | yes | 0.1442 |  |
| 77 | 7223 | 2050 | C | 92 | 2617 | 1862 | yes |  |  |
| 78 | 7202 | 2492 | C | 179 | 7032 | 3623 | yes |  |  |
| 79 | 7235 | 2007 | C | 100 | 5049 | 2024 | yes |  |  |
| 80 | 7279 | 2643 | C | 68 | 3218 | 1376 | yes |  |  |
| 81 | 7274 | 1974 | C | 99 | 3495 | 2004 | yes |  |  |
| 82 | 7195 | 2595 | C | 120 | 6164 | 2429 | yes |  |  |
| 83 | 7045 | 1906 | C | 84 | 2748 | 1700 | yes |  |  |
| 84 | 6942 | 2395 | C | 80 | 2666 | 1619 | yes |  |  |
| 85 | 6643 | 1734 | C | 75 | 2186 | 1518 | yes |  |  |
| 86 | 6595 | 2234 | C | 77 | 2958 | 1559 | yes |  |  |
| 87 | 6587 | 1706 | C | 83 | 4352 | 1680 | yes |  |  |
| 88 | 6562 | 2184 | C | 102 | 3471 | 2065 | yes |  |  |
| 89 | 6445 | 1669 | C | 78 | 3249 | 1579 | yes |  |  |
| 90 | 6102 | 2005 | C | 83 | 4837 | 1680 | yes |  |  |
| 91 | 5911 | 1317 | C | 214 | 10259 | 4332 | yes |  |  |
| 92 | 5965 | 1891 | C | 98 | 3694 | 1984 | yes |  |  |
| 93 | 5649 | 1390 | C | 139 | 5732 | 2813 | yes |  |  |
| 94 | 5671 | 1553 | C | 114 | 5802 | 2307 | yes |  |  |
| 95 | 5576 | 1448 | C | 112 | 4223 | 2267 | yes |  |  |
| 96 | 5566 | 1451 | C | 98 | 4056 | 1984 | yes |  |  |
| 97 | 5575 | 1469 | C | 124 | 4742 | 2510 | yes |  |  |
| 98 | 5649 | 1478 | C | 112 | 7424 | 2267 | yes |  |  |
| 99 | 5589 | 1489 | C | 46 | 1700 | 931 | yes |  |  |
| 100 | 5498 | 1430 | C | 95 | 3301 | 1923 | yes |  |  |
| 101 | 5526 | 1448 | C | 85 | 4221 | 1720 | yes |  |  |
| 102 | 5505 | 1387 | C | 100 | 4950 | 2024 | yes |  |  |
| 103 | 5472 | 1436 | C | 83 | 3053 | 1680 | yes |  |  |
| 104 | 5491 | 1438 | C | 77 | 4417 | 1559 | yes |  |  |
| 105 | 5492 | 1437 | C | 81 | 4757 | 1640 | yes |  |  |
| 106 | 5468 | 1408 | C | 181 | 7040 | 3664 | yes |  |  |
| 107 | 5592 | 1466 | C | 130 | 3899 | 2631 | yes |  |  |
| 108 | 5610 | 1447 | C | 84 | 2610 | 1700 | yes |  |  |
| 109 | 5571 | 1497 | C | 74 | 3396 | 1498 | yes |  |  |
| 110 | 5586 | 1478 | C | 155 | 3986 | 3137 | yes |  |  |
| 111 | 5680 | 1458 | C | 110 | 5285 | 2226 | yes |  |  |
| 112 | 5563 | 1522 | C | 67 | 2193 | 1356 | yes |  |  |
| 113 | 5536 | 1369 | C | 115 | 3231 | 2328 | yes |  |  |
| 114 | 5522 | 1474 | C | 100 | 3281 | 2024 | yes |  |  |
| 115 | 5511 | 1360 | C | 99 | 5038 | 2004 | yes | 0.0861 |  |
| 116 | 5522 | 1511 | C | 147 | 5259 | 2975 | yes |  |  |
| 117 | 5563 | 1316 | C | 95 | 4624 | 1923 | yes |  |  |
| 118 | 5490 | 1502 | C | 127 | 3672 | 2571 | yes |  |  |
| 119 | 5561 | 1432 | C | 119 | 5076 | 2409 | yes |  |  |
| 120 | 5678 | 1567 | C | 185 | 6475 | 3745 | yes |  |  |
| 121 | 5805 | 1503 | C | 124 | 4102 | 2510 | yes |  |  |
| 122 | 5864 | 1683 | C | 216 | 7772 | 4372 | yes |  |  |
| 123 | 6039 | 1592 | C | 241 | 8607 | 4878 | yes |  |  |
| 124 | 6223 | 1848 | C | 184 | 7144 | 3724 | yes |  |  |
| 125 | 6431 | 1864 | C | 196 | 5449 | 3967 | yes |  |  |
| 126 | 6644 | 1995 | C | 404 | 12451 | 8177 | yes |  |  |
| 127 | 7059 | 2128 | C | 148 | 6103 | 2996 | yes |  |  |
| 128 | 7162 | 2354 | C | 123 | 3258 | 2490 | yes |  |  |
| 129 | 7235 | 2219 | C | 83 | 4167 | 1680 | yes |  |  |
| 130 | 7224 | 2333 | C | 88 | 2576 | 1781 | yes |  |  |
| 131 | 7144 | 2190 | C | 85 | 2389 | 1720 | yes |  |  |
| 132 | 7197 | 2390 | C | 378 | 12034 | 7651 | yes |  |  |
| 133 | 7729 | 2381 | C | 110 | 4172 | 2226 | yes |  |  |
| 134 | 7744 | 2699 | C | 91 | 3844 | 1842 | yes |  |  |
| 135 | 7815 | 2461 | C | 303 | 11663 | 6133 | yes |  |  |
| 136 | 8146 | 2793 | C | 91 | 2979 | 1842 | yes |  |  |
| 137 | 8114 | 2704 | C | 338 | 14271 | 6841 | yes |  |  |
| 138 | 8490 | 2859 | C | 320 | 11278 | 6477 | yes |  |  |
| 139 | 8895 | 3135 | C | 90 | 2924 | 1822 | yes |  |  |
| 140 | 8849 | 3046 | C | 534 | 16098 | 10809 | yes |  |  |
| 141 | 9354 | 3246 | C | 104 | 3558 | 2105 | yes |  |  |
| 142 | 9329 | 3390 | C | 62 | 2338 | 1255 | yes |  |  |
| 143 | 9239 | 3301 | C | 414 | 12822 | 8380 | yes |  |  |
| 144 | 9687 | 3500 | C | 472 | 14237 | 9554 | yes |  |  |
| 145 | 10137 | 3636 | C | 95 | 2777 | 1923 | yes |  |  |
| 146 | 9968 | 3731 | C | 62 | 2154 | 1255 | yes |  |  |
| 147 | 9509 | 3407 | C | 418 | 13254 | 8461 | yes |  |  |
| 148 | 9965 | 3576 | C | 142 | 4298 | 2874 | yes |  |  |
| 149 | 9996 | 3741 | C | 62 | 1674 | 1255 | yes |  |  |
| 150 | 10085 | 3729 | C | 357 | 9374 | 7226 | yes |  |  |
| 151 | 10562 | 3926 | C | 86 | 2143 | 1741 | yes |  |  |
| 152 | 10486 | 3896 | C | 60 | 1750 | 1214 | yes |  |  |
| 153 | 10041 | 3764 | C | 374 | 12539 | 7570 | yes |  |  |
| 154 | 10488 | 3794 | C | 93 | 2641 | 1882 | yes |  |  |
| 155 | 10406 | 3941 | C | 60 | 2162 | 1214 | yes |  |  |
| 156 | 10008 | 3630 | C | 321 | 9336 | 6497 | yes |  |  |
| 157 | 10213 | 3673 | C | 99 | 2756 | 2004 | yes |  |  |

Run totals: calls 158; charged 366,332 of 1,285,730 prompt tokens (28.5%); model LLM time 709.7 s; tool+harness max(0, 1601.5 − 1415.9) = 185.6 s; model job time 895.3 s; post-snip calls 136.

## tbench · shift+EPICa4 · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__ZRXqRnZ` (job `e4r0b28__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 2342.7 s
- table row (all 90 runs of this arm): runs 90, calls 3220, computed tok / call 2,112, / run 75,573, LLM 437 s, tool 649 s, job 1085 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | C | 403 | 28302 | 8157 |  |  |  |
| 1 | 2560 | 55 | C | 56 | 5363 | 1133 |  |  |  |
| 2 | 2701 | 86 | C | 176 | 5501 | 3562 |  |  |  |
| 3 | 2924 | 48 | C | 10657 | 508505 | 215706 |  |  |  |
| 4 | 13610 | 30 | C | 184 | 14328 | 3724 |  |  |  |
| 5 | 14398 | 605 | C | 11551 | 442296 | 233802 |  |  |  |
| 6 | 25978 | 30 | C | 62 | 27077 | 1255 |  |  |  |
| 7 | 26787 | 748 | C | 9260 | 345688 | 187430 |  |  |  |
| 8 | 36076 | 30 | C | 64 | 37339 | 1295 |  |  |  |
| 9 | 36882 | 743 | C | 1913 | 77602 | 38721 |  |  |  |
| 10 | 38834 | 40 | C | 61 | 32567 | 1235 |  |  |  |
| 11 | 39632 | 738 | C | 1557 | 97650 | 31515 |  |  |  |
| 12 | 41217 | 29 | C | 61 | 19930 | 1235 |  |  |  |
| 13 | 41811 | 534 | C | 64 | 25410 | 1295 |  |  |  |
| 14 | 42454 | 580 | C | 66 | 14998 | 1336 |  |  |  |
| 15 | 43062 | 543 | C | 1255 | 57858 | 25402 |  |  |  |
| 16 | 44356 | 40 | C | 61 | 25944 | 1235 |  |  |  |
| 17 | 45162 | 746 | C | 227 | 21753 | 4595 |  |  |  |
| 18 | 46144 | 756 | C | 625 | 29731 | 12651 |  |  |  |
| 19 | 46792 | 24 | C | 270 | 28718 | 5465 |  |  |  |
| 20 | 47101 | 40 | C | 61 | 21250 | 1235 |  |  |  |
| 21 | 47907 | 746 | C | 482 | 30440 | 9756 |  |  |  |
| 22 | 48445 | 309 | C | 599 | 44692 | 12124 | yes |  |  |
| 23 | 48844 | 185 | C | 55 | 14337 | 1113 | yes |  |  |
| 24 | 38962 | 895 | C | 130 | 77570 | 2631 | yes | 0.0004 |  |
| 25 | 38583 | 414 | C | 448 | 32943 | 9068 | yes |  |  |
| 26 | 27605 | 22576 | C | 327 | 89391 | 6619 | yes | 0.0222 |  |
| 27 | 27268 | 1050 | C | 88 | 16070 | 1781 | yes |  |  |
| 28 | 18177 | 350 | C | 350 | 86507 | 7084 | yes | 0.0245 |  |

Run totals: calls 29; charged 35,073 of 936,375 prompt tokens (3.7%); model LLM time 832.2 s; tool+harness max(0, 2342.7 − 2259.8) = 83.0 s; model job time 915.1 s; post-snip calls 7.

## tbench · shift+EPICa4 · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__Di5Zbjh` (job `e4r0b02__c00`): reward 0.0 → success 0.0; s15 status completed; agent wall 452.0 s
- table row (all 90 runs of this arm): runs 90, calls 3220, computed tok / call 2,112, / run 75,573, LLM 437 s, tool 649 s, job 1085 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | C | 94 | 3745 | 1903 |  |  |  |
| 1 | 2724 | 979 | C | 4746 | 194616 | 96063 |  |  |  |
| 2 | 7491 | 22 | C | 2723 | 107976 | 55116 |  |  |  |
| 3 | 10249 | 36 | C | 56 | 2841 | 1133 |  |  |  |
| 4 | 10334 | 30 | C | 234 | 9437 | 4736 |  |  |  |
| 5 | 10617 | 50 | C | 147 | 5403 | 2975 |  |  |  |
| 6 | 10801 | 38 | C | 301 | 12434 | 6092 |  |  |  |
| 7 | 11155 | 54 | C | 54 | 2353 | 1093 |  |  |  |
| 8 | 11257 | 49 | C | 461 | 19090 | 9331 |  |  |  |
| 9 | 11763 | 46 | C | 58 | 2257 | 1174 |  |  |  |
| 10 | 11850 | 30 | C | 140 | 5452 | 2834 |  |  |  |
| 11 | 12009 | 20 | C | 256 | 11545 | 5182 |  |  |  |
| 12 | 12311 | 177 | C | 59 | 2459 | 1194 | yes |  |  |
| 13 | 12395 | 26 | C | 312 | 12725 | 6315 |  |  |  |
| 14 | 12771 | 65 | C | 66 | 3320 | 1336 |  |  |  |
| 15 | 12919 | 83 | C | 79 | 3694 | 1599 |  |  |  |
| 16 | 13017 | 20 | C | 200 | 8436 | 4048 |  |  |  |
| 17 | 13262 | 46 | C | 86 | 3775 | 1741 |  |  |  |
| 18 | 13413 | 66 | C | 60 | 2746 | 1214 |  |  |  |
| 19 | 13495 | 23 | C | 186 | 7872 | 3765 |  |  |  |
| 20 | 13730 | 50 | C | 178 | 6954 | 3603 |  |  |  |
| 21 | 13929 | 22 | C | 102 | 4003 | 2065 |  |  |  |
| 22 | 14076 | 46 | C | 324 | 13179 | 6558 |  |  |  |

Run totals: calls 23; charged 3,630 of 257,220 prompt tokens (1.4%); model LLM time 221.1 s; tool+harness max(0, 452.0 − 446.3) = 5.7 s; model job time 226.7 s; post-snip calls 1.

## tbench · shift+EPIC+recompute@JSD · block e4r0 · adaptive-rejection-sampler

- trial `adaptive-rejection-sampler__xqBvVFY` (job `e4r0b28__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 1789.5 s
- table row (all 90 runs of this arm): runs 90, calls 3477, computed tok / call 1,576, / run 60,880, LLM 503 s, tool 492 s, job 995 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2103 | 2103 | C_charged | 406 | 28402 | 8218 |  |  |  |
| 1 | 2528 | 20 | C_charged | 55 | 8385 | 1113 |  |  |  |
| 2 | 3425 | 843 | C_charged | 9233 | 450650 | 186883 |  |  |  |
| 3 | 12703 | 46 | C_charged | 123 | 23186 | 2490 |  |  |  |
| 4 | 12847 | 22 | C_charged | 49 | 9584 | 992 |  |  |  |
| 5 | 13532 | 637 | C_charged | 11668 | 429566 | 236170 |  |  |  |
| 6 | 25229 | 30 | C_charged | 52 | 27405 | 1053 |  |  |  |
| 7 | 25986 | 706 | C_charged | 8195 | 271379 | 165873 |  |  |  |
| 8 | 34210 | 30 | C_charged | 51 | 34245 | 1032 |  |  |  |
| 9 | 34957 | 697 | C_charged | 8369 | 295717 | 169395 |  |  |  |
| 10 | 43371 | 46 | C_charged | 50 | 26872 | 1012 |  |  |  |
| 11 | 44193 | 773 | C_charged | 131 | 21193 | 2652 |  |  |  |
| 12 | 44345 | 22 | C_charged | 87 | 25001 | 1761 |  |  |  |
| 13 | 44717 | 286 | C_charged | 114 | 24221 | 2307 |  |  |  |
| 14 | 44852 | 22 | C_charged | 523 | 36557 | 10586 |  |  |  |

Run totals: calls 15; charged 6,283 of 388,998 prompt tokens (1.6%); model LLM time 791.5 s; tool+harness max(0, 1789.5 − 1712.4) = 77.2 s; model job time 868.7 s; post-snip calls 0.

## tbench · shift+EPIC+recompute@JSD · block e4r0 · break-filter-js-from-html

- trial `break-filter-js-from-html__ySA9dpm` (job `e4r0b02__c00`): reward 1.0 → success 1.0; s15 status completed; agent wall 1846.4 s
- table row (all 90 runs of this arm): runs 90, calls 3477, computed tok / call 1,576, / run 60,880, LLM 503 s, tool 492 s, job 995 s

| call | P | charged | source | out tok | client ms | model ms | post-snip | probe first-token KL | decision (JSD, u, recompute, C_O, C_E) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1652 | 1652 | C_charged | 95 | 4308 | 1923 |  |  |  |
| 1 | 2725 | 979 | C_charged | 8677 | 351094 | 175629 |  |  |  |
| 2 | 11644 | 243 | C_charged | 262 | 10155 | 5303 |  |  |  |
| 3 | 11927 | 22 | C_charged | 292 | 12786 | 5910 |  |  |  |
| 4 | 12536 | 318 | C_charged | 530 | 22428 | 10728 |  |  |  |
| 5 | 13357 | 292 | C_charged | 506 | 21605 | 10242 |  |  |  |
| 6 | 14308 | 446 | C_charged | 171 | 6690 | 3461 |  |  |  |
| 7 | 14513 | 35 | C_charged | 112 | 4363 | 2267 |  |  |  |
| 8 | 14660 | 36 | C_charged | 73 | 3022 | 1478 |  |  |  |
| 9 | 14768 | 36 | C_charged | 63 | 2657 | 1275 |  |  |  |
| 10 | 14853 | 23 | C_charged | 173 | 7262 | 3502 |  |  |  |
| 11 | 15074 | 49 | C_charged | 102 | 3456 | 2065 |  |  |  |
| 12 | 15265 | 90 | C_charged | 79 | 3250 | 1599 |  |  |  |
| 13 | 15426 | 83 | C_charged | 114 | 4588 | 2307 |  |  |  |
| 14 | 15591 | 52 | C_charged | 431 | 18415 | 8724 |  |  |  |
| 15 | 16374 | 353 | C_charged | 389 | 20298 | 7874 |  |  |  |
| 16 | 17110 | 348 | C_charged | 606 | 29605 | 12266 |  |  |  |
| 17 | 18004 | 289 | C_charged | 1519 | 74562 | 30746 |  |  |  |
| 18 | 19544 | 294 | C_charged | 317 | 14625 | 6416 | yes |  | 0.0185, 0.300, False, 25, 269 |
| 19 | 20140 | 280 | C_charged | 499 | 19033 | 10100 |  |  |  |
| 20 | 20699 | 61 | C_charged | 272 | 10079 | 5505 |  |  |  |
| 21 | 21243 | 273 | C_charged | 668 | 25617 | 13521 |  |  |  |
| 22 | 13110 | 332 | C_charged | 511 | 51020 | 10343 | yes | 0.1532 | 0.0839, 0.778, False, 102, 230 |
| 23 | 13682 | 296 | C_charged | 600 | 23702 | 12144 | yes |  | 0.0971, 0.983, False, 84, 212 |
| 24 | 13704 | 792 | C_charged | 598 | 23750 | 12104 | yes |  | 0.1023, 0.516, False, 332, 460 |
| 25 | 13774 | 750 | C_charged | 507 | 21842 | 10262 | yes | 0.0176 | 0.0497, 0.121, False, 311, 439 |
| 26 | 13490 | 430 | C_charged | 527 | 20854 | 10667 | yes |  | 0.0956, 0.164, False, 151, 279 |
| 27 | 13880 | 332 | C_charged | 964 | 34849 | 19512 | yes |  | 0.0093, 0.501, False, 102, 230 |
| 28 | 15023 | 1078 | C_charged | 409 | 14990 | 8278 | yes |  | 0.0745, 0.223, False, 347, 731 |
| 29 | 15450 | 362 | C_charged | 399 | 17340 | 8076 | yes |  | 0.0636, 0.816, False, 117, 245 |
| 30 | 15998 | 664 | C_charged | 491 | 20077 | 9938 | yes |  | 0.1049, 0.522, False, 268, 396 |
| 31 | 16332 | 292 | C_charged | 242 | 9584 | 4898 | yes |  | 0.1100, 0.645, False, 82, 210 |
| 32 | 16455 | 254 | C_charged | 502 | 19814 | 10161 | yes |  | 0.0385, 0.440, False, 63, 191 |
| 33 | 17119 | 842 | C_charged | 931 | 35606 | 18844 | yes |  | 0.1389, 0.438, False, 357, 485 |
| 34 | 18008 | 410 | C_charged | 651 | 28359 | 13177 | yes |  | 0.1245, 0.611, False, 141, 269 |
| 35 | 18039 | 609 | C_charged | 432 | 21324 | 8744 | yes |  | 0.2621, 0.690, False, 157, 452 |
| 36 | 17914 | 554 | C_charged | 369 | 21919 | 7469 | yes |  | 0.1382, 0.185, False, 213, 341 |
| 37 | 17498 | 382 | C_charged | 233 | 15577 | 4716 | yes |  | 0.0644, 0.289, False, 127, 255 |
| 38 | 16327 | 414 | C_charged | 504 | 22523 | 10201 | yes |  | 0.1289, 0.206, False, 143, 271 |
| 39 | 16356 | 384 | C_charged | 608 | 22186 | 12306 | yes |  | 0.0876, 0.128, False, 128, 256 |
| 40 | 16540 | 412 | C_charged | 241 | 9686 | 4878 | yes |  | 0.0535, 0.612, False, 142, 270 |
| 41 | 16338 | 366 | C_charged | 234 | 10469 | 4736 | yes |  | 0.0498, 0.919, False, 119, 247 |
| 42 | 15942 | 402 | C_charged | 466 | 17154 | 9432 | yes |  | 0.1060, 0.576, False, 137, 265 |
| 43 | 15930 | 362 | C_charged | 500 | 21036 | 10120 | yes |  | 0.0601, 0.103, False, 117, 245 |
| 44 | 15601 | 334 | C_charged | 389 | 18230 | 7874 | yes |  | 0.0280, 0.949, False, 103, 231 |
| 45 | 15219 | 404 | C_charged | 419 | 17287 | 8481 | yes |  | 0.1081, 0.857, False, 138, 266 |
| 46 | 15121 | 12811 | C_charged | 268 | 12883 | 5425 | yes |  | 0.0447, 0.006, True, 141, 269 |
| 47 | 14904 | 416 | C_charged | 415 | 19228 | 8400 | yes |  | 0.2472, 0.620, False, 144, 272 |
| 48 | 14243 | 592 | C_charged | 447 | 16502 | 9048 | yes |  | 0.0986, 0.219, False, 232, 360 |
| 49 | 14366 | 532 | C_charged | 276 | 8523 | 5586 | yes |  | 0.1545, 0.730, False, 202, 330 |
| 50 | 14193 | 12037 | C_charged | 372 | 13540 | 7530 | yes |  | 0.0571, 0.021, True, 218, 346 |
| 51 | 14132 | 408 | C_charged | 344 | 13007 | 6963 | yes |  | 0.1389, 0.535, False, 140, 268 |
| 52 | 14280 | 346 | C_charged | 341 | 12914 | 6902 | yes |  | 0.3006, 0.626, False, 109, 237 |
| 53 | 13915 | 434 | C_charged | 416 | 14433 | 8420 | yes |  | 0.0803, 0.577, False, 153, 281 |
| 54 | 13464 | 538 | C_charged | 430 | 15416 | 8704 | yes |  | 0.1332, 0.862, False, 205, 333 |
| 55 | 13306 | 11148 | C_charged | 485 | 15187 | 9817 | yes |  | 0.1299, 0.013, True, 217, 345 |
| 56 | 13251 | 338 | C_charged | 301 | 9124 | 6092 | yes |  | 0.1001, 0.450, False, 105, 233 |
| 57 | 13293 | 602 | C_charged | 434 | 16475 | 8785 | yes |  | 0.0569, 0.499, False, 237, 365 |
| 58 | 13583 | 592 | C_charged | 350 | 9466 | 7084 | yes |  | 0.0864, 0.649, False, 232, 360 |
| 59 | 13413 | 374 | C_charged | 324 | 10721 | 6558 | yes |  | 0.0718, 0.378, False, 123, 251 |
| 60 | 13052 | 280 | C_charged | 216 | 8664 | 4372 | yes |  | 0.1566, 0.635, False, 76, 204 |
| 61 | 13038 | 366 | C_charged | 308 | 11641 | 6234 | yes |  | 0.0560, 0.059, False, 119, 247 |
| 62 | 13051 | 280 | C_charged | 163 | 6047 | 3299 | yes |  | 0.2191, 0.542, False, 76, 204 |
| 63 | 12693 | 274 | C_charged | 176 | 6219 | 3562 | yes |  | 0.0763, 0.991, False, 73, 201 |
| 64 | 12310 | 194 | C_charged | 47 | 3661 | 951 | yes |  | 0.0970, 0.938, False, 33, 161 |
| 65 | 11867 | 202 | C_charged | 70 | 3674 | 1417 | yes |  | 0.0328, 0.937, False, 37, 165 |
| 66 | 11426 | 248 | C_charged | 51 | 3034 | 1032 | yes |  | 0.0182, 0.417, False, 60, 188 |
| 67 | 11105 | 186 | C_charged | 467 | 14440 | 9452 | yes |  | 0.1187, 0.841, False, 29, 157 |
| 68 | 11043 | 364 | C_charged | 453 | 14311 | 9169 | yes |  | 0.0427, 0.859, False, 118, 246 |
| 69 | 10929 | 314 | C_charged | 309 | 9884 | 6254 | yes |  | 0.1219, 0.916, False, 93, 221 |
| 70 | 10818 | 254 | C_charged | 416 | 12334 | 8420 | yes |  | 0.0862, 0.895, False, 63, 191 |
| 71 | 10784 | 252 | C_charged | 361 | 11225 | 7307 | yes |  | 0.2572, 0.558, False, 62, 190 |
| 72 | 10870 | 638 | C_charged | 781 | 24154 | 15808 | yes |  | 0.0587, 0.564, False, 191, 447 |
| 73 | 11246 | 284 | C_charged | 420 | 10871 | 8501 | yes |  | 0.1836, 0.858, False, 78, 206 |
| 74 | 11387 | 921 | C_charged | 379 | 11006 | 7671 | yes |  | 0.0461, 0.785, False, 344, 577 |
| 75 | 11268 | 448 | C_charged | 192 | 7765 | 3886 | yes |  | 0.1074, 0.210, False, 160, 288 |
| 76 | 10974 | 314 | C_charged | 434 | 13279 | 8785 | yes |  | 0.1016, 0.516, False, 93, 221 |
| 77 | 10931 | 250 | C_charged | 289 | 8109 | 5850 | yes |  | 0.0514, 0.382, False, 61, 189 |
| 78 | 10636 | 314 | C_charged | 403 | 9701 | 8157 | yes |  | 0.1000, 0.586, False, 93, 221 |
| 79 | 10745 | 464 | C_charged | 126 | 3620 | 2550 | yes |  | 0.0930, 0.147, False, 168, 296 |
| 80 | 10535 | 256 | C_charged | 404 | 9967 | 8177 | yes |  | 0.1003, 0.909, False, 64, 192 |
| 81 | 10678 | 298 | C_charged | 708 | 23746 | 14330 | yes |  | 0.0398, 0.377, False, 85, 213 |
| 82 | 11179 | 722 | C_charged | 830 | 32638 | 16800 | yes | 0.0716 | 0.0473, 0.349, False, 169, 553 |
| 83 | 12124 | 830 | C_charged | 335 | 15511 | 6781 | yes |  | 0.0491, 0.973, False, 351, 479 |
| 84 | 12352 | 354 | C_charged | 285 | 11513 | 5769 | yes |  | 0.0420, 0.077, False, 113, 241 |
| 85 | 12672 | 10296 | C_charged | 523 | 23174 | 10586 | yes |  | 0.0794, 0.059, True, 108, 236 |
| 86 | 13180 | 358 | C_charged | 707 | 37108 | 14310 | yes |  | 0.0684, 0.453, False, 115, 243 |
| 87 | 14025 | 744 | C_charged | 449 | 26228 | 9088 | yes |  | 0.0912, 0.177, False, 244, 500 |
| 88 | 13948 | 224 | C_charged | 158 | 10828 | 3198 | yes |  | 0.1137, 0.871, False, 48, 176 |
| 89 | 13605 | 218 | C_charged | 42 | 3798 | 850 | yes |  | 0.0062, 0.999, False, 45, 173 |
| 90 | 13317 | 10831 | C_charged | 53 | 4141 | 1073 | yes |  | 0.0259, 0.019, True, 53, 181 |
| 91 | 12932 | 186 | C_charged | 89 | 5627 | 1801 | yes |  | 0.0523, 0.262, False, 29, 157 |
| 92 | 12537 | 260 | C_charged | 337 | 19082 | 6821 | yes |  | 0.0171, 0.312, False, 66, 194 |

Run totals: calls 93; charged 91,303 of 1,287,863 prompt tokens (7.1%); model LLM time 887.8 s; tool+harness max(0, 1846.4 − 1793.5) = 52.9 s; model job time 940.7 s; post-snip calls 72.

