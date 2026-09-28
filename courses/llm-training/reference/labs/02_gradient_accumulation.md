# Lab 02: Trade microbatch size for peak memory

When a full batch does not fit, gradient accumulation can split its work across smaller microbatches before an update. This lab compares full-batch and accumulated gradients on a fixed-shape MLP regression problem. You will learn the normalization rule and memory trade-off before applying accumulation to variable-length language-model batches, where valid-token counts add another constraint.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/02_gradient_accumulation.json).

Use one H100 and review backward accumulation into parameter gradients. This lab uses equally sized microbatches and mean-squared error, not token masking or a full language-model optimizer step.

## Concepts and code path

The program initializes matched model/data conditions, computes full-batch gradients, and compares them with gradients accumulated from scaled microbatch losses. Each microbatch contributes its fraction of the full mean. Smaller microbatches primarily reduce simultaneous activation and temporary storage; the parameter-gradient buffers still have the same size. Timing and peak allocation are measured for the two strategies. The implementation checks sampled gradient entries and does not call an optimizer step as part of its equivalence proof.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the predefined small comparison, then the larger profile as a separate batch/shape experiment. The reported batch sizes are the source of truth for interpreting the memory difference.

```bash
umask 077
python3 tools/submit_lab.py --lab 02_gradient_accumulation slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile small
python3 tools/submit_lab.py --lab 02_gradient_accumulation slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 02_gradient_accumulation --job "$LAB_JOB_ID"
```

Require `sampled_gradients_match`. Both strategies' compared gradient samples and each computed error must be finite; a missing gradient or an absolute sample error above `2e-3` fails before a success record is written. Lesson 7 explains why a threshold alone cannot reject NaN reliably. Inspect `total_batch`, `microbatch`, `sampled_max_gradient_error`, and each strategy's median elapsed time and peak allocation. Sampled agreement is narrower than comparing every parameter gradient and one update.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Full batch / median elapsed (seconds) | `full_batch.median_elapsed_ms` | `s` |
| Accumulated / median elapsed (seconds) | `accumulated.median_elapsed_ms` | `s` |
| Full batch / median peak allocated mib | `full_batch.median_peak_allocated_mib` | `bytes` |
| Accumulated / median peak allocated mib | `accumulated.median_peak_allocated_mib` | `bytes` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 02_gradient_accumulation \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Why must gradients be cleared before the accumulation window but not between its microbatches? Explain why simply averaging microbatch means is wrong when the microbatches contain different numbers of valid tokens.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 02_gradient_accumulation --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first model matrix kernel inside `gradient_pass`, excluding random input and target construction. It diagnoses the first full-batch pass; use Systems and the original gradient checks for the accumulated comparison. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 02_gradient_accumulation '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=gradient_pass,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare full-batch updates with the supplied microbatch accumulation reference. Independently account for valid-token normalization and choose accumulation only when the full update remains close.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A constant scaling discrepancy suggests an incorrect loss divisor or an extra gradient reset. A memory result that does not improve may reflect persistent model state dominating activations rather than a broken accumulation mechanism.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Accumulation preserves an objective only with correct weighting and update boundaries. Extend the proof to every gradient and one optimizer update, then add valid-token weighting before using it for unequal-length language-model batches.
