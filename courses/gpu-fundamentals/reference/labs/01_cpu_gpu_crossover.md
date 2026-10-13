# Lab 01: Find the CPU–GPU crossover for vector work

A GPU can execute arithmetic quickly while still losing an application comparison because transferring data and launching work take time. This lab evaluates the same expression, `x * y + x`, on the CPU, on tensors already stored in GPU memory, and on the GPU with input and output transfers included.

## Run

```bash
# Normal execution
sbatch slurm/01_cpu_gpu_crossover.sbatch --workload small

# Optional profiling
sbatch slurm/01_cpu_gpu_crossover.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/01_cpu_gpu_crossover/jobs/<JOB_ID>/result.json
```

Placement decisions depend on data lifetime as well as arithmetic speed.
