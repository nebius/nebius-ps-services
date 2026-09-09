# Custom CUDA Kernels for GPU Optimization

This CUDA C++20 course teaches when custom kernels are justified and how to prove correctness, safety, hardware behavior, and end-to-end value on one NVIDIA H100.

## 1. Custom kernel decision making

**Objective**

Test framework, compiler, cuBLAS, cuDNN, CUB/CCCL, and CUTLASS options before committing to handwritten CUDA.

**How it works**

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

Expose a host-facing application programming interface (API) rather than asking every consumer to reproduce launch geometry. Its contract names supported shapes, strides, dtypes, alignment, pointer/device ownership, stream ordering, error reporting and numerical tolerance. It should reject unsupported inputs explicitly. A consumer is simply another program that calls that API.

A source package lets consumers compile for their declared toolkit and GPU targets. A compiled static/shared library needs matching headers, linkage and an explicit binary support matrix. A framework extension additionally needs operator registration and, where applicable, gradient and compiler integration; wrapping a pointer is not sufficient. Shipping an sm_90a binary does not promise forward compatibility. A license, documented build, examples, clean consumer test and correctness/sanitizer evidence are release requirements, not optional polish. Later lessons return to these decisions after the kernel has been validated; nothing in this course publishes an artifact on your behalf.

### Execution and dependencies

Fundamentals explains SM90 execution and GPU Optimizations proves a hotspot. The introductory explanation can be studied first; production customization begins only after an end-to-end profile identifies missing semantics or performance that maintained framework and library paths cannot supply.

A custom kernel is useful when a required operation leaves an important gap after maintained paths have been considered. The starting point is the operation's contract: supported shapes and value ranges, layouts/strides, dtypes and accumulation, broadcasting, masks, boundaries, aliasing/mutation, determinism, synchronization and numerical tolerances. Without that contract, a faster implementation may simply compute something different.

The missing work determines the next option. Batching or vectorization can remove repeated small calls. Existing PyTorch operators, compiler fusion or graph replay may remove dispatch or intermediates. cuBLAS, cuDNN and CUB/CCCL provide maintained algorithms, while a CUTLASS composition can specialize an epilogue without replacing the matrix-multiply structure. Handwritten CUDA is the remaining option for the smallest important region those paths cannot supply.

Each candidate should have an explicit reason to help: fewer launches, less data movement, more useful parallel work or support for otherwise unavailable semantics. Its application value depends on how much of the end-to-end critical path that work occupies. A kernel that halves a negligible stage has negligible overall value.

The decision also assigns ownership. Shape coverage, supported architectures, fallback behavior, implementation effort, tests, portability and future maintenance belong beside the expected gain in the worksheet. Accepting a custom path means taking responsibility for those obligations, not merely selecting the fastest microbenchmark row.

A custom kernel transfers ownership of indexing, masking, synchronization, launch geometry, resource use, numerical behavior, profiling, portability, and version maintenance to your team. A microbenchmark win is insufficient if the hotspot contributes little to application time.

### Try a small example

For inputs [1, 2, 3] and [4, 5, 6], both a CPU loop and a GPU kernel should produce [5, 7, 9]. For only three elements, launch and transfer overhead can dominate; the example teaches indexing and correctness, not a speedup. For larger arrays, measure the complete path as well as resident kernel time.

**Practice labs**

- [Lab 01: Launch and validate a bounds-safe vector kernel](reference/labs/01_vector_add.md)

**Mental model**

A custom kernel adds numerical, architecture, toolchain, maintenance, and integration responsibilities. It is justified only for an important residual gap that narrower supported paths do not solve.

## 2. CUDA compilation and execution targets

**Objective**

Compile CUDA C++20 for compute capability 9.0 and verify the selected H100 and toolchain.

**How it works**

A CUDA build translates a program into CPU code and GPU code, then links the pieces and required libraries into an executable. A compiler performs that translation; nvcc is NVIDIA's CUDA compiler driver. CMake describes and organizes the build, including source files, language standards, libraries and target architectures. C++20 names a language-standard version, while SM90 names the GPU machine-code target associated with compute capability 9.0; these version labels describe different things.

Parallel Thread Execution (PTX) is intermediate device instruction code, the PTX assembler (`ptxas`) translates it into target machine code, and SASS is the target's machine-instruction representation. A cubin contains compiled device code. An application can also carry PTX for just-in-time translation by a compatible driver. A successful host build therefore does not prove that the right device implementation exists or can run on the intended H100. Toolchain preflight checks those separate layers before performance work begins.

The operation contract states the architecture and software support boundary. A reproducible build turns `.cu` source into PTX/cubin code the H100 driver can load.

CMake first configures the program's languages, compiler tools, build options and architecture targets. Building then asks `nvcc` to process CUDA source. It separates host and device compilation, uses the host toolchain for CPU code, and invokes device compilation including PTXAS when producing GPU machine code. Linking combines the resulting objects and required CUDA runtime libraries into an executable.

Architecture selection determines what device code the artifact contains. SM90 is the ordinary H100 target. Architecture-accelerated features have narrower compatibility requirements, so generating an SM90 executable does not establish support for every optional Hopper feature. PTX carried with an application also needs a driver capable of translating it for the target.

CTest is a later stage: it runs the checks registered by the CMake project. Configuration succeeding, the executable building and tests running answer different questions. A correct host build alone cannot prove that the intended device code loaded and executed on an H100, and registered tests do not replace sanitizer checks.

A container can package the compiler, headers and libraries under an immutable image digest. The GPU driver still comes from the host environment. Reproducibility therefore requires both a defined build artifact and a compatible execution environment; one compiler-version string cannot stand in for both.

Successful host compilation does not prove device code was generated for SM90, loaded on an H100, or linked against the expected runtime and libraries. Native-only flags also make artifacts irreproducible across build hosts.

**Practice labs**

- [Lab 00: Build and identify the H100 CUDA execution target](reference/labs/00_h100_preflight.md)

