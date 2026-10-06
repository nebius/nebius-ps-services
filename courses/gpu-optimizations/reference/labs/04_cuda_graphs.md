# Lab 04: Replay a fixed-shape workload with a CUDA Graph

A CUDA Graph is a reusable plan of device operations and their dependencies. Capture records compatible work into the plan; replay executes it again, rather than returning cached output values. The plan can still contain several separate kernels: replay is not the same as fusion. Preparing this plan can reduce repeated CPU launch setup. This lab captures one fixed-shape workload using stable buffers and compares replay with eager execution. You will learn why replay is a scheduling optimization rather than an automatic solution for arbitrary inputs, shapes, or dynamic control flow.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 and the approved environment. Review stream ordering and tensor lifetime. The supplied experiment has one input shape and does not implement shape buckets, dynamic routing, or an eager fallback service.

Fast repeated H100 inference or training steps can become CPU-launch limited, making replay valuable. A graph does not improve the kernel instruction path, memory coalescing, or collective algorithm inside the captured sequence.

## Concepts and code path

The program warms and captures a fixed-shape matrix multiplication followed by SiLU. Replay uses the captured input and output storage. Rebinding a Python variable does not change those addresses. The supplied check compares replay with eager execution for the original input. Copying new values into the static input is the optional extension at the end of this guide; the baseline does not test changing inputs, graph updates or shape routing.

Given one fixed-shape step with 60 microseconds of kernels and 40 microseconds of launch overhead, graph replay can remove much of the 40. Change the service to eight shape buckets. Expected observation: warmed hits improve while first-use capture and graph-pool memory grow; an unseen shape must execute the declared fallback rather than silently pad without accounting.

## Practice

`labs/04_cuda_graphs.py` captures a fixed-shape inference computation in a CUDA Graph and compares replay with eager execution. It checks matching output and writes both timing distributions.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_cuda_graphs/logs/%j.out" \
  --error="$PWD/results/04_cuda_graphs/logs/%j.err" \
  slurm/04_cuda_graphs.sbatch --workload small
```

## Check your results

Each new job owns `results/04_cuda_graphs/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/04_cuda_graphs/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/04_cuda_graphs/logs/$LAB_JOB_ID.out"
cat "results/04_cuda_graphs/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `allclose` and `fixed_shape`. Compare the `eager` and `cuda_graph` distributions with the recorded shape. Do not infer capture startup amortization or dynamic-input correctness from this fixed-input gate.

For the supplied baseline, record capture success, eager/replay correctness, and repeated timing. For the extension, additionally retain changed-input reference checks, each supported shape, fallback counts, capture/startup cost, and graph-pool memory. Report only measurements actually collected.

Graphs trade flexibility for lower recurring dispatch overhead.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Eager / median (seconds) | `eager.median_ms` | `s` |
| Cuda graph / median (seconds) | `cuda_graph.median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 04_cuda_graphs --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 04_cuda_graphs \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the baseline without edits first. Each profile starts a separate process and capture; selecting a second profile is not replaying a new shape through the original graph.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_cuda_graphs/logs/%j.out" \
  --error="$PWD/results/04_cuda_graphs/logs/%j.err" slurm/04_cuda_graphs.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_cuda_graphs/logs/%j.out" \
  --error="$PWD/results/04_cuda_graphs/logs/%j.err" slurm/04_cuda_graphs.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Identify which buffers must remain alive across replay. Explain why replacing a Python variable with a new allocation does not update the captured pointer. Use a timeline to check whether launch gaps shrink.

Graph pools and per-bucket captures consume memory. Many buckets reduce fallback but increase warm-up and retained state. Replay improves steady state while potentially worsening startup, debuggability, and rare-shape behavior.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_cuda_graphs/logs/%j.out" \
  --error="$PWD/results/04_cuda_graphs/logs/%j.err" slurm/04_cuda_graphs.nsys.sbatch --workload small
```

The native Systems command is in `slurm/04_cuda_graphs.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `course_measure`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_cuda_graphs/logs/%j.out" \
  --error="$PWD/results/04_cuda_graphs/logs/%j.err" slurm/04_cuda_graphs.ncu.sbatch --workload small
```

The native Compute command is in `slurm/04_cuda_graphs.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare eager launches with CUDA graph replay at fixed buffers and shape. Independently inspect launch gaps, then decide whether saved launch time justifies graph memory and shape constraints.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A capture error may indicate unsupported operations or synchronization inside capture. An unchanged output after supplying new data may mean you updated the wrong buffer. Check storage and ordering before blaming numerical precision.

Avoid comparing replay against a cold eager path or silently reusing stale input storage.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Use the changed-input and shape-routing extension below to test which capture assumptions your workload can preserve.

A successful fixed-input replay does not prove that a changing workload is safe. The extension must update stable input storage, order copies before replay, validate new outputs, and route incompatible shapes explicitly.

List the shape, allocation, and control-flow assumptions in the captured region.

First run Lab 04's implemented fixed-shape eager-versus-graph baseline. It captures one input buffer and does not implement buckets or fallback routing. Extension: create a second input of the same shape, copy it into the captured static buffer before replay on the correctly ordered stream, and compare the replay output with an eager reference for that new input. Then add an explicit shape check: an unsupported shape runs eagerly rather than reusing an incompatible graph. Create separate captures only for deliberately supported buckets.
