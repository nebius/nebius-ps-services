# Choose evidence at the right boundary

A topology map identifies possible paths. A vendor transfer test measures an exercised path. A collective combines transfer and coordination across a group. A training step or inference request adds computation, scheduling and dependencies. Explain each result at the boundary actually measured.

Lab 06 measures directed pairwise NVLink paths, not concurrent switch saturation. Labs 07 and 27 separate RDMA bandwidth from completion latency. Labs 08–14 identify collective ordering and overlap. Labs 15–25 connect communication to model state and outputs. Labs 26–34 connect small-message latency, adapter choice and current NVIDIA runtimes to training throughput and serving objectives.

Use Nsight Systems for CPU gaps, CUDA work, NCCL activity and NVTX phases. PyTorch traces attribute work to framework operations and input shapes; memory profiling records tensor allocations and deallocations during collection. Lab 14 reuses preallocated tensors, so its trace may contain no allocation events. Nsight Compute investigates a selected local kernel's memory traffic, occupancy and stalls; replaying a collective while its peers run normally can hang or distort the experiment. Grafana compares authoritative measured summaries and correlates the job window with device and node activity.

For every independent investigation, predict an observable change before running. Keep useful work and correctness fixed, select one control, repeat clean timings, and inspect diagnostic traces separately. Explain a failed hypothesis rather than searching only for a speedup.

## Read distributed evidence

- Readiness Labs 03–04 must retain two participating ranks on distinct workers
  and pass their NCCL checks. Lab 01's topology check does not benchmark the
  prepared native fabric tools.
- Lab 06 measures directed peer paths, not simultaneous switch saturation.
  Keep bandwidth and latency populations separate: GB/s uses decimal bytes,
  GiB uses powers of 1024, and a request p99 is not a token-interval p99.
- Match Grafana's worker/local-GPU selectors to the private allocation record.
  A short run can fall between telemetry samples. Inspect reports from both
  workers rather than treating low sampled activity as idle kernels.
- Lab 19 checks gradients against BF16 bucket magnitudes with rank-wide
  agreement. Its Compute companion isolates one local backward kernel without
  collectives; Systems explains the distributed schedule.
- Bridge training keeps seeded input and its sequential dataloader fixed.
  NIXL evidence needs both launcher and native binary identities; the launcher
  alone may be a library-path wrapper.

## Interpret Dynamo comparisons

Keep the supplied batch-invariance, attention, RMSNorm and compiler settings
fixed across policies. Labs 32–33 require matching outputs. Lab 34 instead
requires identical transmitted requests and verified generated-token work;
answer text can differ and zero goodput can be valid.

Readiness includes worker health and frontend model discovery. For profiling,
both workers must acknowledge collection and produce nonempty reports before
shutdown. Inspect request-phase GPU activity and warnings in each report.
Labs 32–33 retain private request/cohort timestamps; correlate them with server
traces only after checking clock alignment. Keep instrumented timings separate
from clean comparisons.
