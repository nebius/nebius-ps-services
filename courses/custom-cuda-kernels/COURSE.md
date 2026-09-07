# Custom CUDA Kernels for GPU Optimization

This CUDA C++20 course teaches when custom kernels are justified and how to prove correctness, safety, hardware behavior, and end-to-end value on one NVIDIA H100.

## 1. Decide whether a custom kernel is justified

**Start here**

### What a GPU kernel is

A kernel is a function whose device code executes across many GPU threads. The host application launches that function with a grid and block configuration; each thread identifies its own part of the work. For vector addition, one thread might calculate one output element, while a matrix-multiplication kernel assigns cooperating threads to tiles. A kernel is not the whole application, a GPU core, or a Python function that automatically moved to another processor.

This course teaches CUDA C++20: C++ host code plus CUDA device functions for NVIDIA GPUs. GPU Fundamentals explains threads, memory and execution; GPU Optimizations explains how to identify a worthwhile hotspot. You can study this opening without already having a performance problem. Before attempting production specialization, however, you must establish a correct reference and measured reason for the change.

### Why use library kernels or write a custom one

Most applications should begin with maintained implementations. cuBLAS provides dense linear algebra; cuDNN provides deep-learning primitives; CUB, part of CUDA Core Compute Libraries (CCCL), supplies reusable parallel primitives such as reductions; CUTLASS provides composable templates for high-performance linear algebra. Obtain their documented APIs, examples and supported releases from the official resources at the end of this course. A library call may select and launch several internal kernels; there is no single universal collection of source files called the standard CUDA kernels.

Custom means you own the device implementation or a specialized composition. It can help when an important operation has unsupported semantics, unusual shapes, or avoidable intermediate memory traffic that the existing framework/compiler/library path cannot remove. It also makes you responsible for races, out-of-bounds access, numerics, portability and maintenance. A small local speedup is not enough if integration slows the overall application or excludes required inputs.

The first decision example uses general matrix multiplication (GEMM), which combines rows and columns through multiply-and-sum operations. Its epilogue is output processing performed after the matrix product, such as adding a bias offset and applying the rectified linear unit (ReLU), which replaces negative values with zero. Later lessons implement these stages; here they give a concrete example of separating maintained library work from a possible customization.

### How the application chooses CPU or GPU execution

Ordinary host C++ executes on the CPU. A CUDA kernel is marked with `__global__` and launched with CUDA launch syntax; it does not run on the CPU as an ordinary function call. A host reference implementation uses a separate CPU path. The application chooses which implementation to invoke, allocates accessible storage, arranges transfers when needed, and waits before consuming an unfinished result. Compiling with nvcc does not automatically parallelize every C++ loop or copy every host object to device memory.

For the ordinary explicit-memory path, allocate host inputs and device buffers, copy inputs to the device, launch the kernel in a stream, check submission errors, and synchronize at the required completion boundary before inspecting results. Device buffers can remain resident across many launches. Production wrappers usually accept existing device pointers and a stream so they do not force unnecessary copies or device-wide synchronization. The diagram distinguishes application orchestration from the kernel's device work.

### How another application can reuse your kernel

Expose a host-facing API rather than asking every consumer to reproduce launch geometry. Its contract names supported shapes, strides, dtypes, alignment, pointer/device ownership, stream ordering, error reporting and numerical tolerance. It should reject unsupported inputs explicitly. A consumer is simply another program that calls that API.

A source package lets consumers compile for their declared toolkit and GPU targets. A compiled static/shared library needs matching headers, linkage and an explicit binary support matrix. A framework extension additionally needs operator registration and, where applicable, gradient and compiler integration; wrapping a pointer is not sufficient. Shipping an sm_90a binary does not promise forward compatibility. A license, documented build, examples, clean consumer test and correctness/sanitizer evidence are release requirements, not optional polish. Later lessons return to these decisions after the kernel has been validated; nothing in this course publishes an artifact on your behalf.

### Try a small example and choose your route

For inputs [1, 2, 3] and [4, 5, 6], both a CPU loop and a GPU kernel should produce [5, 7, 9]. For only three elements, launch and transfer overhead can dominate; the example teaches indexing and correctness, not a speedup. For larger arrays, measure the complete path as well as resident kernel time.

Lab 01 implements the CPU reference, explicit transfers, bounded GPU indexing and result comparison. Read its guide as a map of component responsibilities. Complete toolchain preflight in Lesson 2, then run the lab on H100 in Lesson 3. New learners continue through correctness tools before optimization. Advanced learners may use the opening checkpoint: explain why a kernel needs a host wrapper, which memory its pointers refer to, and what evidence justifies distributing it.

**Objective** Test framework, compiler, cuBLAS, cuDNN, CUB/CCCL, and CUTLASS options before committing to handwritten CUDA.

**Prerequisite bridge** Fundamentals explains SM90 execution and GPU Optimizations proves a hotspot. The introductory explanation can be studied first; production customization begins only after an end-to-end profile identifies missing semantics or performance that maintained framework and library paths cannot supply.

**Why it matters** A custom kernel transfers ownership of indexing, masking, synchronization, launch geometry, resource use, numerical behavior, profiling, portability, and version maintenance to your team. A microbenchmark win is insufficient if the hotspot contributes little to application time.

**Mechanism** Freeze the operation contract: shapes and dynamic ranges, layouts/strides, dtypes and accumulation, aliasing/mutation, broadcasting, mask and boundary semantics, determinism, synchronization, tolerances, and supported architectures. Build a decision worksheet that estimates performance benefit, implementation effort and maintenance cost for each option. Try batching/vectorization and existing PyTorch operators; then documented compiler fusion/graphs; then cuBLAS, cuDNN, CUB/CCCL, or another maintained library; then a CUTLASS composition such as a custom epilogue; finally handwritten CUDA for the smallest unsupported region. For each option record end-to-end contribution, work expected to be removed, shape coverage, fallback, maintainers, test burden, and portability.

