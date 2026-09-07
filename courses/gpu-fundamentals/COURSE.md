# GPU Fundamentals for NVIDIA H100 Performance Engineering

This course builds the hardware and system model needed to explain GPU behavior before changing code. Every conclusion must connect workload shape, H100 resources, and measured evidence.

## 1. Separate CPU work, GPU work, and orchestration

**Start here**

### What this course is about

A CPU (central processing unit) runs the operating system and the ordinary instructions of your application. A GPU (graphics processing unit) is a processor designed to make progress on many similar pieces of work at once. This course explains how the two cooperate, how an NVIDIA H100 stores and executes work, and how to tell whether a workload is using it effectively. An array is an ordered collection of numbers; a tensor is a multidimensional array, such as a matrix of image pixels or model weights. You do not need prior CUDA knowledge to begin. Familiarity with a small Python program is enough for the explanations; the practical exercises introduce the relevant PyTorch operations.

### Why use a GPU rather than a CPU

CUDA is NVIDIA's platform for programming its GPUs. PyTorch is a numerical-computing and machine-learning framework that expresses operations on tensors and can use CUDA to execute supported work on NVIDIA devices. A tensor's shape gives its dimensions, while its dtype specifies how each value is represented, such as 32-bit floating point. Batching means processing several items together; synchronization means waiting for required work to finish before using its result. These names describe different parts of execution, not interchangeable ways of saying that a GPU is busy.

Consider applying the same formula to one million independent values. A CPU can perform this correctly, using optimized vector instructions and several cores. A GPU can schedule far more concurrent arithmetic work and has high-bandwidth device memory, so sufficiently large parallel workloads may finish sooner. It does not calculate an entire large matrix in one instant: work is divided into many pieces and takes many execution cycles. GPUs are useful for matrix algebra, image processing, scientific simulation and neural networks because these problems often expose substantial parallelism and data reuse.

The CPU remains a good choice for small jobs, branch-heavy control logic, irregular pointer chasing, file handling and tasks whose data would cost more to move than to process. A CPU-only implementation does not become incorrect merely because the GPU is available; it may simply take longer for a suitable large workload, or it may win for a small one. Both latency (time for one result) and throughput (completed work per second) matter. The right device is an evidence-based choice, not a property of the programming language.

### How the CPU, memory and GPU cooperate

The CPU is the host and the GPU is the device. Host RAM and the H100's high-bandwidth memory, or HBM, are distinct storage resources in the discrete-device model used here. A kernel is a function executed by GPU threads. A launch submits that work; it is not the same thing as copying its input data. A stream is an ordered queue of device work. PyTorch uses the tensor's device and the available implementation to dispatch an operation: CPU tensors use a CPU path; CUDA tensors use a GPU path when supported. Moving a tensor with `to("cuda")` transfers its data; it does not move the Python interpreter onto the GPU.

The overview below separates two kinds of hierarchy. Physically, HBM/global allocations and the device-wide L2 cache serve many streaming multiprocessors (SMs). Each SM contains execution resources, registers and a unified L1/shared-memory resource. L1 caches data automatically; shared memory is explicitly managed temporary storage for cooperating threads in a block. They are not successive compulsory stops for every memory access. Logically, a launch creates a grid of blocks, each block contains threads, and groups of 32 threads form warps. The hardware schedules these groups onto SM resources. NVIDIA's four SM subpartitions are subdivisions inside one SM, not four quadrants of the whole GPU. Later lessons zoom into each relationship.

A preflight is a small readiness check before an experiment. The driver connects the operating system to the GPU; the CUDA runtime supplies services such as allocation and launches; a compiler translates source into executable code. They are separate components, so their version numbers need not be identical. Use the environment setup and Lab 10 to check the allocated device and the framework's CUDA path before timing. `nvidia-smi` reports device/driver information, while a framework query reports the runtime it uses; neither alone proves that a source compiler is installed. Lesson 2 develops compilation and compatibility in detail.

An elementwise operation applies a formula independently at each tensor position; multiplication followed by addition is a small example. A reference computes the intended answer so timing cannot reward missing or changed work. Floating-point arithmetic rounds values, so numerical acceptance needs a declared tolerance. Absolute tolerance permits a fixed difference near zero; relative tolerance scales with the reference magnitude. The elementwise rule is `abs(candidate - reference) <= atol + rtol * abs(reference)`. With reference 2, `atol=1e-6` and `rtol=1e-5`, the allowance is `2.1e-5`. Check all required outputs and reject non-finite values when the operation requires finite results. Pure copies of fixed values can instead require exact equality. A finite result alone does not establish agreement with a reference.

Slurm is the workload manager that assigns cluster resources to jobs. The supplied `sbatch` commands submit batch scripts that run the lab inside an allocation; they do not run GPU work directly on the login host. Use the course environment and the allocated device reported by preflight. A smoke profile selects a smaller test workload, not a different proof of correctness or a CPU substitute. The runbook supplies the exact setup and launch procedure.

### Try a small example and choose your route

Suppose a CPU operation takes 2 milliseconds. A resident GPU operation takes 0.3 milliseconds, but uploading inputs and obtaining the result add 3 milliseconds. For this hypothetical one-shot request the GPU path takes 3.3 milliseconds, so the CPU wins. If ten dependent operations keep intermediate data on the GPU, the same total transfer cost plus ten 0.3-millisecond operations is 6 milliseconds, compared with ten 2-millisecond CPU operations. These are teaching assumptions, not H100 measurements.

Start with the compatibility check, then Lab 01 compares CPU, resident-GPU and transfer-inclusive execution of the same formula. Explain what each timer includes before interpreting a speedup. New learners then follow the software, execution and memory lessons in order. Experienced engineers can use this checkpoint: explain a launch versus a copy, a block versus an SM, and why keeping intermediate data resident can change the device decision.

