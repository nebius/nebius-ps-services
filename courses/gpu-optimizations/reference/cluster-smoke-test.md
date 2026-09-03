# Cluster smoke-test runbook

Run from the `gpu-optimizations` course root. Keep JSON results, Slurm output,
profiler reports, and environment metadata private. Share only a sanitized
summary that follows [evidence-security.md](evidence-security.md). A profiler
run explains behavior; its instrumented duration is not acceptance timing.

Before submitting jobs, restrict files created by the submitting shell:

```bash
umask 077
```

The launchers repeat this setting for child-process artifacts. It does not
replace the cluster's storage and access-control policy.

Confirm that the cluster owner supplied a platform-specific hash-locked
environment or immutable image digest. `requirements.txt` is only a direct
compatibility constraint. Stop before live execution if the approved lock or
image identity is unavailable.

## Gate 1: compute-node tooling

```bash
sbatch slurm/tooling_preflight.sbatch
```

Record PyTorch, CUDA, driver, Nsight, DCGM, GenAI-Perf, and AIPerf versions or
explicit absence. Do not record hostnames, executable paths, GPU UUIDs, or
other asset identifiers in the shareable summary. Stop only the dependent
profiler exercise when a command or permission is unavailable; do not replace
missing evidence with a guess.

## Gate 2: allocation and communication

```bash
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

Require two distinct nodes, one H100 per rank, world size 2, correct rank/device binding, non-MIG devices, NCCL initialization, and a correct reduction.

## Gate 3: measurement discipline

```bash
sbatch slurm/single_gpu.sbatch labs/01_timing_basics.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_sync_trap.py --profile smoke
```

Stop if the learner cannot explain the timed boundary or locate each synchronization.

## Gate 4: practical profiler workshop

First preserve unprofiled baseline and candidate distributions:

```bash
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode baseline
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode optimized
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case memory --mode baseline
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case memory --mode optimized
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case compute --mode optimized
```

Use Nsight Systems to compare synchronization and launch mechanisms:

```bash
sbatch slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode baseline
sbatch slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode optimized
sbatch slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case launch --mode baseline
sbatch slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case launch --mode optimized
```

Use Nsight Compute only after selecting the material range and kernel:

```bash
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy sbatch slurm/ncu_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case memory --mode baseline
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy sbatch slurm/ncu_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case memory --mode optimized
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy sbatch slurm/ncu_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case compute --mode optimized
```

Confirm every requested set and section name in the preflight output before submitting the Nsight Compute jobs; names and composition are tool-version dependent. The Nsight Systems evidence must identify timeline gaps, blocking APIs, kernel spacing, copies, or overlap. The Nsight Compute evidence must name the selected kernel and distinguish defensible algorithmic estimates from report-derived arithmetic intensity, achieved throughput, traffic, scheduler, stall, and occupancy evidence. The pointwise case has no portable algorithmic FLOP count. If profiler access is unavailable, record the exact blocker and do not claim those findings.

## Gate 5: optimization mechanisms

```bash
sbatch slurm/single_gpu.sbatch labs/10_shape_precision.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/03_compile_fusion.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/04_cuda_graphs.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/05_input_pipeline.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/06_activation_checkpointing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/11_sdpa_attention.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/12_allocator_lifetime.py --profile smoke
```

Keep a change only if its correctness check and the predeclared end-to-end metric pass.
For Lab 05, retain the batch-ready gap, H2D event time, device-consumption event
time, and end-to-end wall time for every variant. These single-stream component
measurements are serialized evidence, not a claim of overlap; use a profiler to
prove overlap. For Lab 06, require the bounded matched-state BF16 probe to pass
loss and full input/layer-gradient tolerances before interpreting memory or
timing differences.

## Gate 6: scale and capstone

```bash
sbatch slurm/single_gpu.sbatch labs/08_distributed_scaling.py --profile smoke
sbatch slurm/two_node.sbatch labs/08_distributed_scaling.py --profile smoke
sbatch slurm/two_node.sbatch labs/13_collective_overlap.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/09_capstone.py --profile smoke
```

Compare the one-node and two-node scaling runs using the same global batch. Use the slowest-rank time for global throughput. Record speedup, scaling efficiency, exposed collective time, correctness, and memory. Run the capstone on one node because it is not a distributed program. For the overlap lab, require exact reduction and finite-compute gates, then use an Nsight Systems timeline before attributing any ratio to simultaneous communication and compute.
