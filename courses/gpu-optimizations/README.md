# GPU Performance Optimization

## Course guide

[Read the complete course](index.html) ·
[Syllabus](SYLLABUS.md) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster smoke runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **36 hours**.

## Learning order

Establish a workload contract, correct timing and a profiler hypothesis before making changes. Progress through local launch, graph, input, memory and library optimizations; diagnose local imbalance before qualifying the network in Lesson 11. Apply that evidence to scaling and overlap in Lesson 12. Finish with an evidence-backed library-first decision.

The [library-first lab](reference/labs/16_library_first_decision.md) retains BF16
and checks both implementations against an independent FP64 reference. Its
documented error budget accounts for intermediate BF16 rounding and rejects
invalid outputs before timing or publication.

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

Take this course after GPU Fundamentals. It teaches a general PyTorch
performance workflow before specialization in LLM training, LLM inference, or
custom CUDA kernels.

The lessons connect tool selection, measurement, memory, input pipelines,
distributed execution, and causal reporting. Use the complete
[diagnostic tooling setup](reference/tooling-setup.md) before profiler labs; it
defines allocation-context checks, tool ownership, permissions, evidence scope,
and safe installation boundaries.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/validate_course.py
```

This installs candidate constraints and runs source checks; it does not qualify
the environment for H100 execution. Before submitting a job, use the
owner-approved hash-locked environment or immutable image described in
[Versions and environment](VERSIONS.md), then follow the relevant gates in the
[cluster smoke runbook](reference/cluster-smoke-test.md). Stop if that environment
identity is unavailable. Set the submitting shell's file mask before submission;
the job's internal mask cannot protect scheduler output created earlier.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/01_timing_basics.py --profile smoke
```

Use the Nsight launchers only after the baseline and question are frozen. The
two-node launcher supports preflight, the Lab 17 PyTorch communication sweep, distributed scaling and overlap. Lab 18 uses the separate MPI-aware `slurm/nccl_tests.sbatch` launcher after the external NVIDIA NCCL Tests build has been qualified. Start with default transport selection, collect separate diagnostic and timing runs, and change only one job-local factor. RDMA and GPUDirect RDMA experiments are conditional; the course never configures the fabric.

## Begin with the concepts

Start with the first lesson's **Start here** section and its inline workflow diagram. It defines the subject and essential vocabulary, explains why it is useful, and walks through a small example before advanced engineering details. Follow the syllabus checkpoints for the beginner route; experienced readers can use those checkpoints to identify what they already understand.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.

## Complete transfer pipelines

[Lab 19](reference/labs/19_h2d_pipeline.md) extends Lesson 6 with a bounded,
event-ordered H2D input ring. [Lab 20](reference/labs/20_d2h_pipeline.md) extends
Lesson 8 with output workers, pinned pooling, nonblocking copies and an egress
stream. Both check complete outputs and time the final drain. Measure unprofiled
runs independently from short NVTX captures; overlap and speed remain target
evidence, not consequences of selecting a mode.
