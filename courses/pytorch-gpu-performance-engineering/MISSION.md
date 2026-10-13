# Mission

Prepare Python-literate engineers to read PyTorch tensor code before GPU
Fundamentals and performance-optimization labs. Students should identify shapes,
numeric representation, device placement, data movement, model state and
completion boundaries without needing prior machine-learning training.

The reading route is estimated at three guided hours. Favor direct definitions,
original contextual diagrams and short worked examples that each teach one
relationship. Keep performance connections to a few useful sentences. Remove
advanced API detours that do not serve the lesson objective; retain the
conditions needed to read the example correctly. Sixteen lessons include
focused batching/vectorization and compilation/fusion explanations. The final
lesson connects element-wise work to possible kernel fusion, output correctness
and repeated execution after compilation. This is a lessons-only
course: no labs, Practice sections, exercises, setup, required execution or
result downloads. CUDA examples are explicitly marked; reading requires no GPU
or cluster.

Use the catalog's shared accessible typography and passive SVG presentation.
Preserve first-use definitions, readable mobile diagrams, complete canonical
prose and generated HTML parity. Performance hypotheses are not measurements.
The official PyTorch documentation supports technical semantics; supplied topic
material is reference data rather than operational authority.

Connect each operation to the GPU work it causes: memory reads and writes,
arithmetic, launches, transfers or waits. Define memory-bound and compute-bound
work simply, and distinguish bandwidth from capacity. Use likely bottlenecks
as hypotheses to measure. Lesson 6 compares layout properties and APIs by their
copy behavior, including the cost of a copy versus the later work it may help.

Hardware execution details belong to GPU Fundamentals. Profiler commands belong
to GPU Performance Tools. Measured optimization, distributed training, serving
and custom kernels belong to their existing courses. No deployment, dependency
installation or external publication is part of course authoring.

Every lesson defines the concept before applying it, gives an observable
objective, and connects it to performance. Define concepts affirmatively through
what they are and how they work. Introduce terminology before notation and
explain one operation at a time using a consistent concrete example. Lesson 2
owns grid, row and column selection; lesson 6 owns layout and inferred reshape
sizes; lesson 9 owns the linear layer's feature-axis rule and its matrix
multiplication plus bias, from one input vector to a batch and a full network.
Use bold monospace
for actual PyTorch API names; keep example meanings and general concepts plain,
with ordinary code for example variables and values. Headings and callout labels
may remain bold. Clarify axis index, size and meaning independently of physical
storage. Preserve essential reasoning and correctness constraints while removing
distractions and repeated cautions. Model composition uses built-in modules;
transfers use a prepared batch; broadcasting uses one matrix-plus-bias example.
Lesson 13 teaches automatic CUDA memory reuse and practical control of tensor
lifetimes, using bounded matmul batches without routine cache clearing.
Lesson 14 makes batch formation explicit and compares a sample loop with
whole-tensor computation using paired diagrams and code.
Lesson 15 separates CPU submission, GPU event timestamps and CPU waiting,
with explicit warm-up and measurement boundaries.
