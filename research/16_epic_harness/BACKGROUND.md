# Background: how agent harnesses and agentic LLMs are evaluated, and how KV-reuse fidelity is measured

Background research for `research/16_epic_harness/`, 2026-10-09.

**Questions.** What do papers, leaderboards, engineering posts and production telemetry treat as the right way to evaluate:

- (a) an agent **harness**: the loop around a model, meaning its tools, context management, compaction, memory and sub-agents;
- (b) **agentic capability** in general;
- (c) **KV-reuse fidelity**: how KV-reuse and position-independent-caching (PIC) papers measure closeness to full prefill, and the serving gain they buy.

(c) matters because this topic measures an approximate KV-reuse technique, EPIC/LegoLink, inside a harness that rewrites its own context. §8 maps the findings onto this topic's design.

## Contents

- §0 Scope and how to read
- §1 Evaluating agents means evaluating harness and model together
- §2 Benchmark catalogue (with fit for Qwen3-32B on one H100)
- §3 How harnesses are compared; cache hit rate and the append-only rules
- §4 How context-management and compaction strategies are evaluated
- §5 How KV-reuse and PIC fidelity is evaluated (EPIC in detail)
- §6 How systems papers evaluate serving for agentic workloads
- §7 Statistical methodology
- §8 What this study adopts and why; closest prior work
- References (numbered, grouped by section)

---

## 0. Scope and how to read

**Method.** Three research passes on 2026-10-08/09:

1. A web survey of agent and harness evaluation: papers, leaderboards and engineering posts.
2. A primary-source pass on EPIC and KV reuse: arXiv full text, the ICML 2025 PDF, the EPIC GitHub code and commit history, and vLLM / LMCache source.
3. A survey of this repository's earlier topics.

**What "verified" means here.**

- **✓ (re-read first-hand on 2026-10-09):**
  - Anthropic's "Demystifying evals for AI agents" [1];
  - the Complexity Trap's Table 1 and setup [17];
  - TraceLab's headline numbers [95];
  - Manus' cache rules [91];
  - the Suffix Cache Reuse README [111];
  - Leyline's abstract [176].
- **Everything else** is as the cited source reports it, read during the passes.
- **"(secondary)"** marks a number taken from an aggregator or from a page that cites the original.
- **"(unverified)"** marks a user-submitted or otherwise unconfirmed number.

**Conventions.**

- EPIC's Figure 6 values were read from the vector paths of the ICML PDF, to about ±0.005 absolute.
- arXiv IDs give the month (YYMM).
- `[n]` refers to the numbered reference list at the end.
- Repository paths such as `research/10_rope_shift/README.md` are this repo's own measurements.

**Short answer.**

- **Harness and model are evaluated together.** The field's default is:
  - outcome-graded tasks with code graders first;
  - repeated trials, reported as pass@k and pass^k;
  - cost, tokens, turns and latency reported next to success;
  - transcript reading;
  - paired statistics read against an A/A floor [1, 7, 192].
- **Scaffold and infrastructure effects are large.** At a fixed model, the harness alone moves scores by up to about 15 points [8, 9], and infrastructure alone by about 6 points [2]. So gaps under 3 points are not trusted without paired designs.
- **Compaction studies trade solve rate against cost and tokens.** The Complexity Trap finds masking ≈ summary at about half the cost [17]. Several such studies state that compaction invalidates the KV cache [45, 90, 99]. **None measures what an approximate KV reuse does to task success across repeated compactions.** That is this topic's gap.
- **KV-reuse papers measure fidelity at four levels:**
  - task score relative to full recompute;
  - KV deviation and attention deviation;
  - the next-token distribution: KL, top-1 agreement, first-token agreement, greedy common-prefix length;
  - tool-call agreement.

  They measure gains as TTFT, throughput and recompute ratio, and for agent workloads as job completion time under trace replay [139, 175, 111, 170].
- **Production treats the prefix-cache hit rate as a first-class metric.** Manus calls it "the single most important metric" [91] ✓, and TraceLab measures 95.7% token-weighted on Claude Code / Codex [95] ✓. Production also requires an append-only context and **no timestamp precise to the second at the start of the system prompt** [91] ✓. This topic applies those rules to s15 before it measures EPIC (§3.4, §8).

---

## 1. Evaluating agents means evaluating harness and model together

### 1.1 Anthropic, "Demystifying evals for AI agents" (2026-01-09) [1] ✓
- **Unit of evaluation.** "When we evaluate 'an agent,' we're evaluating the harness *and* the model working together."
- **Graders:**
  - **code-based:** string match, unit tests, static analysis, tool-call and transcript checks. Fast, cheap, objective and reproducible, but brittle.
  - **model-based:** rubrics, natural-language assertions, pairwise comparison, multi-judge consensus. Flexible, but non-deterministic, costlier, and in need of human calibration.
  - **human:** expert review, spot checks, A/B tests. The best quality, but slow and expensive; used to calibrate the model graders.
- **Scoring:** weighted, binary (all checks must pass) or hybrid. "It's often better to grade what the agent produced, not the path it took." Give partial credit on multi-step tasks.
- **pass@k vs pass^k.**
  - pass@k is the chance that at least one of k trials succeeds; use it when one success suffices.
  - pass^k is the chance that all k trials succeed; use it for consistency in customer-facing agents.
  - At 75% per trial, pass^3 = 0.75³ ≈ 42%.
- **Task bank.**
  - Start with 20–50 simple tasks drawn from real failures.
  - Make each task unambiguous: two experts must agree on pass/fail, and a reference solution is required.
  - Balance positive and negative cases.
  - A 0% pass rate over many trials usually means the task is broken.
  - Separate capability evals (low pass rate, climbing) from regression evals (near 100%).
- **Metrics beyond the outcome:**
  - transcript: turns, tool calls, total tokens;
  - latency: TTFT, output tok/s, time to last token;
  - cost per task and error rates.
- **Process.**
  - Read transcripts from many trials: "Failures should seem fair."
  - Give every trial a clean environment. Shared state (leftover files, caches) causes correlated failures; in one case Claude read git history from earlier trials.
  - Make graders resistant to bypass, watch for saturation, and layer automated evals, production monitoring, A/B tests and transcript review.

### 1.2 How sensitive results are to the harness and the infrastructure
- **Terminal-Bench 2.0** [8]:
  - Design: 89 tasks selected from 229; at least 5 runs per agent–model pair; 32,155 trials; resolution rate with a 95% CI.
  - Scaffold gaps at a fixed model:

    | model | harness scores |
    |---|---|
    | GPT-5 | Codex CLI 49.6%, OpenHands 41.5%, Terminus-2 35.2%, mini-SWE-agent 33.9% |
    | Gemini 2.5 Pro | Terminus-2 32.6%, mini-SWE-agent 26.1%, Gemini CLI 19.6%, OpenHands 15.7% |
    | Opus 4.5 | Terminus-2 57.8%, Claude Code 52.1%, OpenHands 51.9% |

  - Even so, "model choice usually matters more".
  - Turns and tokens are essentially uncorrelated with success.
- **Epoch AI** [9]:
  - changing only the scaffold moves SWE-bench Verified by up to 11 points (GPT-5) and 15 points (Kimi K2 Thinking);
  - Responses API vs ChatCompletions is worth about 3 points;
  - provider bugs move scores further.
- **Anthropic, infrastructure noise** [2]:
  - On Terminal-Bench 2.0, the gap between the best- and worst-resourced setups was 6 points (p < 0.01).
  - Infrastructure errors fell from 5.8% to 0.5% once fixed.
  - Treat leaderboard gaps under 3 points with skepticism.
- **Anthropic, multi-agent research system** [3]:
  - token usage alone explains 80% of BrowseComp variance (95% together with tool calls and model choice);
  - the LLM judge outputs a 0–1 score plus pass/fail;
  - the eval started with about 20 queries.
- **Anthropic, effective harnesses for long-running agents** [4]:
  - "compaction isn't sufficient";
  - the fixes are a feature-list JSON, a progress file and git, documented in a failure-mode table.

### 1.3 Holistic and reliability leaderboards
- **HAL, the Holistic Agent Leaderboard** (Princeton, ICLR 2026) [5, 6]:
  - Scale: 21,730 rollouts, 9 models × 9 benchmarks, about $40K. Results are shown as cost–accuracy Pareto frontiers.
  - A task-specific SWE-agent beat the generalist scaffold in 11 of 12 cases; the generalist was cheaper in 20 of 24.
  - Higher reasoning effort gave equal or lower accuracy in 21 of 36 cases.
  - Docent analysis of 1,634 transcripts found shortcuts and leaks.
  - About 2.5B tokens of traces are released.
- **HAL reliability** [7]:
  - twelve metrics;
  - consistency is the outcome variance over K = 5 runs;
  - trajectory divergence by Jensen–Shannon and normalized Levenshtein distance;
  - coefficient of variation of cost, time and number of calls.
  - Main finding: "Outcome consistency remains low across all models."

### 1.4 Failure taxonomies
- **MAST** [10]: 14 failure modes, inter-annotator κ = 0.88, with an LLM-judge annotator.
- **Terminal-Bench 2.0** [8]: an LLM-judge error taxonomy (Execution / Coherence / Verification), calibrated against humans. The appendix lists **"Context Loss"** and **"Step Repetition"**, the two modes that compaction makes likely.

---

## 2. Benchmark catalogue

The last column says whether the benchmark fits a Qwen3-32B agent on one H100; the arithmetic is in §2.5.

