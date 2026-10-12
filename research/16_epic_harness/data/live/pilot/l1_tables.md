**T6 KV deviation from a fresh recompute** (shadow probes: D = sum |K - K*|^2 / sum |K*|^2 over the rows of a class, all 64 layers; V likewise)

| arm | shadows | exact D^K / D^V (rows) | reused D^K / D^V (rows) | head D^K / D^V (rows) | computed D^K / D^V (rows) |
|---|---|---|---|---|---|
| shift | 218 | 6.68e-03 / 3.54e-02 (2,942,208) | 8.72e-02 / 3.85e-01 (3,931,332) | — | 2.03e-02 / 1.29e-01 (1,156,088) |
| epic128 | 249 | 2.72e-03 / 2.24e-02 (3,502,400) | 4.34e-02 / 2.62e-01 (3,888,116) | 6.05e-03 / 4.72e-02 (182,760) | 9.36e-03 / 8.48e-02 (677,072) |

Reused rows by **edits survived** (age) and by **offset from the run head**:

| arm | age 0 | age 1 | age 2-3 | age 4-7 | age 8-15 | age 16+ | off 0-31 | off 32-63 | off 64-127 | off 128-511 | off 512+ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| shift | 1.83e-02 | 5.23e-02 | 8.11e-02 | 7.51e-02 | 1.01e-01 | 1.37e-01 | 1.49e-01 | 1.61e-01 | 1.48e-01 | 1.30e-01 | 7.82e-02 |
| epic128 | 9.35e-04 | 1.96e-02 | 3.36e-02 | 3.53e-02 | 5.41e-02 | 1.12e-01 | — | — | — | 9.94e-02 | 3.64e-02 |

Reused rows by **layer** (groups of 4):

| arm | L0-3 | L12-15 | L24-27 | L36-39 | L48-51 | L60-63 |
|---|---|---|---|---|---|---|
| shift | 1.31e-03 | 2.48e-02 | 5.48e-02 | 1.09e-01 | 2.26e-01 | 1.21e-01 |
| epic128 | 4.43e-04 | 7.40e-03 | 1.82e-02 | 4.06e-02 | 1.26e-01 | 7.37e-02 |

