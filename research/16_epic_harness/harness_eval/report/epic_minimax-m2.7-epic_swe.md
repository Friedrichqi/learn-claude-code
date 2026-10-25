## EPIC round: minimax-m2.7-epic on swe (phase e1)

Arms: epic128, epic128a1, prefix, prefix_aa, shift. Control: mean of prefix and prefix_aa per block. Blocks: 90. Infrastructure failures are excluded and rerun under the same block key.

### Primary: resolve rate, arm minus control (t-interval over task means; non-inferior if the one-sided 95% bound > −0.10; fixed order epic128a1,epic128,shift, stop at first failure)

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] | non-inferior |
|---|---|---|---|---|---|
| epic128a1 | 30 | 90 | -0.061 | [-0.130, +0.007] | no (stop) |
| epic128 | 30 | 90 | +0.017 | [-0.043, +0.076] | not tested (sequence stopped) |
| shift | 30 | 90 | -0.050 | [-0.129, +0.029] | not tested (sequence stopped) |
| prefix_aa vs prefix (A/A) | 30 | 90 | +0.033 | [-0.016, +0.083] | noise floor |

Sensitivity: the same, without the 5 blocks where any arm hit the agent timeout (the timeout binds earlier for the EPIC arms, whose calls are slower on the emulating server):

| arm | tasks | blocks | difference | 90% two-sided [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 29 | 85 | -0.063 | [-0.134, +0.008] |
| epic128 | 29 | 85 | +0.017 | [-0.045, +0.079] |
| shift | 29 | 85 | -0.075 | [-0.150, +0.001] |

### Secondary: tasks whose baseline runs snipped (28 tasks)

| arm | tasks | blocks | difference | 90% [lo, hi] |
|---|---|---|---|---|
| epic128a1 | 28 | 84 | -0.054 | [-0.125, +0.018] |
| epic128 | 28 | 84 | +0.018 | [-0.046, +0.082] |
| shift | 28 | 84 | -0.054 | [-0.139, +0.032] |

### Per arm

| arm | trials | pass@1 | pass^3 | consistent | at budget | agent timeout | lead calls | out tok |
|---|---|---|---|---|---|---|---|---|
| epic128 | 90 | 0.689 | 0.567 | 0.80 | 2.2% | 1 | 47.8 | 9,978 |
| epic128a1 | 90 | 0.611 | 0.467 | 0.77 | 2.2% | 3 | 52.2 | 10,725 |
| prefix | 90 | 0.656 | 0.500 | 0.77 | 3.3% | 1 | 49.4 | 10,228 |
| prefix_aa | 90 | 0.689 | 0.600 | 0.80 | 4.4% | 0 | 53.4 | 10,389 |
| shift | 90 | 0.622 | 0.433 | 0.67 | 2.2% | 1 | 50.7 | 10,691 |

### Against the baseline profile (`minimax-m2.7`, same tasks; another server, template and day)

| run | trials | pass@1 | minus baseline, 90% [lo, hi] (task-paired) | lead calls | at budget | agent timeout |
|---|---|---|---|---|---|---|
| baseline | 90 | 0.644 | — | 69.0 | 8.9% | 0 |
| epic128 | 90 | 0.689 | +0.044 [-0.031, +0.120] | 47.8 | 2.2% | 1 |
| epic128a1 | 90 | 0.611 | -0.033 [-0.102, +0.035] | 52.2 | 2.2% | 3 |
| prefix | 90 | 0.656 | +0.011 [-0.046, +0.069] | 49.4 | 3.3% | 1 |
| prefix_aa | 90 | 0.689 | +0.044 [-0.015, +0.104] | 53.4 | 4.4% | 0 |
| shift | 90 | 0.622 | -0.022 [-0.099, +0.054] | 50.7 | 2.2% | 1 |

### Server side (EPIC_LOG)

| arm | lead calls logged | computed / prompt | post-snip calls | fallbacks | resets | errors | probes | first-token KL | reply KL | reply top-1 | D^K reused by age (0,1,2-3,4-7,8-15,16+) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| epic128 | 4304 | 4.3% | 2442 | 0 | 1 | 0 | 389 | 0.0368 | 0.0212 | 97.34% | 0.022 / 0.108 / 0.120 / 0.121 / 0.158 / 0.246 |
| epic128a1 | 4701 | 25.3% | 2840 | 0 | 0 | 0 | 397 | 0.0171 | 0.0086 | 97.99% | 0.022 / 0.096 / — / — / — / — |
| prefix | 4445 | 48.7% | 0 | 0 | 0 | 0 | 0 | — | — | — | — / — / — / — / — / — |
| prefix_aa | 4802 | 49.0% | 0 | 0 | 0 | 0 | 0 | — | — | — | — / — / — / — / — / — |
| shift | 4566 | 3.8% | 2715 | 0 | 1 | 0 | 395 | 0.0403 | 0.0196 | 97.46% | 0.023 / 0.121 / 0.110 / 0.126 / 0.156 / 0.232 |

### First divergence from `prefix` in the same block (lead-call index; −1 = identical run)

| arm | blocks | identical | median first divergent call |
|---|---|---|---|
| epic128 | 90 | 0 | 0.0 |
| epic128a1 | 90 | 0 | 0.0 |
| prefix_aa | 90 | 0 | 0.0 |
| shift | 90 | 0 | 0.0 |

### Setup, pilots and deviations (MiniMax-M2.7)

- **Selftests** on every server start (`epic/selftest_live.py`):
  - shift equals epicinf, and both match prefix at the first token;
  - a large snip is probed on all four ranks;
  - ranks are consistent;
  - the pool is empty once every session ends.
- **Pilot p0r0**, old template, aborted: replies did not round-trip. `exact` stopped one token short of the
  previous sequence, at the `\n` after `<think>` that the append_think parser drops. KV eviction also gave the
  prefix arms no cache hits.
- **Pilot ep0**, aborted: vLLM's minimax_m2 tool parser consumes `</think>` and the whitespace before a tool call.
  The template now rebuilds `<think>\n R \n</think>\n\n T` from the parsed content.
- **Pilot ep1**: 3 tasks × 5 arms × 1 replicate, one chunk of 3 tasks, so 15 sessions at once.
  - All 15 trials were valid. Resolved: prefix 2/3, prefix_aa 1/3, shift 2/3, epic128 1/3, epic128a1 3/3. This is
    a pipeline check, not an estimate.
  - Gates that passed:
    - fallbacks + resets were 0.1 % of 899 post-snip calls (0 fallbacks, 1 reset);
    - one server record per tagged lead call (1,225 calls);
    - all four ranks overrode the same number of tokens;
    - no session was left on the server.
  - **E-0 failed narrowly.** 97.3 % of 185 pre-snip call pairs reused at least 99 % of the previous prompt and reply
    (mean 0.989), against a gate of 99 %.
    - All 5 misses sat inside the previous reply: the reasoning itself contained `\n\n\n`, and the template split
      reasoning from text at the first one.
    - Fix: tool-call turns now split at the **last** `\n\n\n`. Re-rendering the three logged misses with the new
      template reproduces the generated text exactly.
  - **Preemptions failed: 4 instead of 0.** The KV cache peaked at 99.6 % of 253,536 tokens, with 9–11 requests
    running and 4–6 waiting.
    - The connector handled each one: a resumed request whose cache hit runs past the plan's exact prefix ends in
      a clean session reset. There were 3 overshoot resets and 166 overshoots absorbed.
    - Even so, the gate is meant to hold.
  - **Cost:** 0.111 GPU-h per trial. That figure is inflated because the chunk's slowest trial (203 calls,
    1,465 s) kept the server half idle for about 12 minutes.
