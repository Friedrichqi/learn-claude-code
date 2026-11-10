# Topic 22: waits by the KV use the model saw (wb_waits.py, post hoc)

on-rule: the newest [server] line showed 80 % or more; off-rule: below 80 %. A reason cites the server when it names the KV cache, load, capacity and the like.

| run | on-rule calls | with wait | wait-only | off-rule calls | with wait | wait-only | s asked on / off | on-rule reasons: server / other / none | off-rule reasons: server / other / none |
|---|---|---|---|---|---|---|---|---|---|
| `n32_wait` | 1651 | 64.9 % | 663 | 2348 | 3.9 % | 11 | 30053 / 3990 | 968 / 5 / 98 | 20 / 67 / 4 |

**`n32_wait` off-rule:** trials `instance_gravitational__teleport__Jp2a2dB` (68), `django__django-15503__ik9xPCj` (4), `django__django-7530__9v5wQjz` (3), `mwaskom__seaborn-3069__bHcMTnx` (3), `custom-memory-heap-crash__PSmHAXV` (2); reasons "Waiting for network to stabilize" (64); "" (4); "KV cache at 77%" (4); "KV cache at 78%" (2); "KV cache at 77%, need to pause" (2); "KV cache at 74%, need to pause for server to catch up" (2)
