# Lab 02: Trade microbatch size for peak memory

When a full batch does not fit, gradient accumulation can split its work across smaller microbatches before an update. This lab compares full-batch and accumulated gradients on a fixed-shape MLP regression problem. You will learn the normalization rule and memory trade-off before applying accumulation to variable-length language-model batches, where valid-token counts add another constraint.

## Before you start

**Theory preparation:** Read Lessons 4 and 8 for gradient accumulation, loss normalization, effective batch and numerical equivalence; Lesson 3 defines the linear/GELU workload. Lesson 4 is a preview. Run after Lesson 8, comparing microbatches with the same full-batch objective rather than assuming the script performs an optimizer update.

Use one H100 and review backward accumulation into parameter gradients. This lab uses equally sized microbatches and mean-squared error, not token masking or a full language-model optimizer step.

## Concepts and code path

The program initializes matched model/data conditions, computes full-batch gradients, and compares them with gradients accumulated from scaled microbatch losses. Each microbatch contributes its fraction of the full mean. Smaller microbatches primarily reduce simultaneous activation and temporary storage; the parameter-gradient buffers still have the same size. Timing and peak allocation are measured for the two strategies. The implementation checks sampled gradient entries and does not call an optimizer step as part of its equivalence proof.

## Practice

Run the predefined smoke comparison, then the larger profile as a separate batch/shape experiment. The reported batch sizes are the source of truth for interpreting the memory difference.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile h100
```

## Check your results

Require `sampled_gradients_match`. Both strategies' compared gradient samples and each computed error must be finite; a missing gradient or an absolute sample error above `2e-3` fails before a success record is written. Lesson 7 explains why a threshold alone cannot reject NaN reliably. Inspect `total_batch`, `microbatch`, `sampled_max_gradient_error`, and each strategy's median elapsed time and peak allocation. Sampled agreement is narrower than comparing every parameter gradient and one update.

## Investigate the behavior

Why must gradients be cleared before the accumulation window but not between its microbatches? Explain why simply averaging microbatch means is wrong when the microbatches contain different numbers of valid tokens.

## If something goes wrong

A constant scaling discrepancy suggests an incorrect loss divisor or an extra gradient reset. A memory result that does not improve may reflect persistent model state dominating activations rather than a broken accumulation mechanism.

## Takeaways and next step

Accumulation preserves an objective only with correct weighting and update boundaries. Extend the proof to every gradient and one optimizer update, then add valid-token weighting before using it for unequal-length language-model batches.
