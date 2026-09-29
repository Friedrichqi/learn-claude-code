Modification suggestions:
1. Substitute the diagram on the right of page 2 into the following diagram. Ensure that for each of the hidden states in the middle of this diagram, it should possess dimension size besides it. 
                       Shape: [Batch, Seq_Len, d_model]
                                      │
           ┌──────────────────────────┴──────────────────────────┐
           │                                                     ▼
           │                                            [ Root RMSNorm ]
           │                                                     │
           │                                                     ▼
           │                                             [ QKV Projector ]
           │                                    Weights: W_q, W_k, W_v [d_model, d_model]
           │                                                     │
           │                            ┌────────────────────────┼────────────────────────┐
           │                            ▼                        ▼                        ▼
           │                       Queries (Q)               Keys (K)                 Values (V)
           │                  [B, S, Num_H, d_head]    [B, S, Num_H, d_head]    [B, S, Num_H, d_head]
           │                            │                        │                        │
           │                            ▼                        ▼                        │
           │                    ┌──────────────┐          ┌──────────────┐                │
           │                    │  RoPE (cos)  │          │  RoPE (cos)  │                │
           │                    │  Rotation    │          │  Rotation    │                │
           │                    │  & sin grid  │          │  & sin grid  │                │
           │                    └──────────────┘          └──────────────┘                │
           │                            │                        │                        │
           │                            ▼                        ▼                        │
           │                        Rotated Q                Rotated K                    │
           │                            │                        │                        │
           │                            └───────────┬────────────┘                        │
           │                                        ▼                                     │
           │                            [ Scaled Dot-Product ]                            │
           │                           Softmax( (Q @ K^T) / √d )                          │
           │                                        │                                     │
           │                                        ▼                                     │
           │                              [ Attention Weights ]                           │
           │                                        │                                     │
           │                                        ├─────────────────────────────────────┘
           │                                        ▼
           │                               [ Context Attention ]
           │                                        │
           │                                        ▼
           │                              [ Output Projection ]
           │                             Weight: W_o [d_model, d_model]
           │                                        │
           ▼                                        ▼
    (Residual Join) ─────────────────────────────►( + )
                                                    │
           ┌────────────────────────────────────────┴────────────────────────────────────────┐
           │                                                                                 ▼
           │                                                                        [ Post-Attn RMSNorm ]
           │                                                                                 │
           │                                                                                 ▼
           │                                                                           [ MLP Block ]
           │                                                                  Up-Scales from d_model to d_ffn
           │                                                                                 │
           │                                                   ┌─────────────────────────────┴─────────────────────────────┐
           │                                                   ▼                                                           ▼
           │                                             [ Gate Proj ]                                               [ Up Proj ]
           │                                         (Swish Activation)                                                    │
           │                                                   │                                                           │
           │                                                   └─────────────────────────────┬─────────────────────────────┘
           │                                                                                 ▼
           │                                                                          [ Element Multiplication ]
           │                                                                                 │
           │                                                                                 ▼
           │                                                                           [ Down Proj ]
           │                                                                 Down-Scales from d_ffn back to d_model
           │                                                                                 │
           ▼                                                                                 ▼
    (Residual Join) ──────────────────────────────────────────────────────────────────────►( + )
                                                                                             │
                                                                                             ▼
                                                                                   [ Layer Output Tensor ]
                                                                               Shape: [Batch, Seq_Len, d_model]

2. The left diagram of page 3 should have attention values (it could be better if it is a heat map) within the matrix. And also put an underscore sentence behind the diagram stating that the tokens only reside or only hold for the first layer of the transformer. For the input tokens to the second layer of the transformer, it can now be understood as a simple semantic word, which is the reason why we have the contact the KV cache reuse context problem in the following slides. Just insert a reminder here. 
3. The explanation on the right of page 3:
    a. Each token makes a query (what it looks for), a key (what it offers) and a value (what it passes on). Token i takes a weighted average of the values of tokens 1…i, weighted by how well its query matches their keys. --> Stating that the attention mechanism comes from the concept of database. Supposing in the database we have query, key and value. The objective of a query is to derive the value with the most similar key. So the attention map could be thought of as a weighted summation of all the values from keys that's related to the query.
    b. I remember the weight matrix QKV should be right-multiply rather than left-multiply.
    c.  Causal: token i reads only tokens 1…i. That is why its keys and values can be stored and reused later (slide 5), and also why they depend on every token before it (slide 7). --> Since each token i attends to all the tokens from 1 to i, if we can store the `k` cache and `v` cache from the previous one to `i-1` tokens, we can save effort from reprefilling the previous `i-1` tokens.
    d. Raise a question here: why don't we need `q` cache?
    e. Delete this action of grouped query attention GQA. We don't need to give an explanation here.
