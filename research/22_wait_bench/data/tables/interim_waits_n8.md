# Topic 22: waits by the KV use the model saw (wb_waits.py, post hoc)

on-rule: the newest [server] line showed 80 % or more; off-rule: below 80 %. A reason cites the server when it names the KV cache, load, capacity and the like.

| run | on-rule calls | with wait | wait-only | off-rule calls | with wait | wait-only | s asked on / off | on-rule reasons: server / other / none | off-rule reasons: server / other / none |
|---|---|---|---|---|---|---|---|---|---|
| `n8_wait_s1` | 39 | 66.7 % | 7 | 3639 | 0.8 % | 29 | 445 / 5995 | 24 / 0 / 2 | 2 / 27 / 1 |
| `n8_wait` | 46 | 58.7 % | 15 | 3227 | 0.2 % | 3 | 320 / 65 | 15 / 0 / 12 | 5 / 0 / 1 |

**`n8_wait_s1` off-rule:** trials `instance_gravitational__teleport__cdj67AR` (29), `model-extraction-relu-logits__EVompEQ` (1); reasons "Persistent network issues" (16); "Persistent network issues with Go module proxy" (6); "Network issues downloading netlink module" (2); "Extended network issue with Go module proxy" (2); "Network SSL issues persisting" (1); "Persistent SSL connection issues" (1)

**`n8_wait` off-rule:** trials `instance_gravitational__teleport__pd5sTjK` (2), `django__django-14351__Dky6EaH` (1), `feal-differential-cryptanalysis__x5Pm6No` (1), `mailman__KAPQqtV` (1), `pydata__xarray-6992__mRG9n6Q` (1); reasons "KV cache at 79%" (1); "KV cache at 49%, need to pause to let other requests proceed" (1); "KV cache at 77%, pausing briefly" (1); "KV cache at 75%, pausing briefly" (1); "KV cache at 75% - letting the server catch up" (1); "" (1)
