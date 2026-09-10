# LLM Training

## Course guide

[Read the complete course](index.html) ·
[Syllabus](SYLLABUS.md) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster smoke runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **48 hours**.

## Learning order

First learn what is trained, how text becomes tensors, how the decoder produces logits and how one correct update changes state. Establish resume, memory, precision, recomputation, input and local execution skills before distributed training. Apply that foundation to SFT/LoRA and GRPO, then integrate the evidence in the capstone.

Use the [lesson-by-lesson syllabus](SYLLABUS.md) for the current activity and
readiness checkpoints. Lab numbers identify files; follow lesson order rather
than running every lab numerically. Previews and optional branches are labeled.

## Getting started

The HTML course keeps diagrams beside the relevant explanation in a wide,
responsive layout. Each diagram fits the page and retains a caption and
accessible SVG description. Each lab explains setup, concepts, code structure,
supported experiments, result checks, investigation, troubleshooting, and
takeaways, with links to its related lessons. Use the side-panel contents to
jump between topics.

Take GPU Fundamentals and GPU Performance Optimization first. This standalone
course owns training mechanics and training-specific GPU optimization.

The lessons follow training objectives, correct batches, model execution,
state and memory, adaptation, parallelism, and profiler-driven optimization.
Inference serving uses its own course and isolated environments.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/validate_course.py
```

These are candidate dependencies, not an H100-qualified environment. Before
submitting a job, use the approved environment and immutable identity recorded
in [Versions and environment](VERSIONS.md), then complete the relevant
[cluster smoke gates](reference/cluster-smoke-test.md). Keep target qualification
pending until those checks run successfully. Restrict files in the submitting
shell before Slurm creates its output.

Begin with Lesson 1 and [Lab 32’s CPU learning exercise](reference/labs/32_learning_basics.md). After completing Lessons 1–4, run the following transformer smoke experiment using [Lab 01’s guide](reference/labs/01_tiny_transformer_train.md).

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/01_tiny_transformer_train.py --profile smoke
```

Transformer Engine is optional and must match the installed PyTorch/CUDA ABI.
Training and inference-serving dependencies are intentionally not combined.

The final optimization decision uses `slurm/capstone_three_trials.sbatch`.
It launches three fresh Python processes with distinct seeds, alternates
baseline/candidate order, validates every result, and writes one scoped
aggregate keep/reject record for the learner's causal report.

Labs 27 and 31 validate finite gradients and reconstruct the expected plain-SGD
update from each initial state. Their acceptance gates reject skipped backward
or optimizer work even when the resulting parameter difference is small.

## Begin with the concepts

Lab 32 is a download-free CPU introduction to learning a weight; use `python3 labs/32_learning_basics.py --device cpu` in the course environment. Its explicit `--device cuda` path is for an allocated H100. Continue with the transformer labs after the conceptual checks.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

## DDP communication practice

[Lab 33](reference/labs/33_ddp_buckets.md) follows Lab 28 with real DDP bucket
caps and allreduce, FP16, BF16 and PowerSGD hooks. It records actual bucket
bytes, joined-step samples and numerical trajectories against a full-batch FP32
reference. Compression results remain candidates until convergence on the target task and
performance in independent H100 trials are evaluated.
