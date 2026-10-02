# Syllabus

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a qualified two-worker, sixteen-H100 cluster, which can also run the local labs with one-GPU allocations. The conceptual lessons here remain useful prerequisites.

Each native submission block prepares private log directories before calling `sbatch`; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Before the first experiment, complete shared environment setup and read the unnumbered **Using GPU performance tools** lesson. Existing lesson IDs remain stable; distributed lab links use their new advanced-course identities. Each lab applies measure → inspect → predict → change one variable → measure again → explain.

Estimated guided time: **44 hours**, for the conceptual and local practical route; provisioning and queue time are excluded.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice → Mental model**. Start with [shared environment setup](../README.md#how-to-set-up-the-lab) once, then follow the lesson route. Each lab explains its purpose and needed concepts locally; Practice gives the commands and comparison to make. Use the syllabus to distinguish a reading preview from full execution.

Read the conceptual lessons in numbered order: workload and measurement (1–3), local optimization (4–10), communication and scaling (11–12), library-first decisions (13), then fabric topology, GPU memory registration and rank timelines (14–16). Complete local practice on the base cluster. For distributed execution, follow the advanced course’s own lab route, which verifies topology and transport before training or serving experiments.

Follow the numbered conceptual route; distributed practice links open the dedicated advanced course. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Experimental contract | 1–3 | Freeze equivalent work, measure correctly and select focused evidence |
| Local optimization | 4–10 | Tune execution/data paths and diagnose local imbalance |
| Local integration and decision | 13 | Choose the smallest justified intervention |
| Advanced GPU fabric | 14, 15, 11, 12, 16 | Qualify peer/RDMA paths, tune collectives and correlate rank traces |

## Lesson-by-lesson route

| Lesson | Topic | Competency to build | Practice at this stage |
| --- | --- | --- | --- |
| 1 | Controlled GPU optimization | Freeze workload, correctness tolerance and the outcome that matters | Baseline worksheet; two-node preflight belongs to Lesson 11 |
| 2 | Asynchronous performance measurement | Separate elapsed device intervals from synchronized application timing | Labs 01, 02 |
| 3 | Performance evidence and profiling | Choose a timeline or counter tool to test one hypothesis | Lab 07, then Lab 14 synchronization case; other cases are previews |
| 4 | Submission overhead and kernel fusion | Reduce dispatch and intermediate work without changing the operation | Lab 03 |
| 5 | Reusable GPU execution plans | Identify stable addresses and execution needed for graph replay | Lab 04 |
| 6 | Input readiness and transfer overlap | Keep valid input batches ready while preserving sample ownership | Lab 05, then Lab 19 input overlap |
| 7 | Tensor layout and memory traffic | Reduce unnecessary copies and improve physical access patterns | Reuse Lab 03 for a traffic ledger; preview Lab 10; pipeline-layout extension |
| 8 | Memory allocation and ownership | Separate live tensor memory from allocator reserve and lifetime | Lab 12, then Lab 20 output ownership |
| 9 | Efficient numerical-library execution | Compare library-friendly shapes and precision with explicit correctness gates | Lab 10 |
| 10 | Parallel imbalance and completion tails | Locate whether completion is limited by lanes, blocks or tail waves | Lab 15 |
| 11 | GPU communication paths and performance | Verify the transport, read message-size curves and test one job-local change | [Advanced Lab 05](../advanced-gpu-communication/reference/labs/05_transport_readiness.md); [Advanced Lab 09](../advanced-gpu-communication/reference/labs/09_nccl_transport_sweep.md); [Advanced Lab 10](../advanced-gpu-communication/reference/labs/10_nccl_tests_report.md) |
| 12 | Distributed scaling and communication overlap | Hold global work fixed and identify exposed collective time | [Advanced Lab 12](../advanced-gpu-communication/reference/labs/12_distributed_scaling.md); [Advanced Lab 13](../advanced-gpu-communication/reference/labs/13_collective_overlap.md) |
| 13 | Choosing an optimization layer | Select a maintained optimization layer and justify a keep/reject decision | Labs 09, 16 |
| 14 | GPU fabric topology and peer traffic | Validate rank/GPU placement and compare all 56 directed peer paths | [Advanced Lab 01](../advanced-gpu-communication/reference/labs/01_fabric_topology.md); [Advanced Lab 06](../advanced-gpu-communication/reference/labs/06_nvlink_bandwidth.md) |
| 15 | GPU memory over InfiniBand | Validate transferred bytes, distinguish GPU registration paths and tune queue depth | [Advanced Lab 07](../advanced-gpu-communication/reference/labs/07_rdma_bandwidth.md) |
| 16 | Communication ownership and rank timelines | Compare collective layouts and correlate PyTorch/Nsight traces across ranks | [Advanced Lab 11](../advanced-gpu-communication/reference/labs/11_collective_layout.md); [Advanced Lab 14](../advanced-gpu-communication/reference/labs/14_distributed_profiling.md) |

## Readiness checkpoints

After Lesson 3, describe the bottleneck hypothesis and the evidence that could disprove it. After Lesson 10, explain whether idle time belongs to the local workload or scheduler. In Lesson 11, qualify topology and transport, then interpret the collective size curve before changing one setting. Only then compare one- and two-rank application execution in Lesson 12. The base-route capstone in Lesson 13 integrates local decisions; revisit it with fabric evidence after Lesson 16. The final decision must preserve correctness and include independent repeated runs.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, and ends with a **Mental model**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Complete the profiler-backed capstone with a baseline, one controlled change, correctness checks, repeated measurements and a scoped keep/reject decision.
