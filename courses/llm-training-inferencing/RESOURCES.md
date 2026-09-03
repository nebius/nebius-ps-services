# Official resources

## NVIDIA and PyTorch

- [NVIDIA Hopper Tuning Guide](https://docs.nvidia.com/cuda/hopper-tuning-guide/) — H100 hardware context.
- [NVIDIA Transformer Engine documentation](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/) — supported low-precision transformer training and inference behavior.
- [NVIDIA Transformer Engine FP8 delayed scaling](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/features/low_precision_training/fp8_delayed_scaling/fp8_delayed_scaling.html) — scale history, recipes, and current PyTorch autocast usage.
- [NVIDIA Transformer Engine getting started](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/getting_started/index.html) — current PyTorch integration and FP8 training boundary.
- [PyTorch Transformer building blocks tutorial](https://docs.pytorch.org/tutorials/intermediate/transformer_building_blocks.html) — transformer components and efficient PyTorch APIs.
- [PyTorch automatic mixed precision](https://docs.pytorch.org/docs/stable/amp.html) and [AMP recipe](https://docs.pytorch.org/tutorials/recipes/recipes/amp_recipe.html) — autocast, operation-specific dtypes, and gradient scaling.
- [PyTorch DistributedDataParallel](https://docs.pytorch.org/docs/stable/generated/torch.nn.parallel.DistributedDataParallel.html) — replicated data-parallel semantics.
- [PyTorch FSDP2 tutorial](https://docs.pytorch.org/tutorials/intermediate/FSDP_tutorial.html) — fully sharded training workflow.
- [PyTorch distributed](https://docs.pytorch.org/docs/stable/distributed.html) and [torchrun](https://docs.pytorch.org/docs/stable/elastic/run.html) — process groups, collectives, and launch.
- [PyTorch tensor parallel tutorial](https://docs.pytorch.org/tutorials/intermediate/TP_tutorial.html) — column/row sharding, DTensor layouts, and collective placement.
- [PyTorch pipeline parallelism](https://docs.pytorch.org/docs/stable/distributed.pipelining.html) — layer partitioning, microbatch schedules, and stage communication.
- [PyTorch context parallelism](https://docs.pytorch.org/tutorials/unstable/context_parallel.html) — long-context activation sharding and attention communication.
- [PyTorch activation checkpointing](https://docs.pytorch.org/docs/stable/checkpoint.html) — recomputation contract and cautions.
- [PyTorch saving and loading models](https://docs.pytorch.org/tutorials/beginner/saving_loading_models.html) — model, optimizer, and general-checkpoint state.
- [PyTorch Profiler](https://docs.pytorch.org/docs/stable/profiler.html), [Nsight Systems](https://docs.nvidia.com/nsight-systems/UserGuide/), and [Nsight Compute](https://docs.nvidia.com/nsight-compute/ProfilingGuide/) — framework, system-timeline, and selected-kernel evidence.
- [PyTorch scaled dot product attention](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html) — attention API behavior.
- [PyTorch CUDA semantics](https://docs.pytorch.org/docs/stable/notes/cuda.html) — CUDA Graph capture, replay, streams, and memory constraints.
- [NVIDIA Megatron Bridge performance tuning](https://docs.nvidia.com/nemo/megatron-bridge/latest/performance-guide.html) — model FLOP utilization and training-performance factors.
- [NVIDIA Megatron Core parallelism guide](https://docs.nvidia.com/megatron-core/developer-guide/latest/user-guide/parallelism-guide.html) — data, sharded, tensor, pipeline, context, and expert parallel strategy distinctions.

## Model training and adaptation

- [Hugging Face Transformers documentation](https://huggingface.co/docs/transformers/index) — model, tokenizer, loading, generation, and training APIs.
- [Hugging Face model loading](https://huggingface.co/docs/transformers/models) — config, safetensors, immutable revisions, and remote-code precautions.
- [Hugging Face model cards and license metadata](https://huggingface.co/docs/hub/model-cards) — intended use, limitations, provenance, and license identifiers.
- [Hugging Face generation](https://huggingface.co/docs/transformers/main_classes/text_generation) — generation configuration, sampling, stopping, and cache controls.
- [Hugging Face cache explanation](https://huggingface.co/docs/transformers/main/cache_explanation) — explicit attention-mask, cache-position, and custom generation-loop contracts.
- [Hugging Face PEFT LoRA](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora) — low-rank adaptation concepts and supported behavior.
- [Hugging Face TRL documentation](https://huggingface.co/docs/trl/index) — supervised and reinforcement-learning trainers, including GRPO.

## Inference and serving

- [vLLM documentation](https://docs.vllm.ai/en/stable/) — engine, serving, parallelism, metrics, quantization, prefix caching, and performance guidance.
- [vLLM output API](https://docs.vllm.ai/en/stable/api/vllm/outputs/) — request prompt-token IDs, completion token IDs, and finish-reason fields.
- [vLLM production metrics](https://docs.vllm.ai/en/stable/usage/metrics/) — request, scheduler, cache, TTFT, inter-token, and end-to-end metrics.
- [vLLM optimization and tuning](https://docs.vllm.ai/en/stable/configuration/optimization/) — memory, preemption, batching, and tuning guidance.
- [vLLM speculative decoding](https://docs.vllm.ai/en/stable/api/vllm/v1/spec_decode/index.html) — proposal families and verification components.
- [vLLM attention backend feature support](https://docs.vllm.ai/en/latest/design/attention_backends/) — backend capabilities and selection constraints.
- [vLLM Bench CLI](https://docs.vllm.ai/en/stable/benchmarking/cli/) — engine-native latency, throughput, startup, and online-serving benchmarks.
- [vLLM automatic prefix caching](https://docs.vllm.ai/en/stable/features/automatic_prefix_caching/) — exact-prefix KV reuse, enabling, workloads, and limitations.
- [vLLM LoRA adapters](https://docs.vllm.ai/en/stable/features/lora/) — per-request adapters, dynamic loading, lineage, and serving considerations.
- [vLLM multimodal inputs](https://docs.vllm.ai/en/stable/features/multimodal_inputs/) — multimodal input formats, limits, preprocessing, and model behavior.
- [vLLM parallelism and scaling](https://docs.vllm.ai/en/stable/serving/parallelism_scaling/) — supported multi-GPU and multi-node serving topology.
- [Slurm sbatch](https://slurm.schedmd.com/sbatch.html) and [Slurm srun](https://slurm.schedmd.com/srun.html) — cluster allocations and task launch.

Check [VERSIONS.md](VERSIONS.md) and re-verify version-sensitive behavior before changing the pinned stack.
