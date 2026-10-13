# Lab 24: Compare inference tensor-partition communication patterns

Tensor parallelism can split a layer's weights across GPUs, but partial outputs must be assembled correctly. This lab demonstrates column- and row-parallel linear inference on two H100 ranks and compares both with a full-weight reference.

## Run

```bash
# Normal execution
sbatch slurm/24_inference_tensor_parallel.sbatch --workload small

# Optional profiling
sbatch slurm/24_inference_tensor_parallel.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/24_inference_tensor_parallel/jobs/<JOB_ID>/result.json
```

These tensor-parallel mechanics timings do not measure live serving TTFT.
