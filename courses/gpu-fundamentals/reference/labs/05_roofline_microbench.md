# Lab 05: Contrast memory-oriented and compute-oriented work

Roofline reasoning starts by counting work and data movement before looking at achieved speed. This lab provides two contrasting points: an elementwise vector expression and a matrix multiplication. You will connect their different reuse opportunities to possible bottlenecks without mistaking two measurements for a complete intensity sweep or a measured hardware ceiling.

## Before you start

**Theory preparation:** Read Lessons 2, 4, 9 and 10 for GEMM work, memory traffic, precision and arithmetic intensity/rooflines. Lesson 4 is an input-and-code preview only. Run the complete experiment after Lesson 10; the script has no byte-ledger-only mode.

Use one H100 and record its exact variant, precision mode, and environment. Obtain any proposed bandwidth or compute ceiling independently from an appropriate specification or calibrated experiment, not from the result being evaluated.

H100 has several compute roofs by dtype and instruction path and different bandwidth ceilings by SKU and configuration. State which roof is used and whether it is theoretical or measured on the target.

## Concepts and code path

Recall the matrix-product model from Lab 02: `A[M,K] @ B[K,N]` produces `M*N` outputs, each summing `K` products. The dimensions `M` and `N` count output rows and columns; `K` is the shared reduction dimension. Reusing an input value across outputs is what makes matrix multiplication different from an elementwise operation in the traffic model.

The vector expression performs two counted operations per element and assumes twelve logical bytes: two FP32 reads and one write. Its modeled intensity is `2/12` FLOP/byte. The GEMM reuses matrix elements and uses a conventional `2*M*N*K` numerator. CUDA events time repeated warmed operations. Actual cache behavior, intermediate traffic, and Tensor Core selection require separate evidence.

## Practice

Given a kernel performing 2 billion useful operations while moving 20 GB across HBM, intensity is 0.1 operation per byte. With a measured 2 TB/s bandwidth roof, its memory bound is 0.2 TOP/s. Change the algorithm to halve traffic without changing operations. Expected observation: the bandwidth-derived bound doubles from 0.2 to 0.4 TOP/s. The overall roofline is the smaller of this bound and the compute ceiling. This arithmetic does not depend on the starting measured performance; actual speedup depends on whether memory traffic was limiting execution.

Use Lab 05's vector-expression and GEMM cases as two contrasting intensity points. It does not supply a continuous intensity sweep. Extension: parameterize reuse, declare FLOPs and logical bytes at each point, add an independent numerical reference, and compare achieved rates with independently established roofs before locating a crossover.

Each profile runs one vector case and one GEMM case. Use the larger profile as a second declared workload, not as an automatic fixed-work optimization comparison.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/05_roofline_microbench.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/05_roofline_microbench.py --profile h100
```

## Check your results

Inspect `bandwidth_kernel` and `gemm` distributions and their derived rates. The implementation checks only finite outputs; it does not prove numerical agreement with an independent reference. Add that reference before accepting a modified kernel or publishing an optimization claim.

Retain operations, bytes, achieved bandwidth, achieved compute rate, and the declared theoretical or measured ceilings.

Roofline is a classification model; its accuracy depends on correct byte and operation accounting.

## Investigate the behavior

Calculate `min(compute_ceiling, bandwidth_ceiling * intensity)` with consistent units and an independently chosen ceiling. Explain why measured achieved throughput is compared with this bound rather than used to define it. Distinguish logical bytes from profiler-observed traffic.

Fusion can raise intensity by removing intermediate HBM traffic but may increase registers, reduce occupancy, or limit reuse. Recomputing a cheap value can save bytes; recomputing expensive values can move the kernel toward the compute roof and slow it.

## If something goes wrong

A rate above your proposed roof often indicates inconsistent units, dtype peaks, or byte conventions. Check GiB versus GB and dense versus sparse peaks before assuming hardware broke a limit.

Avoid treating peak hardware rates as guaranteed application performance or ignoring intermediate tensors.

## Takeaways and next step

This is a two-point classification exercise. A genuine intensity sweep is an extension requiring parameterized reuse, explicit FLOP/byte accounting, correctness references, and a record of how the workload changes at every point.

Optimize data movement below the ridge point and execution throughput above it, then validate with profiler evidence.

Explain why fusion often helps bandwidth-bound elementwise sequences.
