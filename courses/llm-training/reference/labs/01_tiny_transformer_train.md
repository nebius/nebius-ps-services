# Lab 01: Trace a complete tiny-transformer training step

A training step transforms token IDs into predictions, evaluates their error, propagates gradients, and updates parameters. This lab makes that entire cycle small enough to inspect on one H100. You will connect model structure to loss, gradient, update, memory, and throughput evidence instead of accepting a decreasing scalar as the only sign that training works.

## Before you start

**Theory preparation:** Read Lessons 1–4 for token/position embeddings, LayerNorm/GELU/attention, shifted cross-entropy, autograd, BF16 autocast, gradient clipping and AdamW. Lessons 1 and 3 offer code previews; the first full update runs in Lesson 4. Read Lesson 5 before the optional checkpoint-saving exercise, and do not treat that partial snapshot as exact resume state.

Use the Training environment on one H100. The model and synthetic batches are local; no external model download is needed. Review logits, next-token labels, and cross-entropy in the course lessons.

H100 Tensor Cores favor supported matrix shapes and reduced-precision inputs, while fused attention reduces intermediate HBM traffic. The layer dependency remains sequential even though each operation contains extensive parallel work.

H100 accelerates the dense work, making optimizer, launch, and communication phases more visible. BF16 commonly avoids FP16 loss scaling, but numerical checks still apply.

## Concepts and code path

`tiny_lm.py` owns the decoder model and synthetic batch construction; `common.py` owns device checks and result handling. The lab orchestrates BF16 forward computation, cross-entropy, backward, and optimizer updates. It tracks parameter movement and gradient norms, then separates baseline allocation from the incremental peak of the timed region. This is a plumbing and performance exercise, not a useful pretrained language model.

## Practice

### After Lesson 3

Given `B=2`, `S=1024`, `H=4096`, and 32 query heads, each head dimension is 128 and the hidden activation holds about 16 MiB in BF16. Change to eight KV heads for GQA. Expected observation: Q keeps 32 heads while K/V projection and cache dimensions shrink fourfold; the model’s logits and loss contract must remain valid.

Inspect Lab 01's model forward and write the expected shape and dtype at each boundary. This is a code-reading and shape-tracing activity; the supplied script also performs backward and updates, so run the complete experiment in Lesson 4. Do not assume a forward-only command-line mode exists.

### After Lesson 4

Run Lab 01 and trace one correct update. Return to Lab 13 for loss-masking and gradient checks. Revisit Lab 25's mask structure with this model context; it does not implement packed-model loss or gradient equivalence, which requires a separate model-forward/loss reference extension. Read Lab 02's effective-batch comparison now; run its accumulation experiment in Lesson 8 after the memory and precision lessons.

### Run the supplied experiment

Run smoke first. Saving a checkpoint is optional and creates private state; it does not perform the deterministic resume-equivalence experiment, which is owned by Lab 24.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/01_tiny_transformer_train.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_tiny_transformer_train.py --profile smoke --save-checkpoint
```

## Check your results

Require finite loss and gradients, gradients for every trainable parameter, and a nonzero tracked parameter update. Inspect initial/final loss, gradient norms, tokens/s, step time, and baseline/incremental memory. A short synthetic run does not prove held-out quality or convergence.

Record shapes, parameter counts, activations, logits, loss, and finite gradients.

Shape traces expose which dimensions drive compute, memory, and parallel partition choices.

Retain pre/post parameters, loss, gradient norms, accumulation steps, effective tokens, and update count.

Numerical closeness is required before timing accumulation strategies.

## Investigate the behavior

Write tensor shapes from token IDs through vocabulary logits. Identify which tensors require gradients and why integer token IDs do not. Explain why optimizer state and saved activations contribute differently to memory.

Increasing the number of heads, hidden width, layer count or sequence length changes capacity and cost in different ways. GQA reduces K/V state and related work but changes architecture. Fused kernels reduce traffic yet have shape/dtype/mask support boundaries that must be verified.

At fixed microbatch size, more accumulation steps increase the effective batch size and place more computation between optimizer updates. At fixed effective batch size, accumulation instead allows smaller microbatches, with possible launch and synchronization overhead. Gradient clipping can stabilize training but adds reduction work and must be included consistently in comparisons.

## If something goes wrong

Missing gradients suggest a detached path, frozen parameter, or unused module. Non-finite loss requires checking data, precision, and update scale before timing. A flat tracked parameter invalidates the update gate even if backward completed.

Avoid treating the number of heads as independent of hidden width and head dimension.

Forgetting to divide microbatch loss changes the effective gradient magnitude.

## Takeaways and next step

Training acceptance needs observable learning signals and state changes, not merely a completed loop. Use this architecture as the reference mental model for masking, recomputation, precision, and distributed training exercises.

Verify `hidden = heads × head_dim` for standard attention and trace every residual-compatible tensor.

Explain which tensors grow with batch, sequence, hidden width, and vocabulary.

Match summed or averaged loss semantics and count optimizer steps explicitly.

State when gradients are created, accumulated, consumed, and cleared.