**Objective** Decide which work belongs on the CPU, which belongs on the GPU, and where launch or transfer overhead dominates.

**Prerequisite bridge** Start with ordinary program latency: a result is ready only after every required stage finishes. GPU execution adds explicit submission, transfer, device-execution, and synchronization stages to that familiar critical path.

**Why it matters** “The GPU is faster” is not an engineering rule. A GPU wins when enough independent work amortizes fixed overhead and when data can stay resident long enough to avoid repeated host-device movement. Interactive latency and batch throughput can therefore prefer different placements.

**Mechanism** Separate three boundaries before comparing devices. CPU time includes the complete CPU operation. Resident GPU time assumes inputs and outputs are already on the device and measures only required device work. Transfer-inclusive GPU time includes copies, launches, synchronization, and result access. The CPU dispatches asynchronous commands into a stream; the driver and runtime enqueue work; the device executes later. Batching increases parallel work per launch, but it also increases queueing delay, temporary memory, and the amount of work tied to one completion point. Before the first benchmark, use warm-up runs to initialize the path without including setup in steady-state samples. A CUDA event is a marker placed in the device's work queue: record start and end around the selected device work and wait for the end before reading elapsed time. For a transfer-inclusive host measurement, wait for earlier work before starting the clock and for the requested result before stopping it. Repeat the experiment and compare distributions, not a single sample. These are the basic timing rules; Lesson 8 expands streams, copies and overlap.

**Recall** A GPU is not a faster serial CPU; what workload property lets thousands of threads make progress together?

**Mental model** The CPU schedules and prepares work while the GPU executes wide batches of similar operations. Transfers and kernel launches are explicit boundaries, so small jobs can spend more time crossing boundaries than computing.

**Practice labs**

- [Lab 01: Find the CPU–GPU crossover for vector work](reference/labs/01_cpu_gpu_crossover.md)
- [Lab 10: Identify the software layer behind GPU execution](reference/labs/10_compatibility_stack.md)

## 2. Read the driver, runtime, toolkit, PTX, SASS, and framework stack

**What it is** The GPU software stack is the set of layers that turns an application into work the device can execute. A driver lets the operating system and programs communicate with the GPU. The CUDA runtime provides callable services—an application programming interface (API)—for allocating memory, submitting work and checking completion. The CUDA Toolkit supplies development tools, including the compiler that translates source code. PyTorch sits above these layers and selects implementations for tensor operations. A Python wheel is an installable package; it may bundle runtime libraries without including the full toolkit.

PTX is an intermediate GPU instruction language; SASS is target-specific machine code. A cubin contains compiled device code, while a fat binary can carry code for multiple targets. Just-in-time (JIT) compilation translates code when needed at load or execution time. Compute capability names a GPU's hardware feature level, not its driver or toolkit version. These separate meanings explain why matching one version number does not establish compatibility.

**Objective** Explain which layer owns compilation, loading, compatibility, and execution.

**Prerequisite bridge** Lesson 1 identified a software boundary between submitted work and device execution. This lesson names every layer at that boundary so a version string or error can be assigned to an owner.

**Why it matters** CUDA failures are often “repaired” at the wrong layer. Installing a local toolkit cannot fix an insufficient kernel-mode driver, and a new driver does not make a Python package contain missing CUDA libraries or kernels for the correct architecture.

**Mechanism** The kernel-mode NVIDIA driver controls the device. User-mode CUDA driver APIs load modules and launch kernels. A framework wheel may bundle a CUDA runtime and libraries independently of any developer toolkit installed on the host. `nvcc` compiles CUDA C++ into PTX, cubins, or a fat binary containing several targets. PTX is a virtual instruction representation that a compatible driver may JIT for a GPU; SASS is the final architecture-specific machine instruction stream. A prebuilt wheel can therefore execute without a local `nvcc`, while building an extension requires a compatible compiler and headers. DMA means direct memory access: a transfer engine moves data without CPU instructions copying each byte.
A matrix product combines a row of one matrix with a column of another by multiplying corresponding entries and summing them. Multiplying shapes M×K and K×N produces M×N values; `[1, 2]` times the column `[3, 4]` produces 11. GEMM is the general matrix-multiplication operation, commonly written `C = alpha*A*B + beta*C`, with alpha and beta scaling its contributions. Here matrix multiplication combines rows and columns; later Python expressions use `@` for that operation and `*` for elementwise multiplication. The product alone costs approximately `2*M*N*K` floating-point operations under the usual multiply-plus-add convention. A projection applies such a matrix to change feature coordinates. Adding a length-N bias to each output row uses broadcasting: the same bias values apply across rows without requiring a separately authored row for each input.

Lab 08 composes a projection, LayerNorm, bias, GELU and a reduction. Layer normalization (LayerNorm) subtracts the mean of a selected feature vector and divides by the square root of its variance plus a small positive epsilon; it can also apply learned scale and offset. This stabilizes the vector's scale. GELU, the Gaussian error linear unit, is the nonlinear function `x*Phi(x)`, where Phi is the cumulative probability of a standard normal variable. It suppresses strongly negative inputs and approaches the identity for strongly positive ones. Applying it after a projection creates a nonlinear transformation. Squaring the resulting elements and taking their mean reduces them to one scalar, a single numerical value. This lab uses the scalar as observable work, without training a model. BF16 stores each value in 16 bits, with a wide exponent range and fewer precision bits than FP32; here it fixes the workload representation, with accuracy comparisons reserved for Lesson 9.

