# Benchmark record

Copy this page for every measured comparison.

- Date, random course run ID, and private artifact location:
- Node count and GPU family (no hostnames or device IDs):
- MIG status:
- Driver, CUDA, PyTorch, NCCL, and Slurm versions:
- Lab and exact arguments:
- Warm-up and measured iterations:
- Input shape and dtype:
- Correctness check:
- Median, minimum, and p90:
- Peak allocated GPU memory:
- Profiler evidence or observation:
- Conclusion and next experiment:

Change one variable at a time. Keep raw JSON and Slurm output private. A faster
run is not accepted if its correctness field fails. Share only a separately
reviewed summary that follows [evidence-security.md](evidence-security.md).
