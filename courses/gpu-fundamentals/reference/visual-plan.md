# Visual plan

The table defines this course's overview diagrams. The
[visual manifest](visual-manifest.json) links additional detailed diagrams to
their conceptual lessons. Each figure appears after the specified section in its declared lesson or lab home,
with accessible labels, captions, and fit-to-width sizing.

The opening detailed H100 overview follows **Start here** in Lesson 1. It
separates physical HBM/L2/SM/L1/shared/register resources from logical
grid/block/warp/thread work, including the four subpartitions inside one SM.
It is a conceptual teaching map, not a die floorplan or fixed-SKU inventory.

| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Software compatibility stack | PyTorch and libraries | CUDA runtime and driver | PTX, SASS, and H100 | Diagnose the layer that owns compilation, loading, or execution. | 2 | Mechanism | flow | lesson |
| H100 execution hierarchy | H100 GPU | Streaming multiprocessor | Execution resources | Physical containment: the GPU contains SMs, and each SM contains schedulers and execution pipelines. The separate launch diagram maps logical blocks onto these resources. | 3 | Mental model | hierarchy | lesson |
| SIMT and divergence | One warp | Active-lane masks | Reconvergence | Divergent paths consume issue time under different lane masks. | 5 | Mechanism | flow | lesson |
| Occupancy and latency hiding | Resident resources | Ready warps | Pipeline progress | Registers and shared memory constrain the warps available to hide latency. | 6 | Mechanism | flow | lesson |
| Memory hierarchy | HBM and caches | Block shared memory | Thread registers | These are distinct storage resources, not nested containers. Caches manage reuse automatically; shared memory is explicitly coordinated and registers hold thread-local values. | 4 | Mechanism | comparison | lesson |
| Coalesced access | Lane addresses | Memory transactions | Useful bandwidth | Adjacent aligned lane addresses reduce wasted transactions. | 7 | Mechanism | flow | lesson |
| Roofline classification | Bytes and operations | Arithmetic intensity | Memory or compute roof | The ridge point separates data-movement and compute opportunities. | 10 | Mechanism | roofline | lesson |
| Sharing modes | Full GPU | MIG or MPS | Scheduler time-slicing | Each sharing model changes isolation, accounting, and interpretation. | 11 | Mechanism | comparison | lesson |
| Health context | ECC and Xid | Power and clocks | Trial correlation | Error/reliability and operating-state signals are separate evidence inputs. Correlate changes with trial timing and scope; correlation alone does not prove a cause. | 11 | Mechanism | comparison | lesson |
| Two-node topology | H100 and host | Network fabric | Remote H100 | Bidirectional cross-node data travels through host and network paths. This schematic is not proof of RDMA or NVSwitch scaling. | 12 | Mechanism | topology | lesson |