### 2.1 Coding and terminal
| benchmark | measures / metric | size | infrastructure / cost | Qwen3-32B or 30B-class | fit |
|---|---|---|---|---|---|
| **SWE-bench Verified / Lite / bash-only** [11–16] | Repository-level issue fixing, graded by hidden tests; % resolved. Bash-only fixes the scaffold to mini-SWE-agent. mini-SWE-agent 1.x and 2.x are not comparable: 2.x uses tool calling and leaves temperature unset. | Verified 500; Lite 300; Verified-Mini 50 (random, from HAL) | Docker. The harness needs ≥ 120 GB disk, 16 GB RAM and 8+ cores. Mini needs about 5 GB instead of 130 GB. mini-SWE-agent defaults: step_limit 250, cost_limit 3, 60 s command timeout. | SWE-agent, 250 turns, raw / masking / summary: **17.0 / 15.0 / 16.0%** non-thinking, **23.0 / 24.6 / 24.8%** thinking [17] ✓. DeepSWE: RL from Qwen3-32B, about 23% to 42.2% Pass@1, averaged over 16 runs [19, 20]. | **yes** |
| SWE-bench Verified caveat [21] | OpenAI stopped reporting Verified: 59.4% of 138 audited hard tasks were flawed, plus contamination. Matters little for a paired A/B on the same tasks. | — | — | — | — |
| **SWE-bench Pro** [22, 23] | Long-horizon multi-file fixes; Pass@1 | 1,865 total, 731 public | Docker | "Qwen-3 32B" **3.4%** (Scale leaderboard, via a search snippet) | floor effect |
| **SWE-bench-Live** [24] | Fresh issues, low contamination | 1,319 initial; Lite 300 | Docker | n/a | possible; low solve rate expected |
| **Terminal-Bench 2.0** (Harbor, Terminus-2) [8] | End-to-end terminal tasks with tests; resolution rate with 95% CI | 89 | Containers (Daytona, 32–100 in parallel). Most trials under 20 min, some up to 2 h; one used about 100M tokens. About $1–100 per model run. | Qwen3-Coder-480B 23.9%, GPT-OSS-120B 18.7%, GPT-OSS-20B 3.1%. Qwen3-32B **1.9%** (secondary, harness unknown [27]). | at the floor |
| **Terminal-Bench 1.0** [25, 26] | Same idea, earlier version | 80 (core v0.1.1), May 2025 | Docker, Terminus agent | n/a | probably at the floor |
| **Aider Polyglot** [28, 29] | Exercism code editing with 2 attempts; % correct | 225 | Docker, light | Qwen3-32B **45.8%** (whole format, no-think, vLLM bf16, 48 s/case; user-submitted, **unverified**); 40.0% thinking via OpenRouter | yes, but short contexts: rarely compacts |
| **MLE-bench** [30, 31] | Kaggle medal rate | 75 competitions; o1-preview + AIDE 16.9% | 24 h per competition, A10 GPU, large VM (third-party estimate) | n/a | no (cost) |

### 2.2 Tool use, conversation and MCP
| benchmark | measures / metric | size | infrastructure | Qwen3-32B / 30B-class | fit |
|---|---|---|---|---|---|
| **τ-bench** [32] | Tool, agent and simulated-user interaction; database end-state check; **pass^k** | retail 115, airline 50. GPT-4o pass^1 61.2% / 35.2%; pass^8 below 25% in retail. | LLM user simulator | n/a | yes |
| **τ²-bench** [33, 34] | Adds a dual-control telecom domain | airline 50, retail 115, telecom 114 (sampled from 2,285). 4 trials at temperature 0; user simulator gpt-4.1-2025-04-14. τ³ fixes more than 75 tasks, so versions are not comparable. | no Docker | Qwen3-32B (thinking) telecom **30** (secondary [35]) | yes, cheap; short contexts |
| **BFCL v3 / v4** [36–39] | Function calling. v3 multi-turn: 200 base + 800 augmented, in 200-task categories (missing parameters, missing functions, long context, composite); state- and response-based checks; more than 20 steps in a turn counts as a fail. v4 adds web search (needs SerpAPI) and memory (KV / vector / recursive summary). | v4 score weights: Agentic 40, Multi-turn 30, Live 10, Non-live 10, Hallucination 10 | — | Qwen3-32B v3 **70.3** thinking / **63.0** non-thinking; Qwen3-30B-A3B 69.1 / 58.6 [40] | yes, very fast; short contexts |
| **StableToolBench** [41, 42] | ToolBench run against a virtual API server (cache plus LLM-simulated APIs); SoPR/SoWR with a GPT-4 judge; averages over 3 runs | thousands of APIs | needs a GPT-4 judge | n/a | partial (judge cost) |
| **ACEBench** [43] | Normal / Special / Agent tool use | about 2,000 entries (secondary) | sandbox | n/a | yes |
| **AppWorld** [44] | 9 apps, 457 APIs; code as action; state-based unit tests that also check collateral damage; TGC/SGC | 750 (test-normal 168, test-challenge 417) | pip, no Docker | Qwen3-14B ReAct 25.6%, rising to 33.9% with ACON [45] | yes |
| **MCP-Bench** [46, 47] | 28 live MCP servers, 250 tools; rule-based metrics plus an o4-mini judge | 104 | live servers | Qwen3-30B-A3B 0.627 (secondary) | partial (servers are live and change) |
| **MCP-Universe** [48] | 11 servers, 133 tools, 84 execution evaluators. GPT-5 43.72%. Long context is a named failure mode. | 231 | live | n/a | partial |
| **MCPMark** [49] | CRUD-heavy tasks; pass@1 and **pass^4**; on average 16.2 turns and 17.4 tool calls | 127 | GitHub, Notion, PostgreSQL… | n/a | partial (needs accounts) |
| **MCP-Atlas** [50] | 36 servers, 220 tools; claims-based partial credit. Opus 4.5 62.3%. | 1,000 | live | n/a | partial |

### 2.3 Web, OS, general and long-horizon
| benchmark | notes | fit |
|---|---|---|
| **GAIA** [51, 52] | 466 questions (about 165 validation, 300 test); exact match; 3 levels | live web, which does not stay fixed between A/B arms |
| **AgentBench** [53] | 8 environments (OS, DB, KG, card game, puzzles, household, shopping, browsing); 29 LLMs | possible but dated |
| **WebArena** [54] | 812 tasks on self-hosted Docker sites; functional end-state checks | heavy infrastructure |
| **BrowseComp** [55] | 1,266 hard-to-find questions; OpenAI Deep Research 51.5% (secondary) | live web |
| **BrowseComp-Plus** [56–58] | 830 queries over a fixed corpus of about 100K documents (BM25 or Qwen3-Embedding index). **Qwen3-32B is the default judge.** Reports accuracy, evidence recall, search calls and calibration; a full o3 run costs about $1,000. | **yes.** Qwen3-32B scores about **10.4%** with the Qwen3-Embed-8B retriever (secondary). It is the benchmark of the context-reuse papers SCR [111] and SideQuest [106]. |
| **OSWorld** [59, 60] | 369 VM tasks, multimodal | no: Qwen3-32B is text-only |
| **TheAgentCompany** [61] | 175 tasks with checkpoints; S_partial = 0.5·partial + 0.5·S_full; self-hosted; run with OpenHands | heavy infrastructure |
| **METR time horizon** [62, 63] | 50% and 80% horizons from a logistic fit against human task time; hierarchical bootstrap CIs. Claude 3.7 Sonnet about 50 min; the horizon doubles roughly every 7 months. | not an A/B instrument |
| **Vending-Bench** [64, 65] | Net worth over runs of up to 2,000 messages. The agent sees the last 30K tokens plus memory tools. About 25M tokens and 5–10 h per run; 5 runs per model; "very high" variance; r = 0.167 between memory filling up and stagnation. VB2 covers a simulated year. | too costly and too noisy |

### 2.4 Memory and long context
- **LongMemEval** [66, 67]:
  - 500 questions over 5 abilities;
  - long-context models drop about 30%;
  - the S variant is about 115K tokens per question (secondary).
- **LoCoMo** [68]:
  - 50 dialogues: about 300 turns / 9K tokens in v1, about 600 turns / 16K tokens in the ACL version;
  - tasks: QA, event summarization, multimodal.
- **MemoryAgentBench** [69]:
  - four competencies: retrieval, test-time learning, long-range understanding, and conflict resolution / selective forgetting;
  - inputs arrive as incremental chunks.
- **Chroma "Context Rot"** [70]:
  - 18 LLMs including Qwen3; performance falls with input length;
  - a focused history of about 300 tokens beats the full 113K-token chat log.
- **Letta Context-Bench** [71]: filesystem and skills suites, with cost tracked.

### 2.5 What fits on one H100 (Qwen3-32B)
- **Architecture** [72]:
  - 64 layers; hidden size 5120; 64 Q heads, 8 KV heads, head_dim 128; intermediate size 25,600; vocabulary 151,936;
  - `max_position_embeddings` 40,960; rope_theta 1e6; no rope_scaling; 32.8B parameters.
- **KV per token:** 2 × 64 × 8 × 128 × 2 B = **256 KiB in BF16** (128 KiB in FP8), so 8 GiB per 32K tokens. The LMCache FAQ's 0.2441 GB per 1K tokens agrees [77].
- **Weights:**
  - BF16 is 65.5 GB, about 61.0 GiB [74];
  - the official FP8 checkpoint is 34.34 GB [75];
  - NVIDIA: "BF16 weights are ~64 GB and leave little KV headroom", use FP8 on 80 GB cards [76].
- **KV pool estimate** for an H100 with 81,559 MiB visible (the vLLM startup log is authoritative):

  | weights | `gpu_memory_utilization` | KV space | BF16-KV tokens |
  |---|---|---|---|
  | BF16 | 0.90 | about 7–8 GiB | about 27–33K |
  | BF16 | 0.95 | about 11–12 GiB | about 43–49K |
  | FP8 | 0.90 | about 36–37 GiB | about 145–150K |

  An FP8 KV cache doubles the token count, but FlashAttention-3 then also quantizes Q [78].
- **Context length** [73]: native 32,768. YaRN (factor 4) reaches 131,072, but it is static and "can hurt short texts".
  - The Complexity Trap ran Qwen3-32B with a 122K YaRN context on 2× H200 [17] ✓.
- **Thinking mode.** On by default; turn it off with `enable_thinking=False`, set per request or server-wide with `--default-chat-template-kwargs` [79].
- **Recommended sampling** [73]: thinking T 0.6 / top_p 0.95 / top_k 20; non-thinking T 0.7 / top_p 0.8 / top_k 20; no greedy decoding in thinking mode. The local `generation_config.json` carries T 0.6, top_k 20, top_p 0.95, which vLLM applies when a request sets none.
- **Chat template** [84]:
  - no BOS token;
  - tool calls are written as `<tool_call>`;
  - tool results are `<tool_response>` blocks inside user turns;
  - the `<think>` content of earlier assistant turns is stripped. In non-thinking mode the generation prompt ends with an empty `<think>\n\n</think>\n\n` block that a re-render drops (§3.4).
- **vLLM:**
  - Qwen3 supported from 0.8.4 [82], with 0.8.5 recommended [80];
  - `--reasoning-parser qwen3` from 0.9.0 [80];
  - `--tool-call-parser hermes` for tool calls [81];
  - the V0 engine was removed in 0.11.0 [83].
- **Re-rotation is exact.** vLLM stores **K after RoPE**: Qwen3 applies q_norm and k_norm, then the rotary embedding, then attention [86, 87]. So moving cached K by δ positions is an exact R(δ) rotation, up to bf16 rounding.
- **This topic's choice:** BF16 weights on one H100, a KV pool of about 35–50K tokens, `--max-model-len 32768`, and at most 2 concurrent sessions (plan §2).

---