**Recall** Why is kernel speed insufficient to justify a production custom kernel?

**Mental model** A custom kernel adds numerical, architecture, toolchain, maintenance, and integration responsibilities. It is justified only for an important residual gap that narrower supported paths do not solve.

**Practice labs**

- [Lab 01: Launch and validate a bounds-safe vector kernel](reference/labs/01_vector_add.md)

## 2. Build and inspect an SM90 CUDA program

**What it is** A CUDA build translates a program into CPU code and GPU code, then links the pieces and required libraries into an executable. A compiler performs that translation; nvcc is NVIDIA's CUDA compiler driver. CMake describes and organizes the build, including source files, language standards, libraries and target architectures. C++20 names a language-standard version, while SM90 names the GPU machine-code target associated with compute capability 9.0; these version labels describe different things.

PTX is intermediate device instruction code, PTXAS translates it into target machine code, and SASS is the target's machine-instruction representation. A cubin contains compiled device code. An application can also carry PTX for just-in-time translation by a compatible driver. A successful host build therefore does not prove that the right device implementation exists or can run on the intended H100. Toolchain preflight checks those separate layers before performance work begins.

**Objective** Compile CUDA C++20 for compute capability 9.0 and verify the selected H100 and toolchain.

**Prerequisite bridge** The operation contract states the architecture and software support boundary. A reproducible build turns `.cu` source into PTX/cubin code the H100 driver can load.

**Why it matters** Successful host compilation does not prove device code was generated for SM90, loaded on an H100, or linked against the expected runtime and libraries. Native-only flags also make artifacts irreproducible across build hosts.

**Mechanism** CMake enables CUDA as a language, requires C++20/CUDA language standards, and sets `CMAKE_CUDA_ARCHITECTURES=90` for the portable H100 baseline. `nvcc` separates host and device compilation, invokes PTXAS for device machine code, and links CUDA runtime dependencies. Lab 00 reports GPU properties, CUDA runtime version and the CUDA API version supported by the driver. Retain the compiler version and target from the build log separately. Optional features need their own build and device qualification; the basic preflight does not certify them. Inspect compiler resource output and, when needed, cubin/SASS with official binary utilities. Fail before benchmarking if the allocated device is not a full compatible H100 or the build target differs. Keep generated build trees and profiler artifacts private and outside the canonical source tree.
CTest is CMake's test runner: it executes the tests registered by the configured project and reports their outcomes. Configuration chooses the toolchain, options and target; building compiles the targets; CTest then runs the registered checks from that completed build. Use the supplied Slurm build launcher so those steps share the qualified image and allocated GPU. A container packages the development tools and libraries, while its immutable digest fixes the image content; the host still supplies the GPU driver. Record the completed build directory and use it for subsequent launchers. Passing CTest neither adds unregistered tests nor replaces separate sanitizer runs.

**Recall** What is the difference between virtual architecture code and target machine code?

**Mental model** CMake selects CUDA language, host compiler, toolkit, and architectures. The driver loads compatible code on the device; architecture-accelerated `sm_90a` features are not forward-compatible.

**Practice labs**

- [Lab 00: Build and identify the H100 CUDA execution target](reference/labs/00_h100_preflight.md)

## 3. Launch a correct vector kernel

**What it is** A vector kernel applies work to elements of an array. In vector addition, element i of the output is the sum of element i from each input. The programmer maps these logical elements onto GPU threads. Launch geometry specifies the number of blocks and threads per block; a global index combines a thread's block position and its local position within that block. A bounds check prevents extra threads in the final block from accessing elements outside the array.

A grid-stride loop lets each thread handle more elements at regular intervals when the grid is smaller than the input. Pointer aliasing means two names refer to overlapping storage; the restricted-pointer promise allows optimizations only when its non-overlap conditions hold. CUDA submission and execution are asynchronous, so checking whether a launch was accepted differs from waiting to learn whether its device accesses completed correctly.

**Objective** Implement indexing, bounds checks, launch geometry, error checks, and CUDA-event timing.

**Prerequisite bridge** The build produces a kernel, but correctness depends on the mapping from logical elements to grid, blocks, threads, and memory. This vector lab establishes the safety pattern reused by every later kernel.

**Why it matters** Most first kernels fail at non-multiple sizes, asynchronous error reporting, aliasing assumptions, or incomplete timing rather than at the arithmetic expression itself.

**Mechanism** Allocate and initialize host/device buffers, copy inputs, calculate `blocks = ceil_div(n, threads)`, launch, check the launch error immediately, and synchronize at a deliberate boundary to surface execution errors. Each thread computes a global index and guards `i < n`; a grid-stride loop extends coverage when work exceeds one element per thread. `__restrict__` permits stronger alias optimizations only when pointer ranges truly do not overlap. Compare every output with a trusted CPU or maintained CUDA reference, including zero, one, prime, undersized, oversized, and misalignment-relevant shapes. Warm up the kernel first, then use CUDA events around device work and a synchronized end-to-end clock separately.
Resource Acquisition Is Initialization (RAII) ties a C++ resource to an owning object's lifetime. The device-buffer helper allocates storage when constructed and releases it when destroyed, including during exception unwinding. The kernel receives its device pointer but does not acquire ownership. Keep that owner alive until every asynchronous use has completed; an object's existence also does not prevent your code from overwriting its contents too early. This makes allocation cleanup predictable while preserving the explicit stream/completion contract.

For numerical acceptance, require finite outputs and compare each one using `abs(observed-expected) <= atol + rtol*abs(expected)`, following Fundamentals Lesson 1's absolute/relative tolerance rule. Set tolerances before timing and compute an independent CPU reference. A matching first element cannot establish correct tail coverage, and a fast launch that leaves outputs unwritten fails the operation contract.

**Recall** How is a one-dimensional global thread index computed?

**Mental model** Blocks cover the problem in tiles of threads. A ceiling division creates enough blocks, and a bounds check protects the partial final block. A grid-stride loop lets a deliberately bounded grid cover larger arrays; `__restrict__` can expose optimization opportunities only when the caller truly guarantees that the named pointer ranges do not alias.

