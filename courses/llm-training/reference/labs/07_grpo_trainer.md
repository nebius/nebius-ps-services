# Lab 07: Verify the GRPO generation-to-update loop

An objective formula is only one part of reward-guided training: the system must generate completions, score them, compute a loss, propagate gradients, and update parameters. This lab exercises that loop with a small pinned model and LoRA adapters. Its deliberately artificial reward guarantees a test signal, allowing you to validate plumbing without pretending to measure learning quality.

## Before you start

**Theory preparation:** Read Lessons 14–15 for LoRA, rollouts, grouped rewards and policy updates. Complete Lab 06 first. The callback and update checks are explained below.

Qualify Transformers, PEFT, TRL, datasets, and the approved immutable model artifact in the Training environment. Use one H100 and private output storage. Complete Lab 06 before interpreting the trainer loss.

## Concepts and code path

TRL (Transformer Reinforcement Learning) supplies a GRPO trainer that connects generation, rewards and updates using the pinned model/tokenizer and LoRA configuration. A small local prompt dataset produces completion pairs. The callback assigns alternating zero and one rewards, regardless of text quality, to create a controlled within-group signal. Adapter hooks observe gradients and saved parameter copies reveal actual updates. Require finite nonzero gradients and a parameter change; these checks establish the update path, not reward quality.

## Practice

Inspect model and step options, then run a bounded three-step smoke trial. Each run needs a new private trainer-output directory; existing output reuse is rejected intentionally.

```bash
umask 077
python labs/07_grpo_trainer.py --help
sbatch slurm/single_gpu.sbatch labs/07_grpo_trainer.py --profile smoke --steps 3
```

## Check your results

Require trainer completion, finite loss, nonzero within-group reward variance, finite nonzero adapter gradients, and a nonzero adapter update. Inspect runtime and gradient/update fields. None of these gates certifies completion quality.

## Investigate the behavior

Trace one pair through generation, scoring, relative advantage, and update. Why would a constant reward hide a broken or inactive learning signal? Which additional components would dominate cost in a real rollout-heavy training system?

## If something goes wrong

Zero reward variance invalidates the plumbing proof, as do zero adapter gradients. Missing package APIs block environment qualification. Do not substitute a different trainer or remove the gradient checks simply to finish the run.

## Takeaways and next step

A systems smoke test should have a controlled signal and observable state transition. A meaningful extension replaces the artificial reward with a reviewed task criterion and adds held-out evaluation, while retaining the existing plumbing gates.
