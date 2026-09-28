# Lab 10: Measure batched offline generation with vLLM

Offline inference submits a known collection of prompts to an engine without exposing an HTTP service. This lab runs real vLLM generation inside the qualified container and accounts for all measured requests and output tokens. You will distinguish engine batch throughput from online request latency and avoid reporting only the final iteration's tokens as the whole run.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/10_vllm_offline.json).

Prepare the approved container runner, exact `VLLM_IMAGE_DIGEST`, and immutable model artifact according to the runbook. Keep the engine environment isolated from mechanics PyTorch. One H100 must have enough memory for the model and configured context.

## Concepts and code path

The launcher runs the Python lab in the engine container. The lab constructs vLLM, warms generation, submits prompt batches, validates every returned completion, and aggregates prompt/output token counts and finish reasons across measured iterations. The elapsed window covers measured generation, not cold engine construction or an online queueing workload.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Inspect the launcher and lab options before running the pinned default. `--enforce-eager` is an explicit engine option for a separately declared comparison, not an automatic fallback on initialization failure.

```bash
umask 077
bash slurm/vllm_offline.sbatch --help
python3 tools/submit_lab.py --lab 10_vllm_offline slurm/vllm_offline.sbatch --profile small
python3 tools/submit_lab.py --lab 10_vllm_offline slurm/vllm_offline.sbatch --profile small --enforce-eager
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 10_vllm_offline --job "$LAB_JOB_ID"
```

Require one completion per prompt, positive prompt counts, bounded positive output counts, and all iterations completed. Inspect total requests/tokens, per-request count summaries, finish reasons, elapsed seconds, and output tokens/s.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Elapsed seconds | `elapsed_seconds` | `s` |
| Output tokens per second | `output_tokens_per_second` | `tokens/s` |
| Prompt tokens total | `prompt_tokens_total` | `none` |
| Output tokens total | `output_tokens_total` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 10_vllm_offline \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Why can actual output length be less than the maximum token budget? Explain why comparing runs with different stopping behavior can change throughput without improving the engine. Which startup costs are excluded from this measurement?

Capture a separate diagnostic run with `--in-process`. This explicit diagnostic setting keeps vLLM GPU launches in the process that owns the course NVTX range. The normal engine uses a child process, and parent NVTX ranges do not extend into that child. Keep the default process mode for clean throughput measurements; this capture cannot measure its inter-process overhead.

```bash
python3 tools/submit_lab.py --lab 10_vllm_offline --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/vllm_offline.sbatch --profile small --in-process
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `vllm_generate`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload and `--in-process` setting in a separate Compute capture. The `vllm_generate` range wraps measured generation calls and excludes model construction and warmup. Its first launches can update request buffers before model math. The command below selects the first matching matrix kernel inside measured generation, using full kernel names. In Systems, identify the selected launch and its role before interpreting the counters. If your qualified engine uses another matrix kernel name, review that name and update `COURSE_PROFILE_KERNEL` for a new diagnostic trial. A buffer-update capture does not measure model matrix work, and one matrix capture does not characterize the complete engine.

Request additional **host RAM** for Compute replay. The profiler can back up device allocations, including vLLM's KV cache, in system memory. A job that runs normally with the launcher's 64 GiB allocation can therefore be killed during capture. The command below requests 256 GiB through Slurm's `SBATCH_MEM_PER_NODE` variable, whose numeric value is in MiB. This changes the job allocation without changing its one-GPU workload. Verify that the worker can satisfy the request; an out-of-memory capture is incomplete evidence.

```bash
SBATCH_MEM_PER_NODE=262144 python3 tools/submit_lab.py --lab 10_vllm_offline '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=vllm_generate,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/vllm_offline.sbatch --profile small --in-process
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the default engine with `--enforce-eager`, the two settings of this boolean control. Keep the model, prompts, sampling and profile fixed. Predict the throughput effect, verify completion accounting, and inspect the named report views. Run additional independent repetitions of both settings and explain whether measurement variation supports the prediction.

**Nsight Systems evidence:** Capture the single-GPU engine in-process for diagnostics; clean timing retains its default engine process mode. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate vllm_generate after warmup and follow its host submissions into GPU streams. Inspect generation kernels and copies, then test the named tuning control with separate unprofiled runs. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing image identity, artifact mismatch, engine startup failure, or insufficient memory blocks this live lab. Do not replace it with a mechanics simulation. Preserve private logs and reduce the declared workload only as a new trial.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Offline throughput requires complete work accounting and fixed generation semantics. Next, use the serving clients to measure request-level behavior under concurrency; offline output rate does not establish TTFT or online goodput.
