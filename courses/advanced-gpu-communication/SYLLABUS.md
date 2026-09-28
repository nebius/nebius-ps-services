# Lab route

Complete shared environment setup once. Keep these identities in filenames, dashboards and evidence. Mechanics labs intentionally reserve the fabric while using only the ranks their model requires.

## Qualify placement and fabric

- [Lab 01: Verify the GPU fabric and rank placement](reference/labs/01_fabric_topology.md)
- [Lab 02: Verify the two-node H100 platform](reference/labs/02_collective_readiness.md)
- [Lab 03: Verify the distributed training allocation](reference/labs/03_training_readiness.md)
- [Lab 04: Verify the two-node inference mechanics platform](reference/labs/04_inference_readiness.md)
- [Lab 05: Establish the optimization platform contract](reference/labs/05_transport_readiness.md)
- [Lab 06: Compare copy engines and SM peer traffic](reference/labs/06_nvlink_bandwidth.md)
- [Lab 07: Validate host and GPU memory over InfiniBand](reference/labs/07_rdma_bandwidth.md)

## Understand collectives and rank timelines

- [Lab 08: Measure a two-node NCCL all-reduce](reference/labs/08_distributed_collectives.md)
- [Lab 09: Measure a two-node NCCL message-size curve](reference/labs/09_nccl_transport_sweep.md)
- [Lab 10: Run and interpret NVIDIA NCCL Tests on Slurm](reference/labs/10_nccl_tests_report.md)
- [Lab 11: Compare flat and hierarchical all-reduce](reference/labs/11_collective_layout.md)
- [Lab 12: Compare one GPU with two-node DDP](reference/labs/12_distributed_scaling.md)
- [Lab 13: Test whether communication can overlap independent compute](reference/labs/13_collective_overlap.md)
- [Lab 14: Correlate PyTorch and Systems traces across ranks](reference/labs/14_distributed_profiling.md)

## Train with distributed state and communication

- [Lab 15: Train replicated models with DDP](reference/labs/15_ddp_train.md)
- [Lab 16: Observe FSDP2 sharded training state](reference/labs/16_fsdp2_train.md)
- [Lab 17: Trace expert routing, gradients, and grouped work](reference/labs/17_training_expert_parallel.md)
- [Lab 18: Partition a linear layer and verify its backward pass](reference/labs/18_training_tensor_parallel.md)
- [Lab 19: Reduce gradients when they become ready](reference/labs/19_gradient_overlap.md)
- [Lab 20: Follow pipeline and context partitions through backward](reference/labs/20_parallelism_mechanics.md)
- [Lab 21: Tune real DDP buckets and communication hooks](reference/labs/21_ddp_buckets.md)
- [Lab 22: Tune microbatching at fixed global batch](reference/labs/22_fabric_training.md)

## Inspect inference parallelism

- [Lab 23: Route inference tokens to expert owners](reference/labs/23_inference_expert_parallel.md)
- [Lab 24: Compare inference tensor-partition communication patterns](reference/labs/24_inference_tensor_parallel.md)
- [Lab 25: Compare request replicas with tensor-parallel decoding](reference/labs/25_fabric_inference.md)

## Isolate network latency and transport

- [Lab 26: Separate collective latency from payload throughput](reference/labs/26_collective_latency.md)
- [Lab 27: Measure GPU-memory RDMA read completion latency](reference/labs/27_rdma_latency.md)
- [Lab 28: Test NCCL network-adapter selection](reference/labs/28_nic_selection.md)
- [Lab 29: Measure NIXL GPU transfer progress](reference/labs/29_nixl_transfer.md)

## Apply current NVIDIA training and serving techniques

- [Lab 30: Overlap Megatron gradient communication](reference/labs/30_megatron_overlap.md)
- [Lab 31: Place hierarchical context-parallel communication](reference/labs/31_context_parallel.md)
- [Lab 32: Compare aggregated and disaggregated Dynamo serving](reference/labs/32_dynamo_disaggregation.md)
- [Lab 33: Test KV-cache-aware request routing](reference/labs/33_dynamo_routing.md)
- [Lab 34: Tune serving goodput under latency objectives](reference/labs/34_serving_goodput.md)

## Final investigation

Choose a training or inference workload family. Write a hypothesis before the candidate run, retain one changed control, and repeat both variants in reversed order. Explain why a bandwidth result can disagree with a latency result. Use a later trace to distinguish a communication tail from a delayed producer, then decide whether to keep the change. Include correctness, variation and a limitation; a negative result can pass.
