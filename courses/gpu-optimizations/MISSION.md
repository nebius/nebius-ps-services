# Mission

## Learner

This course is for engineers who already understand basic GPU execution and need a repeatable method for making PyTorch workloads faster without breaking correctness or optimizing the wrong boundary.

## Capability after the course

Given a slow one- or two-node workload, the learner can build a trustworthy baseline, collect progressively stronger evidence, classify the dominant limiter, apply one targeted change, and decide whether to keep or reject it.

## Learning outcomes

By the end, the learner can:

1. Map user-visible latency or throughput to the stages, resources, and dependencies that constrain it, then use Amdahl and roofline reasoning without treating either model as proof.
2. Classify the dominant timed limiter as compute, memory bandwidth, launch/CPU, communication, or input/storage, while treating transfer and synchronization as cross-cutting mechanisms and capacity as a feasibility constraint.
3. Define workload, correctness, environment, stability controls, timing boundary, and success criteria before changing code.
4. Avoid asynchronous timing and implicit-synchronization traps.
5. Install or request the supported diagnostic tools, verify them inside a Slurm allocation, and record unavailable capabilities without inventing substitute evidence.
6. Use PyTorch Profiler and NVTX to connect framework phases and operators to CUDA work; use Nsight Systems for application timelines; and use Nsight Compute and roofline for a selected kernel.
7. Interpret effective workload TFLOP/s, arithmetic intensity, traffic, throughput, occupancy, and scheduler evidence without confusing application estimates with hardware peak or issued operations.
8. Use `nvidia-smi` and DCGM for device, health, and interval triage, and use GenAI-Perf or AIPerf for serving workload metrics without claiming that any of them identifies a kernel root cause.
9. Reason about grid waves, launch geometry, warp divergence, coalescing, shared-memory conflicts, register pressure, spills, and occupancy as interacting kernel mechanisms.
10. Evaluate precision, shape, layout, fusion, compilation, CUDA Graphs, SDPA, checkpointing, and pipeline changes.
11. Separate local optimization from fixed-work two-node scaling and reason about exposed communication.
12. Produce a capstone comparison that includes distributions, correctness, memory, repeatability, and rejected hypotheses.

## Runtime contract

- Slurm cluster with two worker nodes and one full non-MIG H100 per node.
- One-node studies reserve one H100 through Slurm.
- Distributed studies request both nodes and launch one process per node with Slurm and `torchrun`.
- Nsight access is site-dependent and must be validated before profiler labs.
- Results are conditional on recorded shapes, dtypes, versions, topology, and power/clock state.

## Non-goals

The course does not teach custom CUDA kernel implementation, promise universal speedups, or treat profiler counters as automatic conclusions. It optimizes measured PyTorch workloads and requires equivalence or declared numerical acceptance for every kept change.
