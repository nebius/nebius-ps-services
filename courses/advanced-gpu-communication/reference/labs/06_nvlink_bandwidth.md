# Lab 06: Compare copy engines and SM peer traffic

NVLink carries peer GPU traffic; NVSwitch connects the eight GPUs within this worker. In this experiment you will measure a baseline, inspect the evidence, and change one control while keeping useful work fixed.

## Run

```bash
# Normal execution
sbatch slurm/06_nvlink_bandwidth.sbatch --workload small --engine ce

# Optional profiling
sbatch slurm/06_nvlink_bandwidth.sbatch --workload small --engine ce --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/06_nvlink_bandwidth/jobs/<JOB_ID>/result.json
```

Check all 56 directed peer paths, and interpret bandwidth within this one-node copy-engine or SM-copy experiment.
