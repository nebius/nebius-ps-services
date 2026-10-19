# GPU Performance Optimization

[Read the course](index.html) · Estimated guided time: **44 hours**.

Learn to find a bottleneck, change one factor and decide whether the result
justifies keeping the change. Take GPU Fundamentals first; use
[GPU Performance Tools](../gpu-performance-tools/index.html) for profiler concepts
and [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
for a tensor-reading refresher.

## Learning route

Read measurement (Lessons 1–3), local optimization (4–10), communication and
scaling (11–12), library-first decisions (13), then fabric concepts (14–16).
Follow each lesson's lab links rather than numerical lab order.

- After Lesson 3, state a bottleneck hypothesis and a disconfirming observation.
- After Lesson 10, distinguish workload idle time from scheduler delay.
- Use Lab 19 for a complete input-transfer pipeline and Lab 20 for output
  transfer and consumption; verify every output and include the final drain.
- Complete the local capstone before extending the investigation to a fabric.

## Before the first lab

Use one full, non-MIG H100 per local lab; a qualified advanced cluster can
supply one-GPU allocations. Follow the [Lab Guide](../lab-guide.html#lab-preparation)
to synchronize sources and prepare the available lab software. Check the setup
summary for failures or hardware skips, then review
[Versions and environment](VERSIONS.md) and the
[readiness checks](reference/cluster-smoke-test.md), then run:

```bash
sbatch slurm/01_timing_basics.sbatch --workload small
```

Distributed transport, scaling and profiling practice belongs to
[Advanced Labs](../advanced-gpu-communication/index.html), which qualifies
the two-worker, sixteen-H100 fabric before those experiments.

## Completion

Complete the profiler-backed capstone with a baseline, one controlled change,
correctness checks, independent repeated measurements and a scoped keep/reject
decision. Diagnostic captures explain behavior; use unprofiled runs for timing.

Use [Where to Go Next](NEXT-STEPS.md) for optional reading after the core route.

## Reference guides

- [Versions and environment](VERSIONS.md): runtime identities and qualification limits.
- [Readiness checks](reference/cluster-smoke-test.md): checks before interpreting results.
- [Lab mechanisms and evidence](reference/lab-mechanisms.md): worked interpretation and scope.
- [Benchmark worksheet](reference/benchmark-record.md): compare equivalent work.
- [Evidence and privacy](reference/evidence-security.md): retain raw results privately and share sanitized summaries.
- [Glossary](GLOSSARY.md): terms used throughout the course.
