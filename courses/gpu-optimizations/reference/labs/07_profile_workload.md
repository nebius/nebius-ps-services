# Lab 07: Build a readable profiler map of a workload

A useful profile connects application regions to GPU activity so that you can explain a delay, not merely collect a large report. This lab provides labeled PyTorch and NVTX regions around a small tensor workload. You will choose either the internal PyTorch profiler or an external NVIDIA profiler and keep instrumented evidence separate from benchmark timing.

## Before you start

**Theory preparation:** Read Lesson 3 for operator attribution, self time, instrumentation and profiler scope. Fundamentals Lesson 2 defines the projection/GELU/reduction chain; Optimizations Lesson 2 supplies the separate unprofiled timing contract.

Complete the [diagnostic tooling setup](../tooling-setup.md) on the compute node. Use one H100. Trace files may contain paths or environment details and must remain private until reviewed.

Discover installed section sets and permissions on the target rather than hard-coding another Nsight version. Use NVTX to select a stable semantic phase and profile the exact SM90 kernel chosen for the real shape.

## Concepts and code path

The source generates tensors and executes projection, activation, and reduction work inside named ranges. The literal `train_step` range is only a label: there is no backward pass or optimizer update. Internal profiling aggregates CUDA operators and optionally exports a trace. External-only mode leaves NVTX ranges available without nesting PyTorch Profiler inside the NVIDIA tool.

## Practice

Given a slow `aten::linear` region in PyTorch Profiler, inspect a Systems trace and find a 400-microsecond CPU gap before one 80-microsecond GEMM. Change the hypothesis from “inefficient GEMM” to “host launch delay.” Expected observation: Nsight Compute is unnecessary until a control removes the gap and the GEMM itself remains limiting; a privileged-counter error is recorded as a blocker, not bypassed.

Run Lab 07 and Lab 14's synchronization case through the supplied profiler launchers and write one evidence-backed hypothesis using the timing contract already established. Preview the other Lab 14 cases by reading their descriptions and code. Run the launch case after Lesson 4, the memory case after Lesson 7 and the compute case after Lesson 9, when their techniques have been explained. Revisit the full casebook before the capstone.

Choose one profiler route per run. The first command requests an exported PyTorch trace; the second collects an external system timeline with internal profiling disabled.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/07_profile_workload.py --profile smoke --export-trace
sbatch slurm/nsys_single_gpu.sbatch labs/07_profile_workload.py --profile smoke --external-only
```

## Check your results

Confirm finite output and inspect `trace_exported` and the available CUDA table for the chosen mode. Default execution alone does not export a Chrome trace. A captured range is not proof that its human-readable name describes a complete training step.

Retain the minimal trace range, operator/kernel names, timing, and relevant counters.

A profiler is evidence collection, not an automatic recommendation engine.

## Investigate the behavior

Follow one application range into its operator and kernel activity. Identify whether time lies in large kernels, many small launches, or idle gaps. Explain what further evidence would distinguish CPU starvation from device saturation.

Higher-detail tools add overhead, serialization, replay, storage, and analysis cost. Stop escalation as soon as the current hypothesis is confirmed or disproved; then remeasure the candidate without the profiler.

## If something goes wrong

Do not combine `--export-trace` and `--external-only`; they request incompatible profiling paths. Missing permissions or profiler binaries block that evidence lane, not the unprofiled workload itself.

Collecting every counter for every kernel creates overhead and obscures the causal path.

## Takeaways and next step

Well-scoped ranges make profiles interpretable. Next, use Lab 14's controlled bottleneck cases to test one causal hypothesis and return to unprofiled measurements before accepting an improvement.

Start broad, isolate the dominant path, and collect detailed counters only for a specific hypothesis.

Choose one tool for launch gaps, one for framework attribution, and one for a memory-transaction question.
