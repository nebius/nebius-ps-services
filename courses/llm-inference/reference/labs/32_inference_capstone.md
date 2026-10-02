# Lab 32: Build a causal attention optimization report

This capstone deliberately reuses Lab 24's explicit-attention versus SDPA comparison with the same BF16 tensors and mathematical mask. Lab 24 establishes backend and numerical behavior; the new competency here is making a defensible decision from independent trials, reversed variant order and an aggregate result that passes evidence validation. You will verify output equivalence and collect independent timing/memory trials before explaining the observed mechanism. The required deliverable is an attention microbenchmark report; online latency and throughput require a separate qualified serving campaign with its own workload contract.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and complete Lab 24. Each invocation is one fresh-process trial; the campaign launcher runs three with alternating variant order. Keep raw results and any profiler artifacts private.

No latency, throughput, engine-activation, or scaling claim is complete until measured on the declared H100 nodes with the exact pinned runtime. CPU simulations check their modeled calculations; separate client tests check protocol handling. Neither establishes live engine or service performance.

## Concepts and code path

The lab builds corresponding baseline and SDPA callables, compares outputs, warms each path, and records CUDA-event distributions and incremental peak allocation. It emits a provisional observation with the variant order. The source's workload labels describe the bounded attention shape; no HTTP server, queue, tokenizer, or autoregressive model loop is launched.

Given a mixed workload whose baseline reaches 12,000 tokens/s but violates p95 ITL during long prefills, enable chunking and observe 11,500 tokens/s with ITL inside the SLO and unchanged quality. Change to a short-prompt-only workload where chunking adds overhead. Expected observation: keep a workload-specific profile rather than a universal setting; pending live campaigns remain explicitly pending.

Required mechanics deliverable: submit :

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/32_inference_capstone/logs/%j.out" \
  --error="$PWD/results/32_inference_capstone/logs/%j.err" slurm/capstone_three_trials.sbatch --profile small
```

 for three fresh-process Lab 32 attention comparisons with alternating variant order, correctness, memory, repeated CUDA-event timing, and a profiler-backed explanation. Lab 32 does not launch a service or measure TTFT/ITL. Conditional serving deliverable: after engine/environment qualification, use slurm/vllm_chunked_prefill_ab.sbatch for the supported chunking A/B campaign, following its profile and benchmark-client instructions. Run baseline/candidate campaigns at declared loads and keep at least three independent trials per comparison. Only the live campaign can support service-latency, throughput, failure, or quality conclusions.

## Practice

`labs/32_inference_capstone.py` runs one fresh-process explicit-attention versus SDPA trial, checking equivalent BF16 outputs and recording timing, incremental memory, and variant order. The campaign launcher combines three independent trials into a scoped decision.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/32_inference_capstone/logs/%j.out" \
  --error="$PWD/results/32_inference_capstone/logs/%j.err" \
  slurm/capstone_three_trials.sbatch \
  --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Every input to the capstone aggregator must contain explicit
`experiment.instrumented: false` provenance. Profiled inputs, missing or
malformed provenance, and records declaring
`measurements.acceptance_timing: false` are rejected before an aggregate is
written. Rerun affected trials without profiling; do not edit diagnostic
records to make them appear clean.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/32_inference_capstone/logs/$LAB_JOB_ID.out"
cat "results/32_inference_capstone/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require output allclose at BF16 `rtol=1e-2, atol=1e-2` in every trial. Inspect maximum error, variant order, distributions, incremental peaks, and provisional/publication status. No single trial can establish a repeatable campaign result.

The aggregator requires complete, matching profile, H100/software, workload and
measurement-option fields. Every timing and the derived ratio must be finite
and positive; missing fields, boolean timings and non-finite values cannot
produce a keep decision.

Keep separate records. Mechanics: input shape/dtype/mask, reference error, variant order, raw timing samples, memory, selected kernels, trace, and keep/reject decision. Serving: immutable model/engine configuration, ISL/OSL and load, sampling/stops, output/quality checks, TTFT/ITL or documented proxies, completion latency, throughput, failures, queue/cache metrics, and independent trials. Mark serving evidence pending if no live engine ran.

Publish only claims demonstrated by the declared model, engine, H100 nodes, and workload.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Timing / materialized / median (seconds) | `timing.materialized.median_ms` | `s` |
| Timing / sdpa / median (seconds) | `timing.sdpa.median_ms` | `s` |
| Incremental peak bytes / materialized | `incremental_peak_bytes.materialized` | `bytes` |
| Incremental peak bytes / sdpa | `incremental_peak_bytes.sdpa` | `bytes` |
| Max abs error | `max_abs_error` | `none` |

Complete each three-trial group for acceptance. For repeated publication, run
the complete launcher twice in the same profile, retaining all six clean child
records and both validated aggregates. Pair corresponding children with the
same seed and variant order: 17 with 17, 18 with 18, and 19 with 19. Publish each
pair separately and review its generation before selecting the next pair.
Each child already contains the internal baseline/candidate comparison; the
dashboard pair does not replace either three-trial aggregate, and the two
groups must not be combined into one aggregate. Select only successful,
equivalent, unprofiled originals. On the login node, set the paths to the printed
result files and review the current generation (use `0` for the first selection):

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 32_inference_capstone \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Use the campaign launcher for the three-process comparison. Profile the same attention workload separately if needed to explain dispatch or memory behavior; instrumented duration is not acceptance timing.

```bash
"$COURSE_PYTHON" labs/32_inference_capstone.py --help
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/32_inference_capstone/logs/%j.out" \
  --error="$PWD/results/32_inference_capstone/logs/%j.err" slurm/capstone_three_trials.sbatch --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Does the measured memory difference match the materialized score-matrix explanation? Does the timing conclusion survive order reversal? Explain how a large attention-kernel improvement could have a smaller end-to-end service impact.

The best aggregate-throughput setting may not maximize SLO goodput. More KV reservation can reduce workspaces; chunking can trade prompt throughput for decode latency; quantization can trade quality or kernel support for fit.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/32_inference_capstone/logs/capture-%J-%t.out" \
  --error="results/32_inference_capstone/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/32_inference_capstone/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/32_inference_capstone.py --profile small --variant-order baseline-first
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/32_inference_capstone/logs/capture-%J-%t.out" \
  --error="results/32_inference_capstone/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/32_inference_capstone/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/32_inference_capstone.py --profile small --variant-order baseline-first
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare materialized attention with SDPA across all three trials. Independently reconcile memory and timing evidence and estimate the service-level gain from its hotspot fraction.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Mask or numerical disagreement rejects the candidate. Missing trials, changed shapes, or unsupported backends prevent a comparable aggregate. Preserve unfavorable observations instead of selecting only the fastest candidate sample.

Avoid tuning to one benchmark point while ignoring overload, failure rate, or quality.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Deliver a bounded mechanism-backed attention report. For service claims, separately qualify and run the chunked-prefill/AIPerf campaign with fixed arrivals, ISL/OSL, quality gates, and at least three independent engine trials.

Report the operating envelope and reject any candidate that violates correctness or service constraints.

State the strongest supported claim and one unsupported generalization.
