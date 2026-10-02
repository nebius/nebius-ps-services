# Lab 09: Build a causal compilation experiment

This capstone turns a promising optimization into a controlled comparison. You will evaluate eager versus compiled execution for the same BF16 matmul-and-activation pipeline, preserving shapes, inputs, residency, and mathematical work. The intended deliverable is a defensible conclusion—including an inconclusive or slower candidate—not a predetermined speedup.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Complete timing, compilation, and profiler labs on one H100. Prepare a benchmark worksheet that names the independent variable and acceptance metric. The compiler path uses `fullgraph=True`: the requested function must be captured as one compiler graph, and a graph break that prevents this capture raises an error. One compiler graph can still produce several GPU kernels. Compilation must succeed before any candidate timing is accepted.

## Concepts and code path

SiLU (sigmoid linear unit) gates each input by its sigmoid: `x / (1 + exp(-x))`. The sigmoid is a smooth function between zero and one. The hyperbolic tangent, `tanh`, maps finite real inputs smoothly between -1 and 1. Both activations act elementwise here.

The workload is matmul followed by SiLU, scalar addition, and tanh. Both variants reuse the same resident inputs. Compilation occurs before measured rounds. Three rounds alternate variant order and collect warmed CUDA-event distributions plus synchronized wall distributions. These rounds share one process and compiler state; they are not three independent fresh-process trials.

## Practice

`labs/09_capstone.py` compares eager and compiled execution of the same resident BF16 matrix-and-activation workload across three internal trials. It checks agreement and records controlled settings, trial timings, and observed ratios.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/09_capstone/logs/%j.out" \
  --error="$PWD/results/09_capstone/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/09_capstone.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/09_capstone/logs/$LAB_JOB_ID.out"
cat "results/09_capstone/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require BF16 output agreement at `rtol=1e-2, atol=1e-2` in every round. Inspect `controlled_factors`, per-round `execution_order`, CUDA/wall distributions, and the observed ratios. A median ratio above one is a local observation, not a general compiler guarantee.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Trials / case / eager / CUDA-event median (seconds) | `trials.*.eager.cuda_median_ms` | `s` |
| Trials / case / eager / wall-clock median (seconds) | `trials.*.eager.wall_median_ms` | `s` |
| Trials / case / compiled / CUDA-event median (seconds) | `trials.*.compiled.cuda_median_ms` | `s` |
| Trials / case / compiled / wall-clock median (seconds) | `trials.*.compiled.wall_median_ms` | `s` |
| Median observed ratio across trials | `median_observed_ratio_across_trials` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 09_capstone \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the small capstone first. For an acceptance campaign, submit at least three separate jobs and keep all results, including unfavorable runs. Do not compare a fresh baseline with a selectively warmed candidate.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/09_capstone/logs/%j.out" \
  --error="$PWD/results/09_capstone/logs/%j.err" slurm/single_gpu.sbatch labs/09_capstone.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Does the result survive order reversal and separate processes? Use a focused profile to explain changes in launches or intermediate traffic. Reconcile any difference between device-only and synchronized application-boundary conclusions.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/09_capstone/logs/capture-%J-%t.out" \
  --error="results/09_capstone/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/09_capstone/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/09_capstone.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `course_measure`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/09_capstone/logs/capture-%J-%t.out" \
  --error="results/09_capstone/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/09_capstone/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/09_capstone.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare eager and compiled paths across the counterbalanced trials. Independently compare device-region and complete-request conclusions; choose a path only when both the correctness and application boundary support it.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

If full-graph compilation fails, the candidate is unavailable rather than silently equivalent to eager. If ratios fluctuate around one, report uncertainty and gather repeated evidence instead of selecting the best round.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

A causal report combines controlled work, correctness, repeated observations, and a mechanism. Use Lab 16's escalation decision to determine whether the remaining hotspot warrants a library change or a custom-kernel investigation.
