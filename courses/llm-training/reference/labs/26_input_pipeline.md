# Lab 26: Diagnose a slow training-data producer

A training accelerator can wait because its next batch is not ready, even when the model itself is efficient. This lab compares a serial producer with worker-prefetched loading using deterministic sample identities and a configurable producer delay. You will distinguish configured queue capacity, observed readiness waits, and genuine whole-loop throughput without treating a CPU wait proxy as a measured GPU idle interval.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and enough allocated CPUs for worker processes. The dataset is synthetic and deterministic. This lab exposes worker, prefetch, batch-count, and producer-delay options; it does not benchmark real storage.

A single H100 can expose CPU or storage tails quickly; two nodes may increase shared-filesystem pressure without proving a GPU issue.

## Concepts and code path

Samples contain deterministic IDs and token content; the collator adds producer-time metadata. DataLoader uses prefetch only with positive worker counts. The consumer transfers tokens, executes bounded GPU work and records readiness and step durations. SHA-256 digests fingerprint ordered sample IDs and token bytes: matching digests support unchanged content and order for that serialization, not data quality. Queue-depth samples are snapshots; actual consumer waits show whether preparation delays progress.

For the supplied comparison, keep two workers and change prefetch from two to four batches per worker. Configured queue capacity rises from four to eight batches, but the observed queue can still be empty and whole-loop throughput may not improve. Compare the ordered sample/content digests first, then readiness waits and tokens/s. A long host readiness wait suggests producer pressure; correlate it with the GPU timeline before claiming device idleness. Real storage, offline tokenization and persistent workers are separate extensions.

Run Lab 26 across worker and prefetch settings with a deterministic sample sequence.

## Practice

`labs/26_input_pipeline.py` compares a deterministic serial producer with worker-prefetched training batches. It checks identical sample order and content and writes batch-ready, transfer, consumer, and throughput measurements; batch wait is only a GPU-starvation proxy.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_input_pipeline/logs/%j.out" \
  --error="$PWD/results/26_input_pipeline/logs/%j.err" \
  slurm/26_input_pipeline.sbatch --workload small --workers 2 --prefetch 2
```

## Check your results

Each new job owns `results/26_input_pipeline/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/26_input_pipeline/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/26_input_pipeline/logs/$LAB_JOB_ID.out"
cat "results/26_input_pipeline/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require identical sample order/content, complete consumption, and positive timings. Compare `batch_ready_ms`, `consumer_step_ms`, `wall_seconds`, and tokens/s. The `gpu_idle_gap_proxy_ms` field is a host readiness proxy; use a timeline to establish actual GPU idleness.

Record batch-ready time, queue-depth samples, producer CPU-time summaries, H2D time, tokens/s and the sample/content digest. The wall timer starts after iterator creation and ends after consuming the batches, so it excludes iterator construction and teardown; readiness waits after construction remain inside the measured loop. Treat `gpu_idle_gap_proxy_ms` as a host wait and record actual GPU gaps only from a matching timeline.

The pipeline is improved only if the GPU receives equivalent batches sooner.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Serial slow producer / batch ready p50 (seconds) | `serial_slow_producer.batch_ready_p50_ms` | `s` |
| Worker prefetched / batch ready p50 (seconds) | `worker_prefetched.batch_ready_p50_ms` | `s` |
| Serial slow producer / tokens per second | `serial_slow_producer.tokens_per_second` | `tokens/s` |
| Worker prefetched / tokens per second | `worker_prefetched.tokens_per_second` | `tokens/s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 26_input_pipeline \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Change one producer setting at a time while keeping all data and consumer work fixed. Both commands include the serial reference and the selected worker-prefetched path.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_input_pipeline/logs/%j.out" \
  --error="$PWD/results/26_input_pipeline/logs/%j.err" slurm/26_input_pipeline.sbatch --workload small --workers 2 --prefetch 2
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_input_pipeline/logs/%j.out" \
  --error="$PWD/results/26_input_pipeline/logs/%j.err" slurm/26_input_pipeline.sbatch --workload small --workers 2 --prefetch 4
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Does a larger queue absorb producer variation or merely reserve more host memory? Compare configured capacity with observed queue depth. Explain why adding workers eventually stops helping if the consumer or another resource becomes limiting.

More workers and deeper queues consume RAM and can magnify I/O contention. Offline preprocessing improves steadiness but increases artifact management and reduces online flexibility.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_input_pipeline/logs/%j.out" \
  --error="$PWD/results/26_input_pipeline/logs/%j.err" slurm/26_input_pipeline.nsys.sbatch --workload small --workers 2 --prefetch 2
```

The native Systems command is in `slurm/26_input_pipeline.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/26_input_pipeline/logs/%j.out" \
  --error="$PWD/results/26_input_pipeline/logs/%j.err" slurm/26_input_pipeline.ncu.sbatch --workload small --workers 2 --prefetch 2
```

The native Compute command is in `slurm/26_input_pipeline.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--workers`, `--prefetch` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A digest mismatch means the workload changed and invalidates comparison. Worker failures can arise from multiprocessing or CPU limits. Do not suppress missing batches to make throughput look better.

If a worker-count change alters sample order or content in an extended dataset, inspect worker seeding and sampling. The supplied deterministic dataset must keep both unchanged.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Input optimization must preserve data completeness and order semantics. Add a real dataset and an actual training consumer as a new experiment, retaining the identity checks and measurement of the complete loop.

Verify order and content first, then tune the slowest producer stage.

State where packing belongs relative to tokenization, batching, and transfer.
