# Syllabus

Estimated guided time: **36 hours**.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice labs → Mental model**. Read the definitions, mechanisms and worked examples in **How it works**, then open a **Practice labs** link, then follow the lab's **Theory preparation** in **Before you start**. Its named lessons explain the techniques before full execution; **Concepts and code path** connects them to the implementation. Before running, explain what each technique does, why it is used, how its inputs and dependencies work, and what timing and numerical checks establish. A preview is reading only; a later revisit adds a new interpretation without making an untaught technique a hidden prerequisite for the first run.

Establish a workload contract, correct timing and a profiler hypothesis before making changes. Progress through local launch, graph, input, memory and library optimizations; diagnose local imbalance before network qualification and tuning in Lesson 11, application scaling/overlap in Lesson 12, and the capstone in Lesson 13. Finish with an evidence-backed library-first decision.

Follow the lesson order below. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Experimental contract | 1–3 | Freeze equivalent work, measure correctly and select focused evidence |
| Local optimization | 4–10 | Tune execution/data paths and diagnose local imbalance |
| Distributed integration and decision | 11–13 | Measure exposed communication and choose the smallest justified intervention |

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
| 11 | GPU communication paths and performance | Verify the transport, read message-size curves and test one job-local change | Preflight, Labs 17, 18 |
| 12 | Distributed scaling and communication overlap | Hold global work fixed and identify exposed collective time | Labs 00, 08, 13 |
| 13 | Choosing an optimization layer | Select a maintained optimization layer and justify a keep/reject decision | Labs 09, 16 |

## Readiness checkpoints

After Lesson 3, describe the bottleneck hypothesis and the evidence that could disprove it. After Lesson 10, explain whether idle time belongs to the local workload or scheduler. In Lesson 11, qualify topology and transport, then interpret the collective size curve before changing one setting. Only then compare one- and two-rank application execution in Lesson 12. Lesson 13 integrates the final decision. The final decision must preserve correctness and include independent repeated runs.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice labs**, and ends with a **Mental model**. The linked guides integrate
examples and commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Complete the profiler-backed capstone with a baseline, one controlled change, correctness checks, repeated measurements and a scoped keep/reject decision.
