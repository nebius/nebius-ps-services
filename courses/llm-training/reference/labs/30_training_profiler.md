# Lab 30: Inspect the operators in a tiny training step

A training-step profile helps connect forward, loss, backward, and optimizer work to expensive operators. This lab captures a bounded tiny-model step and emits a short operator summary. Normal execution uses the built-in PyTorch collector; external captures disable it.

## Run

```bash
# Normal execution
sbatch slurm/30_training_profiler.sbatch --workload small

# Optional profiling
sbatch slurm/30_training_profiler.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/30_training_profiler/jobs/<JOB_ID>/result.json
```

A profile proposes a causal hypothesis; it does not prove a speedup.
