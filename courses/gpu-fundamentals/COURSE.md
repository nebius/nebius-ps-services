# Course map

The self-contained teaching artifact is [index.html](index.html). It contains the complete lesson prose, diagrams, exercises, answer keys, and canonical embedded copies of every lab.

## Learning sequence

| Part | Modules | Main question | Practice |
| --- | --- | --- | --- |
| I. GPU foundations and Hopper architecture | 1–5 | Why does a GPU behave differently from a CPU, how is an H100 organized, and how does a PyTorch operation become scheduled parallel work? | CPU/GPU crossover, operator mapping, and Triton launch geometry |
| II. Measurement and scheduling | 6–8 | How do asynchronous execution, bottleneck hypotheses, occupancy, latency hiding, and divergence become trustworthy evidence? | Cluster preflight, CUDA timing/streams, and guided classification |
| III. Data movement and computation | 9–13 | Where do bytes move, when can work overlap, what selects a Tensor Core path, and how does roofline reasoning guide the next experiment? | Transfer/pinning, layout/repack, streams, precision, and roofline labs |
| IV. Multi-node scale and diagnosis | 14–15 | What enters the critical path across two nodes, and how should the learner report a bounded diagnosis? | NCCL collective lab through `torchrun` and the capstone record |

## Assessment model

- Each numbered module is one lesson with a retrieval prompt, a worked example, a practice task, an answer key, and a review cue.
- Every lab requires a prediction before execution and a written interpretation afterward.
- The capstone is a benchmark record that declares the boundary, correctness gate, primary metric, environment, and next experiment.
- Completion requires static course validation now and live cluster evidence when the H100 cluster is available.

## Recommended pacing

Plan for about 30 guided hours: 12 hours of reading and retrieval, 14 hours of labs, and 4 hours for review and the capstone record. Follow the foundations-first order; architecture terms introduced in Part I are reused by every later measurement.
