# Lab 05: Reuse neighboring values with a shared-memory halo

Stencil operations compute each output from nearby input values, so adjacent threads can reuse much of the same data. This lab implements a one-dimensional three-point stencil with a shared tile and two halo values.

## Run

```bash
# Normal execution
sbatch slurm/05_tiled_stencil.sbatch --workload small

# Optional profiling
sbatch slurm/05_tiled_stencil.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/05_tiled_stencil/jobs/<JOB_ID>/result.json
```

Shared tiling makes reuse explicit but adds synchronization and halo overhead.
