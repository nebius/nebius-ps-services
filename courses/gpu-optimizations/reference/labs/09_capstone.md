# Lab 09: Build a causal compilation experiment

This capstone turns a promising optimization into a controlled comparison. You will evaluate eager versus compiled execution for the same BF16 matmul-and-activation pipeline, preserving shapes, inputs, residency, and mathematical work. The intended deliverable is a defensible conclusion—including an inconclusive or slower candidate—not a predetermined speedup.

## Before you start

**Theory preparation:** Read Lessons 1–5 for matched inputs, numerical checks, counterbalanced timing, SiLU/tanh, compilation and launch overhead, then Lesson 13 for a scoped keep/reject decision. Use independent process trials in addition to the script’s within-process rounds.

Complete timing, compilation, and profiler labs on one H100. Prepare a benchmark worksheet that names the independent variable and acceptance metric. The compiler path uses `fullgraph=True`: the requested function must be captured as one compiler graph, and a graph break that prevents this capture raises an error. One compiler graph can still produce several GPU kernels. Compilation must succeed before any candidate timing is accepted.

## Concepts and code path

SiLU (sigmoid linear unit) gates each input by its sigmoid: `x / (1 + exp(-x))`. The sigmoid is a smooth function between zero and one. The hyperbolic tangent, `tanh`, maps finite real inputs smoothly between -1 and 1. Both activations act elementwise here.

The workload is matmul followed by SiLU, scalar addition, and tanh. Both variants reuse the same resident inputs. Compilation occurs before measured rounds. Three rounds alternate variant order and collect warmed CUDA-event distributions plus synchronized wall distributions. These rounds share one process and compiler state; they are not three independent fresh-process trials.

## Practice

Run the smoke capstone first. For an acceptance campaign, submit at least three separate jobs and keep all results, including unfavorable runs. Do not compare a fresh baseline with a selectively warmed candidate.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/09_capstone.py --profile smoke
```

## Check your results

Require BF16 output agreement at `rtol=1e-2, atol=1e-2` in every round. Inspect `controlled_factors`, per-round `execution_order`, CUDA/wall distributions, and the observed ratios. A median ratio above one is a local observation, not a general compiler guarantee.

## Investigate the behavior

Does the result survive order reversal and separate processes? Use a focused profile to explain changes in launches or intermediate traffic. Reconcile any difference between device-only and synchronized application-boundary conclusions.

## If something goes wrong

If full-graph compilation fails, the candidate is unavailable rather than silently equivalent to eager. If ratios fluctuate around one, report uncertainty and gather repeated evidence instead of selecting the best round.

## Takeaways and next step

A causal report combines controlled work, correctness, repeated observations, and a mechanism. Use Lab 16's escalation decision to determine whether the remaining hotspot warrants a library change or a custom-kernel investigation.
