# Cluster smoke-test runbook

**Hardware scope:** run local checks on the base cluster. Distributed checks have moved to the dedicated advanced course and its two-eight-H100 cluster. Never use the TCP base pair as fabric optimization evidence.

This is a target-qualification checklist, not the lesson execution order.
Follow the [syllabus](../SYLLABUS.md) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

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
python3 tools/submit_lab.py --lab 07_profile_workload slurm/tooling_preflight.sbatch
```

Record PyTorch, CUDA, driver, Nsight, and DCGM versions or explicit absence.
Serving-client qualification belongs to the LLM Inference environment.
Do not record hostnames, executable paths, GPU UUIDs, or
other asset identifiers in the shareable summary. Stop only the dependent
profiler exercise when a command or permission is unavailable; do not replace
missing evidence with a guess.

## Gate 2: allocation and communication

Run distributed qualification and experiments from the [advanced lab course](../../advanced-gpu-communication/index.html), which owns their launchers, guides and dashboards.

Require two distinct nodes, one H100 per rank, world size 2, correct rank/device binding, non-MIG devices, NCCL initialization, and a correct reduction.

## Gate 3: measurement discipline

```bash
python3 tools/submit_lab.py --lab 01_timing_basics slurm/single_gpu.sbatch labs/01_timing_basics.py --profile small
python3 tools/submit_lab.py --lab 02_sync_trap slurm/single_gpu.sbatch labs/02_sync_trap.py --profile small
```

Stop if the learner cannot explain the timed boundary or locate each synchronization.

## Gate 4: practical profiler workshop

First preserve unprofiled baseline and candidate distributions:

```bash
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode baseline
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode optimized
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case memory --mode baseline
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case memory --mode optimized
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case compute --mode optimized
```

Use Nsight Systems to compare synchronization and launch mechanisms:

```bash
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode baseline
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode optimized
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case launch --mode baseline
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case launch --mode optimized
```

Use Nsight Compute only after selecting the material range and kernel:

```bash
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/ncu_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case memory --mode baseline
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/ncu_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case memory --mode optimized
NCU_SET=roofline NCU_SECTIONS=MemoryWorkloadAnalysis,SchedulerStats,WarpStateStats,Occupancy python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/ncu_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case compute --mode optimized
```

Confirm every requested set and section name in the preflight output before submitting the Nsight Compute jobs; names and composition are tool-version dependent. The Nsight Systems evidence must identify timeline gaps, blocking APIs, kernel spacing, copies, or overlap. The Nsight Compute evidence must name the selected kernel and distinguish defensible algorithmic estimates from report-derived arithmetic intensity, achieved throughput, traffic, scheduler, stall, and occupancy evidence. The pointwise case has no portable algorithmic FLOP count. If profiler access is unavailable, record the exact blocker and do not claim those findings.

## Gate 5: optimization mechanisms

```bash
python3 tools/submit_lab.py --lab 10_shape_precision slurm/single_gpu.sbatch labs/10_shape_precision.py --profile small
python3 tools/submit_lab.py --lab 03_compile_fusion slurm/single_gpu.sbatch labs/03_compile_fusion.py --profile small
python3 tools/submit_lab.py --lab 04_cuda_graphs slurm/single_gpu.sbatch labs/04_cuda_graphs.py --profile small
python3 tools/submit_lab.py --lab 05_input_pipeline slurm/single_gpu.sbatch labs/05_input_pipeline.py --profile small
python3 tools/submit_lab.py --lab 12_allocator_lifetime slurm/single_gpu.sbatch labs/12_allocator_lifetime.py --profile small
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
python3 tools/submit_lab.py --lab 19_h2d_pipeline slurm/single_gpu.sbatch labs/19_h2d_pipeline.py --mode serial --slots 2
python3 tools/submit_lab.py --lab 19_h2d_pipeline slurm/single_gpu.sbatch labs/19_h2d_pipeline.py --mode pipeline --slots 2
python3 tools/submit_lab.py --lab 20_d2h_pipeline slurm/single_gpu.sbatch labs/20_d2h_pipeline.py --mode serial --slots 2
python3 tools/submit_lab.py --lab 20_d2h_pipeline slurm/single_gpu.sbatch labs/20_d2h_pipeline.py --mode pipeline --slots 2
```

Follow the complete Lab 20 guide for the intervening workers, pooled and
nonblocking modes; the endpoints alone do not isolate their causes. Require
every batch to match, every output to be consumed once and the final drain to
complete. Keep workload, slot count and sink delay fixed between comparisons.
Use separate short Nsight runs to establish copy/compute overlap; CPU controls
or a lower wall time alone do not prove concurrency. Record bounded pool
capacity separately from measured host memory. The synthetic sink is not a
storage-throughput experiment.

## Gate 6: scale and capstone

```bash
python3 tools/submit_lab.py --lab 09_capstone slurm/single_gpu.sbatch labs/09_capstone.py --profile small
```

Run distributed qualification and experiments from the [advanced lab course](../../advanced-gpu-communication/index.html), which owns their launchers, guides and dashboards.

Compare the one-node and two-node scaling runs using the same global batch. Use the slowest-rank time for global throughput. Record speedup, scaling efficiency, exposed collective time, correctness, and memory. Run the capstone on one node because it is not a distributed program. For the overlap lab, require exact reduction and finite-compute gates, then use an Nsight Systems timeline before attributing any ratio to simultaneous communication and compute.

## Gate 7: tails and library-first escalation

```bash
python3 tools/submit_lab.py --lab 15_tail_load_balance slurm/single_gpu.sbatch labs/15_tail_load_balance.py --profile small
python3 tools/submit_lab.py --lab 16_library_first_decision slurm/single_gpu.sbatch labs/16_library_first_decision.py --profile small
```

Distinguish warp divergence, variable block work, rank skew, and a partial final
wave. A proposed custom kernel must address a measured residual hotspot after
framework, compiler, and library options have been evaluated. Repeat accepted
candidates across independent trials and keep unavailable profiler or H100
evidence pending.

## Networking workshop: qualification before tuning

After the advanced course's two-node preflight, qualify Advanced Lab 09 at a
small range, then the full curve. Advanced Lab 10 additionally requires the
reviewed external MPI-enabled NCCL Tests binary and a site-qualified MPI/Slurm
integration. Follow each lab's complete
guide for configuration, output interpretation and independent-run comparisons.

Run distributed qualification and experiments from the [advanced lab course](../../advanced-gpu-communication/index.html), which owns their launchers, guides and dashboards.

The dedicated launcher requires COURSE_NCCL_TESTS and COURSE_MPI supplied by
the learner. Do not pass its binary to torchrun. Require sixteen rank records
across two hosts, eight distinct GPUs per host, visible GPU zero per task,
complete checked rows and a successful
launcher exit. Keep diagnostic logs private and separate from timing runs.
Collect at least three independent baseline/candidate jobs; only then repeat
the candidate in scaling and overlap Labs 08/13.

Socket operation can satisfy the communication baseline. IB/RoCE and GDR
claims remain conditional on independent path evidence. Do not tune or repair
NICs, switches, kernel modules, security settings or Slurm as part of this
procedure. This checklist is pending live execution on the declared cluster.
