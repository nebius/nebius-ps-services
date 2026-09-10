# GPU Fundamentals for NVIDIA H100 Performance Engineering

This course builds the hardware and system model needed to explain GPU behavior before changing code. Every conclusion must connect workload shape, H100 resources, and measured evidence.

## 1. CPU–GPU cooperation

**Objective**

Decide which work belongs on the CPU, which belongs on the GPU, and where launch or transfer overhead dominates.

**How it works**

### What this course is about

A CPU (central processing unit) runs the operating system and the ordinary instructions of your application. A GPU (graphics processing unit) is a processor designed to make progress on many similar pieces of work at once. This course explains how the two cooperate, how an NVIDIA H100 stores and executes work, and how to tell whether a workload is using it effectively. An array is an ordered collection of numbers; a tensor is a multidimensional array, such as a matrix of image pixels or model weights. You do not need prior CUDA knowledge to begin. Familiarity with a small Python program is enough for the explanations; the practical exercises introduce the relevant PyTorch operations.

### Why use a GPU rather than a CPU

CUDA is NVIDIA's platform for programming its GPUs. PyTorch is a numerical-computing and machine-learning framework that expresses operations on tensors and can use CUDA to execute supported work on NVIDIA devices. A tensor's shape gives its dimensions, while its dtype specifies how each value is represented, such as 32-bit floating point. Batching means processing several items together; synchronization means waiting for required work to finish before using its result. These names describe different parts of execution, not interchangeable ways of saying that a GPU is busy.

Consider applying the same formula to one million independent values. A CPU can perform this correctly, using optimized vector instructions and several cores. A GPU can schedule far more concurrent arithmetic work and has high-bandwidth device memory, so sufficiently large parallel workloads may finish sooner. It does not calculate an entire large matrix in one instant: work is divided into many pieces and takes many execution cycles. GPUs are useful for matrix algebra, image processing, scientific simulation and neural networks because these problems often expose substantial parallelism and data reuse.

The CPU remains a good choice for small jobs, branch-heavy control logic, irregular pointer chasing, file handling and tasks whose data would cost more to move than to process. A CPU-only implementation does not become incorrect merely because the GPU is available; it may simply take longer for a suitable large workload, or it may win for a small one. Both latency (time for one result) and throughput (completed work per second) matter. The right device is an evidence-based choice, not a property of the programming language.

### How the CPU, memory and GPU cooperate

The CPU is the host and the GPU is the device. Host RAM and the H100's high-bandwidth memory, or HBM, are distinct storage resources in the discrete-device model used here. A kernel is a function executed by GPU threads. A launch submits that work; it is not the same thing as copying its input data. A stream is an ordered queue of device work. PyTorch uses the tensor's device and the available implementation to dispatch an operation: CPU tensors use a CPU path; CUDA tensors use a GPU path when supported. Moving a tensor with `to("cuda")` transfers its data; it does not move the Python interpreter onto the GPU.

### GPU architecture: based on H100 GPU

Start with three resources: **SMs compute, L2 caches, and HBM stores the large data set**. An SM (streaming multiprocessor) runs groups of GPU threads. L2 is an on-chip cache shared across SMs; it retains data to reduce requests to HBM. HBM (high-bandwidth memory) holds inputs, outputs and other large allocations. The first diagram shows just these three resources. It is a simplified data-access map, not a silicon floorplan or a sequence that every access must traverse.

Use the **H100 SXM 80 GB** as a concrete example. Its enabled resources differ from the H100 PCIe 80 GB, which has 114 SMs, and from the full GH100 die design, which has 144. The numbers below describe the SXM product, not every H100 allocation.

| Physical resource | H100 SXM 80 GB |
| --- | --- |
| GPCs | 8 |
| TPCs | 66 enabled in total; 2 SMs per TPC |
| SMs | 132 |
| SM subpartitions | 4 per SM; 528 in total |
| 32-bit floating point (FP32) CUDA cores | 128 per SM; 16,896 in total |
| Fourth-generation Tensor Cores | 4 per SM; 528 in total |
| L2 cache | 50 MB |
| HBM | 80 GB HBM3 |

### Hardware hierarchy: GPC → TPC → SM → SMSP

Each arrow here means **contains**. GPC stands for **graphics processing cluster**; NVIDIA's Hopper architecture description also calls it a GPU processing cluster. TPC means **texture processing cluster**. These historical names describe groups of hardware even when the GPU is doing numerical computation. A GPC contains TPCs, each TPC contains two SMs, and each H100 SM contains four **SM subpartitions (SMSPs)**. The 66 enabled TPCs are a device total; dividing by eight does not describe an identical layout in every GPC.

Each SMSP has a warp scheduler, registers and execution units. A scheduler chooses ready work; registers hold working values. FP32 CUDA cores perform ordinary single-precision arithmetic, while Tensor Cores perform supported matrix operations. They are physical execution units, not software threads. Level-one (L1) caching and explicitly managed shared memory share a resource within each SM. Shared memory is temporary storage for cooperating threads, not an obligatory stage between L2 and registers. The second diagram enlarges one SM to show these details.

### Work hierarchy: grid → blocks → warps → threads

A **thread** is one execution of the kernel with its own index and working values. A **block** contains threads that can cooperate, and a **grid** contains all blocks in one kernel launch. Within each block, hardware groups consecutive threads into **warps of 32**. Thus the programming hierarchy is grid → blocks → threads; including execution grouping gives grid → blocks → warps → threads. A thread does not contain warps. A partial final warp has unused lanes, and a warp never combines threads from different blocks.

| Logical work or residency limit | H100, compute capability 9.0 |
| --- | --- |
| Grid | One per kernel launch; block count is chosen for the workload |
| Threads per block | Up to 1,024, equivalent to 32 full warps |
| Threads per warp | 32 |
| Resident warps per SM | Up to 64, equivalent to 2,048 resident threads |
| Resident blocks per SM | Up to 32; at most 2 with 1,024 threads per block. Other resource limits can reduce these counts |
| Resident work across 132 SMs | At most 8,448 warps or 270,336 threads |

Here, a block is **resident on an SM** when the GPU has placed it on that SM and reserved register space for its threads, plus any shared memory the block needs. The SM keeps their register values and tracks where they are in the kernel.

