# Lab mechanisms and evidence

## Lab 31: derive the work before reporting utilization

Objective: compare equivalent training updates and explain exactly what the
reported FLOP numerator counts. The [training capstone](../labs/31_training_capstone.py)
uses a small BF16 linear layer followed by GELU and an FP32 loss. It compares
matrix multiplication followed by a separate bias addition with `torch.addmm`. This is a
controlled optimization experiment, not a complete transformer MFU benchmark.

### Follow the autograd graph

Let the input `X` have shape `[T, H]`, weights `W` have shape `[H, H]`, and
output gradient `dY` have shape `[T, H]`. A multiply-accumulate convention counts
one multiplication and one addition as two FLOPs. The forward product `XW`
therefore costs approximately `2 × T × H²` FLOPs. Training the weights needs
`dW = X.T @ dY`, another product with the same work. Computing `dX = dY @ W.T`
would add a third product, but only if the input requires a gradient.

The capstone inputs are fixed sampled data with `requires_grad=False`. Weights
and bias require gradients. Consequently, the matrix-work numerator is
`4 × T × H²`, not `6 × T × H²`. For `T=16, H=8`, that is 4096 rather than
6144 FLOPs. The latter overstates this workload by 50 percent. In a multilayer
network an interior activation usually does require a gradient, so blindly
applying the two-GEMM formula to every layer would also be wrong. Inspect the
actual graph. The [PyTorch autograd guide](https://docs.pytorch.org/docs/2.14/notes/autograd.html#setting-requires-grad)
explains how gradient requirements affect recording and backward execution.

| Operation | Counted here? | Reason |
| --- | --- | --- |
| Forward matrix product | Yes | Produces the hidden activations |
| Weight-gradient matrix product | Yes | Weights are trainable |
| Input-gradient matrix product | No | This lab's sampled inputs require no gradient |
| Bias, GELU, loss and optimizer arithmetic | No; excluded from numerator | The deliberately limited convention counts only GEMMs |
| Full training-step time | Yes; used in denominator | Includes the actual update operation |

### Run the comparison

1. Predict the GEMM count and inspect input/parameter gradient flags in the
   source. Do not enable an unnecessary gradient just to match a formula.
2. Run the [three-trial capstone procedure](cluster-smoke-test.md). Every trial
   checks loss and complete parameter updates, warms both variants, and records
   repeated CUDA-event timings with alternating variant order across processes.
3. Read `minimum_step_flops`, `input_requires_grad`,
   `candidate_minimum_step_tflops`, and the accompanying accounting note.
   Tokens/s is a separate rate; it does not require a peak-FLOP assumption.
4. Supply `--peak-tflops` only with a disclosed, appropriate dense BF16 peak
   for the actual SKU/clock convention. Do not substitute a sparse marketing
   peak for this dense workload. Without a peak, utilization remains unset.
5. Interpret `candidate_matmul_utilization_percent` as the counted matrix work
   divided by full-step time and the stated peak. It is a lower-bound,
   matmul-only utilization estimate, not full-model LLM MFU or a hardware counter.

### Evidence, failure, and review

Retain shapes, precision, gradient flags, the FLOP expression, peak source,
measurement boundary, numerical checks, distributions, and all three trial
records. Extend the [benchmark worksheet](benchmark-record.md) with these fields.
The local regression profiles actual CPU autograd operations for both input
gradient settings; it proves the counting logic, not BF16 H100 performance.

If utilization rises while tokens/s falls, check whether the work convention
changed or extra recomputation was counted. If it exceeds 100 percent, check
units, peak type, clock assumptions, timing boundaries, and double counting
before claiming exceptional hardware behavior. A low estimate alone does not
identify memory, launch, input-pipeline, or communication bottlenecks: use the
profiler and training phase evidence to distinguish them.

Review answer: the numerator follows the graph actually executed, while the
denominator follows the declared completion boundary. General transformer MFU
analysis must account for architecture, active parameters, token counts and
the chosen recomputation convention; this small capstone does not establish it.
