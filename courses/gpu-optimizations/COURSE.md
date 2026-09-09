# GPU Performance Optimization with PyTorch

This course teaches a repeatable optimization workflow: establish a trustworthy baseline, classify the limiter, select the smallest useful evidence, change one factor, and remeasure end to end.

## 1. Controlled GPU optimization

**Objective**

Define equivalent work, accepted outputs, and benchmark identity before tuning.

**How it works**

### What GPU optimization means

GPU optimization means making an application use less time, memory or energy for a specified amount of correct work. It does not mean making a utilization gauge as high as possible. This course teaches a repeatable way to investigate a slow workload, identify the stage that limits its result, and select a change that addresses that stage. GPU Fundamentals provides the prerequisite definitions of GPUs, tensors, kernels and streams.

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

### Execution and dependencies

GPU Fundamentals supplied execution, memory, timing, and roofline models. Optimization begins by turning those models into a controlled experiment whose inputs, outputs, and completion boundary remain fixed.

An optimization changes one part of the path from an input to a usable result. That path can include host preparation, data transfer, launches, GPU kernels, communication and output handling. A stage delays the result when later required work must wait for it. A stage that overlaps independent work may consume resources without adding its full duration to completion time.

The comparison becomes meaningful only when the baseline and candidate perform equivalent work. Shapes, dtypes, seeds, sequence lengths, batch semantics and output tolerances define that work. Warm-up, compilation state, repetitions and environment define the conditions under which it runs. Repeated samples show normal variation; the best sample alone can hide it.

The first explanation to test should connect a resource to an observed delay. Arithmetic demand can limit compute; repeated transfers can limit data movement; slow submission can leave the device waiting; communication or input preparation can delay dependent work. These delays fall into compute, memory/data movement, host/launch, communication, or input/storage limits. Memory capacity answers whether the workload fits at all. Divergence, layout, fragmentation and imbalance describe mechanisms that can produce these constraints, rather than interchangeable names for a bottleneck.

A disconfirming control is a comparison designed to show that an explanation could be wrong. If input preparation appears to starve the GPU, repeat equivalent device work with inputs already resident on it. If the idle gaps remain, input preparation alone does not explain them. The control narrows the cause; it does not replace the full application measurement.

A useful hypothesis predicts both supporting evidence and a control that could disprove it. For example, if input preparation is the cause, starting with resident inputs should remove the associated wait. A high utilization reading or a long kernel by itself cannot make that causal connection.

Amdahl's law limits the possible overall benefit. If a stage occupies 10 percent of serial latency, eliminating it leaves the other 90 percent. Total latency falls by at most 10 percent, giving a maximum speedup of about 1.11×. Improving a small stage cannot remove time spent elsewhere.

Most false speedups come from changing the workload, omitting required synchronization, warming only one candidate, or accepting a numerically different result. A useful result must also matter on the end-to-end critical path.

### Try a small example

Assume a request spends 6 milliseconds preparing data on the CPU and 4 milliseconds executing GPU work, with no overlap. Halving the GPU work yields 8 milliseconds overall, not 5. If preparation of the next batch can safely overlap the current batch's device work, steady-state throughput may instead be limited by the slower stage; first-request latency and buffer ownership still need separate checks. Draw both timelines before choosing a technique.

**Practice labs**

- [Lab 01: Choose a valid GPU timing boundary](reference/labs/01_timing_basics.md)

**Mental model**

A benchmark is a controlled experiment. Input shapes, dtypes, seeds, compilation state, hardware allocation, and correctness criteria are controlled variables; the selected optimization is the independent variable.

## 2. Asynchronous performance measurement

**Objective**

Choose CUDA events, synchronized wall time, or profiler timelines for the question being asked.

**How it works**

GPU submission is asynchronous: a CPU call can finish after requesting work but before the GPU completes it. Wall-clock timing measures elapsed real time between host timestamps. CUDA-event timing measures the interval between markers reached on the device, which can include dependencies, idle gaps and other effects inside that interval; it is not simply a sum of active kernel durations. Synchronization makes the required completion boundary explicit.

Warm-up runs prepare caches, compilation and library choices before steady-state measurement. Lazy initialization defers setup until first use; autotuning tries candidate implementations to choose one. Repeated samples expose variation: the median is the middle ordered value, or the average of the two middle values for an even sample count. A percentile such as p95 marks a value at or below which approximately 95 percent of observations fall. A short isolated kernel and a complete application need different timing boundaries, even when both report milliseconds.

The experiment contract names the completion boundary. This lesson chooses a clock and synchronization placement that actually measures that boundary.

A GPU operation has two relevant moments: the CPU submits it, and the device finishes the required work. A host timer stopped at the first moment measures submission. To measure a usable result, the stopping point must include the second moment and any other dependencies in the request.

CUDA events mark progress on the device. Place a timing-enabled start event before the operation and an end event after it in the same dependency chain. Once the end event completes, their elapsed time describes that device interval. The interval can include waits or idle gaps between the markers; it is not necessarily the sum of active kernel durations. With several streams, dependencies must join all work included in the measurement. An end event in one stream does not automatically cover unrelated work in another.

For an end-to-end iteration, a CPU clock surrounds the full request and stops after its declared completion. This includes required host work and transfers as well as device work. Warm-up precedes steady-state measurement because first use can trigger module loading, allocator growth, compilation and autotuning. If startup is the question, those costs instead belong inside the measurement. Retaining individual samples makes medians and slow tails visible.