For example, one resident warp may be waiting for a memory read while a scheduler lets another ready warp execute. Both remain resident. Therefore, **2,048 resident threads** means the SM can keep track of that many threads at once; their warps share the execution units over time rather than all executing an instruction in the same clock cycle. The thread, warp and block limits all apply together.

A block's size is chosen by the program; it does not always contain 1,024 threads. With 1,024 threads per block, the 2,048-thread capacity allows at most 2 blocks per SM: 2,048 / 1,024 = 2. Each block uses 32 warps, so two blocks also reach the 64-warp limit. Smaller blocks can allow more blocks to reside together, up to the separate 32-block ceiling.

| Threads per block | Upper bound on resident blocks per SM |
| --- | --- |
| 1,024 | 2 |
| 512 | 4 |
| 256 | 8 |
| 128 | 16 |
| 64 | 32 |
| 32 | 32; the block-count limit applies |

These bounds apply only the thread, warp and block limits. Register and shared-memory requirements can reduce how many blocks fit. For these block sizes, all of which are multiples of 32, calculate the smaller of 32 and 2,048 divided by the threads per block. At 32 threads per block, 32 resident blocks use only 1,024 threads: reaching the block limit does not necessarily fill the thread capacity. Smaller blocks therefore do not automatically keep more warps resident or improve performance.

The grid can be much larger than the resident work: pending blocks wait for resources, so a GPU has no fixed lifetime total of grids, blocks or threads.

For example, one kernel processing 1,048,576 values with one thread per value and 256 threads per block launches **one grid of 4,096 blocks**. Each block has 8 warps, giving 32,768 warps over the whole launch. Even if no other resource limits apply, the 2,048-thread limit permits only 8 such blocks per SM, or 1,056 across this GPU at once. The remaining blocks run as resources become available. This arithmetic describes work coverage and an upper bound, not a measured schedule.

The overview and enlargement below connect the two hierarchies: blocks are placed on SMs, their warps use SMSP execution resources, and the SMs access data through the memory system. Lesson 3 develops scheduling in more detail; Hopper's optional thread-block clusters are introduced later as an additional grouping, not a requirement for an ordinary launch.

### Execution and dependencies

Start with ordinary program latency: a result is ready only after every required stage finishes. GPU execution adds explicit submission, transfer, device-execution, and synchronization stages to that familiar critical path.

Follow one calculation from the application to its result. The CPU prepares the input and asks CUDA to run a kernel. CUDA submits the request to a stream, which keeps device operations in order. The GPU executes the kernel when its dependencies and resources allow. The CPU can continue before that execution finishes: returning from the launch means the work was submitted, not that the answer is ready.

Where the data starts and where the answer is needed determine how much work this request involves. If the input is in host RAM, it must be copied to GPU memory before the kernel uses it. If the CPU needs the output, that output must be copied back after the kernel finishes. If the next operation also runs on the GPU, the output can stay there and become its input.

This gives three useful timing comparisons:

- **CPU time:** start before the CPU calculation and stop when its result is ready.
- **Resident GPU time:** the inputs are already in GPU memory, and the result stays there. Measure the required device work through its completion. This isolates the computation from host-device transfer costs.
- **Transfer-inclusive GPU time:** start with inputs on the CPU and stop when the CPU can use the returned result. Include the input copy, launch, device work, output copy and any required waiting.

A timer stopped immediately after submission misses unfinished GPU work. A completed-work measurement waits for the result required by its chosen comparison. Lesson 8 explains how streams, events and synchronization establish that stopping point.

Batching sends more useful work with each launch, so the launch cost is shared across more items. It can improve throughput, but collecting a batch may make an individual request wait longer and requires storage for more inputs and temporary results. Keeping data on the GPU across several operations can avoid paying the transfer cost each time.

“The GPU is faster” is not an engineering rule. A GPU wins when enough independent work amortizes fixed overhead and when data can stay resident long enough to avoid repeated host-device movement. Interactive latency and batch throughput can therefore prefer different placements.

### Try a small example

Suppose a CPU operation takes 2 milliseconds. A resident GPU operation takes 0.3 milliseconds, but uploading inputs and obtaining the result add 3 milliseconds. For this hypothetical one-shot request the GPU path takes 3.3 milliseconds, so the CPU wins. If ten dependent operations keep intermediate data on the GPU, the same total transfer cost plus ten 0.3-millisecond operations is 6 milliseconds, compared with ten 2-millisecond CPU operations. These are teaching assumptions, not H100 measurements.

**Practice labs**

- [Lab 01: Find the CPU–GPU crossover for vector work](reference/labs/01_cpu_gpu_crossover.md)
- [Lab 10: Identify the software layer behind GPU execution](reference/labs/10_compatibility_stack.md)

**Mental model**

The CPU schedules and prepares work while the GPU executes wide batches of similar operations. Transfers and kernel launches are explicit boundaries, so small jobs can spend more time crossing boundaries than computing.

## 2. GPU execution software layers

**Objective**

Explain which layer owns compilation, loading, compatibility, and execution.

**How it works**

The GPU software stack is the set of layers that turns an application into work the device can execute. A driver lets the operating system and programs communicate with the GPU. The CUDA runtime provides callable services—an application programming interface (API)—for allocating memory, submitting work and checking completion. The CUDA Toolkit supplies development tools, including the compiler that translates source code. PyTorch sits above these layers and selects implementations for tensor operations. A Python wheel is an installable package; it may bundle runtime libraries without including the full toolkit.

Parallel Thread Execution (PTX) is an intermediate GPU instruction language; SASS is target-specific machine code. A cubin contains compiled device code, while a fat binary can carry code for multiple targets. Just-in-time (JIT) compilation translates code when needed at load or execution time. Compute capability names a GPU's hardware feature level, not its driver or toolkit version. These separate meanings explain why matching one version number does not establish compatibility.

Lesson 1 identified a software boundary between submitted work and device execution. This lesson names every layer at that boundary so a version string or error can be assigned to an owner.

Consider a PyTorch operation on a CUDA tensor. PyTorch first selects an implementation, often from a library shipped with the framework package. That implementation uses CUDA runtime or driver APIs to arrange memory and submit GPU work. The user-mode driver loads the device code, while the kernel-mode NVIDIA driver manages access to the GPU. The device then executes the loaded kernel.

The code may have been compiled long before this call. The CUDA compiler, `nvcc`, translates CUDA C++ into target-specific device code, an intermediate representation called PTX, or a fat binary containing several versions. A cubin contains compiled device code; SASS names the final machine instructions. If the package supplies suitable PTX instead of a ready machine-code version, a compatible driver can compile it just in time for the GPU.