**Practice labs**

- [Lab 01: Launch and validate a bounds-safe vector kernel](reference/labs/01_vector_add.md)

## 4. Validate with sanitizers and focused profilers

**What it is** A correctness test compares results with an expected answer. A sanitizer observes execution to detect classes of invalid behavior that might accidentally produce the right answer in one test. NVIDIA Compute Sanitizer contains several checkers: memcheck examines memory-access validity, racecheck examines shared-memory hazards, initcheck looks for uses of uninitialized device memory, and synccheck examines synchronization usage. A race occurs when conflicting accesses are not correctly ordered; a barrier coordinates a defined group of participating threads.

Profilers answer a different question. Nsight Systems records the application's execution timeline; Nsight Compute examines selected kernels and hardware counters. A counter is a measurement of hardware activity, not necessarily an elapsed time. Instrumentation and replay can slow or rerun work, so tools explain behavior while separate clean runs supply acceptance timing. Passing a checker supports the exercised inputs and paths, not every possible execution.

**Objective** Use memcheck, racecheck, initcheck, synccheck, Nsight Systems, and Nsight Compute in a deliberate order.

**Prerequisite bridge** Lab 01 now provides a compiled vector kernel, a complete CPU-reference check and event timing. Use that simple program to learn memory checking and focused profiling before optimizing more complex kernels. The shared-memory race and synchronization examples below are previews to revisit with transpose, reduction and stencil.

**Why it matters** A kernel with a race can pass thousands of runs and fail under another schedule or GPU. Profiling incorrect code wastes effort, and profiler overhead must not be mistaken for application timing.

**Mechanism** Run unit/reference tests first. Use Compute Sanitizer memcheck for out-of-bounds, misaligned accesses, leaks, and device exceptions; racecheck for shared-memory hazards; initcheck for uninitialized device memory; and synccheck for invalid barrier/warp synchronization. Fix memory errors before interpreting other tools because each checker has distinct scope. Next use Nsight Systems to locate launches, gaps, copies, and synchronization in the full application. Select only the causal kernel/range for Nsight Compute and a focused metric set; replay and instrumentation add overhead, so collect acceptance timing in an unprofiled run. Archive tool/version, exact command, exit status, and sanitized findings.
A sentinel is a recognizable initial value used to detect missing writes. NaN, meaning Not a Number, is a floating-point value that fails a finite-output check. Before a validation launch that must produce finite values at every output position, initialize the output buffer to NaN; any untouched element then fails validation. Also compare written values with the independent reference because a wrong finite value overwrites the sentinel successfully. Lab 08 uses this pattern for partial tiles. Sentinels supplement sanitizers and must stay outside a kernel-only timing boundary unless initialization is part of the measured operation.

**Recall** Which tool detects an out-of-bounds access and which detects a shared-memory race?

**Mental model** Correctness tests compare outputs with expected results; sanitizers detect invalid behavior; Systems locates the critical path; Compute explains a selected kernel's resource, memory, and instruction behavior.

**Practice labs**

- [Lab 01: Launch and validate a bounds-safe vector kernel](reference/labs/01_vector_add.md)

## 5. Fuse elementwise work to remove HBM traffic

**What it is** An elementwise operation applies a formula independently to corresponding input elements. Scaling multiplies a value, bias adds an offset, and the rectified linear unit (ReLU) activation replaces negative results with zero. Kernel fusion implements several such stages in one kernel. For scale → bias → ReLU, a thread can load an input, carry its intermediate value in a register, and write only the final result. Separate kernels instead communicate through stored intermediate arrays.

Fusion is an implementation change, not permission to change the formula or numerical behavior. Its potential benefit is fewer launches and less intermediate memory traffic, not removal of the required arithmetic. If another operation still needs an intermediate, or the larger kernel uses too many resources, the trade-off changes. This lesson implements fusion in CUDA after the earlier courses introduced its performance rationale.

**Objective** Compare separate scale, bias, and ReLU kernels with one fused kernel.

**Prerequisite bridge** The vector kernel established two input reads, one addition, and one output write. Fusion combines a producer-consumer chain so intermediate values remain in registers instead of returning to HBM.

**Why it matters** Low-intensity elementwise chains often spend more time launching and moving intermediates than computing. Fusion is valuable only when the intermediate has no required external consumer and resource growth does not create a larger limit.

**Mechanism** Build a byte and launch ledger. For `y = relu(scale*x + bias)`, three kernels can read/write intermediate arrays multiple times. A fused kernel loads the required inputs once, performs arithmetic in registers, and writes the final output once. Preserve broadcasting, aliasing, NaN/Inf, signed-zero, precision, and activation semantics. Compare against both the unfused GPU path and a trusted reference. Report logical and estimated physical bytes, launches, CUDA-event distributions, effective bandwidth, registers, occupancy, and end-to-end contribution. Global-memory intermediates are backed by HBM, but later accesses may be served by caches. Count requested logical bytes separately from profiler-measured DRAM bytes; eliminating an intermediate does not guarantee the same reduction in physical HBM traffic.
A fused multiply-add (FMA) computes `a*b+c` with one final rounding, whereas separate multiplication and addition round an intermediate product as well. It counts as two floating-point operations under the course convention. A compiler may contract eligible arithmetic, so establish the allowed numerical result instead of demanding bitwise agreement across differently rounded paths. Later resource and pipeline labs explicitly use `fmaf` and matching CPU `std::fma` references; count their iterations and validate before interpreting throughput.

**Recall** Which intermediate reads and writes disappear after fusion?

**Mental model** Separate elementwise kernels write intermediate arrays to global memory and require multiple launches. Those arrays are backed by HBM, but accesses may be served by caches. Fusion can keep intermediate values in registers between operations.

**Practice labs**

- [Lab 02: Remove an intermediate with elementwise fusion](reference/labs/02_fused_elementwise.md)

