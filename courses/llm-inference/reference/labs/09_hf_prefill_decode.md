# Lab 09: Follow real-model prefill and continuation

The first generated token normally comes from the final-position logits produced by prefill. Subsequent tokens require continuation passes that consume the previously selected token. The two-token example below performs prefill and one continuation pass.

## Run

```bash
# Normal execution
sbatch slurm/09_hf_prefill_decode.sbatch --workload small --new-tokens 2

# Optional profiling
sbatch slurm/09_hf_prefill_decode.sbatch --workload small --new-tokens 2 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/09_hf_prefill_decode/jobs/<JOB_ID>/result.json
```

Compare prefill and continuation for a fixed output-token budget; these model timings do not report client-boundary observations.
