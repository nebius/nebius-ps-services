# Lab 02: Remove unnecessary per-step host synchronization

Fetching a CUDA scalar into Python can force the host to wait for the GPU. In a repeated loop, those waits can prevent efficient submission of later work. This lab compares retrieving each scalar immediately with accumulating on the device and retrieving once, while checking that the final sum still represents the same computation.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and understand Lab 01's synchronized wall boundary. The experiment uses generated tensors, not a training dataset. `--steps` controls how often the scalar-producing work is repeated.

## Concepts and code path

A scalar is one number. Calling `.item()` converts a one-element tensor into a Python number. When the value is on the GPU, the host must wait until that value is available before returning it to Python. For example, logging every step can introduce repeated waits even when the GPU computation itself is unchanged. Keeping contributions in a device tensor postpones that observation, but cannot replace an immediate host-side decision that actually needs the value.

One path calls `.item()` at each step and accumulates on the host. The other keeps scalar contributions on the GPU and delays the host read until the end. Both must produce equivalent sums. The optimization is legal only if the application does not need each scalar immediately to decide the next operation.

## Practice

`labs/02_sync_trap.py` compares extracting a scalar with `.item()` every step against delaying extraction until a final reduction. It checks equivalent sums and writes completed-loop timings.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_sync_trap/logs/%j.out" \
  --error="$PWD/results/02_sync_trap/logs/%j.err" \
  slurm/02_sync_trap.sbatch --workload small
```

## Check your results

Each new job owns `results/02_sync_trap/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/02_sync_trap/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/02_sync_trap/logs/$LAB_JOB_ID.out"
cat "results/02_sync_trap/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `equivalent_sum`, then compare `per_step_item_median_ms` with `delayed_item_median_ms`. The result is a whole-loop comparison, not the isolated cost of a single `.item()` call.

Both sums must be finite. Infinity and NaN (not a number) invalidate the run before a successful result is written; a relative tolerance cannot establish equivalence for those values.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Per step item median (seconds) | `per_step_item_median_ms` | `s` |
| Delayed item median (seconds) | `delayed_item_median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 02_sync_trap \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Start with the supplied loop, then vary only the step count. Keep the tensor profile and environment unchanged when investigating how repeated synchronization affects total duration.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_sync_trap/logs/%j.out" \
  --error="$PWD/results/02_sync_trap/logs/%j.err" slurm/02_sync_trap.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_sync_trap/logs/%j.out" \
  --error="$PWD/results/02_sync_trap/logs/%j.err" slurm/02_sync_trap.sbatch --workload small --steps 100
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Locate each host wait in the source's two control flows. Would delaying logging preserve your application's semantics? Would delaying a convergence decision? Use a timeline to distinguish launch gaps from time spent executing useful device work.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_sync_trap/logs/%j.out" \
  --error="$PWD/results/02_sync_trap/logs/%j.err" slurm/02_sync_trap.nsys.sbatch --workload small
```

The native Systems command is in `slurm/02_sync_trap.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `lab_workload`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/02_sync_trap/logs/%j.out" \
  --error="$PWD/results/02_sync_trap/logs/%j.err" slurm/02_sync_trap.ncu.sbatch --workload small
```

The native Compute command is in `slurm/02_sync_trap.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--steps` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `steps` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A sum mismatch can result from changed work or accumulation order and precision. Investigate it before interpreting timing. If no improvement appears, determine whether each step is already compute-heavy enough to hide submission overhead.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Batching observations can improve execution without changing the mathematical workload, but it changes when information becomes available. Apply this idea to logging only after defining how much observation delay the application can tolerate.
