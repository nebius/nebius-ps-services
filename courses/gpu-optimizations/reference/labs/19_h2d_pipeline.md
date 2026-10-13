# Lab 19: Overlap H2D copies with a bounded input ring

A nonblocking copy can free the CPU while the GPU still executes copies and kernels sequentially. This lab separates those two effects.

## Run

```bash
# Normal execution
sbatch slurm/19_h2d_pipeline.sbatch --mode serial --slots 2 --workload small

# Optional profiling
sbatch slurm/19_h2d_pipeline.sbatch --mode serial --slots 2 --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/19_h2d_pipeline/jobs/<JOB_ID>/result.json
```

Host asynchrony, producer prefetch and device overlap are separate mechanisms.
