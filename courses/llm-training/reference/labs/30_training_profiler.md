# Lab 30: Inspect the operators in a tiny training step

A training-step profile helps connect forward, loss, backward, and optimizer work to expensive operators. This lab captures a bounded tiny-model step and emits a short operator summary. You will use that evidence to form a hypothesis for the capstone while recognizing that one cold profiled step is neither a steady-state benchmark nor a complete utilization analysis.

## Before you start

**Theory preparation:** Read Lesson 10 for bounded profiling, self versus inclusive attribution and instrumentation overhead, after Lessons 3–4 establish the tiny transformer’s update. Revisit the profiling method in Lesson 16; this workload’s hotspot is not automatically the capstone’s hotspot.

Use one H100 with functioning PyTorch CPU/CUDA profiling in the Training environment. Keep raw diagnostic artifacts private. Understand the unprofiled training loop before interpreting aggregated operator entries. First run this in Lesson 10: reuse Lesson 4's update knowledge and focus on attribution, not relearning backward. In Lesson 16, bring the retained profile as a hypothesis source and collect matched evidence for the capstone's own workload.

## Concepts and code path

The program constructs the tiny model and batch, profiles one training step, and sorts aggregated events by self-device time. It publishes a bounded list rather than a raw trace. The listed device/CPU durations help locate work, but inclusive durations can overlap or nest; summing arbitrary rows does not reconstruct elapsed step time.

`--seed` initializes the random generators before constructing the model and
batch. Reuse it to hold those inputs fixed across profiles; it does not make
device timing deterministic.

## Practice

Capture the smoke profile first and identify one plausible bottleneck. Do not change several model or precision settings at once while collecting the evidence intended to explain a baseline.

```bash
umask 077
python labs/30_training_profiler.py --help
sbatch slurm/single_gpu.sbatch labs/30_training_profiler.py --profile smoke
```

## Check your results

Confirm finite loss and inspect `top_operators` and `top_operator_sort`. The check does not compare full gradients or updates. No Chrome trace, warmed step-time distribution, or complete model FLOP utilization is emitted by this lab.

## Investigate the behavior

Map one expensive operator to forward or backward work in the model. Does the table suggest a large arithmetic kernel or many small operations? What timeline evidence would you need to distinguish launch starvation from device execution time?

## If something goes wrong

Missing CUDA activity invalidates device conclusions even if CPU events appear. First-use initialization can dominate this cold sample. Preserve it as diagnostic evidence and collect a warmed unprofiled baseline separately.

## Takeaways and next step

A profile proposes a causal hypothesis; it does not prove a speedup. Use Lab 31's controlled update comparison and independent trials, adding a focused profile of the actual candidate workload when explaining its result.