**Mental model**

CMake selects CUDA language, host compiler, toolkit, and architectures. The driver loads compatible code on the device; architecture-accelerated `sm_90a` features are not forward-compatible.

## 3. Kernel indexing and execution safety

**Objective**

Implement indexing, bounds checks, launch geometry, error checks, and CUDA-event timing.

**How it works**

A vector kernel applies work to elements of an array. In vector addition, element i of the output is the sum of element i from each input. The programmer maps these logical elements onto GPU threads. Launch geometry specifies the number of blocks and threads per block; a global index combines a thread's block position and its local position within that block. A bounds check prevents extra threads in the final block from accessing elements outside the array.

A grid-stride loop lets each thread handle more elements at regular intervals when the grid is smaller than the input. Pointer aliasing means two names refer to overlapping storage; the restricted-pointer promise allows optimizations only when its non-overlap conditions hold. CUDA submission and execution are asynchronous, so checking whether a launch was accepted differs from waiting to learn whether its device accesses completed correctly.

The build produces a kernel, but correctness depends on the mapping from logical elements to grid, blocks, threads, and memory. This vector lab establishes the safety pattern reused by every later kernel.

The host first prepares initialized input buffers and device storage, then transfers any inputs not already resident. It chooses a thread count per block and computes `blocks = ceil_div(n, threads)`, providing enough threads to cover n elements even when n is not an exact multiple of the block size.

Inside the kernel, a thread forms `i = blockIdx.x * blockDim.x + threadIdx.x`. Only threads with `i < n` load inputs and write an output. Extra threads in the final block therefore perform no invalid access. A grid-stride loop can extend this mapping so a deliberately bounded grid covers more than one element per thread.

Submission and execution have separate error boundaries. The host checks launch errors immediately, then waits at a deliberate completion boundary to surface errors from device execution. It cannot inspect an unfinished result merely because the launch call returned. `__restrict__` adds a non-aliasing promise only when the caller truly guarantees that the relevant pointer ranges do not overlap.

Resource Acquisition Is Initialization (RAII) ties C++ cleanup to an owning object's lifetime. That owner must outlast all asynchronous users of the allocation. Keeping it alive does not prevent an early overwrite, so lifetime and execution ordering remain separate responsibilities.

Correctness compares every output with a trusted CPU or maintained CUDA reference, including empty, one-element, prime, partial-block and alignment-relevant cases within the declared application programming interface (API). Warm-up precedes steady kernel timing. CUDA events measure the device interval, while synchronized end-to-end timing includes the orchestration and transfers required by the application.

Most first kernels fail at non-multiple sizes, asynchronous error reporting, aliasing assumptions, or incomplete timing rather than at the arithmetic expression itself.

`ceil_div(N, B)` means divide N by B and round up to the next whole block. For 1,003 elements and 256 threads per block, four blocks launch 1,024 threads. Indices 0 through 1,002 are valid; the last 21 threads must not access the array. Their output guard must still respect any block-wide synchronization required by the algorithm.

**Practice labs**

- [Lab 01: Launch and validate a bounds-safe vector kernel](reference/labs/01_vector_add.md)

**Mental model**

Blocks cover the problem in tiles of threads. A ceiling division creates enough blocks, and a bounds check protects the partial final block. A grid-stride loop lets a deliberately bounded grid cover larger arrays; `__restrict__` can expose optimization opportunities only when the caller truly guarantees that the named pointer ranges do not alias.

## 4. Kernel correctness and performance evidence

**Objective**

Use memcheck, racecheck, initcheck, synccheck, Nsight Systems, and Nsight Compute in a deliberate order.

**How it works**

A correctness test compares results with an expected answer. A sanitizer observes execution to detect classes of invalid behavior that might accidentally produce the right answer in one test. NVIDIA Compute Sanitizer contains several checkers: memcheck examines memory-access validity, racecheck examines shared-memory hazards, initcheck looks for uses of uninitialized device memory, and synccheck examines synchronization usage. A race occurs when conflicting accesses are not correctly ordered; a barrier coordinates a defined group of participating threads.

Profilers answer a different question. Nsight Systems records the application's execution timeline; Nsight Compute examines selected kernels and hardware counters. A counter is a measurement of hardware activity, not necessarily an elapsed time. Instrumentation and replay can slow or rerun work, so tools explain behavior while separate clean runs supply acceptance timing. Passing a checker supports the exercised inputs and paths, not every possible execution.

Lab 01 now provides a compiled vector kernel, a complete CPU-reference check and event timing. Use that simple program to learn memory checking and focused profiling before optimizing more complex kernels. The shared-memory race and synchronization examples below are previews to revisit with transpose, reduction and stencil.

A reference test first checks that every required output agrees with the intended operation. It can miss invalid behavior that happens to produce the correct answer on the tested schedule, so execution checks add a different form of evidence.

Compute Sanitizer's memcheck detects classes of invalid memory access, including out-of-bounds or misaligned accesses and device exceptions; configured leak checking adds allocation-lifetime evidence. Racecheck examines shared-memory hazards, initcheck examines uninitialized device-memory use, and synccheck examines invalid barrier or warp synchronization. Memory errors should be repaired before interpreting later checker findings, because one bad access can contaminate subsequent behavior. A pass establishes only the exercised paths and each checker's scope.

A sentinel helps detect omitted work. If every valid output must be finite, initializing output storage to not a number (NaN) means an unwritten element remains non-finite and fails validation. A wrong finite write still replaces the sentinel, so every written value must also be compared with the independent reference. Sentinel initialization belongs outside kernel-only timing unless it is part of the measured operation.

Once correctness is established, Nsight Systems locates launches, copies, gaps and synchronization on the application timeline. Nsight Compute then examines a selected kernel with focused metrics. Instrumentation and replay can alter execution, so final acceptance timing comes from a separate unprofiled run. Tool version, command, exit status and sanitized findings define the evidence without exposing raw private traces.

