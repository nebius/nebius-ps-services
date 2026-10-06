# Lab 02: Remove an intermediate with elementwise fusion

Two simple kernels can spend more time moving intermediate values than performing arithmetic. This lab compares separate elementwise stages with a fused scale/bias/ReLU calculation using corresponding inputs. You will count logical memory traffic, verify both outputs, and determine whether reducing launches and intermediate storage improves the measured kernel sequence on H100.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the completed SM90 build on one H100. Understand vector indexing and FP32 reference checks. The experiment compares its supplied scalar-scale and bias-array expression, not every possible broadcast layout.

H100 HBM bandwidth is large, so enough elements are needed to amortize launch overhead and measure memory traffic. A compiler or library may already fuse the expression; the custom version must be compared with that best maintained path.

## Concepts and code path

The host prepares input, bias, and reference values. The baseline writes an intermediate that a later kernel reads; the fused kernel computes the same result before writing its final output. Device buffers remain resident throughout timing. Logical traffic counts five FP32 elements per output for the separate path and three for the fused path; actual cache/HBM transactions need profiling.

Given 256 million FP32 elements, each full-array read or write is about 1 GiB. If separate stages materialize two intermediates, they add roughly 4 GiB of write/read traffic. Change to one fused kernel. Expected observation: launches and intermediate traffic fall, but speedup is kept only if profiler traffic, correctness, and full application time confirm the predicted mechanism.

A scale multiplies each value, a bias adds an offset, and ReLU replaces negative values with zero. A fused multiply-add evaluates `a*b+c` with one final rounding. Count the logical bytes read and written by each complete path.

## Practice

`labs/02_fused_elementwise.cu` compares separate scale/bias and ReLU kernels with one fused kernel. It checks both outputs against a CPU reference and reports timing distributions and logical memory traffic. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_fused_elementwise/logs/%j.out" \
  --error="$PWD/results/02_fused_elementwise/logs/%j.err" \
  slurm/02_fused_elementwise.sbatch --workload small
```

## Check your results

Each new job owns `results/02_fused_elementwise/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/02_fused_elementwise/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/02_fused_elementwise/logs/$LAB_JOB_ID.out"
cat "results/02_fused_elementwise/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require both baseline and candidate CPU-reference checks at FP32 tolerances. Inspect `separate` and `fused` timing distributions, `logical_separate_bytes`, and `logical_fused_bytes`. A reduction in logical traffic does not automatically produce an equal percentage reduction in runtime.

Retain launches, logical and estimated physical bytes, bandwidth, time, and numerical equality.

Fusion helps when removed traffic or launch overhead lies on the critical path.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Separate median (seconds) | `separate_median_ms` | `s` |
| Fused median (seconds) | `fused_median_ms` | `s` |
| Logical fused bytes | `logical_fused_bytes` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 02_fused_elementwise --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 02_fused_elementwise \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the paired small paths and a memory check, then use the full case if correctness passes. Both variants are measured by the same executable.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_fused_elementwise/logs/%j.out" \
  --error="$PWD/results/02_fused_elementwise/logs/%j.err" slurm/02_fused_elementwise.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_fused_elementwise/logs/%j.out" \
  --error="$PWD/results/02_fused_elementwise/logs/%j.err" slurm/02_fused_elementwise.sanitizer.sbatch memcheck --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw the intermediate write/read removed by fusion. Which cost matters more for a tiny array: bytes or launches? What extra live values could increase register pressure if many more operations were fused?

Fusion can increase registers and code size, reduce reusable intermediates, complicate dynamic broadcasting, and create many variants. It also changes floating-point evaluation order, requiring explicit tolerance.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_fused_elementwise/logs/%j.out" \
  --error="$PWD/results/02_fused_elementwise/logs/%j.err" slurm/02_fused_elementwise.nsys.sbatch --workload small
```

The native Systems command is in `slurm/02_fused_elementwise.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_fused_elementwise/logs/%j.out" \
  --error="$PWD/results/02_fused_elementwise/logs/%j.err" slurm/02_fused_elementwise.ncu.sbatch --workload small
```

The native Compute command is in `slurm/02_fused_elementwise.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare separate kernels with the fused expression. Independently change the source threads constant from 256 to 128, rebuild, and inspect whether launch geometry changes the conclusion while preserving elements and tolerances.

After changing the CUDA source, rerun this lab’s CUDA preparation from the Lab Guide to compile a new private build with the same pinned toolkit. Then repeat the original run and capture commands.

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A ReLU mismatch may reflect sign handling or changed expression order. Verify both negative and positive reference outputs. Do not accept a candidate that skips work or only matches the easy positive-input region.

Avoid counting only input/output tensors and ignoring intermediate materialization.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Fusion is valuable when it removes material overhead without violating semantics or resource constraints. Extend the experiment by integrating the operation into an application, and include any changes in allocation or copy costs before claiming an end-to-end benefit.

Count the bytes read and written and validate the fused expression against the unfused reference.

State when excessive fusion can increase registers or reduce maintainability.
