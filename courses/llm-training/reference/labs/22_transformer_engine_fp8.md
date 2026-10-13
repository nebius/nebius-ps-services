# Lab 22: Qualify a warmed Transformer Engine FP8 recipe

FP8 execution relies on scaling and quantization state, not simply changing a tensor dtype. This optional lab compares BF16 and FP8 forward/backward behavior for a Transformer Engine linear layer using a delayed-scaling recipe.

## Run

```bash
# Normal execution
sbatch slurm/22_transformer_engine_fp8.sbatch --workload small

# Optional profiling
sbatch slurm/22_transformer_engine_fp8.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/22_transformer_engine_fp8/jobs/<JOB_ID>/result.json
```

An FP8 claim belongs to an exact recipe, workload, and validated environment.
