# Course map

The complete portable course is [index.html](index.html). The repository labs are canonical; the HTML embeds exact copies and the validator checks parity.

## Learning sequence

| Part | Modules | Deliverable |
| --- | --- | --- |
| I. Performance foundations and evidence | 1–2 | End-to-end critical path and a five-class bottleneck hypothesis with an alternative and a disconfirming control |
| II. Diagnostic tools and practical profiling | 3–7 | Compute-node tool inventory plus framework, system, kernel, cluster, and serving evidence collected at the correct scope |
| III. Measurement and targeted optimization | 8–17 | Trustworthy timing and one-factor experiments for launch, transfer, HBM/layout, kernel efficiency, compute/shape, compilation/graphs, input readiness, and capacity |
| IV. Distributed performance and causal decisions | 18–19 | Fixed-work one-rank/two-rank comparison, overlap evidence, and a complete keep/reject report |

## Assessment model

- Each numbered module is one lesson with retrieval, a worked diagnosis, guided practice, an answer key, and a spaced-review cue.
- Every lab starts with a prediction and ends with evidence interpretation and troubleshooting.
- The learner must show when PyTorch Profiler, Nsight Systems, Nsight Compute, DCGM, or a serving load tool is appropriate—and when it is not.
- The learner must reject at least one change that fails correctness, reproducibility, memory, or end-to-end criteria.
- Live completion requires both one-node isolation studies and a two-node `torchrun` scaling result.

## Recommended pacing

Plan for about 40 guided hours: 17 hours of lessons and review, 18 hours of labs, and 5 hours for the capstone. The learner first identifies the critical path and primary bottleneck class, then installs and uses the smallest tool that can answer the next question. Techniques follow the evidence.
