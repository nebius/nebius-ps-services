# Lab 08: Map PyTorch operations to GPU activity

One line of tensor code can trigger several framework operations and many GPU kernels. This lab profiles a small projection, normalization, and activation chain so you can relate high-level intent to execution cost. It is an introductory evidence-reading exercise: the output helps you choose what to inspect next, not prove an optimization by itself.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/08_operator_to_kernels.json).

Use one H100 and an environment in which PyTorch Profiler can capture CPU and CUDA activity. Keep profiler and scheduler output private. Run this before the deeper Nsight exercises in GPU Optimizations.

## Concepts and code path

A matrix multiplication combines rows and columns: shapes M×K and K×N produce M×N values. For example, `[1, 2]` times the column `[3, 4]` gives 11. The multiply-plus-add convention counts approximately `2*M*N*K` floating-point operations. GEMM names general matrix multiplication, often written `C = alpha*A*B + beta*C`. In this lab, Python `@` performs the projection; `*` multiplies corresponding elements. A projection changes feature coordinates; broadcasting applies the same bias vector to every output row.

Layer normalization subtracts a feature vector's mean and divides by the square root of its variance plus a small epsilon. Learned scale and offset can follow. GELU, the Gaussian error linear unit, is the nonlinear activation `x*Phi(x)`, where Phi is the standard normal cumulative probability: it attenuates strongly negative inputs and approaches the identity for strongly positive ones. Activations are intermediate values; an activation function transforms them. BF16 is a 16-bit format with a wide exponent range and fewer precision bits than FP32.

A profiler records execution activity and groups related events. Self time excludes recorded child events; inclusive time includes them. For example, a parent with 10 microseconds of inclusive time and a 7-microsecond child has 3 microseconds of self time under that nesting. Adding parent-inclusive and child time would double-count the child. GPU overlap and framework event attribution also mean a table sum is not automatically application wall time.

CPU and CUDA activity collection associates framework operations with the kernels they submit. Profiling adds overhead; use an unprofiled timer for a performance comparison.

The workload multiplies BF16 activations by weights, applies layer normalization, adds bias, applies GELU, then squares and averages the output. Warm-up precedes the profiled loop. The profiler aggregates events and the program selects the ten largest self-CUDA-time entries. Although the scalar is named `loss`, this workload has no backward pass or optimizer step.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Start with small shapes to learn the event table. Use the larger profile only as a separately declared shape comparison; retain the shape in your notes.

```bash
umask 077
python3 tools/submit_lab.py --lab 08_operator_to_kernels slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile small
python3 tools/submit_lab.py --lab 08_operator_to_kernels slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 08_operator_to_kernels --job "$LAB_JOB_ID"
```

Require a finite scalar and captured CUDA events. Inspect `unique_profile_events`, `cuda_events_with_device_time`, and each top event's `calls` and `self_cuda_time_us`. These gates confirm executed work, not numerical equivalence against a reference.

The profiler reads PyTorch's `self_device_time_total` field from averaged
events and reports the captured CUDA device durations as `self_cuda_time_us`.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cuda events with device time | `cuda_events_with_device_time` | `none` |

Select two successful, equivalent diagnostic runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot; the two slots are independent diagnostic repetitions. Their instrumented durations are not acceptance timings. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 08_operator_to_kernels \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Match each expensive event to the expression that can cause it. Distinguish self time from inclusive time before summing rows. Ask whether repeated small launches or one large GEMM dominates the profile.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 08_operator_to_kernels --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile small --external-only
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 08_operator_to_kernels --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile small --external-only
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Follow the eager expression through operator rows to individual CUDA kernels. Independently select its most expensive kernel for Compute and identify the next implementation change to test; profiler timings remain diagnostic.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

An empty CUDA table is missing evidence, not proof that the workload costs nothing. Check CUDA activity support and profiler warnings. If event attributes differ in your qualified environment, record the API mismatch before modifying the reporting code.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Operator names provide a starting map, not a one-to-one kernel identity. Next, select a focused workload in GPU Optimizations and combine a timeline with uninstrumented timings before deciding which operation deserves attention.