PyTorch Profiler records framework operations and their CPU/device activity. Enable CPU and CUDA collection around a warmed, bounded operation chain, then associate framework entries with the kernels they submit. Self time excludes recorded child events; inclusive time includes them. A single framework operation may dispatch several kernels, and nested entries can overlap in attribution. Use the trace to explain the chain, not to reconstruct elapsed time by summing arbitrary rows. Profiling adds overhead; Lesson 1's unprofiled timing remains the performance boundary.

Compute capability identifies the GPU architecture features an executable targets. H100 has compute capability 9.0. An SM90 target provides ordinary code for that architecture; an SM90a target can use architecture-accelerated features with a narrower compatibility boundary. PTX compatibility still depends on a driver able to translate the supplied representation. These target names describe code requirements, not proof that an application has executed successfully.

**Recall** Which installed component communicates with the GPU, and which component supplies the compiler and development libraries?

**Mental model** PyTorch calls CUDA libraries and runtime APIs; kernels are distributed as machine code or PTX; the driver loads and executes compatible code. The driver and toolkit are related but not interchangeable version numbers.

**Practice labs**

- [Lab 08: Map PyTorch operations to GPU activity](reference/labs/08_operator_to_kernels.md)
- [Lab 10: Identify the software layer behind GPU execution](reference/labs/10_compatibility_stack.md)

## 3. Map H100, GPCs, SMs, warps, and Tensor Cores

**What it is** A GPU has physical execution resources, while a CUDA program describes logical workers. A thread is one worker executing the kernel; a block is a group of threads that can cooperate; a grid is the collection of blocks in one launch. Threads are organized into warps of 32, and a lane is one thread's position within its warp. A streaming multiprocessor (SM) is a physical unit that hosts blocks and executes their warps. Several SMs belong to a graphics processing cluster (GPC); an SM itself contains SM subpartitions (SMSPs) with scheduling and execution resources. These are different levels, not interchangeable names for GPU quadrants.

Tensor Cores are specialized matrix-arithmetic units within an SM. Different launches need not assign corresponding blocks to the same SM; within an ordinary launch, each block remains on its assigned SM for its lifetime. The later Triton examples use a GPU-programming language and compiler whose program instances describe cooperating work, not a different physical GPU hierarchy.

**Objective** Trace a PyTorch operation down to blocks scheduled on H100 streaming multiprocessors.

**Prerequisite bridge** The software stack loads a kernel, but the kernel still needs a physical execution model. Reuse the grid, block, and warp vocabulary whenever later lessons discuss occupancy, divergence, memory, or Tensor Cores.

**Why it matters** A high-level operator can be slow because it dispatches an unexpected kernel, launches too little parallel work, creates a partial final wave, or uses the wrong instruction path. None of those causes is visible from the Python name alone.

**Mechanism** HBM feeds memory controllers and L2; work is distributed through GPU processing clusters into streaming multiprocessors. A CUDA grid contains blocks, and each block remains on one SM for its lifetime. The SM partitions warp scheduling and execution across SMSPs; warps contain 32 lanes, while Tensor Cores execute supported matrix instructions issued by those warps. Registers are logically private to threads, and the unified L1/shared-memory capacity is divided by a configurable carveout. In Triton, a program instance and `BLOCK_SIZE` are analogous scheduling choices, not new hardware levels; `num_warps` controls how many warps cooperate in a program.
Triton is a language and compiler for writing GPU operations using blocks of array elements. A program instance describes one logical block of work; `tl.program_id(0)` identifies that instance along the first grid dimension. To cover N elements with B logical elements per program, launch `ceil(N/B)` instances and form indices `program_id*B + [0, ..., B-1]`. A mask `index < N` disables out-of-range loads and stores. For N=1003 and B=256, four programs cover 1024 logical positions and the final 21 are masked. This is ceiling division and tail masking, not permission to access the extra positions.

In Lab 09, `BLOCK_SIZE` chooses B and `num_warps=4` requests four cooperating warps, or 128 threads, per program. A thread can handle several logical elements, so B is not the CUDA thread count. The JIT compiler specializes the operation before the warmed measurement. Predict coverage from B, retain masks, and vary B while keeping the four-warp setting fixed; explain occupancy only after Lesson 6.

**Recall** How many threads form an NVIDIA warp?

**Mental model** A kernel creates a grid of thread blocks. Blocks are assigned to SMs; each block contains warps of 32 threads; warp instructions use scalar, vector, memory, and matrix pipelines according to the instruction mix.

**Practice labs**

- [Lab 08: Map PyTorch operations to GPU activity](reference/labs/08_operator_to_kernels.md)
- [Lab 09: Sweep logical work per Triton program](reference/labs/09_triton_launch_geometry.md)
- [Lab 11: Separate lane utilization from grid-tail behavior](reference/labs/11_scheduler_tail.md)

## 4. Follow data through registers, caches, shared memory, and HBM

**What it is** The memory hierarchy is a set of storage locations with different capacities, access costs and sharing rules. Registers hold a thread's working values. Shared memory is fast on-chip storage explicitly managed by cooperating threads in a block. A cache automatically retains copies of recently accessed data: L1 is close to an SM and L2 is shared more broadly across the GPU. Device allocations in the global address space normally live in high-bandwidth memory (HBM). An address space describes which addresses code can refer to, not necessarily a distinct physical memory chip.

An allocation reserves storage for values. Temporal locality means reusing the same values soon; spatial locality means accessing nearby addresses. A spill places values that cannot stay in registers into thread-private local memory, which is backed by device memory despite its name. Understanding ownership and reuse comes before deciding where data should live.

**Objective** Choose the nearest useful memory level and recognize when data movement dominates.

