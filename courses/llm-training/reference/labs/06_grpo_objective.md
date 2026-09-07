# Lab 06: Work through a group-relative policy objective

GRPO uses rewards from multiple completions of the same prompt to construct a relative training signal. This lab isolates the objective on small GPU tensors without loading a language model. You will inspect advantages, probability ratios, clipping, and a reference-policy penalty so the later trainer's loss has a concrete mathematical meaning.

## Before you start

**Theory preparation:** Read Lesson 15 for grouped rewards, advantages, old/reference policies, log-probability ratios, the clipped surrogate and the sampled KL penalty. Lessons 3–4 supply loss differentiation. Lesson 1 is a preview; run this objective calculation before the trainer in Lab 07.

Use one H100 and review log probabilities, gradients, and the GRPO calculation in Practice below. A group must contain enough completions to define a useful relative signal; `--group-size` controls this dimension.

H100 accelerates policy inference and training differently. Variable decode length creates rollout stragglers, while trainer GEMMs prefer dense packed tokens; rate matching between the two stages matters.

## Concepts and code path

The code creates reward groups and old, new, and reference log-probability tensors. It normalizes rewards within each group, forms the new-to-old probability ratio, applies a clipped surrogate, adds an approximate KL-related penalty, and differentiates the loss with respect to new log probabilities. There is no rollout engine, tokenizer, language model, or optimizer update here.

## Practice

Given four rewards `[1, 1, 2, 4]`, the mean is 2 and centered advantages are `[-1,-1,0,2]`; normalize only with a declared epsilon and deviation rule. Change to `[3,3,3,3]`. Expected observation: zero variance produces zero or otherwise explicitly handled advantages, not NaNs; the report also shows rollout and reward time before claiming trainer optimization. For a separate clipping calculation with epsilon=0.2 and A=+1, ratio=1.5 gives min(1.5,1.2)=1.2: increasing the already-favored action beyond the upper clip adds no surrogate reward. With A=-1 and ratio=0.5, min(-0.5,-0.8)=-0.8: decreasing the disfavored action below the lower clip likewise stops improving this term. Opposite-direction changes can remain unclipped. Walk through these terms before interpreting gradients or the optional reference penalty.

Run Labs 06 and 07 and verify finite gradients before measuring rollout or trainer time. Lab 07's controlled alternating reward exists only to prove trainer, gradient, and update plumbing; it is not a model-quality reward or a learning-quality benchmark.

Run the default group size, then change only that size to inspect the group statistics. This is an objective mechanics exercise, not a throughput benchmark of a GRPO system.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile smoke --group-size 4
```

## Check your results

Require zero-mean group advantages within the implemented tolerance and finite gradients. Inspect `loss`, `mean_approximate_kl`, and `max_group_advantage_mean_error`. These checks do not establish that rewards represent quality or that a learned policy improves.

Record candidate groups, rewards, normalized advantages, ratios, clipping, KL term, gradients, and policy version.

Generation often dominates elapsed time, while the objective determines whether the update is meaningful. A synthetic reward can validate mechanics but cannot support a policy-quality claim.

## Investigate the behavior

Work out how a completion with above-group-average reward affects the surrogate when its probability ratio grows. Explain why clipping differs from simply clipping rewards, and why identical rewards within a group provide no relative preference.

Larger groups improve relative comparison but multiply rollout cost. Disaggregation scales components independently but transfers weights and can increase policy staleness. Aggressive reward optimization can exploit verifier weaknesses rather than improve the intended task.

## If something goes wrong

Non-finite values can arise from invalid scaling or extreme probability ratios. Inspect group variance and the objective terms before changing thresholds. Do not interpret a scalar loss in isolation from its constituent terms.

Avoid using stale rollout weights without recording policy-version lag.

## Takeaways and next step

The objective converts relative reward into a constrained update signal. Next, run Lab 07 to connect generation, reward evaluation, adapters, and trainer updates while preserving the distinction between plumbing checks and quality evaluation.

Separate rollout, scoring, synchronization, and update phases and validate the objective first.

Trace one response from generation through reward to parameter update.
