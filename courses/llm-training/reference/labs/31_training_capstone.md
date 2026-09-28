# Lab 31: Validate an optimization across complete training updates

This capstone compares a baseline linear training step with a candidate using a library bias path while preserving the intended update. You will combine correctness, repeated timing, peak memory, and explicit FLOP accounting into a bounded decision. The goal is a reproducible causal report, not a large utilization number detached from the work actually performed.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/31_training_capstone.json).

Use one H100 and complete the preceding measurement and profiler exercises. Each lab invocation is one fresh-process trial; the supplied campaign launcher runs three and alternates variant order. Keep all trial records private.

Every input to the capstone aggregator must contain explicit
`experiment.instrumented: false` provenance. Profiled inputs, missing or
malformed provenance, and records declaring
`measurements.acceptance_timing: false` are rejected before an aggregate is
written. Rerun affected trials without profiling; do not edit diagnostic
records to make them appear clean.

GELU (Gaussian error linear unit) is an activation function: it applies a nonlinear transformation after a learned linear operation. It multiplies each input by a smooth factor between zero and one, strongly suppressing very negative inputs while approaching the identity for very positive inputs. This capstone applies GELU to the matrix-plus-bias result before computing the loss.

Profile this lab's matched linear/GELU/bias baseline and candidate separately from acceptance timing. A profile from a different transformer workload cannot explain this comparison. Use the source's actual arguments and identical input shapes and update semantics; profiling is not a separate CLI mode.

H100 results are cluster-, shape-, stack-, and topology-specific. Static validation prepares the campaign; only target runs can support speed, scaling, or MFU claims.

## Concepts and code path

The source creates matched inputs, weights, and bias, verifies one full baseline/candidate update, then times complete steps in the declared order. It records incremental peak allocation and throughput. Fixed inputs require no gradient, so the counted GEMMs are forward XW and weight-gradient X.T@dY: `4*tokens*width^2` FLOPs, not the usual three-GEMM heuristic. Other step operations remain inside timing but outside that numerator.

The supplied baseline computes `inputs @ weight + bias`; the candidate expresses the same biased product with `torch.addmm`. Both then apply GELU, compute mean-squared loss, backpropagate and update weight and bias with SGD. Shape, data, dtype, initial parameters and update count stay fixed. Across three fresh seeded processes, alternate which path runs first and retain every timing and memory result. A shorter forward path can still lose over the complete update, so an unfavorable or mixed result remains valid evidence.

Use separate unprofiled runs of `slurm/capstone_three_trials.sbatch` for three fresh-process trials with alternating variant order. Derive the FLOP count before interpreting utilization: fixed inputs require no gradient, so forward XW and weight-gradient X.T @ dY cost 4 × tokens × width² FLOPs together. There is no input-gradient GEMM. Bias, GELU, loss and optimizer work remain outside this matmul-only numerator, but inside full-step timing. This lower-bound estimate is not full-transformer model FLOPs utilization (MFU); an input requiring gradients would need a third GEMM.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Use the campaign launcher for the required three independent processes. Supply a peak-TFLOP/s reference only after verifying the exact H100 variant and arithmetic mode; an arbitrary peak makes utilization meaningless.

```bash
umask 077
"$COURSE_PYTHON" labs/31_training_capstone.py --help
python3 tools/submit_lab.py --lab 31_training_capstone slurm/capstone_three_trials.sbatch --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 31_training_capstone --job "$LAB_JOB_ID"
```

Require `full_update_close` in every trial. Inspect `variant_order`, timing distributions, incremental peaks, candidate tokens/s, and `minimum_step_flops`. A single record remains provisional until the campaign is complete; matmul-only utilization is not full-transformer MFU.

The gate checks finite gradients and compares each parameter's gradients after
scaling by the reference gradient's maximum magnitude, with
`rtol=1e-2, atol=1e-2`. Each result must also exactly match plain SGD applied to
its initial parameters and its own gradients, and some parameter must change.
A candidate that skips backward or the optimizer cannot pass merely because
its parameters remain close to their initial values.

The aggregator requires complete, matching profile, observed GPU/software, shape and
measurement-option fields. Every timing and the derived ratio must be finite
and positive; missing fields, boolean timings and non-finite values cannot
produce a keep decision. The optional peak-TFLOPS reference may remain unset.

Retain baseline/candidate configs, loss and update checks, traces, samples, memory, throughput, communication, and decision. Also record NUMA placement, observed GPU clocks/boost state, and Python garbage-collection pauses as trial context before attributing a change to model code.

Observed metrics support only the declared model, shapes, nodes, versions, and trial conditions.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Timing / baseline / median (seconds) | `timing.baseline.median_ms` | `s` |
| Timing / candidate / median (seconds) | `timing.candidate.median_ms` | `s` |
| Incremental peak bytes / baseline | `incremental_peak_bytes.baseline` | `bytes` |
| Incremental peak bytes / candidate | `incremental_peak_bytes.candidate` | `bytes` |
| Candidate tokens per second | `candidate_tokens_per_second` | `tokens/s` |

Complete each three-trial group for acceptance. For repeated publication, run
the complete launcher twice in the same profile, retaining all six clean child
records and both validated aggregates. Pair corresponding children with the
same seed and variant order: 17 with 17, 18 with 18, and 19 with 19. Publish each
pair separately and review its generation before selecting the next pair.
Each child already contains the internal baseline/candidate comparison; the
dashboard pair does not replace either three-trial aggregate, and the two
groups must not be combined into one aggregate. Select only successful,
equivalent, unprofiled originals. On the login node, set the paths to the printed
result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 31_training_capstone \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Does the candidate help at the complete-step boundary, not just one operator? Explain the profiler-observed mechanism and any order sensitivity. Identify which omitted arithmetic makes the reported FLOP numerator a lower-bound accounting convention.

A slower step may be accepted if it enables the required batch or removes OOM risk. A faster step may be rejected for quality drift, fragility, startup cost, memory, or a negligible end-to-end contribution.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 31_training_capstone --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/31_training_capstone.py --profile small --variant-order baseline-first
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 31_training_capstone --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch labs/31_training_capstone.py --profile small --variant-order baseline-first
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare baseline and candidate complete steps using all three independent seeded trials. Independently explain a counterexample or order sensitivity before deciding whether to retain the candidate.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Update disagreement rejects the candidate even if loss is close. Missing independent records or inconsistent workload settings invalidate aggregation. Preserve slower trials and avoid selecting only favorable orderings.

Avoid reporting MFU without disclosing the FLOP model or including data/optimizer time inconsistently.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Deliver a report with invariants, numerical gates, all trials, mechanism, limitations, and a rejected hypothesis. A later real-transformer study needs its own FLOP model, valid-token accounting, communication evidence, and quality evaluation.

Publish the model and measurement boundary, then make a scoped keep/reject decision.

State the strongest claim the evidence supports and one claim it does not.
