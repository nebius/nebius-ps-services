# GPU Performance Optimization with PyTorch

This course teaches a repeatable optimization workflow: establish a trustworthy baseline, classify the limiter, select the smallest useful evidence, change one factor, and remeasure end to end.

## 1. Freeze the workload and correctness contract

**Start here**

### What GPU optimization means

GPU optimization means making an application use less time, memory or energy for a specified amount of correct work. It does not mean making a utilization gauge as high as possible. This course teaches a repeatable way to investigate a slow workload, identify the stage that limits its result, and select a change that addresses that stage. If GPUs, tensors, kernels or streams are new to you, first complete GPU Fundamentals; this opening provides a recap rather than assuming profiling experience.

A kernel is a unit of device code launched across GPU threads. A Python operation may invoke one kernel, several kernels, a library routine or compiled generated code. Optimizing that operation could mean executing fewer instructions, reusing data, reducing intermediate allocations, exposing more independent work, or removing unnecessary launches. It can also mean changing the surrounding CPU work so the kernel receives inputs on time.

### Why a powerful GPU can still be slow

A baseline is the reference implementation or run; a candidate contains the change being tested. A control is a comparison designed to isolate a suspected cause. Correctness tolerance is the allowed numerical difference from the reference. The critical path is the chain of dependencies that determines when the result is ready: exposed time still delays completion, whereas overlapped work progresses alongside other work. Amdahl's law bounds the overall benefit by the part of the program left unchanged. A shape lists tensor dimensions, a dtype specifies value representation, and a seed initializes reproducible pseudorandom choices. A text token is a unit produced by a tokenizer, often a word part or punctuation mark; token counts describe equivalent work only when the tokenizer and input are fixed. These are the basic terms used to define an optimization experiment.

An expensive GPU is useful only while advancing the application's required work. It may be waiting for data, starved by CPU submission, repeating avoidable memory reads, processing inefficient shapes, or waiting for another device. A fast kernel can be followed by a long CPU pause. Conversely, a continuously busy GPU can be doing avoidable work. Optimization is needed when measured latency, throughput or memory use fails the application's target, not simply because a newer technique exists.

CPU operations still matter. Loading files, decompressing records, tokenizing text, creating batches, dispatching operations and processing outputs commonly run on the host. Moving every one of them to the GPU is not the goal. Accelerate, batch or overlap the stage that actually delays completion. First confirm correctness and resource availability; an exhausted input queue and an incorrect output are different problems.

### How an application reaches the GPU

Follow one batch through storage or an input source, CPU preparation in host RAM, transfer to device memory, framework dispatch, queued kernel execution, and output consumption. The CPU sends launch commands through the CUDA software stack, while transfer operations move data. A launch does not automatically resend every tensor; already-resident data can be used by successive kernels. The CPU may continue submitting work before the GPU finishes, so a timestamp around a Python call is not necessarily the time to obtain a usable result.

Investigate in this order: define the desired outcome and correctness limits; reproduce a representative baseline; separate CPU, transfer, device, input and communication time; choose one plausible limiting stage; change one factor; repeat the same work; accept only if the complete result improves within the limits. A timeline is often more useful initially than a detailed kernel counter because it shows which stages are actually exposed on the path to completion.

### How techniques and tools fit the investigation

Compare the real input pipeline with synthetic host inputs and with inputs already resident on the GPU to investigate input starvation. Compilation translates a program region into an executable implementation; fusion combines operations into fewer kernels; a CUDA Graph is a reusable plan of device operations and their dependencies. These techniques respectively change implementation, remove intermediate work, or reduce repeated submission overhead. Layout describes how tensor indices map to storage; numerical precision describes how finely values are represented. They are introduced here as a map, then defined and practiced in their own lessons. These are hypotheses to test, not switches to enable together. Distributed overlap belongs later, after the local path is understood.

Read-only nvidia-smi and NVIDIA Data Center GPU Manager (DCGM) observations describe device state and trends. A profiler records execution activity: PyTorch Profiler associates framework operations with that activity, Nsight Systems explains the CPU/transfer/kernel timeline, and Nsight Compute examines selected kernel resource and memory behavior. NVIDIA Tools Extension (NVTX) supplies human-readable ranges in a trace. Profilers add overhead, so collect diagnostic traces separately from final unprofiled timing. Lesson 3 and the Diagnostic tooling setup guide explain selection and safe use.

### Try a small example and choose your route

Assume a request spends 6 milliseconds preparing data on the CPU and 4 milliseconds executing GPU work, with no overlap. Halving the GPU work yields 8 milliseconds overall, not 5. If preparation of the next batch can safely overlap the current batch's device work, steady-state throughput may instead be limited by the slower stage; first-request latency and buffer ownership still need separate checks. Draw both timelines before choosing a technique.

Use Labs 01 and 02 to learn valid timing, then select a profiler and work through the local optimization lessons. The capstone asks which change should be retained, not how many techniques were applied. Advanced learners should still write down the baseline, completion boundary and disconfirming observation before touching the hotspot.

**Objective** Define equivalent work, accepted outputs, and benchmark identity before tuning.

**Prerequisite bridge** GPU Fundamentals supplied execution, memory, timing, and roofline models. Optimization begins by turning those models into a controlled experiment whose inputs, outputs, and completion boundary remain fixed.

**Why it matters** Most false speedups come from changing the workload, omitting required synchronization, warming only one candidate, or accepting a numerically different result. A useful result must also matter on the end-to-end critical path.

**Mechanism** Describe the path as host preparation, transfer, launch, GPU kernels, communication, and output handling. Label each stage exposed or overlapped. Freeze shapes, dtypes, seeds, sequence lengths, batch semantics, warm-up, repetitions, environment, and output tolerance. Report a distribution rather than the best sample. Classify the first supported limiter as compute, memory/data movement, host/launch, communication, or input/storage; capacity is a feasibility constraint, while divergence, layout, fragmentation, and imbalance are mechanisms that can produce those limits. For the selected family, write one expected supporting observation and one disconfirming control before changing code so high utilization, low occupancy, or a long kernel is not mistaken for a cause by itself. Amdahl reasoning bounds the total gain: eliminating a stage that occupies 10 percent of serial latency can reduce total latency by at most 10 percent, corresponding to a maximum speedup of about 1.11×.