This explains why running and building have different requirements. A prebuilt framework wheel may include the CUDA runtime, libraries and kernels it needs, so running it does not necessarily require a local `nvcc`. Building a CUDA extension does require suitable compiler tools and headers. A toolkit installation cannot replace a missing or insufficient driver, and a new driver cannot add kernels absent from the package.

The target also matters. H100 has compute capability 9.0. SM90 identifies ordinary code for that architecture; SM90a allows architecture-accelerated features with narrower compatibility. A driver must understand the supplied PTX version before it can translate it. These names describe compatibility requirements; they do not demonstrate that an application ran successfully.

Data copies use a related hardware path. With direct memory access (DMA), a transfer engine moves the payload after software arranges the transfer, rather than requiring CPU instructions to copy every byte.

CUDA failures are often “repaired” at the wrong layer. Installing a local toolkit cannot fix an insufficient kernel-mode driver, and a new driver does not make a Python package contain missing CUDA libraries or kernels for the correct architecture.

**Practice labs**

- [Lab 08: Map PyTorch operations to GPU activity](reference/labs/08_operator_to_kernels.md)
- [Lab 10: Identify the software layer behind GPU execution](reference/labs/10_compatibility_stack.md)

**Mental model**

PyTorch calls CUDA libraries and runtime APIs; kernels are distributed as machine code or PTX; the driver loads and executes compatible code. The driver and toolkit are related but not interchangeable version numbers.

## 3. GPU execution architecture

**Objective**

Trace a PyTorch operation down to blocks scheduled on H100 streaming multiprocessors.

**How it works**

A GPU has physical execution resources, while a CUDA program describes logical workers. A thread is one worker executing the kernel; a block is a group of threads that can cooperate; a grid is the collection of blocks in one launch. Threads are organized into warps of 32, and a lane is one thread's position within its warp. A streaming multiprocessor (SM) is a physical unit that hosts blocks and executes their warps. A graphics processing cluster (GPC) contains texture processing clusters (TPCs), each with two SMs on H100; an SM itself contains four SM subpartitions (SMSPs) with scheduling and execution resources. These are different levels, not interchangeable names for GPU quadrants.

Tensor Cores are specialized matrix-arithmetic units within an SM. Different launches need not assign corresponding blocks to the same SM; within an ordinary launch, each block remains on its assigned SM for its lifetime. The later Triton examples use a GPU-programming language and compiler whose program instances describe cooperating work, not a different physical GPU hierarchy.

The software stack loads a kernel, but the kernel still needs a physical execution model. Reuse the grid, block, and warp vocabulary whenever later lessons discuss occupancy, divergence, memory, or Tensor Cores.

A kernel launch specifies a grid of blocks. The GPU assigns a block to an SM that has enough resources for it, and the block stays on that SM until it finishes. Other blocks can run alongside it if resources permit; blocks that do not yet fit wait. The grid describes all the work in the launch, not the amount executing at one instant.

Within the SM, each block's threads are grouped into warps of 32 lanes. Scheduling and execution resources are divided among the SM's four subpartitions, or SMSPs. Schedulers select ready warp instructions, which use the appropriate arithmetic, memory or matrix units. Tensor Cores execute supported matrix instructions issued by warps; they are not a separate place to assign an entire Python operation.

Threads keep working values in registers. Cooperating threads in a block can use shared memory, while global-memory requests use the cache and high-bandwidth memory (HBM) system. H100's configurable L1 (level-one cache)/shared-memory carveout divides a shared on-chip capacity between caching and explicitly managed storage. The GPC/TPC/SM hierarchy describes hardware containment; it is not a mandatory sequence through which each byte or instruction passes.

Triton expresses work using logical blocks of array elements. A program instance handles one such block, and `num_warps` controls how many warps cooperate in it. `BLOCK_SIZE` describes logical elements, not a new hardware level or necessarily one element per thread. The instance index, `tl.program_id(0)`, selects its portion of the input.

For N elements and B elements per program, `ceil(N/B)` instances cover the input. Each forms indices `program_id*B + [0, ..., B-1]` and masks accesses where `index < N` is false. With N=1003 and B=256, four programs describe 1024 positions; the final 21 positions must not load or store data. The mask makes the final partial block safe.

A high-level operator can be slow because it dispatches an unexpected kernel, launches too little parallel work, creates a partial final wave, or uses the wrong instruction path. None of those causes is visible from the Python name alone.

**Practice labs**

- [Lab 08: Map PyTorch operations to GPU activity](reference/labs/08_operator_to_kernels.md)
- [Lab 09: Sweep logical work per Triton program](reference/labs/09_triton_launch_geometry.md)
- [Lab 11: Separate lane utilization from grid-tail behavior](reference/labs/11_scheduler_tail.md)

**Mental model**

A kernel creates a grid of thread blocks. Blocks are assigned to SMs; each block contains warps of 32 threads; warp instructions use scalar, vector, memory, and matrix pipelines according to the instruction mix.

## 4. GPU memory hierarchy

**Objective**

Choose the nearest useful memory level and recognize when data movement dominates.

**How it works**

The memory hierarchy is a set of storage locations with different capacities, access costs and sharing rules. Registers hold a thread's working values. Shared memory is fast on-chip storage explicitly managed by cooperating threads in a block. A cache automatically retains copies of recently accessed data: L1 (level-one cache) is close to an SM and L2 (level-two cache) is shared more broadly across the GPU. Device allocations in the global address space normally live in high-bandwidth memory (HBM). An address space describes which addresses code can refer to, not necessarily a distinct physical memory chip.

An allocation reserves storage for values. Temporal locality means reusing the same values soon; spatial locality means accessing nearby addresses. A spill places values that cannot stay in registers into thread-private local memory, which is backed by device memory despite its name. Understanding ownership and reuse comes before deciding where data should live.

Resident threads need storage for private values, block cooperation, cached reuse, and the full data set. These are distinct address spaces and lifetime contracts, not simply “fast” and “slow” memory.

Trace a value used by a kernel. Its global-memory allocation normally lives in HBM. When a thread loads the value, caches may satisfy the request without another HBM access. The compiler keeps working values in registers where possible. If values spill into the thread's local-memory address space, they use device-backed storage and caching; the word “local” does not mean on-chip register storage.

