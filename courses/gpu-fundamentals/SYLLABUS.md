# Syllabus

Estimated guided time: **21 hours**.

## Learning progression

Begin with the first lesson's **Start here** explanation and workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Each later lesson begins with **What it is** before objectives or applications. Read the definition and mechanism, open a **Practice labs** link, then follow the lab's **Theory preparation** in **Before you start**. Its named lessons explain the techniques before full execution; **Concepts and code path** connects them to the implementation. Before running, explain what each technique does, why it is used, how its inputs and dependencies work, and what timing and numerical checks establish. A preview is reading only; a later revisit adds a new interpretation without making an untaught technique a hidden prerequisite for the first run.

Start with CPU/GPU boundaries and the whole H100: SMs, L2 and HBM. Separate hardware containment from grid/block/warp/thread grouping, then identify the software stack and develop scheduling. Lab 01 teaches the timing and numerical checks at their point of use. Define memory resources before SIMT and occupancy, connect access patterns and precision to roofline, and finish with read-only health, networking layers and bounded two-node communication. Learn GPU/NIC attachment, NVLink/NVSwitch, InfiniBand/RoCE, RDMA and GPUDirect RDMA before interpreting NCCL collectives.

Follow the lesson order below. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Entry and execution model | 1–3 | Separate timing boundaries, identify the stack, and map work to hardware |
| Memory and local efficiency | 4–10 | Explain storage, active lanes, residency, transfers, precision and roofline |
| System context and integration | 11–12 | Interpret read-only health, verify topology and measure collectives |

## Lesson-by-lesson route

| Lesson | Topic | Competency to build | Practice at this stage |
| --- | --- | --- | --- |
| 1 | CPU–GPU cooperation | Choose CPU or GPU using equivalent work and explicit timing boundaries | Lab 10 preflight, then Lab 01 |
| 2 | GPU execution software layers | Assign compilation, loading and compatibility failures to the correct layer | Lab 10, then Lab 08 |
| 3 | GPU execution architecture | Map logical grids, blocks and warps to physical H100 resources | Lab 09 geometry; resource interpretation in Lesson 6 |
| 4 | GPU memory hierarchy | Trace value ownership and lifetime through the memory hierarchy | Lab 04 byte ledger; preview Lab 05 |
| 5 | Parallel control flow | Distinguish active-lane divergence from independent useful work | Inspect Lab 11 lane model; full launch after Lesson 6 |
| 6 | Occupancy and latency hiding | Explain resident-warp limits using registers, shared storage and block size | Labs 09, 11 |
| 7 | Memory access efficiency | Map adjacent lanes to addresses and reason about transaction efficiency | Lab 04 |
| 8 | Asynchronous execution and timing | Order transfers and kernels and measure completion rather than submission | Labs 03, 07 |
| 9 | Numerical precision and accelerated arithmetic | Choose numerical formats and identify actual Tensor Core execution | Lab 02 |
| 10 | Arithmetic intensity and performance limits | Derive arithmetic intensity and an independent roofline performance bound | Lab 05 full roofline experiment |
| 11 | GPU sharing and operational health | Distinguish supported sharing and health observations from configuration changes | Lab 12 |
| 12 | GPU communication and distributed execution | Combine placement, network and collective evidence without scale overclaims | Networking theory, then Lab 00 preflight and existing Lab 06 |

## Readiness checkpoints

After Lesson 3, draw the CPU-to-device execution path and explain a trustworthy timer. After Lesson 7, distinguish memory layout, lane utilization and residency. After Lesson 10, derive a roofline bound before comparing measured performance. Finish by attaching health context and the two-node collective evidence to one scoped explanation.

Every lesson retains its definition, prerequisite bridge, mental model and
mechanism, followed by **Practice labs** links. The linked guides integrate
examples and commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Run single-GPU labs through their supplied launchers, then Lab 00 before the two-node collective lab. Keep observation, inference and unverified hypothesis separate.
