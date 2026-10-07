# T9 · Where the EngineCore thread is, and its CPU (stack samples, cells run with HW_STACK_MS)

Classes: samples inside a step whose launch -> result exceeds 1 s (`long`), inside steps under 100 ms (`short`), or neither. busy = thread CPU / wall; GIL held = share of samples where the sampler waited > 3 periods. Group and location columns are shares of samples (where the thread is). CPU s charges each 20-ms interval's CPU to the stack at its end: reliable where the thread stays at one location (long steps), smeared onto the wait in short steps, where the thread works and sleeps within 20 ms.

## s1 / hw_s1_n04 (N = 4)

| class | samples | busy | GIL held | GEMM launch | attention-op launch | other compiled-layer launch | end-of-step wait | other (scheduling, input preparation, sampling) |
|---|---|---|---|---|---|---|---|---|
| long | 11787 | 0.69 | 0.00 | 0.52 | 0.12 | 0.00 | 0.32 | 0.04 |
| short | 40552 | 0.07 | 0.00 | 0.00 | 0.01 | 0.00 | 0.92 | 0.06 |
| other | 7195 | 0.09 | 0.00 | 0.05 | 0.01 | 0.00 | 0.92 | 0.02 |

| class | location (innermost frame) | vLLM caller | time share | CPU s | CPU share |
|---|---|---|---|---|---|
| long | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.32 | 2.2 | 0.01 |
|  | `compiled layer:1212 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.18 | 43.1 | 0.26 |
|  | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.15 | 36.0 | 0.22 |
|  | `compiled layer:1230 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.13 | 30.7 | 0.19 |
|  | `torch/_ops.py:1279:__call__` | `vllm/vllm_flash_attn/flash_attn_interface.py:349:flash_attn_varlen_func` | 0.06 | 14.2 | 0.09 |
|  | `compiled layer:1199 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.05 | 12.9 | 0.08 |
| short | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.92 | 52.2 | 0.86 |
|  | `vllm/v1/worker/gpu/buffer_utils.py:78:copy_to_uva` | `vllm/v1/worker/gpu/buffer_utils.py:78:copy_to_uva` | 0.01 | 0.1 | 0.00 |
|  | `torch/_tensor.py:1215:__iter__` | `vllm/v1/worker/gpu/attn_utils.py:241:build_slot_mappings_by_layer` | 0.01 | 0.2 | 0.00 |
|  | `vllm/v1/worker/gpu/model_runner.py:1420:sample` | `vllm/v1/worker/gpu/model_runner.py:1420:sample` | 0.00 | 0.4 | 0.01 |
|  | `vllm/v1/engine/core.py:1472:_process_engine_step` | `vllm/v1/engine/core.py:1472:_process_engine_step` | 0.00 | 0.0 | 0.00 |
|  | `vllm/v1/worker/gpu/sample/states.py:99:get_top_k_top_p` | `vllm/v1/worker/gpu/sample/states.py:99:get_top_k_top_p` | 0.00 | 0.2 | 0.00 |
| other | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.92 | 2.8 | 0.21 |
|  | `compiled layer:1212 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.02 | 3.2 | 0.25 |
|  | `compiled layer:1230 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 2.1 | 0.16 |
|  | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 1.2 | 0.09 |
|  | `compiled layer:1199 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 0.9 | 0.07 |
|  | `torch/_ops.py:1279:__call__` | `vllm/_custom_ops.py:2694:reshape_and_cache_flash` | 0.01 | 0.7 | 0.06 |

## s1 / hw_s1_n16 (N = 16)

| class | samples | busy | GIL held | GEMM launch | attention-op launch | other compiled-layer launch | end-of-step wait | other (scheduling, input preparation, sampling) |
|---|---|---|---|---|---|---|---|---|
| long | 50099 | 0.93 | 0.00 | 0.81 | 0.08 | 0.00 | 0.07 | 0.03 |
| short | 1105 | 0.13 | 0.00 | 0.03 | 0.03 | 0.00 | 0.88 | 0.07 |
| other | 8138 | 0.78 | 0.00 | 0.69 | 0.09 | 0.00 | 0.21 | 0.01 |

