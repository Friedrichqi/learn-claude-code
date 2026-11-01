# Pre-registration, Stage 2a: does MiniMax-M2.7 follow a targeted front-load line? (compliance probe)

Written 2026-10-30, before any probe sample was drawn. Frozen by `prereg_2a.sha256`, which hashes:
- this file;
- `pw_probe.py`, `pw_rules.py` and `pw_serve.py`;
- `data/probe/items.json`.

`pw_serve.py` refuses an `s2a_*` stage unless these files match. It also checks topics 19 and 20's frozen lists.

## Question (H1)

The harness would show this line only to sessions it expects to hold, appended to the newest user message the way it
appends its status line (`hold_runtime.append_status_line`):

> Capacity is short and your next call will wait. Put any long-running commands that are ready (tests, builds,
> installs, downloads, searches) in this response.

**Does the line make MiniMax issue, in the current response, a long command that its trial ran only some calls
later, but which topic 20's chain test says could already have been decided here?**

- Topic 19 found that MiniMax ignores passive signals: a status line and wait tools.
- This probe tests a directive, not a passive signal.

## Items (`pw_probe.py items`; from topic 20's 58 labeled SWE-bench Pro + Terminal-Bench trials)

All items come from topic 20's main replay records (`rd_analyze.load_run("main")`). A heavy round has at least 1 s of
tools and j > 0.

- **Pair items:** 144 (request, heavy round) pairs on 141 distinct requests.
  - For each heavy round h that joins an earlier chain group k..h, there is one item per request L_j with
    k ≤ j < h.
  - **Primary:** the 76 pairs whose round h ran at least 10 s of tools.
- **Premature controls:** 61 heavy rounds that topic 20 labeled dependent on round h−1, probed at L_{h−1}.
  - Issuing c_h there would run it before the results it needs.
- **Spurious controls:** 100 requests, 50 from Pro and 50 from Terminal-Bench, drawn with seed 21.
  - In each, round j and the next three rounds all ran under 1 s of tools.
  - So any long-running command there would be invented.
- **Fresh controls:** 40 of the pair requests, drawn with seed 22, get a new no-line sample with topic 20's seeds,
  as a drift check.
- **Validity check:** `pw_probe.py check` passed before the freeze:
  - all 302 controls render to the recorded prompt tokens;
  - F and N keep each control's tokens up to its last 7 or fewer tokens;
  - F adds 33 or 38 tokens and N adds 31 or 36.

## Conditions and sampling

| condition | request |
|---|---|
| F | the recorded request, plus `pw_rules.LINE` (above) as a text block on the newest user message |
| N | the same, with `pw_rules.NEUTRAL`: "Session note: this message was logged by the harness as part of its routine bookkeeping for this run and carries no new information about the task or its files." (31 tokens; the line has 33) |
| C | no line: topic 20's stored control samples of the same request (`data/cf/main/texts.jsonl`, read only), plus the 40 fresh ones |

- **Sampling,** as topic 20 did:
  - S = 8 samples per request;
  - temperature 1.0, top_p 0.95, top_k 40;
  - max_tokens 8,192;
  - a 1-token warm-up request first, then n = 8 with seed `rd_cf.seed_of(sid, turn, j, variant, None)`.
- **Volume:** 644 sampling requests.
- **Server:** `rd_serve`'s `profile` configuration, the same server as topic 20's S3 (MiniMax-M2.7, TP=4, GPUs 0-3,
  131,072-token window, prefix caching).
- **Client:** 16 requests in flight.

## Scoring (per sample; action keys and matching from `rd_cf`)

| measure | a sample counts when |
|---|---|
| early (target level) | its tool calls reproduce at least half of c_h's action keys (`rd_cf.matches`) |
| early (loose level) | the same at the loose level (secondary) |
| **valid** | early, and every round m in [j, h) whose effects h depends on (`rd_static` eff_deps) is also issued in the same sample |
| kept | it still issues c_j, the recorded action of the request |
| premature | it issues c_h at L_{h−1} (premature controls) |
| long | it issues a long-running command class (`pw_rules.long_class`; spurious controls) |

- Each item's value is the share of its samples that count.
- **Averages** are taken over items. A pair counts once per (request, h).
- **Intervals:** 95 % from a trial-cluster bootstrap, with 2,000 draws and seed 21.

## Outcomes and the probe part of gate G2

**Primary (H1):** valid early issue, F − N, on the 76 primary pairs.

| test | passes if |
|---|---|
| H1 | F − N ≥ 15 points, and its lower bound > 5 points |
| output tokens | mean tokens per sample, F / N ≤ 1.05 |
| premature | F − N ≤ 5 points |
| spurious | long-command rate F − N ≤ 5 points |
| kept | F − N ≥ −5 points |

- **The probe part of G2 passes only if all five pass.**
- **The rest of G2** is decided in Stage 1, and frozen in `prereg_2b` before any replay: the simulator, rerun at p̂
  with the measured costs, must still meet G1's closed-loop criterion.
- **The replay runs only if all of G2 passes.**
- **The probe always runs,** by the user's choice of 10-30, whatever Stage 1 shows.

**Inputs for the simulator,** reported whatever the gate says:
- p̂ = max(0, valid F − valid C) over all 144 pairs, plus the same over the 76 primary pairs;
- output inflation = (tokens per sample F / C) − 1;
- q = max(0, long-command rate F − C) on the spurious controls.
- **Drift fallback:** if the 40 fresh controls differ from topic 20's stored ones by more than 10 points in "kept",
  then C is not a valid reference, and p̂ and q use N in place of C.

**Also reported:**
- the same measures at ≥ 1 s and at the loose level;
- splits by benchmark and by adjacency (h = j + 1 or farther);
- C next to F and N.

## Validity

**The probe is valid when:**
- every sampling request ends with status `ok`;
- GPUs 0-3 are empty afterwards;
- the frozen lists match at the start.

The runner resumes from its records, so a stopped stage is finished in a second claim. Requests that keep failing are
reported, not dropped silently.

## Budget and claim

- **Stage:** `s2a_probe`, run as `pw_serve.py run --stage s2a_probe --hours 1.5 --config profile -- pw_probe.py run
  --out main --inflight 16`.
- **Expected:** about 0.5 h of sampling plus start-up, about 3 GPU-h. At most 6 GPU-h of the topic's 40 GPU-h cap.
- **Claim:** GPUs 0-3 in the lab sheet, entered by the user as Yijiahao.
