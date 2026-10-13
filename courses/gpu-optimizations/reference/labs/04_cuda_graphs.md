# Lab 04: Replay a fixed-shape workload with a CUDA Graph

A CUDA Graph is a reusable plan of device operations and their dependencies. Capture records compatible work into the plan; replay executes it again, rather than returning cached output values.

## Run

```bash
# Normal execution
sbatch slurm/04_cuda_graphs.sbatch --workload small

# Optional profiling
sbatch slurm/04_cuda_graphs.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/04_cuda_graphs/jobs/<JOB_ID>/result.json
```

Compare eager and graph replay times only after the fixed-shape correctness check passes.