Reading a CUDA scalar with `.item()` is a small operation with a significant ordering effect: its value must reach the CPU before the call can return. A reduction such as `square().mean()` first computes the scalar on the device; reading it then adds a host wait. Frequent logging can therefore serialize a loop. Where the application permits it, detached device scalars can be retained and reported together later. Detaching removes an autograd connection; it neither moves data to the CPU nor waits for completion. The final read still belongs in timing when the application requires it.

CUDA submission is asynchronous. A CPU timer can report only enqueue cost, a CUDA event can report elapsed work in a stream, and synchronized wall time can include host and dependency cost. These are different metrics, not contradictory answers.

**Practice labs**

- [Lab 01: Choose a valid GPU timing boundary](reference/labs/01_timing_basics.md)
- [Lab 02: Remove unnecessary per-step host synchronization](reference/labs/02_sync_trap.md)

**Mental model**

GPU work is queued asynchronously. CUDA events measure time on a device stream, while synchronized wall time includes host and boundary costs.

## 3. Performance evidence and profiling

**Objective**

Escalate from application symptoms to the narrowest tool that answers the next question.

**How it works**

A benchmark measures how long work takes or how much work completes; a profiler records where execution spends time; monitoring samples system state. A trace records events, often displayed as a timeline. A hardware counter measures activity such as bytes moved or instructions executed. NVIDIA Nsight Systems follows the application timeline across CPU work, GPU launches and communication. Nsight Compute examines a selected kernel's hardware behavior. PyTorch Profiler relates framework operations to CPU and device activity.

Instrumentation adds observation work to the program. NVIDIA Tools Extension (NVTX) ranges are named annotations that help identify a chosen phase in a trace. Self time excludes an event's recorded children; inclusive time includes them, so summing overlapping rows can double-count work. Collecting many counters may require replay—rerunning the measured work—which is why a profile explains performance but is not automatically an unbiased benchmark.

Trustworthy timing proves that a slowdown exists but rarely explains it. Tool selection should follow a hypothesis and move from broad semantic context toward a single kernel only when the evidence requires it.

A useful profile connects the code that requested work to the activity that completed it. Begin with that relationship, then ask for more detail only where the trace leaves an unanswered question.

### Choose the view that answers the question

PyTorch Profiler connects Python operators, shapes, allocations, and scheduled training steps; short active windows and semantic NVTX ranges keep traces interpretable. Nsight Systems shows CPU threads, CUDA application programming interface (API) calls, kernels, copies, collectives, and gaps on a common timeline; inspect the timeline before aggregate tables. Nsight Compute replays selected kernels to collect instruction, memory, scheduler, and roofline metrics; replay overhead means its run is not acceptance timing. `nvidia-smi` and DCGM provide device state, NVIDIA Collective Communications Library (NCCL) Tests isolate collectives, `nvbandwidth` isolates supported transfer paths, and serving load tools own request-level distributions. Tool availability and counter permission belong to the cluster owner; the learner verifies them inside an allocation and fails clearly if a required scope is unavailable.

For an initial tool map, use the [Diagnostic tooling setup](reference/tooling-setup.md) guide: it separates device health, continuous telemetry, framework attribution, system timelines, kernel counters and load generation. It also explains protected DCGM Exporter/Prometheus collection, counter contention, NCCL Tests versus nvbandwidth and standardized benchmark context. Reading these interfaces is not permission to reconfigure cluster monitoring or networking. Use the cheapest observation that can distinguish your current hypothesis, then narrow the scope before collecting expensive kernel details.

### Follow a submitted operation onto the device

Read an NVTX range first as a host annotation: it names the code that submitted work, not necessarily the interval during which the GPU executed that work. Follow CUDA API correlation to the corresponding copy or kernel on its stream, then inspect the dependency that delays the next operation. A blank region on one selected stream is only an absence of captured activity there. Other streams, processes, copy engines or uncaptured work may still be active. Likewise, a continuous kernel bar does not establish that all streaming multiprocessors are busy or that any compute pipeline reaches peak throughput. Use qualified hardware metrics when that is the question.

### Account for what the capture includes and changes

Nsight Systems can retain PyTorch context through explicit NVTX and supported PyTorch annotation options. Its visibility depends on the selected trace domains, processes and permissions; it is not an unlimited view of the machine. A nearby memory-unmapping call is a hypothesis for a host delay, not causal proof. Match the blocked API, allocation lifetime and downstream wait, make one controlled change, then repeat the trace. Keep acceptance timing in a separate unprofiled run. Nsight Compute replay, cache control and clock control can change a kernel's conditions; do not compare its duration directly with application timing without accounting for those settings.

Capturing every counter creates huge, perturbing traces without answering the question. Engineers also confuse monitoring, profiling, and benchmarking, or treat unavailable privileged counters as a reason to guess.

**Practice labs**

- [Lab 07: Build a readable profiler map of a workload](reference/labs/07_profile_workload.md)
- [Lab 14: Diagnose synchronization, launch, memory, and compute limits](reference/labs/14_profiler_bottlenecks.md)

**Mental model**

PyTorch Profiler connects framework operators to device work; Nsight Systems exposes CPU, runtime, kernels, communication, and gaps; Nsight Compute diagnoses a selected kernel.

## 4. Submission overhead and kernel fusion

**Objective**

Recognize launch-bound workloads and reduce unnecessary dispatch.

**How it works**

Eager execution asks the framework to perform operations as the program reaches them. Dispatch is the process of selecting and submitting an implementation; dispatch overhead is the CPU work surrounding the useful device computation. Compilation analyzes a program region and generates an executable implementation. Fusion combines several operations into fewer kernels, allowing some intermediate values to remain in registers instead of being written to separate tensors. Vectorization or batching expresses work on many values together rather than issuing a separate operation for each value.

