# Dependency compatibility window

Content was statically reviewed on 2026-09-01 against current NVIDIA, PyTorch, and pytest documentation. Examples target Python 3.12 and directly constrain PyTorch 2.13.0, with pytest 9.1.1 as the offline test runner; they require an NVIDIA H100 (SM90). This is a compatibility window, not a complete transitive lock.

Before live labs, the cluster owner must provide either a platform-specific
hash-locked dependency file installed from an approved source or an immutable
environment image identified by digest. Record that lock or digest in the
private run record. Do not resolve `requirements.txt` directly against an
arbitrary or mutable package index on a managed cluster.

Nsight tools, DCGM, the CUDA driver, NCCL, fabric libraries, and Slurm integration are cluster-owned. Record their actual versions. Profiler report formats, section sets, and available counters vary by tool version and permissions, so use the documented concepts and revalidate exact commands on the target cluster.

GenAI-Perf is retained only for reproducing an existing benchmark workflow; its official project is phasing it out in favor of AIPerf. Pin and record the exact client version and request configuration. Install benchmark clients from an official package index or cluster-approved mirror using an approved exact version or hash-locked requirements file. Start new NVIDIA inference benchmark automation with a current AIPerf release, and do not compare client results until metric definitions and request distributions are confirmed equivalent.