**Prerequisite bridge** Resident threads need storage for private values, block cooperation, cached reuse, and the full data set. These are distinct address spaces and lifetime contracts, not simply “fast” and “slow” memory.

**Why it matters** Performance is often limited by repeated movement rather than arithmetic. Correctly naming where a byte lives reveals whether the next change should improve reuse, remove an intermediate, alter layout, or reduce the workload's memory footprint.

**Mechanism** Registers hold compiler-managed thread values. Local memory is a per-thread address space but is generally backed by device memory and cached, so spills are not on-chip registers. Shared memory is explicitly allocated per block and requires synchronization for cooperative use. L1 and L2 caches capture temporal and spatial reuse without changing program semantics. HBM holds global allocations. In PyTorch, `memory_allocated` tracks live tensor storage while `memory_reserved` includes allocator-managed segments; neither tells you which values the kernel placed in registers or shared memory.
A tensor view changes shape or indexing metadata while sharing existing storage. Its strides specify the storage-element step for each dimension. For a compact 2×3 matrix, strides `[3,1]` mean one row advances three elements and one column advances one. Transposing it produces a 3×2 view with strides `[1,3]`; the six stored values have not moved. `contiguous()` materializes a compact copy when the current layout is not already contiguous. Use shape, strides and element size to derive addresses and count input, output and temporary bytes. Count the copy separately before considering whether repeated later accesses repay it; Lesson 7 develops lane-level coalescing and that reuse trade-off.

**Recall** Which memories are private to a thread, shared by a block, or visible device-wide?

**Mental model** Registers are thread-local, shared memory is explicitly managed per block, caches capture reuse, and HBM provides large capacity with much higher latency. Useful reuse should occur before data returns to HBM.

**Practice labs**

- [Lab 04: Evaluate strided access and the cost of repacking](reference/labs/04_layout_and_coalescing.md)
- [Lab 05: Contrast memory-oriented and compute-oriented work](reference/labs/05_roofline_microbench.md)

## 5. Reason about SIMT divergence and independent work

**What it is** Single instruction, multiple threads (SIMT) is CUDA's execution model: a warp issues an instruction for the threads participating in it. A branch chooses a path, such as the body of an if statement. Warp divergence occurs when threads in that warp choose different paths; the required paths run with different participating threads. An active mask records which lanes participate in an instruction. Predication similarly prevents selected lanes from committing an instruction's effect, while reconvergence brings paths back together.

For example, if some lanes need a long calculation and others need a short one, finishing the short work does not make those lanes perform the long work for their neighbors. Divergence concerns cooperation within a warp. Unequal work between blocks and a partly filled final grid wave are different problems, even when all three leave some capacity unused.

**Objective** Explain how one warp handles branches and why imbalanced lanes waste issue opportunities.

**Prerequisite bridge** A block becomes warps of 32 lanes. This lesson focuses on what happens when those lanes do not request the same instruction, while later tail analysis focuses on different blocks or ranks finishing at different times.

**Why it matters** Divergence is commonly blamed for any uneven timeline. That diagnosis produces ineffective fixes when the real cause is block-duration skew, insufficient grid waves, memory dependencies, or rank imbalance.

**Mechanism** SIMT presents independent thread state but issues a warp instruction over an active-lane mask. Uniform branches keep all relevant lanes active. Predication may execute a short conditional instruction while disabling lanes that should not commit a result. Longer divergent paths execute separately under complementary masks and reconverge at a compiler-defined point. Independent thread scheduling improves flexibility around synchronization, but it does not make divergent lane paths free or remove the need for correct warp-level synchronization.

**Recall** Do 32 lanes in one warp have independent instruction streams?

**Mental model** SIMT executes one instruction over active lanes. Divergent branch paths are executed with different lane masks, and the warp reconverges after the paths complete.

**Practice labs**

- [Lab 11: Separate lane utilization from grid-tail behavior](reference/labs/11_scheduler_tail.md)

## 6. Use occupancy to hide latency rather than chase a maximum

**What it is** Occupancy is the fraction of an SM's maximum supported warps that are resident at a time. Resident means their execution state has resources assigned; it does not mean they issue an instruction every cycle. An eligible warp is ready to issue its next instruction. Latency hiding means executing other eligible work while one warp waits, for example for a memory load to complete. More resident warps can provide more choices, but cannot remove a dependency within one calculation.

Instruction-level parallelism (ILP) means independent instructions within a thread can make progress without waiting for one another's results. For example, updating two independent accumulators (variables holding separate running results) offers more scheduling freedom than repeatedly updating one accumulator. This supplies ready work within a warp, whereas scheduling another resident warp supplies ready work from other threads. Keeping more independent values live can require more registers.

Registers and shared memory limit how many blocks fit together. Register pressure is the demand for register storage; allocation granularity means resources are assigned in fixed-size units rather than arbitrary fractions. Spilling stores excess thread-private values in device-backed local memory. Occupancy is therefore a resource and scheduling measure, not the percentage of peak arithmetic achieved or a guarantee that increasing it makes the program faster.

**Objective** Relate registers, shared memory, block size, active warps, and latency hiding.

**Prerequisite bridge** The hierarchy, memory and SIMT lessons identified SMs, registers, shared storage and active lanes. Occupancy now asks how those finite resources limit resident blocks and warps, and whether that residency is enough to hide waiting.

**Why it matters** A kernel needs enough independent ready work to cover instruction and memory latency. Chasing the maximum occupancy percentage can instead force spills, shrink useful tiles, or increase synchronization.