For example, multiplying, adding and activating an array can launch three kernels in eager execution but may become one fused kernel. A compiler guard checks an assumption such as shape or dtype; a graph break ends a captured compiler region when code cannot be represented there. Aliasing means tensors share storage, and mutation changes existing values. Compilation and fusion are distinct from CUDA Graph replay, introduced next.

A Systems timeline exposes gaps and many short kernels. This lesson chooses semantics-preserving ways to submit fewer, larger pieces of work.

In eager execution, the CPU repeatedly selects and submits implementations as it reaches tensor operations. If each GPU kernel is very short, the device may finish one before the CPU submits the next. Much of the elapsed time can then come from dispatch and launch rather than arithmetic.

Several changes address different parts of this delay. Batching independent items or vectorizing a Python loop expresses more work per call. Existing fused operators combine calculations that would otherwise require several kernels. Fusion can keep intermediate values inside a kernel, removing both launches and the writes and rereads of temporary tensors. Removing an unnecessary `.item()` avoids a different cost: waiting for a device value on the host.

An activation illustrates work that can sometimes be fused with nearby arithmetic. SiLU, the sigmoid linear unit, computes `x/(1+exp(-x))`; tanh maps real values smoothly between -1 and 1. Both return zero for an input of zero. A multiply, add and activation may become one generated kernel if the compiler can preserve the required values and operation semantics.

`torch.compile` analyzes an operation region and may generate fused work or reduce Python overhead. Shape guards check assumptions used by that implementation. Unsupported operations can create graph breaks; changing shapes or assumptions can require other compiled variants or fallbacks. Aliasing, mutation, numerical order and reuse can also prevent a proposed fusion. Cold compilation and cache behavior are separate costs from steady execution.

`fullgraph=True` requires one compiler graph for the selected function and raises an error for an unsupported graph break. That graph represents operations and dependencies; it may still generate several CUDA kernels. Only the emitted work shows whether the intended launches and intermediate traffic were actually removed.

H100 can finish small elementwise kernels faster than Python and the runtime can dispatch them. The symptom is a launch-dense timeline with low work per launch, not merely low average utilization.

**Practice labs**

- [Lab 03: Compare eager and compiled pointwise execution](reference/labs/03_compile_fusion.md)

**Mental model**

Many tiny eager operations repeatedly cross Python, dispatcher, and CUDA-launch boundaries. Fusion, compilation, batching, or graphs can amortize those costs.

## 5. Reusable GPU execution plans

**Objective**

Identify graph-safe regions and measure replay benefits and constraints.

**How it works**

A CUDA Graph is a reusable execution plan for GPU operations and the dependencies between them. Operations, such as kernels or memory copies, are nodes; dependency edges specify which work must finish before other work can start. The ordinary plan is a directed acyclic graph: its dependency arrows do not form a cycle. Instead of rebuilding and submitting each operation individually on every iteration, the application prepares a graph and launches the prepared plan again. Capture records compatible submitted work into a graph; replay executes that recorded plan, not a saved set of numerical answers.

For example, a multiply → add → activation sequence can be replayed through one graph launch while still containing three kernels. Graph replay reduces repeated submission and setup work; kernel fusion combines operations within a kernel, while compilation translates a program representation into executable work. These are separate transformations.

Instantiation prepares a graph as an executable plan. A graph-private memory pool retains storage used by captured work. A shape bucket groups inputs handled by one supported capture; a fallback executes inputs outside that capture's contract through another supported path. Buckets and fallback routing are extensions to this lesson's fixed-shape baseline, not requirements of every CUDA Graph application.

Compilation can change and fuse a graph of operations. CUDA Graphs solve a different problem: they record an already chosen stable sequence of CUDA work and replay its submission cheaply.

A graph replay starts from an execution plan prepared earlier. First, warm-up establishes the allocations, library plans and compiled code needed by the workload. Capture then records compatible device operations and their dependencies. Instantiation prepares that recorded graph for execution. Later replays launch the prepared plan with less repeated host submission work.

The plan refers to storage as well as operations. In this PyTorch workflow, captured allocations use graph-private memory pools, and referenced virtual addresses must remain valid for replay. A common arrangement copies new values into stable input buffers, replays the graph, and consumes results from stable output buffers. The values can change even though the buffers and execution structure stay compatible.

Shapes, referenced storage, control flow, stream dependencies and participating operations form this capture's contract. An input outside that contract cannot simply be substituted and assumed safe. The course's dynamic-input extension handles a bounded set of shape buckets with separate compatible captures and sends other inputs through an eager fallback.

Graph replay does not erase preparation costs or free memory retained by graph pools. Compilation time, capture time, steady replay time, retained memory and bucket hit/fallback rates therefore describe different consequences. A compiled region may be captured, but compilation determines its implementation while the CUDA Graph records how the resulting device work is submitted.

Graph capture is powerful for repeated fixed work but fragile around dynamic allocation, changing addresses, CPU decisions, synchronization, and unsupported operations. Confusing compile, capture, and replay hides cold cost and fallback behavior.

The dependency structure is a directed acyclic graph (DAG): nodes are operations, edges are prerequisites, and no chain loops back to its own unfinished start. Replay repeats this finite plan.

**Practice labs**

- [Lab 04: Replay a fixed-shape workload with a CUDA Graph](reference/labs/04_cuda_graphs.md)

**Mental model**

CUDA Graphs capture a repeated device-work DAG and replay it with lower launch overhead. This PyTorch exercise requires compatible shapes, stable referenced storage and capture-safe operations; these are its capture contract, not a claim that the broader CUDA API forbids graph updates.

