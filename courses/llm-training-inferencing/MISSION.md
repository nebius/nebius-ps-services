# Mission

## Learner

This course is for engineers who know Python and basic PyTorch and want an end-to-end mental model of LLM training and inference, including performance decisions that can be practiced on a small two-node H100 Slurm cluster.

## Capability after the course

The learner can explain the data, compute, memory, and communication path of a decoder-only language model; train and adapt a small model safely; serve and measure it; and diagnose training or inference performance without confusing model quality, kernel speed, and user-visible latency.

## Learning outcomes

By the end, the learner can:

1. Explain text, tokens, token IDs, vocabulary, embedding vectors, positional information, decoder blocks, logits, and shifted next-token labels as one connected data path.
2. Explain causal attention through Q, K, V, masking, softmax, multiple heads, residual paths, normalization, and the MLP sublayer in operational terms.
3. Trace forward computation, loss, autograd, gradients, accumulation, optimizer state, learning-rate policy, parameter updates, evaluation, and checkpoints.
4. Implement and validate a small decoder-only training step on one H100 and distinguish pretraining, continued pretraining, full SFT, LoRA SFT, and a simplified GRPO objective.
5. Explain the separate inference lifecycle: artifact verification, tokenization, prefill, logits, token selection, decode, KV-cache growth, stopping, and detokenization.
6. Optimize training through a phase-specific memory ledger, precision, batching, checkpointing, DDP/FSDP2, communication, and fixed-token evidence.
7. Optimize inference through phase-specific memory accounting, TTFT/ITL/E2E/throughput objectives, scheduling, paged and prefix KV caching, quantization, and serving parallelism.
8. Run one-GPU and two-node training labs with Slurm and `torchrun`, and run single- or two-node vLLM serving labs with explicit topology.
9. Produce separate training and inference optimization reports with correctness and quality gates.

## Runtime contract

- Slurm cluster with two nodes and one full non-MIG H100 per node.
- One-GPU labs use one-node Slurm allocations.
- Distributed PyTorch labs use two nodes, one rank per node, and `torchrun` launched under Slurm.
- vLLM launchers use their official distributed serving mechanisms where applicable.
- Models and revisions are pinned; learners must verify access and cache behavior before live work.

## Non-goals

The labs do not train a production-scale foundation model, establish model quality from tiny synthetic runs, reproduce large-cluster scaling, or promise that every advanced method fits every model. They teach mechanisms and evidence on bounded examples that can run on the declared cluster.
