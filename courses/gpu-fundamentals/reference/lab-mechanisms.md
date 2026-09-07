# Lab mechanisms and evidence

## Lab 11: distinguish inactive lanes from idle block slots

Objective: explain two different reasons why a GPU can have unused capacity.
The [scheduler lab](../labs/11_scheduler_tail.py) contains an executable
Python lane-work model and a separate measured H100 grid-tail experiment.
They answer different questions; neither substitutes for the other.

### Predict the lane-work model

Imagine 64 tasks. Half repeat the same loop body 20 times; half repeat it four
times. The useful work is `32 × 20 + 32 × 4 = 768` lane-iterations in either
ordering. A warp can stop issuing this common loop only after the lane with the
most iterations exits. With alternating long/short lanes, both warps issue 20 iterations.
That provides `2 × 20 × 32 = 1280` lane-opportunities, of which 768 are useful:
60 percent modeled utilization. With one all-long warp and one all-short warp,
the warp issue counts are 20 and four: 768 opportunities, all useful.

| Ordering | Useful lane-iterations | Warp loop iterations | Modeled utilization |
| --- | --- | --- | --- |
| Alternating 20,4 across two warps | 768 | 40 | 60% |
| One 20-iteration warp, one 4-iteration warp | 768 | 24 | 100% |

These are exact answers under the stated model, not measured H100 speedups.
This model applies to a common loop with per-lane exits. Two different branch
bodies generally require issuing each path under its own mask: their costs
cannot simply be replaced by the maximum loop length. Compilers can also
predicate short branches. Independent thread scheduling does not eliminate
these instruction and active-mask effects. See the official
[control-flow discussion](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/#control-flow).

### Run and interpret the GPU experiment

1. Predict the values above. After completing Lesson 6's occupancy explanation,
   run Lab 11 through the course single-GPU launcher as shown in the
   [smoke runbook](cluster-smoke-test.md).
2. Read `lane_work_model.mixed` and `lane_work_model.grouped`. They must have
   equal useful work; only modeled issue cost changes.
3. Separately inspect `cases`. The Triton GPU kernel performs uniform work
   per program while grid size crosses one and two estimated resident-slot
   boundaries. CUDA events measure these kernels, not the lane-work model.
4. Compare a full wave with one additional block. A nearly empty final wave
   can increase duration even though the work inside every block is uniform.
   Check all-program reference correctness before interpreting timing.
5. Confirm residency with compiler resource output and Nsight. The simple
   resource calculation is a planning estimate: register allocation granularity,
   achieved residency and scheduling can differ from the estimate.

Retain the modeled utilization, estimated resident slots, grid sizes, correct
output checks, and timing distributions in separate columns. Do not label the
model as branch efficiency or use the timing ratio as a measured divergence
penalty. In the Custom CUDA course, Lab 06 supplies real divergent and grouped
kernels, independent references, grouping-only timing, and end-to-end timing
including pack/scatter; it is an optional later follow-on, not a Fundamentals
prerequisite or a copied runtime dependency.

### Review and failure analysis

In a later experiment that actually groups GPU work, improved modeled
utilization can coexist with worse end-to-end time if grouping adds memory
traffic or leaves a few expensive blocks at the end. Fundamentals Lab 11 does
not measure grouping cost.
The model intentionally excludes grouping cost, memory stalls, compiler choices
and block scheduling. It supports a hypothesis, not an optimization decision.
If a tiny grid-tail difference disappears inside measurement variance, report
that result rather than manufacturing a cliff.

Review answer: improve lane similarity for the modeled loop; improve work
distribution or grid coverage for a tail wave. Raising occupancy alone proves
neither remedy. Use the diagram of SIMT masks for the first question and the
execution/tail diagram for the second.
