# Lab 15: Distinguish task imbalance from partial grid waves

A long final task can delay completion even when most work has finished, while a partial grid wave can leave SM capacity unused. This lab explores those effects separately with concurrent GEMMs and a Triton wave probe.

## Run

```bash
# Normal execution
sbatch slurm/15_tail_load_balance.sbatch --workload small

# Optional profiling
sbatch slurm/15_tail_load_balance.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/15_tail_load_balance/jobs/<JOB_ID>/result.json
```

The modeled makespan is an optimistic estimate; this diagnostic does not prove a causal regrouping speedup.
