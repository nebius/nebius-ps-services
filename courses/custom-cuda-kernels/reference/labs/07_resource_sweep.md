# Lab 07: Relate block size, live state, and occupancy limits

More resident warps can help hide latency, but a kernel's registers, local storage, and block geometry constrain residency. This lab sweeps thread-block sizes and separately increases per-thread state. You will read compiler/runtime resource evidence alongside timing, distinguishing theoretical residency from achieved occupancy and avoiding comparisons that silently change the amount of arithmetic.

## Before you start

**Theory preparation:** Read Lessons 5 and 9 for FMAs, independent accumulators, block size, registers, spills, occupancy APIs and compiler resource reports. Use Lessons 3–4 for correctness and focused profiling; changing state count also changes arithmetic work.

Use the completed SM90 build on one H100 and retain compiler resource output. Read the block/warp hierarchy lesson. Nsight counter access may require the cluster owner's approved profiler configuration.

Use SM90 limits and compiler output. Thread-block cluster kernels need cluster occupancy APIs and may reduce active blocks further.

## Concepts and code path

A fused multiply-add (FMA) computes `a*b + c` with one final rounding, rather than rounding the product separately before adding. Performance accounting conventionally counts its multiplication and addition as two floating-point operations. The device uses `fmaf`; the source checks the first and last outputs against a CPU `std::fma` reference, not every output element.

A templated kernel maintains several per-thread values through repeated FMAs and writes their sum. For four state values, the host sweeps 64, 128, 256, and 512 threads; separate 256-thread cases use 16 and 64 values. CUDA function attributes report registers/local bytes, and the occupancy API estimates active blocks. Increasing state count changes useful arithmetic as well as resource pressure.

## Practice

Given 320 threads per block, 65,536 32-bit registers per SM, and no tighter thread, shared-memory, or block limit, using 128 registers/thread allows floor(65,536/40,960)=1 block. Change register demand to 96 registers/thread: the register limit then allows floor(65,536/30,720)=2 blocks. Expected observation: actual allocation granularity and the compiled kernel must be checked with occupancy APIs. If the cap adds spills and enough local-memory traffic to slow execution, reject it despite higher theoretical occupancy.

Build and run Lab 07 across block sizes and inspect compiler resource usage and Nsight counters.

Run the supplied sweep and memory check. Choose one case for a focused profiler investigation rather than treating all kernels' counters as one aggregate occupancy measurement.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/07_resource_sweep" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/07_resource_sweep" --smoke
```

## Check your results

Require each case's reference check, noting it samples the first and last output only. Inspect registers/thread, local bytes/thread, active blocks/SM, resident warps, median, and p90. Local storage size alone is not a measured spill-traffic counter.

Retain registers, spill loads/stores, shared bytes, achieved occupancy, eligible warps, and time.

The best resource point supplies enough latency hiding without expensive spills or unnecessary instructions. An L2 persisting-access window is an advanced cache-priority hint for a stable, bounded hot region, not a promise to pin data in L2; it competes for cache capacity and needs an application-level A/B test rather than blanket enablement.

## Investigate the behavior

Compare block sizes at fixed state count while keeping the amount of arithmetic fixed. Separately explain the state-count survey's changed arithmetic. Does the fastest case maximize theoretical resident warps, and what profiler evidence could explain a difference?

Larger blocks amortize work and reduce partials but consume resources. Unrolling increases independent work and registers. Launch bounds guide compiler choices but can hurt performance for other shapes or toolchains.

## If something goes wrong

A resource increase can lower occupancy without making execution slower. Do not force a narrative from occupancy alone. Before attributing unexpected local storage to spills, inspect compiler reports and load/store evidence for the selected kernel.

Avoid treating occupancy percentage as the optimization objective.

## Takeaways and next step

Tune resources to the bottleneck rather than chasing a maximum occupancy percentage. Extend to full-output correctness and a register-pressure experiment that holds useful work constant before accepting a production transformation.

Select the fastest correct configuration in the measurements and explain its performance using resource and stall evidence.

State why lower occupancy can be faster.
