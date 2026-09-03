# Mission

## Learner

This course is for software engineers, ML engineers, and platform engineers who can read Python but do not yet have a dependable mental model for GPU performance. It assumes no CUDA C++ experience.

## Capability after the course

Given a PyTorch workload on the course cluster, the learner can explain where work and data move, measure the correct execution boundary, distinguish a plausible bottleneck from proof, and select the next experiment without relying on GPU-utilization folklore.

## Learning outcomes

By the end, the learner can:

1. Contrast CPU latency-oriented execution with GPU throughput-oriented execution.
2. Trace the logical H100 hierarchy from HBM and memory controllers through L2, GPCs, TPCs, SMs, and the four SM scheduler/execution partitions without confusing a teaching diagram with a physical floorplan.
3. Trace a PyTorch operation through launch, grid, block, warp, SM assignment, memory demand, and arithmetic resources.
4. Explain SIMT execution, divergence, occupancy, latency hiding, waves, and why none is a standalone performance score.
5. Relate HBM, L2, the unified L1/shared-memory resource, registers, PCIe, CUDA Cores, and Tensor Cores to observable costs and ownership scopes.
6. Measure asynchronous CUDA work with synchronization and CUDA events placed at a declared boundary.
7. Classify launch, transfer, memory/layout, compute/shape, input, capacity, and communication symptoms as hypotheses that require disconfirming controls.
8. Test precision, layout, transfer, arithmetic-intensity, and collective hypotheses with correctness gates.
9. Run one-GPU isolation studies and a two-node NCCL study through Slurm.
10. Produce a benchmark record that another learner can interpret and reproduce.

## Runtime contract

- Slurm cluster with exactly two worker nodes.
- Exactly one full, non-MIG NVIDIA H100 per node.
- PyTorch code runs inside Slurm allocations.
- Distributed labs use `torchrun` with two nodes and one process per node.
- Single-device microbenchmarks reserve one node through Slurm to isolate a local GPU effect.
- CUDA, driver, fabric, and software versions must be recorded with results.

## Non-goals

This is not a CUDA C++ programming course, a complete Hopper microarchitecture specification, or proof of site-specific performance. It does not promise a particular speedup. All conclusions are conditional on measured shapes, dtypes, versions, and topology.
