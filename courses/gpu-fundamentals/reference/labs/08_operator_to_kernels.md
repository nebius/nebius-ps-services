# Lab 08: Map PyTorch operations to GPU activity

One line of tensor code can trigger several framework operations and many GPU kernels. This lab profiles a small projection, normalization, and activation chain so you can relate high-level intent to execution cost. Normal execution uses the built-in PyTorch collector; external captures disable it.

## Run

```bash
# Normal execution
sbatch slurm/08_operator_to_kernels.sbatch --workload small

# Optional profiling
sbatch slurm/08_operator_to_kernels.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/08_operator_to_kernels/jobs/<JOB_ID>/result.json
```

Operator names provide a starting map, not a one-to-one kernel identity.