**Recall** Which variables must remain fixed for a performance comparison to be causal?

**Mental model** A benchmark is a controlled experiment. Input shapes, dtypes, seeds, compilation state, hardware allocation, and correctness criteria are controlled variables; the selected optimization is the independent variable.

**Practice labs**

- [Lab 01: Choose a valid GPU timing boundary](reference/labs/01_timing_basics.md)

## 2. Measure asynchronous GPU work correctly

**What it is** GPU submission is asynchronous: a CPU call can finish after requesting work but before the GPU completes it. Wall-clock timing measures elapsed real time between host timestamps. CUDA-event timing measures the interval between markers reached on the device, which can include dependencies, idle gaps and other effects inside that interval; it is not simply a sum of active kernel durations. Synchronization makes the required completion boundary explicit.

Warm-up runs prepare caches, compilation and library choices before steady-state measurement. Lazy initialization defers setup until first use; autotuning tries candidate implementations to choose one. Repeated samples expose variation: the median is the middle ordered value, or the average of the two middle values for an even sample count. A percentile such as p95 marks a value at or below which approximately 95 percent of observations fall. A short isolated kernel and a complete application need different timing boundaries, even when both report milliseconds.

**Objective** Choose CUDA events, synchronized wall time, or profiler timelines for the question being asked.

**Prerequisite bridge** The experiment contract names the completion boundary. This lesson chooses a clock and synchronization placement that actually measures that boundary.

**Why it matters** CUDA submission is asynchronous. A CPU timer can report only enqueue cost, a CUDA event can report elapsed work in a stream, and synchronized wall time can include host and dependency cost. These are different metrics, not contradictory answers.

**Mechanism** Warm the code path before timing to exclude lazy module loading, allocator growth, compilation, and autotuning unless startup is the target. For device time, record timing-enabled CUDA events before and after the operation in the same dependency chain, then synchronize the ending event. For an end-to-end iteration, use a CPU clock around all required work and synchronize exactly at the declared completion. For multiple streams, events must encode producer-consumer waits; one stream’s end event does not cover unrelated work elsewhere. Preserve individual samples and include medians plus tails.
A scalar read extracts one numerical value from a tensor into the host program. Calling `.item()` on a CUDA scalar requires its value to become available to the CPU and can therefore wait for earlier device work. Reductions such as `square().mean()` first compute one value from many elements; converting that result to a Python number introduces the host boundary. For frequent logging, retain detached device scalars and aggregate or transfer them at the declared reporting boundary when semantics permit. Detaching removes the value's autograd connection; it does not copy it to the CPU or wait for the GPU. Include the final read when the application's completion contract needs that result.

**Recall** Why is CPU submission time not kernel execution time?

**Mental model** GPU work is queued asynchronously. CUDA events measure time on a device stream, while synchronized wall time includes host and boundary costs.

**Practice labs**

- [Lab 01: Choose a valid GPU timing boundary](reference/labs/01_timing_basics.md)
- [Lab 02: Remove unnecessary per-step host synchronization](reference/labs/02_sync_trap.md)

## 3. Select Nsight Systems, PyTorch Profiler, or Nsight Compute

**What it is** A benchmark measures how long work takes or how much work completes; a profiler records where execution spends time; monitoring samples system state. A trace records events, often displayed as a timeline. A hardware counter measures activity such as bytes moved or instructions executed. NVIDIA Nsight Systems follows the application timeline across CPU work, GPU launches and communication. Nsight Compute examines a selected kernel's hardware behavior. PyTorch Profiler relates framework operations to CPU and device activity.

Instrumentation adds observation work to the program. NVIDIA Tools Extension (NVTX) ranges are named annotations that help identify a chosen phase in a trace. Self time excludes an event's recorded children; inclusive time includes them, so summing overlapping rows can double-count work. Collecting many counters may require replay—rerunning the measured work—which is why a profile explains performance but is not automatically an unbiased benchmark.

**Objective** Escalate from application symptoms to the narrowest tool that answers the next question.

**Prerequisite bridge** Trustworthy timing proves that a slowdown exists but rarely explains it. Tool selection should follow a hypothesis and move from broad semantic context toward a single kernel only when the evidence requires it.

**Why it matters** Capturing every counter creates huge, perturbing traces without answering the question. Engineers also confuse monitoring, profiling, and benchmarking, or treat unavailable privileged counters as a reason to guess.

**Mechanism** PyTorch Profiler connects Python operators, shapes, allocations, and scheduled training steps; short active windows and semantic NVTX ranges keep traces interpretable. Nsight Systems shows CPU threads, CUDA API calls, kernels, copies, collectives, and gaps on a common timeline; inspect the timeline before aggregate tables. Nsight Compute replays selected kernels to collect instruction, memory, scheduler, and roofline metrics; replay overhead means its run is not acceptance timing. `nvidia-smi` and DCGM provide device state, NCCL Tests isolate collectives, `nvbandwidth` isolates supported transfer paths, and serving load tools own request-level distributions. Tool availability and counter permission belong to the cluster owner; the learner verifies them inside an allocation and fails clearly if a required scope is unavailable.

For an initial tool map, use the [Diagnostic tooling setup](reference/tooling-setup.md) guide: it separates device health, continuous telemetry, framework attribution, system timelines, kernel counters and load generation. It also explains protected DCGM Exporter/Prometheus collection, counter contention, NCCL Tests versus nvbandwidth and standardized benchmark context. Reading these interfaces is not permission to reconfigure cluster monitoring or networking. Use the cheapest observation that can distinguish your current hypothesis, then narrow the scope before collecting expensive kernel details.

Read an NVTX range first as a host annotation: it names the code that submitted work, not necessarily the interval during which the GPU executed that work. Follow CUDA API correlation to the corresponding copy or kernel on its stream, then inspect the dependency that delays the next operation. A blank region on one selected stream is only an absence of captured activity there. Other streams, processes, copy engines or uncaptured work may still be active. Likewise, a continuous kernel bar does not establish that all streaming multiprocessors are busy or that any compute pipeline reaches peak throughput. Use qualified hardware metrics when that is the question.

