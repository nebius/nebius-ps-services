# Syllabus

## Part I: GPU foundations and Hopper architecture

| Module | Topic | Learner output | Lab |
| --- | --- | --- | --- |
| 1 | CPU and GPU execution models | Crossover interpretation separating launch, transfer, and resident execution | 01 CPU/GPU crossover |
| 2 | Logical H100 device architecture | HBM-to-GPC/TPC/SM hierarchy map with SKU-dependent facts labeled | Guided architecture trace |
| 3 | Hopper SM architecture | SMSP, scheduler, register, shared/L1, and execution-resource ownership map | Guided SM resource trace |
| 4 | Grid, block, warp, and SM assignment | Operator-to-kernel execution map plus masked launch-geometry sweep | 08 operator to kernels, 09 Triton launch geometry |
| 5 | Asynchronous launch journey | Host/device enqueue, completion, stream, and dependency explanation | 07 async streams |

## Part II: Measurement and scheduling

| Module | Topic | Learner output | Lab |
| --- | --- | --- | --- |
| 6 | Valid GPU evidence and timing | Written boundary, completion point, correctness gate, and sample policy | 00 cluster preflight and 07 async streams |
| 7 | Bottleneck taxonomy | Symptom, alternative hypothesis, and disconfirming control | Guided diagnosis |
| 8 | Scheduling, occupancy, divergence, and wave tails | Resource hypothesis without occupancy folklore | Guided worksheet |

## Part III: Data movement and computation

| Module | Topic | Learner output | Lab |
| --- | --- | --- | --- |
| 9 | HBM, L2, L1/shared memory, registers, and PCIe | Named-boundary data-path and allocator map | 03 transfer and pinning |
| 10 | Layout and coalescing | Useful-byte reasoning and repack break-even calculation | 04 layout and coalescing |
| 11 | Pinned copies, streams, and double buffering | Dependency-safe copy/compute timeline with fill and drain | 03 transfer and pinning plus 07 async streams |
| 12 | Precision and Tensor Core eligibility | Path-selection and performance-plus-accuracy comparison | 02 Tensor Core precision |
| 13 | Arithmetic intensity and roofline reasoning | Predicted limiter, disconfirming evidence, and next profile | 05 roofline microbench |

## Part IV: Multi-node scale and diagnosis

| Module | Topic | Learner output | Lab |
| --- | --- | --- | --- |
| 14 | Processes, ranks, topology, NCCL, and inter-node costs | Two-node collective result with discovered path and fixed-work semantics | 06 distributed collectives |
| 15 | Bottleneck diagnosis and reporting | Evidence chain plus completed benchmark record | Course capstone |

## Review schedule

- After modules 5, 8, and 13, answer the accumulated review cues without looking back.
- Re-run only the questions answered incorrectly, then revisit their worked examples.
- At the end, explain one local and one distributed result to another learner using no unexplained acronyms.

## Completion criteria

- All lesson exercises completed with self-checks.
- All smoke-profile lab correctness gates pass on the declared cluster.
- At least one result is repeated and reported as a distribution.
- The final benchmark record distinguishes observations, inferences, and unknowns.
