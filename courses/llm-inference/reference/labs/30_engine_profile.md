# Lab 30: Probe an engine's readiness and request schema

Different inference servers expose different readiness endpoints and request schemas even when they host similar models. This lab sends one bounded request through either an OpenAI-compatible API or a declared TensorRT-LLM/Triton repository profile.

## Run

```bash
# Normal execution
sbatch slurm/30_engine_profile.sbatch Qwen/Qwen2.5-0.5B-Instruct 7ae557604adf67be50417f59c2c2f167def9a775 --workload small

# Optional profiling
sbatch slurm/30_engine_profile.sbatch Qwen/Qwen2.5-0.5B-Instruct 7ae557604adf67be50417f59c2c2f167def9a775 --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/30_engine_profile/jobs/<JOB_ID>/result.json
```

Qualify the exact protocol and artifact before benchmarking.