## 6. Coalesce global memory and tile a transpose

**What it is** A matrix transpose exchanges rows and columns: the value at input position [row, column] belongs at output [column, row]. A tile is a smaller rectangular region processed by a cooperating block. Coalescing combines nearby addresses requested by a warp into efficient global-memory transactions. A transpose is interesting because a simple mapping that reads neighboring input values can write widely separated output values.

Shared-memory tiling first stages the tile on chip, then lets threads read it in a different order before writing the transposed result. Shared memory is organized into banks that serve accesses; different addresses contending for the same bank can cause a bank conflict. Padding adds an unused storage column to change that mapping without changing the logical matrix. A block barrier makes the staged values available before other threads consume them. Correct handling of edge tiles is part of the algorithm, not an optional performance detail.

**Objective** Turn strided writes into coalesced transactions with a shared-memory tile.

**Prerequisite bridge** Fusion removes unnecessary trips to HBM. Tiling changes the lane-to-address mapping and creates deliberate on-chip reuse or reordering for traffic that must remain.

**Why it matters** A naive transpose has coalesced reads but strided writes, wasting global-memory transactions. A shared tile can make both sides coalesced, yet its own bank mapping can serialize accesses.

**Mechanism** Map lane addresses for the naive kernel. In a tiled transpose, threads cooperatively load a `32×32` region from global memory with contiguous addresses, synchronize, then read the transposed indices from shared memory and write a contiguous output row. Shared memory has 32 banks for successive 32-bit words; a `tile[32][32]` column access gives a stride of 32 banks and can collide. Padding to `tile[32][33]` turns the stride into 33, equivalent to one bank modulo 32. Every thread participating in `__syncthreads()` must reach it, so use predicates around memory operations instead of early return for partial tiles. Record sectors/requests, bank conflicts, effective bandwidth, and edge-shape correctness.

**Recall** Why does a naive transpose coalesce one direction but stride the other?

**Mental model** A tile loads rows coalesced, synchronizes, then writes transposed rows. Padding the shared tile changes bank mapping and avoids column conflicts. CUDA constant memory is a device memory space that kernels read through a dedicated cache. When participating lanes request the same address, one value can be broadcast to them; different addresses require separate servicing. This suits read-only, warp-uniform values, while divergent addresses or large working sets can favor another path.

**Practice labs**

- [Lab 03: Coalesce a transpose and reduce shared-memory conflicts](reference/labs/03_tiled_transpose.md)

## 7. Reduce with warp, block, atomic, and CUB paths

**What it is** A reduction combines many input values into fewer outputs, such as summing an array into one number. An atomic update performs a read-modify-write indivisibly with respect to competing updates to that location, preventing lost contributions; it does not make all threads finish together or guarantee a chosen floating-point addition order. A hierarchical reduction combines values within a warp, then a block, before combining block results. This reduces the number of workers contending for one global output.

Warp shuffle operations exchange register values between participating lanes without storing them in shared memory. An active mask identifies the participating lanes, while block-level cooperation may require shared storage and a barrier. CUB, part of CUDA Core Compute Libraries (CCCL), supplies maintained parallel primitives including device-wide reduction. It is a baseline to compare with, not merely a slower reference implementation to replace.

**Objective** Compare synchronization and aggregation strategies against a maintained library reference.

**Prerequisite bridge** Tiling coordinates threads through shared memory. Reduction coordinates many input contributions into fewer outputs, introducing ordering, synchronization, and floating-point reproducibility questions.

**Why it matters** Having every thread atomically update one value is simple but can serialize. A fast tree can still be wrong if inactive lanes, barriers, or numerical order are mishandled.

**Mechanism** Start with a correct global-atomic baseline. Aggregate within each warp using shuffle operations and an explicit active mask, combine warp results in shared memory with all required block barriers, and issue one atomic or write one partial per block. A second stage can reduce partials. Compare with CUB/CCCL `DeviceReduce`, which is the maintained baseline and often the production choice. Test non-power-of-two sizes, empty/small inputs, NaNs as required, and multiple block counts. Floating-point addition is not associative, so compare to a higher-precision reference with magnitude-aware tolerance and report deterministic versus nondeterministic order.
CUB's device reduction uses temporary workspace. First query the required byte count, allocate that workspace, then reuse it for the actual reduction calls. This avoids timing repeated setup as if it were reduction work. Atomic variants also need their destination reset before each repetition so results do not accumulate across trials; state whether that reset is inside the application's completion boundary even when the kernel-only measurement excludes it. With shuffle reduction, every named lane must obey the mask's participation rules, and the block barrier must make shared warp partials available before another warp reads them.

**Recall** Why does one global atomic per element create contention?

**Mental model** Reduce locally in registers and warps, combine per block, and minimize global atomics. Synchronize at the narrowest correct scope: a warp primitive is not a substitute for a block barrier when multiple warps exchange shared data. CUB provides a production baseline with tuned algorithms.

**Practice labs**

- [Lab 04: Compare atomic, hierarchical, and CUB reductions](reference/labs/04_reduction.md)

## 8. Reuse halos in a shared-memory stencil

**What it is** A stencil computes each output from a small neighborhood of input values. A one-dimensional radius-one stencil reads the left neighbor, center and right neighbor for each output; an image filter extends the idea to a two-dimensional neighborhood. Neighboring outputs often need some of the same inputs, creating an opportunity for reuse. A tile is the set of outputs assigned to a block. Its halo contains the additional neighboring inputs just outside that output region that the calculation still needs.

Boundary conditions specify what happens where a neighbor lies beyond the global input, such as substituting zero or copying an edge value. They are part of the mathematical operation. Shared-memory staging loads a tile and its halo once for block-local reuse; all participating threads must follow the synchronization protocol even if some have no valid output to write.

**Objective** Load a tile and halo safely, reuse neighbors, and handle boundaries.

