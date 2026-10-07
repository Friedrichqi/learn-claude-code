# S1 · D1 replayed on recorded sessions (offline, CPU)

Each session's lead chain is replayed through tm_server_ext's own delta render and bookkeeping (vLLM's Anthropic converter, the Qwen3 template via the HF tokenizer) and every call is compared with a full re-render, block by block (one block per `<|im_start|>`). Replies stand in for generated tokens as the template renders them after the generation prompt.

| sessions from | sessions | lead calls | D1 calls (delta) | of which after a history edit (truncation at k) | identical outside assistant turns | differing non-assistant blocks | new tokens per call, re-render | new tokens per call, D1 | prefill D1 saves |
|---|---|---|---|---|---|---|---|---|---|
| p1a (32B) | 8 | 847 | 833 | 146 | 833 | 0 | 3,751 | 3,685 | 1.8 % |
| p1b (8B) | 8 | 719 | 711 | 0 | 711 | 0 | 216 | 145 | 32.9 % |
| n01a (32B, 11) | 8 | 817 | 801 | 151 | 801 | 0 | 3,939 | 3,888 | 1.3 % |

*Key.* **new tokens**: prompt tokens past the reusable prefix (whole 16-token blocks) of the previous call: what the engine must prefill even with a warm cache. Re-render: the template drops the generation prompt's empty think block, so the prefix ends 4 tokens before the previous prompt's end. D1: the prefix runs through the previous reply as generated.
