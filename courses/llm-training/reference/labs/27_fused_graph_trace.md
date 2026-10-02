# Lab 27: Separate compiled expressions from captured training steps

Compilation and CUDA Graphs optimize different parts of execution and should not be treated as one indistinguishable feature. This lab checks a compiled expression separately from a captured fixed-shape training update. You will compare the eager and compiled expressions, then compare eager and captured updates, and identify exactly which path the reported profile describes.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 with a qualified compiler backend and CUDA Graph support. Review static buffer lifetime, gradients, and optimizer updates. The source requires full-graph expression compilation; it does not demonstrate successful graph breaks or fallback routing.

H100 can make short surrounding kernels and launch gaps prominent beside fast GEMMs. Fused attention or MLP paths must be verified for the actual dtype and shape.

## Concepts and code path

The script validates an expression compiled with `fullgraph=True` and records its first call. Separately it builds matched training states, captures zero-grad, forward, backward, and SGD update using fixed storage, and verifies the next update. It times eager and captured training steps. The profiler summary covers graph replay, not a complete comparative eager/compiled training trace.

The supplied expression computes a biased matrix product, GELU and mean-squared loss. Its eager and full-graph compiled outputs must agree, and the first compiled call includes startup cost. Separately, the script captures an eager zero-grad/forward/backward/SGD update and checks it against an eager update from matching state. The captured training step is not the compiled expression. Compare the two training-step distributions only after gradient and update checks pass; a faster replay is an observation to establish, not an assumed result.

## Practice

`labs/27_fused_graph_trace.py` checks a compiled expression and compares eager training with a fixed-shape CUDA Graph containing forward, backward, and SGD update. It writes compilation cost, step timing, and optional internal profiler dispatch evidence.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_fused_graph_trace/logs/%j.out" \
  --error="$PWD/results/27_fused_graph_trace/logs/%j.err" \
  slurm/27_fused_graph_trace.sbatch --workload small
```

## Check your results

Each new job owns `results/27_fused_graph_trace/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/27_fused_graph_trace/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/27_fused_graph_trace/logs/$LAB_JOB_ID.out"
cat "results/27_fused_graph_trace/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `compiled_expression_close` and `captured_next_update_close`. Inspect `compiled_first_call_ms`, `cuda_graph_scope`, eager/graph training-step distributions, and `profile_dispatch_keys`. A replay profile cannot establish the number of fused groups in an unprofiled compiled path.

The update gate requires present, finite gradients and compares them after
normalizing each parameter tensor by its reference gradient's maximum magnitude,
using `rtol=1e-2, atol=1e-2`. It independently reconstructs each plain-SGD update
from the initial parameters and that path's gradients, then requires an exact
match and at least one changed parameter. Whole-parameter tolerances alone can
hide an omitted update when the learning rate is small.

Retain compiled first-call time separately from eager/captured training-step distributions, the declared capture scope, numerical checks and replay profiler dispatch keys. Separate traces and hardware counters are required for comparative kernel counts or HBM traffic; these are not supplied compiled-versus-eager training measurements.

Keep fusion or capture only when the full training step remains correct and faster after warm-up.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Eager training step / median (seconds) | `eager_training_step.median_ms` | `s` |
| Cuda graph training step / median (seconds) | `cuda_graph_training_step.median_ms` | `s` |
| Compiled first call (seconds) | `compiled_first_call_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 27_fused_graph_trace \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the supplied separated checks before instrumenting additional paths. Keep compile startup and steady-state training results distinct in your worksheet; they have different scopes.

```bash
"$COURSE_PYTHON" labs/27_fused_graph_trace.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_fused_graph_trace/logs/%j.out" \
  --error="$PWD/results/27_fused_graph_trace/logs/%j.err" slurm/27_fused_graph_trace.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which state changes on each captured update and which storage addresses remain stable? Explain why expression equivalence is not enough to prove optimizer-update equivalence. Identify the profile needed to support each proposed fusion or launch claim.

Fusion can increase register pressure and reduce reuse. Compilation and graph capture improve warmed steps but increase startup, memory, specialization, and debugging cost. A graph-friendly fixed shape may increase padding.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_fused_graph_trace/logs/%j.out" \
  --error="$PWD/results/27_fused_graph_trace/logs/%j.err" slurm/27_fused_graph_trace.nsys.sbatch --workload small --external-only
```

The native Systems command is in `slurm/27_fused_graph_trace.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/27_fused_graph_trace/logs/%j.out" \
  --error="$PWD/results/27_fused_graph_trace/logs/%j.err" slurm/27_fused_graph_trace.ncu.sbatch --workload small --external-only
```

The native Compute command is in `slurm/27_fused_graph_trace.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Check eager versus compiled expression outputs, then compare eager versus captured complete updates from matching state. Keep these two comparisons distinct. Independently inspect optimizer state and fixed addresses before choosing graph replay.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Full-graph compilation failure is a failed candidate, not an automatic eager fallback. Capture errors or mismatched updates require checking gradient buffers, ordering, and optimizer state before timing.

Avoid timing compilation as steady state or capturing a buffer whose contents are not refreshed.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Keep mechanism, correctness, and measurement scopes aligned. Extension: collect separate eager and compiled traces and compiler diagnostics; add explicit shape validation before attempting buckets or fallback behavior.

Validate gradients and updates, and keep startup and warmed execution separate. This fixed-shape, full-graph experiment has no fallback-frequency measurement.

Name one training operation that can make capture unsafe.

A full-graph compilation failure is a rejected expression path, not evidence of a successful graph break or fallback.
