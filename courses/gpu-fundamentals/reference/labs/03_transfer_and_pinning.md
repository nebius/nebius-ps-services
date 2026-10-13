# Lab 03: Measure pageable and pinned host transfers

Pinned memory can help the GPU transfer data directly, but allocating pinned buffers and requesting a nonblocking copy do not automatically overlap transfers with computation. This lab measures four host-to-device copy combinations using a CPU timer that includes waiting for each copy to finish.

## Run

```bash
# Normal execution
sbatch slurm/03_transfer_and_pinning.sbatch --workload small

# Optional profiling
sbatch slurm/03_transfer_and_pinning.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/03_transfer_and_pinning/jobs/<JOB_ID>/result.json
```

Pinning is an enabling mechanism, not an overlap guarantee.
