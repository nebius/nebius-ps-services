# Learning record 0002: End-to-end LLM redesign

> Superseded as a current course description. This record captures the earlier
> 19-module design; later source-audit and editorial records describe the
> current 20-module course.

## Decision

Expand the course into 19 modules spanning model semantics, single-device training, distributed training, parameter-efficient adaptation, reinforcement-learning concepts, autoregressive inference, serving systems, metrics, and optimization reports.

## Rationale

The earlier artifact exposed many techniques but did not unpack the causal path from tokens to loss or from prompt arrival to streamed output deeply enough. Learners need explicit shape/dataflow examples, memory ledgers, metric boundaries, and guided decisions before advanced tooling is useful.

## Practice impact

Three labs were added for loss masking, activation checkpointing, and streaming latency. The existing training and serving labs were retained, embedded into the course, and organized into an ordered two-node Slurm smoke-test.

## Evidence boundary

Static validation proves artifact consistency only. H100 execution, package compatibility, model access, NCCL behavior, vLLM behavior, numerical tolerances, and performance remain live cluster gates.
