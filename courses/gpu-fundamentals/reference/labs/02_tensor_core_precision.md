# Lab 02: Compare matrix precision, error, and throughput

Reduced precision can unlock faster matrix paths, but a faster multiplication is useful only when its numerical error is acceptable. This lab compares four concrete PyTorch matmul modes on H100. You will separate storage dtype, internal matmul policy, and observed accuracy instead of treating every FP32 tensor as a promise of identical arithmetic.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one full H100 and the approved PyTorch environment. Begin with small-sized matrices; `--matrix-size` overrides the preset size when a controlled square-matrix comparison is needed. Keep all other settings fixed.

Hopper supports BF16, TF32, FP16, and FP8 Tensor Core paths. H100 is not a Blackwell NVFP4 device; later-generation formats are comparison material, not part of this runnable contract.

## Concepts and code path

A matmul precision policy controls the internal arithmetic allowed for FP32 matrix multiplication without changing its input or output storage dtype. `highest` uses FP32 internal arithmetic. `high` permits supported faster implementations with reduced internal precision, falling back to `highest` when those implementations are unavailable. The setting alone does not prove which kernel was selected.

Matrix multiplication combines rows and columns: multiplying `A[M,K]` by `B[K,N]` produces `C[M,N]`, where each output sums `K` products. Counting a multiplication and an addition as two floating-point operations gives approximately `2*M*N*K` FLOPs, or `2*N^3` for square matrices. For example, a 2-by-3 matrix times a 3-by-4 matrix produces eight outputs with three products each.

The L2 norm is the square root of the sum of squared elements: it measures the length of the flattened output vector. Relative L2 error divides the error vector's norm by the nonzero reference vector's norm. For reference [3, 4] and observed [3, 4.1], the reference norm is 5, the error norm is 0.1 and relative error is 0.02. This summarizes the whole output; it does not bound every element as `allclose` does. The supplied random reference is expected to have nonzero norm. An all-zero reference extension needs a separately declared absolute-error or stabilized-denominator rule, not an undefined division by zero.

The lab constructs matrix inputs and an FP32 highest-policy reference, then runs FP32 with highest matmul precision, FP32 with high precision, BF16, and FP16. That reference matches the FP32-highest baseline; it is not an independent FP64 accuracy oracle. Warmed CUDA-event samples measure each mode. The conventional square GEMM numerator is approximately `2*N^3` FLOPs; achieved TFLOP/s is an accounting rate, not proof of a particular emitted instruction.

Given an aligned matrix multiply with BF16 inputs, FP32 accumulation, and a trusted FP32 reference result, measure kernel dispatch and error. Change only the hidden width to an irregular shape. Expected observation: the dtype remains BF16 while kernel choice and time may change; neither result is accepted unless its output stays within the declared tolerance.

The four implemented modes are FP32 with highest matmul precision, FP32 with high precision, BF16, and FP16. All modes use the same FP32-highest reference. FP8 is outside this experiment.

## Practice

`labs/02_tensor_core_precision.py` benchmarks matrix multiplication using FP32, TF32, BF16, and FP16. It records timing, throughput, and relative error against FP32, rejecting results outside the lab's teaching tolerance.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_tensor_core_precision/logs/%j.out" \
  --error="$PWD/results/02_tensor_core_precision/logs/%j.err" \
  slurm/02_tensor_core_precision.sbatch --workload small
```

## Check your results

Each new job owns `results/02_tensor_core_precision/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/02_tensor_core_precision/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/02_tensor_core_precision/logs/$LAB_JOB_ID.out"
cat "results/02_tensor_core_precision/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Inspect each mode's error report, timing distribution, and `achieved_tflops`. The supplied gate is finite output with relative L2 error below 0.1, a deliberately broad teaching check. It is not the course's stricter elementwise tolerance and does not establish training-quality equivalence.

Record dispatch/profiler evidence, input, accumulation and output dtypes, shapes, time, memory, and maximum/relative error.

A dtype is accepted only when the real operation uses the intended path and meets its quality tolerance.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Modes / case / timing / median (seconds) | `modes.*.timing.median_ms` | `s` |
| Modes / case / achieved tflops | `modes.*.achieved_tflops` | `FLOPS` |
| Modes / case / relative l2 error | `modes.*.relative_l2_error` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 02_tensor_core_precision \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the supplied survey first. Repeat a declared matrix size in a separate job if you want to examine shape effects without simultaneously changing the precision comparison.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_tensor_core_precision/logs/%j.out" \
  --error="$PWD/results/02_tensor_core_precision/logs/%j.err" slurm/02_tensor_core_precision.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_tensor_core_precision/logs/%j.out" \
  --error="$PWD/results/02_tensor_core_precision/logs/%j.err" slurm/02_tensor_core_precision.sbatch --workload small --matrix-size 2048
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Explain why BF16's range differs from FP16's and why a matmul policy can affect FP32 performance. Use a selected profiler report before claiming Tensor Core execution; a throughput number alone cannot identify the instruction path.

Reduced precision lowers memory and compute cost only where supported kernels dominate. Scaling, casts, small shapes, fallback kernels, and quality checks can erase the benefit. Accuracy acceptance belongs to the model or application contract.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_tensor_core_precision/logs/%j.out" \
  --error="$PWD/results/02_tensor_core_precision/logs/%j.err" slurm/02_tensor_core_precision.nsys.sbatch --workload small
```

The native Systems command is in `slurm/02_tensor_core_precision.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_tensor_core_precision/logs/%j.out" \
  --error="$PWD/results/02_tensor_core_precision/logs/%j.err" slurm/02_tensor_core_precision.ncu.sbatch --workload small
```

The native Compute command is in `slurm/02_tensor_core_precision.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--matrix-size` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `matrix_size` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Non-finite output invalidates timing interpretation. Reduce input scale or investigate precision behavior as a new trial, not by silently relaxing the gate. If a mode is unsupported, retain the explicit failure rather than substituting a dtype.

Avoid assuming every operation in a mixed-precision region executes on Tensor Cores.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Precision is an accuracy–performance contract. Add stricter elementwise checks and a task-level loss/quality experiment before accepting a production change. FP8 scaling recipes and gradient behavior belong to the Training course's dedicated lab.

Verify eligible kernels and compare outputs; do not infer acceleration from dtype alone.

Separate storage dtype, input dtype, accumulation dtype, and output dtype.
