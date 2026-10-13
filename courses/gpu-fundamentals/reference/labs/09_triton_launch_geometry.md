# Lab 09: Sweep logical work per Triton program

Launch geometry determines how a problem is divided among GPU programs and how much work remains in the final partial program. This Python/Triton lab sweeps logical elements per program while holding four warps per program fixed.

## Run

```bash
# Normal execution
sbatch slurm/09_triton_launch_geometry.sbatch --workload small

# Optional profiling
sbatch slurm/09_triton_launch_geometry.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/09_triton_launch_geometry/jobs/<JOB_ID>/result.json
```

Geometry tuning must preserve indexing and useful work.
