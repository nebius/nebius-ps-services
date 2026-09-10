# Lab 04: Replay a fixed-shape workload with a CUDA Graph

A CUDA Graph is a reusable plan of device operations and their dependencies. Capture records compatible work into the plan; replay executes it again, rather than returning cached output values. The plan can still contain several separate kernels: replay is not the same as fusion. Preparing this plan can reduce repeated CPU launch setup. This lab captures one fixed-shape workload using stable buffers and compares replay with eager execution. You will learn why replay is a scheduling optimization rather than an automatic solution for arbitrary inputs, shapes, or dynamic control flow.

## Before you start

**Theory preparation:** Read Lessons 4–5 for compilation versus graph capture, stable storage and replay. Apply Lesson 2's completion boundary. The matrix-multiplication and SiLU workload is explained below.

Use one H100 and the approved environment. Review stream ordering and tensor lifetime. The supplied experiment has one input shape and does not implement shape buckets, dynamic routing, or an eager fallback service.

Fast repeated H100 inference or training steps can become CPU-launch limited, making replay valuable. A graph does not improve the kernel instruction path, memory coalescing, or collective algorithm inside the captured sequence.

## Concepts and code path

The program warms and captures a fixed-shape matrix multiplication followed by SiLU. Replay uses the captured input and output storage. Rebinding a Python variable does not change those addresses. The supplied check compares replay with eager execution for the original input. Copying new values into the static input is the explicit extension in Practice; the baseline does not test changing inputs, graph updates or shape routing.

## Practice

Given one fixed-shape step with 60 microseconds of kernels and 40 microseconds of launch overhead, graph replay can remove much of the 40. Change the service to eight shape buckets. Expected observation: warmed hits improve while first-use capture and graph-pool memory grow; an unseen shape must execute the declared fallback rather than silently pad without accounting.

First run Lab 04's implemented fixed-shape eager-versus-graph baseline. It captures one input buffer and does not implement buckets or fallback routing. Extension: create a second input of the same shape, copy it into the captured static buffer before replay on the correctly ordered stream, and compare the replay output with an eager reference for that new input. Then add an explicit shape check: an unsupported shape runs eagerly rather than reusing an incompatible graph. Create separate captures only for deliberately supported buckets.

Run the baseline without edits first. Each profile starts a separate process and capture; selecting a second profile is not replaying a new shape through the original graph.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/04_cuda_graphs.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/04_cuda_graphs.py --profile h100
```

## Check your results

Require `allclose` and `fixed_shape`. Compare the `eager` and `cuda_graph` distributions with the recorded shape. Do not infer capture startup amortization or dynamic-input correctness from this fixed-input gate.

For the supplied baseline, record capture success, eager/replay correctness, and repeated timing. For the extension, additionally retain changed-input reference checks, each supported shape, fallback counts, capture/startup cost, and graph-pool memory. Report only measurements actually collected.

Graphs trade flexibility for lower recurring dispatch overhead.

## Investigate the behavior

Identify which buffers must remain alive across replay. Explain why replacing a Python variable with a new allocation does not update the captured pointer. Use a timeline to check whether launch gaps shrink.

Graph pools and per-bucket captures consume memory. Many buckets reduce fallback but increase warm-up and retained state. Replay improves steady state while potentially worsening startup, debuggability, and rare-shape behavior.

## If something goes wrong

A capture error may indicate unsupported operations or synchronization inside capture. An unchanged output after supplying new data may mean you updated the wrong buffer. Check storage and ordering before blaming numerical precision.

Avoid comparing replay against a cold eager path or silently reusing stale input storage.

## Takeaways and next step

Use the changed-input and shape-routing extension in Practice to test which capture assumptions your workload can preserve.

A successful fixed-input replay does not prove that a changing workload is safe. The extension must update stable input storage, order copies before replay, validate new outputs, and route incompatible shapes explicitly.

List the shape, allocation, and control-flow assumptions in the captured region.
