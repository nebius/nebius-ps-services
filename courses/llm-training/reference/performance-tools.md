# Using GPU performance tools

## Objective

Use a timeline, a kernel report, and a dashboard to answer different performance questions. Keep the workload and correctness contract fixed while testing one explanation for a measured bottleneck.

## How it works

**Nsight Systems** records a timeline: CPU activity, CUDA calls, copies, kernels, and communication. Start here when an application is slow. Select a time interval, expand its CUDA and NVTX rows, and follow a launch to the corresponding GPU work. A long CPU interval may be waiting, executing, or submitting work; inspect the calls before deciding which. Overlapping rectangles on different streams establish concurrency, while elapsed time and dependencies determine whether that overlap helps.

**Nsight Compute** collects counters for a selected kernel, often by replaying it. Open Details, then Speed Of Light, Memory Workload Analysis, and Occupancy. Relate memory traffic, compute activity, launch dimensions, and resource limits to the algorithm. Occupancy is the fraction of available resident warps in use; maximizing it alone is not the objective. Counter permission errors mean the capture is unavailable, not that a counter is zero. Never replay a whole distributed collective or a live serving workload as a kernel experiment.

**NVTX** adds named application regions to a trace. The supplied capture path names phases such as warm-up, measurement, transfers, and the complete workload. A range marks CPU submission unless the code explicitly waits for completion. Add a name at a meaningful boundary, then filter that range and the observed kernel name for a small Compute capture. An annotation does not change an asynchronous operation into a synchronous one.

**Grafana** displays the selected completed results alongside sampled GPU and node telemetry. Select your persistent workspace, workload profile, GPU UUID, and experiment time interval. Summary panels show the current selected pair independently of the time picker; the chosen interval controls telemetry. The baseline and candidate slots stay unchanged until you explicitly publish another pair. Short kernels can finish between telemetry samples: use measured summaries and profiler evidence to explain them. Shared-device activity is correlation until exclusive attribution is established.

Instrumentation adds overhead. Use a separate diagnostic run to explain behavior, then repeat without profiling to judge performance. The `small` and `large` profiles change workload size; compare the same profile, inputs, seed, and runtime. A larger profile is not an optimization. Distributed captures produce one report per rank; align their collective and step boundaries rather than summing overlapping rank times. Serving captures wrap the GPU server while the client records request latency.

## Practice

Complete the setup checks once, then follow the first experiment's local commands. Save the baseline result, inspect a bounded capture, predict the effect of one change, and run that change without instrumentation. Publish the two validated artifacts and explain both the measured outcome and its limits. An unsuccessful optimization with a sound explanation is a valid result.

## Mental model

Systems locates the delay; Compute examines selected GPU work; Grafana correlates outcomes with device conditions. Correctness and repeated unprofiled measurements decide whether to keep the change.
