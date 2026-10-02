# Lab 20: Drain a bounded D2H output pipeline

A GPU can finish producing an output while a copy or CPU consumer still owns its data. This lab builds the missing completion path. You will compare serial output handling, worker overlap, pinned-buffer reuse, asynchronous copies and a dedicated egress stream. Each stage checks every output and waits for all consumers, so an apparently faster run cannot simply abandon queued work.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one qualified H100 environment. The synthetic workload produces 512-square FP32 outputs in small mode or 2048-square outputs in large mode. Defaults allow two in-flight slots and two worker threads. `--sink-ms` is a controlled blocking delay, not a measurement of a disk, network or Python postprocessor.

## Concepts and code path

`run_pipeline` uses dense matrix multiplication to generate a unique constant output for each batch. Its source and destination stay owned by a slot until consumption finishes. The producer records a ready event. Copies on the egress stream wait for readiness and record a copied event; `consume_after_copy` synchronizes that event before any CPU read. `check_output` validates every element, applies the synthetic sink delay, and returns the batch ID.

`OutputSlot` holds a future. Acquiring a busy slot waits for its consumer and propagates failure; it never releases a failed future as a successful result. At most one task per slot is outstanding. Final drain acquires every slot, joins the streams, and verifies that all batch IDs were consumed exactly once. Copy completion makes output readable; consumer completion makes its buffer reusable.

A worker thread runs a task inside the same process and shares its memory. CPython is the standard Python implementation; bytecode is the instruction form executed by its interpreter. In a GIL-enabled build, the global interpreter lock allows only one thread to execute that bytecode at a time, but this lab's blocking sleep releases it. A separate process has its own interpreter; sending objects to it can require serialization, which encodes them for communication, and extra memory copies. This is why an I/O-like sink and CPU-heavy Python processing need different concurrency experiments.

The five modes form a progression. `serial` mode consumes inline. `workers` mode keeps blocking copies but submits host work to threads. `pooled` mode additionally preallocates pinned destinations. `nonblocking` mode removes the host copy wait but still uses one GPU stream. `pipeline` mode additionally moves copies to an egress stream. Pinning and device pools exist in every mode; only the pinned destination allocation location changes at the pooling stage.

Suppose production takes 4 ms, D2H 2 ms and one blocking consumer 8 ms per output. Serial execution costs 14 ms per output. A steady pipeline still cannot exceed its slowest effective stage: one consumer can finish only one output per 8 ms. Two workers could reduce that sink limit to about 4 ms if the sink permits concurrency, but two buffers, startup, contention and draining can constrain the result. These values are a reasoning exercise. They explain why adding an egress stream cannot cure an already-saturated sink.

Run the bounded output-pipeline experiment. Compare each neighboring mode, using the same worker/slot count and synthetic sink delay. Then set sink delay to zero to see whether the pipeline's benefits still outweigh its overhead for this small workload. This experiment measures transfer and consumer ownership, not allocator behavior in isolation.

## Practice

`labs/20_d2h_pipeline.py` runs a selected serial or staged device-to-host pipeline with optional workers, pooling, and asynchronous copies. It checks every output is correct and consumed once, then writes whole-loop timings and resource limits.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/20_d2h_pipeline/logs/%j.out" \
  --error="$PWD/results/20_d2h_pipeline/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/20_d2h_pipeline.py --mode serial --slots 2 --workers 2 --sink-ms 2
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/20_d2h_pipeline/logs/$LAB_JOB_ID.out"
cat "results/20_d2h_pipeline/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `every_output_matches_exact_reference` and `all_outputs_consumed_once`. `whole_loop_samples_ms` includes GPU production, D2H, exact CPU checking, synthetic sink delays, backpressure and final drain. Initial device buffers and preallocated host pools are outside the timer. Per-output pinned allocations in serial/workers occur inside it. `in_flight_destination_capacity_bytes` describes the slot capacity; it excludes allocator caching, transient reference tensors and other host objects, and is not a measured peak.

Use the annotations described below to explain where output handling waits. Correctness, complete consumption and the included operations and completion checks above must remain satisfied in every mode.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Whole loop / median (seconds) | `whole_loop.median_ms` | `s` |
| In flight destination capacity bytes | `in_flight_destination_capacity_bytes` | `bytes` |
| Synthetic sink (seconds) | `synthetic_sink_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 20_d2h_pipeline \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run from this course directory. Keep slots, workers, sink delay and input shape fixed when comparing neighboring modes. Private result files use independent IDs. Run at least three independent jobs per accepted comparison and alternate their order.

```bash
for mode in serial workers pooled nonblocking pipeline; do
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/20_d2h_pipeline/logs/%j.out" \
  --error="$PWD/results/20_d2h_pipeline/logs/%j.err" slurm/single_gpu.sbatch labs/20_d2h_pipeline.py --mode "$mode" --slots 2 --workers 2 --sink-ms 2
 done
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/20_d2h_pipeline/logs/%j.out" \
  --error="$PWD/results/20_d2h_pipeline/logs/%j.err" slurm/single_gpu.sbatch labs/20_d2h_pipeline.py --mode pipeline --sink-ms 0
```

Profile only a short diagnostic window after measuring unprofiled behavior. Reports are created under a private results subdirectory; do not publish raw traces.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/20_d2h_pipeline/logs/%j.out" \
  --error="$PWD/results/20_d2h_pipeline/logs/%j.err" slurm/nsys_single_gpu.sbatch labs/20_d2h_pipeline.py --mode pipeline --warmup 1 --iterations 2
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Inspect `produce_output`, `d2h_submit`, `host_consume`, `output_backpressure` and `output_drain`. Correlate annotations with device activity rather than interpreting their host lengths as copy durations. First determine whether workers remove host serialization, then whether pooling reduces allocation work, then whether separate streams overlap independent copies and kernels. Vary sink delay and worker count separately. A slow sink can remain the limit after transfer overlap succeeds.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/20_d2h_pipeline/logs/capture-%J-%t.out" \
  --error="results/20_d2h_pipeline/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/20_d2h_pipeline/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/20_d2h_pipeline.py --mode serial --slots 2 --workers 2 --sink-ms 2
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. Select `produce_output` and the GEMM-name filter `.*(gemm|nvjet).*` to skip weight initialization and the input-fill kernel at the start of each output operation. The filter matches the full demangled name, including template arguments when a library uses a generic name such as `Kernel2`. The launch-count limit applies after both filters. With the default warmup, the selected GEMM belongs to the first warmup pipeline call. Verify its kernel name and range in the report; these counters describe one diagnostic GEMM, not a measured whole-loop sample, copy overlap or CPU sink performance.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/20_d2h_pipeline/logs/capture-%J-%t.out" \
  --error="results/20_d2h_pipeline/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include produce_output/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/20_d2h_pipeline/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/20_d2h_pipeline.py --mode serial --slots 2 --workers 2 --sink-ms 2
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--mode`, `--slots`, `--workers`, `--sink-ms` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Stale values indicate premature host reads, device overwrites or destination reuse. Preserve event waits and do not replace them with a guessed sleep. A consumer exception must fail the result and drain executor activity through structured cleanup. Long backpressure with correct results means the bounded queue is working; inspect the slow consumer before expanding it. Threads overlap blocking I/O here; GIL-bound Python computation may require a separate process design with serialization and memory costs accounted for.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

The measured operation ends only when all required outputs have been consumed. Explain which event protects a host read and which future protects reuse. To apply the design to another workload, replace the synthetic delay with a safe real sink and retain exact output checks, bounded ownership and final draining. Do not claim a storage improvement from the supplied synthetic sink.