A kernel with a race can pass thousands of runs and fail under another schedule or GPU. Profiling incorrect code wastes effort, and profiler overhead must not be mistaken for application timing.

**Practice labs**

- [Lab 01: Launch and validate a bounds-safe vector kernel](reference/labs/01_vector_add.md)

**Mental model**

Correctness tests compare outputs with expected results; sanitizers detect invalid behavior; Systems locates the critical path; Compute explains a selected kernel's resource, memory, and instruction behavior.

## 5. Elementwise kernel fusion

**Objective**

Compare separate scale, bias, and ReLU kernels with one fused kernel.

**How it works**

An elementwise operation applies a formula independently to corresponding input elements. Scaling multiplies a value, bias adds an offset, and the rectified linear unit (ReLU) activation replaces negative results with zero. Kernel fusion implements several such stages in one kernel. For scale → bias → ReLU, a thread can load an input, carry its intermediate value in a register, and write only the final result. Separate kernels instead communicate through stored intermediate arrays.

Fusion is an implementation change, not permission to change the formula or numerical behavior. Its potential benefit is fewer launches and less intermediate memory traffic, not removal of the required arithmetic. If another operation still needs an intermediate, or the larger kernel uses too many resources, the trade-off changes. This lesson implements fusion in CUDA after the earlier courses introduced its performance rationale.

The vector kernel established two input reads, one addition, and one output write. Fusion combines a producer-consumer chain so intermediate values remain in registers instead of returning to high-bandwidth memory (HBM).

Follow one element through `y = relu(scale*x + bias)`. Separate kernels write the scaled value into one intermediate, read it to add bias, write another intermediate, then read that value to apply ReLU. Each kernel also needs a launch. The arithmetic is small relative to the repeated storage and submission work.

A fused kernel loads the required inputs, carries the intermediate value in registers, performs the same stages and writes the final output once. The saving is the removed launches and intermediate reads/writes. It is valid only if broadcasting, aliasing, not a number (NaN)/Inf, signed-zero, precision and activation behavior still satisfy the operation's contract and no external consumer requires the removed intermediate.

Logical bytes and physical traffic need separate accounting. Global-memory intermediates are backed by HBM, but some accesses may hit caches. Eliminating a logical write or reread therefore does not guarantee an equal reduction in measured dynamic random-access memory (DRAM) bytes. A byte/launch ledger explains the intended change; profiler traffic and unprofiled timing establish its effect.

Fusion can also change rounding. A fused multiply-add computes `a*b+c` with one final rounding, while separate multiplication and addition round the product first. Fused multiply-add (FMA) counts as two floating-point operations under this course's convention. Compiler contraction may select that path, so the reference comparison needs the allowed numerical result rather than automatic bitwise equality. Register use, occupancy and end-to-end contribution reveal whether the larger kernel introduced a cost that offsets the saving.

Low-intensity elementwise chains often spend more time launching and moving intermediates than computing. Fusion is valuable only when the intermediate has no required external consumer and resource growth does not create a larger limit.

**Practice labs**

- [Lab 02: Remove an intermediate with elementwise fusion](reference/labs/02_fused_elementwise.md)

**Mental model**

Separate elementwise kernels write intermediate arrays to global memory and require multiple launches. Those arrays are backed by HBM, but accesses may be served by caches. Fusion can keep intermediate values in registers between operations.

## 6. Memory layout and tiled transposition

**Objective**

Turn strided writes into coalesced transactions with a shared-memory tile.

**How it works**

A matrix transpose exchanges rows and columns: the value at input position [row, column] belongs at output [column, row]. A tile is a smaller rectangular region processed by a cooperating block. Coalescing combines nearby addresses requested by a warp into efficient global-memory transactions. A transpose is interesting because a simple mapping that reads neighboring input values can write widely separated output values.

Shared-memory tiling first stages the tile on chip, then lets threads read it in a different order before writing the transposed result. Shared memory is organized into banks that serve accesses; different addresses contending for the same bank can cause a bank conflict. Padding adds an unused storage column to change that mapping without changing the logical matrix. A block barrier makes the staged values available before other threads consume them. Correct handling of edge tiles is part of the algorithm, not an optional performance detail.

Fusion removes unnecessary trips to high-bandwidth memory (HBM). Tiling changes the lane-to-address mapping and creates deliberate on-chip reuse or reordering for traffic that must remain.

A naive transpose gives neighboring lanes neighboring input columns, so their loads are coalesced. Those values belong to different output rows, however, so the same lane mapping produces strided stores. One mapping cannot make both sides contiguous without rearranging the data between load and store.

The tiled kernel uses shared memory for that rearrangement. Threads cooperatively load a `32×32` region through contiguous global addresses, then all reach `__syncthreads()`. After the barrier, threads read transposed shared indices and write a contiguous output row. The barrier ensures each value has been written before another thread consumes it.

Shared-memory layout matters too. In the 32-bit-word case, H100 shared memory has 32 banks. Reading a column of `tile[32][32]` steps by 32 words, sending different addresses to the same bank. Padding to `tile[32][33]` changes that stride to 33 words: modulo 32, successive accesses advance one bank. The padding changes storage mapping without adding a logical matrix column.

Edge tiles still need the full synchronization protocol. Predicate invalid loads and stores instead of letting some threads return before the block barrier. Correctness must cover the partial edges, while sectors/requests, bank-conflict evidence and effective bandwidth distinguish global-access savings from shared-memory costs.

A naive transpose has coalesced reads but strided writes, wasting global-memory transactions. A shared tile can make both sides coalesced, yet its own bank mapping can serialize accesses.

CUDA constant memory is a device memory space that kernels read through a dedicated cache. When participating lanes request the same address, one value can be broadcast to them; different addresses require separate servicing. This suits read-only, warp-uniform values, while divergent addresses or large working sets can favor another path.

**Practice labs**

- [Lab 03: Coalesce a transpose and reduce shared-memory conflicts](reference/labs/03_tiled_transpose.md)

**Mental model**

