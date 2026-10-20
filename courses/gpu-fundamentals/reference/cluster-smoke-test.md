# Cluster smoke-test runbook

**Hardware scope:** local GPU checks use one full, non-MIG H100. A qualified advanced cluster can supply one-GPU allocations. Distributed work follows the advanced course's separate fabric qualification.

Run from the `gpu-fundamentals` course root with the prepared runtime described in [VERSIONS.md](../VERSIONS.md); launchers restore it automatically. Keep each Slurm output file and JSON result private; never overwrite an earlier run. Share only a sanitized summary that follows [evidence-security.md](evidence-security.md).

Complete shared software preparation in the [Lab Guide](../../lab-guide.html#lab-preparation). Review its completion summary and follow the Lab Guide’s guidance for any failures or hardware skips before submitting. Keep runtime evidence in those private result directories.

This runbook is a complete platform-qualification checklist, not the teaching
order. Follow the [course](../index.html) for the learning route. Run Lab 10 before
the first local experiment; later gates check the additional mechanisms you use.

## Check the local runtime

```bash
sbatch slurm/10_compatibility_stack.sbatch --workload small
```

Confirm successful completion and the recorded device/runtime before continuing.

## Isolate one-GPU fundamentals

Submit in this order:

```bash
sbatch slurm/01_cpu_gpu_crossover.sbatch --workload small
sbatch slurm/02_tensor_core_precision.sbatch --workload small
sbatch slurm/03_transfer_and_pinning.sbatch --workload small
sbatch slurm/04_layout_and_coalescing.sbatch --workload small
sbatch slurm/05_roofline_microbench.sbatch --workload small
sbatch slurm/07_async_streams.sbatch --workload small
sbatch slurm/08_operator_to_kernels.sbatch --workload small
sbatch slurm/09_triton_launch_geometry.sbatch --workload small
```

For each job, write the prediction first, require the lab's correctness result,
and compare distributions rather than one sample. For Lab 04, preserve the
logical layouts and strides, raw operation distributions, bandwidth estimates
based on logical input/output bytes, repack-copy cost, and computed reuse break-even. A result where
the packed operation is not faster is valid evidence and must report that no
finite break-even exists.

## Broaden only after smoke passes

Repeat selected labs with their supported `small` or `large` profiles. Change one factor at a time. A changed dtype, shape, rank count, power state, process placement, or software version starts a new comparison series.

## Compatibility, scheduling, and read-only health

```bash
sbatch slurm/11_scheduler_tail.sbatch --workload small
sbatch slurm/12_read_only_health.sbatch --workload small
```

Record the wheel/runtime/driver/toolkit roles separately. The lane-work model
preserves total useful work; the grid-tail probe preserves work per program
but varies total program count. Keep that distinction and the stated SM count
with the result. Read-only health evidence must not
change clocks, sharing modes, or error state. Missing operational fields mean
unavailable evidence. Review health changes alongside timing rather than
attributing a slow run to one isolated counter.

## Distributed practice

Use [Advanced Labs](../../advanced-gpu-communication/index.html) for placement,
transport, collective and application checks on its qualified two-worker,
sixteen-H100 fabric. Follow its lab-specific acceptance criteria; two one-GPU
TCP workers do not establish fabric performance. These checks are not gates
for the local experiments above.
