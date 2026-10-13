# Lab 28: Compare full-prefill and chunked scheduling

Long prompt processing can delay requests that are ready to generate another token. This lab models how full non-preemptible prefill and bounded prefill chunks affect admission opportunities under fixed arrivals.

## Run

```bash
# Normal execution
sbatch slurm/28_continuous_batching.sbatch --workload small

# Optional profiling
sbatch slurm/28_continuous_batching.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/28_continuous_batching/jobs/<JOB_ID>/result.json
```

Scheduling changes who waits as well as aggregate progress.