## 3. How harnesses are compared; cache hit rate and the append-only rules

### 3.1 Same model, different scaffold
- **Fixed-model comparisons:** Terminal-Bench 2.0's per-harness results (§1.2) [8], HAL's task-specific vs generalist scaffolds [5], and Epoch's scaffold swaps [9].
- **SWE-bench bash-only** holds mini-SWE-agent fixed in order to isolate the model [11]. mini-SWE-agent keeps a fully linear history in which every step only appends [16]. That is maximally cache-friendly, but it has no compaction at all.
- **Ablations reported in such comparisons:**
  - solve rate;
  - cost per instance;
  - trajectory length;
  - turns and tokens (essentially uncorrelated with success in TB2 [8]);
  - error taxonomies [8, 10].

### 3.2 Production telemetry as targets for realism
- **TraceLab** (Claude Code and Codex; 4,265 sessions, 357,161 LLM steps, 432,510 tool calls; 43 users, 23 models) [95, 96] ✓:

  | measure | value |
  |---|---|
  | token-weighted prefix-cache hit rate | **95.7%** overall; **84.4%** on user-initiated steps |
  | prefill amplification | 5.3× (Table 12; Section 1 says 3.8×, which is inconsistent) |
  | sessions that compact at least once | **9.7%** (Claude 4.5%, Codex 18.4%) |
  | compactions that are tool-initiated, mid-loop | 86.5% |
  | median prefix per step | about 119K tokens overall (Claude 126,180; Codex 115,584) |
  | median tokens appended per step | about 875 overall (Claude 857; Codex 886) |

- **GitHub Copilot** (761M calls) [97] (secondary):
  - prefix caching supplies 90% of in-session tokens;
  - the hit rate drops to 55% at turn boundaries and 8% after a model switch;
  - 87% of calls are agent-initiated.

### 3.3 Context-engineering guidance from people who build agents
- **Anthropic.**
  - Claude Code's compaction preserves decisions and bugs and keeps the 5 most recent files. Tool-result clearing is called "one of the safest lightest touch forms of compaction" [88].
  - Context editing gave +29% on an internal agentic-search eval, and +39% with the memory tool; on a 100-turn web-search eval it cut token use by 84% [89].
  - The context-editing docs warn that tool-result clearing invalidates cached prefixes [90].
- **Manus** (2025-07-18) [91] ✓:
  - "the KV-cache hit rate is the single most important metric for a production-stage AI agent";
  - the input:output ratio is about 100:1, and cached input costs $0.30/MTok vs $3/MTok uncached;
  - rules: "Keep your prompt prefix stable", "Make your context append-only", "Ensure your serialization is deterministic";
  - mask tools rather than removing them: "it masks the token logits during decoding";
  - use session IDs to route requests consistently.
- **OpenAI Codex** [92, 93] (some details secondary):
  - the old prompt must be an exact prefix of the new one;
  - caching turns quadratic cost into linear;
  - a nondeterministic MCP tool order once broke caching;
  - compaction uses a `/responses/compact` endpoint.
- **Cognition** [94]: favored single-threaded agents with full shared traces, later revised to "multiple agents contribute intelligence while writes stay single-threaded".

### 3.4 Cache hit rate as a first-class metric, and the append-only rules
- **Manus warns against timestamps** [91] ✓: "A common mistake is including a timestamp—especially one precise to the second—at the beginning of the system prompt."
- **Why a timestamp is so costly:**
  - An exact-prefix cache misses from the first token that differs onward.
  - In Qwen3's chat template the system text comes first, then the tool schemas (`# Tools … <tools>…</tools>`), then every message.
  - So one changed line in the system text invalidates the tool block and the whole conversation after it: nearly the entire prompt, on every call.
- **This repository measured it on s15:**
  - **topic 11:** the per-second `Current time:` line (`s15_integrated_harness/code.py:944`) dropped the prefix-cache hit rate from 87% to **2%** (`research/11_multiuser_kv/README.md`);
  - **topic 15 F1:** 99.8% of calls were edits (`research/15_agent_serving_synthesis/README.md`).
- **The s15 system message has other variable parts too:**
  - memory records recalled every round, and the memory catalog (`:947-950`);
  - the list of connected MCP servers (`:951-953`).
- **The chat template breaks the prefix too.** Qwen3's template drops the empty think block from the previous reply when it re-renders it. Topic 13 found the reusable prefix ending exactly 4 tokens early on **99.9%** of append-only calls (`research/13_tiered_memory/RESULTS.md`). The same "reasoning-strip" edit accounts for 5.3 of SCR's 7.8 points of reuse beyond the prefix cache [111] ✓.
- **What this topic does about it** (plan §3.0):
  - it makes s15's system message static and moves its dynamic parts into appended messages;
  - it serves an append-only Qwen3 template;
  - it measures reuse before and after (experiment E-0).

---

## 4. How context-management and compaction strategies are evaluated

| work | strategies compared | benchmark / harness | metrics | key result |
|---|---|---|---|---|
| **The Complexity Trap** (JetBrains) [17, 18] ✓ | raw agent; observation masking (M = 10); LLM summary (N = 21 turns summarized, M = 10 kept, first fires at 31); hybrid (N = 43, M = W = 10) | SWE-agent; SWE-bench Verified, n = 500; 250 turns; Qwen3-32B (thinking and non-thinking), Qwen3-Coder-480B-FP8, Gemini 2.5 Flash; OpenHands probe on 50. Qwen3-32B on 2× H200 with a 122K YaRN context; agent temperature 0.8, summary temperature 0. | solve rate, $/instance, trajectory length, summary-cost share; **paired bootstrap B = 10,000**, 95% percentile CIs | Qwen3-32B non-thinking: 17.0 / 15.0 / 16.0% at $1.12 / $0.55 / $0.50. Masking about halves cost and matches summary. Summaries lengthen trajectories by 13–15% and see little cache reuse. The hybrid is 7–11% cheaper. |
| OpenHands condenser [98] | LLM-summarizing condenser vs baseline | Verified subset | solve %, cost per turn | 54% vs 53%, up to 2× cheaper per turn. Cache rebuilds are amortized because condensing fires only at a threshold. |
| SWE-agent history processors [99] | `LastNObservations`, `polling` | — | — | Masking "breaks prompt caching"; `polling` keeps between n and n+polling observations to cut cache breaks |
| Terminus-2 [100] | Proactive summarization when free tokens fall below 8,000, using summary, question and answer subagents | Terminal-Bench | — | `linear_history` records exactly what the model saw, which suits replay |
| **ACON** (ICML 2026) [45] | Optimized guidelines for history and observation compression | AppWorld, OfficeBench, 8-objective QA | accuracy, peak tokens, dependency, cost, **latency** | Peak tokens down 26–54%. States that compression "typically invalidates the existing KV-cache". Qwen3-14B on one A100: 73 s per task with no compression, 88 s with history compression, 102 s with observation compression. |
| MEM1 [101] | RL-learned constant-size internal state | retrieval and web QA, WebShop | accuracy, memory | 3.5× the performance with 3.7× less memory vs Qwen2.5-14B |
| ReSum [102] | Periodic summarization (ReSumTool-30B, from Qwen3-30B-A3B) | BrowseComp-en / zh | Pass@1 | +4.5 points over ReAct |
| Context-Folding [103] | Branch / return folding | deep research, SWE | accuracy, active context | about 10× smaller active context than ReAct |
| CWL, "Beyond Compaction" [104] | Deterministic structured eviction | 89 sequential tasks, 80M tokens | accuracy | Notes that every eviction invalidates the prefix |
| CliffCompaction [105] | Truncate-only compaction proxy | Terminal-Bench, KernelBench | cost, accuracy | up to 50% cheaper |

**KV-level and approximate-reuse analogues** (§5.4 covers them in more detail):
- **Suffix Cache Reuse** [111–113] ✓: BrowseComp-Plus accuracy plus a FLOPs proxy.
- **SideQuest** [106]:
  - FRAMES (424) and a 500-query BrowseComp-Plus subset with gpt-oss-20b;
  - accuracy drops of 2–5%;
  - SGLang throughput on one H100.
- **Online KV compaction** [107]: on BrowseComp-Plus and WideSearch, token eviction keeps most of the accuracy at 80% KV reduction.
- **UltraQuant** (4-bit KV) [108]:
  - replays Claude Code traces and runs SWE-bench Lite (100 tasks, single run);
  - 59/100 vs 62/100, which the authors call "within noise".
- **AgentKVShift** [109]: F1 against refresh ratio.
- **Baseten repeated compaction** [110]: LongHealth; reports a measurable re-compaction penalty.

**What none of these measure:** they compare compaction strategies at exact KV, or KV approximations on QA and search benchmarks. **None combines a real harness's compaction with test-graded tasks, repeated runs, and approximate KV reuse whose staleness compounds over many edits.**

`research/related_work/compaction_kv_reread_related_work.md` collects the 2026 literature that states the underlying problem: TokenPilot, the rate–distortion view, CORVUS, Leyline.

---

## 5. How KV-reuse and PIC fidelity is evaluated

### 5.1 EPIC (Hu et al., arXiv 2410.15332; ICML 2025, PMLR 267:24391–24402) [114–120]

**Versions.**
- **v1, 20 Oct 2024** [115, 116]: titled "…Position-Independent *Context* Caching…".
  - Its algorithm was **AttnLink**: recompute k tokens at each chunk boundary, with "k/2 tokens selected from the preceding chunk" and "k/2 from the following chunk".
  - It also had a KVSplit chunker and the labels AttnLink-16/20; the text wrote "Epic-16". Experiments ran on H800.
- **v2** (16 Feb 2025) and **v3** (27 May 2025) [117], and the **ICML PDF** [119], replace this with **LegoLink**.

**PIC definition** (no formal mathematics; a two-step framework):
- **Why prefix caching falls short:** it needs exact prefix matches, because "each token's KV vector depends on all preceding tokens and their absolute position IDs".
- **What PIC is:** "modular reuse of the KV vectors of immutable tokens… regardless of their prefix". The main challenge is recovering accuracy.
- **Compile step:** prefill each immutable chunk alone, "encoded with position IDs starting from zero", with no generation, and store its KV under a cache ID.
- **Link step:** retrieve and concatenate the cached KV, then recompute a subset of tokens, which always includes the never-computed query.
- **API:** explicit cache IDs through OpenAI-style context-caching APIs, e.g. `expire_cache(cache_id)`; no implicit hash matching.

**LegoLink.**
- **Which tokens are recomputed:**
  - "recomputes each chunk's first k tokens (except the first chunk)";
  - the selected set k′ is "k tokens from each chunk plus the user query";
  - the selection is **static**, fixed before inference.
