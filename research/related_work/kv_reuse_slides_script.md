# KV Reuse Beyond Prefixes: presenter's script

For the 16-slide deck `research/related_work/kv_reuse_slides.html` (published privately as version 5 at
https://claude.ai/artifact/8KxttT4sXWddyq3Y1DHfq4). Target: 45 minutes, then questions. Audience: undergraduates in
the group, no transformer background assumed. Written 2026-09-29.

## Before you start

- Open the deck full screen. ← and → change slides, O shows the overview, P prints. There are no animations, so
  every slide is one click.
- The published link is private. If the students should open it, share it from the page's Share menu first.
- Every measured number in the script is printed on the slide you are showing, a few of them rounded, so read it off
  the screen rather than memorizing it. The only other numbers are a worked example on slide 4.

## How to read the script

- Plain paragraphs are what you say: about 5,300 words, which is 42 minutes at 125 words per minute. The three
  questions to the room and the pointing fill the rest of the 45.
- *(Italics in brackets)* are stage directions: where to point, when to pause.
- **ASK:** marks a question for the room. Wait about ten seconds, then answer it yourself if nobody does.
- Each slide opens with its clock (start → end) and its goal. At each checkpoint, if you are more than a minute late,
  use the "If short" lines on the next few slides.
- Likely questions, with short answers, are at the end, sorted by slide.

## Timing plan

| # | slide | minutes | clock at end |
|---|---|---|---|
| 1 | Cover | 0:45 | 0:45 |
| 2 | A transformer, one token at a time | 4:00 | 4:45 |
| 3 | Attention and the KV cache | 4:45 | 9:30 |
| 4 | RoPE | 3:30 | 13:00 |
| 5 | Prefix caching in vLLM and SGLang | 3:00 | **16:00**, checkpoint: background done |
| 6 | Why gluing breaks | 3:45 | **19:45**, checkpoint: problem stated |
| 7 | CacheBlend | 3:30 | 23:15 |
| 8 | EPIC | 3:00 | 26:15 |
| 9 | KVLink | 3:00 | 29:15 |
| 10 | One storyline | 2:00 | **31:15**, checkpoint: papers done |
| 11 | Why KV reuse matters for our agent | 3:15 | 34:30 |
| 12 | Qwen3-32B, every arm on every metric | 2:30 | 37:00 |
| 13 | Qwen3-8B, every arm on every metric | 2:00 | 39:00 |
| 14 | Which tokens to re-prefill | 2:30 | 41:30 |
| 15 | What the repairs cost in time | 2:00 | **43:30**, checkpoint: results done |
| 16 | TL;DR | 1:30 | 45:00 |

---

## Part 1 · Background

### 1 · Cover

**0:00 → 0:45.** Goal: say what the talk is about and show the map.

Hi everyone. Today's paper reading is about one idea: reusing a language model's cache when the beginning of the
prompt changes.

When a model reads a prompt, it computes and keeps some numbers for every token, called keys and values. Serving
engines reuse them, but only if the next prompt starts with exactly the same tokens. Today we'll see why gluing
separately computed pieces together breaks, how three recent papers repair it, and what our own experiment found.

*(Point to the agenda.)* Four parts and a one-slide summary. You don't need any transformer background; we'll build it
up from scratch.

### 2 · A decoder-only transformer

**0:45 → 4:45.** Goal: prefill versus decode, and attention is the only place where tokens mix.

Let's start with what a language model actually does.

*(Point to pass 1 in the left figure.)* It reads a prompt, "The cat sat on the", and produces one next token: "mat".
This first pass is called **prefill**: the whole prompt goes through the model at once. The orange boxes are the
tokens computed in that pass. Prefill is efficient because all the prompt's tokens go through in parallel.

*(Follow the orange "fed back in" arrow.)* Then the new token is fed back in. In pass 2 only "mat" is new, so it's the
only orange box. The grey boxes are things kept from earlier passes, and that kept material is the KV cache, the star
of today's talk. The model outputs a period, feeds it back, and in pass 3 it outputs an end token. Each of these
one-token passes is a **decode** step, and decode can only ever produce one token per pass.

A **token** is a word piece. Qwen3, the model family we'll use all day, knows 151,936 of them, and every pass outputs
a probability for each one; the next token is picked from those.

*(Point to the right diagram.)* So what's inside "model"? A stack of identical blocks; Qwen3-8B has 36. Here's one
block, with the shape of the data written next to every step. B is the batch, the number of requests processed
together. S is the number of tokens. d_model is how many numbers describe one token: 4,096 in Qwen3-8B.

*(Trace the attention half from the top.)* The left half is **attention**. We normalize the input and multiply it by
three weight matrices to get queries, keys and values. Each is split into 32 heads of 128 numbers, and 32 times 128 is
4,096 again. RoPE, which gets its own slide, rotates the queries and keys; notice that the values skip it. Then every
query is compared with every key, with a causal mask so that no token can look ahead. That's the B by H by S by S
score matrix: one score for every pair of tokens, in every head. That S by S is why long prompts get expensive: double
the tokens and you get four times the scores. A softmax turns the scores into weights, the weights mix the values, the
heads are glued back together, and an output projection returns to 4,096 numbers per token.

*(Point to the + circle under attention.)* Then the block's input is added back. That's the **residual** path: each
token carries a running representation, and each block adds a correction to it.

*(Trace the MLP half.)* The right half is the **MLP**, which works on each token separately. Two projections, a gate
and an "up", expand to 12,288 numbers; they're multiplied element by element, projected back down to 4,096, and the
residual is added again. The output has exactly the shape of the input, which is why we can stack 36 of these.

If you remember one thing from this slide: attention is the only place where tokens exchange information. The MLP
never mixes tokens. So everything about reusing caches today is really about attention.

*If short:* skip the MLP paragraph; say "the MLP transforms each token on its own".

### 3 · Attention and the KV cache

**4:45 → 9:30.** Goal: the database analogy, causality gives the KV cache, and the question of why there is no Q
cache.

Now let's zoom in on attention, because that's where the cache comes from.

*(Point to "A soft database lookup".)* The names query, key and value come from databases. You send a query; the
database finds the entry whose key matches and returns that entry's value. Attention is a soft version of this. Token
i's query is compared with every earlier token's key, and instead of returning one value, attention returns a weighted
average of all their values, with more weight where the key matches the query better.

*(Point to the formula box.)* In symbols: each token's vector x is a row, and the weight matrices multiply from the
right. q is x times W_q, and the same for k and v. The output for token i sums, over tokens j up to i, the softmax of
q_i dot k_j, divided by root d_head, times v_j. The root d_head only keeps the scores in a sensible range.

*(Point to the heat map.)* This heat map is real: I ran Qwen3-8B on "The cat sat on the mat" and averaged the 32 heads
of its first layer. Each row is the token doing the reading, each column the token being read, and each row sums to
one. Read the second row: "cat" can only look at "The" and at itself, and it splits its attention 0.61 to 0.39. The
last row, "mat", spreads its attention over all six tokens, most of it on "the" and on itself. The upper triangle is
empty because a token never reads a later token. That's **causal** attention.

*(Point to the underlined sentence.)* Keep this warning for later. Only in this first layer are the rows and columns
really the words. From the second layer on, each position holds a hidden state that has already mixed in the tokens
before it. It's no longer just "cat"; it's "cat, having read 'The'". That's exactly why a reused cache carries its old
context, as we'll see on slide 6.

*(Point to "Causal attention gives the KV cache".)* Now the payoff. Because token i reads only tokens 1 to i, the keys
and values of earlier tokens never change when a new token arrives. So we store them: that's the **KV cache**. To
produce token i we compute only its own q, k and v, and the first i minus 1 tokens never have to be prefilled again.
It's like taking notes while you read: to continue, you look at your notes instead of re-reading the whole book.

**ASK:** *(Point to the question box.)* We cache K and V. Why don't we cache Q? *(Wait about ten seconds.)*

A query is used exactly once, to compute its own token's output, and once that output exists, the query is done. A new
token brings its own new query, compares it with the old keys, and reads the old values. So later steps only ever need
K and V.

*(Point to the right figure.)* Here's what that saves. To generate three tokens after "The cat sat", without a cache
every step recomputes every token so far: 18 token computations. With a KV cache, each step computes only the newest
token: 6. And the gap grows quickly as the text gets longer.

*(Point to the formula at the bottom right.)* The price is memory. Per token we store two tensors, K and V, for every
layer, for each of the 8 key/value heads, 128 numbers each, at 2 bytes per number. That's 144 KiB per token on
Qwen3-8B and 256 KiB on Qwen3-32B. A typical prompt in our experiment, 9,054 tokens, holds 1.24 GiB of cache on the 8B
model and 2.21 GiB on the 32B. So engines work hard to keep and reuse it.

*If short:* skip the heat-map readings and the memory arithmetic; keep the ASK.

### 4 · RoPE

**9:30 → 13:00.** Goal: position is a rotation of Q and K only, so a cached key can be moved exactly.

There's one thing attention on its own doesn't know: where tokens are. The score, q dot k, is just a dot product; it
gives the same answer whether two tokens are neighbours or a thousand tokens apart. So position has to be added.
Qwen3, like most modern models, uses **RoPE**, rotary position embedding.

*(Point to the circle.)* The idea: cut each 128-number query and key into 64 pairs, and treat each pair as an arrow in
two dimensions. Then rotate the arrow by an angle that grows with the token's position. Here's one pair of one key: at
position n it points here, and if the same key sat δ positions later, it would be turned by R of δ more. A rotation
never changes an arrow's length, only its direction, so position is added without stretching or shrinking the key.

Each pair turns at its own speed, like the hands of a clock. Pair 0 turns one radian per token, fast enough to tell
neighbours apart. Pair 63 turns about a millionth of a radian per token, so it tracks very long distances.

*(Point to "attention sees" in the formula box.)* Why rotations? The query at position m is rotated by R_m, and the
key at position n by R_n. In their dot product the two rotations combine into a single rotation by m minus n. So
attention sees only the **distance** between two tokens, not where they sit. A query at position 10 and a key at
position 7 score exactly as a query at 110 and a key at 107 would: both pairs are 3 apart. It's like giving directions
as "three houses down" instead of a street address.

*(Point to the second bullet.)* And why only queries and keys, not values? Because distance belongs in the score, and
q dot k already sees exactly that. Values are what a token passes on into other tokens' outputs. If we rotated the
values by their own positions, every output would get absolute positions stamped into it, which is exactly what we set
out to avoid.

*(Point to "move a cached key".)* Now the property this whole talk leans on. Say we computed a key at position n, and
now the same text sits at position n plus δ. We don't recompute anything: one more rotation, by δ, gives the key for
the new position. Our experiments call this **shift RoPE**.

*(Point to the bold sentence.)* So a rotation fixes the position part of a cached key exactly. Remember the words
"position part". Two slides from now we'll see what a rotation can't fix.

*If short:* skip the clock-hand paragraph.

### 5 · Prefix caching in vLLM and SGLang

**13:00 → 16:00.** Goal: engines reuse only exact prefixes, for a good reason, and that wastes unchanged text.

We have a cache, and it's expensive. So when a new request arrives, can we reuse the cache from an earlier one? The
two most popular open-source serving engines, vLLM and SGLang, both do this, and both follow the same rule: **exact
prefix only**.

*(Point to the vLLM figure.)* vLLM cuts the prompt into blocks of 16 tokens. Each block gets a hash, and each hash
includes the previous block's hash, like links in a chain. Request A is cached. Request B shares blocks 0 and 1: hits.
Block 2 was edited, so its hash changes. Now look at block 3. Its tokens are exactly the same as before, but its hash
includes block 2's, so it changes too, and block 3 misses. Everything after the first edited block is prefilled again.

*(Point to the SGLang tree.)* SGLang stores cached prompts as a tree. A new request walks down from the root as far as
its tokens match. Request B shares the system prompt and tools, but its first turn was edited, so it branches off
there, and everything from the branch point on is prefilled.

*(Wave at the table.)* The bookkeeping differs, hashed blocks versus a token-level tree, with different eviction and
scheduling. The rule is the same.

Why so strict? Go back to slide 3: every token's keys and values depend on all the tokens before it. Change one early
token and, strictly speaking, every later key and value is different. The engines aren't being lazy; they're being
correct. And exact reuse pays off: the SGLang paper reports up to 6.4 times higher throughput from it. When prompts
only grow at the end, like a shared system prompt or a chat that appends messages, it works beautifully.

*(Point to the bold sentence.)* Here's the catch. Reuse stops at the first token that differs, even when everything
after it is unchanged. Picture deleting one old message near the start of a long chat. The thousands of tokens after
it haven't changed at all, they've only moved, yet the engine throws their cache away. Reusing that unchanged text is
what the rest of this talk is about.

*If short:* skip the SGLang tree and the table.

**Checkpoint: 16:00.**

---

## Part 2 · Why gluing cached pieces breaks

### 6 · Two separately prefilled sentences

**16:00 → 19:45.** Goal: two reasons, missing cross-attention and positions baked in above layer 0.

Let's try the simplest possible version of "reuse the unchanged text" and watch it break.

Take two sentences. A: "Alice owns a cat." B: "Her cat is black." Suppose we prefilled each one separately and cached
it. Can we glue the two caches together and pretend we prefilled "Alice owns a cat. Her cat is black." in one go?
There are two reasons we can't.

*(Point to the left heat map.)* **Reason one: B never saw A.** This is real attention from Qwen3-8B, layer 3.
Prefilled together, B's tokens put 86% of their attention on A. That makes sense: to understand "Her", you need Alice.

*(Point to the red box in the right heat map.)* Prefilled apart, this whole block was never computed: B's cache has
never seen A. And the attention that should have gone to A goes somewhere else, to "Her", which was the first token of
its own little sequence. "Her" now gets 61% of B's attention, against 5% when prefilled together. Why there? Every row
has to sum to one, so the softmax must put the weight somewhere. With A missing, much of that weight lands on the
first token.

*(Point to the caption.)* It's not just layer 3. In every one of the 36 layers, prefilled together, B gives 47 to 95%
of its attention to A. Prefilled apart, zero. And "Her" soaks up 25 to 94% of B's attention instead of 1 to 11%.
Remember this first-token effect; it's the key to the second paper.

*(Point to the grid on the right.)* **Reason two: position.** Rows are layers, columns are tokens. B was cached at
positions 0 to 4 but now sits at 5 to 9. At layer 0, the blue row, we can fix that exactly. A layer-0 key is just the
token's embedding times W_k, rotated by its position, so re-rotating by δ = 5 gives exactly the key a joint prefill
would compute. We checked this on real Qwen3 keys: the largest relative error was 5.5 times 10 to the minus 4, against
0.094 without the rotation.

*(Point to the red rows.)* But from layer 1 up, every key and value came out of attention and MLP layers, which are
nonlinear, and those layers ran with B at its old positions and without A. "Her" was computed as the start of a
sequence. Even B's "cat" is stale: its layer-1 key came from a hidden state that had read "Her" but never "Alice owns
a cat". That's baked into the numbers now, and no rotation can take it out. This is slide 3's warning coming true.

*(Point to the bold line.)* So only a full prefill gives the true cache. All three papers ask the same question: which
few tokens do we re-prefill to get close to it?

**Checkpoint: 19:45.**

---

## Part 3 · Three papers, one storyline

### 7 · CacheBlend (EuroSys 2025)

**19:45 → 23:15.** Goal: sparse attention means only a few tokens need fixing, and they are found by measuring
deviation.

Paper one: CacheBlend, from EuroSys 2025.

*(Point to Figure 3.)* Their setting is RAG, retrieval-augmented generation: retrieve a few chunks of text and glue
them in front of a question. Here, chunk 1 says Messi scored 13 World Cup goals, chunk 2 says Ronaldo scored 8, and
the question asks who scored more. With a full recompute, panel b, the model answers correctly. If you glue each
chunk's own cached KV, panel c, it fails: chunk 2 never attended to chunk 1, so the model can't compare the two
numbers. That's our Alice problem again. And prefix caching only helps with the first chunk.

*(Point to "Key observations".)* Two observations make a cheap fix possible. First, attention is sparse: a few tokens
carry most of the importance, so only a few tokens' caches are badly damaged by gluing. Second, a token that deviates
most at one layer tends to deviate most at the next. So you can find the damaged tokens early and keep that choice for
the rest of the model.

*(Point to "Method".)* The method follows directly. Rotate every reused key to its new position, which is our shift
RoPE. At the first layer, re-prefill every token and compare the fresh keys and values with the cached ones. Deviation
just means how different the fresh ones are from the cached ones; since the keys were already rotated, what remains is
the context damage from slide 6. Keep the top 15% that deviate most; the paper calls them high-KV-deviation, or HKVD,
tokens. In later layers, re-prefill only those, narrowing the set as you go.

There's a neat systems trick too. Caches often live in CPU memory or on an SSD. Recomputing 15% of a layer takes about
3 ms, while loading the next layer's cache from an NVMe drive takes 16 ms, so the recompute hides behind the loading.

*(Point to the results under the figure.)* They report 2.2 to 3.3 times lower time to first token than full recompute,
2.8 to 5 times higher throughput, and quality within 0.01 to 0.03 of full recompute.

*(Point to "In our experiment".)* We ran two versions, re-prefilling 5% or 15% of the moved tokens. I'll define our
tests in part 4, but in short: on Qwen3-32B both fully restore the model's ability to copy code exactly, while on
Qwen3-8B they close only 45 to 66% of a second problem, knowing that a file has been removed.

*If short:* skip the NVMe paragraph.

### 8 · EPIC (ICML 2025)

**23:15 → 26:15.** Goal: attention sinks say which few tokens matter, the first ones of each chunk, so no search is
needed.

Paper two: EPIC, from ICML 2025. Same problem as CacheBlend: chunks cached separately, each starting at position 0,
then glued before a question. But EPIC starts from a different observation: **attention sinks**.

*(Point to "Key observation".)* Language models give a surprisingly large share of attention to the first tokens of a
sequence. During generation every later token can see those first tokens, so the model learns to park attention there
when it has nowhere better to put it. That's harmless in a normal prompt. But a chunk cached on its own also starts
with "first tokens", so after gluing, every chunk start is a fake sink that pulls attention away from the content.

*(Point to the naive row of Figure 4.)* You can see it in their figure. In the naive glued version, attention piles up
on each chunk's first token, the bright columns at positions 0, 16, 27 and 42, and the answer is wrong. We saw the
same thing on slide 6, where "Her" took 25 to 94% of B's attention when B was prefilled alone.

**ASK:** If the damage concentrates on each chunk's first few tokens, what's the cheapest fix? *(Pause.)*

Re-prefill just those. *(Point to the LegoLink row.)* That's their method, LegoLink: re-prefill the first k tokens of
every chunk except the first, with k at most 32, so those tokens see the real context. They skip the first chunk
because it really does sit at the start of the prompt, so its sink is genuine. The attention frees up and the answer
is right.

The big advantage is that the set is fixed in advance, with no measuring pass. CacheBlend must run a full layer over
every token just to decide what to recompute, and EPIC reports that this selection takes 16 to 64% of CacheBlend's
time to first token. *(Point to the results.)* EPIC reports up to 8 times lower time to first token than existing
systems and up to 3 times faster than CacheBlend, with accuracy within 7% of CacheBlend's, already with k equal to 2.

A naming warning: EPIC calls its two steps KVCompile and KVLink. That's not the KVLink paper, which comes next.

*(Point to "In our experiment".)* Our versions re-prefill the first 32 or 128 tokens of each moved block, 4.4% or
10.8% of the moved tokens. EPIC 32 restores the 32B model's exact copying, and EPIC 128 brings the 8B model's "file is
gone" answers back to 55.3%, against 57.5% for a full prefill.

*If short:* skip the paragraph on selection cost.

### 9 · KVLink (NeurIPS 2025)

**26:15 → 29:15.** Goal: first tokens still carry meaning, so trained link tokens take over the reading, at the price
of new weights.

Paper three: KVLink, from NeurIPS 2025. Same problem again: documents cached one by one, then glued before a question.

*(Point to "Key observation".)* Here's how I connect it to EPIC; this link is my reading, not the paper's own words.
EPIC re-prefills each chunk's first tokens because they soak up attention. But those tokens are still real words with
their own meaning, and when you re-prefill them, they mostly reconnect to the words related to them, not to everything
that came before. So why not add tokens that mean nothing at all, trained for one job: reading every earlier token and
carrying the documents' combined meaning forward?

*(Point to the figure.)* That's the method. After each document, KVLink appends five special **link tokens**. In the
figure, link tokens are outlined; green means "may attend" and grey means "masked". Link 1 reads document a. Link 2
reads document a, link 1 and document b. Link 3 reads everything before it. The document tokens only ever read their
own document. That's the trick: a document never looks outside itself, so its cache can be computed once, alone, and
reused anywhere. Only the link tokens are computed fresh for each request, and they do all the reading across
documents. Think of them as a summary card slipped in after each document, written by someone who has read everything
so far. Positions are handled by storing each document's cache unrotated and rotating it into place at inference.

*(Point to "Fine-tuning".)* The model doesn't know how to use link tokens out of the box, so they fine-tune the whole
model: 6,000 steps on question answering plus general data, on Llama models from 1B to 8B.

*(Point to the results.)* The payoff: about 4% higher QA accuracy on average than prior methods, across 7 datasets. On
Llama-3.2-1B with Natural Questions it scores 45.0, against 39.0 for Block-Attention and 25.7 for CacheBlend. And it
cuts time to first token by 96% at a 5,000-token context.

**ASK:** Sounds perfect. What's the catch for us? *(Pause.)*

The model's weights change. If a server has to run an existing model unmodified, it can't use KVLink. That's why our
experiment doesn't test it.

*If short:* skip the fine-tuning paragraph.

### 10 · One storyline

**29:15 → 31:15.** Goal: the three papers as one chain of observations.

Let's put the three papers side by side, because together they tell one story.

*(Point to the three cards, left to right.)* CacheBlend: attention is sparse, so only a few tokens carry the damage of
gluing. Find them by measuring deviation, anew for every request, and re-prefill about 15%.

EPIC: attention sinks tell us which few tokens they are: each chunk's first tokens. So re-prefill the first k of each
chunk, a rule fixed in advance with nothing to search.

KVLink: re-prefilled first tokens still carry their own meaning, so hand the job to dedicated link tokens that mean
nothing and are trained to read and summarize everything before them. That costs a fine-tune.

Notice how each paper answers the question the previous one leaves open. CacheBlend asks which tokens are damaged.
EPIC answers: the first ones. KVLink asks whether re-prefilling real first tokens is even the right tool.

*(Point to the table.)* The table says the same thing row by row. Selection cost goes from a full layer per request,
to nothing, to a one-time fine-tune, and only KVLink changes the model.

*(Point to "How to read the gains".)* One caution: each paper reports its own numbers, on its own models, datasets and
hardware, so the gains row is not a head-to-head comparison. That's exactly why we ran our own. Part 4 tests the two
training-free rules, CacheBlend's and EPIC's, on real agent traffic; in our tables they are the arms CacheBlend 5% and
15%, and EPIC 32 and 128.

**Checkpoint: 31:15.**

---

## Part 4 · Our experiment

### 11 · Why KV reuse matters for our agent

**31:15 → 34:30.** Goal: compaction moves unchanged text every round; the setup, the three questions and the arms.

So why does this matter to us? Our agent harness runs a coding agent on real repositories, one tool call after
another, and its history keeps growing.

*(Point to "the problem".)* When the history reaches its budget, the harness compacts it: old tool results become
short notes, and old messages get archived. Once past that point, it compacts almost every round. Every compaction
edits text early in the prompt, so every token after the first edit moves, and prefix caching re-prefills all of it,
even though most of it didn't change.

*(Point to the figure.)* Here's one event, with the averages over all events. Call k minus 1 is the prompt before the
compaction; call k is the prompt after it. The exact prefix, 3,191 tokens, is loaded as it is. Then a file read that
this step evicted is replaced by a short note. Blocks 1 to 3 are unchanged text that simply moved left by δ: 4,648
tokens on average. And 1,215 tokens are genuinely new: the note, the new turn and the tail.

*(Point to "the prize".)* Those 4,648 moved tokens are the prize. Reusing them skips 79.6% of what prefix caching
re-prefills. On Qwen3-32B the next request's first token arrives in 90 ms, against 765 ms with prefix caching and
1,097 ms with a full prefill.

*(Point to "the runs" and "3 questions".)* Our data: 500 live runs of real tasks on 10 Python codebases, giving 683
compaction events. Qwen3-32B produced the runs, and Qwen3-8B replays the same events. For each event we ask three
questions. **Natural**: does the real next request come out the same as with a full prefill? **Probe**: we append a
question with a known answer about a file that survived: explain it, modify it with an exact SEARCH/REPLACE edit, or
make that edit as a tool call. **Retention**: we ask about a file this step evicted, and check whether the model
admits it's gone.

*(Point to the arms table.)* And these are the arms. Three references: not compressed, as if the compaction never
happened; prefix + prefill, which is standard prefix caching; and full prefill, the ground truth. Then no RoPE, which
reuses the moved text at its old positions, and shift RoPE, which rotates it. And three repairs on top of shift RoPE:
a random 15%, CacheBlend at 5 or 15%, and EPIC at 32 or 128 tokens per block. The last column says how much of the
moved text each arm re-prefills.

*If short:* replace the arm-by-arm reading with "references, the rotation, and three repairs on top of it".

### 12 · Qwen3-32B, every arm on every metric

**34:30 → 37:00.** Goal: rotation is essential, the copy cost is repaired only by the right tokens, and EPIC is the
most faithful.

Here's every arm on every metric for Qwen3-32B. It's a big table, so here's how to read it, and then the three things
that matter.

*(Point to the column groups.)* Columns are grouped by question: cost, then natural, probe and retention. Red means
significantly worse than full prefill, green means a cost that a repair fixed, and bold is the best repair.

*(Point to the top three rows.)* Before the three points, the reference rows. Full prefill says "ref" because
everything is compared with it, and a dash means we didn't run that combination. Prefix + prefill keeps 95.6% of
rounds identical, about the noise floor, so exact reuse is safe, as it should be.

*(Point to the no RoPE row.)* One: rotation is essential. Without it, only 29% of next rounds match, 10.5% of tool
calls are malformed, and every probe collapses.

*(Point to the modify column.)* Two: with rotation, almost everything holds up, except exact copying. The
SEARCH/REPLACE edit needs the model to copy old code verbatim, and shift RoPE's pass rate falls from 72.6 to 61.1, an
11.5-point loss. *(Slide down to the green cells.)* Every non-random repair brings it back to full prefill's level,
72.0 to 74.5. A random 15% reaches only 65.0, and CacheBlend 15% and EPIC 128 each beat it head to head. So it's not
how many tokens you re-prefill; it's which ones.

*(Point to the KL and identical columns.)* Three: fidelity. KL measures how far an arm's next-token probabilities are
from full prefill's; zero means identical. Only EPIC nearly halves it, from 0.0213 to 0.0115, and EPIC 128 keeps the
most rounds identical, 74.7%. That's still far below the 96% you get from two full prefills of the same prompt. For
scale, look at "not compressed": simply undoing the compaction changes a third of next rounds too.

*(Point to the TTFT column.)* And the cost: shift RoPE answers in 90 ms, EPIC in 99 to 121, CacheBlend in 139 to 223,
against 1,097 for a full prefill.

*If short:* say only point two and the TTFT column.

### 13 · Qwen3-8B, every arm on every metric

**37:00 → 39:00.** Goal: the smaller model's cost is a different one, and only EPIC 128 repairs it.

Now Qwen3-8B, replaying the same events. The smaller model breaks somewhere else.

*(Point to the no RoPE row.)* Rotation matters even more on this model: without it, only 23.7% of rounds match and the
KL is 1.90.

*(Point to the modify column.)* With rotation, copying is fine here: 71.3 against 72.6, not a significant difference.

*(Point to the retention columns.)* The cost is knowing what's gone. Asked about the evicted file, full prefill says
"that file is gone" 57.5% of the time. With shift RoPE, only 38.2%, and wrong answers rise from 40.8 to 57.9. It isn't
leaking the evicted file: recall of its content barely moves. The model just loses track of what it no longer has, and
guesses.

*(Point to the green cell in the EPIC 128 row.)* EPIC 128 closes 89% of that gap: at 55.3% it's statistically
indistinguishable from full prefill. CacheBlend and EPIC 32 close 45 to 66% and stay significantly below, and the
random 15% closes 2%.

*(Point to the KL and TTFT columns.)* Fidelity tells the same story: EPIC 128 cuts KL from 0.0518 to 0.0218, 58%
lower, and again keeps the most rounds identical, 75.7%. And the speed: 36 ms for shift RoPE, 38 to 42 for EPIC, 52 to
72 for CacheBlend, against 254 for a full prefill.

### 14 · Which tokens to re-prefill

**39:00 → 41:30.** Goal: why the first tokens of each moved block are the right ones.

Why does EPIC's simple rule win? *(Point to the left chart.)* This is the share of each cost repaired: upper bars are
the 32B copy cost, lower bars the 8B "gone" cost. Read them like this: 100%, the dashed line, means the arm got all
the way back to full prefill, and zero means no better than shift RoPE. Blue, EPIC, is high on both. Orange,
CacheBlend, repairs copying but only part of "gone". Grey, random, barely moves. *(Point to the right chart.)* The KL
cut agrees: EPIC 128 cuts KL by 46 and 58%.

*(Point to "Why the first tokens win".)* The reason is the shape of a compaction. A moved block is a stretch of
history that survived and moved as one piece, and each block starts right where the compaction deleted or replaced
text. So a block's first tokens were computed right after text that is now gone, and their cached keys and values are
the most out of date. It's "Her" from slide 6 all over again.

CacheBlend partly finds them: 14 to 16% of its picks land in a block's first 32 tokens, 3.2 to 3.5 times what random
picks would give. But it spreads the rest thin, over about 427 separate little pieces per event, where EPIC
re-prefills four solid ones, the start of each block. CacheBlend's signal is real, by the way: the footer shows the
top 5% of moved tokens carry 46% of the layer-1 deviation on the 32B and 61% on the 8B. It just spends its budget less
well. At equal budgets EPIC keeps more next rounds identical to full prefill. For EPIC 32 against CacheBlend 5% on the
32B, there are 47 events where only EPIC matches, against 20 where only CacheBlend does.

*(Point to "Cost".)* And it's cheap. EPIC 128 re-prefills 308 tokens per event on average, adding 27 ms to shift
RoPE's 90 ms on Qwen3-32B.

*If short:* skip the equal-budget counts.

### 15 · What the repairs cost in time

**41:30 → 43:30.** Goal: reuse saves the time; EPIC's repair costs little, CacheBlend's several times more.

Finally, time. We timed the real next request for 200 of the events, each run three times, on one H200 per model.

*(Point to the left chart.)* Reuse is what saves the time. On Qwen3-32B, full prefill takes 1,097 ms to the first
token, prefix caching 765, and shift RoPE 90. The 8B shows the same pattern: 254, 184 and 36. Shift RoPE answers in 9%
of full prefill's time on the 32B and 18% on the 8B, while prefix caching saves only 27 to 29%. Both panels use
medians, so one slow request can't drag the numbers around.

*(Point to the right panel.)* The repairs add a little on top. On the 32B, EPIC adds 8 ms for EPIC 32 and 27 ms for
EPIC 128; on the 8B, at most 5. CacheBlend adds 39 to 93 ms on the 32B, 3.5 to 5 times EPIC's addition at similar
budgets, because it pays for a selection pass plus more tokens: CacheBlend 15% computes 666 more tokens than shift
RoPE, EPIC 128 only 305.

*(Point to the last bullet.)* And 25.5 of shift RoPE's 90 ms is just loading and re-rotating the moved cache, so a
fused kernel could shrink it further.

The footer has the caveats: one request at a time, eager mode, and our re-prefilled tokens attend to the whole
context, so these are upper bounds.

**Checkpoint: 43:30.**

---

## Wrap-up

### 16 · TL;DR

**43:30 → 45:00.** Goal: four lines to take home.

Let me wrap up in four lines.

One: attention is a soft lookup over earlier tokens' keys and values. Keeping them, the KV cache, makes each new token
cost one step, at 144 to 256 KiB per token on Qwen3, and engines reuse it only for an exact prefix.

Two: gluing separately cached pieces breaks for two reasons. B never attended to A, and RoPE can be fixed exactly only
at layer 0; above it, the old position is baked in.

Three: one storyline, three papers. CacheBlend: attention is sparse, so re-prefill the few tokens that deviate most.
EPIC: those few tokens are each chunk's first tokens, so re-prefill the first k. KVLink: first tokens still carry
their own meaning, so train dedicated link tokens instead.

Four: in our agent, shift RoPE skips about 80% of the re-prefill work but costs 11.5 points of exact copying on the
32B and 19.3 points of admitting what's gone on the 8B. EPIC 128, re-prefilling 11% of the moved tokens, repairs both
and roughly halves KL, for 27 extra milliseconds on the 32B. A random 15% repairs neither.

*(Point to "Not covered".)* What we didn't cover: staleness that compounds over many compactions in a row, since we
measured one step, and throughput under concurrent load.

Thank you. Happy to take questions.

---

## Likely questions

**Slide 2. Why does the footer say Qwen3 keeps only 8 key/value heads?** Qwen3 uses grouped-query attention: several
query heads share one key/value head (32 query heads over 8 key/value heads in Qwen3-8B, 64 over 8 in Qwen3-32B). It
shrinks the cache; nothing else in the talk changes.

**Slide 2. What does RMSNorm do?** It divides each token's vector by its root-mean-square and multiplies by learned
weights, so the numbers stay in a stable range from block to block.

**Slide 3. Why divide by root d_head?** Dot products of 128-number vectors grow large; dividing by √128 keeps the
softmax from putting all its weight on a single token.

**Slide 5. Why does vLLM match 16-token blocks instead of single tokens?** vLLM stores the cache in fixed-size pages
(PagedAttention), and matching whole pages keeps the bookkeeping cheap. SGLang's tree matches single tokens.

**Slides 7–8. Can EPIC and CacheBlend be combined?** We didn't test that.

**Slide 11. vLLM already does prefix caching, so why is full prefill the reference?** Exact prefix reuse gives the
same result up to numerical noise: prefix + prefill keeps 95.6% of rounds identical on the 32B, about the noise floor.
So full prefill is the quality reference. For speed, the fair baseline is prefix + prefill, 765 ms on the 32B.

**Slide 12. What exactly is the KL?** For each token of the next round, it compares the next-token probability
distribution under full prefill with the arm's, and averages over tokens, in nats. Zero means the two predict
identically.

**Slide 12. Where do the p-values come from?** Exact McNemar tests, a paired test for yes/no outcomes. On the same
events, it compares how often only arm A passes with how often only arm B passes; a lopsided split means a real
difference. The 47 versus 20 on slide 14 are such counts.

**Slide 12. Why do two full prefills of the same prompt disagree 4% of the time?** GPU arithmetic depends slightly on
how requests are batched. A tiny difference can flip one greedily chosen token, and the rest of the reply then
diverges.

**Slide 12. Why does "not compressed" match only 67%?** It is the next round without the compaction, on the old
history. So the compaction itself changes about a third of next rounds, and shift RoPE changes them about as often.
The harm shows up in specific tests (copying on the 32B, eviction on the 8B), not in the raw identical-round rate.

**Slide 12. What is a SEARCH/REPLACE edit?** Aider's edit format: the model writes the exact old lines (SEARCH) and
their replacement (REPLACE). The edit applies only if the SEARCH text matches the file character for character, so it
tests verbatim copying from the context.

**Slide 13. Why do the two models fail differently?** We don't have a mechanistic explanation. Both costs come from
cache computed in a context that no longer exists; which behaviour breaks depends on the model and the task.

**Slide 14. CacheBlend 5% repairs 117% of the 32B copy cost: is it better than full prefill?** No, that's within
noise. Probe results carry 95% intervals of about ±6–7 points, and 74.5 against 72.6 is not a significant difference.

**Slide 15. Why not simply re-prefill everything? It's about a second.** On the 32B that second is 1,097 ms of GPU
time per request, and the agent sends a request every round; on a shared server, that time is taken from other users.
Time to first token is only part of a round, since the reply still has to be generated and tools run, so the
end-to-end saving is smaller than the time-to-first-token ratio.

**Only if asked. Does this work on MLA models such as DeepSeek's?** We tested one MLA model, GLM-4.7-Flash. None of
these token-level repairs fixed its copy cost. It's outside today's scope.