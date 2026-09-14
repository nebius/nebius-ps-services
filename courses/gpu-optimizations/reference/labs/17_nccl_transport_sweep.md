# Lab 17: Measure a two-node NCCL message-size curve

A communication problem can affect tiny synchronization messages differently from large tensors. This lab gives you a controlled way to see that difference before investigating a full training step. You will run the same checked FP32 all-reduce across increasing payload sizes, compare one NCCL setting in a new job, and explain which observations support or contradict your hypothesis.

## Before you start

**Theory preparation:** Read Lesson 11 for collective size curves, transport, controlled communicator settings and bandwidth units. Fundamentals Lesson 12 supplies networking layers; Lessons 2–3 distinguish timing from diagnostic profiling.

Complete Lesson 11's networking explanation and the two-node preflight. Use two allocated nodes with one full H100 on each, a working course PyTorch environment and the existing torchrun launcher. The baseline needs working NCCL communication; it does not require RDMA. The GDR-disabled and QP experiments require an independently qualified IB/RoCE path. Keep the same nodes, interfaces, versions and absence of competing workloads across comparisons where the scheduler permits; otherwise record that allocation variability remains a confounder.

Required execution is two nodes with one full H100 and one rank per node. It cannot benchmark intra-node NVLink/NVSwitch. InfiniBand, RoCE and GDR experiments are conditional on the installed fabric; a working Socket baseline remains a legitimate result. Larger nodes require separate GPU-visibility, MPI and placement qualification, not an unreviewed change to rank counts.

## Concepts and code path

The program is a measurement harness, not a custom implementation of a collective. PyTorch creates the NCCL process group; each rank fills a tensor with its rank number plus one. On two ranks, every element must become exactly three after sum all-reduce. The simple small integers are exactly representable in FP32, so this particular fixture uses an exact check rather than a loose numerical tolerance. For changed floating-point inputs, return to the course FP32 tolerance of rtol=1e-5 and atol=1e-6 against a trusted reference.

The networking helper selects one environment override before NCCL starts. For each payload size, the lab refills the tensor and synchronizes readiness. It records a CUDA start event, submits `all_reduce` with `async_op=True`, calls `work.wait()`, records the stop event on that stream and synchronizes it before reading elapsed time. Preparation, output checks and result gathering stay outside the interval. MAX aggregation supplies the slowest rank's duration per iteration; a separate synchronized host sample includes launch and event-handling overhead.

## Practice

Given an illustrative 64 MiB all-reduce taking 4,000 microseconds on two ranks, algorithmic bandwidth is 67,108,864 / 0.004 / 10⁹ ≈ 16.78 GB/s; normalized bus bandwidth is also 16.78. This is not an H100 performance claim. Change only the eligible collective algorithm in a fresh comparison. Expected observation: a candidate may improve that row but worsen 8 KiB messages. Check which sizes the application actually sends before choosing it; averaging all row bandwidths can hide the regression that matters.

Run preflight, then Lab 17 to learn the curve and test one supported profile. Complete Lab 18 with a qualified MPI-enabled NVIDIA NCCL Tests binary, inspecting both result modes and runtime metadata. Repeat only the promising candidate in Lesson 12's Labs 08 and 13.

### Discover the selected path

Use an allocated node, not the login host, for read-only discovery. Inspect GPU/NIC proximity with `nvidia-smi topo -m`; its legend explains path labels such as PIX, PHB and SYS. Use available `ibv_devinfo`, `ibstat` or `rdma link show` output to distinguish an active port from a merely installed adapter, and InfiniBand from Ethernet/RoCE. An IP interface name and an RDMA HCA name are different namespaces. Record only sanitized topology facts in a shared report; raw outputs may contain hostnames, addresses or device identifiers.

Collect a short diagnostic run with `NCCL_DEBUG=INFO` and relevant INIT, NET and GRAPH logging. Record the runtime NCCL version, participating ranks, selected interfaces/transport and any registration messages. Socket traffic may be used for setup even when payloads use RDMA. `NET/IB` alone does not prove GPUDirect RDMA: correlate it with the qualified GPU-memory registration path and, when supplied by the cluster owner, GPU-buffer RDMA tests. A missing `nvidia-peermem` module is not conclusive because supported DMA-BUF configurations can provide registration instead.

For that diagnostic job, set `NCCL_DEBUG_SUBSYS=INIT,NET,GRAPH` in a temporary job-submission shell. Inspect its private Slurm log. End that shell and submit timing jobs without the verbose settings. Do not change cluster-wide NCCL files. If the script detects inherited experimental settings, inspect the job environment and ask the owner which settings are required before starting a clean comparison.

### Change one factor in a fresh job

Leave automatic selection as the first baseline. The supplied profiles change exactly one experiment setting in a fresh communicator/process. They reject inherited experimental overrides instead of quietly mixing them. Required site interface/plugin configuration remains the cluster owner's responsibility; a site's configuration files can still affect the effective baseline and must be recorded privately.

| Question | Job-local comparison | What would make it meaningful |
| --- | --- | --- |
| Does the network transport matter? | `default` versus `socket`, which selects `NCCL_NET=Socket` | Diagnostics establish distinct payload transports; otherwise it is not an RDMA-versus-socket comparison |
| Does direct GPU-memory access matter? | `default` versus `gdr-off`, using `NCCL_NET_GDR_LEVEL=LOC` | Baseline GDR is independently qualified and the same RDMA transport remains selected |
| Does collective organization matter for these sizes? | `default` versus `ring` or `tree`, using `NCCL_ALGO` | Same work, ranks and transport; unsupported forced algorithms may fail |
| Does flow distribution matter on this fabric? | `qp1` versus `qp4`, changing `NCCL_IB_QPS_PER_CONNECTION` | Qualified IB/RoCE path; same QP splitting policy and no concurrent job changes |