Threads load contiguous rows into a shared tile, wait for the tile to be ready, then read it in transposed order for contiguous output stores. Padding changes bank mapping; guards and barriers preserve correctness.

## 7. Parallel reductions

**Objective**

Compare synchronization and aggregation strategies against a maintained library reference.

**How it works**

A reduction combines many input values into fewer outputs, such as summing an array into one number. An atomic update performs a read-modify-write indivisibly with respect to competing updates to that location, preventing lost contributions; it does not make all threads finish together or guarantee a chosen floating-point addition order. A hierarchical reduction combines values within a warp, then a block, before combining block results. This reduces the number of workers contending for one global output.

Warp shuffle operations exchange register values between participating lanes without storing them in shared memory. An active mask identifies the participating lanes, while block-level cooperation may require shared storage and a barrier. CUB, part of CUDA Core Compute Libraries (CCCL), supplies maintained parallel primitives including device-wide reduction. It is a baseline to compare with, not merely a slower reference implementation to replace.

Tiling coordinates threads through shared memory. Reduction coordinates many input contributions into fewer outputs, introducing ordering, synchronization, and floating-point reproducibility questions.

The simplest sum reduction lets each thread atomically add its contribution to one global result. Atomicity prevents lost updates, but all those updates compete for the same location. It also does not impose a reproducible floating-point addition order.

A hierarchical reduction combines values before they reach that bottleneck. Threads first accumulate local values in registers. Participating lanes exchange and combine partials with warp shuffles. Their explicit mask defines participation; every named lane must follow the primitive's rules, and an inactive source cannot be treated as a valid contribution.

Next, warp representatives write their partial sums to shared memory. A block barrier makes those writes visible before the consuming warp combines them. The block then issues one global atomic or writes one partial for a later reduction kernel. Warp synchronization cannot replace a block barrier when values cross warp boundaries.

CUB/CCCL `DeviceReduce` supplies a maintained implementation and is often the appropriate production choice. Comparisons include non-power-of-two, empty/small and multiple-block cases, plus the declared not a number (NaN) behavior. Because floating-point addition is not associative, different valid trees can differ numerically. A higher-precision reference and magnitude-aware tolerance check the result, while deterministic versus nondeterministic ordering remains an explicit part of the contract.

Having every thread atomically update one value is simple but can serialize. A fast tree can still be wrong if inactive lanes, barriers, or numerical order are mishandled.

For a four-value reduction, start with [1, 2, 3, 4]. Two independent pairs produce 3 and 7; the next level adds those partials to produce 10. A larger reduction repeats that tree across thread, warp and block scopes. Every parent must wait until its inputs exist. Integer arithmetic makes this small illustration exact; floating-point regrouping can change rounding, so correctness uses the declared tolerance.

**Practice labs**

- [Lab 04: Compare atomic, hierarchical, and CUB reductions](reference/labs/04_reduction.md)

**Mental model**

Reduce locally in registers and warps, combine per block, and minimize global atomics. Synchronize at the narrowest correct scope: a warp primitive is not a substitute for a block barrier when multiple warps exchange shared data. CUB provides a production baseline with tuned algorithms.

## 8. Neighborhood reuse and boundary handling

**Objective**

Load a tile and halo safely, reuse neighbors, and handle boundaries.

**How it works**

A stencil computes each output from a small neighborhood of input values. A one-dimensional radius-one stencil reads the left neighbor, center and right neighbor for each output; an image filter extends the idea to a two-dimensional neighborhood. Neighboring outputs often need some of the same inputs, creating an opportunity for reuse. A tile is the set of outputs assigned to a block. Its halo contains the additional neighboring inputs just outside that output region that the calculation still needs.

Boundary conditions specify what happens where a neighbor lies beyond the global input, such as substituting zero or copying an edge value. They are part of the mathematical operation. Shared-memory staging loads a tile and its halo once for block-local reuse; all participating threads must follow the synchronization protocol even if some have no valid output to write.

The transpose uses shared memory for reordering. A stencil uses it for overlapping neighborhood reuse, adding halo ownership and boundary conditions.

For a radius-one stencil, each output needs its left neighbor, center and right neighbor. A block owns a tile of outputs, but computing its first and last outputs also needs one input outside each end of that tile. Those extra values form the left and right halo.

The block first loads its interior inputs and halos into shared memory. Threads responsible for out-of-range global positions write the values required by the boundary condition instead of making invalid reads. Once staging is complete, every participating thread reaches the barrier. Valid output threads can then read their three shared values and compute their result.

Threads with no output in a partial block may still be needed for staging or synchronization. Returning before the block-wide barrier can therefore break the algorithm even if that thread would never store an output. In higher dimensions, edge and corner halo ownership must be assigned just as explicitly.

Cooperative staging reduces repeated global requests for overlapping neighborhoods, but caches may already serve some naive rereads. Logical request counts, measured high-bandwidth memory (HBM) traffic and shared-memory cost answer different questions. Boundary and small-shape reference checks establish correctness first; any naive-versus-tiled performance comparison needs separately timed implementations rather than an assumed speedup from the traffic model.

Neighboring outputs repeatedly read the same inputs. Cooperative staging can reduce repeated global-memory requests and, when those requests miss the caches, HBM traffic, but one incorrect boundary or divergent barrier can deadlock or corrupt rare shapes.

**Practice labs**

- [Lab 05: Reuse neighboring values with a shared-memory halo](reference/labs/05_tiled_stencil.md)

**Mental model**

A block cooperatively loads its interior and halo into shared memory, synchronizes, and computes several outputs from reused values.

## 9. Kernel resource use and occupancy

**Objective**

Sweep launch and compiler resource choices and explain the fastest configuration.

**How it works**

Resource tuning changes how a kernel uses the finite execution state on each streaming multiprocessor (SM). Registers store per-thread working values, and shared memory supports block-local cooperation. Occupancy is the fraction of the SM's supported warps that can be resident, not the fraction of peak performance achieved. Register pressure describes demand for register storage. A spill places values into device-backed thread-local memory when they cannot remain in registers.

