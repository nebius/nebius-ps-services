# Lab 17: Compare greedy and seeded stochastic generation

Generation settings change both output behavior and the amount of work performed, so a performance comparison must declare them. This lab compares greedy decoding with a seeded stochastic profile on a pinned model. You will verify repeatability within each profile and inspect output lengths, while avoiding the mistaken conclusion that different sampling profiles are a controlled one-factor optimization.

## Before you start

**Theory preparation:** Read Lessons 1–3 for artifacts, the autoregressive loop, logits/softmax, greedy decoding, temperature, top-k/top-p, seeds and stopping. Explain the declared order of sampling controls before comparing deterministic and stochastic profiles.

Audit the model and tokenizer revision and use one H100 in the mechanics environment. Review temperature, top-k, top-p, EOS, and maximum-new-token behavior. A fixed seed applies to a declared environment, not every possible implementation.

Sampling kernels are usually not the dominant H100 compute, but host transfers, synchronization, or inefficient per-request processing can become visible at high throughput.

## Concepts and code path

The source tokenizes a fixed prompt and runs repeated greedy and seeded stochastic generations. It compares token-ID sequences within each profile, records elapsed time and generated counts, and requires nonempty outputs. The stochastic profile changes multiple controls together; the code does not implement an independent sweep of each sampling parameter.

## Practice

Given a baseline producing 200 tokens in 2 seconds and a candidate producing 100 after a changed stop ID in 1.2 seconds, completion latency improves while output rate falls from 100 to about 83 tokens/s and semantics differ. Change to the same stop/sampling profile. Expected observation: a fixed-work comparison matches token counts and stopping behavior. A representative stochastic comparison instead fixes the decoding policy and compares length/finish distributions, quality, and token-normalized performance; individual samples need not be identical. For a separate sampling example, probabilities [0.50, 0.30, 0.15, 0.05] give greedy token A. Top-k=2 keeps A and B and renormalizes to [0.625, 0.375]. Top-p=0.9 applied to the original distribution keeps A, B, and C (cumulative 0.95), then renormalizes by 0.95. Temperature acts on logits before these filters; at T=0.5 the probabilities are proportional to the squared original probabilities, giving approximately [0.685, 0.247, 0.062, 0.007]. These are separate controlled examples, not the result of applying every setting at once.

Run Lab 17 and compare greedy and seeded stochastic profiles without mixing their conclusions. Lab 17 compares profiles with multiple settings. For causal interpretation, extend the experiment by varying only temperature, then only top-k, then only top-p from an otherwise fixed profile; repeat enough samples to inspect candidate frequencies, lengths, and finish reasons.

Run the supplied profiles under one output budget. Changing the maximum budget is a separate experiment and may not change actual length if an earlier stopping condition is reached.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/17_sampling_semantics.py --profile smoke --max-new-tokens 32
```

## Check your results

Require greedy repeatability, seeded-sampling repeatability, and nonempty outputs. Inspect generated token counts, token IDs, and elapsed time for each profile. These are bounded behavior checks, not a statistical distribution or quality evaluation.

Retain generation settings, seeds, stop reasons, token counts, outputs or reviewed hashes, and quality checks.

A speedup is invalid if it changes the accepted decoding or quality contract.

## Investigate the behavior

For candidate probabilities 0.6, 0.3, and 0.1, explain which survive top-k of two and a nucleus threshold of 0.8. Why can shorter sampled output make a run finish earlier without improving per-token execution?

Deterministic tests are reproducible but may not represent production diversity. Stochastic tests represent behavior better but require more samples and quality statistics. Forcing fixed output length simplifies performance analysis while changing real stop behavior.

## If something goes wrong

Repeatability failures require inspecting seed placement, artifact identity, and nondeterministic execution. Do not compare text alone when tokenization differs. A maximum budget is an upper bound, not a guaranteed output count.

Avoid comparing requests with different output lengths using only requests per second.

## Takeaways and next step

Fix sampling and stopping semantics before timing comparisons. Extension: vary temperature, top-k, and top-p one at a time across many seeds, and examine candidate frequencies, output lengths, and task quality separately.

Fix semantics and report both request and token-normalized work.

State when deterministic equality is required and when distributional evaluation is appropriate.
