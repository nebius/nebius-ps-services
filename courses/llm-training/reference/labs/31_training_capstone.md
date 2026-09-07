# Lab 31: Validate an optimization across complete training updates

This capstone compares a baseline linear training step with a candidate using a library bias path while preserving the intended update. You will combine correctness, repeated timing, peak memory, and explicit FLOP accounting into a bounded decision. The goal is a reproducible causal report, not a large utilization number detached from the work actually performed.

## Before you start

**Theory preparation:** Read Lesson 16 for independent trials, counterbalanced order, matched-work causality and the disclosed FLOP numerator. Lessons 1, 3 and 4 define linear/bias/GELU, MSE and SGD; Lessons 6 and 10 define incremental memory and matched profiling. Profile this lab’s own baseline and candidate before explaining their timing.

Use one H100 and complete the preceding measurement and profiler exercises. Each lab invocation is one fresh-process trial; the supplied campaign launcher runs three and alternates variant order. Keep all trial records private.

GELU (Gaussian error linear unit) is an activation function: it applies a nonlinear transformation after a learned linear operation. It multiplies each input by a smooth factor between zero and one, strongly suppressing very negative inputs while approaching the identity for very positive inputs. This capstone applies GELU to the matrix-plus-bias result before computing the loss.

Lab 30 teaches profiling on a tiny transformer; this lab compares a different linear/GELU/bias workload. Obtain a matched profile of this lab's own baseline and candidate before attributing its timing change to an operation or kernel. Use the qualified profiling workflow from Lesson 10 on this source and its actual arguments; profiling remains a separate activity, not a new CLI mode. Record identical input shapes and update semantics, then collect acceptance timing without profiler instrumentation. A transformer bottleneck needs its own transformer experiment.

H100 results are cluster-, shape-, stack-, and topology-specific. Static validation prepares the campaign; only target runs can support speed, scaling, or MFU claims.

## Concepts and code path

The source creates matched inputs, weights, and bias, verifies one full baseline/candidate update, then times complete steps in the declared order. It records incremental peak allocation and throughput. Fixed inputs require no gradient, so the counted GEMMs are forward XW and weight-gradient X.T@dY: `4*tokens*width^2` FLOPs, not the usual three-GEMM heuristic. Other step operations remain inside timing but outside that numerator.

## Practice

Given a baseline that cannot fit the required global batch without accumulation and a selective-checkpoint candidate that makes each microstep 8 percent slower but doubles microbatch size, compare fixed global tokens across three runs. Change only checkpoint placement and microbatch/accumulation while preserving the global-token update. Expected observation: the candidate is kept only if end-to-end tokens/s, memory headroom, loss/gradient checks, and operational cost meet the declared target; pending live evidence is never recorded as passed.

Revisit Lab 30 as profiling practice, not as causal evidence for Lab 31: they are different workloads. Lab 30 profiles a tiny transformer; Lab 31 supplies a fixed linear/GELU/bias-path comparison. Collect a matched profile of Lab 31's own baseline and candidate to explain its mechanism, then use separate unprofiled runs of `slurm/capstone_three_trials.sbatch` for three fresh-process trials with alternating variant order. A new optimization chosen from the transformer trace requires a separate matched transformer experiment; Lab 31 does not implement it automatically. Derive the lab's FLOP count before interpreting utilization: fixed inputs require no gradient, so forward XW and weight-gradient X.T @ dY cost 4 × tokens × width² FLOPs together. There is no input-gradient GEMM. Bias, GELU, loss and optimizer work remain outside this matmul-only numerator, but inside full-step timing. The [autograd and utilization walkthrough](../lab-mechanisms.md) shows why this lower-bound estimate is not full-transformer MFU and when a third GEMM would be required.

Use the campaign launcher for the required three independent processes. Supply a peak-TFLOP/s reference only after verifying the exact H100 variant and arithmetic mode; an arbitrary peak makes utilization meaningless.

```bash
umask 077
python labs/31_training_capstone.py --help
sbatch slurm/capstone_three_trials.sbatch --profile smoke
```

## Check your results

Require `full_update_close` in every trial. Inspect `variant_order`, timing distributions, incremental peaks, candidate tokens/s, and `minimum_step_flops`. A single record remains provisional until the campaign is complete; matmul-only utilization is not full-transformer MFU.

The gate checks finite gradients and compares each parameter's gradients after
scaling by the reference gradient's maximum magnitude, with
`rtol=1e-2, atol=1e-2`. Each result must also exactly match plain SGD applied to
its initial parameters and its own gradients, and some parameter must change.
A candidate that skips backward or the optimizer cannot pass merely because
its parameters remain close to their initial values.

The aggregator requires complete, matching profile, H100/software, shape and
measurement-option fields. Every timing and the derived ratio must be finite
and positive; missing fields, boolean timings and non-finite values cannot
produce a keep decision. The optional peak-TFLOPS reference may remain unset.

Retain baseline/candidate configs, loss and update checks, traces, samples, memory, throughput, communication, and decision. Also record NUMA placement, observed GPU clocks/boost state, and Python garbage-collection pauses as trial context before attributing a change to model code.

Observed metrics support only the declared model, shapes, nodes, versions, and trial conditions.

## Investigate the behavior

Does the candidate help at the complete-step boundary, not just one operator? Explain the profiler-observed mechanism and any order sensitivity. Identify which omitted arithmetic makes the reported FLOP numerator a lower-bound accounting convention.

A slower step may be accepted if it enables the required batch or removes OOM risk. A faster step may be rejected for quality drift, fragility, startup cost, memory, or a negligible end-to-end contribution.

## If something goes wrong

Update disagreement rejects the candidate even if loss is close. Missing independent records or inconsistent workload settings invalidate aggregation. Preserve slower trials and avoid selecting only favorable orderings.

Avoid reporting MFU without disclosing the FLOP model or including data/optimizer time inconsistently.

## Takeaways and next step

Deliver a report with invariants, numerical gates, all trials, mechanism, limitations, and a rejected hypothesis. A later real-transformer study needs its own FLOP model, valid-token accounting, communication evidence, and quality evaluation.

Publish the model and measurement boundary, then make a scoped keep/reject decision.

State the strongest claim the evidence supports and one claim it does not.
