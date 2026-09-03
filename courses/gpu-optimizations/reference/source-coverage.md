# Source-concept coverage

This public-safe map records where the source audit landed without retaining private source identifiers.

| Topic family | Explanation | Diagram | Practice | Status |
| --- | --- | --- | --- | --- |
| End-to-end critical path and optimization scope | Module 1 | Host-to-input-to-GPU-to-communication path | Guided worksheet | Covered |
| Five primary bottleneck classes: compute, memory bandwidth, launch/CPU, communication, and input/storage | Module 2 | Five-class diagnosis map | Lab 14 controls and guided classification | Covered |
| Tool installation, ownership, permissions, and compute-node verification | Module 3 and tooling setup guide | Evidence-scope ladder | Tooling preflight | Covered; site installation remains administrator-owned |
| PyTorch Profiler and semantic NVTX ranges | Module 4 | Operator-to-CUDA attribution flow | Lab 07 | Covered |
| Nsight Systems timelines for gaps, synchronization, copies, and overlap | Module 5 | Practical system timeline | Lab 14 synchronization and launch cases | Covered; live report remains gated |
| Nsight Compute, roofline, TFLOP/s, arithmetic intensity, traffic, scheduler, and occupancy | Module 6 | Roofline model | Lab 14 memory and compute cases | Covered; live counters remain gated |
| `nvidia-smi`, DCGM/DCGM Exporter, `nvbandwidth`, NCCL Tests, vLLM Bench, AIPerf, MLPerf, and service metrics | Module 7 | Evidence-scope tables | Tooling preflight and guided tool-selection exercise | Covered; services, binaries, standards runs, and site telemetry remain live-gated |
| Benchmark boundaries, stability controls, warm-up, and timing | Modules 8–9 | Evidence loop | Labs 01 and 02 | Covered |
| Launch and CPU overhead | Module 10 | Timeline evidence | Labs 02–04 and 14 | Covered |
| Transfer and overlap | Module 11 | Dependency-safe overlap reasoning | Lab 05 | Covered |
| HBM traffic, layout, coalescing, and intermediates | Module 12 | Named-boundary byte reasoning | Lab 14 memory case; optional companion: GPU Fundamentals Lab 04 | Covered |
| Waves, geometry, divergence, coalescing, bank conflicts, registers, and spills | Module 13 | Kernel residency and wave-tail diagram | Trace/profile exercise | Covered |
| Precision, shapes, Tensor Cores, and attention | Module 14 | Comparison table | Labs 10, 11, and 14 | Covered |
| Fusion, compilation, CUDA Graphs, and custom-kernel escalation | Module 15 | Benchmark and trace evidence | Labs 03 and 04; optional companion: GPU Fundamentals Lab 09 | Covered |
| Input starvation | Module 16 | Ready-data control | Lab 05 | Covered |
| Allocator lifetime, fragmentation, checkpointing, and capacity | Module 17 | Allocator segments | Labs 06 and 12 | Covered |
| Fixed-work one-rank/two-rank scaling and overlap | Module 18 | Scaling and simultaneous-work timelines | Labs 08 and 13 | Covered |
| Causal keep/reject reporting | Module 19 | Four-gate decision flow | Lab 09 | Covered |

Transfer serialization and synchronization appear as mechanisms that can expose more than one primary class. Capacity is treated as a feasibility limit and can cause paging, recomputation, or admission effects; it is not added as a sixth timed bottleneck class.

Production DCGM/DCGM Exporter administration, `nvbandwidth`, NCCL Tests, and standardized serving load generators are documented tool paths rather than bundled Python labs because they are site-owned executables, services, permissions, or benchmark programs. The course explicitly maps DDP, sharded training, tensor parallelism, and expert routing to the collective tests that can isolate their communication primitives before the real application is profiled.
