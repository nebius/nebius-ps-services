# Lab 09: Follow real-model prefill and continuation

The first generated token normally comes from the final-position logits produced by prefill. Subsequent tokens require continuation passes that consume the previously selected token. This lab runs that schedule explicitly with a pinned Hugging Face model, letting you inspect input length, attention-mask growth, cache positions, and the difference between generated-token count and decode-pass count.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/09_hf_prefill_decode.json).

Qualify the mechanics environment and the approved immutable model/tokenizer artifact. Use one H100 and keep generated data private. Review artifact auditing in Lab 16 before choosing a different model.

H100 Tensor Cores can efficiently process large prefill matrices; single-token decode often uses narrower matrices and repeatedly streams weights/KV, so the same backend need not be optimal for both phases.

## Concepts and code path

The implementation supplies a `DynamicCache`, updates attention masks and cache positions, and verifies cache length against inputs actually forwarded.

The script tokenizes the prompt, performs a prefill pass with cache creation, and selects the first token from its logits. Each later pass supplies the last selected token and advances mask/cache state. For N requested new tokens, the schedule uses one prefill and N−1 continuation passes. Checks enforce that schedule rather than merely accepting nonempty text.

Given a 4,096-token prompt and 32-token output, most matrix-rich work occurs before the first token. Change to a 128-token prompt and 1,024-token output. Expected observation: prefill shrinks, the sequential decode loop dominates completion time and KV lifetime, and transport chunks must be reconciled with server token timestamps before calling their gaps ITL.

Run Lab 09 and annotate phase boundaries, shapes, synchronizations, and output tokens.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Use one-token and multi-token cases to expose the boundary. The one-token request should not require a separate decode pass after prefill.

```bash
umask 077
python3 tools/submit_lab.py --lab 09_hf_prefill_decode slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile small --new-tokens 1
python3 tools/submit_lab.py --lab 09_hf_prefill_decode slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile small --new-tokens 8
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 09_hf_prefill_decode --job "$LAB_JOB_ID"
```

Require the requested token count and advancing mask, cache position, and cache length. Inspect `decode_steps_per_request`, `prefill_timing`, and `decode_timing`; the latter is absent when no continuation pass is needed. These device-phase timings are not client-observed TTFT.

Lab 09 records prompt length, a fixed output-token budget, generated-token counts, decode-forward counts, output shape, and prefill/decode forward-time summaries. It deliberately runs to that budget rather than stopping on EOS, and does not report end-to-end generation time or token arrival gaps. Investigate stopping policy and client-boundary observations separately with the serving/streaming labs; use token-aware engine measurements when reporting ITL or TPOT.

Optimize the phase that dominates the target workload, not an average request that hides ISL and OSL.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Prefill timing / median (seconds) | `prefill_timing.median_ms` | `s` |
| Decode timing / median (seconds) | `decode_timing.median_ms` | `s` |
| Prompt tokens | `prompt_tokens` | `none` |
| Decode steps per request | `decode_steps_per_request` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 09_hf_prefill_decode \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Draw which token is input to each pass and which token is sampled from its output. Why is the last generated token not necessarily already represented in the returned cache when generation stops?

Larger scheduling batches improve decode throughput but add queueing and per-user delay. Streaming improves perceived latency while adding protocol overhead and making chunk gaps an imperfect proxy for token ITL.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 09_hf_prefill_decode --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile small --new-tokens 1
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first model matrix kernel inside `model_forward`, after cache-position construction. It is a first-request prefill diagnostic; it does not measure continuation counters or client-observed TTFT. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 09_hf_prefill_decode '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=model_forward,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile small --new-tokens 1
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--new-tokens` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `new_tokens` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

An off-by-one cache length or mask length invalidates the schedule. Unsupported cache APIs require artifact/environment qualification, not silently discarding cache checks. Do not confuse an EOS-aware service policy with this fixed-token mechanics contract.

Avoid reporting one end-to-end number without separating queue, prefill, and decode.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Phase boundaries must follow actual model execution. Use the serving and streaming clients to add queueing, transport, and delivery observations; do not label a device-only prefill sample as end-to-end service latency.

Preserve the full phase decomposition and token counts with each result.

Explain why prefill and decode prefer different batching and kernels.