**Prerequisite bridge** The transpose uses shared memory for reordering. A stencil uses it for overlapping neighborhood reuse, adding halo ownership and boundary conditions.

**Why it matters** Neighboring outputs repeatedly read the same inputs. Cooperative staging can reduce repeated global-memory requests and, when those requests miss the caches, HBM traffic, but one incorrect boundary or divergent barrier can deadlock or corrupt rare shapes.

**Mechanism** For a one-dimensional radius-one stencil, each block loads its interior tile plus left and right halo values into shared memory. Threads predicate global accesses, initialize out-of-range halo values according to the boundary contract, and all reach the barrier. Only then compute outputs from left, center, and right shared elements. In higher dimensions, cooperative loading assigns corners/edges deliberately and accounts for partial blocks. Never return before a block-wide barrier; inactive output threads still participate in staging/synchronization. Compare all boundaries and small shapes with a CPU or maintained reference, then compare naive versus tiled logical global-memory reads, profiler-measured HBM traffic and shared-memory cost.

**Recall** Which input values are shared by adjacent stencil outputs?

**Mental model** A block cooperatively loads its interior and halo into shared memory, synchronizes, and computes several outputs from reused values.

**Practice labs**

- [Lab 05: Reuse neighboring values with a shared-memory halo](reference/labs/05_tiled_stencil.md)

## 9. Balance blocks, registers, spills, and occupancy

**What it is** Resource tuning changes how a kernel uses the finite execution state on each streaming multiprocessor (SM). Registers store per-thread working values, and shared memory supports block-local cooperation. Occupancy is the fraction of the SM's supported warps that can be resident, not the fraction of peak performance achieved. Register pressure describes demand for register storage. A spill places values into device-backed thread-local memory when they cannot remain in registers.

A stack holds thread-local call or temporary state. Instruction-level parallelism (ILP), introduced in Fundamentals, means several instructions can proceed without depending on one another's unfinished results. Updating independent accumulators (variables holding separate running results) exposes this freedom; repeatedly updating one accumulator retains a dependency chain. Loop unrolling expands loop iterations into instructions, which can expose independent work but increase code size and register demand. Launch bounds provide compiler information about intended launch constraints; a register cap limits register use and may induce spills. The kernel's block size changes how these resources are grouped. These choices interact, so a larger occupancy percentage can accompany slower execution rather than indicating a successful optimization.

**Objective** Sweep launch and compiler resource choices and explain the fastest configuration.

**Prerequisite bridge** The vector, transpose, reduction and stencil kernels established launch blocks, registers, shared memory and barriers. Now connect their per-block resource use to resident blocks and warps. This capacity model is the prerequisite for interpreting the next lesson's partial grid waves.

**Why it matters** Occupancy is an input to latency hiding, not a score. Forcing more occupancy can spill registers to local memory, shrink tiles, or reduce instruction-level parallelism.

**Mechanism** Collect PTXAS register, stack, spill-load/store, and shared-memory reports for each build variant. For each block size, calculate constraints from threads/warps, register file allocation, shared memory, and block slots; use CUDA occupancy APIs for the actual kernel. Then measure eligible/issued warp and stall evidence with a focused Nsight Compute set plus unprofiled CUDA-event timing. Change one factor at a time: block size, tile, unroll, launch bounds, or register cap. Inspect local-memory traffic because an array indexed dynamically or a forced cap can spill. Distinguish theoretical occupancy from achieved activity and the reason warps are not eligible.

A persisting L2 access window gives a stable, bounded memory region preferential cache-retention treatment. It is a cache-priority hint, not a promise to pin data in L2. The region competes with other data for cache capacity; whether that policy is useful depends on the workload and must be checked separately from register and occupancy limits.

**Recall** Which resources limit how many blocks can reside on an SM?

**Mental model** Threads, warps, registers, shared memory, and block slots constrain occupancy. Extra registers may improve local reuse until they reduce residency or spill.

**Practice labs**

- [Lab 07: Relate block size, live state, and occupancy limits](reference/labs/07_resource_sweep.md)

## 10. Diagnose divergence, imbalance, and tail waves

**What it is** Divergence, task imbalance and tail waves describe different kinds of uneven execution. Warp divergence occurs when lanes in one warp need different control-flow paths. Task or block imbalance occurs when some independent groups have more work than others. A tail wave occurs near the end of a grid when too few blocks remain to fill the available resident-block slots. None is the same as percentile tail latency across repeated runs.

Regrouping puts similar tasks together, but packing moves their inputs and scattering restores outputs to their original order. A persistent queue lets running workers request more tasks; work stealing lets an idle worker take work from another queue. These scheduling techniques add their own memory and coordination costs. The important question is which level creates the waste, followed by whether a remedy preserves every task and repays its preparation cost.

**Objective** Create and repair three different sources of unused parallel capacity.

**Prerequisite bridge** The resource sweep established how many blocks can reside at once. Now diagnose whether remaining waste comes from active lane masks, unequal block durations or the final partial wave; each is a different scheduling problem.

**Why it matters** Divergence, irregular work, and tail waves can look similar in average utilization while requiring different remedies. One generic “load balance” fix often moves rather than removes cost.

**Mechanism** Warp divergence executes separate control paths under partial lane masks. Block imbalance gives some blocks more iterations, contention, or data-dependent work than others. Tail waves occur when the remaining blocks cannot fill all resource-limited SM slots. Build three controlled inputs: uniform branch work versus mixed lanes; equal blocks versus skewed task lengths; grid sizes just below/above a full wave. Measure active-lane/branch evidence, per-task/block duration proxies, grid/residency waves, and kernel/end-to-end distributions. Candidate remedies include regrouping data, splitting heavy tasks, persistent queues, work stealing, or changing task granularity, each with preprocessing/atomic overhead.

**Recall** How does a divergent warp differ from a final partial grid wave?

**Mental model** Branch divergence changes active lane masks; work imbalance changes block duration; a partial tail wave leaves some execution slots idle after full waves complete; the idle fraction depends on remaining work.

