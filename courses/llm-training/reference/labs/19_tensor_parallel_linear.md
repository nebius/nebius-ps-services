# Lab 19: Partition a linear layer and verify its backward pass

Tensor parallelism partitions the computation inside a layer rather than assigning independent examples to complete model replicas. This lab splits a linear operation across two H100 ranks and checks its forward output, gradients, and update against a replicated reference. You will learn which tensor dimensions are local and which results must be communicated to preserve the original computation.

## Before you start

**Theory preparation:** Read Lessons 11–12 for tensor ownership, column/row matrix partitions, all-gather/all-reduce, gradient reconstruction and global loss normalization. Use Lessons 4 and 7 for update and relative-L2 checks and complete distributed preflight.

Pass distributed preflight and review matrix multiplication shapes. The hidden width must be divisible by the number of ranks. This is a small operator proof, not a full language-model tensor-parallel framework.

## Concepts and code path

The source creates deterministic weights and inputs, partitions a weight dimension for column-parallel work, and compares local results with reference slices. Backward forms local weight gradients and combines input-gradient contributions. It also demonstrates row-parallel forward reconstruction and checks a simple optimizer update. Reference tensors remain available for verification, so conceptual shard bytes are not the entire resident-memory footprint.

## Practice

Run both ranks through the course launcher. Keep the full result so the forward, backward, update, and communication checks remain associated with the same workload.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile h100
```

## Check your results

Require column/row forward, weight-gradient, input-gradient, and update agreement. Each compared tensor, norm and relative error must be finite, and every error must be below `0.02`. An invalid comparison becomes a failing infinite error so all ranks can reach the shared MIN verdict described in Lesson 12; no successful result is written if any rank fails. Inspect replicated versus per-rank shard bytes, input-gradient all-reduce bytes, collective time, and slowest-rank training-step time. Do not equate theoretical shard storage with measured whole-model fit.

## Investigate the behavior

Write the shapes of X, each weight shard, local Y, and dX. Why are input-gradient contributions summed for a column split? Which communication moves from backward to forward when the partition orientation changes?

## If something goes wrong

Shape mismatches usually indicate the wrong partition axis or reconstruction order. A correct forward result can still have an incorrect backward collective, so inspect every gradient gate independently.

## Takeaways and next step

TP changes tensor ownership and communication dependencies inside a layer. Carry those invariants into a larger block only after its partitioned result and update match an unpartitioned reference; do not infer production scaling from this two-rank proof.
