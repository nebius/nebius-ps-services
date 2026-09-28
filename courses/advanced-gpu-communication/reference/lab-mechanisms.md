# Choose evidence at the right boundary

A topology map identifies possible paths. A vendor transfer test measures an exercised path. A collective combines transfer and coordination across a group. A training step or inference request adds computation, scheduling and dependencies. Explain each result at the boundary actually measured.

Lab 06 measures directed pairwise NVLink paths, not concurrent switch saturation. Labs 07 and 27 separate RDMA bandwidth from completion latency. Labs 08–14 identify collective ordering and overlap. Labs 15–25 connect communication to model state and outputs. Labs 26–34 connect small-message latency, adapter choice and current NVIDIA runtimes to training throughput and serving objectives.

Use Nsight Systems for CPU gaps, CUDA work, NCCL activity and NVTX phases. PyTorch traces attribute work to framework operations and input shapes; memory profiling records tensor allocations and deallocations during collection. Lab 14 reuses preallocated tensors, so its trace may contain no allocation events. Nsight Compute investigates a selected local kernel's memory traffic, occupancy and stalls; replaying a collective while its peers run normally can hang or distort the experiment. Grafana compares authoritative measured summaries and correlates the job window with device and node activity.

For every independent investigation, predict an observable change before running. Keep useful work and correctness fixed, select one control, repeat clean timings, and inspect diagnostic traces separately. Explain a failed hypothesis rather than searching only for a speedup.
