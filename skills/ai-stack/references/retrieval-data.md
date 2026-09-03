# Retrieval, RAG, And AI Data

Use this reference for vector and graph systems, retrieval, RAG, ingestion,
reranking, data-pipeline integration, memory stores, and synthetic data.

## Contents

- End-to-end data path
- Retrieval architecture decision
- Vector-system decision
- Graph-system decision
- RAG patterns
- Ingestion and pipeline integration
- Synthetic data
- Security and governance
- Evaluation and operations
- Candidate-family guidance

Current application-centric default: Use PostgreSQL full-text search, pgvector
HNSW, relational metadata indexes, and row-level security. Switch to Qdrant,
OpenSearch, or Neo4j only when a named vector, search, or graph requirement
passes its acceptance gate. Read `compatibility-contracts.md` for embedding,
dimension, metric, filter, and authorization contracts.

## End-To-End Data Path

Model the complete path before choosing a database:

```text
authoritative source
  -> capture or ingestion
  -> parse and normalize
  -> validate, classify, deduplicate, and version
  -> chunk, extract entities, or transform
  -> embed and index
  -> retrieve and filter
  -> fuse and rerank
  -> enforce access and context budget
  -> generate, cite, or abstain
  -> evaluate and collect governed feedback
```

For every stage define input and output schema, owner, provenance, tenant and
document authorization, freshness, retries, idempotency, lineage, retention,
deletion, backfill, observability, and recovery.

An index is derived state. The authoritative source and rebuild path must remain
clear unless the selected system intentionally becomes the source of truth.

## Retrieval Architecture Decision

Choose from the simplest sufficient pattern:

### Deterministic Source Access

Use a typed API, database query, or tool when the required record or computation
is known and structured. It provides stronger correctness, freshness, and
access control than semantic retrieval for exact tasks.

### Source-Native Search

Use existing SQL, full-text, search-engine, or document-platform capabilities
when they meet relevance, filtering, freshness, and scale targets. Avoid a copy
and synchronization path without measured benefit.

### Two-Step RAG

Retrieve with a bounded deterministic query, then generate from the selected
context. Prefer for predictable latency, easy evaluation, and controlled data
access.

### Agentic Retrieval

Let a model choose sources, queries, or retrieval iterations only when query
planning across heterogeneous tools materially improves difficult tasks.
Bound steps, cost, sources, permissions, and context. Evaluate planning and
retrieval separately.

### Hybrid Retrieval

Combine lexical, semantic, structured, and sometimes graph signals when labeled
queries show that one method misses important cases. Define score
normalization, fusion, reranking, filters, and fallback. Hybrid is an evaluated
architecture, not a default checkbox.

## Vector-System Decision

Do not default to a dedicated vector database. Evaluate these options:

1. No vector search.
2. Vector extension or index in the existing authoritative database.
3. Existing search platform with vector or hybrid support.
4. Dedicated vector system.

A dedicated system may be justified by scale, recall-latency, filtering,
independent indexing, multi-vector or hybrid features, tenancy, operations, or
managed-service requirements.

Evaluate:

- exact versus approximate search and supported index algorithms;
- recall-latency-memory-build-time frontier on labeled queries;
- distance metrics, normalization, dimensions, sparse and dense vectors,
  multi-vector records, and reranking integration;
- metadata filters, selectivity, prefilter versus postfilter behavior, and
  tenant or document ACL enforcement;
- inserts, updates, deletes, tombstones, compaction, consistency, and freshness;
- sharding, replication, failover, backup, restore, rebuild, migration, and
  version compatibility;
- multi-tenancy, quotas, noisy neighbors, encryption, network controls, audit,
  and deletion guarantees;
- client libraries, deployment model, operational owner, and total cost.

Never enforce authorization only after top-k retrieval. Unauthorized records
must not be exposed to the model or reranker.

## Graph-System Decision

Use a graph-native system when the workload requires one or more of:

- relationship-constrained traversal with variable depth;
- multi-hop questions where paths and intermediate entities matter;
- path provenance or explanation;
- graph algorithms, communities, dependencies, or network structure;
- graph-native mutation, constraints, and query semantics;
- combined entity and relationship retrieval that outperforms simpler methods.

Do not add a graph database merely because content contains entities or links.
A relational schema, search index, or application-side expansion may be enough.

Evaluate graph construction quality, entity resolution, ontology or schema,
edge provenance, updates and deletes, traversal limits, authorization along
paths, query safety, vector integration, backup and restore, and operator skill.

GraphRAG is not one architecture. Specify whether the graph is used for entity
retrieval, path expansion, community summaries, query planning, provenance,
reranking, or context assembly.

## RAG Patterns

### Ingestion-Time Decisions

