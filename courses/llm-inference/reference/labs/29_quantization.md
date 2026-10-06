# Lab 29: Inspect quantized weight and KV storage mechanics

Quantization can reduce stored payload size while adding scale metadata, conversion work, and numerical error. This lab implements symmetric INT8 weight and KV representations and compares their reconstruction with BF16 references. You will learn why a smaller logical representation does not by itself prove lower resident memory, native INT8 execution, or a faster language-model service.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use one H100 in the mechanics environment. Read the scale and error walkthrough below before interpreting the numerical gates. Both BF16 and INT8 copies remain resident for the A/B checks, so the process is intentionally not memory-minimized.

H100 supports FP8 operations, but INT4/FP4 and engine-specific formats have distinct kernel and architecture boundaries. Blackwell-only NVFP4 must not be presented as an H100 path.

## Concepts and code path

Symmetric quantization divides a value by a positive scale, rounds it to an integer and clamps it to the supported range. Reconstruction multiplies the integer by that scale. With illustrative scale 0.10, value 0.26 becomes integer 3 and reconstructs as 0.30: compression has introduced error 0.04. This hand calculation uses a deliberately simple fixed scale; the lab derives each tensor's scale from its maximum absolute value and the integer limit 127. Weight-operation error can differ from weight-reconstruction error because matrix multiplication combines many reconstructed values.

The L2 norm is the square root of summed squared elements, treating a tensor as one long vector. Relative L2 error is `norm(observed - reference) / norm(reference)` for a nonzero reference norm. This is the lab's actual rule; it does not add epsilon to that denominator. A value below 0.02 limits aggregate relative tensor error, not every element's error and not language-model quality. The generated random references are expected to be nonzero. Testing zero-reference tensors is an extension that must first define an absolute-error or explicitly stabilized-denominator policy.

The program derives scales, quantizes values to INT8, reconstructs them, and compares the resulting weight operation and KV tensors with references. The weight path dequantizes before BF16 computation; it is not a native quantized GEMM kernel. Timing separates weight paths and KV dequantization, while byte reports describe logical payloads and scales.

Given a 64-GiB budget for weights, KV cache and workspaces, BF16 weights occupying 14 GiB leave 50 GiB for cache and workspaces. If quantized weights and their metadata occupy 8 GiB under the same memory budget, they leave 6 GiB more for those uses. Change only the weight representation and hold ISL/OSL plus quality set fixed. Expected observation: available cache capacity rises if workspace and other runtime requirements remain unchanged, but latency improves only if the selected H100 kernel consumes the format efficiently; fallback/dequantization evidence explains any regression.

Run Lab 29 to measure logical weight/KV storage, dequantization cost, and relative error. Treat supported engine kernels as a separate advanced profile until an exact image and artifact are qualified.

## Practice

`labs/29_quantization.py` compares BF16 weights and KV tensors with symmetric INT8 quantization/dequantization. It records logical storage, timings, and relative error and checks finite outputs and bounded error; it does not benchmark production INT8 engine kernels.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/29_quantization/logs/%j.out" \
  --error="$PWD/results/29_quantization/logs/%j.err" \
  slurm/29_quantization.sbatch --workload small
```

## Check your results

Each new job owns `results/29_quantization/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/29_quantization/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/29_quantization/logs/$LAB_JOB_ID.out"
cat "results/29_quantization/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require finite output and both weight/KV relative L2 errors below 0.02. Inspect logical byte accounting, `weight_timing`, `kv_dequantization_timing`, and `memory_scope`. These tensor-error gates are not a language-model quality evaluation.

For a separately qualified quantized-engine experiment, retain method, calibration/scale policy, kernel/backend, memory, TTFT, ITL, throughput, and quality evaluation. The supplied tensor mechanics establish logical storage, conversion costs and numerical error only.

A quantized profile is accepted only for models and shapes supported by the actual engine.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Weight timing / bf16 / median (seconds) | `weight_timing.bf16.median_ms` | `s` |
| Weight timing / int8 weight dequantize then matmul / median (seconds) | `weight_timing.int8_weight_dequantize_then_matmul.median_ms` | `s` |
| Kv dequantization timing / median (seconds) | `kv_dequantization_timing.median_ms` | `s` |
| Bf16 weight bytes | `bf16_weight_bytes` | `bytes` |
| Int8 weight and scale bytes | `int8_weight_and_scale_bytes` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 29_quantization --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 29_quantization \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the supplied small comparison before changing quantization granularity or scales. A larger shape is a separate numerical and conversion-cost experiment, not automatic evidence of engine memory savings.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/29_quantization/logs/%j.out" \
  --error="$PWD/results/29_quantization/logs/%j.err" slurm/29_quantization.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/29_quantization/logs/%j.out" \
  --error="$PWD/results/29_quantization/logs/%j.err" slurm/29_quantization.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Calculate the ideal payload reduction, then add scales and reconstructed buffers. Why can dequantize-then-compute be slower than the BF16 baseline? Which supported engine kernel would be necessary to test a true low-precision execution benefit?

Coarser scales save metadata and can increase error; finer scales do the reverse. Weight-only quantization can improve model fit while leaving KV limits unchanged. KV quantization helps long-lived concurrency but can add per-token overhead.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/29_quantization/logs/%j.out" \
  --error="$PWD/results/29_quantization/logs/%j.err" slurm/29_quantization.nsys.sbatch --workload small
```

The native Systems command is in `slurm/29_quantization.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/29_quantization/logs/%j.out" \
  --error="$PWD/results/29_quantization/logs/%j.err" slurm/29_quantization.ncu.sbatch --workload small
```

The native Compute command is in `slurm/29_quantization.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare BF16 with dequantize-then-compute at fixed shape. Independently include scale and reconstruction bytes before deciding whether this path saves usable memory; it does not benchmark a native low-bit GEMM.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Large reconstruction error can indicate outliers, scale granularity, or saturation. Do not relax the 0.02 threshold after seeing a failure. An OOM with both copies resident does not disprove the logical compression ratio.

Avoid declaring success from checkpoint size without proving runtime memory or quality.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Separate representation, kernel support, allocation, latency, and quality claims. A production extension requires a pinned supported engine/artifact and actual memory, timing, and task-quality measurements under fixed workload semantics.

Evaluate weights, KV, kernels, latency, capacity, and quality as one system.

Explain which workload benefits most from weight versus KV quantization.
