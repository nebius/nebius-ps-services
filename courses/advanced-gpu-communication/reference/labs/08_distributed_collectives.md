# Lab 08: Measure a two-node NCCL all-reduce

Distributed work cannot progress faster than its required data exchanges allow. This lab reduces a known tensor across two nodes and measures how message size affects completion time.

## Run

```bash
# Normal execution
sbatch slurm/08_distributed_collectives.sbatch --workload small --payload-mib 4

# Optional profiling
sbatch slurm/08_distributed_collectives.sbatch --workload small --payload-mib 4 --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/08_distributed_collectives/jobs/<JOB_ID>/result.json
```

Collective rates are meaningful only with topology, message size, and the timed operations specified.
