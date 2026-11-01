# Compliance probe `main` (pw_probe.py; Stage 2a)

Items: 141 requests carry 144 (request, heavy round) pairs (76 with >= 10 s of tools, the primary items); 61 premature controls; 100 spurious controls; 40 fresh controls. F: the front-load line; N: a neutral line of about the same length; C: no line (topic 20's stored samples).

## Pairs: does the response at L_j issue the heavy round's command?

| scope | measure | F | N | C | F − N (points) | F − C (points) |
|---|---|---|---|---|---|---|
| >= 10 s | valid early issue | 9.9 % [5.1, 17.7] | 4.4 % [1.7, 9.3] | 5.3 % [1.8, 11.2] | +5.4 [+1.5, +10.8] | +4.6 [+1.3, +9.6] |
| >= 10 s | issues it (target) | 14.0 % [7.3, 25.0] | 7.1 % [2.9, 14.8] | 7.6 % [2.9, 15.7] | +6.9 [+2.3, +12.5] | +6.4 [+2.1, +11.6] |
| >= 10 s | issues it (loose) | 15.3 % [8.5, 26.7] | 8.2 % [3.2, 17.3] | 8.9 % [3.1, 18.6] | +7.1 [+2.4, +12.5] | +6.4 [+1.8, +12.1] |
| >= 1 s | valid early issue | 8.8 % [5.1, 14.0] | 4.8 % [1.9, 9.3] | 5.1 % [2.0, 9.5] | +4.0 [+1.7, +7.1] | +3.6 [+1.5, +6.4] |
| >= 1 s | issues it (target) | 11.5 % [7.0, 17.3] | 6.3 % [3.2, 10.9] | 6.4 % [3.1, 11.3] | +5.2 [+2.5, +8.1] | +5.1 [+2.4, +8.1] |
| >= 1 s | issues it (loose) | 13.6 % [8.4, 20.5] | 8.2 % [3.9, 13.9] | 8.1 % [4.0, 13.8] | +5.5 [+2.9, +8.5] | +5.6 [+2.6, +8.8] |

| split (all pairs) | valid F − N (points) |
|---|---|
| swepro | +4.9 [+1.5, +9.1] |
| tbench | +2.8 [-0.8, +7.6] |
| adjacent | +5.9 [+2.2, +11.2] |
| farther | +0.0 [+0.0, +0.0] |

## Costs

| measure | F | N | C | F − N (points) |
|---|---|---|---|---|
| premature: issues a dependent heavy command one call early | 16.6 % [9.4, 25.8] | 12.7 % [6.7, 19.5] | 12.3 % [6.5, 18.5] | +3.9 [+0.2, +8.0] |
| spurious: issues a long-running command where none follows | 7.4 % [2.1, 14.7] | 5.2 % [0.9, 11.1] | 5.0 % [0.9, 11.1] | +2.1 [+0.2, +4.4] |
| kept: still issues the recorded action | 52.5 % [43.9, 60.6] | 57.3 % [48.0, 65.2] | 55.5 % [46.8, 63.9] | -4.8 [-7.2, -2.4] |

Output tokens per sample: F 454, N 476, C 469 (F/N 0.953).
Drift: 40 fresh controls with topic 20's seeds; kept C1 − C +3.4 [+0.0, +6.9] points; identical texts 2.

## Gate G2, probe part (prereg_2a.md)

- H1, valid early issue F − N on the >= 10 s pairs: +5.4 [+1.5, +10.8] points; needs >= 15 with the lower bound > 5: **fail**.
- Output tokens F/N 0.953 (<= 1.05): pass.
- Premature F − N +3.9 [+0.2, +8.0] (<= 5): pass.
- Spurious F − N +2.1 [+0.2, +4.4] (<= 5): pass.
- Kept F − N -4.8 [-7.2, -2.4] (>= -5): pass.
- **Probe part of G2: fails.**

For the simulator (reference C): p̂ = valid F − C on all pairs = 0.036 (>= 10 s pairs 0.046); output inflation = -0.031; spurious long commands q = 0.024.
