# Lab 10: Survey library behavior across shapes and dtypes

Matrix libraries select implementations partly from shape and precision, so nearby dimensions can produce noticeably different performance. This lab surveys matrix widths and dtypes while checking each result against its corresponding reference.

## Run

```bash
# Normal execution
sbatch slurm/10_shape_precision.sbatch --workload small

# Optional profiling
sbatch slurm/10_shape_precision.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/10_shape_precision/jobs/<JOB_ID>/result.json
```

Compare each shape and dtype against its own correctness reference; different dimensions represent different amounts of work.
