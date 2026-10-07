# Lab 25: Explore input length and recurrent operator work

Long inputs and long output sequences stress different parts of an inference workload, but a small operator model must not be mislabeled as a language model. A recurrent update feeds the previous hidden state (the intermediate values retained between steps) into the next computation. Here `tanh`, the hyperbolic tangent, maps each real input smoothly between -1 and 1, providing a bounded nonlinear transformation after a matrix product. This lab projects a batch of input vectors and repeatedly updates that hidden state with matmul and tanh. It teaches workload-shape reasoning while explicitly reporting operator work, not generated tokens or service latency.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 in the mechanics environment. The four cases combine input lengths 128/2048 with 32/512 recurrent iterations. The `--concurrency` option controls simultaneous batch rows here, not concurrent HTTP requests.

Shape-dependent Tensor Core efficiency and HBM/KV pressure make H100 performance highly workload-specific. Test full non-MIG allocation and record whether the server shares the device.

## Concepts and code path

The program computes a dense projection over all input positions, selects the last projected state, then applies repeated `tanh(state @ weight)` updates. It times projection, one recurrent step, the recurrence sequence, and the joined projection-plus-recurrence workload independently. There is no attention, KV cache, vocabulary head, sampling, or token output; the recurrence count is not a real model's OSL schedule.

Given traffic weights of 60 percent at 128/128, 30 percent at 4,096/32, and 10 percent at 128/1,024, use these weights to construct the actual mixed request stream and measure completed output tokens divided by a common wall interval. Do not take a weighted arithmetic mean of isolated throughputs: batching and queueing couple requests, and even serial rate aggregation generally needs a different model. Change the long-prompt share during a scheduled batch window. Expected observation: TTFT and chunked-prefill policy may need a different qualified profile even when daily-average throughput appears unchanged.

The four input-length/recurrent-work cells execute projection and recurrent matmuls, not attention, cache growth, vocabulary sampling, or generated tokens. Their operator rates illustrate shape effects only; actual input/output-token and request-concurrency measurements require a qualified serving engine.

## Practice

`labs/25_workload_metrics.py` runs four synthetic input-projection and recurrent-matrix workloads, validates their counted operator work, and records workload measurements. It measures batch-row operations, not generated tokens or live service requests.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/25_workload_metrics/logs/%j.out" \
  --error="$PWD/results/25_workload_metrics/logs/%j.err" \
  slurm/25_workload_metrics.sbatch --workload small --concurrency 1
```

## Check your results

Each new job owns `results/25_workload_metrics/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/25_workload_metrics/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/25_workload_metrics/logs/$LAB_JOB_ID.out"
cat "results/25_workload_metrics/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require four executed cells, declared operator counts, and finite recurrence output. Inspect `projected_input_rows`, `recurrent_row_updates`, phase distributions, joined timing, and row-updates/s. The joined rate uses a directly measured joined interval, not a sum of phase medians. Finiteness is not an independent numerical reference check.

For a separately qualified live-engine workload experiment, record workload distribution, arrival process, completed/failed requests, tokens, latency percentiles, throughput, and memory. These request-level measurements are not outputs of the supplied operator model.

Engine comparisons are meaningful only for the same workload matrix and arrival model.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Workload cells / case / input projection / median (seconds) | `workload_cells.*.input_projection.median_ms` | `s` |
| Workload cells / case / one recurrent step / median (seconds) | `workload_cells.*.one_recurrent_step.median_ms` | `s` |
| Workload cells / case / joined projection and recurrence / median (seconds) | `workload_cells.*.joined_projection_and_recurrence.median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 25_workload_metrics --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 25_workload_metrics \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Compare batch-row counts while keeping the four length/work cells fixed. Begin with the smaller batch and retain all cells rather than selecting only a favorable point.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/25_workload_metrics/logs/%j.out" \
  --error="$PWD/results/25_workload_metrics/logs/%j.err" slurm/25_workload_metrics.sbatch --workload small --concurrency 1
sbatch --chdir="$PWD" \
  --output="$PWD/results/25_workload_metrics/logs/%j.out" \
  --error="$PWD/results/25_workload_metrics/logs/%j.err" slurm/25_workload_metrics.sbatch --workload small --concurrency 4
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which operation grows with input length and which with iteration count? Why can batching improve matrix efficiency while increasing latency per completed batch? Contrast these simplified costs with real attention and cache growth omitted here.

More cells improve representativeness but increase runtime and model-serving cost. Synthetic fixed-length requests isolate mechanisms; natural prompts capture tokenizer and stop behavior. Both are useful when labeled.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/25_workload_metrics/logs/%j.out" \
  --error="$PWD/results/25_workload_metrics/logs/%j.err" slurm/25_workload_metrics.nsys.sbatch --workload small --concurrency 1
```

The native Systems command is in `slurm/25_workload_metrics.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/25_workload_metrics/logs/%j.out" \
  --error="$PWD/results/25_workload_metrics/logs/%j.err" slurm/25_workload_metrics.ncu.sbatch --workload small --concurrency 1
```

The native Compute command is in `slurm/25_workload_metrics.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--concurrency` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `concurrency` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Do not interpret row-updates/s as output tokens/s or one recurrent-step duration as ITL. Non-finite states invalidate the cell. Memory exhaustion requires a smaller declared workload, not silently skipped cells.

Avoid benchmarking one convenient prompt and generalizing to every serving workload.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Synthetic operators are useful only with honest boundaries. Use Lab 09 for real prefill/continuation semantics and the live streaming/AIPerf workflows for TTFT, token-aware intervals, and output-token throughput.

Publish a representative distribution plus corner cases that isolate prefill, decode, and capacity.

Choose one cell that isolates each phase and one that stresses queueing.
