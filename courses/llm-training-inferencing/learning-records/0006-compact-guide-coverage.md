# Compact-guide coverage decision

## Decision

Retain the four-part foundations-first course and expand only the concepts that
the compact source exposed as too compressed. Existing tokenizer, transformer,
training lifecycle, KV cache, batching, quantization, serving parallelism, and
queueing material already exceeds the source's depth and is not duplicated.

## Additions

- A state-by-state mixed-precision ledger, FP16 scaling behavior, and current
  Transformer Engine FP8 recipe distinctions.
- A full distributed-training strategy map covering replicated and sharded
  data parallelism, tensor, pipeline, context/sequence, and expert parallelism.
- Explicit tokens/s, MFU, HFU, scaling, memory, communication, and quality
  metric boundaries.
- Open- versus closed-loop serving, goodput, phase-specific attention backends,
  CUDA Graph buckets/fallbacks, and speculative-decoding acceptance mechanics.
- Three Slurm labs for matched FP32/BF16/FP16 training, optional FP8, and
  high-/low-acceptance speculation.

The mixed-precision lab gates the loss, complete unclipped-gradient vector,
and complete update vector from identical state before running a separate
warmed timing trial. The optional FP8 lab evaluates several warmed
delayed-scaling states before and after timing, rejects non-finite thresholds,
and distinguishes each warmed allocation baseline from the incremental peak.
The speculation lab verifies the standard greedy recovery path and the target
bonus token produced after a fully accepted draft, and it rejects output limits
that leave no room to exercise the bonus path.

The FP8 lab remains optional and dependency-gated. The speculation lab is a
synthetic correctness/mechanics study; neither lab claims live performance
until the declared H100 smoke gates pass.
