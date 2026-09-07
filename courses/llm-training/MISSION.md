# LLM Training and GPU Optimization on NVIDIA H100

Teach engineers to preserve the learning objective while improving training
correctness, recoverability, memory, precision, data flow, communication, and
throughput on one or two H100 GPUs.

The two-node exercises prove bounded mechanics only; they do not claim
production-scale NVLink, NVSwitch, expert, pipeline, or context performance.

## Beginner entry and advanced progression

The opening lesson introduces the subject, explains why it is useful and describes its main workflow before introducing advanced requirements. Beginners build vocabulary and work through the small example first; experienced readers can use the entry checkpoint and then follow the detailed optimization path. A conceptual CPU exercise, where provided, does not replace the later H100 evidence requirements.

[Lab 33](reference/labs/33_ddp_buckets.md) follows Lab 28 with real DDP bucket
caps and allreduce, FP16, BF16 and PowerSGD hooks. It records actual bucket
bytes, joined-step samples and numerical trajectories against a full-batch FP32
reference. Compression results remain candidates until convergence on the target task and
performance in independent H100 trials are evaluated.
