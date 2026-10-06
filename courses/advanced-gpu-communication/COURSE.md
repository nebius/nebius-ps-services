# Advanced Labs: Multi-GPUs Multi-Nodes communication optimization

Advanced Labs develops the ability to diagnose communication across GPUs,
network adapters, collectives and complete training or inference workloads.
Through guided comparisons and independent investigations, learn to quantify
exposed communication costs and justify a change using correctness, measurements
and traces.

Read [GPU Performance Tools](../gpu-performance-tools/index.html) before practical work.
Complete GPU Fundamentals, GPU Performance Optimization and the relevant training
or inference foundations first. Experienced learners can begin if they can launch
Slurm jobs, produce a correct unprofiled result and interpret its GPU timeline.
This is a labs-only course: each guide contains its own explanation and practice.

Practice requires two eight-H100 workers, sixteen GPUs in total, with local
NVLink/NVSwitch and inter-worker InfiniBand. Prepare through the shared Lab Guide's
[environment setup](../lab-guide.html#lab-preparation-scripts), then qualify placement and
fabric in Labs 01–07. Bounded experiments support scoped decisions; they do not
establish production convergence or large-model scaling.
