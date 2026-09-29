# LLM Training

## Hardware routes

The **base route** uses two workers with one H100 each. Its TCP/IP inter-node path is not representative of GPU-fabric optimization; run single-GPU exercises there.

Distributed practical work now belongs to [Advanced Labs: Multi-GPUs Multi-Nodes communication optimization](../advanced-gpu-communication/index.html). That course requires a qualified two-worker, sixteen-H100 cluster, which can also run the local labs with one-GPU allocations. The conceptual lessons here remain useful prerequisites.

Every submission uses `tools/submit_lab.py`; it creates private `results/<lab>/logs/<job>.out` and `.err` before calling Slurm. Result JSON remains the authoritative experiment record. `small` and `large` select workload presets, independently of the baseline/candidate choice. Qualification, modeling and fixed server experiments can use identical effective parameters in both profiles; read the lab guide and result configuration before comparing them.

Start with [shared environment setup](../README.md#how-to-set-up-the-lab) to prepare the cluster, course runtime, Nsight tools, private Grafana, and readiness checks.

Read [Using GPU performance tools](reference/performance-tools.md) before the first experiment. Every lab includes its own Grafana dashboard, local capture commands, a correctness gate, and a selected-result comparison. Install the shared tools once in shared environment setup and keep `small` and `large` as separate workload campaigns.

## Course guide

[Read the complete course](index.html) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster small runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **52 hours**, for the conceptual and local practical route; provisioning and queue time are excluded.

## Learning order

First learn what is trained, how text becomes tensors, how the decoder produces logits and how one correct update changes state. Establish resume, memory, precision, recomputation, input and local execution skills before distributed training. Apply that foundation to SFT/LoRA and GRPO, then integrate the evidence in the capstone.

Use each lesson’s Objective and Practice for the current activity; the
readiness checkpoints below help you decide when to continue. Lab numbers identify files; follow lesson order rather
than running every lab numerically. Previews and optional branches are labeled.

## Getting started

The HTML course keeps diagrams beside the relevant explanation in a wide,
responsive layout. Each diagram fits the page and retains a caption and
accessible SVG description. Each lab starts with a concise purpose and explains the needed concepts,
code structure, supported experiments, result checks, investigation,
troubleshooting and takeaways locally. Use the side-panel contents to
jump between topics.

Take GPU Fundamentals and GPU Performance Optimization first. This standalone
course owns training mechanics and training-specific GPU optimization.

The lessons follow training objectives, correct batches, model execution,
state and memory, adaptation, parallelism, and profiler-driven optimization.
Inference serving uses its own course and isolated environments.

Prepare and restore the runtime using the [shared guide](../README.md#how-to-run-the-labs). Before
submitting a job, use the approved environment and immutable identity recorded
in [Versions and environment](VERSIONS.md), then complete the relevant
[cluster small gates](reference/cluster-smoke-test.md). Keep target qualification
pending until those checks run successfully. Restrict files in the submitting
shell before Slurm creates its output.

Begin with Lesson 1 and [Lab 32’s CPU learning exercise](reference/labs/32_learning_basics.md). After completing Lessons 1–4, run the following transformer small experiment using [Lab 01’s guide](reference/labs/01_tiny_transformer_train.md).

```bash
umask 077
python3 tools/submit_lab.py --lab 01_tiny_transformer_train slurm/single_gpu.sbatch labs/01_tiny_transformer_train.py --profile small
```

Transformer Engine is optional and must match the installed PyTorch/CUDA ABI.
Follow [Lab 22 runtime qualification](reference/labs/22_transformer_engine_fp8.md)
to check its build and FP8 runtime compiler before the full experiment.
Training and inference-serving dependencies are intentionally not combined.

The final optimization decision uses `slurm/capstone_three_trials.sbatch`.
It launches three fresh Python processes with distinct seeds, alternates
baseline/candidate order, validates every result, and writes one scoped
aggregate keep/reject record for the learner's causal report.
Aggregation retains the observed GPU family in its claim and rejects trials
with different hardware or software environments.

Labs 27 and 31 validate finite gradients and reconstruct the expected plain-SGD
update from each initial state. Their acceptance gates reject skipped backward
or optimizer work even when the resulting parameter difference is small.

## Begin with the concepts

Lab 32 is a download-free CPU introduction to learning a weight; use `"$COURSE_PYTHON" labs/32_learning_basics.py --device cpu` in the course environment. Its explicit `--device cuda` path is for an allocated H100. Continue with the transformer labs after the conceptual checks.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

## DDP communication practice

[Lab 21](../advanced-gpu-communication/reference/labs/21_ddp_buckets.md) follows Advanced Lab 19 with real DDP bucket
caps and allreduce, FP16, BF16 and PowerSGD hooks. It records actual bucket
bytes, joined-step samples and numerical trajectories against a full-batch FP32
reference. Compression results remain candidates until convergence on the target task and
performance in independent H100 trials are evaluated.

Compute captures for Labs 02, 06, 07, 14, 21, 22 and 30 select the operation named in each guide, excluding input/model initialization. Most select a model matrix kernel; Lab 06 selects objective reduction work. First-generation, baseline or BF16 diagnostics do not prove every backward pass, precision mode or update. Keep the complete Systems traces and clean numerical checks with each comparison.

Nsight Systems capture commands and report views are assigned per lab in `reference/observability.json` and repeated in each lab guide. Shared setup imports each course dashboard directory once; CPU-only and protocol-only results omit unrelated GPU telemetry. Explicit profiling exceptions explain which evidence to use instead. Keep captures separate from the unprofiled result pair.

Capstone aggregation requires `experiment.instrumented` to be exactly `false`
in every input and rejects explicit diagnostic timing. Missing or malformed
provenance requires a fresh unprofiled trial; no aggregate is written from
rejected inputs.

## Readiness checkpoints

After Lesson 4, explain every transition from labels to parameter updates and revisit the masking/packing checks. After Lesson 10, attribute single-GPU time and memory before adding ranks. After Lesson 13, draw state placement and the communication critical path. The earlier base-route SFT/LoRA and GRPO labs require no distributed infrastructure. Finish the advanced route by comparing fixed-global-work runs in Lesson 17.

Every lesson starts with its **Objective**, teaches definitions and mechanisms
in **How it works**, links its **Practice**, summarizes the teaching in **Mental model**, then provides **Where to Go Next** and a **Glossary**. The linked guides integrate
worked examples in **Concepts and code path** and concise commands in **Practice**, with H100 scope, trade-offs, evidence,
failure analysis and review in their relevant sections. Before moving on,
explain the new mechanism and its limitation in your own words; a completed
command alone is not evidence of understanding.

## Completion

Local completion requires one-H100 correctness/performance evidence and a causal report over at least three independent trials. Extend that evidence with DDP and FSDP2 in Advanced Labs 15–16 on the separately qualified fabric cluster. Optional Transformer Engine qualification is separate.
