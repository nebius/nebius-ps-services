# Lab 18: Reduce padded prefill work with length buckets

A single batch rectangle can waste substantial work when one prompt is much longer than the others. This lab compares one padded prefill batch with two length buckets containing the same prompts. You will verify corresponding last-token logits and account for padding while considering the extra launches and smaller batch sizes introduced by bucketing.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and the audited immutable Hugging Face artifact in the mechanics environment. Review padding, attention masks, and the difference between a real prompt token and an allocated rectangle position.

## Concepts and code path

Inputs are right-padded, so each prompt's last real-position logits come from `length-1`; left-padded generation needs a different index rule.

The code tokenizes the prompt set, measures true lengths, builds a single padded batch, and separately groups prompts by length. Both paths execute prefill with the appropriate masks. Results are restored to corresponding prompts before comparing final-position logits. This is a prefill batching experiment, not continuous online admission or a complete generation service.

## Practice

`labs/18_padding_bucketing.py` compares one padded prefill batch with two length buckets containing identical prompts. It validates equivalent last-token logits and records padding waste, timing distributions, and numerical error.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/18_padding_bucketing/logs/%j.out" \
  --error="$PWD/results/18_padding_bucketing/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/18_padding_bucketing.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/18_padding_bucketing/logs/$LAB_JOB_ID.out"
cat "results/18_padding_bucketing/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `equivalent_last_token_logits`. Each prompt's compared logits, norm calculation and relative L2 error must be finite, with error no greater than `0.02`, before the maximum is reported. The checks below apply to each prompt; NaN in either an early or a later prompt must fail rather than disappear during aggregation. Inspect true prompt tokens, single-batch and bucketed rectangle tokens, padding counts, and both timing distributions. Fewer padded positions do not guarantee lower elapsed time when launch or batching efficiency changes.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Single padded batch / median (seconds) | `single_padded_batch.median_ms` | `s` |
| Two length buckets / median (seconds) | `two_length_buckets.median_ms` | `s` |
| Single batch padding tokens | `single_batch_padding_tokens` | `none` |
| Bucketed padding tokens | `bucketed_padding_tokens` | `none` |
| Maximum last logit relative l2 | `maximum_last_logit_relative_l2` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 18_padding_bucketing \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the supplied prompt fixture before editing lengths or grouping policy. Keep model revision, prompt contents, dtype, and semantic masks identical between the compared paths.

```bash
"$COURSE_PYTHON" labs/18_padding_bucketing.py --help
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/18_padding_bucketing/logs/%j.out" \
  --error="$PWD/results/18_padding_bucketing/logs/%j.err" slurm/single_gpu.sbatch labs/18_padding_bucketing.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Calculate the padding fraction before and after grouping. Which prompt determines each bucket's rectangle width? Explain the trade-off between finer buckets, extra launches, and smaller matrix batches.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/18_padding_bucketing/logs/capture-%J-%t.out" \
  --error="results/18_padding_bucketing/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/18_padding_bucketing/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/18_padding_bucketing.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/18_padding_bucketing/logs/capture-%J-%t.out" \
  --error="results/18_padding_bucketing/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/18_padding_bucketing/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/18_padding_bucketing.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare unbucketed and length-bucketed prompts with the same true tokens. Independently calculate padding waste and choose the grouping only if saved work outweighs extra launches.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A last-token mismatch may indicate incorrect padding-side handling, mask alignment, or output restoration order. Check prompt identity and valid final positions before changing numerical tolerances.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Length grouping is useful only when it preserves semantics and improves performance at the chosen measurement boundary. A live-service extension must also include queue delay while waiting to form buckets; this offline prefill measurement excludes that delay.
