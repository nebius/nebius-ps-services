# Optimization experiment record

- Hypothesis and suspected limiter:
- Baseline commit or artifact:
- Random course run ID and private artifact location:
- Node count, GPU family, MIG state, and topology class (no node names or device IDs):
- Driver, CUDA, PyTorch, NCCL, Nsight, and Slurm versions:
- Workload shape, dtype, warm-up, and iterations:
- Primary metric and correctness tolerance:
- Baseline median/p90 and peak memory:
- Sanitized profiler evidence summary:
- One change made:
- New median/p90 and peak memory:
- Correctness result:
- Decision: keep, revert, or investigate:

Do not combine unrelated changes in one experiment. Keep raw JSON and profiler
reports private. Share only a separately reviewed summary that follows
[evidence-security.md](evidence-security.md).
