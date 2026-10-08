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
11. Evaluation mode and gradient mode are separate
12. Transfers are part of the input pipeline
13. Live tensors and reserved memory are different
14. Batching expresses repeated work as tensor operations
15. A timer must include completion
16. Code suggests a hypothesis; a profile supplies evidence
17. Compilation can reduce launches and intermediate storage
18. Read a complete step like a performance engineer

Estimated guided hours: 3. Basic Python is the entry requirement; no PyTorch,
machine-learning, GPU or cluster experience is required. Lessons 1–7 own tensor
representation and operations; 8–11 connect them to execution and models; 12–18
develop performance-reading and optimization habits.

Read before GPU Fundamentals. GPU Performance Tools supplies optional command
reference context, not required tensor knowledge. Every lesson has an observable
objective, definition-first teaching, contextual diagrams, short examples that
each teach one relationship and a brief Performance connection. Exact PyTorch
API names use bold monospace; example variables and values use ordinary code;
application meanings stay plain. Headings and callout labels may remain bold.

Define concepts affirmatively and explain terminology before notation. Lesson 2
uses one grid example for indexing, slicing and reshaping. Lesson 6 adds
inferred sizes and axis reordering; lesson 9 explains the linear layer's
feature-axis rule.

Batching compares a row loop with one whole-tensor calculation. Compilation
reuses the preceding function and checks its result. The final explained
inference step demonstrates transfer and delayed recall without an exercise or
completion gate. Keep the conditions needed for each example, without unrelated
API catalogues or advanced setup. Broadcasting teaches one matrix-plus-bias
addition; reductions teach row means; transfers show prepare, copy and compute.

There are no labs, Practice sections, assessments, runtime setup or required
execution. Profiling commands, hardware details, distributed execution and
measured optimization experiments belong to the subsequent courses.