## 6. Input readiness and transfer overlap

**Objective**

Detect and repair host-side starvation without hiding it behind device averages.

**How it works**

An input pipeline turns source data into batches ready for GPU computation. It can read files, decode records, transform values, combine examples and transfer the resulting tensors. PyTorch's DataLoader coordinates fetching and batching; workers perform preparation, and collation combines individual examples into the batch structure. Prefetch prepares future batches before they are requested, while queue depth describes how many are waiting. Persistent workers keep their processes alive between passes through the data, called epochs.

Tokenization converts text into model token IDs; augmentation transforms examples while preserving the intended task. A pinned host buffer supports suitable asynchronous host-to-device (H2D) transfers. A resident-input control starts with data already on the GPU; a synthetic-input control generates data without the real storage path. Comparing these controls helps locate starvation, when the next batch is not ready, without assuming every idle gap is caused by slow GPU kernels.

Even perfect GPU kernels wait if the next batch is not ready. Model the loader as producers filling a bounded queue for a GPU consumer rather than as a single “data time” number.

Follow a batch through the producer stages: read the source, decode it, tokenize or augment it if needed, collate examples, prepare host storage and copy tensors to the GPU. The GPU consumer can begin only when its input is ready. If preparation is slower than consumption, the ready queue empties and the GPU waits, even when its kernels are efficient.

Workers can prepare several batches concurrently, and prefetch lets them work ahead. This absorbs some variation but uses memory and cannot sustain a producer that is consistently too slow. Persistent workers avoid repeated process startup between epochs when that startup matters. Extra workers can instead increase storage contention or CPU scheduling costs. The pipeline must preserve distributed sampling, epoch/seed behavior, sample order where required and exact sample counts.

Pinned memory keeps host pages resident so suitable direct memory access (DMA) transfers can use their stable backing without first staging pageable data. Pinning has a cost and consumes host resources. A preallocated or loader-managed pinning path may amortize that cost; pinning each batch in the hot loop can erase the saving.

A nonblocking copy lets the CPU continue, but a copy and kernel in the same stream still run in order. Actual copy/compute overlap needs separate streams, independent work, suitable hardware and explicit dependencies. A device buffer cannot be read until its input copy finishes or overwritten until its computation finishes. The host source must also remain unchanged until the copy stops reading it. Keeping a tensor alive prevents deallocation, not premature in-place modification.

Batch-ready timestamps, queue depth, idle gaps and stage durations distinguish these waits. Synthetic inputs remove the real storage path; resident inputs also remove host preparation and transfer. Their different effects help locate the delay. Worker prefetch, asynchronous host submission and device overlap solve different problems. Data Loading Library (DALI) or another accelerated pipeline is useful only when it addresses the demonstrated stage while preserving the same input semantics.

Increasing workers can move the bottleneck to storage, CPU scheduling, memory, interprocess transfer, or shared filesystem contention. It can also duplicate or reorder samples and change the training workload.

**Practice labs**

- [Lab 05: Locate waiting in a DataLoader pipeline](reference/labs/05_input_pipeline.md)
- [Lab 19: Overlap H2D copies with a bounded input ring](reference/labs/19_h2d_pipeline.md)

**Mental model**

Reading, decoding, preprocessing, batching, pinning, and transfer form a producer pipeline. The device stalls when the next batch is not ready. NVIDIA DALI is one optional accelerated data-pipeline implementation, not evidence that storage, decode, or transfer is the current limiter.

## 7. Tensor layout and memory traffic

**Objective**

Reduce layout conversions, non-coalesced access, and materialized intermediates.

**How it works**

A tensor's layout maps logical indices to storage addresses. A producer creates values and a consumer uses them; an intermediate is the producer's result passed between operations. Materializing an intermediate means writing it into an allocation instead of keeping it within an executing kernel. A traffic ledger counts those reads, writes and copies. An implicit conversion happens inside a framework or library call when its preferred layout differs from the supplied one.

For example, a transpose can initially be a cheap view, but the next operator may need a physical copy. An epilogue is work applied to an operation's result, such as adding bias after matrix multiplication; fusing it may avoid an intermediate allocation. Aliasing or mutation can require preserving shared storage, so a compiler's alias guard checks assumptions before transforming the program. This lesson extends single-access coalescing to the interfaces between several operators.

Fundamentals Lab 04 established strides, packing cost and break-even reuse for one access pattern. Keep that result as a prerequisite. This lesson asks how several producers and consumers agree on layouts, where implicit conversions appear, and whether fusion can remove an entire intermediate.

Consider two operators where the second consumes the first one's output. In an unfused implementation, the first writes an intermediate tensor and the second reads it. Some reads may hit a cache, but the intermediate still creates storage and memory requests. A fused implementation may pass those values within a kernel and avoid materializing that tensor.

Layout adds another possible cost between the operators. A producer may return a view with no copy, while the consumer requires a different physical arrangement and materializes one internally. An operation-by-operation traffic ledger makes these costs visible: input reads, output writes, temporary writes and rereads, packing copies and later reuse. Kernel and allocation evidence can then distinguish a real copy from a metadata-only view.

Packing once pays an initial conversion cost for cheaper repeated accesses. It breaks even when `pack_cost < reuse_count × (strided_cost - packed_cost)`: the total saving across later uses must exceed the copy. The shape alone cannot establish that saving; strides and the consumer's lane mapping determine the addresses accessed.

