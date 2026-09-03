# Source-concept coverage

This public-safe map records where the source audit landed without retaining
source identifiers or non-public metadata.

| Topic family | Explanation | Diagram | Practice | Status |
| --- | --- | --- | --- | --- |
| CPU versus GPU | Module 1 | Crossover comparison table | Lab 01 | Covered |
| H100 HBM, memory controllers, L2, GPC, TPC, and SM hierarchy | Module 2 | Logical H100 device hierarchy | Guided trace | Covered |
| Hopper SMSPs, schedulers, registers, shared/L1, and execution resources | Module 3 | Schematic Hopper SM | Guided trace | Covered |
| Kernel, grid, block, thread, warp, SM assignment, and waves | Modules 4 and 8 | Execution hierarchy, warp scheduling, and wave-tail diagrams | Labs 08 and 09 | Covered |
| Asynchrony and trustworthy measurement | Modules 5–6 | Host/device journey and timing boundary | Labs 00 and 07 | Covered |
| Bottleneck taxonomy | Module 7 | Symptom-to-control map | Guided diagnosis | Covered |
| HBM, L2, L1/shared memory, registers, PCIe, and allocator scope | Module 9 | Copy and kernel-demand paths | Labs 03 and 05 | Covered |
| Layout and coalescing | Module 10 | Adjacent versus strided lane access | Lab 04 | Covered |
| Pinned copies, streams, overlap, and double buffering | Module 11 | Fill/steady-state/drain timeline | Labs 03 and 07 | Covered |
| CUDA/Tensor Cores and numeric formats | Module 12 | Tensor Core path-selection flow and comparison table | Lab 02 | Covered |
| Arithmetic intensity and roofline | Module 13 | Roofline | Lab 05 | Covered |
| Topology, ranks, and collectives | Module 14 | Discovered two-node communication path | Labs 00 and 06 | Covered |
| Diagnosis and reproducible reporting | Module 15 | No dedicated diagram; benchmark-record template | All lab records | Covered |

Modules 3 and 9 introduce Hopper TMA and distributed shared memory as advanced,
architecture-specific extensions. They are not prerequisites and do not have
a course lab because the Python/PyTorch performance mission does not require a
custom cluster-kernel implementation.