Nsight Systems can retain PyTorch context through explicit NVTX and supported PyTorch annotation options. Its visibility depends on the selected trace domains, processes and permissions; it is not an unlimited view of the machine. A nearby memory-unmapping call is a hypothesis for a host delay, not causal proof. Match the blocked API, allocation lifetime and downstream wait, make one controlled change, then repeat the trace. Keep acceptance timing in a separate unprofiled run. Nsight Compute replay, cache control and clock control can change a kernel's conditions; do not compare its duration directly with application timing without accounting for those settings.

**Recall** Which tool shows a system timeline, and which inspects one kernel's instruction and memory behavior?

**Mental model** PyTorch Profiler connects framework operators to device work; Nsight Systems exposes CPU, runtime, kernels, communication, and gaps; Nsight Compute diagnoses a selected kernel.

**Practice labs**

- [Lab 07: Build a readable profiler map of a workload](reference/labs/07_profile_workload.md)
- [Lab 14: Diagnose synchronization, launch, memory, and compute limits](reference/labs/14_profiler_bottlenecks.md)

## 4. Reduce launch and Python overhead

**What it is** Eager execution asks the framework to perform operations as the program reaches them. Dispatch is the process of selecting and submitting an implementation; dispatch overhead is the CPU work surrounding the useful device computation. Compilation analyzes a program region and generates an executable implementation. Fusion combines several operations into fewer kernels, allowing some intermediate values to remain in registers instead of being written to separate tensors. Vectorization or batching expresses work on many values together rather than issuing a separate operation for each value.

For example, multiplying, adding and activating an array can launch three kernels in eager execution but may become one fused kernel. A compiler guard checks an assumption such as shape or dtype; a graph break ends a captured compiler region when code cannot be represented there. Aliasing means tensors share storage, and mutation changes existing values. Compilation and fusion are distinct from CUDA Graph replay, introduced next.

**Objective** Recognize launch-bound workloads and reduce unnecessary dispatch.

**Prerequisite bridge** A Systems timeline exposes gaps and many short kernels. This lesson chooses semantics-preserving ways to submit fewer, larger pieces of work.

**Why it matters** H100 can finish small elementwise kernels faster than Python and the runtime can dispatch them. The symptom is a launch-dense timeline with low work per launch, not merely low average utilization.

**Mechanism** First remove unnecessary scalar reads such as `.item()` in a hot loop because they can force device-to-host synchronization. Batch independent items, vectorize Python loops, and use existing fused PyTorch or library operators. `torch.compile` may fuse operations and reduce Python overhead, but guards, graph breaks, dynamic shapes, cold compilation, and cache behavior belong in the evidence. Fusion removes launches and intermediate reads/writes only when aliasing, mutation, numerical order, and reuse allow it. Inspect generated regions and fallbacks instead of assuming compilation succeeded.
An activation is a nonlinear elementwise transformation used after linear arithmetic. SiLU, the sigmoid linear unit, computes `x/(1+exp(-x))`; tanh, the hyperbolic tangent, smoothly maps real values between -1 and 1. For example, SiLU(0)=0 and tanh(0)=0, while positive and negative inputs exercise different parts of each curve. Lab 03 combines SiLU, tanh, multiplication and addition, giving the compiler adjacent pointwise operations to fuse. Preserve the exact composition and compare full outputs before attributing a change to fewer launches.

`torch.compile(..., fullgraph=True)` requires the selected function to be captured as one compiler graph and raises an error for an unsupported graph break. A compiler graph is an operation/dependency representation, not a promise of a single CUDA kernel. Warm compilation separately, verify outputs, and inspect emitted work before claiming fusion. This same distinction applies to the later capstone.

**Recall** When does fixed launch cost become a large fraction of runtime?

**Mental model** Many tiny eager operations repeatedly cross Python, dispatcher, and CUDA-launch boundaries. Fusion, compilation, batching, or graphs can amortize those costs.

**Practice labs**

- [Lab 03: Compare eager and compiled pointwise execution](reference/labs/03_compile_fusion.md)

## 5. Use CUDA Graphs only for stable execution

**What it is** A CUDA Graph is a reusable execution plan for GPU operations and the dependencies between them. Operations, such as kernels or memory copies, are nodes; dependency edges specify which work must finish before other work can start. The ordinary plan is a directed acyclic graph: its dependency arrows do not form a cycle. Instead of rebuilding and submitting each operation individually on every iteration, the application prepares a graph and launches the prepared plan again. Capture records compatible submitted work into a graph; replay executes that recorded plan, not a saved set of numerical answers.

For example, a multiply → add → activation sequence can be replayed through one graph launch while still containing three kernels. Graph replay reduces repeated submission/setup work; it is not kernel fusion or program compilation. In this PyTorch lab, capture retains memory addresses and a fixed workload shape. New values can be copied into the same input buffers before replay. Rebinding a Python variable to a new tensor does not change the captured address. Broader CUDA graph-update APIs exist, but are not implemented by this fixed-shape exercise.

Instantiation prepares a graph as an executable plan. A graph-private memory pool retains storage used by captured work. A shape bucket groups inputs handled by one supported capture; a fallback executes inputs outside that capture's contract through another supported path. Buckets and fallback routing are extensions to this lesson's fixed-shape baseline, not requirements of every CUDA Graph application.

**Objective** Identify graph-safe regions and measure replay benefits and constraints.

**Prerequisite bridge** Compilation can change and fuse a graph of operations. CUDA Graphs solve a different problem: they record an already chosen stable sequence of CUDA work and replay its submission cheaply.

**Why it matters** Graph capture is powerful for repeated fixed work but fragile around dynamic allocation, changing addresses, CPU decisions, synchronization, and unsupported operations. Confusing compile, capture, and replay hides cold cost and fallback behavior.

**Mechanism** Warm allocations, library plans, and compilation before capture. During capture, operations populate graph-private memory pools and retain virtual-address relationships required by replay. Inputs are commonly copied into stable buffers and outputs read from stable buffers after replay. For this captured PyTorch workload, shapes, referenced storage, control flow, stream dependencies and participating operations must remain compatible. This course's dynamic-input extension uses a bounded bucket set plus an eager fallback. Measure compile time, capture time, steady replay, memory retained by graph pools, and bucket hit/fallback rate separately. A compiled region can be captured, but each layer’s contract remains distinct.

