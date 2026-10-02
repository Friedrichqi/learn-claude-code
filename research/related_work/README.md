# Related work

> Background reading behind the measured topics: literature and product surveys, one paper reading and the paper
> itself. The notes were written for the week of 2026-09-16 in `weekly_progress/091626/` and moved here
> in the 2026-09-23 cleanup, with references to moved notes updated; they are not merged into any topic README. One content change: the compaction-ladder table in
> `teammate_memory_management.md` §4 (lines 132-141) was replaced by a pointer to its one kept copy in
> `research/03_context_interventions/README.md`. Deck `weekly_progress/091626/slides.html` (bottom-right counter):
> slide 2 cites `continuum_paper_reading.md`, slide 11 cites `serving_memory_balancing.md` §6.3 behind direction 13,
> and slide 4's table of notes lists the first three notes below. "Supports" names the topic READMEs
> `research/NN_*/README.md` that each note gives background to.

| file | question it answers | date | supports |
|---|---|---|---|
| `compaction_kv_reread_related_work.md` | When a harness compacts, what does it pay in prefix-cache re-prefill and in extra rounds re-reading evicted files, and has anyone named, measured or solved that? Solution families, production reports, and where the s15 work sits (Leyline as the nearest prior work to the KV splice). | research 2026-09-12; committed 2026-09-13 (`bdc4788`) | 04 (§4 places the KV-splice experiment), 03, 02 |
| `serving_memory_balancing.md` | How does one request's KV compete with every other request's on the serving side: does batching change the usable context, how do engines, clusters and providers ration finite HBM, including agent KV held across tool pauses (§6.3), and what can a client-side harness do? | session 2026-09-12; note dated 2026-09-16; committed 2026-09-13 (`bdc4788`) | 02 (Part B), 03, 04, 05 |
| `teammate_memory_management.md` | How does a long-lived Claude Code teammate carry history, tool results and KV cache across unrelated tasks, which compaction choices exist, and where does the s15 cascade sit among them? Also records the 2026-09-11 reset to `d67ab7f` (§7). | session 2026-09-11 to 2026-09-12; note dated 2026-09-16; committed 2026-09-13 (`bdc4788`) | 03 (it wrote the compaction-ladder section; the table is kept there), 02 (Part B: teammates never compact) |
| `continuum_paper_reading.md` | What does Continuum (Li et al., arXiv:2511.02230v7) say about end-of-turn KV eviction and per-turn queueing delay for multi-turn agents, how does its KV time-to-live work, and what does it mean for s15's round latency and tool costs? | read 2026-09-16; committed 2026-09-16 (`be379b6`) | 05 (Part A), 06 (Part A), 03 |
| `continuum_reading.html` | The same reading as a styled HTML page ("Continuum's Per-Turn Bubble"), section for section | 2026-09-16 (`be379b6`) | as `continuum_paper_reading.md` |
| `Li et al. - 2026 - Continuum Efficient and Robust Multi-Turn LLM Agent Scheduling with KV Cache Time-to-Live.pdf` | The paper itself: arXiv:2511.02230v7 [cs.OS], 8 Sep 2026, submitted to PVLDB 20(1) | added 2026-09-16 (`be379b6`) | source of the two Continuum files |
| `kv_reuse_slides.html` | "KV Reuse Beyond Prefixes", a 16-slide paper-reading deck for undergraduates (revised 2026-09-29 from `kv_reuse_slides_modification.md`; cut on 2026-09-30 to presented style, where each slide keeps its figure or table plus a few short bullets and the spoken detail lives in the script). It covers the transformer block with every tensor's shape; attention and the KV cache, with a heat map of real Qwen3-8B attention; RoPE; prefix caching in vLLM and SGLang; why two separately prefilled sentences do not glue, with real joint vs separate attention maps; CacheBlend, EPIC and KVLink as one storyline, using the papers' own figures; and `10_rope_shift`'s arms on every metric, one table per model (Qwen3-32B, Qwen3-8B), with time to first token as medians. It uses the 092326 deck's skeleton. | 2026-09-27, revised 2026-09-29 (committed `d0109f1`), cut 2026-09-30 (uncommitted) | 10 (Parts A and B) |
| `kv_reuse_slides_script.md` | What to say on each of the deck's 16 slides in a 45-minute talk: a word-for-word script with stage directions, a timing plan with four checkpoints, lines to cut when running late, and short answers to likely questions. It carries the detail the slimmed slides no longer print. | 2026-09-29 (committed `d0109f1`), cues updated 2026-09-30 (uncommitted) | as `kv_reuse_slides.html` |
| `kv_reuse_slides.pptx` | The same 16 slides as a PowerPoint deck for presenter view. Each slide is the HTML slide rendered at 2560×1440 as one picture, so the text is not editable. Its speaker notes hold that slide's section of the script and the matching answers from "Likely questions", and slide 1's notes start with the timing plan. Rebuild it after changing either file. | 2026-09-30 (uncommitted) | as `kv_reuse_slides.html` |

Not a file here: the reading of **Sutradhara** (Biswas et al., arXiv:2601.12967 v1–v3, Microsoft; "tool calls
account for 30-85% of FTR latency"), how that number was produced and received, and a table of 18 independent
measurements of the share of agent latency spent in tools, with every quote checked against the source
(2026-09-29), is `research/06_tool_cost/README.md` **Part D**, kept beside the measurement that answers it
(Part C).
