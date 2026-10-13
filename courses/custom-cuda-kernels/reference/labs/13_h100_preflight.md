# Lab 13: Build and identify the H100 CUDA execution target

A custom kernel depends on a compiler, target architecture, runtime, and physical device that must agree. This lab establishes the required H100/SM90 execution target before any optimization experiment.

## Run

```bash
# Normal execution
sbatch slurm/13_h100_preflight.sbatch --workload small

# Optional profiling
sbatch slurm/13_h100_preflight.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/13_h100_preflight/jobs/<JOB_ID>/result.json
```

Reproducible kernel work begins with an explicit toolchain and execution target.
