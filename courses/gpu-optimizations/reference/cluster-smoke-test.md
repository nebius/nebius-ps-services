# Cluster smoke-test runbook

**Hardware scope:** local GPU checks use one full, non-MIG H100. A qualified advanced cluster can supply one-GPU allocations. Distributed work follows the advanced course's separate fabric qualification.

This is a target-qualification checklist, not the lesson execution order.
Follow the [course](../index.html) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Run from the `gpu-optimizations` course root. Keep JSON results, Slurm output,
profiler reports, and environment metadata private. Share only a sanitized
summary that follows [evidence-security.md](evidence-security.md). A profiler
run explains behavior; its instrumented duration is not acceptance timing.

Complete shared preparation in the [Lab Guide](../../lab-guide.html#lab-preparation)
and review its completion summary. Follow the Lab Guide’s guidance for any failures or hardware skips before submitting.
Preparation creates private result/log directories
and records installed runtime identities; launchers restore that runtime.
Retain its receipts with the qualification evidence. Directory permissions do
not replace the cluster's storage and access-control policy.

## Compute-node tooling

```bash
sbatch slurm/tooling_preflight.sbatch
```

Record PyTorch, CUDA, driver, Nsight, and DCGM versions or explicit absence.
Serving-client qualification belongs to the LLM Inference environment.
Do not record hostnames, executable paths, GPU UUIDs, or
other asset identifiers in the shareable summary. Stop only the dependent
profiler exercise when a command or permission is unavailable; do not replace
missing evidence with a guess.

## Measurement discipline

```bash
sbatch slurm/01_timing_basics.sbatch --workload small
sbatch slurm/02_sync_trap.sbatch --workload small
```

Stop if the learner cannot explain the timed boundary or locate each synchronization.

## Practical profiler workshop

First preserve unprofiled baseline and candidate distributions:

```bash
sbatch slurm/14_profiler_bottlenecks.sbatch --workload small --case sync --mode baseline
sbatch slurm/14_profiler_bottlenecks.sbatch --workload small --case sync --mode optimized
sbatch slurm/14_profiler_bottlenecks.sbatch --workload small --case memory --mode baseline
sbatch slurm/14_profiler_bottlenecks.sbatch --workload small --case memory --mode optimized
sbatch slurm/14_profiler_bottlenecks.sbatch --workload small --case compute --mode optimized
```

Use Nsight Systems to compare synchronization and launch mechanisms:

```bash
sbatch slurm/14_profiler_bottlenecks.sbatch --profiler nsys --workload small --case sync --mode baseline
sbatch slurm/14_profiler_bottlenecks.sbatch --profiler nsys --workload small --case sync --mode optimized
sbatch slurm/14_profiler_bottlenecks.sbatch --profiler nsys --workload small --case launch --mode baseline
sbatch slurm/14_profiler_bottlenecks.sbatch --profiler nsys --workload small --case launch --mode optimized
```

Use Nsight Compute only after selecting the material range and kernel:

```bash
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy sbatch slurm/14_profiler_bottlenecks.sbatch --profiler ncu --workload small --case memory --mode baseline
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy sbatch slurm/14_profiler_bottlenecks.sbatch --profiler ncu --workload small --case memory --mode optimized
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy sbatch slurm/14_profiler_bottlenecks.sbatch --profiler ncu --workload small --case compute --mode optimized
```

Confirm every requested set and section name in the preflight output before submitting the Nsight Compute jobs; names and composition are tool-version dependent. The Nsight Systems evidence must identify timeline gaps, blocking APIs, kernel spacing, copies, or overlap. The Nsight Compute evidence must name the selected kernel and distinguish defensible algorithmic estimates from report-derived arithmetic intensity, achieved throughput, traffic, scheduler, stall, and occupancy evidence. The pointwise case has no portable algorithmic FLOP count. If profiler access is unavailable, record the exact blocker and do not claim those findings.

## Optimization mechanisms

```bash
sbatch slurm/10_shape_precision.sbatch --workload small
sbatch slurm/03_compile_fusion.sbatch --workload small
sbatch slurm/04_cuda_graphs.sbatch --workload small
sbatch slurm/05_input_pipeline.sbatch --workload small
sbatch slurm/12_allocator_lifetime.sbatch --workload small
```

Keep a change only if its correctness check and the predeclared end-to-end metric pass.
For Lab 05, retain the batch-ready gap, H2D event time, device-consumption event
time, and end-to-end wall time for every variant. These single-stream component
measurements are serialized evidence, not a claim of overlap; use a profiler to
prove overlap. Activation checkpointing is now Training Lab 14, where matched-state loss and
all trainable parameter gradients must pass before memory or timing is interpreted.
Attention/SDPA practice is now Inference Lab 24; these specialized exercises
remain in the learning path with their own course environments.

Qualify complete transfer paths separately from Lab 05's serialized components:

```bash
sbatch slurm/19_h2d_pipeline.sbatch --mode serial --slots 2
sbatch slurm/19_h2d_pipeline.sbatch --mode pipeline --slots 2
sbatch slurm/20_d2h_pipeline.sbatch --mode serial --slots 2
sbatch slurm/20_d2h_pipeline.sbatch --mode pipeline --slots 2
```

Follow the complete Lab 20 guide for the intervening workers, pooled and
nonblocking modes; the endpoints alone do not isolate their causes. Require
every batch to match, every output to be consumed once and the final drain to
complete. Keep workload, slot count and sink delay fixed between comparisons.
Use separate short Nsight runs to establish copy/compute overlap; CPU controls
or a lower wall time alone do not prove concurrency. Record bounded pool
capacity separately from measured host memory. The synthetic sink is not a
storage-throughput experiment.

## Scale and capstone

```bash
sbatch slurm/09_capstone.sbatch --workload small
```

Run the capstone on one GPU. Retain correctness, equivalent work and repeated
unprofiled timings before accepting a change.

## Tails and library-first escalation

```bash
sbatch slurm/15_tail_load_balance.sbatch --workload small
sbatch slurm/16_library_first_decision.sbatch --workload small
```

Distinguish warp divergence, variable block work, rank skew, and a partial final
wave. A proposed custom kernel must address a measured residual hotspot after
framework, compiler, and library options have been evaluated. Repeat accepted
candidates across independent trials and keep unavailable profiler or H100
evidence pending.

## Distributed practice

Use [Advanced Labs](../../advanced-gpu-communication/index.html) for placement,
transport, collective and application checks on its qualified two-worker,
sixteen-H100 fabric. Follow its lab-specific acceptance criteria; two one-GPU
TCP workers do not establish fabric performance. These checks are not gates
for the local experiments above.
