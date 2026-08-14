# AI Workload Contracts

Define one contract per materially different workload before selecting
technologies. A system may need several contracts and different products for
each.

## Contents

- Universal contract
- Model acquisition and customization
- Training and fine-tuning
- Online inference
- Batch inference
- Embedding and reranking
- Agent and tool execution
- Retrieval and RAG
- Synthetic data
- Evaluation and release

## Universal Contract

Capture:

- Purpose: User or system outcome and failure impact.
- Baseline: Current behavior, quality, latency, cost, and failure modes.
- Acceptance: Metrics, thresholds, evaluation set, traffic shape, and owner.
- Data: Sources, volume, modalities, sensitivity, provenance, license, access,
  retention, deletion, and residency.
- Model: Provider or source, architecture, modality, context, license, artifact
  format, tokenizer, templates, adapters, and version policy.
- Environment: Hardware, region, platform, network, storage, identity, and
  operational constraints.
- Lifecycle: Build, evaluate, register, promote, deploy, monitor, rollback, and
  retire.
- Scale: Current, expected, peak, and growth assumptions.
- Economics: Budget, cost unit, utilization target, and capacity buffer.
- Ownership: Development, model, data, security, platform, and operations.
- Unknowns: Facts that can change the architecture or product choice.

Do not invent missing values. Use ranges or mark them unknown.

## Model Acquisition And Customization

Choose among hosted model use, self-hosted existing weights, prompt and tool
changes, retrieval, parameter-efficient tuning, full fine-tuning, continued
pretraining, and pretraining.

Capture:

- Task and modality, quality baseline, and largest observed failure classes.
- Whether failures come from missing knowledge, reasoning, style, domain
  behavior, policy, latency, or tool integration.
- Required model ownership, exportability, customization, and deployment
  location.
- Training examples, labels, feedback quality, provenance, contamination risk,
  and update frequency.
- Evaluation set size, representativeness, hidden holdout, and human review.
- Expected benefit that cannot be achieved by a simpler rung in the ladder.

Require an evaluation against the simpler rung before escalating customization.

## Training And Fine-Tuning

Capture:

- Model architecture, parameter count, active parameters, sequence-length
  distribution, precision, optimizer, and expected memory footprint.
- Dataset size, sample shape, token count, epochs, shuffle, streaming, and
  preprocessing cost.
- Single accelerator feasibility and the reason distribution is required.
- Data, tensor, pipeline, expert, context, and sequence parallelism candidates.
- Accelerator type and count, memory, interconnect, topology, storage bandwidth,
  and placement constraints.
- Checkpoint size, frequency, duration, retention, resume semantics, and recovery
  objective.
- Duration, scheduling, preemption, queueing, utilization, and budget.
- Determinism, reproducibility, experiment tracking, artifact lineage, and
  promotion criteria.

Acceptance must include quality, training stability, throughput or utilization,
recovery, and total run cost.

## Online Inference

Capture distributions, not only averages:

- Input length, output length, modality, structured output, tool calls, and
  streaming behavior.
- Request rate, concurrent sequences, burst size, tenant mix, priority classes,
  and cancellation rate.
- Time to first token, inter-token or time-per-output-token latency, end-to-end
  p50/p95/p99, and maximum deadline.
- Throughput at the accepted latency, queue time, saturation point, and overload
  behavior.
- Model and adapter mix, context reuse, shared-prefix probability, and routing
  constraints.
- Availability, regional failover, warm capacity, rollout, fallback, and
  rollback.
- Cost per accepted request or task, not only cost per token.

Benchmark at several offered loads and report the latency-throughput frontier.

## Batch Inference

Capture:

- Input volume, arrival pattern, deadline, ordering, partitioning, retries, and
  duplicate tolerance.
- Model and adapter mix, sequence distributions, batching opportunity, and
  accelerator utilization.