**Recall** What must remain stable for captured memory addresses and execution topology?

**Mental model** CUDA Graphs capture a repeated device-work DAG and replay it with lower launch overhead. This PyTorch exercise requires compatible shapes, stable referenced storage and capture-safe operations; these are its capture contract, not a claim that the broader CUDA API forbids graph updates.

**Practice labs**

- [Lab 04: Replay a fixed-shape workload with a CUDA Graph](reference/labs/04_cuda_graphs.md)

## 6. Keep the input pipeline ahead of the GPU

**What it is** An input pipeline turns source data into batches ready for GPU computation. It can read files, decode records, transform values, combine examples and transfer the resulting tensors. PyTorch's DataLoader coordinates fetching and batching; workers perform preparation, and collation combines individual examples into the batch structure. Prefetch prepares future batches before they are requested, while queue depth describes how many are waiting. Persistent workers keep their processes alive between passes through the data, called epochs.

Tokenization converts text into model token IDs; augmentation transforms examples while preserving the intended task. A pinned host buffer supports suitable asynchronous host-to-device (H2D) transfers. A resident-input control starts with data already on the GPU; a synthetic-input control generates data without the real storage path. Comparing these controls helps locate starvation, when the next batch is not ready, without assuming every idle gap is caused by slow GPU kernels.

**Objective** Detect and repair host-side starvation without hiding it behind device averages.

**Prerequisite bridge** Even perfect GPU kernels wait if the next batch is not ready. Model the loader as producers filling a bounded queue for a GPU consumer rather than as a single “data time” number.

**Why it matters** Increasing workers can move the bottleneck to storage, CPU scheduling, memory, interprocess transfer, or shared filesystem contention. It can also duplicate or reorder samples and change the training workload.

**Mechanism** Record batch-ready timestamps, queue depth, consumer idle gaps, CPU utilization, storage latency, and transfer time. Separate reading, decoding, tokenization/augmentation, collation, pinning, and H2D copy. Use persistent workers only when process startup is a repeated cost; use prefetch only when memory and freshness permit. Preserve deterministic distributed sampling, epoch/seed behavior, and exact sample counts. Pinned buffers must remain valid until asynchronous copies complete. Resident-input and synthetic-input controls distinguish loader starvation from later stages. DALI or another accelerated pipeline is justified only after the current stage is proven and its semantic equivalence is testable.

Pinned memory is host memory kept resident so suitable direct memory access (DMA) transfers can use stable physical backing without first staging pageable data. Pinning itself costs time and consumes a limited host resource. Preallocate or use a loader's pinning path when justified; calling pin_memory() in the hot loop can erase a gain. Nonblocking submission lets the host continue, but copies and kernels in one stream remain ordered. Copy/compute overlap additionally needs independent work, separate streams, suitable hardware and explicit dependencies.

Lab 19 uses a bounded ring of slots. Each slot owns a pinned host matrix, a device input and an output. Before overwriting a used slot, the host waits for its previous compute-complete event. It fills the next batch, submits H2D on the copy stream, and records a ready event. The compute stream waits for that event before reading the input; it records completion after the GEMMs and exact output check. Meanwhile, the next slot can receive the following input. This deliberately conservative ownership rule protects both host DMA reads and device compute reads. Keeping tensors alive alone does not prevent an in-place overwrite. `record_stream()` tells PyTorch's allocator that a tensor is used on another stream, preventing its storage from being recycled for a new tensor before that recorded use finishes. It does not make a consumer wait for a producer or protect an application's own in-place overwrite.

First isolate stream placement with the supplied serial and pipeline modes, which keep pinned buffers, work and slot count fixed. Then revisit Lab 05's producer controls. Choose loader workers from measured readiness and allocated CPU/memory limits, not automatically from vCPU count. Worker prefetch, host-asynchronous submission and device copy/compute overlap solve distinct waits.

To use a DataLoader, define how a dataset maps an index to one sample, then select a batch size and a collation rule that assembles samples. With zero workers, the calling process fetches the data. With positive `num_workers`, worker processes prepare it; `prefetch_factor` controls how many batches each worker prepares ahead. Pass that option only for a positive worker count. Keep sample identity and deterministic per-index generation fixed while changing producer settings; startup and steady-state readiness answer different questions.

Inference mode is a PyTorch execution context for forward-only work that does not need autograd recording. It avoids recording a backward graph and associated tracking, reducing overhead for suitable input/output pipeline experiments. Put the forward-only loop inside `torch.inference_mode()` and retain explicit output checks. It neither waits for asynchronous copies nor proves parameters cannot be modified. Training loops that need backward require autograd instead.

**Recall** What does an idle gap before each training step suggest?

**Mental model** Reading, decoding, preprocessing, batching, pinning, and transfer form a producer pipeline. The device stalls when the next batch is not ready. NVIDIA DALI is one optional accelerated data-pipeline implementation, not evidence that storage, decode, or transfer is the current limiter.

**Practice labs**

- [Lab 05: Locate waiting in a DataLoader pipeline](reference/labs/05_input_pipeline.md)
- [Lab 19: Overlap H2D copies with a bounded input ring](reference/labs/19_h2d_pipeline.md)

## 7. Optimize memory layout and intermediate traffic

**What it is** A tensor's layout maps logical indices to storage addresses. A producer creates values and a consumer uses them; an intermediate is the producer's result passed between operations. Materializing an intermediate means writing it into an allocation instead of keeping it within an executing kernel. A traffic ledger counts those reads, writes and copies. An implicit conversion happens inside a framework or library call when its preferred layout differs from the supplied one.

For example, a transpose can initially be a cheap view, but the next operator may need a physical copy. An epilogue is work applied to an operation's result, such as adding bias after matrix multiplication; fusing it may avoid an intermediate allocation. Aliasing or mutation can require preserving shared storage, so a compiler's alias guard checks assumptions before transforming the program. This lesson extends single-access coalescing to the interfaces between several operators.

**Objective** Reduce layout conversions, non-coalesced access, and materialized intermediates.

