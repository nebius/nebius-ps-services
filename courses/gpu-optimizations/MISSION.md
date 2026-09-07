# GPU Performance Optimization with PyTorch

Teach engineers to improve GPU workloads with a causal loop: freeze equivalent
work, measure correctly, classify the limiter, choose focused evidence, change
one factor, and remeasure end to end.

The course owns general PyTorch performance. Training checkpointing, inference
attention, and CUDA C++ implementation belong to their specialized courses.

## Beginner entry and advanced progression

The opening lesson introduces the subject, explains why it is useful and describes its main workflow before introducing advanced requirements. Beginners build vocabulary and work through the small example first; experienced readers can use the entry checkpoint and then follow the detailed optimization path. A conceptual CPU exercise, where provided, does not replace the later H100 evidence requirements.

[Lab 19](reference/labs/19_h2d_pipeline.md) extends Lesson 6 with a bounded,
event-ordered H2D input ring. [Lab 20](reference/labs/20_d2h_pipeline.md) extends
Lesson 8 with output workers, pinned pooling, nonblocking copies and an egress
stream. Both check complete outputs and time the final drain. Measure unprofiled
runs independently from short NVTX captures; overlap and speed remain target
evidence, not consequences of selecting a mode.