A stack holds thread-local call or temporary state. Instruction-level parallelism (ILP), introduced in Fundamentals, means several instructions can proceed without depending on one another's unfinished results. Updating independent accumulators (variables holding separate running results) exposes this freedom; repeatedly updating one accumulator retains a dependency chain. Loop unrolling expands loop iterations into instructions, which can expose independent work but increase code size and register demand. Launch bounds provide compiler information about intended launch constraints; a register cap limits register use and may induce spills. The kernel's block size changes how these resources are grouped. These choices interact, so a larger occupancy percentage can accompany slower execution rather than indicating a successful optimization.

The vector, transpose, reduction and stencil kernels established launch blocks, registers, shared memory and barriers. Now connect their per-block resource use to resident blocks and warps. This capacity model is the prerequisite for interpreting the next lesson's partial grid waves.

A compiled kernel requests resources before its blocks can become resident. PTXAS reports register use, stack storage, spill loads/stores and shared memory. Combined with a chosen block size, those demands compete with the SM's thread, warp, register, shared-memory and block-slot limits. The first exhausted resource bounds residency; CUDA occupancy APIs apply those limits to the actual kernel.

Residency only provides possible work. A resident warp may be stalled on a dependency and unable to issue. Eligible/issued-warp observations and stall metrics help explain whether the scheduler has useful choices, while unprofiled CUDA-event timing determines whether a variant completes sooner. Theoretical occupancy and achieved execution activity are different quantities.

Changing block size, tile size, unrolling, launch bounds or a register cap can move several costs at once. More live values may improve reuse or instruction-level parallelism but reduce resident blocks. A forced register cap, or some dynamically indexed local arrays, can introduce device-backed local-memory traffic. That added traffic can overwhelm the benefit of higher occupancy, which is why variants change one factor at a time.

A persisting L2 (level-two cache) access window addresses a separate resource. It gives a bounded memory region preferential cache-retention treatment, not guaranteed residency in L2. That region competes with other data and needs its own reuse evidence; the policy does not repair register spills or alter the SM's occupancy limits.

Occupancy is an input to latency hiding, not a score. Forcing more occupancy can spill registers to local memory, shrink tiles, or reduce instruction-level parallelism.

**Practice labs**

- [Lab 07: Relate block size, live state, and occupancy limits](reference/labs/07_resource_sweep.md)

**Mental model**

Threads, warps, registers, shared memory, and block slots constrain occupancy. Extra registers may improve local reuse until they reduce residency or spill.

## 10. Parallel workload balance

**Objective**

Create and repair three different sources of unused parallel capacity.

**How it works**

Divergence, task imbalance and tail waves describe different kinds of uneven execution. Warp divergence occurs when lanes in one warp need different control-flow paths. Task or block imbalance occurs when some independent groups have more work than others. A tail wave occurs near the end of a grid when too few blocks remain to fill the available resident-block slots. None is the same as percentile tail latency across repeated runs.

Regrouping puts similar tasks together, but packing moves their inputs and scattering restores outputs to their original order. A persistent queue lets running workers request more tasks; work stealing lets an idle worker take work from another queue. These scheduling techniques add their own memory and coordination costs. The important question is which level creates the waste, followed by whether a remedy preserves every task and repays its preparation cost.

The resource sweep established how many blocks can reside at once. Now diagnose whether remaining waste comes from active lane masks, unequal block durations or the final partial wave; each is a different scheduling problem.

Unused capacity can arise at three different levels. Within a warp, divergent control paths execute under different active-lane masks. Across blocks, unequal iteration counts, contention or data-dependent work let some blocks finish earlier than others. At the end of a grid, even equal-duration blocks can leave a partial wave with too few blocks to fill the available SM slots.

Each explanation predicts a different control. Uniform branch work versus mixed lanes isolates divergence. Equal task lengths versus skewed lengths isolates block imbalance. Grid sizes near a full-wave boundary expose the scheduling tail, while requiring care about changed total work. Active-lane metrics, task-duration proxies and grid/residency counts should be interpreted at their matching level.

A remedy must remove more cost than it adds. Regrouping similar tasks may reduce divergence but requires packing inputs and scattering outputs back to their original positions. Splitting heavy tasks can balance durations but adds coordination. Persistent queues or work stealing supply remaining tasks to ready workers while introducing queue and atomic overhead. Changing granularity can reduce a partial wave yet alter resource use.

Kernel and end-to-end distributions show whether the complete remedy helps. Preserving every task, its output position and its numerical result is part of that comparison; dropping expensive work or omitting regrouping time changes the question.

Divergence, irregular work, and tail waves can look similar in average utilization while requiring different remedies. One generic “load balance” fix often moves rather than removes cost.

Suppose a hypothetical device can keep eight equally sized blocks resident for this kernel and the grid has 18 blocks. Under the simplifying assumption that block durations match, two groups of eight can fill those slots, while the final two blocks occupy only two of the eight. This last group is a tail wave. It is different from divergence inside a block and from one block running longer than its peers; the device need not schedule real blocks in perfectly separated waves.

**Practice labs**

- [Lab 06: Group unequal work while preserving output order](reference/labs/06_divergence_tail.md)

**Mental model**

Branch divergence changes active lane masks; work imbalance changes block duration; a partial tail wave leaves some execution slots idle after full waves complete; the idle fraction depends on remaining work.

## 11. Asynchronous memory pipelines

**Objective**

Overlap tile movement with computation using asynchronous copies and double buffering.

**How it works**

An asynchronous copy starts a data movement operation without requiring its issuer to wait immediately for completion. A software pipeline divides repeated work into stages, such as loading a tile and computing from it. Double buffering uses two storage regions so one tile can be consumed while another is being produced. This lesson concerns device-side global-to-shared movement inside a kernel, not CPU-to-GPU input transfer.

