# All-lab Nsight Systems and Grafana audit

The source audit covers all **110 executable labs** across six practical courses. **99 labs have an applicable Nsight Systems capture recipe; 11 explain why GPU capture does not apply. All 110 have an assigned course-owned Grafana JSON dashboard.** Six additional setup dashboards remain available. The first course, Soperator, has text only.

This is source and local validation, not live GPU qualification. No Slurm jobs, vendor captures, installations, deployments or Grafana imports were run on a cluster during this audit.

## Coverage

| Course | Labs / dashboards | Systems recipes | Explicit exceptions |
| --- | --- | --- | --- |
| Advanced Labs: Multi-GPUs Multi-Nodes communication optimization | 34 | 32 | 2 |
| Custom CUDA Kernels for GPU Optimization | 13 | 12 | 1 |
| GPU Fundamentals | 11 | 9 | 2 |
| GPU Performance Optimization | 14 | 14 | 0 |
| LLM Inference | 22 | 16 | 6 |
| LLM Training | 16 | 16 | 0 |

## Repairs

- Added 15 Systems recipes for CUDA correctness/readiness, optional CUDA introductory exercises, cluster kernels, NVLink, NIXL and serving goodput. Capture targets the actual worker, rank or owned GPU server.
- Corrected the moved NCCL launcher's source filename and the scaling lab's recipe to capture the distributed run shown in its guide.
- Added opt-in NVTX to newly covered Python exercises. Optional CPU paths stay usable without CUDA capture. Compute replay is rejected when no qualified Compute recipe exists.
- Kept vendor stdout separate from profiler diagnostics, retained per-worker reports, and prevented captured goodput runs from producing publishable comparison results.
- Assigned explicit applicability, report views and telemetry scope to every lab. Removed device panels from six CPU/model/protocol-only dashboards; retained relevant readiness and RDMA device context without claiming kernel or wire timings.
- Extended standalone validation to reject missing or contradictory applicability, guide/command drift, incorrect dashboard identity, mismatched metrics/units and unrelated GPU telemetry. Updated standalone downloads and rendered course pages.

