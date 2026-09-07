# Lab 27: Separate compiled expressions from captured training steps

Compilation and CUDA Graphs optimize different parts of execution and should not be treated as one indistinguishable feature. This lab checks a compiled expression separately from a captured fixed-shape training update. You will compare the eager and compiled expressions, then compare eager and captured updates, and identify exactly which path the reported profile describes.

## Before you start

**Theory preparation:** Read Lesson 10 for full-graph expression compilation, fixed-storage CUDA Graph capture and replay, profiler scope and warm-up. Lessons 1, 3 and 4 supply GELU, mean squared error, backward and SGD; Lesson 6 supplies graph-memory lifetime. Verify one complete update before measuring replay.

Use one H100 with a qualified compiler backend and CUDA Graph support. Review static buffer lifetime, gradients, and optimizer updates. The source requires full-graph expression compilation; it does not demonstrate successful graph breaks or fallback routing.

H100 can make short surrounding kernels and launch gaps prominent beside fast GEMMs. Fused attention or MLP paths must be verified for the actual dtype and shape.

## Concepts and code path

The script validates an expression compiled with `fullgraph=True` and records its first call. Separately it builds matched training states, captures zero-grad, forward, backward, and SGD update using fixed storage, and verifies the next update. It times eager and captured training steps. The profiler summary covers graph replay, not a complete comparative eager/compiled training trace.

## Practice

Given a forward region with six pointwise kernels, two temporary tensors, and 90 microseconds of launch gaps, compile it and compare outputs plus gradients. Change to graph-capture the stable compiled step after warm-up. Expected observation: fusion reduces launches/traffic and replay reduces submission, but compile time, graph-pool bytes, fallback rate, and optimizer equivalence stay in the report.

Begin with Lab 30 to attribute the tiny transformer's step to operator time and shapes; this adds instrumentation to the update learned in Lesson 4, rather than a second training-step lesson. Preserve that baseline for the capstone. Then run Lab 27's separate compiled-expression and captured-training-update checks. Its expression requires full-graph compilation; its profile covers graph replay, not a comparative compiled/eager training trace. Extension: instrument eager and compiled paths separately to inspect fused groups and launch counts, and add explicit shape validation before a bucket or fallback exercise. A full-graph compilation failure is not an observed successful graph break.

Run the supplied separated checks before instrumenting additional paths. Keep compile startup and steady-state training results distinct in your worksheet; they have different scopes.

```bash
umask 077
python labs/27_fused_graph_trace.py --help
sbatch slurm/single_gpu.sbatch labs/27_fused_graph_trace.py --profile smoke
```

## Check your results

Require `compiled_expression_close` and `captured_next_update_close`. Inspect `compiled_first_call_ms`, `cuda_graph_scope`, eager/graph training-step distributions, and `profile_dispatch_keys`. A replay profile cannot establish the number of fused groups in an unprofiled compiled path.

The update gate requires present, finite gradients and compares them after
normalizing each parameter tensor by its reference gradient's maximum magnitude,
using `rtol=1e-2, atol=1e-2`. It independently reconstructs each plain-SGD update
from the initial parameters and that path's gradients, then requires an exact
match and at least one changed parameter. Whole-parameter tolerances alone can
hide an omitted update when the learning rate is small.

Record compile time separately, graph coverage, kernel count, HBM traffic, step time, and numerical equivalence.

Keep fusion or capture only when the full training step remains correct and faster after warm-up.

## Investigate the behavior

Which state changes on each captured update and which storage addresses remain stable? Explain why expression equivalence is not enough to prove optimizer-update equivalence. Identify the profile needed to support each proposed fusion or launch claim.

Fusion can increase register pressure and reduce reuse. Compilation and graph capture improve warmed steps but increase startup, memory, specialization, and debugging cost. A graph-friendly fixed shape may increase padding.

## If something goes wrong

Full-graph compilation failure is a failed candidate, not an automatic eager fallback. Capture errors or mismatched updates require checking gradient buffers, ordering, and optimizer state before timing.

Avoid timing compilation as steady state or capturing a buffer whose contents are not refreshed.

## Takeaways and next step

Keep mechanism, correctness, and measurement scopes aligned. Extension: collect separate eager and compiled traces and compiler diagnostics; add explicit shape validation before attempting buckets or fallback behavior.

Validate gradients and updates, warm separately, and publish fallback frequency.

Name one training operation that can make capture unsafe.
