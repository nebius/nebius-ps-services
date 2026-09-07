# Lab mechanisms and evidence

## A causal comparison worksheet for every optimization

Objective: turn a plausible performance explanation into a controlled decision.
Use this procedure with the [library-first lab](../labs/16_library_first_decision.py)
and [tail/load-balance lab](../labs/15_tail_load_balance.py), then apply it to
the optimization capstone. The method also prepares you for the specialized
courses without duplicating their labs.

### Start with one testable mechanism

Write three statements before running anything: what stays fixed, what changes,
and what observation would contradict the proposed explanation. For launch
overhead, hold tensor shape, dtype, result, and iteration count fixed while
changing submission or fusion. For a memory-layout experiment, preserve logical
values and include layout conversion cost when the application must pay it.
For load imbalance, preserve total useful work and change its distribution.
In every case, decide whether the primary boundary is a kernel, a repeated
operation, or an application step.

| Hypothesis | Supporting evidence | Control or counterexample to examine |
| --- | --- | --- |
| Launch-bound pointwise chain | Gaps between short kernels; fewer launches after fusion | Larger tensors remove most of the gain |
| Memory traffic limits the region | Fewer intermediate writes and lower measured traffic | More arithmetic without less traffic does not help |
| Final wave wastes capacity | Few blocks remain while most SMs are idle | Comparable work with a better-balanced grid removes the tail |
| Register pressure limits latency hiding | Resource/stall evidence changes with launch choice | Higher occupancy with spills is slower |

Do not derive a hardware diagnosis from one aggregate number. Occupancy says
how much work can be resident, not whether it is eligible to issue. Bandwidth
depends on the memory level and traffic definition. Utilization may be modeled
or measured, and a high value can coexist with a poor end-to-end design.

### Worked decision

Given a 10-millisecond application step containing a 2-millisecond candidate
region, halving only that region saves at most one millisecond: the new step
is nine milliseconds, or about 1.11 times the original throughput. If the
optimization requires a 1.5-millisecond conversion each step, the total becomes
10.5 milliseconds and should be rejected under the original boundary. These
are arithmetic predictions, not H100 measurements. They show why the region
and application must both be measured.

1. Run the relevant smoke lab and validate the output before retaining timings.
2. Use warm-up plus repeated CUDA-event samples for the microbenchmark; retain
   minimum, median and tail statistics rather than one favorable number.
3. Inspect a short profiler region to test the mechanism. Keep profiling and
   primary timing runs separate because instrumentation changes execution.
4. Change only one factor. Repeat the comparison in counterbalanced order,
   holding work, correctness tolerances, environment, and data preparation fixed.
5. Record a keep, reject, or investigate decision in the
   [benchmark worksheet](benchmark-record.md). Include a slower candidate if it
   disproved the original hypothesis; that is a useful engineering result.

### Library-first escalation and scope

Check framework operators, compilation, tensor layout, and supported library
paths before proposing a custom kernel. A kernel is justified by a missing
operation or demonstrated limitation, not by a desire to increase a benchmark
number. The proof burden includes reference correctness, sanitizers, kernel
timing, total application impact, and maintenance across shapes and versions.

The Fundamentals lane model predicts inactive-lane opportunities; its GPU tail
probe measures a different scheduling effect. Training FLOP estimates count the
backward GEMMs actually requested. Inference scheduling models explain policy
but cannot supply real TTFT. CUDA logical arithmetic intensity excludes hidden
traffic. The general lesson is the same: name the numerator, denominator,
assumptions and excluded costs before interpreting any ratio.

Review answer: a locally faster kernel is a candidate, not an accepted change.
Accept it only when equivalent work is correct and the chosen end-to-end
boundary improves under the stated constraints. If a result conflicts with the
model, refine the model using evidence instead of discarding the measurement.
