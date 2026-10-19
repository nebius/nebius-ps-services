# Advanced Labs: Multi-GPUs Multi-Nodes communication optimization

[Read the course](index.html) · **34 labs** · Estimated guided time: **64 hours**,
excluding provisioning and queue time.

Follow communication from fabric paths and collectives to distributed training,
inference and serving. For the full lab route, complete GPU Fundamentals and GPU
Performance Optimization, then the relevant training or inference foundations,
or bring equivalent readiness to launch Slurm jobs, produce a correct unprofiled
result and interpret its GPU timeline.
Use [GPU Performance Tools](../gpu-performance-tools/index.html) for profiling
and [PyTorch for GPU Performance Engineering](../pytorch-gpu-performance-engineering/index.html)
for a tensor-reading refresher.

## Before the first lab

Use two eight-H100 workers with local NVLink/NVSwitch and active InfiniBand.
The two one-GPU TCP workers used for local labs do not qualify this fabric.
Sixteen GPUs are allocated; some mechanics use only two participating ranks.

Complete the [Lab Guide](../lab-guide.html#lab-preparation), check
[Versions and environment](VERSIONS.md), then follow the
[readiness checks](reference/cluster-smoke-test.md) on the qualified cluster.
For the full route, begin with Lab 01's topology and rank-placement check.
For Fundamentals Lesson 12, read through that lesson, complete
[Lab 02](reference/labs/02_collective_readiness.md), verify its placement and
correctness results, then run [Lab 08](reference/labs/08_distributed_collectives.md).
Optimization, a specialization, Lab 01 and Labs 03–07 are not prerequisites for
this assignment. Both entry points require the same prepared, qualified fabric.
After Lab 08, return to Fundamentals to complete the course.

## Runtime preparation

The Lab Guide's single setup command prepares the available communication
runtimes, profiling tools and model dependencies in separate pinned environments.
Dynamo labs use the larger model. Check the completion summary for failures or
hardware skips before submitting a lab. Preparation records candidate software;
it does not prove GPU, fabric or serving compatibility.

NIXL and Dynamo start a private job-owned etcd process on the first worker.
Both worker names must resolve to reachable IPv4 addresses. The prepared
runtime supplies `COURSE_ETCD` and `DYNAMO_UCX_PREFIX`; use its recorded runtime
rather than mixing libraries from another environment. Qualify each vendor
lab with its small workload before performance comparisons.

## Learning route

For the full route, follow the groups below. Keep each lab's identity when selecting commands,
dashboards and results.

### Qualify placement and fabric

- [Lab 01: Verify the GPU fabric and rank placement](reference/labs/01_fabric_topology.md)
- [Lab 02: Verify the two-node H100 platform](reference/labs/02_collective_readiness.md)
- [Lab 03: Verify the distributed training allocation](reference/labs/03_training_readiness.md)
- [Lab 04: Verify the two-node inference mechanics platform](reference/labs/04_inference_readiness.md)
- [Lab 05: Establish the optimization platform contract](reference/labs/05_transport_readiness.md)
- [Lab 06: Compare copy engines and SM peer traffic](reference/labs/06_nvlink_bandwidth.md)
- [Lab 07: Validate host and GPU memory over InfiniBand](reference/labs/07_rdma_bandwidth.md)

### Understand collectives and rank timelines

- [Lab 08: Measure a two-node NCCL all-reduce](reference/labs/08_distributed_collectives.md)
- [Lab 09: Measure a two-node NCCL message-size curve](reference/labs/09_nccl_transport_sweep.md)
- [Lab 10: Run and interpret NVIDIA NCCL Tests on Slurm](reference/labs/10_nccl_tests_report.md)
- [Lab 11: Compare flat and hierarchical all-reduce](reference/labs/11_collective_layout.md)
- [Lab 12: Compare one GPU with two-node DDP](reference/labs/12_distributed_scaling.md)
- [Lab 13: Test whether communication can overlap independent compute](reference/labs/13_collective_overlap.md)
- [Lab 14: Correlate PyTorch and Systems traces across ranks](reference/labs/14_distributed_profiling.md)

### Train with distributed state and communication

- [Lab 15: Train replicated models with DDP](reference/labs/15_ddp_train.md)
- [Lab 16: Observe FSDP2 sharded training state](reference/labs/16_fsdp2_train.md)
- [Lab 17: Trace expert routing, gradients, and grouped work](reference/labs/17_training_expert_parallel.md)
- [Lab 18: Partition a linear layer and verify its backward pass](reference/labs/18_training_tensor_parallel.md)
- [Lab 19: Reduce gradients when they become ready](reference/labs/19_gradient_overlap.md)
- [Lab 20: Follow pipeline and context partitions through backward](reference/labs/20_parallelism_mechanics.md)
- [Lab 21: Tune real DDP buckets and communication hooks](reference/labs/21_ddp_buckets.md)
- [Lab 22: Tune microbatching at fixed global batch](reference/labs/22_fabric_training.md)

### Inspect inference parallelism

- [Lab 23: Route inference tokens to expert owners](reference/labs/23_inference_expert_parallel.md)
- [Lab 24: Compare inference tensor-partition communication patterns](reference/labs/24_inference_tensor_parallel.md)
- [Lab 25: Compare request replicas with tensor-parallel decoding](reference/labs/25_fabric_inference.md)

### Isolate network latency and transport

- [Lab 26: Separate collective latency from payload throughput](reference/labs/26_collective_latency.md)
- [Lab 27: Measure GPU-memory RDMA read completion latency](reference/labs/27_rdma_latency.md)
- [Lab 28: Test NCCL network-adapter selection](reference/labs/28_nic_selection.md)
- [Lab 29: Measure NIXL GPU transfer progress](reference/labs/29_nixl_transfer.md)

### Apply current NVIDIA training and serving techniques

- [Lab 30: Overlap Megatron gradient communication](reference/labs/30_megatron_overlap.md)
- [Lab 31: Place hierarchical context-parallel communication](reference/labs/31_context_parallel.md)
- [Lab 32: Compare aggregated and disaggregated Dynamo serving](reference/labs/32_dynamo_disaggregation.md)
- [Lab 33: Test KV-cache-aware request routing](reference/labs/33_dynamo_routing.md)
- [Lab 34: Tune serving goodput under latency objectives](reference/labs/34_serving_goodput.md)

## Completion

Choose a training or inference workload. State a hypothesis, change one control,
repeat clean baseline/candidate runs in reversed order, and explain the result
with correctness, variation and a limitation. A negative result can pass.
Retain the matching artifacts, topology/software identity, selected dashboard
and relevant rank or server traces privately.

For training, explain useful tokens per second and the collective tail. For
inference, explain first-token latency, streamed delivery and requests meeting
latency objectives. Labs 32–33 require equivalent outputs; Lab 34 fixes
transmitted requests and server-verified generated-token work, allowing different
answer text and valid zero-goodput results. These contracts are not interchangeable.

Use [Where to Go Next](NEXT-STEPS.md) for optional reading.

## Reference guides

- [Versions and environment](VERSIONS.md): candidate runtimes and qualification limits.
- [Readiness checks](reference/cluster-smoke-test.md): placement, transport and vendor gates.
- [Lab mechanisms and evidence](reference/lab-mechanisms.md): measurement boundaries and interpretation.
- [Benchmark worksheet](reference/benchmark-record.md): record controlled comparisons.
- [Evidence and privacy](reference/evidence-security.md): safe collection and sharing.
- [Glossary](GLOSSARY.md): terms used throughout the labs.