**Mechanism** Residency is bounded simultaneously by threads, warps, blocks, registers, shared memory, and architecture limits. The tightest limit determines blocks per SM. Resident warps may be eligible, selected, or stalled on dependencies; “active” therefore does not mean “doing useful work this cycle.” Register allocation is per thread but consumes a finite SM register file in allocation units. Shared memory is per block. Reducing either may allow another block, but forced register caps can turn values into local-memory loads and stores backed by device memory.
An output sentinel is a deliberately recognizable initial value that reveals an output location the kernel failed to write. NaN means “not a number”; it represents an undefined floating-point value and is not a finite result. In Lab 11, fill the output with NaN outside the timed interval, run and synchronize the kernel, then require every expected output to be finite and to match an independently calculated reference. A surviving sentinel detects incomplete writes; the reference detects incorrect written values. Infinity is also non-finite, so neither NaN nor infinity may count as a successful numerical result.

**Recall** Why can a warp that waits on memory allow another warp to run?

**Mental model** An SM keeps several warps resident and switches among ready warps. Registers, shared memory, threads, and block slots limit residency.

**Practice labs**

- [Lab 09: Sweep logical work per Triton program](reference/labs/09_triton_launch_geometry.md)
- [Lab 11: Separate lane utilization from grid-tail behavior](reference/labs/11_scheduler_tail.md)

## 7. Make global memory accesses coalesced

**What it is** Coalescing is the memory system's combination of addresses requested by active lanes of a warp into as few transactions as the access pattern allows. A transaction transfers a chunk of memory, so fetching scattered values can move more data than the useful values alone require. A stride tells how far the storage address advances when a logical index increases. Alignment means starting at a useful address boundary; a sector is a fixed-size portion used in memory-traffic accounting.

A tensor view changes how indices refer to existing storage without copying its values. A copy creates new storage, possibly with a different layout. A contiguous tensor has a compact logical layout, but good coalescing still depends on which indices adjacent lanes actually access. This lesson connects those lane addresses to traffic, rather than treating contiguous storage as a universal performance switch.

**Objective** Connect lane addresses to memory transactions and useful bandwidth.

**Prerequisite bridge** The memory hierarchy explains where data can live. Coalescing explains how one warp’s lane addresses are combined when those data must cross the global-memory interface.

**Why it matters** A kernel can request the same logical number of elements while causing very different physical traffic. Useful bandwidth counts bytes the algorithm needs; transaction traffic includes over-fetch and replay caused by the access pattern.

**Mechanism** For a 32-lane warp reading 4-byte values, lane `i` reading base plus `4*i` covers one aligned 128-byte span, represented by the hardware as aligned sectors and transactions. A large stride spreads lanes across many spans. Misalignment can touch an extra sector. A PyTorch view changes metadata without copying storage, so its strides may expose an awkward lane mapping to the next kernel. Calling `contiguous()` materializes a copy; packing once is profitable only when later reuse saves more time than that copy costs.

**Recall** What address pattern occurs when lane `i` accesses element `i`?

**Mental model** Memory requests from a warp are combined into transactions. Adjacent, aligned lane addresses usually use fewer transactions than large strides or scattered access.

**Practice labs**

- [Lab 04: Evaluate strided access and the cost of repacking](reference/labs/04_layout_and_coalescing.md)

## 8. Time transfers, streams, and synchronization correctly

**What it is** A CUDA stream is an ordered sequence of device work: operations in the same stream obey that order, while independent streams may overlap if resources permit. An event marks progress in a stream. The CPU can wait for an event, or another stream can wait for it to enforce a device-side dependency without blocking the CPU. Nonblocking submission means a host call can return before the requested operation finishes; it does not mean the operation is already complete.

Host-to-device (H2D) and device-to-host (D2H) transfers move data between CPU and GPU memory. Pinned host memory has pages held resident so transfers can use supported direct-memory-access paths; ordinary pageable memory can require staging. Copy engines perform transfers separately from arithmetic execution where supported. Double buffering uses two buffers so a producer can fill the next one while a consumer processes the current one, with events protecting safe reuse.

**Objective** Measure asynchronous work and distinguish overlap from reordered timestamps.

**Prerequisite bridge** Lesson 1 separated submission from completion. Streams and events provide the ordering tools needed to make that distinction correct in programs with copies and multiple device operations.

**Why it matters** Apparent overlap can be a timestamp illusion, and accidental synchronization can erase real overlap. Incorrect stream dependencies can also expose partially produced tensors or surface an earlier asynchronous error at an unrelated later call.

**Mechanism** Operations in one CUDA stream are ordered. Different streams may overlap only when dependencies and hardware resources allow it. A consumer in another stream must wait on an event recorded after the producer, and tensors plus staging buffers must remain alive until their last asynchronous user completes. Host-to-device overlap requires pinned host memory, a nonblocking copy, a separate copy engine path, independent compute, and no hidden synchronization. A double buffer has fill, steady-state, and drain phases; reuse of a buffer must wait for both copy and compute users.

**Recall** Does a normal GPU launch block the CPU until the kernel completes?

**Mental model** CUDA streams order work within a stream while permitting independent work across streams. True copy/compute overlap also requires compatible hardware paths, pinned host memory, independent copy and compute work, and explicit producer–consumer dependencies.

**Practice labs**

- [Lab 03: Measure pageable and pinned host transfers](reference/labs/03_transfer_and_pinning.md)
- [Lab 07: Separate submission time from device completion](reference/labs/07_async_streams.md)

## 9. Choose precision and Tensor Core paths deliberately

**What it is** Floating-point formats represent numbers using a sign, an exponent and a fraction. The exponent controls range—how large or small a value can be—while the fraction controls precision, or the spacing between representable values. FP32, FP16 and FP8 use 32, 16 and 8 bits; BF16 means brain floating point 16 and distributes its bits differently from FP16. Rounding selects a representable approximation. Overflow exceeds the available range; underflow makes very small values lose precision or become zero.