- **Changes for the main run e1, made before it started:**
  - KV memory: `gpu_util` raised from 0.90 to 0.93.
  - `--watermark 0.08`: 8 % of the KV blocks are kept free when admitting requests, to stop preemptions.
  - Template: the last-split fix.
  - Scheduling: one-task chunks, each replicate run as a forward and a reverse batch (`--both-ends`), 4 batches
    at once, so about 15–20 sessions are in flight.
  - Unchanged: the arms, seeds, tasks, replicates and every planned statistic.
- **e1 final gates (09:28 UTC): every gate passed in all three replicates.**
  - E-0 per replicate: 99.43 % of 1,767 pre-snip pairs, 99.22 % of 1,784 and 99.33 % of 1,800.
  - No preemptions.
  - No fallbacks; 0 resets among 13,447 post-snip calls. There were 2 decode-overflow resets in all, both on
    pre-snip calls.
  - One server record per tagged lead call, 22,814 calls in all.
  - All ranks overrode the same number of tokens.
  - No sessions were left on the server.
  - r1's counters come from the last health snapshot, because the runner stops the server the moment a round ends.
  - The interim figure below (98.8 %) was an early sample.
- **E-0 in e1, interim at 04:10 UTC: 98.8 %**, 3 misses in 252 pre-snip pairs, so the gate was still missed
  narrowly at that point.
  - The remaining misses are a case no template can resolve. The reasoning contains `\n\n\n`, and the model goes
    straight from `</think>` to a tool call.
  - The tool parser strips `</think>` together with all the whitespace around it. What remains looks the same as
    reasoning followed by a short message, so the template splits inside the reasoning.
  - Cost: the few hundred reply tokens after the split are recomputed once, at the next call. From then on the
    rendering is stable, and the model sees that part of its reasoning after `</think>`.
  - The template, parser and server are the same for every arm, so this does not bias the arm comparison.
  - The fix for a later round: the middleware swaps the rendered reply for the generated tokens when the two decode
    to the same text up to the `</think>` boundary. Doing it now would mean restarting the server mid-run.
- **Agent timeouts in e1, found at 05:15 UTC.** Three trials of 62 hit Harbor's 50-min agent timeout: 2 epic128a1
  and 1 prefix. All three were on sphinx-doc__sphinx-8548, which almost always fails, and all scored 0.
  - The saturated emulating server is slow per call: 12.5–17 s, against about 2 s in the baseline. So a long run hits
    the timeout before the 250-call cap.
  - It binds earlier for EPIC arms (at about 175 calls, against 234 for prefix), because their calls carry the
    override work and twice the shadow probes. That bias works against EPIC.
  - All runs stay in the primary analysis, as planned. The report adds a sensitivity analysis without the blocks
    where any arm timed out, and a timeout count per arm.
  - Raising the timeout mid-run would have meant restarting the round, and that would have killed the running
    trials.

