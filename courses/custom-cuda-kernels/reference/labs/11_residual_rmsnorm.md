# Lab 11: Fuse residual addition with row-wise RMS normalization

Residual addition followed by RMSNorm is a common transformer data path with opportunities to reuse values before writing them back to memory. This lab implements a small FP32 fused row kernel and validates it against a CPU reference.

## Run

```bash
# Normal execution
sbatch slurm/11_residual_rmsnorm.sbatch --workload small

# Optional profiling
sbatch slurm/11_residual_rmsnorm.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/11_residual_rmsnorm/jobs/<JOB_ID>/result.json
```

Generalize with strided per-thread column loops, tail masks, partial-sum reduction, and FP32 accumulation.