**Practice labs**

- [Lab 06: Group unequal work while preserving output order](reference/labs/06_divergence_tail.md)

## 11. Pipeline global-to-shared copies

**What it is** An asynchronous copy starts a data movement operation without requiring its issuer to wait immediately for completion. A software pipeline divides repeated work into stages, such as loading a tile and computing from it. Double buffering uses two storage regions so one tile can be consumed while another is being produced. This lesson concerns device-side global-to-shared movement inside a kernel, not CPU-to-GPU input transfer.

The producer owns a buffer while filling it; the consumer may read it only after the copy completes and must release it before reuse. Commit and wait operations record progress through that protocol. Priming fills the initial stage, steady state overlaps independent stages, and draining finishes remaining work. CUDA's asynchronous-copy APIs provide mechanisms for this coordination; whether physical overlap occurs depends on the chosen implementation, sufficient independent work and available resources. Merely removing a wait creates a race, not a pipeline.

**Objective** Overlap tile movement with computation using asynchronous copies and double buffering.

**Prerequisite bridge** Shared tiling currently performs load, barrier, compute, then repeats. A pipeline separates producer and consumer stages so the next tile can move while the current tile computes.

**Why it matters** An asynchronous copy does not automatically overlap with computation. Correct stage ownership, alignment, producer commit, consumer wait, buffer reuse, and enough independent compute are required.

**Mechanism** Allocate at least two shared-memory stages. Prime the pipeline by issuing the initial copy. In steady state, wait until the consumer stage is ready, compute from it, and have the producer schedule a future tile into the alternate stage. Commit produced work, release consumed stages, rotate indices, and drain final computation after no more input remains. All participants follow the pipeline/barrier protocol. `cuda::memcpy_async` may select different hardware paths depending on architecture, alignment, and size; TMA is a separate Hopper-capable bulk/tensor mechanism covered later. Verify with sanitizer, correctness, timeline, stalls, resource growth, and end-to-end timing.
Cooperative Groups is CUDA's interface for naming participating thread groups and applying coordinated operations to them. `cooperative_groups::this_thread_block()` names the current block. Lab 08 uses that group with `memcpy_async` to request global-to-shared movement and `wait` before consuming the copied values. Every member follows the same group protocol; a block synchronization also protects shared-buffer reuse after consumption. These are the concrete APIs in this lab, distinct from the related `cuda::memcpy_async` interface. Alignment, byte count and source/destination memory spaces affect which copy path is eligible. Handle the bounded tail explicitly and retain the serial reference rather than assuming that an asynchronous API proves physical overlap.

**Recall** What dependency must complete before computation consumes a newly loaded tile?

**Mental model** Two shared-memory stages let threads compute from one tile while the next tile transfers. Producer/consumer barriers protect stage reuse and data readiness. Warp specialization assigns separate warps to data-production and computation roles so their instruction paths can progress independently; producer/consumer barriers still protect shared stages. A Hopper memory-synchronization domain is an identifier assigned to a kernel launch; its writes and fences carry that identifier. A fence orders memory operations rather than acting as a barrier where all threads meet. Separating independent traffic into domains can keep a fence from waiting for unrelated writes. System scope includes participating host and device threads, rather than only threads on the local GPU. Ordering between different domains requires the documented system-scope fencing, including when both domains are on one GPU. Neither is implemented by this lab; both are advanced extensions requiring a separate correctness design and H100 qualification.

**Practice labs**

- [Lab 08: Double-buffer global-to-shared copies](reference/labs/08_async_pipeline.md)

## 12. Preserve library GEMM and customize the epilogue

**What it is** General matrix multiplication (GEMM) forms a matrix product and may combine it with an existing matrix. Its mainloop computes partial dot products. An epilogue transforms the completed results, for example by scaling, adding bias or applying an activation before storing them. cuBLAS supplies maintained matrix operations. CUTLASS supplies composable CUDA templates that describe matrix kernels and their output processing. Customizing an epilogue can target adjacent work while retaining established matrix-multiplication building blocks.

An M-by-K input multiplied by a K-by-N input produces M-by-N values. Leading dimensions describe the storage step between rows or columns under the chosen layout; alpha and beta scale the product and the additional matrix in the GEMM contract. SIMT and Tensor Core implementations are different arithmetic paths. The included comparison uses an FP32 SIMT CUTLASS path, so its fused output processing must not be described as demonstrating a Tensor Core optimization.

**Objective** Compare cuBLAS plus a separate epilogue with a supported fused library path.

**Prerequisite bridge** Fusion removed elementwise intermediates, while the decision gate said not to rewrite mature GEMM. CUTLASS composes a tuned matrix-multiply mainloop with a customized output epilogue.

**Why it matters** Handwritten GEMM is an inappropriate default for a performance course: it must reproduce years of architecture, shape, precision, and numerical tuning. The remaining optimization opportunity is often work adjacent to GEMM.

**Mechanism** A cuBLAS math mode controls permitted arithmetic choices. Pedantic mode uses the operation's prescribed precision and standardized arithmetic throughout the calculation; it is a numerical policy, not a promise of bitwise equality with a CPU reference. Lab 09 compares two complete FP32 implementations: cuBLAS with a pinned pedantic math mode followed by a separate bias/ReLU kernel, and a source-pinned CUTLASS device::Gemm using OpClassSimt, an Sm80 template specialization compiled for the course target, and LinearCombinationRelu. For CUTLASS, the host expands the N-element bias into an M×N input matrix; this is not an epilogue visitor that broadcasts an N-vector. Match layouts, leading dimensions, alpha/beta, accumulation and reference outputs. Record both kernel identities, initialization/workspace, warm-up, repeated timing, and logical versus measured traffic. Advanced extension: a supported Hopper Tensor Core collective mainloop and broadcast epilogue can avoid the expanded bias, but require a separately chosen template, architecture gates, shape tests, and numerical qualification. The included SIMT path does not demonstrate that implementation.
Storage layout must agree with the library call. Row-major storage puts adjacent columns next to one another; column-major storage puts adjacent rows together. To use the traditional column-major cuBLAS interface for row-major `C = A @ B`, interpret the same buffers as transposes and compute `C.T = B.T @ A.T`, swapping the operand roles and M/N dimensions. Derive each leading dimension from its actual buffer layout; it is a storage stride, not always the logical row count. In the CUTLASS case, alpha=1 includes the product and beta=1 includes the expanded bias matrix before ReLU. Initialize that source matrix before the timed call and keep its bytes in the memory ledger. Changing layout or epilogue scalars changes the operation, not merely a tuning setting.