The producer owns a buffer while filling it; the consumer may read it only after the copy completes and must release it before reuse. Commit and wait operations record progress through that protocol. Priming fills the initial stage, steady state overlaps independent stages, and draining finishes remaining work. CUDA's asynchronous-copy APIs provide mechanisms for this coordination; whether physical overlap occurs depends on the chosen implementation, sufficient independent work and available resources. Merely removing a wait creates a race, not a pipeline.

Shared tiling currently performs load, barrier, compute, then repeats. A pipeline separates producer and consumer stages so the next tile can move while the current tile computes.

A double-buffered device pipeline reserves at least two shared-memory stages. During the initial fill, the producer issues a global-to-shared copy for the first tile and commits that produced work. The consumer waits until the stage is ready before reading it. Issuing or committing a copy does not mean its bytes are already safe to consume.

In steady state, computation reads one completed tile while a future tile can be copied into the alternate stage. After computation finishes using a stage, the consumer releases it so the producer may reuse that storage. Rotating stage indices repeats the same ownership cycle. The final drain completes all remaining copied and computed work after no new input is issued.

All participants must follow the same copy, wait and barrier protocol. Cooperative Groups names the participating CUDA thread group and supplies coordinated operations for it; a group application programming interface (API) does not excuse a member from the required synchronization. Omitting a wait creates a race, and reusing a stage too early overwrites data still in use.

`cuda::memcpy_async` can use different hardware paths depending on architecture, alignment, byte count and source/destination spaces. Tensor Memory Accelerator (TMA) is a separate Hopper-capable bulk/tensor mechanism introduced later. Physical overlap also needs enough independent compute and available resources; the API call alone does not prove it. Correctness, sanitizer findings, timeline/stall evidence, extra shared-memory/register demand and complete elapsed time together describe the pipeline's effect.

An asynchronous copy does not automatically overlap with computation. Correct stage ownership, alignment, producer commit, consumer wait, buffer reuse, and enough independent compute are required.

Warp specialization assigns separate warps to data-production and computation roles so their instruction paths can progress independently; producer/consumer barriers still protect shared stages. A Hopper memory-synchronization domain is an identifier assigned to a kernel launch; its writes and fences carry that identifier. A fence orders memory operations rather than acting as a barrier where all threads meet. Separating independent traffic into domains can keep a fence from waiting for unrelated writes. System scope includes participating host and device threads, rather than only threads on the local GPU. Ordering between different domains requires the documented system-scope fencing, including when both domains are on one GPU. Neither is implemented by this lab; both are advanced extensions requiring a separate correctness design and H100 qualification.

**Practice labs**

- [Lab 08: Double-buffer global-to-shared copies](reference/labs/08_async_pipeline.md)

**Mental model**

A pipeline overlaps a future tile's copy with the current tile's computation. Each stage becomes readable only after its copy completes and reusable only after its consumers finish.

## 12. Matrix multiplication and output fusion

**Objective**

Compare cuBLAS plus a separate epilogue with a supported fused library path.

**How it works**

General matrix multiplication (GEMM) forms a matrix product and may combine it with an existing matrix. Its mainloop computes partial dot products. An epilogue transforms the completed results, for example by scaling, adding bias or applying an activation before storing them. cuBLAS supplies maintained matrix operations. CUTLASS supplies composable CUDA templates that describe matrix kernels and their output processing. Customizing an epilogue can target adjacent work while retaining established matrix-multiplication building blocks.

An M-by-K input multiplied by a K-by-N input produces M-by-N values. Leading dimensions describe the storage step between rows or columns under the chosen layout; alpha and beta scale the product and the additional matrix in the GEMM contract. Single-instruction, multiple-thread (SIMT) and Tensor Core implementations are different arithmetic paths. Fused output processing can use either path; fusion alone does not demonstrate Tensor Core execution.

Fusion removed elementwise intermediates, while the decision gate said not to rewrite mature GEMM. CUTLASS composes a tuned matrix-multiply mainloop with a customized output epilogue.

A GEMM mainloop accumulates the matrix product. Output-side work can then scale it, add another matrix or bias, apply ReLU and store the result. If that epilogue runs as a separate kernel, it must read the GEMM output and write its transformed result. A compatible fused epilogue applies those operations before the final store, potentially removing that intermediate traffic and launch.

The arithmetic policy remains part of correctness. A cuBLAS math mode controls permitted computation choices; pedantic mode follows prescribed precision and standardized arithmetic but does not promise bitwise equality with a CPU reference. A CUTLASS composition must separately support the desired mainloop, epilogue and layout. Using CUTLASS alone does not establish a Hopper Tensor Core path or broadcast-bias implementation. The supplied 32-bit floating point (FP32) SIMT case and its expanded bias remain distinct from those extensions.

Storage interpretation must also match the call. Row-major arrays place adjacent columns together; column-major arrays place adjacent rows together. To compute row-major `C = A @ B` through the traditional column-major cuBLAS interface, interpret the same buffers as transposes and compute `C.T = B.T @ A.T`. This swaps operand roles and M/N dimensions without physically transposing the data.

Each leading dimension comes from the actual buffer's storage stride, not automatically its logical row count. Incorrect layout, alpha or beta changes the operation rather than merely selecting a different implementation. The complete comparison therefore includes numerical policy, layout, output-side work and full operation cost.

Handwritten GEMM is an inappropriate default for a performance course: it must reproduce years of architecture, shape, precision, and numerical tuning. The remaining optimization opportunity is often work adjacent to GEMM.

**Practice labs**

- [Lab 09: Compare a library GEMM with a fused epilogue](reference/labs/09_library_epilogue.md)

**Mental model**

Vendor and template libraries carry sophisticated Tensor Core, tiling, scheduling, and architecture tuning. A custom epilogue targets the missing fusion without replacing GEMM.

## 13. Residual connections and normalization

**Objective**

Implement an large language model (LLM)-relevant fused operation with stable reduction and explicit tolerance.

**How it works**

