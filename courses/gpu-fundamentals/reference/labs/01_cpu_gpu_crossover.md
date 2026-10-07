# Lab 01: Find the CPU–GPU crossover for vector work

A GPU can execute arithmetic quickly while still losing an application comparison because transferring data and launching work take time. This lab evaluates the same expression, `x * y + x`, on the CPU, on tensors already stored in GPU memory, and on the GPU with input and output transfers included. You will use CPU timers and CUDA events to compare these cases and state exactly which operations each measurement includes.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 and the Fundamentals environment. The small profile tests 1,024 and 1,000,000 FP32 elements; the large profile adds 32,000,000. A profile selects workload size, not a different correctness standard. No dataset is needed.

H100 provides enormous parallel and matrix throughput, but it does not remove Python dispatch, launch latency, PCIe or network transfer, or application queueing. Large H100 peak numbers are relevant only after the measured path supplies enough eligible work.

## Concepts and code path

An elementwise operation applies the same formula independently at each position. Here `x * y + x` multiplies corresponding input values and adds `x`. The CPU computes the reference answer; a faster GPU result is useful only if it agrees with that answer.

Floating-point arithmetic rounds values. This lab accepts each GPU value only when `abs(candidate - reference) <= atol + rtol * abs(reference)`, using `atol=1e-6` and `rtol=1e-5`. Absolute tolerance allows a fixed difference near zero; relative tolerance scales with the CPU reference. For reference 2, the allowance is `2.1e-5` (0.000021); for reference zero, it is 0.000001. Every element must pass. A finite result alone does not establish agreement. Pure copies of fixed values can instead require exact equality.

Warm-up executes the GPU operation before steady-state samples so first-use setup is not included. The program creates CPU inputs and copies them to GPU memory once per size for the case with device-resident inputs. It then measures three cases:

| Result field | Timer and included work |
| --- | --- |
| `cpu_median_ms` | `time.perf_counter()` around `x * y + x` on CPU tensors. |
| `gpu_resident` | Timing-enabled CUDA events around the GPU expression, using inputs already in GPU memory. The helper records the start event, submits the expression, records the end event, waits for it with `end.synchronize()`, then reads `start.elapsed_time(end)`. |
| `gpu_with_transfers_median_ms` | `time.perf_counter()` before copying both CPU inputs to the GPU, through the GPU expression and `result_gpu.cpu()`. This output copy completes before the CPU timer stops. |

The CUDA-event interval excludes the initial input copies and does not return the output to the CPU. It can include idle gaps and waits between the events; it is not a sum of active kernel durations. One PyTorch expression can launch multiple kernels. These are three measurements of different amounts of work, not three consecutive execution phases. Repeat measurements to see variation rather than relying on one sample.

Slurm assigns cluster resources to jobs. The `sbatch` commands below run the script inside a GPU allocation; they do not execute GPU work on the login host.

For an illustrative calculation, suppose the CPU expression takes 8 microseconds, host submission takes 10 microseconds in total, GPU execution takes 2 microseconds, and each of the two input copies and one output copy takes 7 microseconds. Assume these costs do not overlap and there are no other delays. The GPU computation takes 2 microseconds, but the complete GPU request takes 10 + 2 + 3 × 7 = 33 microseconds. Now suppose millions of values make the CPU take 900 microseconds, while submission and copies total 140 microseconds and GPU execution takes 100 microseconds: 240 microseconds end to end. The preferred processor changes with the workload and included operations. These are assumed numbers, not measured results or a prediction of the CUDA-event samples.

## Practice

`labs/01_cpu_gpu_crossover.py` evaluates `x * y + x` on the CPU, resident GPU inputs, and GPU inputs requiring transfers. It checks GPU agreement with the CPU reference and writes timing comparisons for each element count.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" \
  slurm/01_cpu_gpu_crossover.sbatch --workload small
```

## Check your results

Each new job owns `results/01_cpu_gpu_crossover/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/01_cpu_gpu_crossover/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/01_cpu_gpu_crossover/logs/$LAB_JOB_ID.out"
cat "results/01_cpu_gpu_crossover/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require FP32 agreement with the CPU expression at `rtol=1e-5, atol=1e-6`. Compare `cpu_median_ms`, the `gpu_resident` distribution, and `gpu_with_transfers_median_ms` for each element count. No particular crossover or speedup is guaranteed.

Record CPU elapsed time, the CUDA-event interval, end-to-end time including transfers, tensor size, dtype and warm-up count. For each of the two GPU cases, identify the smallest tested size that beats the CPU, or record that none did.

A crossover is a property of the operation, software stack, and system—not a universal tensor size.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Sizes / case / cpu median (seconds) | `sizes.*.cpu_median_ms` | `s` |
| Sizes / case / gpu resident / median (seconds) | `sizes.*.gpu_resident.median_ms` | `s` |
| Sizes / case / gpu with transfers median (seconds) | `sizes.*.gpu_with_transfers_median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 01_cpu_gpu_crossover --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_cpu_gpu_crossover \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Start small, then repeat with the larger profile only after correctness passes. Both profiles run all three timing cases, so no source edit is required for this comparison.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" slurm/01_cpu_gpu_crossover.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" slurm/01_cpu_gpu_crossover.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which measurement represents a pipeline that keeps intermediate tensors on the GPU? Which represents a one-off request with CPU inputs and a CPU output? Explain why enlarging the tensor may amortize launch overhead while increasing transfer time.

Moving work to the GPU can improve throughput while worsening single-request latency or memory pressure. Keeping control-heavy work on the CPU can be correct even when a GPU implementation exists. Choose against the service objective, not a device label.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" slurm/01_cpu_gpu_crossover.nsys.sbatch --workload small
```

The native Systems command is in `slurm/01_cpu_gpu_crossover.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" slurm/01_cpu_gpu_crossover.ncu.sbatch --workload small
```

The native Compute command is in `slurm/01_cpu_gpu_crossover.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Open **CPU–GPU crossover by element count** for each slot. Plot CPU, resident GPU, and GPU including transfers at every element count. Locate `cpu_expression`, `h2d_inputs`, `gpu_expression`, and `d2h_output` in Systems. Explain why a CPU-input/CPU-output request includes copies absent from the resident measurement. Data residency is the guided tuning control; changing `small` to `large` only changes the tested sizes.

Guided comparison: Compare CPU, resident-GPU, and transfer-inclusive timing at the same element count. Choose data residency for a repeated pipeline; independently find the first tested size where each GPU path beats the CPU and explain the transfer penalty.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

If small-case times round to nearly zero, increase repetitions and inspect timer resolution rather than reporting an infinite speedup. If memory allocation fails on the large profile, retain small results and record the capacity limit.

Stopping a CPU timer immediately after an asynchronous launch measures submission time and omits unfinished GPU work. A CPU timer can measure a complete GPU request when the required synchronization or blocking output copy occurs before the timer stops.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Placement decisions depend on data lifetime as well as arithmetic speed. Extend the experiment by applying several operations before copying the result back, keeping the same final expression and correctness reference when comparing alternatives.

Prefer the GPU when there is enough parallel work and reuse to repay launches and transfers; keep tiny control-heavy work on the CPU.

For every reported time, name the timer, the included operations and how completion is established.