A block can also load values into shared memory so its threads can reuse them. The program explicitly manages that storage and synchronizes cooperating threads before they consume each other's writes. Caches work differently: they retain data automatically and preserve the program's memory semantics. Shared memory is therefore an optional cooperation mechanism, not a compulsory stop between HBM and registers.

At the framework level, PyTorch's `memory_allocated` reports live tensor storage and `memory_reserved` includes segments held by its allocator. These quantities describe allocation ownership. They do not reveal which values a running kernel keeps in registers or shared memory.

Indexing determines which stored value a load requests. A tensor view changes shape or indexing metadata while sharing storage. For a compact 2×3 matrix, strides `[3,1]` mean that moving one row advances three stored elements and moving one column advances one. A transpose produces a 3×2 view with strides `[1,3]`; the six values have not moved.

When that layout is not contiguous, `contiguous()` can create a compact copy. The copy reads the old storage and writes new storage, so it adds data movement before later operations can benefit. Shape, strides and element size let you derive addresses and distinguish input, output and temporary bytes. Lesson 7 connects those addresses to the memory requests made by a warp.

Performance is often limited by repeated movement rather than arithmetic. Correctly naming where a byte lives reveals whether the next change should improve reuse, remove an intermediate, alter layout, or reduce the workload's memory footprint.

**Practice labs**

- [Lab 04: Evaluate strided access and the cost of repacking](reference/labs/04_layout_and_coalescing.md)
- [Lab 05: Contrast memory-oriented and compute-oriented work](reference/labs/05_roofline_microbench.md)

**Mental model**

Registers are thread-local, shared memory is explicitly managed per block, caches capture reuse, and HBM provides large capacity with much higher latency. Useful reuse should occur before data returns to HBM.

## 5. Parallel control flow

**Objective**

Explain how one warp handles branches and why imbalanced lanes waste issue opportunities.

**How it works**

Single instruction, multiple threads (SIMT) is CUDA's execution model: a warp issues an instruction for the threads participating in it. A branch chooses a path, such as the body of an if statement. Warp divergence occurs when threads in that warp choose different paths; the required paths run with different participating threads. An active mask records which lanes participate in an instruction. Predication similarly prevents selected lanes from committing an instruction's effect, while reconvergence brings paths back together.

For example, if some lanes need a long calculation and others need a short one, finishing the short work does not make those lanes perform the long work for their neighbors. Divergence concerns cooperation within a warp. Unequal work between blocks and a partly filled final grid wave are different problems, even when all three leave some capacity unused.

A block becomes warps of 32 lanes. This lesson focuses on what happens when those lanes do not request the same instruction, while later tail analysis focuses on different blocks or ranks finishing at different times.

Imagine a warp reaching an `if` statement. Each thread evaluates the condition for its own data. If all participating threads choose the same branch, the warp can issue that branch's instructions with all those lanes active.

If the threads choose different branches, each path must still perform its required work. For one path, an active-lane mask selects the lanes that need those instructions; the other lanes do not contribute results. The other path runs with its own mask. Reconvergence brings the participating threads back to a common continuation. Time spent executing one path does not perform useful work for lanes assigned to the other.

For a short conditional, the compiler may use predication: it issues an instruction but prevents selected lanes from committing its effect. This can avoid an explicit branch, yet inactive lanes still do not produce useful results. The exact instruction sequence depends on compilation.

Independent thread scheduling gives the hardware more flexibility in tracking and scheduling threads around divergence and synchronization. It does not turn divergent paths into free parallel work. Code that exchanges values between lanes must still use the required warp-level synchronization; it cannot assume that all lanes always advance together.

Divergence is commonly blamed for any uneven timeline. That diagnosis produces ineffective fixes when the real cause is block-duration skew, insufficient grid waves, memory dependencies, or rank imbalance.

**Practice labs**

- [Lab 11: Separate lane utilization from grid-tail behavior](reference/labs/11_scheduler_tail.md)

**Mental model**

SIMT executes one instruction over active lanes. Divergent branch paths are executed with different lane masks, and the warp reconverges after the paths complete.

## 6. Occupancy and latency hiding

**Objective**

Relate registers, shared memory, block size, active warps, and latency hiding.

**How it works**

Occupancy is the fraction of an SM's maximum supported warps that are resident at a time. Resident means their execution state has resources assigned; it does not mean they issue an instruction every cycle. An eligible warp is ready to issue its next instruction. Latency hiding means executing other eligible work while one warp waits, for example for a memory load to complete. More resident warps can provide more choices, but cannot remove a dependency within one calculation.

Instruction-level parallelism (ILP) means independent instructions within a thread can make progress without waiting for one another's results. For example, updating two independent accumulators (variables holding separate running results) offers more scheduling freedom than repeatedly updating one accumulator. This supplies ready work within a warp, whereas scheduling another resident warp supplies ready work from other threads. Keeping more independent values live can require more registers.

Registers and shared memory limit how many blocks fit together. Register pressure is the demand for register storage; allocation granularity means resources are assigned in fixed-size units rather than arbitrary fractions. Spilling stores excess thread-private values in device-backed local memory. Occupancy is therefore a resource and scheduling measure, not the percentage of peak arithmetic achieved or a guarantee that increasing it makes the program faster.

The hierarchy, memory and single-instruction, multiple-thread (SIMT) lessons identified SMs, registers, shared storage and active lanes. Occupancy now asks how those finite resources limit resident blocks and warps, and whether that residency is enough to hide waiting.

Before a block can become resident, the SM must have room for its threads and warps, a free block slot, enough registers and enough shared memory. All these limits apply together. The resource that runs out first determines how many such blocks can reside on that SM.

Registers are used by individual threads, so a block with many threads can consume substantial register capacity even when each thread uses a modest number. Hardware allocates registers in fixed-size units, which can make a small source change cross a residency threshold. Shared-memory demand is counted per block. Reducing either demand helps residency only if it allows another block to fit within every remaining limit.

Once blocks are resident, the scheduler chooses among their warps. A warp waiting for a load is resident but cannot issue a dependent instruction. Another warp with ready work may issue while it waits. This is latency hiding: the wait still exists, but independent work uses time that would otherwise be idle. A resident warp may be ready, selected to issue, or stalled; residency alone says nothing about useful work in a particular cycle.