4. On page 4, we should have a sentence that ROPE only applies to query and key because we only care about the relative position between different tokens. If we apply it to value as well, it could introduce another absolute position ROPE into the token. Attention sees QK^ T, so it should be Q[M - N]K. Deletes the section stating What our vLLM connector runs, in fp32 (Qwen3's "NeoX" layout pairs dim i with i+64)
5. Merge page 5 into page 3. And delete the two sentences: Agents hit this constantly. Every call resends the whole history: the system prompt, tool definitions, all earlier turns and tool results. Our harness's lead made a median of 30 calls per run. In our own latency study, prefill was 1.2–5.6% of a call at the harness's usual prompt sizes, so reuse there mostly frees GPU time for other requests. Prefill takes seconds only above roughly 50k tokens per call.
6. Remove this paragraph from page 6 because we don't want to dive into our harness system during these slides or this paper reading presentation. In our harness, once history passes 50 messages, early messages are rewritten every round: 96.7% of 47,085 compaction events include such a snip. The next prompt then shares an exact prefix of only 3,191 of its 9,054 tokens, although 4,648 more tokens are unchanged, only moved.
7. On page 7, I want you to use an example of concatenating two separately prefixed sentences into one single sentence, and state or explain why we can't reuse the separately prefixed tokens and concatenate them together.  There are two reasons for the damage: a. for all the layers, the tokens from sentence B cannot attend to tokens in sentence A.  Use a triangle attention heatmap to explain this. b. The ROPE from layer 1 to layer n-1, because the transformer layer is a nonlinear transformation, we cannot rescue the RoPE beyond layer 0, because the hidden states still embed the original positional embedding throughout all 1~n-1 layers.
8. Remove the sentence concerning attention sinks on page 7. Attention sinks: the first tokens of a sequence soak up attention. A span encoded after other text, or alone, carries the wrong kind of "first tokens". EPIC recomputes exactly those.
9. Delete Page 8 because we can state the quantitative results from page 8 side by side with queue mechanisms from CacheBlend and EPIC.
10. Key idea --> Key obervation: a. Attention is sparse, so a few tokens carry most of the importance throughout the sentence. b. And a token that deviates most at one layer tends to deviate most at the next layer, so an early layer reprefilling can pick them.
11. For the paper reading part, for example, cache blend, you can use the figure in the paper to elaborate your statement. For example, Figure 3 could be used to elaborate your problem statement: RAG prompts glue several retrieved ... Also, for example, for the epic you can use Figure 4 from the original paper to elaborate on the phenomenon called attention sink.
12. For page 10, the EPIC problem setup should be the same as the CacheBlend. The key observation of it should be the attention sink (a machine learning phenomenon in large language models (LLMs) where the model assigns disproportionately large attention scores to the earliest tokens in a sequence, because of the recurrent decoding pattern). Since the first few tokens of one sentence carry the most importance of the whole sentence. Why don't we just reprefill the first few tokens in this sentence.
13. On page 11, the paper called KVlink. My major understanding of the observation is that since the attention sink phenomenon that the first few tokens of the sentence carry the most importance in the entire sentence. The first few tokens still contain some of the semantic meanings, so reprefilling it only recovers its connection to related words' tokens. Given the case, why don't we propose some unmeaningful Tokens that are designed to conclude the previous whole documentation. The major methodology of this paper is to insert some trainable special tokens, which don't have any semantic meanings. Their assigned role is to attend to every single token in the previous documentation and try to recover the whole meaning of the documentation. 
14. On page 12, also add a unified storyline to the summary table of all three methodologies. That's from the cache blend. It observes only a few tokens in sentences because of sparse attention phenomenon. In the paper, this observation is given the Attention Sink phenomenon. Maybe the few tokens that matter in the sentence are the first few tokens. Beyond that, the paper KV Link proposes the reprefilled first few tokens in the sentence still carries semantic meanings, so they may simply attend to the most semantically related tokens in the previous documentation.  Why don't we train some meaningful tokens that are originally designed to give a summary of all separately reprefilled documentation.
15. On page 13, the major objective of this page should be why reusing the KV cache is important to our agentic system.  For the 11-armed table, simplify it to only contain:
- Not compressed (change the previous Oracle into this name)
- prefix + prefill
- fully prefill (previous recompute)
- no RoPE
- shift RoPE
- rand15
- CacheBlend...
- EPIC...
16. Unify the different experiment entries and metrics on page 13, 14, and 15. From my understanding, explain, modify, and tool call belongs to natural: the real next request (683);  gone belongs to retention: a question about a file this step evicted (228); wrong belongs to probe: a gold question about a surviving file, 475 (165 explain, 157 modify, 153 tool call). You should categorize all related metrics under one single integrated column. Merge part A and part B into one single session. Do not disaggregate them into two separate sections. 
17. On page 16, reparaphrase those explanations into more comprehensible statements: It does find run heads: 14–16% of blend15's picks are among a run's first 32 tokens, 3.2–3.5× the 4.4% base rate. The rest of its budget is scattered over about 427 spans per event; EPIC recomputes 4. The heads are the first tokens after each edit point, computed right after text that the compaction removed or moved. For example, I don't understand the second and the fourth sentences.
18. On page 17, I observe some data inconsistency between TTFT and add to shift. If you use median, use median for both of the two sections. If you use mean, just maintain mean across the two sections.
19. Delete the information about MLA. We don't have time allocated to it anymore. 
20. Modify the conclusion to long-term rates, the final slide of this presentation, according to our previous modifications. For the paper reading part, it should also be the unified storyline for the three papers stated in point 14 of this file. 