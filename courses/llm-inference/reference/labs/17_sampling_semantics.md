# Lab 17: Compare greedy and seeded stochastic generation

Generation settings change both output behavior and the amount of work performed, so a performance comparison must declare them. This lab compares greedy decoding with a seeded stochastic profile on a pinned model. You will verify repeatability within each profile and inspect output lengths, while avoiding the mistaken conclusion that different sampling profiles are a controlled one-factor optimization.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/17_sampling_semantics.json).

Audit the model and tokenizer revision and use one H100 in the mechanics environment. Review temperature, top-k, top-p, EOS, and maximum-new-token behavior. A fixed seed applies to a declared environment, not every possible implementation.

Sampling kernels are usually not the dominant H100 compute, but host transfers, synchronization, or inefficient per-request processing can become visible at high throughput.

## Concepts and code path

The source tokenizes a fixed prompt and runs repeated greedy and seeded stochastic generations. It compares token-ID sequences within each profile, records elapsed time and generated counts, and requires nonempty outputs. The stochastic profile changes multiple controls together; the code does not implement an independent sweep of each sampling parameter.

Given a baseline producing 200 tokens in 2 seconds and a candidate producing 100 after a changed stop ID in 1.2 seconds, completion latency improves while output rate falls from 100 to about 83 tokens/s and semantics differ. Change to the same stop/sampling profile. Expected observation: a fixed-work comparison matches token counts and stopping behavior. A representative stochastic comparison instead fixes the decoding policy and compares length/finish distributions, quality, and token-normalized performance; individual samples need not be identical. For a separate sampling example, probabilities [0.50, 0.30, 0.15, 0.05] give greedy token A. Top-k=2 keeps A and B and renormalizes to [0.625, 0.375]. Top-p=0.9 applied to the original distribution keeps A, B, and C (cumulative 0.95), then renormalizes by 0.95. Temperature acts on logits before these filters; at T=0.5 the probabilities are proportional to the squared original probabilities, giving approximately [0.685, 0.247, 0.062, 0.007]. These are separate controlled examples, not the result of applying every setting at once.

Run Lab 17 and compare greedy and seeded stochastic profiles without mixing their conclusions. Lab 17 compares profiles with multiple settings. For causal interpretation, extend the experiment by varying only temperature, then only top-k, then only top-p from an otherwise fixed profile; repeat enough samples to inspect candidate frequencies, lengths, and finish reasons.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the supplied profiles under one output budget. Changing the maximum budget is a separate experiment and may not change actual length if an earlier stopping condition is reached.

```bash
umask 077
python3 tools/submit_lab.py --lab 17_sampling_semantics slurm/single_gpu.sbatch labs/17_sampling_semantics.py --profile small --max-new-tokens 32
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 17_sampling_semantics --job "$LAB_JOB_ID"
```

Require greedy repeatability, seeded-sampling repeatability, and nonempty outputs. Inspect generated token counts, token IDs, and elapsed time for each profile. These are bounded behavior checks, not a statistical distribution or quality evaluation.

Retain generation settings, seeds, stop reasons, token counts, outputs or reviewed hashes, and quality checks.

A speedup is invalid if it changes the accepted decoding or quality contract.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Greedy seconds | `greedy_seconds` | `s` |
| Seeded sample seconds | `seeded_sample_seconds` | `s` |
| Greedy output tokens | `greedy_output_tokens` | `none` |
| Seeded sample output tokens | `seeded_sample_output_tokens` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 17_sampling_semantics \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

For candidate probabilities 0.6, 0.3, and 0.1, explain which survive top-k of two and a nucleus threshold of 0.8. Why can shorter sampled output make a run finish earlier without improving per-token execution?

Deterministic tests are reproducible but may not represent production diversity. Stochastic tests represent behavior better but require more samples and quality statistics. Forcing fixed output length simplifies performance analysis while changing real stop behavior.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 17_sampling_semantics --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/17_sampling_semantics.py --profile small --max-new-tokens 32
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first model matrix kernel inside `generation`, excluding model/input initialization and generation setup kernels. It is a first-generation diagnostic, not a warmed end-to-end generation measurement. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 17_sampling_semantics '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=generation,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/single_gpu.sbatch labs/17_sampling_semantics.py --profile small --max-new-tokens 32
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--max-new-tokens` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `max_new_tokens` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Repeatability failures require inspecting seed placement, artifact identity, and nondeterministic execution. Do not compare text alone when tokenization differs. A maximum budget is an upper bound, not a guaranteed output count.

Avoid comparing requests with different output lengths using only requests per second.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Fix sampling and stopping semantics before timing comparisons. Extension: vary temperature, top-k, and top-p one at a time across many seeds, and examine candidate frequencies, output lengths, and task quality separately.

Fix semantics and report both request and token-normalized work.

State when deterministic equality is required and when distributional evaluation is appropriate.