Systems traces target-process descendants rather than unrelated GPU jobs. CUDA API, GPU kernels and copies explain device execution; NIC DMA may not appear as a CUDA kernel. These capture boundaries follow the [official Nsight Systems user guide](https://docs.nvidia.com/nsight-systems/UserGuide/). Vendor and server traces retain runtime-owned annotations; course NVTX names are not promised where the source does not emit them. Dynamo server capture spans startup and requests, bounded by the Slurm job deadline; a report containing only startup is incomplete.

Shared and direct Systems launchers use `--discard-environment=true` so reports do not collect process environment variables. Workloads still inherit their required runtime inputs. These captures also remove inherited `DEBUGINFOD_URLS` to avoid remote symbol downloads during report completion while retaining local symbol resolution. This includes the CUDA and Python batch launchers, worker-side vendor captures, owned Dynamo servers and readiness canaries.

## Applicability exceptions

- **advanced-gpu-communication / 07_rdma_bandwidth:** This verbs benchmark measures NIC DMA into host or GPU memory. CUDA tracing cannot establish RDMA wire bandwidth; use validated perftest throughput and topology evidence.
- **advanced-gpu-communication / 27_rdma_latency:** This verbs benchmark measures NIC completion latency. It is not a CUDA-kernel workload; use perftest latency and the checked host/GPU registration mode.
- **custom-cuda-kernels / 13_h100_preflight:** This device-attribute preflight establishes hardware capability, not kernel performance; use the reported attributes and readiness checks.
- **gpu-fundamentals / 10_compatibility_stack:** This read-only software-stack audit launches no benchmark workload; inspect the compatibility checks, not a GPU execution timeline.
- **gpu-fundamentals / 12_read_only_health:** This health probe reads device status without launching a timed workload; use its health fields and sampled device telemetry.
- **llm-inference / 16_model_artifact_audit:** This CPU-side artifact audit examines configuration and tensor metadata without running model inference.
- **llm-inference / 26_kv_capacity:** This capacity worksheet calculates bytes and token limits; it does not allocate a GPU KV cache.
- **llm-inference / 27_paged_kv:** This CPU page-allocation simulation measures model state, not GPU memory traffic.
- **llm-inference / 28_continuous_batching:** This discrete CPU scheduling model has no GPU server or CUDA kernels.
- **llm-inference / 30_engine_profile:** This HTTP protocol preflight checks an existing endpoint. It owns neither the server nor its capture lifecycle; profiling this client cannot capture server kernels. Use the owned serving experiments for GPU traces.
- **llm-inference / 36_kv_tiering:** This CPU retention model simulates cache placement without transferring GPU data or running inference.

## Validation

- All seven repository validators pass, including exact source/HTML, standalone helper and embedded dashboard/kit parity. All 110 lab dashboard UIDs are unique; datasource bindings, measurement units, missing-data states and aggregate ConfigMap size checks pass.
- The final CPU suite passes **1,151 tests**; the two temporary loopback API fixtures pass separately. New negative tests reject wrong dashboard/query/unit assignments, stale capture instructions, false applicability, unsupported vendor replay, and profiled goodput publication. Worker fixtures preserve literal arguments, rank identity, clean vendor output and nonzero exits. Renamed standalone packages validate across all six practical courses.
- Markdown, shell syntax, shellcheck, Python formatting and whitespace checks pass. Scoped Ruff has no introduced findings; three existing findings remain in unchanged portions of the packing lab and transfer-adoption tests (RUF007, SIM117, PLW1510).
- Browser evidence covers all 18 course/viewport combinations (six practical courses at 1440, 390 and 320 pixels), with current HTML hashes verified. Every lab’s rendered capture instructions and embedded dashboard bytes match source; six actual dashboard downloads and 200% text reflow pass. Initial checks attempted hidden contents links; later runs each had two intermittent heading-position failures. Waiting for the opened native contents layout to settle produced 12 successful repeated checks across the four affected cases. Earlier failed runs remain recorded and are not represented as wholly passing runs. Representative desktop and mobile lab views were also inspected visually. Owned headless Chrome 153.0.8010.48 contexts were closed after each check.
- The changed-scope code/security review found no remaining blocking source issue. Captures are opt-in, worker-scoped and stored privately; no new dependency, credential, external endpoint or live infrastructure change was introduced.

Native H100 execution, the pinned Nsight/runtime combination, counter permissions, actual Grafana query/rendering and VictoriaMetrics ingestion remain separate live checks. The browser checks cover course content and downloaded dashboard bytes; they do not execute Grafana.

## Lab-to-dashboard assignment

The links below resolve to the guide with the exact capture/import commands and to its assigned JSON. Report names remain private under the lab result directory.

### Advanced Labs: Multi-GPUs Multi-Nodes communication optimization

| Lab | Capture process | Dashboard |
| --- | --- | --- |
| [01_fabric_topology](../advanced-gpu-communication/reference/labs/01_fabric_topology.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/01_fabric_topology.json) |
| [02_collective_readiness](../advanced-gpu-communication/reference/labs/02_collective_readiness.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/02_collective_readiness.json) |
| [03_training_readiness](../advanced-gpu-communication/reference/labs/03_training_readiness.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/03_training_readiness.json) |
| [04_inference_readiness](../advanced-gpu-communication/reference/labs/04_inference_readiness.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/04_inference_readiness.json) |
| [05_transport_readiness](../advanced-gpu-communication/reference/labs/05_transport_readiness.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/05_transport_readiness.json) |
| [06_nvlink_bandwidth](../advanced-gpu-communication/reference/labs/06_nvlink_bandwidth.md) | vendor | [JSON](../advanced-gpu-communication/reference/grafana/06_nvlink_bandwidth.json) |
| [07_rdma_bandwidth](../advanced-gpu-communication/reference/labs/07_rdma_bandwidth.md) | Not applicable | [JSON](../advanced-gpu-communication/reference/grafana/07_rdma_bandwidth.json) |
| [08_distributed_collectives](../advanced-gpu-communication/reference/labs/08_distributed_collectives.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/08_distributed_collectives.json) |
| [09_nccl_transport_sweep](../advanced-gpu-communication/reference/labs/09_nccl_transport_sweep.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/09_nccl_transport_sweep.json) |
| [10_nccl_tests_report](../advanced-gpu-communication/reference/labs/10_nccl_tests_report.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/10_nccl_tests_report.json) |
| [11_collective_layout](../advanced-gpu-communication/reference/labs/11_collective_layout.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/11_collective_layout.json) |
| [12_distributed_scaling](../advanced-gpu-communication/reference/labs/12_distributed_scaling.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/12_distributed_scaling.json) |
| [13_collective_overlap](../advanced-gpu-communication/reference/labs/13_collective_overlap.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/13_collective_overlap.json) |
| [14_distributed_profiling](../advanced-gpu-communication/reference/labs/14_distributed_profiling.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/14_distributed_profiling.json) |
| [15_ddp_train](../advanced-gpu-communication/reference/labs/15_ddp_train.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/15_ddp_train.json) |
| [16_fsdp2_train](../advanced-gpu-communication/reference/labs/16_fsdp2_train.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/16_fsdp2_train.json) |
| [17_training_expert_parallel](../advanced-gpu-communication/reference/labs/17_training_expert_parallel.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/17_training_expert_parallel.json) |
| [18_training_tensor_parallel](../advanced-gpu-communication/reference/labs/18_training_tensor_parallel.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/18_training_tensor_parallel.json) |
| [19_gradient_overlap](../advanced-gpu-communication/reference/labs/19_gradient_overlap.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/19_gradient_overlap.json) |
| [20_parallelism_mechanics](../advanced-gpu-communication/reference/labs/20_parallelism_mechanics.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/20_parallelism_mechanics.json) |
| [21_ddp_buckets](../advanced-gpu-communication/reference/labs/21_ddp_buckets.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/21_ddp_buckets.json) |
| [22_fabric_training](../advanced-gpu-communication/reference/labs/22_fabric_training.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/22_fabric_training.json) |
| [23_inference_expert_parallel](../advanced-gpu-communication/reference/labs/23_inference_expert_parallel.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/23_inference_expert_parallel.json) |
| [24_inference_tensor_parallel](../advanced-gpu-communication/reference/labs/24_inference_tensor_parallel.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/24_inference_tensor_parallel.json) |
| [25_fabric_inference](../advanced-gpu-communication/reference/labs/25_fabric_inference.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/25_fabric_inference.json) |
| [26_collective_latency](../advanced-gpu-communication/reference/labs/26_collective_latency.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/26_collective_latency.json) |
| [27_rdma_latency](../advanced-gpu-communication/reference/labs/27_rdma_latency.md) | Not applicable | [JSON](../advanced-gpu-communication/reference/grafana/27_rdma_latency.json) |
| [28_nic_selection](../advanced-gpu-communication/reference/labs/28_nic_selection.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/28_nic_selection.json) |
| [29_nixl_transfer](../advanced-gpu-communication/reference/labs/29_nixl_transfer.md) | vendor | [JSON](../advanced-gpu-communication/reference/grafana/29_nixl_transfer.json) |
| [30_megatron_overlap](../advanced-gpu-communication/reference/labs/30_megatron_overlap.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/30_megatron_overlap.json) |
| [31_context_parallel](../advanced-gpu-communication/reference/labs/31_context_parallel.md) | rank | [JSON](../advanced-gpu-communication/reference/grafana/31_context_parallel.json) |
| [32_dynamo_disaggregation](../advanced-gpu-communication/reference/labs/32_dynamo_disaggregation.md) | server | [JSON](../advanced-gpu-communication/reference/grafana/32_dynamo_disaggregation.json) |
| [33_dynamo_routing](../advanced-gpu-communication/reference/labs/33_dynamo_routing.md) | server | [JSON](../advanced-gpu-communication/reference/grafana/33_dynamo_routing.json) |
| [34_serving_goodput](../advanced-gpu-communication/reference/labs/34_serving_goodput.md) | server | [JSON](../advanced-gpu-communication/reference/grafana/34_serving_goodput.json) |

### Custom CUDA Kernels for GPU Optimization

| Lab | Capture process | Dashboard |
| --- | --- | --- |
| [01_vector_add](../custom-cuda-kernels/reference/labs/01_vector_add.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/01_vector_add.json) |
| [02_fused_elementwise](../custom-cuda-kernels/reference/labs/02_fused_elementwise.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/02_fused_elementwise.json) |
| [03_tiled_transpose](../custom-cuda-kernels/reference/labs/03_tiled_transpose.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/03_tiled_transpose.json) |
| [04_reduction](../custom-cuda-kernels/reference/labs/04_reduction.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/04_reduction.json) |
| [05_tiled_stencil](../custom-cuda-kernels/reference/labs/05_tiled_stencil.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/05_tiled_stencil.json) |
| [06_divergence_tail](../custom-cuda-kernels/reference/labs/06_divergence_tail.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/06_divergence_tail.json) |
| [07_resource_sweep](../custom-cuda-kernels/reference/labs/07_resource_sweep.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/07_resource_sweep.json) |
| [08_async_pipeline](../custom-cuda-kernels/reference/labs/08_async_pipeline.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/08_async_pipeline.json) |
| [09_library_epilogue](../custom-cuda-kernels/reference/labs/09_library_epilogue.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/09_library_epilogue.json) |
| [10_hopper_cluster](../custom-cuda-kernels/reference/labs/10_hopper_cluster.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/10_hopper_cluster.json) |
| [11_residual_rmsnorm](../custom-cuda-kernels/reference/labs/11_residual_rmsnorm.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/11_residual_rmsnorm.json) |
| [12_capstone](../custom-cuda-kernels/reference/labs/12_capstone.md) | worker | [JSON](../custom-cuda-kernels/reference/grafana/12_capstone.json) |
| [13_h100_preflight](../custom-cuda-kernels/reference/labs/13_h100_preflight.md) | Not applicable | [JSON](../custom-cuda-kernels/reference/grafana/13_h100_preflight.json) |

### GPU Fundamentals

| Lab | Capture process | Dashboard |
| --- | --- | --- |
| [01_cpu_gpu_crossover](../gpu-fundamentals/reference/labs/01_cpu_gpu_crossover.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/01_cpu_gpu_crossover.json) |
| [02_tensor_core_precision](../gpu-fundamentals/reference/labs/02_tensor_core_precision.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/02_tensor_core_precision.json) |
| [03_transfer_and_pinning](../gpu-fundamentals/reference/labs/03_transfer_and_pinning.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/03_transfer_and_pinning.json) |
| [04_layout_and_coalescing](../gpu-fundamentals/reference/labs/04_layout_and_coalescing.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/04_layout_and_coalescing.json) |
| [05_roofline_microbench](../gpu-fundamentals/reference/labs/05_roofline_microbench.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/05_roofline_microbench.json) |
| [07_async_streams](../gpu-fundamentals/reference/labs/07_async_streams.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/07_async_streams.json) |
| [08_operator_to_kernels](../gpu-fundamentals/reference/labs/08_operator_to_kernels.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/08_operator_to_kernels.json) |
| [09_triton_launch_geometry](../gpu-fundamentals/reference/labs/09_triton_launch_geometry.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/09_triton_launch_geometry.json) |
| [10_compatibility_stack](../gpu-fundamentals/reference/labs/10_compatibility_stack.md) | Not applicable | [JSON](../gpu-fundamentals/reference/grafana/10_compatibility_stack.json) |
| [11_scheduler_tail](../gpu-fundamentals/reference/labs/11_scheduler_tail.md) | worker | [JSON](../gpu-fundamentals/reference/grafana/11_scheduler_tail.json) |
| [12_read_only_health](../gpu-fundamentals/reference/labs/12_read_only_health.md) | Not applicable | [JSON](../gpu-fundamentals/reference/grafana/12_read_only_health.json) |

### GPU Performance Optimization

| Lab | Capture process | Dashboard |
| --- | --- | --- |
| [01_timing_basics](../gpu-optimizations/reference/labs/01_timing_basics.md) | worker | [JSON](../gpu-optimizations/reference/grafana/01_timing_basics.json) |
| [02_sync_trap](../gpu-optimizations/reference/labs/02_sync_trap.md) | worker | [JSON](../gpu-optimizations/reference/grafana/02_sync_trap.json) |
| [03_compile_fusion](../gpu-optimizations/reference/labs/03_compile_fusion.md) | worker | [JSON](../gpu-optimizations/reference/grafana/03_compile_fusion.json) |
| [04_cuda_graphs](../gpu-optimizations/reference/labs/04_cuda_graphs.md) | worker | [JSON](../gpu-optimizations/reference/grafana/04_cuda_graphs.json) |
| [05_input_pipeline](../gpu-optimizations/reference/labs/05_input_pipeline.md) | worker | [JSON](../gpu-optimizations/reference/grafana/05_input_pipeline.json) |
| [07_profile_workload](../gpu-optimizations/reference/labs/07_profile_workload.md) | worker | [JSON](../gpu-optimizations/reference/grafana/07_profile_workload.json) |
| [09_capstone](../gpu-optimizations/reference/labs/09_capstone.md) | worker | [JSON](../gpu-optimizations/reference/grafana/09_capstone.json) |
| [10_shape_precision](../gpu-optimizations/reference/labs/10_shape_precision.md) | worker | [JSON](../gpu-optimizations/reference/grafana/10_shape_precision.json) |
| [12_allocator_lifetime](../gpu-optimizations/reference/labs/12_allocator_lifetime.md) | worker | [JSON](../gpu-optimizations/reference/grafana/12_allocator_lifetime.json) |
| [14_profiler_bottlenecks](../gpu-optimizations/reference/labs/14_profiler_bottlenecks.md) | worker | [JSON](../gpu-optimizations/reference/grafana/14_profiler_bottlenecks.json) |
| [15_tail_load_balance](../gpu-optimizations/reference/labs/15_tail_load_balance.md) | worker | [JSON](../gpu-optimizations/reference/grafana/15_tail_load_balance.json) |
| [16_library_first_decision](../gpu-optimizations/reference/labs/16_library_first_decision.md) | worker | [JSON](../gpu-optimizations/reference/grafana/16_library_first_decision.json) |
| [19_h2d_pipeline](../gpu-optimizations/reference/labs/19_h2d_pipeline.md) | worker | [JSON](../gpu-optimizations/reference/grafana/19_h2d_pipeline.json) |
| [20_d2h_pipeline](../gpu-optimizations/reference/labs/20_d2h_pipeline.md) | worker | [JSON](../gpu-optimizations/reference/grafana/20_d2h_pipeline.json) |

### LLM Inference

| Lab | Capture process | Dashboard |
| --- | --- | --- |
| [08_kv_cache](../llm-inference/reference/labs/08_kv_cache.md) | worker | [JSON](../llm-inference/reference/grafana/08_kv_cache.json) |
| [09_hf_prefill_decode](../llm-inference/reference/labs/09_hf_prefill_decode.md) | worker | [JSON](../llm-inference/reference/grafana/09_hf_prefill_decode.json) |
| [10_vllm_offline](../llm-inference/reference/labs/10_vllm_offline.md) | worker | [JSON](../llm-inference/reference/grafana/10_vllm_offline.json) |
| [11_serving_client](../llm-inference/reference/labs/11_serving_client.md) | server | [JSON](../llm-inference/reference/grafana/11_serving_client.json) |
| [15_streaming_client](../llm-inference/reference/labs/15_streaming_client.md) | server | [JSON](../llm-inference/reference/grafana/15_streaming_client.json) |
| [16_model_artifact_audit](../llm-inference/reference/labs/16_model_artifact_audit.md) | Not applicable | [JSON](../llm-inference/reference/grafana/16_model_artifact_audit.json) |
| [17_sampling_semantics](../llm-inference/reference/labs/17_sampling_semantics.md) | worker | [JSON](../llm-inference/reference/grafana/17_sampling_semantics.json) |
| [18_padding_bucketing](../llm-inference/reference/labs/18_padding_bucketing.md) | worker | [JSON](../llm-inference/reference/grafana/18_padding_bucketing.json) |
| [20_prefix_cache_client](../llm-inference/reference/labs/20_prefix_cache_client.md) | server | [JSON](../llm-inference/reference/grafana/20_prefix_cache_client.json) |
| [23_speculative_decoding](../llm-inference/reference/labs/23_speculative_decoding.md) | worker | [JSON](../llm-inference/reference/grafana/23_speculative_decoding.json) |
| [24_sdpa_attention](../llm-inference/reference/labs/24_sdpa_attention.md) | worker | [JSON](../llm-inference/reference/grafana/24_sdpa_attention.json) |
| [25_workload_metrics](../llm-inference/reference/labs/25_workload_metrics.md) | worker | [JSON](../llm-inference/reference/grafana/25_workload_metrics.json) |
| [26_kv_capacity](../llm-inference/reference/labs/26_kv_capacity.md) | Not applicable | [JSON](../llm-inference/reference/grafana/26_kv_capacity.json) |
| [27_paged_kv](../llm-inference/reference/labs/27_paged_kv.md) | Not applicable | [JSON](../llm-inference/reference/grafana/27_paged_kv.json) |
| [28_continuous_batching](../llm-inference/reference/labs/28_continuous_batching.md) | Not applicable | [JSON](../llm-inference/reference/grafana/28_continuous_batching.json) |
| [29_quantization](../llm-inference/reference/labs/29_quantization.md) | worker | [JSON](../llm-inference/reference/grafana/29_quantization.json) |
| [30_engine_profile](../llm-inference/reference/labs/30_engine_profile.md) | Not applicable | [JSON](../llm-inference/reference/grafana/30_engine_profile.json) |
| [32_inference_capstone](../llm-inference/reference/labs/32_inference_capstone.md) | worker | [JSON](../llm-inference/reference/grafana/32_inference_capstone.json) |
| [33_speculative_engine_client](../llm-inference/reference/labs/33_speculative_engine_client.md) | server | [JSON](../llm-inference/reference/grafana/33_speculative_engine_client.json) |
| [34_policy_equivalence_client](../llm-inference/reference/labs/34_policy_equivalence_client.md) | server | [JSON](../llm-inference/reference/grafana/34_policy_equivalence_client.json) |
| [35_inference_basics](../llm-inference/reference/labs/35_inference_basics.md) | optional-cuda | [JSON](../llm-inference/reference/grafana/35_inference_basics.json) |
| [36_kv_tiering](../llm-inference/reference/labs/36_kv_tiering.md) | Not applicable | [JSON](../llm-inference/reference/grafana/36_kv_tiering.json) |

### LLM Training

| Lab | Capture process | Dashboard |
| --- | --- | --- |
| [01_tiny_transformer_train](../llm-training/reference/labs/01_tiny_transformer_train.md) | worker | [JSON](../llm-training/reference/grafana/01_tiny_transformer_train.json) |
| [02_gradient_accumulation](../llm-training/reference/labs/02_gradient_accumulation.md) | worker | [JSON](../llm-training/reference/grafana/02_gradient_accumulation.json) |
| [05_lora_sft](../llm-training/reference/labs/05_lora_sft.md) | worker | [JSON](../llm-training/reference/grafana/05_lora_sft.json) |
| [06_grpo_objective](../llm-training/reference/labs/06_grpo_objective.md) | worker | [JSON](../llm-training/reference/grafana/06_grpo_objective.json) |
| [07_grpo_trainer](../llm-training/reference/labs/07_grpo_trainer.md) | worker | [JSON](../llm-training/reference/grafana/07_grpo_trainer.json) |
| [13_loss_masking](../llm-training/reference/labs/13_loss_masking.md) | worker | [JSON](../llm-training/reference/grafana/13_loss_masking.json) |
| [14_activation_checkpointing](../llm-training/reference/labs/14_activation_checkpointing.md) | worker | [JSON](../llm-training/reference/grafana/14_activation_checkpointing.json) |
| [21_mixed_precision_training](../llm-training/reference/labs/21_mixed_precision_training.md) | worker | [JSON](../llm-training/reference/grafana/21_mixed_precision_training.json) |
| [22_transformer_engine_fp8](../llm-training/reference/labs/22_transformer_engine_fp8.md) | worker | [JSON](../llm-training/reference/grafana/22_transformer_engine_fp8.json) |
| [24_checkpoint_resume](../llm-training/reference/labs/24_checkpoint_resume.md) | worker | [JSON](../llm-training/reference/grafana/24_checkpoint_resume.json) |
| [25_sequence_packing](../llm-training/reference/labs/25_sequence_packing.md) | worker | [JSON](../llm-training/reference/grafana/25_sequence_packing.json) |
| [26_input_pipeline](../llm-training/reference/labs/26_input_pipeline.md) | worker | [JSON](../llm-training/reference/grafana/26_input_pipeline.json) |
| [27_fused_graph_trace](../llm-training/reference/labs/27_fused_graph_trace.md) | worker | [JSON](../llm-training/reference/grafana/27_fused_graph_trace.json) |
| [30_training_profiler](../llm-training/reference/labs/30_training_profiler.md) | worker | [JSON](../llm-training/reference/grafana/30_training_profiler.json) |
| [31_training_capstone](../llm-training/reference/labs/31_training_capstone.md) | worker | [JSON](../llm-training/reference/grafana/31_training_capstone.json) |
| [32_learning_basics](../llm-training/reference/labs/32_learning_basics.md) | optional-cuda | [JSON](../llm-training/reference/grafana/32_learning_basics.json) |
