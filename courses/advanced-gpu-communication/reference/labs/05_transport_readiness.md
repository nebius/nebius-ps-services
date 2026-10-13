# Lab 05: Establish the optimization platform contract

An optimization comparison is interpretable only when the execution platform is known and functioning. This lab verifies the advanced two-node H100 fabric used by the distributed exercises, including rank placement and a known collective result.

## Run

```bash
# Normal execution
sbatch slurm/05_transport_readiness.sbatch --workload small

# Optional profiling
sbatch slurm/05_transport_readiness.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/05_transport_readiness/jobs/<JOB_ID>/result.json
```

Preflight separates platform readiness from application performance.