**Prerequisite bridge** Fundamentals Lab 04 established strides, packing cost and break-even reuse for one access pattern. Keep that result as a prerequisite. This lesson asks how several producers and consumers agree on layouts, where implicit conversions appear, and whether fusion can remove an entire intermediate.

**Why it matters** Logical tensor bytes can understate physical traffic. Temporary materialization, layout copies, cache-line over-fetch, and repeated reads consume HBM bandwidth and allocation capacity even when the Python expression looks compact.

**Mechanism** Build an operation-by-operation traffic ledger: input reads, output writes, temporary writes and rereads, packing copies, and expected reuse. Inspect strides, storage aliasing, mutation, and lifetime because they constrain fusion and layout conversion. Use profiler allocation and kernel evidence to identify actual copies such as `contiguous` or implicit library transforms. A pack-once strategy breaks even when `pack_cost < reuse_count × (strided_cost - packed_cost)`. Compiler graph breaks or alias guards can prevent intended fusion, so confirm generated kernels and remaining intermediates.

**Recall** Why can two tensors with identical shapes have different strides?

**Mental model** Operators consume layouts with specific fast paths. A transpose view is cheap until a downstream kernel requires a contiguous copy; unfused operators also write and reread intermediates in global memory, with some accesses potentially served by caches. A persisting-L2 access policy identifies a memory range whose accesses should receive preferential retention in the shared L2 cache on supported configurations. It is a reuse hint, not a guarantee that the range stays resident. Giving a proven reusable range that preference competes with other useful data and requires CUDA-level evidence.

**Practice labs**

- [Lab 03: Compare eager and compiled pointwise execution](reference/labs/03_compile_fusion.md)
- [Lab 10: Survey library behavior across shapes and dtypes](reference/labs/10_shape_precision.md)

## 8. Manage allocator lifetime and peak memory

**What it is** An allocator manages requests for memory. PyTorch's caching allocator can retain blocks after tensors stop using them so later allocations avoid repeated device-allocation overhead. Allocated bytes track live tensor storage; reserved bytes also include storage held by the allocator for reuse. Peak memory is the largest amount observed during a specified interval. A workspace is temporary storage an operation needs in addition to its inputs and outputs.

Fragmentation means free storage is divided into pieces that cannot satisfy a particular request efficiently. Size classes group allocation sizes; an inactive split is an unused part of a split allocation segment. A memory snapshot records the allocator's state for inspection. In-place operations update existing storage, but aliases and autograd—the system that records operations for differentiation—may still need the old values. Reserved memory exceeding allocated memory is therefore not by itself a leak, and releasing cached blocks cannot free live tensors.

An output pipeline moves device results into host buffers and hands completed results to a consumer, such as an encoder or storage client. A buffer pool reuses a bounded set of allocations. Backpressure is the wait imposed when every slot is still owned by a transfer or consumer; it prevents unbounded queued results. A future represents eventual completion or failure of a worker task. This lesson applies allocator lifetime to both GPU and CPU ownership, after Lesson 6 established input-stream dependencies.

**Objective** Distinguish allocated, reserved, live, cached, fragmented, and peak memory.

**Prerequisite bridge** The traffic ledger counts bytes moved. A lifetime ledger asks when each allocation must coexist, which determines whether the workload fits and whether allocator state is being mistaken for a leak.

**Why it matters** Capacity and bandwidth are different problems. Reducing reserved memory may not speed a step, while shortening one large live interval can enable a larger useful batch even with unchanged kernel time.

**Mechanism** Draw each tensor, workspace, communication buffer, compiler/graph pool, and temporary as an interval from allocation to last use. `memory_allocated` is live tensor storage known to the allocator; `memory_reserved` also includes cached segments. Inactive splits and size-class mismatch can make reserved capacity unusable for a new request. Allocation retries and snapshots reveal pressure better than repeatedly calling `empty_cache()`. Remove accidental references, use in-place operations only when autograd and alias contracts allow, and move activation checkpointing or state sharding decisions to the Training course.

For D2H, a returning asynchronous copy call does not make host data readable. Record a produced event after GPU computation. The egress stream waits on that event, copies into a pinned destination and records a copied event. The CPU worker synchronizes that copied event before it reads any value. Only after the worker finishes processing may the producer recycle that slot. Retain the device source until copying completes as well. Lab 20 holds both buffers until the consumer future resolves, a simple stronger rule than the minimum source lifetime.

A thread is an execution path within a process and shares that process's memory with other threads. CPython is the standard Python implementation; bytecode is the instruction form executed by its interpreter. In a conventional GIL-enabled CPython build, the global interpreter lock (GIL) permits only one thread to execute that bytecode at a time. Blocking I/O can release it so another thread progresses. Separate processes can execute Python independently, but passing arguments or results may require serialization—encoding objects for communication—and additional copies. These distinctions explain why the consumer's kind of work matters.

The five modes expose one new mechanism at each stage: `serial` mode handles everything inline; `workers` mode hands completed copies to a bounded thread executor; `pooled` mode moves pinned allocation outside the loop; `nonblocking` mode lets the host enqueue subsequent work while copies remain on the compute stream; `pipeline` mode moves only those copies to a dedicated egress stream. The synthetic sink sleeps to represent blocking I/O and releases the Python interpreter lock. Threads are appropriate for this controlled sink; CPU-heavy Python processing may instead need processes or native code. A process-based design must account for serialization and host copies and should not pass live CUDA tensors between arbitrary workers.

The queue stays bounded by slot count even if the sink slows. Worker exceptions propagate through future.result(), and a failed future retains slot ownership rather than falsely releasing it. Final drain waits for all transfers and consumers. Ending the timer after GPU submission while output workers continue would measure an incomplete operation.

**Recall** Why may reserved memory remain high after tensors are freed?

**Mental model** PyTorch's caching allocator retains blocks for reuse. Tensor lifetimes and size patterns determine peaks and fragmentation; forced cache release is rarely an inner-loop optimization. CUDA memory pools and stream-ordered allocation make reuse and ordering explicit below the framework layer; they do not reduce live tensor bytes.

**Practice labs**

