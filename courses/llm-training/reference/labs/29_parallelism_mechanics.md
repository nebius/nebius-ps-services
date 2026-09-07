# Lab 29: Follow pipeline and context partitions through backward

Pipeline parallelism divides layers, while context parallelism divides sequence positions. This lab demonstrates their ownership and gradient mechanics on two ranks with deliberately small computations. You will trace forward activations, returning gradients, and global normalization without mistaking a one-microbatch pipeline or a partitioned statistic for a production transformer-parallel runtime.

## Before you start

**Theory preparation:** Read Lesson 12 for point-to-point pipeline activations and returned gradients, context shards, all-gather reconstruction and globally normalized reductions. Lessons 3–4 and 11 supply matrix differentiation and rank ownership. Complete distributed preflight before either two-rank mechanics path.

Pass the two-node preflight and review matrix gradients and all-gather/all-reduce semantics. Tensor and expert parallelism have separate training labs; this one focuses on pipeline and context partitions.

## Concepts and code path

The pipeline example sends the first linear layer's activation to rank one, computes the second layer and loss, then returns the activation gradient to rank zero for backward. A full local reference validates gradients. The context example partitions sequence positions, reduces a square-sum statistic, gathers shards for reconstruction, and compares gradients under global normalization. It does not implement distributed attention.

## Practice

Run the paired mechanics through the two-node launcher. The output is primarily correctness and communication accounting; there is no multi-microbatch pipeline efficiency benchmark to tune here.

```bash
umask 077
python labs/29_parallelism_mechanics.py --help
sbatch slurm/two_node.sbatch labs/29_parallelism_mechanics.py --profile smoke
```

## Check your results

Require pipeline forward/backward reference agreement and context partition/gradient agreement at FP32 `rtol=1e-5, atol=1e-6`. Inspect activation-send/gradient-return bytes, global/local shapes, one microbatch, and listed collectives.

## Investigate the behavior

Draw the forward and backward arrows between pipeline stages. Why must the second stage treat the received activation as differentiable? For context partitioning, explain why normalizing each local sum by local size would change the intended global gradient.

## If something goes wrong

A send/receive shape or ordering mismatch can block both stages. Correct reconstruction with wrong gradients points toward normalization or partition indexing. Diagnose those independently rather than treating a successful gather as complete correctness.

## Takeaways and next step

Parallelism begins with explicit state and tensor ownership. Production pipeline schedules and context-parallel attention are extensions requiring new communication, masking, and reference tests; this lab does not establish their throughput.
