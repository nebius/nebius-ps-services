# Foundations-first redesign

## Decision

Begin with the user-visible critical path, limiter families, capacity as a
separate feasibility constraint, and a falsifiable experiment contract. Teach
the profiler ladder only after those foundations, then organize techniques by
the limiter they can change. End with fixed-work distributed analysis and an
explicit four-gate keep/reject decision.

## Rationale

A catalog of tuning techniques encourages premature changes. Critical-path,
Amdahl-style, and roofline reasoning help the learner choose the next control,
while stability controls and disconfirming tests prevent a utilization number
or profiler screenshot from becoming a diagnosis. Kernel geometry, residency,
wave tails, divergence, memory access, register pressure, and spills now have a
dedicated bridge between operator-level and profiler-level reasoning.

## Evidence

The portable page now contains 15 ordered lessons and nine accessible SVG
diagrams. The contents, syllabus, course map, mission, source-coverage map, and
validator use the same three-part progression. The validator passes, and
browser inspection found no oversized arrow markers, box-text overflow, page
overflow, or figure-container escape at desktop and 390-pixel viewport widths.

## Boundary

The course provides runnable mechanisms and static contracts, not measured
H100 speedups. Profiler access, clocks and thermals, CUDA behavior, distributed
overlap, NCCL topology, numerical acceptance, and performance remain live
cluster gates.
