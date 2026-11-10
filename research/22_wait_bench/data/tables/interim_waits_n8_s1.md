# Topic 22: waits by the KV use the model saw (wb_waits.py, post hoc)

on-rule: the newest [server] line showed 80 % or more; off-rule: below 80 %. A reason cites the server when it names the KV cache, load, capacity and the like.

| run | on-rule calls | with wait | wait-only | off-rule calls | with wait | wait-only | s asked on / off | on-rule reasons: server / other / none | off-rule reasons: server / other / none |
|---|---|---|---|---|---|---|---|---|---|
| `n8_wait` | 39 | 66.7 % | 7 | 3639 | 0.8 % | 29 | 445 / 5995 | 24 / 0 / 2 | 2 / 27 / 1 |

**`n8_wait` off-rule:** trials `instance_gravitational__teleport__cdj67AR` (29), `model-extraction-relu-logits__EVompEQ` (1); reasons "Persistent network issues" (16); "Persistent network issues with Go module proxy" (6); "Network issues downloading netlink module" (2); "Extended network issue with Go module proxy" (2); "Network SSL issues persisting" (1); "Persistent SSL connection issues" (1)
