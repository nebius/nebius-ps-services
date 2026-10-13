# Lab 15: Train replicated models with DDP

DistributedDataParallel lets each GPU process different data while keeping replicated model updates coordinated. This lab trains the tiny language model with one rank on each H100 node.

## Run

```bash
# Normal execution
sbatch slurm/15_ddp_train.sbatch --workload small

# Optional profiling
sbatch slurm/15_ddp_train.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/15_ddp_train/jobs/<JOB_ID>/result.json
```

DDP coordinates replicas; it does not reduce persistent model-state ownership per rank.