A residual connection adds an earlier representation to a transformed one, allowing a block to pass information forward through a shortcut. Root mean square normalization (RMSNorm) rescales a vector according to the square root of its mean squared values, then applies learned scale weights. It changes the vector's scale without first subtracting its mean, unlike LayerNorm. Epsilon is a small positive constant added to keep the denominator well behaved, including for a zero vector.

Fused residual addition plus RMSNorm first forms x + skip and then normalizes that sum inside one implementation. Squaring and summing across a row is a reduction, while applying the inverse scale is elementwise work. Combining them can avoid writing and rereading the residual intermediate, but increases the values a kernel must retain or recompute. The supplied 32-bit floating point (FP32) fixed-width lab teaches this combined structure before optional wider-shape or reduced-precision extensions.

Fusion, reduction and resource tuning are now established, with asynchronous pipelines and library epilogues as additional patterns. Residual addition plus RMSNorm combines the core fusion/reduction patterns. Optional Hopper clusters and CUDA Tile C++ are not prerequisites for this case study.

For each row, the kernel first forms the residual values `r = x + skip`. RMSNorm needs their mean square, so it squares the row elements, combines them through a warp/block reduction in FP32 and divides by the row width. It then computes `inv_rms = rsqrt(mean(r²)+epsilon)` and produces each output as its residual value times `inv_rms` times the learned weight.

Every output depends on the same row reduction. The residual values must therefore remain available until that reduction finishes, either retained in registers/shared memory or reproduced afterward. Fusion avoids writing the residual to global memory solely for the normalization to reread it. If the residual is itself a required external output, its store cannot be removed; that choice belongs in the operation contract.

The supplied lab uses FP32 and a fixed width of 256. Extending it to arbitrary or non-power-of-two widths requires correct reduction participation, aligned vector loads with safe scalar tails, and explicit input/output dtypes, epsilon and aliasing rules. Those cases are qualifications to add, not capabilities implied by a fixed-width pass.

Trusted CPU, CUDA or library results check zero, random and extreme inputs within the supported scope. Wider and reduced-precision extensions need their own tolerances. A read/write ledger explains potential savings, while register/shared-memory use and an independently timed baseline establish whether retaining or recomputing residual values costs less than materializing them.

Separate residual and normalization kernels materialize an intermediate and reread it. A fused kernel can remove traffic, but normalization needs a numerically stable row reduction and support for arbitrary hidden sizes.

Precision-specialized paths may change vector width, accumulation, and math instructions; `--use_fast_math` changes numerical semantics globally and is never a free speed switch.

The reciprocal-square-root operation is `rsqrt(v) = 1 / sqrt(v)`. It converts the positive mean-square-plus-epsilon value into the shared normalization scale; epsilon prevents division by zero for an all-zero row.

**Practice labs**

- [Lab 11: Fuse residual addition with row-wise RMS normalization](reference/labs/11_residual_rmsnorm.md)

**Mental model**

The kernel adds the residual, computes a row’s mean square, forms an inverse root with epsilon, and scales each element. Fusion keeps the residual sum available for normalization without writing and rereading an intermediate array.

## 14. Kernel acceptance and integration

**Objective**

Profile a real hotspot, implement one justified change, and make a keep or reject decision.

**How it works**

Production acceptance is the decision that an implementation meets its declared correctness, safety, performance and support requirements well enough to integrate. A microbenchmark measures a bounded operation; end-to-end timing measures the complete application path. The baseline is the reference implementation, the candidate contains the change, and a rejection is a valid outcome when evidence does not justify maintenance. Amdahl's law expresses why improving a small part of a program can have only a limited effect on total time.

The operating envelope lists supported inputs, numerical formats, layouts and devices. Counterbalanced trials vary which implementation runs first to reduce order bias. Packaging is a later integration step: a host-facing application programming interface (API) exposes a callable contract, source distribution lets consumers compile it, and binary distribution supplies already-compiled code with an explicit compatibility contract. A passing teaching executable is not yet a tested reusable package or an authorized public release.

Every lab taught one mechanism in isolation. Production acceptance starts from the application again and proves that a correct, safe kernel changes the end-to-end objective across supported cases.

Acceptance begins at the application boundary. Fixed operation semantics, workload, H100/software identity and success thresholds define what a candidate must preserve. An uninstrumented baseline distribution establishes elapsed time, and a Systems profile identifies how much of it belongs to the suspected hotspot. Amdahl's law then bounds the application value available even from a perfect replacement.

Maintained alternatives come before a new implementation. If a gap remains, the smallest candidate receives independent reference checks, edge-shape cases, explicit errors and numerical tolerances. Relevant sanitizers examine memory and synchronization behavior; compiler reports and a focused Compute profile explain resources and the proposed performance mechanism.

At least three independent kernel and end-to-end trials, with counterbalanced order, test whether the saving survives normal variation and integration overhead. Supported shapes, dtypes, layouts and any declared fallback are part of that evidence. Correctness, non-finite handling, p50/p95 timing, memory, launch count and compilation/startup costs determine the result together with ownership, portability and limitations.

A reusable release adds another boundary: another program calls a supported interface. That contract specifies input validity, device and stream ownership, completion/error behavior, numerical limits and the build/runtime matrix. A clean consumer test checks use without relying on the teaching executable. Source packages and compiled libraries have different distribution obligations; successful compilation alone does not establish runtime support or authorize publication.

Kernel speed, application speed, capacity, portability, and maintenance are separate axes. A narrow benchmark cannot authorize deployment or a broad performance claim.

Suppose a kernel accounts for 40% of a 10-millisecond application step. Making that kernel twice as fast changes its 4 milliseconds to 2; the other 6 remain, so the step becomes 8 milliseconds and the application speedup is 10 / 8 = 1.25. This is the idea behind Amdahl's law: unchanged work limits the total benefit. Integration overhead can reduce the gain further. A p50 latency is the median; p95 is the 95th percentile, below which approximately 95% of observations fall.

**Practice labs**

- [Lab 12: Assemble a kernel acceptance report](reference/labs/12_capstone.md)