Forcing fewer registers can backfire. Values that no longer fit may spill to device-backed local memory, adding loads, stores and new waits. A kernel with more resident warps can therefore run slower than one with fewer warps and less data movement.

A kernel needs enough independent ready work to cover instruction and memory latency. Chasing the maximum occupancy percentage can instead force spills, shrink useful tiles, or increase synchronization.

For a simple resource example, suppose each 256-thread block needs 32 KiB of shared memory and an SM makes 64 KiB available to these blocks. Shared memory permits only two blocks, or 16 warps, even if the thread and register limits would allow more. Relative to a 64-warp ceiling, that is 16 / 64 = 25% theoretical occupancy. This hypothetical capacity calculation predicts what can fit, not which warps are ready or how fast the kernel runs.

**Practice labs**

- [Lab 09: Sweep logical work per Triton program](reference/labs/09_triton_launch_geometry.md)
- [Lab 11: Separate lane utilization from grid-tail behavior](reference/labs/11_scheduler_tail.md)

**Mental model**

An SM keeps several warps resident and switches among ready warps. Registers, shared memory, threads, and block slots limit residency.

## 7. Memory access efficiency

**Objective**

Connect lane addresses to memory transactions and useful bandwidth.

**How it works**

Coalescing is the memory system's combination of addresses requested by active lanes of a warp into as few transactions as the access pattern allows. A transaction transfers a chunk of memory, so fetching scattered values can move more data than the useful values alone require. A stride tells how far the storage address advances when a logical index increases. Alignment means starting at a useful address boundary; a sector is a fixed-size portion used in memory-traffic accounting.

A tensor view changes how indices refer to existing storage without copying its values. A copy creates new storage, possibly with a different layout. A contiguous tensor has a compact logical layout, but good coalescing still depends on which indices adjacent lanes actually access. This lesson connects those lane addresses to traffic, rather than treating contiguous storage as a universal performance switch.

The memory hierarchy explains where data can live. Coalescing explains how one warp’s lane addresses are combined when those data must cross the global-memory interface.

Start with the addresses requested by one warp instruction. Suppose all 32 lanes read one 4-byte value, and lane `i` reads `base + 4*i`. The useful values occupy 128 consecutive bytes. If the base is suitably aligned, the memory system can cover them with a compact group of sectors and transactions. This does not imply a single 128-byte hardware transaction.

Now separate adjacent lane addresses by a large stride. The warp still requests only 32 useful values, but those values lie in many different memory regions. More sectors must be fetched, including bytes no lane needs. An otherwise compact pattern can also require an extra sector when it starts across an alignment boundary. Coalescing concerns this grouping of lane requests, rather than only the total tensor size.

A PyTorch view can expose such a strided pattern without moving any values. The next kernel's mapping from lanes to tensor indices decides the addresses it actually requests, so a view is not automatically slow and contiguous storage is not automatically enough for good coalescing.

Repacking with `contiguous()` creates a copy when needed. It pays for an extra read and write now in exchange for potentially cheaper later accesses. That trade is useful only when the time saved by later operations exceeds the packing cost.

A kernel can request the same logical number of elements while causing very different physical traffic. Useful bandwidth counts bytes the algorithm needs; transaction traffic includes over-fetch and replay caused by the access pattern.

**Practice labs**

- [Lab 04: Evaluate strided access and the cost of repacking](reference/labs/04_layout_and_coalescing.md)

**Mental model**

Memory requests from a warp are combined into transactions. Adjacent, aligned lane addresses usually use fewer transactions than large strides or scattered access.

## 8. Asynchronous execution and timing

**Objective**

Measure asynchronous work and distinguish overlap from reordered timestamps.

**How it works**

A CUDA stream is an ordered sequence of device work: operations in the same stream obey that order, while independent streams may overlap if resources permit. An event marks progress in a stream. The CPU can wait for an event, or another stream can wait for it to enforce a device-side dependency without blocking the CPU. Nonblocking submission means a host call can return before the requested operation finishes; it does not mean the operation is already complete.

Host-to-device (H2D) and device-to-host (D2H) transfers move data between CPU and GPU memory. Pinned host memory has pages held resident so transfers can use supported direct-memory-access paths; ordinary pageable memory can require staging. Copy engines perform transfers separately from arithmetic execution where supported. Double buffering uses two buffers so a producer can fill the next one while a consumer processes the current one, with events protecting safe reuse.

Lesson 1 separated submission from completion. Streams and events provide the ordering tools needed to make that distinction correct in programs with copies and multiple device operations.

Suppose a copy produces an input and a kernel consumes it. Putting both operations in one CUDA stream orders the kernel after the copy. The CPU may return from submitting them while the device is still working, but the stream preserves their required order.

If the producer and consumer use different streams, the program must connect them explicitly. Record an event in the producer's stream after its work. Make the consumer's stream wait for that event before using the result. Recording the event submits a marker; the event becomes complete only when the preceding work has reached it. A stream wait delays dependent device work, whereas a CPU wait delays the host until completion.

Independent work can overlap when the hardware has resources for both operations. To overlap a host-to-device copy with computation, the copy needs suitable pinned host memory and asynchronous submission, a usable copy-engine path, and compute that does not depend on that same unfinished copy. Unintended synchronization can serialize the operations even when these conditions hold.

Double buffering uses this independence across successive batches. First fill one buffer. Then compute on it while filling the other. Continue alternating, and finally wait for the remaining work to drain. A buffer cannot be overwritten while a copy or kernel still uses it; tensors and staging storage must stay alive through their last asynchronous use.

Timing follows the same dependencies. Device events can measure a defined interval in a stream. A host timer for the complete request must stop only after all work required by that request finishes, including any result transfer. Submission time alone measures how quickly the CPU queued work.

Apparent overlap can be a timestamp illusion, and accidental synchronization can erase real overlap. Incorrect stream dependencies can also expose partially produced tensors or surface an earlier asynchronous error at an unrelated later call.

**Practice labs**

- [Lab 03: Measure pageable and pinned host transfers](reference/labs/03_transfer_and_pinning.md)
- [Lab 07: Separate submission time from device completion](reference/labs/07_async_streams.md)

**Mental model**

CUDA streams order work within a stream while permitting independent work across streams. True copy/compute overlap also requires compatible hardware paths, pinned host memory, independent copy and compute work, and explicit producer–consumer dependencies.

## 9. Numerical precision and accelerated arithmetic

**Objective**

Relate dtype, shape, Tensor Core eligibility, throughput, memory, and numerical tolerance.

