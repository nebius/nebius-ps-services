# Lab 08: Map PyTorch operations to GPU activity

One line of tensor code can trigger several framework operations and many GPU kernels. This lab profiles a small projection, normalization, and activation chain so you can relate high-level intent to execution cost. It is an introductory evidence-reading exercise: the output helps you choose what to inspect next, not prove an optimization by itself.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and an environment in which PyTorch Profiler can capture CPU and CUDA activity. Keep profiler and scheduler output private. Run this before the deeper Nsight exercises in GPU Optimizations.

## Concepts and code path

A matrix multiplication combines rows and columns: shapes M×K and K×N produce M×N values. For example, `[1, 2]` times the column `[3, 4]` gives 11. The multiply-plus-add convention counts approximately `2*M*N*K` floating-point operations. GEMM names general matrix multiplication, often written `C = alpha*A*B + beta*C`. In this lab, Python `@` performs the projection; `*` multiplies corresponding elements. A projection changes feature coordinates; broadcasting applies the same bias vector to every output row.

Layer normalization subtracts a feature vector's mean and divides by the square root of its variance plus a small epsilon. Learned scale and offset can follow. GELU, the Gaussian error linear unit, is the nonlinear activation `x*Phi(x)`, where Phi is the standard normal cumulative probability: it attenuates strongly negative inputs and approaches the identity for strongly positive ones. Activations are intermediate values; an activation function transforms them. BF16 is a 16-bit format with a wide exponent range and fewer precision bits than FP32.

A profiler records execution activity and groups related events. Self time excludes recorded child events; inclusive time includes them. For example, a parent with 10 microseconds of inclusive time and a 7-microsecond child has 3 microseconds of self time under that nesting. Adding parent-inclusive and child time would double-count the child. GPU overlap and framework event attribution also mean a table sum is not automatically application wall time.

CPU and CUDA activity collection associates framework operations with the kernels they submit. Profiling adds overhead; use an unprofiled timer for a performance comparison.

The workload multiplies BF16 activations by weights, applies layer normalization, adds bias, applies GELU, then squares and averages the output. Warm-up precedes the profiled loop. The profiler aggregates events and the program selects the ten largest self-CUDA-time entries. Although the scalar is named `loss`, this workload has no backward pass or optimizer step.

## Practice

`labs/08_operator_to_kernels.py` executes matrix multiplication, normalization, activation, and reduction while collecting PyTorch CPU/CUDA events. It writes a bounded operator summary and checks finite work; external capture disables its internal profiler.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_operator_to_kernels/logs/%j.out" \
  --error="$PWD/results/08_operator_to_kernels/logs/%j.err" \
  slurm/08_operator_to_kernels.sbatch --workload small
```

## Check your results

Each new job owns `results/08_operator_to_kernels/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/08_operator_to_kernels/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/08_operator_to_kernels/logs/$LAB_JOB_ID.out"
cat "results/08_operator_to_kernels/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require a finite scalar and captured CUDA events. Inspect `unique_profile_events`, `cuda_events_with_device_time`, and each top event's `calls` and `self_cuda_time_us`. These gates confirm executed work, not numerical equivalence against a reference.

The profiler reads PyTorch's `self_device_time_total` field from averaged
events and reports the captured CUDA device durations as `self_cuda_time_us`.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cuda events with device time | `cuda_events_with_device_time` | `none` |

Select two successful, equivalent diagnostic runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot; the two slots are independent diagnostic repetitions. Their instrumented durations are not acceptance timings. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 08_operator_to_kernels \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Start with small shapes to learn the event table. Use the larger workload preset only as a separately declared shape comparison; retain the shape in your notes.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_operator_to_kernels/logs/%j.out" \
  --error="$PWD/results/08_operator_to_kernels/logs/%j.err" slurm/08_operator_to_kernels.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_operator_to_kernels/logs/%j.out" \
  --error="$PWD/results/08_operator_to_kernels/logs/%j.err" slurm/08_operator_to_kernels.sbatch --workload large
```

Keep a fixed workload preset for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Match each expensive event to the expression that can cause it. Distinguish self time from inclusive time before summing rows. Ask whether repeated small launches or one large GEMM dominates the profile.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_operator_to_kernels/logs/%j.out" \
  --error="$PWD/results/08_operator_to_kernels/logs/%j.err" slurm/08_operator_to_kernels.nsys.sbatch --workload small --external-only
```

The native Systems command is in `slurm/08_operator_to_kernels.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_operator_to_kernels/logs/%j.out" \
  --error="$PWD/results/08_operator_to_kernels/logs/%j.err" slurm/08_operator_to_kernels.ncu.sbatch --workload small --external-only
```

The native Compute command is in `slurm/08_operator_to_kernels.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Follow the eager expression through operator rows to individual CUDA kernels. Independently select its most expensive kernel for Compute and identify the next implementation change to test; profiler timings remain diagnostic.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure after warmup and follow host submissions into GPU streams. Compare this external diagnostic with the PyTorch operator attribution; use a separate timing lab to measure an optimization.

## If something goes wrong

An empty CUDA table is missing evidence, not proof that the workload costs nothing. Check CUDA activity support and profiler warnings. If event attributes differ in your qualified environment, record the API mismatch before modifying the reporting code.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Operator names provide a starting map, not a one-to-one kernel identity. Next, select a focused workload in GPU Optimizations and combine a timeline with uninstrumented timings before deciding which operation deserves attention.
