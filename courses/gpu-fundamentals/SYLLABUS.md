# Syllabus

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a qualified two-worker, sixteen-H100 cluster, which can also run the local labs with one-GPU allocations. The conceptual lessons here remain useful prerequisites.

Each native submission block prepares private log directories before calling `sbatch`; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Before the first experiment, complete shared environment setup and read the unnumbered **Using GPU performance tools** lesson. Existing lesson IDs remain stable; distributed lab links use their new advanced-course identities. Each lab applies measure → inspect → predict → change one variable → measure again → explain.

Estimated guided time: **21 hours**.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice → Mental model**. Start with [shared environment setup](../README.md#how-to-set-up-the-lab) once, then follow the lesson route. Each lab explains its purpose and needed concepts locally; Practice gives the commands and comparison to make. Use the syllabus to distinguish a reading preview from full execution.

Start with CPU/GPU cooperation and the whole H100: SMs, L2 and HBM. Separate hardware containment from grid/block/warp/thread grouping, then identify the software stack and develop scheduling. Lab 01 teaches the timing and numerical checks at their point of use. Define memory resources before SIMT and occupancy, connect access patterns and precision to roofline, and finish with read-only health, networking layers and bounded two-node communication. Learn GPU/NIC attachment, NVLink/NVSwitch, InfiniBand/RoCE, RDMA and GPUDirect RDMA before interpreting NCCL collectives.

Follow the numbered conceptual route; distributed practice links open the dedicated advanced course. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Entry and execution model | 1–3 | Distinguish CPU timers from CUDA events, identify the stack, and map work to hardware |
| Memory and local efficiency | 4–10 | Explain storage, active lanes, residency, transfers, precision and roofline |
| Local system context | 11 | Interpret read-only GPU health |
| Advanced communication | 12 | Verify placement and measure bounded collectives on the fabric cluster |

## Lesson-by-lesson route

| Lesson | Topic | Competency to build | Practice at this stage |
| --- | --- | --- | --- |
| 1 | CPU–GPU cooperation | Choose CPU or GPU using equivalent work with clearly specified timed operations | Lab 10 preflight, then Lab 01 |
| 2 | GPU execution software layers | Assign compilation, loading and compatibility failures to the correct layer | Lab 10, then Lab 08 |
| 3 | GPU execution architecture | Map logical grids, blocks and warps to physical H100 resources | Lab 09 geometry; revisit Lab 08 launch evidence; preview Lab 11; resource interpretation in Lesson 6 |
| 4 | GPU memory hierarchy | Trace value ownership and lifetime through the memory hierarchy | Lab 04 read/write byte counts; preview Lab 05 |
| 5 | Parallel control flow | Distinguish active-lane divergence from independent useful work | Inspect Lab 11 lane model; full launch after Lesson 6 |
| 6 | Occupancy and latency hiding | Explain resident-warp limits using registers, shared storage and block size | Labs 09, 11 |
| 7 | Memory access efficiency | Map adjacent lanes to addresses and reason about transaction efficiency | Lab 04 |
| 8 | Asynchronous execution and timing | Order transfers and kernels and measure completion rather than submission | Labs 03, 07 |
| 9 | Numerical precision and accelerated arithmetic | Choose numerical formats and identify actual Tensor Core execution | Lab 02 |
| 10 | Arithmetic intensity and performance limits | Derive arithmetic intensity and an independent roofline performance bound | Lab 05 full roofline experiment |
| 11 | GPU sharing and operational health | Distinguish supported sharing and health observations from configuration changes | Lab 12 |
| 12 | GPU communication and distributed execution | Combine placement, network and collective evidence without scale overclaims | [Advanced Lab 02](../advanced-gpu-communication/reference/labs/02_collective_readiness.md); [Advanced Lab 08](../advanced-gpu-communication/reference/labs/08_distributed_collectives.md) |

## Readiness checkpoints

After Lesson 3, draw the CPU-to-device execution path and explain a trustworthy timer. After Lesson 7, distinguish memory layout, lane utilization and residency. After Lesson 10, derive a roofline bound before comparing measured performance. Finish by attaching health context and the two-node collective evidence to one scoped explanation.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, and ends with a **Mental model**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Complete the single-GPU labs through their supplied launchers. For distributed practice, move to [Advanced Lab 02: Verify the two-node H100 platform](../advanced-gpu-communication/reference/labs/02_collective_readiness.md), then [Advanced Lab 08: Measure a two-node NCCL all-reduce](../advanced-gpu-communication/reference/labs/08_distributed_collectives.md). Keep observation, inference and unverified hypothesis separate.
