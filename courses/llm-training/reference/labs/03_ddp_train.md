# Lab 03: Train replicated models with DDP

DistributedDataParallel lets each GPU process different data while keeping replicated model updates coordinated. This lab trains the tiny language model with one rank on each H100 node. You will trace how local forward/backward work relates to gradient communication, global token accounting, and the slowest-rank completion time of the distributed workload.

## Before you start

**Theory preparation:** Read Lessons 4 and 11 for complete updates, local/global loss normalization, DDP replicas, process groups and gradient averaging. Use Lessons 6–7 for memory and autocast, and complete distributed preflight before comparing rank timings.

Pass Lab 00 and understand the one-GPU training loop. Both ranks need the same Training environment. The supplied fixed-shape batches do not exercise unequal valid-token counts across ranks.

Two one-H100 nodes validate API and state/collective mechanics over the measured network. They cannot prove dense NVLink/NVSwitch placement or production scaling guidance.

## Concepts and code path

The launcher starts two workers; shared helpers initialize NCCL and bind devices. Each rank builds a full tiny model and optimizer, wraps the model in DDP, and runs local batches. Backward synchronizes parameter gradients through DDP. The program aggregates loss, maximum memory, and elapsed time for reporting; model parameters remain replicated rather than sharded.

## Practice

Given a model whose replicated training state uses 55 GiB per rank and fits one full H100, compare DDP and FSDP2 at equal global valid tokens. Change to a model whose replicated state exceeds device capacity. Expected observation: DDP can win the first case, while FSDP2 becomes a feasibility requirement in the second; transient gather peaks and checkpoint restart still need measurement.

Run Labs 03 and 04 with fixed global tokens and produce a state/collective map. The provided fixed-shape baseline does not exercise unequal valid-token counts across ranks. Extension: use 100 and 300 valid labels on the two ranks, all-reduce the count, apply the normalization derived in Lesson 4, and compare every parameter gradient and one update with a single-process concatenated reference.

Use the two-node launcher and retain the declared global tokens with each timing. A larger profile changes the workload and should be recorded as a new experiment.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/03_ddp_train.py --profile smoke
sbatch slurm/two_node.sbatch labs/03_ddp_train.py --profile h100
```

## Check your results

Inspect `world_size`, `global_tokens`, `slowest_rank_elapsed_ms`, `final_mean_loss`, and maximum-rank peak allocation. Confirm the finite mean-loss gate. The supplied check does not establish every-gradient or one-update equivalence against a concatenated single-process reference.

Retain per-rank memory, step time, collective volume, exposed communication, loss, and parameter agreement.

Two nodes prove mechanics and bounded tradeoffs, not dense-node or large-scale efficiency.

## Investigate the behavior

Map local examples to the global objective. For the default averaged-gradient behavior, explain why each rank must scale a summed loss by world size divided by global valid-token count when valid counts differ.

Sharding increases capacity but adds communication, materialization, and checkpoint complexity. DDP is simpler and often faster when the model fits. CPU offload extends capacity at the cost of transfer time and host memory.

## If something goes wrong

Collective-order mismatches can hang backward even after preflight succeeds. Missing ranks and divergent control flow require private-log inspection. Do not interpret work completed by only one rank as global training progress.

Avoid comparing per-rank batch sizes without holding the global batch fixed.

## Takeaways and next step

DDP coordinates replicas; it does not reduce persistent model-state ownership per rank. Add the course's 100/300-valid-token reference extension before claiming unequal-token correctness, then compare state placement with FSDP2.

Choose DDP when replication fits and simplicity wins; shard when capacity requires it and communication remains acceptable.

Trace one parameter, gradient, and optimizer state through each strategy.
