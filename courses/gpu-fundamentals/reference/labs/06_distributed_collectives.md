# Lab 06: Measure a two-node NCCL all-reduce

Distributed work cannot progress faster than its required data exchanges allow. This lab reduces a known tensor across two nodes and measures how message size affects completion time. It teaches collective semantics and payload-rate accounting on the actual inter-node path, without implying the behavior of an NVLink or NVSwitch system.

## Before you start

**Theory preparation:** Read Lesson 12 for all-reduce semantics, rank ordering, message-size curves, topology and slowest-rank completion, using Lesson 1’s timing principles. Complete Lab 00 before this collective experiment.

Complete Lab 00 on the same two-node allocation model. Both ranks need one H100 and matching software. Networking, transport selection, and scheduler administration remain the cluster owner's responsibility.

With one H100 per node, the measured path includes each GPU’s host attachment, CPU/NUMA placement, NIC path, network, and remote node. It does not exercise intra-node NVLink or NVSwitch. GPUDirect capability must be independently proven before it is claimed.

An active RDMA port establishes a hardware/network capability, not the path selected by NCCL. A runtime log naming an RDMA transport is stronger evidence of selection but still does not, by itself, establish direct GPU-memory registration. Supported DMA-BUF and peer-memory driver paths are alternative ways the platform may provide that registration. The cluster owner qualifies the platform; learners correlate that evidence with the actual application run. A missing optional capability is a clearly stated limitation, not permission to alter the cluster.

## Concepts and code path

Each rank creates a tensor whose known values make the sum predictable. The program initializes NCCL, warms the collective, resets input values between trials, and measures synchronized completion. Rank zero writes the portable result. All-reduce returns the reduced tensor to every participant; it is not merely a send from rank zero to rank one.

## Practice

Given one rank completing compute in 12 milliseconds, the other in 18, and an all-reduce taking 6 after both arrive, the step is at least 24 milliseconds. Change only the fast rank’s kernel to 8 milliseconds. Expected observation: step time stays near 24 because the slowest-rank barrier is unchanged; the optimization is locally real but globally hidden.

First annotate Lesson 12’s networking diagrams without running commands: label the software operation, local attachment, inter-node fabric and memory path. Then use this lab as the collective-mechanics baseline through the two-node launcher and compare several message sizes. GPU Optimizations covers transport diagnostics, NVIDIA NCCL Tests and tuning; do not introduce network configuration changes here.

Submit separate bounded message sizes and keep the rank count unchanged. Use new result files for each run rather than overwriting the smaller-payload evidence.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/06_distributed_collectives.py --profile smoke --payload-mib 4
sbatch slurm/two_node.sbatch labs/06_distributed_collectives.py --profile smoke --payload-mib 32
```

## Check your results

Require `all_reduce_sum`, then inspect `payload_mib`, `median_ms`, and `effective_payload_gib_per_s`. This lab reports rank zero's local median, not a reduced slowest-rank statistic. Its payload rate is not automatically network-link bandwidth or NCCL bus bandwidth.

Record world size, message bytes, collective, elapsed distribution, algorithmic bandwidth, GPU/CPU/NIC NUMA placement, and sanitized topology facts.

Verify GPU, CPU and NIC placement first; ask the cluster owner to resolve placement or NUMA-affinity problems before investigating NCCL tuning. Scaling evidence is valid only for the declared nodes, one-rank-per-node placement, and measured fabric; this course does not claim RDMA or direct-storage activation.

## Investigate the behavior

Explain why small messages can be dominated by startup latency and why larger messages may expose transfer cost. Draw the operation's dependencies and distinguish application payload from the bytes moved by a particular collective algorithm.

Larger buckets amortize latency but delay overlap opportunities and require memory. Different collective algorithms favor different message sizes and topology. Tuning before fixing rank placement or skew usually optimizes the wrong problem.

## If something goes wrong

A hang may be a missing rank, mismatched collective order, or a transport problem. Preserve private logs and confirm preflight before changing the experiment. Never suppress a failed exact-sum check to obtain timings.

Avoid generalizing a two-rank result to dense multi-GPU nodes or production-scale collectives.

## Takeaways and next step

Collective rates are meaningful only with topology, message size, and timing boundaries attached. Extend measurement to the slowest rank before using it as a global-step estimate; then study communication overlap in GPU Optimizations.

Treat the lab as a communication-mechanics baseline and preserve topology as part of benchmark identity.

State what this cluster can and cannot prove about NVLink, NVSwitch, and RDMA.
