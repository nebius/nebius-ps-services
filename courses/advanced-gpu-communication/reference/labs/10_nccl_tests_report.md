# Lab 10: Run and interpret NVIDIA NCCL Tests on Slurm

NVIDIA NCCL Tests separates collective performance from model computation. In this lab you will launch its all-reduce benchmark on two H100 nodes, inspect the rank and version records, interpret both buffer-placement results, and compare a baseline with one justified candidate.

## Run

```bash
# Normal execution
sbatch slurm/10_nccl_tests_report.sbatch default --workload small

# Optional profiling
sbatch slurm/10_nccl_tests_report.sbatch default --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/10_nccl_tests_report/jobs/<JOB_ID>/result.json
```

A trustworthy benchmark report needs identity, correct rank placement, validated outputs, defined units and independent repeated measurements.
