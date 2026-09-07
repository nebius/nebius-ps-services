# Lab 14: Compare no, selective, and full recomputation

Activation checkpointing saves memory by recomputing selected forward work during backward instead of retaining every intermediate. This lab compares three matched tiny-transformer paths and checks loss plus all trainable parameter gradients. You will connect checkpoint boundaries to the memory–compute trade-off rather than assuming that lower peak allocation always produces a better training configuration.

## Before you start

**Theory preparation:** Read Lessons 3–4 for parameter/activation gradients, Lesson 6 for incremental peak allocation and Lesson 8 for selective/full-block recomputation and non-reentrant checkpointing. Use Lesson 7’s matched-state numerical reasoning. This experiment stops after backward, without an optimizer.

Use one H100 and understand the model's forward/backward flow. The inputs are integer token IDs, so an input-token gradient is not defined. Review the [autograd mechanism guide](../lab-mechanisms.md) for parameter and activation distinctions.

Extra H100 compute can make recomputation worthwhile when capacity enables a more efficient microbatch, but added kernels and launch overhead remain visible. Sequence length strongly changes the activation benefit.

## Concepts and code path

The program prepares matched model state and batches for eager, selective, and full checkpointing. Selective means alternate whole transformer blocks, not operator-level selective recomputation. Full means every listed block. It verifies loss and gradients, then measures forward, loss, and backward time plus incremental peak allocation. The reported `step_time` excludes optimizer work and optimizer state: no optimizer is constructed or updated in this lab.

## Practice

Given a block saving 6 GiB of activations and costing 12 milliseconds to recompute, full checkpointing enables microbatch two instead of one but adds 12 milliseconds. Change to checkpoint only a 5-GiB attention intermediate costing 4 milliseconds to replay. Expected observation: if microbatch two still fits, selective recomputation retains most capacity benefit with less step-time penalty at fixed global tokens.

Run Labs 02 and 14 with matched work within each lab, not between their different models. Lab 02 compares an MLP/MSE effective batch against accumulated microbatches. Lab 14 compares eager, selective, and full recomputation of a tiny transformer's forward/loss/backward path. Lab 14 does not execute an optimizer update; complete-update equivalence is a separately implemented extension.

Run all three variants together with the smoke profile. Use the H100 profile only after equivalence passes; its larger model/context is a new memory-pressure experiment.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/14_activation_checkpointing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/14_activation_checkpointing.py --profile h100
```

## Check your results

Require separate loss and gradient agreement for selective and full paths at BF16 `rtol=0.01, atol=0.01`. Inspect checkpointed block indices, forward/backward-only `step_time`, and `median_incremental_peak_bytes`. Token and position embedding parameters are included in the gradient checks; optimizer-update equivalence is not tested here.

For Lab 02 retain effective-batch size, its sampled-gradient check and peak memory. For Lab 14 retain fixed batch/sequence settings, loss and every parameter-gradient comparison, forward/loss/backward timing, peak memory, and recomputed regions. Do not relabel those timings as complete optimizer-step measurements.

Compare loss and gradients at the implemented boundary first, and report the compute cost of the memory saving. An end-to-end training decision additionally requires equivalent optimizer updates and the same useful data per update.

## Investigate the behavior

Which forward intermediates must be recreated for each checkpointed block? Compare memory saved per added millisecond. To apply this reasoning to a complete training loop, explain why persistent optimizer state can reduce the percentage saving in total memory; it is absent from this lab's measured allocation.

Smaller microbatches can reduce GEMM efficiency. More accumulation delays updates and may add synchronization complexity. Recomputation saves memory at a variable compute cost; offload is a different trade that spends transfer bandwidth and is advanced material here.

## If something goes wrong

Gradient disagreement after introducing randomness may reflect unmatched RNG handling or state. Missing gradients suggest a disconnected recomputation path. Stop at the failed equivalence check rather than treating memory savings as success.

Avoid changing microbatch count without holding effective batch or optimizer schedule fixed.

## Takeaways and next step

Recomputation is a selective resource trade, not free memory. A further exercise can implement operator-level policies, but it must preserve this lab's complete loss/gradient gates and explicitly identify the new checkpoint boundaries.

Compare equivalent updates and choose the smallest recomputation set that meets capacity.

Explain why accumulation and checkpointing solve different memory terms.