**Mental model**

Acceptance connects operation contract, numerical correctness, memory/synchronization safety, kernel evidence, and end-to-end product value plus maintenance boundaries.

## 15. Advanced GPU data movement and cooperation

**Objective**

Understand TMA, clusters, and distributed shared memory without making them a core portability requirement.

**How it works**

The Tensor Memory Accelerator (TMA) is a Hopper data-movement mechanism for transferring described tensor regions asynchronously, including global-to-shared copies, with less per-element address work in ordinary threads. A tensor descriptor records the layout, dimensions and related transfer information. Completion must still be tracked before the transferred data is consumed. TMA is not simply another name for every asynchronous CUDA copy.

A thread-block cluster is a group of cooperating blocks with a stronger scheduling relationship than unrelated grid blocks. Distributed shared memory lets participating blocks access shared-memory regions across that cluster; distributed here means within one GPU's cluster, not across networked nodes. Cluster barriers coordinate the group and protect the lifetime of remotely accessed storage. TMA and clusters are distinct mechanisms that can be combined, but neither automatically makes a kernel faster. This optional lesson separates their definitions from the limited cluster-launch probe actually implemented.

The required single-GPU path and its acceptance capstone are complete. This optional branch extends block-scoped shared pipelines with Hopper movement and coordination mechanisms. It does not gate the core course, and its launch and synchronization contracts require separate qualification.

TMA begins with a descriptor of a multidimensional tensor's layout and extents. A designated thread can issue a bulk asynchronous transfer, including global-to-shared movement, while other threads perform independent work. Alignment, bounds, barriers and completion rules determine when the destination is safe to consume. Correct descriptor construction and edge handling are part of the algorithm, not optional checks around an otherwise ordinary copy.

Thread-block clusters address cooperation between blocks. They schedule a supported group with a stronger relationship than unrelated grid blocks, allowing access to shared-memory regions across the cluster. Blocks must establish the required cluster lifetime and synchronization before remote shared accesses, and keep the target block's storage alive until all such accesses finish. Cluster size and resource use can limit occupancy.

TMA and clusters are distinct mechanisms. The optional course probe tests only a cluster launch: an extended configuration requests two blocks per cluster and a grid of four blocks, producing two clusters if the device supports that arrangement. `cudaLaunchKernelEx` submits the attributes along with the ordinary grid/block configuration, and checking every recorded block index verifies the bounded launch.

That result does not demonstrate TMA transfers or distributed shared-memory cooperation. Those extensions need their own descriptor/stride validation, portable reference, supported cluster/occupancy checks, sanitizers and focused profiles. Explicit build and runtime gates keep their narrower qualification separate from the core SM90 course.

TMA and clusters can reduce address-generation work or enable reuse beyond one block, yet unsupported shapes, bad descriptors, cluster occupancy, or missing cluster-wide synchronization can make them incorrect or slower.

**Practice labs**

- [Lab 10: Qualify an optional thread-block-cluster launch](reference/labs/10_hopper_cluster.md)

**Mental model**

TMA moves described tensor regions with less per-element address work; clusters provide stronger scheduling relationships and access to distributed shared memory. Hopper memory-synchronization domains can reduce unnecessary ordering between independent traffic, but incorrect domain reasoning is a correctness bug rather than an unsuccessful optimization.

## 16. Tile-level kernel programming

**Objective**

Decide whether CUDA Tile C++ is useful for a future kernel without making it a prerequisite or silently changing the H100 toolchain.

**How it works**

Tile-level programming expresses work over blocks of values rather than describing every individual thread's index manipulation. A tile might hold a small matrix region that is loaded, transformed, reduced and stored through collective operations. CUDA Tile C++ provides a C++ interface to NVIDIA's tile-oriented programming model. It is an expression and compiler interface, not an additional physical memory tier, GPU generation or replacement for the CUDA runtime.

In explicit thread/block CUDA, the programmer describes much of the mapping and coordination directly. A tile abstraction delegates more of that mapping to the compiler while retaining requirements about data dependencies, boundaries and numerical behavior. This optional appendix asks whether that abstraction makes a selected kernel easier to maintain on a qualified toolchain. It does not assume the required H100 course build supports it or that a shorter source automatically produces a faster kernel.

The core course uses stable CUDA C++ concepts and explicit thread/block code. Tile-level programming is an optional expression model that still compiles to kernels governed by the same memory, synchronization, and evidence rules.

A tile-oriented version starts with the same accepted operation and expresses it in larger units of data. Instead of spelling out every thread's scalar index, it describes a tile shape, loads a tile, applies transformations or reductions, coordinates dependencies and stores the result. The compiler takes responsibility for more of the mapping onto execution resources.

That change in expression does not remove the operation's contract. Partial edge tiles still need safe behavior; loads must finish before consumption; reductions need the intended numerical semantics; and outputs must match the accepted CUDA C++ reference. A shorter source may generate more or less efficient instructions, so readability and performance remain separate questions.

The optional evaluation fixes the required CUDA release and compiler mode, inspects generated targets and resource use, and retains the original implementation as the reference or explicitly supported fallback. Edge-shape correctness, available sanitizer coverage, compile time, binary portability, kernel metrics and complete timing define the comparison.

Application programming interface (API) and compiler support are a separate qualification lane. Trying this expression model does not implicitly update the core H100 toolchain or make CUDA Tile C++ a prerequisite for the rest of the course.

A newer abstraction can reduce indexing code but does not guarantee a better generated kernel, complete feature coverage, or compatibility with the pinned H100 environment.

**Practice labs**

- [Lab 03: Coalesce a transpose and reduce shared-memory conflicts](reference/labs/03_tiled_transpose.md)

**Mental model**

CUDA Tile C++ expresses work over tile-shaped data and cooperative execution abstractions. Its API and generated code are tied to the selected CUDA toolkit; it does not replace the need to understand memory movement, synchronization, numerical contracts, or profiler evidence.
