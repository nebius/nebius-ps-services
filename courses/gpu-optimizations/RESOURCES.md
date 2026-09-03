# Official resources

- [NVIDIA Nsight Systems User Guide](https://docs.nvidia.com/nsight-systems/UserGuide/) — application timelines, CUDA API activity, kernels, transfers, synchronization, and distributed tracing guidance.
- [NVIDIA Nsight Systems Installation Guide](https://docs.nvidia.com/nsight-systems/InstallationGuide/) — supported installation paths and CLI-only package guidance.
- [NVIDIA Nsight Compute Profiling Guide](https://docs.nvidia.com/nsight-compute/ProfilingGuide/) — replay overhead, section sets, kernel metrics, and interpretation constraints.
- [NVIDIA Nsight Compute CLI Guide](https://docs.nvidia.com/nsight-compute/NsightComputeCli/) — command-line collection, NVTX filtering, section sets, and report export.
- [NVIDIA CUDA Best Practices Guide](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/) — measurement, memory access, concurrency, and optimization guidance.
- [NVIDIA Hopper Tuning Guide](https://docs.nvidia.com/cuda/hopper-tuning-guide/) — H100 resource and feature context.
- [NVIDIA DCGM profiling guide](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html) — interval telemetry, supported fields, and coordination with developer profilers.
- [NVIDIA DCGM getting started](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/getting-started.html) — supported package selection, host-engine setup, and command verification.
- [NVIDIA DCGM Exporter installation](https://docs.nvidia.com/datacenter/dcgm/latest/installation/install-dcgm-exporter.html) — Prometheus exposition, lifecycle ownership, and supported deployment paths.
- [NVIDIA nvbandwidth](https://github.com/NVIDIA/nvbandwidth) — host/device and device/device bandwidth and latency test cases.
- [NVIDIA NCCL Tests](https://github.com/NVIDIA/nccl-tests) — collective correctness and message-size performance curves.
- [NVIDIA LLM Benchmarking Guide](https://docs.nvidia.com/nim/benchmarking/llm/latest/overview.html) — AIPerf workload construction and inference metric definitions.
- [NVIDIA AIPerf documentation](https://docs.nvidia.com/aiperf/) — current NVIDIA client workflow for generative-AI performance measurement.
- [NVIDIA GenAI-Perf project](https://github.com/triton-inference-server/perf_analyzer/blob/main/genai-perf/README.md) — legacy client usage and the official transition notice to AIPerf.
- [vLLM Bench CLI](https://docs.vllm.ai/en/stable/cli/bench/) — engine-native latency, throughput, startup, and online-serving benchmarks.
- [MLPerf Training](https://mlcommons.org/benchmarks/training/) — standardized time-to-quality methodology and rules.
- [MLPerf Inference: Datacenter](https://mlcommons.org/benchmarks/inference-datacenter/) — standardized inference scenarios, metrics, quality targets, and rules.
- [PyTorch CUDA semantics](https://docs.pytorch.org/docs/stable/notes/cuda.html) — timing, streams, TF32, allocator, and CUDA Graph behavior.
- [PyTorch Profiler](https://docs.pytorch.org/docs/stable/profiler.html) — profiling API and activity selection.
- [PyTorch NVTX API](https://docs.pytorch.org/docs/stable/generated/torch.cuda.nvtx.range.html) — semantic CUDA timeline ranges from PyTorch code.
- [PyTorch `torch.compile`](https://docs.pytorch.org/docs/stable/torch.compiler.html) — compiler behavior and troubleshooting entry points.
- [PyTorch user-defined Triton kernel tutorial](https://docs.pytorch.org/tutorials/recipes/torch_compile_user_defined_triton_kernel_tutorial.html) — custom kernel definitions, launch grids, and compiler integration.
- [PyTorch scaled dot product attention](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html) — SDPA contract and backend selection controls.
- [PyTorch distributed](https://docs.pytorch.org/docs/stable/distributed.html) and [torchrun](https://docs.pytorch.org/docs/stable/elastic/run.html) — collectives and distributed launch.
- [Slurm sbatch](https://slurm.schedmd.com/sbatch.html) and [Slurm srun](https://slurm.schedmd.com/srun.html) — job allocation and task launch.

Recheck all version-sensitive behavior when changing [VERSIONS.md](VERSIONS.md).
