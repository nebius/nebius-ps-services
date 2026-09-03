# GPU Fundamentals for Performance Engineering

A self-contained, evidence-first course that teaches the GPU mental models needed for responsible performance work on NVIDIA H100 systems.

## Start here

1. Read [MISSION.md](MISSION.md) for the learner contract and outcomes.
2. Open [index.html](index.html) in a browser; it is the complete portable course.
3. Follow [COURSE.md](COURSE.md) and [SYLLABUS.md](SYLLABUS.md) in order.
4. Prepare a cluster-approved environment that satisfies the declared version baseline in [VERSIONS.md](VERSIONS.md).
5. Read [evidence security and safe sharing](reference/evidence-security.md), review the [source-coverage map](reference/source-coverage.md), then run the [cluster smoke-test](reference/cluster-smoke-test.md) before broader experiments.

## Course environment

The lab contract is a Slurm cluster with two worker nodes and one full non-MIG H100 per node. All GPU code runs inside a Slurm allocation. Local-effect labs reserve one node; distributed labs request both nodes and use `torchrun` with one process per node.

The course does not assume the cluster has already been created. Static validation is included in the repository; live H100, NCCL, and timing acceptance must be completed on the future cluster.

For isolated local validation, create the course environment from this
directory and install its declared dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

This local environment supports offline validation; it does not prove the
cluster's CUDA, H100, NCCL, Slurm, or Triton runtime.

## What is included

- 15 numbered modules, each delivered as one lesson, that build from CPU/GPU foundations through H100 device and SM architecture, measurement, data movement, arithmetic, and two-node scale.
- Embedded, accessible SVG diagrams for the H100 hierarchy, one Hopper SM, execution and scheduling, memory and coalescing, overlap, Tensor Core eligibility, roofline reasoning, topology, and diagnosis.
- Ten runnable Python/PyTorch labs, including a Triton launch-geometry exercise, two-node preflight, and NCCL collectives.
- Slurm launchers for isolated one-GPU and two-node runs.
- A benchmark-record template and ordered smoke-test runbook.
- Official NVIDIA, PyTorch, and Slurm references collected only at the end of the course.

## Validate the artifact

```bash
.venv/bin/python tools/validate_course.py
```

This validates structure, embedded-source parity, Python syntax, dependency-free help, and launcher contracts. It does not execute CUDA work.

From the parent `courses` directory, run the offline behavioral contracts with:

```bash
PYTHONDONTWRITEBYTECODE=1 gpu-fundamentals/.venv/bin/python -m pytest
```

## Result discipline

Labs fail when a declared correctness gate is false. Every JSON artifact uses
the canonical `gpu-course-result/v1` schema and a random course run ID; portable
results never contain scheduler job IDs. Result directories and files are
created private and exclusively, so an existing artifact is never overwritten.
Keep the private Slurm output, result JSON, prediction, correctness result, and
environment metadata together. Share only a separately reviewed summary that
follows [the evidence-security guide](reference/evidence-security.md). Never
report an optimization from a timing change alone.
