# Syllabus

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a qualified two-worker, sixteen-H100 cluster, which can also run the local labs with one-GPU allocations. The conceptual lessons here remain useful prerequisites.

Each native submission block prepares private log directories before calling `sbatch`; Slurm writes `results/<lab>/logs/<job>.out` and `.err`. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Before the first experiment, complete shared environment setup and read the unnumbered **Using GPU performance tools** lesson. Existing lesson IDs remain stable; distributed lab links use their new advanced-course identities. Each lab applies measure → inspect → predict → change one variable → measure again → explain.

Estimated guided time: **52 hours**, for the conceptual and local practical route; provisioning and queue time are excluded.

## Learning progression

Begin with the first lesson's **Objective**, then read **How it works** and its workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Every lesson follows **Objective → How it works → Practice → Mental model**. Start with [shared environment setup](../README.md#how-to-set-up-the-lab) once, then follow the lesson route. Each lab explains its purpose and needed concepts locally; Practice gives the commands and comparison to make. Use the syllabus to distinguish a reading preview from full execution.

First learn what a model and a parameter are, how a loss guides a weight update, and how inference differs. Then learn what is trained, how text becomes tensors, how the decoder produces logits and how one correct update changes state. Establish resume, memory, precision, recomputation, input and local execution skills before distributed training. Apply that foundation to SFT/LoRA and GRPO, then integrate the evidence in the capstone.

Follow the numbered conceptual route; distributed practice links open the dedicated advanced course. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Correct single-GPU training | 1–5 | Understand objective, batches, model, update and reproducible resume |
| Single-GPU performance | 6–10 | Explain memory, precision, recomputation, input and local execution |
| Applied training and decision | 14–16 | Apply SFT/LoRA and GRPO, then produce a causal report |
| Advanced distributed training | 11–13, 17 | Place state, analyze collectives and tune a fixed global workload |

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
| 11 | Distributed training-state ownership | Choose replicated or sharded state and normalize distributed gradients | [Advanced Lab 03](../advanced-gpu-communication/reference/labs/03_training_readiness.md); [Advanced Lab 15](../advanced-gpu-communication/reference/labs/15_ddp_train.md); [Advanced Lab 16](../advanced-gpu-communication/reference/labs/16_fsdp2_train.md) |
| 12 | Model partitioning and communication | Trace tensor, pipeline, context and expert partitioning on bounded examples | [Advanced Lab 17](../advanced-gpu-communication/reference/labs/17_training_expert_parallel.md); [Advanced Lab 18](../advanced-gpu-communication/reference/labs/18_training_tensor_parallel.md); [Advanced Lab 20](../advanced-gpu-communication/reference/labs/20_parallelism_mechanics.md) |
| 13 | Gradient readiness and communication overlap | Explain when ready gradients can communicate alongside useful backward work | [Advanced Lab 19](../advanced-gpu-communication/reference/labs/19_gradient_overlap.md); [Advanced Lab 21](../advanced-gpu-communication/reference/labs/21_ddp_buckets.md) |
| 14 | Parameter-efficient adaptation | Apply supervision and adapter parameterization with explicit memory accounting | Labs 05, 13 |
| 15 | Reward-guided policy optimization | Explain grouped reward, policy ratios and the rollout-to-update loop | Labs 06, 07 |
| 16 | Evidence-based training optimization | Connect step evidence and one controlled change to a scoped decision | Reuse Lab 30 profiling skills; profile Lab 31's matched workload separately; run the Lab 31 campaign; optional matmul-only utilization, not full-model MFU |
| 17 | Scaling a fixed training workload | Tune microbatch/accumulation with fixed global work and reference gradients | [Advanced Lab 22](../advanced-gpu-communication/reference/labs/22_fabric_training.md) |

## Readiness checkpoints

After Lesson 4, explain every transition from labels to parameter updates and revisit the masking/packing checks. After Lesson 10, attribute single-GPU time and memory before adding ranks. After Lesson 13, draw state placement and the communication critical path. The earlier base-route SFT/LoRA and GRPO labs require no distributed infrastructure. Finish the advanced route by comparing fixed-global-work runs in Lesson 17.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, and ends with a **Mental model**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Local completion requires one-H100 correctness/performance evidence and a causal report over at least three independent trials. Extend that evidence with DDP and FSDP2 in Advanced Labs 15–16 on the separately qualified fabric cluster. Optional Transformer Engine qualification is separate.
