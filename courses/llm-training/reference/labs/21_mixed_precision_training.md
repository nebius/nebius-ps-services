# Lab 21: Validate precision changes across a full training update

A precision change affects more than forward logits: gradients and optimizer updates can also change. This lab compares FP32, BF16, and scaled FP16 tiny-transformer steps from identical initial state and data. You will use explicit numerical thresholds before comparing device time, end-to-end throughput, and memory, keeping numerical acceptance separate from warmed training trajectories.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/21_mixed_precision_training.json).

Use one H100 in the Training environment. The script runs complete precision variants and has no memory-only mode. The FP16 path uses dynamic gradient scaling; the BF16 path does not require the same scaling behavior by default.

Use the detected H100 memory capacity and preserve operational headroom; do not assume all H100 SKUs expose the same HBM. Full non-MIG allocation is the course contract.

Hopper supports FP8 Tensor Core paths and BF16/FP16/TF32. MXFP8 and NVFP4 examples that require Blackwell remain comparison-only; availability must be queried from the pinned Transformer Engine API.

## Concepts and code path

The program creates matched model/optimizer state and a fixed numerical-test batch. It compares pre-update loss, every collected parameter gradient, and parameter updates against FP32. Separate warmed runs then measure evolving training steps. Common helpers manage device checks and output; the tiny-model module owns architecture. Lab 22 uses a different model, so only within-lab state is matched.

An L2 norm measures overall magnitude as the square root of the sum of squared elements. Relative L2 error divides the norm of candidate-minus-reference by the reference norm. This lab sums over matching named gradient or update tensors as if their elements formed one vector; it does not concatenate them in memory. The denominator is at least `1e-12`, preserving the implementation's near-zero guard. The measure reports aggregate discrepancy, not the largest individual-element error. For reference `[3, 4]` and candidate `[3, 4.1]`, it is `0.1 / 5 = 0.02`. Scalar loss error instead uses the absolute loss difference divided by the larger of the absolute reference loss and `1e-12`.

Given 1 billion parameters with BF16 model weights and gradients plus two FP32 Adam moments and an FP32 master copy, those states alone approach 16 GB before activations and temporaries. Change to a sharded optimizer across two ranks. Expected observation: ideal persistent state per rank falls, but transient gathers, communication buffers, allocator reserve, and phase peaks remain in the measured ledger.

Predict which model, activation, gradient and optimizer allocations must coexist, then compare the memory ledger with measured allocated and reserved peaks. These counters describe different quantities; neither is interchangeable with the sum of tensor sizes.

For the optional FP8 extension in [Lab 22](22_transformer_engine_fp8.md), consider the following comparison. The supplied Lab 21 script does not execute FP8.

Given a BF16 baseline with finite gradients, run a warmed FP8 `te.autocast` region on matched inputs while retaining FP32 optimizer state. Change only the recipe and supported modules. Expected observation: selected GEMMs may use FP8 and activation bytes may fall, but loss/gradient/update tolerances, amax history, and kernel evidence—not the context manager alone—decide acceptance.

Run Lab 21 and, after qualifying Transformer Engine, optional Lab 22. Each lab creates matched state and data within its own comparison. Lab 21 uses a tiny transformer while Lab 22 uses a Transformer Engine linear layer; their results are not a cross-lab matched-model comparison. Record each lab's numerical thresholds and included operations independently.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Inspect the numerical-threshold options before running. Keep the defaults unless the experiment has a separately justified acceptance contract; do not loosen thresholds after seeing a failure.

```bash
umask 077
"$COURSE_PYTHON" labs/21_mixed_precision_training.py --help
python3 tools/submit_lab.py --lab 21_mixed_precision_training slurm/single_gpu.sbatch labs/21_mixed_precision_training.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 21_mixed_precision_training --job "$LAB_JOB_ID"
```

Compare matched pre-update losses, named gradients and parameter-update deltas against FP32 before interpreting separate warmed timing. Gradient and update error aggregate squared values across matching tensor names; the denominator is guarded at `1e-12`. Scalar loss uses absolute difference divided by guarded absolute reference loss. Missing gradients or non-finite state, norms or errors invalidate acceptance. Defaults allow loss relative error 0.05 and gradient/update relative L2 error 0.2. These whole-update gates apply to this recipe; they are not elementwise BF16 allclose or evidence of convergence.

Retain a dtype-by-state ledger, phase peaks, allocator statistics, and shape contract.

Optimize the state that owns the actual peak rather than applying a generic memory technique.

Record input, accumulation and output dtypes, recipe, warm-up, selected kernels, loss, gradient/update error, time, and memory.

Accept precision only when the intended fast path runs and the training signal remains within declared tolerance.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Modes / fp32 / warmed timing / median end to end step (seconds) | `modes.fp32.warmed_timing.median_end_to_end_step_ms` | `s` |
| Modes / bf16 / warmed timing / median end to end step (seconds) | `modes.bf16.warmed_timing.median_end_to_end_step_ms` | `s` |
| Modes / fp16 / warmed timing / median end to end step (seconds) | `modes.fp16.warmed_timing.median_end_to_end_step_ms` | `s` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 21_mixed_precision_training \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Compare device-region time with end-to-end step time and tokens/s. Explain why gradient scaling can prevent FP16 underflow yet still require unscaled-gradient and update checks. Distinguish a one-step equivalence test from long-run quality.

Recomputation saves activations but spends FLOPs; sharding saves persistent per-rank state but adds gathers and reductions; offload spends PCIe/network bandwidth; reduced precision adds metadata and numerical risk.

Lower precision can reduce activation storage, computation time or communication volume, but adds cast/scaling work and quality risk. One-step closeness checks numerical mechanics; longer-run task evaluation is still required for a training-quality claim.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 21_mixed_precision_training --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/21_mixed_precision_training.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first matrix kernel inside `mixed_precision_step`, excluding batch initialization and the separate numerical-equivalence step. It diagnoses the first FP32 warmup pass; it does not supply counters for the other precision modes or warmed throughput. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 21_mixed_precision_training '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=mixed_precision_step,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/single_gpu.sbatch labs/21_mixed_precision_training.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the supplied precision policies using both device and complete-step timing. Independently reject any policy failing its numerical threshold even if throughput improves.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Non-finite, skipped, or ineffective updates invalidate a candidate. Inspect gradient scale and error components before changing precision or input scale. An apparently faster mode with failed numerical gates is not an accepted optimization.

Adding parameter, gradient, optimizer, and activation maxima from different phases overestimates simultaneous live memory.

Avoid comparing precision modes from independently initialized models.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Precision choices require explicit error, state-update, and performance evidence. Extend to a meaningful held-out training task before claiming convergence parity, and qualify Transformer Engine separately before attempting FP8.

Build phase-aware lower and upper bounds and confirm them with device measurements.

Identify which memory terms DDP, FSDP2, checkpointing, and mixed precision change.

Clone initial state and input, warm recipes separately, then compare both numerical and performance evidence.

Explain storage, compute, accumulation, master-weight, and optimizer-state precision.
