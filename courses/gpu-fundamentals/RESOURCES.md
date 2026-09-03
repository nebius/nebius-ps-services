# Official resources

These public official sources supplement the reference list in the portable HTML.

- [NVIDIA CUDA Programming Guide](https://docs.nvidia.com/cuda/cuda-programming-guide/) — execution hierarchy, SIMT behavior, memory spaces, synchronization, and asynchronous execution.
- [NVIDIA Hopper Tuning Guide](https://docs.nvidia.com/cuda/hopper-tuning-guide/) — H100-relevant SM resources, Tensor Memory Accelerator, occupancy constraints, and Hopper tuning guidance.
- [NVIDIA Hopper Architecture In-Depth](https://developer.nvidia.com/blog/nvidia-hopper-architecture-in-depth/) — public GH100/H100 hierarchy, SM, Tensor Core, memory, and product-configuration context.
- [NVIDIA H100 Tensor Core GPU](https://www.nvidia.com/en-us/data-center/h100/) — vendor product overview; do not substitute peak specifications for measured application performance.
- [PyTorch CUDA semantics](https://docs.pytorch.org/docs/stable/notes/cuda.html) — asynchronous execution, streams, events, TF32, memory management, and CUDA Graph notes.
- [PyTorch Profiler](https://docs.pytorch.org/docs/stable/profiler.html) — operator and device activity collection.
- [PyTorch distributed](https://docs.pytorch.org/docs/stable/distributed.html) — process groups and collective APIs.
- [PyTorch torchrun](https://docs.pytorch.org/docs/stable/elastic/run.html) — distributed worker launch contract.
- [PyTorch user-defined Triton kernel tutorial](https://docs.pytorch.org/tutorials/recipes/torch_compile_user_defined_triton_kernel_tutorial.html) — Python kernel definitions, launch grids, and compiler integration.
- [Slurm sbatch](https://slurm.schedmd.com/sbatch.html) and [Slurm srun](https://slurm.schedmd.com/srun.html) — allocation and job-step behavior.

Version-sensitive claims must be rechecked when [VERSIONS.md](VERSIONS.md) changes.
