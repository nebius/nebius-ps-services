# Syllabus

1. A tensor is data with a description
2. Dimensions are indexed axes with sizes
3. Broadcasting reuses values across axes
4. Element-wise work and matrix multiplication differ
5. Reductions remove information and axes
6. Views change interpretation; copies move values
7. Dtype changes storage and numerical behavior
8. Device placement selects where work happens
9. A model is a sequence of tensor operations
10. Training saves information for a backward pass
11. Switching from training to validation and inference
12. Transfers are part of the input pipeline
13. Live tensors and reserved memory are different
14. Batching groups samples; vectorization processes the batch
15. Measure GPU time with CUDA events
16. torch.compile can combine GPU operations

Estimated guided hours: 3. Basic Python is the entry requirement; no PyTorch,
machine-learning, GPU or cluster experience is required. Lessons 1–7 own tensor
representation and operations; 8–11 connect them to execution and models; 12–16
develop performance-reading and optimization habits.

Read before GPU Fundamentals. GPU Performance Tools supplies optional command
reference context, not required tensor knowledge. Every lesson has an observable
objective, definition-first teaching, contextual diagrams, short examples that
each teach one relationship and a brief Performance connection. Exact PyTorch
API names use bold monospace; example variables and values use ordinary code;
application meanings stay plain. Headings and callout labels may remain bold.

Define concepts affirmatively and explain terminology before notation. Lesson 2
uses one grid example for indexing, slicing and reshaping. Lesson 6 adds
inferred sizes, axis reordering and a compact layout/copy comparison; lesson 9
connects input features, hidden neurons and output neurons to the linear layer's
feature-axis rule using a 4 → 2 → 1 network. Pair the neuron graph with a
linear-layer matrix view; explain per-layer weights/biases, row-batch notation
and how the task determines the output size. Lesson 1 distinguishes memory
capacity from bandwidth; lesson 4 defines memory-bound and compute-bound work.
Later performance connections apply these ideas alongside launch, transfer and
waiting costs, without treating an API name as proof of a bottleneck.
Lesson 7 compares seven floating-point encodings through their sign, exponent,
fraction and storage bits; retain the short FP32/BF16 example while explaining
FP8 variant limits, packed FP4 and scaling overhead.
Lesson 8 traces input copies and scalar readback in CPU/GPU memory columns,
then connects CPU waits and stream dependencies to useful concurrent work.

Lesson 11 separates training with per-epoch validation from inference, with a
diagram and commented code for each. Define targets and explain the independent
layer-behavior and gradient-recording settings before the examples.
Lesson 13 separates automatic block management from tensor lifetimes controlled
by the program. Explain cached storage, matmul batch handling, training state,
peak memory and OOM before presenting cache clearing as an occasional handoff.

Lesson 14 separates batch formation with torch.stack() from vectorized
computation, using side-by-side loop and batch diagrams with matching code.
Connect DataLoader batch_size to the earlier training loop. Lesson 15 uses a
function table, CPU/GPU timeline and one commented timer to explain asynchronous
submission, event timestamps, synchronization and warm-up. Lesson 16 finishes with a standalone torch.compile example: multiply by two,
then add three. Compare separate kernels with possible fusion, check eager
and compiled outputs, and explain compilation cost before repeated timing. Keep the conditions needed for each example, without unrelated
API catalogues or advanced setup. Broadcasting teaches one matrix-plus-bias
addition; reductions teach row means; transfers show prepare, copy and compute.

There are no labs, Practice sections, assessments, runtime setup or required
execution. Profiling commands, hardware details, distributed execution and
measured optimization experiments belong to the subsequent courses.