- parsing and layout preservation;
- chunk boundaries and overlap;
- parent-child or hierarchical structures;
- metadata and source identifiers;
- entity and relationship extraction;
- language and modality handling;
- deduplication and near-duplicate policy;
- embedding model and version;
- sparse representation and index;
- validation, quarantine, and publication.

### Query-Time Decisions

- query normalization, classification, expansion, or decomposition;
- source and tenant routing;
- structured, lexical, semantic, graph, or tool retrieval;
- filters and time or version constraints;
- top-k, fusion, reranking, diversity, and threshold;
- context compression and token budget;
- provenance, citations, abstention, and conflict handling.

Do not tune chunking, top-k, or reranking by intuition alone. Use labeled query
sets and stage-level metrics.

## Ingestion And Pipeline Integration

Choose batch, incremental, CDC, event, or on-demand ingestion from freshness,
source support, scale, replay, and ownership requirements.

Define:

- snapshot and incremental cursor or event identity;
- ordering, duplicate delivery, idempotency, late events, and replay;
- schema evolution and parser or embedding version;
- backpressure, rate limits, dead letters, quarantine, and reprocessing;
- backfill and dual-index migration;
- atomic publication or alias switch;
- delete and access-revocation propagation time;
- lineage from source version to chunk, vector, entity, edge, and index;
- reconciliation between authoritative source and derived index.

Do not add a streaming platform when periodic bounded batch ingestion satisfies
the freshness contract. Do not use periodic full rebuilds when delete or access
revocation deadlines require incremental propagation.

## Synthetic Data

Treat synthetic data as a governed generation pipeline:

1. Define the target gap and downstream metric.
2. Select source records, simulators, prompts, generators, and licenses.
3. Generate with immutable configuration and lineage.
4. Validate schema and deterministic constraints.
5. Filter unsafe, low-quality, duplicated, leaked, or contaminated samples.
6. Measure distribution, diversity, difficulty, and coverage.
7. Sample for human review where risk requires it.
8. Keep synthetic and natural data labels and splits visible.
9. Run an ablation against the non-synthetic baseline.
10. Version, retain, delete, and reproduce the generation batch.

Use synthetic data for rare cases, simulation, augmentation, privacy-preserving
workflows, or evaluation only when its limitations are explicit. Avoid using
the same generator and grader without independent calibration.

Examples to investigate include NVIDIA NeMo Curator and custom governed
pipelines. Tooling does not remove provenance, privacy, licensing, contamination,
or downstream-quality obligations.

## Security And Governance

Enforce:

- source and purpose authorization before ingestion;
- tenant and document ACL at retrieval time;
- identity propagation across query, retrieval, reranking, model, and trace;
- encryption, network boundaries, secrets management, and least privilege;
- prompt-injection treatment for indexed content and tool output;
- provenance and citation without leaking hidden or unauthorized source data;
- retention, legal hold, deletion, right-to-be-forgotten propagation, and
  evidence of completion;
- privacy-safe traces, evaluation sets, caches, and long-term memory;
- poisoning detection, quarantine, source trust, and rollback.

Retrieved text is untrusted content, not a trusted instruction source.

## Evaluation And Operations

Evaluate retrieval independently:

- recall at k, precision at k, mean reciprocal rank, normalized discounted
  cumulative gain, or task-specific ranking metrics;
- filter and ACL correctness;
- freshness and delete propagation;
- citation and provenance correctness;
- latency, throughput, memory, index build, update, and cost;
- slices by query type, source, tenant, language, length, time, and difficulty.

Then evaluate the complete path:

- answer correctness, groundedness, completeness, abstention, and citation;
- prompt-injection resistance and data-boundary compliance;
- context use, hallucination, latency, cost, and user task success.

Monitor source capture lag, parse failures, quarantine, embedding failures,
index lag, vector or graph capacity, retrieval latency, empty results, score
shifts, reranker behavior, ACL denials, stale citations, and answer quality.

## Candidate-Family Guidance

| Need | Candidate families to verify | Key boundary |
| --- | --- | --- |
| Vector search in existing transactional store | pgvector or another native extension | Scale, filters, index behavior, operational simplicity, source-of-truth fit |
| Dedicated vector and hybrid retrieval | Qdrant, Weaviate, or equivalent | Recall-latency, filters, updates, tenancy, recovery, managed option |
| Graph-native and GraphRAG retrieval | Neo4j or equivalent | Graph requirement, entity quality, path authorization, vector integration |
| RAG composition and ingestion | LangChain, LlamaIndex, or direct application code | Abstraction value, control, evaluation, dependency and upgrade cost |
| Synthetic-data curation | NeMo Curator or governed custom pipeline | Generator support, filters, provenance, distributed processing, downstream ablation |

Verify current product behavior, supported indexes, consistency, lifecycle, and
operations from official documentation. Benchmark using the target corpus and
labeled queries.
