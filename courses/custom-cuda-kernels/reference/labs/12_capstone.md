# Lab 12: Assemble a kernel acceptance report

A custom kernel is ready only when its correctness, memory safety, performance mechanism, and application impact are understood. This capstone provides a two-kernel affine-plus-tanh baseline and a fused candidate with matched inputs.

## Run

```bash
# Normal execution
sbatch slurm/12_capstone.sbatch --workload small --variant-order baseline-first

# Optional profiling
sbatch slurm/12_capstone.sbatch --workload small --variant-order baseline-first --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/12_capstone/jobs/<JOB_ID>/result.json
```

Deliver the reference contract, build identity, correctness/sanitizer results, all timing trials, mechanism, and remaining integration limits.