Accumulation is the running combination of partial arithmetic results, such as sums in a dot product, and can use a wider format than the inputs. Tensor Cores accelerate supported matrix operations; TF32 is an arithmetic mode for FP32 inputs, not a separate tensor storage dtype. Scaling changes magnitudes to fit a chosen representation. A format decision must consider both the stored values and the arithmetic actually performed.

**Objective** Relate dtype, shape, Tensor Core eligibility, throughput, memory, and numerical tolerance.

**Prerequisite bridge** Arithmetic pipelines consume values fetched through the memory system. Precision changes both the representation of those values and which library or Tensor Core instruction can implement an operation.

**Why it matters** Lower precision can reduce bytes and accelerate matrix operations, but a dtype annotation does not guarantee Tensor Core dispatch or acceptable training/inference behavior. Storage, input, multiply, accumulation, and output precision can differ.

**Mechanism** FP32 provides broad range and precision. TF32 is an NVIDIA Tensor Core compute mode for selected FP32 matrix operations, not a storage dtype. FP16 reduces range and often needs scaling; BF16 retains the FP32-sized exponent with fewer fraction bits. FP8 requires a scaling recipe and higher-precision accumulation or surrounding state. Matrix dimensions, alignment, library heuristics, framework policy, and operation type determine dispatch. Pointwise work may remain on ordinary CUDA cores even inside mixed precision.

An encoding determines which numbers can be represented. FP formats allocate bits to sign, exponent and fraction; INT8/INT4 quantization instead represents values through integer codes and scale/zero-point conventions. Four-bit weights are not automatically FP4 training. Smaller formats trade range or spacing between values for fewer bytes. A scale maps values into the available range; large outliers can make small values hard to preserve. In integer quantization, a zero point is the integer code that represents real zero. Per-tensor scaling shares one scale across the whole tensor; per-channel scaling assigns scales along a selected feature dimension; block scaling assigns a scale to each bounded group of values. Sharing one scale is simple, while smaller groups can improve local fit at the cost of metadata and extra operations. Keep sensitive reductions and accumulations in suitable higher precision, and measure the complete conversion-plus-compute path.
A matmul precision policy chooses which internal arithmetic a framework may use for FP32 matrix products. `torch.set_float32_matmul_precision("highest")` requests FP32 internal computation; `"high"` permits supported faster reduced-precision internal algorithms. It does not convert the stored tensors to BF16 or guarantee one kernel. Set the policy before the operation, hold shapes and inputs fixed, and inspect dispatch separately from measuring time. Explicit BF16 or FP16 inputs are different cases because their values have already been rounded to those storage formats.

The L2 norm is the square root of the sum of squared elements. Relative L2 error divides the norm of candidate-minus-reference by the reference norm, measuring aggregate discrepancy rather than the worst element. Reference `[3,4]` and candidate `[3,4.1]` give `0.1/5 = 0.02`. Choose the reference and near-zero denominator rule before testing; Lab 02 uses a random reference expected to have a nonzero norm. A zero-reference extension needs a declared absolute-error or guarded-denominator policy, as explained in its guide. Compare each candidate with the appropriate reference, keep finite checks, and report both timing and error. An aggregate threshold does not establish task quality or bound every individual element.

**Recall** Why can lower precision reduce both bytes moved and compute time?

**Mental model** H100 Tensor Cores accelerate supported matrix operations and dtypes, but dispatch depends on shapes, alignment, library selection, and framework policy. Accumulation precision and scaling still determine numerical behavior.

**Practice labs**

- [Lab 02: Compare matrix precision, error, and throughput](reference/labs/02_tensor_core_precision.md)

## 10. Classify workloads with arithmetic intensity and roofline

**What it is** The roofline model relates a workload's arithmetic rate to how much data it must move. A FLOP is one floating-point operation; FLOP/s measures operations completed per second. Bandwidth measures bytes transferred per second. Arithmetic intensity is operations per byte at a stated memory boundary, such as HBM. A roofline plot puts intensity on the horizontal axis and achieved arithmetic rate on the vertical axis. Its sloped bandwidth ceiling and horizontal compute ceiling meet at the ridge point.

A low-intensity calculation may have to wait for bytes even when arithmetic units are available; a high-intensity one can reuse enough data to approach a compute limit. The ceilings describe upper bounds under declared assumptions, not expected measurements. The chosen precision, operation type and byte-count boundary must match the plotted workload before the model can explain a result.

**Objective** Decide whether another byte or another operation is the more valuable optimization target.

**Prerequisite bridge** The previous lessons supplied a byte ledger and compute-path model. Roofline combines them into a bound that helps choose the next experiment without pretending to predict every kernel detail.

**Why it matters** Optimizing arithmetic in a bandwidth-limited kernel or compressing bytes in a compute-limited kernel may not move elapsed time. A roofline position turns a vague utilization observation into a falsifiable resource hypothesis.

**Mechanism** Arithmetic intensity is useful operations divided by bytes crossing the chosen boundary. The sloped roof is attainable bandwidth multiplied by intensity; the flat roof is attainable compute rate. Their intersection is the ridge point. Count bytes at one declared boundary—often HBM—and include repeated reads, writes, and intermediates. Use measured sustainable roofs when possible, because marketing peaks are upper bounds. A point far below both roofs may indicate dependencies, launch gaps, imbalance, poor instruction mix, or insufficient parallelism rather than a pure bandwidth/compute limit.

**Recall** How is arithmetic intensity computed?

**Mental model** Arithmetic intensity is operations divided by bytes transferred. Roofline compares that intensity with attainable memory bandwidth and compute throughput to bound performance.

**Practice labs**