- **What happens per layer:**
  - new Q, K and V are computed only for the k′ tokens;
  - K and V are completed with the cached KV of the other N − k′ tokens "at correct positions";
  - the k′ queries attend over all N keys under a causal mask;
  - cost O(kN), against CacheBlend's O(N²).
- **Choice of k:** "k ≤ 32".
  - Figure 6 sweeps LegoLink-32 / 16 / 8 / 4 / 2.
  - The system evaluation uses LegoLink-16, because "16 is the block size of vLLM and a moderate number of tokens to recompute".
  - "LegoLink-2 is sufficient to limit accuracy drops within 0–7% … compared to the default CacheBlend-15". More recomputation "yields diminishing accuracy gains".
- **The attention-sink argument.**
  - In Naive (no-recompute) attention maps, score piles onto the first tokens of each chunk, "because EPIC independently compiles each chunk with position IDs starting from zero". Those tokens "disproportionately absorb attention", the StreamingLLM sink.
  - Recomputing them lets them "recognize their non-initial positions".
  - CacheBlend-15's selections frequently include chunk-initial tokens.
- **LegoLink-0:** at compile time, prepend four dummy tokens (for example BOS) to each chunk and discard their KV. Linking then needs zero recomputation, and the paper says it "preserves accuracy remarkably well".
- **RoPE.** The paper never mentions it. The released code [124]:
  - captures K **before** RoPE at compile time (`self.hack_kv = [k.clone(), v.clone()]`, set before `rotary_emb`);
  - rotates every cached K to its global position at link time (`rotary_emb(cache_fuse_metadata['org_pos'], fake_q, old_kv[0])`);
  - computes recomputed tokens at their true positions.

  So positions are corrected for every reused token by re-rotation, and recomputation repairs the *content* of the sink tokens. Even the Naive baseline is position-corrected, which is an inference from the code path.
- **Naming:**
  - **LegoLink-k:** the first k tokens of each non-first chunk are recomputed;
  - **CacheBlend-r:** r% of all tokens are recomputed;
  - **"EPIC-k" appears in neither v3 nor the ICML version**;
  - the code calls the variants `kvlink-{1,2,4,8,16,32,64}` [123];
  - the sister paper MPIC uses "MPIC-32" [161];
  - **k = 128 appears nowhere**.

  In this repository, `epicK` means LegoLink-k applied to every reused run after a context edit (`research/10_rope_shift`, Part B).

**Setup and results.**
- **Baselines:** Full Recompute (FR), Naive, and CacheBlend-{1, 5, 10, 15, 20}, ported from [141]. Prefix caching is background only.
- **Implementation:** vLLM 0.4.1, about 2K lines of Python, on 1× A100-80GB (CUDA 12.6).
- **Models:** Mistral 7B Instruct (v0.2 in the code), Llama 3.1 8B Instruct, Yi Coder 9B Chat.
- **Data:** LongBench 2WikiMQA, MuSiQue, HotpotQA, SAMSum, MultiNews, plus Needle-in-a-Haystack; 200 cases each, 5K–20K tokens (v1). Each LongBench document is one chunk; SAMSum and Needle are cut into 512-token chunks.
- **Metrics:** F1 (QA, Needle), Rouge-L (SAMSum, MultiNews), TTFT, throughput, and the context cache ratio (CCR, the share of HBM given to the PIC cache).
- **Headline results:**
  - one request at a time: up to 3× better TTFT than CacheBlend, with "accuracy losses limited to within 7%". **This is relative to CacheBlend, not FR.**
  - concurrent serving, LegoLink-16 vs CacheBlend-15: up to 8× lower TTFT and 7× higher throughput;
  - CacheBlend-15 runs out of memory at about 35K tokens, LegoLink-16 at about 50K;
  - CacheBlend's dynamic selection takes 16.37–63.56% of its TTFT;
  - every method scores poorly on Yi Coder and MultiNews.
- **Synchronous TTFT:** FR 0.18–1.5 s, LegoLink-2 0.05–0.12 s, LegoLink-32 0.06–0.58 s. Some LegoLink outputs run "unnecessarily long", which depresses F1 and Rouge-L.

**Accuracy as a percentage of FR** (Figure 6, read from the vector paths, about ±0.005 absolute; each cell is Mistral / Llama):

| task (metric) | FR (absolute) | Naive | CB-15 | LL-32 | LL-16 | LL-2 | LL-0 |
|---|---|---|---|---|---|---|---|
| Needle (F1) | .72 / .69 | 34 / 28 | 74 / 96 | 95 / 97 | 94 / 97 | 90 / 98 | 96 / 92 |
| HotpotQA (F1) | .38 / .24 | 6 / 12 | 88 / 88 | 86 / 92 | 75 / 88 | 75 / 90 | 75 / 96 |
| MuSiQue (F1) | .20 / .145 | 9 / 15 | 88 / 83 | 80 / 83 | 68 / 76 | 79 / 88 | 86 / 90 |
| 2WikiMQA (F1) | .25 / .215 | 13 / 17 | 73 / 83 | 74 / 73 | 71 / 80 | 76 / 81 | 78 / 80 |
| SAMSum (Rouge-L, 512-token chunks) | .35 / .36 | 15 / 11 | 74 / 58 | 100 / 99 | 95 / 90 | 79 / 88 | 99 / 109 |

Against full prefill, LegoLink keeps about **70–98%** of the score on multi-document QA. The effect of k is weak and non-monotonic, except on SAMSum, where LL-32 is roughly equal to FR.

**The code** [121–129]:
- **Repository:** `DerekHJH/epic`, Apache-2.0. Commits: "Open source" on 16 Nov 2025, then "Upgrade vLLM to 0.7.0" on 2 Feb 2026.
- **Status:** the README calls it a demonstration that "focuses solely on functionality rather than performance". The production system, "similar in nature to LMCache", is unreleased.
- **Engine:** vLLM V0 with `LLM(..., gpu_memory_utilization=0.8, enforce_eager=True, max_model_len=32768)` and the XFORMERS backend.
- **Chunks:** token lists `[system] + docs + [query]` with cumulative start offsets. Each segment is compiled alone with `max_tokens=1`.
- **Linking:** `cache_fuse_metadata["kvlink"]` holds the indices to recompute: the first `recomp_num` tokens of each document plus the query. Naive recomputes only the last token.
- **Attention:** an XFormers dense additive mask that handles batch size 1 only, with `layer_idx %= 32` hard-coded.
- **Models:** only `llama.py` and `deepseek_v2.py` are modified; there is **no `qwen3.py`**.
- **Forks:** 5, with no Qwen3 and no V1 port. One fork made quantized-model changes (Apr 2026).

**Upstream and related status.**
- **vLLM RFCs:**
  - #25950, "Generalized KV cache reuse", was closed as not planned [130];
  - #44223 (June 2026) defers non-prefix reuse with RoPE correction and selective recompute [131].
- **MiniPIC** (IBM, June 2026) [134, 135]:
  - a fork of vLLM 0.15.1 that keeps K unrotated and applies RoPE inside the Triton attention kernel;
  - says EPIC can be expressed by omitting a separator token for each document's first k tokens (k rounded to the block size);
  - reports no accuracy evaluation, and tests only a Llama-3-8B-shaped model.
- **MEPIC** (Huawei, Dec 2025) [136]:
  - vLLM plus LMCache; recomputes the first KV block of each chunk; stores KV without position encoding and fuses RoPE into attention;
  - uses EPIC-16 as a baseline; Mistral-7B on Ascend 910B; no public code.
- **Comb** (the EPIC authors, Feb 2026) [137, 138]: "native PIC" with a trained encoder, for Llama-3.1-8B and DeepSeek-V2-Lite only.

### 5.2 PIC methods for RAG-style chunk reuse
| method | mechanism | how quality vs full prefill is measured | gain |
|---|---|---|---|
| **CacheBlend** (EuroSys 2025) [139–141] | Selects "high-KV-deviation" (HKVD) tokens by gradual filtering from layer 1 and recomputes about 15%; rotates reused K to the new positions | **KV deviation** (per-token absolute difference from full-prefill KV) and **attention deviation** (L2 norm of the difference between attention matrices); F1 and Rouge-L stay within about 0.02 of full recompute (2WikiMQA 200, MuSiQue 150) | TTFT 2.2–3.3× lower, throughput 2.8–5× higher; Mistral-7B, Yi-34B, Llama-70B |
| **LMCache CacheBlend for vLLM V1** [142–151] | V1 version from June 2025; Qwen3 added 9 Oct 2025. Model map: Llama / Qwen2 / Qwen3 only, otherwise `NotImplementedError`. Rejects rope scaling (so no YaRN) and requires `rotary_dim == head_size`. Fused `rotary_embedding_k_fused(old_positions, new_positions, k)`. Selects the top-k squared K difference at check layer 1. The blender runs its own layer-by-layer forward inside `start_load_kv` (layerwise mode only). Code comment: "TODO… make prefix caching and blending compatible". In-process mode is deprecated; MP mode runs `lmcache server --engine-type blend`. | inherits CacheBlend's metrics | — (topic 09 found LMCache 0.3.6 rejected the hybrid Qwen3.8-27B: `research/09_efficiency/README.md`) |
| **APE** (ICLR 2025) [152, 153] | Training-free: a shared prefix, attention temperature and a scaling factor; contexts share one position range | diagnoses misalignment by key cosine similarity, K/V norms, QK products and LogSumExp; reports "% of sequential performance" (98% RAG, 93% ICL) | 4.5× end-to-end; 28× less prefill at 128K |
| **Block-Attention** (ICLR 2025) [154] | Fine-tuned; rotates K back to 0, then forward by the new offset | task accuracy vs a full-attention model. Without fine-tuning, the 4-benchmark RAG average falls from about 66 to about 50; fine-tuning restores it. | TTFT −98.7% at 32K |
| **KVLink** [155, 156] | Stores KV without RoPE and applies global RoPE at inference; trainable link tokens | QA accuracy and Rouge-L vs a fine-tuned upper bound | TTFT −85 to −96% (Llama-3.x 1B, 3B, 8B) |
| **TurboRAG** [157, 158] | Fine-tuned Qwen2-7B; "reordered" contiguous positions beat each chunk's original "composite" positions | RGB, LongBench and OpenCompass accuracy vs naive RAG | TTFT 8.6× on average |
| **Cache-Craft** (SIGMOD 2025) [159] | Reuses chunk caches computed under a *different prefix*. Recomputes the top inter-attention tokens per chunk; the recompute fraction is CFO = α·CCI·(1−β′), where CCI (cache context impact) measures how strongly the old prefix shaped the chunk and β′ is prefix overlap. A CUDA kernel removes and reapplies RoPE. | **Rouge-L F1 or Jaccard against the full-recompute output as the oracle**, plus a user study | 1.6× throughput and 2× lower latency vs prefix caching (Llama-3 8B, 70B) |
| **KVShare** [160] | Scores tokens by query attention × ‖ΔV‖₁ and also recomputes 3 tokens per decode step; vLLM 0.4.1 | GSM8K accuracy, DROP EM, SAMSum Rouge-L, plus attention-output deviation ΔH; baselines include EPIC and CacheBlend | TTFT up to 9.39× |
| **MPIC** (EPIC group) [161] | Recomputes the first k tokens of each image plus all text ("MPIC-k"); vLLM 0.9.0, LLaVA-1.6 | GPT-judge scores on MMDU, with **no** comparison against full-prefill outputs | TTFT −54% with ≤ 13.6% score loss |

