# Cluster smoke-test runbook

**Hardware scope:** run local checks on the base cluster. Distributed checks have moved to the dedicated advanced course and its two-eight-H100 cluster. Never use the TCP base pair as fabric optimization evidence.

Run from the `gpu-fundamentals` course root after activating a cluster-approved environment that satisfies [VERSIONS.md](../VERSIONS.md). Keep each Slurm output file and JSON result private; never overwrite an earlier run. Share only a sanitized summary that follows [evidence-security.md](evidence-security.md).

Complete the one-time directory preparation in the shared Lab Guide before submitting. Keep runtime evidence in those private result directories.

This runbook is a complete platform-qualification checklist, not the teaching
order. Learners follow the [syllabus](../SYLLABUS.md): Lab 10 checks the local
environment before the first single-GPU experiment, and the two-node gate
below is required before the final collective lesson. A full qualification
campaign may run these gates together after the concepts have been studied.

## Gate 1: prove the two-node allocation

Run distributed qualification and experiments from the [advanced lab course](../../advanced-gpu-communication/index.html), which owns their launchers, guides and dashboards.

Accept only two distinct hostnames, world size 2, local rank 0 on each node, one visible H100 per rank, no MIG device, successful NCCL initialization, and a correct all-reduce.

## Gate 2: isolate one-GPU fundamentals

Submit in this order:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" slurm/01_cpu_gpu_crossover.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_tensor_core_precision/logs/%j.out" \
  --error="$PWD/results/02_tensor_core_precision/logs/%j.err" slurm/02_tensor_core_precision.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/03_transfer_and_pinning/logs/%j.out" \
  --error="$PWD/results/03_transfer_and_pinning/logs/%j.err" slurm/03_transfer_and_pinning.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_layout_and_coalescing/logs/%j.out" \
  --error="$PWD/results/04_layout_and_coalescing/logs/%j.err" slurm/04_layout_and_coalescing.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_roofline_microbench/logs/%j.out" \
  --error="$PWD/results/05_roofline_microbench/logs/%j.err" slurm/05_roofline_microbench.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/07_async_streams/logs/%j.out" \
  --error="$PWD/results/07_async_streams/logs/%j.err" slurm/07_async_streams.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_operator_to_kernels/logs/%j.out" \
  --error="$PWD/results/08_operator_to_kernels/logs/%j.err" slurm/08_operator_to_kernels.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_triton_launch_geometry/logs/%j.out" \
  --error="$PWD/results/09_triton_launch_geometry/logs/%j.err" slurm/09_triton_launch_geometry.sbatch --workload small
```

For each job, write the prediction first, require the lab's correctness result,
and compare distributions rather than one sample. For Lab 04, preserve the
logical layouts and strides, raw operation distributions, bandwidth estimates
based on logical input/output bytes, repack-copy cost, and computed reuse break-even. A result where
the packed operation is not faster is valid evidence and must report that no
finite break-even exists.

## Gate 3: exercise both nodes

Run distributed qualification and experiments from the [advanced lab course](../../advanced-gpu-communication/index.html), which owns their launchers, guides and dashboards.

Accept only an exact reduction and a JSON result that records rank count,
message size, software versions, and timing/bandwidth evidence. Portable JSON
uses a random course run ID, not a scheduler job ID. Preserve the Slurm output
privately with the JSON result when you need hostname, rank-placement, or
site-topology evidence; do not publish those infrastructure identifiers.

## Gate 4: broaden only after smoke passes

Repeat selected labs with their supported `small` or `large` profiles. Change one factor at a time. A changed dtype, shape, rank count, power state, process placement, or software version starts a new comparison series.

## Gate 5: compatibility, scheduling, and read-only health

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/10_compatibility_stack/logs/%j.out" \
  --error="$PWD/results/10_compatibility_stack/logs/%j.err" slurm/10_compatibility_stack.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/11_scheduler_tail/logs/%j.out" \
  --error="$PWD/results/11_scheduler_tail/logs/%j.err" slurm/11_scheduler_tail.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/12_read_only_health/logs/%j.out" \
  --error="$PWD/results/12_read_only_health/logs/%j.err" slurm/12_read_only_health.sbatch --workload small
```

Record the wheel/runtime/driver/toolkit roles separately. The lane-work model
preserves total useful work; the grid-tail probe preserves work per program
but varies total program count. Keep that distinction and the stated SM count
with the result. Read-only health evidence must not
change clocks, sharing modes, or error state. Missing operational fields mean
unavailable evidence. Review health changes alongside timing rather than
attributing a slow run to one isolated counter.
