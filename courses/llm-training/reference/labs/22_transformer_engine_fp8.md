# Lab 22: Qualify a warmed Transformer Engine FP8 recipe

FP8 execution relies on scaling and quantization state, not simply changing a tensor dtype. This optional lab compares BF16 and FP8 forward/backward behavior for a Transformer Engine linear layer using a delayed-scaling recipe. You will inspect numerical samples before and after timing so a favorable warmed result cannot conceal unstable recipe behavior.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/22_transformer_engine_fp8.json).

Qualify an exact compatible Transformer Engine build on the assigned supported GPU before running. This optional dependency is not a core-course completion gate. Its `te.Linear` workload differs from Lab 21's tiny transformer, so their timings are not a matched-model comparison.

### Qualify the optional runtime

Use an isolated course environment containing the selected PyTorch build. Choose an exact compatible Transformer Engine version and prepare its CUDA development headers using [NVIDIA's installation guide](https://docs.nvidia.com/deeplearning/transformer-engine/installation.html). Build the PyTorch extension against that environment:

```bash
"$COURSE_PYTHON" -m pip install --no-build-isolation \
  "transformer_engine[pytorch]==${COURSE_TE_VERSION:?qualified Transformer Engine version}"
```

A successful import does not exercise FP8 runtime compilation. Check the CUDA headers and NVRTC library actually selected, then run the unchanged small-profile command below on an allocated GPU before proceeding to large. A system CUDA installation can be selected even when PyTorch uses libraries from the Python environment.

If a compiler/header mismatch is diagnosed, use the selected release's supported library-discovery controls. For example, [Transformer Engine 2.19's loader](https://github.com/NVIDIA/TransformerEngine/blob/v2.19/transformer_engine/common/__init__.py) supports `NVRTC_HOME` for an existing matching CUDA library root. Scope that setting to this optional lab and verify the small FP8 numerical gate again. Preserve failed evidence and the declared workload and thresholds.

## Concepts and code path

The implementation uses `te.autocast` with a delayed-scaling recipe and amax history. It establishes matched linear-layer comparisons, warms scaling state, captures output/gradient error samples, and measures repeated execution. Memory accounting distinguishes steady-state allocation from incremental peak. Quantized representations and scaling metadata contribute to the actual memory footprint.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Inspect the output/gradient threshold options and run the small profile only after environment qualification. Record the exact recipe and installed version with the result.

```bash
umask 077
"$COURSE_PYTHON" labs/22_transformer_engine_fp8.py --help
python3 tools/submit_lab.py --lab 22_transformer_engine_fp8 slurm/single_gpu.sbatch labs/22_transformer_engine_fp8.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 22_transformer_engine_fp8 --job "$LAB_JOB_ID"
```

Require finite outputs, gradients, and errors for every numerical sample. Default maximum relative L2 errors are 0.2 for outputs and 0.3 for gradients. Check the warmed validation state before and after timing, not only the best sample.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Bf16 median device region (seconds) | `bf16_median_device_region_ms` | `s` |
| Fp8 median device region (seconds) | `fp8_median_device_region_ms` | `s` |
| Max output relative l2 | `max_output_relative_l2` | `none` |
| Max gradient relative l2 | `max_gradient_relative_l2` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 22_transformer_engine_fp8 \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Explain what an amax history estimates and why early calls can differ from warmed calls. Compare numerical stability, timing, and incremental memory. Why does a short linear-layer experiment not establish transformer training quality?

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 22_transformer_engine_fp8 --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/22_transformer_engine_fp8.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first matrix kernel inside `bf16_step`, excluding layer and input initialization. It diagnoses the first BF16 warmup pass; use Systems and the warmed numerical checks for FP8 behavior and do not interpret it as an FP8 counter report. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 22_transformer_engine_fp8 '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=bf16_step,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/single_gpu.sbatch labs/22_transformer_engine_fp8.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the BF16 reference with Transformer Engine FP8 under the declared scaling recipe and numerical gate. Independently use measured memory, timing and error to decide whether FP8 is acceptable for this shape; precision changes are an explicit numerical trade-off.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing FP8 support, incompatible packages, or recipe failures leave the extension pending. Do not substitute deprecated autocast examples or silently run BF16 while labeling it FP8. Preserve failed numerical samples.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

An FP8 claim belongs to an exact recipe, workload, and validated environment. A further experiment should introduce changing activation scales and a real training objective while keeping explicit output, gradient, and quality gates.
