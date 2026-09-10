# Lab 05: Fine-tune a small model with LoRA adapters

LoRA trains low-rank adapter parameters while leaving most pretrained weights frozen. This lab performs a short supervised fine-tuning exercise on a pinned public model and checks that adapters receive gradients and actually change. You will distinguish parameter efficiency, memory use, and observable model changes from any unsupported claim that a tiny training run improves general model quality.

## Before you start

**Theory preparation:** Read Lessons 2–4 for labels, transformer computation and updates, then Lesson 14 for SFT, LoRA factors and trainable-state accounting. Distinguish padding-only masking from response-only supervision.

Qualify the Training extras and the approved model artifact described in the runbook. Model revisions must be immutable commits and remote custom code is disabled. Downloads, model caches, and adapter artifacts remain private.

Adapter compute can be small relative to the base GEMM but may introduce extra kernels or prevent an optimized fused path. Verify dispatch and end-to-end time on the target stack.

## Concepts and code path

For one projection, the adapter adds `s * B(Ax)` to the frozen base result `Wx`. A reduces the input to rank r; B expands it to the output width; s is the configured update scale. Only the selected trainable factors receive optimizer updates. Read Lesson 14's two-value hand calculation before interpreting trainable-parameter counts; the reduction in state follows from these smaller matrix shapes, not from skipping the base projection.

Transformers loads the pinned tokenizer and model; PEFT (Parameter-Efficient Fine-Tuning) attaches LoRA factors to `q_proj` and `v_proj`. Inspect actual trainable parameters before interpreting the requested configuration. The supplied workload masks padding only, so question and answer tokens both contribute to loss; response-only masking is an extension. The script records trainable counts, adapter gradients and updates, frozen-base behavior and a bounded held-out observation. Frozen base weights still occupy memory.

## Practice

Given a 4096-by-4096 projection and rank 8, full weight count is about 16.8 million while LoRA adds `8*(4096+4096)=65,536` trainable values. Change only the optimization method. Expected observation: adapter optimizer bytes fall by roughly two orders of magnitude, but base weights and activation memory remain, so peak-memory and step-time savings are much smaller.

Run Lab 05 and audit trainable parameters, the implemented padding-only loss mask, loss, and memory. Its checkpoint saving is not implemented. Extension: save adapters with their configuration and pinned base revision, then test save/reload equivalence on a fixed evaluation input; use Lab 13 when adding response-only masking.

Inspect artifact options, then use the pinned defaults after approval. Changing `--model` requires a matching immutable `--revision` and a review of tokenizer, architecture, and target adapter modules.

```bash
umask 077
python labs/05_lora_sft.py --help
sbatch slurm/single_gpu.sbatch labs/05_lora_sft.py --profile smoke
```

## Check your results

Require finite loss, nonzero finite adapter gradients, a nonzero adapter update, and the parameter-efficiency gate. Inspect `trainable_percent`, loss values, `adapter_max_parameter_delta`, elapsed time, and peak allocation. A changed digest is evidence of changed output, not better output.

Retain the supplied trainable/total counts, adapter settings, loss, and memory fields. The baseline also records `held_out_next_token_before/after` and `held_out_logits_digest_before/after` for one fixed held-out input. Preserve these bounded observations without treating a changed token or digest as proof of improved quality. Adapter persistence, reload comparisons, and broader held-out task-quality evaluation require explicit learner extensions.

Parameter-efficiency and runtime speed are different claims.

## Investigate the behavior

Which persistent tensors disappear from the trainable-state ledger and which remain? For a response-only masking extension using Lab 13's concepts, explain why ignoring prompt labels does not stop response tokens from attending to the prompt. Identify what a meaningful held-out quality evaluation would add.

Lower rank reduces state and possibly expressiveness. Targeting more modules adds flexibility and memory. Merging adapters can simplify inference but changes artifact management and may remove convenient multi-adapter serving.

## If something goes wrong

Missing target modules indicate an artifact/architecture mismatch. Zero adapter gradients can indicate incorrect freezing or label masking. Preserve failures and do not enable remote code or choose an unreviewed model to bypass setup.

Avoid comparing LoRA and full tuning with different data, effective batch, or evaluation prompts.

## Takeaways and next step

Parameter-efficient training needs verified gradient flow and updates. Treat this as a small SFT systems example; expand the dataset, evaluation, and checkpoint protocol before claiming useful adaptation.

Keep the objective fixed and report trainable state, optimizer state, memory, and quality separately.

Explain what LoRA saves and what it normally does not save.
