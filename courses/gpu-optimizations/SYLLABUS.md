# Syllabus

## Part I: Performance foundations and evidence

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 1 | End-to-end performance system | Critical-path decomposition with a named objective and boundary | 00 preflight and guided worksheet |
| 2 | Five primary bottleneck classes | Compute, memory-bandwidth, launch/CPU, communication, and input/storage hypotheses with disconfirming controls | 14 profiler workshop and guided diagnosis |

## Part II: Diagnostic tools and practical profiling

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 3 | Tool installation, ownership, and preflight | Compute-node inventory with versions, permissions, and unavailable evidence recorded | Tooling preflight |
| 4 | PyTorch Profiler and NVTX | Operator-to-kernel attribution with semantic phase ranges | 07 profiler workload |
| 5 | Nsight Systems | Timeline diagnosis of host gaps, synchronization, copies, and overlap | 14 synchronization and launch cases |
| 6 | Nsight Compute and roofline | Selected-kernel report with arithmetic intensity, effective TFLOP/s, traffic, and scheduler evidence | 14 memory and compute cases |
| 7 | Telemetry, transport, collective, and serving tools | Tool-selection plan separating DCGM/DCGM Exporter, `nvbandwidth`, NCCL Tests, vLLM Bench, AIPerf, and MLPerf evidence | Tooling preflight and guided serving exercise |

## Part III: Measurement and targeted optimization

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 8 | Optimization contract and stability controls | Frozen work, metric, boundary, correctness, environment, repeatability, and acceptance rule | 00 preflight |
| 9 | Warm-up, events, synchronization, and distributions | Timing protocol with an explicit completion boundary | 01 timing basics, 02 sync trap |
| 10 | Launch and CPU overhead | Synchronization-removal, fusion, or capture hypothesis tied to a host-gap trace | 02 sync trap, 03 compile fusion, 04 CUDA graphs, 14 profiler workshop |
| 11 | Transfer and overlap | Pageable/pinned/async ownership timeline and resident-input control | 05 input pipeline |
| 12 | HBM traffic, layout, and intermediates | Named-boundary byte estimate, reuse control, and materialization proposal | 14 memory case; optional companion: GPU Fundamentals Lab 04 |
| 13 | Kernel efficiency | Grid-wave, geometry, divergence, coalescing, bank-conflict, register, and spill diagnosis | 07 trace plus focused profile; optional companion: GPU Fundamentals Lab 09 |
| 14 | Compute and shape eligibility | Tensor Core path and numerical acceptance table | 10 shape precision, 11 SDPA attention, 14 compute case |
| 15 | Compilation, fusion, and CUDA Graphs | Compile/capture boundary with startup and warmed evidence | 03 compile fusion, 04 CUDA graphs |
| 16 | Input starvation | Ready-data control and loader change | 05 input pipeline |
| 17 | Capacity, allocator, and recomputation | Live/reserved memory ledger plus checkpoint tradeoff | 06 checkpointing, 12 allocator lifetime |

## Part IV: Distributed performance and causal decisions

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 18 | Two-node communication and overlap | Fixed-work scaling efficiency, exposed collective time, and simultaneous-work evidence | 08 distributed scaling, 13 collective overlap |
| 19 | Capstone optimization report | Baseline/candidate distributions, correctness, memory, repeatability, and decision | 09 capstone |

## Review and assessment

- Answer the retrieval cues after modules 2, 7, 14, and 18 without notes.
- For every technique, state the bottleneck class it targets and one condition under which it may fail to improve the declared objective.
- Distinguish transfer and synchronization mechanisms from the five peer bottleneck classes, and distinguish capacity from a timed bottleneck.
- Reject at least one tempting optimization for a documented reason.
- Complete the capstone with observations separated from inferences and unknowns.

## Live completion gate

Static validation is necessary but insufficient. Completion requires the smoke-test sequence on the declared two-node H100 cluster, including profiler reports, an exact collective result, and one equivalent-work scaling comparison.
