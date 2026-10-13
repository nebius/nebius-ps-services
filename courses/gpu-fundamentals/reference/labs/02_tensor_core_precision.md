# Lab 02: Compare matrix precision, error, and throughput

Reduced precision can unlock faster matrix paths, but a faster multiplication is useful only when its numerical error is acceptable. This lab compares four concrete PyTorch matmul modes on H100.

## Run

```bash
# Normal execution
sbatch slurm/02_tensor_core_precision.sbatch --workload small

# Optional profiling
sbatch slurm/02_tensor_core_precision.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/02_tensor_core_precision/jobs/<JOB_ID>/result.json
```

Compare precision throughput and errors against the FP32 highest-policy reference.
