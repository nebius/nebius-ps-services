# Lab 21: Tune real DDP buckets and communication hooks

Gradient readiness only creates an opportunity for overlap; DDP must group gradients into buckets and communicate them in a valid order. This lab exposes that behavior and compares FP32 reduction with FP16, BF16 and PowerSGD communication hooks.

## Run

```bash
# Normal execution
sbatch slurm/21_ddp_buckets.sbatch --hook allreduce --bucket-cap-mb 1 --workload small

# Optional profiling
sbatch slurm/21_ddp_buckets.sbatch --hook allreduce --bucket-cap-mb 1 --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/21_ddp_buckets/jobs/<JOB_ID>/result.json
```

Use both timeline dependencies and step time including completion of the required communication to explain a bucket choice.
