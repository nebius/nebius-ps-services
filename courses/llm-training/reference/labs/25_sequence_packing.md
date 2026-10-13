# Lab 25: Pack variable-length examples without crossing boundaries

Padding reserves positions that may contribute no useful training target, while packing can place several short examples into one fixed-capacity row. This lab constructs a packing plan and a boundary-safe causal mask.

## Run

```bash
# Normal execution
sbatch slurm/25_sequence_packing.sbatch --workload small --capacity 256

# Optional profiling
sbatch slurm/25_sequence_packing.sbatch --workload small --capacity 256 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/25_sequence_packing/jobs/<JOB_ID>/result.json
```

Packing changes data layout and requires explicit semantic boundaries.