**How it works**

Floating-point formats represent numbers using a sign, an exponent and a fraction. The exponent controls range—how large or small a value can be—while the fraction controls precision, or the spacing between representable values. 32-bit floating point (FP32), 16-bit floating point (FP16) and 8-bit floating point (FP8) use 32, 16 and 8 bits; bfloat16 (BF16) means brain floating point 16 and distributes its bits differently from FP16. Rounding selects a representable approximation. Overflow exceeds the available range; underflow makes very small values lose precision or become zero.

Accumulation is the running combination of partial arithmetic results, such as sums in a dot product, and can use a wider format than the inputs. Tensor Cores accelerate supported matrix operations; TensorFloat-32 (TF32) is an arithmetic mode for FP32 inputs, not a separate tensor storage dtype. Scaling changes magnitudes to fit a chosen representation. A format decision must consider both the stored values and the arithmetic actually performed.

Arithmetic pipelines consume values fetched through the memory system. Precision changes both the representation of those values and which library or Tensor Core instruction can implement an operation.

### Separate stored values from the arithmetic used on them

A precision choice affects two stages. First, storing or converting an input rounds its values to a representation. Then the selected kernel performs arithmetic, possibly using a wider format for intermediate sums. The output may be rounded again when it is stored. Input, multiplication, accumulation and output precision therefore need not match.

FP32 offers a broad range and relatively fine precision. FP16 uses fewer bytes but has a narrower range, so some computations need scaling to keep important values representable. BF16 keeps the FP32-sized exponent but fewer fraction bits: it preserves a similar range with coarser spacing. FP8 uses fewer bits again and needs a suitable scaling recipe and higher-precision accumulation or surrounding state.

TF32 is different: it is a Tensor Core compute mode for selected operations on FP32 inputs, not a storage dtype. Matrix dimensions, alignment, operation type, framework policy and library selection determine the actual kernel. Pointwise operations may still run on ordinary CUDA cores inside a mixed-precision program.

### Fit values into the chosen representation

An encoding determines which numbers can be represented. FP formats allocate bits to sign, exponent and fraction; 8-bit integer (INT8)/INT4 quantization instead represents values through integer codes and scale/zero-point conventions. Four-bit weights are not automatically FP4 training. Smaller formats trade range or spacing between values for fewer bytes. A scale maps values into the available range; large outliers can make small values hard to preserve. In integer quantization, a zero point is the integer code that represents real zero. Per-tensor scaling shares one scale across the whole tensor; per-channel scaling assigns scales along a selected feature dimension; block scaling assigns a scale to each bounded group of values. Sharing one scale is simple, while smaller groups can improve local fit at the cost of metadata and extra operations. Keep sensitive reductions and accumulations in suitable higher precision, and measure the complete conversion-plus-compute path.

### Follow the framework policy to the selected implementation

A matmul precision policy chooses which internal arithmetic a framework may use for FP32 matrix products. `torch.set_float32_matmul_precision("highest")` requests FP32 internal computation; `"high"` permits supported faster reduced-precision internal algorithms. It does not convert the stored tensors to BF16 or guarantee one kernel. The policy affects eligible operations after it is set, but identifying the selected kernel requires separate dispatch evidence. Explicit BF16 or FP16 inputs are different cases because their values have already been rounded to those storage formats.

### Understand what the error measure captures

The Euclidean (L2) norm is the square root of the sum of squared elements. Relative L2 error divides the norm of candidate-minus-reference by the reference norm, measuring aggregate discrepancy rather than the worst element. Reference `[3,4]` and candidate `[3,4.1]` give `0.1/5 = 0.02`. Choose the reference and near-zero denominator rule before testing; Lab 02 uses a random reference expected to have a nonzero norm. A zero-reference extension needs a declared absolute-error or guarded-denominator policy, as explained in its guide. Compare each candidate with the appropriate reference, keep finite checks, and report both timing and error. An aggregate threshold does not establish task quality or bound every individual element.

Lower precision can reduce bytes and accelerate matrix operations, but a dtype annotation does not guarantee Tensor Core dispatch or acceptable training/inference behavior. Storage, input, multiply, accumulation, and output precision can differ.

**Practice labs**

- [Lab 02: Compare matrix precision, error, and throughput](reference/labs/02_tensor_core_precision.md)

**Mental model**

H100 Tensor Cores accelerate supported matrix operations and dtypes, but dispatch depends on shapes, alignment, library selection, and framework policy. Accumulation precision and scaling still determine numerical behavior.

## 10. Arithmetic intensity and performance limits

**Objective**

Decide whether another byte or another operation is the more valuable optimization target.

**How it works**

The roofline model relates a workload's arithmetic rate to how much data it must move. A FLOP is one floating-point operation; FLOP/s measures operations completed per second. Bandwidth measures bytes transferred per second. Arithmetic intensity is operations per byte at a stated memory boundary, such as high-bandwidth memory (HBM). A roofline plot puts intensity on the horizontal axis and achieved arithmetic rate on the vertical axis. Its sloped bandwidth ceiling and horizontal compute ceiling meet at the ridge point.

A low-intensity calculation may have to wait for bytes even when arithmetic units are available; a high-intensity one can reuse enough data to approach a compute limit. The ceilings describe upper bounds under declared assumptions, not expected measurements. The chosen precision, operation type and byte-count boundary must match the plotted workload before the model can explain a result.

The previous lessons supplied a byte ledger and compute-path model. Roofline combines them into a bound that helps choose the next experiment without pretending to predict every kernel detail.

Begin with a count of useful floating-point operations and the bytes needed to perform them. Choose one memory boundary, such as HBM, and include the reads, writes and intermediate traffic that cross it. Dividing operations by bytes gives arithmetic intensity: how much useful arithmetic is performed for each byte moved.

The bandwidth ceiling follows directly. If the memory system supplies a given number of bytes per second, and each byte supports a given number of operations, multiplying those quantities gives the maximum arithmetic rate that data supply can sustain. As intensity increases, this ceiling rises: more reuse lets each transferred byte support more work.

The arithmetic units impose a second ceiling, the attainable compute rate for the relevant operation and precision. Performance cannot exceed either ceiling, so the roofline uses the lower of the two. Their intersection is the ridge point. Below that intensity, the bandwidth bound is lower; above it, the compute bound is lower. Independently measured sustainable rates are usually more informative than marketing peaks, which remain upper bounds.

