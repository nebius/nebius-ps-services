# Lab 18: Reduce padded prefill work with length buckets

A single batch rectangle can waste substantial work when one prompt is much longer than the others. This lab compares one padded prefill batch with two length buckets containing the same prompts.

## Run

```bash
# Normal execution
sbatch slurm/18_padding_bucketing.sbatch --workload small

# Optional profiling
sbatch slurm/18_padding_bucketing.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/18_padding_bucketing/jobs/<JOB_ID>/result.json
```

Length grouping is useful only when it preserves semantics and improves performance at the chosen measurement boundary.
