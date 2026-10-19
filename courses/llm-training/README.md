# LLM Training

[Read the course](index.html) · Estimated guided time: **52 hours**.

Connect the training objective to correct updates, memory use and GPU
performance. Take GPU Fundamentals and GPU Performance Optimization first.
Use [GPU Performance Tools](../gpu-performance-tools/index.html) for profiler
concepts and [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
for a tensor-reading refresher.

## Learning route

Begin with learning a weight, tokenization, the decoder and one correct update.
Then study resume, memory, precision, recomputation and data pipelines before
distributed training, adaptation and the capstone.

- After Lesson 4, explain the path from labels to parameter updates and check
  masking and packing boundaries.
- After Lesson 10, attribute single-GPU time and memory before adding ranks.
- After Lesson 13, draw state placement and the communication critical path.
- SFT/LoRA and GRPO practice can run locally; distributed practice has its own
  qualification in [Advanced Labs](../advanced-gpu-communication/index.html).

## Before the first lab

Follow the [Lab Guide](../lab-guide.html#lab-preparation) and
[Versions and environment](VERSIONS.md) for the approved runtime. Local GPU
labs use one full H100; a qualified advanced cluster can supply that allocation.

Start Lesson 1 with [Lab 32](reference/labs/32_learning_basics.md), a download-free
CPU exercise: `"$COURSE_PYTHON" labs/32_learning_basics.py --device cpu` in the
prepared course environment. After Lessons 1–4, follow
[Lab 01](reference/labs/01_tiny_transformer_train.md) for the first transformer run:

```bash
sbatch slurm/01_tiny_transformer_train.sbatch --workload small
```

Lab 22 is an optional FP8 extension. Default preparation includes its isolated
Transformer Engine runtime. Qualify its matching PyTorch/CUDA environment and
hardware separately;
installation alone does not pass that gate.

## Completion

Use Lab 31's trials experiment for at least three independent, unprofiled
runs. Require finite gradients, valid updates, equivalent work and matching
hardware/software before writing a causal report. DDP and FSDP2 practice
continues in Advanced Labs 15–16 on the qualified fabric.

Use [Where to Go Next](NEXT-STEPS.md) for optional reading after the core route.

## Reference guides

- [Versions and environment](VERSIONS.md): runtime identities and qualification limits.
- [Readiness checks](reference/cluster-smoke-test.md): checks before interpreting results.
- [Lab mechanisms and evidence](reference/lab-mechanisms.md): worked interpretation and scope.
- [Benchmark worksheet](reference/benchmark-record.md): compare equivalent work.
- [Evidence and privacy](reference/evidence-security.md): retain raw results privately and share sanitized summaries.
- [Glossary](GLOSSARY.md): terms used throughout the course.
