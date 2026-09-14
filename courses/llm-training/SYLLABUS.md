# Syllabus

Estimated guided time: **48 hours**.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice labs → Mental model**. Read the definitions, mechanisms and worked examples in **How it works**, then open a **Practice labs** link, then follow the lab's **Theory preparation** in **Before you start**. Its named lessons explain the techniques before full execution; **Concepts and code path** connects them to the implementation. Before running, explain what each technique does, why it is used, how its inputs and dependencies work, and what timing and numerical checks establish. A preview is reading only; a later revisit adds a new interpretation without making an untaught technique a hidden prerequisite for the first run.

First learn what a model and a parameter are, how a loss guides a weight update, and how inference differs. Then learn what is trained, how text becomes tensors, how the decoder produces logits and how one correct update changes state. Establish resume, memory, precision, recomputation, input and local execution skills before distributed training. Apply that foundation to SFT/LoRA and GRPO, then integrate the evidence in the capstone.

Follow the lesson order below. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Correct single-GPU training | 1–5 | Understand objective, batches, model, update and reproducible resume |
| Single-GPU performance | 6–10 | Explain memory, precision, recomputation, input and local execution |
| Distributed training | 11–13 | Place state/computation and analyze exposed communication |
| Applied training and decision | 14–16 | Apply SFT/LoRA and GRPO, then produce a causal report |

## Lesson-by-lesson route

| Lesson | Topic | Competency to build | Practice at this stage |
| --- | --- | --- | --- |
| 1 | Model learning and training objectives | Distinguish training objectives and identify which parameters should change | Lab 32 learning mechanics; read-only previews: Labs 01, 05–07 |
| 2 | Causal training data | Construct labels, causal boundaries and valid-token accounting correctly | Lab 25 planning/mask checks; inspect Lab 13, whose loss/gradient checks follow Lesson 4 |
| 3 | Decoder architecture and information flow | Trace token tensors through attention and feed-forward layers to logits | Inspect Lab 01 forward shapes; full execution in Lesson 4 |
| 4 | The parameter-update lifecycle | Connect loss, gradients and optimizer state in one valid update | Labs 01 and 13; revisit Lab 25 mask structure; preview Lab 02 |
| 5 | Training evaluation and recovery | Restore every state component needed for an equivalent continuation | Lab 24 |
| 6 | Training memory and state lifetimes | Attribute peak memory to persistent state and transient activations | Inspect Lab 21's memory accounting; run precision variants in Lesson 7 |
| 7 | Numerical precision in training | Select formats while preserving finite gradients and useful learning signal | Lab 21; optional qualified Lab 22 |
| 8 | Memory savings through accumulation and recomputation | Distinguish effective-batch accumulation from activation recomputation | Labs 02 and 14 as separate matched-work comparisons |
| 9 | Training input readiness | Diagnose GPU starvation without changing which samples are consumed | Lab 26 |
| 10 | Training execution optimization | Optimize a stable local update using fusion, compilation and graph replay | Lab 30 operator profile, then Lab 27 |
| 11 | Distributed training-state ownership | Choose replicated or sharded state and normalize distributed gradients | Labs 00, 03, 04 |
| 12 | Model partitioning and communication | Trace tensor, pipeline, context and expert partitioning on bounded examples | Labs 12, 19, 29 |
| 13 | Gradient readiness and communication overlap | Explain when ready gradients can communicate alongside useful backward work | Lab 28 readiness, then Lab 33 real DDP buckets/hooks |
| 14 | Parameter-efficient adaptation | Apply supervision and adapter parameterization with explicit memory accounting | Labs 05, 13 |
| 15 | Reward-guided policy optimization | Explain grouped reward, policy ratios and the rollout-to-update loop | Labs 06, 07 |
| 16 | Evidence-based training optimization | Connect step evidence and one controlled change to a scoped decision | Reuse Lab 30 profiling skills; profile Lab 31's matched workload separately; run the Lab 31 campaign; optional matmul-only utilization, not full-model MFU |

## Readiness checkpoints

After Lesson 4, explain every transition from labels to parameter updates and revisit the masking/packing checks. After Lesson 10, attribute single-GPU time and memory before adding ranks. After Lesson 13, draw state placement and the communication critical path. SFT/LoRA and GRPO then apply these ideas; their toy labs do not require production-scale distributed infrastructure.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice labs**, and ends with a **Mental model**. The linked guides integrate
examples and commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Required evidence includes one-H100 correctness/performance, bounded two-node DDP/FSDP2, and a causal report over at least three independent trials. Optional Transformer Engine qualification is separate.