A measured point far below both ceilings needs another explanation. Launch gaps, dependent instructions, uneven work, an unsuitable instruction path or too little parallelism can all prevent a kernel from approaching either bound. The model narrows the possibilities; it does not diagnose the bottleneck by itself.

Optimizing arithmetic in a bandwidth-limited kernel or compressing bytes in a compute-limited kernel may not move elapsed time. A roofline position turns a vague utilization observation into a falsifiable resource hypothesis.

For example, suppose a kernel performs 2 billion floating-point operations and transfers 1 billion bytes at the memory boundary being modeled. Its arithmetic intensity is 2 operations per byte. With an assumed sustainable bandwidth of 1 trillion bytes per second, the bandwidth bound is 2 trillion operations per second. If the assumed compute ceiling is 10 trillion operations per second, the lower bound on elapsed time is the larger of 1 millisecond for traffic and 0.2 milliseconds for arithmetic: 1 millisecond. These illustrative assumptions describe a bound; dependencies, launches and inefficient access can make the measured time longer.

**Practice labs**

- [Lab 05: Contrast memory-oriented and compute-oriented work](reference/labs/05_roofline_microbench.md)

**Mental model**

Arithmetic intensity is operations divided by bytes transferred. Roofline compares that intensity with attainable memory bandwidth and compute throughput to bound performance.

## 11. GPU sharing and operational health

**Objective**

Distinguish full GPU, MIG, MPS, and time-slicing and collect non-intervening health evidence.

**How it works**

GPU sharing determines which applications can use physical resources at the same time. Multi-Instance GPU (MIG) partitions supported hardware into isolated GPU instances. Multi-Process Service (MPS) coordinates work from multiple CUDA processes so compatible work can share execution resources. Time-slicing alternates access over time. A CUDA context holds a process's device-execution state; it is not itself a hardware partition. These sharing modes differ from an application merely launching several streams.

Health monitoring reports whether the device is operating normally. Error-correcting code (ECC) detects or corrects certain memory errors; Xid messages are driver-reported diagnostic events. Clock frequency is the rate at which hardware operates, and throttling reduces it in response to limits such as power or temperature. NVIDIA Data Center GPU Manager (DCGM) gathers operational metrics and health checks. A counter's scope identifies which device, instance or time interval it describes; an observation alone does not prove exclusive access.

The single-GPU lessons explain useful work and timing, while the initial preflight checks the allocated device before every run. Now interpret sharing and operational signals in more depth, before adding two-node communication. These observations never authorize changing the cluster.

First separate resource allocation from the way work is shared. A full-GPU allocation assigns a scheduler-visible device to a workload; that allocation alone does not prove that no other process can access it. MIG divides supported compute and memory resources into hardware-isolated instances. MPS instead coordinates concurrent work from CUDA processes. On H100 those clients retain separate CUDA contexts and GPU address spaces while sharing scheduling resources. Time-slicing gives workloads turns on shared resources without creating MIG-style hardware partitions.

These arrangements affect how much work can run and which counters describe it. A reading for a physical GPU may cover a different scope from a reading for one instance. Before interpreting a change, identify the device or instance and the time interval represented by the observation.

Next distinguish the health signals. ECC counters describe corrected or uncorrectable memory errors; a change during a trial has a different meaning from a lifetime total. Xid events are driver error classifications that require further diagnosis. Retired-page and row-remapping information records memory-reliability handling; interpret only fields supported on the device, including H100's row-remapping information. Clocks, power, temperature and link state describe operating conditions. A nearby change can be relevant without proving what caused an application slowdown.

Activity and capacity counters also answer different questions. Sampled GPU activity reports whether work was active during a sampling window, not the fraction of peak arithmetic achieved. Memory-controller activity concerns time serving traffic, while allocated bytes describe occupied high-bandwidth memory (HBM) capacity. Neither capacity nor an activity percentage is a transfer rate. A one-second sample can hide repeated short busy and idle bursts.

A useful interpretation connects signals from the same interval to the application's execution timeline. Passive DCGM health watches interpret collected fields; active diagnostics can alter execution and belong to a separate workflow. More detailed profiling counters require coordination with other collectors. These labs keep observations read-only and escalate concerning changes to an administrator rather than changing sharing or monitoring configuration.

Sharing or clock/thermal events can change variance and throughput enough to invalidate a benchmark. They are context, not automatic root causes, and observing them must not silently reconfigure the node.

**Practice labs**

- [Lab 12: Read GPU health and sharing signals safely](reference/labs/12_read_only_health.md)

**Mental model**

MIG creates hardware-isolated instances, MPS coordinates CUDA processes, and scheduler time-slicing shares execution over time. ECC, Xid, retired-page, power, clock, thermal, and link signals provide system context but do not by themselves prove an application cause.

## 12. GPU communication and distributed execution

**Objective**

Explain the networking layers, trace GPU-to-GPU data movement, and select meaningful collective measurements for two one-GPU nodes.

**How it works**

A node is a machine in the cluster; a rank identifies one participating process; topology describes how machines, GPUs and network interfaces connect. A collective is a communication operation performed by a group of ranks. NVIDIA Collective Communications Library (NCCL) supplies GPU-oriented collectives: all-reduce combines values and returns the combined result to every rank, all-gather assembles each rank's piece, and broadcast sends one rank's values to the group. A shard is one piece of a larger tensor; the payload is the data being communicated.

GPU networking lets separate devices exchange the values needed to cooperate on one problem. The names in this lesson describe different layers, not competing products that all do the same job. NVLink is a GPU interconnect; NVSwitch connects NVLink endpoints; InfiniBand and Ethernet are network fabrics; RDMA over Converged Ethernet (RoCE) carries remote direct memory access (RDMA) over Ethernet; GPUDirect RDMA makes supported GPU memory accessible to a network adapter; NCCL is communication software that uses available paths. A fabric is the connected collection of links and switches carrying traffic between endpoints.

Single-GPU execution, timing, roofline and read-only health checks now provide a local baseline. Two-node work adds process placement and network collectives; verify both allocated devices before interpreting their shared critical path.

Follow a value from one GPU to another. First identify the local device connections, then the network between machines, and finally the software operation that uses that path. Each layer adds a different part of the explanation.

### First locate the devices and their connections

