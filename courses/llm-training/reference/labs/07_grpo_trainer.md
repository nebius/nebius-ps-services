# Lab 07: Verify the GRPO generation-to-update loop

An objective formula is only one part of reward-guided training: the system must generate completions, score them, compute a loss, propagate gradients, and update parameters. This lab exercises that loop with a small pinned model and LoRA adapters. Its deliberately artificial reward guarantees a test signal, allowing you to validate plumbing without pretending to measure learning quality.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Qualify Transformers, PEFT, TRL, datasets, and the approved immutable model artifact in the Training environment. Use one H100 and private output storage. Complete Lab 06 before interpreting the trainer loss.

## Concepts and code path

TRL (Transformer Reinforcement Learning) supplies a GRPO trainer that connects generation, rewards and updates using the pinned model/tokenizer and LoRA configuration. A small local prompt dataset produces completion pairs. The callback assigns alternating zero and one rewards, regardless of text quality, to create a controlled within-group signal. Adapter hooks observe gradients and saved parameter copies reveal actual updates. Require finite nonzero gradients and a parameter change; these checks establish the update path, not reward quality.

## Practice

`labs/07_grpo_trainer.py` runs a bounded GRPO training trial with LoRA and a deterministic alternating reward. It checks reward variation, nonzero gradients, and adapter updates, then writes training metrics without claiming improved answer quality.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/07_grpo_trainer/logs/%j.out" \
  --error="$PWD/results/07_grpo_trainer/logs/%j.err" \
  slurm/07_grpo_trainer.sbatch --workload small --steps 3
```

## Check your results

Each new job owns `results/07_grpo_trainer/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/07_grpo_trainer/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/07_grpo_trainer/logs/$LAB_JOB_ID.out"
cat "results/07_grpo_trainer/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require trainer completion, finite loss, nonzero within-group reward variance, finite nonzero adapter gradients, and a nonzero adapter update. Inspect runtime and gradient/update fields. None of these gates certifies completion quality.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Steps | `steps` | `none` |
| Train runtime seconds | `train_runtime_seconds` | `s` |
| Train loss | `train_loss` | `none` |
| Adapter max parameter delta | `adapter_max_parameter_delta` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 07_grpo_trainer --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 07_grpo_trainer \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Inspect model and step options, then run a bounded three-step small trial. Each run needs a new private trainer-output directory; existing output reuse is rejected intentionally.

```bash
"$COURSE_PYTHON" labs/07_grpo_trainer.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/07_grpo_trainer/logs/%j.out" \
  --error="$PWD/results/07_grpo_trainer/logs/%j.err" slurm/07_grpo_trainer.sbatch --workload small --steps 3
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Trace one pair through generation, scoring, relative advantage, and update. Why would a constant reward hide a broken or inactive learning signal? Which additional components would dominate cost in a real rollout-heavy training system?

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/07_grpo_trainer/logs/%j.out" \
  --error="$PWD/results/07_grpo_trainer/logs/%j.err" slurm/07_grpo_trainer.nsys.sbatch --workload small --steps 3
```

The native Systems command is in `slurm/07_grpo_trainer.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first model matrix kernel inside `trainer_train`, excluding adapter cloning and setup before `trainer.train()`. That kernel may belong to rollout generation; it is not proof of a backward pass or parameter update. Use Systems and the original gradient/update checks for those claims. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/07_grpo_trainer/logs/%j.out" \
  --error="$PWD/results/07_grpo_trainer/logs/%j.err" slurm/07_grpo_trainer.ncu.sbatch --workload small --steps 3
```

The native Compute command is in `slurm/07_grpo_trainer.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--steps` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `steps` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Zero reward variance invalidates the plumbing proof, as do zero adapter gradients. Missing package APIs block environment qualification. Do not substitute a different trainer or remove the gradient checks simply to finish the run.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

A systems small test should have a controlled signal and observable state transition. A meaningful extension replaces the artificial reward with a reviewed task criterion and adds held-out evaluation, while retaining the existing plumbing gates.