**Recall** Why is a handwritten GEMM a poor default production exercise?

**Mental model** Vendor and template libraries carry sophisticated Tensor Core, tiling, scheduling, and architecture tuning. A custom epilogue targets the missing fusion without replacing GEMM.

**Practice labs**

- [Lab 09: Compare a library GEMM with a fused epilogue](reference/labs/09_library_epilogue.md)

## 13. Fuse residual addition and RMSNorm

**What it is** A residual connection adds an earlier representation to a transformed one, allowing a block to pass information forward through a shortcut. Root mean square normalization (RMSNorm) rescales a vector according to the square root of its mean squared values, then applies learned scale weights. It changes the vector's scale without first subtracting its mean, unlike LayerNorm. Epsilon is a small positive constant added to keep the denominator well behaved, including for a zero vector.

Fused residual addition plus RMSNorm first forms x + skip and then normalizes that sum inside one implementation. Squaring and summing across a row is a reduction, while applying the inverse scale is elementwise work. Combining them can avoid writing and rereading the residual intermediate, but increases the values a kernel must retain or recompute. The supplied FP32 fixed-width lab teaches this combined structure before optional wider-shape or reduced-precision extensions.

**Objective** Implement an LLM-relevant fused operation with stable reduction and explicit tolerance.

**Prerequisite bridge** Fusion, reduction and resource tuning are now established, with asynchronous pipelines and library epilogues as additional patterns. Residual addition plus RMSNorm combines the core fusion/reduction patterns. Optional Hopper clusters and CUDA Tile C++ are not prerequisites for this case study.

**Why it matters** Separate residual and normalization kernels materialize an intermediate and reread it. A fused kernel can remove traffic, but normalization needs a numerically stable row reduction and support for arbitrary hidden sizes.

**Mechanism** For each row, compute residual `r = x + skip`, retain or reproduce each element, accumulate the mean square in FP32 using a warp/block reduction, calculate `inv_rms = rsqrt(mean(r²)+epsilon)`, multiply by learned weight, and store the output. Define whether the residual itself is also an output. Handle non-power-of-two widths, vectorized aligned loads with scalar tails, epsilon, input/output dtype, FP32 accumulation, and aliasing. Compare with a trusted CUDA C++/CPU or library formulation across random, extreme, zero, odd-width, and reduced-precision cases. Count separate versus fused reads/writes and inspect registers/shared memory.

**Recall** Which values must be reduced to compute RMS normalization?

**Mental model** The kernel adds the residual, computes mean square, forms an inverse root with epsilon, and scales elements. Fusion avoids writing the residual sum before reading it for normalization. Precision-specialized paths may change vector width, accumulation, and math instructions; `--use_fast_math` changes numerical semantics globally and is never a free speed switch.

**Practice labs**

- [Lab 11: Fuse residual addition with row-wise RMS normalization](reference/labs/11_residual_rmsnorm.md)

## 14. Complete a production acceptance capstone

**What it is** Production acceptance is the decision that an implementation meets its declared correctness, safety, performance and support requirements well enough to integrate. A microbenchmark measures a bounded operation; end-to-end timing measures the complete application path. The baseline is the reference implementation, the candidate contains the change, and a rejection is a valid outcome when evidence does not justify maintenance. Amdahl's law expresses why improving a small part of a program can have only a limited effect on total time.

The operating envelope lists supported inputs, numerical formats, layouts and devices. Counterbalanced trials vary which implementation runs first to reduce order bias. Packaging is a later integration step: a host-facing API exposes a callable contract, source distribution lets consumers compile it, and binary distribution supplies already-compiled code with an explicit compatibility contract. A passing teaching executable is not yet a tested reusable package or an authorized public release.

**Objective** Profile a real hotspot, implement one justified change, and make a keep or reject decision.

**Prerequisite bridge** Every lab taught one mechanism in isolation. Production acceptance starts from the application again and proves that a correct, safe kernel changes the end-to-end objective across supported cases.

**Why it matters** Kernel speed, application speed, capacity, portability, and maintenance are separate axes. A narrow benchmark cannot authorize deployment or a broad performance claim.

**Mechanism** Freeze operation semantics, application workload, H100/software identity, and success thresholds. Use an uninstrumented baseline distribution plus a Systems profile to locate the hotspot; use Compute only for the selected kernel. Estimate Amdahl-limited value and compare maintained alternatives. Implement the smallest candidate with trusted reference tests, edge shapes, exact error handling, and declared tolerances. Run all relevant sanitizers, inspect compiler resource reports and use profiling to explain the mechanism, then execute at least three independent kernel and end-to-end trials in counterbalanced order. Test supported shapes/dtypes/layouts and the fallback. Record correctness, non-finite handling, p50/p95 timing, memory, launch count, compilation/startup, maintenance owner, portability, and limitations.

The supplied capstone applies an affine transformation followed by tanh. Affine means scaling an input and adding an offset; tanh, the hyperbolic tangent, is a smooth elementwise transformation bounded between -1 and 1. The baseline writes the affine intermediate and reads it in a second kernel; the candidate evaluates both stages before its final write. Derive the logical traffic and numerical reference for that exact composition, then retain the same expression and tolerances across counterbalanced trials. Fusion can change intermediate rounding as discussed in Lesson 5, so verify both complete outputs independently before interpreting a timing difference.

