# A `wait` prompt MiniMax-M2.7 acts on when the KV cache reaches 80 %

## Context

**Ask (2026-10-30):** refine the `wait` tool prompt in `s15_integrated_harness/hold_runtime.py` so that MiniMax-M2.7
tends to call `wait` when HBM (vLLM KV-cache) use approaches 80 %. Today it almost never does.
**Constraint (user, mid-planning):** no vLLM server of my own. The GPU part shares the server another Claude session
runs.

**Baseline.** The setup `m_wait` (wait text v3 + the "wait" system paragraph + a share-view `[server]` line every
round) got:
- 2 waits in ~1,500 calls in the pilot;
- 3 in 21,081 in the main run;
- 1 in 3,627 at N=64/128.

Its thinking mentions the server twice in all of that.

**Why the current prompt fails** (checked while planning):
1. **The model never sees the HBM number.** The share line shows token counts ("KV pool 258,176 tokens; sessions
   ahead of you hold 245,476; …"). The server's own KV use (`vllm:kv_cache_usage_perc`) was ≥ 80 % behind 92 % of
   `w16_m_wait`'s lines and 97 % of `w32_m_wait`'s (p50 94-95 %), against 2 % in `w08_m_tools` (p50 51 %).
2. **There is no trigger.** "When the server is crowded" is never defined.
3. **The texts mostly warn about the cost of waiting** ("a response with only wait costs a full model step"). That
   wording was written against topic 18's Qwen wait loops. MiniMax reads it as "don't".
4. **Its thinking is short** (median 127-150 characters per call), so it never makes an implicit judgment.

**Outcome:**
- a v5 prompt with an explicit 80 % rule and a KV line, both opt-in through env vars, so the old setups stay
  byte-identical;
- a measured wait rate for MiniMax at ≥ 80 % and below 80 %, against the recorded baseline.

## Part 1 · The prompt (CPU): `hold_runtime.py`, additive only

**New knobs.** Defaults are unchanged; v1-v4, the existing paragraphs and the existing line formats stay byte for
byte.
- `S15_WAIT_TEXT=v5` selects the new tool description.
- `S15_SERVER_SECTION=wait_kv` selects the new paragraph: `_SERVER_MECHANISM` plus the rule, reusing the existing
  sentence.
- `S15_STATUS_FORMAT`:
  - unset: today's lines;
  - `kv`: the KV line;
  - `kv_cue`: the KV line, plus the cue at or above the threshold.
- `S15_WAIT_KV=0.8`: the threshold the texts and the cue state. "{kv}" is filled with `str.replace` ("80%"), so the
  old texts, which have no braces, are untouched.

**Drafts.** The probe picks among them.
- **v5 tool description:** "Pause this session for `seconds` after this response's other tool calls, so the shared
  model server can catch up. Rule: whenever the newest [server] line shows the KV cache {kv} full or more, include
  one wait call in your response next to your other tool calls; the harness runs them first and then pauses. Wait
  about 10-30 seconds, longer when many requests are waiting. Below {kv}, do not wait, and never add wait to your
  final answer. The result reports the server status after the pause."
- **`wait_kv` paragraph:** `_SERVER_MECHANISM` + " Each round ends with a [server] line that shows how full the KV
  cache is. Server rule: when that line shows {kv} or more, include a wait call in your response next to your other
  tool calls; the harness runs them first and then pauses. Below {kv}, keep working without waiting."
- **`kv` line:** `[server] KV cache 94% full; 12 requests waiting`.
- **`kv_cue` line,** at or above the threshold only: `[server] KV cache 94% full; 12 requests waiting. At 80% or more:
  add a wait call to your response.`

**Implementation:**
- `Config` gains `line_format` and `wait_kv`, and `load_config` reads them; bad values fall back to the defaults.
- `wait_tool()` and `server_section()` fill in "{kv}".
- `format_line()` builds the KV line from `kv_usage` and `waiting`. Both the stock and the share answer carry these
  fields, so the fetch view stays stock/share and `wt_server_ext` is unchanged.
- `run_wait`'s after-pause line goes through the same formatter.
- The module docstring and its env list are updated, with a dated comment on v5 in the style of v2-v4.
- `code.py` is unchanged.
- **The tool stays advertised at all times.** Tools render at the top of MiniMax's prompt, so adding `wait` only
  above 80 % would drop every cached prefix. The trigger lives in the per-round line instead.

**Tests** (`tests/test_s15_server_hold.py`; add the two new keys to `S15_KEYS`):
- the v5 and `wait_kv` texts at 0.8 ("80%") and 0.75 ("75%");
- `kv` and `kv_cue` lines at 0.79, 0.80 and 0.97;
- a missing `kv_usage` gives "n/a" and no cue;
- the after-wait line uses the format;
- v1-v4, the old paragraphs and the old line formats equal those of the frozen copy (loaded by file path);
- default-off still makes no status request.

## Part 2 · Topic 19's freeze: keep a frozen copy (the user's choice, 2026-10-30)

`hold_runtime.py` is hashed in `research/19_efficiency_signals/prereg.sha256`. Two checks use that list:
- `pw_serve.py` (topic 21) checks it before every GPU stage and refuses with exit 4 when a file changed. An edit made
  before topic 21 starts its probe stage would therefore stop the shared server from ever coming up.
- `ef_highn.py` checks it too.

Both look a listed path up inside the topic folder first.

**Step 1 of the execution, before any edit:** copy today's file byte for byte (sha256 `8cf61048…`, verified) to
`research/19_efficiency_signals/s15_integrated_harness/hold_runtime.py`.
- Then `pw_serve.py frozen` must still print "all unchanged".
- Both checks keep verifying exactly what topic 19 ran.
- No frozen file changes.
- All loaders use explicit file paths, so the copy shadows nothing.
- The step is logged in topic 19's README Changes.

## Part 3 · Compliance probe on the shared server (GPU time only on the other session's server)

**Script:** `research/19_efficiency_signals/ef_kvwait.py`, with subcommands `items`, `check`, `run` and `analyze`.
**Pre-registration:** `followups/kvwait_prereg.md` + `.sha256` (texts, items, rule, pass criteria, script hash),
frozen before `run`. `run` refuses when a hash changed.

**Reuse:**
- `rd_rounds.t19_one(meta)` gives rows and pointers for `m_wait` sessions.
- `rd_cf.Runner.variant`, through a thin subclass as `pw_probe.ProbeRunner` does:
  - it renders with `ef_render` and topic 19's template;
  - it sends a warm prefill, then n=S samples at the model's generation config;
  - it parses `<invoke>` calls and its records already list each sample's tool names;
  - it writes `texts.jsonl` (for the requested seconds) and the keys of the recorded action;
  - it resumes from its records.

**Server sharing: topic 21's probe server (the user's choice, 2026-10-30).**
- **The server:** the topic 21 session `precautionary-cpu-work-congestion` [82eb21] (it created
  `data/jobs/probe.log` at 17:00 UTC) starts it for its compliance probe: `pw_serve` stage `s2a_probe`, config
  `profile`. That config is MiniMax-M2.7 TP=4 with topic 19's append-only template, max-model-len 131072, prefix
  caching and max-num-seqs 64, which is what the probe renders for.