- [Lab 05: Contrast memory-oriented and compute-oriented work](reference/labs/05_roofline_microbench.md)

## 11. Read sharing and health state without changing it

**What it is** GPU sharing determines which applications can use physical resources at the same time. Multi-Instance GPU (MIG) partitions supported hardware into isolated GPU instances. Multi-Process Service (MPS) coordinates work from multiple CUDA processes so compatible work can share execution resources. Time-slicing alternates access over time. A CUDA context holds a process's device-execution state; it is not itself a hardware partition. These sharing modes differ from an application merely launching several streams.

Health monitoring reports whether the device is operating normally. Error-correcting code (ECC) detects or corrects certain memory errors; Xid messages are driver-reported diagnostic events. Clock frequency is the rate at which hardware operates, and throttling reduces it in response to limits such as power or temperature. NVIDIA Data Center GPU Manager (DCGM) gathers operational metrics and health checks. A counter's scope identifies which device, instance or time interval it describes; an observation alone does not prove exclusive access.

**Objective** Distinguish full GPU, MIG, MPS, and time-slicing and collect non-intervening health evidence.

**Prerequisite bridge** The single-GPU lessons explain useful work and timing, while the initial preflight checks the allocated device before every run. Now interpret sharing and operational signals in more depth, before adding two-node communication. These observations never authorize changing the cluster.

**Why it matters** Sharing or clock/thermal events can change variance and throughput enough to invalidate a benchmark. They are context, not automatic root causes, and observing them must not silently reconfigure the node.

**Mechanism** Full-GPU allocation gives one workload the scheduler-visible device. MIG partitions supported compute and memory resources into isolated GPU instances. MPS allows work from multiple CUDA processes to execute concurrently. On H100, clients retain separate CUDA contexts and GPU address spaces while sharing GPU scheduling resources; this is not MIG-style hardware partitioning. Time-slicing gives workloads alternating access without MIG’s hardware partitioning. ECC counts, retired pages, Xid events, clocks, power, temperature, and link state form an operational timeline. Passive DCGM health watches interpret retained fields; active diagnostics are a different, potentially intervening workflow. ECC is error-correcting code: distinguish corrected errors from uncorrectable errors and record counter scope and changes, not just totals. Xid is an NVIDIA driver error classification, not a complete diagnosis. Retired-page or row-remapping state records memory-reliability handling; newer GPUs including H100 expose row-remapping information, so interpret only supported fields. Power, thermal limits, and clocks provide operating context, not automatic proof of the cause. These are read-only observations; escalate concerning changes to the administrator.

Telemetry terms answer different questions. Sampled GPU activity reports whether GPU work was active during a sampling window; it is not a percentage of peak arithmetic achieved. Memory-controller activity concerns intervals serving memory traffic, not how much HBM is allocated. Allocated bytes describe occupied capacity, not transfer rate. A one-second sample can hide alternating short busy and idle bursts. Correlate timestamps and use a timeline for launch gaps; do not infer saturation or a bottleneck from one utilization number. DCGM profiling counters add more specific observations, but collection ownership and competing profiler access must be coordinated without changing monitoring configuration in these labs.

**Recall** Which sharing mode partitions hardware resources into isolated GPU instances?

**Mental model** MIG creates hardware-isolated instances, MPS coordinates CUDA processes, and scheduler time-slicing shares execution over time. ECC, Xid, retired-page, power, clock, thermal, and link signals provide system context but do not by themselves prove an application cause.

**Practice labs**

- [Lab 12: Read GPU health and sharing signals safely](reference/labs/12_read_only_health.md)

## 12. Understand GPU networking, topology, and collectives

**What it is** A node is a machine in the cluster; a rank identifies one participating process; topology describes how machines, GPUs and network interfaces connect. A collective is a communication operation performed by a group of ranks. NVIDIA Collective Communications Library (NCCL) supplies GPU-oriented collectives: all-reduce combines values and returns the combined result to every rank, all-gather assembles each rank's piece, and broadcast sends one rank's values to the group. A shard is one piece of a larger tensor; the payload is the data being communicated.

GPU networking lets separate devices exchange the values needed to cooperate on one problem. The names in this lesson describe different layers, not competing products that all do the same job. NVLink is a GPU interconnect; NVSwitch connects NVLink endpoints; InfiniBand and Ethernet are network fabrics; RoCE carries RDMA over Ethernet; GPUDirect RDMA makes supported GPU memory accessible to a network adapter; NCCL is communication software that uses available paths. A fabric is the connected collection of links and switches carrying traffic between endpoints.

**Objective** Explain the networking layers, trace GPU-to-GPU data movement, and select meaningful collective measurements for two one-GPU nodes.

**Prerequisite bridge** Single-GPU execution, timing, roofline and read-only health checks now provide a local baseline. Two-node work adds process placement and network collectives; verify both allocated devices before interpreting their shared critical path.

**Why it matters** A fast local kernel cannot improve a step dominated by synchronization or network transfer. Two ranks can teach collective mechanics and placement, but they cannot establish dense-node or large-cluster scaling.

**Mechanism**

### First locate the devices and their connections

A network interface controller (NIC) connects a machine to a network. In InfiniBand documentation, an endpoint adapter is also called a host channel adapter (HCA); NVIDIA ConnectX adapters provide supported networking capabilities. PCI Express (PCIe) connects devices through switches and host root complexes. NUMA means that the access cost to host memory depends on the CPU/socket attached to that memory. A nearby GPU and NIC may have a different path from a pair whose traffic crosses CPU sockets. Placement therefore matters even when the endpoint GPUs have the same model name.