`NCCL_SOCKET_IFNAME='=eth0'` illustrates exact IP-interface selection; `NCCL_IB_HCA='=mlx5_0:1'` illustrates an exact RDMA adapter/port. These are example names, not cluster discoveries. Confirm the actual interfaces on every rank and freeze selection across the comparison. Without the leading equals sign, prefix matching can accidentally select extra adapters. `NCCL_IB_DISABLE=1` disables NCCL's IB/RoCE transport, not the NIC; the supplied Socket profile explicitly selects Socket to avoid ambiguity with other installed network plugins.

Do not copy old RoCE recipes blindly. NCCL 2.21 and later dynamically select a GID (global identifier), so NVIDIA advises against setting `NCCL_IB_GID_INDEX` for those versions. Do not force LL128 protocols, maximum GDR distances, traffic classes or timeout values as generic speedups. A QP increase can help distribution across paths yet worsen small-message latency. Repeated measurements decide, not the apparent sophistication of the setting.

### Run controlled trials

Submit from the course root. First run a small range and the default profile; then collect a full curve. Each submission starts a new process group. The larger --profile label does not secretly change the message range: the explicit byte bounds define this workload.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
sbatch slurm/two_node.sbatch labs/17_nccl_transport_sweep.py --max-bytes 1048576
sbatch slurm/two_node.sbatch labs/17_nccl_transport_sweep.py --variant default
sbatch slurm/two_node.sbatch labs/17_nccl_transport_sweep.py --variant socket
```

For acceptance, repeat each compared profile in at least three independent jobs, alternating default/candidate order. Within a job, `--warmup` and `--iterations` control repeated samples, not independent runs. Use `--variant gdr-off`, `ring`, `tree`, `qp1` or `qp4` only after writing the corresponding hypothesis from the comparison table above. Compare `qp1` with `qp4` as its own experiment; do not combine a QP change with an algorithm change.

## Check your results

Require all_sizes_exact_on_all_ranks and a complete ordered curve. Each row contains bytes, slowest_rank_samples_ms, median_ms, min_ms, p90_ms, algbw_GBps_at_median and normalized_busbw_GBps_at_median. The wall_summary and slowest_rank_wall_samples_ms fields provide the host completion cross-check. The environment identifies PyTorch, CUDA and loaded NCCL versions. Verbose NCCL logging marks acceptance_timing false; hidden profilers and site policy still need to be excluded in the experiment record. Inspect distributions at each size, not just the fastest sample; a p90 from twenty measurements is a coarse summary, not a stable production tail estimate.

The expected result is a valid measured curve, not a guaranteed speedup. The Socket candidate can match the default if default already used sockets. A GDR-disabled candidate can match default if GDR was unavailable, if another limiter dominates, or if the difference is smaller than run-to-run noise. No outcome alone proves the active transport.

Keep the exact payload curve, dtype, operation, rank mapping, versions, correctness, private transport diagnostics, job-local change, per-run results and variation across runs. Compare the same message size and buffer mode. Record whether the claimed path was qualified, merely observed as selected, or remains unknown.

Small-message delays suggest fixed overhead or synchronization; a large-message plateau points toward a bandwidth limit but does not identify its cause. Spikes, retries or growing tails warrant fabric-health and contention investigation. A faster collective is useful only if exposed application communication also improves.

## Investigate the behavior

Build a small worksheet for three representative sizes: 8 KiB, 1 MiB and 64 MiB. For each, retain all three independent run medians and their range, then compare the median of those run medians between profiles. Do not pool every within-job sample and call them independent runs. Explain whether the effect is limited to small-message latency, large-message transfer, or neither.

Correlate the curve with separate transport diagnostics and read-only link information. If one candidate helps large messages but hurts small ones, inspect actual application collective sizes before choosing it. This harness intentionally inserts barriers and checks; application overlap and asynchronous submission may produce different behavior, which Lesson 12 tests separately.

Diagnostic logging and profilers perturb timing; collect them separately. Blocking on each sample measures each collective through completion but differs from pipelined application behavior. More flows consume adapter resources. A network issue requiring PFC/ECN, MTU, firmware, subnet-manager, ACS/IOMMU or system-limit changes must be escalated to the cluster owner, not repaired by a lab script.

## If something goes wrong

A timeout can result from rank disagreement, unreachable setup interfaces, unavailable transport or a failed participant; it is not a request to increase every timeout. Inspect the first error on each rank and preserve the failed run separately. Unknown algorithms or missing RDMA support are conditional failures. Do not change drivers, subnet managers, NIC settings or switch policies. Stop performance interpretation immediately if the tensor sum is incorrect or a required rank is absent.

Avoid treating a diagnostic setting as a permanent default, attributing a socket-to-RDMA change to an algorithm, accepting unchecked output, or calling normalized bus bandwidth measured wire throughput.

## Takeaways and next step

You now have a reusable experiment structure: freeze the work, prove the selected path, vary one choice before communicator creation, check outputs and compare distributions. Use Lab 18 to measure the same question through NVIDIA's standalone collective benchmark, then Labs 08 and 13 to determine whether any promising change reduces application time or exposed communication.

Prove correctness and placement, identify the selected path, compare one factor across independent runs, then keep it only if the application-level result improves within the declared contract.

What evidence would disprove your claim that GPUDirect RDMA helped, and what remains unknown if the default and GDR-disabled curves are indistinguishable?
