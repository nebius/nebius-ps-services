# Lab 04: Observe FSDP2 sharded training state

Fully Sharded Data Parallel reduces persistent state per rank by distributing parameters, gradients, and optimizer state, but computation still needs appropriately materialized parameters. This lab applies FSDP2 to the tiny transformer on two H100 nodes. You will trace state ownership and communication rather than assume that sharding halves every allocation or guarantees faster training.

## Before you start

**Theory preparation:** Read Lessons 6–7 and 11 for persistent/transient state, mixed-precision policy, FSDP2 sharding, all-gather/reduce-scatter and optimizer construction after sharding. Reuse Lesson 4’s update and normalization rules and complete distributed preflight.

Pass distributed preflight and qualify the Training environment's FSDP2 API. Review DDP first. The example is intentionally small enough for mechanics; it is not a production-scale memory or network benchmark.

## Concepts and code path

The code constructs the model, applies FSDP2's composable sharding to the configured modules, creates the optimizer for that state, and runs the training loop. Parameter materialization and gradient reduction occur around computation. Reporting uses the slowest rank and maximum peak allocation. Transient activations, gathered parameters, and communication buffers still consume memory.

## Practice

Run the supplied sharded path through the two-node launcher. When comparing against DDP, independently verify matching global workload, precision, and timing boundaries instead of assuming matching profile names prove equivalence.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/04_fsdp2_train.py --profile smoke
sbatch slurm/two_node.sbatch labs/03_ddp_train.py --profile smoke
```

## Check your results

Confirm finite mean loss and inspect `world_size`, `slowest_rank_elapsed_ms`, and `max_rank_peak_allocated_mib`. These checks demonstrate bounded execution, not a full reference comparison of all gradients or checkpoint/restart behavior.

## Investigate the behavior

Draw persistent shards separately from temporarily gathered parameters. Which memory survives between steps? Which communication is needed before forward and during backward? Explain why small models can become slower when communication overhead dominates.

## If something goes wrong

An unavailable FSDP2 interface is an environment qualification blocker. A memory peak above your shard estimate may include materialization and activations. Do not silently switch to an older sharding API or claim the same experiment passed.

## Takeaways and next step

Choose sharding from a state-placement and critical-path model. Extend numerical verification and checkpoint handling separately before using the pattern for a larger model; two nodes establish mechanics, not production scaling.