- Checkpointing, partial output, resume, lineage, and idempotent publication.
- Throughput, completion time, cost, failure rate, and data retention.

Do not use online-serving architecture by default when queue-based batch work
can achieve higher utilization with simpler reliability semantics.

## Embedding And Reranking

Capture:

- Content type, language, dimensions, maximum length, batching, and update rate.
- Retrieval task, relevance labels, target recall and precision, top-k, filters,
  and reranking depth.
- Embedding versioning, normalization, distance metric, index compatibility,
  backfill, and mixed-version policy.
- Latency, throughput, cost, and accelerator or CPU constraints.

An embedding change is a data migration. Define re-embedding, dual-read,
validation, rollback, and deletion behavior.

## Agent And Tool Execution

Capture:

- Task boundaries and the selected behavior level: direct model call,
  deterministic workflow containing model calls, or agent.
- Whether one request is sufficient, application code knows the exact
  sequence and continuation rules, or the model must choose tools, continuation,
  or next steps from prior results and run a model-driven
  observe-act-observe loop.
- Whether an unknown iteration count has a deterministic code-owned condition
  or model-controlled continuation.
- Why normal code, a direct call, or a deterministic workflow is insufficient
  before accepting agentic control flow.
- Maximum autonomy, steps, duration, model calls, token use, cost, and retries.
- Tool inventory, side effects, trust level, identity, scopes, input validation,
  output validation, idempotency, and approval requirements.
- Required durability, pause and resume, checkpointing, replay, concurrency,
  cancellation, and human intervention.
- Whether graph and durability semantics wrap a direct call, deterministic
  workflow, agent, or mixture; do not infer agent autonomy from those semantics.
- Session state, durable workflow state, long-term memory, external knowledge,
  audit history, and retention as separate stores.
- MCP or direct integration requirement, transport, authentication, discovery,
  tenancy, and deployment boundary.
- Success metric, trace requirements, failure taxonomy, and safe terminal state.

Acceptance must include task quality, tool correctness, authorization, unsafe
attempt rate, recovery, latency, and cost.

## Retrieval And RAG

Capture:

- Authoritative sources, formats, ownership, corpus size, freshness, updates,
  deletes, and historical versions.
- Parsing, chunking, entity extraction, metadata, language, deduplication, and
  provenance requirements.
- Keyword, semantic, hybrid, graph, structured, and tool-based retrieval needs.
- Query types, filters, relationship depth, access controls, tenants, and
  personalization.
- Labeled relevance set, recall and precision targets, reranking, context
  budget, groundedness, citation, and abstention requirements.
- Index consistency, backfill, rebuild, migration, backup, restore, and deletion
  behavior.

Evaluate retrieval independently from generation, then evaluate the complete
answering path.

## Synthetic Data

Capture:

- Purpose: training, evaluation, rare-event coverage, privacy, simulation, or
  augmentation.
- Source models, source records, prompts or simulators, licenses, and provenance.
- Target distributions, diversity, realism, label correctness, and known gaps.
- Filtering, deduplication, contamination, memorization, privacy, and safety
  checks.
- Human sampling, automated validators, holdout separation, and downstream
  ablation plan.
- Versioning, lineage, retention, and ability to remove a source or generation
  batch.

Synthetic volume is not quality evidence. Require downstream evaluation against
a non-synthetic baseline.

## Evaluation And Release

Capture:

- Evaluation layers: model, retrieval, tool, workflow, end-to-end, safety,
  performance, reliability, and human review.
- Datasets, fixtures, traffic replays, simulators, hidden holdouts, and versioning.
- Metrics, thresholds, confidence intervals, slices, and regression budget.
- Release gates, approvers, canary, shadow or A/B plan, rollback, and monitoring.
- Production feedback, trace sampling, privacy controls, drift detection, and
  retraining or re-indexing triggers.

A release is blocked when required evidence cannot be reproduced or tied to the
exact model, prompt, tools, data, index, runtime, and configuration versions.