A network interface controller (NIC) connects a machine to a network. In InfiniBand documentation, an endpoint adapter is also called a host channel adapter (HCA); NVIDIA ConnectX adapters provide supported networking capabilities. PCI Express (PCIe) connects devices through switches and host root complexes. Non-uniform memory access (NUMA) means that the access cost to host memory depends on the CPU/socket attached to that memory. A nearby GPU and NIC may have a different path from a pair whose traffic crosses CPU sockets. Placement therefore matters even when the endpoint GPUs have the same model name.

NVLink carries communication between supported GPU endpoints at high bandwidth. NVSwitch is switching hardware for NVLink: it gives several GPUs paths to one another instead of requiring every pair to have a dedicated direct link. A supported multi-GPU DGX H100 system has this scale-up connectivity as well as network adapters for communication beyond the system. NVSwitch is not an InfiniBand switch, and NVLink is not a setting that creates a connection between arbitrary servers. Special NVLink Switch System deployments can extend supported NVLink domains; their dedicated hardware is not part of this course's two-node target.

### Then identify the inter-node fabric

InfiniBand is a switched networking architecture with its own link and transport mechanisms. Its subnet manager discovers and configures the fabric's forwarding paths; that service belongs to the cluster operator. NVIDIA Quantum switches and compatible ConnectX adapters are examples of components in an NVIDIA InfiniBand deployment. An H100 GPU alone does not supply an InfiniBand fabric.

RoCE means RDMA over Converged Ethernet. It brings RDMA communication to compatible Ethernet adapters and networks. RoCEv2 uses UDP/IP encapsulation and can traverse a routed IP fabric. It is not ordinary application UDP socket I/O: the RDMA adapter executes the transfer protocol. Ethernet traffic does not automatically become RoCE traffic, and a TCP connection over an InfiniBand IP interface is not proof that an application used RDMA. NVIDIA Spectrum Ethernet and Quantum InfiniBand switches serve different fabric roles.

Congestion is competition for a link or queue whose capacity is temporarily insufficient. On RoCE networks, operators may use ECN (explicit congestion notification) to mark congestion, CNPs (congestion notification packets) to notify senders, and PFC (priority flow control) to pause selected traffic classes. These mechanisms require a coordinated design; indiscriminate pausing or mismatched configuration can hurt latency. Learn what the counters mean, but do not change NIC or switch policies as a course exercise.

### Understand RDMA before adding GPU memory

Remote direct memory access (RDMA) allows a network adapter to transfer data into or out of registered memory on a remote machine without the receiving CPU copying each payload through the ordinary socket path. Registration establishes which memory the adapter may access and how. A queue pair (QP) holds send and receive work queues; a completion reports that submitted work has reached its defined completion boundary. CPUs and drivers still create connections, register buffers and coordinate work. RDMA does not mean that the application has no CPU activity or synchronization requirements.

RDMA can operate on host memory. GPUDirect RDMA adds a supported direct path between a network adapter and GPU memory, avoiding a host-memory staging copy for that transfer. Conceptually, the host-staged path is GPU memory → host buffer → NIC → network → NIC → host buffer → remote GPU memory. A qualified direct path is GPU memory → NIC → network → NIC → remote GPU memory. PCIe attachment and registration remain involved; the NIC is not magically connected to GPU high-bandwidth memory (HBM) without an interconnect. GPUDirect peer-to-peer concerns local device access, while GPUDirect Storage concerns supported storage I/O. Those names do not prove that a particular network transfer is GPU-direct.

### Finally place NCCL above those paths

NCCL supplies topology-aware communication operations; frameworks such as PyTorch invoke it instead of implementing the network protocol themselves. It can use supported local GPU links, PCIe, RDMA network transports or IP sockets. The physical path and collective algorithm are separate choices. A ring organizes communication in neighbor-to-neighbor steps, whereas a tree organizes hierarchical exchanges. Neither word identifies the installed fabric. Even an RDMA data path may use IP connectivity to exchange setup information.

All-reduce combines values and returns the result to every rank; reduce-scatter combines and shards; all-gather reconstructs shards; all-to-all exchanges distinct partitions. For example, rank 0 holds [1, 2] and rank 1 holds [3, 4]. Sum all-reduce gives both [4, 6]; it does not concatenate them into [1, 2, 3, 4]. Every participant must agree on the operation, datatype, count and collective ordering. A mismatch can hang or fail rather than produce a useful benchmark.

Small messages emphasize fixed latency and launch/synchronization overhead, while large messages emphasize sustained transfer. Algorithmic bandwidth divides the logical payload by elapsed time. NCCL Tests' bus bandwidth applies a collective-specific normalization; it is not a direct measurement of physical NIC traffic. Strong scaling holds total work fixed as ranks increase; weak scaling grows total work with rank count. Both require the slowest-rank step time and identical correctness semantics.

### Coordinate the participating processes

Slurm is the cluster workload manager: a job requests resources, and a job step starts processes inside that allocation. A launcher assigns each process a rank, a local device and the information needed to find its peers. PyTorch's `torchrun` supplies such process coordination; a process group is the set of ranks participating in its collectives. All ranks must call matching collectives in the same order. A barrier waits for the group to arrive, while an all-reduce combines payload values; they serve different purposes. Report the maximum elapsed rank time because the group result is not ready while one required rank is unfinished.

### Separate a possible path from the path actually used

Capability, transport selection and memory registration are distinct claims. An active RDMA port establishes capability; a runtime trace can identify the transport selected by NCCL. Direct GPU-memory registration is an additional requirement, provided only through a supported platform integration such as DMA-BUF (Linux’s framework for sharing buffers between device drivers) or a peer-memory driver path. A selected RDMA transport alone does not prove that payloads avoided host staging.

A fast local kernel cannot improve a step dominated by synchronization or network transfer. Two ranks can teach collective mechanics and placement, but they cannot establish dense-node or large-cluster scaling.

**Practice labs**

- [Lab 00: Verify the two-node H100 platform](reference/labs/00_cluster_preflight.md)
- [Lab 06: Measure a two-node NCCL all-reduce](reference/labs/06_distributed_collectives.md)

**Mental model**

Collectives move and combine data across ranks. With one GPU per node, the relevant path includes GPU, host interconnect, network, and the remote node; it does not demonstrate intra-node NVLink or NVSwitch scaling. GPUDirect RDMA and GPUDirect Storage can remove selected CPU-staging paths only when the NIC, storage, driver, topology, and software stack support them.
