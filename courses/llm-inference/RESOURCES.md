# Official resources

1. [NVIDIA explanation of LLM inference, prefill and decode](https://developer.nvidia.com/blog/mastering-llm-techniques-inference-optimization/)
2. [SGLang authors: RadixAttention and prefix-tree KV reuse](https://www.lmsys.org/blog/2024-01-17-sglang/)
3. [TensorRT-LLM speculative decoding families and support boundaries](https://nvidia.github.io/TensorRT-LLM/features/speculative-decoding.html)
4. [NVIDIA Model Optimizer: Medusa heads and EAGLE feature drafting](https://nvidia.github.io/Model-Optimizer/guides/5_speculative_decoding.html)
5. [NVIDIA AIPerf and GenAI-Perf feature comparison](https://docs.nvidia.com/aiperf/getting-started/gen-ai-perf-vs-ai-perf-cli-feature-comparison-matrix)

6. [PyTorch scaled dot product attention](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html)
7. [PyTorch vector norms for aggregate tensor-error calculations](https://docs.pytorch.org/docs/2.14/generated/torch.linalg.vector_norm.html)
8. [PyTorch distributed tensor collectives: broadcast, all-gather and all-reduce](https://docs.pytorch.org/docs/2.14/distributed.html)
9. [Hugging Face generation strategies](https://huggingface.co/docs/transformers/main/en/generation_strategies)
10. [vLLM documentation](https://docs.vllm.ai/en/v0.28.0/)
11. [vLLM 0.28 serve options](https://docs.vllm.ai/en/v0.28.0/cli/serve/)
12. [vLLM 0.28 speculative decoding configuration](https://docs.vllm.ai/en/v0.28.0/api/vllm/config/speculative/)
13. [TensorRT-LLM documentation](https://nvidia.github.io/TensorRT-LLM/latest/index.html)
14. [NVIDIA TensorRT-LLM Triton backend](https://github.com/triton-inference-server/tensorrtllm_backend)
15. [TensorRT-LLM performance tuning](https://nvidia.github.io/TensorRT-LLM/performance/performance-tuning-guide/index.html)
16. [TensorRT-LLM quantization and NVIDIA Model Optimizer](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html)
17. [TensorRT-LLM MHA, MQA, GQA, and XQA](https://nvidia.github.io/TensorRT-LLM/features/attention.html)
18. [Triton TensorRT-LLM backend](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/tensorrtllm_backend/README.html)
19. [NVIDIA AIPerf overview](https://docs.nvidia.com/nim/benchmarking/llm/latest/overview.html)
20. [NVIDIA AIPerf metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html)
21. [AIPerf token, chunk, and completion metric definitions](https://docs.nvidia.com/aiperf/reference/ai-perf-metrics-reference)
22. [NVIDIA AIPerf command-line options](https://docs.nvidia.com/aiperf/reference/command-line-options)
23. [vLLM parallel configuration](https://docs.vllm.ai/en/v0.28.0/api/vllm/config/parallel/)
24. [NVIDIA Dynamo disaggregated serving](https://docs.nvidia.com/dynamo/components/router/disaggregated-serving)
25. [NVIDIA explanation of prefill, first-token generation, and subsequent decode](https://developer.nvidia.com/blog/?p=95274)
26. [NVIDIA Dynamo KV-aware routing](https://docs.nvidia.com/dynamo/dev/knowledge-base/concepts/system-architecture/kv-aware-routing)
27. [NVIDIA Dynamo RDMA overview and required platform support](https://docs.nvidia.com/dynamo/dev/kubernetes/installation/rdma-setup/overview)
28. [Slurm sbatch](https://slurm.schedmd.com/sbatch.html)

## Transfer and retention mechanisms

1. [GPUDirect Storage design and control/data paths](https://docs.nvidia.com/gpudirect-storage/design-guide/index.html)
2. [GPUDirect Storage direct and compatibility paths](https://docs.nvidia.com/gpudirect-storage/o-direct-guide/)
3. [Dynamo KV-cache offload tiers](https://docs.nvidia.com/dynamo/latest/kubernetes/kv-cache-offloading/overview)
4. [Exact speculative sampling and recovery (original paper)](https://arxiv.org/abs/2211.17192)
5. [FlashAttention: tiled attention and online normalization (original paper)](https://arxiv.org/abs/2205.14135)
6. [TensorRT-LLM context/generation phases and in-flight batching](https://nvidia.github.io/TensorRT-LLM/advanced/gpt-attention.html)
7. [NVIDIA Model Optimizer quantization and export](https://nvidia.github.io/Model-Optimizer/guides/1_quantization.html)
8. [CUDA Graphs: explicit construction and stream capture](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/cuda-graphs.html)
9. [vLLM preemption and request resumption](https://docs.vllm.ai/en/latest/configuration/optimization/)
10. [Transformers custom-model loading and remote-code precautions](https://huggingface.co/docs/transformers/models#custom-models)

## Advanced GPU fabric experiments

1. [Nebius GPU platforms and presets](https://docs.nebius.com/compute/virtual-machines/types) — match the selected eight-GPU H100 preset and supported GPU cluster.
2. [NVIDIA nvbandwidth, reviewed source](https://github.com/NVIDIA/nvbandwidth/tree/82fc4e8c6afa0babb8687793678f615b3b8d793e) — single-node directed peer traffic and verification.
3. [RDMA perftest, reviewed source](https://github.com/linux-rdma/perftest/tree/b513a77278c8061ca6c4dcd1a95d08801c6e7623) — validated host/CUDA buffers and queue-depth experiments.
4. [GPUDirect RDMA](https://docs.nvidia.com/cuda/gpudirect-rdma/) — registration and platform prerequisites.
5. [PyTorch profiler recipe](https://docs.pytorch.org/tutorials/recipes/recipes/profiler_recipe.html) — scheduled CPU/CUDA traces, shapes and memory.
6. [Nsight Systems user guide](https://docs.nvidia.com/nsight-systems/UserGuide/index.html) — distributed process captures and NCCL timelines.