### 5.3 Learned and edit-oriented PIC (2026)
- **SemPIC** [162, 163]: Qwen3-4B/8B and Llama-3.1-8B.
  - Micro-F1:

    | model | Full Recompute | Naive PIC | KV Packet | SemPIC |
    |---|---|---|---|---|
    | Qwen3-4B | 0.59 | **0.14** | 0.49 | 0.59 |
    | Qwen3-8B | 0.60 | **0.21** | 0.45 | 0.58 |

    Qwen3 is very sensitive to naive PIC.
  - Defines a **full-relative attention error** R = D(method, Full) / D(Naive, Full), split into a boundary region [0, 0.1) and the interior.
  - EPIC is a baseline, swept from 10 to 90 recomputed tokens.
- **KV Packet** [164]: recompute-free trained adapters; F1 vs full recompute; compared against EPIC and CacheBlend.
- **CacheTune** [165]: 3.7–4.9× TTFT, "close to full recompute".
- **KVEraser** [166]: erases spans using learned steering states. Quality is near full recompute; latency rises 29.6%, against 17.6× for full recompute.

### 5.4 Agentic and multi-agent KV reuse
- **KVFlow** (NeurIPS 2025) [167, 168]: workflow-aware eviction and prefetch for exact prefixes; 1.83–2.19×; **no quality measurement**.
- **Continuum** [169, 170]:
  - pins KV with a time-to-live across tool calls; reuse stays exact;
  - reports job completion time (more than 8×) and task pass rate.
- **KVTether** [171]: message-lifecycle-aware eviction; −26.3% latency vs LMCache; no quality metric.
- **KVCOMM** (NeurIPS 2025) [172, 173]:
  - anchor-based offsets; de-rotates and re-rotates K; runs on HF;
  - MMLU, GSM8K and HumanEval vs no reuse; 67–88% reuse;
  - its headline speed numbers are internally inconsistent.
- **DroidSpeak** [174]:
  - cross-model KV reuse; per-layer sensitivity profiling, where layers that cost more than 10% F1 are "critical"; recomputes contiguous groups of layers;
  - F1, Rouge-L and code similarity vs the receiving model's full prefill;
  - prefill 1.7–3.1× faster.
- **Leyline** (arXiv 2606.01065) [175, 176]:
  - **Abstract** ✓: edit and splice directives for agent policies, plus a "closed-form RoPE-rotation correction". The splice kernel "lifts replay cache-hit by +11.2 pp and cuts latency by up to 241 ms". A **ten-line truncation rule** routed through the same interface "lifts agentic solve rate by +14.3 pp on debug-gym". That solve-rate gain comes from the truncation policy, not from the splice itself.
  - **Full text**, as read during the passes:
    - downstream K is rotated by Δ with **no recompute**; boundary recompute is proposed as future work, citing CacheBlend and EPIC;
    - fidelity metrics: first-token argmax agreement (48/50); mean common-prefix length over 128-token greedy decodes; tool-name agreement; per-step logit L2 and KL (under 0.5 nats where argmax matches);
    - bf16 rotation error of about 0.5% relative L2 for GQA.
- **Irminsul** [177]: MLA-native PIC in SGLang; measures "output consistency"; recovers about 83% of tokens beyond exact-prefix caching.
- **Suffix Cache Reuse (SCR)**, part of Meta/UW "Context Language Models" (arXiv 2609.37725) [111–113] ✓:
  - **Setup:** a monkeypatch on SGLang 0.5.16.
  - **Mechanism:**
    - it diffs the new prompt against the session's previous one and relocates up to K = 6 surviving spans, largest first;
    - each span's keys are re-rotated to the new positions and stored in session-private slots, not the shared radix tree;
    - **the edited span B′, appended tokens, and the last 16 tokens of each relocated span are recomputed** (`KVREUSE_V6_MIN_EXTEND = 16`);
    - for the hybrid Qwen3.6-27B, linear-attention layers restore a recurrent-state snapshot taken before the edit.
  - **Result:** on BrowseComp-Plus it "matches standard SGLang serving using 65.0% of its empirical prefix-reuse FLOPs". Accuracy appears only in charts.
  - **Reuse breakdown:** of the 7.8% of prompt tokens reused beyond prefix-cache hits, 5.3 points come from reasoning stripping and 2.5 from other edits.
  - **Caveats:**
    - the side buffer is 21.97 GiB for Qwen3.6-27B;
    - surviving tokens keep states computed under the old prefix; the authors call this an approximation that "can even be beneficial in some cases".
  - **Differences from EPIC:** it recomputes the *last* tokens of each run, where EPIC recomputes the *first*, and it caps the number of relocated spans.
- **Online KV compaction study** [107]: token eviction vs attention matching on agent traces; token eviction is often the more robust.
- **AgentKVShift** [109]: F1 against refresh ratio; topic 10 also cites it for ROUGE-L against full recompute.

### 5.5 Fidelity metrics in use
| metric | definition | used by |
|---|---|---|
| task score relative to full recompute | e.g. F1 or Rouge-L of method ÷ FR | EPIC [119], CacheBlend [139], APE [152], DroidSpeak [174], SemPIC [162], KV Packet [164] |
| output similarity to the recompute output | Rouge-L F1 / Jaccard of the generated text, with FR's output as the oracle | Cache-Craft [159], CacheBlend, AgentKVShift [109], topic 10 |
| KV deviation | per-token difference between the reused and the full-prefill K/V; CacheBlend's selection signal | CacheBlend [139], LMCache [146], topic 10 (layer-1 `diff_k`) |
| attention deviation | ‖A_method − A_full‖ or attention-output deviation ΔH | CacheBlend [139], KVShare [160] |
| normalized attention error | D(method, Full) / D(Naive, Full), split into boundary and interior | SemPIC [162] |
| next-token distribution | teacher-forced KL(full ‖ method), top-1 agreement, ΔNLL of the reference tokens | Leyline [175], topic 10 |
| first-token argmax agreement; greedy common-prefix length | agreement of the first decoded token; length of the shared greedy continuation | Leyline [175] |
| tool-call agreement | same tool name; BFCL-style AST match of the call multiset; invocation F1 | Leyline [175], topic 10 (`score.py`) |
| gold probes | targeted questions on reused content (verbatim copy, retention of evicted content) | topic 10 |
| task success inside an agent | solve rate on test-graded tasks | Leyline's debug-gym (truncation policy) [176], SCR's BrowseComp-Plus [111], UltraQuant's SWE-bench Lite [108] |
| serving gain | TTFT, throughput, recompute ratio, FLOPs, CCR, job completion time | all; Continuum for JCT [170] |

### 5.6 What implementing PIC on vLLM V1 runs into
- **vLLM assumes prefixes:**
  - V1 prefix lookup stops at the first miss: "A missing block implies every later block misses too (chained hashes)" [132];
  - the KV connector reports hits with prefix semantics [133];
  - FlashAttention custom masks and per-sequence causal flags are wired only for FA4 [87].
- **Extension points:**
  - `KVConnectorBase_V1` loaded via `kv_connector_module_path` [133, 197];
  - general and platform plugins [198];
  - `worker_extension_cls` plus `collective_rpc` [199].
- **Measuring deviation:**
  - `prompt_logprobs` sets `skip_reading_prefix_cache=True` by default, which disables both prefix-cache and connector reads, so set it to False explicitly [200, 201];
  - `logprobs_mode` defaults to raw logprobs [202];
  - `VLLM_BATCH_INVARIANT=1` gives deterministic outputs; dense Qwen3 is validated [85].
- **This repository's approach** (`research/10_rope_shift`, vLLM 0.29):
  - quality: a KV connector whose attention pre-hooks override reused rows layer by layer, so one forward reproduces EPIC's outputs exactly;
  - speed: TTFT timed separately with "shape-equivalent" requests.

---

## 6. How systems papers evaluate serving for agentic workloads

### 6.1 Four evaluation patterns
1. **Synthetic programs.** KVFlow: random-token workflows of fixed lengths on SGLang, with Qwen2.5-32B on H100; reports end-to-end latency only [168].
2. **Program-level Poisson replay.** Autellix replays ShareGPT, BFCL, LATS and a mixed workload, and reports program-level token latency (mean, P95, P99) and throughput at a target latency [178].
3. **Recorded agent traces with tool timing.** Continuum [170]:
   - 100 mini-SWE-agent SWE-bench traces (mean 10.9 turns, 70K tokens) and 100 BFCL-v4 traces;
   - reports average JCT, P90/P95 and throughput against vLLM, LMCache, Autellix and InferCept;
   - also runs real SWE-agents on the 500 Verified tasks and reports pass rate.
4. **Production characterization:** TraceLab [95] ✓ and Copilot [97] (§3.2).

**Earlier systems:**
- InferCept: 37–40% of forward time is recompute [179];
- Parrot: up to 11.7× [180];
- SGLang RadixAttention: LRU eviction plus cache-aware scheduling [181];
- KAIST "Cost of Dynamic Reasoning": latency variance and energy [182].

### 6.2 Replay tooling
- **AIPerf** replays TraceLab using synthesized tokens and per-session block IDs. Its docs note that prefix reuse "degrades when an agent compacts its context", and inter-turn idle gaps can be capped [183].
- **AIPerf issue #1019** proposes loading nebius OpenHands trajectories [184].
- **Mooncake trace format:** chained `hash_ids` over 512-token blocks [185].
- **EvalScope** has a multi-turn stress-test mode [186].

