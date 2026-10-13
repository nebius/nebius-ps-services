# Lab 35: Trace fixed-parameter autoregressive inference

This exercise shows how repeated predictions create a token sequence while the model's parameters stay unchanged. It uses an explicit four-token vocabulary and a fixed table of next-token scores, so every decision can be checked by hand. Normal execution defaults to CPU; use --device cuda for the optional GPU path, which external diagnostics capture.

## Run

```bash
# Normal execution
sbatch slurm/35_inference_basics.sbatch --device cpu --workload small

# Optional profiling
sbatch slurm/35_inference_basics.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/35_inference_basics/jobs/<JOB_ID>/result.json
```

Inference changes request state while using fixed learned parameters.
