# Cluster smoke-test runbook

This is a target-qualification checklist, not the lesson execution order.
Follow the [syllabus](../SYLLABUS.md) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Run from the Inference course root. Use the separate mechanics, client, and
pinned engine environments in [VERSIONS.md](../VERSIONS.md). Prepare the
site-approved container runner and immutable image identities before engine
jobs. Before submitting, set the submitting shell's file-creation mask:

```bash
umask 077
python -m pip check
python tools/validate_course.py
```

Raw model data, prompts, outputs, Slurm logs, server logs, metrics, and profiler
reports remain private. Fill in [benchmark-record.md](benchmark-record.md) and
follow [evidence-security.md](evidence-security.md) before sharing aggregates.

## Gate 1: allocation and artifact identity

```bash
sbatch slurm/single_gpu.sbatch labs/16_model_artifact_audit.py --profile smoke
```

Require a full non-MIG H100, compute capability 9.0, immutable model/tokenizer
identity, compatible configuration and weights, and the stated remote-code
policy. Separate artifact resolution, CPU staging, GPU loading, warm-up, and
readiness. Reaching readiness does not establish request correctness.
Lab 16 checks the local H100. The distinct two-node Lab 00 preflight belongs
to Gate 5 and must use the two-node launcher.

## Gate 2: generation and cache semantics

```bash
sbatch slurm/single_gpu.sbatch labs/35_inference_basics.py --device cuda
sbatch slurm/single_gpu.sbatch labs/08_kv_cache.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/17_sampling_semantics.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/18_padding_bucketing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/23_speculative_decoding.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/24_sdpa_attention.py --profile smoke
```

Lab 35 can first be studied with `--device cpu` without downloads or an engine.
Require the declared token sequence, EOS/length stopping and unchanged
parameters. Its fixed table contains no attention or KV cache and its CPU
result does not qualify the H100 path or serving performance.

Require cache/no-cache agreement and fixed decoding semantics. Lab 09 checks
the requested new-token count, attention mask, cache position, and advancing
cache length. The synthetic speculative lab must match the target-greedy
sequence and exercise partial rejection, recovery, and full-acceptance bonus
paths. High acceptance must reduce target forward calls, while the
low-acceptance control must demonstrate why proposal overhead can remove
that benefit. It is not real-engine speedup evidence. SDPA comparisons hold mask,
shape, dtype, and correctness tolerances fixed.

## Gate 3: workload, capacity, and scheduling mechanics

```bash
sbatch slurm/single_gpu.sbatch labs/25_workload_metrics.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/26_kv_capacity.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/27_paged_kv.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/28_continuous_batching.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/29_quantization.py --profile smoke
```

Define ISL/OSL, arrival pattern, concurrency, TTFT/ITL timestamp endpoints and AIPerf aggregation conventions, and output
tokens for the later live campaign. Lab 25 itself measures synthetic projection
and recurrent operator work, with batch rows rather than service requests and
no generated tokens or TTFT/ITL. Check MHA/GQA/MQA arithmetic, allocation/recycling invariants, and
scheduler semantics before live-engine comparisons. Lab 29 must pass its
declared tensor-error gates; inspect its logical byte counts and timing
observations separately. Actual resident-memory savings, service latency and
model quality require a qualified engine experiment; smaller weights alone do
not establish a faster service.

Lab 36 is a separate local policy gate with no GPU or serving engine requirement:

```bash
python labs/36_kv_tiering.py --storage-gbps 2 --output-dir results/tiering-fast
python labs/36_kv_tiering.py --storage-gbps 0.2 --output-dir results/tiering-slow
```

Hold the arrival sequence and capacities fixed. Verify TTL expiry, LRU
demotion, namespace isolation and restore-versus-recompute decisions using the
complete guide's restart and revision controls. Record modeled foreground and
write costs separately. Neither modeled hits nor assumed bandwidth prove real
KV persistence, GPUDirect Storage activation, TTFT or storage performance.

## Gate 4: one-GPU serving and streaming

```bash
sbatch slurm/vllm_offline.sbatch --profile smoke
sbatch slurm/vllm_benchmark.sbatch
sbatch slurm/vllm_streaming_benchmark.sbatch
sbatch slurm/vllm_prefix_cache.sbatch
```

Use the launchers' documented model and immutable revision inputs. Keep cold
startup separate from steady state. Offline vLLM must aggregate all measured
requests, prompt and output tokens, and the full measured-window throughput.
Serving launchers retain Prometheus snapshots and request failures. For prefix
A/B, compare prompt-token cohorts within the lab's declared 0.8–1.2 median-count
ratio and report the actual ratio. Inspect cache queries/hits rather than
assuming visible repeated text guarantees reuse. Streaming chunks do not
necessarily correspond one-to-one with model tokens.

## Gate 5: two-node serving and placement

```bash
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile smoke
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke --batch-size 1 --output-dir outputs/tp-batch1-run1
sbatch slurm/vllm_two_node.sbatch tp
```

Require two distinct nodes, world size 2, local-rank binding, exact collective
correctness, endpoint health, nonempty successful responses, and clean shutdown
of both server ranks. TP must match both column- and row-parallel reference
outputs. Record model fit, latency, throughput, memory, rank count, and the
actual network topology. Use the trusted private allocation fabric for worker
traffic, restrict rendezvous traffic to the allocated nodes using the site's
approved network controls, and use loopback for the teaching HTTP endpoint.
Do not expose the worker or rendezvous ports publicly. Two nodes do not establish
NVLink, production EP, or large-scale disaggregation performance.

## Gate 6: engine qualification and causal report

Inspect each specialized launcher's help before providing its required engine
repository or profile inputs:

```bash
bash slurm/trtllm_triton.sbatch --help
bash slurm/aiperf.sbatch --help
bash slurm/dynamo_disaggregated_preflight.sbatch --help
bash slurm/vllm_speculative_ab.sbatch --help
sbatch slurm/capstone_three_trials.sbatch --profile smoke
```

Lab 30 and its engine profiles document TensorRT-LLM/Triton, AIPerf, and
conditional Dynamo qualification. Real-engine speculative decoding is optional
and requires compatible target/draft artifacts. Dynamo disaggregation remains
an advanced exercise with capacity, transport, and routing prerequisites.

Keep three independent attention-capstone records with matching shapes,
numerical checks, timing distributions, memory observations and a
profiler-backed explanation. For a separately qualified serving campaign, also
retain throughput/latency curves, queue/KV/preemption evidence, errors and
quality checks. Preserve the rejected hypothesis and counterbalance
baseline/candidate trial order. Record
unsupported engine or topology gates as pending rather than substituting
simulation or CPU mechanics for live serving proof.