### 6.3 Public trajectory datasets
| dataset | size | agent / model | format |
|---|---|---|---|
| nebius/SWE-agent-trajectories [187] | 80,036 (13,389 resolved), 1.11 GB | SWE-agent, open models | parquet: `trajectory` (JSON turns), `target`, `exit_status`, `generated_patch`, `eval_logs` |
| nebius/SWE-rebench-openhands-trajectories [188] | 67,074 (32,161 resolved), 2.08 GB, 64.3 turns on average | OpenHands 0.54 + Qwen3-Coder-480B | messages with `tool_calls`, `tools`, `resolved` |
| SWE-bench/SWE-smith-trajectories [189] | 76,002 rows, 4.22 GB, MIT | SWE-agent + Claude 3.7 Sonnet | splits `tool` / `xml` / `ticks`; `messages`, `resolved`, `patch` |
| SWE-Gym/OpenHands-Sampled-Trajectories [190] | about 6K rows, 301 MB | OpenHands + gpt-4o etc. | `messages`, `tools`, `resolved` |
| JetBrains-Research/the-complexity-trap [18] | 22.5 GB tar.gz | SWE-agent + Qwen3-32B and others, with masking / summary | raw archives |
| TraceLab [96] | 4,265 sessions | Claude Code / Codex | token counts and timing only; compactions detectable |
| agent-evals/hal_traces [6] | about 2.5B tokens | 9 benchmarks | logged LLM calls |
| this repo | — | s15 | `research/08_context_inheritance/traj_ingest.py` ingests Open-SWE, SWE-rebench and SWE-smith; `hal_ingest.py` ingests HAL τ-bench / GAIA; `research/04_kv_splice/data/kv_splice/*.requests.jsonl` holds full s15 request dumps from three 50k-limit runs |

---

## 7. Statistical methodology

