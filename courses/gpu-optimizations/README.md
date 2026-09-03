# GPU Performance Optimization

A practical course for diagnosing and improving GPU workloads using a controlled, evidence-led loop rather than a catalog of tuning tips.

## Start here

1. Read [MISSION.md](MISSION.md).
2. Open the self-contained [index.html](index.html).
3. Use [COURSE.md](COURSE.md) and [SYLLABUS.md](SYLLABUS.md) as the learning route.
4. Obtain the approved locked environment described in [VERSIONS.md](VERSIONS.md).
5. Review the [source-coverage map](reference/source-coverage.md), then execute the [cluster smoke-test](reference/cluster-smoke-test.md) when the H100 cluster is ready.
6. Read [evidence security and safe sharing](reference/evidence-security.md) before collecting or sharing runtime artifacts.

## Runtime contract

The course targets a Slurm cluster with two nodes and one full non-MIG H100 per node. One-GPU diagnosis runs in a single-node allocation. Scaling labs request both nodes and launch one rank per node with Slurm and `torchrun`. `requirements.txt` records the direct compatibility constraint; it is not a transitive dependency lock. Live use requires a cluster-approved hash-locked file or immutable environment image.

For isolated local validation, create the course environment from this
directory and install its declared dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

This local environment supports offline validation; it does not prove the
cluster's CUDA, H100, NCCL, Slurm, or profiler runtime.

## What is included

- 19 foundations-first modules covering the end-to-end performance system, five primary bottleneck classes, tool installation and use, benchmark design, targeted techniques, distributed scaling, and decision reporting.
- 15 runnable PyTorch labs, including a profile-ready bottleneck workshop, shape/precision, SDPA, allocator-lifetime, and two-node overlap studies.
- Practical PyTorch Profiler, NVTX, Nsight Systems, Nsight Compute/roofline, DCGM/DCGM Exporter, `nvbandwidth`, NCCL Tests, vLLM Bench, GenAI-Perf/AIPerf, and MLPerf guidance with explicit evidence boundaries.
- One-node and two-node Slurm launchers.
- A compute-node tooling preflight and a detailed [tooling setup guide](reference/tooling-setup.md).
- Accessible embedded diagrams and exact lab-source copies in the HTML.
- An ordered live smoke-test and reproducible benchmark template.

## Validate without a GPU

```bash
.venv/bin/python tools/validate_course.py
```

The validator checks the teaching structure, embedded-source parity, Python
syntax, static help contracts, security-sensitive publication rules, and Slurm
launcher declarations. It does not execute labs or launchers, and it does not
validate CUDA execution or performance.

From the parent `courses` directory, run the offline behavioral contracts with:

```bash
PYTHONDONTWRITEBYTECODE=1 gpu-optimizations/.venv/bin/python -m pytest
```

Shared result writers treat every literal `false` correctness entry as a failed
lab and refuse to publish that result. Every JSON artifact uses the canonical
`gpu-course-result/v1` schema and a random course run ID rather than a scheduler
job ID.

## Optimization rule

Change one factor at a time. Keep a change only when correctness, the primary end-to-end metric, memory constraints, and repeatability all pass. A faster isolated kernel is not automatically a faster workload.
