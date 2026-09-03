# Diagnostic tooling workshop

## Decision

Teach the five primary bottleneck classes before optimization techniques, then add an early tool sequence that moves from framework attribution to system timelines, selected-kernel counters, cluster telemetry, and serving-load evidence.

## Rationale

The previous tool ladder was too compressed to teach installation ownership, artifact scope, or a complete profiler workflow. It also presented transfer, synchronization, and capacity as peer bottleneck classes, which obscured the source taxonomy and made tool selection less precise.

## Practice impact

Lab 14 now exposes synchronization, launch, memory-traffic, and compute mechanisms with stable NVTX ranges. Generic Slurm launchers produce focused Nsight Systems and Nsight Compute reports without injecting lab-specific arguments. Learners preserve unprofiled timing separately from instrumented evidence.

## Evidence boundary

Commands, syntax, source parity, and public documentation can be verified offline. H100 timing, profiler-counter access, DCGM fields, report contents, and any speedup remain live gates for the future two-node cluster.