Fusion and conversion must also preserve storage relationships. Aliases can expose the same values through several tensors, mutation can make old values significant, and lifetimes determine which results must coexist. Compiler guards or graph breaks may prevent an intended transformation for those reasons. The resulting kernels and surviving intermediates reveal what changed in practice.

Logical tensor bytes can understate physical traffic. Temporary materialization, layout copies, cache-line over-fetch, and repeated reads consume high-bandwidth memory (HBM) bandwidth and allocation capacity even when the Python expression looks compact.

A persisting-L2 (level-two cache) access policy identifies a memory range whose accesses should receive preferential retention in the shared L2 cache on supported configurations. It is a reuse hint, not a guarantee that the range stays resident. Giving a proven reusable range that preference competes with other useful data and requires CUDA-level evidence.

**Practice labs**

- [Lab 03: Compare eager and compiled pointwise execution](reference/labs/03_compile_fusion.md)
- [Lab 10: Survey library behavior across shapes and dtypes](reference/labs/10_shape_precision.md)

**Mental model**

A tensor view changes how existing storage is addressed; a materialized copy moves data. Optimize the producer and consumer together so layout conversion and intermediate traffic are included in the decision.

## 8. Memory allocation and ownership

**Objective**

Distinguish allocated, reserved, live, cached, fragmented, and peak memory.

**How it works**

An allocator manages requests for memory. PyTorch's caching allocator can retain blocks after tensors stop using them so later allocations avoid repeated device-allocation overhead. Allocated bytes track live tensor storage; reserved bytes also include storage held by the allocator for reuse. Peak memory is the largest amount observed during a specified interval. A workspace is temporary storage an operation needs in addition to its inputs and outputs.

Fragmentation means free storage is divided into pieces that cannot satisfy a particular request efficiently. Size classes group allocation sizes; an inactive split is an unused part of a split allocation segment. A memory snapshot records the allocator's state for inspection. In-place operations update existing storage, but aliases and autograd—the system that records operations for differentiation—may still need the old values. Reserved memory exceeding allocated memory is therefore not by itself a leak, and releasing cached blocks cannot free live tensors.

An output pipeline moves device results into host buffers and hands completed results to a consumer, such as an encoder or storage client. A buffer pool reuses a bounded set of allocations. Backpressure is the wait imposed when every slot is still owned by a transfer or consumer; it prevents unbounded queued results. A future represents eventual completion or failure of a worker task. This lesson applies allocator lifetime to both GPU and CPU ownership, after Lesson 6 established input-stream dependencies.

The traffic ledger counts bytes moved. A lifetime ledger asks when each allocation must coexist, which determines whether the workload fits and whether allocator state is being mistaken for a leak.

An allocation contributes to memory demand for as long as some operation needs it. Inputs, outputs, workspaces, communication buffers and compiler or graph pools can overlap in lifetime. Peak demand occurs where the largest set must coexist, so shortening one long-lived allocation can matter more than reducing several small ones.

When a tensor is no longer needed, PyTorch's caching allocator can keep its storage for a later allocation. `memory_allocated` describes live tensor storage known to the allocator; `memory_reserved` includes both active storage and allocator-held capacity. They are overlapping quantities, not two memory bills to add together. High reserved memory after tensors are freed can therefore be normal reuse behavior.

Free capacity is not always available in a useful shape. Inactive split blocks and mismatched size classes can prevent a new request from fitting efficiently. Allocation retries and memory snapshots help distinguish this pressure from genuinely live tensors. `empty_cache()` can release eligible unused cached storage, but it cannot release a tensor that still has a live reference. In-place changes are safe only when aliases and autograd do not require the previous values. Training-specific checkpointing and state sharding address different causes of long lifetimes.

An output pipeline extends lifetime beyond the GPU kernel. A device-to-host copy must finish before the CPU reads its destination. The CPU consumer must then finish before that host buffer is reused. The device source must remain valid until the copy stops reading it. A bounded pool returns a slot only after these owners have released it; when no slot is free, backpressure waits instead of allocating an unbounded queue.

Capacity and bandwidth are different problems. Reducing reserved memory may not speed a step, while shortening one large live interval can enable a larger useful batch even with unchanged kernel time.

CUDA memory pools and stream-ordered allocation make reuse and ordering explicit below the framework layer; they do not reduce live tensor bytes.

**Practice labs**

- [Lab 12: Follow live, cached, and released GPU memory](reference/labs/12_allocator_lifetime.md)
- [Lab 20: Drain a bounded D2H output pipeline](reference/labs/20_d2h_pipeline.md)

**Mental model**

Live tensor storage determines the irreducible memory demand. Allocation reuse can avoid repeated system work, but safe reuse still follows tensor ownership and stream completion.

## 9. Efficient numerical-library execution

**Objective**

Align matrix dimensions, batches, and dtypes with efficient library kernels.

**How it works**

A numerical library selects an implementation using the operation, dimensions, layout, numerical format and hardware. General matrix multiplication (GEMM) multiplies an M-by-K matrix by a K-by-N matrix to produce M-by-N values: M and N are output dimensions, while K is the dimension summed over. A tile is a smaller rectangular piece handled cooperatively by the implementation. Dispatch chooses the implementation; a cast converts values between numerical formats.

Padding adds unused or neutral values to reach a supported or efficient shape. It is valid only when the original result is preserved, such as adding matching zero entries along K. Shape alignment means dimensions suit a kernel's tile sizes; pointer alignment instead concerns memory addresses. Tensor Core hardware supports particular arithmetic paths, not every possible shape or dtype. The goal is to preserve useful work while choosing an efficient library path, not to enlarge the task until a benchmark looks faster.

