# Official resources

1. [CUDA programming model and CPU/GPU responsibilities](https://docs.nvidia.com/cuda/cuda-programming-guide/01-introduction/programming-model.html)

2. [PyTorch performance tuning guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html)
3. [PyTorch scalar extraction with Tensor.item](https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.item.html)
4. [PyTorch Profiler](https://docs.pytorch.org/docs/stable/profiler.html)
5. [torch.compile](https://docs.pytorch.org/docs/stable/generated/torch.compile.html)
6. [PyTorch CUDA Graphs](https://docs.pytorch.org/docs/stable/notes/cuda.html#cuda-graphs)
7. [NVIDIA CUDA Graphs: nodes, dependencies, capture, instantiation and updates](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/cuda-graphs.html)
8. [NVIDIA Nsight Systems](https://docs.nvidia.com/nsight-systems/UserGuide/index.html)
9. [NVIDIA Nsight Compute Profiling Guide](https://docs.nvidia.com/nsight-compute/ProfilingGuide/)
10. [CUDA C++ Best Practices Guide](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/)
11. [CUDA Runtime event timing](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__EVENT.html)
12. [NVIDIA DALI user guide](https://docs.nvidia.com/deeplearning/dali/user-guide/docs/)
13. [CUDA Runtime memory-pool API](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__MEMORY__POOLS.html)
14. [NCCL documentation](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/)
15. [Slurm sbatch](https://slurm.schedmd.com/sbatch.html)
16. [NCCL overview and supported communication paths](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/overview.html)
17. [NVIDIA DGX H100 system and network topology](https://docs.nvidia.com/dgx/dgxh100-user-guide/introduction-to-dgxh100.html)
18. [NVIDIA RDMA architecture, registration and work queues](https://docs.nvidia.com/rdma-aware-networks-programming-user-manual-1-7.pdf)
19. [NVIDIA DGX SuperPOD H100 compute and management fabrics](https://docs.nvidia.com/dgx-superpod/reference-architecture-scalable-infrastructure-h100/latest/network-fabrics.html)
20. [NCCL GPU-to-NIC registration and topology troubleshooting](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting/gpu_troubleshooting.html)
21. [NCCL network diagnostics and RoCE GID guidance](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting/networking_troubleshooting.html)
22. [NCCL environment-variable definitions and warnings](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html)
23. [NCCL performance triage and tuning](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting/performance_and_tuning.html)
24. [NVIDIA NCCL Tests v2.20.0 build and CLI](https://github.com/NVIDIA/nccl-tests/blob/v2.20.0/README.md)
25. [NCCL Tests collective bandwidth formulas](https://github.com/NVIDIA/nccl-tests/blob/v2.20.0/doc/PERFORMANCE.md)
26. [NCCL Tests v2.20.0 table writer and environment-export behavior](https://github.com/NVIDIA/nccl-tests/blob/v2.20.0/src/util.cu)
27. [NCCL Tests timing and device mapping implementation](https://github.com/NVIDIA/nccl-tests/blob/v2.20.0/src/common.cu)
28. [Slurm MPI integration guide](https://slurm.schedmd.com/mpi_guide.html)

## Transfer and retention mechanisms

1. [CUDA asynchronous execution](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/asynchronous-execution.html)
2. [CUDA pinned memory and overlapping transfers](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/index.html)
3. [PyTorch pinned and nonblocking transfer safety](https://docs.pytorch.org/tutorials/intermediate/pinmem_nonblock.html)
4. [CUDA timing with CPU timers and CUDA events](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/#timing)
5. [PyTorch compiler graph breaks](https://docs.pytorch.org/docs/stable/compile/programming_model.graph_breaks_index.html)

## Advanced GPU fabric experiments

1. [Nebius GPU platforms and presets](https://docs.nebius.com/compute/virtual-machines/types) — match the selected eight-GPU H100 preset and supported GPU cluster.
2. [NVIDIA nvbandwidth, reviewed source](https://github.com/NVIDIA/nvbandwidth/tree/82fc4e8c6afa0babb8687793678f615b3b8d793e) — single-node directed peer traffic and verification.
3. [RDMA perftest, reviewed source](https://github.com/linux-rdma/perftest/tree/b513a77278c8061ca6c4dcd1a95d08801c6e7623) — validated host/CUDA buffers and queue-depth experiments.
4. [GPUDirect RDMA](https://docs.nvidia.com/cuda/gpudirect-rdma/) — registration and platform prerequisites.
5. [PyTorch profiler recipe](https://docs.pytorch.org/tutorials/recipes/recipes/profiler_recipe.html) — scheduled CPU/CUDA traces, shapes and memory.
6. [Nsight Systems user guide](https://docs.nvidia.com/nsight-systems/UserGuide/index.html) — distributed process captures and NCCL timelines.
