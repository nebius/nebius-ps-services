# Lab 03: Coalesce a transpose and reduce shared-memory conflicts

A transpose naturally makes either reads or writes strided when implemented directly. This lab compares naive, tiled-unpadded, and tiled-padded CUDA transposes.

## Run

```bash
# Normal execution
sbatch slurm/03_tiled_transpose.sbatch --workload small

# Optional profiling
sbatch slurm/03_tiled_transpose.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/03_tiled_transpose/jobs/<JOB_ID>/result.json
```

Tiling coordinates global access and local reuse; padding changes bank layout.
