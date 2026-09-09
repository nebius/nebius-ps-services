# Syllabus

Estimated guided time: **47 hours**.

## Learning progression

Begin with the first lesson's **Start here** explanation and workflow diagram. It defines the subject, explains why it matters and how it works, and walks through a small example before introducing detailed engineering requirements. No prior CUDA or model-training expertise is assumed in that introduction. The specialized courses still use Fundamentals and Optimizations as their practical prerequisites.

Each later lesson begins with **What it is** before objectives or applications. Read the definition and mechanism, open a **Practice labs** link, then follow the lab's **Theory preparation** in **Before you start**. Its named lessons explain the techniques before full execution; **Concepts and code path** connects them to the implementation. Before running, explain what each technique does, why it is used, how its inputs and dependencies work, and what timing and numerical checks establish. A preview is reading only; a later revisit adds a new interpretation without making an untaught technique a hidden prerequisite for the first run.

First understand fixed-parameter prediction and the token-generation loop in Lab 35. Audit artifacts before loading them, follow generation and fix sampling semantics, then calculate cache capacity and define the workload. Learn basic engine lifecycle before client metrics or live policy experiments. Establish scheduling and an uncompressed attention baseline before quantization, speculation, parallelism and advanced serving.

Follow the lesson order below. Lab numbers are identifiers, not the execution order;
use each lesson's assigned activity and the lab guide's prerequisites. A preview
means inspecting the explanation or code without running an advanced experiment.
Return to a repeated lab when the later lesson adds a new interpretation or check.

| Part | Lessons | Practical outcome |
| --- | --- | --- |
| Correct request mechanics | 1–5 | Control artifact identity, generation, sampling, cache capacity and workload |
| Engine and scheduling baseline | 6–10 | Start safely, measure clients, and study paging, batching and prefix reuse |
| Targeted optimization | 11–14 | Compare attention, quantization, speculation and parallel placement |
| Integrated evaluation | 15–16 | Design benchmark campaigns and deliver a causal report |

## Lesson-by-lesson route

| Lesson | Topic | Competency to build | Practice at this stage |
| --- | --- | --- | --- |
| 1 | Model inference and artifact preparation | Establish trusted model identity and compatible metadata before loading | Lab 35 fixed-model mechanics; Lab 16 metadata audit |
| 2 | Autoregressive generation | Trace tokenization, first-token production, cached decoding and stopping | Lab 09; preview Lab 08's cache shapes for Lesson 4 |
| 3 | Decoding policy and output quality | Fix sampling and quality semantics before comparing optimization variants | Lab 17 |
| 4 | Attention-cache capacity | Derive per-token KV bytes from model heads, layers and format | Labs 08, 26 |
| 5 | Inference workload shape | Specify prompt length, output length and concurrency as a workload | Labs 18, 25 |
| 6 | Model-serving architecture | Launch, probe, warm and stop a controlled single-GPU serving engine | Lab 10; Lab 30 probe after engine setup |
| 7 | Serving latency and useful throughput | Separate client metric boundaries and interpret throughput versus goodput | Labs 11 and 15; introductory AIPerf profile |
| 8 | Attention-cache allocation and reclamation | Track physical cache blocks through growth, release and reuse | Lab 27 |
| 9 | Request scheduling and prompt chunking | Compare scheduling policies with conserved work and real-engine evidence | Labs 28, 34; reuse Lesson 5's Lab 18 padding evidence |
| 10 | Prefix reuse and cache retention | Explain when identical prefixes can safely reuse cached state | Lab 36 CPU retention model, then Labs 20, 34 live prefix policies |
| 11 | Efficient attention execution | Establish the actual attention backend for fixed prefill/decode shapes | Lab 24 |
| 12 | Quantized inference representations | Measure representation savings with numerical and quality checks | Lab 29 |
| 13 | Speculative generation | Verify draft acceptance and recovery while preserving output semantics | Labs 23, 33 |
| 14 | Distributed inference placement | Partition models or requests and measure the resulting serving behavior | Labs 00, 12, 19; batch-one control in Lab 19 |
| 15 | Serving workloads and phase separation | Design token-aware campaigns and bound optional disaggregation claims | Lab 30 revisit; AIPerf campaign and optional disaggregation study |
| 16 | Evidence-based inference optimization | Distinguish attention microbenchmarks from end-to-end serving acceptance | Lab 32 |

## Readiness checkpoints

After Lesson 5, freeze a model, sampling contract, KV estimate and workload matrix. After Lesson 7, distinguish client latency from model-forward timing and run the introductory AIPerf profile before policy A/B campaigns. After Lesson 12, compare scheduling/backend/quantization changes one at a time. Dynamo disaggregation is an optional advanced branch, never a core completion gate.

Every lesson retains its definition, prerequisite bridge, mental model and
mechanism, followed by **Practice labs** links. The linked guides integrate
examples and commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Keep mechanics and serving evidence separate. The capstone requires at least three equivalent-work trials and an honest quality/latency/memory decision; advanced engines require their own target qualification.