- **Finding it:** `run --url URL`, or `--attach`, which takes the newest live
  `research/21_precautionary_work/data/servers/*/server_info.json` (it has `ready_s` and no `t_stop`).
- **Guard,** checked before anything is sent:
  - `stage` starts with `s2a` and `config` is `profile`;
  - `/v1/models` lists MiniMaxAI/MiniMax-M2.7.

  Anything else, such as a load or timing stage (`s2b_*`, `load19`/`load16`), is refused, because the probe's
  traffic would skew it.
- **Low concurrency** (4 requests in flight, S=4), so topic 21's probe slows only a little. Its outcomes are model
  choices, not timings, so sharing does not bias it.
- **If the server stops mid-run,** the probe resumes from its records on the next `s2a` server.
- **Accounting:** topic 21's runner logs the stage's GPU-hours against its cap. I add no sheet claim, no server and
  no hour entry of my own. Both READMEs note the probe's share, roughly 25-35 server-minutes at full use, about
  1.5-2 GPU-h.
- **Step 2 of the execution:** message [82eb21] with what will run on its server: about 1,300 prompts (S=4), at
  most 4 in flight, ready by about \<time\>. The message asks it to either:
  - add the probe to its stage's client command (`bash -c 'pw_probe … & ef_kvwait.py run --url {url} …; wait'`;
    `rd_serve` fills in `{url}` inside each argument) and about 0.5 h to `--hours`; or
  - say when it launches, so `--attach` can join.

  If its stage ends before the probe is ready, the probe waits for the next `s2a` server. It never starts its own.

**Items:**
- **Source:** lead calls of counted sessions in `w16_m_wait` and `w32_m_wait` (real crowded contexts), drawn with a
  seed. Sessions that ever called `wait` are excluded.
- **Strata:** work calls (the recorded reply has tool calls) 70 %, the first call of a turn 15 %, final-answer calls
  15 %.
- **Sizes:** the screen gets 60 items, the confirm 150 fresh ones, from disjoint sessions.

**Rewriting a request.** Three places change; each replacement is asserted to happen exactly once:
- the "wait" paragraph in the system prompt is replaced by the variant's paragraph;
- the v3 wait description is replaced by the variant's;
- every `[server]` block becomes the variant's line, built from the `kv_usage` and `waiting` that the session saw.

The values come from the status server's log (`server/*/wt_ext.jsonl`, state "line"), joined in order by the line's
parsed numbers:
- in `w16_m_wait`, 5,408 of 5,408 lines match in order;
- in `w32_m_wait`, 4,978 match and 78 are out of order, so the join is a forward scan.

