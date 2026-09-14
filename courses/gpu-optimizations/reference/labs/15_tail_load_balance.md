# Lab 15: Distinguish task imbalance from partial grid waves

A long final task can delay completion even when most work has finished, while a partial grid wave can leave SM capacity unused. This lab explores those effects separately with concurrent GEMMs and a Triton wave probe. You will learn why approximate work matching and a scheduling model are useful for diagnosis but insufficient for a fixed-work optimization claim.

## Before you start

**Theory preparation:** Read Lesson 10 and Fundamentals Lessons 3, 5 and 6 for grid waves, occupancy, divergence versus skew and completion tails. Lesson 2 explains how to time multiple streams by waiting for all measured work before the end event. Report the modeled work ratio when comparing the balanced and skewed schedules. Fundamentals Lab 11 explains the NaN output sentinel used to detect unwritten results before reference comparison.

Use one H100 with the course-qualified Triton compiler. Review task makespan—the time until all tasks finish—and the difference between task imbalance, warp divergence, and a grid tail.

Calculate waves from the measured SM90 resource-limited residency, not only total SM count. Persistent or library kernels can have scheduling behavior that a simple block-wave model does not fully describe.

## Concepts and code path

Eight streams execute balanced or skewed GEMM sizes. The script reports task durations, joined makespan, and modeled FLOPs. Work is matched within two percent, not exactly, and matrices differ. A separate Triton probe varies block counts around estimated resident capacity and validates every program's output. This repeats Fundamentals Lab 11's wave probe as an explicit control: its purpose here is to distinguish a uniform-grid tail from the new balanced-versus-skewed eight-stream workload. Reuse the earlier wave model rather than treating it as a new technique. Neither part is a direct divergent-branch experiment.

## Practice

Given capacity for 240 concurrent blocks, 241 uniform blocks require a nearly empty second wave. Change the grid to 480 smaller blocks while preserving the result and the same total useful work, if the algorithm permits. Expected observation: under the same 240-block residency limit, the grid fills two waves; whether it finishes sooner still depends on per-block overhead and actual scheduling. A divergent-warp control would show different active-lane evidence.

Run Lab 15's balanced/skewed concurrent task survey and separate partial-wave probe. The task sets have modeled GEMM work matched within two percent, not identical matrices or exact work. The grid probe varies total blocks. Keep those limits with the timing and do not describe either comparison as a fixed-work regrouping speedup.

Run both task sets and the wave probe together. Keep the declared work ratio in your report rather than describing the candidate as identical useful work.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/15_tail_load_balance.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/15_tail_load_balance.py --profile h100
```

## Check your results

Require the work-ratio gate, finite task outputs, and reference-matching partial-wave outputs. Inspect `makespan_distribution`, `task_duration_distribution`, `skewed_to_balanced_modeled_work_ratio`, and `partial_wave_probe`. GEMM finiteness alone is not numerical reference equivalence.

Record per-unit work, completion distribution, wave count, p50/p90/p99, and total time. The supplied summary does not report p99; estimate that tail only from a separately designed collection with enough observations.

Fix the specific imbalance owner: data partition, task grouping, launch geometry, or synchronization.

## Investigate the behavior

Compare the longest task with the median task and the joined completion time. Does the tail model predict the observed transition? Use a profiler to confirm actual concurrent execution and residency before interpreting the model as hardware fact.

Sorting/grouping work reduces divergence but adds preprocessing and can hurt locality. Splitting heavy tasks improves balance but adds launches/atomics. Padding a grid adds useless work. Dynamic scheduling improves balance with queue-management cost.

## If something goes wrong

A single stream occupying most resources can make apparent concurrency ineffective. Missing resource metadata can leave an optimistic estimate: the code retains the thread-limit bound and omits unavailable register/shared-memory constraints. Independent confirmation is then required. Changing total blocks changes total probe work; do not normalize it away without explaining the model.

Increasing occupancy cannot fix one rank receiving twice the work.

## Takeaways and next step

This is a controlled diagnostic survey, not proof of a causal regrouping speedup. An extension should preserve exact inputs and total useful work while changing only scheduling, then include any packing or reordering overhead.

Locate the level where work becomes uneven and rebalance before tuning lower-level resources.

Give separate remedies for warp divergence, a grid tail, and rank skew.