| class | location (innermost frame) | vLLM caller | time share | CPU s | CPU share |
|---|---|---|---|---|---|
| long | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.27 | 269.0 | 0.29 |
|  | `compiled layer:1212 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.23 | 228.8 | 0.24 |
|  | `compiled layer:1230 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.23 | 228.9 | 0.24 |
|  | `compiled layer:1199 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.08 | 82.8 | 0.09 |
|  | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.07 | 9.3 | 0.01 |
|  | `torch/_ops.py:1279:__call__` | `vllm/_custom_ops.py:2694:reshape_and_cache_flash` | 0.04 | 44.3 | 0.05 |
| short | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.88 | 1.9 | 0.66 |
|  | `vllm/v1/worker/gpu/states.py:117:add_request` | `vllm/v1/worker/gpu/states.py:117:add_request` | 0.01 | 0.0 | 0.01 |
|  | `compiled layer:1230 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 0.1 | 0.04 |
|  | `vllm/v1/engine/core.py:1472:_process_engine_step` | `vllm/v1/engine/core.py:1472:_process_engine_step` | 0.01 | 0.0 | 0.00 |
|  | `torch/_ops.py:1279:__call__` | `vllm/vllm_flash_attn/flash_attn_interface.py:349:flash_attn_varlen_func` | 0.01 | 0.1 | 0.03 |
|  | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 0.1 | 0.03 |
| other | `compiled layer:1212 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.32 | 51.7 | 0.40 |
|  | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.21 | 0.3 | 0.00 |
|  | `compiled layer:1230 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.17 | 28.2 | 0.22 |
|  | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.13 | 21.5 | 0.17 |
|  | `torch/_ops.py:1279:__call__` | `vllm/_custom_ops.py:2694:reshape_and_cache_flash` | 0.06 | 10.4 | 0.08 |
|  | `compiled layer:1199 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.06 | 9.8 | 0.08 |

## s2 / hw_s2_l04 (N = 4)

| class | samples | busy | GIL held | GEMM launch | attention-op launch | other compiled-layer launch | end-of-step wait | other (scheduling, input preparation, sampling) |
|---|---|---|---|---|---|---|---|---|
| short | 55639 | 0.23 | 0.00 | 0.00 | 0.02 | 0.00 | 0.77 | 0.21 |
| other | 3959 | 0.12 | 0.00 | 0.05 | 0.01 | 0.00 | 0.90 | 0.03 |

| class | location (innermost frame) | vLLM caller | time share | CPU s | CPU share |
|---|---|---|---|---|---|
| short | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.77 | 189.0 | 0.75 |
|  | `vllm/v1/worker/gpu/buffer_utils.py:78:copy_to_uva` | `vllm/v1/worker/gpu/buffer_utils.py:78:copy_to_uva` | 0.02 | 4.9 | 0.02 |
|  | `torch/_tensor.py:1215:__iter__` | `vllm/v1/worker/gpu/attn_utils.py:241:build_slot_mappings_by_layer` | 0.02 | 4.4 | 0.02 |
|  | `vllm/v1/engine/core.py:1472:_process_engine_step` | `vllm/v1/engine/core.py:1472:_process_engine_step` | 0.02 | 3.5 | 0.01 |
|  | `vllm/v1/worker/gpu/model_runner.py:1420:sample` | `vllm/v1/worker/gpu/model_runner.py:1420:sample` | 0.01 | 4.0 | 0.02 |
|  | `vllm/v1/worker/gpu/buffer_utils.py:110:copy_to_uva` | `vllm/v1/worker/gpu/buffer_utils.py:110:copy_to_uva` | 0.01 | 2.7 | 0.01 |
| other | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.90 | 2.9 | 0.31 |
|  | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 1.0 | 0.11 |
|  | `compiled layer:1212 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 0.7 | 0.07 |
|  | `compiled layer:854 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 0.7 | 0.07 |
|  | `compiled layer:1229 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 0.6 | 0.07 |
|  | `torch/_ops.py:1279:__call__` | `vllm/vllm_flash_attn/flash_attn_interface.py:349:flash_attn_varlen_func` | 0.01 | 0.4 | 0.05 |

## s2 / hw_s2_l32 (N = 32)

| class | samples | busy | GIL held | GEMM launch | attention-op launch | other compiled-layer launch | end-of-step wait | other (scheduling, input preparation, sampling) |
|---|---|---|---|---|---|---|---|---|
| short | 48284 | 0.14 | 0.00 | 0.01 | 0.03 | 0.00 | 0.85 | 0.11 |
| other | 11177 | 0.12 | 0.00 | 0.06 | 0.02 | 0.00 | 0.88 | 0.05 |

| class | location (innermost frame) | vLLM caller | time share | CPU s | CPU share |
|---|---|---|---|---|---|
| short | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.85 | 105.5 | 0.75 |
|  | `vllm/utils/torch_utils.py:718:async_tensor_h2d` | `vllm/utils/torch_utils.py:718:async_tensor_h2d` | 0.02 | 4.5 | 0.03 |
|  | `vllm/v1/engine/core.py:1472:_process_engine_step` | `vllm/v1/engine/core.py:1472:_process_engine_step` | 0.02 | 1.1 | 0.01 |
|  | `torch/_ops.py:1279:__call__` | `vllm/vllm_flash_attn/flash_attn_interface.py:349:flash_attn_varlen_func` | 0.01 | 4.4 | 0.03 |
|  | `vllm/v1/worker/gpu/buffer_utils.py:78:copy_to_uva` | `vllm/v1/worker/gpu/buffer_utils.py:78:copy_to_uva` | 0.01 | 1.4 | 0.01 |
|  | `vllm/v1/worker/gpu/states.py:117:add_request` | `vllm/v1/worker/gpu/states.py:117:add_request` | 0.01 | 1.9 | 0.01 |
| other | `torch/cuda/streams.py:254:synchronize` | `vllm/v1/worker/gpu/async_utils.py:168:get_output` | 0.88 | 6.7 | 0.26 |
|  | `compiled layer:1229 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.02 | 3.2 | 0.12 |
|  | `compiled layer:1183 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 2.9 | 0.11 |
|  | `compiled layer:1212 extern_kernels.mm` | `vllm/compilation/piecewise_backend.py:198:compiled_graph_wrapper` | 0.01 | 2.1 | 0.08 |
|  | `torch/_ops.py:1279:__call__` | `vllm/vllm_flash_attn/flash_attn_interface.py:349:flash_attn_varlen_func` | 0.01 | 1.2 | 0.04 |
|  | `vllm/utils/torch_utils.py:718:async_tensor_h2d` | `vllm/utils/torch_utils.py:718:async_tensor_h2d` | 0.01 | 0.6 | 0.02 |

