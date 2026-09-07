# Lab 09: Compare a library GEMM with a fused epilogue

The matrix multiplication itself may already be well served by a library, while a following bias and activation add extra work. This lab compares cuBLAS plus a separate bias/ReLU kernel with a source-pinned CUTLASS epilogue path. You will understand the actual implementation choice and its costs before interpreting fusion as a guarantee of superior GEMM performance.

## Before you start

**Theory preparation:** Read Lesson 12 for cuBLAS/CUTLASS, row/column-major conversion, leading dimensions, alpha/beta, SIMT arithmetic and the expanded-bias ReLU epilogue. Reuse Lessons 2–5 for the qualified build, numerical policy and traffic/timing boundary.

Build with the required reviewed CUTLASS 4.6.1 source and `COURSE_ENABLE_CUTLASS=ON`. Use the declared SM90 image and one H100. Missing CUTLASS is a blocked lab, not an accepted cuBLAS-only substitute.

Hopper Tensor Core instructions used by CUTLASS may require `sm_90a`; the course isolates and documents that architecture-specific build. Do not claim forward compatibility for that binary.

## Concepts and code path

The host builds a CPU reference that exercises positive and clamped ReLU outputs. cuBLAS computes row-major GEMM through the equivalent column-major call, followed by a bias/ReLU kernel. The CUTLASS path is pedagogical FP32 SIMT, not a Hopper Tensor Core collective-builder example. It expands the bias into a full source matrix and applies `LinearCombinationRelu` with both alpha and beta set to one; expansion occurs outside timing.

## Practice

Given an FP32 M×N intermediate, it costs 4MN logical bytes to write and another 4MN to read before the separate epilogue writes the final output. Change to a fused epilogue. Expected observation: it may remove that intermediate round trip and a launch. However, Lab 09 also changes the GEMM implementation and reads an expanded bias matrix, so its time difference cannot be attributed only to epilogue placement or called a Tensor Core speedup. Report the full paths and include bias expansion/storage when evaluating integration cost. A tightly controlled epilogue-only extension must hold the underlying GEMM algorithm constant where supported.

Build the required source-pinned CUTLASS path, enabled by default with COURSE_ENABLE_CUTLASS=ON, and run Lab 09's cuBLAS-plus-epilogue and CUTLASS SIMT cases. Check both against the supplied reference. The program offers smoke and full shapes, not an arbitrary-shape CLI; edge-shape coverage is an explicit source-edit/rebuild extension. Leave timing claims pending until the pinned source and CUDA image pass on H100.

Run both required paths and a memory check. The supplied GEMM dimensions `(M, N, K)` are `(128, 192, 96)` for smoke and `(1024, 1536, 768)` for full; arbitrary dimensions require a source edit and rebuild.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/09_library_epilogue" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/09_library_epilogue" --smoke
```

## Check your results

Require both CPU-reference checks and both ReLU branches in the fixture. Inspect cuBLAS-plus-separate and CUTLASS-fused distributions, dimensions, version, and comparison scope. This comparison changes GEMM implementation as well as epilogue placement.

Retain library versions, algorithm/kernel, shapes, launches, time, memory traffic, and numerical error.

Prefer the library path unless a qualified fused path improves the real application.

## Investigate the behavior

Count the extra output pass in the cuBLAS path and the expanded-bias storage in CUTLASS. Why can a fused SIMT implementation lose to a faster library GEMM? Which initialization/copy costs would matter for a one-shot application?

CUTLASS offers composition and source control but creates template/build complexity and qualification work. A separate epilogue is simpler and covers more shapes, while fusion can save launches and intermediate traffic when no other operation needs the intermediate.

## If something goes wrong

Library layout or leading-dimension mistakes often produce structured output errors. A CUTLASS `can_implement` failure must be resolved within the pinned configuration. Do not bypass the reference or version gate to report a timing.

Avoid comparing a cold autotuning library call with a warmed custom candidate.

## Takeaways and next step

Preserve strong GEMM implementations when customizing surrounding work. A further Tensor Core or broadcast-bias implementation is a new candidate requiring its own supported configuration, full correctness, and application-boundary comparison.

Warm both paths, use repeated samples, verify dispatch, and include integration overhead. The included FP32 SIMT path teaches epilogue ownership; it is not a Tensor Core performance claim.

Explain what the epilogue owns and what the GEMM library continues to own.