- [Lab 12: Follow live, cached, and released GPU memory](reference/labs/12_allocator_lifetime.md)
- [Lab 20: Drain a bounded D2H output pipeline](reference/labs/20_d2h_pipeline.md)

## 9. Select shapes and precision for efficient libraries

**What it is** A numerical library selects an implementation using the operation, dimensions, layout, numerical format and hardware. General matrix multiplication (GEMM) multiplies an M-by-K matrix by a K-by-N matrix to produce M-by-N values: M and N are output dimensions, while K is the dimension summed over. A tile is a smaller rectangular piece handled cooperatively by the implementation. Dispatch chooses the implementation; a cast converts values between numerical formats.

Padding adds unused or neutral values to reach a supported or efficient shape. It is valid only when the original result is preserved, such as adding matching zero entries along K. Shape alignment means dimensions suit a kernel's tile sizes; pointer alignment instead concerns memory addresses. Tensor Core hardware supports particular arithmetic paths, not every possible shape or dtype. The goal is to preserve useful work while choosing an efficient library path, not to enlarge the task until a benchmark looks faster.

**Objective** Align matrix dimensions, batches, and dtypes with efficient library kernels.

**Prerequisite bridge** Layout determines how bytes arrive; shape and precision determine which maintained library kernel can consume them and whether Tensor Core tiles are used efficiently.

**Why it matters** A mathematically smaller shape can run slower when it selects a poor kernel, wastes a final tile, or prevents fusion. Padding can improve kernel efficiency while increasing total work, so comparisons must normalize useful work.

**Mechanism** Sweep representative matrix dimensions, batch/token counts, dtypes, and layouts while holding model semantics fixed. Record useful FLOPs/tokens, padded FLOPs/tokens, dispatch/kernel name, Tensor Core evidence, memory, and output error. Distinguish storage/input/accumulation/output precision and include cast/scaling overhead. Alignment is a library- and version-specific hint, not a universal multiple. General attention-shape concepts remain here, while SDPA backend selection and attention-specific labs belong to Inference.

**Recall** What happens when padding increases arithmetic but enables a much better kernel?

**Mental model** Library dispatch depends on dtype, dimensions, strides, alignment, and hardware. A small amount of shape padding can improve utilization, but excess padding wastes work and memory.

**Practice labs**

- [Lab 10: Survey library behavior across shapes and dtypes](reference/labs/10_shape_precision.md)

## 10. Find load imbalance and tail waves

**What it is** Load imbalance means parallel tasks take different amounts of time, leaving some workers idle while others finish. Makespan is the elapsed time until all tasks are done. Contention occurs when workers compete for the same resource. A partial grid wave occurs when too few blocks remain to fill the available resident-block slots. That scheduling tail differs from tail latency, which describes slow observations in a distribution such as p95.

Warp divergence is another level: lanes within one warp follow different paths. Shared memory is divided into independently serviced banks. A bank conflict occurs when accesses to different words compete for the same bank's service; supported same-word broadcasts are different. A persistent kernel keeps workers active to take more tasks instead of launching a fresh grid for each batch of work. These concepts all involve unused capacity, but their causes and fixes differ. Diagnose the level of imbalance before changing task sizes, grouping or scheduling.

**Objective** Distinguish lane divergence, uneven blocks, uneven ranks, and the final partial scheduling wave.

**Prerequisite bridge** The local lessons have covered launch overhead, data movement, allocation, shapes and precision. Now distinguish idle lanes, uneven blocks and partial grid waves before introducing distributed scaling. Rank skew is a preview of the same completion-time problem across workers in Lesson 11.

**Why it matters** These problems can all leave execution resources idle, but they arise at different scheduling levels. A branch rewrite cannot remove a one-block tail, and adding blocks cannot equalize variable per-rank data.

**Mechanism** Warp divergence executes branch paths under partial active masks. Block imbalance gives blocks different durations because of data-dependent work, contention, or uneven tiles. A tail wave occurs when remaining blocks cannot fill the GPU after earlier full waves finish; even uniform blocks can show it. Rank skew appears when distributed workers reach a collective at different times. Inspect active-lane/branch metrics, per-block work distributions or synthetic uniform controls, grid size relative to concurrent blocks, and per-rank timelines respectively. Explanatory concepts such as bank conflicts, registers, spills, and occupancy help classify the kernel, while hands-on low-level repair belongs to Custom CUDA Kernels.

**Recall** Why can average utilization hide the work that determines completion time?

**Mental model** Parallel completion is set by the slowest participating unit. Work distributions, bucket sizes, expert routing, and grid size create different kinds of tails.

**Practice labs**

- [Lab 15: Distinguish task imbalance from partial grid waves](reference/labs/15_tail_load_balance.md)

## 11. Qualify GPU networking and tune NCCL with evidence

**What it is** Communication tuning means reducing the time that correct data exchange adds to a workload. NVIDIA Collective Communications Library (NCCL) implements GPU communication operations such as all-reduce. It chooses among available paths and algorithms; it does not create the physical network. InfiniBand is a switched network architecture, RoCE supplies RDMA over Ethernet, and GPUDirect RDMA permits a supported NIC to access GPU memory directly. Fundamentals explains those layers; here the question is which path a real job selected and whether changing one choice helps.

A transport is the software/data-transfer path, such as NCCL's Socket or IB transport. The IB name also covers RoCE through the verbs interface; a log containing IB does not necessarily mean an InfiniBand physical link. An algorithm organizes collective exchanges, while a protocol controls how NCCL chunks and synchronizes those exchanges. A queue pair (QP) is an adapter work-queue pair; increasing QPs can spread network flows, but also adds overhead. These are distinct experimental factors, not a checklist of switches to enable together.

**Objective** Establish a correct message-size baseline, identify the actual transport, test one job-local change, and decide whether it deserves an application trial.

**Prerequisite bridge** Complete local timing, profiling and load-balance lessons first. Revisit Fundamentals Lesson 12 for networking layers and collective semantics. Use this lesson before Lesson 12's scaling/overlap experiment so a degraded transport is not mistaken for an application scheduling problem.

**Recall** Why can a system support RDMA while one particular job still uses sockets or host staging?

