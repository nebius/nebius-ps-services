# Lab 03: Compare eager and compiled pointwise execution

Several pointwise operations can repeatedly read and write intermediate tensors. Compilation may combine that work and reduce launches or memory traffic.

## Run

```bash
# Normal execution
sbatch slurm/03_compile_fusion.sbatch --workload small

# Optional profiling
sbatch slurm/03_compile_fusion.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/03_compile_fusion/jobs/<JOB_ID>/result.json
```

Fusion reduces logical intermediate traffic; profiling is needed to establish actual HBM traffic.
