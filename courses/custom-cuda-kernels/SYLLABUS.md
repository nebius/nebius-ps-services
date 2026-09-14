# Syllabus

Estimated guided time: **36 hours**.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice labs → Mental model**. Read the definitions, mechanisms and worked examples in **How it works**, then open a **Practice labs** link, then follow the lab's **Theory preparation** in **Before you start**. Its named lessons explain the techniques before full execution; **Concepts and code path** connects them to the implementation. Before running, explain what each technique does, why it is used, how its inputs and dependencies work, and what timing and numerical checks establish. A preview is reading only; a later revisit adds a new interpretation without making an untaught technique a hidden prerequisite for the first run.

Begin with the library-first decision and a reproducible SM90 build. Write one correct vector kernel, learn validation/profiling, then build fusion, memory and reduction skills. Establish resource limits before diagnosing tails, combine mechanisms in pipelines and RMSNorm, and complete acceptance before optional Hopper and Tile branches.

Follow the lesson order below. Lab numbers are identifiers, not the execution order;
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
| 2 | CUDA compilation and execution targets | Build a reproducible SM90 program and verify the allocated device | Lab 00 |
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
in **How it works**, links its **Practice labs**, and ends with a **Mental model**. The linked guides integrate
examples and commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Required acceptance is single-GPU CUDA C++20. Preserve reference tests, relevant sanitizer results, focused profiler evidence and independent uninstrumented trials. Distributed CUDA/NCCL remains outside this course.
