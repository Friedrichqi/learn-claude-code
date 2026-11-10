# Topic 22: waits by the KV use the model saw (wb_waits.py, post hoc)

on-rule: the newest [server] line showed 80 % or more; off-rule: below 80 %. A reason cites the server when it names the KV cache, load, capacity and the like.

| run | on-rule calls | with wait | wait-only | off-rule calls | with wait | wait-only | s asked on / off | on-rule reasons: server / other / none | off-rule reasons: server / other / none |
|---|---|---|---|---|---|---|---|---|---|
| `n16_wait` | 591 | 76.5 % | 284 | 2918 | 0.8 % | 7 | 10975 / 405 | 377 / 4 / 71 | 17 / 2 / 5 |

**`n16_wait` off-rule:** trials `sphinx-doc__sphinx-8035__cVUhq4C` (8), `django__django-15503__Djhpbzd` (3), `write-compressor__Vj9bNBn` (3), `gpt2-codegolf__9grWRfC` (2), `pydata__xarray-4094__GyyDs8Z` (2); reasons "" (5); "KV cache at 72%" (2); "KV cache at 77%" (2); "KV cache at 76% - need to pause" (1); "KV cache at 67%, taking a short pause" (1); "KV cache is at 79%, need to pause to let other sessions catch up" (1)