A reusable release turns an accepted kernel into a supported interface for another program. Its contract includes input validity, device and stream ownership, completion and error behavior, numerical limits, and the supported build/runtime matrix. A clean consumer test checks whether another program can use that interface without depending on the demonstration executable. Source packages and compiled libraries have different distribution obligations, as introduced in Lesson 1; successful compilation alone does not establish runtime support.

**Recall** Which five evidence layers must agree before keeping a custom kernel?

**Mental model** Acceptance connects operation contract, numerical correctness, memory/synchronization safety, kernel evidence, and end-to-end product value plus maintenance boundaries.

**Practice labs**

- [Lab 12: Assemble a kernel acceptance report](reference/labs/12_capstone.md)

## 15. Gate H100 TMA and thread-block clusters

**What it is** The Tensor Memory Accelerator (TMA) is a Hopper data-movement mechanism for transferring described tensor regions asynchronously, including global-to-shared copies, with less per-element address work in ordinary threads. A tensor descriptor records the layout, dimensions and related transfer information. Completion must still be tracked before the transferred data is consumed. TMA is not simply another name for every asynchronous CUDA copy.

A thread-block cluster is a group of cooperating blocks with a stronger scheduling relationship than unrelated grid blocks. Distributed shared memory lets participating blocks access shared-memory regions across that cluster; distributed here means within one GPU's cluster, not across networked nodes. Cluster barriers coordinate the group and protect the lifetime of remotely accessed storage. TMA and clusters are distinct mechanisms that can be combined, but neither automatically makes a kernel faster. This optional lesson separates their definitions from the limited cluster-launch probe actually implemented.

**Objective** Understand TMA, clusters, and distributed shared memory without making them a core portability requirement.

**Prerequisite bridge** The required single-GPU path and its acceptance capstone are complete. This optional branch extends block-scoped shared pipelines with Hopper movement and coordination mechanisms. It does not gate the core course, and its launch and synchronization contracts require separate qualification.

**Why it matters** TMA and clusters can reduce address-generation work or enable reuse beyond one block, yet unsupported shapes, bad descriptors, cluster occupancy, or missing cluster-wide synchronization can make them incorrect or slower.

**Mechanism** TMA describes multidimensional global-memory tensors and issues bulk asynchronous transfers to shared memory, with alignment, bounds, barrier, and completion rules. A designated thread can initiate movement while other threads compute. Thread-block clusters schedule cooperating blocks so they can access distributed shared memory within the cluster; blocks must establish cluster lifetime and use cluster synchronization before remote shared accesses. Build descriptors on the host, validate strides/extents, predicate edges, and compare with a correct portable path. Query supported cluster sizes and occupancy; run memcheck/racecheck/synccheck plus focused profiles. Keep optional code behind explicit build and runtime gates.
An extended launch configuration adds attributes to the grid/block dimensions of an ordinary launch. For the probe, request a cluster dimension of two blocks and a grid of four blocks, giving two clusters; the grid dimensions must be compatible with the requested cluster dimensions and supported device limits. Submit that configuration with `cudaLaunchKernelEx`, check errors and verify every block’s recorded index. This establishes the bounded launch arrangement, not cross-block memory cooperation.

**Recall** Which H100 mechanisms move multidimensional tiles or coordinate blocks in a cluster?

**Mental model** TMA moves described tensor regions with less per-element address work; clusters provide stronger scheduling relationships and access to distributed shared memory. Hopper memory-synchronization domains can reduce unnecessary ordering between independent traffic, but incorrect domain reasoning is a correctness bug rather than an unsuccessful optimization.

**Practice labs**

- [Lab 10: Qualify an optional thread-block-cluster launch](reference/labs/10_hopper_cluster.md)

## 16. Optional appendix: Evaluate CUDA Tile C++

**What it is** Tile-level programming expresses work over blocks of values rather than describing every individual thread's index manipulation. A tile might hold a small matrix region that is loaded, transformed, reduced and stored through collective operations. CUDA Tile C++ provides a C++ interface to NVIDIA's tile-oriented programming model. It is an expression and compiler interface, not an additional physical memory tier, GPU generation or replacement for the CUDA runtime.

In explicit thread/block CUDA, the programmer describes much of the mapping and coordination directly. A tile abstraction delegates more of that mapping to the compiler while retaining requirements about data dependencies, boundaries and numerical behavior. This optional appendix asks whether that abstraction makes a selected kernel easier to maintain on a qualified toolchain. It does not assume the required H100 course build supports it or that a shorter source automatically produces a faster kernel.

**Objective** Decide whether CUDA Tile C++ is useful for a future kernel without making it a prerequisite or silently changing the H100 toolchain.

**Prerequisite bridge** The core course uses stable CUDA C++ concepts and explicit thread/block code. Tile-level programming is an optional expression model that still compiles to kernels governed by the same memory, synchronization, and evidence rules.

**Why it matters** A newer abstraction can reduce indexing code but does not guarantee a better generated kernel, complete feature coverage, or compatibility with the pinned H100 environment.

**Mechanism** Start from an already accepted CUDA C++ lab and restate its operation contract in tile terms: tile shape, load, transform/reduction, synchronization, and store. Pin the required CUDA release and compiler mode, inspect generated target and resources, and retain the original implementation as reference/fallback. Compare edge-shape correctness, sanitizer support, compile time, binary portability, kernel metrics, and end-to-end distributions. Treat API or compiler differences as a separate qualification lane rather than updating the core toolchain implicitly.

**Recall** Which already accepted CUDA C++20 lab could provide a reference for a tile-based experiment?

**Mental model** CUDA Tile C++ expresses work over tile-shaped data and cooperative execution abstractions. Its API and generated code are tied to the selected CUDA toolkit; it does not replace the need to understand memory movement, synchronization, numerical contracts, or profiler evidence.

**Practice labs**

- [Lab 03: Coalesce a transpose and reduce shared-memory conflicts](reference/labs/03_tiled_transpose.md)
