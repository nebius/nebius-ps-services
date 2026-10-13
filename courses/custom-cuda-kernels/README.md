# Custom CUDA Kernels for GPU Optimization

[Read the course](index.html) · Estimated guided time: **36 hours**.

Learn when a custom kernel is justified, then build and validate it against a
library reference. Take GPU Fundamentals and GPU Performance Optimization
first. Read [GPU Performance Tools](../gpu-performance-tools/index.html) for
profiling and [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
when you need a tensor-reading refresher. Learner code here is CUDA C++20.

## Learning route

Start with a library-first decision, a reproducible SM90 build and one correct
vector kernel. Learn validation, memory, reduction and resource limits before
combining mechanisms in pipelines and RMSNorm.

- After Lesson 4, demonstrate an edge case, explain memcheck's scope and select
  one profiling question. Revisit racecheck/synccheck for shared-memory work.
- After Lesson 10, distinguish limited resources from uneven work.
- Lesson 14 completes the required single-GPU path. Lessons 15–16 are optional.
  Lab 10's Hopper thread-block clusters stay inside one GPU; cross-GPU work
  belongs to [Advanced Labs](../advanced-gpu-communication/index.html).

## Before the first lab

Use one full H100 and the approved CUDA runtime from the
[Lab Guide](../lab-guide.html#lab-preparation-scripts) and
[Versions and environment](VERSIONS.md). Preparation compiles SM90 binaries and
Lab 10's separate SM90a target without a GPU; these builds are not GPU qualified.
Use [Lab 13](reference/labs/13_h100_preflight.md) in a GPU allocation before the
other kernel labs:

```bash
sbatch slurm/13_h100_preflight.sbatch --workload small
```

A supported H200 substitute establishes evidence only for that observed device,
not H100 performance. Keep the same GPU identity across capstone trials.
CUTLASS is required for Lab 09's fused bias/ReLU reference; disabling it is not
acceptance evidence. The optional container build-and-test exercise is described
in the Lab Guide.

## Completion

Preserve independent reference checks, relevant sanitizer results, focused
profiler evidence and repeated uninstrumented trials. Select memcheck,
racecheck, initcheck or synccheck for the failure being investigated; sanitizer
timings are diagnostic overhead. Distributed CUDA/NCCL is outside this course.

Use [Where to Go Next](NEXT-STEPS.md) for optional reading after the core route.

## Reference guides

- [Versions and environment](VERSIONS.md): runtime identities and qualification limits.
- [Readiness checks](reference/cluster-smoke-test.md): checks before interpreting results.
- [Lab mechanisms and evidence](reference/lab-mechanisms.md): worked interpretation and scope.
- [Benchmark worksheet](reference/benchmark-record.md): compare equivalent work.
- [Evidence and privacy](reference/evidence-security.md): retain raw results privately and share sanitized summaries.
- [Glossary](GLOSSARY.md): terms used throughout the course.
