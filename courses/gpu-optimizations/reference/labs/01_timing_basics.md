# Lab 01: Compare CPU timers and CUDA events

This lab measures one GPU operation three ways to expose a common benchmarking mistake: timing only the host's submission of asynchronous work. You will compare unsynchronized host time, synchronized host time, and CUDA-event time.

## Run

```bash
# Normal execution
sbatch slurm/01_timing_basics.sbatch --workload small

# Optional profiling
sbatch slurm/01_timing_basics.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/01_timing_basics/jobs/<JOB_ID>/result.json
```

CUDA event intervals can include idle gaps; compare the named host and event timing boundaries.
