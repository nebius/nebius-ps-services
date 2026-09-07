# Lab 11: Separate lane utilization from grid-tail behavior

Uneven work can waste execution capacity at more than one level. This lab deliberately separates a Python model of loop work within warps from an H100 probe of partial grid waves. You will learn which conclusions follow from exact work accounting and which require hardware scheduling evidence rather than a suggestive diagram.

## Before you start

**Theory preparation:** Read Lessons 3, 5 and 6 for the execution hierarchy, lane-mask work, occupancy, partial grid waves and output-sentinel validation. Earlier visits inspect code and the lane model only. Run the combined script after Lesson 6 so its separate GPU-tail experiment has a theoretical basis.

Use the Fundamentals environment with Triton on one H100. In Lesson 5, inspect the lane-work model and work through its arithmetic; the supplied launch runs both parts, not a lane-only mode. After Lesson 6 explains SM residency, run the full lab. Review warp width, SM residency, and the [worked mechanism guide](../lab-mechanisms.md). The two parts answer different questions and must not be combined into one speedup claim.

H100 schedulers choose among eligible warps on each SMSP. A warp with many inactive lanes can still consume an issue slot; a warp stalled on memory is a different condition and can be hidden by another eligible warp.

## Concepts and code path

The Python model groups fixed lane iteration counts and estimates issued warp iterations. Its mixed and regrouped arrangements preserve useful lane work. Separately, the Triton probe derives an estimated resident-block capacity from resource information, launches grids around wave boundaries, and checks each program's output. Grid cases vary block count: work per program is fixed, but total work is not.

## Practice

Given 64 tasks: 32 need a 20-instruction branch body and 32 need a separate 4-instruction body. Initially, each of two warps mixes 16 tasks of each kind, so each warp issues both bodies: 2 × (20 + 4) = 48 modeled warp instructions. Change the grouping of the same tasks into one all-long warp and one all-short warp: 20 + 4 = 24. Expected observation: useful task work is unchanged; sorting and restoring output order add costs. This simplified separate-branch model is not a measured speedup and differs from Lab 11's masked common-loop model.

Inspect Lab 11's Python lane-work model and calculate mixed versus uniform-per-warp loop work, then inspect the inputs to its separate H100 grid-tail probe without interpreting residency yet. The script runs both parts; launch it after the occupancy explanation in Lesson 6. For 64 lanes with half doing 20 iterations and half doing four, both arrangements perform 768 useful lane-iterations; alternating lanes model 60 percent utilization while grouping by warp models 100 percent. This common-loop model is not a GPU branch benchmark and does not model two distinct branch bodies. Follow the [worked lab procedure](../lab-mechanisms.md) before interpreting the measured tail cases. Real divergent/grouped CUDA kernels, including packing and scattering costs, are taught in Custom Kernels Lab 06 as an optional later extension.

Run the complete smoke case first and retain both the modeled lane results and measured grid cases. A second profile is an additional workload, not a fixed-work replacement.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/11_scheduler_tail.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/11_scheduler_tail.py --profile h100
```

## Check your results

Require matching grid outputs and conserved useful work in the lane comparison. The NaN prefill explained in Lesson 6 must be replaced at every expected output; finite checks detect surviving sentinels, and the independent reference checks the values written. Inspect `lane_work_model`, `resident_block_slots`, each case's `model`, and timing. `profiler_confirmation_required` explicitly limits claims about actual residency and scheduling.

Retain modeled lane utilization separately from measured kernel timing and the grid-wave calculation. Actual branch efficiency or active-thread counters require profiling a real divergent kernel; the uniform Triton tail probe cannot establish that result.

Reordering helps only when it groups similar control flow without adding more movement than it saves.

## Investigate the behavior

Explain why adding one block beyond an estimated full wave can create a nearly empty final wave. Then explain why dividing unlike-total-work timings is not a valid optimization speedup. Recalculate the 768 useful lane-iterations example independently.

Reordering data to group similar branches can improve lane utilization but costs preprocessing, changes locality, and may create load imbalance elsewhere. Branchless arithmetic can execute extra work and is not automatically faster.

## If something goes wrong

Missing compiler resource data can leave an optimistic estimate: the program retains the thread-limit bound and omits unavailable register/shared-memory constraints. Do not treat that incomplete estimate as established residency; obtain independent resource/profiler confirmation. A mismatch near a grid boundary suggests indexing or tail handling, which must be fixed before performance analysis.

Calling every long tail “warp divergence” hides queueing, block imbalance, or insufficient grid waves.

## Takeaways and next step

Models clarify mechanisms when their assumptions are explicit. This lab does not benchmark divergent CUDA branches. The Custom Kernels course provides actual divergent/grouped work where regrouping and restoration costs can also be measured.

Identify whether unused execution comes from lane masks, block scheduling, or the final partial wave.

Give one fix for divergence and one different fix for a tail wave.
