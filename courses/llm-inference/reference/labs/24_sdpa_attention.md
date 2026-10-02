# Lab 24: Compare materialized attention with SDPA dispatch

Attention can either materialize a large score matrix or use a more memory-efficient implementation that avoids keeping the entire matrix. This lab compares an explicit reference with PyTorch scaled-dot-product attention for prefill-like and decode-like shapes. You will preserve masks and numerical meaning while inspecting backend dispatch, peak memory, and phase-specific timing.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 in the mechanics environment. Review query length, KV length, head dimension, and causal masking. A decode-shaped query with a longer KV history requires the correct intended mask semantics.

Hopper-aware fused attention paths can use Tensor Cores and specialized data movement, but support is version- and shape-specific. Verify on the pinned PyTorch or engine build.

## Concepts and code path

The source generates Q/K/V tensors and evaluates materialized attention versus SDPA on corresponding inputs. It separately measures warmed timing, incremental peak memory, and profiler dispatch keys. Prefill uses a long query; decode-like work uses a short query with retained history. No complete model, tokenizer, or serving endpoint is involved.

Given `S=4096` with 32 heads, a materialized score tensor has hundreds of millions of elements per batch. Change to a supported fused SDPA backend at identical inputs and mask. Expected observation: intermediate memory drops and kernel structure changes; output tolerance, selected backend, and both prefill and decode timing determine acceptance.

Run Lab 24 for prefill-like and decode-like shapes and record the selected backend.

## Practice

`labs/24_sdpa_attention.py` compares explicit attention and PyTorch scaled dot-product attention for prefill and decode shapes. It checks BF16 numerical agreement and records backend cases, timings, and peak allocation.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_sdpa_attention/logs/%j.out" \
  --error="$PWD/results/24_sdpa_attention/logs/%j.err" \
  slurm/24_sdpa_attention.sbatch --workload small
```

## Check your results

Each new job owns `results/24_sdpa_attention/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/24_sdpa_attention/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/24_sdpa_attention/logs/$LAB_JOB_ID.out"
cat "results/24_sdpa_attention/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `all_cases_close` at BF16 `rtol=1e-2, atol=1e-2`. Inspect each case's timing, incremental peak bytes, dispatch keys, and maximum error. The observed backend belongs to the actual shape, dtype, mask, and environment.

Retain shapes, mask, dtype, backend, kernel names, peak memory, time, and numerical error.

One attention backend is not universally best across phases and shapes.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cases / case / timing / materialized / median (seconds) | `cases.*.timing.materialized.median_ms` | `s` |
| Cases / case / timing / sdpa / median (seconds) | `cases.*.timing.sdpa.median_ms` | `s` |
| Cases / case / incremental peak bytes / materialized | `cases.*.incremental_peak_bytes.materialized` | `bytes` |
| Cases / case / incremental peak bytes / sdpa | `cases.*.incremental_peak_bytes.sdpa` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 24_sdpa_attention \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run both phase shapes together and retain their query/KV lengths and causal flags. The larger profile is a new memory/shape point, not a service workload.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_sdpa_attention/logs/%j.out" \
  --error="$PWD/results/24_sdpa_attention/logs/%j.err" slurm/24_sdpa_attention.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_sdpa_attention/logs/%j.out" \
  --error="$PWD/results/24_sdpa_attention/logs/%j.err" slurm/24_sdpa_attention.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Estimate the size of the explicit score matrix and compare it with observed incremental memory. Why might avoiding that matrix matter more for prefill than for a one-query decode step? Explain why backend names require profiler evidence.

Fused kernels save traffic but may require padding, impose mask constraints, or use more shared memory/registers. A prefill winner may not be a decode winner. Custom attention belongs only after maintained backends are exhausted.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_sdpa_attention/logs/%j.out" \
  --error="$PWD/results/24_sdpa_attention/logs/%j.err" slurm/24_sdpa_attention.nsys.sbatch --workload small --external-only
```

The native Systems command is in `slurm/24_sdpa_attention.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/24_sdpa_attention/logs/%j.out" \
  --error="$PWD/results/24_sdpa_attention/logs/%j.err" slurm/24_sdpa_attention.ncu.sbatch --workload small --external-only
```

The native Compute command is in `slurm/24_sdpa_attention.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare explicit attention and fused SDPA separately for prefill and decode. Independently calculate score-matrix memory and use the kernel trace to verify the actual backend.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A mask mismatch changes the mathematical problem and invalidates timing comparison. If the expected backend is unavailable, record actual dispatch or the failure rather than claiming a fused path from the API name alone.

Avoid timing a fallback while assuming a named fused backend executed.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Attention optimization is phase- and shape-dependent. Use Lab 32 for an independently repeated attention capstone; only a separate live-serving experiment can establish TTFT, inter-token latency, or request throughput effects.

Verify dispatch and evaluate each representative phase separately.

State why KV-cache layout matters more during decode.