**Why it matters** Two fast GPUs can spend most of a step waiting for communication. Wrong placement, a slow link, network congestion or one late rank can overwhelm a small algorithmic improvement. A synthetic all-reduce curve isolates part of the problem; only a subsequent application measurement establishes useful benefit.

**Mental model** Separate four claims: the platform supports a path; this job selected it; the collective performs correctly and quickly on it; the application benefits. Each claim needs its own evidence. Neither a product name nor a high utilization percentage establishes all four. Begin with the actual GPU → local attachment → NIC → fabric → remote NIC → GPU path, then examine operation semantics and timing.

**Mechanism**

### Qualify the path before measuring candidates

Topology and port-state queries describe the available attachment and network, while NCCL startup logs describe what a particular communicator selected. GPU/NIC proximity, active ports, payload transport and GPU-memory registration are different observations. IP interfaces and RDMA HCAs also use different name spaces. Socket traffic can serve setup even when payloads use RDMA; `NET/IB` alone therefore cannot establish GPUDirect RDMA. Registration can use a supported peer-memory or DMA-BUF path, so a missing `nvidia-peermem` module is not conclusive.

NCCL diagnostics are not a universal “query result.” Distinguish device/topology queries, startup path logs, collective benchmark rows and optional tuning reports. Each answers a different question. NCCL Tests v2.20.0 offers `-U 1` tuning details with sufficiently recent NCCL, but that is a separate advanced diagnostic command, not the stable table consumed by Lab 18. Do not assume the NCCL library inside PyTorch is the one dynamically loaded by an independently built benchmark.

### Read a complete size curve, not its best row

Lab 17 implements all-reduce in PyTorch with known FP32 inputs and repeated CUDA-event samples. An asynchronous collective returns a Work handle: an object representing the pending operation. With NCCL, `async_op=True` allows the host to continue after submission. Calling `work.wait()` joins that operation to the currently active CUDA stream, so later work on that stream waits for collective completion; it does not by itself establish that the CPU has waited for device completion. Record the stop event on that stream after `work.wait()`, then synchronize the stop event before reading elapsed time on the host. This places collective completion inside the measured interval. Lesson 12 uses the same dependency rule to overlap independent computation safely. Lab 18 runs NVIDIA's externally built `all_reduce_perf` using MPI, then validates the result table. MPI launches cooperating processes; NCCL performs the collective. The MPI-enabled benchmark must use the site's compatible Slurm integration. The supplied torchrun launcher is for Python distributed labs and cannot substitute for this MPI launch.

The benchmark reports separate out-of-place and in-place results. Out-of-place uses distinct input and output buffers; in-place reuses the input storage for the result. Each has time in microseconds, algorithmic bandwidth in decimal GB/s, normalized bus bandwidth, and a count of incorrect checked elements. A zero `#wrong` is useful only when correctness checking was enabled. It is not a network packet-error count. A completed table with a failed process exit is not a successful run.

For all-reduce with N ranks and logical payload S bytes, `algbw = S / time_seconds / 1e9` and `busbw = algbw × 2(N−1)/N`. The second expression is a normalization for comparing collective work; it is not a hardware traffic counter. With two ranks the multiplier is one, so equal algbw and busbw are expected. They do not establish saturation. Keep MiB (2²⁰ bytes), GB/s (10⁹ bytes/s), and link rates in Gb/s (bits/s) distinct.

### Why a comparison needs a fresh communicator

Automatic selection is the baseline for identifying a useful intervention. Transport, GPU-memory access, collective algorithm and QP count change different parts of execution. A comparison is meaningful only when the selected path is eligible and all other work, ranks and transport conditions remain fixed. Communicator initialization consumes these settings, so a fresh process isolates a candidate from an already initialized baseline. Site interface/plugin configuration is part of the environment contract; inherited experimental overrides would confound the comparison. More network flows can improve distribution while adding latency and adapter overhead, so extra configuration is not evidence of improvement.

MPI, the Message Passing Interface, is a standard for communication between cooperating processes; its runtime also supplies process coordination to MPI-enabled programs. In Lab 18, MPI coordinates the benchmark ranks and NCCL performs the GPU collective. Slurm provides the allocated resources. Use the site's qualified MPI launch integration for that binary; `torchrun` coordinates the Python process-group path in the other labs. Matching rank counts alone does not make the launch protocols interchangeable.

**Practice labs**

- [Lab 00: Establish the optimization platform contract](reference/labs/00_cluster_preflight.md)
- [Lab 17: Measure a two-node NCCL message-size curve](reference/labs/17_nccl_transport_sweep.md)
- [Lab 18: Run and interpret NVIDIA NCCL Tests on Slurm](reference/labs/18_nccl_tests_report.md)

## 12. Diagnose distributed scaling and collective overlap

**What it is** Distributed optimization asks how work and communication combine across processes. A rank identifies a participating process. Data-parallel execution gives ranks different input work; in training, Distributed Data Parallel (DDP) keeps a model replica on each rank and combines gradients. Parameters are the model's adjustable values; loss is the numerical objective used to judge its output. A gradient describes how changing a parameter affects loss; backward computes gradients, and the optimizer uses them to update parameters. This short training example illustrates a general communication dependency rather than replacing the Training course.

A collective is a group communication operation. A bucket combines several values into one transfer, and becomes ready when those values have been produced. Communication is overlapped when independent computation progresses at the same time. Exposed communication is the part still delaying completion on the critical path—the chain of dependencies that determines total elapsed time. An asynchronous call returning early alone does not demonstrate useful overlap.

**Objective** Separate useful compute, communication, exposed communication, and synchronization imbalance.

**Prerequisite bridge** A measured single-GPU baseline, local imbalance diagnosis and Lesson 11's qualified communication path are prerequisites for useful scaling evidence. Distributed execution adds data partitioning, process placement and collective dependencies; one slow rank can now set the completion time for all ranks.

**Why it matters** `async_op=True` or a non-default stream proves only asynchronous submission. The collective still delays the step if consumers wait immediately, if it contends with compute, or if the slowest rank arrives late.

