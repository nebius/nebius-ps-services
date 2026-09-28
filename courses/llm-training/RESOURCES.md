# Official resources

1. [PyTorch beginner tutorial: optimizing model parameters](https://docs.pytorch.org/tutorials/beginner/basics/optimization_tutorial.html)
2. [PyTorch beginner tutorial: automatic differentiation](https://docs.pytorch.org/tutorials/beginner/basics/autogradqs_tutorial.html)
3. [Transformer Engine NVFP4 and stochastic rounding: Blackwell comparison, not an H100 lab](https://nvidia.github.io/TransformerEngine/features/low_precision_training/nvfp4/nvfp4.html)

4. [PyTorch cross-entropy loss, logits and ignored labels](https://docs.pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html)
5. [PyTorch automatic mixed precision](https://docs.pytorch.org/docs/stable/amp.html)
6. [PyTorch distributed overview](https://docs.pytorch.org/tutorials/beginner/dist_overview.html)
7. [PyTorch DistributedDataParallel gradient semantics](https://docs.pytorch.org/docs/stable/generated/torch.nn.parallel.DistributedDataParallel.html)
8. [Hugging Face padding-free training and sample boundaries](https://huggingface.co/docs/transformers/main/padding_free)
9. [PyTorch FSDP2 tutorial](https://docs.pytorch.org/tutorials/intermediate/FSDP_tutorial.html)
10. [PyTorch activation checkpointing](https://docs.pytorch.org/docs/stable/checkpoint.html)
11. [NVIDIA Transformer Engine](https://docs.nvidia.com/deeplearning/transformer-engine/)
12. [Transformer Engine PyTorch API](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/api/pytorch.html)
13. [Transformer Engine low-precision training introduction](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/features/low_precision_training/introduction/introduction.html)
14. [Transformer Engine FP8 recipes](https://docs.nvidia.com/deeplearning/transformer-engine/user-guide/api/common.html)
15. [Megatron Core parallelism guide](https://docs.nvidia.com/megatron-core/developer-guide/latest/user-guide/parallelism-guide.html)
16. [Megatron Core distributed optimizer](https://docs.nvidia.com/megatron-core/developer-guide/latest/user-guide/features/dist_optimizer.html)
17. [Megatron Core mixture-of-experts guidance](https://docs.nvidia.com/megatron-core/developer-guide/latest/user-guide/features/moe.html)
18. [NVIDIA CUTLASS grouped GEMM scheduling](https://docs.nvidia.com/cutlass/latest/media/docs/cpp/grouped_scheduler.html)
19. [Hugging Face PEFT LoRA](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora)
20. [Hugging Face TRL GRPO Trainer](https://huggingface.co/docs/trl/main/en/grpo_trainer)
21. [Slurm sbatch](https://slurm.schedmd.com/sbatch.html)

## Transfer and retention mechanisms

1. [DDP communication hooks and PowerSGD state](https://docs.pytorch.org/docs/2.14/ddp_comm_hooks.html)
2. [DDP bucket sizing and hook contract](https://docs.pytorch.org/docs/2.14/generated/torch.nn.parallel.DistributedDataParallel.html)
3. [NVIDIA Megatron activation recomputation: full and selective granularity](https://docs.nvidia.com/nemo/megatron-bridge/latest/training/activation-recomputation.html)
4. [CUDA Graphs: explicit construction and stream capture](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/cuda-graphs.html)

## Advanced GPU fabric experiments

1. [Nebius GPU platforms and presets](https://docs.nebius.com/compute/virtual-machines/types) — match the selected eight-GPU H100 preset and supported GPU cluster.
2. [NVIDIA nvbandwidth, reviewed source](https://github.com/NVIDIA/nvbandwidth/tree/82fc4e8c6afa0babb8687793678f615b3b8d793e) — single-node directed peer traffic and verification.
3. [RDMA perftest, reviewed source](https://github.com/linux-rdma/perftest/tree/b513a77278c8061ca6c4dcd1a95d08801c6e7623) — validated host/CUDA buffers and queue-depth experiments.
4. [GPUDirect RDMA](https://docs.nvidia.com/cuda/gpudirect-rdma/) — registration and platform prerequisites.
5. [PyTorch profiler recipe](https://docs.pytorch.org/tutorials/recipes/recipes/profiler_recipe.html) — scheduled CPU/CUDA traces, shapes and memory.
6. [Nsight Systems user guide](https://docs.nvidia.com/nsight-systems/UserGuide/index.html) — distributed process captures and NCCL timelines.
