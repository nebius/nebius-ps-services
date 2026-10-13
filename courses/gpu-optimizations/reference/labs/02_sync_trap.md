# Lab 02: Remove unnecessary per-step host synchronization

Fetching a CUDA scalar into Python can force the host to wait for the GPU. In a repeated loop, those waits can prevent efficient submission of later work.

## Run

```bash
# Normal execution
sbatch slurm/02_sync_trap.sbatch --workload small

# Optional profiling
sbatch slurm/02_sync_trap.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/02_sync_trap/jobs/<JOB_ID>/result.json
```

Batching observations can improve execution without changing the mathematical workload, but it changes when information becomes available.
