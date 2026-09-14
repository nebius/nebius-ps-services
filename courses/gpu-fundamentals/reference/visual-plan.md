# Visual plan

The table defines this course's overview diagrams. The
[visual manifest](visual-manifest.json) links additional detailed diagrams to
their conceptual lessons. Each figure appears within the specified lesson section or after the specified lab section in its declared lesson or lab home,
with accessible labels, captions, and fit-to-width sizing.

The opening detailed H100 overview is inside **How it works** in Lesson 1. It
separates physical HBM/L2/SM/L1/shared/register resources from logical
grid/block/warp/thread work, including the four subpartitions inside one SM.
It is a conceptual teaching map, not a die floorplan or fixed-SKU inventory.

| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Software compatibility stack | Packaged kernel or PTX | CUDA driver loads code | GPU executes kernel | Read downward: a framework or library supplies device code, the CUDA driver loads or translates a compatible version, and the GPU executes it. The toolkit compiler prepares code earlier; installing it is not a substitute for a compatible driver. | 2 | How it works | flow | lesson |
| H100 execution hierarchy | H100 GPU | Streaming multiprocessor | Execution resources | Each enclosing box contains the one inside it: an H100 contains SMs, and each SM contains scheduling and execution resources. Logical blocks and warps are work assigned to this hardware, not additional hardware containers. | 3 | How it works | hierarchy | lesson |
| SIMT and divergence | Threads choose paths | Execute each required path | Continue after the branch | Read downward through a divergent branch. Each path runs for the lanes that need it; inactive lanes do not perform useful work for that path. The arrows show execution order, not a measured timeline. | 5 | How it works | flow | lesson |
| Occupancy and latency hiding | Resources admit blocks | Eligible warps can issue | Other work covers waits | Read downward: registers, shared memory and hardware limits bound resident work. A scheduler can select a ready warp while another waits. More resident warps help only if they supply useful ready work. | 6 | How it works | flow | lesson |
| Memory hierarchy | HBM and caches | Block shared memory | Thread registers | Compare three storage roles without treating them as a mandatory path. Caches retain data automatically, shared memory is explicitly coordinated by a block, and registers hold thread working values. | 4 | How it works | comparison | lesson |
| Coalesced access | A warp requests addresses | Requests group into sectors | Less unused data is fetched | The arrows connect lane addresses to memory traffic. Adjacent aligned requests can share fetched regions; large strides spread the same useful values across more regions. Fewer logical values alone does not guarantee fewer transactions. | 7 | How it works | flow | lesson |
| Roofline classification | Bandwidth ceiling | Ridge point | Compute ceiling | The horizontal axis is operations per byte and the vertical axis is operations per second. The sloped bandwidth ceiling meets the flat compute ceiling at the ridge point. This is a schematic bound with no measured workload points. | 10 | How it works | roofline | lesson |
| Sharing modes | Full-GPU allocation | MIG or MPS sharing | Scheduler time-slicing | Compare allocation and sharing choices. MIG partitions supported hardware; MPS coordinates CUDA processes; time-slicing alternates access. A full-GPU scheduler allocation alone does not prove that no other process uses the device. | 11 | How it works | comparison | lesson |
| Health context | ECC and Xid | Power and clocks | Trial correlation | Compare reliability signals with operating-state signals, then relate both to the same trial interval. ECC/Xid changes, power and clocks provide context; temporal correlation alone does not identify the cause of a slowdown. | 11 | How it works | comparison | lesson |
| Two-node topology | Local H100 and attachment | NICs and network fabric | Remote attachment and H100 | Double-ended arrows represent communication in either direction between the nodes. The GPU-to-NIC attachment and network are part of the path; this schematic does not establish RDMA use or NVSwitch scaling. | 12 | How it works | topology | lesson |
