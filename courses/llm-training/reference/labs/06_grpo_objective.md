# Lab 06: Work through a group-relative policy objective

GRPO uses rewards from multiple completions of the same prompt to construct a relative training signal. This lab isolates the objective on small GPU tensors without loading a language model. You will inspect advantages, probability ratios, clipping, and a reference-policy penalty so the later trainer's loss has a concrete mathematical meaning.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/06_grpo_objective.json).

Use one H100. The calculation below shows how group rewards become normalized advantages; `--group-size` controls the number of completions in each group.

H100 accelerates policy inference and training differently. Variable decode length creates rollout stragglers, while trainer GEMMs prefer dense packed tokens; rate matching between the two stages matters.

## Concepts and code path

The code generates reward groups and old, new and reference log probabilities. It normalizes each group's rewards, forms the clipped new-to-old surrogate and minimizes its negative plus `0.02 * (exp(d)-d-1)`, where `d = reference_log_probability - new_log_probability`. Backward differentiates this toy loss with respect to new log probabilities. There is no language model, rollout engine or optimizer update, and the sampled penalty is not a full-policy KL measurement.

Given four rewards `[1, 1, 2, 4]`, the mean is 2 and centered advantages are `[-1,-1,0,2]`; normalize only with a declared epsilon and deviation rule. Change to `[3,3,3,3]`. Expected observation: zero variance produces zero or otherwise explicitly handled advantages, not NaNs; the report also shows rollout and reward time before claiming trainer optimization. For a separate clipping calculation with epsilon=0.2 and A=+1, ratio=1.5 gives min(1.5,1.2)=1.2: increasing the already-favored action beyond the upper clip adds no surrogate reward. With A=-1 and ratio=0.5, min(-0.5,-0.8)=-0.8: decreasing the disfavored action below the lower clip likewise stops improving this term. Opposite-direction changes can remain unclipped. Walk through these terms before interpreting gradients or the optional reference penalty.

Verify finite gradients before interpreting objective timing. A synthetic reward pattern checks the update plumbing; it does not establish model quality.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the default group size, then change only that size to inspect the group statistics. This is an objective mechanics exercise, not a throughput benchmark of a GRPO system.

```bash
umask 077
python3 tools/submit_lab.py --lab 06_grpo_objective slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile small
python3 tools/submit_lab.py --lab 06_grpo_objective slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile small --group-size 4
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 06_grpo_objective --job "$LAB_JOB_ID"
```

Require zero-mean group advantages within the implemented tolerance and finite gradients. Inspect `loss`, `mean_approximate_kl`, and `max_group_advantage_mean_error`. These checks do not establish that rewards represent quality or that a learned policy improves.

Record candidate groups, rewards, normalized advantages, ratios, clipping, KL term, gradients, and policy version.

Generation often dominates elapsed time, while the objective determines whether the update is meaningful. A synthetic reward can validate mechanics but cannot support a policy-quality claim.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Group size | `group_size` | `none` |
| Loss | `loss` | `none` |
| Mean approximate kl | `mean_approximate_kl` | `none` |
| Max group advantage mean error | `max_group_advantage_mean_error` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 06_grpo_objective \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Work out how a completion with above-group-average reward affects the surrogate when its probability ratio grows. Explain why clipping differs from simply clipping rewards, and why identical rewards within a group provide no relative preference.

Larger groups improve relative comparison but multiply rollout cost. Disaggregation scales components independently but transfers weights and can increase policy staleness. Aggressive reward optimization can exploit verifier weaknesses rather than improve the intended task.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 06_grpo_objective --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first reduction kernel inside `grpo_objective`, after random rewards and log-probabilities are constructed. This objective is reduction and elementwise work, so no matrix-kernel filter is applied. One kernel does not prove the complete loss or backward pass. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 06_grpo_objective --export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=grpo_objective slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--group-size` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `group_size` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Non-finite values can arise from invalid scaling or extreme probability ratios. Inspect group variance and the objective terms before changing thresholds. Do not interpret a scalar loss in isolation from its constituent terms.

Avoid using stale rollout weights without recording policy-version lag.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

The objective converts relative reward into a constrained update signal. Next, run Lab 07 to connect generation, reward evaluation, adapters, and trainer updates while preserving the distinction between plumbing checks and quality evaluation.

Separate rollout, scoring, synchronization, and update phases and validate the objective first.

Trace one response from generation through reward to parameter update.
