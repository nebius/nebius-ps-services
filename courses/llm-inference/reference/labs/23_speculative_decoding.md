# Lab 23: Trace speculative acceptance, rejection, and recovery

Speculative decoding proposes several tokens with a cheaper draft and asks the target to verify them. This lab uses a small synthetic greedy target to make acceptance and recovery visible. You will learn why accepting a long prefix can reduce target calls and why draft overhead can erase that benefit when proposals are frequently rejected.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 in the mechanics environment. No external target/draft model is required. The example is a first-order synthetic greedy process, not a transformer engine or proof of stochastic speculative-distribution equivalence.

The target H100 may already batch decode efficiently. A weak draft can consume GPU capacity or host orchestration and reduce throughput at higher concurrency, so real-engine A/B trials are required.

## Concepts and code path

This synthetic target is first-order: current-token input determines next-token scores through embedding, linear and GELU operations. That shortcut is not a full-transformer causal-history assumption.

The code builds a target-only sequence and two speculative cases with deliberately high and low acceptance. It proposes a draft block, verifies the accepted prefix, emits a target recovery token at the first rejection, or emits a target bonus token after full acceptance. It records target calls, proposed/accepted tokens, timing, and memory while requiring the same final greedy sequence.

Given draft proposals `[A,B,C,D]` where the target accepts `A,B` and rejects `C`, keep the two-token prefix, generate the algorithm’s corrected recovery token, and discard `D`. Change to all four accepted. Expected observation: the target may provide a bonus token, but speedup exists only if draft plus verification cost is below the saved target calls and output semantics match.

Run Lab 23 to inspect acceptance patterns, then run `slurm/33_speculative_engine_client.sbatch` with explicit target and draft artifacts. The live launcher performs three independent engine restarts per variant, alternates order, fails if paired greedy-output digests differ, and captures bounded AIPerf profiles.

## Practice

`labs/23_speculative_decoding.py` compares synthetic target-only greedy decoding with high- and low-acceptance draft proposals. It verifies identical sequences and records timing, target calls, acceptance, recovery, bonus tokens, and memory use.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/23_speculative_decoding/logs/%j.out" \
  --error="$PWD/results/23_speculative_decoding/logs/%j.err" \
  slurm/23_speculative_decoding.sbatch --workload small --draft-length 4 --max-new-tokens 32
```

## Check your results

Each new job owns `results/23_speculative_decoding/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/23_speculative_decoding/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/23_speculative_decoding/logs/$LAB_JOB_ID.out"
cat "results/23_speculative_decoding/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require exact target-greedy output in both speculative paths, target-call reduction in the high-acceptance case, and the declared low-acceptance control behavior. Inspect accepted-prefix histograms, bonus/recovery counts, target calls, timings, and peak allocation.

For the separate live-engine campaign, record target/draft revisions, proposal length, acceptance metrics exposed by the engine, target calls, output digests, TTFT, ITL/TPOT, throughput, and failures. Raw generated text remains private.

High acceptance is useful only when the draft and verification path improve end-to-end service metrics.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Target only / median e2e (seconds) | `target_only.median_e2e_ms` | `s` |
| High acceptance / median e2e (seconds) | `high_acceptance.median_e2e_ms` | `s` |
| Low acceptance / median e2e (seconds) | `low_acceptance.median_e2e_ms` | `s` |
| High acceptance / mean accepted prefix | `high_acceptance.mean_accepted_prefix` | `none` |
| Low acceptance / mean accepted prefix | `low_acceptance.mean_accepted_prefix` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 23_speculative_decoding \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Compare draft lengths under the same output budget. The two acceptance controls are supplied by the lab and must both remain in the report.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/23_speculative_decoding/logs/%j.out" \
  --error="$PWD/results/23_speculative_decoding/logs/%j.err" slurm/23_speculative_decoding.sbatch --workload small --draft-length 4 --max-new-tokens 32
sbatch --chdir="$PWD" \
  --output="$PWD/results/23_speculative_decoding/logs/%j.out" \
  --error="$PWD/results/23_speculative_decoding/logs/%j.err" slurm/23_speculative_decoding.sbatch --workload small --draft-length 8 --max-new-tokens 32
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Work through one partially accepted proposal and identify the first token that must be discarded. Why can a longer draft increase wasted work? Compare saved target calls with measured total duration rather than equating the two.

Larger `k` offers more target-call savings when acceptance is high but wastes more draft work on rejection. A stronger draft improves acceptance while costing more. Self-speculative methods reduce extra weights but have different support and tuning.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/23_speculative_decoding/logs/%j.out" \
  --error="$PWD/results/23_speculative_decoding/logs/%j.err" slurm/23_speculative_decoding.nsys.sbatch --workload small --draft-length 4 --max-new-tokens 32
```

The native Systems command is in `slurm/23_speculative_decoding.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first matrix kernel inside `target_only`, excluding model initialization and construction of the ideal draft table. It diagnoses the target-only path; use Systems and the clean results to compare speculative acceptance, recovery and total work. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/23_speculative_decoding/logs/%j.out" \
  --error="$PWD/results/23_speculative_decoding/logs/%j.err" slurm/23_speculative_decoding.ncu.sbatch --workload small --draft-length 4 --max-new-tokens 32
```

The native Compute command is in `slurm/23_speculative_decoding.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--draft-length` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A sequence mismatch often indicates incorrect recovery or bonus handling. Check output-budget boundaries and accepted-prefix indexing before timing. A slower speculative result is valid evidence when proposal/verification costs dominate.

Avoid comparing greedy outputs while claiming correctness for stochastic sampling.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Acceptance is a mechanism, not a speedup guarantee. Optional Lab 33 applies real-engine paired greedy checks and independent benchmark trials after target/draft artifact and environment qualification.

Validate the algorithm's sampling semantics and measure the complete path.

Explain how acceptance rate and draft cost interact.
