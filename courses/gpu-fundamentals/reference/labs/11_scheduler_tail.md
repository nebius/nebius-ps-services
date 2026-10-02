# Lab 11: Separate lane utilization from grid-tail behavior

Uneven work can waste execution capacity at more than one level. This lab deliberately separates a Python model of loop work within warps from an H100 probe of partial grid waves. You will learn which conclusions follow from exact work accounting and which require hardware scheduling evidence rather than a suggestive diagram.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the Fundamentals environment with Triton on one H100. A warp contains 32 lanes; residency is the work that can remain on a streaming multiprocessor (SM) concurrently. The supplied launch runs both the lane-work model and the GPU probe; there is no lane-only mode. The two parts answer different questions and must not be combined into one speedup claim.

H100 schedulers choose among eligible warps on each SMSP. A warp with many inactive lanes can still consume an issue slot; a warp stalled on memory is a different condition and can be hidden by another eligible warp.

## Concepts and code path

The Python model groups fixed lane iteration counts and estimates issued warp iterations. Its mixed and regrouped arrangements preserve useful lane work. Separately, the Triton probe derives an estimated resident-block capacity from resource information, launches grids around wave boundaries, and checks each program's output. Grid cases vary block count: work per program is fixed, but total work is not.

Given 64 tasks: 32 need a 20-instruction branch body and 32 need a separate 4-instruction body. Initially, each of two warps mixes 16 tasks of each kind, so each warp issues both bodies: 2 × (20 + 4) = 48 modeled warp instructions. Change the grouping of the same tasks into one all-long warp and one all-short warp: 20 + 4 = 24. Expected observation: useful task work is unchanged; sorting and restoring output order add costs. This simplified separate-branch model is not a measured speedup and differs from Lab 11's masked common-loop model.

The script runs both the lane-work model and the H100 grid-tail probe. For 64 lanes with half doing 20 iterations and half doing four, both arrangements perform 768 useful lane-iterations; alternating lanes model 60 percent utilization while grouping by warp models 100 percent. This common-loop model is not a GPU branch benchmark and does not model two distinct branch bodies.

## Practice

`labs/11_scheduler_tail.py` combines an analytical lane-utilization model with measured Triton grid-tail cases. It checks every program's output and reports timing plus resource-based residency estimates that still require profiler confirmation.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/11_scheduler_tail/logs/%j.out" \
  --error="$PWD/results/11_scheduler_tail/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/11_scheduler_tail.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/11_scheduler_tail/logs/$LAB_JOB_ID.out"
cat "results/11_scheduler_tail/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

An output sentinel is a recognizable initial value that reveals a missing write. The grid-tail probe fills outputs with NaN outside timing, then runs and synchronizes the kernel. NaN means “not a number”; NaN and infinity are non-finite. Require every expected output to be finite and to match the independent reference: a surviving sentinel detects an omitted write, while the reference detects a wrong value.

Require matching grid outputs and conserved useful work in the lane comparison. The NaN prefill described above must be replaced at every expected output; finite checks detect surviving sentinels, and the independent reference checks the values written. Inspect `lane_work_model`, `resident_block_slots`, each case's `model`, and timing. `profiler_confirmation_required` explicitly limits claims about actual residency and scheduling.

Retain modeled lane utilization separately from measured kernel timing and the grid-wave calculation. Actual branch efficiency or active-thread counters require profiling a real divergent kernel; the uniform Triton tail probe cannot establish that result.

Reordering helps only when it groups similar control flow without adding more movement than it saves.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cases / case / timing / median (seconds) | `cases.*.timing.median_ms` | `s` |
| Cases / case / model / tail utilization | `cases.*.model.tail_utilization` | `none` |
| Resident block slots | `resident_block_slots` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 11_scheduler_tail \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the complete small case first and retain both the modeled lane results and measured grid cases. A second profile is an additional workload, not a fixed-work replacement.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/11_scheduler_tail/logs/%j.out" \
  --error="$PWD/results/11_scheduler_tail/logs/%j.err" slurm/single_gpu.sbatch labs/11_scheduler_tail.py --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/11_scheduler_tail/logs/%j.out" \
  --error="$PWD/results/11_scheduler_tail/logs/%j.err" slurm/single_gpu.sbatch labs/11_scheduler_tail.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Explain why adding one block beyond an estimated full wave can create a nearly empty final wave. Then explain why dividing unlike-total-work timings is not a valid optimization speedup. Recalculate the 768 useful lane-iterations example independently.

Reordering data to group similar branches can improve lane utilization but costs preprocessing, changes locality, and may create load imbalance elsewhere. Branchless arithmetic can execute extra work and is not automatically faster.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/11_scheduler_tail/logs/capture-%J-%t.out" \
  --error="results/11_scheduler_tail/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/11_scheduler_tail/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/11_scheduler_tail.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/11_scheduler_tail/logs/capture-%J-%t.out" \
  --error="results/11_scheduler_tail/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/11_scheduler_tail/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/11_scheduler_tail.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the partial-wave cases and explain their different total work. Independently calculate useful lane-iterations and tail waste; these are workload studies, not equivalent-work speedups.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing compiler resource data can leave an optimistic estimate: the program retains the thread-limit bound and omits unavailable register/shared-memory constraints. Do not treat that incomplete estimate as established residency; obtain independent resource/profiler confirmation. A mismatch near a grid boundary suggests indexing or tail handling, which must be fixed before performance analysis.

Calling every long tail “warp divergence” hides queueing, block imbalance, or insufficient grid waves.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Models clarify mechanisms when their assumptions are explicit. This lab does not benchmark divergent CUDA branches. The Custom Kernels course provides actual divergent/grouped work where regrouping and restoration costs can also be measured.

Identify whether unused execution comes from lane masks, block scheduling, or the final partial wave.

Give one fix for divergence and one different fix for a tail wave.
