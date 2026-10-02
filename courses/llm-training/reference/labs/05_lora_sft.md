# Lab 05: Fine-tune a small model with LoRA adapters

LoRA trains low-rank adapter parameters while leaving most pretrained weights frozen. This lab performs a short supervised fine-tuning exercise on a pinned public model and checks that adapters receive gradients and actually change. You will distinguish parameter efficiency, memory use, and observable model changes from any unsupported claim that a tiny training run improves general model quality.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Qualify the Training extras and the approved model artifact described in the runbook. Model revisions must be immutable commits and remote custom code is disabled. Downloads, model caches, and adapter artifacts remain private.

Adapter compute can be small relative to the base GEMM but may introduce extra kernels or prevent an optimized fused path. Verify dispatch and end-to-end time on the target stack.

## Concepts and code path

For one projection, the adapter adds `s * B(Ax)` to the frozen base result `Wx`. A reduces the input to rank r; B expands it to the output width; s is the configured update scale. Only the selected trainable factors receive optimizer updates. The reduction in trainable state follows from these smaller matrix shapes, not from skipping the base projection.

Transformers loads the pinned tokenizer and model; PEFT (Parameter-Efficient Fine-Tuning) attaches LoRA factors to `q_proj` and `v_proj`. Inspect actual trainable parameters before interpreting the requested configuration. The supplied workload masks padding only, so question and answer tokens both contribute to loss; response-only masking is an extension. The script records trainable counts, adapter gradients and updates, and a bounded held-out observation. PEFT configures the base weights as non-trainable; this lab does not checksum every base tensor before and after training. Frozen base weights still occupy memory.

For an illustrative 4096-by-4096 projection and rank 8, the full matrix has about 16.8 million values while LoRA adds `8*(4096+4096)=65,536` trainable values. This explains the potential optimizer-state reduction, not a measured speedup or a supplied full-fine-tuning comparison. The guided experiment keeps LoRA fixed and changes only gradient-buffer clearing with `--zero-grad-fill`; base weights and activations remain in memory.

## Practice

`labs/05_lora_sft.py` fine-tunes a pinned small language model using LoRA adapters. It checks finite, nonzero adapter gradients and updates, then writes parameter counts, loss, memory, and bounded held-out prediction evidence.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_lora_sft/logs/%j.out" \
  --error="$PWD/results/05_lora_sft/logs/%j.err" \
  slurm/05_lora_sft.sbatch --workload small
```

## Check your results

Each new job owns `results/05_lora_sft/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/05_lora_sft/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/05_lora_sft/logs/$LAB_JOB_ID.out"
cat "results/05_lora_sft/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require finite loss, nonzero finite adapter gradients, a nonzero adapter update, and the parameter-efficiency gate. Inspect `trainable_percent`, loss values, `adapter_max_parameter_delta`, elapsed time, and peak allocation. A changed digest is evidence of changed output, not better output.

Retain the supplied trainable/total counts, adapter settings, loss, and memory fields. The baseline also records `held_out_next_token_before/after` and `held_out_logits_digest_before/after` for one fixed held-out input. Preserve these bounded observations without treating a changed token or digest as proof of improved quality. Adapter persistence, reload comparisons, and broader held-out task-quality evaluation require explicit learner extensions.

Parameter-efficiency and runtime speed are different claims.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Trainable parameters | `trainable_parameters` | `none` |
| Trainable percent | `trainable_percent` | `percent` |
| Elapsed (seconds) | `elapsed_ms` | `s` |
| Peak allocated mib | `peak_allocated_mib` | `bytes` |
| Adapter max parameter delta | `adapter_max_parameter_delta` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 05_lora_sft \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Inspect artifact options, then use the pinned defaults after approval. Changing `--model` requires a matching immutable `--revision` and a review of tokenizer, architecture, and target adapter modules.

```bash
"$COURSE_PYTHON" labs/05_lora_sft.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_lora_sft/logs/%j.out" \
  --error="$PWD/results/05_lora_sft/logs/%j.err" slurm/05_lora_sft.sbatch --workload small
```

For the guided candidate, run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_lora_sft/logs/%j.out" \
  --error="$PWD/results/05_lora_sft/logs/%j.err" slurm/05_lora_sft.sbatch --workload small --zero-grad-fill
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which persistent tensors disappear from the trainable-state ledger and which remain? For a response-only masking extension on the supplied labels, explain why ignoring prompt labels does not stop response tokens from attending to the prompt. Identify what a meaningful held-out quality evaluation would add.

Lower rank reduces state and possibly expressiveness. Targeting more modules adds flexibility and memory. Merging adapters can simplify inference but changes artifact management and may remove convenient multi-adapter serving.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_lora_sft/logs/%j.out" \
  --error="$PWD/results/05_lora_sft/logs/%j.err" slurm/05_lora_sft.nsys.sbatch --workload small
```

The native Systems command is in `slurm/05_lora_sft.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_lora_sft/logs/%j.out" \
  --error="$PWD/results/05_lora_sft/logs/%j.err" slurm/05_lora_sft.ncu.sbatch --workload small
```

The native Compute command is in `slurm/05_lora_sft.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the default gradient release with --zero-grad-fill at fixed model, data, seed and update count. Inspect the zero_grad range and complete-step time, require the same finite-loss/update checks, and compare final loss within numerical tolerance. Independently inspect whether clearing traffic or buffer allocation dominates before choosing a policy.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate forward and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing target modules indicate an artifact/architecture mismatch. Zero adapter gradients can indicate incorrect freezing or label masking. Preserve failures and do not enable remote code or choose an unreviewed model to bypass setup.

Avoid comparing LoRA and full tuning with different data, effective batch, or evaluation prompts.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Parameter-efficient training needs verified gradient flow and updates. Treat this as a small SFT systems example; expand the dataset, evaluation, and checkpoint protocol before claiming useful adaptation.

Keep the objective fixed and report trainable state, optimizer state, memory, and quality separately.

Explain what LoRA saves and what it normally does not save.

Run Lab 05 and audit trainable parameters, the implemented padding-only loss mask, loss, and memory. Its checkpoint saving is not implemented. Extension: save adapters with their configuration and pinned base revision, then test save/reload equivalence on a fixed evaluation input; mask prompt and padding labels when adding response-only supervision.
