# Lab 29: Inspect quantized weight and KV storage mechanics

Quantization can reduce stored payload size while adding scale metadata, conversion work, and numerical error. This lab implements symmetric INT8 weight and KV representations and compares their reconstruction with BF16 references.

## Run

```bash
# Normal execution
sbatch slurm/29_quantization.sbatch --workload small

# Optional profiling
sbatch slurm/29_quantization.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/29_quantization/jobs/<JOB_ID>/result.json
```

Separate representation, kernel support, allocation, latency, and quality claims.
