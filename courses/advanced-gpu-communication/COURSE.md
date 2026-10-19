# Advanced Labs: Multi-GPUs Multi-Nodes communication optimization

Advanced Labs develops the ability to diagnose communication across GPUs,
network adapters, collectives and complete training or inference workloads.
Through guided comparisons and independent investigations, learn to quantify
exposed communication costs and justify a change using correctness, measurements
and traces.

Read [GPU Performance Tools](../gpu-performance-tools/index.html) before practical work.
For the full lab route, complete GPU Fundamentals, GPU Performance Optimization
and the relevant training or inference foundations, or bring equivalent readiness
to launch Slurm jobs, produce a correct unprofiled result and interpret its GPU timeline.
For Fundamentals Lesson 12, enter at Lab 02, then run Lab 08 after Lab 02 passes;
reading Fundamentals through that lesson supplies this assignment's concepts.
This is a labs-only course: each guide contains its own explanation and practice.

Practice requires two eight-H100 workers, sixteen GPUs in total, with local
NVLink/NVSwitch and inter-worker InfiniBand. Follow the shared
[Lab Preparation](../lab-guide.html#lab-preparation) and read its completion
summary. The single command prepares software; these labs qualify the fabric
and workload. The full route begins
with Lab 01 and qualifies placement and fabric in Labs 01–07. The Fundamentals
assignment uses Lab 02's readiness checks before Lab 08. Bounded experiments support scoped decisions; they do not
establish production convergence or large-model scaling.

For a tensor-reading refresher, use [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html), a concise visual course with commented examples and no labs.
