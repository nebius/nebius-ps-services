# Where to Go Next

These optional directions combine recent
programming tools with established advanced kernel designs. They do not
replace the CUDA C++20 labs, change the pinned development environment or
add completion requirements. Python-based tools below are comparative
reading only; all learner kernel implementations in this course remain C++.
Start with the Hopper design, then compare abstraction levels or newer hardware.

## Hopper warp-specialized persistent kernels

Warp specialization assigns different warps to tasks such as loading data
and performing matrix operations. A persistent design lets resident blocks
process multiple work tiles rather than finishing after one tile. Together,
these ideas extend double buffering into explicit scheduling of producers,
consumers and work distribution.
**Investigate**: Which event proves a shared-memory buffer can be reused,
and what happens if one stage becomes the bottleneck?
**Scope**: Established advanced CUTLASS design relevant to H100. Exact kernel
variants have architecture and toolchain requirements; Hopper and Blackwell
implementations do not use identical schedules.
[Study CUTLASS Hopper warp specialization](https://docs.nvidia.com/cutlass/latest/media/docs/cpp/efficient_gemm.html#hopper-warp-specialization).

## CUDA Tile C++ and compiler-managed tiles

CUDA Tile expresses operations on tiles of data while the compiler manages
much of their mapping to threads, memory movement and asynchronous execution.
It offers a different abstraction from manually scheduling SIMT threads,
not a guarantee that arbitrary code becomes fast. This is a next step from
the course's optional Tile appendix into portability and control trade-offs.
**Investigate**: Which tuning responsibilities move into the compiler, and
which shape, layout and numerical choices remain yours?
**Scope**: Recent CUDA 13.3 direction; NVIDIA documents Hopper support.
It remains an optional, separately qualified toolchain path, not a required
change to the C++20 baseline.
[Explore CUDA Tile](https://developer.nvidia.com/cuda/tile) and
[CUDA Tile C++ compilation](https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/tile-compilation-in-cuda.html).

## CuTe DSL and explicit kernel abstractions

CuTe DSL is a Python-based domain-specific language for expressing GPU kernels
using layouts, tensor operations and architecture-aware primitives. It offers
a way to study the same mapping decisions encountered in CUTLASS C++ through
a different authoring interface. It is not ordinary eager Python execution
on the GPU.
**Investigate**: How much control does the author retain over layouts,
asynchronous pipelines and matrix instructions compared with CUDA Tile?
**Scope**: Evolving CUTLASS tooling. Documented coverage includes Hopper
warpgroup operations and separate Blackwell primitives; consult limitations
for the chosen release. Comparative reading only, not a Python lab.
[Read the CuTe DSL overview](https://docs.nvidia.com/cutlass/latest/media/docs/pythonDSL/overview.html).

## CompileIQ and compiler-configuration autotuning

CompileIQ searches compiler configuration choices for a specific workload.
Instead of changing the algorithm, it explores how compiler decisions such
as register allocation and instruction scheduling affect generated code.
A good benchmark objective must reject incorrect results and avoid choosing
a configuration that only benefits one convenient input.
**Investigate**: Does a winning configuration remain correct and useful
across held-out shapes, and does its benefit repay the search cost?
**Scope**: Recent tooling introduced with CUDA 13.3. Its search orchestration
uses Python, so this is reading-only here. Compiler version, GPU, numerical
checks and repeatable timing must be recorded; no speedup is assumed.
[Read NVIDIA's CompileIQ introduction](https://developer.nvidia.com/blog/extract-more-kernel-performance-with-nvidia-compileiq-auto-tuning/).

## Blackwell tensor memory and fifth-generation matrix instructions

Blackwell SM100 introduces tensor memory (TMEM), an on-chip storage space used
by its fifth-generation matrix multiply-accumulate instructions, known as
`tcgen05`. Placing accumulators there changes the relationship between matrix
operations, registers and synchronization. This is an instruction-level
extension to the general architecture comparison in Fundamentals.
**Investigate**: Which accumulator-lifetime and synchronization assumptions
must change when moving from a Hopper matrix kernel to this design?
**Scope**: Blackwell-specific study, not available on H100/SM90. Do not assume
every Blackwell device supports every SM100 primitive; select the documented
architecture target and treat architecture-specific binaries accordingly.
[Study the tcgen05 programming model](https://github.com/NVIDIA/cutlass/blob/main/media/docs/pythonDSL/mma_docs/tcgen05_programming.rst).
