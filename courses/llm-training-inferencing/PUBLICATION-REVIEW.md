# Publication review

Status: public-safe static review and browser-rendered layout QA complete; live
cluster execution remains pending.

## Public-safety review

- The release contains only generic public course content and no non-public source metadata or environment-specific identifiers.
- Model examples use public model identifiers and immutable revisions.
- Serving clients allow loopback HTTP only; they do not accept arbitrary remote endpoints.
- The two-node serving path is documented for a trusted private cluster fabric; its worker/control transport is not presented as a public encrypted endpoint.
- External references are public official NVIDIA, PyTorch, Hugging Face, vLLM, and Slurm documentation.
- Logs, downloaded data/models, environments, results, profiler output, and caches are excluded by `.gitignore`.
- The generic source-coverage map retains topic coverage only; it omits source names and non-public metadata.

## Technical review contract

- Tiny models are labeled as mechanism demonstrations, not quality evidence.
- Training labs include semantic or numerical gates before performance interpretation; Lab 21 compares loss, full gradients, and full updates from identical state before a separate warmed timing run.
- Serving metrics distinguish TTFT, streamed chunk gaps, end-to-end latency, throughput, queueing, and KV-cache pressure.
- An HTTP streaming chunk is explicitly not treated as necessarily one model token.
- Distributed PyTorch launchers request two nodes and invoke `torchrun` with one rank per node.
- The HTML embeds canonical lab source and the validator enforces parity.
- Static validation compiles reviewed Python into a temporary directory but does not execute reviewed lab source; live launcher help is checked separately.
- The single-GPU launcher sets an owner-only umask and suppresses Python bytecode before every one-H100 lab, including Labs 21–23.
- The optional Transformer Engine lab fails closed when its separately approved environment is unavailable, validates warmed delayed-scaling states before and after timing, measures BF16 before FP8 state exists, reports only warmed baselines plus incremental peaks, and remains outside the base requirements.
- The speculative-decoding lab uses synthetic token transitions, verifies target recovery and full-acceptance bonus-token paths, preserves exact target-greedy output, and is labeled as a mechanics study rather than a production speed claim.

## Evidence still pending

The target cluster does not yet exist, so the following remain unverified:

- H100/CUDA/framework compatibility under the pinned environments, including the optional Transformer Engine build.
- External model access and immutable-revision availability from compute nodes.
- DDP, FSDP2, NCCL, and inter-node network behavior.
- vLLM startup, metrics, cache behavior, and multi-node serving on the site.
- Model-artifact network access, deterministic sampling, padding/bucketing equivalence, tensor-parallel collectives, and prefix-cache A/B evidence.
- Mixed-precision/FP8 numerical tolerances, speculative-decoding acceptance behavior, full-workload memory footprints, timing distributions, and optimization outcomes. Lab 22's static contract covers only its declared per-path warmed baseline and incremental allocation boundary.
- Nsight or other site-specific profiler access if used during extension work.

After the four-part foundations-first redesign, the course was served from a
temporary loopback HTTP server and reviewed in the in-app browser at desktop
and 390-pixel viewport widths. The review covered the renamed and simplified
header, training and inference foundations, both optimization parts, diagram
flows and text fit, tables, labs, references, and footer. Content stayed within
the page at both widths; wide diagrams, tables, and source listings used
contained horizontal scrolling.
The validator separately checked anchors, title and contents alignment,
accessible SVG titles, lesson structure, and embedded-source parity.

Execute [reference/cluster-smoke-test.md](reference/cluster-smoke-test.md) and preserve the evidence before changing the status to live-verified.
