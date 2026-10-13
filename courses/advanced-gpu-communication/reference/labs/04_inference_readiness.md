# Lab 04: Verify the two-node inference mechanics platform

Distributed inference requires correctly placed workers before model partitioning or serving behavior can be evaluated. This lab checks the two-node H100 allocation and a known NCCL collective without starting an inference engine.

## Run

```bash
# Normal execution
sbatch slurm/04_inference_readiness.sbatch --workload small

# Optional profiling
sbatch slurm/04_inference_readiness.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/04_inference_readiness/jobs/<JOB_ID>/result.json
```

Distributed readiness has multiple gates.
