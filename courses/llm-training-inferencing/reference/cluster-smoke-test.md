# Cluster smoke-test runbook

Run from the `llm-training-inferencing` course root. Use the training or serving environment pinned in [VERSIONS.md](../VERSIONS.md). Keep model revisions, Slurm output, result JSON, and server logs private. Share only a sanitized summary that follows [evidence-security.md](evidence-security.md).

Before submitting jobs, run `umask 077` in the submitting shell. The launchers
repeat this setting for child-process artifacts, but the scheduler can create
its output file before the script begins.

## Gate 1: cluster and PyTorch distributed

```bash
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

Require two distinct nodes, one non-MIG H100 per rank, world size 2, correct local-rank binding, successful NCCL initialization, and exact collective correctness.

## Gate 2: model semantics on one H100

```bash
sbatch slurm/single_gpu.sbatch labs/16_model_artifact_audit.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/13_loss_masking.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_tiny_transformer_train.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/14_activation_checkpointing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/08_kv_cache.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/17_sampling_semantics.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/18_padding_bucketing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/21_mixed_precision_training.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/23_speculative_decoding.py --profile smoke
```

Stop if masks, finite gradients, parameter updates, Lab 21's identical-state
loss/gradient/update tolerances, cache/no-cache equivalence, exact target-greedy
speculation output, reduced high-acceptance target calls, or required
recovery/bonus paths fail. Treat Lab 23 timing as synthetic mechanism evidence,
not a production serving result.

Lab 01 must additionally report nonzero finite gradient norms, prove that every
trainable parameter received a gradient, show a full-parameter update delta,
and distinguish baseline allocation from incremental timed peak allocation.

If the cluster owner provides an approved Transformer Engine build compatible
with the pinned PyTorch/CUDA environment, run the optional FP8 gate:

```bash
sbatch slurm/single_gpu.sbatch labs/22_transformer_engine_fp8.py --profile smoke
```

Record Transformer Engine's resolved version and recipe. Confirm its numerical
samples cover warmed delayed-scaling states before and after timing. Stop if the dependency
is unavailable or the output/gradient relative-L2 gates fail; do not modify the
base environment ad hoc to force the optional lab to run.

## Gate 3: two-node training mechanisms

```bash
sbatch slurm/two_node.sbatch labs/03_ddp_train.py --profile smoke
sbatch slurm/two_node.sbatch labs/04_fsdp2_train.py --profile smoke
```

Record local/global batch semantics, sharding behavior, peak memory, step time, communication evidence, and exact rank count.

## Gate 4: adaptation and objective labs

```bash
sbatch slurm/single_gpu.sbatch labs/05_lora_sft.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/07_grpo_trainer.py --profile smoke
```

Pinned external model access is required for framework labs. Treat tiny runs as mechanism checks, not quality evaluations.

## Gate 5: inference mechanisms

```bash
sbatch slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/10_vllm_offline.py --profile smoke
sbatch slurm/vllm_benchmark.sbatch
sbatch slurm/vllm_streaming_benchmark.sbatch
sbatch slurm/vllm_prefix_cache.sbatch
```

Keep cold start separate from steady state. Report TTFT, end-to-end latency,
throughput, output correctness, and cache/queue evidence. Preserve the
Prometheus snapshots emitted by all server launchers. For the prefix-cache A/B,
require comparable prompt-token cohorts and inspect cache queries/hits rather
than inferring reuse from repeated visible text.

Lab 09 must emit exactly the requested new-token count with explicit attention
mask, cache position, and cache-length advancement. Lab 10 must aggregate every
measured request and report both prompt-token and output-token totals and
distributions; output-token throughput must use the full measured window.
Portable client results, logs, and metrics use one random course run ID rather
than a scheduler job ID.

## Gate 6: two-node serving and expert-parallel concepts

```bash
sbatch slurm/vllm_two_node.sbatch
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile smoke
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke
```

Require the serving launcher to reach health, complete all loopback requests with
non-empty outputs, write its two-node result JSON, and stop both server ranks
cleanly. Report topology with every scaling result. The MoE lab is a bounded
communication/mechanism study, not a production MoE implementation.
The tensor-parallel lab must match both column- and row-parallel outputs to the
replicated reference and report the slowest rank; it demonstrates mechanics,
not a speedup claim for a model that already fits on one H100.
Run the two-node vLLM multiprocessing path only on a trusted private fabric,
restrict its rendezvous port to the allocated nodes, and do not expose worker
or control traffic to an untrusted network. The teaching HTTP endpoint remains
loopback-only.
