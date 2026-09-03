# Foundations-first redesign

## Decision

Organize the course into four explicit parts: LLM training, LLM inference,
training performance optimization, and inference performance optimization.
Build the training path from tokens and embeddings through decoder blocks,
loss, gradients, and optimizer updates. Build the separate inference path from
artifact verification through prefill, decode, KV-cache behavior, streaming,
and stopping before teaching either optimization workflow.

## Rationale

Training changes parameters; inference keeps parameters fixed while request
state and the KV cache evolve. Mixing those lifecycles obscures which state,
metric, and correctness gate an optimization affects. Separate training and
inference memory ledgers, serving-replica and model-parallel diagrams, paged-KV
release/reuse, and agentic result reinsertion make the later performance
choices traceable to the underlying mechanism.

## Evidence

The renamed portable page contains 24 ordered lessons and 17 accessible SVG
diagrams. The title, contents, syllabus, course map, mission, source-coverage
map, official references, and validator use the same four-part progression.
The validator passes, and browser inspection found no oversized arrow markers,
box-text overflow, page overflow, or figure-container escape at desktop and
390-pixel viewport widths.

## Boundary

Tiny-model and serving labs demonstrate mechanisms rather than production
quality or capacity. H100 execution, external model availability, DDP/FSDP2,
NCCL, vLLM, profiler access, numerical acceptance, and performance remain live
cluster gates.