**Mechanism** For fixed-work strong scaling, compare one and two ranks with the same global useful work and calculate speedup `T1/T2` plus efficiency `T1/(N*T2)`. For weak scaling, hold per-rank work constant and disclose larger global work. Mark backward compute, bucket readiness, collective start/end, optimizer dependency, and exposed wait on a timeline. Smaller buckets start earlier but add launches and fixed collective latency; larger buckets amortize overhead but reduce overlap runway. Always report the slowest rank and separate arrival skew from transfer duration.
The training workload here is a small feed-forward network: a linear projection, GELU activation and another linear projection. Autograd records the forward dependencies needed to differentiate its scalar loss, which is the mean squared output in Lab 08. Call `zero_grad` to clear the previous step, compute the loss, call `backward` to obtain gradients, then call the optimizer's `step` to update parameters. AdamW uses running averages of gradients and squared gradients to adapt updates, plus a separate weight-decay shrinkage. Its history is additional persistent state. This is enough to follow the supplied communication experiment; a finite loss alone does not establish convergence or equal training trajectories.

DDP wraps one model replica per rank and averages its gradients across ranks before the dependent update. Split a fixed global batch into equal disjoint local batches and keep the local mean-loss convention fixed. For a global batch of 64, two ranks each use 32 examples; using 64 on each would double the work and change the scaling question. Identical initial state, data and optimizer settings support comparability, but reduced-precision reduction order can still change numerical results.

An asynchronous collective returns a `Work` handle representing the pending operation. Do independent computation while it progresses, then join before reading, overwriting or updating values that depend on its output. For ordinary CUDA/NCCL operation, `work.wait()` establishes the dependency on the current CUDA stream; it is not universally a host wait for all device execution. A CPU observation or elapsed host-time boundary still needs the relevant device/event synchronization. Retain buffers until the operation finishes and preserve collective order across ranks. Lab 13 applies this to a synthetic schedule; a timeline must establish any actual overlap.

**Recall** What is the difference between communication time and exposed communication time?

**Mental model** Distributed speedup depends on fixed work, per-rank work, collective volume, overlap, and the slowest rank. Communication hidden behind useful compute is not on the critical path. GPU/CPU/NIC NUMA placement and transport health are qualified before NCCL algorithm or protocol tuning.

**Practice labs**

- [Lab 08: Compare one GPU with two-node DDP](reference/labs/08_distributed_scaling.md)
- [Lab 13: Test whether communication can overlap independent compute](reference/labs/13_collective_overlap.md)

## 13. Decide between framework, library, compiler, and custom kernel paths

**What it is** The framework, library, compiler and custom-kernel layers solve different parts of a GPU program. A framework such as PyTorch composes tensor operations. Libraries supply maintained implementations: cuBLAS provides linear algebra, cuDNN neural-network primitives, and CUB parallel building blocks such as reductions within CUDA Core Compute Libraries (CCCL). CUTLASS provides composable templates for matrix kernels. A compiler transforms source or an operation graph into executable code. A custom kernel is device code whose implementation and support contract you maintain.

A hotspot is a region responsible for a meaningful part of the measured cost. An epilogue performs output-side work such as bias and activation after a matrix product. Bias adds an offset; an activation applies a nonlinear function to the result. ReLU, the rectified linear unit, is the activation `max(0, x)`: it replaces negative values with zero and leaves nonnegative values unchanged. A fallback is a deliberately supported alternative for inputs outside a specialization's scope, not permission to hide unsupported behavior. Escalation means selecting the smallest layer that can solve the proven problem while keeping correctness and maintenance manageable.

**Objective** Choose the lowest-maintenance optimization layer that meets the requirement.

**Prerequisite bridge** The earlier lessons identify a causal limiter. Escalation asks which existing abstraction can remove that limiter while preserving correctness, portability, and operational simplicity.

**Why it matters** Custom code can win a microbenchmark yet lose end to end, duplicate a maintained library, or become incompatible with new shapes and software. The optimization layer is an engineering cost decision, not a badge of sophistication.

**Mechanism** Start with workload/batching and documented framework settings. Next choose an existing PyTorch operator or vendor library such as cuBLAS, cuDNN, CUB/CCCL, or a supported attention engine. Use compilation or graph replay when the missing value is fusion or launch reduction. Use CUTLASS when a library skeleton with a specialized epilogue fits. Handwrite CUDA only for a proven hotspot with semantics unsupported by maintained paths. Score each option by end-to-end contribution, expected mechanism, shape/dtype coverage, correctness burden, maintenance, portability, observability, fallback, and owner. The custom-kernel course then owns indexing, masking, synchronization, numerical tests, and version maintenance.
A higher-precision reference computes the intended formula with more numerical resolution than the tested paths. Lab 16 uses FP64, or 64-bit floating point, for both the matrix product and bias addition, then applies ReLU. `torch.addmm` expresses matrix multiplication plus an added input in one maintained operation; it can avoid the separately rounded BF16 intermediate created by `x @ weight + bias`. ReLU remains a separate requested operation, so this source does not guarantee a fused activation epilogue.

Unit roundoff describes the relative rounding scale for normal floating-point values; for BF16 round-to-nearest it is half the spacing above one. Cancellation occurs when large opposite-signed terms leave a small result, making relative error based only on that result misleading. For example, `1.125*3.625 - 4 = 0.078125`, but rounding the product 4.078125 to BF16 first gives 4.0625 and leaves 0.0625. Define an error allowance before comparing paths. Lab 16 combines absolute and relative tolerances with `u*(1+u)*abs(product)` for its intermediate-rounding allowance, where u is BF16 unit roundoff and product is the FP64 product. ReLU cannot amplify absolute error. This is the supplied bounded fixture's numerical policy, not a universal proof for all matrix lengths, overflow or arbitrary inputs. Check both implementations independently against the reference, then compare timing.

**Recall** Why should cuBLAS, cuDNN, SDPA, CUB, or CUTLASS be tested before handwritten CUDA?

**Mental model** Mature libraries encode hardware-specific algorithms and ongoing maintenance. Framework composition, compilation, library selection, or a custom epilogue often captures the value without owning an entire kernel.

**Practice labs**

- [Lab 09: Build a causal compilation experiment](reference/labs/09_capstone.md)
- [Lab 16: Decide whether a custom kernel is justified](reference/labs/16_library_first_decision.md)
