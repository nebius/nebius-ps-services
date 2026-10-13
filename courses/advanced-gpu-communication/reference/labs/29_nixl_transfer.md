# Lab 29: Measure NIXL GPU transfer progress

NVIDIA Inference Xfer Library, or NIXL, moves data between memory regions using transport backends. A progress thread advances transfer work independently of the calling thread.

## Run

```bash
# Normal execution
sbatch slurm/29_nixl_transfer.sbatch --workload small --progress-thread off

# Optional profiling
sbatch slurm/29_nixl_transfer.sbatch --workload small --progress-thread off --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/29_nixl_transfer/jobs/<JOB_ID>/result.json
```

Explain what changed, which observation supports the mechanism, and which alternative explanation remains.
