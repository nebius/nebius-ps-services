# Visual plan

The table defines this course's overview diagrams. The
[visual manifest](visual-manifest.json) links additional detailed diagrams to
their conceptual lessons. Each figure appears after the specified section in its declared lesson or lab home,
with accessible labels, captions, and fit-to-width sizing.

The host-API and device-kernel diagram follows **Start here** in Lesson 1.
CPU and CUDA paths are distinct; the CUDA path separates interface validation,
submission and device execution. Allocation, transfers, completion and the
consumer-facing package contract remain application responsibilities.

| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Library-first decision | Framework/compiler | cuBLAS, CUB, CUTLASS | Narrow custom kernel | Own CUDA only for an important residual gap. | 1 | Mechanism | decision | lesson |
| CUDA execution | Grid and blocks | Warps on SMs | Memory and pipelines | Indexing and block resources connect code to H100 execution. | 3 | Mechanism | flow | lesson |
| Fusion traffic | Separate launches | Register-resident fusion | One output write | Compare the separate and fused designs. Fusion can remove launches and logical intermediate reads/writes; caches determine how much physical HBM traffic is saved. | 5 | Mechanism | comparison | lesson |
| Coalesced transpose | Row-wise global load | Padded shared tile | Row-wise global store | For FP32 32×32 tiles, adding one padding column changes shared-bank mapping so the transposed reads avoid the column conflict. Other layouts need their own bank analysis. | 6 | Mechanism | flow | lesson |
| Hierarchical reduction | Lane values | Warp and block partials | CUB or global result | Local aggregation reduces global atomic contention but may add block synchronization. | 7 | Mechanism | flow | lesson |
| Stencil tile | Interior and halo loads | Shared reuse | Boundary-safe outputs | Tiling helps only when reused values repay synchronization. | 8 | Mechanism | flow | lesson |
| Imbalance diagnosis | Active lane masks | Block-duration skew | Final grid wave | Divergence, skew, and tails require different remedies. | 10 | Mechanism | comparison | lesson |
| Resource tradeoff | Registers and shared memory | Resident warps | Spills and runtime | Registers and shared-memory limits constrain resident warps; spills can add traffic. Choose from measured runtime, not occupancy alone. | 9 | Mechanism | flow | lesson |
| Double-buffer pipeline | Load next tile | Compute current tile | Swap stages | Prime, steady-state, and drain phases protect data dependencies. | 11 | Mechanism | pipeline | lesson |
| Library epilogue | Library GEMM machinery | Bias plus activation | Final store | Lab 09 compares cuBLAS plus a separate epilogue with CUTLASS FP32 SIMT and an expanded bias matrix. It is not a Hopper Tensor Core or pure epilogue-only comparison. | 12 | Mechanism | flow | lesson |
| H100 advanced path | TMA tile movement | Cluster scheduling | Cluster shared-memory access | TMA and clusters are distinct Hopper mechanisms; distributed shared memory requires cooperating cluster blocks. Lab 10 probes cluster launch only, not a TMA or shared-memory pipeline. | 15 | Mechanism | comparison | lesson |
| Acceptance flow | Reference and tests | Sanitizer and profiler | End-to-end decision | Correctness, safety, hardware evidence, and product value must agree. | 14 | Mechanism | decision | lesson |