The newest line's KV is the factor under test.

**Conditions** (S=4 per prompt):

| name | tool text + paragraph | line | newest-line KV |
|---|---|---|---|
| `B0` | v3 + wait (as recorded) | share (as recorded) | as recorded |
| `T` | v3 + wait | `kv` | 0.62 / 0.93 |
| `R` | v5 + wait_kv | `kv` | 0.62 / 0.93 |
| `C` | v3 + wait | `kv_cue` | 0.62 / 0.93 |
| `RC` | v5 + wait_kv | `kv_cue` | 0.62 / 0.93 |

- **Screen:** 60 items × 9 prompts.
  - **Winner:** the highest wait rate at 0.93 on work items, among candidates that meet all of:
    - ≤ 10 % at 0.62;
    - wait-only replies ≤ 5 %;
    - the recorded action kept within 10 points of `B0`.
  - **Ties:** within 5 points, prefer R > RC > C > T, the least per-round text first.
  - **No winner:** if the best is below 30 % at 0.93, stop after the screen and show the thinking of the samples
    that did not wait. At most one revised screen follows, with the user.
- **Confirm:** the winner and `B0` on 150 fresh items, with the newest line at 0.60, 0.75, 0.85 and 0.95. The 0.75
  level shows how early "approaching 80 %" fires.
- **Pass:**
  - wait rate ≥ 50 % at 0.85 and at 0.95, with the bootstrap lower bound ≥ 30 %;
  - ≤ 10 % at 0.60;
  - wait-only ≤ 5 % of samples;
  - waits on final-answer items ≤ 10 %;
  - the recorded action kept within 10 points of `B0`;
  - output tokens p50 at most +15 %.
- **Statistics:** item means, with 95 % intervals from a session-cluster bootstrap (2,000 draws).
- **Tables:** `data/tables/kvwait.{md,json}`, by stratum, by level, and by the number of earlier ≥ 80 % lines in the
  context. They also report the requested seconds and a few thinking excerpts.
- **Limitation, stated in the write-up:** a replayed history holds the recorded replies, which never waited. Live,
  the model would see its own earlier waits.

## Order of execution

1. Make the frozen copy, then confirm that `pw_serve.py frozen` prints "all unchanged".
2. Message topic 21's session [82eb21].
3. Part 1: the edit and its tests; run the full suite.
4. Build `ef_kvwait.py` and its tests; run `items` and `check`; freeze `kvwait_prereg`.
5. Run on the shared `s2a` server; then `analyze`.
6. Write up the README section, the research/README row and memory. Report the screen winner, the confirm table
   against `B0`, and the env line to use.

## Files

- **Edit:**
  - `s15_integrated_harness/hold_runtime.py`;
  - `tests/test_s15_server_hold.py`.
- **New:**
  - the frozen copy `research/19_efficiency_signals/s15_integrated_harness/hold_runtime.py`;
  - `research/19_efficiency_signals/ef_kvwait.py`;
  - `research/19_efficiency_signals/test_ef_kvwait.py`;
  - `followups/kvwait_prereg.md` + `.sha256`;
  - `followups/plan_1030_kvwait.md` (a copy of this plan);
  - `data/kvwait/`.
- **Untouched:**
  - `code.py`;
  - `ef_workloads.py` and every other topic 19 file in its hash lists;
  - topic 20 and topic 21's frozen files.
- **Docs:**
  - topic 19's README: a section "A KV-threshold wait prompt (2026-10-30)" and a Changes entry;
  - the `research/README.md` row;
  - memory.

## Verification

- **CPU tests:**
  - the full test suite before and after the edit; the known baseline is 2 failures;
  - `test_ef_e2e.py` and `test_ef_analyze.py`, which load the live `hold_runtime.py`;
  - `pw_serve.py frozen` and `ef_highn`'s check report everything unchanged.
- **`ef_kvwait.py check`:**
  - `B0` renders to the recorded prompt tokens for every item;
  - each variant differs from `B0` only in the three places;
  - a variant's renders of a session's consecutive calls stay append-only;
  - the line join covers ≥ 99 % of lines.
- **Dry run:** `test_ef_kvwait.py` runs against a fake `/v1/completions`. A fake answering with a wait `<invoke>`
  scores 1; one without scores 0.
- **GPU:**
  - only the shared server, on GPUs 0-3;
  - the server identity is checked before sending;
  - nothing is started or stopped by me.

## Next step (not in this plan)

A live N=16 cell with the winner would show whether the model's own waits pull KV below 80 % and what that does to
turns/h. It needs topic 19's own server (the status extension, the pinned pool) and real crowding, so it cannot ride
on a probe server. It would take about 3.1 GPU-h and a separate claim.

**Rules:**
- no commits or pushes unless asked;
- never say "arm";
- every setup name gets a short gloss.
