# GPU Fundamentals

[Read the course](index.html) · Estimated guided time: **21 hours**.

Build a mental model of GPU execution, memory, precision and communication.
Read [GPU Performance Tools](../gpu-performance-tools/index.html) and
[PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
first; basic Python and Linux knowledge are enough to begin.

## Learning route

Follow the lessons in order: CPU/GPU cooperation and timing, the software and
execution stack, memory and occupancy, then access patterns, precision,
roofline and read-only health. Lab numbers identify files, not execution order.

- After Lesson 3, explain the CPU-to-device path and a trustworthy timer.
- After Lesson 7, distinguish memory layout, active lanes and block residency.
- After Lesson 10, derive a roofline bound before comparing measurements.

## Before the first lab

Use one full, non-MIG H100 per local lab. A qualified advanced cluster can
also provide these one-GPU allocations. Follow the
[Lab Guide](../lab-guide.html#lab-preparation-scripts), check
[Versions and environment](VERSIONS.md), then run from this course directory.
Complete Lab 10 successfully before submitting Lab 01:

```bash
sbatch slurm/10_compatibility_stack.sbatch --workload small
sbatch slurm/01_cpu_gpu_crossover.sbatch --workload small
```

At Lesson 12, use [Advanced Lab 02](../advanced-gpu-communication/reference/labs/02_collective_readiness.md)
then [Advanced Lab 08](../advanced-gpu-communication/reference/labs/08_distributed_collectives.md)
for distributed practice. They require the advanced course's qualified
sixteen-H100 fabric; two one-GPU TCP workers do not qualify that path.

## Completion

Complete the local labs with their correctness checks. Explain a measured
result using timing, hardware and health evidence, keeping observation,
inference and unverified hypothesis separate.

Use [Where to Go Next](NEXT-STEPS.md) for optional reading after the core route.

## Reference guides

- [Versions and environment](VERSIONS.md): runtime identities and qualification limits.
- [Readiness checks](reference/cluster-smoke-test.md): checks before interpreting results.
- [Lab mechanisms and evidence](reference/lab-mechanisms.md): worked interpretation and scope.
- [Benchmark worksheet](reference/benchmark-record.md): compare equivalent work.
- [Evidence and privacy](reference/evidence-security.md): retain raw results privately and share sanitized summaries.
- [Glossary](GLOSSARY.md): terms used throughout the course.
