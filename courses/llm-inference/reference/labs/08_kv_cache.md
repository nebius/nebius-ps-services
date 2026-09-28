# Lab 08: Compare cached attention with full recomputation

Autoregressive generation repeatedly needs keys and values for earlier positions. A KV cache preserves those tensors so each new step can reuse them instead of recomputing the entire prefix. This lab checks cached and recomputed attention results on a small local model, helping you connect saved arithmetic to the cache memory that remains resident.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/08_kv_cache.json).

Use one H100 in the mechanics environment. No external model download is required. Review causal attention and the distinction between query heads and stored key/value heads.

## Concepts and code path

The program constructs a small attention workload, forms prompt keys/values, and validates the cached prompt result against full attention. For continuation, it updates the cache and compares cached computation with recomputation over the corresponding prefix. Repeated timings contrast both strategies. The implementation uses a fixed MHA layout; it is not a paged engine allocator or an MHA/GQA/MQA sweep.

Construction, correctness checks, warm-up and both timed paths run inside
`torch.inference_mode()`. No backward graph is retained with the prompt cache
or the generated continuation. This keeps the experiment's computation and
memory behavior within fixed-parameter inference.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the supplied paired mechanics first. The larger profile changes prompt/generation work and should remain a separately declared point in your cache-memory and timing analysis.

```bash
umask 077
python3 tools/submit_lab.py --lab 08_kv_cache slurm/single_gpu.sbatch labs/08_kv_cache.py --profile small
python3 tools/submit_lab.py --lab 08_kv_cache slurm/single_gpu.sbatch labs/08_kv_cache.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 08_kv_cache --job "$LAB_JOB_ID"
```

Require prompt and continuation agreement with the full-attention reference. Inspect `prompt_max_absolute_error`, `decode_max_absolute_error`, `key_value_cache_mib`, and cached/recompute distributions. Cache bytes here describe the implemented tensors, not an engine's complete reservation.

Both maximum absolute errors must be finite and at most `0.02`. NaN or infinity
in either compared result rejects the experiment before timing or publication;
a non-finite comparison is never evidence of agreement.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Recompute decode / median (seconds) | `recompute_decode.median_ms` | `s` |
| Cached decode / median (seconds) | `cached_decode.median_ms` | `s` |
| Key value cache mib | `key_value_cache_mib` | `bytes` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 08_kv_cache \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Identify which keys/values are newly computed at a continuation step and which are reused. Explain why longer histories increase attention reads even when the earlier projections are cached.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 08_kv_cache --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/08_kv_cache.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 08_kv_cache --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch labs/08_kv_cache.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare recomputation with cached decode for identical prompt and generated tokens. Independently locate cache growth copies in Systems and decide whether a preallocated cache merits a separately validated implementation experiment.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A mismatch appearing only after continuation suggests cache position, concatenation, or mask alignment. Verify these before changing tolerances. Memory estimates must include both K and V and the actual stored dtype.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Caching exchanges repeated computation for persistent state and growing reads. Next, use Lab 26 for ideal head-layout capacity calculations and Lab 27 for logical-to-physical page ownership; neither substitutes for real-engine allocation measurements.
