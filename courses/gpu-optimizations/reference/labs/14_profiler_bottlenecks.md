# Lab 14: Diagnose synchronization, launch, memory, and compute limits

This workshop supplies controlled examples of four bottleneck classes so you can practice choosing the right evidence. Instead of opening a profiler and guessing, you will select one case, compare baseline and candidate, and explain the causal change. Each case has a different mechanism; a single metric such as utilization cannot diagnose all four.

## Before you start

**Theory preparation:** Read Lessons 2–3 before the synchronization case. Preview the other cases by reading their descriptions and code. Run the launch case after Lesson 4 explains compilation, the memory case after Lesson 7 explains traffic and the compute case after Lesson 9 explains precision/dispatch. Complete those lessons before using the full casebook as optimization evidence.

Complete [tooling setup](../tooling-setup.md) and inspect the [runbook's profiler workshop](../cluster-smoke-test.md). Confirm supported Nsight sets/sections on the actual compute node. Keep reports private and use one profiler per run.

At the first profiler lesson, use the synchronization case and recognize the
other bottleneck patterns without adopting their interventions yet. Return to
launch, memory and compute cases after the corresponding local-optimization
lessons; use the complete casebook before the capstone.

## Concepts and code path

The script selects `sync`, `launch`, `memory`, or `compute`, then constructs the chosen baseline or optimized callable and its reference. It warms execution, records unprofiled timing, exposes the `profile_region` NVTX range, then checks numerical equivalence before writing a result. Reject the collected timing if that check fails. The compute candidate changes precision under a declared error gate; the pointwise memory case has no portable algorithmic FLOP count. The synchronization case deliberately reuses Lab 02's immediate-versus-deferred scalar retrieval as a known control; the new task is locating host waits in an NVTX-scoped trace. The memory case reuses Lab 03's pointwise expression; Lab 03 teaches compilation cost, while this workshop asks whether a selected kernel's measured traffic supports a memory-bound diagnosis. Do not count rerunning these controls as discovering two new optimizations.

## Practice

Begin with one paired case. These commands separate acceptance timing from a system timeline; repeat the same pattern for other supported cases using the runbook's focused profiler instructions.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode baseline
sbatch slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode optimized
sbatch slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile smoke --case sync --mode baseline
```

## Check your results

Require `numerically_equivalent` and `finite_output`. Inspect `case`, `mode`, timing, workload details, and the selected range. A profile from another case cannot explain this pair's result.

## Investigate the behavior

For synchronization, locate host waits; for launches, kernel spacing; for memory, traffic and access efficiency; for compute, the selected arithmetic path. Distinguish logical byte estimates from measured transactions and compare the exact selected kernel.

## If something goes wrong

Unsupported profiler sections or permissions are missing evidence, not zero stalls. Compilation and correctness failures disqualify the candidate. Avoid nesting profilers or using their perturbed timings to manufacture an apparent improvement.

## Takeaways and next step

Write one hypothesis, one observed mechanism, and one bounded conclusion per case. Apply the same sequence to a real hotspot only after establishing its workload and correctness contract.
