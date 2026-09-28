# Glossary

- **Collective** — A coordinated operation such as all-reduce, all-gather or all-to-all across a process group.
- **Exposed communication** — Communication that remains on the critical path after useful overlap.
- **Goodput** — Completed requests per second that meet every declared latency objective.
- **GPUDirect RDMA** — A device path allowing the network adapter to access registered GPU memory without staging each transfer through host memory.
- **HCA** — Host channel adapter connecting a worker to the InfiniBand fabric.
- **InfiniBand** — The inter-node fabric used by the eight-GPU platform; link state and selected transport need separate evidence.
- **KV cache** — Attention keys and values retained for previously processed tokens.
- **NIXL** — NVIDIA Inference Xfer Library, used to transfer cache and other data between memory regions.
- **NVLink / NVSwitch** — GPU links and switching that connect GPUs within the selected H100 node. A topology label alone does not prove switch health.
- **Rank** — One process participating in a distributed group; global rank identifies the process across workers.
- **RDMA** — Remote direct memory access between registered memory regions.
- **Tensor / context / expert parallelism** — Partitioning model operations, sequence context or routed experts across ranks.
- **TTFT** — Time from submitting a request until the client sees its first generated content.
