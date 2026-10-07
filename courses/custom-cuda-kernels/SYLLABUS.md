# Syllabus

For a tensor-reading refresher, use [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html), a concise visual course with commented examples and no labs.

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

This course has no multi-node executable labs. Hopper thread-block clusters in Lab 10 operate inside one GPU and remain in the base route. Use [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html) for cross-GPU practice.

Lab preparation creates private log directories before any `sbatch` submission; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Before the first experiment, read the [GPU Performance Tools](../gpu-performance-tools/index.html) course. Existing lesson and executable lab IDs remain stable. Each lab applies measure → inspect → predict → change one variable → measure again → explain.

Estimated guided time: **36 hours**.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice → Mental model**. Start with [shared environment setup](../lab-guide.html#lab-preparation-scripts) once, then follow the lesson route. Each lab explains its purpose and needed concepts locally; Practice gives the commands and comparison to make. Use the syllabus to distinguish a reading preview from full execution.

Begin with the library-first decision and a reproducible SM90 build. Write one correct vector kernel, learn validation/profiling, then build fusion, memory and reduction skills. Establish resource limits before diagnosing tails, combine mechanisms in pipelines and RMSNorm, and complete acceptance before optional Hopper and Tile branches.

Follow the base lesson group first, then the separate advanced group below. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Decision, build and safety | 1–4 | Define the operation, build, validate indexing and learn the tool sequence |
| Data movement and cooperation | 5–8 | Fuse, coalesce, reduce and reuse shared-memory tiles |
| Resources and composition | 9–13 | Reason about residency/tails and compose pipelines, libraries and RMSNorm |
| Core acceptance | 14 | Make a correctness/safety/performance/maintenance decision |
| Optional advanced branches | 15–16 | Evaluate Hopper mechanisms and CUDA Tile without gating core completion |

## Lesson-by-lesson route

| Lesson | Topic | Competency to build | Practice at this stage |
| --- | --- | --- | --- |
| 1 | Custom kernel decision making | Reject custom ownership unless maintained paths leave an important gap | Library-first worksheet; run Lab 01 in Lesson 3 |
| 2 | CUDA compilation and execution targets | Build a reproducible SM90 program and verify the allocated device | Lab 13 |
| 3 | Kernel indexing and execution safety | Map elements to threads with complete bounds and error checks | Lab 01 |
| 4 | Kernel correctness and performance evidence | Validate outputs and safety before collecting focused profiler evidence | Revisit Lab 01 with sanitizer/profiler launchers |
| 5 | Elementwise kernel fusion | Remove intermediate traffic while preserving the elementwise operation | Lab 02 |
| 6 | Memory layout and tiled transposition | Use shared tiles to coalesce both sides of a transpose | Lab 03 |
| 7 | Parallel reductions | Compare aggregation and synchronization against a maintained reduction baseline | Lab 04 |
| 8 | Neighborhood reuse and boundary handling | Load shared interiors and halos without violating boundary semantics | Lab 05 |
| 9 | Kernel resource use and occupancy | Connect block size and compiler resources to residency and spills | Lab 07 |
| 10 | Parallel workload balance | Separate divergence, unequal block work and partial scheduling waves | Lab 06 |
| 11 | Asynchronous memory pipelines | Protect producer/consumer dependencies in a double-buffered copy pipeline | Lab 08 |
| 12 | Matrix multiplication and output fusion | Reuse maintained GEMM machinery while evaluating an epilogue composition | Lab 09 |
| 13 | Residual connections and normalization | Compose residual fusion and row reduction with explicit numerical limits | Lab 11 |
| 14 | Kernel acceptance and integration | Accept or reject a candidate using kernel and end-to-end evidence | Lab 12 three-trial acceptance |
| 15 | Advanced GPU data movement and cooperation | Qualify optional cluster/TMA concepts beyond the portable core | Optional Lab 10; extensions separately qualified |
| 16 | Tile-level kernel programming | Evaluate an optional tile abstraction without changing the core toolchain | Optional isolated CUDA Tile design experiment |

## Readiness checkpoints

After Lesson 4, demonstrate a correct edge case, understand what memcheck establishes and choose one profiling question. Revisit racecheck/synccheck when shared-memory cooperation appears. After Lesson 10, distinguish resource capacity from uneven work. Lesson 14 completes the required single-H100 path; Lessons 15–16 are optional and are not prerequisites for RMSNorm or the capstone.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, and ends with a **Mental model**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Required acceptance is single-GPU CUDA C++20. Preserve reference tests, relevant sanitizer results, focused profiler evidence and independent uninstrumented trials. Distributed CUDA/NCCL remains outside this course.
