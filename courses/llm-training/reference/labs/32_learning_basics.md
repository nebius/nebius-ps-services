# Lab 32: Learn a weight and separate training from inference

This first exercise makes learning visible without a transformer, downloaded model or service. A scalar model predicts weight × input. Starting with weight zero, PyTorch computes prediction error, differentiates it and updates the weight for 30 steps, then checks how closely its predictions reproduce the examples. You then use the learned weight on an input excluded from training and confirm that inference does not change the weight. The purpose is to understand the state transition before studying performance engineering.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Activate the course's PyTorch environment. CPU is the default and is sufficient for this conceptual exercise; CUDA is an explicit option on a full H100 allocation. No data or model is downloaded. Read the opening definitions of parameter, prediction, loss, gradient, optimizer and held-out evaluation. Both profile labels run the same tiny example; neither is a performance benchmark. The seed field is shared result metadata, but this deterministic example does not sample training data.

H100 changes the feasible batch, precision, and parallel execution, not the definition of the objective. Tiny course models demonstrate mechanics and performance measurement, never production model quality.

## Concepts and code path

`run_experiment` owns the learning loop and checks; `main` selects the device and writes the result. `torch.nn.Parameter` marks the scalar weight as trainable. Inputs [1, 2] have targets [2, 4]. Each of 30 steps clears the previous gradient, computes mean squared error, calls backward and applies SGD with learning rate 0.1. Clearing matters because backward otherwise accumulates gradients.

After learning, inference mode computes the remaining loss and the prediction for input 3. It records no new gradient graph. The program separately checks the parameter value and the absence of gradients; disabling graph recording alone is not proof that arbitrary application code cannot mutate a weight.

## Practice

`labs/32_learning_basics.py` learns the scalar relationship `y = 2x` using autograd and SGD. It checks the learned weight, held-out prediction, and unchanged parameters during inference, then writes losses, gradient, and update observations.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/32_learning_basics/logs/%j.out" \
  --error="$PWD/results/32_learning_basics/logs/%j.err" \
  slurm/cpu.sbatch \
  labs/32_learning_basics.py --device cpu
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/32_learning_basics/logs/$LAB_JOB_ID.out"
cat "results/32_learning_basics/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Expect `initial_weight` 0, `loss_before` 10 and `first_gradient` -10. After the first update the weight would be 1 and loss 2.5; after the supplied 30 updates `final_weight` should be approximately 2 and `held_out_prediction` approximately 6. FP32 checks use rtol 1e-5 and atol 1e-6. `weight_after_inference` must equal the final learned value. Inspect the result's environment before labeling it a CPU or H100 run. No elapsed-time or speedup claim is produced.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Initial weight | `initial_weight` | `none` |
| First gradient | `first_gradient` | `none` |
| Final weight | `final_weight` | `none` |
| Loss before | `loss_before` | `none` |
| Loss after | `loss_after` | `none` |
| Held out prediction | `held_out_prediction` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 32_learning_basics \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. CPU runs have no allocated GPU; inspect their artifact fields and job logs. For optional CUDA runs, set the time picker to **Experiment start** through **Experiment end**, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Submit the CPU example again and inspect the first gradient, learned weight, and held-out prediction. The second submission is an optional CUDA alternative. Save each job number for the result checks above.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/32_learning_basics/logs/%j.out" \
  --error="$PWD/results/32_learning_basics/logs/%j.err" slurm/cpu.sbatch labs/32_learning_basics.py --device cpu
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/32_learning_basics/logs/%j.out" \
  --error="$PWD/results/32_learning_basics/logs/%j.err" slurm/single_gpu.sbatch labs/32_learning_basics.py --device cuda
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Hand-calculate the first update before reading the result. Why does a negative gradient increase the weight when SGD subtracts it? Why would omitting gradient clearing change the update rule? As an explicit source-edit extension, try a smaller learning rate and compare update count; do not invent a command-line flag. Explain why one successful held-out point on this synthetic linear rule does not prove generalization on language, noisy data or an unrelated task.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/32_learning_basics/logs/capture-%J-%t.out" \
  --error="results/32_learning_basics/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/32_learning_basics/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/32_learning_basics.py --device cuda
```

**Nsight Systems evidence:** The documented --device cuda run executes GPU work; the optional CPU run does not. For --device cuda only, expand lab_workload and CUDA API/GPU rows. Observe short elementwise/reduction launches and synchronization from scalar reads. Relate them to the 30 updates and held-out result; this scalar exercise is not a training-throughput benchmark. CPU mode has no GPU profiling. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

If PyTorch cannot be imported, check that the intended interpreter is selected and the course dependencies are installed. CUDA selection intentionally fails if a full compatible H100 is unavailable; use the CPU mode for the introductory concept. Numerical failure after editing the loop requires checking the target formula, reduction, learning rate and gradient clearing. Do not weaken the tolerance to conceal an incorrect update.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Training changes parameters through forward, loss, backward and optimizer steps. Inference uses the resulting parameters without those updates. Carry this distinction into next-token labels, transformer logits and cross-entropy; the larger Lab 01 adds realistic tensor structure, not a different meaning of learning.
