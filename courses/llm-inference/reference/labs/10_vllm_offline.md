# Lab 10: Measure batched offline generation with vLLM

Offline inference submits a known collection of prompts to an engine without exposing an HTTP service. This lab runs real vLLM generation in the prepared native runtime and accounts for all measured requests and output tokens.

## Run

```bash
# Normal execution
sbatch slurm/10_vllm_offline.sbatch --workload small

# Optional profiling
sbatch slurm/10_vllm_offline.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/10_vllm_offline/jobs/<JOB_ID>/result.json
```

Offline throughput requires complete work accounting and fixed generation semantics.
