# Where to Go Next

These optional reading directions combine
recent developments with established advanced concepts; they are not additional
lessons or lab requirements. The course remains H100-based. Check the linked
release and hardware requirements before experimenting in a separate environment:
current documentation does not mean that a feature is qualified in this course.
Start with the architecture comparison, then choose a direction that matches
the systems you expect to use.

## Blackwell architecture and portable performance reasoning

Blackwell is a newer NVIDIA GPU architecture that retains the CUDA execution
model but changes resource limits, memory capacities and supported operations.
Comparing its tuning guide with Hopper's helps you distinguish durable ideas,
such as locality and latency hiding, from chip-specific tuning decisions.
**Investigate**: Which assumptions in an H100 occupancy or roofline calculation
must be replaced when moving to a particular Blackwell GPU?
**Scope**: Newer-hardware comparison, not an H100 feature upgrade; different
Blackwell compute capabilities do not have identical resources.
[Read NVIDIA's Blackwell tuning guide](https://docs.nvidia.com/cuda/blackwell-tuning-guide/).

## Vera Rubin and rack-scale computing

NVIDIA's Vera Rubin platform combines CPUs, GPUs and interconnects into a
larger computing system. Its NVLink 6 fabric connects GPUs within a scale-up
system, while network technologies connect systems at scale-out. This extends
the course's topology vocabulary beyond reasoning about one GPU in isolation.
**Investigate**: Which communications stay inside the scale-up fabric, and
which cross a network boundary?
**Scope**: Current vendor platform direction; hardware, deployment availability
and topology must be checked separately. Two independent H100 nodes do not
reproduce this platform.
[Explore NVIDIA's Vera Rubin platform](https://www.nvidia.com/en-us/data-center/technologies/rubin/).

## Unified Memory, HMM and hardware coherence

Unified Memory provides a shared programming view of memory accessible to CPUs
and GPUs. Heterogeneous Memory Management (HMM) is Linux support for
coordinating CPU and device access to process memory, allowing supported GPUs
to use ordinary system allocations. Hardware coherence, available on systems
such as Grace Hopper, uses hardware to keep CPU/GPU views of shared data
consistent under the platform's memory rules. A page is a fixed-size region
managed by the virtual-memory system. Page migration moves a page's
physical backing between memory locations; remote access uses data where it
remains. These mechanisms have different costs. A common address space does
not make every access equally fast: placement and the interconnect still matter.
**Investigate**: How would you distinguish a local GPU-memory access, a remote
access and a page migration?
**Scope**: Established concept with evolving platform support. HMM requires an
appropriate OS/driver configuration; an ordinary H100 host is not a Grace Hopper
system.
[Study CUDA Unified Memory](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/unified-memory.html).

## CUDA Python 1.0 and explicit runtime control

CUDA Python exposes CUDA facilities through Python. Its 1.0 release develops
the lower-level path beneath tensor frameworks: applications can explicitly
manage devices, streams, memory and graphs through components such as
`cuda.core`. Understanding this layer helps explain which responsibilities
PyTorch normally handles for you.
**Investigate**: Who owns a device allocation and its lifetime when two Python
libraries share it?
**Scope**: Recent software direction, described in NVIDIA's May 26, 2026 CUDA
13.3 announcement. Package, toolkit and driver requirements still need checking;
no course dependency changes are required.
[Read the CUDA Python release overview](https://developer.nvidia.com/blog/nvidia-cuda-13-3-enhances-gpu-development-with-tile-programming-in-c-compiler-autotuning-and-python-updates).

## GPU confidential computing and attestation

Confidential computing aims to protect data while it is being processed,
not just while stored or transmitted. Attestation checks evidence about the
device's identity and security state before a workload trusts it. This adds
a security boundary to the course's operational-health model: a healthy GPU
and a trusted execution environment are different claims.
**Investigate**: What does successful GPU attestation establish, and what
application or host risks remain outside that evidence?
**Scope**: Established advanced security topic with evolving tooling. Supported
Hopper configurations exist, but require a qualified confidential-computing
platform; do not change cluster security modes for this reading exercise.
[Study NVIDIA GPU attestation](https://docs.nvidia.com/attestation/attestation-client-tools-sdk/latest/gpu_and_switch_attestation.html).