NVLink carries communication between supported GPU endpoints at high bandwidth. NVSwitch is switching hardware for NVLink: it gives several GPUs paths to one another instead of requiring every pair to have a dedicated direct link. A supported multi-GPU DGX H100 system has this scale-up connectivity as well as network adapters for communication beyond the system. NVSwitch is not an InfiniBand switch, and NVLink is not a setting that creates a connection between arbitrary servers. Special NVLink Switch System deployments can extend supported NVLink domains; their dedicated hardware is not part of this course's two-node target.

### Then identify the inter-node fabric

InfiniBand is a switched networking architecture with its own link and transport mechanisms. Its subnet manager discovers and configures the fabric's forwarding paths; that service belongs to the cluster operator. NVIDIA Quantum switches and compatible ConnectX adapters are examples of components in an NVIDIA InfiniBand deployment. An H100 GPU alone does not supply an InfiniBand fabric.

RoCE means RDMA over Converged Ethernet. It brings RDMA communication to compatible Ethernet adapters and networks. RoCEv2 uses UDP/IP encapsulation and can traverse a routed IP fabric. It is not ordinary application UDP socket I/O: the RDMA adapter executes the transfer protocol. Ethernet traffic does not automatically become RoCE traffic, and a TCP connection over an InfiniBand IP interface is not proof that an application used RDMA. NVIDIA Spectrum Ethernet and Quantum InfiniBand switches serve different fabric roles.

Congestion is competition for a link or queue whose capacity is temporarily insufficient. On RoCE networks, operators may use ECN (explicit congestion notification) to mark congestion, CNPs (congestion notification packets) to notify senders, and PFC (priority flow control) to pause selected traffic classes. These mechanisms require a coordinated design; indiscriminate pausing or mismatched configuration can hurt latency. Learn what the counters mean, but do not change NIC or switch policies as a course exercise.

### Understand RDMA before adding GPU memory

Remote direct memory access (RDMA) allows a network adapter to transfer data into or out of registered memory on a remote machine without the receiving CPU copying each payload through the ordinary socket path. Registration establishes which memory the adapter may access and how. A queue pair (QP) holds send and receive work queues; a completion reports that submitted work has reached its defined completion boundary. CPUs and drivers still create connections, register buffers and coordinate work. RDMA does not mean that the application has no CPU activity or synchronization requirements.

RDMA can operate on host memory. GPUDirect RDMA adds a supported direct path between a network adapter and GPU memory, avoiding a host-memory staging copy for that transfer. Conceptually, the host-staged path is GPU memory → host buffer → NIC → network → NIC → host buffer → remote GPU memory. A qualified direct path is GPU memory → NIC → network → NIC → remote GPU memory. PCIe attachment and registration remain involved; the NIC is not magically connected to GPU HBM without an interconnect. GPUDirect peer-to-peer concerns local device access, while GPUDirect Storage concerns supported storage I/O. Those names do not prove that a particular network transfer is GPU-direct.

### Finally place NCCL above those paths

NCCL supplies topology-aware communication operations; frameworks such as PyTorch invoke it instead of implementing the network protocol themselves. It can use supported local GPU links, PCIe, RDMA network transports or IP sockets. The physical path and collective algorithm are separate choices. A ring organizes communication in neighbor-to-neighbor steps, whereas a tree organizes hierarchical exchanges. Neither word identifies the installed fabric. Even an RDMA data path may use IP connectivity to exchange setup information.

All-reduce combines values and returns the result to every rank; reduce-scatter combines and shards; all-gather reconstructs shards; all-to-all exchanges distinct partitions. For example, rank 0 holds [1, 2] and rank 1 holds [3, 4]. Sum all-reduce gives both [4, 6]; it does not concatenate them into [1, 2, 3, 4]. Every participant must agree on the operation, datatype, count and collective ordering. A mismatch can hang or fail rather than produce a useful benchmark.

Small messages emphasize fixed latency and launch/synchronization overhead, while large messages emphasize sustained transfer. Algorithmic bandwidth divides the logical payload by elapsed time. NCCL Tests' bus bandwidth applies a collective-specific normalization; it is not a direct measurement of physical NIC traffic. Strong scaling holds total work fixed as ranks increase; weak scaling grows total work with rank count. Both require the slowest-rank step time and identical correctness semantics.
Slurm is the cluster workload manager: a job requests resources, and a job step starts processes inside that allocation. A launcher assigns each process a rank, a local device and the information needed to find its peers. PyTorch's `torchrun` supplies such process coordination; a process group is the set of ranks participating in its collectives. Use the supplied launcher to establish the group, run the known-value preflight, and release the group on exit. All ranks must call matching collectives in the same order. A barrier waits for the group to arrive, while an all-reduce combines payload values; they serve different purposes. Report the maximum elapsed rank time because the group result is not ready while one required rank is unfinished.

Capability, transport selection and memory registration are distinct claims. An active RDMA port establishes capability; a runtime trace can identify the transport selected by NCCL. Direct GPU-memory registration is an additional requirement, provided only through a supported platform integration such as DMA-BUF or a peer-memory driver path. A selected RDMA transport alone does not prove that payloads avoided host staging.

**Recall** Which collective gives every rank the reduction result?

**Mental model** Collectives move and combine data across ranks. With one GPU per node, the relevant path includes GPU, host interconnect, network, and the remote node; it does not demonstrate intra-node NVLink or NVSwitch scaling. GPUDirect RDMA and GPUDirect Storage can remove selected CPU-staging paths only when the NIC, storage, driver, topology, and software stack support them.

**Practice labs**

- [Lab 00: Verify the two-node H100 platform](reference/labs/00_cluster_preflight.md)
- [Lab 06: Measure a two-node NCCL all-reduce](reference/labs/06_distributed_collectives.md)
