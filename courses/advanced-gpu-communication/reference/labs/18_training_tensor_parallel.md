# Lab 18: Partition a linear layer and verify its backward pass

Tensor parallelism partitions the computation inside a layer rather than assigning independent examples to complete model replicas. This lab splits a linear operation across two H100 ranks and checks its forward output, gradients, and update against a replicated reference.

## Run

```bash
# Normal execution
sbatch slurm/18_training_tensor_parallel.sbatch --workload small

# Optional profiling
sbatch slurm/18_training_tensor_parallel.sbatch --workload small --profiler all
```

## Check your results

```bash
sacct -j <JOB_ID> --format=JobID,State,ExitCode
cat results/18_training_tensor_parallel/jobs/<JOB_ID>/result.json
```

TP changes tensor ownership and communication dependencies inside a layer.