Layout determines how bytes arrive; shape and precision determine which maintained library kernel can consume them and whether Tensor Core tiles are used efficiently.

A library receives an operation together with its shapes, dtypes and layout, then selects an implementation for the available hardware. A matrix kernel divides the work into tiles. Dimensions that leave small partial tiles may use the selected implementation inefficiently, while a nearby larger shape may select a different kernel or fit its tiles better.

Padding can exploit that behavior, but it adds work. For a matrix product, zero padding along the reduction dimension preserves the original sum when both operands are padded consistently. The kernel still multiplies the extra entries. A faster kernel is useful only if the complete padded calculation, including preparation and result handling, finishes sooner for the same useful output. Useful floating-point operations (FLOPs) or tokens and padded FLOPs or tokens must therefore remain separate counts.

Precision affects both bytes and eligible arithmetic. Stored inputs may use one format, multiplication another internal mode, accumulation a wider format and outputs a final rounded format. Casts and scaling add operations of their own. A dtype change or aligned dimension alone does not prove Tensor Core execution; library dispatch and kernel evidence establish the actual path.

Representative shapes, batches, dtypes and layouts expose these choices without changing model semantics. Alignment requirements depend on the library, version and operation rather than one universal multiple. Time, memory and output error describe the result together. Attention-specific backend selection and scaled dot-product attention (SDPA) experiments build on this model in the Inference course.

A mathematically smaller shape can run slower when it selects a poor kernel, wastes a final tile, or prevents fusion. Padding can improve kernel efficiency while increasing total work, so comparisons must normalize useful work.

**Practice labs**

- [Lab 10: Survey library behavior across shapes and dtypes](reference/labs/10_shape_precision.md)

**Mental model**

Library dispatch depends on dtype, dimensions, strides, alignment, and hardware. A small amount of shape padding can improve utilization, but excess padding wastes work and memory.

## 10. Parallel imbalance and completion tails

**Objective**

Distinguish lane divergence, uneven blocks, uneven ranks, and the final partial scheduling wave.

**How it works**

Load imbalance means parallel tasks take different amounts of time, leaving some workers idle while others finish. Makespan is the elapsed time until all tasks are done. Contention occurs when workers compete for the same resource. A partial grid wave occurs when too few blocks remain to fill the available resident-block slots. That scheduling tail differs from tail latency, which describes slow observations in a distribution such as p95.

Warp divergence is another level: lanes within one warp follow different paths. Shared memory is divided into independently serviced banks. A bank conflict occurs when accesses to different words compete for the same bank's service; supported same-word broadcasts are different. A persistent kernel keeps workers active to take more tasks instead of launching a fresh grid for each batch of work. These concepts all involve unused capacity, but their causes and fixes differ. Diagnose the level of imbalance before changing task sizes, grouping or scheduling.

The local lessons have covered launch overhead, data movement, allocation, shapes and precision. Now distinguish idle lanes, uneven blocks and partial grid waves before introducing distributed scaling. Rank skew is a preview of the same completion-time problem across workers in Lesson 11.

Parallel work finishes when its last required task finishes. To explain unused capacity near that point, first identify which unit is uneven: lanes within a warp, blocks within a grid, or processes across a distributed job.

Inside a warp, divergent branches run with different active-lane masks. Lanes on one path do not perform useful work while the other path's instructions execute. Across blocks, data-dependent work, contention or unequal tiles can instead give whole blocks different durations. These are separate causes even when both leave execution capacity unused.

A partial final wave needs no unequal durations at all. Suppose earlier waves fill all available resident-block slots, but only a few blocks remain. Those blocks still have to run, leaving other slots unused until the grid completes. Comparing the grid size with concurrent block capacity explains this scheduling tail. A uniform-work control helps distinguish it from blocks that are individually slow.

Across ranks, one worker may reach a collective later than its peers. Their apparent communication time then includes waiting for its arrival. Per-rank timelines separate that skew from the transfer itself. Active-lane metrics, block-work distributions and rank timelines answer different questions and should not substitute for one another. Bank conflicts, registers, spills and occupancy can explain a kernel's behavior; implementing their low-level repair belongs to Custom CUDA Kernels.

These problems can all leave execution resources idle, but they arise at different scheduling levels. A branch rewrite cannot remove a one-block tail, and adding blocks cannot equalize variable per-rank data.

**Practice labs**

- [Lab 15: Distinguish task imbalance from partial grid waves](reference/labs/15_tail_load_balance.md)

**Mental model**

Parallel completion is set by the slowest participating unit. Work distributions, bucket sizes, expert routing, and grid size create different kinds of tails.

## 11. GPU communication paths and performance

**Objective**

Establish a correct message-size baseline, identify the actual transport, test one job-local change, and decide whether it deserves an application trial.

**How it works**

Communication tuning means reducing the time that correct data exchange adds to a workload. NVIDIA Collective Communications Library (NCCL) implements GPU communication operations such as all-reduce. It chooses among available paths and algorithms; it does not create the physical network. InfiniBand is a switched network architecture, RDMA over Converged Ethernet (RoCE) supplies remote direct memory access (RDMA) over Ethernet, and GPUDirect RDMA permits a supported network interface card (NIC) to access GPU memory directly. Fundamentals explains those layers; here the question is which path a real job selected and whether changing one choice helps.

A transport is the software/data-transfer path, such as NCCL's Socket or IB transport. The IB name also covers RoCE through the verbs interface; a log containing IB does not necessarily mean an InfiniBand physical link. An algorithm organizes collective exchanges, while a protocol controls how NCCL chunks and synchronizes those exchanges. A queue pair (QP) is an adapter work-queue pair; increasing QPs can spread network flows, but also adds overhead. These are distinct experimental factors, not a checklist of switches to enable together.