**Estimators** [32, 191], with n trials per task and c successes:
- pass@k = 1 − E_task[ C(n−c, k) / C(n, k) ] (Chen et al.'s unbiased estimator);
- pass^k = E_task[ C(c, k) / C(n, k) ].

**Repeat counts used in practice:**

| source | repeats |
|---|---|
| Terminal-Bench 2.0 [8] | ≥ 5 per agent–model pair |
| τ²-bench [33] | 4 |
| τ-bench [32] | ≥ 3 |
| DeepSWE [19] | 16 |
| HAL reliability [7] | K = 5 |
| Epoch [9] | 4 (8–32 is typical for static benchmarks) |
| Complexity Trap [17] ✓ | single runs, analyzed with a paired bootstrap |

**Error bars** ("Adding error bars to evals") [192, 193]:
- report the SEM, clustered by task; clustered SEs can be more than 3× the naive ones;
- resample within each question;
- use **paired differences**: per-question correlations between frontier models run 0.3–0.7;
- do a power analysis before running.

**The Complexity Trap's test** [17] ✓: a paired nonparametric bootstrap with B = 10,000, 95% percentile CIs, two-sided p = 2·min(Pr(Δ* ≥ 0), Pr(Δ* ≤ 0)), significance at p < 0.05.

**Noise sources:**
- infrastructure: about 6 points [2];
- provider and scaffold versions [9];
- **batch nondeterminism:** temperature 0 is not deterministic without batch-invariant kernels [194]; vLLM offers `VLLM_BATCH_INVARIANT=1` [85];
- benchmark flaws: up to 100% relative mis-estimation (the ABC checklist) [195];
- signal-to-noise framing (AI2) [196].

**Power arithmetic, own derivations** (standard normal quantiles):
- **Paired binary outcomes, two-sided α = 0.05, power 0.8** (McNemar):
  - n ≈ [1.96·√p_d + 0.84·√(p_d − δ²)]² / δ², with discordance p_d.
  - At p_d = 0.12: about **375** pairs for δ = 5 points, about 1,050 for 3 points, about 2,350 for 2 points.
  - The Complexity Trap's empirical paired CIs at n = 500 are about ±3.6 points [17].
- **Non-inferiority, one-sided α = 0.05, power 0.8:**
  - n = (1.645 + 0.842)²·σ_d² / δ² = 6.18·σ_d² / δ² for a continuous score, or 6.18·π_d / δ² for binary success.
  - On one GPU, task-level A/B tests can therefore rule out only regressions of several points. Smaller effects have to be read from per-call fidelity metrics, which come with thousands of paired samples.
- **A/A arm:** run the baseline against itself under the same seeds. It gives the intrinsic discordance floor, and any claimed arm difference has to exceed it.
  - HAL's reliability results [7] show why this matters for agents: outcome consistency is low even with nothing changed.

---

## 8. What this study adopts and why

### 8.1 Design choices and their sources
| choice in `research/16_epic_harness` | sources | why |
|---|---|---|
| Evaluate inside this repo's own harness (s15), harness and model together | [1, 8, 9] | The scaffold moves results by up to about 15 points, so EPIC's effect has to be measured in the system it would serve |
| **Append-only baseline first** (E-0): a static s15 system message (no timestamp; memory and MCP state moved into appended messages) and an append-only Qwen3 template that keeps the empty think block; reuse rate reported per configuration with miss attribution | Manus [91] ✓, Codex [92], TraceLab [95] ✓, topics 11, 13 and 15 (§3.4) | EPIC must be compared against a cache-friendly baseline. Otherwise its gains would be inflated by avoidable misses, and only compaction edits should remain to explain |
| **L1, KV restoration:** per-layer K/V deviation from full recompute, attention-output deviation, restoration fraction ρ_k = 1 − D_epicK / D_shift, by token age and offset from the run head | CacheBlend [139], SemPIC [162], Leyline [175] | Answers "how much of the full re-prefill KV does EPIC restore" directly. Snip slides its window every round, so EPIC's run head is the oldest retained message; the deep tail accumulates staleness, which is why age is binned |
| **L2, next round:** teacher-forced KL / top-1 / ΔNLL, first-token agreement, greedy common prefix, AST tool-call match, invocation F1, validity and grounding, gold and retention probes | topic 10 (`score.py`), Leyline [175], Cache-Craft [159], BFCL [36] | Per-call evidence with high statistical power |
| **L3, job:** deterministic code graders per template (AST, compile, diff, citation checks); pass@k / pass^k; outcome consistency; first-divergence and Levenshtein / Jensen–Shannon trajectory divergence against the A/A distribution; failure taxonomy | [1], τ-bench [32], HAL reliability [7], TB2 / MAST [8, 10] | Job-level quality and reliability, graded on outcomes with code graders first |
| **L4, system:** per call the prompt, exact-prefix, shifted-reuse, recomputed-head and fresh tokens; KV hit rate; modeled TTFT and TPOT; JCT as-run and counterfactual; GPU-seconds per task and per success | Manus [91], TraceLab [95], Continuum [170], HAL [5], topic 10 §B3.6 | Job-level performance without trusting the wall-clock of emulated arms |
| A/A arm, common random numbers (per-call seeds), paired cluster bootstrap, fixed-sequence non-inferiority (epic128 → 64 → 32 → shift) | [2, 7, 17, 192, 193] | Separates EPIC's perturbation from sampling and infrastructure noise. Non-inferiority is the right question for an approximation |
| Two regimes: R1 (snip and micro, staleness compounding over many edits) and R2 (LLM-summary compaction with a retained tail, large shifts) | Complexity Trap [17], Claude Code compaction [88], s15's own mechanisms | Covers both compaction families the user named (snip compact, LLM-call compact) |
| E-A compounded replay with one-step controls (`shift1`, `epicK_1step`) and a compaction-only variant | topic 04 (shift vs shift1), topic 10 §6 caveat | Isolates compounding staleness from trajectory divergence, with perfect pairing |
| X2: SWE-agent observation-masking replica on SWE-bench Verified-mini with EPIC serving | Complexity Trap [17] ✓, SWE-agent docs [99], HAL Verified-mini [12] | External validity on a second harness and a standard test-graded benchmark, in the setting where masking "breaks prompt caching" |

### 8.2 Closest prior work vs this study
| work | setting | edits handled | staleness | fidelity metrics | task-level evaluation |
|---|---|---|---|---|---|
| Topic 10 Part B (`research/10_rope_shift`) | 683 events from 500 live s15 runs, replayed offline; Qwen3-32B / 8B, GLM-4.7-Flash; vLLM 0.29 | snip, placeholder; summaries excluded | **one step** (call k−1's KV fresh) | next-round identity, AST, KL / top-1 / ΔNLL, gold and retention probes, validity; TTFT from shape-equivalent requests | no |
| SCR [111] ✓ | SGLang 0.5.16 patch; Qwen3.6-27B (hybrid); BrowseComp-Plus | any edit, including reasoning strips; up to 6 relocated spans; recomputes the **last 16** tokens per span | live: states computed under old prefixes persist | FLOPs proxy; accuracy in charts | yes (BrowseComp-Plus accuracy) |
| Leyline [175, 176] | Serving directives with per-architecture kernels | splice and remove spans; RoPE correction; **no** boundary recompute | — | first-token agreement, greedy common prefix, tool-name agreement, logit KL / L2; replay cache-hit, latency | debug-gym, but the solve-rate gain comes from a truncation policy |
| AgentKVShift [109] | Agent KV reuse with a position shift and a refresh budget | — | — | F1 vs refresh ratio; ROUGE-L vs recompute | not established |
| **This study** | Live s15 closed loop with an append-only baseline, plus a SWE-agent masking replica; Qwen3-32B BF16; 1× H100; vLLM 0.31 | snip, placeholders, previews, reactive summary tail; **EPIC k = 32 / 64 / 128** plus shift | **compounded** (live) plus one-step controls (replay) | L1 KV restoration, L2 next round, L4 tokens / TTFT / JCT | **yes** (deterministic graders, pass^k, A/A, non-inferiority) |

### 8.3 The gap
- **No prior work has done the combination this study does.** That combination is:
  - a real harness that compacts repeatedly;
  - approximate, position-shifted KV reuse whose staleness compounds over dozens of edits;
  - selective recompute at k = 32/64/128;
  - test-graded tasks with repeated, paired runs;
  - all of it measured against a baseline already made append-only.
- **Closest coverage:**
  - topic 10 measured one step of staleness only;
  - SCR uses a different recompute rule and a single QA-style benchmark;
  - Leyline does no recompute;
  - the compaction-strategy papers keep KV exact.

---

## References

**§0–1. Evaluating agents and harnesses**
1. Anthropic, "Demystifying evals for AI agents" (2026-01-09). https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents ✓
2. Anthropic, "Infrastructure noise" in agent evals. https://www.anthropic.com/engineering/infrastructure-noise
3. Anthropic, "How we built our multi-agent research system". https://www.anthropic.com/engineering/multi-agent-research-system
4. Anthropic, "Effective harnesses for long-running agents". https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
5. HAL: Holistic Agent Leaderboard (ICLR 2026), arXiv 2510.11977. https://arxiv.org/html/2510.11977
6. HAL traces dataset. https://huggingface.co/datasets/agent-evals/hal_traces
7. HAL agent reliability study, arXiv 2602.16666. https://arxiv.org/html/2602.16666v1
8. Terminal-Bench 2.0, arXiv 2601.11868. https://arxiv.org/html/2601.11868
9. Epoch AI, "Why benchmarking is hard". https://epoch.ai/gradient-updates/why-benchmarking-is-hard
10. MAST: multi-agent system failure taxonomy, arXiv 2503.13657. https://arxiv.org/abs/2503.13657

**§2. Benchmarks**

11. SWE-bench bash-only leaderboard. https://www.swebench.com/bash-only.html
12. HAL SWE-bench Verified Mini. https://hal.cs.princeton.edu/swebench_verified_mini
13. SWE-bench harness reference. https://swebench.com/SWE-bench/reference/harness
14. EvalScope, SWE-bench Verified Mini. https://evalscope.readthedocs.io/en/latest/benchmarks/swe_bench_verified_mini.html
15. mini-SWE-agent, SWE-bench usage. https://mini-swe-agent.com/latest/usage/swebench/
16. mini-SWE-agent repository. https://github.com/SWE-agent/mini-swe-agent
17. Lindenbauer et al., "The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management", arXiv 2508.21433. https://arxiv.org/html/2508.21433v3 ✓
18. The Complexity Trap trajectories. https://huggingface.co/datasets/JetBrains-Research/the-complexity-trap
19. DeepSWE-Preview model card. https://huggingface.co/agentica-org/DeepSWE-Preview
20. DeepSWE blog. https://together.ai/blog/deepswe
21. OpenAI, "Why we no longer evaluate SWE-bench Verified". https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
22. SWE-bench Pro, arXiv 2509.16941. https://arxiv.org/abs/2509.16941
23. Scale, SWE-bench Pro public leaderboard. https://scale.com/leaderboard/swe_bench_pro_public
24. SWE-bench-Live, arXiv 2505.23419. https://arxiv.org/abs/2505.23419
25. Terminal-Bench, about. https://www.tbench.ai/about
26. Terminal-Bench, leaderboard submission. https://www.tbench.ai/docs/submitting-to-leaderboard
27. Featherless, Terminus-Qwen3-8b page (cites Qwen3-32B on Terminal-Bench). https://featherless.ai/models/OrionLLM/Terminus-Qwen3-8b
28. Aider, Qwen3 results. https://aider.chat/2025/05/08/qwen3.html
29. DataLearner, Aider benchmark. https://www.datalearner.com/en/benchmarks/aider-benchmark
30. OpenAI, MLE-bench. https://openai.com/index/mle-bench/
31. Steel registry, MLE-bench. https://leaderboard.steel.dev/registry/benchmarks/mle-bench
32. τ-bench, arXiv 2406.12045. https://arxiv.org/html/2406.12045
33. τ²-bench, arXiv 2506.07982. https://arxiv.org/html/2506.07982
34. τ²-bench repository. https://github.com/sierra-research/tau2-bench
35. arXiv 2511.08042 (cites Qwen3-32B τ² telecom). https://arxiv.org/pdf/2511.08042
36. BFCL v3, multi-turn. https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html
37. BFCL v4, memory. https://gorilla.cs.berkeley.edu/blogs/16_bfcl_v4_memory.html
38. BFCL v4, web search. https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html
39. EvalScope, BFCL v4. https://evalscope.readthedocs.io/en/latest/third_party/bfcl_v4.html
40. Qwen3 Technical Report, arXiv 2505.09388. https://arxiv.org/html/2505.09388v1
41. StableToolBench, arXiv 2403.07714. https://arxiv.org/abs/2403.07714
42. StableToolBench repository. https://github.com/zhichengg/StableToolBench
43. ACEBench, arXiv 2501.12851. https://arxiv.org/abs/2501.12851
44. AppWorld, arXiv 2407.18901. https://arxiv.org/abs/2407.18901
45. ACON, arXiv 2510.00615. https://arxiv.org/html/2510.00615
46. MCP-Bench, arXiv 2508.20453. https://arxiv.org/abs/2508.20453
47. benchmarklist, MCP-Bench. https://benchmarklist.com/benchmarks/mcpbench/
48. MCP-Universe, arXiv 2508.14704. https://arxiv.org/abs/2508.14704
49. MCPMark, arXiv 2509.24002. https://arxiv.org/html/2509.24002v1
50. MCP-Atlas, arXiv 2602.00933. https://arxiv.org/abs/2602.00933
51. GAIA (Meta). https://ai.meta.com/research/publications/gaia-a-benchmark-for-general-ai-assistants/
52. Steel registry, GAIA. https://leaderboard.steel.dev/registry/benchmarks/gaia
53. AgentBench, arXiv 2308.03688. https://arxiv.org/abs/2308.03688
54. WebArena paper. https://webarena.dev/static/paper.pdf
55. Gigazine on BrowseComp. https://www.gigazine.net/gsc_news/en/20250411-openai-browsecomp-benchmark-browsing-agent
56. BrowseComp-Plus repository. https://github.com/texttron/BrowseComp-Plus
57. BrowseComp-Plus, arXiv 2508.06600. https://arxiv.org/abs/2508.06600
58. alphaXiv, BrowseComp-Plus overview. https://www.alphaxiv.org/overview/2508.06600v1
59. OSWorld, arXiv 2404.07972. https://arxiv.org/abs/2404.07972
60. Steel, OSWorld leaderboard. https://leaderboard.steel.dev/leaderboards/osworld.md
61. alphaXiv, TheAgentCompany overview. https://www.alphaxiv.org/overview/2412.14161v2
62. METR, "Measuring AI ability to complete long tasks", arXiv 2503.14499. https://arxiv.org/abs/2503.14499
63. Same paper, HTML. https://arxiv.org/html/2503.14499v1
64. Vending-Bench, arXiv 2502.15840. https://arxiv.org/html/2502.15840
65. Epoch, Vending-Bench 2. https://epoch.ai/benchmarks/vending-bench-2
66. LongMemEval, arXiv 2410.10813. https://arxiv.org/abs/2410.10813
67. Mastra, LongMemEval README. https://cdn.jsdelivr.net/npm/@mastra/longmemeval@1.1.10/README.md
68. LoCoMo, arXiv 2402.17753. https://arxiv.org/abs/2402.17753v1
69. MemoryAgentBench, arXiv 2507.05257. https://arxiv.org/abs/2507.05257
70. Chroma, "Context Rot". https://research.trychroma.com/context-rot
71. Letta, Context-Bench. https://letta.com/blog/context-bench

*§2.5, serving facts*

72. Qwen3-32B config.json. https://huggingface.co/Qwen/Qwen3-32B/raw/main/config.json
73. Qwen3-32B model card. https://huggingface.co/Qwen/Qwen3-32B
74. Qwen3-32B files. https://huggingface.co/Qwen/Qwen3-32B/tree/main
75. Qwen3-32B-FP8 files. https://huggingface.co/Qwen/Qwen3-32B-FP8/tree/main
76. NVIDIA Dynamo, Qwen3-32B recipe. https://docs.nvidia.com/dynamo/recipes/qwen3-32b
77. LMCache FAQ (KV size per 1K tokens). https://docs.lmcache.ai/getting_started/faq.html
78. vLLM, quantized KV cache. https://docs.vllm.ai/en/stable/features/quantization/quantized_kvcache/
79. vLLM, reasoning outputs. https://docs.vllm.ai/en/latest/features/reasoning_outputs.html
80. Qwen docs, vLLM deployment. https://qwen.readthedocs.io/en/latest/deployment/vllm.html
81. Qwen docs, function calling. https://qwen.readthedocs.io/en/latest/framework/function_call.html
82. vLLM 0.8.4 release notes. https://newreleases.io/project/pypi/vllm/release/0.8.4
83. vLLM 0.11.0 release notes. https://newreleases.io/project/pypi/vllm/release/0.11.0
84. Qwen3-32B tokenizer_config.json (chat template). https://huggingface.co/Qwen/Qwen3-32B/raw/main/tokenizer_config.json
85. vLLM, batch invariance. https://docs.vllm.ai/en/stable/features/batch_invariance/
86. vLLM `qwen3.py`. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3.py
87. vLLM V1 FlashAttention backend. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flash_attn.py

**§3. Comparing harnesses, context engineering and cache rules**

88. Anthropic, "Effective context engineering for AI agents". https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
89. Claude blog, "Managing context" (context editing and the memory tool). https://claude.com/blog/context-management
90. Claude docs, context editing. https://platform.claude.com/docs/en/build-with-claude/context-editing
91. Manus, "Context Engineering for AI Agents: Lessons from Building Manus" (2025-07-18). https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus ✓
92. OpenAI, "Unrolling the Codex agent loop". https://openai.com/index/unrolling-the-codex-agent-loop
93. InfoQ on the Codex agent loop. https://www.infoq.com/news/2026/02/codex-agent-loop
94. Cognition, multi-agents follow-up. https://cognition.ai/blog/multi-agents-working
95. TraceLab: Characterizing Coding Agent Workloads for LLM Serving, arXiv 2606.30560. https://arxiv.org/html/2606.30560 ✓
96. TraceLab repository. https://github.com/uw-syfi/TraceLab
97. Microsoft Research, "Agentic coding in the wild: characterizing GitHub Copilot at production scale". https://www.microsoft.com/en-us/research/publication/agentic-coding-in-the-wild-characterizing-github-copilot-at-production-scale/

**§4. Evaluating context management**

98. OpenHands, context condensation. https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents
99. SWE-agent, history processor configuration. https://swe-agent.com/latest/reference/history_processor_config/
100. Harbor, Terminus-2 docs. https://github.com/harbor-framework/harbor/blob/26106c6d9c66848a1440faf5724d336affac52aa/docs/content/docs/agents/terminus-2.mdx
101. MEM1, arXiv 2506.15841. https://arxiv.org/abs/2506.15841
102. ReSum, arXiv 2509.13313. https://arxiv.org/abs/2509.13313
103. Context-Folding, arXiv 2510.11967. https://arxiv.org/abs/2510.11967
104. Beyond Compaction (CWL), arXiv 2606.11213. https://arxiv.org/abs/2606.11213
105. CliffCompaction, arXiv 2609.26779. https://arxiv.org/abs/2609.26779
106. SideQuest, arXiv 2602.22603. https://arxiv.org/html/2602.22603
107. Online KV-cache compaction for agents, arXiv 2608.00902. https://arxiv.org/abs/2608.00902
108. UltraQuant, arXiv 2606.20474. https://arxiv.org/abs/2606.20474
109. AgentKVShift, arXiv 2607.21604. https://arxiv.org/abs/2607.21604
110. Baseten, repeated KV-cache compaction for long-running agents. https://labs.baseten.co/articles/repeated-kv-cache-for-long-running-agents
111. Meta/UW, Context Language Models: Suffix Cache Reuse README. https://github.com/facebookresearch/context-language-models/blob/18dc11115f50f261233c5bba7937834491e307e8/suffix_cache_reuse/README.md ✓
112. Context Language Models, arXiv 2609.37725. https://arxiv.org/abs/2609.37725
113. Wavect, Context Language Models vs compaction. https://wavect.io/blog/context-language-models-vs-compaction/

**§5. KV-reuse and PIC fidelity**

114. EPIC, arXiv 2410.15332. https://arxiv.org/abs/2410.15332
115. EPIC v1 abstract. https://arxiv.org/abs/2410.15332v1
116. EPIC v1 HTML. https://arxiv.org/html/2410.15332v1
117. EPIC v3 HTML. https://arxiv.org/html/2410.15332v3
118. EPIC, PMLR 267. https://proceedings.mlr.press/v267/hu25j.html
119. EPIC, ICML 2025 PDF. https://raw.githubusercontent.com/mlresearch/v267/main/assets/hu25j/hu25j.pdf
120. EPIC, OpenReview. https://openreview.net/forum?id=qjd3ZUiHRT
121. EPIC code. https://github.com/DerekHJH/epic
122. EPIC README. https://raw.githubusercontent.com/DerekHJH/epic/main/README.md
123. EPIC `main.py` (end-to-end eval). https://raw.githubusercontent.com/DerekHJH/epic/main/benchmarks_ours/evals/e2e/main.py
124. EPIC `llama.py`. https://raw.githubusercontent.com/DerekHJH/epic/main/vllm/model_executor/models/llama.py
125. EPIC `xformers.py`. https://raw.githubusercontent.com/DerekHJH/epic/main/vllm/attention/backends/xformers.py
126. EPIC `deepseek_v2.py`. https://raw.githubusercontent.com/DerekHJH/epic/main/vllm/model_executor/models/deepseek_v2.py
127. EPIC commits. https://api.github.com/repos/DerekHJH/epic/commits
128. EPIC forks. https://api.github.com/repos/DerekHJH/epic/forks
129. HAVibhavSharma/epic commits. https://api.github.com/repos/HAVibhavSharma/epic/commits
130. vLLM RFC #25950, generalized KV cache reuse. https://github.com/vllm-project/vllm/issues/25950
131. vLLM RFC #44223. https://github.com/vllm-project/vllm/issues/44223
132. vLLM `single_type_kv_cache_manager.py`. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/core/single_type_kv_cache_manager.py
133. vLLM KV connector `base.py`. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/distributed/kv_transfer/kv_connector/v1/base.py
134. MiniPIC, arXiv 2606.13126. https://arxiv.org/html/2606.13126v1
135. IBM vLLM MiniPIC commit. https://github.com/IBM/vllm/commit/6631ff34489612db6823b1ae3369c83eb6241853
136. MEPIC, arXiv 2512.16822. https://arxiv.org/html/2512.16822v1
137. Comb, Hugging Face paper page. https://huggingface.co/papers/2602.01519
138. Comb repository. https://github.com/shijuzhao/Comb
139. CacheBlend, arXiv 2405.16444 (HTML v3). https://arxiv.org/html/2405.16444v3
140. CacheBlend abstract. https://arxiv.org/abs/2405.16444
141. CacheBlend repository. https://github.com/YaoJiayi/CacheBlend
142. LMCache `blender.py` history. https://api.github.com/repos/LMCache/LMCache/commits?path=lmcache/v1/compute/blend/blender.py
143. LMCache `qwen3.py` history. https://api.github.com/repos/LMCache/LMCache/commits?path=lmcache/v1/compute/models/qwen3.py
144. LMCache `models/utils.py`. https://raw.githubusercontent.com/LMCache/LMCache/dev/lmcache/v1/compute/models/utils.py
145. LMCache `positional_encoding.py`. https://raw.githubusercontent.com/LMCache/LMCache/dev/lmcache/v1/compute/positional_encoding.py
146. LMCache `blender.py`. https://raw.githubusercontent.com/LMCache/LMCache/dev/lmcache/v1/compute/blend/blender.py
147. LMCache vLLM V1 adapter. https://raw.githubusercontent.com/LMCache/LMCache/dev/lmcache/integration/vllm/vllm_v1_adapter.py
148. LMCache `blend_in_process` example. https://github.com/LMCache/LMCache/tree/dev/examples/blend_in_process
149. LMCache blending docs (legacy). https://docs.lmcache.ai/kv_cache_optimizations/blending.html
150. LMCache MP mode docs. https://docs.lmcache.ai/mp/index.html
151. LMCache Qwen3 recipe. https://docs.lmcache.ai/recipes/qwen3.html
152. APE, arXiv 2502.05431. https://arxiv.org/html/2502.05431
153. APE code. https://github.com/Infini-AI-Lab/APE
154. Block-Attention, arXiv 2409.15355. https://arxiv.org/html/2409.15355v5
155. KVLink, arXiv 2502.16002. https://arxiv.org/html/2502.16002v4
156. KVLink code. https://github.com/UCSB-NLP-Chang/KVLink
157. TurboRAG, arXiv 2410.07590. https://arxiv.org/html/2410.07590
158. TurboRAG code. https://github.com/MooreThreads/TurboRAG
159. Cache-Craft, arXiv 2502.15734. https://arxiv.org/html/2502.15734
160. KVShare, arXiv 2503.16525. https://arxiv.org/html/2503.16525v2
161. MPIC, arXiv 2502.01960. https://arxiv.org/html/2502.01960v2
162. SemPIC, arXiv 2607.28069. https://arxiv.org/html/2607.28069v2
163. SemPIC code. https://github.com/jn12-29/SemPIC
164. KV Packet, arXiv 2604.13226. https://arxiv.org/abs/2604.13226
165. CacheTune, arXiv 2605.24022. https://arxiv.org/abs/2605.24022
166. KVEraser, arXiv 2606.17034. https://arxiv.org/abs/2606.17034
167. KVFlow, arXiv 2507.07400. https://arxiv.org/abs/2507.07400
168. KVFlow HTML. https://arxiv.org/html/2507.07400v1
169. Continuum, arXiv 2511.02230. https://arxiv.org/abs/2511.02230
170. Continuum HTML. https://arxiv.org/html/2511.02230v2
171. KVTether, arXiv 2609.39819. https://arxiv.org/abs/2609.39819
172. KVCOMM, arXiv 2510.12872. https://arxiv.org/html/2510.12872v2
173. KVCOMM code. https://github.com/FastMAS/KVCOMM
174. DroidSpeak, arXiv 2411.02820. https://arxiv.org/html/2411.02820v4
175. Leyline: KV Cache Directives for Agentic Inference, HTML. https://arxiv.org/html/2606.01065v1
176. Leyline abstract, arXiv 2606.01065. https://arxiv.org/abs/2606.01065 ✓
177. Irminsul, arXiv 2605.05696. https://arxiv.org/abs/2605.05696

**§6. Systems evaluation**

178. Autellix, arXiv 2502.13965. https://arxiv.org/html/2502.13965v1
179. InferCept, arXiv 2402.01869. https://arxiv.org/abs/2402.01869
180. Parrot (OSDI 2024). https://www.usenix.org/conference/osdi24/presentation/lin-chaofan
181. LMSYS, SGLang and RadixAttention. https://lmsys.org/blog/2024-01-17-sglang
182. KAIST, "The Cost of Dynamic Reasoning", arXiv 2506.04301. https://arxiv.org/abs/2506.04301
183. NVIDIA AIPerf, replaying TraceLab agentic coding traces. https://docs.nvidia.com/aiperf/tutorials/datasets-inputs/replay-trace-lab-agentic-coding-traces.md
184. AIPerf issue #1019. https://github.com/ai-dynamo/aiperf/issues/1019
185. Mooncake, arXiv 2407.00079. https://arxiv.org/abs/2407.00079
186. EvalScope, multi-turn stress test. https://evalscope.readthedocs.io/en/latest/user_guides/stress_test/multi_turn.html
187. nebius/SWE-agent-trajectories. https://huggingface.co/datasets/nebius/SWE-agent-trajectories
188. nebius/SWE-rebench-openhands-trajectories. https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories
189. SWE-bench/SWE-smith-trajectories. https://huggingface.co/datasets/SWE-bench/SWE-smith-trajectories
190. SWE-Gym/OpenHands-Sampled-Trajectories. https://huggingface.co/datasets/SWE-Gym/OpenHands-Sampled-Trajectories

**§7. Statistics**

191. Chen et al., "Evaluating Large Language Models Trained on Code" (pass@k estimator), arXiv 2107.03374. https://arxiv.org/abs/2107.03374
192. Anthropic, "A statistical approach to model evaluations". https://www.anthropic.com/research/statistical-approach-to-model-evals
193. Miller, "Adding Error Bars to Evals", arXiv 2411.00640. https://arxiv.org/abs/2411.00640
194. Simon Willison on Thinking Machines' "Defeating nondeterminism in LLM inference". https://simonwillison.net/2025/Sep/11/defeating-nondeterminism
195. Agentic Benchmark Checklist (ABC), arXiv 2507.02825. https://arxiv.org/abs/2507.02825
196. AI2, "Signal and Noise", arXiv 2508.13144. https://arxiv.org/abs/2508.13144

**§5.6. vLLM implementation notes**

197. vLLM `config/kv_transfer.py`. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/kv_transfer.py
198. vLLM plugin system. https://docs.vllm.ai/en/latest/design/plugin_system.html
199. vLLM `config/parallel.py` (`worker_extension_cls`). https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/parallel.py
200. vLLM `sampling_params.py`. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/sampling_params.py
201. vLLM V1 scheduler. https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/core/sched/scheduler.py
202. vLLM `config/model.py` (`logprobs_mode`). https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/model.py

**Repository sources cited by path:**
- `research/10_rope_shift/README.md`: Parts A and B, and §B3.6 latency.
- `research/11_multiuser_kv/README.md`: timestamp effect, 87% to 2%.
- `research/13_tiered_memory/RESULTS.md`: think-strip, 99.9%.
- `research/15_agent_serving_synthesis/README.md`: F1, plus E7 / I3 / I16 / I23.
- `research/04_kv_splice/README.md`: shift vs shift1.
- `research/09_efficiency/README.md`: LMCache and the hybrid model.
- `research/related_work/compaction_kv_reread_related_work.md`.
- `s15_integrated_harness/code.py`: lines 933–954 (system prompt), 2413–2529 (compaction).
