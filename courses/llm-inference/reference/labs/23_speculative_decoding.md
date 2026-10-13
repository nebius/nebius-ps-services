# Lab 23: Trace speculative acceptance, rejection, and recovery

Speculative decoding proposes several tokens with a cheaper draft and asks the target to verify them. This lab uses a small synthetic greedy target to make acceptance and recovery visible.

## Run

```bash
# Normal execution
sbatch slurm/23_speculative_decoding.sbatch --workload small --draft-length 4 --max-new-tokens 32

# Optional profiling
sbatch slurm/23_speculative_decoding.sbatch --workload small --draft-length 4 --max-new-tokens 32 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/23_speculative_decoding/jobs/<JOB_ID>/result.json
```

Acceptance is a mechanism, not a speedup guarantee.