Complete local timing, profiling and load-balance lessons first. Revisit Fundamentals Lesson 12 for networking layers and collective semantics. Use this lesson before Lesson 12's scaling/overlap experiment so a degraded transport is not mistaken for an application scheduling problem.

A collective needs both a usable data path and agreement among its participating processes. Physical connectivity makes a path possible; communicator initialization selects how this job will use it. Performance depends on what happens after that selection.

### Qualify the path before measuring candidates

Topology and port-state queries describe the available attachment and network, while NCCL startup logs describe what a particular communicator selected. GPU/NIC proximity, active ports, payload transport and GPU-memory registration are different observations. IP interfaces and RDMA HCAs also use different name spaces. Socket traffic can serve setup even when payloads use RDMA; `NET/IB` alone therefore cannot establish GPUDirect RDMA. Registration can use a supported peer-memory or DMA-BUF (Linux’s framework for sharing buffers between device drivers) path, so a missing `nvidia-peermem` module is not conclusive.

Device and topology queries, communicator startup logs and collective benchmark rows answer different questions: what paths exist, what this job selected and how its collective behaved. The NCCL library loaded by a standalone benchmark may differ from the one used by PyTorch.

### Read a complete size curve, not its best row

An asynchronous collective returns a Work handle representing pending work. For this CUDA/NCCL path, `work.wait()` joins completion to the current CUDA stream; it does not by itself mean the CPU has waited for device completion. A host timing observation still needs the relevant event or device synchronization.

The logical payload is the tensor size being reduced. Dividing that size by elapsed time gives algorithmic bandwidth. NCCL Tests also reports a collective-specific normalized bus bandwidth. For all-reduce with N ranks and logical payload S bytes, `algbw = S / time_seconds / 1e9` and `busbw = algbw × 2(N−1)/N`. The second expression is a normalization for comparing collective work; it is not a hardware traffic counter. With two ranks the multiplier is one, so equal algbw and busbw are expected. They do not establish saturation. Keep MiB (2²⁰ bytes), GB/s (10⁹ bytes/s), and link rates in Gb/s (bits/s) distinct.

### Why a comparison needs a fresh communicator

Automatic selection is the baseline for identifying a useful intervention. Transport, GPU-memory access, collective algorithm and QP count change different parts of execution. A comparison is meaningful only when the selected path is eligible and all other work, ranks and transport conditions remain fixed. Communicator initialization consumes these settings, so a fresh process isolates a candidate from an already initialized baseline. Site interface/plugin configuration is part of the environment contract; inherited experimental overrides would confound the comparison. More network flows can improve distribution while adding latency and adapter overhead, so extra configuration is not evidence of improvement.

MPI, the Message Passing Interface, coordinates cooperating processes. In an MPI-enabled NCCL benchmark, MPI coordinates ranks while NCCL performs the GPU collective.

Two fast GPUs can spend most of a step waiting for communication. Wrong placement, a slow link, network congestion or one late rank can overwhelm a small algorithmic improvement. A synthetic all-reduce curve isolates part of the problem; only a subsequent application measurement establishes useful benefit.

**Practice labs**

- [Lab 00: Establish the optimization platform contract](reference/labs/00_cluster_preflight.md)
- [Lab 17: Measure a two-node NCCL message-size curve](reference/labs/17_nccl_transport_sweep.md)
- [Lab 18: Run and interpret NVIDIA NCCL Tests on Slurm](reference/labs/18_nccl_tests_report.md)

**Mental model**

Separate four claims: the platform supports a path; this job selected it; the collective performs correctly and quickly on it; the application benefits. Each claim needs its own evidence. Neither a product name nor a high utilization percentage establishes all four. Begin with the actual GPU → local attachment → NIC → fabric → remote NIC → GPU path, then examine operation semantics and timing.

## 12. Distributed scaling and communication overlap

**Objective**

Separate useful compute, communication, exposed communication, and synchronization imbalance.

**How it works**

Distributed optimization asks how work and communication combine across processes. A rank identifies a participating process. Data-parallel execution gives ranks different input work; in training, Distributed Data Parallel (DDP) keeps a model replica on each rank and combines gradients. Parameters are the model's adjustable values; loss is the numerical objective used to judge its output. A gradient describes how changing a parameter affects loss; backward computes gradients, and the optimizer uses them to update parameters. This short training example illustrates a general communication dependency rather than replacing the Training course.

A collective is a group communication operation. A bucket combines several values into one transfer, and becomes ready when those values have been produced. Communication is overlapped when independent computation progresses at the same time. Exposed communication is the part still delaying completion on the critical path—the chain of dependencies that determines total elapsed time. An asynchronous call returning early alone does not demonstrate useful overlap.

A measured single-GPU baseline, local imbalance diagnosis and Lesson 11's qualified communication path are prerequisites for useful scaling evidence. Distributed execution adds data partitioning, process placement and collective dependencies; one slow rank can now set the completion time for all ranks.

In fixed-work strong scaling, the same global task is split among more ranks. Compare its one-rank time T1 with its N-rank time TN: speedup is `T1/TN`, and efficiency is `T1/(N*TN)`. For two ranks TN is T2. Weak scaling asks a different question: each rank keeps the same local work, so adding ranks increases the global task.

