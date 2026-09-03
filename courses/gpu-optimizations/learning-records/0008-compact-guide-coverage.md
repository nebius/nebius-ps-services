# Compact-guide coverage decision

## Decision

Keep the course's existing evidence-first sequence and add only the tool scopes
that the compact source exposed as underdeveloped. The existing bottleneck,
profiler, roofline, optimization, and paired-pathology lessons already provide
more depth than the source and should not be duplicated.

## Additions

- DCGM Exporter is distinguished from interactive DCGM and kernel profilers.
- `nvbandwidth` is used for copy paths, while NCCL Tests isolate named
  collectives and message sizes.
- DDP, sharded training, tensor parallelism, and expert routing are mapped to
  the relevant collective tests before application profiling.
- vLLM Bench, endpoint load clients, and MLPerf are separated by benchmark
  scope and reproducibility contract.

These utilities remain site- or environment-owned. The portable course checks
their availability but does not bundle binaries, weaken counter permissions,
or represent an informal lab as a standards-compliant result.
