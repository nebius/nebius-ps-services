# GPU Fundamentals

Start Lesson 1 with the whole H100 SXM 80 GB: SMs, L2 cache and HBM, then distinguish physical hardware from grids, blocks, warps and threads, including how block size limits residency. Open Lab 10 for readiness checks and Lab 01 for its formula, timing procedure and numerical acceptance.

## Course guide

[Read the complete course](index.html) ·
[Syllabus](SYLLABUS.md) ·
[Glossary](GLOSSARY.md) ·
[Versions and environment](VERSIONS.md) ·
[Cluster smoke runbook](reference/cluster-smoke-test.md) ·
[Benchmark worksheet](reference/benchmark-record.md) ·
[Lab mechanisms and evidence](reference/lab-mechanisms.md)

Estimated guided time: **21 hours**.

## Learning order

Start with CPU/GPU boundaries and trustworthy timing, then identify the software stack and H100 execution hierarchy. Define memory resources before SIMT and occupancy, connect access patterns and precision to roofline, and finish with read-only health, networking layers and bounded two-node communication. Learn GPU/NIC attachment, NVLink/NVSwitch, InfiniBand/RoCE, RDMA and GPUDirect RDMA before interpreting NCCL collectives.

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

Start here. The course builds the mental model used by every later performance
course. Read `COURSE.md`, use `index.html` for the self-contained visual
version, and run labs only through the supplied Slurm launchers.

The course explains architecture, execution, timing, memory, precision, and
two-node communication through worked examples and practical labs.

## Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/validate_course.py
```

PyTorch 2.14 is the manifest target. Clean Linux installation and H100
qualification remain pending; no fallback version is approved. Complete the
documented environment checks and H100 smoke path before publication.

## First single-GPU run

After environment setup, run the compatibility check before the CPU/GPU
comparison. Confirm that each job succeeds before continuing.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/10_compatibility_stack.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_cpu_gpu_crossover.py --profile smoke
```

## At Lesson 12

Run the two-node preflight before the collective experiment. These jobs belong
to the later communication lesson, not the first single-GPU session.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
sbatch slurm/two_node.sbatch labs/06_distributed_collectives.py --profile smoke
```

Cluster creation, credentials, drivers, scheduler administration, and GPU
configuration are outside the course.

## Continue learning

After the core course, use [Where to Go Next](NEXT-STEPS.md) for optional
reading on current technologies and advanced concepts. Each entry
includes a study question, official sources and hardware or maturity limits;
these directions do not change the required labs or environment.
