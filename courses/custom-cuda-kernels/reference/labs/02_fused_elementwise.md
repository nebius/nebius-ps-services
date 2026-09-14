# Lab 02: Remove an intermediate with elementwise fusion

Two simple kernels can spend more time moving intermediate values than performing arithmetic. This lab compares separate elementwise stages with a fused scale/bias/ReLU calculation using corresponding inputs. You will count logical memory traffic, verify both outputs, and determine whether reducing launches and intermediate storage improves the measured kernel sequence on H100.

## Before you start

**Theory preparation:** Read Lesson 5 for scale/bias/ReLU, fusion, intermediate traffic and FMA rounding, after Lessons 3–4 establish complete references and tool order. Explain the read/write byte counts for the separate and fused implementations before comparing the supplied paths.

Use the completed SM90 build on one H100. Understand vector indexing and FP32 reference checks. The experiment compares its supplied scalar-scale and bias-array expression, not every possible broadcast layout.

H100 HBM bandwidth is large, so enough elements are needed to amortize launch overhead and measure memory traffic. A compiler or library may already fuse the expression; the custom version must be compared with that best maintained path.

## Concepts and code path

The host prepares input, bias, and reference values. The baseline writes an intermediate that a later kernel reads; the fused kernel computes the same result before writing its final output. Device buffers remain resident throughout timing. Logical traffic counts five FP32 elements per output for the separate path and three for the fused path; actual cache/HBM transactions need profiling.

## Practice

Given 256 million FP32 elements, each full-array read or write is about 1 GiB. If separate stages materialize two intermediates, they add roughly 4 GiB of write/read traffic. Change to one fused kernel. Expected observation: launches and intermediate traffic fall, but speedup is kept only if profiler traffic, correctness, and full application time confirm the predicted mechanism.

Run Lab 02 and calculate expected bytes for both paths.

Run the paired smoke paths and a memory check, then use the full case if correctness passes. Both variants are measured by the same executable.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/02_fused_elementwise" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/02_fused_elementwise" --smoke
```

## Check your results

Require both baseline and candidate CPU-reference checks at FP32 tolerances. Inspect `separate` and `fused` timing distributions, `logical_separate_bytes`, and `logical_fused_bytes`. A reduction in logical traffic does not automatically produce an equal percentage reduction in runtime.

Retain launches, logical and estimated physical bytes, bandwidth, time, and numerical equality.

Fusion helps when removed traffic or launch overhead lies on the critical path.

## Investigate the behavior

Draw the intermediate write/read removed by fusion. Which cost matters more for a tiny array: bytes or launches? What extra live values could increase register pressure if many more operations were fused?

Fusion can increase registers and code size, reduce reusable intermediates, complicate dynamic broadcasting, and create many variants. It also changes floating-point evaluation order, requiring explicit tolerance.

## If something goes wrong

A ReLU mismatch may reflect sign handling or changed expression order. Verify both negative and positive reference outputs. Do not accept a candidate that skips work or only matches the easy positive-input region.

Avoid counting only input/output tensors and ignoring intermediate materialization.

## Takeaways and next step

Fusion is valuable when it removes material overhead without violating semantics or resource constraints. Extend the experiment by integrating the operation into an application, and include any changes in allocation or copy costs before claiming an end-to-end benefit.

Count the bytes read and written and validate the fused expression against the unfused reference.

State when excessive fusion can increase registers or reduce maintainability.
