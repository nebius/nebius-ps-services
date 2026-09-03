# Cluster smoke-test runbook

Run from the `gpu-fundamentals` course root after activating a cluster-approved environment that satisfies [VERSIONS.md](../VERSIONS.md). Keep each Slurm output file and JSON result private; never overwrite an earlier run. Share only a sanitized summary that follows [evidence-security.md](evidence-security.md).

Before submitting jobs, run `umask 077` in the submitting shell. The launchers
repeat this setting for child-process artifacts, but the scheduler can create
its output file before the script begins.

## Gate 1: prove the two-node allocation

```bash
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

Accept only two distinct hostnames, world size 2, local rank 0 on each node, one visible H100 per rank, no MIG device, successful NCCL initialization, and a correct all-reduce.

## Gate 2: isolate one-GPU fundamentals

Submit in this order:

```bash
sbatch slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_tensor_core_precision.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/03_transfer_and_pinning.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/04_layout_and_coalescing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/05_roofline_microbench.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/07_async_streams.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/09_triton_launch_geometry.py --profile smoke
```

For each job, write the prediction first, require the lab's correctness result,
and compare distributions rather than one sample. For Lab 04, preserve the
logical layouts and strides, raw operation distributions, useful-bandwidth
lower bounds, repack-copy cost, and computed reuse break-even. A result where
the packed operation is not faster is valid evidence and must report that no
finite break-even exists.

## Gate 3: exercise both nodes

```bash
sbatch slurm/two_node.sbatch labs/06_distributed_collectives.py --profile smoke
```

Accept only an exact reduction and a JSON result that records rank count,
message size, software versions, and timing/bandwidth evidence. Portable JSON
uses a random course run ID, not a scheduler job ID. Preserve the Slurm output
privately with the JSON result when you need hostname, rank-placement, or
site-topology evidence; do not publish those infrastructure identifiers.

## Gate 4: broaden only after smoke passes

Repeat selected labs with their default or teaching profiles. Change one factor at a time. A changed dtype, shape, rank count, power state, process placement, or software version starts a new comparison series.
