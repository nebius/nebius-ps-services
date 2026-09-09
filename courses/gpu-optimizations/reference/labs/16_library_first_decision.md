# Lab 16: Custom kernel decision making

Before maintaining custom GPU code, check whether a supported framework or library primitive already expresses the computation efficiently. This lab compares a composed matrix expression with a maintained PyTorch primitive. You will use the result to write an escalation decision based on a measured remaining problem rather than on the assumption that lower-level code must be faster.

## Before you start

**Theory preparation:** Read Lessons 9 and 13 for precision, cancellation and the framework-versus-library decision. Apply Lessons 1–2's correctness and timing contract. This fixture's BF16 error budget is derived below.

Use one H100 and complete the measurement and correctness lessons. Prepare a decision record containing required semantics, supported shapes/dtypes, end-to-end importance, and the maintenance cost you are willing to accept.

Prefer paths already tuned for SM90 and the pinned CUDA/library stack. Architecture-specific SM90a mechanisms require explicit gates and reduce forward compatibility.

## Concepts and code path

ReLU (rectified linear unit) computes `max(0, x)`, replacing negative values with zero while preserving nonnegative values. The source evaluates a matmul-plus-bias-and-ReLU composition and a path using `addmm` for the matrix/bias portion. Both operate on the same resident BF16 inputs. ReLU remains a separate framework operation; this is not proof that the entire expression is fused into one kernel.

The composition rounds its matrix product to BF16 before adding bias. `addmm` can add bias before that final rounding, so the two valid outputs need not pass a direct elementwise comparison. For example, `1.125 * 3.625 - 4` is exactly `0.078125`, but rounding the product to BF16 first gives `0.0625` after subtraction. Near cancellation, the relative difference is large even though the intermediate rounding error is small. PyTorch's [numerical accuracy guidance](https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html) explains why equivalent expressions can produce different floating-point results.

FP64 is a 64-bit floating-point format with more precision than FP32 or BF16. Here it provides a higher-precision reference, not exact real arithmetic. Unit roundoff bounds the relative error of one rounding-to-nearest step in the normal range; for BF16 it is `2^-8`. That single-step bound does not by itself bound all errors in a matrix reduction.

The lab instead converts the original BF16 inputs to FP64 and computes `P = X @ W` and `R = relu(P + bias)` independently of either measured output. Each BF16 path must satisfy this elementwise error budget:

```text
abs(output - R) <= 0.01 + 0.01 * abs(R) + u * (1 + u) * abs(P)
u = 2^-8  (BF16 unit roundoff)
```

The extra term bounds the composition's intermediate product rounding and its propagation through final BF16 rounding. ReLU cannot amplify absolute error. The existing `atol=0.01` and `rtol=0.01` provide the residual acceptance budget; the latter also exceeds BF16's final relative rounding error. This is the acceptance policy for this lab, not an error guarantee for arbitrary inputs or reduction algorithms. Both paths use the same budget, derived from the independent reference rather than from their disagreement. Reference construction and checks run outside the timed region, and their temporary tensors are released before timing.

## Practice

Given a request where GEMM plus bias/ReLU consumes 35 percent of latency and separate epilogue traffic is proven limiting, compare framework compile, cuBLAS plus epilogue, and CUTLASS fusion. Change to handwritten GEMM only if maintained paths cannot express the semantics. Expected observation: a fused library epilogue can capture the missing traffic reduction while retaining library GEMM quality and a clear fallback.

Complete Lab 09's controlled eager/compiled comparison, then Lab 16's library-first decision record for one measured hotspot. Lab 09 contains three counterbalanced rounds within one process; collect at least three separate job runs for independent-process acceptance. Follow the selected specialized course only when the remaining hotspot justifies it.

Run the paired implementations and retain both timings regardless of which wins. The larger profile is another workload point, not evidence that one path is universally preferable.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/16_library_first_decision.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/16_library_first_decision.py --profile h100
```

## Check your results

Require both `composed_matches_reference` and `library_addmm_matches_reference`, then inspect `shape`, `timing`, and `decision_order`. In `numerics`, inspect the FP64 reference dtype, tolerances, BF16 unit roundoff, maximum intermediate rounding allowance and each path's `max_abs_error` and `max_error_budget_fraction`. The fraction is the maximum of the elementwise error-to-budget ratios and must not exceed one. Both paths must independently pass their reference checks.

Outputs must also have the expected shape and BF16 dtype, contain only finite values and satisfy ReLU's nonnegative-output contract. An invalid reference, budget or output stops the run before either timing or JSON publication. Use a profiler before describing the selected implementation or fusion boundary.

Retain hotspot share, existing-library trials, correctness, end-to-end impact, portability, and maintenance cost.

A custom kernel is justified only when the unmet requirement is important and narrower alternatives fail with evidence.

## Investigate the behavior

What fraction of an actual application does this expression consume? Could compilation or a maintained primitive remove the hotspot? Explain why a substantial microbenchmark improvement can have little end-to-end effect when the hotspot is small.

Higher-level solutions may leave some performance unused but have wider coverage and lower maintenance. Lower-level control can specialize aggressively but moves correctness, safety, and future qualification onto the team.

## If something goes wrong

Exceeding the declared reference budget invalidates substitution even if the candidate is faster. Inspect the recorded inputs, dtype and reduction settings before changing an acceptance rule; a pairwise BF16 mismatch alone does not establish a defect. If timing differences are smaller than run variation, record an inconclusive result. Do not escalate to custom code without a concrete unmet requirement.

Avoid optimizing a visually complex kernel that contributes little end-to-end time.

## Takeaways and next step

Your deliverable is a justified choice among framework composition, library primitive, compilation, and custom implementation. If custom work remains warranted, carry the exact semantics, baseline, and acceptance criteria into the Custom CUDA Kernels capstone.

Use framework configuration first, then compilation or specialized libraries, and custom CUDA only for a proven residual gap.

State the evidence that would make you reject your own custom-kernel proposal.
