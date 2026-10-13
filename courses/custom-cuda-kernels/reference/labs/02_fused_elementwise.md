# Lab 02: Remove an intermediate with elementwise fusion

Two simple kernels can spend more time moving intermediate values than performing arithmetic. This lab compares separate elementwise stages with a fused scale/bias/ReLU calculation using corresponding inputs.

## Run

```bash
# Normal execution
sbatch slurm/02_fused_elementwise.sbatch --workload small

# Optional profiling
sbatch slurm/02_fused_elementwise.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/02_fused_elementwise/jobs/<JOB_ID>/result.json
```

Fusion is valuable when it removes material overhead without violating semantics or resource constraints.
