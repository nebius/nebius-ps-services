# Lab 31: Validate an optimization across complete training updates

This capstone compares a baseline linear training step with a candidate using a library bias path while preserving the intended update. You will combine correctness, repeated timing, peak memory, and explicit FLOP accounting into a bounded decision.

## Run

```bash
# Normal execution
sbatch slurm/31_training_capstone.sbatch --workload small --variant-order baseline-first

# Optional profiling
sbatch slurm/31_training_capstone.sbatch --workload small --variant-order baseline-first --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/31_training_capstone/jobs/<JOB_ID>/result.json
```

Deliver a report with invariants, numerical gates, all trials, mechanism, limitations, and a rejected hypothesis.
