# Lab 16: Observe FSDP2 sharded training state

Fully Sharded Data Parallel reduces persistent state per rank by distributing parameters, gradients, and optimizer state, but computation still needs appropriately materialized parameters. This lab applies FSDP2 to the tiny transformer on two H100 nodes.

## Run

```bash
# Normal execution
sbatch slurm/16_fsdp2_train.sbatch --workload small

# Optional profiling
sbatch slurm/16_fsdp2_train.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/16_fsdp2_train/jobs/<JOB_ID>/result.json
```

Choose sharding from a state-placement and critical-path model.
