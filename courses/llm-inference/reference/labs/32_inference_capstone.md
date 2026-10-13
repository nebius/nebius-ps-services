# Lab 32: Build a causal attention optimization report

This capstone deliberately reuses Lab 24's explicit-attention versus SDPA comparison with the same BF16 tensors and mathematical mask. Lab 24 establishes backend and numerical behavior; the new competency here is making a defensible decision from independent trials, reversed variant order and an aggregate result that passes evidence validation.

## Run

```bash
# Normal execution
sbatch slurm/32_inference_capstone.sbatch --workload small --variant-order baseline-first

# Optional profiling
sbatch slurm/32_inference_capstone.sbatch --workload small --variant-order baseline-first --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/32_inference_capstone/jobs/<JOB_ID>/result.json
```

Deliver a bounded mechanism-backed attention report.
