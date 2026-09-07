# Lab 23: Trace speculative acceptance, rejection, and recovery

Speculative decoding proposes several tokens with a cheaper draft and asks the target to verify them. This lab uses a small synthetic greedy target to make acceptance and recovery visible. You will learn why accepting a long prefix can reduce target calls and why draft overhead can erase that benefit when proposals are frequently rejected.

## Before you start

**Theory preparation:** Read Lesson 13 for first-order target transitions, draft acceptance, rejection recovery, bonus tokens and greedy sequence equivalence. Lessons 1 and 3 supply fixed-parameter generation and sampling, while Fundamentals Lesson 2 defines matrix/GELU operations. Measure all speculative work, not acceptance alone.

Use one H100 in the mechanics environment. No external target/draft model is required. The example is a first-order synthetic greedy process, not a transformer engine or proof of stochastic speculative-distribution equivalence.

The target H100 may already batch decode efficiently. A weak draft can consume GPU capacity or host orchestration and reduce throughput at higher concurrency, so real-engine A/B trials are required.

## Concepts and code path

The code builds a target-only sequence and two speculative cases with deliberately high and low acceptance. It proposes a draft block, verifies the accepted prefix, emits a target recovery token at the first rejection, or emits a target bonus token after full acceptance. It records target calls, proposed/accepted tokens, timing, and memory while requiring the same final greedy sequence.

## Practice

Given draft proposals `[A,B,C,D]` where the target accepts `A,B` and rejects `C`, keep the two-token prefix, generate the algorithm’s corrected recovery token, and discard `D`. Change to all four accepted. Expected observation: the target may provide a bonus token, but speedup exists only if draft plus verification cost is below the saved target calls and output semantics match.

Run Lab 23 to inspect acceptance patterns, then run `slurm/vllm_speculative_ab.sbatch` with explicit target and draft artifacts. The live launcher performs three independent engine restarts per variant, alternates order, fails if paired greedy-output digests differ, and captures bounded AIPerf profiles.

Compare draft lengths under the same output budget. The two acceptance controls are supplied by the lab and must both remain in the report.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/23_speculative_decoding.py --profile smoke --draft-length 4 --max-new-tokens 32
sbatch slurm/single_gpu.sbatch labs/23_speculative_decoding.py --profile smoke --draft-length 8 --max-new-tokens 32
```

## Check your results

Require exact target-greedy output in both speculative paths, target-call reduction in the high-acceptance case, and the declared low-acceptance control behavior. Inspect accepted-prefix histograms, bonus/recovery counts, target calls, timings, and peak allocation.

For the separate live-engine campaign, record target/draft revisions, proposal length, acceptance metrics exposed by the engine, target calls, output digests, TTFT, ITL/TPOT, throughput, and failures. Raw generated text remains private.

High acceptance is useful only when the draft and verification path improve end-to-end service metrics.

## Investigate the behavior

Work through one partially accepted proposal and identify the first token that must be discarded. Why can a longer draft increase wasted work? Compare saved target calls with measured total duration rather than equating the two.

Larger `k` offers more target-call savings when acceptance is high but wastes more draft work on rejection. A stronger draft improves acceptance while costing more. Self-speculative methods reduce extra weights but have different support and tuning.

## If something goes wrong

A sequence mismatch often indicates incorrect recovery or bonus handling. Check output-budget boundaries and accepted-prefix indexing before timing. A slower speculative result is valid evidence when proposal/verification costs dominate.

Avoid comparing greedy outputs while claiming correctness for stochastic sampling.

## Takeaways and next step

Acceptance is a mechanism, not a speedup guarantee. Optional Lab 33 applies real-engine paired greedy checks and independent benchmark trials after target/draft artifact and environment qualification.

Validate the algorithm's sampling semantics and measure the complete path.

Explain how acceptance rate and draft cost interact.
