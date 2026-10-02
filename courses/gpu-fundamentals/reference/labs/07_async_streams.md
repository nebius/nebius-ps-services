# Lab 07: Separate submission time from device completion

GPU work is normally queued asynchronously, so a short Python call does not mean a short GPU operation. This lab compares host submission time, serial device execution, and independent-stream execution for matrix work. It helps you understand when parallel queues can expose concurrency and why shared GPU resources can still prevent a speedup.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 after the timing and transfer lessons. Inputs are resident on the device. This is a compute-stream experiment, not a demonstration of a complete host-input pipeline.

## Concepts and code path

The program creates independent matrix computations, times host enqueue calls, and uses CUDA events for device intervals. The concurrent path schedules independent work on separate streams and joins dependencies before observing the result. Correct ordering is part of the algorithm: a consumer must not read an unfinished producer's tensor just because Python has returned.

Given a 5-millisecond copy followed by 8 milliseconds of independent compute, a serial schedule takes about 13 milliseconds after warm-up. Change to a correctly event-ordered double buffer. Expected observation: steady-state iterations can approach 8 milliseconds, but the first still fills and the last drains; two immediate CPU timestamps measure only submission.

This is a schematic copy/compute pipeline, not the supplied resident-matrix experiment.

## Practice

`labs/07_async_streams.py` runs two matrix multiplications sequentially and on independent CUDA streams. It checks matching outputs and records host enqueue time separately from completed GPU timing.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/07_async_streams/logs/%j.out" \
  --error="$PWD/results/07_async_streams/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/07_async_streams.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/07_async_streams/logs/$LAB_JOB_ID.out"
cat "results/07_async_streams/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `allclose` between corresponding serial and concurrent results. Compare `host_enqueue_median_ms`, `sequential_device`, and `independent_streams_device`. Do not divide the FLOP count by host submission time and call the result device throughput.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Host enqueue median (seconds) | `host_enqueue_median_ms` | `s` |
| Sequential device / median (seconds) | `sequential_device.median_ms` | `s` |
| Independent streams device / median (seconds) | `independent_streams_device.median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 07_async_streams \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the paired serial and stream paths together. Repeat the larger profile separately to investigate whether a single operation already consumes most of the device's available resources.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/07_async_streams/logs/%j.out" \
  --error="$PWD/results/07_async_streams/logs/%j.err" slurm/single_gpu.sbatch labs/07_async_streams.py --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/07_async_streams/logs/%j.out" \
  --error="$PWD/results/07_async_streams/logs/%j.err" slurm/single_gpu.sbatch labs/07_async_streams.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Mark the start, finish, and join on a two-stream timeline. If streams do not improve elapsed time, ask whether GEMMs already saturate compute or memory resources. Use a profiler timeline to establish actual overlap.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/07_async_streams/logs/capture-%J-%t.out" \
  --error="results/07_async_streams/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/07_async_streams/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/07_async_streams.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/07_async_streams/logs/capture-%J-%t.out" \
  --error="results/07_async_streams/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include lab_workload/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/07_async_streams/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/07_async_streams.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare sequential and concurrent matrix multiplies with both operations and the final join fixed. Independently identify a timeline interval where streams do or do not overlap and decide whether two streams help this workload.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Intermittent incorrect results after an edit suggest a missing dependency or unsafe tensor lifetime. Restore explicit producer/consumer ordering before benchmarking. Instrumented runs can change scheduling, so preserve unprofiled timing separately.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Streams express independent work; they do not manufacture additional hardware capacity. A useful extension introduces one genuine dependency and demonstrates why its consumer must wait while unrelated work remains eligible to execute.
