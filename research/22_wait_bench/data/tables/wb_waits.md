# Topic 22: waits by the KV use the model saw (wb_waits.py, post hoc)

on-rule: the newest [server] line showed 80 % or more; off-rule: below 80 %. A reason cites the server when it names the KV cache, load, capacity and the like.

| run | on-rule calls | with wait | wait-only | off-rule calls | with wait | wait-only | s asked on / off | on-rule reasons: server / other / none | off-rule reasons: server / other / none |
|---|---|---|---|---|---|---|---|---|---|
| `n16_wait` | 591 | 76.5 % | 284 | 2918 | 0.8 % | 7 | 10975 / 405 | 377 / 4 / 71 | 17 / 2 / 5 |
| `n32_wait` | 1651 | 64.9 % | 663 | 2348 | 3.9 % | 11 | 30053 / 3990 | 968 / 5 / 98 | 20 / 67 / 4 |
| `n4_wait` | 0 | - | 0 | 3355 | 0.0 % | 0 | 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| `n8_wait` | 46 | 58.7 % | 15 | 3227 | 0.2 % | 3 | 320 / 65 | 15 / 0 / 12 | 5 / 0 / 1 |

**`n16_wait` off-rule:** trials `sphinx-doc__sphinx-8035__cVUhq4C` (8), `django__django-15503__Djhpbzd` (3), `write-compressor__Vj9bNBn` (3), `gpt2-codegolf__9grWRfC` (2), `pydata__xarray-4094__GyyDs8Z` (2); reasons "" (5); "KV cache at 72%" (2); "KV cache at 77%" (2); "KV cache at 76% - need to pause" (1); "KV cache at 67%, taking a short pause" (1); "KV cache is at 79%, need to pause to let other sessions catch up" (1)

**`n32_wait` off-rule:** trials `instance_gravitational__teleport__Jp2a2dB` (68), `django__django-15503__ik9xPCj` (4), `django__django-7530__9v5wQjz` (3), `mwaskom__seaborn-3069__bHcMTnx` (3), `custom-memory-heap-crash__PSmHAXV` (2); reasons "Waiting for network to stabilize" (64); "" (4); "KV cache at 77%" (4); "KV cache at 78%" (2); "KV cache at 77%, need to pause" (2); "KV cache at 74%, need to pause for server to catch up" (2)

**`n4_wait` off-rule:** trials -; reasons -

**`n8_wait` off-rule:** trials `instance_gravitational__teleport__pd5sTjK` (2), `django__django-14351__Dky6EaH` (1), `feal-differential-cryptanalysis__x5Pm6No` (1), `mailman__KAPQqtV` (1), `pydata__xarray-6992__mRG9n6Q` (1); reasons "KV cache at 79%" (1); "KV cache at 49%, need to pause to let other requests proceed" (1); "KV cache at 77%, pausing briefly" (1); "KV cache at 75%, pausing briefly" (1); "KV cache at 75% - letting the server catch up" (1); "" (1)
