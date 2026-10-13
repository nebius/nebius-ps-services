# Lab 24: Compare materialized attention with SDPA dispatch

Attention can either materialize a large score matrix or use a more memory-efficient implementation that avoids keeping the entire matrix. This lab compares an explicit reference with PyTorch scaled-dot-product attention for prefill-like and decode-like shapes.

## Run

```bash
# Normal execution
sbatch slurm/24_sdpa_attention.sbatch --workload small

# Optional profiling
sbatch slurm/24_sdpa_attention.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/24_sdpa_attention/jobs/<JOB_ID>/result.json
```

Attention optimization is phase- and shape-dependent.
