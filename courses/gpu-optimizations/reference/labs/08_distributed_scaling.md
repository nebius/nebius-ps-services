# Lab 08: Compare one GPU with two-node DDP

Adding a GPU adds both compute capacity and communication work. This lab runs a bounded training step on one GPU and through two-node DistributedDataParallel while preserving global batch size. You will calculate global throughput and scaling efficiency from the slowest participating rank rather than assuming that twice the devices should halve step time.

## Before you start

**Theory preparation:** Read Lessons 11–12 for qualified communication, the forward/loss/backward/AdamW update, DDP gradient averaging, fixed-global-batch scaling and slowest-rank timing. Fundamentals supplies matrix/GELU/BF16 meanings. The Training course is a later specialization, not a hidden prerequisite for this bounded example.

Pass the two-node preflight and use matching environments. The global batch must be divisible by world size. This topology has an inter-node link, not a shared NVLink/NVSwitch fabric.

## Concepts and code path

The one-rank path runs the model locally. The distributed path splits global samples across ranks, wraps the model in DDP, executes backward communication, and updates parameters. Each iteration's duration is reduced to the maximum rank time before throughput is calculated. The local batch gets smaller when rank count grows, which can also change kernel efficiency.

## Practice

Keep the declared global batch identical across these jobs. They form the scaling pair; running only the second command cannot establish a speedup.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/08_distributed_scaling.py --profile smoke --global-batch 64
sbatch slurm/two_node.sbatch labs/08_distributed_scaling.py --profile smoke --global-batch 64
```

## Check your results

Require finite loss on every rank. Compare `global_batch`, `median_step_ms`, `samples_per_second`, and the slowest-rank timing scope. This gate does not compare every gradient or update against a one-process reference; do not claim full training equivalence from finite loss alone.

## Investigate the behavior

Compute speedup as one-rank time divided by two-rank time, then divide by two for efficiency. Explain how communication and reduced local batch size can each limit the result.

## If something goes wrong

An indivisible global batch is rejected intentionally. If one rank stalls, global progress stalls too; inspect private placement and communication logs. Do not average away the straggler to improve the throughput report.

## Takeaways and next step

Scaling is an end-to-end property with an explicit workload contract. Add matched-state gradient/update equivalence before using a modified training path, and use Lab 13 to investigate communication overlap separately.
