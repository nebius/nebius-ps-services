# Lab 13: Decide which tokens contribute to the training loss

Prompt text and padding may appear in a batch without being intended training targets. This lab constructs two short causal sequences and explicitly masks selected labels. You will count trained positions and compare masked with unmasked cross-entropy, learning that a loss mask chooses supervision while an attention mask controls which context an operation may read.

## Before you start

**Theory preparation:** Read Lesson 2 for shifted labels and masks and Lessons 3–4 for logits, cross-entropy, ignored-target normalization and backward. Only inspect labels in Lesson 2; run the full script after Lesson 4. Lesson 14 later applies the same distinction to SFT response supervision.

Use one H100 and the local tiny-model implementation. No tokenizer or external artifact is required. Review the one-position shift between input tokens and next-token labels.

## Concepts and code path

The program slices token sequences into inputs and shifted labels, assigns `-100` to prompt-target and padding positions, and computes vocabulary logits. Cross-entropy with `ignore_index=-100` excludes those targets from its mean. Backward then propagates the masked objective through the model. Ignoring a target does not erase that token's contextual contribution to later predictions.

## Practice

Given microbatches with 100 and 300 valid tokens, averaging their two mean losses gives the short batch half the weight instead of its correct one quarter: twice its intended batch contribution. Each token in that short batch receives three times the weight of a token in the longer batch. Change to sum both loss numerators and divide by 400 valid tokens before the equivalent gradient update. Expected observation: gradients match the concatenated reference within tolerance, then clipping and one AdamW step occur once. To check where the loss numerator comes from, take vocabulary probabilities [0.1, 0.7, 0.2]. A target at index 1 contributes -log(0.7) ≈ 0.3567; a target at index 2 contributes -log(0.2) ≈ 1.6094. Their token-mean loss is (0.3567 + 1.6094)/2 ≈ 0.9831. A third ignored position contributes neither numerator nor denominator. In PyTorch, cross_entropy with reduction='sum' supplies the numerator; divide by the count of nonignored targets, not padded tensor size. Skip or explicitly reject a globally empty target batch.

This is a hand calculation and a proposed complete-update extension, not an operation performed by the supplied masking script. Use the supplied fixture to check labels and finite gradients; implement a matched concatenated-reference update separately before claiming accumulation equivalence.

Run the supplied two-example fixture before editing masks. The exact token counts provide a hand-checkable invariant; changing them starts a new fixture with a new expected count.

```bash
umask 077
python labs/13_loss_masking.py --help
sbatch slurm/single_gpu.sbatch labs/13_loss_masking.py --profile smoke
```

## Check your results

Expect seven trained and seven ignored shifted positions. Require the ignore-index and finite-gradient gates, then inspect masked and unmasked losses. The code permits absent gradients in this finite-value check; Lab 01 provides the stronger all-trainable-parameters gradient check.

## Investigate the behavior

Mark each target position by hand and explain why shifting happens before interpreting prompt boundaries. Which denominator should a global per-token loss use? Explain why different masked/unmasked losses need not have a predictable ordering.

## If something goes wrong

Off-by-one masking commonly supervises a prompt token or suppresses the first response target. Compare input and target columns explicitly. A batch with no valid targets needs deliberate handling rather than an undefined mean.

## Takeaways and next step

Supervision boundaries are part of the objective. Revisit sequence packing with this understanding of supervision boundaries. Correct loss labels alone are insufficient: attention must also prevent independent examples from reading one another's tokens.
