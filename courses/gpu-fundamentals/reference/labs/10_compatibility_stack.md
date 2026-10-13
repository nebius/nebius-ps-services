# Lab 10: Identify the software layer behind GPU execution

Driver, CUDA runtime, compiler toolkit, and PyTorch versions answer different questions. This lab records them separately and launches a small framework operation on H100.

## Run

```bash
# Normal execution
sbatch slurm/10_compatibility_stack.sbatch --workload small

# Optional profiling
sbatch slurm/10_compatibility_stack.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/10_compatibility_stack/jobs/<JOB_ID>/result.json
```

Compatibility is a relationship among layers, not one version label.