DDP illustrates the dependencies. Each rank holds a model replica and processes its assigned input. DDP averages gradients across ranks before the dependent optimizer update. With equal-sized disjoint batches and the same local mean-loss convention, a global batch of 64 can be split into 32 examples per rank on two ranks. Giving each rank 64 would double the work. Initial state, data and optimizer settings must also agree; reduced-precision reduction order can still cause numerical differences.

Backward produces gradients progressively. A bucket groups gradients for communication and becomes ready when all its required gradients have been produced. A smaller bucket may become ready sooner, giving its collective more time to overlap remaining backward computation. It also creates more collectives and pays more fixed latency. A larger bucket amortizes that overhead but may start too late to hide its transfer. The optimizer waits for all gradients it needs, so any unfinished communication remains exposed on the critical path.

An asynchronous collective returns a `Work` handle while the operation is pending. Independent computation can proceed before joining the result. For ordinary CUDA/NVIDIA Collective Communications Library (NCCL) work, `work.wait()` establishes the dependency on the current CUDA stream; it does not universally mean that the CPU waited for every device operation. A host timing boundary still needs the relevant event or device synchronization. Buffers must remain valid, and ranks must preserve matching collective order.

A timeline connects bucket readiness, collective start and end, independent computation and the final consumer wait. It must establish actual overlap; early return from a call cannot do so. The slowest rank sets group completion, and its late arrival must be separated from time spent transferring data.

`async_op=True` or a non-default stream proves only asynchronous submission. The collective still delays the step if consumers wait immediately, if it contends with compute, or if the slowest rank arrives late.

Non-uniform memory access (NUMA) means that a CPU’s access cost depends on which socket owns the memory. Placing communication buffers and processes near their GPU and network attachment can avoid extra host interconnect traffic; placement must be verified for the actual allocation.

**Practice labs**

- [Lab 08: Compare one GPU with two-node DDP](reference/labs/08_distributed_scaling.md)
- [Lab 13: Test whether communication can overlap independent compute](reference/labs/13_collective_overlap.md)

**Mental model**

Distributed speedup depends on fixed work, per-rank work, collective volume, overlap, and the slowest rank. Communication hidden behind useful compute is not on the critical path. GPU/CPU/NIC NUMA placement and transport health are qualified before NCCL algorithm or protocol tuning.

## 13. Choosing an optimization layer

**Objective**

Choose the lowest-maintenance optimization layer that meets the requirement.

**How it works**

The framework, library, compiler and custom-kernel layers solve different parts of a GPU program. A framework such as PyTorch composes tensor operations. Libraries supply maintained implementations: cuBLAS provides linear algebra, cuDNN neural-network primitives, and CUB parallel building blocks such as reductions within CUDA Core Compute Libraries (CCCL). CUTLASS provides composable templates for matrix kernels. A compiler transforms source or an operation graph into executable code. A custom kernel is device code whose implementation and support contract you maintain.

A hotspot is a region responsible for a meaningful part of the measured cost. An epilogue performs output-side work such as bias and activation after a matrix product. Bias adds an offset; an activation applies a nonlinear function to the result. ReLU, the rectified linear unit, is the activation `max(0, x)`: it replaces negative values with zero and leaves nonnegative values unchanged. A fallback is a deliberately supported alternative for inputs outside a specialization's scope, not permission to hide unsupported behavior. Escalation means selecting the smallest layer that can solve the proven problem while keeping correctness and maintenance manageable.

The earlier lessons identify a causal limiter. Escalation asks which existing abstraction can remove that limiter while preserving correctness, portability, and operational simplicity.

The appropriate implementation layer follows from the missing capability. If unnecessary batching or framework work causes the delay, changing that work may solve it without replacing the kernel. If a maintained operator already expresses the desired calculation, PyTorch, cuBLAS, cuDNN, CUB/CCCL or a supported attention engine can provide an implementation with an existing support path.

When repeated submission or intermediates are the problem, compilation, fusion or CUDA Graph replay may address that specific cost. When a matrix product is already efficient but its output-side work needs specialization, a CUTLASS skeleton with a custom epilogue may be sufficient. Handwritten CUDA becomes a candidate when a meaningful hotspot remains and maintained paths do not express the required semantics efficiently.

Each option brings a scope: supported shapes and dtypes, numerical behavior, portability, observability, fallback behavior and an owner who maintains it. A microbenchmark gain contributes to the application only through the time it removes from the critical path. Correctness and maintenance effort therefore belong in the decision alongside elapsed time. The Custom CUDA course develops the additional indexing, masking, synchronization, testing and version responsibilities.

Numerical comparison needs an appropriate reference. A higher-precision calculation helps distinguish different valid rounding paths from a changed formula. Unit roundoff describes the relative rounding scale of normal floating-point values, but cancellation can make a small final result unusually sensitive to intermediate rounding.

For example, `1.125 * 3.625 - 4 = 0.078125` in exact arithmetic. Rounding the product to bfloat16 (BF16) first gives 4.0625, leaving 0.0625 after subtraction. The small final difference comes from a much larger intermediate. An acceptance rule based only on relative error in the small result can misclassify that rounding path; the rule must reflect the operation's actual rounding structure.

Custom code can win a microbenchmark yet lose end to end, duplicate a maintained library, or become incompatible with new shapes and software. The optimization layer is an engineering cost decision, not a badge of sophistication.

**Practice labs**

- [Lab 09: Build a causal compilation experiment](reference/labs/09_capstone.md)
- [Lab 16: Custom kernel decision making](reference/labs/16_library_first_decision.md)

**Mental model**

Mature libraries encode hardware-specific algorithms and ongoing maintenance. Framework composition, compilation, library selection, or a custom epilogue often captures the value without owning an entire kernel.
