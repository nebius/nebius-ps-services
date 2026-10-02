# Lab 13: Decide which tokens contribute to the training loss

Prompt text and padding may appear in a batch without being intended training targets. This lab constructs two short causal sequences and explicitly masks selected labels. You will count trained positions and compare masked with unmasked cross-entropy, learning that a loss mask chooses supervision while an attention mask controls which context an operation may read.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and the local tiny-model implementation. No tokenizer or external artifact is required. Review the one-position shift between input tokens and next-token labels.

## Concepts and code path

The program slices token sequences into inputs and shifted labels, assigns `-100` to prompt-target and padding positions, and computes vocabulary logits. Cross-entropy with `ignore_index=-100` excludes those targets from its mean. Backward then propagates the masked objective through the model. Ignoring a target does not erase that token's contextual contribution to later predictions.

Given microbatches with 100 and 300 valid tokens, averaging their two mean losses gives the short batch half the weight instead of its correct one quarter: twice its intended batch contribution. Each token in that short batch receives three times the weight of a token in the longer batch. Change to sum both loss numerators and divide by 400 valid tokens before the equivalent gradient update. Expected observation: gradients match the concatenated reference within tolerance, then clipping and one AdamW step occur once. To check where the loss numerator comes from, take vocabulary probabilities [0.1, 0.7, 0.2]. A target at index 1 contributes -log(0.7) ≈ 0.3567; a target at index 2 contributes -log(0.2) ≈ 1.6094. Their token-mean loss is (0.3567 + 1.6094)/2 ≈ 0.9831. A third ignored position contributes neither numerator nor denominator. In PyTorch, cross_entropy with reduction='sum' supplies the numerator; divide by the count of nonignored targets, not padded tensor size. Skip or explicitly reject a globally empty target batch.

This is a hand calculation and a proposed complete-update extension, not an operation performed by the supplied masking script. Use the supplied fixture to check labels and finite gradients; implement a matched concatenated-reference update separately before claiming accumulation equivalence.

## Practice

`labs/13_loss_masking.py` compares masked and unmasked token losses, excluding selected prompt and padding positions from training. It checks masking and finite gradients and writes trained-token counts and both losses.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_loss_masking/logs/%j.out" \
  --error="$PWD/results/13_loss_masking/logs/%j.err" \
  slurm/13_loss_masking.sbatch --workload small
```

## Check your results

Each new job owns `results/13_loss_masking/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/13_loss_masking/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/13_loss_masking/logs/$LAB_JOB_ID.out"
cat "results/13_loss_masking/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Expect seven trained and seven ignored shifted positions. Require the ignore-index and finite-gradient gates, then inspect masked and unmasked losses. The code permits absent gradients in this finite-value check; Lab 01 provides the stronger all-trainable-parameters gradient check.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Trained tokens | `trained_tokens` | `none` |
| Ignored tokens | `ignored_tokens` | `none` |
| Masked loss | `masked_loss` | `none` |
| Unmasked loss | `unmasked_loss` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 13_loss_masking \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the supplied two-example fixture before editing masks. The exact token counts provide a hand-checkable invariant; changing them starts a new fixture with a new expected count.

```bash
"$COURSE_PYTHON" labs/13_loss_masking.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_loss_masking/logs/%j.out" \
  --error="$PWD/results/13_loss_masking/logs/%j.err" slurm/13_loss_masking.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Mark each target position by hand and explain why shifting happens before interpreting prompt boundaries. Which denominator should a global per-token loss use? Explain why different masked/unmasked losses need not have a predictable ordering.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/13_loss_masking/logs/%j.out" \
  --error="$PWD/results/13_loss_masking/logs/%j.err" slurm/13_loss_masking.nsys.sbatch --workload small
```

The native Systems command is in `slurm/13_loss_masking.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Expand lab_workload, CUDA API and CUDA GPU rows. Locate forward, cross-entropy and backward launches; compare the trained/ignored token and finite-gradient evidence. The trace explains execution, not a masking speedup. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Off-by-one masking commonly supervises a prompt token or suppresses the first response target. Compare input and target columns explicitly. A batch with no valid targets needs deliberate handling rather than an undefined mean.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Supervision boundaries are part of the objective. Revisit sequence packing with this understanding of supervision boundaries. Correct loss labels alone are insufficient: attention must also prevent independent examples from reading one another's tokens.
